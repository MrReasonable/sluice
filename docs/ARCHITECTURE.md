# Architecture

## `core/`

Shared by every sub-app:

- `config.py`: layered config. Code defaults, overridden by a `sluice.yaml`
  file, overridden last by environment variables.
- `vault.py`: the lead/experience store. Reads and writes an Obsidian-style
  markdown vault without clobbering status, scores, or notes a human or
  another agent has already set: a fresh scrape touches only a `last_seen`
  marker on an existing note. A create races the filesystem via an
  exclusive (`O_CREAT|O_EXCL`) write and re-reconciles on `FileExistsError`;
  a modify (`update_fields`, `append_body_section`, `set_tailored_cv`,
  status normalization) goes through the same shape one level in --
  content-CAS plus an atomic replace, re-deriving the edit from fresh
  content on a bounded number of lost races, and refusing loudly
  (`VaultConflict`) rather than clobbering if the conflict is sustained
  (#16). This is best-effort, not absolute: a residual micro-window
  remains between the freshness compare and the `os.replace`, so a lost
  update is still possible in that ~2-syscall gap. It is a large
  improvement over the pre-#16 whole-function window, not a guarantee --
  an advisory lock was considered and declined as YAGNI.
- `backends.py`: LLM clients. `ClaudeMaxBackend` shells out to a `claude`
  CLI, local or over SSH; `AnthropicBackend` calls the Anthropic Messages
  API directly; `OpenAiCompatibleBackend` calls any OpenAI-compatible HTTP
  endpoint; `FallbackBackend` tries the first and falls back to the second
  on error; `make_backend` builds any of them by name.
- `camofox.py`: an HTTP client for a Camofox headless-browser server, the
  impure fetch boundary that ingest sources drive a tab through.
- `urlguard.py`: url policy for the dossier fetcher. Decides whether a
  scraped lead url may be navigated to -- http(s) only, globally routable
  addresses only, with a per-host/CIDR allowlist for a deliberately
  self-hosted board. Pure except for `_resolve`, which is injected, so the
  suite never resolves DNS. Ingest is NOT guarded: its urls come from a
  source's own spec or the user's config, not from a scraped page.
- `status.py`: the canonical status vocabulary shared across sub-apps.
  Triage owns the early states (new, shortlist, research, needs_review,
  dismiss); track owns the later ones (applied, phone_screen, ... offer,
  rejected); neither overwrites the other's. The one crossing between the
  two lifecycles, `shortlist -> applied`, has two actors -- apply (on send)
  and track (on a domain-matched confirmation receipt) -- both gated by the
  same `can_apply` predicate.
- `paths.py`: where every path sluice owns lives (#80). One `resolve()`, one
  order -- env var, then config key, then the XDG base directory for that
  `kind` (`config`/`state`/`cache`, validated against that closed set). It
  performs no writes, so RESOLVING a path cannot touch the disk; the writer
  that needs a parent creates it. That is a claim about `resolve` only, and not
  about a `--dry-run` as a whole, which does still write: `ingest run --dry-run`
  records per-source health. Reads `XDG_*` per call, never at import, because
  an import-time snapshot is unpatchable by tests. It also holds the table of
  where each path lived BEFORE the sweep, so the migration has one home and
  the cwd-relative literals survive in exactly one module.

  Disposition, complete:

  | what | env var | now under | note |
  | --- | --- | --- | --- |
  | config file | `SLUICE_CONFIG` | config | all five loaders, via `config_file()` |
  | dedup state | `SEEN_DB` | state | **refuses** if left behind, on a writing ingest run only |
  | track dedup state | *none* | state | **refuses** (incl. dry runs); `.lastrun` + #49 store derive from it and move with it |
  | source health | `SLUICE_HEALTH` | state | |
  | disabled sources | `SLUICE_DISABLED` | state | |
  | triage audit | `TRIAGE_AUDIT` | state | was a dead config key |
  | dossier cache | `DOSSIER_DIR` | cache | ONE root key; was two sub-app keys |
  | Google OAuth token | *none* | state | written `0600`, parent created |
  | vault | `VAULT_DIR` | **unmoved** | gains a config key; precedence in `stores/vault.py:_make` |

  The cv/apply artefact paths (`cv-output`, `cv-served`, `cv-home`,
  `cv-host`, `cv-uploads`, the render script) stay cwd-relative by design:
  they are outputs a user places, not state sluice owns.

  Nothing is ever moved automatically. A path left at its old location warns
  and names the `mv`, except the two dedup stores, which refuse -- continuing
  with an empty dedup set re-creates every lead a human merged away, which can
  mean a second application under their name (see #81).

  The two refusals are scoped differently, deliberately. `ingest` refuses only
  when the run actually writes dedup state, so `--dry-run` and `--sink json`
  proceed. Every `track` command refuses, dry runs included, because a track dry
  run reads the #49 dead-letter store to report what it WOULD do, and against a
  relocated store it would report nothing to do -- a silently wrong answer a
  human then acts on. `doctor` never refuses, since a relocated file is exactly
  what one runs doctor to hear about. An explicitly named path (env var or
  config key) short-circuits resolution before the check either way, so callers
  who name their own paths are immune by construction rather than by a rule
  repeated at each site.

**How a state file behaves when it cannot be read** is one convention, keyed on
what a wrong answer COSTS, not on which module happens to own the file. A
seventh state file should pick its tier from this list rather than copy
whichever neighbour it was written next to:

- **Raise** when a silent empty is irreversible.
  - `SeenDb.load` (`seendb.py`) and `_load_seen` (`core/app.py`) are dedup state
    and are read-modify-written, so an empty read is not one lost run: it is
    written back as the new truth, and for a dedup store that means a duplicate
    application under the user's name.
  - `open_entries` (`track/deadletter.py`) is neither -- it is the #49 proposal
    queue and every writer hits the database independently. It raises for its
    own reason (F1): an empty read silently discards the backlog of proposals a
    human has not acted on, and reports the run as ordinary.
- **Warn and continue** when a wrong answer is recoverable AND the caller only
  reports it: `_disabled_or_warn` (`cli.py`), used by `list-sources`, where a
  wrong answer misprints a status line. Its raising sibling `_load_disabled` is
  what `enable`/`disable` call, because those rewrite the file, and what
  `ingest run` calls, because that ACTS on the answer -- it would scrape the
  sources the operator turned off. The tier follows what the caller does with
  the value, which the file itself cannot know.
- **Silent** when the value is derived and the next successful run repairs it:
  `HealthStore._load` (`health.py`) and `_load_lastrun` (`core/app.py`).
  `ingest/engine.py` rules the same way on the write side ("health is
  best-effort; never fail a run over it"). NB repairing the FILE is not the same
  as recovering the information: a lost `.lastrun` is rewritten next run, but the
  receipts in the gap it no longer covers are never re-queried -- see that
  function's docstring. It sits here because nothing is destroyed by the read
  itself, not because the loss is free.

- `seendb.py`: a sqlite dedup store for already-seen leads. Reading it never
  CREATES it (`sqlite3.connect` would, and the resulting empty file disarms the
  relocation refusal above), and an unreadable database RAISES rather than
  reading as empty -- a silent empty dedup set re-creates every lead a human
  merged away. An existing database with no table is the one tolerated empty:
  that is a real first-run state.
- `resilience.py`: retry-with-backoff, hard timeout, and rate-limit
  precheck helpers that wrap each source's I/O.
- `health.py`, `dossier.py`, `leads.py`, `log.py`, `relevance.py`: health
  reporting, per-lead dossier assembly, the source-agnostic `Lead` model,
  logging, and the relevance gate.

## The five sub-apps

1. **ingest** (`sluice/ingest/`): declarative sources (`base.Source`, split
   into an impure `fetch` and a pure `parse`) driven by `engine.run()`,
   which dedups via `core.seendb`, gates via `core.relevance`, and writes
   through a sink (vault or JSON) to the lead store.
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   `apply.py` writes verdicts back, skipping any lead already in the
   application lifecycle; `audit.py` logs every decision.
3. **cv** (`sluice/cv/`): select verified source material, bundle it into
   a closed set, compose a tailored CV against that bundle (an LLM call
   over `core.backends`), validate it against a fabrication gate (a hard
   fail triggers exactly one retry, then the lead is skipped rather than
   rendered ungated), render (shells out to an external script), and serve
   under an opaque, cache-busted filename. Above the hard gate sits a softer,
   human-facing layer (#60): an advisory LLM audit (`audit.py`) flags claims
   the bundle does not support, and an `unsupported` flag still renders and
   serves the PDF (it passed the hard gate) but WITHHOLDS the send-ready
   `tailored_cv` pointer, so `apply` cannot select it. The hold is
   recorded in two NEW frontmatter keys (`pending_cv`, `needs_signoff`) — the
   note's `status` stays `shortlist` (never-regress is untouched); the CV is
   simply invisible to apply without the pointer. A held lead is skipped on
   re-run so a non-deterministic re-audit cannot promote it by luck.
   (`needs-signoff` and `skipped-needs-signoff` are `CvResult` run-report
   labels, not `status`-key values.) `sluice cv signoff --lead X` promotes
   the held CV after the candidate reviews the flagged claims; `--discard`
   rejects it and frees a fresh compose. The default is on
   (`cv.require_signoff`); it never touches the pure hard gate.
4. **apply** (`sluice/apply/`): select eligible leads, stage the rendered
   CV file and a prep packet, and record the applied transition
   (never-clobber). Actual ATS form submission is human-driven; this
   sub-app prepares the material, it does not drive a browser.
5. **track** (`sluice/track/`): fetch Gmail and Google Calendar since the
   last run, classify each message into an `Event` (refuse rather than
   guess on ambiguity), and reconcile it against lead status
   (never-regress: a status can only move forward). A domain-matched
   application receipt (`track/receipt.py`) advances a `shortlist` lead to
   `applied`: a proof-grade match auto-advances with evidence recorded, but
   only when `event.confidence >= cfg.auto_apply_min` -- below that floor it
   proposes like any weaker match. Proof means the SENDER host is the lead's
   own host (never a body link, which the sender controls); the delivering
   server AUTHENTICATED that domain (an `Authentication-Results` dkim/dmarc/
   spf PASS whose domain aligns with the sender, since a `From` header is
   free text anyone can forge); and neither side is multi-tenant -- an ATS
   relay (`ats_relay_domains`) or one of the job boards sluice scrapes
   (`job_board_domains`), since a board-sourced lead's `url` identifies the
   board, not the employer. Failing or missing authentication degrades to a
   proposal rather than dropping the signal. A weaker corroborated or
   cross-lead-ambiguous match only proposes. Receipt proposals have two
   producers: reconcile's own corroborated/below-floor path, and -- when
   deterministic matching finds nothing at all (tier `none`) while the LLM
   named a lead that is already in-flight -- an engine-level fallback that
   records a dead-letter row and never writes. Un-acted-on proposals
   are durably surfaced via `track/deadletter.py` -- a sqlite dead-letter
   re-emitted every run until `track confirm`/`track dismiss` clears it, or a
   lead's own proposals are cleared automatically when it auto-advances --
   so a proposal never vanishes after a single report.

## The plugin core

Two modules and a composition root make the seams real:

- `core/plugins.py`: the adapter registry. `register(seam, name, factory)` /
  `get(seam, name)`, keyed by the name in config. An unknown name raises and
  lists the valid ones; it never falls through to a default. For the store seam
  that matters most, because a quiet wrong default means writing the user's
  leads somewhere they did not ask for.
- `core/protocols.py`: `Store`, `Fetcher`, `Renderer`. Interface only. `LeadNote`
  carries an opaque `ref` (a path for the vault, a row id for some other store)
  and a `slug` the store issues -- identity used to be re-derived from the
  markdown filename in four separate modules, and that is what pinned the store
  to a filesystem.
- `core/app.py`: `Sluice(config)`, the composition root. Resolves the adapters
  config names -- store, fetcher, renderer, and backend (by ROLE: auto/primary/
  fallback, over whichever provider config selects) -- and OWNS the pipeline
  operations as value-returning methods: `ingest()`, `triage()`, `compose_cv()`,
  `prep()`, `record()`, `track()`, `track_confirm()`, `track_dismiss()`,
  `normalize_statuses()`, `expire_report()`, `expire()`. It also owns the state
  those operations need that is not itself an adapter: the dossier cache
  (`dossier_cache()`), the lead-staleness rule (`staleness()`, #9 -- built once
  per invocation from `lead_ttl_days` and the `today` collaborator, so cv, apply
  and expire cannot disagree about it), and track's file-backed seen-message set,
  last-successful-run watermark, and dead-letter store of un-acted-on proposals.
  Adapters are built lazily on first use, so an offline command still never
  constructs a browser, a store or a backend.

Implementations live in `sluice/stores/`, `sluice/fetchers/`, `sluice/renderers/`
and `sluice/backends/`, each self-registering on import exactly as
`ingest/sources/` already did.

There are two kinds of plugin. An **adapter** plugin is something core calls (a
store, a renderer, a fetcher, a backend); a **surface** plugin is something that
calls core (a web UI, a TUI, a daemon). The registry serves adapters. Surfaces
need a programmatic API to drive, and `Sluice` is that API: it resolves the
adapters AND drives the pipeline, so a surface no longer constructs a `Vault()`
or a `Camofox()`, builds a backend, or duplicates the triage/compose/prep/record/
track wiring itself. `cli.py` is now a thin shell over `Sluice` -- each command
builds one, calls one method, and formats the result for the terminal -- so a web
UI written today has nothing left in `cli.py` worth forking.

`tests/conformance/test_store_contract.py` is parameterised over every registered
store and asserts never-clobber (a re-scrape touches only `last_seen`, and that
marker may only move **forward** — an older re-scrape leaves the newer stored value,
so `last_seen` is monotonic), never-regress, slug/ref identity, and
never-silently-absorb-a-different-opportunity: two jobs with a proven **location**
difference produce two notes, and when identity is uncertain `upsert` returns an
explicit `merged` rather than absorbing the lead silently (#5). `merged` is a
permitted outcome — the property forbids the *silent* absorb, not the merge. The
contract's identity is url + location; it does not compare titles, so "distinct titles
at one company+location split" is **not** a conformance property (a store keyed on
url+location could absorb them and still pass). The `vault` store escapes that only
because the title is in the filename — see the store-seam note below.
Location is decided one layer up too: the ingest **read** key (`Lead.dedup_key`, for
URL-less leads) folds in `_norm_location(location)`, so the engine's own dedup does not
collapse two cities *before* the store's split can run (#23). The two layers use two
notions of sameness — an equality hash upstream, token overlap in the store — but the
upstream key can only *over*-split relative to the store, which is the safe direction:
anything the engine lets through, the store re-merges.
Those guarantees used to live inside `core/vault.py`; a second store would have
shipped without them. They are now properties of the contract, and a store passes
that suite or it does not ship.

Another property joins those: **the conflict outcome**. A modify-write that
keeps losing the race against a concurrent editor (a human in Obsidian, Syncthing, a
second `sluice` process) must refuse loudly rather than clobber -- raising
`VaultConflict` for the field-writers, or folding into `upsert`'s `refused` outcome
-- and write nothing. `test_a_sustained_write_conflict_refuses_rather_than_clobbers`
pins this at the contract, the same altitude as never-clobber and
last_seen-monotonicity. The CAS *mechanism* that makes the outcome possible --
content-compare, atomic replace, bounded re-apply -- is vault-specific and not itself
asserted; a store keyed on real rows (a database transaction, say) can satisfy the
same outcome by a wholly different route (#16).

A third absorption shape sits beside those two: a batch sweep over many notes
(`normalize_all_statuses`) folds a per-note sustained conflict into a `skipped`
report rather than raising or aborting, so one racing note never aborts the whole
sweep. A race a bounded re-derive can resolve still commits (counted `changed`); a
race that makes the collapse a genuine no-op is an abstain (counted `unchanged`,
not `changed` -- nothing was written, so there is nothing to report as a change).

**Lead staleness** (#9) is the other human-gated read-path pass. `lead_ttl_days`
(root `Config`, default `0` = off) is the age past which a lead's `last_seen`
makes it stale. `sluice leads expire` REPORTS the stale set and writes nothing;
`--expire [SLUG...]` dismisses everything reported, or only the slugs named, by
EXACT slug equality. It moves a lead to `dismiss` — the triage-owned end state,
never a `_TERMINAL`, since every terminal is application-owned — recording the
prior status in the audit note. Application-owned leads are never enumerated,
and the write additionally passes `require_status`, which re-reads status inside
the store's CAS transform: the read loop is a window in which a lead can enter
the application lifecycle, and a check against the enumerated note is a snapshot
that is stale by construction. A lead holding a #60 sign-off (`pending_cv`) is
refused, because dismissing it silently discards work in flight — a composed CV no
human has signed off. (`sign_off_cv` resolves over all of `TRIAGE_OWNED`, so a
dismissed lead is still reachable; the refusal rests on the discarded work, not on
reachability.)
`cv run` and `apply prep` independently refuse a stale lead before spending
anything, both with `--include-stale`; the policy reaching all three is one
frozen `StalenessPolicy` built by `Sluice.staleness()`. Staleness is a cheap
proxy: whether a role is still open can only be answered on the employer's own
site, so it does not replace checking before applying.

A fourth property sits beside the write contract, but a deliberately weaker one:
**read-path dedup** (#23) is human-gated, not automatic. `sluice leads dedupe`
clusters already-stored lead notes it suspects are duplicates and REPORTS the
clusters; it changes nothing. `--merge <id> [<id>...]` merges only the clusters
the human names, by a report id that hashes the cluster's membership, so an id
from a stale report (membership has since changed) is refused rather than acted
on blind. This is a read-path pass over notes already in the vault -- ingest's
own write-time dedup (`Lead.dedup_key`, `core/leads.py`) is unchanged. Identity
here is ROLE-level, never company-level: a cluster requires the same firm AND
the same role (`_norm_tokens`, minus the configured `dedupe_title_noise_words`)
AND a complete-linkage-compatible location (`_location_cliques`) -- a component
is only a cluster if every pair in it is compatible, so a chain of blank-location
edges can never bridge two notes with two different, named cities.

A merge keeps the survivor inside never-clobber's usual rule: only `alt_urls`,
`first_seen` (minimised) and `last_seen` (advanced) change, re-derived against
the fresh note through the same CAS path every modify-write uses, so a caller's
stale bounds can never regress them. The survivor is chosen from among the
members already holding the cluster's winning status, decided by an
order-independent, N-ary status-precedence verdict (`core/status.py:
resolve_merge_status`) that ranks application-owned status above triage-owned:
within application-owned, live statuses rank by ladder position, but a
terminal is never ranked against a live one -- a terminal beside a live
re-application, like two different terminals, is a genuine ambiguity and a
CONFLICT, and that cluster's merge is refused rather than guessed. Losers are
moved, never deleted, to `Job Applications/Job Leads/_merged/` -- reversible,
and invisible to `read_leads` for the same structural reason the Experience
Library's `_inbox/` is invisible to its read: both are subdirectories the
`.md`-file listing skips over. A loser's own downstream state (scores, notes,
a rendered CV, a sign-off hold) is therefore INTENTIONALLY dropped from the
active view on merge, recovered only by moving the note back out of `_merged/`
by hand; the report flags a loser carrying a rendered CV (`tailored_cv`), an
open sign-off hold (`pending_cv`/`needs_signoff`), or an application-owned
status -- not merely a score or a notes field -- so the human sees what a
merge would discard before naming it.

The Store-contract surface changed to carry this: `merge_cluster` was ADDED to
the `Store` protocol and its conformance suite, and the dead `existing_keys` --
never called outside its own tests, superseded by the ingest-side `seen.db`
cache before it shipped a real caller -- was REMOVED. Neither change widens
the ingest, never-clobber or never-regress contracts described above; the
merge is built to uphold all three, not to carve out an exception to them.

## Adapter-selector seams

Four points in the config are the seams for pluggable adapters.

- **backend**: `sluice/backends/`, selected by provider name through the adapter
  registry (`make_backend` is now a thin shim over `plugins.get("backend", name)`).
  Implementations: `claude-max` (flat-rate `claude --print` CLI), `anthropic` (direct
  Messages API), `deepseek` and `openai` (OpenAI-compatible). Role selection
  (`auto`/`primary`/`fallback`) sits ABOVE the provider seam, in `Sluice.backend()`:
  the config picks which provider fills each role, the role picks which backend runs.
  `tests/conformance/test_backend_contract.py` asserts the portable contract over every
  registered provider — an empty/whitespace response and a transport failure both raise
  `BackendError` (the property `FallbackBackend` relies on), and a valid response returns as
  its text — so a new provider passes it or does not ship, exactly as the store bullet's
  conformance suite does.
- **store**: `sluice/stores/`, selected by `store:` (default `vault`).
  Implementations: `vault` (the Obsidian-style markdown vault in
  `core/vault.py`). A SQLite store is the obvious next one, and the
  conformance suite is what it must pass. The `vault` store derives note
  identity from the filename, so it needs discriminators the contract does
  not: on a `Company - Title` collision it appends the location, and — when
  the 120-char cap truncated the title — a stable digest of the full title,
  so two distinct long titles at one location still split. The first-seen
  keeps the clean, digest-less name (zero migration); only the collider is
  suffixed. This is a vault filename concern, not a Store property — a store
  with real keys distinguishes those rows without it.
- **renderer**: `sluice/renderers/`, selected by `cv.renderer:` (default
  `script`). Implementations: `script` (shells out to the external WeasyPrint
  script at `cv.render_script`) and `weasyprint` (bundled, in-process, needs
  `pip install 'sluice[render]'`). Note the shipped `render_script` default
  points at a file that does not exist in the repo; `script` now says so at
  construction rather than dying after a CV has been composed and gated.
- **fetcher**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server).
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.

`sluice doctor` is a read-only preflight over the backend seam: it enumerates every
configured backend (primary and fallback, per sub-app), classifies each as
`ok`/`degraded`/`dead`, and exits non-zero when a run-blocking backend is dead. The
classification is role-aware -- a keyless fallback degrades (the sanctioned
primary-only path, exit 0), while a keyed-but-broken backend is `dead` regardless of
role, the silently-non-functional fallback the tool exists to catch. Live round-trip
by default; `--offline` for a config-only check; `--strict` to also fail on degraded.

## Injected collaborators — the other kind of seam

The four above are *adapter* seams: a config key selects an implementation by
name from a registry. A second, smaller set of injection points looks similar
and is deliberately not the same thing. They carry no config key and no registry
entry, and are passed in by the caller:

- **`client`**, **`now_iso`** — parameters of `Sluice.track()`: the Google API
  client, and the run timestamp that becomes the `lastrun` watermark.
- **`sleep`**, **`today`**, **`resolve_host`** — `Sluice.__init__` keyword-only
  parameters. `sleep` and `today` are threaded into `ingest.base.Ctx` and
  `ingest.sink.VaultSink`: the page-settle wait and the date stamp. Two clock
  shapes rather than one is deliberate — the sink stamps per lead so it needs a
  callable, while track persists one value per run. `today` also feeds
  `Sluice.staleness()` (#9), which **calls** it once and freezes the resulting
  string into a `StalenessPolicy`; that is the second consumer, and the reason
  the callable-vs-string distinction is now load-bearing beyond ingest — binding
  the callable itself would reach `date.fromisoformat(<function>)` inside a
  gate. `resolve_host` is the DNS resolver the dossier url guard uses; it is
  deliberately NOT a seam, because a registry entry is reachable from config and
  that would put an off switch for an SSRF guard under a YAML key.

The rule for a new dependency: **does a user legitimately choose among
implementations?** If yes it is an adapter seam and belongs in the registry,
where an unknown name raises and lists the valid ones. If there is exactly one
real shape and the only other caller is a test, it is a passed-in collaborator.

That line is a safety property rather than a stylistic one. A registry entry is
reachable from config, and config is user-facing: a selectable `sleep` would let
someone set `sleep: none` and scrape a board until it bans them; a selectable
`today` would let them freeze the clock and quietly break the `last_seen`
monotonicity the store contract guarantees. There is likewise one Google, so a
name-keyed lookup would advertise a choice that does not exist.

Neither kind may be accepted and ignored. An unknown *adapter* key raises
`UnknownAdapter` at construction, listing the valid seams. The collaborators are
weaker: `Sluice.__init__` ends in `**overrides`, so a typo'd `sleep=` is absorbed
there. That was reported as an unknown seam override — loud, but naming the four
adapter seams and so pointing at the wrong fix. `resolve_host` was the third
`__init__` collaborator and triggered the tightening this paragraph used to
defer: the raise now carries a hint naming the collaborators and the seams
*separately*, and `_COLLABORATORS` is pinned to the real signature by a guard
test. The scope is `__init__` keywords only — `client`/`now_iso` are
`Sluice.track()` parameters, never reach `**overrides`, and a typo there is
already a plain `TypeError`.

