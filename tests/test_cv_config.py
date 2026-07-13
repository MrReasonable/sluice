# tests/test_cv_config.py
import textwrap
from sluice.cv.config import load_cv_config

def test_defaults_run_without_a_file():
    cfg = load_cv_config(path=None)
    assert cfg.name == "Your Name"
    assert cfg.neutral_filename == "CV.pdf"
    assert cfg.prefix_map == {}    # no employer codes ship by default; supply your own
    assert cfg.negatives == []     # no fact-check negatives ship by default

def test_yaml_overrides_cv_block(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent('''
    cv:
      neutral_filename: "CV.pdf"
      ttl_days: 3
    '''))
    cfg = load_cv_config(path=str(p))
    assert cfg.neutral_filename == "CV.pdf"
    assert cfg.ttl_days == 3
    assert cfg.prefix_map == {}   # untouched default (still empty, no yaml override given)
