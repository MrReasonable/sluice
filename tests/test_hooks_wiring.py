"""The guard's wiring, pinned at its source.

A correct guard that is not wired is inert, and this exact file has already shipped inert
once: it carried `{"hooks": {}}` plus a comment asserting rulesync could not do PreToolUse.
That was a misdiagnosis of a schema error, and nothing caught it, because the only thing
guarding the wiring was prose -- and prose failing is the whole reason the guard exists.

Every rulesync failure mode here is SILENT. Feed it Claude Code's native PascalCase/nested
shape and it skips the event, drops the command, writes a hook with no `command` key, and
prints "All done!". Omit the top-level `hooks` record and it prints a Zod error, exits 0,
and -- re-measured on the pinned version -- writes every other output anyway, leaving ONLY
.claude/settings.json missing under an ordinary "All done!" summary. An exit-code check
would pass all of it, and so would a glance at the tree. So these assert the shape.

That second mode CHANGED SHAPE across a version bump, in the dangerous direction: the
previously pinned rulesync wrote nothing and printed "All files are up to date", which no
one could miss. Do not carry this description across the next bump on trust -- re-run it.
`.rulesync/hooks.json`'s own comment records the same measurement, and the drift guard plus
the CI grep on the emitted settings.json are what actually catch it.

WHY THE SOURCE AND NOT THE GENERATED FILE: `.claude/settings.json` is gitignored and produced
by `npm run rulesync`, which needs node and network. CI DOES now run the generator, in the
separate `rulesync` job -- but this suite must stay offline and hermetic, so asserting the
generated artifact here still could not work. `.rulesync/hooks.json` is the tracked input, and
the schema is what actually goes wrong. The conclusion is unchanged; only its reason moved.
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
    """Zod-required. Omit it and generate drops ONLY `.claude/settings.json` -- silently,
    exiting 0, with every other output written. See the module docstring: this failure mode
    got QUIETER across a version bump, not louder."""
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
