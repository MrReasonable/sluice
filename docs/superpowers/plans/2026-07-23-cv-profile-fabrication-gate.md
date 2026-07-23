# CV Profile Fabrication Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the CV-profile fabrication hole (#30) — a fabricated **number** in the PROFILE currently passes the gate and ships in the PDF, and the prompt actively pulls the profile toward the job ad.

**Architecture:** Three independent changes, each its own commit, all in `sluice/cv/`. (1) Harden the compose prompt to a "tailor, don't author" frame that forbids the JD as a source. (2) Extend the pure/deterministic `validate` gate with a numeric floor on the PROFILE region, checked against a bundle-derived permitted set. (3) Add a symmetric structural guard in `engine` so a dropped `PROFILE` header fails closed. The hard gate stays pure, deterministic and offline throughout; the qualitative (numberless) fabrication class is out of scope and filed as #60.

**Tech Stack:** Python 3.12+ standard library only. `pytest` for tests (offline, no Camofox, no network). `ruff` for lint.

**Source of truth:** `docs/superpowers/specs/2026-07-22-cv-profile-fabrication-gate-design.md` (reviewed via `/review-plan` over two full 5-agent rounds, 0 Critical / 0 High, all findings folded). Branch `fix/cv-profile-fabrication-gate` already exists off `main`; the design doc is already committed on it; follow-up issue #60 is filed.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency. (`validate.py` already imports `re`; nothing else is needed.)
- **The hard gate stays pure and deterministic.** `validate()` does no I/O, calls no model, and returns the same violations for the same inputs. A CV is never rendered/served/staged with a non-empty violation list; retry is exactly once, then skip.
- **WORK bullets stay strict (the #5 divergence).** Baseline numbers become permitted **only for the PROFILE**, never for a WORK bullet. `test_baseline_numbers_are_not_permitted_in_a_bullet` (test_cv_validate.py) MUST stay green and untouched.
- **No personal data** in `sluice/` or `tests/`. New fixtures use the synthetic `Example …` company family; the `CLEAN_CV` profile line is invented, number-free, and carries no employer/role/location preference.
- **No `--` (double hyphen) or U+2014 (em dash) in the compose prompt's own prose.** The prompt forbids them, so it must not model them; the only `--` allowed is the `(--)` inside the rule that names the banned token.
- **Conventional Commits.** Commit subjects are `fix(cv): …`.
- **Commit trailer:** every commit message ends with a blank line then `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- **Mutation-witness discipline (per CLAUDE.md):** run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` **once** before witnessing (production-code mutants need content-addressed bytecode). **Mutate by MOVING or DELETING, never ADDING.** **Commit the implementation BEFORE running any witness that restores via `git checkout`** (a git-checkout restore wipes uncommitted work — this has bitten twice). Restore byte-identical after each mutant. Isolate each new test's witness **by node id** and confirm no pre-existing test in the same file reddens — **except mutation row 7**, which is integration-witnessed and deliberately reddens pre-existing clean-CV tests.
- **Human-gated, do NOT apply:** the `.rulesync/rules/CLAUDE.md` CV-gate paragraph under-describes the new citation-free bundle-wide profile check. This is recorded in the PR body for the user to apply; the implementer does not edit `.rulesync/`.
- **The PR body must NOT claim "#30 fixes profile fabrication."** It fixes the prompt *cause* of the observed (qualitative) incident and floors the *verifiable* number/decoy class; the numberless qualitative class stays audit-advisory and is contained by #60.

---

## File Structure

| File | Task | Responsibility of the change |
| --- | --- | --- |
| `sluice/cv/compose.py` | 1 | Replace the `_RULES` "CV RULES" preamble with the hardened tailoring frame; fix `build_prompt`'s `.format(...)` kwargs (`{company}`→`{role}`). The output-format tail is unchanged. |
| `tests/test_cv_compose.py` | 1 | Tests 8 (tailoring/anti-invention wording) and 9 (CV-prompt neutrality guard). |
| `sluice/cv/validate.py` | 2 | Add `_CITE_RE` constant; `_bundle_ids_and_nums` returns a 3-tuple with a `baseline` number pool; `validate()` sweeps the PROFILE region for un-bundle-able numbers. |
| `tests/test_cv_validate.py` | 2 | `_cv_with_profile` helper; Tests 1–5, 6a, 6b, 6c. |
| `sluice/cv/engine.py` | 3 | Symmetric `STRUCTURAL` guard for a missing `PROFILE` header. |
| `tests/test_cv_engine.py` | 3 | Add a number-free `PROFILE` to `CLEAN_CV`; Test 7 (missing-PROFILE structural). |

Tasks are ordered 1 → 2 → 3 and are independently testable. Task 2's profile sweep does **not** run on a header-less `CLEAN_CV` (the sweep is skipped when no `PROFILE` header is present), so Task 2 does not break the engine tests; Task 3's guard is what requires the `CLEAN_CV` fixture edit, so the two land together.

---

## Task 1: Harden the compose prompt (tailor, don't author)

**Files:**
- Modify: `sluice/cv/compose.py` (the `_RULES` string, lines ~5–12; and `build_prompt`'s `_RULES.format(...)` call, lines ~52–53)
- Test: `tests/test_cv_compose.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change. `build_prompt(bundle_text, jd, company, role, name="Your Name", contact="", employers=None, prior_violations=None)` keeps its signature; only the emitted prompt text changes. `_RULES` after this task interpolates `{employer_line}`, `{role}`, `{contact}`, `{name_heading}` — and **no longer** `{company}`.

- [ ] **Step 1: Write the failing tests**

Add both tests to `tests/test_cv_compose.py` (the file already imports `from sluice.cv import compose as C`):

```python
def test_prompt_is_a_tailoring_task_and_forbids_invention():
    # The observed incident's root cause was the profile rule "lead with what
    # {company} values", which points the profile at the JD (not a permitted source).
    # These are WORDING assertions: they pin that the anti-fabrication instructions
    # are present, not that fabrication cannot occur.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst")
    assert "lead with what" not in p                    # the JD-pull is gone
    assert "TAILOR, NOT TO WRITE" in p                  # the task frame
    assert "an invented match is a failure" in p        # the JD-gap omit rule
    assert "Introduce nothing not in the bundle" in p   # hardened profile framing
    assert "—" not in p                 # still no em dash (matches the existing guard)


def test_cv_prompt_expresses_no_role_or_culture_preference():
    # neu-001: the triage guard test_shipped_prompt_expresses_no_role_or_culture_
    # preference (tests/test_prompt.py) covers only the TRIAGE prompt, not this CV
    # _RULES. Mirror it here so the hardened CV prompt cannot grow an opinion about
    # which jobs are good. Tokens are chosen to avoid the prompt's own vocabulary
    # (it names slop words to ban them); reconcile with the triage guard's token
    # list if that changes.
    p = C.build_prompt("BUNDLE", "", "Acme", "Analyst").lower()
    for token in ("startup", "enterprise", "faang", "remote-first", "fast-paced",
                  "unicorn", "rockstar", "ninja", "equity", "salary", "well-funded"):
        assert token not in p, token
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cv_compose.py::test_prompt_is_a_tailoring_task_and_forbids_invention tests/test_cv_compose.py::test_cv_prompt_expresses_no_role_or_culture_preference -v`
Expected: `test_prompt_is_a_tailoring_task_and_forbids_invention` FAILS (the current prompt contains "lead with what" and not "TAILOR, NOT TO WRITE"). `test_cv_prompt_expresses_no_role_or_culture_preference` likely already PASSES (the current prompt has none of those tokens) — that is fine; it is a regression guard for the new prompt, not a red-first test. Note which is red.

- [ ] **Step 3: Replace the `_RULES` preamble in `sluice/cv/compose.py`**

Replace the current `_RULES` bullet preamble (everything from `CV RULES (follow exactly):` down to and including the `- Profile: "I" voice, 2 to 3 sentences, lead with what {company} values.` line) with the hardened preamble below. **Leave the `Output the CV in EXACTLY this format …` tail (from `Output the CV in EXACTLY this format` to the end of the string) exactly as it is** — it holds the `{contact}` and `{name_heading}` placeholders and the format contract `cv_render_v2.py` parses.

```python
_RULES = """CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given a candidate's verified facts in the SOURCE BUNDLE. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You are not authoring a new CV, and you add nothing that is not already in the bundle.
- The SOURCE BUNDLE is the ONLY permitted source. If a detail is not in the bundle, leave it out. Never infer from general knowledge, from the job ad, or from what the role "should" have. NO FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills, certifications, achievements, or motivations that are not in the bundle.
- If the role asks for experience, a skill, or a quality the bundle does not contain, DO NOT add it. Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Every number and named fact survives unchanged from the bundle entry it came from.
- Every WORK EXPERIENCE bullet MUST end with a citation [id] naming the bundle entry it came from (several allowed: [id] [id]). No uncited bullets. Any number in a bullet must appear in a cited entry.
- {employer_line}
- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens (--). En-dash date ranges (12/2025-present) are fine.
- No AI slop (no spearheaded, fostered, drove, leveraged, seamless, passionate about, proven track record). Short sentences. Real metrics only.
- Profile: "I" voice, 2 to 3 sentences. Compose it ONLY from facts in the SOURCE BUNDLE, ordered and emphasised for {role}. Introduce nothing not in the bundle. No motivations, aspirations, or company-specific claims. Any number in the profile must appear in the SOURCE BUNDLE.

Output the CV in EXACTLY this format (what cv_render_v2.py parses):
{contact}

{name_heading}

PROFILE
(2 to 3 sentences)

WORK EXPERIENCE

<Company>
MM/YYYY-MM/YYYY | LOCATION | Role
- bullet ending with a citation [id]

CERTIFICATES
- cert

EDUCATION
- university, dates | degree"""
```

- [ ] **Step 4: Fix `build_prompt`'s `.format(...)` call in `sluice/cv/compose.py`**

The preamble now interpolates `{role}` instead of `{company}`. Change the `_RULES.format(...)` call inside `build_prompt`:

```python
        _RULES.format(contact=contact, name_heading=name.upper(),
                     employer_line=_employer_line(employers), role=role),
```

(`role` is already a `build_prompt` parameter. `company` is still a parameter and is still used in the first line `f"Compose a tailored CV for {name} applying for {role} at {company}."` — only the `.format` kwarg `company=` is dropped.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_cv_compose.py -v`
Expected: all pass, including the pre-existing `test_prompt_contains_bundle_jd_and_forbids_em_dashes` and `test_prompt_excludes_material_not_given` (the new prompt still contains `[id]`, `NO em dashes`, the bundle/jd/company/role, and no `Notion`/`training data`).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: the two new tests pass and the full suite stays green.

- [ ] **Step 7: Lint**

Run: `ruff check sluice tests`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add sluice/cv/compose.py tests/test_cv_compose.py
git commit -m "$(cat <<'EOF'
fix(cv): compose the whole CV from the bundle, tailoring not authoring (#30)

Reframe the _RULES preamble as a tailoring task ("tailor, don't author")
and add an explicit omit-don't-invent rule for the JD-gap, replacing the
"lead with what {company} values" profile pull that pointed the profile
at the job ad. A wording test pins the anti-fabrication instructions; a
CV-prompt neutrality guard mirrors the triage one (which did not cover
this prompt).

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

- [ ] **Step 9: Mutation witnesses (row 6)**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once. Then, restoring via `git checkout -- sluice/cv/compose.py` after each (the implementation is now committed, so this is safe):

- Restore the profile pull **wholesale**: put `lead with what {company} values` back on the profile line AND re-add `company=company` to `_RULES.format(...)`. Run `pytest tests/test_cv_compose.py::test_prompt_is_a_tailoring_task_and_forbids_invention` → expect RED on `"lead with what" not in p`. (Reverting only the line without re-adding `company=` raises `KeyError` and *errors* instead of *fails* — revert both.)
- Delete the `YOUR TASK IS TO TAILOR, NOT TO WRITE …` clause → same test RED on the frame assertion.
- Delete the `If the role asks … an invented match is a failure.` clause → same test RED on the omit assertion.

Restore byte-identical after each (`git checkout -- sluice/cv/compose.py`). Confirm no pre-existing compose test reddens under these mutants.

---

## Task 2: Numeric floor on the PROFILE region (`validate`)

**Files:**
- Modify: `sluice/cv/validate.py` (add `_CITE_RE`; change `_bundle_ids_and_nums` and `validate`)
- Test: `tests/test_cv_validate.py`

**Interfaces:**
- Consumes: `render._CITE_RE` **shape** (duplicated, not imported — the pure gate must not depend on the impure renderer). Test 6c pins the equality.
- Produces:
  - `_bundle_ids_and_nums(bundle_text) -> (ids: dict, nums: dict, baseline: set)` — a **3-tuple** now (was a 2-tuple). Sole caller is `validate()` (verified: no test calls it directly).
  - `validate()` — unchanged signature `validate(cv_text, bundle_text, employers=None, fabrication_decoys=None) -> list`. New violation string: `f"INVENTED PROFILE METRIC {n} not in bundle: {prose}"`. **Note the exact phrase `INVENTED PROFILE METRIC`** — assert on it in full, never on the bare substring `INVENTED` (which also matches the WORK path's `INVENTED METRIC`).

- [ ] **Step 1: Add the `_cv_with_profile` test helper**

At the top of `tests/test_cv_validate.py`, after the existing `_bundle` / `_work_cv` helpers (which use `_ENTRIES` and `_PREFIX_MAP`), add:

```python
def _cv_with_profile(profile, *bullets):
    # A CV whose PROFILE line is caller-controlled, over the _ENTRIES bundle
    # (ES1 metrics=90 body "Ran 42 services…"; EA1 metrics=12 body "Owned 8
    # dashboards."). Default bullet is clean (42 is in ES1's body). One WORK entry,
    # so the reverse-chronology check sees a single start year and passes.
    return "\n".join(["JANE ROE", "", "PROFILE", profile, "",
                      "WORK EXPERIENCE", "",
                      "Example Systems", "02/2023–present | Alfa | Staff Engineer",
                      *(bullets or ["- Ran 42 services [ES1]"]), "",
                      "CERTIFICATES", "- Cert", "", "EDUCATION", "- School"])
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_cv_validate.py`:

```python
def test_invented_profile_metric_flagged():
    # 500 appears nowhere in the bundle -> flagged. The core new coverage.
    v = validate(_cv_with_profile("I scaled platforms to 500 users."), _bundle())
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_number_from_baseline_is_permitted():
    b = _bundle(baseline="Baseline mentions 777 deployments.")
    # In the PROFILE, 777 (a baseline aggregate) is permitted...
    assert validate(_cv_with_profile("I led 777 deployments."), b) == []
    # ...but in a WORK BULLET the same baseline number is still flagged. Bullets
    # stay strict (the #5 divergence); assert both to make the asymmetry explicit.
    v = validate(_cv_with_profile("I build.", "- Led 777 deployments [ES1]"), b)
    assert any("INVENTED METRIC" in x for x in v), v


def test_profile_number_from_an_entry_is_permitted():
    # 8 comes from EA1's body -> in the profile pool (union of all entries).
    assert validate(_cv_with_profile("I built 8 dashboards across teams."), _bundle()) == []


def test_profile_number_from_negatives_is_flagged():
    b = _bundle(negatives=["never claim 500 users"])
    v = validate(_cv_with_profile("I scaled to 500 users."), b)
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_decoy_flagged():
    # Characterisation: the decoy check is already GLOBAL, so a decoy in the profile
    # is flagged without new code. Guards against a future change scoping it to a region.
    v = validate(_cv_with_profile("I built systems at Larkspur."), _bundle(),
                 fabrication_decoys=["Larkspur"])
    assert any("FABRICATED" in x for x in v), v


def test_a_profile_id_citation_code_is_not_an_invented_metric():
    # A stray id-shaped [ES1] in the profile: render strips it, so the reader never
    # sees the '1' (which is not in the pool); the gate must not count it.
    assert validate(_cv_with_profile("I led the platform team [ES1]."), _bundle()) == []


def test_a_profile_non_id_bracketed_number_is_flagged():
    # [500] is NOT id-shaped, so render leaves it in the PDF; the reader sees 500, so
    # it must be checked. This distinguishes the narrow (render-matching) strip from
    # the broad WORK strip -- the inv-001 fail-open.
    v = validate(_cv_with_profile("I scaled to [500] users."), _bundle())
    assert any("INVENTED PROFILE METRIC" in x for x in v), v


def test_profile_strip_matches_render_citation_shape():
    # The profile strip must remove exactly what the renderer removes, or a token
    # validate strips but render delivers reopens the [500] fail-open. Pin equality.
    from sluice.cv.render import _CITE_RE as _RENDER_CITE_RE
    from sluice.cv.validate import _CITE_RE as _VALIDATE_CITE_RE
    for s in ("I scaled [ES1] fast", "I scaled [500] users", "count [es1] here",
              "value [AB12] ok", "unicode [ES१] digit", "plain text"):
        assert _VALIDATE_CITE_RE.sub("", s) == _RENDER_CITE_RE.sub("", s), s
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cv_validate.py -k "profile" -v`
Expected: `test_invented_profile_metric_flagged`, `test_profile_number_from_negatives_is_flagged`, `test_a_profile_non_id_bracketed_number_is_flagged` FAIL (no profile sweep yet — they expect a flag). `test_profile_strip_matches_render_citation_shape` FAILS with `ImportError`/`AttributeError` (`validate._CITE_RE` does not exist yet). The permitted/decoy/id-code tests may already pass (no profile sweep means no flag). Note the reds.

- [ ] **Step 4: Add the `_CITE_RE` constant to `sluice/cv/validate.py`**

Immediately after the existing `_ID_RE = re.compile(...)` definition, add:

```python
# The PROFILE is prose, not bullets, so its citation strip must match what the
# RENDERER delivers, not the WORK-bullet strip. render.strip_citations removes only
# id-shaped [XX9] codes (render._CITE_RE), so a NON-id bracket like [500] SURVIVES
# into the PDF and the profile check must see and check it. This pattern is
# byte-identical to render._CITE_RE (render.py:10); test_profile_strip_matches_render_
# citation_shape pins that equality, because a comment cannot enforce it and a drift
# silently reopens a fabricated-number-ships fail-open. Deliberately DISTINCT from
# _ID_RE: _ID_RE parses bundle-GENERATED codes (uppercase via _prefix); _CITE_RE
# mirrors render's LENIENT strip of whatever the model emitted ([A-Za-z]). (#30)
_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")
```

- [ ] **Step 5: Return a baseline pool from `_bundle_ids_and_nums`**

Replace `_bundle_ids_and_nums` in `sluice/cv/validate.py` with:

```python
def _bundle_ids_and_nums(bundle_text):
    ids, nums, baseline = {}, {}, set()
    cur = None
    seen_id = False
    for line in bundle_text.splitlines():
        if _SECTION_RE.match(line):
            cur = None                      # this entry's lines have ended
            continue
        m = _ID_RE.match(line)
        if m:
            seen_id = True
            cur = m.group(1)
            ids[cur] = line
            after = line[m.end():]          # exclude the id token itself
            nums[cur] = set(re.findall(r"\d+", after))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
        elif not seen_id:
            # Numbers before the first [id] are the BASELINE block -- a permitted
            # SOURCE for the PROFILE (an aggregate summary) but NOT for a WORK bullet,
            # which must trace to its specific cited entry. Negatives are NOT captured
            # here: they land after the last [id] (seen_id True, cur cleared by their
            # === header ===), so they fall into neither pool and stay excluded -- the
            # same exclusion #31 established for bullets. (#30)
            baseline |= set(re.findall(r"\d+", line))
    return ids, nums, baseline
```

- [ ] **Step 6: Add the profile sweep to `validate()`**

In `sluice/cv/validate.py`, change the unpack line at the top of `validate()`:

```python
    ids, nums, baseline = _bundle_ids_and_nums(bundle_text)
```

Then, after the `NOT REVERSE-CHRONOLOGICAL` block and immediately before `in_work = False`, insert the permitted-set computation and switch the loop to also track `in_profile`. The final loop body reads:

```python
    # Permitted numbers for PROFILE prose: any figure the bundle actually contains as
    # a source -- every entry's allowlist plus the baseline block -- but NOT the
    # negatives (excluded by the parse). Broader than a WORK bullet, which is tied to
    # its cited entry, because a profile is an aggregate summary. (#30)
    profile_permitted = baseline.union(*nums.values())
    in_work = False
    in_profile = False
    for line in cv_text.splitlines():
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile = True
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile = False, False
        if in_profile:
            # Prose, NOT a bullet: no citation required or expected (requiring [id]
            # on prose invites a fake-citation launder). Strip citations with render's
            # EXACT shape (_CITE_RE) so the check sees what the reader sees -- narrower
            # than the WORK strip on purpose: a non-id bracket like [500] survives to
            # the PDF and MUST be checked, and the profile has no BAD-CITATION backstop
            # behind the strip. (#30)
            prose = _CITE_RE.sub("", line)
            for n in re.findall(r"\d+", prose):
                if n not in profile_permitted:
                    v.append(f"INVENTED PROFILE METRIC {n} not in bundle: {prose.strip()[:50]}")
        if in_work and line.lstrip().startswith(("-", "•", "*")):
            cites = re.findall(r"\[([^\]]+)\]", line)
            if not cites:
                v.append(f"UNCITED BULLET: {line.strip()[:60]}")
                continue
            bad = [c for c in cites if c not in ids]
            if bad:
                v.append(f"BAD CITATION {bad}: not bundle entries - {line.strip()[:50]}")
                continue
            prose = re.sub(r"\[[^\]]+\]", "", line)
            bullet_nums = set(re.findall(r"\d+", prose))
            union = set().union(*(nums[c] for c in cites))
            invented = bullet_nums - union
            if invented:
                v.append(f"INVENTED METRIC {sorted(invented)} not in {cites}: {prose.strip()[:50]}")
    return v
```

(The WORK-bullet block below `if in_work …` is byte-for-byte the existing code — reproduced here so the whole loop is visible; do not otherwise alter it.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cv_validate.py -v`
Expected: all pass — the 8 new tests AND every pre-existing test, in particular `test_baseline_numbers_are_not_permitted_in_a_bullet` (unchanged: bullets still exclude baseline) and `test_clean_passes` (its `_cv(FULL)` profile "I lead." is number-free).

- [ ] **Step 8: Run the full suite and lint**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: the eight new tests pass and the full suite stays green, lint clean.

- [ ] **Step 9: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_validate.py
git commit -m "$(cat <<'EOF'
fix(cv): gate numbers in the CV profile against the bundle (#30)

Extend the pure/deterministic validate gate with a numeric floor on the
PROFILE region: any number not present in the source bundle (baseline
plus all entries, excluding the negatives block) is an INVENTED PROFILE
METRIC and blocks rendering. The profile citation strip is byte-identical
to render's, so a non-id bracket like [500] -- which render leaves in the
PDF -- is checked rather than stripped, closing a ship-a-fabricated-number
fail-open. WORK bullets stay strict; baseline numbers are permitted only
in the profile aggregate.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

- [ ] **Step 10: Mutation witnesses (rows 1–4, 6b/8, 9, 10, and the integration row 7)**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once. For each mutant: apply by MOVE/DELETE, run the named test by node id, confirm RED, then `git checkout -- sluice/cv/validate.py` to restore (implementation is committed, so safe).

| # | Mutation in `validate.py` | Run (node id) | Expect |
| --- | --- | --- | --- |
| 1 | Delete the whole `if in_profile:` number-check block | `::test_invented_profile_metric_flagged` | RED |
| 2 | `profile_permitted = set().union(*nums.values())` (drop `baseline`) | `::test_profile_number_from_baseline_is_permitted` | RED |
| 3 | Delete the `elif not seen_id:` guard so its body runs under `else:` (negatives feed `baseline`) | `::test_profile_number_from_negatives_is_flagged` | RED |
| 4 | `prose = line` (delete the profile bracket-strip) | `::test_a_profile_id_citation_code_is_not_an_invented_metric` | RED |
| 8 | `prose = re.sub(r"\[[^\]]+\]", "", line)` in the profile block (widen strip) | `::test_a_profile_non_id_bracketed_number_is_flagged` | RED |
| 9 | `profile_permitted = baseline` (drop `*nums.values()`) | `::test_profile_number_from_an_entry_is_permitted` | RED |
| 10 | `_CITE_RE = re.compile(r"\[[A-Za-z]{2}\d+\]")` (drop `\s*`, `\d` for `[0-9]`) | `::test_profile_strip_matches_render_citation_shape` | RED |

Confirm for rows 1–4, 8, 9, 10 that **no pre-existing** test in `test_cv_validate.py` reddens under the mutant (isolation by node id).

Then the **integration row 7** (exempt from the isolation rule):

- Mutation: delete `in_profile = False` from the `WORK EXPERIENCE` arm (`if u == "WORK EXPERIENCE": in_work = True` only) so `in_profile` leaks into WORK.
- Run: `python -m pytest tests/test_cv_validate.py -q`
- Expect: **multiple** pre-existing clean-CV tests RED (e.g. `test_clean_passes`) because WORK date-line years (`2023`, `2020`…) are swept and are not in `profile_permitted`. This is a cross-region leak that *should* break many tests — report it as **integration-witnessed** in the PR body, do not try to isolate it to one test.
- Restore: `git checkout -- sluice/cv/validate.py`.

If any row does NOT redden as stated, stop and diagnose — a non-killing row means the test is not load-bearing (report it in the PR body per "report, do not quietly keep").

---

## Task 3: Fail closed on a missing PROFILE header (`engine`)

**Files:**
- Modify: `sluice/cv/engine.py` (add the structural guard beside the existing WORK-EXPERIENCE guard, ~line 78)
- Modify/Test: `tests/test_cv_engine.py` (add a `PROFILE` to `CLEAN_CV`; add Test 7)

**Interfaces:**
- Consumes: `run_one` / `CvResult` (unchanged). The guard prepends a `STRUCTURAL: …` string to `violations`, exactly like the existing WORK guard, so a missing `PROFILE` header yields `status == "skipped-gate"`.
- Produces: no signature change.

- [ ] **Step 1: Add a number-free PROFILE to `CLEAN_CV`**

In `tests/test_cv_engine.py`, change the first line of the `CLEAN_CV` list so it carries a `PROFILE` section (number-free, so it is clean under both the numeric floor and the structural guard):

```python
CLEAN_CV = "\n".join([
    "JANE ROE", "", "PROFILE", "I build reliable systems.", "", "WORK EXPERIENCE", "",
    "Example Systems", "02/2023–present | Alfa | Staff Engineer", "- Shipped [EF1]", "",
    "Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer",
    "- Grew team from 3 to 8 [EF1]", "",
    "Example Robotics", "09/2017–05/2020 | Charlie | Engineer", "- Coached [EF1]", "",
    "Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer", "- CI [EF1]", "",
    "CERTIFICATES", "- CSM", "EDUCATION", "- Uni",
])
```

- [ ] **Step 2: Run the suite to confirm the fixture change is a safe no-op**

Run: `python -m pytest tests/test_cv_engine.py -q`
Expected: still all green. Adding a number-free `PROFILE` does not trip the (already-landed) numeric floor, and no structural guard exists yet, so nothing changes behaviourally.

- [ ] **Step 3: Write the failing test**

Add to `tests/test_cv_engine.py`:

```python
def test_missing_profile_header_is_structural():
    # A composed CV with no PROFILE header: the profile sweep never runs (fail-open),
    # so the engine must HARD-fail the gate and render nothing. Mirror of
    # test_drifted_work_header_fails_closed.
    no_profile = CLEAN_CV.replace("PROFILE\nI build reliable systems.\n\n", "")
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(no_profile), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x for x in r.violations)
    assert rend.rendered == [], "a CV with no PROFILE header was RENDERED"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `python -m pytest tests/test_cv_engine.py::test_missing_profile_header_is_structural -v`
Expected: FAIL — with no guard, `no_profile` has cited clean bullets and no profile, so `validate()` returns `[]`, the CV renders, `status == "rendered"` and `rend.rendered` is non-empty.

- [ ] **Step 5: Add the structural guard in `sluice/cv/engine.py`**

Immediately after the existing missing-`WORK EXPERIENCE` guard (the `if not any(line.strip().upper() == "WORK EXPERIENCE" …)` block inside the `for _ in range(2):` loop), add the symmetric guard:

```python
        # Symmetric with the WORK-EXPERIENCE guard above: the profile fabrication
        # sweep in validate() is keyed on the exact "PROFILE" header, so a composed CV
        # that drops the header has an empty profile region and is swept -- a silent
        # fail-open. Catch it here and HARD-fail the gate. (#30)
        if not any(line.strip().upper() == "PROFILE" for line in cv_text.splitlines()):
            violations = ["STRUCTURAL: composed CV lacks the exact 'PROFILE' header, "
                          "so the profile fabrication check did not run"] + violations
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_cv_engine.py::test_missing_profile_header_is_structural -v`
Expected: PASS (`status == "skipped-gate"`, a `STRUCTURAL` violation present, nothing rendered).

- [ ] **Step 7: Run the full suite and lint**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: the new test passes and the full suite stays green, lint clean. This is the re-run **after** the `CLEAN_CV` fixture edit that §8 of the spec requires.

- [ ] **Step 8: Commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "$(cat <<'EOF'
fix(cv): a missing PROFILE header fails the gate closed (#30)

The profile fabrication sweep is keyed on the exact PROFILE header, so a
composed CV that drops it would be swept over an empty region and pass --
a silent fail-open. Add a symmetric structural guard in the engine beside
the WORK-EXPERIENCE one, and give CLEAN_CV a number-free PROFILE so the
guard has a header to find.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

- [ ] **Step 9: Mutation witness (row 5)**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once. Then:

- Mutation: delete the new `PROFILE` structural guard block in `engine.py`.
- Run: `python -m pytest tests/test_cv_engine.py::test_missing_profile_header_is_structural -v`
- Expect: RED (`status == "rendered"`, renderer non-empty).
- Restore: `git checkout -- sluice/cv/engine.py`.
- Confirm no other engine test reddens under this mutant.

---

## Finalisation (after all three tasks)

- [ ] **Full green + lint + mutation cache intact**

```bash
python -m pytest -q
ruff check sluice tests
```
Expected: the full suite passes (it includes the new Tests 1–5, 6a, 6b, 6c, 7, 8, 9). No new dependency; `sluice/` still standard-library only.

- [ ] **Confirm the invariants held**

- `validate()` signature unchanged; no I/O added; deterministic.
- `test_baseline_numbers_are_not_permitted_in_a_bullet` still green and untouched.
- No personal data in the diff; new fixtures are `Example …` / invented.
- Three Conventional-Commit commits, each with the trailer.

- [ ] **Pre-push review, per the standing cadence**

Run `/review-pr` (the specialist team) and the CodeRabbit CLI **before** pushing the branch. The PR body must:
- State plainly that this floors the **verifiable** number/decoy profile class and fixes the prompt cause, and that the **numberless qualitative class is NOT closed** (audit-advisory, contained by **#60**) — do not claim "#30 fixes profile fabrication."
- Record the **human-gated `.rulesync` doc tweak**: the canonical CV-gate paragraph is bullet-scoped/citation-anchored and under-describes the new citation-free, bundle-wide profile check — for the user to apply, not the implementer.
- Note that mutation **row 7** is integration-witnessed (reddens pre-existing clean-CV tests by design).

---

## Self-Review (author checklist — completed)

**Spec coverage:** §4.1 prompt hardening → Task 1. §4.2(a) baseline pool + §4.2(b) profile sweep + `_CITE_RE` → Task 2. §4.3 structural guard → Task 3. §5 bullets-stay-strict → asserted in Task 2 Step 2 (`test_profile_number_from_baseline_is_permitted` second assertion) and by leaving `test_baseline_numbers_are_not_permitted_in_a_bullet` untouched. §6 Tests 1–9 → Tasks 1–3. §6 Test 6c drift + §7 rows 1–10 → Task 2/Task 3 witness steps. §8 `CLEAN_CV` fixture → Task 3 Step 1 + the after-edit re-run in Step 7. §10 residuals + §11 `.rulesync` note → Finalisation PR-body step. Every spec section maps to a task.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; every command shows its expected outcome.

**Type consistency:** `_bundle_ids_and_nums` returns the 3-tuple `(ids, nums, baseline)` in Task 2 Step 5 and is unpacked as `ids, nums, baseline = …` in Step 6 — consistent. `_CITE_RE` defined in Step 4, used in Step 6 and Test 6c. The violation phrase `INVENTED PROFILE METRIC` is emitted in Step 6 and asserted in Tests 1/4/6b. `_cv_with_profile` defined in Step 1, used by all Task-2 tests.
