"""sluice doctor: the pure enumeration/classification core, the Sluice.doctor
wiring (with an injected probe so it stays offline), and the cmd_doctor exit
codes. Everything here is hermetic -- no network, no browser, no real LLM."""
import os
from dataclasses import dataclass

import pytest

from sluice.core.doctor import (
    DEAD, DEGRADED, NOTICE, OK, BackendCheck, BackendTarget, ComponentCheck,
    DoctorReport, RoleUse, classify, classify_dossier_cache, classify_gate,
    classify_negatives_vs_skills, classify_renderer, classify_skills_reconciliation,
    classify_store, classify_track_google, enumerate_targets,
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
    """`Sluice.doctor` now also classifies the renderer, the store's on-disk
    artefacts and track's Google adapter (`ComponentCheck`, core/doctor.py) --
    and every test below PREDATES that, written when doctor classified
    backends alone. On a bare, unconfigured `Sluice()` two of those are
    genuinely broken (no vault at `./vault`, no WeasyPrint native libs in the
    test environment), which would fail every `exit_code() == 0` assertion
    below for reasons that have nothing to do with what each test is actually
    checking. Measured: without this fixture, 5 pre-existing tests in this
    file go red the moment Sluice.doctor grows components, none of them about
    a backend.

    `Sluice.store`/`Sluice.renderer` are patched to return a bare sentinel --
    `getattr(sentinel, "preflight", None)` is None (nothing to report, the
    documented optional-seam shape) and a sentinel is not RenderError, so
    construction "succeeds" trivially. `cv.config.load_cv_config` is patched
    to a bare `CvConfig()` too -- #133/#107 removed CvConfig's `name`/
    `contact` fields entirely, so there is no longer a cv_cfg-shaped identity
    check for this fixture to keep healthy (identity now lives in the vault's
    Candidate Profile note, and the `store` sentinel above already makes that
    check report nothing rather than DEAD).

    Composes correctly with the file's existing "read `load_cv_config()`, then
    `dataclasses.replace` a couple of fields, then monkeypatch it back"
    pattern (`test_doctor_probe_does_not_inherit_the_compose_timeout` and
    others): because fixtures apply before a test body runs, those tests'
    OWN call to `load_cv_config()` already observes this fixture's patched
    default and carries it through the `replace`. A test that wants the REAL
    renderer/store behaviour (below, in the component-check section)
    re-patches the same three targets locally -- monkeypatch applies a test
    body's own patch after fixture setup, so the local one wins."""
    from sluice.core.app import Sluice
    from sluice.cv.config import CvConfig

    monkeypatch.setattr(Sluice, "store", lambda self: object())
    monkeypatch.setattr(Sluice, "renderer", lambda self, cvcfg: object())
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda *a, **k: CvConfig())


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


def _one(checks, subject):
    """The single ComponentCheck matching `subject`, or a loud failure naming
    what was actually there -- never a silent `None`/IndexError that would let
    a test pass on a check that never ran (CLAUDE.md's own recurring lesson:
    a sweep matching nothing must not read as success)."""
    matches = [c for c in checks if c.subject == subject]
    assert len(matches) == 1, f"expected exactly one {subject!r} check, got {matches}"
    return matches[0]


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
from sluice.cv.config import load_cv_config  # noqa: E402

# Captured at IMPORT time, before any test's autouse fixture ever patches
# Sluice.renderer/Sluice.store -- the two typo'd-adapter-name tests below need
# the REAL seam resolution (to actually reach plugins.get and raise
# UnknownAdapter), not the _harmless_components fixture's bare sentinel, so
# they restore these via monkeypatch.setattr(Sluice, "renderer", _REAL_RENDERER)
# etc. rather than trying to "undo" a fixture patch that has not applied yet
# at collection time.
_REAL_RENDERER = Sluice.renderer
_REAL_STORE = Sluice.store
# Same shape, for `load_cv_config` (a module-level function, not a Sluice method,
# so there is no unbound-method form to capture -- the function object itself).
# `test_a_real_legacy_cv_config_is_caught_end_to_end_by_doctor` restores this one
# so it exercises the REAL migration guard in cv/config.py, not the autouse
# fixture's bare-CvConfig stand-in.
_REAL_LOAD_CV_CONFIG = load_cv_config


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


def test_enumerate_reflects_the_real_fallback_host_when_fallback_is_claude_max(monkeypatch):
    """#117 follow-up (round-3 review of PR #114): `_make_fallback` now forwards
    host/claude_path when the fallback role IS claude-max (a real remote-host install
    can name claude-max as either role), so a real run built off `Sluice.backend()`
    probes that host. `enumerate_targets`'s own spec list hardcoded ("", "claude") for
    EVERY fallback unconditionally -- true before #117, stale the moment it shipped,
    and the drift guard above never catches it because none of its three sub-app
    fixtures configure fallback_backend="claude-max". Doctor exists specifically to
    catch a silently-non-functional fallback before the primary dies; this is that
    exact failure class, reintroduced in doctor's own enumeration."""
    import dataclasses

    from sluice.cv.config import load_cv_config
    from sluice.track.config import load_track_config
    from sluice.triage.config import load_triage_config

    tri = dataclasses.replace(
        load_triage_config(), primary_backend="deepseek",
        fallback_backend="claude-max", claude_max_host="tri-fallback-host",
        claude_max_path="tri-fallback-path")

    targets = enumerate_targets(tri, load_cv_config(), load_track_config())
    fallback = next(t for t in targets for u in t.uses
                    if u.subapp == "triage" and u.role == "fallback")
    assert (fallback.host, fallback.claude_path) == \
        ("tri-fallback-host", "tri-fallback-path")


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
    # The two candidate keys are included here (Task 8 added them): a "healthy"
    # facts dict that omits them still passed this test before, only because it
    # never looked at the Candidate Profile row -- but `classify_store` itself
    # would have quietly returned a DEAD row from a facts dict this test's own
    # name calls healthy, which is precisely the drift this file's fix-round
    # review caught.
    checks = classify_store({
        "vault_exists": True, "baseline_exists": True, "criteria_present": True,
        "experience_total": 10, "experience_verified": 8,
        "candidate_name_present": True, "candidate_contact_present": True,
    })
    by_subject = {c.subject: c for c in checks}
    assert by_subject["baseline_rel"].state == OK
    assert by_subject["Judging Profile"].state == OK
    assert "8" in by_subject["Experience Library"].detail
    assert "10" in by_subject["Experience Library"].detail
    assert by_subject["Candidate Profile"].state == OK


def test_classify_store_none_facts_reports_nothing():
    # None means the store has no preflight() at all -- "cannot say" must not
    # be reported the same as "said something is wrong".
    assert classify_store(None) == []


# ── classify_store: the Candidate Profile row (#133/#107) ────────────────────
_HEALTHY_STORE_FACTS = {
    "vault_exists": True, "baseline_exists": True, "criteria_present": True,
    "experience_verified": 3,
}


def test_a_blank_candidate_profile_is_dead_and_blocks_cv():
    checks = classify_store({**_HEALTHY_STORE_FACTS, "candidate_name_present": False,
                             "candidate_contact_present": False})
    c = _one(checks, "Candidate Profile")
    assert c.state == DEAD
    assert c.blocks == ("cv",)


def test_a_declared_name_with_blank_contact_is_still_dead():
    # The half-declared shape cv/engine.py's skipped-config gate itself refuses on
    # (#107's real report: a name alone reached compose, paying a dossier fetch and
    # an LLM call, before failing the header STRUCTURAL guard on every attempt).
    checks = classify_store({**_HEALTHY_STORE_FACTS, "candidate_name_present": True,
                             "candidate_contact_present": False})
    assert _one(checks, "Candidate Profile").state == DEAD


def test_a_declared_contact_with_blank_name_is_still_dead():
    # The mirror-image half-declared shape -- distinct from the name-only case
    # above so a fix that only checks one of the two facts cannot pass both.
    checks = classify_store({**_HEALTHY_STORE_FACTS, "candidate_name_present": False,
                             "candidate_contact_present": True})
    assert _one(checks, "Candidate Profile").state == DEAD


def test_a_fully_declared_identity_is_ok():
    checks = classify_store({**_HEALTHY_STORE_FACTS, "candidate_name_present": True,
                             "candidate_contact_present": True})
    assert _one(checks, "Candidate Profile").state == OK


def test_the_dead_message_does_not_nudge_disclosure_of_the_other_fields():
    """doctor reports what blocks a command. "Fill in the rest for better apply
    automation" reads as a prompt to supply ethnicity, religion, sexual orientation
    and disability to a tool telling you something is wrong."""
    c = _one(classify_store({**_HEALTHY_STORE_FACTS, "candidate_name_present": False,
                             "candidate_contact_present": False}), "Candidate Profile")
    lowered = c.detail.lower()
    for word in ("ethnicity", "monitoring", "equal-opportunit", "the rest", "apply"):
        assert word not in lowered


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


# ── classify_dossier_cache (#169) ─────────────────────────────────────────────
def test_classify_dossier_cache_reports_a_distribution_not_a_verdict():
    # A distribution is descriptive: it changes nothing about which leads are
    # judged, so it is not the shipped judgement a threshold verdict would be. And
    # it is never inert -- at the shipped `min_jd_chars: 0` a threshold count
    # against that floor would be identically zero, leaving the accepted residual
    # (#169 decision 3) invisible, which is how #169 was found in the first place
    # (a human hand-counting a real cache).
    # Arbitrary counts, deliberately not #169's measured ones: the assertion is that each
    # number REACHES the detail string, which any numbers exercise identically, and a
    # fixture is the one position where a real install's figures buy nothing at all.
    check = classify_dossier_cache({"total": 91, "empty": 13, "under_200": 27,
                                    "under_800": 58})
    assert check.state == NOTICE
    assert "27" in check.detail and "91" in check.detail


def test_classify_dossier_cache_names_every_bucket():
    # Not just the two figures the brief happens to assert on -- every number the
    # facts dict carries must reach the printed report, or a bucket could silently
    # stop being reported while this test stayed green on the other two. Includes
    # "unreadable" (#169 fix round): a broken cache FILE (corrupt JSON, unreadable
    # outright) is a distinct fact from an "empty" JD (a fetch that produced nothing),
    # so it must appear in the detail string as its own figure, not vanish into
    # "empty"'s count.
    # Arbitrary again, and distinct enough that no expected figure is a substring of
    # another (or of the "200"/"800" bucket labels), which is what keeps a bucket that
    # stopped being printed from passing on somebody else's digits.
    check = classify_dossier_cache({"total": 91, "unreadable": 4, "empty": 13,
                                    "under_200": 27, "under_800": 58})
    for n in ("91", "4", "13", "27", "58"):
        assert n in check.detail, check.detail


def test_classify_dossier_cache_is_always_a_notice_never_a_severity():
    # Mirrors classify_gate: an install's own scraped-data shape is a fact worth
    # knowing, not a defect doctor should fail a run -- let alone a `--strict`
    # one -- over. A short-JD-heavy cache is not evidence the PIPELINE is broken.
    check = classify_dossier_cache(
        {"total": 5, "unreadable": 0, "empty": 5, "under_200": 5, "under_800": 5})
    assert check.state == NOTICE


def test_classify_dossier_cache_empty_cache_is_reported_without_a_verdict():
    # The fresh-install shape: nothing cached yet. Must not read as an error --
    # `doctor` is exactly the tool a user runs before ever having scraped anything.
    check = classify_dossier_cache(
        {"total": 0, "unreadable": 0, "empty": 0, "under_200": 0, "under_800": 0})
    assert check.state == NOTICE
    assert "no" in check.detail.lower()


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

    cvc = dataclasses.replace(CvConfig(), renderer="bogus-typo")
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


def test_doctor_reports_a_broken_cv_config_rather_than_tracebacking(monkeypatch):
    """core/app.py's doctor calls load_cv_config() ahead of the deliberately
    guarded constructions below it (self.renderer(), self.store()) -- unlike
    those two, its own call was unguarded before this fix. `load_cv_config`
    already raises ValueError today for several config mistakes unrelated to
    #133/#107 -- cv.baseline_rel, cv.render_script without cv.renderer, a
    non-positive cv.compose_timeout, a retired cv.dossier_dir -- so this is
    witnessed against a REAL raise, not only the cv.name/cv.contact one
    (#133/#107) that originally motivated adding the guard.

    Fix-round finding: the row must reflect the REAL failure, not a hardcoded
    guess. Before this fix a bad compose_timeout was reported as
    component="cv-identity", subject="cv.name" -- literally telling a user
    their candidate's NAME was the problem when their actual mistake was an
    unrelated integer. A message that never mentions "name" pins this: an
    assertion that only checked "some DEAD row exists somewhere" would pass a
    hardcoded-subject implementation exactly as well as a correct one, so this
    checks component/subject/detail directly instead."""
    def _raise(*a, **k):
        raise ValueError("cv.compose_timeout must be a positive integer (seconds), got 0")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _raise)
    rep = Sluice().doctor(offline=True)          # must not raise
    row = _one([c for c in rep.components if c.component == "cv-config"], "cv:")
    assert row.state == DEAD
    assert "compose_timeout" in row.detail
    assert "name" not in row.detail.lower(), (
        "an unrelated cv: error must not be mislabelled as a name problem")
    assert row.blocks == ("cv",)
    assert rep.exit_code() == 1


def test_a_legacy_cv_name_config_error_gets_the_same_treatment(monkeypatch):
    """The scenario that originally motivated this guard -- #133/#107's
    migration raising on a legacy cv.name/cv.contact -- goes through the SAME
    guard as any other load_cv_config ValueError; there is no name-specific
    special case to drift out of sync with the general one above. The message
    below is what `cv/config.py`'s real migration guard actually raises
    (verbatim), not a stand-in -- see test_cv_config.py for that guard's own
    unit tests; this one is only about doctor's generic handling of whatever
    load_cv_config raises."""
    def _raise(*a, **k):
        raise ValueError(
            "cv.name has moved to the vault. sluice now reads your identity "
            "from 'Job Applications/Candidate Profile.md' (frontmatter keys: "
            "forenames, surname, email, mobile, linkedin). Remove cv.name "
            "from the `cv:` block and put the value in that note.")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _raise)
    rep = Sluice().doctor(offline=True)          # must not raise
    row = _one([c for c in rep.components if c.component == "cv-config"], "cv:")
    assert row.state == DEAD
    assert "cv.name has moved to the vault" in row.detail
    assert rep.exit_code() == 1


def test_a_broken_cv_config_does_not_swallow_unrelated_checks(monkeypatch, tmp_path):
    """I2 (fix round 1): the guard must skip only what actually reads cv_cfg --
    cv's own backend targets, the renderer, and cv's row in the gate-posture
    sweep -- not the whole report. The canonical user this matters for is
    exactly who doctor exists to help: someone mid #133/#107 migration whose
    cv: block is broken AND whose Candidate Profile note is the one thing
    they most need doctor to check. An early return (the design this fix
    replaces) hid that row -- and every store/track/camofox/other-sub-app-gate
    row -- behind the unrelated cv: error."""
    from sluice.core.vault import Vault

    (tmp_path / "My CV").mkdir()
    (tmp_path / "My CV" / "CV.md").write_text("# Baseline\n", encoding="utf-8")
    (tmp_path / "Job Applications").mkdir()
    (tmp_path / "Job Applications" / "Judging Profile.md").write_text(
        "criteria\n", encoding="utf-8")
    _seed_candidate_note(tmp_path, {"forenames": "Ada", "email": "ada@example.invalid"})
    vault = Vault(str(tmp_path))
    monkeypatch.setattr(Sluice, "store", lambda self: vault)

    def _raise(*a, **k):
        raise ValueError("cv.compose_timeout must be a positive integer (seconds), got 0")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _raise)

    rep = Sluice().doctor(offline=True)          # must not raise
    store_checks = {c.subject: c for c in rep.components if c.component == "store"}
    assert store_checks["baseline_rel"].state == OK
    assert store_checks["Judging Profile"].state == OK
    assert store_checks["Candidate Profile"].state == OK

    assert [c for c in rep.components if c.component == "track"]
    assert [c for c in rep.components if c.component == "camofox"]
    gate_subjects = [c.subject for c in rep.components if c.component == "gates"]
    assert any(s.startswith("TriageConfig.") for s in gate_subjects), (
        "a sub-app unrelated to cv_cfg must still get its gate row")
    assert not any(s.startswith("CvConfig.") for s in gate_subjects), (
        "there is no cv_cfg to read a CvConfig gate row off"
    )

    # Renderer is one of the three things genuinely skipped when cv_cfg is
    # None -- absent entirely, not present-and-broken.
    assert not [c for c in rep.components if c.component == "renderer"]

    # Triage's and track's backends were still enumerated and checked; only
    # cv's own two specs were omitted from enumerate_targets.
    assert rep.checks
    assert not any("cv" in {u.subapp for u in c.target.uses} for c in rep.checks)


def test_a_real_legacy_cv_config_is_caught_end_to_end_by_doctor(monkeypatch, tmp_path):
    """Every test above this one exercises the cv-config guard through a MOCKED
    `load_cv_config` that raises a synthetic ValueError -- proving doctor's generic
    handling, never that the REAL migration guard in cv/config.py actually reaches
    it. This is the one test that writes a genuine `sluice.yaml` carrying a legacy
    `cv.name`, points `SLUICE_CONFIG` at it, and lets the REAL loader raise -- the
    exact user journey CLAUDE.md documents: `job-sluice doctor` against a legacy
    config must still produce a full report, not one row or a traceback."""
    config_path = tmp_path / "sluice.yaml"
    config_path.write_text('cv:\n  name: "Ada Example"\n', encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(config_path))
    # Undo the autouse fixture's bare-CvConfig stand-in for THIS test only -- it exists
    # so every OTHER test in this file need not care about cv-config health, but here
    # the real loader reading the real file above is the entire point.
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _REAL_LOAD_CV_CONFIG)
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)

    rep = Sluice().doctor(offline=True)          # must not raise
    row = _one([c for c in rep.components if c.component == "cv-config"], "cv:")
    assert row.state == DEAD
    assert "cv.name has moved to the vault" in row.detail
    assert row.blocks == ("cv",)

    # A FULL report, not one row: the guard must not have swallowed everything else.
    # (No real vault at `./vault` here, so the store row is DEAD too -- that is a
    # SEPARATE, expected fact about this bare Sluice(), not evidence the cv: error
    # leaked into it; test_a_broken_cv_config_does_not_swallow_unrelated_checks above
    # already proves the store rows stay healthy when the vault itself is healthy.)
    assert rep.checks, "triage's and track's backends must still be enumerated"
    assert [c for c in rep.components if c.component == "store"]
    assert [c for c in rep.components if c.component == "track"]
    assert [c for c in rep.components if c.component == "camofox"]
    assert rep.exit_code() == 1


def test_a_malformed_cv_block_is_caught_end_to_end_by_doctor(monkeypatch, tmp_path):
    """Sibling to the legacy-key test above, for the OTHER way a `cv:` block goes bad.

    The test above proves doctor survives a `cv:` mapping carrying a retired KEY. This
    one proves it survives a `cv:` that is not a mapping at all -- the shape a wrong
    indent produces, and the one doctor exists to diagnose. Measured before the fix,
    `load_cv_config` raised `AttributeError`/`TypeError` here rather than `ValueError`,
    so doctor's `except ValueError` did not catch it and the command tracebacked on the
    very thing a user runs it to hear about. The guard's own comment in `core/app.py`
    asserted this handler covered a malformed `cv:`; nothing tested that arm, so the
    claim was false and silent -- exactly the "a comment that states a mechanism needs a
    row that falsifies it" class CLAUDE.md names.

    Parametrised over the two ORIGINAL exception classes, not one representative: they
    came from two different lines of the loader (`.items()` versus an `in` membership
    test), so one row cannot witness the other.
    """
    for body in ('cv: "not a mapping"\n', "cv: 5\n"):
        config_path = tmp_path / "sluice.yaml"
        config_path.write_text(body, encoding="utf-8")
        monkeypatch.setenv("SLUICE_CONFIG", str(config_path))
        monkeypatch.setattr("sluice.cv.config.load_cv_config", _REAL_LOAD_CV_CONFIG)
        monkeypatch.setattr(Sluice, "store", _REAL_STORE)

        rep = Sluice().doctor(offline=True)          # must not raise
        row = _one([c for c in rep.components if c.component == "cv-config"], "cv:")
        assert row.state == DEAD, f"{body!r}: expected a DEAD cv-config row"
        assert "must be a mapping" in row.detail, f"{body!r}: {row.detail!r}"
        assert row.blocks == ("cv",)
        # A FULL report, not one row -- the same claim the legacy-key test makes, which
        # is what distinguishes "doctor handled it" from "doctor died politely".
        assert rep.checks, f"{body!r}: triage's and track's backends must still be enumerated"
        assert [c for c in rep.components if c.component == "store"]
        assert [c for c in rep.components if c.component == "camofox"]


def _seed_candidate_note(tmp_path, fields):
    """Write `Job Applications/Candidate Profile.md` directly under `tmp_path`,
    one `key: value` frontmatter line per given field -- the flat shape
    `Vault.read_candidate_profile()`'s `_fm_dict` parses. `fields={}` writes a
    note that EXISTS on disk but declares nothing (a real file, zero fields --
    callers that need this "present but blank" shape assert the file's
    presence themselves, since this helper cannot make that claim on their
    behalf without knowing which of its callers rely on it).

    Its own copy rather than a Vault.write_document call or an import of
    tests/conformance/seeds.py's `_seed_vault(candidate=...)`: that helper
    writes through a Store instance, and some tests below (e.g.
    `test_preflight_reports_the_two_identity_facts`) want to seed the note
    before constructing one."""
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH

    dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(f"---\n{lines}\n---\n")


def test_preflight_reports_the_two_identity_facts(tmp_path):
    from sluice.core.vault import Vault

    _seed_candidate_note(tmp_path, {"forenames": "Ada", "email": "ada@example.invalid"})
    facts = Vault(str(tmp_path)).preflight()
    assert facts["candidate_name_present"] is True
    assert facts["candidate_contact_present"] is True


def test_preflight_reports_absence_for_an_unseeded_vault(tmp_path):
    # The other half of the pair above -- without it, a preflight() that always
    # reported True (e.g. a stray `not not` or a dropped `.strip()`) would pass
    # the declared-facts test just as well as a correct one.
    from sluice.core.vault import Vault

    facts = Vault(str(tmp_path)).preflight()
    assert facts["candidate_name_present"] is False
    assert facts["candidate_contact_present"] is False


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


def test_sluice_doctor_feeds_a_real_preflight_result_into_the_candidate_check(
        monkeypatch, tmp_path):
    """Successor to test_sluice_doctor_wires_the_loaded_cv_config_into_cv_identity,
    whose docstring cites a real prior bug: hardcoding the classifier's inputs left
    the whole suite green while the wiring was broken."""
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH
    from sluice.core.vault import Vault

    _seed_candidate_note(tmp_path, {})           # present but all blank
    # Both this shape and no note at all classify identically as DEAD (an
    # undeclared field abstains rather than being inferred, so a blank
    # candidate note and a missing one carry the same facts) -- the assertion
    # below is what actually distinguishes "present but blank" from "absent"
    # for THIS test, since the DEAD verdict alone cannot tell them apart.
    assert os.path.exists(os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH))
    vault = Vault(str(tmp_path))
    monkeypatch.setattr(Sluice, "store", lambda self: vault)

    rep = Sluice().doctor(offline=True)
    store_checks = [c for c in rep.components if c.component == "store"]
    assert _one(store_checks, "Candidate Profile").state == DEAD

    _seed_candidate_note(tmp_path, {"forenames": "Ada", "email": "ada@example.invalid"})
    rep = Sluice().doctor(offline=True)
    store_checks = [c for c in rep.components if c.component == "store"]
    assert _one(store_checks, "Candidate Profile").state == OK


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


def _write_dossier(dossier_dir, cache_key, *, markdown=None, omit_jd=False):
    """One cached dossier file, the on-disk shape `DossierCache.get_or_build`
    (core/dossier.py) actually writes -- `.json` suffix, `jd.markdown` the field
    the scan reads. `omit_jd=True` writes a dossier with no `jd` key at all (one
    of the three degenerate shapes Task 8's brief names), which a pre-#169 cached
    entry could legitimately have."""
    import json

    os.makedirs(dossier_dir, exist_ok=True)
    body = {"schema_version": 2, "lead_id": cache_key, "built_at": "2026-01-01T00:00:00"}
    if not omit_jd:
        body["jd"] = {"markdown": markdown}
    with open(os.path.join(dossier_dir, f"{cache_key}.json"), "w", encoding="utf-8") as f:
        json.dump(body, f)


def test_sluice_doctor_reports_the_real_dossier_cache_distribution(monkeypatch, tmp_path):
    # Closes the same gap the other "wires a real X" tests close: nothing before this
    # built REAL cached-dossier files on disk and checked the resulting component row.
    dossier_dir = str(tmp_path / "dossiers")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    _write_dossier(dossier_dir, "a", markdown="x" * 1000)   # >= 800: neither bucket
    _write_dossier(dossier_dir, "b", markdown="x" * 500)    # >= 200, < 800
    _write_dossier(dossier_dir, "c", markdown="x" * 50)     # < 200
    _write_dossier(dossier_dir, "d", markdown="")            # empty

    rep = Sluice().doctor(offline=True)
    row = _one([c for c in rep.components if c.component == "dossier-cache"], "cached JDs")
    assert row.state == NOTICE
    assert "4 cached" in row.detail
    assert "1 empty" in row.detail
    assert "2 under 200 chars" in row.detail    # cumulative: the empty one counts too
    assert "3 under 800 chars" in row.detail    # cumulative: under_200 counts too


def test_sluice_doctor_reports_no_dossiers_yet_for_a_fresh_install(monkeypatch, tmp_path):
    # The directory is never created by resolving _dossier_dir() or by scanning it --
    # a fresh install (or any install that has not run triage/cv yet) must be reported
    # honestly as "nothing cached", not crash and not silently create the directory
    # (the #81 shape: a read that creates something disarms a later notice).
    dossier_dir = str(tmp_path / "never-created")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    assert not os.path.exists(dossier_dir)

    rep = Sluice().doctor(offline=True)   # must not raise

    row = _one([c for c in rep.components if c.component == "dossier-cache"], "cached JDs")
    assert row.state == NOTICE
    assert not os.path.exists(dossier_dir), "resolving/scanning must not create the directory"


def test_sluice_doctor_dossier_cache_scan_tolerates_a_stray_file_where_the_dir_should_be(
        monkeypatch, tmp_path):
    # FileNotFoundError's sibling in the same except tuple: a real deployment can have
    # a stray plain FILE sitting at the path the dossier directory is expected (e.g. a
    # leftover from an aborted migration). os.listdir on that path raises
    # NotADirectoryError, not FileNotFoundError -- must be treated the same as "nothing
    # cached", not crash.
    dossier_dir = str(tmp_path / "dossiers")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    with open(dossier_dir, "w", encoding="utf-8") as f:
        f.write("not a directory")

    rep = Sluice().doctor(offline=True)   # must not raise

    row = _one([c for c in rep.components if c.component == "dossier-cache"], "cached JDs")
    assert row.state == NOTICE
    assert "no" in row.detail.lower()


def test_sluice_doctor_dossier_cache_scan_tolerates_corrupt_and_keyless_entries(
        monkeypatch, tmp_path):
    # Two of the three degenerate shapes the brief names (the third, a missing
    # directory, is covered above): a file that is not valid JSON at all, and a file
    # whose JSON is valid but has no `jd` key. Neither may raise out of doctor -- but
    # they are NOT the same fact, and must land in different buckets (the fix-round
    # finding this test now pins): a file that will not parse is a broken CACHE FILE
    # (an interrupted write, a bad disk), reported as "unreadable", never folded into
    # "empty" -- a user reading "2 empty" would conclude their scraper is blocked when
    # their disk is failing. A keyless-but-valid file, by contrast, genuinely carries
    # no JD text -- the same verdict `jd_arrived` (core/dossier.py) already gives a
    # malformed `jd` -- so it stays folded into "empty" alongside a dossier whose fetch
    # produced a real but blank JD (see classify_dossier_cache's docstring for the
    # full reasoning).
    dossier_dir = str(tmp_path / "dossiers")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    os.makedirs(dossier_dir, exist_ok=True)
    with open(os.path.join(dossier_dir, "corrupt.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    _write_dossier(dossier_dir, "keyless", omit_jd=True)

    rep = Sluice().doctor(offline=True)   # must not raise

    row = _one([c for c in rep.components if c.component == "dossier-cache"], "cached JDs")
    assert row.state == NOTICE
    assert "2 cached" in row.detail
    assert "1 unreadable" in row.detail
    assert "1 empty" in row.detail


def test_sluice_doctor_dossier_cache_scan_creates_and_writes_nothing(monkeypatch, tmp_path):
    # Never-clobber, applied to a READ: scanning the cache to report on it must not
    # create the directory, touch an existing entry, or write anything -- the same
    # property test_doctor_store_preflight_writes_nothing pins for the store.
    dossier_dir = str(tmp_path / "dossiers")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    _write_dossier(dossier_dir, "a", markdown="hello")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    before_bytes = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    Sluice().doctor(offline=True)

    after = sorted(str(p) for p in tmp_path.rglob("*"))
    assert before == after, f"the dossier-cache scan wrote something: before={before} after={after}"
    # Names alone do not pin "touch an existing entry": an in-place rewrite leaves the
    # path set identical. Compare CONTENT too, or the docstring above claims more than
    # the assertion checks.
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before_bytes


def test_sluice_doctor_reports_an_unreadable_dossier_dir_instead_of_tracebacking(
        monkeypatch, tmp_path):
    # doctor exists to diagnose a broken install, so it must survive the broken install.
    # os.listdir on a mode-000 directory raises PermissionError, which is not
    # FileNotFoundError or NotADirectoryError; cli.main converts only ValueError, so this
    # escaped as a traceback from the one command meant to explain it.
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    _write_dossier(str(dossier_dir), "a", markdown="hello")
    monkeypatch.setenv("DOSSIER_DIR", str(dossier_dir))
    dossier_dir.chmod(0o000)
    try:
        rep = Sluice().doctor(offline=True)          # must not raise
    finally:
        dossier_dir.chmod(0o755)                     # always restorable for cleanup
    row = _one([c for c in rep.components if c.component == "dossier-cache"], "cached JDs")
    assert row.state == NOTICE


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


def test_the_dossier_buckets_are_cumulative_and_do_not_partition_total(monkeypatch, tmp_path):
    """The bucket arithmetic, pinned because the docstring got it wrong.

    `empty`/`under_200`/`under_800` are CUMULATIVE, and an entry of 800+ characters falls
    in none of them. So the identity is `unreadable + under_800 + (>= 800) == total`, and
    `unreadable + under_800` is strictly LESS than `total` on any install holding one good
    JD. An earlier docstring claimed the buckets summed to `total`, which would make a
    healthy cache look like it had lost entries.
    """
    dossier_dir = str(tmp_path / "dossiers")
    monkeypatch.setenv("DOSSIER_DIR", dossier_dir)
    _write_dossier(dossier_dir, "healthy", markdown="x" * 900)   # in NO length bucket
    _write_dossier(dossier_dir, "shortish", markdown="y" * 300)  # under_800 only
    _write_dossier(dossier_dir, "tiny", markdown="z" * 50)       # under_800 + under_200
    _write_dossier(dossier_dir, "blank", markdown="")            # all three
    with open(os.path.join(dossier_dir, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")

    rep = Sluice().doctor(offline=True)
    detail = _one([c for c in rep.components if c.component == "dossier-cache"],
                  "cached JDs").detail

    assert "5 cached" in detail
    assert "1 unreadable" in detail
    assert "1 empty" in detail
    assert "2 under 200 chars" in detail    # blank + tiny -- cumulative, not disjoint
    assert "3 under 800 chars" in detail    # blank + tiny + shortish
    # The identity the docstring now states, and the one it used to state.
    assert 1 + 3 < 5, "unreadable + under_800 must be strictly less than total here"


def test_preflight_reports_pending_and_verified_counts_without_duplicating_the_existing_keys(tmp_path):
    """`experience_total`/`experience_verified` already exist and are consumed at
    core/doctor.py's classify_store (the Experience Library row). Adding parallel
    keys for the same two facts would leave two sources for one fact -- the exact
    drift shape this codebase removes on sight. `skills`/`stories` get the new
    `<kind>_pending` key too, and (being new kinds) their own `_total`/`_verified`.

    The corpus is MIXED on purpose (#164 review, H6). This row used to assert
    `skills_verified == 0` in a state where `skills_total` was also 0, so the two
    numbers were indistinguishable and `sum(1 for e in every if e.get("verified"))`
    mutated to `len(every)` survived -- meaning `doctor` could have printed
    "3 verified / 3 total" over a corpus with ONE citable entry, the reassuring
    direction to be wrong in, on the number a user checks before composing. Two
    verified entries and one unverified make `total` and `verified` differ, so only a
    real per-entry count satisfies both.
    """
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path))
    base = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory")
    os.makedirs(base)
    for name in ("citable-one", "citable-two"):
        with open(os.path.join(base, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nProficiency: P\nverified: 2026-01-01\n---\nReviewed.\n")
    with open(os.path.join(base, "hand-written.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: Q\n---\nNever run through verify.\n")
    v.propose_evidence("skills", name="alpha", fields={})

    facts = v.preflight()
    assert facts["skills_pending"] == 1
    assert facts["skills_total"] == 3, "the unverified entry is not counted in the total"
    assert facts["skills_verified"] == 2, "the count is not per-entry"
    assert facts["experience_pending"] == 0
    assert facts["experience_total"] == 0 and facts["experience_verified"] == 0
    assert "experience_entries" not in facts, "a duplicate of the existing key"


def test_doctor_prints_the_two_evidence_counts_it_was_given_rather_than_one_twice(tmp_path):
    """The classify_store half of the row above (#164 review, H6). A preflight that
    counts correctly buys nothing if the message that renders it reads the wrong key,
    and "N verified / N total" over a corpus with one citable entry is a fabrication-
    gate reassurance a user acts on. Seeded so the two numbers differ, and asserted on
    the rendered string, which is what a user actually reads.
    """
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path))
    base = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory")
    os.makedirs(base)
    for name in ("citable-one", "citable-two"):
        with open(os.path.join(base, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nProficiency: P\nverified: 2026-01-01\n---\nReviewed.\n")
    with open(os.path.join(base, "hand-written.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: Q\n---\nNever run through verify.\n")

    [row] = [r for r in classify_store(v.preflight()) if r.subject == "Skills Inventory"]
    assert "2 verified / 3 total" in row.detail


def test_doctor_claims_citability_only_for_the_corpus_the_gate_actually_reads(tmp_path):
    """#164 review, M2. Every kind's row said "only verified entries are citable by the
    CV fabrication gate", while `cv/engine.py` reads `experience` alone -- so a user was
    told their Skills Inventory was citable when nothing licensed it.
    Wrong in the reassuring direction, which is the direction people stop looking in.

    Derived from the registry flags rather than asserting on kind names, so a flag change
    carries this row with it instead of failing it. All THREE branches are asserted,
    because a message that claimed citability for NOTHING would satisfy the negative half
    on its own.

    Three branches since #165, not two: `skills` is now READ by the composer as framing
    while remaining uncitable, so both "citable" and "nothing reads this corpus" are false
    for it. That middle state is the whole point of splitting the flag -- collapsing it
    back into either neighbour re-creates the #164 M2 over-claim in one direction or a
    plain falsehood in the other.
    """
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    rows = {r.subject: r.detail for r in classify_store(Vault(str(tmp_path)).preflight())}
    assert any(s.cited_by_gate for s in EVIDENCE_KINDS.values()), \
        "no kind is flagged cited_by_gate -- the citable half is vacuous"
    assert any(s.read_by_composer and not s.cited_by_gate
               for s in EVIDENCE_KINDS.values()), \
        "no kind is framing-only -- the middle branch below is unexercised"
    assert any(not s.read_by_composer for s in EVIDENCE_KINDS.values()), \
        "every kind is read -- the unread half is vacuous"
    for kind, spec in EVIDENCE_KINDS.items():
        detail = rows[spec.relpath.rsplit("/", 1)[-1]]
        if spec.cited_by_gate:
            assert "are citable by the CV fabrication gate" in detail, kind
        elif spec.read_by_composer:
            assert "shown to the CV composer as framing" in detail, kind
            assert "are citable by the CV fabrication gate" not in detail, kind
        else:
            assert "nothing reads this corpus yet" in detail, kind
            assert "are citable by the CV fabrication gate" not in detail, kind


def test_doctor_reports_a_notice_naming_the_command_that_makes_entries_citable(tmp_path):
    """A pending count with nowhere pointing at it is noise, not a notice -- the
    whole reason this row exists is to name the command that resolves the silent-
    inert state a propose-only write introduces (see core/doctor.py's docstring)."""
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={})
    rows = classify_store(v.preflight())
    pending = [r for r in rows if "skills" in r.subject.lower()]
    assert pending, "no row for the skills store"
    assert any("verify" in r.detail for r in pending), \
        "the notice does not name the action that resolves it"


def _symlink_a_kind_out_of_the_vault(tmp_path, kind):
    """Point one evidence kind's directory at a real directory OUTSIDE the vault.

    `Vault._evidence_dir` refuses to read or write through it, correctly -- the rows
    below are about `preflight` and `classify_store` surviving that refusal per kind, never
    about softening it. The relpath comes from the registry, so a renamed or fourth kind
    cannot leave this fixture building a directory nothing resolves to.
    """
    from sluice.core.protocols import EVIDENCE_KINDS

    outside = tmp_path / "outside-the-vault"
    outside.mkdir()
    link = (tmp_path / "vault").joinpath(*EVIDENCE_KINDS[kind].relpath.split("/"))
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(outside), str(link))


def test_one_unreadable_evidence_kind_does_not_erase_every_other_store_fact(tmp_path):
    """Round-2 review, H2. `Vault.preflight`'s per-kind loop had no isolation, so the
    OSError `_evidence_dir` correctly raises for a symlinked evidence directory unwound
    past the loop and out of `preflight` entirely -- and `Sluice.doctor`'s catch-all then
    emitted a lone `store | preflight | DEAD` row.

    Measured with `STAR Stories` symlinked. `_evidence_dir` arrived with #164, so before it
    the symlink is a stray directory nothing reads and `doctor` prints four store rows,
    including `Candidate Profile | dead | blocks: cv`; this branch printed one. So a user
    whose `cv run` says `skipped-config` runs `doctor` to find out why is told only about a
    corpus nothing reads -- on the single command that exists to diagnose exactly this.

    Asserted on the FACTS, at the `preflight` layer: the broken kind reports `<kind>_error`
    and no count triple (a `0` would read as "the corpus is empty", which is the quiet
    wrong default this codebase engineers out), and every other kind still reports its own.
    """
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    _symlink_a_kind_out_of_the_vault(tmp_path, "stories")
    v = Vault(str(tmp_path / "vault"))
    v.propose_evidence("skills", name="alpha", fields={})

    facts = v.preflight()
    assert "is a symlink" in facts["stories_error"]
    for key in ("stories_total", "stories_verified", "stories_pending"):
        assert key not in facts, f"{key} would read as an empty corpus, not an unreadable one"
    # Every OTHER kind still answers -- the isolation is per kind, not "give up quietly".
    for kind in EVIDENCE_KINDS:
        if kind == "stories":
            continue
        assert f"{kind}_error" not in facts
        assert facts[f"{kind}_total"] == 0
    assert facts["skills_pending"] == 1, "a readable kind lost its count to its neighbour"
    # The facts a `cv run` refusal is diagnosed from are still there at all.
    assert facts["vault_exists"] is True
    assert "candidate_name_present" in facts and "baseline_exists" in facts


def test_an_unreadable_evidence_corpus_takes_its_own_row_and_leaves_the_others_standing(
        tmp_path):
    """The `classify_store` half of the row above: facts in `preflight`, classification
    here, the split `core/doctor.py`'s own docstring already states.

    DEAD rather than NOTICE, because NOTICE never reaches the exit code (see
    `DoctorReport.exit_code`) and a directory the store cannot read at all is a fault the
    user must act on, not a posture. `blocks` is asserted on the UNCITED kind here:
    an unreadable `skills` corpus DEGRADES rather than blocking `cv` (#165: cv/engine.py
    catches it and composes without the framing) and nothing reads `stories` at all, so
    naming a sub-app for either would be the
    over-claim `EvidenceKind.cited_by_gate` exists to prevent.
    """
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    _symlink_a_kind_out_of_the_vault(tmp_path, "stories")
    rows = classify_store(Vault(str(tmp_path / "vault")).preflight())
    by_subject = {r.subject: r for r in rows}

    broken = by_subject[EVIDENCE_KINDS["stories"].relpath.rsplit("/", 1)[-1]]
    assert broken.state == DEAD
    assert "cannot be read" in broken.detail and "is a symlink" in broken.detail
    assert broken.blocks == (), "nothing reads stories, so it blocks no sub-app"
    # Every other store row survives -- the whole point of the isolation.
    for subject in ("baseline_rel", "Judging Profile", "Candidate Profile",
                    EVIDENCE_KINDS["experience"].relpath.rsplit("/", 1)[-1],
                    EVIDENCE_KINDS["skills"].relpath.rsplit("/", 1)[-1]):
        assert subject in by_subject, f"{subject} was erased by an unrelated corpus"
    assert by_subject["Candidate Profile"].state == DEAD, \
        "the row a `cv run` skipped-config refusal is diagnosed from"


def test_an_unreadable_cited_corpus_names_cv_as_what_it_blocks(tmp_path):
    """The other half of the `blocks` split above, so a fix hardcoding `()` or `("cv",)`
    for every kind cannot satisfy both rows. Keyed on `cited_by_gate`, so a flag
    flip carries these rather than failing them."""
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    cited = [k for k, s in EVIDENCE_KINDS.items() if s.cited_by_gate]
    assert cited, "no kind is flagged cited_by_gate -- this row would be vacuous"
    kind = cited[0]
    _symlink_a_kind_out_of_the_vault(tmp_path, kind)
    v = Vault(str(tmp_path / "vault"))
    # The measurement behind the claim: the gate's own reader RAISES rather than
    # returning [], so `cv/engine.py` cannot build a bundle at all.
    with pytest.raises(OSError, match="is a symlink"):
        v.read_evidence("experience", verified_only=True)

    row = _one(classify_store(v.preflight()),
               EVIDENCE_KINDS[kind].relpath.rsplit("/", 1)[-1])
    assert row.state == DEAD
    assert row.blocks == ("cv",)


def test_doctor_reports_an_unreadable_corpus_through_the_real_wiring(tmp_path, monkeypatch):
    """End to end through `Sluice.doctor`, not just the two pure halves: the lone
    `store | preflight | DEAD` row this replaces came from `Sluice.doctor`'s catch-all
    around the hook, so the row proving that catch-all is no longer reached has to run
    that wiring.
    """
    from sluice.core.config import Config
    from sluice.core.protocols import EVIDENCE_KINDS

    _symlink_a_kind_out_of_the_vault(tmp_path, "stories")
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    # Restore the REAL store resolution: this file's autouse `_harmless_components`
    # fixture hands `Sluice.doctor` a bare sentinel with no `preflight` at all, which is
    # the documented "cannot say" shape -- so without this the row below would assert over
    # an empty component list and pass against any implementation whatsoever.
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)
    report = Sluice(Config(vault_dir=str(tmp_path / "vault"))).doctor(offline=True)
    store_rows = [c for c in report.components if c.component == "store"]

    assert [r for r in store_rows if r.subject == "preflight"] == [], \
        "the whole-method catch-all fired; per-kind isolation did not"
    subjects = {r.subject for r in store_rows}
    assert {spec.relpath.rsplit("/", 1)[-1] for spec in EVIDENCE_KINDS.values()} <= subjects
    assert "Candidate Profile" in subjects and "baseline_rel" in subjects


def _mis_encode_an_evidence_entry(tmp_path, kind):
    """Write one entry file under `kind` whose bytes are NOT valid UTF-8.

    The other exception hierarchy `preflight`'s per-kind guard has to survive, and the
    reason it is a separate fixture from `_symlink_a_kind_out_of_the_vault` rather than a
    parameter on it: `Vault._evidence_entries` reads every entry through `_read`, which
    opens with `encoding="utf-8"` and no `errors=`, so a hand-edited or sync-mangled entry
    raises UnicodeDecodeError -- a ValueError SUBCLASS, not an OSError. A guard catching
    only OSError is green under every symlink row above and still fails here.

    Real bytes on disk, never a monkeypatched `_read`: a patched reader would prove only
    that the guard catches whatever the test chose to raise at it. Relpath from the
    registry, so a renamed or fourth kind cannot leave this fixture writing somewhere
    nothing reads.
    """
    from sluice.core.protocols import EVIDENCE_KINDS

    entry = (tmp_path / "vault").joinpath(
        *EVIDENCE_KINDS[kind].relpath.split("/")) / "alpha.md"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"---\nCompany: \xff\xfe\n---\nbody\n")
    return entry


def test_a_mis_encoded_evidence_entry_is_isolated_per_kind_like_an_unreadable_directory(
        tmp_path):
    """`preflight`'s per-kind guard shipped as `except OSError` alone, under a comment
    asserting the only ValueError its two callees raise is `_kind`'s unknown-kind guard
    (which indeed cannot fire here). That was false: `_read`'s `encoding="utf-8"` makes a
    mis-encoded entry file raise UnicodeDecodeError, a ValueError subclass.

    Measured on the narrow spelling with this exact fixture: the exception unwound past the
    loop and out of `preflight` entirely -- the failure mode the paragraph beside that guard
    records for the symlinked case, and the one the guard was added to stop. So this row is
    the SECOND hierarchy, asserted the same way its symlinked sibling is: the broken kind
    reports `<kind>_error` and no count triple, and every other kind still answers.
    """
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    _mis_encode_an_evidence_entry(tmp_path, "experience")
    v = Vault(str(tmp_path / "vault"))
    v.propose_evidence("skills", name="alpha", fields={})

    facts = v.preflight()
    # The decoder's own words, so a DIFFERENT failure reaching the same arm cannot satisfy
    # this row -- the `is a symlink` assertion's counterpart, one hierarchy over.
    assert "utf-8" in facts["experience_error"] and "decode" in facts["experience_error"]
    for key in ("experience_total", "experience_verified", "experience_pending"):
        assert key not in facts, f"{key} would read as an empty corpus, not an unreadable one"
    for kind in EVIDENCE_KINDS:
        if kind == "experience":
            continue
        assert f"{kind}_error" not in facts
    assert facts["skills_pending"] == 1, "a readable kind lost its count to its neighbour"
    assert facts["vault_exists"] is True
    assert "candidate_name_present" in facts and "baseline_exists" in facts


def test_a_mis_encoded_evidence_entry_takes_its_own_dead_row(tmp_path):
    """The `classify_store` half, so the fact this guard now records is asserted to reach a
    user as a row rather than only as a dict key -- the same split the symlinked pair above
    uses, and the same DEAD state for the same reason (a NOTICE never reaches the exit
    code)."""
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    _mis_encode_an_evidence_entry(tmp_path, "experience")
    rows = classify_store(Vault(str(tmp_path / "vault")).preflight())
    row = _one(rows, EVIDENCE_KINDS["experience"].relpath.rsplit("/", 1)[-1])
    assert row.state == DEAD
    assert "cannot be read" in row.detail and "decode" in row.detail
    # Every other store row survives -- the isolation is per kind, not "give up quietly".
    subjects = {r.subject for r in rows}
    assert {"Candidate Profile", "baseline_rel", "Judging Profile"} <= subjects


def test_the_missing_token_row_names_where_the_token_must_go():
    """A diagnostic that says a file is missing without saying WHERE is not actionable.

    `track.token_path` resolves through a config key and then an XDG root, so the location is not
    something a reader can infer -- and this row exists to tell them what to do next. The path
    reaches the classifier from `Sluice.doctor`, which was already computing `os.path.exists()`
    on it and discarding the value.

    The fallback arm is pinned too: the parameter is defaulted so direct constructions keep
    working, and a default that silently produced an empty `at ` would read as a bug.
    """
    c = classify_track_google(available=True, import_error=None, token_present=False,
                              token_path="/state/sluice/google_token.json")
    assert c.state == DEGRADED
    assert "/state/sluice/google_token.json" in c.detail

    bare = classify_track_google(available=True, import_error=None, token_present=False)
    assert "track.token_path" in bare.detail, bare.detail
    assert "at  " not in bare.detail, f"empty path left a dangling 'at': {bare.detail}"


def test_a_present_token_says_nothing_about_the_path():
    """Anti-over-reach: the OK arm must not start leaking a filesystem path into normal output."""
    c = classify_track_google(available=True, import_error=None, token_present=True,
                              token_path="/state/sluice/google_token.json")
    assert c.state == OK
    assert "/state/sluice" not in c.detail


def test_doctor_passes_the_RESOLVED_token_path_through_to_the_google_row(monkeypatch, tmp_path):
    """The CALL SITE, not the helper -- which is the whole point of this test.

    A first version of this pinned `classify_track_google` directly, and measured, that left the
    caller free to stop passing the path entirely: deleting `token_path=track_cfg.token_path`
    from `Sluice.doctor` kept every assertion green while the row silently reverted to naming the
    config key instead of a location. That is this repo's recorded "testing the helper reproduces
    the defect one level up" failure (#170), so the check has to run the real wiring.

    `SLUICE_STATE`-rooted rather than a literal: the path must be the one the config RESOLVED, so
    a test naming its own string would pass against a caller that hardcoded anything at all.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr("sluice.track.google_client.probe_availability", lambda: (True, None))

    rep = Sluice().doctor(offline=True, probe=lambda b: None)
    google = [c for c in rep.components if c.subject == "google_token.json"]
    assert google, f"no missing-token row: {[c.subject for c in rep.components]}"
    assert str(tmp_path) in google[0].detail, (
        f"the row does not name the resolved token path: {google[0].detail}")


# ── #165: a configured negative that contradicts the verified Skills Inventory ──
def test_a_negative_naming_a_held_skill_is_reported():
    rows = classify_negatives_vs_skills(["never claim documenting experience"],
                                        [{"best_for": "documentation"}])
    assert len(rows) == 1 and rows[0].state == NOTICE


def test_the_report_names_no_configured_value():
    """A DoctorReport is returned whole to MCP clients (sluice/mcpserver.py), and
    `classify_gate` reports this SAME config key as a COUNT for that reason. Echoing the
    user's own preference prose into a diagnostic makes it a disclosure surface, so the
    row must LOCATE the line, never quote it."""
    neg = "never claim documenting experience"
    rows = classify_negatives_vs_skills([neg], [{"best_for": "documentation"}])
    assert neg not in rows[0].detail
    assert "documenting" not in rows[0].detail
    # The STEMS too, not just the words as the user typed them: the leak this guards
    # against is `{sorted(overlap)}` in place of `{len(overlap)}`, which would print
    # ['document'] -- a form neither the raw line nor the raw word check can see.
    assert "document" not in rows[0].detail
    assert "[" not in rows[0].detail, "the row renders a term list rather than a count"
    assert rows[0].subject == "cv.negatives[0]"


def test_an_empty_inventory_abstains():
    """Empty-config-abstains: an install with no Skills Inventory has nothing to
    contradict, and must not have every negative reported."""
    assert classify_negatives_vs_skills(["never claim anything"], []) == []


def test_an_empty_negatives_list_abstains():
    assert classify_negatives_vs_skills([], [{"best_for": "documentation"}]) == []


def test_an_inventory_with_no_domains_abstains():
    """A skill whose Domain is blank contributes no terms, so there is nothing to
    contradict. This is genuinely an equivalent mutant of the `not skills` guard above --
    deleting either one leaves this green -- and it is kept because the two states are
    different for a READER: "you have no inventory" and "your inventory declares no
    domains" are different things to be told, and a later change that makes the second
    report something would land here."""
    assert classify_negatives_vs_skills(["never claim anything"], [{"best_for": ""}]) == []


def test_a_stopword_in_a_domain_does_not_manufacture_a_contradiction():
    """Measured before the length floor existed: a Domain reading "Data and analytics for
    the platform" contributes the stem `the`, so EVERY negative containing the word "the"
    reported a contradiction. NOTICE-tier, so it cost no lead -- but a row that fires on
    everything is one a user learns to ignore, which is the whole value of the check."""
    assert classify_negatives_vs_skills(
        ["no mention of the finance sector"],
        [{"best_for": "Data and analytics for the platform"}]) == []
    # ...while a real overlap in the same inventory still reports.
    assert classify_negatives_vs_skills(
        ["never claim analytics work"],
        [{"best_for": "Data and analytics for the platform"}])


def test_a_negative_about_something_not_in_the_inventory_is_not_reported():
    assert classify_negatives_vs_skills(["never claim a security clearance"],
                                        [{"best_for": "documentation"}]) == []


def test_the_match_survives_a_word_form_difference():
    """Why this shares the stemmer: a negative saying 'documenting' and a skill whose
    Domain says 'documentation' are the same disagreement."""
    assert classify_negatives_vs_skills(["no documenting"], [{"best_for": "documentation"}])


def test_the_entry_title_is_not_a_matchable_term():
    """The title is a NAME the user chose, so matching its stems makes any negative
    containing an ordinary word like 'skills' fire a NOTICE about nothing. A false
    contradiction report is worse than a missed one here: the whole value of the row is
    that it means something."""
    assert classify_negatives_vs_skills(
        ["never claim these skills"],
        [{"best_for": "platform", "title": "Example Cloud Skill"}]) == []


def test_the_row_never_affects_the_exit_code():
    """NOTICE, never DEGRADED. `--strict` in a cron job failing because a negative
    overlaps an inventory is the 672ad2a class aimed at the tool's own exit status."""
    rows = classify_negatives_vs_skills(["no documenting"], [{"best_for": "documentation"}])
    assert DoctorReport(checks=[], components=rows).exit_code(strict=True) == 0


def test_the_negatives_cross_check_runs_through_the_real_wiring(tmp_path, monkeypatch):
    """Every other test of this check calls the pure classifier directly, so the smallest
    DELETION in production code -- removing the call site in `Sluice.doctor` -- leaves them
    all green. This is the one that reddens.

    Follows `test_doctor_reports_an_unreadable_corpus_through_the_real_wiring`'s idiom,
    including restoring the REAL store: this file's autouse `_harmless_components` fixture
    hands `Sluice.doctor` a sentinel with no evidence reads at all.
    """
    import os

    from sluice.core.config import Config

    vault = tmp_path / "vault"
    sk = vault / "Job Applications" / "Skills Inventory"
    os.makedirs(sk, exist_ok=True)
    (sk / "Example Cloud Skill.md").write_text(
        "---\nProficiency: 8 years\nDomain: documentation\nEvidence: e\n"
        "Signal Value: depth\nverified: 2026-08-25\n---\nBody.\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)
    monkeypatch.setattr(
        "sluice.cv.config.load_cv_config",
        lambda *a, **k: __import__("sluice.cv.config", fromlist=["CvConfig"]).CvConfig(
            negatives=["never claim documenting experience"]))

    report = Sluice(Config(vault_dir=str(vault))).doctor(offline=True)
    rows = [c for c in report.components if c.subject.startswith("cv.negatives[")]
    assert rows, ("Sluice.doctor did not run the negatives cross-check -- the pure "
                  "classifier's own tests cannot see this")
    assert rows[0].state == NOTICE
    # Exit-code neutrality is asserted on the rows themselves in
    # test_the_row_never_affects_the_exit_code. Asserting it on THIS report would conflate
    # the NOTICE with the genuinely DEAD rows a bare tmp vault produces (no baseline CV, no
    # Candidate Profile), which is a different claim and one that would fail for the right
    # reasons.


# ── #168 Task 10: an experience entry's `Skills:` claims vs the Skills Inventory ──
#
# Entries below are built as literal nested dicts -- `fields` holding a `Skills` entry
# written out in full at each call site -- matching tests/test_cv_bundle.py's and
# tests/test_cv_engine.py's own precedent for this exact shape, rather than through a
# value-taking helper function. A helper taking the skill
# string as a PARAMETER (an earlier version of this file had one, `_exp(skills)`, built
# via `entry["fields"]["Skills"] = skills`) puts the actual value only at the CALL SITE,
# with no "Skills:" text anywhere near it in the source -- invisible to BOTH neutrality
# collectors in tests/test_fixture_name_neutrality.py: the `Skills:`-keyed one (which
# needs the literal key text immediately before the value) and the `Example <Word>`
# identity sweep (which would otherwise catch it independent of any key at all, the way
# #167 needed it to for body prose). Measured directly: with the helper shape,
# `_all_fixture_skill_values()` and `_cv_fixture_identities()` both missed
# "ExampleZephyrOnly" entirely -- a collector matching SYNTAX, not semantics, exactly
# the "pattern consumed by two engines" class CLAUDE.md names. A literal dict avoids the
# indirection instead of teaching either sweep a new shape to look for.
def _skill(title: str) -> dict:
    """A minimal Skills Inventory entry dict -- only `title` matters."""
    return {"title": title}


def test_a_claimed_skill_matching_the_inventory_by_slug_reports_nothing():
    """The POSITIVE control every negative test below leans on: a real matching pair,
    proving the row CAN fire before any test asserts it does not. `title` is the
    STORED FILENAME (`evidence_slug(name)`, lowercase-dashed), never the raw typed
    text -- "Example Widget" reduces to "example-widget"."""
    assert classify_skills_reconciliation(
        [{"fields": {"Skills": "Example Widget"}}], [_skill("example-widget")]) == []


def test_an_inventory_skill_named_by_no_entry_is_reported():
    rows = classify_skills_reconciliation([], [_skill("example-widget")])
    assert len(rows) == 1
    assert rows[0].state == NOTICE
    assert rows[0].subject == "Skills Inventory (unclaimed)"
    assert "1 inventory skill" in rows[0].detail
    assert "job-sluice experience list" in rows[0].detail


def test_an_entry_skill_absent_from_the_inventory_is_reported():
    rows = classify_skills_reconciliation([{"fields": {"Skills": "Example Ghost"}}], [])
    assert len(rows) == 1
    assert rows[0].state == NOTICE
    assert rows[0].subject == "Experience Library (unmatched)"
    assert "1 entry Skills:" in rows[0].detail
    assert "job-sluice skills list" in rows[0].detail


def test_both_rows_can_fire_together_on_a_wholly_disjoint_pair():
    """Distinct MUTANT-killing shape from the two single-row tests above: a defect that
    always returns at most one row (e.g. an early `return` after the first check) is
    invisible to either of them alone but caught here."""
    rows = classify_skills_reconciliation(
        [{"fields": {"Skills": "Example Ghost"}}], [_skill("example-orphan")])
    assert len(rows) == 2
    subjects = {r.subject for r in rows}
    assert subjects == {"Skills Inventory (unclaimed)", "Experience Library (unmatched)"}


def test_no_experience_and_no_inventory_abstains():
    assert classify_skills_reconciliation([], []) == []


def test_a_blank_skills_value_never_counts_as_a_claim():
    """SC5 (cv/bundle.py:_skill_items): blank is absent. A blank `Skills:` value must
    contribute NOTHING to `claimed` -- proven two ways in one test. Against an empty
    inventory, no row fires at all (a phantom "" name would otherwise be reported as
    an unmatched claim, which this first assertion catches). Against a non-empty
    inventory, only the orphan row fires -- a phantom "" claim would otherwise also
    SATISFY the orphan check by coincidence for any title that happens to be falsy,
    so the second assertion pins the count explicitly rather than merely checking
    the row is present."""
    assert classify_skills_reconciliation([{"fields": {"Skills": ""}}], []) == []
    rows = classify_skills_reconciliation(
        [{"fields": {"Skills": ""}}], [_skill("example-widget")])
    assert len(rows) == 1
    assert rows[0].subject == "Skills Inventory (unclaimed)"


def test_a_missing_skills_key_never_counts_as_a_claim():
    """`.get("Skills", "")` defaults an ABSENT key the same way a blank one reads --
    the shape every pre-#168 Experience Library note actually has (no `Skills:` line at
    all, per tests/test_evidence_kinds.py's own `gamma` fixture), as opposed to the
    test above's explicitly-blank shape."""
    assert classify_skills_reconciliation([{"fields": {}}], []) == []


def test_a_missing_fields_key_never_counts_as_a_claim():
    """`(e.get("fields") or {})` -- an entry with no `fields` key at all must abstain
    the same way, not raise. Not a shape `Vault._evidence_entries` ever produces (every
    real entry dict carries `fields`), but the Store contract does not require it and
    doctor never refuses on an unusual-but-harmless shape."""
    assert classify_skills_reconciliation([{}], []) == []


def test_a_none_skills_value_does_not_raise():
    """`fields.get("Skills", "")` only supplies the DEFAULT when the key is ABSENT --
    an explicit None VALUE (a Store returning a Skills key set to Python's null rather
    than omitting the key) passes straight through to `.split`, which raises
    `AttributeError` on None. Not reachable via the real `Vault` today
    (`_parse_fm_spaced`/`_fm_dict` always yield `str`), but `core/protocols.py`'s Store
    contract does not forbid it, and "doctor never refuses" -- this module's own house
    rule -- means a malformed but plausible Store return must not crash the whole
    report over one bad field.

    Built via subscript assignment, unlike every other entry in this file -- None is
    not a candidate skill NAME (there is nothing here for a human to confirm invented
    vs. real), so this one line is the sole deliberate exception to this file's own
    "always a literal dict" rule stated above: a subscript assignment keeps the bare
    word None off the `Skills:`-collector's literal-adjacency match, which is exactly
    right here since there is no name being hidden from review."""
    entry = {"fields": {}}
    entry["fields"]["Skills"] = None
    assert classify_skills_reconciliation([entry], []) == []


def test_duplicate_claims_across_entries_count_once():
    """`claimed` is built as a SET across every experience entry -- two entries both
    naming "Example Ghost" (absent from the inventory) must report ONE unmatched name,
    not two. A defect that counts occurrences instead of distinct names would report a
    count of 2 in that row's detail text, which this test's exact assertion catches."""
    rows = classify_skills_reconciliation(
        [{"fields": {"Skills": "Example Ghost"}},
         {"fields": {"Skills": "Example Ghost, Example Widget"}}],
        [_skill("example-widget")])
    assert len(rows) == 1
    assert "1 entry Skills:" in rows[0].detail


def test_a_name_that_cannot_reduce_falls_back_to_verbatim_and_can_still_match():
    """An all-punctuation `Skills:` value makes `evidence_slug` raise -- `_keys` must
    fall back to the verbatim string alone rather than propagating the exception
    (doctor never refuses), and a hand-placed inventory entry whose own title was
    never reduced (evidence_slug is CREATE-time only, per its own docstring) can still
    match it verbatim."""
    assert classify_skills_reconciliation(
        [{"fields": {"Skills": "###"}}], [_skill("###")]) == []


def test_a_name_that_cannot_reduce_and_does_not_match_verbatim_is_still_reported():
    rows = classify_skills_reconciliation(
        [{"fields": {"Skills": "###"}}], [_skill("something-else")])
    subjects = {r.subject for r in rows}
    assert "Experience Library (unmatched)" in subjects
    assert "Skills Inventory (unclaimed)" in subjects


def test_the_report_names_no_skill_string():
    """Same discipline as test_the_report_names_no_configured_value above: a
    DoctorReport reaches MCP clients whole, and this module's own "no doctor row
    carries user-authored text" rule means neither the raw typed name nor its reduced
    slug form may appear in either row's detail or subject."""
    rows = classify_skills_reconciliation(
        [{"fields": {"Skills": "Example Zephyr"}}], [_skill("example-orphan-only")])
    assert len(rows) == 2
    for r in rows:
        assert "Example Zephyr" not in r.detail
        assert "example-zephyr" not in r.detail.lower()
        assert "example-orphan-only" not in r.detail
        assert "example-zephyr" not in r.subject.lower()
        assert "example-orphan-only" not in r.subject.lower()


def test_the_rows_never_affect_the_exit_code():
    """NOTICE, never DEGRADED -- same posture classify_negatives_vs_skills's identical
    test pins."""
    rows = classify_skills_reconciliation([{"fields": {"Skills": "Example Ghost"}}], [])
    assert DoctorReport(checks=[], components=rows).exit_code(strict=True) == 0


def test_the_skills_reconciliation_runs_through_the_real_wiring(tmp_path, monkeypatch):
    """Every other test of this check calls the pure classifier directly, so the
    smallest DELETION in production code -- removing the call site in `Sluice.doctor`
    -- leaves them all green. This is the one that reddens. Follows
    test_the_negatives_cross_check_runs_through_the_real_wiring's idiom exactly,
    including restoring the REAL store the file's autouse `_harmless_components`
    fixture replaces with a bare sentinel.
    """
    import os

    from sluice.core.config import Config

    vault = tmp_path / "vault"
    exp = vault / "Job Applications" / "Experience Library"
    sk = vault / "Job Applications" / "Skills Inventory"
    os.makedirs(exp, exist_ok=True)
    os.makedirs(sk, exist_ok=True)
    (exp / "alpha.md").write_text(
        "---\nCompany: Example Alpha\nCategory: \nBest For: \nMetrics: \n"
        "Skills: Example Ghost\nverified: 2026-08-25\n---\nBody.\n", encoding="utf-8")
    (sk / "Example Orphan.md").write_text(
        "---\nProficiency: 8 years\nDomain: platform\nEvidence: e\n"
        "Signal Value: depth\nverified: 2026-08-25\n---\nBody.\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    monkeypatch.setattr(Sluice, "store", _REAL_STORE)

    report = Sluice(Config(vault_dir=str(vault))).doctor(offline=True)
    # Filtered on the `gates` component too, not subject alone: `classify_store`
    # ABOVE this call in `Sluice.doctor` already emits its own "store"-component
    # rows at the bare "Skills Inventory"/"Experience Library" subjects (the
    # per-kind total/verified/pending counts), so a subject-only filter is
    # satisfied by THOSE rows regardless of whether this reconciliation ran at
    # all -- measured: deleting the call site under test left this assertion green
    # until this component filter was added.
    gate_subjects = {c.subject for c in report.components if c.component == "gates"}
    expected = {"Skills Inventory (unclaimed)", "Experience Library (unmatched)"}
    assert expected <= gate_subjects, (
        "Sluice.doctor did not run the skills reconciliation -- the pure classifier's "
        f"own tests cannot see this (gates subjects: {sorted(gate_subjects)})")
