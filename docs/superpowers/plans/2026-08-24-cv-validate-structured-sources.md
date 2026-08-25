# Structured Source Set for the CV Fabrication Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `cv/validate.py` recovering the citable `[id]` codes by re-parsing the rendered bundle text, so a line of user free text can no longer mint an id or rebind another entry's permitted numbers.

**Architecture:** `sluice/cv/bundle.py` grows one definition of what an entry contributes to the prompt (`_entry_block`) and one for the baseline (`_baseline_block`). `render_bundle` joins them into the prompt; a new `bundle_sources` harvests `\d+` from the same blocks and returns a `BundleSources`. `validate()`'s second parameter becomes that value instead of text, and the bundle parser (`_bundle_ids_and_nums`, `_ID_RE`, `_SECTION_RE`) is deleted. Ids and entry boundaries then come from `build_bundle`'s structure and cannot be parsed out of user text.

**Tech Stack:** Python 3.12+, stdlib only in `sluice/`. pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-24-cv-validate-structured-sources-design.md` — read it before Task 1. It carries the measurements, the rejected alternatives (D1–D10), and two `/review-plan` rounds of corrections. The plan below argues from it and does not repeat its reasoning.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency. `bundle.py` may import `re` and `typing.NamedTuple`; nothing else.
- **`cv/validate.py` stays PURE and DETERMINISTIC.** No I/O, no clock, no randomness. Same inputs, same violation list, same order.
- **The retry-once-then-skip contract in `cv/engine.py` does not move.** `for _ in range(2)`, the retained hard-clean draft, and the `best` rebind are untouched by this work.
- **No personal data in `sluice/` or `tests/`.** Every fixture value is synthetic. New `Example <Word>` identities need a roster entry (Task 8).
- **Exception messages never carry USER CONTENT.** The subject of this rule is the argument a
  stale caller would pass `validate()` — the entire rendered bundle: the baseline CV verbatim plus
  every entry's company, title, metrics and body. For that, name `type(x).__name__` and nothing
  else. Never `{x}`, `{x!r}`, `%s`, `str(x)` or `repr(x)` — a `NamedTuple`'s `str()` IS its
  `repr()`, and `cv/engine.py:795` logs the exception with `%s`.
  A GENERATED IDENTIFIER is not user content and is deliberately named: `bundle_sources`'
  duplicate-id `ValueError` says which id collided, because `type(eid).__name__` is always `str`
  and a message that refuses to say which id is undiagnosable — which fights this repo's
  fail-loudly-and-list-the-valid-names rule. Measured, the exposure is nil anyway: that same
  `_log.warning` line already logs `note.ref`, which is the note's filesystem PATH, and the vault
  derives note filenames from company and title. The company is in that log line unconditionally,
  on every failure, whether or not an exception message mentions a two-letter prefix derived from
  it. An earlier draft of this constraint said "no part of its VALUE", which reads as forbidding
  the id too; that overstated the rule and is corrected here rather than obeyed literally.
- **Mutants go in `sluice/` only.** Never edit a test file inside a witness loop: `compileall --invalidation-mode checked-hash` does not cover pytest's own rewritten test bytecode, which stays timestamp-based.
- **Mutate by MOVING or DELETING, never by ADDING.** A check added beside the original is an equivalent mutant and survives.
- Quality bar for every commit: `.venv/bin/python -m pytest` green and `.venv/bin/ruff check sluice tests scripts` clean. Ruff is not in `[test]`: `.venv/bin/pip install ruff==0.15.21`. Use the venv's own executables throughout -- a version-manager shim can silently resolve a different `ruff`/`pip` outside an activated venv, and CI pins this version.

---

### Task 1: Capture the pre-change prompt text

This task creates the frozen literal every later task is measured against. It must be done FIRST, while `render_bundle` is still the shipped implementation — the whole point is a reference the code under test cannot move.

**Files:**
- Modify: `tests/test_cv_bundle.py` — append the frozen fixture block below the existing `ENTRIES`

**Why not a separate `tests/fixtures/` module:** Task 8 derives the neutrality sweep from
`tests/test_cv_*.py`. A module under `tests/fixtures/` sits outside that glob, so the
`Example <Word>` identities introduced here would escape the very ratchet Task 8 widens.

**Interfaces:**
- Produces: module-level `FROZEN_ENTRIES`, `FROZEN_BASELINE`, `FROZEN_NEGATIVES`, `FROZEN_PREFIX_MAP`, `FROZEN_BUNDLE_TEXT` in `tests/test_cv_bundle.py` — consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the generator script in the scratchpad (NOT in the repo)**

```python
# scratchpad/gen_frozen.py
from sluice.cv.bundle import build_bundle, render_bundle

ENTRIES = [
    {"company": "Example Alpha 31", "title": "Staff Engineer, 32 teams",
     "metrics": "33 34", "best_for": "leadership 35", "category": "platform 36",
     "body": "Ran 37 services.\nOwned 38 dashboards."},
    {"company": "Example Beta 41", "title": "Principal Engineer",
     "metrics": "43", "best_for": "delivery 45", "category": "data 46",
     "body": "Cut latency to 47 ms."},
    {"company": "Example Alpha 51", "title": "Engineer", "metrics": "53",
     "best_for": "", "category": "", "body": ""},
]
b = build_bundle(entries=ENTRIES, baseline="Baseline names 21 and 22.",
                 negatives=["never claim 91 users", "never claim 92 uptime"],
                 jd_keywords=[],
                 prefix_map={"Example Alpha 31": "AL", "Example Beta 41": "BE",
                             "Example Alpha 51": "AL"})
print(repr(render_bundle(b)))
```

Every field carries a DISTINCT sentinel, including `best_for` and `category` which `render_bundle` does not emit, and one entry has no `body` so the no-body arm is covered. `jd_keywords=[]` plus an explicit `prefix_map` are both required for deterministic ids — `build_bundle` ranks before it assigns codes.

- [ ] **Step 2: Run it and capture the output**

Run: `.venv/bin/python scratchpad/gen_frozen.py`
Expected: a single `repr()` of the rendered bundle, on one line.

- [ ] **Step 3: Write the fixture module**

Paste the captured string as `FROZEN_BUNDLE_TEXT`. Write it as a triple-quoted literal with real newlines rather than the `repr`, so a future diff is readable.

```python
# tests/test_cv_bundle.py -- append below the existing ENTRIES
"""The bundle prompt as it stood BEFORE #174's refactor, frozen as a literal.

This is the reference for `tests/test_cv_bundle.py`'s two assertions, and it is
load-bearing that it is a LITERAL rather than something recomputed: a reference derived
from `render_bundle` moves with any mutation of `render_bundle`, which is exactly how
this spec's revision 2 shipped three tests that killed nothing (measured -- see the
design doc's D10). Captured from the shipped implementation at the commit that precedes
the refactor.

Every entry field carries a distinct sentinel digit, INCLUDING `best_for` and `category`,
which `render_bundle` does not emit -- so the equality below asserts their exclusion
rather than merely failing to mention it. The third entry has no body, covering
`_entry_block`'s one-line arm.

Updating this literal is a DELIBERATE act: it means the prompt the model sees has
changed. Re-capture it, read the diff, and say in the commit message why the prompt moved.
"""

FROZEN_ENTRIES = [ ... ]        # exactly as in the generator above
FROZEN_BASELINE = "Baseline names 21 and 22."
FROZEN_NEGATIVES = ["never claim 91 users", "never claim 92 uptime"]
FROZEN_PREFIX_MAP = {"Example Alpha 31": "AL", "Example Beta 41": "BE",
                     "Example Alpha 51": "AL"}   # keys are the FULL company strings:
# `_prefix` does `prefix_map.get(company) or company`, so a key that is a PREFIX of the
# company falls through to deriving "EX" from the name and every id collides into one
# sequence. Measured. tests/test_cv_bundle.py:5-9 already documents this trap.

FROZEN_BUNDLE_TEXT = """\
<paste the captured text here, verbatim>"""
```

- [ ] **Step 4: Verify the literal round-trips against the SHIPPED renderer**

```python
# scratchpad/check_frozen.py -- run from the repo root
from sluice.cv.bundle import build_bundle, render_bundle
from tests.test_cv_bundle import (
    FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES, FROZEN_PREFIX_MAP, FROZEN_BUNDLE_TEXT)

b = build_bundle(entries=FROZEN_ENTRIES, baseline=FROZEN_BASELINE,
                 negatives=FROZEN_NEGATIVES, jd_keywords=[], prefix_map=FROZEN_PREFIX_MAP)
assert render_bundle(b) == FROZEN_BUNDLE_TEXT, "the literal does not match the shipped renderer"
print("frozen literal matches the shipped renderer")
```

Run: `.venv/bin/python scratchpad/check_frozen.py`
Expected: `frozen literal matches the shipped renderer`. If it does not, the paste lost a trailing newline or a blank line — fix the literal, not the assertion.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv_bundle.py
git commit -m "test(cv): freeze the bundle prompt before the #174 refactor"
```

---

### Task 2: Extract `_entry_block` and `_baseline_block`, byte-identically

Pure refactor. `render_bundle`'s output must not move by one byte — Task 1's literal is the proof.

**Files:**
- Modify: `sluice/cv/bundle.py:45-65`
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: Task 1's `FROZEN_BUNDLE_TEXT`.
- Produces: `_entry_block(entry: dict) -> list[str]`, `_baseline_block(bundle: dict) -> list[str]` — consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cv_bundle.py -- append
def _frozen_bundle():
    return B.build_bundle(entries=FROZEN_ENTRIES, baseline=FROZEN_BASELINE,
                          negatives=FROZEN_NEGATIVES, jd_keywords=[],
                          prefix_map=FROZEN_PREFIX_MAP)


def test_the_rendered_prompt_has_not_drifted():
    """`render_bundle`'s output IS the prompt two live LLM calls see (cv/engine.py:320
    compose, :647 audit). The pre-#174 text is frozen at the top of this file, so a refactor
    that changes presentation without changing any digit -- reordering fields, renaming
    `metrics=`, dropping the inter-entry blank line -- is caught here rather than shipping
    a silently different prompt. The existing substring pin below cannot see any of those.

    Updating the literal is the deliberate act; failing this test is not a reason to
    weaken it."""
    assert B.render_bundle(_frozen_bundle()) == FROZEN_BUNDLE_TEXT
```

- [ ] **Step 2: Run it to confirm it PASSES before the refactor**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py::test_the_rendered_prompt_has_not_drifted -v`
Expected: PASS. This test starts green on purpose — it is a regression pin for the refactor in Step 3, not a spec for new behaviour. Its failing state is proved in Step 5.

- [ ] **Step 3: Extract the two builders**

Replace `render_bundle` in `sluice/cv/bundle.py` with:

```python
def _entry_block(entry: dict) -> list[str]:
    """The lines ONE entry contributes to the rendered bundle.

    The single definition of what an entry is made of, shared by `render_bundle` (which
    joins these into the prompt) and `bundle_sources` (which harvests this entry's
    permitted numbers from them). Sharing it is what makes the prompt and the allowlist
    unable to disagree -- see #174.

    THE RULE, and it is narrower than it looks: every line this function returns is a
    SOURCE for that entry, and nothing else is. Not "whatever the model was shown" -- the
    NEGATIVE CONSTRAINTS block is shown to the model and is deliberately not citable
    (#31). So a line added here becomes citable by that entry, measured: a per-entry
    "do NOT claim N" caution folded in here widened every entry's allowlist with the
    whole suite green. Presentation that must not become a source belongs in
    `render_bundle`, not here.

    Excludes the inter-entry blank line for the same reason: it is presentation, carries
    no digits, and `render_bundle` owns it.
    """
    lines = [f"[{entry['id']}] ({entry.get('company','')}) {entry.get('title','')} "
             f"| metrics={entry.get('metrics','')}"]
    if entry.get("body"):
        lines.append(entry["body"])
    return lines


def _baseline_block(bundle: dict) -> list[str]:
    """The baseline CV's SOURCE lines -- no header, no blank, no slice.

    Sibling of `_entry_block`, same rule: every line returned is a source, this time for
    the PROFILE-only pool. It holds no header deliberately. An earlier draft returned the
    header too and had `bundle_sources` drop it with `block[1:]`, which has two live
    mutants: keep a second header and its future digits become citable in the one region
    with no BAD-CITATION backstop behind it (`validate.py`'s profile sweep); drop the
    header and `[1:]` eats the real baseline instead, so every baseline-sourced profile
    figure is reported INVENTED and the lead is skipped. Owning no presentation removes
    both.
    """
    return [bundle["baseline"]]


def render_bundle(bundle: dict) -> str:
    """Render the bundle as the prompt text the model actually sees.

    The `[id]` codes and the `=== SECTION ===` headers used to be a parsing contract with
    `cv/validate.py`, which recovered the citable ids from this text. It no longer does
    (#174): ids and entry boundaries come from `build_bundle`'s structure via
    `bundle_sources`, so no line of user free text can mint or rebind one. The headers
    are now presentation only, and this function owns ALL of them -- the two builders
    above own only source lines.

    `tests/test_cv_bundle.py::test_the_rendered_prompt_has_not_drifted` pins this
    function's exact output, because it is the prompt two live LLM calls receive.
    """
    lines = ["=== BASELINE CV (authoritative for dates/employers/certs) ==="]
    lines += _baseline_block(bundle)
    lines += ["",
              "=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ==="]
    for e in bundle["entries"]:
        lines += _entry_block(e)
        lines.append("")
    lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
    lines += [f"- {n}" for n in bundle["negatives"]]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS, all of it. A byte-identical refactor breaks nothing. If `test_the_rendered_prompt_has_not_drifted` fails, the extraction changed the output — fix the extraction.

- [ ] **Step 5: Witness the new test**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Mutant: in `_entry_block`, DELETE the `lines.append(entry["body"])` line (and its `if`).

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py::test_the_rendered_prompt_has_not_drifted -v`
Expected: FAIL. Restore the line, re-run, expect PASS.

Second mutant, the one the existing substring pin cannot see: in `render_bundle`, DELETE `lines.append("")` inside the entry loop.
Expected: FAIL. Restore.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -m "refactor(cv): extract the bundle's per-entry and baseline block builders"
```

---

### Task 3: `BundleSources` and `bundle_sources`

**Files:**
- Modify: `sluice/cv/bundle.py`
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: `_entry_block`, `_baseline_block` (Task 2); Task 1's fixture.
- Produces: `BundleSources(nums: dict[str, frozenset[str]], baseline: frozenset[str])` with an `ids` property, and `bundle_sources(bundle: dict) -> BundleSources` — consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cv_bundle.py -- append
import pytest


def _oracle(bundle_text):
    """`_bundle_ids_and_nums` as it stood in sluice/cv/validate.py before #174 deleted it.

    Transcribed from `git show <PRE-REFACTOR-SHA>:sluice/cv/validate.py` lines 52-77. The
    ONLY change in transcription: `nums` values are frozen to `frozenset` to compare
    against `BundleSources`. Every predicate -- both regexes, the `continue`, the three
    branches -- is byte-for-byte the pre-change code.

    Deriving this reference by reading the NEW code would assert that the code equals
    itself and certify nothing. Feeding it `render_bundle(b)` would do the same one level
    out, because `render_bundle` is itself under test here: measured, `drop_title`,
    `drop_company` and `emit_best_for` ALL SURVIVE that spelling, since both sides of the
    equality move with the mutant. It is fed the FROZEN literal for that reason.
    """
    section_re = re.compile(r"^\s*={3,}[^=].*[^=]={3,}\s*$")
    id_re = re.compile(r"^\[([A-Z]{2}\d+)\]")
    nums, baseline = {}, set()
    cur, seen_id = None, False
    for line in bundle_text.splitlines():
        if section_re.match(line):
            cur = None
            continue
        m = id_re.match(line)
        if m:
            seen_id, cur = True, m.group(1)
            nums[cur] = set(re.findall(r"\d+", line[m.end():]))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
        elif not seen_id:
            baseline |= set(re.findall(r"\d+", line))
    return {k: frozenset(v) for k, v in nums.items()}, frozenset(baseline)


def test_the_allowlist_still_matches_the_frozen_prompt():
    """The co-variant detector, and the reason the reference is a frozen literal.

    `_entry_block` feeds BOTH the prompt and the allowlist, so deleting a field from it
    removes that field from both and any render-vs-sources comparison still agrees --
    measured across 24 scenarios, killed by nothing. Comparing against text captured
    BEFORE the refactor is what makes the loss visible: the frozen literal still contains
    the digits, the new derivation no longer yields them, and the equality breaks.

    The corpus is CLEAN on purpose. On POISONED input (an `[XX9]`-shaped line inside an
    entry body or the baseline) the two are deliberately UNEQUAL -- that inequality is
    the entire point of #174, and a future reader must not "repair" this by widening the
    corpus.
    """
    b = _frozen_bundle()
    assert B.bundle_sources(b) == B.BundleSources(*_oracle(FROZEN_BUNDLE_TEXT))


def test_ids_is_derived_from_nums():
    b = _frozen_bundle()
    s = B.bundle_sources(b)
    assert set(s.ids) == set(s.nums)
    assert set(s.ids) == {"AL1", "BE1", "AL2"}


def test_a_duplicate_id_raises_naming_the_id_and_not_the_entry():
    """`assign_codes` cannot produce a duplicate, so this is unreachable from
    `build_bundle`. It earns its lines because `bundle_sources` takes an untyped dict and
    #164 is about to give bundle contents a non-human author -- and because the failure it
    prevents is one entry's allowlist silently replacing another's, which is #174's own
    defect shape one layer up.

    The message must name the ID and no part of the ENTRY: an entry carries the user's
    company, title, metrics and body, and cv/engine.py:789 logs a failed run with %s.
    """
    b = {"baseline": "", "negatives": [],
         "entries": [{"id": "AL1", "company": "Example Alpha", "title": "A",
                      "metrics": "1", "body": "secret body text"},
                     {"id": "AL1", "company": "Example Beta", "title": "B",
                      "metrics": "2", "body": "other secret"}]}
    with pytest.raises(ValueError) as ei:
        B.bundle_sources(b)
    assert "AL1" in str(ei.value)
    assert "secret body text" not in str(ei.value)
    assert "Example Alpha" not in str(ei.value)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k "allowlist or ids_is_derived or duplicate_id" -v`
Expected: FAIL, `AttributeError: module 'sluice.cv.bundle' has no attribute 'bundle_sources'`.

- [ ] **Step 3: Implement**

Add to `sluice/cv/bundle.py`, importing `NamedTuple` from `typing` at the top:

```python
class BundleSources(NamedTuple):
    """What the fabrication gate is allowed to treat as a source, keyed by entry id.

    Handed to `cv/validate.py` instead of the rendered bundle TEXT (#174). The gate used
    to recover this by re-parsing that text, which meant any line of user free text could
    decide what an id was: a body line shaped like an existing code REBOUND that entry's
    permitted numbers, so a fabricated figure passed AND the entry's real metric was
    reported INVENTED. Passing the derived value removes the gate's capability to be
    fooled, rather than narrowing the ways in.

    `ids` is a derived property rather than a third field. Carrying it as data would
    re-create, one level up, the exact redundancy this fixes -- two structures that can
    disagree about what an id is.
    """
    nums: dict[str, frozenset[str]]
    baseline: frozenset[str]

    @property
    def ids(self):
        return self.nums.keys()


def bundle_sources(bundle: dict) -> BundleSources:
    """Derive the citable ids and their permitted numbers from the bundle's STRUCTURE.

    Ids and entry boundaries come from `build_bundle`; the numbers come from exactly the
    lines that entry contributed to the prompt, via the shared `_entry_block`. Nothing
    here parses the rendered text, so nothing here can invent an id.

    `bundle["negatives"]` is read by NOTHING. #31 established that exclusion by where the
    negatives happened to land in the text, which failed at zero entries -- with no ids
    the negatives fell through into the baseline pool and a do-not-say figure was
    profile-permitted (measured). It is now a property of the derivation.

    The `[{id}] ` token is sliced by LENGTH from the known id, never matched out of the
    text: `_entry_block` puts it first on line 0, and that offset-0 contract is what
    `test_the_allowlist_still_matches_the_frozen_prompt` pins.
    """
    nums: dict[str, frozenset[str]] = {}
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in nums:
            # Fail loudly at construction. Naming the id and NOT the entry is deliberate:
            # the entry holds the user's own CV prose, and this message reaches a log.
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]   # drop the leading `[{eid}]`
        nums[eid] = frozenset(re.findall(r"\d+", "\n".join(block)))
    baseline = frozenset(re.findall(r"\d+", "\n".join(_baseline_block(bundle))))
    return BundleSources(nums, baseline)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -v`
Expected: PASS.

- [ ] **Step 5: Witness the co-variant detector**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Run each mutant, then restore, then run the next. Each must FAIL
`tests/test_cv_bundle.py::test_the_allowlist_still_matches_the_frozen_prompt`:

| # | Mutant (all DELETIONS or MOVES in `sluice/cv/bundle.py`) |
| --- | --- |
| 1 | In `_entry_block`, delete `{entry.get('title','')} ` from the f-string |
| 2 | In `_entry_block`, delete `({entry.get('company','')}) ` from the f-string |
| 3 | In `_entry_block`, delete `| metrics={entry.get('metrics','')}` from the f-string |
| 4 | In `bundle_sources`, delete the `block[0] = block[0][len(eid) + 2:]` line |
| 5 | In `_baseline_block`, return `[]` instead of `[bundle["baseline"]]` |

Run each by node id. Confirm no sibling test already kills it — run
`.venv/bin/python -m pytest tests/test_cv_bundle.py -v` under mutant 1 and check that
`test_the_allowlist_still_matches_the_frozen_prompt` is among the failures rather than only
`test_the_rendered_prompt_has_not_drifted`.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -m "feat(cv): derive the gate's source set from the bundle structure (#174)"
```

---

### Task 4: Change `validate()`'s signature and delete the parser

**Files:**
- Modify: `sluice/cv/validate.py:12-49` (delete two regexes, rewrite the `_CITE_RE` comment), `:52-77` (delete `_bundle_ids_and_nums`), `:139-198` (`validate`)
- Test: `tests/test_cv_validate.py`

**Interfaces:**
- Consumes: `BundleSources`, `bundle_sources` (Task 3).
- Produces: `validate(cv_text, sources: BundleSources, employers=None, fabrication_decoys=None) -> list[str]` — consumed by Tasks 5 and 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cv_validate.py -- append
def test_a_stale_text_caller_fails_loudly_without_echoing_the_bundle():
    """The second parameter changed TYPE while keeping its POSITION, so a caller left on
    the old signature must be told what to do rather than dying inside the gate with an
    AttributeError that reads as a gate bug.

    The message names the type and NO PART of the value. The stale argument is the whole
    rendered bundle -- the user's baseline CV verbatim plus every entry's company, title,
    metrics and body -- and cv/engine.py:789 logs a failed run with %s, so an
    interpolated argument writes the user's CV source corpus into a log file. Asserting
    the absence is the load-bearing half: a `{sources}` spelling contains no `repr` and
    satisfies a naive "never repr" rule while leaking identically (a NamedTuple's str()
    IS its repr()).
    """
    stale = "=== BASELINE CV ===\nJane Roe, 12 years, secret-contact-line\n"
    with pytest.raises(TypeError) as ei:
        validate("PROFILE\nI build.\n", stale)
    assert "bundle_sources" in str(ei.value)
    assert "str" in str(ei.value)
    assert "secret-contact-line" not in str(ei.value)
    assert "Jane Roe" not in str(ei.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py::test_a_stale_text_caller_fails_loudly_without_echoing_the_bundle -v`
Expected: FAIL — `DID NOT RAISE TypeError` (today a `str` is parsed happily).

- [ ] **Step 3: Rewrite `validate` and delete the parser**

In `sluice/cv/validate.py`: add `from sluice.cv.bundle import BundleSources` at the top (`bundle.py` imports nothing from this module, so there is no cycle). Delete `_SECTION_RE` (`:14-22`), `_ID_RE` (`:24-38`) and `_bundle_ids_and_nums` (`:52-77`) with their comment blocks.

Rewrite the `_CITE_RE` comment, which currently explains itself by contrast with the deleted `_ID_RE`:

```python
# The PROFILE is prose, not bullets, so its citation strip must match what the
# RENDERER delivers, not the WORK-bullet strip. render.strip_citations removes only
# id-shaped [XX9] codes (render._CITE_RE), so a NON-id bracket like [500] SURVIVES
# into the PDF and the profile check must see and check it. This pattern is
# byte-identical to render._CITE_RE (render.py:10); test_profile_strip_matches_render_
# citation_shape pins that equality, because a comment cannot enforce it and a drift
# silently reopens a fabricated-number-ships fail-open.
#
# This is now the ONLY regex in this module that touches a citation code. Until #174
# there was a second one, `_ID_RE`, which parsed the bundle text to decide which ids
# existed -- and could therefore be fooled by a line of user free text. Ids now arrive
# structurally in `BundleSources`. This one is unrelated to that: it mirrors render's
# LENIENT strip of whatever the MODEL emitted ([A-Za-z]), not any generated code. (#30)
_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")
```

Then the signature:

```python
def validate(cv_text, sources, employers=None, fabrication_decoys=None):
    if not isinstance(sources, BundleSources):
        # Fail loudly at construction. The old second parameter was the rendered bundle
        # TEXT; the position is unchanged, so a stale caller would otherwise reach
        # `sources.nums` and raise AttributeError from inside the gate, which reads as a
        # gate bug rather than a call-site one.
        #
        # The type ONLY, never the value: the stale argument is the user's whole CV source
        # corpus and cv/engine.py:789 logs this exception with %s.
        raise TypeError(
            f"validate() takes a BundleSources, not {type(sources).__name__} -- build it "
            "with cv.bundle.bundle_sources(bundle)")
    v = []
    ids, nums, baseline = sources.ids, sources.nums, sources.baseline
    ...  # the rest of the body is unchanged
```

The `ids, nums, baseline = ...` unpack keeps every downstream line (`c not in ids`,
`nums[c]`, `baseline.union(...)`) byte-identical, so this task changes the source of the
values and nothing about how they are used.

- [ ] **Step 4: Migrate this file's two bundle constructions**

`tests/test_cv_validate.py:31` (`BUNDLE`) and `:149` (`_bundle()`) are the only two — every
one of the file's 31 `validate(...)` calls routes through one of them. Change
`render_bundle(build_bundle(...))` to `bundle_sources(build_bundle(...))` in both, and update
the import on `:6`. Also rewrite `:16-22`'s comment, whose "validate() had never once been
exercised against the text render_bundle actually produces" rationale stops being true, and
`:70`'s, which names the deleted `_bundle_ids_and_nums`.

- [ ] **Step 5: Run this file's tests**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py -v`
Expected: all PASS except the two characterisation tests, which now fail — that is Task 5.

- [ ] **Step 6: Witness the TypeError guard**

Mutant: DELETE the `if not isinstance(...)` block (both lines).
Run: `.venv/bin/python -m pytest tests/test_cv_validate.py::test_a_stale_text_caller_fails_loudly_without_echoing_the_bundle -v`
Expected: FAIL with `AttributeError` rather than `TypeError`. Restore.

Second mutant, for the leak half: change the message to `f"... not {sources}"`.
Expected: FAIL on the `secret-contact-line` assertion. This is the spelling that a naive
"never `repr`" rule would have permitted — witness against THIS one, not `{sources!r}`.
Restore.

- [ ] **Step 7: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_validate.py
git commit -m "fix(cv): hand validate() its source set instead of the bundle text (#174)"
```

---

### Task 5: Settle the existing tests the change retires or flips

The suite is red between Task 4 and here. This task closes it.

**Files:**
- Modify: `tests/test_cv_validate.py`

**Interfaces:** none new.

- [ ] **Step 1: Flip the two characterisation tests**

Both were written to go red on exactly this change. `:233
test_an_id_shaped_bracket_in_free_text_is_still_a_citable_id` becomes
`test_an_id_shaped_bracket_in_free_text_is_not_a_citable_id`, asserting `BAD CITATION`.
`:245 test_an_id_shaped_line_in_a_later_body_shadows_the_real_entry` becomes
`..._no_longer_shadows_the_real_entry`, asserting that the genuine metric survives AND the
fabricated one is refused. Rewrite both comments to say the residual is closed and how, citing
#174 — do not leave a comment describing a bound that no longer exists.

- [ ] **Step 2: Delete the two subsumed tests**

`:200 test_a_setext_underline_in_a_body_does_not_end_the_entry` and `:226
test_a_bracket_led_body_lines_numbers_join_the_enclosing_entry` become exact duplicates of
`:191 test_a_body_sourced_number_stays_permitted` under the new derivation — their only
deletion mutant is "drop `body` from `_entry_block`", which six siblings already kill. Delete
both. A permanently-green test is how the next person deletes a real guard.

- [ ] **Step 3: Correct the two KEEP comments**

`:217 test_a_bracket_led_body_line_is_not_a_citable_id` — KEEP, and its comment must NOT
claim it has no deletion mutant. Measured: deleting `validate.py`'s BAD-CITATION arm turns it
RED, and it is the ONLY test in the repo asserting `BAD CITATION`. Its comment says it now
holds by construction for the id half AND remains the sole guard on that arm.

`:182 test_negatives_block_does_not_widen_the_last_entrys_allowlist` — KEEP. Its comment says:
no mutant SPECIFIC to the negatives exclusion, because re-widening needs an ADD-shaped mutant
which the witness rule forbids; it still goes red on deletion of the INVENTED-METRIC arm.

- [ ] **Step 4: Run the file and then the suite**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py -v`
Expected: PASS.
Run: `.venv/bin/python -m pytest`
Expected: still red in `tests/test_cv_engine.py`, `tests/test_cv_parse.py`,
`tests/test_onboard_questions.py` — Task 6.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv_validate.py
git commit -m "test(cv): close the id-shadowing characterisations #174 retires"
```

---

### Task 6: Migrate the remaining call sites and wire the engine

**Files:**
- Modify: `sluice/cv/engine.py:288-289`, `:346`
- Modify: `tests/test_cv_engine.py:383`, `:446`, `:655`, `:1902` and `:197`
- Modify: `tests/test_cv_parse.py::_gate_verdict`
- Modify: `tests/test_onboard_questions.py:258-259`

**Interfaces:**
- Consumes: `bundle_sources` (Task 3), `validate` (Task 4).

- [ ] **Step 1: Write the failing end-to-end test**

```python
# tests/test_cv_engine.py -- append
def test_a_poisoned_entry_body_cannot_launder_a_fabricated_figure_through_the_gate():
    """#174, pinned where the user experiences it rather than only at validate().

    An entry body whose first line is shaped like ANOTHER entry's code used to rebind that
    entry's permitted numbers, because the gate recovered ids by parsing the rendered
    bundle. Both directions went wrong at once: the fabricated figure cleared the HARD gate
    with zero violations, and the poisoned entry's own genuine metric was reported INVENTED.
    """
    entries = [
        {"company": "Example Foundry", "title": "EM", "metrics": "12",
         "best_for": "", "category": "", "body": ""},
        {"company": "Example Foundry", "title": "Lead", "metrics": "7",
         "best_for": "", "category": "", "body": "[EF1] fabricated 4200 units"},
    ]
    sources = bundle_sources(build_bundle(
        entries=entries, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
    cv = CLEAN_CV.replace("- Grew team from 3 to 8 [EF1]",
                          "- Delivered 4200 units [EF1]")
    assert "4200" in cv, "the replace no-opped"
    assert any("INVENTED METRIC" in v for v in validate(cv, sources))
    # ...and the poisoned entry's real metric is still its own.
    assert "12" in sources.nums["EF1"]
```

Adjust the `CLEAN_CV.replace(...)` anchor to a real bullet in that fixture — assert the
replace landed, as the surrounding tests already do.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -k poisoned_entry_body -v`
Expected: FAIL — before the fix the fabricated figure is permitted, so no `INVENTED METRIC`.
(If Tasks 3-4 are already committed it will PASS; in that case witness it in Step 5 instead
of failing here, and say so in the commit message.)

- [ ] **Step 3: Wire the engine**

`sluice/cv/engine.py`, directly after `:288`:

```python
        bundle_text = _bundle.render_bundle(b)
        # Bound HERE, beside `bundle_text` and BEFORE the retry loop, not inlined at the
        # `_validate` call: both are derived from the same `b`, and adjacency is what stops
        # a later edit rebuilding one from a different bundle and leaving the other stale.
        # Before the loop because `bundle_sources` raises on a malformed bundle, and a
        # fault knowable here must not cost an LLM compose first.
        sources = _bundle.bundle_sources(b)
```

and at `:346`, `bundle_text` becomes `sources`:

```python
            violations = _validate(cv_text, sources, employers=cvcfg.employers,
                                   fabrication_decoys=cvcfg.fabrication_decoys)
```

`bundle_text` stays live — `compose` at `:320` and `run_audit` at `:647` both still take it.

- [ ] **Step 4: Migrate the remaining test call sites**

All five `tests/test_cv_engine.py` constructions are identical in shape:

```python
    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
```

becomes

```python
    sources = bundle_sources(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
```

with the local renamed at its `validate(...)` use. Update the import.

`tests/test_cv_parse.py::_gate_verdict` — same substitution; its docstring already says it
uses the repo's real gate, which stays true.

`tests/test_onboard_questions.py:258-259` construct no bundle and pass `""` twice. They become
`BundleSources({}, frozenset())` — an explicitly empty source set, which is what that test
means (it exercises only the employer-completeness gate). Import `BundleSources` beside the
existing `validate` import inside the test function.

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest`
Expected: PASS, all of it.
Run: `.venv/bin/ruff check sluice tests scripts`
Expected: clean.

- [ ] **Step 6: Witness the engine wiring**

Mutant: in `sluice/cv/engine.py`, MOVE the `sources = _bundle.bundle_sources(b)` line to
inside the `for _ in range(2)` loop, after the `compose` call.
Expected: the suite stays green — this mutant is NOT caught by a test, and that is honest:
the ordering is a cost argument (no LLM spend before a knowable fault), not a behaviour
difference. Note it in the commit message rather than inventing a test that pins line order.
Restore.

- [ ] **Step 7: Commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py tests/test_cv_parse.py tests/test_onboard_questions.py
git commit -m "fix(cv): wire the engine to the structured source set (#174)"
```

---

### Task 7: The three behaviour changes, pinned

Regression guards against re-introduction. None has a deletion mutant — re-introducing any of
these exclusions needs an ADD-shaped mutant, which the witness rule forbids — so each says so
in its own comment, or a later mutation round reads survival as inertness.

**Files:**
- Modify: `tests/test_cv_validate.py`

- [ ] **Step 1: Write the three tests**

```python
def test_a_section_shaped_body_line_no_longer_strands_the_entrys_numbers():
    """Change 2. `_SECTION_RE` existed only to keep the negatives block off the last
    entry, and it took a genuine `=== X ===` line in an entry BODY with it: measured, the
    user's own verified figure below was reported INVENTED, costing the single retry and
    potentially the lead. The derivation has no positional parse, so the whole body counts.

    No deletion mutant: re-introducing the stranding needs a line ADDED to
    `_entry_block`/`bundle_sources`, and the witness rule forbids ADD-shaped mutants. This
    is a regression guard, not an inert test.
    """
    e = [dict(_ENTRIES[0], body="Highlights\n=== Detail ===\nCut latency to 250 ms"),
         _ENTRIES[1]]
    assert validate(_work_cv("- Cut latency to 250 ms [ES1]"), _bundle(entries=e)) == []


def test_an_id_shaped_baseline_line_no_longer_mints_a_citable_entry():
    """The second live instance of #174's class, found while scoping it and not named in
    the issue: the baseline pool accumulated only while no id had been seen, so an
    `[XX9]`-shaped line anywhere in the baseline CV minted a fully citable entry. A bullet
    citing it then carried a fabricated figure under a fabricated citation, gate-clean.

    No deletion mutant -- see the sibling above.
    """
    b = _bundle(baseline="Career summary.\n[ZZ9] stray line with 4200")
    v = validate(_work_cv("- Shipped 4200 units [ZZ9]"), b)
    assert any("BAD CITATION" in x for x in v), v


def test_at_zero_entries_the_negatives_no_longer_reach_the_profile_pool():
    """The third live instance, and it runs the other way -- a NARROWING.

    With no entries `seen_id` never set, so the NEGATIVE CONSTRAINTS block fell through
    into the baseline arm and its do-not-say figures became profile-permitted. Measured:
    gate-clean today, a violation after. Zero entries is reachable on any install before
    the user has written an Experience Library entry (core/vault.py:1225-1229).

    No deletion mutant: the derivation never reads `bundle["negatives"]` at all, so
    re-introducing this needs an ADD.
    """
    b = _bundle(entries=[], negatives=["never claim 500 users"])
    v = validate(_cv_with_profile("I scaled to 500 users.", "- Ran things"), b)
    assert any("INVENTED PROFILE METRIC" in x for x in v), v
```

The two PROFILE widenings from the design's change 3 are covered by the existing
`test_profile_number_from_baseline_is_permitted`, which already asserts the pool by value;
extend its comment to name them rather than adding a fourth test with no distinct mutant.

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py -k "no_longer" -v`
Expected: PASS (the behaviour landed in Tasks 3-4; these pin it).

- [ ] **Step 3: Confirm each would have failed BEFORE the change**

```bash
git stash push -u -m "wip-174-task7-check"
git stash list --format='%H %gs'   # capture YOUR entry's SHA
```

Check out `origin/main`'s `sluice/cv/` into a scratchpad copy and run the three bodies
against the OLD `validate` by hand — do not use bare `git stash`/`git stash pop`, the stack is
shared with other worktrees. Simpler and preferred: run the three scenarios against the old
implementation in `scratchpad/` using the `_oracle` transcription from Task 3, which is the
old parser. Each must produce the pre-change verdict documented in the design's change table.

Restore with `git stash apply <sha>` then drop the entry by re-finding it by tag.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cv_validate.py
git commit -m "test(cv): pin the three fabrication-gate holes #174 closes"
```

---

### Task 8: Derive the neutrality module set

The new work lands in `sluice/cv/bundle.py`, whose tests live in `tests/test_cv_bundle.py` —
a CV module that no neutrality control reaches. Extending the hand-list closes the instance
and leaves the class: 13 `test_cv_*.py` modules exist and 6 are listed, and this would be the
second per-instance patch to the same tuple.

**Files:**
- Modify: `tests/test_fixture_name_neutrality.py:1315-1322` (`_CV_TEST_MODULES`), `:1331-1339` (`_cv_fixture_identities`), `:169` (`_REVIEWED_FIXTURE_IDENTITIES`)

- [ ] **Step 1: Write the failing scope test**

```python
def test_the_cv_module_set_is_derived_and_not_hand_listed():
    """A hand-list is only safe while somebody remembers it, and twice now nobody did:
    `test_slop_phrase_retirement.py` at #181, and `test_cv_bundle.py` at #174 -- the very
    module the second one put new fixtures in. Deriving the set closes the class.

    Asserts the SCOPE, not the result: a glob that matches nothing satisfies every
    assertion over it, and for a negative guard like this one finding nothing IS the
    success case, so the count is the only thing that can catch a broken sweep.
    """
    assert len(_CV_TEST_MODULES) >= 13
    assert "test_cv_bundle.py" in _CV_TEST_MODULES
    for extra in _CV_MODULES_NOT_MATCHING_THE_CONVENTION:
        assert extra in _CV_TEST_MODULES, extra
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -k module_set_is_derived -v`
Expected: FAIL — `NameError` on `_CV_MODULES_NOT_MATCHING_THE_CONVENTION`.

- [ ] **Step 3: Derive the set**

```python
# CV-domain modules whose FILENAME does not start `test_cv_`, so the glob below cannot
# find them. Hand-listed of necessity -- but this is now the only hand-list, and it holds
# the exceptions rather than the rule, which is the part that kept going stale.
_CV_MODULES_NOT_MATCHING_THE_CONVENTION = (
    "test_slop_phrase_retirement.py",     # #181
    "test_renderer_template.py",          # CV-body employer/education identities
    "test_onboard_questions.py",          # an employer fixture probing the gate
)

# Derived, not enumerated (#174). Twice a CV module was added and nobody remembered to
# list it, the second time being the module this very change put new fixtures in.
_CV_TEST_MODULES = tuple(sorted(
    {p.name for p in _TESTS_DIR.glob("test_cv_*.py")}
    | set(_CV_MODULES_NOT_MATCHING_THE_CONVENTION)))
```

`_cv_fixture_identities` already skips a missing path, so it needs no change.

- [ ] **Step 4: Run the neutrality file and read what goes red**

Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -v`
Expected: `test_cv_body_identities_are_on_the_reviewed_roster` FAILS naming exactly
`['Example Alpha', 'Example Sans']`. Any OTHER value means a new identity arrived since this
plan was written — STOP and escalate it to the owner neutrally ("is this real or invented?"),
never lead, and never write a suspected-real value anywhere new.

- [ ] **Step 5: Record the owner's two rulings**

Both were ruled INVENTED by the repo owner on 2026-08-24.

Add `"Example Alpha"` to `_REVIEWED_FIXTURE_IDENTITIES` — a placeholder employer in
`tests/test_onboard_questions.py:257`, same `Example <Word>` construction as the roster's
existing entries.

`Example Sans` is a TYPEFACE in a `@font-face` fixture
(`tests/test_renderer_template.py:420`), not a lead identity, so it is exempted by name
rather than rostered — putting a font family on a roster documented as being about employers
would quietly widen what that roster means:

```python
# `_REVIEWED_FIXTURE_IDENTITIES` is about LEAD identities -- employers a fixture names.
# `Example Sans` is a font FAMILY in a @font-face fixture, beside genuine faces like
# "DejaVu Sans"; it names no firm, and rostering it would make the roster mean something
# wider than it says. Exempted by name, not by pattern, so a real employer that happened
# to end in "Sans" would still force the human call. Same shape as CLAUDE.md's cairo/pango
# carve-out from the place-name sweep. (Owner's ruling, 2026-08-24.)
_CV_IDENTITY_EXEMPT = frozenset({"Example Sans"})
```

and subtract it in `test_cv_body_identities_are_on_the_reviewed_roster`:

```python
    unreviewed = sorted(_cv_fixture_identities()
                        - _REVIEWED_FIXTURE_IDENTITIES - _CV_IDENTITY_EXEMPT)
```

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest`
Expected: PASS.
Run: `.venv/bin/ruff check sluice tests scripts`
Expected: clean.

- [ ] **Step 7: Witness the scope assertion**

Mutant: in `_CV_TEST_MODULES`, change the glob to `test_cv_bundle.py` only (a MOVE to a
narrower value, not an added line).
Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -k module_set_is_derived -v`
Expected: FAIL on the `>= 13` assertion. Restore.

- [ ] **Step 8: Commit**

```bash
git add tests/test_fixture_name_neutrality.py
git commit -m "test(neutrality): derive the CV module set instead of hand-listing it"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (the cv paragraph, ~`:380-432`)
- Modify: `.rulesync/rules/CLAUDE.md` (the CV-fabrication-gate paragraph, ~`:401-437`)

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

One sentence in the cv paragraph, after the HARD-tier description: the gate is HANDED its
source set (`cv/bundle.py`'s `bundle_sources`, derived from `build_bundle`'s structured
entries) rather than recovering it by re-parsing the rendered bundle, so no line of user free
text can mint or rebind a citable id (#174). Name the three holes that closes.

- [ ] **Step 2: Update `.rulesync/rules/CLAUDE.md`**

Same sentence, in the "The CV fabrication gate is hard" paragraph. Assert nothing that is not
now true in the tree — this file is the highest-leverage place in the repo to state something
false, since every future agent reads it.

- [ ] **Step 3: Regenerate**

Run: `npm ci --ignore-scripts && npm run rulesync`
Expected: `CLAUDE.md`, `AGENTS.md` and `.claude/` regenerate. `--ignore-scripts` is not
optional — one package in the pinned tree declares a postinstall, and CI takes the same path.

- [ ] **Step 4: Confirm no drift**

Run: `.venv/bin/python -m pytest tests/test_docs_claims.py tests/test_no_leaked_files.py -v`
Expected: PASS.
Run: `git status --short`
Expected: `CLAUDE.md`/`AGENTS.md` are gitignored and must NOT appear as tracked changes.

- [ ] **Step 5: Commit**

```bash
git add docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "docs(cv): state that the gate is handed its source set (#174)"
```

---

### Task 10: Final verification

- [ ] **Step 1: Full suite from a clean cache**

```bash
find . -name __pycache__ -type d -prune -exec rm -rf {} +
.venv/bin/python -m pytest
```
Expected: PASS. A stale `__pycache__` produces phantom failures in this repo; clearing it
before believing any result is standing practice.

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check sluice tests scripts`
Expected: clean.

- [ ] **Step 3: Coverage, as CI runs it**

Run: `.venv/bin/python -m pytest --cov`
Expected: a report. It does not gate; read the `cv/bundle.py` and `cv/validate.py` rows and
confirm the new branches are covered.

- [ ] **Step 4: Confirm the parser is really gone**

```bash
grep -rn "_bundle_ids_and_nums\|_SECTION_RE\|_ID_RE" sluice/ tests/
```
Expected: **no hit is live code** — no definition, no call, no reference that executes. Do NOT
expect a particular COUNT of hits, and do not expect `tests/` to hold only one. Every name here
appears in prose that explains why the parser was deleted, and prose about a deletion naturally
multiplies as more places come to describe it: the surviving `_CITE_RE` comment in
`cv/validate.py` contrasts itself against the deleted `_ID_RE`; `core/vault.py`'s `_ID_SHAPED`
records that it used to mirror it; several tests in `tests/test_cv_validate.py` name `_SECTION_RE`
when stating which mutant they do and do not kill; `tests/test_cv_bundle.py` carries the `_oracle`
transcription; and `tests/test_evidence_store.py` explains what its drift pin used to compare
against.

Read each hit and confirm it is a comment or docstring. One hit is not about this parser at all:
`tests/harness/backend.py`'s `_DOSSIER_ID_RE` is an unrelated identifier the pattern matches as a
substring.

Stated as a shape rather than a count deliberately. An earlier draft of this step said "no hits in
`sluice/`, and in `tests/` the only hit is the `_oracle`", which was true when written and false by
the time the branch merged — the rebase onto #164 and the review-response round each added an
honest historical mention. A verification step that reports a failure which is not one is worse
than no step.

- [ ] **Step 5: Confirm the stdlib rule holds**

```bash
grep -n "^import\|^from" sluice/cv/bundle.py sluice/cv/validate.py
```
Expected: `re`, `typing.NamedTuple`, and `sluice.cv.bundle` in `validate.py`. Nothing else.

- [ ] **Step 6: Push and request review**

```bash
git push
```
Then run `/review-pr` BEFORE spending a CodeRabbit slot — the local specialist team is free
and parallel, CodeRabbit refills at roughly one review per hour and is dismissed on every push.

---

## Self-Review

**Spec coverage.** D1 → Tasks 2-3. D2 → Tasks 4, 6. D3 → Task 3. D4 → Task 3. D5 → Task 6
(`bundle_text` stays for compose/audit). D6/D7 are rejected alternatives, no task needed.
D8 → Task 2 (`_baseline_block` holds no header). D9 → Task 3 (the length slice, pinned by
mutant 4). D10 → Tasks 1 and 3 (the frozen literal and its two assertions). Change 1 → Task 6.
Changes 2-4 → Task 7. The per-test verdict table → Task 5. Neutrality → Task 8. Docs → Task 9.

**Two spec items deliberately NOT given their own task**, both recorded here so the omission
is visible rather than silent:

- The accepted residual and its baseline twin (a citation token's own digits counting as
  source). The spec says a characterisation test carrying the go-RED comment should replace
  the two being retired in Task 5. Fold it into Task 5 Step 1 as a third test if the executor
  wants it; it has no deletion mutant either way.
- The `_CITE_RE` comment rewrite is inside Task 4 Step 3 rather than a separate step.

**Placeholder scan.** One `<paste the captured text here>` in Task 1 Step 3 and one
`<PRE-REFACTOR-SHA>` in Task 3 Step 1 — both are values that can only be produced by running
the preceding step, and both name the command that produces them. No other placeholders.

**Type consistency.** `bundle_sources(bundle: dict) -> BundleSources` in Task 3 is what Tasks
4, 6 and 7 call. `BundleSources(nums, baseline)` positional construction in Task 3 matches
`BundleSources({}, frozenset())` in Task 6. `_entry_block(entry) -> list[str]` in Task 2
matches its use in Task 3. `validate(cv_text, sources, ...)` in Task 4 matches every call site
in Tasks 5-7.
