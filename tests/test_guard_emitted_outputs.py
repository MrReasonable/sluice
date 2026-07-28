"""The emitted-output content guard.

This code ran as a heredoc inside `.github/workflows/ci.yml` until a security review flagged the
inline interpreter: unlinted, untestable, and a `run:` step whose command name said nothing about
what it did. These are the cases that were impossible to write while it lived in YAML.

Offline and hermetic throughout -- every case builds a synthetic tree under `tmp_path`. Nothing
here shells out to npm or runs the generator.
"""

import json
from pathlib import Path

import pytest

from scripts.guard_emitted_outputs import hook_commands, main, violations

GUARD = 'python3 "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"'


def _tree(root: Path, *, command=GUARD, agents=("a", "b"), skills=("s",), settings=True,
          event="PreToolUse"):
    """A synthetic emitted tree. Sources and emitted names agree unless a test diverges them."""
    (root / ".rulesync" / "subagents").mkdir(parents=True)
    (root / ".claude" / "agents").mkdir(parents=True)
    for name in agents:
        (root / ".rulesync" / "subagents" / f"{name}.md").write_text("x", encoding="utf-8")
        (root / ".claude" / "agents" / f"{name}.md").write_text("x", encoding="utf-8")
    for name in skills:
        (root / ".rulesync" / "skills" / name).mkdir(parents=True)
        (root / ".claude" / "skills" / name).mkdir(parents=True)
    if settings:
        doc = {"hooks": {event: [{"matcher": "Bash", "hooks": [{"type": "command",
                                                                "command": command}]}]}}
        (root / ".claude" / "settings.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_a_faithful_tree_has_no_violations(tmp_path):
    assert violations(_tree(tmp_path)) == []


def test_a_missing_settings_file_is_a_violation(tmp_path):
    """Measured on 15.1.0: a malformed hooks.json emits the whole tree EXCEPT settings.json.

    Absence is the failure. Treating a missing file as "nothing to check" is how the guard
    would ship inert while every count still matched.
    """
    found = violations(_tree(tmp_path, settings=False))
    assert any("was not emitted at all" in v for v in found)


def test_the_command_must_sit_under_the_path_claude_code_reads(tmp_path):
    """The whole reason this is not a grep.

    The command IS in the file -- under the wrong event. A substring check passes; Claude Code
    reads `PreToolUse` and nothing else, so the guard never runs.
    """
    root = _tree(tmp_path, event="PostToolUse")
    # The needle a grep would use, matched against the raw bytes exactly as `grep` would see
    # them. `guard_no_bypass.py` rather than the whole command because json.dumps escapes the
    # inner quotes -- which is itself a reason a substring check is a poor way to assert this.
    assert "guard_no_bypass.py" in (root / ".claude" / "settings.json").read_text()
    assert any("hooks.PreToolUse" in v for v in violations(root))


def test_a_command_less_hook_is_a_violation(tmp_path):
    """rulesync can emit the hook structure with no command and still print `All done!`."""
    assert any("no guard_no_bypass.py" in v for v in violations(_tree(tmp_path, command="")))


def test_a_renamed_agent_is_caught_though_the_COUNT_is_unchanged(tmp_path):
    """Two agents before, two after -- a file count sees nothing. The merge gate lost one."""
    root = _tree(tmp_path)
    (root / ".claude" / "agents" / "b.md").rename(root / ".claude" / "agents" / "typo.md")
    found = violations(root)
    assert any("does not match its .rulesync/ source" in v for v in found)
    assert any("'b'" in v and "'typo'" in v for v in found)


def test_a_renamed_skill_is_caught(tmp_path):
    root = _tree(tmp_path)
    (root / ".claude" / "skills" / "s").rename(root / ".claude" / "skills" / "other")
    assert any("skills/" in v for v in violations(root))


def test_hook_commands_reads_only_the_one_path():
    """Pure. A command nested anywhere else is not a command Claude Code will run."""
    assert hook_commands({"hooks": {"PreToolUse": [{"hooks": [{"command": "x"}]}]}}) == ["x"]
    assert hook_commands({"hooks": {"PostToolUse": [{"hooks": [{"command": "x"}]}]}}) == []
    assert hook_commands({}) == []


@pytest.mark.parametrize("settings", [True, False])
def test_main_exit_code_is_the_contract(tmp_path, settings):
    """CI reads the exit code and nothing else."""
    assert main([str(_tree(tmp_path, settings=settings))]) == (0 if settings else 1)
