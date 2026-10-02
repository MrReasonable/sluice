# Unbundled-Term Check Implementation Plan

> **Status: implemented in PR #366.** Like every plan under `docs/superpowers/`, this is a
> historical design record and the code wins on any disagreement. Review rounds changed the
> shipped behaviour in these places, which the task text below predates:
> - `mention_vocab` subtracts only each negative's own CANDIDATE tokens
>   (`cv/terms.py::candidates`), never every word of it.
> - A bare `+` or `#` is not a candidate; arm (ii) needs a letter in the token.
> - Term findings live in their own `CvResult.terms` field (cv run, run.json, MCP `cv_run`),
>   not inside `slop`.
> - Retention also refuses to let a retry whose voice check failed displace a retained
>   draft whose voice was measured.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a deterministic STYLE-tier check that reports a term named in PROFILE prose or a
WORK bullet that appears nowhere in what the composer was shown, driving the composer's one
retry without ever binning a lead (#194).

**Architecture:** two pure pieces feed the engine's existing STYLE plumbing.
`cv/bundle.py::mention_vocab(bundle)` builds a case-folded vocabulary from the bundle's
structure (baseline, entry blocks, entry skills, Skills Inventory framing, minus the
negatives). `cv/terms.py::unbundled_terms(lines, vocab)` reports shape-bearing tokens absent
from it. `cv/engine.py` calls both beside the slop check and keeps the term findings in their
own list, so the sign-off hold tags them `term\t`. A separate fix makes the retry loop keep
the hard-clean draft with FEWER style findings rather than the last one.

**Tech Stack:** Python 3.12+ stdlib, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-02-unbundled-term-design.md`. Read it before
starting; this plan argues from it.

## Global Constraints

- **Purity.** `sluice/` stays standard-library only. `cv/terms.py` and `mention_vocab` do no
  I/O.
- **Untouched.** `BundleSources`, `bundle_sources` and `validate()` get NO new field or
  parameter (spec §3.3).
- **Never binning.** A STYLE finding never bins a lead. The lead is skipped only if no
  attempt ever clears the HARD tier.
- **Synthetic tokens.** Every test token is invented and `Example`-shaped. No real product,
  employer, place or CV content enters `sluice/`, `tests/`, a commit or the PR body. No token
  from the spec's §5 measurement enters anything.
- **No line numbers.** Never cite a line number in a comment or docstring
  (`tests/test_citation_drift.py`). Cite `file.py::symbol`.
- **No counts in prose.** Do not write a count of members, tests, files or sites in any comment,
  docstring, doc or commit body.
- **Verification commands.**
  - Tests: `.venv/bin/python -m pytest` (whole suite, offline).
  - Lint: `.venv/bin/ruff check sluice tests scripts`.
  - Run both from the branch's worktree root, referred to below as `$WORKTREE`.
- **Commit type for the feature.** Tasks 3–8 land as ONE `feat(cv)` commit after autosquash.
  - Task 3 makes the target commit.
  - Tasks 4–8 commit as `fixup! feat(cv): report terms CV prose names but no evidence carries (#194)`.
  - Task 1 is `test(cv)` and Task 2 is `fix(cv)`. Both stay separate.
- **Commit trailer.** Every commit message ends with the trailer line
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.

## Review Focus

These are inputs the spec implies that no task's tests would otherwise exercise. Each has a test
in the task named.

1. **A draft missing its `WORK EXPERIENCE` header.** `section_spans` then reads the whole
   body as PROFILE, so headers and company words become candidates.
   - Expected: the lead is still `skipped-gate` or retried for the HARD reason, and the HARD
     violation comes FIRST in the retry prompt, ahead of any term finding.
   - Test: Task 6, `test_a_headerless_draft_puts_the_hard_violation_first`.
2. **A hyphenated or possessive form of a bundled name.** `Example-based`, `Exampleworks's`.
   `_WORD_RE` splits on `-` and `'`.
   - Expected: no finding when the stem is in the vocabulary.
   - Test: Task 4, `test_hyphen_and_possessive_forms_of_a_bundled_name_are_quiet`.
3. **The same unbundled term on two lines, and twice on one line.**
   - Expected: one finding per line, never two for the same line.
   - Test: Task 4, `test_a_term_is_reported_once_per_line`.
4. **An empty vocabulary** (a bundle with an empty baseline and no entries).
   - Expected: accepted, and every candidate reported. It is not a `TypeError`, because an
     empty frozenset is the right shape.
   - Test: Task 4, `test_an_empty_vocabulary_is_a_valid_shape_and_reports_every_candidate`.
5. **A non-ASCII name** (`Exämple`). `_WORD_RE` is ASCII-only and fragments it into `Ex` +
   `mple`.
   - Expected: no finding when the same name is in the bundle, because both sides fragment
     identically.
   - Test: Task 4, `test_a_non_ascii_name_in_the_bundle_is_quiet`.

---

## File Structure

- **Create `sluice/cv/terms.py`.** Pure. `candidates(line) -> list[str]`, and
  `unbundled_terms(lines, vocab) -> list[tuple[int, str, str]]`.
- **Modify `sluice/cv/bundle.py`.**
  - Add `mention_vocab(bundle) -> frozenset[str]`.
  - Reword the `_framing_lines` docstring.
  - Reword the never-touches-`skills` statement in the `bundle_sources` docstring.
- **Modify `sluice/cv/config.py`.** Add the `term_check: bool = True` field.
- **Modify `sluice/cv/engine.py`.**
  - Bind the vocabulary.
  - Split `style_msgs` into `slop_msgs` + `term_msgs`.
  - Change `best` to a 4-tuple.
  - Apply the retention rule.
  - Tag holds `term\t`.
  - Reword the `CvResult.slop` comment.
- **Modify `sluice/cli.py`.** In `_print_signoff_claims`, print a `term\t` heading and reword
  the slop comment.
- **Modify `sluice/mcpserver.py`.** Reword the `slop` comments.
- **Modify the config docs.** In `sluice.yaml.example`, add `term_check`, shipped commented.
  In `docs/CONFIGURATION.md`, add the `term_check` row.
- **Modify the prose docs.** `docs/ARCHITECTURE.md` and `.rulesync/rules/CLAUDE.md`: the
  STYLE-tier, retention and framing sentences.
- **Tests.**
  - Create `tests/test_cv_terms.py` and `tests/test_cv_mention_vocab.py`.
  - Modify `tests/test_cv_engine.py`, `tests/test_cv_signoff_prompt.py`,
    `tests/test_cv_config.py` and `tests/test_sluice_neutral_defaults.py`.

---

### Task 1: Make the shared engine fixture's bundle carry its own `CI`

`tests/test_cv_engine.py::CLEAN_CV` has a bullet `- CI [EF1]`, and nothing in `ENTRIES` or
the `"BASELINE"` baseline says CI. Once the check is wired, every test that composes
`CLEAN_CV` gets an unbundled-term retry. In review, that both failed tests outright and masked
the slop retry in eight others (spec §6). This task fixes the premise before anything can
fire.

**Files:**
- Modify: `tests/test_cv_engine.py`, the `ENTRIES` literal.

**Interfaces:**
- Produces: `ENTRIES[0]["body"] == "Grew 3 to 8 with CI."`. It carries the same digits as
  before (`3`, `8`), so no allowlist changes.

- [ ] **Step 1: Create the worktree venv**

```bash
cd "$WORKTREE"
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -e ".[test]" ruff==0.15.21
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Expected: install completes. `.venv/bin/python -c "import sluice, sys; print(sluice.__file__)"`
prints a path under `sluice-194`.

- [ ] **Step 2: Baseline the suite**

Run: `.venv/bin/python -m pytest -q -x -p no:cacheprovider 2>&1 | tail -3`
Expected: all pass. Record the pass count in your scratch notes, not in any file.

- [ ] **Step 3: Edit the fixture**

In `tests/test_cv_engine.py`, change the `ENTRIES` literal's body and add the comment:

```python
# `CI` in the body is load-bearing (#194): CLEAN_CV's `- CI [EF1]` bullet names it, and the
# unbundled-term check reports a capitalised term the bundle never carries. Without it every
# test composing CLEAN_CV would get a retry it does not credit, which in review MASKED the
# slop-driven retry eight tests exist to witness. No digit added, so no allowlist moves.
ENTRIES = [{"title": "Grew team", "company": "Example Foundry", "best_for": "delivery",
            "category": "people", "metrics": "3 8", "body": "Grew 3 to 8 with CI."}]
```

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3`
Expected: the same pass count as Step 2, all green.

If a frozen-prompt test that imports `ENTRIES` goes red, update that frozen literal ONLY by
adding ` with CI` where the body appears, and read the diff first. Run
`grep -rn "Grew 3 to 8" tests` to find every such literal.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv_engine.py
git commit -m "test(cv): let the shared engine fixture's bundle carry the term its CV names

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: Retain the hard-clean draft with fewer style findings, not the last one

Spec §2.3. Today `best` is reassigned on every hard-clean attempt, so a hard-clean retry
carrying MORE style findings replaces a cleaner first draft.

**Files:**
- Modify: `sluice/cv/engine.py`, the retry loop's `best = (cv_text, style_msgs, voice_flags)`
  assignment and its `record.retained(attempt)`.
- Test: `tests/test_cv_engine.py`, two new drafts and two tests beside
  `test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty`.

**Interfaces:**
- Consumes: `_run_sequence(monkeypatch, drafts)`, `_DRAFTS`, `STYLE_DIRTY_CV` and
  `FakeRenderer.rendered` (all existing).
- Produces:
  - **New drafts.** `STYLE_DIRTIER_CV` carries two slop findings. `STYLE_DIRTY_B_CV` carries
    one finding, different text from `STYLE_DIRTY_CV`.
  - **New `_DRAFTS` keys.** `"hard-clean-style-dirtier"` and `"hard-clean-style-dirty-b"`.
  - **Engine rule.** `best` is replaced only when the new finding count is ≤ the retained
    one.

- [ ] **Step 1: Add the fixtures and their premise rows**

In `tests/test_cv_engine.py`, directly below `STYLE_DIRTY_CV`, add:

```python
# Two STYLE findings where STYLE_DIRTY_CV has one (#194 retention, spec §2.3): `leverage`
# and `seamless` are both slop._PHRASES stems, on the same PROFILE line. Hard-clean like
# its sibling -- only the prose changes.
STYLE_DIRTIER_CV = CLEAN_CV.replace(
    "I build reliable systems.",
    "I leverage seamless delivery patterns across teams.")

# ONE finding, like STYLE_DIRTY_CV, but different text -- so a tie between the two is
# observable in what renders.
STYLE_DIRTY_B_CV = CLEAN_CV.replace(
    "I build reliable systems.",
    "I foster the same delivery patterns across teams.")
```

Add `"hard-clean-style-dirtier": STYLE_DIRTIER_CV` and
`"hard-clean-style-dirty-b": STYLE_DIRTY_B_CV` to `_DRAFTS`.

In `test_the_sequence_fixtures_are_the_tiers_they_claim`'s row list, add:

```python
        ("hard-clean-style-dirtier", STYLE_DIRTIER_CV, False, True),
        ("hard-clean-style-dirty-b", STYLE_DIRTY_B_CV, False, True),
```

Then add a new test below it, pinning the finding COUNTS the retention tests depend on:

```python
def test_the_retention_fixtures_carry_the_finding_counts_they_claim():
    """PREMISE of the two retention tests below: a 'fewer findings' comparison means
    nothing unless the fixtures really differ in count, and a stem leaving
    slop._PHRASES would silently collapse them to a tie."""
    from sluice.cv.slop import check_phrases
    from sluice.cv.validate import section_spans

    def count(text):
        profile, work, _skills = section_spans(text)
        return len(check_phrases(profile + work))

    assert count(STYLE_DIRTY_CV) == 1
    assert count(STYLE_DIRTY_B_CV) == 1
    assert count(STYLE_DIRTIER_CV) == 2
```

- [ ] **Step 2: Write the failing retention tests**

Below `test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty`, add:

```python
def test_a_retry_with_MORE_style_findings_does_not_replace_a_cleaner_draft(monkeypatch):
    """#194, spec §2.3. Both drafts are hard-clean; attempt 2 is style-WORSE. The loop used
    to keep whichever hard-clean draft came LAST, so the dirtier retry shipped. With
    `style_hold` off (the default) nothing then flags it, and an unbundled-term finding is
    a probable invention -- so keeping the worse draft is the failure this rule removes."""
    res, be, rend = _run_sequence(
        monkeypatch, ["hard-clean-style-dirty", "hard-clean-style-dirtier"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2, "the style finding never reached the retry"
    assert rend.rendered == [STYLE_DIRTY_CV], "the cleaner attempt-1 draft must ship"
    # The audit reads the same retained draft the renderer got (the engine's rebind).
    assert be.audited == [STYLE_DIRTY_CV]


def test_a_tie_in_style_findings_keeps_the_later_draft(monkeypatch):
    """A tie keeps attempt 2: it was composed with attempt 1's findings in front of it,
    and keeping it is the pre-#194 behaviour, so a tie changes nothing."""
    res, _be, rend = _run_sequence(
        monkeypatch, ["hard-clean-style-dirty", "hard-clean-style-dirty-b"])
    assert res.status == "rendered"
    assert rend.rendered == [STYLE_DIRTY_B_CV]
```

- [ ] **Step 3: Run them to see the first fail**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -q -p no:cacheprovider -k "MORE_style_findings or tie_in_style or retention_fixtures or tiers_they_claim"`

Expected:
- `test_a_retry_with_MORE_style_findings_does_not_replace_a_cleaner_draft` FAILS, with
  `rend.rendered == [STYLE_DIRTIER_CV]`.
- The other three PASS.

- [ ] **Step 4: Implement the rule**

In `sluice/cv/engine.py`, replace the block from `best = (cv_text, style_msgs, voice_flags)`
down to `break`:

```python
                best = (cv_text, style_msgs, voice_flags)
                # Beside `best`, so the two cannot disagree about which attempt was kept.
                record.retained(attempt)
                if not style_msgs and not voice_flags:
                    break
```

with:

```python
                # Keep the hard-clean draft with FEWER style/voice findings, not merely the
                # LAST one (#194, spec §2.3). Reassigning on every hard-clean attempt let a
                # style-WORSE retry replace a cleaner attempt 1, and with `style_hold` off
                # nothing then flagged it. A TIE keeps the later draft: it was composed with
                # the earlier findings in front of it, and keeping it is the pre-#194
                # behaviour, so only a strictly worse retry is refused.
                found = len(style_msgs) + len(voice_flags)
                if best is None or found <= len(best[1]) + len(best[2]):
                    best = (cv_text, style_msgs, voice_flags)
                    # Inside the same condition, so `run.json`'s `retained_attempt` names
                    # the draft actually kept rather than the last one examined.
                    record.retained(attempt)
                if not style_msgs and not voice_flags:
                    break
```

Then update the comment block above `best = None` (the one beginning "The last attempt that
cleared the HARD gate"). Its first sentence becomes:

```python
        # The hard-clean attempt with the FEWEST style/voice findings (ties go to the later
        # one), as `(cv_text, style_msgs, voice_flags)`, or None if no attempt ever did.
```

Leave the rest of that comment as it is.

- [ ] **Step 5: Run the engine and artefact suites**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py tests/test_cv_run_artefacts.py -q -p no:cacheprovider`
Expected: all PASS, including the two new tests.

If a `test_cv_run_artefacts.py` row asserting `retained_attempt` now differs, read it. It is
correct to change ONLY if its sequence has a style-worse retry. Otherwise the implementation
is wrong.

- [ ] **Step 6: Mutation witness**

Commit first (Step 8 order is fine: commit, witness, amend nothing). Then make two
mutations, running the two new tests after each and restoring before the next.

1. Delete `found <= len(best[1]) + len(best[2])` together with the `or` before it, leaving
   `if best is None:`.
   - Expected: the tie test FAILS, because attempt 1 ships.
2. Change `<=` to `<` by deleting the `=` character.
   - Expected: the tie test FAILS, and the MORE test still passes.

Restore with `git checkout sluice/cv/engine.py` after each.

- [ ] **Step 7: Full suite + lint**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3 && .venv/bin/ruff check sluice tests scripts`
Expected: all green, `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "fix(cv): keep the hard-clean draft with fewer style findings, not the last one

A hard-clean retry carrying more style findings replaced a cleaner first draft,
and with style_hold off nothing flagged the swap. A tie still keeps the later draft.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

(Run Step 6 after this commit, restoring with `git checkout sluice/cv/engine.py`.)

---

### Task 3: `mention_vocab` — what the composer was shown, minus the negatives

Spec §3.3.

**Files:**
- Modify: `sluice/cv/bundle.py`. Add `mention_vocab` directly after `bundle_sources`, and
  reword the `_framing_lines` and `bundle_sources` docstrings.
- Create: `tests/test_cv_mention_vocab.py`.

**Interfaces:**
- Consumes: `build_bundle`, `_baseline_block`, `_entry_block`, `_entry_skills_line`,
  `_framing_lines` and `_WORD_RE` (existing, `cv/bundle.py`).
- Produces: `mention_vocab(bundle: dict) -> frozenset[str]`, case-folded `_WORD_RE` tokens.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_mention_vocab.py`:

```python
"""cv/bundle.py::mention_vocab (#194): the vocabulary the unbundled-term check RECOGNISES.

Spec: docs/superpowers/specs/2026-10-02-unbundled-term-design.md §3.3. One row per
source, never one row for all -- a single row over a bundle carrying every source
cannot tell which source suppressed the term.
"""
from sluice.cv.bundle import build_bundle, bundle_sources, mention_vocab
from sluice.cv.validate import validate


def _bundle(*, baseline="", entries=(), negatives=(), skills=()):
    return build_bundle(list(entries), baseline, list(negatives), [], {}, skills=list(skills))


def _entry(**kw):
    e = {"title": "", "company": "", "metrics": "", "body": "", "fields": {}}
    e.update(kw)
    return e


def test_the_baseline_is_in_the_vocabulary():
    assert "examplequery" in mention_vocab(_bundle(baseline="Ran ExampleQuery daily."))


def test_an_entry_heading_is_in_the_vocabulary():
    v = mention_vocab(_bundle(entries=[_entry(title="Built Examplebus", company="Example Co")]))
    assert "examplebus" in v


def test_an_entry_body_is_in_the_vocabulary():
    assert "examplestore" in mention_vocab(_bundle(entries=[_entry(body="Moved to Examplestore.")]))


def test_an_entry_skills_field_is_in_the_vocabulary():
    v = mention_vocab(_bundle(entries=[_entry(fields={"Skills": "Examplemesh"})]))
    assert "examplemesh" in v


def test_a_skills_inventory_note_is_in_the_vocabulary():
    """IN, though no HARD row licenses it (spec §3.3): this check asks whether the term was
    INVENTED, and a declared skill was not."""
    v = mention_vocab(_bundle(skills=[{"title": "Examplelang", "fields": {}, "body": ""}]))
    assert "examplelang" in v


def test_a_presentation_header_word_is_not_in_the_vocabulary():
    """`=== VERIFIED EXPERIENCE ENTRIES ... ===` is prompt scaffolding, not the user's words.
    `verified` appears in no source below, so its presence could only come from a header."""
    v = mention_vocab(_bundle(baseline="Ran things.", entries=[_entry(body="Did things.")]))
    assert "verified" not in v
    assert "baseline" not in v


def test_a_negative_term_is_subtracted_even_when_an_inventory_note_carries_it():
    """Subtracted by TERM, not merely kept out as a source (spec §3.3): a configured
    'never claim X' must report X however else the bundle came to mention it."""
    b = _bundle(skills=[{"title": "Examplelang", "fields": {}, "body": ""}],
                negatives=["never claim Examplelang"])
    assert "examplelang" not in mention_vocab(b)


def test_every_entry_skill_token_is_in_the_vocabulary():
    """The SUPERSET guard behind spec §3.4's disjointness: row 1 (MISATTRIBUTED SKILL)
    reports only terms in this set's skills subset, the unbundled-term check only terms
    outside the whole set, so no term can be reported by both."""
    entries = [_entry(company="Example A", fields={"Skills": "Example Widget, Examplemesh"}),
               _entry(company="Example B", fields={"Skills": "Examplebus"})]
    b = _bundle(entries=entries)
    v = mention_vocab(b)
    skill_tokens = {t.casefold() for es in bundle_sources(b).entries.values()
                    for s in es.skills for t in s.split()}
    assert skill_tokens, "the sweep enumerated no skill tokens at all"
    assert skill_tokens <= v


def test_the_vocabulary_cannot_widen_the_hard_gate():
    """SEPARATION (spec §3.3, review INV-1): mention_vocab is a STYLE-only pool and
    deliberately carries an inventory-only skill. Row 2 must still REFUSE that skill on a
    SKILLS line -- asserted on the discriminating message, not on emptiness, so a gate that
    started reading this pool would go red here."""
    b = _bundle(baseline="Ran things.",
                skills=[{"title": "Examplelang", "fields": {}, "body": ""}])
    assert "examplelang" in mention_vocab(b), "premise: the pool does carry the skill"
    cv = "\n".join(["Jane Roe", "PROFILE", "Ran things.", "WORK EXPERIENCE",
                    "SKILLS", "- Examplelang"])
    assert any(v.startswith("UNSOURCED SKILL 'Examplelang'")
               for v in validate(cv, bundle_sources(b)))


def test_a_non_ascii_name_tokenises_identically_on_both_sides():
    """Review Focus 5: `_WORD_RE` is ASCII-only, so `Exämple` fragments. The vocabulary
    must hold the same fragments the check will look up."""
    v = mention_vocab(_bundle(baseline="Worked at Exämple."))
    assert {"ex", "mple"} <= v
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_cv_mention_vocab.py -q -p no:cacheprovider`
Expected: collection ERROR, `ImportError: cannot import name 'mention_vocab'`.

- [ ] **Step 3: Implement**

In `sluice/cv/bundle.py`, directly after `bundle_sources`, add:

```python
def mention_vocab(bundle: dict) -> frozenset[str]:
    """Every case-folded token the composer was SHOWN as source or framing, minus the
    negatives: what the unbundled-term check (cv/terms.py, #194) RECOGNISES.

    A STYLE-tier pool, and deliberately NOT a `BundleSources` field. That type is the
    hard gate's licensing contract, and this set carries the two things the contract
    excludes -- each entry's heading line and the Skills Inventory framing -- because the
    question here is different: not "is this claim supported" (row 2, cv/audit.py) but "did
    the composer INVENT this term". A declared skill was not invented, and flagging it
    would make the only answer "delete a true skill". Keeping the pool off the licensing
    type is what stops a later HARD row reading it by accident
    (`tests/test_cv_mention_vocab.py::test_the_vocabulary_cannot_widen_the_hard_gate`).

    Built from STRUCTURE through the same per-section emitters the prompt uses, never by
    re-reading rendered text (#174), so a presentation header's words are not in it.

    The negatives are SUBTRACTED by term, not merely left out as a source: "never claim X"
    must report X even when an inventory note also names it. Subtracting an ordinary word
    ("never", "claim") changes nothing, because a lowercase word is never a candidate.

    The job description is not in it, on purpose: a JD is the likeliest place an invented
    term comes from.
    """
    lines = list(_baseline_block(bundle))
    for e in bundle["entries"]:
        lines += _entry_block(e) + _entry_skills_line(e)
    for s in bundle.get("skills", ()):
        lines += _framing_lines(s)
    words = {t.casefold() for line in lines for t in _WORD_RE.findall(line or "")}
    from sluice.cv.terms import candidates   # lazy: cv/terms.py imports this module
    banned = {t.casefold() for n in bundle["negatives"] for t in candidates(n)}
    return frozenset(words - banned)
```

In `_framing_lines`' docstring, replace the sentence beginning `Nothing harvests from here:`,
up to and including `which is what makes a skills figure licensed nowhere.`, with:

```text
    Nothing that LICENSES reads these lines: `bundle_sources` walks `bundle["entries"]` and
    never touches `bundle["skills"]`, which is what makes a skills figure licensed nowhere.
    `mention_vocab` (#194) does read them, to RECOGNISE a declared skill as not invented --
    a STYLE-tier question, kept off `BundleSources` so it cannot become a licence.
```

In `bundle_sources`' docstring, find the statement that the function never reads
`bundle["skills"]`, if one is present. Scope it with a trailing clause:

```text
(for LICENSING; `mention_vocab` reads it to recognise, never to license)
```

If no such sentence exists in that docstring, change nothing there. Confirm with:

```bash
grep -n "skills" sluice/cv/bundle.py
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cv_mention_vocab.py tests/test_cv_bundle.py tests/test_citation_drift.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/cv/bundle.py tests/test_cv_mention_vocab.py
git commit -m "feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses: one mutation at a time, running `tests/test_cv_mention_vocab.py`, and restoring
with `git checkout sluice/cv/bundle.py` after each.

| Mutation | Expected red |
|---|---|
| delete `+ _entry_skills_line(e)` | `test_an_entry_skills_field_is_in_the_vocabulary` and the superset row |
| delete the `for s in bundle.get("skills", ())` loop (both lines) | the inventory row and the separation premise |
| replace `words - banned` with `words` | the negative row |
| delete `_entry_block(e) + ` | the heading and body rows |

---

### Task 4: `cv/terms.py` — candidates and `unbundled_terms`

Spec §3.2, §3.4, §3.5.

**Files:**
- Create: `sluice/cv/terms.py`, `tests/test_cv_terms.py`.

**Interfaces:**
- Consumes: `cv/validate.py::_CITE_RE` and `cv/bundle.py::_WORD_RE` (the one tokeniser;
  `validate._tokens` is `_WORD_RE.findall`, and positions are needed here, so this uses
  `finditer` on the same regex). Also `mention_vocab` (Task 3), in the anti-vacuity test.
- Produces:
  - `candidates(line: str) -> list[str]`
  - `unbundled_terms(lines: list[tuple[int, str]], vocab: frozenset[str] | set[str]) -> list[tuple[int, str, str]]`,
    returning `(line no, term, snippet)` where the snippet is `line.strip()[:50]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_terms.py`:

```python
"""cv/terms.py (#194): the unbundled-term check's candidate rule and report.

Spec: docs/superpowers/specs/2026-10-02-unbundled-term-design.md §3.2-§3.4. Every arm has
a token ONLY that arm admits, so deleting the arm turns exactly its row red. Tokens are
invented and Example-shaped throughout.
"""
import pytest

from sluice.cv.bundle import build_bundle, mention_vocab
from sluice.cv.terms import candidates, unbundled_terms
from sluice.cv.validate import section_spans


# ── arms ──────────────────────────────────────────────────────────────────────
def test_arm_i_an_inner_capital_fires_even_at_sentence_start():
    # Sentence-initial, so arm (iii) cannot be what admits it.
    assert candidates("ExampleQuery runs the reports.") == ["ExampleQuery"]


def test_arm_ii_a_trailing_hash_or_plus_fires_on_a_lowercase_token():
    assert candidates("Wrote services in examplelang# daily.") == ["examplelang#"]
    assert candidates("Wrote services in examplelang+ daily.") == ["examplelang+"]


def test_arm_iii_a_leading_capital_fires_mid_sentence():
    assert candidates("Moved reporting onto Examplequery last year.") == ["Examplequery"]


# ── exclusions ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("line", [
    "Moved reporting onto examplev2 last year.",       # digit, lowercase
    "Moved reporting onto Example2 last year.",        # digit, capitalised
    "Cut latency by +15 points.",                      # leading-symbol figure
    "Ranked #1 in the region.",
])
def test_any_digit_excludes_the_token(line):
    assert candidates(line) == []


@pytest.mark.parametrize("line", [
    "Examplequery runs the reports.",                  # first token of the line
    "Shipped it. Examplequery runs the reports.",      # after a full stop
    "Result: Examplequery runs the reports.",          # after a colon
])
def test_a_sentence_initial_capital_alone_is_not_a_candidate(line):
    assert "Examplequery" not in candidates(line)


def test_a_lowercase_token_is_never_a_candidate():
    assert candidates("moved reporting onto examplequery last year.") == []


def test_a_citation_is_stripped_before_tokenising():
    assert candidates("- Shipped the reports [AB1]") == []


def test_a_leading_dot_name_fires():
    assert candidates("Rebuilt the service on .Example last year.") == [".Example"]


# ── report ────────────────────────────────────────────────────────────────────
def test_a_case_folded_vocabulary_match_suppresses():
    assert unbundled_terms([(1, "Moved onto ExampleQuery today.")],
                           frozenset({"examplequery"})) == []


def test_the_plural_fold_strips_exactly_one_s():
    v = frozenset({"examplequery"})
    assert unbundled_terms([(1, "Ran two Examplequerys nightly.")], v) == []
    assert [t for _ln, t, _s in
            unbundled_terms([(1, "Ran two Examplequeryss nightly.")], v)] == ["Examplequeryss"]


def test_an_unbundled_candidate_is_reported_with_its_line_and_snippet():
    line = "Moved reporting onto Examplequery last year."
    assert unbundled_terms([(7, line)], frozenset()) == [(7, "Examplequery", line[:50])]


def test_a_term_is_reported_once_per_line():
    """Review Focus 3."""
    lines = [(1, "Ran Examplequery and then Examplequery again."),
             (2, "Kept Examplequery running.")]
    assert [(ln, t) for ln, t, _s in unbundled_terms(lines, frozenset())] == [
        (1, "Examplequery"), (2, "Examplequery")]


def test_hyphen_and_possessive_forms_of_a_bundled_name_are_quiet():
    """Review Focus 2: `_WORD_RE` splits on `-` and `'`, so the stem is what is looked up."""
    v = frozenset({"exampleworks"})
    assert unbundled_terms([(1, "Ran the Exampleworks-based pipeline.")], v) == []
    assert unbundled_terms([(1, "Owned the Exampleworks's roadmap.")], v) == []


def test_an_empty_vocabulary_is_a_valid_shape_and_reports_every_candidate():
    """Review Focus 4: empty is the right SHAPE, so it must not raise."""
    out = unbundled_terms([(1, "Moved onto Examplequery and ExampleBus.")], frozenset())
    assert [t for _ln, t, _s in out] == ["Examplequery", "ExampleBus"]


def test_a_non_ascii_name_in_the_bundle_is_quiet():
    """Review Focus 5: both sides fragment `Exämple` identically."""
    v = mention_vocab(build_bundle([], "Worked at Exämple.", [], [], {}))
    assert unbundled_terms([(1, "Rejoined Exämple later.")], v) == []


@pytest.mark.parametrize("bad", ["examplequery", ["examplequery"], {1, 2}])
def test_a_wrongly_shaped_vocabulary_raises_naming_the_type(bad):
    """A `str` would substring-match and silently suppress every finding; a list is the
    wrong container; a set of non-str is the wrong members. Fail loudly, naming the type
    only -- never the value, which is the user's own vocabulary."""
    with pytest.raises(TypeError, match=r"mention_vocab"):
        unbundled_terms([(1, "Moved onto Examplequery.")], bad)


# ── anti-vacuity, over the suite's real gate-clean CV ─────────────────────────
def test_the_check_scans_real_lines_finds_candidates_and_reports_nothing_bundled():
    """Roster: tests/test_cv_engine.py::CLEAN_CV with its own ENTRIES bundle. All three
    clauses are needed: (c) alone passes on a sweep that scanned nothing (a)
    or whose rule admits nothing (b)."""
    from tests.test_cv_engine import CLEAN_CV, ENTRIES
    profile, work, _skills = section_spans(CLEAN_CV)
    lines = sorted(dict(profile + work).items())
    assert lines, "(a) section_spans yielded no scoped lines"
    assert any(candidates(text) for _ln, text in lines), "(b) the rule admits nothing"
    vocab = mention_vocab(build_bundle(ENTRIES, "BASELINE", [], [], {"Example Foundry": "EF"}))
    assert unbundled_terms(lines, vocab) == [], "(c) the fixture's own bundle must cover it"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_cv_terms.py -q -p no:cacheprovider`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'sluice.cv.terms'`.

- [ ] **Step 3: Implement**

Create `sluice/cv/terms.py`:

```python
"""The unbundled-term check (#194): a STYLE-tier detector for a term the composer invented.

A token in PROFILE prose or a WORK bullet that is SHAPED like a name and appears nowhere in
what the composer was shown (`cv/bundle.py::mention_vocab`) is reported. A finding drives
the composer's one retry and never bins a lead (#167's STYLE tier); it escalates to a
sign-off hold only under the opt-in `cv.style_hold`. Pure and import-light, like
`cv/slop.py` and `cv/voice.py` -- the engine owns which lines are scanned.

Design and measurement: docs/superpowers/specs/2026-10-02-unbundled-term-design.md.
"""
from sluice.cv.bundle import _WORD_RE
from sluice.cv.validate import _CITE_RE

# A token after one of these reads as sentence-initial, where a leading capital carries no
# signal. Punctuation-based on purpose: a capital after an abbreviation's period
# ("Example Ltd. Examplequery") is skipped -- an under-fire, the direction a retry-driving
# check should err.
_SENTENCE_END = (".", "!", "?", ":")
# Bullet markers stripped before tokenising, plus the space between marker and text.
_MARKERS = "-•*–— "


def candidates(line):
    """The tokens of `line` SHAPED like a name, in order.

    Citations are stripped with render's exact `_CITE_RE`, so the check sees what the
    reader sees; then the bullet marker. Tokenised with `_WORD_RE`, the ONE tokeniser the
    #168 rows use (`cv/validate.py::_tokens` is its `findall`; positions are needed here).

    A candidate contains NO digit -- any digit belongs to the numeric gate, which already
    licenses or refuses `120ms`, `+15`, `#1` and `p99`; that class sank the morphology rule
    #168 measured and rejected -- and has at least one of:

    (i) an uppercase letter after its first character;
    (ii) a trailing `#` or `+`;
    (iii) a leading uppercase letter, when not sentence-initial.

    No arm for an all-caps token at sentence start: (i) already admits every all-caps token
    of two or more letters, and a separate arm was measured as an equivalent mutant.
    """
    text = _CITE_RE.sub("", line).lstrip(_MARKERS)
    out, first = [], True
    for m in _WORD_RE.finditer(text):
        tok = m.group()
        initial = first or text[:m.start()].rstrip().endswith(_SENTENCE_END)
        first = False
        if any(c.isdigit() for c in tok):
            continue
        if (any(c.isupper() for c in tok[1:]) or tok.endswith(("#", "+"))
                or (tok[:1].isupper() and not initial)):
            out.append(tok)
    return out


def unbundled_terms(lines, vocab):
    """`(line no, term, snippet)` for each candidate in `lines` absent from `vocab`.

    `lines` is the engine's scoped `(line no, text)` list; `vocab` is
    `cv/bundle.py::mention_vocab`'s case-folded set. A candidate is suppressed when its
    case-folded form, or that form with ONE trailing `s` removed, is in `vocab` -- the fold
    can only SUPPRESS, so it cannot widen what is reported. Reported once per (line, term).

    Fails loudly on a wrong `vocab` shape, naming the type and never the value: a `str`
    would answer `in` by SUBSTRING and silently suppress every finding, which reads exactly
    like a clean CV.
    """
    if not isinstance(vocab, (set, frozenset)):
        raise TypeError(f"unbundled_terms() takes a set of case-folded str, not "
                        f"{type(vocab).__name__} -- build it with "
                        "cv.bundle.mention_vocab(bundle)")
    bad = next((t for t in vocab if not isinstance(t, str)), None)
    if bad is not None:
        raise TypeError(f"unbundled_terms() takes a set of str, but it holds a "
                        f"{type(bad).__name__} -- build it with "
                        "cv.bundle.mention_vocab(bundle)")
    found = []
    for ln, line in lines:
        seen = set()
        for tok in candidates(line):
            folded = tok.casefold()
            if (tok in seen or folded in vocab
                    or (folded.endswith("s") and folded[:-1] in vocab)):
                continue
            seen.add(tok)
            found.append((ln, tok, line.strip()[:50]))
    return found
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cv_terms.py -q -p no:cacheprovider`
Expected: all PASS.

If the anti-vacuity row's clause (b) fails, the CLEAN_CV scope has no candidate, which means
Task 1's `CI` bullet is no longer in scope. Stop and investigate; do not weaken the clause.

- [ ] **Step 5: Lint, commit, then witness**

```bash
.venv/bin/ruff check sluice tests scripts
git add sluice/cv/terms.py tests/test_cv_terms.py
git commit -m "fixup! feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses: one at a time, running `tests/test_cv_terms.py`, and restoring with
`git checkout sluice/cv/terms.py`.

| Mutation (DELETE or MOVE only) | Expected red |
|---|---|
| delete `any(c.isupper() for c in tok[1:]) or ` | arm (i), `.Example`, `ExampleBus` in the empty-vocab row |
| delete `tok.endswith(("#", "+"))\n                or ` so the `or` chain keeps (i) and (iii) | arm (ii) |
| delete ` and not initial` | all three sentence-initial rows |
| delete `or (tok[:1].isupper() and not initial)` | arm (iii) and the once-per-line row |
| delete the `if any(c.isdigit() ...)`/`continue` pair | the `Example2` digit row |
| delete `_CITE_RE.sub("", line)` → `line` | the citation row |
| delete `or (folded.endswith("s") and folded[:-1] in vocab)` | the plural row |
| delete `tok in seen or ` | the once-per-line row |
| delete the first `if not isinstance(vocab, ...)`/`raise` | the `str` and list rows |

---

### Task 5: `cv.term_check`

Spec §2.2.

**Files:**
- Modify: `sluice/cv/config.py`. Add the field after `style_hold`.
- Modify: `sluice.yaml.example`. Add it to the cv block, commented, after `style_hold`.
- Modify: `docs/CONFIGURATION.md`. Add a row after `voice_check`.
- Test: `tests/test_cv_config.py`, `tests/test_sluice_neutral_defaults.py`.

**Interfaces:**
- Produces: `CvConfig.term_check: bool = True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cv_config.py`:

```python
def test_term_check_round_trips_off_through_the_cv_block(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("cv:\n  term_check: false\n", encoding="utf-8")
    assert load_cv_config(str(p)).term_check is False


def test_a_quoted_term_check_is_refused_rather_than_read_as_true(tmp_path):
    # `"false"` is a non-empty STRING, truthy -- read as-is it would leave the check ON
    # while the file plainly says off. The loader's generic bool-field check refuses it.
    p = tmp_path / "config.yaml"
    p.write_text('cv:\n  term_check: "false"\n', encoding="utf-8")
    with pytest.raises((ValueError, TypeError), match="term_check"):
        load_cv_config(str(p))
```

In `tests/test_sluice_neutral_defaults.py`, after
`test_the_example_config_ships_voice_check_and_style_hold_commented`, add:

```python
# ── #194: cv.term_check ships ON ──────────────────────────────────────────────
# Unlike voice_check it spends nothing per lead: it is pure and deterministic, and costs
# one extra compose only when it fires (the slop stems' profile, also on by default). It is
# not a job preference -- it says nothing about which jobs are good and never bins a lead.
# Pinned in BOTH directions so a flip to False is as visible as a flip to True would be.
def test_term_check_dataclass_default_is_on():
    assert CvConfig().term_check is True


def test_the_example_config_documents_term_check_commented():
    text = _example_text()
    assert "term_check:" in text, "term_check must be documented at all"
    cv_block = _active_block(text, "cv")
    assert "term_check" not in cv_block, "term_check must ship COMMENTED, not active"
```

Before writing that second test, read how
`test_the_example_config_ships_voice_check_and_style_hold_commented` obtains `text` and
`cv_block`. Use exactly the same helper calls, replacing `_example_text()` and
`_active_block(...)` above with whatever that test actually calls. Check with:

```bash
sed -n '/def test_the_example_config_ships_voice_check_and_style_hold_commented/,/^def /p' tests/test_sluice_neutral_defaults.py
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_cv_config.py tests/test_sluice_neutral_defaults.py -q -p no:cacheprovider -k term_check`
Expected: FAIL, with `AttributeError: 'CvConfig' object has no attribute 'term_check'` or the
example assertion.

- [ ] **Step 3: Implement**

In `sluice/cv/config.py`, after the `style_hold: bool = False` line, add:

```python
    # Whether the unbundled-term check runs (#194, cv/terms.py): a term CV prose names that
    # appears nowhere in what the composer was shown drives the one retry. ON by default,
    # deliberately unlike voice_check: it is pure and deterministic and spends nothing
    # unless it fires, the slop stems' cost profile. The escape exists for a THIN vault --
    # precision rests on the baseline CV carrying the user's ordinary vocabulary. No
    # allow-list: a term the candidate really holds belongs in their evidence.
    term_check: bool = True
```

In `sluice.yaml.example`, after the `#   style_hold: false` line, add:

```yaml
#   # Whether a term CV prose names but nothing in your evidence carries drives the
#   # composer's one retry (#194). ON by default; it never bins a lead. Turn it off if a
#   # thin vault (a short baseline CV) makes it fire on ordinary words.
#   term_check: true
```

In `docs/CONFIGURATION.md`, after the `voice_check` row, add:

```markdown
| `term_check` | `true` | #194: whether a term named in PROFILE prose or a WORK bullet that appears nowhere in what the composer was shown (baseline, experience entries, their `Skills:`, the Skills Inventory — never the job description) drives the composer's one retry. Deterministic and spends nothing unless it fires; it never bins a lead, and holds only under `style_hold`. Turn it off if a thin vault makes it fire on ordinary words. Rejects non-bool values |
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cv_config.py tests/test_sluice_neutral_defaults.py tests/test_config_example.py tests/test_docs_claims.py -q -p no:cacheprovider`
Expected: all PASS.

If the quoted-value row fails because nothing raises, the generic bool check does not cover
this field. Stop: the spec's §2.2 claim is then false and must be revisited. Do not add an
ad-hoc check without saying so in the PR.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/config.py sluice.yaml.example docs/CONFIGURATION.md tests/test_cv_config.py tests/test_sluice_neutral_defaults.py
git commit -m "fixup! feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: Wire the check into the engine

Spec §4.

**Files:**
- Modify: `sluice/cv/engine.py`. Change the imports, bind `vocab` beside
  `sources = _bundle.bundle_sources(b)`, split `style_msgs` where it is built, change the
  `best` tuple to four, change the rebind, tag the hold, and reword the `CvResult.slop` field
  comment.
- Test: `tests/test_cv_engine.py`.

**Interfaces:**
- Consumes: `mention_vocab` (Task 3), `unbundled_terms` (Task 4), `CvConfig.term_check`
  (Task 5), and the retention rule (Task 2).
- Produces:
  - **Message format.** `"UNBUNDLED TERM {term!r}: named nowhere in your evidence: {snippet}"`.
  - **Hold entries.** `term\t<msg>` in a `style_hold` hold.
  - **`CvResult.slop`.** Now carries slop messages followed by term messages.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cv_engine.py`, add near the other STYLE fixtures:

```python
# A hard-clean draft whose PROFILE names a term no source carries (#194). Only the prose
# changes, so the HARD gate is untouched; `Examplequery` is mid-sentence, so arm (iii)
# admits it.
UNBUNDLED_TERM_CV = CLEAN_CV.replace(
    "I build reliable systems.", "I build reliable systems on Examplequery.")
```

Add `"unbundled-term": UNBUNDLED_TERM_CV` to `_DRAFTS`, and add this row to
`test_the_sequence_fixtures_are_the_tiers_they_claim`:

```python
        ("unbundled-term", UNBUNDLED_TERM_CV, False, False),
```

It reads `False` because that test's `style` column measures SLOP only. The next test pins
the term.

Then add:

```python
def test_the_unbundled_term_fixture_carries_exactly_one_term():
    from sluice.cv.bundle import mention_vocab
    from sluice.cv.terms import unbundled_terms
    from sluice.cv.validate import section_spans
    profile, work, _skills = section_spans(UNBUNDLED_TERM_CV)
    vocab = mention_vocab(build_bundle(ENTRIES, "BASELINE", [], [], {"Example Foundry": "EF"}))
    assert [t for _ln, t, _s in unbundled_terms(sorted(dict(profile + work).items()), vocab)] \
        == ["Examplequery"]


def test_an_unbundled_term_drives_exactly_one_retry_with_the_finding(monkeypatch):
    res, be, rend = _run_sequence(monkeypatch, ["unbundled-term", "clean"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2
    assert "UNBUNDLED TERM 'Examplequery'" in be.compose_prompts[1]
    assert rend.rendered == [CLEAN_CV], "the clean retry is the fewer-findings draft"


def test_a_persisting_unbundled_term_still_renders_with_style_hold_off(monkeypatch):
    res, _be, rend = _run_sequence(monkeypatch, ["unbundled-term", "unbundled-term"])
    assert res.status == "rendered", "a STYLE finding must never bin a lead"
    assert rend.rendered == [UNBUNDLED_TERM_CV]
    assert any(m.startswith("UNBUNDLED TERM 'Examplequery'") for m in res.slop)


def test_term_check_off_sends_no_term_finding_to_the_retry(monkeypatch):
    _served(monkeypatch)
    be, rend = _SequenceBackend(["unbundled-term", "clean"]), FakeRenderer()
    cfg = _cfg(); cfg.term_check = False
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}), FakeVault(ENTRIES), cfg, be, FakeCache(),
                  renderer=rend)
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 1, "with the check off, the draft is clean"


def test_a_style_hold_tags_an_unbundled_term_as_a_term_not_a_style_concern(monkeypatch):
    """Spec §4: the engine KNOWS the kind when it builds the message, so it tags it --
    never re-parsing a message prefix later."""
    _served(monkeypatch)
    be, rend = _SequenceBackend(["unbundled-term", "unbundled-term"]), FakeRenderer()
    cfg = _cfg(); cfg.style_hold = True
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    v = FakeVault(ENTRIES, notes=[note])
    res = run_one(note, v, cfg, be, FakeCache(), renderer=rend)
    claims = v.fields[note.ref]["needs_signoff"]
    claims = json.loads(claims) if isinstance(claims, str) else claims
    assert any(c.startswith("term\tUNBUNDLED TERM 'Examplequery'") for c in claims), claims
    assert not any(c.startswith("style\tUNBUNDLED TERM") for c in claims), claims
    assert res.status == "needs-signoff"


def test_a_headerless_draft_puts_the_hard_violation_first(monkeypatch):
    """Review Focus 1: without `WORK EXPERIENCE`, section_spans reads the whole body as
    PROFILE, so headers and company words become term candidates. The HARD reason must
    still lead the retry prompt -- the composer reads that list in order."""
    headerless = CLEAN_CV.replace("WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE")
    _DRAFTS["headerless"] = headerless
    try:
        res, be, _rend = _run_sequence(monkeypatch, ["headerless", "clean"])
    finally:
        del _DRAFTS["headerless"]
    retry = be.compose_prompts[1]
    hard_at = retry.find("WORK EXPERIENCE")
    term_at = retry.find("UNBUNDLED TERM")
    assert hard_at != -1, "the missing-header violation never reached the retry"
    assert term_at == -1 or hard_at < term_at
```

Before Step 2, confirm the hold-test plumbing:

- Read how `test_style_hold_withholds_the_pointer_when_enabled` reads the stamped claims, and
  how `FakeVault.hold_for_signoff` records them.
- Change `v.fields[note.ref]["needs_signoff"]` in the hold test to whatever accessor that test
  actually uses. The two lines above are the intent, not the exact accessor.
- Confirm the headerless test's `"WORK EXPERIENCE"` substring is what the engine's
  missing-header violation message contains:

```bash
grep -n "WORK EXPERIENCE" sluice/cv/engine.py
```

  Use the message's real distinctive text if it differs.

Also extend `test_clean_cv_is_actually_clean`, appending:

```python
    # #194: the premise extends to the unbundled-term check. CLEAN_CV composing clean
    # under it is what keeps every test below crediting the retry it means to.
    from sluice.cv.bundle import mention_vocab
    from sluice.cv.terms import unbundled_terms
    from sluice.cv.validate import section_spans
    profile, work, _skills = section_spans(CLEAN_CV)
    vocab = mention_vocab(build_bundle(ENTRIES, "BASELINE", [], [], {"Example Foundry": "EF"}))
    assert unbundled_terms(sorted(dict(profile + work).items()), vocab) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -q -p no:cacheprovider -k "unbundled or term_check or headerless or clean_cv_is_actually"`

Expected:
- The fixture and premise rows PASS.
- The retry, persisting, hold and term-check-off rows FAIL: no UNBUNDLED TERM is produced
  yet, and the off row's assertion passes trivially.

- [ ] **Step 3: Implement**

Make these edits in `sluice/cv/engine.py`.

**(a) Imports.** Beside `from sluice.cv.slop import check_phrases as _slop_phrases`, add:

```python
from sluice.cv.terms import unbundled_terms as _unbundled_terms
```

**(b) Bind the vocabulary.** Directly after `sources = _bundle.bundle_sources(b)`, add:

```python
        # The unbundled-term check's vocabulary (#194), from the SAME `b`, beside
        # `sources` for the reason given above. A separate value and never a
        # `BundleSources` field: it carries the Skills Inventory framing, which no HARD row
        # may license -- see `cv/bundle.py::mention_vocab`.
        vocab = _bundle.mention_vocab(b)
```

**(c) Split the findings.** Replace:

```python
            style_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip
                          in _slop_phrases(scoped_lines, allow=cvcfg.slop_allow)]
```

with:

```python
            slop_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip
                         in _slop_phrases(scoped_lines, allow=cvcfg.slop_allow)]
            # The unbundled-term check (#194, cv/terms.py) over the SAME scoped lines: a
            # term this prose names that nothing the composer was shown carries. Kept in
            # its OWN list so a `style_hold` hold can tag it `term\t` from what the engine
            # already knows, rather than re-parsing a message prefix later.
            term_msgs = ([f"UNBUNDLED TERM {term!r}: named nowhere in your evidence: {snip}"
                          for _ln, term, snip in _unbundled_terms(scoped_lines, vocab)]
                         if cvcfg.term_check else [])
            style_msgs = slop_msgs + term_msgs
```

**(d) A four-member `best`.** In the Task 2 block, replace
`best = (cv_text, style_msgs, voice_flags)` with
`best = (cv_text, slop_msgs, term_msgs, voice_flags)`. Replace its comparison with:

```python
                found = len(style_msgs) + len(voice_flags)
                if best is None or found <= len(best[1]) + len(best[2]) + len(best[3]):
```

Update the comment above `best = None` from `(cv_text, style_msgs, voice_flags)` to
`(cv_text, slop_msgs, term_msgs, voice_flags)`.

**(e) The rebind.** Replace `cv_text, style_msgs, voice_flags = best` with:

```python
        cv_text, slop_msgs, term_msgs, voice_flags = best
        # Re-derived, never carried: `style_msgs` is defined as slop + term, and every
        # CvResult below reads it. One assignment keeps them unable to disagree.
        style_msgs = slop_msgs + term_msgs
```

**(f) The hold tag.** Replace:

```python
        style_blockers = ([f"style\t{msg}" for msg in style_msgs + voice_flags]
                          if cvcfg.style_hold else [])
```

with:

```python
        # `term\t` (#194) names a probable INVENTION, which the sign-off prompt must not
        # describe as a "style/voice concern"; cli.py prints it under its own heading.
        style_blockers = ([f"style\t{msg}" for msg in slop_msgs + voice_flags]
                          + [f"term\t{msg}" for msg in term_msgs]
                          if cvcfg.style_hold else [])
```

**(g) The `CvResult.slop` field comment.** Find it with `grep -n "slop" sluice/cv/engine.py`.
Wherever it defines the field as `cv/slop.py`'s findings, reword it to: the deterministic
STYLE tier's findings, `cv/slop.py`'s `SLOP <label>: <snippet>` messages followed by
`cv/terms.py`'s `UNBUNDLED TERM ...` messages, with the prefix telling the kinds apart.

- [ ] **Step 4: Run the engine-adjacent suites**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py tests/test_cv_run_artefacts.py tests/test_cv_backend_failure.py tests/test_cv_triage_framing.py tests/test_dossier_guard.py tests/test_app_operations.py tests/test_onboard_questions.py tests/e2e -q -p no:cacheprovider`
Expected: all PASS.

If anything fails:
- Do NOT edit a compose-count assertion.
- Print the retry prompt (`be.compose_prompts[1]`) and find the term that fired.
- A synthetic fixture whose bundle lacks a term its CV names gets the term added to its
  BUNDLE, as in Task 1, never removed from the CV.

- [ ] **Step 5: The slop-disabled control (the masking check)**

Write a scratch plugin OUTSIDE the repo. Do not put it under `tests/`:

```bash
CTL=$(mktemp -d)   # scratch, OUTSIDE the repo
cat > $CTL/ctl194.py <<'EOF'
"""Scratch control for #194: disable slop phrases, optionally the term check, at import."""
import os
import sluice.cv.engine as E
if os.environ.get("CTL_NOSLOP"):
    E._slop_phrases = lambda lines, allow=(): []
if os.environ.get("CTL_NOTERM"):
    E._unbundled_terms = lambda lines, vocab: []
EOF
cd "$WORKTREE"
for mode in "CTL_NOSLOP=1 CTL_NOTERM=1" "CTL_NOSLOP=1"; do
  env $mode PYTHONPATH=$CTL .venv/bin/python -m pytest \
    -p ctl194 -q -p no:cacheprovider tests/test_cv_engine.py tests/test_cv_run_artefacts.py \
    2>&1 | grep -E "^FAILED" | sort > "$CTL/$(echo $mode | tr ' =' '__').txt"
done
diff $CTL/CTL_NOSLOP_1_CTL_NOTERM_1.txt $CTL/CTL_NOSLOP_1.txt && echo IDENTICAL
wc -l $CTL/*.txt
```

Expected:
- `IDENTICAL`. With slop disabled, exactly the same tests fail whether the term check is on
  or off. That means no test's slop-retry witness is being satisfied by a term finding.
- A non-zero line count in both files. Zero would mean the control did nothing: check that
  the plugin loaded by adding `print("ctl194 loaded")` and re-running one file with `-s`.

If the two files differ, every test in the second-only set is masked. Find the term its
fixture names and add it to that fixture's bundle, as in Task 1.

- [ ] **Step 6: Full suite + lint**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3 && .venv/bin/ruff check sluice tests scripts`
Expected: all green.

- [ ] **Step 7: Commit, then witness**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "fixup! feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses: one at a time, running the `-k` selection from Step 2, and restoring with
`git checkout sluice/cv/engine.py`.

| Mutation | Expected red |
|---|---|
| replace `if cvcfg.term_check else []` with `if True else []` (deletes the gate's effect) | the term-check-off row |
| delete `+ [f"term\t{msg}" for msg in term_msgs]` | the hold row |
| replace `style_msgs = slop_msgs + term_msgs` in the loop with `style_msgs = slop_msgs` | the retry row |

---

### Task 7: The sign-off prompt names a term finding as a possible invention

Spec §4. INV-4 in the review: under `style_hold`, the prompt would otherwise describe this
finding as a "style/voice concern".

**Files:**
- Modify: `sluice/cli.py::_print_signoff_claims`.
- Test: `tests/test_cv_signoff_prompt.py`.

**Interfaces:**
- Consumes: hold entries tagged `term\t<msg>` (Task 6).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cv_signoff_prompt.py`:

```python
def test_a_term_entry_is_announced_as_a_possible_invention(capsys):
    """#194: an unbundled term is a probable INVENTED technology, so it must not be filed
    under 'style/voice concern(s)', which would understate it to the person signing off."""
    _print_signoff_claims("slug", ["term\tUNBUNDLED TERM 'Examplequery': named nowhere"])
    err = capsys.readouterr().err
    assert "slug has 1 term(s) named nowhere in your evidence (possible invention):" in err
    assert "UNBUNDLED TERM 'Examplequery'" in err
    assert "style/voice" not in err
    assert "unsupported claim" not in err


def test_a_hold_with_no_term_entry_prints_exactly_what_it_did_before_194(capsys):
    """A hold stamped before #194 carries no `term\\t` entry and must not be re-described."""
    _print_signoff_claims("slug", ["unsupported\tMotivated by placeholder\tNONE",
                                   "style\tSLOP leverage: x"])
    assert capsys.readouterr().err == (
        "cv signoff: slug has 1 unsupported claim(s):\n"
        "  - unsupported\tMotivated by placeholder\tNONE\n"
        "cv signoff: slug has 1 style/voice concern(s):\n"
        "  - SLOP leverage: x\n")
```

- [ ] **Step 2: Run to see the first fail**

Run: `.venv/bin/python -m pytest tests/test_cv_signoff_prompt.py -q -p no:cacheprovider`
Expected: `test_a_term_entry_is_announced_as_a_possible_invention` FAILS, because the entry
lands in the fabrication group. Everything else PASSES.

- [ ] **Step 3: Implement**

In `sluice/cli.py::_print_signoff_claims`:

- Change `fabrication, style, unaudited = [], [], []` to
  `fabrication, style, term, unaudited = [], [], [], []`.
- Add an arm after the `style` one:

```python
        elif sep and kind == "term":
            # #194: an unbundled term is a probable INVENTION, not a style concern --
            # printed under its own heading so a reviewer does not read it as wording.
            term.append(rest)
```

- After the `if style:` block, add:

```python
    if term:
        print(f"cv signoff: {slug} has {len(term)} term(s) named nowhere in your evidence "
              f"(possible invention):", file=sys.stderr)
        for c in term:
            print(f"  - {c}", file=sys.stderr)
```

In the function's docstring, after the sentence about `framing\t`, add one sentence:

```text
A `term\t` entry (#194, cv/terms.py) prints under its own "possible invention" heading:
it is a term nothing in the user's evidence carries, which "style/voice" would understate.
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_cv_signoff_prompt.py tests/test_citation_drift.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py tests/test_cv_signoff_prompt.py
git commit -m "fixup! feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 8: Prose that this change makes false

Spec §8. Find every hit by grepping the CLAIM, then fix it.

**Files:**
- Modify: whatever the grep finds. Expect `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`,
  and comments in `sluice/cli.py`, `sluice/mcpserver.py` and `sluice/cv/engine.py`.

- [ ] **Step 1: Enumerate**

```bash
cd "$WORKTREE"
grep -rn "never touches\|nothing harvests\|Nothing harvests\|third signal\|RETAINS the last\|last HARD-clean\|style/voice\|TWO halves\|two halves\|cv/slop.py's\|licensed by nothing\|retained draft" sluice docs/ARCHITECTURE.md docs/USAGE.md .rulesync README.md | grep -v "docs/superpowers"
```

Expected: a list of hits. Record it in scratch notes. Each hit must be either fixed or
deliberately left, with a reason.

- [ ] **Step 2: Apply the edits**

For each hit, rewrite the sentence so it is true after Tasks 2–7. No count of members
anywhere. The required rewrites:

- **`.rulesync/rules/CLAUDE.md`, the SCOPED STYLE tier sentence ("has TWO halves and neither
  blocks").** It becomes: the scoped STYLE tier's members are `cv/slop.py`'s AI-tell stems,
  `cv/terms.py`'s unbundled-term check (#194, on by default via `cv.term_check`), and the
  opt-in model-judged `cv/voice.py` check, and none of them blocks. State no number.
- **`.rulesync/rules/CLAUDE.md`, "RETAINS the last HARD-clean draft across that retry".** It
  becomes: RETAINS the HARD-clean draft with the fewest STYLE/VOICE findings across that
  retry (a tie keeps the later one), so a worse or failed second attempt can never bin a lead
  or replace a cleaner first draft.
- **`.rulesync/rules/CLAUDE.md`, wherever it says the Skills Inventory is "licensed by
  nothing".** Keep the sentence, and append: it is RECOGNISED by `cv/bundle.py::mention_vocab`,
  so the unbundled-term check does not report a declared skill, but nothing LICENSES it.
- **`docs/ARCHITECTURE.md`, the STYLE-tier paragraph and the retention sentence.** Same
  content as the two `CLAUDE.md` rewrites above, in that file's register. If it calls voice
  "the third signal", reword it without the ordinal.
- **`sluice/cli.py` and `sluice/mcpserver.py`, comments describing `slop` as
  `cv/slop.py`'s findings only.** Reword to "the deterministic STYLE tier's findings
  (`cv/slop.py` and `cv/terms.py`)". In `mcpserver.py`, the untrusted-content reasoning
  applies unchanged: the term snippet is composed text too.

- [ ] **Step 3: Regenerate and run the guards**

```bash
npm ci --ignore-scripts && npm run rulesync
.venv/bin/python -m pytest tests/test_docs_claims.py tests/test_citation_drift.py tests/test_doc_links_from_code.py -q -p no:cacheprovider
git status --porcelain
```

Expected:
- The tests PASS.
- `git status` shows only tracked files you edited. `CLAUDE.md`/`AGENTS.md` are gitignored,
  so they do not appear.

- [ ] **Step 4: Re-grep**

Re-run Step 1's grep.
Expected: every remaining hit is one you deliberately left, for example a historical
`docs/superpowers/` reference.

- [ ] **Step 5: Commit**

```bash
git add -A docs .rulesync sluice
git commit -m "fixup! feat(cv): report terms CV prose names but no evidence carries (#194)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: Autosquash, per-commit verification, local review

- [ ] **Step 1: Autosquash**

```bash
cd "$WORKTREE"
GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash origin/main
git log --oneline origin/main..HEAD
```

Expected: five commits, in this order:

1. `docs(spec)` ×2
2. `test(cv)`
3. `fix(cv)`
4. `feat(cv): report terms CV prose names but no evidence carries (#194)`

The `feat` commit's message is the Task 3 one.

- [ ] **Step 2: Rewrite the feat commit's body**

Amend the `feat(cv)` commit's message to describe the whole feature. Use the
`amend! <subject>` commit approach from the project memory, or a `git rebase -i` edit with
`GIT_SEQUENCE_EDITOR`. The body should say:

- what is reported, and in which lines;
- that a finding drives one retry and never bins a lead;
- the `cv.term_check` default;
- the `term\t` sign-off heading.

It must name no token from the measurement and no count.

- [ ] **Step 3: Test each commit**

```bash
for c in $(git rev-list --reverse origin/main..HEAD); do
  git checkout -q "$c" && .venv/bin/python -m pytest -q -p no:cacheprovider -x 2>&1 | tail -1 | sed "s/^/$c: /"
done
git checkout -q feat/194-named-technology
```

Expected: every commit green.

- [ ] **Step 4: Ruff + full suite + no-PATH variant**

```bash
.venv/bin/ruff check sluice tests scripts
.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -1
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: all green.

- [ ] **Step 5: Local review before any push**

Run `/review-pr` on the branch (project rule: local review BEFORE pushing, then CodeRabbit).
Address findings as fixups, re-autosquash, and repeat Step 3.
