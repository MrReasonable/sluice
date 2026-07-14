# tests/test_cv_config.py
import textwrap

import pytest

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

def test_legacy_cv_baseline_rel_raises_rather_than_dropping_silently(tmp_path):
    # baseline_rel MOVED from `cv:` to the root config (only the store can honour it). The
    # loader filters unknown keys with `hasattr`, so a still-present `cv.baseline_rel` would
    # be dropped in silence -- and it was LIVE before the move, so a user with a curated
    # baseline would quietly get a CV composed from the stale default `My CV/CV.md`, with the
    # fabrication gate green (it checks bullets against cited entries, not the baseline's
    # employers/dates). This asserts that quiet-drop is a loud raise, per the codebase's
    # fail-at-construction rule. Without it, simplifying the loader back to a plain hasattr
    # filter would reintroduce the silent stale-baseline with nothing going red.
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent('''
    cv:
      baseline_rel: "My CV/Curated.md"
    '''))
    with pytest.raises(ValueError) as e:
        load_cv_config(path=str(p))
    msg = str(e.value)
    assert "baseline_rel" in msg
    assert "top level" in msg, "the error must tell the operator where to move the key"
