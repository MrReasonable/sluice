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


def test_primary_model_reaches_the_claude_max_backend():
    # The deleted test_track_backend_wiring was the only test proving that
    # primary_model lands on the constructed backend's .model attribute, not merely
    # that it's passed as a kwarg to make_backend. Without this, _make_primary could
    # silently drop or hardcode the model and nothing would fail.
    be = _b("primary", primary_name="claude-max", primary_model="claude-sonnet-4-5")
    assert be.model == "claude-sonnet-4-5"


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


# ── #28: the compose timeout reaches the constructed backend ─────────────────────
def test_timeout_reaches_the_primary_backend():
    """Same gap the effort tests above close, for the knob added by #28: a test that only
    asserted `Sluice.backend()` was CALLED with a timeout would not see whether it landed
    on the object that runs the subprocess.
    """
    assert _b("primary", timeout=900).timeout == 900


def test_timeout_defaults_without_the_caller_naming_one():
    """Every existing caller omits it, so the omitted path is the live one."""
    assert _b("primary").timeout == 300


# ── #28: the knob must reach the ENGINE, not merely Sluice.backend() ─────────────
def test_compose_cv_forwards_cv_compose_timeout_to_the_backend(monkeypatch, tmp_path):
    """Deleting `timeout=cvcfg.compose_timeout` from compose_cv's Sluice.backend(...)
    call left the WHOLE SUITE GREEN. `test_timeout_reaches_the_primary_backend` starts at
    Sluice.backend(timeout=900) -- one frame PAST the wiring under test, so it could not
    see the knob failing to arrive.

    That is the same mistake twice: a guard placed one layer above the thing that breaks.
    This one starts at `compose_cv`, so the wiring itself is what is asserted, and it
    checks the value landed on the constructed object rather than merely that some
    timeout was passed -- a sentinel distinct from the default is what makes a
    cross-field misread visible.
    """
    import dataclasses
    from sluice.core.app import Sluice
    from sluice.cv.config import load_cv_config

    cvc = dataclasses.replace(load_cv_config(), compose_timeout=1234,
                              primary_backend="claude-max", compose_model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: cvc)

    seen = {}
    real = Sluice.backend

    def spy(self, role, **kw):
        seen["timeout"] = kw.get("timeout")
        return real(self, role, **kw)

    monkeypatch.setattr(Sluice, "backend", spy)
    # No try/except, for the reason CodeRabbit gave for its sibling in test_doctor.py:
    # a bare `except Exception: pass` lets a later regression pass this test with `seen`
    # already populated by the spy. Measured -- the dry run completes on an empty
    # shortlist, so there was never an exception for the handler to catch.
    Sluice().compose_cv(all_shortlist=True, dry_run=True)
    assert seen.get("timeout") == 1234, (
        "compose_cv did not forward cv.compose_timeout to Sluice.backend()")


def test_the_timeout_reaches_the_fallback_leg_too(key):
    """`auto` builds a FallbackBackend whose `complete` tries the primary and THEN the
    fallback, so a knob that sizes only the primary leaves half a lead's worst case
    pinned at the shipped default -- and `--backend fallback` ignoring it entirely.

    Both legs are asserted because threading it into `_make_primary` alone passed every
    test that existed when the knob was added.
    """
    be = _b("auto", timeout=900)
    assert be.primary.timeout == 900
    assert be.fallback.timeout == 900


def test_the_timeout_reaches_a_strictly_selected_fallback(key):
    """`--backend fallback` takes its own construction arm, which silently ignored the
    knob: it never called the builder the timeout had been threaded into."""
    assert _b("fallback", timeout=900).timeout == 900
