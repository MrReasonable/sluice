"""Injectable Google adapter. The real client uses the container venv's google
libs, lazy-imported inside methods so this module imports fine in the offline dev
venv (where those libs are absent). All offline tests use a fake with the same
shape."""
import base64
import contextlib
import os
import tempfile

from sluice.core.log import get_logger


_log = get_logger("track.google_client")


class GoogleAuthError(Exception):
    pass


def _write_token(path: str, data: str) -> None:
    """Write the OAuth token, creating its parent and forcing mode 0600.

    Two things the bare `open(path, "w")` this replaces got wrong. It created no parent
    directory, which was survivable while the token sat in the cwd and is not now that
    it resolves under the per-system state root (#80): on a fresh install the first
    refresh would raise FileNotFoundError, mid auth flow. And it left the file at the
    umask default, typically 0644 -- a world-readable credential.

    Written to a temp file in the same directory and moved into place with `os.replace`,
    the shape `core/vault.py` already uses for the same reason: a refresh that is
    interrupted partway must not leave a truncated token behind. `O_TRUNC` writing in
    place would, and an unparseable token means a full reauth.

    That also settles the permissions, in both directions at once. `mkstemp` creates at
    0600, so the credential is never briefly world-readable -- the window a plain `open`
    followed by a `chmod` leaves open, which is long enough for a local reader to copy
    it. And `os.replace` carries the TEMP file's mode onto the destination, so a refresh
    over a token an older sluice left at 0644 ends up 0600 without a separate `chmod`
    that would have to be remembered. No `chmod` call is therefore needed or wanted here:
    one would normalise both arms and make a weakened creation mode an equivalent mutant.

    The parent is created 0700: it holds this credential alongside the rest of sluice's
    state. Only newly created directories get that mode -- an existing directory's
    permissions are the user's business, and silently tightening or refusing on one would
    be a surprise from a token write.

    Stdlib only, like the rest of `sluice/`.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".google_token.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray 0600 temp behind on failure -- including on KeyboardInterrupt,
        # which is why this catches BaseException and re-raises rather than `except OSError`.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def probe_availability() -> tuple[bool, str | None]:
    """Can `RealGoogleClient` actually be built in this process? Returns
    (available, import_error). Used by `sluice doctor` (core/app.py) to report
    on track's Google adapter WITHOUT importing the google client libs itself --
    this module is the ONE sanctioned site (see CLAUDE.md's stdlib-only rule for
    `sluice/`), so a second copy of these three imports living in core/app.py
    would duplicate knowledge of exactly which submodules matter and could
    silently drift out of step with `_creds`/`gmail`/`calendar` below.

    `(ImportError, OSError)`, not `ImportError` alone, for the same reason
    `renderers/template.py`'s `_make` catches both: a missing NATIVE dependency
    underneath a Python package does not always surface as ImportError."""
    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
    except (ImportError, OSError) as e:
        return False, str(e)
    return True, None


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
                _write_token(self.token_path, creds.to_json())
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

    def search_messages(self, query, max_results=500):
        """Every message id matching `query`, across pages, capped at `max_results`.

        The cap moved from 50-per-REQUEST to a total across pages, because 50 was never a
        sane answer for this caller: `engine.run` filters against `seen` AFTER the fetch, so
        on any busy window the already-processed messages consumed the cap and the unseen
        ones -- the entire point of the call -- never arrived. Gmail returns newest-first, so
        the ones starved out were the OLDEST unprocessed: exactly the set that had failed
        before (#139)."""
        items, truncated = _paged(
            self._gmail_svc().users().messages(),
            dict(userId="me", q=query, maxResults=min(max_results, 500)),
            "messages", max_results)
        if truncated:
            _log.warning(
                "track: gmail search hit the %d-message cap for %r -- there are more matches "
                "than this run will see. Narrow gmail_extra_query or shorten the window.",
                max_results, query)
        return [m["id"] for m in items]

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

    def list_events(self, time_min_iso, time_max_iso, max_results=2500):
        """Every event in the window, across pages, capped at `max_results`.

        A truncated page here is not a smaller answer, it is a WRONG one. `_find_ours` reads
        absence as "we never created this", so an event of ours sitting off-page makes
        `sync_event` insert a duplicate -- or, on a cancel, skip the delete entirely, and
        report `present` while the interview stays in the calendar (#138). The window is
        2 * calendar_lookahead_days (90 days by default) with `singleEvents=True`, which
        EXPANDS recurrences: one daily standup is ~90 items on its own."""
        items, truncated = _paged(
            self._cal_svc().events(),
            dict(calendarId="primary", timeMin=time_min_iso, timeMax=time_max_iso,
                 singleEvents=True, maxResults=250),
            "items", max_results)
        if truncated:
            _log.warning(
                "track: calendar window returned more than %d events -- an event of ours may "
                "be off-page, which reads as absent. Reduce calendar_lookahead_days.",
                max_results)
        return items

    def insert_event(self, body):
        return self._cal_svc().events().insert(calendarId="primary", body=body).execute()["id"]

    def update_event(self, event_id, body):
        return self._cal_svc().events().update(
            calendarId="primary", eventId=event_id, body=body).execute()["id"]

    def delete_event(self, event_id):
        self._cal_svc().events().delete(calendarId="primary", eventId=event_id).execute()


def _paged(endpoint, params: dict, item_key: str, max_results: int) -> tuple:
    """`(items, truncated)` across every page of a Google list endpoint.

    ONE implementation for both callers. Two hand-rolled loops is how one of them keeps
    `nextPageToken` and the other quietly does not -- which is the state #137 found them in,
    with each call reading a truncated first page as the complete set.

    Uses the client library's own `list_next(previous_request, previous_response)` protocol
    rather than threading `pageToken` by hand: that is the documented paging contract, and it
    is what the discovery-built service exposes.

    `truncated` is returned rather than logged here so the CALLER can say what running out
    costs, which differs sharply between the two (a missed message versus a duplicated or
    undeleted calendar entry). Silently returning a short list is the one thing this must
    never do -- that is the bug being fixed."""
    request = endpoint.list(**params)
    items, truncated = [], False
    while request is not None:
        response = request.execute()
        items.extend(response.get(item_key, []))
        if len(items) >= max_results:
            # More pages remained: report it. A cap is still needed (an unbounded walk over a
            # mailbox is its own outage -- a cron run that never returns), but it must be an
            # HONEST one.
            truncated = endpoint.list_next(request, response) is not None
            return items[:max_results], truncated
        request = endpoint.list_next(request, response)
    return items, truncated


def _walk_parts(payload):
    yield payload
    for p in payload.get("parts", []) or []:
        yield from _walk_parts(p)


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
