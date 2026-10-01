"""#333's config shape: one `backend` + `model` per stage, a root `backend_retries`, and a
dedicated tier-3 `resolve_backend`/`resolve_model` -- with every retired key REFUSED.

Refused rather than dropped because each sub-app loader filters unknown keys with `hasattr`:
a silently-dropped `fallback_backend` would leave its owner believing a second provider still
stands behind the first, which is the exact belief #333 removes.
"""
import dataclasses

import pytest

from sluice.core.config import RETIRED_BACKEND_KEYS, Config, load_config
from sluice.cv.config import CvConfig, load_cv_config
from sluice.track.config import TrackConfig, load_track_config
from sluice.triage.config import TriageConfig, load_triage_config

_LOADERS = {"triage": load_triage_config, "track": load_track_config, "cv": load_cv_config}
_CLASSES = {"triage": TriageConfig, "track": TrackConfig, "cv": CvConfig}


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    def write(text):
        p = tmp_path / "c.yaml"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("SLUICE_CONFIG", str(p))
    return write


def _rows():
    return [(b, k) for b, keys in RETIRED_BACKEND_KEYS.items() for k in keys]


def test_the_retired_roster_covers_all_three_stages():
    # Anti-vacuity: a roster that lost a block would sweep nothing for it and stay green.
    assert set(RETIRED_BACKEND_KEYS) == set(_LOADERS)
    assert all(RETIRED_BACKEND_KEYS[b] for b in RETIRED_BACKEND_KEYS)


@pytest.mark.parametrize("block,key", _rows())
def test_every_retired_backend_key_raises_naming_its_replacement(block, key, write_config):
    write_config(f"{block}:\n  {key}: SECRET-VALUE\n")
    with pytest.raises(ValueError) as ei:
        _LOADERS[block]()
    msg = str(ei.value)
    assert f"{block}.{key}" in msg
    assert "SECRET-VALUE" not in msg
    new = RETIRED_BACKEND_KEYS[block][key]
    if new:
        assert f"{block}.{new}" in msg


@pytest.mark.parametrize("block", sorted(RETIRED_BACKEND_KEYS))
def test_a_retired_key_raises_even_beside_its_replacement(block, write_config):
    old = next(k for k, v in RETIRED_BACKEND_KEYS[block].items() if v == "backend")
    write_config(f"{block}:\n  backend: deepseek\n  {old}: deepseek\n")
    with pytest.raises(ValueError, match=f"{block}.{old}"):
        _LOADERS[block]()


@pytest.mark.parametrize("block,key", _rows())
def test_no_retired_key_is_still_a_dataclass_field(block, key):
    # A re-added field would make the loader's `hasattr` filter ACCEPT the key again -- and
    # the refusal fires before the overlay, so only this row would notice the regression.
    assert key not in {f.name for f in dataclasses.fields(_CLASSES[block])}


@pytest.mark.parametrize("block", sorted(_LOADERS))
def test_the_new_keys_load(block, write_config):
    write_config(f"{block}:\n  backend: deepseek\n  model: m1\n")
    c = _LOADERS[block]()
    assert (c.backend, c.model) == ("deepseek", "m1")


@pytest.mark.parametrize("block", sorted(_LOADERS))
def test_shipped_defaults_are_unchanged_in_substance(block):
    c = _CLASSES[block]()
    assert (c.backend, c.model) == ("claude-max", "claude-sonnet-4-5")


def test_tier3_keys_default_empty_and_load(write_config):
    assert (TriageConfig().resolve_backend, TriageConfig().resolve_model) == ("", "")
    write_config("triage:\n  resolve_backend: openai\n  resolve_model: m2\n")
    t = load_triage_config()
    assert (t.resolve_backend, t.resolve_model) == ("openai", "m2")


@pytest.mark.parametrize("bad", ["true", "yes", "-1", "1.5", "two"])
def test_backend_retries_rejects_a_non_count(bad, write_config):
    write_config(f"backend_retries: {bad}\n")
    with pytest.raises(ValueError, match="backend_retries"):
        load_config(None)


def test_backend_retries_defaults_to_two_and_accepts_zero(write_config, monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert Config().backend_retries == 2
    assert load_config(None).backend_retries == 2
    write_config("backend_retries: 0\n")
    assert load_config(None).backend_retries == 0
