"""The `template` renderer.

NO `pytest.importorskip` ANYWHERE IN THIS FILE. jinja2 is in the `test` extra precisely
so these run in CI; an importorskip would silently skip them and read as green, which is
the trap tests/test_renderers.py records having been hit by once already.

WeasyPrint is NOT imported here -- it needs cairo/pango (and, on macOS, a
DYLD_FALLBACK_LIBRARY_PATH). It is injected as a fake, exactly as the renderer it
replaces was tested.
"""
import os

import pytest

from sluice.renderers.script import RenderError
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

    def write_pdf(self, path, stylesheets=None):
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
        return TemplateRenderer(None, html_module=FakeHTML, css_module=lambda string="": object())
    path = tmp_path / "user.html.j2"
    path.write_text(template_text, encoding="utf-8")
    return TemplateRenderer(str(path), html_module=FakeHTML,
                            css_module=lambda string="": object())


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
    r = TemplateRenderer(str(path), html_module=FakeHTML,
                         css_module=lambda string="": object())
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
        TemplateRenderer(str(tmp_path / "nope.html.j2"), html_module=FakeHTML,
                         css_module=lambda string="": object())


def test_a_template_directory_is_refused_at_construction(tmp_path):
    """os.path.exists() is True for a directory -- the same trap `script` already
    documents. isfile, not exists."""
    d = tmp_path / "adir.html.j2"
    d.mkdir()
    with pytest.raises(RenderError, match="template"):
        TemplateRenderer(str(d), html_module=FakeHTML, css_module=lambda string="": object())


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


def test_the_shipped_template_contributes_no_content():
    """The shipped template is NOT neutral -- a template must lay something out, so its
    layout is a shipped opinion. The property that IS achievable and mechanically
    checkable is narrower: it contributes no CONTENT of its own.

    The heading set is DERIVED from cv/compose.py's _RULES, never hand-listed, so this
    guard cannot drift from what the composer actually emits.
    """
    import re
    from importlib.resources import files

    from sluice.cv.compose import _RULES

    headings = {ln.strip() for ln in _RULES.splitlines()
                if ln.strip() and ln.strip() == ln.strip().upper()
                and all(c.isalpha() or c.isspace() for c in ln.strip())}
    assert headings, "derived no headings, so this guard would pass vacuously"

    text = files("sluice.templates").joinpath("cv_plain.html.j2").read_text(encoding="utf-8")
    stripped = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    stripped = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ", stripped, flags=re.S)
    stripped = re.sub(r"<[^>]*>", " ", stripped, flags=re.S)
    # A token with no letters and no digits is PUNCTUATION -- the " | " separators
    # between dates/location/title, which `_RULES` itself uses in the format it asks the
    # composer for. Punctuation is layout, not content, and layout is admittedly a
    # shipped opinion (see the docstring). Dropping these keeps the guard aimed at the
    # thing it can actually check: words the template puts in the user's mouth.
    leftover = {tok for tok in (t.strip() for t in stripped.splitlines())
                if tok and any(c.isalnum() for c in tok)}
    assert leftover <= headings, (
        f"the shipped template contributes content of its own: {sorted(leftover - headings)}")


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
        def write_pdf(self, path, stylesheets=None):
            pass          # writes nothing

    r = TemplateRenderer(None, html_module=SilentHTML,
                         css_module=lambda string="": object())
    with pytest.raises(RenderError, match="no file"):
        r.render(CV, str(tmp_path / "out"))


def test_this_module_never_uses_importorskip():
    """The trap is documented twice and has still recurred once.

    Walks this file's own AST rather than string-matching, per CLAUDE.md's guard-testing
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
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_renderer_template.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    # Resolve the LOCAL bindings this file's own imports create, rather than assuming
    # `pytest` or `importorskip` are the names in scope -- an aliased import walks
    # straight past a check keyed on the canonical name, which is exactly the failure
    # shape CLAUDE.md's guard-testing section names.
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

    # SCOPE assertion, same shape as test_the_shipped_template_contributes_no_content's
    # "derived no headings" check just above. This file DOES `import pytest` (see the
    # top of the module), so an empty `pytest_module_names` means the binding-resolution
    # loop above is broken, not that the file is clean -- and a broken resolver makes
    # `is_qualified` structurally unable to match ANY `pytest.importorskip(...)` call,
    # however many exist below, leaving `violations` empty and this guard passing
    # vacuously. `all([])` is `True`; a guard that finds nothing is not the same as a
    # guard that looked.
    assert pytest_module_names, (
        "resolved no local binding for the `pytest` module import, so a qualified "
        "pytest.importorskip(...) call could never match and this guard would pass "
        "having checked nothing")

    violations = []
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
            violations.append(node.lineno)

    assert not violations, f"pytest.importorskip called at line(s) {violations}"
