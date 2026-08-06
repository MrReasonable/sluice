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
    """The trap is documented twice and has still recurred once."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_renderer_template.py")
    with open(path, encoding="utf-8") as f:
        body = f.read()
    occurrences = [ln for ln in body.splitlines() if "importorskip" in ln]
    # This docstring-and-comment file mentions the word; only a CALL is forbidden. The
    # match requires the "pytest." qualifier that every real call in this codebase uses
    # (see tests/test_renderers.py) rather than the bare function name alone -- measured,
    # not assumed: this guard reads its OWN source file, and this test's own `def` line
    # ends in the bare name immediately followed by an opening paren, an accidental
    # match a name-only check would flag on itself, every run, forever.
    call_form = "pytest." + "importorskip("
    assert not [ln for ln in occurrences if call_form in ln]
