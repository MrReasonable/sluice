"""sluice doctor: the pure enumeration/classification core, the Sluice.doctor
wiring (with an injected probe so it stays offline), and the cmd_doctor exit
codes. Everything here is hermetic -- no network, no browser, no real LLM."""
from dataclasses import dataclass

import pytest

from sluice.core.doctor import (
    DEAD, DEGRADED, OK, BackendCheck, BackendTarget, DoctorReport, RoleUse,
    classify, enumerate_targets, format_roles,
)


@pytest.fixture(autouse=True)
def _no_ambient_sluice_config(monkeypatch):
    # Hermeticity: the workflow docs instruct `export SLUICE_CONFIG=...`, and the
    # Sluice.doctor / cmd_doctor tests below assert the *default* backend
    # identities -- so a developer's exported config must not leak in and false-
    # fail them. Same guard the config-test modules use (test_sluice_neutral_
    # defaults.py, test_triage_config.py, test_config.py).
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)


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


def test_doctor_never_builds_a_store_or_browser(monkeypatch):
    # The offline guarantee: doctor touches only the backend seam.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(Sluice, "store",
                        lambda self: pytest.fail("doctor resolved a store"))
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
