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
