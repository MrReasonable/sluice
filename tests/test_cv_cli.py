from sluice.cli import _build_parser


def test_cv_run_parses_lead_and_flags():
    args = _build_parser().parse_args(
        ["cv", "run", "--lead", "acme-em", "--dry-run", "--backend", "deepseek"])
    assert args.group == "cv" and args.cmd == "run"
    assert args.lead == "acme-em" and args.dry_run and args.backend == "deepseek"


def test_cv_run_parses_all_shortlist():
    args = _build_parser().parse_args(["cv", "run", "--all-shortlist", "--limit", "3"])
    assert args.all_shortlist and args.limit == 3


# The former _build_compose_backend tests (fallback provider/model/url/key, role
# behaviour for auto/claude-max/deepseek, and the max-effort pins) moved to
# tests/test_backend_selection.py (generic role/provider construction + the new
# end-to-end effort tests) and
# tests/test_app_operations.py::test_compose_cv_threads_the_cv_config_into_the_backend
# (cv's specific config-field mapping into Sluice.backend's kwargs), now that cv's
# backend construction is Sluice.backend() rather than a cli.py wrapper.
