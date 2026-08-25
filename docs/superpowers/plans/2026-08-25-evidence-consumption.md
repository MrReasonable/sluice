# Skills Inventory Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the user's verified Skills Inventory in front of the CV composer as a fourth, non-citable bundle section; derive the skills-shaped negative constraint instead of hand-typing it; and stop `bundle.rank()` missing an entry because the ad said "documenting" and the entry said "documentation".

**Architecture:** `cv/bundle.py` grows a `skills` parameter whose lines `render_bundle` owns and `bundle_sources` never sees, so non-citability is structural. The ADVISORY audit is handed the bundle without that section, so it keeps judging against exactly the sources it judges against today. `sluice/core/stem.py` adds a Porter stemmer that `rank()` and a new `doctor` check both use. `cv/engine.py` switches to reading evidence BY KIND, which retires the `read_experience_entries` delegate and splits `EvidenceKind.cited_by_gate` into two flags this change makes non-equivalent.

**Tech Stack:** Python 3.12+, standard library only inside `sluice/`. pytest. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-08-25-evidence-consumption-design.md` — read it first, D1-D11. This plan is revision 2, after a five-reviewer `/review-plan` round returned 49 findings (0 Critical, 21 High).

## Global Constraints

- **`sluice/` is standard-library only.** The stemmer is hand-written for this reason.
- **Neutrality:** no employer names, role preferences, locations, contact details, hostnames or absolute paths in `sluice/` or `tests/`. **Every `Example <Word>` literal in a `test_cv_*.py` module is swept by `_CV_IDENTITY_RE` (`tests/test_fixture_name_neutrality.py:1492`) and must already be on `_REVIEWED_FIXTURE_IDENTITIES` (:199).** This plan therefore reuses only rostered identities — `Example Cloud`, `Example Data`, `Example Candidate`, `Example Co`, `Example Foundry`. Do NOT add roster entries, and do NOT widen `_CV_IDENTITY_EXEMPT`: that ratchet exists to force a human call, and sidestepping it is the weakening it guards against.
- **Conventional commits** on every commit, `wip:` included — release-please reads the subjects.
- **Never widen `cv/validate.py`.** Skills license numbers in NEITHER pool.
- **`_entry_block`'s rule is inviolate:** every line it returns is a source for that entry. The framing lines are NOT a `_block` and must never be folded into one.
- **Run before mutation testing:** `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- **Never mutate the repo to witness a mutant** — copy the function to `/tmp` and mutate the copy. A `git checkout` restore has wiped uncommitted work here before.
- **Interpreter:** `.venv/bin/python` explicitly, never a bare `python` shim.
- **Edit `.rulesync/rules/CLAUDE.md`, never `CLAUDE.md`** (generated), then `npm ci --ignore-scripts && npm run rulesync`.

## Task order is load-bearing

Revision 1 put the engine change before the registry change and before the fake-store migration, which left **76 tests red across 4 files** at two intermediate commits while both steps claimed "expect PASS". Four reviewers found it independently. The order below has no red commit:

```
1 stemmer -> 2 rank -> 3 skills section -> 4 derived negative -> 5 re-freeze -> 6 prompt rule
  -> 7 REGISTRY flag split (no engine dependency)
  -> 8 ENGINE by-kind read + fake-store migration + audit split   (one commit, green)
  -> 9 flag derivation tests -> 10 retire the delegate -> 11 doctor -> 12 docs
```

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

Revision 1's block began `cd /tmp` and never returned, so it wrote to `/tmp/tests/data/` and Step 7's `git add` failed on pathspec — found by three reviewers. This version resolves the repo root explicitly and never relies on the working directory.

```bash
repo="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d)"
# -f so an HTTP error page is not silently saved AS the corpus.
curl -fsSL -o "$tmp/voc.txt" https://tartarus.org/martin/PorterStemmer/voc.txt
curl -fsSL -o "$tmp/out.txt" https://tartarus.org/martin/PorterStemmer/output.txt
test "$(wc -l < "$tmp/voc.txt")" = "$(wc -l < "$tmp/out.txt")" || { echo "LENGTH MISMATCH"; exit 1; }
# Row shape, checked at BUILD time as well as asserted in the suite: one lowercase
# alphabetic word per line. This is what forecloses an email, URL, path or capitalised
# identity entering tests/ inside a 353KB file nobody will read.
grep -nvE '^[a-z]+$' "$tmp/voc.txt" && { echo "NON-WORD ROW IN voc.txt"; exit 1; }
grep -nvE '^[a-z]*$' "$tmp/out.txt" && { echo "NON-WORD ROW IN output.txt"; exit 1; }
mkdir -p "$repo/tests/data"
{ echo "# Porter stemmer test vocabulary -- VERBATIM third-party corpus, do not edit."
  echo "# Source: https://tartarus.org/martin/PorterStemmer/ (voc.txt + output.txt)"
  echo "# Author: Martin Porter. Captured 2026-08-25. That page licenses the algorithm"
  echo "# encodings 'free of charge for any purpose'; it states no separate terms for"
  echo "# these test files, and we redistribute them on the reading that they share it."
  echo "# NOT a sluice fixture: no neutrality sweep may 'clean' a word here. The corpus"
  echo "# is worth something only while it is byte-identical to the reference."
  echo "# Format: <word> <expected stem>, one pair per line."
  paste -d' ' "$tmp/voc.txt" "$tmp/out.txt"; } > "$repo/tests/data/porter_vocabulary.txt"
wc -l "$repo/tests/data/porter_vocabulary.txt"      # expect 23539 (23531 rows + 8 header lines)
shasum -a 256 "$repo/tests/data/porter_vocabulary.txt"   # record this for Step 2's digest pin
```

The header is EIGHT `echo` lines, not seven. Revision 1 said 23538; three reviewers counted 23539.

- [ ] **Step 2: Write the failing test**

`tests/test_core_stem.py`. Three guards, each closing a different way this could certify nothing.

```python
"""The stemmer is certified against Martin Porter's own published vocabulary rather than
against examples chosen here. A table of cases the author picked certifies nothing."""
import hashlib
import pathlib
import re

import pytest

from sluice.core.stem import stem, stem_all, tokens

_CORPUS = pathlib.Path(__file__).resolve().parent / "data" / "porter_vocabulary.txt"

# Paste the Step 1 shasum here. Without it, regenerating the expected-stem column FROM
# `stem()` makes the equality below assert that the code equals itself -- forever, inside
# a 353KB diff nobody reads. Same ratchet as `_REVIEWED_CORPUS_DIGESTS` in
# tests/test_fixture_name_neutrality.py. Updating it is the deliberate act.
_CORPUS_SHA256 = "<paste from Step 1>"


def _rows():
    out = []
    for line in _CORPUS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        word, expected = line.split(" ", 1)
        out.append((word, expected))
    return out


def test_the_corpus_is_the_reference_corpus_unmodified():
    assert hashlib.sha256(_CORPUS.read_bytes()).hexdigest() == _CORPUS_SHA256, (
        "tests/data/porter_vocabulary.txt has changed. It is a VERBATIM third-party "
        "corpus; if you regenerated its stem column from stem(), the equality test "
        "below now certifies that the code equals itself.")


def test_the_corpus_is_present_and_whole():
    """SCOPE, not violations. A corpus that failed to load leaves every assertion over it
    iterating an empty list -- green forever, this repo's `all([])` trap."""
    assert len(_rows()) == 23531


def test_every_corpus_row_is_two_lowercase_words():
    """Structural neutrality, asserted rather than measured once at authoring time. One
    line forecloses any email, URL, absolute path or capitalised identity entering tests/
    through this file, with no blocklist to maintain."""
    bad = [r for r in _rows() if not re.fullmatch(r"[a-z]+ [a-z]*", " ".join(r))]
    assert not bad, f"non-word rows in the corpus: {bad[:5]}"


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

`sluice/core/stem.py`:

```python
# sluice/core/stem.py
"""Porter (1980) suffix stripping, so JD keywords match evidence across word forms.

Hand-written because `sluice/` is standard-library only (CLAUDE.md), and because the
alternatives were measured and are worse: an ad-hoc suffix list is unprincipled
(`deployment` -> `deploym` but `deployments` -> `deplo`), and every common-prefix
threshold tried had false positives AND misses. See the spec's D7.

Certified against Martin Porter's published 23,531-word vocabulary at 100.0000%
(tests/data/porter_vocabulary.txt). That corpus buys a ONE-TIME validation of this
implementation against the reference; it is not drift detection of the reference.

Consumers: `cv/bundle.py:rank` and `core/doctor.py`. Deliberately NOT
`core/relevance.py`, whose keep/drop lists are a user-specified ingest gate applied
before dedup -- widening that match silently changes which leads are discarded, the
672ad2a failure.
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
# them, agreement with the published vocabulary is 99.932%, and every one of the 16
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
    """The Porter stem of one word. Lowercases; words of 2 letters or fewer are returned
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
    """Lowercased alphabetic runs. The tokeniser both sides of a match must share -- a
    keyword stemmed against an unstemmed haystack matches nothing."""
    return _WORD_RE.findall((text or "").lower())


def stem_all(text):
    """The set of stems in `text`. The comparable form for keyword matching."""
    return {stem(t) for t in tokens(text)}
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_core_stem.py -q`
Expected: PASS. Paste the Step 1 `shasum` into `_CORPUS_SHA256` first, or
`test_the_corpus_is_the_reference_corpus_unmodified` fails on the placeholder.

- [ ] **Step 6: Witness that the corpus test is load-bearing**

Mutate by DELETING, in a `/tmp` COPY. Never mutate the repo — a `git checkout` restore has wiped uncommitted work here before, and `sed -i ''` is the BSD spelling that silently creates a backup file named `-e` on GNU sed.

```bash
mkdir -p /tmp/stemwitness && cp sluice/core/stem.py /tmp/stemwitness/mutant.py
# Delete the `logi -> log` rule: witnessed by exactly ONE word in 23,531 (`apology`).
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("/tmp/stemwitness/mutant.py"); s = p.read_text()
old = '''          ("logi", "log")]'''
assert old in s, "anchor missed -- the witness would report a false SURVIVED"
p.write_text(s.replace(old, "          ]"))
EOF
.venv/bin/python - <<'EOF'
import sys, pathlib, re
sys.path.insert(0, "/tmp/stemwitness")
import mutant
rows = [l.split(" ", 1) for l in pathlib.Path("tests/data/porter_vocabulary.txt")
        .read_text().splitlines() if not l.startswith("#") and l.strip()]
bad = [(w, e) for w, e in rows if mutant.stem(w) != e]
print(f"mutant disagreements: {len(bad)} -> {'KILLED' if bad else 'SURVIVED (BAD)'}")
EOF
rm -rf /tmp/stemwitness
```

Expected: **KILLED** (at least one disagreement). `SURVIVED` means the corpus did not load — check Step 1's row count before believing any later result.

- [ ] **Step 7: Commit**

```bash
git add sluice/core/stem.py tests/test_core_stem.py tests/data/porter_vocabulary.txt
git commit -q -m "feat(core): add a Porter stemmer for keyword matching (#165)"
```

---

### Task 2: `rank()` matches on stems

**Files:**
- Modify: `sluice/cv/bundle.py:30-36`
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: `sluice.core.stem.stem`, `sluice.core.stem.stem_all` (Task 1).
- Produces: `rank(entries, jd_keywords)` — unchanged signature, stem-based scoring.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv_bundle.py`. The entry must BEAT scoring competitors — a two-entry probe where both score 0 proves nothing, since `sorted` is stable and merely preserves input order. That mistake is recorded in the spec.

```python
def _rank_entry(best_for, title):
    return {"title": title, "company": "Example Co", "best_for": best_for,
            "category": "", "metrics": "", "body": ""}


def test_a_word_form_mismatch_no_longer_buries_the_right_entry():
    """#165's comment. The ad's top requirement was 'documenting'; the one entry that
    evidenced it said 'documentation'. `"documenting" in "documentation"` is False, so it
    scored zero and ranked BELOW every unrelated entry that matched a different ad word.
    Measured before the fix: position 6 of 7."""
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
    """`"java" in "javascript"` is True, so the old ranker scored a JavaScript entry on a
    Java keyword. Stems do not relate them."""
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

    Matching is on STEMS, both sides, so "documenting", "documentation" and "documented"
    all rank the same entry (#165). Before this it was raw substring containment, which
    missed every inflection AND related words it should not ("java" in "javascript").

    Orders, never excludes: the FULL verified set is emitted either way, so a ranking
    change can never lose evidence -- only move it. It DOES change which `[id]` an entry
    receives, since `assign_codes` runs after this.

    The haystack stays `best_for`/`category`/`title` and deliberately excludes `body`:
    matching into free prose lets a long entry out-score a precise one on volume.
    """
    wanted = {_stem(k) for k in jd_keywords}

    def score(e):
        hay = f"{e.get('best_for','')} {e.get('category','')} {e.get('title','')}"
        return len(wanted & _stem_all(hay))

    return sorted(entries, key=score, reverse=True)
```

and add at the top of `sluice/cv/bundle.py`:

```python
from sluice.core.stem import stem as _stem, stem_all as _stem_all
```

- [ ] **Step 4: Run the full bundle suite**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q`
Expected: PASS, **including both frozen tests**. Measured: `_frozen_bundle()` passes `jd_keywords=[]`, so every score is 0, the stable sort preserves order, and the frozen literal is untouched by this task. If a frozen test reddens here, the ranker changed something it should not have — do not re-freeze to fix it.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "fix(cv): rank evidence on stems, not raw substrings (#165)"
```

---

### Task 3: Skills as a fourth, non-citable bundle section

**Files:**
- Modify: `sluice/cv/bundle.py` (`build_bundle`, `render_bundle`, new `_framing_lines`)
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: Task 2's `rank`.
- Produces: `build_bundle(entries, baseline, negatives, jd_keywords, prefix_map, skills=())`, `bundle["skills"]`, and `render_bundle(bundle, *, include_framing=True)`.

**Naming note.** The helper is `_framing_lines`, NOT `_skills_block`. `cv/bundle.py` has already established `_<x>_block(...) -> list[str]` as meaning *every line returned is a SOURCE for the fabrication gate* — that is `_entry_block`'s and `_baseline_block`'s stated contract. A third `_block` whose lines are deliberately NOT sources invites exactly the mistake this feature must not make.

- [ ] **Step 1: Write the failing tests**

Fixture identities are `Example Cloud` (rostered at `test_fixture_name_neutrality.py:203`) — do not invent new ones.

```python
_SKILL = {"title": "Example Cloud Skill", "best_for": "platform documentation",
          "company": "", "category": "", "metrics": "", "body": "Body prose.",
          "fields": {"Proficiency": "8 years", "Domain": "platform documentation",
                     "Evidence": "shipped 62 things", "Signal Value": "depth not breadth"}}


def _bundle_with_skills(skills=(_SKILL,)):
    return B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES,
                          [], FROZEN_PREFIX_MAP, skills=list(skills))


def test_a_skills_digit_is_licensed_in_neither_pool():
    """THE load-bearing test of this feature, and it compares against NO frozen literal --
    so re-capturing FROZEN_BUNDLE_TEXT cannot bring it back into sync. `8` and `62` are
    the skill's own figures; neither may become a permitted number anywhere."""
    s = B.bundle_sources(_bundle_with_skills())
    assert "62" not in s.baseline
    assert all("62" not in n for n in s.nums.values())
    assert all("8" not in n for n in s.nums.values()), (
        "a skills digit reached an entry's allowlist -- the framing lines have been "
        "folded into _entry_block, which licenses them for that entry")


def test_the_skills_section_renders_after_the_entries_and_before_the_negatives():
    text = B.render_bundle(_bundle_with_skills())
    assert text.index("[AL2]") < text.index("=== SKILLS INVENTORY") \
           < text.index("=== NEGATIVE CONSTRAINTS")


def test_the_skills_section_carries_the_four_fields_and_the_body():
    text = B.render_bundle(_bundle_with_skills())
    for fragment in ("Example Cloud Skill", "proficiency=8 years",
                     "signal=depth not breadth", "shipped 62 things", "Body prose."):
        assert fragment in text, fragment


def test_an_empty_inventory_emits_no_header_at_all():
    """Not an empty header: that asserts to the model that the candidate has no skills,
    which is a negative claim it may act on. Empty means abstain."""
    assert "SKILLS INVENTORY" not in B.render_bundle(_bundle_with_skills(skills=()))


def test_include_framing_false_omits_the_section_but_keeps_every_source():
    """D11: the ADVISORY audit is handed this spelling, so it keeps judging against
    exactly the sources it judges against today. Every non-framing line must survive --
    asserting only the absence would pass for a function that returned ''."""
    b = _bundle_with_skills()
    text = B.render_bundle(b, include_framing=False)
    assert "SKILLS INVENTORY" not in text
    assert "Example Cloud Skill" not in text
    for fragment in ("=== BASELINE CV", "[AL1]", "[BE1]", "[AL2]",
                     "=== VERIFIED EXPERIENCE ENTRIES", "=== NEGATIVE CONSTRAINTS"):
        assert fragment in text, fragment
    assert text == B.render_bundle(B.build_bundle(
        FROZEN_ENTRIES, FROZEN_BASELINE, FROZEN_NEGATIVES, [], FROZEN_PREFIX_MAP))
```

**Revision 1 also proposed `test_the_pre_174_oracle_still_agrees_when_skills_are_present`. It is DELETED, not fixed.** It fed `_oracle(B.render_bundle(b))`, the self-certifying spelling `_oracle`'s own docstring forbids; a reviewer measured 3 of 3 co-variant `_entry_block` deletion mutants (`drop_title`, `drop_company`, `drop_body`) surviving it. Task 5 re-freezes the literal *with* skills present, so the EXISTING `test_the_allowlist_still_matches_the_frozen_prompt` covers the skills case correctly and for free.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k skill -q`
Expected: FAIL — `build_bundle() got an unexpected keyword argument 'skills'`.

- [ ] **Step 3: Implement**

In `sluice/cv/bundle.py`:

```python
def _framing_lines(skill: dict) -> list[str]:
    """The lines ONE skills entry contributes to the COMPOSER's prompt.

    Deliberately NOT named `_skills_block`. In this module `_entry_block` and
    `_baseline_block` carry a stated contract -- every line returned is a SOURCE the
    fabrication gate may license -- and these lines are the opposite of that. Nothing
    harvests from here: `bundle_sources` walks `bundle["entries"]` and never touches
    `bundle["skills"]`, which is what makes a skills figure licensed nowhere (#165).
    Folding these into `_entry_block`, or teaching `bundle_sources` to read them,
    licenses every skills digit at once;
    `test_a_skills_digit_is_licensed_in_neither_pool` is what catches that.

    Reads `fields` by the kind's own frontmatter names rather than the floor keys:
    `EVIDENCE_KINDS["skills"]` maps only `best_for <- Domain`, so Proficiency, Evidence
    and Signal Value have no floor analogue and are reachable only here.
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

Change `build_bundle`'s signature to `(entries, baseline, negatives, jd_keywords, prefix_map, skills=())` and add to its returned dict:

```python
            # Ranked by the same JD keywords so the most relevant framing leads -- but
            # NOT code-assigned: an [id] is what makes a thing citable, and the whole
            # point of this section is that it is not. Defaults to () so every existing
            # caller and test constructs a bundle unchanged.
            "skills": rank(list(skills), jd_keywords),
```

And in `render_bundle`, add the keyword-only parameter and the section:

```python
def render_bundle(bundle: dict, *, include_framing: bool = True) -> str:
    ...
    `include_framing=False` omits the SKILLS INVENTORY section and changes nothing else.
    That spelling exists for ONE caller: the #60 ADVISORY audit (`cv/engine.py`, via
    `cv/audit.py`), whose prompt opens "SOURCE BUNDLE is the ONLY truth". Handing the
    auditor the framing section would make a CV claim resting on a skills line alone read
    as SUPPORTED -- where today it is `unsupported` and, at the shipped
    `cv.require_signoff: true`, withholds the send-ready pointer until a human signs off.
    The spec's D3 says such a claim is illegitimate, so widening the auditor's source set
    would disarm the one layer that catches it (spec D11).
```

then, between the entry loop and the negatives header:

```python
    # After the entries it frames, before the hard "must NOT appear" list. Placement is
    # measured, not stylistic: emitted BEFORE the entries, the pre-#174 oracle in
    # tests/test_cv_bundle.py folds these digits into `baseline` and disagrees with
    # `bundle_sources`. Omitted ENTIRELY when empty -- an empty header would assert to
    # the model that the candidate holds no skills.
    if include_framing and bundle.get("skills"):
        lines += ["=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ==="]
        for sk in bundle["skills"]:
            lines += _framing_lines(sk)
        lines.append("")
    lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q`
Expected: PASS. Both frozen tests still pass, because `_frozen_bundle()` passes no `skills` yet and the section is omitted when empty.

- [ ] **Step 5: Witness the non-citability test in a /tmp copy**

```bash
mkdir -p /tmp/bundlewitness && cp sluice/cv/bundle.py /tmp/bundlewitness/mutant.py
```

In the COPY only, MOVE the framing lines into the harvested set (make `bundle_sources` also walk `bundle["skills"]`) — the mutation the guard exists for. Import the copy in a throwaway script and assert a skills digit now appears in `nums`. Confirm it would fail `test_a_skills_digit_is_licensed_in_neither_pool`, then `rm -rf /tmp/bundlewitness`. **Do not mutate `sluice/cv/bundle.py`.**

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "feat(cv): emit the Skills Inventory as a non-citable bundle section (#165)"
```

---

### Task 4: The derived negative constraint

**Files:**
- Modify: `sluice/cv/bundle.py` (`build_bundle`, new `_DERIVED_NEGATIVE`)
- Test: `tests/test_cv_bundle.py`

- [ ] **Step 1: Write the failing test**

Revision 1's line omitted the BASELINE CV, which contradicts Task 6's own prompt rule and would tell the composer to drop every technology named only in the user's real CV. The three sources must match D3 exactly.

```python
def test_the_derived_constraint_appears_only_with_a_non_empty_inventory():
    assert _bundle_with_skills()["negatives"][0] == B._DERIVED_NEGATIVE
    assert B._DERIVED_NEGATIVE not in _bundle_with_skills(skills=())["negatives"]


def test_configured_negatives_survive_alongside_the_derived_one():
    """cv.negatives stays: an inventory cannot express a negative that is not about
    skills at all ('never claim a security clearance')."""
    b = B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE, ["never claim 91 users"],
                       [], FROZEN_PREFIX_MAP, skills=[_SKILL])
    assert b["negatives"] == [B._DERIVED_NEGATIVE, "never claim 91 users"]


def test_the_derived_constraint_permits_every_source_the_prompt_permits():
    """It sits in the most strongly worded block in the prompt, so a source it forgets is
    a source the composer drops. compose._RULES permits the BASELINE CV and the VERIFIED
    EXPERIENCE ENTRIES; the SKILLS INVENTORY is named because it is visible and must be
    excluded from the CLAIM set without being excluded from the emphasis set."""
    for source in ("SKILLS INVENTORY", "VERIFIED EXPERIENCE ENTRIES", "BASELINE CV"):
        assert source in B._DERIVED_NEGATIVE, source


def test_the_derived_constraint_names_no_skill_and_so_cannot_go_stale():
    """A cross-reference, not a generated roster: a roster would duplicate the SKILLS
    section immediately above it and grow without bound."""
    assert "Example Cloud" not in B._DERIVED_NEGATIVE
    assert "platform" not in B._DERIVED_NEGATIVE


def test_the_derived_constraint_reaches_no_number_pool():
    """#31: the negatives block is shown to the model and is deliberately not citable.
    The derived line is prose in that block and must inherit that exactly."""
    s = B.bundle_sources(B.build_bundle(FROZEN_ENTRIES, FROZEN_BASELINE,
                                        ["never claim 91 users"], [], FROZEN_PREFIX_MAP,
                                        skills=[_SKILL]))
    assert "91" not in s.baseline and all("91" not in n for n in s.nums.values())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -k derived -q`
Expected: FAIL — `module 'sluice.cv.bundle' has no attribute '_DERIVED_NEGATIVE'`.

- [ ] **Step 3: Implement**

Add above `build_bundle`:

```python
# The skills-shaped negative, DERIVED rather than hand-typed (#165). `cv.negatives` is a
# prose shadow of the Skills Inventory and drifts from it; this line names no skill, so it
# cannot go stale. It names all THREE permitted sources, matching compose._RULES exactly:
# an omitted source here reads to the composer as a source it must not use, and this line
# sits in the most strongly worded block in the prompt. It does NOT, on its own, stop a
# stale CONFIGURED negative disagreeing with the inventory -- `core/doctor.py`'s
# classify_negatives_vs_skills is what makes that disagreement visible.
_DERIVED_NEGATIVE = ("claim no technology, language, framework or tool that is not named "
                     "in the BASELINE CV, the VERIFIED EXPERIENCE ENTRIES or the SKILLS "
                     "INVENTORY above")
```

and in `build_bundle`, replace the `negatives` value with `derived + list(negatives)` where `derived = [_DERIVED_NEGATIVE] if ranked_skills else []` (binding `ranked_skills = rank(list(skills), jd_keywords)` once and reusing it for the `"skills"` key).

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q` — expect PASS.

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py
git commit -q -m "feat(cv): derive the skills negative constraint instead of typing it (#165)"
```

---

### Task 5: Re-freeze `FROZEN_BUNDLE_TEXT` — its own commit, readable diff

**Files:**
- Modify: `tests/test_cv_bundle.py` (`FROZEN_SKILLS`, `_frozen_bundle`, `FROZEN_BUNDLE_TEXT`, the sentinel test)

This task exists ONLY so the freeze diff is reviewable in isolation. `_entry_block`'s docstring is explicit that re-capturing is how a widening launders through green: both frozen tests move with the mutant and stay green. A human reading THIS diff is the control.

- [ ] **Step 1: Give `_frozen_bundle` a skills entry**

`Example Data` is rostered (`test_fixture_name_neutrality.py:204`). Sentinels `71`/`72` collide with nothing in the existing literal (which uses 21/22, 31-38, 41-47, 51-53, 91-92) — verify that before capturing.

```python
FROZEN_SKILLS = [{"title": "Example Data Skill", "best_for": "platform", "body": "",
                  "fields": {"Proficiency": "71 years", "Domain": "platform",
                             "Evidence": "shipped 72 things", "Signal Value": "depth"}}]


def _frozen_bundle():
    return B.build_bundle(entries=FROZEN_ENTRIES, baseline=FROZEN_BASELINE,
                          negatives=FROZEN_NEGATIVES, jd_keywords=[],
                          prefix_map=FROZEN_PREFIX_MAP, skills=FROZEN_SKILLS)
```

- [ ] **Step 2: Regenerate the literal and READ THE DIFF**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'tests')
import test_cv_bundle as T
from sluice.cv import bundle as B
print(B.render_bundle(T._frozen_bundle()))
"
```

Paste the output into `FROZEN_BUNDLE_TEXT`, then `git diff tests/test_cv_bundle.py`.

**Read it.** The ONLY additions must be the derived negative line, the `=== SKILLS INVENTORY ... ===` header and its one entry. If any existing entry line changed, a source was widened or narrowed — stop and investigate rather than accepting the capture.

- [ ] **Step 3: Extend the literal-independent sentinel test**

Append to `test_bundle_sources_sentinels_hold_independent_of_the_frozen_literal`:

```python
    # The skills sentinels must be absent from EVERY pool. This assertion compares against
    # no literal, so re-freezing cannot bring it back into sync -- it is the one check a
    # re-capture cannot silently move.
    for sentinel in ("71", "72"):
        assert sentinel not in s.baseline
        assert all(sentinel not in n for n in s.nums.values())
```

- [ ] **Step 4: Run and commit alone**

Run: `.venv/bin/python -m pytest tests/test_cv_bundle.py -q` — expect PASS, including `test_the_allowlist_still_matches_the_frozen_prompt`, which now covers the skills-present case against the frozen literal (the correct, non-self-certifying oracle).

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

Use the module's existing `_NAME` fixture (`Example Candidate`, rostered) — do not introduce a new identity literal.

```python
def test_the_prompt_forbids_quoting_a_number_from_the_skills_section():
    """Without this the model is told the bundle is 'the ONLY permitted source' and shown
    `Proficiency: 8 years`, which it will reasonably use -- earning INVENTED PROFILE
    METRIC and, if the retry repeats it, a skipped lead. The trap is ours to close
    (#165, spec D3)."""
    prompt = C.build_prompt("BUNDLE", "JD", "Example Co", "Role", name=_NAME)
    assert "SKILLS INVENTORY" in prompt
    assert "never quote a number from it" in prompt


def test_the_prompt_permits_the_same_three_sources_the_derived_negative_does():
    """The derived negative (cv/bundle.py:_DERIVED_NEGATIVE) and this rule appear in the
    same prompt. A source named by one and not the other is a contradiction the composer
    resolves by dropping content."""
    from sluice.cv.bundle import _DERIVED_NEGATIVE
    prompt = C.build_prompt("BUNDLE", "JD", "Example Co", "Role", name=_NAME)
    for source in ("BASELINE CV", "VERIFIED EXPERIENCE ENTRY"):
        assert source in prompt, source
    assert "BASELINE CV" in _DERIVED_NEGATIVE
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_compose.py -k skills -q` — expect FAIL.

- [ ] **Step 3: Implement**

Add to `_RULES` in `sluice/cv/compose.py`, immediately after the "Every WORK EXPERIENCE bullet MUST end with a citation" rule:

```
- The SKILLS INVENTORY section is FRAMING, not a source. Use it to choose which experience entries to lead with and how to describe them. Never cite it, never quote a number from it, and never introduce a claim that rests on it alone: every fact in the CV must still come from the BASELINE CV or a VERIFIED EXPERIENCE ENTRY.
```

- [ ] **Step 4: Run the whole compose suite and commit**

Run: `.venv/bin/python -m pytest tests/test_cv_compose.py tests/test_prompt_neutrality.py -q`
Expected: PASS, including `test_the_prompt_names_exactly_the_phrases_the_gate_enforces` (this rule adds no `slop._PHRASES` stem) and the prompt-neutrality sweep.

```bash
git add sluice/cv/compose.py tests/test_cv_compose.py
git commit -q -m "feat(cv): tell the composer the skills section is framing, not a source (#165)"
```

---

### Task 7: Split the registry flag — BEFORE the engine change

**Files:**
- Modify: `sluice/core/protocols.py` (`EvidenceKind`: docstring at :62 and :76-97, the field, `__post_init__`, the registry at :144-176)
- Modify: `sluice/core/doctor.py:385-412`
- Test: `tests/test_evidence_store.py`

**Why this task is FIRST, and why it is split from Task 9.** `EvidenceKind.cited_by_gate` means *"the CV fabrication gate READS this corpus"*, and `test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` derives the true set by grepping `cv/engine.py`. Revision 1 changed the engine first, which left that test RED at an intermediate commit while claiming PASS. The registry half depends on nothing in the engine, so it lands first and the tree stays green throughout.

- [ ] **Step 1: Write the failing test**

```python
def test_every_cited_kind_is_also_read_by_the_composer():
    """The gate cannot cite a corpus the composer never put in the bundle. Pinned in
    __post_init__ as well: a registry invariant enforced only by a test is one a new
    EvidenceKind constructed anywhere else does not have to satisfy."""
    for kind, spec in EVIDENCE_KINDS.items():
        if spec.cited_by_gate:
            assert spec.read_by_composer, f"{kind} is cited but never composed from"


def test_a_cited_kind_that_is_not_composed_from_is_refused_at_construction():
    with pytest.raises(ValueError, match="cited_by_gate"):
        EvidenceKind("X", ("A",), cited_by_gate=True, read_by_composer=False)


def test_the_registry_flags_are_what_this_change_intends():
    """SCOPE: pins all three kinds, so a kind silently dropped from the registry or a
    flag flipped in either direction reddens here rather than passing vacuously."""
    assert {k: (s.read_by_composer, s.cited_by_gate) for k, s in EVIDENCE_KINDS.items()} \
        == {"experience": (True, True), "skills": (True, False), "stories": (False, False)}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_evidence_store.py -k "read_by_composer or cited_kind or registry_flags" -q`
Expected: FAIL — `EvidenceKind.__init__() got an unexpected keyword argument 'read_by_composer'`.

- [ ] **Step 3: Implement**

Add the field to `EvidenceKind` beside `cited_by_gate`:

```python
    # Whether cv/engine.py puts this corpus in the COMPOSER's bundle at all. SPLIT from
    # `cited_by_gate` at #165, which made the two non-equivalent for the first time:
    # `skills` reaches the prompt as a FRAMING section whose digits `bundle_sources`
    # licenses nowhere, and which the ADVISORY audit is deliberately not shown (spec D11).
    # #164 wrote one flag because "read" and "cited" then coincided; collapsing them again
    # would make `doctor` tell a user their skills are citable, the over-claim
    # `cited_by_gate` was introduced to prevent.
    read_by_composer: bool = False
```

Add to `__post_init__`, beside the existing `floor_map` guards:

```python
        # Fail loudly at construction, this module's house rule. The gate can only license
        # content the composer actually put in the bundle, so the reverse combination is
        # incoherent rather than merely unused -- and a registry invariant pinned only by a
        # test is one that a kind constructed anywhere else never has to satisfy.
        if self.cited_by_gate and not self.read_by_composer:
            raise ValueError(
                "cited_by_gate=True requires read_by_composer=True: the fabrication gate "
                "cannot license a corpus the composer never emits into the bundle")
```

Set the registry: `experience` gains `read_by_composer=True` (keeping `cited_by_gate=True`); `skills` gains `read_by_composer=True` only; `stories` gains neither.

Correct the now-false prose at `protocols.py:62` (which says "THREE of the four attributes"), `:76-86` (the `cited_by_gate` paragraph), `:95` ("rework #165 walks straight into") and `:146` ("default to False until #165").

In `core/doctor.py`, give `classify_store` a third arm — "citable" and "nothing reads this corpus yet" are now BOTH false for `skills`:

```python
        if spec.cited_by_gate:
            detail = (f"{verified} verified / {total} total entries -- only verified "
                      f"entries are citable by the CV fabrication gate")
        elif spec.read_by_composer:
            # True for `skills` since #165: the composer is shown them as FRAMING, the
            # gate licenses no figure from them, and the advisory audit is not shown them
            # at all (spec D11). "citable" here would be the #164 M2 over-claim; "nothing
            # reads this corpus" is now simply false.
            detail = (f"{verified} verified / {total} total entries -- shown to the CV "
                      f"composer as framing; not a citable source for the gate")
        else:
            detail = (f"{verified} verified / {total} total entries -- reviewed, but "
                      f"nothing reads this corpus yet")
```

Leave `blocks=("cv",)` at `:391` keyed on `cited_by_gate`: after Task 8 an unreadable skills corpus no longer blocks `cv`, so widening it to `read_by_composer` would over-claim in the other direction. Correct that comment's `(#165)` reference and the module docstring's "until #165 lands" at `:327`.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_evidence_store.py tests/test_doctor.py -q`
Expected: PASS. `test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` still passes — the engine has not changed yet, and `cited_by_gate` is still `{experience}`.

```bash
git add sluice/core/protocols.py sluice/core/doctor.py tests/test_evidence_store.py
git commit -q -m "refactor(core): split read_by_composer from cited_by_gate (#165)"
```

---

### Task 8: Engine reads evidence BY KIND — one green commit

**Files:**
- Modify: `sluice/cv/engine.py` (`CvResult`, the bundle build at :284-288, the audit call at :653, every later `CvResult(...)`)
- Modify: `sluice/cli.py:752-758,773-779`, `sluice/mcpserver.py:420`
- Modify: `tests/test_cv_engine.py:38` (`FakeVault`), `tests/test_app_operations.py:290`, `tests/test_mcpserver.py:779,831`
- Test: `tests/test_cv_engine.py`

**The fake-store migration is IN this task, not deferred.** Four reviewers found that revision 1 left 76 tests red across 4 files by switching the engine here and renaming the fakes in Task 10. `FakeVault` is constructed 58 times in `test_cv_engine.py` alone.

- [ ] **Step 1: Migrate the fake stores FIRST, so the suite never goes red**

In `tests/test_cv_engine.py:38`, `tests/test_app_operations.py:290` and `tests/test_mcpserver.py:779,831`, replace `read_experience_entries` with the by-kind spelling. Keep the old method as a delegate for this one commit so the tree is green either side of the engine edit:

```python
    def read_evidence(self, kind, verified_only=True):
        # Task 10 deletes read_experience_entries entirely; until then both spellings
        # answer, so this commit is green before AND after the engine switch below.
        return self._entries if kind == "experience" else []
    def read_experience_entries(self, verified_only=True): return self._entries
```

- [ ] **Step 2: Write the failing tests**

`FakeBackend(cv_out, audit_out=...)` takes a **string**, not a list, and has **no** `.prompts` — revision 1 got both wrong and three reviewers caught it. Prompt-recording tests define a local backend (see `TwoShotBackend`, `tests/test_cv_engine.py:220`). `StalenessPolicy`'s field is `ttl_days`, not `lead_ttl_days`, and it needs `today=` or it abstains.

```python
class RecordingBackend:
    """Records every prompt. Mirrors FakeBackend's routing: compose prompts carry
    'SOURCE BUNDLE' and not 'auditing'; audit prompts carry both."""
    def __init__(self, cv_out=None):
        self.last_backend = "primary"; self.prompts = []; self.audit_prompts = []
        self.cv_out = cv_out if cv_out is not None else CLEAN_CV
    def complete(self, prompt):
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
            self.prompts.append(prompt); return self.cv_out
        self.audit_prompts.append(prompt); return "supported\tx\tSF1"


class SkillsVault(FakeVault):
    """FakeVault plus a skills corpus. `skills_error` makes the read raise the way a
    symlinked directory or a non-UTF-8 entry really does."""
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


_SKILL_ENTRY = {"title": "Example Cloud Skill", "best_for": "platform", "body": "",
                "fields": {"Proficiency": "8 years", "Domain": "platform",
                           "Evidence": "shipped things", "Signal Value": "depth"}}


def _shortlist_note(**fm):
    return Note({"status": "shortlist", "company": "Example Foundry",
                 "role": "Analyst", **fm})


def test_a_skill_reaches_the_composers_prompt():
    """The whole point of the issue: the corpus was inert. Asserts on the PROMPT the
    backend received, never on an internal."""
    be = RecordingBackend()
    run_one(_shortlist_note(), SkillsVault(ENTRIES, skills=[_SKILL_ENTRY]), _cfg(), be,
            FakeCache(), renderer=FakeRenderer())
    assert "SKILLS INVENTORY" in be.prompts[0]
    assert "Example Cloud Skill" in be.prompts[0]


def test_the_advisory_audit_is_never_shown_the_framing_section():
    """spec D11. cv/audit.py's prompt opens 'SOURCE BUNDLE is the ONLY truth', so a CV
    claim resting on a skills line alone would read as SUPPORTED and be served unsigned --
    where today it is `unsupported` and, at the shipped cv.require_signoff, withheld until
    a human signs off. This is the assertion that keeps the #60 hold armed."""
    be = RecordingBackend()
    run_one(_shortlist_note(), SkillsVault(ENTRIES, skills=[_SKILL_ENTRY]), _cfg(), be,
            FakeCache(), renderer=FakeRenderer())
    assert be.audit_prompts, "the audit never ran; this test would pass vacuously"
    assert "SKILLS INVENTORY" not in be.audit_prompts[0]
    assert "Example Cloud Skill" not in be.audit_prompts[0]
    # ...but the real sources must still be there, or this passes for an empty bundle.
    assert "VERIFIED EXPERIENCE ENTRIES" in be.audit_prompts[0]


@pytest.mark.parametrize("err", [
    OSError("evidence directory is a symlink"),
    # A non-UTF-8 entry. `_read` opens with encoding='utf-8', so this is a ValueError,
    # NOT an OSError -- the exact shortfall Vault.preflight already shipped and fixed
    # (core/vault.py:1950-1968). Catching OSError alone lets it escape run_one, and
    # run_batch then records `error` for EVERY lead: the outcome this guard exists to
    # prevent, caused by the guard.
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
])
def test_an_unreadable_skills_corpus_composes_without_it_and_says_so(err):
    """A framing-only corpus may never cost a lead (#167's rule, one layer out). The
    experience read is deliberately NOT wrapped: it is the gate's only citable evidence,
    and a bundle with no ids fails every bullet anyway."""
    v = SkillsVault(ENTRIES, skills_error=err)
    r = run_one(_shortlist_note(), v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(),
                renderer=FakeRenderer())
    assert r.status == "rendered", "a broken framing corpus binned the lead"
    assert r.skills_unreadable is True


def test_an_unreadable_experience_corpus_still_fails_loudly():
    """The other half of the same decision, and the arm a naive 'wrap the evidence reads'
    would silently swallow. Without this, moving the experience read inside the try is
    green everywhere."""
    class ExperienceError(SkillsVault):
        def read_evidence(self, kind, verified_only=True):
            if kind == "experience":
                raise OSError("experience library is a symlink")
            return []
    with pytest.raises(OSError):
        run_one(_shortlist_note(), ExperienceError(ENTRIES), _cfg(),
                FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer())


def test_skills_reach_the_bundle_verified_only():
    """An `_inbox/` skill must never reach the composer: `verified:` is the trust root."""
    v = SkillsVault(ENTRIES, skills=[])
    run_one(_shortlist_note(), v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(),
            renderer=FakeRenderer())
    assert ("skills", True) in v.reads


def test_a_refused_lead_never_reads_any_evidence_corpus():
    """`skipped-stale` returns at engine.py:208, before the bundle build at :286, so a
    broken corpus costs nothing on a lead that was never going to compose. This guards the
    PLACEMENT: hoisting the read above the guards would spend a vault read on every
    refused lead and could raise before the refusal."""
    v = SkillsVault(ENTRIES, skills_error=OSError("would raise if reached"))
    r = run_one(_shortlist_note(last_seen="2000-01-01"), v, _cfg(),
                FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer(),
                policy=StalenessPolicy(ttl_days=1, today="2026-08-25"))
    assert r.status == "skipped-stale"
    assert v.reads == [], f"a refused lead touched the evidence corpora: {v.reads}"
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -k "skill or advisory_audit or refused_lead" -q`
Expected: FAIL — `CvResult` has no `skills_unreadable`.

- [ ] **Step 4: Implement**

Add to `CvResult` beside `dossier_failed`:

```python
    # #165: the Skills Inventory could not be READ (a symlinked corpus, a non-UTF-8 entry)
    # and the CV was composed without its framing section. Visibility, never control flow
    # -- the shape `dossier_failed` above established, and it carries the same obligation:
    # a field with no reader is the "computed and discarded" defect #167 opened over. Read
    # by cli.py's per-result line and blind-count summary, and by mcpserver.py's cv_run.
    # A MISSING corpus is NOT this: `read_evidence` returns [] for one, the abstain case.
    skills_unreadable: bool = False
```

Replace `engine.py:284-288` (four lines, keeping `bundle_text`):

```python
        entries = vault.read_evidence("experience", verified_only=True)
        baseline = vault.read_baseline()
        # A broken SKILLS corpus must not cost a lead. `read_evidence` returns [] for a
        # MISSING directory, so reaching here means genuine breakage -- which `doctor`
        # already reports per-kind as DEAD. Letting it propagate would put a framing-only
        # corpus inside the same try as the experience read and fail every lead in the
        # batch; #167's rule is that a thing affecting only tailoring QUALITY may never bin
        # a lead. The experience read above is deliberately NOT wrapped: it is the gate's
        # only citable evidence.
        #
        # `(OSError, ValueError)`, not OSError alone: `_read` opens with encoding='utf-8',
        # so a non-UTF-8 entry raises UnicodeDecodeError -- a ValueError. Catching OSError
        # alone lets it escape run_one, and run_batch then records `error` for EVERY lead,
        # which is precisely the outcome this guard exists to prevent. Vault.preflight
        # shipped that same shortfall and now catches both (core/vault.py:1950-1968).
        skills_unreadable = False
        try:
            skills = vault.read_evidence("skills", verified_only=True)
        except (OSError, ValueError) as e:
            _log.warning("skills inventory for %s unreadable, composing without it: %s",
                         note.ref, e)
            skills, skills_unreadable = [], True
        b = _bundle.build_bundle(entries, baseline, cvcfg.negatives,
                                 _jd_keywords(role, jd), cvcfg.prefix_map, skills=skills)
        bundle_text = _bundle.render_bundle(b)
        # The ADVISORY audit gets the SOURCE bundle only (spec D11). Bound here beside
        # `bundle_text` for the same reason `sources` is: all three derive from one `b`,
        # and adjacency is what stops a later edit rebuilding one and leaving another
        # stale.
        audit_bundle_text = _bundle.render_bundle(b, include_framing=False)
```

At `engine.py:653`, pass the audit its own text: `run_audit(backend, cv_text, audit_bundle_text)`.

Thread `skills_unreadable=skills_unreadable` into every `CvResult(...)` constructed after this point in `run_one`.

Give it readers, mirroring `dossier_failed` exactly:
- `sluice/cli.py:756` — add `skills_unreadable={r.skills_unreadable}` to the per-result line.
- `sluice/cli.py:777` — beside the `blind` summary, add a count and a line: `f"cv: {n} CV(s) composed without the Skills Inventory (corpus unreadable)"`.
- `sluice/mcpserver.py:420` — add `"skills_unreadable": r.skills_unreadable` to the dict.

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. This is the commit revision 1 got wrong; running the whole suite (not just `test_cv_engine.py`) is what proves the fake-store migration in Step 1 was complete.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/engine.py sluice/cli.py sluice/mcpserver.py tests/test_cv_engine.py \
        tests/test_app_operations.py tests/test_mcpserver.py
git commit -q -m "feat(cv): compose from the Skills Inventory, degrading if it is unreadable (#165)"
```

---

### Task 9: Derive the two flags from what the code actually does

**Files:**
- Modify: `tests/test_evidence_store.py:133-165`

- [ ] **Step 1: Replace the source-grep test with two derivations**

```python
def test_read_by_composer_names_exactly_the_kinds_the_cv_engine_reads():
    """#164's mechanism, retargeted at the flag it actually answers."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "sluice" / "cv" / "engine.py"
           ).read_text(encoding="utf-8")
    reached = set(re.findall(r"""read_evidence\(\s*["']([a-z]+)["']""", src))
    assert reached, ("the sweep found no evidence read in sluice/cv/engine.py -- the "
                     "matcher is broken, not the engine; without this the equality below "
                     "would compare two empty sets and pass vacuously")
    assert reached == {k for k, s in EVIDENCE_KINDS.items() if s.read_by_composer}


def test_cited_by_gate_is_exactly_what_bundle_sources_actually_licenses():
    """#164 derived this by grepping the engine, on the assumption that a corpus the
    engine READS is a corpus the gate CITES. #165 breaks that: skills reach the prompt and
    are licensed nowhere. So derive it by EXECUTION -- give each kind a distinct sentinel
    digit, build a real bundle, and ask `bundle_sources` which sentinels it licensed. A
    source grep cannot answer this; only running the derivation can.

    SCOPE: `sentinels` must cover every kind flagged `read_by_composer`, or a kind added
    later is silently outside the comparison and this passes vacuously."""
    from sluice.cv import bundle as B
    sentinels = {"experience": "8801", "skills": "8802"}
    assert set(sentinels) == {k for k, s in EVIDENCE_KINDS.items() if s.read_by_composer}, (
        "a read_by_composer kind has no sentinel here, so it is outside this comparison")
    b = B.build_bundle(
        [{"title": "t", "company": "Example Co", "best_for": "", "category": "",
          "metrics": sentinels["experience"], "body": ""}],
        "baseline", [], [], {},
        skills=[{"title": "s", "best_for": "", "body": "",
                 "fields": {"Domain": "", "Proficiency": sentinels["skills"],
                            "Evidence": "", "Signal Value": ""}}])
    sources = B.bundle_sources(b)
    licensed = set().union(*sources.nums.values(), sources.baseline)
    assert sentinels["experience"] in licensed, (
        "the experience sentinel was not licensed -- the fixture is wrong, and the "
        "equality below would pass for the wrong reason")
    actually_cited = {k for k, digit in sentinels.items() if digit in licensed}
    assert actually_cited == {k for k, s in EVIDENCE_KINDS.items() if s.cited_by_gate}
```

- [ ] **Step 2: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_evidence_store.py -q` — expect PASS (the engine now reads both kinds, and the registry from Task 7 already flags them correctly).

```bash
git add tests/test_evidence_store.py
git commit -q -m "test(core): derive cited_by_gate by execution, not by grepping (#165)"
```

---

### Task 10: Retire `read_experience_entries`

**Files:**
- Modify: `sluice/core/protocols.py:732-744` (delete the member), `sluice/core/vault.py:1756-1775` (delete the delegate), `sluice/core/doctor.py:387` (a comment reference)
- Modify: `tests/conformance/test_store_contract.py:345-380`, `tests/conformance/seeds.py:4`
- Modify: `tests/test_mcpserver.py:1271,1277`, `tests/test_cv_engine.py`, `tests/test_app_operations.py`, `tests/test_core_vault_cv.py:34,46,66,70,80`, `tests/test_doctor.py:1710`, `tests/test_evidence_store.py:155-158,181`

Its own docstring says **"EXPIRES AT #165 … DELETE this member rather than inheriting it"**: a Protocol member is a REQUIRED member, so keeping it means every future store implements a second spelling for a caller that no longer exists.

- [ ] **Step 1: Confirm there is no production caller left**

```bash
grep -rn "read_experience_entries" sluice/ --exclude-dir=__pycache__
```

Expected: only `core/protocols.py`, `core/vault.py`, and the comment at `core/doctor.py:387`. If `cv/engine.py` appears, Task 8 is incomplete — stop.

- [ ] **Step 2: Delete the member, the delegate, and the temporary fake delegates**

Remove it from `core/protocols.py` and `core/vault.py`, and remove the one-commit delegate Task 8 Step 1 added to the three fake stores. In `core/doctor.py:387`, replace the reference with `read_evidence("experience", ...)`, keeping the measured claim it records (a symlinked Experience Library raises rather than returning `[]`).

- [ ] **Step 3: Retarget the tests**

In `tests/test_mcpserver.py`, **remove** `"read_experience_entries"` from `_STORE_READ_METHODS` (:1277) and from the prose list at :1271 — do NOT replace it with `"read_evidence"`, which is already in that frozenset. Rename the conformance row to `test_read_evidence_honours_verified_only` and call `store.read_evidence("experience", verified_only=...)`; update its reference in `tests/conformance/seeds.py:4`. In `tests/test_evidence_store.py:155-158`, delete the `if "read_experience_entries(" in src` branch — the engine now names every kind as a string, which is the point.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. This task touches eight test files; a partial rename shows up here and nowhere else.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -q -m "refactor(core): retire read_experience_entries for read_evidence (#165)"
```

---

### Task 11: `doctor` reports a negative that contradicts the inventory

**Files:**
- Modify: `sluice/core/doctor.py` (new `skill_terms`, `classify_negatives_vs_skills`)
- Modify: `sluice/core/app.py:2171-2178`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_negative_naming_a_held_skill_is_reported():
    rows = D.classify_negatives_vs_skills(["never claim documenting experience"],
                                          {"document", "platform"})
    assert len(rows) == 1 and rows[0].state == D.NOTICE
    assert "documenting" in rows[0].detail


def test_an_empty_inventory_abstains():
    """Empty-config-abstains: an install with no Skills Inventory must not have every
    negative reported as a contradiction."""
    assert D.classify_negatives_vs_skills(["never claim anything"], set()) == []


def test_an_empty_negatives_list_abstains():
    assert D.classify_negatives_vs_skills([], {"document"}) == []


def test_a_negative_about_something_not_in_the_inventory_is_not_reported():
    assert D.classify_negatives_vs_skills(["never claim a security clearance"],
                                          {"document"}) == []


def test_the_match_survives_a_word_form_difference():
    """Why this shares the stemmer: a negative saying 'documenting' and a skill whose
    Domain says 'documentation' are the same disagreement."""
    assert D.classify_negatives_vs_skills(["no documenting"], D.skill_terms(
        [{"best_for": "documentation", "title": "x"}]))


def test_skill_terms_reads_the_domain_and_not_the_entry_title():
    """The title is a NAME the user chose ('Example Cloud Skill'), so unioning its stems
    makes any negative containing an ordinary word like 'skills' fire a false NOTICE.
    `Domain` (the best_for floor) is the classification axis and the only honest side."""
    terms = D.skill_terms([{"best_for": "platform", "title": "Example Cloud Skill"}])
    assert "platform" in terms
    assert not terms & {"exampl", "cloud", "skill"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -k "negatives_vs_skills or skill_terms" -q` — expect FAIL.

- [ ] **Step 3: Implement**

In `core/doctor.py`, with `from sluice.core.stem import stem_all` at the top:

```python
def skill_terms(entries: list) -> set:
    """The comparable stem set for a verified Skills Inventory read.

    `best_for` ONLY -- the floor key `EVIDENCE_KINDS["skills"]` maps onto `Domain`, which
    is the kind's classification axis. The entry TITLE is deliberately excluded: it is a
    name the user chose, so unioning its stems makes an ordinary word in it ('skill',
    'example') match any negative containing that word and fire a NOTICE about nothing.
    A false contradiction report is worse than a missed one here -- the whole value of
    this check is that a row means something.

    Here rather than at the call site so `core/app.py` -- an orchestrator -- need not know
    that matching is stemmed at all, and so a second caller cannot spell it differently.
    """
    return set().union(*(stem_all(e.get("best_for", "")) for e in entries)) if entries \
        else set()


def classify_negatives_vs_skills(negatives: list, skill_terms_: set) -> list:
    """One NOTICE per configured `cv.negatives` string naming a skill the verified Skills
    Inventory actually holds (#165).

    `cv.negatives` is prose asserting which technologies the candidate does and does not
    work in, maintained by hand and separately from the inventory that already answers
    that. The bundle's derived cross-reference cannot stop the two disagreeing -- it names
    nothing, so it adds a third voice rather than replacing the stale one. This is what
    makes the disagreement visible.

    NOTICE, never DEGRADED: a contradiction is worth knowing before a compose and must
    never affect the exit code -- `--strict` in a cron job failing because a negative
    overlaps an inventory is the 672ad2a class aimed at the tool's own exit status. Same
    posture `classify_gate` already takes.

    Abstains on either empty input: an install with no inventory has nothing to contradict.
    """
    if not negatives or not skill_terms_:
        return []
    out = []
    for neg in negatives:
        overlap = stem_all(neg) & skill_terms_
        if overlap:
            out.append(ComponentCheck(
                "gates", "cv.negatives", NOTICE,
                f"contradicts the verified Skills Inventory: {neg!r} names "
                f"{sorted(overlap)}, which the inventory holds -- the composer is told "
                f"both. Remove the line, or remove the skill."))
    return out
```

- [ ] **Step 4: Wire it into `Sluice.doctor()`**

In `sluice/core/app.py`, inside the existing `else:` branch after `classify_store`. No new import — everything goes through `_doctor`. The `except` is narrowed to the store read: a bug in the pure classifier must not be swallowed at DEBUG.

```python
                # #165. Needs BOTH the store and cv_cfg, which is why it lives here and
                # not in `Vault.preflight()` -- whose docstring commits it to counts rather
                # than content, and which is a Store-seam member every implementation would
                # have to grow.
                if cv_cfg is not None:
                    try:
                        skills = store.read_evidence("skills", verified_only=True)
                    except Exception as e:  # noqa: BLE001 -- an unreadable corpus is
                        # already reported DEAD by classify_store above (when the store
                        # implements the optional preflight hook; when it does not, this
                        # log line is the only signal, which is why it is WARNING).
                        _log.warning("skills read for the negatives cross-check "
                                     "failed: %s", e)
                    else:
                        # Deliberately OUTSIDE the try: these two are pure, and a bug in
                        # them must surface, not be logged and dropped.
                        components.extend(_doctor.classify_negatives_vs_skills(
                            cv_cfg.negatives, _doctor.skill_terms(skills)))
```

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_doctor.py tests/test_app_operations.py -q` — expect PASS.

```bash
git add sluice/core/doctor.py sluice/core/app.py tests/test_doctor.py
git commit -q -m "feat(doctor): report a negative that contradicts the Skills Inventory (#165)"
```

---

### Task 12: Documentation — every `#165` claim this change falsifies

Revision 1 assigned three prose sites and its own final-verification grep would have failed. The full set, enumerated by `grep -rn "#165" sluice/ docs/`:

**Files:**
- `sluice/cli.py:1540` · `sluice/evidence/wizard.py:38,40` · `sluice/evidence/commands.py:29,33` — all say the gate reads `experience` alone "until #165". Now `skills` is read by the composer and still not cited; reword to the two-flag distinction rather than deleting the caveat.
- `docs/ARCHITECTURE.md:1232,1397,1401-1403` — `read_experience_entries` in TWO places plus the "EXPIRES AT #165" sixth-member paragraph. Also add the four bundle sections and state that `bundle_sources` walks `bundle["entries"]` alone.
- `docs/USAGE.md:330,344,373,442` — four claims that nothing consumes the corpora until #165.
- `docs/CONFIGURATION.md` and `sluice.yaml.example` — **note the whole `cv:` block ships COMMENTED (`sluice.yaml.example:169`), so there is no live `cv.negatives` key.** Add the commented key with its explanation rather than assuming one exists.
- `.rulesync/rules/CLAUDE.md` — the CV-gate paragraph: four bundle sections, skills license numbers in neither pool, the audit's separate source set (D11), and the two `EvidenceKind` flags.

- [ ] **Step 1: Edit each file above.** Edit `.rulesync/rules/CLAUDE.md`, never `CLAUDE.md`.

- [ ] **Step 2: Regenerate and verify no drift**

```bash
npm ci --ignore-scripts && npm run rulesync
git status --short          # CLAUDE.md/AGENTS.md are gitignored; expect no tracked churn
.venv/bin/python -m pytest -q
```

- [ ] **Step 3: Commit**

```bash
git add docs/ sluice.yaml.example .rulesync/
git commit -q -m "docs(cv): describe the fourth bundle section and the two evidence flags (#165)"
```

---

## Final verification

- [ ] `.venv/bin/python -m pytest -q` — full suite green
- [ ] `ruff check sluice tests scripts` — clean (`pip install ruff==0.15.21`, the CI pin)
- [ ] `grep -rn "#165" sluice/ docs/ --exclude-dir=__pycache__ | grep -v superpowers` — **every remaining hit must describe what the code does NOW**, not what waits on #165. Revision 1's grep pattern missed four sites; read the hits rather than trusting an empty result.
- [ ] `git log --oneline origin/main..HEAD` — the re-freeze (Task 5) is its own commit, and no commit message is a non-Conventional subject
- [ ] Every commit is green: `git rebase --exec '.venv/bin/python -m pytest -q' origin/main`
- [ ] Run `/review-pr` BEFORE pushing. CodeRabbit is the scarce resource (~1/hour, adaptive); the local specialist team is free and parallel.
