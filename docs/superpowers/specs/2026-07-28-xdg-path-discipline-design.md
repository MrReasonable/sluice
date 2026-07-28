# Resolve paths per-system (XDG), not relative to the cwd (#80)

**Status:** design approved 2026-07-28 (brainstormed with the user; five decisions settled below).

**Issue:** #80 — `refactor(core): resolve paths per-system (XDG), not relative to the cwd`
**Sub-apps:** `core` (a new `core/paths.py`, plus `config`, `seendb`, `health`, `vault`), `cli`,
`core/app` (triage's audit + dossier cache)
**Blocks:** #8 (`sluice init`) — the wizard has to tell the user where things live, so it is built
against settled paths rather than being written twice.

## Problem

Every path sluice resolves by default is relative to the current working directory. Enumerated by
grepping `os.environ.get` across `sluice/` rather than recalled:

| what | env override | default today | site | kind |
| --- | --- | --- | --- | --- |
| config file | `SLUICE_CONFIG` | none — unset means no config at all | `core/config.py:100` (+ the four sub-app loaders) | config |
| vault | `VAULT_DIR` | `./vault` | `core/vault.py:33,85` | the user's own data |
| dedup state | `SEEN_DB` | `./seen.db` | `core/seendb.py:10,15` | state |
| source health | `SLUICE_HEALTH` | `./sluice_health.json` | `core/health.py:26` | state |
| disabled sources | `SLUICE_DISABLED` | `./sluice_disabled.json` | `cli.py:32` | state |
| triage audit | `TRIAGE_AUDIT` | `./triage-audit.jsonl` | `core/app.py:660` | state |
| dossier cache | `DOSSIER_DIR` | `./dossiers` | `core/app.py:666` | cache |

That is fine for a repo you `pip install -e`. It is wrong for a Homebrew/apt/yum install, where
there is no project directory to stand in: running `sluice` from a home directory scatters five
files and two directories into it.

One of these is a correctness problem rather than untidiness. **`seen.db` is cwd-relative, so dedup
state silently depends on where the user was standing.** Run the same pipeline from a different
directory and sluice re-scrapes everything it already has, with no error and no warning. The other
six are tidiness; this one loses work.

A second gap surfaces alongside it. **The vault is the only path with no config key at all.**
`stores/vault.py:11-16` deliberately passes `dir=None` so `Vault` reads `VAULT_DIR` from the
environment, and `load_config` has no `vault_dir`. So the single most important path is settable
only by an env var, which does not survive a new shell — and #8's interactive `init` would have
nowhere to persist the answer it prompts for.

## The five settled decisions

1. **XDG, on macOS too.** Not `~/Library/Application Support`. Homebrew-installed CLIs
   overwhelmingly use XDG, and one rule across platforms is worth more than matching an Apple
   convention that users of this tool mostly do not see.
2. **Per-path env vars keep winning.** `SEEN_DB`, `SLUICE_HEALTH`, `SLUICE_DISABLED`,
   `TRIAGE_AUDIT`, `DOSSIER_DIR`, `VAULT_DIR`, `SLUICE_CONFIG` all still override the derived
   default. The documented layering (code default < YAML < env) holds, and every existing test that
   pins a path keeps working untouched.
3. **Detect and warn; never move.** Auto-migration was considered and rejected: moving user data as
   a side effect of which directory the process started in is the failure mode this sweep exists to
   remove, not a fix for it.
4. **`vault_dir` becomes a config key, and `./vault` stays the default.** The vault is user data;
   inventing a location under `~/.local/share` would be worse than a predictable one, and after #8's
   `init` writes `vault_dir` nobody reaches the default anyway.
5. **No `sluice paths` command and no `XDG_RUNTIME_DIR`.** Nothing needs the former yet (`init`
   prints the resolved set in #8), and nothing here is a socket or a lock.

## Design

### `sluice/core/paths.py` — the resolver

Pure, stdlib-only, no I/O and no `mkdir` at resolution time. A resolver that created directories
would make `--dry-run` write to disk, which is the property the dry-run flags exist to guarantee.

```
config_file()      -> $SLUICE_CONFIG | $XDG_CONFIG_HOME/sluice/config.yaml | ~/.config/sluice/config.yaml
state_file(name)   -> $XDG_STATE_HOME/sluice/<name>  | ~/.local/state/sluice/<name>
cache_dir(name)    -> $XDG_CACHE_HOME/sluice/<name>  | ~/.cache/sluice/<name>
```

`state_file` takes `seen.db`, `health.json`, `disabled.json`, `triage-audit.jsonl`. `cache_dir`
takes `dossiers`. The names lose their `sluice_` prefix and `./` — they are already inside a
`sluice/` directory, so `sluice_health.json` there would stutter.

Each call site keeps its own env var and consults it first, so the shape at every site is
`os.environ.get("<VAR>") or paths.state_file("<name>")` — not a new indirection that swallows the
override.

Reading `XDG_*` per call rather than at import: the vars are part of the process environment tests
manipulate, and an import-time snapshot is unpatchable. This matches `cli.py:31`'s existing comment
on `_disabled_path`, which is lazy for exactly that reason.

The legacy name each site compares against is the literal default it has today, not a derivation:

| resolved | legacy |
| --- | --- |
| `<state>/seen.db` | `./seen.db` |
| `<state>/health.json` | `./sluice_health.json` |
| `<state>/disabled.json` | `./sluice_disabled.json` |
| `<state>/triage-audit.jsonl` | `./triage-audit.jsonl` |
| `<cache>/dossiers` | `./dossiers` |

### The config file, and the behaviour change in it

**Five** loaders read `SLUICE_CONFIG` independently — `core/config.py:100`, `triage/config.py:58`,
`cv/config.py:73`, `apply/config.py:25`, `track/config.py:116` — each parsing its own top-level
block of the same file. All five switch to `paths.config_file()`, which keeps `SLUICE_CONFIG`
first. Changing four and missing one would give a config that half-loads: the root keys found, a
sub-app's block silently absent, and no error anywhere. The completeness of this list is the point,
so a test enumerates the loaders (glob `sluice/**/config.py` for `load_*_config`, as
`test_sluice_neutral_defaults.py` already discovers config dataclasses) rather than naming five.

This is the one **behaviour change** in the sweep. Today an unset `SLUICE_CONFIG` means no config
file at all, so every knob falls to its code default. Afterwards, an unset `SLUICE_CONFIG` reads
`~/.config/sluice/config.yaml` when it exists. That is the point — a packaged install with nothing
exported should find its config — but it means a user who has both an exported `SLUICE_CONFIG` and
a stale file at the XDG path sees no change, while a user with only the latter sees it start being
read. No legacy warning applies here: there was never a cwd default to strand.

### The legacy warning

One helper, called at each relocated site, firing only when **that site's env var is unset**:

- if the legacy cwd path exists and the resolved path does not → log one WARNING naming both paths
  and the exact `mv`, then use the resolved path;
- otherwise, silence.

It never opens, moves, or stats-then-acts on the legacy file. Detection is `os.path.exists` on two
paths and nothing more.

Gating on "env var unset" is what keeps it quiet in the suite and the harness, which pin every path
explicitly. It also means the warning is only ever about an unconfigured install, which is the only
case that can be surprised by the move.

### `vault_dir`, and the ordering trap in it

`Config` gains `vault_dir: str = ""`; `stores/vault.py:_make` passes it through as `dir`. The trap
is precedence. `core/vault.py:85` currently reads:

```python
self.dir = dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT)
```

Passing the YAML value in as `dir` makes **config beat env** — the exact inversion of the documented
layering. It would look correct and pass any test that sets only one of the two. So:

```python
self.dir = os.environ.get("VAULT_DIR") or dir or _DEFAULT_VAULT
```

`""` from an unset YAML key falls through to `./vault`, unchanged from today. Empty means abstain,
as everywhere else.

`vault_dir` is a `str`-typed config field, so the list-keyed neutral-defaults sweep
(`tests/test_sluice_neutral_defaults.py`) does not reach it and must not be widened to — `0`/`""`
means abstain is not universal there. It carries a named assertion alongside the existing str-typed
checks (`baseline_rel` not absolute, `store`/`fetcher`) instead.

### Parent directories

`SeenDb._init` (`seendb.py:27`) and `HealthStore._save` (`health.py:37`) already `makedirs` their
parent. The disabled-overlay writer in `cli.py` and the triage `AuditLog` do not — they have never
needed to, because `./x.json` has no parent to create. Both need one after the move. This is the
kind of thing that works on a developer machine (where `~/.local/state/sluice/` exists after the
first `ingest run`) and fails for a user whose first command is `sluice ingest disable`.

## Testing

### The hermeticity risk the sweep introduces

Today every default is cwd-relative and tests run in `tmp_path`, so nothing reaches a real home
directory. Afterwards, **any test that leaves `XDG_STATE_HOME` unset writes to the developer's
actual `~/.local/state/sluice/` — and passes while doing it.**

So `tests/conftest.py` gets an autouse fixture pinning `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and
`XDG_CACHE_HOME` to `tmp_path`, and the existing e2e hermetic assertion — which snapshots
`(size, mtime_ns)` per repo-root regular file — extends to cover the three XDG roots. Without that
extension the assertion keeps passing while the escape it exists to catch moves outside its
snapshot.

### The permanent tests

- **`tests/test_paths.py` — the resolver as a pure function.** `XDG_*` set and unset, `~`
  expansion, and no filesystem side effects (assert the directory is still absent after resolving).
- **Per-path env override.** One row per relocated site asserting the env var still wins. These are
  the rows that prove decision 2 rather than assuming it.
- **`vault_dir` precedence.** Env beats YAML beats default, written so that swapping the two
  operands in that `or` chain reddens it. A test that sets only one source cannot catch the
  inversion, which is the entire point of the row — and the inversion is the likeliest bug in this
  change.
- **Legacy warning.** Fires on (legacy present, resolved absent); silent when the resolved path
  exists; silent when the env var is set; and asserts both files are byte-identical and still in
  place afterwards, so "never move" is pinned rather than asserted in a comment.
- **Parent creation.** The disabled overlay and the audit log each write successfully into a
  resolved directory that does not exist yet.

### Mutation witnesses to run

Each must be run **by node id**, with the rest of the file confirmed green, so a pre-existing test
is not what catches it. Mutate by moving or deleting, never by adding, and run
`compileall --invalidation-mode checked-hash` first.

| # | mutant | expected to redden |
| --- | --- | --- |
| M1 | swap `os.environ.get("VAULT_DIR") or dir` back to `dir or os.environ.get(...)` | the precedence row, and nothing else |
| M2 | delete a site's `os.environ.get("<VAR>") or` prefix | that site's env-override row |
| M3 | delete the `and not os.path.exists(resolved)` conjunct in the legacy check | the "silent when resolved exists" row |
| M4 | make the legacy helper `shutil.move` instead of warn | the byte-identical/still-present assertion |
| M5 | drop the autouse `XDG_*` fixture | the e2e hermetic assertion |
| M6 | revert one sub-app loader to bare `os.environ.get("SLUICE_CONFIG")` | the loader-completeness row |

M5 is the one worth stating plainly: it is a witness that the *guard* works, not that the feature
does. Prior work here has repeatedly shipped assertions satisfied by adjacent prose or by a
pre-existing test, so each row above names the single smallest edit that should break it.

## Docs

- `sluice.yaml.example` gains a commented `vault_dir` with no value.
- `docs/ARCHITECTURE.md` gets the path table above.
- `.rulesync/rules/CLAUDE.md` gets only what is not visible from the file tree: paths resolve
  through `core/paths.py`, per-path env vars still win, nothing is auto-migrated. Not the table —
  that file states its own contract that per-module detail belongs in `ARCHITECTURE.md`.
- `README.md`'s quickstart stops implying a project directory.

## Out of scope

- `sluice init` itself (#8), including the `sluice.yaml.example` placeholder fix, which lands with
  the wizard that consumes it.
- Packaging (`brew`/`apt`/`yum` formulae). This change is the prerequisite that makes packaging
  sensible, not the packaging.
- Relocating the vault. It is user data with a location only the user knows.
