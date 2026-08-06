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

    r = TemplateRenderer(None, html_module=SilentHTML)
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
    "pip install 'sluice[render]'" goes hunting for a package they have.
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
    assert "sluice[render]" in msg, "the message does not name the extra"
    assert "cairo" in msg and "pango" in msg, (
        "the message does not name the SYSTEM libraries, which is the half a user with "
        f"the extra already installed needs: {msg}")
    assert "README" in msg, "the message does not point at the documented macOS step"
    assert isinstance(ei.value.__cause__, OSError)
