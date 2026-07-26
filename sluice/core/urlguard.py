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
