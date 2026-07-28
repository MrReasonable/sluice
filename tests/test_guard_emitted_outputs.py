"""The emitted-hook guard.

This code ran as a heredoc inside `.github/workflows/ci.yml` until a security review flagged the
inline interpreter: unlinted, untestable, and a `run:` step whose command name said nothing about
what it did. These are the cases that were impossible to write while it lived in YAML.

SCOPE. The guard deliberately checks one thing -- that the emitted `settings.json` still invokes
the no-bypass guard. An earlier draft also compared agent and skill names, then their frontmatter;
each round of review then found the next uncovered field, because re-verifying a pinned
generator's whole output is unbounded. `scripts/guard_emitted_outputs.py`'s docstring records why
it stops here. Tests for the dropped checks were removed with them rather than left asserting
nothing.

Offline and hermetic -- every case builds a synthetic tree under `tmp_path`. Nothing shells out to
npm or runs the generator. The one subprocess is `sys.executable` on the guard itself, which is
how the exit-code contract gets asserted at all.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guard_emitted_outputs import hook_commands, is_guard_command, main, violations

GUARD = 'python3 "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"'
SCRIPT = Path(__file__).parent.parent / "scripts" / "guard_emitted_outputs.py"

# Distinguishes "no command key" from "an empty command": different emitted documents, and only
# one was covered before. A sentinel rather than None, because None is itself a value rulesync
# could plausibly write and must stay assertable.
OMIT = object()


def _tree(root: Path, *, command=GUARD, settings=True, event="PreToolUse", hook_type="command"):
    """A synthetic emitted tree carrying just the artifact this guard reads."""
    (root / ".claude").mkdir(parents=True)
    if settings:
        hook = {"type": hook_type}
        if command is not OMIT:
            hook["command"] = command
        doc = {"hooks": {event: [{"matcher": "Bash", "hooks": [hook]}]}}
        (root / ".claude" / "settings.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_a_faithful_tree_has_no_violations(tmp_path):
    assert violations(_tree(tmp_path)) == []


def test_a_missing_settings_file_is_a_violation(tmp_path):
    """Measured on 15.1.0: a malformed hooks.json emits the whole tree EXCEPT settings.json.

    Absence is the failure. Treating a missing file as "nothing to check" is how the guard would
    ship inert while every count still matched.
    """
    assert any("was not emitted at all" in v for v in violations(_tree(tmp_path, settings=False)))


def test_unreadable_json_is_a_violation_not_a_traceback(tmp_path):
    """One malformed artifact should name itself, not abort with a stack trace."""
    root = _tree(tmp_path)
    (root / ".claude" / "settings.json").write_text("{ not json", encoding="utf-8")
    assert any("could not be read as JSON" in v for v in violations(root))


def test_the_command_must_sit_under_the_path_claude_code_reads(tmp_path):
    """The whole reason this is not a grep.

    The command IS in the file -- under the wrong event. A substring check passes; Claude Code
    reads `PreToolUse` and nothing else, so the guard never runs.
    """
    root = _tree(tmp_path, event="PostToolUse")
    assert "guard_no_bypass.py" in (root / ".claude" / "settings.json").read_text()
    assert any("hooks.PreToolUse" in v for v in violations(root))


@pytest.mark.parametrize("command", ["", OMIT], ids=["empty-command", "no-command-key"])
def test_a_command_less_hook_is_a_violation(tmp_path, command):
    """rulesync can emit the hook structure with no command and still print `All done!`.

    BOTH shapes, because they are not the same emitted document: mutating the guard's
    `hook.get("command", "")` to `hook["command"]` survives the empty-string case green and
    raises on the missing-key one, so the absent key -- the shape `.rulesync/hooks.json`
    describes -- was going unasserted.
    """
    assert violations(_tree(tmp_path, command=command)) != []


def test_a_command_that_only_MENTIONS_the_guard_does_not_satisfy_it(tmp_path):
    """`echo <path>` contains the needle and runs nothing.

    A substring test accepts it: the inert-guard state reproduced inside the check for inert
    guards. This is what the HEAD half of the command pin buys.
    """
    root = _tree(tmp_path, command='echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"')
    assert any("only MENTIONS the guard" in v for v in violations(root))


def test_a_command_running_some_OTHER_script_does_not_satisfy_it(tmp_path):
    """What the TAIL half buys, and nothing else did.

    Deleting `command.endswith(_COMMAND_TAIL)` left the whole suite green: every other case here
    has the right head, so without this the check accepts any `python3 "…"` at all and stops
    being bound to the no-bypass guard.
    """
    root = _tree(tmp_path, command='python3 "$CLAUDE_PROJECT_DIR/scripts/something_else.py"')
    assert violations(root) != []


def test_a_hook_of_another_type_does_not_satisfy_the_guard(tmp_path):
    """Claude Code executes an entry only when its `type` says `command`.

    The command is verbatim correct and at exactly the right path; only the type differs, so
    nothing runs. A check ignoring `type` would call this tree faithful.
    """
    assert violations(_tree(tmp_path, hook_type="prompt")) != []


def test_hook_commands_reads_only_the_one_path_and_type():
    """Pure. A command nested elsewhere, or of another type, is not one Claude Code will run."""
    def doc(event="PreToolUse", hook_type="command"):
        return {"hooks": {event: [{"hooks": [{"type": hook_type, "command": "x"}]}]}}
    assert hook_commands(doc()) == ["x"]
    assert hook_commands(doc(event="PostToolUse")) == []
    assert hook_commands(doc(hook_type="prompt")) == []
    assert hook_commands({}) == []


def test_is_guard_command_needs_both_ends():
    """Each end pinned separately, so a mutant deleting either is caught here too."""
    assert is_guard_command(GUARD)
    assert not is_guard_command('echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"')
    assert not is_guard_command('python3 "$CLAUDE_PROJECT_DIR/scripts/other.py"')


def test_main_returns_zero_on_a_faithful_tree(tmp_path):
    assert main([str(_tree(tmp_path))]) == 0


def test_the_module_exits_nonzero_as_a_process(tmp_path):
    """The CI contract IS the exit code.

    A suite that only calls `main()` in-process stays green when `sys.exit(main(...))` is
    deleted -- the lesson `tests/test_guard_no_bypass.py:26-30` records. Driven with a FAILING
    tree, because a passing one cannot tell the two apart.

    Asserts the exact code AND that the root appears in stderr: `returncode != 0` alone accepts a
    nonzero from any cause, and mutating `sys.argv[1:]` to `sys.argv` survived that green -- the
    guard ran on the wrong path and the test could not tell.
    """
    root = _tree(tmp_path, settings=False)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert str(root) in proc.stderr
