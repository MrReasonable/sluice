"""ingest + health handlers, driven through the real main(argv) over the harness.

Re-homed from tests/test_cli.py. Every assertion there is preserved (the _print_report
helper tests moved to tests/test_cli_report.py -- they exercise a printer, not a
handler), and the no-enabled-sources branch (unwitnessed at HEAD) is added.
"""
import pytest

from sluice.cli import _build_parser


def _cord_line(out):
    return next(line for line in out.splitlines() if line.startswith("cord"))


def test_list_sources_lists_shipped_sources(cli):
    _h, run = cli()
    rc, out, _err = run(["ingest", "list-sources"])
    assert rc == 0
    assert "cord" in out and "wttj" in out


def test_disable_then_list_shows_disabled(cli):
    _h, run = cli()
    assert run(["ingest", "disable", "cord"])[0] == 0
    out = run(["ingest", "list-sources"])[1]
    assert "disabled" in _cord_line(out)


def test_enable_reverses_disable(cli):
    _h, run = cli()
    run(["ingest", "disable", "cord"])
    run(["ingest", "enable", "cord"])
    out = run(["ingest", "list-sources"])[1]
    assert "enabled" in _cord_line(out)


def test_health_command_runs(cli):
    _h, run = cli()
    rc, out, _err = run(["health"])
    assert rc == 0
    assert "cord" in out


def test_list_sources_health_flag(cli):
    _h, run = cli()
    rc, out, _err = run(["ingest", "list-sources", "--health"])
    assert rc == 0
    assert "baseline=" in out


def test_ingest_run_no_enabled_sources_returns_1(cli):
    # cmd_run refuses (rc 1) when the selection is empty, BEFORE touching the browser
    # -- an offline branch nothing witnessed at HEAD. Disabling the one source we then
    # ask for empties the selection via _is_enabled without an unknown-id KeyError.
    _h, run = cli()
    assert run(["ingest", "disable", "cord"])[0] == 0
    rc, _out, _err = run(["ingest", "run", "--source", "cord"])
    assert rc == 1


def test_ingest_run_all_and_source_are_mutually_exclusive():
    # Pins the production fix: `run --source X --all` used to silently drop --all.
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["ingest", "run", "--source", "cord", "--all"])


def test_ingest_run_accepts_each_selector_alone():
    # The exclusion must not over-restrict: bare, --all alone, --source alone stay valid.
    p = _build_parser()
    assert p.parse_args(["ingest", "run"]).group == "ingest"
    assert p.parse_args(["ingest", "run", "--all"]).all is True
    assert p.parse_args(["ingest", "run", "--source", "cord"]).source == ["cord"]
