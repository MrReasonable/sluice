#!/usr/bin/env python3
"""Assert the CONTENT of what `rulesync generate` emitted, not merely how much of it.

WHY THIS EXISTS ALONGSIDE `guard_rulesync_drift.py`. That guard counts FILES, and a count is
identical whether or not the hook it counted carries a command, and identical whether the agents
emitted are this repo's or somebody else's. Only the emitted artifact can tell those apart.

WHY IT IS A SCRIPT AND NOT INLINE IN `ci.yml`. It began as a heredoc in the workflow. Inline
interpreter blocks are invisible to `ruff check`, cannot be unit-tested, and leave a `run:` step
whose command name says nothing about what it does. Here it is linted with everything else and
covered by `tests/test_guard_emitted_outputs.py`, and the workflow gets a named command back.

WHY THE HOOK CHECK IS STRUCTURAL. `grep -q guard_no_bypass.py .claude/settings.json` proves only
that the STRING is somewhere in the file. A rulesync that re-nested the command under a different
event -- or under none -- still matches, while Claude Code, which reads exactly one path, runs
nothing. Measured on the 9.6.3 -> 15.1.0 bump: the emitted shape held, but the shape of the
FAILURE moved, so the check asserts the path rather than the substring.

WHY THE NAMES ARE CHECKED. The five subagents and four skills are this repo's merge gate. A
renamed or dropped one is a review that silently never runs, and the file count stays 5 and 4
throughout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The one path Claude Code reads. A command anywhere else in the file never runs.
_HOOK_EVENT = "PreToolUse"
_REQUIRED_COMMAND = "guard_no_bypass.py"


def hook_commands(settings: dict) -> list[str]:
    """Every command at `hooks.PreToolUse[*].hooks[*].command`. Pure.

    Deliberately reads only that path: finding the command elsewhere in the document is not a
    pass, it is the inert-guard failure this exists to catch.
    """
    return [
        hook.get("command", "")
        for matcher in settings.get("hooks", {}).get(_HOOK_EVENT, [])
        for hook in matcher.get("hooks", [])
    ]


def _names(directory: Path, *, dirs: bool) -> set[str]:
    if not directory.is_dir():
        return set()
    if dirs:
        return {p.name for p in directory.iterdir() if p.is_dir()}
    return {p.stem for p in directory.glob("*.md")}


def violations(root: Path) -> list[str]:
    """Every content mismatch between `.rulesync/` and what was emitted. Pure given the tree.

    Returns a list of human-readable problems; empty means the emitted tree is faithful.
    """
    found: list[str] = []

    settings_path = root / ".claude" / "settings.json"
    if not settings_path.is_file():
        # Measured on 15.1.0: a malformed hooks.json emits CLAUDE.md, AGENTS.md and .claude/
        # but no settings.json at all -- a near-complete tree missing only the guard. Absence
        # is the failure, not a reason to skip the check.
        found.append(
            f"{settings_path} was not emitted at all. rulesync wrote the rest of the tree, so "
            "this is a dropped hook rather than a failed run: the no-bypass guard would ship "
            "INERT. Re-verify .rulesync/hooks.json against this rulesync version's schema."
        )
    else:
        commands = hook_commands(json.loads(settings_path.read_text(encoding="utf-8")))
        if not any(_REQUIRED_COMMAND in command for command in commands):
            found.append(
                f"no {_REQUIRED_COMMAND} command sits under hooks.{_HOOK_EVENT}[*].hooks[*]"
                f".command.\n  commands found at that path: {commands}\n"
                "Either the command was dropped, or this rulesync version nests hooks somewhere "
                "else. Both ship the no-bypass guard INERT -- Claude Code reads that path and "
                "nothing else, so a command sitting elsewhere in the file never runs."
            )

    for label, emitted, source in (
        ("agents", _names(root / ".claude" / "agents", dirs=False),
         _names(root / ".rulesync" / "subagents", dirs=False)),
        ("skills", _names(root / ".claude" / "skills", dirs=True),
         _names(root / ".rulesync" / "skills", dirs=True)),
    ):
        if emitted != source:
            found.append(
                f"the emitted .claude/{label}/ does not match its .rulesync/ source.\n"
                f"  in the source but NOT emitted: {sorted(source - emitted)}\n"
                f"  emitted but NOT in the source: {sorted(emitted - source)}\n"
                "The file count can match exactly while the names do not. These are the review "
                "agents and skills this repo's merge gate is built from, so a renamed or dropped "
                "one is a review that never runs, with nothing red anywhere to say so."
            )

    return found


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path.cwd()
    found = violations(root)
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
