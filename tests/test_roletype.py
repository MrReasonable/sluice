"""#223 §2.2 and §2.4: the closed `role_type` set, and observing one from a JD.

Two properties carry the weight here, and both are about the SAFE direction.
`normalise_role_type` must fold an unrecognised value to blank rather than pass it
through, because `classify` does a substring test on whatever it is handed and
`Contract-to-perm` matches the contract branch regardless of what its author meant.
And `observe_role_type` must abstain far more readily than it answers: an observation
outranks every other origin (§2.5), so a wrong one is the worst outcome this module
can produce, while a blank one just leaves today's behaviour in place.
"""
import logging

import pytest

from sluice.core.roletype import (
    ASSUMED,
    DECLARED,
    OBSERVED,
    normalise_role_type,
    observe_role_type,
    provenance_rank,
    trusted_provenance,
)


# ── normalise_role_type: the closed set ──────────────────────────────────────
@pytest.mark.parametrize("raw", ["contract", "Contract", "CONTRACT", " contract ",
                                 '"contract"', "contractor", "Contracting"])
def test_the_contract_spellings_a_real_vault_holds_all_fold_to_one_token(raw):
    assert normalise_role_type(raw) == "contract"


@pytest.mark.parametrize("raw", ["permanent", "Permanent", "perm", "Perm",
                                 " perm ", '"Permanent"'])
def test_the_permanent_spellings_a_real_vault_holds_all_fold_to_one_token(raw):
    assert normalise_role_type(raw) == "permanent"


def test_every_alias_the_table_ships_is_exercised():
    """Enumerated over `_ALIASES` itself, never a hand-picked sample.

    Measured before this existed: 7 of the 13 rows could be DELETED with nothing in the
    suite reddening -- only `fixed term` was reached by any other test. An alias table
    whose rows are individually inert is a hand-list nothing checks, which is the shape
    this repo keeps finding; parametrising over the real dict means a row added later is
    covered the day it lands rather than the day someone remembers.
    """
    from sluice.core.roletype import _ALIASES
    assert _ALIASES, "the alias table is empty -- this sweep would certify nothing"
    for spelling, canonical in _ALIASES.items():
        assert normalise_role_type(spelling) == canonical
        assert normalise_role_type(spelling.upper()) == canonical
        assert normalise_role_type(f'  "{spelling}"  ') == canonical


@pytest.mark.parametrize("raw", ["", "   ", None, '""'])
def test_a_blank_stays_blank_without_a_warning(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="sluice.core.roletype"):
        assert normalise_role_type(raw) == ""
    assert not caplog.records, "blank is a real answer, not an unrecognised one"


def test_an_ambiguous_value_folds_to_blank_rather_than_matching_the_contract_branch():
    # The issue names this shape: today `"contract" in "contract-to-perm"` is True,
    # so a value whose whole point is that it is BOTH takes the contract branch.
    assert normalise_role_type("Contract-to-perm") == ""


def test_an_unrecognised_value_warns_rather_than_being_stored(caplog):
    with caplog.at_level(logging.WARNING, logger="sluice.core.roletype"):
        assert normalise_role_type("banana") == ""
    assert any("banana" in r.getMessage() for r in caplog.records), (
        "an unrecognised role_type must be loud -- silently blanking it is how the "
        "vocabulary drifts without anyone noticing")


def test_normalising_never_raises_whatever_it_is_handed():
    # Matches `_safe_or_blank`'s per-item-isolation discipline: this runs inside the
    # ingest sink loop, where one malformed row must not abort the run.
    for hostile in (object(), 17, ["contract"], {"a": 1}):
        assert normalise_role_type(hostile) == ""


# ── observe_role_type: high precision, cheap abstention ──────────────────────
@pytest.mark.parametrize("jd", [
    "This role is inside IR35 and runs for an initial period.",
    "Offered outside IR35 for an initial six months.",
    "We are offering a day rate of up to £600 for the right person.",
    "The daily rate is negotiable for the right person.",
    "This is a fixed-term engagement covering a parental leave.",
    # "permanently" must not trip the permanent marker: the vocabulary is
    # word-bounded, so an adverb sharing a stem is not evidence of basis.
    "An interim appointment while we recruit permanently.",
    "This is a temporary role covering a secondment.",
])
def test_a_posting_that_says_contract_is_observed_as_contract(jd):
    assert observe_role_type(jd) == "contract"


@pytest.mark.parametrize("jd", [
    "£550 per day, negotiable.",
    "£500/day, negotiable depending on experience.",
    "A 6-month contract with a strong chance of extension.",
])
def test_the_recall_this_vocabulary_deliberately_gives_up(jd):
    """These ARE contract adverts and they abstain. Named rather than left implicit.

    `per day`, `/day` and `month contract` were standalone markers until they were
    measured firing on ordinary prose -- "ships several releases per day", "we handle
    2TB/day of telemetry", "most customers sign a 12 month contract with us". The last is
    the expensive one: "6-month contract" is among the commonest ways a contract advert
    states its nature, and it is lexically indistinguishable from the customer-contract
    sentence.

    Abstention is the safe side. An observation outranks the user's own `declared` value,
    is written to the vault and is sticky, so a false one costs more than a missing one;
    and a genuine contract advert almost always also says IR35, "day rate" or "contract
    role" -- a claim FALSIFIED by the three rows above it, none of which carries any of
    the three. Measured recall on 30 realistic contract adverts is 37%: 19 abstentions,
    and zero in the wrong direction. That is the trade, stated as a number rather than as
    a reassurance.

    `per day` and `/day` remain DAY MARKERS for the SALARY field in
    `triage/classify.py`, where the string is short and controlled -- all six non-empty
    golden salary strings are money-shaped and at most five words. That is a different
    question about a different input, not an inconsistency.
    """
    assert observe_role_type(jd) == ""


@pytest.mark.parametrize("jd", [
    "This is a permanent position on our core engineering team.",
    "A perm role with equity and a company pension.",
    "Offered on a permanent basis after a short probation.",
    # Correctly `permanent`, not an abstention: it IS a permanent role, and the fact that
    # its employer is an umbrella company is the job's subject rather than its basis.
    "A permanent role at an umbrella company, running payroll for contractors.",
])
def test_a_posting_that_says_permanent_is_observed_as_permanent(jd):
    assert observe_role_type(jd) == "permanent"


def test_a_posting_carrying_evidence_for_both_abstains():
    jd = "This is a contract position, not a permanent role."
    assert observe_role_type(jd) == "", (
        "§2.4: a JD carrying evidence for both abstains -- an observation outranks "
        "every other origin, so a coin-flip here would be worse than saying nothing")


# ── the vocabulary's OWN bar: a bare word in prose is not evidence ────────────
#
# Every string below observed a basis before review. They are the reason a qualifier
# now counts only in a BASIS-BEARING COLLOCATION -- immediately followed by a noun
# naming the thing being advertised -- rather than on its own. The module docstring
# claimed a "near-conclusive" bar all along; these are what it actually cleared.
#
# The cost is real and accepted: a genuine advert saying only "we are looking for a
# contractor" now abstains. Recall is the cheap side. An `observed` value outranks the
# user's own `declared` one, is written to the vault, and is sticky -- the next run sees
# `observed == previous` and skips, so a hand correction is overwritten again.
@pytest.mark.parametrize("jd", [
    # A basis word doing ordinary work in a sentence.
    "In the interim you will report to the Head of Engineering.",
    "You will own the interim reporting pack each month.",
    "Our Contractors and Suppliers governance team owns this process.",
    "We do not offer temporary or agency placements.",
    # A standalone marker doing ordinary work. All four of these observed `contract`
    # until the collocation rule reached `_CONTRACT_STANDALONE` too.
    "The platform ships several releases per day.",
    "We handle 2TB/day of telemetry across the fleet.",
    "A per diem is paid for travel to the London office.",
    "Most customers sign a 12 month contract with us.",
    # A `post-<word>` compound after a qualifier. `post` was a basis noun, and hyphens
    # fold to spaces, so the DERIVED set reproduced the bare-word defect it replaced.
    "Contract Manager (Permanent). You will own contract post-award administration.",
    "Salaried, permanent. Temporary post-launch cover is provided by the vendor.",
    # An annual figure is not evidence of permanence: fixed-term and interim staff are
    # quoted annual salaries too, pro rata. `per annum`, `annual salary` and `fte` were
    # all permanent markers before review, and all three are wrong here.
    "12 month FTC. Salary £90,000 per annum, pro rata.",
    "We are covering 2 FTE while the team scales.",
    "6 month engagement. Compensation is £90,000 per annum equivalent.",
    "Fixed term maternity cover. The annual salary for this role is £90,000.",
])
def test_a_basis_word_used_in_passing_is_not_an_observation(jd):
    assert observe_role_type(jd) == ""


@pytest.mark.parametrize("jd", [
    "This is a contract-to-perm position.",
    "Temp to perm opportunity.",
    "Contract to permanent role, 6 months initial.",
    "A 12 month contract with a view to permanent.",
    "Temp to hire, with a view to a longer term role.",
])
def test_a_role_that_is_both_in_sequence_abstains(jd):
    # `normalise_role_type` already REFUSES the stored form of this idea
    # (`Contract-to-perm` -> "" and a warning). An observer that resolved the same idea
    # to `permanent` -- the one provenance outranking the user's own -- would contradict
    # its own module, and did.
    assert normalise_role_type("Contract-to-perm") == ""
    assert observe_role_type(jd) == ""


@pytest.mark.parametrize("jd", [
    "",
    None,
    "We are hiring a Senior Software Engineer to work on our payments platform.",
    "You will report to the Head of Engineering and own the deployment pipeline.",
])
def test_a_posting_that_says_nothing_about_basis_abstains(jd):
    assert observe_role_type(jd) == ""


def test_full_time_alone_is_not_evidence_of_permanence():
    # Deliberately excluded from the vocabulary: "full-time" describes HOURS, and
    # contract postings routinely carry it. Treating it as permanence would mint a
    # wrong `observed` value -- which outranks the user's own `declared` one -- on a
    # large population of contract adverts.
    assert observe_role_type("This is a full-time, on-site position.") == ""


def test_observing_never_raises_whatever_it_is_handed():
    for hostile in (object(), 17, ["contract"], {"a": 1}):
        assert observe_role_type(hostile) == ""


# ── provenance ───────────────────────────────────────────────────────────────
def test_the_ladder_is_observed_over_declared_over_assumed_over_blank():
    assert (provenance_rank(OBSERVED) > provenance_rank(DECLARED)
            > provenance_rank(ASSUMED) > provenance_rank(""))


def test_an_unknown_provenance_ranks_at_the_bottom():
    assert provenance_rank("whatever") == provenance_rank("")


def test_only_observed_and_declared_are_trusted_by_the_gate():
    assert trusted_provenance(OBSERVED)
    assert trusted_provenance(DECLARED)
    assert not trusted_provenance(ASSUMED)
    assert not trusted_provenance("")
    assert not trusted_provenance("whatever")


@pytest.mark.parametrize("jd", [
    # The PERMANENT side of the same rule. Nothing pinned it: a mutant putting
    # `permanent` back as a bare standalone marker passed the whole suite, and every one
    # of these then observed `permanent` on an advert that says nothing about basis. The
    # falsifying rows existed only for `interim`/`temporary`/`fixed term`.
    "You will deputise for the permanent Head of Data.",
    "This team is permanent; the project is not.",
    "Reporting to the permanent Head of Engineering.",
])
def test_a_permanent_word_used_in_passing_is_not_an_observation(jd):
    assert observe_role_type(jd) == ""


def test_the_vocabulary_the_cross_product_is_built_from():
    """SCOPE, and it is the half the sweep below cannot provide.

    That sweep iterates the DERIVED marker lists, so deleting a basis noun shrinks both
    the vocabulary and the sweep together and every remaining row still passes -- a
    guard built over its own subject cannot see its subject shrink. Measured: dropping 4
    of the 11 nouns left the sweep green.

    So the INPUTS are pinned exactly, with the same "update this deliberately"
    instruction `test_core_layering`'s module count carries. `post` is absent on purpose
    and named here so a future reader does not helpfully restore it: hyphens fold to
    spaces, so it matched every `post-<word>` compound following a qualifier.
    """
    from sluice.core.roletype import (
        _BASIS_NOUNS,
        _CONTRACT_QUALIFIERS,
        _PERMANENT_QUALIFIERS,
    )
    assert set(_BASIS_NOUNS) == {
        "role", "position", "contract", "appointment", "assignment", "engagement",
        "basis", "placement", "vacancy", "opportunity"}, (
        "the basis nouns changed. If one was ADDED or REMOVED, update this set -- and "
        "check the new one against `post`, which was removed for matching `post-award` "
        "and `post-launch` after a qualifier.")
    assert set(_CONTRACT_QUALIFIERS) == {
        "fixed term", "interim", "temporary", "temp", "freelance", "contract"}
    assert set(_PERMANENT_QUALIFIERS) == {"permanent", "perm"}


def test_every_generated_collocation_actually_fires():
    """Enumerated over the real cross-product, never a hand-picked sample.

    Measured before this existed: across the 39 distinct JDs the suite feeds
    `observe_role_type`, 66 of 74 contract markers and 16 of 22 permanent markers never
    fired at all. Round 1 added a sweep over `_ALIASES` for exactly this reason and
    shipped an unswept `_BASIS_NOUNS` in the same commit -- so the derived set repeated
    the hand-list's own failure one layer up.

    Each phrase is exercised through `observe_role_type`, not by matching the regex
    directly: a marker that compiles but is unreachable (shadowed by the ambiguity set,
    or matching on the other side too) is exactly what this is looking for.
    """
    from sluice.core.roletype import _JD_CONTRACT_MARKERS, _JD_PERMANENT_MARKERS
    assert _JD_CONTRACT_MARKERS and _JD_PERMANENT_MARKERS, (
        "the marker sets are empty -- this sweep would certify nothing")
    for markers, expected in ((_JD_CONTRACT_MARKERS, "contract"),
                              (_JD_PERMANENT_MARKERS, "permanent")):
        for phrase in markers:
            jd = f"We are advertising this as a {phrase}."
            assert observe_role_type(jd) == expected, (
                f"the {expected} marker {phrase!r} is in the vocabulary but does not "
                f"reach a verdict -- it is shadowed, or it matches both sides")


@pytest.mark.parametrize("jd", [
    # PLURALS, and the reason the cross-product generates singular only. A plural names a
    # CLASS of roles the advert discusses far more often than the one it advertises, and
    # these are the populations where that is the job: recruitment, bid and business
    # development, consultancy, procurement, HR, payroll. Measured when plurals were
    # generated: 27 of 32 such adverts observed `contract`, against 18 before.
    "You will source candidates for contract opportunities across the region.",
    "The team advertises contract vacancies on behalf of our clients.",
    "You will manage contract placements end to end.",
    "Reporting on temporary placements is part of the role.",
    "You will own contract engagements with our enterprise customers.",
    # The both-sides rule does NOT save this one: `permanent` is followed by `and`, so
    # only the contract side fires. Worth its own row -- it is the case that looks like
    # abstention would cover it.
    "We advertise both permanent and contract positions.",
    # Bare `ir35` and `umbrella company` were standalone markers on the claim that they
    # "appear in no permanent advert". False of the jobs whose SUBJECT is contracting.
    "Permanent Compliance Manager. You will own our IR35 determinations.",
    "Permanent Payroll Lead. Experience of umbrella company models required.",
])
def test_a_basis_phrase_that_is_the_jobs_SUBJECT_is_not_an_observation(jd):
    assert observe_role_type(jd) == ""
