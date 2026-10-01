"""sluice.yaml.example must document only keys that actually exist.

The config loaders apply `if hasattr(cfg, k)`, so a key that no dataclass defines
is silently ignored -- a user copies it, believes it took effect, and it does
nothing. That is exactly how `strong_model` survived in the example while being
read by no config class at all.
"""
import pathlib
import re

import pytest
import yaml

from sluice.apply.config import ApplyConfig
from sluice.core.config import RETIRED_BACKEND_KEYS as _RETIRED
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


# The reference doc's section heading for each config class, keyed by class name. The ROOT class
# is read by `load_config`; the other four by their own loaders, from a top-level block of the
# same file.
_REFERENCE_SECTIONS = {
    "Config": "## Root",
    "TriageConfig": "## `triage:`",
    "CvConfig": "## `cv:`",
    "ApplyConfig": "## `apply:`",
    "TrackConfig": "## `track:`",
}
# Rows that name no field, each for a stated reason. Any OTHER such row is a key the doc
# advertises and no loader reads -- how `strong_model` outlived its field.
_ROWS_WITHOUT_A_FIELD = {
    ("## Root", "locations"): "retired; setting it raises, and the row says what replaced it",
    ("## `triage:`", "dossier_concurrency"): "retired marker pointing at the root key",
}
# #333's retired backend keys each keep a row saying what replaced them. Derived from the
# loader's own refusal table rather than hand-listed, so a key added to (or dropped from)
# that table moves its exemption with it.
_ROWS_WITHOUT_A_FIELD.update({
    (f"## `{block}:`", key): "retired by #333; loading it raises, and the row names the fix"
    for block, keys in _RETIRED.items() for key in keys})
# A config class with no section of its own, and where its fields ARE documented instead.
_DOCUMENTED_ELSEWHERE = {
    "SourceConfig": "one entry of the root `sources` mapping, documented in that key's row",
}


def _is_dataclass_decorator(dec):
    """`@dataclass`, `@dataclass(frozen=True)`, `@dataclasses.dataclass` and
    `@dataclasses.dataclass(...)` alike: the decorator's NAME after unwrapping a call and an
    attribute. Matching the source text instead found only the bare spelling, so a class
    declared any other way was invisible to both sides of the roster check below."""
    import ast
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Attribute):
        return dec.attr == "dataclass"
    return isinstance(dec, ast.Name) and dec.id == "dataclass"


def _config_classes():
    """Every `*Config` dataclass under sluice/, DISCOVERED rather than listed, so a new one
    fails the guard below until it is given a section or a named exemption."""
    import ast
    import importlib
    found = {}
    for path in (EXAMPLE.parent / "sluice").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.ClassDef) and node.name.endswith("Config")
                    and any(_is_dataclass_decorator(d) for d in node.decorator_list)):
                mod = ".".join(path.relative_to(EXAMPLE.parent).with_suffix("").parts)
                found[node.name] = getattr(importlib.import_module(mod), node.name)
    return found


def _reference_rows(text, heading):
    """The keys named in the FIRST cell of each table row under `heading`, up to the next
    `## ` heading -- the first cell, not anywhere in the section, so a field mentioned only in
    another key's prose does not count as documented."""
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    keys = set()
    for line in text[start:end if end != -1 else None].splitlines():
        if line.startswith("| `"):
            keys.update(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    return keys


def test_every_config_field_has_a_row_in_its_own_reference_section():
    """The reverse of `test_every_example_key_binds_to_a_real_config_field`: a field with no
    row is a knob a user cannot find. `sluice.yaml.example` is a partial catalogue by design,
    so the complete list lives in docs/CONFIGURATION.md and is asserted there."""
    import dataclasses

    classes = _config_classes()
    assert set(classes) == set(_REFERENCE_SECTIONS) | set(_DOCUMENTED_ELSEWHERE), (
        f"config classes {sorted(classes)} do not match the sections "
        f"{sorted(_REFERENCE_SECTIONS)} plus the exemptions {sorted(_DOCUMENTED_ELSEWHERE)}: "
        "give a new class its own section of docs/CONFIGURATION.md, or say where it is documented")
    text = (EXAMPLE.parent / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    missing, swept = {}, 0
    for cls, heading in _REFERENCE_SECTIONS.items():
        names = {f.name for f in dataclasses.fields(classes[cls])}
        rows = _reference_rows(text, heading)
        assert rows, f"no table rows found under {heading!r}: the section parse is broken"
        swept += len(names)
        gap = sorted(names - rows)
        if gap:
            missing[heading] = gap
        stale = sorted(k for k in rows - names if (heading, k) not in _ROWS_WITHOUT_A_FIELD)
        if stale:
            missing[f"{heading} (rows naming no field)"] = stale
    assert swept > 50, f"swept only {swept} fields: the class roster is broken"
    assert not missing, (
        f"docs/CONFIGURATION.md and the config classes disagree: {missing}. A field with no row "
        "needs one; a row naming no field is stale, or belongs in _ROWS_WITHOUT_A_FIELD")


@pytest.mark.parametrize("spelling, matched", [
    ("@dataclass", True),
    ("@dataclass(frozen=True)", True),
    ("@dataclasses.dataclass", True),
    ("@dataclasses.dataclass(slots=True)", True),
    ("@functools.cache", False),
])
def test_the_class_discovery_matches_every_dataclass_spelling(spelling, matched):
    import ast
    tree = ast.parse(f"{spelling}\nclass ExampleConfig:\n    x: int = 0\n")
    assert _is_dataclass_decorator(tree.body[0].decorator_list[0]) is matched
