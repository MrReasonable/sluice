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

It also implements the Renderer seam's OPTIONAL `precheck` hook (see
`sluice/core/protocols.py`), which is how the meta-line grammar below stays THIS
renderer's requirement instead of the engine's. `script` implements no counterpart and
is deliberately not gated by it.
"""
import os

from sluice.core import plugins
# The seam's error type, taken from the seam rather than from the sibling implementation
# that happened to define it first (see `core/protocols.py`).
from sluice.core.protocols import RenderError
from sluice.cv.parse import CvParseError, parse_cv
from sluice.renderers import register

_PACKAGED_DEFAULT = "cv_plain.html.j2"

# Covers BOTH ways the backend fails to load, because the fix differs and only one of
# them is a pip problem. Naming only the extra sent a user with the extra already
# installed hunting for a package they had -- see `_make`, and README's Rendering
# prerequisites for the macOS loader-path case this text points at.
_MISSING_EXTRA = (
    "renderer 'template' could not load its rendering backend: pip install "
    "'job-sluice[render]'. If that extra is ALREADY installed, WeasyPrint additionally needs "
    "its native libraries (cairo, pango, gdk-pixbuf), which are not Python packages and "
    "cannot be installed by pip -- see https://github.com/MrReasonable/sluice/blob/main/"
    "docs/INSTALL.md#system-libraries-for-pdf-rendering, including "
    "the macOS DYLD_FALLBACK_LIBRARY_PATH step (not needed if you installed via "
    "brew install -- only a pip install under a non-Homebrew Python needs it). "
    "Or set cv.renderer: script to use your own render script instead."
)


class TemplateRenderer:
    """Fills a user's Jinja2 template with a parsed CvDocument and writes the PDF via
    WeasyPrint. html_module is the WeasyPrint HTML class (or a fake, in tests) --
    injected rather than imported here, so this class can be constructed and
    unit-tested with no native libraries installed at all."""

    def __init__(self, template_path, *, html_module):
        # Only HTML is injected. A `css_module` parameter sat here too, stored and never
        # called, justified by a comment saying it mirrored `WeasyPrintRenderer`'s
        # constructor shape -- and `WeasyPrintRenderer` is deleted in the same change that
        # introduced this class, so the reason was false the moment it was written. This
        # renderer's CSS lives inline in the template's own <style> block (cv_plain.html.j2
        # carries one, and a user template is free to carry its own) and WeasyPrint parses
        # <style> out of the rendered HTML string itself: there is no separate stylesheet
        # to hand it, and so nothing for a CSS class to do.
        self._HTML = html_module

        # template_path=None/"" -> the packaged default. An explicitly named path must
        # be a FILE at construction time, not merely exist -- isfile, not exists():
        # exists() is True for a directory too, the same trap `script.py` already
        # documents, and checking NOW rather than at the first render() call is the
        # whole point of this check: a render failure must not surface only after the
        # LLM has already composed a CV and it has passed the fabrication gate.
        if template_path:
            # expanduser at INGRESS, which is this project's rule for a path a user
            # names (core/paths.py:30 states it, and this is a new ingress site). `~`
            # is expanded by a SHELL, not by open(), so `cv.template: ~/mine.html.j2`
            # in a YAML file reaches here literally and reported "is not a file" for a
            # file that plainly exists. Expanded before the isfile check, so the check
            # and the open agree, and before `_template_name` below, so an error names
            # the path actually opened.
            template_path = os.path.expanduser(template_path)
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
            #
            # Wrapped, because THIS is the branch every user takes. An install whose
            # package-data went missing (the exact failure tests/test_packaging.py
            # guards) raised a bare FileNotFoundError naming a path inside site-packages,
            # while the explicitly-named branch above raised a RenderError that says what
            # to do. The seam's callers handle RenderError; a stray OSError crossing the
            # seam is the untyped failure this renderer exists to stop producing.
            from importlib.resources import files
            try:
                text = files("sluice.templates").joinpath(_PACKAGED_DEFAULT).read_text(
                    encoding="utf-8")
            except OSError as e:
                raise RenderError(
                    f"renderer 'template': the packaged default template "
                    f"(sluice.templates:{_PACKAGED_DEFAULT}) could not be read: {e}. This "
                    f"install is missing its package data; reinstall sluice, or set "
                    f"cv.template to your own Jinja2 template."
                ) from e

        # jinja2 imported here, not at module scope, and not only in `_make`: this class
        # must stay constructible directly (as every test in this module does) without
        # requiring a caller to go through the seam factory first. `_make` already proves
        # jinja2 is installed before it ever reaches this constructor, so THAT path is
        # covered -- but a direct construction (the one this docstring says stays
        # supported) skips `_make` entirely, and without this except a missing jinja2
        # there escaped as a bare `ImportError` rather than the seam's `RenderError`,
        # same gap `_make` itself closed for `ImportError`/`OSError` a few lines below.
        try:
            from jinja2 import Environment, StrictUndefined
            from jinja2.exceptions import TemplateError
        except ImportError as e:
            raise RenderError(f"{_MISSING_EXTRA} (underlying error: {e})") from e

        # Named for the RenderError below: a StrictUndefined failure must say WHICH
        # template broke, and the packaged default has no filesystem path a user can
        # act on -- name it explicitly rather than letting an error print None/"".
        self._template_name = (
            template_path or f"sluice.templates:{_PACKAGED_DEFAULT} (packaged default)")

        # autoescape=True, UNCONDITIONALLY -- never select_autoescape(). Measured (see
        # the task report): select_autoescape()('cv_plain.html.j2') is False, because it
        # suffix-matches .html/.htm/.xml and the conventional .j2 suffix defeats it. With
        # escaping off, a gate-verified bullet reading "Cut p99 latency to <200ms"
        # renders as an unknown HTML element and WeasyPrint DROPS the text -- the PDF
        # then silently differs from what validate() approved, and nobody sees it until
        # after the CV has been sent under the user's name.
        #
        # undefined=StrictUndefined, for the identical reason: Jinja2's DEFAULT
        # Undefined renders a typo'd field ({{ document.nmae }}, {{ document.work[0]
        # .titel }}) as an EMPTY STRING rather than raising -- measured 2026-08-06, only
        # a wholly undefined ROOT name raises by default, so `document.nmae` constructs,
        # renders, and produces a PDF silently missing the candidate's name with nothing
        # anywhere to say so. A user's template is free text sluice does not control, and
        # this is the SAME "silently differs from what validate() approved" harm the
        # autoescape choice above already exists to prevent, just on a typo instead of an
        # unescaped character. `CvDocument` (sluice/cv/parse.py) always populates every
        # field its dataclasses declare, so StrictUndefined has nothing legitimate left
        # to break: every attribute a template can reach is guaranteed present.
        #
        # `from_string` COMPILES, so an unclosed `{% for %}` raises here, at construction
        # -- which is where this renderer wants every failure it can move. Wrapped
        # because `from_string` carries NO filename: jinja2's own TemplateSyntaxError
        # says `File "<template>", line 4`, so the raw exception loses exactly the thing
        # `_template_name` exists to supply, and a user with several templates cannot
        # tell which one broke. Re-raised with the original as `__cause__`, so the line
        # number and the jinja2 traceback are still there for anyone who wants them.
        try:
            self._template = Environment(
                autoescape=True, undefined=StrictUndefined).from_string(text)
        except TemplateError as e:
            raise RenderError(
                f"renderer 'template': {self._template_name} is not valid Jinja2: {e}"
            ) from e

    def precheck(self, cv_text: str) -> list[str]:
        """The Renderer seam's OPTIONAL grammar hook -- see core/protocols.py.

        cv/engine.py calls this inside its compose/gate retry loop and folds the result
        in with the fabrication gate's violations, so a CV this renderer cannot lay out
        is re-composed once rather than discovered at render time, after the LLM spend
        and past the only retry there is.

        NOT a second fabrication gate, and it must never become one: it reports what
        `parse_cv` reports, which is SHAPE only (see sluice/cv/parse.py's module
        docstring). The `FORMAT:` prefix matches the shape the engine's other gate
        messages take, since the string is prompt text fed straight back to the model.

        `script` deliberately has no counterpart. It shells out to arbitrary user code
        and has no grammar of its own to impose, and the engine applying THIS renderer's
        grammar to it was measured to report `skipped-gate` on a gate-clean CV that the
        operator's own script would have rendered.
        """
        try:
            parse_cv(cv_text)
        except CvParseError as e:
            return [f"FORMAT: {e}"]
        return []

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        # parse_cv, not a second gate: the fabrication gate has already run on cv_text by
        # the time any renderer sees it (see sluice/cv/parse.py's module docstring). A
        # SHAPE this parser cannot model raises CvParseError -- which `precheck` above
        # has already reported to cv/engine.py's retry loop, so reaching this call with
        # an unparseable CV means the retry was exhausted and the engine chose not to
        # render. Left uncaught deliberately: it is the loud failure, not the silent one.
        document = parse_cv(cv_text)
        os.makedirs(out_dir, exist_ok=True)
        pdf_path = os.path.join(out_dir, neutral_name)
        # StrictUndefined (see __init__) turns a typo'd field reference into an
        # exception instead of a silently blank PDF. Imported here, not at module
        # scope, for the same lazy-import reason as jinja2 itself (see the module
        # docstring): re-raised as RenderError so the message names THIS renderer and
        # the offending template rather than surfacing a bare jinja2 traceback with no
        # renderer context -- Jinja2's own UndefinedError text already names the
        # missing attribute (e.g. "... object has no attribute 'nmae'").
        from jinja2.exceptions import TemplateError, UndefinedError
        try:
            html = self._template.render(document=document)
        except UndefinedError as e:
            raise RenderError(
                f"renderer 'template': {self._template_name} references a field "
                f"CvDocument does not have: {e}") from e
        except (TemplateError, TypeError, ValueError) as e:
            # UndefinedError is one Jinja2 runtime failure among several a user's free-text
            # template can trigger -- a filter applied to the wrong type raises
            # TemplateRuntimeError (a TemplateError) or a bare TypeError/ValueError from
            # the filter itself. `__init__` already wraps a syntax error at COMPILE time
            # for the identical reason: the template is content this renderer does not
            # control, and the seam's contract is ONE error type, not "whichever Jinja2
            # exception happens to be raised this time."
            raise RenderError(
                f"renderer 'template': {self._template_name} failed to render: "
                f"{type(e).__name__}: {e}") from e
        # WeasyPrint's own failures (and any OSError writing pdf_path) are the second half
        # of the seam-untyped gap above: nothing after the try/except catches them, so a
        # WeasyPrint internal error crossed the seam raw. Bounded to this ONE call --
        # there is no other code between it and the "wrote no file" check below that this
        # broad except could accidentally swallow.
        try:
            self._HTML(string=html).write_pdf(pdf_path)
        except Exception as e:
            raise RenderError(
                f"renderer 'template': WeasyPrint could not write {pdf_path}: "
                f"{type(e).__name__}: {e}") from e
        # This renderer's OWN "wrote no file" check. cv/render.py has an equivalent, but
        # that one belongs to the `script` renderer's subprocess path and does not apply
        # here (spec's Failure modes table) -- this renderer never shells out.
        if not os.path.isfile(pdf_path):
            raise RenderError(f"renderer 'template' wrote no file: {pdf_path}")
        return pdf_path


def _make(cvcfg):
    # OSError alongside ImportError, and it is the one that actually fires in the field:
    # importing weasyprint without its native libraries (cairo/pango/gobject) raises
    # `OSError: cannot load library 'libgobject-2.0-0'`, NOT ImportError -- measured
    # 2026-08-06 on a machine with the render extra installed and no
    # DYLD_FALLBACK_LIBRARY_PATH set. Catching ImportError alone let that escape the seam
    # as a raw cffi OSError, which defeats this feature's whole claim that a render
    # failure is now loud and diagnosable AT CONSTRUCTION rather than after the LLM
    # spend. It is also the single likeliest real failure, because those libraries are
    # not Python dependencies and no `pip install` can supply them.
    #
    # jinja2 stays FIRST, in its own statement: a missing jinja2 short-circuits before
    # weasyprint's import runs at all, so the two failures cannot be confused for each
    # other while the message covers both.
    try:
        import jinja2  # noqa: F401  -- imported only to prove the extra is installed;
        # TemplateRenderer imports Environment again itself when it actually builds one.
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        raise RenderError(f"{_MISSING_EXTRA} (underlying error: {e})") from e
    # CvConfig.template now exists (blank means "use the packaged default", per
    # TemplateRenderer's own constructor check), so read it directly -- the earlier
    # `getattr` defence only existed because the config field had not landed yet.
    template_path = cvcfg.template or None
    return TemplateRenderer(template_path, html_module=HTML)


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
