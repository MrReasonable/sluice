"""`job-sluice track auth` (#201), offline.

The real InstalledAppFlow is never driven here. `google-auth` is not in the [test]
extra -- `import google` fails -- so every success path injects a fake `flow_factory`
(`FakeFlow`/`FakeCreds` below) instead of touching the real library. The one exception is
`test_probe_availability_ignores_the_consent_package`, which is neither a success path nor
a test of this module -- it stubs `sys.modules`, the convention
`tests/test_track_google_client.py` already uses, to probe `google_client.py`'s own import
sweep.
"""
import datetime
import os
import re
import stat
import sys
import types

import pytest


@pytest.fixture
def pinned_umask():
    """0o022, the ordinary developer/CI value. Under `umask 077` a plain create already
    yields 0600, so every mode row below would pass on unfixed code."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


class FakeCreds:
    """`scopes` and `granted_scopes` are SEPARATE on purpose.

    google_auth_oauthlib's `credentials_from_session` sets `scopes` from the list we
    REQUESTED and `granted_scopes` from the token response. A stub that conflates them
    makes the scope check pass vacuously in exactly the granular-consent case it exists
    to catch.
    """
    def __init__(self, *, refresh_token="rt", scopes=None, granted_scopes=None):
        self.refresh_token = refresh_token
        self.scopes = list(scopes or [])
        self.granted_scopes = granted_scopes
    def to_json(self):
        return '{"refresh_token": "%s"}' % (self.refresh_token or "")


class FakeFlow:
    def __init__(self, creds):
        self._creds = creds
        self.kwargs = None
    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return None
    @property
    def credentials(self):
        return self._creds


def make_factory(creds, spy=None):
    def factory(client_secrets_path, scopes):
        flow = FakeFlow(creds)
        if spy is not None:
            spy.append((client_secrets_path, tuple(scopes), flow))
        return flow
    return factory


def _secrets(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text('{"installed": {"client_id": "x", "client_secret": "y"}}')
    return str(p)


def _full(mod):
    return FakeCreds(scopes=list(mod.SCOPES), granted_scopes=list(mod.SCOPES))


def _dir_mode_is_enforced(d) -> bool:
    """Chmod `d` to 0500 and MEASURE whether that actually prevents a create.

    Gating on `os.name != "nt"` and `geteuid() != 0` gates on the TOPIC the test
    mentions, not on the property it needs: root, a permissive mount option and an
    ACL-carrying filesystem each satisfy those conditions while still allowing the
    write, and the test would then run to completion having refused nothing. A gate
    that hides its own guard is worse than no guard, because it reads as coverage.
    """
    os.chmod(d, 0o500)
    probe = os.path.join(d, ".enforcement-probe")
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return True
    os.unlink(probe)
    return False


def test_an_existing_token_is_refused_without_force_and_the_flow_is_never_built(tmp_path):
    """Fail fast, and assert the FACTORY was never called.

    A test that only checks the exit code passes just as well when the refusal happens
    AFTER a consent round-trip the user cannot get back.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    tok.write_text("existing")
    spy = []
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok),
                                flow_factory=make_factory(_full(auth), spy))
    assert out["ok"] is False
    assert "exists" in out["reason"]
    assert spy == [], "the consent flow was constructed before the refusal"
    assert tok.read_text() == "existing"


def test_a_missing_client_secrets_file_fails_before_the_flow_is_built(tmp_path):
    from sluice.track import auth
    spy = []
    out = auth.run_consent_flow(client_secrets_path=str(tmp_path / "absent.json"),
                                token_path=str(tmp_path / "google_token.json"),
                                flow_factory=make_factory(_full(auth), spy))
    assert out["ok"] is False
    assert "client secrets" in out["reason"]
    assert spy == []


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX mode bits, and root ignores them anyway")
def test_an_unreadable_client_secrets_file_fails_before_the_flow_is_built(tmp_path):
    """`os.path.exists` only proves the DIRECTORY ENTRY is there. A file that exists but
    cannot be opened (wrong permissions here; an ACL or a directory in the wild) used to
    reach `InstalledAppFlow.from_client_secrets_file` inside the generic `except
    Exception` arm and surface as `the consent flow failed: PermissionError: ...` --
    the exact install-level-gap-reported-as-a-typo shape `probe_flow_available`'s own
    pre-flight above exists to keep out of the same function. Asserts the FACTORY was
    never called, same discipline as the sibling "missing" test above, and that the
    "not found" wording (which would be actively misleading for a file that IS there)
    does not leak into this arm's reason.
    """
    from sluice.track import auth
    p = _secrets(tmp_path)
    os.chmod(p, 0o000)
    try:
        spy = []
        out = auth.run_consent_flow(client_secrets_path=p,
                                    token_path=str(tmp_path / "google_token.json"),
                                    flow_factory=make_factory(_full(auth), spy))
    finally:
        os.chmod(p, 0o600)  # restore so tmp_path's own cleanup can remove it
    assert out["ok"] is False
    assert p in out["reason"]
    assert "not found" not in out["reason"], (
        "an unreadable file was reported as absent, which sends the user looking at the "
        "wrong problem")
    assert spy == [], "the consent flow was constructed before the unreadable-file refusal"


def test_a_credential_with_no_refresh_token_is_refused_and_nothing_is_written(tmp_path):
    """Without a refresh_token the credential works until it first expires, and there IS a
    diagnosis then -- `google_client.RealGoogleClient._creds` raises
    `GoogleAuthError("google token invalid and could not refresh")`, `cmd_track_run` exits
    1, and `docs/TROUBLESHOOTING.md`'s reauth remedy fires -- but that message never names
    the missing refresh_token, and the remedy it points at (delete the token, re-run
    `track auth`) discards a credential that was never actually viable rather than one
    that genuinely went bad. Refusing HERE, before anything is written, is what keeps that
    vague, delayed diagnosis from ever reaching a user in the first place."""
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    creds = FakeCreds(refresh_token=None, scopes=list(auth.SCOPES),
                      granted_scopes=list(auth.SCOPES))
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(creds))
    assert out["ok"] is False and "refresh_token" in out["reason"]
    assert not tok.exists()


def test_a_partially_granted_credential_is_refused_and_nothing_is_written(tmp_path):
    """Granular consent lets a user deselect a scope. A credential holding only
    gmail.readonly parses fine, doctor says OK, and every calendar call then fails as an
    ordinary per-message failure with `track run` exiting 0 -- so an interview is never
    booked and nothing says why."""
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    creds = FakeCreds(scopes=list(auth.SCOPES), granted_scopes=[auth.SCOPES[0]])
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(creds))
    assert out["ok"] is False
    # Pins the useful property -- the message NAMES the missing scope -- rather than a bare
    # `"scope" in reason`, which the word "scopes" alone satisfies and so does the
    # unrelated granted_scopes-absent message.
    assert auth.SCOPES[1] in out["reason"]
    assert not tok.exists()


def test_an_unreported_grant_fails_closed_rather_than_falling_back(tmp_path):
    """`granted_scopes` absent or None is a REFUSAL.

    The natural spelling -- `creds.granted_scopes or creds.scopes` -- silently restores
    the vacuous compare, and a stub whose two attributes merely differ never exercises
    this case. sluice cannot tell "no grant reported" from "grant complete".
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    creds = FakeCreds(scopes=list(auth.SCOPES), granted_scopes=None)
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(creds))
    assert out["ok"] is False and "granted_scopes" in out["reason"]
    assert not tok.exists()


def test_granted_scopes_as_a_space_delimited_string_is_not_character_set(tmp_path):
    """`google.oauth2.credentials.Credentials` documents `granted_scopes` as
    `Optional[Sequence[str]]`, and `str` IS a `Sequence[str]`. `set(granted)` on a bare
    string character-sets it -- `set("a b")` is `{"a", " ", "b"}` -- so every real scope
    URL fails the membership test below and a complete, correctly granted consent is
    refused with a message the user can never clear by granting more. Not reachable
    through the real library today (oauthlib's `parse_token_response` splits `scope` into
    a list), which is exactly why this drives the string form directly rather than
    trusting that shape to arrive on its own.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    creds = FakeCreds(scopes=list(auth.SCOPES), granted_scopes=" ".join(auth.SCOPES))
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(creds))
    assert out["ok"] is True, out["reason"]


def test_a_good_mint_writes_the_token_private(tmp_path, pinned_umask):
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(_full(auth)))
    assert out["ok"] is True, out["reason"]
    assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_a_concurrent_minter_wins_the_write_and_this_run_is_refused_not_clobbered(tmp_path):
    """The CALLER half of `write_token(exclusive=True)`'s guard, unexercised until now.

    Both this function's docstring and `write_token`'s own name it as THE guard against a
    second minter landing at `token_path` -- "the pre-flight is a COURTESY and not the
    guard" -- but every existing test here reaches `write_token` with the destination
    unoccupied: the four `exclusive` rows in `tests/test_track_google_client.py` call
    `write_token` directly, and on every path this file drives, the pre-flight
    `os.path.exists` refusal (or `--force`'s archive) already cleared the destination
    before this call's own write. None of that proves the KERNEL guard does anything --
    deleting `exclusive=True` from the call site in `run_consent_flow` is green against
    all of it.

    So: a `flow_factory` whose `run_local_server` plants a DIFFERENT token at
    `token_path` as a side effect -- standing in for a second `track auth` (or a
    hand-run script) finishing its own mint while THIS run's browser tab is still open,
    which is exactly the window between the pre-flight check above and the write at the
    end of this function. The rest of this run proceeds as if nothing happened (its own
    credential is fully granted), so the only thing that can stop it clobbering the
    concurrent mint is `write_token`'s O_EXCL reservation raising on the final write.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    concurrent = '{"refresh_token": "CONCURRENT_MINTER"}'

    class _RacingFlow:
        def __init__(self, creds):
            self._creds = creds

        def run_local_server(self, **kwargs):
            # Lands AFTER the pre-flight `os.path.exists(token_path)` refusal above has
            # already passed (the file did not exist when this function was entered) and
            # BEFORE this run's own `write_token(exclusive=True)` call below.
            tok.write_text(concurrent)

        @property
        def credentials(self):
            return self._creds

    def racing_factory(client_secrets_path, scopes):
        return _RacingFlow(_full(auth))

    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=racing_factory)
    assert out["ok"] is False
    assert "could not write the token" in out["reason"], out["reason"]
    assert tok.read_text() == concurrent, (
        "the concurrent minter's credential was clobbered by this run's write")


def test_the_flow_is_asked_for_offline_access_and_a_fresh_consent(tmp_path):
    """Passed explicitly rather than relying on run_local_server's defaults: a
    re-authorisation by a user who has already granted can return a credential with no
    refresh_token."""
    from sluice.track import auth
    spy = []
    auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                          token_path=str(tmp_path / "google_token.json"),
                          flow_factory=make_factory(_full(auth), spy))
    _, scopes, flow = spy[0]
    assert scopes == tuple(auth.SCOPES)
    assert flow.kwargs["access_type"] == "offline"
    assert flow.kwargs["prompt"] == "consent"


@pytest.mark.parametrize("kwargs,expected", [
    ({}, {"port": 0, "open_browser": True}),
    ({"port": 8765}, {"port": 8765, "open_browser": True}),
    ({"open_browser": False}, {"port": 0, "open_browser": False}),
])
def test_the_flags_reach_run_local_server(tmp_path, kwargs, expected):
    from sluice.track import auth
    spy = []
    auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                          token_path=str(tmp_path / "google_token.json"),
                          flow_factory=make_factory(_full(auth), spy), **kwargs)
    flow = spy[0][2]
    assert flow.kwargs["port"] == expected["port"]
    assert flow.kwargs["open_browser"] == expected["open_browser"]


def test_force_archives_the_old_token_before_replacing_it(tmp_path, pinned_umask):
    """The fixture starts at 0644 ON PURPOSE.

    `os.replace` is a rename and does not chmod, so a 0600 fixture passes whether or not
    the code sets the archive's mode -- and the population --force serves is exactly the
    one holding a 0644 token from the old hand-written script.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    tok.write_text('{"refresh_token": "OLD"}')
    os.chmod(tok, 0o644)
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), force=True,
                                flow_factory=make_factory(_full(auth)))
    assert out["ok"] is True, out["reason"]
    archive = out["archived"]
    assert archive and os.path.exists(archive)
    assert "OLD" in open(archive).read(), "the archive is not the old credential"
    assert stat.S_IMODE(os.stat(archive).st_mode) == 0o600


def test_a_failure_after_archiving_names_the_archive_in_the_reason(tmp_path, monkeypatch):
    """The window this sequence has of its own.

    Archive succeeds, the write then fails, and the user has NO token and a working
    credential at a sibling they have never heard of -- `track run` then holds the
    watermark and exits 0, the exact state this feature exists to remove. "The archive
    makes it recoverable" is worth nothing unless something tells them where it is.

    Patches `auth.write_token`, not `google_client.write_token`: `auth.py` binds the name
    at module scope via `from sluice.track.google_client import write_token`, so patching
    the attribute on `google_client` leaves the binding `auth` already holds untouched --
    the exact false-green shape this repo keeps finding, where a patch that cannot work
    lets a test pass without exercising anything.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    tok.write_text('{"refresh_token": "OLD"}')
    monkeypatch.setattr(auth, "write_token",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space")))
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), force=True,
                                flow_factory=make_factory(_full(auth)))
    assert out["ok"] is False
    assert out["archived"] and out["archived"] in out["reason"], (
        "the failure did not tell the user where their old credential went")


def test_a_refused_credential_leaves_an_existing_token_untouched_even_under_force(tmp_path):
    """The worst shape this module can take: `--force` moves the user's WORKING token to a
    sibling, and only then does a scope refusal fire -- reporting `archived: None`, so
    nothing tells them where their working credential went.

    Proven load-bearing rather than assumed: hoisting the archive block to just after
    `creds = flow.credentials` (ahead of every refusal below it) leaves every other test in
    this file green, because the three refusal tests never pass `force=True` with an
    existing token, and the three `force=True` tests all supply a fully-granted
    credential. This is the one test that combines both.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    original = '{"refresh_token": "ORIGINAL"}'
    tok.write_text(original)
    creds = FakeCreds(scopes=list(auth.SCOPES), granted_scopes=[auth.SCOPES[0]])
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), force=True,
                                flow_factory=make_factory(creds))
    assert out["ok"] is False
    assert out["archived"] is None
    assert tok.read_text() == original, "the existing token was moved despite the refusal"
    assert not [p for p in tmp_path.iterdir() if ".replaced-" in p.name], (
        "an archive sibling was created despite the refusal")


def test_a_to_json_failure_leaves_an_existing_token_untouched_even_under_force(tmp_path):
    """The sibling of the test above, for the OTHER refusal `--force` must not race:
    `payload = creds.to_json()` sits ABOVE the archive block on purpose -- a serialization
    failure is decided entirely from the in-memory credential, exactly like every scope/
    refresh_token check above it, so it must be reported (here: propagated) before
    `--force` moves the user's working token to a sibling path. `FakeCreds.to_json` never
    raises, so every existing test in this file -- including the two directly above, which
    drive the identical `--force`-plus-existing-token shape for a SCOPE refusal -- is blind
    to `to_json` moving back below the archive block. This one supplies a credential whose
    `to_json` does raise.
    """
    from sluice.track import auth

    class _UnserializableCreds(FakeCreds):
        def to_json(self):
            raise ValueError("cannot serialize this credential")

    tok = tmp_path / "google_token.json"
    original = '{"refresh_token": "ORIGINAL"}'
    tok.write_text(original)
    creds = _UnserializableCreds(scopes=list(auth.SCOPES), granted_scopes=list(auth.SCOPES))
    with pytest.raises(ValueError):
        auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                              token_path=str(tok), force=True,
                              flow_factory=make_factory(creds))
    assert tok.read_text() == original, "the existing token was moved despite the failure"
    assert not [p for p in tmp_path.iterdir() if ".replaced-" in p.name], (
        "an archive sibling was created despite the to_json failure")


def test_the_archive_rename_itself_failing_reports_no_archive(tmp_path, monkeypatch):
    """`os.replace` inside the `--force` archive step is wrapped in `except Exception` and
    returns `archived: None`. Nothing exercised that branch until this test.

    Patches `auth.os.replace`, not the failure shape used elsewhere in this file
    (`auth.write_token`) -- `auth.py` binds `os` at module scope via `import os`, so the
    name the code under test actually reads is the attribute on that module object. Same
    binding-vs-name-lookup reasoning this file's own
    `test_a_failure_after_archiving_names_the_archive_in_the_reason` docstring already
    states for `write_token`.

    The property that MATTERS is `archived is None`, not merely `ok is False`: the raise
    happens INSIDE `os.replace`, before any rename completes, so the archive was never
    created at all -- reporting a path here would send the user looking for their old
    credential somewhere it never reached, which is worse than reporting nothing.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    original = '{"refresh_token": "OLD"}'
    tok.write_text(original)

    def _raise(*args, **kwargs):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(auth.os, "replace", _raise)
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), force=True,
                                flow_factory=make_factory(_full(auth)))
    assert out["ok"] is False
    assert "could not archive the existing token" in out["reason"]
    assert out["archived"] is None
    assert tok.read_text() == original, "the existing token was replaced despite the failure"
    # The archive name is now RESERVED with O_CREAT|O_EXCL before the move, so the failure
    # arm has something of its own to clean up. Leaving it would strand an empty
    # `.replaced-` sibling beside a token that never moved -- which reads exactly like a
    # recovery artefact while holding nothing, the opposite of what the archive promises.
    assert not [p for p in tmp_path.iterdir() if ".replaced-" in p.name], (
        "the failed archive left its reservation behind")


def test_the_archive_name_is_reserved_not_merely_looked_up(tmp_path, pinned_umask):
    """The reservation is what decides a race, and `os.path.exists` cannot.

    An earlier cut walked the candidates with `os.path.exists` and returned a free NAME,
    leaving a window between that look and the caller's `os.replace`. Two concurrent
    `--force` runs both see the same name free; if the first has already written its new
    token by the time the second moves, the second overwrites the first's archive -- so the
    credential the archive exists to preserve is the one destroyed, and
    `write_token(exclusive=True)` runs afterwards and cannot prevent it.

    Asserting the file EXISTS after the call is what distinguishes reserving from looking:
    under the old shape both calls returned the same name and neither created anything.
    """
    from sluice.track import auth
    tok = tmp_path / "google_token.json"

    first = auth._reserve_archive_path(str(tok))
    assert os.path.exists(first), "the candidate was returned without being reserved"
    second = auth._reserve_archive_path(str(tok))
    assert second != first, "a second caller was handed a name the first already holds"
    assert os.path.exists(second)
    assert stat.S_IMODE(os.stat(first).st_mode) == 0o600


def test_an_unwritable_token_directory_is_reported_rather_than_raised(tmp_path):
    """The reservation CREATES, so it fails the way any create can -- and every other
    failure in this function is a report, not an exception.

    Measured across the change that introduced it: while `_reserve_archive_path` only
    LOOKED (`os.path.exists`, which cannot raise), this directory produced a clean
    "could not archive the existing token" report from `os.replace` inside the try.
    Once the function began CREATING its reservation, the `os.open` raised
    PermissionError straight out of `run_consent_flow`, whose whole contract is a
    report dict. Hardening the archive must not pay for itself with a traceback in the
    one situation -- a credential directory the user cannot write -- where they most
    need to be told which of their paths is the problem.
    """
    from sluice.track import auth
    secrets = _secrets(tmp_path)
    d = tmp_path / "state"
    d.mkdir()
    tok = d / "google_token.json"
    tok.write_text("existing")
    if not _dir_mode_is_enforced(d):
        os.chmod(d, 0o700)
        pytest.skip("0500 on a directory is not enforced here, so nothing would refuse "
                    "the reservation and this row would assert on an unrefused call")
    try:
        out = auth.run_consent_flow(client_secrets_path=secrets, token_path=str(tok),
                                    force=True, flow_factory=make_factory(_full(auth)))
    finally:
        os.chmod(d, 0o700)
    assert out["ok"] is False
    assert "reserve an archive name" in out["reason"], out["reason"]
    assert out["archived"] is None
    assert tok.read_text() == "existing", "the token moved despite the refusal"


def test_a_flow_failure_is_reported_rather_than_raised(tmp_path):
    """The most common non-success outcome of an interactive consent -- the user clicking
    Deny -- must not surface as a traceback; neither should a `--port` already bound.
    `flow_factory` itself is the simplest place to inject either failure shape, since a
    real `InstalledAppFlow.from_client_secrets_file`/`run_local_server` call can raise for
    both reasons."""
    from sluice.track import auth
    def factory(client_secrets_path, scopes):
        raise RuntimeError("access_denied")
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tmp_path / "google_token.json"),
                                flow_factory=factory)
    assert out["ok"] is False
    assert "RuntimeError" in out["reason"] and "access_denied" in out["reason"]


def test_a_keyboard_interrupt_during_the_flow_still_propagates(tmp_path):
    """`Exception`, never `BaseException` -- a user's Ctrl-C during the browser wait must
    stop the process, not get folded into an ordinary failure report."""
    from sluice.track import auth
    def factory(client_secrets_path, scopes):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                              token_path=str(tmp_path / "google_token.json"),
                              flow_factory=factory)


def test_the_archive_name_does_not_overwrite_a_same_second_archive(
        tmp_path, pinned_umask, monkeypatch):
    """Destroying one recovery artefact with another is the harm the archive prevents.

    The collision this test exists to force must be FORCED, not hoped for: without
    freezing the clock `_reserve_archive_path` reads, the two loop iterations below only collide
    when both land in the same wall-clock second, which a slow CI runner or a debugger
    pause can silently miss -- leaving the test green having exercised nothing. `auth.py`
    binds `datetime` at module scope via `import datetime`, so replacing that name (not
    patching the real stdlib `datetime.datetime` class, which would leak the freeze into
    every OTHER module sharing that same imported object for the life of the test) is
    what `_reserve_archive_path`'s own `datetime.datetime.now(...)` call actually reads.
    """
    from sluice.track import auth

    class _FrozenNow(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, tzinfo=tz)

    monkeypatch.setattr(
        auth, "datetime",
        types.SimpleNamespace(datetime=_FrozenNow, timezone=datetime.timezone))
    tok = tmp_path / "google_token.json"
    for marker in ("FIRST", "SECOND"):
        tok.write_text('{"refresh_token": "%s"}' % marker)
        out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                    token_path=str(tok), force=True,
                                    flow_factory=make_factory(_full(auth)))
        assert out["ok"] is True, out["reason"]
    archives = sorted(p for p in tmp_path.iterdir() if ".replaced-" in p.name)
    assert len(archives) == 2, f"an archive was overwritten: {archives}"
    assert archives[0].name.endswith("20260101T000000Z"), (
        "the clock was not actually frozen -- this run's real timestamp leaked through")
    assert archives[1].name.endswith("20260101T000000Z.1"), (
        "the second archive did not collide with the first at the frozen timestamp")


def test_the_flow_probe_reports_the_missing_package_without_naming_an_installed_extra(
        monkeypatch):
    """"Install the google extra" is a WRONG remedy on Homebrew and Docker, which both
    bake [google] already -- there the package is missing only through a probe skew.

    Forces the missing-package path with `sys.modules` (the pattern
    `test_probe_availability_ignores_the_consent_package` already uses) rather than
    relying on whatever happens to be installed in the venv running the suite -- a bare
    `if not ok: ...` is unfalsifiable in an environment where the package IS present, and
    the substantive assertion about NOT naming an extra was never checked at all.
    """
    from sluice.track import auth
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)
    ok, err = auth.probe_flow_available()
    assert ok is False
    assert "google_auth_oauthlib" in err
    assert "extra" not in err.lower(), (
        "the probe message told the reader to install an extra -- wrong on Homebrew and "
        "Docker installs that already bake [google] in, where the package is missing only "
        "through a probe skew")


def test_probe_availability_ignores_the_consent_package(monkeypatch):
    """The upgrade regression, in the form that catches a fusion INSIDE the probe.

    `pip install -U job-sluice` does not re-resolve extras, so every existing [google]
    install has google-api-python-client and google-auth and NOT google-auth-oauthlib,
    while `track run` keeps working. If someone adds the new import to this function,
    every one of those installs newly reports SETUP on upgrade.
    """
    from sluice.track.google_client import probe_availability
    for name in ("google", "google.auth", "google.auth.transport",
                 "google.auth.transport.requests", "google.oauth2",
                 "google.oauth2.credentials", "googleapiclient",
                 "googleapiclient.discovery"):
        mod = types.ModuleType(name)
        if name.endswith("requests"):
            mod.Request = object
        if name.endswith("credentials"):
            mod.Credentials = object
        if name.endswith("discovery"):
            mod.build = object
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)
    assert probe_availability() == (True, None), (
        "probe_availability now depends on google-auth-oauthlib, so every install that "
        "upgraded without re-resolving extras will newly report track SETUP")


def test_the_facade_carries_the_flow_factory_through(tmp_path, monkeypatch):
    """The seam must reach the path the CLI takes.

    Injecting only into run_consent_flow leaves `Sluice.track_auth` constructing the real
    InstalledAppFlow, so the flag-to-parameter mapping would be the one join no test can
    reach. Mirrors `Sluice.track(..., client=None)`, whose docstring already settles this:
    one shape, no config selecting among providers, so it is a test seam and not a
    `plugins.get` registry. No fifth seam is created.
    """
    from sluice.core.app import Sluice
    from sluice.track import auth
    from sluice.track.config import load_track_config
    # NO env var sets the token path: `load_track_config` resolves it with
    # `resolve(env_var=None, ...)`. conftest's autouse fixture sandboxes XDG_STATE_HOME,
    # so the resolved path is already under tmp -- read it back rather than trying to
    # steer it, which is what an env var that does not exist would have silently failed
    # to do while the assertions below passed against a path this test never controlled.
    expected = load_track_config().token_path
    spy = []
    out = Sluice(None).track_auth(client_secrets=_secrets(tmp_path), port=8765,
                                  open_browser=False,
                                  flow_factory=make_factory(_full(auth), spy))
    assert out["ok"] is True, out["reason"]
    assert out["token_path"] == expected
    assert os.path.exists(expected), "the facade wrote somewhere else"
    assert spy, "the facade did not use the injected factory"
    assert spy[0][2].kwargs["port"] == 8765
    assert spy[0][2].kwargs["open_browser"] is False


def test_the_facade_does_not_inherit_the_relocated_seen_db_refusal(tmp_path, monkeypatch):
    """`track auth` reads and writes neither the dedup store nor the dead-letter store.

    Every sibling passes refuse_relocated_seen_db=True and is right to: they report on
    those stores. Copying it here would let a moved track-seen.db refuse to mint a
    credential it has nothing to do with -- and the population reaching this command is
    exactly the one whose `track` has never successfully run.

    Asserted on the CALL rather than by relocating a store, because neither seen_db nor
    token_path takes an env var and the refusal short-circuits on an explicitly-named
    path anyway -- so a relocation staged through config would exercise the wrong arm.
    This goes red the moment someone adds the flag.

    `called` is a SEPARATE flag from `seen`, and both are asserted: if `track_auth` never
    reached `load_track_config` at all -- a refactor that resolves `token_path` some other
    way, say -- `seen` would stay empty and `not seen.get("refuse_relocated_seen_db")`
    would pass having proved nothing about the property this test names. Asserting `called`
    FIRST is what makes the second assertion mean what its message claims.
    """
    from sluice.core.app import Sluice
    from sluice.track import auth, config as track_config
    seen = {}
    called = []
    real = track_config.load_track_config

    def spy_loader(**kwargs):
        called.append(True)
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(track_config, "load_track_config", spy_loader)
    out = Sluice(None).track_auth(client_secrets=_secrets(tmp_path),
                                  flow_factory=make_factory(_full(auth)))
    assert out["ok"] is True, out["reason"]
    assert called, (
        "track_auth never called load_track_config at all -- the kwarg check below "
        "would pass having proved nothing")
    assert not seen.get("refuse_relocated_seen_db"), (
        "track auth inherited the dedup-store refusal; a moved track-seen.db would now "
        "block minting a credential unrelated to it")


def test_the_cli_exposes_track_auth_with_its_four_flags():
    from sluice.cli import _build_parser
    sub = _build_parser().parse_args(
        ["track", "auth", "--client-secrets", "cs.json", "--port", "8765",
         "--no-browser", "--force"])
    assert sub.client_secrets == "cs.json"
    assert sub.port == 8765
    assert sub.no_browser is True
    assert sub.force is True


def test_client_secrets_is_required():
    """An argument rather than a config key: the file is read exactly once, and after
    consent client_id and client_secret live in the token itself."""
    from sluice.cli import _build_parser
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["track", "auth"])


def test_cmd_track_auth_forwards_the_parsed_flags_to_the_facade(monkeypatch):
    """The two tests above inspect only the argparse `Namespace` -- neither calls
    `cmd_track_auth`. So a `cmd_track_auth` edited to drop `port=args.port`, hardcode
    `open_browser=True` regardless of `--no-browser`, or drop `force=args.force` would
    leave both green: the flags parse, but nothing proves they reach the facade.

    Mirrors `test_confirm_TELLS_the_operator_their_when_was_dropped`
    (`tests/test_track_dry_run_and_date_guards.py`), whose own docstring records the
    identical gap already caught once here for `cmd_track_confirm`: monkeypatch the
    facade method and capture what actually reaches it, rather than trusting the
    Namespace alone.
    """
    from sluice import cli
    from sluice.cli import _build_parser
    from sluice.core.config import Config

    captured = {}

    def fake_track_auth(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "reason": "", "token_path": "tok", "archived": None}

    monkeypatch.setattr("sluice.core.app.Sluice.track_auth", fake_track_auth)
    args = _build_parser().parse_args(
        ["track", "auth", "--client-secrets", "cs.json", "--port", "8765",
         "--no-browser", "--force"])
    cli.cmd_track_auth(args, Config())
    # `open_browser=False` is the one value TRANSFORMED rather than forwarded --
    # asserted explicitly, since a dropped `not` reads as identical to a correct
    # `open_browser=args.no_browser` on every OTHER field.
    assert captured == {
        "client_secrets": "cs.json", "port": 8765, "open_browser": False, "force": True}


def test_cmd_track_auth_returns_1_on_a_failed_mint(monkeypatch):
    """A failed mint of the documented install step exiting 0 is exactly the defect
    class #201 exists to remove: a cron wrapper or an `&&`-chained shell that checks the
    exit code would see success. Flipping this `return 1` to `return 0` is green against
    every OTHER test in this file, none of which inspects the return value on the
    failure arm."""
    from sluice import cli
    from sluice.cli import _build_parser
    from sluice.core.config import Config

    monkeypatch.setattr(
        "sluice.core.app.Sluice.track_auth",
        lambda self, **k: {"ok": False, "reason": "boom", "token_path": "tok",
                           "archived": None})
    args = _build_parser().parse_args(["track", "auth", "--client-secrets", "cs.json"])
    assert cli.cmd_track_auth(args, Config()) == 1


def test_cmd_track_auth_reports_the_archive_path_on_success(monkeypatch, capsys):
    """`run_consent_flow` names an archive INSIDE `out["reason"]` only on the failure arm
    (see `test_a_failure_after_archiving_names_the_archive_in_the_reason` above); on
    success `archived` is a separate field the CLI has to print itself, or a user whose
    old, still-working credential was just moved to an unannounced sibling learns nothing
    about it. Deleting the `if out["archived"]:` print is green against every other test
    here, none of which supplies a truthy `archived`.
    """
    from sluice import cli
    from sluice.cli import _build_parser
    from sluice.core.config import Config

    monkeypatch.setattr(
        "sluice.core.app.Sluice.track_auth",
        lambda self, **k: {"ok": True, "reason": "minted", "token_path": "tok",
                           "archived": "tok.replaced-20260101T000000Z"})
    args = _build_parser().parse_args(["track", "auth", "--client-secrets", "cs.json"])
    assert cli.cmd_track_auth(args, Config()) == 0
    err = capsys.readouterr().err
    assert "tok.replaced-20260101T000000Z" in err, f"the archive path was not reported: {err!r}"


def test_the_install_guide_scope_table_matches_the_real_scopes():
    """Derived on BOTH sides. A hand-listed expectation here would drift from SCOPES
    silently, and the table is what a user pastes into the consent screen.

    The split runs from the heading to EOF rather than to the next `##`, which is wider
    than the table it means to read -- deliberately, because a scope URL that drifted into
    a LATER section of the install guide is exactly as wrong as one that drifted out of
    this table, and the comparison is an equality, so the wider window catches it too. A
    renamed heading raises on `[1]` rather than comparing an empty set, so the vacuous
    shape is loud.
    """
    from pathlib import Path

    from sluice.track.auth import SCOPES
    text = (Path(__file__).parent.parent / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    section = text.split("## Google access for")[1]
    documented = set(re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", section))
    assert documented == set(SCOPES), (
        f"INSTALL.md's scope table and sluice/track/auth.py disagree: "
        f"only in doc {sorted(documented - set(SCOPES))}, "
        f"only in code {sorted(set(SCOPES) - documented)}")


def test_a_missing_consent_package_is_reported_by_the_probe_not_the_generic_arm(
        tmp_path, monkeypatch):
    """`probe_flow_available` had no production caller, so its message was unreachable.

    Measured on this very venv, which IS the population in question -- `[test]` does not
    install `google-auth-oauthlib` -- before the probe was wired in:
    `track-auth: the consent flow failed: ModuleNotFoundError: No module named
    'google_auth_oauthlib'`. The import failed inside the generic `except Exception`, so
    the crafted message the probe exists to produce reached nobody, on exactly the
    upgrade population it was written for.

    Three properties, and the ORDER one is why this passes a secrets path that does not
    exist: the reason is the probe's own message; the generic arm did not produce it; and
    it is reached ahead of the client-secrets check, because a missing package is a gap in
    the install rather than in the argument the user just typed.

    `flow_factory` is deliberately NOT supplied. The probe is guarded on its absence, and
    the sibling rows in this file are the other half of that guard: every one of them
    injects a fake and must keep passing with the package missing.
    """
    from sluice.track import auth
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", None)
    tok = tmp_path / "google_token.json"
    out = auth.run_consent_flow(client_secrets_path=str(tmp_path / "absent.json"),
                                token_path=str(tok))
    assert out["ok"] is False
    assert "google_auth_oauthlib" in out["reason"]
    assert "consent flow failed" not in out["reason"], (
        f"the missing package surfaced from the generic except arm: {out['reason']!r}")
    assert "client secrets" not in out["reason"], (
        f"the secrets check ran first, so an install gap is reported as a typo in what "
        f"the user typed: {out['reason']!r}")
    assert not tok.exists()
