"""`doctor` reports WHICH browser profile a run will use, and flags the config that selects none.

2026-08-15: the production runner set `CAMOFOX_SESSION=contract-scanner`, intending to select
a profile holding 335 cookies. Persistence keys on `sha256(userId)` and ignores sessionKey, so
the run used the cookie-less `default` profile. LinkedIn returned zero rows for eight-plus runs
and auto-retired, taking jobserve and indeed with it.

Nothing could have told the operator which profile was in play. `doctor` is where that belongs:
it is the one command whose entire job is "tell me what this install will actually do".

Config-only by construction. `Sluice.doctor`'s docstring states that it NEVER opens a browser,
and that invariant is worth more than a liveness check -- the failure here was a
misconfiguration, visible without touching the network.
"""
from sluice.core.doctor import DEGRADED, NOTICE, OK, classify_camofox


def _check(**kw):
    kw.setdefault("user_env", None)
    kw.setdefault("session_env", None)
    kw.setdefault("resolved_user", "default")
    kw.setdefault("auth_dependent_sources", ())
    return classify_camofox(**kw)


def test_it_names_the_profile_the_run_will_actually_use():
    # The operator's question is "whose cookies do I get?", and the answer is a hash on disk.
    # Printing it is what lets them correlate with ~/.camofox/profiles/<hash>/.
    c = _check(user_env="ian", resolved_user="ian")
    assert c.state == OK
    assert "ian" in c.detail
    # sha256("ian")[:32] -- the real profile directory name.
    assert "b54a95127a4b573f41e335fdbd339dcc" in c.detail


def test_the_default_profile_is_reported_not_hidden():
    c = _check(resolved_user="default")
    assert c.state == OK
    assert "default" in c.detail
    assert "37a8eec1ce19687d132fe29051dca629" in c.detail   # sha256("default")[:32]


def test_session_without_user_is_DEGRADED_and_says_what_to_do():
    # THE incident config. It must not read as healthy: the operator believes they selected a
    # profile and did not.
    c = _check(session_env="contract-scanner", resolved_user="default")
    assert c.state == DEGRADED
    assert "CAMOFOX_SESSION" in c.detail and "CAMOFOX_USER" in c.detail
    assert "contract-scanner" in c.detail, "must name the value that selected nothing"
    assert "ingest" in c.blocks, "this is what stops browser sources working"


def test_session_WITH_user_is_fine():
    # Coherent: a profile was chosen AND a session named. Flagging it would train the reader
    # to ignore the row.
    c = _check(user_env="ian", session_env="contract-scanner", resolved_user="ian")
    assert c.state == OK


def test_auth_dependent_sources_are_named_as_a_notice():
    """Connects the config row to its consequence.

    A reader looking at `CAMOFOX_USER=default` has no way to know that three sources will
    silently yield zero if that profile is logged out. NOTICE, not DEGRADED: an unauthenticated
    profile is legitimate (most sources need no login), so it must not affect the exit code.
    """
    c = _check(resolved_user="default", auth_dependent_sources=("linkedin",))
    assert c.state in (OK, NOTICE)
    assert "linkedin" in c.detail


def test_a_degraded_config_stays_degraded_even_with_auth_sources():
    # Precedence: the misconfiguration is the actionable fact and must not be softened into a
    # notice by the presence of auth-dependent sources.
    c = _check(session_env="x", resolved_user="default", auth_dependent_sources=("linkedin",))
    assert c.state == DEGRADED


def test_doctors_resolved_user_agrees_with_what_the_client_actually_uses(monkeypatch):
    """Two readings of the same fact must not drift.

    `doctor` resolves the profile from the environment (to avoid constructing a client, which
    warns); `Camofox` resolves it in __init__. If those ever disagreed, doctor would confidently
    report a profile the run does not use -- worse than reporting nothing, because it would be
    believed. Both read `DEFAULT_USER`, and this pins that they agree in both arms.
    """
    from sluice.core.camofox import DEFAULT_USER, Camofox

    monkeypatch.delenv("CAMOFOX_USER", raising=False)
    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    assert Camofox().user == DEFAULT_USER

    monkeypatch.setenv("CAMOFOX_USER", "ian")
    assert Camofox().user == "ian"


def test_the_camofox_row_actually_reaches_the_doctor_report(monkeypatch, tmp_path):
    """A classifier nothing calls is dead code that silently never fires.

    This is the second time in this change set that a producer was needed to make a new
    classification real -- the `auth` drift reason was the first -- so it is pinned rather
    than assumed.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    monkeypatch.setenv("CAMOFOX_SESSION", "contract-scanner")
    monkeypatch.delenv("CAMOFOX_USER", raising=False)
    rep = Sluice(Config()).doctor(offline=True, probe=lambda b: None)
    rows = [c for c in rep.components if c.component == "camofox"]
    assert rows, "doctor reported no camofox row at all"
    assert rows[0].state == DEGRADED, rows[0].detail
    assert "contract-scanner" in rows[0].detail


def test_the_report_names_the_REAL_auth_dependent_sources(monkeypatch):
    """The enumeration must actually find them.

    Found by a surviving mutant: replacing `_registry.all_sources()` with `[]` broke nothing,
    because the sibling tests assert on the profile name and pass whether the row is OK or
    NOTICE. An enumeration that silently yields nothing is the "a search that finds nothing
    proves nothing" shape -- the row would quietly stop warning the day it mattered.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.ingest import sources as registry

    expected = {s.id for s in registry.all_sources() if getattr(s, "auth_probe_js", None)}
    assert expected, "no source declares an auth probe -- this test has become vacuous"

    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    monkeypatch.setenv("CAMOFOX_USER", "ian")
    rep = Sluice(Config()).doctor(offline=True, probe=lambda b: None)
    row = [c for c in rep.components if c.component == "camofox"][0]
    assert row.state == NOTICE, row.detail
    for sid in expected:
        assert sid in row.detail, f"{sid} declares an auth probe but doctor did not name it"


def test_the_camofox_row_is_present_under_offline(monkeypatch):
    # The check is config-only, so --offline must not omit it: offline is exactly the mode
    # someone uses to sanity-check a config before a run.
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    monkeypatch.setenv("CAMOFOX_USER", "ian")
    rep = Sluice(Config()).doctor(offline=True, probe=lambda b: None)
    rows = [c for c in rep.components if c.component == "camofox"]
    assert rows and "ian" in rows[0].detail


def test_the_profile_hash_matches_the_servers_scheme():
    """Pinned against the real algorithm rather than a copied constant.

    The camofox persistence plugin documents "each userId gets a deterministic SHA256-hashed
    subdirectory"; the directory name is the first 32 hex chars. If that ever drifts, doctor
    would print a hash that matches nothing on disk -- a confidently wrong answer, which is
    worse than printing none.
    """
    import hashlib

    for user in ("ian", "default", "contract-scanner"):
        expected = hashlib.sha256(user.encode()).hexdigest()[:32]
        assert expected in _check(resolved_user=user).detail
