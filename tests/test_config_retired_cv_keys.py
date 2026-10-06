"""D11 (#364/#365/#368): the retired `baseline_rel` and `cv.employers` keys RAISE, naming
the CV Layout, from `load_config` -- so every command stops on them, not only `cv`. Neither
value is echoed: an error travels further than the config file it came from."""
import pytest

from sluice.core.config import load_config
from sluice.core.protocols import CV_LAYOUT_RELPATH


@pytest.mark.parametrize("text,key", [
    ("baseline_rel: My CV/Example CV.md\n", "baseline_rel"),
    ("cv:\n  employers: [Example Alpha]\n", "cv.employers"),
], ids=["root-baseline_rel", "cv.employers"])
def test_a_retired_key_stops_every_command_naming_the_cv_layout(tmp_path, text, key):
    path = tmp_path / "sluice.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(str(path))
    message = str(exc.value)
    assert key in message and CV_LAYOUT_RELPATH in message
    assert "Example" not in message, "a retired key's value is never echoed"


def test_a_clean_config_loads(tmp_path):
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  fabrication_decoys: [examplelang]\n", encoding="utf-8")
    load_config(str(path))


def test_the_refusal_stops_a_command_that_has_nothing_to_do_with_cvs(tmp_path, monkeypatch,
                                                                    capsys):
    from sluice.cli import main
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  employers: [Example Alpha]\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(path))
    assert main(["ingest", "list-sources"]) != 0
    assert "cv.employers" in capsys.readouterr().err


def test_cv_baseline_rel_names_the_cv_layout_and_echoes_nothing(tmp_path):
    from sluice.cv.config import load_cv_config
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  baseline_rel: My CV/Example CV.md\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(path))
    assert CV_LAYOUT_RELPATH in str(exc.value) and "Example CV" not in str(exc.value)


def test_load_cv_config_refuses_cv_employers_on_its_own(tmp_path):
    # load_cv_config makes the refusal itself (sluice/cv/config.py), not only load_config:
    # its hasattr filter would otherwise drop `employers` in silence for a caller that
    # reaches it directly. Called directly here, so load_config cannot be what refuses.
    from sluice.cv.config import load_cv_config
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  employers: [Example Alpha]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(path))
    message = str(exc.value)
    assert "cv.employers" in message and CV_LAYOUT_RELPATH in message
    assert "Example Alpha" not in message, "a retired key's value is never echoed"


def test_an_unmatchable_decoy_is_refused_at_load_by_its_position(tmp_path):
    from sluice.cv.config import load_cv_config
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  fabrication_decoys: [examplelang, Exämple]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(path))
    assert "entry 2" in str(exc.value) and "Exämple" not in str(exc.value)
