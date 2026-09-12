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


def test_the_example_advertises_dossier_concurrency_at_the_ROOT_not_under_triage():
    """#309's key moved to root, and the catalogue must not teach the retired spelling.

    `load_triage_config` HARD-RAISES on `triage.dossier_concurrency`, so an example that
    showed it there would hand a reader a file that cannot load -- the worst kind of
    catalogue error, because the example is what people copy from.

    Read as TEXT, deliberately. Every other check in this file goes through
    `yaml.safe_load`, and the example ships this key COMMENTED (it is opt-in), so a parsed
    view cannot see it at all: moving the commented block back under `triage:` left the
    whole suite green. Indentation is the whole signal here, so indentation is what is
    asserted.
    """
    text = EXAMPLE.read_text(encoding="utf-8")
    # Matches the key DECLARATION whether or not it is commented. Requiring a "#" was
    # a hole: an UNCOMMENTED `triage:\n  dossier_concurrency: 4` would be skipped by
    # the filter while the commented root line still satisfied `hits`, so this row
    # passed on an example that `load_triage_config` hard-rejects.
    hits = [l for l in text.splitlines()
            if l.strip().lstrip("#").strip().startswith("dossier_concurrency:")]
    # SCOPE: if the key ever stops being mentioned, this row must fail rather than pass by
    # sweeping nothing -- the `all([])` case.
    assert hits, "sluice.yaml.example no longer mentions dossier_concurrency at all"
    for line in hits:
        # strip WHITESPACE first: an indented `  # key:` does not start with "#", so
        # lstrip("#") alone leaves the marker on and the key never matches. That bug
        # made this row inert against the very mutation it exists for -- caught by
        # running it, not by reading it.
        body = line.strip().lstrip("#").strip()
        if not body.startswith("dossier_concurrency"):
            continue                      # prose mentioning the key, not the key itself
        assert not line.startswith(" "), (
            f"the example shows dossier_concurrency INDENTED, i.e. under a sub-app block: "
            f"{line!r}. It is a root key, and `triage.dossier_concurrency` now raises.")


def test_the_config_reference_lists_dossier_concurrency_under_root():
    """The docs table equivalent of the row above: the key is documented at root, and the
    `triage:` table carries only a retired marker pointing there."""
    text = (EXAMPLE.parent / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    root_start = text.index("## Root")
    triage_start = text.index("## `triage:`")
    root_block, triage_block = text[root_start:triage_start], text[triage_start:]
    assert "| `dossier_concurrency` |" in root_block, "not documented under Root"
    triage_rows = [l for l in triage_block.splitlines() if l.startswith("| `dossier_concurrency`")]
    assert triage_rows, "the retired marker is gone from the triage: table"
    assert "retired" in triage_rows[0].lower(), (
        f"the triage: table documents dossier_concurrency as live: {triage_rows[0]!r}")
