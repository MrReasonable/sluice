import pytest

from sluice.core.leads import NON_ANSWER_COMPANIES
from sluice.triage.classify import classify
from sluice.triage.config import TriageConfig

# Role preferences are personal, so the suite never asserts on real ones: the
# `titles` fixture generates synthetic accept/reject lists (see conftest.py).
# Floors are explicit because the shipped defaults are 0 (neutral/off).


def _cfg(titles, **kw):
    accept, reject = titles
    cfg = TriageConfig(contract_floor_gbp_day=480, perm_floor_gbp=90_000, **kw)
    cfg.accept_titles = list(accept)
    cfg.reject_titles = list(reject)
    return cfg


def L(titles=None, **kw):
    role = titles[0][0].title() if titles else "Some Role"
    base = {"company": "Acme", "role": role,
            "location": "remote", "salary": "", "role_type": "permanent",
            "url": "https://x/y"}
    base.update(kw)
    return base


def test_accepted_title_is_kept(titles):
    accept, _ = titles
    assert classify(L(titles, role=accept[0].title()), _cfg(titles))[0] == "keep"


def test_rejected_title_is_rejected(titles):
    _, reject = titles
    verdict, why = classify(L(titles, role=reject[0].title()), _cfg(titles))
    assert verdict == "reject"
    assert reject[0] in why


def test_title_on_neither_list_is_not_rejected_on_shape(titles):
    # An unlisted title must not be screened out by the shape gate; it is the
    # LLM judge's business, not the pre-gate's.
    assert classify(L(titles, role="Wholly Unlisted Role"), _cfg(titles))[0] == "keep"


def test_geography_reject(titles):
    # Rejecting on geography requires a CONFIGURED geography; the shipped default
    # abstains (see test_unconfigured_geography_gate_abstains).
    cfg = _cfg(titles)
    cfg.target_locations = ["remote"]
    assert classify(L(titles, location="Whitlockfurt, Vesperia"), cfg)[0] == "reject"


def test_contract_day_rate_floor(titles):
    assert classify(L(titles, role_type="contract", salary="£450/day"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, role_type="contract", salary="£600/day"), _cfg(titles))[0] == "keep"


def test_perm_salary_floor(titles):
    assert classify(L(titles, salary="£80,000"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, salary="£120,000"), _cfg(titles))[0] == "keep"


def test_configured_reject_company_is_skipped(titles):
    # reject_companies ships empty by default (no PII); the mechanism itself
    # is exercised here via an explicit config, not the code default.
    cfg = TriageConfig(reject_companies=["acme"])
    assert classify(L(titles, company="Acme"), cfg)[0] == "reject"


def test_needs_review_when_no_company(titles):
    d, r = classify(L(titles, company="", url="https://x/y"), _cfg(titles))
    assert d == "needs_review"


# #151: the sentinel check used to be a hand-rolled `not company or company.lower() ==
# "unknown"`, which recognised exactly one placeholder spelling. `is_placeholder_company`
# recognises the whole `NON_ANSWER_COMPANIES` vocabulary -- legacy/foreign notes carry
# "Confidential", "N/A", "Undisclosed", ... none of which sluice itself ever writes, but
# all of which are equally unusable as a real employer name. Sampled rather than
# exhaustive (`NON_ANSWER_COMPANIES` has 19 members): the exhaustive sweep belongs to
# `test_all_non_answers_are_placeholders` in `tests/test_leads_company.py` (Task 1),
# which already covers every member and casing; this test only needs to prove classify()
# DELEGATES to that predicate rather than its own narrower copy.
_SAMPLE_PLACEHOLDERS = ("unknown", "confidential", "n/a", "not disclosed", "stealth startup")
assert set(_SAMPLE_PLACEHOLDERS) <= NON_ANSWER_COMPANIES, (
    "the sample drifted from the real vocabulary -- pick values that still exist")


@pytest.mark.parametrize("value", _SAMPLE_PLACEHOLDERS)
@pytest.mark.parametrize("casing", [str.lower, str.upper, str.title])
def test_placeholder_company_is_needs_review(titles, value, casing):
    d, _ = classify(L(titles, company=casing(value), url="https://x/y"), _cfg(titles))
    assert d == "needs_review"


def test_real_company_is_not_needs_review_on_company_alone(titles):
    # Control case: a real employer name must classify normally (keep, here, since
    # nothing else about the lead is disqualifying) rather than tripping the
    # placeholder gate.
    d, _ = classify(L(titles, company="Example Foundry"), _cfg(titles))
    assert d == "keep"


def test_placeholder_company_does_not_shield_a_rejected_title(titles):
    # The placeholder check sits LAST in classify() -- after the title/location/pay
    # rejects -- on purpose (see the comment in classify.py). A lead with BOTH a
    # placeholder company and a rejected title must still reject: the resolution
    # pass this gate feeds is for leads that are otherwise worth pursuing, not a
    # backdoor around every other filter.
    _, reject = titles
    verdict, why = classify(
        L(titles, role=reject[0].title(), company="Unknown"), _cfg(titles))
    assert verdict == "reject"
    assert reject[0] in why


def test_reason_is_plain_no_em_dash(titles):
    _, reject = titles
    _, reason = classify(L(titles, role=reject[0].title()), _cfg(titles))
    assert "\u2014" not in reason and reason


# ── the accept list must not whitelist an unrelated disqualifier ─────────────

def test_accept_token_does_not_whitelist_a_reject_token_it_does_not_contain(titles):
    # The accept list exists so a BROAD reject pattern cannot kill a good title.
    # It must not go further and wave through a title carrying an accept token
    # AND a genuine disqualifier -- that is the mixed case the gate exists for.
    accept, reject = titles
    mixed = f"{accept[0].title()} / {reject[0].title()}"
    verdict, why = classify(L(titles, role=mixed), _cfg(titles))
    assert verdict == "reject"
    assert reject[0] in why


def test_accept_list_still_protects_a_substring_reject_pattern(titles):
    # The reason the override exists: a reject pattern that is PART of an accepted
    # title must be ignored, not treated as a disqualifier.
    accept, _ = titles
    cfg = _cfg(titles)
    cfg.reject_titles = [accept[0].split()[-1]]   # a bare word inside the accepted phrase
    assert classify(L(titles, role=accept[0].title()), cfg)[0] == "keep"


def test_unconfigured_geography_gate_abstains(titles):
    # The empty list must mean "no opinion", not "match nothing". Guarding this is
    # the difference between passing every lead through and rejecting every lead
    # that names a location.
    cfg = _cfg(titles)
    cfg.target_locations = []
    for loc in ("Palmerburgh", "Osterfurt", "Remote", "Anywhere at all", ""):
        lead = L(titles, location=loc)
        assert classify(lead, cfg)[0] == "keep", f"unconfigured gate rejected {loc!r}"


def test_configured_geography_gate_still_filters(titles):
    cfg = _cfg(titles)
    cfg.target_locations = ["palmerburgh"]
    assert classify(L(titles, location="Palmerburgh"), cfg)[0] == "keep"
    assert classify(L(titles, location="Osterfurt"), cfg)[0] == "reject"


# ── salary parsing ────────────────────────────────────────────────────────────
# The floors used to run on a parser that stripped every non-digit and concatenated
# the rest, which broke them in both directions. These pin both directions, because
# only one of them is expensive: a floor that fails OPEN just shows you jobs you did
# not want, while a floor that fails CLOSED silently bins a job you did.

def test_salary_range_is_read_at_its_ceiling_not_its_concatenation(titles):
    # "£30,000-£40,000" once parsed as 3000040000 and sailed over every floor, so a
    # configured floor silently did nothing. It must be read as its top (40,000).
    assert classify(L(titles, salary="£30,000-£40,000"), _cfg(titles))[0] == "reject"
    # ...and a range whose ceiling clears the floor is kept, even though its FLOOR
    # does not. Rejecting on the lower bound would bin a job the user wants.
    assert classify(L(titles, salary="£80,000-£120,000"), _cfg(titles))[0] == "keep"


def test_k_notation_is_expanded(titles):
    # "£60k" parsed as 60, which the perm branch's credibility guard turned into a
    # (lucky) abstain -- so the floor silently ignored every k-formatted salary.
    assert classify(L(titles, salary="£60k"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, salary="up to £75k"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, salary="£120k"), _cfg(titles))[0] == "keep"


def test_k_notation_day_rate_is_not_binned(titles):
    # The contract branch had NO credibility guard, so "£1.5k/day" parsed as 15 and
    # was REJECTED against a 480/day floor. This is the destructive direction: a
    # well-paid contract silently binned. 1.5k/day is 1500, comfortably over.
    assert classify(L(titles, role_type="contract", salary="£1.5k/day"), _cfg(titles))[0] == "keep"


def test_percentages_do_not_contribute_a_salary(titles):
    # "10%" must not parse as a money amount; the salary here is 50,000, under floor.
    assert classify(L(titles, salary="£50,000 + 10% bonus"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, salary="£120,000 + 10% bonus"), _cfg(titles))[0] == "keep"


def test_unparseable_salary_abstains(titles):
    # No credible number means no opinion. Never a reject.
    for salary in ("", "Competitive", "DOE", "Negotiable"):
        assert classify(L(titles, salary=salary), _cfg(titles))[0] == "keep"


def test_stray_numbers_do_not_become_the_day_rate(titles):
    # "6 month contract" must not make this a £6/day lead and bin it.
    assert classify(
        L(titles, role_type="contract", salary="6 month contract, £500/day"),
        _cfg(titles))[0] == "keep"


def test_unconfigured_floors_never_reject(titles):
    # The shipped floors are 0. An unconfigured gate abstains: no salary string,
    # however mangled, may produce a reject.
    neutral = TriageConfig()
    neutral.accept_titles, neutral.reject_titles = [], []
    for salary in ("£1", "£60k", "£30,000-£40,000", "junk", ""):
        for role_type in ("permanent", "contract"):
            assert classify(
                L(titles, salary=salary, role_type=role_type), neutral)[0] != "reject"


def test_k_notation_does_not_lose_a_pound_to_float_truncation(titles):
    # int() truncates a float product toward zero, and most decimal fractions are not
    # exactly representable: int(2.01 * 1000) is 2009, not 2010. A day rate sitting exactly
    # on a configured floor would then flip keep->reject on binary representation error
    # rather than on the advertised pay. (CodeRabbit flagged this; its example, 1.15, does
    # NOT truncate on CPython -- 2.01 does. 18 such cases exist between £0.01k and £20k.)
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("£2.01k") == (2010, 2010, "GBP")
    assert _salary_ceiling("£4.02k") == (4020, 4020, "GBP")
    assert _salary_ceiling("£1.5k/day") == (1500, 1500, "GBP")

    # ...and the boundary behaviour that motivates it: a 2010/day contract must not be
    # rejected by a 2010 floor. (_cfg already fixes the floors, so set it directly.)
    cfg = _cfg(titles)
    cfg.contract_floor_gbp_day = 2010
    assert classify(L(titles, role_type="contract", salary="£2.01k/day"), cfg)[0] == "keep"


def test_a_bare_number_is_not_a_salary(titles):
    # A salary field carrying a stray identifier -- "Ref 50000", a postcode -- must not be
    # read as advertised pay. It would be REJECTED by a floor, binning a lead whose pay was
    # never stated at all: the fails-closed direction, which is the bug class this whole
    # module exists to remove, arriving through the parser. Money needs money CONTEXT: a
    # currency symbol, or a k suffix.
    from sluice.triage.classify import _salary_ceiling
    for junk in ("postcode 1234", "Ref 50000", "Job ID 60000", "12 month contract"):
        assert _salary_ceiling(junk) is None, f"{junk!r} parsed as money"
        # ...and therefore never rejects, even under a floor that the stray number is below.
        assert classify(L(titles, salary=junk), _cfg(titles))[0] != "reject"


def test_money_context_is_a_symbol_or_a_k_suffix(titles):
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("£60,000") == (60000, 60000, "GBP")
    assert _salary_ceiling("$120,000").advertised == 120000
    assert _salary_ceiling("$120,000").currency == "USD"
    # the suffix IS the context; no currency named, so no conversion is applied
    assert _salary_ceiling("60k") == (60000, 60000, None)
    assert _salary_ceiling("60000") is None         # bare digits are not money


def test_a_single_symbol_range_reads_its_upper_bound(titles):
    # Boards routinely write "£30,000-40,000" with ONE symbol. The upper bound then carries no
    # money context of its own, so the ceiling read as 30,000 and a £35k floor REJECTED a role
    # paying up to £40k -- fails-closed, the expensive direction. A bare number IS money when it
    # is the tail of a range whose head was money.
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("£30,000-40,000") == (40000, 40000, "GBP")
    assert _salary_ceiling("£60k-80k") == (80000, 80000, "GBP")
    assert _salary_ceiling("£60k to £80k") == (80000, 80000, "GBP")

    # ...but a stray number that is NOT a range tail is still not money.
    assert _salary_ceiling("£500/day, ref 60000") == (500, 500, "GBP")

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 35_000
    assert classify(L(titles, salary="£30,000-40,000"), cfg)[0] == "keep"


def test_classify_signature_never_gains_a_side_effecting_dependency():
    import inspect
    params = set(inspect.signature(classify).parameters)
    assert params == {"lead", "cfg"}, (
        "classify() must stay pure -- no dossier_cache, sources, or fetcher "
        "parameter, per its own docstring's no-dossier/no-LLM contract")


# ── #128: reject/accept matching is word-boundary, not plain substring ────────

def test_reject_pattern_does_not_match_inside_a_longer_word(titles):
    # "engineer" is a strict character-for-character prefix of "engineering", so a
    # plain `pat in role` substring check treated "security engineer" as present
    # inside "Security Engineering Manager" -- a genuinely different, unrelated
    # word. Word-boundary matching must not fire here.
    cfg = _cfg(titles)
    cfg.reject_titles = ["security engineer"]
    cfg.accept_titles = []
    assert classify(L(titles, role="Security Engineering Manager"), cfg)[0] == "keep"


def test_reject_pattern_still_matches_as_a_standalone_word(titles):
    # The fix must not over-correct: a genuine standalone occurrence (followed by a
    # real word boundary -- space or end of string) still rejects.
    cfg = _cfg(titles)
    cfg.reject_titles = ["security engineer"]
    cfg.accept_titles = []
    assert classify(L(titles, role="Senior Security Engineer"), cfg)[0] == "reject"


def test_word_boundary_fix_lets_a_genuine_accept_title_through(titles):
    # Real leads lost to this: a role carrying BOTH a genuine accepted title
    # ("engineering manager") and an unrelated reject pattern that only collided
    # via the engineer/engineering prefix ("machine learning engineer") must be
    # kept, not silently killed before the LLM judge ever sees it.
    cfg = _cfg(titles)
    cfg.accept_titles = ["engineering manager"]
    cfg.reject_titles = ["machine learning engineer"]
    verdict, _ = classify(
        L(titles, role="Ads Conversion Modeling, Machine Learning Engineering Manager"), cfg)
    assert verdict == "keep"


def test_reject_pattern_does_not_match_inside_a_longer_acronym(titles):
    # Same shape, a different pair: bare "vp" must not match inside "svp" -- there
    # is no boundary between the "s" and the "v".
    cfg = _cfg(titles)
    cfg.reject_titles = ["vp"]
    cfg.accept_titles = []
    assert classify(L(titles, role="SVP of Engineering"), cfg)[0] == "keep"
    # ...but a standalone "VP" is still caught.
    assert classify(L(titles, role="VP of Engineering"), cfg)[0] == "reject"


# A configured pattern that itself starts or ends in a non-word character (the "+"
# in "c++", the "." in "sr.") breaks plain `\b`: a boundary requires a word/non-word
# TRANSITION, and a pattern edge that is already non-word can never supply one side
# of it. `\bc\+\+\b` never matches "C++ Developer" -- verified live before fixing --
# because there is no boundary between the trailing "+" and the following space
# (non-word next to non-word). Each case below would silently never reject/accept
# before this fix, with no error and no signal that the pattern was inert.
@pytest.mark.parametrize("pat,role", [
    ("c++", "C++ Developer"),
    ("c#", "C# Retail Merchandiser"),
    ("sr.", "Sr. Director, Product Management"),
    (".net", "Backend Developer (.NET)"),
])
def test_reject_pattern_with_punctuation_still_matches_a_standalone_occurrence(titles, pat, role):
    cfg = _cfg(titles)
    cfg.reject_titles = [pat]
    cfg.accept_titles = []
    assert classify(L(titles, role=role), cfg)[0] == "reject"


# ── #223 §2.3: the gate decides on evidence, not on which search ran ──────────
#
# `role_type` used to be consulted unconditionally, and it records which SEARCH found
# the lead. The gate now reads the SALARY's own markers first and consults `role_type`
# only when the note says the value was observed on the posting or declared by the user.
#
# Every row here goes through the real `classify()`, never `_pay_basis` alone: the
# helper's answer is only interesting insofar as it moves a verdict, and a table
# asserting on the helper would stay green if the gate stopped calling it.
#
# `_cfg` fixes contract_floor_gbp_day=480 and perm_floor_gbp=90_000 throughout, which is
# what makes the two flip rows below discriminate: £300 rejects on the day branch and
# keeps on the annual one, and £1,200 does exactly the reverse.

_DAY_MARKERS = ["£450/day", "£450 per day", "£450 day rate", "£450 a day",
                "£450 daily", "£450 per diem", "£450 p/d", "£450 pd"]
_ANNUAL_MARKERS = ["£45,000 per annum", "£45,000 p.a.", "£45,000 pa",
                   "£45,000/year", "£45,000 per year", "£45,000 annually"]


@pytest.mark.parametrize("salary", _DAY_MARKERS)
def test_a_salary_that_names_a_day_basis_is_judged_against_the_day_floor(titles, salary):
    # 450 is under the 480 day floor and over _MIN_CREDIBLE_DAY_RATE, so a row reaching
    # the DAY branch rejects. It is also under _MIN_CREDIBLE_SALARY, so a row reaching
    # the ANNUAL branch keeps -- the branches disagree, which is what makes the row a
    # witness rather than a coincidence.
    assert classify(L(titles, role_type="", salary=salary), _cfg(titles))[0] == "reject"


@pytest.mark.parametrize("salary", _ANNUAL_MARKERS)
def test_a_salary_that_names_an_annual_basis_is_judged_against_the_annual_floor(titles, salary):
    # 45,000 is under the 90,000 annual floor and over _MIN_CREDIBLE_SALARY -> reject on
    # the ANNUAL branch; it is over the 480 day floor, so the DAY branch would keep.
    assert classify(L(titles, role_type="contract", role_type_source="declared",
                      salary=salary), _cfg(titles))[0] == "reject"


def test_the_salarys_own_marker_beats_a_declared_role_type(titles):
    # Steps 1-2 run before step 3. A user who declared `job_type: contract` on a search
    # that returned a posting advertising an annual salary is not the authority on this
    # posting's pay basis -- the posting is.
    assert classify(L(titles, role_type="contract", role_type_source="declared",
                      salary="£45,000 per annum"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, role_type="permanent", role_type_source="observed",
                      salary="£450/day"), _cfg(titles))[0] == "reject"


@pytest.mark.parametrize("source", ["declared", "observed"])
def test_an_unmarked_salary_consults_a_trusted_role_type(titles, source):
    # The flip rows. UNMARKED salaries, because a marked one can never reach step 3.
    assert classify(L(titles, role_type="contract", role_type_source=source,
                      salary="£300"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, role_type="contract", role_type_source=source,
                      salary="£1,200"), _cfg(titles))[0] == "keep"


@pytest.mark.parametrize("source", ["assumed", "", "whatever"])
def test_an_unmarked_salary_ignores_an_untrusted_role_type(titles, source):
    # The same two rows, flipped by provenance ALONE. This is #223's whole complaint:
    # `role_type: contract` here records that a contract-labelled SEARCH found the lead,
    # and nothing read the posting.
    assert classify(L(titles, role_type="contract", role_type_source=source,
                      salary="£300"), _cfg(titles))[0] == "keep"
    assert classify(L(titles, role_type="contract", role_type_source=source,
                      salary="£1,200"), _cfg(titles))[0] == "reject"


def test_a_note_predating_the_feature_carries_no_provenance_key_at_all(titles):
    # Not the same as a blank one, and worth its own row: `L()` builds the frontmatter
    # dict, so this is a note written before #223 ever ran. It reads as `assumed`.
    lead = L(titles, role_type="contract", salary="£300")
    assert "role_type_source" not in lead
    assert classify(lead, _cfg(titles))[0] == "keep"


@pytest.mark.parametrize("stored", ['"contract"', "Contractor", "Fixed-Term", " contract "])
def test_a_hand_typed_role_type_is_folded_before_the_gate_reads_it(titles, stored):
    # A human editing the note in Obsidian, or a vault accumulated before #223, holds
    # whatever spelling the writer used. Every row here needs the ALIAS fold, not merely
    # a `.lower()`: an earlier draft of this test used `Contract`, which a bare
    # `.lower()` also folds, so it stayed green under a mutant that deleted
    # `normalise_role_type` from the read path outright. #223 names the quoted spelling
    # as one a real vault actually holds.
    assert classify(L(titles, role_type=stored, role_type_source="declared",
                      salary="£300"), _cfg(titles))[0] == "reject"


def test_an_unrecognised_role_type_does_not_reach_the_contract_branch(titles):
    # The substring defect the closed set exists for: `"contract" in "contract-to-perm"`
    # is True, so this row took the day branch on its first eight characters however its
    # author meant it. It now folds to blank and falls through to the annual branch.
    assert classify(L(titles, role_type="Contract-to-perm", role_type_source="declared",
                      salary="£300"), _cfg(titles))[0] == "keep"


def test_hourly_and_weekly_pay_are_never_judged_against_the_day_floor(titles):
    # The harm the ORIGINAL §2.3 declined hourly/weekly support to avoid, kept as a test
    # now that the support exists. Routing either to the `day` basis moves the applicable
    # credibility floor from 1000 down to 50 and opens the day branch's reject window
    # exactly where realistic hourly and weekly figures sit.
    #
    # This test replaces `test_hourly_and_weekly_pay_stay_unrecognised`, which pinned the
    # decision commit 10 REVERSED and was left behind when it did. Measured: deleting the
    # `hour` and `week` rows from `_BASES` outright left that test passing, so it had
    # stopped guarding anything while still reading like a decision. A test that survives
    # the removal of the feature it names is worse than no test -- it certifies.
    cfg = _cfg(titles)          # already fixes contract_floor_gbp_day=480
    for salary in ("£65 per hour", "£250 per week"):
        verdict, why = classify(L(titles, role_type="", salary=salary), cfg)
        assert verdict == "keep", f"{salary} was judged against the day floor: {why}"
    # ...and each is judged against its OWN floor when one is set, which is what makes
    # the row above a choice rather than an accident of everything abstaining.
    assert classify(L(titles, role_type="", salary="£65 per hour"),
                    _cfg(titles, contract_floor_gbp_hour=80)) == (
        "reject", "Hourly rate below floor: 65 < 80")
    assert classify(L(titles, role_type="", salary="£250 per week"),
                    _cfg(titles, contract_floor_gbp_week=2000)) == (
        "reject", "Weekly rate below floor: 250 < 2000")


def test_an_unmarked_salary_with_no_trusted_provenance_is_judged_exactly_as_before(titles):
    # Step 4. The fall-through is today's behaviour byte-for-byte: a bare amount is read
    # as annual, deliberately unchanged rather than improved by a guess.
    assert classify(L(titles, role_type="", salary="£45,000"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, role_type="", salary="£120,000"), _cfg(titles))[0] == "keep"


# ── #223: hourly and weekly pay get their own bases and their own floors ─────
#
# The live defect this closes. The pay basis was never parsed for hourly or weekly, so
# both fell to the ANNUAL branch and the bare number met `perm_floor_gbp`. What decided
# the outcome was MAGNITUDE, not basis: measured against the shipped gate with
# perm_floor_gbp=90_000, `£999 per week` kept and `£1,000 per week` rejected, and
# `£2,000 per week` -- about £104k a year -- was binned silently.
#
# An earlier draft of §2.3 declined to add these BECAUSE the basis set was two-valued:
# admitting them would have routed both to `day`, moving the applicable credibility floor
# from 1000 down to 50 and opening the day branch's reject window exactly where realistic
# hourly and weekly figures sit. That argument is against reusing the DAY floor, not
# against parsing the basis -- so each basis now carries its own floor, and an unset one
# abstains exactly as `contract_floor_gbp_day` and `perm_floor_gbp` already do at 0.

_HOURLY = ["£65 per hour", "£65/hour", "£65/hr", "£65 an hour", "£65 hourly",
           "£65 p/h", "£65 ph"]
_WEEKLY = ["£2,000 per week", "£2,000/week", "£2,000/wk", "£2,000 a week",
           "£2,000 weekly", "£2,000 p/w", "£2,000 pw"]


@pytest.mark.parametrize("salary", _WEEKLY)
def test_a_weekly_rate_is_no_longer_binned_against_the_annual_floor(titles, salary):
    # THE defect. £2,000 a week is roughly £104,000 a year, and every one of these
    # spellings was rejected as a sub-90,000 salary.
    assert classify(L(titles, role_type="", salary=salary), _cfg(titles))[0] == "keep"


@pytest.mark.parametrize("salary", _HOURLY)
def test_an_hourly_rate_is_judged_on_its_own_floor_or_not_at_all(titles, salary):
    # Unconfigured, the hourly floor abstains -- the same 0-means-no-floor rule the two
    # existing floors use, and the reason a fresh install cannot silently bin anything.
    assert classify(L(titles, role_type="", salary=salary), _cfg(titles))[0] == "keep"
    cfg = _cfg(titles, contract_floor_gbp_hour=80)
    assert classify(L(titles, role_type="", salary=salary), cfg) == (
        "reject", "Hourly rate below floor: 65 < 80")


@pytest.mark.parametrize("salary", _WEEKLY)
def test_a_weekly_rate_is_judged_on_its_own_floor_when_one_is_set(titles, salary):
    cfg = _cfg(titles, contract_floor_gbp_week=2500)
    assert classify(L(titles, role_type="", salary=salary), cfg) == (
        "reject", "Weekly rate below floor: 2000 < 2500")


def test_an_hourly_floor_does_not_judge_a_daily_or_annual_rate(titles):
    # Each basis is judged against ITS OWN floor and no other. An 80/hour floor must not
    # reach a £450/day lead (which would reject it) or a £45,000 salary.
    cfg = _cfg(titles, contract_floor_gbp_hour=80)
    assert classify(L(titles, role_type="", salary="£450/day"), cfg)[0] == "reject"
    assert "Day rate" in classify(L(titles, role_type="", salary="£450/day"), cfg)[1]
    assert classify(L(titles, role_type="", salary="£120,000 per annum"), cfg)[0] == "keep"


def test_an_implausible_hourly_parse_abstains_rather_than_rejecting(titles):
    # The credibility guard, per basis. A stray "£2" is a mis-parse, not an offer, and a
    # wrong reject bins a lead the user never sees. These floors are MONOTONE -- they sit
    # inside the reject conjunction, so they can only turn a reject into an abstain.
    cfg = _cfg(titles, contract_floor_gbp_hour=80)
    assert classify(L(titles, role_type="", salary="£2 per hour"), cfg)[0] == "keep"


def test_an_implausible_weekly_parse_abstains_rather_than_rejecting(titles):
    cfg = _cfg(titles, contract_floor_gbp_week=2500)
    assert classify(L(titles, role_type="", salary="£20 per week"), cfg)[0] == "keep"


def test_a_salary_naming_two_different_bases_abstains(titles):
    # Ambiguity abstains, the same rule `observe_role_type` follows for a JD carrying
    # evidence for both. Picking a winner here would be an arbitrary precedence between
    # two things the advert actually said, and the day branch's reject window sits right
    # where an hourly figure lands -- so a wrong pick manufactures a reject.
    #
    # The ceiling here is 2,000, which is over _MIN_CREDIBLE_SALARY and under the 90,000
    # annual floor -- so an "ambiguous" that fell back to `annual` REJECTS. That is the
    # discriminating property, and it is the whole point of the row: an earlier draft
    # paired £65/hour with £500/day, whose 500 ceiling sits under the credibility guard,
    # so the annual fallback kept too and the row witnessed nothing.
    cfg = _cfg(titles, contract_floor_gbp_hour=80)
    assert classify(L(titles, role_type="", salary="£65 per hour, £2,000 per week"),
                    cfg)[0] == "keep"


def test_an_unknown_basis_yields_no_opinion_rather_than_an_annual_one(titles):
    # `_pay_reject` must not default an unrecognised basis to the annual floor. That
    # default is EXACTLY how `£2,000 per week` came to be judged as a sub-90,000 salary,
    # and re-introducing it one layer in would restore the defect for the ambiguous case
    # while every single-basis row stayed green.
    from sluice.triage.classify import _pay_reject
    assert _pay_reject("£2,000", "ambiguous", _cfg(titles)) is None
    assert _pay_reject("£2,000", None, _cfg(titles)) is None
    # ...and the annual floor really would have rejected it, so the row discriminates.
    assert _pay_reject("£2,000", "annual", _cfg(titles)) == (
        "reject", "Salary below floor: 2000 < 90000")


def test_an_unmarked_salary_is_still_judged_exactly_as_before(titles):
    # The four-way split must not disturb step 4. A bare amount still falls through to
    # the annual branch, which is the pre-#223 behaviour and deliberately unimproved.
    assert classify(L(titles, role_type="", salary="£45,000"), _cfg(titles))[0] == "reject"
    assert classify(L(titles, role_type="", salary="£120,000"), _cfg(titles))[0] == "keep"


def test_the_new_floors_ship_neutral_so_an_unconfigured_gate_abstains():
    # The empty-config-abstains invariant, on the two knobs this adds. A shipped
    # non-zero floor would silently bin hourly and weekly leads on a fresh install --
    # which is the 672ad2a class this whole change exists to remove, reintroduced.
    neutral = TriageConfig()
    assert neutral.contract_floor_gbp_hour == 0
    assert neutral.contract_floor_gbp_week == 0


def test_a_krona_salary_is_money(titles):
    # #305 defect 1. _MONEY_RE recognised money only by [£$€] or a k suffix, so a Nordic
    # posting -- "SEK 900 000", "900 000 kr" -- parsed as NO money at all. By this module's
    # own rule ("no opinion never rejects") every such posting then sailed past the pay
    # floor whatever it paid. The bug is that the parser cannot SEE the money; abstaining
    # on what it cannot see is correct and stays.
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("SEK 900,000")[1:] == (900000, "SEK")
    # A bare "kr" resolves to DKK, the STRONGEST of the four currencies spelled that
    # way, so a wrong guess over-values and abstains rather than manufacturing a
    # reject -- see the reasoning beside _CURRENCY_MARKERS.
    assert _salary_ceiling("900,000 kr")[1:] == (900000, "DKK")
    assert _salary_ceiling("NOK 1,100,000")[1:] == (1100000, "NOK")
    assert _salary_ceiling("zł 250,000")[1:] == (250000, "PLN")


def test_the_ceiling_reports_the_currency_it_parsed(titles):
    # The existing symbols keep working and now say which currency they were, because a
    # bare number cannot be compared to a floor denominated in something else (#305
    # defect 2). A bare k figure has no currency of its own and reports None, so the
    # caller can apply today's behaviour rather than guess.
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("£60,000")[1:] == (60000, "GBP")
    assert _salary_ceiling("€105,000")[1:] == (105000, "EUR")
    assert _salary_ceiling("$120,000")[1:] == (120000, "USD")
    assert _salary_ceiling("60k")[1:] == (60000, None)
    assert _salary_ceiling("60000") is None


def test_a_foreign_salary_is_converted_before_it_meets_the_floor(titles):
    # #305 defect 2. There was no conversion anywhere, so a floor of 100000 GBP kept a
    # EUR 105,000 role (about GBP 90k) and would have kept any krona figure at all. The
    # floor is denominated in GBP; the advert is not.
    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 100000
    # ~GBP 77k at any plausible rate: under the floor, so reject.
    assert classify(L(titles, salary="€90,000 per annum"), cfg)[0] == "reject"
    # ~GBP 69k: under the floor in every direction.
    assert classify(L(titles, salary="SEK 900,000 per annum"), cfg)[0] == "reject"
    # ~GBP 129k: over the floor, so it must survive.
    assert classify(L(titles, salary="€150,000 per annum"), cfg)[0] != "reject"


def test_an_unconvertible_currency_abstains_rather_than_rejects(titles, monkeypatch):
    # The module's standing rule, extended to money it cannot value: no opinion never
    # rejects. A currency with no rate must not manufacture a reject, because a wrong
    # reject bins a lead the user never sees.
    #
    # The condition is CONSTRUCTED, and it has to be. An earlier cut of this test picked a
    # currency it believed had no rate and asserted the abstain -- but that currency did
    # not parse as MONEY either, so `_salary_ceiling` returned None one step earlier and
    # the test passed without the abstain branch ever running. Deleting the branch left
    # the whole suite green. The parser's alphabet is now derived from the rate table
    # (`fx.known_currencies()`), so no literal string can be in one and out of the other:
    # the only honest way to reach this branch is to take a rate away at runtime, which is
    # also the real-world shape -- a table that has dropped a code the parser still reads.
    from sluice.core import fx
    from sluice.triage.classify import _pay_reject, _salary_ceiling

    monkeypatch.setattr(fx, "_PINNED", {c: r for c, r in fx._PINNED.items() if c != "SEK"})
    monkeypatch.setattr(fx, "_cache", None)
    assert fx.rate("SEK") is None, "the fixture must actually remove the rate"

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 100000
    # Parses as money, names its currency, and cannot be valued -- so no opinion.
    assert _salary_ceiling("SEK 900,000 per annum") is None
    assert _pay_reject("SEK 900,000 per annum", "annual", cfg) is None
    assert classify(L(titles, salary="SEK 900,000 per annum"), cfg)[0] != "reject"


def test_one_unvaluable_figure_abstains_for_the_whole_advert(titles, monkeypatch):
    # The ceiling is the largest of a SET, so a set with an unknown member has no known
    # largest. Skipping the unknown row and taking the max of what is left would compare
    # the floor against a figure that was never the top -- a reject earned by the parser
    # losing a number, which is the direction this module never fails in.
    from sluice.core import fx
    from sluice.triage.classify import _salary_ceiling

    monkeypatch.setattr(fx, "_PINNED", {c: r for c, r in fx._PINNED.items() if c != "SEK"})
    monkeypatch.setattr(fx, "_cache", None)

    # Sterling alone would be the ceiling and would reject against a 100,000 floor; the
    # unvaluable krona figure beside it may well be the real top, so the whole advert
    # abstains rather than being judged on the half that happens to convert.
    assert _salary_ceiling("£60,000 UK / SEK 900,000 SE") is None
    assert _salary_ceiling("£60,000 UK") is not None


def test_an_unmarked_k_figure_keeps_todays_behaviour(titles):
    # A bare "90k" names no currency. Treating it as the floor's own currency is exactly
    # what happened before #305, and changing that silently would move existing verdicts
    # on every UK lead in the vault.
    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 100000
    assert classify(L(titles, salary="90k per annum"), cfg)[0] == "reject"
    assert classify(L(titles, salary="120k per annum"), cfg)[0] != "reject"


def test_an_amount_never_swallows_the_number_beside_it(titles):
    """The space thousands separator must not turn two numbers into one.

    Regression for the widening that introduced it: writing the amount as one permissive
    class, `\\d[\\d,\\s ]*`, let it run across whitespace into whatever came next -- and it
    broke the pay floor in BOTH directions at once. "£45,000 25 days holiday" became
    4,500,025 and cleared a floor it should have failed, while "£120,000\\n2 roles" became
    nothing at all, because `\\s` also matched the newline and `float()` then raised on a
    string no separator-stripping could repair. Either way the floor silently stopped
    applying to ordinary sterling adverts, which is the defect this whole module exists to
    prevent.
    """
    from sluice.triage.classify import _salary_ceiling

    # Whole three-digit groups only, so an adjacent number cannot join the amount...
    assert _salary_ceiling("£45,000 25 days holiday")[1:] == (45000, "GBP")
    assert _salary_ceiling("£38,000 37 hours")[1:] == (38000, "GBP")
    assert _salary_ceiling("£50,000 37.5 hours per week")[1:] == (50000, "GBP")
    assert _salary_ceiling("$120,000 25 days holiday")[1:] == (120000, "USD")
    assert _salary_ceiling("SEK 900 000 12 month contract")[1:] == (900000, "SEK")
    # ...and a newline is not a thousands separator, so the amount before it survives.
    assert _salary_ceiling("£120,000\n2 roles")[1:] == (120000, "GBP")
    assert _salary_ceiling("£45,000\n25 days holiday")[1:] == (45000, "GBP")

    # NOT covered here, and pre-existing rather than introduced by the separator work:
    # "$120,000 401k" reads 401,000 as a bare k-suffixed amount and takes it as the
    # ceiling. `main`'s regex does the same -- the k suffix is its own money context, and
    # nothing distinguishes a US retirement plan from a salary written the same way. It
    # fails permissive (an inflated ceiling clears the floor), which is why it is left
    # alone here rather than fixed in a change about separators.
    #
    # ...while the separators that ARE real still group: plain, non-breaking and narrow
    # no-break spaces all appear in scraped Nordic postings.
    assert _salary_ceiling("SEK 900 000")[1:] == (900000, "SEK")
    assert _salary_ceiling("SEK 900\u00a0000")[1:] == (900000, "SEK")
    assert _salary_ceiling("SEK 900\u202f000")[1:] == (900000, "SEK")
    assert _salary_ceiling("NOK 1 100 000")[1:] == (1100000, "NOK")


def test_a_space_separated_range_reads_its_upper_bound(titles):
    """`_MONEY_RE` and `_RANGE_TAIL_RE` must accept the same number grammar.

    When only the first was widened for the space separator, "€90 000 - 110 000" matched a
    tail of "- 110" and the ceiling stayed at the BOTTOM of the band -- so the advert was
    rejected against a floor its real top cleared. That is precisely the fails-closed harm
    `_RANGE_TAIL_RE` exists to prevent, reintroduced by widening one half of the pair.
    """
    from sluice.triage.classify import _salary_ceiling

    assert _salary_ceiling("€90 000 - 110 000")[1:] == (110000, "EUR")
    assert _salary_ceiling("SEK 900 000 - 1 100 000")[1:] == (1100000, "SEK")
    # ...and the comma spelling of the same advert has always worked; both must agree.
    assert _salary_ceiling("€90,000 - 110,000")[1:] == (110000, "EUR")

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 80000
    assert classify(L(titles, salary="€90 000 - 110 000 per annum"), cfg)[0] != "reject"


def test_the_ceiling_is_the_most_MONEY_not_the_biggest_number(titles):
    """Across currencies the largest number and the largest amount are different rows.

    `max` on the printed figure picked the SEK row here -- the smallest of the three in
    sterling -- and rejected against a floor the advert's real top cleared. The ceiling has
    to be chosen after conversion, or "fail open" is only true within one currency.
    """
    from sluice.triage.classify import _salary_ceiling

    advert = "$180,000 US / £120,000 UK / SEK 1,400,000 SE"
    top = _salary_ceiling(advert)
    assert (top.advertised, top.currency) == (180000, "USD"), (
        "the dollar row is worth the most; SEK 1,400,000 is merely the biggest number")

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 120000
    assert classify(L(titles, salary=advert + " per annum"), cfg)[0] != "reject"


def test_the_parsers_alphabet_is_exactly_what_fx_can_value(titles):
    """Neither list may grow a member the other lacks.

    A code the parser reads but `fx` cannot value parses as money and then abstains, so the
    floor quietly stops applying to that market; a code `fx` can value but the parser does
    not read is not seen as money at all, which is #305 itself. The first shipped cut kept
    a hand-written nine-currency alphabet beside a hand-written nine-rate table, so both
    failures were one edit away and whole markets -- CAD, AUD, CZK -- were invisible.
    """
    from sluice.core import fx
    from sluice.triage.classify import _CURRENCY_MARKERS, _salary_ceiling

    # The ISO half is exactly the self-mapping entries: a code names itself, while every
    # vernacular spelling names some OTHER string ("lei" -> "RON"). Derived that way rather
    # than by shape -- an earlier cut used "three letters and alphabetic", which silently
    # swept in the Romanian spelling once the vernacular half was completed.
    iso = {marker for marker, code in _CURRENCY_MARKERS.items() if marker == code}
    assert iso == set(fx.known_currencies()), (
        "the parser's ISO alphabet and fx's rate table have drifted apart")

    # ...and every vernacular spelling must name a currency that table can actually value,
    # or it parses as money and then abstains -- the floor silently stopping for a market
    # whose notation the parser claims to read.
    unvaluable = {marker: code for marker, code in _CURRENCY_MARKERS.items()
                  if code not in fx.known_currencies()}
    assert not unvaluable, f"markers naming a currency fx cannot value: {unvaluable}"

    # ...and the codes an earlier cut omitted really do parse now.
    for advert, expected in [("CAD 150,000", "CAD"), ("AUD 200,000", "AUD"),
                             ("CZK 2,000,000", "CZK")]:
        assert _salary_ceiling(advert).currency == expected


def test_every_currency_marker_parses_on_both_sides_of_the_amount(titles):
    """Neither branch of `_MONEY_RE` may recognise a marker the other does not.

    A marker accepted in only one position means an advert written the other way round
    carries no money context, so the pay floor silently stops applying to it -- #305 defect
    1, in whichever spelling the omitted branch happened to miss. Five of the markers were
    asymmetric when this was written: `zl` and the three symbols parsed only BEFORE the
    amount, `kr` only after. The euro was the expensive one: plenty of notations put the
    symbol AFTER the amount, as in "45 000 €", so a whole spelling of the commonest foreign
    currency in this table carried no money context at all.

    Swept over `_CURRENCY_MARKERS` rather than over a hand-written list of examples, so a
    marker added later is covered without anyone remembering to add a row here.
    """
    from sluice.triage.classify import _CURRENCY_MARKERS, _salary_ceiling

    assert len(_CURRENCY_MARKERS) > 30, (
        "the sweep must actually enumerate the vocabulary; a shrunken marker table would "
        "make this test pass by checking almost nothing")

    # SYMMETRY per spelling, never "this exact string parses". The ISO codes are matched
    # uppercase on purpose -- a lowercase "usd" or "try" in prose is a word far more often
    # than a currency -- so sweeping the dict keys verbatim would assert the CASE POLICY
    # instead of the property under test, and fail on every lowercase ISO key.
    asymmetric, unreachable = [], []
    for marker, code in sorted(_CURRENCY_MARKERS.items()):
        reachable = False
        for spelling in sorted({marker, marker.upper()}):
            before = _salary_ceiling(f"{spelling} 250 000")
            after = _salary_ceiling(f"250 000 {spelling}")
            ok_before = before is not None and before.currency == code
            ok_after = after is not None and after.currency == code
            if ok_before != ok_after:
                asymmetric.append(
                    f"{spelling!r} -> {code}: before={ok_before} after={ok_after}")
            reachable = reachable or ok_before
        # ...and ANTI-VACUITY: a marker that parses in neither position satisfies symmetry
        # trivially, so it has to be caught separately or the sweep passes on a dead table.
        if not reachable:
            unreachable.append(f"{marker!r} -> {code}")
    assert not asymmetric, "asymmetric markers: " + "; ".join(asymmetric)
    assert not unreachable, "markers that parse in NO position: " + "; ".join(unreachable)


def test_the_spellings_boards_actually_write_are_money(titles):
    # The concrete cases behind the sweep above, named so a failure says which market broke.
    from sluice.triage.classify import _salary_ceiling

    for advert, expected in [
            ("250 000 zl", "PLN"),      # the ASCII spelling, when markup loses diacritics
            ("250 000 zł", "PLN"),
            ("45 000 €", "EUR"),        # notation that puts the symbol after the amount
            ("120,000 $", "USD"),
            ("kr 450 000", "DKK"),      # the same marker, before the amount
            ("450 000 kr", "DKK"),
    ]:
        parsed = _salary_ceiling(advert)
        assert parsed is not None, f"{advert!r} was not recognised as money at all"
        assert parsed.currency == expected, f"{advert!r} -> {parsed.currency}, want {expected}"


def test_a_foreign_reject_message_shows_the_converted_figure_and_the_advertised_one(titles):
    """The message compares GBP to GBP, and says where the GBP came from.

    SURVIVING MUTANT before this test: the `(from N CUR)` disclosure was asserted nowhere,
    so it could be deleted, or `shown` swapped for the ADVERTISED figure -- which prints
    "Salary below floor: 900000 < 100000", a number visibly larger than the floor it is
    said to be below. Every existing message assertion was sterling-only, where the two
    figures are equal and the bug is invisible.
    """
    from sluice.triage.classify import _pay_reject

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 100000
    verdict, reason = _pay_reject("SEK 900,000 per annum", "annual", cfg)
    assert verdict == "reject"
    converted = str(fx_gbp("SEK", 900000))
    assert reason.startswith(f"Salary below floor: {converted} "), (
        f"the message must lead with the CONVERTED figure, got {reason!r}")
    assert reason.endswith("< 100000"), (
        f"the floor comparison must be the last thing said, got {reason!r}")
    assert "(from 900000 SEK)" in reason, (
        f"the message must disclose the advert's own figure and currency, got {reason!r}")
    # The advertised figure must never be the one compared to the floor: 900000 beside
    # "below floor: ... < 100000" reads as a straight comparison and is visibly absurd.
    assert not reason.startswith("Salary below floor: 900000"), (
        "the message is quoting the advertised figure against a GBP floor")

    # ...and a sterling lead keeps the plain form, with no redundant conversion noise.
    plain = _pay_reject("£60,000 per annum", "annual", cfg)[1]
    assert plain == "Salary below floor: 60000 < 100000"


def fx_gbp(currency, amount):
    from sluice.core import fx
    return fx.to_gbp(amount, currency)


def test_the_money_pattern_is_case_sensitive_for_iso_codes(titles):
    """Compiling `_MONEY_RE` with `re.I` must not be a silent option.

    SURVIVING MUTANT before this test: adding `re.I` reddened nothing across the whole
    suite, and it makes ordinary English words into currencies -- `try 250 000` becomes
    Turkish lira, `php 8` becomes Philippine pesos. The sweep beside this one deliberately
    declines to assert the case policy, so nothing else covered it.
    """
    from sluice.triage.classify import _salary_ceiling

    for prose in ("try 250 000 users", "php 8 or later", "usd 90 000"):
        assert _salary_ceiling(prose) is None, (
            f"{prose!r} parsed as money -- the ISO alternation has become case-insensitive")

    # ...while the upper-case spellings those words shadow still work.
    assert _salary_ceiling("TRY 250 000").currency == "TRY"
    assert _salary_ceiling("USD 90 000").currency == "USD"


def test_a_number_before_a_salary_does_not_steal_its_currency_marker(titles):
    """The post-amount branch matches its marker by LOOKAHEAD, so it cannot consume it.

    Consuming it let any number to the left of a salary swallow that salary's only money
    context. Measured against `main` before the fix, at a 50000 floor:

      "Ref 12345 <sym>60,000"  -- main kept; head REJECTED on 12345
      "Grade 7 <sym>50,000"    -- ceiling 7, so the floor stopped applying at all
      "Band 6 <sym>35,392 to 42,618" -- main rejected on 42,618; head abstained

    Reference numbers, posting years, grades and bands all sit in front of a salary in real
    adverts, so this failed in both directions on ordinary sterling postings. Leaving the
    marker in place lets the pre-branch read it too, and `max` then picks the real ceiling.
    """
    from sluice.triage.classify import _pay_reject, _salary_amounts, _salary_ceiling

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 50000

    # Both readings are emitted, and the ceiling is the real salary rather than the stray.
    assert _salary_ceiling("Ref 12345 £60,000 per annum").advertised == 60000
    assert (12345, "GBP") in _salary_amounts("Ref 12345 £60,000 per annum")
    assert _pay_reject("Ref 12345 £60,000 per annum", "annual", cfg) is None
    assert _pay_reject("Posted 2026 £60,000 per annum", "annual", cfg) is None

    # ...and the mirror direction: a small stray must not switch the floor off.
    assert _salary_ceiling("Grade 7 £50,000").advertised == 50000
    assert _pay_reject("Band 6 £35,392 to 42,618", "annual", cfg) == (
        "reject", "Salary below floor: 42618 < 50000")

    # The marker-after-amount case this branch exists for still works.
    assert _salary_ceiling("900 000 kr").advertised == 900000
    assert _salary_ceiling("45 000 €").currency == "EUR"


def test_a_monthly_advert_abstains_rather_than_being_judged_as_annual(titles):
    """A month's pay must never be compared to an annual floor.

    `_BASES` has no monthly row, so before this an advert saying "per month" reached no
    basis, fell through to the ANNUAL branch, and was judged as if the figure were a
    year's pay -- a twelvefold error, always in the reject direction.

    Harmless while the parser could not see the money, and #305 is exactly what made it
    reachable: teaching the parser krona and zloty walked ordinary monthly postings in
    those markets into the reject window. Sterling had the same hole and it closes here too.
    """
    from sluice.triage.classify import _pay_basis, _pay_reject

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 60000

    for advert in ("55 000 SEK per month", "25 000 zl per month", "50 000 kr/month",
                   "£5,000 per month", "£5,000 pcm", "45 000 kr monthly"):
        assert _pay_basis(advert, None, None) == "month", (
            f"{advert!r} was not recognised as monthly")
        assert _pay_reject(advert, _pay_basis(advert, None, None), cfg) is None, (
            f"{advert!r} produced a verdict; a monthly advert has no floor to be judged "
            "against and must abstain")

    # ...and the annual cases either side of it are untouched.
    assert _pay_basis("£60,000 per annum", None, None) == "annual"
    assert _pay_reject("£30,000 per annum", "annual", cfg) == (
        "reject", "Salary below floor: 30000 < 60000")
    assert _pay_reject("900 000 SEK per year", "annual", cfg) is None


def test_every_vernacular_spelling_resolves_to_the_currency_it_names(titles):
    """Swept over the whole vernacular map, not a chosen handful.

    The map was two market families -- the two this feature was built for -- while thirteen
    currencies in the same rate table were unreadable in their own notation. That is the
    argument `fx._PINNED` makes against a hand-picked table, applied one file too late. A
    sweep is what stops it regressing to a handful again; spot-checking four spellings did
    not notice when the other twenty were absent.
    """
    from sluice.triage.classify import _VERNACULAR_MARKERS, _salary_ceiling

    # An EXACT roster, hand-written here and compared against the module's. A count floor
    # is not a completeness check: witnessed, deleting two markers took the map from 27 to
    # 25, stayed above the floor, left the suite green, and silently stopped two markets
    # being money -- the #305 defect itself. An equality forces any change to be deliberate.
    expected = {
        "CA$": "CAD", "A$": "AUD", "S$": "SGD", "HK$": "HKD", "NZ$": "NZD",
        "US$": "USD", "R$": "BRL", "Mex$": "MXN",
        "£": "GBP", "$": "USD", "€": "EUR", "¥": "CNY",
        "₹": "INR", "₩": "KRW", "₺": "TRY", "₪": "ILS", "₱": "PHP", "฿": "THB",
        "kr": "DKK", "zł": "PLN", "zl": "PLN", "Kč": "CZK", "Ft": "HUF",
        "Rp": "IDR", "RM": "MYR", "lei": "RON", "Lei": "RON",
    }
    assert _VERNACULAR_MARKERS == expected, (
        "the vernacular map changed; update this roster deliberately, and check the new "
        "state against the completeness reasoning beside _VERNACULAR_MARKERS")

    for marker, code in sorted(_VERNACULAR_MARKERS.items()):
        for advert in (f"{marker} 250 000", f"250 000 {marker}"):
            parsed = _salary_ceiling(advert)
            assert parsed is not None, f"{advert!r} was not recognised as money at all"
            assert parsed.currency == code, (
                f"{advert!r} -> {parsed.currency}, want {code}")


def test_an_ambiguous_symbol_resolves_to_the_strongest_currency_it_could_mean(titles):
    """The rule, asserted against `fx`'s own table rather than trusted from a comment.

    Several symbols name more than one currency and no regex settles which, so the choice
    is which way to be WRONG. Resolving to the strongest means an unmarked figure is
    OVER-valued, and an over-valued advert clears a floor it may not deserve to -- the
    permissive direction. The reverse pick makes every wrong guess a reject, which is the
    harm this module exists to prevent.
    """
    from sluice.core import fx
    from sluice.triage.classify import _VERNACULAR_MARKERS

    families = {
        "kr": ("DKK", "SEK", "NOK", "ISK"),
        "$": ("USD", "CAD", "AUD", "SGD", "HKD", "NZD", "MXN"),
        "¥": ("CNY", "JPY"),
    }
    for symbol, candidates in families.items():
        chosen = _VERNACULAR_MARKERS[symbol]
        strongest = max(candidates, key=lambda c: fx.rate(c))
        assert chosen == strongest, (
            f"{symbol!r} resolves to {chosen}, but {strongest} is the strongest of "
            f"{candidates} -- resolving to a weaker one makes every wrong guess a REJECT")


def test_a_marker_must_stand_apart_from_the_text_around_it(titles):
    """`_marker_pattern`'s boundaries are the whole reason that function exists.

    SURVIVING MUTANTS before this test: deleting the LEADING `\\b` made "BONUSGBP 60,000"
    and "ACAD 150,000" money; deleting the TRAILING one made "SEK250,000" and "kr450,000"
    money. The suite stayed green both ways, because every existing sweep puts a space
    between the marker and the amount and so never exercises either edge.

    A boundary is derived per marker from its own first and last character, since `\\b`
    beside a SYMBOL asserts the opposite of what is meant. Both halves are pinned here.
    """
    from sluice.triage.classify import _salary_ceiling

    # A marker buried at the end of a longer word is not a marker.
    for prose in ("BONUSGBP 60,000", "ACAD 150,000", "Bakr 450,000", "MYZL 250,000"):
        assert _salary_ceiling(prose) is None, f"{prose!r} parsed as money"

    # ...nor is one running straight into the amount with no separation.
    for prose in ("SEK250,000", "kr450,000", "GBP60,000"):
        assert _salary_ceiling(prose) is None, f"{prose!r} parsed as money"

    # ...while a SYMBOL legitimately abuts its amount, which is why the boundary is derived
    # per marker rather than applied to every arm.
    assert _salary_ceiling("£60,000").advertised == 60000
    assert _salary_ceiling("€105,000").advertised == 105000
    assert _salary_ceiling("SEK 250,000").advertised == 250000


def test_a_range_whose_currency_follows_the_head_still_reads_its_top(titles):
    """The tail lookup must resume AFTER a lookahead-matched marker.

    The post branch matches its marker by lookahead, so the match ENDS on the marker and a
    tail lookup starting at `m.end()` sees "USD - 150,000" -- no leading dash, no match, and
    the band's upper bound silently lost. Measured before the fix, at a 100000 floor:
    "120,000 USD - 150,000" REJECTED on its head at GBP 88,463 while the real top cleared.

    That is the "ceiling stayed at the BOTTOM of the band" harm `_RANGE_TAIL_RE` exists to
    prevent, reintroduced by the fix for marker-stealing -- and invisible to the existing
    range tests, every one of which marks the HEAD.
    """
    from sluice.triage.classify import _pay_reject, _salary_ceiling

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 100000

    assert _salary_ceiling("120,000 USD - 150,000").advertised == 150000
    assert _salary_ceiling("900 000 SEK to 1 100 000").advertised == 1100000
    assert _salary_ceiling("45 000 kr - 55 000").advertised == 55000
    assert _pay_reject("120,000 USD - 150,000 per annum", "annual", cfg) is None, (
        "the top of the band clears the floor; rejecting means the tail was lost")

    # ...and the pre-marked spellings, which never had the bug, are byte-identical.
    assert _salary_ceiling("£30,000-40,000") == (40000, 40000, "GBP")
    assert _salary_ceiling("€90 000 - 110 000").advertised == 110000
    assert _salary_ceiling("£500/day, ref 60000") == (500, 500, "GBP")


def test_an_unmarked_foreign_figure_abstains_instead_of_being_read_as_annual(titles):
    """The basis default is gated on the currency the parser already resolved.

    An unmarked STERLING figure is overwhelmingly an annual salary, and defaulting it to
    annual is the pre-#223 behaviour every existing vault was judged under. That reasoning
    does not carry to an advert this parser may simply have failed to read: monthly quoting
    is ordinary in many markets, the marker saying so is a phrase in that market's language,
    and a phrase list is unbounded. #305 shipped ten non-English spellings, half of them
    transliterations no board writes, and every gap was a twelvefold WRONG REJECT.

    So a non-sterling figure with no recognised basis abstains. The currency stands in for
    the confidence the phrase list cannot provide.
    """
    from sluice.triage.classify import _unmarked_basis

    cfg = _cfg(titles)
    cfg.perm_floor_gbp = 60000

    # Foreign, basis unreadable -> no opinion, whatever language the advert is in.
    for advert in ("45 000 kr per m\u00e5nad", "12 000 z\u0142/miesi\u0105c",
                   "7 000 EUR al mese", "15 000 CZK m\u011bs\u00ed\u010dn\u011b",
                   "45 000 kr", "SEK 45 000"):
        assert _unmarked_basis(advert) is None, f"{advert!r} should abstain"
        assert classify(L(titles, salary=advert), cfg)[0] != "reject"

    # Sterling, unmarked -> annual, exactly as before.
    for advert in ("\u00a330,000", "90k", "\u00a345,000"):
        assert _unmarked_basis(advert) == "annual", f"{advert!r} must keep today's default"
    assert classify(L(titles, salary="\u00a330,000"), cfg)[0] == "reject"

    # Foreign WITH a readable basis is still judged -- the feature still works.
    assert classify(L(titles, salary="SEK 400 000 per year"), cfg)[0] == "reject"
    assert classify(L(titles, salary="SEK 45 000 per month"), cfg)[0] != "reject"


def test_the_month_vocabulary_is_english_only(titles):
    """Deliberate, and the currency gate is what makes it safe.

    An earlier cut hand-listed ten non-English spellings. Half were ASCII transliterations
    no board writes, so the real spellings fell through and were rejected at a twelfth of
    their worth; and the SET was a claim about which markets matter -- same currency and
    figure, the French spelling kept and the Italian one rejected. Deleting all ten left the
    suite green, because every monthly test was in English.

    What replaces it is `_unmarked_basis`: a foreign-language advert is covered by its
    CURRENCY, not by guessing its vocabulary. This pins the decision so the list cannot
    quietly regrow.
    """
    from sluice.triage.classify import _MONTH_MARKERS

    non_ascii = [m for m in _MONTH_MARKERS if not m.isascii()]
    assert not non_ascii, f"non-English month markers have returned: {non_ascii}"
    assert set(_MONTH_MARKERS) == {
        "/month", "/mo", "per month", "monthly", "a month", "per calendar month",
        "pcm", "p/m", "per mth", "/mth"}


# #311: digit grouping read from PLACEMENT, not from an assumed en-GB locale.
#
# Rows are (text, expected pounds-equivalent-of-the-printed-figure). They assert the
# ADVERTISED number, never the converted one, so these stay true whatever the rate table
# says -- conversion is #305's job and has its own tests.
_GROUPING_CASES = [
    # en-GB: comma groups, dot is the decimal mark.
    ("£30,000", 30000),
    ("£1,100,000", 1100000),
    ("£1.50", 1),                  # one-fifty: a dot with two digits after is a decimal
    ("£30,000.50", 30000),
    # Continental: dot groups, comma is the decimal mark. Every one of these read as a
    # two-digit number before #311, fell below `_MIN_CREDIBLE_SALARY`, and so abstained --
    # the pay floor silently never fired for these markets.
    ("€60.000", 60000),
    ("€1.100.000", 1100000),
    # Spaced, because an ALPHABETIC marker jammed against digits carries no money context
    # here and never did -- `PLN45.000` reads as nothing on `main` too. That is marker
    # adjacency, a separate axis from grouping, so this row uses the spelling #311 itself
    # quotes rather than inventing one that would smuggle in a second change.
    ("45.000 zł", 45000),
    ("€1.500", 1500),              # fifteen hundred, not one-point-five
    ("€60,5k", 60500),             # comma decimal, then the k multiplier
    # Space grouping, which no locale uses as a decimal mark.
    ("SEK 900 000", 900000),
    ("SEK 1 100 000", 1100000),
    # Both separators present: the LAST one is the decimal mark, whichever it is.
    ("€1.100.000,50", 1100000),
    ("£1,100,000.50", 1100000),
]


@pytest.mark.parametrize(("text", "expected"), _GROUPING_CASES)
def test_digit_grouping_is_read_from_placement_not_from_a_locale(text, expected):
    """#311: `60.000` is sixty thousand, and `1.50` is still one-fifty.

    You never know an advert's locale, and roughly half of Europe and Latin America
    inverts the en-GB convention. Placement settles it without one: a space is always
    grouping, the last of two different separators is the decimal mark, a repeated
    separator groups, and a lone separator followed by exactly three digits groups --
    because no salary is written to three decimal places.

    Fails against the en-GB parser this replaces, which read every continental row as a
    two-digit number, put it under `_MIN_CREDIBLE_SALARY`, and abstained -- so the pay
    floor never fired on those adverts at all.
    """
    from sluice.triage.classify import _salary_ceiling

    got = _salary_ceiling(text)
    assert got is not None, f"{text!r} carried no money context at all"
    assert got.advertised == expected, (
        f"{text!r} parsed as {got.advertised}, expected {expected}")

