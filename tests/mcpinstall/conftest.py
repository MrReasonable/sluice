"""Sandbox for the install tests, on top of tests/conftest.py's path pins.

PATH is an empty folder, so a real `claude` or `code` on this machine is never found, and every
variable that relocates a client's config is removed, so nothing resolves into a real profile.
`test_clients.py::test_production_lookups_find_no_client_in_the_sandbox` proves it."""
import pytest

CLIENT_ENV = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "VSCODE_IPC_HOOK_CLI", "APPDATA",
              "LOCALAPPDATA", "USERPROFILE", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG",
              "GEMINI_CLI_HOME", "XDG_DATA_HOME")


@pytest.fixture(autouse=True)
def _client_sandbox(tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for var in CLIENT_ENV:
        monkeypatch.delenv(var, raising=False)
