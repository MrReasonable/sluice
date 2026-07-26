# Harden the dossier fetcher against SSRF (#18)

**Status:** design approved 2026-07-26; revised twice after `/review-plan` (5 reviewers per round).
Round 1 (9 High) rewrote the failure-handling contract, closed two holes in the address rule, fixed
the allowlist validation and its error message, corrected the blast-radius enumeration and replaced
the fixture addresses. Round 2 (4 High, 0 Critical, all round-1 findings verified fixed) found the
post-check would have broken every fetch on a real browser, that the hermeticity guard could not
redden, and that the address table still could not catch its own most dangerous mutant.
The three original decisions are unchanged throughout.
**Issue:** #18 — `Harden the dossier fetcher against SSRF (scheme + private-IP + post-redirect)`
**Sub-app:** `core` (the fetcher seam's only untrusted-input call site)

## Problem

`Sluice.dossier_cache()` (`core/app.py:251-268`) feeds a **vault-sourced lead URL** straight into
`Camofox.create_tab(url)`, which navigates a real browser, and stores whatever
`document.body.innerText` returns as the dossier's JD markdown. That URL is not the user's: it is
`row.get("link") or row.get("url")` off a scraped job listing (`ingest/base.py:115`) — an
attacker-influenceable field. A posting whose apply link points at `http://127.0.0.1:8080/admin`,
`http://169.254.169.254/latest/meta-data/`, or `file:///etc/passwd` gets fetched, and its response
body gets written to disk as a dossier the judge then reads.

sluice's threat model is a **local single-user CLI**, so this is not the classic
untrusted-user-to-privileged-server boundary — which is why #18 was split out of #17 rather than
jammed into a pure-wiring PR. It is worth closing anyway: the input really is
attacker-influenceable, the harm is real on a developer machine (a local admin port, a cloud
metadata endpoint on a VM, a LAN service), and the guard costs one small module. What it must
**not** do is regress the ordinary case — a lead on a public job board must fetch exactly as it does
today. Round 2 found the first draft of the post-check would have failed exactly that test, so this
is not a hypothetical constraint.

**Enumerated, not hand-listed:** `create_tab` has exactly four call sites in `sluice/`
(`core/app.py:263`, `ingest/base.py:141`, `ingest/base.py:194`,
`ingest/sources/linkedin.py:45`). Only the first takes untrusted input; the other three navigate to
a source's `searches_spec` literal or to the user's own `sources.<id>.searches` config. That is why
the guard is scoped to the dossier path (decision 3) — blocking the ingest three by default would
break a user who deliberately configured a LAN board source, the `672ad2a` failure class.

## The three approved decisions

1. **Block by default, per-host/CIDR allowlist opt-out.** Non-`http(s)` schemes and
   non-globally-routable destinations are refused out of the box. A new root-`Config` field
   `dossier_allow_hosts` (empty default) grants explicit exceptions by hostname or CIDR. Empty
   means *"no exceptions granted"*, **not** *"match nothing"* — an unconfigured install still
   fetches every public URL, so this is not a preference gate under empty-config-abstains.

2. **Pre-check + post-redirect re-check, residual documented.** Validate scheme and every resolved
   address **before** navigating; after navigation re-read the landed URL and **discard the body**
   if it landed somewhere blocked. Residuals stated plainly (see *The residual*). Browser-level
   enforcement is the airtight fix and is **out of scope**.

3. **Dossier path only.** The validator lives in its own module so a future call site can adopt it
   deliberately. Ingest is **not** guarded.

## The rule (the load-bearing detail)

### Scheme and host

`http` and `https` only, compared after `.lower()`. Everything else — `file:`, `javascript:`,
`data:`, `ftp:`, an empty scheme, a bare path — is refused. A URL whose host is `None` or empty is
refused separately.

These are **two independent refusals** and the tests must keep them independent. Most non-http(s)
schemes *also* yield hostname `None`, so a test using `file:///etc/passwd` to exercise the *scheme*
check passes whether or not that check exists — this cost five of six originally-proposed cases
their meaning. A scheme fixture must carry a host: `ftp://host.invalid/x` and
`file://allowed.invalid/etc/passwd` do.

Host extraction is a **pure module-level `_host(url) -> str`**, mirroring `receipt._host`
(`track/receipt.py:38-60`) in shape *and* in purity — the precedent is a pure function, which is why
its hard-won rules have direct tests. It returns `""` for unparseable, non-ASCII, or host-less
input.

**The non-ASCII check runs on the raw URL, before `urlparse`.** This is not a stylistic preference:
`urlparse("http://Kexample.invalid/x").hostname` is `'kexample.invalid'`, which `.isascii()`
returns True for — urlparse's *own* lowering has already folded U+212A KELVIN to ASCII `k`. So a
non-ASCII check applied to `.hostname` **can never fire**. An earlier draft described the ordering
correctly and then endorsed `.hostname` as the accessor, which would have produced exactly the inert
check that shipped once in #10. `receipt._host` avoids this by testing the raw input string first;
`_host` must do the same.

`urlparse().hostname` remains the right accessor *after* that check: it strips IPv6 brackets
(`http://[::1]:8080/x` → `::1`) and takes the **last** `@` in a userinfo trick
(`http://user@evil.example@127.0.0.1/` → `127.0.0.1`). `ValueError` from a malformed IPv6 literal is
caught and returns `""`.

### Host → addresses

The host is **never classified by parsing it as an IP literal**. It is always handed to the
resolver, because `getaddrinfo` normalizes the obfuscated forms that exist to defeat
literal-parsing. Verified: `2130706433`, `0x7f000001` and `127.1` all resolve to `127.0.0.1`, while
`ipaddress.ip_address("2130706433")` raises. (`127.0.0.1.`, with the trailing dot, does **not**
resolve — it raises `gaierror` and so fails closed.)

A resolver that raises, or returns **zero** addresses, blocks the URL.

**The resolver catch is narrow — `OSError` only, never bare `Exception`.** `socket.gaierror`
subclasses `OSError`, so the legitimate failure is covered, while a bug in the guard itself
propagates instead of being silently converted into a "blocked" verdict. This is load-bearing for
testing, not only for hygiene: the session-wide hermeticity guard (below) raises a `BaseException`
subclass precisely so that neither this catch nor a consumer's `except Exception` can swallow it.

### Address class

An address is acceptable iff **`is_global and not is_multicast`**, applied to the address *and* to
any IPv4 address embedded inside it.

The base predicate covers every category the approved decision named — loopback, private,
link-local (including the cloud-metadata address `169.254.169.254`), reserved, multicast,
unspecified — as one default-deny expression rather than a six-way `or`. Six redundant `or` terms
would be the equivalent-mutant shape CLAUDE.md warns about: deleting a redundant conjunct leaves the
suite green.

**The embedding rule closes two real holes.** An IPv6 address can carry an IPv4 destination as its
payload, and `is_global` reads the *wrapper*, not the payload:

| embedding prefix | example | base predicate alone |
|---|---|---|
| `::ffff:0:0/96` v4-mapped | `::ffff:127.0.0.1` | already blocked |
| `2002::/16` 6to4 (RFC 3056) | `2002:7f00:1::1` | already blocked |
| `64:ff9b:1::/48` NAT64 local-use (RFC 8215) | `64:ff9b:1::7f00:1` | already blocked |
| **`64:ff9b::/96` NAT64 well-known (RFC 6052)** | `64:ff9b::7f00:1` | **ALLOWED — hole** |
| **`::/96` v4-compatible (RFC 4291, deprecated)** | `::127.0.0.1` | **ALLOWED — hole** |

On a DNS64 network `getaddrinfo` synthesises the NAT64 form for an A-record-only name — including
one pointing at loopback — and the gateway translates it back.

`_embedded_v4(addr)` returns the IPv4 address an IPv6 address carries (via `.ipv4_mapped`,
`.sixtofour`, or the low 32 bits for `::/96` and `64:ff9b::/96`), or `None`. When it returns an
address, **that** address must also satisfy the predicate. It is a pure function of an `ipaddress`
object and lives inside `verdict`. Handling the already-blocked prefixes through the same path is
one code path with one witness, not redundancy, and it stops a future CPython reclassification from
reopening a neighbouring prefix. `64:ff9b:1::/48`'s offset is deployment-specific under RFC 8215 and
therefore not extractable — recorded in the residual rather than guessed at.

**The rule has four branches, and the table is organised by branch, not by named shape.** This
matters: the table has now been found hand-picked three times, and each time adding the named rows
left the *method* unfixed. A row exists to witness a branch.

| branch | must be BLOCKED | must be ALLOWED |
|---|---|---|
| wrapper fails base predicate | `127.0.0.1`, `::1`, `10.0.0.1`, `172.31.255.254`, `192.168.1.1`, `fc00::1`, `fd00::1`, `169.254.169.254`, `fe80::1`, `fe80::1%en0`, `240.0.0.1`, `0.0.0.0`, `::`, `203.0.113.1`, `192.0.2.1`, `198.51.100.1`, `2001:db8::1`, `100.64.0.1`, `198.18.0.1` | `192.88.99.1`, `2001:20::1` |
| wrapper global **but multicast** | `224.0.0.1`, `ff02::1` | — |
| embeds v4, **payload** fails predicate | `64:ff9b::7f00:1`, `::127.0.0.1`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, `2002:7f00:1::1` | — |
| embeds v4, **payload** passes predicate | — | **`64:ff9b::192.88.99.1`, `::ffff:192.88.99.1`** |

The last row is the one the previous draft lacked, and its absence was the table's most dangerous
gap. Without an *allowed*-embedding case, a mutant that drops the embedded address's predicate call
— leaving "any extractable v4 blocks" — stays green across every other row, while blocking **every
public board** on a DNS64 network or under `AI_V4MAPPED`. That is a `672ad2a`-direction regression
the table was structurally unable to catch. Verified: both are wrapper-global and payload-global.

**Any** blocked address among a host's answers blocks the URL, so a multi-A-record host cannot
smuggle one private answer through by ordering.

The base predicate blocks slightly more than the six named categories: `100.64.0.1` (CGNAT) carries
none of the six flags, and neither would a future IANA special-purpose range. Same direction as the
approved decision; the allowlist is the opt-out.

### Allowlist

`dossier_allow_hosts` is a list of strings, each either a **hostname** or a **CIDR / bare IP**.

**Dispatch — strip, then reject empty, then IP-shaped-first.** Each entry is `.strip()`ed; an entry
that is empty (originally or after stripping) **raises**. Then:

> An entry is **IP-shaped** iff it contains `/` or `:`, **or** matches `^[0-9.]+$`.
> An IP-shaped entry must parse as `ipaddress.ip_network(entry, strict=True)` or it **raises**.
> Everything else is a hostname.

Three drafts of this rule, each fixing the last:

- Keying on `/` alone let `10.0.0.300` through as a hostname grant that could never fire, and made a
  bare `10.0.0.1` a hostname, contradicting this spec's own bare-IP semantics.
- Adding "hex digits, dots and colons" fixed those but broke two ways: `'10.0.0.1 '` (a stray space
  in a YAML entry) and `''` still fell through to the hostname branch as silent dead grants, while
  the hex-only test **raised on legitimate single-label LAN hostnames** — `db`, `dc`, `ad`, `cafe`,
  `abc` are all hex-only, and a single-label LAN host is precisely the user this opt-out exists for.
- The rule above is verified against all of those: `10.0.0.300`, `127.1`, `2130706433`, `[::1]`,
  `[fd00::5]` and `jobs.invalid:8080` all raise; `db`, `cafe`, `abc`, `abba`, `jobs.invalid` all
  reach the hostname branch; `10.0.0.0/8` and `fd00::/8` parse as networks.

The `:` clause is what makes a bracketed literal (`[fd00::5]`) or a host:port entry
(`jobs.invalid:8080`) **raise** rather than becoming an inert hostname grant — `urlparse` strips
both brackets and port, so neither could ever match, and a user copying a host out of their LAN
board's URL would otherwise get a permanently dead exception and the same warning they were trying
to silence. Accepted edge: the single-label hostnames `1` and `0` are IP-shaped and raise. That is
loud and pathological rather than silent and plausible, which is the right trade.

**`strict=True`, not `strict=False`.** `strict=False` silently widens `192.0.2.5/24` to
`192.0.2.0/24` — the user names one address and receives 256 exceptions to the SSRF guard, with
nothing recording the widening. That is the CIDR analogue of the suffix match this section rejects
below; widening is the unsafe direction on an allowlist. Verified: `strict=True` parses every
documented form (`10.0.0.1` → `/32`, `10.0.0.0/8`, `2001:20::1` → `/128`) and raises only on
host-bits-set, which is a config typo that should fail loudly.

Grant semantics, evaluated only against addresses that would otherwise be blocked:

- **hostname entry** — matches iff the URL's hostname is **exactly equal** (case-insensitively),
  after stripping at most one trailing `.` from **both** sides. Not a suffix match: on a denylist a
  suffix widens the safe direction, but on an **allowlist** a suffix hands `evil.example.jobs.invalid`
  the grant meant for `jobs.invalid`. The trailing-dot normalisation is needed because
  `urlparse('http://jobs.invalid./x').hostname` is `jobs.invalid.`, which would not equal a
  `jobs.invalid` entry — the user's exception would silently never fire. A leading `www.` is still
  **not** stripped: that is a receipt-matching nicety and would be wrong against a list the user
  wrote literally.
- **network entry** — a blocked address passes iff contained in some allowed network.

The allowlist grants **address-class exceptions only** — never a scheme. `file://` is refused for an
allowlisted host exactly as for any other, because the scheme check runs first.

**Validation raises at construction — and must not echo the user's network.** This is where the
first draft was wrong twice, both being the #67 residual recurring:

- It claimed to report "the offending entry only", citing `_merge_denylist` — a precedent that only
  ever echoes values which already failed `isinstance(k, str)`, never a host. Worse, it layered
  validation on `_str_list`, which raises `f"... got {value!r}"` — **the whole list** — and runs
  first.
- `ipaddress`'s own `ValueError` **contains the literal**: verified, `ip_network('192.0.2.0/33')`
  raises `'192.0.2.0/33' does not appear to be an IPv4 or IPv6 network`. So `raise ... from e`
  re-exposes the entry through the exception chain.

The rule: validate in a dedicated function, **not** through `_str_list`. Report the config key, the
offending entry's **index**, and the expected *shape* — never the entry's value, never the list.
Sever the chain with `from None`. A config file is one of the few places a user's real private
hostnames legitimately live, and an exception message travels further (logs, bug reports) than the
file does.

### Failure handling

**A blocked URL raises `DossierBlocked`; it does not return the empty-dossier shape.** The first
draft argued the opposite, on the premise that raising "would abort nothing usefully". That premise
is false for triage:

| | exception | returned empty dossier |
|---|---|---|
| `triage/engine.py:82-86` | `continue` → lead kept **out of** `dossiers`, never judged, **no status write**, counted in `report.failures` (count printed by `cli.py:219`) | appended to `dossiers` → judged on an empty JD → `apply_verdict` (`triage/engine.py:106`) writes whatever status that verdict names, `failures=0` |
| `cv/engine.py:66-71` | logs, composes with an empty JD | identical |

*Which* status gets written is the backend's output for an empty JD and no offline test can pin it;
what the code determines — that a status write derived from a blocked fetch happens at all, and is
invisible in the run summary — is the whole argument and is enough.

`DossierBlocked` is defined in `core/urlguard.py`, the module that owns the policy — **not** in
`core/protocols.py` beside `VaultConflict`. `VaultConflict` lives there because it is a *Store
contract* outcome that any store may raise and the conformance suite asserts; no `Fetcher` raises
`DossierBlocked`, so placing it there would tell a future fetcher implementer it is part of their
contract.

Both consumers' existing bare `except Exception` catch it — **no consumer change is needed**.
Enumerated: `get_or_build` has exactly two callers in `sluice/` (`triage/engine.py:83`,
`cv/engine.py:67`, the latter reached from `run_batch` at `:177`), and both already catch.

Raising also fixes a second defect for free. `DossierCache.get_or_build` (`core/dossier.py:49-69`)
calls the fetcher at `:53` and writes at `:66-68`, so raising writes nothing. Had the closure
returned an empty dossier, `_fresh` would have served it for `ttl_days` (default **7** —
`triage/config.py:38`, `cv/config.py:41`), and a user who read the log, added the host to
`dossier_allow_hosts` and re-ran would have got the same empty dossier for a week: closure never
called, allowlist never consulted, no log line.

**`DossierBlocked` carries the reason slug only — no URL, no host, no config entry.** Both sinks
interpolate `str(e)`: `cv/engine.py:70` logs it (`_log.warning("dossier for %s failed: %s", …)`) and
`triage/engine.py:85` stores it in `report.failures`. The cv one is a live log sink of the #67
rev-001 shape, so an exception carrying the URL would log through cv exactly what the app.py line is
written to withhold. The host reaches the operator via that WARNING line, which is the single place
it is needed for the allowlist remedy.

Reason slugs are a small fixed set the tests assert on: `scheme`, `no-host`, `non-ascii`,
`resolve-failed`, `resolve-empty`, `blocked-address`, `not-settled`, `landed-blocked`,
`landed-unreadable`.

The WARNING log line (at **WARNING**, not DEBUG — a security refusal at DEBUG is effectively silent)
names the reason and the host. `UrlVerdict.host` is `""` **only when the URL yielded no host** —
the bare `file:///…` and `http:///…` shapes. A scheme failure does *not* generally imply a missing
host: `urlparse("ftp://host.invalid/x").hostname` is `'host.invalid'`, which is precisely why that
URL works as a scheme fixture. An earlier draft asserted "on a scheme failure there is no host",
which contradicted its own fixture rationale and would have told the implementer to discard a parsed
host from a security log.

## Components

### `sluice/core/urlguard.py` (new — the testable core)

```python
class DossierBlocked(Exception):   # message is the reason slug ONLY

@dataclass
class UrlVerdict:
    allowed: bool
    reason: str = ""        # "" when allowed; one of the fixed slugs otherwise
    host: str = ""          # "" only when the URL yielded no host

def _host(url) -> str:                                    # PURE (mirrors receipt._host)
def _embedded_v4(addr) -> IPv4Address | None:             # PURE
def verdict(host, addrs, *, allow_hosts) -> UrlVerdict:   # PURE — the branch-tested policy
def check_url(url, *, allow_hosts, resolve=_resolve) -> UrlVerdict
def _resolve(host) -> list[str]:                          # socket.getaddrinfo -> address strings
```

With `_host` extracted, `check_url` reduces to `_host` → `resolve` → `verdict`, and its own logic is
just the resolver call and the fail-closed handling (`OSError` → `resolve-failed`, empty →
`resolve-empty`). The scheme check stays in `check_url` because it precedes host extraction. That is
an honest description of where policy lives, not a claim that all of it is in `verdict`.

The allowlist is checked **inside `verdict`**, not short-circuited before resolution: one place, one
witness. The redundant lookup for an allowlisted host is free — we are about to fetch it anyway.

`socket` and `ipaddress` are stdlib; no new dependency.

### `sluice/core/config.py`

Two root-`Config` fields, beside `fetcher: str = "camofox"`:

- `dossier_allow_hosts: list = field(default_factory=list)`
- `dossier_settle_seconds: float = 3.0` — the bounded deadline for the post-check (below), matching
  `BrowserListSource.wait`'s existing default. Environmental rather than a preference, so it is a
  config knob per rule 13 rather than a constant.

Neither belongs in `TriageConfig`/`CvConfig`: `dossier_cache` is called from **both** sub-apps
(`app.py:402` triage, `app.py:436` cv), and a security policy that could differ between them is a
bug.

`load_config` validates `dossier_allow_hosts` in a dedicated function — **not** `_str_list`, whose
message would leak the whole list.

`dossier_allow_hosts` is a list default, so `tests/test_sluice_neutral_defaults.py`'s value-keyed
sweep picks it up and requires `[]`. It becomes the first swept field whose "empty" is *inverted*
relative to its neighbours: for `accept_titles` empty means "pass everything through", here it means
"grant no exceptions" (the public-URL path is unaffected for a different reason — the address rule,
not the list). A one-line comment at its assertion records that, so nobody can cite the sweep as
licence to loosen the guard.

### `sluice/core/app.py` — the `dossier_cache` fetch closure

```
url present?
  pre-check   check_url(url, allow_hosts=cfg.dossier_allow_hosts, resolve=self._resolve_host)
              blocked -> log WARNING(reason, host); raise DossierBlocked(reason)   (no tab opened)
  create_tab(url) -> tid
  SETTLE      poll evaluate(tid, "location.href") until it reports a real navigation,
              sleeping via self._sleep between polls, bounded by dossier_settle_seconds
              deadline exceeded -> close_tab; raise DossierBlocked("not-settled")
  post-check  re-run check_url on the settled URL
              blocked -> close_tab; raise DossierBlocked("landed-blocked")
              unreadable (non-dict, empty) -> close_tab; raise DossierBlocked("landed-unreadable")
  read        evaluate(tid, "document.body.innerText"); close_tab
```

**The settle loop is not optional, and its absence would have broken the feature outright.**
`Camofox.create_tab` (`core/camofox.py:45-54`) opens the tab and then fires `navigate` as a
*separate* `POST /tabs/{tid}/navigate`, with no wait for load completion. Both existing read-back
sites sleep first — `ingest/base.py:140-153` and `ingest/sources/linkedin.py:45-53` each
`sleep(self.wait)` before probing `location.href`. A closure that probed immediately would read
`about:blank`, fail the scheme check, and raise for **every lead**: `judged=0`, every keep into
`report.failures`, every CV composed on an empty JD. The first draft documented the *permissive*
consequence of an unsettled tab (residual 5) and missed that the same unsettledness breaks the
feature in the other direction. It is unfalsifiable offline — `tests/harness/browser.py:38-42`
answers `location.href` synchronously — which is why it survived a full round of review.

`not-settled` is a **distinct reason slug** from `landed-blocked`: "the page never loaded" and "the
page loaded somewhere forbidden" are different operator problems, and collapsing them would make a
misconfigured browser look like an attack.

Two further details:

- **The probe is `location.href`, not `document.URL`** — the exact string the two ingest read-back
  sites use (`ingest/base.py:152`, `ingest/sources/linkedin.py:53`), and the one
  `tests/harness/browser.py:42` answers by exact match.
- **The body is read only after the post-check passes**, so a blocked destination's response body is
  never pulled into sluice's memory.

Reusing `evaluate` rather than growing a fifth `Fetcher` method is deliberate — a protocol method
for one consumer is the abstraction to refuse. But the *contract* changed even though the signature
did not: `evaluate(tab, "location.href")` was a health signal and now gates a response body. The
`Fetcher` docstring in `core/protocols.py` gains a paragraph saying so, since there is no `Fetcher`
conformance suite to carry it.

### `sluice/core/app.py` — the resolver injection point

`Sluice.__init__(self, config=None, *, sleep=None, today=None, **overrides)` gains a keyword-only
`resolve_host=None`, stored as `self._resolve_host`. `None` means production's `urlguard._resolve`.
The settle loop reuses the **existing** `self._sleep` collaborator — no second injection point.

**Why a collaborator and not a seam** — the governing rule is already written at
`docs/ARCHITECTURE.md:284-296`: *"does a user legitimately choose among implementations?"* Its safety
rationale is decisive and stronger than any mechanical argument: **a registry entry is reachable
from config, and config is user-facing** — a seam-resolved resolver would put an off switch for the
SSRF guard under a YAML key. (The mechanical facts hold too: `**overrides` is validated against
`_SEAMS` at `app.py:174-179`, and `Sluice._resolve` at `app.py:186` is the seam-resolution method,
which is why the parameter is named `resolve_host`.)

**Discharging the pre-registered trigger.** `ARCHITECTURE.md:298-302` records that a typo'd
collaborator is absorbed by `**overrides` and reported as an unknown *seam* override — loud, but
pointing at the wrong fix — and that this is *"worth tightening if a third collaborator ever lands"*.
`resolve_host` is the third **`__init__`-level** collaborator (`sleep`, `today`, then this);
`client`/`now_iso` are `Sluice.track()` parameters and never reach `**overrides`, so a typo there is
already a plain `TypeError` and is untouched. The `ARCHITECTURE.md` rewrite must scope the resolved
trigger to `__init__` keywords rather than to collaborators in general.

The mechanism matters and the obvious implementation is wrong. There is nothing to *validate*:
`sleep`/`today`/`resolve_host` are explicit keyword-only parameters, so a typo'd `resolve_hosts=`
never binds to them and always lands in `**overrides`. The only live change is the **message**. And
widening the `known` list passed to `plugins.UnknownAdapter("seam override", unknown[0], _SEAMS)`
would print *"expected backend, store, fetcher, renderer, sleep, today, resolve_host"* — advertising
the collaborators **as seams**, erasing the distinction `ARCHITECTURE.md:271-296` exists to draw, and
implying three config keys that do not exist. So:

- Keep the `set(overrides) - set(_SEAMS)` guard exactly as it is.
- Add a module-level `_COLLABORATORS = ("sleep", "today", "resolve_host")`, used **only** for
  messaging.
- Before raising, test the unknown key against `_COLLABORATORS` so the message names the two
  categories **separately** — e.g. *"unknown keyword 'resolve_hosts': injected collaborators are
  sleep, today, resolve_host; adapter seam overrides are backend, store, fetcher, renderer"*.
- A guard test asserts `_COLLABORATORS` equals `Sluice.__init__`'s keyword-only parameter names via
  `inspect.signature`. (Verified: today that is `('sleep', 'today')`.) A stale tuple when a fourth
  collaborator lands would reinstate the exact misdirection this removes.

**The blast radius is six tests, three wiring sites.** The first draft's table was built by grepping
and missed one; two reviewers independently rebuilt it by wrapping `DossierCache.fetcher` across the
suite, and a third confirmed the corrected count the same way.

| test | url | wiring |
|---|---|---|
| `tests/e2e/test_a_clean_lead_reaches_rejected.py:83` | `https://remoteok.example/jobs/{1,2}` | `Harness.sluice()` |
| `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py:35` | `https://remoteok.example/jobs/1` | `Harness.sluice()` |
| `tests/e2e/test_an_empty_config_bins_nothing.py:26` | `https://remoteok.example/jobs/1` | `Harness.sluice()` |
| `tests/e2e/test_triage_leaves_my_application.py:28` | `https://remoteok.example/x` | `Harness.sluice()` |
| `tests/functional/test_cv.py::test_cv_run_composes_and_renders` | `https://example.invalid/jobs/1` (`:30`, also `:62`) | `_HarnessSluice`, `tests/functional/conftest.py:47-55` |
| `tests/test_app_operations.py:46` | `https://example.invalid/job` | direct `Sluice(...)` at `:44` |

Wiring sites: `Harness.sluice()` (`tests/harness/config.py:90-96` — `build_harness` itself constructs
no `Sluice`), `tests/functional/conftest.py`'s `_HarnessSluice`, and `test_app_operations.py:44` by
hand. (`test_app_operations.py:54` passes no url; `:65` runs `no_llm=True`, which skips the dossier —
`triage/engine.py:78`.)

The functional-tier test is the dangerous one: `cv/engine.py:66-71` swallows the failure and the
scripted backend returns its CV regardless of JD text, so it stays **green** whether it does real DNS
or silently exercises the blocked path.

**The durable fix is the guard, not the list.** A session-scoped autouse fixture in
`tests/conftest.py` replaces `socket.getaddrinfo` with a raiser — verified achievable: installing it
leaves the current suite fully green, so the suite performs zero DNS today. **The raiser must
subclass `BaseException`, not `Exception`.** A plain `Exception` is swallowed twice — first by
`check_url`'s own fail-closed rule, which turns a raising resolver into a *verdict*, then by
`cv/engine.py:66-71` — so an implementer who forgot `resolve_host` in the functional conftest (the
exact round-1 miss) would still see green. With `BaseException` plus the narrow `OSError` catch
specified above, that omission fails loudly.

### `sluice.yaml.example`

A commented `dossier_allow_hosts` block plus `dossier_settle_seconds`, in the adapter-seams region,
documenting both entry forms and that an empty list grants nothing rather than blocking everything.
Values are pinned here rather than left to the implementer: `jobs.invalid` and `10.0.0.0/8`. A whole
RFC 1918 block encodes nothing about anyone's network, where a specific /24 would — and for the same
reason this spec's own invalid-CIDR example is `192.0.2.0/33`, from the documentation range, rather
than a plausible private subnet.

## The residual

Stated plainly, in the same spirit as the #16 CAS micro-window. **This raises the bar; it is not a
hermetic seal.**

1. **DNS rebinding.** Our `getaddrinfo` and the browser's own resolution are two separate lookups. A
   host whose answer changes between them can pass the pre-check and still be navigated to an
   internal address. The post-check does not catch it: the *URL* is unchanged; only the address
   behind it moved.
2. **The request precedes the post-check.** A redirect to an internal destination means the browser
   has **already sent** the request there. We withhold the **data**, not the request. A blind-SSRF
   side effect — a `GET` that mutates internal state — is not prevented.
3. **Browser-level enforcement is the airtight fix and is out of scope.** A request-interception
   policy in Camofox, or an egress proxy, would decide per-connection after resolution with no
   window at all.
4. **Port is not policy.** `http://<global-host>:22/` is permitted: the destination is a global
   address, so it is not an internal-network reach, and filtering ports would refuse legitimate
   boards on non-standard ports.
5. **A late redirect can still outrun the settle loop.** The loop waits for navigation to *start*
   producing a real URL; a redirect firing after that point can leave the post-check validating the
   pre-redirect URL while the body probe returns post-redirect content. Bounding the deadline
   shrinks the window and eliminates the `about:blank` failure that would have broken every fetch,
   but does not close it. Not witnessable offline — `tests/harness/browser.py:42` answers
   synchronously — so it is recorded here rather than tested.
6. **`64:ff9b:1::/48` is not extractable.** RFC 8215's local-use NAT64 prefix embeds the IPv4 address
   at a deployment-specific offset, so `_embedded_v4` cannot decode it. It is blocked today by the
   base predicate; if CPython ever reclassified it as global, the embedding rule would not catch it.

## Invariants upheld

- **Never-clobber / never-regress.** No status or note write is added or changed. Raising rather than
  returning is precisely what keeps triage from writing a status derived from a blocked fetch.
- **Empty config abstains.** No new preference gate. `dossier_allow_hosts` is a safety allowlist
  whose empty default leaves the public-URL path exactly as today; the neutral-defaults sweep covers
  it and passes, with a comment recording why its "empty" is inverted.
- **Fail loudly at construction.** A malformed allowlist entry raises, naming the key, the entry
  index and the expected shape — never the value.
- **No silent failures.** `DossierBlocked` propagates to the consumers' existing per-item handlers
  (the documented per-item-isolation pattern); the WARNING line and `report.failures` make a refusal
  visible. The resolver catch is narrow so a bug in the guard cannot masquerade as a block.
- **Standard-library only.** `socket`, `ipaddress`, `urllib.parse`. No new dependency.
- **Pure/impure split.** `_host`, `_embedded_v4` and `verdict` are pure and directly tested; the
  resolver is injected at two levels; **no test performs DNS**, enforced by a `BaseException`-raising
  session fixture rather than by an enumeration.
- **Adapter seams.** No seam change; the post-redirect read uses existing `Fetcher.evaluate`, with the
  contract change recorded in the protocol docstring.
- **Neutrality.** No hostnames, absolute paths or real-network addresses in `sluice/` or `tests/`.

## Testing (synthetic, offline)

**Pure `verdict` table — organised by branch.** Every cell of the four-branch table above, including
the two **allowed**-embedding rows without which the drop-the-payload-check mutant cannot be caught.

**Pure `_host` table** (direct, not through `check_url`): the U+212A KELVIN case asserted against
the **raw URL** — `http://Kexample.invalid/x` must yield `""`, and the test must fail if the
check is moved after `urlparse` (a move-mutant, since `.hostname` is already folded to
`kexample.invalid` and `.isascii()`); `http://user@evil.example@127.0.0.1/` → `127.0.0.1`;
`http://[::1]:8080/x` → `::1`; `https://[abc` → `""` rather than raising; `http:///etc/passwd` → `""`;
`http://jobs.invalid./x` → `jobs.invalid.`.

**Scheme and empty host, kept independent.** `ftp://host.invalid/x` and
`file://allowed.invalid/etc/passwd` carry a host, so they uniquely witness the scheme check — each
asserting the `scheme` slug, with the fake resolver mapping the host to a **global** address so the
address rule cannot be what blocks them. `http:///etc/passwd` uniquely witnesses `no-host`. A
separate test asserts the allowlist never grants a scheme: a `file://` URL whose host is allowlisted
still blocks with the `scheme` slug. `HTTPS://Example.COM/a` passes (case).

**Resolver behaviour:** two addresses, only one private → block; a resolver raising `OSError` →
`resolve-failed`; zero addresses → `resolve-empty`; an unparseable answer → block. A resolver raising
a **non-`OSError`** propagates rather than becoming a verdict (this is what makes the session guard
work). A named test asserts `2130706433`, `0x7f000001` and `127.1` reach the resolver as-is, and that
`127.0.0.1.` fails closed via `gaierror`.

**Allowlist:** exact hostname grant admits; `evil.example.jobs.invalid` against a `jobs.invalid`
grant does **not**; `http://jobs.invalid./x` **is** admitted by a `jobs.invalid` grant; a CIDR grant
admits inside and refuses outside; a bare IP behaves as a single-address network. Validation raises
for: `10.0.0.300`, `[::1]`, `jobs.invalid:8080`, `192.0.2.5/24` (host bits set), `""`, a whitespace-
only entry, a non-string entry. `db`, `cafe`, `abc` and `jobs.invalid` are accepted as hostnames, and
`'10.0.0.1 '` is accepted as a network — the two directions the previous rule got wrong. One test
asserts the raised message contains **neither** the entry value nor any other list element, and that
`__cause__` is `None`.

**Call-site behaviour** (fake fetcher, no browser): a blocked lead URL raises `DossierBlocked` and
**never calls `create_tab`**; an allowed URL fetches as today; a fetcher whose settled
`location.href` reports a blocked destination raises `landed-blocked` with
`document.body.innerText` **never evaluated** and the tab closed; an unreadable `location.href`
raises `landed-unreadable`; a fetcher that never leaves `about:blank` raises `not-settled` after the
bounded deadline, with the injected `sleep` asserted to have been called (so the loop is real and
the test does not wall-clock). Absence assertions record the fake's **exact probe sequence** and pair
with the allowed-URL positive control, where the body probe *does* appear.
A test asserts `str(DossierBlocked(...))` contains neither the host nor the URL.

**Consumer-level, each with a positive control over the same fixture and asserted path, differing
only in the URL's address class:** after a blocked fetch a triage run leaves the lead's status
byte-unchanged, records it in `report.failures`, and writes **no** dossier file; after an allowed
fetch the same run writes exactly one file at that path and **does** change the status byte. Both
controls are required — "no file written" passes vacuously against a directory `core/dossier.py`
only creates on the write path, and "status unchanged" passes vacuously whenever the lead never
reached the dossier step at all (`triage/engine.py:78`). The scripted judge's verdict is pinned to a
status **different** from the starting one, so the `raise`→`return` mutant necessarily writes a
different byte rather than coincidentally the same.

**Resolver injection:** `Sluice(resolve_host=...)` threads the fake into the closure — a host mapped
to a global address fetches, one mapped to a private address does not. A companion test asserts the
**production default** is `urlguard._resolve` when the parameter is omitted, so a wiring that always
used the fake cannot ship green. The harness fake **raises on an unmapped host**: one mapping
everything to a global address would make every e2e and functional test pass regardless of the guard.

**Hermeticity:** the session-scoped autouse fixture in `tests/conftest.py` replaces
`socket.getaddrinfo` with a **`BaseException`** raiser; a test asserts the fixture is active.

**Collaborator messaging:** `Sluice(resolve_hosts=...)` raises a message naming collaborators and
seams **separately**; and `_COLLABORATORS` equals `Sluice.__init__`'s keyword-only parameter names
via `inspect.signature`.

**Config:** `Config().dossier_allow_hosts == []`; the same through `load_config(None)` with
`SLUICE_CONFIG` cleared (otherwise it reads the developer's own config and passes for the wrong
reason); a round-trip test proving the neutral default costs no override capability.

**Classification pinning** — `test_fixture_addresses_are_globally_classified` asserts
`is_global=True` for **the blocked fixtures whose classification makes a witness load-bearing**, not
only the allowed ones: `224.0.0.1` and `ff02::1` (which is what makes `not is_multicast`
load-bearing), `64:ff9b::7f00:1` and `::127.0.0.1` (which is what makes the embedding rule
load-bearing), plus `192.88.99.1`, `2001:20::1`, `::ffff:192.88.99.1` and `64:ff9b::192.88.99.1`.
Each assertion carries a comment naming the witness whose premise it pins. Without this, a future
CPython reclassifying any of the four blocked ones to `is_global=False` would leave the base
predicate blocking that row, the table green, and the named witness silently no longer reddening —
the "a comment is not a check" shape from #30's inv-001.

### Fixture addresses

The allowed case cannot use the RFC documentation ranges: `203.0.113.1`, `192.0.2.1`,
`198.51.100.1` and `2001:db8::1` are all `is_private` in CPython — exactly what this guard blocks. An
earlier draft proposed `93.184.216.34` and `8.8.8.8`; both are declined as real, routable,
operator-assigned addresses, the first carrying an ownership annotation no offline test can falsify
and the second being a live resolver address in a DNS-guard fixture. The repo's only existing IP
literals are `127.0.0.1` (`sluice/core/camofox.py:14`) and `192.0.2.1`
(`tests/test_track_receipt.py:436`).

Use **`192.88.99.1`** (RFC 3068 6to4 relay anycast, withdrawn by RFC 7526) and **`2001:20::1`**
(RFC 7343 ORCHIDv2): both `is_global=True, is_multicast=False`, neither operator-run or routed,
neither carrying an ownership claim. `192.88.99.0/24` is relay *anycast* and embeds no address, so
the embedding rule does not touch it — had that rule been a prefix denylist including it, this
fixture and that hardening would have collided. Hostnames stay in the RFC-reserved family
(`example.invalid`, `jobs.invalid`).

### Mutation witnesses (named)

Mutate by **moving or deleting**, never adding. Run each by node id, confirm the named test reddens
*and* that no pre-existing test is what catches it.

| mutant | reddens |
|---|---|
| delete `and not is_multicast` | the multicast rows |
| delete the embedded-address predicate call (any extractable v4 blocks) | **the allowed-embedding rows** |
| delete `_embedded_v4`'s use entirely (wrapper only) | `64:ff9b::7f00:1`, `::127.0.0.1` |
| delete the "every address must pass" quantifier | the two-address test |
| delete the scheme check | `ftp://host.invalid/x` (confirm no empty-host case catches it) |
| delete the empty-host refusal | `http:///etc/passwd` (confirm no scheme case catches it) |
| **move** the non-ASCII check after `urlparse` | the raw-URL KELVIN case |
| delete the pre-check | "blocked URL never calls `create_tab`" |
| delete the settle loop | the `about:blank` / `not-settled` test |
| delete the post-check | the redirected-fetcher test |
| replace exact-equality allowlist matching with a suffix test | `evil.example.jobs.invalid` |
| delete the trailing-dot normalisation | the `http://jobs.invalid./x` grant test |
| widen the resolver catch from `OSError` to `Exception` | the non-`OSError`-propagates test |
| change `strict=True` to `strict=False` | the `192.0.2.5/24` validation test |
| delete the `resolve_host` thread-through | the injection test, **and the session guard trips** |
| change `raise DossierBlocked` to `return` the empty shape | the triage status/failures test |

The last two are stated that way deliberately: "the blast-radius tests start doing DNS" is not an
observable red signal without the `BaseException` guard, and the `return` mutant is caught by the
`report.failures` assertion plus the pinned judge verdict, not by an unpinned status byte.

**Commit before witnessing.** A witness script restoring via `git checkout -- <file>` wipes
uncommitted changes in that file, and the empty post-run diff hides the loss.

## Scope

`core/urlguard.py` (new); two root-`Config` fields plus a dedicated validator; one keyword-only
`Sluice.__init__` parameter plus the `_COLLABORATORS` messaging tightening; the closure's pre-check,
settle loop and post-check; a `Fetcher` docstring paragraph in `core/protocols.py`; a
`sluice.yaml.example` block. Test side: `tests/conftest.py` (the `getaddrinfo` guard),
`tests/harness/config.py`, `tests/functional/conftest.py`, `tests/test_app_operations.py`.

Docs: `docs/ARCHITECTURE.md` needs the `core/` inventory (`:3-43`) to gain `urlguard.py`, the
collaborator enumeration (`:271-283`) to gain `resolve_host`, and the trigger (`:298-302`) resolved —
**scoped to `__init__` keywords**, since `client`/`now_iso` on `track()` are untouched.
`.rulesync/rules/CLAUDE.md` describes the four seams but does not enumerate collaborators, so it
needs no change — and it is human-gated regardless, so any change there is escalated, never
auto-applied.

No new dependency, no adapter-seam change, no new CLI command, no protocol *signature* change.
Ingest is untouched.

## Decisions taken across two review rounds

1. **Address rule** — `is_global and not is_multicast`, plus the `_embedded_v4` payload recheck. The
   tightening beyond the six named categories is kept.
2. **`requires-python`** — no floor change; the classification-pinning test discharges it by
   asserting the premises on whatever interpreter runs.
3. **Fixture addresses** — `192.88.99.1` and `2001:20::1`, no ownership annotation.
4. **Failure contract** — raise `DossierBlocked` (reason slug only), do not return an empty dossier.
5. **Post-check** — bounded settle loop on the existing `sleep` collaborator, `not-settled` as its
   own slug.
6. **Hermeticity** — a `BaseException`-raising session fixture, plus a narrow `OSError` resolver
   catch, replacing the enumeration as the load-bearing guarantee.

Nothing in this spec is a TBD. The one judgment deliberately left unresolved in code is residual 6
(`64:ff9b:1::/48` is undecodable); it is documented rather than guessed at.
