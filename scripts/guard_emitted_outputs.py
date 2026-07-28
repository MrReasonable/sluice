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

WHY THE NAMES ARE NOT ENOUGH, AND THE FILES ARE OPENED. The FILENAME is a pass-through -- rulesync
copies it -- while the frontmatter is REGENERATED on emit. Measured on the pinned version: every
source's `targets:` key is stripped, so the block that reaches disk is the generator's, not the
repo's. Claude Code dispatches on the frontmatter `name:`, not on the filename. A version that
mangled `name:` would therefore emit five correctly-named files and register ZERO agents -- stems
all matching, this guard green, the merge gate silently gone. Measured the same way: a zero-byte
agent and a skill directory holding no `SKILL.md` both passed a check that only listed names. So
every emitted agent's declared name is read back, and every emitted skill must carry a real
`SKILL.md`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The one path Claude Code reads. A command anywhere else in the file never runs.
_HOOK_EVENT = "PreToolUse"
_REQUIRED_COMMAND = "guard_no_bypass.py"

# The guard invocation, pinned either side of the one part that legitimately varies. Claude Code
# expands `$CLAUDE_PROJECT_DIR` at run time and rulesync writes it through literally, but a tree
# carrying a resolved absolute path there would still be correct, so the middle is tolerated.
#
# WHY NOT A BARE SUBSTRING. `guard_no_bypass.py` appearing anywhere in the command was the whole
# test, and `echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"` satisfies it while running
# nothing -- the inert-guard state this script exists to reject, reproduced inside the check for
# it. tests/test_hooks_wiring.py pins the SOURCE command byte-exactly for exactly this reason;
# these two constants are its counterpart on the emitted artifact.
#
# WHY NOT BYTE-EXACT EQUALITY EITHER. That would red a legitimate tree the moment the interpreter
# path or the project-dir spelling changed, which is a re-verification prompt, not a defect.
_COMMAND_HEAD = 'python3 "'
_COMMAND_TAIL = f'/scripts/{_REQUIRED_COMMAND}"'

_FRONTMATTER_FENCE = "---"
_NAME_KEY = "name:"


def hook_commands(settings: dict) -> list[str]:
    """Every `command` at `hooks.PreToolUse[*].hooks[*]` whose `type` is `command`. Pure.

    Deliberately reads only that path: finding the command elsewhere in the document is not a
    pass, it is the inert-guard failure this exists to catch. The `type` filter is the same
    argument one level down -- Claude Code executes a hook entry only when its type says
    `command`, so an entry of any other type carries the string and never runs it.

    A hook entry with no `command` key yields `""` rather than raising. rulesync emits exactly
    that shape when fed the wrong input schema, and it has to reach the caller as a value that
    fails the check, not as a KeyError that stops the other checks from running at all.
    """
    return [
        hook.get("command", "")
        for matcher in settings.get("hooks", {}).get(_HOOK_EVENT, [])
        for hook in matcher.get("hooks", [])
        if hook.get("type") == "command"
    ]


def is_guard_command(command: str) -> bool:
    """Whether this command actually RUNS the no-bypass guard. Pure. See `_COMMAND_HEAD`."""
    return command.startswith(_COMMAND_HEAD) and command.endswith(_COMMAND_TAIL)


def frontmatter_name(text: str) -> str | None:
    """The top-level `name:` of a Markdown YAML frontmatter block, or None. Pure.

    Line-scanned rather than YAML-parsed on purpose. This repo adds no dependency it does not
    guard behind a try/except ImportError, and a guard that cannot run because an import failed
    is a guard that is not running.

    Matched at COLUMN 0 only, which is what keeps the line scan honest: the emitted agents carry
    a folded `description: >-` whose continuation lines are indented, so prose containing "name:"
    can never be mistaken for the key.

    One `None` covers every unusable shape -- empty file, no opening fence, an unterminated
    block, a block with no `name:`, a `name:` with an empty value -- because the caller's next
    move is identical for all of them: name the file and say what was expected.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    closing = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == _FRONTMATTER_FENCE),
        None,
    )
    if closing is None:
        return None
    for line in lines[1:closing]:
        if line.startswith(_NAME_KEY):
            return line[len(_NAME_KEY) :].strip().strip("\"'") or None
    return None


def _names(directory: Path, *, dirs: bool) -> set[str]:
    """The member names of a directory the caller has ALREADY proved exists.

    No missing-directory fallback, deliberately. Returning `set()` for an absent directory made a
    vanished source compare EQUAL to a vanished emitted tree: probed, deleting both
    `.rulesync/subagents/` and `.claude/agents/` produced no violation at all. Absence is now the
    caller's own check, and this cannot manufacture an empty set to compare.
    """
    if dirs:
        return {p.name for p in directory.iterdir() if p.is_dir()}
    return {p.stem for p in directory.glob("*.md")}


def _hook_violations(root: Path) -> list[str]:
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.is_file():
        # Measured on 15.1.0: a malformed hooks.json emits CLAUDE.md, AGENTS.md and .claude/
        # but no settings.json at all -- a near-complete tree missing only the guard. Absence
        # is the failure, not a reason to skip the check.
        return [
            f"{settings_path} was not emitted at all. rulesync wrote the rest of the tree, so "
            "this is a dropped hook rather than a failed run: the no-bypass guard would ship "
            "INERT. Re-verify .rulesync/hooks.json against this rulesync version's schema."
        ]

    commands = hook_commands(json.loads(settings_path.read_text(encoding="utf-8")))
    if any(is_guard_command(command) for command in commands):
        return []
    return [
        f"no command that RUNS {_REQUIRED_COMMAND} sits under hooks.{_HOOK_EVENT}[*].hooks[*] "
        f"with type `command`.\n  commands found at that path: {commands}\n"
        "Either the command was dropped, or this rulesync version nests hooks somewhere else, "
        "or what is there only mentions the guard without executing it. All three ship the "
        "no-bypass guard INERT -- Claude Code reads that path, runs only `command`-typed entries, "
        f"and nothing else.\n  expected a command starting {_COMMAND_HEAD!r} and ending "
        f"{_COMMAND_TAIL!r}."
    ]


def _name_violations(root: Path) -> list[str]:
    """The emitted name sets against their `.rulesync/` sources, plus the existence of all four."""
    found: list[str] = []
    for label, emitted_dir, source_dir, dirs in (
        ("agents", root / ".claude" / "agents", root / ".rulesync" / "subagents", False),
        ("skills", root / ".claude" / "skills", root / ".rulesync" / "skills", True),
    ):
        absent = [str(d) for d in (source_dir, emitted_dir) if not d.is_dir()]
        if absent:
            found.append(
                f"{label}: {', '.join(absent)} does not exist.\n"
                "A missing directory is its own failure, never an empty set to compare against. "
                "Reading both sides as empty made a vanished source EQUAL a vanished emitted "
                "tree, so losing the entire merge gate raised no violation at all."
            )
            continue

        emitted, source = _names(emitted_dir, dirs=dirs), _names(source_dir, dirs=dirs)
        if emitted != source:
            found.append(
                f"the emitted .claude/{label}/ does not match its .rulesync/ source.\n"
                f"  in the source but NOT emitted: {sorted(source - emitted)}\n"
                f"  emitted but NOT in the source: {sorted(emitted - source)}\n"
                "The file count can match exactly while the names do not. These are the review "
                "agents and skills this repo's merge gate is built from, so a renamed or dropped "
                "one is a review that never runs, with nothing red anywhere to say so.\n"
                "This compares the FULL source set, which assumes every .rulesync/ source targets "
                "claudecode -- true of every one of them today. A source deliberately scoped away "
                "from claudecode would surface here as 'in the source but NOT emitted', and wants "
                "its scope read rather than a name restored."
            )
    return found


def _agent_content_violations(agents_dir: Path) -> list[str]:
    """Every emitted agent whose declared name is not the one Claude Code will register it under.

    An absent directory is `_name_violations`' report to make; saying it twice helps nobody.
    """
    if not agents_dir.is_dir():
        return []
    found: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        name = frontmatter_name(path.read_text(encoding="utf-8", errors="replace"))
        if name != path.stem:
            found.append(
                f"{path} declares name {name!r}, so Claude Code registers it under that -- not "
                f"under the filename {path.stem!r} this guard's name check compares.\n"
                "The filename is a PASS-THROUGH (rulesync copies it) while the frontmatter is "
                "REGENERATED on emit -- measured: the source's `targets:` key is stripped there. "
                "So a version that mangled `name:` emits correctly-named files and registers "
                "NOTHING, with every stem matching. `None` means the file is empty or its "
                "frontmatter is unusable, which registers nothing just as surely."
            )
    return found


def _skill_content_violations(skills_dir: Path) -> list[str]:
    """Every emitted skill directory carrying no usable `SKILL.md`.

    A skill IS its `SKILL.md`; the directory is only where it lives. Listing directory names --
    all the name check can do -- cannot see inside one, so an empty directory has the right name
    and loads nothing: the same silent-inert failure as a renamed agent.
    """
    if not skills_dir.is_dir():
        return []
    found: list[str] = []
    for directory in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill = directory / "SKILL.md"
        if not skill.is_file() or skill.stat().st_size == 0:
            found.append(
                f"{directory} holds no non-empty SKILL.md. The directory NAME is all the name "
                "check sees, so an empty one passes it while the skill itself loads nothing."
            )
    return found


def violations(root: Path) -> list[str]:
    """Every content mismatch between `.rulesync/` and what was emitted. Pure given the tree.

    Returns a list of human-readable problems; empty means the emitted tree is faithful.
    """
    return [
        *_hook_violations(root),
        *_name_violations(root),
        *_agent_content_violations(root / ".claude" / "agents"),
        *_skill_content_violations(root / ".claude" / "skills"),
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path.cwd()
    found = violations(root)
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
