import pathlib
import re
from types import SimpleNamespace
from sluice.track.config import TrackConfig
from sluice.track.receipt import match_receipt, ReceiptMatch, _suffix_match

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


def test_apply_link_in_body_never_grants_proof():
    # Body links are SENDER-CONTROLLED footer content: "view your application at
    # <company>" proves nothing about who sent the mail, and admitting body hosts let
    # ANY sender hand itself proof of any lead whose site it chose to link. Proof is now
    # the sender host alone, so this resolves to `none` -- the sender is neither the
    # lead's own host nor an ATS relay, so there is nothing to corroborate either.
    # (Renamed from test_proof_via_apply_link_in_body, which asserted the old behaviour.)
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="mailer@mailer.invalid",
                           body="View your application at https://example.com/status"), leads, ATS)
    assert m.tier == "none"


def test_body_link_to_ats_does_not_unlock_corroboration():
    # The same rule at the corroborated tier: `from_ats` keys off the SENDER only.
    # Computing it over body hosts too meant any sender could unlock the corroborated
    # tier -- and with it a runnable `--to applied` proposal -- for any lead it named,
    # just by linking an ATS in its footer.
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1",
                   company="Example")]
    m = match_receipt(_msg(frm="mailer@mailer.invalid",
                           body="Example has received your application. "
                                "Track it at https://boards.greenhouse.io/example"), leads, ATS)
    assert m.tier == "none"


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


def test_ats_receipt_linking_another_leads_site_refuses():
    # An ATS receipt commonly links a company's own site in its body (a "view your
    # application" footer) -- ordinary traffic, not an attack. Here the receipt is FROM
    # greenhouse (where lead B is hosted) and its body both names "Example" (shared by
    # both leads) AND links example.com (lead A's own site). It must refuse.
    # This was originally the CROSS-TIER ambiguity case (proof on A via the body link,
    # corroboration on B); now that body links grant nothing and both tiers key off the
    # sender, proof and corroboration are disjoint by construction and it resolves as
    # ordinary ambiguous corroboration. The refusal is what matters, so the scenario is
    # kept -- under its new name -- rather than deleted along with the code path.
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


def test_malformed_lead_url_does_not_crash():
    # A malformed IPv6-literal-looking fm["url"] must not raise ValueError out of
    # urlparse: lead URLs are scraped off the internet, so this is reachable, not
    # hypothetical. This is now the ONLY route into `_host`'s ValueError guard -- the
    # companion test that fed the same fragment through body text went with the body-
    # link scrape itself, and a test whose subject no longer exists cannot fail.
    leads = [_lead("Example - Analyst", "https://[abc")]
    m = match_receipt(_msg(frm="jobs@example.com"), leads, ATS)
    assert m.tier == "none"


def test_none_valued_headers_and_body_do_not_crash():
    # A present-but-None Subject raised TypeError on the concatenation and a None
    # headers dict raised AttributeError -- both out of this PURE module. engine.run's
    # per-message `except` swallows the raise WITHOUT adding the id to `seen`, so the
    # message re-fails on every future run: a silent poison message, not a one-off
    # failure. A `.get(key, "")` default does not cover this; only `or ""` does.
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    assert match_receipt({"headers": {"from": None, "subject": None}, "body_text": None},
                         leads, ATS).tier == "none"
    assert match_receipt({"headers": None, "body_text": None}, leads, ATS).tier == "none"
    # the lead side has the same shape: a note whose url/company keys are present-but-None
    none_lead = [SimpleNamespace(slug="Example - Analyst", fm={"url": None, "company": None})]
    assert match_receipt(_msg(frm="jobs@example.com"), none_lead, ATS).tier == "none"


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
    # NB the escape must land where the ascii 'k' is: "ba\u212anko.example" folds to
    # "baknko.example", an unrelated host that mismatches for a reason having nothing to
    # do with the fold, which made the assertion unfalsifiable (found pre-push).
    confusable_host = "ban" + "\u212a" + "o.example"
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


def test_board_sourced_lead_is_never_proof_matched():
    # THE Critical this branch shipped with, reproduced on SHIPPED DEFAULTS with no user
    # config. sluice scrapes job BOARDS, so a lead's `url` is the URL it was INGESTED
    # from -- for most leads a multi-tenant aggregator, never the employer. An unrelated
    # sender whose body merely linked that board used to proof-match the lead and
    # AUTO-ADVANCE a job the user had never applied to, which silently suppresses the
    # real application and is effectively irreversible.
    # (linkedin.com is a shipped ingest source id -- sluice/ingest/sources/linkedin.py --
    # so naming it here reveals nothing about any user's job hunt.)
    cfg = TrackConfig()
    leads = [_lead("Alpha - Analyst", "https://www.linkedin.com/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm="careers@unrelated-employer.invalid",
                           body="We got your application. Follow us: "
                                "https://www.linkedin.com/company/gamma"),
                      leads, cfg.ats_relay_domains, cfg.job_board_domains)
    assert m.lead_slug is None and m.tier == "none"


def test_board_sender_is_never_proof_of_a_specific_employer():
    # The other half of the Critical: the BOARD itself sends the receipt (about some
    # other company entirely). Sender host and lead host are then genuinely equal, so
    # `_hosts_match` cannot tell them apart and only the multi-tenant exclusion
    # withholds proof -- this is the case that reddens when it is removed.
    cfg = TrackConfig()
    leads = [_lead("Alpha - Analyst", "https://www.linkedin.com/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm="jobs-noreply@linkedin.com", subject="Your application"),
                      leads, cfg.ats_relay_domains, cfg.job_board_domains)
    assert m.lead_slug is None and m.tier == "none"


def test_board_sourced_lead_still_corroborates_from_an_ats():
    # The accepted cost of the fix, pinned so it is a decision and not a surprise: a
    # board-sourced lead can no longer reach `proof`, so it DEGRADES to corroboration
    # (or to a proposal) rather than disappearing. Same lead, but a real ATS relay
    # sender naming the company -> corroborated, which reconcile proposes.
    cfg = TrackConfig()
    leads = [_lead("Alpha - Analyst", "https://www.linkedin.com/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io", body="Alpha received your application."),
                      leads, cfg.ats_relay_domains, cfg.job_board_domains)
    assert m.tier == "corroborated" and m.lead_slug == "Alpha - Analyst"


def test_lead_side_multi_tenant_check_is_not_redundant():
    # `_is_multi_tenant` is consulted on BOTH sides. The lead side looks redundant while
    # every configured key is a REGISTRABLE domain (a sender matching a shared lead host
    # is then shared itself), but a user may add a NON-registrable board host -- and the
    # board's parent domain then reads as a non-shared sender while `_hosts_match`'s
    # bidirectional subdomain rule still pairs the two.
    boards = {"jobs.board.invalid": "example-board"}
    leads = [_lead("Alpha - Analyst", "https://jobs.board.invalid/1", company="Alpha")]
    assert match_receipt(_msg(frm="noreply@board.invalid"), leads, ATS, boards).tier == "none"


def test_sender_side_multi_tenant_check_is_not_redundant():
    # The mirror, and the reason BOTH clauses have to be there: deleting the sender-side
    # one left the whole suite green, because every other board case is also caught by
    # the lead side. With a non-registrable board key the two come apart the other way --
    # "x.jobs.board.invalid" is a subdomain of the lead's "board.invalid", so
    # `_hosts_match` fires while only the SENDER is multi-tenant. Neither clause
    # subsumes the other.
    boards = {"jobs.board.invalid": "example-board"}
    leads = [_lead("Alpha - Analyst", "https://board.invalid/1", company="Alpha")]
    m = match_receipt(_msg(frm="noreply@x.jobs.board.invalid"), leads, ATS, boards)
    assert m.tier == "none"


def test_job_board_defaults_cover_every_shipped_source_host():
    # The hand-list trap: the shipped denylist was written by reading the source plugins
    # once, and a new board plugin added without a matching entry silently reopens the
    # Critical for that board. Enumerate the registry instead of trusting the list --
    # statically, by scraping URL literals out of the plugin sources rather than
    # importing them, since importing an ingest source pulls in the browser stack the
    # offline suite must never touch.
    boards = TrackConfig().job_board_domains
    src = pathlib.Path(__file__).resolve().parent.parent / "sluice" / "ingest" / "sources"
    hosts = set()
    for p in sorted(src.glob("*.py")):
        for u in re.findall(r"https?://([A-Za-z0-9.-]+)", p.read_text(encoding="utf-8")):
            u = u.lower()
            hosts.add(u[4:] if u.startswith("www.") else u)
    assert hosts, "no source hosts discovered -- the sweep itself is broken"
    assert sorted(h for h in hosts if not _suffix_match(h, boards)) == []


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
