# What sluice guarantees

Five properties, each enforced by tests rather than promised in prose. They are grouped here
because they share a shape: every one of them guards a failure that is **silent, asymmetric and
hard to undo** — the kind you discover weeks later, when the evidence of what went wrong is gone.

[`README.md`](https://github.com/MrReasonable/sluice/blob/main/README.md) summarises these in a
paragraph. This page is the mechanics.

## Your edits survive

A re-scrape of a lead you already have touches only its `last_seen` marker — never its status,
never its scores, never your notes in the body. Creating a note for a genuinely new lead is the
only wholesale write sluice ever makes.

Every other write — a status change, a score, enrichment, the CV pointer — goes through a surgical
compare-and-set. The edit is re-derived from the *fresh* note on each attempt and committed via a
temporary file and an atomic rename, so a concurrent writer's other keys and body survive, and a
half-written note is never observable. A sustained race abstains rather than overwriting.

It is best-effort rather than a lock, and deliberately so: the writer sluice is actually racing is
**you**, editing the note in Obsidian, and you take no lock. A residual window remains between the
freshness re-read and the rename. It is documented rather than hidden, because the alternative —
locking a user's own vault against them — is worse.

Rewriting notes wholesale is the fragility sluice exists to remove.

## Status never regresses out of the application lifecycle

One `status` key, two lifecycles, separate owners.

| Owner | States |
|---|---|
| triage | `new`, `shortlist`, `research`, `needs_review`, `dismiss`, `unjudgeable` |
| track | `applied`, `phone_screen`, `interview`, `offer`, `accepted`, `rejected`, `withdrawn` |

Triage may rewrite freely among its own states — re-reading a job description and moving
`shortlist` to `dismiss` is normal. What it may never do is touch a lead that has entered the
application lifecycle. Status moves forward on that ladder only, and a terminal state is never
advanced out of. An unrecognised status is passed through untouched rather than silently
rewritten, because a status sluice does not understand is more likely to be yours than corrupt.

A lead you merged away is not re-created by a later scrape that still matches the identity
recorded at merge time — and identity is compared up to case, because job boards render one
employer several ways. Where the posting's identity has drifted past what was recorded, the lead
is re-created **visibly**, as a duplicate you can see and merge again. That is the direction to
fail in: a visible duplicate costs you a moment, and a silent suppression costs you the job.

## The CV cannot invent things

The gate is pure, deterministic, and hard — a violation blocks rendering outright, and a lead
whose every attempt failed is skipped rather than served an ungated CV.

- Every work-experience bullet must cite a real entry from a closed evidence bundle.
- Every number in that bullet must appear in the entry it cites.
- The profile prose carries no per-bullet citations, so it is held to a numeric floor over the
  whole source set: a figure appearing nowhere in your source material is a violation.
- Skills must be contained in the evidence you supplied, not invented around it.

The gate is **handed** its source set rather than recovering it by re-parsing the composed text.
That distinction closed three real holes at once, all of which let a line of free text mint or
rebind a citable source. No line of prose can now license a number.

Above the hard gate sits an advisory LLM audit, which catches the qualitative fabrication a
deterministic check cannot — a claim that is technically sourced and still misleading. It does not
block rendering. It withholds the send-ready CV pointer for your sign-off, which you clear with
`job-sluice cv signoff`.

**One limit, stated rather than buried.** The gate runs on the composed *text*. A custom Jinja2
template is free text sluice does not audit, so a template can add prose the gate never saw.

## An empty setting abstains

Unconfigured means "no opinion". It never means "match nothing".

Every preference gate — accepted titles, target locations, rejected companies, relevance keywords,
pay floors — defaults to empty, and an empty gate passes every lead through. What the judge looks
for is read at runtime from a note in your vault, never from this repository.

Getting this backwards bins an entire job hunt in silence, and it happened once: the location gate
shipped a single default value, and since the classifier rejects anything that does not match it, a
fresh install silently binned every job that had a location on it at all. A test now fails the
build if it recurs, and the numeric floors carry their own guards, because a sweep keyed on list
defaults cannot see an integer.

The same posture governs the exit code of `job-sluice doctor`: a thing you have not supplied yet is
not a fault, so a fresh install exits 0 and tells you what is still waiting on you.

## No personal data in this repository

No employer names, locations, contact details, hostnames, absolute paths or preferences in the
shipped package or its tests. Fixtures are synthetic and swept by a guard that ratchets: a new
value in a lead-identity position fails the build until a human rules on it, because nothing local
can tell whether a name is real.

Your search belongs in your config and your vault, which is also why sluice can be a public
repository at all.

---

The module-by-module description of how these are implemented is in
[`ARCHITECTURE.md`](ARCHITECTURE.md); the store contract they rest on is stated there and in
`sluice/core/protocols.py`.
