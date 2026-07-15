"""Guard tests for `Sluice.backend()` -- role selection over the configured
primary/fallback pair (SP-core-facade Task 1).

`--backend` (and, before this move, the per-command `_build_backend` /
`_build_compose_backend` / `_track_backend` wrappers in cli.py) names a ROLE
(auto/primary/fallback), never a provider; the config decides which provider
fills each role. This file pins that resolution against the new composition-
root API so it no longer depends on cli.py's now-removed `_select_backend`.
It is a straight port of the former tests/test_cli_backend_selection.py: every
assertion there is preserved here, retargeted at `Sluice(Config()).backend(...)`.
"""
import pytest

from sluice.core.app import Sluice
from sluice.core.backends import BackendError, DEFAULT_BASE_URLS
from sluice.core.config import Config


def _b(role="auto", **kw):
    base = dict(primary_name="claude-max", primary_model="m", effort="max", host="",
                claude_path="claude", fallback_name="deepseek", fallback_model="cheap")
    base.update(kw)
    return Sluice(Config()).backend(role, **base)


@pytest.fixture
def key(monkeypatch):
    """The normal deployed state: the per-token fallback has its credentials."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# ── auto: pairs primary+fallback, or degrades ────────────────────────────────

def test_auto_builds_a_fallback_pair_with_a_key(key):
    be = _b("auto")
    assert type(be).__name__ == "FallbackBackend"
    assert type(be.primary).__name__ == "ClaudeMaxBackend"
    assert type(be.fallback).__name__ == "OpenAiCompatibleBackend"


def test_auto_degrades_to_bare_primary_without_a_fallback_key(no_key, caplog):
    # A claude-max-only setup (no per-token key) is legitimate and must keep
    # running -- but with no safety net, so it has to say so rather than build a
    # keyless fallback that 401s at the exact moment the primary goes down.
    be = _b("auto")
    assert type(be).__name__ == "ClaudeMaxBackend"
    assert "no fallback" in caplog.text


# ── primary: bare, ignores the fallback ──────────────────────────────────────

def test_primary_role_ignores_the_fallback(no_key):
    assert _b("primary").__class__.__name__ == "ClaudeMaxBackend"


# ── fallback: strict, missing key is fatal ───────────────────────────────────

def test_fallback_role_selects_the_configured_fallback(key):
    be = _b("fallback", fallback_model="cheap")
    assert type(be).__name__ == "OpenAiCompatibleBackend"
    assert be.model == "cheap"


def test_fallback_role_missing_key_is_fatal(no_key):
    # Nothing to degrade to when the fallback is what was asked for.
    with pytest.raises(BackendError, match="requires an api_key"):
        _b("fallback")


# ── legacy provider-flavoured aliases ────────────────────────────────────────

def test_alias_claude_max_is_primary(no_key):
    assert _b("claude-max").__class__.__name__ == "ClaudeMaxBackend"


def test_alias_deepseek_is_fallback(key):
    assert _b("deepseek").__class__.__name__ == "OpenAiCompatibleBackend"


# ── unrecognised choices fail loudly rather than defaulting to auto ─────────

def test_unknown_role_raises_rather_than_defaulting_to_auto():
    # "primry" matches neither role branch, so before this guard it fell through
    # to auto -- a typo would quietly get a backend nobody asked for.
    with pytest.raises(BackendError, match="unknown backend choice"):
        _b("primry")


def test_unknown_provider_name_still_raises_backenderror():
    # make_backend is unchanged, so a bad PROVIDER name keeps raising BackendError
    # (not a new KeyError) -- this pins that the exception class did not drift.
    with pytest.raises(BackendError):
        _b("primary", primary_name="bogus")


def test_unknown_provider_name_in_fallback_role_still_raises_backenderror(no_key):
    with pytest.raises(BackendError, match="unknown backend"):
        _b("fallback", fallback_name="not-a-provider")


# ── per-provider construction: config repoints the fallback provider ────────

def test_config_can_repoint_the_fallback_provider(monkeypatch):
    # The point of the original PR-B: swapping providers is config, not code.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    be = _b("fallback", fallback_name="openai", fallback_model="gpt-4o-mini")
    assert be.model == "gpt-4o-mini"
    assert be.url == "https://api.openai.com/v1/chat/completions"
    assert be.api_key == "sk-openai"


def test_base_url_override_is_honoured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:8080/v1")
    be = _b("fallback")
    assert be.url == "http://localhost:8080/v1/chat/completions"


def test_deepseek_fallback_uses_the_documented_default_endpoint(key):
    # Pins the default endpoint via the constant, not a live URL literal.
    be = _b("fallback")
    assert be.model == "cheap"
    assert be.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.api_key == "sk-test"


# ── effort reaches the primary's cmd_template end to end ────────────────────
# The per-sub-app spy tests in test_app_operations.py assert `effort` is *passed*
# to Sluice.backend(); they stub backend() itself, so they cannot see whether the
# value actually lands in the constructed ClaudeMaxBackend's cmd_template. These
# two close that gap -- formerly covered by the now-deleted cli.py wrapper tests
# (test_triage_backend_primary_uses_medium_effort, test_compose_backend_claude_max_uses_max_effort).
def test_effort_medium_reaches_the_primary_cmd_template():
    # Triage judges a large backlog; medium keeps a full run from taking hours.
    be = _b("primary", effort="medium")
    ct = be.cmd_template
    assert ct[ct.index("--effort") + 1] == "medium"


def test_effort_max_reaches_the_primary_cmd_template():
    # cv compose needs full reasoning quality.
    be = _b("primary", effort="max")
    ct = be.cmd_template
    assert ct[ct.index("--effort") + 1] == "max"


# ── CLI-level coverage carried over unchanged ────────────────────────────────
# Not about Sluice.backend() itself, but it was in the file being retired and
# nothing else in the suite pins it: the `--backend` flag must exist (and
# default to "auto") on every sub-app that honours it.

@pytest.mark.parametrize("argv,cmd", [
    (["triage", "run"], "triage"),
    (["cv", "run", "--lead", "x"], "cv"),
    (["track", "run"], "track"),
])
def test_every_sub_app_parses_and_defaults_backend(argv, cmd):
    # A backend_choice parameter no CLI caller can set is a dead parameter --
    # exactly the bug this guard exists to catch (it happened once in triage).
    from sluice.cli import _build_parser
    args = _build_parser().parse_args(argv)
    assert args.backend == "auto"
    args = _build_parser().parse_args([*argv, "--backend", "fallback"])
    assert args.backend == "fallback"
