import pathlib
import re
from types import SimpleNamespace
from sluice.track.config import TrackConfig
from sluice.track.receipt import match_receipt, ReceiptMatch, _suffix_match

# Reserved placeholder hosts, passed in as the denylists under test: every test below
# exercises the matching ALGORITHM and has no reason to name a real vendor. The tests
# that genuinely verify the SHIPPED denylists derive their hosts from TrackConfig().
ATS = {"ats.example.invalid": "example-ats", "relay.example.invalid": "example-relay"}


def _lead(slug, url, company="Example"):
    return SimpleNamespace(slug=slug, fm={"url": url, "company": company})


def _dkim_pass(domain):
    """What a delivering server writes for a genuine, aligned DKIM signature -- including
    the parenthesised comment and the extra properties Gmail really emits, so the parser
    is exercised against the shape it will actually meet."""
    return (f"mx.example.invalid (mx.example.invalid); "
            f"dkim=pass header.i=@{domain} header.s=sel header.b=Zm9vYmFy")


def _msg(frm="", subject="", body="", auth=None):
    # `auth` is the RFC 8601 Authentication-Results header. Defaults to ABSENT, so every
    # test that expects a proof-tier match has to say out loud that the sender domain was
    # authenticated -- and the ones that expect a refusal keep an aligned pass, so the
    # conjunct under test is the only thing standing between them and a match.
    headers = {"from": frm, "subject": subject}
    if auth is not None:
        headers["authentication-results"] = auth
    return {"headers": headers, "body_text": body}


def test_proof_exact_host_single_lead():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="jobs@example.com", subject="Thanks for applying",
                           auth=_dkim_pass("example.com")), leads, ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_proof_subdomain_of_lead_host():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="no-reply@careers.example.com",
                           auth=_dkim_pass("example.com")), leads, ATS)
    assert m.tier == "proof" and m.lead_slug == "Example - Analyst"


def test_apply_link_in_body_never_grants_proof():
    # Body links are SENDER-CONTROLLED footer content: "view your application at
    # <company>" proves nothing about who sent the mail, and admitting body hosts let
    # ANY sender hand itself proof of any lead whose site it chose to link. Proof is now
    # the sender host alone, so this resolves to `none` -- the sender is neither the
    # lead's own host nor an ATS relay, so there is nothing to corroborate either.
    # (Renamed from test_proof_via_apply_link_in_body, which asserted the old behaviour.)
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="mailer@mailer.invalid", auth=_dkim_pass("mailer.invalid"),
                           body="View your application at https://example.com/status"), leads, ATS)
    assert m.tier == "none"


def test_body_link_to_ats_does_not_unlock_corroboration():
    # The same rule at the corroborated tier: `from_ats` keys off the SENDER only.
    # Computing it over body hosts too meant any sender could unlock the corroborated
    # tier -- and with it a runnable `--to applied` proposal -- for any lead it named,
    # just by linking an ATS in its footer.
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1",
                   company="Example")]
    m = match_receipt(_msg(frm="mailer@mailer.invalid", auth=_dkim_pass("mailer.invalid"),
                           body="Example has received your application. "
                                "Track it at https://boards.ats.example.invalid/example"), leads, ATS)
    assert m.tier == "none"


def test_ambiguous_two_leads_same_host_proposes_neither():
    leads = [_lead("Example - Analyst", "https://example.com/a"),
             _lead("Example - Manager", "https://example.com/b")]
    m = match_receipt(_msg(frm="jobs@example.com", auth=_dkim_pass("example.com")), leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Example - Manager"]


def test_corroborated_ats_plus_company_in_body():
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                           body="Example has received your application."), leads, ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_ats_without_company_in_body_no_match():
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                              body="Your application was received."), leads, ATS)
    assert m == ReceiptMatch(None, "none", [])


def test_none_traps():
    # example.invalid (not example.com) here so the dot-less-suffix traps below
    # (notexample.invalid / evilexample.invalid) share its base and TLD -- that is
    # what makes them a substring trap AGAINST this lead, rather than an unrelated
    # domain that would trivially return "none" regardless of any bug.
    lead = [_lead("Example - Analyst", "https://example.invalid/careers/1")]
    # name-only mention from an unrelated service
    assert match_receipt(_msg(frm="digest@aggregator.invalid", auth=_dkim_pass("aggregator.invalid"),
                                 body="jobs at Example"), lead, ATS).tier == "none"
    for host in ("evilexample.invalid", "example.com.attacker.invalid", "notexample.invalid"):
        # each ENDS WITH the literal string "example.invalid" but WITHOUT a preceding
        # dot -- a naive `host.endswith(target)` (missing the dot separator) would
        # wrongly treat these as subdomains; `_hosts_match` requires the dot.
        assert match_receipt(_msg(frm=f"x@{host}", auth=_dkim_pass(host)), lead, ATS).tier == "none", host
    # sibling subdomain of a DIFFERENT registrable domain
    assert match_receipt(_msg(frm="x@careers.other.invalid", auth=_dkim_pass("other.invalid")),
                         lead, ATS).tier == "none"


def test_multipart_tld_does_not_collapse():
    # alpha.example.com and beta.example.com share nothing but a two-label suffix
    # (example.com) -- a naive "last two labels = registrable domain" reduction
    # would collapse BOTH to example.com and wrongly treat them as the same site.
    # A multi-part real-TLD pair makes the same point but risks naming a domain
    # that happens to be genuinely registered; staying inside the RFC-reserved
    # example.com family keeps the trap with no such risk.
    leads = [_lead("Alpha - Analyst", "https://alpha.example.com/careers/1")]
    assert match_receipt(_msg(frm="noreply@beta.example.com",
                                 auth=_dkim_pass("beta.example.com")), leads, ATS).tier == "none"


def test_url_less_lead_never_matches():
    leads = [_lead("Example - Analyst", "")]
    assert match_receipt(_msg(frm="jobs@example.com", auth=_dkim_pass("example.com")),
                         leads, ATS).tier == "none"


def test_ats_receipt_linking_another_leads_site_refuses():
    # An ATS receipt commonly links a company's own site in its body (a "view your
    # application" footer) -- ordinary traffic, not an attack. Here the receipt is FROM
    # the ATS relay (where lead B is hosted) and its body both names "Example" (shared by
    # both leads) AND links example.com (lead A's own site). It must refuse.
    # This was originally the CROSS-TIER ambiguity case (proof on A via the body link,
    # corroboration on B); now that body links grant nothing and both tiers key off the
    # sender, proof and corroboration are disjoint by construction and it resolves as
    # ordinary ambiguous corroboration. The refusal is what matters, so the scenario is
    # kept -- under its new name -- rather than deleted along with the code path.
    leads = [_lead("Example - Analyst", "https://example.com/a", company="Example"),
             _lead("Example - Manager", "https://boards.ats.example.invalid/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                           body="Example has received your application. Visit us at https://example.com"),
                       leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Example - Manager"]


def test_proof_survives_when_corrob_is_the_same_lead():
    # The cross-tier refusal must not fire when the ONLY corroborated candidate
    # IS the proof winner -- that is corroborating evidence for the same lead,
    # not a competing one, and the clean proof advance must still happen.
    leads = [_lead("Example - Analyst", "https://example.com/a", company="Example")]
    m = match_receipt(_msg(frm="jobs@example.com", auth=_dkim_pass("example.com"),
                           body="Example has received your application."),
                       leads, ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_malformed_lead_url_does_not_crash():
    # A malformed IPv6-literal-looking fm["url"] must not raise ValueError out of
    # urlparse: lead URLs are scraped off the internet, so this is reachable, not
    # hypothetical. This is now the ONLY route into `_host`'s ValueError guard -- the
    # companion test that fed the same fragment through body text went with the body-
    # link scrape itself, and a test whose subject no longer exists cannot fail.
    leads = [_lead("Example - Analyst", "https://[abc")]
    m = match_receipt(_msg(frm="jobs@example.com", auth=_dkim_pass("example.com")), leads, ATS)
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
    m = match_receipt(_msg(frm='"jobs@example.com" <x@evilexample.invalid>',
                           auth=_dkim_pass("evilexample.invalid")), leads, ATS)
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
    # host without that host actually being an ATS relay -- "ats.example.invalid" sits
    # inside "fake-ats.example.invalid.evil.invalid", but the actual suffix is
    # ".evil.invalid". `_is_ats` must require a dot-separated suffix (or exact
    # equality), never a plain substring test, or a lookalike sender would win
    # ATS-relay status and unlock the corroborated-tier company-name check.
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="noreply@fake-ats.example.invalid.evil.invalid",
                           auth=_dkim_pass("fake-ats.example.invalid.evil.invalid"),
                           body="Example has received your application."), leads, ATS)
    assert m.tier == "none"


def test_empty_company_never_corroborates():
    # A lead with no company recorded must not corroborate-match EVERY ATS
    # receipt: _norm_tokens("") is the empty set, and an empty set is a SUBSET
    # of any token set, so the guard requiring a non-empty company is load
    # bearing -- without it, an empty company would vacuously satisfy the
    # `company <= tokens` check regardless of what the receipt actually says.
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1", company="")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                           body="Nothing relevant here."), leads, ATS)
    assert m.tier == "none"


def _shipped(denylist):
    """One host DERIVED from a shipped denylist. The four tests below are about the
    SHIPPED defaults -- that they are non-empty and that a host in them cannot prove a
    specific employer -- and not about any particular vendor, so typing a brand in would
    add a real name to a fixture without adding coverage. sorted()[0] is deterministic,
    and the empty assert makes an emptied default fail HERE rather than silently turning
    every case below into a vacuous pass."""
    assert denylist, "a shipped safety denylist must be non-empty"
    return sorted(denylist)[0]


def test_shipped_default_ats_domains_withhold_proof():
    # Finding 3 (whole-branch review): every test above uses the module-local ATS
    # constant, never the ACTUAL shipped default (TrackConfig().ats_relay_domains).
    # Emptying that default makes the matcher MORE permissive -- the same message +
    # lead flips corroborated -> proof, because the ATS exclusion is what withholds
    # proof status -- which is correct and documented in sluice.yaml.example, but
    # nothing pinned it: if someone "fixed" the default to empty for consistency with
    # the empty-config-abstains rule, nothing would redden. Use the real default.
    ats = TrackConfig().ats_relay_domains
    host = _shipped(ats)
    leads = [_lead("Example - Analyst", f"https://boards.{host}/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm=f"no-reply@{host}", auth=_dkim_pass(host),
                            body="Example has received your application."),
                       leads, ats)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_board_sourced_lead_is_never_proof_matched():
    # THE Critical this branch shipped with, reproduced on SHIPPED DEFAULTS with no user
    # config. sluice scrapes job BOARDS, so a lead's `url` is the URL it was INGESTED
    # from -- for most leads a multi-tenant aggregator, never the employer. An unrelated
    # sender whose body merely linked that board used to proof-match the lead and
    # AUTO-ADVANCE a job the user had never applied to, which silently suppresses the
    # real application and is effectively irreversible.
    cfg = TrackConfig()
    board = _shipped(cfg.job_board_domains)
    leads = [_lead("Alpha - Analyst", f"https://www.{board}/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm="careers@unrelated-employer.invalid",
                           auth=_dkim_pass("unrelated-employer.invalid"),
                           body=f"We got your application. Follow us: https://www.{board}/company/gamma"),
                      leads, cfg.ats_relay_domains, cfg.job_board_domains)
    assert m.lead_slug is None and m.tier == "none"


def test_board_sender_is_never_proof_of_a_specific_employer():
    # The other half of the Critical: the BOARD itself sends the receipt (about some
    # other company entirely). Sender host and lead host are then genuinely equal, so
    # `_hosts_match` cannot tell them apart and only the multi-tenant exclusion
    # withholds proof -- this is the case that reddens when it is removed.
    cfg = TrackConfig()
    board = _shipped(cfg.job_board_domains)
    leads = [_lead("Alpha - Analyst", f"https://www.{board}/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm=f"jobs-noreply@{board}", subject="Your application",
                           auth=_dkim_pass(board)),
                      leads, cfg.ats_relay_domains, cfg.job_board_domains)
    assert m.lead_slug is None and m.tier == "none"


def test_board_sourced_lead_still_corroborates_from_an_ats():
    # The accepted cost of the fix, pinned so it is a decision and not a surprise: a
    # board-sourced lead can no longer reach `proof`, so it DEGRADES to corroboration
    # (or to a proposal) rather than disappearing. Same lead, but an ATS relay sender
    # naming the company -> corroborated, which reconcile proposes. The board side is the
    # shipped default (that is the half under test); the ATS side is the placeholder
    # MERGED IN exactly as a user's own in-house relay would be.
    cfg = TrackConfig()
    board = _shipped(cfg.job_board_domains)
    leads = [_lead("Alpha - Analyst", f"https://www.{board}/jobs/view/1111", company="Alpha")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                           body="Alpha received your application."),
                      leads, {**cfg.ats_relay_domains, **ATS}, cfg.job_board_domains)
    assert m.tier == "corroborated" and m.lead_slug == "Alpha - Analyst"


def test_lead_side_multi_tenant_check_is_not_redundant():
    # `_is_multi_tenant` is consulted on BOTH sides. The lead side looks redundant while
    # every configured key is a REGISTRABLE domain (a sender matching a shared lead host
    # is then shared itself), but a user may add a NON-registrable board host -- and the
    # board's parent domain then reads as a non-shared sender while `_hosts_match`'s
    # bidirectional subdomain rule still pairs the two.
    boards = {"jobs.board.invalid": "example-board"}
    leads = [_lead("Alpha - Analyst", "https://jobs.board.invalid/1", company="Alpha")]
    assert match_receipt(_msg(frm="noreply@board.invalid", auth=_dkim_pass("board.invalid")),
                         leads, ATS, boards).tier == "none"


def test_sender_side_multi_tenant_check_is_not_redundant():
    # The mirror, and the reason BOTH clauses have to be there: deleting the sender-side
    # one left the whole suite green, because every other board case is also caught by
    # the lead side. With a non-registrable board key the two come apart the other way --
    # "x.jobs.board.invalid" is a subdomain of the lead's "board.invalid", so
    # `_hosts_match` fires while only the SENDER is multi-tenant. Neither clause
    # subsumes the other.
    boards = {"jobs.board.invalid": "example-board"}
    leads = [_lead("Alpha - Analyst", "https://board.invalid/1", company="Alpha")]
    m = match_receipt(_msg(frm="noreply@x.jobs.board.invalid",
                           auth=_dkim_pass("x.jobs.board.invalid")), leads, ATS, boards)
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
    leads = [_lead("Example - Analyst", "https://boards.ats.example.invalid/example/jobs/1", company="Example"),
             _lead("Widget - Engineer", "https://boards.ats.example.invalid/widget/jobs/2", company="Widget")]
    m = match_receipt(_msg(frm="no-reply@ats.example.invalid", auth=_dkim_pass("ats.example.invalid"),
                           body="Example and Widget both received applications today."), leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Widget - Engineer"]


# ── Sender authentication (CodeRabbit security round) ───────────────────────
#
# The RFC 5322 `From` domain is free text the sender writes. "Sender host == lead host"
# is therefore a claim, not evidence: anyone who knows the user is job hunting can forge
# a "thanks for applying" from an employer's domain and have sluice mark that lead
# `applied`, silently suppressing a real application. Proof needs the delivering server's
# own authentication verdict on top of the host match; everything short of an ALIGNED
# PASS degrades to the propose path so a human confirms, because the write is
# irreversible.


def _unauth_lead():
    return [_lead("Example - Analyst", "https://example.com/careers/1")]


def test_absent_authentication_degrades_to_propose():
    # No Authentication-Results at all -- the pre-fix behaviour for EVERY message, and the
    # case that reddens when the `and authenticated` conjunct is deleted. The signal is
    # kept (a resolved lead at the corroborated tier, which reconcile proposes), never
    # dropped and never auto-advanced.
    m = match_receipt(_msg(frm="jobs@example.com"), _unauth_lead(), ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_failing_dkim_degrades_to_propose():
    auth = "mx.example.invalid; dkim=fail header.i=@example.com; spf=fail smtp.mailfrom=x@example.com"
    m = match_receipt(_msg(frm="jobs@example.com", auth=auth), _unauth_lead(), ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_unaligned_dkim_pass_degrades_to_propose():
    # A PASS proves only that the SIGNING domain authorised the mail. An attacker signs
    # with a domain they control and forges the From header; without an alignment check
    # that reads as "authenticated" and hands them a proof-grade advance.
    m = match_receipt(_msg(frm="jobs@example.com", auth=_dkim_pass("attacker.invalid")),
                      _unauth_lead(), ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_sibling_subdomain_signature_is_not_aligned():
    # The documented cost of using `_hosts_match` for alignment instead of DMARC's
    # relaxed "same organizational domain": siblings under one parent do not align, since
    # deciding they do would need the public suffix list (not stdlib, not derivable by
    # counting labels -- the exact collapse `_hosts_match` refuses). It fails CLOSED, to a
    # proposal, which is the safe direction for an irreversible write.
    leads = [_lead("Example - Analyst", "https://careers.example.com/1")]
    m = match_receipt(_msg(frm="jobs@careers.example.com", auth=_dkim_pass("mail.example.com")),
                      leads, ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_dmarc_pass_aligned_reaches_proof():
    auth = "mx.example.invalid; dkim=none; dmarc=pass (p=REJECT sp=REJECT) header.from=example.com"
    m = match_receipt(_msg(frm="jobs@example.com", auth=auth), _unauth_lead(), ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_spf_pass_aligned_reaches_proof():
    # SPF authenticates the ENVELOPE sender, so it is evidence about the From domain only
    # when the two align -- which is what the alignment check requires of it.
    auth = ("mx.example.invalid; spf=pass (mx.example.invalid: domain of jobs@example.com "
            "designates 192.0.2.1 as permitted sender) smtp.mailfrom=jobs@example.com")
    m = match_receipt(_msg(frm="jobs@example.com", auth=auth), _unauth_lead(), ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_a_pass_hidden_inside_a_comment_grants_nothing():
    # RFC 8601 comments are free text, and a `;` inside one would split that method's
    # resinfo into fragments -- so a sender-supplied comment could smuggle a `dkim=pass`
    # no server ever asserted. Comments come out before any parsing. Here the real verdict
    # is spf=FAIL and the "pass" exists only inside the parentheses.
    # The smuggled pair must be followed by SPACE-separated text, not by the closing
    # paren: with `)` immediately after the domain, `_host("example.com)")` fails to
    # match for a reason having nothing to do with comment handling, and the mutant that
    # deletes `_strip_comments` survives -- an inert witness, confirmed by running it.
    auth = ("mx.example.invalid; spf=fail (note; dkim=pass header.i=@example.com as the "
            "sender wrote it) smtp.mailfrom=x@attacker.invalid")
    m = match_receipt(_msg(frm="jobs@example.com", auth=auth), _unauth_lead(), ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_unparseable_authentication_header_degrades_to_propose():
    # Garbage in the header must fail CLOSED, not raise out of this pure module (which
    # engine.run's per-message except would swallow, re-failing the same message forever).
    for auth in ("", "???", "mx.example.invalid", "dkim=pass", ";;;", "dkim=pass header.d="):
        m = match_receipt(_msg(frm="jobs@example.com", auth=auth), _unauth_lead(), ATS)
        assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst", auth


def test_non_string_authentication_header_does_not_crash():
    # Same defensive shape as `_headers`: a present-but-None (or otherwise non-string)
    # header value must abstain rather than raise.
    msg = {"headers": {"from": "jobs@example.com", "authentication-results": None},
           "body_text": ""}
    assert match_receipt(msg, _unauth_lead(), ATS).tier == "corroborated"


def test_authentication_alone_never_creates_a_match():
    # The conjunct is ADDITIVE: an aligned pass for a sender that matches no lead host
    # still resolves to `none`. Authentication says the domain is real, never that it is
    # the lead's.
    m = match_receipt(_msg(frm="jobs@other.invalid", auth=_dkim_pass("other.invalid")),
                      _unauth_lead(), ATS)
    assert m == ReceiptMatch(None, "none", [])
