# Candidate Profile — one vault-sourced identity for CV composition and apply form-filling

- **Date**: 2026-08-19
- **Status**: DESIGNED, revised after four plan-review rounds. Not yet implemented.
- **Issues**: **#133** (apply has no candidate profile — postal address, right-to-work, EO monitoring,
  "how did you hear" — so every application re-asks the same form fields) and **#107** (`cv.contact`
  blank makes CV composition fail deterministically, not just "degraded"). Tackled together on the
  user's explicit call: both are personal-data gaps that intersect at one seam — `cv.contact` (today,
  free text in `sluice.yaml`) and #133's proposed candidate-side contact data are the same thing wearing
  two names.
- **Supersedes**: `CvConfig.name` and `CvConfig.contact` (and the `cv.name`/`cv.contact` YAML keys) —
  removed, migrated to the vault. This is a **breaking CONFIG change**; per this repo's release
  convention a breaking config change outranks a breaking API change in the changelog and needs
  explicit call-out when the release PR is edited.
- **Grounded in a real artefact.** The user maintains a `Job Applications/Candidate Profile.md` in
  their own vault, in active use before this design existed. The field set and the
  frontmatter/body split below follow its structure. Field *names* are the only thing carried across;
  values, defaults, and the note's own prose stay in the user's vault where they belong.

## Goal

Give both issues one answer instead of two:

- #107's narrow fix: stop `cv run` from burning a real backend call to fail deterministically on a
  blank contact. Refuse before spend, the same way an unconfigured `cv.name` already refuses before
  spend today.
- #133's broader fix: give `apply prep` a real source of candidate-side form data — address,
  right-to-work status, employment history with the employer being applied to, "how did you hear
  about us", and equal-opportunities monitoring answers — so an otherwise-automated application stops
  stalling on fields sluice has never modeled.

Both are solved by reading a Candidate Profile vault note through the Store contract, the same way
`cv`/`triage` already read the Judging Profile and baseline CV.

## Why unify rather than fix each narrowly

The alternative — leave `cv.contact` in local config, add a separate `CandidateProfile` for #133's
apply-only fields — was considered and explicitly rejected (user decision, 2026-08-19). Identity/contact
and apply-form data are one document to the person filling the form; splitting them in sluice's model
would put a seam where the user does not experience one.

## Why the vault, not local config (`sluice.yaml`)

Also an explicit decision, not a default: `sluice.local.yaml` already holds some personal data
(`cv.name`, `cv.contact`, `cv.compose_host`) today, so "vault vs. config" was a live question. The
vault wins because this is content a human edits and re-reads — the same argument that already put the
Judging Profile and the baseline CV there — and because it keeps the most sensitive data sluice touches
out of a file that sits in the repo's working directory.

## The note

`Job Applications/Candidate Profile.md`, alongside the existing Judging Profile
(`Job Applications/Judging Profile.md`) and the baseline CV.

**Every field is a flat frontmatter key. The body carries no data at all** — it is human-facing prose
(why the file exists, what is still unanswered, a backlink to the Judging Profile). Two earlier drafts
of this design got the storage shape wrong, both worth recording:

1. **First draft**: `contact` as one free-text multi-line frontmatter field. A plan-review finding
   (`inv-001`, Critical) caught that `core/vault.py`'s frontmatter reader (`_fm_dict`) is a
   deliberately flat, line-based `key: value` scanner, not real YAML — because `_set_fm`'s
   compare-and-set writer has to preserve a note's other content byte-for-byte, which a full YAML
   round-trip would break. A genuinely multi-line frontmatter value collapses or loses lines silently.
2. **Second draft**: moved every field to headed body sections to sidestep the flat-parser limitation.
   Wrong turn: it invented a body-section parsing mechanism nothing else in the vault uses. The actual
   fix is decomposition — `contact` becomes separate `email`/`mobile`/`linkedin` keys, `address`
   becomes `address_line1`/`address_line2`/`town`/`county`/`postcode`/`country`. Once decomposed, every
   field is single-line and the flat parser is sufficient.

An Obsidian Base does **not** fit here: Bases give a tabular view *across many notes of a kind*, and a
Candidate Profile has exactly one record. There is nothing to tabulate.

### Fields

36 keys, grouped here for readability — the note itself is one flat frontmatter block.

**Identity & contact** (feeds `cv`, via `full_name()`/`contact_block()`):
`forenames`, `surname`, `email`, `mobile`, `linkedin`.

**Address** (feeds `apply`, one packet key per field — matches how real ATS forms usually ask for
address anyway, as separate fields rather than one block):
`address_line1`, `address_line2`, `town`, `county`, `postcode`, `country`.

**Right to work & employment history** (feeds `apply`):
`requires_uk_work_permit`, `right_to_work_uk`, `currently_employed_by_them`,
`previously_employed_by_them`, `referred_by_current_employee`.

The last three are per-employer facts in principle — no single value is true for every application.
They are stored with a static default anyway, on the reasoning that a default correct for the
overwhelming majority of applications, corrected by hand on the rare exception, beats no default at
all. This reverses an earlier version of this design, which excluded them on the abstract reasoning
that "no meaningful static default exists." That reasoning was wrong: a static default is defensible
exactly where the harm of an occasionally-wrong guess is negligible and the correction cost is low,
which is not the same failure shape as inferring a *protected characteristic*.

**How you heard about the role** (feeds `apply`): `how_heard_default`, `how_heard_detail_from_lead_source`.

Two fields, not one, because `apply/packet.py` already computes `listing_host` (the job board,
best-effort, from the URL) independently. `how_heard_detail_from_lead_source` (declared as the literal
string `"true"`, case-insensitive; anything else, including blank, means "no") tells the packet
builder to prefer that computed value over the stored default when it resolves to something more
specific than `"other"`; `how_heard_default` is the fallback otherwise. See "`apply/` changes" for the
exact resolution rule.

**Equal-opportunities monitoring** (feeds `apply`, special-category data):
`gender_identity`, `identifies_as_trans`, `ethnicity`, `religion`, `sexual_orientation`,
`preferred_pronouns`, `disability`, `neurodivergent`, `open_about_orientation_at_work`.

**Other** (feeds `apply`): `date_of_birth`, `title`, `marital_status`, `nationality`,
`dual_nationality`, `first_language`, `served_armed_forces`, `caring_responsibility`,
`worked_in_construction`.

**`date_of_birth` is ISO 8601 (`YYYY-MM-DD`).** A two-digit day/month pair is genuinely ambiguous to a
parser without hardcoding a locale assumption sluice has no business hardcoding, and this codebase's
standing convention is to fail loudly — or here, abstain — rather than guess at an ambiguous format. A
note whose existing value uses another format needs one manual edit; `age_from_dob` abstains rather
than guessing until it gets one.

**No stored age band.** A stored band goes silently stale — the same declared value reads as a
different bucket a few years later with nothing prompting an update. A birth date is a fixed fact; age
is *derived* from it at the moment it is needed. Real ATS forms bucket ages differently from each other
anyway (18-24 vs. 18-25, "under 18" present or absent), so there is no single bucket scheme sluice
could store correctly for every form. Which bucket an age falls into on a given form is the
form-filling skill's job, exactly like every other ATS-option-matching decision.
`Vault.read_candidate_profile()` reads only the 36 keys below; any other key in the file — including an
age band a user keeps for their own reference — is ignored, so no migration or clean-up is required.

`core/candidate.py` provides `age_from_dob(dob: str, today: str) -> int | None`, both ISO 8601 strings,
parsed internally via `date.fromisoformat`. **`today` is a `str`, not a `date`, deliberately matching
`Sluice.staleness()`'s existing pattern** (`rev2-001`) — `self._today` is a zero-arg *callable*
returning a string, never a string itself, and `Sluice.staleness`'s docstring (`core/app.py`) already
records the exact trap of binding the unresolved callable into a typed value:
`date.fromisoformat(<function>)` raises `TypeError` past whichever guard was meant to fail safe.
Cite that docstring by SYMBOL, never by line range — this repo has twice been bitten by a line count
in prose going stale silently.

`age_from_dob` returns `None` on a blank or unparseable `dob`, and logs a warning **naming only the
field, never the raw `dob` string** (a log is a plausible place for a sensitive value to leak into a
bug report) on the unparseable case ONLY. **A blank `date_of_birth` abstains SILENTLY**
(`rev5-001`): `""` is the designed default of an optional field, so warning on it is a warning on
every lead of every run for a user who simply declined to declare a DOB, and a warning that fires on
the normal path is how a codebase teaches its users to ignore warnings. This is the
empty-config-abstains posture the rest of sluice already takes — `lead_ttl_days: 0` means OFF and
says nothing. The inventory of which fields a user has left undeclared belongs in `doctor`, once per
invocation, not in a per-lead derivation.

**A non-`str` `today` RAISES `TypeError` before the parse is attempted** (`rev5-002`), naming
`today`. This is the clock trap above, and the precedent this parameter's type was chosen from
resolves it by refusing: `StalenessPolicy` refuses a non-`str` at construction so the mistake cannot
reach a gate silently (`Sluice.staleness`). Catching it instead inverts that precedent — and, because
the sole warning message names `date_of_birth`, an unresolved-callable `today` would point the
operator at the user's vault note while the bug sits in sluice's own caller, on every lead, with the
age silently absent from every packet. A caller bug and user data are different failures and must not
share an abstain path.

**For `dob` it catches `ValueError` and `TypeError` only** (`inv4-006`) — `ValueError` for a malformed
or out-of-range date, `TypeError` for a non-`str` reaching the field. The narrow tuple is not what
surfaces the clock trap (it would swallow that `TypeError` exactly as a bare `except Exception`
would, which is why the guard above exists instead); it is there so an unforeseen exception class
propagates rather than being silently converted into "this user declared no DOB".

**A `dob` later than `today` abstains, and WARNS** (`rev5-003`). The function already declines to
guess at an unparseable date; an impossible one is the same case, and the value flows onto an ATS
form under the user's name. It also makes a transposed `age_from_dob(today, dob)` — two `str`s, so
no type guard can see it — abstain rather than report a large negative number as an age.

The warning is what separates this from `rev5-001`'s silence, and the line is *declared versus
undeclared*, not *abstain versus not*. A blank `date_of_birth` is a user declining to answer, which
is the designed default and must be silent. A future one is a declared value that cannot be used —
the same category as the unparseable value `rev5-001` still warns about, and the same remedy (fix
the note). Silence there would mean a user who typed `2062` in place of `1962` loses `age` from
every packet of every run with nothing said anywhere, which is the quiet-wrong-default failure this
codebase most consistently engineers out. It names the field, never the value, exactly as the
unparseable warning does.

**Deliberately excluded, not merely left blank:**

- **Salary expectation** — #133 names this explicitly, and excludes it: it is per-application, and a
  stored default risks putting a negotiating position onto a form automatically, unreviewed.

### Presence semantics

A field counts as **declared** when `.strip()` on its frontmatter value is truthy. A key entirely
absent, a key present with an empty value (`key: ""` or bare `key:`), and a key present with only
whitespace are all **undeclared** — the same shape `cvcfg.name.strip()` / `cvcfg.contact.strip()` are
already checked with today. Undeclared fields are never inferred, defaulted, or guessed by any code
sluice ships — that constraint is enforced at the one seam where a *decision* about an undeclared field
would otherwise get made: the form-filling step (see "`apply/` changes").

## Store contract

**`CandidateProfile`'s dataclass lives in `core/protocols.py`** (`arch-003`), beside `LeadNote` and
`UpsertResult` — the contract's other typed return shapes. It is the literal declared return type of a
new Store contract method, and `core/protocols.py`'s own docstring rule ("interface only, no logic") is
what already explains why `RenderError` moved *into* that file from `renderers/script.py`.

Every one of its 36 fields is a plain `str` defaulting to `""` — no `bool` fields. `_fm_dict` never
invokes real YAML parsing (it is a regex line-scanner, so `right_to_work_uk: true` and `disability: No`
both arrive as the literal strings `"true"` and `"No"`), and forcing a Python `bool` would (a) buy
nothing, since nothing downstream needs Python-level boolean logic beyond the one
`how_heard_detail_from_lead_source` case, handled as an explicit string-literal check, and (b) risk
exactly the `bool`-subclasses-`int`/PyYAML-coerces-`yes` trap this codebase is already careful about for
config fields that WOULD go through a real YAML loader. There is deliberately no normalization pass
reconciling `true`/`false` against `Yes`/`No` spellings — both are declared strings, and interpreting
either is the downstream ATS-matching step's job.

`core/protocols.py` also declares `CANDIDATE_PROFILE_RELPATH = "Job Applications/Candidate Profile.md"`,
next to the existing `CRITERIA_RELPATH`, for the same reason that constant lives in the contract module
rather than in `core/vault.py` — a non-filesystem store treats it as an opaque document key, not a path.

`core/candidate.py` (new, mirrors `core/criteria.py`) holds the functions with a body — nothing with
logic belongs in the interface-only `protocols.py`:

- `age_from_dob(dob, today)` — above.
- `full_name(profile) -> str` — `" ".join(p for p in (profile.forenames.strip(),
  profile.surname.strip()) if p)`. Feeds the CV header's name line.
- `contact_block(profile) -> str` — **the bare declared value, one per line, in `mobile`, `email`,
  `linkedin` order**, undeclared lines omitted rather than emitted empty.
- `has_any_declared(profile) -> bool` — true when any of the 36 fields is non-blank. Used by `sluice
  init` as its existence probe; see that section for why it is this predicate and not `full_name`.

**`contact_block` emits bare values, not labels** (`rev4-007`). `sluice.yaml.example:194-197` currently
illustrates `cv.contact` as a labelled block (`Phone number: …`, `Email address: …`, `Web: …`), but
those labels are one user's formatting choice living in a value they can edit. Moving them into
`core/candidate.py` would make them a shipped constant with no way to override, which is a formatting
preference in code. Bare values ship no opinion, and a user who wants a label can put it in the field
value itself — the field is free text. **Stated consequence**: an existing user's next composed CV
drops the labels from its contact lines. That is a real, visible change to the artefact and belongs in
the breaking-change note alongside the config removal.

`Store.read_candidate_profile() -> CandidateProfile` joins `read_baseline`/`read_criteria` as a
MUST-support Store contract method — not optional like `preflight`/`precheck`. An optional member would
push a `getattr` None-branch into four callers and hand `cv` a "the store cannot say" case with no safe
answer: composing without a name is the fabrication risk #99 exists to stop, and refusing on a store
that simply did not implement the hook would be a silent feature-off. **MUST-support means the
conformance suite enforces it** (`test-001` + `arch-002`): `tests/conformance/test_store_contract.py`
gets a `read_candidate_profile` entry parametrized over every registered `store_name`, in the same shape
as `test_read_criteria_abstains_when_unset` — **both** of its directions, abstain and round-trip, since
asserting only abstain passes an amnesiac store that never reads the user's profile at all
(`arch4-008`). The round-trip direction needs a seeder: `tests/conformance/seeds.py`'s `_seed_vault`
currently accepts only `experience`/`criteria`/`conflicted_status` and gains a `candidate=` parameter
(`test4-005`).

`Vault.read_candidate_profile()` reads the note's frontmatter once via `_fm_dict`, builds a
`CandidateProfile` from the 36 known keys, and ignores anything else present. A missing note returns an
all-blank `CandidateProfile` — the same "unset means empty string, caller falls back" shape
`read_criteria` already has, not a raise.

**`_fm_dict` is the only parser involved, and that is a choice, not an inheritance** (`rev4-003`). An
earlier draft of this document claimed `read_experience_entries` already uses `_fm_dict`; it does not —
it uses `_parse_fm_spaced` (`core/vault.py:1236`), a different helper tolerant of spaced and capitalised
keys. `_fm_dict`'s key regex is `[A-Za-z0-9_]+`, so it silently drops any key it cannot match rather
than erroring. The 36 field names above are all lowercase-with-underscores and match it, and
`_fm_dict` is the right reader because this note is machine-written and machine-read; but the choice
must be made deliberately, and a field name added later that does not match that character class will
be silently invisible rather than loud. The dataclass field names ARE the frontmatter keys, so this is
enforceable: the round-trip test below covers it.

## The frontmatter round-trip is verified, not assumed

`_fm_dict` ends in `.strip().strip('"').strip("'")` and unescapes nothing. Measured: a value of `Ex'`
reads back as `Ex`, and a value written through a quoting emitter as `Example "Nick" Name` reads back
with the backslashes still in it. Identity values now round-trip through this reader on their way to
`full_name()`, which feeds **both** `compose()` and the #99/#100 STRUCTURAL guard — so a lossy
round-trip corrupts the value and then compares the corrupted value against itself, and the PDF
headline ships wrong with every guard green (`inv4-002`, `rev4-004`).

**There is no escaping scheme in this design.** Instead, the write path uses the real reader as its
oracle: render the frontmatter block, run `_fm_dict` over the rendered text, and refuse any key whose
value does not come back byte-identical to what was typed. In `collect_candidate` (interactive) that
refusal re-prompts, naming the offending character; on a non-interactive path it becomes a `failed`
line, never a silent write. This is the "assert through the engine that RUNS it" rule from
`CLAUDE.md` applied to a write instead of a regex — a hand-rolled escaping scheme is exactly the kind
of second implementation of a parser's rules that drifts from it.

Mechanically this means `onboard/plan.py` (which owns rendering) needs `_fm_dict`. It is module-private
today; promoting it to a documented internal helper on `core/vault.py`, or adding a thin
`parse_frontmatter(text) -> dict` wrapper beside it, is the smaller of the two changes and is preferred
— the alternative is a second frontmatter parser in `onboard/`, which is the drift this whole section
exists to prevent.

## `cv/` changes

- `CvConfig` loses `name` and `contact` entirely.
- `load_cv_config` gains a migration guard in the shape of the existing `baseline_rel` guard: if
  `cv.name` or `cv.contact` is present in the YAML `cv:` block, raise, naming the new vault note as the
  destination. Not a silent `hasattr`-drop — the fields no longer exist on `CvConfig` at all, so
  without this guard a user's old config would silently stop supplying a name/contact with nothing said.
  **The guard keys on `"name" in data`, not on the value being truthy** (`arch4-009`): `cv/config.py`
  already carries both spellings deliberately and documents the difference, and a `cv.name: ""` left
  behind by a half-finished migration must be as loud as a populated one.
- `cv/engine.py`: fetch `profile = vault.read_candidate_profile()` at the point the current `cv.name`
  placeholder check runs — before any dossier fetch or LLM spend — and replace that check with:

  ```python
  full = full_name(profile)
  contact = contact_block(profile)
  if not full.strip() or not contact.strip():
      return CvResult(note.ref, "skipped-config")
  ```

  Strictly simpler than today's check (no more comparing against the `CvConfig.name` class default
  sentinel `"Your Name"` — a blank derived name just *is* blank), and it is the direct fix for #107: a
  blank contact now aborts before any backend call instead of composing, gate-checking, and reporting
  `skipped-gate` on every attempt.
- The STRUCTURAL guard (`cv/engine.py`'s #99/#100 header-shape checks) and the `compose()` call both
  read `full_name(profile)`/`contact_block(profile)` where they previously read
  `cvcfg.name`/`cvcfg.contact`. No other change to that guard's logic.
- **The profile is fetched in `run_one`, so a batch `cv run` re-reads it once per lead** (`arch4-010`).
  This is consistent with how `run_one` already re-reads other per-run state and is not a correctness
  problem — but it is a different lifetime from `apply`'s prep-scoped fetch below, and the difference
  is deliberate rather than an oversight: `cv run` is already the expensive path (a backend call per
  lead), and hoisting the read would mean a note edited mid-batch is ignored for the rest of the run.

## `apply/` changes

`build_packet(note, cfg, *, profile, today, cv_staged)` gains `profile: CandidateProfile` and
`today: str` (an ISO 8601 date string — see "Fields" for why `str`, not `date`). **Both are
keyword-only**, joining the `*` the function already uses (`arch4-007`): two new required *positional*
parameters in front of an existing keyword-only one silently transpose at any call site that passes
positionally, and there are three in `sluice/` plus five in `tests/test_apply_packet.py`.

`core/app.py`'s `prep()` fetches `profile` once per `prep()` call (`self.store().read_candidate_profile()`)
and reuses the `today` it **already resolves** — `prep()` calls `self.staleness(include_stale=...)` at
`core/app.py:1573`, which does `clock = self._today or _today; today = clock()` internally. Resolving
the clock a second time beside it would call the callable twice per `prep()` and could straddle
midnight, giving one `prep()` two different dates (`arch4-004`). Thread the single resolved value.
Both `profile` and `today` pass down through `engine.prep_one` / `engine.preview_all` / the dry-run
branch — not re-read or recomputed per lead inside `preview_all`'s loop over the whole shortlist.

New packet keys, **each included only when declared** (omitted from the dict entirely when undeclared —
not present as an empty string, so the form-filling skill can distinguish "sluice has nothing to offer
for this" from "sluice explicitly knows this is blank"). One key per profile field, with two computed
exceptions:

`address_line1`, `address_line2`, `town`, `county`, `postcode`, `country`, `requires_uk_work_permit`,
`right_to_work_uk`, `currently_employed_by_them`, `previously_employed_by_them`,
`referred_by_current_employee`, `gender_identity`, `identifies_as_trans`, `ethnicity`, `religion`,
`sexual_orientation`, `preferred_pronouns`, `disability`, `neurodivergent`,
`open_about_orientation_at_work`, `title`, `marital_status`, `nationality`, `dual_nationality`,
`first_language`, `served_armed_forces`, `caring_responsibility`, `worked_in_construction` — declared
value passed through verbatim.

`age` — computed via `age_from_dob(profile.date_of_birth, today)`, included only when it resolves to a
number.

`how_heard` — resolved, not passed through raw, and **included only when it resolves non-`None`**
(the same shape as `age`; `inv2-002` + `rev2-002`):

```python
def resolve_how_heard(profile, listing_host):
    prefer_lead = profile.how_heard_detail_from_lead_source.strip().lower() == "true"
    if prefer_lead and listing_host not in ("", "other"):
        return listing_host
    return profile.how_heard_default.strip() or None
```

`forenames`/`surname`/`email`/`mobile`/`linkedin` are **not** added to the packet. `render_text`'s
existing RULES block already says "Use first names only. No real full names in third-party forms" — the
CV upload is the name/contact channel.

### `render_text` renders them, and that is not optional

`render_text` is the DEFAULT output of `apply prep` — `sluice/cli.py:783/799/804` call it unless
`--json` is passed — and it is a hand-enumerated seven-key list today. Adding packet keys without
adding them here leaves #133 inert on the path a human actually reads (`inv4-003`). The new fields are
rendered in two blocks:

- **`DETAILS`** — address, right to work, employment history with this employer, how heard, age.
  Printed plainly, each line omitted when its key is absent from the packet.
- **`MONITORING (special-category; optional on most forms)`** — the nine equal-opportunities fields,
  under that heading, so what they are is visible at the point they appear.

The monitoring block prints by default rather than being withheld behind a flag (`inv4-007`). The
user asked sluice to fill these forms; withholding the answers by default defeats the feature and
leaves them retyping the exact fields #133 is about. The data is on their own machine, printed on their
own explicit command, and the packet already carries salary and location. The proportionate mitigation
is the visible heading plus the note in `docs/USAGE.md` that `apply prep --json` exists for anyone
piping the packet somewhere it will be retained — not a default that quietly drops the feature.

`render_text` also gains a rule for the downstream form-filling step: never guess or infer a value for
a field that is not present in the packet, treat any ATS question with no matching packet field as one
to leave for the human, and treat every MONITORING answer as optional — a form that allows "prefer not
to say" gets that unless the packet says otherwise. This is where "never infer, never default" becomes
enforceable: sluice's own code does no ATS-option matching (that is the `job-application-workflow`
skill's job, browser-driven and human-reviewed per the existing "Never auto-submit" rule), so the
instruction has to live in the text handed to whatever does that matching.

## `doctor.py` changes

Remove the existing `cv-identity` / `cv.name` and `cv-identity` / `cv.contact` checks (they read
`cvcfg`, which no longer has these fields).

Add one `store` / `Candidate Profile` check. **It is fed by `Vault.preflight()`, and only that**
(`arch4-005`, `inv4-004`, `rev4-005` — three reviewers found this specified two contradictory ways).
`core/doctor.py`'s `classify_store(facts: dict)` is pure and reads primitives out of the fact dict; it
never holds a store handle. So `Vault.preflight()` gains two boolean facts — whether the derived full
name is blank, and whether the derived contact block is blank — computed inside `preflight` where the
store already has the note. `Sluice.doctor` does not call `read_candidate_profile()` itself, and the
Testing section below tests the wiring at that seam rather than at an invented one.

Severity is **DEAD** with `blocks=("cv",)` when either fact says blank — cv cannot compose without
them, the same severity class as a missing baseline.

**The message names only what is broken: the name and contact needed to compose a CV.** It does *not*
mention the rest of the profile (`neu4-005`). "Fill in the rest for better apply automation" reads as a
prompt to supply ethnicity, religion, sexual orientation and disability to a tool that is telling you
something is wrong, and `doctor`'s job is to report what blocks a command, not to encourage optional
disclosure. A user who wants to know what `apply` can use has `docs/CONFIGURATION.md` and the packet
itself.

**`Sluice.doctor` calls `load_cv_config()` unguarded at `core/app.py:1748`** (`inv4-005`), before the
deliberately-guarded constructions further down. Once `load_cv_config` raises on a legacy `cv.name`,
`doctor` — the command a user runs precisely because something is wrong — tracebacks mid-migration. That
call needs the same try/except-and-report treatment its neighbours already have, reporting the
migration as a DEAD finding rather than dying on it. **This is the single most important item in this
document to get right**, because it is the failure mode of the migration itself.

**This hardens `cv.contact`'s severity from today's DEGRADED to DEAD** (`rev-001`). Today's DEGRADED
message reasons that a user's own `cv.template` "may supply contact details another way," implying a
blank contact is a legitimate configuration for someone with a fully custom template. That path is not
being removed: the STRUCTURAL guard only ever inspects the LLM's *composed text*, never the rendered
PDF, so a user with a custom template can still declare a non-blank placeholder and have their template
ignore `document.contact` entirely. What is being corrected is that DEGRADED described a success path —
composition completing with zero contact lines — that #107's own issue text reports live testing found
effectively never happens. DEAD matches reality; the escape hatch is unaffected.

## `sluice init`

**This section is rewritten against `sluice/cli.py:1015-1174` rather than described from memory.**
Three prior rounds each produced a differently-broken version of this gate, because each reasoned about
onboarding from prose. Four reviewers independently found round 3's version unimplementable
(`arch4-001`, `inv4-001`, `rev4-002`) and deadlock-prone (`arch4-002`, `rev4-001`).

### Why the five identity questions are NOT catalogue questions

`onboard/questions.py`'s `catalogue()` loses `cv_name` and `cv_contact` and gains nothing. Two
independent facts about `cmd_init` make a catalogue question the wrong home:

1. `cli.py:1039-1041` filters the catalogue down to `vault_dir` alone whenever the config already
   exists. A migrating user — config present from a previous run, Candidate Profile note absent — would
   be asked nothing and get a bare note written. That is verbatim the bug `cli.py:1095-1100` records as
   already fixed once for the Judging Profile.
2. Any gate reading the store cannot exist in time. `collect()` runs at `cli.py:1050`; `store` is not
   constructed until `cli.py:1073`, because it needs `vault_dir`, which is itself an answer from that
   same `collect()` call.

Instead, the five identity questions live in **`collect_candidate(asker)`, a new sibling of
`collect_profile` in `onboard/ask.py`**, and are asked in the same place and under the same conditions:

```python
candidate_exists = has_any_declared(store.read_candidate_profile())   # beside profile_exists, cli.py:1074

if interactive:
    if not profile_exists:   profile_answers   = collect_profile(asker)
    if not candidate_exists: candidate_answers = collect_candidate(asker)

plan = build_plan(answers, profile_answers=profile_answers,
                  candidate_answers=candidate_answers, sources=sources)
```

This also corrects a false precedent an earlier draft cited: `build_plan` renders the Judging Profile
from `profile_answers`, **a separate dict from a separate interview**, not "a subset of the same
`answers` dict". That separateness is exactly what makes its gate independent, and it is the property
being copied.

The five questions map one-for-one onto frontmatter keys: `cv_forenames` → `forenames`, `cv_surname` →
`surname`, `cv_email` → `email`, `cv_mobile` → `mobile`, `cv_linkedin` → `linkedin`. These are exactly
the fields `cv`'s composition path reads. The other 31 ship with no visible value (key present, empty),
since no onboarding question exists for them — matching "Presence semantics"'s undeclared rule and
regressing nothing, because nothing asks them today either.

### The write is conditional, and that is a deliberate difference from the Judging Profile

```python
if any(v.strip() for v in candidate_answers.values()):
    handle = store.write_document(CANDIDATE_PROFILE_RELPATH, plan.candidate_text, only_if_absent=True)
    # ...identical .init-scaffold.md rescue and `failed` reporting as CRITERIA_RELPATH's
```

The Judging Profile writes unconditionally, and it can: `_render_profile({})` always emits headings plus
`DEFAULT_CRITERIA`'s own prose, so `bool(read_criteria())` is True on the next run and the gate closes.
An all-blank Candidate Profile frontmatter block has no such content. Writing one would leave
`has_any_declared` False **forever**: the note exists so `write_document(only_if_absent=True)` refuses,
but the gate never closes, so every later interactive run re-asks, parks the answers in
`.init-scaffold.md`, and the run after that reports `failed` — with the real note still empty. That is
the deadlock `arch4-002` and `rev4-001` found, and `init --no-input --vault ./vault` (the first
documented onboarding command) walks straight into it.

Gating the write on "at least one declared answer" makes the write-gate and the existence-probe **the
same predicate on both sides of the round trip**, which is what makes the deadlock impossible rather
than merely unlikely. `has_any_declared` is that predicate, and it is why the probe is not
`bool(full_name(...))`: a user who answers only `email` produces a note that exists and is useful, but
whose `full_name` is blank — a `full_name` probe would re-ask forever.

Consequences, stated rather than discovered later:

- `init --no-input` writes **no** Candidate Profile note. `doctor` then reports the new DEAD check,
  which is the honest answer: nothing has supplied a name or contact yet.
- An interactive run where the user skips all five questions also writes no note, and the next run asks
  again — correct, since they did not answer.
- The `.init-scaffold.md` rescue still exists for the genuine race (a note appearing between the probe
  at `cli.py:1074` and the write at `cli.py:1137`) and for a note that exists but is blank.

Rendering `plan.candidate_text` reuses `onboard/plan.py`'s existing two-artefact shape, emitting
frontmatter rather than headed prose, and every value goes through the round-trip verification in "The
frontmatter round-trip is verified, not assumed".

## Docs and the example config

Removing `CvConfig.name`/`.contact` leaves a set of files asserting they still exist. **This document
deliberately does not enumerate that set** (`arch4-003`). Three prior rounds each hand-listed it and
each list was incomplete — the fourth round still found a user-facing string at `sluice/cli.py:675-686`
telling operators to set `cv.name` in `sluice.yaml`, which is a *code* change that three doc-focused
sweeps missed precisely because it was filed under docs. The list is the liability; the command is the
artefact:

```bash
git grep -n -E 'cv\.name|cv\.contact|cvcfg\.name|cvcfg\.contact|Your Name'
```

Every hit is in scope for this PR, not a follow-up — a stale architecture doc is worse than a missing
one, because it is believed. The transformation per category:

- **Shipped code printing guidance to a user** (e.g. `sluice/cli.py`'s preflight hint): point at the
  vault note, not the config key. These are code changes and need a test, not just a read-through.
- **`sluice.yaml.example`**: replace the `name:`/`contact:` catalogue lines with a comment pointing at
  `Job Applications/Candidate Profile.md`.
- **`.rulesync/rules/CLAUDE.md`**: update the #99/#100 section's `cvcfg.name`/`cvcfg.contact`
  references to `full_name(profile)`/`contact_block(profile)`, and drop the "Your Name" sentinel
  description — the new check is a direct blank check, not a sentinel comparison. Regenerate with
  `npm run rulesync` afterwards.
- **`docs/ARCHITECTURE.md`, `docs/USAGE.md`**: the `doctor` description, and `USAGE`'s `apply prep`
  entry (the new `DETAILS`/`MONITORING` blocks and the `--json` note).
- **`docs/CONFIGURATION.md`**: remove the `cv.name`/`cv.contact` rows, document the vault note.
- **`README.md`, `docs/TROUBLESHOOTING.md`**: the fabrication-gate passage and the placeholder-name fix
  instruction both currently name `cv.name`.

## Explicitly out of scope (a real follow-up)

- **Onboarding questions for the 31 fields with no question today** — address (6), right-to-work and
  employment history (5), how-heard (2), date of birth (1), the nine equal-opportunities fields, and
  the remaining eight "Other" fields. Wording these well — especially the equal-opportunities ones: no
  implied "normal" answer, easy to leave unanswered, no question order that makes skipping feel
  conspicuous — is genuinely separate design work from wiring the plumbing, and nothing regresses by
  deferring it.
- **Changelog wording for the breaking config change** — handled when the release PR is edited, per
  this repo's existing release process.

## Testing

### The change surface is found by grep, not by this list

Three rounds hand-listed the affected tests and all three lists were incomplete; the fourth round found
four more High-severity omissions. Run this first and treat every hit as in scope:

```bash
git grep -n -E 'cv\.name|cv\.contact|cvcfg\.name|cvcfg\.contact|CvConfig\(|Your Name|classify_cv_identity' -- tests/
```

Known non-obvious hits, recorded because each is a *coverage* question and not merely a broken import:

- **`tests/harness/config.py`** writes `"name": cv_name` into the emitted `cv:` block on **every**
  `build_harness` call, so the new migration guard reddens the whole e2e + functional tier at once
  (`test4-001`). The harness stops emitting the key; nothing replaces it, because the identity now
  comes from the store the harness already builds.
- **`tests/test_sluice_neutral_defaults.py::test_config_overlay_restores_neutralized_defaults`** writes
  `cv:\n  name: "Someone"` and asserts the round-trip (`neu4-003`). It exists to prove neutralized
  defaults cost no override capability, so it is **retargeted, not deleted**: the override it proves
  becomes a still-live `cv:` key, and a new sibling proves the same property for the vault note — a
  declared `forenames`/`surname` round-trips out of `read_candidate_profile()`.
- **`tests/test_onboard_emit.py::test_a_control_character_survives_the_whole_config_render`**
  (`test4-002`) rides on `cv_contact` and is the only end-to-end proof that an arbitrary paste reaching
  `init` survives the emitter. It is **retargeted onto the frontmatter emitter**, where it becomes the
  regression test for "The frontmatter round-trip is verified, not assumed" — the hostile-input case is
  now more load-bearing than before, not less.
- **`tests/functional/test_cv.py::test_cv_run_shipped_default_name_returns_1`** (`test4-004`) is the
  refuse-before-spend property at the CLI layer, and its own docstring states the split from the
  engine-level test. An engine-level substitute cannot establish `rc == 1`. Both layers keep a test.
- **`tests/test_doctor.py`**'s autouse `_harmless_components` fixture does
  `dataclasses.replace(CvConfig(), name=..., contact=...)` at `:63` — a `TypeError` erroring all 63
  tests in the file once those fields are gone (`test4-007`).
- **`tests/test_mcpserver.py`** derives `_STORE_WRITE_METHODS` as `vars(Store)` minus a **hand-listed**
  `_STORE_READ_METHODS`, so adding `read_candidate_profile` to the Protocol silently reclassifies a read
  as a write in the MCP isolation sweep (`test4-006`). Add it to the read set.
- Residual call sites (`test4-009`): `tests/test_cv_config.py:10`, `tests/test_app_injection.py:317`,
  `tests/test_cv_engine.py:118-133`'s `_cfg()`.

### Retire-vs-substitute

`test_the_shipped_default_name_is_refused_before_any_spend` (`tests/test_cv_engine.py:812-830`) is
**retired, not substituted** (`test-002`): it exists to exercise the non-blank `"Your Name"` sentinel,
an expression `CvConfig` no longer has. The blank-`full_name` `skipped-config` test replaces its
coverage. `tests/test_doctor.py`'s two `classify_cv_identity` unit tests and the report-level
assertions are retired the same way (`test2-001`), replaced with equivalents for the new
`store`/`Candidate Profile` check — including a direct successor to
`test_sluice_doctor_wires_the_loaded_cv_config_into_cv_identity`, whose own docstring cites a real prior
bug where hardcoding the classifier's inputs left the whole suite green. **That successor tests the
`Vault.preflight()` seam**, matching the single wiring decided above.

### New coverage

- `core/candidate.py`:
  - `age_from_dob` — exact-year boundary cases (day before/after a birthday), blank input, a non-ISO
    format explicitly rejected as unparseable rather than guessed at, and a **non-`str` `dob`**
    reaching the `TypeError` arm. Each returns `None` and does not raise, and the unparseable cases
    log a warning containing no raw `dob` substring (`neu-003`).
  - `age_from_dob`, the three cases added at `rev5-*`: a blank `dob` returns `None` and logs
    **nothing** (assert `caplog.records == []`, the two-directional pair to the existing
    `assert caplog.records` on the malformed case — otherwise nothing distinguishes silent from
    warning); a non-`str` `today` **raises** `TypeError` whose message names `today` (assert the
    discriminating message, not the type — the parse below it raises the same type); and a `dob`
    later than `today` returns `None` rather than a negative int **and logs a warning naming the
    field** (assert both — the return alone does not distinguish it from `rev5-001`'s silent
    blank, which is the whole point of the distinction).
  - `full_name` — both declared, only forenames, only surname, neither.
  - `contact_block` — every combination of the three declared/undeclared; order always
    mobile/email/linkedin; undeclared lines never emitted blank; **values bare, no labels**.
  - `has_any_declared` — all blank → `False`; exactly one field declared, including a field that is
    *not* part of `full_name` (e.g. `email` alone) → `True`. This is the predicate the init deadlock
    turns on, so the single-non-identity-field case is the one that matters.
- `Vault.read_candidate_profile()`: missing note → all-blank profile; a note with only some keys
  answered → exactly those declared; unknown frontmatter keys ignored; **a key whose name does not
  match `_fm_dict`'s `[A-Za-z0-9_]+` class is dropped** — pinned so the parser choice above is a
  tested fact rather than a comment.
- Conformance: `read_candidate_profile` parametrized over every registered `store_name`, **both
  directions**, with the new `candidate=` seeder.
- `load_cv_config`: `cv.name` present → raises naming the vault note; `cv.name: ""` present → also
  raises (the `in data` spelling).
- `cv/engine.py`: blank `full_name`/`contact_block` → `skipped-config` **with the backend fake
  asserted never invoked**, not merely a matching result — the whole point is no spend. Both declared →
  STRUCTURAL guard and `compose()` read the derived values.
- `apply/packet.py`: each new field present iff declared; `age` present iff `date_of_birth` parses;
  `how_heard` across three axes — `how_heard_detail_from_lead_source` × whether `listing_host` is
  specific × `how_heard_default` blank/non-blank — asserting `how_heard` is *omitted* in the `None`
  case, not written as a null (`inv2-002` + `rev2-002`).
  `forenames`/`surname`/`email`/`mobile`/`linkedin` never appear regardless of profile state.
- **`render_text` (`inv4-003`)**: a declared field appears in the rendered text, an undeclared one
  produces no line at all (not an empty one), and the nine monitoring fields appear under the
  `MONITORING` heading. Asserting only on the packet dict would pass while the default output path
  rendered none of it.
- `core/app.py` / `apply/engine.py`: a call-counting fake store asserts `read_candidate_profile()` is
  invoked **exactly once per `prep()` call** across all three call paths (single-lead, dry-run
  single-lead, all-shortlist), and that the clock callable is invoked once, not twice (`arch4-004`).
  Contents-only assertions look identical under an accidental per-lead re-fetch (`rev-003` + `test-003`).
- `doctor.py`: blank name or contact → DEAD blocking `cv`; fully populated → OK; **a legacy `cv.name`
  in config → a DEAD migration finding, not a traceback** (`inv4-005`).
- `sluice/cli.py`'s `cmd_init` — extending `tests/functional/test_init.py`, which already has 23 tests
  driving the real `cmd_init` through `tests/harness/initdriver.py` (an earlier draft called this "the
  previously untested module" and cited the wrong model file; both were wrong, `test4-003`):
  - populated note + re-run → note byte-for-byte unchanged, five questions never asked (call-counting
    asker);
  - `--no-input` → **no** Candidate Profile note written, and a second `--no-input` run still writes
    none — the deadlock regression test;
  - interactive run with all five skipped → no note, and the next run still asks;
  - write refused after answers exist → `.init-scaffold.md` written and reported; spare also occupied →
    `failed`, not silent loss;
  - a value that does not survive the `_fm_dict` round-trip → re-prompted interactively, `failed`
    non-interactively, never written silently.
- `onboard/plan.py`: five answered questions → a rendered config with **no** `cv.name`/`cv.contact`
  keys at all, and a Candidate Profile note carrying those five under their frontmatter keys.
- `tests/test_onboard_questions.py::test_every_value_bearing_question_states_its_consequence`: the five
  new questions are **not** added to the exempt set (`test2-002`). Their `.consequence` text must be
  **accurate**, which the round-3 draft's was not (`neu4-006`): `full_name` joins whichever of
  forenames/surname is non-blank and `contact_block` emits whichever channels are declared, so "until
  every one of these is answered" is false. The true consequence is that `cv run` refuses
  (`skipped-config`) until **at least one name part and at least one contact channel** are declared —
  and the wording must not imply more personal data is required than the code needs.

### The neutrality guards

- `tests/test_sluice_neutral_defaults.py::test_cv_defaults_carry_no_pii` loses its two `CvConfig().name`
  /`.contact` assertions and gains `test_candidate_profile_defaults_carry_no_pii`. **Derived, not
  hand-listed** (`neu4-001`, `test4-008`): it iterates `dataclasses.fields(CandidateProfile)` and
  asserts every default is `""`, so field 37 is covered the day it is added. Hand-listing 36 names is
  the enumeration failure this file's own comments already record twice.
  **`CandidateProfile` cannot simply join `_SWEPT_CONFIGS`**: `test_swept_configs_covers_every_config_dataclass`
  asserts `discovered == set(_SWEPT_CONFIGS)` as an **equality** against
  `_discover_config_dataclasses()`, which globs `sluice/**/config.py` for `*Config` — so appending a
  class that lives in `core/protocols.py` and is not named `*Config` reddens *that* guard instead. The
  new test is therefore its own derived sweep beside the existing ones, and carries a scope assertion
  (it enumerated 36 fields, not zero) so a broken `dataclasses.fields` call cannot pass vacuously.
- **Protected-characteristic fixtures get a mechanism, not a promise** (`neu4-002`). Every other
  personal-data category in this repo is enforced — seeded `faker` for titles, a reviewed roster plus
  four collectors plus RFC 2606 for names and mail domains — while the round-3 draft protected these
  nine with the prose "obviously-synthetic placeholders (documented as such)". Nothing local can tell
  whether `ethnicity: <a real category>` in a packet test is synthetic, which is exactly why the
  existing guards are ratchets that force a human call. So: test fixtures for the nine
  equal-opportunities fields use the token shape `SYNTHETIC-<FIELD>-<N>` (e.g.
  `SYNTHETIC-ETHNICITY-1`), and `tests/test_fixture_name_neutrality.py` gains a **fifth collector**
  sweeping those field positions in `tests/` and asserting every captured value matches
  `^SYNTHETIC-[A-Z_]+-\d+$`. The collector inherits the file's existing
  `test_every_collector_actually_finds_fixtures` parametrization, which is what stops the sweep passing
  by matching nothing — the failure mode this repo has hit before.

### Not covered by a new automated test

`sluice.yaml.example`, `.rulesync/rules/CLAUDE.md`, `README.md` and `docs/*` rely on the existing
`tests/test_docs_claims.py` / `tests/test_no_copy_instruction.py` drift guards. The `sluice/cli.py`
user-facing string identified above is **not** in that category — it is shipped code and gets a test
asserting it names the vault note.
