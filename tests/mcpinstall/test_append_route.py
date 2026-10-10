"""Codex: install adds a NEW `[mcp_servers.job-sluice]` table at the end of config.toml and edits
nothing (owner's ruling, 2026-10-10). `codex mcp add` re-serialises the whole server table,
dropping comments and other servers' unknown fields, so it is not used."""
import pathlib
import tomllib

from sluice.mcpinstall import clients, routes, server
from tests.mcpinstall.fakes import Rig

SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))
CODEX = clients.by_name("codex")
SENTINEL = "SENTINEL-NOT-A-SECRET-APPEND"

EXISTING = (
    '# my settings\n'
    'model = "example-model"   # a trailing comment\n'
    '\n'
    '[mcp_servers.other]\n'
    'command = "/opt/other/bin/srv"\n'
    'custom_field = "kept"   # a field codex mcp add would drop\n'
)


def _toml(rig, text=None) -> pathlib.Path:
    p = pathlib.Path(rig.path("codex"))
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        p.write_bytes(text.encode() if isinstance(text, str) else text)
    return p


def _state(rig):
    state = routes.read_state(CODEX, rig.path("codex"))
    assert isinstance(state, routes.FileState), state
    return state


def _apply(rig, spec=SPEC):
    state = _state(rig)
    assert isinstance(state.current, routes.Absent)
    return routes.apply_append(CODEX, state, spec, rig.deps)


def _said(out) -> str:
    return " ".join((out.reason, *out.details, *(out.paste or ())))


def test_an_absent_entry_is_appended_and_every_existing_byte_kept(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    out = _apply(rig)
    assert out.kind == "registered", out
    after = p.read_bytes()
    assert after.startswith(EXISTING.encode())
    doc = tomllib.loads(after.decode())
    assert doc["mcp_servers"]["other"]["custom_field"] == "kept"
    assert doc["mcp_servers"]["job-sluice"] == clients.entry_value(CODEX, SPEC)
    assert len(rig.copies_written) == 1 and rig.copies() == []


def test_a_file_without_a_final_newline_still_appends_cleanly(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, 'model = "example-model"')
    assert _apply(rig).kind == "registered"
    assert p.read_bytes().startswith(b'model = "example-model"\n')
    assert tomllib.loads(p.read_text())["model"] == "example-model"


def test_a_crlf_file_keeps_every_byte(tmp_path):
    rig = Rig(tmp_path)
    old = EXISTING.replace("\n", "\r\n").encode()
    p = _toml(rig, old)
    assert _apply(rig).kind == "registered"
    assert p.read_bytes().startswith(old)
    assert tomllib.loads(p.read_bytes().decode())["mcp_servers"]["other"]["custom_field"] == "kept"


def test_an_empty_file_gets_just_the_entry(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, b"")
    assert _apply(rig).kind == "registered"
    assert p.read_text() == clients.snippet(CODEX, SPEC) + "\n"


def test_a_missing_file_is_created(tmp_path):
    rig = Rig(tmp_path)
    assert _apply(rig).kind == "registered"
    doc = tomllib.loads(pathlib.Path(rig.path("codex")).read_text())
    assert doc == {"mcp_servers": {"job-sluice": clients.entry_value(CODEX, SPEC)}}


def test_a_non_ascii_launcher_is_appended_as_valid_toml(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    path = "/opt/My Apps/jöb \U0001F600/job-sluice"
    spec = server.ServerSpec((path, "mcp", "serve"), (("SLUICE_CONFIG", path),))
    assert _apply(rig, spec).kind == "registered"
    assert tomllib.loads(p.read_text())["mcp_servers"]["job-sluice"]["command"] == path


def test_a_nan_elsewhere_does_not_block_the_append(tmp_path):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING.replace('model = "example-model"', "ratio = nan"))
    assert _apply(rig).kind == "registered"


def test_an_inline_server_table_is_manual_and_nothing_is_written(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, 'mcp_servers = { other = { command = "x", env = { T = "%s" } } }\n'
              % SENTINEL)
    before = p.read_bytes()
    out = _apply(rig)
    assert out.kind == "manual" and p.read_bytes() == before and rig.copies_written == []
    # The guidance is the inline form, which DOES read inside that table; the table form the
    # other outcomes print would make this file unreadable.
    instruction, text = out.paste
    assert "[mcp_servers" not in text
    doc = tomllib.loads('mcp_servers = { other = { command = "x" }, %s }\n' % text)
    assert doc["mcp_servers"]["job-sluice"] == clients.entry_value(CODEX, SPEC)
    assert SENTINEL not in _said(out)


def test_an_appended_text_that_changes_anything_else_is_refused(tmp_path):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING)
    state = _state(rig)
    assert routes.appended(CODEX, state, SPEC) is not None          # control
    extra = clients.snippet(CODEX, SPEC) + '\n\n[sneaky]\nk = 1'
    assert routes.appended(CODEX, state, SPEC, text=extra) is None   # parses, but adds [sneaky]


def test_a_change_after_the_copy_keeps_the_copy_and_appends_nothing(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    state = _state(rig)
    rig.after_copy = lambda: p.write_text(EXISTING + 'late = 1\n')
    out = routes.apply_append(CODEX, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert "job-sluice" not in p.read_text() and rig.copies() == rig.copies_written


def test_a_failed_readback_keeps_and_names_the_copy(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING)
    state = _state(rig)
    monkeypatch.setattr(routes, "read_state", lambda c, path: routes.Unreadable("gone"))
    out = routes.apply_append(CODEX, state, SPEC, rig.deps)
    assert out.kind == "failed" and "readback could not read" in out.reason
    assert rig.copies() == rig.copies_written and len(rig.copies()) == 1
    assert any(rig.copies_written[0] in d for d in out.details)


def test_an_existing_entry_is_edited_by_hand_with_its_other_keys_named(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, '[mcp_servers.job-sluice]\ncommand = "/old/job-sluice"\n'
              'args = ["--token", "%s"]\nstartup_timeout_sec = 30\n\n'
              '[mcp_servers.job-sluice.env]\nMY_KEY = "%s"\n' % (SENTINEL, SENTINEL))
    before = p.read_bytes()
    state = _state(rig)
    out = routes.edit_by_hand(CODEX, state, SPEC, rig.deps)
    said = _said(out)
    assert out.kind == "manual" and "does not edit an existing one" in out.reason
    assert "MY_KEY" in said and "startup_timeout_sec" in said and "keep" in said
    assert SENTINEL not in said and p.read_bytes() == before
