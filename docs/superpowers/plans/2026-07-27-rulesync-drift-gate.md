# rulesync Drift Gate Implementation Plan (#2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CI fail when `rulesync generate` on a fresh checkout either dirties the tree or stops producing the outputs it should, so the `.gitignore` re-audit obligation that today exists only as prose has something behind it.

**Architecture:** A pinned npm toolchain (`package.json` + lockfile) makes rulesync's output deterministic from tracked inputs. One CI job regenerates on a fresh checkout and asserts two things: the tree stays clean (I1), and the per-feature file counts match a frozen map (I2). I2 exists because I1 alone is provably green on the failure that matters — a malformed `hooks.json` drops 17 files, exits 0, and leaves the tree clean. A stdlib-only guard script under `scripts/` does the parsing, split pure-from-impure so the parser is unit-testable offline.

**Tech Stack:** Python 3.12+ stdlib only (no new `sluice/` dependency — the guard lives in `scripts/`). pytest + faker for tests. npm/Node only in CI and only for the generator. rulesync 9.6.3, integrity-pinned via `package-lock.json`.

**Spec:** `docs/superpowers/specs/2026-07-27-rulesync-drift-gate-design.md`

## Already shipped — DO NOT re-plan

D10 landed ahead of this plan. These files are already correct; touching them again is drift:

| Commit | Work |
|---|---|
| `90061b3` | Four `E741`s in `scripts/diff_vs_legacy.py` fixed by rename |
| `b39e0e3` | CI lint extended to `scripts/`; all twelve documented statements of the quality bar moved in lockstep |
| `428606a` | `tests/test_ci_wiring.py` created, pinning CI's lint targets against the docs that state them |

`tests/test_ci_wiring.py` **already exists**. Task 4 *adds to* it; it does not create it.

## Global Constraints

- **`sluice/` is untouched.** Nothing in this plan modifies `sluice/`. The guard lives in `scripts/`.
- **Standard library only** in the guard. No `yaml`, no `requests`.
- **The offline suite never shells out to npm or npx.** Every test here reads tracked files or fixture strings.
- **Quality bar before every commit:** `ruff check sluice tests scripts` clean AND `python -m pytest` green. ruff is pinned `0.15.21` and is not in `[test]`; `pip install ruff==0.15.21` if absent.
- **ruff line-length is 100** (`pyproject.toml:33`).
- **Conventional Commits.** `type(scope): description`.
- **Never `ruff format`** — it would reformat most of the repo. This repo uses the linter only.
- **`.rulesync/` is canonical and user-authorised for this work only** — the generate command, `hooks.json`'s `_comment` trailing pointer, and (already done) the ruff target list. **No rule content changes.**
- **Counts are only valid on a fresh tree.** rulesync skips writing files whose content already matches; re-running on a generated tree reports ~14 files, not 243. Never "verify" `EXPECTED` by running the generator in the working copy.

## File Structure

| File | Responsibility |
|---|---|
| `package.json` (create) | Pins rulesync 9.6.3; holds the one generate command as an npm script |
| `package-lock.json` (create) | Integrity-pins the 244-package transitive tree — what makes I2's exact counts legitimate |
| `.gitignore` (modify) | Ignore `/node_modules/` and `.npmrc`; reword the two version literals; point at the enforcer |
| `tests/test_no_leaked_files.py` (modify) | Gate the two new ignores; enumerate all three FORBIDDEN tuples |
| `scripts/guard_rulesync_drift.py` (create) | Pure `parse_summary` + `main` comparing against frozen `EXPECTED` |
| `tests/test_guard_rulesync_drift.py` (create) | Parser cases, `main` cases, subprocess exit-code contract |
| `tests/test_rulesync_version_pin.py` (create) | One version literal, with `hooks.json`'s schema record asserted equal to it |
| `.rulesync/rules/CLAUDE.md` (modify) | Generate command → the two-step form |
| `.rulesync/hooks.json` (modify) | `_comment` trailing pointer → `package.json` |
| `.github/workflows/ci.yml` (modify) | New `rulesync` job; `ci-success` gains the job **and** a third conjunct |
| `.github/dependabot.yml` (modify) | npm ecosystem |
| `tests/test_ci_wiring.py` (modify) | Gate wiring assertions added to the existing lint-bar ones |
| `tests/test_hooks_wiring.py` (modify) | Docstring reason corrected — CI now does run rulesync |

---

### Task 1: npm toolchain, ignores, and the leak gate that covers them

**Files:**
- Create: `package.json`, `package-lock.json`
- Modify: `.gitignore`
- Test: `tests/test_no_leaked_files.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `package.json` with `scripts.rulesync` = `node_modules/.bin/rulesync generate -t '*' -f '*'` and `devDependencies.rulesync` = `9.6.3`. Tasks 3 and 4 read both.

- [ ] **Step 1: Write `package.json`**

The explicit `node_modules/.bin/` path is load-bearing. `npm run` *prepends* `node_modules/.bin` to PATH — it does not restrict PATH — so a bare `rulesync` falls through to any global install. Measured: with no `node_modules/`, a bare `rulesync` silently ran a global 9.2.0 and exited 0. The explicit path exits 127 instead.

```json
{
  "name": "sluice-tooling",
  "private": true,
  "description": "CI tooling only. sluice itself is stdlib-only Python; this pins the rulesync generator so its output is determined by tracked inputs.",
  "scripts": {
    "rulesync": "node_modules/.bin/rulesync generate -t '*' -f '*'"
  },
  "devDependencies": {
    "rulesync": "9.6.3"
  }
}
```

- [ ] **Step 2: Generate the lockfile**

Run: `npm install --package-lock-only`
Expected: `package-lock.json` created, roughly 113 KB, 244 packages. Do **not** hand-edit it.

- [ ] **Step 3: Make the coverage test enumerate all three tuples**

The existing `test_the_gate_covers_every_path_gitignore_covers` (`tests/test_no_leaked_files.py:112-117`) iterates `FORBIDDEN_EXACT + FORBIDDEN_PREFIXES` and then *hand-asserts* `.memsearch`. `FORBIDDEN_COMPONENTS` is never enumerated, so `.npmrc` would be added in Step 4 and checked by nothing. This step must come **first**, or Step 5 passes vacuously. Replace that function entirely:

```python
def test_the_gate_covers_every_path_gitignore_covers():
    """A gate guarding fewer paths than .gitignore is a gate with a hole in it.

    Enumerates all THREE tuples. The previous version iterated two and hand-asserted
    `.memsearch`, so a later addition to FORBIDDEN_COMPONENTS -- `.npmrc`, which stops a
    registry credential becoming committable -- would have sat outside the check entirely.
    Enumerate, never hand-list.
    """
    ignored = (REPO / ".gitignore").read_text()
    gated = FORBIDDEN_EXACT + FORBIDDEN_PREFIXES + FORBIDDEN_COMPONENTS
    assert gated, "no paths gated: this test would pass without checking anything"
    for path in gated:
        assert path.strip("/") in ignored, f"{path} is gated but NOT gitignored -- they must agree"
```

- [ ] **Step 4: Add the new entries to the gate tuples**

In `tests/test_no_leaked_files.py`, change line 39 and line 43:

```python
FORBIDDEN_PREFIXES = (".claude/", ".cursor/", "node_modules/")
# .memsearch and .npmrc may appear at ANY depth. Both .gitignore rules are deliberately
# unanchored -- for a directory that has already leaked personal data three times, and for a
# file that can carry a registry auth token, catching them anywhere is the safer default.
FORBIDDEN_COMPONENTS = (".memsearch", ".npmrc")
```

- [ ] **Step 5: Run it to verify it FAILS**

Run: `python -m pytest tests/test_no_leaked_files.py::test_the_gate_covers_every_path_gitignore_covers -v`
Expected: **FAIL** — `node_modules/ is gated but NOT gitignored`. The gate now demands two paths `.gitignore` does not yet carry.

- [ ] **Step 6: Add the two ignores**

Append to `.gitignore`, after the `!/.rulesync/**` line and before the `.memsearch/` block:

```
# npm toolchain for the rulesync generator (CI only; sluice itself is stdlib-only Python).
# Root-anchored like the generated-output block above.
/node_modules/
# NOT root-anchored, deliberately -- same reasoning as .memsearch/ below. Root-anchoring exists
# only to stop a bare `CLAUDE.md` swallowing the canonical source at .rulesync/rules/CLAUDE.md,
# a collision a credential file cannot cause. .npmrc can carry a registry auth token, and it is
# also how an `npm_config_*` setting could switch on a dependency's install-time script, so
# catching it at any depth is the safer default.
.npmrc
```

- [ ] **Step 7: Run to verify it passes**

Run: `python -m pytest tests/test_no_leaked_files.py -v && ruff check sluice tests scripts`
Expected: all PASS, ruff clean.

- [ ] **Step 8: Witness the gate — delete an ignore line and confirm red**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
git stash -- .gitignore && python -m pytest tests/test_no_leaked_files.py::test_the_gate_covers_every_path_gitignore_covers -q; git stash pop
```
Expected: FAIL naming `node_modules/` or `.npmrc`, then restored.

- [ ] **Step 9: Commit**

```bash
git add package.json package-lock.json .gitignore tests/test_no_leaked_files.py
git commit -m "build(npm): pin the rulesync generator, and gate the ignores it needs"
```

---

### Task 2: the drift guard and its offline tests

**Files:**
- Create: `scripts/guard_rulesync_drift.py`
- Test: `tests/test_guard_rulesync_drift.py`

**Interfaces:**
- Consumes: nothing at runtime. Reads a captured-output file path.
- Produces: `parse_summary(text: str) -> dict[str, int]` (pure, raises `ValueError`); `main(argv: list[str]) -> int`; module constant `EXPECTED: dict[str, int]`. Task 4's CI job invokes the module with one positional argument.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_guard_rulesync_drift.py`. Both fixtures are real captured rulesync 9.6.3 output.

```python
"""The drift guard's parser and its process contract.

The two fixtures are REAL captured rulesync 9.6.3 output, including the emoji and the exact
`All done!` phrasing. A version bump may change that wording; the expected failure is then a
loud parse error, never a silent pass. If these strings stop matching, fix the parser -- do not
relax the test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import guard_rulesync_drift as guard  # noqa: E402

GREEN = "🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)"
# What a malformed .rulesync/hooks.json actually produces. Note it OMITS the hooks term rather
# than printing `0 hooks` -- a parser regexing `(\d+) hooks` finds no match and, if it treats
# "no match" as "skip", reports success on the one input this guard exists to reject.
BROKEN_HOOKS = "🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)"


def test_a_matching_summary_parses_to_the_expected_map():
    assert guard.parse_summary(GREEN) == guard.EXPECTED


def test_an_omitted_feature_term_is_absent_not_zero_padded():
    """The parser reports what it saw; main() is what turns absence into a failure."""
    assert "hooks" not in guard.parse_summary(BROKEN_HOOKS)


def test_no_summary_line_is_a_hard_error():
    with pytest.raises(ValueError, match="no `All done!` summary"):
        guard.parse_summary("Written 3 subagents\nsome unrelated chatter\n")


def test_two_summary_lines_are_a_hard_error():
    with pytest.raises(ValueError, match="expected exactly 1"):
        guard.parse_summary(GREEN + "\n" + GREEN)


def test_main_returns_zero_on_a_matching_capture(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN, encoding="utf-8")
    assert guard.main([str(capture)]) == 0


def test_main_rejects_a_silently_dropped_feature(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(BROKEN_HOOKS, encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_a_partial_drop_that_a_non_zero_check_would_pass(tmp_path):
    """16 of 17 hooks. Only an equality comparison catches this; `> 0` does not."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("17 hooks", "16 hooks"), encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_an_unexpected_feature(tmp_path):
    """A newly-enabled rulesync feature is drift: it emits into paths nothing ignores yet."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("+ 17 hooks", "+ 17 hooks + 3 commands"), encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_a_missing_capture_file(tmp_path):
    assert guard.main([str(tmp_path / "nope.txt")]) != 0


def test_an_empty_capture_file_is_a_hard_error_not_a_pass(tmp_path):
    """The pipefail analysis rests on this and nothing tested it.

    Without `set -o pipefail`, `rulesync | tee` swallows a non-zero rulesync exit -- but tee
    still leaves a 0-BYTE capture. This case is the only reason that swallowed failure still
    surfaces. If an empty file ever parsed as a pass, the CI step would go green on a rulesync
    that never ran.
    """
    capture = tmp_path / "out.txt"
    capture.write_text("", encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_the_module_exits_nonzero_as_a_process(tmp_path):
    """The CI contract IS the exit code. A suite that only calls main() in-process stays green
    when `sys.exit(main(...))` is deleted -- the lesson tests/test_guard_no_bypass.py:26-30
    records. Driven with a FAILING capture: a passing one cannot tell the two apart."""
    capture = tmp_path / "out.txt"
    capture.write_text(BROKEN_HOOKS, encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "guard_rulesync_drift.py"
    proc = subprocess.run([sys.executable, str(script), str(capture)], capture_output=True)
    assert proc.returncode != 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_guard_rulesync_drift.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'guard_rulesync_drift'`.

- [ ] **Step 3: Write the guard**

Create `scripts/guard_rulesync_drift.py`:

```python
#!/usr/bin/env python3
"""Assert `rulesync generate` produced every output it was supposed to.

WHY COUNTS AND NOT JUST A CLEAN TREE. A malformed `.rulesync/hooks.json` makes rulesync drop 17
files, exit **0**, and leave `git status --porcelain` **empty**. Exit code and porcelain both
pass green on the one failure this gate exists to catch. Reproduced again on a real version
delta: rulesync 9.2.0 against a 9.6.3-audited .gitignore emitted 238 files with a clean tree.

WHY EXACT COUNTS AND NOT "NON-ZERO". A partial drop -- 16 of 17 hooks -- passes a non-zero
check. The output set is fully determined by tracked inputs (`.rulesync/` AND
`package-lock.json`, which is why the lockfile exists), so an exact pin is legitimate.

WHEN EXPECTED CHANGES. Only when `.rulesync/` or the lockfile changes. Updating it is the moment
to re-audit `.gitignore`'s generated-output list, because a version bump is exactly when a new
target or feature starts emitting into a path nothing ignores. A Dependabot rulesync PR is
therefore EXPECTED to arrive red: that is the gate working, not a defect to debug away.

THE COUNTS ARE ONLY VALID ON A FRESH TREE. rulesync skips writing a file whose content already
matches, so re-running in a working copy reports a handful of files rather than the full set.
"""

from __future__ import annotations

import re
import sys

EXPECTED = {"rules": 20, "subagents": 114, "skills": 92, "hooks": 17}

_SUMMARY = re.compile(r"All done! Written \d+ file\(s\) total \(([^)]*)\)")
_TERM = re.compile(r"(\d+)\s+([a-z]+)")


def parse_summary(text: str) -> dict[str, int]:
    """Feature -> count, parsed from rulesync's `All done!` line. Pure.

    A feature that produced nothing is OMITTED from the parenthetical rather than printed as
    `0 hooks`. So the returned map simply lacks that key; `main` is what turns absence into a
    failure. A caller that regexes `(\\d+) hooks`, finds no match, and moves on would report
    success on exactly the input this guard exists to reject.
    """
    found = _SUMMARY.findall(text)
    if not found:
        raise ValueError(
            "no `All done!` summary line in the captured output. rulesync did not finish, the "
            "capture is empty, or the output format changed on a version bump. Any of those "
            "must fail loudly rather than pass."
        )
    if len(found) > 1:
        raise ValueError(f"found {len(found)} summary lines, expected exactly 1")
    return {name: int(count) for count, name in _TERM.findall(found[0])}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: guard_rulesync_drift.py <captured-rulesync-output>", file=sys.stderr)
        return 2
    try:
        text = open(argv[0], encoding="utf-8").read()
    except OSError as exc:
        print(f"cannot read the captured output: {exc}", file=sys.stderr)
        return 2
    try:
        actual = parse_summary(text)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    # Absent means zero, so a silently-dropped feature MISMATCHES rather than being skipped.
    normalised = {feature: actual.get(feature, 0) for feature in EXPECTED}
    unexpected = sorted(set(actual) - set(EXPECTED))
    if normalised == EXPECTED and not unexpected:
        return 0

    print("rulesync output does not match the pinned expectation.", file=sys.stderr)
    for feature in sorted(EXPECTED):
        print(f"  {feature:<10} expected {EXPECTED[feature]:>4}  actual {normalised[feature]:>4}",
              file=sys.stderr)
    for feature in unexpected:
        print(f"  {feature:<10} UNEXPECTED feature, emitting {actual[feature]} file(s) -- it "
              f"writes to paths .gitignore may not cover", file=sys.stderr)
    print(
        "\nIf .rulesync/ or package-lock.json changed, update EXPECTED -- and re-audit "
        "the generated-output list in .gitignore while you are there.\n"
        "If neither changed, generation is broken.\n"
        "NOTE: these counts are only valid on a FRESH checkout; rulesync skips files whose "
        "content already matches, so a re-run in a working copy reports far fewer.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_guard_rulesync_drift.py -v && ruff check sluice tests scripts`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit before mutating**

An untracked file cannot be restored by `git checkout --`, and the empty post-run diff hides the loss.

```bash
git add scripts/guard_rulesync_drift.py tests/test_guard_rulesync_drift.py
git commit -m "feat(scripts): add the rulesync generation drift guard"
```

- [ ] **Step 6: Mutation-witness the guard**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Apply each mutant by **deleting or moving**, never adding. After each: run the named test by node id, confirm FAIL, then `git checkout -- scripts/guard_rulesync_drift.py`.

| # | Mutation | Must be killed by |
|---|---|---|
| 1 | Delete `sys.exit(...)` from `__main__`, leaving bare `main(sys.argv[1:])` | `test_the_module_exits_nonzero_as_a_process` |
| 2 | Change `normalised == EXPECTED` to `all(normalised[k] > 0 for k in EXPECTED)` — the "non-zero is good enough" mistake | `test_main_rejects_a_partial_drop_that_a_non_zero_check_would_pass` |
| 3 | Delete `for feature in EXPECTED` normalisation, using `actual` directly | `test_main_rejects_a_silently_dropped_feature` |
| 4 | Delete the `unexpected` clause from the `if` | `test_main_rejects_an_unexpected_feature` |
| 5 | Delete the `if not found: raise` block | `test_no_summary_line_is_a_hard_error` and `test_an_empty_capture_file_is_a_hard_error_not_a_pass` |
| 6 | Delete the `if len(found) > 1: raise` block | `test_two_summary_lines_are_a_hard_error` |

For each, also run `python -m pytest -q --ignore=tests/test_guard_rulesync_drift.py` and confirm it stays GREEN — proving the new file is what kills the mutant, not a pre-existing test.

- [ ] **Step 7: Confirm restored**

Run: `git status --porcelain && python -m pytest -q`
Expected: clean tree, all green.

---

### Task 3: collapse the version literal, and pin what survives

**Files:**
- Modify: `.rulesync/rules/CLAUDE.md:13-16`, `.rulesync/hooks.json` (`_comment` trailing sentence), `.gitignore`
- Test: `tests/test_rulesync_version_pin.py` (create)

**Interfaces:**
- Consumes: `package.json` from Task 1.
- Produces: the invariant that `package.json` is the only place a rulesync version may be *chosen*.

**Why this task exists.** Before it, `9.6.3` sits in four live places and a bump means four edits kept in step by nothing. After it, prose names no version at all — except one deliberate, asserted exception.

- [ ] **Step 1: Reword the canonical generate command**

`.rulesync/rules/CLAUDE.md`, lines 13-16 currently read:

```
**This file is generated.** The canonical source is `.rulesync/rules/CLAUDE.md`. Run
`npx rulesync@9.6.3 generate -t '*' -f '*'` after cloning to populate the AI-tool outputs
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, ...), all of which are gitignored. Editing a
generated file instead of the `.rulesync/` source is drift.
```

Replace with:

```
**This file is generated.** The canonical source is `.rulesync/rules/CLAUDE.md`. Run
`npm ci && npm run rulesync` after cloning to populate the AI-tool outputs
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, ...), all of which are gitignored. Editing a
generated file instead of the `.rulesync/` source is drift. The version and the flags both
live in `package.json`, so this command never names either -- and CI runs the same one.
```

- [ ] **Step 2: Retarget the hooks.json cross-reference**

In `.rulesync/hooks.json`, the `_comment` ends:

```
Re-verify the emitted .claude/settings.json by hand when changing the pinned version in .rulesync/rules/CLAUDE.md.
```

Change the final clause to `... when changing the pinned version in package.json.`

**Leave the opening clause — "This is rulesync 9.6.3's CANONICAL schema" — exactly as it is.** That literal records which version the hook schema was verified against, in the comment that calls itself the only defence against a bump silently dropping the hook command. Step 4 asserts it rather than erasing it.

- [ ] **Step 3: Reword both `.gitignore` version literals**

Line 37 currently reads `#   npx rulesync@9.6.3 generate -t '*' -f '*'` → `#   npm ci && npm run rulesync`

In the block at lines 52-61, the clause `# other tool rulesync 9.6.3 knows about.` → `# other tool the pinned rulesync knows about.`

In the same block, extend the re-audit sentence so the two halves reference each other:

```
# is what changes) AND whenever a new rulesync FEATURE is turned on: `-f '*'` covers
# rules/ignore/mcp/subagents/commands/skills/hooks/permissions, and enabling one emits a fresh
# output per target. scripts/guard_rulesync_drift.py is what now FORCES that re-audit: it pins
# the per-feature output counts, so a bump or a new feature fails CI until a human looks here.
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_rulesync_version_pin.py`:

```python
"""One place chooses the rulesync version; everywhere else agrees or says nothing.

Before this, `9.6.3` sat in four live places and a bump meant four edits kept in step by
nothing. package.json is now the only place that CHOOSES. One exception is deliberate:
.rulesync/hooks.json's `_comment` records which version's schema that file was written
against, and that comment calls itself the only defence against a version bump silently
dropping the hook command. Erasing the literal would lose the record; excluding the file
would make this sweep vacuous exactly where it matters. So it is ASSERTED EQUAL instead --
which means a bump turns this test red until a human re-verifies the emitted settings.json,
precisely what that comment asks for and today cannot enforce.

docs/superpowers/ is excluded as a dated record: the convention is a dated superseded note,
never rewriting history.
"""

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+\b")
ALLOWED = {"package.json", "package-lock.json", ".rulesync/hooks.json"}


def _pinned_version() -> str:
    manifest = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    return manifest["devDependencies"]["rulesync"]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=30, check=True
    ).stdout
    return [p for p in out.splitlines() if not p.startswith("docs/superpowers/")]


def test_the_pinned_version_is_readable_and_specific():
    """Non-vacuity: every assertion below compares against this string."""
    version = _pinned_version()
    assert VERSION_RE.fullmatch(version), f"package.json pins a non-specific version: {version!r}"


def test_hooks_json_records_the_version_it_was_verified_against():
    comment = json.loads((REPO / ".rulesync" / "hooks.json").read_text(encoding="utf-8"))["_comment"]
    found = VERSION_RE.findall(comment)
    assert found, (
        ".rulesync/hooks.json's _comment no longer records which rulesync version its schema was "
        "verified against. That record is the only defence against a bump silently dropping the "
        "hook command -- restore it rather than deleting it."
    )
    for version in found:
        assert version == _pinned_version(), (
            f".rulesync/hooks.json says {version}, package.json pins {_pinned_version()}. "
            "Re-verify the emitted .claude/settings.json by hand, then update the comment."
        )


def test_no_other_tracked_file_names_a_rulesync_version():
    version = _pinned_version()
    files = _tracked_files()
    assert files, "git ls-files returned nothing: this sweep would pass without checking"
    offenders = [
        path
        for path in files
        if path not in ALLOWED
        and version in (REPO / path).read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, (
        f"these tracked files hardcode the rulesync version {version}: {offenders}. "
        "package.json is the only place that chooses it; prose should name no version at all."
    )
```

- [ ] **Step 5: Run to verify it fails before the rewording, passes after**

Run: `python -m pytest tests/test_rulesync_version_pin.py -v`
Expected: PASS — Steps 1-3 already did the rewording. To prove the test is load-bearing, temporarily restore one literal:

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
sed -i '' 's|#   npm ci \&\& npm run rulesync|#   npx rulesync@9.6.3 generate|' .gitignore
python -m pytest tests/test_rulesync_version_pin.py::test_no_other_tracked_file_names_a_rulesync_version -q
git checkout -- .gitignore
```
Expected: FAIL naming `.gitignore`, then restored.

- [ ] **Step 6: Witness the hooks.json assertion**

```bash
python -c "
import json,pathlib
p=pathlib.Path('.rulesync/hooks.json'); d=json.loads(p.read_text())
d['_comment']=d['_comment'].replace('9.6.3','9.9.9'); p.write_text(json.dumps(d,indent=2))
"
python -m pytest tests/test_rulesync_version_pin.py::test_hooks_json_records_the_version_it_was_verified_against -q
git checkout -- .rulesync/hooks.json
```
Expected: FAIL naming the mismatch, then restored.

- [ ] **Step 7: Regenerate and confirm a clean tree**

Run: `npm ci --ignore-scripts && npm run rulesync && git status --porcelain`
Expected: only the files this task edited appear. No generated output is tracked.

- [ ] **Step 8: Commit**

```bash
git add .rulesync/rules/CLAUDE.md .rulesync/hooks.json .gitignore tests/test_rulesync_version_pin.py
git commit -m "refactor(rulesync): make package.json the only place that names a version"
```

---

### Task 4: the CI job, wired so it actually blocks

**Files:**
- Modify: `.github/workflows/ci.yml`, `.github/dependabot.yml`
- Test: `tests/test_ci_wiring.py` (**exists** — add to it, do not recreate)

**Interfaces:**
- Consumes: `package.json` (Task 1), `scripts/guard_rulesync_drift.py` (Task 2).
- Produces: a blocking `rulesync` check.

**The trap this task exists to avoid.** `ci-success` carries `if: always()`, so adding a job to its `needs:` only *orders* it. The sole thing that fails `ci-success` is the explicit `&&` chain in its run block. Wire the job without extending that chain and a **red** gate yields a **green** required check.

- [ ] **Step 1: Add the job**

Insert into `.github/workflows/ci.yml` after the `test` job:

```yaml
  rulesync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with:
          node-version: "22"
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      # --ignore-scripts: one package in the pinned tree declares a postinstall. It is
      # env-gated and does not fetch by default, but it executes outside the lockfile's
      # determination -- exactly what the lockfile is here to rule out.
      - run: npm ci --ignore-scripts
      - run: |
          set -euo pipefail
          npm run rulesync | tee "$RUNNER_TEMP/rulesync-output.txt"
          python scripts/guard_rulesync_drift.py "$RUNNER_TEMP/rulesync-output.txt"
          if [ -n "$(git status --porcelain)" ]; then
            git status --porcelain
            exit 1
          fi
```

Four details, each of which was wrong in a draft:
- `set -o pipefail` — a `run:` with no `shell:` key is `bash -e {0}`, `-e` but **not** pipefail. Without it `$?` is `tee`'s status.
- `setup-python` — the step calls `python`; both existing Python jobs pin it.
- capture in `$RUNNER_TEMP` — a scratch file beside the checkout is untracked and trips the porcelain check.
- guard **before** porcelain — a fail-open produces a *clean* tree, so porcelain alone passes on it.

- [ ] **Step 2: Wire `ci-success` — both halves**

```yaml
  ci-success:
    needs: [lint, test, rulesync]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: |
          [ "${{ needs.lint.result }}" = success ] && \
          [ "${{ needs.test.result }}" = success ] && \
          [ "${{ needs.rulesync.result }}" = success ]
```

Do **not** give the new job a `name:` key — the check-run name is the job id unless `name:` is set, and renaming a required check breaks the branch-protection binding.

- [ ] **Step 3: Add the npm Dependabot ecosystem**

Append to `.github/dependabot.yml`:

```yaml
  # npm covers package.json -- the rulesync generator only; sluice itself ships no JS.
  # A bump here is EXPECTED to land red: scripts/guard_rulesync_drift.py pins the per-feature
  # output counts, and a new rulesync version moves them. That is the gate working. Update
  # EXPECTED and re-audit .gitignore's generated-output list on the bump PR, then merge.
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
    commit-message:
      prefix: chore
      include: scope
    groups:
      npm:
        patterns: ["*"]
```

- [ ] **Step 4: Write the failing wiring tests**

Append to the existing `tests/test_ci_wiring.py`:

```python
def _ci_text() -> str:
    return CI.read_text()


def test_ci_success_requires_the_rulesync_job():
    """Membership asserted SEPARATELY from the consistency check below.

    Consistency alone is not enough: deleting the `needs:` entry AND its conjunct together
    leaves both sides agreeing while the gate is unwired.
    """
    text = _ci_text()
    assert "rulesync" in text.split("ci-success:")[1].split("if:")[0], (
        "ci-success does not depend on the rulesync job"
    )
    assert 'needs.rulesync.result }}" = success' in text, (
        "ci-success does not CHECK the rulesync result. `if: always()` means `needs:` only "
        "orders the job -- the && chain is the only thing that can fail ci-success, so a red "
        "gate would yield a green required check."
    )


def test_every_needed_job_is_checked_in_the_success_chain():
    """Both ends enumerated from the file, never hand-listed."""
    block = _ci_text().split("ci-success:")[1]
    needed = re.search(r"needs:\s*\[([^\]]*)\]", block).group(1)
    jobs = [j.strip() for j in needed.split(",") if j.strip()]
    assert jobs, "ci-success declares no needs: this test would pass without checking anything"
    for job in jobs:
        assert f'needs.{job}.result }}}}" = success' in block, (
            f"ci-success needs {job!r} but never checks its result"
        )


def test_the_rulesync_job_sets_pipefail():
    """`bash -e {0}` is the default -- `-e` but NOT pipefail, so `cmd | tee` reports tee's
    status and a rulesync exit 1 would be swallowed."""
    assert "set -euo pipefail" in _ci_text()


def test_the_capture_file_is_outside_the_work_tree():
    """A scratch file beside the checkout is itself untracked and trips the porcelain check."""
    assert '$RUNNER_TEMP/rulesync-output.txt' in _ci_text()


def test_the_guard_runs_before_the_porcelain_check():
    """Index comparison, not substring presence: a substring test passes when EITHER is
    deleted. A fail-open produces a CLEAN tree, so porcelain-first would pass on it."""
    text = _ci_text()
    guard_at = text.index("guard_rulesync_drift.py")
    porcelain_at = text.index("git status --porcelain")
    assert guard_at < porcelain_at, "the completeness guard must run before the tree check"


def test_the_job_uses_the_locked_binary_not_npx():
    """Substituting `npx rulesync@9.6.3 generate ...` back keeps the counts identical and the
    guard green while silently discarding the locked transitive tree the pin exists for."""
    text = _ci_text()
    assert "npm ci --ignore-scripts" in text
    assert "npx" not in text, "the CI job must not invoke npx: it can fetch an unpinned rulesync"
    assert text.index("npm ci --ignore-scripts") < text.index("guard_rulesync_drift.py")


def test_package_json_runs_the_locked_binary_by_path():
    """`npm run` PREPENDS node_modules/.bin to PATH, it does not restrict PATH. Measured: with
    no node_modules, a bare `rulesync` silently ran a global 9.2.0 and exited 0."""
    manifest = json.loads((ROOT / "package.json").read_text())
    assert manifest["scripts"]["rulesync"] == "node_modules/.bin/rulesync generate -t '*' -f '*'"
```

Add `import json` to the module's imports.

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_ci_wiring.py -v && ruff check sluice tests scripts`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Validate the workflow**

Run: `zizmor --offline --strict-collection .github/workflows/`
Expected: no findings.

- [ ] **Step 7: Commit before mutating**

```bash
git add .github/workflows/ci.yml .github/dependabot.yml tests/test_ci_wiring.py
git commit -m "ci(rulesync): gate regeneration hygiene and output completeness"
```

- [ ] **Step 8: Mutation-witness the wiring**

Each mutant by deletion; after each, run the named test by node id, confirm FAIL, then `git checkout -- .github/workflows/ci.yml`.

| # | Mutation | Must be killed by |
|---|---|---|
| 1 | Delete the third conjunct from `ci-success` | `test_ci_success_requires_the_rulesync_job` |
| 2 | Delete `rulesync` from `needs:` **and** its conjunct together | `test_ci_success_requires_the_rulesync_job` |
| 3 | Delete `set -euo pipefail` | `test_the_rulesync_job_sets_pipefail` |
| 4 | Delete the `git status --porcelain` block | `test_the_guard_runs_before_the_porcelain_check` (raises `ValueError` on the missing substring) |
| 5 | Move the porcelain block above the guard call | `test_the_guard_runs_before_the_porcelain_check` |
| 6 | Replace `npm run rulesync` with `npx rulesync@9.6.3 generate -t '*' -f '*'` | `test_the_job_uses_the_locked_binary_not_npx` |
| 7 | In `package.json`, replace the script body with `rulesync generate -t '*' -f '*'` | `test_package_json_runs_the_locked_binary_by_path` |

For each, also run `python -m pytest -q --ignore=tests/test_ci_wiring.py` and confirm GREEN.

- [ ] **Step 9: Confirm restored**

Run: `git status --porcelain && python -m pytest -q && ruff check sluice tests scripts`

---

### Task 5: correct the reasons this work falsifies, and rewrite issue #2

**Files:**
- Modify: `tests/test_hooks_wiring.py` (docstring only)
- External: GitHub issue #2 body

**Interfaces:**
- Consumes: the shipped CI job from Task 4.
- Produces: nothing code depends on.

**Why.** A stale *reason* is harder to notice than a wrong conclusion, because nothing fails.

- [ ] **Step 1: Correct the hooks-wiring docstring**

`tests/test_hooks_wiring.py`'s docstring says:

```
WHY THE SOURCE AND NOT THE GENERATED FILE: `.claude/settings.json` is gitignored and
produced by `npx rulesync generate`, which needs node and network. CI never runs it, so
asserting the generated artifact could not be hermetic. `.rulesync/hooks.json` is the
tracked input, and the schema is what actually goes wrong.
```

`CI never runs it` is now false. Replace that paragraph with:

```
WHY THE SOURCE AND NOT THE GENERATED FILE: `.claude/settings.json` is gitignored and produced
by `npm run rulesync`, which needs node and network. CI DOES now run the generator, in the
separate `rulesync` job -- but this suite must stay offline and hermetic, so asserting the
generated artifact here still could not work. `.rulesync/hooks.json` is the tracked input, and
the schema is what actually goes wrong. The conclusion is unchanged; only its reason moved.
```

- [ ] **Step 2: Verify**

Run: `python -m pytest tests/test_hooks_wiring.py -v && ruff check sluice tests scripts`
Expected: PASS, clean.

- [ ] **Step 3: Commit**

```bash
git add tests/test_hooks_wiring.py
git commit -m "docs(tests): correct the hooks-wiring rationale now CI runs the generator"
```

- [ ] **Step 4: Rewrite issue #2's body**

The current body opens "The repo carries **no** agent/assistant instructions at all", which was true when filed and is false now, and asks for a goal that cannot be built. Write the new body to a file and apply it:

```bash
cat > /tmp/issue2.md <<'EOF'
## Status

The headline proposal — adopt `rulesync` and author the rules once — **shipped**. `.rulesync/`
is canonical and tracked, and generates the per-tool outputs, all gitignored.

This issue now covers the remaining half: CI enforcement.

## What the original text got wrong

It opened "The repo carries no agent/assistant instructions at all." True when filed, false now.

More importantly it asked for "generation checked in CI so a hand-edit of a generated file fails
the build." **That cannot be built.** The generated outputs are gitignored, so a hand-edit is a
local, untracked change that never reaches CI. Tracking them instead was considered and rejected:
it buys the literal goal at the cost of a large generated diff in every PR.

## What is actually being built

Two invariants, asserted on a fresh checkout:

- **Hygiene** — running the documented generate command leaves `git status --porcelain` empty.
  Catches a `.gitignore` gap when a version bump or a newly-enabled feature emits into a fresh
  path, including into the tracked `.github/` tree.
- **Completeness** — the run reports the pinned per-feature output counts.

The second exists because the first is provably insufficient: a malformed `.rulesync/hooks.json`
silently drops 17 files, exits 0, and leaves the tree clean. Both the exit code and the porcelain
check pass green on the one failure the gate exists to catch.

Scope: a pinned npm toolchain so output is determined by tracked inputs, a stdlib-only guard under
`scripts/`, the CI job, and the offline tests that keep all of it honest.

Design: `docs/superpowers/specs/2026-07-27-rulesync-drift-gate-design.md`
EOF
gh issue edit 2 --body-file /tmp/issue2.md
```

- [ ] **Step 5: Confirm**

Run: `gh issue view 2 --json title,body --jq .body | head -5`
Expected: the new text.

---

## Definition of Done

- [ ] `python -m pytest` green (expect ~1211 + the new tests).
- [ ] `ruff check sluice tests scripts` clean.
- [ ] `zizmor --offline --strict-collection .github/workflows/` no findings.
- [ ] `npm ci --ignore-scripts && npm run rulesync && git status --porcelain` → empty.
- [ ] `git grep -n '9\.6\.3'` outside `docs/superpowers/` shows only `package.json`, `package-lock.json`, `.rulesync/hooks.json`.
- [ ] Every mutant in Tasks 2 and 4 witnessed FAIL by its named test, with the rest of the suite green.
- [ ] Issue #2's body rewritten.
- [ ] `/review-pr` run **before** pushing the branch.
