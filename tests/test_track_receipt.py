from types import SimpleNamespace
from sluice.track.config import TrackConfig
from sluice.track.receipt import match_receipt, ReceiptMatch

ATS = {"greenhouse.io": "greenhouse", "lever.co": "lever"}


def _lead(slug, url, company="Example"):
    return SimpleNamespace(slug=slug, fm={"url": url, "company": company})


def _msg(frm="", subject="", body=""):
    return {"headers": {"from": frm, "subject": subject}, "body_text": body}


def test_proof_exact_host_single_lead():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="jobs@example.com", subject="Thanks for applying"), leads, ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_proof_subdomain_of_lead_host():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="no-reply@careers.example.com"), leads, ATS)
    assert m.tier == "proof" and m.lead_slug == "Example - Analyst"


def test_proof_via_apply_link_in_body():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="mailer@sendgrid.invalid",
                           body="View your application at https://example.com/status"), leads, ATS)
    assert m.tier == "proof"


def test_ambiguous_two_leads_same_host_proposes_neither():
    leads = [_lead("Example - Analyst", "https://example.com/a"),
             _lead("Example - Manager", "https://example.com/b")]
    m = match_receipt(_msg(frm="jobs@example.com"), leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Example - Manager"]


def test_corroborated_ats_plus_company_in_body():
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io",
                           body="Example has received your application."), leads, ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_ats_without_company_in_body_no_match():
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io", body="Your application was received."), leads, ATS)
    assert m == ReceiptMatch(None, "none", [])


def test_none_traps():
    # example.invalid (not example.com) here so the dot-less-suffix traps below
    # (notexample.invalid / evilexample.invalid) share its base and TLD -- that is
    # what makes them a substring trap AGAINST this lead, rather than an unrelated
    # domain that would trivially return "none" regardless of any bug.
    lead = [_lead("Example - Analyst", "https://example.invalid/careers/1")]
    # name-only mention from an unrelated service
    assert match_receipt(_msg(frm="digest@indeed.invalid", body="jobs at Example"), lead, ATS).tier == "none"
    for host in ("evilexample.invalid", "example.com.attacker.invalid", "notexample.invalid"):
        # each ENDS WITH the literal string "example.invalid" but WITHOUT a preceding
        # dot -- a naive `host.endswith(target)` (missing the dot separator) would
        # wrongly treat these as subdomains; `_hosts_match` requires the dot.
        assert match_receipt(_msg(frm=f"x@{host}"), lead, ATS).tier == "none", host
    # sibling subdomain of a DIFFERENT registrable domain
    assert match_receipt(_msg(frm="x@careers.other.invalid"), lead, ATS).tier == "none"


def test_multipart_tld_does_not_collapse():
    # alpha.example.com and beta.example.com share nothing but a two-label suffix
    # (example.com) -- a naive "last two labels = registrable domain" reduction
    # would collapse BOTH to example.com and wrongly treat them as the same site.
    # A multi-part real-TLD pair makes the same point but risks naming a domain
    # that happens to be genuinely registered; staying inside the RFC-reserved
    # example.com family keeps the trap with no such risk.
    leads = [_lead("Alpha - Analyst", "https://alpha.example.com/careers/1")]
    assert match_receipt(_msg(frm="noreply@beta.example.com"), leads, ATS).tier == "none"


def test_url_less_lead_never_matches():
    leads = [_lead("Example - Analyst", "")]
    assert match_receipt(_msg(frm="jobs@example.com"), leads, ATS).tier == "none"


def test_cross_tier_ambiguity_proof_plus_different_corrob_lead_refuses():
    # An ATS receipt commonly links the company's own site in its body (a "view
    # your application" footer) -- ordinary traffic, not an attack. Here the
    # receipt is FROM greenhouse (where lead B is hosted) and its body both names
    # "Example" (B's company) AND links example.com (lead A's own site). Before
    # the fix-round-1 patch this silently returned lead A at tier "proof" --
    # the wrong lead, because B was excluded from `proof` by the ATS rule and
    # then discarded once a single proof winner existed. It must refuse instead.
    leads = [_lead("Example - Analyst", "https://example.com/a", company="Example"),
             _lead("Example - Manager", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io",
                           body="Example has received your application. Visit us at https://example.com"),
                       leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Example - Manager"]


def test_proof_survives_when_corrob_is_the_same_lead():
    # The cross-tier refusal must not fire when the ONLY corroborated candidate
    # IS the proof winner -- that is corroborating evidence for the same lead,
    # not a competing one, and the clean proof advance must still happen.
    leads = [_lead("Example - Analyst", "https://example.com/a", company="Example")]
    m = match_receipt(_msg(frm="jobs@example.com", body="Example has received your application."),
                       leads, ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_malformed_bracket_url_in_body_does_not_crash():
    # `_URL_RE` deliberately admits "[" (a permissive scrape); a malformed
    # IPv6-literal-looking fragment must not raise out of urlparse -- body text
    # arrives off the internet, so this is reachable, not hypothetical.
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="mailer@sendgrid.invalid", body="apply at https://[abc"), leads, ATS)
    assert m.tier == "none"


def test_malformed_lead_url_does_not_crash():
    # The same crash is reachable from a malformed fm["url"], not just body text.
    leads = [_lead("Example - Analyst", "https://[abc")]
    m = match_receipt(_msg(frm="jobs@example.com"), leads, ATS)
    assert m.tier == "none"


def test_sender_display_name_spoof_does_not_match():
    # RFC 5322 allows arbitrary display-name text before the angle-bracket
    # address. A naive scan for the first "user@host" in the raw header would
    # read the SENDER-CONTROLLED display name as the real address; the genuine
    # envelope address (inside <...>) is a different, unrelated domain.
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm='"jobs@example.com" <x@evilexample.invalid>'), leads, ATS)
    assert m.tier == "none"


def test_unicode_confusable_host_not_folded_to_match():
    # 'banko.example' (plain ASCII) vs a FROM header spelled with U+212A KELVIN
    # SIGN in place of the ascii 'k' -- str.lower() folds U+212A to ascii 'k',
    # so lowering BEFORE checking for non-ASCII input would make these look
    # like the SAME host. They are not: reject the non-ASCII header instead.
    # Built from an explicit codepoint escape, never a typed glyph, so the
    # exact character under test is unambiguous in the source.
    confusable_host = "ba" + "\u212a" + "nko.example"
    leads = [_lead("Example - Analyst", "https://banko.example/careers/1")]
    m = match_receipt(_msg(frm=f"jobs@{confusable_host}"), leads, ATS)
    assert m.tier == "none"


def test_is_ats_requires_dot_separated_suffix_not_substring():
    # A real ATS domain name can appear as a bare SUBSTRING of a spoofed sender
    # host without that host actually being an ATS relay -- "greenhouse.io" sits
    # inside "fake-greenhouse.io.evil.invalid", but the actual suffix is
    # ".evil.invalid". `_is_ats` must require a dot-separated suffix (or exact
    # equality), never a plain substring test, or a lookalike sender would win
    # ATS-relay status and unlock the corroborated-tier company-name check.
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="noreply@fake-greenhouse.io.evil.invalid",
                           body="Example has received your application."), leads, ATS)
    assert m.tier == "none"


def test_empty_company_never_corroborates():
    # A lead with no company recorded must not corroborate-match EVERY ATS
    # receipt: _norm_tokens("") is the empty set, and an empty set is a SUBSET
    # of any token set, so the guard requiring a non-empty company is load
    # bearing -- without it, an empty company would vacuously satisfy the
    # `company <= tokens` check regardless of what the receipt actually says.
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io", body="Nothing relevant here."), leads, ATS)
    assert m.tier == "none"


def test_shipped_default_ats_domains_withhold_proof():
    # Finding 3 (whole-branch review): every test above uses the module-local ATS
    # constant, never the ACTUAL shipped default (TrackConfig().ats_relay_domains).
    # Emptying that default makes the matcher MORE permissive -- the same message +
    # lead flips corroborated -> proof, because the ATS exclusion is what withholds
    # proof status -- which is correct and documented in sluice.yaml.example, but
    # nothing pinned it: if someone "fixed" the default to empty for consistency with
    # the empty-config-abstains rule, nothing would redden. Use the real default.
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io", body="Example has received your application."),
                       leads, TrackConfig().ats_relay_domains)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_ambiguous_corroboration_two_leads_same_ats_sender_proposes_neither():
    # Refuse-on-ambiguity must hold at the corroborated tier too, not only at
    # proof: two DIFFERENT leads, both hosted on the same ATS relay, both named
    # in the same receipt body, must resolve to no single lead.
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example"),
             _lead("Widget - Engineer", "https://boards.greenhouse.io/widget/jobs/2", company="Widget")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io",
                           body="Example and Widget both received applications today."), leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Widget - Engineer"]
