# CV profile — the prompt sources from the JD and no gate covers the profile

- **Date**: 2026-07-22
- **Status**: REVISED after `/review-plan` rounds 1 (0C/1H/4M/7L) and 2 (0C/0H/5M/6L), all folded. R1's
  High (inv-001) closed the profile-strip fail-open; R2 corroborated (4 reviewers) that the profile
  `_CITE_RE` must be byte-identical to render's with a drift test (Test 6c), carved row 7 out of the
  isolation rule as integration-witnessed, gave Test 3 its own witness (row 9), and fixed a `--`
  self-contradiction in the hardened prompt. No Critical/High across either round. Ready for
  `writing-plans`.
- **Origin**: issue #30. Third item in the CV-fabrication-gate cluster (#28, #31 shipped as PR #51,
  #30). #31's parser cleanup is a prerequisite for the number-pool half of this change.
- **Expert consult**: an LLM-reliability/guardrail review was run on the three candidate approaches
  before this design. Its recommendation — keep the hard gate pure, fix the prompt (the real root
  cause), floor the verifiable class, route the qualitative residual to human sign-off — is the design
  below. The two rejected approaches are recorded in §3 so the choice is legible, not assumed.
- **Scope decisions (user-confirmed)**:
  - **Full scope**: prompt fix (`compose.py`) + deterministic numeric floor on the profile
    (`validate.py`) + a symmetric structural guard for a missing `PROFILE` header (`engine.py`).
  - The **hard gate stays pure and deterministic**. Promoting the LLM audit to a blocking gate
    (approach #2) and requiring the profile to carry `[id]` citations (approach #3) were both
    considered and **rejected** — see §3.
  - The **qualitative residual** (a numberless invented aspiration) is irreducible to a pure gate and
    is deliberately **out of scope**, filed as **#60** (make an unsupported profile audit flag block
    human sign-off, not rendering). Design-laden; brainstorm before building.

## Problem — two independent halves, and only one of them is the gate

The composed CV has a `PROFILE` section (2–3 sentences, "I" voice) that **precedes** the
`WORK EXPERIENCE` header. Two things conspire so a fabricated profile passes with `violations=0`:

### Half 1 (the root cause of what was observed) — the prompt points the profile at the JD

`_RULES` in `sluice/cv/compose.py` opens with a general prohibition —
*"Compose ONLY from the SOURCE BUNDLE below … Never infer from general knowledge. NO FABRICATION."* —
and then issues a **competing, specific** instruction (compose.py:12):

```
- Profile: "I" voice, 2 to 3 sentences, lead with what {company} values.
```

`{company}` is interpolated from the lead, and the JD sits in the same prompt under
`=== THE ROLE (JD) ===`, while the bundle is labelled `=== SOURCE BUNDLE (the ONLY permitted
source) ===`. A company's *values* live only in the JD, which is explicitly not-a-permitted-source, so
the model's cheapest way to obey *"lead with what {company} values"* is to **invent a JD-aligned
motivation**. Functionally this line is a fabrication instruction, and it is profile-specific — which
is exactly why two independent backends fabricated **only in the profile** on runs that both reported
`violations=0`. The advisory audit caught them, classifying two claims of the shape
*"Motivated by <aspiration>"* as `unsupported`. **A clean prompt would not have produced this
incident; the dirty prompt did.**

### Half 2 (a separate, currently-open hole) — the deterministic gate cannot see the profile

In `sluice/cv/validate.py` the citation / bad-citation / invented-metric checks sit behind
`if in_work and line.lstrip().startswith(("-", "•", "*"))` (validate.py:82). `in_work` is set only by
a `WORK EXPERIENCE` header and reset by `CERTIFICATES`/`EDUCATION`. The `PROFILE` precedes the header,
so those checks **never run on it**. The `NOT REVERSE-CHRONOLOGICAL` check is also post-header
(`cv_text.split("WORK EXPERIENCE")[-1]`). The invented-metric capability exists; it is scoped away
from the one section whose prose makes claims about the candidate.

Note this is a **distinct** failure from Half 1: even with a clean prompt, a fabricated **number** in
the profile (*"I scaled systems to 2M users"*) sails straight through the gate today. That number
class is HARD-verifiable and is what the deterministic floor below closes. It is arguably more
damaging than a vague aspiration, and it is not the class that was observed — which is the point of
§5.

## The governing rule and the philosophy that fixes the scope

**Keep the hard gate pure and deterministic.** That property is not decoration: it is what makes every
test in `tests/test_cv_validate.py` an offline, reproducible guarantee, and it is why the retry-once
loop in `engine.run_one` can converge on a fixed target. A blocking gate that calls a model would
surrender exactly that — and today `run_audit` (engine.py:95) reuses the **composing** backend, so an
audit-as-hard-gate would have a model grade its own homework. The judge earns its keep only where
there is no human in the loop; here the candidate reviews the CV before sending.

**Floor the class, not the sample.** Designing the gate solely around the one qualitative incident
observed is this repo's own *"a table whose cases you chose certifies nothing"* fallacy applied to
threat modelling. The deterministic gate closes the number/decoy class comprehensively; the
qualitative class it cannot decide is contained by the human sign-off (#60), and that boundary is
stated, not hidden.

## §3 — the two rejected approaches, recorded so the choice is legible

**Rejected: promote the LLM audit to a BLOCKING gate for the profile (an `unsupported` verdict → retry
→ skip).** It catches the exact qualitative fabrication observed, but the cost is the gate's defining
property: (a) the offline test property breaks — the gate can then only be witnessed against a
scripted fake verdict, i.e. a mock, not the guarantee; (b) a stochastic verdict in a *blocking* gate
is non-reproducible (same lead renders one day, skips the next) and can chase a moving target across
the retry; (c) a *blocking* false positive silently drops a legitimate lead to `skipped-gate`,
trading a silent-fabrication risk for a silent-drop risk against the repo's never-lose-a-lead ethos;
(d) the self-audit wiring above. This is where the pure/deterministic invariant is load-bearing.

**Rejected: require the profile to carry `[id]` citations, then run the full WORK check above the
header.** It looks rigorous but inverts into a fabrication-laundering mechanism. The honest citation
for *"motivated by resilient systems"* **does not exist** (it is not a bundle fact), so the model's
cheapest way to satisfy the format is to append a **plausible real** `[id]` — and a numberless
aspiration + a valid `[id]` **passes the deterministic check**, which only verifies the id exists and
that *numbers* trace to it. You would train the prompt to attach fake citations to fabricated claims
and have the gate rubber-stamp them — strictly worse than doing nothing. The citations are stripped by
`render._CITE_RE` before the human ever sees them, so the "discipline" exists purely to satisfy the
gate, and forcing citations onto an "I" voice summary degrades the prose. This approach is argued
against, not merely passed over.

## §4 — Design

### 4.1 Prompt hardening — `sluice/cv/compose.py` (the root cause of the observed incident)

**Broadened on user direction (2026-07-22).** An onboarding wizard will later hand-build the bundle for
new users, so the prompt must be as solid as a prompt can be that it **only rewrites the candidate's
verified facts for the specific role and never invents anything** — hardened hardest for the high-risk
case the wizard creates: a *thin* bundle applied to a job whose ad asks for more than the bundle holds.
So this is no longer a one-line profile edit; it reframes the whole `_RULES` preamble as a **tailoring**
task ("tailor, don't author") and adds an explicit "omit, do not invent, when the role wants what you
do not have" rule.

Proposed `_RULES` (the task frame and the JD-gap rule are new; the citation/number/em-dash/slop rules
are unchanged; the profile line is the hardened form). The prompt string must contain **no U+2014 em
dash** (an existing test asserts it) and — since it forbids double hyphens — **no `--` in its own
prose** either; the only `--` is the `(--)` inside the rule that names the banned token (review
rev-r2-001):

```
CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given a candidate's verified facts in the SOURCE
  BUNDLE. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You are not
  authoring a new CV, and you add nothing that is not already in the bundle.
- The SOURCE BUNDLE is the ONLY permitted source. If a detail is not in the bundle, leave it out.
  Never infer from general knowledge, from the job ad, or from what the role "should" have. NO
  FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills,
  certifications, achievements, or motivations that are not in the bundle.
- If the role asks for experience, a skill, or a quality the bundle does not contain, DO NOT add it.
  Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Every number and named fact
  survives unchanged from the bundle entry it came from.
- Every WORK EXPERIENCE bullet MUST end with a citation [id] naming the bundle entry it came from
  (several allowed: [id] [id]). No uncited bullets. Any number in a bullet must appear in a cited entry.
- {employer_line}
- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens
  (--). En-dash date ranges (12/2025-present) are fine.
- No AI slop (no spearheaded, fostered, drove, leveraged, seamless, passionate about, proven track
  record). Short sentences. Real metrics only.
- Profile: "I" voice, 2 to 3 sentences. Compose it ONLY from facts in the SOURCE BUNDLE, ordered and
  emphasised for {role}. Introduce nothing not in the bundle. No motivations, aspirations, or
  company-specific claims. Any number in the profile must appear in the SOURCE BUNDLE.
```

(The prompt now uses no `--` in its own prose — it forbids double hyphens, so it must not model them;
the `(--)` in the em-dash rule names the banned token and stays. Review rev-r2-001.)

**The block above is the `CV RULES` PREAMBLE only** (review rev-001). `_RULES` also contains the
retained `Output the CV in EXACTLY this format:` tail with the `{contact}` and `{name_heading}`
placeholders and the format skeleton `cv_render_v2` parses (compose.py:14-32) — that tail is
**UNCHANGED and must not be dropped** when the preamble is replaced. Mechanics: after the edit `_RULES`
no longer interpolates `{company}` (its only use was the deleted profile pull) and now interpolates
`{role}`, so `build_prompt`'s call becomes `_RULES.format(contact=contact, name_heading=name.upper(),
employer_line=_employer_line(employers), role=role)` — it **keeps** `contact=`/`name_heading=`, gains
`role=role` (already a param), and drops the now-dead `company=`. The profile "Any number …" clause
aligns the prompt with the gate in §4.2.

**The honest architecture — a prompt reduces, the gate blocks.** No prompt can *guarantee* "never
invent"; that word is earned by the layered system, not by wording. Strongest first:

1. **The deterministic gate (§4.2, hard block — the only guarantee):** a fabricated **number** (in a
   bullet against its cited entry, and now in the profile against the bundle) and a configured
   **decoy** string are blocked, offline and reproducibly; an uncited or badly-cited bullet is blocked.
2. **The hardened prompt (this section, probabilistic — belt, not backstop):** makes qualitative
   invention far less likely and is pinned against regression by *wording* tests (§6) — but a wording
   test pins the instruction, not the behaviour.
3. **The advisory audit + #60 (human sign-off):** the class the gate cannot decide — a numberless
   invented *quality* ("built Kubernetes platforms" citing an entry that never mentions Kubernetes; a
   fabricated motivation). The deterministic gate deliberately does **not** verify that a bullet's
   *words* trace to its cited entry: rephrasing legitimately changes words, so a word-containment check
   would false-positive on exactly the tailoring we want. Semantic support is the audit's domain, not
   the pure gate's.

This is why §4.1 alone is insufficient and §4.2 is required. The onboarding-wizard scenario *raises*
#60's priority (see §10): the thin-bundle new user is the case most likely to produce, and least
likely to notice, an invented qualitative match, and only human sign-off closes it.

### 4.2 A deterministic numeric floor on the profile — `sluice/cv/validate.py`

**(a) Surface a baseline number pool from the bundle parse.** `_bundle_ids_and_nums` today drops
baseline numbers (the baseline block precedes the first `[id]`, so those lines hit neither the `[id]`
branch nor `elif cur`). Extend it to accumulate the numbers on lines **before the first `[id]`** into a
`baseline` set and return it alongside `ids, nums`:

```python
def _bundle_ids_and_nums(bundle_text):
    ids, nums, baseline = {}, {}, set()
    cur = None
    seen_id = False
    for line in bundle_text.splitlines():
        if _SECTION_RE.match(line):
            cur = None
            continue
        m = _ID_RE.match(line)
        if m:
            seen_id = True
            cur = m.group(1)
            ids[cur] = line
            after = line[m.end():]
            nums[cur] = set(re.findall(r"\d+", after))
        elif cur:
            nums[cur] |= set(re.findall(r"\d+", line))
        elif not seen_id:
            # Numbers before the first [id] are the BASELINE block. They are a
            # permitted SOURCE for the profile (an aggregate summary) but NOT for a
            # WORK bullet, which must trace to its specific cited entry. Negatives are
            # NOT captured here: they land after the last [id] (seen_id True, cur
            # cleared by their === header === below), so they fall into neither pool
            # and stay excluded -- the same exclusion #31 established for bullets. (#30)
            baseline |= set(re.findall(r"\d+", line))
    return ids, nums, baseline
```

**(b) Sweep the profile region for un-bundle-able numbers.** `validate.py` gains a module constant
`_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")` — **byte-identical to `render._CITE_RE`
(render.py:10)**, including the `\s*` and the ASCII `[0-9]` (**not** `\d`, which also matches non-ASCII
digits that render's `[0-9]` would leave visible — a token validate stripped but render delivered is
the same fail-open class as inv-001; review inv-r2-001). The profile strip below uses it so the numeric
check sees exactly the text render delivers. A comment cannot enforce that equality, so **Test 6c pins
it**: `validate._CITE_RE` and `render._CITE_RE` must strip identically, so editing either regex reddens
a test instead of silently reopening the fail-open (review arc-002 / inv-r2-001, four reviewers).
`_CITE_RE` is **deliberately distinct** from `_ID_RE = \[([A-Z]{2}\d+)\]`, and a code comment must say
why so a future reader does not reconcile them: `_ID_RE` parses bundle-**generated** codes (always
uppercase via `_prefix`), while `_CITE_RE` mirrors render's **lenient** strip of whatever the model
emitted (`[A-Za-z]`) (review arc-003). In `validate()`, the permitted set for profile prose is broader
than for a bullet — a summary may draw on baseline aggregates, so it is `{all entry numbers} ∪
{baseline} − {negatives}` (negatives already excluded by the parse):

```python
    ids, nums, baseline = _bundle_ids_and_nums(bundle_text)
    ...
    profile_permitted = baseline.union(*nums.values())   # copy of baseline when nums is empty
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
            # Prose, NOT a bullet: no citation is required or expected (requiring [id]
            # on prose invites a fake-citation launder -- see the rejected approach #3).
            # Strip citations with render's EXACT id shape (_CITE_RE above, == render.py:10)
            # so the check sees precisely the text the reader sees. This is NARROWER than
            # the WORK-bullet strip ON PURPOSE: render removes only id-shaped [XX9] codes,
            # so a NON-id bracket like `[500]` SURVIVES into the PDF. The broad WORK strip
            # `\[[^\]]+\]` would delete `[500]` before the digit check, passing a fabricated
            # number that then SHIPS -- and the profile has no BAD-CITATION backstop behind
            # the strip the way a WORK bullet does. So match render, not the bullet path.
            # Pinned by the [ES1]/[500] test pair (Tests 6a/6b). (#30, review inv-001)
            prose = _CITE_RE.sub("", line)
            for n in re.findall(r"\d+", prose):
                if n not in profile_permitted:
                    v.append(f"INVENTED PROFILE METRIC {n} not in bundle: {prose.strip()[:50]}")
        if in_work and line.lstrip().startswith(("-", "•", "*")):
            ...   # existing WORK bullet checks, UNCHANGED
```

`validate()`'s signature, purity and determinism are untouched (a new return element on a private
helper is internal). No config knob; no `sluice.yaml.example` change.

### 4.3 A symmetric structural guard — `sluice/cv/engine.py`

The profile sweep is keyed on the exact `PROFILE` header, so a composed CV that **omits** the header
has an empty profile region and is swept — a silent fail-open, the mirror of the `WORK EXPERIENCE`
drift already guarded at engine.py:78. Add the symmetric guard **beside** it (fail closed):

```python
if not any(line.strip().upper() == "PROFILE" for line in cv_text.splitlines()):
    violations = ["STRUCTURAL: composed CV lacks the exact 'PROFILE' header, so the "
                  "profile fabrication check did not run"] + violations
```

It lives in `engine.py`, not `validate.py`, for the same reason the `WORK EXPERIENCE` guard does:
`validate()` returns `[]` on a text whose section it cannot find, and the engine is the layer that
turns that false-clean into a HARD-fail + retry + skip.

### 4.4 Behaviour surface

| CV region | Before | After |
| --- | --- | --- |
| A `WORK EXPERIENCE` bullet's numbers | checked against its **cited entry** | **unchanged** |
| A `PROFILE` number present in an entry or the baseline | not checked (profile unscanned) | permitted |
| A `PROFILE` number present **nowhere** in the bundle | not checked → passes | **`INVENTED PROFILE METRIC` → blocks** |
| A `PROFILE` number present **only in the negatives block** | not checked → passes | **flagged** (negatives excluded from the pool) |
| A `PROFILE` **non-id bracketed** number `[500]` (render leaves it in the PDF) | not checked → passes | **flagged** — the strip matches render, so the reader-visible number is checked (review inv-001) |
| A configured decoy string in the `PROFILE` | already flagged (`FABRICATED`, global check) | **unchanged** — characterised by a test, not re-implemented |
| A composed CV with **no `PROFILE` header** | renders (profile unscanned) | **`STRUCTURAL` → blocks** |
| A numberless invented aspiration in the `PROFILE` | audit-advisory only | **still audit-advisory** — out of scope, see #60 |

## §5 — Divergence from the launch-prompt assumption (bullets stay strict)

The launch prompt and #31's `test_baseline_numbers_are_not_permitted_in_a_bullet` (validate.py test
:191, "Permitting them is #30's design question") anticipated that #30 would **flip** that test —
permit baseline numbers for **bullets**. **This design does not.** Baseline numbers become permitted
**only for the profile**; a WORK bullet still must trace its numbers to its **specific cited entry**,
because a bullet makes a specific claim about one role while a profile makes an **aggregate** claim
across the whole career.

Consequence: `test_baseline_numbers_are_not_permitted_in_a_bullet` **stays green and is not touched**,
and #31's mutation row 8 ("bucket baseline numbers under the first entry") remains a valid witness for
it. #30 is thereby **decoupled** from #31's test — cleaner and lower-risk than the launch-prompt
assumption. This divergence is called out explicitly because it contradicts a prior expectation, and
an unstated contradiction is the failure mode this repo most consistently engineers out.

## §6 — Tests

**`tests/test_cv_validate.py`** (pure `validate`; bundles built through `build_bundle` + `render_bundle`
with `jd_keywords=[]` and an explicit `prefix_map`, ids verified by printing the rendered bundle).
**Every profile test asserts on the exact `INVENTED PROFILE METRIC` phrase, never `any("INVENTED" in
x)`** — that substring also matches the WORK path's `INVENTED METRIC`, so it would go green on the wrong
violation (review tst-001):

1. **`test_invented_profile_metric_flagged`** — a profile line with a number present **nowhere** in
   the bundle → `INVENTED PROFILE METRIC`. The core new coverage. Fails today (profile unscanned).
2. **`test_profile_number_from_baseline_is_permitted`** — a profile number appearing **only** in the
   baseline block → passes. Proves the baseline pool, and is the deliberate divergence from the bullet
   behaviour in §5 (the same number in a *bullet* is still flagged — assert both in one test to make
   the asymmetry explicit and non-accidental).
3. **`test_profile_number_from_an_entry_is_permitted`** — a profile number drawn from an entry's
   `metrics`/`body` → passes. Guards against a fix that narrows the pool to nothing; its dedicated
   witness is §7 **row 9** (`profile_permitted = baseline`), not row 7 (review tst-r2-002).
4. **`test_profile_number_from_negatives_is_flagged`** — a number appearing **only** in the negatives
   block, used in the profile → flagged. Mirrors #31's negatives test for the profile pool.
5. **`test_profile_decoy_flagged`** — a configured decoy string in the profile → `FABRICATED`.
   **Characterisation** of the already-global decoy check (validate.py:62), so a future change that
   accidentally scopes decoys to a region is visible. Not new behaviour.
6a. **`test_a_profile_id_citation_code_is_not_an_invented_metric`** — a profile line errantly carrying
   an **id-shaped** `[ES1]` whose code-digit is **not** in the pool → still clean. render strips `[ES1]`,
   so the reader never sees the `1`; the gate must not count it. Prevents a false-positive silent drop.
6b. **`test_a_profile_non_id_bracketed_number_is_flagged`** — a profile line `... [500] ...` where 500
   is **not** in the bundle → **flagged `INVENTED PROFILE METRIC`**. render's `_CITE_RE` leaves `[500]`
   in the PDF (it is not id-shaped), so the reader sees it and it must be checked. **This is the test
   that distinguishes the narrow (render-matching) strip from the broad WORK strip** — 6a alone passes
   under both, which is exactly the review inv-001 fail-open. Killing mutation: widen the strip to
   `\[[^\]]+\]` (§7 row 8).
6c. **`test_profile_strip_matches_render_citation_shape`** — asserts `validate._CITE_RE` and
   `render._CITE_RE` strip **identically** across a battery (`[ES1]`, `[500]`, `[es1]`, a non-ASCII-digit
   `[ES६]`, ` [AB12]`). The only *executable* binding of the "profile strip == render strip" contract,
   so a drift in either regex reddens here instead of silently reopening the inv-001 fail-open (review
   arc-002 / inv-r2-001, four reviewers). Killing mutation: §7 row 10.

**`tests/test_cv_engine.py`**:

7. **`test_missing_profile_header_is_structural`** — a composed CV with the `PROFILE` header removed →
   `status == "skipped-gate"` with a `STRUCTURAL` violation, and (as `test_gate_failure…` does) the
   renderer is asserted to have rendered nothing. Mirrors `test_drifted_work_header_fails_closed`.

**`tests/test_cv_compose.py`**:

8. **`test_prompt_is_a_tailoring_task_and_forbids_invention`** — `build_prompt(...)` no longer contains
   *"lead with what"*; it *does* contain the tailoring frame (*"TAILOR, NOT TO WRITE"*), the JD-gap
   omit rule (*"invented match is a failure"*), and the hardened profile framing. **Wording** tests,
   stated as such: they pin that the anti-fabrication instructions are present, not that fabrication
   cannot occur. Complements the existing `test_prompt_excludes_material_not_given` and keeps the
   existing `test_prompt_contains_bundle_jd_and_forbids_em_dashes` green (no U+2014 in the new text).
9. **`test_cv_prompt_expresses_no_role_or_culture_preference`** — the CV-side mirror of the triage
   `test_shipped_prompt_expresses_no_role_or_culture_preference`, which (verified `tests/test_prompt.py:25`)
   covers only the **triage** system prompt, not this CV `_RULES`. Asserts `build_prompt(...)` with
   neutral inputs names no employer, role-type preference, culture word, location, or salary, and
   expresses no opinion about which jobs are good. Closes the neutrality-guard gap neu-001 identified so
   the hardened prompt stays neutral against future edits.

## §7 — Mutation witnesses (required, per CLAUDE.md)

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` **once first**. Mutate
by **moving or deleting**, never adding. Each mutant is run **in isolation** by node id (the named new
test RED, and — because a mutation killed only by a pre-existing test witnesses nothing about a new
test — no pre-existing test in the same file reddens; **row 7 is the exception, see below**). Restore
byte-identical (sha256-checked).

| # | Mutation | Kills |
| --- | --- | --- |
| 1 | Delete the `if in_profile:` number-check block | Test 1 |
| 2 | Drop the `baseline` term: `profile_permitted = set().union(*nums.values())` | Test 2 |
| 3 | Delete the `not seen_id` guard so negatives also feed `baseline` | Test 4 |
| 4 | Delete the bracket-strip: `prose = line` | Test 6a |
| 5 | Delete the `PROFILE` structural guard in `engine.py` | Test 7 |
| 6 | Delete each pinned `_RULES` clause in turn (tailoring frame / JD-gap omit rule / the `{company}`-values pull). NB the pull sub-mutation must revert §4.1 **wholesale** — re-add BOTH the line AND `company=` to `.format`, else `.format` raises `KeyError` and Test 8 *errors* instead of failing its assertion (review tst-005) | Test 8 (matching assertion) |
| 7 | Delete `in_profile = False` from the `WORK EXPERIENCE` arm (region leaks into WORK) | clean-CV tests, integration* |
| 8 | Widen the profile strip to `\[[^\]]+\]` (the broad WORK form, which deletes non-id brackets like `[500]`) | Test 6b |
| 9 | Drop the entry term: `profile_permitted = baseline` | Test 3 |
| 10 | Alter `validate._CITE_RE` so it no longer strips as `render._CITE_RE` does | Test 6c |

\*Row 7 is **integration-witnessed, not isolatable** (review tst-r2-001). The profile sweep has no
bullet-marker gate — while `in_profile` it scans *every* line — so a leaked `in_profile` sweeps WORK
**date lines**, and a year like `2023` in `02/2023–present | …` is not in `profile_permitted`. That
reddens **every clean-CV test with dated WORK lines, pre-existing ones included** (`test_clean_passes`
et al. on the `_cv(FULL)` fixture), so Row 7 is explicitly **exempt from the "no pre-existing test
reddens" clause** — it is a cross-region leak that *should* break many tests. It is **not** equivalent,
and the earlier bullet-only reasoning misidentified why. Row 7 is **not** the witness for Test 3's
"don't narrow the pool to nothing" purpose — that is **row 9** (`profile_permitted = baseline`); row 7
only reddens Test 3 incidentally via the date-line leak (review tst-r2-002). Any dedicated leak test
must assert the exact `INVENTED PROFILE METRIC` phrase — a leaked sweep over a fabricated WORK bullet
emits *both* `INVENTED METRIC` and `INVENTED PROFILE METRIC`, so `any("INVENTED" in x)` is inert.

## §8 — Fixture discipline (a fixture change is exactly when a guard goes inert)

`CLEAN_CV` in `tests/test_cv_engine.py:56` has **no `PROFILE` header**. The §4.3 structural guard makes
every engine test that drives `CLEAN_CV` through `run_one`/`run_batch` to a rendered/dry-run/served
outcome hit `skipped-gate`. So `CLEAN_CV` gains a **number-free** `PROFILE` section (e.g. `"PROFILE",
"I build reliable systems.", ""` before `WORK EXPERIENCE`) — number-free so it is clean under both §4.2
and §4.3.

**Enumerate the breakers from the failing run, not a hand-list.** Three reviewers (inv-002, rev-002,
tst-002) found the first draft's list wrong in both directions — this repo's own "set-claim without
checking each member" anti-pattern, in the very section whose thesis is that a fixture change is when a
guard goes inert. The two that do **not** break: `test_batch_skips_leads_that_already_have_a_cv`
(returns `skipped-has-cv` before `run_one` composes) and `test_clean_cv_is_actually_clean` (calls
`validate()` directly, which the *engine*-level guard never touches). Verified breakers include
`test_happy_path_renders_and_records`, `test_no_serve_renders_but_does_not_mark_lead`,
`test_advisory_audit_failure_does_not_block_render`, `test_batch_survives_a_single_lead_exception`, and
`test_dry_run_reports_but_writes_nothing`. The **full-suite re-run after the edit** — not any list — is
what actually protects this.

The validate helpers `_cv()`/`_work_cv()` already carry a number-free `PROFILE` (`"I lead."` /
`"I build things."`), so existing validate tests stay green unchanged — verified by reading them, not
assumed. **All §7 witnesses and the existing suite are re-run *after* the `CLEAN_CV` edit**, because a
fixture change is precisely how a surviving assertion goes inert (#31's recorded lesson).

## §9 — Neutrality

No personal data enters `sluice/` or `tests/`. New fixtures use the synthetic `Example …` company
family and `conftest`-style placeholder locations already established. The `CLEAN_CV` profile line is
invented, number-free, and carries no employer/role/location preference. The prompt string change adds
no personal data (it *removes* a `{company}`-values instruction). This diff touches only
`test_cv_engine.py`/`test_cv_validate.py`/`test_cv_compose.py`, all already in the `Example …` family;
the repo-wide location/company literal pass (#27 and the six/four-file residual noted in the #31 spec)
is **not** in scope here.

## §10 — Known residuals and out of scope (stated, not hidden)

- **The qualitative class is not closed** — a numberless, decoy-free invented aspiration or an invented
  *quality/skill* citing a real entry that does not support it is irreducible to a pure gate and remains
  **audit-advisory**. Contained by human sign-off, **filed as #60**. The PR must **not** claim "#30
  fixes profile fabrication": it fixes the prompt *cause* of the observed incident (unit-untestable by
  nature) and floors the *verifiable* number/decoy class with mutation-witnessed tests. This residual is
  stated in a code comment the way #31's `test_an_id_shaped_bracket…` characterisation comments state
  theirs. **The planned onboarding wizard raises #60's priority:** the thin-bundle new user is the case
  most likely to produce, and least likely to notice, an invented qualitative match, and only human
  sign-off closes it — the hardened prompt (§4.1) reduces the rate but is not a guarantee.
- **The profile can carry only bundle-present numbers.** A legitimate aggregate typical of "I" voice
  prose — "10 years", "3 companies", "since 2015" — that does not appear literally (`\d+`) in the bundle
  or baseline is flagged `INVENTED PROFILE METRIC` and, on persistent retry, lands the lead at
  `skipped-gate`. This is the **correct** fail direction (abstain + reported status, lead stays at
  `shortlist`, no silent loss — not a fabricated ship); the baseline pool is the intended pressure valve
  (aggregates belong in the authoritative baseline), and the prompt's "any number must appear in the
  bundle" rule + the one retry give the model a chance to rephrase or omit. The *mechanism* is already
  pinned by Test 1 (a bundle-absent profile number is flagged); this documents the benign case of it.
  (review inv-003 / tst-004)
- **`CERTIFICATES`/`EDUCATION` are not swept.** They also escape `in_work`, but they are structured
  factual lists with no JD-pull instruction and materially lower fabrication risk than the "I" voice
  profile the issue names as "the one that matters most." Left as a measured, lower-severity residual;
  flag to the user if broader coverage is wanted.
- **Word-form numbers** (`"twelve"`) bypass the `\d+` regex in the profile sweep exactly as they do in
  the existing bullet sweep — a property of the whole numeric gate, pre-existing, not new to #30.
- **The `nums`-overwrite residual** from #31 (an id-shaped free-text body line shadowing a real entry)
  is unchanged and out of scope; closing it needs the `validate()` signature change #31 documented.

## §11 — Config / docs impact

- No new config knob → no `sluice.yaml.example` change.
- `docs/ARCHITECTURE.md` describes the CV gate generically and needs no update — **verified by the
  architect reviewer** (it is generic), not assumed.
- **A `.rulesync/` doc tweak is proposed, human-gated (I propose, the user applies).** The canonical
  `.rulesync/rules/CLAUDE.md` CV-gate paragraph reads "every number **in a bullet** must appear in a
  **cited entry**" — bullet-scoped and citation-anchored. The new profile check is citation-**free** and
  bundle-**wide**, plus a new `PROFILE` structural guard, so the paragraph now *under-describes* the gate
  (nothing in it becomes false, but "an extension of every-number-must-cite" mischaracterises the new
  mechanism). Flagged for the human-gated `.rulesync` pass and recorded in the PR body, **not applied
  here** (`.rulesync/` is canonical). (review arc-001)

## §12 — Definition of done

- `python -m pytest` green, fast/offline (count-independent: a changing suite total does not invalidate a green run).
- `ruff check sluice tests` clean.
- All §7 mutation rows (1–10) run, each stated outcome observed, each restored byte-identical.
- Every new test's witness isolated **by node id** (review tst-003): run the specific new test (e.g.
  `pytest tests/test_cv_validate.py::test_a_profile_non_id_bracketed_number_is_flagged`) under the
  mutation and confirm it reddens, AND confirm no *pre-existing* test in the same file reddens under
  that mutation. The new tests go into existing files, so "run the file alone" cannot isolate them.
  **Exception: row 7** (the cross-region `in_profile` leak) is integration-witnessed and deliberately
  reddens pre-existing clean-CV tests; the "no pre-existing test reddens" clause does not apply to it,
  and it is reported as integration-witnessed in the PR body (review tst-r2-001).
- `CLEAN_CV` edit made; the full suite and all witnesses **re-run after** it.
- The observed-vs-verifiable honesty caveat (§10) present in both a code comment and the PR body.
- `/review-plan` run on this spec and its findings folded before implementation; `/review-pr` +
  CodeRabbit run **before** push, per the standing cadence.

## §13 — Commits (planned)

1. `fix(cv): compose the whole CV from the bundle, tailoring not authoring (#30)` — §4.1 (the whole
   `_RULES` preamble reframe, not just the profile line) + Tests 8, 9. (Retitled per review rev-003.)
2. `fix(cv): gate numbers in the CV profile against the bundle (#30)` — §4.2 + Tests 1–5, 6a, 6b, 6c.
3. `fix(cv): a missing PROFILE header fails the gate closed (#30)` — §4.3 + Test 7 + the `CLEAN_CV`
   fixture edit (§8). Kept with its guard so the fixture change and the guard that requires it land
   together.

## Out of scope

- **#60** — the qualitative-audit sign-off workflow (design-laden; brainstorm first).
- **#28** — falsified as written; unrelated.
- Permitting baseline numbers for WORK **bullets** (§5) — deliberately not done.
- `CERTIFICATES`/`EDUCATION` prose sweeps, word-form numbers, the #31 overwrite residual (§10).
- The repo-wide location/company-literal neutrality pass (#27 and the #31-spec residual).
