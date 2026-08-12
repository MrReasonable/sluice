"""The emitted-hook guard.

This code ran as a heredoc inside `.github/workflows/ci.yml` until a security review flagged the
inline interpreter: unlinted, untestable, and a `run:` step whose command name said nothing about
what it did. These are the cases that were impossible to write while it lived in YAML.

SCOPE. Two bounded checks: the emitted `settings.json` still INVOKES the no-bypass guard, and the
emitted agent and skill NAME SETS match their `.rulesync/` sources. An earlier draft went further
and read frontmatter back, after which each review round found the next uncovered field, because
opening files has no end. Comparing two sets of names does -- it is a complete answer to "are the
same units present?". `scripts/guard_emitted_outputs.py`'s docstring records where the line is and
why. Tests for the dropped content checks were removed with them rather than left asserting
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
EGRESS_GUARD = 'python3 "$CLAUDE_PROJECT_DIR/scripts/guard_reviewer_egress.py"'
SCRIPT = Path(__file__).parent.parent / "scripts" / "guard_emitted_outputs.py"

# Distinguishes "no command key" from "an empty command": different emitted documents, and only
# one was covered before. A sentinel rather than None, because None is itself a value rulesync
# could plausibly write and must stay assertable.
OMIT = object()


def _tree(root: Path, *, commands=(GUARD, EGRESS_GUARD), settings=True, event="PreToolUse",
          hook_type="command", agents=("a", "b"), skills=("s",)):
    """A synthetic emitted tree. Sources and emitted names agree unless a test diverges them.

    `commands` defaults to BOTH real guards, matching the real emitted tree: a faithful tree
    must carry both, not just the one this file used to check.
    """
    (root / ".claude").mkdir(parents=True)
    (root / ".rulesync" / "subagents").mkdir(parents=True)
    (root / ".claude" / "agents").mkdir(parents=True)
    for name in agents:
        (root / ".rulesync" / "subagents" / f"{name}.md").write_text("x", encoding="utf-8")
        (root / ".claude" / "agents" / f"{name}.md").write_text("x", encoding="utf-8")
    for name in skills:
        (root / ".rulesync" / "skills" / name).mkdir(parents=True)
        (root / ".claude" / "skills" / name).mkdir(parents=True)
    if settings:
        hooks = []
        for command in commands:
            hook = {"type": hook_type}
            if command is not OMIT:
                hook["command"] = command
            hooks.append(hook)
        doc = {"hooks": {event: [{"matcher": "Bash", "hooks": hooks}]}}
        (root / ".claude" / "settings.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_a_faithful_tree_has_no_violations(tmp_path):
    assert violations(_tree(tmp_path)) == []


def test_a_missing_settings_file_is_a_violation(tmp_path):
    """Re-measured on the pinned version: a hooks.json missing its top-level `hooks` record
    emits the whole tree EXCEPT settings.json.

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
    describes -- was going unasserted. The OTHER guard stays correct, so this also proves the
    check is PER-GUARD: a faithful second hook must not paper over a broken first one.
    """
    assert violations(_tree(tmp_path, commands=(command, EGRESS_GUARD))) != []


def test_a_command_that_only_MENTIONS_the_guard_does_not_satisfy_it(tmp_path):
    """`echo <path>` contains the needle and runs nothing.

    A substring test accepts it: the inert-guard state reproduced inside the check for inert
    guards. This is what the HEAD half of the command pin buys.
    """
    root = _tree(
        tmp_path,
        commands=('echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"', EGRESS_GUARD),
    )
    assert any("only MENTIONS the guard" in v for v in violations(root))


def test_a_command_running_some_OTHER_script_does_not_satisfy_it(tmp_path):
    """What the TAIL half buys, and nothing else did.

    Deleting the tail check left the whole suite green: every other case here has the right
    head, so without this the check accepts any `python3 "…"` at all and stops being bound to
    a specific guard.
    """
    root = _tree(
        tmp_path,
        commands=('python3 "$CLAUDE_PROJECT_DIR/scripts/something_else.py"', EGRESS_GUARD),
    )
    assert violations(root) != []


def test_a_hook_of_another_type_does_not_satisfy_the_guard(tmp_path):
    """Claude Code executes an entry only when its `type` says `command`.

    The command is verbatim correct and at exactly the right path; only the type differs, so
    nothing runs. A check ignoring `type` would call this tree faithful.
    """
    assert violations(_tree(tmp_path, hook_type="prompt")) != []


def test_the_reviewer_egress_guard_is_checked_independently(tmp_path):
    """The no-bypass guard being present and correct must not paper over the egress guard
    being missing -- the two are independent gates (#102), checked independently.
    """
    root = _tree(
        tmp_path,
        commands=(GUARD, 'python3 "$CLAUDE_PROJECT_DIR/scripts/something_else.py"'),
    )
    found = violations(root)
    assert any("guard_reviewer_egress.py" in v for v in found)
    assert not any(
        "no command invoking guard_no_bypass.py" in v for v in found
    ), "the correct guard_no_bypass.py hook must not be reported as missing"


def test_hook_commands_reads_only_the_one_path_and_type():
    """Pure. A command nested elsewhere, or of another type, is not one Claude Code will run."""
    def doc(event="PreToolUse", hook_type="command"):
        return {"hooks": {event: [{"hooks": [{"type": hook_type, "command": "x"}]}]}}
    assert hook_commands(doc()) == ["x"]
    assert hook_commands(doc(event="PostToolUse")) == []
    assert hook_commands(doc(hook_type="prompt")) == []
    assert hook_commands({}) == []


def test_is_guard_command_needs_both_ends():
    """Each end pinned separately, so a mutant deleting either is caught here too. Checked for
    BOTH guard scripts, since `script_name` is now an explicit argument rather than a shared
    constant -- a mutant hardcoding the wrong script name would pass one and fail the other.
    """
    assert is_guard_command(GUARD, "guard_no_bypass.py")
    assert not is_guard_command(
        'echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"', "guard_no_bypass.py"
    )
    assert not is_guard_command(
        'python3 "$CLAUDE_PROJECT_DIR/scripts/other.py"', "guard_no_bypass.py"
    )
    assert is_guard_command(EGRESS_GUARD, "guard_reviewer_egress.py")
    assert not is_guard_command(GUARD, "guard_reviewer_egress.py")
    assert not is_guard_command(EGRESS_GUARD, "guard_no_bypass.py")


def test_a_renamed_agent_is_caught_though_the_COUNT_is_unchanged(tmp_path):
    """Five agents before, five after -- `EXPECTED` in the drift guard sees nothing.

    Measured on the real emitted tree before this check existed: renaming one agent passed the
    drift guard (count still 5), passed this guard, and left `git status` clean because the
    emitted tree is gitignored. A review agent that IS the merge gate could vanish unnoticed.
    """
    root = _tree(tmp_path)
    (root / ".claude" / "agents" / "b.md").rename(root / ".claude" / "agents" / "typo.md")
    found = violations(root)
    assert any("does not match its .rulesync/ source" in v for v in found)
    assert any("'b'" in v and "'typo'" in v for v in found)


def test_a_renamed_skill_is_caught(tmp_path):
    root = _tree(tmp_path)
    (root / ".claude" / "skills" / "s").rename(root / ".claude" / "skills" / "other")
    assert any("skills/" in v for v in violations(root))


@pytest.mark.parametrize(
    "gone", [".claude/agents", ".rulesync/subagents"], ids=["emitted", "source"]
)
def test_a_vanished_directory_is_a_finding_not_an_empty_comparison(tmp_path, gone):
    """Both sides missing would compare equal with a `set()` fallback, and pass.

    That is why `_names` returns None: a comparison whose operands do not exist is not evidence
    of agreement. Each side is asserted separately so a mutant restoring the fallback reds here.
    """
    root = _tree(tmp_path)
    for path in (root / gone).iterdir():
        path.unlink()
    (root / gone).rmdir()
    assert any("does not exist" in v for v in violations(root))


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
