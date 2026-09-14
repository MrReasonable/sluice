# CV composer framing from triage — design (#329)

Status: direction agreed in chat; revised after two `/review-plan` rounds. Every open question those
rounds raised is recorded as a decision in §2.

Issue: **#329** (the composer frames a CV against the JD, the role title and the Skills Inventory,
and never sees the judgement triage already recorded on the same lead).

No count of call sites, rules or tests appears in this document. Where a roster matters, the test
that derives it is named instead.

---

## 1. The problem

`cv/compose.py`'s module docstring: "The prompt's entire factual content is the closed verified
bundle + the JD + the format contract; the backend has no other source." `cv/engine.py::_run_one`
reads `company` and `role` (written at ingest) for the prompt, and no triage JUDGEMENT field
reaches it.

By the time a lead reaches `cv run`, triage has usually judged it: `culture_flags` (a quoted,
comma-joined frontmatter string of `positive:`/`negative:`/`unknown:` signals) and a list of
`concerns`. The concerns exist only as prose: `triage/apply.py::apply_verdict` appends
`Concerns: a; b` inside a dated `[triage YYYY-MM-DD]` fragment of `relevance_notes`, the same field
`leads dismiss` and `leads expire` append to.

Both are judgements of the JD against the user's own criteria, not summaries of the JD alone:
`triage/engine.py::run` builds the judge's system prompt with
`build_system_prompt_from(vault.read_criteria())`, and the shipped few-shot in `triage/prompt.py`
words its concerns against "the profile".

So a concern or a negative flag cannot influence which verified entries the composer leads with.
Every gate still passes either way, because the gates check sourcing, wording and structure, never
emphasis.

## 2. Decisions

| Question | Decision |
|---|---|
| Where the composer gets concerns | A new structured `triage_concerns` frontmatter key written by triage (§3.1). `relevance_notes` is never parsed. |
| `cv signoff` shows the framing | Yes, as a snapshot of what the composer was given (§5). |
| Framing for a lead triage never judged | Hand-edit the note once the lead is at `shortlist` (§3.4). No `--framing-file` flag. |
| How wide the triage type fix goes | Every field of every verdict is validated before it is applied (§3.3). |
| A verdict whose `verdict` field is unusable, missing or null | Dropped as unrepairable: a failures line names the lead and the field, and the lead stays where it was for the next run (§3.3). |
| A hand-typed YAML list under a framing key | Triage leaves that key unwritten, checked against the fresh note inside the write, through a new `update_fields` keyword (§3.4). |
| What reaches the composer | Both flags and concerns, under a rule forbidding any mention of them (§4.2), with the unguarded residual stated (§4.3). |
| An off switch | None. Always on; the release note says so; blanking a lead's two keys opts it out (§8). |
| MCP `cv_signoff` response shape | `framing` returned separately from `claims`; the confirm token still covers the stored array (§5.3). |
| Process | This spec, `/review-plan`, an implementation plan, then TDD. |

---

## 3. Triage side

### 3.1 The `triage_concerns` key

**The name is prefixed.** `core/vault.py::_set_fm`, `_fm_value` and `_fm_dict` all match a key with
leading whitespace allowed (`^\s*key\s*:`). No lead note sluice created before this ships carries a
top-level concerns key, so the first write to a note whose frontmatter nests an indented
`concerns:` under some other property would land on that nested line, orphan its siblings and break
the YAML; `_fm_dict` would also read the nested value as the lead's own. A prefixed name makes that
collision unlikely at no cost. `culture_flags` keeps its name: every note sluice created already
carries it at top level.

`apply_verdict` writes `triage_concerns` in the same loop that writes `glassdoor_rating` and
`culture_flags`, joined with `"; "` and quoted:

- **Replaced on every verdict, for every input shape.** A verdict whose normalised concerns are empty
  writes `""`, clearing an earlier value. §3.2 is what makes this hold when an item is unsafe. The
  one exception is a key holding a hand-typed block list, which is left alone (§3.4).
- `relevance_notes` keeps its `Concerns: ...` fragment, built from the same normalised list. It is
  the human-readable history; the key is what code reads.

`core/vault.py`'s create template gains `triage_concerns: ""` directly after `culture_flags: ""`, so
a new note's schema does not vary by whether triage has run. README's sample lead note gains the same
line; `tests/test_readme_quickstart.py::test_the_sample_note_is_what_leads_add_actually_writes`
enforces that the two agree.

Leads judged before this ships have `culture_flags` and no `triage_concerns` key. Their framing is
built from `culture_flags` alone (§4.1).

### 3.2 List fields are normalised one item at a time

`triage/judge.py::parse_verdicts` validates that a reply is a JSON array and nothing about each
verdict's fields. Measured during design with a stub store:

- A bare string is joined **character by character**: `culture_flags: "positive: coaching named"`
  was written as `"p, o, s, i, t, i, v, e, :, ..."`, and `concerns: "client hidden"` became
  `Concerns: c; l; i; e; n; t; ...` in `relevance_notes`.
- A non-string element (`concerns: [1]`, `culture_flags: [{...}]`) raises `TypeError`.
- Measured in review: a dict value joins its KEYS.

A list field is normalised to a list of strings:

- a list or tuple: each element that is a string **and** passes `frontmatter_safe` is kept; every
  other element is dropped and logged, naming the field and the lead, never the value;
- a bare string: one item, subject to the same safety check;
- a missing or empty value (`None`, `""`, `[]`): an empty list, silently, as today's `or []` does;
- any other value (a number, a dict): an empty list, logged.

Dropping rather than `str()`-ing is deliberate: `str({"positive": "x"})` writes a Python repr into a
user's note.

**Where it runs.** This normalisation lives in one helper in `triage/apply.py`,
`normalise_fields(raw, slug)`, which checks and repairs every field except `lead_id` and returns the
same `(normalised, reason)` pair as `normalise_verdict`.
`normalise_verdict` (§3.3) is that helper plus the `lead_id` check, for the engine, which has to match
a verdict to a note by that id. `apply_verdict` calls `normalise_fields` on its own input, with the
note's slug, because it is handed the note directly and a direct caller need not supply a `lead_id`.
A second pass over already-normalised fields changes nothing and logs nothing.

**The joined check.** `apply_verdict`'s loop keeps its `frontmatter_safe` check on each joined value.
It stays live for `glassdoor_rating`, which is not normalised item by item. For `culture_flags` and
`triage_concerns` it cannot fire, because every item is already safe and the joiners `", "` and
`"; "` are safe characters, so no test claims to witness it for those two keys.

**A deliberate change to `culture_flags`.** Today one unsafe flag makes the whole joined value fail
`frontmatter_safe`, the key is skipped, and the PREVIOUS verdict's flags stay on the note while
`relevance_notes` records the new judgement. That was cosmetic while nothing read the key. This
design gives it a reader, so the unsafe item alone is now dropped and the key reflects the verdict
just applied.

### 3.3 Every verdict is validated before it is applied

Measured in review, driving `apply_verdict` with one field changed per call: `relevance_score:
"high"` raises `ValueError`; a wrong-typed `relevance_score`, `fit_reasoning` or
`recommended_next_action` raises `TypeError`; a non-string `verdict` raises `AttributeError`. Read
from the code: a verdict that is not a dict raises at `triage/engine.py::run`'s
`judged_ids = {v.get("lead_id") for v in verdicts}`, before any verdict is applied; a list
`lead_id` raises there too; and `verdict` is clamped a second time outside `apply_verdict`, for the
counts row.

Read from the code, not yet executed end to end: none of those is caught before `cli.py::main`. The
engine's verdict loop catches only `VaultConflict`, `Sluice.triage` returns `_triage_run(...)`
directly, and `cmd_triage_run` does not wrap it. `cli.py::main` turns a `ValueError` into
`job-sluice: <message>` with exit 2, the shape of a usage error, so a malformed model reply is
reported as the user's mistake; anything else exits 1 with a traceback through `core/safeout.py`.
Either way, verdicts earlier in the batch are written, later ones are not, and no summary prints.

**Fix.** A pure `normalise_verdict(raw) -> tuple[dict | None, str]` in `triage/apply.py`, beside
`clamp_verdict`, which already owns the judge's verdict vocabulary. It returns the normalised verdict
with an empty reason, or `None` with the reason the verdict is unusable, so the rejection and the
failure line that reports it come from one function. In `triage/engine.py::run`, `report.judged` keeps
counting what came back from `judge()`, as it does today. `cli.py`'s triage digest reports a run
whose `judged` is zero while `sent_to_judge` is not as the judge being called and returning NOTHING,
so counting after normalisation would blame the backend for a reply made entirely of malformed
verdicts. The next line
normalises, so every later reader (`judged_ids`, the apply loop, the counts clamp, the audit entry)
sees a well-formed dict. `judge()`'s own contract, verdicts in and verdicts out, does not change; the
engine's comment beside that call already keeps it that way on purpose.

- **Unrepairable, returns `None`:** not a dict; `lead_id` not a non-empty string; or `verdict`
  missing, `None`, not a string, or blank after stripping. The verdict is dropped with a
  `report.failures` line naming the field, and the lead id when it is usable, never the raw value.
  A verdict with no usable `lead_id` names no lead, so its line makes no promise about one. When the
  reply holds no other verdict for that lead, its dossier also counts in the existing "N of
  M dossier(s) came back with no verdict" line, whose listed causes gain "a malformed verdict
  reported above". Nothing is written, so the lead stays in the status that selected it and the next
  run judges it again.
- **A lead with any NAMED unusable verdict gets none applied.** The failure line for a rejected
  verdict that names a lead says that lead was left as it was, which is false if a usable verdict for
  the same lead lands, and the engine's first-verdict-wins rule never sees a verdict rejected before
  the loop. So a usable verdict for a lead that also got a rejected, named verdict is skipped with its
  own failure line, whether it came before or after the rejected one in the reply. A rejected verdict
  with no usable `lead_id` names none, so it withholds nothing from any lead.
- **This changes one existing path.** A missing or null `verdict` today clamps to `needs_review`
  silently, moving the lead out of the default run's selection with no failure reported. After this
  it is dropped like any other unusable verdict.
- A **string** `verdict` outside the judge's vocabulary still clamps to `needs_review` through
  `clamp_verdict`, as it does today.
- `fit_reasoning`, `recommended_next_action`: a non-string becomes `""`, logged.
- `relevance_score`: an `int` stays; a finite `float`, or a string `int()` accepts after stripping,
  converts; anything else becomes `0`, logged, which is what today's `or 0` gives a missing score.
  `bool` is checked FIRST and rejected, because it subclasses `int`.
- `culture_flags`, `concerns`: §3.2.
- Any other key passes through untouched.

Given a verdict that is not a dict, or whose `verdict` field is unusable, `apply_verdict` writes
nothing and returns `"skipped"`. The engine never passes it one.

`cmd_triage_run` prints `failures=N` and each message and returns 0 whatever `report.failures` holds
(read from the code), so a rejected verdict is reported and leaves the exit code as it is, like every
other per-verdict failure today.

### 3.4 Hand-editing the framing

The vault is edited by hand as a first-class workflow, so a user can type `culture_flags` or
`triage_concerns` into a lead. A hand edit survives only while no triage run judges that lead:

- `core/status.py::DEFAULT_TRIAGE_STATUSES` selects `new`, `research` and `unjudgeable`, so the
  default run re-judges any lead still in one of those states and replaces both keys, writing `""`
  when the verdict has none. The typed text is recorded nowhere else.
- A `--status` selection that includes `shortlist` does the same to a shortlisted lead.

The supported workflow is therefore: set `status: shortlist`, then edit the two keys. That covers
the issue's own example, a lead a person moved to shortlist without running triage, which is why
`--framing-file` stays out.

**Accepted shape: one quoted line per key**, the shape triage writes: `culture_flags: "positive: a,
negative: b"` and `triage_concerns: "a; b"`. `core/vault.py::_fm_dict` is flat and line-based:
measured in review, a YAML block list reads back as `''` (no framing, silently) and a flow list
reads back as its literal bracketed text. This design documents the shape and pins both read
behaviours with tests. It does not widen `_fm_dict`, which is the store-wide lead parser.

**A hand-typed multi-line value is never overwritten.** `_set_fm` replaces only the key's own line. Writing
over a `triage_concerns:` line that is followed by `  - item` lines leaves those lines behind under a
plain value, and a YAML parser, which is what Obsidian uses, then refuses the note. Measured in review.
sluice's line-based reader carries on, so nothing else notices, and every later write leaves the
orphaned lines in place.

So `update_fields` gains a keyword, `preserve_block_values: frozenset | None`. For each named key
that is also in `fields`, the FRESH stored frontmatter is checked inside the CAS transform, once,
before any named field is written (`_set_fm` matches at any indentation, so an earlier write can move a
nested child line and change what a later check reads). A key whose next line is indented deeper than
the key's own line, or starts with `-` at the key's own indentation, holds a value spread over several
lines: a block list, a nested mapping, or a `|` or `>` block scalar. The key's own line is not
consulted, since a trailing `# comment` there is not a value. Such a key is left unwritten and logged, naming the note and the key, never the
value; the other fields still land. A key whose next line is another key at its own indentation is
written normally, whatever its value. `apply_verdict` passes
`frozenset({"culture_flags", "triage_concerns"})`. The user's value survives and the note stays
valid; the read side sees only the key's own line (`''` for a block list), so that lead gets no
usable framing until the user rewrites the key as one quoted line.

This is a Store contract change: `core/protocols.py`'s `update_fields` gains the keyword and its
obligation, and `tests/conformance/test_store_contract.py` gains a row. The existing test doubles are
unaffected: each `update_fields` defined under `tests/` either accepts `**kwargs` or belongs to a
double `apply_verdict` never writes through.

---

## 4. CV side

### 4.1 One derivation, used twice

`cv/engine.py::_run_one` reads `fm.get("culture_flags", "")` and `fm.get("triage_concerns", "")`
beside `company, role = ...`, so the engine stays the one place that says which lead keys cv reads.
It passes them to `framing_lines(culture_flags, triage_concerns) -> tuple[str, ...]` in
`cv/compose.py`, a pure formatter over two strings (named so it cannot shadow the `triage_framing`
parameter below). It returns up to two lines, each only when its value is non-blank after stripping:

```text
culture flags: <culture_flags, verbatim>
concerns: <triage_concerns, verbatim>
```

The two labels live in a `PROMPT`-named constant so the neutrality sweep reaches them (§6). Values
are rendered verbatim rather than split back into items: `culture_flags` is comma-joined and a flag
may itself contain a comma, so any split is a guess.

The same tuple goes to both consumers: the compose call (§4.2) and the sign-off snapshot (§5).

### 4.2 The prompt

`build_prompt` and `compose` gain a keyword `triage_framing=()`. `compose()` passes its arguments to
`build_prompt` one by one, so `triage_framing` has to be added to that call as well as to both
signatures; §7's `run_one`-level prompt row is what catches a forgotten forward. When it is
non-empty, and only then, `build_prompt` adds two things:

1. **A rules bullet**, `_TRIAGE_FRAMING_PROMPT_RULE`, through a `{triage_framing_rule}` placeholder in
   `_RULES` with the same trailing-newline shape as `_SKILLS_ATTRIBUTION_PROMPT_RULE`. The name ends
   in `_PROMPT_RULE` on purpose: `tests/test_cv_compose.py::test_each_gated_prompt_rule_is_its_own_bullet_in_the_rules_list`
   discovers rules by that suffix, and the plan widens that test to render with framing rather than
   renaming the constant out of its reach. Its text, in substance: the TRIAGE NOTES section is
   framing, not a source; use it only to choose which verified entries to lead with and which to
   play down; never cite it, never take a number or a name from it, never introduce a claim that
   rests on it, never describe the employer or its culture, and never state, paraphrase or allude to
   anything in it, including the candidate's preferences, criteria or reasons.
2. **A section after the JD**, before `=== SOURCE BUNDLE ===`, headed by
   `_TRIAGE_FRAMING_PROMPT_HEADER`:

   ```text
   === TRIAGE NOTES ON THIS ROLE (framing only; NOT citable, introduces no facts) ===
   - culture flags: ...
   - concerns: ...
   ```

**Constraints on the shipped text.** The rule, the header and the labels contain no `--`, no example
of a preference, criterion or reason, and never the word `auditing`, which the CV test doubles in
`tests/test_cv_engine.py` use to tell a compose prompt from an audit prompt. No `_EXEMPT` entry is
added to `tests/test_prompt_neutrality.py` for `sluice.cv.compose.build_prompt` or the new constants:
an exemption there would switch its term off for the whole CV prompt, so if a wording trips the
sweep, the wording changes.

With `triage_framing` empty, `build_prompt`'s output is byte-identical to today's, pinned by a
standing structural test (§7).

**Why outside the SOURCE BUNDLE.** The notes are lead data, not evidence. The bundle `b` is built only
from evidence reads, so keeping frontmatter out of `b` keeps non-citability structural rather than
parsed: `bundle_sources` (the gate's allowlist) and `render_bundle` (the audit's input) cannot reach
the notes. Secondarily, a section inside a block headed "the ONLY permitted source" would contradict
that label. The SKILLS INVENTORY is framing inside that heading by an earlier, deliberate decision
(#165); this design leaves it where it is.

### 4.3 What does not change, and what nothing checks

- **The gates.** `validate()` receives `sources = bundle_sources(b)`, derived from the bundle's
  structured entries, which never read the lead's frontmatter. The section can license nothing.
- **The advisory audit and the voice check.** `run_audit` receives `render_bundle(b)` and `run_voice`
  receives the scoped CV lines; neither is built from `build_prompt`.
- **#330's prompt artefact.** `prompt.attempt-N.txt` is written through `compose()`'s `on_prompt`
  callback, so it captures the section with no new code (asserted in §7).
- **Provenance.** Flags and concerns are the judge model's reading of the scraped JD against the
  user's Judging Profile, so they can carry the user's private criteria and reservations. Wherever the
  profile still holds the shipped default criteria text, either because there is no profile or
  because `job-sluice init` left a heading unanswered (`onboard/plan.py::_render_profile` keeps the
  default prose there), `core/criteria.py`'s default tells the judge to note in `concerns` that no
  role-shape criteria are available and to put its own sector observations there, and flag polarity
  is the model's call rather than the user's. A hand-edited value is whatever the user typed.
- **Residual, stated.** The rule forbids the composer mentioning anything in the notes, but no
  deterministic check sees a non-numeric echo: the HARD gate checks citations, figures and skills;
  the audit never sees the notes; a CV that raises no hold is never shown to anyone beside its
  framing. `_RULES` already forbids motivations and aspirations in the profile, which covers the most
  likely echo. A number present only in the notes, echoed into PROFILE prose or a WORK bullet, is
  refused by the HARD gate (§7's acceptance row); one the source set also holds (for a WORK bullet,
  one of its cited entries) passes, as a sourced figure should. A number echoed into a CERTIFICATES or EDUCATION line, or into a WORK company or
  `dates | location | role` line, is not checked, and is part of the residual.

### 4.4 #330 is merged

#330 merged on 2026-09-14 and shipped in 2.16.0, and this branch is rebased onto that release. On
`main`, `cv/engine.py::run_one` is a thin wrapper that owns the run's artefact record and `_run_one`
holds the body, which is where §4.1 and §5.1 land.

---

## 5. Sign-off shows the framing the composer was given

### 5.1 Snapshot at hold time

The framing is recorded **when the hold is stamped**, not re-read at sign-off:

- A re-triage or a hand edit between compose and sign-off would otherwise show something the
  composer never saw.
- #330's `prompt.attempt-N.txt` already records it, but `cv signoff` and MCP `cv_signoff` read the
  NOTE, and MCP cannot serve a file; `cv.output_dir` is one of the deliberately cwd-relative workspace
  paths, so a sign-off run from another directory cannot find the artefact; and two leads with the
  same company and role share one `_slug(company, role)` artefact directory, whose files a later run
  replaces.

It rides the existing `needs_signoff` JSON array as tagged entries, as #167's `style\t` entries do,
so `hold_for_signoff(ref, *, pending, claims)` is unchanged.

**One home for the tag: `sluice/core/leads.py`.** It holds the `framing` tag, `framing_entries(lines)`
(used by the writer in `cv/engine.py`, which already imports from that module) and
`split_framing(entries) -> (framing_lines, other_entries)` (used by `cli.py` and `mcpserver.py`).
A new `cv` module was the first design and cannot work:
`tests/test_mcpserver.py::test_mcpserver_imports_from_sluice_only_within_an_explicit_allow_list`
admits only `sluice.core.app`, `sluice.core.leads` and `sluice.core.status` into `mcpserver.py`.
`core/leads.py` already holds the content warnings `mcpserver.py`'s `cv_signoff` consumes, so this
needs no new module and no change to that guard. The guard's own docstring hand-counts the names
imported, which the added import makes stale; that count is deleted rather than updated. The existing
`style\t` tag is left where it is.

```python
claims = blockers + framing_entries(framing)
```

The hold condition stays `served and blockers`. **Framing never causes a hold**: a CV with framing
and no unsupported claim or style blocker renders and sets `tailored_cv` exactly as today. Every other
consumer of `needs_signoff` stays correct for that reason: the vault writes and deletes the value
verbatim, `core/app.py`'s dedupe and expire reports test it for truthiness (still true only beside a
real blocker), and `Sluice.sign_off_cv` passes the parsed list to its `confirm` callback unchanged.

### 5.2 CLI

`cli.py::_print_signoff_claims` calls `split_framing` first. Framing lines print first, under a
heading that says they are the triage notes the composer was given and are not claims. The remaining
entries keep today's style/fabrication split and wording, so a hold stamped before this ships prints
exactly as it does now (pinned by an exact-output row). `--yes` and `--discard` print no claims and
print no framing.

### 5.3 MCP

`mcpserver.py::cv_signoff` returns `claims` (the entries that are not framing) and a sibling
`framing` (the framing lines, tag stripped) everywhere it returns claims today: `needs_confirmation`,
`stale_confirmation`, and the `promoted`/`discarded`/`collision` outcomes. `_confirm_token` keeps
hashing the raw stored array, so a change in framing still stales a token.

The strings a client reads are updated so framing is never called a claim to relay: the registered
`cv_signoff_tool` description (what an MCP client actually reads, distinct from `cv_signoff`'s own
docstring, which is updated too), both `detail` strings, and the content warnings.

**The framing warning** is a new public constant in `core/leads.py`, beside
`USER_AUTHORED_CONTENT_WARNING` and shaped like it: a provenance clause naming both origins (the
triage model's reading of the job page against the user's Judging Profile, or text the user typed)
followed by the module's private `_NEVER_AN_INSTRUCTION` tail. Like the existing warnings, it carries
no subject; `mcpserver.py` supplies that where it uses the constant. It reuses neither
`UNTRUSTED_DERIVED_CONTENT_WARNING`, which calls its content text an LLM composed from a third-party
web page, nor `USER_AUTHORED_CONTENT_WARNING`, which says the content is not something sluice scraped
or composed.

**`get_lead`'s warning.** MCP `get_lead` returns the whole frontmatter under one warning that calls
all of it text copied from a third-party web page. After this change that frontmatter carries
`triage_concerns`, and a hold's `needs_signoff` carries framing entries, so the warning gains two
sentences: `culture_flags`, `triage_concerns` and each `framing` entry carry the framing warning's
provenance, and every other `needs_signoff` entry carries `UNTRUSTED_DERIVED_CONTENT_WARNING`'s.
`culture_flags` was already mislabelled before this change, and the same sentence corrects it.
`relevance_notes` keeps its existing scraped label rather than joining this group: `classify`'s
skip reason and other writers still copy scraped or agent-supplied text into it verbatim.
`get_lead`'s docstring and the registered `get_lead_tool` description say the same.

---

## 6. Neutrality

The section's content comes from the lead at run time; nothing ships a default. The shipped text is
the rule, the header and the two labels, all in `PROMPT`-named constants, all swept by
`tests/test_prompt_neutrality.py`, and all bound by §4.2's constraints:

- `_SYNTHETIC_ARGS["sluice.cv.compose.build_prompt"]` gains
  `"triage_framing": ("SYNTHETIC framing",)`, because the parameter defaults to empty and a
  defaulted render sweeps none of the new text.
- A floor assertion on the `sluice.cv.compose.build_prompt` entry specifically: its swept text
  contains the header and the rule text. Without it the override can go inert silently, which
  already happened once (`_render`'s override precedence, recorded in that function's docstring).
- The new constants' qualified names join `_KNOWN_PROMPTS`, so renaming one out of the constant
  discovery route goes red rather than silently leaving the labels unswept.

**Fixture values** are obviously synthetic tokens held in one constant in `tests/conftest.py`, beside
`LOCATIONS` (for example `positive: SYNTHETIC-FLAG-A` and `SYNTHETIC-CONCERN-A`): no culture, role or
work-style vocabulary, never the shipped few-shot's phrases, and never the word `auditing`. Every
existing verdict fixture that a §7 row asserts `culture_flags` or `triage_concerns` against moves onto
that constant, including the verdicts in `tests/test_apply.py::test_apply_verdict_writes_all_fields`
and `tests/test_frontmatter_write_sweep.py::test_an_ordinary_verdict_still_writes_both_fields`.
The acceptance row's figure is an arbitrary number with no rate, salary or currency wording. Companies
come from `tests/test_fixture_name_neutrality.py`'s reviewed roster. Roles are `Analyst`, the literal the
triage, CV and conformance test helpers already share; a row needing varied roles takes them from the
`titles` fixture.
`docs/USAGE.md`'s hand-edit example uses placeholder values, as §3.4 does.

---

## 7. Tests

Behaviour, asserted where the behaviour is observed: on the prompt a backend received, on the note a
store wrote, on what a command printed. Exact equality wherever a value is known; never the substring
shape a one-character fixture satisfies by accident. §3.1's key write lands before the §3.2 and §3.3
rows, so a concerns row is red for normalisation and not for a missing key.

**Triage: `normalise_verdict`**
- Parametrised over every field the judge schema names (`lead_id`, `verdict`, `relevance_score`,
  `fit_reasoning`, `concerns`, `culture_flags`, `recommended_next_action`) with wrong-typed values,
  asserting the exact normalised dict or `None`. Score rows include `True` (becomes `0`) and `"high"`;
  `"80"` and `80.0` are labelled pins, since no mutant of the fix changes them. List rows include a
  multi-character bare string, a dict, a mixed list, and an unsafe item beside a safe one.
- A second pass over its own output returns an identical dict and logs nothing.

**Triage: the engine**, driven through `triage/engine.py::run` with two leads and a backend keyed by
company rather than position, the malformed verdict on lead A:
- **Shapes that raise today** (`relevance_score: "high"`, `fit_reasoning: 7`,
  `recommended_next_action: 5`, `concerns: [1]`, `culture_flags: [{...}]`): both leads land at the
  backend's verdict; A's written values are exact; A's triage audit entry carries the normalised
  reason and score; `report.failures == []`.
- **Coercions** (`relevance_score: True`, a bare-string concerns, a dict flags, a mixed list, an
  unsafe item beside a safe one): per row, the expected status, score, key value and
  `relevance_notes` fragment.
- **Unrepairable verdicts**, parametrised over a non-dict element (a string, a number), `lead_id`
  missing, `""`, a number or a list, and `verdict` missing, `None`, a number, a list or `""`: the
  other lead lands; `report.failures` equals exactly the rejection line followed by the no-verdict
  line; no "no note matches this lead_id" line; the raw value appears in neither; the rejected lead's
  note is unchanged.
- A dropped list item is logged with the field name and the lead's slug and without the value.

**Triage: `apply_verdict`**, called directly on a real `Vault`:
- It writes `triage_concerns`; a later verdict with none clears it; a note seeded with an earlier
  value, given one unsafe and one safe item, ends holding exactly the safe one, for both keys; given
  a verdict whose `verdict` field is unusable, it writes nothing and returns `"skipped"`.
- A note whose frontmatter nests an indented `concerns:` under another property keeps that line and
  its siblings byte-intact. Witness: write the key as `concerns`.
- **Multi-line values:** `triage_concerns` and `culture_flags` hand-typed as a block list (items
  indented deeper than the key, and at the key's own indentation) and as a `|` block scalar survive
  byte-intact while status and score still land; the note still parses as YAML; a warning names the
  key. A key holding `""` followed by another top-level key is written normally. Witness: drop
  `preserve_block_values` from `apply_verdict`'s call.
- `tests/conformance/test_store_contract.py` pins `preserve_block_values` as a store obligation.
- `tests/test_frontmatter_write_sweep.py::test_the_triage_verdict_fields_cannot_inject_frontmatter`
  covers `triage_concerns`.
- A newly created note carries `triage_concerns: ""`.
- Hand-edit reads, through a real `Vault`: a one-line quoted value frames; a block list reads as `''`
  and frames nothing; a flow list reads as its literal text.

**Triage witnesses.** Deleting the `normalise_fields` call inside `apply_verdict` reddens the direct
`apply_verdict` rows. Deleting the engine's call reddens the audit-entry assertions and the
unrepairable rows. Replacing both calls with a catch-all around the engine's `apply_verdict` call
(the rejected fix in place of the chosen one) reddens A's written-value assertions while B's stay
green. Deleting the item filter reddens the seeded rows: the joined check then skips the key and the earlier
value survives. The injection-sweep row stays green under that mutation, because the joined check
still refuses the unsafe value; it is a regression pin, not the filter's witness.

**CV**
- `framing_lines`: both values; flags only (one line, no concerns label); concerns only; a
  whitespace-only value is omitted; both blank gives `()`.
- **Round trip, an integration pin:** `apply_verdict` writes both keys onto a lead note in a real vault
  built with `tests/test_cv_engine.py::_vault_with_candidate`, `read_leads({"shortlist"})` reads it
  back, and `run_one` with a recording backend puts both lines in the compose prompt. It has no unique
  witness: renaming the key in `apply_verdict` also reddens the `apply_verdict` key rows and the
  engine's concerns rows, and renaming it in `_run_one` also reddens the `run_one` rows.
- Through `run_one`: a lead with framing puts the header, the rule (including its no-mention sentence)
  and both lines in the compose prompt; a blank lead puts none of them there (the mirror control).
- `prompt.attempt-1.txt` contains the section.
- **Byte identity, standing:** `build_prompt(..., triage_framing=())` equals the call without the
  keyword; and a framed render's lines, minus exactly the rule's lines and the section block (its blank
  separator, header and bullet lines), equal the unframed render's lines.
- `test_each_gated_prompt_rule_is_its_own_bullet_in_the_rules_list` renders with `triage_framing` set,
  with the bullet that follows the new splice point added to its neighbour list, so its whole-line and
  `--` checks cover the new rule.
- **The acceptance row:** a figure present only in `triage_concerns`, composed into PROFILE prose.
  Assert the figure appears inside the TRIAGE NOTES section of the compose prompt (the wiring witness),
  a violation starting `INVENTED PROFILE METRIC <figure>`, and nothing rendered. Control: the same note
  with a CV lacking the figure reaches `rendered`. Witness: feed the framing lines into `build_bundle`'s
  baseline argument; this row and the audit row must go red.
- The audit prompt contains neither the header nor the framing lines.

**Sign-off**
- A hold stamped with a blocker writes the framing entries after the blockers.
- **Snapshot:** a backend whose compose call assigns `note.fm["triage_concerns"]` IN PLACE (so a
  re-read at the hold site would see it; `_run_one` binds `fm = note.fm`), with an audit reply that
  holds the CV. The stored framing entries equal the lines in the compose prompt, not the changed
  value. Witness: re-derive framing from `note.fm` at the hold site.
- Framing with no blocker: the prompt carried the framing, and the result is `rendered` with
  `tailored_cv` set and no `needs_signoff`. Witness: move the framing entries into `blockers`; this row
  must then hold the CV.
- `split_framing`: mixed entries; no framing; non-string entries (the key is hand-editable) go to the
  other group without raising.
- **CLI**, in `tests/test_cv_signoff_prompt.py`: the exact-output row for a legacy hold (unsupported and
  style entries, no framing) is committed BEFORE `_print_signoff_claims` changes, so "today's output"
  is recorded from today's code. The framing row asserts exact counts: framing under its own heading,
  counted as neither an unsupported claim nor a style concern.
- **MCP**, in `tests/test_mcpserver.py`:
  - `needs_confirmation` returns `framing` separately and `claims` without framing entries.
  - A re-hold with byte-identical `pending_cv` and non-framing entries, changing only a framing entry,
    yields `stale_confirmation`. Witness: `_confirm_token` hashing only the other entries.
    `test_cv_signoff_tool_stale_token_after_a_re_hold_writes_nothing` changes both, so it cannot see
    that mutant.
  - A discard of a hold carrying a blocker and a framing entry returns `claims` without it and
    `framing` with the tag stripped, following
    `test_cv_signoff_tool_discard_returns_claims_with_content_warning`.
  - The registered tool description names framing, read through `build_server` and `list_tools` as
    `test_no_tool_description_denies_the_propose_tool_that_is_registered` does.
- `tests/test_core_leads_content_warning.py`: the framing warning ends with the shared
  `_NEVER_AN_INSTRUCTION` tail, has its own provenance clause, and does not contain the derived
  warning's third-party-web-page wording.

**Neutrality** as §6, with the witness deleting only the `triage_framing` key from `_SYNTHETIC_ARGS`,
never the whole `build_prompt` entry.

Every witness follows this repo's method: move or delete, never add, with content-hashed `.pyc` first.

## 8. Documentation and release

- `sluice/cv/compose.py` module docstring: state the property rather than extend a list. The SOURCE
  BUNDLE is the only citable source; everything else in the prompt (the JD, the triage notes) is
  framing the gate cannot license.
- `sluice/cv/artefacts.py`: its note that a prompt carries the whole bundle and the contact block also
  covers the lead's triage notes.
- `sluice/core/protocols.py`: `update_fields`' docstring gains the `preserve_block_values` obligation.
- `docs/ARCHITECTURE.md`:
  - the cv section's composer paragraph (where the notes sit and why);
  - the #60 sign-off paragraph (the `framing` kind, owned by `core/leads.py`);
  - item 2's "`apply.py` writes verdicts back, skipping any lead already in the application lifecycle"
    sentence (verdicts are validated first, and an unusable one is dropped);
  - the passages describing `update_fields`' `require_blank` guard gain `preserve_block_values`.
  - Its MCP section names `cv_signoff` without describing its response, and stays that way; the shape
    lives in `docs/USAGE.md` and `mcpserver.py`.
- `docs/USAGE.md`: the `prompt.attempt-N.txt` row (stated as a property, not a list); `cv signoff` (the
  framing heading); the hand-edit workflow in the cv section (the one-line shape, set `shortlist`
  first, a block list is left alone and frames nothing); the MCP `cv_signoff` line.
- `docs/TROUBLESHOOTING.md`: the list of upstream causes for a recurring gate finding gains the lead's
  own `culture_flags`/`triage_concerns`, the one a user fixes by editing the note.
- `README.md`: the sample lead note's `triage_concerns: ""`.
- `.rulesync/rules/CLAUDE.md`: the fabrication-gate paragraph names the triage notes beside the SKILLS
  INVENTORY as outside the source set, with §4.3's scoped wording on echoed numbers; the never-clobber
  paragraph names `preserve_block_values` beside `require_status`. Regenerate afterwards.
- **In-code statements this makes false**, each checked present in the tree:
  - `triage/apply.py::apply_verdict`'s "Abstain on the FIELD, never the write" comment (items are now
    abstained on one by one);
  - `cli.py::_print_signoff_claims`' docstring ("carrying two shapes of entry"; "An entry with NO
    "style\t" prefix keeps EXACTLY today's");
  - `cv/engine.py::_run_one`'s comment above `style_blockers` ("An entry with NO such prefix is
    exactly");
  - `mcpserver.py::_confirm_token`'s docstring (the tuple it hashes against the `claims` the response
    now carries);
  - `mcpserver.py`'s "#131 decision 16" comment;
  - `triage/engine.py::run`'s "off the same raw `verdict` dict" comment;
  - `core/leads.py`'s comment listing what the derived warning covers;
  - the hand count in `tests/test_mcpserver.py`'s isolation-sweep docstring (deleted).

  Beyond this list, each changed claim is grepped for, not only the code that changed.
- **Release note.** No config key gates this, so the release PR's changelog entry says that CVs for
  already-triaged leads now use their `culture_flags` and `triage_concerns`, that blanking both keys
  opts a lead out, and that a triage verdict missing its verdict field is now dropped and reported
  rather than moving the lead to `needs_review`. The entry is checked on the changelog file, the tag
  and the release body.
- **Commit types.** `feat(triage)` for §3.1's key, `fix(triage)` for §3.2 to §3.4, `feat(cv)` and
  `feat(mcp)` for §4 and §5, `docs:` for documentation-only changes. None takes `!`: none of
  `CHANGELOG.md`'s breaking classes (what an empty value means, a load-bearing default, where a file is
  read or written, what a status transition may do) is touched. The dropped-verdict change narrows
  what triage writes on a malformed model reply; it does not change which status transitions are
  permitted.

## 9. Out of scope

- Any change to `validate()`, `bundle_sources`, `render_bundle` or the audit's corpus.
- Any other change to the Store protocol: `preserve_block_values` (§3.4) is the one addition.
- A `--framing-file` flag (§3.4).
- Widening `core/vault.py::_fm_dict` to read YAML lists, or `_set_fm` to rewrite them.
- Moving the existing `style\t` tag into `core/leads.py`.
- Relabelling any MCP warning other than `cv_signoff`'s and `get_lead`'s (§5.3). `list_leads` returns
  only company, role and url, which are scraped.
- Refreshing `culture_flags`/`triage_concerns` on already-shortlisted leads.
