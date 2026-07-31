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
    cap = capsys.readouterr()
    assert cap.out == ""                 # the summary prints to stderr, not stdout
    assert "3 skipped" in cap.err
    # sparse (#5): merged/refused print ONLY when non-zero, so a clean run omits them
    assert "merged" not in cap.err and "refused" not in cap.err


def test_print_report_surfaces_merged_and_refused(capsys):
    class _R:
        sources = []
        written = {"created": 1, "updated": 0, "merged": 2, "refused": 3, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "2 merged" in err and "3 refused" in err


def test_print_report_surfaces_merged_away(capsys):
    # #81: merged_away (url-proven) and merged_away_unproven (every weaker match -- a
    # location-token overlap, or an inconclusive comparison) are distinct outcomes with
    # distinct wording -- a human reading the run summary must be able to tell
    # "self-healed" from "still needs a decision" without opening the vault.
    class _R:
        sources = []
        written = {"created": 1, "updated": 0, "merged_away": 2,
                    "merged_away_unproven": 1, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "2 merged-away" in err and "1 merged-away (unproven)" in err
