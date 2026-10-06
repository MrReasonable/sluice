"""#364 spec §12.2: every CV Layout sample in the shipped docs is a TEMPLATE, not a layout. A
verbatim copy must refuse at every placeholder rather than render one, and the identities a
sample shows are reviewed or `Example`-shaped, since a layout is an employment history."""
import pathlib
import re

import pytest
import yaml

from sluice.core.layout import parse_layout
from sluice.core.protocols import LayoutError

_DOCS = [pathlib.Path("README.md"), *sorted(pathlib.Path("docs").glob("*.md"))]
_FENCE = re.compile(r"^```ya?ml\n(.*?)^```", re.M | re.S)
_PLACEHOLDER = re.compile(r"<[^<>]+>")


def _samples():
    """(doc name, mapping) for every fenced YAML block with a top-level `roles:` key. The
    note's own `---` frontmatter fences are dropped first: kept, PyYAML reads two documents."""
    out = []
    for path in _DOCS:
        for block in _FENCE.findall(path.read_text(encoding="utf-8")):
            body = "\n".join(ln for ln in block.splitlines() if ln.strip() != "---")
            try:
                data = yaml.safe_load(body)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and "roles" in data:
                out.append((path.name, data))
    return out


def _placeholder_paths(value, path=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _placeholder_paths(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            yield from _placeholder_paths(inner, f"{path}[{i}]")
    elif isinstance(value, str) and _PLACEHOLDER.search(value):
        yield path


def test_the_docs_show_a_layout_sample():
    # The must-find floor: a sweep that found no sample would pass every row below.
    assert _samples(), "no CV Layout sample in README.md or docs/*.md"


def test_every_layout_sample_refuses_at_every_placeholder():
    for name, data in _samples():
        paths = list(_placeholder_paths(data))
        assert paths, f"{name}: a sample with no placeholder would load as a real layout"
        with pytest.raises(LayoutError) as exc:
            parse_layout(data)
        for p in paths:
            assert any(problem.startswith(f"{p}:") for problem in exc.value.problems), (
                f"{name}: the placeholder at {p} is not refused by name")


def test_layout_samples_show_only_reviewed_or_example_shaped_identities():
    from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES
    for name, data in _samples():
        for role in data.get("roles") or []:
            for value in [role.get("heading"), *(role.get("employers") or [])]:
                if value is None or _PLACEHOLDER.search(str(value)):
                    continue
                assert (str(value).startswith("Example")
                        or value in _REVIEWED_FIXTURE_IDENTITIES), (name, value)


def test_a_samples_education_items_are_one_qualification_each():
    # #364 spec §12.2: one item per qualification, in the shape the sample shows -- dates then
    # ONE qualification after the meta separator -- so a copy is written a line per item.
    for name, data in _samples():
        for item in data.get("education") or []:
            assert str(item).count("|") == 1, (name, item)
