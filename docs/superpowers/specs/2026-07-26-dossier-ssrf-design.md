# Harden the dossier fetcher against SSRF (#18)

**Status:** design approved 2026-07-26 (brainstormed in conversation); revised once after
`/review-plan` (5 reviewers: 0 Critical, 9 High, 9 Medium, 7 Low). The three original decisions are
unchanged. Round 1 rewrote the failure-handling contract (it rested on a false premise), closed two
holes in the address rule, fixed the allowlist validation and its error message, corrected the
blast-radius enumeration, and replaced the fixture addresses.
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
jammed into a pure-wiring PR. It is worth closing anyway, and now: the input really is
attacker-influenceable (whoever writes a job posting chooses the URL), the harm is real on a
developer machine (a local admin port, a cloud metadata endpoint on a VM, a LAN service), and the
guard costs one small module. What it must **not** do is regress the ordinary case — a lead on a
public job board must fetch exactly as it does today.

**Enumerated, not hand-listed:** `create_tab` has exactly four call sites in `sluice/`
(`core/app.py:263`, `ingest/base.py:141`, `ingest/base.py:194`,
`ingest/sources/linkedin.py:45`). Only the first takes untrusted input; the other three navigate to
a source's `searches_spec` literal or to the user's own `sources.<id>.searches` config. That is why
the guard is scoped to the dossier path (decision 3 below) — blocking the ingest three by default
would break a user who deliberately configured a LAN board source, which is the `672ad2a` failure
class this repo has already shipped once.

## The three approved decisions

1. **Block by default, per-host/CIDR allowlist opt-out.** Non-`http(s)` schemes and
   non-globally-routable destinations are refused out of the box. A new root-`Config` field
   `dossier_allow_hosts` (empty default) grants explicit exceptions by hostname or CIDR. Empty
   means *"no exceptions granted"*, **not** *"match nothing"* — an unconfigured install still
   fetches every public URL, so this is not a preference gate under empty-config-abstains. Same
   shape as `ats_relay_domains`: a safety list legitimately ships restrictive.

2. **Pre-check + post-redirect re-check, residual documented.** Validate the scheme and every
   resolved address **before** navigating; after navigation, re-read the landed URL and **discard
   the body** if it landed somewhere blocked. The residual is stated plainly in this spec, not
   papered over (see *The residual*). Browser-level enforcement is the airtight fix and is **out of
   scope**.

3. **Dossier path only.** The validator lives in its own module so a future call site can adopt it
   deliberately. Ingest is **not** guarded.

## The rule (the load-bearing detail)

### Scheme

`http` and `https` only, compared after `.lower()` (`urlparse` already lowercases the scheme, but
the check states it rather than relying on that). Everything else — `file:`, `javascript:`,
`data:`, `ftp:`, an empty scheme, a bare path — is refused. A URL whose `hostname` is `None` or
empty is refused too: `urlparse("http:///etc/passwd").hostname` is `None`, so an empty host is a
reachable shape, not a hypothetical one.

These are **two independent refusals** and the tests must keep them independent. Almost every
non-http(s) scheme also yields hostname `None`, so a test using `file:///etc/passwd` to exercise
the *scheme* check passes whether or not that check exists — see the testing section, where this
cost five of six originally-proposed cases their meaning.

### Host → addresses

The host is **never classified by parsing it as an IP literal**. It is always handed to the
resolver, because `getaddrinfo` normalizes the obfuscated forms that exist precisely to defeat
literal-parsing. Verified: `2130706433`, `0x7f000001` and `127.1` all resolve to `127.0.0.1`, while
`ipaddress.ip_address("2130706433")` raises rather than recognising it. (`127.0.0.1.`, with the
trailing dot, does **not** resolve — it raises `gaierror` and so fails closed by the rule below. An
earlier draft listed it alongside the other three as resolving; it does not.)

A resolver that raises, or that returns **zero** addresses, blocks the URL — fail closed.

`urlparse().hostname` is the right accessor: it strips IPv6 brackets (`http://[::1]:8080/x` →
`::1`) and takes the **last** `@` in a userinfo trick (`http://user@evil.example@127.0.0.1/` →
`127.0.0.1`).

### Address class

An address is acceptable iff **`is_global and not is_multicast`**, applied to the address *and* to
any IPv4 address embedded inside it.

The base predicate covers every category the approved decision named — loopback, private,
link-local (which already covers the cloud-metadata address `169.254.169.254`), reserved,
multicast, unspecified — as one default-deny expression rather than a six-way `or`. Six redundant
`or` terms would be the equivalent-mutant shape CLAUDE.md warns about: deleting a redundant
conjunct leaves the suite green, so the table would certify nothing.

**The embedding rule closes a real hole.** An IPv6 address can carry an IPv4 destination as its
payload, and `is_global` reads the *wrapper*, not the payload. Measured against the base predicate
alone:

| embedding prefix | example | base predicate |
|---|---|---|
| `::ffff:0:0/96` v4-mapped | `::ffff:127.0.0.1` | already blocked |
| `2002::/16` 6to4 (RFC 3056) | `2002:7f00:1::1` | already blocked |
| `64:ff9b:1::/48` NAT64 local-use (RFC 8215) | `64:ff9b:1::7f00:1` | already blocked |
| **`64:ff9b::/96` NAT64 well-known (RFC 6052)** | `64:ff9b::7f00:1` | **ALLOWED — hole** |
| **`::/96` v4-compatible (RFC 4291, deprecated)** | `::127.0.0.1` | **ALLOWED — hole** |

On a DNS64 network `getaddrinfo` synthesises the NAT64 form for an A-record-only name — including
one pointing at loopback or a private address — and the gateway translates it back. So the base
predicate would permit a navigation that lands on `127.0.0.1`. The v4-compatible hole is the same
shape and was found while checking the first.

The fix is one mechanism, not a prefix denylist: `_embedded_v4(addr)` returns the IPv4 address an
IPv6 address carries (via `.ipv4_mapped`, `.sixtofour`, or the low 32 bits for `::/96` and
`64:ff9b::/96`), or `None`. When it returns an address, **that** address must also satisfy the
predicate. Handling the already-blocked prefixes through the same path is not redundancy — it is
one code path with one mutation witness, and it stops a future CPython classification change from
silently reopening a neighbouring prefix. `64:ff9b:1::/48`'s embedding offset is deployment-specific
under RFC 8215 and therefore not extractable; it is already blocked by the base predicate, and that
is recorded here rather than guessed at.

Full verdict table (all verified against CPython on this machine):

| address | class | verdict |
|---|---|---|
| `127.0.0.1`, `::1` | loopback | blocked |
| `10.0.0.1`, `172.31.255.254`, `192.168.1.1`, `fc00::1`, `fd00::1` | private | blocked |
| `169.254.169.254`, `fe80::1`, `fe80::1%en0` | link-local (incl. cloud metadata, scoped) | blocked |
| `240.0.0.1` | reserved | blocked |
| `224.0.0.1`, `ff02::1` | multicast | blocked |
| `0.0.0.0`, `::` | unspecified | blocked |
| `203.0.113.1`, `192.0.2.1`, `198.51.100.1`, `2001:db8::1` | RFC documentation | blocked |
| `100.64.0.1` | CGNAT (RFC 6598) | blocked |
| `198.18.0.1` | benchmarking (RFC 2544) | blocked |
| `::ffff:127.0.0.1`, `::ffff:10.0.0.1` | v4-mapped | blocked |
| `2002:7f00:1::1` | 6to4 | blocked |
| `64:ff9b::7f00:1` | NAT64 well-known | blocked **by the embedding rule** |
| `::127.0.0.1` | v4-compatible | blocked **by the embedding rule** |
| `192.88.99.1`, `2001:20::1` | global | **allowed** |

**Any** blocked address among a host's answers blocks the URL. A host with an A record for a global
address and a second for `127.0.0.1` cannot smuggle the private one through by ordering.

The base predicate blocks slightly more than the six named categories: `100.64.0.1` (CGNAT) carries
none of the six flags, and neither would a future IANA special-purpose range. That is the same
direction as the approved decision, and the allowlist is the opt-out.

The classification is CPython's `ipaddress`, whose special-purpose table has changed across patch
releases. What this guard promises is *"whatever the running interpreter considers globally
routable"*, not a frozen table. `requires-python` is `>=3.12`; CI runs 3.12/3.13/3.14. A
classification-pinning test (below) asserts the fixture addresses' classes on whatever interpreter
is running, so an interpreter that reclassified one would redden rather than silently change the
tests' meaning.

### Allowlist

`dossier_allow_hosts` is a list of strings, each either a **hostname** or a **CIDR / bare IP**.

**Dispatch is IP-shaped-first, not `/`-keyed.** An earlier draft keyed on the presence of `/`, which
let `10.0.0.300` through as a hostname grant that could never fire — silently — and contradicted
this spec's own bare-IP semantics (`10.0.0.1` would have been read as a hostname and so could not
admit a host resolving to it). Instead: try `ipaddress.ip_network(entry, strict=False)` on every
entry; if it parses, it is a network (a bare IP is a `/32` or `/128`). If it does not parse **and
the entry is IP-shaped** (contains `/`, or consists only of hex digits, dots and colons), raise. Only
a plausible hostname falls through to the hostname branch.

Grant semantics, evaluated only against addresses that would otherwise be blocked:

- **hostname entry** — matches iff the URL's hostname is **exactly equal** (case-insensitively) to
  the entry, after stripping at most one trailing `.` from **both** sides. Not a suffix match: on a
  denylist a suffix widens the safe direction, but on an **allowlist** a suffix match hands
  `evil.example.jobs.invalid` the grant meant for `jobs.invalid`. The trailing-dot normalisation is
  needed because `urlparse('http://jobs.invalid./x').hostname` is `jobs.invalid.`, which would not
  equal a `jobs.invalid` entry — the user's configured exception would silently never fire. (A
  leading `www.` is still **not** stripped: that is a matching nicety for receipt hosts and would be
  wrong against a list the user wrote literally.) An exact grant covers every address that host
  resolves to, which is what a user with a LAN board wants, and is their explicit opt-out.
- **network entry** — a blocked address passes iff it is contained in some allowed network.

Any blocked address neither host-granted nor network-covered blocks the URL. The allowlist grants
**address-class exceptions only** — never a scheme. `file:///etc/passwd` is refused for a host on
the allowlist exactly as for one that is not, because the scheme check runs before the allowlist is
consulted.

**Validation raises at construction — and must not echo the user's network.** This is where the
first draft was wrong twice, and both are the #67 residual recurring:

- It claimed the message reports "the offending entry only, never the whole user block", citing
  `_merge_denylist`. That precedent only ever echoes values that already failed
  `isinstance(k, str)` — never a host. Worse, the draft layered validation on `_str_list`, which
  raises `f"... got {value!r}"` — **the whole list** — and runs first, so both cases in the draft's
  own test list would have dumped every configured internal hostname into an exception.
- `ipaddress`'s own `ValueError` **contains the literal**: verified,
  `ip_network('10.42.7.0/33')` raises `'10.42.7.0/33' does not appear to be an IPv4 or IPv6
  network`. So `raise ... from e` re-exposes the user's subnet through the exception chain.

The rule: validate `dossier_allow_hosts` in its own function, **not** through `_str_list`. Report
the config key, the offending entry's **index**, and the expected *shape* — never the entry's value,
and never the list. Sever the chain with `from None` so `ipaddress`'s message cannot travel. A
config file is one of the few places a user's real private hostnames legitimately live, and an
exception message travels further (logs, bug reports) than the file does.

### Failure handling

**A blocked URL raises `DossierBlocked`; it does not return the empty-dossier shape.** The first
draft argued the opposite, on the premise that raising "would abort nothing usefully". That premise
is false for triage, and the difference is a silent wrong write:

| | exception | returned empty dossier |
|---|---|---|
| `triage/engine.py:82-86` | `continue` → lead kept **out of** `dossiers`, never judged, **no status write**, counted in `report.failures` (printed by `cli.py:219`) | appended to `dossiers` → judged on `jd.markdown == ""` → a confident verdict → **`status: dismiss` written**, `failures=0` |
| `cv/engine.py:66-71` | logs, composes with an empty JD | identical |

So returning would let a blocked fetch quietly bin a lead with nothing in the run summary to show
for it. `DossierBlocked` (defined in `core/urlguard.py`, the module that owns the policy) is caught
by both consumers' existing `except Exception` — **no consumer change is needed**, triage abstains
and reports, cv degrades exactly as it does for a fetch failure today.

Raising also fixes a second defect for free. `DossierCache.get_or_build` (`core/dossier.py:49-69`)
persists whatever the closure returns and `_fresh` serves it for `ttl_days` (default **7** —
`triage/config.py:38`, `cv/config.py:41`). Had the closure returned an empty dossier, a user who
read the block log, added the host to `dossier_allow_hosts` and re-ran would have got the same empty
dossier for a week: closure never called, allowlist never consulted, no log line. Raising means
nothing is written, so the grant takes effect on the next run. ("Already what happens on a failed
fetch" was true of the mechanism but not of the situation — a transient failure has no config remedy
for the cache to mask.)

The log line is at **WARNING** (an unspecified level was a gap; at DEBUG a security refusal is
effectively silent) and names the **reason and the host** — not the full URL, whose query string
carries the user's data, and not the config entry. On a scheme failure there is no host: `UrlVerdict.host`
is `""` and the line names the reason and the scheme instead.

## Components

### `sluice/core/urlguard.py` (new — the testable core)

Purity splits, it does not vanish: DNS is I/O, so this cannot be one pure module like
`track/receipt.py`. It splits the way `Source.parse`/`Source.fetch` already do — a **pure** policy
plus a **thin injected** resolver, so no test touches DNS.

```python
class DossierBlocked(Exception):   # raised by the dossier closure, caught by existing handlers

@dataclass
class UrlVerdict:
    allowed: bool
    reason: str = ""        # "" when allowed; a short stable slug otherwise (tests assert on it)
    host: str = ""          # "" when the URL never yielded one (scheme failure, empty host)

def verdict(host, addrs, *, allow_hosts) -> UrlVerdict:   # PURE — the table-tested policy
def check_url(url, *, allow_hosts, resolve=_resolve) -> UrlVerdict
def _resolve(host) -> list[str]:   # socket.getaddrinfo(host, None) -> address strings
```

`check_url` is impure **only** in that it calls `resolve`. Note honestly what that means for the
"one pure policy" framing: the address-class and allowlist rules live in `verdict`, but the scheme
refusal, the empty-host refusal, the non-ASCII refusal and the resolver-raises/empty refusals are
decided in `check_url`. Those are still deterministic and unit-testable — `check_url` with a fake
`resolve` is a pure function of its inputs — but they are not in `verdict`'s table, so the testing
section covers them explicitly rather than assuming the table reaches them.

The allowlist is checked **inside `verdict`**, not short-circuited in `check_url` before resolution:
one place, one mutation witness. The redundant lookup for an allowlisted host is free — we are about
to fetch it anyway.

Host extraction mirrors `receipt._host`'s hard-won shape: reject non-ASCII **before** any lowering
(U+212A KELVIN folds to ASCII `k` under `str.lower()`, so checking after is already too late; a
genuine IDN arrives as ASCII punycode anyway), and catch `ValueError` from `urlparse` on a malformed
IPv6 literal.

`socket` and `ipaddress` are stdlib; no new dependency.

### `sluice/core/config.py`

`dossier_allow_hosts: list = field(default_factory=list)` on the **root** `Config`, beside
`fetcher: str = "camofox"`. Not in `TriageConfig` or `CvConfig`: `dossier_cache` is called from
**both** sub-apps (`app.py:402` triage via `DOSSIER_DIR`, `app.py:436` cv via `cvcfg.dossier_dir`),
and a security policy that could differ between triage and cv is a bug, not a feature.

`load_config` validates it via a dedicated function (see *Allowlist* — **not** via `_str_list`,
whose error message would leak the whole list).

The field is a **list** default, so `tests/test_sluice_neutral_defaults.py`'s value-keyed sweep picks
it up automatically and requires `[]` — which it is. It becomes the first swept field whose
empty-means-what is *inverted* relative to every other entry: for `accept_titles` empty means "pass
everything through", here it means "grant no exceptions" (the public-URL path is unaffected for a
different reason — the address rule, not the list). A one-line comment at its assertion records
that, so a future reader cannot cite the sweep as licence to loosen the guard.

### `sluice/core/app.py` — the `dossier_cache` fetch closure

~15 lines, no seam change:

```
url present?
  pre-check  check_url(url, allow_hosts=cfg.dossier_allow_hosts, resolve=self._resolve_host)
             blocked -> log WARNING(reason, host); raise DossierBlocked   (no tab opened)
  create_tab(url) -> tid
  post-check evaluate(tid, "location.href") -> landed
             re-run check_url(landed); blocked (or unreadable) -> close_tab; log; raise
  read       evaluate(tid, "document.body.innerText"); close_tab
```

- **The probe is `location.href`, not `document.URL`.** Both reach the final URL through the
  existing `Fetcher.evaluate` seam in the same `{"result": ...}` envelope, so no protocol change is
  needed — and `location.href` is the exact string the two ingest read-back sites already use
  (`ingest/base.py:152`, `ingest/sources/linkedin.py:53`) **and** the one `tests/harness/browser.py`
  answers by exact match.
- **The body is read only after the post-check passes**, so a blocked redirect's response body is
  never pulled into sluice's memory. Scoped honestly: see residual 5 — `create_tab` navigates
  asynchronously and this closure does not settle, so the claim holds for the tab state the probe
  observes, not for every possible interleaving.
- An unreadable landed URL (non-dict result, empty string) **blocks**. "We could not verify where
  this landed" must not read as "fine" on a security check.

Reusing `evaluate` rather than growing a fifth `Fetcher` method is deliberate — a protocol method
for one consumer is the abstraction to refuse. But the *contract* changed even though the signature
did not: `evaluate(tab, "location.href")` was a health signal and now gates a response body. The
`Fetcher` docstring in `core/protocols.py` gains a paragraph saying so, since there is no `Fetcher`
conformance suite to carry it.

### `sluice/core/app.py` — the resolver injection point

`Sluice.__init__(self, config=None, *, sleep=None, today=None, **overrides)` gains a keyword-only
`resolve_host=None`, stored as `self._resolve_host` and threaded into the closure's `check_url`.
`None` means production's `urlguard._resolve`.

**Why a collaborator and not a seam** — the governing rule is already written down at
`docs/ARCHITECTURE.md:284-296`: *"does a user legitimately choose among implementations?"* If yes it
is an adapter seam; if there is exactly one real shape and the only other caller is a test, it is a
passed-in collaborator. The safety rationale there is decisive and stronger than any mechanical
argument: **a registry entry is reachable from config, and config is user-facing** — a seam-resolved
resolver would put an off switch for the SSRF guard under a YAML key. (The mechanical facts also
hold: `**overrides` is validated against `_SEAMS` at `app.py:174-179` so an override would raise, and
`Sluice._resolve` at `app.py:186` is the seam-resolution method, which is why the parameter is named
`resolve_host`.)

**This fires a trigger the architecture of record pre-registered.** `ARCHITECTURE.md:298-302` says a
typo'd collaborator is absorbed by `**overrides` and reported as an unknown *seam* override — loud,
but pointing at the wrong fix — and that this is *"worth tightening if a third collaborator ever
lands"*. `resolve_host` is the third (`sleep`, `today`, then this). The tightening is in scope:
validate collaborator names beside `_SEAMS` so a typo'd `resolve_hosts=` names the collaborators
rather than the four seams.

**The blast radius is six tests, not five.** The first draft's table was built by grepping for
`.triage(` / `.cv(` / `dossier_cache(`; two reviewers independently rebuilt it by wrapping
`DossierCache.fetcher` across the whole suite and found one more — the same method the draft claimed
to have used. Corrected:

| test | url it fetches | wiring |
|---|---|---|
| `tests/e2e/test_a_clean_lead_reaches_rejected.py:83` | `https://remoteok.example/jobs/{1,2}` | `Harness.sluice()` |
| `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py:35` | `https://remoteok.example/jobs/1` | `Harness.sluice()` |
| `tests/e2e/test_an_empty_config_bins_nothing.py:26` | `https://remoteok.example/jobs/1` | `Harness.sluice()` |
| `tests/e2e/test_triage_leaves_my_application.py:28` | `https://remoteok.example/x` | `Harness.sluice()` |
| `tests/functional/test_cv.py::test_cv_run_composes_and_renders` | `https://example.invalid/jobs/1` (`:31`) | **`_HarnessSluice` in `tests/functional/conftest.py:47-55`** |
| `tests/test_app_operations.py:45` | `https://example.invalid/job` | direct `Sluice(...)` |

(`test_app_operations.py:54` passes no url; `:65` runs `no_llm=True`, which skips the dossier —
`triage/engine.py:78`, `if keeps and not no_llm`.)

The functional-tier test is the dangerous one: `cv/engine.py:66-71` swallows the dossier failure and
the scripted backend returns its CV regardless of JD text, so it would stay **green while doing real
DNS every run**. There are therefore **three** wiring sites, not one: `Harness.sluice()`
(`tests/harness/config.py:87-93` — note `build_harness` itself constructs no `Sluice`),
`tests/functional/conftest.py`'s `_HarnessSluice`, and `test_app_operations.py:45` by hand.

**The durable fix is not the hand-list.** The suite performs zero DNS today, so a session-scoped
autouse fixture in `tests/conftest.py` replaces `socket.getaddrinfo` with a raiser. Then the next
test that reaches a resolver fails loudly instead of requiring this enumeration to be redone
correctly — which it was not, the first time.

### `sluice.yaml.example`

A commented `dossier_allow_hosts` block in the adapter-seams region, documenting both entry forms and
that an empty list grants nothing rather than blocking everything. Values are pinned here rather than
left to the implementer, because a CIDR is exactly where a real subnet leaks: use `jobs.invalid` and
`10.0.0.0/8` — a whole RFC 1918 block encodes nothing about anyone's network, where `10.42.7.0/24`
would.

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
5. **Navigation is asynchronous and this closure does not settle.** `Camofox.create_tab` fires a
   separate async `/navigate`; every other read-back site in the repo sleeps first and the dossier
   closure does not. An unsettled redirect can pass the post-check on the pre-redirect URL, with the
   body probe then returning post-redirect content. It is not witnessable offline —
   `tests/harness/browser.py:42` answers `location.href` synchronously — so it is recorded here
   rather than tested. This bounds the "body never pulled into memory" claim above.
6. **`64:ff9b:1::/48` is not extractable.** RFC 8215's local-use NAT64 prefix embeds the IPv4 address
   at a deployment-specific offset, so `_embedded_v4` cannot decode it. It is blocked today by the
   base predicate; if CPython ever reclassified it as global, the embedding rule would not catch it.

## Invariants upheld

- **Never-clobber / never-regress.** No status or note write is added or changed. Verified with the
  invariant reviewer: the guard adds no vault write, and raising rather than returning is what keeps
  triage from writing a status derived from a blocked fetch.
- **Empty config abstains.** No new preference gate. `dossier_allow_hosts` is a safety allowlist
  whose empty default leaves the public-URL path exactly as it is today; the neutral-defaults sweep
  covers it as a list-defaulting field and passes, with a comment recording why its "empty" is
  inverted relative to the preference gates around it.
- **Fail loudly at construction.** A malformed allowlist entry raises, naming the key, the entry
  index and the expected shape — never the value.
- **No silent failures.** `DossierBlocked` propagates to the consumers' existing per-item handlers,
  which is the repo's documented per-item-isolation pattern; the WARNING log and triage's
  `report.failures` count make a refusal visible.
- **Standard-library only.** `socket`, `ipaddress`, `urllib.parse`. No new dependency.
- **Pure/impure split.** `verdict` is pure and table-tested; the resolver is injected at two levels
  (`check_url(resolve=...)`, `Sluice(resolve_host=...)`); **no test performs DNS**, enforced by a
  session fixture rather than by an enumeration.
- **Adapter seams.** No seam change; the post-redirect read goes through existing `Fetcher.evaluate`,
  with the contract change recorded in the protocol docstring.
- **Neutrality.** No hostnames, absolute paths or real-network addresses in `sluice/` or `tests/`.

## Testing (synthetic, offline)

**Pure `verdict` table** — every row of the full verdict table above, v4 and v6, including the rows
the first draft omitted (`::ffff:127.0.0.1`, `::ffff:10.0.0.1`, `2002:7f00:1::1`, `fe80::1%en0`,
`198.18.0.1`) and the two embedding-rule rows (`64:ff9b::7f00:1`, `::127.0.0.1`), each asserted
**blocked**. The table is adversarial rather than one-row-per-chosen-case: the shapes come from what
a DNS64/6to4/v4-mapped path can actually synthesise, not from the categories the author already had
in mind.

**Scheme and empty host, kept independent.** `ftp://host.invalid/x` and
`file://allowed.invalid/etc/passwd` carry a *host*, so they uniquely witness the scheme check;
`http:///etc/passwd` (hostname `None`) uniquely witnesses the empty-host refusal. Each asserts the
specific `reason` slug, and the scheme cases supply a fake resolver mapping the host to a global
address so the address rule cannot be what blocks them. Also: `HTTPS://Example.COM/a` passes (case);
`http://user@evil.example@127.0.0.1/` blocks (userinfo); `http://[::1]:8080/x` blocks (bracketed
literal); `https://[abc` blocks rather than raising; a non-ASCII host blocks before any lowering.
A separate test asserts the prose claim that **the allowlist never grants a scheme** — a
`file://` URL whose host is allowlisted still blocks, with the scheme reason.

**Resolver behaviour:** two addresses where only one is private → block; a resolver that raises →
block; zero addresses → block; an unparseable answer → block. A named test asserts `2130706433`,
`0x7f000001` and `127.1` reach the resolver as-is rather than being literal-parsed, and that
`127.0.0.1.` fails closed via `gaierror`.

**Allowlist:** an exact hostname grant admits an otherwise-blocked host; `evil.example.jobs.invalid`
against a `jobs.invalid` grant is **not** admitted; `http://jobs.invalid./x` **is** admitted by a
`jobs.invalid` grant (trailing dot); a CIDR grant admits inside and refuses outside; a bare IP
behaves as a single-address network; `10.0.0.300` **raises**; a non-string entry raises; and a test
asserts the raised message contains **neither** the entry value nor any other list element, and that
`__cause__` is `None`.

**Call-site behaviour** (fake fetcher, no browser): a blocked lead URL raises `DossierBlocked` and
**never calls `create_tab`**; an allowed URL fetches as today; a fetcher whose `location.href`
reports a blocked destination raises with `document.body.innerText` **never evaluated** and the tab
closed; an unreadable `location.href` blocks. The absence assertions record the fake's **exact probe
sequence** and pair with a positive control (the allowed-URL case, where the body probe *does*
appear) — otherwise "never evaluated" passes vacuously if `create_tab` returned a falsy tid.
Two consumer-level tests pin the reason raising was chosen: after a blocked fetch, a triage run
leaves the lead's status **byte-unchanged** and reports it in `report.failures`, and no dossier file
is written (so the allowlist remedy is not masked for `ttl_days`).

**Resolver injection:** `Sluice(resolve_host=...)` threads the fake into the closure — a host the
fake maps to a global address fetches, one it maps to a private address does not. A companion test
asserts the **production default** is `urlguard._resolve` when the parameter is omitted, so a wiring
that always used the fake cannot ship green. The harness fake **raises on an unmapped host**: one
that mapped everything to a global address would make every e2e and functional test pass regardless
of the guard.

**Hermeticity:** a session-scoped autouse fixture in `tests/conftest.py` replaces
`socket.getaddrinfo` with a raiser, and a test asserts the fixture is active. This is the guard that
makes the six-test enumeration non-load-bearing.

**Config:** `Config().dossier_allow_hosts == []`; the same through `load_config(None)` with
`SLUICE_CONFIG` cleared (otherwise the assertion reads the developer's own config and passes for the
wrong reason); a round-trip test proving the neutral default costs no override capability.

**Classification pinning:** `test_fixture_addresses_are_globally_classified` asserts
`192.88.99.1` and `2001:20::1` are `is_global=True, is_multicast=False` on the running interpreter.
Both are special-purpose ranges, so this pins the premise the allowed-case fixtures rest on and
discharges the `requires-python` concern: an interpreter that reclassified either reddens here rather
than silently inverting a test's meaning.

### Fixture addresses

The allowed case cannot use the RFC documentation ranges: `203.0.113.1`, `192.0.2.1`,
`198.51.100.1` and `2001:db8::1` are all `is_private` in CPython — they are exactly what this guard
blocks. An earlier draft proposed `93.184.216.34` and `8.8.8.8`; both are declined. They are real,
routable, operator-assigned addresses, the first shipped with an ownership annotation no offline test
can falsify, and the second is a live DNS resolver address appearing in a DNS-guard fixture. The
repo's only existing IP literals are `127.0.0.1` (`sluice/core/camofox.py:14`) and `192.0.2.1`
(`tests/test_track_receipt.py:436`) — no precedent for either.

Use **`192.88.99.1`** (RFC 3068 6to4 relay anycast, withdrawn by RFC 7526) and **`2001:20::1`**
(RFC 7343 ORCHIDv2). Both are `is_global=True, is_multicast=False`, neither is operator-run or
routed, and neither carries an ownership claim to annotate. Note the near-miss: `192.88.99.0/24` is
relay *anycast* and embeds no address, so the embedding rule does not touch it — had the rule been a
prefix denylist including it, this fixture and that hardening would have collided. Hostnames stay in
the RFC-reserved family (`example.invalid`, `jobs.invalid`).

### Mutation witnesses (named)

Mutate by **moving or deleting**, never adding. Run each by node id and confirm the named test
reddens *and* that no pre-existing test is what catches it.

- Delete `and not is_multicast` → the multicast rows redden. (Load-bearing, verified: `224.0.0.1`
  and `ff02::1` are both `is_global=True`.)
- Delete the `_embedded_v4` recheck → the `64:ff9b::7f00:1` and `::127.0.0.1` rows redden.
- Delete the "every address must pass" quantifier → the two-address test reddens.
- Delete the scheme check → `ftp://host.invalid/x` reddens (and confirm no empty-host case catches it).
- Delete the empty-host refusal → `http:///etc/passwd` reddens (and confirm no scheme case catches it).
- Delete the pre-check → "blocked URL never calls `create_tab`" reddens.
- Delete the post-check → the redirected-fetcher test reddens.
- Replace exact-equality allowlist matching with a suffix test → `evil.example.jobs.invalid` reddens.
- Delete the trailing-dot normalisation → the `http://jobs.invalid./x` grant test reddens.
- Delete the `resolve_host` thread-through → the injection test reddens **and the session
  `getaddrinfo` guard trips**. (Stated that way deliberately: "the blast-radius tests start doing
  DNS" is not an observable red signal without that guard — the functional-tier test would go green.)
- Change `raise DossierBlocked` to `return` the empty shape → the triage status-unchanged test reddens.

**Commit before witnessing.** A witness script restoring via `git checkout -- <file>` wipes
uncommitted changes in that file, and the empty post-run diff hides the loss.

## Scope

`core/urlguard.py` (new); one root-`Config` field plus a dedicated validator; one keyword-only
`Sluice.__init__` parameter plus the collaborator-name tightening
`ARCHITECTURE.md:298-302` pre-registered; ~15 lines in `dossier_cache`'s closure; a `Fetcher`
docstring paragraph in `core/protocols.py`; a `sluice.yaml.example` block; and on the test side
`tests/conftest.py` (the `getaddrinfo` guard), `tests/harness/config.py`, `tests/functional/conftest.py`
and `tests/test_app_operations.py`.

Docs: `docs/ARCHITECTURE.md` needs the `core/` module inventory to gain `urlguard.py`, the injected-
collaborator enumeration (`:271-283`) to gain `resolve_host`, and the third-collaborator trigger
(`:300-302`) resolved rather than left dangling. `.rulesync/rules/CLAUDE.md` describes the four seams
but does not enumerate collaborators, so it needs no change — and it is human-gated regardless, so
any change there is escalated, never auto-applied.

No new dependency, no adapter-seam change, no new CLI command, no protocol *signature* change.
Ingest is untouched.

## Decisions taken at review, and what remains

Settled (the three items the first draft left open are resolved):

1. **Address rule** — `is_global and not is_multicast`, plus the `_embedded_v4` recheck. The
   tightening beyond the six named categories is kept; the embedding rule was added because the base
   predicate had two live holes.
2. **`requires-python`** — no floor change. The classification-pinning test discharges it by
   asserting the premise on whatever interpreter runs.
3. **Fixture addresses** — `192.88.99.1` and `2001:20::1`, no ownership annotation.

Remaining for the implementer, not the reader: nothing in this spec is a TBD. The one judgment call
deliberately *not* resolved in code is residual 6 (`64:ff9b:1::/48` is undecodable); it is documented
rather than guessed at, and if it ever needs closing that is a prefix-length policy decision, not a
bug fix.
