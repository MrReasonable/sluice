"""_print_report: the ingest run summary printer (a helper, not a handler).

Re-homed from tests/test_cli.py. It prints to STDERR, and prints the merged/refused
(#5) counts only when non-zero -- both behaviours are pinned here.
"""
from sluice.cli import _print_report


def test_print_report_surfaces_skipped(capsys):
    class _R:
        sources = []
        written = {"created": 1, "updated": 2, "skipped": 3}

    _print_report(_R())
    err = capsys.readouterr().err        # the summary prints to stderr, not stdout
    assert "3 skipped" in err


def test_print_report_surfaces_merged_and_refused(capsys):
    class _R:
        sources = []
        written = {"created": 1, "updated": 0, "merged": 2, "refused": 3, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "2 merged" in err and "3 refused" in err
