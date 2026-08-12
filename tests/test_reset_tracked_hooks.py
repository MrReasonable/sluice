"""`scripts/reset_tracked_hooks.py`: the rulesync job's pre-generation step.

WHY THIS EXISTS. `.claude/settings.json` is the one rulesync output this repo tracks (it also
carries Claude Code's hand-authored `enabledPlugins` key). A CI checkout therefore always
supplies a copy before `npm run rulesync` runs, which -- unless that copy's `hooks` key is
cleared first -- lets rulesync's own "skip a file that already matches" behaviour silently omit
`hooks` from its summary (breaking `guard_rulesync_drift.py`'s exact count) or lets a stale but
structurally-valid copy survive a genuinely broken generate run undetected (defeating
`guard_emitted_outputs.py`). The script's own docstring has the full chain, verified against a
simulated fresh checkout; these tests pin the unit itself.

Offline and hermetic -- nothing here shells out to npm or the real generator.
"""

import json
import subprocess
import sys
from pathlib import Path

from scripts.reset_tracked_hooks import main, strip_hooks

SCRIPT = Path(__file__).parent.parent / "scripts" / "reset_tracked_hooks.py"


def test_strip_hooks_removes_the_hooks_key():
    assert strip_hooks({"enabledPlugins": {"paad@paad": True}, "hooks": {"PreToolUse": []}}) == {
        "enabledPlugins": {"paad@paad": True}
    }


def test_strip_hooks_is_a_noop_without_a_hooks_key():
    doc = {"enabledPlugins": {"paad@paad": True}}
    assert strip_hooks(doc) == doc


def test_strip_hooks_on_an_empty_document():
    assert strip_hooks({}) == {}


def test_strip_hooks_does_not_mutate_its_argument():
    doc = {"hooks": {}, "enabledPlugins": {}}
    strip_hooks(doc)
    assert "hooks" in doc, "strip_hooks must return a new dict, not mutate the caller's"


def test_strip_hooks_preserves_key_order_of_the_remaining_keys():
    """The module docstring's fixed-point claim ("rulesync APPENDS the key it writes rather
    than preserving original position, so the opposite order would never reach a stable fixed
    point") is about ORDER, not just membership. `==` on two dicts ignores order, so every
    other test here would stay green under a sort-based or otherwise order-scrambling rewrite
    of `strip_hooks`. `hooks` deliberately sits in the MIDDLE, with keys on both sides, so a
    mutant that merely preserves first/last position cannot pass by accident.
    """
    doc = {"zebra": 1, "hooks": {}, "enabledPlugins": {"paad@paad": True}, "apple": 2}
    assert list(strip_hooks(doc).keys()) == ["zebra", "enabledPlugins", "apple"]


def test_main_clears_hooks_but_preserves_enabled_plugins(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"enabledPlugins": {"paad@paad": True}, "hooks": {"PreToolUse": []}}),
        encoding="utf-8",
    )
    assert main([str(tmp_path)]) == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "enabledPlugins": {"paad@paad": True}
    }


def test_main_is_a_noop_when_the_file_is_absent(tmp_path):
    """A checkout that predates tracking this file, or a from-scratch run. rulesync writes it
    fresh either way, so there is nothing for this step to do -- and nothing to fail on."""
    assert main([str(tmp_path)]) == 0
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_main_is_a_noop_when_there_is_no_hooks_key(tmp_path):
    """Already in the state generation needs: rewriting identical content would make the file's
    mtime -- and, if formatting ever drifted, its bytes -- change for no reason."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"enabledPlugins": {"paad@paad": True}})
    settings.write_text(original, encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert settings.read_text(encoding="utf-8") == original


def test_main_leaves_malformed_json_untouched_rather_than_crashing(tmp_path):
    """A guard downstream of generation (`guard_emitted_outputs.py`) is what names a malformed
    artifact as a violation. This step runs BEFORE generation and must not abort the pipeline
    over content it did not write and is not the one responsible for judging."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not json", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert settings.read_text(encoding="utf-8") == "{ not json"


def test_main_defaults_to_the_current_directory(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {}


def test_running_twice_is_idempotent(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"enabledPlugins": {"paad@paad": True}, "hooks": {"PreToolUse": []}}),
        encoding="utf-8",
    )
    assert main([str(tmp_path)]) == 0
    once = settings.read_text(encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    assert settings.read_text(encoding="utf-8") == once


def test_the_module_runs_as_a_process(tmp_path):
    """The CI contract is invoking this as a script, not calling `main()` in-process -- pinned
    the way `tests/test_guard_emitted_outputs.py::test_the_module_exits_nonzero_as_a_process`
    pins its sibling, so `sys.exit(main(...))` being deleted cannot go unnoticed."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {}, "kept": True}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {"kept": True}
