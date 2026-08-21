# Location Identity Normalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give issue #5 a location comparison it can key a note split on, at the bar it needs — confidently different, or abstain.

**Architecture:** Two pure functions and three string constants in `sluice/core/leads.py`, beside `_norm_url` and matching its shape. `_norm_location` canonicalizes; `_compare_locations` returns `SAME`/`DIFFERENT`/`UNKNOWN` by **token overlap** — disjoint token sets are the only evidence of difference. No caller ships in this change: #5 is the consumer and is parked. The tests are the caller, and since both functions are pure, nothing is deferred to integration.

**Tech Stack:** Python 3.12+, standard library only (`re`, `unicodedata`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-16-location-identity-normalizer-design.md` (plan-reviewed 3x).
**Issue:** #25. **Evidence:** `docs/superpowers/specs/2026-07-16-location-identity-evidence.py`.

## Global Constraints

- **Standard library only in `sluice/`.** This change adds `unicodedata` and uses the existing `re`. Nothing else.
- **No personal data in `sluice/` or `tests/`.** No place name, country, or region. No gazetteer, country list, or transliteration table. Tests use **synthetic** place names (`Palmerburgh`, `Clarkefurt`, `Westland`, `North Clarke`) — the `tests/test_demash.py` convention.
- **No new config key.** `location_noise_words` lands with #5, its only reader. Here the empty default is the function parameter default, `noise=frozenset()`.
- **Comments explain *why*** — the invariant upheld, the bug prevented. Match the existing density in `core/leads.py`; several comments there encode real incidents.
- **Conventional commits** (`feat(leads): ...`).
- **The suite stays offline and under ~2s.** Baseline: 576 passed in ~1s.
- **`DIFFERENT` is the only verdict #5 acts on.** A wrong `DIFFERENT` manufactures a duplicate note (a regression on today); a wrong `SAME` merges, which is today's behaviour and is recoverable. Every design choice falls out of that asymmetry.

---

### Task 1: `_norm_location`

**Files:**
- Modify: `sluice/core/leads.py:1-13` (add the `unicodedata` import; add the function after `_norm_url`)
- Create: `tests/test_leads_location.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_norm_location(s: str) -> str`. Task 2 calls it for both operands and for every noise word.

**Why two mutations, two witnesses (read before writing the tests):** NFKD-folding and the unicode-aware `\W` are load-bearing for **different** reasons, and conflating them is what makes a guard test inert. `Zürich` **cannot** witness the character class — NFKD has already folded `ü` to `u`, so `[^a-z0-9]` yields `zurich` either way. `København` is the **only** witness for the class, because `ø` has no NFKD decomposition and the class is therefore the only live variable. Each needs its own test. Both are verified below.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leads_location.py`:

```python
"""Location identity (#25): the comparison #5 keys every note split on.

Synthetic place names throughout (Palmerburgh/Clarkefurt, the tests/test_demash.py convention).
The rule was derived from the real board payloads in tests/fixtures/, but naming those cities here
would encode one person's job-hunt geography in tests/. The SHAPES carry the regression risk; the
specific cities do not -- these seven synthetic shapes reproduce the real corpus's 15-of-21
token-subset failure exactly. The derivation lives in
docs/superpowers/specs/2026-07-16-location-identity-evidence.py.
"""
from sluice.core.leads import _norm_location


def test_norm_location_casefolds_collapses_and_strips():
    assert _norm_location("  Palmerburgh  ") == "palmerburgh"
    assert _norm_location("PALMERBURGH   ZZ9Z") == "palmerburgh zz9z"
    # bool("   ") is True, so a blank that did not normalize to "" would let whitespace dirt
    # read as evidence of a difference. An empty side must abstain instead.
    assert _norm_location("   ") == ""
    assert _norm_location("") == ""


def test_norm_location_treats_real_board_punctuation_as_separators():
    # Both characters are real: one board renders "<city>\xa0∙ Choose area".
    assert _norm_location("Palmerburgh\xa0∙ Choose area") == "palmerburgh choose area"
    assert (_norm_location("Palmerburgh, Westland, North Clarke (Hybrid)")
            == "palmerburgh westland north clarke hybrid")


def test_norm_location_folds_accents():
    # Asserts the EXACT STRING, not the token count: a token-count assertion is GREEN under both
    # single mutations and catches neither. Deleting the NFKD fold makes
    # _compare_locations("Zürich", "Zurich") return DIFFERENT -- it SPLITS.
    assert _norm_location("Zürich") == "zurich"
    assert _norm_location("Zurich") == "zurich"


def test_norm_location_keeps_non_ascii_letters_whole():
    # The ONLY guard for `\W` vs `[^a-z0-9]`. "ø" has no NFKD decomposition -- it is a distinct
    # letter, not an accented "o" -- so the character class is the only live variable here.
    # Under [^a-z0-9] this shreds to "k benhavn": two junk tokens where there was one word.
    assert _norm_location("København") == "københavn"
    assert len(_norm_location("København").split()) == 1
```

**Import only what this task uses.** `ruff`'s `F401` is **not** relaxed for `tests/*` — `pyproject.toml`
says so explicitly: *"F (unused import/var) is NOT relaxed here, so real cruft is still caught."*
Importing Task 2's names now turns Step 6 red. Task 2 extends the import line when it adds its tests.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_location.py -q`

Expected: collection error — `ImportError: cannot import name '_norm_location' from 'sluice.core.leads'`.

- [ ] **Step 3: Write the implementation**

In `sluice/core/leads.py`, add the import to the existing block at the top (keep alphabetical order — `hashlib`, `re`, `unicodedata`):

```python
import unicodedata
```

Then add this function immediately after `_norm_url` (after line 13, before the `@dataclass`):

```python
def _norm_location(s: str) -> str:
    """Canonicalize a location for comparison: NFKD-fold and drop combining marks, casefold, then
    collapse runs of non-word characters to a single space. Blank-ish input becomes "", which is
    what lets an absent location abstain rather than read as evidence (`bool("   ")` is True).

    Both halves are load-bearing, for DIFFERENT reasons -- conflating them is how the guard test
    for this goes inert. The NFKD fold makes 'Zürich' and 'Zurich' one token; without it they share
    no token and SPLIT. The unicode-aware `\\W` (not `[^a-z0-9]`) keeps letters that NFKD cannot
    fold whole: 'ø' is a distinct letter, not an accented 'o', so `[^a-z0-9]` would shred
    'københavn' into 'k benhavn'. Neither witnesses the other; see tests/test_leads_location.py.
    """
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\W+", " ", s).strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_location.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Verify each mutation reddens its own witness — and only its own**

This is the step the whole spec exists for: a mutation list nobody ran certifies nothing.

Run:

```bash
.venv/bin/python -c "
import re, unicodedata
def build(nfkd, wclass):
    def f(s):
        s = s.casefold()
        if nfkd:
            s = unicodedata.normalize('NFKD', s)
            s = ''.join(c for c in s if not unicodedata.combining(c))
        return re.sub(r'\W+' if wclass else r'[^a-z0-9]+', ' ', s).strip()
    return f
for name, nfkd, wclass in [('correct', 1, 1), ('no NFKD', 0, 1), ('[^a-z0-9]', 1, 0)]:
    f = build(nfkd, wclass)
    print(f'{name:10} Zürich->{f(\"Zürich\")!r:12} 4a={f(\"Zürich\")==\"zurich\"}  '
          f'København->{f(\"København\")!r:14} 4b={len(f(\"København\").split())==1}')
"
```

Expected output — each mutant fails exactly one witness:

```
correct    Zürich->'zurich'     4a=True  København->'københavn'    4b=True
no NFKD    Zürich->'zürich'     4a=False  København->'københavn'    4b=True
[^a-z0-9]  Zürich->'zurich'     4a=True  København->'k benhavn'    4b=False
```

If `no NFKD` shows `4b=False`, or `[^a-z0-9]` shows `4a=False`, the witnesses are not isolated — stop and re-read the docstring.

- [ ] **Step 6: Lint**

Run: `ruff check sluice tests`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add sluice/core/leads.py tests/test_leads_location.py
git commit -m "feat(leads): _norm_location — fold, casefold, collapse (#25)"
```

---

### Task 2: `_compare_locations` and the verdict vocabulary

**Files:**
- Modify: `sluice/core/leads.py` (add the three constants after the imports; add the function after `_norm_location`)
- Modify: `tests/test_leads_location.py` (append)

**Interfaces:**
- Consumes: `_norm_location(s: str) -> str` from Task 1.
- Produces: `SAME`, `DIFFERENT`, `UNKNOWN` (module-level `str` constants) and `_compare_locations(a: str, b: str, noise=frozenset()) -> str`. #5's `same_opportunity` calls it and returns its verdict; `core/vault.py` reads the constants.

**Why a tri-state and not a bool:** #5 consumes this in **two** of its four rules, not one. Its rule 2 is keyed on normalized *equality* and fires **0 of 33** real same-city re-post pairs — re-posts overlap but are never equal (`Palmerburgh` vs `Palmerburgh ZZ9Z`). A bool expressing only rule 3 would leave rule 2 silently dead and route every ordinary re-post into #5's `merged` counter, which #5 calls "its only signal". Returning the trichotomy collapses #5's rules 2–4 into one call and takes rule 2 to 33/33.

**Why public constants but a private function:** `core/vault.py` reads the verdict, so the constants are public; `_compare_locations`'s only consumer is `same_opportunity` in this same module, so it is private. That matches the file's own precedent — `_norm_url` private and in-module, `slug_matches` public and cross-module.

- [ ] **Step 1: Write the failing tests**

First **replace** the import line at the top of `tests/test_leads_location.py` (Task 1 imported only
what it used, because `ruff`'s `F401` is not relaxed for `tests/*`):

```python
import itertools

import pytest

from sluice.core.leads import DIFFERENT, SAME, UNKNOWN, _compare_locations, _norm_location
```

Then append:

```python
# The seven shapes the real corpus renders a single city in. These reproduce the corpus's
# token-subset failure exactly: subset splits 15 of these 21 pairs, overlap splits 0.
_SAME_CITY_SHAPES = [
    "Palmerburgh",
    "Palmerburgh ZZ9Z",
    "Hybrid work in Palmerburgh",
    "Palmerburgh\xa0∙ Choose area",
    "Palmerburgh Area, North Clarke (Hybrid)",
    "Palmerburgh, Westland, North Clarke (Hybrid)",
    "Palmerburgh, Westland, North Clarke (Remote)",
]


def test_every_rendering_of_one_city_is_never_a_split():
    # THE test. Boards decorate a city differently on every re-post, so neither side of most
    # pairs is a subset of the other -- token-subset splits 15 of these 21 and manufactures a
    # duplicate note per cross-board re-post. Overlap keys on the shared city token instead.
    for a, b in itertools.combinations(_SAME_CITY_SHAPES, 2):
        assert _compare_locations(a, b) == SAME, f"{a!r} vs {b!r} would split a re-post"


def test_genuinely_different_cities_are_the_only_split():
    assert _compare_locations("Palmerburgh", "Clarkefurt") == DIFFERENT
    assert _compare_locations("Palmerburgh ZZ9Z", "Clarkefurt (Hybrid)") == DIFFERENT


def test_compare_locations_is_symmetric():
    assert (_compare_locations("Palmerburgh", "Clarkefurt")
            == _compare_locations("Clarkefurt", "Palmerburgh"))
    assert (_compare_locations("Palmerburgh ZZ9Z", "Palmerburgh")
            == _compare_locations("Palmerburgh", "Palmerburgh ZZ9Z"))


def test_compare_locations_is_reflexive_for_anything_with_a_surviving_token():
    # The qualifier is required, not pedantry: "" and a noise-emptied value are both UNKNOWN.
    assert _compare_locations("Palmerburgh", "Palmerburgh") == SAME
    assert _compare_locations("", "") == UNKNOWN


def test_absent_evidence_abstains_rather_than_splitting():
    # These reach UNKNOWN via an empty INPUT. The other route -- noise subtraction emptying a
    # side -- is a different code path, covered only by the two noise-emptying tests below.
    assert _compare_locations("Palmerburgh", "") == UNKNOWN
    assert _compare_locations("Palmerburgh", "   ") == UNKNOWN
    assert _compare_locations("", "") == UNKNOWN


def test_noise_is_normalized_and_tokenized_not_used_raw():
    # The region is TWO words deliberately: with a one-word region a multi-word noise entry
    # strips nothing and the arity assertion could not pass against a correct implementation.
    # Raw `noise` yields a knob that silently does nothing -- it fails toward merge, which is
    # safe, but silently, which is the failure class this codebase engineers out.
    a, b = "Palmerburgh, North Clarke", "Clarkefurt, North Clarke"
    assert _compare_locations(a, b) == SAME                          # shares the region
    assert _compare_locations(a, b, {"North Clarke"}) == DIFFERENT   # arity: multi-word entry
    assert _compare_locations(a, b, {"NORTH CLARKE"}) == DIFFERENT   # case
    assert _compare_locations(a, b, {"north", "clarke"}) == DIFFERENT


def test_noise_as_a_bare_str_raises():
    # `location_noise_words: Remote` (a YAML scalar instead of a list) is an ordinary user error.
    # Iterating a str yields single-letter tokens that strip nothing: inert, and silent.
    with pytest.raises(TypeError, match="not a str"):
        _compare_locations("Palmerburgh", "Clarkefurt", noise="Palmerburgh")


def test_remote_versus_a_city_is_the_accepted_cost():
    # On the record (user decision, 2026-07-16). remoteok and weworkremotely ship as sources, so
    # remote-vs-city is a shipped configuration and this splits out of the box. Pinned in BOTH
    # directions so it cannot be "fixed" by accident -- a code-default noise list is NOT the fix,
    # because stripping "remote" turns "Remote, US" vs "Remote, UK" from SAME into a SPLIT.
    assert _compare_locations("Remote", "Palmerburgh") == DIFFERENT
    # Configuring it ABSTAINS; it does not merge. Subtraction empties one side.
    assert _compare_locations("Remote", "Palmerburgh", {"remote"}) == UNKNOWN


def test_noise_emptying_both_sides_abstains_rather_than_splitting():
    # The ONLY test reaching UNKNOWN via subtraction rather than empty input, and therefore the
    # only witness for moving the empty check BEFORE noise subtraction. That mutant returns
    # DIFFERENT here -- splitting two IDENTICAL locations, the worst verdict available.
    assert _compare_locations("Remote", "Remote", {"remote"}) == UNKNOWN


def test_multi_word_countries_sharing_a_token_merge_at_default():
    # The structural miss: 18 of the 30 real ones. Any two multi-word country names sharing a
    # token merge until that token is configured as noise. Merge direction, so it matches today.
    a, b = "Palmerburgh - North Clarke Republic", "Clarkefurt, North Clarke Brennmark"
    assert _compare_locations(a, b) == SAME
    assert _compare_locations(a, b, {"North Clarke"}) == DIFFERENT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_location.py -q`

Expected: collection error — `ImportError: cannot import name 'DIFFERENT' from 'sluice.core.leads'`.

- [ ] **Step 3: Write the implementation**

In `sluice/core/leads.py`, add the constants after the import block and before `_norm_url`:

```python
# The verdict vocabulary, shared with #5's `same_opportunity`. Strings, not an enum -- core/status.py
# sets that convention. DIFFERENT is the ONLY verdict a caller may split on.
SAME = "same"
DIFFERENT = "different"
UNKNOWN = "unknown"
```

Then add this function immediately after `_norm_location`:

```python
def _compare_locations(a: str, b: str, noise=frozenset()) -> str:
    """Compare two locations by token OVERLAP. Returns DIFFERENT only on positive evidence of
    difference -- disjoint, non-empty token sets. Overlapping evidence is SAME; absent evidence is
    UNKNOWN. **DIFFERENT is the only verdict #5 acts on**, so UNKNOWN and SAME are both safe to be
    wrong about and DIFFERENT is not: a wrong DIFFERENT manufactures a second note for an ordinary
    cross-board re-post, while a wrong SAME merges, which is what today already does.

    Overlap, not subset or containment, and that is measured rather than chosen: boards decorate a
    city differently on every re-post ('Palmerburgh', 'Palmerburgh ZZ9Z', 'Palmerburgh ∙ Choose area'), so neither
    side is usually a subset of the other and token-subset splits 15 of 21 real same-city pairs.
    Every rendering shares the CITY token; the rest is decoration. Overlap keys on the signal.
    See docs/superpowers/specs/2026-07-16-location-identity-evidence.py to re-derive the numbers.

    `noise` is vocabulary that decorates a location without locating it. It is fed through
    _norm_location and TOKENIZED rather than used raw, because raw subtraction gives a knob that
    silently does nothing: {'UK'} never matches the token 'uk' (case), and {'Allied Brennmark'} equals
    no single token (arity). A bare str raises rather than iterating into characters (shape).
    """
    if isinstance(noise, str):
        raise TypeError(f"noise must be a set of words, not a str: {noise!r}")
    drop = {tok for w in noise for tok in _norm_location(w).split()}
    ta = set(_norm_location(a).split()) - drop
    tb = set(_norm_location(b).split()) - drop
    # Emptiness is checked AFTER subtraction, deliberately: noise can empty a side, and that must
    # abstain. Hoisting this check above the subtraction makes _compare_locations('Remote',
    # 'Remote', {'remote'}) return DIFFERENT -- splitting two identical locations.
    if not ta or not tb:
        return UNKNOWN
    return SAME if ta & tb else DIFFERENT
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_location.py -q`

Expected: `14 passed` (4 from Task 1, 10 from Task 2).

> Drift note (post-execution): PR #32's review added a 15th test — the guard for the NFKD/casefold
> *ordering*, which the two fold tests could not witness. Re-running the plan today gives `15 passed`
> here and `591 passed` at Step 6. The counts above are left as executed rather than back-dated: the
> 15th test is not Task 1's or Task 2's, and relabelling it as theirs would be a new falsehood.

- [ ] **Step 5: Verify each mutation reddens the tests that name it**

Run each mutation by hand against the suite. For each, edit `sluice/core/leads.py`, run the command, confirm the expected failures, then **revert the edit**.

| # | mutation | must redden |
|---|---|---|
| 1 | `return SAME if (ta <= tb or tb <= ta) else DIFFERENT` | `test_every_rendering_of_one_city_is_never_a_split` |
| 2 | move `if not ta or not tb: return UNKNOWN` above the two subtractions | `test_remote_versus_a_city_is_the_accepted_cost` **and** `test_noise_emptying_both_sides_abstains_rather_than_splitting` |
| 3 | `drop = set(noise)` | `test_noise_is_normalized_and_tokenized_not_used_raw` **and** `test_multi_word_countries_sharing_a_token_merge_at_default` |

Run after each: `.venv/bin/python -m pytest tests/test_leads_location.py -q`

Expected: mutation 1 → 3 failed; mutation 2 → 2 failed; mutation 3 → 2 failed. **If any mutation shows a FULLY GREEN run, the test that names it is inert — stop and fix the test before continuing.** (Stated as "fully green" rather than a count: the count moved from 14 to 15 after this plan ran, and a sentinel keyed to a stale number silently stops sentinelling.)

- [ ] **Step 6: Confirm the tree is back to correct and the whole suite is green**

Run: `.venv/bin/python -m pytest -q`
Expected: `590 passed` in under ~2s (576 baseline + 14 new).

- [ ] **Step 7: Lint**

Run: `ruff check sluice tests`
Expected: `All checks passed!`

- [ ] **Step 8: Confirm the evidence still derives (DoD 3)**

Run: `.venv/bin/python docs/superpowers/specs/2026-07-16-location-identity-evidence.py`

Expected: the counts the spec quotes — `0/33` for #5's rule 2 as written, `{'SAME': 33}` for the tri-state, `33 SAME / 0 DIFFERENT` same-city and `237 / 30` different-city at the empty default, `267 / 0` configured. A one-time derivation check; CI does not run it.

- [ ] **Step 9: Confirm neutrality (DoD 11)**

What DoD 11 forbids is place vocabulary in **data or logic** — a gazetteer, a country list, a
transliteration table, or a rule whose behaviour depends on knowing what a city is. Illustrative
place names **in a docstring** are permitted (user decision, 2026-07-16; see the spec's DoD 11 for
the reasoning and the recorded dissent). So the check is scoped to executable lines, not comments.

Run:

```bash
git diff --stat main -- sluice/ tests/
# Place vocabulary in EXECUTABLE code (strings/identifiers), ignoring comments and docstrings.
.venv/bin/python - <<'PY'
import ast, pathlib
src = pathlib.Path("sluice/core/leads.py").read_text()
banned = {"palmerburgh", "clarkefurt", "marshburgh", "hensleyfurt", "asr", "abm", "wexmoor", "remote", "hybrid"}
tree = ast.parse(src)
hits = []
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # ast strips comments entirely; skip docstrings, which are the first stmt of a scope.
        for w in banned:
            if w in node.value.casefold().split() or node.value.casefold() == w:
                hits.append((node.lineno, node.value[:40]))
    if isinstance(node, ast.Name) and node.id.casefold() in banned:
        hits.append((node.lineno, node.id))
docstring_lines = set()
for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
    d = ast.get_docstring(scope, clean=False)
    if d:
        body0 = scope.body[0]
        docstring_lines.update(range(body0.lineno, (body0.end_lineno or body0.lineno) + 1))
real = [h for h in hits if h[0] not in docstring_lines]
print("place vocabulary in executable code:", real or "NONE")
PY
grep -nE "GAZETTEER|COUNTRIES|_CITIES|TRANSLIT" sluice/core/leads.py || echo "no gazetteer/country/translit table: OK"
```

Expected: the diff touches only `sluice/core/leads.py` and `tests/test_leads_location.py`; the AST
check prints `NONE`; the grep reports no table. **A hit in executable code is a real Critical
finding** — it would mean the rule stopped being vocabulary-free.

- [ ] **Step 10: Commit**

```bash
git add sluice/core/leads.py tests/test_leads_location.py
git commit -m "feat(leads): _compare_locations — token overlap, tri-state verdict (#25)"
```

---

## Definition of done (from the spec, mapped to tasks)

| DoD | where |
|---|---|
| 1. pytest passes, offline, <2s | Task 2 Step 6 |
| 2. `ruff check sluice tests` | Task 1 Step 6, Task 2 Step 7 |
| 3. evidence script reproduces every count | Task 2 Step 8 |
| 4a. `_norm_location('Zürich') == 'zurich'` (NFKD witness) | Task 1 Steps 1, 5 |
| 4b. `_norm_location('København')` one token (class witness) | Task 1 Steps 1, 5 |
| 5. corpus shape pairs all `SAME` (kills token-subset) | Task 2 Steps 1, 5 |
| 6. `Remote`/city both directions; noise-emptied abstains (kills the hoist) | Task 2 Steps 1, 5 |
| 7. noise case + arity + bare-str raise (kills raw noise) | Task 2 Steps 1, 5 |
| 8. `allied`-collision shape (does **not** witness the hoist — item 6 does) | Task 2 Step 1 |
| 9. #5's resumption instruction corrected | Done, but **not in this PR** — #5's spec is parked and unmerged on `fix/lead-identity-write-path`; the correction travels with it. A file that is not on `main` cannot be corrected on `main`. |
| 10. verdict vocabulary + docstring states DIFFERENT is the only actionable verdict | Task 2 Step 3 |
| 11. no place name/country/region added to `sluice/`; no config key | Task 2 Step 9 |

## After the plan

Per the spec's Process: review with **both** `/review-pr` and CodeRabbit — read the CodeRabbit rate-limit comment **before** triggering. Then #5 resumes (see its "Blocked on #6" section: rules 2–4 collapse into one call; `location_noise_words` on the root `Config` with its guard assertion and its `sluice.yaml.example` line; its REFUSE recipe is stale).
