"""The external render script, registered as the `script` renderer.

The escape hatch, selected by `cv.renderer: script`: `template` is the default
(cv/config.py::CvConfig.renderer). It hands an operator's own script the canonical text
form (cv/document.py::to_text), so an operator who already has a working script keeps it.

It does now fail LOUDLY AT CONSTRUCTION when the script is missing. That is not
pedantry: `CvConfig.render_script` defaults to `./scripts/cv_render_v2.py`, and **that
file does not exist in this repository**. Every fresh clone's `sluice cv run` therefore
composes a CV, passes it through the fabrication gate, and only then dies at the last
step -- after the LLM spend, and with an error that points at a subprocess rather than at
the config. Checking at construction turns a confusing late failure into a clear early
one, and tells the user their two real options.
"""
import os

# RE-EXPORTED, not defined here. `RenderError` is the Renderer seam's error type and now
# lives beside the protocol that documents it (`core/protocols.py`), the same way
# `VaultConflict` lives beside `Store`. It was defined in this module only because this
# was the first renderer; `renderers/template.py` and `core/app.py` then imported it from
# here, which made an implementation module the home of a contract type and gave `core/`
# its one and only import from an implementation package. Kept importable under the old
# name so no existing call site had to move for a pure relocation.
from sluice.core.protocols import CvDocument, RenderError
from sluice.renderers import register

__all__ = ["RenderError", "ScriptRenderer"]


class ScriptRenderer:
    def __init__(self, script: str, *, python_bin: str, home: str):
        # isfile, not exists: exists() is True for a DIRECTORY, so a render_script pointing
        # at one would pass construction and fail later in the subprocess -- defeating the
        # entire point of checking at construction.
        if not script or not os.path.isfile(script):
            raise RenderError(
                f"renderer 'script': render_script is not a file: '{script}'. "
                f"Set cv.render_script to your WeasyPrint script, or switch to the "
                f"bundled renderer with cv.renderer: template "
                f"(pip install 'job-sluice[render]')."
            )
        self.script, self.python_bin, self.home = script, python_bin, home

    def render(self, document, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        """Write the document in its canonical text form and hand it to the render script;
        return the PDF path. Anything but a `CvDocument` raises `RenderError`."""
        from sluice.cv.render import render as _render
        # A CvDocument is written in the canonical text format (cv/document.py::to_text),
        # citation-free: exactly what a script received from the old pipeline, pinned by
        # tests/test_cv_script_golden.py.
        if not isinstance(document, CvDocument):
            # Fail loud, never coerce: str() of a wrong object would hand the script junk,
            # and composed text is no longer a CV this renderer is given.
            raise RenderError(
                f"renderer 'script': render() takes a CvDocument, "
                f"got {type(document).__name__}")
        from sluice.cv.document import to_text
        document = to_text(document)
        return _render(document, out_dir, render_script=self.script,
                       python_bin=self.python_bin, home=self.home,
                       neutral_name=neutral_name)


def _make(cvcfg):
    return ScriptRenderer(cvcfg.render_script, python_bin=cvcfg.render_python,
                          home=cvcfg.render_home)


register("script", _make)
