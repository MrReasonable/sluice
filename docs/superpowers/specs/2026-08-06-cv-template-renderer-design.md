# The CV renderer takes a user's TEMPLATE, because sluice must not ship a CV design

Status: design, approved 2026-08-06; revised after a five-reviewer plan review.

It closes the gap for a `pip install 'sluice[render]'` — NOT for a bare `pip install
sluice`, which still cannot produce a PDF because WeasyPrint's system libraries
(cairo, pango) are not a Python dependency and cannot be made one. The first draft claimed
to "close the render half of the gap" without that qualifier. What changes is that the
failure is now loud and diagnosable at construction instead of arriving after the LLM
spend.

## Problem

A fresh clone composes a CV, passes the fabrication gate, and only then dies — after the
LLM spend. Re-verified on 2026-08-06, not recalled:

```
cv.renderer      = "script"                       CvConfig default
cv.render_script = "./scripts/cv_render_v2.py"    NOT IN THIS REPOSITORY
weasyprint                                        not installed
ScriptRenderer(CvConfig())                        -> RenderError at construction
```

`sluice/renderers/script.py`'s own docstring already admits the first two lines. The
bundled `weasyprint` renderer was added as the answer and deliberately left non-default,
on the reasoning that switching would "silently change the layout of an operator's CV".

That reasoning is sound and is handled below rather than dismissed. But it protects a
hypothetical: no operator can have a working `script` default, because the script the
default names has never existed.

## The deeper problem, which is why this is not a one-line default change

The bundled renderer wraps the whole CV in `<pre>` and applies a plain stylesheet. It
ignores the structure entirely — even though `cv/compose.py`'s `_RULES` says in terms:

> Output the CV in EXACTLY this format (what cv_render_v2.py parses)

The composer emits a parseable document. Nothing parses it.

Fixing that by choosing a nicer layout would be the wrong move, and the reason is the
project's own founding constraint. Sluice ships no opinion about which jobs are good;
shipping a CV design is the same category of mistake — an opinion about how someone
else's CV should look, baked into a tool meant to be reusable. The existing `script`
renderer is already the right SEAM for this (bring your own renderer, sluice supplies
only content). Its problem is the entry cost: you must write Python.

**So: sluice owns the content and the contract. The user owns the design.**

## The change

Three units, each with one job.

### 0. The grammar, stated — and enforced where it can still be recovered

The composer is ASKED for `MM/YYYY-MM/YYYY | LOCATION | Role` but nothing enforces it:
`cv/validate.py` pins the section headers and the citations, and stops there. So a CV that
renders today can fail to parse tomorrow, and the first draft of this spec made that fatal
— `CvParseError` after the LLM spend, with no retry, because the engine's single retry is
keyed on GATE violations and closes before render.

That is worse than the status quo for any CV whose role line wobbles, and it re-opens the
exact problem this design exists to close. Two changes fix it:

**The grammar is stated here and becomes the parser's contract**, so an implementer is not
inferring it from a prompt:

```
<contact line(s)>            one or more, before the name
<NAME>                       the name heading
PROFILE
<prose>
WORK EXPERIENCE
<Company>                    a line that is not a bullet and not a section header
MM/YYYY-MM/YYYY | LOCATION | Role      <- the meta line; `present` is legal for the end date
- bullet
CERTIFICATES                 optional
- cert
EDUCATION                    optional
- entry
```

**A parse failure feeds the EXISTING retry rather than killing the lead.** `cv/engine.py`
already composes up to twice, appending violations to the second prompt. `parse_cv` runs
INSIDE that loop, immediately after `validate`, and a `CvParseError` is appended to
`gate_msgs` in the same shape a gate violation is. The model is being asked to fix its own
formatting, which is the thing an LLM is reliably good at.

If the second attempt still will not parse, the lead is skipped with an `error` — the
existing per-lead isolation, verified present at `engine.py:250-264`. That is the same
outcome as a lead that cannot clear the gate, and it costs no extra backend call beyond the
retry that already existed.

This supersedes the first draft's "refuse, and rely on per-lead isolation". The refusal was
right about the harm (a half-parsed CV goes to an employer under the user's name) and wrong
about the remedy (throwing away a CV that a retry would fix).

### 1. `sluice/cv/parse.py` — new, PURE

```
parse_cv(text: str) -> CvDocument        # raises CvParseError
```

Turns the composed CV into structured data. No I/O, no rendering, no validation of
FACTS — the fabrication gate has already run and re-checking here would be a second,
weaker gate and a way around the real one.

All the risk in this design lives in this function, which is why it is pure: every case
is a table-driven unit test with no fixtures, no subprocess, no PDF.

```
CvDocument
  name          str
  contact       str
  profile       str
  work          list[Role]
  certificates  list[str]
  education     list[str]

Role
  company   str
  dates     str
  location  str
  title     str
  bullets   list[str]
```

**This shape is the PUBLIC CONTRACT** a template author writes against. It is documented
and pinned by tests; changing a field name is a breaking change for every user template
and gets a migration note.

Citations are stripped INSIDE `parse_cv`, not by its caller. Two reasons, and the second is
a correctness bug in the first draft: leaving it to the caller makes it an obligation each
renderer must remember (`strip_citations` is already duplicated per-renderer), and
`_CITE_RE`'s leading `\s*` eats NEWLINES — so strip-then-parse mutates the very line
structure the parser depends on. Strip per-field, after the line structure has been read.

The `[NO1]` tokens the gate checked therefore cannot reach a PDF whatever a template does.

### 2. `sluice/renderers/template.py` — new seam implementation

Registered as `template`. Loads the template, renders it with Jinja2, writes the PDF with
WeasyPrint. Both imported LAZILY inside the factory: `sluice/` is standard-library only,
and the registry must populate without the extra installed.

**`autoescape=True`, unconditionally, and it is a CONTRACT rather than a default.** Do NOT
use `select_autoescape()`. Measured on jinja2 3.x:

```
select_autoescape()('cv_plain.html.j2')  ->  False      # the shipped template's own name
select_autoescape()('cv_plain.html')     ->  True
```

It suffix-matches `.html`/`.htm`/`.xml`, and the conventional `.j2` suffix defeats it. With
autoescape off, a gate-verified bullet reading `Cut p99 latency to <200ms` renders as an
unknown HTML element and **WeasyPrint drops the text** — measured, not reasoned. The PDF
then differs from what `validate()` approved, and nobody sees it until after the CV is sent.
That is the same harm this design cites to justify handling a bad parse, so it cannot be
left to a filename convention.

The escaping today lives in `renderers/weasyprint.py:_escape`, which section 3 deletes.
Moving it into this renderer's contract is what stops the deletion taking the knowledge
with it.

### 3. The shipped template, and one worked example

- `sluice/templates/cv_plain.html.j2` — packaged (see Packaging below; it must reach a
  WHEEL, not merely the source tree). Legible, single-column, correct.

  **It is NOT "neutral", and the first draft's claim that it is the CV equivalent of the
  judge's fallback criteria is wrong.** That fallback abstains IN ITS CONTENT — it says no
  criteria are configured and tells the judge to invent none. A template cannot abstain: it
  must lay something out, so a shipped template is a shipped design however plain it is.

  The property that IS achievable, and is mechanically checkable, is narrower:

  > **The shipped template contributes no CONTENT of its own.** Every literal text node in
  > it is either a `CvDocument` field reference or a section heading `cv/compose.py:_RULES`
  > already emits.

  Guarded by enumerating the template's text nodes against a heading set DERIVED from
  `_RULES` rather than hand-listed, so the guard cannot drift from what the composer emits.
  Layout and typography remain a shipped opinion; the spec says so rather than claiming
  otherwise.

- `docs/cv-template-example.html.j2` — illustrative, and constrained by more than its label.
  A worked example asked for "real CSS" is exactly the pressure that produces a filled-in
  specimen with a plausible name, employer and location — and `docs/` is the same public
  repo, while every existing neutrality guard is scoped to `sluice/` and `tests/`.

  **It carries ZERO sample values: expressions and CSS only.** Where a value is unavoidable
  for the CSS to make sense, it uses the `Example …`/`example.invalid` family this repo
  already uses in fixtures. The neutrality sweep extends to `docs/**/*.j2`.

## Packaging — the packaged template does NOT ship today, and the obvious guard hides it

`pyproject.toml` has only `[tool.setuptools.packages.find] include = ["sluice*"]`. That
selects PACKAGES, not data files: there is no `package-data`, no `include-package-data`, no
`MANIFEST.in`, and `sluice/` currently ships zero non-`.py` files. So as things stand the
template reaches a source checkout and nothing else.

**And the guard the first draft proposed — "the default template must exist as a package
resource" — cannot fail where CI runs it.** CI installs editable, so
`importlib.resources.files` reads the checkout and returns the file whatever the packaging
says. The guard would stay green while every `pip install sluice` shipped a default renderer
with no template: the same shape as the bug this whole design exists to fix, reintroduced by
its own fix. Three reviewers reached this independently.

Required, and all three parts are load-bearing:

- `[tool.setuptools.package-data] sluice = ["templates/*.html.j2"]`
- `sluice/templates/__init__.py` — `packages.find` (not `find_namespace`) will not descend
  into a directory without one
- a guard that asserts against a **built artefact** (or derives the glob from the pyproject
  table), and is falsified by deleting the `package-data` entry

Not verified locally: no modern setuptools was available offline to build a wheel and
witness the failure. The implementation must do that, and treat "I could not build it" as a
blocker rather than a caveat.

## Config

| Key | Default | Meaning |
| --- | --- | --- |
| `cv.renderer` | `"template"` (was `"script"`) | which seam entry |
| `cv.template` | `""` | path to the template; blank resolves to the packaged default |

Both go in `sluice.yaml.example` as well as the dataclass (hard rule 13 — the first draft
named only the dataclass, and `sluice.yaml.example:143-153` documents all three things this
change invalidates).

The blank default is the same SHAPE as `core/paths.py`'s blank-means-derive — a non-empty
default is truthy, short-circuits the chain, and makes the packaged template unreachable
while nothing goes red. It does NOT route through `paths.resolve()`: like `render_script`,
`cv.template` names a workspace artefact the user is standing in, one of the deliberate
cwd-relative exceptions rather than per-system state.

`cv.template` is a `str`, so it is invisible to `tests/test_sluice_neutral_defaults.py`'s
list-keyed sweep. It needs its own named guard, exactly as `lead_layout` does for the same
reason.

## Migration — three breaking cases, all LOUD

1. **`render_script` set with no explicit `renderer`.** This is the case the old guard test
   was protecting, and the only one that could silently change an operator's output: they
   were relying on the `script` default. `load_cv_config` raises and names the fix
   (`cv.renderer: script`). Not auto-detected and not quietly reinterpreted — an implicit
   coupling between two keys is its own quiet-wrong-default.
2. **`cv.renderer: weasyprint`.** The `<pre>`-dumping renderer is REMOVED — it is opt-in and
   strictly worse than `template` with the shipped default. (The first draft justified this
   with "days old". That is false: it landed 2026-07-14, three weeks before this spec. The
   argument stands on the replacement being better, not on the code being new.)

   **Removing it alone does NOT produce the promised message.** If the entry simply
   disappears from the registry, `plugins.get`'s unknown-name error lists the VALID names
   and would never mention `template`, so "raises, naming `template` as the replacement" is
   an empty promise. The migration message needs somewhere deliberate to live — a retired-
   name branch in the renderer resolution, in the shape `refuse_retired_dossier_dir` already
   uses for a retired config key.

   **Six sites tell a user to set `cv.renderer: weasyprint` and all six go stale.** Found by
   grep, not from memory:

   | Site | What it says |
   | --- | --- |
   | `sluice/renderers/script.py:32` | the construction error's "or switch to the bundled one" — pinned by `tests/test_renderers.py:33` |
   | `sluice/cv/config.py:56` | the comment beside the `renderer` field |
   | `sluice/onboard/questions.py:207` | `sluice init`'s hint (its CHOICES self-heal from the registry; the hint does not) |
   | `sluice.yaml.example:149-151` | the documented renderer options |
   | `docs/ARCHITECTURE.md:832` | the renderer seam's two production impls |
   | `.rulesync/rules/CLAUDE.md:357-358, 405` | the stdlib-only exception list, naming a file this spec deletes |

   The last is the worst: it is the canonical rules file every future agent reads, and it
   would assert an exception for a module that no longer exists while omitting `jinja2`.
   The first draft's *Out of scope* section explicitly excluded `script.py`'s message, which
   was wrong — that is the one a user hits first.
3. **The `cv.renderer` default itself changes.** A config-breaking change by this repo's
   own CHANGELOG rule, so it gets an explicit migration note in the release PR.

## Failure modes

| Condition | Behaviour |
| --- | --- |
| Template file missing / not a file | `RenderError` at CONSTRUCTION, naming the path |
| jinja2 or weasyprint absent | `RenderError` at construction, naming `pip install 'sluice[render]'` |
| Role line unparseable, 1st attempt | `CvParseError` appended to `gate_msgs`; the EXISTING retry re-composes with it |
| Still unparseable after the retry | lead skipped with `error` via per-lead isolation (`engine.py:250-264`); run continues |
| Section present that `CvDocument` does not model | refuses — user content must not vanish silently from a PDF sent under their name |
| Template renders but writes no PDF | this renderer's OWN check. The first draft cited an "existing" one; that belongs to `cv/render.py`'s subprocess path and does not apply here |

**Why an unparseable line REFUSES rather than degrading.** The CV has already passed the
fabrication gate, so its facts are sound and only its shape is in doubt. But the artefact
goes to an employer under the user's name, and a half-parsed CV could put a date where a
title belongs or drop a role entirely — wrong in a way the user would not see until after
sending. Losing one CV to a retry is cheap; sending a mangled one is not. Rendering it
with a WARNING was considered and rejected: this project has repeatedly found that a
warning inside a batch run is functionally a silent failure.

## Dependencies

`jinja2` joins BOTH the `render` extra (it is needed at runtime) and the `test` extra (so CI
can exercise the real engine — see Testing). No change to `dependencies`: `sluice/` stays
standard-library-only and both imports are inside the factory, which is where CLAUDE.md
draws the line ("whether a user's install can end up executing it").

## Documentation this change invalidates

Every site below asserts something this change makes false. Named here so the implementer
updates them as part of the work rather than leaving a trail of stale claims:

- `.rulesync/rules/CLAUDE.md:357-358` — the stdlib-only exception list names
  `renderers/weasyprint.py`, which this deletes, and omits `jinja2`. **The canonical rules
  file: every future agent reads it, so a false claim here propagates.** Regenerate with
  `npm run rulesync` afterwards.
- `.rulesync/rules/CLAUDE.md:405` — the renderer seam's "two self-registering production
  impls".
- `docs/ARCHITECTURE.md:830-835` — same seam description.
- `sluice.yaml.example:143-153` — the renderer options, plus the missing `cv.template`.
- `README.md` — the render prerequisites, including the WeasyPrint system libraries and the
  macOS loader path.
- `CHANGELOG.md` — a default change is config-breaking by this repo's own rule, so it needs
  a migration note in the release PR.

## Testing

**`jinja2` moves into the `test` extra as well as `render`.** Without that this spec
reproduces the exact trap it cites. `tests/test_renderers.py` records that a
`pytest.importorskip("weasyprint")` once made CI silently SKIP the only test pinning that
citations are stripped — and CI installs `[test]`, never `[render]`. The shipped-template
test needs the REAL engine (a fake cannot prove a template renders), so under the first
draft it could only be written as `importorskip("jinja2")` and would skip in CI, while
"absent jinja2 raises at construction" would pass VACUOUSLY there. jinja2 is pure Python
with no system libraries, so it belongs in `test` on the same footing as `faker`.
WeasyPrint stays out — it needs cairo/pango — and is covered by injected fakes as today.

A guard forbids `importorskip` in the new test module: the trap is now documented twice and
has still recurred once.

| # | Test | Fails first because |
| --- | --- | --- |
| 1 | `test_parse_reads_every_section` | `cv/parse.py` does not exist |
| 2 | `test_parse_reads_multiple_roles_and_their_bullets` | ditto |
| 3 | `test_parse_raises_on_an_unparseable_meta_line` | ditto |
| 4 | `test_parse_strips_citations_from_bullets` | ditto |
| 5 | `test_parse_preserves_line_structure_while_stripping` | the `\s*`-eats-newlines bug |
| 6 | `test_parse_refuses_a_section_it_does_not_model` | silent drop of user content |
| 7 | `test_parse_does_not_silently_misassign_fields` | see below |
| 8 | `test_template_renderer_escapes_html_in_a_bullet` | `autoescape=True` not set |
| 9 | `test_missing_template_file_raises_at_construction` | renderer does not exist |
| 10 | `test_absent_jinja2_raises_naming_the_extra` | ditto |
| 11 | `test_absent_weasyprint_raises_naming_the_extra` | ditto |
| 12 | `test_the_shipped_template_renders_a_parsed_document` | template does not exist |
| 13 | `test_the_shipped_template_contributes_no_content` | the neutrality property above |
| 14 | `test_the_shipped_template_is_in_the_built_wheel` | `package-data` not declared |
| 15 | `test_a_parse_failure_feeds_the_retry_not_the_bin` | parse is not wired into the loop |
| 16 | `test_selecting_the_retired_weasyprint_name_names_template` | no retired-name branch |
| 17 | `test_render_script_without_an_explicit_renderer_is_refused` | migration case 1 |
| 18 | `test_cv_template_default_is_blank` | the named `str` guard |

**#7 is the one the first draft missed entirely.** It argued at length that a bad parse must
not degrade, because a mangled CV goes to an employer under the user's name — and then
specified no case where parsing SUCCEEDS WRONGLY. A date absorbed into `title`, a bullet
swallowed as a company, a meta line read as a heading: each raises nothing, produces a wrong
PDF, and is precisely the harm the refusal argument rests on. Table-driven, asserting the
whole `CvDocument`, not merely that it parsed.

**#14 must assert against a BUILT artefact.** Asserting `importlib.resources` finds the file
passes in CI's editable install whatever the packaging says — see Packaging above.

**#16 needs the retired-name branch to exist**; a bare registry removal produces an error
listing valid names and never mentioning `template`.

Fixtures stay synthetic and offline; `tests/conftest.py` blocks `socket.getaddrinfo`
session-wide. The existing seam-membership assertion
(`{"script", ...} <= set(Sluice.available("renderer"))`) is KEPT, with the `weasyprint`
entry replaced rather than the line deleted.

## Known limitations, stated rather than buried

- **A template author can defeat ATS parsing.** Two-column grids and tables produce PDFs
  whose text extracts in the wrong order, and the CV's destination is an ATS upload
  (`apply` stages it for a browser-assisted form fill). Sluice cannot prevent this and
  should not try. The shipped default is single-column and the example says so explicitly.
- **WeasyPrint still needs system libraries** (cairo, pango, gdk-pixbuf), and on macOS also
  a loader path — measured: the libraries were present via Homebrew and the import still
  failed until `DYLD_FALLBACK_LIBRARY_PATH` was set. This design does not remove that; it
  stops it being a silent failure. Documented in README, and `doctor` reporting it is a
  candidate follow-up.
- **The parse layer is backend-agnostic** by construction. `CvDocument` has nothing
  HTML-specific in it, so an fpdf2 renderer needing no system libraries could sit on the
  same parse layer later. It could not consume HTML templates, so it would be a separate
  seam entry rather than a swap.

## Out of scope

- PDF overlay (drawing onto a user's PDF as a background). Considered and rejected: it
  breaks silently when content overflows its box, and content length is exactly what
  varies between applications.
- Designing a good-looking CV. That is the user's job, which is the whole point.
- Touching the fabrication gate, the composer's `_RULES`, or the `script` renderer's
  behaviour. `script` stays as the full-control escape hatch.
