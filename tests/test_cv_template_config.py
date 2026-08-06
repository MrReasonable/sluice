"""`cv.template` and the two migration refusals (spec: Config, Migration)."""
import pytest

from sluice.core import plugins
from sluice.core.app import Sluice
from sluice.cv.config import CvConfig, load_cv_config


def test_cv_template_default_is_blank():
    """`cv.template` is a `str`, so it is INVISIBLE to
    tests/test_sluice_neutral_defaults.py's list-keyed sweep -- it needs its own named
    guard, exactly as `lead_layout` does for the same reason.

    Blank is load-bearing: a non-empty default is truthy, short-circuits the resolution
    chain, and makes the packaged template unreachable while nothing goes red.
    """
    assert CvConfig().template == ""


def test_the_renderer_default_is_template():
    assert CvConfig().renderer == "template", (
        "the default renderer must be `template`: `script`'s default render_script has "
        "never existed in this repository, so no operator can be relying on it")


def test_render_script_without_an_explicit_renderer_is_refused(tmp_path, monkeypatch):
    """The ONE case that could silently change an operator's output: they set
    render_script and relied on the `script` default. Not auto-detected and not quietly
    reinterpreted -- an implicit coupling between two keys is its own quiet wrong default.
    """
    p = tmp_path / "sluice.yaml"
    p.write_text("cv:\n  render_script: ./my_render.py\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="cv.renderer: script"):
        load_cv_config(str(p))


def test_render_script_with_an_explicit_renderer_is_accepted(tmp_path, monkeypatch):
    """The refusal must be reachable ONLY by the ambiguous case -- otherwise it is a
    guard that refuses everything and proves nothing."""
    p = tmp_path / "sluice.yaml"
    p.write_text("cv:\n  renderer: script\n  render_script: ./my_render.py\n",
                 encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    cfg = load_cv_config(str(p))
    assert cfg.renderer == "script" and cfg.render_script == "./my_render.py"


def test_a_valueless_render_script_does_not_trip_the_migration_guard(tmp_path, monkeypatch):
    """`render_script:` with nothing after it is YAML null, and a commented-out or
    half-edited value is an ordinary thing to leave in a config file.

    The guard keyed on `"render_script" in data`, which is True for None -- so a file
    that sets NOTHING raised, telling the operator to add `cv.renderer: script` to keep
    a render script they had already removed. The setattr loop skips None, so a valueless
    key must load exactly like an absent one; that contract is why
    `test_a_valueless_key_never_becomes_None` exists, and this guard sat outside it
    because it runs BEFORE the loop.
    """
    p = tmp_path / "sluice.yaml"
    p.write_text("cv:\n  render_script:\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    cfg = load_cv_config(str(p))
    assert cfg.renderer == "template", "an unset key changed the default renderer"
    assert cfg.render_script == CvConfig().render_script, (
        "a valueless key overwrote the code default instead of being skipped")


def test_selecting_the_retired_weasyprint_name_names_template():
    """A BARE registry removal cannot produce this message: `plugins.get`'s unknown-name
    error lists the VALID names and would never mention `template`, so "raises, naming
    template as the replacement" would be an empty promise. The retired name needs a
    deliberate branch."""
    cfg = CvConfig()
    cfg.renderer = "weasyprint"
    with pytest.raises(plugins.UnknownAdapter) as e:
        Sluice(None).renderer(cfg)
    msg = str(e.value)
    # NOT `"template" in msg`: `template` is itself a currently-registered renderer name,
    # so it already appears in UnknownAdapter's base "(registered: ...)" list with NO
    # hint at all -- measured by mutation testing `plugins.register_retired(...)`'s
    # deletion: that weaker assertion stayed GREEN with the retired-name branch entirely
    # gone, which is exactly the inert-guard failure mode this suite exists to catch.
    # Assert wording that can only come from the retirement HINT itself.
    assert "renders your own Jinja2 template" in msg, (
        "the migration message does not name the replacement")


def test_a_retired_name_is_not_offered_as_a_choice():
    """`sluice init` derives its renderer choices FROM the registry. A retired name that
    stayed registered would keep being offered -- so retirement must not be implemented
    as a factory that raises."""
    assert "weasyprint" not in Sluice.available("renderer")
    assert "template" in Sluice.available("renderer")
