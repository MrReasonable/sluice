# Structured CV composition: the model tailors content, sluice builds the CV — design (#364, #365, #368)

Status: approved section by section in conversation 2026-10-04/05; revised after two
`/review-plan` rounds. The owner decisions those rounds raised are recorded in §3 (D6, D10,
D11, D13, D14). One PR, released as a major version. Awaits review of this written spec.

## 1. The problem

### 1.1 Three issues, one cause

- **#364** — a long-career CV's roll-up block (several early employers collapsed under one
  heading) fails `cv run` with a `FORMAT:` refusal from the `template` renderer's parser.
- **#365** — the composer writes a SKILLS section of competency phrases taken from the job
  ad; the hard gate correctly refuses them (`UNSOURCED SKILL`), burning compose calls with a
  violation count that swings run to run.
- **#368** — the #194 term check subtracts every name-shaped token in `cv.negatives` from
  its vocabulary, so a prose negative that names employers or uses capitals for emphasis
  makes ordinary, evidenced terms report as `UNBUNDLED TERM`.

All three come from the same design:

1. **The model writes the whole CV as free text, and sluice reads the structure back out of
   it in three places** — the gate (`cv/validate.py::section_spans`), the `template`
   renderer's parser (`cv/parse.py::parse_cv`) and the engine's name/contact guards. Each
   has its own idea of what a line means, and every disagreement between them is a bug.
   `.rulesync/rules/CLAUDE.md` carries pages on keeping them in step (marker equality and
   floor rules, the implication sweep, the envelope unwrap, the #99 guards). #364 is one more
   disagreement.
2. **The facts that should never vary between CVs have no structured home.** Employers,
   dates, titles, locations, the roll-up grouping, certificates and education are copied by
   the model from the baseline CV's text on every run, and no check reads them.
3. **`cv.negatives` is the only free-text channel into the prompt**, so it ends up carrying
   layout rules, length limits and workarounds for the gate's own rules as well as genuine
   prohibitions — and #194 then parses all of it as bans.
4. **SKILLS is generated, then checked against a list the model never sees as a list.**

### 1.2 What was measured

Synthetic, against `tests/test_cv_engine.py::CLEAN_CV` with a roll-up appended:

| Roll-up shape | `validate()` | `parse_cv` |
|---|---|---|
| `DATES \| LOCATION` + unbulleted cited paragraph (#364's shape) | clean | refused |
| `DATES \| LOCATION` + bulleted cited line | clean | parses (title = the location) |
| `DATES \| LOCATION \| Role` + unbulleted paragraph | clean | refused |
| `DATES \| LOCATION` + unbulleted, **uncited paragraph with invented figures** | **clean** | refused |
| the same invented line, bulleted | `UNCITED BULLET` | parses |

- #364's stated first cause is false: a two-field meta line parses. The unbulleted body is
  the only trigger.
- An unbulleted WORK paragraph is never citation- or figure-checked. Under `template` the
  parser's refusal is the only thing stopping it; under `script`, which has no `precheck`,
  invented figures render.
- The gate never checks that a bullet's cited entry belongs to the employer it sits under:
  `CLEAN_CV` cites one entry under four different employers and is pinned clean.
- `MISATTRIBUTED SKILL` (row 1) refuses an ORDINARY word declared by another entry: with
  entry A declaring `coaching` and entry B declaring only `React`, the bullet
  `- Ran weekly coaching for the UI team [B]` is refused, `Coaching` with a capital passes,
  and the two available fixes are to cite A or to add `coaching` to B. The second grows
  `Skills:` toward an index of every word a bullet might use rather than a list of headline
  skills.

Mechanisms the issues and the code show, stated generally:

- A baseline CV whose roles are written as paragraphs shows the model a second template
  that contradicts the prompt's format block, which asks for bullets.
- A negative that names employers or uses capitals for emphasis subtracts terms the user's
  own evidence carries (#368), and under `cv.style_hold` each such finding is a retry and
  then a hold.
- A baseline CV and a user's other records can disagree on dates, and nothing reconciles
  them.

## 2. What the checks are for

Agreed during design, and the test every check in this spec was held to:

> **The checks catch the model putting words in the user's mouth, and nothing more** — a
> figure the user never wrote, a tool taken from the job ad, the user's work moved under the
> wrong employer. A tailored CV goes out under the user's name and the user will not
> proofread every variant, so the model's mistakes must be caught. What the user chooses to
> claim is the user's business: anything written in their vault is theirs, and sluice never
> asks them to prove it.

Consequences, each decided in conversation: the profile stays a plain paragraph (no
per-sentence citations); the CV Layout needs no `verified:` stamp; the skills list needs no
evidence beyond being a verified vault skill or tool; a skill or bullet over budget is
trimmed and reported, never refused. Checks run over the text the MODEL wrote; vault text
the user wrote is never refused by them.

## 3. Decisions

- **D1 — Structured composition.** The composer returns JSON content: a profile, bullets per
  role slot with their citations in a separate field, and skills picked from a closed list.
  sluice assembles the CV document and renders it. No check parses CV text any more.
- **D2 — The vault is the single source.** A baseline CV is something a user populates the
  vault from; it is not read when composing.
- **D3 — A CV Layout note** holds the role structure (headings, dates, roll-ups, budgets),
  certificates, education and the skills cap. Composing requires it.
- **D4 — `Tools:` replaces `Skills:` as the attribution index** on experience entries.
  `Skills:` is no longer read as data. Its PRESENCE is read once, for the upgrade warning
  (§6.6). Existing values stay in the notes as plain tags.
- **D5 — SKILLS per CV from verified vault skills.** The pool is the CV names of verified
  Skills Inventory notes (D13) plus the `Tools:` items of verified experience entries. The
  model picks; an off-pool pick is dropped and reported.
- **D6 — Employer scoping** (owner decisions after both review rounds). A bullet may cite
  only entries eligible for its role:
  - an entry is eligible for each role whose employers its `Company:` names;
  - an entry whose company is listed under the layout's `any_role:` is eligible for every
    role;
  - every other entry, including one with a BLANK `Company:`, is eligible for NONE. Its work
    can never land under another employer, and `doctor` lists it in the default view unless
    the layout lists its company under `omitted:`.
- **D7 — #368.** The term check stops reading negatives. `fabrication_decoys` (already a
  hard check) is where a "never claim X" lives, matched as a whole term and validated when
  the config loads. `cv.negatives` is composer guidance only.
- **D8 — Transport.** JSON is requested in the prompt on every backend; no backend changes.
  Real composes are measured before merge (§12.4); the shape of the one contingency is
  decided now and built only if the measurement requires it.
- **D9 — One PR, one major version** (the owner's instruction). The change is breaking for
  every install that composes CVs (§10).
- **D10 — Budgets** (owner decision). An absent (or valueless) `bullets_max`/`skills_max`
  means no cap. An explicit `0` means none: a role listed with no bullets, or a CV with no
  SKILLS section.
- **D11 — Retired keys raise** (owner decision, the repo's existing rule for retired keys).
  `cv.employers` and the root `baseline_rel` stop every command with a message naming the
  CV Layout. Both are checked in `core/config.py::load_config`, which runs before every
  command.
- **D12 — A verified Skills Inventory note's name is CV content.** That is a new licensing
  channel, so it gets its own `EvidenceKind` flag (§4.4) and every message that tells a user
  what verifying a skill does is keyed on it.
- **D13 — A skill's CV name comes from a `Label:` field** (owner decision after round 2).
  `skills add` writes the name the user typed into `Label:`, because the note's filename is
  a slug (`C#` becomes `c.md`). A note without a label uses its title.
- **D14 — Non-blocking warnings are shown by default** (owner decision after round 2). The
  "not on your CV", "no company" and "attribution check off" rows appear in `doctor`'s
  default view as warnings that block nothing, so `doctor --require cv` still passes.

## 4. Vault data model

### 4.1 The CV Layout note

`Job Applications/CV Layout.md`, beside the Candidate Profile note. YAML frontmatter, read
with `yaml.safe_load` behind `core/vault.py`'s existing guarded import (PyYAML is a declared
runtime dependency).

Every value below is a placeholder, and lists are written as block lists. That matters: in
YAML's inline `[a, b]` style an unquoted comma SPLITS an item, so `[Example, Inc]` would load
as two employers. `docs/CONFIGURATION.md` states the rule beside the example: an item
containing a comma must be quoted or sit on its own block-list line.

```yaml
# skills_max: <n>                     # optional; absent = no cap, 0 = no SKILLS section
roles:                                # CV order, top to bottom; required, non-empty
  - heading: "Example Alpha"          # required
    from: "<MM/YYYY>"                 # required
    to: "<MM/YYYY or present>"        # required
    location: "<location>"            # optional, default ""
    title: "<title>"                  # optional, default ""
    # bullets_max: <n>                # optional; absent = no cap, 0 = no bullets
  - heading: "Example Group"          # a roll-up: one heading over several employers
    from: "<MM/YYYY>"
    to: "<MM/YYYY>"
    employers:                        # optional, default: the heading
      - "Example Beta"
      - "Example Gamma"
any_role:                             # optional: companies whose entries fit any role
  - "<company>"
omitted:                              # optional: companies deliberately left off the CV
  - "<company>"
certificates:                         # optional
  - "<certificate>"
education:                            # optional
  - "<institution, dates | qualification>"
```

**Placeholders refuse.** `parse_layout` rejects any string carrying one of the example's
`<…>` placeholder tokens, naming its path. A verbatim or half-edited copy therefore refuses
at every placeholder, never renders one.

Validation is pure: `core/layout.py::parse_layout(mapping) -> CvLayout`, raising one
`LayoutError` that lists EVERY problem found, each naming its path (e.g.
`roles[3].from: '13/2020' is not MM/YYYY`).

- The frontmatter must be a mapping.
- `roles` must be present: a non-empty list of mappings.
- Per role:
  - `heading` is a non-empty string.
  - `from` is `MM/YYYY` with month 01–12. `to` is the same, or `present` in any case
    (normalised to lower case). `from` may equal `to`, but may not be after it.
  - `location` and `title` are strings. `employers` is a non-empty list of non-empty
    strings.
  - `bullets_max` is an int ≥ 0, with `bool` refused before `int` is checked (PyYAML reads
    `yes` as `True`). A valueless `bullets_max:` reads as absent, never as 0.
  - `heading`, `location` and `title` may not contain `|` (it separates the meta line's
    fields, §7.2), and may not equal a section heading (`cv/document.py::SECTION_HEADINGS`,
    compared stripped and case-folded).
  - **An unknown key inside a role is an error**, because that is where a typo
    (`bullet_max`) would otherwise be silently ignored.
- At the top level:
  - `skills_max` follows `bullets_max`' rule.
  - `certificates`, `education`, `any_role` and `omitted` are each absent or a list of
    non-empty strings. A scalar is an error naming the key; a valueless key reads as absent.
  - **A per-role key at the top level** (`bullets_max`, `heading`, `from`, …) is refused by
    name ("bullets_max belongs inside a role").
  - **A near miss of a known key** — `difflib` at a 0.8 cutoff against `roles`,
    `skills_max`, `certificates`, `education`, `any_role`, `omitted` — is an error saying
    which key was meant. Every other unknown top-level key is ignored, because Obsidian and
    the user keep their own metadata there (`base:`, `tags:`, `aliases:`, `description:`,
    `notes:`).
  - A company listed under `omitted:` and also under `any_role:` or a role's `employers` is
    an error: the two say opposite things.
- **Every string** in the layout refuses a control character (`core/safeout.py::is_control`)
  and a line break (`\n`, `\r`, U+2028, U+2029). `script` re-reads the canonical text as
  lines (§7.2), so a vault string must not be able to forge one either.
- Role ORDER is not checked, and neither is anything else that is simply the user's choice.
  The old `NOT REVERSE-CHRONOLOGICAL` check existed because the model could reorder roles;
  it cannot now.

**Store seam:** `Store.read_cv_layout() -> CvLayout | None`, must-support like
`read_candidate_profile`, with three outcomes:

| Outcome | Cause |
|---|---|
| `None` | absent: `FileNotFoundError`, `IsADirectoryError`, `NotADirectoryError` |
| `LayoutError` | malformed: any §4.1 rule, a `yaml.YAMLError`, a `RecursionError`, or PyYAML unavailable (naming the package) |
| `OSError`/`ValueError` | unreadable: the symlink refusal (like the evidence readers), a permission error, non-UTF-8 |

`LayoutError` subclasses `ValueError`, so the existing `(OSError, ValueError)` catches still
hold; a caller that reports the three cases differently catches `LayoutError` first.

**Contract types** live in `core/protocols.py`, beside `CandidateProfile`: `CvLayout`, its
per-role type `LayoutRole`, `LayoutError`, and the moved `Role`/`CvDocument` (§7.1).
`core/layout.py` holds the bodies: `parse_layout`, the matching in §4.3 and `build_slots`.

### 4.2 Experience entries: `Tools:`

- `EVIDENCE_KINDS["experience"].fields` replaces `Skills` with `Tools`. `experience add`
  derives its flags from that tuple, so it gains `--tools` and loses `--skills`. An MCP
  `propose_evidence` call carrying `Skills` is then refused as an undeclared field, the
  refusal any unknown field already gets.
- **The legacy key's presence is still visible.** A new read-only `EvidenceKind.legacy_fields`
  (`("Skills",)` for experience) makes `Vault._evidence_entries` report, under an entry key
  of its own and never inside `fields`, whether the note carries a non-empty value for each.
  The CLI flag builder, the evidence wizard, `_render_evidence_note` and MCP
  `propose_evidence` read `fields` only, so `--skills` stays gone and a `Skills` proposal is
  still refused. A conformance row makes a second store surface it too. Only §6.6 reads it.
- `Tools:` holds named tools, technologies, methods and standards whose attribution the user
  wants checked. It is comma-separated or a YAML block list, the two spellings `Skills:`
  accepts today. sluice polices exactly what is listed, in whatever case it is written: the
  user's declaration is the rule. An entry DECLARES tools when `tool_items(entry)` is
  non-empty; a blank `Tools:` declares none.
- **The per-token rule stays** (`cv/bundle.py::SKILL_TOKEN_RE`, moved and renamed for
  tools): every token of an item must begin with a letter, or a dot then a letter. Span
  removal (§6.1) subtracts from the numeric gate, and an item such as `Result 92` would let a
  model's invented `92` vanish; a digit-led name such as `ISO 9001` has the identical shape
  and is refused with it. `docs/CONFIGURATION.md` says so beside the word "standards".
- **A bad value fails once, before any spend.** `cv/engine.py::missing_prerequisites` reads
  every verified entry's `Tools:` once per run, and `doctor` carries a DEAD row (blocking
  `cv`) naming the entry and the item. Today the equivalent error is raised inside
  `build_bundle`, after each lead's dossier fetch, once per lead.
- `EntrySources.skills` becomes `EntrySources.tools`; the reader moves to `core/tokens.py`
  (§9.5) as `tool_items`.

### 4.3 Mapping entries to roles: `Company:`

- `employers_of(entry)`: the whole `company` value, plus its parts split on `,` `;` `/`. Each
  is folded with `core/vault.py::_fold_note_name` — the repo's one name fold (case and
  Unicode canonical equivalence) — and whitespace-collapsed; empties are dropped. Taking the
  whole value as well as the parts keeps an employer whose own name contains a comma
  matchable.
- A role's `employers`, and the `any_role` and `omitted` lists, are folded the same way but
  never split: each list item is one employer. Splitting them would let `Example, Inc` match
  any entry whose company merely ends in `Inc`.
- Eligibility (D6), over VERIFIED entries:

| The entry's `Company:` | Eligible for | `doctor` |
|---|---|---|
| matches a role's `employers` | each role it matches | — |
| matches an `any_role` item | every role | — |
| matches an `omitted` item | no role | — |
| blank | no role | warning: "no company" |
| matches nothing else | no role | warning: "not on your CV" |

- **Making an entry fit any role is explicit:** give it a company (for example
  `Across roles`) and list that under `any_role:`.
- `core/layout.py::build_slots(layout, entries)` builds the slot table — `R1`…`Rn` in
  layout order, each with its role, its eligible entry ids and its effective budget — **once
  per lead**. That one value is passed to the prompt, `parse_reply`, the selection step, the
  checks, `assemble` and the report, so no two of them can disagree about what a slot may
  cite.
- **A slot with no eligible entries** has an effective budget of 0: it is shown to the model
  as heading-only, and anything written there is trimmed, so it never costs a retry.

### 4.4 The skills pool

- The CV NAMES of verified entries of every evidence kind whose new
  `EvidenceKind.names_in_skills_pool` flag is set (`skills` only), then the `Tools:` items of
  verified experience entries. A skill note's CV name is its `Label:` value when non-empty,
  else its title (D13). Duplicates are removed case-insensitively, keeping the first
  spelling, so a Skills Inventory name wins over a `Tools:` item.
- An item matching a `fabrication_decoys` term (whole-term, §8) is excluded from the pool.
- An empty pool, or `skills_max: 0`, means no skills are requested and the CV has no SKILLS
  section.
- An unreadable Skills Inventory composes with the `Tools:` half alone and is flagged on the
  result (`skills_unreadable`), as today: a framing-quality problem never costs a lead (#165).
- **`Label:`** joins `EVIDENCE_KINDS["skills"].fields`, so `skills add` gains `--label`.
  `cmd_evidence_add` fills `Label` from the raw `--name` when `--label` is not given, so the
  name the user typed survives the filename slug.
- **What verifying a skill note now means** (D12): `evidence/commands.py::verify_outcome`,
  `core/doctor.py::classify_store`'s skills row, the MCP `propose_evidence` detail and the
  citability prose in `.rulesync/rules/CLAUDE.md` and `docs/ARCHITECTURE.md` are keyed on
  `names_in_skills_pool`, and say that a verified skill's name may be listed on CVs. The flag
  governs the NAMES route only; an experience entry's `Tools:` reach the pool through
  `tool_items` regardless, and the flag's docstring says so.
  `test_cited_by_gate_is_exactly_what_bundle_sources_actually_licenses` gains an
  execution-derived sibling: build a pool from seeded data and assert whose names it holds.

### 4.5 What is no longer read

- The baseline CV (`baseline_rel`): not when composing, not by `doctor`.
- `Skills:` on experience entries, except its presence for §6.6.

## 5. Composition

### 5.1 The prompt

- The job ad and triage's framing for the lead (#329), as today.
- The verified experience entries, each with its id, `Company:`, title, body, figures and
  `Tools:`. This is `cv/bundle.py::render_composer_bundle` minus the baseline block and the
  `Skills:` line, plus a `Tools:` line.
- The Skills Inventory as framing, as today.
- **The role slots** from `build_slots`: slot id, heading, dates, title, the entry ids the
  slot may cite, and its effective budget. A 0-budget slot is shown as "heading only, no
  bullets".
- **The skills pool** as a closed list, with `skills_max`, unless no skills are requested.
- `cv.negatives`, presented as the user's guidance.
- The rules:
  - tailor rather than write, and invent nothing;
  - every bullet lists in `cites` the entries it draws on, including every entry it takes a
    figure or a tool from;
  - text never contains brackets, citations or line breaks;
  - order bullets most relevant first, and pick skills only from the list;
  - the profile is in the first person, two to three sentences;
  - the banned phrases (`cv/slop.py::_PHRASES`, rendered as today), and no em dash or
    double hyphen;
  - reply with JSON only, in the given shape.
- The two places that name claim sources — `_RULES`' framing bullet and
  `cv/bundle.py::_DERIVED_NEGATIVE_PROMPT` — drop "the BASELINE CV". They say that claims
  in the profile and bullets rest on the verified experience entries, that the Skills
  Inventory is framing for those, and that it IS a source for the skills list.
  `test_the_derived_constraint_names_the_same_claim_sources_as_the_prompt_rule` keeps the
  two in agreement.
- **Neutrality reach.** Every new block of shipped prompt text — including the retry block,
  which now also lists the previous attempt's drops — lives in `build_prompt` or in a
  `*PROMPT*`-named constant, so `tests/test_prompt_neutrality.py`'s discovery reaches it;
  `render_composer_bundle`'s own section headers are brought into that sweep too. The JSON
  shape shown to the model uses placeholders only (§5.2).

Gone from the prompt: the baseline CV, the text format contract, `_employer_line`, and every
`_SKILLS_*` constant (`skills_requested` goes with them).

### 5.2 The reply

The shape, with the placeholders the prompt itself uses. `_prefix` can produce none of them,
and `parse_reply` refuses a reply that still carries one, so a backend that echoes the
example cannot ship it:

```json
{
  "profile": "<profile>",
  "roles": {"<slot>": [{"text": "<bullet>", "cites": ["<id>"]}]},
  "skills": ["<skill from the list>"]
}
```

`parse_reply(obj, slots) -> Reply | list[str]`, pure, in `cv/reply.py`. It returns a typed
reply or `REPLY:` findings, each naming the field. They are listed here so the plan can pin
one row each:

- the top level is not an object;
- `profile` is missing, not a string, or empty;
- `roles` is missing or not an object;
- a key in `roles` is not a slot id (`REPLY: unknown slot 'R9'`);
- a slot's value is not a list;
- a bullet is not an object, its `text` is not a non-empty string, or its `cites` is not a
  list of strings;
- `profile` or any bullet `text` contains `[` or `]` — citations belong in `cites`, and a
  bracket could otherwise hide a figure from the check (§6.1);
- `profile` or any bullet `text` contains a control character (`core/safeout.py::is_control`)
  or a line break (`\n`, `\r`, U+2028, U+2029), or equals a section heading
  (`SECTION_HEADINGS`, stripped and case-folded) — `script` re-reads the canonical text as
  lines (§7.2);
- any string carries one of the prompt's placeholders (`<profile>`, `<slot>`, `<bullet>`,
  `<id>`, `<skill from the list>`).

Two shape rules are evaluated over the SELECTION rather than the raw reply (§6.0), because
they are about what would render:

- **no bullets at all:** a `REPLY:` finding when, after selection, no slot with a non-zero
  effective budget holds a bullet, while at least one such slot exists. A layout whose every
  effective budget is 0 renders headings only, as configured.

`skills` is the exception to refusal: a missing value is none, and a malformed one (not a
list of strings) drops the whole pick list and reports it, never refuses. The SKILLS section
is framing-grade content, and §4.4's rule is that framing never costs a lead.

Unknown keys — at the top level or inside a bullet object — are ignored. A slot left out
renders with its heading and no bullets.

### 5.3 Reading the reply

`cv/reply.py::extract_json(text)`:

1. **Candidates, in order:** the content of each fenced block (```` ``` ```` or
   ```` ```json ````), stripped of surrounding whitespace; then each `{` in the text.
2. **Decoding:** `json.JSONDecoder(object_pairs_hook=…).raw_decode` from the candidate's
   start. The hook refuses duplicate keys with a dedicated error, which becomes
   `REPLY: duplicate key '<key>'` and is reported, never skipped past to the next `{`.
3. **Choice:** the first decoded object carrying both `profile` and `roles` wins; failing
   that, the first decoded object. Chat after the object is ignored, and a stray `{}` in a
   preamble cannot beat a real reply.
4. **Nothing decodes** → `REPLY: no JSON object in the reply`.

This replaces `compose._unwrap_agent_envelope`. `cv/compose.py::compose` returns the raw
text and the engine extracts and parses, so each attempt's artefact is the reply exactly as
received.

## 6. Checks

### 6.0 The order, per attempt

1. `extract_json`, then `parse_reply`. Shape findings are hard findings.
2. **Select:**
   - skills: drop picks that are off the pool, then duplicates, THEN apply `skills_max` to
     what remains, so a rejected pick never uses up the cap;
   - bullets: trim each slot's bullets beyond its effective budget, keeping the first N, by
     slot id (never by the reply's key order).

   The result is ONE selection: it is what is checked, retained, assembled, audited and
   rendered.
3. The zero-bullet rule (§5.2) over the selection.
4. The §6.1 hard checks over the selection.
5. The §6.3 style tier over the selection.
6. Retain:
   - `best` carries the selection, its drop report and its style findings as one value, and
     the engine's rebind rebinds all of them;
   - the retention rule is unchanged: keep the hard-clean attempt with the fewest style
     findings;
   - a bullet trimmed away is never checked or audited, so it can never cost a retry or a
     lead.

After the loop, the engine:

1. assembles the retained selection;
2. writes `cv.rendered.md` (`to_text` with cites) and audits the selection's own excerpt
   (`audit_text`, §6.4);
3. returns here on a dry run;
4. otherwise renders, then holds or writes, as today.

### 6.1 Refused — fed to the single retry, then the lead is skipped

| Finding | Rule |
|---|---|
| `REPLY: …` | a shape finding from §5.2/§5.3 |
| `UNCITED BULLET` | a bullet with an empty `cites` list |
| `BAD CITATION` | a cite that is not an entry id in the bundle |
| `WRONG EMPLOYER` | a cite to an entry not eligible for this slot. The message says whether the entry belongs to another role, is not on the CV, or has no company |
| `INVENTED METRIC` | a figure in a bullet absent from its cited entries' figures |
| `INVENTED PROFILE METRIC` | a figure in the profile absent from every entry's figures |
| `MISATTRIBUTED TOOL` | a bullet names an item from the verified `Tools:` vocabulary that none of its cited entries licenses |
| `FABRICATED` | a `fabrication_decoys` term in the profile or a bullet |
| `SLOP EM-DASH` / `DOUBLE-HYPHEN-DASH` | in the profile or a bullet |

Every row reads the text the model wrote, never vault text. In particular `cv/slop.py::check_hard`
runs over the profile and the kept bullets, not over the whole document: a vault string with
an em dash (an education line a word processor auto-corrected) is the user's and renders,
where today it would bin every lead with a finding no retry can clear.

**Figures.**
- **Tool names are removed first.** Each matched `Tools:` TOKEN's own character span is
  replaced by a space in the original text; the gaps between tokens are never removed, so no
  new digit run can form. A token matches only where its neighbouring characters are not
  alphanumeric in any script.
- **Which tools license a removal:**
  - for a bullet, its cited entries' tools only;
  - for the profile, every entry's `Tools:`, and never the pool's Skills Inventory names
    (#165: a skills digit is licensed in no pool).
- **Matching is by token sequence, never substring.** A licensed `Widget3` must not launder
  `Widget30`.
- **What remains is scanned for digits in ANY script:** every character for which
  `unicodedata.digit(c, None)` is not `None` (full-width, Arabic-Indic, superscript). Each
  run is normalised to ASCII, on BOTH sides of the comparison: a bullet's figures and the
  cited entries' figures alike.
- **No bracket is stripped**, because §5.2 refuses brackets in text.
- **Negatives license no figure.**

**`MISATTRIBUTED TOOL`.**
- **It runs whenever ANY verified entry declares `Tools:`** (§4.2). A bullet names an item
  when the item's tokens appear in it as a token sequence with identical case.
- **A cited entry licenses a tool when either holds:**
  - its `Tools:` lists the tool;
  - its own title or body names the tool by the SAME case rule — identical case, except that
    an all-lowercase declared tool also accepts a capitalised mention. A body saying
    `go-live` therefore never licenses `Go`, and a body saying `Coaching` at the start of a
    sentence licenses a declared `coaching`. The entry's `Company:` is never text that
    licenses.
- **What that leaves refused:** a tool the cited entries never mention, which is the real
  misattribution.
- **What it avoids:** refusing a true word on a partially annotated vault, the false
  positive today's per-entry abstain exists for. The vault-wide switch replaces that
  abstain. Span removal stays `Tools:`-only, so a body word never blanks a digit.

**`FABRICATED`.**
- **How decoys match:** whole, case-insensitive token sequences (§8), within one field. A
  decoy never matches across the profile and a bullet, or across two bullets.
- **Sentence punctuation breaks a phrase.** In the shared matcher, `.` `;` `:` `!` `?`
  followed by whitespace ends a run of adjacent tokens, so `Example Health` never matches
  `at Example. Health checks ran`. This applies to tool naming too; an internal dot
  (`Node.js`) is part of its token and unaffected.
- **What a decoy may be:** load-time validation (§9.3) refuses any decoy the shared tokeniser
  cannot represent, so a configured ban can never be silently unmatchable.

### 6.2 Dropped and reported — no retry

The selection step's drops are reported on the result (§9.2), describing the RETAINED attempt:

- skill picks off the pool, compared case-insensitively (a kept pick renders in the POOL's
  spelling, never the model's); duplicates; picks beyond `skills_max`;
- bullets beyond a slot's effective budget.

A drop never triggers a retry. If a retry happens for another reason, its prompt lists the
drops so the model can choose better.

### 6.3 Style tier — unchanged except in scope

`cv/slop.py::check_phrases`, the #194 term check (`cv/terms.py::unbundled_terms`) and the
opt-in voice check (`cv/voice.py::run_voice`) run over the selected profile and bullet
texts: the text the model wrote and nothing else. A slop stem inside a Skills Inventory name
or a certificate is the user's and draws nothing. Unchanged: `cv.style_hold`, the retry
contract and the retention rule.

### 6.4 Advisory audit — scoped to what the model wrote

`cv/document.py::audit_text(selection, slots)` builds the audit's own excerpt: the profile,
then per role its heading and its kept bullets with their ` [ID]` cites. It carries no meta
line, contact, certificates, education or SKILLS. `run_audit` receives that excerpt and
nothing else; `to_text` never reaches the auditor.

The auditor's corpus is `render_bundle` (no baseline) with each entry's `Tools:` line added
through `_source_section`'s `entry_lines` override, using an emitter other than
`_entry_block`, so `bundle_sources` never harvests a figure from it. That reverses
`_entry_skills_line`'s "a claim resting on `Skills:` alone must read unsupported" posture for
entry-declared tools: the hard gate licenses a tool through `Tools:`, so the auditor must see
the same evidence, or every tool-naming bullet would be held.

Vault-sourced fields are not audited. The auditor has no truth for them, and auditing them
would hold almost every CV under the shipped `cv.require_signoff: true`.
`cv.require_signoff` otherwise governs holds as before.

### 6.5 Gone

These can no longer fail, now that the structure is data:

- `MISSING EMPLOYER` and `NOT REVERSE-CHRONOLOGICAL`;
- `UNSOURCED SKILL`;
- every `FORMAT:` refusal;
- the `STRUCTURAL:` header and name/contact guards;
- `MISATTRIBUTED SKILL`, replaced by `MISATTRIBUTED TOOL`.

### 6.6 The attribution check must not go dark silently

`attribution_check_off` reports the check's actual state: true whenever no verified entry
declares `Tools:`. It rides on `CvResult`, `run.json` and the MCP result, shaped like
`skills_unreadable`.

The LOUD signals fire only for an upgraded vault — some verified entry still carries a
non-empty legacy `Skills:` (§4.2) and none declares `Tools:`:

- **`doctor`:** a warning row in the default view (D14), naming the remedy.
- **`cv run`:** one WARNING per run.

A vault with neither field is an unconfigured install: the flag is true, because the check
is off, but nothing warns, consistent with empty-config-abstains.

## 7. Building and rendering the CV

### 7.1 Assembly

`cv/document.py::assemble(layout, slots, selection, candidate) -> AssembledCv`, pure:

- **`name`:** `full_name(profile)` upper-cased, which keeps today's output: the composer was
  asked for the name in capitals and the parser kept that line.
- **`contact`:** from the Candidate Profile (`core/candidate.py`).
- **`profile`:** from the selection.
- **`work`:** one `Role` per layout role, in layout order, each found by its SLOT ID — never
  by zipping the reply's slots against the layout:
  - `company` = heading;
  - `dates` = `from–to` with an en dash, or `MM/YYYY–present`;
  - `location` and `title` from the layout;
  - `bullets` from that slot's kept bullets, with their cites kept aside.
- **`skills`:** the kept picks, in the model's order, each in the pool's spelling.
- **`certificates` and `education`:** from the layout.

`AssembledCv` carries the public `CvDocument` plus each bullet's cites. `CvDocument` and
`Role` keep their exact fields, because they are the contract user templates are written
against, and move to `core/protocols.py`.

`cv/document.py` also owns `SECTION_HEADINGS`: the one tuple of section headings `to_text`
writes. Anything that needs the heading set derives it from there.

### 7.2 The renderer seam

- **The new signature** is `Renderer.render(document: CvDocument, out_dir, *, neutral_name) -> str`.
- **`precheck` is removed** from the seam (`core/protocols.py`), from `template` and from the
  engine. Docstrings that cite it as the precedent for an optional seam member are
  re-anchored on `Store.preflight`, which survives.
- **`template`** renders the document directly. The shipped template and the documented
  example (`docs/cv-template-example.html.j2`) drop empty meta fields with
  `select | join(" | ")`, so an untitled role renders cleanly. A user's own template is
  outside the repo's view, so the CHANGELOG tells users that a role can now have no title.
- **`script`** writes `to_text(document)`, citation-free, and runs the script as before.
  Its meta line is POSITIONAL, `dates | location | title`, always three fields, an empty
  field written as empty. §4.1 refuses `|` in those fields, so a script's field positions
  stay fixed. The en-dash date separator and the positional line are stated in the
  CHANGELOG for script authors.
- **`Sluice.compose_cv`** no longer resolves the renderer on a dry run; it did so only to run
  `precheck`.

### 7.3 The canonical text

`cv/document.py::to_text(document, *, cites=None)` writes:

1. the contact lines and the name;
2. `PROFILE`;
3. `WORK EXPERIENCE`, each role as its heading line, its meta line, then `- bullet` lines;
4. `CERTIFICATES`, `EDUCATION` and `SKILLS`, each written only when non-empty.

`cites` is `AssembledCv`'s per-bullet cite lists:
- **Given it,** each bullet ends with its ` [ID]` citations. That form goes to
  `cv.rendered.md` only.
- **Without it,** the text is citation-free. That form goes to `script`.

The audit's input is `audit_text` (§6.4); the voice excerpt is the profile and bullet texts
alone (§6.3).

### 7.4 Artefacts

- **Each attempt's raw reply** is saved as `reply.attempt-N.txt`. The extension promises
  nothing about content, because a reply can be chat-wrapped.
- **`cv.rendered.md`** is `to_text` with cites: the document sluice built, with each bullet's
  citations. It is no longer "the text handed to the renderer", because `template` now
  receives the document itself.
- **`run.json`** gains `skills_dropped`, `bullets_trimmed` and `attribution_check_off`.
- **`_OWNED`** matches the new names AND the legacy `cv.attempt-N.md`, so a 3.x run's
  attempt files are cleared by the first 4.0 run in the same directory.

## 8. #368 and related

- **The term check's vocabulary.** `cv/bundle.py::mention_vocab` is built from:
  - the experience entries: heading line, body and `Tools:`;
  - the Skills Inventory framing;
  - the CV Layout's text.

  It subtracts nothing, and the baseline leaves it with §4.5.
- **`fabrication_decoys`** match as whole, case-insensitive token sequences through
  `core/tokens.py`'s shared matcher, with the sentence-punctuation boundary of §6.1. A
  whole-term match no longer fires on a longer token that merely contains the decoy (the way
  `Java` sits inside `JavaScript`), and a multi-word decoy matches as a phrase. A decoy is
  refused at load (§9.3) when it tokenises to nothing, or when its tokens drop any non-space
  character. A non-Latin, accented or punctuation-bearing decoy is therefore refused by name,
  never left silently unmatchable; a hyphenated compound can be written with a space
  (`co founder`), which matches the hyphenated form because both tokenise alike.
- **`cv.negatives`** keeps its name and becomes free-text guidance to the composer. No check
  reads it. `docs/CONFIGURATION.md` says so, and says where a ban belongs.
- **`classify_negatives_vs_skills` retires.** It reads negatives as prose bans through a
  negation heuristic, which is the parsing D7 removes. In its place, `doctor` reports a
  warning row (D14) when a decoy matches, as a whole term, a verified entry's `Tools:` item,
  a verified Skills Inventory CV name, or any CV Layout string. Such a ban contradicts the
  user's own data: it keeps a tool or skill out of the pool (§4.4), and a layout string is
  rendered regardless, because it is the user's.

## 9. Everything else

### 9.1 `doctor` and prerequisites

**Reading the layout once.** `Sluice.doctor` calls `Store.read_cv_layout` ONCE, in its own
`try` (the #259 shape, so one bad note never collapses the store rows), and classifies the
outcome with a pure `classify_cv_layout`. When it parsed, the same value is passed, with the
verified experience entries `doctor` already reads, to the eligibility classifier.
`Vault.preflight` records nothing about the layout.

- **Row `store / cv_layout`** (blocking `cv`):
  - `OK`, with the role count;
  - `SETUP` when absent;
  - `DEAD` when malformed or unreadable, with the reason.

  A row pins it in agreement with `cv run`'s refusal and `doctor --require cv` for absent,
  malformed, unreadable and valid layouts, as the baseline and Candidate Profile are pinned
  today.
- **DEAD row, blocking `cv`:** a `Tools:` value the token rule refuses, naming the entry and
  the item.
- **Warning rows (D14)** — listed in the default view, blocking nothing:
  - entries "not on your CV" (§4.3);
  - entries with "no company" (§4.3);
  - the attribution check off on an upgraded vault (§6.6);
  - a decoy contradicting the user's own data (§8).

  The eligibility-derived rows are computed only when the layout parsed; an absent,
  malformed or unreadable layout produces exactly the `cv_layout` row. Only verified entries
  count.
- **The printer change behind D14.** A `ComponentCheck` gains a `warn_by_default` flag. The
  default view lists DEGRADED rows that carry it, under a heading that says they block
  nothing; `--require` is unaffected and `--strict` still fails on them, as on any DEGRADED
  row. Rows printed today keep today's behaviour.
- **Removed:**
  - the `baseline_rel` row;
  - both `Skills:` rows (`classify_skills_request`, `classify_skills_reconciliation`);
  - `classify_negatives_vs_skills`.
- **`cv/engine.py::missing_prerequisites`** — once per run, before any spend, replacing the
  baseline check:
  - the CV Layout, with one sentence each for absent, malformed and unreadable;
  - `Tools:` readability;
  - **no citable slot:** when no slot has both a non-zero budget and an eligible entry, while
    some role has a non-zero budget (a misconfiguration such as legal-suffix company names
    the layout does not match), naming the `doctor` rows to read.
- **`run_one` refuses before any spend when the layout reads `None`** (a `skipped-config`
  refusal, like the blank-identity one). It never treats `None` as zero roles. A row covers a
  layout deleted between the prerequisite check and `run_one`.

### 9.2 Reporting

`CvResult` gains three fields, each describing the retained attempt:
- `skills_dropped: list[str]`;
- `bullets_trimmed: list[str]`, for example `R2 (Example Alpha): kept 5 of 7`;
- `attribution_check_off: bool`.

`cv run`'s summary and detail lines, `run.json` and the MCP `cv_run` result carry all three,
the way #194 added `terms`. `tests/test_docs_claims.py`'s expected summary-key set gains them.

`skills_dropped` is model-written text that a job ad can steer. So it joins the trigger for
`mcpserver.py::_CV_RUN_CONTENT_WARNING`, and the warning text names it.

### 9.3 Config and setup

- **Retired keys raise (D11),** both in `core/config.py::load_config`, beside
  `refuse_retired_locations` and in its shape: the root `baseline_rel`, and `cv.employers`
  read from the raw `cv:` block. Each message names the CV Layout. The existing
  `cv.baseline_rel` raise in `cv/config.py` is re-pointed at the CV Layout too: its "move it
  to the top level" advice would now send a user to a key that also raises.
  `tests/test_docs_claims.py::_RETIRED_CONFIG` and `Sluice.doctor`'s list of cv-config errors
  follow.
- **`fabrication_decoys` are validated in `load_cv_config`** (§8), each refusal naming the
  decoy.
- **`job-sluice init`** drops the `cv_employers` question. The two
  `tests/test_onboard_plan.py` legs that used it are retargeted, not deleted: the nested-list
  leg onto `triage.reject_companies`, and the cross-block leg onto the root `relevance_keep`
  list beside it, with the leg's comment rewritten to credit them.
- **`sluice.yaml.example`** loses both keys and gains a pointer to the CV Layout note.

### 9.4 Removed code

- `cv/parse.py`, the whole module. `CvDocument`/`Role` move to `core/protocols.py`.
- `cv/validate.py`'s text machinery: `section_spans`, the marker tuples and the line-based
  `validate`. **`cv/validate.py` stays the home of the §6.1 checks**, rewritten over the
  selection, so the review-routing tables that name it as the fabrication gate stay true.
- From `cv/compose.py`:
  - `_unwrap_agent_envelope` and its helpers;
  - `_REQUIRED_HEADERS`;
  - `_SKILLS_ATTRIBUTION_PROMPT_RULE`, `_SKILLS_FORMAT_PROMPT_RULE` and `_SKILLS_PROMPT_BLOCK`;
  - `_employer_line` and the text format contract.
- From `cv/engine.py`: the `STRUCTURAL:` guards, `_contact_key`/`_same_contact_reworded`/
  `_contact_rewording`, and the `precheck` call.
- `Store.read_baseline` and `Vault.read_baseline` (and `stores/vault.py::_make`'s
  `baseline_rel` argument); `Renderer.precheck`.
- `evidence/commands.py`'s `Skills`-specific display, which becomes `Tools`.

### 9.5 One home for shared derivations

`core/` may not import a sub-app. `doctor` and the gate must agree about the same facts, and
a second copy is how they would come to disagree. So each derivation `doctor` needs has one
home in `core/`, imported by both:

- **`core/tokens.py`:** `_WORD_RE`, `tokens`, `subseq` (with the sentence-punctuation
  boundary), `tool_items` and the decoy validator. They move out of `cv/bundle.py` and
  `cv/validate.py`, which import them back.
- **`core/layout.py`:** `parse_layout`, `employers_of`, eligibility and `build_slots`.

**The import cycle, and where it breaks.** `core/vault.py` needs `parse_layout` (for
`read_cv_layout`), and `core/layout.py` needs `core/vault.py::_fold_note_name`. Both at module
scope is an `ImportError` at startup. `core/layout.py` therefore imports the fold INSIDE its
one fold helper, the precedent `core/doctor.py` set for `evidence_slug`, with a comment
naming the cycle. Never a second copy of the fold. A row imports each module first in a
fresh interpreter.

**Employer matching is a third kind of fold consumer**, beside lead identity and archive
filenames. It sits on the identity side: a WIDER fold makes more entries eligible for more
roles, which loosens `WRONG EMPLOYER`, so the fold must not widen for it. The fold's
docstring, `docs/ARCHITECTURE.md`'s fold paragraph and `.rulesync/rules/CLAUDE.md` each
record it.

Rows pin `doctor` and the gate to the same answer over `_WORD_RE`'s edge cases — a
`#`-suffixed token, a dotted name, a leading-dot name, a trailing dot — in synthetic shapes.

## 10. Versioning

Breaking for every install that composes CVs — a `!` commit, released as 4.0.0. The
CHANGELOG entry (edited in the release PR) carries the migration steps:

1. Create `Job Applications/CV Layout.md` (shape in `docs/CONFIGURATION.md`); `cv run` refuses
   without it and `doctor` says so. An entry is matched to a role when its `Company:` (or one
   of its `,` `;` `/` parts) equals a role's heading or `employers` item, ignoring case and
   accents. Use `any_role:` for companies whose entries fit any role and `omitted:` for those
   deliberately left off the CV. Entries with a blank `Company:` are no longer citable: give
   them a company. Run `doctor` afterwards: its "not on your CV" and "no company" rows list
   every entry that still matches no role.
2. Add `Tools:` to experience entries. Once any entry declares it, the attribution check is
   on for every entry. `Skills:` is no longer read, and `experience add --skills` is now
   `--tools`. An MCP `propose_evidence` carrying `Skills` is refused.
3. Move any "never claim X" from `cv.negatives` into `fabrication_decoys`, which now matches
   whole terms and refuses a decoy it cannot match.
4. The baseline CV is no longer read; anything only it says must be in an evidence note to be
   used.
5. The SKILLS section now comes from verified Skills Inventory notes and entries' `Tools:` —
   review which skill notes are verified, because a verified note's name may now appear on
   your CVs. A note created with `skills add` before 4.0 has a slugged filename: add a
   `Label:` with the spelling you want on CVs.
6. Remove `cv.employers` and `baseline_rel`; both now stop every command until removed.
7. For `script` users: the meta line is always three positional fields, and dates use an en
   dash. For user templates: a role can now have no title.

## 11. Measurement and migration data stay private

Any real-vault measurement and any migration of a particular user's vault are done outside
the repo, and their results are shared with that user only. They never appear in a commit,
a PR body or comment, an issue, the CHANGELOG or a release note. If the PR must say that a
measurement happened, it gives aggregate counts only: for example, how many of N first
attempts parsed. It names no lead, employer, heading or skill. Drafts derived from a real
vault are written outside every worktree, and real-vault runs write their artefacts outside
every worktree too (run from a cwd outside them, or with `cv.output_dir` set outside); the
artefacts are deleted, or left with the owner, once the counts are taken.

## 12. Proof

### 12.1 New tests

All fixtures are synthetic: `tests/conftest.py`'s seeded faker, `Example …` names from the
reviewed rosters, layout titles drawn from the faker `titles` pool, and synthetic shapes —
never real technology names — for tool, decoy and tokeniser rows (an `Examplelang` /
`Examplelangscript` pair, a `#`-suffixed token, a dotted name, a leading-dot name). Every
fixture value is GLOBALLY unique, so any rendered string can be attributed to one source.

- **Layout:**
  - one row per §4.1 rule, plus a row showing that one malformed note reports ALL its
    problems;
  - `from == to` accepted, against `from > to` refused; `to: PRESENT` normalised;
  - a per-role key at the top level; a near miss (`skill_max`, `anyrole`, `role`,
    `certifications`) refused, against `tags`, `description` and `notes` ignored;
  - a scalar `certificates`; a valueless list key read as absent; a valueless
    `bullets_max:` read as absent, never 0;
  - `|`, a control character and a line break refused in their fields; a heading equal to a
    section heading refused; a company both `omitted` and in a role refused;
  - a placeholder left anywhere refused at its path.
- **YAML through the store:** the `Example, Inc` employer, `any_role` and education rows are
  written as YAML TEXT and read through `Store.read_cv_layout`, each with a quoted or
  block-list control, so the test runs the route a user's note takes.
- **Reply:** one row per §5.2 and §5.3 finding, including:
  - `roles` absent, misspelt at the top level, nested under another key, and present but
    empty;
  - a top-level array; duplicate keys reported by name;
  - fenced replies in the real captured shape (a newline after the opening fence), and a
    SEPARATING row: a decodable `{}` in chat before a real fenced reply, which only the
    fenced-first, `profile`-and-`roles` rule passes;
  - an echoed placeholder; a text equal to a section heading;
  - malformed `skills` dropped, not refused; unknown keys inside a bullet ignored.
- **Brackets:** ordinary gate rows, each asserting the specific finding so one deletion
  reddens it.
  - `[40%]` in a bullet and `[500]` in the profile each give the `REPLY:` bracket finding;
  - an inline `[EX1]` where `1` IS a cited figure gives the `REPLY:` bracket finding;
  - the bullet-side twin of `test_a_profile_non_id_bracketed_number_is_flagged`: the §6.1
    figure check, called directly on a selection carrying `[40%]`, gives `INVENTED METRIC`;
  - no document string matches `cv/render.py::_CITE_RE`.
- **Figures:**
  - full-width, Arabic-Indic and superscript figures, in a bullet and in the profile;
  - an entry written in native digits licenses the ASCII figure, and the reverse;
  - `Widget3` licensing never launders `Widget30`, including a full-width zero after it;
  - a figure between the two tokens of a licensed multi-token tool is still checked;
  - a fabricated figure beside a licensed tool;
  - span removal licensed by cited entries only;
  - pool names never strip a profile digit;
  - negatives never license a figure.
- **Employer scoping:** each pair straddles the check.
  - An entry eligible for R1 cited under R2 → `WRONG EMPLOYER`; the same entry under R1 →
    clean.
  - A bullet citing one eligible and one other-role entry → refused for the second cite only.
  - A role without `employers:` scopes by its heading.
  - `Example, Inc` never matches `Other, Inc`.
  - `any_role`, `omitted`, blank and unmatched → the §4.3 table, including each warning row.
  - NFC and NFD spellings of one name match.
  - A slot with no eligible entries is shown heading-only and costs no retry.
- **Slot binding:** a reply listing slots in reverse order, and one omitting a middle slot,
  each put every bullet under its own slot's role, with its own cites in `to_text` and its
  own heading in `audit_text`.
- **Tools attribution:**
  - licensed by declaration; licensed by the entry's own text;
  - a body saying `go-live`-shaped text never licenses a capitalised tool, straddled by a body
    naming it exactly; a lowercase-declared tool is licensed by a capitalised mention;
  - a body naming a longer token never licenses the shorter tool it contains (the
    `Examplelang`/`Examplelangscript` pair);
  - unlicensed → refused;
  - a lowercase listed item is policed as declared;
  - an unverified entry's `Tools:` joins neither the vocabulary nor the pool;
  - the switch both ways, with the OFF state pinned at every surface (below).
- **`attribution_check_off`:** four vaults, read through the real `Vault`, never a hand-built
  entry dict — a `Skills:`-only vault (flag true, warning row, one WARNING across two leads);
  a vault with neither field (flag true, no warning); any verified `Tools:` (flag false); and
  `Tools:` only on an UNVERIFIED entry (flag true). Each asserts the flag on `CvResult`,
  `run.json` and the MCP result.
- **Decoys:**
  - a decoy refuses its own whole term and passes a longer token containing it;
  - a lowercase spelling is refused; a multi-word decoy matches;
  - a multi-word decoy split across two fields, or across a sentence break, does not match;
  - the load validator accepts a `#`-suffixed token, a dotted name, a leading-dot name and a
    multi-word decoy; it refuses a tokenless, a non-Latin, an accented and a
    punctuation-bearing decoy, naming each; a space-separated compound matches its hyphenated
    form.
- **Skills pool:**
  - unverified Skills Inventory names and unverified `Tools:` are absent from the pool, and a
    pick of either is dropped and reported;
  - `propose_evidence('skills', …)` through the real store with a name the slug destroys
    (an `Examplelang#`-shaped name) renders that name, via `Label:`, in the SKILLS section;
  - the fixture's pick differs from the pool item in case only, and the document carries the
    POOL's spelling; a one-token difference is dropped;
  - the Inventory-first spelling wins; a decoy-matching item is excluded;
  - picks `[off-pool, A, A, B]` with `skills_max: 2` keep `[A, B]`, reporting the off-pool
    pick and the duplicate, with no cap drop.
- **Budgets:**
  - absent keeps everything; `0` keeps nothing; `bool` refused;
  - N == max trims and reports nothing; N == max + 1 trims one and reports it;
  - an over-budget bullet carrying an invented figure, a slop stem or an unbundled term costs
    no retry and is absent from the audited text;
  - every effective budget 0 with an empty reply renders headings only; bullets only in
    0-budget slots while an uncapped slot is empty give the zero-bullet `REPLY:` finding;
  - `skills_max: 0` requests no skills and drops nothing.
- **Retention:** attempt 1 is hard-clean but carries one surviving style finding (a
  `_PHRASES` stem in a kept bullet, `style_hold` off) plus drops; attempt 2 is hard-dirty
  with DIFFERENT drops. The row asserts EXACTLY two compose calls first, then that the
  rendered document holds attempt 1's selection and the result reports attempt 1's drops.
  The scripted backend asserts at teardown that every scripted draft was consumed.
- **Audit scope:** the row asserts on the TEXT the recording fake auditor received: it holds
  the profile and every kept bullet with its heading and cites, each cited entry's `Tools:`
  line is in its corpus, and it holds no layout date, certificate, education item, skill or
  trimmed bullet.
- **Vault text is never refused:** an em dash in a layout education line, certificate or
  heading renders with no finding, against the same em dash in a bullet, refused; a slop stem
  in a Skills Inventory name or a certificate draws no style finding, against the same stem
  in a bullet.
- **Renderers:**
  - `template` renders a document without parsing;
  - the `script` golden (§12.3);
  - hand-written literal `to_text` rows: an empty title gives `MM/YYYY–present | <location> | `
    (three fields); an empty location gives `MM/YYYY–MM/YYYY |  | <title>`; a 0-budget role
    gives its heading and meta line and no bullets; no SKILLS heading without picks;
  - every registered renderer and every test fake matches the new `render` parameters.
- **Provenance guard,** in four parts:
  1. A recursive leaf-path set over `CvDocument` and every nested dataclass (`work[].title`,
     `work[].bullets[]`, `skills[]`, …), checked against a closed provenance map with no
     default arm and three categories: vault-sourced, model-written, and model-selected but
     vault-valued (`skills[]`).
  2. A scope assertion on VALUES: every fixture leaf is non-empty and globally unique.
  3. A hostile reply carrying canary values at the top level and inside bullet objects (where
     the parser tolerates unknown keys), under keys named like every vault-owned field. The
     row first asserts the CV RENDERED (status `rendered`, renderer called once, document
     non-empty), then that no canary reached the document, `cv.rendered.md` or the audited
     text.
  4. One engine-level row through `run_one`, using the Retention row's two-attempt sequence,
     asserting that the document's reply-sourced leaves are attempt 1's.
- **Conformance:**
  - `read_cv_layout`: absent, declared round-trip, malformed, YAML error, `RecursionError`,
    PyYAML unavailable (the module's `yaml` patched to `None`) → `LayoutError` naming it,
    a non-UTF-8 note → a `ValueError` that is NOT a `LayoutError`, symlink, non-mapping;
  - `legacy_fields` surfaced by the store, outside `fields`;
  - `tests/conformance/seeds.py` gains a `layout=` seeder;
  - `read_cv_layout` joins `tests/test_mcpserver.py::_STORE_READ_METHODS`;
  - the FakeVault signature row is derived from the `Store` protocol members rather than
    hand-listed.
- **Doctor:** each new row in each state, asserted through `cli.py::_print_doctor_verdict`'s
  default output, not only the row's state; the layout agreement row; `doctor --require cv`
  agreeing with `missing_prerequisites` for every layout and `Tools:` state; zero
  eligibility rows when the layout is absent, malformed or unreadable.
- **Prerequisites:** a facade row through `Sluice.compose_cv` with two leads and one verified
  entry carrying a digit-led `Tools:` item expects one refusal naming the entry and the item,
  zero dossier fetches and zero compose calls; the same shape for "no citable slot".
- **Config:** each retired key raises with its message, run through a NON-cv command, with a
  control showing no raise for a clean config; decoy validation at load.
- **D12 messages:** `verify_outcome`, `classify_store`'s skills row and the MCP propose detail
  are keyed on `names_in_skills_pool`, mirroring the `cited_by_gate` rows.
- **MCP:** a non-empty `skills_dropped` carries `content_warning`.
- **Artefacts:** a directory holding a 3.x run's `cv.attempt-1.md` is cleared by a 4.0 run.
- **Imports:** `sluice.core.layout` and `sluice.core.vault`, each imported first in a fresh
  interpreter.
- **Sandbox:**
  - `tests/test_cv_backend_failure.py`'s two single-lead tests run in `tmp_path`;
  - a session-installed `sys.addaudithook` guard, armed per test, records any write-mode open,
    `mkdir`, rename/replace or `sqlite3.connect` that resolves (against the SESSION-START
    cwd) to a watched path, and fails that test at teardown. Recording rather than raising
    keeps a `sluice/` `except BaseException` arm from swallowing it; it sees only this
    process, so a concurrent real run cannot redden a test;
  - a session-end check catches subprocess writes the hook cannot see;
  - the watched set is DERIVED, never hand-listed: the `"./…"` string constants an AST walk
    finds in `sluice/` (excluding `core/paths.py`), pinned equal to that walk. A file-valued
    default is watched as that exact path, never its parent; a container-side path
    (`apply.camofox_cv_dir`) is excluded by name, with the reason;
  - a positive control proves the guard sees a write.

### 12.2 Guards carried, not retired

A rebuild is when guards get lost. Each item below is retargeted, and the plan names it:

- **Prompt neutrality** (`tests/test_prompt_neutrality.py`):
  - `_SYNTHETIC_ARGS` is rebuilt with SYNTHETIC-token slots, pool, guidance and retry input
    (`prior_violations` and the drops list), so every conditional block renders;
  - one coverage row per new block, including the retry header and the drops framing, in the
    pattern of `test_the_swept_cv_prompt_carries_the_triage_framing_text`; the same for the
    audit prompt's per-bullet block;
  - one finding of each new kind (`REPLY:`, `WRONG EMPLOYER`, `MISATTRIBUTED TOOL`) rendered
    through the retry override, so the message templates are swept;
  - the static CV-prompt guard points at every rule constant the new prompt renders;
  - never an `_EXEMPT` entry for a `sluice.cv` prompt.
- **Fixture-name neutrality** (`tests/test_fixture_name_neutrality.py`), each new collector
  with its own "finds something" floor and a "sees every shape it claims to" row on synthetic
  strings, and the scope pins bumped deliberately:
  - the skill collectors gain `Tools` in all four shapes (frontmatter, block list, kwarg,
    dict literal) BESIDE the existing `Skills`/`skills=`/`*_skills=` coverage, which now
    carries CV SKILLS values; a separate floor is kept for the residual `Skills:` fixtures;
  - routed to `_IDENTITY_COLLECTORS` (the employer roster): the layout's `heading` and
    `employers`, and the document's `company=` — in keyword, dict-key and YAML (flow and
    block) spellings;
  - routed to the skill roster (`_REVIEWED_SKILL_VALUES`): reply `skills` lists (a dict that
    also carries `profile` or `roles`) and Skills Inventory names and labels;
  - routed to the `LOCATIONS` / `Example Location` convention: `location=` and the layout's
    `location`;
  - checked for SYNTHETIC-token or `Example`-shape: `education=`, `certificates=` and their
    layout spellings;
  - a `title` collector accepts only an empty value, a SYNTHETIC or `<placeholder>` token, or
    a member of the seeded `tests/conftest.py` title pool;
  - `_CV_TEST_MODULES` reaches the new test modules, and the sweep (or a sibling) covers
    `scripts/*.py`;
  - one shared synthetic layout lives in `tests/conftest.py`.
- **Neutral defaults** (`tests/test_sluice_neutral_defaults.py`):
  - the only sanctioned deletions are the assertions on the two removed fields;
  - added: rows showing that `CvLayout`'s optional fields abstain (caps absent, `location`
    and `title` empty, lists empty, `employers` derived as `[heading]`);
  - added: `read_cv_layout` returns `None` with no built-in fallback;
  - added: a derived sweep showing that no `str` default on a swept config class is an
    absolute or `~` path, replacing the deleted `baseline_rel` relativity assertion, sharing
    the sandbox guard's scope assertion; the comments that cited the deleted assertion are
    re-pointed at it.
- **`composer_headings()`** (`tests/template_content.py`): re-derived from
  `cv/document.py::SECTION_HEADINGS`. It is the allowlist for the shipped-template and docs
  content sweeps, which are neutrality guards, not grammar-sync tests. `_skills_run`'s
  docstring is re-anchored on `SECTION_HEADINGS` and `to_text`.
- **Docs layout samples:** a sweep extracts every fenced YAML block with a top-level `roles:`
  key from `README.md` and `docs/*.md` (excluding `docs/superpowers/`), asserts `parse_layout`
  refuses each one naming every placeholder path, asserts each list's item count matches its
  prose (one per qualification for education), and requires roster or `Example`-shaped
  headings and employers. It has a must-find floor.
- **Store contract:** the three `write_document` rows that read back through
  `read_baseline` are re-pointed at `read_criteria`, keeping every assertion.
- **Docs claims** (`tests/test_docs_claims.py`): `_FOLDED_IN_CATEGORIES` names `cv/reply.py`
  for `REPLY`, and drops `STRUCTURAL` and `FORMAT`.
- **Gate guards retargeted at the selection:**
  - `test_a_skills_digit_is_licensed_in_neither_pool`
  - `test_the_allowlist_still_matches_the_frozen_prompt`
  - `test_bundle_sources_sentinels_hold_independent_of_the_frozen_literal`
  - `test_the_derived_constraint_reaches_no_number_pool`
  - `test_a_digit_leading_skill_token_stays_refused_whatever_it_names`
  - `test_negatives_block_does_not_widen_the_last_entrys_allowlist`
  - `test_profile_number_from_negatives_is_flagged`
  - `test_at_zero_entries_the_negatives_no_longer_reach_the_profile_pool`
  - `test_a_profile_non_id_bracketed_number_is_flagged`
  - `test_the_vocabulary_cannot_widen_the_hard_gate`

  Plus two new pool exclusions: layout dates and headings reach no figure pool, and
  `Tools:` digits stay out of `_entry_block`.
- **Engine contract,** each using the Retention row's two-attempt sequence and asserting the
  compose count first:
  - retry exactly once;
  - `skipped-gate` iff no attempt was hard-clean;
  - the retained draft is the one rendered AND audited;
  - a first compose that raises bins the lead, while a retry that raises ships the retained
    draft;
  - the voice check does not run while the hard gate is dirty, and `voice_check` off spends
    no extra call.
- **Harness and end-to-end** (`tests/harness/`, `tests/e2e/`, `tests/functional/`):
  - reply-shaped passing and failing fixtures, the failing one failing ONE named §6.1 check
    that each row asserts;
  - `_seed_vault` and `make_composable` seed a CV Layout and `Tools:`;
  - the recording renderer drops `precheck` and records documents;
  - `tests/harness/registry.py::isolate_plugin_registry` iterates `core.app._SEAMS` instead of
    its hand-listed four;
  - the unbacked-skill e2e asserts the pick is absent from the document and present in
    `skills_dropped`;
  - the unannotated-vault e2e becomes: an empty pool requests no skills and renders no
    SKILLS section;
  - the usage e2e stays the `cv-compose` witness.
- **`scripts/smoke_installed.py::check_real_render`:** a `CvDocument` literal whose offline
  pin asserts that the sections the size floor relies on are populated.
- **Onboarding** (`tests/test_onboard_plan.py`): the retargeted legs of §9.3.

### 12.3 Ledger and witnesses

**The ledger is derived, never hand-listed.** It covers every test node id collected at the
fork point but not on the branch, plus every test function whose body changed in the branch's
diff over `tests/`. One line per test id: the property it protected, and either
"obsolete because …" or "ported as <node id>". A check confirms that every "ported as" id is
collected on the branch.

**Witnessing.** Every gate row is witnessed by DELETING the code it pins, or by SWAPPING the
old behaviour back in at one site of the post-change code — never by adding beside, and never
by running the new rows against pre-change code, where they would fail on an import or a
signature for the wrong reason. Each witness runs after a checked-hash `compileall`, and the
witnesses are re-run after the port, not before it. In particular:
- **#368:** re-subtract negative tokens in `mention_vocab`; the #368 row must redden.
- **Decoys:** swap the whole-term match for `d.casefold() in t.casefold()`; the longer-token
  row must redden.
- **Brackets:** delete the `REPLY:` bracket refusal, and separately swap today's
  `re.sub(r"\[[^\]]+\]", "", …)` into the digit scan; each reddens its own row.
- **Spans:** swap token-sequence span removal for substring removal (the `Widget30` row);
  strip pool spans from the profile (the Skills Inventory digit row); license removal from all
  entries (the beside-a-licensed-tool row).
- **Licensing:** swap the tool-licensing match for a case-insensitive or substring check; the
  `go-live` and longer-token rows must redden.
- **Digits:** normalise the reply side only; the native-digit entry row must redden.
- **Slots:** swap `assemble`'s slot-id lookup for a zip; both slot-binding rows must redden.
- **Retention:** delete the rebind, and separately build `best` without the drop report; the
  provenance part-4 and Retention rows must redden.
- **Skills:** render the reply's skill string instead of the pool's; the case-only row must
  redden.
- **Text:** swap `to_text`'s positional meta join for one that drops empty fields; the
  empty-field rows must redden.
- **Fences:** remove the strip of fenced content; the separating row must redden.

**The `script` golden** is captured, in a commit before the change lands, from today's
`cv/render.py::strip_citations` over a canonical fixture CV, and committed as a literal, so
it never pins `to_text` against itself.

### 12.4 Real composes, before merge

With the owner's permission, run dry runs for a handful of real leads on the owner's
configured backend and host, after migrating that vault (§11). Measure per lead:
- whether attempt 1's JSON parsed;
- compose calls;
- bullets total;
- hard findings;
- skills dropped and bullets trimmed;
- style findings;
- the audit's unsupported count.

**The contingency, decided now.** If any reply fails to parse:
- `complete(prompt, *, json_schema=None)` becomes a keyword-only per-call hint;
- the wrappers `core/usage.py::MeteredBackend` and `core/backends.py::RetryingBackend`
  forward it;
- the per-token providers accept and ignore it;
- `claude-max` passes it as the CLI's `--json-schema`, after confirming the flag exists on the
  configured host;
- rows show that every provider accepts it, that each wrapper forwards it, and that only
  `claude-max` maps it.

It is built only if the measurement requires it.

## 13. Accepted risks

- **Profile claims without figures are checked only by the advisory audit.** Deliberate (§2).
- **Two roles at one employer** (a promotion or a re-hire): that employer's entries are
  eligible under both, so work from the earlier stint can sit under the later title.
- **An `any_role` entry cited under one heading** attributes career-wide work to that heading.
- **`MISATTRIBUTED TOOL` polices exactly what is listed, case-sensitively.** It under-fires
  when a bullet writes a tool in another case, which is the direction a hard check must err
  in. A lowercase ordinary word that the user lists in `Tools:` is policed like any other. An
  entry whose body spells a capitalised tool in another case does not license it, which costs
  one retry.
- **Digit-led tool names (`ISO 9001`) cannot be listed in `Tools:`.** Refusing them closes the
  laundering path in §4.2.
- **A decoy the shared ASCII tokeniser cannot represent is refused at load.** A Unicode-aware
  tokeniser is out of scope.
- **Spelled-out numbers ("five hundred") are not figure-checked**, as today.
- **An ASCII letter written against a digit is not refused.** `8O%` (a capital O), `2l0` or
  `3l%` shows the page a figure `figures()` reads only the digits of, and passes, because
  `5G`, `O2` and `10l` are real text. A NON-Latin letter against a digit (a Cyrillic or Greek
  capital O) is refused as a look-alike; the advisory audit is the only check on the ASCII
  case.
- **No fallback for an install without a CV Layout.** Composing refuses until one exists.
  Keeping the baseline as a second structure source would preserve the text-copying path
  this design removes.
- **A skill note's CV name renders verbatim** (its `Label:`, else its title). Notes verified
  before 4.0 under framing-only semantics now reach CVs; the CHANGELOG asks users to review
  them, and to label notes `skills add` slugged.
- **`script` users stay exposed until this ships** to the unbulleted-paragraph hole in §1.2,
  because D9 ships everything in one PR.
- **JSON reliability** varies by backend; §12.4 measures it before merge.

## 14. Docs and prose to update

**Files:**
- `README.md`: the CV section, the sample, and the commands table's skills wording.
- `docs/USAGE.md`: `cv run` output, the `experience add` and `skills add` flags, and the
  "Diagnostic artefacts" table (the reply rename, `cv.rendered.md`'s new meaning, the three
  `run.json` keys).
- `docs/CONFIGURATION.md`: the CV Layout with its comma rule and matching rule, `Tools:` and
  the digit-led refusal, `Label:`, decoys, negatives, budgets, and the retired keys.
- `docs/ARCHITECTURE.md`: the cv sub-app, the Store and Renderer seams, the citability prose,
  and the fold's third consumer kind.
- `docs/TROUBLESHOOTING.md`: the retired `FORMAT:`/`UNSOURCED SKILL` messages, the skipped-gate
  walkthrough's artefact names, and the new setup and warning rows.
- `docs/AI-SETUP.md`: a CV Layout step in place of the baseline steps — interview the user,
  then create the note ONLY if it is absent; if it exists, show the user a diff and edit only
  what they approve, never rewrite it. Any sluice code that ever writes a layout skeleton uses
  `write_document(..., only_if_absent=True)`.
- `docs/GUARANTEES.md`: what the gate now guarantees, including employer scoping (for entries
  that name a company), and that skill picks are dropped rather than refused.
- `sluice.yaml.example`.
- `sluice/templates/cv_plain.html.j2`'s comments, which cite `parse.py` and "a heading the
  composer already emits".
- `.rulesync/rules/CLAUDE.md`. These CV-gate sections describe machinery this design removes,
  so they are rewritten, not amended:
  - parser strictness and the implication sweep;
  - marker equality and floor;
  - the envelope unwrap and the #99 guards;
  - the SKILLS rows;
  - the Conventions seam bullet (`precheck`, dry-run renderer resolution, preflight's
    baseline fact);
  - the citability section's skills flags;
  - the fold paragraph, which gains employer matching as a consumer kind.
- `.rulesync/skills/{review-pr,review-plan,address-comments,path-to-green}` and
  `.rulesync/subagents/sluice-invariant-reviewer.md`: their routing still names
  `cv/validate.py` and `cv/engine.py`, which stay true (§9.4), and gains `cv/reply.py`,
  `cv/document.py`, `core/layout.py` and `core/tokens.py`.

**Prose inside code,** found by grepping the CLAIM words across `sluice/`, `scripts/`,
`tests/` and `.rulesync/`: `precheck`, `read_baseline`, `baseline_rel`, `baseline CV`,
`ground truth`, `parse_cv`, `parse.py`, `cv/parse`, `cv.parse`, `section_spans`,
`cv_employers`, `cv.employers`, `classify_negatives_vs_skills`, `FORMAT:`, `Skills:`,
`framing, licensed by nothing`, `composer already emits`. Known sites:
- `core/protocols.py`'s Store docstring, `read_candidate_profile`'s, and `EvidenceKind`'s
  (whose attribute count is deleted, not bumped);
- the preflight contract;
- the `write_document` docstrings in `core/protocols.py` and `core/vault.py`, which call the
  baseline "the fabrication gate's ground truth";
- `stores/vault.py::_make`;
- `core/doctor.py::classify_store`;
- `core/app.py`'s doctor docstring and comment;
- `ingest/base.py::Source`;
- `triage/engine.py`'s dry-run justification;
- `cv/voice.py` and `core/safeout.py`'s docstrings;
- `cv/slop.py::check_hard`'s docstring, which argues whole-document scope;
- `cli.py`'s `FORMAT` comment;
- `mcpserver.py`'s doctor description and propose detail;
- the comments in `tests/test_sluice_neutral_defaults.py` and
  `tests/test_fixture_name_neutrality.py` that cite deleted mechanisms.

## 15. Out of scope

- A command that populates the vault from a CV (the role D2 gives a baseline CV).
- #288 (locale and spelling).
- Reconciling the CV Layout's dates against a separate employment record.
- A Unicode-aware shared tokeniser.
