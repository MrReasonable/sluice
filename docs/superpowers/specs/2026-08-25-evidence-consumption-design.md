# Consume the Skills Inventory: a fourth bundle section, derived negatives, and a ranker that survives word forms

Issue: #165 (slices A, B1 and E of five). Depends on #164 (CLOSED) and #174 (MERGED, `b3e439f`).

Scope agreed with the author before design: this closes proposals 1 and 2a of #165, plus the
ranking defect raised in its comment. Proposal 2b (a mechanical named-technology hard gate),
proposal 3 (STAR Stories to interview prep briefs and cover notes) and proposal 4
(evidence-aware triage) are each filed separately -- see **Out of scope**.

Every claim below was produced by executing the shipped code, not by reading it. Where a claim
in an earlier draft turned out to be false, the correction is kept inline rather than quietly
replaced, because the false version is the one a reader is likely to arrive at independently.

## Goal

#164 built the evidence corpus and wired its capture path. It deliberately stopped short of
consumption, and left the seam pre-cut: `EVIDENCE_KINDS` marks `experience` as
`cited_by_gate=True` and comments that "`skills`/`stories` default to False until #165"
(`core/protocols.py:145`). `skills` also carries `floor_map=(("best_for", "Domain"),)` so that
`cv/bundle.py`'s ranker can already score a skills entry.

Nothing reads it. The bundle the composing model sees is three sections, and the Skills
Inventory is in none of them. `Proficiency`, `Domain`, `Evidence` and `Signal Value` -- the
fields that say how a skill should be READ, which is exactly what stops a composer reaching for
the job ad's vocabulary instead of the candidate's own -- have never reached the model.

Two further defects travel with it.

`cv.negatives` is a hand-typed prose shadow of the same file. It asserts which technologies the
candidate does and does not work in, is maintained separately from the inventory that already
answers that, and goes stale with nothing anywhere reporting the disagreement.

And `bundle.rank()` cannot match word forms.

### The ranking defect, measured

`rank()` scores by raw substring containment between two vocabularies nobody normalised:
`_jd_keywords` extracts whole words of 4+ letters from the ad, and `score` asks `k in hay` where
`hay` is `best_for + category + title`.

An entry that directly evidences the ad's most-emphasised requirement, buried among entries that
match other ad words:

```
jd keywords: ['delivery', 'documenting', 'planning']
ranked order: ['unrelated-0', ..., 'unrelated-5', 'THE-RIGHT-ONE']
position of THE-RIGHT-ONE: 6 of 7
its score: 0 | an unrelated entry's score: 2
```

`"documenting" in "documentation"` is `False`, so the one entry that answers the requirement
scores zero and ranks last. The evidence existed, was verified, and was in the bundle.

**A first draft of this measurement was wrong and is worth recording.** It ranked two entries,
both scoring 0, and reported that `documentation` came first -- which demonstrated nothing, since
`sorted` is stable and that was simply input order. A ranking defect is only visible when the
right entry has to BEAT scoring competitors, so the probe must bury it among them.

## Decisions taken (and the options rejected)

### D1 -- Skills are a fourth section, and non-citability is structural rather than parsed

`build_bundle` grows a `skills` parameter and a `bundle["skills"]` key. A NEW
`render_composer_bundle` emits the section below; `render_bundle` -- the auditor's renderer --
never does, and D11 is why. That split arrived after this decision was first written, and D1 said
`render_bundle` until then: left uncorrected, the design record would direct a future change at
the one renderer that must never carry framing.

```
=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ===
- <title> | proficiency=<Proficiency> | domain=<Domain> | signal=<Signal Value>
  <Evidence>
  <body>
```

Before #174 this would have been delicate: `validate()` recovered the citable ids by re-parsing
the bundle TEXT, so a new section had to be proven to reset that parser the way NEGATIVE
CONSTRAINTS does (#31). #174 deleted the parser. `bundle_sources` now walks `bundle["entries"]`
and nothing else, so a skills key is invisible to the allowlist by construction. Measured, with
a skills entry carrying the sentinels 999 and 998 attached to a real bundle:

```
sources: BundleSources(nums={'EX1': frozenset({'31','32','33','34','37'})}, baseline=frozenset({'21'}))
'999'/'998' licensed anywhere? False
```

This is presentation, so `render_composer_bundle` owns it (D11). `_entry_block`'s standing rule -- *every line
this function returns is a source for that entry, and nothing else is* -- is untouched, and must
stay untouched: a maintainer who "tidies" the skills block into `_entry_block` licenses every
skills digit for every entry at once.

**Rejected: making skills citable.** #165 rules it out and is right to. Skills entries contain
numbers, so a `[id]` per skill widens the numeric allowlist the fabrication gate depends on, in
exchange for citations on claims that are framing rather than fact.

### D2 -- The section is emitted AFTER the last entry

`tests/test_cv_bundle.py::_oracle` is the pre-#174 text parser, kept deliberately as a
co-variant detector: it is fed the FROZEN literal rather than `render_bundle`'s output, because
deriving the reference from the code under test certifies that the code equals itself. Adding a
section changes what that oracle sees, so placement has to be measured, not reasoned about:

```
skills after  / 3 entries   : oracle==sources? True    skills digits the oracle licenses that sources do not: []
skills before / 3 entries   : oracle==sources? False   ... ['61', '62', '8']
```

After the last `[id]`, the oracle's `=== header ===` reset drops skills lines into neither pool
and the two derivations still agree. Before the entries, `seen_id` is still False and the oracle
folds skills numbers into `baseline` -- a genuine disagreement.

**An earlier draft of this section justified the placement with a claim that is false.** It said
the zero-entry case also depended on placement. It does not: the disagreement at zero entries is
PRE-EXISTING and has nothing to do with skills. With no skills section at all:

```
render_bundle([]) -> oracle baseline=['21','91']  sources baseline=['21']  agree=False
```

The negatives digit 91 already leaks into the oracle's profile pool, which is precisely the
zero-entry hole #174's design records and `bundle_sources` fixed. `FROZEN_ENTRIES` carries three
entries, so the frozen test never exercises it either way. The placement decision stands on the
3-entry measurement alone.

Placement is also right on its own terms: framing that says "use these to decide what to
emphasise ABOVE" belongs after what it frames, and the hard "must NOT appear" list stays last.

### D3 -- Skills license numbers in NEITHER pool, and the prompt says so

`validate()` has two number pools: the per-entry WORK-bullet allowlist, and the wider PROFILE
pool (`baseline` union every entry's numbers). D1 keeps skills out of both.

The cost is real and must be stated. The model is shown a bundle it is told is "the ONLY
permitted source", and a skills entry reading `Proficiency: 8 years` is a true fact the candidate
declared. A composer that writes it into the profile earns `INVENTED PROFILE METRIC 8`, one
retry, and -- if it repeats -- a skipped lead. That is a trap of our own construction, so the
prompt closes it: one new CV RULE in `compose.py:_RULES` stating that the SKILLS INVENTORY
orders and emphasises, is never citable, and that no number may be quoted from it.

The refusal passes this repo's own test for a legitimate one (`cv/parse.py`'s two deliberate
exceptions): it is **answerable without inventing content** -- drop the figure, or cite the
experience entry that carries it. That is exactly what the LOCATION-field refusal failed.

**Rejected: letting skills numbers join the PROFILE pool.** Superficially attractive, because the
baseline CV already has that exact shape -- shown to the model, not citable by `[id]`, its numbers
permitted in PROFILE prose only. The asymmetry is that the profile sweep is the one region with
no BAD-CITATION backstop behind it, and "this digit appears somewhere in some skills entry" is a
much weaker licence than "this digit is in the candidate's authoritative CV". Widening later is
cheap; narrowing after CVs have shipped under a candidate's name is not.

**Rejected: stripping digits from the skills block.** It removes the trap by never showing the
model an unusable number, and it mangles real content -- `Python 3`, `OAuth2`, `S3`, `Node 18`.

### D4 -- An empty inventory emits no section, and there is no config knob

A missing Skills Inventory directory reads as `[]` without raising (measured), so the common
"user has not populated it" case needs no special handling. When `bundle["skills"]` is empty,
`render_composer_bundle` emits no header at all: an empty `=== SKILLS INVENTORY ===` asserts to the model
that the candidate has no skills, which is a negative claim it may act on.

No `cv.skills_in_bundle` knob. Populating the inventory IS the opt-in, and a flag whose only job
is to duplicate "is this list empty" is the inert-flag drift this repo removes on sight (the
`leads` passes' missing `--dry-run` is the same rule).

### D5 -- A broken corpus warns and degrades; it never bins a lead

`read_evidence` raises only on genuine breakage. Measured, a symlinked corpus:

```
raised OSError: evidence directory '.../Job Applications/STAR Stories' is a symlink ...
```

`cv/engine.py` reads skills inside `run_one`'s try, beside `read_experience_entries`. Letting an
OSError propagate there would give the framing-only corpus the power to fail every lead in the
batch. So the read is caught, WARNED by name, and composition proceeds with no skills section --
with the fact surfaced on `CvResult` rather than swallowed.

This mirrors `dossier_failed`, which exists for precisely this shape and whose comment states the
principle: it "does NOT change control flow ... only visibility: without it, `status: rendered` is
indistinguishable from a CV genuinely tailored to a real job description." The #167 rule that a
style finding may never cost a lead is the same rule one layer out.

`doctor` already reports an unreadable corpus per-kind, so this is degraded-and-visible, not
silent. The experience read keeps its current behaviour and is NOT wrapped: it is the gate's only
citable evidence, and a bundle with no ids fails every bullet anyway.

### D6 -- `negatives` gains a derived cross-reference; the drift is reported by `doctor`

`build_bundle` prepends one derived constraint when the inventory is non-empty: *claim no
technology, language, framework or tool that is not named in the SKILLS INVENTORY or VERIFIED
EXPERIENCE ENTRIES above.* It names nothing, so it cannot go stale. Configured `cv.negatives`
are unioned after it and the key stays, because an inventory cannot express the negatives that
are not about skills at all ("never claim a security clearance").

**Be honest about what this buys.** A derived line does not stop a stale hand-typed line
disagreeing with the inventory; it adds a third voice. What closes #165's actual complaint is
making the disagreement visible: a new pure `classify_negatives_vs_skills(negatives, skill_terms)`
in `core/doctor.py` reports any `cv.negatives` string naming a skill the verified inventory
holds. It reuses D7's stemmer, which is what makes these slices one change rather than three.

It lives in `Sluice.doctor()`, which already holds both the store and the cv config -- **not** in
`Vault.preflight()`, whose docstring commits it to counts rather than content and which is a
Store-seam contract every implementation would have to grow.

**Rejected: generating an enumeration of the inventory into the negatives block.** It duplicates
the SKILLS section immediately above it, grows without bound, and still reports no drift.

**Rejected: refusing `cv.negatives` outright**, the way `load_cv_config` refuses a legacy
`cv.name`. Negatives have legitimate non-skills uses, so refusing the key removes a capability to
fix a drift the key is not the only cause of.

### D7 -- Porter's stemmer, in `sluice/core/stem.py`, certified against Porter's own vocabulary

Pure, deterministic, standard-library. `rank()` tokenises and stems both sides; the D6 doctor
check reuses it.

Four candidate matchers were measured against a table of must-conflate and must-not-conflate
pairs before choosing:

| matcher | correct | notes |
|---|---|---|
| substring (today) | 8/25 | misses every inflection; also matches `java`/`javascript`, `scala`/`scalability` |
| ad-hoc suffix list | 20/25 | unprincipled: `deployment` to `deploym` but `deployments` to `deplo` |
| common prefix (4 thresholds) | 15-20/25 | every threshold has false positives AND misses |
| **Porter** | **22/25** | all three misses are genuinely different words (`mentoring`/`mentorship`) |

Porter gets every must-not-conflate pair right, including all four that today's substring match
gets wrong.

A table of cases chosen by the person writing the code certifies nothing, so the implementation
is certified against an external reference instead: Martin Porter's published 23,531-word test
vocabulary and its expected output (`tartarus.org/martin/PorterStemmer`).

On provenance, stated exactly rather than paraphrased. That page licenses the *encodings of the
algorithm*: "All these encodings of the algorithm can be used free of charge for any purpose",
and its FAQ adds that licence notes are "never more restrictive than the BSD License". It states
no separate terms for `voc.txt`/`output.txt`, so vendoring them rests on the reasonable reading
that the test files share the algorithm's terms -- an inference, not a quoted grant.

**Decision (author, 2026-08-25): vendor them.** The concern was raised and answered -- this
project is open source, and a takedown request, in the unlikely event of one, is a fine outcome
to accept. The alternative (a one-off validation recorded here, no fixture in the repo) was
rejected because the mutation study below shows the standing check is the only thing that catches
four of the rules.

Two obligations follow from taking that decision rather than avoiding it. The fixture carries a
provenance header naming Martin Porter, the source URL and the capture date, because correct
attribution is right on its own terms and makes a request trivial to honour. And the header says
the file is a VERBATIM third-party corpus, so no future neutrality or fixture sweep "cleans up" a
word in it -- the corpus is only worth anything while it is byte-identical to the reference.

```
agreement: 23531/23531 = 100.0000%
```

That was **not** the first result. A faithful reading of the 1980 paper scores 99.932%, and all
16 failures are one class -- `apology`, `assembly`, `horribly`, `possibly` and their kin. They are
the reference implementation's two documented departures from the paper: step 2 carries
`bli -> ble` in place of `abli -> able`, and adds `logi -> log`. With those, agreement is exact.

**The fixture is the full corpus, and a random sample was measured and rejected.** A 42-mutant
delete-only study (mutating by MOVING or DELETING, never ADDING) found 34 killable mutants and 8
equivalent ones. Several rules are witnessed by exactly ONE word in the whole corpus:

```
    1  step2/3 drop tional->tion    e.g. ['traditional']
    1  step2/3 drop izer->ize       e.g. ['temporizer']
    1  step2/3 drop logi->log       e.g. ['apology']
    1  step2/3 drop alize->al       e.g. ['naturalize']
```

so sampling essentially never catches them:

```
random sample  2000: mutants surviving, per trial -> [7, 8, 8, 8, 8, 9, 7, 7, 8, 8, 5, 8]
random sample  4000: mutants surviving, per trial -> [3, 3, 4, 5, 5, 4, 6, 4, 3, 8, 5, 6]
```

Even 4,000 words -- 17% of the corpus -- leaves mutants alive. The full corpus kills all 34 by
construction and costs 67 ms. The working-tree file is ~353 KB; git packs it to ~99 KB.

It goes in **`tests/data/`, not `tests/fixtures/`**. `tests/fixtures/` is closed: its own sweep
asserts `every_file == files` over `*/raw.json` and tells a maintainer to "move them out of the
fixture tree".

Three sweeps were checked against the new file rather than assumed to ignore it, and it needs a
carve-out from none of them. `tests/test_fixture_name_neutrality.py`'s identity sweep walks
`_TESTS_DIR.rglob("*.py")`, so a `.txt` is outside it. `tests/test_no_leaked_files.py`'s
`_GATE_PATHSPEC` is deliberately EMPTY -- every tracked file is swept for absolute home paths --
so the corpus IS grepped by it, and measured, it is clean: pure ASCII, every line matching
`^[a-z]+$`, zero `/Users/` or `/home/` shapes, no slashes at all. And `tests/fixtures/`'s
`every_file == files` assertion does not reach `tests/data/`.

The honest limit, in this repo's own words about frozen fixtures: **a frozen corpus buys a
one-time validation, not drift detection.** It certifies this implementation against Porter at
capture time. It says nothing about whether Porter is the right stemmer for job ads.

### D8 -- What the stemmer deliberately does not touch

`core/relevance.py`'s `relevance_keep`/`relevance_drop` keep exact substring matching. Those are
a user-specified ingest gate applied before dedup and before any LLM call; widening the match
silently changes which leads are discarded, which is the failure `672ad2a` already cost this
project once.

`rank()`'s haystack also stays `best_for + category + title` -- not `body`. Matching into bodies
would let a long entry out-score a precise one on volume alone. Both are separable changes, and
neither is needed for the property #165 asks for.

### D9 -- `cited_by_gate` splits in two, because this change makes it ambiguous

Found while planning, not while designing, and it is the sharpest thing in this change.

`EvidenceKind.cited_by_gate` (#164) means "the CV fabrication gate READS this corpus", and
`test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` derives the true set by
grepping `cv/engine.py` for `read_evidence("<kind>")`. Both were written when *the engine reads
it* and *the gate cites it* were the same statement.

D1 makes them different statements on purpose: the engine reads `skills`, and the gate licenses
nothing from them. So the existing test goes red, and the two ways out are both wrong. Flipping
`skills.cited_by_gate = True` makes `doctor` tell a user that verifying a skill made it "citable
by the CV fabrication gate" -- false, and false in the reassuring direction #164 explicitly names
as the worst one ("a user reads it as 'my skills are feeding my CVs' and stops looking"). Leaving
it False leaves a red test asserting something that is no longer true.

So the flag splits. `read_by_composer` says the corpus reaches the prompt; `cited_by_gate` says
the gate licenses its content. `experience` is both, `skills` is the first only, `stories` is
neither.

The derivation has to change with it, and this is the part worth getting right. `read_by_composer`
keeps #164's source grep, which still answers exactly the question it asks. `cited_by_gate` cannot
be answered by grepping anything -- citability is decided by `bundle_sources`, which walks
`bundle["entries"]` and knows nothing about kinds. It is derived by EXECUTION instead: build a
bundle carrying one entry per kind, each with a distinct sentinel digit, run `bundle_sources`, and
ask which sentinels it licensed. That oracle cannot go stale, because it IS the mechanism.

`classify_store`'s message gains a third arm for the read-but-not-cited case, since "citable" and
"nothing reads this corpus yet" are now both false for `skills`. Its `blocks=("cv",)` stays keyed
on `cited_by_gate`: after D5 an unreadable skills corpus no longer blocks `cv`, so widening that
to `read_by_composer` would over-claim in the other direction.

### D10 -- `read_experience_entries` is retired here, because #164 said it expires here

Its own Protocol docstring: *"EXPIRES AT #165. It predates the kind registry and survives only
because `cv/engine.py` still calls it; #165 rewrites that caller to read the corpora it composes
from by kind. When it does, DELETE this member rather than inheriting it."* D1 is that rewrite.

A Protocol member is a REQUIRED member, so keeping it means every future store implements a second
spelling of a call it already implements, for a caller that no longer exists. Its conformance row
and its hand-listed test literals go with it -- eight test files, which is why the plan gives it
its own task rather than folding it into the engine change.

Keeping the engine on the delegate and only ADDING `read_evidence("skills")` was considered and
rejected: it leaves one kind read through a legacy delegate and the other through the registry,
and it leaves `protocols.py:736` asserting something false.

### D11 -- the ADVISORY audit is handed the bundle WITHOUT the framing section

Found by plan review, and it is the most consequential thing this design missed. Neither the spec
nor the plan mentioned `cv/audit.py` at all.

`cv/engine.py:653` calls `run_audit(backend, cv_text, bundle_text)` with the SAME text built for the
composer, and `build_audit_prompt` opens "SOURCE BUNDLE is the ONLY truth". So emitting skills into
`bundle_text` silently widens what the #60 advisory audit treats as support. A CV claim resting on a
skills line alone is judged `unsupported` today -- which, with `cv.require_signoff` shipped True,
WITHHOLDS the send-ready `tailored_cv` pointer until a human signs off. After D1 it would be judged
`supported` and served unsigned.

D3 says a claim resting on skills alone is exactly what must not happen. So D1, unmitigated,
disarms the only layer that could catch the failure D3 defines. That is the wrong direction on the
one gate that exists for qualitative fabrication.

**The audit keeps calling `render_bundle`, unchanged. The composer calls a new
`render_composer_bundle`.** The framing section and the derived negative below are emitted by that
one, and by nothing else. The audit call site is not edited at all, which is the strongest available
form of "it sees exactly the text it sees today".

**Rejected: a keyword-only `include_framing=True` on `render_bundle`.** That was revision 2's
design, and plan review round 2 falsified it twice over. It defaults toward WIDENING -- a third
caller who forgets the kwarg gets the framing and treats a skills line as support, re-arming this
exact failure -- and, measured, it did not even work: `_DERIVED_NEGATIVE` contains the literal
string `SKILLS INVENTORY` and lands in `bundle["negatives"]`, which rendered under BOTH spellings.
The auditor was handed a sentence naming a permitted source it could not see, so the re-widening
arrived as PROSE, the very route this decision rejects below. Two separate reviewers measured it.
A second function has no default to get wrong.

**Rejected: adding a framing rule to `build_audit_prompt` instead.** It leaves the auditor reasoning
about a distinction stated in prose, when the same outcome is available by not showing it the
section at all. Not showing it is exact; a prompt instruction is a request.

The residual is a possible FALSE `unsupported`: the composer legitimately takes VOCABULARY from a
skills entry (that is what framing is for), and the auditor never sees that vocabulary. It fails in
the safe direction -- a sign-off hold a human clears, never an unsigned CV -- and the audit is
advisory and never blocks rendering either way.

## Behaviour changes a user will notice

1. **Ranking order changes for everyone.** Today's substring match relates `java`/`javascript`
   and `scala`/`scalability`; stemming does not. Measured, those are false positives -- but they
   are somebody's current ranking.
2. **Ranking never changes INCLUSION.** The full verified set is always emitted; JD keywords
   order and emphasise, never exclude. So a ranking change cannot lose evidence.
3. **Changed order changes the `[id]` codes**, because `assign_codes` runs after `rank`:
   ```
   jd_keywords=[]            -> [('EX1','documentation'), ('EX2','delivery')]
   jd_keywords=['delivery']  -> [('EX1','delivery'), ('EX2','documentation')]
   ```
   Ids have never been stable across runs; this is not new, but any frozen fixture carrying ids
   moves.

## The freeze diff is the risk in this change

`FROZEN_BUNDLE_TEXT` must be re-captured for D1 and D7. `_entry_block`'s docstring is explicit
that this is how a widening launders through green: re-capture the literal and both
`test_the_rendered_prompt_has_not_drifted` and `test_the_allowlist_still_matches_the_frozen_prompt`
move with the mutant and stay green. *"Nothing here can tell a deliberate prompt change from a
silent allowlist widening; a human reading the freeze diff is what still has to."*

Two mitigations, because a human reading a diff is a control that fails quietly:

1. The re-freeze is its OWN commit, touching only the literal, so the diff is readable in
   isolation rather than buried in a feature commit.
2. `test_bundle_sources_sentinels_hold_independent_of_the_frozen_literal` -- which compares
   against no literal and so cannot be brought back into sync by re-freezing -- gains skills
   sentinels asserting that skills digits appear in NO pool. That is the D1 claim pinned by
   something a re-capture cannot move.

## Tests

Behaviour, not coverage. Each names the defect it would catch.

**Bundle**
- A skills entry's digits appear in neither `nums` nor `baseline` (sentinel-keyed, no frozen
  literal). Mutant: moving the skills block into `_entry_block`.
- An empty inventory emits no `SKILLS INVENTORY` header at all. Mutant: emitting an empty header.
- The section renders after the last entry and before NEGATIVE CONSTRAINTS. Mutant: reordering.
- The pre-#174 oracle still agrees with `bundle_sources` on a 3-entry bundle carrying skills.
- The derived cross-reference appears only when the inventory is non-empty, and configured
  `cv.negatives` survive alongside it.

**Stemmer**
- Full-corpus equality against Porter's published vocabulary (23,531 rows).
- The property #165 asks for, at the `rank()` level rather than the stemmer's: an entry spelling
  the requirement `documentation` ranks above unrelated entries when the ad says `documenting`.
  Built so the right entry must BEAT scoring competitors -- the mistake the first draft of the
  measurement made.
- The must-not-conflate pairs, including the two the current substring match gets wrong.

**Engine**
- An unreadable Skills Inventory composes without the section, warns by name, and marks the
  result -- rather than raising. Mutant: removing the catch, which must fail this test.
- Skills are read `verified_only=True`: an `_inbox/` skill never reaches the bundle. Measured
  today: `verified_only=True` returns only the stamped entry.
- A skills read is not attempted when composition is already refused (`skipped-config`,
  `skipped-stale`), so a broken corpus costs nothing on a lead that was never going to compose.

**Doctor**
- `classify_negatives_vs_skills` reports a negative naming a held skill, and abstains when the
  inventory is empty (empty-config-abstains).
- The `classify_store` message that currently says skills/stories claim no citability
  "until #165 lands" no longer says it.

**Prompt**
- The skills rule is present in `_RULES`, and the existing
  `test_the_prompt_names_exactly_the_phrases_the_gate_enforces` still passes.

## Docs that become false and must change

- `core/protocols.py:145` -- "`skills`/`stories` default to False until #165".
- `core/doctor.py:classify_store` -- the "until #165 lands" sentence.
- `docs/ARCHITECTURE.md` (bundle sections, the evidence paragraph), `docs/CONFIGURATION.md`
  (`cv.negatives`), `sluice.yaml.example`.
- `.rulesync/rules/CLAUDE.md` -- the CV gate paragraph, regenerated after editing. Never edit
  `CLAUDE.md` directly.

## Out of scope, filed separately

- **#194** (#165 proposal 2b), a mechanical named-technology hard gate. It overlaps #168's item 4
  (containment-checking an emitted SKILLS section) and wants one spec with it, not two designs
  for one gate.
- **#195** (#165 proposal 3), STAR Stories to interview prep briefs and cover note generation.
  Two new command surfaces with no existing flow to change, and no gate that fits free prose.
- **#196** (#165 proposal 4), evidence-aware triage. #165 itself calls this a follow-up.
- **#168** is UNBLOCKED by this change: it depends on "a canonical skills location, and reading it
  into the bundle", which D1 delivers.

## Revision history

- **r1** -- first draft.
- **r2** -- three corrections from executing the claims rather than reading them.
  - The ranking measurement in r1 demonstrated nothing: both entries scored 0, so stable sort
    preserved input order. Rebuilt so the right entry must beat scoring competitors.
  - r1 justified D2's placement partly on the zero-entry case. False: that disagreement is
    pre-existing, has no connection to skills, and the frozen literal (3 entries) never reaches
    it. D2 now rests on the 3-entry measurement alone.
  - r1 planned to hand-roll a suffix stemmer. Measured against Porter's corpus it was
    unprincipled (`deployment` to `deploym`, `deployments` to `deplo`); replaced with Porter,
    certified at 100.0000%.
- **r3** -- fixture sizing was an assumption; replaced with a 42-mutant study. A random sample of
  any affordable size leaves mutants alive, because several rules are witnessed by one word in
  23,531. Full corpus, in `tests/data/` -- `tests/fixtures/` is closed by its own sweep.
- **r4** -- self-review. Two claims were asserted rather than checked, and one was wrong.
  - "free for any purpose" was applied to the test vocabulary. Porter's page grants that to the
    algorithm ENCODINGS and states nothing about `voc.txt`/`output.txt`. Now quoted exactly, with
    the inference labelled as one and a fallback named.
  - The claim that a skills read is never attempted on an already-refused lead was checked, not
    assumed: `skipped-selection`/`skipped-needs-signoff`/`skipped-stale`/`skipped-config` all
    return at `cv/engine.py:187-240`, and the bundle build is at :286. True by placement, which
    is exactly why the test guards the placement.
- **r5** -- planning found two requirements this design had missed, both of them consequences of
  D1 that #164 had already written down and this spec had not read closely enough. Added as D9
  (`cited_by_gate` splits, and `cited_by_gate` must be derived by execution rather than by
  grepping a source file) and D10 (`read_experience_entries` retires here, by #164's own
  instruction). D9 is the one to review hardest: it is the only place this change could quietly
  tell a user something false about what the fabrication gate reads.
- **r6** -- five-reviewer `/review-plan` round: 49 findings, 0 Critical, 21 High. Eight were raised
  independently by two or more reviewers, which is the strongest signal the round produced. The
  design-level ones are recorded above as D11 (the advisory audit, missed entirely) and here:
  - The derived negative in D6 omitted the BASELINE CV, contradicting D3's own prompt rule and
    instructing the composer to drop every technology named only in the user's real CV.
  - D5's catch was specified as `except OSError`, but a non-UTF-8 entry raises `UnicodeDecodeError`,
    a `ValueError`. It would escape `run_one` and make `run_batch` record `error` for EVERY lead --
    the exact outcome D5 exists to prevent. `Vault.preflight` shipped this same mistake and now
    catches `(OSError, ValueError)`; this follows it.
  - The plan's proposed oracle test for D2 was inert: it fed `_oracle` the output of
    `render_bundle`, the self-certifying spelling `_oracle`'s own docstring forbids. Measured by a
    reviewer: 3 of 3 co-variant `_entry_block` deletion mutants survive it. Deleted rather than
    repaired -- re-freezing the literal with skills present (D-freeze) makes the EXISTING test cover
    the case for free.
  - `CvResult.skills_unreadable` had no reader, which is the "computed and discarded" defect #167
    opened over, in the docstring of the very dataclass it joins. It now mirrors `dossier_failed`'s
    reader set.
- **r7** -- second five-reviewer round: 44 findings, 0 Critical, 15 High. **All four cross-cutting
  High clusters were defects in r6's own fixes**, which is this repo's documented escalation
  pattern rather than a surprise. The design-level one is recorded in D11 above: the
  `include_framing` boolean both defaulted toward widening AND failed to achieve what it was for,
  because the derived negative names the section by name. `render_composer_bundle` replaces it, and
  the derived negative moves into that function so it cannot reach the auditor at all.
