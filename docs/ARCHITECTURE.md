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

  Normalisation is one rule with one apparent exception. `expanduser` at
  INGRESS, wherever a path first arrives from outside -- enumerated from the
  source by `tests/test_path_tilde.py` rather than counted here, because two
  versions of this paragraph carried a number and both were wrong (four when
  there were five, five when a sixth landed) with nothing going red either
  time. `abspath` ONLY where the
  value outlives the cwd it was read in, whether by being written down
  (`questions.py`, and the preset `cli.py` hands `job-sluice init`) or compared
  (`cli.py`'s `--vault` against `$VAULT_DIR`). Neither is true of what `resolve`
  returns, so it does not abspath, and a relative explicit value comes back
  exactly as written. At CONSUMPTION, neither -- except that a path becoming a
  `file://` URI must be absolute or its first segment is read as the URI
  authority, so `existing_db_uri` joins the cwd on. That is not a breach: it
  changes the URI, never the path a caller sees. Measured before it did:
  `SEEN_DB=relative/state/seen.db` saved on run 1 and died on run 2 with
  `invalid uri authority: relative`, and every non-absolute spelling (`./`,
  `../`, `~/`) failed identically. It JOINS rather than `abspath`s because
  `normpath` collapses `..` lexically while every other operation on the same
  value lets the OS resolve it -- measured through a symlinked parent, the
  writer created one file and the reader opened another. `vault.py`'s "No
  abspath -- a relative vault is legitimate" and `questions.py`'s "Absolute,
  always" look opposed and are not: a path used in place, versus a path written
  down for later.

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
  with an empty dedup set silently re-submits every already-known lead to the
  write path. The write path now probes `_merged/` by name before creating
  (#81), so a merged-away lead usually self-heals rather than being
  re-created, but the probe is name-keyed and a re-scrape whose title has
  drifted past every candidate still slips through, which can mean a second
  application under their name (see #81, and the residual noted below).

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

- `seendb.py`: a sqlite dedup store for already-seen leads. Reading it never
  CREATES it (`sqlite3.connect` would, and the resulting empty file disarms the
  relocation refusal above), and an unreadable database RAISES rather than
  reading as empty -- a silent empty dedup set re-submits every already-known
  lead to the write path, which can resurrect one merged away (#81's residual;
  see the store-contract section below). An existing database with no table is
  the one tolerated empty: that is a real first-run state.
- `resilience.py`: retry-with-backoff, hard timeout, and rate-limit
  precheck helpers that wrap each source's I/O.
- `health.py`, `dossier.py`, `leads.py`, `log.py`, `relevance.py`: health
  reporting, per-lead dossier assembly (`DossierCache`, keyed on a stable url
  hash rather than the company/role slug so a #109 mid-run company mutation
  does not double-fetch; also captures `page_title`/`structured_data` for
  triage's tier-2 AND tier-3 company resolution, both excluded from what
  `slim()` sends the judge), the source-agnostic `Lead` model, logging, and
  the relevance gate.
  Re-keying `cache_key` makes every dossier cached before this version
  unreachable, so expect one full re-fetch on the first triage or cv run after
  upgrading -- bounded, not data loss, since the default `ttl_days: 7` would
  have expired them inside a week anyway.

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


## The five sub-apps

1. **ingest** (`sluice/ingest/`): declarative sources (`base.Source`, split
   into an impure `fetch` and a pure `parse`) driven by `engine.run()`,
   which dedups via `core.seendb`, gates via `core.relevance`, and writes
   through a sink (vault or JSON) to the lead store.
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   A lead classify() leaves at blank-company `needs_review` gets one
   resolution attempt (`resolve.py`, #109/#120/#151) before that: a free
   regex over the role text's own trailing "<role> at <Company>" clause,
   tier 0, a free URL-pattern tier 1, an opt-in, no-LLM page-visit tier 2,
   then -- also opt-in, and only when tiers 0-2 all abstain -- an LLM
   read of that SAME page data, tier 3, on a SEPARATE backend from the
   judge's (always the cheap "fallback" role, regardless of `--backend`)
   -- so "for free" no longer describes the WHOLE classify pass
   unconditionally: a blank-company lead can trigger a real page visit when
   `triage.company_resolve_fetch` is on, and an LLM call when
   `triage.company_resolve_llm` is also on. `apply.py` writes verdicts
   back, skipping any lead already in the application lifecycle (its own
   writes, and the new resolution write, are all `require_status`-guarded
   against a lead entering that lifecycle mid-run); `audit.py` logs every
   decision that actually landed -- a lead whose write was refused (already
   application-owned, or a status change mid-run) is logged nowhere, so the
   audit never claims a decision that was not applied.
3. **cv** (`sluice/cv/`): select verified source material, bundle it into
   a closed set, compose a tailored CV against that bundle (an LLM call
   over `core.backends`), validate it against a fabrication gate (a hard
   fail triggers exactly one retry, then the lead is skipped rather than
   rendered ungated), render (by default `template`: fill the user's own
   Jinja2 template — or the packaged one — and write a PDF via WeasyPrint;
   `script` shells out to an external render script instead), and serve
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
   labels, not `status`-key values.) `job-sluice cv signoff --lead X` promotes
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
   records a dead-letter row and never writes. Un-acted-on work is durably
   surfaced via `track/deadletter.py` -- a sqlite dead-letter re-emitted every
   run until `track confirm`/`track dismiss` clears it, or a lead's own
   proposals are cleared automatically when it auto-advances -- so it never
   vanishes after a single report. The store holds three kinds of row, not
   just status proposals: a `failure` row for a message that could not be
   processed at all, and a `calendar` row for a calendar action that could not
   be completed or verified. An auto-advance clears a lead's STATUS proposals
   only -- advancing to `rejected` does not remove a stale calendar entry, nor
   make a failed message succeed.

## `onboard/` — a command package, not a sixth sub-app

`sluice/onboard/` backs `job-sluice init` (#8). It sits BESIDE the pipeline rather
than inside it: nothing in `ingest -> triage -> cv -> apply -> track` imports it,
and it has no engine, no store of its own and no place in any run.

Split pure-from-impure, which is the whole reason its guarantees are unit-testable:

- **`questions.py`** (pure apart from `parse_path`, which resolves against `$HOME` and the cwd --
  a path parser has to consult the environment): the declarative catalogue — one `Question` per key,
  each carrying its `parse`, the dotted config keys it `writes_to`, a hint and a
  consequence line. Every preference question has `default=None`, which means a
  blank answer SKIPS it. The vault is the sole exception, and its default arrives
  as a `catalogue(default_vault=...)` PARAMETER so a pure catalogue never imports
  the concrete store. Backend and renderer choices are derived from the live
  registries, never hand-listed.
- **`emit.py`** (pure): hand-rolled YAML scalars. `safe_dump` would destroy the
  comments that are most of the template's value and a round-tripping loader is
  barred by the standard-library-only rule, so strings are always double-quoted —
  the one form with a total escape grammar.
- **`plan.py`** (pure): `build_plan(answers, ...) -> InitPlan`, producing the two
  artefact texts plus the notes the report prints. The config is RENDERED FROM THE
  CATALOGUE, which makes "every key the wizard can write appears in the file it
  writes" true by construction. An unanswered key is emitted COMMENTED; the block
  HEADER stays ACTIVE, because a commented header made the file's own
  `# <- uncomment and set YOUR OWN` marker produce an unparseable config for every
  nested key (16 of 19), and all four loaders have always read a null block as
  empty.
- **`ask.py`** (impure): the only half that touches a terminal. `TtyAsker` prompts
  and re-asks on a bad answer; `NoInputAsker` answers only from flags and REFUSES
  rather than reading stdin, because a wizard blocking on a pipe is a hung CI job
  with no diagnosis. Both satisfy one small interface, so `--no-input` is the same
  wizard with the prompting removed rather than a second code path.

Two properties are load-bearing and each has its own guard:

**An unanswered run writes a config identical to no config at all.** Asserted as
an enumerated differential — `dataclasses.asdict(loader(emitted))` equals
`dataclasses.asdict(loader(None))` field-for-field, for all four loaders, except
`vault_dir` — paired with a scope assertion that every catalogue key is present
and commented, because the differential alone passes just as happily on an empty
file. This is the empty-config-abstains invariant expressed at the wizard, and
`672ad2a` is what happens without it.

**An unanswered profile heading carries `DEFAULT_CRITERIA`'s own prose.**
`build_system_prompt_from` falls back to `core/criteria.py`'s shipped neutral criteria only when
the criteria text is missing or EMPTY, and a scaffold is never empty — so bare
headings would permanently strip the judge's abstain instructions while the
surrounding scaffold still told it to treat the profile as authoritative. The
heading set is DERIVED by splitting `DEFAULT_CRITERIA` on its own headings, so
there is no second list to drift out of step.

`cli.cmd_init` is the impure shell: it preflights the CONFIG destination before asking
anything, writes the config with an exclusive `open(dest, "x")` and the profile
through the STORE SEAM via `write_document(..., only_if_absent=True)`, and rolls
nothing back on a partial failure. It REFUSES when `--vault` and `$VAULT_DIR`
disagree, because `stores/vault.py:_make` is env-first and a precedence rule would
write to one path while the report named the other.

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
builds one, calls one method, and formats the result for the terminal -- so a surface
built today has nothing left in `cli.py` worth forking. `sluice/mcpserver.py` (#105,
extended #131) is the first one: a Model Context Protocol server exposing four
read-only tools (`list_leads`, `get_lead`, `doctor`, `health`) always, and five
write-capable tools (`dismiss_lead`, `apply_record`, `cv_run`, `cv_signoff`,
`create_lead`) under `--write`. Every write tool is a thin translation layer over
exactly one `Sluice` write method -- `sluice/mcpserver.py` itself contains no store
write (AST-enforced) -- so a write tool can never become a second, undocumented
write path for an invariant `Sluice`'s own methods already hold.

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

Another property joins those: **non-resurrection** (#81). A lead a human has merged
away via `merge_cluster` must remain discoverable by `upsert` **through the identity the
store recorded at merge time**, and must not be re-created when that identity is
presented again -- `test_merged_away_lead_is_never_recreated` pins this at the contract,
the same safety class as never-clobber: a synthetic-id store does not get it for free
just by archiving losers, since creating freely on top of that still resurrects them.

State it that way, not as an absolute "never re-created": a re-scrape whose identity has
**drifted beyond what the store recorded** is outside the guarantee. For the vault the
recorded identity is the note NAME the loser was seated at, so a re-scrape whose title
has drifted past every `Company - Title` name candidate `_resolve_path` builds is still
created -- a visible duplicate a human can merge again. The conformance suite exercises
only the location-split shape, so it does not police that residual; the contract does, by
naming it. A url index over the archive would close the gap; it is `#23` territory and
changes `upsert`'s cost model, so it is deliberately out of scope here.

`upsert`'s return vocabulary is six-member: `created`/`updated`/`merged`/`refused` as
before, plus `merged_away` and `merged_away_unproven`. Both write nothing. `refused`
now covers a third cause alongside #5's name collision and #1's ambiguous candidate: a
lead whose note would read back with neither company nor role, which has no name to be
seated at and which `_is_lead_note` then hides from every read — so creating it put an
unreachable stub in the vault and its lead in `seen.db`, which has no removal path. That
refusal is decided by running the read's own chain (`_split_frontmatter` → `_fm_dict` →
`_is_lead_note`) over the frontmatter `upsert` is about to write, rather than by a second
normalisation of the raw fields: `_fm_dict` ends in `.strip().strip('"').strip("'")`, so a
company of `"` or `'` is present to any raw truthiness test and empty to every read, and
each such spelling closed by hand leaves the next one open. `merged_away`
requires the store to have PROVED identity -- for the vault, a matching non-empty url on
both sides -- and only it may enter the dedup store. Every weaker match is
`merged_away_unproven`: the vault's location-token overlap, or an inconclusive
comparison. That one still suppresses, but it re-surfaces and re-reports on every run
until a human acts, because `seen.db` has no removal path and a same-company/title/
location RE-POST carrying a brand-new url is a real job -- recording it would suppress
that job permanently and invisibly, with no note anywhere to reverse it from.

**"Until a human acts" means one specific action**, and it is the same hand-move the
dedupe section below documents as the *recovery* path: move the archived note back out of
`_merged/`. The two are the same operation because the count is what a persistent
unproven match looks like from the outside. Once restored, the note is in the active view
again and the next scrape reconciles against it as an ordinary note. The outcome is
`updated` when the restored note matches on location (a location-only SAME) and `merged`
when the comparison stays inconclusive -- the same two verdicts that sent the lead to the
unproven arm in the first place, now reached against a live note. BOTH are on the sink's
allowlist, so either way it enters `seen.db` and the count stops; measured on both arms.
Nothing else clears it: there is no acknowledge command, and none should be added without
deciding what an acknowledgement would mean for a lead the tool could not identify. Note
what restoring costs when the two really were different jobs: the re-post's `last_seen`
lands on the restored note rather than minting its own, so a human who wanted them split
has to split them by hand. The run summary prints this as `N merged-away (unproven)`
(`cli.py:_print_report`), distinct from `N merged-away`, so the two are told apart without
opening the vault.

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
makes it stale. `job-sluice leads expire` REPORTS the stale set and writes nothing;
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

**`job-sluice leads dismiss --lead SLUG --reason REASON`** (#131) writes `dismiss`
by the same route as `leads expire` -- `require_status=_DISMISSABLE_FROM` re-read
inside the CAS transform, `require_blank={"pending_cv"}` refusing a lead holding
a #60 sign-off hold -- but is the ONE `leads` pass that writes unconditionally on
every call rather than reporting by default: the verdict it writes is one the
USER typed (`--lead`/`--reason`), not one this tool computed, matching the
pipeline commands' contract rather than `dedupe`/`expire`/`reconcile`'s. Resolves
by EXACT slug equality, never substring, and refuses (writes nothing) when the
slug names two or more notes rather than picking one.

**Preventing overwrites of hand-edits** (#109): a sibling guard to
`require_status`, also on `Vault.update_fields`, named `require_blank`. It
re-reads the named fields' **current** content inside the CAS transform and
refuses the write (returns `False`, matching `require_status`'s abstain
contract) if any of them hold non-blank content — refusing on **presence**
rather than value-inequality, so even a value identical to what would be
written still refuses. This closes a race where a company resolved from a
scraped page could silently overwrite a company a human typed into the note
during the multi-second resolution fetch. Like `require_status`, it is now
part of the `Store` protocol contract, so any future second Store
implementation must honor it too.

A fourth property sits beside the write contract, but a deliberately weaker one:
**read-path dedup** (#23) is human-gated, not automatic. `job-sluice leads dedupe`
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

The lead scan is **recursive**. `Vault._walk` defines the scan set once — every
directory under `Job Applications/Job Leads`, minus `_PRIVATE_SUBDIRS` at the **top
level** (today just `_merged/`) — and `read_leads`, `normalize_all_statuses` and
`_locate` all consume it, so the exclusion cannot be applied in one place and
forgotten in another. The prune fires only at `leads_dir` itself, and that
restriction is deliberate: `leads_dir/_merged` is the one directory `merge_cluster`
CREATES and `_archived_match` reads — it also CAS-writes the survivor note, which is not
under `_merged/` at all — while pruning the name at every depth would
instead hide a same-named folder the *user* made and mint duplicates of its notes.
Excluding it by name is load-bearing regardless: before the scan was recursive it was
invisible only because `os.listdir` is flat, and a walk that reached it would return
every loser `job-sluice leads dedupe --merge` archived, undoing #81.

Two rules follow from sharing a directory with the user's own notes. A file counts as
a lead when EITHER `company` or `role` is present (`_is_lead_note`), and is excluded
only when BOTH are absent — one surviving field is enough. The threshold sits there
rather than at "both present" because a hand edit that blanks one field (the #16 threat
model: a human in Obsidian) would otherwise make a real lead invisible to `read_leads`,
and a lead nobody reads is a lead nobody triages. And a lead's identity is its note NAME, not its
path: `_locate` searches the whole scan set, so a note the user files in a subfolder
is updated in place. `_locate` deliberately does NOT apply `_is_lead_note`, though: a
note un-findable there is re-created as a duplicate rather than merely dropped from a
read, so the cost falls the other way — a non-lead file squatting a lead's exact
candidate name is reconciled against as though it were a lead, unchanged from the
flat store. A name resolving to two or more notes is ambiguous identity and `upsert`
refuses.

**The scan set and the write folder are two different things** (#1). The scan set is
every directory a lead may be READ from; the **write folder** is the ONE directory a
new note is CREATED in. `Vault._write_folder()` resolves it through
`layout_subfolder("new", lead_layout)` — the status a created note actually carries —
so a created note is by construction already where its status implies, and `leads
reconcile` has nothing to do with a note ingest just made. It is made on the CREATE arm
only: `upsert`'s `leads_dir` makedirs sits above the update/merge/create fan-out, so
repointing that one would mint an empty `Active/` on a pure `last_seen` bump.

`lead_layout` (root config, `""` by default) selects the layout. `""` is flat and
byte-identical to the pre-#1 store; `"active_archive"` files leads into `Active/` and
`Archive/`. The Archive set is **derived** — `dismiss` plus every terminal read from
`core/status.py` via `is_terminal` — never hand-listed, so a terminal added there later
archives automatically. An unknown value raises and lists the valid names, at BOTH
`load_config` (so a YAML typo is a usage error, not a traceback) and `Vault.__init__`
(so the ~150 direct `Vault(...)` constructions are covered too).

**`job-sluice leads reconcile`** is the only pass that MOVES a lead note. It reports by
default and moves on `--apply`; there is no `--dry-run`, because the default *is* the
dry run. It moves notes only within the **managed** folders — the leads-dir root, plus
the layout's own folders. The root is seeded explicitly and is not derivable: under
`active_archive` no canonical status maps to it, and deriving the set left the root out,
so every note in a flat vault reported as user-filed and nothing ever moved. Five
classes are reported and never moved: a non-canonical status (`unknown`, never-regress);
a slug two notes claim (`ambiguous`, which this pass cannot repair — see above); a lead
in the user's own subfolder (`user_filed`, decision 4 applied to writes); a taken
destination (`collisions`, refused rather than suffixed, because the filename is the
slug is the identity); and a per-note failure (`skipped`) — an `OSError`, a destination
that cannot be created, or a destination that is a SYMLINK, which would file the lead out
of the scan set entirely and so is refused rather than followed.

It writes no note BYTES, only directory entries — but that is not never-clobber "by
construction". A move landing between `_cas_write`'s freshness re-read and
`_atomic_write`'s `os.replace(tmp, path)` RE-CREATES the source path, leaving two notes
at one basename and a lead `upsert` then refuses permanently. No portable stdlib
atomic-conditional-rename exists, so this is the same accepted residual as `_cas_write`'s
own micro-window: documented, warned about in the command's help, and REPORTED — an
applied sweep re-reads and names any basename now claimed by twice, so the run that
caused it says so rather than leaving a later ingest to surface it as an unexplained
refusal.

`reconcile_layout` is deliberately NOT on the Store protocol. The contrast with
`merge_cluster`, which is, is the useful one: #81 non-resurrection is a store-agnostic
OBLIGATION any store can satisfy (a SQL one with a tombstone row), whereas a folder
layout is a MECHANISM carrying no obligation — putting it on the contract would make
every other store pretend to honour a concept it does not have, which is what
`ensure_stfolder` was moved out of the protocol to avoid. The facade checks the
capability and the CLI renders a store without it as a usage error (rc 2).

#81's documented recovery is unaffected: a note hand-moved back out of `_merged/` is
found by NAME in whatever folder it lands in, so the next scrape reconciles against it
as an ordinary note.

That refusal covers the WRITE path only, and the read path has no equivalent. On a
flat store slug uniqueness held *by construction* — one directory cannot hold two
files with the same basename, and `Vault._slug_for` derives the slug from the
basename. A recursive scan removes that guarantee: with notes at
`Active/Acme - Analyst.md` and `Archive/Acme - Analyst.md`, `read_leads` returns
BOTH, at one slug. Three consumers key a dict on exact slug equality and silently
keep whichever twin they see last — `track/engine.py`'s `note_by_slug` and
`shortlist_by_slug`, and `core/app.py`'s `by_slug` in `expire`. A fourth,
`apply/select.py: select_all`, keys on nothing at all: it iterates
`read_leads({"shortlist"})` directly, so it kept BOTH twins. That one is the reason
a fix aimed at the slug-keyed dicts is not the whole fix — the batch path never had
a dict to harden. Three costs follow.

`select_all`'s only caller is `apply/engine.py: preview_all`, behind `apply prep
--all-shortlist`, so its cost is a REPORT defect rather than a write: preview_all
builds packets with `cv_staged=False` and never calls `cvfile.stage`, and no sluice
command submits an application at all — the packet's rules hand the form to the
human. One job therefore appeared twice in the printed ready queue, under one
label, and a human working down that queue works it twice. APPLY's single-lead paths
were never exposed: `select_one` and `record_one` both already refused
`len(matches) > 1`. CV's two were. `Sluice.compose_cv` (`cv --lead`) and
`Sluice.sign_off_cv` (`cv signoff --lead`) each filtered `read_leads` through
`slug_matches` and then took `notes[0]` — and `slug_matches` is a SUBSTRING match, so
two notes satisfy one typed fragment without any help from the recursive scan, which
merely adds a second way in by letting two notes share one slug outright. Composing
against the wrong twin seats the send-ready `tailored_cv` pointer that `apply prep`
reads on a lead the user did not name, and signing off the wrong twin releases a #60
hold on a CV no human reviewed. Both now refuse and NAME the candidates: `compose_cv`
returns one `skipped-ambiguous` CvResult per candidate (`run_batch`'s existing
vocabulary), `sign_off_cv` returns `select_one`'s `ambiguous: <ref> | <ref>` reason
string, and `cli.py` exits non-zero on each rather than printing a skip row among the
ordinary ones. Neither touches `slug_matches` itself: `expire` narrows by EQUALITY for
its own stated reason, and tightening the shared matcher would silently change `apply`.

`shortlist_by_slug` is the set `match_receipt` searches, so the dropped twin is
invisible to the receipt matcher and a receipt whose evidence fits it is weighed
against the survivor instead — and where the survivor's url HOST satisfies
`_hosts_match` against the sender with neither side multi-tenant, that survivor can
be auto-advanced to `applied`, an application recorded against the wrong note. Read
that on the host, not the url: `match_receipt` never compares urls, and
`_hosts_match` accepts a subdomain relation in either direction, so two twins on one
employer's site whose urls differ only by subdomain or path BOTH satisfy it.
Identical urls are sufficient, never necessary.

And `leads expire --expire <slug>` acts on one twin while the other is neither
expired nor reported `no-match`, so the human sees no sign the second exists.

All four now take their verdict from `core/leads.py: index_by_slug`, which DROPS
every slug two or more notes claim rather than keeping the last twin — the shape
`apply/select.py: select_one` and `track confirm` already use for an ambiguous
`--lead`. It is PURE: it returns `(index, dropped)`, the second element mapping each
dropped slug to its members, and the CALLER logs (through `ambiguous_slug_warnings`,
so the sites cannot drift into as many different wordings — five as of the fifth
consumer below). Returning the grouping is what
lets `select_all` and `track` stop rebuilding it. `select_all` walks notes rather
than slugs, so it uses `dropped` only as a membership test, and SKIPS those notes
with an `ambiguous:` reason naming both refs, which `preview_all` reports; dropping them silently would be the mirror
failure, a real application suppressed with nothing said. `leads expire --expire
<slug>` reports `ambiguous`, its own outcome rather than `no-match` — which would
say the lead is not stale when in fact two of it are — and the CLI classifies it
with the other write-did-not-happen outcomes, so the command exits non-zero.

A receipt is refused a write and gets a dead-letter row for review. Staying quiet
would apply the untracked-job ruling to a job that is tracked twice over, and the
message is `seen.add`ed and never re-queried, so the evidence would be lost
outright. `track/engine.py` therefore probes the DROPPED twins explicitly — only
after the deterministic pass came back empty, so it can never intercept a real
match, and on that probe's RESULT rather than on the mere existence of a duplicate,
which would make a false signpost of every unmatched receipt in a vault holding
one. The row carries no `--to applied`: `confirm` resolves a lead by slug, and this
slug is the one that resolves to two notes. It names the two notes to rename or
merge, and re-surfaces every run until that happens.

A FIFTH consumer, `triage/engine.py`'s enrich pass, reached the same defect by a
different route and needed the recursive scan for none of it. It did not key on
`note.slug` at all: the judge round trip is keyed on the dossier's `lead_id`, and
`DossierCache.get_or_build` stamped that field from `cache_key` — a hash of the URL, so
that two leads at one page share one cache entry rather than fetch it twice. That is
right for STORAGE and is the saving `cache_key` exists for; it is wrong for IDENTITY.
Two not-yet-deduped leads at one url (a re-scrape, a cross-post) were presented to the
judge under one id, both verdicts came back wearing it, and `note_by_id`/`by_id`
resolved each to whichever note was inserted last — one lead took the other's verdict,
the other took none, silently. The judge's copy of the dossier now overrides `lead_id`
to `note.slug`, alongside the four lead fields it already re-derives there, so the
on-disk cache entry and `cache_key` are untouched; that also makes the judge-stage audit
line's `slug` field hold a real slug, which it never did. Keying on the slug then brings
the bounded-uniqueness problem with it, so the pass takes `index_by_slug`'s verdict on
the twins like the other four, over the KEPT set rather than the whole read (a twin the
classify pass rejected cannot be misrouted — that write goes through `note.ref`), and
appends the shared `ambiguous_slug_warnings` line to `TriageReport.failures` rather than
only logging it, since a lead dropped from the run in silence is the mirror failure.

`read_leads` returns both twins — dropping one would take a lead out of the write
path's lookup too, and the next scrape would re-create it — and warns, naming both
paths. That warning is deduped per store on `(slug, refs)`, on the discipline the
symlink warning uses — but with no measured case behind it. Enumerated across all
eleven `read_leads` call sites, no shipped command reads one status set twice
through a single store: every `apply`, `cv`, `triage`, `leads` and `track confirm`
path reads once, and `track run`'s two reads take disjoint sets
(`APPLICATION_OWNED` and `{"shortlist"}`), so a twin lands in exactly one. The
suppression is kept as forward-looking, not as a fix for observed noise — a decision
that has to be reconciled with `track/receipt.py`, which DELETED its own unreachable
guard on the ruling that a guard for a state the code cannot reach is inert. Two
things separate them, and `core/vault.py: read_leads` states both: there the
unreachability is local and structural (the two tiers are disjoint by construction
within one function, so only editing that function can change it), while here it rests
on a survey of external callers that any new command falsifies without touching the
vault; and receipt.py's guard could not be witnessed at all, whereas this one is
reached through the public read path by two tests that read twice on one store, one of
which reddens when the suppression is deleted (measured: it is the only test in the
suite that does). The refs are in the key so a genuinely NEW collision at that slug is
still reported. Repairing the state is NOT reconcile's job, and #1 settled that
explicitly: the slug IS the note filename, so a rename orphans the note from
`_resolve_path`'s candidate walk and the next scrape mints a fresh one, while
choosing which twin survives is a merge decision `resolve_merge_status` owns.
`job-sluice leads reconcile` REPORTS the pair under `ambiguous`, names both paths and
moves neither; the repair is `job-sluice leads dedupe --merge`, or a hand rename.

Sluice does not write this state. Creates go to one directory, `_resolve_path`
refuses an ambiguous candidate, and — since a stale scan-set cache would otherwise
let a create mint a twin invisibly (see below) — the cache is re-derived before a
create stands. So it arrives by hand: a copied note, a note restored out of
`_merged/` into a subfolder beside a root twin, or a part-way manual
reorganisation.

The directory list itself is cached per `Vault` instance, and `_scan_dirs` is the only
thing that caches it — computed once there, not re-walked per lead. Of the scan set's
three consumers it serves exactly one: `_locate` reads the set through `_scan_dirs`,
while `read_leads` and `normalize_all_statuses` each call `_walk` fresh on every
invocation, so a filesystem change made mid-run IS visible to those two. The one
answer `_scan_dirs` never caches is "leads_dir is missing", since `upsert` creates
that directory mid-run and a cached miss would leave every later lookup in the same
run blind to it. The cache's own staleness window is a human filing a note into a
NEW subfolder while a run is in progress, and that window is closed on every verdict
`_locate` reaches by finding NOTHING: `_resolve_path` re-derives the list from disk,
cache bypassed, before such a verdict is allowed to stand, and re-resolves if the
folder set moved.

Left open it did not degrade gracefully. Nothing reads before it writes — the cache
is filled by the FIRST `_locate` the store performs, which is that same walk on the
run's first lead, since `read_leads` and `normalize_all_statuses` call `_walk`
directly and leave it `None` and the ingest sink never reads at all. So from the
second lead on it is a snapshot, and the very next lead of the same identity was
CREATED at the root name — sluice's own duplicate — and from the next run on both
twins were visible, the candidate resolved to two notes and `upsert` REFUSED the
lead permanently with its `last_seen` frozen (which, with `lead_ttl_days` set, ages
it into the stale set and offers a twin for dismissal). That is not the
create-race's direction: a create race re-resolves, SEES the raced note and updates
it.

The re-derive is gated on the CONDITION — `_resolve_candidates` reports whether any
candidate came back EMPTY, and `_resolve_path` keys on that — never on a list of
outcome strings. The arms that pay are therefore whatever leaves that `if not found:`
branch, today `create`, `merged_away` and `merged_away_unproven`, and a fourth added
later inherits the re-derive instead of silently opting out. The hand-listed form went
stale once already, shipping as `create` alone with the archive pair added afterwards
and nothing red in between, because a stale scan set is invisible by construction. A
guard keyed on `create` alone short-circuits before the archive pair, and measured
with the cache warmed, an archived twin under `_merged/` and the active note filed
into a new subfolder, the stale answer was `merged_away` against a fresh answer of
`updated` — and `merged_away` is the RECORDED arm, so `seen.db`, which has no
removal path, would suppress that lead permanently.

The three arms that DID identify a note — update, merge and refuse — are not
re-derived, and the reason is cost rather than impossibility. A stale list has two
directions and `missed` reports only one: found NOWHERE. The other is found ONCE
where a fresh list finds TWICE, and it does move an answer. Measured with the cache
warmed and a twin hand-filed at `Active/<the same name>.md` mid-run:
`('update', missed=False)` gives `updated` and `('merge', missed=False)` gives
`merged`, where the fresh answer is `refused` in both. Closing that means re-deriving
on the arms that carry a steady-state run, which is exactly the per-lead walk the
cache replaces. Measured on the 5500-note vault: 500 updates cost 247 ms with no
re-derive at all, against 2.1 s for a walk per lead.

The residual is bounded in a way the create-arm wedge was not, which is why the trade
goes this way. The write is a `last_seen` bump on one of two twins — never-clobber, no
note minted — and the state is not silent: `read_leads` warns on it and names both
paths, so every command that reads leads says so. What it costs is that the twin
enters `seen.db` (`updated` and `merged` are both on the sink's allowlist), so
*ingest* stops re-reporting the ambiguity, and the other twin's `last_seen` stays
frozen. Sluice does not create this state — it arrives from a human with a filesystem
— and repairing it belongs with `job-sluice leads dedupe --merge` (or a hand
rename). `leads reconcile` REPORTS such a pair and declines to move either note:
the filename is the slug, so it cannot rename, and it must not pick a survivor.

**`job-sluice leads rename`** (#151) is the file-*name* analogue of `leads reconcile` — the
two axes this store tracks, WHICH FOLDER a note sits in and WHAT BASENAME it carries, are
orthogonal, so the two passes can run in either order and neither disturbs the other's work.
`reconcile_layout` only ever moves a note between directories (its basename untouched);
`Vault.reconcile_names` only ever renames a note within its OWN directory (its folder
untouched) -- there is no `_managed_dirs()` gate the way `reconcile_layout` has one, so a note
the user filed by hand keeps that folder here, and no `lead_layout` gate either, since a wrong
basename exists whether or not a layout is even configured. It reports by default and moves on
`--apply`, the same shape as `reconcile_layout` and for the same reason -- there is no
`dry_run` parameter to be inert.

A note created against a blank or sentinel ("Unknown", ...) company (#151) is seated at
`" - <role>.md"`/`"Unknown - <role>.md"`; once triage backfills a real company the frontmatter
and the filename disagree, and `_resolve_path`'s candidate walk is keyed on the FILENAME,
never the frontmatter, so a re-scrape of the same posting mints a SECOND note rather than
finding the one already there. `reconcile_names` closes that gap by renaming the note in
place -- like `reconcile_layout`, it writes no note BYTES, only a directory entry.

The qualification is `_frontmatter_name`'s job, and it is an exact RE-DERIVATION rather than a
`" - "`-prefix heuristic: the current stem must be byte-identical to one of `_candidate_names`'
own outputs when called with the PLACEHOLDER head, so a human-renamed note, or one whose role
has drifted since it was seated without the file being renamed, is invisible to this pass by
construction. The rename target is ALWAYS candidate 1 -- never a location- or digest-suffixed
candidate the note may currently be seated at -- because `_resolve_path` always tries
candidate 1 first, and a note not sitting there is invisible to that first probe.

The report has seven buckets: `examined` (a count); `renames` (`(slug, target, folder)`
triples); `unresolved` (`(slug, company)` pairs -- the current name IS one this store minted
from a placeholder, but the frontmatter still offers nothing safe to rename to); `collisions`
(`(slug, target, reason)` TRIPLES, unlike `reconcile_layout`'s bare pair, because the reason
distinguishes which of three collision layers refused); `ambiguous` (the same
`index_by_slug`-derived bucket `reconcile_layout` reports -- two notes already claiming one
slug cannot be repaired by either pass); `resurrected` (a note whose OLD basename re-appeared
after an applied rename, on a probe narrower than `reconcile_layout`'s own post-sweep
`ambiguous` re-read: a raced RENAME re-creates the source at a DIFFERENT slug from the new
one, invisible to `index_by_slug`, so this pass instead re-checks `os.path.exists` on each
renamed note's pre-sweep path); and `skipped` (a symlinked note -- left alone as a structure
the user deliberately built, not a detachment hazard, since source dir == dest dir for a
rename -- or an `OSError`).

COLLISION HANDLING has three layers, because `_reserve_and_move`'s own `O_EXCL` reservation is
scoped to ONE directory, and source dir == dest dir for a rename -- so it alone cannot see a
note already seated at the target basename in a DIFFERENT folder, exactly the cross-folder
duplicate shape #151 itself reports and that only a recursive, layout-aware vault makes
possible. Layer 1 is a vault-wide precheck, `self._locate(target)`, against the pre-sweep
vault (re-derived via `self._rescan_dirs()` immediately beforehand, mirroring
`_resolve_path`'s own re-derive before trusting an absent verdict). Layer 2 is a within-run
precheck: two stale notes in the SAME sweep that would both mint the identical target are
grouped and BOTH refused, computed as a separate pass over every note's already-decided target
so the outcome cannot depend on iteration order. Layer 3 is
`_reserve_and_move(..., suffix_on_collision=False)` itself, the last word against a writer
racing outside this sweep's own read -- refused rather than suffixed, since a numeric suffix
would change the basename, which is the slug, orphaning the note from the very re-scrape this
pass exists to let find it.

The ACCEPTED RESIDUAL: layer 2 refuses BOTH notes whenever two genuinely DISTINCT leads --
same company and role, a different city, say -- happen to backfill to the identical bare
candidate-1 target, with no candidate-2/3 fallback attempted ("always candidate 1" is the
deliberate simplifying rule, not an oversight). They are reported as colliding on every run,
forever, with nothing here telling the operator they are not actually a duplicate pair; the
remedy is manual -- a human renames one of the two by hand to a name outside
`_frontmatter_name`'s minted set, which this pass then leaves alone by construction.

`Sluice.rename(apply=True)` additionally migrates the dead-letter store's rows for every note
actually renamed, since a dead-letter proposal is keyed on the lead's slug and a rename
changes it -- otherwise a proposal filed against the OLD slug becomes permanently unreachable
by `track confirm`/`track dismiss --lead` the moment the note moves out from under it. The
dead-letter store's reachability is checked BEFORE any vault rename runs, so an unreachable
store refuses the WHOLE operation with zero notes renamed rather than stranding some
proposals; once renames have actually landed, a PER-PAIR migration failure is isolated into
`report["deadletter"]["failed"]` rather than rolled back, since undoing a rename that already
succeeded would trade a recoverable problem (a stray dead-letter row still filed under the old
slug) for the unrecoverable one this feature exists to prevent -- a duplicate note minted on
the next scrape.

An unreadable directory in the scan set **raises** (`os.walk(..., onerror=)`). The
default swallows it and yields nothing, which would make every lead beneath it
invisible to the read path and to the write path — i.e. re-created — from one
permissions bit. The same rule binds every probe that decides whether a path in the
LEAD TREE is there, because `os.path.exists`/`isdir`/`isfile` all swallow EVERY
`OSError` and so read an unstatable path as an absent one. There are two, and between
them they cover all four such decisions in that tree. `_is_dir` — which `_scan_dirs`,
`read_leads` and `normalize_all_statuses` each ask whether `leads_dir` exists at all —
answers False only to `FileNotFoundError` and lets a `PermissionError` out.
`_is_note_file`, `_locate`'s per-candidate probe, answers FOUND only for a regular file
and absent only for `FileNotFoundError`/`NotADirectoryError`. `_is_dir` has one more
caller, outside that tree and bound by the same rule for a different reason:
`read_experience_entries`, below.

Each of the four `_is_dir` callers had to be converted separately, and
`normalize_all_statuses` was the last of the three under `leads_dir` —
worth stating because it is the one that WRITES, so its silent empty read was reported
back to the CLI as a successful sweep that canonicalized nothing. Its `os.path.isdir`
False also short-circuited *before* `_walk`, so `onerror=_reraise` never fired. Measured
with the parent directory at mode 600: `read_leads` raised `PermissionError` while
`normalize_all_statuses` returned `{'changed': 0, 'unchanged': 0, …}` over a vault
holding a real note.

`_is_note_file` is the sharpest of the two: absent is the branch that CREATES
and that can record a `merged_away` in `seen.db`, so a directory at mode `r--` — which
`os.walk` still lists, so `onerror` never fires — made every note inside it read as
gone. Measured against a live `applied` note with a url-identical archived twin:
`merged_away`, recorded, `last_seen` frozen, and the only log line said the lead had
been merged away.

The fourth `_is_dir` caller sits outside the lead tree. `read_experience_entries`
probes the *Experience Library*, which no scan walks and which no write path is keyed
on, so its harm is not a re-created lead and it took the rule on its own merits: these
entries are the ONLY citable evidence the hard fabrication gate recognises, so an empty
read leaves a bundle with no ids and every WORK bullet violates it — measured,
`BAD CITATION` for a bullet that cites and `UNCITED BULLET` for one that does not.
The CV is therefore never rendered — it fails CLOSED — and what it costs is that a
permissions problem reaches the user as `skipped-gate`, a fabrication verdict against
their composer, after a dossier fetch and a full compose have been paid for. The silent
case needs the VAULT ROOT to be unstatable, since with the library itself at mode 000
`os.listdir` already raises: `os.stat(base)` is then the call that fails, and
`os.path.isdir` turned that into `return []`.

`os.walk` does **not** follow symlinks, and that default is kept: following would let
a link loop spin the walk and let a link out of the vault pull arbitrary directories
into the scan set. A symlinked subfolder is therefore invisible to `read_leads` and
to `_locate` alike, so every lead behind one is re-created — and symlinking a folder
into an Obsidian vault is ordinary practice. `os.walk` still LISTS an undescended
symlink in `dirnames`, so the scan warns about any that holds `.md` files at ANY
depth — the probe walks the target and short-circuits on the first hit, because a flat
listing missed exactly the nested layout a recursive scan invites (measured: a note at
`<target>/2025/` came back `created`, the `applied` original untouched, with no log
line at all). An unreadable target is reported rather than skipped; the warning itself
never raises, since the caller is the one definition of the scan set. Links holding no
notes stay quiet, since a warning that fires on every walk for a harmless one is a
warning users learn to ignore.

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
and excluded from `read_leads` by the prune above (`_PRIVATE_SUBDIRS`), not by
the flat-listing accident that hides the Experience Library's `_inbox/` from
its own read -- that listing is still `os.listdir`, so a subdirectory is never
descended into at all; the lead scan used to work the same way, before #1 made
it recursive. A loser's own downstream state (scores, notes,
a rendered CV, a sign-off hold) is therefore INTENTIONALLY dropped from the
active view on merge, recovered only by moving the note back out of `_merged/`
by hand; the report flags a loser carrying a rendered CV (`tailored_cv`), an
open sign-off hold (`pending_cv`/`needs_signoff`), or an application-owned
status -- not merely a score or a notes field -- so the human sees what a
merge would discard before naming it.

`_merged/` is no longer write-only (#81). `Vault.upsert`'s create arm now reads it
too: before minting a brand-new note, it lists the archive and, for each entry whose
filename could belong to one of the incoming lead's name candidates, compares the name
`merge_cluster` stamped onto that entry (or, for a legacy or stamp-failed one, its own
filename) against the candidate, then runs the same verdict the active walk uses. A match
the incoming lead's own url PROVES -- both urls non-empty and equal -- returns
`merged_away` and creates nothing; every weaker match (a location-token overlap, or an
inconclusive comparison) returns `merged_away_unproven`, which also creates nothing but
never enters `seen.db`. This moves the create path's cost: it used to be a bare
`not os.path.exists` check, ZERO reads. It now costs one `os.listdir(_merged/)` on
every create -- cheap, and the overwhelmingly common case when nothing has ever been
merged -- plus, only for an entry whose filename matches a candidate, one read and one
frontmatter parse.

**Do not prune `_merged/`.** It was a reversible archive; it is now load-bearing. It is
the backstop the write path consults when the dedup set is empty -- a fresh machine, a
retargeted `SEEN_DB`, a 0-byte or tableless database -- which is precisely the situation
non-resurrection exists for. Deleting entries out of it destroys both halves at once: the
guarantee (a pruned lead is re-created on the next scrape, which can mean a second
application under the user's name) and the documented recovery path (moving the note back
out of `_merged/` by hand is the only way to get a merged-away loser's scores, notes,
rendered CV or sign-off hold back). Nothing in sluice prunes it, and nothing should; a
vault-cleanup script that treats it as scratch is the way this gets lost.

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
  This seam has a second, OPTIONAL member too: `preflight() -> dict`, the same
  shape as the renderer seam's `precheck` below (undeclared on the `Protocol`
  for the identical reason -- an optional member must stay optional to
  declare). `job-sluice doctor` reaches it via `getattr(store, "preflight", None)`;
  an implementation that omits it reports nothing for that component rather
  than being treated as broken. `Vault.preflight` returns FACTS only (does the
  vault directory exist, is the baseline CV readable, is a Judging Profile
  present, how many Experience Library entries are verified) -- never
  verdicts, which stay in `core/doctor.py` alongside the backend classification
  rules. It is read-only by contract: stats paths and reuses this store's own
  read methods, never opens anything that does not already exist, so it
  cannot disarm the #81 relocation notice above.
- **renderer**: `sluice/renderers/`, selected by `cv.renderer:` (default
  `template`). Implementations: `template` (fills a user's own Jinja2 template --
  or the packaged default at `sluice/templates/cv_plain.html.j2` when
  `cv.template` is blank -- with the parsed CV, then renders it via WeasyPrint;
  needs `pip install -e '.[render]'`) and `script` (the full-control escape
  hatch: shells out to an external render script at `cv.render_script`). Note
  the shipped `render_script` default points at a file that does not exist in
  the repo; `script` says so at construction rather than dying after a CV has
  been composed and gated. `weasyprint` -- the earlier bundled renderer, a
  fixed `<pre>` dump with no template -- is RETIRED: selecting it now raises,
  naming `template` as the replacement, rather than silently falling through
  to a default or a confusing "unknown adapter" error.
  This seam has a second, OPTIONAL member: `precheck(cv_text) -> list[str]`.
  A renderer implements it when the composed CV must satisfy a grammar of its
  own that the fabrication gate does not model — `template` does (its meta-line
  grammar), `script` deliberately does not, since it shells out to arbitrary
  user code and has no grammar to impose. `cv/engine.py` reaches it through
  `getattr(renderer, "precheck", None)` inside its compose/gate retry loop and
  folds the strings in with the gate's violations, so a formatting complaint
  reaches the model's one retry instead of arriving at render time, after the
  LLM spend and past the only recovery there is. It is NOT a second fabrication
  gate and must not become one: it reports SHAPE, never facts. Keeping it on
  the renderer is what stops one implementation's requirements binding the
  whole seam — measured, the engine calling `parse_cv` unconditionally reported
  `skipped-gate` under `cv.renderer: script` for a gate-clean CV that script
  would have rendered. `precheck` still carries renderer-SPECIFIC grammar only
  (`template`'s meta-line format); the pre-`PROFILE` name/contact header block
  is enforced separately, by three inline STRUCTURAL guards in `cv/engine.py`
  itself (#99: header line count, name anchor; a third added on review,
  comparing the contact lines' actual content against `cvcfg.contact`), because
  that shape is what `cv/compose.py`'s prompt requested of every renderer
  alike, not a layout requirement any one renderer owns.
- **fetcher**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server). The dossier
  fetch closure built from it (`Sluice.dossier_cache`) reads
  `document.body.innerText` for the JD, and -- for triage's tier-2 company
  resolution (#109) -- also `document.title` and EVERY
  `script[type="application/ld+json"]` tag, parsed in the page and returned as
  one JSON array (a board's own JobPosting schema is routinely not the first
  such tag), in the same already-open tab. The JD read is a hard refusal on an
  unreadable body; the two resolution-only captures are best-effort and degrade
  to `""` instead.
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.
  A source may optionally implement `company_from_url(url) -> str | None`
  (#109), the same optional-member shape as `Store.preflight`/
  `Renderer.precheck` above -- `Sluice.triage()` threads `sources.get` into
  `triage.engine.run` as `get_source`, the same lazy inside-the-method import
  `ingest()` already uses; `triage/` itself never imports `sluice.ingest`
  directly.

`job-sluice doctor` is a read-only preflight over the whole pipeline, not only the backend
seam: it enumerates every configured backend (primary and fallback, per sub-app) and
classifies each as `ok`/`degraded`/`dead`, then does the same for a second table of
component checks -- the renderer (does `cv.renderer` actually construct, catching a
missing `render` extra or WeasyPrint's native libraries before the dossier fetch and
LLM spend rather than after), the CV identity fields (`cv.name` still the shipped
placeholder, `cv.contact` blank), the store's on-disk artefacts (the vault directory,
the baseline CV, the Judging Profile, Experience Library entry counts, via the
Store seam's OPTIONAL `preflight()` hook), track's Google adapter, the Camofox profile an
ingest run will drive, and the current
posture (abstaining or active) of every list-typed preference gate. Backend
classification is role-aware -- a keyless fallback degrades (the sanctioned
primary-only path, exit 0), while a keyed-but-broken backend is `dead` regardless of
role, the silently-non-functional fallback the tool exists to catch. Component
classification adds a fourth state, `notice`, for the gate-posture rows: it NEVER
affects `exit_code`, under `--strict` or otherwise, because an abstaining gate (an
unconfigured preference simply passes every lead through) is the shipped default and
legitimate -- grading it as a failure would be the 672ad2a class of bug (see Invariants)
aimed at doctor's own exit status. Live round-trip by default; `--offline` for a
config-only check (the component checks were already local, so `--offline` changes
nothing about them); `--strict` to also fail on degraded. See `docs/USAGE.md` for every
flag `doctor` and the rest of the CLI take, and `docs/CONFIGURATION.md` for every config
key these seams read.

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

