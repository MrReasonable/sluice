"""sluice.yaml.example must document only keys that actually exist.

The config loaders apply `if hasattr(cfg, k)`, so a key that no dataclass defines
is silently ignored -- a user copies it, believes it took effect, and it does
nothing. That is exactly how `strong_model` survived in the example while being
read by no config class at all.
"""
import pathlib

import yaml

from sluice.apply.config import ApplyConfig
from sluice.cv.config import CvConfig
from sluice.track.config import TrackConfig
from sluice.triage.config import TriageConfig

EXAMPLE = pathlib.Path(__file__).parent.parent / "sluice.yaml.example"

# Sections of the example that map onto a config dataclass. `locations` and
# `sources` are consumed by core.config instead, so they are not listed here.
SECTION_CLASSES = {
    "triage": TriageConfig,
    "cv": CvConfig,
    "apply": ApplyConfig,
    "track": TrackConfig,
}


def _example():
    return yaml.safe_load(EXAMPLE.read_text()) or {}


def test_every_example_key_binds_to_a_real_config_field():
    data = _example()
    unknown = []
    for section, cls in SECTION_CLASSES.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue  # section absent or commented out
        cfg = cls()
        unknown += [f"{section}.{k}" for k in block if not hasattr(cfg, k)]
    assert not unknown, (
        f"sluice.yaml.example documents keys no config class reads (the loader's "
        f"hasattr guard silently ignores them): {unknown}")


def test_example_model_ids_are_bare_not_provider_prefixed():
    # The value is sent verbatim as the API's `model` field -- the provider is
    # already chosen by *_backend -- so a "deepseek/" or "anthropic/" prefix 400s.
    data = _example()
    for section in SECTION_CLASSES:
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if key.endswith("_model") and isinstance(value, str):
                assert "/" not in value, (
                    f"{section}.{key}={value!r} is provider-prefixed; the API takes a "
                    f"bare model id and would reject this")
