#!/usr/bin/env python3
"""Assert the emitted `.claude/settings.json` still carries the no-bypass hook command.

WHY THIS EXISTS ALONGSIDE `guard_rulesync_drift.py`. That guard counts FILES, and a count is
identical whether or not the hook it counted carries a command. `.rulesync/hooks.json` records
the failure precisely: feed rulesync the wrong schema and it "skips the event AND silently drops
the command", printing `All done!` and exiting 0. Measured again on the 9.6.3 -> 15.1.0 bump,
where the failure SHAPE moved: 9.6.3 wrote nothing, 15.1.0 writes the whole tree minus
`settings.json`. Only the emitted artifact can tell, and the `rulesync` CI job is the first
environment where node exists and the generator has actually run.

WHY IT IS A SCRIPT AND NOT INLINE IN `ci.yml`. It began as a heredoc. Inline interpreter blocks
are invisible to `ruff check`, cannot be unit-tested, and leave a `run:` step whose command name
says nothing about what it does.

WHY IT CHECKS ONLY THIS, AND DELIBERATELY STOPS. An earlier draft also compared emitted agent and
skill NAMES against `.rulesync/`, then read their frontmatter back. Reviewers then correctly found
the next uncovered field each round -- skill frontmatter, the hook `matcher`, `CLAUDE.md` bodies --
because that regress has no natural end. It amounts to re-verifying that a lockfile-pinned
third-party generator emitted everything correctly, which is unbounded work for a gate whose job
is to make a human re-audit `.gitignore` when a bump is due. The bounded question is the one
`.rulesync/hooks.json` actually documents, and this answers it. Widening this script again is a
decision to take on that regress, not a small addition.

WHY IT IS STRUCTURAL, NOT A GREP. `grep -q guard_no_bypass.py .claude/settings.json` proves only
that the STRING is somewhere in the file. A rulesync that re-nested the command under a different
event still matches while Claude Code, which reads exactly one path, runs nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The one path Claude Code reads. A command anywhere else in the file never runs.
_HOOK_EVENT = "PreToolUse"
_REQUIRED_COMMAND = "guard_no_bypass.py"
# Claude Code executes an entry only when its `type` says so.
_COMMAND_TYPE = "command"

# The guard invocation, pinned either side of the one part that legitimately varies. Claude Code
# expands `$CLAUDE_PROJECT_DIR` at run time and rulesync writes it through literally, but a tree
# carrying a resolved absolute path there would still be correct, so the middle is tolerated.
#
# BOTH ENDS ARE LOAD-BEARING, and each has its own test. Without the TAIL, any `python3 "…"`
# passes and the check stops being bound to the no-bypass guard at all. Without the HEAD,
# `echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"` passes and runs nothing -- the
# inert-guard state this script exists to reject, reproduced inside the check for it.
# `tests/test_hooks_wiring.py` pins the SOURCE command byte-exactly for the same reason; these
# two constants are its counterpart on the emitted artifact.
#
# WHY NOT BYTE-EXACT EQUALITY. That would red a legitimate tree the moment the interpreter path or
# the project-dir spelling changed, which is a re-verification prompt, not a defect.
_COMMAND_HEAD = 'python3 "'
_COMMAND_TAIL = f'/scripts/{_REQUIRED_COMMAND}"'


def hook_commands(settings: dict) -> list[str]:
    """Every `type: command` entry at `hooks.PreToolUse[*].hooks[*].command`. Pure.

    Deliberately reads only that path and only that type: a command found elsewhere in the
    document, or under a type Claude Code does not execute, is not a pass.
    """
    return [
        hook.get("command", "")
        for matcher in settings.get("hooks", {}).get(_HOOK_EVENT, [])
        for hook in matcher.get("hooks", [])
        if hook.get("type") == _COMMAND_TYPE
    ]


def is_guard_command(command: str) -> bool:
    """Does this string actually INVOKE the no-bypass guard, rather than merely name it? Pure."""
    return command.startswith(_COMMAND_HEAD) and command.endswith(_COMMAND_TAIL)


def violations(root: Path) -> list[str]:
    """Every reason the emitted settings.json would leave the no-bypass guard inert.

    Pure given the tree; empty means the emitted hook is faithful.
    """
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.is_file():
        # Measured on 15.1.0: a malformed hooks.json emits CLAUDE.md, AGENTS.md and .claude/ but
        # no settings.json at all -- a near-complete tree missing only the guard. Absence is the
        # failure, not a reason to skip the check.
        return [
            f"{settings_path} was not emitted at all. rulesync wrote the rest of the tree, so "
            "this is a dropped hook rather than a failed run: the no-bypass guard would ship "
            "INERT. Re-verify .rulesync/hooks.json against this rulesync version's schema."
        ]

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A violation rather than a traceback. An unreadable or malformed artifact is exactly the
        # "the generator did something new" case worth naming in the guard's own prose.
        return [f"{settings_path} could not be read as JSON: {exc}"]

    commands = hook_commands(settings)
    if any(is_guard_command(command) for command in commands):
        return []

    mentions = [command for command in commands if _REQUIRED_COMMAND in command]
    detail = (
        f"a command at that path only MENTIONS the guard without invoking it: {mentions}"
        if mentions
        else f"commands found at that path: {commands}"
    )
    return [
        f"no command invoking {_REQUIRED_COMMAND} sits under hooks.{_HOOK_EVENT}[*].hooks[*]"
        f".command with type {_COMMAND_TYPE!r}.\n  {detail}\n"
        "Either the command was dropped, or this rulesync version nests hooks somewhere else. "
        "Both ship the no-bypass guard INERT -- Claude Code reads that path and nothing else, so "
        "a command sitting elsewhere in the file never runs. Re-verify .rulesync/hooks.json "
        "against this rulesync version's hook schema before touching anything else."
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path.cwd()
    found = violations(root)
    for problem in found:
        # The root is in every line so the subprocess test can prove the guard ran on the tree it
        # was handed, rather than merely exiting non-zero for some other reason.
        print(f"{root}: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
