# Skills as gated content, and the containment gate — design (#168 + #194)

Status: proposed. Supersedes nothing; extends #165 (skills reach the composer as framing)
and #174 (the gate is handed a structured `BundleSources`).

Issues: #168 (`CvDocument` has no skills field, so skills get injected at render time and
bypass the fabrication gate) and #194 (mechanical named-technology gate). They are planned
together because they propose the same primitive — see "One mechanism, three scopes".

---

## 1. The problem, and the correction to how the issues frame it

#168 item 4 asks that "every emitted skill must appear in the source bundle". #194 asks
that "any technology named in a generated CV must appear in the bundle". Both issues say
this is one gate described twice, and warn that two designs for one gate is how the two
drift.

Containment against the bundle is indeed one primitive. **But the two scopes have opposite
false-positive profiles, and collapsing them into one tier is what would actually go
wrong.**

- **Misattribution (a).** A skill the candidate genuinely holds, decorating a bullet whose
  role it is not associated with. The refusal is answerable by moving or dropping the
  mention. No content is invented. It passes the standing test this repo applies to every
  new refusal.
- **Pure invention (b).** A technology the candidate does not hold at all. Catching this
  requires open-world detection, which is where the shipped-lexicon maintenance burden,
  the neutrality problem, and the invention pressure all live. This is #194's headline
  case, and a relational check cannot see it: an invented name is not in the candidate's
  own vocabulary, so nothing matches it.

The repo has already paid for getting this distinction wrong once. `cv/parse.py`'s LOCATION
refusal made the only actionable reading of its message *invent a city*, turning a parser
refusal into fabrication pressure aimed at the feature that exists to prevent fabrication.

**Decision SC1: one spec, one containment primitive, two tiers split by scope.** (a) is
HARD and blocks. (b) lands in the STYLE tier introduced by #167 — findings that drive the
composer's single retry but cannot bin a lead, escalating to a #60 sign-off hold only under
the opt-in `cv.style_hold`. A false positive there costs a retry and a human glance, never
a binned lead.

That tier already exists and is already proven; nothing in either issue mentions it.

---

## 2. The model: skills support claims relationally

**Decision SC2: a skill is not licensed by set membership, but by association with a role.**

The gate already works this way for numbers. `cv/validate.py` permits a figure in a WORK
bullet only if it appears in a **cited** entry — `union = set().union(*(nums[c] for c in
cites))` — never merely somewhere in the bundle. Skills get the identical treatment one
level up: naming a skill in a bullet is licensed by the entries that bullet cites, not by
the bundle as a whole.

**Decision SC3: the association is stored on the experience entry.**

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

No `floor_map` entry: `Skills` has no floor analogue, the same reason `Proficiency`,
`Evidence` and `Signal Value` have none on the skills kind.

The relation is many-to-many and could equally have lived on the skill note as
`Roles:`. Storing it on the entry was chosen because that is where the gate already reads:
`bundle_sources` walks `bundle["entries"]` and derives `nums` per entry, so a per-entry
skills frozenset slots in beside it with no company-name join and no resolution pass. It
also makes licensing per-**entry** rather than per-employer for free, so a skill used on one
project does not license every bullet from that employer.

The costs are real and accepted: adding one skill means editing the entries that evidence
it, and the Skills Inventory note no longer states its own roles. Section 8 makes that
drift visible rather than silent.

### 2.1 This is what resolves #165's D3/D11 tension

#165 shipped the Skills Inventory as framing: not code-assigned, `bundle_sources` never
sees it, `compose._RULES` (that spec's D3) tells the model never to rest a claim on it
alone, and the #60 advisory auditor is never shown it at all (that spec's D11, the
two-renderer split in `cv/bundle.py`).

#168 wants skills emitted as content. The obvious readings of that both cost something:
making the inventory citable undoes D11 and re-opens the `cited_by_gate` over-claim; leaving
it framing-only means the auditor cannot see the framing, so it classifies every emitted
skill `unsupported`, and at the shipped `cv.require_signoff: true` the pointer is withheld on
every CV — the #60 hold degrading into noise.

**Storing the association on the experience entry avoids both.** The licensing source
becomes the `Skills:` field on the **experience** kind, which is already
`cited_by_gate=True` and already rendered into `render_bundle` — the auditor's view.
Therefore:

- `EVIDENCE_KINDS["skills"]` keeps `cited_by_gate=False`, `read_by_composer=True`. Unchanged.
- `_DERIVED_NEGATIVE_PROMPT` keeps naming exactly two claim sources. Unchanged, and
  `test_the_derived_constraint_names_the_same_claim_sources_as_the_prompt_rule` keeps
  passing.
- #165's D3 survives verbatim: a Skills Inventory line still supports nothing on its own.
- #165's D11 survives: the auditor is still never shown the framing, and does not need to
  be, because every emitted skill traces to an experience entry it already sees.
- The #60 hold does not weaken.

The Skills Inventory keeps its existing job — ordering and framing, via `rank()` on the
`Domain`-mapped `best_for` — and gains one more: it is part of the vocabulary the emitted
`SKILLS` section may draw on (section 4).

---

## 3. One mechanism, three scopes

| Scope | Licensed by | Tier |
|---|---|---|
| A skill named in a WORK bullet | union of `Skills:` on the entries **that bullet cites** | HARD |
| The emitted `SKILLS` section | union of `Skills:` across all bundle entries ∪ Skills Inventory titles | HARD |
| Any other technology-shaped token in PROFILE/WORK prose | present **anywhere** in the bundle | STYLE |

Rows 1 and 2 are #168 (and #194's case (a)). Row 3 is #194's case (b).

### 3.1 Bundle plumbing

A new `_entry_skills_line(entry)` in `cv/bundle.py`, sibling to `_entry_block` and
`_baseline_block`, rendered by `_source_section` immediately after each entry's block so
**both** audiences see it.

It is deliberately **not** folded into `_entry_block`. That function carries a stated
contract — every line it returns is a numeric SOURCE for that entry, harvested by
`bundle_sources` — so putting skills there would license every digit inside every skill
name at once. The new function carries the inverted contract, stated in its own docstring:
every token it returns is a **skill** source for that entry, and **no digit of it is a
numeric source**. This mirrors `_framing_lines`, which is deliberately not named
`_skills_block` for the same reason.

`BundleSources` gains a third field:

```python
class BundleSources(NamedTuple):
    nums: dict[str, frozenset[str]]
    baseline: frozenset[str]
    skills: dict[str, frozenset[str]]   # keyed by entry id, exactly like `nums`
```

`nums` and `baseline` are untouched. `ids` stays a derived property over `nums`.

### 3.2 The digit fix, which is not optional

**Measured on `origin/main` at `1c1d1715`, before any change:** a skill name containing a
digit, in a bullet, reads to the numeric gate as a fabricated metric.

```
licensed nums for that entry: ['40']
VIOLATION: INVENTED METRIC ['3'] not in ['EX1']: - Ran the migration on S3 with a 40% latency reduc
```

This is a latent defect today — nothing currently invites skill names into bullets, so it
surfaces only when a model happens to write one. **#168 makes it the feature's main path.**
Any name with an embedded digit is affected, which is a large share of real technology
names.

The only actionable reading of `INVENTED METRIC ['3']` is to delete a true skill name. That
is the LOCATION shape, arriving as a side effect of #168 rather than from the new gate.

**Fix:** when a bullet's skill mention is licensed, its span is removed before `\d+`
extraction — the same technique `cv/validate.py` already applies to citations, via
`_CITE_RE.sub` for the profile and `re.sub(r"\[[^\]]+\]", "", line)` for bullets. Only spans
for skills the cited entries actually license are removed, so an *unlicensed* digit-bearing
mention still reports, and reports as a skill violation rather than as a phantom metric.

---

## 4. Grammar

### 4.1 Placement, chosen for its failure mode

**Measured on `origin/main`,** a `SKILLS` section behaves in two completely different ways
depending on where it sits:

| `SKILLS` placed | `section_spans` collects | `validate` reports | `parse_cv` |
|---|---|---|---|
| After `WORK EXPERIENCE`, before `CERTIFICATES` | its lines, as WORK bullets | `UNCITED BULLET` per line | `unmodelled section header 'SKILLS'` |
| After `EDUCATION` (last) | **nothing** | **nothing at all** | `EDUCATION: unrecognised line 'SKILLS'` |

The second row is the trap. `EDUCATION` clears `in_work` and nothing re-sets it, so a
trailing `SKILLS` section is invisible to every check.

**Decision SC4: `SKILLS` is emitted after `WORK EXPERIENCE` and before `CERTIFICATES`,**
because if a later edit drops the `SKILLS` branch from `section_spans`, that placement fails
**loudly** (`UNCITED BULLET`) where placement-last fails **silently**.

Composed order is grammar; rendered order is presentation. The template may still place
skills anywhere on the page, so this constrains the PDF layout not at all.

### 4.2 The indivisible commit

**Decision SC5: `section_spans`, the containment check, `parse.py`/`CvDocument` and
`compose._RULES` change in one commit.**

If `SKILLS` joins `_TRAILING_SECTIONS` and `_RULES` without `section_spans` gaining a
matching region, the result is a section no check looks at — the exact ungated-content hole
#168 exists to close, rebuilt inside the composer instead of inside a renderer. This is a
stronger sequencing constraint than the one #168 states for its own item 5.

### 4.3 `section_spans`

Gains `SKILLS` as a **named** third region. Never as a generalised "any all-caps line ends
the section": that function's docstring is explicit that the generalisation is a gate
*weakening*, because bullets under headers this module does not model (`PUBLICATIONS`,
`PROJECTS`, `AWARDS`) are citation-checked today and would silently stop being.

Only `SKILLS` changes treatment — it stops being citation-checked and starts being
containment-checked. Every other unmodelled header keeps exactly its current behaviour.

No skill carries an `[id]`. #168's own reasoning applies: per-phrase citations are clumsy,
and requiring a citation on prose invites a fake-citation launder — the same argument
`cv/validate.py` already makes for PROFILE prose.

### 4.4 `cv/parse.py`

- `CvDocument` gains `skills: list[str]`.
- `_TRAILING_SECTIONS` gains `SKILLS`.
- The trailing reader already uses the wider `_TRAILING_MARKERS`, so dash and bullet-glyph
  variants work unchanged.
- `_BULLET_MARKERS` is **not** touched. It must stay exactly equal to the gate's own set,
  and `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_checks` enforces that
  equality in both directions.
- The existing refusal of a REPEATED trailing header extends to `SKILLS` for the reason it
  already refuses a repeated `CERTIFICATES`: entries under the second heading are dropped
  from the PDF, and the template guards the section with `{% if %}`, so the heading vanishes
  with them.

---

## 5. The matching rule

**Decision SC6: case-sensitive exact token-sequence match, no stemming.**

Tokenisation is case-preserving alphanumeric runs, applied to both sides; a skill
matches when its token sequence appears as a contiguous subsequence of the line's tokens.

Three things this deliberately is not:

- **Not `core/stem.py`.** Stemming is right for `rank()`, which asks a *relevance*
  question, and wrong here, which asks an *identity* question: a licensed `Python` would
  license an emitted `Pythonic`, widening the gate. `tokens()` is also alphabetic-only, so
  it destroys exactly the digit-bearing names section 3.2 exists to protect.
- **Not substring containment.** `"java" in "javascript"` is the bug `rank()` was rewritten
  to remove; a containment gate must not reintroduce it.
- **Not case-insensitive.** This is what keeps the closed-world hazard out of the HARD
  tier. A candidate whose inventory lists a short, common-word skill name is not blocked for
  using that word in its ordinary English sense.

**This rule governs row 1 only** — scanning free prose for skill mentions. Row 2
compares a whole emitted `SKILLS` line against the vocabulary, where there is no
ordinary-English collision to guard against (nothing is being found *inside* a sentence),
so row 2 normalises case and whitespace before comparing. Getting this backwards would
make row 2 unable to match an inventory note filed lowercase against a capitalised emitted
skill, and every inventory-only skill would be refused.

The Skills Inventory's contribution to row 2's vocabulary is each verified entry's `title`
— the note filename without `.md` — which is what `Vault._evidence_entries` already
supplies and what `rank()` already orders.

**Every failure mode of row 1 is an under-fire**, and that is the point. A missed
misattribution is still reachable by the STYLE tier and the #60 audit; a false HARD block
bins a lead. An inflected mention, a lowercase mention, or a sentence-initial capital
simply is not detected, so it cannot produce a violation.

---

## 6. The (b) detector

**Decision SC7: on by default, with a morphology-based candidate set.**

Candidates are tokens carrying a structural technology tell — internal capitals, an
embedded digit, or a trailing symbol. (Shapes, named here only as shapes: `PostgreSQL`,
`S3`, `C#`.) Never "any capitalised word".

Two properties follow, and the second was not the reason for the choice but confirms it:

1. **Cost.** STYLE findings feed `retry_msgs`, so every finding costs a second LLM call for
   that lead. A morphology test excludes ordinary sentence-initial capitals, employer names
   and month names *by construction*, so a clean CV costs nothing.
2. **Neutrality.** No vocabulary ships. This is the same line `classify_negatives_vs_skills`
   already holds when it uses a length floor instead of a stopword list — *"no stopword list
   ships, which is the thing this repo declines to do"*. #194 names a shipped lexicon as
   both a maintenance burden and a neutrality problem; a structural test has neither.

**Scope:** the PROFILE and WORK prose lines from `section_spans`, exactly as #167 scopes the
existing STYLE tier, and for the reason that tier states — the only way to answer a
complaint about an employer or certificate line is to rename the employer or the
certificate. The `SKILLS` section is excluded, being HARD-checked already.

**Subtraction:** every candidate present anywhere in the bundle text, case-insensitively.

**Accepted gap, stated plainly:** a plain-English invented technology carrying no
morphological tell is not detected. Coverage was given up deliberately to keep the default
cheap. `cv.technology_check` turns the check off; nothing turns it up, and widening the
candidate set is a later decision to make on its own evidence.

---

## 7. Prompt

Two additions to `compose._RULES`, both phrased so they **name no skill** and therefore
cannot go stale — the property `_DERIVED_NEGATIVE_PROMPT` exists to have:

1. A `SKILLS` block in the format contract, placed per SC4.
2. A rule that a bullet may name a skill only if an entry it cites lists that skill.

The per-entry licensed set is already visible to the model through `_entry_skills_line`, so
neither rule enumerates anything. #165's D3 sentence is untouched.

Adding a `SKILLS` heading to `_RULES` also licenses the template heading automatically —
see section 10.

---

## 8. Doctor reconciliation

Two `NOTICE` rows in `core/doctor.py`, modelled on `classify_negatives_vs_skills`, covering
the two ways the corpora drift once the association lives on the entry:

- inventory skills evidenced by no entry — listable in `SKILLS`, able to decorate nothing;
- entry `Skills:` names absent from the inventory — able to decorate, but with no `Domain`
  or `Signal Value` for `rank()` to order the framing by.

Both report **counts only**, never the skill text. `DoctorReport` is returned whole to MCP
clients, which is why the existing row reports an index and an overlap size rather than the
configured prose.

`Vault.preflight` already iterates `EVIDENCE_KINDS` reporting `<kind>_verified` and
`<kind>_pending`; it answers with facts, and classification stays in `core/doctor.py`.

---

## 9. Config

One knob: `cv.technology_check`, defaulting `true`, in the same shape as
`cv.require_signoff`. It is not a preference gate, so the empty-config-abstains invariant is
not in play.

Added to `CvConfig` and to `sluice.yaml.example` as a catalogue entry, never hardcoded in
logic.

---

## 10. What was measured, and what was not

Measured by execution against `origin/main` at `1c1d1715`, in a clean worktree:

- **A digit-bearing skill name in a bullet reports `INVENTED METRIC`.** Section 3.2. This is
  the finding that makes the digit fix load-bearing rather than tidy.
- **A `SKILLS` section is either flagged `UNCITED` or invisible, depending on placement.**
  Section 4.1. This is the finding that dictates SC4 and SC5.
- **#168's item-5 ordering constraint is already mechanically enforced.**
  `tests/template_content.py:composer_headings()` derives the legal template headings from
  `compose._RULES` itself. Mutating the shipped template's `CERTIFICATES` heading to `SKILLS`
  fails `test_every_shipped_template_contributes_no_content` with
  `contributes content of its own: ['SKILLS']`. So the template physically cannot grow a
  skills section before `_RULES` emits one. **#168's item 5 needs no new guard — only a
  note that the existing one covers it.**

Reasoned but not executed, and flagged as such for the plan to verify:

- that case-sensitive exact matching under-fires in every direction (argued in section 5
  from the tokenisation, not measured against a corpus);
- that the morphology candidate set is quiet on a clean CV (argued, not measured — the plan
  should measure it against the repo's existing gate-clean fixtures before this ships on by
  default).

---

## 11. Alternatives declined

- **Skills become a full citable source (`cited_by_gate=True`).** Undoes #165's D11,
  re-opens the over-claim `cited_by_gate` was split from `read_by_composer` to prevent, and
  lets a skills line support a WORK bullet's number.
- **Skills stay framing-only and the emitted section licenses nothing.** Cheapest, but the
  auditor still cannot see the framing, so every emitted skill classifies `unsupported` and
  the #60 hold fires on every CV — degrading the one layer that covers qualitative
  fabrication into noise.
- **Reject #168's premise and gate what renderers may add instead.** A legitimate different
  spec; it does not deliver skills as tailored, gated content, which is what #168 is for.
- **Association on the skill note keyed by company.** Matches how a person maintains an
  inventory, but needs a company-name join, over-licenses across an employer's entries, and
  makes a typo silently unlink a skill.
- **Association on the skill note keyed by entry title.** Per-entry precise, but keys the
  relation on a note filename — an artifact of how the vault was organised on the day —
  where a rename unlinks silently.
- **(b) as a HARD gate over a bundle-derived permitted set.** What #194 literally asks for,
  and the option that walks into the hazard #194 itself names: closed-world matching over
  free prose flags ordinary English, and a hard block whose only actionable reading is
  "reword until it stops complaining" is the LOCATION refusal with a wider blast radius.
- **(b) reporting without driving the retry.** Introduces a third behaviour into a tier
  built around two, and denies the model the retry that would fix a real invention.

---

## 12. Guards

Each closes a specific fail-open, and each must be witnessed by mutation — moving or
deleting, never adding.

| Guard | The mutant it must kill |
|---|---|
| Digit isolation | folding `_entry_skills_line` into `_entry_block`, which licenses every skill digit as a metric |
| Section equality | `section_spans` and `parse._TRAILING_SECTIONS` disagreeing about `SKILLS`, derived from AST like the existing bullet-marker equality test |
| Scope assertion | the containment sweep enumerating zero skills and passing vacuously — `all([])` is `True`, and for a negative guard finding nothing is the success case |
| Implication sweep | extend `tests/test_cv_parse.py`'s gate-clean ⇒ parse-does-not-raise rows over the `SKILLS` alphabet |
| Under-fire direction | a *licensed* digit-bearing skill no longer reporting `INVENTED METRIC` (section 3.2's measured case, inverted) |
| Prompt/gate agreement | a rule in `_RULES` permitting what the containment check forbids, or the reverse — one test must READ the other string rather than restate it |

The last is this repo's named recurrence: prose added to a prompt contradicting prose
already in it, with both guard tests blind because each asserted one side.

Fixtures use invented technology-shaped names (`ExampleQL`, `Widget3`, `Foo#`) rather than
real products — the morphology cases stay exercised, and a fixture list of real
technologies would hint at the candidate's actual stack.

---

## 13. Sequencing

1. **Grammar + gate, indivisible (SC5).** `Skills` on the experience kind,
   `_entry_skills_line`, `BundleSources.skills`, `section_spans`' `SKILLS` region, the
   containment check for rows 1 and 2, the digit fix, `parse.py` + `CvDocument`, `_RULES`.
2. Doctor reconciliation rows.
3. The (b) STYLE-tier detector and `cv.technology_check`.
4. **Template last** — already mechanically blocked until block 1 lands (section 10).

Blocks 2 and 3 are independent of each other and may land in either order.

---

## 14. Accepted risks

- **(b) misses a plain-English invented technology.** Deliberate (SC7). Coverage traded for a
  cheap default; the derived negative and the #60 audit remain the only cover for that shape,
  as they are today.
- **The two skill vocabularies can drift.** Entry `Skills:` and the Skills Inventory are
  maintained separately. Section 8 makes the drift visible; nothing forces them to agree,
  deliberately, so that neither corpus becomes a prerequisite for the other.
- **Case-sensitive matching under-fires.** A lowercase or inflected mention of a
  misattributed skill is not caught. Accepted as the correct direction for a hard gate.
- **`_entry_skills_line` widens the composer prompt**, so `FROZEN_BUNDLE_TEXT` must be
  re-captured. That freeze is a ratchet against a literal, not against the world: a human
  reading the freeze diff is still what distinguishes a deliberate prompt change from a
  silent allowlist widening.
