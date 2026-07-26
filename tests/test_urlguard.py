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


# --- host extraction ---------------------------------------------------------

def test_www_is_preserved():
    """The guard must check the host the BROWSER fetches.

    track/receipt._host ends with `return host[4:] if host.startswith("www.")
    else host` -- correct there, a total bypass here: strip it and the guard
    resolves attacker.invalid while the browser fetches www.attacker.invalid.
    Point the two names at different addresses and the check means nothing.
    """
    assert urlguard._host("http://www.jobs.invalid/x") == "www.jobs.invalid"


# Written as an explicit escape, NOT as a literal character. U+212A is visually
# identical to ASCII "K", so a literal is silently corrupted by transcription --
# a reviewer reproduced exactly that failure. The premise assertion below makes a
# lost codepoint redden as a fixture problem rather than as a guard problem.
KELVIN = "\u212a"


def test_the_kelvin_fixture_is_the_confusable_not_ascii_k():
    assert not KELVIN.isascii() and KELVIN.lower() == "k"


def test_a_non_ascii_host_is_refused_before_any_lowering():
    """U+212A KELVIN folds to ASCII 'k' under str.lower().

    urlparse().hostname is ITSELF lowercased, so
    urlparse(f"http://{KELVIN}example.invalid/x").hostname is 'kexample.invalid',
    which .isascii() returns True for. A non-ASCII check applied to .hostname can
    therefore NEVER fire -- the inert-test shape that shipped once in #10. The
    check must run on the raw AUTHORITY, before urlparse lowercases it.
    """
    url = f"http://{KELVIN}example.invalid/x"
    assert urlguard._host(url) == ""
    # and the confusable must not be silently accepted as the real host
    assert urlguard._host(url) != "kexample.invalid"


def test_a_non_ascii_path_does_not_refuse_the_url():
    """The check is scoped to the AUTHORITY, not the whole url.

    Checking the raw url would refuse every posting whose path or query carries a
    non-ASCII byte -- entirely normal on a French or German board. Because
    check_url returns on `not host` BEFORE consulting the allowlist, such a lead
    would be blocked permanently with no remedy: the guard's default silently
    changing which jobs a user sees, which is the 672ad2a direction.
    """
    assert urlguard._host("https://jobs.example/careers/développeur") == "jobs.example"
    assert urlguard._host("https://jobs.example/x?q=café") == "jobs.example"


@pytest.mark.parametrize("url,expected", [
    ("http://user@evil.example@127.0.0.1/", "127.0.0.1"),  # userinfo: last @ wins
    ("http://[::1]:8080/x", "::1"),                        # brackets stripped
    ("http://jobs.invalid./x", "jobs.invalid."),            # trailing dot preserved
    ("HTTPS://Example.INVALID/a", "example.invalid"),       # case folded
    ("http:///etc/passwd", ""),                             # no host
    ("https://[abc", ""),                                   # malformed literal, no raise
    ("", ""),
])
def test_host_extraction(url, expected):
    assert urlguard._host(url) == expected


# --- check_url ---------------------------------------------------------------

def _fake_resolve(mapping, *, default=None):
    """A resolver over a fixed map. Unmapped hosts raise OSError unless `default`."""
    def _r(host):
        if host in mapping:
            return list(mapping[host])
        if default is not None:
            return list(default)
        raise OSError(f"unmapped {host!r}")
    return _r


_GLOBAL = _fake_resolve({}, default=["192.88.99.1"])


@pytest.mark.parametrize("url", [
    "ftp://host.invalid/x",
    "file://allowed.invalid/etc/passwd",
])
def test_non_http_schemes_block_even_when_the_host_resolves_globally(url):
    """These two fixtures CARRY A HOST, which is the point.

    Most non-http(s) urls (file:///x, javascript:alert(1)) also yield hostname
    None, so using one of those to test the SCHEME check passes whether or not
    that check exists -- five of six originally-proposed cases were inert this
    way. The resolver maps every host to a global address so the address rule
    cannot be what refuses these.
    """
    v = urlguard.check_url(url, allow_hosts=_EMPTY, resolve=_GLOBAL)
    assert not v.allowed and v.reason == urlguard.SCHEME


def test_a_url_with_no_host_blocks_with_its_own_slug():
    v = urlguard.check_url("http:///etc/passwd", allow_hosts=_EMPTY, resolve=_GLOBAL)
    assert not v.allowed and v.reason == urlguard.NO_HOST


def test_the_allowlist_never_grants_a_scheme():
    allow = urlguard.parse_allow_hosts(["allowed.invalid"])
    v = urlguard.check_url("file://allowed.invalid/etc/passwd",
                           allow_hosts=allow, resolve=_GLOBAL)
    assert not v.allowed and v.reason == urlguard.SCHEME


def test_uppercase_scheme_and_host_pass():
    v = urlguard.check_url("HTTPS://Example.INVALID/a",
                           allow_hosts=_EMPTY, resolve=_GLOBAL)
    assert v.allowed


def test_a_resolver_raising_oserror_blocks():
    def _boom(host):
        raise OSError("nope")
    v = urlguard.check_url("https://h.invalid/x", allow_hosts=_EMPTY, resolve=_boom)
    assert not v.allowed and v.reason == urlguard.RESOLVE_FAILED


def test_a_resolver_raising_a_non_oserror_propagates():
    """The catch is narrow ON PURPOSE.

    A bare `except Exception` would convert a BUG IN THE GUARD into a "blocked"
    verdict, and would also swallow the session-wide DNS guard, which is how a
    forgotten wiring stayed green through a review round.
    """
    class _Boom(Exception):
        pass

    def _boom(host):
        raise _Boom("a bug, not a resolution failure")
    with pytest.raises(_Boom):
        urlguard.check_url("https://h.invalid/x", allow_hosts=_EMPTY, resolve=_boom)


def test_zero_addresses_blocks():
    v = urlguard.check_url("https://h.invalid/x", allow_hosts=_EMPTY,
                           resolve=lambda h: [])
    assert not v.allowed and v.reason == urlguard.RESOLVE_EMPTY


def test_obfuscated_hosts_reach_the_resolver_verbatim():
    """The guard must NEVER classify a host by parsing it as an IP literal.

    getaddrinfo normalises exactly the forms that exist to defeat that:
    2130706433 / 0x7f000001 / 127.1 all resolve to 127.0.0.1, while
    ipaddress.ip_address("2130706433") raises. So we resolve, always.
    """
    seen = []

    def _spy(host):
        seen.append(host)
        return ["127.0.0.1"]
    for h in ("2130706433", "0x7f000001", "127.1"):
        v = urlguard.check_url(f"http://{h}/", allow_hosts=_EMPTY, resolve=_spy)
        assert not v.allowed and v.reason == urlguard.BLOCKED_ADDRESS
    assert seen == ["2130706433", "0x7f000001", "127.1"]


def test_a_blocked_verdict_carries_the_host_for_the_log_line():
    v = urlguard.check_url("http://h.invalid/x", allow_hosts=_EMPTY,
                           resolve=_fake_resolve({"h.invalid": ["127.0.0.1"]}))
    assert v.host == "h.invalid"


def test_a_malformed_ipv6_literal_returns_a_verdict_rather_than_raising():
    """`_host` swallows urlsplit's ValueError, but check_url parses the scheme too.

    An unguarded second parse would RAISE out of the guard on "https://[abc" --
    the tested-function-is-not-the-called-function shape.
    """
    v = urlguard.check_url("https://[abc", allow_hosts=_EMPTY, resolve=_GLOBAL)
    assert not v.allowed and v.reason == urlguard.NO_HOST


def test_a_scheme_failure_still_reports_its_host():
    """An earlier draft asserted "on a scheme failure there is no host", which
    contradicted its own fixture rationale: ftp://host.invalid/x HAS one, and
    discarding it would strip the security log of the half the operator needs."""
    v = urlguard.check_url("ftp://host.invalid/x", allow_hosts=_EMPTY, resolve=_GLOBAL)
    assert v.host == "host.invalid"


# --- config ------------------------------------------------------------------

def test_dossier_allow_hosts_defaults_empty():
    from sluice.core.config import Config
    assert Config().dossier_allow_hosts == []


def test_dossier_allow_hosts_defaults_empty_through_the_loader(monkeypatch):
    # Clear the env or this silently reads the developer's own config and passes
    # for the wrong reason -- the trap already documented in the neutral-defaults tests.
    from sluice.core.config import load_config
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_config(None).dossier_allow_hosts == []


def test_dossier_allow_hosts_round_trips(tmp_path):
    from sluice.core.config import load_config
    p = tmp_path / "sluice.local.yaml"
    p.write_text('dossier_allow_hosts: ["jobs.invalid", "10.0.0.0/8"]\n')
    assert load_config(str(p)).dossier_allow_hosts == ["jobs.invalid", "10.0.0.0/8"]


def test_a_malformed_allowlist_raises_at_load(tmp_path):
    from sluice.core.config import load_config
    p = tmp_path / "sluice.local.yaml"
    p.write_text('dossier_allow_hosts: ["10.0.0.300"]\n')
    with pytest.raises(ValueError) as ei:
        load_config(str(p))
    assert "dossier_allow_hosts[0]" in str(ei.value)
    assert "10.0.0.300" not in str(ei.value)


def test_a_scalar_allowlist_raises_at_load(tmp_path):
    """A YAML scalar must raise, not list()-explode into per-character grants.

    The scalar is DOTLESS on purpose. An earlier version used `jobs.invalid`,
    whose explosion contains ".", which is IP-shaped and raises -- so the test
    passed while the guard it was written for did not exist at all.
    """
    from sluice.core.config import load_config
    p = tmp_path / "sluice.local.yaml"
    p.write_text('dossier_allow_hosts: myboard\n')
    with pytest.raises(ValueError):
        load_config(str(p))
