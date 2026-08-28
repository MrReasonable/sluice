# Skills as gated content — design (#168)

Status: proposed, after three `/review-plan` rounds — round 1: 42 findings, 2 Critical; round 2: 38,
2 Critical; round 3: 31, **0 Critical**. Each round's findings were concentrated in the previous
round's own fixes, which is why the corrections below are stated with the measurement that forced
them rather than as bare decisions. Extends #165 (skills reach the composer as framing) and #174 (the
gate is handed a structured `BundleSources`).

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

### 1.3 Coverage of #168's five items

Verified against the issue body, because the review roster's egress guard blocks `gh` and no
round could check this:

| #168 item | Where |
|---|---|
| 1. Source it from a canonical path | SC3 — `Skills:` on the experience entry (§2) |
| 2. Emit a SKILLS section from the composer | §6, conditional per SC5 |
| 3. Model it in `parse.py` and `CvDocument` | §4.4 — `skills: list[str]` |
| 4. Gate it by containment | §3 — rows 1 and 2 |
| 5. Render it in the shipped template | §13 block 5, last |

All five are addressed; none is deferred.

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
Skills: Example Query, Example Framework
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
- **#165's D11 survives, and `_entry_skills_line` is rendered by `render_composer_bundle` ONLY.**
  Two earlier revisions put it in `_source_section`, which `render_bundle` returns and
  `cv/engine.py` hands to `run_audit` — widening the ADVISORY auditor's corpus. They justified that
  as making an emitted skill supportable rather than `unsupported`.

  **That justification does not survive review.** `render_bundle` is the corpus for the WHOLE CV, not
  just the gated `SKILLS` section, so the widening also flips **WORK-bullet** claims to `supported` —
  including precisely the cases row 1 cannot see: an un-annotated cited entry (SC5's per-entry
  abstain, the guaranteed day-one state) and a case-mismatched mention (SC9). A misattributed skill in
  a bullet would then be caught by neither the hard gate nor the #60 sign-off hold, and section 14
  listed those two risks separately without ever noting that the layer removed by the first was the
  only cover for the second.

  Nothing the design needs is lost by keeping the auditor's corpus unchanged: row 2's route into the
  gate is `BundleSources.skills` / `.source_tokens`, derived **structurally** (3.3), so the gate never
  depended on the auditor seeing the text. `render_composer_bundle` already exists for exactly this
  split, and its own docstring states the harm the widening would cause.

  Consequence for section 12: **two** frozen tests go red, not three —
  `test_the_composer_prompt_has_not_drifted` and the allowlist test.
  `test_the_rendered_prompt_has_not_drifted` stays green, and its docstring's D11 byte-identity pin
  stays true.

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
| **Abstains when** | any cited entry declares no **non-empty** `Skills:` | never — fails closed, see SC5 |
| **Matching** | case-sensitive subsequence, in the bullet's prose (SC9) | case-insensitive subsequence, in the source token sequence (SC9) |

### 3.1 SC4: row 2's vocabulary is the bundle's source text

Entry `Skills:` **∪ the baseline CV block ∪ entry bodies**. Exactly those three, enumerated:
**not** "everything `_source_section` contributes", which two reviewers found is a different and
larger set — it also carries the `=== … ===` presentation headers and `_entry_block`'s head line
(`[id] (company) title | metrics=…`), under which an emitted `- Example Alpha` would be a licensed
skill token. The enumeration governs; the gloss is gone.

Three independent reasons for the widening, all from round 2:

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

**The residual this buys, stated rather than left implicit:** an ordinary English word that happens to
appear in the baseline CV or an entry body passes row 2 as a "skill". That is an under-fire, and it is
the right direction here — row 2's job is to refuse *invention*, (b) is out of scope (1.2), and row 1
does the precision work. Recorded in section 14.

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

**Blank is absent, everywhere, and `_entry_skills_line` omits it.** This is not an edge case:
`_evidence_entries` materialises every declared field via `fm.get(k, "")`, so **every existing note
gets `Skills == ""` the day SC3 lands**, and `_render_evidence_note` writes a blank `Skills:` into
every new one. An unconditional line would render N empty `Skills:` lines into both bundles — the same
"negative claim it may act on" that `render_composer_bundle` refuses one function away. So: an empty
value is treated as no value by all three conditions, and contributes no line to either prompt.

The guards in section 11 must say **non-empty**, and must use a *production-shaped* fixture — a note
with a blank `Skills:`, never one with the key omitted, which no production path produces. A
presence-keyed implementation passes a key-omitting fixture while re-opening round 2's measured row-1
over-fire.

All three conditions read **one derived value** so they cannot disagree — reviewers confirmed the
∃-over-all-entries and ∀-over-cited-entries pair can genuinely share one `skills: dict[id → frozenset]`.

### 3.3 Bundle plumbing

`_entry_skills_line(entry)` in `cv/bundle.py`, sibling to `_entry_block` and `_baseline_block`,
rendered by **`render_composer_bundle` only**, after each entry's block.

**Not `_source_section`.** That helper feeds BOTH renderers, so putting skills there widens the
ADVISORY auditor's corpus for the whole CV — see 2.1. The composer needs the text; the gate does
not (it reads the structured members below); the auditor must not have it.

Deliberately **not** folded into `_entry_block`: that function's contract is that every line it
returns is a numeric SOURCE harvested by `bundle_sources`. The new function carries the inverted
contract in its own docstring — every token is a **skill** source for that entry, **no digit of it is
a numeric source**.

`BundleSources` gains **two** members, and the second is what round 3 found missing:

- `entries: dict[str, EntrySources]`, where `EntrySources` is a NamedTuple of
  `(nums: frozenset[str], skills: frozenset[str])` — **row 1's** per-entry vocabulary, carried
  beside the numeric one in ONE id-keyed structure. Two separate id-keyed dicts could disagree about
  what an id is, which is what the `ids` property's docstring argues against; collapsing them makes
  key equality structural for hand-built values too, so the alternative (a `ValueError` in
  `validate()`) is unnecessary — and that guard would have *narrowed the ways in* rather than removed
  the capability, the distinction #174's own docstring draws.
- `source_tokens: tuple[tuple[str, ...], ...]` — **row 2's** bundle-wide vocabulary as one
  ordered token sequence per source block, derived in `bundle_sources`
  from `_baseline_block`, each `_entry_block`'s body, and each `_entry_skills_line`.

  **Its NESTING needs a shape guard.** A flat `tuple[str, ...]` is structurally valid Python and
  iterates as *characters* inside row 2's matcher, so every emitted skill would read UNSOURCED and
  every lead would go `skipped-gate` — silently, on a value that looks right. Reject a member that is
  not a tuple/list of `str` at construction.

The second is not optional. `nums` and `baseline` are **digit** sets (`re.findall(r"\d+", …)`), and
`cv/engine.py` hands `validate` the `BundleSources` and nothing else — so SC4's widened vocabulary,
which needs the baseline's and bodies' *words*, had no route into the gate at all. The obvious repair
(re-parsing rendered bundle text inside `validate`) is exactly what #174 removed, so the tokens are
derived structurally alongside everything else.

`bundle_sources` builds `nums` and `skills` in one pass over `bundle["entries"]`, making key equality
structural. **Round 2's caveat, carried:** that constrains only the factory, not the hand-constructed
`BundleSources` values that exist in tests, and `ids` derived from `nums` alone will not notice a
`skills` key `nums` lacks. The plan adds a construction-time check rather than relying on the one-pass
build alone.

### 3.4 SC6: digit handling in bullets and PROFILE

**Measured on `origin/main`:** a digit-bearing skill name reads to the numeric gate as a fabricated
metric — `INVENTED METRIC ['3']` in a bullet, and `INVENTED PROFILE METRIC 3` twice in prose. Latent
today; #168 makes it the main path. The only actionable answer is to delete a true skill name.

A skill mention's span is removed before `\d+` extraction — the technique `validate.py` already
applies to citations. **It covers PROFILE as well as bullets.**

**Which vocabulary licenses removal is load-bearing, and SC4 made the old phrasing ambiguous.** There
are now two bundle-wide sets, and only one of them may license removal from the HARD numeric gate:

- **In a bullet:** `Skills:` on the entries **that bullet cites**. Nothing wider.
- **In PROFILE:** the union of **entry `Skills:`** across the bundle — PROFILE has no citation to
  scope by, and `profile_permitted` is already a bundle-wide pool.
- **Never SC4's row-2 vocabulary.** That set is the baseline CV's and entry bodies' *words*; licensing
  removal from it would let any ordinary word in the user's prose blank an adjacent digit, which is a
  hole in the numeric gate rather than a fix to it.

**The PROFILE half of that rule has NO POSSIBLE FALSIFIER, and saying so is the honest record.**
Task 6's implementer and its reviewer independently tried to construct a case where the two
vocabularies give different observable results, and both failed for the same structural reason:
`bundle_sources` harvests every body and baseline digit into `nums`/`baseline` via a direct
`\d+` regex, so any digit `source_tokens` could additionally license is already in
`profile_permitted`. The wider vocabulary strips tokens whose digits were permitted anyway.

So this is a **tightening kept for defence in depth**, not an enforced invariant: it costs nothing,
it is the more defensible bound, and it starts to matter the moment `nums` harvesting narrows. But
no test can distinguish the two implementations today, and none should be written claiming to — a
row whose name promises a discrimination it cannot make is worse than one that states the limit.
The test that exists pins the weaker, real property: a declared skill's digits are not reported as
invented in PROFILE prose.

**Span removal is decided independently of row 1's verdict, and getting this wrong was round 3's
sharpest finding.** An earlier revision removed a span only "when a skill mention is licensed" —
i.e. when row 1 passed it. But row 1 is case-sensitive (SC9) and abstains when a cited entry is
un-annotated (SC5), and section 3.3 deliberately keeps skill digits out of every numeric pool. So each
row-1 *under*-fire became a hard `INVENTED METRIC`: `- Migrated to s3 [A1]` (wrong case), or
`… Example Widget3 [A1][B1]` where B declares no `Skills:`. Retry, then `skipped-gate`, on a skill the user really
declared — this section's own opening harm, reintroduced by the fix for it.

The rule: **span removal matches against the cited entries' `Skills:` case-INSENSITIVELY, and runs in
row 1's abstain arm too.** It is a numeric-gate concern, not an attribution verdict. Row 1 keeps its
case-sensitive rule for the separate question of whether a mention is *misattributed*.

**The subtractive-licence constraint.** Span removal makes `Skills:` the first field that *subtracts*
from the hard numeric gate. **Every TOKEN of a skill item must begin with a letter**, and the
per-token part is the whole guard: an item-level check accepts `Result 92` — one
comma-separated item, beginning with a letter — and removal then blanks `92` from every bullet
citing that entry, which is the exact path the rule exists to close. Per token it refuses
`Result 92`, `92x`, `120ms` and a bare `92` alike, while accepting `Example Widget3`, where the
digit sits inside a letter-led token. Refused loudly at bundle construction. Round 2 flagged that the earlier "wholly-numeric" rule missed `92x`, `120ms` and `p99`.

**Accepted residual, stated plainly rather than mitigated:** a letter-leading token that is *also* a
metric shorthand — `p99` — still licenses removal of its digits for bullets citing that entry. It
requires the user to have written `p99` into their own `Skills:`. A tighter rule (two leading
alphabetic characters) would kill legitimate short names, so this is a deliberate trade.

**Correction (final review of this branch), and the code is what shipped.** Two changes. The rule is
"begins with a letter, **or a dot then a letter**" — `SKILL_TOKEN_RE` is `^\.?[A-Za-z]` — because
the letter-only rule made `.NET` and `.NET Core` inexpressible, with no answer to the refusal but to
misspell them; a dot-then-letter token carries no digit for span removal to blank, so it costs the
guard nothing. And the OVER-refusal above is understated as written: it is not only metric shorthand
that is refused but **every DIGIT-leading token**, which costs `ISO 9001`, `Web 2.0`, `Section 508`,
`3D modelling`, `5S` and `802.11ac`. Those stay refused deliberately — a word followed by a bare
number is structurally identical to `Result 92`, and nothing available here separates them — but the
limitation is stated in `SKILL_TOKEN_RE`'s comment, in the raised message, in `docs/USAGE.md` and
`docs/ARCHITECTURE.md`, and pinned by
`test_a_digit_leading_skill_token_stays_refused_whatever_it_names`, rather than left reading as a
rule that only catches shorthand.

An earlier revision proposed a `doctor` notice for a `Skills:` token whose digits also appear in the
same entry's `Metrics:`. **That condition is inverted and the row is dropped:** every digit in
`Metrics:` is already in `nums[eid]` via `_entry_block`, so those are exactly the cases where removal
is a no-op. The set that would matter is the reverse — digits appearing nowhere in that entry's
`_entry_block` numbers — and it is narrow enough that a row reporting it would fire on almost nothing
while still needing a payload that echoes the user's own text into a `DoctorReport`. Recorded in
section 14 instead.

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
(which includes `–` and `—`) while `section_spans` collects on `("-", "•", "*")`. A line `– Example Query`
then parses into `CvDocument.skills`, renders into the PDF, and is **never containment-checked**.

**The `SKILLS` region in `section_spans` collects on the SAME set `cv/parse.py` accepts for `SKILLS`,
and a guard asserts that equality.** `_BULLET_MARKERS` — the WORK set, which must stay exactly equal
to the gate's citation-checked set — is untouched.

**The gate's set must be a SUPERSET, not an equal.** `cv/parse.py` is the `template` renderer's
parser; `script` implements no `precheck` and never parses at all. Asserting equality pins a
renderer-INDEPENDENT gate to one renderer's grammar, so a later narrowing of `_TRAILING_MARKERS`
(template's own business) would silently narrow the gate for every renderer with the assertion still
green. Assert `set(_SKILLS_MARKERS) >= set(parse._TRAILING_MARKERS)` and keep `_SKILLS_MARKERS` as
the gate's own deliberately-wide set; the reverse direction is covered by the SKILLS implication
sweep.

**Both marker tuples become NAMED module constants** in `cv/validate.py` —
`_WORK_BULLET_MARKERS` and `_SKILLS_MARKERS` — and both `startswith` calls take the name. This is
not tidiness: the existing guard recovers literal tuples from the AST and indexes `[0]`, and after
this change there are two non-equal literals in the module. Selecting the WORK one *by value* (the
tuple matching `_BULLET_MARKERS`) turns the equality assertion into a tautology — the
assert-the-code-equals-itself hazard this spec names twice elsewhere. Binding each region's markers
to a name lets the guard read the two `ast.Assign` nodes **by name** and keep asserting something
falsifiable.

This also **falsifies a shipped claim**: `.rulesync/rules/CLAUDE.md` licenses `_TRAILING_MARKERS`
being wider than `_BULLET_MARKERS` precisely because "the gate never citation-checks" those sections.
`SKILLS` is the first trailing section the hard gate checks, so that sentence must change. It is in
section 13's documentation block.

### 4.2 SC8: the indivisible commit

`section_spans`, both containment rows, `parse.py`/`CvDocument`, `compose._RULES` and
`compose._REQUIRED_HEADERS` change in **one commit**. Splitting them ships either an
always-`UNCITED` section or an ungated one.

**`_unwrap_agent_envelope` is NOT in that set, and two earlier revisions were wrong to list it.** Measured on shipped code with `_REQUIRED_HEADERS` unchanged: a
realistic full-wrap #28 envelope leaves a bulleted `SKILLS` section entirely intact, because
`_is_envelope_aside` already returns False for it. **SC7's bulleted shape is what carries the
protection**, so an "envelope survival" guard over `_unwrap_agent_envelope` is an equivalent mutant
and cannot be witnessed — it is dropped from section 11 for that reason rather than left to fail
silently.

`_REQUIRED_HEADERS` stays, and what it does should be stated: it is `_is_envelope_aside`'s
"these lines are real CV content" test, so adding `SKILLS` only *widens* what survives unwrapping and
cannot fail a CV closed.

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

**The mechanism is settled** (round 3): implement the skills region as a **contiguous bullet run
that does not clear `in_work`**. That satisfies both cases without the generalisation `section_spans`'
docstring forbids — the region ends at the first non-bullet line, so an unmodelled header after
`SKILLS` returns to the WORK region it never left, and one after `CERTIFICATES` is untouched.

**A blank line must NOT end the run.** Three reviewers measured the same defect independently:
`cv/parse.py` skips blank runs in three places and its own comment calls that spacing "the LIKELY
case, not an exotic one", so a run ending at the first *non-bullet* line diverges from the section
the parser actually reads. Two harms, both measured against the real `parse_cv`:

- A blank under the header, with `SKILLS` after `EDUCATION`: the skills region is empty AND the work
  region is empty, while `parse_cv` still returns both lines as skills entries and the template
  renders them. **Checked by nothing** — which falsifies 3.2's "row 2 always runs on an emitted
  section … it fails closed".
- A blank *inside* the run drops the following bullets back into WORK, reported `UNCITED BULLET` —
  and 4.4 forbids per-skill `[id]`s, so the retry's only *literal* remedy is the one this design
  rules out, while its cheapest compliance (add `[AL1]`) is work-clean and still renders as a skill.

The rule: **a blank line does not clear `in_skills`; a non-blank non-bullet line does** — the latter
is safe — but **not for the reason an earlier revision gave**, which was measured false. That
revision said `parse_cv` raises on that same terminator line. It does not: it raises
`unmodelled section header 'SKILLS'` at the HEADER, whatever follows the run.

Today the property therefore holds *more* strongly than claimed — any SKILLS section at all
rejects the whole document. But that is an accident of the parser not yet modelling SKILLS, and
it **stops being true at 4.4**, where the parser learns to accept one. From then on the
correspondence has to be established rather than inherited: `cv/parse.py`'s trailing-section
reader refuses a non-marker line under a trailing header, which is what makes gate and parser
stop at the same place. **4.4 owns that obligation and needs a test for it** — a terminator the
gate honours and the parser silently absorbs is a SKILLS line that renders uncontained. In code, `if in_skills and line.strip() and not is_bullet`.

**SC7's guard widens with it.** Marker-set equality compares tuples and cannot see this class at all.
The property to assert is a grammar one: *every entry line in any text `parse_cv` accepts as a SKILLS
section is collected into `section_spans`' skills region*, swept over blank-line placements (under
the header, between entries, trailing) as well as markers.

**Four non-negotiable tests**, not three: the two preservation cases above, bullets after `SKILLS`
with no intervening header, and the after-`CERTIFICATES` no-region case — the first three all keep
`in_work` true and cannot observe it.

`section_spans` returns a **3-tuple** (`profile`, `work`, `skills`) consumed at **two production**
call sites — `validate`'s loop and `cv/engine.py`'s STYLE scoping — **plus nine in `tests/`, across
`tests/test_cv_validate.py` and `tests/test_cv_engine.py`**, all of which unpack two values today. Skills lines are **excluded from the STYLE tier**: a slop complaint
about a bare skill name is answerable only by renaming the skill, the same reasoning that already
scopes that tier away from employer and certificate lines.

No skill carries an `[id]`; per-phrase citations invite a fake-citation launder.

### 4.4 `cv/parse.py`

- `CvDocument` gains `skills: list[str]`.
- `_TRAILING_SECTIONS` gains `SKILLS`.
- `_BULLET_MARKERS` is **not** touched.
- The repeated-trailing-header refusal extends to `SKILLS`, and **its remedy text names the
  sections derived from `_TRAILING_SECTIONS`** — so a repeated `SKILLS` header is told about
  `SKILLS`. The text is a hardcoded "Emit CERTIFICATES and EDUCATION at most once each" today;
  deriving it is the contract, and adding a third literal is not.

---

## 5. SC9: the matching rule

Case-preserving alphanumeric-run tokenisation on both sides; a skill matches when its token sequence
appears as a contiguous subsequence.

Not `core/stem.py`: stemming answers a *relevance* question (right for `rank()`), this is an
*identity* question — a licensed `Widget` would license an emitted `Widgeting`, a different word that
merely shares a stem. `tokens()` is also alphabetic-only, so it destroys the digit-bearing names
section 3.4 protects. Not substring containment: `"java" in "javascript"` is the bug `rank()` was
rewritten to remove.

**One operation, two parameterisations.** Both rows ask whether a skill's token sequence appears
*contiguously* in a haystack; they differ only in haystack and case-folding. An earlier revision
described row 2 as whole-line *set membership*, which SC4 made undefined — its vocabulary is
unstructured source text, not a set of names, and a two-word skill is no single token.

| | haystack | case |
|---|---|---|
| Row 1 | the bullet's prose, vocabulary = union of all entries' `Skills:` | **sensitive** |
| Row 2 | the bundle's source **token sequence** (SC4) | **insensitive** |

Row 1 is case-sensitive because it scans free prose, where a short common-word skill name would
collide with its ordinary sense. Row 2 is case-insensitive because it matches a whole emitted item
against a corpus, with no sentence to collide with — and a case-sensitive rule there would refuse a
skill whose note is filed lowercase. Both directions need a guard (section 11).

**What row 2 costs at this width:** it catches pure invention — a name absent from the whole corpus —
and licenses anything the corpus contains, including an incidental adjacent word pair. That is the
under-fire recorded in section 14, and it is why row 1 does the precision work.

**Every failure mode of row 1 is an under-fire — but only once span removal is decoupled from it
(3.4).** An inflected, lowercase or sentence-initial mention is simply not detected as a
misattribution. Two corrections this claim has needed: round 2 measured that without SC5's per-entry
abstain, row 1 *over*-fires on a partially annotated vault; round 3 measured that while span removal
was gated on row 1's verdict, an under-fire converted into a hard `INVENTED METRIC` one layer down.
The claim is true of the design as it now stands, and false of both earlier ones.

---

## 6. Prompt

Three additions to `compose._RULES`, all phrased to **name no skill** so they cannot go stale:

1. A bulleted `SKILLS` block in the format contract, positioned per SC7, **emitted only when at least
   one entry declares `Skills:`** (SC5).
2. A rule that a bullet may name a skill only if an entry it cites lists that skill (row 1).
3. **A rule for row 2** — that every line of the `SKILLS` section must come from the source bundle.
   Round 2 found the earlier revision supplied no prompt rule for row 2 at all, which is how the
   prompt and the gate came to disagree.

**The neutrality sweep needs work, and both earlier answers were wrong.**
`tests/test_prompt_neutrality.py`'s `_render` supplies synthetic values for *required* parameters
only — measured, `_employer_line`'s configured branch is already unswept for exactly this reason, so a
conditional `SKILLS` block threaded abstain-shaped would likewise be unswept and section 6's coverage
vacuous.

The previous revision's repair — add an `_SYNTHETIC_ARGS` entry — is **inert, measured twice
independently**: `_render` `continue`s on any parameter carrying a default *before* it reads the
overrides, so an entry naming an abstain-shaped vocabulary is read and discarded. A `_FORBIDDEN` term
moved into such a block sweeps clean, meaning the proposed witness would pass while proving nothing.

**`_render` itself must change** — either the parameter's requiredness, or `_render`'s
default-before-overrides precedence. That makes it a sixth entry in 11.1. The witness is unchanged and
non-negotiable: move a `_FORBIDDEN` term into the block and watch the sweep go **red** before trusting
it. Note the block is also invisible to the static `_RULES` reader in
`test_cv_prompt_expresses_no_role_or_culture_preference` when threaded through a substitution slot.

**`composer_headings()` must change, and BOTH earlier answers were wrong.** The helper derives the
legal template headings from the `_RULES` **constant**, statically. A conditional `SKILLS` block never
appears in that constant and a `{skills_block}` slot fails its `isalpha` filter, so the template
heading would be rejected **permanently** — not merely until block 1 lands, which is what section 9
originally inferred.

The obvious repair — render `_RULES` and derive from the rendered text — was proposed in the previous
revision and **four reviewers independently measured that it opens a leak**. `{name_heading}` is
`name.upper()` on its own line, so the substituted name matches the all-caps-alphabetic filter exactly
and enters the set:

```
raw      : ['CERTIFICATES', 'EDUCATION', 'PROFILE', 'WORK EXPERIENCE']
rendered : ['CERTIFICATES', 'EDUCATION', 'PROFILE', 'WORK EXPERIENCE', '<the substituted name>']
```

That set is the **allowlist** in three template no-content guards and in the shipped-file leak sweep,
so a template could then print that literal with every negative guard green — and the stated
acceptance criterion ("still fails on an undeclared heading") cannot catch it, because the leaked name
*is* declared.

**The fix keeps it static and anchors it on an independent source:** derive from the parser's own
grammar — `PROFILE`, `WORK EXPERIENCE`, and `_TRAILING_SECTIONS` (which gains `SKILLS` in 4.4). That is
independent of `_RULES`, so it is not self-certifying, it carries `SKILLS` automatically, and no
substituted value can ever enter it. The plan must still show the helper fails on an undeclared
heading, and must assert the derived set is non-empty.

---

## 7. Doctor reconciliation

Two `NOTICE` rows in `core/doctor.py`, modelled on `classify_negatives_vs_skills`:

- inventory skills evidenced by no entry — framing-only, licensing nothing;
- entry `Skills:` names absent from the inventory — licensing, but with no `Domain` or `Signal Value`
  for `rank()`.

**No doctor row carries user-authored text today** — even the vault problem report names the key
`vault_dir` rather than the path — and `DoctorReport` reaches MCP clients whole. Two locator designs
have now been rejected: an *ordinal* (round 2: unresolvable, `cmd_evidence_list` prints no index and
`--pending` selects a different set), and the **entry's note title** (round 3: `title` is
`evidence_slug()` of the user's own `--name`, which for an Experience Library entry commonly encodes
an employer or client — and the cited precedent's docstring separately excludes the entry title as
"a name the user chose").

**Each row therefore reports a count plus the command that resolves it** — `job-sluice experience list`
or `job-sluice skills list` — and no user-authored string at all. That is the precedent's own shape.
Surfacing an entry's `Skills:` in the `experience list` output is part of this block's work, so the
count is actionable there rather than in the report. This binds all three rows, section 3.4's
included.

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
- Every consumer of `EvidenceKind.fields` copes with a fifth `experience` field unedited. Scope and
  spelling, so a later reader can re-run it rather than trust a count:
  `grep -rn '\.fields' sluice --include='*.py'`, discarding `dataclasses.fields(...)` (that is
  `CandidateProfile`, unrelated). **Six** read sites — `cli.py`'s flag loop, `protocols.py`'s own
  `__post_init__`, `core/vault.py` ×3 (`_render_evidence_note` ×2 and `_evidence_entries`),
  `evidence/wizard.py`, `evidence/commands.py`. No `floor_map` entry is required because
  `__post_init__` validates floor→field, not field→floor.

  Two DOWNSTREAM `entry["fields"]` consumers that grep does not reach, and they matter because one
  of them is where a user first sees the new key: `mcpserver.py` (passes the dict through whole) and
  `cv/bundle.py`'s `_framing_lines` (the `skills` kind only). An earlier revision "corrected" an
  over-count by dropping `mcpserver.py` entirely — right that it is not a `spec.fields` reader,
  wrong to stop mentioning it.

  For contrast, these read floor keys or counts and never `fields`:
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
| Row-1 abstain | row 1 firing when a cited entry declares no **non-empty** `Skills:`, witnessed on a production-shaped blank-value fixture |
| Row-2 fail-closed | an emitted `SKILLS` section going unchecked on an un-annotated vault |
| Request abstain | a `SKILLS` block requested when no entry declares a **non-empty** `Skills:` |
| Citation-check preservation | **both** 4.3 cases, one per direction |
| Marker equality | `section_spans`' SKILLS markers diverging from `parse.py`'s SKILLS markers |
| Case-rule direction | swapping row 1's and row 2's normalisation, both ways |
| Section equality | `section_spans` and `_TRAILING_SECTIONS` disagreeing about `SKILLS` |
| Scope assertion | the sweep enumerating zero skills and passing vacuously (`all([])` is `True`) |
| Prompt/gate agreement | a `_RULES` rule permitting what a containment row forbids, or the reverse — one test must READ the other string, never restate it |
| **SC4's vocabulary width** | narrowing row 2 back to entry `Skills:` alone (round 2's rejected design). The "prompt/gate agreement" row does NOT cover this: its mechanism is reading the other *string*, and row 2's other side is a computed frozenset — measured, that narrowing leaves `_RULES` byte-identical and every other guard green |
| Span-removal independence | gating span removal on row 1's verdict again (3.4), which converts a row-1 under-fire into a hard `INVENTED METRIC` |
| Blank-value handling | treating a blank `Skills:` as a declared value — the default state of every note the day SC3 lands |

### 11.1 Nine existing guards this collides with

Round 2 found three the earlier revision missed. None may be deleted; each needs a deliberate,
argued change:

1. `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks` asserts
   `len(gate_markers) == 1` over every literal-tuple `startswith()` in `cv/validate.py`. Measured
   with the guard's own AST comprehension: a SKILLS region takes it to **0**, not 2, because the
   sweep admits only a literal `ast.Tuple` and 4.1 binds BOTH `startswith` arguments to names. A
   worker widening the pin to `== 2` as an earlier revision instructed still fails; the half-applied
   variant (naming only the SKILLS tuple) leaves exactly 1 literal and the assertion passes green
   with SC7's equality never checked. The remedy is unchanged — recover two `ast.Assign` nodes by
   target name and assert both names were found — but the stated diagnosis now matches it.
2. `_validate_line_sets_before_the_extraction` — the shipped random sweep's alphabet **already
   contains `"SKILLS"`**, and the proposed helper diverges on **136/2000 rows** at the shipped seed.
   "Update the reference" is the assert-the-code-equals-itself hazard its own comment names.
3. `test_unmodelled_trailing_content_is_refused_rather_than_left_unconsumed` stops raising once
   `_TRAILING_SECTIONS` gains `SKILLS`. Re-anchor it on `PUBLICATIONS`.
4. `test_the_rendered_prompt_has_not_drifted` and `test_the_composer_prompt_has_not_drifted` both red
   (section 2.1) — the first pins D11 as byte-identity with the pre-#165 auditor text.
5. `test_the_allowlist_still_matches_the_frozen_prompt` — section 12.
6. `tests/test_prompt_neutrality.py`'s `_render` — its default-before-overrides precedence must
   change for the conditional `SKILLS` block to be swept at all (section 6). Found in round 3; the
   previous revision assumed a fixture entry would suffice, and it is inert.
7. `tests/template_content.py::composer_headings` **and its three consumers** —
   `test_every_shipped_template_contributes_no_content`,
   `test_the_no_content_guard_catches_planted_content`, and
   `test_docs_template_examples_contribute_no_static_content`. Section 6 re-anchors the derivation;
   all three read the result as an allowlist, so all three move with it.
8. `tests/test_cv_parse.py`'s implication sweep gains a **third** documented exception (the SKILLS
   grammar sibling in section 11), alongside the LOCATION and repeated-trailing-header ones.
9. `test_evidence_skill_values_are_on_the_reviewed_roster` (11.2) -- not a pre-existing guard this
   design breaks, but a NEW one this design's own work makes go red repeatedly: Task 4 is where
   fixture skill values start accumulating (`tests/test_cv_skills_containment.py`), and the roster
   this test enforces has no entries until 11.2 lands, ten tasks later. A task whose definition of
   done is a green suite cannot defer the fix that far — the neutrality file's own docstring warns
   this is exactly the suppression pressure that gets a real guard weakened rather than satisfied.
   Each task must roster its OWN new values in the same change, on `_REVIEWED_SKILL_VALUES`, never
   `_REVIEWED_FIXTURE_IDENTITIES` (11.2 states why).

### 11.2 Fixture neutrality

`Skills:` is a fixture position **no existing sweep reaches**:
`tests/test_fixture_name_neutrality.py`'s evidence collector is keyed on the literal `Company`. Prose
guidance is insufficient — it failed once for `Company` at #135 — so extending that collector is part
of this work.

Three shape requirements, the last of them measured off the existing collector's own comment:

- It must read **both the comma and the block-list** spellings (section 2).
- A comma-joined value must collect as **separate identities**, not one — round 2's note.
- It must treat a **literal two-character `\n` escape** as an item separator alongside a real
  newline. This repo's evidence fixtures pack a whole frontmatter block into ONE Python string
  literal joined that way, so a collector reading only real newlines sweeps clean over exactly the
  fixtures that exist. Requires a shape-coverage test, not just a value test.

**Each new fixture skill value joins `_REVIEWED_SKILL_VALUES` — its OWN roster — in the same
change.** Not `_REVIEWED_FIXTURE_IDENTITIES`: that roster's docstring scopes it to "LEAD identities
— employers a fixture names", and `_CV_IDENTITY_EXEMPT` exists by the owner's 2026-08-24 ruling
precisely to keep a product-shaped non-employer value off it ("rostering it would make the roster
mean something wider than it says"). Adding technology names by policy is what that carve-out was
created to prevent — one list answering two different questions, with no way to tell afterwards
which call an entry records. Same tool, separate question, following the `_REVIEWED_CANDIDATE_VALUES`
precedent.

Note `_CV_IDENTITY_RE`'s `[A-Za-z]+` truncates at a digit, so it captures `Example Widget` from
`Example Widget3`. A roster instruction naming only the full value leaves the guard red on a string
nobody typed.
Nothing running locally can establish whether a technology-shaped name belongs to a real product —
that is the judgement the ratchet exists to force at the moment a value is added. `Example Query`,
`Example Framework` and `Example Widget3` are invented for this work and follow the `Example <Word>`
convention the sweep's own failure message prescribes. `Example Widget3` carries a digit deliberately:
it is the fixture that exercises SC6's span removal, and no existing roster entry has that shape.

---

## 12. `FROZEN_BUNDLE_TEXT` — settled, and the earlier diagnosis was wrong

An earlier revision recorded this as an open question and described the break as digit-driven.
**Round 2 settled it by reading the real test, and the diagnosis was wrong.**

`_oracle` returns a **2-tuple**; section 3.3 makes `BundleSources` 4-field (`nums`, `baseline`,
`skills`, `source_tokens`). Measured against the 3-field shape, and the arity argument is unchanged by
the fourth member:

```
Proposed(*oracle) TypeError: missing 1 required positional argument: 'skills'
with skills defaulted to {}: False   # the one-pass build keys all 3 entries with frozenset()
```

It breaks with **no `Skills:` value anywhere and no digit involved** — plain arity. The digit story
was a fixture choice: `Skills: Example Query, Example Framework` keeps the oracle agreeing (measured
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
   additions and the `composer_headings()` derivation change; the section
   12 repair; the nine guard collisions in 11.1.
2. Doctor rows, plus `experience list` surfacing `Skills:` so they have a locator.
3. The neutrality collector extension (11.2). (`_render_evidence_note`'s blank `Skills:` is **not**
   work and does not land here: it writes `{k: str(fields.get(k, "")) for k in spec.fields}`, so the
   blank appears on every new experience note the moment block 1 adds the field. It is live from
   block 1, which is why block 1's abstain guards must be fixtured against a blank value rather than
   a missing key. The `_SYNTHETIC_ARGS` entry moved into block 1 with section 6's `_render` fix.)
4. Documentation. **The claims block 1 falsifies are corrected IN block 1**, not here — an earlier
   revision also deferred `docs/USAGE.md`'s `experience add` flag list, which block 1 falsifies the
   moment the field is DECLARED (the flag is generated from `spec.fields`), one task before any gate
   work. That one is now corrected in block 1 too, together with a derived guard comparing each
   registry-generated `add` command's documented flags against the real parser — so the next field
   addition cannot repeat it. An earlier
   revision repaired them a block later, leaving an interval in which a shipped document asserted
   something the code had already made false and nothing was red. Those are: the wider-`_TRAILING_MARKERS`
   licence in both `.rulesync/rules/CLAUDE.md` and `cv/parse.py`'s own comment ("no check here for a
   wider marker to slip past") -- correct that one PRECISELY, because half of it stays true: the gate
   still never *citation*-checks a trailing section. What changes is that `SKILLS` is now
   *containment*-checked, so "the bypass argument has no force there" no longer holds, and a marker
   the parser accepts and `section_spans` does not is a bypass of the new check; the repeated-trailing-header exception spelled as CERTIFICATES/EDUCATION
   only, and the **2-tuple `section_spans` contract** stated in `.rulesync/rules/CLAUDE.md`,
   `docs/ARCHITECTURE.md` (two places) and `cv/voice.py`.

   This block carries the rest: `docs/ARCHITECTURE.md`'s `BundleSources` story and framing/citable
   split, `docs/USAGE.md`, and `sluice.yaml.example` if any text changes. Regenerate with
   `npm run rulesync`.
5. **Template last.**

Block 1 is large because SC8 makes it indivisible; blocks 2-4 are independent of each other.

Between blocks 1 and 5 a CV composes a `SKILLS` section, passes the gate, parses into
`CvDocument.skills`, and renders nothing. `StrictUndefined` catches a template referencing a missing
field but not the reverse, so that interval is silent by construction. Acceptable because the section
is only requested once a user has populated `Skills:`, but block 5 should not lag.

---

## 14. Accepted risks

- **(b) is not covered mechanically.** Deliberate, on measurement (1.2). #194 carries the evidence.
- **The auditor's corpus is deliberately NOT widened** (2.1), reversing two earlier revisions. The
  cost accepted with it: an emitted `SKILLS` line rests on entry `Skills:` text the auditor cannot
  see, so the #60 audit may classify it `unsupported` and withhold the pointer for human sign-off.
  That is the safe direction, and it is what keeps a *misattributed* skill in a WORK bullet — the
  case row 1 abstains on or under-fires on — still covered by the sign-off hold rather than by
  nothing at all.
- **A metric-shorthand skill name subtracts from the numeric gate** (3.4). `p99` is the example.
  **Documented only — there is no doctor notice for it.** An earlier revision proposed one and
  sections 3.4 and 7 removed it as inverted (its condition selected exactly the no-op cases); this
  bullet claimed it still existed. Re-adding a row here means specifying the *reverse* condition —
  digits appearing nowhere in that entry's `_entry_block` numbers — with a payload that carries no
  user text.
- **Row 2 over-licenses.** Its vocabulary is the bundle's source text, so an ordinary English word
  appearing in the baseline CV or an entry body passes as a "skill" (3.1). Deliberate: row 2 refuses
  invention, (b) is out of scope, and row 1 does the precision work.
- **Row 1 under-fires**, by construction and by SC5's per-entry abstain — and only genuinely so now
  that span removal is decoupled from its verdict (3.4).
- **The two skill vocabularies can drift.** Nothing forces entry `Skills:` and the Skills Inventory to
  agree, so neither becomes a prerequisite for the other. Section 7 makes the drift visible.
