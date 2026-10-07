# In-session setup and the career coach: design

Status: revision 4, 2026-10-07. Three `/review-plan` rounds (five reviewers each) are folded in;
the "Changes from revision N" tables at the end map every finding. Round 3 found no Critical or
High finding. Piece 2 of
"drive sluice from Claude Code" (piece 1 is `2026-10-06-mcp-verify-elicitation-design.md`). Piece 3
rewrites `docs/AI-SETUP.md` around one Claude Code session; piece 4 (pipeline commands as MCP
tools) stays optional.

## Goal

A user can install sluice, add its MCP server to Claude Code, type one slash command, and leave
the session with a configured hunt: a config, a Judging Profile, a Candidate Profile, searches,
and a researched Role Brief. They do it by talking to a career coach that works for them, and
they can come back later to revise the hunt ("interview me for a career change").

The coaching is the product. The write tools are plumbing under it.

## What this guards against

The same two properties as piece 1, applied to setup:

- **A human sees every value before it is written.** Each proposed change sits under its own
  unticked checkbox in a review form.
- **The model cannot answer for the human.** Only boxes the client returns as `true` are
  written.

Plus the project's standing rule for preferences: **an unanswered question writes nothing.** A
gate the coach inferred looks identical to one the user chose, and the leads it discards never
appear anywhere for them to notice (`docs/AI-SETUP.md`, rule 1).

The form is the guarantee. The coach's conversational rules (below) are instructions to a model,
and no CI test can enforce what a model says; the scenario evals measure them, they do not gate
them.

Out of scope, per the threat model (`domain_threat_model`): a client or hook configured to
answer the form for the user. That is the user's own tooling.

## Decisions taken in brainstorming and review

| Question | Decision |
|---|---|
| Who authors the values | The coach interviews in chat and drafts; a review form gates every write. |
| Create or update | Both. An existing hunt can be revised, including a career change. |
| Several roles with different CVs | A separate sub-project (multi-hunt), designed after this. |
| Form granularity | One unticked checkbox per unit. Only ticked units are written. |
| Config writes | Line-level edits, checked by re-loading with every real loader. |
| Tool structure | One review tool, one read tool, one prompt. |
| Role research | Persisted as a Role Brief vault note. No pipeline stage reads it. |
| Coach quality | Scenario evals with simulated users, dev-only, built BEFORE the playbooks. |
| Where the flow lives | Pure text-in/text-out in `sluice/onboard/review.py`; reads, the config check and writes in `core/app.py` facade methods. |
| The vault on a first run | As `init`: `$VAULT_DIR`, when set, IS the answer; otherwise a ticked `vault_dir` unit is required before any config is created. |
| `vault_dir` on an existing hunt | Not a unit. |
| `backend` | One unit that fans out (`Question.writes_to`), set aside when the stages disagree or a stage names its own model. |
| An existing hunt whose vault is the cwd-relative default | Every note unit is set aside; `setup_status` says so. |
| Creating a hunt | One creator, `apply_setup`; `cmd_init` keeps its own write block, and a test pins that both write the same bytes. |
| Branch shape | One PR, its commits grouped by the implementation plan into reviewable phases. |
| Removing a source's last search | Set aside; the reason names `ingest disable`. |
| A symlinked config file | Written through to its target; the link is kept. |

## Measured before designing

Claude Code 2.1.292, mcp 2.2.0, throwaway stdio servers:

- `claude -p '/mcp__probe__career_interview pivot'` reached an MCP prompt and passed the argument.
  The prompt string must precede `--mcp-config`, which is variadic.
- Asked to list the server's prompts, the model could not see them. The user invokes the slash
  command, so the install output and `docs/AI-SETUP.md` must name it.
- `claude -p ... --output-format json` returns a `session_id`; `claude -p --resume <id>` continued
  the conversation with the prompt's content still in context. A multi-turn headless conversation
  is feasible.
- **Client isolation for the evals.** By default `claude -p` loads the developer's user
  CLAUDE.md, memory, hooks and every installed plugin's skills (measured: all present). With
  `HOME` pointed at an empty directory it is not logged in; `--bare` needs `ANTHROPIC_API_KEY`.
  `--restricted --strict-mcp-config`, run from an empty working directory, keeps the login and
  loaded no user CLAUDE.md, no memory, no hooks and no plugin skills (built-in skills only).
  `--restricted` also drops web search unless `--tools` names it.

Piece 1's measurements still bind every form: the message folds after three lines, a checkbox
description shows in full up to about 2,000 characters (`mcpserver.py::_DESC_MAX_CHARS` is 1,900),
an entry taller than `_FORM_LINES` cannot show without scrolling, the dialog does not scroll in
tmux, and headless `-p` answers a form with `cancel`.

## Units

A unit is the smallest thing a checkbox approves. Five kinds:

| Kind | Target | Value is validated by |
|---|---|---|
| `profile` | one Judging Profile heading (`plan.PROFILE_HEADINGS`) | the prose rule |
| `candidate` | one interviewable Candidate Profile field (`ask._CANDIDATE_PROMPTS`) | the frontmatter rule |
| `config` | one catalogue key (`questions.catalogue()`), except `vault_dir` once a config exists | that `Question`'s own `parse`, given the raw string exactly as a typed answer |
| `search` | add or remove one `[label, url]` under a registered source id | registry lookup, `questions.parse_url` |
| `brief` | one Role Brief section (`ROLE_BRIEF_SECTIONS`) | the prose rule |

A change either SETS a value or CLEARS it. Clearing a config key writes back exactly the line
`init` renders for an unset key (`plan.py::_render_key` with no value:
`# <leaf>:   # <- uncomment and set YOUR OWN`), which returns that gate to abstaining. Clearing a
profile heading restores `DEFAULT_CRITERIA`'s neutral prose for it; clearing a brief section
restores the skeleton's placeholder text for it; clearing a candidate field blanks it.

No target vocabulary contains `verified`, and a change naming it is set aside (tested).

### The input schema

`setup_review(changes: list[Change])`, where a `Change` is one flat object:

```json
{"kind": "profile|candidate|config|brief|search",
 "target": "heading text | field name | catalogue key | section name | source id",
 "value": "the new text, or the raw config answer",
 "clear": false,
 "label": "search only", "url": "search only", "remove": false}
```

`value` is required unless `clear` (or, for `search`, `remove`) is true. A config `value` is the
raw string a user would type to `init`, passed to that question's `parse`, so `init` and the
coach cannot disagree about what an answer means. A candidate value is the plain text of the
field; the setter quotes it through `emit.scalar`, as `init` does. The schema is pinned in
`tests/functional/test_mcp_contract.py`.

### Validation rules

- **Prose rule** (`profile`, `brief`): non-empty; no line that would change the note's structure
  when spliced in (a line starting with `#`, a line that is exactly `---`, `<!--`, `-->`); and it
  must FIT one checkbox, by both measures `_pack_form` applies, characters and display height
  (`_entry_lines` against `_FORM_LINES`), counted with the description's own framing; and it must
  pass `mcpserver.py::_hides_text`, the control-character check `_pack_form` also applies. After
  an in-place splice the note's heading sequence must be unchanged (re-parsed, compared).
- **Target rule** (`profile`, `brief` updates): the target heading must occur exactly once in the
  existing note. Twice is set aside, because the edit would land in one copy while the judge reads
  both. Absent (a hand-edited note that dropped it) is appended as a new section at the end of the
  note, which adds text and changes none; the heading sequence after an append must be the old
  sequence plus exactly the target heading, last.
- **Frontmatter rule** (`candidate`): the edit goes through a public, guarded setter (see The
  Store contract), and the edited text is re-read through `parse_candidate_profile`; the target
  field must read back as the new value and every other field must be unchanged. Clearing a
  candidate field writes it back blank, the "undeclared" shape `init` writes for a field nobody
  answered.
- **Set-aside reasons name a remedy that exists for that unit**: the note to edit by hand for a
  vault unit, the config file for a config unit. `_set_aside_reason` and `_render_form` are
  parameterised by the unit kind's remedy text; their evidence wording stays exactly as it is for
  `verify_evidence`.

### Units that are set aside by design

- `vault_dir` once a config exists. Moving the vault would leave `seen.db` recording every
  already-seen lead, so none of them would ever be created in the new vault. The reason says so.
- `vault_dir` when `$VAULT_DIR` is set in the server's environment. `VAULT_DIR` decides the vault
  (`stores/vault.py::_make` is env-first), so the unit could change nothing; on a first run the
  variable is used as the answer instead (First run).
- `backend` when `triage.backend`, `cv.backend` and `track.backend` disagree (there is no single
  current value, and one tick would overwrite per-stage choices), or when any stage sets its own
  `model` (the model would no longer match the provider).
- Every vault unit, on an EXISTING hunt whose vault is the cwd-relative default: a config with
  no `vault_dir` and no `$VAULT_DIR`. The store would resolve `./vault` against wherever the
  client started the server, so the coach would write notes no other command reads. `Vault`
  already reports this fact (`preflight()`'s `vault_dir_is_default`); `setup_status` surfaces it,
  and the reason says to set `vault_dir` in the config by hand (it is not a unit once a config
  exists).
- Removing a source's LAST search. An empty override makes the source run its shipped example
  search (`ingest/base.py::searches_for`), which is not what "remove" means. The reason names
  `job-sluice ingest disable <id>`.

Measured, and the reason there is no general environment-override rule: no catalogue key other
than `vault_dir` is overridden by an environment variable. The loaders read only the claude CLI's
host and path and the Telegram pair (none of them catalogue keys), `$SLUICE_LOCATIONS` is retired
and raises, and `VAULT_DIR` is read only in the store factory.

### The Role Brief

`Job Applications/Role Brief.md`, a new `ROLE_BRIEF_RELPATH` beside `CRITERIA_RELPATH` in
`core/protocols.py`. Its sections are one tuple, `ROLE_BRIEF_SECTIONS`, defined once in
`sluice/onboard/review.py` and used by the renderer, the validator and the coach. They are
structure and carry no opinion:

- The role, as researched
- Title variants seen on boards
- Pay structure
- Signals of a good posting and a poor one
- Sources consulted

The judge keeps reading only the Judging Profile the user approved: researched text written by
the model must not reach any scoring or composing decision. Pinned by a sweep (Testing) over
`sluice/` for every way to name the note: the constant, any local alias of it (derived from each
file's `ImportFrom` nodes), the literal path string, and the note's basename `Role Brief` in any
string. Because `read_document` takes any `rel`, the sweep also enumerates every
`read_document(` call site in `sluice/` and requires its argument to be a name bound to one of
the document constants in `core/protocols.py`, so a path built inline cannot reach the note
unseen. Allowed sites are named FUNCTIONS, not files: the setup facade methods,
`onboard/review.py`, `onboard/coach/`, and `mcpserver.py`'s two setup tools. A reference anywhere
else, including any other `Sluice` method, fails. A behavioural sentinel backs the sweep: a vault
whose Role Brief carries a unique marker is triaged with a fake backend and composed for with a
fake backend, and neither the judge's prompt nor the composer's bundle contains the marker.

## Form layout

Each unit is one checkbox. Its description shows the new text, and for an update the text it
replaces, when both fit by characters and height. When they do not, the description shows the new
text in full and says it replaces the current text of that section or field, naming the note to
compare against. A unit whose new text alone does not fit is set aside by the prose rule before
any form is built, and so is one that fails `_hides_text`, so every set-aside a setup unit can
meet carries the setup remedy rather than `_pack_form`'s evidence wording. Packing into
one-screen forms reuses piece 1's `_pack_form`.

## Where the code lives

The shape mirrors piece 1's `Sluice.promote_shown_evidence`: the tool translates, and the facade
method that WRITES holds every check that guards the write, so a second caller cannot skip one.

- **`sluice/onboard/review.py`** (pure, text in and text out, no I/O): parse `Change`s, validate
  them, compute each artefact's edited text and each unit's before/after, render the Role Brief.
  From `sluice.core.vault` it imports exactly two names, `parse_frontmatter` and the public
  guarded setter, as `onboard/plan.py` already imports the first.
- **`sluice/onboard/edit.py`** (pure): the line-level config editor `review.py` calls.
- **`sluice/onboard/coach/`**: a package (`__init__.py` plus the Markdown playbooks) holding the
  prompt library (below).
- **`core/config.py::write_config_text(path, text, *, expect_sha=None)`**: the config file's one
  writer, beside the reader that already lives there. It resolves a symlink and replaces the
  TARGET in the target's own directory (a temp file there, then `os.replace`), so a link into a
  dotfiles repository survives and points at the new bytes; preserves the existing file's mode;
  takes a module-level lock keyed by the resolved path; under `expect_sha` replaces only when the
  current bytes hash to it and returns `False` otherwise; with no `expect_sha` it creates
  exclusively (`O_EXCL`), creating the parent directory first. It is new rather than borrowed:
  `core/vault.py`'s `_atomic_write` replaces a symlink instead of writing through it and creates
  no directory, and it and `_lock_for` are private to the store.
- **`core/app.py`** gains the impure half, as facade methods named unlike any `Store` method:
  - `Sluice.from_config_file()`: a classmethod that loads the config from
    `paths.config_file()` and builds a `Sluice`. The holder rebuild and first-run writes go
    through it, so `mcpserver.py` needs no `core.config` or `core.paths` import.
  - `Sluice.setup_snapshot() -> SetupSnapshot`: the current text and sha of the config file and
    the three notes, whether each exists, and whether the vault is the cwd-relative default.
  - `Sluice.apply_setup(writes: list[ArtefactWrite]) -> dict[str, ArtefactOutcome]`, where an
    `ArtefactWrite` is `(artefact, text, expect_sha | None, writes_to | None)` (`expect_sha`
    None means create; `writes_to` names the settings a config write may change). It is the ONE
    creator and the one updater: it runs the config check (below) on a config write ITSELF,
    before writing, and refuses the write when the check fails; writes the config first; rebuilds
    its own store from the new config when the config was created or changed; then writes the
    notes, each artefact isolated, so one failure is reported and the others proceed. On a first
    run it also creates the Leads view (`LEADS_VIEW_RELPATH`, rendered by `plan.build_plan` as
    `init` renders it) if absent, so a coach-created hunt matches an `init`-created one.

  None of these import anything from `sluice.onboard`.
- **`cmd_init` keeps its own write block.** Lifting it was considered and dropped: it writes four
  artefacts under per-artefact conditions with the `.init-scaffold.md` spare, a shape
  `apply_setup` has no reason to carry. Parity is pinned by a test instead: for the same answers,
  `init` and a first-run `setup_review` write byte-identical config, Judging Profile, Candidate
  Profile and Leads view.
- **`tests/test_mcpserver.py`'s isolation sweep** extends from `mcpserver.py`'s own AST to every
  `sluice.onboard` module the setup tools reach, discovered by following `review.py`'s imports
  within `sluice.onboard` (so `plan.py`, `emit.py`, `questions.py` and `edit.py` are walked too),
  plus the `coach` package. `sluice.onboard.review`, `sluice.onboard.edit` and the coach modules
  join `_ISOLATION_ALLOWED_MODULES`, each with `safeout`'s "no write path" reason. In the walked
  modules the sweep flags:
  - a store write (`.write_document(` and the store's other write methods);
  - a file write: `open(..., "w"/"x"/"a")`, `os.replace`, `write_config_text`, and
    `core/vault.py`'s plain writers `_write`, `_atomic_write` and `_cas_write`;
  - a facade write: `apply_setup`;
  - any import from `sluice.core.vault` other than the two names above, and any import from
    `sluice.core.config`.

  `questions.catalogue()`'s existing lazy `Sluice.available(...)` read stays allowed: it lists
  registry names and writes nothing. The sweep asserts which modules it walked, and planted
  `_atomic_write(...)` and `.write_document(...)` calls in `review.py` each turn it red.

## The Store contract

Two additions in `core/protocols.py`, pinned in `tests/conformance/test_store_contract.py`:

- **`read_document(rel) -> str | None`.** The text, decoded as UTF-8 and read with `newline=""`
  so a CRLF note keeps its line endings; `None` when absent. Undecodable or unreadable RAISES
  rather than reading as empty. Added to `_STORE_READ_METHODS`. The sha the form records is
  SHA-256 over that text encoded as UTF-8, which for a decodable note is its raw bytes.
- **`write_document(..., expect_sha=...)`.** Replaces the document only when its current text
  hashes to `expect_sha`; returns the handle on success and `""` on an abstain, exactly like
  `only_if_absent`. A missing document under `expect_sha` abstains. Combining it with
  `only_if_absent` raises. Written with `newline=""`, so an edit to a CRLF note does not rewrite
  its line endings. The vault implementation takes `_lock_for` as `_cas_write` does, and is
  best-effort under the same compare-then-replace window, not a lock. The method's docstring,
  which still scopes it to the rejected-leads digest, is corrected. Every update path in this
  feature passes `expect_sha`; the bare replace stays for the digest only.

The Candidate Profile's single-line setter becomes a public guarded helper in `core/vault.py`,
beside `parse_frontmatter`. It refuses a duplicate key (`_set_fm` writes the first occurrence
while `_fm_dict` reads the last, so the edit would report `written` while the CV kept the old
value), a stored multi-line value (`_holds_multiline_value`) and a write that would break the note
(`_single_line_write_breaks_note`), exactly as `update_fields` does. It is a text helper for the
one store whose notes have frontmatter, not a `Store` member.

## Tools

### `setup_status` (every privilege level)

Returns every unit's current value, which artefacts exist, whether a config file exists,
whether `$VAULT_DIR` decides the vault, and whether the vault is the cwd-relative default (in
which case vault units will be set aside). A note that cannot be read or decoded is reported by
name as unreadable, with its units' current values absent, rather than failing the whole call. No absolute path appears anywhere in the response
(asserted over the whole serialised result).

### `setup_review(changes)` (only under `--write`)

A SEP-2322 step function shaped like `mcpserver.py::verify_evidence_step`:

1. **First call.** Take a fresh snapshot, validate each change through `review.py`, and set aside
   each failure with its reason. Pack the surviving units into forms, each under its own unticked
   box, and return an `InputRequiredResult` whose state carries each artefact's sha as shown.
2. **Retry.** Re-take the snapshot, compute each artefact's text from the shown sha's bytes for
   the ticked units, and call `apply_setup`, which runs the config check itself. Report each unit
   as `written`,
   `declined`, `conflict`, `set_aside` or `failed`, plus units not shown yet so the coach can send
   the next batch.

Outcome mapping, so the caller always gets a structured answer (mcp 2.x discards exception
messages):

- An artefact whose text changed since the form, or whose create abstained because it now
  exists (for example `init` ran meanwhile): every unit for it is `conflict`.
- A note that cannot be read or decoded, a loader that rejects the old or new config, or an
  `OSError` on a write: every unit for that artefact is `failed`, with a reason naming the
  artefact and the cause (never an absolute path). Other artefacts proceed.
- If the config's create is a `conflict` or `failed` on a first run, no note is written: the
  notes need the vault the config was to name. They report `set_aside` with that reason.

A client that cannot take an `InputRequiredResult` gets `unsupported_client`, the outcome name
`verify_evidence_step` already uses.

### Fresh state on every call

`build_server` currently builds one `Sluice(config)` at startup, and every tool closes over it.
That instance moves into a holder the tools read through. Each setup call builds its own
`Sluice.from_config_file()`. After any `setup_review` in which the config was written, the holder
is rebuilt the same way, in a `finally`, so `doctor`, `verify_evidence` and the rest see the new
hunt without a restart, including when a later note write failed. A rebuild that itself fails
never replaces the per-unit outcomes, which describe writes that already landed: the old holder
stays, and the result carries `restart_needed` with the reason, so the coach tells the user to
restart the server.

### First run

With no config file, `setup_status` says so. The vault is decided as `cmd_init` decides it:

- When `$VAULT_DIR` is set, it IS the `vault_dir` answer (normalised by `parse_path`, as `init`
  normalises its preset). The coach is told so and proposes no `vault_dir` unit.
- Otherwise a ticked `vault_dir` unit (`parse_path`: absolute) is required. Without one, NO config
  is created and every unit is set aside ("choose where your notes live first"). A config with no
  vault would send every later note to `./vault`, relative to wherever the client started the
  server.

Write order: the config, rendered through `plan.build_plan` from the ticked config units plus the
vault answer exactly as `init` renders answers, so every unticked key is COMMENTED and the text
equals `init`'s for the same answers; then the vault units and the Leads view, through the
store `apply_setup` rebuilt from that new config. A Judging Profile created with only some headings ticked keeps the
neutral default prose under the rest, so the judge still abstains there.

## Write paths

Each artefact is written once per review, with all its ticked units applied together, under
`expect_sha` against the text the form was built from. Order: config first, then notes.

### Vault notes (Judging Profile, Candidate Profile, Role Brief)

- **Create:** `write_document(only_if_absent=True)`, the note rendered in full (`plan.py`'s
  renderers; `review.py` for the Role Brief).
- **Update:** the edit is recomputed from the shown text (one heading's body, one section's body,
  or one frontmatter line through the guarded setter) and written with `expect_sha`.

### The config file

`sluice/onboard/edit.py` (text in, text out):

- **Set:** replace the key's own line inside its block, or the commented line `init` wrote, with
  the `emit.scalar` / `emit.flow_list` rendering. A key in neither form (a config from an older
  `init`, or hand-written) is INSERTED at the end of its block, creating the block if absent,
  which is the same treatment a new source's `searches` block gets. A fan-out key (`backend`) is
  set in each block its `writes_to` names.
- **Clear:** write back the exact line `init` renders for an unset key.
- **Search:** insert or delete one `- [label, url]` line under `sources.<id>.searches`, creating
  the source's block when absent.
- **Refuse,** setting the unit aside with the reason, for a shape it cannot place: a multi-line
  block value, a duplicate key, a searches entry not in flow form.

**The config check** runs inside `apply_setup`, on every config write, before anything is
written. It loads the old and the new text through every config loader and requires two things:
every setting that CHANGED is one the write's `writes_to` names (for a search, that source's
`searches`), and every setting `writes_to` names READS its new value afterwards. It does not
require each named setting to change: an answer equal to the value already in force (choosing the
shipped default backend, or `0` for `lead_ttl_days`) is a legitimate no-op, and clearing a key
that holds its default changes nothing a loader reads. The second clause is what catches an editor
that wrote only two of `backend`'s three blocks. The loaders take a file path, so the check writes
each text to a private temporary file; it reads no environment variable of its own, and none needs
clearing, since no catalogue key has a load-time override (measured, above). The loaders are a
hand-written roster of five (`load_config`, `load_triage_config`, `load_cv_config`,
`load_apply_config`, `load_track_config`), asserted equal, in both directions, to the set of
top-level `load_*config` functions discovered in `sluice/`. A loader that RAISES on either text is
a `failed` outcome naming it. On any other difference the old text stays and every unit for the
config is set aside, naming the setting that changed.

### Invariants kept

- **Never-clobber:** no wholesale rewrite of an existing artefact; only section, field or line
  edits of ticked units, each under `expect_sha`.
- **Empty config abstains:** an unticked key stays commented; an unticked heading keeps the
  neutral default prose; clearing restores both.
- **Citability:** no unit can reach `verified:`.
- **Fail loudly:** an unknown `kind`, target or catalogue key is set aside by name, never mapped
  to a default; every failure is a structured outcome.

## The career coach

### Shape

`/mcp__sluice__career_interview [focus]`, registered at every privilege level. A read-only
server's coach can interview and research, and tells the user to restart the server with
`--write` before the review step.

The playbooks are Markdown files packaged as data in `sluice/onboard/coach/` (a persona core plus
one playbook per phase, each with its own method and exit criteria), read with
`importlib.resources`. Later phases add files rather than growing one text. A pure function
assembles the prompt from them and `focus`; `mcpserver.py` registers it. Text kept as data files
is also what stops the existing module-constant sweeps (`tests/onboard_prose.py`) from treating
playbook prose as label strings. The wheel and sdist must carry the files: `pyproject.toml`'s
`[tool.setuptools.package-data]` gains `onboard/coach/*.md` beside the existing template entry,
and `tests/test_packaging.py` asserts the files are in the built artefacts.

`review.py` and `edit.py` DO sit inside `sluice.onboard`, so their module constants (the
section tuple, reason templates, form framing, the skeleton) meet
`tests/onboard_prose.py::test_the_prose_roster_covers_every_declared_constant`. They are routed
into that file's `shipped_prose()`, which makes them swept prose, rather than into `_NOT_PROSE`;
a name `review.py` imports and re-exports is listed with its provenance, as the file already does
for imported constants.

### Phases

1. **Open.** Call `setup_status`. With no hunt, offer two paths: help choosing a role, or tell
   me the jobs you want. With a hunt, summarise its gates and Role Brief and ask what has
   changed. `focus` (for example "career change") steers this.
2. **Discovery** (the help-me-choose path). A real coaching structure: experience and
   transferable skills, what energises and what drains, values and non-negotiables, constraints
   (pay, location, hours, notice). It ends with two to four candidate directions, each with
   reasons and trade-offs. The user chooses one, or none. Nothing is written in this phase.
3. **Research.** For the chosen role, research it with the client's web search and draft the
   Role Brief sections with sources. Show the draft in chat before any form.
4. **Role-specific interview.** Questions drawn from the research and mapped onto units: titles
   wanted and rejected, locations, pay floors in the field's own structure (day rate or salary),
   the Judging Profile headings in the user's words, the identity fields. Searches last: which
   boards they use, and the URLs pasted from their browser.
5. **Review.** Batch the units into `setup_review`, say a form is coming, report each unit's
   outcome, offer to re-propose conflicts.
6. **Hand-off.** Run `doctor` and name what is left from piece 1's division of labour:
   verifying evidence, the CV Layout, job-board logins.

### Rules the prompt states

- Research shapes the questions, never the answers. A value the coach suggests is offered, and
  stays unset unless the user says yes in chat AND ticks it.
- An unanswered question proposes no unit. The coach explains that an empty gate passes every
  lead, so leaving one empty is a real choice.
- The Judging Profile is the user's words, never the coach's reading of their CV.
- Never answer the form. Never re-send a declined unit unless asked.

### Neutrality

Sluice still ships no opinion. The coach's expertise is generated in the session, for this
user, from research, and its suggestions may be opinionated because none reaches a gate or the
judge without the user's tick. The SHIPPED text names no role, sector, seniority or employer, and
contains no example list of roles or sectors (an example is an opinion about what is typical).
That last rule is checked at review time only; no vocabulary can enumerate every example, and the
spec does not pretend otherwise.

Enforced over every shipped string this feature puts in front of the client or into the vault:

- the assembled prompt, with `focus` absent and present (the prompt takes no hunt state; the
  model fetches that with `setup_status`, so there is no other axis to vary);
- the prompt's registered description and its argument's description;
- `setup_status`'s and `setup_review`'s registered descriptions;
- the form framing text and every set-aside reason template;
- the Role Brief skeleton and the appended-section text.

The sweep discovers the playbook files with `importlib.resources`, asserts it found at least one
and that every one contributes to the assembled prompt, and applies two vocabularies:
`questions.NO_TAXONOMY_WORDS` and the forbidden list of
`test_shipped_prompt_expresses_no_role_or_culture_preference`. That list is hoisted, unchanged,
from a local inside the test function into a module-level constant in `tests/test_prompt.py`, which
the existing test then reads; the test's assertions and the list's contents do not change. Both
remain smoke tests. Each list is matched the way its existing guard matches it:
`NO_TAXONOMY_WORDS` on word boundaries (`questions.expresses_a_preference`), the forbidden list as
lowercase substrings. The hoisted list is a tuple, so nothing can mutate it in place. A standing
test (it runs every time, not a one-off mutation) calls the SAME assembler the server registers,
which takes its playbook source as a parameter defaulting to the packaged files, hands it a
source containing a planted role word, and asserts the sweep reports it. When
ordinary coaching prose trips a word ("hiring manager", "senior colleague"), the playbook is
reworded; a word is never removed from either list, and no exemption is added.

### Roadmap (recorded, not built here)

- Market reality check: demand and pay benchmarks per location.
- Gap analysis against the evidence corpus: what to build, what to foreground.
- CV positioning per role.
- Interview coach, building on the Role Brief and STAR stories (#195).
- Multi-hunt, once that sub-project lands.
- Check-ins: "you've dismissed 40 leads on this; revisit the gate?"

## Scenario evals (`scripts/coach_eval/`)

Built FIRST, before the playbooks, so the coach is iterated against scores from the start.
Dev-only: it spends tokens and is not hermetic, so it never gates CI.

- **Loop.** A coach turn is `claude -p --resume <session_id>` against `mcp serve --write`; a user
  turn is a second `claude -p` given the persona and the transcript so far. Headless runs answer
  the form with `cancel`, so the eval scores what the coach PROPOSED (the `setup_review` inputs),
  which is the behaviour the rules govern.
- **Client isolation.** Both clients run `--restricted --strict-mcp-config` from an empty working
  directory inside the eval's temporary directory, with `--tools` naming exactly what the role
  needs (web search for the coach, nothing for the simulated user), and with the only MCP server
  the eval's own `mcp serve`. Measured above: this loads no user CLAUDE.md, memory, hooks or
  plugin skills, so nothing from the developer's own job hunt can reach a transcript and from
  there a playbook. That measurement is per version, so every run also checks it: each client is
  run with `--output-format stream-json --verbose`, whose first `system`/`init` event reports
  `claude_code_version`, `mcp_servers`, `tools` and `plugins` (measured). The harness refuses to
  continue unless the version is one recorded as measured, `mcp_servers` is exactly the eval's own
  server, `tools` is exactly what it asked for, and every plugin's `path` is `builtin`. The init
  event does not report CLAUDE.md or memory, which is why an unmeasured version is refused
  outright; re-measuring one is a documented manual probe, recorded with its date.
- **Server isolation.** The server's environment is built in a stated order. First it UNSETS
  every variable `paths.resolve` consults as `env_var=` (derived the way
  `tests/test_path_sandbox.py::test_the_sandbox_covers_every_path_env_var` derives them, and
  asserted equal to `tests/conftest.py::PATH_ENV_VARS`), plus every variable `tests/conftest.py`'s
  sandbox clears for other reasons (today the three `CAMOFOX_*` and the two `SLUICE_TELEGRAM_*`,
  which `doctor` would otherwise read and report from the developer's real setup), plus
  `VAULT_DIR`. Then it SETS `SLUICE_CONFIG`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
  `XDG_CACHE_HOME` and `HOME` into the temporary directory, and starts the server with an empty
  temporary directory as its working directory. `VAULT_DIR` stays unset so the personas exercise
  the default first-run path, where a ticked `vault_dir` unit gates every write; one persona runs
  with it set into the sandbox to cover the other arm. A pure function,
  `isolation_problems(env, sandbox)`, returns every variable that resolves outside the sandbox or
  should be unset and is not; the harness refuses to start unless it is empty, and it has
  hermetic tests in CI, one row per variable.
- **Output.** Transcripts, briefs and scorecards contain live web research and model-played
  users, so they are written outside the repository (the temporary directory, or a path given on
  the command line), never under `scripts/`.
- **Personas.** A structured file per persona: a seeded-faker name, a location from
  `tests/conftest.py::LOCATIONS` (the repo's existing fictional roster, not a second one to
  drift), and a hand-written situation (career changer, graduate, undecided, hard
  constraints, returner). A CI test asserts every persona's name comes from the seeded generator
  and its location is in the roster. The situation is free prose, which no roster can sweep, so it
  is reviewed like any shipped prose.
- **Rubric.** Deterministic checks over the tool calls only: `setup_status` called before any
  `setup_review`; every `setup_review` input valid against the schema; every brief proposal's
  "Sources consulted" non-empty; no change targets `verified`; turn count. Everything that needs
  reading the conversation (was a unit proposed before the user agreed to it, were the questions
  specific to the role, the coaching quality) is scored by an LLM grader and labelled as such in
  the scorecard.
- **Scorecard.** One per persona, used to iterate the playbooks.

## Testing (hermetic, CI)

- **`edit.py`**, table tests: set, clear, uncomment, insert a missing key, create a missing
  block, add and remove a search, and each refusal shape, plus hand-written block shapes (a block
  with comments between keys, a block with no trailing newline). Properties, for every catalogue
  key on an `init`-rendered file: setting the same value twice equals setting it once; after any
  operation each block in the key's `writes_to` holds the key on exactly one line, counted by a
  matcher written in the test from `_render_key`'s two line shapes rather than by `edit.py`'s own
  matcher; set then clear is byte-identical to the original. For searches: add then remove is
  byte-identical; removing the last search is refused.
- **The config check**, through `apply_setup` on crafted texts: a change in a setting the write's
  `writes_to` does not name (refused); `backend` reaching all three of its settings (passes) and
  only two (refused, by the reads-its-new-value clause); an answer equal to the value already in
  force (passes, writes the line, changes no loaded setting); clearing a key that holds its default
  (passes); a loader that raises (`failed`). Witness: a deliberately faulty editor that also flips
  an unrelated key is caught and nothing is written. The five-loader roster is asserted equal to
  the discovered set in both directions.
- **`writes_to` per key:** for every catalogue key, setting a non-default value on an
  `init`-rendered file changes exactly the settings its `writes_to` names (derived by running the
  loaders, not restated).
- **Store contract** (`test_store_contract.py`): `read_document` returns the text, `None` when
  absent and creates nothing (no file, no directory) in doing so, and raises when the path is
  unreadable (made a directory, so the row holds under uid 0, per `conftest._cannot_unread_a_dir`)
  or undecodable; `expect_sha` matching replaces; stale abstains with `""`; missing abstains;
  combined with `only_if_absent` raises; a CRLF note's sha is computed over its raw bytes and an
  edit leaves its other lines' `\r\n` intact (compared as bytes on disk).
- **`write_config_text`:** exclusive create (and its parent directory); `expect_sha` match
  replaces and mismatch returns `False`; the file's mode survives a replace; a symlinked config is
  written through, the link still a link afterwards and its target holding the new bytes.
- **Candidate setter:** a duplicate key, a multi-line value and a note-breaking write are each
  refused; a normal edit re-reads through `parse_candidate_profile` as the new value; a clear
  leaves the field blank.
- **Prose and target rules:** a heading line, `---`, `<!--`, `-->` and a control character in a
  value are each set aside with the setup remedy; a value one character over and one line over the
  form limit is set aside; a duplicated target heading is set aside; an absent one is appended and
  the new heading sequence is the old one plus exactly that heading; an appended value that smuggles
  a second heading is set aside; a valid in-place splice leaves the heading sequence unchanged.
- **`setup_review` end to end** through `mcp.Client` with an `elicitation_callback` (piece 1's
  harness): a ticked subset is written and an unticked unit is not; an edit to one note between
  form and retry gives `conflict` for that note while another note in the same review is written;
  a config edited between form and retry conflicts; a config CREATED between form and retry on a
  first run conflicts, no note is written, and the planted config is unchanged byte for byte; an
  unreadable note is `failed` while the others are written, and `setup_status` reports it by name;
  a holder rebuild that raises leaves the outcomes intact and reports `restart_needed`; every
  by-design set-aside fires with its reason, including every vault unit on an existing config with
  no `vault_dir` and no `$VAULT_DIR`.
- **First run**, with `VAULT_DIR` unset AND the working directory changed to an empty temporary
  directory (so `./vault` cannot resolve to a checkout's own gitignored vault): no `vault_dir` unit
  means no config and every unit set aside; with one, the notes and the Leads view land in the
  named vault; afterwards `doctor`, through the rebuilt holder, reports a Judging Profile that
  exists only in that vault.
- **First run with `$VAULT_DIR` set:** the config names that vault, no `vault_dir` unit is
  proposed or accepted, and the notes land there.
- **`init` parity:** for the same answers, `job-sluice init` and a first-run `setup_review` write
  byte-identical config, Judging Profile, Candidate Profile and Leads view.
- **Isolation sweep:** walks every `sluice.onboard` module the setup tools reach plus the coach
  package, asserts the walked set, and planted `_atomic_write(...)` and `.write_document(...)`
  calls in `review.py`, and a planted extra import from `sluice.core.vault`, are each reported.
- **Responses:** no absolute path in any `setup_status` or `setup_review` response (whole
  serialised result).
- **No `verified`:** a change targeting `verified` in any kind is set aside.
- **Role Brief stays unread:** the sweep matches the constant, its local aliases, the literal path
  and the basename, and every `read_document(` call site's argument; its scope assertion requires
  the definition site, each allowed function and at least one `read_document(` call site to be
  seen; planted references (another `Sluice` method naming the constant; an inline-built path
  passed to `read_document`) are each reported; and the behavioural sentinel holds.
- **Contract pins** in `tests/functional/test_mcp_contract.py`: tool names, the `changes`
  schema, the prompt's registration and argument, and the registered-description sweep (a tool
  has two docstrings).
- **Coach neutrality and packaging:** as in the Neutrality and Shape sections, including the
  `shipped_prose()` routing for `review.py`'s and `edit.py`'s constants.
- **Eval isolation:** `isolation_problems` with one row per variable it must unset and one per
  variable it must set, each placed outside the sandbox (reported), plus a clean environment
  (empty); and the init-event check against a recorded event with an extra MCP server, an extra
  tool, a non-builtin plugin and an unmeasured version (each refused).

## Documentation

In this piece, not piece 3:

- `docs/USAGE.md`: `mcp serve` lists `setup_status`, `setup_review` and the `career_interview`
  prompt, and says the slash command is how a user starts it.
- `cli.py`'s `mcp serve --write` help string, and `build_server`'s docstring, which both
  enumerate the write tools.
- `docs/ARCHITECTURE.md`: the onboard section (new modules, imported by `mcpserver.py` as well as
  `cli.py`), the mcpserver paragraph (the holder, the widened isolation sweep, and its sentence
  that every write tool is a thin translation over exactly one `Sluice` write method, which stays
  true because `setup_review` writes only through `apply_setup`), the Store contract
  (`read_document`, `expect_sha`), and `core/config.py`'s new writer.
- `README.md`: the MCP paragraph names the coach.
- `docs/CONFIGURATION.md`: the Role Brief note, and that no pipeline stage reads it.
- `.rulesync/rules/CLAUDE.md`: the command-packages paragraph (onboard is now also imported by
  `mcpserver.py`) and the Store write path, then `npm run rulesync`.

Piece 3 still owns the `docs/AI-SETUP.md` rewrite, which must name the slash command.

## Deferred, with owners

- Multi-hunt (several roles, each with its own Judging Profile, CV Layout and gates): its own
  brainstorm and spec.
- The coach roadmap above.
- `docs/AI-SETUP.md`: piece 3.

## Changes from revision 1

| Finding(s) | Resolution |
|---|---|
| inv-001, G1, arch-5, TE-5 (first run hits a stale store) | Fresh state on every call; holder rebuilt after a config write; config written first; vault rules on a first run. |
| arch-1 (no home for the flow) | Where the code lives; isolation sweep widened and deepened. |
| arch-2 (no raw read) | `Store.read_document`. |
| arch-3, inv-008, TE-6 (`expect_sha` details) | The Store contract; full conformance rows. |
| arch-4, inv-004 (config write placement and location) | Facade write, `paths.config_file()`; `cmd_init` block lifted. |
| inv-002 (candidate duplicate key) | Guarded public setter; re-read check. |
| inv-003 (prose splice) | Prose rule. |
| inv-005 (moving the vault) | `vault_dir` is first-run only. |
| inv-006, N5 (brief stays unread) | Function-scoped sweep. |
| inv-007, G4 (last search) | Set aside, names `ingest disable`. |
| G2 (brief cap vs form) | Form layout; prose rule measures characters and height. |
| G3, TE-2, G10 (fan-out and env overrides) | `writes_to`; the measured env finding. |
| G5 (missing keys) | Inserted at the end of the block. |
| G6 (schema) | The input schema. |
| G7, arch-6 (docs) | Documentation section. |
| G8 (outcome name) | `unsupported_client`. |
| G9 (eval bundled) | Owner's decision: same branch, built first. |
| TE-1 (check witness) | Faulty-editor witness; five-loader roster both ways. |
| TE-3, N1, N2 (coach unswept) | Every client-facing string swept; standing planted witness. |
| TE-4 (vacuous property) | Idempotence, one line per block, byte-identical round trip. |
| TE-7 (what CI cannot enforce) | Stated under "What this guards against". |
| TE-8 (`verified`) | Tested. |
| N3, N4 (eval data and isolation) | Client and server isolation, output location, persona roster. |
| N6, arch-7 | Confirmations; section tuple defined once. |

## Changes from revision 2

| Finding(s) | Resolution |
|---|---|
| r2-G1, r2-inv-001, r2-arch-4 (first run with or without `$VAULT_DIR`) | First run decides the vault as `init` does; no config without a vault; tests for both. |
| r2-arch-1 (isolation sweep shallow) | Sweep walks every allow-listed module, asserts its scope, planted witness. |
| r2-arch-2, r2-G5 (reload imports) | `Sluice.from_config_file()`. |
| r2-arch-3, r2-G4, r2-TE-4 (duplicate field) | `Question.writes_to`; a per-key test derives it from the loaders. |
| r2-arch-5 (bytes vs str) | `read_document` returns text read with `newline=""`; writes use `newline=""`. |
| r2-arch-6 (signatures) | Facade signatures named; the lifted block takes rendered texts. |
| r2-G2 (form height, evidence wording) | Prose rule measures height; reasons and framing parameterised by kind. |
| r2-G3, r2-TE-1 (check purity, roster, env) | Check moved to the facade with temp files; five-loader roster both ways; env clearing dropped as unfalsifiable and unneeded (measured). |
| r2-G6 (missing heading, candidate quoting) | Target rule; `emit.scalar` quoting. |
| r2-G7 (help string, docstring) | Documentation section. |
| r2-inv-002, r2-inv-006 (partial failures) | Outcome mapping with `failed`; notes withheld when a first-run config is not created; holder rebuilt in `finally`. |
| r2-inv-003 (duplicate heading) | Target rule. |
| r2-inv-004, r2-N8, r2-TE-5 (brief sweep bypassable) | Constant, aliases and literal; function-scoped allowance; scope assertion and planted reference. |
| r2-inv-005 (config replace) | Atomic, mode-preserving, locked; a symlink is written through to its target. |
| r2-N1 (eval client loads the developer's context) | `--restricted --strict-mcp-config` from an empty directory, measured. |
| r2-N2, r2-N5 (sweep coverage) | Every client-facing and vault-bound string; `focus` is the only axis. |
| r2-N3 (roster test vs playbook constants) | Playbooks are packaged Markdown data, not module constants. |
| r2-N4 (forbidden list is a local) | Hoisted unchanged to a module constant the existing test reads. |
| r2-N6, r2-TE-6 (path env vars, refuse-to-start untested) | Unset every `env_var=` variable, derived from the call sites and pinned to `PATH_ENV_VARS`; `isolation_problems` tested in CI. |
| r2-N7 (personas outside every sweep) | Structured persona files with their own roster test. |
| r2-N9 (example-list rule) | Stated as review-time only. |
| r2-TE-2 (first-run test resolves `./vault` in the checkout) | Test changes directory; `doctor` checks something only the named vault has. |
| r2-TE-3 (properties contradict Clear) | Clear writes `init`'s own unset line; one line per block; search properties. |
| r2-TE-7 (unreadable and CRLF rows) | Directory-at-path; bytes compared on disk. |

## Changes from revision 3

Round 3 found no Critical or High finding.

| Finding(s) | Resolution |
|---|---|
| r3-arch-1 (check owned by the tool) | `apply_setup` runs the config check itself; `check_config_change` is gone from the facade. |
| r3-arch-2, r3-TE-2 (sweep shallow on imports) | Sweep walks the onboard modules the tools reach; `core.vault` imports limited to two names; plain writers, `write_config_text` and `apply_setup` in the flagged set; planted `_atomic_write`. |
| r3-arch-3, r3-G2 (two creators, missing Leads view) | One creator (`apply_setup`), which also creates the Leads view; `cmd_init` keeps its block; byte-parity test. |
| r3-arch-4 (config writer has no home) | `core/config.py::write_config_text`, specified; its own test rows. |
| r3-arch-5, r3-inv-003 (holder rebuild hides outcomes) | Failed rebuild keeps outcomes and the old holder, reports `restart_needed`; `setup_status` names an unreadable note. |
| r3-arch-6 (packaging) | `package-data` entry and the coach package's `__init__.py` named. |
| r3-inv-001 (existing hunt, default vault) | Every vault unit set aside; `setup_status` reports it; test row. |
| r3-inv-002, r3-G1 (append vs unchanged headings) | Append arm checks old sequence plus the target last; smuggled-heading row. |
| r3-N1 (onboard prose roster) | `review.py`/`edit.py` constants routed into `shipped_prose()`, not `_NOT_PROSE`. |
| r3-N2 (isolation measured once) | Per-run init-event check; unmeasured versions refused. |
| r3-N3 (CAMOFOX, Telegram) | Unset with the path variables, in a stated order. |
| r3-N4 (brief reachable via `read_document`) | Basename match, `read_document(` call-site check, behavioural sentinel. |
| r3-N5 (hoist and matching) | Tuple; each list matched as its existing guard matches it. |
| r3-N6 (second roster) | Personas use `conftest.LOCATIONS`. |
| r3-G3 (evals never cover the default path) | `VAULT_DIR` unset by default, one persona with it set; order stated. |
| r3-G4 (`_pack_form` claim) | Prose rule applies `_hides_text`; claim corrected. |
| r3-G5 (clear undefined for candidate, brief) | Defined. |
| r3-G6 (branch shape) | One PR; the implementation plan groups its commits into reviewable phases. |
| r3-TE-1 (check refuses a no-op answer) | Changed ⊆ `writes_to`, and every named setting reads its new value; no-op rows pass. |
| r3-TE-3 (row precision) | Injectable assembler, per-variable rows, independent line matcher, absent read creates nothing, race row byte check. |
