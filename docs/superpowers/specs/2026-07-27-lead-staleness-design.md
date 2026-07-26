# Lead staleness — a months-old posting should not look like today's (#9)

**Status:** design approved 2026-07-27, ahead of `/review-plan`.
**Issue:** #9 — `feat(leads): lead staleness — a months-old posting should not look like today's`
**Sub-apps:** `core` (the predicate + the config knob), `cli`/`core.app` (`leads expire`), `cv`,
`apply`

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
   SLUG SLUG` narrows to named leads. This follows `leads dedupe`'s `--merge nargs="+"` shape
   (`cli.py:535-538`) rather than the `--dry-run`-opts-out shape of `triage run`/`ingest
   run`/`track run`, because a bulk status write across a job hunt is the `672ad2a` blast radius
   and the safe direction for a mistyped command is "printed a list", not "dismissed 200 leads".
   **`--dry-run` is deliberately not offered** — the default *is* the dry run, and a redundant flag
   that does nothing is drift.

4. **cv refuses, with `--include-stale`.** A stale lead is skipped before any spend. The escape
   hatch exists because there is a real false-positive mode: `last_seen` only bumps when a lead
   reappears in a scrape, so narrowing `sources.<id>.searches` makes a still-live posting go stale
   spuriously. A hard refusal with no way forward is the kind of thing that makes a user set
   `lead_ttl_days: 0` and lose the feature entirely.

5. **apply refuses too, same escape hatch.** #9 names only the CV step, which leaves a gap: a lead
   whose CV was composed 100 days ago already has a `tailored_cv`, so `run_batch` skips it
   (`skipped-has-cv`, `cv/engine.py:196-198`) and it never re-reaches the cv guard at all —
   `apply prep` then stages it for a posting that may have closed. Once a queue has any age that is
   the steady state, not an edge case, and #9's own framing says applying to a closed role is worse
   than doing nothing.

## The predicate — `core/leads.py`

Pure, deterministic, reference date injected. Two functions, because the report needs the number
and the gates need the verdict:

```python
def days_stale(last_seen: str, today: str) -> int | None:
    """Whole days from `last_seen` to `today`; None when `last_seen` is absent or
    unparseable."""

def is_stale(last_seen: str, ttl_days: int, today: str) -> bool:
    """True iff the lead is older than the TTL. Abstains (False) when ttl_days <= 0
    or when days_stale returns None."""
```

Four behaviours are load-bearing, and each is a mutation target the test plan names:

- **Strictly greater.** `days > ttl_days`. A lead last seen exactly `ttl_days` ago is not yet
  stale.
- **`ttl_days <= 0` → never stale.** This is what makes an unconfigured install expire nothing.
  `<= 0` rather than `== 0` so a hand-built `Config` with a negative value abstains rather than
  expiring the entire vault.
- **Absent or unparseable `last_seen` → never stale.** A missing date is not evidence of age.
  Notes predating the field, and hand-created notes, both exist in real vaults, and binning them
  because a field failed to parse is the `672ad2a` shape at the data level. `date.fromisoformat`
  raising `ValueError` returns `None`, not "infinitely old".
- **Frontmatter quoting is stripped** the same way `Vault._bump_last_seen` does
  (`core/vault.py:579`: `.strip().strip('"').strip("'")`). Quoted date values exist in the wild
  because that writer tolerates them; a predicate that does not strip them would silently abstain
  on every quoted note — failing safe, but failing *silently*, which is how a feature ends up
  believed-to-work and inert.

A `last_seen` in the future (clock skew, a hand edit) yields negative days and is therefore not
stale. That falls out of `>` rather than needing its own branch.

## Config

`Config.lead_ttl_days: int = 0` on the **root** `Config`, not on `CvConfig`/`ApplyConfig`.
Staleness is a property of a lead read by three sub-apps, and a staleness policy that differs
between them is a bug — the same reasoning `dossier_allow_hosts` carries at `core/config.py:53-59`.
It reaches the sub-apps the way `dossier_allow_hosts` does: read off `self.config` inside the
`Sluice` method that needs it (`core/app.py:281`).

The name matters. **`ttl_days: int = 7` already exists in both `cv/config.py:41` and
`triage/config.py:38`** as the dossier-cache TTL, an unrelated concept. Reusing that name in a
third place would be a live collision.

`load_config` validates: a non-`int` or a negative raises at construction naming the key, the house
style (`_select_backend`'s guard, `_str_list`). The value is an integer with no personal content,
so echoing it in the error message is fine — unlike `dossier_allow_hosts`, which deliberately does
not echo because a config file is one of the few places real private hostnames legitimately live.

Add to `sluice.yaml.example` with the default and a one-line note that `0` means off.

## `sluice leads expire`

`Sluice.expire_report()` and `Sluice.expire(slugs=None)` on `core/app.py`, mirroring
`dedupe_report()`/`dedupe_merge()`; `cmd_leads_expire` in `cli.py` mirrors `cmd_leads_dedupe`
(`cli.py:183-208`), including the `--json` machine-readable form.

The read is `store.read_leads({"new", "shortlist", "research", "needs_review"})`. Application-owned
notes are **never read at all**, so never-regress here is structural rather than a check that can be
deleted — but a defence-in-depth `is_application_owned` guard still runs immediately before each
write, because the note can change between the read and the write, exactly as `triage/apply.py:14-18`
does.

The write is `triage/apply.py`'s shape verbatim:

```
status: shortlist  ->  dismiss

relevance_notes:
  [expire 2026-07-27] stale: last_seen 2026-04-02 is 116d old
  (lead_ttl_days=90). Was: shortlist.
```

`note_tag` (`[expire YYYY-MM-DD]`) makes the append idempotent within a day, the same way triage's
tag does. The prior status is in the note text because that is the only record of what to restore.

`VaultConflict` is caught **per lead** and counted, never fatal — one conflicting note must not
abort the sweep over the rest, which is `normalize_all_statuses`' established behaviour
(`core/protocols.py`, the `summary["skipped"]` contract) and #16's callers-treat-as-non-fatal rule.

Output:

```
$ sluice leads expire
[stale] example-backend-eng   116d  shortlist
[stale] example-sre-platform  203d  new
expire: 2 stale, 0 written (--expire to apply)

$ sluice leads expire --expire
expire: 2 dismissed

$ sluice leads expire --expire example-backend-eng
expire: 1 dismissed, 1 left
```

A `--expire SLUG` naming a lead that is not in the stale set is reported and not written — it is not
a licence to dismiss an arbitrary lead by name.

## The cv guard

One check, in `cv/engine.py:run_one`, placed **after** the #60 sign-off latch
(`engine.py:67-68`) and **before** `dossier_cache.get_or_build` (`engine.py:73`) — the first line
that spends anything. Returns `CvResult(note.ref, "skipped-stale")`, joining the existing
`skipped-*` family, and `CvResult`'s docstring gains it.

`run_one` is the single choke point. Both cv paths reach it: `Sluice.compose_cv`'s single-lead
branch calls it directly (`core/app.py:543`) and `run_batch` calls it per lead
(`cv/engine.py:202`). The #60 latch's own comment (`engine.py:64-66`) already states and relies on
this property, so one early return covers both — no second call site to enumerate.

**After the latch, not before,** so the new check is strictly additive: it can only fire on leads
that would otherwise have gone on to compose, and #60's observable latch behaviour is unperturbed. A
lead that is both held and stale still reports `skipped-needs-signoff`.

`--include-stale` on `sluice cv run` threads `Sluice.compose_cv(include_stale=...)` →
`run_one(..., include_stale=...)`, with `run_batch` forwarding the kwarg. `lead_ttl_days` reaches
`run_one` the same way, read off `self.config` in `compose_cv`.

## The apply guard

One check, in `apply/select.py:eligibility`, returning `(False, "stale")` alongside the existing
`not_shortlist`/`no_url`/`no_artifact`/`missing_file` vocabulary. Both `select_one` and `select_all`
route through `eligibility`, so that one site covers `prep --lead` and `prep --all-shortlist`
alike. `PrepResult` already carries `status="skipped"` plus a free-text `reason`
(`apply/engine.py:13-18`), so no new result shape is needed.

`lead_ttl_days` and `include_stale` reach `eligibility` by explicit keyword from `Sluice.prep()`,
threaded through `prep_one`/`preview_all` and `select_one`/`select_all`. **Not** copied onto
`ApplyConfig`: a shadow `apply.lead_ttl_days` key that a user could set in YAML and have silently
overwritten by the root value is worse than a three-level keyword thread.

## Testing

Behaviour-asserting, offline, synthetic fixtures. Fixture leads use the `example.invalid` family and
seeded `faker` titles per `tests/conftest.py`; no real firm names — `Acme` in particular is out
(web-flagged as a real firm on #64).

**Predicate unit tests** (`tests/test_lead_staleness.py`): the boundary in both directions
(`days == ttl_days` not stale, `ttl_days + 1` stale); `ttl_days=0` abstains; a negative `ttl_days`
abstains; empty `last_seen` abstains; unparseable `last_seen` abstains; a quoted `last_seen` parses;
a future `last_seen` is not stale.

**Neutral-default test:** `Config().lead_ttl_days == 0`, explicitly named.

> The `#26`/`#63` neutral-defaults sweep **does not cover this knob and must not be assumed to.**
> That guard is value-keyed on `list`-defaulting fields because "empty list == abstain" is
> universal. `0 == abstain` is **not** universal for ints — the dossier-cache `ttl_days: int = 7` is
> a legitimate non-zero default where `0` would mean "never cache" — so widening the sweep to all
> int fields would false-positive on it. This knob gets its own named guard, and the reason is
> recorded here so a later reader does not "simplify" it away into the generic sweep.

**Expire behaviour tests:** an `applied` lead with an ancient `last_seen` survives untouched; a
`dismiss` lead is skipped; the bare report writes nothing; `--expire` dismisses the reported set;
`--expire SLUG` narrows; `--expire` naming a non-stale lead does not write; a `VaultConflict` on one
lead is counted and the sweep continues; the audit note records the prior status.

**cv guard tests:** `skipped-stale` from the single-lead path and from `run_batch`; **the dossier
fetcher is never called** for a stale lead (the `tests/harness/browser.py` fake asserts zero
`create_tab` calls); `--include-stale` composes normally.

**apply guard tests:** `eligibility` returns `(False, "stale")`; both `select_one` and `select_all`
reflect it; `--include-stale` stages normally.

**Mutation witnesses.** Run `python -m compileall -q -f --invalidation-mode checked-hash sluice
tests scripts` first. Mutate by **moving or deleting**, never by adding. Each mutant must redden a
**named new test run by node id**, and the witness must confirm no pre-existing test in the same
file is what actually catches it:

| Mutant | Must redden |
|---|---|
| Delete the `ttl_days <= 0` abstain | the `ttl_days=0` predicate test |
| `>` → `>=` on the boundary | the `days == ttl_days` test |
| Delete the unparseable-date `None` return | the garbage-`last_seen` test |
| Move the cv check below `get_or_build` | the zero-`create_tab`-calls assertion |
| Delete the `is_application_owned` guard in expire | the `applied`-survives test |

Commit the implementation **before** any witness that restores via `git checkout --`, or restore
from a saved copy: an empty post-run diff hides the loss, because the file then matches HEAD.

## Out of scope

**Triage gains no stale guard.** It has a comparable cost story — it reads `{"new", "research"}`, so
a stale `new` lead costs a backend call to judge — but `expire` sweeps un-reviewed leads wholesale,
triage runs on the cheap model, and a third gate is surface area without much of a cost story. The
predicate is a pure function, so adding a third call site later is a small change, not a new
mechanism.

**`first_seen` is read but never gated on.** It appears in the report for context. Age since first
sighting is not staleness — a long-running posting that keeps reappearing is genuinely still open,
which is exactly what `last_seen` captures and `first_seen` does not.

## The residual

#9's own closing caveat, preserved because it is the honest limit of the feature: the check that
actually matters — **"is this role still open on the employer's own site?"** — cannot be answered
from the store. `last_seen` records when sluice last saw the posting in a search it happened to run,
which is a proxy for the posting being live, not a measurement of it. Narrowing a search, a source
outage, or a board re-ranking all age a live lead; a board that leaves closed postings up keeps a
dead one fresh.

Staleness catches the obvious cases cheaply. It is not a substitute for verifying before applying,
and neither the docs nor the CLI output should imply otherwise.
