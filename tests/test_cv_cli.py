from sluice.cli import _build_parser
from sluice.core.backends import DEFAULT_BASE_URLS


def test_cv_run_parses_lead_and_flags():
    args = _build_parser().parse_args(
        ["cv", "run", "--lead", "acme-em", "--dry-run", "--backend", "deepseek"])
    assert args.group == "cv" and args.cmd == "run"
    assert args.lead == "acme-em" and args.dry_run and args.backend == "deepseek"


def test_cv_run_parses_all_shortlist():
    args = _build_parser().parse_args(["cv", "run", "--all-shortlist", "--limit", "3"])
    assert args.all_shortlist and args.limit == 3


def test_compose_fallback_targets_deepseek_direct(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)  # exercise the default
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig())
    assert be.fallback.model == "deepseek-v4-flash"
    # Assert the default endpoint via the constant, not a live URL literal:
    # pins that the provider default is applied and the path appended.
    assert be.fallback.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.fallback.api_key == "sk-test"


def test_build_compose_backend_auto_returns_fallback_backend(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")  # a configured fallback exists
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig(), "auto")
    assert type(be).__name__ == "FallbackBackend"


def test_build_compose_backend_claude_max_returns_claude_max_only():
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig(), "claude-max")
    assert type(be).__name__ == "ClaudeMaxBackend"


def test_build_compose_backend_deepseek_returns_deepseek_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig(), "deepseek")
    assert type(be).__name__ == "OpenAiCompatibleBackend"


def test_compose_backend_auto_primary_uses_max_effort(monkeypatch):
    # cv compose still needs max reasoning; only triage should drop to medium.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig(), "auto")
    ct = be.primary.cmd_template
    assert ct[ct.index("--effort") + 1] == "max"


def test_compose_backend_claude_max_uses_max_effort():
    from sluice.cli import _build_compose_backend
    from sluice.cv.config import CvConfig
    be = _build_compose_backend(CvConfig(), "claude-max")
    ct = be.cmd_template
    assert ct[ct.index("--effort") + 1] == "max"
