#!/usr/bin/env python3
"""Clear rulesync's own `hooks` key from `.claude/settings.json` before generation runs.

WHY THIS FILE IS TRACKED AT ALL. Every other rulesync output is gitignored (see .gitignore's
generated-output block) so a broken generate leaves it simply ABSENT -- exactly the signal
`guard_emitted_outputs.py` and `guard_rulesync_drift.py` rely on. `.claude/settings.json` is
the one exception: Claude Code's own `enabledPlugins` key -- written by `/plugin marketplace
add`/`/plugin install`, never by rulesync -- lives in the SAME shared file, and tracking it is
the only way a plugin enable reaches every worktree and contributor rather than staying one
machine's private config. rulesync's hooks writer merges additively (verified: regenerating
against a file already carrying `enabledPlugins` leaves it untouched), so the file can carry
both a rulesync-owned key and a hand-authored one without conflict -- ordinarily.

WHY THAT BREAKS TWO EXISTING GUARDS ON A FRESH CHECKOUT. Once the file is tracked, a CI
checkout always provides *some* copy before `npm run rulesync` runs -- and if that copy
already matches what rulesync would write, rulesync SKIPS it (its own documented behaviour:
`guard_rulesync_drift.py`'s "THE COUNTS ARE ONLY VALID ON A FRESH TREE" note). Measured: a
settings.json that is already correct produces `Written 11 file(s) total (2 rules + 5
subagents + 4 skills)` -- `hooks` silently OMITTED from the summary rather than printed as
`0 hooks` -- which mismatches `guard_rulesync_drift.py`'s pinned `EXPECTED["hooks"] = 1` on
every single healthy run. And if generation were ever silently broken instead (the
`.rulesync/hooks.json` failure mode that drops only settings.json -- exit 0 until 16.18.0,
which exits 1; the stale-copy hazard below is the same either way -- documented
there and in `tests/test_hooks_wiring.py`), the stale checked-out copy would sit there looking
structurally valid, and `guard_emitted_outputs.py` -- which checks structure, not whether this
run actually produced it -- would pass having verified nothing about the current run at all.

THE FIX. Clear only the `hooks` key -- the one part rulesync actually owns -- before
generation runs, leaving `enabledPlugins` (and any future hand-authored key) untouched.
rulesync is then FORCED to rewrite `hooks` fresh every run, which restores both guarantees:
the count guard sees a real `1 hooks` every time, and a broken generate genuinely leaves the
key absent rather than falling back to a stale copy. Verified end-to-end against a simulated
fresh checkout (CLAUDE.md/AGENTS.md/.claude/agents/.claude/skills absent, settings.json
carrying only `enabledPlugins`): `npm run rulesync` reports `Written 12 file(s) total (2
rules + 5 subagents + 4 skills + 1 hooks)`, `guard_rulesync_drift.py` passes, and the
resulting file is byte-identical to the committed one -- which is also why the committed file
orders `enabledPlugins` before `hooks`: rulesync APPENDS the key it writes rather than
preserving original position, so the opposite order would never reach a stable fixed point.

Idempotent and side-effect-free when the file is already hooks-less or absent, so running it
twice, or against a checkout that predates tracking this file at all, is never an error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SETTINGS_PATH = Path(".claude") / "settings.json"


def strip_hooks(document: dict) -> dict:
    """`document` with its `hooks` key removed, if present. Pure; never mutates the input."""
    return {key: value for key, value in document.items() if key != "hooks"}


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path.cwd()
    path = root / _SETTINGS_PATH
    if not path.is_file():
        # Nothing to clear -- either a checkout that predates tracking this file, or a
        # from-scratch run. Either way rulesync writes it fresh, which is the goal.
        return 0
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{path} could not be read as JSON, leaving it untouched: {exc}", file=sys.stderr)
        return 0
    if not isinstance(document, dict):
        # Valid JSON, wrong shape (a list, a string, a number...). `strip_hooks` calls
        # `.items()` unconditionally and would crash on any of those -- same posture as the
        # malformed-JSON case above: this step doesn't own the content, so it leaves it
        # untouched rather than aborting the pipeline over something a downstream guard
        # (`guard_emitted_outputs.py`) is what actually judges.
        print(f"{path} is not a JSON object, leaving it untouched.", file=sys.stderr)
        return 0
    stripped = strip_hooks(document)
    if stripped == document:
        return 0
    path.write_text(json.dumps(stripped, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
