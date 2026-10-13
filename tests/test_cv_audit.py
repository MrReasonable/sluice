import pytest
from sluice.cv import audit as A
from sluice.core.backends import Completion

class FakeBackend:
    def __init__(self, out): self.out = out; self.prompt = None
    def complete(self, prompt): self.prompt = prompt; return Completion(self.out)

# The bundle's entry ids, which a `deduced` verdict must cite from (#380).
IDS = frozenset({"SF3", "SF5"})


def test_prompt_frames_bundle_as_only_truth():
    p = A.build_audit_prompt("CV", "BUNDLE")
    assert "SOURCE BUNDLE" in p and "CV" in p and "BUNDLE" in p

def test_flagged_are_unsupported_and_paraphrase_lines():
    report = "supported\tled team\tSF3\nparaphrase\tgrew it\tSF3\nunsupported\tran Meridian Trust\tNONE"
    be = FakeBackend(report)
    got_report, flagged = A.run_audit(be, "CV", "BUNDLE")
    assert got_report == report
    assert len(flagged) == 2
    assert any("unsupported" in f for f in flagged)

def test_all_supported_yields_no_flags():
    be = FakeBackend("supported\tled team\tSF3")
    _, flagged = A.run_audit(be, "CV", "BUNDLE")
    assert flagged == []

def test_unsupported_claims_is_the_unsupported_subset():
    # The sign-off gate (#60) blocks on `unsupported` ONLY. `paraphrase` (same fact
    # reworded) is legitimate tailoring -- blocking on it would fire on nearly every
    # CV and train rubber-stamping -- so it stays advisory. This filter is the sole
    # thing given a consequence; run_audit and CvResult.audit_flags are unchanged.
    _, flagged = A.run_audit(FakeBackend(
        "supported\tled team\tSF3\nparaphrase\tgrew it\tSF3\n"
        "unsupported\tMotivated by placeholder\tNONE"), "CV", "BUNDLE")
    assert A.unsupported_claims(flagged, IDS) == ["unsupported\tMotivated by placeholder\tNONE"]

def test_unsupported_claims_empty_when_no_unsupported():
    assert A.unsupported_claims([], IDS) == []
    assert A.unsupported_claims(["paraphrase\tgrew it\tSF3"], IDS) == []

def test_unsupported_claims_matches_the_verdict_token_exactly():
    # The verdict is the FIRST tab-delimited field; a malformed 'unsupportedness'
    # prefix must NOT be read as 'unsupported' and block (a startswith would).
    assert A.unsupported_claims(["unsupportedness\tsome claim\tNONE"], IDS) == []
    assert A.unsupported_claims(["unsupported\treal claim\tNONE"], IDS) == ["unsupported\treal claim\tNONE"]


# #380: a claim no single entry states is DEDUCED when every particular it names is
# evidenced somewhere in the bundle and the timeline lets them combine; it is UNSUPPORTED
# when any particular has no evidence or the timeline rules the combination out. The
# example: coaching in a technology is deducible from leadership plus that technology
# before or during the leadership; it is a fabrication when the technology appears nowhere.

def test_the_prompt_defines_deduced_as_every_particular_evidenced_and_timeline_permitting():
    p = A.build_audit_prompt("CV", "BUNDLE")
    assert "supported, paraphrase, deduced, unsupported" in p
    assert A.DEDUCED_RULE in p
    for part in ("no single entry", "every particular", "timeline", "every id"):
        assert part in A.DEDUCED_RULE, part


def test_the_prompt_names_both_ways_a_combination_is_unsupported():
    p = A.build_audit_prompt("CV", "BUNDLE")
    assert A.UNSUPPORTED_RULE in p
    assert "no evidence anywhere" in A.UNSUPPORTED_RULE
    assert "timeline rules" in A.UNSUPPORTED_RULE


def test_the_prompt_measures_a_bullet_against_its_own_role():
    # A bullet sits under a role heading in the audit text, and the timeline must be read
    # against THAT role, else work begun after it would license a claim made inside it.
    # The whole sentence, not "role it sits under": both rule texts carry that phrase too,
    # so it alone stayed green with this sentence deleted (witnessed).
    assert ("A bullet is read against the role it sits under in the CV; the profile "
            "against the whole career.") in A.build_audit_prompt("CV", "BUNDLE")


def test_a_deduced_line_is_flagged():
    report = "supported\tled team\tSF3\ndeduced\tcoached teams in Examplestack\tSF3,SF5"
    _, flagged = A.run_audit(FakeBackend(report), "CV", "BUNDLE")
    assert flagged == ["deduced\tcoached teams in Examplestack\tSF3,SF5"]


def test_a_deduced_line_citing_real_ids_does_not_hold():
    # Visible in audit_flags, without the sign-off consequence: `unsupported` holds.
    _, flagged = A.run_audit(FakeBackend(
        "deduced\tcoached teams in Examplestack\tSF3,SF5\n"
        "unsupported\tcoached teams in Otherstack\tNONE"), "CV", "BUNDLE")
    assert A.unsupported_claims(flagged, IDS) == ["unsupported\tcoached teams in Otherstack\tNONE"]



@pytest.mark.parametrize("cites", ["NONE", "", "SF9", "SF3,SF9", "  "])
def test_a_deduction_without_known_ids_holds(cites):
    # A deduction is "every part evidenced somewhere", so one citing nothing -- or an id no
    # entry carries -- has shown no evidence and is held like `unsupported`. Without this
    # an auditor writing `deduced ... NONE` would release a CV the old verdict set held.
    line = f"deduced\tcoached teams in Examplestack\t{cites}"
    assert A.unsupported_claims([line], IDS) == [line]


@pytest.mark.parametrize("cites", ["SF3,SF5", "SF3, SF5", "[SF3] [SF5]", "sf3", "SF3;SF5",
                                   "(SF3)", "SF3 and SF5", "SF3/SF5"])
def test_a_deduction_citing_only_known_ids_does_not_hold(cites):
    assert A.unsupported_claims([f"deduced\tcoached teams\t{cites}"], IDS) == []


def test_a_deduction_line_with_no_cite_field_holds():
    assert A.unsupported_claims(["deduced\tcoached teams"], IDS) == ["deduced\tcoached teams"]


@pytest.mark.parametrize("line", [
    "deduced coached teams NONE",          # spaces where the tabs belong
    "deduced: coached teams\tNONE",        # a colon after the verdict
    "Deduced coached teams SF3",           # no cite FIELD, so nothing is evidenced
    "unsupported coached teams NONE",
    "unsupported: coached teams\tNONE",
])
def test_a_line_without_tabs_is_read_by_its_leading_verdict_word(line):
    # The verdict is the line's first WORD, so a model that writes spaces or a colon
    # instead of a tab still has its `unsupported` held, and a `deduced` with no cite field
    # fails closed. Read by the tab field alone, these released the CV (round-two review).
    assert A.unsupported_claims([line], IDS) == [line]
