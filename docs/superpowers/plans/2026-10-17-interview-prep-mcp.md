# Interview prep over MCP (#195 PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship PR 2 of #195:
- a read-only `prep_brief` MCP tool over `Sluice.prep_brief`;
- an argument-free `interview_prep` coach prompt built from a new `rehearse.md` playbook;
- a separate prep eval runner (`scripts/coach_eval/prep.py`), with its seeder, rubric, grader and
  personas.

**Architecture:** The tool and the prompt register in `sluice/mcpserver.py::build_server` beside
`get_lead` and `career_interview`, at every privilege level. The prompt text comes from a new
`onboard/coach` assembler that imports no backend. The eval runner is a second runner that reuses
`run.py`'s stateless helpers. The setup loop changes only in the edits listed in spec §10.

**Tech Stack:** Python 3.12+ stdlib. `mcp==2.3.0` comes from the `[test]` extra. pytest, ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-10-16-interview-prep-design.md`, §9, §10 and "Testing (PR 2)".
It is the authority on every name, rule and test row. PR 1, merged in `ae80ce7e`, built the facade
this PR drives.

**Research (exact code facts, quoted from the tree at `ae80ce7e`; read the one your task names):**
- `.superpowers/sdd/pr2-research/mcp.md`: mcpserver registration shapes, the warning constants,
  the roster and prompt pins.
- `.superpowers/sdd/pr2-research/coach.md`: the coach assembler, the playbook format, and every
  `PLAYBOOKS`-keyed guard, with its exact assertion and its wording traps.
- `.superpowers/sdd/pr2-research/evals.md`: `scripts/coach_eval` (run, personas, rubric, serve,
  isolation) and every `tests/test_coach_eval.py` pin.

## Global Constraints

- `sluice/` stays standard-library only. `mcp` stays imported lazily inside `build_server`.
- **No personal data.** Companies in tests and personas come from `_REVIEWED_FIXTURE_IDENTITIES`,
  roles from the seeded `titles` fixture or faker seeds, and places from `tests/conftest.py`'s
  `LOCATIONS`. No line-number citations in comments; cite `file.py::symbol`. No ledger or ruling IDs
  in shipped comments.
- **The read-only tool never writes.** It calls `sluice.prep_brief(lead=lead)` with no `write`
  keyword, and every outcome is structured. It never returns a path.
- **The coach text names no role, sector or employer**, and expresses no opinion about which
  competencies, values or answer content matter. Watch the wording traps listed in
  `pr2-research/coach.md` §3:
  - the `NO_TAXONOMY_WORDS` word list: "manager", "staff", "platform" and the rest;
  - the `FORBIDDEN_ROLE_AND_CULTURE_TERMS` substrings: "dora", "team lead" and the rest;
  - the retired-form sweep's "tick" and `\bforms?\b`;
  - the example markers, including the new "etc." and "and so on".
- **No fixed MCP prefix in `sluice/`.** Write `/mcp__<name>__interview_prep`. In docs only
  `/mcp__job-sluice__...` is allowed, and only beside the naming-rule paragraph
  (`tests/test_coach_command_prefix.py`).
- **No paid eval run.** Running `scripts/coach_eval` live spends the owner's allowance and needs
  their go-ahead, so this PR ships the runner and its offline tests only.
- **Commits.** Conventional commits; each body includes `Refs #195` (never a closing keyword) and
  ends with `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- **`$VENV`.** It is the main checkout's `.venv`. Run tests with `PYTHONPATH=$PWD $VENV/bin/python -m pytest -q`
  from the worktree, and lint with `$VENV/bin/ruff check sluice tests scripts`.
- **Witness discipline:**
  - implement, run green, COMMIT, then run the witnesses;
  - run `$VENV/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
    once first;
  - make each mutant with a script;
  - restore it with the Edit tool (the repo's PreToolUse hook blocks `git checkout`/`git restore`
    of a dirty file) and confirm with `git diff --quiet`;
  - record a `mutant -> failing test` table in the task's commit message (`git commit --amend`;
    the commit is unpushed).
- Never `git stash`, never push, never dispatch subagents.

## Review Focus

1. **A coach that guesses a slug fragment first.** `prep_brief` returns `not_found` with bounded
   `suggestions` and a `suggestions_warning`, and the rubric's slug check still passes once the
   coach calls with the real slug. Owned by Task 1 and Task 3.
2. **A read-only server.** `interview_prep`'s served text never names `propose_evidence`, and gives
   the `stories add` command instead. Owned by Task 2.
3. **An exported `VAULT_DIR`/`SLUICE_CONFIG` on the developer's machine.** The eval seeder must
   write nothing there. Owned by Task 3.
4. **A registered tool the eval rosters do not place.** The roster tests at BOTH levels go red.
   Owned by Task 1 (setup's `DISALLOWED_SLUICE_TOOLS` gains `prep_brief`) and Task 3 (prep's
   tuples).
5. **A story body holding markup reaching the coach.** It arrives as structured data with
   `entries_warning`, never as a path. Owned by Task 1.

## Amendments from the plan review (binding; override the task text where they differ)

**B0.** Every finding in the plan review's five findings files is binding unless a line below
overrides it, and each task's implementer reads the findings that touch its task. The files are
`~/.cache/sluice/review-plan/2026-10-17-interview-prep-mcp/20261017T134005Z-64109/findings/*.json`.

**B1, which witness discriminates (Task 3).** "Compute `disallowed` from `list_tools()`" cannot go
red, and is dropped. The witness is "register a new tool on the server and place it on NEITHER
hand-listed tuple", which must go red at both levels. No witness list may end in a placeholder:
copy each witness the spec names for Task 3's rows into the task's commit table.

**B2, `git add` before the suite.** Some guards read `git ls-files`, which sees only tracked files.
Two of them are the fixed-MCP-prefix sweep and the sdist row, so `git add` every new file before
running the suite.

**B3, the prompt guards.** The no-preference sweep of `assemble_prep_prompt` (both levels) goes
INSIDE `test_the_assembled_prompt_names_no_preference`, which
`tests/test_renderer_template.py`'s `covered` dict certifies by name. Add two rows that kill the
surviving mutants:
- the prep prompt contains NO setup playbook's text;
- the read-only prep prompt contains `stories add`, while the write-level prompt names
  `propose_evidence`.

**B4, the write-level capture note.** It gets a named constant, `PREP_WRITE_NOTE`, appended when
`write` is true, and added to `shipped_prose`'s coach tuple. It says: offer `propose_evidence` for a
new story in the user's own words; it lands in `_inbox/`; and it appears in a brief only after
`job-sluice stories verify`. Pin each of those three clauses in the served write-level text.
`rehearse.md` itself names neither tool.

**B5, a warning for `brief.lead`.** The ok reply's `brief.lead` carries the scraped company and
role, so it gets its own warning key, `lead_warning`, built on `UNTRUSTED_SCRAPED_CONTENT_WARNING`.
It joins the registered-descriptions sweep beside the other three. Ruling: this follows the
one-key-per-provenance pattern of `cv_run`/`cv_signoff`, and costs one more key if it is wrong.

**B6, prose to correct** (on top of Task 1's list). Each is reworded to point at the registered
roster, or to name the new reader, with no count:
- `docs/USAGE.md`'s read-only tool roster and its career-coach section;
- `docs/MCP.md`'s tool table;
- `build_server`'s docstring;
- `tests/functional/test_mcp_contract.py`'s module docstring;
- `docs/ARCHITECTURE.md`'s `coach/` bullet;
- the coach module docstring ("setup_save ... the only route to a write"; "building the prompt
  imports the backends", which is now true of `assemble_prompt` only);
- every sentence that says stories are read by `leads prep` ALONE, which now also has the
  `prep_brief` MCP tool. Grep `leads prep` across `sluice docs .rulesync/rules`.

**B7, the eval runner (Task 3).**
- **Unknown runner.** A saved run whose scorecard names an unknown `runner` is REFUSED with a clear
  error and never falls back to setup, and a row pins that.
- **Seeder raises.** The seeder raises when `upsert` is not `created`, when the JD append returns
  `False`, and when any read-back check fails. Stories go under the vault's
  `Job Applications/STAR Stories/`, the `EVIDENCE_KINDS["stories"].relpath`.
- **Persona guard.** Every persona-guard row carries a scope assertion that it found the prep
  personas, so it cannot pass on an empty list. Each story's past-employer `company` is checked
  against `_REVIEWED_FIXTURE_IDENTITIES`.
- **Board-name guard.** The extension stays whole-file and is never narrowed. Its failure message
  says a match on an ordinary word is fixed by rewording the persona, never by changing the guard.

---

### Task 1: The read-only `prep_brief` MCP tool

**Files:**
- Modify:
  - `sluice/mcpserver.py`: the module function, the `_tool` closure registered beside `get_lead`,
    and three warning constants;
  - `scripts/coach_eval/run.py`: `DISALLOWED_SLUICE_TOOLS` gains `"prep_brief"`, so the setup coach
    can never call it and the existing roster test stays exact;
  - `README.md` (`## MCP server`), `docs/ARCHITECTURE.md` (the first MCP roster paragraph) and
    `docs/MCP.md` ("That is the whole surface without `--write`"): rewrite each stale roster
    sentence to point at the registered roster instead of restating it, wherever the sentence
    allows (spec §9 "Prose corrected"). Also correct the `mcpserver.py` module docstring and
    `list_evidence`'s allow-list sentence.
- Test:
  - `tests/functional/test_mcp_contract.py`: the three exact read-tier tool-set equalities, plus
    new SDK rows;
  - `tests/test_coach_prompt.py::test_registered_descriptions_name_no_preference`: the tool's
    description and the three warnings join it by name;
  - `tests/test_coach_eval.py`: its roster tests must stay green with the one-line `run.py` edit.
- Read first: `.superpowers/sdd/pr2-research/mcp.md`.

**Interfaces:**
- Consumes: `Sluice.prep_brief(*, lead, write=False) -> BriefResult`. Its fields are `outcome`,
  `slug`, `status`, `candidates`, `suggestions`, `more_suggestions`, `brief`, `jd_source`,
  `jd_text` and `reason`. The outcomes without a write are `ok | not_found | ambiguous |
  entries_unreadable`.
- Produces: `sluice.mcpserver.prep_brief(sluice, lead: str) -> dict` and a tool registered as
  `prep_brief`, whose schema is exactly `{lead}`.

- [ ] **Step 1: Write the failing tests.**
  - The tool rosters: in the three exact equalities in `test_mcp_contract.py`, at both levels, add
    `prep_brief`, and pin its input schema exactly as `{"lead"}` required.
  - SDK rows, each asserting no path anywhere in the serialised response:
    - `ok`, where `jd_warning` and `entries_warning` are present, `jd` equals
      `{"source": r.jd_source, "text": r.jd_text}`, `brief` equals the facade's `to_json`, and the
      reply's top-level key set is asserted exactly;
    - `not_found`, with `suggestions` and `suggestions_warning`;
    - `ambiguous`;
    - `entries_unreadable`, from a chmod-000 story file marked `UNREADABLE_DIR`.
  - The read-only row: on a WRITE-READY fixture, built as PR 1's tests do (a story with a
    `Company:` line and a JD section), the note is byte-identical after a tool call at both levels.
    An in-test control shows `Sluice.prep_brief(lead=..., write=True)` on the same fixture does
    write.
  - An AST pin over `mcpserver.py`. It finds exactly ONE call whose function is the `prep_brief`
    attribute on the facade object, and asserts that its keywords are exactly `[lead]`: no `write`,
    no `**` (a keyword whose `arg` is `None`), no `*args`.
  - The registered-descriptions sweep includes the tool's description and the three warning texts,
    by name, with a scope assertion that each was found.

- [ ] **Step 2: Run them, and confirm they fail.**

- [ ] **Step 3: Implement.** Mirror `get_lead`'s shape exactly (see `pr2-research/mcp.md` §1).

```python
_PREP_JD_WARNING = (
    f"The `jd` text, whether it came from the note's own section or a cached page, "
    f"{UNTRUSTED_SCRAPED_CONTENT_WARNING}")
_PREP_ENTRIES_WARNING = (
    f"Each entry's title, fields and body {USER_AUTHORED_CONTENT_WARNING} An entry marked "
    f"`verified: false` has not been reviewed through `job-sluice <kind> verify`: ask the user "
    f"before treating it as settled.")
_PREP_SUGGESTIONS_WARNING = (
    f"Each suggested slug is built from a lead's company and role, which "
    f"{UNTRUSTED_SCRAPED_CONTENT_WARNING}")


def prep_brief(sluice: Sluice, lead: str) -> dict:
    """One lead's interview prep brief by EXACT slug, read-only: never writes, at any level."""
    r = sluice.prep_brief(lead=lead)
    out = {"outcome": r.outcome, "slug": r.slug, "status": r.status,
           "candidates": r.candidates, "suggestions": r.suggestions,
           "more_suggestions": r.more_suggestions, "reason": r.reason,
           "brief": r.brief, "jd": {"source": r.jd_source, "text": r.jd_text}}
    if r.outcome == "ok":
        out["jd_warning"] = _PREP_JD_WARNING
        out["entries_warning"] = _PREP_ENTRIES_WARNING
    if r.suggestions:
        out["suggestions_warning"] = _PREP_SUGGESTIONS_WARNING
    return out
```

  Register it beside `get_lead`, before `if write:`. Its closure docstring is the registered
  description. That description says the tool is read-only, takes the exact slug (the note's
  filename) that `list_leads` returns, and that a miss returns suggestions; and it points at the
  warnings. Check the real constant names in `sluice/core/leads.py` before relying on these.
  Import whatever is needed from allow-listed modules only.

- [ ] **Step 4: Run green, then commit.** Run the full suite and ruff, then commit
  `feat(mcp): a read-only prep_brief tool over the interview prep brief (#195)`.

- [ ] **Step 5: Witnesses (after the commit).**
  - pass `write=True` in the tool (the read-only and AST rows go red);
  - drop each warning (red);
  - drop `prep_brief` from `DISALLOWED_SLUICE_TOOLS` (the eval roster test goes red);
  - drop a key from the reply (the exact key-set row goes red).

  Amend the commit message with the table.

### Task 2: The `interview_prep` prompt, its playbook and every playbook guard

**Files:**
- Create: `sluice/onboard/coach/rehearse.md`.
- Modify:
  - `sluice/onboard/coach/__init__.py`: `PREP_PLAYBOOKS = ("rehearse",)`, the module docstring,
    and `assemble_prep_prompt(*, write: bool = True, read=read_playbook) -> str`;
  - `sluice/mcpserver.py`: register `interview_prep`, with no arguments, beside `career_interview`;
  - `sluice/onboard/coach/handoff.md`: correct the "experience, skills and stories" CV sentence,
    keeping its `evidence`-before-`form` shape (`pr2-research/coach.md` §2);
  - `.rulesync/rules/CLAUDE.md`: in the sentence on mcpserver importing `sluice.onboard`, name
    `interview_prep` and state that `assemble_prep_prompt` imports no backend; then run
    `npm run rulesync`;
  - `docs/MCP.md`: the prompt, documented for Claude Code only, the client where it is MEASURED
    (owner's rule: no entry for an unmeasured client). Keep the naming-rule paragraph, and keep
    `/mcp__job-sluice__career_interview` present.
- Test: every guard in `pr2-research/coach.md` §3–§10.
  - `tests/test_coach_prompt.py`:
    - the found-and-used equality becomes `== set(PLAYBOOKS) | set(PREP_PLAYBOOKS)`;
    - per-assembler containment: `rehearse` is in the prep prompt at both levels, and NOT in
      `assemble_prompt()`;
    - both text-only sweeps parametrise over the union, with a scope assertion;
    - `_EXAMPLE_MARKERS` gains `"etc."` and `"and so on"`;
    - the no-preference sweep covers `assemble_prep_prompt` at both levels, inside or beside
      `test_the_assembled_prompt_names_no_preference`, whose NAME must stay;
    - a planted-word twin plants into `"rehearse"`;
  - `tests/test_onboard_questions.py`: two DISTINCT `rendered:prep_prompt(...)` labels, the count
    7 -> 9, and `PREP_PLAYBOOKS` mapped to both prep surfaces;
  - `tests/onboard_prose.py`: `_prep_prompt_text(write)`, the two surfaces, `PREP_PLAYBOOKS` in
    `_NOT_PROSE`, and any new str constant added to the coach tuple in `shipped_prose`;
  - `tests/test_renderer_template.py`: `shipped == union`;
  - `tests/test_packaging.py`: a row that `rehearse.md` is among `_expected_playbooks()`, in the
    wheel and in the sdist;
  - `tests/functional/test_mcp_contract.py` and `tests/test_ai_setup_contract.py`: the prompt roster
    `{"career_interview", "interview_prep"}` at both levels, selecting by name rather than by
    tuple-unpack;
  - `interview_prep` has no arguments, and its served text names `prep_brief`, plus
    `propose_evidence` at the write level ONLY;
  - a subprocess row: `assemble_prep_prompt` at both levels loads neither `sluice.core.backends`
    nor `sluice.backends`, with an `assemble_prompt()` control row that does.
- Read first: `.superpowers/sdd/pr2-research/coach.md` and spec §9 "The playbook" / "neutrality".

**Interfaces:**
- Produces: `coach.PREP_PLAYBOOKS`, `coach.assemble_prep_prompt(*, write=True, read=read_playbook)`,
  and an MCP prompt named `interview_prep`.

- [ ] **Step 1: Write the failing tests.**

- [ ] **Step 2: Confirm they fail.**

- [ ] **Step 3: Implement.**

```python
PREP_PLAYBOOKS = ("rehearse",)
PREP_READ_ONLY_NOTE = (
    "This sluice server is read-only, so it cannot save a story. When the user tells a story "
    "worth keeping, give them the command to add it themselves: `job-sluice stories add`, "
    "then `job-sluice stories verify` to include it in a brief.")


def assemble_prep_prompt(*, write: bool = True, read=read_playbook) -> str:
    """The interview_prep prompt: PREP_PLAYBOOKS only (never the setup playbooks), plus a
    read-only note. Imports no backend and never raises -- unlike assemble_prompt it needs no
    backend requirement text."""
    parts = [read(n).strip() for n in PREP_PLAYBOOKS]
    if not write:
        parts.append(PREP_READ_ONLY_NOTE)
    return "\n\n".join(parts) + "\n"
```

  The playbook must name `propose_evidence` at the write level only. Write `rehearse.md` without
  naming `propose_evidence`, and give the write-level capture rule through a second constant
  appended when `write` is true. Add each new constant to `shipped_prose`'s coach tuple.

  Write `rehearse.md` in the coach's phase format (H1, `##` sections, a `## Done when` for each,
  one paragraph per line). Its phases are spec §9's six, in order:
  1. choose the lead (find the slug with `list_leads`);
  2. read the brief with `prep_brief`;
  3. ask, openly, how the interview will run (no list of formats);
  4. name the JD requirements no entry evidences, and ask for a real example (never supply one);
  5. rehearse, giving feedback only on completeness (situation, the user's own action, outcome) and
     fidelity to the note; never treat an unverified entry as settled;
  6. capture.

  Use the placeholder form `/mcp__<name>__interview_prep` if a command is named. Respect every
  wording trap in Global Constraints.

  Register the prompt in `build_server`:

```python
    @mcp_server.prompt(name="interview_prep")
    def interview_prep_prompt() -> str:
        """Prepare for a job interview: rehearse your own stories against one lead's job
        description, and find the requirements you have no story for yet. Type the command on
        its own; the coach asks which interview it is."""
        return coach.assemble_prep_prompt(write=write)
```

- [ ] **Step 4: Run green, then commit.** Run the full suite and ruff, run `npm run rulesync`
  (if node_modules is missing: `npm ci --ignore-scripts`), then commit
  `feat(mcp): an interview_prep coach prompt over the prep brief (#195)`.

- [ ] **Step 5: Witnesses (after the commit).**
  - add `rehearse` to `PLAYBOOKS` (the not-in-career_interview row goes red);
  - name `propose_evidence` in `rehearse.md` (the read-only row goes red);
  - plant "etc." in `rehearse.md` (the example-list sweep goes red);
  - call `_units()` from `assemble_prep_prompt` (the subprocess row goes red);
  - drop the `interview_prep` registration (both roster tests go red).

  Amend the commit with the table.

### Task 3: The prep eval runner, `scripts/coach_eval/prep.py`

**Files:**
- Create:
  - `scripts/coach_eval/prep.py`;
  - `scripts/coach_eval/personas/prep/` with three persona JSON files: strong verified stories at
    the write level; gaps; only unverified stories at read-only.
- Modify (spec §10, "What changes in run.py", listed rather than claimed away):
  - `scripts/coach_eval/run.py`:
    - `coach_args` gains keywords `tools`, `disallowed` and `allowed`, defaulting to run's own
      constants;
    - extract `write_mcp_config(sandbox, server_args) -> Path`;
    - `_saved_runs` yields each run's `runner`, read from its scorecard (missing means setup);
    - `regrade` and `print_grader_prompts` dispatch on that `runner`, with function-local imports
      of prep;
    - `main()` gains `--prep-persona ID` and `--prep-all`, and `--grader-model`/`--out` reach the
      prep entry point;
  - `scripts/coach_eval/serve.py`: `--read-only` runs the server without `--write`;
  - `scripts/coach_eval/personas.py`: `PrepPersona` and `load_prep_personas`;
  - `scripts/coach_eval/rubric.py`: `prep_brief_ok(events)` and
    `prep_deterministic(events, *, slug, write, kept, max_turns)`;
  - `scripts/coach_eval/README.md`: both runners, the prep persona directory, the read-only flag,
    and each runner's scorecard keys.
- Test: `tests/test_coach_eval.py`, with every row in spec "Testing (PR 2)" → "The eval harness".
- Read first: `.superpowers/sdd/pr2-research/evals.md`, then spec §10 in full. It is the binding
  design for every name: `prep.kept(write)`/`prep.disallowed(write)` built from HAND-LISTED tuples;
  `seed_vault(persona, sandbox, *, store=None)`; `NO_BRIEF_FACT`; `grade_prep`;
  `prep.run_persona(p, out_dir, grader_model)`; `copy_prep_files`; `"runner": "prep"`.

**Interfaces:**
- Consumes: the `prep_brief` tool (Task 1) and the `interview_prep` prompt (Task 2).
- Produces: `scripts/coach_eval/prep.py`, the public surface spec §10 names.

- [ ] **Step 1: Write the failing tests** (spec "Testing (PR 2)": eval harness rows).
  - **Setup is unchanged except as listed.** Every existing setup test passes unchanged, and
    `coach_args()` with no keywords equals the argv built from run's own constants.
  - **One patch governs both runners:** patch `run._claude` to a recording fake plus a
    `subprocess.run` tripwire, then drive one stubbed prep persona end to end.
  - **Rosters at both levels** use the hand-listed tuples, and the persona ids of the two runners
    are disjoint.
  - **`--tools ""`:** the init check fails when WebSearch or a disallowed tool is present.
  - **`PrepPersona`'s refusals**, parametrised over every story field.
  - **The seeder is isolated.** Decoy `VAULT_DIR` and `SLUICE_CONFIG` are written to nothing; the
    read-back runs after `delenv`; the url host is `jobs.example.com`; and the raise rows fire,
    via `seed_vault(..., store=spy)`.
  - **The simulated user's prompt** carries every story body verbatim.
  - **The rubric:** an all-pass row and an each-check-fails parametrisation, including a not_found
    fragment followed by an ok call that passes, and the slug check reading NOT_EXERCISED with no
    successful call.
  - **The grader:**
    - the facts come from the first `ok` call;
    - the `NO_BRIEF_FACT` row;
    - the overwrite rows, which hold in a regrade too;
    - the tool-result payloads are built by CALLING the real `mcpserver.prep_brief(sluice, lead)`
      on a seeded sandbox vault.
  - **The flags reach the runner:** the grader model and `--out` reach it, and the print-grader
    path writes `prep_grader_prompt`'s exact text.
  - **Regrade dispatch** goes by `runner`.
  - **`serve.py --read-only`.**
  - **The persona guard** (spec §10 "Persona content"): rosters, `LOCATIONS`, placeholders only in
    the JD template, `expresses_a_preference` over the raw template and every story's title,
    best_for and body, the board-name guard extended to `personas/prep/` with a scope assertion,
    and a per-persona digest.

- [ ] **Step 2: Confirm they fail.**

- [ ] **Step 3: Implement**, per spec §10. The seeder:
  - constructs `Vault(str(run.sandbox_vault(sandbox, vault_env=False)))` explicitly through its
    `store=` seam;
  - creates the lead with `upsert`;
  - writes the JD with `append_body_section(ref, jdsection.TAG, jdsection.render(jd))`;
  - sets the status with `update_fields`;
  - writes the stories as notes in the stories folder, with `verified:` when set;
  - reads the lead back and raises on any mismatch.

  Persona content must follow spec §10's neutrality rules exactly:
  - hand-written synthetic stories;
  - companies from `_REVIEWED_FIXTURE_IDENTITIES`;
  - a JD template whose only placeholders are `{company}`, `{location}` and `{role}`;
  - story bodies with no placeholders and no names.

  Do NOT run a paid eval.

- [ ] **Step 4: Run green, then commit.** Run the full suite (also once with
  `env PATH="/usr/bin:/bin"`) and ruff, then commit
  `test(coach-eval): a prep coach eval runner with a seeded vault and its own rubric (#195)` (the repo's type for the eval harness, which never ships).

- [ ] **Step 5: Witnesses (after the commit).** Run each witness spec §10 / "Testing (PR 2)"
  names:
  - import `_claude` by name in prep.py (red on the tripwire);
  - compute `disallowed` from `list_tools()` instead of the hand-listed tuples;
  - drop the `_when` on the slug check;
  - drop each seeder read-back check;
  - the rest of the spec's list.

  Amend the commit with the table.

---

## Finish

1. Run `/review-pr` on the branch (the five specialists plus the CodeRabbit CLI). Fold the
   findings in one fix wave, with a scoped re-review.
2. Collapse the history to `docs(plan)`, one `feat(mcp)` (the tool and the prompt) and one
   `test(coach-eval)` (the runner), so the changelog reads as one feature. Test each commit.
3. Push, open the PR with `Refs #195` (no closing keyword), check `closingIssuesReferences` is
   empty, and push-notify the owner.
4. Run `path-to-green` to drive it to merge (CodeRabbit is the approver; never bypass it).
5. #195 stays open for the cover-notes spec. A paid eval run of `interview_prep` waits for the
   owner's go-ahead.
