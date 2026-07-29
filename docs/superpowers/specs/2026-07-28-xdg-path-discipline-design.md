# Resolve paths per-system (XDG), not relative to the cwd (#80)

**Status:** design approved 2026-07-28. Rewritten after `/review-plan` round 1 (4 Critical, 11 High);
**revised again after round 2 (0 Critical, 10 High, 4 Medium)**. Round 2 found no defect in the
original code and no defect in the user's decisions — **every High was inside mechanism round 1's
fixes had added.** The decisions are unchanged; the mechanism under them has been replaced twice.

**Issue:** #80 · **Blocks:** #8 (`sluice init`) · **Related:** #81 (the `_merged/` blindness, out of scope)
**Sub-apps:** `core` (new `core/paths.py`, plus `config`, `seendb`, `health`, `vault`), `cli`,
`triage`, `cv`, `track`, `core/app` (composition root)

## What two review rounds corrected

Kept because an implementer reading the earlier reasoning will be tempted to write it back, and
because the *pattern* is this repo's recurring one.

**Round 1 — four Criticals, three of them in fixes the spec itself proposed:**

- **The enumeration claimed a method it did not use.** `grep os.environ.get` cannot find a path
  default declared as a config-dataclass field. 19 sites, not 7 — and the miss included
  `track-seen.db`, which carries the `.lastrun` watermark and the #49 dead-letter store, so it loses
  more than the `seen.db` the spec had called its worst case. **Enumerate both ends: env reads *and*
  `"./"` literals.**
- **The `vault_dir` precedence fix made env beat an explicit constructor argument**, retargeting 150
  positional `Vault(str(tmp_path))` test constructions at a real vault, green in CI throughout.
- **"Warn and continue" on a relocated `seen.db` resurrects merged-away duplicates** as `status: new`
  → a second application under the user's name.
- **`delenv("SLUICE_CONFIG")` stops meaning "no config"** once an XDG default exists, pointing the
  flagship neutrality guard at the developer's own preferences.

**Round 2 — ten Highs, all in round 1's fixes.** The unifying cause: each fix was specified as *what
the mechanism should be* and never executed. Every one below falls out in seconds when run.

- **The `env or <config key> or resolver` chain short-circuits.** Five of the nine moving paths have
  **non-empty** config defaults (`"./track-seen.db"`, `"./dossiers"`, …), so the resolver is
  unreachable and the sweep would have moved four paths, not nine.
- **`vault_dir` would ship dead.** `load_config` names every field explicitly — no splat, no loop
  (`core/config.py:153-164`; the repo's own comment at `tests/test_sluice_neutral_defaults.py:254`
  says so) — so a dataclass field alone leaves it `""` forever. **The identical dead-key defect this
  spec diagnoses at `triage/config.py:39,40`, reproduced inside its fix.**
- **The example-config guard was vacuous.** Modelled on a scan that excludes comment lines, against a
  key that ships commented: `all()` over an empty list. Executed — a commented absolute personal path
  passed identically to a placeholder.
- **M8 witnessed the wrong thing.** The fixture pins `XDG_CONFIG_HOME` *and* `HOME`; varying only the
  first proves XDG-over-`~` precedence, not closure. On macOS — the platform decision 1 singles out —
  `XDG_CONFIG_HOME` is conventionally unset, so `HOME` is the only real pin.
- **The refusal was unscoped.** It would fire on `ingest run --dry-run` (`SeenDb()` is built at
  `core/app.py:447`, *before* the branch at `:449`), break explicit `SeenDb(tmp_path/…)` constructions
  for anyone with a stray `./seen.db`, and — in a config loader — kill `sluice doctor`, which calls
  `load_track_config()` at `core/app.py:951`.
- **`track-seen.db` has no constructor to refuse at** (three reviewers). It is a `TrackConfig` field
  consumed by free functions, so task 5 had no site for the worse of its two subjects.
- **"One row per moved site (nine)" was arithmetic that contradicted the spec's own table.** Only
  **six** moved paths have an env var; rows #3, #8 and #9 are marked `none` three rows above the
  claim. The generalise-from-a-subset failure, inside the row written to prevent it.
- **The definition of done contradicted itself** — one section required keeping legacy `"./"`
  literals for comparison, another grepped to prove they were gone.

## Problem

Enumerated by two greps over `sluice/` — `os.environ.get` and `"\./` — because either alone is blind
to half the set. Independently re-swept in round 2 (including `expanduser`, `Path.home`, `tempfile`,
concatenated paths and function-signature defaults): **19 sites, complete.**

| # | what | env var | default today | site | disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | config file | `SLUICE_CONFIG` | none | 5 loaders | **move** |
| 2 | dedup state | `SEEN_DB` | `./seen.db` | `core/seendb.py:10` | **move** → state |
| 3 | track dedup state | *none* | `./track-seen.db` | `track/config.py:90` | **move** → state |
| 4 | source health | `SLUICE_HEALTH` | `./sluice_health.json` | `core/health.py:26` | **move** → state |
| 5 | disabled sources | `SLUICE_DISABLED` | `./sluice_disabled.json` | `cli.py:32` | **move** → state |
| 6 | triage audit | `TRIAGE_AUDIT` | `./triage-audit.jsonl` | `app.py:660`, `triage/config.py:40` | **move** → state |
| 7 | dossier cache (triage) | `DOSSIER_DIR` | `./dossiers` | `app.py:666`, `triage/config.py:39` | **move** → cache |
| 8 | dossier cache (cv) | *none* | `./dossiers` | `cv/config.py:47` | **move** → cache, *same dir as #7* |
| 9 | Google OAuth token | *none* | `./google_token.json` | `track/config.py:89` | **move** → state, mode `0600` |
| 10 | vault | `VAULT_DIR` | `./vault` | `core/vault.py:33,85` | stay; gains a config key |
| 11-16 | cv/apply artefacts + render script | *none* | `./cv-output`, `./cv-served`, `./cv-home`, `./cv-host`, `./cv-uploads`, `./scripts/cv_render_v2.py` | `cv/config.py:52,54,55,56`, `cv/render.py:18`, `apply/config.py:14,15,16` | stay |

**Nine paths move (six env-backed, three config-only); seven stay.** The line is *state, cache and
credentials move; artefacts and user data stay*. Row #16 is a path **inside the browser container**,
not a host path at all.

### What cwd-dependence costs

- **`#3` is the worst case.** `core/app.py:862-865` derives three things from it — the `.lastrun`
  watermark, the seen-message set, and the **#49 dead-letter store**. Run `track` from another
  directory and the entire backlog of un-acted-on proposals silently disappears.
- **`#2`** re-scrapes everything already seen.
- **`#9`** is a written OAuth credential.
- **`#7`/`#8` are the same directory today.** Moving one and not the other splits a cache the two
  sub-apps share, so cv re-fetches every dossier over the live SSRF-guarded path. **A partial sweep is
  worse here than none.**

### Two pre-existing config defects

- **`triage/config.py:39,40` are dead keys** — declared, and read by nothing (`app.py:660,666` read
  the env vars directly). Setting them in YAML changes nothing, silently. (`cv/config.py:47` *is*
  read, at `app.py:700`.)
- **The vault has no config key**, so it is settable only by an env var that does not survive a new
  shell — and #8's wizard would have nowhere to persist what it prompts for.

## The settled decisions

1. **XDG, on macOS too.** One rule across platforms beats matching a convention this tool's users
   mostly do not see.
2. **Resolution order is `env → config key → XDG`, through one function.** *(Revised twice.)*
3. **Never move a file.**
4. **But refuse to start for the two dedup stores** — `seen.db` and `track-seen.db` raise when a
   legacy file exists and the resolved one does not. **Scoped** (below): only on a *resolved* path, at
   named sites, never on a read-only command.
5. **`vault_dir` becomes a config key; `./vault` stays its default; precedence lives in the factory**
   (`stores/vault.py:_make`), not in `Vault.__init__`.
6. **Sweep state, cache and credentials only.**
7. **One root `Config.dossier_dir`** replaces the two sub-app keys. *(New in round 2.)*
8. **No `sluice paths` command, no `XDG_RUNTIME_DIR`.**
9. **The example-config placeholders are invented nonsense** (author confirmed) — an
   `abstain-default` matter for #8, not personal data.

### Why the two dedup stores refuse rather than warn

Warn-and-continue on `seen.db` produces a **duplicate application**, reported as ordinary activity:
the resolved db is absent → `SeenDb.load` swallows and returns an empty set (`seendb.py:23-24`) →
every lead reads as unseen → `Vault._resolve_path` builds candidates only under `leads_dir`
(`vault.py:177`) and never consults `leads_dir/_merged/` (`vault.py:674`) → every human-merged
duplicate whose posting is still live is **created** afresh with `status: new` and re-enters
triage → cv → apply. The underlying `_merged/` blindness is **issue #81**, true today and out of
scope; the refusal removes the path this change would otherwise open.

## Design

### `sluice/core/paths.py` — one resolution function

Round 2's architect finding: stating the order in prose and repeating it at four structurally
different call sites documents a smear rather than removing it, and it is what forced M3 to nine runs.
**One entry point instead:**

```python
def resolve(*, env_var, config_value, kind, name, legacy=None, fatal=False) -> str:
    """The ONE resolution path. Order: env var, then config value, then the XDG
    location for `kind` ("config" | "state" | "cache")."""
```

XDG roots: `$XDG_CONFIG_HOME | ~/.config`, `$XDG_STATE_HOME | ~/.local/state`,
`$XDG_CACHE_HOME | ~/.cache`, each under `sluice/`.

**Purity, stated honestly.** `resolve` performs **no writes** — no `mkdir`, so `--dry-run` cannot
touch the disk. It *does* read: the environment, and (when `legacy` is given) `os.path.exists` on two
paths. Draft 2 called this "pure, no I/O", which was already false of its own env reads. Directories
are created by the writer that needs them.

Reading `XDG_*` per call, never at import: an import-time snapshot is unpatchable by tests. This
matches `cli.py:31`'s existing comment on `_disabled_path`.

**Legacy handling lives here too**, which is what lets the legacy literals live in one table rather
than scattered across nine sites (and unblocks the definition-of-done grep):

- `fatal=False` → warn once, naming both paths and the `mv`; use the resolved path.
- `fatal=True` → raise, same message. Only rows #2 and #3.
- **Both are reached only when the path was actually resolved.** An explicit `env_var` or
  `config_value` short-circuits before the legacy check, so a caller who names a path never triggers
  it. That single property fixes round 2's unscoped-refusal finding at its root: explicit
  `SeenDb(tmp_path/…)` constructions and `SEEN_DB`-exporting users are both immune by construction.

### Config keys must be blanked *and* loaded

Two coupled changes, either alone silently inert:

- **Blank the defaults.** `TrackConfig.seen_db`, `TrackConfig.token_path`, `TriageConfig.audit_jsonl`
  and the new `Config.vault_dir`/`Config.dossier_dir` all default to `""`. A non-empty default
  short-circuits `resolve` and the XDG location is never reached. *Verified by execution.*
- **Name them in the loaders.** `load_config` (and each sub-app loader) names every field explicitly,
  so a new dataclass field that is not added to the loader's constructor call is `""` forever.

`TrackConfig.seen_db` blanked means the three uses at `core/app.py:862-865` must all be fed the
*resolved* value — `deadletter_path("")` would otherwise orphan the #49 backlog while `_load_seen`
swallows the `OSError` (`app.py:110-114`). Resolve once, at the top of the track flow, and thread it.

### One root `dossier_dir`

`Config.dossier_dir: str = ""`, resolved once and passed to **both** `dossier_cache(...)` calls
(`app.py:666` and `app.py:700`). `triage.dossier_dir` and `cv.dossier_dir` are retired with a loud
raise if set — the precedent `load_cv_config` already sets for the relocated `cv.baseline_rel`.

This *shrinks* the design: it makes the shared cache structural rather than something a test checks,
and removes three levers over one directory. **Breaking change:** `cv.dossier_dir` is live today, so
anyone who set it gets a startup error naming the replacement. That is the intent — a silent
migration here re-opens the split.

### `vault_dir` — precedence in the factory

**`core/vault.py:85` is not touched.** An explicit constructor argument beating the environment is
correct for a constructor, and 150 tests depend on it. The layering happens in `stores/vault.py:_make`
— the **only** production `Vault(` call site (verified by grep), so nothing bypasses it:

```python
return Vault(paths.resolve(env_var="VAULT_DIR", config_value=config.vault_dir,
                           kind=None, name=None) or None, ...)
```

`kind=None` means "no XDG fallback": the vault keeps `Vault.__init__`'s `./vault`. Update that file's
"the store still resolves its own location" comment — the store still owns its *default*; the factory
now supplies a configured value, as it already does for `baseline_rel`.

### The config file, and the one behaviour change

Five loaders read `SLUICE_CONFIG` independently — `core/config.py:100`, `triage/config.py:58`,
`cv/config.py:73`, `apply/config.py:25`, `track/config.py:116`. All five change together; converting
four gives a config that half-loads with no error anywhere.

**The sweep's only behaviour change:** an unset `SLUICE_CONFIG` currently means *no config file*;
afterwards it means *read `~/.config/sluice/config.yaml` if it exists*. Guard-test consequences below.

### Parent directories and the token

Verified per site. **Already correct:** `cli.py:46`, `triage/audit.py:19`, `seendb.py:27`,
`health.py:37`, `deadletter.py:54`, `app.py:135`, `dossier.py:66`.

**The real gap is the OAuth token.** `google_client.py:27` writes it with a bare
`open(self.token_path, "w")` — no `makedirs`, mode `0644`. Extract a stdlib `_write_token(path, data)`
helper (the current write is behind the Google libs and a network refresh, so it is otherwise
unreachable hermetically) that creates the parent and writes `0600`. **`os.open(..., 0o600)` does not
change the mode of an existing file**, so a refresh over an existing `0644` token must `os.chmod`
explicitly — a fresh-write-only assertion passes while a real `0644` token stays `0644`.

## Testing

### The two escapes

Round 1 identified the **write** escape (a test with `XDG_STATE_HOME` unset writing into a real
`~/.local/state/sluice/`). The **read** escape matters more: **seven test files** pin config isolation
by *absence* via `delenv("SLUICE_CONFIG")` (13 sites), including both loader assertions in
`tests/test_sluice_neutral_defaults.py` (lines 83, 257), whose own comment says the delenv exists so
the assertion cannot "silently read the developer's own SLUICE_CONFIG and pass for the wrong reason".

So the autouse fixture in `tests/conftest.py` pins `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
`XDG_CACHE_HOME`, **`HOME`** and **`VAULT_DIR`**, and both `XDG_CONFIG_HOME` and `HOME` are named
load-bearing at the fixture and in that file's comment. `tests/conftest.py`'s autouse fixtures do
reach the e2e and functional tiers — verified twice via `--setup-plan`.

**The e2e hermetic assertion cannot witness any of this** (`tests/harness/config.py:210-218` pins all
seven env vars, so that tier never resolves an XDG path; it is also a local helper in one file). The
guard is a dedicated unit test that unsets the per-path env vars and asserts resolution lands under
the pinned roots. Assert *under a pinned root* rather than planting a file anywhere real.

### The permanent tests

- **`tests/test_paths.py`** — `resolve` as a function: each `kind`, `XDG_*` set and unset, `~`
  expansion, and **no writes** (assert the directory is still absent after resolving).
- **Six env-override rows** — one per env-backed moved path (#1, #2, #4, #5, #6, #7). *Not nine.*
- **Three config-key rows** — #3, #8, #9, which have no env var and resolve via a config value.
- **Blanked-default rows**: each of the five blanked defaults reaches the XDG location when nothing
  is configured. Without these the short-circuit regression returns silently.
- **The shared dossier cache**: both `dossier_cache` calls receive the same directory. Structural
  after decision 7, so this is a **regression guard, not a new witness** — it passes on `main`.
- **`vault_dir` precedence**: env beats YAML beats default, **and** an explicit `Vault(dir=…)` beats
  the environment. Both directions.
- **Legacy refusal** (#2, #3): raises when (legacy present, resolved absent, nothing explicit); does
  **not** raise when the env var or config value is set; does **not** fire on `ingest run --dry-run`;
  both files byte-identical and present afterwards.
- **Legacy warning** (the other seven): warns once; silent when the resolved path exists; silent when
  explicit; nothing moved.
- **Loader completeness**: all five loaders honour `resolve`. Discovery uses **`load*config`** —
  `load_*_config` does **not** match `load_config` (verified with `fnmatch`) — against a **pinned
  non-empty roster** so an empty glob cannot pass vacuously.
- **`vault_dir` ships un-filled in `sluice.yaml.example`**: scan **every line containing
  `vault_dir:`, comments included**, asserting the value does not start with `/` or `~` — the shape
  used for `baseline_rel` at `test_sluice_neutral_defaults.py:73`. **Not** the
  `startswith("<key>:")` shape of `test_example_config_ships_lead_ttl_days_off`, which excludes
  comments and is therefore vacuous for a key that ships commented. *Executed both shapes against a
  planted commented absolute path: the specified one fails, the modelled one passes.*
- **`Config.vault_dir` and `Config.dossier_dir` dataclass defaults are `""`**, labelled explicitly as
  neutrality guards — str-typed, so the #26 list sweep misses them by design, and an unlabelled
  incidental assertion is how the `baseline_rel` guard was lost once already.
- **Token**: fresh write is `0600` with its parent created; **and a refresh over an existing `0644`
  file ends `0600`**.
- **Retired keys**: `triage.dossier_dir` / `cv.dossier_dir` set in YAML raise, naming the replacement.

### Mutation witnesses

Each runs **by node id**, with the rest of the file confirmed green. Mutate by **moving or deleting,
never adding**. Run `compileall --invalidation-mode checked-hash` first.

| # | mutant | must redden |
| --- | --- | --- |
| M1 | swap `env_var` and `config_value` in `resolve`'s chain | the precedence rows |
| M2 | drop `dir or` from `Vault.__init__` | the explicit-argument row |
| M3 | delete `resolve`'s env-var term | the six env rows (one mutant now, not nine runs) |
| M4 | delete `resolve`'s config-value term | the three config rows **and** the blanked-default rows |
| M5 | restore one non-empty config default (e.g. `"./track-seen.db"`) | that path's blanked-default row |
| M6 | feed `app.py:700` a different directory from `app.py:666` | the shared-cache row |
| M7 | change `fatal=True` to `fatal=False` for `seen.db` | the refusal row |
| M8 | move the legacy check *above* the env/config short-circuit | the "does not raise when explicit" row |
| M9a | drop the `XDG_CONFIG_HOME` pin, run under `env -u XDG_CONFIG_HOME` | the neutral-defaults loader assertions |
| M9b | drop the `HOME` pin | the same assertions — this is the macOS-relevant half |
| M10 | narrow the loader roster to four (drop the root loader) | loader completeness |
| M11 | write the token `0644` on fresh create | the fresh-write row |
| M12 | drop the `chmod` on refresh | the refresh-over-`0644` row |

M9a **must** run with `env -u XDG_CONFIG_HOME`: on a machine where it is exported, the mutant reads
the ambient value, stays green, and reads as "the pin is inert" — re-opening round 1's Critical
through its own witness. M9b is the half that matters on macOS.

M5 is the round-2 regression witness: without it, restoring a non-empty default silently reverts the
sweep for that path.

### Red-first

Each task starts from its failing test. Two are **not** naturally red-first and must be labelled, not
presented as witnesses: the shared-dossier-cache row (passes on `main`; M6 is its falsifier) and the
`resolve` purity row. The token rows *are* red-first once `_write_token` exists — `open(…, "w")`
yields `0644` today.

## Task breakdown

1. `core/paths.py` (`resolve`, the XDG roots, the legacy table) + `tests/test_paths.py`. No call sites.
2. The `conftest.py` autouse fixture (XDG × 3, `HOME`, `VAULT_DIR`) + the
   `test_sluice_neutral_defaults.py` comment. **Before** any resolution change, so the suite is
   sandboxed before it can escape.
3. The five config loaders + the loader-completeness test.
4. Blank the five config defaults, name them in their loaders, and add the blanked-default rows.
5. The seven warn-and-continue sites.
6. Root `Config.dossier_dir`; retire the two sub-app keys with a raise.
7. The two dedup stores: `fatal=True`, with the track site being a helper called from
   `Sluice.track()`/`track_confirm()`/`track_dismiss()` — **not** `load_track_config()`, which
   `doctor()` calls at `app.py:951`. Verify the ingest site sits *after* the dry-run branch.
8. `vault_dir`: config field, `_make`, `sluice.yaml.example`, the corrected text-scan guard.
9. `google_client._write_token` (parent + `0600` + chmod-on-refresh).
10. Docs.
11. Mutation witnesses M1–M12, by node id.

Commit types: `refactor(core):` for the mechanical moves; **`feat(core):`** for the loader change and
for `dossier_dir`, both of which change behaviour; `feat(track):` for the token permissions.

## Definition of done

```bash
python -m pytest                                   # green; count up by the new rows
ruff check sluice tests scripts                    # clean (ruff==0.15.21, the CI pin)
grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py
```

The last command must list **only** the seven "stay" paths (nine lines — `cv-served` and `cv-home`
each appear twice). The `grep -v` is load-bearing: `core/paths.py` holds the legacy table, so the
legacy literals must survive there. Round 2 caught the earlier version of this gate demanding the
deletion of the very literals another section required.

Plus: M1–M12 each observed red by node id, with the rest of the owning file green.

## Docs

- `sluice.yaml.example`: commented `vault_dir` and `dossier_dir`, no values.
- `docs/ARCHITECTURE.md`: the disposition table.
- `.rulesync/rules/CLAUDE.md`: only what the file tree does not show — that every path goes through
  `paths.resolve`, the `env → config → XDG` order, that nothing is auto-migrated, and that the two
  dedup stores refuse rather than warn.
- `README.md`: the quickstart stops implying a project directory.

## Out of scope

- **`sluice init` (#8)**, including the `sluice.yaml.example` placeholder fix.
- **#81**, the `_merged/` blindness in `_resolve_path`.
- **Packaging.** This is the prerequisite, not the packaging.
- **Relocating the vault or the seven artefact paths.**
- **`app.py:118`'s missing `or "."`** — cosmetic, unreachable before and after the move. Dropped in
  round 2 rather than carried as a drive-by.
