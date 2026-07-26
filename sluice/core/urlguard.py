"""URL policy for the dossier fetcher (#18).

`Sluice.dossier_cache()` navigates a browser to a lead url that came off a
scraped listing -- an attacker-influenceable field. This module decides whether
that url may be fetched: http(s) only, and only to globally routable addresses,
with an explicit per-host/CIDR opt-out for a user who deliberately runs a board
on their LAN.

Purity splits rather than vanishing. DNS is I/O, so the split mirrors
Source.parse/Source.fetch: `_host`, `_embedded_v4`, `verdict` and
`parse_allow_hosts` are PURE and table-tested; `_resolve` is the one impure
function and is injected, so no test resolves.

Design notes that are NOT obvious and were each found by review:
  - the address rule is a default-deny predicate, not a six-way `or` of the
    named categories: redundant conjuncts are equivalent mutants no test kills;
  - it is applied to any IPv4 address EMBEDDED in an IPv6 one, because
    `is_global` reads the wrapper and a DNS64 answer can carry 127.0.0.1;
  - `_host` must NOT strip a leading `www.`, unlike its sibling in track/receipt.
"""
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# The closed set of reason slugs. Defined here so the closure in core/app.py and
# every test import the same strings rather than re-typing them.
SCHEME = "scheme"
NO_HOST = "no-host"
RESOLVE_FAILED = "resolve-failed"
RESOLVE_EMPTY = "resolve-empty"
BLOCKED_ADDRESS = "blocked-address"
NOT_SETTLED = "not-settled"
LANDED_BLOCKED = "landed-blocked"
LANDED_UNREADABLE = "landed-unreadable"
# The two paths that used to fall through to a silently-cached empty dossier.
NO_TAB = "no-tab"
BODY_UNREADABLE = "body-unreadable"

# Digits and dots only. This is the IP-shaped test's third draft; the two before
# it each had a silent failure mode recorded in the spec. Keying on `/` alone let
# `10.0.0.300` become a hostname grant that could never fire; adding "hex digits"
# fixed that but RAISED on `db`, `cafe`, `abc` -- legitimate single-label LAN
# hostnames, i.e. exactly the user this opt-out exists for.
_DIGITS_AND_DOTS = re.compile(r"[0-9.]+")

# Conservative letter-digit-hyphen hostname shape: dot-separated non-empty labels.
# Deliberately narrow -- anything it rejects is something that could never match a
# real url host, so refusing loudly beats granting inertly.
_LDH = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
                  r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*")


class DossierBlocked(Exception):
    """A dossier fetch was refused by policy.

    The message is the reason SLUG ONLY -- never a url, host, or config entry.
    cv/engine.py:70 does `_log.warning("dossier for %s failed: %s", note.ref, e)`,
    so anything carried here is logged verbatim; that is the #67 leak shape.
    The host reaches the operator via the WARNING line in dossier_cache, which is
    the single place it is needed (to write an allowlist entry).
    """


@dataclass(frozen=True)
class AllowList:
    """Parsed `dossier_allow_hosts`. Empty means "no exceptions granted" -- NOT
    "match nothing": an unconfigured install still fetches every public url,
    because the address rule, not this list, is what admits them."""
    hosts: frozenset = frozenset()
    networks: tuple = ()


def _ip_shaped(entry: str) -> bool:
    """Should this entry be required to parse as an address/network?

    True for anything containing `/` or `:`, or made only of digits and dots.
    The `:` clause is what makes `[fd00::5]` and `jobs.invalid:8080` RAISE rather
    than becoming inert hostname grants -- urlparse strips brackets and ports, so
    neither could ever match a real url, and the user would get a permanently dead
    exception plus the same warning they were trying to silence.

    Accepted edge: `1` and `0` are IP-shaped and therefore raise. That is loud and
    pathological, rather than silent and plausible, which is the right trade.
    """
    return "/" in entry or ":" in entry or bool(_DIGITS_AND_DOTS.fullmatch(entry))


def _norm_host(host: str) -> str:
    """Lowercase and drop at most one trailing dot, so a fully-qualified url host
    (`jobs.invalid.`) compares equal to the entry a user wrote (`jobs.invalid`).
    Without this the user's exception silently never fires."""
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


def parse_allow_hosts(entries, *, key: str = "dossier_allow_hosts") -> AllowList:
    """Validate and split `entries` into hostname grants and network grants.

    Raises ValueError naming the config key and the entry's INDEX -- never its
    value, and never another element of the list.
    """
    valid = f"{key}:\n    - jobs.invalid        # a hostname\n    - 10.0.0.0/8          # or a CIDR"
    if not isinstance(entries, (list, tuple)):
        raise ValueError(
            f"{key} must be a list of strings, got {type(entries).__name__}. "
            f"Valid form:\n  {valid}") from None
    hosts, networks = set(), []
    for i, raw in enumerate(entries):
        if not isinstance(raw, str):
            raise ValueError(
                f"{key}[{i}] must be a string, got {type(raw).__name__}. "
                f"Valid form:\n  {valid}") from None
        entry = raw.strip()
        if not entry:
            raise ValueError(
                f"{key}[{i}] is empty. Valid form:\n  {valid}") from None
        if _ip_shaped(entry):
            try:
                # strict=True, deliberately. strict=False silently widens
                # `192.0.2.5/24` to `192.0.2.0/24` -- the user names one address
                # and receives 256 exceptions to the guard, with nothing recording
                # it. Widening is the unsafe direction on an ALLOWlist.
                networks.append(ipaddress.ip_network(entry, strict=True))
            except ValueError:
                # `from None`: ipaddress' own ValueError text contains the literal
                # entry, so chaining it would re-expose the user's subnet.
                raise ValueError(
                    f"{key}[{i}] looks like an address or network but is not a "
                    f"valid one. Valid form:\n  {valid}") from None
        elif _LDH.fullmatch(entry.rstrip(".")):
            hosts.add(_norm_host(entry))
        else:
            # Not IP-shaped and not a legal hostname. Accepting it would add a grant
            # that can never equal urlparse().hostname -- the user's exception would
            # silently never fire and they would keep seeing the same refusal. A
            # wildcard (`*.jobs.invalid`) is the most likely case; we do not support
            # one, so say so at construction rather than ignoring it.
            raise ValueError(
                f"{key}[{i}] is neither a valid hostname nor an address/network "
                f"(wildcards are not supported). Valid form:\n  {valid}") from None
    return AllowList(hosts=frozenset(hosts), networks=tuple(networks))


# IPv6 prefixes that carry an IPv4 address in their low 32 bits. `.ipv4_mapped`
# and `.sixtofour` cover two more shapes and are used directly below.
_V4_COMPAT = ipaddress.ip_network("::/96")        # RFC 4291, deprecated
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")   # RFC 6052


@dataclass(frozen=True)
class UrlVerdict:
    allowed: bool
    reason: str = ""     # "" when allowed; one of the module's slugs otherwise
    host: str = ""       # "" only when the url yielded no host at all


def _embedded_v4(addr):
    """The IPv4 address an IPv6 address carries, or None.

    `is_global` reads the WRAPPER, not the payload, so an IPv6 address can be
    globally classified while addressing 127.0.0.1. On a DNS64 network
    getaddrinfo synthesises exactly that for an A-record-only name.

    RFC 8215's local-use NAT64 prefix (64:ff9b:1::/48) is deliberately absent:
    its embedding offset is deployment-specific and therefore not decodable. It
    is blocked today by the base predicate; that residual is documented in the
    spec rather than guessed at.
    """
    if not isinstance(addr, ipaddress.IPv6Address):
        return None
    if addr.ipv4_mapped is not None:          # ::ffff:0:0/96
        return addr.ipv4_mapped
    if addr.sixtofour is not None:            # 2002::/16
        return addr.sixtofour
    if addr in _V4_COMPAT or addr in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def _routable(addr) -> bool:
    """One default-deny predicate, applied to the address AND to any IPv4 it embeds.

    NOT written as `not (is_loopback or is_private or is_link_local or is_reserved
    or is_multicast or is_unspecified)`. That form has six redundant conjuncts:
    delete any one and the suite stays green, so the table would certify nothing.
    `is_global` already subsumes all six -- and CGNAT (100.64.0.0/10), which
    carries none of them -- and tracks CPython's IANA special-purpose table.
    `is_multicast` is NOT subsumed: 224.0.0.1 and ff02::1 are both is_global=True.
    """
    if not (addr.is_global and not addr.is_multicast):
        return False
    embedded = _embedded_v4(addr)
    if embedded is not None:
        return embedded.is_global and not embedded.is_multicast
    return True


def _granted(addr, host: str, allow: AllowList) -> bool:
    """Does the user's allowlist cover this otherwise-blocked address?

    A hostname grant is EXACT (see the subdomain test) and covers every address
    that host resolves to -- the user explicitly trusted the name. A network grant
    covers the address regardless of name.
    """
    if _norm_host(host) in allow.hosts:
        return True
    return any(addr in net for net in allow.networks
               if net.version == addr.version)


def verdict(host: str, addrs, *, allow_hosts: AllowList) -> UrlVerdict:
    """PURE policy: given a host and its ALREADY-RESOLVED addresses, may we fetch?

    Every answer must pass, so a multi-A-record host cannot smuggle one private
    address through by ordering. An unparseable answer blocks -- fail closed.
    """
    answers = list(addrs)
    if not answers:
        return UrlVerdict(False, RESOLVE_EMPTY, host)
    for raw in answers:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            return UrlVerdict(False, BLOCKED_ADDRESS, host)
        if not _routable(addr) and not _granted(addr, host, allow_hosts):
            return UrlVerdict(False, BLOCKED_ADDRESS, host)
    return UrlVerdict(True, "", host)


def _host(url: str) -> str:
    """The host of a url, exactly as the browser will see it. "" if there is none.

    Modelled on track/receipt._host, with ONE deliberate difference: this does NOT
    strip a leading `www.`. That line is a receipt-matching nicety there and a
    TOTAL GUARD BYPASS here -- stripping it makes the guard resolve and check
    `attacker.invalid` while the browser navigates to `www.attacker.invalid`,
    which may resolve somewhere else entirely.

    The ASCII check runs on the raw AUTHORITY, before urlparse lowercases it.
    U+212A KELVIN folds to ASCII 'k' under that lowering, so a check applied to
    .hostname can never fire (#10 shipped exactly that inert check once). A genuine
    IDN arrives pre-encoded as ASCII punycode, so refusing a non-ASCII authority
    costs nothing real.

    It is scoped to the authority and NOT to the whole url, deliberately: a path or
    query may legitimately carry non-ASCII (a French or German posting), and
    refusing those would block the lead permanently -- check_url returns on an empty
    host before the allowlist is consulted, so there would be no remedy.
    """
    value = (url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        # .netloc is the RAW authority -- urlsplit does not lowercase it, which is
        # what makes this check able to fire at all. .hostname (below) does.
        if not parts.netloc.isascii():
            return ""
        # .hostname strips IPv6 brackets and takes the LAST `@` of a userinfo trick,
        # both of which we want.
        return parts.hostname or ""
    except ValueError:      # e.g. "https://[abc" -- an invalid IPv6 literal
        return ""
