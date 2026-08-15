import os
import stat

import pytest

import sluice.track.google_client as gc


class FakeGoogleClient:
    """Reference fake used across track tests; mirrors the real interface."""
    def __init__(self, messages=None, events=None):
        self.messages = messages or {}
        self.events = list(events or [])
        self.inserted, self.updated, self.deleted = [], [], []
        # Every (time_min_iso, time_max_iso) pair the caller passed. The real API rejects a
        # bound without a UTC offset (RFC 3339), so the ARGUMENTS are behaviour a test must be
        # able to assert on, not just the return value.
        self.listed = []

    def search_messages(self, query, max_results=50):
        return list(self.messages.keys())

    def get_message(self, message_id):
        return self.messages[message_id]

    def list_events(self, time_min_iso, time_max_iso):
        self.listed.append((time_min_iso, time_max_iso))
        return list(self.events)

    def insert_event(self, body):
        self.inserted.append(body); return "ev-new"

    def update_event(self, event_id, body):
        self.updated.append((event_id, body)); return event_id

    def delete_event(self, event_id):
        self.deleted.append(event_id)


def test_module_imports_without_google_libs():
    # Importing the module must not import googleapiclient at top level (offline dev venv).
    assert hasattr(gc, "RealGoogleClient") and hasattr(gc, "GoogleAuthError")


def test_real_client_constructs_without_touching_google():
    # Constructor stores the path; no network / no google import until a method is called.
    c = gc.RealGoogleClient("/nonexistent/google_token.json")
    assert c.token_path == "/nonexistent/google_token.json"


def test_fake_satisfies_interface():
    f = FakeGoogleClient(messages={"m1": {"headers": {}, "body_text": "", "thread_id": "t", "attachments": []}})
    assert f.search_messages("q") == ["m1"]
    assert f.get_message("m1")["thread_id"] == "t"
    f.insert_event({"summary": "x"}); assert f.inserted


class _Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


class _FakeGmail:
    def __init__(self, msg): self._msg = msg
    def users(self): return self
    def messages(self): return self
    def get(self, userId, id, format): return _Exec(self._msg)
    def attachments(self): return self


def test_duplicate_headers_keep_the_first_occurrence():
    # A header name may legally repeat, and for the TRACE headers it normally does: every
    # hop PREPENDS its own, so the FIRST Authentication-Results is the one our delivering
    # server stamped and any later duplicate came from the sender -- who can put whatever
    # they like in a message they send. A last-wins dict comprehension gave that forged
    # copy priority over the real verdict, which would defeat receipt._sender_authenticated
    # (the proof tier reads exactly this header) while looking entirely correct. Ordered
    # as Gmail returns them: real first, forged second.
    payload = {"headers": [
        {"name": "Authentication-Results", "value": "mx.example.invalid; dkim=fail"},
        {"name": "authentication-results", "value": "mx.example.invalid; dkim=pass header.d=example.com"},
        {"name": "From", "value": "jobs@example.com"},
    ]}
    c = gc.RealGoogleClient("/nonexistent")
    c._gmail = _FakeGmail({"payload": payload, "threadId": "t"})
    assert c.get_message("m1")["headers"]["authentication-results"].endswith("dkim=fail")


def test_get_message_decodes_inline_ics():
    import base64
    ics = b"BEGIN:VEVENT\r\nUID:x\r\nEND:VEVENT"
    inline = base64.urlsafe_b64encode(ics).decode().rstrip("=")
    payload = {"headers": [{"name": "From", "value": "a@b"}],
               "parts": [{"mimeType": "text/calendar", "filename": "invite.ics", "body": {"data": inline}}]}
    c = gc.RealGoogleClient("/nonexistent")
    c._gmail = _FakeGmail({"payload": payload, "threadId": "t"})
    out = c.get_message("m1")
    assert out["attachments"] and out["attachments"][0]["data"].startswith(b"BEGIN:VEVENT")


# ── the OAuth token is a CREDENTIAL, so it is written 0600 (#80, table row #8) ─

@pytest.fixture
def pinned_umask():
    """0o022 -- the ordinary developer/CI value.

    Load-bearing, not tidiness: under `umask 077` a plain `open(path, "w")` already
    yields 0600 (0o644 & ~0o077 == 0o600), so every assertion below would pass on the
    UNFIXED code on such a machine and fail to reproduce anywhere else. Pinning makes
    the rows mean the same thing on every machine.
    """
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_fresh_token_is_written_private(tmp_path, pinned_umask):
    from sluice.track.google_client import _write_token
    p = tmp_path / "google_token.json"
    _write_token(str(p), '{"token": "x"}')
    assert _mode(p) == 0o600, "an OAuth token is a credential, not a data file"
    assert p.read_text(encoding="utf-8") == '{"token": "x"}'


def test_a_refresh_over_a_world_readable_token_tightens_it(tmp_path, pinned_umask):
    # The reason the chmod is UNCONDITIONAL. `os.open(..., 0o600)` does not change an
    # EXISTING file's mode, so a refresh over a token left at 0644 by an older sluice
    # (or by a user's own editor) would silently stay 0644 forever -- the mode nobody
    # ever looks at again.
    from sluice.track.google_client import _write_token
    p = tmp_path / "google_token.json"
    p.write_text("old", encoding="utf-8")
    os.chmod(p, 0o644)
    _write_token(str(p), "new")
    assert _mode(p) == 0o600


def test_writing_a_token_creates_its_parent_directory(tmp_path, pinned_umask):
    # #80 moved this file under the per-system state root, which on a fresh install
    # does not exist yet. The old bare `open(path, "w")` would raise FileNotFoundError
    # on the first refresh -- during an auth flow, which is the worst moment.
    from sluice.track.google_client import _write_token
    p = tmp_path / "state" / "sluice" / "google_token.json"
    _write_token(str(p), "tok")
    assert p.read_text(encoding="utf-8") == "tok"


def test_the_refresh_path_writes_through_write_token(tmp_path, monkeypatch, pinned_umask):
    """The CALLER half, which the three rows above cannot see.

    They drive `_write_token` directly, so reverting `_creds` to its old bare
    `open(self.token_path, "w")` leaves every one of them green -- measured, not
    assumed. The google libs live only in the container venv, so this stands fake
    modules up under their import names; `_creds` imports them lazily, inside the
    method, which is exactly what makes that possible.
    """
    import sys
    import types

    class _Creds:
        valid = False
        refresh_token = "r"
        def refresh(self, request): type(self).valid = True
        def to_json(self): return '{"refreshed": true}'

    cred_mod = types.ModuleType("google.oauth2.credentials")
    cred_mod.Credentials = types.SimpleNamespace(
        from_authorized_user_file=lambda path: _Creds())
    req_mod = types.ModuleType("google.auth.transport.requests")
    req_mod.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", cred_mod)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", req_mod)

    p = tmp_path / "state" / "sluice" / "google_token.json"
    gc.RealGoogleClient(str(p))._creds()
    assert p.read_text(encoding="utf-8") == '{"refreshed": true}'
    assert _mode(p) == 0o600, "a refreshed token must not be left world-readable"


def test_a_fresh_token_is_private_at_every_instant(tmp_path, monkeypatch, pinned_umask):
    """Private at CREATION, not merely by the time the function returns.

    A plain `open()` followed by a `chmod` leaves a window in which the token exists
    world-readable -- long enough for a local reader to copy it, and a real finding
    against the first version of this function. `os.chmod` is neutralised here so the
    row cannot be satisfied by a late tightening: with a chmod in play, a mutant that
    weakens the creation mode is an EQUIVALENT mutant and this could never see it.
    """
    from sluice.track import google_client as mod
    monkeypatch.setattr(mod.os, "chmod", lambda *a, **k: None)
    p = tmp_path / "google_token.json"
    mod._write_token(str(p), "tok")
    assert _mode(p) == 0o600, "the token was world-readable at some point during the write"


def test_the_token_parent_directory_is_private(tmp_path, pinned_umask):
    from sluice.track.google_client import _write_token
    parent = tmp_path / "state" / "sluice"
    _write_token(str(parent / "google_token.json"), "tok")
    assert _mode(parent) == 0o700, "sluice's state directory holds a credential"


def test_an_interrupted_write_leaves_no_stray_temp(tmp_path, monkeypatch, pinned_umask):
    # The temp file is created 0600 in the same directory. If the write blows up, it must
    # not be left behind -- a stray private file that never gets cleaned up.
    from sluice.track import google_client as mod
    p = tmp_path / "google_token.json"
    monkeypatch.setattr(mod.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        mod._write_token(str(p), "tok")
    assert list(tmp_path.iterdir()) == [], f"stray temp left behind: {list(tmp_path.iterdir())}"


def test_an_interrupted_write_cleans_up_on_keyboard_interrupt(tmp_path, monkeypatch,
                                                              pinned_umask):
    """The reason `_write_token` catches BaseException rather than Exception.

    Ctrl-C during a token refresh is the realistic interruption, and `KeyboardInterrupt`
    does not inherit from `Exception` -- so an `except Exception` would leave a 0600
    temp file behind on the one interruption users actually cause. Without this row that
    justification was untestable prose: swapping the handler to `except Exception` left
    the whole suite green, because the only other interruption row raises `OSError`.
    """
    from sluice.track import google_client as mod
    p = tmp_path / "google_token.json"

    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod.os, "replace", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        mod._write_token(str(p), "tok")
    assert list(tmp_path.iterdir()) == [], "a temp survived Ctrl-C during a token write"
