"""The renderer seam.

A renderer is only ever reached PAST the fabrication gate. Nothing here may validate,
and nothing here may see a CV with outstanding violations -- that is cv/engine.py's job
and these tests exist partly to keep it that way.
"""
import pytest

from sluice.core.app import Sluice
from sluice.cv.config import CvConfig
from sluice.renderers.script import RenderError, ScriptRenderer


def test_script_renderer_raises_at_construction_when_the_script_is_missing(tmp_path):
    """Fail loudly at construction, not at call time.

    CvConfig.render_script defaults to ./scripts/cv_render_v2.py, WHICH DOES NOT EXIST IN
    THIS REPO. Before this guard, every fresh clone's `sluice cv run` composed a CV, spent
    the LLM tokens, passed the fabrication gate, and only THEN died inside a subprocess --
    with an error pointing at the subprocess rather than at the config.
    """
    with pytest.raises(RenderError, match="render_script is not a file"):
        ScriptRenderer(str(tmp_path / "nope.py"), python_bin="/usr/bin/python3",
                       home=str(tmp_path))


def test_script_renderer_error_names_both_ways_out(tmp_path):
    # An error that does not tell you the fix is a worse error.
    with pytest.raises(RenderError) as e:
        ScriptRenderer(str(tmp_path / "nope.py"), python_bin="x", home="y")
    msg = str(e.value)
    assert "cv.render_script" in msg          # supply your own script...
    assert "cv.renderer: template" in msg     # ...or switch to the bundled one


def test_script_renderer_constructs_when_the_script_exists(tmp_path):
    script = tmp_path / "render.py"
    script.write_text("# a render script\n")
    r = ScriptRenderer(str(script), python_bin="/usr/bin/python3", home=str(tmp_path))
    assert r.script == str(script)


def test_the_renderer_seam_is_selected_by_name():
    # Membership, not equality -- `template` replaces `weasyprint` here, but this line
    # stays (spec's Testing section) rather than being deleted, so a future removal of
    # `script` or `template` from the seam still fails a test at this exact spot.
    assert {"script", "template"} <= set(Sluice.available("renderer"))


def test_unknown_renderer_raises_rather_than_defaulting():
    from sluice.core import plugins
    cfg = CvConfig()
    cfg.renderer = "latex"
    with pytest.raises(plugins.UnknownAdapter) as e:
        Sluice(None).renderer(cfg)
    assert "latex" in str(e.value) and "script" in str(e.value)


def test_script_renderer_rejects_a_directory(tmp_path):
    """os.path.exists() is True for a directory. A render_script pointing at one would have
    passed construction and died later in the subprocess -- which is exactly the late,
    confusing failure this guard exists to convert into an early, clear one."""
    d = tmp_path / "not_a_script"
    d.mkdir()
    with pytest.raises(RenderError, match="not a file"):
        ScriptRenderer(str(d), python_bin="/usr/bin/python3", home=str(tmp_path))
