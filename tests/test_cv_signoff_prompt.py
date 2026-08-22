"""_print_signoff_claims: the `cv signoff` prompt's claim-kind splitter (#167 Task 15).

Re-homed the way tests/test_cli_report.py re-homed _print_report: a small print helper,
tested directly with capsys rather than through the full cmd_cv_signoff CLI plumbing
(interactive input(), a seeded Vault hold, argparse) that would otherwise be needed just
to observe two lines of stderr.

hold_for_signoff's `claims` array (sluice/cv/engine.py) now carries TWO shapes of entry:
a raw audit verdict line ("unsupported\\t<claim>\\t<cited-id>", exactly what every hold
stamped before this change wrote) and a "style\\t<finding>"-tagged one (cv.style_hold,
#167 Task 15). The prompt must announce them differently -- a style/voice finding is not
a fabrication risk -- and an UNPREFIXED entry must keep TODAY'S wording exactly, so a
pre-existing hold is not re-described by this upgrade.
"""
from sluice.cli import _print_signoff_claims


def test_an_unprefixed_entry_keeps_todays_wording(capsys):
    _print_signoff_claims("slug", ["unsupported\tMotivated by placeholder\tNONE"])
    err = capsys.readouterr().err
    assert "slug has 1 unsupported claim(s):" in err
    assert "Motivated by placeholder" in err
    # No style-kind wording leaks into a hold that carries no style claim at all.
    assert "style" not in err.lower()


def test_a_style_prefixed_entry_is_not_announced_as_a_fabrication_risk(capsys):
    _print_signoff_claims("slug", ["style\tSLOP leverage: I leverage the same delivery"])
    err = capsys.readouterr().err
    assert "unsupported claim" not in err
    assert "SLOP leverage" in err


def test_the_signoff_prompt_names_the_kind(capsys):
    # The brief's own pin: a MIXED hold (one legacy fabrication entry, one style entry)
    # keeps the fabrication count worded exactly as it always was.
    _print_signoff_claims("slug", ["unsupported\tclaim", "style\tSLOP leverage: ..."])
    err = capsys.readouterr().err
    assert "1 unsupported claim(s)" in err


def test_a_hold_carrying_only_style_claims_prints_no_fabrication_line(capsys):
    _print_signoff_claims("slug", ["style\tSLOP leverage: ...", "style\tSLOP streamline: ..."])
    err = capsys.readouterr().err
    assert "unsupported claim" not in err


def test_a_hold_carrying_both_kinds_names_both(capsys):
    _print_signoff_claims("slug", ["unsupported\tMotivated by placeholder\tNONE",
                                   "style\tSLOP leverage: ..."])
    err = capsys.readouterr().err
    assert "1 unsupported claim(s)" in err
    assert "SLOP leverage" in err
