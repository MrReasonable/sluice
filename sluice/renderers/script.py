"""The external render script, registered as the `script` renderer.

This is today's behaviour and stays the default, so nothing changes for an operator who
already has a working script.

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
from sluice.core.protocols import RenderError
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

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        from sluice.cv.render import render as _render
        return _render(cv_text, out_dir, render_script=self.script,
                       python_bin=self.python_bin, home=self.home,
                       neutral_name=neutral_name)


def _make(cvcfg):
    return ScriptRenderer(cvcfg.render_script, python_bin=cvcfg.render_python,
                          home=cvcfg.render_home)


register("script", _make)
