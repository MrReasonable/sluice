"""`doctor` reports WHICH browser profile a run will use, and flags the config that selects none.

2026-08-15: the production runner set `CAMOFOX_SESSION=example-session`, intending to select
a different, already-authenticated profile. Persistence keys on `sha256(userId)` and ignores sessionKey, so
the run used the cookie-less `default` profile. LinkedIn returned zero rows for eight-plus runs
and auto-retired, taking jobserve and indeed with it.

Nothing could have told the operator which profile was in play. `doctor` is where that belongs:
it is the one command whose entire job is "tell me what this install will actually do".

Config-only by construction. `Sluice.doctor`'s docstring states that it NEVER opens a browser,
and that invariant is worth more than a liveness check -- the failure here was a
misconfiguration, visible without touching the network.
"""
import hashlib

from sluice.core.doctor import DEGRADED, NOTICE, OK, classify_camofox


def _profile_hash(user):
    """The digest doctor should print, derived independently of `camofox_profile_dir`'s call
    site so a test failure names a real disagreement rather than a copied literal drifting.

    NOTE: this cannot verify the SERVER's scheme -- it restates this repo's belief about it.
    See `camofox_profile_dir` for why that belief is unverifiable from here.
    """
    return hashlib.sha256(user.encode()).hexdigest()[:32]


def _check(**kw):
    kw.setdefault("user_env", None)
    kw.setdefault("session_env", None)
    kw.setdefault("resolved_user", "default")
    kw.setdefault("probe_capable_sources", ())
    return classify_camofox(**kw)


def test_it_names_the_profile_the_run_will_actually_use():
    # The operator's question is "whose cookies do I get?", and the answer is a hash on disk.
    # Printing it is what lets them correlate with ~/.camofox/profiles/<hash>/.
    c = _check(user_env="example-user", resolved_user="example-user")
    assert c.state == OK
    assert "example-user" in c.detail
    # Derived, never hardcoded: a literal digest would both rot silently if the scheme
    # changed and bake a reversible preimage of whatever name it was generated from.
    assert f"({_profile_hash('example-user')})" in c.detail


def test_the_default_profile_is_reported_not_hidden():
    c = _check(resolved_user="default")
    assert c.state == OK
    assert "default" in c.detail
    assert f"({_profile_hash('default')})" in c.detail


def test_session_without_user_is_DEGRADED_and_says_what_to_do():
    # THE incident config. It must not read as healthy: the operator believes they selected a
    # profile and did not.
    c = _check(session_env="example-session", resolved_user="default")
    assert c.state == DEGRADED
    assert "CAMOFOX_SESSION" in c.detail and "CAMOFOX_USER" in c.detail
    assert "example-session" in c.detail, "must name the value that selected nothing"
    assert "ingest" in c.blocks, "a DEGRADED row must say what the failure costs"


def test_session_WITH_user_is_fine():
    # Coherent: a profile was chosen AND a session named. Flagging it would train the reader
    # to ignore the row.
    c = _check(user_env="example-user", session_env="example-session", resolved_user="example-user")
    assert c.state == OK


def test_probe_capable_sources_are_named_as_a_notice():
    """Connects the config row to its consequence.

    A reader looking at `CAMOFOX_USER=default` has no way to know what a logged-out profile
    would do. The source id here is a SYNTHETIC label: `classify_camofox` only formats it, so
    a real one would couple this unit test to whichever source happens to declare a probe.
    The two integration tests below deliberately DERIVE the real ids from the registry, which
    is the only place they belong. NOTICE, not DEGRADED: an unauthenticated profile is legitimate (most sources
    need no login), so it must not affect the exit code.

    The wording promises DETECTION, not coverage -- the probe is opt-in, so a source that
    needs a login and ships no probe is simply absent from this list. Claiming otherwise would
    have the row assert that every unlisted source is login-independent.
    """
    c = _check(resolved_user="default", probe_capable_sources=("example-source",))
    assert c.state == NOTICE
    assert "example-source" in c.detail
    assert "can detect" in c.detail, "must promise detection, not that it needs auth"
    assert "Other sources cannot" in c.detail, "must say the coverage is partial"


def test_healthy_rows_do_not_claim_to_block_ingest():
    """`ComponentCheck.blocks` names what a FAILURE costs.

    Setting it on the OK/NOTICE rows printed `blocks: ingest` beside a green row -- an
    operator reading a correctly configured install was told ingest was blocked. Every other
    classifier in this module omits `blocks` when healthy.
    """
    assert _check(resolved_user="default").blocks == ()
    assert _check(resolved_user="default", probe_capable_sources=("example-source",)).blocks == ()
    assert _check(session_env="x", resolved_user="default").blocks == ("ingest",)


def test_the_degraded_row_ALSO_names_what_will_silently_yield_zero():
    """The incident's own configuration: session set, a probe-capable source present.

    Branching instead of composing told the operator to set CAMOFOX_USER but not which
    sources were about to return nothing -- the half that connects the misconfiguration to
    the symptom they can actually see.
    """
    c = _check(session_env="x", resolved_user="default", probe_capable_sources=("example-source",))
    assert c.state == DEGRADED
    assert "CAMOFOX_USER" in c.detail and "example-source" in c.detail


def test_a_degraded_config_stays_degraded_even_with_auth_sources():
    # Precedence: the misconfiguration is the actionable fact and must not be softened into a
    # notice by the presence of auth-dependent sources.
    c = _check(session_env="x", resolved_user="default", probe_capable_sources=("example-source",))
    assert c.state == DEGRADED


def _shipped_sources():
    """Registered sources defined under `sluice.`, i.e. what an install actually ships.

    `SourceRegistry` is a global that tests register into without cleanup, so an unfiltered
    `all_sources()` can pick up another module's stray. Filtering by module states the
    population these assertions are about rather than depending on which test ran first.
    """
    from sluice.ingest import sources as registry

    return [s for s in registry.all_sources() if type(s).__module__.startswith("sluice.")]


def _probe_capable_ids():
    """The SAME predicate `Sluice.doctor` uses, not half of it.

    Doctor derives its set with `isinstance(s, BrowserListSource) and s.auth_probe_js`; these
    tests checked only the `auth_probe_js` half, so a non-list-shaped source declaring a probe
    would make the expectation and the production behaviour disagree -- and the test would
    report doctor as broken, or miss that it was.
    """
    from sluice.ingest.base import BrowserListSource

    return {s.id for s in _shipped_sources()
            if isinstance(s, BrowserListSource) and getattr(s, "auth_probe_js", None)}


def test_doctors_resolved_user_agrees_with_what_the_client_actually_uses(monkeypatch):
    """Two readings of the same fact must not drift.

    `doctor` resolves the profile from the environment (to avoid constructing a client, which
    warns); `Camofox` resolves it in __init__. If those ever disagreed, doctor would confidently
    report a profile the run does not use -- worse than reporting nothing, because it would be
    believed. Both read `DEFAULT_USER`, and this pins that they agree in both arms.

    Both READINGS have to appear, which is the correction here: this compared `Camofox().user`
    against a constant in one arm and a literal in the other, and never called `resolve_user`
    at all -- so the drift it exists to catch could happen with the test green. A test whose
    docstring names two things must touch two things.
    """
    from sluice.core.camofox import DEFAULT_USER, Camofox, resolve_user

    monkeypatch.delenv("CAMOFOX_USER", raising=False)
    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    assert Camofox().user == DEFAULT_USER
    assert resolve_user() == Camofox().user, "doctor and the client disagree when unset"

    monkeypatch.setenv("CAMOFOX_USER", "example-user")
    assert Camofox().user == "example-user"
    assert resolve_user() == Camofox().user, "doctor and the client disagree when set"


def test_the_camofox_row_actually_reaches_the_doctor_report(monkeypatch, tmp_path):
    """A classifier nothing calls is dead code that silently never fires.

    This is the second time in this change set that a producer was needed to make a new
    classification real -- the `auth` drift reason was the first -- so it is pinned rather
    than assumed.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    monkeypatch.setenv("CAMOFOX_SESSION", "example-session")
    monkeypatch.delenv("CAMOFOX_USER", raising=False)
    rep = Sluice(Config()).doctor(offline=True, probe=lambda b: None)
    rows = [c for c in rep.components if c.component == "camofox"]
    assert rows, "doctor reported no camofox row at all"
    assert rows[0].state == DEGRADED, rows[0].detail
    assert "example-session" in rows[0].detail


def test_the_report_names_the_REAL_auth_dependent_sources(monkeypatch):
    """The enumeration must actually find them.

    Found by a surviving mutant: replacing `_registry.all_sources()` with `[]` broke nothing,
    because the sibling tests assert on the profile name and pass whether the row is OK or
    NOTICE. An enumeration that silently yields nothing is the "a search that finds nothing
    proves nothing" shape -- the row would quietly stop warning the day it mattered.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    expected = _probe_capable_ids()
    assert expected, "no source declares an auth probe -- this test has become vacuous"

    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    monkeypatch.setenv("CAMOFOX_USER", "example-user")
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
    monkeypatch.setenv("CAMOFOX_USER", "example-user")
    rep = Sluice(Config()).doctor(offline=True, probe=lambda b: None)
    rows = [c for c in rep.components if c.component == "camofox"]
    assert rows and "example-user" in rows[0].detail


def test_the_profile_hash_matches_the_servers_scheme():
    """The digest doctor prints is the one `core.camofox.profile_dir` computes.

    HONEST LIMIT: this cannot verify the SERVER's scheme. Both sides recompute the same
    expression, so it catches a divergence between doctor and the camofox module -- and
    nothing about whether this repo's belief about the plugin matches reality. That belief is
    unverifiable from here and `profile_dir`'s docstring says so.
    """

    for user in ("example-user", "default", "example-session"):
        # Parenthesised: a bare `in` passes for a LONGER digest, since a 40-char hash
        # contains its own 32-char prefix -- so truncation drift would go unnoticed.
        assert f"({_profile_hash(user)})" in _check(resolved_user=user).detail


def test_doctor_and_the_client_agree_on_an_EXPORTED_BUT_EMPTY_user(monkeypatch):
    """`CAMOFOX_USER=$SOME_UNSET_VAR` in a runner script exports an empty string.

    The two readings used to disagree on it -- `os.environ.get(k, default)` treats an empty
    var as PRESENT, `os.environ.get(k) or default` treats it as absent -- so the client drove
    the profile named `""` while doctor reported `default`. Doctor confidently naming a
    profile the run does not use is the same shape as the misconfiguration this row exists to
    catch. One resolver now serves both.
    """
    from sluice.core.camofox import Camofox, resolve_user

    monkeypatch.setenv("CAMOFOX_USER", "")
    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    assert Camofox().user == resolve_user(), "the client and doctor disagree on an empty var"
    assert Camofox().user == "default", "an empty var must read as unset, not as a profile"


def test_the_report_does_not_name_sources_that_CANNOT_detect_a_logout(monkeypatch):
    """Pinned in the ABSENT direction too.

    The sibling asserts `expected subset of detail`, which a superset also satisfies -- so
    deleting the `auth_probe_js` filter, and naming every browser source as probe-capable,
    left the suite green. The row would then promise detection for ~20 sources that have none.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    capable = _probe_capable_ids()
    incapable = {s.id for s in _shipped_sources()} - capable
    assert capable and incapable, "both halves must be non-empty or this proves nothing"

    monkeypatch.delenv("CAMOFOX_SESSION", raising=False)
    monkeypatch.setenv("CAMOFOX_USER", "example-user")
    row = [c for c in Sluice(Config()).doctor(offline=True, probe=lambda b: None).components
           if c.component == "camofox"][0]
    named = [s for s in incapable if s in row.detail]
    assert not named, f"doctor promised detection for sources with no probe: {named}"
