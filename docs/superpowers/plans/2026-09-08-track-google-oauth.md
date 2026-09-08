# `job-sluice track auth` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `track` a supported command that mints its Google OAuth token, replacing the hand-written script `docs/INSTALL.md` currently walks users through, and fix the consent-screen defect that makes the documented procedure yield a credential that dies in seven days.

**Architecture:** A new `sluice/track/auth.py` owns the consent flow and its own availability probe, kept separate from `google_client.py`'s probe so `doctor` does not start requiring a package that existing installs lack. The flow is injected as a `flow_factory` all the way through `Sluice.track_auth`, so the path the CLI takes is the path the tests drive. Token writing reuses the one existing writer, which gains an `exclusive` parameter enforcing refusal at the write rather than at a pre-flight check.

**Tech Stack:** Python 3.12-3.14, stdlib only in `sluice/` except named exceptions; `google-auth-oauthlib` (new, `google` extra); pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-track-google-oauth-design.md` — read it alongside this plan. Every design decision here is argued there.

## Global Constraints

- `sluice/` is standard-library only except for NAMED exceptions in `.rulesync/rules/CLAUDE.md`. The new `google_auth_oauthlib` import must be **function-local**; at module scope `probe_flow_available` becomes unreachable and the remedy message is replaced by a raw traceback, while `sys.modules` stubbing keeps tests green.
- New dependency floors go at the MAJOR boundary: `google-auth-oauthlib>=1,<2`. A floor younger than a day is invisible to `brew update-python-resources` and fails the `homebrew` release job.
- Never cite a LINE NUMBER in a comment or docstring. Cite `file.py::symbol`, or quote the claim so `grep` finds it.
- Comments explain *why* — the invariant upheld, the bug prevented. Match the surrounding density.
- Conventional Commits (`feat(track): ...`, `test(track): ...`, `docs: ...`). No `!` — nothing here changes an existing user's install.
- No personal data in `sluice/` or `tests/`. The SSH example host is `example.invalid`.
- Tests are hermetic and offline. `google-auth` is NOT in the `[test]` extra: `import google` fails, so every success path stubs via `monkeypatch.setitem(sys.modules, ...)`.
- Mode assertions require the `pinned_umask` fixture, which is **file-local** to `tests/test_track_google_client.py`.
- Run `python -m pytest` and `ruff check sluice tests scripts` before every commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `sluice/track/google_client.py` | MODIFY. `_write_token` → `write_token`, gains `exclusive=`. |
| `sluice/track/auth.py` | CREATE. `SCOPES`, `probe_flow_available`, `run_consent_flow`. |
| `sluice/core/app.py` | MODIFY. `Sluice.track_auth`. |
| `sluice/cli.py` | MODIFY. `cmd_track_auth` + parser. |
| `sluice/core/doctor.py` | MODIFY. SETUP message; legacy NOTICE row. |
| `pyproject.toml`, `scripts/render_homebrew_formula.py`, `.github/workflows/ci.yml` | MODIFY. Dependency + both extras probes. |
| `tests/test_track_auth.py` | CREATE. The flow's own tests. |
| `tests/test_no_false_consent_flow_claim.py` | REPLACE. Pattern → ratchet. |
| `tests/test_docs_claims.py` | MODIFY. Flag guard. |
| Docs | MODIFY. INSTALL, README, TROUBLESHOOTING, USAGE, ARCHITECTURE, `.rulesync/`. |

---

### Task 1: `write_token` gains an exclusive create

**Files:**
- Modify: `sluice/track/google_client.py` (`_write_token`, and its call in `_creds`)
- Modify: `tests/test_track_google_client.py` (rename at every import site)
- Modify: `tests/test_state_file_tiers.py` (a docstring names `` `_write_token` `` in bare backticks; `test_citation_drift.py` checks only the `file.py::symbol` form, so nothing will fail if this is missed)
- Test: `tests/test_track_google_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `write_token(path: str, data: str, *, exclusive: bool = False) -> None`. Raises `FileExistsError` when `exclusive=True` and `path` exists. Task 2 calls it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_track_google_client.py`, beside the existing mode rows:

```python
def test_an_exclusive_write_refuses_an_existing_token(tmp_path, pinned_umask):
    """The refusal must be AT THE WRITE, not at a caller's pre-flight check.

    A caller that checks os.path.exists, runs a minutes-long consent flow, then writes
    is a stale snapshot: two concurrent `track auth` runs both clear the check and the
    last silently wins, possibly for a different Google account. Only the kernel can
    decide this, so O_EXCL decides it.
    """
    from sluice.track.google_client import write_token
    p = tmp_path / "google_token.json"
    write_token(str(p), '{"refresh_token": "FIRST"}', exclusive=True)
    with pytest.raises(FileExistsError):
        write_token(str(p), '{"refresh_token": "SECOND"}', exclusive=True)
    assert "FIRST" in p.read_text(), "the refused write clobbered the existing token"


def test_an_exclusive_write_creates_the_credential_private(tmp_path, pinned_umask):
    """0600, on the EXCLUSIVE path specifically.

    `open(path, "x")` gives O_CREAT|O_EXCL but cannot set a creation mode -- measured at
    0644 under umask 022, a world-readable credential carrying gmail.readonly and
    read-write calendar.events. Every other mode row in this file drives the no-flag
    path, so an unqualified assertion would pass while the mint path shipped 0644.
    """
    from sluice.track.google_client import write_token
    p = tmp_path / "google_token.json"
    write_token(str(p), "tok", exclusive=True)
    assert _mode(str(p)) == 0o600


def test_an_exclusive_write_leaves_no_temp_on_the_SUCCESS_path(tmp_path, pinned_umask):
    """The assertion that catches an os.link-shaped implementation.

    `os.link` creates a second name for the same inode rather than consuming the source,
    so a successful mint leaves a byte-identical copy of the refresh token behind, one
    per run. `test_an_interrupted_write_leaves_no_stray_temp` cannot see this: it patches
    os.replace to raise, so it only ever walks the failure arm.
    """
    from sluice.track.google_client import write_token
    write_token(str(tmp_path / "google_token.json"), "tok", exclusive=True)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_a_failure_after_the_reservation_adopts_no_empty_token(tmp_path, monkeypatch,
                                                               pinned_umask):
    """The reservation creates a 0-byte destination; a later failure must remove it.

    `classify_track_google` reports OK on file PRESENCE alone, so a 0-byte token left
    behind reads as `track google OK` for ever while every Gmail call fails.
    """
    from sluice.track import google_client as mod
    p = tmp_path / "google_token.json"
    monkeypatch.setattr(mod.os, "replace",
                        lambda *a: (_ for _ in ()).throw(OSError(28, "No space")))
    with pytest.raises(OSError):
        mod.write_token(str(p), "tok", exclusive=True)
    assert not p.exists(), "a 0-byte reservation was left where doctor reports OK"
    assert not [q for q in tmp_path.iterdir() if q.name.endswith(".tmp")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_track_google_client.py -k "exclusive or adopts_no_empty" -v`
Expected: FAIL — `ImportError: cannot import name 'write_token'`.

- [ ] **Step 3: Rename and add the parameter**

In `sluice/track/google_client.py`, rename `_write_token` to `write_token`, add the parameter, and keep the whole existing docstring — appending the paragraph below. Update the one production call inside `_creds` (`_write_token(self.token_path, creds.to_json())` → `write_token(...)`, no `exclusive`: a refresh legitimately overwrites).

```python
def write_token(path: str, data: str, *, exclusive: bool = False) -> None:
    # ... existing docstring, plus:
    """
    `exclusive=True` is the MINT path, and the refusal has to live here rather than in a
    caller. A caller that checks `os.path.exists`, runs a consent flow that can block for
    minutes, and then writes is the stale-snapshot shape this repo already knows is
    byte-identical to no guard -- the reason `update_fields`' `require_status` could not be
    hoisted into its caller either.

    The shape is `_reserve_and_move`'s (`core/vault.py`), not `_write(exclusive=True)`'s.
    That one opens with mode "x", which gives O_CREAT|O_EXCL but CANNOT set a creation
    mode: measured 0644 under umask 022, a world-readable credential. `os.link` is not it
    either -- `_reserve_and_move`'s docstring records that shape as rejected on #23, and it
    does not consume the temp, so every successful mint would leave a second copy of the
    refresh token beside the real one.

    So: reserve the destination with O_EXCL at 0600, then `os.replace` the temp over it.
    The replace carries mkstemp's 0600 and consumes the temp. If anything fails AFTER the
    reservation, the 0-byte destination is removed -- `classify_track_google` reports OK on
    presence alone, so an empty token would read as healthy for ever.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".google_token.", suffix=".tmp")
    reserved = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            reserved = True
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray 0600 temp behind on failure -- including on KeyboardInterrupt,
        # which is why this catches BaseException and re-raises rather than `except OSError`.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        if reserved:
            # Ownership is "our O_EXCL open returned a handle", never os.path.exists, which
            # a race could fool into unlinking a token a concurrent minter just landed.
            with contextlib.suppress(OSError):
                os.unlink(path)
        raise
```

- [ ] **Step 4: Update every reference to the old name**

Run: `grep -rn "_write_token" sluice tests` and rename each. Note `tests/test_track_google_client.py::test_the_refresh_path_writes_through_write_token` already carries the new name in its own title, and `tests/test_state_file_tiers.py` names it in a docstring.

- [ ] **Step 5: Run the full file, then the suite**

Run: `python -m pytest tests/test_track_google_client.py -v && python -m pytest -q`
Expected: PASS; suite total unchanged apart from the four new rows.

- [ ] **Step 6: Commit**

```bash
git add sluice/track/google_client.py tests/test_track_google_client.py tests/test_state_file_tiers.py
git commit -m "feat(track): give write_token an exclusive create for the mint path"
```

---

### Task 2: `sluice/track/auth.py` — scopes, probe, flow

**Files:**
- Create: `sluice/track/auth.py`
- Test: `tests/test_track_auth.py` (create)

**Interfaces:**
- Consumes: `write_token(path, data, *, exclusive=False)` from Task 1.
- Produces:
  - `SCOPES: tuple[str, ...]`
  - `probe_flow_available() -> tuple[bool, str | None]`
  - `run_consent_flow(*, client_secrets_path: str, token_path: str, port: int = 0, open_browser: bool = True, force: bool = False, flow_factory=None) -> dict` returning `{"ok": bool, "reason": str, "token_path": str, "archived": str | None}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_track_auth.py`. The stub models `scopes` and `granted_scopes` as DIFFERENT attributes, because conflating them is the defect the scope check exists to catch.

```python
"""`job-sluice track auth` (#201), offline.

The real InstalledAppFlow is never driven here. `google-auth` is not in the [test]
extra -- `import google` fails -- so the success paths stub through sys.modules, the
convention `tests/test_track_google_client.py` already uses.
"""
import os
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


def test_a_credential_with_no_refresh_token_is_refused_and_nothing_is_written(tmp_path):
    """Without a refresh_token the credential works for an hour and then dies with no
    diagnosis -- `_creds` requires one."""
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
    assert out["ok"] is False and "scope" in out["reason"]
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


def test_a_good_mint_writes_the_token_private(tmp_path, pinned_umask):
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), flow_factory=make_factory(_full(auth)))
    assert out["ok"] is True, out["reason"]
    assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


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
    """
    from sluice.track import auth
    from sluice.track import google_client
    tok = tmp_path / "google_token.json"
    tok.write_text('{"refresh_token": "OLD"}')
    monkeypatch.setattr(google_client, "write_token",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space")))
    out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                token_path=str(tok), force=True,
                                flow_factory=make_factory(_full(auth)))
    assert out["ok"] is False
    assert out["archived"] and out["archived"] in out["reason"], (
        "the failure did not tell the user where their old credential went")


def test_the_archive_name_does_not_overwrite_a_same_second_archive(tmp_path, pinned_umask):
    """Destroying one recovery artefact with another is the harm the archive prevents."""
    from sluice.track import auth
    tok = tmp_path / "google_token.json"
    for marker in ("FIRST", "SECOND"):
        tok.write_text('{"refresh_token": "%s"}' % marker)
        out = auth.run_consent_flow(client_secrets_path=_secrets(tmp_path),
                                    token_path=str(tok), force=True,
                                    flow_factory=make_factory(_full(auth)))
        assert out["ok"] is True, out["reason"]
    archives = sorted(p for p in tmp_path.iterdir() if ".replaced-" in p.name)
    assert len(archives) == 2, f"an archive was overwritten: {archives}"


def test_the_flow_probe_reports_the_missing_package_without_naming_an_installed_extra():
    """"Install the google extra" is a WRONG remedy on Homebrew and Docker, which both
    bake [google] already -- there the package is missing only through a probe skew."""
    from sluice.track import auth
    ok, err = auth.probe_flow_available()
    assert isinstance(ok, bool)
    if not ok:
        assert "google_auth_oauthlib" in err


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_track_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.track.auth'`.

- [ ] **Step 3: Write `sluice/track/auth.py`**

```python
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
import fails before the function that exists to report it politely can run -- and tests
would not notice, because `sys.modules` stubbing keeps them green either way.
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
        return False, f"google_auth_oauthlib is not importable ({e})"
    return True, None


def _default_flow_factory(client_secrets_path, scopes):
    from google_auth_oauthlib.flow import InstalledAppFlow
    return InstalledAppFlow.from_client_secrets_file(client_secrets_path, list(scopes))


def _archive_path(token_path: str) -> str:
    """A collision-suffixed sibling. A bare `.replaced-<UTC>` overwrites an archive from
    the same second, which is reachable on a fast retry -- and destroying one recovery
    artefact with another is the harm the archive exists to prevent."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{token_path}.replaced-{stamp}"
    candidate, n = base, 1
    while os.path.exists(candidate):
        candidate = f"{base}.{n}"
        n += 1
    return candidate


def run_consent_flow(*, client_secrets_path, token_path, port=0, open_browser=True,
                     force=False, flow_factory=None):
    """Mint a credential and write it to `token_path`. Returns a report dict.

    `{"ok": bool, "reason": str, "token_path": str, "archived": str | None}` -- a report
    rather than an exception, because the CLI turns each outcome into a distinct line and
    exit code, and because `mcp`-style callers cannot act on an exception's message.

    ORDER IS LOAD-BEARING. Everything that can be known before the browser opens is
    checked before it opens: a consent round-trip cannot be handed back, and refusing
    afterwards wastes the one step that needs a human. That pre-flight is a COURTESY and
    not the guard -- the guard is `write_token(exclusive=True)`, which refuses in the
    kernel. See `google_client.write_token`.

    The credential is validated IN MEMORY before anything is written. Verifying after the
    write would leave an unusable credential on disk -- and under `--force`, the working
    one it replaced is already archived away -- while `classify_track_google` reports OK on
    presence alone, so it would read as healthy for ever.
    """
    flow_factory = flow_factory or _default_flow_factory
    if not os.path.exists(client_secrets_path):
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": f"client secrets file not found at {client_secrets_path}"}
    if os.path.exists(token_path) and not force:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": (f"a token already exists at {token_path}; pass --force to "
                           "replace it (the existing one is archived beside it first)")}

    flow = flow_factory(client_secrets_path, SCOPES)
    # Both passed explicitly rather than trusting run_local_server's defaults: a
    # re-authorisation by a user who has already granted can return a credential carrying
    # no refresh_token, which `_creds` requires.
    flow.run_local_server(port=port, open_browser=open_browser,
                          access_type="offline", prompt="consent")
    creds = flow.credentials

    if not getattr(creds, "refresh_token", None):
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": "the minted credential carries no refresh_token, so it would "
                          "stop working within the hour; re-run the consent"}
    granted = getattr(creds, "granted_scopes", None)
    if not granted:
        # FAIL CLOSED. `granted or creds.scopes` is the natural spelling and silently
        # restores the vacuous compare: `scopes` is the list we REQUESTED, so comparing it
        # with itself passes in exactly the granular-consent case this checks for.
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": "the credential reported no granted_scopes, so sluice cannot "
                          "tell a complete grant from a partial one"}
    missing = [s for s in SCOPES if s not in set(granted)]
    if missing:
        return {"ok": False, "token_path": token_path, "archived": None,
                "reason": f"consent did not grant {', '.join(missing)} -- without it "
                          "track cannot do its job and would fail silently"}

    archived = None
    if force and os.path.exists(token_path):
        archived = _archive_path(token_path)
        os.replace(token_path, archived)
        # os.replace is a rename and does NOT chmod, so the archive inherits the old
        # inode's mode -- and the population --force serves is the one holding a 0644
        # token from the pre-#201 hand-written script.
        with contextlib.suppress(OSError):
            os.chmod(archived, 0o600)
    try:
        write_token(token_path, creds.to_json(), exclusive=True)
    except Exception as e:
        # The window this sequence has of its own: the archive succeeded and the write did
        # not, so the user has no token and a working credential somewhere they have never
        # heard of. Naming the archive is the whole of what makes it recoverable.
        where = f"; your previous credential is at {archived}" if archived else ""
        return {"ok": False, "token_path": token_path, "archived": archived,
                "reason": f"could not write the token: {e}{where}"}
    _log.info("track: wrote a Google credential to %s", token_path)
    return {"ok": True, "token_path": token_path, "archived": archived, "reason": "minted"}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_track_auth.py -v && ruff check sluice tests scripts`
Expected: PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add sluice/track/auth.py tests/test_track_auth.py
git commit -m "feat(track): add the OAuth consent flow behind an injected factory"
```

---

### Task 3: `Sluice.track_auth` facade

**Files:**
- Modify: `sluice/core/app.py` (after `track_dismiss`)
- Test: `tests/test_track_auth.py`

**Interfaces:**
- Consumes: `run_consent_flow(...)` from Task 2.
- Produces: `Sluice.track_auth(*, client_secrets, port=0, open_browser=True, force=False, flow_factory=None) -> dict` — the same dict Task 2 returns. Task 4 calls it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_track_auth.py`:

```python
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
    """
    from sluice.core.app import Sluice
    from sluice.track import auth, config as track_config
    seen = {}
    real = track_config.load_track_config

    def spy_loader(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(track_config, "load_track_config", spy_loader)
    out = Sluice(None).track_auth(client_secrets=_secrets(tmp_path),
                                  flow_factory=make_factory(_full(auth)))
    assert out["ok"] is True, out["reason"]
    assert not seen.get("refuse_relocated_seen_db"), (
        "track auth inherited the dedup-store refusal; a moved track-seen.db would now "
        "block minting a credential unrelated to it")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_track_auth.py -k facade -v`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'track_auth'`.

- [ ] **Step 3: Add the facade method**

In `sluice/core/app.py`, immediately after `track_dismiss`:

```python
    def track_auth(self, *, client_secrets, port=0, open_browser=True, force=False,
                   flow_factory=None):
        """Mint the Google credential `track run` reads. Returns `run_consent_flow`'s dict.

        `load_track_config()` WITHOUT `refuse_relocated_seen_db=True`, and that omission is
        deliberate rather than an oversight. Every sibling passes it because it reads or
        writes the dedup or dead-letter store and would report nothing to do against a
        relocated one. This command touches neither, so the refusal would only mean a moved
        `track-seen.db` blocks minting a credential unrelated to it -- and the population
        that reaches this command is precisely the one whose `track` has never run.

        `flow_factory` is threaded through rather than resolved here, exactly as `track`
        threads `client`: one shape, nothing in config selecting among implementations, so
        it is a test seam and not a `plugins.get` registry.
        """
        from sluice.track.auth import run_consent_flow
        from sluice.track.config import load_track_config

        tcfg = load_track_config()
        return run_consent_flow(client_secrets_path=client_secrets,
                                token_path=tcfg.token_path, port=port,
                                open_browser=open_browser, force=force,
                                flow_factory=flow_factory)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_track_auth.py -v && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_track_auth.py
git commit -m "feat(track): add Sluice.track_auth carrying the flow seam to the CLI path"
```

---

### Task 4: CLI wiring

**Files:**
- Modify: `sluice/cli.py` (a `cmd_track_auth` beside `cmd_track_dismiss`; a parser block after `tdis.set_defaults(func=cmd_track_dismiss)`)
- Test: `tests/test_track_auth.py`

**Interfaces:**
- Consumes: `Sluice.track_auth(...)` from Task 3.
- Produces: the `track auth` subcommand with `--client-secrets` (required), `--port` (int, default 0), `--no-browser`, `--force`. Tasks 8 and 9 assert against this parser.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_track_auth.py -k cli_exposes -v`
Expected: FAIL — argparse errors with `invalid choice: 'auth'`.

- [ ] **Step 3: Add the command function**

In `sluice/cli.py`, beside the other track commands. Lazy import, like every sibling:

```python
def cmd_track_auth(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).track_auth(client_secrets=args.client_secrets, port=args.port,
                                    open_browser=not args.no_browser, force=args.force)
    if out["ok"]:
        print(f"track-auth: wrote {out['token_path']}", file=sys.stderr)
        if out["archived"]:
            print(f"  previous credential archived at {out['archived']}", file=sys.stderr)
        return 0
    print(f"track-auth: {out['reason']}", file=sys.stderr)
    return 1
```

- [ ] **Step 4: Add the parser block**

Immediately after `tdis.set_defaults(func=cmd_track_dismiss)`:

```python
    tauth = track.add_parser("auth", help="mint the Google OAuth token track run needs")
    tauth.add_argument("--client-secrets", required=True,
                       help="path to the Desktop-app OAuth client JSON you downloaded "
                            "from the Google Cloud console")
    tauth.add_argument("--port", type=int, default=0,
                       help="bind this fixed port instead of an ephemeral one, so it can "
                            "be forwarded over SSH (see docs/INSTALL.md)")
    tauth.add_argument("--no-browser", action="store_true",
                       help="print the consent URL instead of opening a browser")
    tauth.add_argument("--force", action="store_true",
                       help="replace an existing token; the old one is archived beside it")
    tauth.set_defaults(func=cmd_track_auth)
```

- [ ] **Step 5: Run the tests, then the suite**

Run: `python -m pytest tests/test_track_auth.py -v && python -m pytest -q`
Expected: `tests/test_track_auth.py` passes. The suite now has EXPECTED failures in `tests/test_docs_claims.py` — a new subcommand is undocumented and the hand-edited literal is stale. Tasks 8 and 9 clear them. Note which fail; do not "fix" them here.

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py tests/test_track_auth.py
git commit -m "feat(cli): add job-sluice track auth"
```

---

### Task 5: `doctor` — remedy message and the legacy NOTICE

**Files:**
- Modify: `sluice/core/doctor.py` (`classify_track_google`: the SETUP message, and its docstring's stale exception count)
- Modify: `sluice/core/app.py` (pass whether a legacy token was found)
- Test: `tests/test_doctor.py` (follow the file's existing `classify_track_google` rows)

**Interfaces:**
- Consumes: the command name from Task 4.
- Produces: nothing later tasks call.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_missing_token_remedy_names_the_command_that_mints_one():
    from sluice.core.doctor import classify_track_google
    c = classify_track_google(available=True, import_error=None, token_present=False,
                              token_path="/x/google_token.json")
    assert "track auth" in c.detail
    assert "does not run the OAuth consent flow" not in c.detail, (
        "the disclaimer is false now that `track auth` exists")


def test_a_legacy_token_in_the_working_directory_is_reported():
    """`resolve`'s _LEGACY notice is keyed on the RESOLVED path not existing, and
    `paths.py` warns that a writer which creates it disarms the notice for ever. A user
    who followed the pre-XDG instructions has a live credential in their cwd; minting a
    new one at the resolved path would silence the only thing naming the old one.

    doctor is the durable home for that: it is the command whose job is to report a
    relocated file, and a row here survives the run that disarmed the warning.
    """
    from sluice.core.doctor import classify_track_google
    c = classify_track_google(available=True, import_error=None, token_present=True,
                              token_path="/x/google_token.json",
                              legacy_token_path="./google_token.json")
    assert "./google_token.json" in c.detail
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_doctor.py -k "remedy_names_the_command or legacy_token" -v`
Expected: FAIL — the old disclaimer text; then `TypeError` on the unknown kwarg.

- [ ] **Step 3: Edit `classify_track_google`**

Replace the `not token_present` message's second half, and add the parameter. The docstring currently says `sluice/` is stdlib-only "except for the three named, deliberate exceptions" — that count is already wrong before this change; **replace the count rather than increment it** ("except for the named, deliberate exceptions -- see CLAUDE.md").

```python
def classify_track_google(*, available: bool, import_error: str | None,
                          token_present: bool, token_path: str = "",
                          legacy_token_path: str = "") -> ComponentCheck:
    ...
    if not token_present:
        where = f" at {token_path}" if token_path else " at track.token_path"
        return ComponentCheck(
            "track", "google_token.json", SETUP,
            f"google libs are importable but no token file exists yet{where} -- "
            "`track run` cannot reach Gmail/Calendar until one does. Run "
            "`job-sluice track auth --client-secrets <your client_secret.json>`; see "
            "https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md"
            "#google-access-for-track for the Cloud console steps first",
            blocks=("track",))
    if legacy_token_path:
        # Reported, never acted on: doctor is the one command that must not refuse on a
        # relocated file, and this row is what survives after a mint at the resolved path
        # disarms `resolve`'s own _LEGACY notice.
        return ComponentCheck(
            "track", "google_token.json", SETUP,
            f"a token is in use{f' at {token_path}' if token_path else ''}, but a legacy "
            f"credential is still at {legacy_token_path} -- sluice no longer reads it. "
            "Delete it once you are sure nothing else uses it.")
    return ComponentCheck("track", "google", OK, "libs importable, token present")
```

In `sluice/core/app.py`'s doctor sweep, pass it — the legacy location comes from `core/paths.py`'s `_LEGACY` table, not a literal:

```python
        from sluice.core.paths import _LEGACY
        legacy = _LEGACY.get("google_token.json", "")
        legacy = legacy if legacy and os.path.exists(legacy) else ""
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_doctor.py -v && python -m pytest -q`
Expected: `test_doctor.py` passes; the Task 4 doc failures persist.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/doctor.py sluice/core/app.py tests/test_doctor.py
git commit -m "fix(doctor): name track auth as the remedy and report a legacy token"
```

---

### Task 6: Dependency and both extras probes

**Files:**
- Modify: `pyproject.toml` (the `google` extra)
- Modify: `scripts/render_homebrew_formula.py` (the `test do` import line)
- Modify: `.github/workflows/ci.yml` (the docker smoke-import)
- Test: `tests/test_homebrew_formula.py`, `tests/test_ci_wiring.py` (existing tests will fail until these agree)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks call.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_homebrew_formula.py`:

```python
def test_the_formula_probes_the_consent_package_too():
    """Both extras probes hand-list one top-level module per baked extra, and a skew
    degrades SILENTLY -- only as an ImportError the first time a user runs a
    Google-tracker command. The import name is google_auth_oauthlib, not the
    distribution name.
    """
    formula = render(**FIXTURE)
    assert "google_auth_oauthlib" in formula
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_homebrew_formula.py -k consent_package -v`
Expected: FAIL.

- [ ] **Step 3: Make the three edits**

`pyproject.toml`:

```toml
google = ["google-api-python-client>=2,<3", "google-auth>=2,<3",
          # `job-sluice track auth`'s consent flow (#201). Major-boundary floor, per this
          # file's own rule: the ceiling is what Dependabot tracks, and a floor younger
          # than a day is invisible to `brew update-python-resources`.
          "google-auth-oauthlib>=1,<2"]
```

`scripts/render_homebrew_formula.py`, the `test do` line:

```python
    system libexec/"bin/python", "-c", "import mcp, googleapiclient, google_auth_oauthlib, argcomplete"
```

`.github/workflows/ci.yml`, the docker smoke-import:

```yaml
        run: docker run --rm --entrypoint python job-sluice:ci -c "import weasyprint, jinja2, googleapiclient, google_auth_oauthlib, mcp, argcomplete"
```

- [ ] **Step 4: Run the packaging tests**

Run: `python -m pytest tests/test_homebrew_formula.py tests/test_ci_wiring.py tests/test_packaging.py -v`
Expected: PASS. If `test_ci_wiring.py` pins the import list explicitly, update that expectation too.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml scripts/render_homebrew_formula.py .github/workflows/ci.yml tests/test_homebrew_formula.py
git commit -m "build(google): add google-auth-oauthlib and probe it on both baked channels"
```

---

### Task 7: Replace the consent guard with a ratchet

**Files:**
- Replace: `tests/test_no_false_consent_flow_claim.py`
- Test: itself

**Interfaces:**
- Consumes: the `track auth` command from Task 4, doctor's message from Task 5.
- Produces: nothing later tasks call.

**Read the spec's "The guard" section before starting.** Three drafts specified this by a sorting criterion and a reviewer falsified each; the file's contents are specified directly for that reason. Do not sort the old file — write the replacement.

- [ ] **Step 1: Write the replacement**

Rewrite the module docstring to record why the pattern is gone, then:

```python
# The sentences that ACTUALLY SHIPPED false, kept as DATA. A ratchet over known values,
# not a classifier: nothing local can decide whether a new sentence misattributes consent,
# and the measurements below show why every attempt to pattern-match it failed.
_SHIPPED_FALSE = (
    "obtained on first `track run` via an interactive consent flow",
    "`track` will walk you through the interactive consent flow again",
    "google libs are importable but no token file exists yet -- the first `track run` "
    "will need an interactive OAuth consent",
)


def _norm(text):
    """Collapse whitespace on BOTH sides before comparing.

    The code half arrives pre-joined by `ast`, but the prose half is raw markdown and two
    of these sentences shipped in README/TROUBLESHOOTING, where a reflow moves the line
    breaks. Comparing raw would silently stop matching.
    """
    return " ".join(text.split())


def test_no_shipped_text_repeats_a_sentence_that_was_false():
    offenders = [(rel, s) for rel, texts in _shipped_texts() for s in _SHIPPED_FALSE
                 if any(_norm(s) in _norm(t) for t in texts)]
    assert not offenders, (
        f"shipped text repeats a sentence that was measured false: {offenders}. "
        "`track auth` mints the credential; `track run` still never prompts.")


@pytest.mark.parametrize("sentence", _SHIPPED_FALSE)
def test_the_comparator_catches_a_planted_sentence(tmp_path, sentence):
    """ANTI-VACUITY. A broken comparator finds nothing and reads as success, which is this
    guard's whole failure mode -- and the scope test below asserts what is READ, not what
    is FOUND, so it cannot cover this.

    Both shapes: markdown, and a re-wrapped Python literal (which `ast` joins, the hazard
    the original file was built around and which survives exact-string comparison).
    """
    md = f"Some prose.\n{sentence}\nMore prose.\n"
    assert any(_norm(sentence) in _norm(t) for t in _searchable("x.md", md))
    wrapped = 'MSG = (\n    "%s"\n    "%s"\n)\n' % (sentence[:20], sentence[20:])
    assert any(_norm(sentence) in _norm(t) for t in _searchable("x.py", wrapped))


def test_doctors_missing_token_remedy_names_a_real_command():
    """The one runtime string the original incident was measured on, and the one this
    change hand-edits."""
    from sluice.cli import _build_parser
    from sluice.core.doctor import classify_track_google
    detail = classify_track_google(available=True, import_error=None,
                                   token_present=False, token_path="/x/t.json").detail
    m = re.search(r"job-sluice (\w+) (\w+)", detail)
    assert m, f"doctor's remedy names no command: {detail!r}"
    _build_parser().parse_args([m.group(1), m.group(2), "--client-secrets", "x"])
```

Keep `_searchable`, `_identifiers`, `_shipped_texts`, `_PROSE`/`_CODE`/`_SHIPPED`, and `test_the_sweep_reads_the_files_it_means_to` unchanged. Delete `_CONSENT_CLAIM`, `_OAUTH_ENDPOINT`, `_FLOW_ENTRY` and every test referencing them — the mechanism now exists, so those assertions are false by design.

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_no_false_consent_flow_claim.py -v`
Expected: PASS.

- [ ] **Step 3: Prove the ratchet is load-bearing**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first. Then paste one `_SHIPPED_FALSE` sentence into `docs/TROUBLESHOOTING.md`, re-run the file by node id, confirm RED, and revert. Mutate by MOVING or DELETING, never adding.

- [ ] **Step 4: Commit**

```bash
git add tests/test_no_false_consent_flow_claim.py
git commit -m "test(track): ratchet the consent claims now that a real flow exists"
```

---

### Task 8: Flag guard

**Files:**
- Modify: `tests/test_docs_claims.py`

**Interfaces:**
- Consumes: the parser from Task 4.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

```python
def test_every_track_auth_flag_is_documented_both_ways():
    """`test_every_real_command_is_documented_in_usage_md` compares COMMANDS and never
    looks at flags. Bidirectional here, unlike its evidence-add model, which diffs
    `real - documented` only -- that catches a flag added to the parser but not one
    DROPPED from it while the doc still instructs it.
    """
    real = _parser_flags("track", "auth")
    assert real, "walked no flags for `track auth` -- the vacuous-pass shape"
    usage = dict(_read_all()).get("docs/USAGE.md", "")
    assert usage, "docs/USAGE.md was not readable, so this would pass vacuously"
    documented = _documented_flags(usage, "track", "auth")
    assert not (real - documented), f"undocumented: {sorted(real - documented)}"
    assert not (documented - real), f"documented but not real: {sorted(documented - real)}"


def test_every_track_auth_flag_used_in_prose_is_real():
    """The gap CLAUDE.md names: nothing runs a command in INSTALL.md against the thing
    serving it, so a renamed flag ships green. `_documented_flags` matches only a USAGE.md
    heading and returns the empty set for any other file, so this needs its own extractor.
    """
    real = _parser_flags("track", "auth")
    assert real, "walked no flags for `track auth`"
    seen, invoked = set(), False
    for rel, text in _read_all():
        for m in re.finditer(r"job-sluice track auth([^\n`]*)", text):
            invoked = True
            seen |= {f for f in re.findall(r"--[\w-]+", m.group(1))}
    assert invoked, "no doc invokes `job-sluice track auth` -- nothing was checked"
    assert not (seen - real), f"prose instructs flags the parser rejects: {sorted(seen - real)}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_docs_claims.py -k track_auth_flag -v`
Expected: FAIL — USAGE.md has no heading yet (Task 9 adds it).

- [ ] **Step 3: Commit after Task 9 turns them green**

These stay red until the docs land. Complete Task 9, then run both and commit together.

---

### Task 9: Documentation

**Files:**
- Modify: `docs/INSTALL.md` (the `## Google access for `track`` section — keep the heading's SLUG; `tests/test_doc_links_from_code.py` compares GitHub slugs, not bytes)
- Modify: `README.md` (the requirements row at "which you mint yourself"; the Commands table row `| \`job-sluice track\` | ... (\`run\`, \`confirm\`, \`dismiss\`) |` gains `auth`). **Do NOT add it to the `## Quickstart` section.** `tests/test_readme_quickstart.py` parses that heading and EXECUTES every command under it, asserting each is offline by construction; `track auth` is neither offline nor runnable without a client secrets file, so putting it there turns a green suite red in a way whose cause is two files away from the edit.
- Modify: `docs/TROUBLESHOOTING.md` (the `## \`track\` reauth needed` remedy)
- Modify: `docs/USAGE.md` (a `### \`job-sluice track auth ...\`` heading listing all four flags)
- Modify: `docs/ARCHITECTURE.md` (the track sub-app description AND its injected-collaborators roster — `flow_factory` is a METHOD parameter, so the existing sweep, bound to `Sluice.__init__` keywords, will not catch this)
- Modify: `tests/test_docs_claims.py::test_the_command_tree_walk_is_not_vacuous` (a hand-edited literal subcommand total; `track auth` moves it by one — its docstring says the edit IS the review step)
- Modify: `tests/test_track_engine.py` (a docstring asserts sluice "has no consent flow at all" and cross-references the guard file)
- Modify: `sluice/track/google_client.py` (`probe_availability`'s docstring says this module is "the ONE sanctioned site" for the google imports — false once `auth.py` ships)
- Modify: `.rulesync/rules/CLAUDE.md` (the stdlib-only exception list)

- [ ] **Step 1: Rewrite INSTALL's Google section**

Keep the Cloud project, enabled APIs and Desktop client. Delete the throwaway virtualenv and `get_token.py`. Add, as its own numbered step, the publishing-status fix:

> **Set the consent screen's publishing status to *In Production*.** A screen left in *Testing* — the default for a new one — issues refresh tokens that **expire after seven days**, so `track` would need re-authorising every week. Publishing removes that cap. The app stays unverified, so you will see a warning screen once and click through it; a user cap applies to unverified apps, which is irrelevant for a single user.

Then the command, and the headless line using `example.invalid`:

````markdown
```bash
job-sluice track auth --client-secrets ~/Downloads/client_secret_xxx.json
```

On a headless box or in the container, forward the port and skip the browser:

```bash
ssh -L 8765:localhost:8765 you@example.invalid
job-sluice track auth --client-secrets client_secret.json --port 8765 --no-browser
```
````

Keep the scope table — `tests/test_track_auth.py` will check it against `SCOPES` in Task 9 Step 3.

- [ ] **Step 2: Make the other prose edits**

Each is a small replacement; make them all before running anything. `.rulesync/rules/CLAUDE.md`'s entry must state the property, not just the module: *"`google_auth_oauthlib`, imported lazily inside functions in `sluice/track/auth.py`"* — module scope would make `probe_flow_available` unreachable while stubbing keeps tests green. Then run `npm ci --ignore-scripts && npm run rulesync`.

- [ ] **Step 3: Add the scope-table test**

```python
def test_the_install_guide_scope_table_matches_the_real_scopes():
    """Derived on BOTH sides. A hand-listed expectation here would drift from SCOPES
    silently, and the table is what a user pastes into the consent screen."""
    from pathlib import Path
    from sluice.track.auth import SCOPES
    text = (Path(__file__).parent.parent / "docs" / "INSTALL.md").read_text()
    section = text.split("## Google access for")[1]
    documented = set(re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", section))
    assert documented == set(SCOPES), (
        f"INSTALL.md's scope table and sluice/track/auth.py disagree: "
        f"only in doc {sorted(documented - set(SCOPES))}, "
        f"only in code {sorted(set(SCOPES) - documented)}")
```

- [ ] **Step 4: Run everything**

Run: `python -m pytest -q && ruff check sluice tests scripts`
Expected: the whole suite green, including Task 8's two tests and the docs sweeps from Task 4.

- [ ] **Step 5: Verify the documented command actually runs**

CLAUDE.md is explicit that nothing runs a command in INSTALL.md against the channel serving it, so a wrong invocation ships green. Run it:

```bash
job-sluice track auth --help
job-sluice track auth --client-secrets /nonexistent.json   # expect exit 1, named reason
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: document job-sluice track auth and the consent-screen publishing fix"
```

---

## Definition of Done

- [ ] `python -m pytest` green (5950 + the new rows, 7 skipped on macOS).
- [ ] `ruff check sluice tests scripts` clean.
- [ ] `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`, then each new guard mutated by MOVING or DELETING and confirmed RED by node id, with no pre-existing sibling catching the mutant.
- [ ] `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q` also green — this machine has tools CI does not, and that difference has shipped four failing tests before.
- [ ] `job-sluice track auth --help` runs, and the INSTALL.md invocation was executed rather than reasoned about.
- [ ] `/review-pr` run BEFORE pushing.
