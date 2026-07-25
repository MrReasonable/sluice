"""Deterministic receipt -> lead matching. Pure: no I/O, so it is tested offline.
The LLM decides a message IS an application receipt; this module decides WHICH
shortlist lead it belongs to, by domain -- never by a fuzzy name match. A wrong or
arbitrary advance silently suppresses a real application, so the two failure modes
this guards are (a) matching a name-only mention and (b) advancing an AMBIGUOUS
match; both resolve to `none`/propose, never a proof advance (#10)."""
import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from urllib.parse import urlparse

from sluice.core.leads import _norm_tokens

# A permissive URL scrape of the body; the host is what matters, not the full URL.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_EMAIL_DOMAIN_RE = re.compile(r"[\w.+-]+@([\w.-]+)")


@dataclass
class ReceiptMatch:
    lead_slug: "str | None" = None
    tier: str = "none"                 # proof | corroborated | none
    candidates: list = field(default_factory=list)


def _host(value: str) -> str:
    """Host of a URL or a bare domain: lowercased, leading www. stripped. Empty when
    nothing parseable -- a url-less lead thus never matches (abstain, not match-all) --
    or when urlparse rejects the input outright: a malformed fragment (e.g. an
    unbalanced "[" from an IPv6-literal-looking URL) raises ValueError, and body text
    and headers arrive off the internet, so that is a reachable crash, not a
    hypothetical one -- or when the host isn't plain ASCII: Unicode case-folding can
    equate a lookalike character with a real one (U+212A KELVIN SIGN folds to ascii
    'k' under str.lower()), so the ascii check runs BEFORE any lowering (ours or
    urlparse's own internal lower()) -- checking after would already be too late,
    since the fold that makes the lookalike indistinguishable would have already
    happened. A genuine internationalized domain arrives pre-encoded as ASCII
    punycode (xn--...) anyway, so rejecting non-ASCII outright costs nothing real."""
    v = (value or "").strip()
    if not v or not v.isascii():
        return ""
    v = v.lower()
    if "://" not in v:
        v = "//" + v                   # let urlparse read a bare host/domain
    try:
        host = urlparse(v).hostname or ""
    except ValueError:                 # e.g. "https://[abc" -- an invalid IPv6 literal
        return ""
    return host[4:] if host.startswith("www.") else host


def _sender_host(msg) -> str:
    """Domain of the REAL envelope address, never the display name. RFC 5322 permits
    arbitrary text before the angle-bracket address, so a header like
    '"jobs@example.com" <x@evil.invalid>' names evil.invalid as the actual sender --
    a raw @-scan of the whole header string would grab the sender-controlled display
    name's address instead and be trivially spoofable. parseaddr (stdlib) is the
    correct RFC 5322 parse; only its second element is untrusted-but-real."""
    _, addr = parseaddr(msg.get("headers", {}).get("from", "") or "")
    m = _EMAIL_DOMAIN_RE.search(addr)
    return _host(m.group(1)) if m else ""


def _link_hosts(msg) -> set:
    return {h for h in (_host(u) for u in _URL_RE.findall(msg.get("body_text", "") or "")) if h}


def _hosts_match(a: str, b: str) -> bool:
    """Full-host equality or a subdomain relationship EITHER direction. Bidirectional so
    a bare corporate domain matches a jobs subdomain and vice versa; on FULL hosts, so
    alpha.example.com and beta.example.com never collapse to a shared example.com
    suffix (never reduce to a "registrable domain" by keeping the last two labels --
    that is exactly the collapse this guards against)."""
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _is_ats(host: str, ats) -> bool:
    return any(host == k or host.endswith("." + k) for k in (ats or {}))


def match_receipt(msg, shortlist_leads, ats_relay_domains) -> ReceiptMatch:
    ats = ats_relay_domains or {}
    receipt_hosts = {h for h in ({_sender_host(msg)} | _link_hosts(msg)) if h}
    if not receipt_hosts:
        return ReceiptMatch(None, "none", [])
    tokens = _norm_tokens(
        msg.get("headers", {}).get("subject", "") + " " + (msg.get("body_text", "") or ""))
    from_ats = any(_is_ats(r, ats) for r in receipt_hosts)
    proof, corrob = [], []
    for lead in shortlist_leads:
        lead_host = _host(lead.fm.get("url", ""))
        # proof: a full-host match to the lead's OWN (non-ATS) domain.
        if lead_host and not _is_ats(lead_host, ats) \
                and any(_hosts_match(r, lead_host) and not _is_ats(r, ats) for r in receipt_hosts):
            proof.append(lead.slug)
            continue
        # corroborated: from an ATS relay host, with the company named in the body.
        if from_ats:
            company = _norm_tokens(lead.fm.get("company", ""))
            if company and company <= tokens:
                corrob.append(lead.slug)
    if len(proof) == 1:
        # A single proof match can still be AMBIGUOUS ACROSS TIERS: an ATS receipt
        # routinely links the company's own site in its body (a "view your
        # application" footer is ordinary traffic, not an attack), so the same
        # receipt can proof-match one lead via that link while corroborating a
        # DIFFERENT lead via its ATS sender + company name. Cross-tier ambiguity is
        # still ambiguity -- refuse rather than silently pick the proof winner, the
        # same refuse-on-ambiguity principle already applied WITHIN a tier, extended
        # across tiers (#10 fix-round-1: a receipt from greenhouse hosting lead B,
        # whose body links lead A's own site, must not silently advance A).
        other_corrob = set(corrob) - {proof[0]}
        if other_corrob:
            return ReceiptMatch(None, "corroborated", sorted(set(proof) | set(corrob)))
        return ReceiptMatch(proof[0], "proof", [])
    if len(proof) > 1:                                 # ambiguous proof -> propose, never advance
        return ReceiptMatch(None, "corroborated", sorted(proof))
    if len(corrob) == 1:
        return ReceiptMatch(corrob[0], "corroborated", [])
    if len(corrob) > 1:
        return ReceiptMatch(None, "corroborated", sorted(corrob))
    return ReceiptMatch(None, "none", [])
