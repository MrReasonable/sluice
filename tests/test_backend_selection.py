"""Guard tests for `Sluice.backend()` -- ONE configured backend per stage (#333).

There used to be a role layer here (auto/primary/fallback) over a primary/fallback pair, and
a provider failure under `auto` silently sent the prompt to a second provider and model.
#333 removed it: a stage names one provider and model, `RetryingBackend` retries it on
itself, and `--backend` is a one-run PROVIDER override. These rows pin that resolution, and
the per-construction properties (model, effort, timeout, host, endpoint) that each used to be
pinned once per leg.
"""
import pytest

import sluice.core.app as app_mod
from sluice.core.app import Sluice
from sluice.core.backends import (BackendError, DEFAULT_BASE_URLS, DEFAULT_MODELS,
                                  RetryingBackend)
from sluice.core.config import Config


def _b(**kw):
    base = dict(provider="claude-max", model="m", effort="max", host="",
                claude_path="claude")
    base.update(kw)
    return Sluice(Config()).backend(**base)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# ── one backend, retried on itself ───────────────────────────────────────────

def test_the_configured_backend_is_wrapped_for_retry_and_labelled(key):
    be = _b(provider="deepseek", model="m-cfg")
    assert isinstance(be, RetryingBackend)
    assert be.retries == Config().backend_retries == 2
    assert be.label == "deepseek m-cfg"
    assert type(be.inner).__name__ == "OpenAiCompatibleBackend"


def test_backend_retries_reaches_the_wrapper(key):
    import dataclasses
    s = Sluice(dataclasses.replace(Config(), backend_retries=0))
    be = s.backend(provider="deepseek", model="m", effort="max", host="", claude_path="claude")
    assert be.retries == 0


def test_the_label_names_the_default_model_when_none_was_configured(key):
    # The label is what a run report prints as the model that served, so a blank model must
    # surface as the one make_backend actually used -- never as "deepseek ".
    assert _b(provider="deepseek", model="").label == f"deepseek {DEFAULT_MODELS['deepseek']}"


def test_the_injected_sleep_reaches_the_wrapper(key):
    slept = []
    be = Sluice(Config(), sleep=slept.append).backend(
        provider="deepseek", model="m", effort="max", host="", claude_path="claude")
    assert be._sleep == slept.append


def test_a_missing_key_raises_at_construction_and_never_degrades(no_key):
    # The degradable "fallback with no key -> primary-only, warn" arm is gone: a stage whose
    # one backend cannot be built fails loudly, like every other seam.
    with pytest.raises(BackendError, match="requires an api_key"):
        _b(provider="deepseek")


def test_no_code_path_constructs_a_second_provider(key, monkeypatch):
    """#333 acceptance: nothing builds a provider other than the one named."""
    built = []
    real = app_mod._build_backend
    monkeypatch.setattr(app_mod, "_build_backend",
                        lambda name, *a, **k: built.append(name) or real(name, *a, **k))
    _b(provider="deepseek")
    assert built == ["deepseek"]


# ── --backend: a one-run PROVIDER override ───────────────────────────────────

def test_override_to_the_configured_provider_keeps_the_configured_model(key):
    # `--backend claude-max` on a claude-max install is the commonest override in an
    # existing cron; it must not silently change model.
    assert _b(provider="deepseek", model="m-cfg", override="deepseek").label == \
        "deepseek m-cfg"


def test_override_to_another_provider_uses_that_providers_default_model(key):
    be = _b(provider="claude-max", model="claude-sonnet-4-5", override="deepseek")
    assert be.label == f"deepseek {DEFAULT_MODELS['deepseek']}"
    assert be.inner.model == DEFAULT_MODELS["deepseek"]


@pytest.mark.parametrize("role", ["auto", "primary", "fallback"])
def test_a_retired_role_name_raises_with_the_migration_hint(role):
    with pytest.raises(BackendError, match="omit --backend") as ei:
        _b(override=role)
    assert "#333" in str(ei.value)


@pytest.mark.parametrize("role", ["auto", "primary", "fallback"])
def test_a_retired_role_raises_even_when_a_backend_is_injected(role):
    # Validated BEFORE the injected-seam short-circuit, or a test/MCP caller injecting a
    # backend would have a retired spelling silently accepted.
    s = Sluice(Config(), backend=object())
    with pytest.raises(BackendError):
        s.backend(provider="claude-max", model="m", effort="max", host="",
                  claude_path="claude", override=role)


def test_an_unknown_override_raises_listing_the_providers():
    with pytest.raises(BackendError, match="deepseek"):
        _b(override="deepsek")


def test_an_unknown_configured_provider_still_raises_backenderror():
    with pytest.raises(BackendError, match="unknown backend"):
        _b(provider="bogus")


# ── per-provider construction ────────────────────────────────────────────────

def test_the_model_reaches_the_constructed_provider():
    assert _b(provider="claude-max", model="claude-sonnet-4-5").inner.model == \
        "claude-sonnet-4-5"


def test_config_can_point_a_stage_at_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    be = _b(provider="openai", model="gpt-4o-mini").inner
    assert be.model == "gpt-4o-mini"
    assert be.url == "https://api.openai.com/v1/chat/completions"
    assert be.api_key == "sk-openai"


def test_base_url_override_is_honoured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:8080/v1")
    assert _b(provider="deepseek").inner.url == "http://localhost:8080/v1/chat/completions"


def test_deepseek_uses_the_documented_default_endpoint(key):
    be = _b(provider="deepseek", model="cheap").inner
    assert be.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.api_key == "sk-test"


# The per-sub-app spy tests in test_app_operations.py assert `effort` is *passed* to
# Sluice.backend(); they stub backend() itself, so they cannot see whether the value lands in
# the constructed ClaudeMaxBackend's cmd_template. These close that gap.
@pytest.mark.parametrize("effort", ["medium", "max"])
def test_effort_reaches_the_cmd_template(effort):
    ct = _b(effort=effort).inner.cmd_template
    assert ct[ct.index("--effort") + 1] == effort


def test_claude_max_host_and_path_reach_the_constructed_backend():
    be = _b(host="remote.example.invalid", claude_path="/opt/claude/bin/claude").inner
    assert be.host == "remote.example.invalid"
    assert be.claude_path == "/opt/claude/bin/claude"


def test_an_override_keeps_the_stages_claude_max_host(key):
    # The host/path describe WHERE claude-max runs for this stage, so `--backend claude-max`
    # on a deepseek-configured stage must still reach the configured host.
    be = _b(provider="deepseek", host="remote.example.invalid", override="claude-max").inner
    assert be.host == "remote.example.invalid"


# ── the CLI flag ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [["triage", "run"], ["cv", "run", "--lead", "x"],
                                  ["track", "run"]])
def test_every_sub_app_parses_backend_and_defaults_to_none(argv):
    # A parameter no CLI caller can set is a dead parameter -- exactly the bug this guard
    # exists to catch (it happened once in triage).
    from sluice.cli import _build_parser
    assert _build_parser().parse_args(argv).backend is None
    assert _build_parser().parse_args([*argv, "--backend", "deepseek"]).backend == "deepseek"


@pytest.mark.parametrize("value", ["auto", "primary", "fallback"])
def test_the_cli_refuses_a_retired_role_with_the_migration_message(value, capsys):
    from sluice.cli import _build_parser
    with pytest.raises(SystemExit) as ei:
        _build_parser().parse_args(["cv", "run", "--lead", "x", "--backend", value])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "retired in #333" in err and "omit --backend" in err


def test_the_cli_refuses_an_unknown_provider_listing_the_real_ones(capsys):
    from sluice.cli import _build_parser
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["track", "run", "--backend", "deepsek"])
    err = capsys.readouterr().err
    assert all(name in err for name in DEFAULT_MODELS)


# ── #28: the compose timeout reaches the constructed backend ─────────────────

def test_timeout_reaches_the_constructed_backend():
    assert _b(timeout=900).inner.timeout == 900


def test_timeout_defaults_to_the_root_key_without_the_caller_naming_one():
    assert _b().inner.timeout == Config().backend_timeout


def test_compose_cv_forwards_cv_compose_timeout_to_the_backend(monkeypatch, tmp_path):
    """Deleting `timeout=cvcfg.compose_timeout` from compose_cv's Sluice.backend(...) call
    once left the WHOLE SUITE GREEN, because the guard started one frame past the wiring
    under test. This one starts at `compose_cv`, with a sentinel distinct from the default."""
    import dataclasses
    from sluice.cv.config import load_cv_config

    cvc = dataclasses.replace(load_cv_config(), compose_timeout=1234,
                              backend="claude-max", model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: cvc)

    seen = {}
    real = Sluice.backend

    def spy(self, **kw):
        seen["timeout"] = kw.get("timeout")
        return real(self, **kw)

    monkeypatch.setattr(Sluice, "backend", spy)
    # #242: the refusal precedes the backend, so the vault must be set up to reach the spy.
    from sluice.core.vault import Vault
    from tests.conftest import make_composable
    make_composable(Vault(str(tmp_path / "vault")))
    Sluice().compose_cv(all_shortlist=True, dry_run=True)
    assert seen.get("timeout") == 1234, (
        "compose_cv did not forward cv.compose_timeout to Sluice.backend()")
