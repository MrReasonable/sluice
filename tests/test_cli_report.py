"""_print_report: the ingest run summary printer (a helper, not a handler).

Re-homed from tests/test_cli.py. It prints to STDERR, and prints the merged/refused
(#5) counts only when non-zero -- both behaviours are pinned here.
"""
from sluice.cli import _print_report
from sluice.ingest.engine import SourceResult


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


def test_print_report_names_a_withheld_count_on_the_per_source_line(capsys):
    # #156's circuit breaker: a withheld run must be distinguishable from an ordinary
    # `fresh` count on the digest an operator actually reads.
    class _R:
        sources = [SourceResult("cwjobs", status="ok", fetched=25, fresh=25,
                                drift="fallback", withheld=25)]
        written = {"created": 0, "updated": 0, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "withheld=25" in err


def test_print_report_omits_withheld_when_zero(capsys):
    # Sparse, matching the `written:` line's own discipline -- an ordinary healthy source
    # must not print a stray "withheld=0" on every line.
    class _R:
        sources = [SourceResult("cord", status="ok", fetched=10, fresh=10)]
        written = {"created": 10, "updated": 0, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "withheld" not in err
