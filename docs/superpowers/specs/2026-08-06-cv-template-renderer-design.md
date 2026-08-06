# The CV renderer takes a user's TEMPLATE, because sluice must not ship a CV design

Status: design, approved 2026-08-06. Supersedes nothing; closes the render half of the
"fresh install cannot produce a PDF" gap.

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

Citations are stripped BEFORE parsing (reusing `cv/render.py:strip_citations`), so the
`[NO1]` tokens the gate checked cannot reach a PDF whatever a template does.

### 2. `sluice/renderers/template.py` — new seam implementation

Registered as `template`. Loads the template, renders it with Jinja2, writes the PDF with
WeasyPrint. Both imported LAZILY inside the factory: `sluice/` is standard-library only,
and the registry must populate without the extra installed.

### 3. The shipped template, and one worked example

- `sluice/templates/cv_plain.html.j2` — packaged (a package resource, so it exists on any
  install including a wheel). Deliberately plain: legible, single-column, correct, and
  expressing no design taste. This is the CV equivalent of the judge's shipped fallback
  criteria (`sluice/core/criteria.py`), which "states that no criteria are configured and
  declines to invent any -- it must never substitute an opinion".
- `docs/cv-template-example.html.j2` — clearly labelled illustrative. Shows the loops, the
  data shape, and real CSS, so someone converting a design from a GUI CV builder has
  something to read rather than only a contract to interpret.

## Config

| Key | Default | Meaning |
| --- | --- | --- |
| `cv.renderer` | `"template"` (was `"script"`) | which seam entry |
| `cv.template` | `""` | path to the template; blank resolves to the packaged default |

The blank default follows the pattern `core/paths.py` already establishes: a non-empty
default would be truthy, short-circuit the resolution chain, and make the packaged
template unreachable while nothing went red.

## Migration — three breaking cases, all LOUD

1. **`render_script` set with no explicit `renderer`.** This is the case the old guard test
   was protecting, and the only one that could silently change an operator's output: they
   were relying on the `script` default. `load_cv_config` raises and names the fix
   (`cv.renderer: script`). Not auto-detected and not quietly reinterpreted — an implicit
   coupling between two keys is its own quiet-wrong-default.
2. **`cv.renderer: weasyprint`.** The `<pre>`-dumping renderer is REMOVED. It is opt-in,
   days old, and strictly worse than `template` with the plain default. Selecting it
   raises, naming `template` as the replacement.
3. **The `cv.renderer` default itself changes.** A config-breaking change by this repo's
   own CHANGELOG rule, so it gets an explicit migration note in the release PR.

## Failure modes

| Condition | Behaviour |
| --- | --- |
| Template file missing / not a file | `RenderError` at CONSTRUCTION, naming the path |
| jinja2 or weasyprint absent | `RenderError` at construction, naming `pip install 'sluice[render]'` |
| Role line unparseable | `CvParseError` at render -> engine's per-lead isolation -> `error` for that lead, run continues |
| Template renders but writes no PDF | existing exit-0-wrote-nothing check |

**Why an unparseable line REFUSES rather than degrading.** The CV has already passed the
fabrication gate, so its facts are sound and only its shape is in doubt. But the artefact
goes to an employer under the user's name, and a half-parsed CV could put a date where a
title belongs or drop a role entirely — wrong in a way the user would not see until after
sending. Losing one CV to a retry is cheap; sending a mangled one is not. Rendering it
with a WARNING was considered and rejected: this project has repeatedly found that a
warning inside a batch run is functionally a silent failure.

## Dependencies

The `render` extra gains `jinja2` alongside `weasyprint`. No change to `dependencies` —
`sluice/` stays standard-library-only, and both imports are inside the factory.

## Testing

- **`parse_cv` (pure, the bulk of it):** each section; sections absent; multiple roles;
  bullets with and without citations; an unparseable role line RAISES; citations stripped;
  a document that would break the contract fails rather than half-parsing.
- **Construction:** missing template file, absent jinja2, absent weasyprint — each a named
  `RenderError` at construction, not at call time.
- **Rendering without the extra installed:** inject fake HTML/CSS classes, following the
  pattern `tests/test_renderers.py` already uses. That test file records why: an earlier
  version opened with `pytest.importorskip("weasyprint")`, so CI (which installs only the
  `test` extra) SKIPPED the one test pinning that citations are stripped.
- **The packaged default template** must exist as a package resource and must render the
  parsed shape — a guard against it being lost from packaging, which would break every
  fresh install while the source tree looked fine.
- **Migration:** each of the three breaking cases raises with a message naming the fix, and
  the migration the message asks for actually works.

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
