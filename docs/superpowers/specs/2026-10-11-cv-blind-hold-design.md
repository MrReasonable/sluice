# Blind CV hold and a lead-supplied job description (#369) — design

Approved in brainstorming on 2026-10-11; revised the same day after one `/review-plan` round
(5 reviewers, 0 Critical / 4 High / 18 Medium / 10 Low), every finding folded in below.
Rulings are recorded under Decisions.

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
| How the create path writes the section (arc-001) | `Sluice.create_lead` creates the note, then calls the EXISTING `Store.append_body_section`. `Lead` and `Vault._render_new` are untouched | `append_body_section` is already a Store-protocol member with a stated contract (idempotent by tag, CAS-routed), so no contract change and no conformance gap. It also avoids adding a `Lead` field, which `fields(Lead)`-derived rosters would pick up (`core/config.py::_PARAMS_KEY_CLASH` would start refusing a search param named `job_description`; the JSON sink would grow a key on every scraped lead). Cost: two writes, so a lead can exist without its section, which is reported (§3) |
| Hold reason text | One of three fixed phrases, never exception text | `needs_signoff` is frontmatter in the user's note; an exception message can carry a url or arbitrary page text. The exception is already logged |
| Exit code of a blind hold | 0, the same as every `needs-signoff` | The issue's "exit non-zero" was its fallback ("at minimum"); a hold is the stronger response and is reported per result |
| `--url` on `leads add` / `create_lead` | Stays required | Making it optional changes apply eligibility (`apply/select.py::eligibility`) and is a separate decision |
| Release type | `feat(cv)`, no `!` | `CHANGELOG.md`'s classes: a new default changing what an existing install does is `Changed` with a migration note, edited into the release PR. Nothing in a user's config stops parsing |

## 1. The section format — one module

New module `sluice/core/jdsection.py`, the ONE home of the section's grammar, imported by the
reader (dossier callers) and the writer (`Sluice.create_lead`). Two copies of "which line ends
the section" (one deciding where reading stops, one deciding what writing must neutralise)
would drift, and a drift silently truncates the posting (arc-002, inv-004, rev-004, tst-005).

```python
HEADING = "## Job description"

def breaks_section(line: str) -> bool: ...
def read(body: str) -> tuple[str, int]: ...     # (text, number of sections found)
def render(text: str) -> str: ...               # the section to append, headings demoted
```

- **Line endings.** Both functions split on `str.splitlines()` semantics after normalising
  `\r\n` and `\r` to `\n`, so a CRLF note reads the same as an LF one.
- **`breaks_section(line)`** is True for a CommonMark ATX heading of level 1 or 2: up to three
  leading spaces, one or two `#`, then a space, a tab, or end of line (`#`, `##`, `#\tX`,
  `  ## X` all break; `###`, `#hashtag`, four-space-indented `## X` do not). One predicate,
  used by both sides.
- **`read(body)`** finds the first line that, stripped of trailing whitespace and compared
  case-insensitively, equals `HEADING`, and returns the lines after it up to the next line for
  which `breaks_section` is True (or end of body), stripped. It also returns how many such
  heading lines the body holds, so the CALLER can warn about duplicates (rev-003). `("", 0)`
  when there is none.
- **`render(text)`** returns `HEADING + "\n\n" + text` with every line of `text` for which
  `breaks_section` is True demoted to level 3 (its leading `#`s replaced by `###`). That
  changes heading levels only; nothing is refused. A `## Job description` line inside the text
  is demoted like any other, so a written note has exactly one section.
- **Residuals, stated:** setext headings (a line underlined with `===`/`---`) and fenced code
  are not parsed. Setext is harmless: the reader does not treat it as a break, so nothing is
  truncated. A `## ` line inside a fence DOES break on read and IS demoted on write; both sides
  agree, so the writer never produces a section the reader truncates.

Pure, stdlib only, no I/O. It lives in `core/` because both triage and cv read it and `core/`
may not import a sub-app.

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

### cv (`cv/engine.py::_run_one`)

- `supplied, sections = jdsection.read(note.body)` is computed **BEFORE** the dossier `try`
  (tst-002). Inside that `try` it would be swallowed by the `except Exception` arm and
  reported as "the job description fetch failed": a bug in the reader would masquerade as a
  failed fetch. Outside it, a raise reaches `run_batch`'s per-lead catch-all as `error`.
- `sections > 1` logs a warning naming the note and saying the first section was used.
- The call becomes `dossier_cache.get_or_build(fm, supplied_jd=supplied)`.
- A local `jd_source` is set alongside `dossier_failed`: `"note"` when `supplied` was
  non-empty, `"fetched"` when a fetch produced a JD that arrived, `"none"` otherwise.
  `CvResult` gains `jd_source: str = ""`; it is reported, never control flow.

### triage (`triage/engine.py::_prefetch_dossiers`)

Notes whose body yields a section are handled INSIDE `_prefetch_dossiers`, from the SAME
twin-filtered `targets` list, stored under the SAME `keeps` index, and with exceptions caught
into `_FetchFailed` like every other lead (inv-002). That keeps the function's load-bearing
contract: every non-ambiguous index present, per-lead isolation, `keeps` order. They are built
directly (no fetcher call, so no pool slot) and are NOT grouped by `cache_key`, since two notes
sharing a key may carry different sections. Notes without a section go through the existing
grouped pool unchanged. The docstring is updated to say so.

The caller's existing `jd_arrived` / `unjudgeable` branch is unchanged in control flow; its
reason text distinguishes a too-short supplied JD (see Error handling).

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

`Sluice.create_lead(..., job_description: str = "")`:

1. Calls `store.upsert` exactly as today.
2. Only when the outcome is `created` AND `job_description.strip()` is non-empty: calls
   `store.append_body_section(ref, jdsection.HEADING, jdsection.render(job_description))`.
   The tag is the heading itself; on a note created a moment ago it is absent, and the
   idempotence makes a retry harmless.
3. `CreateLeadResult` gains `job_description_written: bool`, True only when step 2 ran and
   returned True. Every other outcome of the six (`updated`, `merged`, `refused`,
   `merged_away`, `merged_away_unproven`) leaves it False, as does a `VaultConflict` or
   `False` from the append (inv-006). A `VaultConflict` on the append is caught there, logged,
   and reported as not written; the lead itself WAS created and that outcome is reported
   unchanged.

`updated` and `merged` never touch the note body (never-clobber), and no new write function is
added (no new CodeQL sink).

### CLI

`leads add --jd-file PATH|-` reads the text (`-` is stdin), matching the evidence commands'
`--body-file`. When a JD was given and `job_description_written` is False, it prints that the
job description was NOT written, why (the note already existed; or the write conflicted), and
to add a `## Job description` section to the note by hand. The exit code follows today's
rules.

### MCP

- Both layers change (rev-002): `mcpserver.py::create_lead` and the registered
  `create_lead_tool` inside `build_server`, which is what the client reads. The registered
  tool's docstring says `job_description` is written only when the lead is CREATED, and that
  the text is treated as untrusted page text by the triage judge and the CV composer (neu-003).
- The result carries `job_description_written`.
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

### Engine

`blind_reason` is set beside `dossier_failed`, to one of three fixed phrases:

| Producer | `blind_reason` |
|---|---|
| The fetch raised | `the job description fetch failed` |
| A fetch (or no url, no section) produced no JD that arrived | `no job description arrived` |
| A note section shorter than `min_jd_chars` | `the job description in the note is shorter than min_jd_chars (N)` |

In the blockers assembly, beside `unaudited`:

```python
blind = [f"blind\t{blind_reason}"] if dossier_failed and cvcfg.blind_hold else []
blockers = (
    (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
    + blind + style_blockers)
```

Everything after that is the existing path: `hold_for_signoff` with `pending_cv` and
`needs_signoff`, a `needs-signoff` result, `skipped-has-cv` when a CV appeared meanwhile.
`status` stays `shortlist`, so never-regress is untouched.

The `jd_arrived`-arm comment in `_run_one` ("a short JD costs tailoring QUALITY, not
correctness") stays true of the GATE and is extended: it is now also why the CV is HELD rather
than refused (arc-005).

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

- `cli.py::_print_signoff_claims`: a `blind\t` entry gets its own group, printed after
  framing: `cv signoff: <slug> was composed WITHOUT a job description (<reason>) -- it is not
  tailored to this role. To tailor it: --discard, add a "## Job description" section to the
  note, and re-run cv run.` Not counted as an unsupported claim. The docstring's tag roster
  gains it.
- `cli.py::cmd_cv_run`: each result line gains `jd=<note|fetched|none>`. The trailing count
  line is reworded from `composed blind (dossier fetch failed)` to `composed without a job
  description`, and a second count line reports `N CV(s) used the job description from the
  lead note` when any did. Comments updated to say the hold, not the line, is the control.
- `mcpserver.py`: `cv_run`'s result gains `jd_source`. `cv_signoff` currently separates only
  `framing` entries (`core/leads.py::split_framing`), so a `blind\t` entry would reach the
  agent as a claim under the derived-content warning (neu-002, rev-002). `cv_signoff` returns
  blind entries under their own `blind` key, excluded from `claims`; the tool description
  says what they mean. The confirm token keeps binding the stored hold as today.

## Error handling

| Case | Behaviour |
|---|---|
| Fetch raises, no section | composed; `jd=none`; held, `the job description fetch failed` |
| Fetch returns no JD, or no url, no section | composed; `jd=none`; held, `no job description arrived` |
| Section present, long enough | no JD fetch; `jd=note`; not held for blindness |
| Section shorter than `min_jd_chars` | triage stamps `unjudgeable` with a reason naming the short note section (not "was fetched"); cv composes, `jd=note`, held with the short-section phrase |
| Two sections | first used; warning names the note |
| `jdsection.read` raises | `error` for that lead via the per-lead catch-all, never "fetch failed" |
| `blind_hold: false` | today's behaviour: served, `tailored_cv` written, count line printed |
| Nothing served | no hold, as every hold today |
| Held blind lead, section added, re-run | `skipped-needs-signoff` until `--discard` (documented) |
| `create_lead` on an existing lead with a JD | note untouched; `job_description_written: false`; CLI says so |
| Append conflicts after create | lead created; `job_description_written: false`; logged |

## Testing

Fixtures are synthetic (neu-001): JD text is placeholder prose or seeded-faker text, companies
come from the existing synthetic roster, and no fixture or doc example carries a contact,
salary or city line. Every guard below names the mutant that witnesses it, applied by MOVE or
DELETE after `compileall --invalidation-mode checked-hash`, and the row must go red.

**`core/jdsection.py`**
- `read`: no section; empty section; heading case variants and trailing whitespace; ends at
  `#`, `##`, `#\tX`, `  ## X` and a bare `##`; does NOT end at `###`, `#tag`, four-space
  indent; CRLF body; end of body; two sections returns the first text and count 2.
- `render` round trip with a fixture that DOES contain `# X`, `## X` and `## Job description`
  lines (tst-005): `read(render(t))` returns all of `t` with those lines demoted. Witness:
  delete the demotion, the read truncates, row red.
- A sweep asserting `render`'s demotion and `read`'s terminator both call `breaks_section`
  (one predicate, by construction).

**Dossier**
- Cache holds a fresh entry with JD text A for the lead's key; call with `supplied_jd=B`;
  assert the dossier carries B, the fetcher was not called, and the cache file is
  byte-identical (tst-001). Witness: move the supplied check below `_fresh`, the row returns A.
- Empty cache, `supplied_jd=B`: fetcher not called, no file written.
- `min_jd_chars` applies: a supplied JD under the floor gives `jd_arrived` False.
- `_assemble` is the one builder: both arms return the same key set.

**cv engine** (fakes updated FIRST: `tests/structured_cv.py::Cache.get_or_build` and the SSRF
test's `_BlockedCache` accept `supplied_jd`; the `Note` fakes gain `body`)
- Blind lead × `blind_hold` on/off × `require_signoff` on/off: held with a `blind\t` entry and
  no `tailored_cv` exactly when `blind_hold` is on, whatever `require_signoff` says.
- Each of the three producers gives its own `blind_reason`.
- A section lead is `rendered`, `jd_source == "note"`, fetcher never called, not held.
- A `jdsection.read` that raises surfaces as `error`, and the log does not say "fetch failed"
  (tst-002). Witness: move the call inside the `try`, row red.
- The SSRF test still passes for its own reason: assert the logged exception is the SSRF
  block.
- Dry run: no hold, `jd_source` reported.
- Hold → add section → re-run is `skipped-needs-signoff`; `--discard` → re-run renders with
  `jd=note` (rev-001).
- Existing engine tests that break under the new default are fixed one by one, each setting
  `blind_hold` explicitly or supplying a JD, NOT by flipping `_cfg()`'s default (tst-003).

**Config**
- `CvConfig().blind_hold is True`, as a named row in `tests/test_sluice_neutral_defaults.py`
  beside `style_hold`/`term_check` (inv-001, rev-005, tst-003). Witness: flip the default.
- `blind_hold` joins the quoted-false and real-boolean parametrisations in
  `tests/test_cv_config.py`.
- `docs/CONFIGURATION.md` gains a row (`tests/test_config_example.py::test_every_config_field_has_a_row_in_its_own_reference_section`
  goes red without it). The example's key binds to a real field
  (`test_every_example_key_binds_to_a_real_config_field`); nothing checks the reverse
  direction, so the example entry is a task item, not a guarded one.

**triage**
- A lead with a section and no url is judged, not `unjudgeable`.
- Two notes whose `cache_key` is ASSERTED equal first (tst-007), with different sections, each
  get their own JD. Witness: group section notes by key, row red.
- A section note whose build raises lands as `_FetchFailed` at its own index; the run
  continues.
- A too-short section is `unjudgeable` with the note-section reason.

**Create path**
- `created` with a JD: the section is appended and `jdsection.read` returns it;
  `job_description_written` True.
- Each of the other five outcomes: note byte-identical, flag False (inv-006).
- Append raising `VaultConflict`: outcome `created`, flag False.
- `leads add --jd-file -` reads stdin; `--jd-file PATH` reads a file; the not-written message
  prints on `updated`.
- MCP: `create_lead`'s pinned input set gains `job_description`; the result carries the flag.

**Reporting**
- `cmd_cv_run` prints `jd=` on each line, the reworded count line, and the note-JD count line
  (no test pins either wording today, tst-004).
- `_print_signoff_claims` prints the blind group with both routes and does not count it as an
  unsupported claim.
- MCP `cv_signoff` returns `blind` separately and excludes it from `claims`.

## Docs

- `docs/CONFIGURATION.md`: `cv.blind_hold`.
- `docs/USAGE.md`: `leads add --jd-file`, the section convention (synthetic example), the
  `jd=` field, and the reworded line that replaces "when any dossier fetch failed and
  composition proceeded blind" (rev-009).
- `docs/ARCHITECTURE.md`: the dossier's supplied arm and `core/jdsection.py`; its "a thin JD
  costs only quality" reasoning extended to say the CV is held (arc-005).
- `docs/TROUBLESHOOTING.md`: a held blind CV, both routes, and the `--discard` step.
- `sluice.yaml.example`: the key.
- `.rulesync/rules/CLAUDE.md` (regenerate after): "The advisory audit and the holds"
  (`blind_hold` beside `style_hold`); the `min_jd_chars` paragraph (a supplied JD obeys it);
  the `leads add` paragraph (`--jd-file`, written through `append_body_section`). Each claim
  lands in the task that makes it true.
- Code comments that state the old wording: `cli.py`'s count-line comment, the
  `_prefetch_dossiers` docstring, and the comments in `tests/test_cv_engine.py` and
  `tests/test_app_operations.py` that quote "composed blind".

## Out of scope

- Making `url` optional on `leads add` / `create_lead`.
- A command that writes a JD section into an EXISTING note.
- Refusing a blind compose before spend (the rejected alternative).
- Holding a CV composed from a model-written JD (ruled report-only).
