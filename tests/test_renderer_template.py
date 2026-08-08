"""The `template` renderer.

NO `pytest.importorskip` ANYWHERE UNDER `tests/`. jinja2 is in the `test` extra precisely
so these run in CI; an importorskip would silently skip them and read as green, which is
the trap tests/test_renderers.py records having been hit by once already -- and which
recurred a second time, in tests/test_cv_engine.py, while the guard below still read only
this one file. `test_no_test_module_uses_importorskip` now sweeps the whole tree.

WeasyPrint is NOT imported here -- it needs cairo/pango (and, on macOS, a
DYLD_FALLBACK_LIBRARY_PATH). It is injected as a fake, exactly as the renderer it
replaces was tested.
"""
import os

import pytest

from sluice.core.protocols import RenderError
from sluice.renderers.template import TemplateRenderer

CV = """\
Email: someone@example.invalid

EXAMPLE PERSON

PROFILE
Engineer with nine years building data pipelines.

WORK EXPERIENCE

Example Data Co
03/2021-present | EXAMPLECITY | Staff Engineer
- Cut p99 latency to <200ms [ED1]

CERTIFICATES
- Example Cloud Practitioner, 2022

EDUCATION
- Example University, 2010-2013 | BSc Computer Science
"""


class FakeHTML:
    """Captures the HTML the renderer hands WeasyPrint, and writes a stub PDF."""
    captured = {}

    def __init__(self, string=""):
        FakeHTML.captured["html"] = string

    def write_pdf(self, path):
        # No `stylesheets` kwarg: it was WeasyPrintRenderer's shape (a separate CSS
        # object it passed alongside the HTML), and that class is deleted. This
        # renderer's own write_pdf call passes only `path`, so a fake still accepting
        # `stylesheets` no longer pins the call shape the real code can actually make.
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 fake")


@pytest.fixture(autouse=True)
def _reset_fake_html_captured():
    """FakeHTML.captured is CLASS-level mutable state, shared across every test in this
    file. Every test today happens to call render() (which repopulates it) before
    reading it, so a stale-read is not reachable YET -- but that is a property of what
    today's test bodies happen to do, not a property this design guarantees. A future
    test that reads `captured` without rendering first would otherwise silently see a
    PREVIOUS test's html and pass on stale data instead of failing loudly. Reset before
    every test so that failure mode cannot occur no matter how this file grows."""
    FakeHTML.captured = {}


def _renderer(tmp_path, template_text=None):
    if template_text is None:
        return TemplateRenderer(None, html_module=FakeHTML)
    path = tmp_path / "user.html.j2"
    path.write_text(template_text, encoding="utf-8")
    return TemplateRenderer(str(path), html_module=FakeHTML)


def test_template_renderer_escapes_html_in_a_bullet(tmp_path):
    """autoescape=True is a CONTRACT, not a filename convention.

    `select_autoescape()` suffix-matches .html/.htm/.xml and returns False for the
    conventional .j2 suffix. With autoescape off, this gate-verified bullet renders as an
    unknown HTML element and WeasyPrint DROPS the text -- so the PDF differs from what
    validate() approved, and nobody sees it until after the CV is sent.
    """
    r = _renderer(tmp_path, "{{ document.work[0].bullets[0] }}")
    r.render(CV, str(tmp_path / "out"))
    html = FakeHTML.captured["html"]
    assert "&lt;200ms" in html, "the bullet was not escaped; WeasyPrint will drop it"
    assert "<200ms" not in html


def test_a_misspelled_field_raises_instead_of_rendering_blank(tmp_path):
    """StrictUndefined (see TemplateRenderer.__init__): Jinja2's default Undefined
    renders `{{ document.nmae }}` as an EMPTY STRING and raises only for a wholly
    undefined ROOT name. Measured pre-fix: this exact template constructed and
    rendered with no error at all, silently producing a PDF missing the candidate's
    name -- the same "silently differs from what validate() approved" harm the
    autoescape test above already exists to catch, just via a typo instead of an
    unescaped character. Match on the misspelled field name: Jinja2's own error text
    names it, and RenderError must carry that through, not swallow it.
    """
    r = _renderer(tmp_path, "{{ document.nmae }}")
    with pytest.raises(RenderError, match="nmae"):
        r.render(CV, str(tmp_path / "out"))


def test_a_misspelled_nested_field_raises_naming_the_template(tmp_path):
    """Same axis, one level deeper (`document.work[0].titel`) -- a template's field
    typos are not only at the top level -- and asserts the error names THIS renderer
    and the template path, not a bare jinja2 traceback with no renderer context.
    """
    path = tmp_path / "user.html.j2"
    path.write_text("{{ document.work[0].titel }}", encoding="utf-8")
    r = TemplateRenderer(str(path), html_module=FakeHTML)
    with pytest.raises(RenderError, match=r"template.*titel") as ei:
        r.render(CV, str(tmp_path / "out"))
    assert str(path) in str(ei.value)


def test_template_renderer_strips_citations_before_writing(tmp_path):
    """The [id] tokens must never reach an employer. parse_cv strips them, so no
    template can reintroduce them however it is written."""
    r = _renderer(tmp_path, "{{ document.work[0].bullets[0] }}")
    r.render(CV, str(tmp_path / "out"))
    assert "[ED1]" not in FakeHTML.captured["html"]
    assert "Cut p99 latency to" in FakeHTML.captured["html"]


def test_missing_template_file_raises_at_construction(tmp_path):
    """At CONSTRUCTION, not at call time -- the whole point of this feature is that a
    render failure stops arriving after the LLM spend."""
    with pytest.raises(RenderError, match="template"):
        TemplateRenderer(str(tmp_path / "nope.html.j2"), html_module=FakeHTML)


def test_a_template_directory_is_refused_at_construction(tmp_path):
    """os.path.exists() is True for a directory -- the same trap `script` already
    documents. isfile, not exists."""
    d = tmp_path / "adir.html.j2"
    d.mkdir()
    with pytest.raises(RenderError, match="template"):
        TemplateRenderer(str(d), html_module=FakeHTML)


def test_the_shipped_template_renders_a_parsed_document(tmp_path):
    """The REAL jinja2 engine against the REAL shipped template. A fake engine cannot
    prove a template renders."""
    r = _renderer(tmp_path)          # None -> the packaged default
    out = r.render(CV, str(tmp_path / "out"))
    assert out.endswith("CV.pdf") and os.path.exists(out)
    html = FakeHTML.captured["html"]
    for expected in ("EXAMPLE PERSON", "Example Data Co", "Staff Engineer",
                     "EXAMPLECITY", "Example Cloud Practitioner, 2022"):
        assert expected in html, f"{expected!r} missing from the rendered CV"


def test_a_blank_location_does_not_leave_a_dangling_separator(tmp_path):
    """`sluice/cv/parse.py` accepts a 2-field meta line with no location, setting
    `location=""` -- and both templates joined dates/location/title with unconditional
    `|` characters, so the rendered line read `03/2021-present |  | Staff Engineer`.
    `leftover_content` classifies a bare `" | "` as punctuation (see its own no-content
    guard), so nothing else in this file's guards would have caught the defect either.
    CodeRabbit's cloud review on PR #97 found this against the shipped template.
    """
    cv_no_location = CV.replace(
        "03/2021-present | EXAMPLECITY | Staff Engineer", "03/2021-present | Staff Engineer")
    assert "03/2021-present | Staff Engineer" in cv_no_location, "the replace no-opped"
    r = _renderer(tmp_path)
    r.render(cv_no_location, str(tmp_path / "out"))
    html = FakeHTML.captured["html"]
    assert "03/2021-present | Staff Engineer" in html
    assert " |  | " not in html, "a blank LOCATION left a dangling separator in the PDF"


def test_every_shipped_template_contributes_no_content():
    """A shipped template is NOT neutral -- a template must lay something out, so its
    layout is a shipped opinion. The property that IS achievable and mechanically
    checkable is narrower: it contributes no CONTENT of its own.

    ENUMERATES, rather than reading one hardcoded path. `pyproject.toml` ships the
    directory by GLOB (`templates/*.html.j2`), so a second template dropped in beside
    the first is packaged into every install automatically -- and was guarded by
    nothing, because this read exactly one filename. The docs sweep in
    tests/test_no_leaked_files.py already enumerated its side; the shipping side is the
    one where an unguarded file actually reaches a user.

    Strip and heading derivation are shared with that sweep (tests/template_content.py),
    because they used to be two copies and the same bug lived in both.
    """
    from tests.template_content import (composer_headings, leftover_content,
                                        packaged_templates)

    headings = composer_headings()
    assert headings, "derived no headings, so this guard would pass vacuously"

    templates = packaged_templates()
    assert templates, (
        "no packaged *.html.j2 found -- for a NEGATIVE guard an empty sweep reads "
        "exactly like a clean one, so the scope is asserted before the content is")

    for name, text in templates:
        leftover = leftover_content(text)
        assert leftover <= headings, (
            f"the shipped template {name} contributes content of its own: "
            f"{sorted(leftover - headings)}")


def test_the_shipped_template_sweep_covers_every_route_into_a_users_install():
    """SCOPE, against the packaging manifest rather than against itself.

    The sweep above inspects `*.html.j2` under `sluice.templates`. That is the only
    package-data route today, and the assertion is what makes "only" true rather than
    assumed: adding, say, `templates/*.css` or a second data directory to
    `[tool.setuptools.package-data]` would ship a file into every install that the sweep
    cannot see, and no test would say so. Derived from pyproject, so this reds on the
    change that creates the hole instead of on the leak that eventually follows.
    """
    from tests.template_content import packaged_data_patterns

    patterns = packaged_data_patterns()
    assert patterns, "read no package-data patterns, so this guard checked nothing"
    assert set(patterns) == {"templates/*.html.j2"}, (
        f"pyproject ships package data this neutrality sweep does not inspect: "
        f"{sorted(set(patterns) - {'templates/*.html.j2'})}. Extend "
        "tests/template_content.py::packaged_templates to cover it, then widen this "
        "assertion -- do not widen this assertion alone.")


@pytest.mark.parametrize("planted,why", [
    ("<p>seeking a remote-first role, no agencies</p>",
     "plain body text -- the case the guard already caught"),
    ("<style>.contact::after { content: \" -- seeking a remote-first role\"; }</style>",
     "CSS GENERATED content: text WeasyPrint really renders, invisible to a guard that "
     "deletes the style block wholesale"),
    ("<style>.contact::before{content:'no agencies please'}</style>",
     "...with single quotes and no spaces, since the harvest must not depend on "
     "formatting"),
    # EMPTY element, deliberately. An earlier version wrote `>x</p>` and passed for the
    # wrong reason: `x` survives tag-stripping on its own, so the row went red whether or
    # not the attribute was ever inspected. Measured -- scoping the harvest back to
    # <style> blocks left this row GREEN once the stray `x` was removed, which is what
    # makes it a witness rather than decoration.
    ("<p style=\"content: 'no agencies please'\"></p>",
     "...in a style ATTRIBUTE, which tag-stripping would otherwise erase"),
    # ── round 2: the guard was still denylisting SYNTAX SHAPES ────────────────────
    # Every row below was measured GREEN against the previous version of the harvest,
    # which knew about the `content` property and nothing else, and deleted Jinja code
    # wholesale on the premise that its output is always the gate-approved CvDocument.
    ('{{ "seeking a remote-first Rust role, no agencies" }}',
     "a Jinja STRING LITERAL. Measured through the real jinja2 and the shipped template: "
     "planted after the <h1> line it renders VERBATIM into the HTML, and all nine "
     "assertions in this file stayed green -- the premise that a Jinja block's output is "
     "always CvDocument holds for a field reference and fails for a literal"),
    ('{% set blurb = "no agencies please" %}',
     "...in a STATEMENT rather than an expression, since the harvest must not depend on "
     "which of the two delimiters the author reached for"),
    ("{{ document.name | default('Anonymous Contractor') }}",
     "...as a FILTER argument, which is the spelling a template author is most likely to "
     "reach for innocently and the one that reads least like planted text"),
    ('<style>li { list-style-type: "no agencies "; }</style>',
     "CSS list-style-type: a word printed before EVERY bullet, and not `content:`"),
    ('<style>h1 { string-set: hdr "seeking remote Rust work"; }'
     " @page { @top-center { content: string(hdr); } }</style>",
     "CSS string-set + a running header: the words are in the string-set declaration, "
     "and the `content:` that prints them on every page carries no literal at all"),
    ('<style>q { quotes: "no agencies" "please"; }</style>',
     "CSS quotes: two literals, both drawn around quoted text"),
    ('<style>h1 { bookmark-label: "seeking remote Rust work"; }</style>',
     "CSS bookmark-label: into the PDF OUTLINE rather than the page, which is still text "
     "shipped under the user's name"),
    ("<p style=\"list-style-type: 'no agencies'\"></p>",
     "...and a non-`content` property in a style ATTRIBUTE, so the generalisation and the "
     "attribute route are both live at once rather than only in combination with each other"),
    ("<svg><text>no agencies please</text></svg>",
     "an SVG text node -- caught by the ordinary markup path, and pinned so the docstring's "
     "residual list stays honest about which routes really are residual"),
    # ── round 3: the harvest was tokenising the declaration VALUE with `[^;{}]*` ───
    # Every row below was measured GREEN against round 2's harvest. The cut at the first
    # `;`/`{`/`}` truncated the value MID-STRING, `_CSS_STRING_RE` then found no complete
    # quote pair in the fragment, and the harvest returned the EMPTY SET -- not a partial
    # catch, nothing at all. Verified against real CSS with tinycss2 (WeasyPrint's own
    # tokeniser): a semicolon or brace between quotes is part of one declaration value.
    ('<style>.contact::after { content: "seeking a remote role; no agencies"; }</style>',
     "a SEMICOLON inside the string. The value was cut at it, leaving `\"seeking a remote "
     "role` -- one unbalanced quote, from which the string extractor matched nothing"),
    ('<style>.contact::after { content: "no agencies {please}"; }</style>',
     "...and the BRACE form of the same cut, since the terminator class held all three"),
    ('<p style="list-style-type: \'no agencies; please\'"></p>',
     "...in a style ATTRIBUTE, so the tokenising bug and the attribute route are live at "
     "once rather than only in combination"),
    ('<style>h1 { string-set: hdr "seeking remote work; no agencies"; }'
     " @page { @top-center { content: string(hdr); } }</style>",
     "...feeding a RUNNING HEADER, which puts the truncated string on every page"),
    ("<p style=list-style-type:'no agencies'></p>",
     "the NINTH bypass, found beside the tokenising bug: an UNQUOTED style attribute whose "
     "value then opens a quote of its own. `_STYLE_ATTR_RE` offered only the two quoted "
     "forms, so this matched nothing and was never harvested at all"),
    ("<p>{{ 450 }}</p>",
     "a BARE NUMERIC literal in a Jinja output expression -- a pay floor typed straight "
     "into the template. The Jinja pass harvested QUOTED literals only, so this rendered "
     "verbatim and was never seen, while the docstring claimed to catch every literal a "
     "template author can type and have drawn on the page"),
])
def test_the_no_content_guard_catches_planted_content(planted, why):
    """POSITIVE CONTROLS. The guard above is NEGATIVE -- it passes when it finds nothing
    -- so a healthy tree cannot distinguish "clean" from "broken and finding nothing".
    Row 2 is the measured failure: planted verbatim in the shipped template, all twelve
    assertions in this file stayed green.

    Planted into the REAL shipped template rather than a synthetic stand-in, so each row
    is the change a human would actually commit.
    """
    from tests.template_content import (composer_headings, leftover_content,
                                        packaged_templates)

    _, text = packaged_templates()[0]
    assert leftover_content(text) <= composer_headings(), (
        "the real template is already dirty, so this row proves nothing about the plant")
    assert not leftover_content(planted + text) <= composer_headings(), why


def test_the_no_content_strip_still_has_exactly_one_definition():
    """The sharing itself, asserted STRUCTURALLY instead of by convention.

    This helper existed as two copies once, and a review found the identical bug in both:
    that is why it was extracted. A comment saying "shared" does not stop the next author
    inlining a variant, and round 2 of the review found TWO more bugs in the same strip --
    if either had had to be fixed twice, one copy would still be wrong.

    ENUMERATES from the source: every module under tests/ that so much as MENTIONS
    `leftover_content` must either BE the canonical module or take it from there by
    import. A hand-list of the two known consumers would walk straight past a third.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent
    canonical = root / "template_content.py"
    assert canonical.is_file(), "the canonical module moved; this guard checks nothing"

    mentions, definers, importers = set(), set(), set()
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "leftover_content" not in src:
            continue
        mentions.add(path)
        for node in ast.walk(ast.parse(src, filename=str(path))):
            if isinstance(node, ast.FunctionDef) and node.name == "leftover_content":
                definers.add(path)
            # Both import spellings, so `import tests.template_content as tc` is not a
            # hole -- and `... import leftover_content as _lc` is covered too, because
            # `alias.name` is the ORIGINAL name, not the local binding.
            elif isinstance(node, ast.ImportFrom) and (node.module or "") == \
                    "tests.template_content":
                importers.add(path)
            elif isinstance(node, ast.Import) and any(
                    a.name == "tests.template_content" for a in node.names):
                importers.add(path)

    assert len(mentions) > 1, (
        "only one module mentions leftover_content -- either the consumers were deleted "
        "or this sweep stopped matching, and for a structural guard those look identical")
    assert definers == {canonical}, (
        f"leftover_content is defined outside {canonical.name}: "
        f"{sorted(str(p.relative_to(root)) for p in definers - {canonical})}. It was two "
        f"copies once and the same bug lived in both -- import it, do not re-implement it.")
    assert mentions - importers == {canonical}, (
        f"{sorted(str(p.relative_to(root)) for p in mentions - importers - {canonical})} "
        f"uses the name leftover_content without importing it from tests.template_content")


@pytest.mark.parametrize("planted,why", [
    ('<style>.x::after { content: attr(data-blurb); }</style>'
     '<p class="x" data-blurb="no agencies"></p>',
     "content: attr() -- the words live in an HTML ATTRIBUTE, and attribute values are "
     "erased with their tag; harvesting them would mean flagging every class and href"),
    ('<img src="missing.png" alt="no agencies please">',
     "an alt attribute WeasyPrint may fall back to -- same place, same reason"),
    ("<p>{{ 450 if document.work else 0 }}</p>",
     "a number inside a COMPOUND Jinja expression. Only the WHOLE-BODY form `{{ 450 }}` "
     "is harvested -- see `_JINJA_NUMBER_RE`: taking every digit inside a `{{ }}` would "
     "red on `document.work[0]` and on `truncate(200)`, ordinary constructs that draw no "
     "number, and a guard that reds on a healthy template is one people delete. This is a "
     "drawn line rather than a structural limit, which is exactly why it needs a row"),
])
def test_the_no_content_guards_known_residual_is_pinned_not_assumed(planted, why):
    """The RESIDUAL, executable rather than asserted in prose.

    `leftover_content`'s docstring names exactly two routes it structurally cannot see,
    and a docstring is not a check -- a claim about a limit goes stale in silence the same
    way a claim about a mechanism does, which is this repo's named defect class and the
    reason this round exists at all. So the limit is a test: these rows MUST pass the
    guard today, and the day one of them stops passing, someone has widened the harvest
    and owes the docstring an edit.

    This is a statement about the HARVEST, deliberately not about WeasyPrint: whether a
    given build actually draws these is not measured here (no WeasyPrint in the offline
    suite), and claiming it would be exactly the overreach being pinned against.
    """
    from tests.template_content import (composer_headings, leftover_content,
                                        packaged_templates)

    _, text = packaged_templates()[0]
    assert leftover_content(planted + text) <= composer_headings(), (
        f"the guard now catches this route ({why}) -- good, but leftover_content's "
        f"docstring still lists it as a residual it cannot see. Update the docstring.")


@pytest.mark.parametrize("benign", [
    "<style>.role::after { content: \"\"; }</style>",
    "<style>.meta span + span::before { content: \" | \"; }</style>",
    "<style>.bullets li::before { content: \"\\2022\"; }</style>",
    "<style>.name::after { content: \"{{ document.name }}\"; }</style>",
    # The two exemptions the generalised property harvest carries, and they are what stop
    # it from redding on a clean tree: BOTH shipped templates already name a font in
    # quotes ("DejaVu Sans", "Times New Roman"). A guard that reds on a healthy repo is
    # not a stricter guard, it is one people delete.
    "<style>body { font-family: \"DejaVu Sans\", Helvetica; }</style>",
    "<style>@font-face { src: url(\"Example Sans.woff2\"); font-family: \"Example Sans\"; }"
    "</style>",
    "<style>body { background: url(\"example-watermark.png\"); }</style>",
    # Jinja that is a FIELD REFERENCE rather than a literal: its output is the candidate's
    # own gate-approved CvDocument, so deleting it is correct and must stay correct.
    "{% for role in document.work %}{{ role.company }}{% endfor %}",
    # A Jinja COMMENT reaches no page at all -- the engine discards it before rendering --
    # and both shipped templates open with a long one explaining their layout choices.
    "{# a template author's note about why this layout is single-column #}",
])
def test_the_no_content_guard_spares_punctuation_and_cv_data(benign):
    """NEGATIVE controls, and they are what stop the fix above from being useless.

    A guard that rejected every `content:` declaration would forbid the separators and
    glyphs a layout is entitled to, and template authors would route around it. Empty
    strings and pure punctuation carry no letters or digits and are dropped by the same
    rule that already drops the " | " between dates and title; a literal interpolating a
    CvDocument field is the CANDIDATE's content, not the template's.
    """
    from tests.template_content import (composer_headings, leftover_content,
                                        packaged_templates)

    _, text = packaged_templates()[0]
    assert leftover_content(benign + text) <= composer_headings()


def test_absent_jinja2_raises_naming_the_extra(monkeypatch, tmp_path):
    """Fail loudly at construction, naming the fix."""
    import builtins
    real_import = builtins.__import__

    def no_jinja(name, *a, **kw):
        if name.startswith("jinja2"):
            raise ImportError("no jinja2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_jinja)
    from sluice.renderers.template import _make

    class Cfg:
        template = ""
    with pytest.raises(RenderError, match=r"sluice\[render\]"):
        _make(Cfg())


def test_absent_weasyprint_raises_naming_the_extra(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_weasy(name, *a, **kw):
        if name.startswith("weasyprint"):
            raise ImportError("no weasyprint")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_weasy)
    from sluice.renderers.template import _make

    class Cfg:
        template = ""
    with pytest.raises(RenderError, match=r"sluice\[render\]"):
        _make(Cfg())


def test_the_renderer_reports_when_it_writes_no_pdf(tmp_path):
    """This renderer's OWN check. cv/render.py's equivalent belongs to the subprocess
    path and does not apply here."""
    class SilentHTML(FakeHTML):
        def write_pdf(self, path):
            pass          # writes nothing

    r = TemplateRenderer(None, html_module=SilentHTML)
    with pytest.raises(RenderError, match="no file"):
        r.render(CV, str(tmp_path / "out"))


def test_no_test_module_uses_importorskip():
    """The trap is documented twice and has now recurred TWICE.

    SWEEPS ALL OF `tests/`, and that widening is itself a review finding. This guard used
    to read one hardcoded filename -- its own -- so it was structurally unable to see the
    second recurrence, which landed in `tests/test_cv_engine.py`: a
    `pytest.importorskip("jinja2")` opening the one test that drives the REAL
    TemplateRenderer through `run_one`. With jinja2 genuinely absent that test SKIPPED and
    the file read green, which is precisely the failure `tests/test_renderers.py` records
    having been hit by once already with weasyprint. A guard scoped to the file it lives
    in only ever protects the file least likely to reoffend.

    jinja2 is in the `test` extra, so nothing under `tests/` needs a skip guard for it.

    Walks each file's AST rather than string-matching, per CLAUDE.md's guard-testing
    rule: "Hand-listed names lose to an import alias... Derive the local bindings from
    each file's own ImportFrom and ClassDef nodes." A substring check keyed on
    "pytest.importorskip(" is evadable by `from pytest import importorskip` (then a BARE
    call, no qualifier at all) or `import pytest as pt` (then `pt.importorskip(...)`) --
    neither leaves that literal substring anywhere in the file for a string-match guard
    to find, even though both actually invoke the exact function this test exists to
    forbid. Walking the AST also disposes of the self-trip problem structurally rather
    than by needle-crafting a match string: an AST walk sees CALLS, not substrings, so
    this very test's own `def` line and its docstring are simply not calls and can never
    match, no matter what words they contain.
    """
    import ast
    import pathlib

    root = pathlib.Path(os.path.dirname(os.path.abspath(__file__)))
    paths = sorted(root.rglob("*.py"))

    # SCOPE, asserted before the content. For a NEGATIVE guard an empty sweep reads
    # exactly like a clean one, so pin that the walk found the tree it meant to -- and
    # name this file specifically, since a sweep that silently stopped reaching the
    # module it lives in has stopped being a widening of anything.
    assert len(paths) > 1, "the tests/ walk found at most one file, so it swept nothing"
    assert root / "test_renderer_template.py" in paths, (
        "the sweep no longer reaches its own module, so the walk is broken")

    resolved_any = False
    violations = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        # Resolve the LOCAL bindings EACH file's own imports create, rather than assuming
        # `pytest` or `importorskip` are the names in scope -- an aliased import walks
        # straight past a check keyed on the canonical name, which is exactly the failure
        # shape CLAUDE.md's guard-testing section names. Per file, because a binding in
        # one module says nothing about the names in another.
        pytest_module_names = set()      # names bound to the `pytest` module itself
        bare_importorskip_names = set()  # names bound directly to `pytest.importorskip`
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pytest":
                        pytest_module_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "pytest":
                    for alias in node.names:
                        if alias.name == "importorskip":
                            bare_importorskip_names.add(alias.asname or alias.name)
        resolved_any = resolved_any or bool(pytest_module_names)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # <binding>.importorskip(...) where <binding> resolves to the pytest module
            # under ANY name it was imported as (plain "pytest" or an alias).
            is_qualified = (isinstance(func, ast.Attribute) and func.attr == "importorskip"
                            and isinstance(func.value, ast.Name)
                            and func.value.id in pytest_module_names)
            # a bare importorskip(...) reached via `from pytest import importorskip [as x]`.
            is_bare = isinstance(func, ast.Name) and func.id in bare_importorskip_names
            if is_qualified or is_bare:
                violations.append(f"{path.relative_to(root)}:{node.lineno}")

    # The other half of SCOPE, and the one that can hide. Many files under tests/ do
    # `import pytest`; an empty `resolved_any` means the binding-resolution loop above is
    # broken, not that the tree is clean -- and a broken resolver makes `is_qualified`
    # structurally unable to match ANY `pytest.importorskip(...)` call, however many
    # exist, leaving `violations` empty and this guard passing vacuously. `all([])` is
    # `True`; a guard that finds nothing is not the same as a guard that looked.
    assert resolved_any, (
        "resolved no local binding for the `pytest` module in ANY file under tests/, so "
        "a qualified pytest.importorskip(...) call could never match and this guard "
        "would pass having checked nothing")

    assert not violations, (
        f"pytest.importorskip called at {violations}. Every dependency these tests reach "
        f"for is in the `test` extra, so a skip guard cannot protect anything -- it can "
        f"only turn an absent dependency into a green run.")


def test_precheck_reports_a_shape_failure_as_a_format_violation(tmp_path):
    """The seam's OPTIONAL hook (core/protocols.py), implemented here and NOT by
    `script`. cv/engine.py folds the returned strings in with the fabrication gate's own,
    so this renderer's grammar reaches the model's one retry instead of surfacing at
    render time -- after the LLM spend, past the only recovery there is.

    Asserts both arms: `[]` for a CV this renderer can lay out, and a `FORMAT:`-prefixed
    string naming the problem for one it cannot. A precheck that returned something
    truthy for every CV would bin every lead, so the empty arm is the load-bearing one.
    """
    r = _renderer(tmp_path, "{{ document.name }}")
    assert r.precheck(CV) == []
    broken = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer",
                        "03/2021-present Staff Engineer")
    assert "03/2021-present Staff Engineer" in broken, "the replace no-opped"
    msgs = r.precheck(broken)
    assert len(msgs) == 1 and msgs[0].startswith("FORMAT: "), msgs
    assert "meta line" in msgs[0]


def test_a_jinja_syntax_error_is_raised_as_a_render_error_naming_the_template(tmp_path):
    """`from_string` carries NO filename, so an unclosed `{% for %}` raises
    `jinja2.exceptions.TemplateSyntaxError` reading `File "<template>", line N` -- which
    loses precisely what `_template_name` exists to supply. A user with several templates
    cannot tell which one broke, and the seam leaks a jinja2 type its callers do not
    handle. Re-raised as RenderError, with the original preserved as `__cause__` so the
    line number and jinja2's own traceback survive for anyone who wants them.
    """
    path = tmp_path / "broken.html.j2"
    path.write_text("<p>{% for role in document.work %}{{ role.title }}</p>", encoding="utf-8")
    with pytest.raises(RenderError, match="not valid Jinja2") as ei:
        TemplateRenderer(str(path), html_module=FakeHTML)
    assert str(path) in str(ei.value), "the error does not name the offending template"
    from jinja2.exceptions import TemplateError
    assert isinstance(ei.value.__cause__, TemplateError), (
        "the original jinja2 error was discarded rather than chained")


def test_absent_jinja2_raises_naming_the_extra_on_direct_construction(monkeypatch, tmp_path):
    """The docstring's OWN claim -- this class stays constructible directly, without
    going through `_make` -- means jinja2's absence must be caught here too, not only in
    `_make`. Before this, a direct construction with jinja2 missing raised a bare
    `ImportError` at the `from jinja2 import ...` line, leaking the untyped failure
    `_make`'s own try/except already exists to stop. CodeRabbit's cloud review found
    this: `_make` proves jinja2 is installed before it ever reaches this constructor, so
    only the DIRECT path was exposed.
    """
    import builtins
    real_import = builtins.__import__

    def no_jinja(name, *a, **kw):
        if name.startswith("jinja2"):
            raise ImportError("no jinja2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_jinja)
    with pytest.raises(RenderError, match=r"sluice\[render\]"):
        TemplateRenderer(None, html_module=FakeHTML)


def test_a_bad_expression_at_render_time_is_raised_as_a_render_error(tmp_path):
    """`UndefinedError` is ONE Jinja2 runtime failure among several a user's free-text
    template can trigger. Before this fix, only `UndefinedError` was converted, so any
    other runtime failure -- a `TemplateRuntimeError`, or a bare `TypeError`/`ValueError`
    from an expression or filter -- crossed the seam raw. Verified against the real
    engine: `document.name` is a `str`, and `str + int` raises `TypeError` at render time,
    not at template-compile time (`__init__`'s syntax check does not see it).
    """
    path = tmp_path / "bad_expr.html.j2"
    path.write_text("<p>{{ document.name + 1 }}</p>", encoding="utf-8")
    r = TemplateRenderer(str(path), html_module=FakeHTML)
    with pytest.raises(RenderError, match="failed to render") as ei:
        r.render(CV, str(tmp_path / "out"))
    assert str(path) in str(ei.value), "the error does not name the offending template"


def test_a_weasyprint_failure_at_write_time_is_raised_as_a_render_error(tmp_path):
    """`self._HTML(...).write_pdf(pdf_path)` is the SECOND half of the seam-untyped gap:
    a WeasyPrint internal failure, or an OSError on the output path, propagated raw
    before this fix. `core/protocols.py` states RenderError is the seam's error type for
    a renderer that "could not produce a PDF" -- this is that case.
    """
    class BoomHTML(FakeHTML):
        def write_pdf(self, path):
            raise RuntimeError("weasyprint blew up")

    r = TemplateRenderer(None, html_module=BoomHTML)
    with pytest.raises(RenderError, match="WeasyPrint could not write") as ei:
        r.render(CV, str(tmp_path / "out"))
    assert isinstance(ei.value.__cause__, RuntimeError), (
        "the original WeasyPrint error was discarded rather than chained")


def test_a_tilde_in_cv_template_is_expanded(tmp_path, monkeypatch):
    """expanduser at INGRESS -- core/paths.py states the convention and this is a new
    ingress site. `~` is expanded by a SHELL; a value read out of a YAML file never met
    one, so `cv.template: ~/mine.html.j2` arrived here literally and was reported as
    "is not a file" for a file that plainly exists.

    conftest.py's autouse fixture already points HOME at a tmp dir, but this sets it
    explicitly: the assertion is about THIS path resolving under THIS home, and relying
    on a fixture two files away to have chosen the same directory would make the test
    pass for a reason it does not state.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))   # expanduser's Windows source
    (home / "mine.html.j2").write_text("{{ document.name }}", encoding="utf-8")

    r = TemplateRenderer("~/mine.html.j2", html_module=FakeHTML)
    r.render(CV, str(tmp_path / "out"))
    assert "EXAMPLE PERSON" in FakeHTML.captured["html"]


def test_a_missing_packaged_default_raises_a_render_error(monkeypatch, tmp_path):
    """The PACKAGED-DEFAULT branch, which is the one every user takes.

    An explicitly-named path that cannot be read already raised a RenderError saying what
    to do; the default branch raised a bare FileNotFoundError naming a path inside
    site-packages -- an untyped failure crossing the seam, from the arm most likely to
    be exercised. Reproduces the failure tests/test_packaging.py exists to prevent (an
    install whose package data went missing) at the point where a user would meet it.
    """
    import importlib.resources

    def no_package_data(_anchor):
        raise FileNotFoundError("no such package data")

    monkeypatch.setattr(importlib.resources, "files", no_package_data)
    with pytest.raises(RenderError, match="packaged default"):
        TemplateRenderer(None, html_module=FakeHTML)


def test_a_missing_system_library_raises_naming_both_fixes(monkeypatch):
    """The single MOST LIKELY real failure of this feature, and it used to escape raw.

    WeasyPrint links natively against cairo/pango/gobject, which are not Python packages
    and which no `pip install` can supply. Importing it without them raises
    `OSError: cannot load library 'libgobject-2.0-0'` -- measured 2026-08-06 on a machine
    with the render extra installed -- and `except ImportError` did not catch it, so the
    branch's headline claim (a render failure is now loud and diagnosable AT
    CONSTRUCTION) did not hold for the case it was written for.

    The message must name BOTH fixes. A user who already has the extra and reads only
    "pip install 'job-sluice[render]'" goes hunting for a package they have.
    """
    import builtins
    real_import = builtins.__import__

    def no_native_libs(name, *a, **kw):
        if name.startswith("weasyprint"):
            raise OSError("cannot load library 'libgobject-2.0-0': dlopen(...) not found")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_native_libs)
    from sluice.renderers.template import _make

    class Cfg:
        template = ""
    with pytest.raises(RenderError) as ei:
        _make(Cfg())
    msg = str(ei.value)
    # The exact distribution name, not a bare substring check: "sluice[render]" is ALSO a
    # substring of the correct "job-sluice[render]" (the PyPI distribution, since the rename
    # -- see pyproject.toml's [project] comment), so a substring assertion here would pass
    # whether or not the message actually said the right thing, and did exactly that until
    # this was tightened.
    assert "job-sluice[render]" in msg, "the message does not name the extra"
    assert "cairo" in msg and "pango" in msg, (
        "the message does not name the SYSTEM libraries, which is the half a user with "
        f"the extra already installed needs: {msg}")
    assert "README" in msg, "the message does not point at the documented macOS step"
    assert isinstance(ei.value.__cause__, OSError)
