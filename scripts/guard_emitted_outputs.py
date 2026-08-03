#!/usr/bin/env python3
"""Assert the emitted tree still carries the no-bypass hook, and the same agents and skills.

WHY THIS EXISTS ALONGSIDE `guard_rulesync_drift.py`. That guard counts FILES, and a count is
identical whether or not the hook it counted carries a command. This is not hypothetical, and it
is the sharper half of the argument: measured on the pinned version, feeding rulesync Claude
Code's native nested shape emits a settings.json at exactly the path Claude Code reads, carrying a
well-formed hook with NO command -- the full file set written, the `+ 1 hooks` summary term
present, and the drift guard exiting 0. (No aggregate file counts here on purpose: `EXPECTED` in
the drift guard is the one place those belong, and its own docstring records them going stale on a
bump. The quoted summary term stays -- that string is what the guard parses.) Nothing but the
emitted artifact can tell that one apart from a good run.
The other mode (omitting the top-level `hooks` record) drops `settings.json` entirely, which the
count guard DOES see. `.rulesync/hooks.json`'s comment records both, re-measured, and carries the
version record; this docstring deliberately names no version so there is only one place to update.
Either way the `rulesync` CI job is the first environment where node exists and the generator has
actually run.

WHY IT IS A SCRIPT AND NOT INLINE IN `ci.yml`. It began as a heredoc. Inline interpreter blocks
are invisible to `ruff check`, cannot be unit-tested, and leave a `run:` step whose command name
says nothing about what it does.

WHY THE NAMES ARE COMPARED BUT THE FILES ARE NOT OPENED. The five subagents and four skills ARE
this repo's merge gate, and `EXPECTED` in the drift guard pins their COUNT, not their identity: a
renamed agent leaves it 5, the tree clean (they are gitignored), and a review that silently never
runs. Measured on a real emitted tree -- renaming one agent passed every other gate. So the name
SETS are compared.

That is where it stops, deliberately. An earlier draft went on to read frontmatter back, and each
review round then correctly found the next uncovered field -- skill frontmatter, the hook
`matcher`, `CLAUDE.md` bodies -- because opening files has no natural end: it amounts to
re-verifying that a lockfile-pinned third-party generator emitted everything correctly, which is
unbounded work for a gate whose job is to make a human re-audit `.gitignore` on a bump. Comparing
two sets of names is a complete answer to a bounded question ("are the same units present?").
Reading what is inside them is not. Widening this script past that line is a decision to take on
the regress, not a small addition.

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


def _names(directory: Path, *, dirs: bool) -> set[str] | None:
    """The unit names in `directory`, or None if it does not exist. Pure.

    None rather than an empty set, and the distinction is load-bearing: with a set(), a source
    tree and an emitted tree that had BOTH vanished would compare equal and pass. Absence is a
    finding, so the caller has to handle it rather than compare it away.
    """
    if not directory.is_dir():
        return None
    if dirs:
        return {p.name for p in directory.iterdir() if p.is_dir()}
    return {p.stem for p in directory.glob("*.md")}


def _name_violations(root: Path) -> list[str]:
    """Emitted agent and skill names, against the `.rulesync/` sources they come from."""
    found: list[str] = []
    for label, emitted_dir, source_dir, dirs in (
        ("agents", root / ".claude" / "agents", root / ".rulesync" / "subagents", False),
        ("skills", root / ".claude" / "skills", root / ".rulesync" / "skills", True),
    ):
        emitted, source = _names(emitted_dir, dirs=dirs), _names(source_dir, dirs=dirs)
        for missing, path in ((emitted is None, emitted_dir), (source is None, source_dir)):
            if missing:
                found.append(
                    f"{path} does not exist. The emitted tree and its source must both be "
                    f"present for the {label} comparison to mean anything; a vanished directory "
                    "is a finding, not an empty comparison."
                )
        if emitted is None or source is None or emitted == source:
            continue
        found.append(
            f"the emitted .claude/{label}/ does not match its .rulesync/ source.\n"
            f"  in the source but NOT emitted: {sorted(source - emitted)}\n"
            f"  emitted but NOT in the source: {sorted(emitted - source)}\n"
            "The file COUNT can match exactly while the names do not, so the drift guard cannot "
            "see this. These are the review agents and skills this repo's merge gate is built "
            "from: a renamed or dropped one is a review that never runs, with nothing red "
            "anywhere to say so."
        )
    return found


def violations(root: Path) -> list[str]:
    """Every reason the emitted tree would leave a gate inert. Pure; empty means faithful."""
    return [*_hook_violations(root), *_name_violations(root)]


def _hook_violations(root: Path) -> list[str]:
    """Every reason the emitted settings.json would leave the no-bypass guard inert."""
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.is_file():
        # Re-measured on the pinned version: a hooks.json missing its top-level `hooks` record
        # emits CLAUDE.md, AGENTS.md and .claude/ but no settings.json at all -- a near-complete
        # tree missing only the guard. Absence is the failure, not a reason to skip the check.
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
