"""A user's Jinja2 template + WeasyPrint, registered as the `template` renderer.

This is the renderer the design doc introduces to replace shipping a CV design: the
template's LAYOUT belongs to the user (their own `.html.j2` file, or the packaged
default), while sluice supplies only the CONTENT -- the parsed `CvDocument` the
fabrication gate already certified. Neither jinja2 nor weasyprint is imported at module
scope: `sluice/` is standard-library only, and `sluice/renderers/__init__.py`'s autoload
imports every sibling module at process start (including this one) for every command,
render-related or not, so a module-scope import of either would break commands that
never touch rendering. Both are imported lazily, inside `_make`, and `TemplateRenderer`
itself imports jinja2 lazily too so it stays constructible directly (as the tests do)
without going through `_make` at all.
"""
import os

from sluice.core import plugins
from sluice.cv.parse import parse_cv
from sluice.renderers import register
from sluice.renderers.script import RenderError  # one error type for the whole seam

_MISSING_EXTRA = "renderer 'template' requires the render extra: pip install 'sluice[render]'"


class TemplateRenderer:
    """Fills a user's Jinja2 template with a parsed CvDocument and writes the PDF via
    WeasyPrint. html_module/css_module are the WeasyPrint HTML/CSS classes (or fakes, in
    tests) -- injected rather than imported here, exactly like `WeasyPrintRenderer`, so
    this class can be constructed and unit-tested with no native libraries installed at
    all."""

    def __init__(self, template_path, *, html_module, css_module):
        # html_module/css_module mirror WeasyPrintRenderer's injected-class constructor
        # shape, keeping the two renderer implementations swappable and testable the
        # same way. css_module is stored but never called: this renderer's CSS lives
        # inline in the template's own <style> block (cv_plain.html.j2 carries one, and
        # a user template is free to carry its own), and WeasyPrint parses <style> tags
        # out of the rendered HTML string automatically -- there is no separate
        # stylesheet to hand it.
        self._HTML = html_module
        self._CSS = css_module

        # template_path=None/"" -> the packaged default. An explicitly named path must
        # be a FILE at construction time, not merely exist -- isfile, not exists():
        # exists() is True for a directory too, the same trap `script.py` already
        # documents, and checking NOW rather than at the first render() call is the
        # whole point of this check: a render failure must not surface only after the
        # LLM has already composed a CV and it has passed the fabrication gate.
        if template_path:
            if not os.path.isfile(template_path):
                raise RenderError(
                    f"renderer 'template': cv.template is not a file: '{template_path}'. "
                    f"Point cv.template at your Jinja2 template, or leave it unset to use "
                    f"the packaged default."
                )
            with open(template_path, encoding="utf-8") as f:
                text = f.read()
        else:
            # importlib.resources, not a relative filesystem path: this must resolve to
            # the PACKAGED file wherever sluice is installed (a real `pip install`, not
            # merely a source checkout) -- see sluice/templates/__init__.py and the
            # design doc's Packaging section, both of which exist so this actually ships
            # in a wheel rather than only working from a checkout.
            from importlib.resources import files
            text = files("sluice.templates").joinpath("cv_plain.html.j2").read_text(
                encoding="utf-8")

        # jinja2 imported here, not at module scope, and not only in `_make`: this class
        # must stay constructible directly (as every test in this module does) without
        # requiring a caller to go through the seam factory first.
        from jinja2 import Environment

        # autoescape=True, UNCONDITIONALLY -- never select_autoescape(). Measured (see
        # the task report): select_autoescape()('cv_plain.html.j2') is False, because it
        # suffix-matches .html/.htm/.xml and the conventional .j2 suffix defeats it. With
        # escaping off, a gate-verified bullet reading "Cut p99 latency to <200ms"
        # renders as an unknown HTML element and WeasyPrint DROPS the text -- the PDF
        # then silently differs from what validate() approved, and nobody sees it until
        # after the CV has been sent under the user's name.
        self._template = Environment(autoescape=True).from_string(text)

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        # parse_cv, not a second gate: the fabrication gate has already run on cv_text by
        # the time any renderer sees it (see sluice/cv/parse.py's module docstring). A
        # SHAPE this parser cannot model raises CvParseError, which cv/engine.py's
        # existing single retry handles exactly like a gate violation -- this renderer
        # neither catches it nor treats it specially, and never validates facts itself.
        document = parse_cv(cv_text)
        os.makedirs(out_dir, exist_ok=True)
        pdf_path = os.path.join(out_dir, neutral_name)
        html = self._template.render(document=document)
        self._HTML(string=html).write_pdf(pdf_path)
        # This renderer's OWN "wrote no file" check. cv/render.py has an equivalent, but
        # that one belongs to the `script` renderer's subprocess path and does not apply
        # here (spec's Failure modes table) -- this renderer never shells out.
        if not os.path.isfile(pdf_path):
            raise RenderError(f"renderer 'template' wrote no file: {pdf_path}")
        return pdf_path


def _make(cvcfg):
    # jinja2 checked FIRST, in its own statement, before weasyprint is even named:
    # importing weasyprint without its native libraries (cairo/pango/gobject) raises
    # OSError, not ImportError -- measured 2026-08-06 on this machine, no
    # DYLD_FALLBACK_LIBRARY_PATH set -- and that would blow straight through an
    # `except ImportError` below. Checking jinja2 first, and letting a missing jinja2
    # short-circuit the statement before weasyprint's import ever runs, means a missing
    # jinja2 alone is reported cleanly without ever touching weasyprint's import at all.
    try:
        import jinja2  # noqa: F401  -- imported only to prove the extra is installed;
        # TemplateRenderer imports Environment again itself when it actually builds one.
        from weasyprint import CSS, HTML
    except ImportError as e:
        raise RenderError(_MISSING_EXTRA) from e
    # CvConfig.template now exists (blank means "use the packaged default", per
    # TemplateRenderer's own constructor check), so read it directly -- the earlier
    # `getattr` defence only existed because the config field had not landed yet.
    template_path = cvcfg.template or None
    return TemplateRenderer(template_path, html_module=HTML, css_module=CSS)


register("template", _make)
# `weasyprint` was a <pre>-dumping renderer that ignored the CV's structure entirely.
# `template` supersedes it: same WeasyPrint backend, but the composed CV is parsed and
# laid out by the user's own Jinja2 template. Retired rather than silently dropped so a
# config naming it says what to do instead.
plugins.register_retired(
    "renderer", "weasyprint",
    "The bundled `weasyprint` renderer has been replaced by `template`, which renders "
    "your own Jinja2 template. Set cv.renderer: template (and optionally cv.template: "
    "/path/to/your.html.j2; blank uses the packaged default).")
