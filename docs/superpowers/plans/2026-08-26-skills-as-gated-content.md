# Skills as Gated Content — Implementation Plan (#168)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a tailored CV emit a `SKILLS` section that passes through the same fabrication gate as every other claim, instead of being injected by a renderer after composition where nothing checks it.

**Architecture:** Each experience entry declares `Skills:` in frontmatter, making skill→role a stored relation. Two containment checks then run in the existing hard gate: **row 1** (a skill named in a WORK bullet must be listed on an entry that bullet cites — the same shape as the existing numeric rule) and **row 2** (every line of the emitted `SKILLS` section must appear in the bundle's source text). Skill names reach the gate structurally via two new `BundleSources` members, never by re-parsing rendered prompt text.

**Tech Stack:** Python 3.12–3.14, stdlib only in `sluice/`. pytest. No new dependencies, no new config keys.

**Spec:** `docs/superpowers/specs/2026-08-26-skills-containment-design.md` — read it alongside this plan. It carries the measurements behind every decision and three rounds of review corrections. Decision labels (SC1–SC9) referenced below are defined there.

---

## Global Constraints

Copied verbatim from the spec and from `CLAUDE.md`. Every task's requirements implicitly include these.

- **Standard library only in `sluice/`.** This work adds no runtime dependency.
- **No new config knob.** The spec introduces none; do not add one.
- **No personal data in `sluice/` or `tests/`.** No employer names, locations, contacts, hostnames, absolute paths. Fixture skills use invented technology-shaped names: `ExampleQL`, `WidgetFramework`, `Widget3`.
- **The fabrication gate stays pure and deterministic.** `validate()` does no I/O. Retry is exactly once, then skip.
- **Empty config abstains.** A blank or absent `Skills:` must never block a lead.
- **Comments explain *why*** — the invariant upheld, the bug prevented. Match the surrounding density.
- **Conventional Commits.** `feat(cv): …`, `test(cv): …`, `docs(cv): …`. release-please reads these.
- **`.rulesync/` is canonical.** Edit `.rulesync/rules/CLAUDE.md`, never the generated `CLAUDE.md`. Regenerate with `npm ci --ignore-scripts && npm run rulesync`.
- **Definition of done, every task:** `./.venv/bin/python -m pytest` green (4989 passing at baseline) and `ruff check sluice tests scripts` clean.
- **Mutation discipline:** where a task says "witness the guard", mutate by **moving or deleting**, never adding, and run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before starting.

---

## Ordering: how this satisfies SC8 without one giant commit

The spec's SC8 says the grammar and gate change "in one commit", because splitting them ships either an always-`UNCITED` section or an ungated one. **Both of those failure modes require the composer to actually emit a `SKILLS` section**, and it only does that when `compose._RULES` asks.

So the constraint that actually matters is an **ordering**, and it is stricter than "one commit" in one respect and looser in another:

1. Bundle plumbing (Tasks 1–2) — inert; adds data and one prompt line.
2. Gate checks (Tasks 3–6) — check a section nothing yet emits.
3. Parser acceptance (Task 7) — the parser may not accept a `SKILLS` section before the gate checks one, or a spontaneously-emitted section reaches the PDF ungated.
4. **`_RULES` last (Task 8)** — only now does the composer request the section.

Every intermediate state is at least as safe as today's `main`. Tasks 1–9 must land in the **same PR**; they need not be one commit.

---

## File Structure

**Modified — production:**

| File | Responsibility added |
|---|---|
| `sluice/core/protocols.py` | `Skills` on `EVIDENCE_KINDS["experience"].fields` |
| `sluice/cv/bundle.py` | `_entry_skills_line`; `BundleSources.skills` + `.source_tokens`; skill-token shape refusal |
| `sluice/cv/validate.py` | `section_spans`' SKILLS region; rows 1 and 2; digit span removal |
| `sluice/cv/parse.py` | `CvDocument.skills`; `_TRAILING_SECTIONS` + `SKILLS`; derived remedy text |
| `sluice/cv/compose.py` | `_RULES`' three additions; `_REQUIRED_HEADERS` |
| `sluice/cv/engine.py` | `section_spans`' third return value at the STYLE-tier call site |
| `sluice/core/doctor.py` | two reconciliation NOTICE rows |
| `sluice/evidence/commands.py` | `experience list` surfacing `Skills:` |

**Modified — tests (guard collisions; each needs a deliberate, argued change):**

`tests/test_cv_bundle.py` (`_oracle` arity), `tests/test_cv_parse.py` (marker equality, `len(gate_markers)`, trailing-content re-anchor), `tests/test_cv_validate.py` (`_validate_line_sets_before_the_extraction`), `tests/template_content.py` (`composer_headings`), `tests/test_prompt_neutrality.py` (`_render` precedence), `tests/test_fixture_name_neutrality.py` (collector extension).

**New tests:** `tests/test_cv_skills_containment.py` — rows 1 and 2, abstain, digit handling.

---

## Task 1: `Skills` on the experience kind

**Files:**
- Modify: `sluice/core/protocols.py` (`EVIDENCE_KINDS["experience"]`)
- Test: `tests/test_evidence_kinds.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `EVIDENCE_KINDS["experience"].fields == ("Company", "Category", "Best For", "Metrics", "Skills")`. Every entry dict from `Vault.read_evidence("experience")` gains `entry["fields"]["Skills"]`, a `str`, `""` when absent.

- [ ] **Step 1: Write the failing test**

```python
def test_experience_declares_skills_and_reads_blank_as_absent(tmp_path):
    """`Skills` is the association SC3 stores on the entry. It must read as "" when the
    key is missing OR blank -- `_evidence_entries` materialises every declared field via
    `fm.get(k, "")`, so EVERY existing note gets `Skills == ""` the day this lands, and
    `_render_evidence_note` writes it blank into every new one. Blank is the DEFAULT
    state, not an edge case, and SC5 treats it as absent everywhere."""
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    assert "Skills" in EVIDENCE_KINDS["experience"].fields

    v = Vault(str(tmp_path))
    d = tmp_path / "Job Applications" / "Experience Library"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(
        "Company: Example Alpha\nSkills:\nVerified: 2026-08-01\n\nBody.\n")
    (d / "beta.md").write_text(
        "Company: Example Beta\nSkills: ExampleQL, WidgetFramework\n"
        "Verified: 2026-08-01\n\nBody.\n")

    by_title = {e["title"]: e for e in v.read_evidence("experience")}
    assert by_title["alpha"]["fields"]["Skills"] == ""
    assert by_title["beta"]["fields"]["Skills"] == "ExampleQL, WidgetFramework"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `./.venv/bin/python -m pytest tests/test_evidence_kinds.py -k skills -v`
Expected: FAIL — `assert "Skills" in ("Company", "Category", "Best For", "Metrics")`.

- [ ] **Step 3: Add the field**

In `sluice/core/protocols.py`, extend the `experience` kind's `fields` tuple:

```python
    "experience": EvidenceKind("Job Applications/Experience Library",
                               ("Company", "Category", "Best For", "Metrics", "Skills"),
                               cited_by_gate=True, read_by_composer=True),
```

Add above it, in the existing comment block's style:

```python
    # `Skills` (#168) is the skill->role association SC3 stores here rather than on the
    # skill note: it is where the gate already reads, so a per-entry frozenset slots in
    # beside `nums` with no name join. No `floor_map` entry -- it has no floor analogue,
    # exactly like the skills kind's own Proficiency/Evidence/Signal Value. It is NOT a
    # second numeric source: cv/bundle.py renders it through `_entry_skills_line`, whose
    # contract is that no digit of it reaches the numeric allowlist.
```

- [ ] **Step 4: Run the test and the full suite**

Run: `./.venv/bin/python -m pytest tests/test_evidence_kinds.py -k skills -v`
Expected: PASS.
Run: `./.venv/bin/python -m pytest`
Expected: PASS. `--skills` and the wizard question are generated from `spec.fields` by `cli.py`'s registry loop and `evidence/wizard.py`, so they appear with no further work — verify with `./.venv/bin/job-sluice experience add --help`, which must now list `--skills`.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/protocols.py tests/test_evidence_kinds.py
git commit -m "feat(cv): declare Skills on the experience evidence kind (#168)"
```

---

## Task 2: `_entry_skills_line`, and two new `BundleSources` members

**Files:**
- Modify: `sluice/cv/bundle.py`
- Modify: `tests/test_cv_bundle.py` (frozen-text re-capture + `_oracle` arity)
- Test: `tests/test_cv_bundle.py`

**Interfaces:**
- Consumes: Task 1's `entry["fields"]["Skills"]`.
- Produces:
  - `_entry_skills_line(entry) -> list[str]` — `[]` for a blank/absent value.
  - `BundleSources(nums, baseline, skills, source_tokens)`; `skills: dict[str, frozenset[str]]` keyed by entry id; `source_tokens: tuple[tuple[str, ...], ...]` — one token SEQUENCE per source block, **not** a flat set.
  - `bundle.SKILL_TOKEN_RE` — the shape a `Skills:` item must match.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_blank_skills_value_contributes_no_line():
    """SC5: blank is absent. An unconditional line would render an empty `Skills:` into
    both prompts -- the same "negative claim it may act on" that `render_composer_bundle`
    refuses one function away -- and every existing note has `Skills == ""` on day one."""
    assert B._entry_skills_line({"id": "AL1", "fields": {"Skills": ""}}) == []
    assert B._entry_skills_line({"id": "AL1", "fields": {}}) == []


def test_skills_are_licensed_per_entry_and_their_digits_are_not():
    """The inverted contract of `_entry_block`: every token here is a SKILL source for
    this entry, and NO digit of it is a numeric source. Folding this into `_entry_block`
    would license every skill digit as a metric at once."""
    b = B.build_bundle(
        entries=[{"company": "Example Alpha", "title": "rebuild", "metrics": "40",
                  "body": "Did the work.", "fields": {"Skills": "Widget3"}}],
        baseline="Example Alpha, 2020-2024.", negatives=[], jd_keywords=[],
        prefix_map={})
    s = B.bundle_sources(b)
    eid = b["entries"][0]["id"]
    assert s.skills[eid] == frozenset({"Widget3"})
    assert "3" not in s.nums[eid]          # the skill's digit is licensed NOWHERE
    assert "3" not in s.baseline


def test_source_tokens_carry_the_words_row_2_checks_against():
    """SC4's vocabulary needs the baseline's and bodies' WORDS. `nums` and `baseline` are
    DIGIT sets, and engine.py hands `validate` the BundleSources and nothing else -- so
    without this member row 2 has no route into the gate at all, and the obvious repair
    (re-parsing rendered text inside validate) is what #174 removed."""
    b = B.build_bundle(
        entries=[{"company": "Example Alpha", "title": "rebuild", "metrics": "40",
                  "body": "Ran a WidgetFramework migration.",
                  "fields": {"Skills": "ExampleQL"}}],
        baseline="Used Widget3 throughout.", negatives=["never claim 91 users"],
        jd_keywords=[], prefix_map={})
    s = B.bundle_sources(b)
    flat = {t for block in s.source_tokens for t in block}
    assert {"ExampleQL", "WidgetFramework", "Widget3"} <= flat
    # The NEGATIVES are not a source -- #31, and now a property of the derivation.
    assert "users" not in flat


def test_source_tokens_are_per_block_so_a_two_word_skill_cannot_match_across_a_seam():
    """Row 2 searches for a skill's token SEQUENCE, so a flat token list would let
    "Widget Framework" match the last word of one entry followed by the first word of the
    next -- an adjacency that exists nowhere in the user's prose."""
    b = B.build_bundle(
        entries=[{"company": "A", "title": "t", "metrics": "", "body": "Ran Widget",
                  "fields": {"Skills": ""}},
                 {"company": "B", "title": "t", "metrics": "", "body": "Framework work",
                  "fields": {"Skills": ""}}],
        baseline="b", negatives=[], jd_keywords=[], prefix_map={})
    s = B.bundle_sources(b)
    assert not any(list(block[i:i + 2]) == ["Widget", "Framework"]
                   for block in s.source_tokens for i in range(len(block)))


def test_a_digit_leading_skill_token_is_refused_at_construction():
    """SC6: span removal makes `Skills:` the first field that SUBTRACTS from the hard
    numeric gate. Without a shape rule, `Skills: 92x` blanks `92` from every bullet
    citing that entry. Fail loudly at construction, this module's house rule."""
    with pytest.raises(ValueError, match="must begin with a letter"):
        B.build_bundle(
            entries=[{"company": "Example Alpha", "title": "t", "metrics": "",
                      "body": "", "fields": {"Skills": "92x"}}],
            baseline="b", negatives=[], jd_keywords=[], prefix_map={})
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_bundle.py -k "skills_line or per_entry or source_tokens or digit_leading" -v`
Expected: FAIL — `AttributeError: module has no attribute '_entry_skills_line'`.

- [ ] **Step 3: Implement**

In `sluice/cv/bundle.py`, after `_baseline_block`:

```python
# A `Skills:` item must BEGIN WITH A LETTER. Span removal (cv/validate.py) makes this the
# first field that SUBTRACTS from the hard numeric gate, so an unconstrained value is a
# laundering path: measured, `Skills: Result 92` would blank `92` from every bullet citing
# the entry, and `92x` / `120ms` slip past a wholly-numeric check. A letter-leading rule
# closes all three. It does NOT close a letter-leading metric shorthand (`p99` still
# licenses removing `99` for its own entry) -- that is a stated residual, visible through
# core/doctor.py, and tightening it further would kill legitimate short names.
SKILL_TOKEN_RE = re.compile(r"^[A-Za-z]")


def _skill_items(entry: dict) -> list[str]:
    """The `Skills:` items for one entry, blank-safe.

    Accepts the comma spelling AND a YAML block list: `_parse_fm_spaced` joins a block
    list to the identical comma string, so both arrive here the same way -- which is why
    a collector written for one shape alone sweeps clean over the other.

    A BLANK value yields [], and that is load-bearing: `_evidence_entries` materialises
    every declared field via `fm.get(k, "")`, so every existing note carries
    `Skills == ""` the day #168 lands. Blank is absent (SC5).
    """
    raw = (entry.get("fields") or {}).get("Skills", "")
    items = [t.strip() for t in raw.split(",") if t.strip()]
    for item in items:
        if not SKILL_TOKEN_RE.match(item):
            raise ValueError(
                f"skill {item!r} must begin with a letter: a digit-leading value would "
                "let the numeric gate's span removal blank a real figure")
    return items


def _entry_skills_line(entry: dict) -> list[str]:
    """The lines ONE entry contributes as SKILL sources.

    Sibling of `_entry_block` and `_baseline_block`, carrying the INVERTED contract:
    every token here is a SKILL source for this entry, and NO DIGIT of it is a numeric
    source. That is why it is a separate function -- `_entry_block`'s stated rule is that
    every line it returns is harvested by `bundle_sources` into `nums`, so folding these
    in would license every digit inside every skill name at once (`Widget3` -> `3`).

    Deliberately not named `_skills_block`, matching `_framing_lines`' precedent for the
    same reason: the `_*_block` names in this module mean "numeric source".
    """
    items = _skill_items(entry)
    return [f"skills={', '.join(items)}"] if items else []
```

In `_source_section`, render it after each entry's block:

```python
    for e in bundle["entries"]:
        lines += _entry_block(e)
        lines += _entry_skills_line(e)
        lines.append("")
```

Extend `BundleSources`:

```python
class BundleSources(NamedTuple):
    nums: dict[str, frozenset[str]]
    baseline: frozenset[str]
    # Row 1's vocabulary: which skills each entry licenses, keyed exactly like `nums` so
    # a bullet's citations union over the two the same way.
    skills: dict[str, frozenset[str]]
    # Row 2's vocabulary: the WORDS of the bundle's source text, as one token SEQUENCE
    # per source block. Separate from the above because row 2 asks a different question
    # (did you invent this) at a different granularity (bundle-wide). `nums`/`baseline`
    # are digit sets and cannot answer it. Sequences rather than a set, because a skill
    # can be two words and no single token is "machine learning".
    source_tokens: tuple[tuple[str, ...], ...]

    @property
    def ids(self):
        return self.nums.keys()
```

In `bundle_sources`, build all four in the one pass:

```python
    nums: dict[str, frozenset[str]] = {}
    skills: dict[str, frozenset[str]] = {}
    blocks: list[tuple] = []
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in nums:
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]
        nums[eid] = frozenset(re.findall(r"\d+", "\n".join(block)))
        items = _skill_items(e)
        skills[eid] = frozenset(items)
        # Row 2's vocabulary, SC4: entry `Skills:` + the entry's BODY + the baseline.
        # Enumerated, never "everything _source_section contributes" -- that larger set
        # also carries the presentation headers and `_entry_block`'s head line, under
        # which an emitted `- Example Alpha` would be a licensed skill token.
        #
        # Kept as one token SEQUENCE PER BLOCK, never flattened: row 2 searches for a
        # skill's token subsequence, and a flat list would invent adjacencies across
        # block seams that exist nowhere in the user's prose.
        blocks.append(tuple(items))
        blocks.append(tuple(_WORD_RE.findall(e.get("body", ""))))
    baseline_block = _baseline_block(bundle)
    baseline = frozenset(re.findall(r"\d+", "\n".join(baseline_block)))
    blocks.append(tuple(_WORD_RE.findall("\n".join(baseline_block))))
    # `skills` and `nums` are keyed in ONE pass, so their key sets are equal by
    # construction rather than by assertion.
    return BundleSources(nums, baseline, skills, tuple(b for b in blocks if b))
```

Add near `SKILL_TOKEN_RE`:

```python
_WORD_RE = re.compile(r"[A-Za-z0-9#+.]+")
```

- [ ] **Step 4: Repair the three frozen tests, in this commit**

`_entry_skills_line` changes the prompt text, so `test_the_rendered_prompt_has_not_drifted`, `test_the_composer_prompt_has_not_drifted` and `test_the_allowlist_still_matches_the_frozen_prompt` all go red. Re-capture the two frozen literals, and read the diff yourself: the freeze is a ratchet against a literal, not against the world, and only a human reading that diff distinguishes a deliberate prompt change from a silent widening.

For the allowlist test, the break is **arity**, not digits — `_oracle` returns a 2-tuple and `BundleSources` is now 4-field. Change the assertion to compare only what the oracle models:

```python
    b = _frozen_bundle()
    s = B.bundle_sources(b)
    # `_oracle` transcribes the pre-#174 NUMERIC harvester and never modelled skills, so
    # it can only speak to `nums` and `baseline`. Comparing the whole tuple would be
    # comparing it to fields it has no opinion about; teaching it about skills would make
    # it derive from the code under test, which its own docstring forbids.
    assert (s.nums, s.baseline) == _oracle(FROZEN_BUNDLE_TEXT)
```

Keep `FROZEN_ENTRIES`' skills values **digit-free** (`ExampleQL`, `WidgetFramework`), so the oracle keeps agreeing. Witness the digit case over a separate bundle — the literal-free style `test_a_skills_digit_is_licensed_in_neither_pool` already uses.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/python -m pytest`
Expected: PASS. If `tests/test_onboard_questions.py` also reds, it is the same arity break — apply the same two-field comparison.

- [ ] **Step 6: Witness the digit-isolation guard**

Move `_entry_skills_line(e)` into `_entry_block`'s return (do not add a second call — an added check is an equivalent mutant, the original still fires). Run `./.venv/bin/python -m pytest tests/test_cv_bundle.py -k per_entry -v`; expect FAIL on `"3" not in s.nums[eid]`. Restore.

- [ ] **Step 7: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_bundle.py tests/test_onboard_questions.py
git commit -m "feat(cv): carry per-entry skills and source tokens on BundleSources (#168)"
```

---

## Task 3: the `SKILLS` region in `section_spans`

**Files:**
- Modify: `sluice/cv/validate.py` (`section_spans`)
- Modify: `sluice/cv/engine.py` (STYLE-tier call site)
- Modify: `tests/test_cv_validate.py` (`_validate_line_sets_before_the_extraction`)
- Test: `tests/test_cv_skills_containment.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `section_spans(cv_text) -> (profile_lines, work_bullets, skills_lines)`, a **3-tuple**. Both existing call sites must be updated.

- [ ] **Step 1: Write the failing tests — all three preservation cases**

```python
_CV = """PROFILE
I did the work.

WORK EXPERIENCE

Example Alpha
01/2020-01/2024 | LOCATION | Engineer
- Ran the rebuild [AL1]

SKILLS
- ExampleQL
- WidgetFramework
{tail}"""


def test_skills_lines_are_collected_into_their_own_region():
    _p, work, skills = V.section_spans(_CV.format(tail=""))
    assert [t.strip() for _n, t in skills] == ["- ExampleQL", "- WidgetFramework"]
    assert [t.strip() for _n, t in work] == ["- Ran the rebuild [AL1]"]


def test_a_publications_bullet_after_skills_is_still_citation_checked():
    """Direction 1, measured on main: this bullet IS citation-checked today. A SKILLS
    region that simply cleared `in_work` would swallow it, and a fabricated figure in it
    would never be number-checked."""
    tail = "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n"
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert any("92%" in t for _n, t in work)


def test_a_publications_bullet_after_certificates_is_still_uncited_clean():
    """Direction 2, measured on main: this bullet is NOT citation-checked today. The
    obvious repair for direction 1 -- revert to the WORK region on any unmodelled header
    -- regresses this one. Both directions are non-negotiable."""
    tail = ("\nCERTIFICATES\n- Example Practitioner\n"
            "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n")
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert not any("92%" in t for _n, t in work)


def test_bullets_resume_the_work_region_after_the_skills_run_ends():
    """The case a contiguous-run implementation gets wrong: no intervening header at all."""
    tail = "\n- Ran the migration [AL1]\n"
    _p, work, skills = V.section_spans(_CV.format(tail=tail))
    assert [t.strip() for _n, t in skills] == ["- ExampleQL", "- WidgetFramework"]
    assert any("migration" in t for _n, t in work)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k section_spans -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 2)`.

- [ ] **Step 3: Implement the contiguous bullet run**

In `section_spans`, add `skills` to the returns and track the region as a run that **does not clear `in_work`**:

```python
    profile, work, skills = [], [], []
    in_work = False
    in_profile = False
    in_skills = False
    for i, line in enumerate(cv_text.splitlines(), 1):
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile, in_skills = True, False
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile, in_skills = True, False, False
            continue
        if u == "SKILLS":
            # A CONTIGUOUS BULLET RUN that deliberately does NOT clear `in_work`.
            # Clearing it would swallow a PUBLICATIONS section emitted afterwards, whose
            # bullets ARE citation-checked today -- measured -- and a fabricated figure
            # there would ship unchecked. Reverting to WORK on any unmodelled header
            # instead regresses the mirror case (a PUBLICATIONS bullet after
            # CERTIFICATES, uncited-clean today). A run that ends at the first non-bullet
            # line satisfies both, without the "any all-caps line ends the section"
            # generalisation this function's docstring names as a gate WEAKENING.
            in_skills, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile, in_skills = False, False, False
        is_bullet = line.lstrip().startswith(_SKILLS_MARKERS)
        if in_skills and not is_bullet:
            in_skills = False              # the run ends at the first non-bullet line
        if in_skills:
            skills.append((i, line))
            continue                       # a skills line is NOT also a work bullet
        if in_profile:
            profile.append((i, line))
        if in_work and line.lstrip().startswith(_WORK_BULLET_MARKERS):
            work.append((i, line))
    return profile, work, skills
```

Add BOTH marker tuples above `section_spans`, as NAMED module constants, and have both
`startswith` calls take the name. The existing AST guard recovers literal tuples and indexes
`[0]`; with two non-equal literals in the module, selecting the WORK one *by value* would make
its equality assertion a tautology. Names let the guard read two `ast.Assign` nodes by name.

```python
# The WORK set. Must stay EXACTLY equal to cv/parse.py's `_BULLET_MARKERS` -- too narrow
# refuses a gate-clean CV, too wide is a citation-check bypass.
_WORK_BULLET_MARKERS = ("-", "•", "*")

# The SKILLS region's markers, and they must EQUAL what cv/parse.py accepts for SKILLS.
# SKILLS is the FIRST trailing section the hard gate checks, which is exactly why: a
# marker the parser accepts here and this function does not is a gate BYPASS -- the line
# parses into CvDocument.skills, renders into the PDF, and is never containment-checked.
# `_BULLET_MARKERS` in cv/parse.py stays separate and narrow: it is the WORK set, pinned
# equal to this module's own citation-check tuple.
_SKILLS_MARKERS = ("-", "•", "*", "–", "—")
```

- [ ] **Step 4: Update both call sites**

`validate()`:

```python
    profile_lines, work_bullets, skills_lines = section_spans(cv_text)
```

`cv/engine.py`'s STYLE scoping — skills lines are **excluded** from that tier:

```python
            # `section_spans` returns a third region since #168. Skills lines are
            # deliberately NOT in the STYLE tier's scope: a slop complaint about a bare
            # skill name is answerable only by renaming the skill, which is the same
            # reasoning that already scopes this tier away from employer and certificate
            # lines.
            profile_lines, work_lines, _skills_lines = section_spans(cv_text)
```

- [ ] **Step 5: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -v`
Expected: PASS, all four.

- [ ] **Step 6: Resolve the `_validate_line_sets_before_the_extraction` collision**

That sweep's random alphabet **already contains `"SKILLS"`**, and its transcribed reference diverges on 136/2000 rows at the shipped seed. Do **not** "update the reference" from the new code — that is the assert-the-code-equals-itself hazard its own comment names. Extend the transcription by hand to model the contiguous run, and leave a comment recording that the divergence was expected and why.

- [ ] **Step 7: Run the full suite and commit**

```bash
./.venv/bin/python -m pytest
git add sluice/cv/validate.py sluice/cv/engine.py tests/test_cv_validate.py tests/test_cv_skills_containment.py
git commit -m "feat(cv): give section_spans a named SKILLS region (#168)"
```

---

## Task 4: row 2 — the emitted section must come from the bundle

**Files:**
- Modify: `sluice/cv/validate.py` (`validate`)
- Test: `tests/test_cv_skills_containment.py`

**Interfaces:**
- Consumes: `BundleSources.source_tokens` (Task 2), `section_spans`' third region (Task 3).
- Produces: violation string `f"UNSOURCED SKILL {item!r}: not in the bundle"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_two_word_skill_is_matched_as_a_sequence_not_a_token():
    """Row 2's vocabulary is source TEXT, so whole-line set membership is undefined --
    no single token is "Widget Framework". Both rows use one subsequence primitive."""
    s = _sources(body="Ran a Widget Framework migration.", skills="",
                 baseline="Example Alpha.")
    assert V.validate(_cv_with_skills(["Widget Framework"]), s) == []
    assert any("UNSOURCED SKILL" in x
               for x in V.validate(_cv_with_skills(["Framework Widget"]), s))


def test_an_emitted_skill_absent_from_the_bundle_is_refused():
    s = _sources(body="Ran the rebuild.", skills="ExampleQL", baseline="Example Alpha.")
    v = V.validate(_cv_with_skills(["ExampleQL", "Kubernetes"]), s)
    assert any("UNSOURCED SKILL" in x and "Kubernetes" in x for x in v)
    assert not any("ExampleQL" in x for x in v)


def test_row_2_licenses_a_skill_from_the_baseline_or_a_body():
    """SC4. `_RULES` and `_DERIVED_NEGATIVE_PROMPT` both license the BASELINE CV and
    verified entries, so a gate licensing only `Skills:` refuses what the prompt in the
    same run requires -- measured as `skipped-gate` on every lead."""
    s = _sources(body="Ran a WidgetFramework migration.", skills="",
                 baseline="Used Widget3 throughout.")
    assert V.validate(_cv_with_skills(["WidgetFramework", "Widget3"]), s) == []


def test_row_2_fails_closed_when_no_entry_declares_skills():
    """SC5, and the most serious finding of the review: making row 2 CONDITIONAL on a
    non-empty vocabulary turned fail-closed into fail-OPEN. `section_spans` is pure over
    text, so its SKILLS region exists regardless -- a model-emitted section on an
    un-annotated vault would then be checked by NOTHING and render, where today it yields
    UNCITED BULLET. Only the _RULES request is conditional; this check always runs."""
    s = _sources(body="Ran the rebuild.", skills="", baseline="Example Alpha.")
    v = V.validate(_cv_with_skills(["Kubernetes"]), s)
    assert any("UNSOURCED SKILL" in x for x in v)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k row_2 -v`
Expected: FAIL — no `UNSOURCED SKILL` produced.

- [ ] **Step 3: Implement**

Add the row-2 matcher beside `_names_skill`. Task 5 defines `_tokens`, `_subseq` and
`_names_skill`; if Task 5 has not run yet, define `_tokens` and `_subseq` here and let Task 5
reuse them. **They must be ONE implementation** — two copies let the vocabulary the gate builds
drift from the one it searches with.

```python
def _in_source(blocks, item):
    """True when `item`'s token sequence appears contiguously in ANY source block.

    Case-INSENSITIVE, unlike row 1: this matches a whole emitted item against a corpus,
    with no sentence to collide with, and a case-sensitive rule would refuse a skill whose
    note is filed lowercase. Per-BLOCK rather than over a flattened corpus, so a two-word
    skill cannot match an adjacency invented at a block seam.
    """
    needle = [t.casefold() for t in _tokens(item)]
    return bool(needle) and any(
        _subseq([t.casefold() for t in block], needle) for block in blocks)
```

Inside `validate`'s line-ordered loop, add a third branch:

```python
    skills_by_line = dict(skills_lines)
    ...
        if i in skills_by_line:
            # ROW 2 (SC4): did you invent this? Licensed by the bundle's SOURCE TEXT --
            # entry `Skills:` + entry bodies + the baseline -- because `compose._RULES`
            # and `_DERIVED_NEGATIVE_PROMPT` license exactly those, and #168 asks that an
            # emitted skill "appear in the source bundle", not in `Skills:` alone.
            #
            # ALWAYS RUNS. Never conditional on a non-empty vocabulary: `section_spans`
            # is pure over text, so a section emitted on an un-annotated vault would
            # otherwise be checked by nothing at all. Fail closed.
            #
            # Normalised comparison, unlike row 1's case-sensitive in-prose scan: this
            # compares a whole emitted line against the vocabulary, with no sentence to
            # collide with, and a case-sensitive rule here would refuse every skill whose
            # note is filed lowercase.
            item = _CITE_RE.sub("", skills_by_line[i]).lstrip("-•*–— ").strip()
            if item and not _in_source(sources.source_tokens, item):
                v.append(f"UNSOURCED SKILL {item!r}: not in the bundle")
```

- [ ] **Step 4: Run the tests, then the suite**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -v` → PASS.
Run: `./.venv/bin/python -m pytest` → PASS.

- [ ] **Step 5: Witness the SC4 width guard**

Narrow the vocabulary to `sources.skills` alone (round 2's rejected design — **delete** the body and baseline contributions in `bundle_sources`, do not add a filter). Run `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k licenses_a_skill -v`; expect FAIL. This is the guard nothing else covers: the "prompt/gate agreement" test reads strings, and row 2's other side is a computed frozenset, so that narrowing leaves `_RULES` byte-identical and every other guard green. Restore.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_skills_containment.py
git commit -m "feat(cv): refuse an emitted skill absent from the bundle (#168)"
```

---

## Task 5: row 1 — a bullet's skill must belong to a cited entry

**Files:**
- Modify: `sluice/cv/validate.py` (`validate`)
- Test: `tests/test_cv_skills_containment.py`

**Interfaces:**
- Consumes: `BundleSources.skills` (Task 2).
- Produces: violation string `f"MISATTRIBUTED SKILL {item!r} not in {cites}: …"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_skill_named_in_a_bullet_citing_the_wrong_entry_is_refused():
    s = _two_entry_sources(al_skills="ExampleQL", be_skills="WidgetFramework")
    v = V.validate(_bullet("Ran the ExampleQL work [BE1]"), s)
    assert any("MISATTRIBUTED SKILL" in x and "ExampleQL" in x for x in v)


def test_row_1_abstains_when_a_cited_entry_declares_no_skills():
    """SC5, measured in review: with the abstain condition bundle-wide instead of
    per-entry, a bullet citing an un-annotated entry and naming a skill present in THAT
    ENTRY'S OWN BODY was a hard violation -- the gate refusing a token from the cited
    entry's own source line."""
    s = _two_entry_sources(al_skills="ExampleQL", be_skills="")
    assert V.validate(_bullet("Ran the ExampleQL work [BE1]"), s) == []


def test_row_1_abstains_on_a_blank_value_not_only_a_missing_key():
    """`_evidence_entries` materialises every declared field, so `Skills == ""` is the
    PRODUCTION shape and a key-omitting fixture proves nothing. A presence-keyed
    implementation passes that fixture while re-opening the over-fire above."""
    s = _two_entry_sources(al_skills="ExampleQL", be_skills="   ")
    assert V.validate(_bullet("Ran the ExampleQL work [BE1]"), s) == []


def test_row_1_is_case_sensitive_so_ordinary_english_never_collides():
    """SC9: row 1 scans free prose, where a short common-word skill name would otherwise
    collide with its ordinary sense. Every failure mode here is an UNDER-fire."""
    s = _two_entry_sources(al_skills="Go", be_skills="WidgetFramework")
    assert V.validate(_bullet("Ran the go to market work [BE1]"), s) == []
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k row_1 -v`
Expected: FAIL — no `MISATTRIBUTED SKILL` produced.

- [ ] **Step 3: Implement**

In the WORK-bullet branch, after the citation checks:

```python
            # ROW 1 (SC2): is this attributed to the right role? Licensed by the entries
            # THIS BULLET CITES -- the identical shape as the numeric rule two lines up,
            # which permits a figure only if a cited entry carries it.
            #
            # ABSTAINS PER-ENTRY (SC5): if ANY cited entry declares no non-empty
            # `Skills:`, this bullet is not checked at all. Measured otherwise: on a
            # partially annotated vault a bullet citing an un-annotated entry, naming a
            # skill from that entry's own body, was a hard violation.
            #
            # CASE-SENSITIVE (SC9): this scans free prose. A candidate whose inventory
            # lists a short common-word skill must not be blocked for using that word in
            # its ordinary sense. Every failure mode is an under-fire, which is the
            # direction a hard gate must err.
            if all(sources.skills.get(c) for c in cites):
                licensed = set().union(*(sources.skills[c] for c in cites))
                vocabulary = set().union(*sources.skills.values())
                for item in sorted(vocabulary - licensed):
                    if _names_skill(prose, item):
                        v.append(f"MISATTRIBUTED SKILL {item!r} not in {cites}: "
                                 f"{prose.strip()[:50]}")
```

Add the matcher above `validate`:

```python
def _tokens(text):
    """Case-PRESERVING alphanumeric runs. Deliberately not core/stem.py: stemming answers
    a RELEVANCE question (right for rank()), and this is an IDENTITY question -- a
    licensed `Widget` would license an emitted `Widgeting`. `stem.tokens` is also
    alphabetic-only, so it destroys the digit-bearing names span removal exists to
    protect."""
    return _WORD_RE.findall(text)


def _subseq(hay, needle):
    """True when `needle` appears as a CONTIGUOUS subsequence of `hay`.

    The ONE matching primitive both rows use -- row 1 case-sensitively over a bullet's
    prose, row 2 case-insensitively over each source block. Never substring containment:
    `"java" in "javascript"` is the bug rank() was rewritten to remove. Two copies of this
    would let the vocabulary the gate BUILDS drift from the one it SEARCHES with.
    """
    if not needle:
        return False
    return any(list(hay[i:i + len(needle)]) == list(needle)
               for i in range(len(hay) - len(needle) + 1))


def _names_skill(text, skill):
    """Row 1: `skill`'s token sequence, CASE-SENSITIVELY, in `text`."""
    return _subseq(_tokens(text), _tokens(skill))
```

- [ ] **Step 4: Run, then the suite**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -v` → PASS.
Run: `./.venv/bin/python -m pytest` → PASS.

- [ ] **Step 5: Witness the abstain guard**

Delete the `if all(sources.skills.get(c) for c in cites):` condition (unindent the body — a **deletion**, not an added check). Run `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k abstains -v`; expect both abstain tests to FAIL. Restore.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_skills_containment.py
git commit -m "feat(cv): refuse a skill attributed to an uncited entry (#168)"
```

---

## Task 6: digit handling — span removal, decoupled from row 1

**Files:**
- Modify: `sluice/cv/validate.py`
- Test: `tests/test_cv_skills_containment.py`

**Interfaces:**
- Consumes: `BundleSources.skills`, `.source_tokens`.
- Produces: no new violation strings — this **suppresses** false ones.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_digit_bearing_skill_name_is_not_a_fabricated_metric():
    """Measured on main BEFORE this feature: `- Ran the migration on Widget3 … [AL1]`
    reports INVENTED METRIC ['3']. The only actionable answer is to delete a true skill
    name -- the LOCATION shape. Latent today; #168 makes it the main path."""
    s = _two_entry_sources(al_skills="Widget3", be_skills="WidgetFramework")
    assert V.validate(_bullet("Ran the Widget3 migration [AL1]"), s) == []


def test_profile_removal_uses_entry_skills_not_the_row_2_vocabulary():
    """The wide vocabulary would let an ordinary baseline word blank an adjacent digit."""
    s = _two_entry_sources(al_skills="", be_skills="", baseline="We shipped Alpha 92 times.")
    v = V.validate(_cv(profile="Alpha 92 shipments.", bullet="Ran it [AL1]"), s)
    assert any("INVENTED PROFILE METRIC" in x for x in v)


def test_the_same_holds_in_profile_prose():
    """PROFILE has no citation to hang the per-entry rule on, so it uses the union of
    entry `Skills:` -- consistent with `profile_permitted` already being bundle-wide."""
    s = _two_entry_sources(al_skills="Widget3", be_skills="")
    cv = _cv(profile="Deep experience with Widget3.", bullet="Ran it [AL1]")
    assert not any("INVENTED PROFILE METRIC" in x for x in V.validate(cv, s))


def test_span_removal_does_not_depend_on_row_1_passing():
    """The review's sharpest finding: while removal was gated on row 1's verdict, three
    decisions combined into the harm it exists to prevent. Row 1 is case-sensitive (SC9)
    and abstains on an un-annotated cited entry (SC5), and skill digits sit in NO numeric
    pool -- so each row-1 UNDER-fire became a hard INVENTED METRIC on a skill the user
    really declared. Removal is a numeric-gate concern, not an attribution verdict."""
    # wrong case: row 1 does not match, and must still not produce a numeric violation
    s = _two_entry_sources(al_skills="Widget3", be_skills="WidgetFramework")
    assert not any("INVENTED" in x for x in V.validate(_bullet("Ran widget3 [AL1]"), s))
    # abstaining entry: same
    s2 = _two_entry_sources(al_skills="Widget3", be_skills="")
    assert not any("INVENTED" in x
                   for x in V.validate(_bullet("Ran Widget3 [AL1][BE1]"), s2))


def test_a_fabricated_number_beside_a_licensed_skill_is_still_caught():
    """The OVER-fire direction, which the guard list originally omitted. Removing a span
    must not become a hole."""
    s = _two_entry_sources(al_skills="Widget3", be_skills="")
    v = V.validate(_bullet("Ran Widget3 and cut cost by 92% [AL1]"), s)
    assert any("INVENTED METRIC" in x and "92" in x for x in v)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k digit -v`
Expected: FAIL — `INVENTED METRIC ['3']`.

- [ ] **Step 3: Implement**

Add above `validate`:

```python
def _strip_skill_spans(text, skills):
    """Remove each licensed skill's own span before `\\d+` extraction.

    The same technique this module already applies to citations, and for the same reason:
    a digit that is part of a NAME is not a metric. Without it `Widget3` reads as the
    number 3 and the only actionable answer is to delete a true skill name.

    CASE-INSENSITIVE, and decided WITHOUT reference to row 1's verdict. Row 1 answers a
    different question (misattribution) under a case-SENSITIVE rule that deliberately
    under-fires; gating removal on it converted every one of those under-fires into a
    hard INVENTED METRIC. `cv/bundle.py`'s letter-leading `SKILL_TOKEN_RE` is what stops
    this subtracting a real figure.
    """
    for skill in sorted(skills, key=len, reverse=True):
        text = re.sub(re.escape(skill), " ", text, flags=re.IGNORECASE)
    return text
```

In the WORK-bullet branch, before extracting `bullet_nums`:

```python
            cited_skills = set().union(*(sources.skills.get(c, frozenset())
                                         for c in cites)) if cites else set()
            bullet_nums = set(re.findall(r"\d+", _strip_skill_spans(prose, cited_skills)))
```

In the PROFILE branch — note the vocabulary, which is **not** SC4's:

```python
            # The union of entry `Skills:`, NEVER `sources.source_tokens`. PROFILE has no
            # citation to scope by, but licensing removal from SC4's row-2 vocabulary (the
            # baseline's and bodies' WORDS) would let any ordinary word in the user's prose
            # blank an adjacent digit -- a hole in the numeric gate rather than a fix to it.
            all_skills = set().union(*sources.skills.values()) if sources.skills else set()
            prose = _strip_skill_spans(_CITE_RE.sub("", line), all_skills)
```

- [ ] **Step 4: Run, then the suite**

Run: `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -v` → PASS.
Run: `./.venv/bin/python -m pytest` → PASS.

- [ ] **Step 5: Witness the independence guard**

Change `cited_skills` to `licensed` (row 1's set, computed only in the `all(...)` arm) — a **move**, not an addition. Run `./.venv/bin/python -m pytest tests/test_cv_skills_containment.py -k does_not_depend -v`; expect FAIL. Restore.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_skills_containment.py
git commit -m "fix(cv): stop a digit-bearing skill name reading as a fabricated metric (#168)"
```

---

## Task 7: the parser accepts `SKILLS`

**Files:**
- Modify: `sluice/cv/parse.py`
- Modify: `tests/test_cv_parse.py` (two guard collisions)
- Test: `tests/test_cv_parse.py`

**Interfaces:**
- Consumes: `validate._SKILLS_MARKERS` (Task 3), for the equality guard only.
- Produces: `CvDocument.skills: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_skills_section_parses_into_the_document():
    doc = P.parse_cv(_CV_WITH_SKILLS)
    assert doc.skills == ["ExampleQL", "WidgetFramework"]


def test_the_skills_region_markers_equal_what_the_gate_collects():
    """SKILLS is the FIRST trailing section the hard gate checks, so this equality is a
    gate property, not tidiness: a marker the parser accepts and `section_spans` does not
    is a BYPASS -- the line parses into CvDocument.skills, renders into the PDF, and is
    never containment-checked. Derived from both modules, never hand-listed."""
    from sluice.cv.validate import _SKILLS_MARKERS
    assert set(_SKILLS_MARKERS) == set(P._TRAILING_MARKERS)


def test_the_repeated_header_remedy_names_every_trailing_section():
    """The remedy text used to hardcode 'CERTIFICATES and EDUCATION'. Extending the
    refusal to SKILLS without the message produces a refusal naming the wrong sections."""
    with pytest.raises(P.CvParseError, match="SKILLS"):
        P.parse_cv(_CV_WITH_TWO_SKILLS_HEADERS)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_parse.py -k "skills_section or region_markers or remedy" -v`
Expected: FAIL — `unmodelled section header 'SKILLS'`.

- [ ] **Step 3: Implement**

```python
@dataclass
class CvDocument:
    name: str
    contact: str
    profile: str
    work: list[Role]
    skills: list[str]
    certificates: list[str]
    education: list[str]
```

```python
_TRAILING_SECTIONS = frozenset({"SKILLS", "CERTIFICATES", "EDUCATION"})
```

Derive the remedy text rather than adding a third literal:

```python
            names = " and ".join(sorted(_TRAILING_SECTIONS))
            raise CvParseError(
                f"{header} appears twice: entries under the second one would be dropped "
                f"from the PDF. Emit {names} at most once each, with every entry under a "
                f"single header.")
```

- [ ] **Step 4: Resolve the two guard collisions**

`test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks` asserts `len(gate_markers) == 1` over every literal-tuple `startswith()` in `cv/validate.py`; Task 3 took that to 2. **Widen the pin deliberately, and select by NAME.** Task 3 bound both tuples to named constants precisely so this is possible: recover the two `ast.Assign` nodes by their target names (`_WORK_BULLET_MARKERS`, `_SKILLS_MARKERS`) and assert `_WORK_BULLET_MARKERS == parse._BULLET_MARKERS` and `_SKILLS_MARKERS == parse._TRAILING_MARKERS`. Selecting the WORK tuple by VALUE — "the one that matches `_BULLET_MARKERS`" — turns the assertion into a tautology, the same assert-the-code-equals-itself hazard the spec names for `_oracle` and `_validate_line_sets_before_the_extraction`. Assert both names were found, so a rename cannot make the sweep pass vacuously.

`test_unmodelled_trailing_content_is_refused_rather_than_left_unconsumed` stops raising once `_TRAILING_SECTIONS` gains `SKILLS`. **Re-anchor it on `PUBLICATIONS`**, which remains unmodelled.

- [ ] **Step 5: Add a SKILLS sibling to the implication sweep**

`tests/test_cv_parse.py`'s existing sweep is a three-way parametrize over separator × terminal × start-month applied to the first role's date range in one fixture — **not** a general gate-clean ⇒ no-raise sweep. Add a **sibling** parametrize over the SKILLS grammar (each marker in `_TRAILING_MARKERS` × optional space after the marker), asserting `validate(...) == [] ⇒ parse_cv(...) does not raise`.

- [ ] **Step 6: Run the full suite and commit**

```bash
./.venv/bin/python -m pytest
git add sluice/cv/parse.py tests/test_cv_parse.py
git commit -m "feat(cv): model a SKILLS section in the CV parser (#168)"
```

---

## Task 8: the prompt asks for the section

**Files:**
- Modify: `sluice/cv/compose.py` (`_RULES`, `build_prompt`, `_REQUIRED_HEADERS`)
- Modify: `tests/template_content.py` (`composer_headings`)
- Modify: `tests/test_prompt_neutrality.py` (`_render`)
- Test: `tests/test_cv_compose.py`

**Interfaces:**
- Consumes: `bundle["entries"]`'s `Skills:` values, via a new `skills_block` argument.
- Produces: `build_prompt(..., skills_requested: bool = False)`.

**This is the task that turns the feature on.** Everything before it is inert.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_skills_block_is_absent_when_no_entry_declares_skills():
    """SC5's request abstain. An unconditional block against a gate that can license
    nothing was the review's first Critical: prompt demands the section, every line
    violates, one retry, skipped-gate -- on every lead, on every vault at upgrade."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=False)
    assert "SKILLS" not in p


def test_the_skills_block_is_present_when_an_entry_declares_skills():
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=True)
    assert "SKILLS" in p


def test_the_prompt_states_the_rule_row_2_enforces():
    """A `_RULES` rule permitting what a containment row forbids is the mutant the guard
    table names, and the design shipped it twice. This test READS the rule text."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=True)
    assert "SOURCE BUNDLE" in p and "SKILLS" in p
```

- [ ] **Step 2: Run and confirm they fail**

Run: `./.venv/bin/python -m pytest tests/test_cv_compose.py -k skills -v`
Expected: FAIL — `unexpected keyword argument 'skills_requested'`.

- [ ] **Step 3: Implement**

Add to `_RULES`' rule list, phrased to name no skill so it cannot go stale:

```
- You may name a skill in a WORK EXPERIENCE bullet only if an entry that bullet cites lists that skill. If no cited entry lists it, leave it out.
- Every line of the SKILLS section must come from the SOURCE BUNDLE. Do not add a skill the bundle does not contain.
```

Add `{skills_block}` to the format contract, and render it conditionally:

```python
_SKILLS_BLOCK = """
SKILLS
- skill
"""


def build_prompt(bundle_text, jd, company, role, *, name, contact="",
                 employers=None, prior_violations=None, slop_allow=None,
                 skills_requested=False):
    ...
        _RULES.format(..., skills_block=_SKILLS_BLOCK if skills_requested else "")
```

Add `"SKILLS"` to `_REQUIRED_HEADERS`. That set is `_is_envelope_aside`'s "these lines are real CV content" test, so adding `SKILLS` only *widens* what survives unwrapping and cannot fail a CV closed.

**Do NOT add an "envelope survival" guard.** Measured on shipped code with `_REQUIRED_HEADERS` unchanged: a realistic full-wrap #28 envelope leaves a bulleted `SKILLS` section entirely intact, because `_is_envelope_aside` already returns False for it. SC7's bulleted shape is what carries the protection, so such a guard is an equivalent mutant — it would pass whether or not the code was correct.

- [ ] **Step 4: Re-anchor `composer_headings()` — do NOT render `_RULES`**

Four reviewers independently measured that deriving from *rendered* `_RULES` puts the substituted name into the set, because `{name_heading}` is `name.upper()` on its own line and matches the all-caps-alphabetic filter. That set is the **allowlist** in three template no-content guards and the shipped-file leak sweep, so a template could then print that literal with every negative guard green.

Anchor it on the parser's grammar instead — independent of `_RULES`, so not self-certifying, and it carries `SKILLS` automatically:

```python
def composer_headings() -> set[str]:
    """The section headings a template may legitimately print.

    DERIVED from cv/parse.py's own grammar -- the sections the parser models -- never
    from `_RULES`. Reading `_RULES` statically misses the conditional SKILLS block (which
    would reject the heading permanently); RENDERING `_RULES` admits `{name_heading}`,
    i.e. `name.upper()`, into a set that is the ALLOWLIST for the leak sweeps. Measured:
    the rendered set gains the substituted name.

    Callers must assert non-empty: `set() <= anything` is True.
    """
    from sluice.cv.parse import _TRAILING_SECTIONS
    return {"PROFILE", "WORK EXPERIENCE"} | set(_TRAILING_SECTIONS)
```

- [ ] **Step 5: Fix `_render`'s precedence in the neutrality sweep**

`tests/test_prompt_neutrality.py`'s `_render` `continue`s on any parameter carrying a default **before** it reads `overrides` — measured, so a `_SYNTHETIC_ARGS` entry for a defaulted parameter is inert and a `_FORBIDDEN` term inside the conditional block sweeps clean. Consult the overrides first:

```python
    for name, p in inspect.signature(func).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if name in overrides:
            value = overrides[name]          # an explicit override beats a default
        elif p.default is not inspect.Parameter.empty:
            continue
        else:
            value = _SYNTHETIC.format(name)
```

Add `{"skills_requested": True}` to `_SYNTHETIC_ARGS` for `sluice.cv.compose.build_prompt`.

- [ ] **Step 6: Witness the sweep — it must go RED**

Temporarily move a `_FORBIDDEN` term into `_SKILLS_BLOCK`. Run `./.venv/bin/python -m pytest tests/test_prompt_neutrality.py -v`; expect FAIL naming the term. **If it passes, the sweep is still inert and step 5 is wrong.** Restore.

- [ ] **Step 7: Thread it through the engine**

In `cv/engine.py`, pass `skills_requested=any(_skill_items(e) for e in bundle["entries"])` — the **same derived value** SC5 requires both conditions to read.

- [ ] **Step 8: Run the full suite and commit**

```bash
./.venv/bin/python -m pytest
git add sluice/cv/compose.py sluice/cv/engine.py tests/template_content.py tests/test_prompt_neutrality.py tests/test_cv_compose.py
git commit -m "feat(cv): request a gated SKILLS section from the composer (#168)"
```

---

## Task 9: correct the documents block 1 falsifies

**Files:**
- Modify: `.rulesync/rules/CLAUDE.md`, `sluice/cv/parse.py` (comment), `docs/ARCHITECTURE.md`, `sluice/cv/voice.py` (comment)

Done **in block 1**, not later: an earlier plan revision repaired these a block afterwards, leaving an interval in which a shipped document asserted something the code had already made false, with nothing red.

- [ ] **Step 1: Correct each falsified claim**

1. `.rulesync/rules/CLAUDE.md` — the `_TRAILING_MARKERS` licence ("the gate never citation-checks them AT ALL"). `SKILLS` is now the first trailing section the hard gate checks.
2. `sluice/cv/parse.py`'s own comment — "no check here for a wider marker to slip past".
3. The repeated-trailing-header exception, spelled as CERTIFICATES/EDUCATION only.
4. The **2-tuple `section_spans` contract**, stated in `.rulesync/rules/CLAUDE.md`, `docs/ARCHITECTURE.md` (two places) and `sluice/cv/voice.py`.

- [ ] **Step 2: Regenerate and verify**

```bash
npm ci --ignore-scripts && npm run rulesync
./.venv/bin/python -m pytest
git add -A
git commit -m "docs(cv): correct the claims the SKILLS gate falsifies (#168)"
```

---

## Task 10: doctor reconciliation

**Files:** Modify `sluice/core/doctor.py`, `sluice/evidence/commands.py`. Test: `tests/test_doctor.py`.

**Two** `NOTICE` rows: inventory skills evidenced by no entry; entry `Skills:` absent from the inventory.

A third row was proposed for the `p99` subtractive residual — a `Skills:` token whose digits also appear in the same entry's `Metrics:` — and **is dropped as inverted**: every digit in `Metrics:` is already in `nums[eid]` via `_entry_block`, so that condition selects exactly the cases where span removal is a no-op. The residual is recorded in the spec's section 14 instead.

**Each row reports a count plus the resolving command** — `job-sluice experience list` or `job-sluice skills list` — and **no user-authored string at all.** Two locator designs were rejected in review: an ordinal (`cmd_evidence_list` prints no index, and `--pending` selects a different set) and the entry title (`evidence_slug()` of the user's own `--name`, which commonly encodes an employer; the cited precedent's docstring separately excludes it as "a name the user chose"). No doctor row carries user-authored text today.

Surface an entry's `Skills:` in `experience list` so the counts are actionable there rather than in the report.

- [ ] Write the failing tests; implement; witness that a row naming a skill string fails a test asserting the report contains no `Skills:` value; commit as `feat(doctor): reconcile entry skills against the inventory (#168)`.

---

## Task 11: new-note default and the neutrality collector

**Files:** Modify `sluice/core/vault.py` (`_render_evidence_note`), `tests/test_fixture_name_neutrality.py`.

`_render_evidence_note`'s blank `Skills:` is **not** work and does not land here: it writes
`{k: str(fields.get(k, "")) for k in spec.fields}`, so the blank appears on every new experience
note the moment Task 1 adds the field. It is live from Task 1 — which is why Task 5's abstain
guards must be fixtured against a **blank value**, never a missing key.

- [ ] Extend the fixture-neutrality collector to the `Skills` field. It is currently keyed on the literal string `Company`, so this position is reached by **no sweep**, and prose guidance already failed once for `Company` at #135. Three shape requirements, the last measured off the existing `Company:` collector's own comment:

  - both the comma and block-list spellings;
  - a comma-joined value must collect as **separate** identities, not one;
  - a **literal two-character `\n` escape** must count as an item separator alongside a real newline. This repo's evidence fixtures pack a whole frontmatter block into ONE Python string literal joined that way, so a collector reading only real newlines sweeps clean over exactly the fixtures that exist. Needs a shape-coverage test, not just a value test.
- [ ] Add each new fixture skill value (`ExampleQL`, `WidgetFramework`, `Widget3`) to `_REVIEWED_FIXTURE_IDENTITIES` in the same change. Nothing local can establish whether a technology-shaped name belongs to a real product — the ratchet exists to force that one-time human call at the moment the value is added.
- [ ] Assert the collector's scope is non-empty before asserting on its contents — for a negative guard, finding nothing is the success case.
- [ ] Commit as `test(cv): sweep the Skills fixture position for leaked identities (#168)`.

---

## Task 12: remaining documentation

**Files:** `docs/ARCHITECTURE.md`, `docs/USAGE.md`, `sluice.yaml.example` if any text changes.

- [ ] `BundleSources`' four-member story and the framing/citable split; the two containment rows; `Skills:` in the Experience Library format. Regenerate rulesync. Commit as `docs: describe the skills containment gate (#168)`.

---

## Task 13: render it in the shipped template — LAST

**Files:** `sluice/templates/cv_plain.html.j2`, `tests/test_renderer_template.py`.

#168's item 5, and it must land last. Verified by mutation on `origin/main`: adding a `SKILLS` heading to the template today fails `test_every_shipped_template_contributes_no_content` with `contributes content of its own: ['SKILLS']`. After Task 8's `composer_headings()` re-anchor it passes, because `_TRAILING_SECTIONS` carries `SKILLS`.

- [ ] Add a `{% if document.skills %}`-guarded section, matching the existing `CERTIFICATES` block's shape. Commit as `feat(cv): render the SKILLS section in the shipped template (#168)`.

---

## Self-Review

**Spec coverage.** Walked each spec section: 1.1→T4/T5; 1.2 (out of scope, no task); 2/SC3→T1; 2.1→T2 step 4 (the three frozen tests); 3/SC4→T4; 3.2/SC5→T4, T5, T8; 3.3→T2; 3.4/SC6→T6; 4.1/SC7→T3, T7; 4.2/SC8→the ordering note; 4.3→T3; 4.4→T7; 5/SC9→T5; 6→T8; 7→T10; 8→T1 (auto-generated), T11; 9 (measurements, no task); 11→witness steps in T2, T4, T5, T6; 11.1's EIGHT collisions→T2 (frozen ×3), T3 (`_validate_line_sets`), T7 (`len(gate_markers)`, trailing-content, implication-sweep sibling), T8 (`_render`, `composer_headings` + its three consumers); 11.2→T11; 12→T2 step 4; 13→T9, T12, T13; 14 (risks, no task). **No gaps.**

**Placeholder scan.** Tasks 10–12 are described at a coarser grain than 1–9 deliberately: they are independent of each other and of the gate work, and each has a named file, a named behaviour and a named commit message. If executing them as written proves under-specified, expand that task before starting it rather than improvising.

**Type consistency.** `_entry_skills_line`, `_skill_items`, `SKILL_TOKEN_RE`, `_WORD_RE` (bundle.py); `_WORK_BULLET_MARKERS`, `_SKILLS_MARKERS`, `_tokens`, `_subseq`, `_names_skill`, `_in_source`, `_strip_skill_spans` (validate.py); `BundleSources(nums, baseline, skills, source_tokens)`; `section_spans → (profile, work, skills)`; `build_prompt(..., skills_requested=False)`; `CvDocument.skills`. Each is defined in exactly one task and consumed under the same name later. `_WORD_RE` is defined in `bundle.py` (Task 2) and re-used in `validate.py` (Task 5) — **import it, do not redefine it**, or the two tokenisers can drift and the vocabulary the gate builds stops matching the one it searches with.
