# Unbundled-term check: invention in CV prose — design (#194)

Status: approved in conversation 2026-10-02 (on by default via `cv.term_check`, no allow-list,
negatives subtracted, retention fixed). Revised after a `/review-plan` round; awaits review.

## 1. The problem, and what is already covered

#194 asks that every technology a composed CV names appear in the bundle. #168 shipped the
half closed over the candidate's own data as two HARD rows in `cv/validate.py`:

- **Row 1, `MISATTRIBUTED SKILL`.** A WORK bullet names a skill from the candidate's own
  `Skills:` vocabulary that none of the bullet's cited entries declares.
- **Row 2, `UNSOURCED SKILL`.** A SKILLS-section line whose token sequence appears in no
  source block.

Neither row sees **pure invention in prose**: a PROFILE sentence or WORK bullet naming a term
that appears nowhere in anything the composer was shown. Row 1 looks only for terms already
in the vocabulary; row 2 reads only the SKILLS section. This spec closes scope (b) of the
#168 design (`2026-08-26-skills-containment-design.md` §1.2).

That design rejected a morphology rule for (b) on a measurement. The candidates were
internal capitals, embedded digits and a trailing symbol, compared against the *citable*
pool. It produced 9 non-technologies over 12 synthetic fixtures:

- sentence-final words, from a tokeniser trailing-dot defect since fixed in `_WORD_RE`;
- metric tokens the numeric gate already licensed;
- on a purpose-built CV, `p99` and `120ms`.

Its instruction to a future attempt was to pick the tier first, measure against realistic
CVs, and settle the vocabulary question. §2, §5 and §3.3 answer those in turn. The metric
class is closed by construction in §3.2: any token containing a digit is not a candidate.

## 2. Tier, switch, and the retry contract

### 2.1 STYLE tier

The check joins #167's STYLE tier beside `cv/slop.py`'s stems and the opt-in `cv/voice.py`:

- A finding drives the composer's single retry, and the finding is fed back.
- A finding never bins a lead.
- A finding escalates to a sign-off hold only under the opt-in `cv.style_hold`.

A HARD tier is not viable. Free prose has a real false-positive rate, and a hard refusal of
true content is `cv/parse.py`'s LOCATION failure again.

### 2.2 `cv.term_check: bool = True`

This is one key answering one question: is the check on? It defaults to on, deliberately
unlike `cv.voice_check`. That knob ships off because it spends an LLM call on every lead.
This check is pure and deterministic, and it costs one extra composition only when it
fires, which is the slop stems' cost profile, and those are on by default.

The key exists as an escape for a thin vault. Precision rests on the baseline CV carrying
the user's ordinary vocabulary (§5), and a sparse vault fires more often.

- **Placement.** It is a `CvConfig` field, listed in `sluice.yaml.example` and
  `docs/CONFIGURATION.md`.
- **Validation.** `load_cv_config`'s generic bool-field check
  (`isinstance(getattr(cfg, k), bool) and not isinstance(v, bool)`) already rejects a
  quoted `"false"`, so the new key needs no loader code.

**No allow-list.** For a term the candidate genuinely holds, the remedy is to add evidence
to the vault. An allow-list would let a user permanently license a term the candidate does
not hold. A recurring false positive is a defect in the rule, and the rule is where it gets
fixed.

### 2.3 Retention: keep the draft with fewer findings, not the last one

**Today.** `cv/engine.py`'s retry loop reassigns `best` on every hard-clean attempt. A
hard-clean retry carrying MORE STYLE/VOICE findings therefore replaces a hard-clean first
draft carrying fewer. The retained draft is the last one, not the best one. No lead is lost,
but with `style_hold` off, a retry naming two unbundled terms ships in place of a first draft
naming one. A check whose findings are probable inventions makes that worth fixing. The fix
applies to the whole STYLE tier, because slop and voice share the one `best` triple.

**The rule.** A hard-clean attempt replaces `best` when `best` is None, or when its finding
count is ≤ `best`'s. The finding count is `len(style_msgs) + len(voice_flags)`.

- **A tie keeps the later draft.** That is today's behaviour, and the later draft was
  composed with the earlier findings in front of it.
- **`record.retained(attempt)` moves inside the same condition**, so `run.json`'s
  `retained_attempt` still names the draft actually kept.
- **The loop's early `break`** on a draft with no findings is unchanged.
- **An unmeasured voice check never out-ranks a measured one.** A voice check that raised
  fails open to no flags, which would otherwise count as zero voice findings. A hard-clean
  attempt whose voice check was attempted and raised therefore never replaces a retained
  draft whose voice check ran. With `cv.voice_check` off, nothing changes.
- **Every reader of `best`** (the rebind, `style_hold`, `CvResult.slop`/`voice_flags`) still
  reads one triple, so no reader can describe a draft it did not keep.

The `.rulesync/rules/CLAUDE.md` sentence "RETAINS the last HARD-clean draft" changes to
match (§8).

## 3. The rule

### 3.1 Scope

The check reads the STYLE tier's existing scoped lines: PROFILE prose plus WORK bullets,
from `section_spans`, in the line-ordered union `cv/engine.py` already builds for
`_slop_phrases`.

- **SKILLS lines** stay out. Row 2 owns them, HARD.
- **Company lines, meta lines, CERTIFICATES and EDUCATION** stay out, for the tier's
  existing reason: a complaint about them is answerable only by renaming what they name.

### 3.2 Candidates: a pure `candidates(line) -> list[str]`

Prepare each line in two steps:

1. Strip citations with `_CITE_RE`, render's exact shape, so the check sees what the reader
   sees.
2. Strip the leading bullet marker.

Then tokenise with `_WORD_RE`, the one tokeniser #168's rows use (`cv/validate.py::_tokens` is its
`findall`; this check needs token positions, so it uses `finditer` on the same regex).

A token is a **candidate** when it contains **no digit**, AND at least one of these holds:

- **(i)** it has an uppercase letter after its first character;
- **(ii)** it ends in `#` or `+` and contains a letter (a lone `+` in "sales + support"
  is a token of its own and names nothing);
- **(iii)** it has a leading uppercase letter and is **not sentence-initial**.

A token is **sentence-initial** when it is the first token of the line, or when the nearest
non-space character before it is `.`, `!`, `?` or `:`.

Reasons, one clause each:

- **Any digit excludes the token.** The numeric gate owns digits: `120ms`, `+15`, `#1`,
  `p99`, `Q3` and `92x` are all its territory, already licensed or refused there. This is
  the measured false-positive class, and it is closed by position rather than by a unit
  list. The cost is that digit-bearing names (a cloud service with a number in it) are not
  candidates (§7). In a WORK bullet the numeric gate still refuses an unlicensed digit
  HARD.
- **A sentence-initial capital carries no signal.** It is the ONLY signal that position
  discounts: a sentence-initial `ExampleQuery` still fires on (i).
- **A leading dot is not excluded.** `.Example` fires on (i).
- **A lowercase-only, digit-free token is never a candidate.** Otherwise every English word
  is one (the first row of §5's table).
- **No separate all-caps-at-sentence-start arm.** Any all-caps token of length ≥ 2 already
  satisfies (i). A review measured the separate arm as an equivalent mutant, and it was
  dropped.

### 3.3 The vocabulary: a pure `mention_vocab(bundle) -> frozenset[str]`

A candidate is **reported** unless its case-folded form, or that form with ONE trailing `s`
removed, is in the vocabulary.

`cv/bundle.py::mention_vocab(bundle)` builds the vocabulary from the bundle's STRUCTURE,
never from rendered text (#174's rule). It returns the case-folded `_tokens` of what the
composer is shown as source or framing:

- the baseline (`_baseline_block`);
- each entry's `_entry_block` lines, its heading included, plus `_entry_skills_line`;
- each Skills Inventory note's `_framing_lines`.

It then **subtracts** the case-folded CANDIDATE tokens (§3.2's `candidates`) of each entry in
`bundle["negatives"]`, never a negative's ordinary words.

Excluded:

- **The negatives, by TERM rather than merely by source.** A configured "never claim X"
  reports X even when an inventory note also names it, provided X is written name-shaped in
  the negative itself ("never claim Examplelang", through arm (iii) mid-sentence; an
  all-lowercase "never claim examplelang" subtracts nothing). Only the negative's own
  candidates are subtracted: a free-text negative ("do not overstate platform leadership") would
  otherwise remove `platform` from the vocabulary, and a capitalised `Platform` elsewhere in
  the CV would then be reported on every lead. `cv/terms.py` imports `cv/bundle.py`, so
  `mention_vocab` imports `candidates` lazily.
- **The `=== ... ===` presentation headers.**
- **The job description.** It is the most likely source of an invented term.

**It is NOT a `BundleSources` field.** `BundleSources` is the hard gate's licensing contract
("What the fabrication gate is allowed to treat as a source"), and this pool deliberately
contains the two things that contract excludes: entry headings and the Skills Inventory
framing. A STYLE-only pool sitting on the licensing type would invite a later HARD row to
read it and silently license framing. So:

- `BundleSources`, `bundle_sources` and `validate()` are unchanged.
- The engine calls `mention_vocab(b)` beside `bundle_sources(b)`.
- A guard pins the separation (§6).

**Why the Skills Inventory is in, although no HARD row licenses it.** Row 2 and
`cv/audit.py` ask whether a CLAIM is supported, and #165 D3 still makes a claim resting on
framing alone illegitimate (the audit marks it `unsupported`). This check asks whether the
composer INVENTED a term. A skill the candidate declared is not invented, and flagging it
would make the only actionable answer "delete a true skill". It was also measured: the
citable pool alone flags inventory-only terms (§5).

**The plural fold** adds no word. It only SUPPRESSES a finding whose singular is already the
user's own word, so it cannot widen what the rule reports. It is English-shaped (§7).

### 3.4 Output: `unbundled_terms(lines, vocab) -> list[tuple[int, str, str]]`

The function returns `(line no, term, snippet)`, once per (line, term), ordered by line and
then by position. The engine formats each tuple as:

```text
UNBUNDLED TERM 'X': named nowhere in your evidence: <first 50 chars of the line>
```

Those messages are kept in their own `term_msgs` list (§4), and `style_msgs` is derived as
slop messages followed by term messages, so term findings follow slop findings in the retry
prompt rather than interleaving by line. It can always be answered without inventing anything:
delete the term, use the bundle's own spelling, or add real evidence to the vault.

**The function fails loudly on a wrong `vocab` shape.** A non-set `vocab` (a `str`, where
`in` would substring-match and suppress every finding; a list; or a set with non-`str`
members) raises `TypeError`, naming the type only. This is the same reasoning as
`validate()`'s `source_tokens` shape check.

**Disjoint from row 1 by construction.** Row 1 reports only terms in the skills vocabulary,
and every token of every entry's `Skills:` is in `mention_vocab` (via
`_entry_skills_line`), so no term is reported by both.

- **The superset relation is pinned (§6).**
- **The negative subtraction is the one exception, and it is not a conflict.** A term that
  is both a declared skill and a negative can now fire here as well as in row 1, because
  a name-shaped "never claim X" is reported in both places. That is user data contradicting itself, and
  it is reported rather than resolved.

### 3.5 Module: `cv/terms.py`

`candidates` and `unbundled_terms` live in a new `cv/terms.py`, matching the
one-module-per-STYLE-check shape of `slop.py` and `voice.py`. It imports `_tokens` from
`cv/validate.py`, as the engine already imports `section_spans` from there. One tokeniser
stays shared, and `validate.py`'s module docstring ("a non-empty list hard-blocks") stays
true.

## 4. Where the code goes

- **`cv/bundle.py`.** Add `mention_vocab(bundle)`. Reword the `_framing_lines` docstring:
  nothing that LICENSES reads it; `mention_vocab` reads it to RECOGNISE. Reword
  `bundle_sources`' statement about never touching `bundle["skills"]` so it is scoped to
  licensing.
- **`cv/terms.py`.** New, pure, standard-library only.
- **`cv/config.py`.** Add `term_check: bool = True`.
- **`cv/engine.py`.**
  - Compute `vocab = mention_vocab(b)` once per lead, beside `bundle_sources(b)`.
  - Beside `_slop_phrases`, when `cvcfg.term_check` is on, extend `style_msgs` with the
    formatted findings over `scoped_lines`.
  - Apply the §2.3 retention rule.
  - **Sign-off tag.** An UNBUNDLED TERM finding is tagged `term\t<msg>` at hold time instead
    of `style\t`. The engine knows the kind when it builds the message, so it does not parse
    a prefix back out later, which is the direction the engine's own comment calls fragile.
- **`cli.py::_print_signoff_claims`.** A `term\t` entry prints under its own heading:
  `N term(s) named nowhere in your evidence (possible invention)`. Without that, a probable
  invented technology would print as a "style/voice concern". Holds stamped earlier carry no
  `term\t` tag, so they print exactly as before.
- **`CvResult.terms`** carries these findings, on its own field beside `slop`, which keeps
  `cv/slop.py`'s findings only. That is the `voice_flags` precedent: telling the kinds apart
  by a message prefix is the encoding the engine already rejects for voice. On
  `skipped-gate`, `slop` keeps its HARD entries plus the last attempt's phrase findings and
  `terms` takes that attempt's term findings. `cv run` counts `terms=` on its summary line
  and prints each finding below it; the MCP `cv_run` result and `run.json` carry a `terms`
  key, the MCP one under the same untrusted-content warning as `slop`, since each finding
  embeds a snippet of the composed CV.
- **No prompt change.** `_DERIVED_NEGATIVE_PROMPT` already forbids a technology not in the
  bundle, and the retry feeds the finding back verbatim.

## 5. What was measured

The measurement ran locally, over real composed CVs, against a bundle rebuilt from the
owner's current vault. Nothing from it enters the repo: fixtures, docstrings, commits and
the PR stay synthetic (#312's lesson). The corpus was the owner's real composed CVs, with
text extracted from PDF, wrapped lines rejoined, and meta lines dropped to match §3.1.

| Rule | What it reported |
|---|---|
| any token not in the bundle | many tokens, nearly all inflected English verbs |
| shape rule vs the citable pool (`source_tokens`) | far fewer, including inventory-only terms and digit-bearing figures |
| **§3 rule vs `mention_vocab`** | **a handful, firing on a small minority of the CVs (about one in seven)** |

Those tokens are:

- one term absent from every verified evidence note;
- one dotted spelling of a term the bundle carries undotted;
- one compound of a bundled name.

Each is answerable by deletion or respelling. No ordinary English word survived. The
measurement used the earlier digit-leading exclusion. §3.2's any-digit exclusion is strictly
narrower in what it can report, so it cannot report more than that.

The limits:

- the vault has grown since those CVs were composed, so this undercounts;
- it is one owner's vault, and a rich one;
- PDF extraction approximates the composed text.

A synthetic review probe confirmed that a THIN bundle fires on title-case English the
baseline does not happen to contain: cities, months, methodology names, acronyms like
`OKRs`, and a first-person `I`. That is why `cv.term_check` exists (§2.2).

## 6. Tests and guards

**Neutrality.** Every test token is invented and `Example`-shaped (for example
`ExampleQuery`, `Examplequery`, `.Example`, `Examplelang#`). No token from §5's measurement,
and no real product name beyond what the suite already contains, enters a fixture,
docstring, commit or PR body.

**Order matters. Task 1 is the existing fixture.** `tests/test_cv_engine.py`'s shared
`CLEAN_CV` contains `- CI [EF1]`, and nothing in its `ENTRIES` or baseline says CI. A scratch
implementation of this rule measured it firing across that file. Six existing tests failed
outright. Worse, with the slop phrase check disabled, eight tests that should go red stayed
green, because the unbundled term supplied the retry they credit to slop (among them
`test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty`,
`test_style_hold_withholds_the_pointer_when_enabled` and
`test_the_rendered_text_is_the_retained_draft_even_when_a_later_attempt_was_worse`). So,
BEFORE any wiring:

- Add CI to that fixture's bundle sources.
- Extend `test_clean_cv_is_actually_clean` to assert `unbundled_terms` reports nothing.
- Change no compose-count assertion.
- After wiring, re-run the slop-disabled control and require those tests to go red again.
  The same sweep covers `tests/test_cv_run_artefacts.py`, which had two of the failures.

**Rule rows** (`tests/test_cv_terms.py`). Each arm gets a token that ONLY that arm admits,
so deleting the arm turns its row red:

| Arm | Token | Notes |
|---|---|---|
| (i) | sentence-initial `ExampleQuery` | (iii) cannot fire at sentence start |
| (ii) | lowercase `examplelang#` | — |
| (iii) | mid-sentence `Examplequery` | — |

Exclusion rows:

- any digit, in a lowercase token (`examplev2`) and a capitalised one (`Example2`);
- sentence-initial `Examplequery` is not a candidate, including after `:` and `.`;
- lowercase-only;
- `[AB1]` is stripped;
- a leading-dot `.Example` fires.

Suppression rows:

- a case-folded match suppresses;
- the plural fold suppresses one `s` only (`Examplequeriess` is still reported).

Witness discipline: MOVE or DELETE the clause, never add one beside it. Commit before each
witness.

**Vocabulary rows** (`mention_vocab`), one row per source, never one row for all:

- each of these suppresses: the baseline, an entry heading, an entry body, `Skills:`, a
  Skills Inventory note;
- the JD does NOT suppress;
- a presentation-header word is absent;
- a term in a negative is REPORTED even when an inventory note also carries it.

**Guards:**

- **Superset.** Every case-folded token of every entry's skills is in `mention_vocab`, which
  pins §3.4's disjointness.
- **Separation (INV-1).** `BundleSources` gains no field. `validate()`'s output on a bundle
  is unchanged when an inventory note and an entry heading carry a term the CV names: the
  row-2 refusal of an inventory-only skill still fires. The test asserts on the
  discriminating message, not on emptiness.
- **Shape check.** `unbundled_terms(lines, "examplequery")` and a list both raise
  `TypeError`, and the message names the type.
- **Anti-vacuity.** The roster is named: `tests/test_cv_engine.py::CLEAN_CV` and
  `tests/test_cv_validate.py`'s gate-clean CVs. Each one is run through `section_spans`,
  and the guard asserts all three of:
  - (a) the scoped line count is > 0;
  - (b) `candidates` returns a non-empty set over those lines;
  - (c) `unbundled_terms` reports nothing against the fixture's own bundle.

  Without (b), (c) is vacuous.

**Config rows:**

- the `term_check` default is True;
- `term_check: "false"` (quoted) raises through the generic bool check;
- off means no UNBUNDLED TERM in a `run_one` retry prompt.

**Engine wiring, through `run_one`** (with `_SequenceBackend` and `FakeVault`):

- An unbundled term on a hard-clean draft triggers exactly one retry, with the finding in
  the retry prompt.
- A clean retry renders.
- A persisting finding renders with `style_hold` off, and holds with `style_hold` on.
- A dry run reports it.

**Retention (§2.3):**

- Draft 1 has one finding and draft 2 has two: draft 1 is rendered, and `retained_attempt`
  is 1.
- A tie keeps draft 2.
- The rebind holds: the audit and the render read the retained draft.

**Sign-off rows:**

- A `term\t` entry prints under the invention heading, not as "style/voice".
- An existing `style\t` entry prints as before.

**Scope rows.** The same unbundled term on a SKILLS, meta, CERTIFICATES or EDUCATION line is
not reported by this check. Row 2 still reports the SKILLS one.

## 7. Accepted risks

- **Digit-bearing technology names are not candidates.** In a WORK bullet, the numeric gate
  still refuses their unlicensed digit HARD. In PROFILE prose a digit must be in the
  baseline or an entry.
- **Lowercase technology names are not candidates.** A retry-driving check should under-fire.
- **A negative written in lowercase does not suppress the recognised term.** The
  subtraction takes only a negative's candidates, so "never claim examplelang" leaves an
  inventory note's Examplelang in the vocabulary. Writing the term name-shaped in the
  negative is the fix; subtracting every word of a negative would instead remove ordinary
  words such as `platform` and fire on every lead.
- **Sentence-initial detection is punctuation-based.** A capital after an abbreviation's
  period ("Ltd.", "U.S.") reads as sentence-initial and is skipped, which is an under-fire.
- **Title-case English absent from the bundle fires** (cities, months, methodology names,
  `I`). This is rare on a full baseline. On a thin vault, `cv.term_check: false` is the
  escape. A city flagged in PROFILE prose is answerable by deleting it from prose. It is
  never a demand to supply one, which is what separates it from the LOCATION refusal.
- **The plural fold is English-shaped.** A non-English CV gets less suppression and more
  retries, never fewer findings.
- **Spelling variants fire** (dotted vs undotted, hyphenated vs joined). They are answerable
  by adopting the bundle's spelling.
- **Abbreviations with internal capitals and a missing space after a full stop also fire.**
  `U.S` or `Ph.D` read as an inner-capital token (arm (i)), and `it.Examplequery` reads as
  one dotted token. Each costs one retry and never a lost lead.
- **Non-ASCII names fragment.** The shared, ASCII-only `_WORD_RE` splits "Exämple" into
  "Ex" and "mple", so an invented non-ASCII name may be reported only as a fragment, or not
  at all. Accepted: the vocabulary fragments identically, so a bundled non-ASCII name stays
  quiet, and the shared regex is not changed here.
- **Proper nouns that are not technologies but are absent from the bundle fire**, such as a
  target employer named from the JD. That is still invention relative to the evidence.

## 8. Docs and prose to update

Found by grepping the CLAIMS, not by listing files by hand. The implementer re-runs these
greps and fixes every hit rather than trusting this list:
`grep -rn "never touches\|nothing harvests\|third signal\|RETAINS the last\|style/voice\|halves\|cv/slop.py's" sluice docs .rulesync`.

- **`docs/ARCHITECTURE.md`**: the STYLE-tier paragraph (voice is no longer "the third
  signal"), the Skills Inventory non-citability sentence (scope it to licensing), and the
  retention rule.
- **`.rulesync/rules/CLAUDE.md`**: the SCOPED STYLE tier sentence ("TWO halves", rewritten to
  name the members without a count), the "RETAINS the last HARD-clean draft" sentence, and
  the Skills Inventory "licensed by nothing" wording (still true for licensing; add that
  this check recognises it). Then run `npm run rulesync`.
- **Docstrings and comments**: `cv/bundle.py`'s `_framing_lines` and `bundle_sources`, the
  `CvResult.slop` field comment (and the new `CvResult.terms` one), and the slop comments in
  `cli.py` and `mcpserver.py`.
- **`docs/USAGE.md`**: the `cv run` summary line and finding-kind table gain `terms`, and the
  `run.json` key list.
- **`docs/CONFIGURATION.md`** and **`sluice.yaml.example`**: `cv.term_check`.

**Commits.** Use `feat(cv)` for the check and `fix(cv)` for the retention change, as
separate commits. Neither takes a `!`: no config changes meaning, and keeping a better draft
is not breaking under `CHANGELOG.md`'s list.
