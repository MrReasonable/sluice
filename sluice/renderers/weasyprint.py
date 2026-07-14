"""A bundled in-process renderer, registered as `weasyprint`.

This is the answer to `script`'s missing default: no external file to supply, no
subprocess, no HOME-dependent environment. `weasyprint` is already declared as the
`render` extra in pyproject.toml, so this adds no new dependency -- it just uses the one
that was already there.

It is NOT the default. Switching the default would break an operator who already has a
working render script and whose CV layout depends on it. Opt in with `cv.renderer:
weasyprint`.

WeasyPrint is imported inside the factory, not at module scope: `sluice/` is
standard-library only, and the registry must be populatable without the optional extra
installed. Importing this module always works; asking for the renderer without the extra
fails loudly, at construction, with a message that names the fix.
"""
import os

from sluice.cv.render import strip_citations
from sluice.renderers import register

# Deliberately plain. The CV's content is gated; its typography is not the gate's problem,
# and a heavyweight default stylesheet here would silently change every operator's layout.
# The CV text is wrapped in <pre>, and WeasyPrint's user-agent stylesheet declares
# `pre { white-space: pre; font-family: monospace }` DIRECTLY on the element -- a direct UA
# declaration beats a value inherited from `body`, so wrapping and the intended font have to
# be set on `pre` itself, not on `body`. Without `white-space: pre-wrap` a bullet longer
# than the text column overflows the page box and is silently clipped in the PDF an employer
# sees (print media has no scrollbar); `overflow-wrap: anywhere` breaks a single over-long
# token such as a URL. `font-*: inherit` pulls the body font back onto the <pre> so the
# DejaVu Sans declaration actually reaches the text instead of being dead.
_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "DejaVu Sans", "Helvetica", sans-serif; font-size: 10.5pt;
       line-height: 1.35; }
pre { font-family: inherit; font-size: inherit; line-height: inherit;
      white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
"""


class WeasyPrintRenderer:
    def __init__(self, html_module, css_module):
        self._HTML, self._CSS = html_module, css_module

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        os.makedirs(out_dir, exist_ok=True)
        pdf_path = os.path.join(out_dir, neutral_name)
        # strip_citations, not validate: the [id] tokens the fabrication gate checked are
        # an internal artefact and must not reach the employer. The GATE already ran --
        # a renderer that validated would be a second, weaker gate, and a way around the
        # real one.
        body = strip_citations(cv_text)
        self._HTML(string=f"<pre>{_escape(body)}</pre>").write_pdf(
            pdf_path, stylesheets=[self._CSS(string=_CSS)])
        if not os.path.exists(pdf_path):
            raise RuntimeError(f"weasyprint wrote no file: {pdf_path}")
        return pdf_path


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _make(cvcfg):
    try:
        from weasyprint import CSS, HTML
    except ImportError as e:  # fail loudly at construction, naming the fix
        raise RuntimeError(
            "renderer 'weasyprint' requires the render extra: pip install 'sluice[render]'"
        ) from e
    return WeasyPrintRenderer(HTML, CSS)


register("weasyprint", _make)
