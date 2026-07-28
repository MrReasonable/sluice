"""The emitted-output content guard.

This code ran as a heredoc inside `.github/workflows/ci.yml` until a security review flagged the
inline interpreter: unlinted, untestable, and a `run:` step whose command name said nothing about
what it did. These are the cases that were impossible to write while it lived in YAML.

Offline and hermetic throughout -- every case builds a synthetic tree under `tmp_path`. Nothing
here shells out to npm or runs the generator. The one subprocess is `sys.executable` on the guard
itself, which is how the exit-code contract gets asserted at all.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guard_emitted_outputs import frontmatter_name, hook_commands, main, violations

GUARD = 'python3 "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"'
SCRIPT = Path(__file__).parent.parent / "scripts" / "guard_emitted_outputs.py"

# Distinguishes "no command key" from "an empty command", which are different emitted shapes and
# were collapsed into one case before. A sentinel rather than None: None is itself a value
# rulesync could plausibly write, and it must stay assertable.
OMIT = object()


def _agent(name: str) -> str:
    """What rulesync emits: frontmatter whose `name:` is what Claude Code registers."""
    return f"---\nname: {name}\ndescription: >-\n  synthetic\n---\nbody\n"


def _tree(root: Path, *, command=GUARD, agents=("a", "b"), skills=("s",), settings=True,
          event="PreToolUse", hook_type="command", declared=None):
    """A synthetic emitted tree. Sources and emitted names agree unless a test diverges them.

    `declared` maps an agent stem to the name its emitted frontmatter should declare instead,
    which is the only way to build the stems-match-but-nothing-registers tree.
    """
    declared = declared or {}
    (root / ".rulesync" / "subagents").mkdir(parents=True)
    (root / ".claude" / "agents").mkdir(parents=True)
    for name in agents:
        (root / ".rulesync" / "subagents" / f"{name}.md").write_text("x", encoding="utf-8")
        (root / ".claude" / "agents" / f"{name}.md").write_text(
            _agent(declared.get(name, name)), encoding="utf-8"
        )
    for name in skills:
        (root / ".rulesync" / "skills" / name).mkdir(parents=True)
        (root / ".claude" / "skills" / name).mkdir(parents=True)
        (root / ".claude" / "skills" / name / "SKILL.md").write_text("x", encoding="utf-8")
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


@pytest.mark.parametrize("command", ["", OMIT], ids=["empty-command", "no-command-key"])
def test_a_command_less_hook_is_a_violation(tmp_path, command):
    """rulesync can emit the hook structure with no command and still print `All done!`.

    BOTH shapes, because they are not the same emitted document and only one was covered. The
    guard reads the key with `hook.get("command", "")`; mutating that to `hook["command"]`
    survived the empty-string case green and raised KeyError on the missing-key one, so the
    absent key -- the shape this test's name and .rulesync/hooks.json's comment both describe --
    was going unasserted.
    """
    assert any("guard_no_bypass.py" in v for v in violations(_tree(tmp_path, command=command)))


def test_a_command_that_only_MENTIONS_the_guard_does_not_satisfy_it(tmp_path):
    """`echo <path>` contains the needle and runs nothing.

    A substring test accepts it, which is the inert-guard state reproduced inside the check for
    inert guards. tests/test_hooks_wiring.py pins the source command byte-exactly for the same
    reason; this is the artifact-side counterpart.
    """
    root = _tree(tmp_path, command='echo "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"')
    assert any("only mentions the guard" in v for v in violations(root))


def test_a_hook_of_another_type_does_not_satisfy_the_guard(tmp_path):
    """Claude Code executes an entry only when its `type` says `command`.

    The command is verbatim correct and sits at exactly the right path; only the type differs,
    so nothing runs. A check that ignored `type` would call this tree faithful.
    """
    found = violations(_tree(tmp_path, hook_type="prompt"))
    assert any("type `command`" in v for v in found)


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


def test_an_agent_whose_declared_name_is_not_its_filename_is_a_violation(tmp_path):
    """The stems all match and NOTHING registers.

    Measured on the pinned version: rulesync REWRITES frontmatter on emit (the source's
    `targets:` key is stripped) while the filename is a pass-through. Claude Code dispatches on
    the declared `name:`. So a version that mangled it emits five correctly-named files and
    registers zero agents -- every name-set comparison green, the merge gate gone.
    """
    root = _tree(tmp_path, declared={"b": "something-else"})
    found = violations(root)
    assert any("does not match its .rulesync/ source" in v for v in found) is False
    assert any("declares name 'something-else'" in v and "'b'" in v for v in found)


@pytest.mark.parametrize(
    "content",
    ["", "no frontmatter at all\n", "---\nname: a\n", "---\ndescription: x\n---\n"],
    ids=["zero-byte", "no-fence", "unterminated", "no-name-key"],
)
def test_an_agent_with_no_usable_frontmatter_is_a_violation(tmp_path, content):
    """Probed before the fix: a zero-byte `.claude/agents/a.md` returned NO violations.

    A guard called "emitted-content" that never opened a file. Each shape here registers nothing
    in Claude Code, so each has to be a violation rather than a name that happens to compare
    equal.
    """
    root = _tree(tmp_path)
    (root / ".claude" / "agents" / "a.md").write_text(content, encoding="utf-8")
    assert any("declares name None" in v for v in violations(root))


def test_a_skill_directory_with_no_SKILL_md_is_a_violation(tmp_path):
    """Probed before the fix: a skill directory holding no SKILL.md returned NO violations.

    A skill IS its SKILL.md. The directory name is all the name check can see, so an empty one
    passes it while the skill loads nothing.
    """
    root = _tree(tmp_path)
    (root / ".claude" / "skills" / "s" / "SKILL.md").unlink()
    assert any("no non-empty SKILL.md" in v for v in violations(root))


def test_an_empty_SKILL_md_is_a_violation(tmp_path):
    """Present is not usable. A zero-byte SKILL.md satisfies `is_file()` and loads nothing."""
    root = _tree(tmp_path)
    (root / ".claude" / "skills" / "s" / "SKILL.md").write_text("", encoding="utf-8")
    assert any("no non-empty SKILL.md" in v for v in violations(root))


@pytest.mark.parametrize(
    "vanished",
    [(".rulesync", "subagents"), (".claude", "agents"), (".rulesync", "skills"),
     (".claude", "skills")],
)
def test_a_vanished_directory_is_its_own_violation(tmp_path, vanished):
    """Probed: `_names` returned `set()` for a missing directory, so a vanished SOURCE compared
    EQUAL to a vanished emitted tree and the whole merge gate could disappear silently.

    Each of the four directories is checked separately, because a check that only noticed when
    one side was missing would still pass the both-sides case that started this.
    """
    root = _tree(tmp_path)
    for path in sorted((root.joinpath(*vanished)).rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    root.joinpath(*vanished).rmdir()
    assert any("does not exist" in v for v in violations(root))


def test_both_sides_vanishing_together_is_still_a_violation(tmp_path):
    """The exact probed hole: source AND emitted gone, two empty sets, comparing equal."""
    root = _tree(tmp_path, agents=(), skills=("s",))
    (root / ".rulesync" / "subagents").rmdir()
    (root / ".claude" / "agents").rmdir()
    assert any("does not exist" in v for v in violations(root))


def test_hook_commands_reads_only_the_one_path():
    """Pure. A command nested anywhere else is not a command Claude Code will run."""
    entry = {"type": "command", "command": "x"}
    assert hook_commands({"hooks": {"PreToolUse": [{"hooks": [entry]}]}}) == ["x"]
    assert hook_commands({"hooks": {"PostToolUse": [{"hooks": [entry]}]}}) == []
    assert hook_commands({}) == []
    # Right path, wrong type: carried, never executed, so it is not a command at all here.
    assert hook_commands({"hooks": {"PreToolUse": [{"hooks": [{"type": "prompt",
                                                              "command": "x"}]}]}}) == []
    # A `command`-typed entry with the key absent must survive as a failing VALUE rather than
    # raising, or one malformed hook stops every other check in the run.
    assert hook_commands({"hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]}}) == [""]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\nname: a\n---\n", "a"),
        ('---\nname: "a"\n---\n', "a"),
        ("---\ndescription: >-\n  a name: not-this\nname: a\n---\n", "a"),
        ("", None),
        ("name: a\n", None),
        ("---\nname: a\n", None),
        ("---\nname:\n---\n", None),
        ("---\n  name: a\n---\n", None),
    ],
)
def test_frontmatter_name_reads_the_key_at_column_zero_only(text, expected):
    """Pure, and the indented case is the load-bearing one: the emitted agents carry a folded
    `description: >-` whose continuation lines are indented prose, so a scan that ignored
    indentation could read a sentence as the key."""
    assert frontmatter_name(text) == expected


@pytest.mark.parametrize("settings", [True, False])
def test_main_exit_code_is_the_contract(tmp_path, settings):
    """CI reads the exit code and nothing else."""
    assert main([str(_tree(tmp_path, settings=settings))]) == (0 if settings else 1)


def test_the_module_exits_nonzero_as_a_process(tmp_path):
    """The CI contract IS the exit code, and only a subprocess can assert it.

    WITNESSED: deleting the `sys.exit(...)` wrapper at the bottom of the script -- leaving a bare
    `main(sys.argv[1:])` -- left the ENTIRE suite green while the script exited 0 on a tree with
    no guard command at all. `test_main_exit_code_is_the_contract` calls `main()` in-process and
    structurally cannot tell `sys.exit(main(...))` from `main(...)`.

    Driven with a FAILING tree: a passing one exits 0 either way and distinguishes nothing. Both
    sibling guards carry this test -- tests/test_guard_rulesync_drift.py and
    tests/test_guard_no_bypass.py, whose docstring records the lesson.
    """
    root = _tree(tmp_path, settings=False)
    proc = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True)
    assert proc.returncode != 0
