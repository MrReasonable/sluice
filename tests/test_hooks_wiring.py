"""The guard's wiring, pinned at its source.

A correct guard that is not wired is inert, and this exact file has already shipped inert
once: it carried `{"hooks": {}}` plus a comment asserting rulesync could not do PreToolUse.
That was a misdiagnosis of a schema error, and nothing caught it, because the only thing
guarding the wiring was prose -- and prose failing is the whole reason the guard exists.

Every rulesync failure mode here is SILENT. Feed it Claude Code's native PascalCase/nested
shape and it skips the event, drops the command, writes a hook with no `command` key, and
prints "All done!". Omit the top-level `hooks` record and it prints a Zod error, then
"All files are up to date", writes nothing, and exits 0. An exit-code check would pass all
of it. So these assert the shape.

WHY THE SOURCE AND NOT THE GENERATED FILE: `.claude/settings.json` is gitignored and
produced by `npx rulesync generate`, which needs node and network. CI never runs it, so
asserting the generated artifact could not be hermetic. `.rulesync/hooks.json` is the
tracked input, and the schema is what actually goes wrong.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
HOOKS = ROOT / ".rulesync" / "hooks.json"


def _config():
    return json.loads(HOOKS.read_text(encoding="utf-8"))


def _definitions():
    return _config()["claudecode"]["hooks"]["preToolUse"]


def test_the_top_level_hooks_record_exists():
    """Zod-required. Omit it and generate writes nothing, silently, exiting 0."""
    assert isinstance(_config().get("hooks"), dict)


def test_the_event_key_is_rulesyncs_camelcase_not_claude_codes_pascalcase():
    """`preToolUse` is rulesync's name. `PreToolUse` is the OUTPUT name and is skipped here."""
    hooks = _config()["claudecode"]["hooks"]
    assert "preToolUse" in hooks
    assert "PreToolUse" not in hooks


def test_the_hook_is_scoped_to_claudecode_only():
    """The guard parses Claude Code's payload shape; it is noise in any other tool's config.

    Pins the scope SET, not just the empty shared block: asserting only `hooks == {}` would
    stay green while a second tool scope quietly emitted the guard into another tool's
    config.
    """
    config = _config()
    assert config["hooks"] == {}
    assert [key for key in config if key not in ("_comment", "version", "hooks")] == [
        "claudecode"
    ]


def test_exactly_one_definition_is_wired():
    """The inert state this file exists to catch is `preToolUse: []`, and an empty list
    makes every `for definition in ...` assertion below vacuously true. Pin the count so
    the emptiness is caught by an assertion rather than by an incidental IndexError.
    """
    assert len(_definitions()) == 1


def test_each_definition_is_flat_not_claude_codes_nested_shape():
    definitions = _definitions()
    assert definitions, "no preToolUse definitions: the guard is wired inert"
    for definition in definitions:
        assert definition["type"] == "command"
        assert isinstance(definition.get("command"), str) and definition["command"]
        # The nested `hooks` list is what rulesync GENERATES. Supplying it here is the
        # mistake that silently drops the command.
        assert "hooks" not in definition


def test_the_hook_runs_the_guard_on_bash_and_the_guard_exists():
    definition = _definitions()[0]
    assert definition["matcher"] == "Bash"
    # Pin the command EXACTLY, not by substring: `echo scripts/guard_no_bypass.py` contains
    # the path and runs nothing. The quoting matters too -- `$CLAUDE_PROJECT_DIR` expands to
    # a path that may contain spaces.
    assert definition["command"] == 'python3 "$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py"'
    # A command pointing at a missing script would still generate cleanly, and CPython's
    # exit 2 for "cannot open file" would even look like a block.
    assert (ROOT / "scripts" / "guard_no_bypass.py").is_file()
