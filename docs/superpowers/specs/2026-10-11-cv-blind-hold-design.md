# Blind CV hold and a lead-supplied job description (#369) — design

Approved in brainstorming on 2026-10-11 and revised the same day through `/review-plan`, every
finding folded in below. Rulings are recorded under Decisions.

## Goal

`cv run` must not quietly serve a CV composed against no job description, and a lead that
carries its own job description must be usable without a fetch.

**The problem, measured in the issue:** a shortlist lead with no `url` (or whose fetch fails
or returns nothing) composes from the verified bundle alone, passes the fabrication gate
honestly (`violations=0`), renders, is served, and gets `tailored_cv` written, exiting 0. The
only signal is a count line printed after the success line. A blind CV looks just like a
tailored one at the moment it is sent. A referral or InMail lead has no posting to fetch, and
there is no way to give `cv run` its job description: the JD can sit in the note body and the
composer never sees it.

**Success:**

1. At shipped defaults, a blind compose never gets a `tailored_cv` pointer without an
   explicit sign-off.
2. A lead whose note carries a `## Job description` section is judged by triage and composed
   by cv from that text, with no fetch FOR THE JOB DESCRIPTION. (Company resolution may still
   fetch a lead's url; see §2.)
3. `job-sluice leads add` and MCP `create_lead` can write that section when they create the
   lead.
4. Every CV result says where its job description came from, so a JD taken from the note is
   never invisible.

## Decisions (owner, 2026-10-11)

| Question | Decision |
|---|---|
| Blind compose | HOLD for sign-off (not refuse-unless-`--allow-blind`). The CV is composed and rendered; `tailored_cv` is withheld until `job-sluice cv signoff` |
| Hold key | Its own key, `cv.blind_hold`, default `true`. Not `require_signoff` (fabrication) and not `style_hold` (wording) |
| Where the note's JD is read | The dossier layer, so triage and cv both read it. The section wins over a fetch and is never cached |
| Create path | In this PR: `leads add --jd-file` and `create_lead(job_description=...)` write the section on create |
| A JD the MCP model wrote | REPORT ONLY (review round 1, inv-003). Any section counts as a JD; every result reports `jd_source`. A made-up JD costs tailoring quality, not truth: the gate licenses figures and tools from verified entries only |

Decided while writing the spec, not escalated:

| Question | Decision | Why |
|---|---|---|
| How the create path writes the section (arc-001) | `Sluice.create_lead` creates the note, then calls the EXISTING `Store.append_body_section`. `Lead` and `Vault._render_new` are untouched | `append_body_section` is already a Store-protocol member with a stated contract (idempotent by tag, CAS-routed). It also avoids adding a `Lead` field, which `fields(Lead)`-derived rosters would pick up (`core/config.py::_PARAMS_KEY_CLASH` would start refusing a search param named `job_description`; the JSON sink would grow a key on every scraped lead). Cost: two writes, so a lead can exist without its section, which is reported (§3) |
| Where the append gets its `ref` (round 2: arc-101, inv-101, rev-101, tst-106) | `UpsertResult` gains `ref`, populated exactly when `slug` is. This is the ONE Store-contract change in this design, stated in `core/protocols.py` and pinned by a conformance row | `UpsertResult.slug` already exists because a caller must never reconstruct which note a write touched (`Sluice.create_lead`'s docstring records re-reading `read_leads` by company+title as proven wrong). The `ref` is the same answer in the form the write methods take. The alternatives were worse: rebuilding a path from the slug is wrong under `lead_layout`, and a slug lookup over `read_leads` costs a full vault walk per create. Not a `!`: nothing imports `sluice` as a library |
| Hold reason text | One of three fixed phrases, never exception text | `needs_signoff` is frontmatter in the user's note; an exception message can carry a url or arbitrary page text. The exception is already logged |
| Exit code of a blind hold | 0, the same as every `needs-signoff` | The issue's "exit non-zero" was its fallback ("at minimum"); a hold is the stronger response and is reported per result |
| `--url` on `leads add` / `create_lead` | Stays required | Making it optional changes apply eligibility (`apply/select.py::eligibility`) and is a separate decision |
| Release type | `feat(cv)`, no `!` | `CHANGELOG.md`'s classes: a new default changing what an existing install does is `Changed` with a migration note, edited into the release PR. Nothing in a user's config stops parsing |


## 1. The section format — one module

New module `sluice/core/jdsection.py`, the ONE home of the section's grammar, imported by the
reader (dossier callers) and the writer (`Sluice.create_lead`). Two copies of "which line ends
the section" (one deciding where reading stops, one deciding what writing must neutralise)
would drift, and a drift silently truncates the posting (arc-002, inv-004, rev-004, tst-005).
It sits beside `core/names.py` and `core/tokens.py`, the same shape: small, pure, shared by
more than one sub-app, which `core/` may not import.

```python
HEADING = "## Job description"
TAG = "<!-- sluice:job-description -->"

def breaks_section(line: str) -> bool: ...
def read(body: str) -> tuple[str, int]: ...     # (text, number of sections found)
def render(text: str) -> str: ...               # the section to append, headings demoted
```

- **Line endings.** Both functions normalise `\r\n` and `\r` to `\n` before splitting, so a
  CRLF note reads the same as an LF one.
- **`breaks_section(line)`** is True for a CommonMark ATX heading of level 1 or 2: up to three
  leading spaces, one or two `#`, then a space, a tab, or end of line (`#`, `##`, `#\tX`,
  `  ## X` all break; `###`, `#hashtag`, four-space-indented `## X` do not). One predicate,
  used by both sides.
- **`read(body)`** finds the first HEADING line: the line with a trailing `TAG` and trailing
  whitespace removed, compared case-insensitively, equals `HEADING`. It returns the lines
  after it up to the next line for which `breaks_section` is True (or end of body), stripped,
  and how many HEADING lines the body holds, so the CALLER can warn about duplicates
  (rev-003). `("", 0)` when there is none. A hand-typed heading without the tag reads the same
  as a written one.
- **`render(text)`** returns `f"{HEADING} {TAG}\n\n{demoted}"`, where every line of `text` for
  which `breaks_section` is True is demoted to level 3 (its leading `#` run replaced by `###`,
  indentation kept). Heading levels change; nothing is refused. A `## Job description` line
  inside the text is demoted like any other, so a written note has exactly one section.
- **Why a `TAG`** (arc-104, inv-103): `Store.append_body_section`'s idempotence is "the tag
  appears anywhere in the fresh file". The heading text alone could appear in a frontmatter
  title or a pasted body, turning the append into a no-op on a note that has no section. The
  comment is invisible in Obsidian's reading view and is what `track/reconcile.py`'s tags
  already look like.
- **Fenced code** is skipped by the reader, as Obsidian renders it (added in review on #394): a
  `## Job description` line inside a fence is not the section, and a `## ` line inside a fence
  does not end it. The writer still demotes such a line, which agrees with the reader either way.
- **Residual, stated:** setext headings (a line underlined with `===`/`---`) are not parsed.
  That is harmless: the reader does not treat one as a break, so nothing is truncated.

Pure, stdlib only, no I/O.

## 2. Reading the job description

### The dossier layer (`core/dossier.py`)

`get_or_build`'s dict literal moves into one private builder, `_assemble(lead, enrich)`, used by
both arms, so the supplied arm cannot grow a second copy of the dossier's key set (arc-003).

`DossierCache.get_or_build(lead, *, supplied_jd: str = "")`:

- **`supplied_jd` non-empty:** checked FIRST, before `_path` / `_fresh`. Returns
  `_assemble(lead, {"jd": {"markdown": supplied_jd}})`. **No fetcher call, no cache read, no
  cache write**: an edit to the section takes effect on the next run, and a stale cached fetch
  can never shadow it.
- The returned dossier is judged by the same `jd_arrived` predicate, so `min_jd_chars` applies
  to a supplied JD exactly as to a fetched one.
- **`supplied_jd` empty:** today's behaviour, byte for byte.

A keyword parameter rather than reading `body` inside the cache: the cache is handed frontmatter
dicts and must not learn what a note is.

**A section counts only when its text is non-empty after `strip()`** (rev-201, inv-201). A
bare `## Job description` heading with nothing under it is NO section, in cv and in triage
alike: no keyword, `jd_source` decided by the fetch, the fetch's own blind reason. One
predicate, `supplied.strip()`, is the only test either caller makes; the heading count is
used for the duplicate warning and nothing else.

**Callers pass the keyword ONLY when a section counts** (rev-102, tst-101):
`get_or_build(fm)` exactly as today otherwise. About ten test fakes implement
`get_or_build(self, fm)`; an unconditional keyword would make each raise `TypeError` inside
cv's `except Exception` arm, read as a failed fetch, and (under the new default) a blind hold,
so tests would go red or green for the wrong reason. Conditional passing leaves every
existing fake and every lead without a section on today's exact call. Only the fakes the new
section rows use are widened.

### cv (`cv/engine.py::_run_one`)

- `supplied, sections = jdsection.read(note.body)` is computed **BEFORE** the dossier `try`
  (tst-002). Inside it, a reader bug would be swallowed and reported as "the job description
  fetch failed". Outside it, a raise reaches `run_batch`'s per-lead catch-all as `error`.
- `sections > 1` logs a warning naming the note and saying the first section was used.
- `jd_source` is set alongside `dossier_failed` and says where the TEXT came from, never
  whether it was enough (inv-104):
  - `"note"`: a section counted (non-empty, whatever its length relative to `min_jd_chars`);
  - `"fetched"`: a fetch returned JD text, even text below `min_jd_chars`;
  - `"none"`: no text at all (the fetch raised, or returned an empty JD).

  Whether the text sufficed is `dossier_failed`'s job, as today. Both are reported, never
  control flow beyond the hold below.
- `jd_source` travels every route `dossier_failed` travels (arc-103): `CvResult.jd_source`,
  the exception stamp `_run_one`'s handler sets so an `error` result is rebuilt with it
  (`run_batch` and `Sluice.compose_cv` read it with `getattr(e, "jd_source", "")`), the
  `run.json` artefact, and MCP `cv_run`'s result.

### triage (`triage/engine.py::_prefetch_dossiers`)

- Sections are read per lead INSIDE `_prefetch_dossiers`, each in its own `try`, over the SAME
  twin-filtered `targets` list (inv-002, tst-104). A read that raises is stored at that lead's
  index as a new `_SectionUnreadable(e)` result, which the caller reports as
  `section <ref>: <error>` (not `dossier <ref>: ...`, the masquerade tst-002 fixed in cv;
  inv-104) and skips, the run continuing.
- A lead whose section counts (the same `strip()` predicate as cv) is built directly with `supplied_jd` (no fetcher call, no
  pool slot), stored at its own `keeps` index, with a raise caught into `_FetchFailed` like
  every other lead. It is NOT grouped by `cache_key`: two notes sharing a key may carry
  different sections. Leads without a section go through the existing grouped pool unchanged.
- `sections > 1` logs the same warning as cv.
- The function also returns the set of indices built from a section, so the caller words the
  `unjudgeable` reason correctly (rev-103): a short note section reads `the job description in
  the note is shorter than min_jd_chars (N)`, not `no job description was fetched`. A set of
  indices, not a marker key in the dossier: a marker would ride `slim()` into the judge prompt
  (`DossierCache.jd_arrived`'s docstring).
- The function's docstring states the new arm and that its index contract is unchanged.

### What is NOT changed

`triage/resolve.py` keeps calling `get_or_build(fm)` with no supplied JD. Company resolution
reads the fetched page (`page_title`, `structured_data`, and at tier 3 the page's JD text), and
it already abstains without a url. So a lead with BOTH a url and a section may still have its
page fetched and cached by company resolution when its company is unresolved; the JUDGE and
the COMPOSER read the section regardless (arc-004, rev-007).

The supplied JD licenses nothing at the fabrication gate: the gate reads only verified
experience entries (`cv/validate.py::entry_facts`). It reaches the composer and the judge
exactly where a fetched JD would, as the same untrusted page-class text.

## 3. Writing the section on create

### The one Store-contract change

`core/protocols.py::UpsertResult` gains `ref: object = None`, populated exactly when `slug` is
(`created` / `updated` / `merged`), and carrying the same opaque handle `read_leads` would issue
for that note. Its docstring states the obligation in the shape `slug`'s already does. The
vault's `upsert` sets it from the path it just resolved. A conformance row in
`tests/conformance/test_store_contract.py` asserts that a `created` result's `ref` is accepted
by `append_body_section` and names the same note `read_leads` returns for that slug, and that
`ref` is `None` on `refused` / `merged_away` / `merged_away_unproven`.

### `Sluice.create_lead(..., job_description: str | None = None)`

`None` means no JD was given; a string is a JD the caller asked to write (arc-301).

0. A non-`None` `job_description` that is empty or whitespace-only joins `create_lead`'s
   EXISTING up-front `ValueError` (the one that already names every invalid field before any
   write), so it is refused before `upsert` and no lead is created. This is the ONE home of the
   empty-JD refusal: the CLI and the MCP tool both reach it, because both drive this facade,
   and neither restates it. A caller that asked for a JD and silently got a lead without one
   is the quiet wrong default.
1. Calls `store.upsert` exactly as today.
2. Only when the outcome is `created`, `job_description` is not `None`, AND `result.ref` is
   not `None` (step 5): calls
   `store.append_body_section(result.ref, jdsection.TAG, jdsection.render(job_description))`.
3. `CreateLeadResult` gains `job_description_written: bool` and
   `job_description_note: str`. Written is True only when step 2 ran and returned True. Every
   other case leaves it False, with the note saying why, from a fixed set:
   - outcome not `created` (`updated`, `merged`, `refused`, `merged_away`,
     `merged_away_unproven`): `the lead already existed, or was not created`;
   - `created` with no `ref` (step 5): `the store did not return a reference to the new note`;
   - the append returned False: `a job description section was already present`;
   - the append raised `VaultConflict` or `OSError` (the note was edited, moved or renamed
     between create and append: `leads reconcile`, Obsidian, Syncthing; inv-102):
     `the note changed before the job description could be added`, with the exception
     logged.

   Both exception arms are caught there with a comment saying why: the lead WAS created, and
   letting the error escape would report a failed create for a note that exists, inviting a
   retry that lands on `updated` and never writes the JD. The `created` outcome and slug are
   reported unchanged.
4. No JD given (`None`): `job_description_written` False, note empty.
5. Outcome `created` but `result.ref is None` (a store or fake that does not honour the new
   contract field; inv-202): the append is NOT attempted, written False, note
   `the store did not return a reference to the new note`, logged as a warning. Without this
   arm, `append_body_section(None, ...)` raises `TypeError`, which neither caught exception
   covers, and a created lead reads as a failed create.

`updated` and `merged` never touch the note body (never-clobber), and no new write function is
added (no new CodeQL sink).

### CLI

`leads add --jd-file PATH|-` reads the text (`-` is stdin), matching the evidence commands'
`--body-file` in shape (`sluice/evidence/commands.py::cmd_evidence_add`: read first, in its
own `try`, a named message and exit 1) and going two steps further (rev-202). The file is read
BEFORE `create_lead` is called. An `OSError`, and also a `UnicodeDecodeError` (a `ValueError`,
which the precedent's `except OSError` does not catch), prints a named message and exits 1 with
nothing written. The text read is passed as `job_description` (never `None` once the flag is
given), so an empty or whitespace-only file reaches `create_lead`'s own refusal (step 0) and
propagates as a `ValueError` to `main`'s own handler, exactly as an invalid `--url` does today
(`cli.py::cmd_leads_add`'s docstring): printed, exit 2, no lead. No lead is
created on any refusal, so a corrected re-run lands on `created`, not `updated`. Without
`--jd-file` the CLI passes `None`. When a JD was given and not written, it prints `job_description_note` and tells
the user to add a `## Job description` section to the note by hand. The exit code follows
today's rules.

### MCP

- Both layers change (rev-002): `mcpserver.py::create_lead` and the registered
  `create_lead_tool` inside `build_server`, which is what the client reads. The registered
  tool's docstring says `job_description` is written only when the lead is CREATED, and that
  the text is treated as untrusted page text by the triage judge and the CV composer (neu-003).
- The tool's `job_description` parameter defaults to `None` and is passed through as given, so
  a blank string reaches step 0's refusal exactly as the CLI's does.
- The result carries `job_description_written` and `job_description_note`.
- `tests/functional/test_mcp_contract.py` pins `create_lead`'s input properties by exact
  equality; that set gains `job_description`.

## 4. The blind hold

### Config

`cv/config.py::CvConfig` gains `blind_hold: bool = True`, with a comment in its neighbours'
style: a separate key because `require_signoff` was chosen for fabrication and `style_hold`
for wording, and a blind CV is neither. On by default because the failure it prevents is
invisible at the moment it matters, the reasoning `require_signoff` ships `True` under. It
filters no lead (it withholds a pointer), so it is a safety valve, not a preference gate.

`load_cv_config`'s bool guard is keyed on the default's type, so a quoted `"false"` is refused
by existing code; the tests that pin that are parametrised over a hand-written list, which
gains `blind_hold` (tst-003). `sluice.yaml.example` and `docs/CONFIGURATION.md` gain the key.

### The tag's one owner

`core/leads.py` gains `BLIND_TAG = "blind"` and `split_blind(entries)` beside `FRAMING_TAG` /
`split_framing`: the writer (`cv/engine.py`) and both readers (`cli.py`, `mcpserver.py`) must
spell the tag from one constant, and `core/leads.py` is where all three already import the
framing tag's helpers from (arc-102).

### Engine

`blind_reason` is set beside `dossier_failed`, to one of three fixed phrases:

| Producer | `blind_reason` |
|---|---|
| The fetch raised | `the job description fetch failed` |
| A fetch (or no url, no section) produced no JD that arrived | `no job description arrived` |
| A note section shorter than `min_jd_chars` | `the job description in the note is shorter than min_jd_chars (N)` |

In the blockers assembly, beside `unaudited`:

```python
blind = [f"{BLIND_TAG}\t{blind_reason}"] if dossier_failed and cvcfg.blind_hold else []
blockers = (
    (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
    + blind + style_blockers)
```

Everything after that is the existing path: `hold_for_signoff` with `pending_cv` and
`needs_signoff`, a `needs-signoff` result, `skipped-has-cv` when a CV appeared meanwhile.
`status` stays `shortlist`, so never-regress is untouched.

Comments that become false are rewritten (arc-005, arc-103): the `jd_arrived`-arm comment in
`_run_one` ("a short JD costs tailoring QUALITY, not correctness") stays true of the GATE and
is extended to say why the CV is now HELD rather than refused; the comment on
`CvResult.dossier_failed` ("visibility, never control flow") and the three comments that cite
it as a precedent (`skills_unreadable`, `artefacts_failed`, and `cli.py`'s count-line comment)
are updated, since `dossier_failed` now drives the hold under `blind_hold`.

Not changed, stated so nobody adds it:

- Not a gate finding; `violations=0` stays true.
- No hold when nothing is served (`--no-serve`, or no `served_dir`), as every hold today.
- A dry run reports `dossier_failed` and `jd_source` and holds nothing.

### Releasing or fixing a held blind CV

`cv/engine.py::run_one`'s #60 latch skips any lead holding `pending_cv` before compose. So
adding a section and re-running does NOTHING while the hold stands (rev-001). The two routes:

- `job-sluice cv signoff --lead SLUG` — accept the generalist CV as it is.
- `job-sluice cv signoff --lead SLUG --discard`, add the section, re-run `cv run` — get a
  tailored CV.

The sign-off message for a blind entry names both.

### CLI and MCP reporting

- `cli.py::_print_signoff_claims`: entries split by `split_blind` get their own group, printed
  after framing: `cv signoff: <slug> was composed WITHOUT a job description (<reason>) -- it
  is not tailored to this role. To tailor it: --discard, add a "## Job description" section
  to the note, and re-run cv run.` Not counted as an unsupported claim. The docstring's tag
  roster gains it.
- `cli.py::cmd_cv_run`: each result line gains `jd_source=<note|fetched|none>`. The trailing count
  line is reworded from `composed blind (dossier fetch failed)` to `composed without a job
  description`, and a second count line reports `N CV(s) used the job description from the
  lead note` when any did.
- `mcpserver.py::cv_signoff` splits with `split_blind` at EVERY return arm that currently
  splits framing: `needs_confirmation`, `stale_confirmation`, and the
  `promoted`/`discarded`/`collision` arm (tst-105). Blind entries go under their own `blind`
  key, excluded from `claims`; the tool description says what they mean. The confirm token
  keeps binding the whole stored array, as today.
- `mcpserver.py::_GET_LEAD_CONTENT_WARNING` says `blind` entries in `needs_signoff` are
  sluice's own fixed text, beside its framing clause, instead of falling under "every other
  entry ... derived" (neu-101).

## Error handling

| Case | Behaviour |
|---|---|
| Fetch raises, no section | composed; `jd_source=none`; held, `the job description fetch failed` |
| Fetch returns an empty JD, or no url, no section | composed; `jd_source=none`; held, `no job description arrived` |
| Fetch returns a JD below `min_jd_chars` | composed from it; `jd_source=fetched`; held, `no job description arrived` |
| Section present, long enough | no JD fetch; `jd_source=note`; not held for blindness |
| Section shorter than `min_jd_chars` | triage: `unjudgeable`, reason names the short note section; cv: composed, `jd_source=note`, held with the short-section phrase |
| Two sections | first used; warning names the note (cv and triage) |
| Empty section (heading, no text) | treated as no section, in cv and triage: the fetch decides `jd_source` and the reason |
| `jdsection.read` raises | cv: `error` for that lead; triage: `section <ref>: ...`, lead skipped; never "fetch failed" |
| `blind_hold: false` | today's behaviour: served, `tailored_cv` written, count line printed |
| Nothing served | no hold, as every hold today |
| Held blind lead, section added, re-run | `skipped-needs-signoff` until `--discard` (documented) |
| `create_lead` with a JD, outcome not `created` | note untouched; not written; note says so |
| Append returns False / raises `VaultConflict` or `OSError` | lead created; not written; note says why; logged |
| `created` with no `ref` | append not attempted; lead created; not written; no-reference note; warning logged |
| `--jd-file` unreadable or not UTF-8 | named read message, exit 1, no lead created |
| JD given but empty or whitespace-only (CLI file/stdin, MCP, facade) | `ValueError` naming `job_description` before any write; CLI exit 2; no lead created |

## Testing

**Fixtures are synthetic** (neu-001, neu-102): JD text is placeholder prose or seeded-faker
text, companies come from the existing synthetic roster, job titles from the seeded-faker
`titles`/`cfg_titles` fixtures, and no fixture or doc example carries a contact, salary or city
line.

**Witnesses.** Every row that guards a NEW branch carries a `Witness:` naming its mutant
(tst-202), applied by MOVE or DELETE after `compileall --invalidation-mode checked-hash`; the
row must go red for the named reason. A row without one is a plain behaviour row over
unchanged code, and the plan keeps that distinction rather than claiming more.

**`core/jdsection.py`**
- `read`: no section; empty section (`("", 1)`); heading case variants, trailing whitespace, with and
  without `TAG`; ends at `#`, `##`, `#\tX`, `  ## X` and a bare `##`; does NOT end at `###`,
  `#tag`, four-space indent; CRLF and lone-CR bodies; end of body; two sections returns the
  first text and count 2.
- `render` round trip over the SAME edge spellings `read` breaks on (tst-103): a fixture
  containing `# X`, `## X`, `#\tX`, a bare `##`, `  ## X` and `## Job description`;
  `read(render(t))` returns all of `t` with exactly those lines demoted. Witness: replace
  `render`'s use of `breaks_section` with `line.startswith(("# ", "## "))`; the `#\tX`, bare
  `##` and indented cases truncate, row red. A second witness deletes the demotion outright.

**Dossier**
- Control first (tst-102): with the clock pinned and a hand-written fresh entry carrying JD
  text A above `min_jd_chars`, `get_or_build(lead)` returns A with zero fetcher calls. Then
  `supplied_jd=B` returns B, the fetcher is not called, and the cache file is byte-identical
  (tst-001). Witness: move the supplied check below `_fresh`; the row returns A.
- Empty cache, `supplied_jd=B`: fetcher not called, no file written.
- `min_jd_chars` applies: a supplied JD under the floor gives `jd_arrived` False.
- `_assemble` is the one builder: both arms return the same key set.

**cv engine**
- Existing fakes are untouched by conditional passing. A sweep test asserts the claim: a
  lead with no section calls `get_or_build` with exactly one positional argument and no
  keyword (a recording fake). Witness: pass `supplied_jd=""` unconditionally; row red.
- Blind lead × `blind_hold` on/off × `require_signoff` on/off, with the audit RUNNING CLEAN
  (no flags) in every cell (tst-202): held with a `blind\t` entry and no `tailored_cv` exactly
  when `blind_hold` is on, whatever `require_signoff` says; with `blind_hold` off the CV is
  served in both of its cells. A clean audit is what separates "not held" from "held because the
  audit did not run". Witness: move `blind` inside the `require_signoff` conditional; the
  `blind_hold` on / `require_signoff` off cell goes red.
- Each of the three producers gives its own `blind_reason`, asserted by exact phrase. Witness:
  delete the fetch-raised arm's assignment so it falls through to `no job description
  arrived`; that row red.
- `jd_source` VALUES (tst-107): `fetched` on a healthy fetch, `fetched` on a too-short fetched
  JD, `none` on a raise and on an empty JD, `note` on a section; and on an `error` result
  rebuilt from the exception stamp. Witness: delete the `fetched` assignment; the too-short
  row reads `none`. Second witness: delete the stamp; the `error` row reads `""`.
- An EMPTY section (heading, no text) with a fetch that returns a JD (rev-201, inv-201):
  `get_or_build` called with no keyword, `jd_source == "fetched"`, not held. With a fetch that
  raises: `jd_source == "none"`, held with `the job description fetch failed`, never the
  short-section phrase. Witness: test `sections` instead of `supplied.strip()`; both rows red.
- `run.json` carries `jd_source` on a rendered result AND on an `error` result (arc-201).
  Witness: drop the key from the artefact writer; row red.
- A section lead is `rendered`, `jd_source == "note"`, fetcher never called, not held.
- A `jdsection.read` that raises surfaces as `error`, and the log does not say "fetch failed"
  (tst-002). Witness: move the call inside the `try`; row red.
- Duplicate sections log the warning naming the note (tst-107).
- Dry run: no hold, `jd_source` reported.
- Hold → add section → re-run is `skipped-needs-signoff`; `--discard` → re-run renders with
  `jd_source == "note"` (rev-001).
- Existing tests that break under the new default are fixed one by one, in ANY file, not only
  the engine tests (tst-203): `tests/test_config_paths.py`'s `_NullCache` and every other stand-in
  cache a red row turns up. Each first asserts the lead's `jd_source` (so a broken fake cannot
  hide behind the fix), then sets `blind_hold` explicitly or supplies a JD; `_cfg()`'s default
  is NOT flipped (tst-003, tst-101).

**Config** (inv-001, rev-005, tst-003)
- `CvConfig().blind_hold is True`, as a named row in `tests/test_sluice_neutral_defaults.py`
  beside `style_hold`/`term_check`. Witness: flip the default.
- `load_cv_config` with no `cv:` block yields `blind_hold is True`.
- `blind_hold` joins the quoted-false and real-boolean parametrisations in
  `tests/test_cv_config.py`.
- `docs/CONFIGURATION.md` gains a row
  (`tests/test_config_example.py::test_every_config_field_has_a_row_in_its_own_reference_section`
  goes red without it). The example's key binds to a real field
  (`test_every_example_key_binds_to_a_real_config_field`); nothing checks the reverse
  direction, so a row asserts `sluice.yaml.example` mentions `blind_hold`, and that it is not
  ACTIVE (the example is a catalogue whose active values matter).

**triage** (tst-201). The existing stand-in caches (`_RecordingCache`, `_CacheWhere`, the
resolve-test double) return a JD whatever the lead holds, so a section row run on them goes
green with the feature deleted. The section rows therefore use a REAL `DossierCache` in
`tmp_path` with a COUNTING fetcher that returns an EMPTY JD, on leads whose `company` is
already set, so company resolution never fetches and every fetcher call is the prefetch's.
- A lead with a section and no url is judged, not `unjudgeable`, and a sentinel string from
  the section appears in the judge's prompt. Witness: drop the supplied arm in
  `_prefetch_dossiers`; the lead goes `unjudgeable` and the sentinel is absent.
- A url-bearing lead with a section: zero fetcher calls, the sentinel in the prompt (tst-104).
  Without the section, the same lead makes one call and is `unjudgeable` (the control showing
  the counter counts).
- An empty section on a url-bearing lead: one fetcher call, `unjudgeable` with the fetched
  reason, not the note-section reason (rev-201). Witness: test the heading count instead of
  `supplied.strip()` in `_prefetch_dossiers`; zero fetcher calls and the note-section reason,
  row red (tst-302).
- Two notes whose `cache_key` is ASSERTED equal first (tst-007), with different sections, each
  get their own JD, at concurrency 1 and at concurrency > 1. Witness: group section notes by
  key; row red.
- A section read that raises lands as `section <ref>: ...` at its own index; other leads are
  judged; the report does not say `dossier` (tst-104, inv-104). Witness: drop the per-lead
  `try`; the run aborts, row red.
- A too-short section is `unjudgeable` with the note-section reason.
- Duplicate sections log the warning.

**Create path** (tst-106)
- `created` with a JD: the section is appended, `jdsection.read` returns it,
  `job_description_written` True. Run under `lead_layout` flat AND a non-flat layout, so a
  ref rebuilt from the slug would fail.
- The five other outcomes: a spy store asserts `append_body_section` was NEVER called, and the
  note reads the fixed reason. (Not "byte-identical": three of those outcomes write no note,
  so that assertion would be vacuous.)
- Append returning False, raising `VaultConflict`, raising `FileNotFoundError`: outcome
  `created` with its slug, written False, the matching note. Witness: narrow the `except` to
  `VaultConflict`; the `FileNotFoundError` row raises.
- Witness for the spy row: delete the `created` check; the `updated` row sees a call.
- `created` with `ref=None` from a fake store (inv-202) whose `append_body_section` RAISES
  `TypeError` on a `None` ref (as the vault's `_cas_write` path would), and records every
  call: no append attempted, written False, the no-reference note, outcome `created`. Witness:
  delete the `ref` condition from step 2; the call is recorded and the `TypeError` escapes
  `create_lead`, row red for the stated reason (rev-302, tst-302).
- Store conformance row for `UpsertResult.ref` (§3). Witness: drop `ref=` from the vault's
  `created` return; row red.
- `leads add --jd-file -` reads stdin; `--jd-file PATH` reads a file; the not-written note
  prints on `updated`.
- `--jd-file` naming a missing file and a non-UTF-8 file (rev-202): exit 1, the named read
  message, NO note created (the store read back empty). Witness: delete the `try` around the
  read; the rows show a traceback in place of the named message (rev-301).
- `--jd-file` naming an empty file, and whitespace-only stdin: exit 2 (the `ValueError` path
  an invalid `--url` already takes), the field named in the message, no note. `Sluice.create_lead(job_description="  ")` raises `ValueError` naming
  `job_description` with the store read back empty, and the MCP `create_lead` tool with a blank
  `job_description` likewise creates nothing (arc-301). Witness: delete step 0's check; each
  row finds a lead created at success with no section (rev-301).
- `create_lead(job_description=None)` and the CLI without `--jd-file`: today's behaviour, no
  append attempted.
- MCP: `create_lead`'s pinned input set gains `job_description`; the result carries both
  fields.

**Reporting**
- `cmd_cv_run` prints the right `jd_source=` value per line, the reworded count line, and the
  note-JD count line (no test pins either wording today, tst-004). The key is spelled as its
  field, like every other key on the line (arc-202), and
  `tests/test_docs_claims.py::test_the_cv_summary_key_extraction_is_not_vacuous`'s exact set
  gains `jd_source`.
- `_print_signoff_claims` prints the blind group with both routes and does not count it as an
  unsupported claim.
- MCP `cv_signoff` returns `blind` separately and excludes it from `claims` at EACH of its
  three return arms, one row per arm (tst-105). Witness: drop the split from one arm; that
  arm's row red.
- The `cv_signoff` confirm token still binds a blind entry (tst-203): changing ONLY the blind
  entry in `needs_signoff` makes the old token return `stale_confirmation`.
- `_GET_LEAD_CONTENT_WARNING` names `blind` entries as sluice's own text (neu-101).

## Docs

- `docs/CONFIGURATION.md`: `cv.blind_hold`.
- `docs/USAGE.md`: `leads add --jd-file`, the section convention (synthetic example), the
  `jd_source=` key in the per-result line paragraph, `jd_source` in the `run.json` table row
  (arc-201), and the reworded line that replaces "when any dossier fetch failed and
  composition proceeded blind" (rev-009).
- `docs/ARCHITECTURE.md`: the dossier's supplied arm and `core/jdsection.py`; its "a thin JD
  costs only quality" reasoning extended to say the CV is held (arc-005); `UpsertResult.ref`
  in the store-contract description.
- `docs/TROUBLESHOOTING.md`: a held blind CV, both routes, and the `--discard` step.
- `sluice.yaml.example`: the key, commented.
- `.rulesync/rules/CLAUDE.md` (regenerate after): "The advisory audit and the holds"
  (`blind_hold` beside `style_hold`); the `min_jd_chars` paragraph (a supplied JD obeys it);
  the `leads add` paragraph (`--jd-file`, written through `append_body_section` with the
  store-issued `ref`). Each claim lands in the task that makes it true.
- Code comments stating the old wording or the old "visibility only" rule: `cli.py`'s
  count-line comment, `cv/engine.py::run_batch`'s and `core/app.py::compose_cv`'s "composed
  blind" comments (rev-103), the `CvResult.dossier_failed` comment and the three that cite it
  (§4), the `_prefetch_dossiers` docstring, and the comments in `tests/test_cv_engine.py` and
  `tests/test_app_operations.py` that quote "composed blind".

## Out of scope

- Making `url` optional on `leads add` / `create_lead`.
- A command that writes a JD section into an EXISTING note.
- Refusing a blind compose before spend (the rejected alternative).
- Holding a CV composed from a model-written JD (ruled report-only).
