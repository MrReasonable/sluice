"""A recording renderer registered through the real `renderer` seam.

RECORDING, not discarding: "no CV was rendered when the gate failed" is only
assertable if we keep every `cv_text` the engine handed a renderer. A no-op
renderer makes that assertion vacuous -- the exact hole `test_cv_engine.py`'s
`FakeRenderer` was written to close, here at the composition-root level.

It also writes a minimal real PDF to disk and returns its path, so the
downstream apply hop -- which stages the served file and %PDF-verifies it -- runs
for real rather than being stubbed. The bytes only need the `%PDF` magic that
`apply/cvfile.py::_is_pdf` checks; a valid one-page document is unnecessary.
"""
import os

from sluice.core import plugins

RENDERER_SEAM = "renderer"
RENDERER_NAME = "recording"

_MINIMAL_PDF = b"%PDF-1.4\n%%EOF\n"


class Recorder:
    """The shared sink a test inspects after a run."""

    def __init__(self):
        self.rendered: list[str] = []   # every cv_text the engine asked to render
        self.paths: list[str] = []      # every output path returned


class RecordingRenderer:
    def __init__(self, recorder):
        self.recorder = recorder

    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        self.recorder.rendered.append(cv_text)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, neutral_name)
        with open(path, "wb") as f:
            f.write(_MINIMAL_PDF)
        self.recorder.paths.append(path)
        return path


def install_recording_renderer(recorder):
    """Register a recording renderer under the real `renderer` seam as
    `recording`. Config selects it via `cv.renderer: recording`, so `plugins.get`
    and that config key are exercised. The `renderer` registry asserts a SUBSET
    (`{"script","weasyprint"} <= available`), so an extra name is safe."""
    plugins.register(RENDERER_SEAM, RENDERER_NAME,
                     lambda cvcfg: RecordingRenderer(recorder))
    return recorder
