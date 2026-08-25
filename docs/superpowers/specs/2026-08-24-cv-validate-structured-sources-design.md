# Hand the fabrication gate its true source set, instead of re-parsing the bundle text

Issue: #174. Prerequisite for restoring #164's MCP write tool.

Two `/review-plan` rounds, five specialist reviewers each. Corrections are marked inline as
`[r-...]` and listed in the revision history. Round 1 falsified three claims in the first draft;
round 2 found that **most of round 1's own fixes were wrong** — including two proposed tests that
were inert as specified. That is this repo's documented pattern: review rounds escalate rather
than converge, and the new material is where the defects are.

## Goal

`cv/validate.py` recovers the citable `[id]` codes and the numbers each one licenses by
re-parsing the rendered bundle TEXT (`_bundle_ids_and_nums`, `validate.py:52`). The parse has
therefore always had the authority to decide what an id IS, and any line of user free text
spliced into that bundle can exercise it. Line 66 is an assignment rather than a union, so a
free-text line naming an id that already appeared REBINDS that id's permitted numbers.

Measured on the shipped function. `POISON` is a two-entry bundle whose SECOND entry's body is
the single line `[AL1] fabricated 4200 units`; `CLEAN` is the same bundle with that second entry
absent, which is why it shows one id and not two `[r5]`:

```
CLEAN  nums: {'AL1': {'12'}}          POISON nums: {'AL1': {'4200'}, 'BE1': {'7'}}
fabricated 4200 permitted?  True   []
genuine 12 survives?        False  ["INVENTED METRIC ['12'] not in ['AL1']: - Delivered 12 units"]
```

Both directions go wrong at once: a WORK bullet carrying the fabricated figure clears the HARD
gate with zero violations, and the entry's real metric is reported INVENTED if it is used.

The bound is documented at `validate.py:32-37` and pinned by two characterisation tests. That was
a reasonable trade while an entry body could only be hand-typed by the person the CV is about.
#164 gives entry bodies a non-human author, and deferred its write tool out of that PR
specifically because of this.

### A second live instance, in the baseline

Measured while scoping this, and not named in #174. `_bundle_ids_and_nums` accumulates the
baseline pool only while `not seen_id`, so an `[XX9]`-shaped line anywhere in the BASELINE CV
text mints a fully citable entry:

```
baseline = "Base has 777.\n[ZZ9] stray line with 4200"
  -> ids: ['AL1', 'ZZ9']   nums: {'AL1': {'12'}, 'ZZ9': {'4200'}}
  -> 'ZZ9' citable? True
```

A bullet `- Shipped 4200 units [ZZ9]` is then gate-clean: a fabricated figure under a
fabricated citation, both sourced from a stray line of the user's own baseline. Same class,
different input surface, closed by the same change at no extra cost.

## Decisions taken (and the options rejected)

**D1 — `render_bundle` and the source derivation share ONE per-entry block builder.**
The alternative considered first was a `bundle_sources` that names `company`/`title`/
`metrics`/`body` itself, guarded by a sentinel-number test asserting it matches what
`render_bundle` emits in both directions. That test is real, but it guards a claim two
functions make separately — the drift shape this repo keeps being bitten by, and the standing
rule is to REMOVE the drift surface rather than test it. So `_entry_block(entry) -> list[str]`
becomes the single definition of what an entry contributes to the prompt; `render_bundle`
joins the blocks, and `bundle_sources` harvests `\d+` from the same block with the `[ID]`
token sliced off by LENGTH (structurally, from the known id — never matched out of the text).

**The licensing rule is scoped to the block, not to what the model saw `[r-arc-001]`.** Revision
1 justified D1 with "a field added to the emitted line is licensed automatically, which is
correct: the model was shown it". That is false, and this design's own contents falsify it: the
NEGATIVE CONSTRAINTS block is shown to the model and is deliberately not citable (#31). The rule
that IS true by construction is narrower — **every line `_entry_block` returns is a source for
that entry, and nothing else is a source for it**. The distinction is load-bearing rather than
pedantic: a reviewer prototyped this design, confirmed `render_bundle`'s output byte-identical,
then added one plausible presentational line to the block — a per-entry `do NOT claim 3 direct
reports / 250 users` caution — and measured `AL1: ['12'] -> ['12','250','3']`. Every entry
widened, and `test_negatives_block_does_not_widen_the_last_entrys_allowlist` does not fire. So
the rule goes in `_entry_block`'s own docstring, and Testing's frozen-text pair enforces it — see
D10, which replaces the output-shape pin revision 2 proposed for this and which measured inert.

**`_entry_block` does NOT own the inter-entry blank line `[r-rev-006]`.** `render_bundle`
appends it (`bundle.py:62`), and it stays there: a blank line contributes no digits, so putting
it inside the block would be presentation leaking into the source definition for no gain. Stated
because it is otherwise ambiguous, and because it is the exact shape of the silent-prompt-change
risk Testing's first frozen-text assertion exists for. D8 applies the same rule to the baseline.

**The cost of D1, which revision 1 missed `[r-tst-002]`.** Sharing the builder makes DRIFT
between the prompt and the allowlist impossible. It makes CO-VARIANT LOSS invisible: delete
`title` or `company` from `_entry_block` and the prompt and the allowlist lose it together, so
they still agree and no render-vs-sources equivalence test can ever see it. Measured: that mutant
is killed by no existing test and by no test revision 1 proposed — 24 transcribed scenarios, zero
verdict changes. The harm is change 2 below in reverse: a `title` of `Engineer, 24 teams` yields
`nums: []`, and `- Led 24 teams [ES1]` is reported INVENTED. This is why Testing's frozen-text
pair is not optional: revision 1 declined an oracle outright, and revision 2 specified one that
could not fire (D10).

**D2 — `validate`'s second parameter changes MEANING, not gains a sibling.** `bundle_text` is
consumed on exactly one line of `validate` today, so there is nothing left for it to do once
the sources arrive structurally. Keeping it as `validate(cv_text, bundle_text, *,
sources=None)` with a text fallback was rejected outright: an optional structured argument
means a caller that forgets it silently keeps the defect, which is the fail-open direction on
a fabrication gate.

Because the parameter's TYPE changes while its POSITION does not, a stale caller passing text
must fail loudly rather than obscurely — `validate` raises `TypeError` naming `bundle_sources`
when handed anything but a `BundleSources`. Without it, a string reaches `sources.nums` and
raises `AttributeError` from inside the gate, which reads as a gate bug rather than a call-site
one.

**That message carries the argument's TYPE and NO PART OF ITS VALUE `[r-neu-002, r2-neu-001]`.**
The stale argument is `bundle_text`: the user's baseline CV verbatim plus every entry's company,
title, metrics and body. `cv/engine.py:789` logs a failed `run_one` as
`_log.warning("cv run failed for %s: %s", note.ref, e)`, so a message embedding the argument
writes the user's entire CV source corpus into a log file.

Revision 2 stated this as "carry `type(sources).__name__`, never `repr(sources)`", which bans one
SPELLING rather than stating a property. Measured: `f"got {sources}"` contains no `repr`,
satisfies that rule as written, and leaks identically — and it is not only the stale-text case,
because a `NamedTuple`'s `str()` IS its `repr()`:

```
str == repr for a NamedTuple?  True
f'got {sources}'    -> got BASELINE CV: <the whole baseline>
'got %s' % sources  -> got BASELINE CV: <the whole baseline>
```

So the rule binds the VALUE and both types: the message may name `type(sources).__name__` and
must interpolate nothing else. The witness mutant is the plain `{sources}` spelling, NOT the
`!r` one revision 2 named — that one was already dead. The same rule binds D4's `ValueError`:
name the duplicate id (a three-character code), never the entry.

**The engine binds `sources` as a local ADJACENT to `bundle_text` `[r2-arc-001]`.** Revision 1
inlined it at the call site (`_validate(cv_text, _bundle.bundle_sources(b), ...)`) to stop two
locals from one `b` desyncing. That over-corrected: `engine.py:346` is inside `for _ in
range(2)`, AFTER the `compose` call at `:320`, so D4's duplicate-id `ValueError` — justified
entirely by #164's non-human author — would cost a full LLM compose per lead before firing on a
fault knowable at `:288`. Recomputation cost is nil; ORDERING is the issue. `bundle_text` is
already a local at `:288`, so binding `sources` on the very next line puts both derivations of
`b` adjacent — a HARDER desync to introduce than one buried 58 lines away, which is what
revision 1 was actually reaching for.

**D3 — `BundleSources` carries `nums` and `baseline`, and `ids` is derived `[r-rev-002]`.**
Today `ids` and `nums` are written on the same line and so have identical key sets by
construction; carrying both as FIELDS would re-create, one level up, exactly the redundancy #174
is about — two structures that can disagree about what an id is. So `nums: dict[str,
frozenset[str]]` and `baseline: frozenset[str]` are the only fields, and `ids` is an `@property`
returning `self.nums.keys()`. The citation check spells itself `c not in sources.ids`, so the
property has a real reader; revision 1 contradicted itself by also specifying `c not in
sources.nums`, which would have shipped `ids` dead.

**D4 — a duplicate id raises.** `assign_codes` sequences per prefix and `{**e, "id": ...}`
overwrites any caller-supplied id, so `build_bundle` cannot produce one. The guard earns its two
lines not because `build_bundle` might regress but because **`bundle_sources` accepts an untyped
`dict`** `[r-rev-009]`, and #164 is about to give bundle contents a non-human author. The failure
it prevents is one entry's allowlist silently replacing another's — #174's own defect shape, one
layer up. `ValueError` naming the id.

**D5 — `bundle_text` stays, and `cv/audit.py` is not migrated.** The engine still builds it for
`compose` (`engine.py:320`) and `run_audit` (`engine.py:647`). The advisory audit is an LLM
prompt and must show the model exactly what the composer saw, so it keeps taking text; it is
not an oversight to be tidied up later. Confirmed that nothing else parses `render_bundle`'s
output: `compose.py:94-108` and `audit.py:13` interpolate it only, `mcpserver.py` never touches
the bundle, and `cv/parse.py:446`'s LOCATION claim asserts an ABSENCE, which survives.

**D6 — the cheap narrowing is not the fix.** Changing `nums[cur] = ...` to `|=` stops the
overwrite half but leaves the mint half (a body line `[QQ7] ...` still becomes a citable entry)
and still admits the fabricated figure — it would merely also keep the genuine one. #174 rejects
it, and refusing `_ID_RE`-shaped lines at #164's write path is the same kind of narrowing:
fewer ways in, with the parser's authority to be fooled intact.

**D7 — `validate` takes the sources, not the bundle dict.** Revision 1 rejected
`validate(cv_text, bundle)` on the grounds that it would put "what counts as a source" back
inside the gate. That premise was false `[r-arc-002]`: the gate would CALL `bundle_sources`, so
the definition would still live in `cv/bundle.py`. The real reason to keep the sources as the
parameter is D2's — the gate should be handed a source set it has no power to compute, so that
"validate did not invent this id" is true of the SIGNATURE and not merely of the current body.

**D8 — the baseline shares a block builder too, and it holds NO headers `[r-inv-002,
r2-inv-001, r2-arc-003, r2-rev-004]`.** Revision 1 harvested `bundle["baseline"]` directly while
`render_bundle` emitted a baseline BLOCK, and claimed in its risk register that drift was
"impossible by construction" — true for entries only. Revision 2 added a `_baseline_block`
harvested as `block[1:]`, and three reviewers independently found that WORSE than the problem it
fixed: `render_bundle` emits THREE constant headers (rendered lines 0, 3 and the negatives one),
the baseline region holds two of them, and `[1:]` skips exactly one. Ownership of the second was
never stated. Both mutants are live:

- `_baseline_block` keeps the second header and `[1:]` licenses its future digits into the pool
  `validate.py:171-173` says has no BAD-CITATION backstop — fail-open.
- Delete the header from `_baseline_block` and `[1:]` eats the REAL baseline text, so every
  baseline-sourced PROFILE figure becomes `INVENTED PROFILE METRIC`, the gate blocks, and the
  lead is skipped — fail-closed, but it silently costs leads.

So `_baseline_block(bundle) -> list[str]` returns **only source lines and no header at all**, and
is harvested WHOLE with no slice. `render_bundle` owns all three headers. That also restores
consistency with this spec's own `[r-rev-006]` rule that a builder does not own presentation —
revision 2 broke its own rule one decision later.

**D9 — the `[ID]` slice is an offset-0 contract, stated `[r-inv-003]`.** `bundle_sources` strips
the id token by LENGTH (`len(id) + 2`), which is correct only while `_entry_block` puts `[{id}]`
first on line 0 with no leading whitespace. Only the leak direction is guarded today
(`tests/test_cv_validate.py:68`). The frozen-text pair in Testing covers the other direction —
revision 2 credited an output-shape pin that does not, see D10.

**D10 — the detectors are frozen TEXT, not derived from the code under test `[r2-tst-001..003]`.**
Revision 2 specified three new tests, and round 2 measured all three INERT. Each derived its
reference from the thing it was testing: the equivalence oracle was fed `render_bundle(b)`, the
`best_for` equality computed `emitted` from `render_bundle(b)`, and the shape pin checked line
count plus offset-0 — which a caution folded into the EXISTING line 0 passes while widening every
allowlist. Measured, on a prototype of this design:

```
mutant           oracle(render_bundle(b))       oracle(FROZEN_TEXT)
drop_title       HOLDS -> SURVIVES              FAILS -> KILLED
drop_company     HOLDS -> SURVIVES              FAILS -> KILLED
emit_best_for    HOLDS -> SURVIVES              FAILS -> KILLED
```

This is the repo's own named failure — assert a mechanism, then write a check that cannot
falsify it — and revision 2 walked into it three times in one section while citing
`tests/test_cv_validate.py:327-366` as the precedent. That precedent feeds its transcribed
oracle a TEST-OWNED input; revision 2 copied the transcription and dropped the half that makes it
a detector. Testing below is rewritten around a single frozen text literal.

## The change

### `sluice/cv/bundle.py`

```python
class BundleSources(NamedTuple):
    nums: dict[str, frozenset[str]]   # id -> the numbers that id licenses
    baseline: frozenset[str]          # PROFILE-only pool

    @property
    def ids(self): ...                # self.nums.keys(); derived, so it cannot drift

def _entry_block(entry) -> list[str]     # the ONE definition of an entry's emitted lines
def _baseline_block(bundle) -> list[str] # source lines ONLY, no header, no slice (D8)
def bundle_sources(bundle: dict) -> BundleSources
def render_bundle(bundle: dict) -> str   # now joins both builders' output
```

`bundle["negatives"]` is read by neither builder — the exclusion #31 established by where the
negatives landed in the text is now a property of the derivation.

### `sluice/cv/validate.py`

`validate(cv_text, sources, employers=None, fabrication_decoys=None)`, plus D2's `TypeError`
guard. `_bundle_ids_and_nums`, `_ID_RE` and `_SECTION_RE` are deleted — with the parse gone, so
is the parser's authority to invent an id. `_CITE_RE` stays: it is the PROFILE strip and mirrors
`render._CITE_RE`. **Its comment (`validate.py:46-48`) explains itself by contrast with `_ID_RE`
and must be rewritten, not left dangling `[r-arc-003]`.** The module stays pure and deterministic
and gains one intra-package import (`bundle.py` imports nothing from `validate.py`, so no cycle).

`section_spans` is untouched.

### `sluice/cv/engine.py`

`sources = _bundle.bundle_sources(b)` is bound at `:289`, on the line directly after
`bundle_text = _bundle.render_bundle(b)` and BEFORE the `for _ in range(2)` retry loop, so D4's
duplicate-id guard fires ahead of any compose spend and the two derivations of `b` sit adjacent
(D2). `engine.py:346` then passes `sources` instead of `bundle_text`. The retry-once-then-skip
contract, the retained-hard-clean-draft rebind, and the prompt the model sees are all unchanged.

Verified that the guards fail CLOSED: `engine.py:346` sits under the outer `try` at `:283` whose
handler re-raises, so a stale caller lands as `CvResult("error")` in `run_batch`'s commented
per-lead isolation — never a render.

## What changes behaviour

Verified by execution.

1. **An `[XX9]`-shaped line in an entry body or in the baseline is now just text.** It can
   neither mint an id nor rebind one. On #174's own poisoned bundle the derivation returns
   `{'AL1': {'12'}, 'BE1': {'1', '4200', '7'}}`: AL1 keeps its genuine metric and never sees
   4200.

2. **A `=== X ===` line inside an entry body no longer strands that entry's later numbers.**
   A false-positive removal rather than a loosened gate. Measured today:

   ```
   body = "Highlights\n=== Detail ===\nCut latency to 250 ms"
     -> nums: {'AL1': {'12'}}          (250 dropped)
     -> "- Cut latency to 250 ms [AL1]"
        -> ["INVENTED METRIC ['250'] not in ['AL1']: ..."]
   ```

   The user's own verified figure, which `render_bundle` showed the model in full, is reported
   as fabricated — costing the single retry and potentially the lead. `_SECTION_RE` exists only
   to keep the negatives block off the last entry (#31), and that need does not survive a
   structural derivation.

3. **Two further widenings, both in the PROFILE pool `[r-inv-001, r2-inv-002, r2-rev-003]`.**
   Revision 1 claimed change 2 was "the one WIDENING"; revision 2 said three more. Both were
   wrong, and revision 2's table was wrong for an instructive reason: it measured the `baseline`
   variable, when the check consults `profile_permitted = baseline.union(*nums.values())`
   (`validate.py:155`). Re-measured against the right variable:

   | baseline input | PROFILE pool today | proposed | genuinely gained |
   | --- | --- | --- | --- |
   | `=== 2020 Highlights ===` line | `['12','42']` | `['12','2020','42']` | `2020` |
   | digits after an id-shaped line | `['12','42','8888']` | `+ '9'` | `9` (NOT `8888`) |
   | a `[ZZ9]` token's own digit | `['12','42']` | `+ '9'` | `9` |

   `8888` is ALREADY profile-permitted today, via the very `ZZ9` entry the mint creates — so rows
   2 and 3 collapse to one figure, a citation token's own digit. Both survivors are figures from
   `bundle["baseline"]`, which `render_bundle` labels *authoritative for dates/employers/certs*
   and which the profile pool exists to draw on (#30: a profile is an aggregate summary,
   deliberately broader than a bullet). Today's exclusions are accidents of the text parse. They
   are defensible as fixes — but `validate.py:171-173` states the PROFILE has no BAD-CITATION
   backstop, so they are pinned by value rather than left to be discovered.

4. **A fourth delta, in the NARROWING direction, and it closes a third live hole
   `[r2-inv-002, r2-arc-004]`.** Revision 1 and 2 both asserted "negatives stay out of every
   allowlist" as though it were already true. It is not, at ZERO entries — reachable per
   `core/vault.py:1225-1229`, an install before the user has written an Experience Library entry.
   With no entries `seen_id` never sets, so the negatives block falls through to the baseline arm:

   ```
   ZERO ENTRIES     PROFILE pool today ['500']  ->  proposed []
   a do-not-say figure is profile-permitted TODAY: True     after: False
   ```

   A PROFILE citing a do-not-say figure is gate-clean today and a violation afterwards. So this
   change closes THREE live fabrication-gate holes, not one: #174's entry-body rebind, the
   baseline mint, and this. Negatives stay out by construction only once the derivation never
   reads them.

### Accepted residuals

A citation-shaped token inside an entry's own body contributes its digits to that entry — BE1's
set gains `'1'` from the literal `AL1` in its body text above. And its **baseline twin**
`[r-tst-005]`: the baseline harvest has no `[ID]` slice at all, so the `9` of a `[ZZ9]` token in
the baseline becomes a PROFILE-permitted figure (row 3 above).

Both left as-is deliberately. The only close is stripping citation-shaped tokens before
harvesting, which needs a second regex that must agree with `render.strip_citations` — a new
drift surface, added to protect against single digits the user wrote in their own source
material and that the model was shown. Unlike revision 1, this is recorded in `tests/` as well as
here, by a characterisation test carrying the same "expected to go RED the day someone closes
this" comment the two retiring tests carry — so the retirement is a swap and not a net loss.

## Existing tests this changes

Revision 1 said "two tests change their assertions and nothing else in those files moves". That
is false for COVERAGE: six tests in `tests/test_cv_validate.py` change verdict, and four of them
survive syntactically while
ceasing to be load-bearing, which is how the next person deletes a real guard. Four of the five
reviewers landed on this independently. Per-test verdicts `[r-tst-001, r-rev-001, r-arc-003,
r-inv-004, r-neu-003]`:

| test (`tests/test_cv_validate.py`) | verdict |
| --- | --- |
| `:233 test_an_id_shaped_bracket_in_free_text_is_still_a_citable_id` | FLIP to assert closure. Its comment says it is "expected to go RED the day someone closes the residual" — the signal firing as designed. |
| `:245 test_an_id_shaped_line_in_a_later_body_shadows_the_real_entry` | FLIP to assert closure. Its comment says only "out of scope here" and "pinned so the bound is MEASURED" — a weaker claim than revision 1 attributed to it `[r-rev-003]`. |
| `:200 test_a_setext_underline_in_a_body_does_not_end_the_entry` | Becomes an exact duplicate of `:191` — its only deletion mutant is "drop `body` from `_entry_block`", already killed by six siblings. DELETE, and let change 2's own test carry the property. |
| `:226 test_a_bracket_led_body_lines_numbers_join_the_enclosing_entry` | Same: duplicate of `:191` under the new derivation. DELETE. |
| `:217 test_a_bracket_led_body_line_is_not_a_citable_id` | KEEP, and its comment must NOT say "no deletion mutant" `[r2-rev-001]`. Revision 2 instructed exactly that while ALSO folding the dropped `[ZZ9]` test into it on the grounds that it kills `validate.py:188`. Measured: deleting the BAD-CITATION arm turns it RED, and it is the ONLY test in the repo asserting `BAD CITATION`. That comment would have marked the sole guard on a fabrication-gate arm as inert. |
| `:182 test_negatives_block_does_not_widen_the_last_entrys_allowlist` | KEEP. Revision 2's "no deletion mutant at all" is also false `[r2-rev-002]` — deleting the INVENTED-METRIC arm turns it RED. The true, narrower claim is what the comment says: no mutant SPECIFIC to the negatives exclusion, because re-widening it needs an ADD-shaped mutant, which the witness rule forbids. |

Three comments the change falsifies and which must be rewritten with the code they describe:
`:16-22` (the `BUNDLE` rationale — "validate() had never once been exercised against the text
`render_bundle` actually produces" stops being true once `validate` never sees rendered text),
`:70` (names the deleted `_bundle_ids_and_nums`), and `sluice/cv/validate.py:46-48` (above).

Type-only migrations, enumerated rather than counted `[r-rev-004]`: `tests/test_cv_validate.py`'s
module-level `BUNDLE` (`:31`) and `_bundle()` (`:149`) — every one of the file's 31 `validate(...)`
calls routes through one of those two; five local `bundle_text = render_bundle(build_bundle(...))`
constructions in `tests/test_cv_engine.py`; `tests/test_cv_parse.py::_gate_verdict`; and two
literal `""` call sites in `tests/test_onboard_questions.py` (`:258`, `:259`), which construct no
bundle at all and become an empty `BundleSources`.

## Testing

Split by whether a DELETION mutant exists, because the repo's witness rule forbids ADD-shaped
mutants and a later mutation round must not read a regression guard's survival as inertness
`[r-tst-004]`. New unit tests for `_entry_block`/`bundle_sources` live in
`tests/test_cv_bundle.py`, which is where `render_bundle` is already tested `[r-rev-007]`.

**The frozen-text pair — ONE literal, TWO assertions, and the whole detector for this module.**
Everything revision 2 proposed here was inert (D10). The reference must be a value the code under
test cannot move. So `tests/test_cv_bundle.py` holds a `FROZEN_BUNDLE_TEXT` literal — the exact
output of the PRE-CHANGE `render_bundle` over a fixed multi-entry bundle carrying a distinct
sentinel digit in EVERY entry key, including the two `render_bundle` does not emit — and beside it
the transcribed `_bundle_ids_and_nums`, quoted from a named SHA the way
`tests/test_cv_validate.py:327-366` quotes `git show b831dc9:sluice/cv/validate.py`, with any
transcription deviation stated. Then:

```
assert render_bundle(b)  == FROZEN_BUNDLE_TEXT          # the PROMPT did not drift
assert bundle_sources(b) == _oracle(FROZEN_BUNDLE_TEXT) # the ALLOWLIST still matches the prompt
```

The first assertion makes revision 1's one-off byte-identity check permanent, so presentation
drift that changes no digits (reordering fields, `metrics=` → `Metrics:`) is caught forever rather
than once. The second is the co-variant detector. Together they kill every mutant in this module,
measured on a prototype: `drop_title`, `drop_company`, `drop_metrics`, `emit_best_for`, a caution
folded into line 0, and a broken `[ID]` slice — the last via the second assertion alone, since it
does not touch `render_bundle`. Obtain the SHA with `git log --oneline -1 -- sluice/cv/validate.py`
before the deleting commit lands.

The test states that the corpus is CLEAN-only, because on poisoned input the two are deliberately
UNEQUAL — that inequality is the entire fix, and a future reader must not "repair" it by widening
the corpus.

**This subsumes two of revision 2's proposed tests.** The `_entry_block` output-shape pin is
DROPPED: measured, it caught only the appended-line spelling and passed a caution folded into line
0, and every mutant it was credited with is killed by the pair above. Shipping it would add a test
whose mutants are all pre-killed, which the repo's own rule calls inert. The `best_for`/`category`
test is dropped as a separate case for the same reason — its sentinels are already in the frozen
corpus, so the pair asserts the exclusion by equality rather than by a negative that passes
vacuously.

**Also mutation-witnessed** (a named deletion mutant in `sluice/`, no sibling killing it):

- **`TypeError` on a text caller** (`tests/test_cv_validate.py`) — asserting the MESSAGE names
  `bundle_sources`, and separately that it does NOT contain the bundle text. The witness mutant is
  the plain `f"got {sources}"` spelling (D2) — the `!r` one is already dead, so witnessing against
  it would prove nothing. A guard raising the same exception type as the path behind it cannot be
  witnessed on the type.
- **`ValueError` on a duplicate id** (`tests/test_cv_bundle.py`) — asserting the message names
  the id and not the entry.
- **The zero-entry narrowing** (`tests/test_cv_validate.py`) — change 4. A do-not-say figure in a
  PROFILE over a bundle with no entries is gate-clean today and a violation after; the deletion
  mutant is the derivation reading `bundle["negatives"]`.

**Regression guards against re-introduction** (no deletion mutant exists; each says so in its own
comment):

- The poisoned body, end to end through `cv/engine.py`'s real gate (`tests/test_cv_engine.py`),
  so #174's measured harm is pinned where the user experiences it.
- The poisoned baseline (`tests/test_cv_validate.py`).
- The `=== X ===` body line keeps its numbers — change 2 (`tests/test_cv_validate.py`).
- The two PROFILE widenings — change 3, asserted against `profile_permitted` and not `baseline`
  `[r2-tst-005]`. These belong HERE and not with the witnessed tests: the derivation never reads
  the baseline positionally any more, so re-introducing either exclusion needs an ADD-shaped
  mutant. Revision 2 filed them in the wrong group.
- The accepted residual and its baseline twin, carrying the go-RED comment (see above).

**Dropped from revision 1:** the standalone `[ZZ9]` BAD CITATION test — its mutant is
`validate.py:188`, already killed by `test_a_bracket_led_body_line_is_not_a_citable_id`
`[r-tst-004]`. Folded into that sibling instead.

**No separate one-off migration check.** Revision 1 proposed a throwaway byte-identity probe and
revision 2 kept it; the frozen-text pair above makes it permanent instead, which is strictly
better — `bundle_text` feeds two live LLM prompts, and a check that runs once cannot catch the
second person to touch `_entry_block`. Capturing `FROZEN_BUNDLE_TEXT` from the pre-change
`render_bundle` IS the migration check, performed once and then kept.

**Neutrality — DERIVE the module set, do not extend the hand-list `[r-neu-001, r2-neu-002]`.**
Revision 2 added `test_cv_bundle.py` to `_CV_TEST_MODULES`
(`tests/test_fixture_name_neutrality.py:1315`). That closes the instance and leaves the class:
measured against the real roster object, 13 `test_cv_*.py` modules exist and 6 are listed, and
this would be the SECOND per-instance patch to the same tuple (the first was
`test_slop_phrase_retirement.py` at #181). "Closing a gap class for one instance does not close it
for identical instances" is a standing rule here.

So the set becomes a `glob("test_cv_*.py")` UNION a named tuple for CV-domain modules the
convention does not match (`test_slop_phrase_retirement.py`, `test_renderer_template.py`,
`test_onboard_questions.py` — the last two hold CV-body identities no positional collector
reaches, and the third is migrated by this very change). The sweep must assert its own SCOPE —
that it enumerated a non-empty set of modules — since a glob that matches nothing satisfies every
assertion over it.

Measured blast radius, against `_REVIEWED_FIXTURE_IDENTITIES` itself rather than a grep: exactly
two values go red, and every `test_cv_*.py` module is already clean.

- `Example Alpha` (`tests/test_onboard_questions.py:257`) — a placeholder employer in a probe of
  the employers gate's case-sensitivity. Owner's ruling (2026-08-24): invented. Joins the roster
  as a reviewed identity, the same `Example <Word>` construction as the 59 already there. (The
  roster carries bare `Alpha`, which is why the two-word form did not match.)
- `Example Sans` (`tests/test_renderer_template.py:420`) — a made-up TYPEFACE in a `@font-face`
  fixture, beside genuine font names. Owner's ruling (2026-08-24): invented, and EXEMPTED by name
  rather than rostered, because `_REVIEWED_FIXTURE_IDENTITIES` is documented as being about LEAD
  identities and a font family is not one. Same shape as `CLAUDE.md`'s existing `cairo/pango`
  carve-out from the place-name sweep. The exemption carries that reasoning inline, so the roster
  does not quietly come to mean something wider.

**Witness procedure `[r-tst-006]`:** `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash
sluice tests scripts` first. Mutants go in `sluice/` ONLY, and no test file is edited inside a
witness loop — the checked-hash cache does not cover pytest's own rewritten test bytecode, which
stays timestamp-based. This task edits test files unusually heavily, so the invariant is stated
rather than left implied. Every mutation-witnessed test is run BY NODE ID, confirming no sibling
already kills the mutant.

## Documentation

- `render_bundle`'s docstring says the emit conventions are "a contract with `cv/validate.py`,
  which parses this text back". Rewritten to name `_entry_block`/`_baseline_block` as the shared
  definitions and `bundle_sources` as the sibling consumer.
- `_entry_block`'s own docstring carries D1's scoped licensing rule.
- `cv/validate.py`'s module docstring, the `_ID_RE` comment block (`:24-37`) and the `_CITE_RE`
  comment (`:46-48`) go with the code they describe.
- `docs/ARCHITECTURE.md`'s cv paragraph (`:380-432`) and `.rulesync/rules/CLAUDE.md`'s gate
  paragraph (`:401-437`): one line each, that the gate is handed its source set rather than
  recovering it. Both currently state the gate's OBLIGATION and never how ids are recovered, so
  they are sufficient and no other canonical doc goes stale `[r-arc-003]`. Then `npm run
  rulesync`.
- The four historical spec/plan documents quoting `_bundle_ids_and_nums` stay untouched, per this
  repo's rule that implemented design documents are not maintained.

## Risk register

| Risk | Mitigation |
| --- | --- |
| A caller left on the text signature | `TypeError` at the boundary, named message, asserted; verified to fail closed via `engine.py:283`'s re-raise. One production caller exists. |
| Prompt and allowlist drift apart | Impossible by construction for BOTH halves once D8 lands — one builder each, two consumers, and no slice on either. |
| Prompt and allowlist regress TOGETHER (D1's real cost) | The frozen equivalence oracle. This is the risk revision 1 did not name and left uncovered. |
| A presentational line added to `_entry_block` becomes citable | D1's scoped rule in the docstring + the frozen-text pair, which kills it whether it is appended as a new line or folded into line 0. The output-shape pin revision 2 credited here caught only the first spelling. |
| The PROFILE widenings admit something they should not | All three come from the baseline block the bundle declares authoritative and shows the model; pinned by value in a named test. |
| The user's CV leaks into a log via a guard message | `type(sources).__name__` only; asserted by the `TypeError` test. |
| A retired test leaves a silent coverage hole | Per-test verdict table above; the two deletions are justified as exact duplicates, and the two keeps carry comments stating precisely which mutant they do and do not kill — revision 2 got both of those wrong. |

## Out of scope

- **#164's MCP write tool.** This is its prerequisite, not its delivery. Whether #164's
  write-path refusal of `_ID_RE`-shaped lines is still worth keeping once the parser is gone is
  #164's call — it costs nothing and remains reasonable input hygiene.
- **`cv/audit.py`.** Keeps `bundle_text` by design (D5).
- **`section_spans`, the STYLE/VOICE tier, the retry contract, `cv/parse.py`.** Untouched.
- **The WORK-bullet number regex** (`\d+`). Unchanged, both sides.
- **The implementation ORDER and commit subjects.** This is a design spec; the ordered task list
  (`bundle.py` first, then `validate.py` + `engine.py` + the type-only migrations in one commit
  so the suite is never red between them, then tests, then docs + rulesync) belongs in the
  implementation plan that follows this document `[r-rev-008]`.

## Revision history

- 2026-08-24 — first draft.
- 2026-08-24 — revision 2, after `/review-plan` (5 reviewers, 26 findings, no Critical).
  Substantive corrections: D1's licensing rule was falsified by the negatives block
  (`arc-001`); D1's co-variant-loss cost was unnamed and the frozen oracle wrongly declined
  (`tst-002`); "the one WIDENING" was three more, all in the un-backstopped PROFILE pool
  (`inv-001`); the baseline half kept a drift surface the risk register called impossible
  (`inv-002`); "nothing else in those files moves" was false for coverage, five tests affected
  (`tst-001`/`rev-001`/`arc-003`/`inv-004`/`neu-003` — the review's strongest corroboration);
  a guard message could log the user's whole CV (`neu-002`); `render_bundle`'s byte-identity was
  asserted unmeasured (`rev-006`); `BundleSources.ids` would have shipped dead (`rev-002`); D7's
  rejection premise was false (`arc-002`); `test_cv_bundle.py` sits outside the neutrality
  ratchet (`neu-001`); the `CLEAN` column and two counts did not reproduce (`rev-005`/`rev-004`).
- 2026-08-24 — revision 3, after a second `/review-plan` round (5 reviewers, 27 findings, 10 High,
  no Critical). **Nearly every High was a defect in one of revision 2's own fixes**, which is the
  documented behaviour of review rounds in this repo rather than a surprise.

  Inert detectors, all three of revision 2's new tests (`r2-tst-001..003`, corroborated by
  `r2-arc-002` and `r2-inv-003`): each derived its reference from the code under test. The
  equivalence oracle was fed `render_bundle(b)`, so `drop_title`/`drop_company`/`emit_best_for`
  all SURVIVED, measured; the `best_for` equality computed `emitted` the same way; the shape pin
  passed a caution folded into line 0. Replaced by D10's frozen-text pair, measured to kill all
  six mutants. Two of the three tests are dropped as subsumed.

  Over-corrections: `_baseline_block`'s `block[1:]` was worse than the direct harvest it replaced,
  with two live mutants and an unstated header owner (`r2-inv-001`/`r2-arc-003`/`r2-rev-004`, three
  reviewers); inlining `bundle_sources(b)` moved it inside the retry loop after the LLM spend
  (`r2-arc-001`); the leak rule banned one spelling instead of stating a property of the value,
  and a `NamedTuple`'s `str()` is its `repr()` (`r2-neu-001`).

  Mis-measurements: change 3's table read `baseline` where the check reads `profile_permitted`, so
  it was two widenings and not three (`r2-inv-002`/`r2-rev-003`) — and the re-measurement surfaced
  a FOURTH delta in the narrowing direction, a third live hole this change closes (zero entries
  leak the negatives block into the profile pool today). Two of the six verdict-table rows were
  wrong, one of them instructing a comment that would have marked the repo's ONLY `BAD CITATION`
  guard inert (`r2-rev-001`/`r2-rev-002`). The neutrality fix closed one instance of a class with
  seven open (`r2-neu-002`); the set is now derived, with the owner ruling on the two values that
  go red.

  One round-2 correction did NOT reproduce and was not applied: `engine.py:283` → `:282`. The
  outer `try` is on 283.
