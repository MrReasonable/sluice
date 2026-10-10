import json
import os
import stat

import pytest

from sluice.mcpinstall import clients, routes, server
from tests.mcpinstall.fakes import OTHER, Rig

SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))
CURSOR = clients.by_name("cursor")


def _apply(rig, name="cursor", spec=SPEC):
    c = clients.by_name(name)
    state = routes.read_state(c, rig.path(name))
    assert not isinstance(state, routes.Unreadable), state
    return routes.apply_json(c, state, spec, rig.deps)


def test_a_missing_file_is_created_with_only_the_entry(tmp_path):
    rig = Rig(tmp_path)
    out = _apply(rig)
    assert out.kind == "registered", out
    assert rig.read("cursor") == {"mcpServers": {"job-sluice": clients.entry_value(CURSOR, SPEC)}}
    assert rig.copies() == []


def test_an_empty_file_is_a_create(tmp_path):
    rig = Rig(tmp_path)
    rig.write("cursor", "")
    assert _apply(rig).kind == "registered"


def test_other_keys_and_a_credential_survive_and_the_clean_copy_is_deleted(tmp_path):
    rig = Rig(tmp_path)
    rig.write("cursor", {"mcpServers": {"other": OTHER}, "theme": {"x": 1}})
    out = _apply(rig)
    assert out.kind == "registered", out
    doc = rig.read("cursor")
    assert doc["mcpServers"]["other"] == OTHER and doc["theme"] == {"x": 1}
    assert len(rig.copies_written) == 1 and rig.copies() == []


def test_a_replace_keeps_and_names_a_user_env_key(tmp_path):
    rig = Rig(tmp_path)
    old = {"command": "/old/job-sluice", "args": ["mcp", "serve"],
           "env": {"MY_API_KEY": "SENTINEL-NOT-A-SECRET-3", "SLUICE_CONFIG": "/old.yaml"}}
    rig.write("cursor", {"mcpServers": {"job-sluice": old}})
    out = _apply(rig)
    assert out.kind == "replaced", out
    env = rig.read("cursor")["mcpServers"]["job-sluice"]["env"]
    assert env == {"MY_API_KEY": "SENTINEL-NOT-A-SECRET-3", "SLUICE_CONFIG": "/cfg/sluice.yaml"}
    assert any("MY_API_KEY" in d for d in out.details)
    assert not any("SENTINEL" in d for d in out.details)


def test_a_replace_keeps_and_names_the_entry_s_other_settings(tmp_path):
    rig = Rig(tmp_path)
    old = {"command": "/old/job-sluice", "args": [], "autoApprove": ["list_leads"], "cwd": "/w"}
    rig.write("cursor", {"mcpServers": {"job-sluice": old}})
    out = _apply(rig)
    entry = rig.read("cursor")["mcpServers"]["job-sluice"]
    assert out.kind == "replaced" and entry["autoApprove"] == ["list_leads"] and entry["cwd"] == "/w"
    assert any("autoApprove, cwd" in d for d in out.details)


def test_the_backup_folder_name_is_the_one_the_path_sweeps_read():
    assert os.path.basename(routes.backup_dir()) == routes.BACKUP_FOLDER


@pytest.mark.parametrize("content,reason", [
    ("\ufeff{}", "byte-order mark"),
    ('{"mcpServers": {}} // c', "comments"),
    ('{"a": 1, "a": 2}', "twice"),
    ("[]", "top level"),
    ('{"mcpServers": []}', "server table"),
    ('{"mcpServers": {"job-sluice": "x"}}', "shape"),
    ("{", "not valid JSON"),
])
def test_every_refusal_reads_unreadable_and_writes_nothing(tmp_path, content, reason):
    rig = Rig(tmp_path)
    p = rig.write("cursor", content)
    before = p.read_bytes()
    got = routes.read_state(CURSOR, str(p))
    assert isinstance(got, routes.Unreadable) and reason in got.reason
    assert p.read_bytes() == before and rig.copies() == []


def test_non_utf8_bytes_are_unreadable(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", "")
    p.write_bytes(b'{"a": "\xff"}')
    assert isinstance(routes.read_state(CURSOR, str(p)), routes.Unreadable)


def test_a_folder_is_unreadable(tmp_path):
    rig = Rig(tmp_path)
    os.makedirs(rig.path("cursor"))
    assert "folder" in routes.read_state(CURSOR, rig.path("cursor")).reason


def test_a_change_before_the_copy_leaves_no_copy_and_replaces_nothing(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    state = routes.read_state(CURSOR, str(p))
    p.write_text('{"mcpServers": {"late": {}}}')
    out = routes.apply_json(CURSOR, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert rig.copies_written == [] and "job-sluice" not in rig.read("cursor")["mcpServers"]


def test_a_change_after_the_copy_keeps_and_names_the_copy(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    state = routes.read_state(CURSOR, str(p))
    rig.after_copy = lambda: p.write_text('{"mcpServers": {"late": {}}}')
    out = routes.apply_json(CURSOR, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert rig.copies() == rig.copies_written
    assert any(f"{routes.BACKUP_FOLDER}/{rig.copies_written[0]}" in d for d in out.details)
    assert "job-sluice" not in rig.read("cursor")["mcpServers"]


def test_the_copy_carries_the_file_s_mode(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    p.chmod(0o600)
    state = routes.read_state(CURSOR, str(p))
    rig.after_copy = lambda: p.write_text("{}")       # fail, so the copy is kept to inspect
    routes.apply_json(CURSOR, state, SPEC, rig.deps)
    mode = stat.S_IMODE(os.stat(os.path.join(rig.backup_dir, rig.copies()[0])).st_mode)
    assert mode == 0o600


def test_a_symlinked_file_has_its_target_replaced(tmp_path):
    rig = Rig(tmp_path)
    real = tmp_path / "dotfiles" / "mcp.json"
    real.parent.mkdir()
    real.write_text('{"mcpServers": {}}')
    link = tmp_path / "home" / ".cursor" / "mcp.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert _apply(rig).kind == "registered"
    assert link.is_symlink() and "job-sluice" in json.loads(real.read_text())["mcpServers"]


def test_a_path_with_spaces_and_non_ascii_round_trips(tmp_path):
    rig = Rig(tmp_path)
    spec = server.ServerSpec(("/opt/My Apps/jöb/job-sluice", "mcp", "serve"), ())
    assert _apply(rig, spec=spec).kind == "registered"
    entry = rig.read("cursor")["mcpServers"]["job-sluice"]
    assert entry["command"] == "/opt/My Apps/jöb/job-sluice"


def test_vscode_writes_its_servers_table(tmp_path):
    rig = Rig(tmp_path)
    assert _apply(rig, "vscode").kind == "registered"
    assert rig.read("vscode")["servers"]["job-sluice"]["type"] == "stdio"


def test_matches_compares_argv_and_every_pinned_key_on_both_sides():
    entry = routes.Entry(SPEC.argv, (("SLUICE_CONFIG", "/cfg/sluice.yaml"), ("X_KEY", "v")))
    assert routes.matches(entry, SPEC)
    assert not routes.matches(routes.Entry(SPEC.argv, ()), SPEC)
    bare = server.ServerSpec(SPEC.argv, ())
    assert not routes.matches(entry, bare)     # the old entry pins a path this run does not


def test_a_lone_surrogate_in_the_file_still_writes_it_back_unchanged(tmp_path):
    rig = Rig(tmp_path)
    rig.write("vscode", '{"servers": {"x": {"command": "\\ud800"}}}')
    assert _apply(rig, "vscode").kind == "registered"
    assert rig.read("vscode")["servers"]["x"]["command"] == "\ud800"


def test_a_file_nested_too_deep_is_unreadable(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", '{"a": ' + "[" * 200000 + "]" * 200000 + "}")
    got = routes.read_state(CURSOR, str(p))
    assert isinstance(got, routes.Unreadable) and "nested" in got.reason


def test_an_unexpected_error_after_the_copy_keeps_and_names_it(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.write("cursor", {"mcpServers": {"other": OTHER}})
    state = routes.read_state(CURSOR, rig.path("cursor"))

    def boom(*a, **k):
        raise RuntimeError("SENTINEL-NOT-A-SECRET-BOOM")
    monkeypatch.setattr(routes, "_verify", boom)
    out = routes.apply_json(CURSOR, state, SPEC, rig.deps)
    assert out.kind == "failed" and "unexpected error: RuntimeError" in out.reason
    assert "SENTINEL" not in out.reason and rig.copies() == rig.copies_written
    assert any(rig.copies_written[0] in d for d in out.details)


@pytest.mark.parametrize("number", ["1e400", "-1e400", "1.00000000000000000001", "NaN", "Infinity"])
def test_a_number_a_re_save_would_change_is_refused_before_any_write(tmp_path, number):
    """The JSON route re-saves the whole file with json.dumps, which writes an out-of-range
    number as the bare word Infinity (not JSON) and rounds a long float; the client could then
    not read its own file, and readback (python's json, which accepts both) would not notice."""
    rig = Rig(tmp_path)
    text = '{"mcpServers": {"other": {"command": "/o", "timeout": %s}}}' % number
    p = rig.write("cursor", text)
    got = routes.read_state(CURSOR, str(p))
    assert isinstance(got, routes.Unreadable) and "number" in got.reason
    assert p.read_text() == text and rig.copies_written == []


def test_an_exact_float_is_still_read():
    cursor_state = clients.by_name("cursor")
    assert routes._load(cursor_state, b'{"a": 0.1, "b": 1e5, "c": -2.5e-3}') == {
        "a": 0.1, "b": 1e5, "c": -2.5e-3}
