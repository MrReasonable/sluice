# CV bundle parser — free text must not widen the INVENTED METRIC allowlist

- **Date**: 2026-07-20
- **Status**: REVISED after two `/review-plan` rounds. Round 1 (5 agents): 0C / 6H / 6M / 3L.
  Round 2 (4 agents, anti-flattery brief): 1C / 1H / 5M / 4L. All folded.
- **Origin**: issue #31. The second defect (§2) was found by the invariant reviewer in round 1 and is
  folded here rather than filed, on the standing rule that a self-contained follow-up is addressed in
  the PR that surfaces it.
- **Scope decisions (user-confirmed)**:
  - Fold the `[id]`-anchor bypass (§2). Same function, same widening shape, fails open.
  - Port the tests to renderer-backed fixtures (chosen over additive-only), as its own commit.
  - Regenerate the fixtures in the three touched test files (§Neutrality).
  - The BASELINE half of the walk is **pinned by a test, not changed** — permitting baseline numbers
    is #30's design question.

## Problem — two defects in one walk, both widening the same allowlist

`_bundle_ids_and_nums` in `sluice/cv/validate.py` attributes numbers to the most recently seen `[id]`.
`render_bundle` splices **user free text** into that stream (entry `body` fields, the `baseline`
block) and appends the **negative constraints** after the last entry. Both defects below let a number
reach `nums[<entry>]` that the entry does not contain; `validate()` computes
`invented = bullet_nums - union`, so both let a WORK bullet carry a fabricated figure and pass.

### Defect 1 (#31) — the negatives block is attributed to the last entry

`render_bundle` emits `=== NEGATIVE CONSTRAINTS (must NOT appear) ===` then `- {n}` lines. Those match
neither the `[id]` pattern nor any boundary the parser recognises, so they hit `elif cur:` while `cur`
still points at the last-ranked entry.

**Reproduced end to end** before this spec was written, and independently re-reproduced by four
reviewers. With `negatives=["never claim 500 users"]` and `- Scaled the platform to 500 users [TR1]`:

```
nums['TR1'] == {'90', '500', '99'}      # 500 and 99 come from the NEGATIVES
validate(...) == []                      # the hard gate passes a fabricated figure
```

Negative constraints exist *because* the model has hallucinated those figures before, so this is the
class of number most likely to reappear. The one guard aimed at them is the one that stops firing.

### Defect 2 — any bracket-led free-text line becomes a citable id

`re.match(r"\[([^\]]+)\]", line)` matches **any** bracket-led line, and entry `body` / `baseline` are
user free text passed through verbatim. Reproduced on today's tree: a body line

```
[2019] Rebuilt the pipeline to 250 nodes
```

registers `2019` as a **citable id** with `nums={'250'}`, so `- Ran 250 nodes [2019]` returns
`violations: []`. This one **fails open**, one region earlier in the same walk.

### Why the existing tests did not catch either

`tests/test_cv_validate.py` builds its bundle by hand — no `===` headers, no entry bodies, no
baseline, no negatives. Confirmed: `render_bundle` appears only in `tests/test_cv_bundle.py` and
`validate` only in `tests/test_cv_validate.py`, so **the parser has never been exercised against real
renderer output.** That unrealism is the shared root cause, and the port (§Commit 3) is the durable
half of the fix.

## The governing rule

**`cur` is set only by a line whose leading bracket matches the id shape the bundle actually
generates, and is cleared by anything that ends the entry list.** Numbers reach an entry only while
the walk is inside that entry's own lines.

Round-1 note: the first draft asserted the opening clause as though already true. Defect 2 is its
falsification. §2 makes it true; §4 records precisely how far, because it is a narrowing and not a
closure.

## Design

### 1. Section headers clear `cur` — `sluice/cv/validate.py`

```python
# render_bundle emits `=== SECTION ===` headers; NEGATIVE CONSTRAINTS is the one that
# lands AFTER the last [id]. Without this reset its lines fall through to `elif cur`
# and union the do-not-say numbers into the last-ranked entry's allowlist -- so the one
# guard aimed at those figures stops firing on exactly them. The `[^=]` guards require a
# non-'=' character inside each delimiter, so a bare setext underline (`======`) in an
# entry body does not reset (pinned by Test 2b). A body line genuinely shaped like a
# header still would, and that fails CLOSED: the entry's later numbers drop and a
# legitimate bullet is flagged INVENTED METRIC -- visible, never a silent pass. (#31)
_SECTION_RE = re.compile(r"^\s*={3,}[^=].*[^=]={3,}\s*$")
```

Round-1 correction: `^\s*={3,}.*={3,}\s*$` made the comment's claim **false** — `.*` matches `=`, so
`======` matched. Verified independently by two reviewers: the `[^=]` form matches all three real
headers and rejects `======`, `=======`, `=== ===`, `=== =====`. (`===  ===`, two spaces, does match —
degenerate, and fails closed.)

### 2. The id pattern is anchored to the generated shape

```python
# assign_codes/_prefix guarantee exactly two A-Z letters plus a sequence number, so
# anchoring costs nothing real and closes a gate bypass: `body` and `baseline` are user
# free text spliced in verbatim, and an unanchored bracket match turned a line like
# `[2019] Rebuilt the pipeline to 250 nodes` into a citable id owning 250 -- a bullet
# could cite a YEAR and carry a fabricated figure past the gate. Fails CLOSED: a bullet
# citing an id this rejects is an unknown citation, already a BAD CITATION violation.
# NB this NARROWS the bypass, it does not close it -- see Test 6.
_ID_RE = re.compile(r"^\[([A-Z]{2}\d+)\]")
```

Verified: fuzzing `assign_codes`/`_prefix` over empty, numeric, non-ASCII, single-letter and long
company names produced **zero** ids this rejects (`_prefix` coerces every input to exactly two A-Z,
including the `XX` fallback). Two reviewers ran this independently, one at 1.2M samples.

**Corroboration this design did not originally claim:** `sluice/cv/render.py:10` already encodes the
same shape — `_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")`. The strip step was already
assuming the id shape that `validate` ignored, so anchoring aligns the two rather than inventing a
constraint. (`render.py` is case-insensitive; `_prefix` uppercases, so `[A-Z]{2}` is the tighter and
correct form here.)

### 3. The shared format contract is documented on the producer side

`bundle.py`'s module docstring already states the `[id]` contract. This change adds a *second* shared
contract, so `render_bundle` gains a docstring line stating that its `=== ... ===` headers terminate
the preceding entry for the validate gate. Producer-side documentation is the established precedent
in this file. Shipped in the **same commit** as §1 — a parser depending on a producer contract
documented two commits later is a gap, not a sequence.

### 4. Behaviour surface

| Bundle region | Before | After |
| --- | --- | --- |
| Entry `[id]` line + its `body` | attributed to that entry | **unchanged** |
| A bracket-led free-text line whose bracket is **not** id-shaped (`[2019] …`) | becomes a citable id owning its numbers | not an id; its numbers join the enclosing entry |
| A bracket-led free-text line whose bracket **is** id-shaped (`[QQ7] …`) | becomes a citable id | **still becomes a citable id — narrowed, not closed** |
| `=== NEGATIVE CONSTRAINTS ===` block | attributed to the last-ranked entry | not attributed |
| `=== BASELINE CV ===` block | contributes no numbers | **unchanged** (pinned by Test 3) |

Round-2 correction: the third row previously read as a closure. It is not. A body line
`[QQ7] fabricated 500 users` still mints a citable id, and `- Scaled to 500 users [QQ7]` returns `[]`.
Closing it needs `validate()` to receive the real id list — a signature change, out of scope. The
residual is bounded, documented, and **pinned by Test 6** so it cannot silently widen.

`validate()`'s signature, purity and determinism are untouched. No config knob, so no
`sluice.yaml.example` change; `docs/ARCHITECTURE.md` describes the cv gate only generically
(lines 42–48) and needs no update — verified by three reviewers, not assumed.

## Tests — `tests/test_cv_validate.py`

All build their bundle through `build_bundle` + `render_bundle` with **`jd_keywords=[]`** and an
explicit **`prefix_map`**, so ids are deterministic (`build_bundle` ranks *before* `assign_codes`, so
ids are unstable without both). Ids are verified by printing the rendered bundle, never inferred.

1. **Negatives regression.** A bullet citing the last-ranked entry, using a number appearing only in
   `negatives` → `INVENTED METRIC`. Fails today (returns `[]`).
2. **Not over-broad.** A legitimate bullet citing that entry, using a number from the entry's
   **`body`** → passes. Round-1 correction: this previously drew its number from `metrics=`, which is
   parsed on the same line that sets `cur` and so **cannot be affected by any cur-clearing change**.
   Two reviewers measured the old form green against a maximally over-broad mutant; the body-sourced
   form dies against it.
2b. **A setext underline in a body does not reset.** Body `Highlights` / `======` /
   `Cut latency to 250 ms`; a bullet citing `250` passes. Pins the `[^=]` clause, which was otherwise
   a load-bearing comment with nothing executing it.
3. **Baseline stays non-permitted.** A bullet using a number appearing only in the baseline block is
   flagged. Pins today's behaviour so #30 changes it deliberately.
4. **Non-id bracket is not an id.** A bullet citing `[2019]` where a body line reads
   `[2019] Rebuilt the pipeline to 250 nodes` → `BAD CITATION`. Fails today.
5. **Its numbers still reach the enclosing entry.** A bullet citing the real enclosing id and using
   `250` → passes. Guards against fixing §2 by discarding the line's numbers entirely.
6. **The residual is bounded and known.** A body line `[QQ7] fabricated 500 users` still mints a
   citable id: a bullet citing `[QQ7]` for `500` passes. Asserts the *current* bound so that any
   future change that widens or closes it is visible rather than silent.

## Mutation witnesses (required, per CLAUDE.md)

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` **once first**.
Mutate by **moving or deleting** — a check added *beside* the original is an equivalent mutant and
stays green. Row 7 is the documented exception: it tests the *absence* of a branch, so the additive
form is the only form, and it is verifiably not equivalent.

| # | Mutation | Kills |
| --- | --- | --- |
| 1 | Delete `cur = None` from the `_SECTION_RE` branch | Test 1 |
| 2 | Delete the whole `_SECTION_RE` branch | Test 1 |
| 3 | Restructure the branch into a third `elif` after `elif cur:` (genuinely unreachable) | Test 1 |
| 4 | Widen `_SECTION_RE` to `^\s*={3,}.*={3,}\s*$` | Test 2b |
| 5 | Revert `_ID_RE` to `\[([^\]]+)\]` | Tests 4 + 5 |
| 6 | Clear `cur` on **every** non-`[id]` line (maximally over-broad) | Tests 2 + 5 |
| 7 | Drop the numbers of a bracket-led non-id line instead of unioning them *(additive; see note)* | Test 5 |
| 8 | Bucket baseline numbers under the first entry (a #30-shaped change) | Test 3 |

Round-1 correction: the previous row 3 read "move the branch *after* the `elif cur:` arm", which two
reviewers independently measured **green** — headers carry no digits, so `elif cur:` unions nothing
and the reset still fires in the same iteration. Equivalent mutant; a green mutant reads as "this test
is inert." Round-2 corrections: row 4 was previously recorded as a non-killing row, which was honest
but left the `[^=]` clause unexecuted — Test 2b makes it a killing row. Row 8 is new: Test 3 was
witnessed by nothing.

All eight rows were run out-of-tree by the test-engineer, each with a behavioural fingerprint proving
the mutant loaded, each restored byte-identical.

## Neutrality

Fixture content in the touched files is **generated for the test** — labels, dates, locations, role
titles, and the overall structure alike. Nothing is adapted from any real document, render, CV, or
`sluice.local.yaml`. Three files are in scope because this change rewrites the relevant lines in each:

- `tests/test_cv_validate.py` — ported and regenerated (§Commit 3/4).
- `tests/test_cv_engine.py` — fixture regenerated.
- `tests/test_cv_slop.py:13` — fixture regenerated. Round-2 finding: this line was initially swept
  into the "location literal, out of scope" narrowing below, but it differs in kind — it carries a
  fixture value, not a bare location. Its assertion needs only an en-dash date range, so any synthetic
  string satisfies it.

Only the **descending-start-year** property is load-bearing: `sluice/cv/validate.py:39` extracts
`\d{2}/(\d{4})\s*[–-]` and nothing else. Everything above that is free to be regenerated.

**Scope narrowing, stated honestly:** a bare `LONDON` location literal appears in 9 test files
(CLAUDE.md forbids locations in `tests/`; `conftest.py` establishes `Alfa`/`Bravo`/`Charlie`). This
change fixes it in the three files it already rewrites and leaves the other six. That is a partial
remediation of a repo-wide class, not a closure. (#27 covers `tests/fixtures/*/raw.json` — captured
payloads, a separate question.)

**Pre-existing copies outside the working tree** are recorded separately and deliberately not
enumerated here; remediating the working tree does not reach them, and the PR body will say so
without restating the content.

## Port discipline

The port is where this plan can quietly go wrong, so it is constrained:

- **Assertion-level survival.** Every test function survives by name **and every `assert` in it
  survives with the same intent**. Round-1 correction: the previous wording checked only function
  names, but #26's recorded escape lost an *assertion inside a surviving structure*.
- **Explicit test→mutation pairs, and they are RUN.** Each ported test names the mutation that kills
  it, and that mutation is executed and observed red. Round-2 correction: naming pairs without running
  them is the unfalsified-claim shape this repo has shipped before. Any ported test with no killing
  mutation is reported in the PR body, not quietly kept.
- **Ids are deterministic or the port is wrong.** `prefix_map` and `jd_keywords=[]` are mandatory.
  `test_multi_citation_union` and `test_id_digits_not_counted_as_metric` both assert `== []`, which
  **cannot report that it is checking the wrong entry**, so their ids are verified by printing the
  rendered bundle.
- **`CLEAN_CV` gets a direct assertion.** It feeds `validate`'s reverse-chronology check; breaking it
  currently fails 5 tests loudly but leaves 3 `skipped-gate` tests vacuous. Assert
  `validate(CLEAN_CV, …) == []` directly so the fixture's validity is stated, not implied.

## Definition of done

- `python -m pytest` green (712 today; +6 before the port).
- `ruff check sluice tests` clean.
- Every row of the mutation table run, its stated outcome observed, then restored byte-identical.
- Every port test→mutation pair run and observed red; any pair without a killing mutation reported.
- Every assertion present in `tests/test_cv_validate.py` before the port present after it.
- The three touched test files' fixtures are regenerated; `rg -n 'LONDON' tests/test_cv_validate.py
  tests/test_cv_engine.py tests/test_cv_slop.py` returns nothing.

## Out of scope

- **#30** (gating the PROFILE) — its own design; §1 and §2 are prerequisites for the version reusing
  `nums`. Mutation row 8 is deliberately shaped like the change #30 would make.
- **#28** — falsified as written; unrelated.
- Closing the id-shaped residual in §4 — needs a `validate()` signature change.
- The six other test files carrying a bare location literal — a repo-wide pass.
- Any change to `render_bundle`'s *output*. The renderer marks every section; the parser is the half
  that ignored the marking.

## Commits

1. `fix(cv): a bundle section header ends the entry it follows (#31)` — §1 + §3 + Tests 1, 2, 2b, 3.
2. `fix(cv): only a generated [id] code opens a bundle entry (#31)` — §2 + Tests 4, 5, 6.
3. `test(cv): build validate fixtures through render_bundle (#31)` — the port.
4. `test(cv): regenerate cv test fixtures from synthetic values (#31)` — the neutrality rewrite across
   the three files. Split from the port: `test_cv_engine.py` and `test_cv_slop.py` are not ported,
   and a message naming only the port would misdescribe them.
