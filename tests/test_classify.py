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
    assert _salary_ceiling("£2.01k") == 2010
    assert _salary_ceiling("£4.02k") == 4020
    assert _salary_ceiling("£1.5k/day") == 1500

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
    assert _salary_ceiling("£60,000") == 60000
    assert _salary_ceiling("$120,000") == 120000
    assert _salary_ceiling("60k") == 60000          # the suffix IS the context
    assert _salary_ceiling("60000") is None         # bare digits are not money


def test_a_single_symbol_range_reads_its_upper_bound(titles):
    # Boards routinely write "£30,000-40,000" with ONE symbol. The upper bound then carries no
    # money context of its own, so the ceiling read as 30,000 and a £35k floor REJECTED a role
    # paying up to £40k -- fails-closed, the expensive direction. A bare number IS money when it
    # is the tail of a range whose head was money.
    from sluice.triage.classify import _salary_ceiling
    assert _salary_ceiling("£30,000-40,000") == 40000
    assert _salary_ceiling("£60k-80k") == 80000
    assert _salary_ceiling("£60k to £80k") == 80000

    # ...but a stray number that is NOT a range tail is still not money.
    assert _salary_ceiling("£500/day, ref 60000") == 500

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
