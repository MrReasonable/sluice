# Skills Inventory Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the user's verified Skills Inventory in front of the CV composer as a fourth, non-citable bundle section; derive the skills-shaped negative constraint instead of hand-typing it; and stop `bundle.rank()` missing an entry because the ad said "documenting" and the entry said "documentation".

**Architecture:** `cv/bundle.py` grows a `skills` parameter whose lines `render_bundle` owns and `bundle_sources` never sees, so non-citability is structural. `sluice/core/stem.py` adds a Porter stemmer that `rank()` and a new `doctor` check both use. `cv/engine.py` switches to reading evidence BY KIND, which retires the `read_experience_entries` delegate and splits `EvidenceKind.cited_by_gate` into two flags that this change makes non-equivalent for the first time.

**Tech Stack:** Python 3.12+, standard library only inside `sluice/` (see Global Constraints). pytest. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-25-evidence-consumption-design.md` — read it first; the plan argues from it and does not repeat its reasoning.

## Global Constraints

- **`sluice/` is standard-library only.** No new runtime dependency. The stemmer is hand-written for this reason.
- **Neutrality:** no employer names, role preferences, locations, contact details, hostnames or absolute paths in `sluice/` or `tests/`. Fixtures stay synthetic. `tests/data/porter_vocabulary.txt` is the one verbatim third-party file and is measured clean (pure ASCII, `^[a-z]+$` per line, zero `/Users/` or `/home/` shapes).
- **Conventional commits.** `release-please` reads the subjects. Use `feat(cv)`, `fix(cv)`, `test(cv)`, `docs(cv)`, `refactor(core)`.
- **Never widen `cv/validate.py`.** Skills must license numbers in NEITHER pool.
- **`_entry_block`'s rule is inviolate:** every line it returns is a source for that entry. Skills lines belong in `render_bundle`, never in `_entry_block`.
- **Run before mutation testing:** `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- **Interpreter:** call `.venv/bin/python` explicitly, never a bare `python` shim.
- **Edit `.rulesync/rules/CLAUDE.md`, never `CLAUDE.md`** (generated), then `npm ci --ignore-scripts && npm run rulesync`.

---

## File Structure

| File | Responsibility |
|---|---|
| `sluice/core/stem.py` (create) | Porter stemmer + `tokens()`. Pure, no imports from `sluice`. |
| `tests/data/porter_vocabulary.txt` (create) | Verbatim `word stem` corpus, 23,531 rows, with a provenance header. |
| `tests/test_core_stem.py` (create) | Corpus equality + the must-not-conflate pairs. |
| `sluice/cv/bundle.py` (modify) | `skills` param, `_skills_block`, derived negative, stemmed `rank`. |
| `sluice/cv/compose.py` (modify) | One new CV RULE. |
| `sluice/cv/engine.py` (modify) | Read evidence by kind; catch a broken skills corpus. |
| `sluice/core/protocols.py` (modify) | Split the flag; delete `read_experience_entries`. |
| `sluice/core/vault.py` (modify) | Delete the delegate. |
| `sluice/core/doctor.py` (modify) | `classify_negatives_vs_skills`; correct the `#165` messages. |
| `sluice/core/app.py` (modify) | Wire the new check into `Sluice.doctor()`. |

---

### Task 1: The Porter stemmer

**Files:**
- Create: `sluice/core/stem.py`
- Create: `tests/data/porter_vocabulary.txt`
- Test: `tests/test_core_stem.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `stem(word: str) -> str`, `tokens(text: str) -> list[str]`, `stem_all(text: str) -> set[str]`.

- [ ] **Step 1: Build the corpus fixture**

The two source files are fetched once and joined into one `word stem` file so a row cannot drift from its expectation.

```bash
cd /tmp && curl -sSL -o voc.txt https://tartarus.org/martin/PorterStemmer/voc.txt \
  && curl -sSL -o out.txt https://tartarus.org/martin/PorterStemmer/output.txt
test "$(wc -l < voc.txt)" = "$(wc -l < out.txt)" || { echo "LENGTH MISMATCH"; exit 1; }
grep -qE '/(Users|home)/' voc.txt out.txt && { echo "HOME PATH IN CORPUS"; exit 1; }
mkdir -p tests/data
{ echo "# Porter stemmer test vocabulary -- VERBATIM third-party corpus, do not edit."
  echo "# Source: https://tartarus.org/martin/PorterStemmer/ (voc.txt + output.txt)"
  echo "# Author: Martin Porter. Captured 2026-08-25. The page licenses the algorithm"
  echo "# encodings 'free of charge for any purpose'; it states no separate terms for"
  echo "# these test files, and we redistribute them on the reading that they share it."
  echo "# NOT a sluice fixture: no neutrality sweep may 'clean' a word here. The corpus"
  echo "# is worth something only while it is byte-identical to the reference."
  echo "# Format: <word> <expected stem>, one pair per line."
  paste -d' ' voc.txt out.txt; } > tests/data/porter_vocabulary.txt
wc -l tests/data/porter_vocabulary.txt   # expect 23538 (23531 + 7 header lines)
```

- [ ] **Step 2: Write the failing test**

`tests/test_core_stem.py`:

```python
"""The stemmer is certified against Martin Porter's own published vocabulary rather
than against examples chosen here. A table of cases the author picked certifies
nothing -- see the spec's D7 and the 42-mutant study behind it."""
import pathlib

import pytest

from sluice.core.stem import stem, stem_all, tokens

_CORPUS = pathlib.Path(__file__).resolve().parent / "data" / "porter_vocabulary.txt"


def _rows():
    out = []
    for line in _CORPUS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        word, expected = line.split()
        out.append((word, expected))
    return out


def test_the_corpus_is_present_and_whole():
    """Scope, not violations. A corpus that failed to load leaves the equality below
    iterating an empty list -- green forever, this repo's `all([])` trap."""
    rows = _rows()
    assert len(rows) == 23531, f"expected Porter's full vocabulary, got {len(rows)} rows"


def test_stem_matches_porters_published_vocabulary():
    wrong = [(w, e, stem(w)) for w, e in _rows() if stem(w) != e]
    assert not wrong, f"{len(wrong)} disagreements with the reference, e.g. {wrong[:5]}"


@pytest.mark.parametrize("a,b", [
    ("documenting", "documentation"), ("documented", "documentation"),
    ("deployments", "deployment"), ("migrated", "migration"),
    ("automating", "automation"), ("testing", "tests"),
])
def test_inflections_of_one_word_share_a_stem(a, b):
    assert stem(a) == stem(b), f"{a!r} and {b!r} must rank the same entry"


@pytest.mark.parametrize("a,b", [
    ("planning", "plant"), ("planning", "plane"), ("commit", "committee"),
    ("management", "mandate"), ("contract", "contrast"), ("release", "relevant"),
    # The two the CURRENT substring match gets wrong: `"java" in "javascript"` is True.
    ("java", "javascript"), ("scala", "scalability"),
])
def test_distinct_words_do_not_share_a_stem(a, b):
    assert stem(a) != stem(b), f"{a!r} and {b!r} must not be conflated"


def test_tokens_splits_on_non_letters_and_lowercases():
    assert tokens("Platform/Docs, 2024 tooling") == ["platform", "docs", "tooling"]


def test_stem_all_is_the_stemmed_token_set():
    assert stem_all("Documenting the deployments") == {stem("documenting"), "the",
                                                       stem("deployments")}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_core_stem.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'sluice.core.stem'`.

- [ ] **Step 4: Implement the stemmer**

`sluice/core/stem.py`. The two departures from the 1980 paper in `_STEP2` are load-bearing and measured: a faithful reading of the paper scores 99.932% on the reference corpus, and all 16 failures (`apology`, `assembly`, `horribly`, ...) are these.

```python
# sluice/core/stem.py
"""Porter (1980) suffix stripping, so JD keywords match evidence across word forms.

Hand-written because `sluice/` is standard-library only (CLAUDE.md), and because the
alternatives were measured and are worse: an ad-hoc suffix list is unprincipled
(`deployment` -> `deploym` but `deployments` -> `deplo`), and every common-prefix
threshold tried had false positives AND misses. See the spec's D7.

Certified against Martin Porter's published 23,531-word vocabulary at 100.0000%
(tests/data/porter_vocabulary.txt). That corpus buys a ONE-TIME validation of this
implementation against the reference, not drift detection of the reference itself.

Consumers: `cv/bundle.py:rank` and `core/doctor.py:classify_negatives_vs_skills`.
Deliberately NOT `core/relevance.py`, whose keep/drop lists are a user-specified
ingest gate applied before dedup -- widening that match silently changes which leads
are discarded, the 672ad2a failure.
"""
import re

_VOWELS = "aeiou"
_WORD_RE = re.compile(r"[a-z]+")


def _is_consonant(w, i):
    """Porter's definition: y is a consonant unless preceded by a consonant."""
    c = w[i]
    if c in _VOWELS:
        return False
    if c == "y":
        return i == 0 or not _is_consonant(w, i - 1)
    return True


def _measure(stem_):
    """Porter's m: the count of VC sequences in [C](VC)^m[V]."""
    s = "".join("c" if _is_consonant(stem_, i) else "v" for i in range(len(stem_)))
    m, i = 0, 0
    while i < len(s) and s[i] == "c":
        i += 1
    while i < len(s):
        while i < len(s) and s[i] == "v":
            i += 1
        if i >= len(s):
            break
        while i < len(s) and s[i] == "c":
            i += 1
        m += 1
    return m


def _has_vowel(stem_):
    return any(not _is_consonant(stem_, i) for i in range(len(stem_)))


def _double_consonant(w):
    return len(w) >= 2 and w[-1] == w[-2] and _is_consonant(w, len(w) - 1)


def _cvc(w):
    """*o: ends consonant-vowel-consonant where the last is not w, x or y."""
    if len(w) < 3:
        return False
    return (_is_consonant(w, len(w) - 3) and not _is_consonant(w, len(w) - 2)
            and _is_consonant(w, len(w) - 1) and w[-1] not in "wxy")


# Two entries here are the REFERENCE IMPLEMENTATION's documented departures from the
# 1980 paper, not transcription slips: `bli -> ble` stands in place of the paper's
# `abli -> able`, and `logi -> log` is absent from the paper entirely. Measured: without
# them agreement with the published vocabulary is 99.932%, and every one of the 16
# failures is one of these two (`apology`, `assembly`, `horribly`, `possibly`, ...).
_STEP2 = [("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
          ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"),
          ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
          ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
          ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
          ("logi", "log")]
_STEP3 = [("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
          ("ical", "ic"), ("ful", ""), ("ness", "")]
_STEP4 = ["al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
          "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize"]


def stem(word):
    """The Porter stem of one word. Lowercases; returns words of 2 letters or fewer
    unchanged, as the algorithm specifies."""
    w = word.lower()
    if len(w) <= 2:
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    applied_1b = False
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and _has_vowel(w[:-2]):
        w, applied_1b = w[:-2], True
    elif w.endswith("ing") and _has_vowel(w[:-3]):
        w, applied_1b = w[:-3], True
    if applied_1b:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif _double_consonant(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif _measure(w) == 1 and _cvc(w):
            w += "e"
    if w.endswith("y") and _has_vowel(w[:-1]):
        w = w[:-1] + "i"
    # Steps 2 and 3: the LONGEST matching suffix wins, never the first listed.
    for table in (_STEP2, _STEP3):
        best = None
        for suf, rep in table:
            if w.endswith(suf) and (best is None or len(suf) > len(best[0])):
                best = (suf, rep)
        if best and _measure(w[:-len(best[0])]) > 0:
            w = w[:-len(best[0])] + best[1]
    best4 = None
    for suf in _STEP4:
        if w.endswith(suf) and (best4 is None or len(suf) > len(best4)):
            best4 = suf
    if best4:
        candidate = w[:-len(best4)]
        if _measure(candidate) > 1 and (best4 != "ion" or candidate.endswith(("s", "t"))):
            w = candidate
    if w.endswith("e"):
        m = _measure(w[:-1])
        if m > 1 or (m == 1 and not _cvc(w[:-1])):
            w = w[:-1]
    if _measure(w) > 1 and _double_consonant(w) and w.endswith("l"):
        w = w[:-1]
    return w


def tokens(text):
    """Lowercased alphabetic runs. The tokeniser both sides of a match must share --
    a keyword stemmed against an unstemmed haystack matches nothing."""
    return _WORD_RE.findall((text or "").lower())


def stem_all(text):
    """The set of stems in `text`. The comparable form for keyword matching."""
    return {stem(t) for t in tokens(text)}
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_core_stem.py -q`
Expected: PASS, including `test_stem_matches_porters_published_vocabulary` (23,531 rows, ~70 ms).

- [ ] **Step 6: Witness that the corpus test is load-bearing**

Mutate by DELETING, never adding. Commit first — a mutation witness that restores via `git checkout` wipes uncommitted work.

```bash
git add -A && git commit -q -m "wip: stemmer" # temporary; amended in Step 7
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
# Delete the `logi -> log` rule -- witnessed by exactly ONE word in 23,531 (`apology`).
sed -i '' 's/^          ("logi", "log")\]/          ]/' sluice/core/stem.py
.venv/bin/python -m pytest tests/test_core_stem.py -q   # MUST fail
git checkout sluice/core/stem.py
.venv/bin/python -m pytest tests/test_core_stem.py -q   # green again
```

Expected: the mutant is KILLED. If it survives, the corpus did not load — check Step 1's row count before believing any later result.

- [ ] **Step 7: Commit**

```bash
git add sluice/core/stem.py tests/test_core_stem.py tests/data/porter_vocabulary.txt
git commit -q --amend -m "feat(core): add a Porter stemmer for keyword matching (#165)"
```

---

### Task 2: `rank()` matches on stems

**Files:**
- Modify: `sluice/cv/bundle.py:30-36`
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: `sluice.core.stem.stem_all`, `sluice.core.stem.stem` (Task 1).
- Produces: `rank(entries, jd_keywords)` — unchanged signature, stem-based scoring.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv_bundle.py`. The entry must BEAT scoring competitors — a two-entry probe where both score 0 proves nothing, since `sorted` is stable and merely preserves input order. That mistake is recorded in the spec.

```python
def _rank_entry(best_for, title):
    return {"title": title, "company": "Example Co", "best_for": best_for,
            "category": "", "metrics": "", "body": ""}


def test_a_word_form_mismatch_no_longer_buries_the_right_entry():
    """#165's comment. The ad's top requirement was 'documenting'; the one entry that
    evidenced it said 'documentation'. `"documenting" in "documentation"` is False, so
    it scored zero and ranked BELOW every unrelated entry that happened to match a
    different ad word. Measured before the fix: position 6 of 7."""
    entries = ([_rank_entry("delivery planning", f"unrelated-{i}") for i in range(3)]
               + [_rank_entry("documentation", "THE-RIGHT-ONE")]
               + [_rank_entry("delivery planning", f"unrelated-{i}") for i in range(3, 6)])
    ranked = B.rank(entries, ["documenting", "delivery", "planning"])
    assert ranked[0]["title"] == "THE-RIGHT-ONE", [e["title"] for e in ranked]


def test_ranking_orders_and_never_excludes():
    """The property the whole bundle rests on: JD keywords reorder, never filter."""
    entries = [_rank_entry("documentation", "a"), _rank_entry("nothing relevant", "b")]
    assert len(B.rank(entries, ["documenting"])) == 2


def test_the_substring_false_positives_are_gone():
    """`"java" in "javascript"` is True, so the old ranker scored a JavaScript entry on
    a Java keyword. Stems do not relate them."""
    entries = [_rank_entry("javascript", "js"), _rank_entry("java", "java")]
    assert B.rank(entries, ["java"])[0]["title"] == "java"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k "word_form or substring_false" -q`
Expected: `test_a_word_form_mismatch_no_longer_buries_the_right_entry` FAILS (`unrelated-0` first).

- [ ] **Step 3: Implement**

Replace `rank` in `sluice/cv/bundle.py`:

```python
def rank(entries: list[dict], jd_keywords: list[str]) -> list[dict]:
    """Order entries by how many JD keywords their classification fields answer.

    Matching is on STEMS, both sides, so "documenting", "documentation" and
    "documented" all rank the same entry (#165). Before this it was raw substring
    containment, which missed every inflection AND related words it should not
    ("java" in "javascript" is True).

    Orders, never excludes: the FULL verified set is emitted either way, so a ranking
    change can never lose evidence -- only move it. It DOES change which `[id]` an
    entry receives, since `assign_codes` runs after this.

    The haystack stays `best_for`/`category`/`title` and deliberately excludes `body`:
    matching into free prose lets a long entry out-score a precise one on volume.
    """
    wanted = {_stem(k) for k in jd_keywords}

    def score(e):
        hay = f"{e.get('best_for','')} {e.get('category','')} {e.get('title','')}"
        return len(wanted & _stem_all(hay))

    return sorted(entries, key=score, reverse=True)
```

and add the import at the top of `sluice/cv/bundle.py`:

```python
from sluice.core.stem import stem as _stem, stem_all as _stem_all
```

- [ ] **Step 4: Run the full bundle suite**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q`
Expected: PASS, **including the two frozen tests**. Measured: `_frozen_bundle()` passes `jd_keywords=[]`, so every score is 0, the stable sort preserves order, and the frozen literal is untouched by this task. If a frozen test reddens here, the ranker changed something it should not have — do not re-freeze to fix it.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "fix(cv): rank evidence on stems, not raw substrings (#165)"
```

---

### Task 3: Skills as a fourth, non-citable bundle section

**Files:**
- Modify: `sluice/cv/bundle.py` (`build_bundle`, `render_bundle`, new `_skills_block`)
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: Task 2's `rank`.
- Produces: `build_bundle(entries, baseline, negatives, jd_keywords, prefix_map, skills=())` and `bundle["skills"]`.

- [ ] **Step 1: Write the failing tests**

```python
_SKILL = {"title": "Example Platform Skill", "best_for": "platform documentation",
          "company": "", "category": "", "metrics": "", "body": "Body prose.",
          "fields": {"Proficiency": "8 years", "Domain": "platform documentation",
                     "Evidence": "shipped 62 things", "Signal Value": "depth not breadth"}}


def _bundle_with_skills(skills=(_SKILL,)):
    return B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES,
                          [], FROZEN_PREFIX_MAP, skills=list(skills))


def test_a_skills_digit_is_licensed_in_neither_pool():
    """THE load-bearing test of this feature, and it compares against NO frozen literal
    -- so re-capturing FROZEN_BUNDLE_TEXT cannot bring it back into sync. `8` and `62`
    are the skill's own figures; neither may become a permitted number anywhere."""
    s = B.bundle_sources(_bundle_with_skills())
    assert "62" not in s.baseline
    assert all("62" not in n for n in s.nums.values())
    assert all("8" not in n for n in s.nums.values()), (
        "a skills digit reached an entry's allowlist -- the skills block has been "
        "moved into _entry_block, which licenses it for that entry")


def test_the_skills_section_renders_after_the_entries_and_before_the_negatives():
    text = B.render_bundle(_bundle_with_skills())
    assert text.index("[AL2]") < text.index("=== SKILLS INVENTORY") \
           < text.index("=== NEGATIVE CONSTRAINTS")


def test_the_skills_section_carries_the_four_fields_and_the_body():
    text = B.render_bundle(_bundle_with_skills())
    assert "Example Platform Skill" in text
    assert "proficiency=8 years" in text
    assert "signal=depth not breadth" in text
    assert "shipped 62 things" in text
    assert "Body prose." in text


def test_an_empty_inventory_emits_no_header_at_all():
    """Not an empty header: that asserts to the model that the candidate has no
    skills, which is a negative claim it may act on. Empty means abstain."""
    assert "SKILLS INVENTORY" not in B.render_bundle(_bundle_with_skills(skills=()))


def test_the_pre_174_oracle_still_agrees_when_skills_are_present():
    """`_oracle` is the pre-#174 text parser kept as a co-variant detector. With the
    section after the last [id], its `=== header ===` reset drops skills lines into
    neither pool, so the two derivations still agree. Emitted BEFORE the entries they
    would fall into `baseline` (measured: 61, 62, 8 leak)."""
    b = _bundle_with_skills()
    text = B.render_bundle(b)
    assert B.bundle_sources(b) == B.BundleSources(*_oracle(text))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k skill -q`
Expected: FAIL — `build_bundle() got an unexpected keyword argument 'skills'`.

- [ ] **Step 3: Implement**

In `sluice/cv/bundle.py`, change `build_bundle` and `render_bundle` and add `_skills_block`:

```python
def build_bundle(entries, baseline, negatives, jd_keywords, prefix_map,
                 skills=()) -> dict:
    ranked = rank(entries, jd_keywords)
    return {"baseline": baseline, "entries": assign_codes(ranked, prefix_map),
            "negatives": list(negatives),
            # Ranked by the same JD keywords, so the most relevant framing leads --
            # but NOT code-assigned: an [id] is what makes a thing citable, and the
            # whole point of this section is that it is not. Defaults to () so every
            # existing caller and test constructs a bundle unchanged.
            "skills": rank(list(skills), jd_keywords)}


def _skills_block(skill: dict) -> list[str]:
    """The lines ONE skills entry contributes to the rendered prompt.

    Deliberately NOT a sibling of `_entry_block`, despite the shape. `_entry_block`'s
    rule is that every line it returns is a SOURCE for that entry, and `bundle_sources`
    harvests digits from it. Nothing harvests from this: `bundle_sources` walks
    `bundle["entries"]` and never touches `bundle["skills"]`, which is what makes a
    skills figure licensed nowhere (#165). Moving this into `_entry_block` -- or
    teaching `bundle_sources` to read it -- licenses every skills digit at once, and
    `test_a_skills_digit_is_licensed_in_neither_pool` is what catches that.

    Reads `fields` by the kind's own frontmatter names rather than the floor keys:
    `EVIDENCE_KINDS["skills"]` maps only `best_for <- Domain`, so Proficiency,
    Evidence and Signal Value have no floor analogue and are reachable only here.
    """
    f = skill.get("fields") or {}
    head = f"- {skill.get('title','')}"
    for label, key in (("proficiency", "Proficiency"), ("domain", "Domain"),
                       ("signal", "Signal Value")):
        if f.get(key):
            head += f" | {label}={f[key]}"
    lines = [head]
    if f.get("Evidence"):
        lines.append(f"  {f['Evidence']}")
    if skill.get("body"):
        lines.append(f"  {skill['body']}")
    return lines
```

and in `render_bundle`, insert between the entry loop and the negatives header:

```python
    # After the entries it frames, before the hard "must NOT appear" list. Placement is
    # measured, not stylistic: emitted BEFORE the entries, the pre-#174 oracle in
    # tests/test_cv_bundle.py folds these digits into `baseline` and disagrees with
    # `bundle_sources`. Omitted ENTIRELY when empty -- an empty header would assert to
    # the model that the candidate holds no skills.
    if bundle.get("skills"):
        lines += ["=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ==="]
        for sk in bundle["skills"]:
            lines += _skills_block(sk)
        lines.append("")
    lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q`
Expected: the five new tests PASS. `test_the_rendered_prompt_has_not_drifted` and `test_the_allowlist_still_matches_the_frozen_prompt` still PASS, because `_frozen_bundle()` passes no `skills` and the section is omitted when empty.

- [ ] **Step 5: Witness the non-citability test**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Temporarily MOVE the skills lines into the harvested set by adding `bundle["skills"]` handling to `bundle_sources` — this is the mutation the guard exists for. Confirm `test_a_skills_digit_is_licensed_in_neither_pool` FAILS, then revert with `git checkout sluice/cv/bundle.py` and re-run.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "feat(cv): emit the Skills Inventory as a non-citable bundle section (#165)"
```

---

### Task 4: The derived negative constraint

**Files:**
- Modify: `sluice/cv/bundle.py` (`build_bundle`)
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: Task 3's `bundle["skills"]`.
- Produces: `bundle["negatives"]` with the derived line first when the inventory is non-empty.

- [ ] **Step 1: Write the failing test**

```python
_DERIVED = ("claim no technology, language, framework or tool that is not named in "
            "the SKILLS INVENTORY or the VERIFIED EXPERIENCE ENTRIES above")


def test_the_derived_constraint_appears_only_with_a_non_empty_inventory():
    with_skills = _bundle_with_skills()
    without = _bundle_with_skills(skills=())
    assert with_skills["negatives"][0] == _DERIVED
    assert _DERIVED not in without["negatives"]


def test_configured_negatives_survive_alongside_the_derived_one():
    """cv.negatives stays: an inventory cannot express a negative that is not about
    skills at all ('never claim a security clearance')."""
    b = B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, ["never claim 91 users"],
                       [], FROZEN_PREFIX_MAP, skills=[_SKILL])
    assert b["negatives"] == [_DERIVED, "never claim 91 users"]


def test_the_derived_constraint_names_nothing_and_so_cannot_go_stale():
    """It is a cross-reference, not a generated roster. A roster would duplicate the
    SKILLS section immediately above it and grow without bound."""
    assert "Example Platform Skill" not in _DERIVED
    assert "platform" not in _DERIVED
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k derived -q`
Expected: FAIL — `negatives[0]` is the configured string, not the derived one.

- [ ] **Step 3: Implement**

In `build_bundle`, replace the `negatives` line:

```python
    ranked_skills = rank(list(skills), jd_keywords)
    # Derived rather than hand-typed (#165). `cv.negatives` is a prose shadow of the
    # Skills Inventory and drifts from it; this line names NOTHING, so it cannot go
    # stale, and it is conditional on the inventory existing so an unconfigured install
    # gains no constraint it cannot satisfy. It does not, on its own, stop a stale
    # CONFIGURED negative disagreeing with the inventory -- `core/doctor.py`'s
    # classify_negatives_vs_skills is what makes that disagreement visible.
    derived = [_DERIVED_NEGATIVE] if ranked_skills else []
    return {"baseline": baseline, "entries": assign_codes(ranked, prefix_map),
            "negatives": derived + list(negatives),
            "skills": ranked_skills}
```

and add the module constant above `build_bundle`:

```python
# Exported so tests and `core/doctor.py` compare against ONE spelling rather than two
# literals that can drift.
_DERIVED_NEGATIVE = ("claim no technology, language, framework or tool that is not "
                     "named in the SKILLS INVENTORY or the VERIFIED EXPERIENCE "
                     "ENTRIES above")
```

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q` — expect PASS.

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "feat(cv): derive the skills negative constraint instead of typing it (#165)"
```

---

### Task 5: Re-freeze `FROZEN_BUNDLE_TEXT` — its own commit, readable diff

**Files:**
- Modify: `tests/test_cv_bundle.py` (`FROZEN_BUNDLE_TEXT`, `_frozen_bundle`)

This task exists ONLY so the freeze diff is reviewable in isolation. `_entry_block`'s docstring is explicit that re-capturing is how a widening launders through green: both frozen tests move with the mutant and stay green. A human reading THIS diff is the control.

- [ ] **Step 1: Give `_frozen_bundle` a skills entry**

```python
FROZEN_SKILLS = [{"title": "Example Frozen Skill", "best_for": "platform", "body": "",
                  "fields": {"Proficiency": "71 years", "Domain": "platform",
                             "Evidence": "shipped 72 things", "Signal Value": "depth"}}]


def _frozen_bundle():
    return B.build_bundle(entries=FROZEN_ENTRIES, baseline=FROZEN_BASELINE,
                          negatives=FROZEN_NEGATIVES, jd_keywords=[],
                          prefix_map=FROZEN_PREFIX_MAP, skills=FROZEN_SKILLS)
```

The sentinels `71` and `72` are chosen to collide with nothing already in the literal (which uses 21/22, 31-38, 41-47, 51-53, 91-92).

- [ ] **Step 2: Regenerate the literal and READ THE DIFF**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'tests')
import test_cv_bundle as T
from sluice.cv import bundle as B
print(B.render_bundle(T._frozen_bundle()))
"
```

Paste the output into `FROZEN_BUNDLE_TEXT`, then:

```bash
git diff tests/test_cv_bundle.py
```

**Read it.** The ONLY additions must be the derived negative line, the `=== SKILLS INVENTORY ... ===` header and its one entry. If any existing entry line changed, a source was widened or narrowed — stop and investigate rather than accepting the capture.

- [ ] **Step 3: Extend the literal-independent sentinel test**

Append to `test_bundle_sources_sentinels_hold_independent_of_the_frozen_literal`:

```python
    # The skills sentinels must be absent from EVERY pool. This assertion compares
    # against no literal, so re-freezing cannot bring it back into sync -- it is the
    # one check a re-capture cannot silently move.
    assert "71" not in s.baseline and all("71" not in n for n in s.nums.values())
    assert "72" not in s.baseline and all("72" not in n for n in s.nums.values())
```

- [ ] **Step 4: Run and commit alone**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q` — expect PASS.

```bash
git add tests/test_cv_bundle.py
git commit -q -m "test(cv): re-freeze the bundle prompt for the skills section (#165)"
```

---

### Task 6: The prompt rule

**Files:**
- Modify: `sluice/cv/compose.py` (`_RULES`)
- Test: `tests/test_cv_compose.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_prompt_forbids_quoting_a_number_from_the_skills_section():
    """Without this the model is told the bundle is 'the ONLY permitted source' and
    shown `Proficiency: 8 years`, which it will reasonably use -- earning INVENTED
    PROFILE METRIC and, if the retry repeats it, a skipped lead. The trap is ours to
    close (#165, spec D3)."""
    prompt = C.build_prompt("BUNDLE", "JD", "Example Co", "Role", name="Example Name")
    assert "SKILLS INVENTORY" in prompt
    assert "never quote a number from it" in prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_compose.py -k skills -q` — expect FAIL.

- [ ] **Step 3: Implement**

Add to `_RULES` in `sluice/cv/compose.py`, immediately after the "Every WORK EXPERIENCE bullet MUST end with a citation" rule:

```
- The SKILLS INVENTORY section is FRAMING, not a source. Use it to choose which experience entries to lead with and how to describe them. Never cite it, never quote a number from it, and never introduce a claim that rests on it alone: every fact in the CV must still come from the BASELINE CV or a VERIFIED EXPERIENCE ENTRY.
```

- [ ] **Step 4: Run the whole compose suite and commit**

Run: `.venv/bin/python -m pytest tests/test_cv_compose.py -q`
Expected: PASS, including `test_the_prompt_names_exactly_the_phrases_the_gate_enforces` (this rule adds no slop stems).

```bash
git add sluice/cv/compose.py tests/test_cv_compose.py
git commit -q -m "feat(cv): tell the composer the skills section is framing, not a source (#165)"
```

---

### Task 7: Engine reads evidence BY KIND, and a broken corpus never bins a lead

**Files:**
- Modify: `sluice/cv/engine.py:284-288` and `CvResult` (around line 95)
- Test: `tests/test_cv_engine.py`

**Interfaces:**
- Consumes: `Store.read_evidence(kind, verified_only)`; Task 3's `build_bundle(..., skills=...)`.
- Produces: `CvResult.skills_unreadable: bool`.

- [ ] **Step 1: Write the failing tests**

```python
class SkillsVault(FakeVault):
    """FakeVault plus the by-kind read Task 7 introduces. `skills_error` makes the
    skills corpus raise the way a symlinked directory really does."""
    def __init__(self, entries, *, skills=(), skills_error=None, **kw):
        super().__init__(entries, **kw)
        self._skills, self._skills_error = list(skills), skills_error
        self.reads = []
    def read_evidence(self, kind, verified_only=True):
        self.reads.append((kind, verified_only))
        if kind == "skills":
            if self._skills_error:
                raise self._skills_error
            return self._skills
        return self._entries


def _shortlist_note():
    return Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})


def test_an_unreadable_skills_corpus_composes_without_it_and_says_so():
    """A framing-only corpus may never cost a lead (#167's rule, one layer out). The
    experience read is deliberately NOT wrapped: it is the gate's only citable
    evidence, and a bundle with no ids fails every bullet anyway."""
    v = SkillsVault(ENTRIES, skills_error=OSError("evidence directory is a symlink"))
    r = run_one(_shortlist_note(), v, _cfg(), FakeBackend([CLEAN_CV]), FakeCache(),
                renderer=FakeRenderer())
    assert r.status == "rendered", "a broken framing corpus binned the lead"
    assert r.skills_unreadable is True


def test_skills_reach_the_bundle_verified_only():
    """An `_inbox/` skill must never reach the composer: `verified:` is the trust root,
    and a propose-only write is exactly what has not been reviewed yet."""
    v = SkillsVault(ENTRIES, skills=[])
    run_one(_shortlist_note(), v, _cfg(), FakeBackend([CLEAN_CV]), FakeCache(),
            renderer=FakeRenderer())
    assert ("skills", True) in v.reads


def test_a_skill_reaches_the_composers_prompt():
    """The whole point of the issue: the corpus was inert, and this is what proves it
    is not. Asserts on the PROMPT the backend received, never on an internal."""
    be = FakeBackend([CLEAN_CV])
    v = SkillsVault(ENTRIES, skills=[
        {"title": "Example Platform Skill", "best_for": "platform", "body": "",
         "fields": {"Proficiency": "8 years", "Domain": "platform",
                    "Evidence": "shipped things", "Signal Value": "depth"}}])
    run_one(_shortlist_note(), v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert "SKILLS INVENTORY" in be.prompts[0]
    assert "Example Platform Skill" in be.prompts[0]


def test_a_refused_lead_never_reads_the_skills_corpus():
    """`skipped-stale` returns at engine.py:208, before the bundle build at :286, so a
    broken corpus costs nothing on a lead that was never going to compose. This guards
    the PLACEMENT, which is what makes that true: hoisting the read above the guards
    would spend a vault read on every refused lead and could raise before the refusal."""
    v = SkillsVault(ENTRIES, skills_error=OSError("would raise if reached"))
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "last_seen": "2000-01-01"})
    r = run_one(note, v, _cfg(), FakeBackend([CLEAN_CV]), FakeCache(),
                renderer=FakeRenderer(), policy=StalenessPolicy(lead_ttl_days=1))
    assert r.status == "skipped-stale"
    assert v.reads == [], f"a refused lead touched the evidence corpora: {v.reads}"
```

`ENTRIES`, `CLEAN_CV`, `FakeBackend`, `FakeCache`, `FakeRenderer` and `_cfg()` all already exist in `tests/test_cv_engine.py`; reuse them rather than inventing a harness. Confirm `FakeBackend`'s constructor and its `.prompts` attribute (line 142) and `StalenessPolicy`'s real parameter name before running these.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -k skills -q`
Expected: FAIL — `CvResult` has no `skills_unreadable`.

- [ ] **Step 3: Implement**

Add to `CvResult` beside `dossier_failed`:

```python
    # #165: the Skills Inventory could not be READ (a symlinked corpus, an unreadable
    # entry) and the CV was composed without its framing section. Visibility, never
    # control flow -- the exact shape `dossier_failed` above established. A missing
    # corpus is NOT this: `read_evidence` returns [] for one, which is the abstain
    # case and entirely normal.
    skills_unreadable: bool = False
```

Replace `engine.py:284-288`:

```python
        entries = vault.read_evidence("experience", verified_only=True)
        baseline = vault.read_baseline()
        # A broken SKILLS corpus must not cost a lead. `read_evidence` returns [] for a
        # MISSING directory, so the only way here is genuine breakage -- which `doctor`
        # already reports per-kind as DEAD. Letting it propagate would put a
        # framing-only corpus inside the same try as the experience read and fail every
        # lead in the batch; #167's rule is that a thing which only affects tailoring
        # QUALITY may never bin a lead.
        skills_unreadable = False
        try:
            skills = vault.read_evidence("skills", verified_only=True)
        except OSError as e:
            _log.warning("skills inventory for %s unreadable, composing without it: %s",
                         note.ref, e)
            skills, skills_unreadable = [], True
        b = _bundle.build_bundle(entries, baseline, cvcfg.negatives,
                                 _jd_keywords(role, jd), cvcfg.prefix_map, skills=skills)
```

Then thread `skills_unreadable=skills_unreadable` into every `CvResult(...)` constructed after this point in `run_one` (the `skipped-gate`, `dry-run`, `needs-signoff`, `skipped-has-cv` and `rendered` returns).

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -q` — expect PASS.

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -q -m "feat(cv): compose from the Skills Inventory, degrading if it is unreadable (#165)"
```

---

### Task 8: Split `cited_by_gate`, which this change makes non-equivalent for the first time

**Files:**
- Modify: `sluice/core/protocols.py:76-86,144-176`
- Modify: `sluice/core/doctor.py:385-412`
- Test: `tests/test_evidence_store.py:133-165`

**Why this task exists.** `EvidenceKind.cited_by_gate` means *"the CV fabrication gate READS this corpus"*, and `test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` derives the true set by grepping `cv/engine.py` for `read_evidence("<kind>")`. #164 wrote both on the assumption that *engine reads it* ⟺ *gate cites it*. Task 7 breaks that equivalence deliberately: the engine now reads `skills`, and the gate does NOT cite them. Flipping the flag to True would make `doctor` tell a user their skills are "citable by the CV fabrication gate" — false, and false in the reassuring direction #164 names as the worst.

- [ ] **Step 1: Write the failing test — derived from EXECUTION, not source-grepping**

Replace `test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` with two tests:

```python
def test_read_by_composer_names_exactly_the_kinds_the_cv_engine_reads():
    """Unchanged in mechanism from #164's version, retargeted at the new flag."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "sluice" / "cv" / "engine.py"
           ).read_text(encoding="utf-8")
    reached = set(re.findall(r"""read_evidence\(\s*["']([a-z]+)["']""", src))
    assert reached, ("the sweep found no evidence read in sluice/cv/engine.py -- the "
                     "matcher is broken, not the engine")
    assert reached == {k for k, s in EVIDENCE_KINDS.items() if s.read_by_composer}


def test_cited_by_gate_is_exactly_what_bundle_sources_actually_licenses():
    """#164 derived this by grepping the engine, on the assumption that a corpus the
    engine READS is a corpus the gate CITES. #165 breaks that: skills reach the prompt
    and are licensed nowhere. So derive it by EXECUTION instead -- build a bundle
    carrying one entry per kind with a distinct sentinel digit and ask
    `bundle_sources` which sentinels it licensed. A source grep cannot answer this;
    only running the derivation can."""
    from sluice.cv import bundle as B
    sentinels = {"experience": "8801", "skills": "8802"}
    b = B.build_bundle(
        [{"title": "t", "company": "Example Co", "best_for": "", "category": "",
          "metrics": sentinels["experience"], "body": ""}],
        "baseline", [], [], {},
        skills=[{"title": "s", "best_for": "", "body": "",
                 "fields": {"Domain": "", "Proficiency": sentinels["skills"],
                            "Evidence": "", "Signal Value": ""}}])
    s = B.bundle_sources(b)
    licensed = set().union(*s.nums.values(), s.baseline)
    actually_cited = {k for k, digit in sentinels.items() if digit in licensed}
    assert actually_cited == {k for k, sp in EVIDENCE_KINDS.items() if sp.cited_by_gate}


def test_every_cited_kind_is_also_read_by_the_composer():
    """The gate cannot cite a corpus the composer never put in the bundle."""
    for kind, spec in EVIDENCE_KINDS.items():
        if spec.cited_by_gate:
            assert spec.read_by_composer, f"{kind} is cited but never composed from"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_store.py -k "read_by_composer or cited_by_gate" -q`
Expected: FAIL — `EvidenceKind` has no `read_by_composer`.

- [ ] **Step 3: Implement**

In `sluice/core/protocols.py`, add the field to `EvidenceKind` beside `cited_by_gate`:

```python
    # Whether cv/engine.py puts this corpus in the composer's bundle at all. SPLIT from
    # `cited_by_gate` at #165, which made the two non-equivalent for the first time:
    # `skills` reaches the prompt as a FRAMING section whose digits `bundle_sources`
    # licenses nowhere. #164 wrote one flag because "read" and "cited" then coincided;
    # collapsing them again would make `doctor` tell a user their skills are citable,
    # which is the over-claim `cited_by_gate` was introduced to prevent.
    read_by_composer: bool = False
```

Set the registry entries: `experience` gets `read_by_composer=True, cited_by_gate=True`; `skills` gets `read_by_composer=True` (leaving `cited_by_gate` False); `stories` gets neither. Update the `#165` prose at `protocols.py:79-81`, `:95` and `:146` so it no longer says these kinds are waiting.

In `sluice/core/doctor.py`, correct both messages. At `:412`, key the "not read" wording on `read_by_composer` rather than `cited_by_gate`, and give a read-but-not-cited corpus its own accurate sentence:

```python
        if spec.cited_by_gate:
            detail = (f"{verified} verified / {total} total entries -- only verified "
                      f"entries are citable by the CV fabrication gate")
        elif spec.read_by_composer:
            # True after #165 for `skills`: the composer is shown them as framing, and
            # the gate licenses no figure from them. Saying "citable" here would be the
            # #164 M2 over-claim; saying "not read" would now be simply false.
            detail = (f"{verified} verified / {total} total entries -- shown to the CV "
                      f"composer as framing; not a citable source for the gate")
        else:
            detail = (f"{verified} verified / {total} total entries -- reviewed, but "
                      f"nothing reads this corpus yet")
```

At `:391`, `blocks=("cv",)` must stay keyed on `cited_by_gate`: an unreadable skills corpus no longer blocks `cv` (Task 7 degrades instead), so widening it to `read_by_composer` would over-claim in the other direction.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_evidence_store.py tests/test_doctor.py -q` — expect PASS.

```bash
git add sluice/core/protocols.py sluice/core/doctor.py tests/test_evidence_store.py
git commit -q -m "refactor(core): split read_by_composer from cited_by_gate (#165)"
```

---

### Task 9: Retire `read_experience_entries`

**Files:**
- Modify: `sluice/core/protocols.py:732-744` (delete the member)
- Modify: `sluice/core/vault.py:1756-1775` (delete the delegate)
- Modify: `tests/conformance/test_store_contract.py:345-380`, `tests/conformance/seeds.py:4`
- Modify: `tests/test_mcpserver.py:768,779,831,1271,1277`, `tests/test_cv_engine.py:38,1673`, `tests/test_app_operations.py:290`, `tests/test_core_vault_cv.py:34,46,66,70,80`, `tests/test_doctor.py:1710`, `tests/test_evidence_store.py:155-158,181`
- Modify: `sluice/core/doctor.py:387`

Its own docstring says **"EXPIRES AT #165 ... DELETE this member rather than inheriting it"**: a Protocol member is a REQUIRED member, so every future store would implement a second spelling of a call it already implements, for a caller that no longer exists (Task 7 removed it).

- [ ] **Step 1: Confirm there is no production caller left**

```bash
grep -rn "read_experience_entries" sluice/ --exclude-dir=__pycache__
```

Expected: only `core/protocols.py`, `core/vault.py`, and the comment at `core/doctor.py:387`. If `cv/engine.py` still appears, Task 7 is incomplete — stop.

- [ ] **Step 2: Delete the Protocol member and the Vault delegate**

Remove `read_experience_entries` from `sluice/core/protocols.py` and `sluice/core/vault.py` entirely. In `core/doctor.py:387`, replace the reference in the comment with `read_evidence("experience", ...)`, keeping the measured claim it records (a symlinked Experience Library raises rather than returning `[]`).

- [ ] **Step 3: Retarget every test**

Rename the conformance row to `test_read_evidence_honours_verified_only` and call `store.read_evidence("experience", verified_only=...)`. In `tests/test_mcpserver.py:1277`, replace `"read_experience_entries"` with `"read_evidence"` in the store-method roster. In the fake stores (`test_cv_engine.py:38`, `test_app_operations.py:290`, `test_mcpserver.py:779,831`), replace the method with `read_evidence(self, kind, verified_only=True)`. In `tests/test_evidence_store.py:155-158`, delete the `if "read_experience_entries(" in src` branch — the engine now names every kind as a string, which is the whole point.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. This task touches eight test files; a partial rename shows up here and nowhere else.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -q -m "refactor(core): retire read_experience_entries for read_evidence (#165)"
```

---

### Task 10: `doctor` reports a negative that contradicts the inventory

**Files:**
- Modify: `sluice/core/doctor.py` (new `classify_negatives_vs_skills`)
- Modify: `sluice/core/app.py:2171-2178` (wire it in)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `sluice.core.stem.stem_all` (Task 1); `Store.read_evidence("skills")`.
- Produces: `classify_negatives_vs_skills(negatives: list, skill_terms: set) -> list`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_negative_naming_a_held_skill_is_reported():
    """#165: cv.negatives is a hand-typed shadow of the Skills Inventory and drifts.
    A derived bundle line cannot stop the two disagreeing -- only making the
    disagreement visible can."""
    rows = D.classify_negatives_vs_skills(
        ["never claim documenting experience"], {"document", "platform"})
    assert len(rows) == 1
    assert rows[0].state == D.NOTICE
    assert "documenting" in rows[0].detail


def test_an_empty_inventory_abstains():
    """Empty-config-abstains. An install with no Skills Inventory must not have every
    negative reported as a contradiction."""
    assert D.classify_negatives_vs_skills(["never claim anything"], set()) == []


def test_an_empty_negatives_list_abstains():
    assert D.classify_negatives_vs_skills([], {"document"}) == []


def test_a_negative_about_something_not_in_the_inventory_is_not_reported():
    assert D.classify_negatives_vs_skills(
        ["never claim a security clearance"], {"document"}) == []


def test_the_match_survives_a_word_form_difference():
    """The whole reason this shares the stemmer: a negative saying 'documenting' and a
    skill whose Domain says 'documentation' are the same disagreement."""
    assert D.classify_negatives_vs_skills(["no documenting"], {stem("documentation")})
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -k negatives_vs_skills -q` — expect FAIL.

- [ ] **Step 3: Implement**

```python
def classify_negatives_vs_skills(negatives: list, skill_terms: set) -> list:
    """One NOTICE per configured `cv.negatives` string that names a skill the verified
    Skills Inventory actually holds (#165).

    `cv.negatives` is prose asserting which technologies the candidate does and does
    not work in, maintained by hand and separately from the inventory that already
    answers that. The bundle's derived cross-reference cannot stop the two disagreeing
    -- it names nothing, so it adds a third voice rather than replacing the stale one.
    This is what makes the disagreement visible.

    NOTICE, never DEGRADED: a contradiction is a fact worth knowing before a compose,
    and it must never affect the exit code -- `--strict` in a cron job failing because
    somebody's negative overlaps their inventory is the 672ad2a class aimed at the
    tool's own exit status. Same posture `classify_gate` already takes.

    Abstains on either empty input, which is the empty-config-abstains rule: an install
    with no inventory has nothing to contradict.

    Matching is on STEMS via `core/stem.py`, so a negative saying "documenting" and a
    skill whose Domain says "documentation" are recognised as the same disagreement --
    the word-form defect this issue also fixes in `bundle.rank()`.
    """
    if not negatives or not skill_terms:
        return []
    out = []
    for neg in negatives:
        overlap = stem_all(neg) & skill_terms
        if overlap:
            out.append(ComponentCheck(
                "gates", "cv.negatives", NOTICE,
                f"contradicts the verified Skills Inventory: {neg!r} names "
                f"{sorted(overlap)}, which the inventory holds -- the composer is told "
                f"both. Remove the line, or remove the skill."))
    return out
```

with `from sluice.core.stem import stem_all` at the top of `core/doctor.py`, plus the
term-derivation helper beside it, so `core/app.py` never imports the stemmer and its
consumers stay the two modules the spec names:

```python
def skill_terms(entries: list) -> set:
    """The comparable stem set for a verified Skills Inventory read.

    Here rather than at the call site so `core/app.py` -- an orchestrator -- does not
    have to know that matching is stemmed at all, and so the derivation cannot be
    spelled differently by a second caller later. `best_for` is the floor key
    `EVIDENCE_KINDS["skills"]` maps onto `Domain`; `title` is the skill's own name.
    """
    terms = set()
    for e in entries or []:
        terms |= stem_all(e.get("best_for", ""))
        terms |= stem_all(e.get("title", ""))
    return terms
```

- [ ] **Step 4: Wire it into `Sluice.doctor()`**

In `sluice/core/app.py`, inside the existing `else:` branch after `classify_store`:

```python
                # #165. Needs BOTH the store and cv_cfg, which is why it lives here and
                # not in `Vault.preflight()` -- whose docstring commits it to counts
                # rather than content, and which is a Store-seam member every
                # implementation would have to grow.
                if cv_cfg is not None:
                    try:
                        components.extend(_doctor.classify_negatives_vs_skills(
                            cv_cfg.negatives,
                            _doctor.skill_terms(
                                store.read_evidence("skills", verified_only=True))))
                    except Exception as e:  # noqa: BLE001 -- an unreadable corpus is
                        # already reported DEAD by classify_store above; this check
                        # must not turn that into a crash of the diagnostic tool.
                        _log.debug("negatives/skills cross-check skipped: %s", e)
```

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q` — expect PASS.

```bash
git add sluice/core/doctor.py sluice/core/app.py tests/test_doctor.py
git commit -q -m "feat(doctor): report a negative that contradicts the Skills Inventory (#165)"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `sluice.yaml.example`, `.rulesync/rules/CLAUDE.md`

- [ ] **Step 1: `docs/ARCHITECTURE.md`**

In the CV section, describe the four bundle sections and state that `bundle_sources` walks `bundle["entries"]` alone, which is what makes skills non-citable. In the evidence paragraph, record that `skills` is now `read_by_composer` but not `cited_by_gate`, and that `read_experience_entries` is gone.

- [ ] **Step 2: `docs/CONFIGURATION.md` and `sluice.yaml.example`**

Under `cv.negatives`, state that a skills-shaped negative is now redundant with the Skills Inventory, that the bundle derives the cross-reference automatically, and that `doctor` reports a contradiction. Keep the key documented — it still carries negatives no inventory can express.

- [ ] **Step 3: `.rulesync/rules/CLAUDE.md`**

Update the CV-gate paragraph: the bundle has four sections; skills license numbers in neither pool; `EvidenceKind` carries two flags and what each means. **Edit `.rulesync/rules/CLAUDE.md`, never `CLAUDE.md`.**

- [ ] **Step 4: Regenerate and verify no drift**

```bash
npm ci --ignore-scripts && npm run rulesync
git status --short          # CLAUDE.md/AGENTS.md are gitignored; expect no tracked churn
.venv/bin/python -m pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add docs/ sluice.yaml.example .rulesync/
git commit -q -m "docs(cv): describe the fourth bundle section and the two evidence flags (#165)"
```

---

## Final verification

- [ ] `.venv/bin/python -m pytest -q` — full suite green
- [ ] `ruff check sluice tests scripts` — clean (install the CI pin: `pip install ruff==0.15.21`)
- [ ] `.venv/bin/python -m pytest --cov` — coverage reports, does not gate
- [ ] `grep -rn "until #165\|#165 lands\|EXPIRES AT #165\|wait on #165" sluice/ docs/` — returns nothing
- [ ] `git log --oneline origin/main..HEAD` — the re-freeze (Task 5) is its own commit
- [ ] Run `/review-pr` BEFORE pushing. CodeRabbit is the scarce resource (~1/hour, adaptive); the local specialist team is free and parallel.
