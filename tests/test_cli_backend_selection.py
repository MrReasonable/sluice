"""Config-driven backend selection at the CLI build sites (SP2 PR-B).

`--backend` names a ROLE (auto/primary/fallback) and the config decides which
provider fills it, so these tests pin the resolution rather than any one vendor.
"""
import pytest

from sluice.cli import _build_backend, _build_compose_backend, _track_backend
from sluice.core.backends import BackendError
from sluice.cv.config import CvConfig
from sluice.track.config import TrackConfig
from sluice.triage.config import TriageConfig


@pytest.fixture
def key(monkeypatch):
    """The normal deployed state: the per-token fallback has its credentials."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# Every build site takes the same (config, choice) shape, so drive them as data:
# a regression that reaches only one of the three is the failure mode here.
SITES = [
    ("triage", _build_backend, TriageConfig),
    ("cv", _build_compose_backend, CvConfig),
    ("track", _track_backend, TrackConfig),
]


@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_auto_pairs_primary_with_fallback(label, build, Cfg, key):
    be = build(Cfg(), "auto")
    assert type(be).__name__ == "FallbackBackend"
    assert type(be.primary).__name__ == "ClaudeMaxBackend"
    assert type(be.fallback).__name__ == "OpenAiCompatibleBackend"


@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_role_primary_selects_the_configured_primary(label, build, Cfg, key):
    assert type(build(Cfg(), "primary")).__name__ == "ClaudeMaxBackend"


@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_role_fallback_selects_the_configured_fallback(label, build, Cfg, key):
    be = build(Cfg(), "fallback")
    assert type(be).__name__ == "OpenAiCompatibleBackend"
    assert be.model == Cfg().cheap_model


@pytest.mark.parametrize("alias,expected", [
    ("claude-max", "ClaudeMaxBackend"),      # legacy name for the primary role
    ("deepseek", "OpenAiCompatibleBackend"),  # legacy name for the fallback role
])
def test_legacy_provider_aliases_still_resolve(alias, expected, key):
    # Existing crons and muscle memory pass the old provider-flavoured values;
    # they must keep working now that the flag names a role.
    assert type(_build_compose_backend(CvConfig(), alias)).__name__ == expected


@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_auto_degrades_to_primary_only_when_fallback_has_no_key(label, build, Cfg, no_key, caplog):
    # A claude-max-only setup (no per-token key) is legitimate and must keep
    # running -- but with no safety net, so it has to say so rather than build a
    # keyless fallback that 401s at the exact moment the primary goes down.
    be = build(Cfg(), "auto")
    assert type(be).__name__ == "ClaudeMaxBackend"
    assert "no fallback" in caplog.text


@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_explicitly_selecting_an_unusable_fallback_is_fatal(label, build, Cfg, no_key):
    # Nothing to degrade to when the fallback is what was asked for.
    with pytest.raises(BackendError, match="requires an api_key"):
        build(Cfg(), "fallback")


def test_unknown_backend_name_in_config_is_rejected(key):
    cfg = TriageConfig()
    cfg.fallback_backend = "not-a-provider"
    with pytest.raises(BackendError, match="unknown backend"):
        _build_backend(cfg, "fallback")


def test_config_can_repoint_the_fallback_provider(monkeypatch):
    # The point of PR-B: swapping providers is config, not code.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = TriageConfig()
    cfg.fallback_backend = "openai"
    cfg.cheap_model = "gpt-4o-mini"
    be = _build_backend(cfg, "fallback")
    assert be.model == "gpt-4o-mini"
    assert be.url == "https://api.openai.com/v1/chat/completions"
    assert be.api_key == "sk-openai"


def test_base_url_override_is_honoured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:8080/v1")
    be = _build_backend(TriageConfig(), "fallback")
    assert be.url == "http://localhost:8080/v1/chat/completions"


def test_track_config_exposes_backend_selectors():
    # track had no selectors while its backend was hardcoded; config-driven
    # construction needs them, and they must match the other two sub-apps.
    cfg = TrackConfig()
    assert cfg.primary_backend == "claude-max"
    assert cfg.fallback_backend == "deepseek"


# ── CodeRabbit PR#7: a bad choice must fail, not degrade ─────────────────────

@pytest.mark.parametrize("label,build,Cfg", SITES)
def test_unrecognised_choice_raises_rather_than_silently_meaning_auto(label, build, Cfg, key):
    # "primry" matches neither role branch, so before this guard it fell through
    # to auto -- a typo would quietly get a backend nobody asked for.
    with pytest.raises(BackendError, match="unknown backend choice"):
        build(Cfg(), "primry")


# ── CodeRabbit PR#7: every sub-app exposes the flag it honours ───────────────

@pytest.mark.parametrize("argv,cmd", [
    (["triage", "run"], "triage"),
    (["cv", "run", "--lead", "x"], "cv"),
    (["track", "run"], "track"),
])
def test_every_sub_app_parses_and_defaults_backend(argv, cmd):
    # A backend_choice parameter no CLI caller can set is a dead parameter --
    # exactly the bug this PR fixed in triage. Pin the flag on all three so the
    # capability can't drift back to being test-only.
    from sluice.cli import _build_parser
    args = _build_parser().parse_args(argv)
    assert args.backend == "auto"
    args = _build_parser().parse_args([*argv, "--backend", "fallback"])
    assert args.backend == "fallback"
