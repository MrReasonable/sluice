# Lead staleness — a months-old posting should not look like today's (#9)

**Status:** design approved 2026-07-27; revised once after `/review-plan` (5 reviewers, 0 Critical,
11 High, 12 Medium, 6 Low). The five user-settled decisions are unchanged. The revision replaced the
plumbing wholesale (a four-level keyword thread became one frozen `StalenessPolicy`), fixed a config
validator that admitted `lead_ttl_days: yes` as a one-day TTL, fixed an argparse shape that could not
express its own CLI, added slug-resolution and sign-off-hold rules that were missing entirely, and
rebuilt the mutation table after the test engineer proved **three of its five rows certify nothing**.

**Issue:** #9 — `feat(leads): lead staleness — a months-old posting should not look like today's`
**Sub-apps:** `core` (the policy + the config knob), `cli`/`core.app` (`leads expire`), `cv`, `apply`

Two review findings are worth carrying into implementation as warnings, because both are this
repo's recurring failure mode caught in my own spec:

- I wrote a paragraph asserting `run_one` is the single choke point for cv (**true**, verified by
  enumeration), then one section later hand-listed the apply producers and **missed one**
  (`core/app.py:630`). Two reviewers found it independently. Enumerate both ends of every dataflow.
- Three of five mutation-witness rows were **equivalent or inert** — they would have stayed green
  while certifying that a guard was covered. A witness table whose rows you chose is not evidence.

## Problem

A lead has no notion of age. `Lead.first_seen` and `Lead.last_seen` already exist
(`core/leads.py:85-86`) and `ingest/sink.py:35-37` already stamps both — `first_seen` only when
empty, `last_seen` on every write — so the *data* is there and has been all along. Nothing reads
it. A posting scraped six months ago sits in the store looking exactly like one scraped this
morning, and the pipeline will tailor a CV for it and stage an application.

That costs two different things. Tailoring a CV for a closed role burns a compose call and a render
for nothing. Applying to one is worse than doing nothing: it consumes a slot in the user's own
tracking, and it is not recoverable by editing a note.

No new plumbing is needed to know a lead's age. This is entirely a read of existing data, plus one
new maintenance command and two gates.

## The tension in the issue text, and how it resolves

#9 says `expire` should "move them to a terminal status" **and** "never touch an application-owned
status (`applied`, `phone_screen`, `interview`, `offer`, `rejected`)". Those two requirements
contradict each other. `_TERMINAL = ("rejected", "accepted", "withdrawn")` (`core/status.py:57`)
and **all three are in `APPLICATION_OWNED`** (`core/status.py:15-18`). There is no
triage-owned terminal to move to.

The triage-owned end state is `dismiss`. So `expire` writes `dismiss`, never a `_TERMINAL`. A
design that reached for a terminal would collide with never-regress on its first test.

## The five settled decisions

1. **Expire writes `dismiss`,** with the prior status recorded in the audit note so a human can
   reverse it. Not a `_TERMINAL` (impossible, above); not a new `stale_at` frontmatter key (that
   would leave `status` unchanged and oblige triage, cv and apply each to learn a new key — three
   new read sites, three new ways to silently not filter).

2. **Eligible statuses: `new`, `shortlist`, `research`, `needs_review`** — every `TRIAGE_OWNED`
   state except `dismiss`, which is already the destination. `shortlist` is included deliberately:
   it is the state `compose_cv` and `apply prep` read, so it is where staleness actually costs
   money. The human gate (decision 3) is what keeps that from being a surprising write.

3. **Report by default; `--expire [SLUG...]` writes.** Bare `sluice leads expire` prints the stale
   set and writes nothing. `--expire` with no arguments dismisses everything reported; `--expire
   SLUG SLUG` narrows to named leads. This follows `leads dedupe`'s report-then-act shape rather
   than the `--dry-run`-opts-out shape of `triage run`/`ingest run`/`track run`, because a bulk
   status write across a job hunt is the `672ad2a` blast radius and the safe direction for a
   mistyped command is "printed a list", not "dismissed 200 leads". **`--dry-run` is deliberately
   not offered** — the default *is* the dry run, and a redundant flag that does nothing is drift.

4. **cv refuses, with `--include-stale`.** A stale lead is skipped before any spend. The escape
   hatch exists because there is a real false-positive mode: `last_seen` only bumps when a lead
   reappears in a scrape, so narrowing `sources.<id>.searches` makes a still-live posting go stale
   spuriously. A hard refusal with no way forward is the kind of thing that makes a user set
   `lead_ttl_days: 0` and lose the feature entirely.

5. **`apply prep` refuses too, same escape hatch.** #9 names only the CV step, which leaves a gap: a
   lead whose CV was composed 100 days ago already has a `tailored_cv`, so `run_batch` skips it
   (`skipped-has-cv`, `cv/engine.py:196-198`) and it never re-reaches the cv guard at all —
   `apply prep` then stages it for a posting that may have closed. Once a queue has any age that is
   the steady state, not an edge case.

   **`apply record` is deliberately NOT gated.** It is the only path that performs the
   `shortlist -> applied` write, so gating it looks consistent — but `record` registers an
   application the user has *already sent*. Refusing to record a past act does not prevent
   anything; it just loses the record and leaves the lead wrongly on `shortlist`. The gate belongs
   on `prep`, which is what *precedes* the send.

## `StalenessPolicy` — the one carrier

The first draft threaded `lead_ttl_days` and `include_stale` as two keyword arguments across four
levels, and sourced the reference date by having each call site read its own clock. Review killed
both: the thread missed a producer (`core/app.py:630`), nothing pinned the date, and the plan's own
claim that "a policy that differs between sub-apps is a bug" was asserted rather than structural.

One frozen value object, in `core/leads.py`:

```python
@dataclass(frozen=True)
class StalenessPolicy:
    """The staleness rule in force for one invocation, built once in `Sluice` and passed
    whole. Frozen, and the default abstains: a call site that forgets to pass one gets
    ttl_days=0 and therefore never marks anything stale. Fail-safe is the only acceptable
    direction here -- the failure it guards is binning a lead the user still wants."""
    ttl_days: int = 0
    today: str = ""
    include_stale: bool = False

    def days(self, last_seen: str) -> int | None:
        """Whole days from `last_seen` to `today`; None when `last_seen` is absent or
        unparseable."""

    def is_stale(self, last_seen: str) -> bool:
        """days(...) > ttl_days. False when ttl_days <= 0 or days(...) is None."""

    def blocks(self, last_seen: str) -> bool:
        """is_stale(...) and not include_stale. The single question the cv and apply
        gates ask, so neither can implement the override differently."""
```

Built once, in `Sluice`, from the collaborator the composition root **already declares** —
`_COLLABORATORS = ("sleep", "today", "resolve_host")` (`core/app.py:63`), stored as `self._today`
(`:193`) and currently threaded only to `VaultSink` (`:393`):

```python
def staleness(self, *, include_stale: bool = False) -> StalenessPolicy:
    return StalenessPolicy(ttl_days=self.config.lead_ttl_days,
                           today=self._today or date.today().isoformat(),
                           include_stale=include_stale)
```

That makes `Sluice(today="2026-07-27")` the injection point for every staleness test, rather than
each engine reading `date.today()` the way `cv/engine.py:168,177` already does twice.

Four behaviours are load-bearing, and each is a mutation target the test plan names:

- **Strictly greater.** `days > ttl_days`. A lead last seen exactly `ttl_days` ago is not yet stale.
- **`ttl_days <= 0` → never stale.** This is what makes an unconfigured install expire nothing.
  `<= 0` rather than `== 0` so a hand-built policy with a negative value abstains rather than
  expiring the entire vault.
- **Absent or unparseable `last_seen` → never stale.** A missing date is not evidence of age.
  Notes predating the field, and hand-created notes, both exist in real vaults, and binning them
  because a field failed to parse is the `672ad2a` shape at the data level. `date.fromisoformat`
  raising `ValueError` returns `None`, not "infinitely old".
- **Frontmatter quoting is stripped** the same way `Vault._bump_last_seen` does
  (`core/vault.py:579`: `.strip().strip('"').strip("'")`). Quoted date values exist in the wild
  because that writer tolerates them; a policy that did not strip them would silently abstain on
  every quoted note — failing safe, but failing *silently*, which is how a feature ends up
  believed-to-work and inert.

A `last_seen` in the future (clock skew, a hand edit) yields negative days and is therefore not
stale. That falls out of `>` rather than needing its own branch.

## Config

`Config.lead_ttl_days: int = 0` on the **root** `Config`, not on `CvConfig`/`ApplyConfig`.
Staleness is a property of a lead read by three sub-apps, and a staleness policy that differs
between them is a bug. Putting it on `ApplyConfig` would also create a dead key in the other
direction — a field `load_apply_config` never reads.

**The precedent is `location_noise_words`/`dedupe_title_noise_words` (`core/config.py:66-71`), not
`dossier_allow_hosts`.** The first draft cited the latter; that is wrong in a way worth recording,
because `dossier_allow_hosts`' own comment (`core/config.py:56-59`) *explicitly disclaims* abstain
semantics — it is a safety allowlist where empty means "no exceptions granted", not "match
nothing". `lead_ttl_days: 0` is a genuine abstain default, so it should cite the knobs that are.

The name matters. **`ttl_days: int = 7` already exists in both `cv/config.py:41` and
`triage/config.py:38`** as the dossier-cache TTL, an unrelated concept. Reusing that name in a
third place would be a live collision.

### Validation must reject `bool`

`load_config` validates and raises at construction naming the key — the house style
(`_select_backend`, `_str_list`). The check is **not** "a non-`int` or a negative raises", which is
what the first draft said and which is unsafe:

```python
if isinstance(v, bool) or not isinstance(v, int) or v < 0:
    raise ValueError(f"lead_ttl_days must be a non-negative integer, got {v!r}")
```

`bool` subclasses `int`, and PyYAML resolves `yes`/`on`/`true`/`True` to `True` (verified:
`yaml.safe_load("lead_ttl_days: yes")` → `True`, `isinstance(True, int)` → `True`). So
`lead_ttl_days: yes` — the natural thing to type when the docs say `0` means off and you want it
**on** — would load clean as a valid int and set a **one-day TTL**: `cv run` returns
`skipped-stale` for every lead, `apply prep` refuses every lead, and `expire` proposes the entire
vault. Silently, with no error. That is an abstain inversion reached by typing the obvious thing,
which is the `672ad2a` class.

The value is an integer with no personal content, so echoing it in the error message is fine —
unlike `dossier_allow_hosts`, which deliberately does not echo because a config file is one of the
few places real private hostnames legitimately live.

### `sluice.yaml.example` must ship it commented out

The example file is **copied verbatim** by the documented quickstart (`cp sluice.yaml.example
sluice.local.yaml`), and it contains both conventions: `locations` is commented out with an explicit
"this file is COPIED" rationale (`sluice.yaml.example:12-14`), while the pay floors two blocks below
ship **active illustrative non-zero values** (`:76-77`). An implementer reaching for the nearest
example would write the unsafe one, and a copied non-zero `lead_ttl_days` silently switches on the
cv and apply refusals — neither of which is human-gated the way `--expire` is.

So: commented out, or `0`, never an active illustrative value, with the reason stated inline the way
the `locations` block states it.

## `sluice leads expire`

`Sluice.expire_report()` and `Sluice.expire(slugs=None)` on `core/app.py`, mirroring
`dedupe_report()`/`dedupe_merge()`; `cmd_leads_expire` in `cli.py` mirrors `cmd_leads_dedupe`
(`cli.py:183-208`) — **except in one place, called out below, where copying it produces a silently
inert flag.**

The read is `store.read_leads({"new", "shortlist", "research", "needs_review"})`. Application-owned
notes are **never read at all**, so never-regress here is structural. A defence-in-depth
`is_application_owned` check still runs immediately before each write, because the note can change
between the read and the write — and because that is the *only* thing standing between a
concurrently-applied lead and a `dismiss`, it must be witnessed by a test that actually races
(see the mutation table).

### Slug resolution is exact

`--expire SLUG` matches `note.slug` by **equality**, not `core/leads.py:slug_matches`, which is a
*substring* match. The two existing users of that helper already disagree about what to do with
multiple hits — `apply/select.select_one` refuses on ambiguity ("never silently picks the first,
unlike cv") while `compose_cv`/`sign_off_cv` take `notes[0]` — and neither behaviour is acceptable
for a bulk status write. A user typing the narrow form is choosing the *safer* option under decision
3; it must not be the one that dismisses leads they did not name. A slug that matches nothing is
reported and not written.

### A sign-off hold is refused, not expired

Expiring a `shortlist` lead that holds a #60 sign-off (`pending_cv` set, `tailored_cv` withheld)
**strands it permanently**: `Sluice.sign_off_cv` resolves through `read_leads({"shortlist"})`
(`core/app.py:576-580`), so once the status is `dismiss`, both `cv signoff` and `cv signoff
--discard` report no match, `cv run --lead` cannot reopen it, and the served PDF is inert —
recoverable only by hand-editing frontmatter.

So a lead with `pending_cv` or `needs_signoff` is **flagged in the report and refused by `--expire`,
whether named explicitly or swept in bulk**, with the message naming the way out:
`resolve the sign-off hold first: sluice cv signoff --lead <slug> --discard`.

`leads dedupe` flags a broader set at `core/app.py:414-416` (`tailored_cv` **or** `needs_signoff`
**or** `pending_cv` **or** application-owned). Reusing that predicate wholesale would be wrong here:
a lead with a completed `tailored_cv` and no hold strands nothing when dismissed, so it is flagged
in the report as informational — you spent a compose on it — but not refused. The refusal is
scoped to the hold, which is the only irrecoverable case.

### The write

The write is `triage/apply.py`'s shape:

```
status: shortlist  ->  dismiss

relevance_notes:
  [expire 2026-07-27] stale: last_seen 2026-04-02 is 116d old
  (lead_ttl_days=90). Was: shortlist.
```

`note_tag` (`[expire YYYY-MM-DD]`) makes the append idempotent within a day, the same way triage's
tag does. The prior status is in the note text because that is the only record of what to restore.

`VaultConflict` is caught **per lead** and counted, never fatal — one conflicting note must not
abort the sweep over the rest, which is `normalize_all_statuses`' established behaviour and #16's
callers-treat-as-non-fatal rule.

### Output, and the off state

```
$ sluice leads expire
expire: lead_ttl_days is unset (0) -- staleness is off, nothing to report
```

An unconfigured install must not print `0 stale`, which is indistinguishable from "nothing is
stale" and would let a user believe a knob they never set is protecting them.

Configured:

```
$ sluice leads expire
[stale] example-backend-eng   116d  shortlist  first_seen 2026-01-08  ⚑cv
[stale] example-sre-platform  203d  new        first_seen 2025-12-04
[held ] example-data-eng      140d  shortlist  first_seen 2026-01-30  sign-off hold
expire: 3 stale (1 refused: sign-off hold), 0 written (--expire to apply)

$ sluice leads expire --expire
expire: 2 dismissed, 1 refused (sign-off hold)
```

`--json` emits one object per stale lead:
`{"slug","status","last_seen","first_seen","days","flagged":["cv"],"refused":"sign-off-hold"|null}`.

### The argparse shape is NOT dedupe's

This is the one place copying `cmd_leads_dedupe` breaks the feature. `--merge` uses `nargs="+"`
(`cli.py:536`), which **requires** an argument, so a bare `--expire` would be an argparse error
rather than "dismiss everything reported". Switching to `nargs="*"` alone fixes that and breaks the
other half: dedupe dispatches on `if args.merge:`, and a bare `--expire`'s falsy `[]` would fall
straight through to the report branch, leaving the write flag **silently inert**.

```python
ex.add_argument("--expire", nargs="*", default=None, metavar="SLUG")
...
if args.expire is not None:      # NOT `if args.expire:` -- [] is the bulk case
```

This needs a test at the **CLI parse layer**. Every other expire test in this plan sits at the
`Sluice.expire()` level and would stay green through a broken parser.

## The cv guard

One check, in `cv/engine.py:run_one`, placed **after** the #60 sign-off latch
(`engine.py:67-68`) and **before** `dossier_cache.get_or_build` (`engine.py:73`) — the first line
that spends anything. Returns `CvResult(note.ref, "skipped-stale")`, joining the existing
`skipped-*` family, and `CvResult`'s docstring gains it.

`run_one` is the single choke point, **verified by enumeration**: its only production callers are
`core/app.py:543` (the single-lead branch) and `cv/engine.py:202` (`run_batch`, per lead). One early
return covers both.

**After the latch, not before,** so the new check is strictly additive: it can only fire on leads
that would otherwise have gone on to compose, and #60's observable latch behaviour is unperturbed. A
lead that is both held and stale still reports `skipped-needs-signoff`.

`run_one` and `run_batch` take `policy: StalenessPolicy = StalenessPolicy()`; `Sluice.compose_cv`
gains `include_stale=False` and builds the policy via `self.staleness(...)`.

## The apply guard

One check, in `apply/select.py:eligibility`, returning `(False, "stale")` alongside the existing
`not_shortlist`/`no_url`/`no_artifact`/`missing_file` vocabulary. `eligibility` is the single choke
point, **verified by enumeration**: its only callers are `apply/select.py:45` (`select_one`) and
`:53` (`select_all`). `PrepResult` already carries `status="skipped"` plus a free-text `reason`
(`apply/engine.py:13-18`), so no new result shape is needed.

**`Sluice.prep` has three branches into selection, not two** — this is the finding two reviewers
raised independently, and the first draft named only the first and third:

| `core/app.py` | branch | calls |
| --- | --- | --- |
| `:628` | `all_shortlist` | `engine.preview_all` → `select_all` |
| `:630` | single lead **`dry_run`** | `select.select_one` **directly**, bypassing `prep_one` |
| `:635` | single lead, real | `engine.prep_one` → `select_one` |

Miss `:630` and `apply prep --lead X --dry-run` previews a lead the real run refuses, and
`--include-stale` is dead on that path. All three take the policy, and a test asserts **dry-run and
real run agree** on a stale lead.

## Testing

Behaviour-asserting, offline, synthetic fixtures. Fixture leads use the `example.invalid` family and
seeded `faker` titles via `tests/conftest.py`'s `titles`/`cfg_titles`; no real firm names — `Acme` in
particular is out (web-flagged as a real firm on #64).

**Policy unit tests** (`tests/test_lead_staleness.py`): the boundary in both directions
(`days == ttl_days` not stale, `ttl_days + 1` stale); `ttl_days=0` abstains **on an ancient lead**
(see the mutation table — a same-day fixture makes this test inert); a negative `ttl_days` abstains;
empty `last_seen` abstains; unparseable `last_seen` abstains; a quoted `last_seen` parses; a future
`last_seen` is not stale; `blocks()` is False when `include_stale` is set on an otherwise-stale lead.

**Config tests:** `Config().lead_ttl_days == 0` **and** the loader default, i.e.
`load_config()` with `monkeypatch.delenv("SLUICE_CONFIG", raising=False)` — the pattern
`tests/test_sluice_neutral_defaults.py:79-81` already uses and comments. Both halves are needed
because `load_config` names every field explicitly (`core/config.py:135-145`, no splat, no loop), so
the loader default is an independent literal the dataclass assertion does not constrain. Plus:
`lead_ttl_days: yes` raises; a negative raises; a non-int raises; and the root key appears in
`sluice.yaml.example` **commented out or zero** (`test_config_example.py` currently guards only
sub-app blocks, so a root key is otherwise unguarded).

> The `#26`/`#63` neutral-defaults sweep **does not cover this knob and must not be widened to.**
> That guard is value-keyed on `list`-defaulting fields because "empty list == abstain" is
> universal. `0 == abstain` is **not** universal for ints — the dossier-cache `ttl_days: int = 7` is
> a legitimate non-zero default where `0` would mean "never cache" — so widening the sweep to all
> int fields would false-positive on it. Verified empirically during review: adding
> `lead_ttl_days: int = 90` (a deliberately non-neutral value) to the root `Config` left the **full
> suite green**. Recorded so it is not re-litigated.

**Expire behaviour tests:** an `applied` lead with an ancient `last_seen` survives untouched; a
`dismiss` lead is skipped; the bare report writes nothing; the unset-knob message appears; `--expire`
dismisses the reported set; `--expire SLUG` narrows and matches **exactly** (a slug that is a
substring of two others expires neither); a sign-off-held lead is refused by both forms; a
`VaultConflict` on one lead is counted and the sweep continues; `note_tag` idempotency (a second
same-day run appends nothing); the `--json` shape; and the **argparse-layer** test that a bare
`--expire` writes.

**cv guard tests:** `skipped-stale` from the single-lead path and from `run_batch`; `--include-stale`
composes normally; and — for the placement decision — **a recording dossier cache injected at the
`run_one` layer asserts zero `get_or_build` calls** for a stale lead. The first draft specified
`tests/harness/browser.py` for this; review proved that cannot be written (the fake exposes no public
call count, `build_harness` has no root-config knob so `lead_ttl_days` cannot be set in a harness run
at all, and existing cv tests use a hand-rolled `FakeCache` rather than the browser fake).

**apply guard tests:** `eligibility` returns `(False, "stale")`; `select_one` and `select_all` both
reflect it; **dry-run and real run agree** on a stale lead (the `core/app.py:630` regression); and a
wiring test that the gate is live end-to-end through `Sluice`, since every plumbing default abstains
and a forgotten policy argument would leave the gate inert and green.

### Mutation witnesses

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Mutate by **moving or deleting**, never by adding. Each mutant must redden a **named new test run by
node id**, and the witness must confirm no pre-existing test is what actually catches it.

Three rows of the first draft's table were proved inert by the review and are replaced here:

| Mutant | Must redden | Note |
| --- | --- | --- |
| Delete the `ttl_days <= 0` abstain | the `ttl_days=0` test, **fixture ancient** | With a same-day fixture the survivor is `days > ttl_days` → `0 > 0` → False, and the mutant lives. The natural fixture is the inert one. |
| `>` → `>=` on the boundary | the `days == ttl_days` test | |
| Delete the `except ValueError: return None` **and its `try`** | the garbage-`last_seen` test | Deleting only the `return None` leaves a bare `except` that still swallows; the mutation has to remove the handler. |
| Move the cv check below `get_or_build` | the zero-`get_or_build` assertion | The **only** witness for the placement decision; every `skipped-stale` assertion stays green under this mutant. |
| Delete the `is_application_owned` guard in expire | a **racing** test via `tests/conftest.py:racing_read` | The plain `applied`-survives test is an **equivalent mutant**: `read_leads` filters on status before the write loop, so the applied lead never reaches the guard. Probed against the real `Vault` during review. |

Commit the implementation **before** any witness that restores via `git checkout --`, or restore
from a saved copy: an empty post-run diff hides the loss, because the file then matches HEAD.

## Docs

- `sluice.yaml.example`: the new root key, commented out, with its rationale.
- `docs/ARCHITECTURE.md`: it enumerates the composition root's operations and the `leads` command
  group; both change. Not scheduling this was a review finding.
- **`.rulesync/` is NOT touched by this change.** `CLAUDE.md`'s Invariants section arguably wants a
  line about staleness, but that tree is canonical and human-gated — flagged for the user, not
  edited here.

## Definition of done

```bash
ruff check sluice tests          # ruff==0.15.21, the CI pin
python -m pytest                 # all green, offline, ~2s
```

Task-by-task sequencing, commit boundaries and per-task verification live in the implementation plan
(`superpowers:writing-plans`), not here. The dependency order is fixed by the policy object: the
`core/leads.py` policy and the `core/config.py` knob land first, `Sluice.staleness()` second, and the
three consumers (`expire`, cv, apply) in any order after that.

## Out of scope

**Triage gains no stale guard.** It has a comparable cost story — it reads `{"new", "research"}`, so
a stale `new` lead costs a backend call to judge — but `expire` sweeps un-reviewed leads wholesale,
triage runs on the cheap model, and a third gate is surface area without much of a cost story. The
policy is a value object, so adding a third consumer later is a small change, not a new mechanism.

**`first_seen` is reported but never gated on.** It appears in the report and the `--json` output for
context. Age since first sighting is not staleness — a long-running posting that keeps reappearing is
genuinely still open, which is exactly what `last_seen` captures and `first_seen` does not.

**One unstated store obligation is now stated.** The read side assumes `fm["last_seen"]` is a string
`date.fromisoformat` can parse. A second store implementation must honour that, or every lead abstains
and the feature is silently inert. It is a weaker obligation than the write side's (`update_fields` is
already pinned by the contract) and it fails safe, so it is documented rather than added to the
conformance suite.

## The residual

#9's own closing caveat, preserved because it is the honest limit of the feature: the check that
actually matters — **"is this role still open on the employer's own site?"** — cannot be answered
from the store. `last_seen` records when sluice last saw the posting in a search it happened to run,
which is a proxy for the posting being live, not a measurement of it. Narrowing a search, a source
outage, or a board re-ranking all age a live lead; a board that leaves closed postings up keeps a
dead one fresh.

Staleness catches the obvious cases cheaply. It is not a substitute for verifying before applying,
and neither the docs nor the CLI output should imply otherwise.
