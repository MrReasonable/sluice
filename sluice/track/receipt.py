"""Deterministic receipt -> lead matching. Pure: no I/O, so it is tested offline.
The LLM decides a message IS an application receipt; this module decides WHICH
shortlist lead it belongs to, by domain -- never by a fuzzy name match. A wrong or
arbitrary advance silently suppresses a real application, so the two failure modes
this guards are (a) matching a name-only mention and (b) advancing an AMBIGUOUS
match; both resolve to `none`/propose, never a proof advance (#10)."""
import re
from dataclasses import dataclass, field
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
    nothing parseable -- a url-less lead thus never matches (abstain, not match-all)."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" not in v:
        v = "//" + v                   # let urlparse read a bare host/domain
    host = urlparse(v).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _sender_host(msg) -> str:
    m = _EMAIL_DOMAIN_RE.search(msg.get("headers", {}).get("from", "") or "")
    return _host(m.group(1)) if m else ""


def _link_hosts(msg) -> set:
    return {h for h in (_host(u) for u in _URL_RE.findall(msg.get("body_text", "") or "")) if h}


def _hosts_match(a: str, b: str) -> bool:
    """Full-host equality or a subdomain relationship EITHER direction. Bidirectional so
    a bare corporate domain matches a jobs subdomain and vice versa; on FULL hosts, so
    bigco.co.uk and random.co.uk never collapse to a shared co.uk suffix."""
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
        return ReceiptMatch(proof[0], "proof", [])
    if len(proof) > 1:                                 # ambiguous proof -> propose, never advance
        return ReceiptMatch(None, "corroborated", sorted(proof))
    if len(corrob) == 1:
        return ReceiptMatch(corrob[0], "corroborated", [])
    if len(corrob) > 1:
        return ReceiptMatch(None, "corroborated", sorted(corrob))
    return ReceiptMatch(None, "none", [])
