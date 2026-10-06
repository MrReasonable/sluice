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
recorded at merge time — and identity is compared up to case, and up to Unicode canonical
equivalence, because job boards render one employer several ways and may publish an accented
name in different composition forms. Where the posting's identity has drifted past what was recorded, the lead
is re-created **visibly**, as a duplicate you can see and merge again. That is the direction to
fail in: a visible duplicate costs you a moment, and a silent suppression costs you the job.

## The CV cannot invent things

The gate is pure, deterministic, and hard — a violation blocks rendering outright, and a lead
whose every attempt failed is skipped rather than served an ungated CV.

- Every work bullet must cite a verified entry, and only an entry your CV Layout places under
  the role it sits under. An entry with no company, or one matching no role, is cited nowhere.
- Every number in a bullet must appear in an entry it cites; every number in the profile
  must appear in some entry. One residual, stated: an entry that groups digits with a plain
  space is read both ways, because sluice cannot tell which you meant -- an entry reading
  `Led 3 100-person teams` licenses `3,100` as well as `3` and `100`. A second: an ASCII
  letter written against a digit is not refused, so `8O%` with a capital O passes with only
  the `8` checked, because `5G` and `O2` are real text. A non-Latin letter there (a Cyrillic
  or Greek O) is refused.
- Once any entry declares `Tools:`, a tool named in a bullet must be listed, or named, by an
  entry it cites.
- Headings, dates, locations, titles, certificates, education, your name and your contact
  details come from your vault, never from the model.
- A skill the model picks that is not one of your verified skills or tools is dropped, never
  shown.

The model's reply is **data**: a JSON object holding the profile, the cited bullets and the
skill picks. Sluice reads it, checks it, and assembles the CV itself, so the gate never
re-parses composed text and no line of prose can mint or rebind a citable source. A reply that
writes a number or a word in a form the checks could read differently from the PDF -- a
numeral without digits (a Roman, CJK or vulgar-fraction numeral, a circled number past nine),
a look-alike letter, an unusual separator between digits, a comma decimal, a number grouped
with a plain space -- is refused and sent back for the one retry. A bullet over its role's
budget is trimmed rather than checked, so one written that way and then trimmed costs
nothing: it never reaches the CV.

Above the hard gate sits an advisory LLM audit, which catches the qualitative fabrication a
deterministic check cannot — a claim that is technically sourced and still misleading. It does not
block rendering. It withholds the send-ready CV pointer for your sign-off, which you clear with
`job-sluice cv signoff`.

**One limit, stated rather than buried.** The gate checks what the *model* wrote. A custom Jinja2
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
