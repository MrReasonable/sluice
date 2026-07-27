# Lead staleness — a months-old posting should not look like today's (#9)

**Status:** design approved 2026-07-27; revised twice after `/review-plan`.
Round 1 (5 reviewers): 0 Critical, 11 High. Round 2 (4 reviewers; 2 stalled, 1 relaunched, the
cross-cutting checks done by hand): 0 Critical, 6 High. The five user-settled decisions are unchanged
throughout; everything underneath them has been replaced at least once.

**Issue:** #9 — `feat(leads): lead staleness — a months-old posting should not look like today's`
**Sub-apps:** `core` (the policy, the config knob, one `Store` contract addition), `cli`/`core.app`
(`leads expire`), `cv`, `apply`

Three things this spec got wrong twice, kept here because the implementer will be tempted to write
them back:

- **A guard on the in-memory note is an equivalent mutant.** Probed against the real `Vault`:
  deleting expire's status guard and running it with `is_application_owned(note.status)` produce
  *byte-identical* results — both write `dismiss` over an `applied` note. Only a **fresh re-read
  inside the CAS transform** refuses. Both existing call sites (`core/app.py:416`,
  `triage/apply.py:15`) use the in-memory form, so copying the local idiom is the failure.
- **`today` is a zero-arg callable, not a string** (`ingest/sink.py:26,31`; every test passes
  `today=lambda: "2026-07-07"`). Draft 2 specified `Sluice(today="2026-07-27")`, which breaks
  `VaultSink`, and binding the real convention into the policy gives
  `date.fromisoformat(<function>)` → `TypeError`, which `except ValueError` does not catch.
- **Every specified test pinned the OFF state.** Four independent drops would leave the whole
  feature inert with a green suite. Off-by-default is the safety property; it is not evidence the
  thing works.

## Problem

A lead has no notion of age. `Lead.first_seen` and `Lead.last_seen` already exist
(`core/leads.py:85-86`) and `ingest/sink.py:35-37` already stamps both — `first_seen` only when
empty, `last_seen` on every write — so the *data* is there and has been all along. Nothing reads
it. A posting scraped six months ago sits in the store looking exactly like one scraped this
morning, and the pipeline will tailor a CV for it and stage an application.

That costs two different things. Tailoring a CV for a closed role burns a compose call and a render
for nothing. Applying to one is worse than doing nothing: it consumes a slot in the user's own
tracking, and it is not recoverable by editing a note.

## The tension in the issue text, and how it resolves

#9 says `expire` should "move them to a terminal status" **and** "never touch an application-owned
status (`applied`, `phone_screen`, `interview`, `offer`, `rejected`)". Those contradict each other.
`_TERMINAL = ("rejected", "accepted", "withdrawn")` (`core/status.py:57`) and **all three are in
`APPLICATION_OWNED`** (`core/status.py:15-18`). There is no triage-owned terminal to move to.

The triage-owned end state is `dismiss`. So `expire` writes `dismiss`, never a `_TERMINAL`.

## The five settled decisions

1. **Expire writes `dismiss`,** with the prior status recorded in the audit note so a human can
   reverse it. Not a `_TERMINAL` (impossible, above); not a new `stale_at` frontmatter key (that
   would leave `status` unchanged and oblige triage, cv and apply each to learn a new key — three
   new read sites, three new ways to silently not filter).

2. **Eligible statuses: `new`, `shortlist`, `research`, `needs_review`** — every `TRIAGE_OWNED`
   state except `dismiss`, which is already the destination. `shortlist` is included deliberately:
   it is the state `compose_cv` and `apply prep` read, so it is where staleness costs money.

3. **Report by default; `--expire [SLUG...]` writes.** Bare `sluice leads expire` prints the stale
   set and writes nothing. `--expire` with no arguments dismisses everything reported; `--expire
   SLUG SLUG` narrows. This follows `leads dedupe`'s report-then-act shape rather than the
   `--dry-run`-opts-out shape of `triage run`/`ingest run`/`track run`, because a bulk status write
   across a job hunt is the `672ad2a` blast radius and the safe direction for a mistyped command is
   "printed a list". **`--dry-run` is deliberately not offered** — the default *is* the dry run.

4. **cv refuses, with `--include-stale`.** A stale lead is skipped before any spend. The escape
   hatch exists because there is a real false-positive mode: `last_seen` only bumps when a lead
   reappears in a scrape, so narrowing `sources.<id>.searches` ages a still-live posting. A hard
   refusal with no way forward makes a user set `lead_ttl_days: 0` and lose the feature entirely.

5. **`apply prep` refuses too, same escape hatch.** #9 names only cv, which leaves a gap: a lead
   whose CV was composed 100 days ago already has a `tailored_cv`, so `run_batch` skips it
   (`skipped-has-cv`, `cv/engine.py:196-198`) and it never re-reaches the cv guard — `apply prep`
   then stages it for a posting that may have closed.

   **`apply record` is deliberately NOT gated.** It performs the only `shortlist -> applied` write,
   so gating it looks consistent — but `record` registers an application the user has *already
   sent*. Refusing to record a past act prevents nothing; it loses the record and strands the lead
   on `shortlist`. The gate belongs on `prep`, which precedes the send.

## `StalenessPolicy` — the one carrier

Draft 1 threaded `lead_ttl_days` and `include_stale` as two keyword arguments across four levels and
let each call site read its own clock. Review killed both: the thread missed a producer
(`core/app.py:630`), nothing pinned the date, and "a policy that differs between sub-apps is a bug"
was asserted rather than structural.

One frozen value object, in `core/leads.py`:

```python
@dataclass(frozen=True)
class StalenessPolicy:
    """The staleness rule in force for one invocation, built once in `Sluice` and passed
    whole. Frozen, and the default abstains: a call site that forgets to pass one gets
    ttl_days=0 and never marks anything stale. Fail-safe is the only acceptable direction
    -- the failure it guards is binning a lead the user still wants."""
    ttl_days: int = 0
    today: str = ""
    include_stale: bool = False

    def days(self, last_seen: str) -> int | None:
        """Whole days from `last_seen` to `today`; None when EITHER is absent or
        unparseable. `today` is parsed inside the same guard as `last_seen`: a bad
        injected clock must abstain, not raise, for the same reason a bad `last_seen`
        must."""

    def is_stale(self, last_seen: str) -> bool:
        """days(...) > ttl_days. False when ttl_days <= 0 or days(...) is None."""

    def blocks(self, last_seen: str) -> bool:
        """is_stale(...) and not include_stale. The single question the cv and apply
        gates ask, so neither can implement the override differently."""
```

Built once, in `Sluice`, from the collaborator the composition root **already declares** —
`_COLLABORATORS = ("sleep", "today", "resolve_host")` (`core/app.py:63`), stored as `self._today`
(`:193`), currently threaded only to `VaultSink` (`:393`):

```python
def staleness(self, *, include_stale: bool = False) -> StalenessPolicy:
    # `today` is a zero-arg CALLABLE, not a string -- VaultSink does `today or _today`
    # then calls it (ingest/sink.py:26,31), and every test injects `lambda: "2026-07-07"`.
    # CALL it here: binding the function itself into the frozen policy would give
    # date.fromisoformat(<function>) -> TypeError, which `except ValueError` does NOT
    # catch, turning the designed fail-safe abstain into a traceback on three commands.
    clock = self._today or _today
    return StalenessPolicy(ttl_days=self.config.lead_ttl_days,
                           today=clock(),
                           include_stale=include_stale)
```

`Sluice(today=lambda: "2026-07-27")` is therefore the injection point for every staleness test —
matching the convention already live at `tests/test_app_injection.py:148`.

Three behaviours are load-bearing, and each is a mutation target:

- **Strictly greater.** `days > ttl_days`. A lead last seen exactly `ttl_days` ago is not yet stale.
- **`ttl_days <= 0` → never stale.** What makes an unconfigured install expire nothing. `<= 0`
  rather than `== 0` so a hand-built policy with a negative value abstains rather than expiring the
  whole vault.
- **Absent or unparseable date → never stale.** A missing date is not evidence of age. Notes
  predating the field, and hand-created notes, both exist in real vaults; binning them because a
  field failed to parse is the `672ad2a` shape at the data level.

A `last_seen` in the future (clock skew, a hand edit) yields negative days and is therefore not
stale — that falls out of `>` rather than needing its own branch.

> **Not load-bearing, and deliberately untested:** frontmatter quote-stripping. Draft 2 listed it as
> a fourth invariant with a test. It is a **no-op** — `_fm_dict` (`core/vault.py:889`) already does
> `.strip().strip('"').strip("'")` before any caller sees `note.fm["last_seen"]`, so the test could
> never fail and the policy would be carrying one store's quoting convention. `days()` may still
> `.strip()` defensively; it must not be described as a guarantee.

## Config

`Config.lead_ttl_days: int = 0` on the **root** `Config`. Staleness is a property of a lead read by
three sub-apps, and a policy that differs between them is a bug. Putting it on `ApplyConfig` would
also create a dead key — a field `load_apply_config` never reads.

**The precedent is `location_noise_words`/`dedupe_title_noise_words` (`core/config.py:66-71`), whose
comments say "Empty by default -> ... (abstain)" — not `dossier_allow_hosts`, which draft 1 cited.
That one's own comment (`core/config.py:56-59`) *explicitly disclaims* abstain semantics: it is a
safety allowlist where empty means "no exceptions granted", not "match nothing".**

The name matters: **`ttl_days: int = 7` already exists in both `cv/config.py:41` and
`triage/config.py:38`** as the dossier-cache TTL. Reusing it would be a live collision.

### Validation must reject `bool`, and must abstain on absent

```python
raw = data.get("lead_ttl_days")
raw = 0 if raw is None else raw          # ABSENT is the abstain case, not an error
if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
    raise ValueError(f"lead_ttl_days must be a non-negative integer, got {raw!r}")
```

Both halves are load-bearing and each was a review finding:

- `bool` subclasses `int`, and PyYAML resolves `yes`/`on`/`true`/`True` to `True` (verified:
  `yaml.safe_load("lead_ttl_days: yes")` → `True`, `isinstance(True, int)` → `True`). So
  `lead_ttl_days: yes` — the natural thing to type when the docs say `0` means off and you want it
  **on** — would load clean as a valid int and set a **one-day TTL**: `cv run` returns
  `skipped-stale` for every lead, `apply prep` refuses every lead, `expire` proposes the entire
  vault. Silently. That is an abstain inversion reached by typing the obvious thing.
- The absent key must yield `0`, not `None`. Draft 2's snippet left the binding implicit and the
  obvious one raises on an unconfigured install — breaking abstain in the section written to
  protect it.

No env var can reintroduce a value: `load_config` reads the environment only for `SLUICE_CONFIG`,
`SLUICE_LOCATIONS` and the two Telegram secrets, so this is the only route in.

The value is an integer with no personal content, so echoing it is fine — unlike
`dossier_allow_hosts`, which deliberately does not echo because a config file is one of the few
places real private hostnames legitimately live.

### `sluice.yaml.example` must ship it commented out

The example file is **copied verbatim** by the documented quickstart, and it contains both
conventions: `locations` is commented out with an explicit "this file is COPIED" rationale
(`sluice.yaml.example:12-14`), while the pay floors two blocks below ship **active illustrative
non-zero values** (`:76-77`). An implementer reaching for the nearest example writes the unsafe one,
and a copied non-zero `lead_ttl_days` silently switches on the cv and apply refusals — neither of
which is human-gated the way `--expire` is.

Commented out, or `0`, never an active illustrative value, with the reason stated inline.

## One `Store` contract addition

`update_fields` gains an optional `require_status`:

```python
def update_fields(self, ref, fields, *, append_note=None, note_tag=None,
                  require_status: frozenset | None = None) -> bool:
    """... When `require_status` is given, the transform re-reads the FRESH status from
    the note it is about to write and abstains -- writing nothing, returning False -- if
    it is not in that set. Returns whether a write happened.

    This is never-regress under concurrency and it CANNOT be done by the caller: expire's
    read loop is a window in which a lead can enter the application lifecycle via
    `apply record` or a #10 receipt, and a check against the enumerated LeadNote reads a
    snapshot that is stale by construction."""
```

A **parameter on the existing write helper**, not a second write function: CodeQL flags a new write
function as a new sink even when the behaviour it carries is pre-existing. The shape mirrors
`set_tailored_cv(only_if_absent=...)` and `hold_for_signoff`, which already make their decision
inside the transform on fresh content.

Existing callers pass nothing and are unaffected (the return value is new but ignorable).
`core/protocols.py` and `tests/conformance/test_store_contract.py` both gain the case — that suite
exists so a second store cannot ship without the property.

## `sluice leads expire`

`Sluice.expire_report()` and `Sluice.expire(slugs=None)` on `core/app.py`, mirroring
`dedupe_report()`/`dedupe_merge()`; `cmd_leads_expire` mirrors `cmd_leads_dedupe` (`cli.py:183-208`)
**except in the two places called out below, where copying it breaks the feature.**

The read is `store.read_leads({"new", "shortlist", "research", "needs_review"})`, so
application-owned notes are never enumerated. The write then passes
`require_status=frozenset({"new", "shortlist", "research", "needs_review"})`, which is what actually
holds never-regress — see the box above and mutation row 5.

### Slugs are printed in the form `--expire` accepts

`--expire SLUG` matches `note.slug` by **equality**, not `core/leads.py:slug_matches`, which is a
*substring* match whose two existing callers already disagree about ambiguity
(`apply/select.select_one` refuses, `compose_cv`/`sign_off_cv` take `notes[0]`). Neither behaviour is
acceptable for a bulk status write.

**A store-issued slug is the note FILENAME**, e.g. `Example Ltd - Example Role` — spaces, capitals,
a ` - ` separator (`Vault._slug_for`, verified against a real vault). It is *not* the hyphenated
`Lead.slug`. Draft 2's sample output printed the hyphenated form, so copy-pasting a printed slug
into `--expire` would have matched nothing, written nothing, and exited 0. The report prints exactly
what `--expire` accepts, the help text shows the shell quoting (`--expire "Example Ltd - Example
Role"`), and **a named slug that matches nothing is reported on stderr and exits non-zero** — a
silent no-op is the failure mode this whole command is shaped to avoid.

### A sign-off hold is refused — scoped to `pending_cv` only

> **Superseded during implementation (2026-07-27):** the stranding argument below was true when
> written, and drove the refusal. Implementation then widened `sign_off_cv` to resolve over all of
> `TRIAGE_OWNED` (CodeRabbit caught that `_EXPIRABLE` omits `dismiss`), so a dismissed held lead is
> now reachable. **The refusal survives on a different reason:** dismissing it silently discards a
> composed CV no human has signed off.

Expiring a `shortlist` lead that holds a #60 sign-off strands it: `Sluice.sign_off_cv` resolves
through `read_leads({"shortlist"})` (`core/app.py:576-580`), so once the status is `dismiss` both
`cv signoff` and `cv signoff --discard` report no match, `cv run --lead` cannot reopen it, and the
served PDF is inert — recoverable only by hand-editing frontmatter.

So a lead with **`pending_cv`** is flagged in the report and refused by `--expire`, bulk or named,
with the message naming the way out: `resolve the sign-off hold first: sluice cv signoff --lead
<slug> --discard`.

**Keyed on `pending_cv` alone, not `pending_cv or needs_signoff`.** `Vault.sign_off` returns a no-op
without `pending_cv` (`core/vault.py:389`) and only clears `needs_signoff` on that same branch, so a
note carrying `needs_signoff` alone — reachable by a hand-edit in Obsidian, the primary #16 threat —
would be refused *forever* by a message whose escape hatch does nothing. `needs_signoff` and
`tailored_cv` are report-only flags.

`leads dedupe` flags a broader set at `core/app.py:414-416`. Reusing it wholesale would be wrong: a
lead with a completed `tailored_cv` and no hold strands nothing when dismissed.

### The write

```
status: shortlist  ->  dismiss

relevance_notes:
  [expire 2026-07-27] stale: last_seen 2026-04-02 is 116d old
  (lead_ttl_days=90). Was: shortlist.
```

`note_tag` (`[expire YYYY-MM-DD]`) makes the append idempotent within a day. The prior status is in
the note text because that is the only record of what to restore. `VaultConflict` is caught **per
lead** and counted, never fatal — #16's callers-treat-as-non-fatal rule.

### Output, and the off state

```
$ sluice leads expire
expire: lead_ttl_days is unset (0) -- staleness is off, nothing to report
```

An unconfigured install must not print `0 stale`, which is indistinguishable from "nothing is
stale" and would let a user believe a knob they never set is protecting them.

```
$ sluice leads expire
[stale] Example Ltd - Example Role         116d  shortlist  first_seen 2026-01-08  ⚑cv
[stale] Example Industries - Example Role  203d  new        first_seen 2025-12-04
[held ] Example Holdings - Example Role    140d  shortlist  first_seen 2026-01-30  sign-off hold
expire: 3 stale (1 refused: sign-off hold), 0 written (--expire to apply)
```

`--json` emits one object per stale lead:
`{"slug","status","last_seen","first_seen","days","flagged":["cv"],"refused":"sign-off-hold"|null}`.

### The argparse shape is NOT dedupe's

`--merge` uses `nargs="+"` (`cli.py:536`), which **requires** an argument, so a bare `--expire` would
be an argparse error rather than "dismiss everything reported". `nargs="*"` alone fixes that and
breaks the other half: dedupe dispatches on `if args.merge:`, and a bare `--expire`'s falsy `[]`
falls through to the report branch, leaving the write flag **silently inert**. Verified by running
argparse:

| argv | `args.expire` | `is not None` | truthy |
| --- | --- | --- | --- |
| *(absent)* | `None` | ✗ | ✗ |
| `--expire` | `[]` | ✓ | ✗ ← the case `if args.expire:` loses |
| `--expire A` | `['A']` | ✓ | ✓ |

```python
ex.add_argument("--expire", nargs="*", default=None, metavar="SLUG")
...
if args.expire is not None:      # NOT `if args.expire:` -- [] is the bulk case
```

Needs a test at the **CLI parse layer**; every other expire test sits at the `Sluice.expire()` level
and stays green through a broken parser.

## The cv guard

One check in `cv/engine.py:run_one`, **after** the #60 sign-off latch (`engine.py:67-68`) and
**before** `dossier_cache.get_or_build` (`engine.py:73`) — the first line that spends anything.
Returns `CvResult(note.ref, "skipped-stale")`; `CvResult`'s docstring gains it.

`run_one` is the single choke point, **verified by enumeration**: its only production callers are
`core/app.py:543` and `cv/engine.py:202`. After the latch, not before, so the check is strictly
additive and #60's observable behaviour is unperturbed — a lead both held and stale still reports
`skipped-needs-signoff`.

`run_one` and `run_batch` take `policy: StalenessPolicy = StalenessPolicy()`; `Sluice.compose_cv`
gains `include_stale=False` and builds it via `self.staleness(...)`. The gate asks `policy.blocks`,
never `policy.is_stale` — that is what makes `--include-stale` one decision rather than two.

## The apply guard

One check in `apply/select.py:eligibility`, returning `(False, "stale")` alongside
`not_shortlist`/`no_url`/`no_artifact`/`missing_file`. `eligibility` is the single choke point,
**verified by enumeration**: its only callers are `apply/select.py:45` and `:53`. `PrepResult`
already carries `status="skipped"` plus a free-text `reason` (`apply/engine.py:13-18`).

**`Sluice.prep` has three branches into selection, not two** — found independently by two reviewers:

| `core/app.py` | branch | calls |
| --- | --- | --- |
| `:628` | `all_shortlist` | `engine.preview_all` → `select_all` |
| `:630` | single lead **`dry_run`** | `select.select_one` **directly**, bypassing `prep_one` |
| `:635` | single lead, real | `engine.prep_one` → `select_one` |

All three take the policy. `sluice apply prep` gains `--include-stale`, threaded through
`Sluice.prep(include_stale=...)` to all three — draft 2 specified the flag but wired it only on cv.

## Testing

Behaviour-asserting, offline, synthetic fixtures. Fixture leads use the `example.invalid` family.

Titles in the new fixtures are neutral literals (`Example Role`), **not** draws from
`tests/conftest.py`'s seeded-`faker` `titles`/`cfg_titles`. That is a deliberate narrowing of an
earlier draft of this section, which claimed the fixtures use `faker` — they do not, and the claim
would have been a doc asserting a mechanism the code does not implement. The `faker` fixtures exist
so that no test encodes a *taste* in job titles; a literal carrying no role or seniority signal
satisfies the same property, and every neighbouring test file in this repo already uses literals.
Slugs in this document (`Example Ltd - Example Role`) show the store-issued *format*.

New company names must come from the `Example …`/`example.invalid` family. Pre-existing placeholder
names elsewhere in `tests/` are out of scope here — a repo-wide rename touches seven files and
belongs in its own `test:` change, not a staleness PR.

**Policy unit tests** (`tests/test_lead_staleness.py`): the boundary both ways (`days == ttl_days`
not stale, `+1` stale); `ttl_days=0` abstains **on an ancient lead** (a same-day fixture makes this
test inert — see the table); a negative `ttl_days` abstains; empty `last_seen` abstains; unparseable
`last_seen` abstains; an unparseable `today` abstains rather than raising; a future `last_seen` is
not stale; `blocks()` is False when `include_stale` is set on an otherwise-stale lead.

**Config tests:** `Config().lead_ttl_days == 0`; the loader default via `load_config()` with
`monkeypatch.delenv("SLUICE_CONFIG", raising=False)` (the pattern
`tests/test_sluice_neutral_defaults.py:79-81` already uses and comments) — both halves are needed
because `load_config` names every field explicitly (`core/config.py:134-145`), so the loader default
is an independent literal the dataclass assertion does not constrain; an **absent** key yields `0`
without raising; `lead_ttl_days: yes` raises; a negative raises; a non-int raises; a configured
value round-trips (`lead_ttl_days: 90` → `Config.lead_ttl_days == 90`); and the root key appears in
`sluice.yaml.example` commented out or zero (`test_config_example.py` currently guards only sub-app
blocks, so a root key is otherwise unguarded).

> The `#26`/`#63` neutral-defaults sweep **does not cover this knob and must not be widened to.**
> It is value-keyed on `list`-defaulting fields because "empty list == abstain" is universal.
> `0 == abstain` is not universal for ints — the dossier-cache `ttl_days: int = 7` is a legitimate
> non-zero default where `0` would mean "never cache" — so widening to all int fields would
> false-positive on it. Verified twice during review, independently: adding
> `lead_ttl_days: int = 90` to the root `Config` left the full suite green. Recorded so it is not
> re-litigated.

**Wiring tests — the gap that would have shipped the feature inert.** Every test above pins the OFF
state, and four independent drops (`Sluice.staleness()` omitting `ttl_days=`; `compose_cv`, `prep`
or `expire` not passing or not reading the policy) leave the feature dead with a green suite. The cv
tests as naturally written — calling `run_one` directly with an explicit policy, the shape of
`tests/test_cv_engine.py` — catch none of them. One **`Sluice`-layer** test per consumer, using the
injection pattern already live at `tests/test_app_injection.py:71,100,148`:

```python
s = Sluice(Config(lead_ttl_days=30), store=<Vault>, backend=<fake>,
           renderer=<fake>, today=lambda: "2026-07-27")
```

→ `compose_cv` gives `skipped-stale`, `prep` gives `stale`, `expire_report` reports rather than
printing the unset-knob message.

**Expire behaviour tests:** an `applied` lead survives (see row 5 — this needs the racing form); a
`dismiss` lead is skipped; the bare report writes nothing; the unset-knob message appears;
`--expire` dismisses the reported set; `--expire SLUG` matches **exactly** and a slug that is a
substring of two others expires neither; an unmatched named slug exits non-zero; a `pending_cv` lead
is refused by both forms; a `needs_signoff`-only lead is **not** refused; a `VaultConflict` on one
lead is counted and the sweep continues; `note_tag` idempotency; the `--json` shape; and the
**argparse-layer** test that a bare `--expire` writes.

**cv guard tests:** `skipped-stale` from the single-lead path and from `run_batch`;
`--include-stale` composes; and a **recording dossier cache** asserting zero `get_or_build` calls
for a stale lead. `run_one` takes the cache as its fifth positional parameter and
`tests/test_cv_engine.py:64`'s `FakeCache` is already the right shape. (Draft 2 specified
`tests/harness/browser.py`; review proved that cannot be written — the fake exposes no public call
count and `build_harness` has no root-config knob, so `lead_ttl_days` cannot be set in a harness run
at all.)

**apply guard tests:** `eligibility` returns `(False, "stale")`; `select_one` and `select_all` both
reflect it; `--include-stale` stages; and the `core/app.py:630` regression — assert **dry-run and
real run both report the stale outcome**, not merely that they agree. "They agree" is satisfied by
the both-inert state: drop the policy at `:630` and `:635` and they agree on `staged`.

### Mutation witnesses

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Mutate by **moving or deleting**, never adding. Each mutant must redden a **named new test run by
node id**, and the witness must confirm no pre-existing test catches it first.

| Mutant | Must redden | Note |
| --- | --- | --- |
| Delete the `ttl_days <= 0` abstain | the `ttl_days=0` test, **fixture ancient** | With a same-day fixture the survivor is `0 > 0` → False and the mutant lives. The natural fixture is the inert one. |
| `>` → `>=` on the boundary | the `days == ttl_days` test | |
| Delete the `try` + `except ValueError: return None` | the garbage-date test | Deleting only the `return None` leaves a bare `except` that still swallows. Dedent the body; verified this leaves runnable code. |
| Move the cv check below `get_or_build` | the zero-`get_or_build` assertion | The **only** witness for the placement decision. |
| Delete `require_status=` from expire's write | the **racing** `applied`-survives test | Probed: an in-memory `note.status` guard is byte-identical to no guard — both write `dismiss`. The racer must fire on the **enumeration read** (`racing_read` returns pre-edit bytes, so installing it later leaves even a fresh-re-read guard seeing `shortlist`). |
| Delete `ttl_days=self.config.lead_ttl_days` from `Sluice.staleness()` | the three `Sluice`-layer wiring tests | Nothing else in the suite can see this. |

Commit the implementation **before** any witness that restores via `git checkout --`, or restore
from a saved copy: an empty post-run diff hides the loss, because the file then matches HEAD.

## Docs

- `sluice.yaml.example`: the new root key, commented out, with its rationale.
- `docs/ARCHITECTURE.md`: the composition root's operation list and the `leads` command group both
  change. **`:277-317` additionally asserts `today` is threaded only to `Ctx`/`VaultSink` and
  justifies its non-seam status by `last_seen` monotonicity alone** — both claims stop being true,
  and `staleness()` belongs in the non-adapter-state clause beside `dossier_cache()`.
- **`.rulesync/` is NOT touched.** `CLAUDE.md`'s Invariants section arguably wants a line about
  staleness, but that tree is canonical and human-gated — flagged for the user, not edited here.

## Definition of done

```bash
ruff check sluice tests          # ruff==0.15.21, the CI pin
python -m pytest                 # all green, offline, ~2s
```

Task sequencing and commit boundaries live in the implementation plan. The dependency order is fixed
by the policy: `core/leads.py`'s policy and `core/config.py`'s knob first, then `update_fields`'
`require_status` plus its protocol and conformance updates, then `Sluice.staleness()`, then the three
consumers in any order.

## Out of scope

**Triage gains no stale guard.** It reads `{"new", "research"}`, so a stale `new` lead costs a
backend call — but `expire` sweeps un-reviewed leads wholesale, triage runs on the cheap model, and
a third gate is surface area without much of a cost story. The policy is a value object, so a third
consumer later is a small change, not a new mechanism.

**`first_seen` is reported but never gated on.** It appears in the report and `--json` for context.
Age since first sighting is not staleness — a long-running posting that keeps reappearing is
genuinely still open, which is what `last_seen` captures and `first_seen` does not.

## The residual

#9's own closing caveat, preserved because it is the honest limit of the feature: the check that
actually matters — **"is this role still open on the employer's own site?"** — cannot be answered
from the store. `last_seen` records when sluice last saw the posting in a search it happened to run,
which is a proxy for the posting being live, not a measurement of it. Narrowing a search, a source
outage, or a board re-ranking all age a live lead; a board that leaves closed postings up keeps a
dead one fresh.

Staleness catches the obvious cases cheaply. It is not a substitute for verifying before applying,
and neither the docs nor the CLI output should imply otherwise.
