"""A fake Google client matching `RealGoogleClient`'s surface.

`track.engine.run` calls `search_messages` then `get_message`; `calendar_sync`
calls `list_events`/`insert_event`/`update_event`/`delete_event` when an `.ics`
attachment is present. All are implemented here so a call the engine makes lands
on a real method rather than an `AttributeError` that would mask the assertion
under test (the same discipline `test_app_operations.py::_FakeGoogle` follows).
It is passed via `Sluice.track(client=...)`, the explicit test seam -- Google has
one client shape, so there is no registry, no config selection.
"""


class FakeGoogleClient:
    auth_error = False

    def __init__(self, messages=None):
        # {message_id: message_dict}. A message_dict matches what
        # RealGoogleClient.get_message returns: headers / body_text / thread_id /
        # attachments. search_messages hands back the ids (the fake ignores the
        # time-scoped Gmail query, which the engine builds but we need not honour).
        self.messages = dict(messages or {})

    def search_messages(self, query, max_results=50):
        # `(ids, truncated)` -- the real client's contract, unconditionally. This fake was
        # `**kwargs`-shaped and silently swallowed the old opt-in kwarg, which is how a bare
        # list reached an unpack in production code.
        return list(self.messages), False

    def get_message(self, message_id):
        return self.messages.get(message_id, {})

    # Calendar surface -- reached only when a message carries an .ics attachment.
    def list_events(self, *a, **k):
        return [], False

    def insert_event(self, *a, **k):
        return "evt-1"

    def update_event(self, *a, **k):
        return "evt-1"

    def delete_event(self, *a, **k):
        return None
