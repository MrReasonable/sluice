---
root: true
targets:
  - '*'
globs:
  - '**/*'
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This file is generated.** The canonical source is `.rulesync/rules/CLAUDE.md`. Run
`npm ci --ignore-scripts && npm run rulesync` after cloning to populate the AI-tool outputs
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, ...), all of which are gitignored. Editing a
generated file instead of the `.rulesync/` source is drift. The version and the flags both
live in `package.json`, so this command never names either -- and CI runs the same one,
`--ignore-scripts` included: one package in the pinned tree declares a postinstall, so a doc
that drops the flag sends a human down an install path CI deliberately does not take.

## Commands

```bash
pip install -e ".[test]"        # pytest, pytest-cov, faker (see Neutrality), jinja2 (see the
                                 # renderer seam below), setuptools + build (tests/test_packaging.py
                                 # builds a real wheel offline)
python -m pytest                # fast (well under a second), fully offline: no Camofox, no network
python -m pytest tests/test_triage_engine.py            # one file
python -m pytest tests/test_triage_engine.py -k judge   # one test
ruff check sluice tests scripts         # NB: ruff is NOT in [test]; pip install ruff==0.15.21 (the CI pin)

# Coverage, the way CI runs it (#11). Every knob -- which tree, branch coverage, the missing-line
# column -- lives in pyproject.toml, so the bare flag renders the same report CI publishes.
# It REPORTS and does not gate: there is no threshold, deliberately, because a floor invites
# tests written to raise the number rather than to catch bugs.
python -m pytest --cov

# Run ONCE before mutation testing: content-addresses the .pyc caches so a mutant can't run
# stale bytecode and lie green. Proving a test fails is the mutate-then-pytest step; see below.
# `scripts` is included so mutation-testing a scripts/ helper (e.g. guard_no_bypass.py) is covered too.
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

**Mutation testing.** The claim "this test would catch that" is worth nothing unverified, so the way
to check a test is load-bearing is to break the code and watch it go red — not to reason about it.
Two things make a mutant lie *green*, which reads as "this test is inert" and gets a real guard
deleted:

- **Mutate by MOVING or DELETING, never by ADDING.** A check added beside the original is an
  equivalent mutant: the original still fires and the suite stays green.
- **Stale bytecode.** CPython invalidates a `.pyc` on *(source mtime, size)*, so a size-preserving
  edit restored within the same second runs the OLD bytecode against the NEW source — silently.
  `text =` → `return` is exactly that shape (each carries a trailing space, so both are 7 bytes),
  and it has already cost a debugging session. The `compileall --invalidation-mode checked-hash`
  line above makes `sluice/`'s and `scripts/`'s caches content-addressed, which is what mutation
  testing needs, since mutants go in production code. Measured: 90/90 stay hash-based across mutate → pytest → restore,
  so it is durable and costs nothing measurable. Clearing `__pycache__` also works but is a
  discipline you must remember every time, and forgetting it fails in the dangerous direction.

  It content-addresses `sluice/` and `scripts/` (both are plain imports, so the checked-hash cache is
  what runs). It does **not** protect `tests/`, even though `tests` is on the line: pytest's assertion
  rewriter keeps its own `*-pytest-N.N.N.pyc` alongside, those are timestamp-based, and pytest imports
  *those*. So a size-preserving edit to a TEST file within the same second is still exposed. That is not
  the mutation-testing case (mutants go in production code under `sluice/` or `scripts/`), but do not
  read the line as protecting more than it does.

`inspect.getsource` cannot diagnose the second one — it re-reads the source file, so it happily
shows corrected code while stale bytecode executes. Run the function and look at what it returns.

**Guard tests fail open, and the failure is invisible.** Four ways it has actually happened here, each
found by running the guard rather than reading it:

- **A sweep that discovers nothing passes.** `all([])` is `True`, and a discovery loop whose matcher
  is broken yields an empty set that satisfies every assertion over it. Assert on the SCOPE, never on
  the violations: a guard must pin that it enumerated the things it meant to look at (the loaders,
  the `*Config` classes, the settings in the example file), because for a *negative* guard — a leak
  gate, a forbidden-pattern sweep — finding nothing is the success case, and demanding a non-empty
  result there would be backwards. One such assertion, added late, caught its own sweep matching
  nothing at all on the very first run — the walk resolved imports but not class definitions, so it
  had been enumerating an empty set.
- **A pattern consumed by two engines must be asserted through the engine that RUNS it.** A regex
  built for Python `re` and handed to `git grep -E` is not the same regex: inside a bracket
  expression POSIX treats `\` as a literal member, not an escape, so the class terminates early. A
  neutrality gate written that way matched nothing for its entire life while its regression test —
  which compiled the string with `re`, where the escapes do work — certified it green.
- **Hand-listed names lose to an import alias.** `from x import y as _z` walks straight past a sweep
  keyed on `"y"`. Derive the local bindings from each file's own `ImportFrom` and `ClassDef` nodes.
  For the same reason, an allow-list of path COMPONENTS accepts anything after the component; allow
  whole values.
- **A comment that states a mechanism needs a row that falsifies it.** `except BaseException` was
  justified in a comment by `KeyboardInterrupt`; swapping it to `except Exception` left the whole
  suite green, because nothing tested that arm. Prose is not a check, and a *reason* stated in a
  comment goes stale silently — grep the CLAIM, not just the code that changed.

The suite is fast and hermetic — there is no reason not to run all of it. `run_tests.sh` is the same
thing via `.venv/bin/python`, so it needs a `.venv/` (gitignored) to exist first. CI
(`.github/workflows/ci.yml`) runs four jobs: `lint` (ruff + zizmor), `test` (pytest on Python
3.12/3.13/3.14), `rulesync` (regenerates `.rulesync/`'s outputs and fails the build on any drift
or hand-edited generated file), and `ci-success`, the aggregate gate over the first three.

Running the pipeline:

```bash
export SLUICE_CONFIG="$(pwd)/sluice.local.yaml"  # git-ignored; quoted for paths with spaces
job-sluice init --no-input --vault ./vault       # writes the config + a Judging Profile
job-sluice ingest list-sources --health
job-sluice ingest run --source reed --dry-run  # dry-run/JSON sink never writes vault or seen.db
job-sluice triage run --no-llm              # deterministic classify only, no backend call
                                           # (needs leads already in the vault: the dry run above
                                           #  writes none, so drop --dry-run to feed this)
```

**Do NOT `cp sluice.yaml.example` into place — that gives you a config whose gates are already
CLOSED, and nothing says so.** `sluice.yaml.example` is a CATALOGUE: it ships illustrative values
ACTIVE, not commented, and `relevance_keep` is applied at ingest before dedup and before any LLM
call. Measured against a verbatim copy, `is_relevant("Senior Software Engineer")` is `False` — only
a `horticultural consultant` survives, and `accept_titles`, `contract_floor_gbp_day` and
`perm_floor_gbp` are live too. So a fresh copy scrapes and then silently discards nearly
everything, which reads as a broken source rather than a closed gate.

`job-sluice init` (#8) exists to remove that trap: it renders the config FROM the question catalogue
with every unanswered key COMMENTED, so an unanswered run writes a file that is field-for-field
equal to no config at all EXCEPT `vault_dir` — the wizard's one required answer, and the one
difference `tests/test_onboard_plan.py` exempts by name. It never overwrites an artefact, so
re-running is safe. The example file stays a catalogue to read, not a template to copy, and
`tests/test_no_copy_instruction.py` fails the build if any shipped doc goes back to instructing
the copy.

`ingest run` and `ingest test-source` drive a live Camofox browser server; every other command is
offline. `job-sluice ingest test-source ID --raw` prints the raw fetch payload, which is how golden
parser fixtures get captured.

## Architecture

Pipeline: `ingest -> triage -> cv -> apply -> track`. Five sub-apps under `sluice/`, all sitting on
`sluice/core/`, plus `sluice/onboard/` — a COMMAND package for `job-sluice init`, not a sixth sub-app:
nothing downstream imports it and it sits beside the pipeline rather than inside it.
`docs/ARCHITECTURE.md` has the per-module detail; what follows is what you cannot see from the file
tree.

**Config is layered and single-file.** Code defaults < the YAML file at `$SLUICE_CONFIG` (else
`<XDG config>/sluice/config.yaml`) < env vars. Each sub-app has its own `load_*_config()` reading
its own top-level block of that same file (`triage:`, `cv:`, `apply:`, `track:`); ingest reads the
root keys. Every knob has a code default, so everything runs with no config file at all. New
tunables go in the relevant `*Config` dataclass and `sluice.yaml.example` — never hardcoded in
logic. Only `load_config` names its fields explicitly; the four sub-app loaders are
`hasattr`-filtered `setattr` loops, so a new ROOT field is dead until `load_config` names it, and
the sub-app loaders must not be "fixed" into naming theirs (`load_track_config`'s merged-denylist
branch lives in that loop).

**Every RELOCATABLE path goes through `core/paths.py` (#80).** One `resolve()`, one order — env var,
then config key, then the XDG base directory for that `kind`. An explicitly-named value — env var or
config key — is taken as the caller gave it EXCEPT for a leading `~`, which is expanded: returning it
verbatim while the XDG fallback expanded made one resolver answer two ways, and `SEEN_DB=~/state/seen.db`
then loaded an EMPTY dedup set (the #81 harm) with nothing said, because naming a path short-circuits
the relocation check that would have spoken. Expanding is where it stops — `expanduser` at ingress,
`abspath` only where a value outlives the cwd it was read in, neither at consumption;
`docs/ARCHITECTURE.md` carries that rule and the one exception it looks like it has. It is not every
path in the codebase,
and the exceptions are deliberate: seven artefact paths stay cwd-relative — the five CV working
directories in `apply/config.py`, `cv/config.py` and `cv/render.py`, the render SCRIPT
(`cv/config.py`'s `render_script`, an executable rather than a directory), and `core/vault.py`'s
`DEFAULT_VAULT` — because they name a workspace the user is standing in rather than per-system
state. `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py` pins them at nine lines
(`cv-served` and `cv-home` appear twice each). Two things follow that are easy to undo by
accident. A path's config default must be `""`: a non-empty default is always truthy, so it
short-circuits the chain, the XDG location is never reached, and nothing goes red while the feature
is inert. And precedence belongs in the FACTORY, never ahead of an explicit constructor argument —
`stores/vault.py:_make` and `HealthStore`/`SeenDb`'s `path or resolve(...)` are that shape, because
the reverse would make an env var beat the ~150 positional `Vault(str(tmp_path))` constructions in
the suite and retarget them at a developer's real vault, green in CI throughout. The vault itself
does NOT relocate (it is the user's Obsidian directory), and its two-term `or` lives in `_make`.

Nothing is auto-migrated. A path left behind warns; the two dedup stores REFUSE, because an empty
dedup set makes every already-known lead read as unseen and silently re-submits it to the write
path. `Vault.upsert` now probes `_merged/` by name before creating (#81), so a lead a human merged
away usually self-heals rather than being re-created -- but that probe is name-keyed, so a re-scrape
whose title has drifted past every name candidate still slips past it and is created afresh, and if
its twin was already `applied` that is a second application under the user's name. **That notice is
keyed on the resolved path not EXISTING, so any code that touches it — even harmlessly — disarms it
from then on.** `sqlite3.connect` creates a 0-byte file
merely by opening one, which is how a `--dry-run` silently disabled the refusal for every later real
run; a store therefore must not create anything on a read. For the same reason a store must not read
an unreadable file as empty: the relocated case and the corrupt case cause the identical harm, so
both are loud. The two are scoped DIFFERENTLY, and the difference is deliberate: `ingest`
refuses only when the run actually writes dedup state (`--dry-run` and `--sink json` proceed), while
every `track` command refuses including its dry runs, because a track dry run READS the #49
dead-letter store to report what it would do and against a relocated store would report nothing to
do. `doctor` never refuses — a relocated file is exactly what one runs it to hear about. An
explicitly named path (env var or config key) short-circuits before the check either way, so callers
who name their own paths are immune by construction. `tests/conftest.py`'s autouse fixture sandboxes
every one of these; `XDG_CONFIG_HOME` and `HOME` are consecutive rungs of one chain, so both are
load-bearing and neither substitutes for the other.

**The `leads` passes report by default; the pipeline commands write by default.** `leads dedupe`
(`--merge ID [ID ...]`), `leads expire` (`--expire [SLUG...]`) and `leads reconcile` (`--apply`, #1)
print and change nothing until told otherwise, and none offers `--dry-run` — the default IS the dry
run, and a flag that does nothing is drift. `triage run`/`ingest run`/`track run` invert both halves. The distinguishing
property is whose judgement the write encodes: a pipeline command acts on a verdict the user
configured, while a `leads` pass writes over a set the TOOL computed, so a mistyped one should print
a list rather than change a hundred notes. **Exception: `leads dismiss` writes unconditionally on
every call** (#131), like the pipeline commands, not like its `leads` siblings — the verdict it
writes is the one the user typed (`--lead`/`--reason`), not one the tool computed. (`docs/ARCHITECTURE.md`
has the per-pass mechanics.)

**Backends are selected by role, not provider.** `--backend` takes `auto|primary|fallback`
(`claude-max`/`deepseek` survive as deprecated aliases). Which provider fills each role is config
(`primary_backend`, `fallback_backend`). `auto` degrades to primary-only when the fallback has no API
key, and warns loudly; `--backend fallback` hard-errors instead, because there is nothing to degrade
to. Construction failures are raised at build time, not at first call. See `_select_backend` in
`cli.py`.

**Sources are declarative plugins.** A module in `sluice/ingest/sources/` calls `register(...)` at
import time; the package auto-imports every sibling, and one broken plugin is logged and skipped
rather than sinking the registry. Most boards are one `BrowserListSource(id, extractor_js,
searches_spec, ...)` literal — see `remoteok.py`. `Source` splits impure `fetch` (drives the browser)
from pure `parse` (raw dict -> `list[Lead]`), which is the whole reason parsers can be tested offline
against golden fixtures. Each source ships exactly one neutral example search; a user's real search
list belongs in `sources.<id>.searches` in config.

**`cli.py` imports the heavy modules inside command functions, not at module scope.** That is
deliberate: it keeps offline commands (and their tests) from ever touching Camofox, the vault, or a
backend. Keep new commands lazy the same way.

## Invariants

These are load-bearing, enforced by tests, and easy to break by accident. They are also the hard
rules the review agents enforce — see `.rulesync/skills/review-pr/SKILL.md`.

**Never-clobber (writes).** A re-scrape of an existing lead touches only its `last_seen` marker —
never status, never enrichment, never the note body. Creating a note for a genuinely new lead is the
only wholesale write. Rewriting notes wholesale is the exact fragility sluice exists to remove. Every
*modify*-write (status, scores, enrichment, the CV pointer) goes through the surgical compare-and-set
path in `core/vault.py`: the edit is re-derived from the *fresh* note on each attempt and committed via
a temp-file + `os.replace` (torn-file safety), so a concurrent writer's other keys and body survive; a
sustained race raises `VaultConflict` (`core/protocols.py`, a Store-contract outcome) rather than
clobbering, and callers treat that as a non-fatal outcome. It is best-effort under a residual
compare→replace micro-window, not a lock — the primary threat is a human editing the note in Obsidian,
who takes no lock (#16). `update_fields` also accepts `require_status` (#9): a frozen set the
transform re-reads the *fresh* status against, abstaining (returning `False`, writing nothing) on a
mismatch. That check CANNOT be hoisted into the caller — probed against a real vault, a guard on the
enumerated `LeadNote` is byte-identical to no guard at all, because the snapshot is stale by
construction. It is a parameter on the existing writer rather than a second write function, because
CodeQL flags a new write function as a new sink.

`job-sluice leads reconcile` (#1) is the one pass that MOVES a note, and a move writes no note bytes —
only a directory entry, via the `O_EXCL`-reserve + `os.replace` primitive `merge_cluster` shares. It
never read-modify-writes a status, so never-regress is untouched. That is NOT the same as
never-clobber holding "by construction", and the difference is measured: a move landing between
`_cas_write`'s freshness re-read and `_atomic_write`'s `os.replace(tmp, path)` RE-CREATES the source
path, leaving two notes at one basename and a lead `upsert` then refuses permanently. No portable
stdlib atomic-conditional-rename exists, so it is the same accepted residual as `_cas_write`'s own
micro-window — documented, warned about in the command's help, and REPORTED by the sweep that caused
it rather than left for a later ingest to surface as an unexplained refusal. That report is a single
post-sweep SNAPSHOT and so is best-effort: a race landing after it is missed and surfaces on the next
run instead. It turns the common case from silent into named, which is the whole claim.

A move must also never follow a SYMLINK. `_walk` keeps `os.walk`'s `followlinks=False`, so a
symlinked `Active/` is outside the scan set: a note filed there leaves `read_leads` AND `_locate`,
every later scrape refuses, and the lead is invisible for good — measured at exit 0 with zero log
records emitted. Both the reconcile destination and the create-arm write folder refuse a symlink
rather than writing into one.

**Non-resurrection (#81), in the never-clobber family.** A lead a human merged away via `sluice
leads dedupe --merge` must not be silently re-created by a later re-scrape — a wrong create undoes a
human's decision and, if its surviving twin was already `applied`, means a second application under
the user's name. `merge_cluster` archives each loser under `leads_dir/_merged/` and stamps the note
name it was seated at into it (`archived_from_note`); `Vault._resolve_path` probes that archive
before returning `create`, via the ONE verdict `_reconcile` (shared with the active walk, so the two
cannot drift), and `_archived_match` maps the result. `upsert`'s return vocabulary is therefore
SIX-member — `created`/`updated`/`merged`/`refused`, plus `merged_away` and `merged_away_unproven`.
Both archive outcomes write NOTHING: not the note, not `leads_dir`, not the Syncthing marker. They
differ only in evidence, and that difference is the load-bearing part: `merged_away` requires a
url-PROVEN match (both urls non-empty and equal) and is the only one of the TWO the ingest sink
records in `seen.db`: it joins the allowlist, which reads
`created`/`updated`/`merged`/`merged_away`. Every weaker match — a location-token overlap, or an
inconclusive comparison — is `merged_away_unproven` and must NEVER be recorded, because `seen.db`
has no removal path and a same-company/title/location RE-POST carrying a brand-new url is a real job
that would otherwise be suppressed forever with no note anywhere to reverse it. That arm therefore
re-reports on every run until a human acts, and there is exactly ONE action — the same hand-move
`docs/ARCHITECTURE.md` documents as the recovery path: move the archived note back out of
`_merged/`. It returns to the active view, the next scrape reconciles against it as an ordinary
note, and the outcome becomes `updated` (a location-only SAME) or `merged` (an inconclusive
comparison) -- measured on BOTH arms. Either is on the allowlist, so the count stops. The Store
contract states the obligation as bounded, not absolute: a merged-away loser must remain
discoverable through the identity the store RECORDED at merge time, and a re-scrape whose identity
has drifted past that (for the vault, past every name candidate) is outside the guarantee and is
created — a visible duplicate, the direction to fail in. `_merged/` is load-bearing retention, not
scratch: do not prune it. The lead scan is recursive (#1), so `_merged/` is excluded from it BY NAME
(`_PRIVATE_SUBDIRS`, at the TOP LEVEL only) rather than by the accident that a flat `os.listdir` never descended into
it -- deleting that prune resurfaces every archived loser and undoes this invariant outright.
See `core/protocols.py`, `docs/ARCHITECTURE.md`, and
`tests/conformance/test_store_contract.py::test_merged_away_lead_is_never_recreated`.

**Never-regress (status).** One `status` frontmatter key, two lifecycles with separate owners
(`core/status.py`). Triage owns `new/shortlist/research/needs_review/dismiss` and may rewrite them;
track owns `applied/phone_screen/.../rejected` and triage must never touch a lead that has entered
that lifecycle. Status only moves forward on the ladder; terminals are never advanced out of.
`shortlist -> applied` is the only transition apply may make on send; track makes the same
transition when a confirmation receipt arrives (`track/receipt.py`, #10) — both route
through the one `can_apply` predicate (`can_transition` dispatches a `--to applied` request to it,
since `track confirm` accepts an arbitrary target), so apply-on-send and track-on-receipt are the
sole crossings into the application lifecycle; every later move is an on-ladder `can_advance` step.
A receipt auto-advances only under the FULL guard set — a `proof`-tier match (the sender host
matches one of the lead's known hosts — `applied_url` then `url`, #136 — on a message whose
`Authentication-Results` records a PASS aligned with that sender, and neither host multi-tenant: no
ATS relay, no job board sluice scrapes), the lead present in `receipt_by_slug` (the combined
shortlist ∪ in-flight index `track/engine.py` matches receipts against, so a lead already at
`applied` or later can be domain-matched too — `can_apply` below is what actually restricts the
WRITE to `shortlist`), `can_apply`, and `confidence >= auto_apply_min`. Every weaker outcome
proposes to the dead-letter for a human, because a wrong `applied` silently suppresses a real
application and is irreversible. A domain match for a lead ALREADY past `shortlist` cannot write
(`can_apply` refuses), so it stamps the evidence onto the lead's own note instead of proposing —
see `docs/ARCHITECTURE.md`'s track paragraph for the reasoning.
An unrecognized status is passed through untouched rather than silently rewritten.
`job-sluice leads expire` (#9) writes `dismiss` — triage-owned, so never-regress permits it — and never
a `_TERMINAL`, since every terminal is application-owned. It reads only
`TRIAGE_OWNED - {"dismiss"}` (derived, never hand-listed, so the set cannot name an
application-owned state) and passes that same set as `require_status`, which is what actually holds
the invariant when a lead enters the application lifecycle mid-sweep. It is NOT unconditional: a
lead holding a `pending_cv` sign-off hold (#60) is refused, because dismissing it silently discards
a composed CV no human has signed off. Any second bulk-dismiss path must refuse the same.
`Sluice.dismiss_lead()` (#131) is that second writer -- a single-lead dismiss, not a
bulk sweep, so `expire_report`'s pre-filtering argument does not apply to it; it uses
its own `_DISMISSABLE_FROM` (the full `TRIAGE_OWNED` set, `dismiss` included) rather
than `_EXPIRABLE`, and its `pending_cv` sign-off-hold refusal is checked CAS-fresh
inside the write transform via `require_blank` -- unlike `leads expire`'s equivalent
refusal, which is still decided from a snapshot.

**Empty config means abstain, not match-nothing.** Every preference gate (`accept_titles`,
`target_locations`, `reject_companies`, `relevance_keep`/`relevance_drop`, pay floors) defaults to
empty/zero, and an unconfigured gate passes every lead through. Getting this backwards silently bins
someone's entire job hunt — it has happened once already (`672ad2a`), and
`tests/test_sluice_neutral_defaults.py` now fails the build if it recurs. `lead_ttl_days` (#9) is
the same shape at the root config: `0` means staleness is OFF, so an unconfigured install expires
nothing and refuses nothing. Its validator rejects `bool` *before* checking `int`, because `bool`
subclasses `int` and PyYAML resolves `yes`/`on`/`true` to `True` — so `lead_ttl_days: yes`, the
natural thing to type to turn the feature ON, would otherwise load as a one-day TTL and mark every
lead stale with no error anywhere. The list-keyed neutral-defaults sweep does NOT cover int fields
and must not be widened to: `0 == abstain` is not universal (the dossier-cache `ttl_days: int = 7`
is a legitimate non-zero default), so this knob carries its own named guard.

`lead_layout` (#1) is the THIRD root knob with that property: `""` is flat, so an unconfigured
install files notes exactly where the pre-#1 store did, and it is what keeps the whole layout
feature inert until someone opts in. Its failure mode is a NAME rather than a value — a plain
membership check against `LEAD_LAYOUTS`, with none of `lead_ttl_days`' bool-subclasses-int hazard —
so it raises and lists the valid ones at BOTH `load_config` (a YAML typo is a usage error, not a
traceback) and `Vault.__init__` (which is what covers the ~150 direct `Vault(...)` constructions a
loader-only check would miss). It carries its own named guard too, for the mirror-image reason:
the sweep is keyed on LIST defaults, so a `str` field is invisible to it.

**The CV fabrication gate is hard.** `cv/validate.py` is pure and deterministic: every WORK bullet
must cite a real bundle `[id]` and every number in a bullet must appear in a cited entry; the PROFILE
prose (which has no per-bullet citations) has a bundle-wide numeric floor — a figure present nowhere in
the bundle is a violation, citations stripped with render's exact `_CITE_RE` — and a composed CV
missing the exact `WORK EXPERIENCE`/`PROFILE` headers fails closed, since the section-keyed checks
would otherwise silently not run. A non-empty violation list blocks rendering; the engine retries
composition exactly once with the violations fed back, then skips the lead — a CV is never rendered
ungated. Above this hard gate sits a softer, human-facing layer (#60, on by default via
`cv.require_signoff`): an advisory LLM audit (`cv/audit.py`) catches the qualitative fabrication the
deterministic gate cannot, and an `unsupported` flag WITHHOLDS the send-ready `tailored_cv` pointer (status `needs-signoff`, via
`Store.sign_off`/`hold_for_signoff`, cleared by `job-sluice cv signoff`) rather than blocking rendering —
it never touches the pure hard gate.

**The gate is blind to the name/contact block, and that block renders as the PDF's headline (#99).**
`cv/validate.py` never inspects anything before `PROFILE`; `cv/parse.py`'s grammar takes the LAST
non-blank line before `PROFILE` as the name and everything before it as contact, with zero shape check
on either. Measured on the real production path: a composer's routine one-sentence preamble ahead of
the CV proper desyncs that assignment silently -- a LinkedIn-URL contact line became the parsed name,
the real name landed in contact, `validate()` reported zero violations, and the CV would have rendered.
`cv/engine.py`'s retry loop closes this with three inline STRUCTURAL guards, in the same shape as the
`WORK EXPERIENCE`/`PROFILE` header checks beside them: the header block's LINE COUNT must match
`contact_block(profile)`'s lines plus one (the name), its LAST line must case-fold-match
`full_name(profile)`, and the lines BEFORE that last one must equal `contact_block(profile)`'s own
non-empty lines verbatim. The third guard was added on CodeRabbit's review of the first two (PR #100):
a same-count preamble occupying exactly the contact slot, with the name still correctly anchored,
passed both of the first two checks while silently dropping the real contact information. It runs LAST,
after the name-anchor check, because a same-count REORDERING also fails the content comparison and the
anchor check's message is the more specific diagnosis for that shape. All three compare against
`cv_name`/`cv_contact` (#133/#107: `cv/engine.py` derives both once per lead, before any spend, as
`full_name(profile)`/`contact_block(profile)` off the vault's Candidate Profile note --
`core/candidate.py`) -- ground truth `cv/parse.py` never has, since it is pure and takes only `text` --
and all three live in the engine rather than reaching them through `cv.parse` or a renderer's
`precheck`, for the same reason as each other: `precheck` only reaches the `template` renderer (`script`
implements none at all, `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar`),
while these guards must bind every renderer alike, because the shape they enforce is what `compose.py`'s
own prompt REQUESTED, not what any one renderer's LAYOUT needs. **The engine may guard what the prompt
required; only a renderer may guard what its own layout needs.** A composed CV whose derived
`full_name(profile)`/`contact_block(profile)` are blank needs no preamble at all to fail the same way --
it is the model complying exactly with what the vault declared, and no STRUCTURAL check can distinguish
a blank derived name from a genuine one. There is no placeholder sentinel this GATE compares
against any more: identity moved out of `cv.name`/`cv.contact` config keys and into the vault
note (#133/#107), so a derived value that is blank just IS blank -- "" cannot collide with a real
name the way the old `"Your Name"` default theoretically could. (The literal string no longer
ships at all: `cv/compose.py`'s `build_prompt`/`compose` made `name` a required KEYWORD-ONLY
argument with no default, closing even the unreachable path rather than leaving an inert
placeholder behind it -- the one production caller, `cv/engine.py`'s `run_one`, already passed
`name=cv_name` explicitly, so nothing there changed; `compose.py`'s own unit tests now pass a
fixture identity instead of relying on a shipped default. `contact` keeps its `""` default,
deliberately: an empty contact block is the neutral, already-abstain-shaped value this codebase
uses throughout, not a placeholder that could misrepresent anyone.) `cv/engine.py` therefore also refuses to compose at all
while either derived value is blank, before any dossier fetch or LLM spend (`skipped-config`), mirroring
the `#9` staleness guard beside it -- the same "quiet wrong default" posture this codebase takes
elsewhere (e.g. `cv/config.py`'s `load_cv_config`, which raises loudly rather than silently drop a
legacy `cv.name`/`cv.contact` left in a config file), applied here to the single most visible line of an
artefact sent under the user's identity.

**`compose.py` recovers the artefact from an agentic backend's conversational envelope (#28).**
`claude --print` is Claude Code, an agent, not a completion endpoint: given a "compose X" prompt it
may write a file and summarise, ask clarifying questions, hedge about missing tools, comply with a
drifted output format, or -- the shape that survived #91's argv fix and #99/#100's header guards --
comply with everything except "no preamble, acknowledgement, commentary, separator, or closing
remark", wrapping an otherwise gate-clean CV in a short conversational aside on one or both sides,
delimited by a markdown-style `---` line. `slop.py`'s unqualified `DOUBLE-HYPHEN-DASH` rule correctly
rejects a bare `---` either way -- the fix is not to weaken that gate but to recover the real CV
before it reaches the gate at all. `compose()`'s `_unwrap_agent_envelope` strips a short aside
(fewer than four non-blank lines) from before the first such fence and/or after the last one, but
ONLY when none of those lines is one of the CV's own section headers OR shaped like one of its
entries (a bullet, or a pipe-separated `dates | LOCATION | Role` meta line); a fence anywhere else, or
one with genuine CV content on either side, is left untouched, because guessing wrong there would
silently discard a real section rather than merely fail a gate that retries. It closes the specific
gap the original fix candidate for `#28` (a two-fence-only unwrap) left open: a model complying with
"no preamble" still appends a closing remark behind a SINGLE fence, which a two-fence-only unwrap
cannot see. The entry-shape check itself closes two content-loss findings from this fix's own
`/review-pr` round, both confirmed by execution rather than argued from the code: a genuine final
WORK EXPERIENCE entry (company line, meta line, one cited bullet) is exactly as short and as
header-free as a real conversational aside, and so is a section's own body when a fence lands right
after that section's header rather than before it, since the header is then on the wrong side of the
fence for the header check alone to see. Checking for the entry's own shape catches both without
needing to remember what already appeared on the other side of a fence. The one gap still accepted
is a fence positioned between the name and `PROFILE` -- never observed on the real production path,
since every captured case wraps the WHOLE CV rather than a sub-slice of its own header, and a bare
name line has neither a section header nor an entry shape to be caught by -- which this function may
still misread as a genuine leading aside and strip along with the real name; that degrades safely
rather than silently, because the resulting headerless CV still trips the `#99` STRUCTURAL count
guard immediately above and forces the ordinary retry rather than shipping nameless.

**A renderer's `precheck` must never be STRICTER than that gate** -- with two narrowly-scoped,
individually-justified exceptions, both stated below with the test that licenses them ("the refusal
must be answerable WITHOUT inventing content"); read to the end of this section before concluding a
given refusal violates the rule. `cv/engine.py`'s retry loop also
calls the Renderer seam's optional `precheck(cv_text) -> list[str]` (`core/protocols.py`) and folds
its strings in with the gate's violations, so a renderer's own grammar reaches the model's one retry
rather than arriving after the LLM spend. That makes it the one place a formatting rule can bin a
lead the gate certified clean: gate passes → precheck refuses → compose, gate green, retry, fail,
lead binned. Instances of it shipped on the `template` renderer's parser REPEATEDLY, each found by
someone happening to think of a case and adding a row: the en dash, the em dash, the word `to`, the
terminal token's casing and spelling, a single-digit month, a case-drifted section header, a blank
line under a trailing header, an en-dash CERTIFICATES marker, a LOCATION field nothing upstream can
supply — and, on a second review round, that same LOCATION field spelled as a BLANK MIDDLE pipe
(`dates |  | Role`), which was refused while the two-field spelling of the identical fact was
accepted. Deliberately no total — the count is not derivable from anything executable, two files
carried different numbers, and this repo has already been bitten by a stale count in prose twice
(`core/paths.py`'s ingress sites). The LOCATION one is the worst shape: the only actionable reading
of "add the missing field" is *invent a city*, so a parser refusal became fabrication pressure aimed
at the feature that exists to prevent fabrication. Widen the parser, never `cv/validate.py`.

`tests/test_cv_parse.py`'s implication sweep is the standing check, and **its coverage is narrower
than the rule** — read this before concluding a case is already swept. It asserts
`validate(cv, bundle) == [] ⇒ parse_cv(cv) does not raise` with the antecedent COMPUTED from the
real gate per row, over one alphabet: separator × terminal token × start-month width, applied to the
FIRST role's date range in one fixture. So it covers `parts[0]` of one meta line and nothing else.
Known un-swept axis, measured: a FOUR-field meta line
(`02/2023–present | Example Location A | Staff Engineer | Platform`) is gate-CLEAN and
refused. That one is left refusing on purpose — four fields is genuinely malformed and the message
names the expected shape, so the retry can act on it — but it is a gap in the sweep, not a case the
sweep passed.

The name/contact misassignment (#99, above) is a THIRD gate-clean, un-swept case, and it is
deliberately NOT a third exception to the implication: `parse_cv` still does not raise on it, on
purpose, because the parser has no ground truth to refuse against and tightening it would only
protect the `template` renderer for zero added coverage on the path that actually ships. Protection
lives at `cv/engine.py` instead, comparing the same header block against the vault-derived
`cv_name`/`cv_contact` (`core/candidate.py`) -- `cvcfg` carries neither field as of #133/#107.
`test_a_preamble_line_is_gate_clean_and_parsed_without_refusal_on_purpose`
(`tests/test_cv_parse.py`) pins this as intentional, the same way the two exceptions below are
pinned — read it before "fixing" this by widening the parser after all.

A REPEATED trailing header (`CERTIFICATES` … `EDUCATION` … `CERTIFICATES` again) is the second
deliberate refusal of gate-clean input, and it was added rather than inherited: measured, it was
gate-clean AND slop-clean AND parsed without raising, returning `certificates == []` with the second
block's entries gone — and since the template guards each section with `{% if document.certificates
%}`, the heading vanished with them, so the PDF was indistinguishable from a candidate who holds
none. Both exceptions pass the same test, and it is the test to apply to any third: the refusal must
be answerable WITHOUT inventing content (here, merge the two headings). That is exactly what the
LOCATION refusal failed, and why that one went the other way. The repeat is refused even when it
turns out to be empty and so drops nothing; that over-refusal is stated in `cv/parse.py` rather than
disguised as a distinction the code draws.

The one place a parser may legitimately be stricter is a WORK bullet marker, and there the
requirement is EQUALITY with the gate, not merely "no wider": a marker `validate.py` does not also
citation-check would render an UNCITED bullet into the PDF ungated, while one it checks and the
parser rejects is the governing bug class again. `_TRAILING_MARKERS` (CERTIFICATES/EDUCATION, which
the gate never citation-checks, so the bypass argument has no force there) is a separate, wider
tuple from `_BULLET_MARKERS` for exactly that reason — never widen the shared one.

**Neutrality: no personal data in this repo.** No employer names, role preferences, locations,
contact details, hostnames, or absolute paths in `sluice/` or `tests/`. The judge's criteria are read
at runtime from the user's vault (`Job Applications/Judging Profile.md`), never from source. Tests
generate synthetic job titles with seeded `faker` (`tests/conftest.py`) rather than hardcoding
anyone's taste. Personal values reach the code only through `sluice.local.yaml` and the vault.

**`sluice/` is standard-library only.** The sole exceptions: `yaml`, imported under a guarded
`try/except ImportError` in each config module; the Google client libraries, imported lazily inside
functions in `track/google_client.py`; `jinja2`/`weasyprint`, both imported lazily inside
`renderers/template.py` (`renderers/weasyprint.py` -- the old bundled renderer -- is DELETED;
selecting the retired `weasyprint` renderer name now raises via `plugins._RETIRED`, naming
`template` as the replacement); and `argcomplete`, imported under the same guarded
`try/except ImportError` shape at the top of `cli.py`, behind the `completion` extra --
`argcomplete.autocomplete(parser)` is itself a no-op unless a shell's completion hook has set
`_ARGCOMPLETE`, so importing it costs nothing on an ordinary invocation, and its `.completer`
callbacks (see `_complete_source_id`/`_complete_status`) must never raise, since an exception
there breaks the user's shell on every TAB press, not just the one command.

And `mcp`, imported lazily inside `build_server()`'s own function body in
`sluice/mcpserver.py` (never at module scope, and nowhere in `cli.py` at all) behind
the `mcp` extra -- it pulls in an async/network stack (uvicorn, starlette, anyio,
pydantic, ...) meaningfully heavier than a config-file parser, so nothing outside
`job-sluice mcp serve` may cause it to load; a bare install never imports it.

HTTP goes through `urllib`, not `requests`. Do not add a runtime dependency without a deliberate decision. The rule
binds `sluice/` -- what ships to a user. The root `package.json` is not an exception to it: it
pins the Node-based `rulesync` CLI that regenerates `.rulesync/`'s AI-tool outputs, a CI-only
dev-time tool that never ships in the package and nothing a user installing `job-sluice` ever
sees. Nor is the `test` extra (`pytest`, `faker`, `pytest-cov`, `setuptools`, `build`) --
installed to run the gate and never imported by `sluice/`. Being an EXTRA is not what exempts a
package from the rule, which is the part the table disguises: `render`, `google` and `completion`
sit beside `test` in the same `optional-dependencies` and are firmly INSIDE the rule -- they
install the very `jinja2`/`weasyprint`, Google, and `argcomplete` imports named above. `jinja2`
ALSO sits in `test` (deliberately -- see Commands above, so a shipped-template test runs for real
in CI rather than skipping the way an earlier `weasyprint` importorskip once did), but being in
two extras at once does not move it out of the rule: it is still `render` that puts it firmly
inside, exactly like `weasyprint`. `mcp` sits in BOTH `mcp` and `test` for the identical reason --
CI installs only `[test]`, and `tests/functional/test_mcp_contract.py` needs the real package to
drive it for real rather than skip itself. Being in two extras does not move it out of the rule
either: it is still `mcp` that puts it firmly inside. The line is whether a user's install can end
up executing it.

**Fail loudly at construction.** An unknown backend/adapter name raises and lists the valid names
rather than falling through to a default. A quiet wrong default is the bug class this codebase most
consistently engineers out; see `_select_backend`'s guard in `cli.py`.

## Conventions

- Comments explain *why* — the invariant being upheld, the bug being prevented, the trade-off taken.
  The existing code is dense with them and several encode real incidents; match that density rather
  than stripping it.
- Conventional commits (`fix(triage): ...`, `ci: ...`, `docs: ...`). These are not decoration
  since #12: release-please reads the subjects to decide the next version and to draft the
  changelog, so a mistyped type silently changes what gets released.
- **The PyPI distribution name is `job-sluice`, not `sluice`.** The latter has been squatted
  since 2015 by an unrelated, dormant zfs-snapshot tool with no console script of its own (no
  binary collision, but `pip install sluice` could never resolve here). Distribution name,
  import package, and console-script name are three independent things in Python packaging, and
  only two of them changed: `pyproject.toml`'s `[project] name` and `[project.scripts]` are both
  `job-sluice`, but `import sluice` and every `SLUICE_*` env var / `~/.config/sluice/` XDG path
  stay exactly as they are -- those are invisible to a user, and renaming them would be a
  breaking CONFIG change (this project's own change-classification rule below rates that above a
  breaking API change) for no user-visible benefit. Do not "fix" `job-sluice` back to `sluice`
  anywhere it appears in `pyproject.toml`, `cli.py`'s `prog=`/`--version`, or a user-facing
  printed string -- see `test_release_version.py` and `tests/test_docs_claims.py`, both of which
  pin this.
- **The version has ONE home: `sluice/__init__.py`.** `pyproject.toml` declares `dynamic` and
  setuptools reads that attribute statically, so `pip show job-sluice` and `job-sluice --version`
  cannot disagree — there is no second value to drift from. The line carries an
  `# x-release-please-version` marker and `release-please-config.json` lists the file in
  `extra-files`; BOTH are required, they are independent, and losing either stops the bump while
  the release PR still opens and the changelog still updates. `tests/test_release_version.py`
  pins the pair, enumerating marker-carrying files by walk rather than naming the path.
- **Releases are cut by merging release-please's PR**, never by tagging from a shell: the tag and
  the version are written by the same tool in the same commit. Edit the generated changelog entry
  IN that PR before merging — a `fix(vault): ...` subject cannot say that a config now means
  something different, and a breaking CONFIG change outranks a breaking API change here. Note the
  PR needs a token that is not the default `GITHUB_TOKEN`, or the `qa-gates` ruleset blocks it
  forever: GitHub raises no workflow runs from `GITHUB_TOKEN` events, so `ci-success` never
  reports on it. It is a GitHub App token minted per run, NOT a PAT, and the reason is the
  approval leg: a PAT opens the PR as the repo owner, nobody may approve their own PR, and
  `.coderabbit.yaml` now skips release PRs — so a PAT would deadlock them. An App authors the
  PR, leaving a human free to approve.
- Tests assert on behaviour, not merely that code runs. Fixtures stay synthetic.
- The four adapter seams (backend, store, renderer, fetcher — the config keys, and the
  `_STORE_SEAM`/`_FETCHER_SEAM`/`_RENDERER_SEAM` constants in `core/app.py`) are each a name-keyed
  registry resolved via `plugins.get`. The backend seam has four self-registering provider
  implementations (`anthropic`/`openai`/`claude-max`/`deepseek` in `sluice/backends/` — the names a
  config `primary_backend`/`fallback_backend` selects; `claude-max`/`deepseek` ALSO survive as
  deprecated `--backend` role aliases, which is the separate role concern below, not a second registry);
  the RENDERER seam has two self-registering
  production impls — `template` (the default: fills a user's Jinja2 template, or the packaged
  default, via WeasyPrint; `pip install -e '.[render]'`) and `script` (the external shell-out
  escape hatch) — selected by `cv.renderer`, so by-name selection between real implementations is
  already LIVE there. That seam has a second, OPTIONAL member, `precheck(cv_text) ->
  list[str]`: a renderer implements it only when the composed CV must satisfy a grammar of its own
  (`template` does, `script` does not, and the engine reaches it through `getattr` so an absent one
  gates nothing). Keeping it on the renderer is what stops one implementation's requirements binding
  the whole seam — measured, the engine imposing `template`'s grammar unconditionally reported
  `skipped-gate` under `cv.renderer: script` for a gate-clean CV. Being optional means it is the one
  seam member NOTHING types, so the engine checks the RETURN at the call site and raises naming the
  renderer: a `precheck` returning a bare `str` was otherwise spread one CHARACTER per violation into
  the retry prompt. `Sluice.compose_cv` resolves the renderer on a `--dry-run` too, purely so this
  hook runs — a dry run reporting no violations where a real run reports `skipped-gate` is a preview
  that false-greens the input it is previewing; a `RenderError` during that construction is caught,
  WARNED about by name, and the dry run proceeds. Store and fetcher have one production impl each (`vault`,
  `camofox`); the STORE seam has since grown the same OPTIONAL shape, `preflight() -> dict`, reached
  via `getattr` exactly like `precheck` and for the same reason (an implementation that cannot say is
  not one that is broken) — `job-sluice doctor` (see below) is the one caller, and `Vault.preflight`
  answers with FACTS (vault dir, baseline CV, Judging Profile, Experience Library counts, and --
  #133/#107 -- whether a candidate name and a contact block are declared), never
  verdicts, keeping classification in `core/doctor.py` where the backend rules already live. The
  selection is also exercised in tests — `tests/harness/` registers a fake fetcher
  (`browser.py`) and renderer (`renderer.py`) and resolves them through the same seam. The backend seam
  differs in shape, though: a role layer (auto/primary/fallback, in
  `Sluice.backend()`) sits above the provider lookup, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other three do. Route new implementations through those seams (a self-registering module) rather than
  around them.
- `.rulesync/` is canonical. `CLAUDE.md`, `AGENTS.md`, `.claude/` and the other AI-tool outputs are
  generated and gitignored; edit the source, then regenerate. **`.claude/settings.json` is the one
  deliberate exception, tracked rather than gitignored:** Claude Code's own `enabledPlugins` key
  (written by `/plugin marketplace add`, never by rulesync) lives in the same shared file rulesync's
  `hooks` feature writes, and tracking it is the only way a plugin enable reaches every worktree and
  contributor rather than staying one machine's private config. rulesync's hooks writer merges
  additively, so the two coexist; `.gitignore` carries the `/.claude/*` + `!/.claude/settings.json`
  shape this requires (a bare `/.claude/` would make the re-include inert), and
  `tests/test_no_leaked_files.py`'s `.claude/` prefix gate carves out exactly this one path by name
  -- every other path under `.claude/` (agents/, skills/, worktrees/, scheduled_tasks.lock) stays as
  forbidden as before. Tracking it also means a CI checkout always supplies a copy before generation
  runs, which defeats two things a purely-gitignored file relies on being absent for:
  `guard_rulesync_drift.py`'s exact hook count (rulesync silently SKIPS rewriting a file that
  already matches, dropping `hooks` from its summary rather than reporting it as zero) and
  `guard_emitted_outputs.py`'s structural check (a stale-but-valid copy would survive a genuinely
  broken generate run undetected). `scripts/reset_tracked_hooks.py` runs before `npm run rulesync`
  in CI and clears just the `hooks` key -- the one part rulesync owns -- restoring both guarantees
  without discarding `enabledPlugins`; its docstring has the measured chain end to end.
- **`README.md` and everything under `docs/`, plus `CONTRIBUTING.md`/`SECURITY.md`, are the
  opposite of the point above: tracked, hand-written, human-facing documentation, not generated
  outputs.** Edit them directly; there is no source-of-truth file to regenerate them from, the way
  there is for `CLAUDE.md`/`AGENTS.md`. `docs/USAGE.md` is the one exception with an automated
  check on it rather than a generator: `tests/test_docs_claims.py` walks the real `cli.py` parser
  and fails the build if a command it documents stops existing, or a real command goes
  undocumented -- the same generate-then-diff discipline as the `rulesync` CI job, applied to a
  hand-written file instead of a generated one. `docs/ARCHITECTURE.md` is the living technical
  description (module-by-module, the seams, the store contract); `docs/USAGE.md` is the CLI
  reference; `docs/CONFIGURATION.md` is the config-key reference; `docs/TROUBLESHOOTING.md` is
  fixes for specific failures. `docs/superpowers/specs/` and `.../plans/` are historical design
  documents once implemented -- not maintained, and the code wins on any disagreement.
