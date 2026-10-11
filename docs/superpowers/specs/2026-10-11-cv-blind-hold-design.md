# Blind CV hold and a lead-supplied job description (#369) — design

Approved in brainstorming on 2026-10-11. Rulings are recorded under Decisions.

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

1. A blind compose never gets a `tailored_cv` pointer without an explicit sign-off, at
   shipped defaults.
2. A lead whose note carries a `## Job description` section is judged by triage and composed
   by cv from that text, with no fetch.
3. `job-sluice leads add` and MCP `create_lead` can write that section when they create the
   lead.

## Decisions (owner, 2026-10-11)

| Question | Decision |
|---|---|
| Blind compose | HOLD for sign-off (not refuse-unless-`--allow-blind`). The CV is composed and rendered; `tailored_cv` is withheld until `job-sluice cv signoff` |
| Hold key | Its own key, `cv.blind_hold`, default `true`. Not `require_signoff` (fabrication) and not `style_hold` (wording) |
| Where the note's JD is read | The dossier layer, so triage and cv both read it. The section wins over a fetch and is never cached |
| Create path | In this PR: `leads add --jd-file` and `create_lead(job_description=...)` write the section on create |

Decided while writing the spec, not escalated:

| Question | Decision | Why |
|---|---|---|
| Hold reason text | One of two fixed phrases, never the exception text | `needs_signoff` is frontmatter in the user's note; an exception message can carry a url or arbitrary page text. The exception is already logged |
| Exit code of a blind hold | 0, the same as every `needs-signoff` | The issue's "exit non-zero" was its fallback ("at minimum"); a hold is the stronger response and is already visible per result |
| `--url` on `leads add` / `create_lead` | Stays required | Making it optional changes apply eligibility (`apply/select.eligibility` requires http(s)) and is a separate decision |
| Release type | `feat(cv)`, no `!` | `CHANGELOG.md`'s classes: a new default changing what an existing install does is `Changed` with a migration note, edited into the release PR. Nothing in a user's config stops parsing |

## 1. The blind hold

### Config

`cv/config.py::CvConfig` gains:

```python
blind_hold: bool = True
```

With a comment in the style of its neighbours: a separate key because `require_signoff` was
chosen for fabrication and `style_hold` for wording, and a blind CV is neither. A user who
turns off the fabrication hold has not said anything about blind CVs, and the reverse. On by
default because the failure it prevents is invisible at the moment it matters (the issue's
argument), the same reasoning `require_signoff` ships `True` under.

`load_cv_config`'s bool guard is keyed on the default's type, so a quoted `"false"` is refused
with no new code. `sluice.yaml.example` and `docs/CONFIGURATION.md` gain the key.

### Engine (`cv/engine.py::_run_one`)

`dossier_failed` is already computed from both producers (the fetch raised; `jd_arrived` is
False). It gains a sibling local, `blind_reason`, set at the same two sites to one of:

- `"the job description fetch failed"` (the `except` arm)
- `"no job description arrived"` (the `jd_arrived` arm, which also covers a lead with no url
  and no section)

In the blockers assembly, beside `unaudited`:

```python
blind = [f"blind\t{blind_reason}"] if dossier_failed and cvcfg.blind_hold else []
blockers = (
    (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
    + blind + style_blockers)
```

Everything after that is the existing path: `hold_for_signoff` with `pending_cv` and
`needs_signoff`, `needs-signoff` result, `skipped-has-cv` when a CV appeared meanwhile.
`status` stays `shortlist`, so never-regress is untouched.

Not changed, stated so nobody adds it:

- Not a gate finding. The fabrication gate checks the selection against the bundle, which is
  fine; `violations=0` stays true.
- No hold when nothing is served (`--no-serve`, or no `served_dir`), the same as every hold
  today: there is no pointer to withhold.
- A dry run reports `dossier_failed` as now and holds nothing.

### CLI and MCP

- `cli.py::_print_signoff_claims`: a `blind\t` entry gets its own group, printed first after
  framing: `cv signoff: <slug> was composed WITHOUT a job description (<reason>) -- it is not
  tailored to this role.` Not counted as an unsupported claim. The docstring's tag roster
  gains it.
- `cli.py::cmd_cv_run`'s trailing count line is reworded from `composed blind (dossier fetch
  failed)` to `composed without a job description`, since a lead with no url never attempted a
  fetch. Its comment is updated to say the hold, not this line, is now the control.
- `mcpserver.py`: `needs_signoff`'s description already treats every non-framing entry as
  derived content; no change beyond any prose that enumerates the tags. To be confirmed by
  grep at implementation time, not assumed.

## 2. Reading the job description from the note

### The section

A lead note body may carry:

```markdown
## Job description

<the posting's text, any markdown, ### subsections allowed>
```

`core/dossier.py::note_jd(body: str) -> str` is the one reader:

- The heading line is `## Job description`, matched case-insensitively, trailing whitespace
  ignored. Exactly level 2.
- The section runs to the next heading of level 1 or 2 (a line starting `# ` or `## `), or end
  of body. A `###` heading inside it is content.
- Returns the stripped text, `""` when there is no section or it is empty.
- A second `## Job description` section: the first is used and the caller logs a warning
  naming the note. Joining them would guess at intent; refusing would make a lead unusable
  over a formatting slip.
- Fenced code is not parsed specially. A `## ` line inside a fence ends the section. Stated
  as a residual; the create path (§3) never writes one.

It lives in `core/dossier.py` because it decides what the pipeline calls the job description,
the same question `jd_text` answers for a fetched dossier.

### The dossier layer

`DossierCache.get_or_build(lead, *, supplied_jd: str = "")`:

- `supplied_jd` non-empty: return a dossier built from the lead with `jd = {"markdown":
  supplied_jd}`, `glassdoor = {}`, `page_title = ""`, `structured_data = ""`, and the same
  `role_type_observed` derivation from that text. **No fetch, no cache read, no cache write**:
  an edit to the section takes effect on the next run, and a stale cached fetch can never
  shadow it.
- The returned dossier is judged by the same `jd_arrived` predicate, so `min_jd_chars`
  applies to a supplied JD exactly as to a fetched one.
- `supplied_jd` empty: today's behaviour, byte for byte.

A keyword parameter rather than reading `body` inside the cache, because the cache is handed
frontmatter dicts by three callers and should not learn what a note is.

### Callers

- `cv/engine.py::_run_one`: `dossier_cache.get_or_build(fm, supplied_jd=note_jd(note.body))`.
  A note with a section therefore has `dossier_failed` False and is not held.
- `triage/engine.py::_prefetch_dossiers`: a note whose body yields a section is built directly
  (no fetcher call, so no pool slot) and is NOT grouped by `cache_key`, since two notes
  sharing a key may carry different sections. Notes without one go through the existing
  grouped pool unchanged. Its result then flows through the existing `jd_arrived` /
  `unjudgeable` branch unchanged.
- `triage/resolve.py`: unchanged. Company resolution reads `page_title` and
  `structured_data`, which a supplied JD does not have, and it already abstains without a url.

The supplied JD licenses nothing at the fabrication gate: the gate reads only verified
experience entries (`cv/validate.py::entry_facts`). It reaches the composer and the judge
exactly where a fetched JD would, and is treated as the same untrusted page-class text.

## 3. Writing the section on create

### Store side

`core/leads.py::Lead` gains `job_description: str = ""`. No scraper sets it.

`core/vault.py::Vault._render_new` appends, when it is non-empty:

```markdown

## Job description

<text>
```

The text is body, not frontmatter, so the `_safe_or_blank` frontmatter guards do not apply.
Before writing, any line of the text that is a level-1 or level-2 heading is demoted one level
(`# X` to `## X` would still close the section, so both become `### X`), so a pasted
`## Responsibilities` cannot end the section early and make the rest of the posting invisible
to `note_jd`. That changes heading levels only; nothing is refused. A `## Job description`
line inside the text is demoted like any other, so the written note has exactly one section.

Written only on the `created` arm, because `_render_new` is only reached there. On
`updated`/`merged` the existing note is not rewritten (never-clobber); no new write path is
added, so no new CodeQL sink.

### Facade, CLI, MCP

- `Sluice.create_lead(..., job_description: str = "")` threads it into the `Lead`.
- `leads add --jd-file PATH|-` reads the text (`-` is stdin), matching the evidence commands'
  `--body-file`. When the outcome is `updated` or `merged`, the command prints that the note
  already existed and the job description was NOT written, and to add the section by hand.
  The exit code follows today's rules.
- MCP `create_lead` gains `job_description: str = ""` with the same report in its result
  (`job_description_written: bool`).

## Error handling

| Case | Behaviour |
|---|---|
| Fetch raises, no section | `dossier_failed`; composed; held with `blind\tthe job description fetch failed` |
| Fetch returns no JD / no url, no section | `dossier_failed`; composed; held with `blind\tno job description arrived` |
| Section present | No fetch; not blind; not held for blindness |
| Section present but shorter than `min_jd_chars` | Not arrived: triage stamps `unjudgeable`; cv composes and holds as blind |
| Two sections | First used; warning logged naming the note |
| `blind_hold: false` | Today's behaviour: served, count line printed |
| Nothing served | No hold, as every hold today |
| `leads add --jd-file` on an existing lead | Note untouched; told the JD was not written |

## Testing

Every row asserts behaviour; each new guard gets a mutation witness (delete the line it
depends on, watch the row go red).

- **Engine:** a blind lead with `blind_hold` on is `needs-signoff` with a `blind\t` entry and no
  `tailored_cv`; with it off, `rendered` with `tailored_cv`. Crossed with `require_signoff` on
  and off, so neither key can stand in for the other. Both producers (raise; no JD) each give
  their own reason. A lead with a section is `rendered`, not held, and the fetcher is never
  called.
- **`note_jd`:** no section; empty section; case variants; ends at `#` and `##` but not `###`;
  end of body; two sections.
- **Dossier:** `supplied_jd` never calls the fetcher and writes no cache file even when a fresh
  cached entry exists for the same key; `jd_arrived` applies `min_jd_chars` to it.
- **Triage:** a lead with a section and no url is judged, not `unjudgeable`; two notes sharing a
  `cache_key` with different sections each get their own JD.
- **Create path:** `created` writes the section with demoted headings and `note_jd` reads back
  the original text modulo demotion; `updated` leaves the note byte-identical; `leads add
  --jd-file -` reads stdin; the CLI and MCP report `job_description_written` truthfully.
- **Sign-off:** `_print_signoff_claims` prints the blind group and does not count it as an
  unsupported claim.

## Docs

`docs/CONFIGURATION.md` (`cv.blind_hold`), `docs/USAGE.md` (`leads add --jd-file`, the section
convention), `docs/ARCHITECTURE.md` (the dossier's supplied-JD arm, the hold),
`docs/TROUBLESHOOTING.md` (a held blind CV and how to release it or add a JD),
`sluice.yaml.example`, and `.rulesync/rules/CLAUDE.md`'s "The advisory audit and the holds"
paragraph (regenerate after).

## Out of scope

- Making `url` optional on `leads add` / `create_lead`.
- A command that writes a JD section into an EXISTING note.
- Refusing a blind compose before spend (the rejected alternative).
