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
