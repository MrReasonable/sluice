# Skills as gated content — design (#168)

Status: proposed, revised after a five-reviewer `/review-plan` round (42 findings: 2 Critical,
22 High, 16 Medium, 2 Low). Extends #165 (skills reach the composer as framing) and #174 (the
gate is handed a structured `BundleSources`).

Issue: **#168**. **#194 is deliberately NOT in this spec** — see section 1.2.

---

## 1. The problem

#168 item 4 asks that "every emitted skill must appear in the source bundle". #194 asks that
"any technology named in a generated CV must appear in the bundle". Both issues say this is one
gate described twice.

Containment against the bundle is one primitive. But it splits into two cases with opposite
false-positive profiles:

- **(a) Misattribution.** A skill the candidate genuinely holds, decorating a bullet whose role
  it is not associated with. Answerable by moving or dropping the mention. No content invented.
- **(b) Pure invention.** A technology the candidate does not hold at all. Requires open-world
  detection.

The repo has paid for confusing these once already: `cv/parse.py`'s LOCATION refusal made the
only actionable reading of its message *invent a city*, turning a parser refusal into
fabrication pressure aimed at the feature that exists to prevent fabrication.

### 1.1 SC1: this spec ships (a) as a HARD gate

(a) is exact, closed over the candidate's own data, and every refusal is answerable by deletion.
It earns the hard tier.

### 1.2 (b) is out of scope, and the reason is a measurement

An earlier revision of this spec put (b) in #167's STYLE tier behind a morphology-based
candidate set — tokens with internal capitals, embedded digits, or a trailing symbol — and
argued that shape test would be quiet on a clean CV *by construction*, so it could ship on by
default without a shipped lexicon.

**Two reviewers measured that claim independently and it is false.**

- Against the repo's own 12 gate-clean CV fixtures, through real `section_spans` and after
  `validate.py`'s citation and bullet strips: **9 residual candidates, none of them a
  technology.** Six were sentence-final English words caught by the trailing-symbol tell. Three
  were metric tokens the numeric gate *already licenses* from a cited `Metrics:` — so a licensed
  figure is reported as an unbundled technology, and the only actionable reading is to delete
  it. That is the LOCATION shape again.
- On a purpose-built CV that `validate()` certifies clean, against a bundle spelling the facts
  in a different form: **2 false positives, `p99` and `120ms`.** (A separate run against the
  three smallest fixtures returned zero, and the reviewer correctly called that measurement
  vacuous — those fixtures scan two profile lines and a handful of `- Shipped [ID]` bullets.)

The unenumerated class is unit- and percentile-shaped tokens. None of the three stated
morphological exclusions covers it, and the cheap repair is a unit-suffix exclusion list — the
shipped vocabulary #194 names as both a maintenance burden and a neutrality problem, and which
this design promised never to ship.

**So (b) does not ship here.** #194 is re-filed carrying this measurement, the (a)/(b) split,
and the tier analysis, so the next attempt starts from evidence rather than from the original
framing. Until then (b)'s only cover remains what covers it today: `compose._RULES`'s
no-fabrication clause, `bundle.py`'s `_DERIVED_NEGATIVE_PROMPT`, and the #60 advisory audit.

**This spec introduces no new config knob.** The earlier `cv.technology_check` went with (b).

---

## 2. The model: skills support claims relationally

**SC2: a skill is not licensed by set membership, but by association with a role.**

The gate already works this way for numbers. `cv/validate.py` permits a figure in a WORK bullet
only if it appears in a **cited** entry — `union = set().union(*(nums[c] for c in cites))` —
never merely somewhere in the bundle. Skills get the identical treatment one level up.

**SC3: the association is stored on the experience entry.**

`EVIDENCE_KINDS["experience"]` gains `Skills` in its `fields` tuple:

```
# Job Applications/Experience Library/alpha-platform-rebuild.md
Company: Example Alpha
Category: platform
Best For: event-driven work
Metrics: 40% latency reduction
Skills: ExampleQL, Widget3
Verified: 2026-08-01
```

No `floor_map` entry: `Skills` has no floor analogue, the same reason `Proficiency`, `Evidence`
and `Signal Value` have none on the skills kind. The review enumerated all seven consumers of
`EvidenceKind.fields` and confirmed every one copes with a fifth field unedited — see section 9.

**Delimiter: comma-separated on one line.** `_parse_fm_spaced` is line-based, so a multi-line
frontmatter value does not round-trip (its continuation lines are re-read as further keys) —
the same constraint that keeps STAR's Situation/Task/Action/Result in the note body. Leading and
trailing whitespace per item is stripped; an empty item is dropped.

The relation is many-to-many and could equally have lived on the skill note as `Roles:`.
Storing it on the entry was chosen because that is where the gate already reads: `bundle_sources`
walks `bundle["entries"]` and derives `nums` per entry, so a per-entry skills frozenset slots in
beside it with no company-name join and no resolution pass. It also makes licensing per-**entry**
rather than per-employer for free.

The costs are real and accepted: adding one skill means editing the entries that evidence it,
and the Skills Inventory note does not state its own roles. Section 8 makes that drift visible.

### 2.1 SC4: the emitted section is licensed by entries ONLY, never by the Skills Inventory

An earlier revision licensed the emitted `SKILLS` section from "all entries' `Skills:` ∪ Skills
Inventory titles". **All five reviewers rejected that union**, and it was the single
most-corroborated finding of the round. The reasons, verified in code:

- `_DERIVED_NEGATIVE_PROMPT`'s own docstring states that **naming a technology IS a claim**, and
  both it and `compose._RULES` require every fact to come from the BASELINE CV or a VERIFIED
  EXPERIENCE ENTRY. Licensing an inventory-only skill contradicts the prompt the same run ships.
- `cv/engine.py` hands `run_audit` the output of `render_bundle`, which never shows the
  inventory. An inventory-only skill is therefore a claim absent from the auditor's bundle:
  verdict `unsupported`, and at the shipped `cv.require_signoff: true` the send-ready pointer is
  withheld. That is verbatim the degradation section 10 declines an alternative to avoid.
- `BundleSources.skills` keyed by entry id could not have held that vocabulary anyway: Skills
  Inventory entries deliberately carry no `[id]` (`build_bundle` ranks them but never
  `assign_codes` them, "because an `[id]` is what makes a thing citable").

With the union dropped, the licensing source is the `Skills:` field on the **experience** kind
alone — already gate-licensed, already rendered into `render_bundle`, already seen by the
auditor. So:

- `EVIDENCE_KINDS["skills"]` keeps `cited_by_gate=False`, `read_by_composer=True`. Unchanged.
- `_DERIVED_NEGATIVE_PROMPT` keeps naming exactly two claim sources. Unchanged.
- #165's D3 survives verbatim: a Skills Inventory line still supports nothing on its own, and
  now nothing in this design contradicts that.
- #165's D11 survives: the auditor is still never shown the framing.
- The Skills Inventory keeps exactly its existing job — ordering and framing via `rank()` on the
  `Domain`-mapped `best_for`. It gains nothing and loses nothing.

**Wording correction the review forced.** `cited_by_gate` is a **per-kind** flag, not a
per-field one. Saying the association is licensed "because the experience kind is
`cited_by_gate=True`" reads the flag at the wrong altitude, and that flag's docstring calls
over-claiming here "the worst direction to be wrong in". The accurate statement: the gate
licenses content the composer emitted from a citable kind, and `Skills:` is a field on such a
kind; the flag itself is unchanged and gains no new meaning.

**Consequence for `skills_unreadable`.** With the inventory out of every gate, `cv/engine.py`'s
existing swallow of an unreadable Skills Inventory stays licensed by its stated premise — that a
thing affecting only tailoring QUALITY may never bin a lead. The earlier revision falsified that
premise by making the inventory half of a hard gate; this one does not.

---

## 3. Two scopes

| Scope | Licensed by | Tier |
|---|---|---|
| A skill named in a WORK bullet | union of `Skills:` on the entries **that bullet cites** | HARD |
| The emitted `SKILLS` section | union of `Skills:` across **all bundle entries** | HARD |

### 3.1 SC5: both rows abstain when the vocabulary is empty

**This is the fix for the round's two Critical findings.** `Skills:` is a new field, so it is
absent on every vault that exists today: the derived vocabulary is empty everywhere on first
upgrade. An earlier revision added the `SKILLS` block to `compose._RULES` *unconditionally*
against a gate that could license nothing — prompt demands the section, every line violates, one
retry, `skipped-gate`, **on every lead**. That is the `672ad2a` class exactly.

The precedent was already in the file and unapplied: `render_composer_bundle` omits the framing
header entirely when the inventory is empty, because "an empty header would assert to the model
that the candidate holds no skills, a negative claim it may act on."

Carried over:

- **The `SKILLS` block is added to `_RULES` only when the derived vocabulary is non-empty.**
  No vocabulary, no section requested, nothing to violate.
- **Row 2 runs only when the vocabulary is non-empty.**
- **Row 1 abstains by construction.** It fires only on a token that IS in the bundle-wide skill
  vocabulary but is NOT in the cited entries' sets. An empty vocabulary detects nothing, so
  nothing can violate. This resolves an ambiguity the earlier revision left open — row 1 scans
  for members of the *vocabulary*, never for "anything that looks like a skill".

A vault with no `Skills:` anywhere therefore behaves exactly as it does today, which is the
abstain-shaped outcome this invariant requires.

### 3.2 Bundle plumbing

A new `_entry_skills_line(entry)` in `cv/bundle.py`, sibling to `_entry_block` and
`_baseline_block`, rendered by `_source_section` immediately after each entry's block so **both**
audiences see it.

Deliberately **not** folded into `_entry_block`: that function's stated contract is that every
line it returns is a numeric SOURCE harvested by `bundle_sources`, so putting skills there would
license every digit inside every skill name at once. The new function carries the inverted
contract in its own docstring: every token it returns is a **skill** source for that entry, and
**no digit of it is a numeric source**.

`BundleSources` gains a third field, `skills: dict[str, frozenset[str]]`, keyed by entry id
exactly like `nums`. Row 2's vocabulary is the union of its values, so one structure serves both
rows.

Two obligations the review surfaced:

- **`skills.keys() == nums.keys()` must be guaranteed at construction.** `validate()`'s existing
  guard is `isinstance`-only by design ("the type ONLY, never the value"), and hand-constructed
  `BundleSources` values exist in tests. `bundle_sources` builds both dicts in one pass over
  `bundle["entries"]`, which makes the equality structural rather than asserted.
- **`ids` stays a derived property over `nums`.** That NamedTuple's docstring explains why
  carrying `ids` as data would re-create the #174 redundancy; adding `skills` keyed by the same
  ids does not re-open it *provided* the one-pass construction above holds, and that is the
  reason it is stated as an obligation rather than left implicit.

### 3.3 SC6: digit handling, in bullets and in PROFILE

**Measured on `origin/main` at `1c1d1715`:** a skill name containing a digit reads to the numeric
gate as a fabricated metric.

```
licensed nums for that entry: ['40']
VIOLATION: INVENTED METRIC ['3'] not in ['EX1']: - Ran the migration on Widget3 with a 40% latency reduc
```

This is a latent defect today; #168 makes it the feature's main path, since any name with an
embedded digit is affected. The only actionable answer to `INVENTED METRIC ['3']` is to delete a
true skill name.

**The fix, and its two corrections from review:**

When a skill mention is licensed, its span is removed before `\d+` extraction — the technique
`cv/validate.py` already applies to citations.

1. **It covers PROFILE as well as bullets.** Measured: the same CV yields
   `INVENTED PROFILE METRIC 3` twice, and the earlier revision's bullet-only fix left that live
   in the one region the prompt change makes more likely to contain skill names. PROFILE has no
   citation to hang the per-entry rule on, so it uses the **bundle-wide** skill vocabulary —
   consistent with how that region already works, since `profile_permitted` is already a
   bundle-wide numeric pool rather than a per-entry one. This is not a new principle, it is the
   existing PROFILE/WORK asymmetry applied to a second kind of token.
2. **A `Skills:` value may not license a bare numeric token.** Span removal makes `Skills:` the
   first field that *subtracts* from the hard numeric gate, and with no shape constraint an entry
   declaring `Skills: Result 92` would blank `92` from every bullet citing it — a laundering path
   this design would have introduced. Only digits *inside* an alphanumeric token are removable
   (`Widget3`, `ExampleQL2`); a skill token that is wholly numeric licenses nothing and is
   rejected at bundle construction, loudly, in this module's house style.

**Correction to an earlier claim.** The earlier revision said an unlicensed digit-bearing mention
"reports as a skill violation rather than as a phantom metric". That is not what the mechanism
does: spans are removed only for *licensed* skills, so an unlicensed mention keeps its digits and
`validate` emits `INVENTED METRIC` **alongside** the skill violation. Two messages, both
actionable, and the spec no longer claims otherwise.

---

## 4. Grammar

### 4.1 SC7: placement and line shape

**Measured on `origin/main`,** a `SKILLS` section behaves in three different ways:

| `SKILLS` placed | line shape | `section_spans` collects | `validate` reports |
|---|---|---|---|
| After `WORK EXPERIENCE`, before `CERTIFICATES` | bulleted | its lines, as WORK bullets | `UNCITED BULLET` per line |
| After `EDUCATION` (last) | bulleted | **nothing** | **nothing at all** |
| After `WORK EXPERIENCE`, before `CERTIFICATES` | comma list | **nothing** | **nothing at all** |

**`SKILLS` is emitted after `WORK EXPERIENCE`, before `CERTIFICATES`, with one bulleted entry per
line** using `_TRAILING_MARKERS`.

Both halves are load-bearing and the third row is why. The placement argument — that this
position fails **loudly** (`UNCITED BULLET`) if a later edit drops the `SKILLS` branch from
`section_spans`, where placement-last fails silently — **holds only if the lines carry a bullet
marker**, because `section_spans` collects a WORK line only on `startswith(("-", "•", "*"))`. An
unmarked comma-list in the correct position is exactly the silent trap the placement was chosen
to avoid. The earlier revision specified the placement and not the shape, which reviewers
correctly called a placeholder that the whole of section 4 rested on.

Composed order is grammar; rendered order is presentation. The template may place skills anywhere
on the page.

### 4.2 SC8: the indivisible commit

`section_spans`, the containment check, `parse.py`/`CvDocument`, and `compose._RULES` change in
**one commit**. Splitting them ships either an always-`UNCITED` section or an ungated one.

`compose._REQUIRED_HEADERS` and `_unwrap_agent_envelope` are in that commit too, and were missing
from the earlier revision's file list. Measured: a non-bulleted trailing `SKILLS` section behind a
`---` fence is silently deleted by `_unwrap_agent_envelope`, whose `_looks_like_cv_content` knows
only bullets and pipe-separated meta lines. The bulleted shape in SC7 is what keeps a real
`SKILLS` section on the right side of that check, and the plan must pin it.

### 4.3 `section_spans`, and the regression it must not cause

Gains `SKILLS` as a **named** third region — never a generalised "any all-caps line ends the
section", which that function's docstring is explicit is a gate *weakening*.

**The earlier revision claimed "every other unmodelled header keeps exactly its current
behaviour". That is false, measured.** Today a `PUBLICATIONS` bullet after `WORK EXPERIENCE` is
citation-checked, because `in_work` stays set. Under SC7, `SKILLS` clears `in_work`, so a
`PUBLICATIONS` section emitted after `SKILLS` falls into the *skills* region instead — where a
fabricated `92%` is containment-checked and never number-checked, and ships.

**The requirement, stated so the plan cannot miss it: no bullet that is citation-checked today
may stop being citation-checked.** The mechanism is the plan's to choose and to test — reverting
to the WORK region on an unmodelled header is the obvious candidate — but the property is not
negotiable, and it needs a test that emits `PUBLICATIONS` after `SKILLS` and asserts the bullet is
still citation-checked.

`section_spans` returns a 2-tuple consumed at **two** call sites — `validate`'s own loop and
`cv/engine.py`'s STYLE scoping. A third region changes the signature for both. The plan must say
what the STYLE tier does with skills lines: the answer should be **exclude them**, since a slop
complaint about a bare skill name is answerable only by renaming the skill, which is the same
reasoning that already scopes that tier away from employer and certificate lines.

No skill carries an `[id]`; per-phrase citations are clumsy and invite a fake-citation launder.

### 4.4 `cv/parse.py`

- `CvDocument` gains `skills: list[str]`.
- `_TRAILING_SECTIONS` gains `SKILLS`; the trailing reader already uses the wider
  `_TRAILING_MARKERS`.
- `_BULLET_MARKERS` is **not** touched — it must stay exactly equal to the gate's set.
- The repeated-trailing-header refusal extends to `SKILLS`, **and its remedy text is
  hardcoded**: "Emit CERTIFICATES and EDUCATION at most once each". Extending the refusal without
  the message produces a refusal naming the wrong sections. Derive the list from
  `_TRAILING_SECTIONS` rather than adding a third literal.

---

## 5. The matching rule

**SC9: row 1 uses case-sensitive exact token-sequence match, no stemming. Row 2 normalises.**

Tokenisation is case-preserving alphanumeric runs on both sides; a skill matches when its token
sequence appears as a contiguous subsequence of the line's tokens.

Not `core/stem.py`: stemming answers a *relevance* question (right for `rank()`) and this is an
*identity* question — a licensed `Widget` would license an emitted `Widgeting`, a different word
that merely shares a stem. `tokens()` is also
alphabetic-only, so it destroys the digit-bearing names section 3.3 protects. Not substring
containment: `"java" in "javascript"` is the bug `rank()` was rewritten to remove.

**Row 1 is case-sensitive** because it scans free prose, where a short common-word skill name
would otherwise collide with ordinary English. **Row 2 normalises case and whitespace** because it
compares a whole emitted line against the vocabulary, with no sentence to collide with. Getting
these backwards in either direction is a real harm, and neither direction currently has a guard —
section 11 adds one.

**Every failure mode of row 1 is an under-fire**, which is the direction a hard gate must err. An
inflected, lowercase, or sentence-initial mention is simply not detected.

---

## 6. Prompt

Two additions to `compose._RULES`, both phrased so they **name no skill** and therefore cannot go
stale — the property `_DERIVED_NEGATIVE_PROMPT` exists to have:

1. A bulleted `SKILLS` block in the format contract, positioned per SC7, **emitted only when the
   derived vocabulary is non-empty** (SC5).
2. A rule that a bullet may name a skill only if an entry it cites lists that skill.

The per-entry licensed set is already visible to the model through `_entry_skills_line`, so
neither rule enumerates anything. #165's D3 sentence is untouched.

`tests/test_prompt_neutrality.py` renders `compose.build_prompt` with synthetic arguments, so
these additions are swept for neutrality automatically — verified during review.

---

## 7. Doctor reconciliation

Two `NOTICE` rows in `core/doctor.py`, modelled on `classify_negatives_vs_skills`:

- inventory skills evidenced by no entry — framing-only, able to license nothing;
- entry `Skills:` names absent from the inventory — able to license, but with no `Domain` or
  `Signal Value` for `rank()` to order the framing by.

**The precedent reports three things, not one**: an index into the user's own list, a count, and a
locator command. The earlier revision proposed a bare count, which reviewers correctly identified
as unactionable — and an unactionable count is exactly what creates pressure to put the skill text
into `DoctorReport`, which reaches MCP clients whole. So each row carries a count **and a
locator**. `job-sluice skills list` exists; `job-sluice experience list` prints title and marker
only, so surfacing an entry's `Skills:` is part of this block's work. Titles and citation codes are
not safe locators (`_prefix` takes the company's first two letters); an ordinal is.

---

## 8. User-visible surfaces

Named because the review found them unlisted, not because they are hard:

- `job-sluice experience add` needs a `--skills` argument, or the field is unreachable without
  hand-editing a note.
- The evidence wizard needs a `Skills` question.
- `_render_evidence_note` will emit a blank `Skills:` on newly created experience notes.

---

## 9. What was measured

Reproduced by execution against `origin/main` at `1c1d1715`, and independently re-verified during
review:

- A digit-bearing skill name in a bullet reports `INVENTED METRIC`; the same CV reports
  `INVENTED PROFILE METRIC` twice (section 3.3).
- A `SKILLS` section is `UNCITED`, invisible, or invisible-again depending on placement **and line
  shape** (section 4.1).
- Mutating the shipped template's `CERTIFICATES` heading to `SKILLS` fails
  `test_every_shipped_template_contributes_no_content` with
  `contributes content of its own: ['SKILLS']`, so the template cannot grow a skills section
  before `_RULES` emits one. **#168's item 5 needs a note, not a new guard.**
- A `PUBLICATIONS` bullet after `SKILLS` leaves the citation-checked region (section 4.3).
- The morphology candidate set is not quiet (section 1.2) — the measurement that removed (b).
- All seven consumers of `EvidenceKind.fields` cope with a fifth `experience` field unedited:
  `protocols.__post_init__`, `vault._render_evidence_note`, `vault._evidence_entries`, the CLI's
  evidence iteration, `evidence/commands.py`, `evidence/wizard.py`, `mcpserver.py`. `rank`,
  `assign_codes`, `_entry_block`, `doctor` and `preflight` read floor keys or counts and never
  `fields`, which is why no `floor_map` entry is required.

---

## 10. Alternatives declined

- **Licensing the emitted section from Skills Inventory titles.** Rejected by all five reviewers;
  see section 2.1.
- **Skills become a full citable source (`cited_by_gate=True`).** Undoes #165's D11 and re-opens
  the over-claim the flag split was made to prevent.
- **Skills stay framing-only and the emitted section licenses nothing.** The auditor cannot see
  the framing, so every emitted skill classifies `unsupported` and the #60 hold fires on every CV.
- **Reject #168's premise and gate what renderers may add instead.** A legitimate different spec;
  it does not deliver skills as tailored, gated content.
- **Association on the skill note, keyed by company or by entry title.** Needs a join, and a typo
  or a note rename unlinks a skill silently.
- **(b) as a HARD gate over a bundle-derived permitted set.** What #194 literally asks for, and
  the option that walks into the hazard #194 itself names.
- **(b) in the STYLE tier, on by default.** Was this spec's own earlier position; removed on
  measurement (section 1.2).

---

## 11. Guards

Each closes a specific fail-open, and each must be witnessed by mutation — moving or deleting,
never adding. A mutant killed by a pre-existing test witnesses nothing about a new one, so each
must be run by node id and confirmed unique.

| Guard | The mutant it must kill |
|---|---|
| Digit isolation | folding `_entry_skills_line` into `_entry_block`, licensing every skill digit as a metric |
| Digit **over**-fire | a fabricated number hidden in or adjacent to a licensed skill span passing unreported — the direction the earlier revision's guard list omitted entirely |
| Numeric-token refusal | a wholly-numeric `Skills:` token being accepted, re-opening the `Skills: Result 92` laundering path |
| Abstain | a vault with no `Skills:` anywhere emitting a `SKILLS` block, or running row 1 or row 2 at all |
| Citation-check preservation | a `PUBLICATIONS` bullet after `SKILLS` no longer being citation-checked |
| Case-rule direction | swapping row 1's and row 2's normalisation, in **both** directions |
| Section equality | `section_spans` and `parse._TRAILING_SECTIONS` disagreeing about `SKILLS` |
| Scope assertion | the containment sweep enumerating zero skills and passing vacuously — `all([])` is `True` |
| Envelope survival | a real `SKILLS` section being stripped by `_unwrap_agent_envelope` |
| Prompt/gate agreement | a `_RULES` rule permitting what the containment check forbids, or the reverse — one test must READ the other string, never restate it |

**Two existing guards this design collides with**, both to be resolved in the plan, not assumed:

- `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks` asserts
  `len(gate_markers) == 1` over every literal-tuple `startswith()` in `cv/validate.py`. A bulleted
  `SKILLS` region takes that count to 2 — reproduced — and reds the scope pin. The pin is
  load-bearing (it is what stops the AST equality test reading the wrong tuple), so it needs
  widening deliberately, not deleting.
- The implication sweep in `tests/test_cv_parse.py` is a three-way parametrize over separator ×
  terminal × start-month applied to the **first role's date range in one fixture** — not a general
  gate-clean ⇒ no-raise sweep. "Extend it over the SKILLS alphabet" misdescribed it; the plan
  should add a sibling sweep for the SKILLS grammar rather than parameters to that one.

**Fixture neutrality.** `Skills:` is a fixture position **no existing sweep reaches**:
`tests/test_fixture_name_neutrality.py`'s evidence collector is keyed on the literal string
`Company`. Prose guidance is not enough — it already failed once for `Company` at #135 — so
extending that collector to the `Skills` field is part of this work, not an aspiration. Fixture
values use invented technology-shaped names (`ExampleQL`, `Widget3`), which no existing collector
would match either.

---

## 12. Open question for the plan

**`FROZEN_BUNDLE_TEXT` and the #174 co-variance oracle.** Re-capturing the frozen prompt does not
fix `test_the_allowlist_still_matches_the_frozen_prompt`; it **breaks it permanently**. That test
compares `bundle_sources(b)` against `_oracle(FROZEN_BUNDLE_TEXT)`, and `_oracle` transcribes the
pre-#174 harvester, taking every digit on any line following an `[ID]` line. Measured: oracle
`['3','31',…]` against real `['31',…]`, the `3` coming from `Widget3`. This design deliberately
renders a line that must **not** be harvested, and the test has no way to express that.

Two candidate repairs, neither adopted here:

- Hand-extend `_oracle` to model the skills line as a literal. The docstring's prohibition is on
  deriving the oracle *from* `bundle_sources` — which would make it assert the code equals itself
  — and a hand-written extension arguably preserves the two-independent-implementations property.
- Render the skills line outside the region the oracle harvests.

**The plan reproduces the failure first and chooses with the real test in front of it.** This is
recorded as an open question rather than settled from a description, because the right repair
depends on structure that has not been read closely enough yet, and guessing wrong disables a
detector #174 exists to keep.

---

## 13. Sequencing

1. **Grammar + gate, indivisible (SC8).** `Skills` on the experience kind, `_entry_skills_line`,
   `BundleSources.skills`, `section_spans`' `SKILLS` region and the citation-check preservation
   property, both containment rows with their abstain rule, the digit handling in bullets and
   PROFILE with the numeric-token refusal, `parse.py` + `CvDocument`, `compose._RULES` and the
   envelope check. Resolve the `FROZEN_BUNDLE_TEXT` question (section 12) inside this block —
   it cannot land green otherwise.
2. Doctor reconciliation rows, plus surfacing an entry's `Skills:` in `experience list` so the
   rows have a locator.
3. User-visible surfaces (section 8): `experience add --skills`, the wizard question.
4. Documentation: `docs/ARCHITECTURE.md` (the `BundleSources` story, the framing/citable split),
   `docs/USAGE.md`, `docs/CONFIGURATION.md` if any key text changes, `sluice.yaml.example`, and
   `.rulesync/rules/CLAUDE.md` — which currently states the renderer-precheck exceptions and
   spells the repeated-trailing-header exception as CERTIFICATES/EDUCATION only. Regenerate with
   `npm run rulesync`.
5. **Template last** — already mechanically blocked until block 1 lands (section 9).

Between blocks 1 and 5 a CV composes a `SKILLS` section, passes the gate, parses into
`CvDocument.skills`, and renders nothing. `StrictUndefined` catches a template referencing a field
`CvDocument` lacks but not the reverse, so that interval is silent by construction. It is
acceptable because the section is only requested when a user has populated `Skills:`, but block 5
should not lag blocks 2-4 by long.

---

## 14. Accepted risks

- **(b) is not covered mechanically.** Deliberate (section 1.2), on measurement. #194 carries the
  evidence.
- **The two skill vocabularies can drift.** Entry `Skills:` and the Skills Inventory are
  maintained separately, and nothing forces them to agree so that neither becomes a prerequisite
  for the other. Section 7 makes the drift visible.
- **Row 1 under-fires.** A lowercase or inflected mention of a misattributed skill is not caught.
  Correct direction for a hard gate.
- **"The #60 hold does not weaken" is a claim, not yet a measurement.** `_entry_skills_line` sits
  in `_source_section`, which `render_bundle` returns and `cv/engine.py` hands to the auditor, so
  the auditor's source set genuinely does widen — by skill names attached to entries it already
  sees. The expected effect is that emitted skills classify `supported` rather than
  `unsupported`. The plan must measure that rather than inherit this sentence.
