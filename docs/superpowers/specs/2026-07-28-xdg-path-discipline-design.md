# Resolve paths per-system (XDG), not relative to the cwd (#80)

**Status:** design approved 2026-07-28; **rewritten 2026-07-29 after `/review-plan`** (5 reviewers:
4 Critical, 11 High). Three of the four Criticals were in fixes this spec itself proposed. The
user's decisions are unchanged in intent; the mechanisms under them have all been replaced.

**Issue:** #80 — `refactor(core): resolve paths per-system (XDG), not relative to the cwd`
**Sub-apps:** `core` (a new `core/paths.py`, plus `config`, `seendb`, `health`, `vault`), `cli`,
`triage`, `cv`, `track`, `core/app` (the composition root)
**Blocks:** #8 (`sluice init`) — the wizard has to tell the user where things live.

## What this spec got wrong the first time

Kept here because it is the failure mode this repo keeps hitting, and because an implementer
reading the first draft's reasoning will be tempted to write it back.

- **The enumeration claimed a method it did not use.** Draft 1 said its path table was "enumerated
  rather than recalled", by `grep os.environ.get` over `sluice/`. That grep can only find paths that
  *already have an env var*. Every path default declared as a config-dataclass field is invisible to
  it. The real count is **19 sites, not 7** — and the ones it missed include the worst case in the
  codebase. Enumerate BOTH ends: the env reads *and* the `"./"` literals.
- **The `vault_dir` precedence fix was worse than the bug it fixed.** Draft 1 specified
  `os.environ.get("VAULT_DIR") or dir or _DEFAULT_VAULT` in `Vault.__init__`. That makes the
  environment beat an **explicit constructor argument**. 150 test sites pass a path positionally and
  nothing pins `VAULT_DIR` globally, so a developer with it exported would run `pytest` directly into
  their real vault — `normalize_all_statuses` rewriting every note's status, `merge_cluster`
  `os.replace`-ing real notes into `_merged/`. CI has no `VAULT_DIR`, so the suite stays green while
  doing it. **Precedence belongs in the factory, not the constructor.**
- **"Parent directories" was false at both sites it named.** `cli.py:46` and `triage/audit.py:19`
  already `makedirs`. The test it proposed would have passed against `main` before a line was
  written. There IS a real gap — at `track/google_client.py:27`, a site draft 1 never saw.
- **Two of six mutation rows were vacuous.** `M5` could not fail (`tests/harness/config.py:210-218`
  pins all seven env vars, so the e2e tier never resolves an XDG path at all). `M6`'s discovery glob
  `load_*_config` **does not match `load_config`** — verified with `fnmatch` — so it would have
  missed the root loader, the most important of the five, while reddening on a sub-app loader and
  reading as proof of exactly the completeness it lacked.

## Problem

Paths sluice resolves by default are relative to the current working directory. Enumerated by two
greps over `sluice/` — `os.environ.get` for the env-backed ones and `"\./` for the literal defaults —
because either alone is blind to half the set:

| # | what | env override | default today | site | disposition |
| --- | --- | --- | --- | --- | --- |
| 1 | config file | `SLUICE_CONFIG` | none | 5 loaders (below) | **move** (gains a default) |
| 2 | dedup state | `SEEN_DB` | `./seen.db` | `core/seendb.py:10` | **move** → state |
| 3 | track dedup state | *none* | `./track-seen.db` | `track/config.py:90` | **move** → state |
| 4 | source health | `SLUICE_HEALTH` | `./sluice_health.json` | `core/health.py:26` | **move** → state |
| 5 | disabled sources | `SLUICE_DISABLED` | `./sluice_disabled.json` | `cli.py:32` | **move** → state |
| 6 | triage audit | `TRIAGE_AUDIT` | `./triage-audit.jsonl` | `app.py:660`, `triage/config.py:40` | **move** → state |
| 7 | dossier cache (triage) | `DOSSIER_DIR` | `./dossiers` | `app.py:666`, `triage/config.py:39` | **move** → cache |
| 8 | dossier cache (cv) | *none* | `./dossiers` | `cv/config.py:47` | **move** → cache, *with #7* |
| 9 | Google OAuth token | *none* | `./google_token.json` | `track/config.py:89` | **move** → state, mode `0600` |
| 10 | vault | `VAULT_DIR` | `./vault` | `core/vault.py:33,85` | stay; gains a config key |
| 11 | cv output | *none* | `./cv-output` | `cv/config.py:55` | stay |
| 12 | cv served | *none* | `./cv-served` | `cv/config.py:56`, `apply/config.py:14` | stay |
| 13 | cv render home | *none* | `./cv-home` | `cv/config.py:54`, `cv/render.py:18` | stay |
| 14 | render script | *none* | `./scripts/cv_render_v2.py` | `cv/config.py:52` | stay |
| 15 | camofox upload dir | *none* | `./cv-host` | `apply/config.py:15` | stay |
| 16 | camofox cv dir (container-internal) | *none* | `./cv-uploads` | `apply/config.py:16` | stay |

**Nine paths move; seven stay.** The line is *state, cache and credentials move; artefacts and user
data stay*. A rendered CV landing in the directory you ran from is defensible, arguably desirable;
`#16` is a path *inside the browser container* and is not a host path at all.

### What cwd-dependence actually costs

Two of these lose work rather than merely scattering files, and **the worse one is `#3`, which draft
1 never saw**:

- **`track-seen.db` carries three things.** `core/app.py:863-865` derives the `.lastrun` watermark,
  the seen-message set, *and* the **#49 dead-letter store** from it. Run `track` from a different
  directory and the entire backlog of un-acted-on proposals — the durable record that exists so a
  human sees every ambiguous receipt — silently disappears, and the watermark resets.
- **`seen.db` (`#2`)** re-scrapes everything already seen. Draft 1 called this "the one that loses
  work"; it is the *lesser* of the two.
- **`#9` is a written OAuth credential** whose location depends on where you were standing.
- **`#7`/`#8` are the same directory today** (`./dossiers`), shared by triage and cv. Moving only the
  env-backed one **splits a cache the two sub-apps share**, so cv re-fetches every dossier over the
  live, SSRF-guarded network path. A partial sweep is worse here than none.

### Two defects in the config keys themselves

- **`triage/config.py:39,40` declare `dossier_dir` and `audit_jsonl`, and nothing reads them.**
  `core/app.py:660,666` read the env vars directly and never consult `tcfg`. They are dead keys: a
  user setting `triage.dossier_dir` in YAML today changes nothing, silently. (`cv/config.py:47` *is*
  read, at `app.py:700` — hence the split above.)
- **The vault has no config key**, so it is settable only by an env var, which does not survive a new
  shell — and #8's wizard would have nowhere to persist the answer it prompts for. (Draft 1 said the
  vault was "the only path with no config key". False: several have none. It is the one that
  *matters*, which is a different claim.)

## The settled decisions

1. **XDG, on macOS too.** Not `~/Library/Application Support`. Homebrew-installed CLIs
   overwhelmingly use XDG, and one rule across platforms beats matching a convention users of this
   tool mostly do not see.
2. **Per-path env vars keep winning**, and the two dead keys become live: the chain is
   `env or <config key> or paths.*()`. Draft 1's `env or paths.*()` would have entrenched the dead
   keys and falsified the documented layering at exactly those sites.
3. **Never move a file.** Auto-migration is rejected: moving user data as a side effect of which
   directory the process started in is the failure mode this sweep exists to remove.
4. **But refuse to start for the two dedup stores.** *(Revised after review.)* `seen.db` and
   `track-seen.db` **raise at construction** when a legacy file exists and the resolved one does not,
   naming both paths and the `mv`. Everything else warns and continues. Rationale below — a warning
   there is silently destructive, and hard rule 8 (fail loudly at construction) is the repo's own
   answer to that shape.
5. **`vault_dir` becomes a config key; `./vault` stays its default; precedence lives in the
   factory.** `core/vault.py:85` is **not** touched.
6. **Sweep state, cache and credentials only.** Artefact directories stay cwd-relative.
7. **No `sluice paths` command and no `XDG_RUNTIME_DIR`.** Nothing needs the former yet (`init`
   prints the resolved set in #8); nothing here is a socket or a lock.
8. **The example-config placeholders are invented nonsense, not anyone's real settings** (author
   confirmed). So they are an `abstain-default` problem only, not personal data, and commenting them
   out belongs to #8 as planned.

### Why the two dedup stores refuse rather than warn

Warn-and-continue on `seen.db` produces a **duplicate application under the user's name**, reported
as ordinary activity. The chain, verified against the code:

1. The resolved `seen.db` does not exist yet, and `SeenDb.load` swallows the failure and returns an
   empty set (`seendb.py:23-24`). Every scraped lead reads as unseen.
2. `Vault._resolve_path` builds its candidates only under `leads_dir` (`vault.py:177`). But
   `merge_cluster` archives merged-away losers into `leads_dir/_merged/` (`vault.py:674`) — a
   directory the candidate walk never consults.
3. So every duplicate a human resolved via `sluice leads dedupe`, whose posting is still live, is
   **created** afresh with `status: new`, and re-enters triage → cv → apply. If its surviving twin
   was already `applied`, that is a second application to the same job.
4. The run reports `created: N`, indistinguishable from ordinary new-lead activity.

This does not overturn decision 3 — nothing is moved either way. Sluice declines to proceed where
proceeding is destructive, which is what the rest of this codebase already does at construction time.

Note the underlying `_merged/` blindness is a **latent bug that exists today**, independent of this
sweep (any lost `seen.db` triggers it). Closing it means editing #5's candidate walk, which is
delicate and out of scope here; the refusal removes the path this change would otherwise open. Worth
its own issue.

## Design

### `sluice/core/paths.py` — the resolver

Pure, stdlib-only, no I/O and no `mkdir` at resolution time. A resolver that created directories
would make `--dry-run` write to disk, which is the property those flags exist to guarantee.

```
config_file()      -> $XDG_CONFIG_HOME/sluice/config.yaml | ~/.config/sluice/config.yaml
state_file(name)   -> $XDG_STATE_HOME/sluice/<name>       | ~/.local/state/sluice/<name>
cache_dir(name)    -> $XDG_CACHE_HOME/sluice/<name>       | ~/.cache/sluice/<name>
```

`state_file` serves `seen.db`, `track-seen.db`, `health.json`, `disabled.json`,
`triage-audit.jsonl`, `google_token.json`. `cache_dir` serves `dossiers`. Names lose their `sluice_`
prefix — they are already inside a `sluice/` directory, so `sluice_health.json` there would stutter.

The resolver does **not** read the per-path env vars. Each call site keeps its own, so the shape is
uniform and the resolver stays a pure function of the XDG environment:

```python
path = os.environ.get("<VAR>") or <config key, where one exists> or paths.state_file("<name>")
```

**One rule, four site shapes.** The chain lives at structurally different places — inside a class
`__init__` (`SeenDb`, `HealthStore`), in a module-level helper (`cli._disabled_path`), in the
composition root (`core/app.py`, for triage's audit and both dossier caches), and in a store factory
(`stores/vault.py`). That spread is pre-existing. The rule is stated once here and repeated as a
one-line comment at each site: **env, then config key, then resolver — in that order, at the point of
use, never at import.** Reading `XDG_*` per call rather than at import matters because an import-time
snapshot is unpatchable by tests; this matches `cli.py:31`'s existing comment on `_disabled_path`.

Legacy names each site compares against are the literal defaults in the table above, not derived.

### `vault_dir` — precedence in the factory

`Config` gains `vault_dir: str = ""`. **`core/vault.py:85` keeps `dir or os.environ.get("VAULT_DIR",
_DEFAULT_VAULT)` exactly as it is** — an explicit constructor argument beating the environment is
correct for a constructor, and 150 tests depend on it.

The layering happens one level up, in `stores/vault.py:_make`:

```python
return Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None, ...)
```

`None` falls through to the constructor's own default, so an unset key changes nothing. This is also
a much smaller inversion of that file's documented "config selects WHICH store, the store still
resolves its own location" comment than draft 1's — the store still owns its default; the factory
now supplies a configured value the way it already supplies `baseline_rel`. Update that comment.

### The config file, and the one behaviour change

**Five** loaders read `SLUICE_CONFIG` independently — `core/config.py:100`, `triage/config.py:58`,
`cv/config.py:73`, `apply/config.py:25`, `track/config.py:116` — each parsing its own top-level block
of the same file. All five must change together: converting four would give a config that
half-loads, with the root keys found, one sub-app's block silently absent, and no error anywhere.

This is the sweep's **only behaviour change**: an unset `SLUICE_CONFIG` currently means *no config
file at all*; afterwards it means *read `~/.config/sluice/config.yaml` if it exists*. That is the
point — a packaged install with nothing exported should find its config — but see the guard-test
consequence below, which is the more important half.

### Parent directories

Corrected from draft 1. Verified per site:

- **Already correct:** `cli.py:46`, `triage/audit.py:19`, `seendb.py:27`, `health.py:37`,
  `deadletter.py:54`, `app.py:135` (`_save_lastrun`), `dossier.py:66`.
- **Real gap:** `track/google_client.py:27` writes the OAuth token with a bare
  `open(self.token_path, "w")` and no `makedirs`. Needs one, plus mode `0600` — today the file lands
  beside the user in a directory they chose; afterwards sluice creates the directory, so the
  permissions become sluice's responsibility.
- **Latent:** `app.py:118` (`_save_seen`) calls `os.makedirs(os.path.dirname(path), exist_ok=True)`
  **without** the `or "."` fallback every other site carries. Harmless today (`./track-seen.db` has
  dirname `"."`) and harmless after the move (an absolute dirname), but it breaks on a bare relative
  filename. Add the fallback for consistency.

## Testing

### The two escapes, and why they need different guards

Draft 1 identified the **write** escape (a test leaving `XDG_STATE_HOME` unset writes into the
developer's real `~/.local/state/sluice/`). Review found a **read** escape that matters more:

**Seven test files pin config isolation by *absence*, via `delenv("SLUICE_CONFIG")`** — including both
loader assertions in `tests/test_sluice_neutral_defaults.py` (lines 83, 257), whose own comment says
the delenv exists so the assertion cannot "silently read the developer's own SLUICE_CONFIG and pass
for the wrong reason". After this change, `delenv` stops meaning *no config* and starts meaning *read
`~/.config/sluice/config.yaml`* — the very file #8's `init` writes. The flagship neutrality guard
would begin reading the maintainer's real preferences, pass, and prove nothing. CI's home has no such
file, so it is green in CI and wrong on the machine where it is load-bearing.

So `XDG_CONFIG_HOME` is **load-bearing for guard-test isolation**, not merely for tidiness, and it
must be named as such at the fixture and in `test_sluice_neutral_defaults.py`'s comment.

The autouse fixture in `tests/conftest.py` pins `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`,
**`HOME`** (otherwise the deliberately-XDG-unset rows fall back to the developer's real home) and
**`VAULT_DIR`** (nothing pins it today; see the Critical above). `tests/conftest.py`'s autouse
fixtures do reach the e2e and functional tiers — verified via `--setup-plan`, which shows the
existing session-scoped `_forbid_dns` set up for an e2e test — so one fixture covers every tier.

**The e2e hermetic assertion cannot witness any of this.** `tests/harness/config.py:210-218` pins all
seven per-path env vars, and env wins, so that tier never resolves an XDG path. It is also a local
helper in one file, not a shared assertion. Draft 1's M5 was therefore an equivalent mutant. The
guard is instead a dedicated unit test that **unsets the per-path env vars** and asserts resolution
lands under the pinned XDG roots.

### The permanent tests

- **`tests/test_paths.py`** — the resolver as a pure function: `XDG_*` set and unset, `~` expansion,
  and no filesystem side effects (assert the directory is still absent after resolving).
- **Per-path env override**, one row per moved site (nine), asserting the env var still wins.
- **Config-key layer**, for the sites that have one: `triage.dossier_dir` and `triage.audit_jsonl`
  set in YAML are honoured — these are the two dead keys, so these rows are new behaviour, not
  regression cover.
- **The shared dossier cache**: triage and cv resolve to the **same** directory when neither is
  configured. This is the anti-regression row for the split.
- **`vault_dir` precedence**: env beats YAML beats default, *and* an explicit `Vault(dir=...)` beats
  the environment. Both directions, written so that swapping operands in either chain reddens it.
- **Legacy refusal**: `SeenDb`/track construction raises when (legacy present, resolved absent), the
  message names both paths, and **both files are byte-identical and still present afterwards**.
- **Legacy warning** for the other seven: fires on (legacy present, resolved absent); silent when the
  resolved path exists; silent when the env var is set; nothing moved.
- **Loader completeness**: all five loaders honour the resolver. Discovery must use a pattern that
  matches `load_config` — `load*config` — because **`load_*_config` does not** (verified with
  `fnmatch`). Assert against a **pinned non-empty roster** so an empty glob cannot pass vacuously.
- **`vault_dir` ships un-filled in `sluice.yaml.example`**: a *text* scan, modelled on the existing
  `test_example_config_ships_lead_ttl_days_off` (`test_sluice_neutral_defaults.py:301`).
  `test_config_example.py` yaml-parses the file, so a commented key is invisible to it — and
  `vault_dir` is the field most likely to be filled in with an absolute personal path, in the file
  the quickstart tells people to copy.
- **Token permissions**: the written token file is mode `0600` and its parent is created.

### Mutation witnesses

Each runs **by node id**, with the rest of the file confirmed green, so a pre-existing test is not
what catches it. Mutate by **moving or deleting, never adding**. Run
`compileall --invalidation-mode checked-hash` first. Draft 1's M5/M6 are gone — both were vacuous.

| # | mutant (smallest edit that breaks the claim) | must redden |
| --- | --- | --- |
| M1 | swap the operands in `_make`'s `env or config.vault_dir` | the YAML-vs-env precedence row |
| M2 | drop `dir or` from `Vault.__init__` | the explicit-argument row (the Critical) |
| M3 | delete one site's `os.environ.get("<VAR>") or` prefix | that site's override row — run for **all nine**, not one |
| M4 | delete the `<config key> or` term at a dead-key site | the config-key layer row |
| M5 | point cv's dossier resolution at a different name | the shared-cache row |
| M6 | replace the `seen.db` construction-time raise with a warn | the refusal row |
| M7 | delete the `and not os.path.exists(resolved)` conjunct | the "silent when resolved exists" row |
| M8 | remove `XDG_CONFIG_HOME` from the autouse fixture, with a config file planted at the resolved path | the neutral-defaults loader assertions |
| M9 | narrow the loader roster to four (drop the root loader) | the loader-completeness row |

M3 is listed as nine runs deliberately: draft 1 witnessed one site and generalised, which is the
hand-listing failure this repo has hit repeatedly. M8 is the read-escape witness and replaces draft
1's untestable M5.

### Red-first

Each task starts from its failing test. The two that are *not* naturally red-first, and must be
checked against `main` before being counted as coverage: the token-permissions row (no token-writing
test exists today) and the shared-dossier-cache row (passes on `main`, since both default to
`./dossiers` — it is a **regression guard**, and must be labelled as one rather than presented as a
new witness).

## Task breakdown

1. `core/paths.py` + `tests/test_paths.py`. No call sites yet.
2. The `conftest.py` autouse fixture (XDG × 3, `HOME`, `VAULT_DIR`) + the `test_sluice_neutral_defaults.py`
   comment update. **Before** any resolution changes, so the suite is sandboxed before it can escape.
3. The five config loaders + the loader-completeness test.
4. The seven warn-and-continue sites, including the two dead-key revivals and the shared dossier cache.
5. The two dedup stores: construction-time refusal.
6. `vault_dir`: config field, `stores/vault.py:_make`, `sluice.yaml.example`, the text-scan guard.
7. `google_client.py` parent + `0600`; the `_save_seen` `or "."` fallback.
8. Docs (below).
9. Mutation witnesses M1–M9, by node id.

Commit type: **`refactor(core):`** for the mechanical moves, but the loader change is a stated
behaviour change and takes **`feat(core):`** with the behaviour named in the body. Do not label the
whole branch `refactor` — one of these commits changes what an unset env var means.

## Definition of done

```bash
python -m pytest                            # all green, count up by the new rows
ruff check sluice tests scripts             # clean (ruff==0.15.21, the CI pin)
grep -rn '"\./' sluice --include='*.py'     # only the seven "stay" sites remain
```

Plus: M1–M9 each run by node id and observed red, with the rest of the owning file green.

## Docs

- `sluice.yaml.example` gains a commented `vault_dir` with no value, and comments for the two
  revived triage keys.
- `docs/ARCHITECTURE.md` gets the disposition table.
- `.rulesync/rules/CLAUDE.md` gets only what the file tree does not show: the
  `env → config key → resolver` order, that nothing is auto-migrated, and that the two dedup stores
  refuse rather than warn. Not the table — that file states its own contract that per-module detail
  lives in `ARCHITECTURE.md`.
- `README.md`'s quickstart stops implying a project directory.

## Out of scope

- **`sluice init` (#8)**, including the `sluice.yaml.example` placeholder fix.
- **The `_merged/` blindness in `_resolve_path`** — a real latent bug, but it edits #5's candidate
  walk and wants its own issue and review.
- **Packaging** (`brew`/`apt`/`yum` formulae). This is the prerequisite, not the packaging.
- **Relocating the vault, or the seven artefact paths.**
