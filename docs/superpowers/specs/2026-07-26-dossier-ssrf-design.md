# Harden the dossier fetcher against SSRF (#18)

**Status:** design approved 2026-07-26 (brainstormed in conversation); this document is the
written form, not a new negotiation. Three decisions were settled and are recorded verbatim
below — they are not re-opened here.
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
guard costs one small module. What it must **not** do is regress the ordinary case — a lead on
a public job board must fetch exactly as it does today.

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

### Host → addresses

The host is **never classified by parsing it as an IP literal**. It is always handed to the
resolver, because `getaddrinfo` normalizes the obfuscated forms that exist precisely to defeat
literal-parsing (verified: `2130706433`, `0x7f000001`, `127.1` and `127.0.0.1.` all resolve to
`127.0.0.1`, and `ipaddress.ip_address("2130706433")` raises rather than recognising it). A
resolver that raises, or that returns **zero** addresses, blocks the URL — fail closed.

`urlparse().hostname` is the right accessor: it strips IPv6 brackets (`http://[::1]:8080/x` →
`::1`) and takes the **last** `@` in a userinfo trick (`http://user@evil.example@127.0.0.1/` →
`127.0.0.1`).

### Address class

An address is acceptable iff **`is_global and not is_multicast`**.

That covers every category the approved decision named — loopback, private, link-local (which
already covers the cloud-metadata address `169.254.169.254`), reserved, multicast, unspecified —
as one default-deny predicate rather than a six-way `or`. Verified against every category, v4 and
v6:

| address | class | verdict |
|---|---|---|
| `127.0.0.1`, `::1` | loopback | blocked |
| `10.0.0.1`, `172.31.255.254`, `192.168.1.1`, `fc00::1`, `fd00::1` | private | blocked |
| `169.254.169.254`, `fe80::1` | link-local (incl. cloud metadata) | blocked |
| `240.0.0.1` | reserved | blocked |
| `224.0.0.1`, `ff02::1` | multicast | blocked |
| `0.0.0.0`, `::` | unspecified | blocked |
| `203.0.113.1`, `192.0.2.1`, `198.51.100.1`, `2001:db8::1` | RFC documentation | blocked |
| `100.64.0.1` | CGNAT (RFC 6598) | blocked |
| `8.8.8.8`, `93.184.216.34`, `2606:2800:220:1:248:1893:25c8:1946` | global | **allowed** |

Two consequences are worth stating rather than discovering later:

- **It blocks slightly more than the enumeration named.** `100.64.0.1` (CGNAT) carries **none** of
  the six flags — an enumeration-shaped rule would have let it through. So would any future
  IANA special-purpose range CPython adds. The `is_global` form tracks CPython's special-purpose
  table automatically. This is the same direction as the approved decision (block by default), not
  a widening, and the allowlist remains the opt-out for anyone who wants a CGNAT destination.
- **Six redundant `or` terms would be unwitnessable.** A predicate written as
  `is_global and not is_multicast and not is_loopback and not is_private ...` is the
  equivalent-mutant shape CLAUDE.md warns about: deleting any redundant conjunct leaves the suite
  green, so the test table would certify nothing. One predicate, one witness per category in the
  table.

**Any** blocked address among a host's answers blocks the URL. A host with an A record for a public
address and a second for `127.0.0.1` cannot smuggle the private one through by ordering.

The classification is CPython's `ipaddress`, whose special-purpose table has changed across patch
releases. What this guard promises is therefore *"whatever the running interpreter considers
globally routable"*, not a frozen table pinned in sluice. `pyproject.toml` declares
`requires-python = ">=3.12"` and CI runs 3.12/3.13/3.14; flagged for review rather than silently
assumed harmless.

### Allowlist

`dossier_allow_hosts` is a list of strings, each either a **hostname** or a **CIDR / bare IP**.
An entry containing `/` must parse as an `ipaddress` network or construction raises — a
typo'd `10.0.0.0/33` silently degrading to "a hostname that never matches" would leave a user
believing they granted an exception they did not.

Grant semantics, evaluated only against addresses that would otherwise be blocked:

- **hostname entry** — matches iff the URL's hostname is **exactly equal** (case-insensitively) to
  the entry. Not a suffix match: on a denylist a suffix widens the safe direction (more hosts
  read as multi-tenant), but on an **allowlist** a suffix match hands `evil.example.jobs.lan` the
  grant meant for `jobs.lan`. An exact grant covers every address that host resolves to — which is
  what a user with a LAN board wants, and is their explicit opt-out.
- **network entry** — a blocked address passes iff it is contained in some allowed network.

Any blocked address neither host-granted nor network-covered blocks the URL. The allowlist grants
**address-class exceptions only** — it never grants a scheme. `file:///etc/passwd` is refused for a
host on the allowlist exactly as it is for one that is not, because the scheme check runs in
`check_url` before the allowlist is ever consulted.

Validation raises at construction (house style: `_select_backend`, `_merge_denylist`,
`_str_list`), naming the key and a valid form. Like `_merge_denylist`, the message reports the
**offending entry only, never the whole user block**: a config file is one of the few places a
user's real private hostnames legitimately live, and an exception message travels further (logs,
bug reports) than the file does.

### Failure handling

A blocked URL is **logged and returns the empty-dossier shape**, never raised. `dossier_cache`'s
closure already returns `{"jd": {"markdown": ""}, "glassdoor": {}}` for a lead with no url, and
both consumers wrap `get_or_build` in a per-item `except` (`triage/engine.py:83-86` appends to
`report.failures`; `cv/engine.py:66-71` logs and continues), so raising would work but would
abort nothing usefully. `DossierCache` then caches an empty dossier for `ttl_days` — which is
**already** what happens when a fetch fails today, so it is not a new behaviour and is not solved
here.

The log line names the **reason and the host**, not the full URL: a lead URL carries query strings
(tracking ids, search params) that are the user's data, and the host is the whole basis of the
decision. Same discipline as #41/#67's redaction work.

## Components

### `sluice/core/urlguard.py` (new — the testable core)

Purity splits, it does not vanish: DNS is I/O, so this cannot be one pure module like
`track/receipt.py`. It splits the way `Source.parse`/`Source.fetch` already do — a **pure** policy
plus a **thin injected** resolver, so no test touches DNS.

```python
@dataclass
class UrlVerdict:
    allowed: bool
    reason: str = ""        # "" when allowed; a short stable slug when not
    host: str = ""          # for the log line

def verdict(host, addrs, *, allow_hosts) -> UrlVerdict:   # PURE — the table-tested policy
def check_url(url, *, allow_hosts, resolve=_resolve) -> UrlVerdict   # scheme + host, then resolve
def _resolve(host) -> list[str]:   # socket.getaddrinfo(host, None) -> address strings
```

`check_url` is impure **only** in that it calls `resolve`; the scheme/host extraction is pure and
the class/allowlist decision lives entirely in `verdict`, which the test table drives directly with
a list of address strings. The allowlist is checked **inside `verdict`**, not short-circuited in
`check_url` before resolution: one place, one mutation witness. The redundant DNS lookup for an
allowlisted host is free — we are about to fetch it anyway.

Host extraction mirrors `receipt._host`'s hard-won shape: reject non-ASCII **before** any lowering
(U+212A KELVIN folds to ASCII `k` under `str.lower()`, so checking after is already too late; a
genuine IDN arrives as ASCII punycode anyway), and catch `ValueError` from `urlparse` on a
malformed IPv6 literal. It does **not** strip a leading `www.` — that is a matching nicety for
receipt hosts and would be wrong here, where the string is compared against an allowlist the user
wrote literally.

`socket` and `ipaddress` are stdlib; no new dependency.

### `sluice/core/config.py`

`dossier_allow_hosts: list = field(default_factory=list)` on the **root** `Config`, beside
`fetcher: str = "camofox"`. Not in `TriageConfig` or `CvConfig`: `dossier_cache` is called from
**both** sub-apps (`app.py:402` triage via `DOSSIER_DIR`, `app.py:436` cv via `cvcfg.dossier_dir`),
so a per-sub-app knob would drift and one sub-app would silently fetch what the other refuses. The
precedent is `Config.baseline_rel`, whose own comment records that a seam-level concern must live
where the resolved adapter can reach it.

`load_config` validates via a new entry-shape check layered on `_str_list` (which already rejects a
YAML scalar exploding into single characters).

The field is a **list** default, so `tests/test_sluice_neutral_defaults.py`'s value-keyed sweep
picks it up automatically and requires it to default `[]` — which it does. That is the correct
outcome and not a conflict: the sweep guards *preference* gates that must abstain, and this list
genuinely ships empty. The reason an empty allowlist is safe is different from the reason an empty
`accept_titles` is safe, and the spec says so out loud: empty here means *no exceptions granted*,
and public URLs — the ordinary case — are unaffected.

### `sluice/core/app.py` — the `dossier_cache` fetch closure

~15 lines, no seam change:

```
url present?
  pre-check  check_url(url, allow_hosts=self.config.dossier_allow_hosts)
             blocked -> log(reason, host); return the empty dossier shape  (no tab opened)
  create_tab(url) -> tid
  post-check evaluate(tid, "location.href") -> landed
             re-run check_url(landed); blocked (or unreadable) -> close_tab; log; empty dossier
  read       evaluate(tid, "document.body.innerText"); close_tab
```

Two details:

- **The probe is `location.href`, not `document.URL`.** Both reach the final URL through the
  existing `Fetcher.evaluate` seam in the same `{"result": ...}` envelope, so no protocol change is
  needed — but `location.href` is the exact string the two ingest read-back sites already use
  (`ingest/base.py:152`, `ingest/sources/linkedin.py:53`) **and** the one `tests/harness/browser.py`
  answers exactly (it matches probes by exact string, deliberately). Using `document.URL` would
  have meant a second idiom and a harness change for no gain.
- **The body is read only after the post-check passes.** Ordering it that way means a blocked
  redirect's response body is never pulled into sluice's memory at all, only left in the browser.

An unreadable landed URL (a non-dict result, an empty string) **blocks**. "We could not verify where
this landed" must not read as "fine" on a security check; any conforming `Fetcher` can answer
`evaluate`, so the cost is a fetcher that lies about its own location.

### `sluice/core/app.py` — the resolver injection point (required, not optional)

`Sluice.__init__(self, config=None, *, sleep=None, today=None, **overrides)` gains a fourth
keyword-only parameter, `resolve_host=None`, stored as `self._resolve_host` and threaded into the
`dossier_cache` closure's `check_url` call. `None` means production's `urlguard._resolve`.

It is a **plain keyword-only parameter beside `sleep` and `today`, not a `**overrides` entry**:
`__init__` validates `set(overrides) - set(_SEAMS)` and raises `UnknownAdapter` on anything that is
not one of the four seams (`app.py:174-179`), so passing `resolve_host` as an override would raise.
A resolver is not an adapter seam — it is the same shape as the injected clock and sleep.

Named `resolve_host`, not `resolve`: `Sluice._resolve` is already the seam-resolution method
(`app.py:186`), and `self._resolve` would shadow it.

**This is load-bearing, not a convenience.** Without it the guard puts a DNS lookup into an
otherwise fully-offline suite. Enumerated (grep over `tests/`, not hand-listed), the existing tests
that reach the dossier closure are:

| test | url it fetches |
|---|---|
| `tests/e2e/test_a_clean_lead_reaches_rejected.py:83` | `https://remoteok.example/jobs/{1,2}` |
| `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py:35` | `https://remoteok.example/jobs/1` |
| `tests/e2e/test_an_empty_config_bins_nothing.py:26` | `https://remoteok.example/jobs/1` |
| `tests/e2e/test_triage_leaves_my_application.py:28` | `https://remoteok.example/x` |
| `tests/test_app_operations.py:45` | `https://example.invalid/job` |

(`test_app_operations.py:54` passes no url and never reaches the check; `:65` runs `no_llm=True`,
which skips the dossier entirely — `triage/engine.py:79`, `if keeps and not no_llm`.)

Every one of those hosts is in the RFC-reserved `.example` / `.invalid` family, which by design
**does not resolve** (verified: `getaddrinfo` raises `gaierror` for both). So with no injection
point the guard would fail these tests in *both* directions at once — each would attempt a real DNS
query against the machine's configured nameserver (breaking the offline guarantee and adding
resolver-timeout latency to a 2.2-second suite), and would then fail closed, emptying the dossier
and breaking `assert d["jd"]["markdown"] == "JD BODY"`.

The fix is one line per affected test: pass a fake `resolve_host` mapping the fixture host to a
global address. `tests/harness/config.py`'s `build_harness` absorbs it for the four e2e tests, so
only `test_app_operations.py:45` changes by hand.

### `sluice.yaml.example`

A commented `dossier_allow_hosts` block in the adapter-seams region (it is a fetcher-adjacent root
key), documenting both entry forms, the block-by-default semantics, and that an empty list grants
nothing rather than blocking everything. Generic placeholders only — never a real private hostname.

## The residual

Stated plainly, in the same spirit as the #16 CAS micro-window. **This raises the bar; it is not a
hermetic seal.**

1. **DNS rebinding.** Our `getaddrinfo` and the browser's own resolution are two separate lookups.
   A host whose answer changes between them — a short-TTL record, or a resolver answering
   differently per query — can pass the pre-check and still be navigated to an internal address.
   The post-check does not catch it: the *URL* is unchanged; only the address behind it moved.
2. **The request precedes the post-check.** A redirect to an internal destination means the browser
   has **already sent** the request there. We withhold the **data** (the body is discarded and
   nothing is written), not the request. A blind-SSRF side effect — a `GET` that mutates internal
   state — is not prevented.
3. **Browser-level enforcement is the airtight fix and is out of scope.** A request-interception
   policy in Camofox (or an egress proxy) would decide per-connection, after resolution, with no
   window at all. That is a fetcher-implementation feature, not a policy module, and #18 does not
   buy it.
4. **Port is not policy.** `http://<public-host>:22/` is permitted: the destination is a public
   address, so it is not an internal-network reach, and filtering ports would refuse legitimate
   boards on non-standard ports. Out of scope, deliberately.

These are documented rather than hidden because the failure mode of a *claimed*-airtight guard is
worse than a *stated*-partial one: the next person to add a call site would adopt it believing it
seals the hole.

## Invariants upheld

- **Never-clobber / never-regress.** No status or note write is added or changed. A blocked fetch
  produces the same empty dossier a failed fetch already produces.
- **Empty config abstains.** No new preference gate. `dossier_allow_hosts` is a safety allowlist
  whose empty default leaves the ordinary (public-URL) path exactly as it is today; the
  neutral-defaults sweep covers it as a list-defaulting field and passes.
- **Fail loudly at construction.** A malformed allowlist entry raises, naming the key and a valid
  form, rather than degrading to a grant that never fires.
- **Standard-library only.** `socket`, `ipaddress`, `urllib.parse`. No new dependency.
- **Pure/impure split.** `verdict` is pure and table-tested; the resolver is injected at two levels
  (`check_url(resolve=...)` for unit tests, `Sluice(resolve_host=...)` for anything driving the real
  closure); **no test performs DNS**, and the suite stays hermetic and sub-second.
- **Adapter seams.** No seam change. The post-redirect read goes through the existing
  `Fetcher.evaluate`.
- **Neutrality.** No hostnames, no absolute paths, no private addresses from any real network in
  `sluice/` or `tests/`. See the fixture note below.

## Testing (synthetic, offline)

**Pure `verdict` table** — one case per row of the address-class table above (v4 **and** v6:
loopback, private, link-local incl. `169.254.169.254`, reserved, multicast, unspecified,
documentation, CGNAT, global), so each category is independently witnessed rather than certified by
one predicate.

**Scheme and host** (through `check_url` with a fake resolver): `http`/`https` pass; `file:`,
`javascript:`, `data:`, `ftp:`, a bare path and an empty scheme block; `HTTPS://Example.COM/a`
passes (case); `http:///etc/passwd` (hostname `None`) blocks; `http://user@evil.example@127.0.0.1/`
blocks (userinfo trick, hostname is the last `@`); `http://[::1]:8080/x` blocks (bracket-stripped
literal); a malformed IPv6 literal (`https://[abc`) blocks rather than raising; a non-ASCII host
blocks before any lowering.

**Resolver behaviour:** a host resolving to **two** addresses where only **one** is private must
block (multi-A-record smuggling); a resolver that **raises** blocks; a resolver returning **zero**
addresses blocks; a resolver returning an unparseable string blocks. A named test asserts the
obfuscated forms (`2130706433`, `0x7f000001`, `127.1`, `127.0.0.1.`) reach the resolver as-is rather
than being literal-parsed — the reason this design resolves instead of classifying the string.

**Allowlist:** an exact hostname grant admits an otherwise-blocked host; a **suffix** of a granted
host (`evil.example.jobs.invalid` against a `jobs.invalid` grant) is **not** admitted; a CIDR grant
admits an address inside it and refuses one outside; a bare-IP grant behaves as a single-address
network; an entry containing `/` that is not a valid network **raises at construction**; a
non-string entry and a YAML scalar raise.

**Call-site behaviour** (a fake fetcher, no browser): a blocked lead URL returns the empty dossier
and **never calls `create_tab`**; an allowed URL fetches as today; a fetcher whose `location.href`
reports a blocked destination yields an empty dossier with **`document.body.innerText` never
evaluated** and the tab closed; an unreadable `location.href` blocks. Assert the *absence* of the
write and of the body probe, not merely a verdict value — a tier/verdict assertion alone would pass
for the wrong reason if the closure ignored it.

**Resolver injection:** a test asserts `Sluice(resolve_host=...)` threads the fake all the way into
the closure — i.e. that a lead url whose host the fake maps to a **global** address fetches, and one
the fake maps to a **private** address does not. Without that assertion the injection could be wired
to nothing and the five tests above would pass for the wrong reason (they would simply resolve
nothing and block, or resolve for real). A companion test asserts the **production default** is
`urlguard._resolve` when `resolve_host` is not passed — otherwise a wiring that always used the fake
would ship green.

**Config:** `Config().dossier_allow_hosts == []`; the same through `load_config(None)` with
`SLUICE_CONFIG` cleared (otherwise the assertion silently reads the developer's own config and
passes for the wrong reason — the trap already documented in the neutral-defaults tests); a
round-trip test proving the neutral default costs no override capability.

### Fixture note — a real constraint, flagged for the neutrality reviewer

Every RFC-reserved documentation address this repo would normally reach for is `is_private` in
CPython (`203.0.113.1`, `192.0.2.1`, `198.51.100.1`, `2001:db8::1` — all verified), so the
**allowed** case cannot use one: the documentation ranges are exactly what this guard blocks. The
proposal is `93.184.216.34` (the IANA-operated `example.com` address) and
`2606:2800:220:1:248:1893:25c8:1946` for v6, with the resolver injected so nothing is ever
contacted. Hostnames stay in the RFC-reserved family (`example.invalid`, `jobs.invalid`). This is a
genuine collision between the fixture convention and the feature, not an oversight — settle it at
`/review-plan` rather than in implementation.

### Mutation witnesses (named)

Per CLAUDE.md, mutate by **moving or deleting**, never adding, and run the named node id to confirm
it reddens (a mutation killed by a pre-existing test witnesses nothing about a new one).

- Delete `and not is_multicast` from the class predicate → the multicast table rows redden.
- Delete the "every address must pass" quantifier (accept if **any** address is global) → the
  two-address multi-A-record test reddens.
- Delete the pre-check block in the closure → the "blocked URL never calls `create_tab`" test
  reddens.
- Delete the post-check block → the redirected-fetcher test reddens.
- Delete the exact-equality allowlist comparison in favour of a suffix test → the
  `evil.example.jobs.invalid` test reddens.
- Delete the `resolve_host` thread-through in `dossier_cache` (fall back to the module default) →
  the injection test reddens **and** the five tests in the blast-radius table start doing DNS.

Each mutant must be run **by node id** against its named test, and the test confirmed to be the one
that catches it — a mutation killed by a pre-existing test witnesses nothing about a new one.

**Commit before witnessing.** A witness script that restores via `git checkout -- <file>` wipes
uncommitted working-tree changes in that file, and the empty post-run diff hides the loss.

## Scope

One new module (`core/urlguard.py`), one root-`Config` field plus its loader validation, one
keyword-only `Sluice.__init__` parameter, ~15 lines in `dossier_cache`'s closure, a
`sluice.yaml.example` block, and the resolver wiring in `tests/harness/config.py` plus one
hand-edited existing test. No new dependency, no adapter-seam change, no new CLI command, no
protocol change. Ingest is untouched.

## Open items for `/review-plan`

1. The address rule is `is_global and not is_multicast` rather than the six-way enumeration the
   brainstorm named. It blocks a strict superset (CGNAT `100.64.0.0/10` plus any future IANA
   special-purpose range), which is the same safe direction — confirm the tightening is wanted.
2. `ipaddress`'s special-purpose table is CPython's and has moved across patch releases;
   `requires-python` is `>=3.12`. Confirm this needs no floor change.
3. The allow-case fixture address (see the fixture note) — a neutrality call.
