from types import SimpleNamespace
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
    lead = [_lead("Example - Analyst", "https://example.com/careers/1")]
    # name-only mention from an unrelated service
    assert match_receipt(_msg(frm="digest@indeed.invalid", body="jobs at Example"), lead, ATS).tier == "none"
    for host in ("evilexample.com", "example.com.attacker.invalid", "notexample.com"):
        assert match_receipt(_msg(frm=f"x@{host}"), lead, ATS).tier == "none", host
    # sibling subdomain of a DIFFERENT registrable domain
    assert match_receipt(_msg(frm="x@careers.other.invalid"), lead, ATS).tier == "none"


def test_multipart_tld_does_not_collapse():
    # bigco.co.uk and random.co.uk must NOT match on a shared co.uk suffix.
    leads = [_lead("Bigco - Analyst", "https://bigco.co.uk/careers/1")]
    assert match_receipt(_msg(frm="noreply@random.co.uk"), leads, ATS).tier == "none"


def test_url_less_lead_never_matches():
    leads = [_lead("Example - Analyst", "")]
    assert match_receipt(_msg(frm="jobs@example.com"), leads, ATS).tier == "none"
