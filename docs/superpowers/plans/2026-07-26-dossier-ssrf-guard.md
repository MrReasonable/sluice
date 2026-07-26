# Dossier SSRF Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `Sluice.dossier_cache()` from navigating a browser to a scraped lead URL that points at a non-http(s) scheme or a non-globally-routable address, without regressing any public-board fetch.

**Architecture:** A new pure module `sluice/core/urlguard.py` decides scheme/host/address policy; DNS is an injected collaborator so no test resolves. The `dossier_cache` closure pre-checks before opening a tab and re-checks the landed URL before reading the body, raising `DossierBlocked` (caught by both consumers' existing per-item handlers) rather than returning an empty dossier.

**Tech Stack:** Python 3.12+, stdlib only (`socket`, `ipaddress`, `urllib.parse`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-dossier-ssrf-design.md` — approved after three `/review-plan` rounds. Read it before starting; it records *why* several obvious implementations are wrong.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency. `socket`, `ipaddress`, `urllib.parse` only.
- **Every test is offline and synthetic.** No test may resolve DNS, open a browser, or touch a real vault.
- **Neutrality:** no real hostnames, absolute paths, employer names or addresses assigned to a real operator or network in `sluice/` or `tests/`. Fixture hosts use the RFC-reserved `.example` / `.invalid` family. Stated by property rather than by list, because the address table legitimately needs globally-*classified* literals: the two sanctioned public fixtures are `192.88.99.1` (RFC 3068, withdrawn by RFC 7526) and `2001:20::1` (RFC 7343 ORCHIDv2); beyond those, only structural wrappers (NAT64 well-known, v4-compatible, 6to4) whose embedded payload is itself reserved.
- **Reason slugs are a closed set**, defined once in `urlguard` and imported everywhere else: `scheme`, `no-host`, `resolve-failed`, `resolve-empty`, `blocked-address`, `not-settled`, `landed-blocked`, `landed-unreadable`, `no-tab`, `body-unreadable`. There is deliberately **no** `non-ascii` slug. (The last two were added at plan review: the two paths that previously fell through to a silently-cached empty dossier — see Task 8.)
- **`DossierBlocked` carries the reason slug only** — never a URL, host, or config entry. `cv/engine.py:70` logs `str(e)` verbatim.
- **Config validation never echoes user values.** Report key + entry **index** + expected shape. Always `raise ... from None`.
- **Comments explain *why*.** Match the surrounding density; several comments in this codebase encode real incidents.
- **Conventional commits**, scope `core` unless the task says otherwise.
- **Run `python -m pytest` before every commit.** The suite is ~2s; there is no reason not to.
- **Never assert an absolute suite count.** Expected outcomes are stated as "green, no new failures" plus the new tests named. A hardcoded total drifts with every parametrize edit and reads as a failure when it is merely stale.
- **Narrow runs name node IDs, never `-k`.** A `-k` selector that matches nothing exits 0 and prints "N deselected", which is success-shaped output that verifies nothing.

## Two traps this spec was written to prevent

Both were found by review after being introduced by "follow the existing precedent" reasoning. They are called out again at the point of use:

1. **`receipt._host`'s last line strips `www.`** — copying it makes the guard resolve `attacker.invalid` while the browser fetches `www.attacker.invalid`. Total bypass. (Task 4)
2. **`self._sleep` is `None` in production** — every `cli.py` construction is bare `Sluice(config)`. The closure must touch no collaborator that can be `None`. (Task 8)

## File Structure

| File | Responsibility |
|---|---|
| `sluice/core/urlguard.py` (new) | All policy: allowlist parsing, address classification, host extraction, scheme check, the injected resolver. Pure except `_resolve`. |
| `sluice/core/config.py` | `dossier_allow_hosts` field + loader validation. |
| `sluice/core/app.py` | `resolve_host` collaborator, `_COLLABORATORS`, the guarded `dossier_cache` closure. |
| `sluice/core/plugins.py` | `UnknownAdapter` gains an optional `hint`. |
| `sluice/core/protocols.py` | `Fetcher` docstring records the changed `evaluate` contract. |
| `sluice.yaml.example` | Documents `dossier_allow_hosts`. |
| `tests/conftest.py` | Session-wide `getaddrinfo` guard (fixture + exception only). |
| `tests/test_hermeticity.py` (new) | Asserts that guard is installed. Separate file because pytest's `python_files = test_*.py` never collects `conftest.py`. |
| `tests/test_urlguard.py` (new) | The pure policy tables. |
| `tests/test_dossier_guard.py` (new) | Closure + consumer behaviour. |
| `tests/harness/config.py`, `tests/functional/conftest.py`, `tests/test_app_operations.py` | Resolver wiring for the six existing tests that reach the closure. |
| `docs/ARCHITECTURE.md` | Module inventory, collaborator enumeration, the `:298-302` trigger. |

---

### Task 1: Hermeticity guard

Lands first so every later task is protected. It must raise `BaseException` — a plain `Exception` would be swallowed by whichever consumer handles the dossier failure (`cv/engine.py:66-71`, or triage's per-item handler), which is exactly how a forgotten wiring shipped green in review round 1. (`check_url`'s own catch is `OSError`-only and would NOT swallow it — an earlier draft of this line claimed it would, which was false.)

**Files:**
- Modify: `tests/conftest.py` (append the exception + fixture)
- Create: `tests/test_hermeticity.py` (the assertion)

> **Why two files.** `pyproject.toml` sets `testpaths = ["tests"]` with no `python_files`
> override, so pytest's default `test_*.py` **never collects `conftest.py`** (verified: 0 tests
> collected from it). An assertion written there runs only when someone types its node ID by hand —
> which is the precise inertness this task exists to prevent, one level up.

**Interfaces:**
- Consumes: nothing.
- Produces: `tests.conftest.DnsUsedInTests` (a `BaseException` subclass), and an autouse session fixture that makes `socket.getaddrinfo` raise it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hermeticity.py`:

```python
"""The suite must never resolve DNS. See tests/conftest.py for the guard itself."""
import socket

import pytest

from tests.conftest import DnsUsedInTests


def test_the_suite_cannot_resolve_dns():
    """The guard fixture below is load-bearing, so assert it is actually installed.

    Without this, the fixture could be silently broken (a typo'd name, a scope
    change) and the whole suite would go back to being able to resolve, which is
    how a forgotten `resolve_host=` wiring stayed green through a review round.

    It lives in this file rather than in conftest.py because pytest does not
    collect conftest.py -- an assertion there would itself be inert.
    """
    with pytest.raises(DnsUsedInTests):
        socket.getaddrinfo("anything.invalid", None)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_hermeticity.py -v`
Expected: FAIL at **collection** — `ImportError: cannot import name 'DnsUsedInTests' from 'tests.conftest'`.

- [ ] **Step 3: Add the guard**

Append to `tests/conftest.py` (the exception and fixture only — the assertion stays in its own file):

```python
class DnsUsedInTests(BaseException):
    """Raised when a test tries to resolve a hostname.

    Subclasses BaseException, NOT Exception, and that is load-bearing. A plain
    Exception would be swallowed on the dossier path by whichever consumer calls
    it -- cv/engine.py's per-item `except Exception` (which proceeds with an
    empty JD) or triage/engine.py's per-item `except Exception` (which records
    report.failures and skips the lead). An implementer who forgot to inject `resolve_host=` at
    one of the three test wiring sites would therefore see a GREEN suite that
    was doing real DNS on every run. That happened in review, which is why this
    exists at all.
    """


@pytest.fixture(scope="session", autouse=True)
def _forbid_dns():
    """Make socket.getaddrinfo raise for the whole session.

    Verified before writing: the suite performs zero DNS today, so this changes
    nothing that currently passes. monkeypatch is function-scoped, so the
    set/restore is done by hand.
    """
    import socket
    real = socket.getaddrinfo

    def _raise(*args, **kwargs):
        raise DnsUsedInTests(
            "tests must not resolve DNS -- inject resolve_host= instead")

    socket.getaddrinfo = _raise
    try:
        yield
    finally:
        socket.getaddrinfo = real
```

- [ ] **Step 4: Run the new test, then the whole suite**

Run: `python -m pytest tests/test_hermeticity.py -v`
Expected: PASS, 1 test collected. **If it collects 0, the file is misnamed** — that is the failure this split exists to prevent.

Run: `python -m pytest`
Expected: green, with exactly one new test. **If anything else fails, stop** — it means some existing test does resolve, which contradicts the spec's premise and must be reported, not worked around.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_hermeticity.py
git commit -m "test: forbid DNS resolution suite-wide (#18)"
```

---

### Task 2: `urlguard` — allowlist parsing and validation

Pure, self-contained, and lands before `verdict` so `verdict` can consume a parsed `AllowList` rather than re-parsing strings.

**Files:**
- Create: `sluice/core/urlguard.py`
- Test: `tests/test_urlguard.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `AllowList` — frozen dataclass, fields `hosts: frozenset[str]`, `networks: tuple`.
  - `parse_allow_hosts(entries, *, key: str = "dossier_allow_hosts") -> AllowList` — raises `ValueError` on any malformed entry.
  - Slug constants `SCHEME`, `NO_HOST`, `RESOLVE_FAILED`, `RESOLVE_EMPTY`, `BLOCKED_ADDRESS`, `NOT_SETTLED`, `LANDED_BLOCKED`, `LANDED_UNREADABLE`.
  - `DossierBlocked(Exception)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_urlguard.py`:

```python
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


@pytest.mark.parametrize("entry", ["db", "cafe", "abc", "abba", "jobs.invalid"])
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: all FAIL — `ModuleNotFoundError: No module named 'sluice.core.urlguard'`.

- [ ] **Step 3: Create the module**

Create `sluice/core/urlguard.py`:

```python
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
import socket
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: all PASS.

Run: `python -m pytest && ruff check sluice tests`
Expected: green, ruff clean. (Do not check an absolute total — see Global Constraints.)

- [ ] **Step 5: Commit**

```bash
git add sluice/core/urlguard.py tests/test_urlguard.py
git commit -m "feat(core): allowlist parsing for the dossier url guard (#18)"
```

---

### Task 3: `urlguard` — address classification and `verdict`

The heart of the guard. The test table is organised by **failure cause**, not by named shape: the previous two organisations each let a real mutant survive.

**Files:**
- Modify: `sluice/core/urlguard.py`
- Test: `tests/test_urlguard.py`

**Interfaces:**
- Consumes: `AllowList` (Task 2).
- Produces:
  - `UrlVerdict` — frozen dataclass, fields `allowed: bool`, `reason: str = ""`, `host: str = ""`.
  - `_embedded_v4(addr) -> ipaddress.IPv4Address | None`
  - `verdict(host: str, addrs, *, allow_hosts: AllowList) -> UrlVerdict` — `addrs` is an iterable of address strings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_urlguard.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: the new tests FAIL with `AttributeError: module 'sluice.core.urlguard' has no attribute 'verdict'` — **except** `test_fixture_addresses_are_globally_classified`, which uses only `ipaddress` and passes immediately. That is correct: it pins a CPython premise, not this module's behaviour. Task 2's tests still pass.

- [ ] **Step 3: Implement**

Append to `sluice/core/urlguard.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: all PASS.

Run: `python -m pytest && ruff check sluice tests`
Expected: green.

- [ ] **Step 5: Witness the two mutants unique to this task**

Content-address the caches once (per CLAUDE.md):

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Mutant A — delete the payload's multicast term. In `_routable`, change
`return embedded.is_global and not embedded.is_multicast` to `return embedded.is_global`.

Run: `python -m pytest "tests/test_urlguard.py::test_blocked_addresses[64:ff9b::224.0.0.1]" -v`
Expected: **FAIL**. Then run the whole file and confirm this is the *only* failing row — that is what proves the row is not redundant. Restore the line.

Mutant B — delete the embedding recheck. In `_routable`, delete the three lines from `embedded = _embedded_v4(addr)` to `return embedded.is_global and not embedded.is_multicast`.

Run: `python -m pytest "tests/test_urlguard.py::test_blocked_addresses[64:ff9b::7f00:1]" "tests/test_urlguard.py::test_blocked_addresses[::127.0.0.1]" -v`
Expected: both **FAIL**. Then run the whole file and confirm the three
`_PAYLOAD_ALREADY_BLOCKED_BY_WRAPPER` rows stay **GREEN** — that is what proves they are
regression pins rather than witnesses, and why the two groups are filed separately. Restore.

Run `python -m pytest` after restoring; expected green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/urlguard.py tests/test_urlguard.py
git commit -m "feat(core): address-class policy for the dossier url guard (#18)"
```

---

### Task 4: `urlguard` — host extraction

**Read the warning in Step 3 before writing the code.** This is the trap that would ship a total bypass.

**Files:**
- Modify: `sluice/core/urlguard.py`
- Test: `tests/test_urlguard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_host(url: str) -> str` — the host exactly as the browser will see it, or `""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_urlguard.py`:

```python
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
    assert urlguard._host("https://jobs.example/careers/d\u00e9veloppeur") == "jobs.example"
    assert urlguard._host("https://jobs.example/x?q=caf\u00e9") == "jobs.example"


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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: the new `_host` tests FAIL — `AttributeError: module 'sluice.core.urlguard' has no attribute '_host'`. (Whole-file, not `-k`: a selector matching nothing exits 0 and looks like success.)

- [ ] **Step 3: Implement**

Append to `sluice/core/urlguard.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: all PASS.

Run: `python -m pytest && ruff check sluice tests`

- [ ] **Step 5: Witness the two mutants**

Mutant A (a **move**) — relocate the ascii check from the raw `.netloc` onto the parsed `.hostname`:

```python
    value = (url or "").strip()
    if not value:
        return ""
    try:
        host = urlsplit(value).hostname or ""
    except ValueError:
        return ""
    return host if host.isascii() else ""
```

Run: `python -m pytest tests/test_urlguard.py::test_a_non_ascii_host_is_refused_before_any_lowering -v`
Expected: **FAIL**. Restore.

Mutant B (the one **addition** in this plan, and the exception proves the rule: it guards an *omission*, so there is nothing to delete) — append `receipt._host`'s last line:

```python
        host = urlparse(value).hostname or ""
        return host[4:] if host.startswith("www.") else host
```

Run: `python -m pytest tests/test_urlguard.py::test_www_is_preserved -v`
Expected: **FAIL**. Restore, and re-run the full suite.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/urlguard.py tests/test_urlguard.py
git commit -m "feat(core): host extraction for the dossier url guard (#18)"
```

---

### Task 5: `urlguard` — `check_url` and the injected resolver

**Files:**
- Modify: `sluice/core/urlguard.py`
- Test: `tests/test_urlguard.py`

**Interfaces:**
- Consumes: `_host`, `verdict`, `AllowList`, the slug constants.
- Produces:
  - `_resolve(host: str) -> list[str]`
  - `check_url(url: str, *, allow_hosts: AllowList, resolve=_resolve) -> UrlVerdict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_urlguard.py`:

```python
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
    verdict. (It would NOT swallow the session-wide DNS guard: that raises a
    BaseException subclass, which `except Exception` never catches.)
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
```

- [ ] **Step 2: Run and watch them fail**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: the new tests FAIL — `AttributeError: module 'sluice.core.urlguard' has no attribute 'check_url'`. (Whole-file, not `-k check_url`: no test name contains that string, so the selector would deselect everything and exit 0.)

- [ ] **Step 3: Implement**

Append to `sluice/core/urlguard.py`:

```python
_ALLOWED_SCHEMES = ("http", "https")


def _resolve(host: str) -> list[str]:
    """Production resolver: every address `host` answers with.

    The one impure function in this module, and injectable at every call site so
    no test resolves.
    """
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_url(url: str, *, allow_hosts: AllowList, resolve=_resolve) -> UrlVerdict:
    """May the dossier fetcher navigate to `url`?

    Impure ONLY in that it calls `resolve`. The scheme and empty-host refusals live
    here because they precede host extraction; everything after is `verdict`.
    """
    try:
        # One guarded parse for the scheme. urlsplit raises ValueError on a
        # mismatched bracket ("https://[abc"), and an unguarded call here would
        # raise straight out of the guard even though _host handles it.
        scheme = (urlsplit(url or "").scheme or "").lower()
    except ValueError:
        return UrlVerdict(False, NO_HOST, "")
    host = _host(url)
    if scheme not in _ALLOWED_SCHEMES:
        # Checked BEFORE the host refusal so a scheme fixture that carries a host
        # (ftp://host.invalid/x) reports `scheme`, and so the allowlist -- consulted
        # only inside verdict() -- can never grant a scheme.
        return UrlVerdict(False, SCHEME, host)
    if not host:
        return UrlVerdict(False, NO_HOST, "")
    try:
        addrs = resolve(host)
    except OSError:
        # Narrow on purpose: a bare `except Exception` would turn a BUG IN THIS
        # MODULE into a tidy "blocked" verdict. socket.gaierror subclasses OSError,
        # so the real failure mode is covered.
        return UrlVerdict(False, RESOLVE_FAILED, host)
    return verdict(host, addrs, allow_hosts=allow_hosts)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_urlguard.py -v` — all PASS.
Run: `python -m pytest && ruff check sluice tests` — green.

- [ ] **Step 5: Witness the scheme/host independence**

Mutant A — delete the scheme check (the `if scheme not in _ALLOWED_SCHEMES` block).

Run:
```bash
python -m pytest tests/test_urlguard.py::test_non_http_schemes_block_even_when_the_host_resolves_globally \
                 tests/test_urlguard.py::test_the_allowlist_never_grants_a_scheme -v
```
Expected: **FAIL**. Then run `tests/test_urlguard.py::test_a_url_with_no_host_blocks_with_its_own_slug` and confirm it still **PASSES** — that is what proves the two refusals are independent. Restore.

Mutant B — delete the `if not host:` block.

Run: `python -m pytest tests/test_urlguard.py::test_a_url_with_no_host_blocks_with_its_own_slug -v`
Expected: **FAIL**, and no scheme case fails. Restore.

Mutant C — widen `except OSError` to `except Exception`.

Run: `python -m pytest tests/test_urlguard.py::test_a_resolver_raising_a_non_oserror_propagates -v`
Expected: **FAIL**. Restore, re-run the suite.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/urlguard.py tests/test_urlguard.py
git commit -m "feat(core): check_url and the injected resolver (#18)"
```

---

### Task 6: Config field, loader validation, and documentation

**Files:**
- Modify: `sluice/core/config.py`
- Modify: `sluice.yaml.example`
- Modify: `tests/test_sluice_neutral_defaults.py` (one comment + one assertion)
- Test: `tests/test_urlguard.py` (config round-trip lives with the guard's tests)

**Interfaces:**
- Consumes: `urlguard.parse_allow_hosts` (Task 2).
- Produces: `Config.dossier_allow_hosts: list` (default `[]`), validated by `load_config`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_urlguard.py`:

```python
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
```

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_urlguard.py -v`
Expected: the new config tests FAIL — `AttributeError: 'Config' object has no attribute 'dossier_allow_hosts'`.

- [ ] **Step 3: Add the field and validation**

In `sluice/core/config.py`, add to the `Config` dataclass immediately after the `baseline_rel` block (keeping it beside the other seam-level knobs):

```python
    # Hosts/CIDRs exempt from the dossier fetcher's SSRF guard (#18). A SAFETY
    # allowlist, not a preference gate: empty means "no exceptions granted", NOT
    # "match nothing" -- an unconfigured install still fetches every public url,
    # because the address rule admits them, not this list. Lives on the root
    # Config, not TriageConfig/CvConfig, because dossier_cache is called from BOTH
    # sub-apps and a security policy that differs between them is a bug.
    dossier_allow_hosts: list = field(default_factory=list)
```

In `load_config`, before the `return Config(...)`:

```python
    # Validate here so a malformed entry fails at CONSTRUCTION, naming the key and
    # the entry's index -- never its value. Deliberately NOT _str_list: that raises
    # with `got {value!r}`, i.e. the whole list, and a config file is one of the few
    # places a user's real private hostnames legitimately live.
    from sluice.core.urlguard import parse_allow_hosts
    raw_allow = data.get("dossier_allow_hosts")
    # Pass the RAW value: `list(...)` first would explode a YAML scalar into one
    # entry per character BEFORE parse_allow_hosts' isinstance guard could fire, so
    # `dossier_allow_hosts: myboard` would load silently as seven inert one-character
    # grants on a SAFETY allowlist. That is the bug class `_str_list` exists for.
    parse_allow_hosts([] if raw_allow is None else raw_allow)
    allow = list(raw_allow or [])   # coerce only AFTER validation has passed
```

and add to the `Config(...)` call:

```python
                  dossier_allow_hosts=allow,
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_urlguard.py -v` — PASS.
Run: `python -m pytest` — green.

- [ ] **Step 5: Document the knob and the sweep's inverted "empty"**

In `sluice.yaml.example`, after the `baseline_rel: My CV/CV.md` line:

```yaml
# Hosts the dossier fetcher may reach even though they are not globally routable.
# The fetcher follows lead urls scraped off job boards, so by default it refuses
# non-http(s) schemes and any address that is not globally routable (loopback,
# private, link-local, cloud-metadata, ...). Empty grants no exceptions; it does
# NOT block public urls, which are admitted by the address rule rather than by
# this list. Set it only if you deliberately run a board on your own network.
# Entries are a hostname (exact, no subdomains) or a CIDR / bare IP.
# dossier_allow_hosts:
#   - jobs.invalid
#   - 10.0.0.0/8
```

In `tests/test_sluice_neutral_defaults.py`, inside `test_ingest_defaults_carry_no_preference`, after the `dedupe_title_noise_words` assertion:

```python
    # #18: covered by the value-keyed sweep below as a list-defaulting field, and it
    # must default empty -- but its "empty" is INVERTED relative to every other entry
    # here. For accept_titles, empty means "pass everything through"; for this SAFETY
    # allowlist it means "grant no exceptions", and public urls stay fetchable because
    # of the address rule, not this list. Do not read the sweep as licence to loosen
    # the guard.
    assert c.dossier_allow_hosts == []
```

- [ ] **Step 6: Run everything and commit**

Run: `python -m pytest && ruff check sluice tests` — green (the sweep now covers the new field automatically).

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_sluice_neutral_defaults.py tests/test_urlguard.py
git commit -m "feat(core): dossier_allow_hosts config knob (#18)"
```

---

### Task 7: Composition root — the `resolve_host` collaborator

**Files:**
- Modify: `sluice/core/plugins.py`
- Modify: `sluice/core/app.py`
- Test: `tests/test_app_injection.py` (append)

**Interfaces:**
- Consumes: nothing from `urlguard` yet.
- Produces:
  - `plugins.UnknownAdapter(seam, name, known, hint="")` — `hint` appended to the message.
  - `app._COLLABORATORS: tuple[str, ...]`
  - `Sluice.__init__(..., resolve_host=None)`, stored as `self._resolve_host`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_injection.py`:

```python
def test_a_typod_collaborator_names_collaborators_and_seams_separately():
    """ARCHITECTURE.md:298-302 pre-registered this tightening for "a third
    collaborator". resolve_host is it.

    The obvious implementation -- widening UnknownAdapter's `known` -- would print
    the collaborators AS SEAMS, erasing the distinction ARCHITECTURE.md:271-296
    exists to draw and implying config keys that do not exist.
    """
    from sluice.core import plugins
    from sluice.core.app import Sluice
    with pytest.raises(plugins.UnknownAdapter) as ei:
        Sluice(None, resolve_hosts=lambda h: [])
    msg = str(ei.value)
    assert "resolve_host" in msg and "sleep" in msg and "today" in msg
    assert "fetcher" in msg and "store" in msg
    assert "collaborator" in msg.lower()


def test_collaborators_tuple_matches_the_real_signature():
    """A stale tuple when a fourth collaborator lands would reinstate exactly the
    misdirection this tightening removes."""
    import inspect
    from sluice.core.app import Sluice, _COLLABORATORS
    kwonly = tuple(n for n, p in inspect.signature(Sluice.__init__).parameters.items()
                   if p.kind is p.KEYWORD_ONLY)
    assert _COLLABORATORS == kwonly


def test_an_unknown_seam_message_is_unchanged_for_existing_callers():
    from sluice.core import plugins
    e = plugins.UnknownAdapter("backend", "nope", ["a", "b"])
    assert str(e) == "unknown backend 'nope' (registered: a, b)"


def test_resolve_host_defaults_to_the_production_resolver():
    """Without this, a wiring that ALWAYS used a fake would ship green."""
    from sluice.core.app import Sluice
    from sluice.core import urlguard
    assert Sluice(None)._resolve_host is None
    assert Sluice(None, resolve_host=None)._resolve_host is None
    # and the closure resolves that None to the real one -- asserted in Task 8.
    assert callable(urlguard._resolve)
```

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_app_injection.py -k "collaborator or resolve_host or unknown_seam" -v`
Expected: FAIL — `_COLLABORATORS` does not exist; `resolve_hosts=` raises without the hint.

- [ ] **Step 3: Implement**

In `sluice/core/plugins.py`, change `UnknownAdapter.__init__`:

```python
    def __init__(self, seam: str, name: str, known, hint: str = ""):
        self.seam, self.name = seam, name
        known_names = ", ".join(sorted(known)) or "(none registered)"
        # KeyError's str() re-quotes its arg, so carry the message explicitly.
        self.message = f"unknown {seam} '{name}' (registered: {known_names})"
        # `hint` exists because this class HARDCODES its format, so a raise site
        # cannot otherwise say anything extra. Default empty, so every existing
        # caller's message is byte-identical.
        if hint:
            self.message += f". {hint}"
        super().__init__(self.message)
```

In `sluice/core/app.py`, after the `_SEAMS` definition:

```python
# The injected collaborators of Sluice.__init__ -- NOT seams. Used only to make a
# typo'd keyword point at the right fix: they are keyword-only params, so a typo
# never binds to them and always lands in **overrides, where it would otherwise be
# reported as an unknown SEAM. Pinned to the real signature by a guard test.
# (client/now_iso are Sluice.track() parameters and never reach **overrides.)
_COLLABORATORS = ("sleep", "today", "resolve_host")
```

Change `__init__`'s signature and the raise:

```python
    def __init__(self, config=None, *, sleep=None, today=None, resolve_host=None,
                 **overrides):
```

```python
        if unknown:
            raise plugins.UnknownAdapter(
                "seam override", unknown[0], _SEAMS,
                hint=(f"injected collaborators ({', '.join(_COLLABORATORS)}) are "
                      f"keyword-only parameters, not seam overrides"))
```

and after `self._sleep = sleep`:

```python
        # DNS for the dossier url guard (#18). None means urlguard's real resolver;
        # tests inject a fake so the suite never resolves. A collaborator, not a
        # seam: a registry entry is reachable from config, so a seam-resolved
        # resolver would put an off switch for the SSRF guard under a YAML key.
        self._resolve_host = resolve_host
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_app_injection.py -v` — PASS.
Run: `python -m pytest && ruff check sluice tests` — green.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/plugins.py sluice/core/app.py tests/test_app_injection.py
git commit -m "feat(core): resolve_host collaborator and collaborator-aware typo message (#18)"
```

---

### Task 8: Guard the closure, and keep the six existing tests green

The wiring is folded in here deliberately: activating the guard breaks the six tests that reach the closure, so the fix must land in the same commit.

**Files:**
- Modify: `sluice/core/app.py` (the `dossier_cache` closure)
- Modify: `sluice/core/protocols.py` (`Fetcher` docstring)
- Modify: `tests/harness/config.py`, `tests/functional/conftest.py`, `tests/test_app_operations.py`
- Test: `tests/test_dossier_guard.py` (create)

**Interfaces:**
- Consumes: `urlguard.check_url`, `urlguard.parse_allow_hosts`, `urlguard.DossierBlocked`, the slug constants, `Sluice._resolve_host`.
- Produces: `tests.harness.config.harness_resolve(host) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dossier_guard.py`:

```python
"""The dossier fetch closure's SSRF guard (#18)."""
import pytest

from sluice.core import urlguard
from sluice.core.app import Sluice
from sluice.core.config import Config

GLOBAL_ADDR = "192.88.99.1"     # RFC 3068, withdrawn by RFC 7526: global, no operator


_UNSET = object()


class _Tab:
    """A fake Fetcher recording its exact probe sequence."""

    def __init__(self, landed="https://jobs.invalid/x", body="JD BODY",
                 landed_result=_UNSET):
        self.landed, self.body, self.landed_result = landed, body, landed_result
        self.calls = []

    def create_tab(self, url):
        self.calls.append(("create_tab", url))
        return "tab-1"

    def evaluate(self, tid, js):
        self.calls.append(("evaluate", js))
        if js == "location.href":
            if self.landed_result is not _UNSET:
                return self.landed_result
            return {"result": self.landed}
        return {"result": self.body}

    def scroll(self, tid, amount):
        self.calls.append(("scroll", amount))

    def close_tab(self, tid):
        self.calls.append(("close_tab", tid))


@pytest.fixture
def role(titles):
    """A synthetic job title from the seeded pool, matching the convention in
    test_app_operations.py's dossier tests. The repo generates titles rather than
    hardcoding them so no real person's preferences leak into the suite."""
    return titles[0][0]


def _cache(tmp_path, fetcher, *, resolve=None, allow=()):
    cfg = Config()
    cfg.dossier_allow_hosts = list(allow)
    app = Sluice(cfg, fetcher=fetcher,
                 resolve_host=resolve or (lambda h: [GLOBAL_ADDR]))
    return app.dossier_cache(str(tmp_path), ttl_days=7)


def test_an_allowed_url_fetches_and_probes_in_order(tmp_path, role):
    """The positive control every absence assertion below is paired with."""
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"
    assert tab.calls == [
        ("create_tab", "https://jobs.invalid/x"),
        ("evaluate", "location.href"),
        ("evaluate", "document.body.innerText"),
        ("close_tab", "tab-1"),
    ]


def test_a_blocked_url_never_opens_a_tab(tmp_path, role):
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.BLOCKED_ADDRESS
    assert tab.calls == [], "no tab may be opened for a url we already refused"


def test_a_redirect_to_a_blocked_host_discards_the_body(tmp_path, role):
    def _resolve(host):
        return ["127.0.0.1"] if host == "internal.invalid" else [GLOBAL_ADDR]
    tab = _Tab(landed="http://internal.invalid/admin")
    cache = _cache(tmp_path, tab, resolve=_resolve)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.LANDED_BLOCKED
    probes = [c for c in tab.calls if c[0] == "evaluate"]
    assert probes == [("evaluate", "location.href")], \
        "the body must never be read from a blocked destination"
    assert ("close_tab", "tab-1") in tab.calls


@pytest.mark.parametrize("landed", ["", "about:blank"])
def test_an_unnavigated_tab_is_refused(tmp_path, role, landed):
    """Camofox's navigate awaits page.goto, so the tab is never at about:blank when
    we probe. This asserts that assumption rather than trusting it: if a different
    fetcher or a changed server ever violates it, we must fail closed rather than
    check a url the browser never went to."""
    tab = _Tab(landed=landed)
    cache = _cache(tmp_path, tab)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.NOT_SETTLED


@pytest.mark.parametrize("bad", [None, "not-a-dict", {}, {"result": 42}])
def test_an_unreadable_landed_url_is_refused(tmp_path, role, bad):
    tab = _Tab(landed_result=bad)
    cache = _cache(tmp_path, tab)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.LANDED_UNREADABLE


def test_a_tab_that_never_opens_is_refused(tmp_path, role):
    """Previously fell through to a cached empty dossier -- see the closure comment."""
    class _NoTab(_Tab):
        def create_tab(self, url):
            self.calls.append(("create_tab", url))
            return None
    tab = _NoTab()
    with pytest.raises(urlguard.DossierBlocked) as ei:
        _cache(tmp_path, tab).get_or_build(
            {"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.NO_TAB


def test_an_unreadable_body_is_refused(tmp_path, role):
    """Ditto: a non-string body must not become an empty JD nobody can distinguish
    from a real one."""
    class _BadBody(_Tab):
        def evaluate(self, tid, js):
            self.calls.append(("evaluate", js))
            if js == "location.href":
                return {"result": self.landed}
            return {"result": None}
    tab = _BadBody()
    with pytest.raises(urlguard.DossierBlocked) as ei:
        _cache(tmp_path, tab).get_or_build(
            {"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE


def test_a_lead_with_no_url_is_unchanged(tmp_path, role):
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "" and tab.calls == []


def test_the_allowlist_admits_a_private_host(tmp_path, role):
    tab = _Tab(landed="http://jobs.invalid/x")
    cache = _cache(tmp_path, tab, resolve=lambda h: ["10.0.0.1"],
                   allow=["jobs.invalid"])
    d = cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"


def test_dossier_blocked_carries_no_host_or_url(tmp_path, role):
    """cv/engine.py:70 logs str(e) verbatim -- the #67 leak shape."""
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "http://secret-host.invalid/path?token=x",
                            "company": "Aye", "role": role})
    msg = str(ei.value)
    assert "secret-host" not in msg and "token" not in msg and "://" not in msg


def test_a_production_shaped_sluice_fetches(tmp_path, role):
    """cli.py builds `Sluice(config)` -- no injected collaborators at all.

    The closure must touch nothing that is None in production. A previous draft
    reached for self._sleep, which IS None there, and would have raised TypeError
    on the first cache miss of every real run while this suite stayed green.
    """
    tab = _Tab()
    cfg = Config()
    app = Sluice(cfg, fetcher=tab)          # no sleep=, today=, resolve_host=
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    # The real resolver would be used, so the DNS guard fires -- that is the point:
    # it proves the closure got all the way to resolution without an attribute error.
    from tests.conftest import DnsUsedInTests
    with pytest.raises(DnsUsedInTests):
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
```

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_dossier_guard.py -v`
Expected: FAIL — the closure does not guard anything yet.

- [ ] **Step 3: Guard the closure**

Replace `Sluice.dossier_cache` in `sluice/core/app.py`:

```python
    def dossier_cache(self, dossier_dir, ttl_days):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text read via
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses.

        The lead url comes off a scraped listing, so it is guarded (#18): checked before
        a tab is opened, and the LANDED url re-checked before the body is read. A refusal
        RAISES rather than returning an empty dossier -- see the comment on the raise.
        """
        import typing  # noqa: F401  (NoReturn annotation on _refuse below)

        from sluice.core.dossier import DossierCache
        from sluice.core import urlguard
        # Parsed once per cache, not per fetch. Raises here if a Config was built by
        # hand with a malformed list (load_config validates the same way).
        allow = urlguard.parse_allow_hosts(
            getattr(self.config, "dossier_allow_hosts", []))
        # `or` the module default: self._resolve_host is None unless a test injects one.
        resolve = self._resolve_host or urlguard._resolve
        cam = {}

        def _refuse(reason, host="") -> "typing.NoReturn":
            """Log and RAISE. Never returns.

            It raises rather than returning the exception for the caller to raise:
            with a returning helper, dropping one `raise` keyword downgrades a
            refusal to a logged warning followed by a fall-through that reads and
            returns the body. That one-token deletion is precisely Task 9's own
            mutant, so the shape must make it impossible rather than merely tested.
            """
            _log.warning("dossier fetch refused (%s) host=%s", reason, host or "?")
            # The exception carries the SLUG ONLY: cv/engine.py logs str(e) verbatim
            # and triage/engine.py stores it in report.failures.
            raise urlguard.DossierBlocked(reason)

        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
            if url:
                pre = urlguard.check_url(url, allow_hosts=allow, resolve=resolve)
                if not pre.allowed:
                    _refuse(pre.reason, pre.host)
                if "client" not in cam:
                    cam["client"] = self.fetcher()
                c = cam["client"]
                tid = c.create_tab(url)
                if not tid:
                    # PRE-EXISTING behaviour was to fall through and return the empty
                    # dossier shape here. That is the outcome this whole feature exists
                    # to prevent: get_or_build CACHES it for ttl_days, triage judges the
                    # lead on a JD nobody read, apply_verdict writes a status from it,
                    # and report.failures stays empty. Raising costs one retry next run.
                    _refuse(urlguard.NO_TAB, pre.host)
                # Camofox's navigate awaits page.goto(waitUntil='domcontentloaded'),
                # so the tab HAS navigated by now and HTTP redirects are already
                # followed. The checks below assert that rather than trusting it.
                res = c.evaluate(tid, "location.href")
                landed = res.get("result") if isinstance(res, dict) else None
                if not isinstance(landed, str):
                    c.close_tab(tid)
                    _refuse(urlguard.LANDED_UNREADABLE)
                if not landed or landed == "about:blank":
                    c.close_tab(tid)
                    _refuse(urlguard.NOT_SETTLED)
                post = urlguard.check_url(landed, allow_hosts=allow, resolve=resolve)
                if not post.allowed:
                    c.close_tab(tid)
                    _refuse(urlguard.LANDED_BLOCKED, post.host)
                # Only now is the body safe to pull into memory.
                body = c.evaluate(tid, "document.body.innerText")
                md = body.get("result") if isinstance(body, dict) else None
                c.close_tab(tid)
                if not isinstance(md, str):
                    # Same reasoning as no-tab: a non-string body used to become a
                    # cached empty JD indistinguishable from a real empty one.
                    _refuse(urlguard.BODY_UNREADABLE, pre.host)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}

        return DossierCache(dossier_dir, ttl_days, fetcher=fetch)
```

Add to the `Fetcher` docstring in `sluice/core/protocols.py`:

```python
class Fetcher(Protocol):
    """The impure I/O boundary an ingest source drives a tab through. Today: Camofox.

    `Source.fetch` receives one of these on the Ctx and `Source.parse` never sees it --
    that split is what makes parsers testable offline against golden fixtures.

    One CONTRACT note that the signatures do not carry: `evaluate(tab,
    "location.href")` is no longer only a health signal. The dossier fetcher (#18)
    uses it to decide whether a response body may be read, so an implementation that
    reports a url the tab did not actually land on defeats an SSRF guard. Report the
    tab's real current url, or return a non-string so the caller fails closed.
    """
```

- [ ] **Step 4: Wire the three test sites**

In `tests/harness/config.py`, add above the `Harness` dataclass:

```python
# The harness resolves the RFC-reserved fixture family and NOTHING else. A fake that
# mapped every host to a global address would make every e2e and functional test pass
# regardless of what the SSRF guard does; raising on an unmapped host keeps the guard
# under test. `.example`/`.invalid` never resolve for real, which is why the session
# DNS guard would otherwise fire here.
_FIXTURE_ADDR = "192.88.99.1"    # RFC 3068, withdrawn: global, no operator


def harness_resolve(host):
    if host.endswith((".example", ".invalid")):
        return [_FIXTURE_ADDR]
    raise OSError(f"harness resolver: unmapped host {host!r}")
```

and change `Harness.sluice`:

```python
    def sluice(self, backend, *, today=None, sleep=None):
        """A Sluice wired to this harness: the scripted fetcher and recording
        renderer via the config seams, `backend` via the per-instance override,
        a no-op sleep so the browser's page-settle waits cost nothing, and a
        fixture-only DNS resolver so the dossier guard runs without resolving."""
        from sluice.core.app import Sluice
        return Sluice(self.config, backend=backend, today=today,
                      resolve_host=harness_resolve,
                      sleep=sleep if sleep is not None else (lambda *a, **k: None))
```

In `tests/functional/conftest.py`, inside `_HarnessSluice.__init__`:

```python
                kw.setdefault("resolve_host", harness_resolve)
```

with `from tests.harness.config import harness_resolve` added to that module's imports (check the existing import line for `build_harness` and extend it).

In `tests/test_app_operations.py:44`, change the construction:

```python
    app = Sluice(Config(), fetcher=_FakeTab(),
                 resolve_host=lambda h: ["192.88.99.1"])
```

`_FakeTab` (`tests/test_app_operations.py:9-12`) answers **every** `evaluate` with `{"result": "JD BODY"}`, which the new post-check would read as a landed url and refuse with the `scheme` slug. Replace it with:

```python
class _FakeTab:
    def create_tab(self, url): return "t1"

    def evaluate(self, tab, js):
        # The dossier closure now probes the landed url before reading the body
        # (#18); answering both probes with "JD BODY" would read as a url with no
        # scheme and refuse the fetch.
        if js == "location.href":
            return {"result": "https://example.invalid/job"}
        return {"result": "JD BODY"}

    def close_tab(self, tab): return None
```

- [ ] **Step 5: Run everything**

Run: `python -m pytest tests/test_dossier_guard.py -v` — PASS.
Run: `python -m pytest && ruff check sluice tests` — green.

**These six existing tests reach the closure and must all still pass:**

```
tests/e2e/test_a_clean_lead_reaches_rejected.py::test_a_clean_lead_reaches_rejected
tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py::test_a_cv_citing_an_unbacked_figure_never_ships
tests/e2e/test_an_empty_config_bins_nothing.py::test_an_empty_config_bins_nothing
tests/e2e/test_triage_leaves_my_application.py::test_triage_leaves_my_application
tests/functional/test_cv.py::test_cv_run_composes_and_renders
tests/test_app_operations.py::test_dossier_cache_fetches_jd_via_the_fetcher_seam
```

(Confirm the exact node IDs with `python -m pytest <file> --collect-only -q` if a name differs.)
`tests/test_doctor.py:405`'s `Sluice().triage()` does **not** reach the fetch path — it aborts at
`Sluice.backend`, which that test monkeypatches to raise `_Stop`. Stated so it is not rediscovered.

A failure with `DnsUsedInTests` means a wiring site was missed. But note what that check does **not**
catch: a site wired to a resolver that maps everything to a global address would pass while testing
nothing. That is why `harness_resolve` raises on an unmapped host rather than defaulting.

- [ ] **Step 6: Witness**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

- Delete the pre-check block → `test_a_blocked_url_never_opens_a_tab` reddens.
- Delete the post-check block → `test_a_redirect_to_a_blocked_host_discards_the_body` reddens.
- Delete the `about:blank` refusal → `test_an_unnavigated_tab_is_refused` reddens.
- Change `resolve = self._resolve_host or urlguard._resolve` to `resolve = self._resolve_host` → `test_a_production_shaped_sluice_fetches` reddens (`TypeError`, not `DnsUsedInTests`).
- Delete the `if not tid:` refusal → `test_a_tab_that_never_opens_is_refused` reddens.
- Delete the `if not isinstance(md, str):` refusal → `test_an_unreadable_body_is_refused` reddens.
- Change `_refuse` to `return urlguard.DossierBlocked(reason)` (and drop one call site's effect) →
  the corresponding refusal test reddens. This is why `_refuse` raises rather than returning: with a
  returning helper the mutation is a one-token deletion at any of six call sites.

Restore each, re-run the suite.

- [ ] **Step 7: Commit**

```bash
git add sluice/core/app.py sluice/core/protocols.py tests/test_dossier_guard.py \
        tests/harness/config.py tests/functional/conftest.py tests/test_app_operations.py
git commit -m "feat(core): guard the dossier fetch against SSRF (#18)"
```

---

### Task 9: Consumer-level behaviour

Proves the *reason* raising was chosen over returning an empty dossier. Every absence assertion is paired with a positive control differing only in the url's address class.

**Files:**
- Modify: `tests/test_dossier_guard.py`

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: nothing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dossier_guard.py`:

```python
# --- consumer behaviour ------------------------------------------------------
# Why raising beats returning an empty dossier: triage's `except` does `continue`,
# so the lead is kept OUT of the judge batch and counted. A returned empty dossier
# would be judged on an empty JD and a status written from it, with failures=0.

def _triage_run(tmp_path, monkeypatch, role, *, resolve, landed="https://jobs.invalid/x"):
    """Drive a real triage run over one shortlist-able lead, with a stub judge."""
    import os
    from sluice.triage import engine as tengine
    vault_dir = tmp_path / "vault"
    # "Job Leads", not "Leads" -- core/vault.py:29 is
    # _LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads").
    # The wrong path loads ZERO leads, which makes both assertions below pass
    # vacuously and the Step 4 mutant redden with AND without the mutation.
    leads = vault_dir / "Job Applications" / "Job Leads"
    os.makedirs(leads, exist_ok=True)
    (leads / f"Aye - {role}.md").write_text(
        f'---\ncompany: "Aye"\nrole: "{role}"\nstatus: new\n'
        'url: "https://jobs.invalid/x"\nscore: 0\n---\n# body\n')
    monkeypatch.setenv("VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "dossiers"))
    # Pin the judge's verdict to a status DIFFERENT from the starting one, so the
    # raise->return mutant necessarily writes a different byte rather than
    # coincidentally the same.
    # The keys apply_verdict actually reads (triage/apply.py:36-37): "verdict" and
    # "relevance_score". A stub emitting decision/score lands the lead on
    # needs_review, which silently breaks the positive control that anchors both
    # vacuity guards below.
    monkeypatch.setattr(tengine, "judge", lambda dossiers, backend, **kw: [
        {"lead_id": d["lead_id"], "verdict": "shortlist", "relevance_score": 90,
         "fit_reasoning": "synthetic"} for d in dossiers])
    app = Sluice(Config(), fetcher=_Tab(landed=landed),
                 backend=object(), resolve_host=resolve)
    report = app.triage(statuses=("new",))
    return report, (leads / f"Aye - {role}.md").read_text(), tmp_path / "dossiers"


def test_a_blocked_dossier_leaves_the_lead_untouched(tmp_path, monkeypatch, role):
    report, note, dossier_dir = _triage_run(
        tmp_path, monkeypatch, role, resolve=lambda h: ["127.0.0.1"])
    assert "status: new" in note, "a blocked fetch must not move the lead"
    assert report.failures, "and must be visible in the run summary"
    cached = list(dossier_dir.glob("*.json")) if dossier_dir.exists() else []
    assert cached == [], \
        "no dossier may be cached, or the allowlist remedy is masked for ttl_days"


def test_the_positive_control_does_move_the_lead(tmp_path, monkeypatch, role):
    """Without this, both assertions above pass vacuously -- the dossier dir need
    not exist, and the status is unchanged whenever the lead never reached the
    dossier step at all (a wrong vault path did exactly that in an earlier draft)."""
    report, note, dossier_dir = _triage_run(
        tmp_path, monkeypatch, role, resolve=lambda h: [GLOBAL_ADDR])
    assert "status: shortlist" in note
    assert not report.failures
    assert len(list(dossier_dir.glob("*.json"))) == 1
```

Add one more, pinning what the **cv** consumer actually does:

```python
def test_the_cv_consumer_proceeds_with_an_empty_jd(tmp_path, monkeypatch, role):
    """Raising is NOT behaviourally different for cv -- record that honestly.

    cv/engine.py:66-70 catches Exception, logs, and PROCEEDS with jd = "". So for
    this consumer a raise and a returned empty dossier are indistinguishable: a CV
    is still composed and the fabrication gate still runs. The raise-vs-return
    argument rests entirely on the TRIAGE side (above). Stating it here stops a
    reader inferring that cv skips the lead, which it does not.
    """
    from sluice.cv import engine as cvengine
    seen = {}
    monkeypatch.setattr(cvengine, "_jd_keywords", lambda r, jd: seen.setdefault("jd", jd) or [])
    # ... drive run_one with a blocked dossier; assert it returns a CvResult and
    # that seen["jd"] == "" -- i.e. composition proceeded on an empty JD.
```

> The body above is deliberately a sketch, because the exact `run_one` wiring depends on fixtures this
> plan does not otherwise build. **If assembling it costs more than ~15 minutes, do not force it:**
> delete the test and instead add the docstring's first paragraph as a comment in the task, plus a line
> in the spec's residual list. The point is that the asymmetry is *recorded*, not that it is asserted.

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_dossier_guard.py -v`
Expected: the new consumer tests FAIL. **Make the positive control pass FIRST** — until it does, the two
absence assertions in the blocked case prove nothing, and an earlier draft shipped with both passing
vacuously because the vault path was wrong.

> If `app.triage(...)`'s signature or the audit/env wiring differs from the above, fix the helper to match the real code — do not weaken the assertions.

- [ ] **Step 3: Run the suite**

Run: `python -m pytest && ruff check sluice tests` — green.

- [ ] **Step 4: Witness the raise-vs-return choice**

This is the mutant the whole raise-not-return argument exists for. In `urlguard`, change
`DossierBlocked` so `_refuse` cannot propagate — replace its `raise` with a log-and-return, and make
the pre-check call site fall through:

```python
        def _refuse(reason, host=""):
            _log.warning("dossier fetch refused (%s) host=%s", reason, host or "?")
            # MUTANT: was `raise urlguard.DossierBlocked(reason)`
```

```python
                if not pre.allowed:
                    _refuse(pre.reason, pre.host)
                    return {"jd": {"markdown": ""}, "glassdoor": {}}
```

Run: `python -m pytest tests/test_dossier_guard.py::test_a_blocked_dossier_leaves_the_lead_untouched -v`
Expected: **FAIL** — the lead moves to `shortlist` and `report.failures` is empty.

Then run `tests/test_dossier_guard.py::test_the_positive_control_does_move_the_lead` and confirm it
still **PASSES** under the mutant. That is the check that matters: if the control fails too, the
fixture is broken rather than the mutation being caught, which is exactly how an earlier draft of
this task certified nothing. Restore and re-run the suite.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dossier_guard.py
git commit -m "test(core): consumer behaviour for a blocked dossier fetch (#18)"
```

---

### Task 10: Architecture documentation

`docs/ARCHITECTURE.md` is the architecture of record and goes stale in three places. **`.rulesync/` is canonical and human-gated — do not edit it or any generated file; if you believe it needs a change, say so and stop.**

**Files:**
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:** none.

- [ ] **Step 1: Add the module to the `core/` inventory**

After the `camofox.py` bullet (around line 29):

```markdown
- `urlguard.py`: url policy for the dossier fetcher. Decides whether a
  scraped lead url may be navigated to -- http(s) only, globally routable
  addresses only, with a per-host/CIDR allowlist for a deliberately
  self-hosted board. Pure except for `_resolve`, which is injected, so the
  suite never resolves DNS. Ingest is NOT guarded: its urls come from a
  source's own spec or the user's config, not from a scraped page.
```

- [ ] **Step 2: Add `resolve_host` to the collaborator enumeration**

Extend the `sleep`/`today` bullet (around line 280):

```markdown
- **`sleep`**, **`today`**, **`resolve_host`** — `Sluice.__init__` keyword-only
  parameters. `sleep` and `today` are threaded into `ingest.base.Ctx` and
  `ingest.sink.VaultSink`: the page-settle wait and the date stamp. Two clock
  shapes rather than one is deliberate — the sink stamps per lead so it needs a
  callable, while track persists one value per run. `resolve_host` is the DNS
  resolver the dossier url guard uses; it is deliberately NOT a seam, because a
  registry entry is reachable from config and that would put an off switch for
  an SSRF guard under a YAML key.
```

- [ ] **Step 3: Resolve the pre-registered trigger**

Replace the final paragraph (the one ending *"Worth tightening if a third collaborator ever lands."*):

```markdown
Neither kind may be accepted and ignored. An unknown *adapter* key raises
`UnknownAdapter` at construction, listing the valid seams. The collaborators are
weaker: `Sluice.__init__` ends in `**overrides`, so a typo'd `sleep=` is absorbed
there. That was reported as an unknown seam override — loud, but naming the four
adapter seams and so pointing at the wrong fix. `resolve_host` was the third
`__init__` collaborator and triggered the tightening this paragraph used to
defer: the raise now carries a hint naming the collaborators and the seams
*separately*, and `_COLLABORATORS` is pinned to the real signature by a guard
test. The scope is `__init__` keywords only — `client`/`now_iso` are
`Sluice.track()` parameters, never reach `**overrides`, and a typo there is
already a plain `TypeError`.
```

- [ ] **Step 4: Verify nothing else drifted**

Run: `grep -n "urlguard\|resolve_host" docs/ARCHITECTURE.md` — expect the three edits above.
Run: `python -m pytest && ruff check sluice tests` — green.

- [ ] **Step 5: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: record urlguard and the resolve_host collaborator (#18)"
```

---

## Final verification

- [ ] `python -m pytest` — all green, no skips added.
- [ ] `ruff check sluice tests` — clean.
- [ ] `git log --oneline main..HEAD` — every message is a valid conventional commit.
- [ ] `grep -rnE "(93\.184\.216\.34|8\.8\.8\.8)" sluice tests` — neither declined fixture address.
      (These two are the spec's actual declined list. An earlier draft also grepped for a specific
      private /16 that appears nowhere in the spec or repo — naming one in a tracked public file as a
      "rejected value" is the remediation-hazard shape, and it guarded nothing.)
- [ ] `grep -rn "non-ascii" sluice tests` — the dead slug never appeared.
- [ ] Run `/review-pr` **before pushing**. CodeRabbit is the scarce resource; the specialist team is free and parallel.

## Self-review notes

Checked against the spec:

- **Spec coverage:** every Components subsection maps to a task (urlguard → 2-5, config → 6, app/resolver → 7, closure + protocols + wiring → 8, yaml.example → 6, ARCHITECTURE.md → 10). Every Testing paragraph maps to a task's Step 1. All seven residuals are documentation-only and carried in the spec, not the code.
- **Type consistency:** `AllowList` is produced in Task 2 and consumed by `verdict` (3) and `check_url` (5); `UrlVerdict` is produced in Task 3 and consumed in 5 and 8; slug constants are defined in Task 2 and used in 3, 5, 8, 9. `parse_allow_hosts` is called in `load_config` (6) and `dossier_cache` (8) — the same signature both times.
- **Known deviation from the spec, RULED ON at plan review — keep it.** The spec's sketch shows `verdict(host, addrs, *, allow_hosts)` taking the raw config list; this plan has it take a parsed `AllowList`. The architect's reason is better than the one originally given here: `Config` must hold **YAML-shaped values only**, because the #26/#63 neutral-defaults sweep resolves `isinstance(getattr(cls(), f.name), list)` — a parsed `AllowList` on the dataclass would drop straight *out* of the sweep that Task 6 Step 5 depends on. Translating a config string into a resolved object at the composition root is exactly what `_resolve`/`backend()` already do. The double call to `parse_allow_hosts` is **two boundaries, not duplication**: `load_config` covers the CLI path, `dossier_cache` covers a hand-built `Config` from a test or a future surface.
- **Task 9 caveat:** its `_triage_run` helper is written against `triage/engine.py` as read, and plan review found it wrong twice — the leads subdirectory (`Job Leads`, not `Leads`) and the judge stub's keys (`verdict`/`relevance_score`, not `decision`/`score`). Both are fixed above, but the helper is still the least-verified code in this plan. Step 2's "make the positive control pass FIRST" is the guard: with the wrong path, both blocked-case assertions passed vacuously *and* the Step 4 mutant reddened either way, so the task certified nothing while looking green.
- **Plan review found 11 High findings**, four reviewers independently landing on the same config bug. The two that generalise: a test placed where pytest does not collect it is inert no matter how good it is, and a fixture using an invisible character (U+212A) cannot survive transcription — write the escape.
