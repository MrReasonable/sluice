# Evidence-aware triage (#196) — design

Status: design approved in conversation 2026-10-13. Revised after `/review-plan` round 1
(0 Critical / 4 High / 11 Medium / 8 Low), round 2 (0 Critical / 2 High / 13 Medium / 7 Low) and
round 3 (0 Critical / 1 High / 5 Medium / 9 Low); every finding is folded below, and the two
owner decisions round 2 raised are D4 and D5.

## Goal

Let the triage judge weigh a lead against the candidate's VERIFIED evidence (Experience Library
and Skills Inventory), when the user switches it on, so a lead's score reflects whether the
candidate's record supports what the ad asks for — without sluice computing a coverage number,
and without a missing or partial library being a reason to lower a score.

## What the issue says, checked against `main` (4ae11bde)

- "`read_experience_entries` has two consumers" — stale. It was retired in #165 for
  `Store.read_evidence(kind, verified_only=True)` (`core/protocols.py`). Its callers today are
  `cv/engine.py`, `cv/selection.py::named_entries`, and `core/app.py` (the CV paths,
  `list_evidence` and `Sluice.doctor`). `core/doctor.py` names it only in a comment.
- "Lead scoring runs off `read_criteria()` alone" — true. `triage/engine.py::run` builds the judge
  prompt as `build_system_prompt_from(vault.read_criteria())`; nothing in `sluice/triage/` reads
  evidence. Since #378 the dossier also carries `followup_answers`, the user's own notes on a lead.
- #193's stemmer (`core/stem.py`) is NOT used by this design (see Rejected approaches).

## Owner decisions (2026-10-13)

| # | Question | Decision |
|---|----------|----------|
| D1 | How does coverage affect triage? | **Feed the judge.** Verified evidence is context in the judge prompt, like #378's answers. Sluice computes no coverage number. |
| D2 | What may the judge do with an uncovered requirement? | **Note it, never score down.** Supporting evidence may raise confidence; a gap goes in `concerns` and is never on its own a reason to lower `relevance_score` or change the verdict. The library may simply be incomplete. |
| D3 | Which evidence? | **Experience + skills, compact.** One line per entry, no note bodies, no prose fields. Stories excluded (interview material, not requirement coverage). |
| D4 | Default? | **Opt-in, permanently.** `triage.evidence: false` ships and stays the default. The live eval (§7) runs before merge; if it passes, `docs/CONFIGURATION.md` recommends turning the key on and states what it sends to which backend. |
| D5 | A judge call that returns no verdicts while the backend is up | **Exits non-zero, always.** Today the CLI prints "Treat this run as failed" and exits 0. This PR makes that case exit 1 whatever the evidence setting, and adds a record-size hint when a record was sent (§5). |

**How D4 got here.** First "on by default"; round 1 (three reviewers independently) showed the
D2 guarantee is a prompt instruction, unmeasured, and that `triage.backend` is configured
separately from `cv.backend`, so on-by-default sends verified employer history to a provider that
never received it. "Off until the eval passes, then flip" followed; round 2 (two reviewers) showed
the eval answers only the first reason — on the owner's backend — and nothing about the second.
Opt-in matches every precedent for "starts sending or writing" (`record_usage`,
`company_resolve_fetch`, `company_resolve_llm`).

## Rejected approaches

- **A ranked subset per dossier** (`cv/bundle.py::rank` + the #193 stemmer choosing the entries
  relevant to each JD). Cheaper on a large library, but the ranker decides what the judge never
  sees, and an entry it drops reads to the judge as a gap — #193's word-form failure moved into
  triage.
- **Sluice-computed coverage** (stem-matching JD requirements against evidence, a number in the
  dossier or the score). Ruled out by D1; it is the "number a verdict depends on" the issue warns
  is the 672ad2a class.
- **A size cap on the block.** A cap silently hides entries, which the judge then reads as gaps.
  See §3 "Size".
- **A code-side backstop** (overriding a `dismiss` whose concerns are all evidence gaps). It
  would parse model prose to change a verdict. Revisited only if the live eval fails, and then as
  a new owner decision.
- **Rendering every declared field** (round 1's fix for per-kind formatting). Round 2 measured it
  printing a skill's `Label` twice and pulling in skills' prose fields `Evidence` and
  `Signal Value`, against D3. Replaced by §1's declared field list.
- **A `rejected=` out-parameter on `judge()`** (round 1's fix for the all-batches-rejected case).
  It reopened the engine's deliberate "verdicts in, verdicts out" contract, and named a cause the
  engine cannot know. Replaced by D5, derived from fields the report already has.

## Design

### 1. Which kinds and fields reach the judge: `EvidenceKind.judge_fields`

`EvidenceKind` gains `judge_fields: tuple = ()` — the kind's declared fields the judge is shown,
in order. A kind is judge-read exactly when the tuple is non-empty; there is no separate boolean,
so the two cannot disagree. Set on:

- `experience`: `("Company", "Category", "Best For", "Metrics", "Tools")` — not `Skills`: by the
  owner's model, an entry's `Skills:` are general soft skills tied to NO job, so printing them on
  a line beside `Company:` would attribute them to that employer. General skills reach the judge
  through the Skills Inventory instead.
- `skills`: `("Proficiency", "Domain")` — not `Label` (it is the entry's name, §3), not
  `Evidence` or `Signal Value` (prose; and `Evidence:` would collide with the JD's "evidence").
- `stories`: `()`.

`__post_init__` refuses a `judge_fields` member that is not in the kind's `fields`, so a typo
fails at import, not as a silently empty column. The engine selects kinds by this attribute,
never by name, and the renderer is generic over it, so judge-reading a further kind is a
one-line registry edit.

Consumers that must learn it:

- `core/doctor.py::classify_store`'s per-kind row. Its last branch says nothing reads a corpus
  that is neither cited nor composer-read; with `judge_fields` set that could become false — but
  only when `triage.evidence` is on, which `classify_store` cannot see and which ships off.
  So the branch's wording becomes conditional ("nothing reads this unless `triage.evidence` is
  on" for a kind whose only reader is the judge). No kind is judge-only today; this keeps the
  flag's legal space honest and is tested with a rebound registry.
- `tests/` that rebuild `EvidenceKind` by hand-listing its keyword attributes are switched to
  rebuild from `dataclasses.fields`, so the new attribute does not break them by arity.

`verify_outcome` is NOT changed: its messages say what verifying buys for the CV, and the judge
reads the record only when the user has opted in.

### 2. `cv_name` moves to `core/protocols.py`

`cv/selection.py::cv_name` (a skill note's display name: `Label:`, else its title) is store
vocabulary, not CV logic: `core/doctor.py` already restates its rule because `core/` may not
import `cv/`. It moves to `core/protocols.py` beside `EvidenceKind`; `cv/selection.py` imports
it from there under the same name, so `core/app.py`'s import and every existing caller keep
working. `core/doctor.py::classify_skill_labels` currently restates the Label-else-title blank
test inline while its docstring claims "the blank test is cv_name's own"; now that `core/` can
reach it, doctor CALLS `core/protocols.py::cv_name`, which makes that sentence true. Citations of
`cv/selection.py::cv_name` are updated to `core/protocols.py::cv_name` in `core/doctor.py`'s
docstrings and in `.rulesync/rules/CLAUDE.md` (which the citation-drift test
does not sweep, and which does not count an import as a symbol). **Triage imports nothing from
`cv/`** — no pipeline sub-app imports another today, and §7.11 keeps it so.

### 3. Rendering: pure, in `triage/prompt.py`

`build_system_prompt_from(criteria: str, record: Sequence[tuple[EvidenceKind, list]] | None =
None) -> str` — the engine passes each `EvidenceKind` it already looked up together with its
entries, so the renderer neither reads the registry nor does I/O. A kind's heading is the
basename of its `relpath` (`Experience Library`, `Skills Inventory`); `EvidenceKind` has no
separate label attribute and none is added.

- **Abstain by construction:** when `record` is None or every entry list is empty, the returned
  prompt is BYTE-IDENTICAL to today's. Every insertion below happens only when at least one entry
  exists.
- **Named constants, no string splicing.** `_SCAFFOLD_TAIL` is split into named constants at the
  points below, concatenated in order, so the no-record concatenation is today's exact text and
  an insertion cannot silently no-op on a drifted anchor. New constants carry `PROMPT` in their
  names, and are added to `tests/test_prompt_neutrality.py`'s `_KNOWN_PROMPTS` floor so a rename
  cannot drop them from the direct sweep.
- **Three insertions, all conditional, each at a named split point:**
  1. **The record block**, between the criteria and the tail's `## Inputs` — the block sits with
     the other candidate-side material, before the per-dossier instructions.
  2. **The coverage rule**, directly after the `## Scoring scale` list.
  3. **A record note** directly after the numbered `## Final reminders` list — an unnumbered
     paragraph, so the list's numbering is untouched.
- **The block is named "Candidate record", not "evidence".** The scaffold already uses
  "evidence" for the JD ("The `jd.markdown` … is your PRIMARY evidence", "strong evidence-backed
  fit"). The header says: this is the candidate's own verified record; it is about the
  CANDIDATE, never about the role; never quote it as JD text; and wherever the scaffold says
  "evidence" it still means the JD.
- **One line per entry, generic over kinds.** Per kind, a sub-heading (its `relpath` basename), then one
  line per entry: `cv_name(entry)`, then each `judge_fields` member that is non-empty, as
  `Field: value`, joined by ` | `. No per-kind code.
- Each value is flattened to one line (whitespace runs, newlines included, collapsed to one
  space), so a hand-typed block value cannot forge a prompt line or heading. Values are the user's
  own vault text; per the threat model this guards accidental structure, not a hostile author.
- Entries render in the store's order; no ranking, no cap.
- **The coverage rule (D2)**, in substance: where the candidate record supports a JD requirement,
  that is evidence of fit and may raise the score. A requirement the record does not cover goes in
  `concerns` as "no verified evidence for <X>". Such a gap is NEVER on its own a reason to lower
  `relevance_score` or move the verdict down a band, because the record may be incomplete. It then
  names the two existing lines it qualifies, so they cannot be read against it:
  - the thin-JD bullet ("say what is missing in `concerns` and score conservatively") is about
    what the JD omits, not about gaps in the record;
  - final reminder 4 ("never flag … if the profile states the candidate already satisfies
    them") still applies; the record adds supporting facts, and a gap concern is not raised for
    anything the profile already says the candidate satisfies.
  "Be willing to dismiss" applies to the JD and the profile, not to gaps in the record.
- **The record note** after the reminders restates the rule in one line (a gap is a concern,
  never a score), for the same reason the reminders list exists.
- **Size.** `triage/judge.py::_build_prompt` prefixes the system prompt to EVERY batch
  (`batch_size` default 5), so the block is paid once per batch, not per run. A compact line is
  ~30-60 tokens; a 100-entry record is ~3-6k tokens per batch, comparable to the scaffold
  itself. Past a provider's context limit, every batch is rejected non-transiently; D5 makes that
  run exit non-zero with a hint naming the record (§5). Accepted rather than capped (a cap hides
  entries); `docs/CONFIGURATION.md` states the per-batch cost beside the key.

### 4. Reading: in the engine, through the store

In `triage/engine.py::run`, inside `if keeps and not no_llm:`, the judge call gains a NEW guard:
when no dossier survived the fetch, `judge` is not called and nothing is read (today it is called
with `[]`, which makes no backend call and writes, counts and audits nothing). The guard skips
ONLY the criteria/record read, `sent_to_judge` and the `judge` call; the rest of that branch
(verdict reconciliation over an empty list) and the rejected-leads note render that follows the
branch (`render_rejected_note`, at the end of `run`) still run, so it is a guard around three
statements, not an early return from `run`. One observable change: on such a
run `report.backend` is no longer set, so the digest's "via <backend>" reads as no backend ran,
which matches what happened (`_format_triage_digest`'s docstring already describes that case). Behind that guard, and only when `cfg.evidence` is True, the engine looks up
`protocols.EVIDENCE_KINDS` AT CALL TIME (module attribute, not a from-import bound at load,
following `cv/selection.py::pool_kinds`) and, for each kind with non-empty `judge_fields`:

```python
entries = vault.read_evidence(kind, verified_only=True)
```

- `verified_only=True` is the citability boundary the CV uses: unverified entries in the kind's
  own folder, and anything under `_inbox/`, never reach the judge. It is passed explicitly even
  though it is the default; §7.2 pins it with a `=False` mutant (deleting the argument is an
  equivalent mutant).
- ABSENT returns `[]` per the Store contract, so that kind contributes nothing.
- UNREADABLE raises → caught as `(OSError, UnicodeDecodeError)` PER KIND, each kind in its own
  `try`, logged as a WARNING naming the kind and the error; that kind contributes nothing and is
  recorded on the report (§5). A bare `ValueError` is NOT caught: `read_evidence` raises
  `ValueError` for an unknown kind, and a store that does not support a judge-read kind must fail
  loudly, not read as "unreadable". Round 2 confirmed the vault's evidence read path raises no
  other `ValueError` for a real unreadable corpus (non-UTF-8 is `UnicodeDecodeError`; symlink
  refusals and missing/unreadable directories are `OSError`).

  Why warn-and-continue is not a silent failure (Hard Rule 9): it is per-item isolation, visible
  on the report, the stderr summary and the digest, and losing a kind reproduces today's prompt
  for that kind. A corrupt evidence note must not stop triage.
- `triage run --dry-run` DOES call the judge (it skips writes, not the backend), so with the key
  on, a dry run reads and sends the record too. Stated in `docs/CONFIGURATION.md`.

### 5. Reporting and the exit code (D5)

`TriageReport` gains `record_counts: dict` (kind → entries rendered) and
`record_unreadable: list` (kinds that raised). A record "was sent" when the counts sum to > 0.

- **stderr summary** (`cli.py::cmd_triage_run`'s `triage: {counts} judged=…` line): gains
  `record=` — the per-kind counts, `off`, or the counts plus `unreadable=<kinds>`, and `none`
  when the judge was not called. It is printed on EVERY run, written in the same
  `record={...}` f-string form as the other keys, so
  `tests/test_docs_claims.py::_printed_summary_keys` finds it; `record` is added to that test's
  pinned key set in the same commit, which is what makes the `docs/USAGE.md` update checked
  rather than trusted.
- **push digest** (`_format_triage_digest`): one line when a kind was unreadable (the case a user
  must act on). And the existing "returned NOTHING. Treat this run as failed" arm gains, when a
  record was sent, "Your candidate record was sent with every batch (N entries); if it is too
  large for the provider, set `triage.evidence: false`."
- **exit code (D5):** `cmd_triage_run` returns 1 when `report.sent_to_judge > 0`,
  `report.judged == 0`, and no `backend_error` (which already returns 1) — exactly the
  condition the digest already calls failed. The `return 1` sits beside the existing
  `backend_error` return, AFTER `_notify_reporting`, so the digest (with its "returned NOTHING"
  line and the record hint) is still pushed before the run exits. Derived from existing report fields; `judge()`'s
  signature and contract are unchanged. The stderr gets a matching one-line reason, with the
  record hint when a record was sent. The #223 early-return path is unchanged.
- `docs/USAGE.md`, which documents both outputs, is updated, including the new exit condition.

### 6. Downstream and re-judging scope

- Only leads in `DEFAULT_TRIAGE_STATUSES` (`new`, `research`, `unjudgeable`) are judged by a
  default run, so switching the key on re-judges nothing already shortlisted or dismissed.
  `research` leads are re-judged every run; the rule forbids the record moving them down, but
  that is a prompt instruction, and the live eval measures it before the docs recommend the key.
- Gap concerns ("no verified evidence for X") land in the note's `triage_concerns`, which
  `cv run` shows the composer as TRIAGE NOTES. Round 2 confirmed this is benign: `cv/compose.py`
  treats triage notes as non-citable framing outside the gate's bundle (#329).

### 7. Testing

**Offline, in CI (`python -m pytest`).** Each item names the defect that turns it red.

1. **Abstain.** (a) An explicit assertion that none of the three insertion constants appears in
   the no-record render (`None`, `[]`, all-empty entry lists), for the shipped default criteria
   and a synthetic one — red if any insertion becomes unconditional — AND that the three inputs
   render byte-equal to each other (red if only `None` takes the abstain path and the others
   leave a stray separator). (b) The split constants
   re-concatenate to the pre-split tail: a SHA-256 of the render for the SYNTHETIC criteria only
   is frozen, captured on this branch before `prompt.py` is edited. It does not include
   `DEFAULT_CRITERIA`, so editing the shipped criteria does not break it; a legitimate scaffold
   edit re-freezes it in the same commit, with the diff read. (c) Through the engine:
   `cfg.evidence=False` with a populated store makes NO `read_evidence` call.
2. **Verified only.** The fixture holds a verified and an UNVERIFIED entry in the kind's own
   folder, each with a distinct sentinel. Precondition: the store returns the unverified one under
   `verified_only=False` (measured in round 2: it does). Then only the verified sentinel reaches
   the prompt the fake backend receives. Witness: `verified_only=False` → red.
3. **Kind roster.** The kinds read are exactly those with non-empty `judge_fields`; the test first
   asserts that set is non-empty. Rebinding `sluice.core.protocols.EVIDENCE_KINDS` (the binding
   the engine reads at call time) to add `judge_fields` to `stories` makes stories read — red if
   the engine binds the registry at import. `__post_init__` refuses an undeclared field name.
4. **Per-kind isolation.** (a) skills raises `UnicodeDecodeError` → WARNING naming skills,
   `record_unreadable == ["skills"]`, experience still in the prompt; (b) experience raises
   `OSError` → skills still in the prompt (red if one `try` wraps the loop); (c) a `ValueError`
   that is NOT a `UnicodeDecodeError` propagates (red if `ValueError` is caught — this is the one
   arm that discriminates, since `UnicodeDecodeError` subclasses `ValueError`).
5. **Never read off the judge path.** `--no-llm` makes no `read_evidence` call. A run where every
   JD fetch failed (zero dossiers) neither calls `judge` nor reads the record, leaves
   `report.backend` unset, and STILL renders the rejected-leads note — red if the guard is
   removed, the read moves above it, or the guard is written as an early return.
6. **Rendering.** `judge_fields` order, empty fields omitted, `cv_name`'s Label-else-title with
   `Label` NOT repeated as a field, prose fields absent, newline flattening (a block value cannot
   start a prompt line), and a rebound registry with a third judge-read kind rendering with no
   code change.
7. **Neutrality.** A `_SYNTHETIC_ARGS` override for
   `sluice.triage.prompt.build_system_prompt_from`'s `record` (synthetic entries, both kinds);
   the test asserts all THREE insertions are in the swept text, following
   `test_the_swept_structured_prompt_carries_every_conditional_block`. The new constants are in
   `_KNOWN_PROMPTS`.
8. **Config.** `evidence: "false"` refuses; the default is `False`.
9. **Reporting.** `record=` for each state, `none` when the judge was not called; `record` in
   `_printed_summary_keys`' pinned set; the digest's
   unreadable line only when a kind raised; the record-size hint only when a record was sent
   (rows: all batches rejected with evidence off → no hint; on with every kind empty → no hint;
   on with entries → hint — red if the "record sent" condition is deleted).
10. **Exit code (D5).** `sent_to_judge > 0` and `judged == 0` → exit 1, with the key on AND off,
    and the digest was pushed first (the notify stub received it);
    one batch of several rejected → exit 0; zero dossiers → exit 0 (the new guard means the judge
    was never called); `backend_error` → still exit 1 via its existing path.
11. **doctor.** A rebound registry with a judge-only kind: its row says it is read only when
    `triage.evidence` is on, and the unchanged `stories` row's text is asserted EQUAL to its
    pre-change text (a substring check would pass if the new wording leaked onto `stories`). The
    test names the binding it patches (`core/doctor.py` imports the registry at load).
    `classify_skill_labels` calls `cv_name` (§2): deleting the call reddens its existing tests.
12. **`cv_name` move.** Existing `cv/selection.py` tests stay green unmodified; `sluice/triage/`
    imports nothing from `sluice.cv` (AST sweep, asserting it found triage modules to sweep).

Each guard is mutation-witnessed in the implementation by MOVING or DELETING the code it pins.

**Live, on demand (not CI): `scripts/triage_evidence_eval/`.** Follows `scripts/coach_eval/`'s
rules: dev-only, no run without the owner's go-ahead, the first run's cost reported before any
further run. Its result decides whether `docs/CONFIGURATION.md` RECOMMENDS the key (D4); it does
not change any default.

- **Inputs, all synthetic:** a Judging Profile written for the eval (not the user's), synthetic
  leads, and a synthetic partial record. The eval's Judging Profile DELIBERATELY states
  preferences — no lead could be expected to shortlist otherwise — and that is acceptable
  because it is a dev-only fixture under `scripts/`, never shipped in `sluice/` and never a
  default; neutrality here means no REAL identity, not no opinion.
- **Identity checks, on LOADED values:** `tests/test_triage_evidence_eval.py` loads the fixtures
  through the eval's OWN loader and asserts, per field:
  - every lead `company` and every record entry's `Company:` is in
    `tests/test_fixture_name_neutrality.py`'s `_REVIEWED_FIXTURE_IDENTITIES |
    _SPARE_FIXTURE_IDENTITIES` (the repo's enumerable roster; an `Example`-prefix SHAPE rule was
    rejected by the owner on 2026-09-11 and is not used);
  - every lead `title` is drawn from `tests/conftest.py::_title_pool`'s seeded output;
  - every `location` is in `tests/conftest.py::LOCATIONS`;
  - every URL's host is a reserved example domain (`example.com`/`.org`/`.net`, RFC 2606);
  - a minimum count of leads and record entries loaded, so a loader that reads nothing fails.
  JD bodies and the profile are free text no roster can enumerate, so — as #27 did for the golden
  corpus — a SHA-256 digest over the canonical loaded fixtures is pinned, and the test's failure
  message says a human must read the diff before re-pinning. `expresses_a_preference` is NOT
  used: round 3 measured it rejecting 10 of 39 seeded titles, and the profile must express
  preferences by design.
- **Leads:** should-shortlist leads, each naming one requirement the record does not cover; a
  borderline lead (expected `research`, ~62-70), where a drop crosses a verdict band; and a
  NO-SUPPORT lead whose requirements the record covers not at all, so a gap penalty cannot be
  netted out by credit for supported requirements.
- **Procedure:** each lead judged N times (default 5) per condition, with and without the record,
  through the real configured triage backend.
- **Ordering:** verdict bands rank `dismiss` < `research` < `shortlist`; a median verdict is the
  median of that rank. An `unjudgeable` verdict in a BASELINE run makes that lead INVALID; one in
  a WITH-RECORD run is a FAIL (the record moved a lead out of its band — in production that
  changes its status).
- **Per-lead result:**
  - INVALID if the no-record baseline's median verdict is not the expected one;
  - FAIL if the with-record median verdict is in a lower band than the baseline's, or its median
    score is below the baseline median by more than the baseline's spread (max − min over its N
    runs), or fewer than a majority of with-record runs carry the gap's fixed requirement term in
    `concerns` (case-insensitive substring);
  - PASS otherwise.
- **Overall:** FAIL if any lead fails; INCONCLUSIVE if the borderline or the no-support lead is
  INVALID, or fewer than half the leads are valid; PASS otherwise. Only PASS lets the docs
  recommend the key. Run-to-run noise can trip a FAIL arm on an innocent record; that errs
  safe (the docs then do not recommend the key) and is accepted.
- Offline unit tests cover argument handling, each scoring rule failing when its rule is broken,
  the INVALID and INCONCLUSIVE outcomes, and the fixture checks above — as
  `tests/test_coach_eval.py` does.

### 8. Docs

- `docs/ARCHITECTURE.md`: the triage paragraph (the judge reads the verified record when
  `triage.evidence` is on; D2; selected by `judge_fields`), and the store-contract paragraph's
  list of `EvidenceKind` attributes.
- `docs/CONFIGURATION.md` + `sluice.yaml.example`: `triage.evidence` — off by default; what it
  sends to which backend (triage's, which may differ from `cv.backend`); per-batch cost; dry runs
  send it too; and, only if the live eval passed, the recommendation to turn it on.
- `docs/USAGE.md`: the `record=` summary field, the digest lines, and D5's exit condition.
- `CHANGELOG.md`: release-please writes the entries; the D5 exit-code change is a `fix(triage)`
  whose commit body states it, since an unattended run that previously exited 0 now exits 1.
- `.rulesync/rules/CLAUDE.md`: the `EvidenceKind` attributes paragraph gains `judge_fields`, and
  the `cv_name` citation moves (§2). Regenerate afterwards.

## Out of scope

- Ranking or stemming evidence for triage (#193's stemmer stays a CV-ranker tool).
- Stories (#195's territory).
- Changing `audit_jsonl` or the verdict schema.
- Any deterministic adjustment to `relevance_score`.
- Changing `judge()`'s signature.
