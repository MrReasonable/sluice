# tests/test_cv_config.py
import textwrap

import pytest

from sluice.cv.config import CvConfig, load_cv_config

def test_defaults_run_without_a_file():
    cfg = load_cv_config(path=None)
    assert cfg.name == "Your Name"
    assert cfg.neutral_filename == "CV.pdf"
    assert cfg.prefix_map == {}    # no employer codes ship by default; supply your own
    assert cfg.negatives == []     # no fact-check negatives ship by default

def test_require_signoff_defaults_true():
    # The profile audit sign-off gate (#60) ships LIVE: an `unsupported` qualitative
    # claim withholds the send-ready pointer until a human signs off. It is a safety
    # valve, not a job preference, so it defaults on (empty-config-abstains does not
    # bind); set it False to restore the old auto-serve. Pinned so a default flip
    # goes red loudly -- and the #60 mutation witness points here.
    from sluice.cv.config import CvConfig
    assert CvConfig().require_signoff is True

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


# ── #28: the compose timeout is a config knob, not a buried literal ──────────────


def test_compose_timeout_defaults_to_the_current_behaviour():
    """300s was the hardcoded value; making it configurable must not silently retune it.

    Changing the DEFAULT is a separate judgement from making it reachable, and this pins
    that the knob's arrival changed nobody's runtime.
    """
    assert CvConfig().compose_timeout == 300


def test_compose_timeout_is_read_from_the_cv_block(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("cv:\n  compose_timeout: 900\n", encoding="utf-8")
    assert load_cv_config(str(p)).compose_timeout == 900


def test_compose_timeout_rejects_a_yaml_bool(tmp_path):
    """`bool` subclasses `int`, and PyYAML resolves `yes`/`on`/`true` to True.

    `compose_timeout: yes` -- a plausible thing to type -- would otherwise load as True,
    which IS 1, giving every composition a ONE SECOND budget. Every lead would then time
    out and degrade to the fallback, with no error naming the cause. Same hazard, and the
    same bool-before-int ordering, as `lead_ttl_days` at the root config.
    """
    p = tmp_path / "s.yaml"
    p.write_text("cv:\n  compose_timeout: yes\n", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        load_cv_config(str(p))
    assert "compose_timeout" in str(ei.value)


@pytest.mark.parametrize("bad", ["0", "-5", '"abc"'], ids=["zero", "negative", "string"])
def test_compose_timeout_rejects_a_nonpositive_or_non_integer(tmp_path, bad):
    """0 and negatives are not 'off' here -- there is no off. `subprocess.run(timeout=0)`
    kills every call instantly, which is a broken install wearing a config's clothes.
    Unlike `lead_ttl_days`, this knob has no abstain value, so it fails loudly instead.
    """
    p = tmp_path / "s.yaml"
    p.write_text(f"cv:\n  compose_timeout: {bad}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_cv_config(str(p))
