# Skills as gated content — design (#168)

Status: proposed, after two `/review-plan` rounds (round 1: 42 findings, 2 Critical; round 2: 38
findings, 2 Critical, nearly all of them defects in round 1's own fixes). Extends #165 (skills reach
the composer as framing) and #174 (the gate is handed a structured `BundleSources`).

Issue: **#168**. **#194 is deliberately NOT in this spec** — see section 1.2.

---

## 1. The problem

#168 item 4 asks that "every emitted skill must appear in the source bundle". #194 asks that "any
technology named in a generated CV must appear in the bundle". Both issues say this is one gate
described twice. Containment is one primitive, but it splits into two cases with opposite
false-positive profiles:

- **(a) Misattribution.** A skill the candidate holds, decorating a bullet whose role it is not
  associated with. Answerable by moving or dropping the mention.
- **(b) Pure invention.** A technology the candidate does not hold at all. Requires open-world
  detection.

`cv/parse.py`'s LOCATION refusal is the standing warning: it made the only actionable reading of its
message *invent a city*, turning a parser refusal into fabrication pressure aimed at the feature that
exists to prevent fabrication.

### 1.1 SC1: this spec ships (a) as a HARD gate

### 1.2 (b) is out of scope, on a measurement

An earlier revision put (b) in #167's STYLE tier behind a morphology-based candidate set (internal
capitals, embedded digits, trailing symbol), arguing that shape test excludes ordinary English *by
construction* and so needs no shipped lexicon. **Two reviewers measured it independently and it is
false:**

- Against the repo's 12 gate-clean CV fixtures, through real `section_spans` and after `validate.py`'s
  strips: **9 residual candidates, none a technology.** Six were sentence-final English words; three
  were metric tokens the numeric gate *already licenses* from a cited `Metrics:` — a licensed figure
  reported as an unbundled technology.
- On a purpose-built gate-clean CV: **2 false positives, `p99` and `120ms`.** (A run against only
  the three smallest fixtures returned zero, correctly called vacuous — those scan two profile lines
  and a handful of `- Shipped [ID]` bullets. Recorded so it is not later mistaken for a clean result.)

The unenumerated class is unit- and percentile-shaped tokens; the cheap repair is a unit-suffix list,
the exact shipped vocabulary #194 names as a maintenance and neutrality problem. **#194 is re-filed
carrying this measurement.** Until then (b)'s cover is unchanged: `_RULES`'s no-fabrication clause,
`_DERIVED_NEGATIVE_PROMPT`, and the #60 advisory audit.

**This spec introduces no new config knob.**

---

## 2. The model: skills support claims relationally

**SC2: a skill is licensed by association with a role, not by set membership.**

`cv/validate.py` already permits a figure in a WORK bullet only if it appears in a **cited** entry —
`union = set().union(*(nums[c] for c in cites))`. Skills get the same treatment one level up.

**SC3: the association is stored on the experience entry.** `EVIDENCE_KINDS["experience"]` gains
`Skills` in its `fields` tuple:

```
# Job Applications/Experience Library/alpha-platform-rebuild.md
Company: Example Alpha
Category: platform
Best For: event-driven work
Metrics: 40% latency reduction
Skills: ExampleQL, WidgetFramework
Verified: 2026-08-01
```

No `floor_map` entry: `Skills` has no floor analogue, as with the skills kind's own `Proficiency`,
`Evidence` and `Signal Value`.

**Value shape: a comma-separated list OR a YAML block list.** A round-1 revision claimed
`_parse_fm_spaced` cannot round-trip a multi-line value and mandated commas. **That claim was false,
measured through the real reader: it supports block lists and joins them to the identical comma
string.** Both shapes are therefore accepted, and this matters beyond tidiness — a collector written
for the single-line shape sweeps clean over a block-list value, which is the shape this repo's
evidence fixtures actually use. Section 11's neutrality collector must read both.

Storing the relation on the entry rather than on the skill note puts it where the gate already reads:
`bundle_sources` walks `bundle["entries"]`, so a per-entry skills frozenset slots in beside `nums`
with no name join and no resolution pass, and licensing is per-entry rather than per-employer.

Costs accepted: adding a skill means editing the entries that evidence it, and the Skills Inventory
note does not state its own roles. Section 7 makes that drift visible.

### 2.1 What this does to #165's D3 and D11

- `EVIDENCE_KINDS["skills"]` keeps `cited_by_gate=False`, `read_by_composer=True`. Unchanged.
- `_DERIVED_NEGATIVE_PROMPT` keeps naming exactly two claim sources. Unchanged.
- **#165's D3 survives**: a Skills Inventory line still supports nothing on its own. The Inventory
  keeps exactly its existing job — ordering and framing via `rank()` on the `Domain`-mapped
  `best_for` — and licenses nothing in this design.
- **#165's D11 does NOT survive untouched, and the earlier revisions were wrong to claim it did.**
  `_entry_skills_line` sits in `_source_section`, which `render_bundle` returns and `cv/engine.py`
  hands to `run_audit`. The auditor's source set genuinely widens, by skill names attached to entries
  it already sees. `test_the_rendered_prompt_has_not_drifted`'s docstring pins D11 as **byte-identity
  with the pre-#165 auditor text**, and this design breaks that. Three frozen tests go red, not one:
  that test, `test_the_composer_prompt_has_not_drifted`, and the allowlist test in section 12.

  The widening is *intended* and is what makes an emitted skill supportable by the auditor rather
  than `unsupported` — the architect traced `Skills:` → `_entry_skills_line` → `_source_section` →
  `render_bundle` → `run_audit` end to end and confirmed it. But it is a change to a pinned property,
  not a preservation of it, and the plan states it as such.

**Wording correction.** `cited_by_gate` is a **per-kind** flag, not per-field. The gate licenses
content the composer emitted from a citable kind; `Skills:` is a field on such a kind. The flag is
unchanged and gains no new meaning.

**`skills_unreadable`.** With the Inventory out of every gate, `cv/engine.py`'s existing swallow of an
unreadable Skills Inventory keeps its stated premise — a thing affecting only tailoring QUALITY may
never bin a lead.

---

## 3. Two rows, two different questions

**This is the correction round 2 forced, and it is the centre of the design.** Both previous
revisions collapsed these into one vocabulary and one abstain condition — first too wide (round 1
licensed the emitted section from Skills Inventory titles, contradicting the prompt), then too narrow
(round 2 licensed it from entry `Skills:` alone, which is narrower than the prompt *and* narrower
than #168's own wording). They are different questions and take different rules.

| | Row 1 — WORK bullets | Row 2 — the emitted `SKILLS` section |
|---|---|---|
| **Question** | is this attributed to the right role? | did you invent this? |
| **Licensed by** | `Skills:` on the entries **that bullet cites** | the bundle's own **source text** |
| **Granularity** | per-entry | bundle-wide |
| **Abstains when** | any cited entry declares no `Skills:` | never — see SC5 |
| **Matching** | case-sensitive, in-prose (SC9) | normalised, whole-line (SC9) |

### 3.1 SC4: row 2's vocabulary is the bundle's source text

Entry `Skills:` **∪ the baseline CV ∪ entry bodies** — everything `_source_section` contributes as a
source. Three independent reasons, all from round 2:

- `compose._RULES` and `_DERIVED_NEGATIVE_PROMPT` both license the BASELINE CV **and** verified
  entries. A gate licensing only `Skills:` refuses what the prompt in the same run requires: a model
  complying with rule 1 puts a baseline-CV technology in the requested block, every line violates,
  one retry, `skipped-gate` — on every lead, with a non-empty vocabulary, so SC5 does not cover it.
  That is verbatim the "`_RULES` permits what the containment check forbids" mutant section 11 lists
  as a guard, shipped by the design.
- The existing precedent points the same way: `profile_permitted = baseline.union(*nums.values())`
  already includes the baseline for an aggregate region.
- #168 item 4 says "must appear in the **source bundle**", not "must appear in `Skills:`".

**Row 1 is unaffected** and stays per-entry. Row 2 answers invention; row 1 answers attribution.

### 3.2 SC5: abstain per-entry on row 1; row 2 fails closed

Round 1's Critical was that an empty vocabulary hard-blocked every lead. **Round 2 found that fix
turned fail-closed into fail-open**, and it is the most serious finding of either round:
`section_spans` is pure over text, so its `SKILLS` region always clears `in_work`; making row 2
conditional on a non-empty vocabulary meant a model-emitted `SKILLS` section on an un-annotated vault
was checked by **nothing** and rendered. Measured against real `validate` today, that same section
yields `UNCITED BULLET` twice — the fix removed a working guard.

The rule that holds both ends:

- **Only the `_RULES` block is conditional.** The `SKILLS` section is *requested* only when at least
  one bundle entry declares a non-empty `Skills:`. No curated skills, no request. This is the abstain,
  and it is the same shape as `render_composer_bundle` omitting the framing header on an empty
  inventory ("an empty header would assert to the model that the candidate holds no skills, a
  negative claim it may act on").
- **Row 2 always runs on an emitted section**, whatever the vocabulary. It fails closed. With SC4's
  vocabulary this is nearly always satisfiable anyway — the baseline CV is non-empty in any bundle
  that composes at all — so an unrequested-but-emitted section is checked rather than waved through.
- **Row 1 abstains per-entry.** It fires only when **every cited entry declares a non-empty
  `Skills:`**. Round 2 measured the alternative: with one entry annotated and one not, a bullet citing
  the un-annotated entry and naming a skill present in **that entry's own body** was a hard violation
  — the gate refusing a token from the cited entry's own source line. Per-entry abstain is an
  under-fire, the direction SC9 already commits to.

Both conditions read **one derived value** so they cannot disagree.

### 3.3 Bundle plumbing

`_entry_skills_line(entry)` in `cv/bundle.py`, sibling to `_entry_block` and `_baseline_block`,
rendered by `_source_section` after each entry's block so both audiences see it.

Deliberately **not** folded into `_entry_block`: that function's contract is that every line it
returns is a numeric SOURCE harvested by `bundle_sources`. The new function carries the inverted
contract in its own docstring — every token is a **skill** source for that entry, **no digit of it is
a numeric source**.

`BundleSources` gains `skills: dict[str, frozenset[str]]`, keyed by entry id like `nums`.
`bundle_sources` builds both dicts in one pass over `bundle["entries"]`, making key equality
structural. **Round 2's caveat, carried:** that constrains only the factory, not the hand-constructed
`BundleSources` values that exist in tests, and `ids` derived from `nums` alone will not notice a
`skills` key `nums` lacks. The plan adds a construction-time check rather than relying on the one-pass
build alone.

### 3.4 SC6: digit handling in bullets and PROFILE

**Measured on `origin/main`:** a digit-bearing skill name reads to the numeric gate as a fabricated
metric — `INVENTED METRIC ['3']` in a bullet, and `INVENTED PROFILE METRIC 3` twice in prose. Latent
today; #168 makes it the main path. The only actionable answer is to delete a true skill name.

When a skill mention is licensed, its span is removed before `\d+` extraction — the technique
`validate.py` already applies to citations. **It covers PROFILE as well as bullets**, using the
bundle-wide vocabulary, consistent with `profile_permitted` already being a bundle-wide pool.

**The subtractive-licence constraint.** Span removal makes `Skills:` the first field that *subtracts*
from the hard numeric gate. A skill token must **begin with a letter**; a wholly-numeric or
digit-leading token (`92`, `92x`, `120ms`) licenses nothing and is refused loudly at bundle
construction. Round 2 flagged that the earlier "wholly-numeric" rule missed `92x`, `120ms` and `p99`.

**Accepted residual, flagged for scrutiny:** a letter-leading token that is *also* a metric shorthand
— `p99` is the real example — still licenses removal of its digits for bullets citing that entry. It
requires the user to have written `p99` into their own `Skills:`. A tighter rule (require two leading
alphabetic characters) would kill legitimate short names, so this is a deliberate trade rather than
an oversight. The plan adds a `doctor` notice for a `Skills:` token whose digits also appear as a
standalone figure in the same entry's `Metrics:`.

**Correction retained.** An *unlicensed* digit-bearing mention keeps its digits, so `validate` emits
`INVENTED METRIC` **alongside** the skill violation. Two messages, both actionable.

---

## 4. Grammar

### 4.1 SC7: placement, line shape, and marker set

**Measured on `origin/main`:**

| `SKILLS` placed | line shape | `section_spans` collects | `validate` reports |
|---|---|---|---|
| after WORK, before CERTIFICATES | bulleted `-` | its lines, as WORK bullets | `UNCITED BULLET` per line |
| after EDUCATION (last) | bulleted `-` | nothing | nothing |
| after WORK, before CERTIFICATES | comma list | nothing | nothing |

**`SKILLS` is emitted after `WORK EXPERIENCE`, before `CERTIFICATES`, one bulleted entry per line.**
The placement argument — this position fails **loudly** if a later edit drops the `SKILLS` branch from
`section_spans`, where placement-last fails silently — holds only because the lines are bulleted.

**Marker equality is part of the grammar, and round 2 found it was not.** Three reviewers
independently found the same bypass: an earlier revision said SKILLS bullets use `_TRAILING_MARKERS`
(which includes `–` and `—`) while `section_spans` collects on `("-", "•", "*")`. A line `– ExampleQL`
then parses into `CvDocument.skills`, renders into the PDF, and is **never containment-checked**.

**The `SKILLS` region in `section_spans` collects on the SAME set `cv/parse.py` accepts for `SKILLS`,
and a guard asserts that equality.** `_BULLET_MARKERS` — the WORK set, which must stay exactly equal
to the gate's citation-checked set — is untouched.

This also **falsifies a shipped claim**: `.rulesync/rules/CLAUDE.md` licenses `_TRAILING_MARKERS`
being wider than `_BULLET_MARKERS` precisely because "the gate never citation-checks" those sections.
`SKILLS` is the first trailing section the hard gate checks, so that sentence must change. It is in
section 13's documentation block.

### 4.2 SC8: the indivisible commit

`section_spans`, both containment rows, `parse.py`/`CvDocument`, `compose._RULES`,
`compose._REQUIRED_HEADERS` and `_unwrap_agent_envelope` change in **one commit**. Splitting them
ships either an always-`UNCITED` section or an ungated one.

Measured: a non-bulleted trailing `SKILLS` section behind a `---` fence is silently deleted by
`_unwrap_agent_envelope`, whose `_looks_like_cv_content` knows only bullets and pipe-separated meta
lines. SC7's bulleted shape is what keeps a real section on the right side of that check.

### 4.3 `section_spans`, and the regressions it must not cause

Gains `SKILLS` as a **named** third region — never a generalised "any all-caps line ends the
section", which that function's docstring is explicit is a gate weakening.

**The property, stated in both directions** — round 2 measured that the earlier one-directional
version had a candidate mechanism that regressed the other way:

> **No bullet's treatment may change except inside the `SKILLS` section itself.**

Two measured cases the plan must test, one in each direction:

- A `PUBLICATIONS` bullet **after `SKILLS`** is citation-checked today. Under a naive
  implementation it falls into the skills region, where a fabricated `92%` is never number-checked
  and ships.
- A `PUBLICATIONS` bullet **after `CERTIFICATES`** is uncited-clean today. Under the obvious repair
  for the first case ("revert to the WORK region on an unmodelled header") it starts being
  citation-checked, which is a new over-fire.

The mechanism is the plan's to choose; **both cases are non-negotiable tests.**

`section_spans` returns a 2-tuple consumed at **two** call sites — `validate`'s loop and
`cv/engine.py`'s STYLE scoping. Skills lines are **excluded from the STYLE tier**: a slop complaint
about a bare skill name is answerable only by renaming the skill, the same reasoning that already
scopes that tier away from employer and certificate lines.

No skill carries an `[id]`; per-phrase citations invite a fake-citation launder.

### 4.4 `cv/parse.py`

- `CvDocument` gains `skills: list[str]`.
- `_TRAILING_SECTIONS` gains `SKILLS`.
- `_BULLET_MARKERS` is **not** touched.
- The repeated-trailing-header refusal extends to `SKILLS`, and its remedy text is **hardcoded**
  ("Emit CERTIFICATES and EDUCATION at most once each"). Derive the list from `_TRAILING_SECTIONS`
  rather than adding a third literal.

---

## 5. SC9: the matching rule

Case-preserving alphanumeric-run tokenisation on both sides; a skill matches when its token sequence
appears as a contiguous subsequence.

Not `core/stem.py`: stemming answers a *relevance* question (right for `rank()`), this is an
*identity* question — a licensed `Widget` would license an emitted `Widgeting`, a different word that
merely shares a stem. `tokens()` is also alphabetic-only, so it destroys the digit-bearing names
section 3.4 protects. Not substring containment: `"java" in "javascript"` is the bug `rank()` was
rewritten to remove.

**Row 1 is case-sensitive** — it scans free prose, where a short common-word skill name would collide
with ordinary English. **Row 2 normalises case and whitespace** — it compares a whole emitted line
against the vocabulary, with no sentence to collide with. Both directions need a guard (section 11).

**Every failure mode of row 1 is an under-fire.** An inflected, lowercase or sentence-initial mention
is not detected. Note this is true only *given* SC5's per-entry abstain: round 2 measured that
without it, row 1 over-fires on a partially annotated vault.

---

## 6. Prompt

Three additions to `compose._RULES`, all phrased to **name no skill** so they cannot go stale:

1. A bulleted `SKILLS` block in the format contract, positioned per SC7, **emitted only when at least
   one entry declares `Skills:`** (SC5).
2. A rule that a bullet may name a skill only if an entry it cites lists that skill (row 1).
3. **A rule for row 2** — that every line of the `SKILLS` section must come from the source bundle.
   Round 2 found the earlier revision supplied no prompt rule for row 2 at all, which is how the
   prompt and the gate came to disagree.

**The neutrality sweep needs work, and the earlier claim that it did not was wrong.**
`tests/test_prompt_neutrality.py`'s `_render` supplies synthetic values for *required* parameters
only — measured, `_employer_line`'s configured branch is already unswept for exactly this reason. A
conditional `SKILLS` block threaded abstain-shaped (mirroring `employers=None`) would likewise be
unswept, making section 6's coverage vacuous. The plan adds an `_SYNTHETIC_ARGS` entry with a
non-empty vocabulary and witnesses it by moving a `_FORBIDDEN` term into the block.

**`composer_headings()` must change with this, and section 9's earlier inference was wrong.** That
helper derives the legal template headings from the `_RULES` **constant**, statically. A conditional
`SKILLS` block never appears in the constant, and a `{skills_block}` substitution slot fails its
`isalpha` filter — so the template heading would be rejected **permanently**, not merely until block 1
lands. The fix keeps it derived rather than hand-listed: render `_RULES` with a representative
non-empty vocabulary and take the headings from the rendered text. That is a derivation change, not a
weakening, and the plan must show the helper still fails on an undeclared heading.

---

## 7. Doctor reconciliation

Two `NOTICE` rows in `core/doctor.py`, modelled on `classify_negatives_vs_skills`:

- inventory skills evidenced by no entry — framing-only, licensing nothing;
- entry `Skills:` names absent from the inventory — licensing, but with no `Domain` or `Signal Value`
  for `rank()`.

Plus the section 3.4 row: a `Skills:` token whose digits also appear as a standalone figure in the
same entry's `Metrics:`.

The precedent reports **an identifier, a count, and a locator**, never the user's text —
`DoctorReport` reaches MCP clients whole. Round 2 found the earlier revision's *ordinal* locator
unresolvable: `cmd_evidence_list` prints no index, and `--pending` selects a different set. The
identifier is therefore the **entry's note title**, which `experience list` already prints. Surfacing
an entry's `Skills:` in that listing is part of this block's work so the rows are actionable.

---

## 8. User-visible surfaces

Round 2 corrected this section: `cli.py`'s registry loop generates `--skills` from `spec.fields`, and
`evidence/wizard.py` generates its question the same way. **Both come free with SC3**, and
hand-writing them outside that loop would be the one place an example-value hint could enter shipped
text. What remains:

- `_render_evidence_note` will emit a blank `Skills:` on newly created experience notes.
- `experience list` surfacing an entry's `Skills:` (section 7).

---

## 9. What was measured

Reproduced by execution against `origin/main` at `1c1d1715`, across two review rounds:

- A digit-bearing skill name reports `INVENTED METRIC` in a bullet and `INVENTED PROFILE METRIC`
  twice in prose (3.4).
- A `SKILLS` section is `UNCITED`, invisible, or invisible-again depending on placement **and line
  shape** (4.1) — and, under an earlier revision's conditional row 2, invisible on an un-annotated
  vault where it is `UNCITED` today (3.2).
- A `PUBLICATIONS` bullet after `SKILLS` leaves the citation-checked region; one after `CERTIFICATES`
  is uncited-clean today (4.3).
- `composer_headings()` returns four headings from the `_RULES` constant, statically (6).
- The morphology candidate set is not quiet (1.2).
- All seven consumers of `EvidenceKind.fields` cope with a fifth `experience` field unedited.
  `rank`, `assign_codes`, `_entry_block`, `doctor` and `preflight` read floor keys or counts and never
  `fields`, which is why no `floor_map` entry is required. **Round 2 correction: `mcpserver.py` was
  over-counted in this list.**
- Mutating the shipped template's `CERTIFICATES` heading to `SKILLS` fails
  `test_every_shipped_template_contributes_no_content`. **The inference drawn from this was wrong** —
  see section 6.

---

## 10. Alternatives declined

- **Licensing the emitted section from Skills Inventory titles** (round 1) — contradicts the prompt
  and makes an inventory-only skill `unsupported` to the auditor.
- **Licensing it from entry `Skills:` alone** (round 2) — narrower than the prompt and than #168.
- **Skills as a full citable source (`cited_by_gate=True`)** — undoes the flag split.
- **Skills framing-only, emitted section licenses nothing** — the #60 hold fires on every CV.
- **Gating what renderers may add instead of composing skills** — a different spec.
- **Association on the skill note** — needs a join; a typo or rename unlinks silently.
- **(b) as a HARD gate, or in the STYLE tier on by default** — see 1.2.

---

## 11. Guards

Each closes a specific fail-open and must be witnessed by mutation — moving or deleting, never adding
— run by node id, and confirmed not already killed by a pre-existing test.

| Guard | The mutant it must kill |
|---|---|
| Digit isolation | folding `_entry_skills_line` into `_entry_block` |
| Digit over-fire | a fabricated number hidden in or adjacent to a licensed skill span passing unreported |
| Skill-token shape | a digit-leading `Skills:` token accepted, re-opening the subtractive path |
| Row-1 abstain | row 1 firing when a cited entry declares no `Skills:` |
| Row-2 fail-closed | an emitted `SKILLS` section going unchecked on an un-annotated vault |
| Request abstain | a `SKILLS` block requested when no entry declares `Skills:` |
| Citation-check preservation | **both** 4.3 cases, one per direction |
| Marker equality | `section_spans`' SKILLS markers diverging from `parse.py`'s SKILLS markers |
| Case-rule direction | swapping row 1's and row 2's normalisation, both ways |
| Section equality | `section_spans` and `_TRAILING_SECTIONS` disagreeing about `SKILLS` |
| Scope assertion | the sweep enumerating zero skills and passing vacuously (`all([])` is `True`) |
| Envelope survival | a real `SKILLS` section stripped by `_unwrap_agent_envelope` |
| Prompt/gate agreement | a `_RULES` rule permitting what a containment row forbids, or the reverse — one test must READ the other string, never restate it |

### 11.1 Five existing guards this collides with

Round 2 found three the earlier revision missed. None may be deleted; each needs a deliberate,
argued change:

1. `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks` asserts
   `len(gate_markers) == 1` over every literal-tuple `startswith()` in `cv/validate.py`. A SKILLS
   region takes it to 2. The pin stops the AST equality test reading the wrong tuple, so it must be
   widened knowingly.
2. `_validate_line_sets_before_the_extraction` — the shipped random sweep's alphabet **already
   contains `"SKILLS"`**, and the proposed helper diverges on **136/2000 rows** at the shipped seed.
   "Update the reference" is the assert-the-code-equals-itself hazard its own comment names.
3. `test_unmodelled_trailing_content_is_refused_rather_than_left_unconsumed` stops raising once
   `_TRAILING_SECTIONS` gains `SKILLS`. Re-anchor it on `PUBLICATIONS`.
4. `test_the_rendered_prompt_has_not_drifted` and `test_the_composer_prompt_has_not_drifted` both red
   (section 2.1) — the first pins D11 as byte-identity with the pre-#165 auditor text.
5. `test_the_allowlist_still_matches_the_frozen_prompt` — section 12.

### 11.2 Fixture neutrality

`Skills:` is a fixture position **no existing sweep reaches**:
`tests/test_fixture_name_neutrality.py`'s evidence collector is keyed on the literal `Company`. Prose
guidance is insufficient — it failed once for `Company` at #135 — so extending that collector is part
of this work. **It must read both the comma and block-list shapes** (section 2); a single-line-only
collector sweeps clean over the shape the repo's own fixtures use. Round 2 also noted a comma-joined
value collects as one identity into the lead-identity roster, which the extension must handle.

Fixture values use invented technology-shaped names (`ExampleQL`, `WidgetFramework`).

---

## 12. `FROZEN_BUNDLE_TEXT` — settled, and the earlier diagnosis was wrong

An earlier revision recorded this as an open question and described the break as digit-driven.
**Round 2 settled it by reading the real test, and the diagnosis was wrong.**

`_oracle` returns a **2-tuple**; section 3.3 makes `BundleSources` 3-field. Measured:

```
Proposed(*oracle) TypeError: missing 1 required positional argument: 'skills'
with skills defaulted to {}: False   # the one-pass build keys all 3 entries with frozenset()
```

It breaks with **no `Skills:` value anywhere and no digit involved** — plain arity. The digit story
was a fixture choice: `Skills: ExampleQL, WidgetFramework` keeps the oracle agreeing (measured
`True`); only a digit-bearing name in the **frozen** fixture breaks it.

**The repair, measured:** compare the two fields `_oracle` actually models —
`(s.nums, s.baseline) == _oracle(...)` — since it transcribes the pre-#174 **numeric** harvester and
never modelled skills. Keep `FROZEN_ENTRIES`' skills digit-free, and put the digit witness over a
separate bundle in the literal-free style `test_a_skills_digit_is_licensed_in_neither_pool` already
uses. This also fixes a break in `tests/test_onboard_questions.py`.

The #174 co-variance property is preserved: the oracle stays an independent transcription of the
numeric harvester, and is not taught to derive anything from `bundle_sources`.

---

## 13. Sequencing

Definition of done for every block: `./.venv/bin/python -m pytest` green, and
`ruff check sluice tests scripts` clean.

1. **Grammar + gate, indivisible (SC8).** `Skills` on the experience kind; `_entry_skills_line`;
   `BundleSources.skills` with its construction check; `section_spans`' SKILLS region with matched
   markers and both 4.3 preservation cases; both containment rows with SC5's abstain and fail-closed
   rules; SC6's digit handling and token-shape refusal; `parse.py` + `CvDocument`; `_RULES`' three
   additions and the `composer_headings()` derivation change; `_unwrap_agent_envelope`; the section
   12 repair; the five guard collisions in 11.1.
2. Doctor rows, plus `experience list` surfacing `Skills:` so they have a locator.
3. `_render_evidence_note`'s blank `Skills:`; the neutrality collector extension (11.2); the
   `_SYNTHETIC_ARGS` prompt-sweep entry (section 6).
4. Documentation: `docs/ARCHITECTURE.md` (the `BundleSources` story, the framing/citable split),
   `docs/USAGE.md`, `sluice.yaml.example` if any text changes, and **`.rulesync/rules/CLAUDE.md`** —
   which states the renderer-precheck exceptions, spells the repeated-trailing-header exception as
   CERTIFICATES/EDUCATION only, and licenses the wider `_TRAILING_MARKERS` on the grounds that the
   gate never citation-checks those sections (4.1 falsifies that). Regenerate with `npm run rulesync`.
5. **Template last.**

Block 1 is large because SC8 makes it indivisible; blocks 2-4 are independent of each other.

Between blocks 1 and 5 a CV composes a `SKILLS` section, passes the gate, parses into
`CvDocument.skills`, and renders nothing. `StrictUndefined` catches a template referencing a missing
field but not the reverse, so that interval is silent by construction. Acceptable because the section
is only requested once a user has populated `Skills:`, but block 5 should not lag.

---

## 14. Accepted risks

- **(b) is not covered mechanically.** Deliberate, on measurement (1.2). #194 carries the evidence.
- **D11's byte-identity pin is broken deliberately** (2.1). The auditor's source set widens by skill
  names on entries it already sees; that widening is what makes an emitted skill supportable rather
  than `unsupported`. It is a change to a pinned property and is stated as one.
- **A metric-shorthand skill name subtracts from the numeric gate** (3.4). `p99` is the example; the
  doctor notice makes it visible.
- **Row 1 under-fires**, by construction and by SC5's per-entry abstain.
- **The two skill vocabularies can drift.** Nothing forces entry `Skills:` and the Skills Inventory to
  agree, so neither becomes a prerequisite for the other. Section 7 makes the drift visible.
