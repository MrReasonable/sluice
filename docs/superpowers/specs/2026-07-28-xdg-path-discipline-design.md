# Resolve paths per-system (XDG), not relative to the cwd (#80)

**Status:** design approved 2026-07-28. Three `/review-plan` rounds folded in — R1 (4 Critical, 11
High), R2 (0 Critical, 10 High), R3 (0 Critical, ~8 High). **The user's decisions have not changed
once; the mechanism under them has been replaced three times.** R3's convergence call: one reviewer
"converged, implement"; four "fold then implement"; none asked for a fourth round.

**Issue:** #80 · **Blocks:** #8 (`sluice init`) · **Related:** #81 (the `_merged/` blindness, out of scope)
**Sub-apps:** `core` (new `core/paths.py`, plus `config`, `seendb`, `health`, `vault`), `cli`,
`triage`, `cv`, `track`, `core/app` (composition root)

## What three review rounds corrected

The severity fell each round and the *character* shifted: the design was wrong, then the mechanism
was wrong, then the bookkeeping was wrong. The unifying cause never changed — **each fix was
specified in prose and not executed.** Everything below fell out in seconds when finally run.

**R1 — four Criticals, three of them in fixes the spec itself proposed.**

- **The enumeration claimed a method it did not use.** `grep os.environ.get` cannot see a path
  default declared as a config-dataclass field. 19 sites, not 7 — and the miss included
  `track-seen.db`, which loses more than the `seen.db` the spec called its worst case.
- **The `vault_dir` precedence fix made env beat an explicit constructor argument**, retargeting 150
  positional `Vault(str(tmp_path))` test constructions at a real vault, green in CI throughout.
- **"Warn and continue" on a relocated `seen.db` resurrects merged-away duplicates** → a second
  application under the user's name.
- **`delenv("SLUICE_CONFIG")` stops meaning "no config"** once an XDG default exists.

**R2 — ten Highs, every one inside R1's fixes.**

- **The `env or <config key> or resolver` chain short-circuited** on five non-empty config defaults,
  so the resolver was unreachable and the sweep would have moved four paths, not nine.
- **`vault_dir` would ship dead** — `load_config` names every field explicitly, so a dataclass field
  alone stays `""`. The identical dead-key defect this spec diagnoses at `triage/config.py:39,40`.
- **The example-config guard was vacuous** — a scan that excludes comments, against a key that ships
  commented.
- **M8 witnessed the wrong variable.** **The refusal was unscoped.** **`track-seen.db` had no
  constructor to refuse at.** **"Nine env rows" contradicted the spec's own table** (six).
- **The definition of done contradicted itself.**

**R3 — ~8 Highs, mostly inside R2's fixes, but bookkeeping rather than mechanism.**

- **`ingest run --dry-run` still hit the refusal.** `seen` goes to the engine on *both* branches
  (`app.py:453`) and `ingest/engine.py:42` reads it unconditionally — correctly, or a dry run would
  lie about dedup. The instruction to place the check "after the dry-run branch" named a position
  that does not exist.
- **Three of seven `seen_db` consumers were hand-listed.** Reads at `app.py:863,864,865,867,878,892,915`.
  **The hand-list-instead-of-enumerate failure, committed inside the fix for a bad enumeration.**
- **Both M9 rows were green by construction.** `$XDG_CONFIG_HOME | ~/.config` is a fallback chain, so
  the two pins mask each other. *The witness for R1's read-escape Critical has now been wrong three
  rounds running.*
- **M11 was an equivalent mutant** — the mandated unconditional `chmod` forces `0600` either way, and
  the red-first claim was umask-dependent (`0o644 & ~0o077 == 0o600`).
- **The `vault_dir` scan lost the presence assertion** its model pairs with, and `dossier_dir` — added
  by R2's own decision 7 — had no guard.
- **Two stale counts**: "seven warn sites" is five; the blanked-default row included `vault_dir`,
  which decision 5 excludes by design.
- **"Each sub-app loader names every field explicitly" is false.** Only `load_config` does; triage,
  cv, track and apply are `hasattr`-filtered `setattr` loops (`triage/config.py:63-67`).

## Implementation notes (2026-07-29) — what execution changed

The design above stands as approved; this records where building it diverged, and one
witness that was **still** wrong. Nothing here reopens a settled decision.

**Four deviations, each because the specified mechanism did not survive contact.**

1. **`load_triage_config`/`load_track_config` needed an INVERTED guard, not "resolve
   after the loop".** Both `return cfg` EARLY when there is no config file — which is
   exactly what a fresh install runs — so resolution placed after the loop never ran for
   the case it exists to serve. One exit instead. Witnessed: restoring the early return
   reddens only the no-config rows, while the configured rows stay green.
2. **`paths.config_file()` replaces five copies of the same `resolve` call.** "All five
   change together" is a property worth making structural rather than tested-for.
3. **The legacy literals are a TABLE in `paths.py`, not arguments at call sites.** Every
   moving path was `./<basename>`, and the DoD grep requires those literals to survive in
   that module alone; passing them in would have put a copy at each site.
4. **Track's refusal is `refuse_relocated_seen_db=False` on `load_track_config`, not a
   separate helper.** The helper would have to re-derive whether the value was CONFIGURED
   or merely resolved — a second resolution site, reimplementing the short-circuit that
   makes an explicit path immune instead of inheriting it. Same shape as `update_fields`'
   `require_status`. R2's actual requirement (doctor must not refuse) is met by the
   default.

Likewise `dossier_dir` resolves in `Sluice._dossier_dir`, not `load_config`, matching
`vault_dir`: a Config carries what the user CONFIGURED and the composition root decides
what that means — every test builds `Sluice(Config())` by hand and would otherwise hold a
blank.

**M9b was wrong a fourth time.** Dropping the `HOME` pin does NOT redden the
neutral-defaults loader assertions: those resolve `kind="config"`, where the still-pinned
`XDG_CONFIG_HOME` wins outright, so `HOME` is never consulted. Executed. What the `HOME`
pin actually protects is the XDG-**unset** branch, and the row that exercises it is
`test_path_sandbox.py::test_home_is_pinned_so_the_xdg_unset_branch_cannot_escape`, which
delenvs `XDG_CONFIG_HOME` itself — that row does redden, so the pin is witnessed, by a
different test than this table names. **M9a is correct as written** and reddens four rows,
both neutrality assertions among them.

**A security review of the token commit found a real defect in `_write_token`.** Creating
with a plain `open()` and tightening afterwards leaves a window in which the credential
exists world-readable. Creation now uses `os.open(..., 0o600)` **and** keeps the
unconditional `chmod` — neither replaces the other. The chmod normalises both arms, so a
mutant weakening the creation mode is equivalent; the new row neutralises `os.chmod` to
stay falsifiable.

## Problem

Enumerated by two greps over `sluice/` — `os.environ.get` and `"\./` — because either alone is blind
to half the set. Independently re-swept twice (including `expanduser`, `Path.home`, `tempfile`,
concatenated paths, function-signature defaults): **19 sites, complete.**

| # | what | env var | default today | site | disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | config file | `SLUICE_CONFIG` | none | 5 loaders | **move** |
| 2 | dedup state | `SEEN_DB` | `./seen.db` | `core/seendb.py:10` | **move** → state, *fatal* |
| 3 | track dedup state | *none* | `./track-seen.db` | `track/config.py:90` | **move** → state, *fatal* |
| 4 | source health | `SLUICE_HEALTH` | `./sluice_health.json` | `core/health.py:26` | **move** → state |
| 5 | disabled sources | `SLUICE_DISABLED` | `./sluice_disabled.json` | `cli.py:32` | **move** → state |
| 6 | triage audit | `TRIAGE_AUDIT` | `./triage-audit.jsonl` | `app.py:660`, `triage/config.py:40` | **move** → state |
| 7 | dossier cache | `DOSSIER_DIR` | `./dossiers` | `app.py:666,700`, `triage/config.py:39`, `cv/config.py:47` | **move** → cache, *one root key* |
| 8 | Google OAuth token | *none* | `./google_token.json` | `track/config.py:89` | **move** → state, mode `0600` |
| 9 | vault | `VAULT_DIR` | `./vault` | `core/vault.py:33,85` | stay; gains a config key |
| 10-16 | cv/apply artefacts + render script | *none* | `./cv-output`, `./cv-served` ×2, `./cv-home` ×2, `./cv-host`, `./cv-uploads`, `./scripts/cv_render_v2.py` | `cv/config.py:52,54,55,56`, `cv/render.py:18`, `apply/config.py:14,15,16` | stay |

**Eight paths move; seven stay** (nine grep lines — `cv-served` and `cv-home` appear twice each).
Row #16 (`cv-uploads`) is a path **inside the browser container**, not a host path.

### What cwd-dependence costs

- **`#3` is the worst case.** `app.py:863-865,878,892,915` derive the `.lastrun` watermark, the
  seen-message set, and the **#49 dead-letter store** from it. Run `track` from another directory and
  the entire backlog of un-acted-on proposals silently disappears.
- **`#2`** re-scrapes everything already seen. **`#8`** is a written OAuth credential.
- **`#7` is one directory shared by triage and cv today.** Moving one and not the other splits it and
  cv re-fetches every dossier over the live SSRF-guarded path. **A partial sweep is worse than none** —
  which is why decision 7 makes the sharing structural rather than tested for.

### Two pre-existing config defects

- **`triage/config.py:39,40` are dead keys** — declared, read by nothing (`app.py:660,666` read the
  env vars directly). Setting them in YAML changes nothing, silently.
- **The vault has no config key**, so it is settable only by an env var that does not survive a new
  shell — and #8's wizard would have nowhere to persist what it prompts for.

## The settled decisions

1. **XDG, on macOS too.**
2. **Resolution order is `env → config key → XDG`, through one function.**
3. **Never move a file.**
4. **But refuse to start for the two dedup stores** — scoped so it never fires on a read-only command.
5. **`vault_dir` becomes a config key; `./vault` stays its default; precedence lives in the factory.**
6. **Sweep state, cache and credentials only.**
7. **One root `Config.dossier_dir`** replaces the two sub-app keys.
8. **No `sluice paths` command, no `XDG_RUNTIME_DIR`.**
9. **The example-config placeholders are invented nonsense** (author confirmed).

### Why the two dedup stores refuse rather than warn

Warn-and-continue on `seen.db` produces a **duplicate application**, reported as ordinary activity:
resolved db absent → `SeenDb.load` swallows and returns an empty set (`seendb.py:23-24`) → every lead
reads as unseen → `Vault._resolve_path` builds candidates only under `leads_dir` (`vault.py:177`) and
never consults `leads_dir/_merged/` (`vault.py:674`) → every human-merged duplicate whose posting is
still live is **created** afresh with `status: new`. The `_merged/` blindness is **#81**, true today
and out of scope; the refusal removes the path this change would otherwise open.

## Design

### `sluice/core/paths.py` — one resolution function

```python
def resolve(*, env_var, config_value, kind, name, legacy=None, fatal=False) -> str
```

`kind` is `"config" | "state" | "cache"`, **validated against that closed set and raising on anything
else** — R3 removed the `kind=None` sentinel that draft 3 used for the vault, because a silent `None`
is the opposite of this repo's raise-and-list rule. **The vault does not go through `resolve`**; its
two-term `or` lives in `stores/vault.py:_make`.

XDG roots: `$XDG_CONFIG_HOME | ~/.config`, `$XDG_STATE_HOME | ~/.local/state`,
`$XDG_CACHE_HOME | ~/.cache`, each under `sluice/`.

**Purity, stated honestly.** `resolve` performs **no writes** — no `mkdir`, so `--dry-run` cannot
touch the disk. It *does* read: the environment, and (when `legacy` is given) `os.path.exists` on two
paths. Draft 2's "pure, no I/O" was already false of its own env reads. Directories are created by
the writer that needs them.

Read `XDG_*` per call, never at import: an import-time snapshot is unpatchable by tests.

**Legacy handling lives here**, which gives the legacy literals one home (and unblocks the DoD grep):

- `fatal=False` → warn once, naming both paths and the `mv`; use the resolved path.
- `fatal=True` → raise, same message.
- **Both are reached only when the path was actually resolved.** An explicit `env_var` or
  `config_value` short-circuits first, so a caller who names a path never triggers either. That
  property is what makes explicit `SeenDb(tmp_path/…)` constructions and `SEEN_DB`-exporting users
  immune by construction — **and it must have its own test row with a planted legacy file**, or it
  passes vacuously (R3).

### Config keys: blank the defaults, and know which loaders need naming

Two coupled changes, either alone silently inert:

- **Blank the defaults** — `TrackConfig.seen_db`, `TrackConfig.token_path`, `TriageConfig.audit_jsonl`,
  and the new `Config.vault_dir` / `Config.dossier_dir` all default to `""`. A non-empty default
  short-circuits `resolve` and the XDG location is never reached. *Verified by execution.*
- **Name the new fields in `load_config` only.** It is the sole loader that names fields explicitly
  (`core/config.py:153-164`). The four sub-app loaders are `hasattr`-filtered `setattr` loops, so a
  new field loads automatically there — **do not "fix" them**; `load_track_config`'s merged-denylist
  branch lives in that loop.

**Resolve inside the loaders, non-fatally.** `load_track_config` resolves `seen_db` and `token_path`
after its loop; `load_triage_config` resolves `audit_jsonl`. That is what feeds **all seven**
`tcfg.seen_db`/`token_path` consumers (`app.py:863,864,865,867,878,892,915`) without threading a value
through three methods — R3 found the hand-listed "three uses at 862-865" missed four, including
`_save_seen` at `:878` (whose `app.py:118` `makedirs` has no `or "."`, so `""` raises
`FileNotFoundError`) and `deadletter_path("")` at `:892`/`:915`, which would make `track confirm` and
`track dismiss` open a *different* #49 store from `track run` — reporting success while the real entry
re-surfaces forever. The **fatal** check stays out of the loaders (R2: `doctor()` calls
`load_track_config()` at `app.py:951`) and lives in the helper called from
`Sluice.track()`/`track_confirm()`/`track_dismiss()`.

A test pins that `""` never escapes a loader, which is what keeps `app.py:118` safe without changing it.

### One root `dossier_dir`

`Config.dossier_dir: str = ""`, resolved once and passed to **both** `dossier_cache(...)` calls
(`app.py:666`, `app.py:700`). `triage.dossier_dir` and `cv.dossier_dir` are retired with a raise —
the `load_cv_config`/`cv.baseline_rel` precedent (`cv/config.py:87-91`), same loader, same reason.
**The raise names the key and its replacement, never echoing the value** (R3): `baseline_rel` is
store-relative, but `dossier_dir` is a host path usually under a home directory, and
`core/config.py:130-133` already rules that way for `dossier_allow_hosts`.

**Breaking change**, deliberately: `cv.dossier_dir` is live today. It also fires in `doctor()`, as
`baseline_rel` already does. **`tests/harness/config.py:194` sets it** (and `:214` pins `DOSSIER_DIR`
to a *different* directory), so the harness is part of this migration — R3 found the migration list
named docs but not the harness, which would have broken every e2e and functional test.

### `vault_dir` — precedence in the factory

**`core/vault.py:85` is not touched.** An explicit constructor argument beating the environment is
correct for a constructor, and 150 tests depend on it. `stores/vault.py:_make` — the **only**
production `Vault(` call site — does the two-term `or`:

```python
return Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None, ...)
```

`None` falls through to `Vault.__init__`'s `./vault`. Update that file's "the store still resolves
its own location" comment: the store still owns its *default*; the factory supplies a configured
value, as it already does for `baseline_rel`.

### The config file, and the one behaviour change

Five loaders read `SLUICE_CONFIG` — `core/config.py:100`, `triage/config.py:58`, `cv/config.py:73`,
`apply/config.py:25`, `track/config.py:116`. All five change together; converting four gives a config
that half-loads with no error anywhere.

**The sweep's only behaviour change:** an unset `SLUICE_CONFIG` currently means *no config file*;
afterwards it means *read `~/.config/sluice/config.yaml` if it exists*.

### Parent directories and the token

**Already correct:** `cli.py:46`, `triage/audit.py:19`, `seendb.py:27`, `health.py:37`,
`deadletter.py:54`, `app.py:135`, `dossier.py:66`.

**The gap is the OAuth token.** `google_client.py:27` writes it with a bare `open(path, "w")` — no
`makedirs`, mode `0644`. Extract a stdlib `_write_token(path, data)` that creates the parent and
**unconditionally** `chmod`s `0600`: `os.open(..., 0o600)` does not change an existing file's mode, so
a refresh over an existing `0644` token would otherwise stay `0644`.

## Testing

### The two escapes

**Seven test files** pin config isolation by *absence* via `delenv("SLUICE_CONFIG")` (13 sites),
including both loader assertions in `tests/test_sluice_neutral_defaults.py` (lines 83, 257), whose own
comment says the delenv exists so the assertion cannot "silently read the developer's own
SLUICE_CONFIG and pass for the wrong reason". After this change `delenv` means *read
`~/.config/sluice/config.yaml`*.

The `tests/conftest.py` autouse fixture pins `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`,
**`HOME`** and **`VAULT_DIR`**. Both `XDG_CONFIG_HOME` and `HOME` are load-bearing and named as such
at the fixture and in that file's comment. `tests/conftest.py` autouse fixtures reach the e2e and
functional tiers — verified twice via `--setup-plan`.

**The e2e hermetic assertion cannot witness this** (`tests/harness/config.py:210-218` pins all seven
env vars, so that tier never resolves an XDG path). The guard is a dedicated unit test that unsets the
per-path env vars and asserts resolution lands **under** a pinned root — never planting a file
anywhere real.

### The permanent tests

- **`tests/test_paths.py`** — `resolve` per `kind`; an invalid `kind` raises and lists the valid ones;
  `XDG_*` set and unset; `~` expansion; **no writes** (directory still absent after resolving).
- **Six env-override rows** — #1, #2, #4, #5, #6, #7. *Not eight: #3 and #8 have no env var.*
- **Three config-key rows** — #3, #7 (root key), #8.
- **One row setting env *and* config on the same `resolve` call** (R3: otherwise M1's only falsifier
  is the vault row, which no longer goes through `resolve` at all).
- **Four blanked-default rows** — `TrackConfig.seen_db`, `TrackConfig.token_path`,
  `TriageConfig.audit_jsonl`, `Config.dossier_dir` reach an XDG location when nothing is configured.
  **Plus a separate `vault_dir` row asserting `./vault`** — it is blanked but deliberately has no XDG
  fallback, so folding it into the XDG rows is satisfiable only by relocating the vault (R3).
- **`""` never escapes a loader** — pins the `app.py:118` / `deadletter_path("")` hazard shut.
- **Legacy refusal** (#2, #3), each with a **planted legacy file**: raises on (legacy present, resolved
  absent, nothing explicit); does **not** raise when the env var is set; does **not** raise when the
  config value is set; does **not** fire on `ingest run --dry-run` or `--json`; both files
  byte-identical and present afterwards.
- **Legacy warning** (the other five), same planted-file discipline.
- **Loader completeness**: all five honour `resolve`. Discovery uses **`load*config`** —
  `load_*_config` does **not** match `load_config` (verified with `fnmatch`) — against a **pinned
  non-empty roster**.
- **Example-config path-key sweep**: over **every line containing a `*_dir:` key**, comments included,
  assert the value does not start with `/` or `~`; **plus a presence assertion per key**. This covers
  `vault_dir` *and* `dossier_dir` (R3: the previous row guarded one key and dropped the presence
  assertion its `lead_ttl_days` model pairs with, so an absent key passed vacuously). Executed against
  planted commented-absolute and commented-tilde values: both fail; placeholder and relative pass.
- **`Config.vault_dir` / `Config.dossier_dir` dataclass defaults are `""`**, labelled explicitly as
  neutrality guards — str-typed, so the #26 list sweep misses them by design.
- **Token**, with **`umask` pinned to `0o022`** (R3: under `umask 077` a plain `open` already yields
  `0600`, so an unpinned umask makes the row machine-dependent): fresh write is `0600` with its parent
  created; a refresh over an existing `0644` file ends `0600`.
- **Retired keys**: `triage.dossier_dir` / `cv.dossier_dir` set in YAML raise, naming key and
  replacement, **not** the value.

### Mutation witnesses

By node id, rest of the file green. **Move or delete, never add.** Run
`compileall --invalidation-mode checked-hash` first.

| # | mutant | must redden |
| --- | --- | --- |
| M1 | swap `env_var` and `config_value` in `resolve`'s chain | the env+config row |
| M2 | drop `dir or` from `Vault.__init__` | the explicit-argument row |
| M3 | delete `resolve`'s env-var term | the six env rows |
| M4 | delete `resolve`'s config-value term | the three config rows and the blanked-default rows |
| M5 | restore one non-empty config default | that path's blanked-default row |
| M6 | feed `app.py:700` a different directory from `app.py:666` | the shared-cache row |
| M7 | change `fatal=True` to `fatal=False` for `seen.db` | the refusal row |
| M8 | move the legacy check *above* the env/config short-circuit | the three "does not raise when explicit" rows |
| M9a | drop the `XDG_CONFIG_HOME` pin, **with ambient `XDG_CONFIG_HOME` aimed at a planted config** | the neutral-defaults loader assertions |
| M9b | drop the `HOME` pin, **under `env -u XDG_CONFIG_HOME`, with a planted config at ambient `$HOME/.config`** | the same assertions |
| M10 | narrow the loader roster to four (drop the root loader) | loader completeness |
| M11 | delete the `chmod` from `_write_token` | the fresh **and** refresh token rows |
| M12 | make the `chmod` fresh-only | the refresh row |
| M13 | delete the `makedirs` from `_write_token` | the parent-created row |
| M14 | pass `fatal=True` unconditionally from `Sluice.ingest` | the dry-run row |

**M9a and M9b each need their ambient variable aimed at a planted config.** `$XDG_CONFIG_HOME | ~/.config`
is a fallback chain, so the two pins mask each other: dropping the XDG pin still lands under the
pinned `HOME`, and dropping the `HOME` pin leaves the XDG pin winning outright. *Both halves were
green by construction in draft 3 — the third consecutive round in which this witness was wrong.*
Verified by execution.

M11/M12/M13 were re-derived by execution under a pinned umask; draft 3's M11 was an equivalent mutant
because the mandated unconditional `chmod` forces `0600` either way.

### Red-first

Each task starts from its failing test. Two are **not** naturally red-first and are labelled, not
presented as witnesses: the shared-dossier-cache row (passes on `main`; M6 is its falsifier) and the
`resolve` no-writes row. The token rows *are* red-first once `_write_token` exists, **given the pinned
umask**.

## Task breakdown

Ordered so nothing depends on a later task (R3 found tasks 4/6/8 inverted).

1. `core/paths.py` (`resolve`, XDG roots, `kind` validation, the legacy table) + `tests/test_paths.py`.
2. The `conftest.py` autouse fixture (XDG × 3, `HOME`, `VAULT_DIR`) + the
   `test_sluice_neutral_defaults.py` comment. **Before** any resolution change, so the suite is
   sandboxed before it can escape.
3. `Config.vault_dir` and `Config.dossier_dir`: dataclass fields **and** `load_config` lines.
4. Blank `TrackConfig.seen_db`/`token_path` and `TriageConfig.audit_jsonl`; resolve non-fatally inside
   `load_track_config`/`load_triage_config`; the `""`-never-escapes row. **Updates the pre-existing
   `tests/test_track_config.py:8,37`, which assert the old literals.**
5. The five config loaders honour `resolve` + the loader-completeness test.
6. The **five** warn-and-continue sites (#4, #5, #6, #7, #8). *Re-derived, not `8 − 2`: #1 has no
   legacy literal and #7 is now one resolution.*
7. Root `dossier_dir` threaded to both `dossier_cache` calls; retire the two sub-app keys with a
   raise; **update `tests/harness/config.py:194,214`**.
8. The two dedup stores, `fatal=True`: resolve in `Sluice.ingest` and pass
   `fatal=not (dry_run or json_sink)` to `SeenDb(path)` — there is no "after the dry-run branch"
   position, since `seen` reaches the engine on both branches (`app.py:453`) and `engine.py:42` reads
   it unconditionally. Track's fatal check is a helper called from
   `Sluice.track()`/`track_confirm()`/`track_dismiss()`, never `load_track_config()`.
9. `vault_dir` in `_make` + `sluice.yaml.example` + the path-key sweep.
10. `google_client._write_token` (parent + unconditional `0600`).
11. Docs.
12. Mutation witnesses M1–M14, by node id.

Commit types: `refactor(core):` for the mechanical moves; **`feat(core):`** for the loader change and
`dossier_dir`, both behaviour changes; `feat(track):` for the token permissions.

## Definition of done

```bash
python -m pytest                                   # green; count up by the new rows
ruff check sluice tests scripts                    # clean (ruff==0.15.21, the CI pin)
grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py
```

The last must list **exactly nine lines** — the seven "stay" paths, with `cv-served`
(`apply/config.py:14`, `cv/config.py:56`) and `cv-home` (`cv/config.py:54`, `cv/render.py:18`) twice
each. *Arithmetic executed: 19 lines today, 10 moving, 9 remaining.* The `grep -v` is load-bearing —
`core/paths.py` holds the legacy table, so those literals must survive there.

Plus: M1–M14 each observed red by node id, with the rest of the owning file green.

## Docs

- `sluice.yaml.example`: commented `vault_dir` and `dossier_dir`, no values.
- `docs/ARCHITECTURE.md`: the disposition table.
- `.rulesync/rules/CLAUDE.md`: only what the file tree does not show — every path goes through
  `paths.resolve` (except the vault, whose two-term `or` is in `_make`), the `env → config → XDG`
  order, nothing is auto-migrated, and the two dedup stores refuse rather than warn.
- `README.md`: the quickstart stops implying a project directory.

## Out of scope

- **`sluice init` (#8)**, including the `sluice.yaml.example` placeholder fix.
- **#81**, the `_merged/` blindness in `_resolve_path`.
- **Packaging.** This is the prerequisite, not the packaging.
- **Relocating the vault or the seven artefact paths.**
- **`app.py:118`'s missing `or "."`.** Reachable only if `""` escapes a loader, which task 4 pins
  shut. Left alone deliberately rather than carried as a drive-by.
