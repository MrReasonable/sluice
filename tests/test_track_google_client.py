import sluice.track.google_client as gc


class FakeGoogleClient:
    """Reference fake used across track tests; mirrors the real interface."""
    def __init__(self, messages=None, events=None):
        self.messages = messages or {}
        self.events = list(events or [])
        self.inserted, self.updated, self.deleted = [], [], []

    def search_messages(self, query, max_results=50):
        return list(self.messages.keys())

    def get_message(self, message_id):
        return self.messages[message_id]

    def list_events(self, time_min_iso, time_max_iso):
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


def test_get_message_decodes_inline_ics():
    import base64
    class _Exec:
        def __init__(self, v): self.v = v
        def execute(self): return self.v
    class _FakeGmail:
        def __init__(self, msg): self._msg = msg
        def users(self): return self
        def messages(self): return self
        def get(self, userId, id, format): return _Exec(self._msg)
        def attachments(self): return self
    ics = b"BEGIN:VEVENT\r\nUID:x\r\nEND:VEVENT"
    inline = base64.urlsafe_b64encode(ics).decode().rstrip("=")
    payload = {"headers": [{"name": "From", "value": "a@b"}],
               "parts": [{"mimeType": "text/calendar", "filename": "invite.ics", "body": {"data": inline}}]}
    c = gc.RealGoogleClient("/nonexistent")
    c._gmail = _FakeGmail({"payload": payload, "threadId": "t"})
    out = c.get_message("m1")
    assert out["attachments"] and out["attachments"][0]["data"].startswith(b"BEGIN:VEVENT")
