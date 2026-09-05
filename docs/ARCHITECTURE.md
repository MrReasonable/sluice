# Architecture

## `core/`

Shared by every sub-app:

- `config.py`: layered config. Code defaults, overridden by a `sluice.yaml`
  file, overridden last by environment variables. Also holds
  `validate_search_entry`, the shared `sources.<id>.searches`/`searches_spec`
  grammar `ingest/base.py` imports rather than the reverse -- see the source
  contract discussion below.
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
  dismiss, and -- #169 -- unjudgeable, stamped in place of a judge verdict
  when a dossier's job description never arrived); track owns the later ones
  (applied, phone_screen, ... offer, rejected); neither overwrites the
  other's. The one crossing between the two lifecycles, `shortlist ->
  applied`, has two actors -- apply (on send) and track (on a domain-matched
  confirmation receipt) -- both gated by the same `can_apply` predicate.
  `DEFAULT_TRIAGE_STATUSES` (`new`, `research`, `unjudgeable`) is the one
  hand-picked RETRY subset `triage run --status` selects when the user names
  nothing -- deliberately NOT derived from the triage-owned set as a whole,
  which also holds `shortlist`/`needs_review`/`dismiss`, so a derivation
  would re-judge leads a human has already decided about, every run.
  `unjudgeable` belongs in it because that IS the retry: the cache no longer
  persists a fetch that produced no JD (see `dossier.py` below), so the next
  run refetches.
- `roletype.py` (#223): the `role_type` closed set, its provenance ladder, and
  `observe_role_type`, which reads a pay basis off a job description. Pure apart
  from one thing, named because this page qualifies purity precisely everywhere
  else: `normalise_role_type` WARNS on an unrecognised value, and it runs in
  ingest's per-row loop and in classify's read path. Nothing else here touches
  the disk, the network or a clock.
  `normalise_role_type` folds every spelling a real vault holds to
  `contract | permanent | ""` and WARNS on anything else -- deliberately the
  opposite of `status.py:normalize`, which passes an unrecognised status through
  untouched so a genuinely new state is never silently rewritten. A status is a
  state a human chose; a role_type is a two-valued fact about pay basis, and the
  gate's contract branch was a SUBSTRING test, so `contract-to-perm` -- a value
  whose whole meaning is "both" -- took the contract branch on its first eight
  characters.

  The ladder is `observed > declared > assumed > ""`: the posting's own words,
  then the user's assertion (a search they configured, or a lead they typed),
  then the tool's guess (a shipped example search, a source's `extra`). Only the
  top two are consulted by the relevance gate -- #223's complaint is that
  `role_type` recorded which SEARCH found a lead and was then read as a fact
  about the job. A note with no `role_type_source` key reads as `assumed`, which
  fails toward not trusting.

  `observe_role_type`'s vocabulary is high-precision and low-recall on purpose,
  and the asymmetry is the design: an observation outranks every other origin, so
  a wrong one overwrites the user's own declaration, while a blank one leaves
  today's behaviour in place. "Full-time" is absent for exactly that reason --
  it describes HOURS, not basis, and contract adverts carry it routinely. The
  markers are English/UK-board idiom; another market's spellings abstain, which
  is a visible gap rather than a silent misread.
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

  `DossierCache.jd_arrived(dossier)` (#169) is the single owner of "did a JD
  actually arrive" -- a predicate over the fetched `jd.markdown`, not a
  marker key riding along in the returned dict (which `slim()` would have to
  exclude by name, the way it already excludes `lead_snapshot`/`page_title`/
  `structured_data`). `get_or_build` asks it before PERSISTING a fetch: the
  freshly-fetched dossier is always returned to the caller, but a fetch that
  produced no JD text is never written to disk, so the cache stops serving a
  fetch failure for the whole TTL -- before this, an empty scrape was cached
  exactly like a real one, and the nightly retry set (`DEFAULT_TRIAGE_STATUSES`
  above) paid for the same non-answer every run until the entry aged out.
  `_fresh()` applies the same predicate to a cache HIT, so an entry written
  before this existed -- or one a later refetch still failed to fill -- is
  treated as stale and retried on every read rather than served for the rest
  of its TTL. `min_jd_chars` (root `Config`, default `0`: only a wholly empty
  JD ever fails) is the shared floor below which a fetched JD counts as not
  having arrived; `Sluice.triage()` and `Sluice.compose_cv()` both build their
  `DossierCache` from `self.config.min_jd_chars`, since triage and cv already
  share this one cache directory (#80) and must agree on the floor. Triage's
  enrich pass asks `jd_arrived` per dossier and marks the lead `unjudgeable`
  (never spending a judge call on page chrome) rather than letting it collapse
  into `research`; `cv/engine.py`'s `run_one` asks the identical question and
  sets `CvResult.dossier_failed` when it (or the fetch itself) fails, composing
  anyway rather than refusing the lead outright. The two sub-apps ask the same
  question and take DIFFERENT actions on purpose. Triage abstains because
  judging chrome spends a real judge call and writes a verdict nobody can
  trust; composition proceeds because a thin JD costs tailoring QUALITY, not
  correctness, and the gate still citation-checks every bullet against the
  bundle. So a fetch that RAISED composes with `jd=""` (there is no text), while
  one that returned sub-floor text keeps it -- identical at the shipped
  `min_jd_chars: 0`, where only a wholly empty JD fails the predicate, and
  divergent above it.
- `Sluice.health_report(include_leads=False)` (`core/app.py`) is the
  per-source health REPORT `job-sluice health` and the MCP `health` tool both
  show: `HealthStore`'s baseline/recent counts and stuck-streak reason,
  merged with the source registry, sorted by source id. NO vault I/O by
  default, so an ordinary, cheap-and-often call costs nothing beyond a file
  read; opting in (`include_leads=True`, `job-sluice health --leads`) adds
  exactly one `read_leads(_CONCLUDED)` pass, which fills each source's
  `unjudgeable`/`concluded` counts (#169). `_CONCLUDED` is every triage-owned
  status except `new` -- the leads triage has reached a conclusion about, in
  either direction. It is deliberately NOT `DEFAULT_TRIAGE_STATUSES`, which is
  the SELECTION default: a lead leaves that set as soon as it is judged, so the
  numerator stayed while the denominator drained and the printed rate climbed
  toward 100% as a source got HEALTHIER (measured: 500 scraped, 480 dismissed,
  17 judged, 3 stuck printed `3/3`). Including `dismiss` does dilute a source
  that breaks today against its own history; that trade is taken deliberately,
  because a false alarm in a health report trains people to ignore the row,
  and `detect_drift`'s per-run reasons plus the ingest breaker are what
  actually catch a source breaking today. Classifying whether the resulting
  rate is bad is left to the caller; `health_report` reports facts, not a
  verdict.
- `candidate.py` (#133/#107): derivations over `CandidateProfile` (the
  dataclass itself lives in `protocols.py`, mirroring `criteria.py`'s
  type/logic split). `full_name`/`contact_block` build the CV header from the
  five identity fields; `has_any_declared` is the "does this note say
  anything at all" probe `cmd_init`'s write gate and re-interview gate both
  share; `age_from_dob` derives the `apply` packet's `age` key. Read through
  the Store contract's `read_candidate_profile()` (`CANDIDATE_PROFILE_RELPATH
  = "Job Applications/Candidate Profile.md"`, a MUST-support member alongside
  `CRITERIA_RELPATH`, with a defined abstain: an all-blank `CandidateProfile`
  for a missing document, never `None`). Consumed by `cv/engine.py`,
  `apply/packet.py`, `Vault.preflight` and `cli.py::cmd_init`.

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

   Per-source **health** (`core/health.py`) is a run history + drift
   classifier, not merely a row counter: a rotted extractor's dominant
   failure mode is succeeding at reading the wrong page, not crashing, and
   a row count alone cannot tell that apart from a genuinely quiet search
   (#156). `HealthStore.record` appends `{count, signals}` per run (a
   30-run rolling window, `_KEEP`), and `detect_drift` classifies the
   CURRENT run against that history in TWO regimes, not one flat ladder --
   which reasons are even reachable depends on whether the run's count is
   zero or positive, and getting this flattened into a single ordered list
   would wrongly suggest `auth` outranks `fallback`, when in fact `auth`
   never competes with it at all.

   First, `_explained` computes at most ONE candidate reason, in this
   precedence (first match wins): `unreachable` (the browser never gave us
   a tab or a `cam.evaluate()` call failed -- checked FIRST because it
   explains every other signal's absence, e.g. a Camofox outage),
   `redirect` (the landed HOST differs from the requested one, apex-`www`
   exempted via `_dewww`),
   `login` (the landed URL's PATH carries a segment from a small
   vocabulary -- `login`, `authwall`, `session`, `challenge`, ... -- that
   the requested path did not itself ask for; segment-**prefix** matching
   with a non-alphanumeric boundary, not exact-segment (misses
   `/authwall`) or substring (matches `/author/...`)), `blocked` (a
   source-specific rate-limit signal, e.g. `workinstartups`'s
   HEAD-precheck), `auth` (an opt-in `auth_probe_js` found the page's
   LOGGED-OUT markup -- only `linkedin` ships one today), or `None`.

   `detect_drift` then branches on count. At `count == 0`, `_explained`'s
   candidate is returned as-is (any of the five above), or `zero` if it
   found none -- this is the ONLY regime `auth`/`unreachable` can ever
   surface in. At `count > 0`, only `redirect`/`login`/`blocked` survive
   from `_explained` (a login wall or a rate-limit page can still return
   rows); `auth`/`unreachable` are discarded even if `_explained` computed
   them, to avoid firing drift off one search's stale signal in a
   multi-search source. Past that point sit two count>0-only, content-
   inspecting reasons NO count==0 run can ever reach: `fallback` (a row
   the extractor's own degraded path stamped -- `ingest/base.py`'s
   `_first_degraded` promotes the marker from RAW rows, so it survives
   even a row `parse` later drops on a blank title), then `paths` (#153 --
   EVERY raw row was rejected by the source's own `posting_paths`
   allowlist, i.e. the board renamed the path its postings live at; the
   gate is EQUALITY with `count` rather than a ratio, and both terms are
   accumulated off the same hint on the same line of `_run_source`, so
   numerator and denominator cannot straddle two populations or two
   pipeline stages. A PARTIAL rename is deliberately not caught here and
   is not claimed to be -- `count` is RAW rows and stays healthy -- which
   is why `SourceResult.rejected_paths` is printed by `_print_report`
   whether or not this gate fires), then `blank` (a
   company/link completeness collapse measured over EVERY search's
   **parsed** leads this run, aggregated rather than taken from any one
   search's snapshot -- never the raw payload, since a source's `parse`
   can repair a field the raw row lacks, e.g. `naukrigulf` recovering a
   company mashed into the title via the listing URL's own seam). Last, `drop` (the bare
   count falls below 40% of the 7-run median baseline), then a healthy
   `None`.

   `blank` needs history a bare `{count, signals}` record cannot supply on
   its own: `HealthStore` also persists a STICKY per-signal high-water
   (`rate_high_water`), updated as `max(stored, this_run)` on every
   `count > 0` run and deliberately kept SEPARATE from the 30-run rolling
   window -- deriving it from that window instead would make a source that
   rots and stays rotted fire for exactly 30 runs and then go permanently
   silent once its one healthy run ages out of the retained history, which
   measured shorter than any of #156's real incidents lasted. `blank`
   fires only when: the source's own high-water is at least 0.8 (a source
   that never carried a high completeness rate cannot have "collapsed"
   from one -- 0.5 was tried and several real sources' ordinary variance
   crossed it), the current run's rate is below 40% of that high-water,
   AND the run immediately before it was ALSO below that threshold (one
   bad run alone is noise, not a streak -- costs one run of detection
   latency, measured to remove nearly all false positives on small or
   partial-completeness sources). Below 8 parsed leads, no rate is
   computed at all -- "no rate measured" and "a rate of exactly 0.0" stay
   distinguishable, and a tiny sample (a comma-less title on a 1-2-row
   carousel read) is noise a floor exists to exclude.

   WHAT IS MEASURED AND WHAT IS CLASSIFIED ON ARE TWO DIFFERENT ROSTERS
   (2026-08-27). `RATE_SIGNALS` is everything `_lead_rates` computes and
   `record` high-waters -- `company_rate`, `link_rate` and `location_rate`.
   `BLANK_SIGNALS` is the strict SUBSET `_blank_reason` classifies on, and
   it is company and link only. `location_rate` exists because location
   was previously measured nowhere at all, which is how reed served ~20
   rows a run with location on none of them while every check stayed green
   -- the vocabulary was company and link, and reed kept both. It is kept
   OUT of the classifying set because `blank` sits in `BREAKER_REASONS`
   and withholds every lead the source produced: that is the right price
   for a company collapse (incident 1's harm was ~185 blank-companied
   notes burned into `seen.db`, which has no removal path) and not
   obviously right for a location one, where a lead that kept its title,
   company and link is still worth having. Promoting it is a real option
   and needs its own measurement against the fleet's healthy windows, the
   same evidence the row floor and the 2-run streak were each chosen on.

   THE HIGH-WATER FLOOR HAS A BLIND SPOT, AND IT IS REPORTED RATHER THAN
   CLOSED. Skipping any signal whose high-water never cleared 0.8 is right
   for a board that genuinely does not publish a field (weworkremotely's
   extractor hardcodes an empty company) and silently wrong for one that
   was ALREADY broken when its first run was recorded: the high-water only
   ever climbs, so such a source never establishes a bar to fall from and
   is exempt for good. Measured 2026-08-27, reed's company high-water was
   0.1, taken from a run whose extractor was already reading the wrong
   elements -- the one check that would have reported the collapse was, by
   construction, switched off for exactly the source that needed it. Every
   source is in this state after the health file is first created or lost.
   It is NOT fixed by lowering the floor or adding an absolute one: `blank`
   bins a source's whole run, so a board that legitimately lacks a field
   would be binned daily, and nothing local can tell that case from a
   stopped selector. `HealthStore.unguarded_signals` names the exemption
   instead and `ingest list-sources --health` prints it -- the rates per
   source, plus `UNGUARDED(<signal>)` BY NAME, shown for ENABLED sources
   only (nothing runs for a disabled one, so no guard can be blind). A
   human rules on which case a given source is, and records the benign
   ruling with the source's own `unpublished_fields`, which silences the
   flag for the named field only -- without it, the two boards that
   hardcode an empty company light it on every invocation for ever, and a
   flag permanently lit on known-benign rows is how a report teaches its
   reader to skip the column.

   A source whose NEWEST run recorded no rate is reported `UNMEASURED`
   rather than carrying a rate with no flag: below `_RATE_ROW_FLOOR`,
   `_lead_rates` withholds every rate key, so `_blank_reason` sees no
   rate for this run and returns False whatever the high-water says --
   the source genuinely is not guarded. The gate is the AGE, not the
   presence of a rate: `age != 0` covers both the never-measured case
   (`-1`) and the merely STALE one, because a rate retained from three
   runs ago says nothing about whether the guard is live now.
   `unguarded_signals` is consulted only at `age == 0`. Gating on "some
   rate exists" instead let a stale rate render with no `UNMEASURED` and,
   on a low high-water, a confident `UNGUARDED` -- two claims about a
   guard that was not running. The rates themselves come from
   `HealthStore.latest_rates`, which walks BACK to the last run that
   carried any and returns how many runs back that was; the CLI prints
   that age, because a rate up to 30 runs old rendered as this run's
   measurement is the same reassuring-stale-100% failure the whole report
   exists to remove. Note the distinction the code actually draws is "no
   rate in the retained 30-run window", which is not the same as "never
   measured" -- an old measurement can age out.

   `should_retire` (three consecutive `_is_dead` runs) and `_RECOVERABLE`
   (`auth`, `blocked`, `unreachable` -- reasons an OPERATOR ACTION brings
   back, so they defer retirement indefinitely) predate #156 and are
   UNCHANGED by it: `fallback`/`blank` are count>0-only phenomena and were
   never candidates for either. `login` is deliberately NOT
   `_RECOVERABLE`, even though it sounds like the `auth` case -- `_is_dead`
   already short-circuits on `count > 0`, so membership never mattered for
   the incident that motivated it (a login-walled board that still
   returned a constant, low row count), and including it would grant a
   PERMANENTLY paywalled board the same unlimited life this repo's real
   auto-retire history (`sources/hired.py`, `sources/hackajob.py`, both
   retired by hand after the board moved) exists to catch.

   `fallback`/`login`/`blank` also gate the WRITE, not merely the report:
   `engine.run()`'s `BREAKER_REASONS` withholds a source's leads from the
   sink entirely for a run classified as one of the three, rather than
   writing them and merely flagging the drift. Health is still recorded
   unconditionally first, so the next run's baseline/high-water reflect
   what was actually fetched. A withheld lead is simply never passed to
   `sink.write()`, so it never enters `seen.db` and the next run re-fetches
   and re-evaluates it from scratch once the rot clears -- the identical
   self-healing discipline `sink.py`'s own `refused`/`skipped`/
   `merged_away_unproven` outcomes already follow, needing no special-case
   recovery path. `redirect`/`blocked`/`auth`/`unreachable`/`zero`/`drop`
   stay report-only, each for its own reason: `auth`/`unreachable`/`zero`
   are structurally count==0-only, so there is nothing to withhold;
   `blocked`'s one shipped producer (`workinstartups.py`'s HEAD-precheck)
   always returns zero rows when it fires, though the classifier itself
   permits a future source's `blocked` to carry a positive count;
   `redirect` genuinely CAN carry a positive count (a cross-host redirect
   landing on a page that still parses rows) and is left out anyway
   because it predates this change and withholding on it is a separate
   scope decision; and `drop` is the lowest-confidence signal here -- a
   bare row-count comparison with no content inspection behind it, so
   suppressing a real day's leads on a false `drop` would be a worse
   failure than a late report.
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   A lead classify() leaves at blank/placeholder-company `needs_review` gets one
   resolution attempt (`resolve.py`, #109/#120/#151) before that: a free
   regex over the role text's own trailing "<role> at <Company>" clause,
   tier 0, a free URL-pattern tier 1, an opt-in, no-LLM page-visit tier 2,
   then -- also opt-in, and only when tiers 0-2 all abstain -- an LLM
   read of that SAME page data, tier 3, on a SEPARATE backend from the
   judge's (always the cheap "fallback" role, regardless of `--backend`)
   -- so "for free" no longer describes the WHOLE classify pass
   unconditionally: a blank/placeholder-company lead can trigger a real page visit when
   `triage.company_resolve_fetch` is on, and an LLM call when
   `triage.company_resolve_llm` is also on. `apply.py` writes verdicts
   back, skipping any lead already in the application lifecycle (its own
   writes, and the new resolution write, are all `require_status`-guarded
   against a lead entering that lifecycle mid-run); `audit.py` logs every
   decision that actually landed -- a lead whose write was refused (already
   application-owned, or a status change mid-run) is logged nowhere, so the
   audit never claims a decision that was not applied.

   `classify.py`'s pay gate reads a BASIS rather than a role label (#223).
   `_pay_basis` asks the salary's own markers first (`/day`, `per hour`,
   `per annum`, ...), so the posting's own words beat everything; only an
   UNMARKED salary consults `role_type`, and only when the note records the
   value as `observed` or `declared`. An advert naming TWO bases abstains, the
   same rule the JD observer follows. An advert naming none falls through to the
   annual branch, byte-for-byte the pre-#223 behaviour for a bare amount.

   `_BASES` maps each of `hour|day|week|annual` to its own markers, its own
   credibility floor and its own config key, and `_pay_reject` is a lookup in
   that table — so a basis is judged against its own floor or against none.
   That table is what makes hourly and weekly safe to parse. An earlier draft
   left them unrecognised because a two-valued signature routed them to `day`,
   moving the applicable credibility floor from 1000 down to 50 and opening the
   reject window exactly where realistic hourly and weekly figures sit; the harm
   was reusing the DAY floor, not parsing the basis. Silence was not free: an
   unparsed basis does not abstain, it falls to `perm_floor_gbp`, and `£2,000
   per week` — about £104k a year — was rejected as a sub-floor salary. All four
   floors default to 0 = no floor, so an unconfigured install abstains.
   Conversion constants were the other candidate and were declined: shipped
   hours-per-day and days-per-week numbers are an assumption about someone's
   working pattern, wrong for anyone on a four-day week, and a preference
   wearing the clothes of a parsing fact.

   The enrich pass writes an `observed` role_type back from the fetched JD,
   `require_status`-guarded like its two sibling writes, best-effort and
   unreported (`update_fields` cannot distinguish a refusal from a no-op, and
   "the note already says this" is the common case). It fills a blank and
   corrects the tool's own `assumed` guess. It does NOT overwrite a `declared`
   value: that disagreement is announced, in the run summary and the durable
   audit log, and the user's value stands. §2.5 originally put `observed` above
   `declared` outright; three review rounds measured the observer at 37% recall
   with a residual false-positive rate on adverts whose SUBJECT is contracting,
   and a lexical guess should not silently replace something a user typed.

   `reverdict.py` is a one-shot migration marker, not a store. Notes written
   before provenance existed read as `assumed`, so the gate stops consulting
   their `role_type` -- a BATCH re-verdict on the first run after upgrade, and
   `dismiss` is not re-selected, so a lead dismissed that way is never seen
   again. The first run that would apply it names the affected leads and writes
   nothing; re-invoking applies it. Unlike the two dedup stores, a missing or
   corrupt marker must fail LOUD rather than refuse: it means "show the notice
   again", which costs one skipped run.
3. **cv** (`sluice/cv/`): select verified source material, bundle it into
   a closed set, compose a tailored CV against that bundle (an LLM call
   over `core.backends`), gate it, render (by default `template`: fill the
   user's own Jinja2 template — or the packaged one — and write a PDF via
   WeasyPrint; `script` shells out to an external render script instead), and
   serve under an opaque, cache-busted filename.

   Before any of that, `Sluice.compose_cv` refuses ONCE for the whole run if the vault
   cannot compose at all (#242): no baseline CV at `baseline_rel` (missing, empty or
   unreadable), or no verified entries in a `cited_by_gate` corpus. It is a property of
   the INSTALL rather than of a lead, so it is not a per-lead `CvResult` -- it raises
   through `main`'s usage-error path (exit 2) before the renderer, the backend and the
   dossier fetch. That ordering is the point: the earlier per-lead behaviour spent a
   browser fetch for both halves and, for the corpus half, two backend calls before the
   gate rejected the result. `doctor` reports the same two facts and the two MUST agree,
   which is why `Vault.preflight`'s `baseline_exists` is `.strip()`-based rather than
   existence-only and why `core/doctor.py` grades an empty citable corpus SETUP with
   `blocks=("cv",)` -- `blocks` is the load-bearing half of that agreement, and the one
   the state rename in #243 deliberately left alone.

   The gate has two tiers
   (#167). The HARD tier -- `cv/validate.py`'s fabrication/citation checks,
   `cv/engine.py`'s own inline STRUCTURAL guards beside them (the exact
   `WORK EXPERIENCE`/`PROFILE` headers, and the header/contact-block
   anchors, #99), the renderer's own optional `precheck`, and
   `cv/slop.py`'s `check_hard` (an em dash or a literal `--`, unscoped over
   the whole document) -- BLOCKS: a lead with no attempt that ever cleared
   it is skipped rather than rendered ungated. The gate is handed its source
   set rather than recovering it: `cv/validate.py`'s second parameter is a
   `cv/bundle.py` `BundleSources`, built by `bundle_sources(bundle)` from
   `build_bundle`'s own structured entries, not by re-parsing the rendered
   bundle text (#174) -- so no line of user free text can mint or rebind a
   citable `[id]`. Since #165 the bundle has FOUR sections and TWO renderers:
   `render_bundle` emits the baseline, the verified entries and the negative
   constraints, and `render_composer_bundle` adds a SKILLS INVENTORY framing
   section plus one derived negative on top of it. Only the composer sees the
   second; the #60 advisory audit keeps calling `render_bundle`, because its
   prompt opens "SOURCE BUNDLE is the ONLY truth" and a claim resting on a
   skills line alone must stay `unsupported` and stay held for sign-off.
   Non-citability is structural rather than parsed: `bundle_sources` walks
   `bundle["entries"]` and never touches `bundle["skills"]`, so a skills figure
   is licensed in neither the per-entry allowlist nor the wider PROFILE pool.
   Two flags on `EvidenceKind` carry the distinction the single old one cannot:
   `read_by_composer` (the corpus reaches the prompt) and `cited_by_gate` (the
   gate may license its content), and `__post_init__` refuses the incoherent
   combination. That closed three live holes: a later body line shaped
   like an earlier real code used to rebind that entry's allowlist, so a
   fabricated figure passed while the entry's own genuine metric was
   reported invented; an `[XX9]`-shaped line anywhere in the baseline minted
   a fully citable entry of its own; and, at zero entries, the NEGATIVE
   CONSTRAINTS block fell through into the PROFILE pool so a do-not-say
   figure was profile-permitted. That closure has a price, deliberately
   accepted: `_entry_block` (`cv/bundle.py`) now feeds BOTH the rendered
   prompt and the gate's allowlist, so a change to how an entry is
   PRESENTED to the model is also a change to what the gate PERMITS -- the
   two can no longer drift apart, which is the fix, but they also can no
   longer be varied independently. It also re-admits two narrow PROFILE-pool
   widenings the old positional parse excluded as a side effect of its own
   bugs rather than by design: a `=== 2020 Highlights ===`-shaped line
   inside the BASELINE now permits its digits in PROFILE prose (the old
   parser's section-header check matched it first and `continue`d, so 2020
   never reached the baseline pool at all), and an id-shaped baseline
   line's own digit -- e.g. the `9` of a stray `[ZZ9]` token -- likewise
   (the old parser sliced the id token off before harvesting digits for the
   entry it minted, and by then `seen_id` was already true, so the digit
   never reached the baseline pool either way). Both are consequences of
   `bundle_sources` harvesting the baseline block by a single unscoped
   `\d+` sweep with no positional or shape parse at all, which is also
   exactly what removes the three holes above.

   Since #168 a fifth field, `Skills:`, on an Experience Library entry licenses
   skills RELATIONALLY: it names, per entry, which skills that entry evidences,
   so a CV bullet citing the entry may use those names without tripping the
   misattribution check below, and a digit inside one of them (`Widget3`) is
   not read as an invented metric for a bullet citing that same entry. Every
   token of a `Skills:` item must begin with a letter, or with a dot then a
   letter so `.NET` is expressible (`cv/bundle.py`'s `SKILL_TOKEN_RE`, checked
   PER TOKEN rather than per item -- an item-level check would accept
   `Result 92` because the item begins with `R`, and span removal would then
   blank the real figure `92` from every bullet citing the entry). A
   DIGIT-leading token stays refused whatever it names, which costs real values
   (`ISO 9001`, `Web 2.0`, `Section 508`, `3D modelling`, `5S`, `802.11ac`) and
   is stated as an over-refusal rather than disguised as a distinction the code
   draws: nothing separates those from the metric shorthand the rule exists to
   close. It is fail-loudly at `build_bundle` construction, this module's own
   house rule, rather than at gate time far from the note that caused it.
   That construction call sits inside `cv/engine.py`'s per-lead try, but the
   blast radius is the RUN, not the lead: `build_bundle` runs per lead over the
   SHARED verified corpus, so one malformed value raises for every lead --
   measured, three shortlisted leads all returned `cv run`'s `error` outcome
   from a single bad `Skills:` value. The per-lead try means the run completes
   rather than aborting, and the proposal and verification commands are
   untouched, since they do not import `cv/bundle.py` at all.
   `BundleSources`' per-entry allowlist is now a NamedTuple,
   `EntrySources(nums, skills)`, rather than a bare digit set, so the two
   fields travel together keyed by the same entry id and no second id-keyed
   structure can disagree about what an id licenses. `BundleSources` itself grew from two
   stored fields (`nums`, `baseline`) to three -- `entries` (`dict[str,
   EntrySources]`, replacing the old bare `nums` dict), `baseline` (unchanged),
   and `source_tokens` (new: one token SEQUENCE per source block -- each
   entry's `Skills:` tokens, each entry's body tokens, and the baseline's
   tokens -- kept unflattened so a two-word skill can never match an adjacency
   invented at a block seam). `ids` and `nums` are both DERIVED PROPERTIES, not
   stored fields: a stored second view could disagree with `entries` about what
   an id licenses, the exact redundancy #174 removed one level up, and every
   pre-#168 `validate()` caller keeps reading `sources.nums` unchanged.

   The framing/citable split states which of the three evidence kinds gets
   which `EvidenceKind` flag: `experience` carries both (`cited_by_gate=True,
   read_by_composer=True`); `skills` carries `read_by_composer` alone (framing
   only, licensing nothing -- see above); `stories` carries neither (captured
   and reviewed, consumed by nothing yet). `__post_init__` refuses
   `cited_by_gate=True` without `read_by_composer=True`, because the gate
   cannot license content the composer never emitted into the bundle in the
   first place.

   `cv/validate.py` gained two CONTAINMENT rows alongside the gate's existing
   citation and number checks (#168), and they differ from each other by
   design, in both what they scope against and how they compare. Row 1,
   MISATTRIBUTED SKILL, scans a WORK bullet's own prose CASE-SENSITIVELY
   (`_names_skill`) against the vocabulary of skills NOT licensed by that
   bullet's own cited entries -- the bundle's whole skill vocabulary minus the
   union the cited entries themselves license -- and ABSTAINS entirely for that
   bullet unless EVERY entry it cites declares a non-empty `Skills:`: measured
   otherwise, a bullet citing a partially-annotated entry and naming a skill
   drawn straight from that entry's own body text was flagged a hard violation.
   Case-sensitivity and the per-entry abstain are both deliberate under-fires,
   the direction a hard gate must err. Row 2, UNSOURCED SKILL, checks the
   SKILLS region's own emitted lines (`skills_by_line`, the third value
   `section_spans` now returns) CASE-INSENSITIVELY (`_in_source`) against
   `sources.source_tokens` -- did the model invent this line at all, regardless
   of which entry (if any) it might belong to -- and ALWAYS runs, never
   conditional on a non-empty vocabulary, because `section_spans` is pure over
   text and a SKILLS section emitted on an unannotated vault must still be
   checked by something. Row 1 answers "is this attributed to the right entry";
   row 2 answers "did you invent this at all", over a different corpus (the
   bundle's whole source TEXT, not one entry's licensed names) at a different
   granularity (a whole emitted line, not a scan through free prose). Both rows
   share one tokeniser and one subsequence primitive (`_tokens`/`_subseq`) so
   the vocabulary the gate BUILDS cannot drift from the one it SEARCHES with;
   only the case-folding and the corpus differ.

   Row 2's own SKILLS run (`section_spans`' `in_skills` extraction) ends at a
   heading the FORMAT CONTRACT defines -- `PROFILE`, `WORK EXPERIENCE`,
   `CERTIFICATES`, `EDUCATION`, each of which already has its own branch in that
   loop -- or at a non-blank non-bullet line reached while `in_work` is live,
   and at nothing else. Every other non-bullet line keeps the run alive: a group
   heading (`Languages`), and an off-contract section header (`PUBLICATIONS`,
   `PROJECTS`, `AWARDS`) in either capitalisation. The rule used to end the run
   at any ALL-CAPS line instead, which left a shouted group heading
   (`LANGUAGES`) ending it and its bullets checked by nothing at all under
   `cv.renderer: script` -- and decided two identical situations oppositely on
   capitalisation alone, since the Title-Case spelling of the same unmodelled
   section WAS swallowed and checked. Replacing the shoutiness heuristic with
   the contract's own closed set closes that hole and removes the asymmetry.
   ONE residual remains, in the OVER-checking direction and accepted on purpose:
   an off-contract section emitted AFTER a SKILLS run has its bullets
   containment-checked as skills, so a genuine entry there can be flagged
   `UNSOURCED SKILL`. "Shout the heading" is no longer an answer to that; the
   remedy is to emit the section BEFORE SKILLS or not at all, since
   `compose._RULES` asks for none of the three. Under the shipped `template`
   renderer that document is refused by `parse_cv` whatever the gate says, so
   the added exposure is `cv.renderer: script` alone. The direction is
   deliberate: over-checking costs a retry a human can answer, under-checking
   ships an ungated line, and for a containment gate that is the right way
   round. Pinned by four rows in `tests/test_cv_skills_containment.py`:
   `test_a_bullet_under_a_group_heading_is_still_row_2_checked`,
   `test_only_a_contract_heading_ends_the_run` (which derives the heading set
   from `tests/template_content.py`'s `composer_headings()` rather than typing
   it), `test_an_off_contract_section_after_skills_is_over_checked`, and
   `test_a_group_heading_while_work_is_live_still_ends_the_run`.

   The STYLE tier
   (`cv/slop.py`'s `check_phrases`, ~40 case-insensitive AI-tell stems)
   never blocks; it is also SCOPED, unlike the hard tier --
   `cv/validate.py`'s `section_spans` (the gate's own line split, extracted
   so nothing keeps a second copy) yields the PROFILE-prose and WORK-bullet
   lines -- two of the THREE regions the function now returns, since #168's
   Task 3 added a SKILLS region alongside them, deliberately left out here --
   since the only way to answer a phrase complaint about an employer,
   certificate or education line is to rename the thing it names. An OPT-IN
   third signal (`cv.voice_check`, off by default, `cv/voice.py`) rides the
   same retry once the hard tier is clean: a model judgment of the draft's
   VOICE, for an AI-tell clause a fixed phrase list cannot catch -- it fails
   open on a backend error, like the advisory audit below. It is scoped by
   the SAME two of `section_spans`' three regions, because the reason for
   that scoping is a property of the tier and not of the phrase list: the
   engine rejoins those lines into the text it hands `run_voice`, so the model
   never sees an employer, certificate, education, or SKILLS line it could
   complain about, and skips the call entirely when that text is blank. What
   the model gives up is document context (the contact block, section headers and the
   employer/date/role meta lines) -- acceptable because those lines are
   transcribed declared facts rather than composed prose, and the prompt
   already forbids judging content. EITHER a HARD finding OR a surviving
   STYLE/VOICE finding triggers
   exactly one retry with the findings fed back, and the loop RETAINS the
   last HARD-clean draft across it, so a retry that comes back hard-dirty
   (or simply fails) never bins a lead a style phrase alone would otherwise
   have cost -- a phrase may never cost a lead. At shipped defaults
   (`cv.style_hold` off, `cv.slop_allow` empty -- full enforcement of every
   stem) that retry is the one real cost change: a hard-clean draft still
   using one of the ~40 stems in prose costs a second compose call,
   mitigated by `compose.py`'s own prompt already instructing the model
   against the same list (rendered from `cv/slop.py`'s `_PHRASES` so the two
   cannot drift). Above the hard gate sits a softer, human-facing layer
   (#60): an advisory LLM audit (`audit.py`) flags claims the bundle does
   not support, and an `unsupported` flag still renders and serves the PDF
   (it passed the hard gate) but WITHHOLDS the send-ready `tailored_cv`
   pointer, so `apply` cannot select it. `cv.style_hold` (#167, off by
   default) gives a surviving STYLE or VOICE finding the SAME consequence,
   deliberately as a SEPARATE config key rather than riding
   `cv.require_signoff`: that flag's True default was chosen for
   FABRICATION, and riding it would mean an unconfigured install withholds
   `tailored_cv` on any of ~40 case-insensitive stems out of the box. The
   hold is recorded in two NEW frontmatter keys (`pending_cv`,
   `needs_signoff`) — the note's `status` stays `shortlist` (never-regress
   is untouched); the CV is simply invisible to apply without the pointer. A
   held lead is skipped on re-run so a non-deterministic re-audit cannot
   promote it by luck. (`needs-signoff` and `skipped-needs-signoff` are
   `CvResult` run-report labels, not `status`-key values.) `job-sluice cv
   signoff --lead X` promotes the held CV after the candidate reviews the
   flagged claims; `--discard` rejects it and frees a fresh compose. The
   default is on for fabrication (`cv.require_signoff`); neither signoff
   flag touches the pure hard gate.
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
   proposes like any weaker match. Proof means the SENDER host matches one of
   the lead's known hosts -- `applied_url` (the URL actually submitted to,
   written by `apply/record.py` at apply time) then `url` (the ingest source),
   checked independently per host so a clean `applied_url` can never lend its
   multi-tenant-free status to a different, multi-tenant `url` on the same
   lead (#136; never a body link, which the sender controls); the delivering
   server AUTHENTICATED that domain (an `Authentication-Results` dkim/dmarc/
   spf PASS whose domain aligns with the sender, since a `From` header is
   free text anyone can forge); and neither side is multi-tenant -- an ATS
   relay (`ats_relay_domains`) or one of the job boards sluice scrapes
   (`job_board_domains`), since a board-sourced lead's `url` identifies the
   board, not the employer. Failing or missing authentication degrades to a
   proposal rather than dropping the signal. A weaker corroborated or
   cross-lead-ambiguous match only proposes.

   The matcher searches `receipt_by_slug` -- shortlist AND in-flight leads
   together, indexed by `index_by_slug` over their concatenation rather than a
   dict merge, so a slug claimed by one note in each set is dropped as a twin
   rather than silently resolved to whichever came last (#136). Searching
   in-flight leads too matters because a lead reaches `applied` at apply time
   and its receipt normally arrives AFTER: in steady state the shortlist set
   alone is nearly always empty, so before #136 the deterministic matcher
   could almost never find the lead a receipt actually concerned. A domain
   match for a lead already past `shortlist` can never WRITE (`can_apply`
   refuses everything but `shortlist`), so `reconcile` stamps the evidence
   (sender, subject, date, tier) onto the lead's own note -- via the same
   idempotent-by-tag helper the auto-advance path uses -- and reports it quiet
   rather than either advancing or proposing. That stamp is what still catches
   a model mislabelling a genuine rejection as a "receipt": the real subject
   line ends up on the note even though nothing else changes.

   Receipt proposals have two producers: reconcile's own corroborated/
   below-floor path, and -- when deterministic matching finds nothing at all
   (tier `none`) while the LLM named a lead that is already in-flight -- an
   engine-level fallback that records a dead-letter row and never writes. That
   fallback's hint offers `job-sluice track dismiss --id <mid>` rather than a
   bare "review manually", with one deliberate exception: a slug claimed by
   two notes (a same-set OR cross-set twin) gets a hint naming both notes to
   rename or merge, with no `dismiss` lever, because that row has a real
   remedy and must keep re-surfacing until a human applies it. Un-acted-on
   work is durably surfaced via `track/deadletter.py` -- a sqlite dead-letter
   re-emitted every run until `track confirm`/`track dismiss` clears it, or a
   lead's own proposals are cleared automatically when it auto-advances -- so
   it never vanishes after a single report. The store holds three kinds of
   row, not just status proposals: a `failure` row for a message that could
   not be processed at all, and a `calendar` row for a calendar action that
   could not be completed or verified. An auto-advance clears a lead's STATUS
   proposals only -- advancing to `rejected` does not remove a stale calendar
   entry, nor make a failed message succeed. A hint offering `track confirm`
   is filtered through `_confirmable`, the same `can_transition` predicate
   `confirm()` itself calls, so a hint and the command it offers can never
   disagree -- an ambiguous receipt match that includes an in-flight candidate
   never mints a `--to applied` command for it, which `can_apply` would refuse
   forever. `RunReport.receipts_recorded` counts the quiet-skip stamps in the
   run digest, alongside the log-stream trace, since the digest is what
   survives under cron.

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
  as a `catalogue(default_vault=...)` PARAMETER so this module never imports
  the concrete store. Backend and renderer choices are derived from the live
  registries, never hand-listed.
- **`emit.py`** (pure): hand-rolled YAML scalars. `safe_dump` would destroy the
  comments that are most of the template's value and a round-tripping loader is
  barred by the standard-library-only rule, so strings are always double-quoted —
  the one form with a total escape grammar.
- **`plan.py`** (pure, with one deliberate exception -- see below): `build_plan(answers, ...) ->
  InitPlan`, producing FOUR artefact texts (`config_text`, `profile_text`, `candidate_text`,
  `view_text`)
  plus the notes the report prints. The config is RENDERED FROM THE
  CATALOGUE, which makes "every key the wizard can write appears in the file it
  writes" true by construction. An unanswered key is emitted COMMENTED; the block
  HEADER stays ACTIVE, because a commented header made the file's own
  `# <- uncomment and set YOUR OWN` marker produce an unparseable config for every
  nested key (16 of 19), and all four loaders have always read a null block as
  empty. `candidate_text` (#133/#107) is the one artefact whose own construction can fail:
  `_render_candidate` writes every one of the 36 `CandidateProfile` fields through
  `emit.scalar()` and then re-reads the WHOLE note back through `core/vault.py`'s
  `parse_frontmatter` -- the real reader `Vault.read_candidate_profile` also uses -- comparing
  per-field against what was asked for. A value that does not survive that round trip (an
  interior quote, a control character) raises `FrontmatterRoundTripError` rather than ever
  returning text that would silently corrupt on the way back in. That is the one place this
  module imports the concrete store (`from sluice.core.vault import parse_frontmatter`,
  module scope) rather than staying import-free like `questions.py` above: the check is only
  meaningful against the SAME reader production uses, and rolling a second frontmatter parser
  here to avoid the import would defeat the very thing the check exists to prove.
- **`ask.py`** (impure): the only half that touches a terminal. `TtyAsker` prompts
  and re-asks on a bad answer; `NoInputAsker` answers only from flags and REFUSES
  rather than reading stdin, because a wizard blocking on a pipe is a hung CI job
  with no diagnosis. Both satisfy one small interface, so `--no-input` is the same
  wizard with the prompting removed rather than a second code path. `collect_candidate`
  is the third interview (#133/#107), gated differently from the other two: `collect_profile`
  and the board walk are gated on whether their artefact EXISTS yet; `collect_candidate` is
  gated on whether the note DECLARES anything (`has_any_declared`), because an unconditional
  existence gate would close the moment `cmd_init` writes an all-blank scaffold -- see the
  conditional-write paragraph below.

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
anything, writes the config with an exclusive `open(dest, "x")`, and writes the Judging
Profile AND the Candidate Profile through the STORE SEAM via
`write_document(..., only_if_absent=True)`, rolling nothing back on a partial failure. It
REFUSES when `--vault` and `$VAULT_DIR` disagree, because `stores/vault.py:_make` is
env-first and a precedence rule would write to one path while the report named the other.

Before any write, `cmd_init` loops on `build_plan`, catching `FrontmatterRoundTripError`: a
hostile candidate answer re-asks the five candidate questions (or, with no terminal to retry
on, blanks the answers and loops once more, since a blank value always round-trips) rather
than losing the whole interview -- every preference question, the board walk, the five
Judging Profile prompts -- to one bad answer in the last of three independent interviews.
(Three interviews, four artefacts: the fourth, `view_text`, is the Obsidian Bases view
and takes no answers at all, so no interview can corrupt it and it is written
unconditionally under the same never-overwrite rule as the rest.)

Of the four artefacts, the Candidate Profile is the only one written CONDITIONALLY, on
`has_any_declared(parse_candidate_profile(plan.candidate_text))` -- the rendered ARTEFACT,
not the raw answer dict. That conditionality is load-bearing, not cosmetic: the Judging
Profile always emits `DEFAULT_CRITERIA`'s own headings and prose, so its existence probe
(`bool(store.read_criteria())`) is True on the very next run whether or not the user answered
anything, and its write gate closes for free. An all-blank Candidate Profile has no such
fallback content, so writing one unconditionally would leave `has_any_declared` False
forever even with the note on disk: the write refuses on every later run (never-clobber), the
interview re-asks on every later run, the re-asked answers park in the
`.init-scaffold.md` rescue, and the run after that reports `failed` because the scaffold slot
is occupied too -- a permanent deadlock. Gating on the rendered text rather than the answer
dict closes a narrower version of the same trap: an earlier shape gated on
`any(candidate_answers.values())`, which agreed with the artefact-based probe only by the
accident that every question `collect_candidate` asks happens to have a mapped
`CandidateProfile` field; a future question added with no matching entry would satisfy the
answer-dict gate while `_render_candidate` wrote an all-blank note regardless, reaching the
same deadlock on the very first run. See `cli.py`'s write block for the full account.

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
extended #131, extended again #164 and #175) is the first one: a Model Context
Protocol server exposing the read-only tools (`list_leads`, `get_lead`, `doctor`,
`health`, `list_evidence`) always, and the write-capable tools (`dismiss_lead`,
`apply_record`, `cv_run`, `cv_signoff`, `create_lead`, `propose_evidence`) under
`--write`. No COUNT of those is stated here on purpose: "five" stood in this paragraph,
in `mcpserver.py`'s own module docstring, in `build_server`, in `cli.py`'s `--write`
help, in `docs/USAGE.md` and in both MCP test files, and every one went stale the moment
#175 registered a sixth. No count of THOSE either — three reviewers tallied the stale
statements and returned three different totals, which is the argument for enumerating
rather than counting. `tests/functional/test_mcp_contract.py`'s exact-set `==`
assertions pin the roster at both privilege levels; prose cannot.

`list_evidence` has a PROPOSE counterpart since #175 and still has no VERIFY
counterpart at any privilege level -- that, not "read-only", is the standing property.
Proposing lands an entry under `_inbox/`, which `read_evidence` cannot see, so it is
inert until a human promotes it; VERIFYING is what makes it citable, and a second
promotion path is a new trust root rather than a convenience (#164's central
decision, unchanged).

What deferred the propose tool was the gate, not the store. An evidence body reaches
`cv/validate.py`'s fabrication-gate bundle verbatim, and while that gate recovered its
ids by parsing the rendered bundle, `nums[cur] = set(...)` was an ASSIGNMENT rather
than a union -- so an LLM-authored body shaped like a citation code REBOUND another
entry's permitted numbers, and a write tool would have handed that bypass to whatever
calls this MCP server. #174 deleted that parse: the gate is handed a structural
`BundleSources`, and no body line can mint or rebind an id.

What survives is smaller. `bundle_sources` harvests every digit in an entry's own
block, so a citation-shaped token in a body still contributes ITS digits to that entry
-- the residual #174's design accepts, and the one
`core/vault.py`'s `_refuse_citation_shaped_body` refuses outright on both evidence
write paths. That guard, not the gate, is what `propose_evidence` relies on. Every write tool is a thin translation layer over
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
created -- a visible duplicate a human can merge again.

Comparing that recorded name up to CASE is #205's second half, and it was a live breach rather
than a tidy-up: measured on the code before it, merging a lead away and re-scraping it with the
company spelled `EXAMPLE CO` instead of `Example Co` returned `created`. The exact-casing control
suppressed correctly, so the guard was working and the re-scrape simply walked past it — undoing
a human's merge, and where the surviving twin was already `applied`, meaning a second application
under the user's name. Folding can only SUPPRESS more, never resurrect more, so it moves in the
safe direction by construction; and it does not widen what enters `seen.db`, because that arm
stays gated on `url_proven` — a matching non-empty url, which no amount of name folding can
manufacture. A fold-widened match that is not url-proven lands on `merged_away_unproven`, writes
nothing, records nothing, and re-reports every run until a human acts. The conformance suite exercises
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
a #60 sign-off hold -- but is one of the TWO `leads` passes that write
unconditionally on every call rather than reporting by default (`leads add`,
below, is the other): the verdict it writes is one the USER typed
(`--lead`/`--reason`), not one this tool computed, matching the pipeline
commands' contract rather than `dedupe`/`expire`/`reconcile`'s. Resolves
by EXACT slug equality, never substring, and refuses (writes nothing) when the
slug names two or more notes rather than picking one.

**`job-sluice leads add --url URL --company NAME --role TITLE`** (#241) is the
only route into the lead store needing neither a browser nor an MCP client.
`ingest run` needs a Camofox server; `mcpserver.create_lead` drives this very
facade without one, but only under `job-sluice mcp serve --write` with a
configured client. So before this command a fresh install could not reach
`triage` or `cv` from the CLI at all, and both README's quickstart and
`docs/AI-SETUP.md` had to tell their reader to hand-author a note file --
bypassing dedup, never-clobber and the #81 archive probe in one step.

It is a THIN front-end over `Sluice.create_lead`, the same facade
`mcpserver.create_lead` drives, and deliberately not a second writer: that facade
already validates every field, calls `store.upsert` directly (so `seen.db` is
untouched -- a hand-added lead must not suppress the later genuine scrape of the
same posting, and that store has no removal path), and stamps
`role_type_source=declared` because a basis the user typed is the provenance the
relevance gate may act on. A sibling write function would be a new CodeQL sink
with all of that to re-argue, which is the same reasoning that gave
`update_fields` a parameter rather than a twin.

The command's whole correctness is reporting what `upsert` actually returned.
All six outcomes are reported by their own name -- a bare "created" would be a
lie on five of them -- and the three that write nothing (`refused`,
`merged_away`, `merged_away_unproven`) exit non-zero, because a silent no-op
exiting 0 is the failure mode every `leads` pass is shaped against. The two
`merged_away` arms stay distinct rather than collapsing into one message: they
differ in whether the archive match was url-PROVEN, which is the same distinction
that decides whether the ingest sink may record the lead, and each names the one
recovery action (move the note out of `_merged/`) so a #81 refusal is not a
permanent no with nothing said about the way out.

Two decisions about the flag set are load-bearing.

There is no `--source`, because `source` is not a free-text provenance note: it
is a key into the ingest source registry, and `triage/resolve.py` reads it as one
in two places. The live one is `_is_board_name`, which discards a resolved
company equal to the source id as the board's own name -- measured,
`_is_board_name("Reed", {"source": "reed"})` is True and the same call under
`manual` is False, so a `--source reed` would throw away an employer genuinely
called Reed. The other is `company_from_url`: resolve.py looks the id up and
calls that hook when the source defines one. That arm is a FORWARD hazard rather
than a live one, and the distinction matters -- an earlier version of this
paragraph claimed `--source reed` would aim reed's url extractor at a foreign
url, which cannot happen (reed defines no such hook, and `wellfound`, the only
source that does, anchors its regex on its own host and abstains). The hook is
optional, so a future source need not. `manual` matches no registered source, so
both paths abstain.

`--role-type` is an argparse `choices=` rather than free text validated
downstream. `normalise_role_type` warns and returns "" for an unrecognised value,
and the warning IS visible (the default level is INFO), so the hazard is not
silence: it is that the command would go on to succeed, print `created`, and
leave a note whose pay basis is blank while `triage` judges the salary against
the wrong floor. Refusing up front writes nothing. The accepted set is DERIVED
from `roletype._ALIASES` (exported as `ACCEPTED_ROLE_TYPES`) rather than pinned
to the two canonical values, because 11 of the 13 spellings the facade honours
are aliases -- `perm`, `freelance`, `interim`, `fte` among them -- and a
hand-pinned pair made this CLI reject input the MCP tool over the same facade
accepts and maps correctly.

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

**That name is matched up to CASE** (#205). A board renders one employer several ways and the
name is built from the company string verbatim, so a byte-for-byte match seated a separate note
per spelling, each with its own status — one spelling holding a live `shortlist` while its twin
held a `dismiss`, so dismissing the role under one did not stop it returning as `new` under the
other. It also wedged replication silently: a case-insensitive filesystem cannot hold the pair,
and Syncthing reports the folder `state=idle` while delivering neither note. `_fold_note_name` is
the one fold, and every path that resolves a lead by NAME goes through it — `_locate`,
`_archived_match`, `read_leads`' report, and `reconcile_names`. Stated as that obligation rather
than a roster, because the roster shipped as three and was stale inside the same branch. They
cannot be allowed to drift: a `_locate` that folds against an `_archived_match` that does not is
measurably a **resurrection**, and a `reconcile_names` that does not measurably **mints** the pair
— both were live here before review. It is CASE only: Unicode normalization is a real and separate axis (a macOS
filesystem may return NFD for a name written NFC), and every widening past case claims two
differently spelled names are one job.

`_locate` probes the exact name FIRST and folds only on a miss, which is what keeps the cost
where it was — the exact probe does not move as the store grows, while the folded listing is about
three orders of magnitude dearer and scales with the note count, so the fold is paid on the
create/miss arm that already pays `_archived_match`'s listdir. One consequence follows and is REPORTED rather than closed: against a pair a pre-#205
store already holds, a scrape whose casing matches either note returns that one and updates
silently; only a third casing, matching neither, reaches the ambiguous refusal. So the standing
signal on such pairs is `read_leads`' own warning, which names `leads dedupe` — `cluster_duplicates`
normalizes company and role through `_norm_tokens`, which casefolds, so such a pair already
clusters. Only the signal was missing.

What `--merge` then does is conditional, and the warning says so rather than promising a
resolution the pass refuses: `resolve_merge_status` returns `conflict` for two distinct non-`new`
triage states, so the pair #205 actually reports — one twin `shortlist`, the other `dismiss` —
clusters and does **not** merge (measured), while twins that agree merge normally. That refusal is
correct, since picking the surviving status is exactly the human judgement a conflict demands.

The report itself sweeps every lead note WALKED, not the list `read_leads` returns — the one place
it differs from the sibling duplicate-slug sweep beside it, which is deliberately per-returned-list.
A case pair is a property of the store, and the shape #205 reports puts one twin at `shortlist` and
the other at `dismiss`, so every status-filtered read surfaces exactly one of them; grouping over
the returned list made `read_leads({"shortlist"})` say nothing about the very pair the report
exists for.

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
`receipt_by_slug` (named `shortlist_by_slug` before #136 widened it to also index
in-flight leads), and `core/app.py`'s `by_slug` in `expire`. A fourth,
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

`receipt_by_slug` is the set `match_receipt` searches, so the dropped twin is
invisible to the receipt matcher and a receipt whose evidence fits it is weighed
against the survivor instead — and where the survivor's url HOST satisfies
`_hosts_match` against the sender with neither side multi-tenant, that survivor can
be auto-advanced to `applied`, an application recorded against the wrong note. Read
that on the host, not the url: `match_receipt` never compares urls, and
`_hosts_match` accepts a subdomain relation in either direction, so two twins on one
employer's site whose urls differ only by subdomain or path BOTH satisfy it.
Identical urls are sufficient, never necessary. Since #136 this is no longer confined
to two notes both filed as shortlist: `receipt_by_slug` is built by running
`index_by_slug` over shortlist AND in-flight leads TOGETHER (one call over their
concatenation, never a dict merge of two separately-indexed sets — a merge would
silently keep whichever twin came last rather than dropping the pair), so a slug
claimed by one shortlist note and one in-flight note is caught by the exact same
mechanism, which neither index alone could see.

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
one, invisible to `index_by_slug`, so this pass instead re-checks `_is_note_file` -- never
`os.path.exists`, which swallows every `OSError` and would read an unstatable old path as
"gone" instead of reporting the genuine resurrection that call exists to catch -- on each
renamed note's pre-sweep path); and `skipped` (a symlinked note -- left alone as a structure
the user deliberately built, not a detachment hazard, since source dir == dest dir for a
rename -- an `OSError` from the move itself, or an `OSError` the resurrection probe raised on
an already-renamed note, isolated per-note so it cannot escape the sweep).

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
and absent only for `FileNotFoundError`/`NotADirectoryError`. `_is_dir` has further
callers outside that tree, bound by the same rule for their own reasons — the evidence
read and `preflight`, both below.

Each `_is_dir` caller had to be converted separately, and
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

The `_is_dir` callers outside the lead tree take the rule for their own reasons.
`_evidence_entries` — the single probe behind both `read_evidence` and
`read_pending_evidence` — probes an evidence directory (the *Experience
Library*, for the read the fabrication gate depends on), which no scan walks. A write path IS keyed on it now
(#164): `propose_evidence` lands an unverified proposal in its `_inbox/` subdirectory,
and `verify_evidence` promotes one into the directory itself -- both through the same
`_evidence_dir` RESOLVER this read uses, which is where the symlink refusal lives, so
every read and every write is bound by it alike. That sentence used to claim a shared
path *expression* was enough to keep "a guard added to one side" from going missing on
the other, and a probe falsified it: the refusal actually sat in `propose_evidence`'s
own body, so a symlinked `_inbox/` refused the write, listed the foreign directory's
entries through `read_pending_evidence` anyway, and let `verify_evidence` promote one
and then `os.unlink` the source — deleting a file outside the vault (#164 review, H1).
Sharing the path expression shares only the path; a guard is shared only by living in
the resolver, which is now where it lives.

**What that refusal covers, stated so the boundary is not guessed at.**
`_evidence_dir` walks EVERY path component below the vault directory — each component
of the kind's relpath, plus `_inbox` when the inbox is being resolved — and raises on
the first `os.path.islink`, outermost first. It is a walk rather than a list of named
levels because the list was wrong twice: a check on `_inbox` alone missed a symlinked
KIND directory (`_inbox` beneath it is an ordinary subdirectory of a foreign tree, so
`islink` is False), and a check naming both missed a symlinked ANCESTOR — measured on
`vault/Job Applications ->` outside, the first component of every kind's relpath: the
pending listing showed `['alpha']`, `verify_evidence` returned True, and its `os.unlink`
deleted a file outside the vault. Outermost first, which only bites when more than one
component is a link (a symlinked ancestor with a symlinked `_inbox` nested inside the
foreign tree): the message carries one instruction, "move the real folder into the
vault", and moving the inner folder changes nothing while the ancestor still points
away. With a single link the two directions agree — measured, reversing the walk against
a one-link fixture left the whole suite green, which is why the test that pins the order
nests two. The vault directory ITSELF is not probed: it is the path the user named
(`--vault`, a config key, an env var), so a symlink there is the boundary rather than an
escape from it.
`_evidence_entry_path` is the other half of the same class, at the ENTRY FILE, and the
harm is the mirror image — injection rather than deletion. Measured: an `_inbox/x.md`
symlinked to a file outside the vault was read through, promoted into the citable
directory carrying the foreign body, and the `os.unlink` removed only the LINK, so the
foreign file survived. It binds the citable listing too, not just the inbox, since a
symlinked entry sitting in the kind directory feeds `cv/bundle.py` content from outside
the vault with no promotion involved at all. Both refuse rather than resolve, for the
`islink`-never-`realpath` reason `_write_folder` already states.
Both are probes, not locks: a symlink swapped in AFTER the walk and before
`verify_evidence`'s `os.unlink` still escapes (it must carry byte-identical content to
survive the compare-and-set first). That is the same accepted residual class as
`_cas_write`'s own compare → replace micro-window — no portable stdlib call resolves a
path and operates on it atomically — and closing the ROUTINE case is the whole claim.

Back to `_is_dir`: the harm a quiet `[]` does here is still not a re-created LEAD --
those two writers manage evidence entries, not lead notes -- so `_evidence_entries` took
that rule on its own merits: these entries are the ONLY citable evidence the hard fabrication gate
recognises, so an empty read leaves a bundle with no ids and every WORK bullet
violates it — measured,
`BAD CITATION` for a bullet that cites and `UNCITED BULLET` for one that does not.
The CV is therefore never rendered — it fails CLOSED — and what it costs is that a
permissions problem reaches the user as `skipped-gate`, a fabrication verdict against
their composer, after a dossier fetch and a full compose have been paid for. The silent
case needs the VAULT ROOT to be unstatable, since with the library itself at mode 000
`os.listdir` already raises: `os.stat(base)` is then the call that fails, and
`os.path.isdir` turned that into `return []`.

`Vault.preflight` probes the vault root the same way (that probe predates #164 —
this paragraph tagged it with that issue and was wrong), and the reason is the
diagnosis rather than the data: `job-sluice doctor` reports FACTS, so a `vault_exists`
that swallowed a `PermissionError` would hand `classify_store` a False and print
`vault directory does not exist` — a DEAD verdict naming the wrong cause, on the one
command a user runs *because* something is already wrong. Propagating instead lands it
in `Sluice.doctor`'s own broad handler around the hook, as a DEAD `store`/`preflight`
row carrying the real error text — so the permissions problem is what the user reads.

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
  Its evidence surface is `read_evidence`, `read_pending_evidence`,
  `read_pending_evidence_text`, `propose_evidence` and `verify_evidence` — five
  members, all added by #164. There used to be a sixth, `read_experience_entries`,
  a second required spelling of `read_evidence("experience")` kept for the one
  caller (`cv/engine.py`) that had not yet been rewritten to read per kind. #165
  rewrote that caller and DELETED the member, as its own docstring scheduled: a
  Protocol member is a required member, so leaving it would make every future store
  implement a second name for a call it already implements, for a caller that no
  longer exists. Its conformance row was renamed onto `read_evidence` rather than
  dropped, so the seam is still bound by a contract test. A filesystem
  `path` is no longer part of what the two readers PROMISE: that key was
  required once, purely so `core/app.py` could `open()` it for the bytes a
  human reviews, which made the store-agnostic facade reach through the seam
  at a filesystem and forced a `path` on stores that have none. The `vault`
  store still carries one in its own dicts and `core/protocols.py` says so
  outright — a store MAY carry extra keys; what changed is that no
  contract-bound caller may read one, which is the part a second store
  depends on.
  `read_pending_evidence_text` is the contract member for those bytes, and it
  must be a FRESH read, because it is what `verify_evidence`'s compare-and-set
  is handed. Which of a kind's own frontmatter fields fills each of the four
  TEXT floor keys (`company`/`category`/`best_for`/`metrics`) is
  `FLOOR_FIELD_SOURCES` merged with that kind's `floor_map`, not an identity
  mapping a store invents: `skills` names its keyword axis `Domain`, and
  without the override `cv/bundle.py`'s `rank()` scored a `platform` skill
  zero against the keyword `platform`.
  This seam has a second, OPTIONAL member too: `preflight() -> dict`, the same
  shape as the renderer seam's `precheck` below (undeclared on the `Protocol`
  for the identical reason -- an optional member must stay optional to
  declare). `job-sluice doctor` reaches it via `getattr(store, "preflight", None)`;
  an implementation that omits it reports nothing for that component rather
  than being treated as broken. `Vault.preflight` returns FACTS only (does the
  vault directory exist, is the baseline CV readable, is a Judging Profile
  present, a `<kind>_total`/`<kind>_verified`/`<kind>_pending` triple for each
  of the three evidence corpora (#164: `experience` -- the pre-#164
  `experience_total`/`experience_verified` names kept as-is since `doctor`
  already consumes them by name -- `skills` and `stories`, iterated off
  `EVIDENCE_KINDS` rather than hand-listed so a fourth kind needs no edit
  here either), and -- #133/#107 -- is a candidate name declared and is a
  contact block declared, read via `full_name(profile)`/`contact_block(profile)`
  off `read_candidate_profile()`) -- never verdicts, which stay in
  `core/doctor.py` alongside the backend
  classification rules. That per-kind loop is ISOLATED: a kind whose
  directories cannot be read reports `<kind>_error` (the OSError's text)
  INSTEAD of its count triple, never a zero, and `classify_store` gives it its
  own DEAD row. Without that isolation one symlinked evidence directory
  unwound out of `preflight` entirely and `Sluice.doctor`'s catch-all printed a
  single `store | preflight | dead` row — measured, four store rows including
  `Candidate Profile | dead | blocks: cv` became one, on the command a user
  runs *because* something is already wrong. It is read-only by contract: stats paths and reuses
  this store's own read methods, never opens anything that does not already
  exist, so it cannot disarm the #81 relocation notice above.
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
  comparing the contact lines' actual content against `contact_block(profile)`
  -- #133/#107: the candidate's identity now comes from the vault's Candidate
  Profile note, not a `cv.name`/`cv.contact` config key), because that shape is
  what `cv/compose.py`'s prompt requested of every renderer alike, not a layout
  requirement any one renderer owns.
- **fetcher**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server). The dossier
  fetch closure built from it (`Sluice.dossier_cache`) POLLS
  `document.body.innerText` for the JD -- `waitUntil='domcontentloaded'` fires
  before a client-rendered posting has painted, so a single read returned an
  empty body on every lead for some ATS vendors (#228). It re-reads until two
  consecutive reads agree or `dossier_settle_ms` is spent, and returns the
  LONGEST read, because a settled page can then be overlaid by a cookie banner
  and the last read is the banner. It also captures `document.title` and EVERY
  `script[type="application/ld+json"]` tag, parsed in the page and returned as
  one JSON array (a board's own JobPosting schema is routinely not the first
  such tag), in the same already-open tab.

  The JSON-LD capture is no longer resolution-only: when its `JobPosting`
  description yields more text than the settled body, it becomes the JD (#228),
  which is what stops a page settling into navigation chrome from being cached
  as a job description. `core/dossier.py` owns that extraction and the walk over
  it, shared with triage's tier-2 resolution so one blob cannot get two answers.

  The JD read is a hard refusal on an unreadable body. The metadata captures are
  best-effort about their RESULT -- a missing, malformed or raising probe
  degrades to `""` -- but not about their LOCATION: the landed-url guard is
  re-applied before EVERY read, including the metadata ones, because settling
  turned a one-`evaluate` check-to-read window into a multi-second one and a
  client-rendered page is the kind most likely to navigate late. A refusal there
  propagates rather than degrading.
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.
  A source may optionally implement `company_from_url(url) -> str | None`
  (#109), the same optional-member shape as `Store.preflight`/
  `Renderer.precheck` above -- `Sluice.triage()` threads `sources.get` into
  `triage.engine.run` as `get_source`, the same lazy inside-the-method import
  `ingest()` already uses; `triage/` itself never imports `sluice.ingest`
  directly. A `BrowserListSource` subclass may also override `parse` itself for
  row-level repair, provided it delegates to `super().parse(...)` so
  `_row_to_lead` and the base class's title-non-empty filter still run:
  `naukrigulf` overrides it to recover a company mashed into the title via the
  listing URL's own seam (#151), `wellfound` to drop company-profile-card rows
  its extractor selector lets through (#151). Row filtering also has a
  DECLARATIVE form that needs no override: `posting_paths` (#153), a tuple of
  URL path prefixes a posting's link must start with, honoured by both base
  classes' `parse`. It is a plugin declaration beside `extractor_js`/`wait`,
  NOT a user config key -- `sources.<id>` accepts only `{enabled, tuning,
  searches}`. Empty is the shipped default and ABSTAINS, so the sources that
  declare nothing are byte-identical to before it existed; a misdeclaration
  (a bare string, or a prefix without a leading `/`) raises at construction
  via `validate_posting_paths`, because both directions are otherwise silent
  and they fail OPPOSITE ways -- one admits everything, the other rejects
  everything. Prefer it to a `parse` override for a pure destination check:
  an override that forgets to delegate to `super().parse(...)` loses the
  guard silently.

  Two further plugin declarations sit beside it, neither a user config key.
  `unpublished_fields` names completeness signals the BOARD does not publish
  (weworkremotely and eighty_k both hardcode an empty company), so
  `ingest list-sources --health` stops printing a permanent `UNGUARDED(...)`
  for a rate that can never climb; it is report-only and cannot suppress a
  drift reason. `reprobed` (#207 ask 4) is the ISO date on which a source's
  RETIREMENT was last checked against the live world -- "a retirement is a
  claim about the outside world and it goes stale", and the rule for
  recording that belongs in the source contract rather than in one test.
  Both default to the abstaining empty value, and a malformed `reprobed`
  raises at construction via `validate_reprobed`, on the same reasoning as
  `validate_posting_paths`: a recorded check date of `2026-99-99` reads as
  evidence to a human and parses as nothing.

  `reprobed` is a FIELD rather than a date mined out of the module docstring,
  and that was arrived at by measurement rather than taste. The docstring
  version had to decide from PROSE whether a line asserted that a check had
  happened, and each tightening acquired a fresh hole: a tuple comparison
  ranked the impossible `2026-99-99` above the floor; requiring a marker word
  admitted `unverified`, which contains `verified`; word-bounding the markers
  still admitted `not verified`, `never confirmed`, `no longer verified` and
  `yet to be re-probed`. That set is unbounded because it is a question about
  natural language. A declared date cannot be negated. The docstring still
  carries the REASON, which is what a human actually reads and what no field
  replaces; the field carries only the WHEN. `tests/test_drifted_boards.py`
  holds the policy half -- that a DISABLED source must carry one, and that it
  must fall between the re-probe floor and today.

  `searches_spec` is also validated at construction, beside `posting_paths` and
  `reprobed` (#212 round 2), and it is different in kind from both: it
  is simultaneously a plugin declaration AND the field a user's own
  `sources.<id>.searches` config key replaces wholesale (`SourceConfig.searches`,
  `core/config.py`). Because a config key and a plugin declaration must accept the
  identical shape, its grammar cannot live purely beside `validate_posting_paths`/
  `validate_reprobed` here -- it lives in `core/config.py` as `validate_search_entry`,
  called from THREE rungs: `load_config` (a user's own config, caught at load time),
  `_mk_search` here (defence in depth for anything building a `Search` without going
  through `load_config`), and `BrowserListSource.__post_init__` (this class's own
  construction-time check, so a malformed BUILT-IN example is caught by the registry's
  per-plugin isolation at import time rather than at first use). See
  `validate_search_entry`'s own docstring for the full three-rung picture and why the
  function lives in `core/` rather than here.

  A `Search` also records whether it came from `sources.<id>.searches` or from the
  source's shipped example (`Search.configured`, #212). `searches_for` is the only thing
  that ever sets it TRUE -- it is the one function that chooses between the two; `_mk_search`
  is the actual writer, defaulting to `False`, which is what a shipped example IS, so a
  producer that never thinks about provenance is treated as the tool's guess rather than
  the user's assertion. Two
  read-only consumers surface it: the run report's `example_searches=N` and `ingest
  list-sources`'s `EXAMPLE-SEARCH(n/m)` (shown on the plain listing too, not only under
  `--health`), both sparse, so a fully configured install sees neither. The flag exists
  because a plain `list[Search]` could
  not distinguish the two, which is what stopped #223 telling a `job_type` the user
  asserted from one a source's `extra` guessed. Unlike the declarations above it
  is neither a plugin declaration nor a user config key -- it is DERIVED, per search,
  at the moment `searches_for` picks a side.

`job-sluice doctor` is a read-only preflight over the whole pipeline, not only the backend
seam: it enumerates every configured backend (primary and fallback, per sub-app) and
classifies each as `ok`/`degraded`/`dead`/`setup`, then does the same for a second table of
component checks -- the renderer (does `cv.renderer` actually construct, catching a
missing `render` extra or WeasyPrint's native libraries before the dossier fetch and
LLM spend rather than after), the store's on-disk artefacts (the vault directory,
the baseline CV -- present AND non-empty, matching the refusal below rather than mere
existence -- the Judging Profile, a verified/pending row for each of the three evidence
corpora (#164: Experience Library, Skills Inventory, STAR Stories; NOTICE, except that a
CITABLE corpus with nothing verified is SETUP and blocks `cv`, because #242 makes `cv run`
refuse exactly that vault -- a NOTICE there would call the install fine about the thing
that stops the next command),
and -- #133/#107 -- the Candidate Profile note's own declared name/contact, checked
here rather than as a separate identity-fields row, via the Store seam's OPTIONAL
`preflight()` hook), track's Google adapter, the Camofox profile an ingest run will
drive, the current posture of every list-typed setting -- abstaining, or its own role's
equivalent, or active,
and the shared dossier cache's cached-JD length distribution (#169) -- how many entries
are empty, how many under 200 characters, how many under 800, and how many are
unreadable outright (a broken cache file -- an interrupted write, a bad disk -- kept as
its own bucket rather than folded into "empty", since that is a storage fault, not
evidence the fetch itself produced nothing), always reported as a fact rather than a
threshold verdict, since `min_jd_chars` ships at `0` (the near-empty band off) and a
count against that floor would be identically zero at the shipped default. A legacy
`cv.name`/`cv.contact` still set in `sluice.yaml` is a THIRD, separate failure mode: it
makes `load_cv_config()` raise, which `Sluice.doctor` catches ahead of the
deliberately-guarded `self.renderer()`/`self.store()` constructions below it (triage's config
loads first, unguarded -- it has no cv-shaped legacy-key hazard of its own) and turns into one
DEAD `cv-config` row naming the real error, rather than a traceback out of the one command a
user runs because something -- possibly that very config -- is wrong; only the three checks
that actually read `cv_cfg` (cv's own backend targets, the renderer, cv's row in the
gate-posture sweep, and #165's negatives-vs-Skills-Inventory cross-check, which sits inside
the store branch but is gated on the same condition) are skipped, and the report is
otherwise full -- the store's Candidate
Profile row, track/Google, camofox and every other sub-app's gate rows are unrelated to
`cv_cfg` and still run. Backend
classification is role-aware -- a keyless fallback degrades (the sanctioned
primary-only path, exit 0), a keyless PRIMARY is `setup` (see the state model below),
and a keyed-but-broken backend is `dead` regardless of
role, the silently-non-functional fallback the tool exists to catch. Component
classification adds a fifth state, `notice`, for the gate-posture rows -- and, since
#165, for the `cv.negatives[i]` rows reporting a configured negative that contradicts the
verified Skills Inventory, which name an INDEX and an overlap COUNT rather than the
user's own text, since a report is returned whole to MCP clients. #168's Task 10 added a
second cross-reference between the same two corpora, `core/doctor.py`'s
`classify_skills_reconciliation`: up to two more `notice` rows, `Skills
Inventory (unclaimed)` (an inventory skill no experience entry's `Skills:` claims) and
`Experience Library (unmatched)` (an entry's `Skills:` name absent from the inventory) --
each suppressed at zero and, the same posture, reporting only a COUNT rather than the
skill's own name. Unlike the `cv.negatives[i]` check just named, it is not one of the
`cv_cfg`-gated checks a few sentences up: it needs only both evidence corpora to be
readable, so it still runs when `cv_cfg` failed to load. A gate row whose role IS
declared never affects `exit_code`, under `--strict` or otherwise, because an abstaining
gate (an unconfigured preference simply passes every lead through) is the shipped default
and legitimate -- grading it as a failure would be the 672ad2a class of bug (see
Invariants) aimed at doctor's own exit status. The one row that does affect it is the
undeclared-role branch (#245): the sweep is by runtime `isinstance`, so a user's YAML can
put a list on a setting that takes a scalar, and that is a wrong-shaped VALUE rather than
an abstaining gate -- measured, `track.gmail_extra_query` as a list raises `TypeError` in
`track/engine.py`. It is DEGRADED, so `--strict` sees it; the developer case of a real
gate shipping without a role cannot reach here, because the build fails first.

**The `setup` state, and the verdict that reads it (#243).** Five states, not four: `setup`
is a component the user has not SUPPLIED yet, as distinct from one they supplied that does not
work. The split matters because `doctor` is the command `init` tells a new user to run next,
and on a fresh install every dead row was the former -- no baseline CV, no verified evidence,
no Candidate Profile, no `render` extra, no vault -- so the happy path printed a screenful of
rows across four states, several of them `dead`, and exited 1. The reassurance that this was expected had to
live in README prose, because the exit code said otherwise.

The rule is "did they give us something broken, or nothing at all". An unset API key is `setup`
and a key that fails its round-trip is `dead`. A `claude` CLI not on `$PATH` is `setup` when
`claude_max_path`/`compose_claude_path` hold the shipped bare `claude`, and `dead` when the user
NAMED a path that is not there -- a typo or a moved binary is something they supplied. That check
runs in BOTH modes; while it sat inside `classify`'s `if offline:` the two disagreed about one
fact, offline calling it `setup` while a live run skipped it, attempted the probe anyway and
reported `probe_error` -- so a fresh install with no `claude` got `Broken: triage leads, tailored
CVs, track replies` from plain `job-sluice doctor`, and only `--offline` told the truth.

A vault draws the same explicit-vs-default line: absent at the default `./vault` is `setup`
(nobody has run `init` yet), absent at a path the user configured is `dead`, because a named
directory that is gone is an unmounted drive or a renamed Obsidian folder and it stops every
sub-app. `Vault` records which case it is at construction (`vault_dir_is_default`), since that is
the last moment the distinction exists; `core/protocols.py` states the obligation for a second
store, and a store that stays silent gets the louder reading.

A missing or empty baseline CV is `setup`. An UNREADABLE one is not a `baseline_rel` row at all
-- `Vault.preflight` lets the `PermissionError` propagate, deliberately, rather than reading it
as absent, so the whole store report collapses to one `store`/`preflight` `dead` row carrying the
real error text. (A baseline that is a symlink out of the vault reads `ok`: `preflight` reads
through it. The symlink refusals are on the evidence directories, not here.)

The renderer fork is DECLARED by the seam, not inferred: `core/protocols.py`'s
`RenderDependencyError` (a `RenderError` subclass, so every existing `except RenderError` still
catches it) means "something I need is not installed", and `core/app.py` asks
`isinstance(e, RenderDependencyError)` and nothing else. The first cut asked
`isinstance(e.__cause__, ImportError)`, which was wrong three ways: it missed the case that
actually fires in the field (weasyprint importing with the extra present but cairo/pango absent
raises `OSError`, which `renderers/template.py` calls "the single likeliest real failure"), so the
documented macOS install got exit 1 under a heading saying something it had configured was broken;
widening that tuple would have mirrored one renderer's `except` clause, letting that renderer
silently change doctor's verdict, and would have swept in the packaged-template read one layer
down, whose own message says "reinstall sluice"; and a renderer raising from inside an `except`
with no `from` clause has `__cause__ = None` (implicit chaining sets `__context__`), so it
would be classified broken however plainly its message said "not installed".

`setup` never reaches `exit_code`, under `--strict` or otherwise, the same by-construction
exclusion `notice` has (the states the loop tests for, not a filter over a wider set). So
`doctor` exits 0 when nothing is broken and 1 when something is, which is what an alert wants to
fire on -- exit 0 means "nothing is broken", NOT "everything works", and the verdict above the
rows is what says what is still needed.

`DoctorReport.verdict()` is that verdict. It maps the rows onto `CAPABILITIES` -- the five
pipeline sub-apps with the phrase a user would recognise -- and buckets each capability as READY
(nothing blocks it), SETUP (everything blocking it is unsupplied), DEGRADED (it runs, but
something the user configured is not doing its job) or BROKEN (at least one blocker is supplied
and does not work), worst-of wins. DEGRADED sits above SETUP because an unsupplied thing does not
run and says so, while a misconfigured one runs and quietly does the wrong thing.

What a row blocks is `blocks`, and it is read on every blocking state rather than on two of them.
Reading it on `dead`/`setup` alone was measurably wrong in both directions: `classify_camofox`'s
`CAMOFOX_USER` mismatch is DEGRADED and carries `blocks=("ingest",)` -- the 2026-08-15 incident
where a run drove the wrong cookie profile and a board returned zero rows for days -- and printed
`Ready now: scrape job boards` directly above a `--verbose` row saying `blocks: ingest`, with the
remedy shown nowhere; and, the other way, a DEAD row carrying NO `blocks` changes no bucket, so an
unbuildable store printed four capabilities as ready. Those two rows in `core/app.py` now name
`ALL_CAPABILITIES`, derived from the roster rather than spelled out. A DEGRADED row with an empty
`blocks` still blocks nothing, which is what keeps the sanctioned keyless-fallback degrade out of
the verdict entirely.

The verdict re-derives nothing: it reads the states the classifiers already assigned, so the
default view and `--verbose` cannot disagree about a row. Backend rows block only where the target
is that sub-app's PRIMARY -- a shared target that is triage's primary and cv's fallback, with its
key unset, stops triage and merely degrades cv, which is what `Sluice.backend()`'s `auto` role
does at runtime.

`CAPABILITIES` is the one hand-written roster here (a label is not derivable: `cv` is the package
name, "tailored CVs" is what the user came for, and nothing in `sluice/` enumerates the sub-apps
to derive the keys from). Its correctness is held two ways. Membership is enforced at RUNTIME by
`ComponentCheck.__post_init__`, which refuses a `blocks` naming anything outside the roster -- the
same fail-loudly-at-construction rule unknown backend and adapter names follow, and it sees every
row wherever it is minted. Reachability -- that no label sits permanently in `Ready now` because
nothing can ever block it -- is swept from the source across every module under `sluice/` by
`tests/test_doctor_verdict.py`. The membership half used to be that sweep too, keyed on
`core/doctor.py` alone, and it certified only some of the rows: `core/app.py` mints them as well.

`doctor --require <capability>` is the machine-readable half of the same verdict, and the
answer to what the exit-code change took away from automation: a `setup` row does not fail the
build, so an install that stops working for a reason sluice reads as *unsupplied* -- a cron
unit whose `PATH` lacks `~/.local/bin`, an API key not exported outside an interactive shell --
exits 0. Naming a capability exits 1 the moment it is anything but READY. It compares against
that one bucket rather than listing the failing ones, so a fifth bucket cannot silently start
passing. It takes capability KEYS and never the display labels, which are prose and free to be
reworded; `Verdict.buckets` is the name-keyed map it reads, written in the same pass as the
four label lists so the two cannot disagree. An unknown name is a usage error (exit 2), raised
BEFORE the preflight runs and listing the valid names -- distinct on purpose from the exit 1
that means a capability is down, because a monitor must be able to tell those apart.

The roster itself lives in `core/protocols.py` rather than in `core/doctor.py` where its only
consumer is. That is measured, not tidiness: `cli.py` needs the names at PARSER-BUILD time to
list them in `--require`'s help, `_build_parser()` runs on every invocation, and importing
`core.doctor` there costs ~36ms and drags in `core.backends` -- one of the three families
`cli.py` deliberately keeps off the critical path. `core/protocols.py` was already imported at
`cli.py`'s module scope, costs ~3ms, and is where the rest of the cross-module vocabulary lives.
Hand-listing the names in the help string instead would be the stale-roster trap this repo keeps
walking into.

`cli.py` prints the verdict by DEFAULT and the two tables under `--verbose`. The table is
demoted, not deleted: it answers "is each component healthy", which is the right question once
something is wrong and the wrong one sixty seconds into an install. Every remedy still prints,
verbatim from the row that knows it -- doctor no longer appends a generic "and here is what to
do" blurb to a renderer error, which used to restate the raise site's own remedy and made that
one row 1,207 characters. Live round-trip by default; `--offline` for a
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

**The Candidate Profile read cadence is the same kind of deliberate divergence as the
clock shapes above, decided once here rather than left as an inconsistency between two
sub-apps' comments.** `apply` reads `store.read_candidate_profile()` exactly ONCE per
`prep()` call (`core/app.py`) and threads the result into `prep_one`/`preview_all` as a
required keyword — never re-read inside the batch loop. That makes two packets produced
in the same run agreeing with each other impossible to violate BY CONSTRUCTION: a
mid-run edit to the note cannot make lead A and lead B disagree, because both were built
from the one snapshot `prep()` resolved. `cv`, by contrast, reads the profile once PER
LEAD, inside `run_one` (`cv/engine.py`) — an ordinary-case simplification, not a
structural guarantee: a note edited mid-batch could in principle make two CVs in one
`cv run` disagree. Both are correct for what each artefact needs. `apply` must pin
`today` anyway (the same clock-freezing reasoning as above), and the profile rides
along on that same resolved snapshot. `cv` is self-consistent WITHIN a lead regardless —
`cv_name`/`cv_contact` are derived once at the top of `run_one` and feed both the
compose prompt and the `#99`/`#100` STRUCTURAL guards for that SAME lead, so no composed
CV is ever validated against an identity other than the one it was built under; the
per-lead read costs nothing beside a dossier fetch and up to two LLM calls. Do not
thread `profile` through `cv/engine.py::run_one` to match apply's shape unless a real
correctness bug turns up — that ripple was weighed against this PR's cost and rejected
as not worth it for zero measured gain.

