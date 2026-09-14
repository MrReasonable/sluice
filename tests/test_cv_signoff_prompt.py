"""_print_signoff_claims: the `cv signoff` prompt's claim-kind splitter (#167 Task 15).

Re-homed the way tests/test_cli_report.py re-homed _print_report: a small print helper,
tested directly with capsys rather than through the full cmd_cv_signoff CLI plumbing
(interactive input(), a seeded Vault hold, argparse) that would otherwise be needed just
to observe two lines of stderr.

hold_for_signoff's `claims` array (sluice/cv/engine.py) carries tagged kinds of entry, and
#329 added a `framing\\t` kind this prompt prints apart from both: a raw audit verdict
line ("unsupported\\t<claim>\\t<cited-id>", exactly what every hold
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


def test_a_hold_with_no_framing_prints_exactly_what_it_printed_before_329(capsys):
    """Whole-output equality, recorded from the code BEFORE #329 changed the printer: a hold
    stamped before that change must not be re-described by it."""
    _print_signoff_claims("slug", ["unsupported\tMotivated by placeholder\tNONE",
                                   "style\tSLOP leverage: x"])
    assert capsys.readouterr().err == (
        "cv signoff: slug has 1 unsupported claim(s):\n"
        "  - unsupported\tMotivated by placeholder\tNONE\n"
        "cv signoff: slug has 1 style/voice concern(s):\n"
        "  - SLOP leverage: x\n")


def test_a_non_string_claim_is_printed_rather_than_crashing_the_prompt(capsys):
    # `needs_signoff` is hand-editable YAML in a lead note, and Sluice.sign_off_cv passes
    # a parsed JSON array through element-wise (`parsed if isinstance(parsed, list) else
    # [str(parsed)]`, core/app.py) -- so `needs_signoff: '[1, 2]'` reaches this printer as
    # ints, not strings. This helper's split calls `.partition("\t")` per entry, which the
    # pre-Task-15 `print(f"  - {c}")` never did: an int raised AttributeError and took the
    # whole `cv signoff` command down for that lead. It failed in the SAFE direction (the
    # hold stands, no CV is released), but the candidate could not clear the hold at all.
    # An unsplittable entry carries no "style" tag, so it belongs in the fabrication group
    # -- the same place today's unprefixed entries go.
    _print_signoff_claims("slug", [1, "unsupported\tclaim"])
    err = capsys.readouterr().err
    assert "2 unsupported claim(s)" in err
    assert "- 1" in err


def test_framing_prints_under_its_own_heading_and_is_never_counted_as_a_claim(capsys):
    from sluice.core.leads import framing_entries
    from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS
    # A real hold carries both flags and concerns (#329); a row with only one line could
    # not catch the two being printed out of order or one being dropped.
    flags_line = f"culture flags: {FRAMING_FLAGS[0]}"
    concerns_line = f"concerns: {FRAMING_CONCERNS[0]}"
    _print_signoff_claims("slug", ["unsupported\tMotivated by placeholder\tNONE",
                                   *framing_entries([flags_line, concerns_line])])
    assert capsys.readouterr().err == (
        "cv signoff: slug: triage notes the composer was given (context, not claims):\n"
        f"  - {flags_line}\n"
        f"  - {concerns_line}\n"
        "cv signoff: slug has 1 unsupported claim(s):\n"
        "  - unsupported\tMotivated by placeholder\tNONE\n")
