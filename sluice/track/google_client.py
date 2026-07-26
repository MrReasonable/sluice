"""Injectable Google adapter. The real client uses the container venv's google
libs, lazy-imported inside methods so this module imports fine in the offline dev
venv (where those libs are absent). All offline tests use a fake with the same
shape."""
import base64


class GoogleAuthError(Exception):
    pass


class RealGoogleClient:
    """Gmail + Calendar over google_token.json. Lazy-imports google libs."""

    def __init__(self, token_path: str):
        self.token_path = token_path
        self._gmail = None
        self._cal = None

    def _creds(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        try:
            creds = Credentials.from_authorized_user_file(self.token_path)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                with open(self.token_path, "w") as f:
                    f.write(creds.to_json())
            if not creds.valid:
                raise GoogleAuthError("google token invalid and could not refresh")
            return creds
        except GoogleAuthError:
            raise
        except Exception as e:  # refresh/parse failure -> reauth needed
            raise GoogleAuthError(f"google auth failed: {e}") from e

    def _svc(self, name, version):
        from googleapiclient.discovery import build
        return build(name, version, credentials=self._creds(), cache_discovery=False)

    def _gmail_svc(self):
        if self._gmail is None:
            self._gmail = self._svc("gmail", "v1")
        return self._gmail

    def _cal_svc(self):
        if self._cal is None:
            self._cal = self._svc("calendar", "v3")
        return self._cal

    def search_messages(self, query, max_results=50):
        r = self._gmail_svc().users().messages().list(
            userId="me", q=query, maxResults=max_results).execute()
        return [m["id"] for m in r.get("messages", [])]

    def get_message(self, message_id):
        g = self._gmail_svc()
        msg = g.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {})
        # FIRST occurrence wins, not last. A header name may legally repeat, and for the
        # TRACE headers that is the norm: every hop PREPENDS its own, so the FIRST
        # `Authentication-Results` is the one our own delivering server stamped and any
        # later duplicate came from the sender. A last-wins dict comprehension handed a
        # forged `Authentication-Results: ...; dkim=pass ...` -- which anyone can put in a
        # message they send -- priority over Gmail's real verdict, defeating
        # receipt._sender_authenticated outright (#10). Same reasoning protects From and
        # Subject, where the first is also what a mail client displays.
        headers = {}
        for h in payload.get("headers", []):
            headers.setdefault(h["name"].lower(), h["value"])
        body_text, attachments = "", []
        for part in _walk_parts(payload):
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            if mime == "text/plain" and body.get("data"):
                body_text += _b64url(body["data"]).decode("utf-8", "replace")
            elif body.get("attachmentId") or part.get("filename") or mime == "text/calendar":
                data = b""
                if body.get("attachmentId"):            # separate attachment: a second call (F6)
                    att = g.users().messages().attachments().get(
                        userId="me", messageId=message_id, id=body["attachmentId"]).execute()
                    data = _b64url(att.get("data", ""))
                elif body.get("data"):                  # inline attachment bytes (e.g. text/calendar invites)
                    data = _b64url(body["data"])
                attachments.append({"filename": part.get("filename", ""),
                                    "mime": mime, "data": data})
        return {"headers": headers, "body_text": body_text,
                "thread_id": msg.get("threadId", ""), "attachments": attachments}

    def list_events(self, time_min_iso, time_max_iso):
        r = self._cal_svc().events().list(
            calendarId="primary", timeMin=time_min_iso, timeMax=time_max_iso,
            singleEvents=True, maxResults=250).execute()
        return r.get("items", [])

    def insert_event(self, body):
        return self._cal_svc().events().insert(calendarId="primary", body=body).execute()["id"]

    def update_event(self, event_id, body):
        return self._cal_svc().events().update(
            calendarId="primary", eventId=event_id, body=body).execute()["id"]

    def delete_event(self, event_id):
        self._cal_svc().events().delete(calendarId="primary", eventId=event_id).execute()


def _walk_parts(payload):
    yield payload
    for p in payload.get("parts", []) or []:
        yield from _walk_parts(p)


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
