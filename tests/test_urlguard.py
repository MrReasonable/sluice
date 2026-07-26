"""Tests for the dossier SSRF guard's pure policy (#18)."""
import ipaddress

import pytest

from sluice.core import urlguard


# --- allowlist parsing -------------------------------------------------------
# The dispatch rule went through three drafts; each of these cases killed one of
# them. See the spec's "Allowlist" section for which.

@pytest.mark.parametrize("entry", [
    "10.0.0.300",          # digits+dots, not a valid address -> must NOT become a hostname
    "127.1",               # ditto (getaddrinfo would normalise it, the allowlist must not)
    "2130706433",
    "[::1]",               # brackets: urlparse strips them, so this could never match
    "[fd00::5]",
    "jobs.invalid:8080",   # port: urlparse strips it, so this could never match
    "192.0.2.5/24",        # host bits set -> strict=True refuses to silently widen
    "",
    "   ",
    # Not IP-shaped, but not a hostname either. Each is a plausible thing a user
    # writes on an allowlist, and each could NEVER equal urlparse().hostname --
    # the same permanently-inert-grant failure the `:` clause above closes.
    "*.jobs.invalid",      # wildcards are not supported; say so rather than ignoring
    ".jobs.invalid",       # empty leading label
    "jobs invalid",        # inner space (only leading/trailing are stripped)
    "jobs_invalid@x",
])
def test_malformed_allowlist_entries_raise(entry):
    with pytest.raises(ValueError):
        urlguard.parse_allow_hosts([entry])


def test_non_string_entry_raises():
    with pytest.raises(ValueError):
        urlguard.parse_allow_hosts([object()])


def test_a_yaml_scalar_raises_rather_than_exploding():
    """A bare string must not become one grant per character.

    Uses a DOTLESS scalar deliberately: 'jobs.invalid' would explode to a list
    containing '.', which is IP-shaped and raises for the wrong reason -- the
    version of this test that did that passed while the guard was absent.
    """
    with pytest.raises(ValueError):
        urlguard.parse_allow_hosts("myboard")


@pytest.mark.parametrize("entry", ["db", "cafe", "abc", "abba", "jobs.invalid", "nas1", "host2.invalid"])
def test_short_hex_like_hostnames_are_accepted_as_hostnames(entry):
    # A single-label LAN hostname is precisely the user this opt-out exists for.
    # An earlier "hex digits only" heuristic raised on every one of these.
    parsed = urlguard.parse_allow_hosts([entry])
    assert parsed.hosts == frozenset({entry})
    assert parsed.networks == ()


def test_whitespace_is_stripped_not_silently_dropped():
    # A stray space in a YAML entry used to fall through to the hostname branch
    # as a grant that could never fire.
    parsed = urlguard.parse_allow_hosts(["10.0.0.1 "])
    assert parsed.networks == (ipaddress.ip_network("10.0.0.1/32"),)
    assert parsed.hosts == frozenset()


def test_networks_and_hostnames_are_separated():
    parsed = urlguard.parse_allow_hosts(["10.0.0.0/8", "fd00::/8", "jobs.invalid"])
    assert parsed.hosts == frozenset({"jobs.invalid"})
    assert set(parsed.networks) == {ipaddress.ip_network("10.0.0.0/8"),
                                    ipaddress.ip_network("fd00::/8")}


def test_hostname_entries_are_normalised_for_comparison():
    parsed = urlguard.parse_allow_hosts(["JOBS.invalid."])
    assert parsed.hosts == frozenset({"jobs.invalid"})


def test_empty_allowlist_parses_to_an_empty_allowlist():
    parsed = urlguard.parse_allow_hosts([])
    assert parsed.hosts == frozenset() and parsed.networks == ()


def test_validation_error_leaks_neither_the_entry_nor_its_neighbours():
    # A config file is one of the few places a user's real private hostnames
    # legitimately live, and an exception travels further than the file does.
    # ipaddress' own ValueError contains the literal, so the chain must be cut.
    entries = ["private-a.invalid", "10.0.0.300", "private-b.invalid"]
    with pytest.raises(ValueError) as ei:
        urlguard.parse_allow_hosts(entries)
    msg = str(ei.value)
    assert "10.0.0.300" not in msg
    assert "private-a.invalid" not in msg
    assert "private-b.invalid" not in msg
    assert "dossier_allow_hosts" in msg
    assert "[1]" in msg, "the message must locate the entry by index"
    assert ei.value.__cause__ is None, "from None: ipaddress' message must not travel"


# --- address classification --------------------------------------------------
# Rows are keyed to the predicate's FOUR TERMS, not to named shapes. The table has
# been found hand-picked three times; each fix that only added shapes left a real
# mutant alive. Every term below has at least one row that fails on that term ALONE.

_EMPTY = urlguard.AllowList()

# term 1: the wrapper address is not globally routable.
_WRAPPER_NOT_GLOBAL = [
    "127.0.0.1", "::1",                                   # loopback
    "10.0.0.1", "172.31.255.254", "192.168.1.1",          # private v4
    "fc00::1", "fd00::1",                                 # private v6
    "169.254.169.254", "fe80::1", "fe80::1%1",            # link-local incl. cloud metadata, scoped
    "240.0.0.1",                                          # reserved
    "0.0.0.0", "::",                                      # unspecified
    "203.0.113.1", "192.0.2.1", "198.51.100.1", "2001:db8::1",   # RFC documentation
    "100.64.0.1",                                         # CGNAT (carries NONE of the six named flags)
    "198.18.0.1",                                         # benchmarking
]
# term 2: wrapper IS global, and only `not is_multicast` refuses it.
_WRAPPER_MULTICAST = ["224.0.0.1", "ff02::1"]
# term 3: wrapper passes is_global; only the EMBEDDED v4 refuses it. These two are
# the load-bearing witnesses for _embedded_v4 -- verified, they are the ONLY rows
# that redden when the embedding recheck is deleted.
_PAYLOAD_NOT_GLOBAL = [
    "64:ff9b::7f00:1",     # NAT64 well-known -- is_global=True on the wrapper, a real hole
    "::127.0.0.1",         # v4-compatible  -- is_global=True on the wrapper, ditto
]
# Blocked TWICE OVER: CPython's IPv6Address.is_global already consults the mapped /
# 6to4 payload, so the base predicate alone refuses these and they witness nothing
# about _embedded_v4 (measured: they stay green under Mutant B). Kept as regression
# pins against a future CPython change, NOT as witnesses -- filed separately so the
# table's labels state what each row actually proves.
_PAYLOAD_ALREADY_BLOCKED_BY_WRAPPER = [
    "::ffff:127.0.0.1", "::ffff:10.0.0.1",                # v4-mapped
    "2002:7f00:1::1",                                     # 6to4
]
# term 4: wrapper passes AND the embedded v4 is global -- only `not is_multicast`
# on the PAYLOAD refuses it. This is the single row that kills the
# `emb.is_global`-only mutant; nothing else in the table does.
_PAYLOAD_MULTICAST = ["64:ff9b::224.0.0.1"]
# allowed: no embedding, and embedding whose payload passes BOTH terms. Without the
# second group, a mutant reading "any extractable v4 blocks" survives the whole
# table while blocking every public board on a DNS64 network.
_ALLOWED_PLAIN = ["192.88.99.1", "2001:20::1"]
_ALLOWED_EMBEDDED = ["64:ff9b::192.88.99.1", "::ffff:192.88.99.1"]


@pytest.mark.parametrize("addr", _WRAPPER_NOT_GLOBAL + _WRAPPER_MULTICAST
                         + _PAYLOAD_NOT_GLOBAL + _PAYLOAD_ALREADY_BLOCKED_BY_WRAPPER
                         + _PAYLOAD_MULTICAST)
def test_blocked_addresses(addr):
    v = urlguard.verdict("host.invalid", [addr], allow_hosts=_EMPTY)
    assert not v.allowed
    assert v.reason == urlguard.BLOCKED_ADDRESS
    assert v.host == "host.invalid"


@pytest.mark.parametrize("addr", _ALLOWED_PLAIN + _ALLOWED_EMBEDDED)
def test_allowed_addresses(addr):
    v = urlguard.verdict("host.invalid", [addr], allow_hosts=_EMPTY)
    assert v.allowed and v.reason == ""


def test_any_blocked_answer_blocks_the_whole_host():
    # A multi-A-record host must not smuggle a private answer through by ordering.
    for answers in (["192.88.99.1", "127.0.0.1"], ["127.0.0.1", "192.88.99.1"]):
        assert not urlguard.verdict("h.invalid", answers, allow_hosts=_EMPTY).allowed


def test_no_addresses_blocks():
    v = urlguard.verdict("h.invalid", [], allow_hosts=_EMPTY)
    assert not v.allowed and v.reason == urlguard.RESOLVE_EMPTY


def test_unparseable_answer_blocks():
    v = urlguard.verdict("h.invalid", ["not-an-address"], allow_hosts=_EMPTY)
    assert not v.allowed and v.reason == urlguard.BLOCKED_ADDRESS


# --- allowlist grants --------------------------------------------------------

def test_exact_hostname_grant_admits_an_otherwise_blocked_host():
    allow = urlguard.parse_allow_hosts(["jobs.invalid"])
    assert urlguard.verdict("jobs.invalid", ["10.0.0.1"], allow_hosts=allow).allowed


def test_a_subdomain_of_a_granted_host_is_not_admitted():
    # On a DENYlist a suffix match widens the safe direction; on an ALLOWlist it
    # hands evil.example.jobs.invalid the grant meant for jobs.invalid.
    allow = urlguard.parse_allow_hosts(["jobs.invalid"])
    assert not urlguard.verdict(
        "evil.example.jobs.invalid", ["10.0.0.1"], allow_hosts=allow).allowed


def test_a_trailing_dot_host_is_admitted_by_a_plain_grant():
    # urlparse('http://jobs.invalid./x').hostname is 'jobs.invalid.'
    allow = urlguard.parse_allow_hosts(["jobs.invalid"])
    assert urlguard.verdict("jobs.invalid.", ["10.0.0.1"], allow_hosts=allow).allowed


def test_www_is_not_stripped_when_matching_a_grant():
    # _host does not strip www., so a grant for the bare name must NOT admit it.
    allow = urlguard.parse_allow_hosts(["jobs.invalid"])
    assert not urlguard.verdict(
        "www.jobs.invalid", ["10.0.0.1"], allow_hosts=allow).allowed


def test_cidr_grant_admits_inside_and_refuses_outside():
    allow = urlguard.parse_allow_hosts(["10.0.0.0/8"])
    assert urlguard.verdict("h.invalid", ["10.1.2.3"], allow_hosts=allow).allowed
    assert not urlguard.verdict("h.invalid", ["192.168.1.1"], allow_hosts=allow).allowed


def test_bare_ip_grant_is_a_single_address_network():
    allow = urlguard.parse_allow_hosts(["10.0.0.1"])
    assert urlguard.verdict("h.invalid", ["10.0.0.1"], allow_hosts=allow).allowed
    assert not urlguard.verdict("h.invalid", ["10.0.0.2"], allow_hosts=allow).allowed


def test_a_grant_must_cover_every_blocked_answer():
    allow = urlguard.parse_allow_hosts(["10.0.0.0/8"])
    assert not urlguard.verdict(
        "h.invalid", ["10.1.2.3", "192.168.1.1"], allow_hosts=allow).allowed


# --- the premises the mutation witnesses rest on -----------------------------

def test_fixture_addresses_are_globally_classified():
    """Pin the CPython classifications the witnesses depend on.

    Not just the allowed fixtures: the load-bearing premise is that four BLOCKED
    ones are is_global=True. If a future CPython reclassified any of them, the
    base predicate alone would block that row, the table would stay green, and the
    named witness would silently stop reddening -- the "a comment is not a check"
    shape from #30's inv-001.
    """
    g = lambda a: ipaddress.ip_address(a).is_global
    m = lambda a: ipaddress.ip_address(a).is_multicast
    # premise for the `not is_multicast` witness on the WRAPPER
    assert g("224.0.0.1") and m("224.0.0.1")
    assert g("ff02::1") and m("ff02::1")
    # premise for the _embedded_v4 witness: these two wrappers must be is_global,
    # or the hole they represent does not exist and the witness stops witnessing.
    assert g("64:ff9b::7f00:1"), "NAT64 wrapper must be global or the hole is not real"
    assert g("::127.0.0.1"), "v4-compatible wrapper must be global"
    # ...and the converse premise for the belt-and-braces rows: they are blocked by
    # the WRAPPER today, which is why they are not witnesses. If CPython ever made one
    # global, it would become a real hole and this assertion is what would say so.
    for a in ("::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::1"):
        assert not g(a), a
    # premise for the `not is_multicast` witness on the PAYLOAD
    assert g("64:ff9b::224.0.0.1")
    # premise for every allowed row
    for a in ("192.88.99.1", "2001:20::1", "::ffff:192.88.99.1", "64:ff9b::192.88.99.1"):
        assert g(a) and not m(a), a
