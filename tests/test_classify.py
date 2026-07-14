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
    assert classify(L(titles, location="Bangalore, India"), cfg)[0] == "reject"


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
    for loc in ("London", "Berlin", "Remote", "Anywhere at all", ""):
        lead = L(titles, location=loc)
        assert classify(lead, cfg)[0] == "keep", f"unconfigured gate rejected {loc!r}"


def test_configured_geography_gate_still_filters(titles):
    cfg = _cfg(titles)
    cfg.target_locations = ["london"]
    assert classify(L(titles, location="London"), cfg)[0] == "keep"
    assert classify(L(titles, location="Berlin"), cfg)[0] == "reject"


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
