"""Minting the Google OAuth credential `track` reads (#201).

SEPARATE FROM `google_client.py` ON PURPOSE, and the reason is not tidiness. The two
modules answer different questions -- can I USE a credential, can I MINT one -- and
`classify_track_google` must keep answering the first WITHOUT depending on
`google-auth-oauthlib`. `pip install -U job-sluice` does not re-resolve extras, so every
existing `[google]` install has `google-api-python-client` and `google-auth` and not this
package, while `track run` keeps working perfectly; fusing the probes would report SETUP
across that whole population on upgrade.

The file boundary itself is a COHESION judgement rather than a forced constraint --
`google_client.py`'s google imports are already function-local, so the flow could have
lived there without breaking the probe. It is about lifecycle: that module runs on every
`track run`, this one runs once.

`google_auth_oauthlib` is imported INSIDE the functions that need it. At module scope
`probe_flow_available` would be unreachable on an install lacking the package -- the
import fails before the function that exists to report it politely can run, and its
crafted message is replaced by a raw traceback. That failure does NOT go unnoticed:
every test in this module and in `tests/test_doctor.py` does `from sluice.track import
auth` unstubbed (the one test that touches `sys.modules` sets the entry to `None`,
which makes the import RAISE rather than succeed), and CI's `[test]` install never
carries `google-auth-oauthlib` -- `google` is a separate extra exercised through fakes
-- so a hoist turns every one of those tests red with `ModuleNotFoundError` at this
file's own import line, not silently. Measured, hoisting both imports and running
`tests/test_track_auth.py tests/test_doctor.py`: 79 failed, 117 passed. This is written
down anyway because the PLACEMENT argument above -- which population's polite message a
hoist would break -- is not something that traceback states, not because the suite is
blind to the mutation.
"""
import contextlib
import datetime
import os

from sluice.core.log import get_logger
from sluice.track.google_client import write_token


_log = get_logger("track.auth")

# Derived from the API calls `google_client.py` actually makes: users().messages()
# list/get plus attachments().get (all reads), and events() list/insert/update/delete on
# calendarId="primary". `docs/INSTALL.md`'s scope table is checked against this tuple.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
)


def probe_flow_available() -> tuple[bool, str | None]:
    """Can the consent flow be built in this process? `(available, import_error)`.

    Mirrors `google_client.probe_availability`'s shape and its `(ImportError, OSError)`
    reasoning: a missing NATIVE dependency underneath a Python package does not always
    surface as ImportError. Deliberately a SECOND function rather than a branch in that
    one -- see this module's docstring for the upgrade population that depends on the
    separation.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
    except (ImportError, OSError) as e:
        # Point at the docs, not at an install command: on Homebrew and Docker the `google`
        # extra IS already installed, so "pip install" is wrong for exactly the population
        # this message reaches most often -- a probe skew, not a missing extra. The anchor
        # is checked live by tests/test_doc_links_from_code.py against docs/INSTALL.md's
        # real heading slug.
        return False, (f"google_auth_oauthlib is not importable ({e}); see "
                        "https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md"
                        "#google-access-for-track")
    return True, None


def _default_flow_factory(client_secrets_path, scopes):
    from google_auth_oauthlib.flow import InstalledAppFlow
    return InstalledAppFlow.from_client_secrets_file(client_secrets_path, list(scopes))


def _reserve_archive_path(token_path: str) -> str:
    """RESERVE a collision-suffixed sibling and return it, having created it.

    A bare `.replaced-<UTC>` overwrites an archive from the same second, which is
    reachable on a fast retry -- and destroying one recovery artefact with another is the
    harm the archive exists to prevent.

    Reserving rather than merely LOOKING is the part that has to be atomic, and an earlier
    cut of this got it wrong: it walked the candidates with `os.path.exists` and returned a
    free name, leaving a window between the check and the caller's `os.replace`. Two
    concurrent `--force` runs both see the same name free, and if the first has already
    written its new token by the time the second moves, the second overwrites the first's
    archive -- so the credential the archive existed to preserve is the one destroyed.
    `write_token(exclusive=True)` runs afterwards and cannot prevent it.

    `O_CREAT|O_EXCL` decides the race in the kernel instead, the same way the mint itself
    does: whoever creates the name owns it, and a loser retries the next suffix. The
    caller's `os.replace` then lands on a name nothing else can hold, overwriting this
    function's own 0-byte reservation.

    The reservation is created 0600 so the name is never briefly world-readable, though
    `os.replace` carries the SOURCE inode's mode onto it -- which is why the caller still
    sets the mode explicitly afterwards.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{token_path}.replaced-{stamp}"
    candidate, n = base, 1
    while True:
        try:
            os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return candidate
        except FileExistsError:
            # Bounded: a caller that cannot find a free suffix in this many tries is not
            # racing, it is looking at a directory doing something else entirely, and
            # spinning forever inside a credential write is the worse failure.
            if n > 1000:
                raise
            candidate = f"{base}.{n}"
            n += 1


def run_consent_flow(*, client_secrets_path, token_path, port=0, open_browser=True,
                     force=False, flow_factory=None):
    """Mint a credential and write it to `token_path`. Returns a report dict.

    `{"ok": bool, "reason": str, "token_path": str, "archived": str | None}` -- a report
    rather than an exception, because the CLI turns each outcome into a distinct line and
    exit code, and because `mcp`-style callers cannot act on an exception's message. That
    contract covers the interactive flow itself too: clicking Deny is the most common
    non-success outcome of a consent round-trip, and a `--port` already bound is the next
    most common -- both are ordinary, expected failures of a step that talks to a browser
    and a human, so `flow_factory`/`run_local_server`/`.credentials` are caught and
    reported like every other refusal rather than left to raise a traceback. `Exception`,
    never `BaseException`: a `KeyboardInterrupt` during the browser wait must still stop
    the process.

    ORDER IS LOAD-BEARING. Everything that can be known before the browser opens is
    checked before it opens: a consent round-trip cannot be handed back, and refusing
    afterwards wastes the one step that needs a human. `probe_flow_available` leads that
    pre-flight, ahead of even the client-secrets check -- see the comment at that call for
    why a missing package is reported before a missing file. That pre-flight is a COURTESY
    and not the guard -- the guard is `write_token(exclusive=True)`, which refuses in the
    kernel. See `google_client.write_token`.

    The credential is validated IN MEMORY before anything is written, and archiving the
    existing token under `--force` happens LAST, after every refusal that can still be
    decided from that in-memory credential. Verifying after the write would leave an
    unusable credential on disk -- and archiving before validating would strand a WORKING
    credential at an unannounced sibling path the moment validation then refused -- while
    `classify_track_google` reports OK on token presence alone, so either would read as
    healthy for ever.
    """
    if flow_factory is None:
        # FIRST, ahead of the client-secrets check, and that order is the point: this
        # reports a gap in the INSTALL rather than in the argument the user just typed,
        # and the population it serves -- a `[google]` install predating #201, whose
        # extras `pip install -U` never re-resolved -- usually has a perfectly good
        # secrets path. Measured on such an install before this existed: the import failed
        # inside the `except Exception` below and printed `the consent flow failed:
        # ModuleNotFoundError: No module named 'google_auth_oauthlib'`, so the crafted
        # message `probe_flow_available` exists to produce was unreachable from every
        # production path. Guarded on an ABSENT factory rather than run unconditionally,
        # because an injected one needs no package at all -- probing regardless would make
        # every test that supplies a fake depend on a library `[test]` deliberately omits.
        # `is None` rather than the `or` that used to select the default here: keying both
        # the probe and the selection on one condition is what stops them drifting apart.
        available, import_error = probe_flow_available()
        if not available:
            return {"ok": False, "token_path": token_path, "archived": None,
                    "reason": import_error}
        flow_factory = _default_flow_factory
    # expanduser at INGRESS (core/paths.py states the rule): this is a new ingress site,
    # a path a user names on the command line, and `--client-secrets=~/...` (the `=` form)
    # reaches here literally -- no shell expands a `~` that never stood alone as its own
    # argv token. Without this, the open() below fails on a path that plainly exists,
    # reporting "not found" at the literal `~`.
    client_secrets_path = os.path.expanduser(client_secrets_path)
    # PROVES readability rather than merely asking `os.path.exists`, which answers a
    # different question: a file that exists but cannot be opened (wrong permissions, an
    # ACL, a directory sitting at that path) used to sail past this check and reach
    # `InstalledAppFlow.from_client_secrets_file`, further down inside `flow_factory`'s own
    # call, surfacing as the generic "the consent flow failed: PermissionError: ..." -- the
    # same install-level-gap-reported-as-a-typo shape `probe_flow_available`'s own
    # pre-flight above exists to keep out of this function. Actually opening it (rather
    # than `os.access(path, os.R_OK)`) is deliberate too: `access` answers "would a read be
    # allowed" by a check that can itself be stale or wrong under some ACL/mount
    # configurations, where a subsequent open still refuses -- opening is the only way to
    # prove the property this pre-flight claims.
    try:
        with open(client_secrets_path, "rb"):
            pass
    except FileNotFoundError:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": f"client secrets file not found at {client_secrets_path}"}
    except OSError as e:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": (f"client secrets file at {client_secrets_path} could not be "
                           f"read: {type(e).__name__}: {e}")}
    if os.path.exists(token_path) and not force:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": (f"a token already exists at {token_path}; pass --force to "
                           "replace it (the existing one is archived beside it first)")}

    try:
        flow = flow_factory(client_secrets_path, SCOPES)
        # Both passed explicitly rather than trusting run_local_server's defaults: a
        # re-authorisation by a user who has already granted can return a credential
        # carrying no refresh_token, which the check below requires.
        flow.run_local_server(port=port, open_browser=open_browser,
                              access_type="offline", prompt="consent")
        creds = flow.credentials
    except Exception as e:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": f"the consent flow failed: {type(e).__name__}: {e}"}

    if not getattr(creds, "refresh_token", None):
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": "the minted credential carries no refresh_token, so it would "
                          "stop working within the hour; re-run the consent"}
    granted = getattr(creds, "granted_scopes", None)
    if not granted:
        # FAIL CLOSED. `granted or creds.scopes` is the natural spelling and silently
        # restores the vacuous compare: `scopes` is the list we REQUESTED, so comparing it
        # with itself passes in exactly the granular-consent case this checks for. This
        # ALSO fires on a real, complete grant if the installed google-auth predates
        # `granted_scopes` reporting -- a population that exists, by this module's own
        # docstring's reasoning: the `google` extra floors at `>=2,<3`, and
        # `pip install -U job-sluice` does not re-resolve extras, so an old install's
        # google-auth is never upgraded alongside it. Failing closed is still right (a
        # missing signal is not a passing one), but the message must name BOTH possible
        # causes rather than asserting the wrong one at someone who granted everything.
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": "the credential reported no granted_scopes, so sluice cannot "
                          "tell a complete grant from a partial one; if you granted every "
                          "permission the consent screen listed, your installed "
                          "google-auth library may predate granted_scopes reporting -- "
                          "upgrading it (pip install -U google-auth) may fix this"}
    if isinstance(granted, str):
        # Defensive, not a fix for a bug seen in production: oauthlib's
        # `parse_token_response` splits the token response's `scope` field into a list
        # today, so `granted` is never actually a bare string through the real library --
        # MEASURED, not assumed. But `google.oauth2.credentials.Credentials` documents
        # `granted_scopes` as `Optional[Sequence[str]]`, and `str` IS a `Sequence[str]`,
        # so a future library version returning the space-delimited wire format verbatim
        # would make `set(granted)` character-set it: `"a b"` becomes `{"a", " ", "b"}`,
        # every real scope URL fails the `in` test below, and a complete, correctly
        # granted consent is refused with a message the user can never clear by granting
        # more. This module already states the posture of not relying on library
        # behaviour it has not measured (see `probe_flow_available`'s `(ImportError,
        # OSError)` catch) -- this is the same posture applied here.
        granted = granted.split()
    missing = [s for s in SCOPES if s not in set(granted)]
    if missing:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": f"consent did not grant the scope(s) {', '.join(missing)} -- "
                          "without them track cannot do its job and would fail silently"}

    # Evaluated OUTSIDE the try below: a serialization failure here is a property of the
    # CREDENTIAL, and folding it into the write's except would report "could not write the
    # token", pointing the user at disk and permissions for a problem that is neither. ALSO
    # ahead of the archive block below: a serialization failure here is decided entirely
    # from the in-memory credential, same as every check above it, so it belongs before
    # `--force` moves the user's working token to a sibling path -- an exception here must
    # not leave that token stranded and unannounced while this one still fails to write.
    payload = creds.to_json()

    archived = None
    if force and os.path.exists(token_path):
        try:
            archived = _reserve_archive_path(token_path)
        except OSError as e:
            # Reserving CREATES a file, where the `os.path.exists` walk this replaced only
            # looked -- so it can fail the way any create can, and a token directory the
            # user cannot write is the realistic one. Measured on that case: before the
            # reservation landed this returned a clean report from `os.replace` below,
            # and after it the `os.open` raised straight out of `run_consent_flow`, whose
            # whole contract is a report. Hardening the archive must not turn a refusal
            # into a traceback in the one situation -- an unwritable directory -- where
            # the user most needs to be told which of their paths is the problem.
            return {"ok": False, "token_path": token_path, "archived": None,
                    "reason": f"could not reserve an archive name for the existing "
                              f"token: {type(e).__name__}: {e}"}
        try:
            os.replace(token_path, archived)
        except Exception as e:
            # The reservation is a 0-byte file THIS call created and nothing else can hold,
            # so removing it is safe and is not the `os.path.exists` guess `write_token`'s
            # own cleanup deliberately avoids. Leaving it would strand an empty
            # `.replaced-` sibling next to a token that never moved, which reads exactly
            # like a recovery artefact while holding nothing.
            with contextlib.suppress(OSError):
                os.unlink(archived)
            return {"ok": False, "token_path": token_path, "archived": None,
                    "reason": f"could not archive the existing token: "
                              f"{type(e).__name__}: {e}"}
        # os.replace is a rename and does NOT chmod, so the archive inherits the old
        # inode's mode -- and the population --force serves is the one holding a 0644
        # token from the pre-#201 hand-written script. Left silent on failure: a chmod
        # that cannot apply leaves the archive at whatever mode the live token already
        # had, which is no worse than the token was a moment before this line ran.
        with contextlib.suppress(OSError):
            os.chmod(archived, 0o600)
    try:
        write_token(token_path, payload, exclusive=True)
    except Exception as e:
        # The window this sequence has of its own: the archive succeeded and the write did
        # not, so the user has no token and a working credential somewhere they have never
        # heard of. Naming the archive is the whole of what makes it recoverable.
        where = f"; your previous credential is at {archived}" if archived else ""
        return {"ok": False, "token_path": token_path, "archived": archived,
                "reason": f"could not write the token: {e}{where}"}
    _log.info("track: wrote a Google credential to %s", token_path)
    return {"ok": True, "token_path": token_path, "archived": archived, "reason": "minted"}
