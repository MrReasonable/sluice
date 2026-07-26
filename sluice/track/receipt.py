"""Deterministic receipt -> lead matching. Pure: no I/O, so it is tested offline.
The LLM decides a message IS an application receipt; this module decides WHICH
shortlist lead it belongs to, by SENDER domain -- never by a fuzzy name match, and
never by a link in the body (which the sender writes and so controls). A wrong or
arbitrary advance silently suppresses a real application, so the three failure modes
this guards are (a) matching a name-only mention, (b) advancing an AMBIGUOUS match and
(c) trusting an UNAUTHENTICATED sender domain; all three resolve to `none`/propose,
never a proof advance (#10)."""
import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from urllib.parse import urlparse

from sluice.core.leads import _norm_tokens

_EMAIL_DOMAIN_RE = re.compile(r"[\w.+-]+@([\w.-]+)")
_COMMENT_RE = re.compile(r"\([^()]*\)")
_METHOD_RE = re.compile(r"([A-Za-z][A-Za-z0-9-]*)\s*=\s*([A-Za-z]+)")
_PROP_RE = re.compile(r"([A-Za-z]+\.[A-Za-z-]+)\s*=\s*([^\s;]+)")

# Which RFC 8601 authentication methods can establish that a `From` domain is genuine,
# and which of each method's `ptype.property` pairs names the domain that must ALIGN with
# it. dkim: the signing domain (header.d; header.i is the same domain as an @-address).
# dmarc: the domain the receiving server itself checked alignment for. spf: the envelope
# sender / HELO domain, which is only evidence about the From domain when the two align.
_AUTH_DOMAIN_PROPS = {"dkim": ("header.d", "header.i"),
                      "dmarc": ("header.from",),
                      "spf": ("smtp.mailfrom", "smtp.helo")}


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


def _headers(msg) -> dict:
    """`msg["headers"]` read defensively: it can be absent, None, or carry None VALUES.
    A `.get(key, "")` default only covers a MISSING key -- a present-but-None Subject
    still returns None and used to raise TypeError out of this PURE module, while a None
    headers dict raised AttributeError. engine.run's per-message `except` swallows both
    WITHOUT adding the id to `seen`, so the same message re-fails on every future run: a
    silent poison message, not a one-off failure. Every read of this dict therefore uses
    `or ""`, matching what `body_text`'s call sites already do."""
    return msg.get("headers") or {}


def _sender_host(msg) -> str:
    """Domain of the REAL envelope address, never the display name. RFC 5322 permits
    arbitrary text before the angle-bracket address, so a header like
    '"jobs@example.com" <x@evil.invalid>' names evil.invalid as the actual sender --
    a raw @-scan of the whole header string would grab the sender-controlled display
    name's address instead and be trivially spoofable. parseaddr (stdlib) is the
    correct RFC 5322 parse; only its second element is untrusted-but-real."""
    _, addr = parseaddr(_headers(msg).get("from") or "")
    m = _EMAIL_DOMAIN_RE.search(addr)
    return _host(m.group(1)) if m else ""


def _strip_comments(value: str) -> str:
    """Remove RFC 8601 CFWS comments before parsing. Gmail writes them routinely --
    `spf=pass (mx.example: domain of x designates ...) smtp.mailfrom=...` -- and a comment
    may contain `;` and `=`, which would both split one method's resinfo into fragments
    and let a SENDER-supplied comment smuggle a `dkim=pass` no server ever asserted. Run
    to a fixed point because comments nest; each pass strictly shortens the string, so it
    terminates."""
    prev = None
    while prev != value:
        prev, value = value, _COMMENT_RE.sub(" ", value)
    return value


def _hosts_match(a: str, b: str) -> bool:
    """Full-host equality or a subdomain relationship EITHER direction. Bidirectional so
    a bare corporate domain matches a jobs subdomain and vice versa; on FULL hosts, so
    alpha.example.com and beta.example.com never collapse to a shared example.com
    suffix (never reduce to a "registrable domain" by keeping the last two labels --
    that is exactly the collapse this guards against)."""
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _sender_authenticated(msg, sender: str) -> bool:
    """True iff the server that DELIVERED this message recorded an authentication PASS
    whose authenticated domain aligns with `sender` (RFC 8601 `Authentication-Results`,
    already lowercased into `msg["headers"]` by `track/google_client.py` -- no fetch-layer
    change, and this function stays pure).

    Why proof requires it: an RFC 5322 `From` domain is free text the sender writes, so
    "the sender host equals the lead's host" is a CLAIM, not evidence. Anyone who knows
    the user is job hunting can forge a "thanks for applying" from an employer's domain
    and have sluice mark that lead `applied` -- suppressing a real application, silently
    and irreversibly, which is precisely this feature's harm model. DKIM/SPF/DMARC are the
    only in-message signals that the domain owner actually authorised the mail, and only
    the receiving server's verdict counts: we cannot verify a signature from here (pure,
    stdlib-only, no key fetch), so we read the verdict it already wrote down.

    ALIGNMENT is `_hosts_match` -- equality or a subdomain relationship either way -- and
    deliberately NOT DMARC's relaxed "same organizational domain", which needs the public
    suffix list: that is neither in the stdlib nor derivable by counting labels, and
    guessing it is the exact collapse `_hosts_match` exists to refuse (alpha.example.com
    and beta.example.com are not the same site). The cost is that a sibling subdomain
    signing for its sibling reads as UNALIGNED. That fails CLOSED -- the receipt degrades
    to a human-confirmed proposal rather than auto-advancing -- which is the only safe
    direction for an irreversible write.

    Absent, unparseable, non-passing or unaligned: False. Fail closed on every route."""
    raw = _headers(msg).get("authentication-results")
    if not sender or not isinstance(raw, str):
        return False
    # `;` separates one method's resinfo from the next; the first field is the
    # authserv-id, which carries no `method=result` and so falls out of _METHOD_RE.
    for chunk in _strip_comments(raw).split(";"):
        m = _METHOD_RE.match(chunk.strip())
        if not m:
            continue
        props = _AUTH_DOMAIN_PROPS.get(m.group(1).lower())
        if props is None or m.group(2).lower() != "pass":
            continue
        for prop, value in _PROP_RE.findall(chunk):
            if prop.lower() not in props:
                continue
            # smtp.mailfrom / header.i carry a full address; the domain is after the '@'.
            # `_host` then applies the same parse (and the same non-ASCII refusal) the
            # sender host went through, so a confusable cannot align with its lookalike.
            domain = _host(value.strip('\'",<>').rsplit("@", 1)[-1])
            if _hosts_match(sender, domain):
                return True
    return False


def _suffix_match(host: str, domains) -> bool:
    """True iff `host` equals, or is a dot-separated SUBDOMAIN of, one of `domains`'
    keys. A dot-separated suffix, never a bare substring: "greenhouse.io" sits inside
    "fake-greenhouse.io.evil.invalid", whose real suffix is ".evil.invalid", and a
    substring test would hand that lookalike the privileges of the genuine relay."""
    return any(host == k or host.endswith("." + k) for k in (domains or {}))


def _is_multi_tenant(host: str, ats, boards) -> bool:
    """True when `host` is shared by MANY employers, so it can never prove WHICH
    employer a receipt concerns. Two sets, and BOTH are consulted: ATS relay hosts
    (`ats_relay_domains`) and the job boards sluice itself scrapes
    (`job_board_domains`).

    The boards half closes the Critical this module shipped with. A lead's `fm["url"]`
    is the URL sluice INGESTED the lead from, and sluice scrapes job BOARDS -- so for
    most leads that host is a multi-tenant aggregator (linkedin.com, reed.co.uk, ...),
    not the employer. Treating it as employer-identifying meant a receipt whose sender
    merely shared that board host proof-matched a lead the user had never applied to,
    and auto-advanced it: a wrong `applied` silently suppresses a real application and
    is effectively irreversible.

    Checked on BOTH sides of the comparison. The sender-side check is what stops a
    board-sent receipt; the lead-side check is not redundant, because a user may list a
    non-registrable host (`jobs.board.example`) whose PARENT would still read as a
    non-shared sender under `_hosts_match`'s bidirectional subdomain rule.

    An empty set means "nothing known to be shared", not "match nothing": this is a
    safety DENYLIST, not a preference gate, so emptying it makes the proof tier MORE
    permissive. The shipped defaults are non-empty by design (see sluice.yaml.example)
    and `load_track_config` MERGES user entries over them rather than replacing them."""
    return _suffix_match(host, ats) or _suffix_match(host, boards)


def match_receipt(msg, shortlist_leads, ats_relay_domains, job_board_domains=None) -> ReceiptMatch:
    ats, boards = ats_relay_domains or {}, job_board_domains or {}
    # Only the SENDER host is evidence of who sent the mail. Body links are sender-
    # controlled footer content -- any sender can link anything, so "we got your
    # application, follow us at <board>" from an unrelated address used to hand that
    # sender both a proof host AND the ATS-relay flag. Both tiers now key off the
    # sender alone (#10 pre-push review, Critical; the corroborated half was flagged
    # independently by CodeRabbit).
    sender = _sender_host(msg)
    # Computed ONCE, outside the loop: authentication is a property of the message, not of
    # any lead. Placed here, in the function that decides the TIER, so the guarantee is
    # structural -- no caller can forget it, and reconcile's auto-apply gate (which keys
    # off `proof`) is unable to advance on an unauthenticated receipt.
    authenticated = _sender_authenticated(msg, sender)
    tokens = _norm_tokens((_headers(msg).get("subject") or "") + " "
                          + (msg.get("body_text") or ""))
    from_ats = _suffix_match(sender, ats)
    proof, corrob = [], []
    for lead in shortlist_leads:
        lead_host = _host(lead.fm.get("url") or "")
        # The host half of proof: the sender IS the lead's own host, and neither side is a
        # host shared by many employers. `_hosts_match` is False when either host is
        # empty, so a url-less lead or an unparseable From abstains rather than matching
        # everything.
        host_proof = (_hosts_match(sender, lead_host)
                      and not _is_multi_tenant(sender, ats, boards)
                      and not _is_multi_tenant(lead_host, ats, boards))
        # proof needs BOTH halves: the host lines up AND the delivering server
        # authenticated that domain. An ADDITIONAL conjunct, never a replacement -- the
        # multi-tenant exclusions still do all the work they did before.
        if host_proof and authenticated:
            proof.append(lead.slug)
            continue
        if host_proof:
            # Host matches, authentication does not. DEGRADE, never drop: an unauthenticated
            # receipt from the right-looking domain is still the strongest signal available
            # about this lead, so it becomes a proposal a human confirms rather than
            # vanishing (that would be the #40 silent-loss class) or auto-advancing (that
            # would be the forgery this check exists to stop). `continue` costs nothing --
            # the ATS branch below cannot fire for a proof-eligible sender, since every
            # ATS relay is multi-tenant.
            corrob.append(lead.slug)
            continue
        # corroborated: from an ATS relay host, with the company named in the body.
        if from_ats:
            company = _norm_tokens(lead.fm.get("company") or "")
            if company and company <= tokens:
                corrob.append(lead.slug)
    # No cross-tier ambiguity check: with both tiers keyed off the sender, the two are
    # now DISJOINT by construction -- corroboration requires an ATS sender, proof
    # requires a sender that is NOT multi-tenant, and every ATS relay is multi-tenant.
    # The unauthenticated-degrade route above cannot reopen it either: `authenticated`
    # is per-MESSAGE, so when it is False nothing reaches `proof` at all, and when it is
    # True nothing reaches `corrob` by that route.
    # A guard for a state the code cannot reach is an inert guard, so it is gone rather
    # than kept with a comment claiming it fires. (The receipt that motivated it -- an
    # ATS sender whose body links a second lead's own site -- now resolves through the
    # ordinary ambiguous-corroboration branch below, still refusing to advance.)
    if len(proof) == 1:
        return ReceiptMatch(proof[0], "proof", [])
    if len(proof) > 1:                                 # ambiguous proof -> propose, never advance
        return ReceiptMatch(None, "corroborated", sorted(proof))
    if len(corrob) == 1:
        return ReceiptMatch(corrob[0], "corroborated", [])
    if len(corrob) > 1:
        return ReceiptMatch(None, "corroborated", sorted(corrob))
    return ReceiptMatch(None, "none", [])
