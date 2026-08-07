"""sluice doctor: the pure enumeration/classification core, the Sluice.doctor
wiring (with an injected probe so it stays offline), and the cmd_doctor exit
codes. Everything here is hermetic -- no network, no browser, no real LLM."""
from dataclasses import dataclass

import pytest

from sluice.core.doctor import (
    DEAD, DEGRADED, NOTICE, OK, BackendCheck, BackendTarget, ComponentCheck,
    DoctorReport, RoleUse, classify, classify_cv_identity, classify_gate,
    classify_renderer, classify_store, classify_track_google, enumerate_targets,
    format_roles, list_typed_fields,
)


@pytest.fixture(autouse=True)
def _no_ambient_sluice_config(monkeypatch):
    # Hermeticity: the workflow docs instruct `export SLUICE_CONFIG=...`, and the
    # Sluice.doctor / cmd_doctor tests below assert the *default* backend
    # identities -- so a developer's exported config must not leak in and false-
    # fail them. Same guard the config-test modules use (test_sluice_neutral_
    # defaults.py, test_triage_config.py, test_config.py).
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def _harmless_components(monkeypatch):
    """`Sluice.doctor` now also classifies the renderer, cv identity, the
    store's on-disk artefacts and track's Google adapter (`ComponentCheck`,
    core/doctor.py) -- and every test below PREDATES that, written when doctor
    classified backends alone. On a bare, unconfigured `Sluice()` those four
    are genuinely broken (no vault at `./vault`, no WeasyPrint native libs in
    the test environment, `cv.name` still the shipped placeholder), which
    would fail every `exit_code() == 0` assertion below for reasons that have
    nothing to do with what each test is actually checking. Measured: without
    this fixture, 5 pre-existing tests in this file go red the moment
    Sluice.doctor grows components, none of them about a backend.

    `Sluice.store`/`Sluice.renderer` are patched to return a bare sentinel --
    `getattr(sentinel, "preflight", None)` is None (nothing to report, the
    documented optional-seam shape) and a sentinel is not RenderError, so
    construction "succeeds" trivially. `cv.config.load_cv_config` is patched
    to a name/contact-filled CvConfig so cv-identity classifies OK rather than
    DEAD on the placeholder.

    Composes correctly with the file's existing "read `load_cv_config()`, then
    `dataclasses.replace` a couple of fields, then monkeypatch it back"
    pattern (`test_doctor_probe_does_not_inherit_the_compose_timeout` and
    others): because fixtures apply before a test body runs, those tests'
    OWN call to `load_cv_config()` already observes this fixture's healthy
    default and carries `name`/`contact` through the `replace`. A test that
    wants the REAL renderer/store/cv-identity behaviour (below, in the
    component-check section) re-patches the same three targets locally --
    monkeypatch applies a test body's own patch after fixture setup, so the
    local one wins."""
    import dataclasses

    from sluice.core.app import Sluice
    from sluice.cv.config import CvConfig

    monkeypatch.setattr(Sluice, "store", lambda self: object())
    monkeypatch.setattr(Sluice, "renderer", lambda self, cvcfg: object())
    healthy_cv = dataclasses.replace(CvConfig(), name="Test Person",
                                     contact="test@example.invalid")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: healthy_cv)


# ── fakes: minimal config objects with just the fields enumerate reads ────────
@dataclass
class _Triage:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


@dataclass
class _Cv:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    compose_model: str = "claude-sonnet-4-5"
    compose_host: str = ""
    compose_claude_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


@dataclass
class _Track:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


def _target(provider="deepseek", model="deepseek-v4-flash", host="",
            claude_path="claude", roles=(("triage", "fallback"),)):
    return BackendTarget(provider=provider, model=model, host=host,
                         claude_path=claude_path,
                         uses=[RoleUse(s, r) for s, r in roles])


# ── enumerate_targets ─────────────────────────────────────────────────────────
def test_enumerate_dedupes_shared_backends():
    # Defaults: all three sub-apps share claude-max@sonnet-4-5 (primary) and
    # deepseek@v4-flash (fallback) -> exactly two targets, each with three uses.
    targets = enumerate_targets(_Triage(), _Cv(), _Track())
    assert len(targets) == 2
    by_provider = {t.provider: t for t in targets}
    assert set(by_provider) == {"claude-max", "deepseek"}
    assert {u.subapp for u in by_provider["claude-max"].uses} == {"triage", "cv", "track"}
    assert all(u.role == "primary" for u in by_provider["claude-max"].uses)
    assert by_provider["claude-max"].is_primary is True
    assert by_provider["deepseek"].is_primary is False


def test_enumerate_splits_on_per_subapp_model_override():
    # A cv-only model override must NOT collapse into triage/track's claude-max
    # target -- that is the "live model id, per sub-app" guarantee.
    cv = _Cv(compose_model="claude-opus-4-1")
    targets = enumerate_targets(_Triage(), cv, _Track())
    claude = [t for t in targets if t.provider == "claude-max"]
    assert len(claude) == 2
    models = {t.model for t in claude}
    assert models == {"claude-sonnet-4-5", "claude-opus-4-1"}


# ── classify ──────────────────────────────────────────────────────────────────
def test_classify_unknown_provider_is_dead_even_offline():
    t = _target(provider="gpt5", roles=(("triage", "fallback"),))
    c = classify(t, known=False, needs_key=True, key_present=False,
                 key_var="", cli_present=None, offline=True, probe_error=None)
    assert c.state == DEAD
    assert "gpt5" in c.detail


def test_classify_keyless_primary_is_dead():
    t = _target(provider="deepseek", roles=(("triage", "primary"),))
    c = classify(t, known=True, needs_key=True, key_present=False,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == DEAD
    assert "DEEPSEEK_API_KEY" in c.detail


def test_classify_keyless_fallback_is_degraded():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=False,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == DEGRADED
    assert "DEEPSEEK_API_KEY" in c.detail
    assert "primary-only" in c.detail


def test_classify_offline_ok_when_static_checks_pass():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=True,
                 probe_error=None)
    assert c.state == OK
    assert "offline" in c.detail


def test_classify_offline_claude_cli_missing_is_dead():
    t = _target(provider="claude-max", model="claude-sonnet-4-5",
                roles=(("triage", "primary"),))
    c = classify(t, known=True, needs_key=False, key_present=False,
                 key_var="", cli_present=False, offline=True, probe_error=None)
    assert c.state == DEAD
    assert "claude" in c.detail.lower()


def test_classify_live_probe_error_is_dead():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error="HTTP 401 from api.deepseek.com: bad key")
    assert c.state == DEAD
    assert "401" in c.detail


def test_classify_live_ok():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == OK


# ── DoctorReport.exit_code ────────────────────────────────────────────────────
def _check(state):
    return BackendCheck(target=_target(), state=state, detail="")


def test_exit_code_dead_is_nonzero():
    rep = DoctorReport(checks=[_check(OK), _check(DEAD)])
    assert rep.exit_code() == 1


def test_exit_code_degraded_is_zero_by_default_nonzero_strict():
    rep = DoctorReport(checks=[_check(OK), _check(DEGRADED)])
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 1


def test_exit_code_all_ok_is_zero():
    rep = DoctorReport(checks=[_check(OK), _check(OK)])
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 0


def test_format_roles_groups_by_role_in_order():
    uses = [RoleUse("triage", "primary"), RoleUse("cv", "primary"),
            RoleUse("track", "primary")]
    assert format_roles(uses) == "primary: triage, cv, track"


def test_enumerate_includes_claude_path_in_dedup_key():
    # rev-001: same provider/model/host, DIFFERENT claude binaries must NOT
    # collapse -- else only the first path is ever checked.
    triage = _Triage(claude_max_path="/opt/a/claude")
    track = _Track(claude_max_path="/opt/b/claude")
    targets = enumerate_targets(triage, _Cv(), track)
    claude = [t for t in targets if t.provider == "claude-max"]
    assert {t.claude_path for t in claude} == {"/opt/a/claude", "/opt/b/claude", "claude"}


def test_enumerate_merges_and_flags_mixed_primary_fallback_role():
    # tst-003: a backend used as BOTH primary and fallback (same key) dedupes to
    # one target that is_primary -- so the strict primary rule applies to it.
    cfg = _Triage(primary_backend="claude-max", claude_max_model="m",
                  claude_max_host="", claude_max_path="claude",
                  fallback_backend="claude-max", cheap_model="m")
    targets = enumerate_targets(cfg, _Cv(), _Track())
    merged = [t for t in targets
              if t.provider == "claude-max" and t.model == "m" and t.host == ""]
    assert len(merged) == 1
    assert {u.role for u in merged[0].uses} == {"primary", "fallback"}
    assert merged[0].is_primary is True
    assert format_roles(merged[0].uses) == "primary: triage; fallback: triage"


def test_classify_keyless_mixed_role_is_dead():
    # tst-003: is_primary wins -- a keyless per-token backend used as BOTH roles
    # is dead (a run using it as primary cannot happen), not merely degraded.
    t = _target(provider="deepseek",
                roles=(("triage", "primary"), ("cv", "fallback")))
    c = classify(t, known=True, needs_key=True, key_present=False,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == DEAD


# ── Sluice.doctor (impure wiring, with an injected probe so it stays offline) ──
from sluice.core.app import Sluice           # noqa: E402
from sluice.core.backends import (           # noqa: E402
    BackendError, OpenAiCompatibleBackend,
)

# Captured at IMPORT time, before any test's autouse fixture ever patches
# Sluice.renderer/Sluice.store -- the two typo'd-adapter-name tests below need
# the REAL seam resolution (to actually reach plugins.get and raise
# UnknownAdapter), not the _harmless_components fixture's bare sentinel, so
# they restore these via monkeypatch.setattr(Sluice, "renderer", _REAL_RENDERER)
# etc. rather than trying to "undo" a fixture patch that has not applied yet
# at collection time.
_REAL_RENDERER = Sluice.renderer
_REAL_STORE = Sluice.store


def _ok_probe(backend):
    """A round-trip that always succeeds -- returns nothing, raises nothing."""
    return None


def _deepseek_dies_probe(backend):
    """Succeed for claude-max, fail for the per-token (deepseek) backend --
    the exact 'keyed but silently non-functional fallback' scenario."""
    if isinstance(backend, OpenAiCompatibleBackend):
        raise BackendError("HTTP 401 from api.deepseek.com: invalid key")
    return None


def test_doctor_live_all_ok(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rep = Sluice().doctor(probe=_ok_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states == {"claude-max": OK, "deepseek": OK}
    assert rep.exit_code() == 0


def test_doctor_live_keyed_fallback_broken_is_dead(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rep = Sluice().doctor(probe=_deepseek_dies_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == OK
    assert states["deepseek"] == DEAD          # believed-in fallback, actually dead
    assert rep.exit_code() == 1                # dead -> non-zero even without --strict


def test_doctor_keyless_fallback_is_degraded_not_probed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []
    rep = Sluice().doctor(probe=lambda b: calls.append(b))
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["deepseek"] == DEGRADED
    # the keyless fallback is classified WITHOUT a round-trip (nothing to test)
    assert all(not isinstance(b, OpenAiCompatibleBackend) for b in calls)
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 1


def test_doctor_offline_skips_probe_and_checks_claude_cli(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    calls = []
    rep = Sluice().doctor(offline=True, probe=lambda b: calls.append(b))
    assert calls == []                          # offline never round-trips
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == OK           # CLI present
    assert states["deepseek"] == DEGRADED       # keyless fallback
    assert rep.exit_code() == 0


def test_doctor_offline_reports_dead_when_claude_cli_absent(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    rep = Sluice().doctor(offline=True, probe=_ok_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == DEAD
    assert rep.exit_code() == 1


def test_doctor_offline_dead_when_plugin_unregistered(monkeypatch):
    # A provider in DEFAULT_MODELS whose plugin failed to register is NOT usable:
    # make_backend would raise, and live mode catches that -- but --offline skips
    # make_backend, so `known` must also require registration or offline would
    # report the broken provider `ok`. Simulate deepseek's factory missing from
    # the seam. (CodeRabbit #21: validate registration, not just DEFAULT_MODELS.)
    monkeypatch.setattr(Sluice, "available", staticmethod(lambda seam: ["claude-max"]))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    rep = Sluice().doctor(offline=True, probe=_ok_probe)
    deepseek = next(c for c in rep.checks if c.target.provider == "deepseek")
    assert deepseek.state == DEAD
    assert rep.exit_code() == 1


def test_doctor_never_builds_a_fetcher(monkeypatch):
    # The offline guarantee, narrowed by ComponentCheck: doctor now legitimately
    # resolves the store (to reach its optional preflight hook) and the renderer
    # (construction IS the probe -- see classify_renderer), but a live Camofox
    # browser is still never touched. This test used to also pin "never builds a
    # store"; that half is now `test_doctor_store_preflight_writes_nothing` and
    # `test_a_store_without_preflight_reports_nothing_rather_than_raising` below,
    # which assert what doctor's store use actually does rather than that it
    # doesn't happen at all.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(Sluice, "fetcher",
                        lambda self: pytest.fail("doctor resolved a fetcher"))
    Sluice().doctor(probe=_ok_probe)            # must not fail


# tst-002: the elapsed field is printed, so pin when it is set vs None.
def test_doctor_records_elapsed_on_live_probe_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rep = Sluice().doctor(probe=_ok_probe)
    by = {c.target.provider: c for c in rep.checks}
    assert isinstance(by["claude-max"].elapsed, float)
    assert isinstance(by["deepseek"].elapsed, float)


def test_doctor_offline_leaves_elapsed_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    rep = Sluice().doctor(offline=True, probe=_ok_probe)
    assert all(c.elapsed is None for c in rep.checks)


def test_doctor_keyless_fallback_has_no_elapsed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rep = Sluice().doctor(probe=_ok_probe)
    deepseek = next(c for c in rep.checks if c.target.provider == "deepseek")
    assert deepseek.elapsed is None             # degraded, never probed


# tst-001: exercise unknown-provider and keyless-primary through the FULL
# Sluice.doctor wiring (not just pure classify) by injecting a sub-app config via
# the from-imported loader. Sluice.doctor does `from sluice.triage.config import
# load_triage_config` at call time, so patching the module attribute takes effect.
def test_doctor_unknown_provider_in_config_is_dead(monkeypatch):
    monkeypatch.setattr("sluice.triage.config.load_triage_config",
                        lambda: _Triage(fallback_backend="gpt5"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rep = Sluice().doctor(probe=_ok_probe)      # must not crash on the typo
    gpt5 = next(c for c in rep.checks if c.target.provider == "gpt5")
    assert gpt5.state == DEAD
    assert rep.exit_code() == 1


def test_doctor_keyless_primary_is_dead_through_wiring(monkeypatch):
    monkeypatch.setattr("sluice.triage.config.load_triage_config",
                        lambda: _Triage(primary_backend="deepseek"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rep = Sluice().doctor(probe=_ok_probe)
    assert any(c.state == DEAD and c.target.provider == "deepseek"
               and c.target.is_primary for c in rep.checks)
    assert rep.exit_code() == 1


def test_enumerate_matches_operation_backend_wiring(monkeypatch, tmp_path):
    # Doctor must probe the SAME backend a real run builds. Spy on Sluice.backend
    # to capture what each operation feeds it, and assert enumerate_targets derives
    # the identical primary/fallback per sub-app. DISTINCT sentinel values per
    # sub-app+field (via dataclasses.replace on the real configs, so unrelated
    # fields like seen_db stay intact) make this pin the mapping tightly: a
    # cross-field or cross-sub-app misread yields a value mismatch, not a silent
    # pass on an equal default. This is the arc-001 drift guard.
    import dataclasses

    from sluice.cv.config import load_cv_config
    from sluice.track.config import load_track_config
    from sluice.triage.config import load_triage_config

    tri = dataclasses.replace(
        load_triage_config(), primary_backend="tri-prov", claude_max_model="tri-model",
        claude_max_host="tri-host", claude_max_path="tri-path",
        fallback_backend="tri-fbprov", cheap_model="tri-fbmodel")
    cvc = dataclasses.replace(
        load_cv_config(), primary_backend="cv-prov", compose_model="cv-model",
        compose_host="cv-host", compose_claude_path="cv-path",
        fallback_backend="cv-fbprov", cheap_model="cv-fbmodel")
    trk = dataclasses.replace(
        load_track_config(), primary_backend="trk-prov", claude_max_model="trk-model",
        claude_max_host="trk-host", claude_max_path="trk-path",
        fallback_backend="trk-fbprov", cheap_model="trk-fbmodel")
    monkeypatch.setattr("sluice.triage.config.load_triage_config", lambda: tri)
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: cvc)
    # `**_` and not a bare lambda: `Sluice.track` passes refuse_relocated_seen_db=True
    # (#80), and a stub that does not accept the real signature fails with a TypeError
    # instead of exercising this test's actual assertion -- the same faithful-fake rule
    # the _FakeGoogle docstring in test_app_operations.py records.
    monkeypatch.setattr("sluice.track.config.load_track_config", lambda *a, **_: trk)

    class _Stop(Exception):
        pass

    def _capture(run):
        rec = {}

        def spy(self, role, *, primary_name, primary_model, effort, host,
                claude_path, fallback_name, fallback_model, timeout=None):
            rec["primary"] = (primary_name, primary_model, host, claude_path)
            rec["fallback"] = (fallback_name, fallback_model)
            # RECORDED, not discarded. Widening this fake to merely ACCEPT the #28
            # timeout would make the one test that inspects doctor-vs-cv wiring swallow
            # the new value -- a guard quietly narrowed by the change it was meant to
            # watch. test_doctor_probe_does_not_inherit_the_compose_timeout reads it.
            rec["timeout"] = timeout
            raise _Stop()

        monkeypatch.setattr(Sluice, "backend", spy)
        try:
            run()
        except _Stop:
            pass
        return rec

    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    wired = {
        "triage": _capture(lambda: Sluice().triage()),
        "cv": _capture(lambda: Sluice().compose_cv(all_shortlist=True, dry_run=True)),
        "track": _capture(lambda: Sluice().track(client=object())),
    }

    targets = enumerate_targets(tri, cvc, trk)
    derived = {}
    for t in targets:
        for u in t.uses:
            derived[(u.subapp, u.role)] = t

    for subapp, w in wired.items():
        p = derived[(subapp, "primary")]
        assert (p.provider, p.model, p.host, p.claude_path) == w["primary"], subapp
        f = derived[(subapp, "fallback")]
        assert (f.provider, f.model) == w["fallback"], subapp
        assert (f.host, f.claude_path) == ("", "claude"), subapp


# ── cmd_doctor / argparse (offline; live exit codes are covered via Sluice) ───
from sluice.cli import main                    # noqa: E402


def test_cli_doctor_offline_degraded_fallback_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "claude-max" in out and "deepseek" in out
    assert "degraded" in out


def test_cli_doctor_strict_fails_on_degraded(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    assert main(["doctor", "--offline", "--strict"]) == 1


def test_cli_doctor_offline_dead_when_claude_missing_exits_nonzero(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert main(["doctor", "--offline"]) == 1


def test_print_doctor_shows_elapsed_for_a_live_probe(capsys):
    # cmd_doctor cannot inject a probe, so its CLI tests only ever run offline
    # (elapsed=None). Exercise the live-elapsed format branch of _print_doctor
    # directly with a synthetic report so `(0.4s)` is actually rendered.
    from sluice.cli import _print_doctor

    target = BackendTarget(provider="claude-max", model="m", host="",
                           claude_path="claude", uses=[RoleUse("triage", "primary")])
    report = DoctorReport(checks=[BackendCheck(target, OK, "round-trip ok", elapsed=0.4)])
    _print_doctor(report, offline=False)
    out = capsys.readouterr().out
    assert "(0.4s)" in out
    assert "1 ok, 0 degraded, 0 dead" in out


def test_doctor_probe_does_not_inherit_the_compose_timeout(monkeypatch, tmp_path):
    """The exclusion was claimed only in a comment: making the probe inherit a
    compose-sized timeout left the whole suite green.

    `doctor` builds its own backend for PROBE_PROMPT, a two-token round trip. Borrowing a
    raised `cv.compose_timeout` would make the command you run BECAUSE something is wrong
    sit on a dead host for as long as that knob says. Asserted on the value `make_backend`
    actually receives, since that is where the borrowing would have to happen.
    """
    import dataclasses

    from sluice.core.backends import BackendError
    from sluice.cv.config import load_cv_config

    cvc = dataclasses.replace(load_cv_config(), compose_timeout=4321,
                              primary_backend="claude-max", compose_model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: cvc)

    seen = []

    def spy_make(name, model="", **kw):
        seen.append(kw.get("timeout"))
        raise BackendError("stop here -- construction is all this test needs")

    # `doctor` does `from sluice.core.backends import make_backend` at METHOD scope, so
    # the import re-runs per call and patching the source module is what takes effect.
    monkeypatch.setattr("sluice.core.backends.make_backend", spy_make)
    # No try/except: `Sluice.doctor` already catches BackendError around the
    # make_backend/probe pair, so a handler here is dead code that could only mask a
    # later regression while `seen` stayed non-empty and this test stayed green.
    Sluice().doctor(offline=False)
    assert seen, "doctor never constructed a backend, so this asserts nothing"
    assert all(t != 4321 for t in seen), (
        f"doctor's probe inherited cv.compose_timeout: {seen}. Its probe is a two-token "
        "round trip; a compose-sized budget makes doctor hang on the dead host it exists "
        "to diagnose.")


def test_an_option_like_host_is_reported_dead_not_raised(monkeypatch):
    """The reason the argv guard raises BackendError rather than ValueError.

    `Sluice.doctor` catches BackendError around construction and renders it as a `dead`
    check. Any other type escapes that handler and takes down the whole command -- and
    doctor is exactly what someone runs to be TOLD their host is misconfigured. Asserting
    the type at the constructor cannot see this; only running doctor can.

    `probe` is injected so no real round-trip happens: the refusal is at construction, so
    the probe must never be reached for the claude-max target anyway.
    """
    import dataclasses

    from sluice.cv.config import CvConfig

    cvc = dataclasses.replace(CvConfig(), compose_host="-oProxyCommand=id",
                              primary_backend="claude-max", compose_model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: cvc)

    report = Sluice().doctor(offline=False, probe=lambda b: None)
    bad = [c for c in report.checks if c.target.host == "-oProxyCommand=id"]
    assert bad, "the option-like host produced no check at all"
    assert bad[0].state == DEAD
    assert "host begins with '-'" in bad[0].detail
    assert report.exit_code() == 1
    # Construction is still ATTEMPTED on this live path (app.py builds and probes before
    # calling classify), so a raise of the wrong type still escapes `except BackendError`
    # and crashes the command rather than being reported. classify's rule short-circuits
    # the MESSAGE, not the construction, which is why this stays the test that pins the
    # type -- witnessed: swapping the raise to ValueError reds this node.


def test_offline_doctor_also_reports_an_option_like_host_dead(monkeypatch):
    """`--offline` never constructs a backend, so the constructor guard is unreachable
    there and this was measured reporting `ok` / exit 0 -- an offline run blessing a
    config the live run refuses.

    Offline is precisely the mode for checking a config without touching the network, and
    it already decides the other config-only faults (unknown provider, key unset, CLI not
    on PATH). A leading `-` is config-only too.
    """
    import dataclasses

    from sluice.cv.config import CvConfig

    cvc = dataclasses.replace(CvConfig(), compose_host="-oProxyCommand=id",
                              primary_backend="claude-max", compose_model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: cvc)

    report = Sluice().doctor(offline=True)
    bad = [c for c in report.checks if c.target.host == "-oProxyCommand=id"]
    assert bad, "the option-like host produced no check at all"
    assert bad[0].state == DEAD, f"offline blessed it: {bad[0].state} / {bad[0].detail}"
    assert "argument injection" in bad[0].detail
    assert report.exit_code() == 1


# ── component checks: pure classification ──────────────────────────────────
def test_classify_renderer_ok():
    c = classify_renderer(None)
    assert c.state == OK
    assert c.blocks == ()


def test_classify_renderer_dead_blocks_cv_and_names_the_fix():
    c = classify_renderer("weasyprint could not be imported")
    assert c.state == DEAD
    assert c.blocks == ("cv",)
    assert "weasyprint could not be imported" in c.detail
    # A DEAD renderer must say WHAT TO DO, not just that it is broken -- the
    # LOCATION field lesson (CLAUDE.md) applies here too: a refusal with no
    # actionable next step just relocates the confusion.
    assert "render" in c.detail and "cairo" in c.detail


def test_classify_cv_identity_placeholder_is_dead_blank_contact_is_degraded():
    checks = classify_cv_identity("Your Name", "", placeholder="Your Name")
    by_subject = {c.subject: c for c in checks}
    assert by_subject["cv.name"].state == DEAD
    assert by_subject["cv.name"].blocks == ("cv",)
    assert by_subject["cv.contact"].state == DEGRADED


def test_classify_cv_identity_configured_is_ok():
    checks = classify_cv_identity("Real Name", "real@example.invalid",
                                  placeholder="Your Name")
    assert all(c.state == OK for c in checks)


def test_classify_store_missing_vault_short_circuits_to_one_dead_row():
    # Four DEAD rows for one cause (a vault that does not exist) would bury the
    # actual problem -- classify_store must short-circuit rather than also
    # report baseline/criteria/experience as independently broken.
    checks = classify_store({"vault_exists": False})
    assert len(checks) == 1
    assert checks[0].state == DEAD
    assert checks[0].blocks == ("ingest", "triage", "cv", "apply", "track")


def test_classify_store_missing_baseline_is_dead_missing_profile_is_degraded():
    checks = classify_store({
        "vault_exists": True, "baseline_exists": False, "criteria_present": False,
        "experience_total": 0, "experience_verified": 0,
    })
    by_subject = {c.subject: c for c in checks}
    assert by_subject["baseline_rel"].state == DEAD
    assert by_subject["Judging Profile"].state == DEGRADED
    assert by_subject["Experience Library"].state == NOTICE


def test_classify_store_healthy_facts_are_ok():
    checks = classify_store({
        "vault_exists": True, "baseline_exists": True, "criteria_present": True,
        "experience_total": 10, "experience_verified": 8,
    })
    by_subject = {c.subject: c for c in checks}
    assert by_subject["baseline_rel"].state == OK
    assert by_subject["Judging Profile"].state == OK
    assert "8" in by_subject["Experience Library"].detail
    assert "10" in by_subject["Experience Library"].detail


def test_classify_store_none_facts_reports_nothing():
    # None means the store has no preflight() at all -- "cannot say" must not
    # be reported the same as "said something is wrong".
    assert classify_store(None) == []


def test_classify_track_google_unavailable_is_degraded():
    c = classify_track_google(available=False,
                              import_error="no module named googleapiclient",
                              token_present=False)
    assert c.state == DEGRADED
    assert "googleapiclient" in c.detail


def test_classify_track_google_no_token_is_degraded():
    c = classify_track_google(available=True, import_error=None, token_present=False)
    assert c.state == DEGRADED
    assert "token" in c.detail


def test_classify_track_google_ready_is_ok():
    c = classify_track_google(available=True, import_error=None, token_present=True)
    assert c.state == OK


def test_list_typed_fields_ignores_non_list_fields():
    @dataclass
    class _Sample:
        titles: list = None
        floor: int = 0

    obj = _Sample(titles=["a", "b"], floor=3)
    assert list_typed_fields(obj) == [("titles", ["a", "b"])]


def test_classify_gate_empty_is_abstaining_notice_nonempty_is_active_notice():
    abstaining = classify_gate("TriageConfig", "accept_titles", [])
    active = classify_gate("TriageConfig", "accept_titles", ["a", "b"])
    assert abstaining.state == NOTICE and "abstaining" in abstaining.detail
    assert active.state == NOTICE and "2" in active.detail
    assert abstaining.subject == active.subject == "TriageConfig.accept_titles"


def test_an_abstaining_gate_never_affects_the_exit_code():
    # The load-bearing exclusion: NOTICE must not contribute under --strict
    # either, or a fresh install that has not opted into every optional gate
    # would fail a cron job's --strict check on its own shipped defaults --
    # the 672ad2a class aimed at doctor's exit status instead of a lead.
    notice = ComponentCheck("gates", "TriageConfig.accept_titles", NOTICE,
                            "abstaining (empty)")
    rep = DoctorReport(checks=[], components=[notice])
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 0


# ── component checks: through the full Sluice.doctor wiring ──────────────────
def test_a_dead_renderer_fails_the_exit_code(monkeypatch):
    from sluice.core.protocols import RenderError

    def _raise(self, cvcfg):
        raise RenderError("weasyprint could not be imported")

    monkeypatch.setattr(Sluice, "renderer", _raise)   # overrides the autouse sentinel
    rep = Sluice().doctor(offline=True)
    renderer_checks = [c for c in rep.components if c.component == "renderer"]
    assert renderer_checks and renderer_checks[0].state == DEAD
    assert rep.exit_code() == 1


def test_a_typo_d_renderer_name_is_reported_dead_not_crashed(monkeypatch):
    # A misconfigured cv.renderer/store name raises plugins.UnknownAdapter at
    # RESOLUTION, before RenderError or the store's optional preflight() are
    # ever reachable. Witnessed: before this guard existed, a typo'd
    # cv.renderer crashed the WHOLE command with an uncaught UnknownAdapter,
    # losing every check already computed -- the opposite of what a
    # diagnostic tool run BECAUSE something is wrong should do.
    import dataclasses

    from sluice.cv.config import CvConfig

    cvc = dataclasses.replace(CvConfig(), renderer="bogus-typo",
                              name="Test Person", contact="test@example.invalid")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: cvc)
    # Restore the REAL renderer resolution -- the autouse fixture's sentinel
    # would swallow the typo silently, since it never calls plugins.get at all.
    monkeypatch.setattr(Sluice, "renderer", _REAL_RENDERER)
    rep = Sluice().doctor(offline=True)   # must not raise
    renderer_checks = [c for c in rep.components if c.component == "renderer"]
    assert renderer_checks and renderer_checks[0].state == DEAD
    assert "bogus-typo" in renderer_checks[0].detail
    assert rep.exit_code() == 1


def test_a_typo_d_store_name_is_reported_dead_not_crashed(monkeypatch):
    import dataclasses

    from sluice.core.config import Config

    cfg = dataclasses.replace(Config(), store="bogus-typo")
    # Restore the REAL store resolution -- the autouse fixture's sentinel
    # would swallow the typo silently, since it never calls plugins.get at all.
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)
    rep = Sluice(cfg).doctor(offline=True)   # must not raise
    store_checks = [c for c in rep.components if c.component == "store"]
    assert store_checks and store_checks[0].state == DEAD
    assert "bogus-typo" in store_checks[0].detail
    assert rep.exit_code() == 1


def test_an_invalid_lead_layout_is_reported_dead_not_crashed(monkeypatch):
    # A sibling of the two typo tests above: `Vault.__init__` raises
    # ValueError (not UnknownAdapter) for an unrecognized `lead_layout`
    # (`layout_subfolder`'s own guard, core/leads.py). `load_config()`
    # already validates this for the real CLI path (main() catches it before
    # Sluice is ever constructed), but a directly-constructed Config -- which
    # is exactly how a library caller or a test builds one -- bypasses that,
    # so Sluice.doctor() must guard it independently, the same as it already
    # guards an unknown store/renderer NAME at the same call site.
    import dataclasses

    from sluice.core.config import Config

    cfg = dataclasses.replace(Config(), lead_layout="bogus-layout")
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)
    rep = Sluice(cfg).doctor(offline=True)   # must not raise
    store_checks = [c for c in rep.components if c.component == "store"]
    assert store_checks and store_checks[0].state == DEAD
    assert "bogus-layout" in store_checks[0].detail
    assert rep.exit_code() == 1


def test_sluice_doctor_wires_the_loaded_cv_config_into_cv_identity(monkeypatch):
    # Closes a real gap: every OTHER test in this file runs under the autouse
    # _harmless_components fixture's FIXED healthy CvConfig, so none of them
    # prove Sluice.doctor actually reads cv_cfg.name/cv_cfg.contact rather
    # than a hardcoded stand-in. Witnessed: hardcoding classify_cv_identity's
    # inputs in core/app.py to constants left the whole suite green.
    import dataclasses

    from sluice.cv.config import CvConfig

    custom = dataclasses.replace(CvConfig(), name="Distinctive Custom Name",
                                 contact="distinctive@example.invalid")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: custom)
    rep = Sluice().doctor(offline=True)
    by_subject = {c.subject: c for c in rep.components if c.component == "cv-identity"}
    assert by_subject["cv.name"].state == OK
    assert by_subject["cv.contact"].state == OK

    placeholder = CvConfig()   # name still "Your Name", contact still ""
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: placeholder)
    rep = Sluice().doctor(offline=True)
    by_subject = {c.subject: c for c in rep.components if c.component == "cv-identity"}
    assert by_subject["cv.name"].state == DEAD
    assert by_subject["cv.contact"].state == DEGRADED


def test_sluice_doctor_wires_a_real_vaults_preflight_into_store_components(monkeypatch, tmp_path):
    # Closes a real gap: Vault.preflight()'s dict keys are never checked
    # against what classify_store actually reads, and no test builds a REAL
    # Vault with real artefacts on disk and checks the resulting components.
    # Witnessed: renaming baseline_exists -> baseline_exist in
    # Vault.preflight left the whole suite green (classify_store's .get()
    # silently reads the typo'd key as absent).
    from sluice.core.vault import Vault

    (tmp_path / "My CV").mkdir()
    (tmp_path / "My CV" / "CV.md").write_text("# Baseline\n", encoding="utf-8")
    (tmp_path / "Job Applications").mkdir()
    (tmp_path / "Job Applications" / "Judging Profile.md").write_text(
        "criteria\n", encoding="utf-8")
    vault = Vault(str(tmp_path))
    monkeypatch.setattr(Sluice, "store", lambda self: vault)

    rep = Sluice().doctor(offline=True)
    by_subject = {c.subject: c for c in rep.components if c.component == "store"}
    assert by_subject["baseline_rel"].state == OK
    assert by_subject["Judging Profile"].state == OK


def test_sluice_doctor_wires_the_real_token_path_into_track_google(monkeypatch, tmp_path):
    # Same gap, one level narrower: track_cfg.token_path -> os.path.exists(...)
    # is never exercised with a REAL path in any test above (the autouse
    # fixture and every other test leave it at whatever the real environment
    # resolves to). Witnessed: hardcoding token_present=True in core/app.py
    # left the whole suite green.
    import dataclasses

    from sluice.track.config import TrackConfig

    monkeypatch.setattr("sluice.track.google_client.probe_availability",
                        lambda: (True, None))
    token_path = tmp_path / "google_token.json"
    trk = dataclasses.replace(TrackConfig(), token_path=str(token_path))
    monkeypatch.setattr("sluice.track.config.load_track_config", lambda *a, **k: trk)
    rep = Sluice().doctor(offline=True)
    track_checks = [c for c in rep.components if c.component == "track"]
    assert track_checks and track_checks[0].state == DEGRADED
    assert "token" in track_checks[0].detail

    token_path.write_text("{}", encoding="utf-8")
    rep = Sluice().doctor(offline=True)
    track_checks = [c for c in rep.components if c.component == "track"]
    assert track_checks and track_checks[0].state == OK


def test_every_preference_gate_appears_in_the_posture_report():
    # SCOPE assertion, not a violations assertion -- CLAUDE.md's own lesson: a
    # sweep whose derivation enumerates nothing would make the membership
    # check below pass vacuously. Derived from the same dataclasses
    # Sluice.doctor loads, via the same list_typed_fields helper it calls, so
    # a gate added to any of them cannot silently go unreported by BOTH sides
    # agreeing to ignore it.
    from sluice.apply.config import ApplyConfig
    from sluice.core.config import Config
    from sluice.cv.config import CvConfig
    from sluice.track.config import TrackConfig
    from sluice.triage.config import TriageConfig

    expected = {
        f"{type(cfg).__name__}.{name}"
        for cfg in (Config(), TriageConfig(), CvConfig(), TrackConfig(), ApplyConfig())
        for name, _ in list_typed_fields(cfg)
    }
    assert expected, "the derivation enumerated no list-typed gate at all"

    rep = Sluice().doctor(offline=True)
    reported = {c.subject for c in rep.components if c.component == "gates"}
    assert reported == expected


def test_doctor_store_preflight_writes_nothing(monkeypatch, tmp_path):
    # The mutation-testable form of what test_doctor_never_builds_a_fetcher used
    # to also claim about the store: doctor now legitimately resolves it, so
    # "never touched" is no longer true -- "touches it and writes nothing" is
    # the property that actually matters (see #81's relocation-notice hazard:
    # even a read that CREATES a file disarms it for every later run).
    from sluice.core.vault import Vault

    vault = Vault(str(tmp_path))
    monkeypatch.setattr(Sluice, "store", lambda self: vault)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    Sluice().doctor(probe=_ok_probe)
    after = sorted(str(p) for p in tmp_path.rglob("*"))
    assert before == after == [], (
        f"store.preflight() created something: before={before} after={after}")


def test_a_store_without_preflight_reports_nothing_rather_than_raising(monkeypatch):
    # The getattr seam, same shape as cv/engine.py's optional Renderer.precheck:
    # a store that does not implement preflight() is not a store that is
    # broken, so it must contribute zero component rows, not a crash.
    class _StoreWithoutPreflight:
        pass

    monkeypatch.setattr(Sluice, "store", lambda self: _StoreWithoutPreflight())
    rep = Sluice().doctor(offline=True)   # must not raise
    assert not [c for c in rep.components if c.component == "store"]


def test_a_store_whose_preflight_raises_is_reported_dead_not_crashed(monkeypatch):
    class _BrokenStore:
        def preflight(self):
            raise PermissionError("cannot stat vault_dir")

    monkeypatch.setattr(Sluice, "store", lambda self: _BrokenStore())
    rep = Sluice().doctor(offline=True)   # must not raise -- doctor diagnoses, never crashes
    store_checks = [c for c in rep.components if c.component == "store"]
    assert store_checks and store_checks[0].state == DEAD
    assert "cannot stat vault_dir" in store_checks[0].detail
    assert rep.exit_code() == 1


def test_cli_doctor_prints_component_section(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "gates" in out
    assert "notice" in out
    # The backend table's own summary line is untouched -- pinned verbatim by
    # test_print_doctor_shows_elapsed_for_a_live_probe -- and a second summary
    # line for components follows it, counting a fourth state the first line
    # never has to.
    assert " notice\n" in out
