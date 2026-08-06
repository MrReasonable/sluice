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

    def precheck(self, cv_text):
        """The seam's OPTIONAL grammar hook, mirroring `renderers/template.py`'s.

        Declared DELIBERATELY, and the reason is a measured hole rather than symmetry:
        this fake is the renderer every e2e and functional test resolves through the real
        composition root, and while it had no `precheck` the engine's
        `getattr(renderer, "precheck", None)` was None on every one of those runs. The
        grammar check this whole change exists to add was therefore dead code at the only
        layer that exercises the full wiring -- the same shape as the seam inversion the
        branch was written to fix.

        Mirrors the real one exactly (parse, report a SHAPE failure as a `FORMAT:` string)
        rather than returning `[]`: a hook that always answers "nothing to say" would keep
        the call live and the CHECK dead, which is the worse of the two failures because
        it looks covered. `tests/harness/config.py`'s PASSING_CV parses clean, so this
        changes no existing expectation -- verified by running the suite. And the call is
        genuinely REACHED rather than merely declared: mutating this method to raise turns
        four tests under tests/e2e/ and tests/functional/ red, which is what makes the
        paragraph above a measurement instead of an intention.

        `script`'s deliberate absence of a counterpart is covered by
        `tests/test_cv_engine.py::test_a_renderer_without_precheck_is_not_gated_by_
        another_renderers_grammar`, whose own fake declares no hook; that property does
        not need this one to stay hookless too.
        """
        from sluice.cv.parse import CvParseError, parse_cv
        try:
            parse_cv(cv_text)
        except CvParseError as e:
            return [f"FORMAT: {e}"]
        return []

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
    (`{"script","template"} <= available`), so an extra name is safe."""
    plugins.register(RENDERER_SEAM, RENDERER_NAME,
                     lambda cvcfg: RecordingRenderer(recorder))
    return recorder
