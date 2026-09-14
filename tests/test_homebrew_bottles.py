"""Offline proof of every decision the Homebrew bottle channel makes (#279).

Design: docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 9a. A green dry run
only ever takes a check's accept branch, so every refuse branch is proven here, and every function
also has an accept row: a check that always refuses would otherwise pass every refuse row.

EVERY expected value is restated here by hand. Module-level `_EXPECTED*` constants are guarded by
`test_every_expected_constant_is_built_only_from_literals`, ported from
tests/test_homebrew_formula.py for the same reason: an expectation read out of the module under
test compares that module with itself.

Fixtures are synthetic: `example.invalid` URLs where the host is not the property under test, fake
digests, owner `ExampleOwner`. Resource-stanza URLs keep the files.pythonhosted.org host because
that host is exactly what the formula grammar checks. tests/homebrew_fixtures/ holds sanitised real
Homebrew output. Its formula keeps the renderer's `homepage` line, which names the upstream project's
owner, because the validator compares that text with `render()`. A change to the renderer's template
makes that fixture stale: recapture it on a Mac with a real `brew bottle --merge`, as
docs/superpowers/plans/2026-09-14-homebrew-bottles.md's first task did, never by hand.
"""
import ast
import json
import pathlib

import pytest

from scripts import homebrew_bottles as hb
from scripts.homebrew_bottles import Refusal

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "homebrew_bottles.py"
FIXTURES = ROOT / "tests" / "homebrew_fixtures"

_EXPECTED_PLATFORMS = {("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe")}

_PYPI_URL = "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz"


# --- push_target and VERSION -------------------------------------------------------------------


def test_push_target_accepts_default_and_auto():
    assert hb.validate_push_target("default") == "default"
    assert hb.validate_push_target("auto") == "auto"


@pytest.mark.parametrize("value", [None, "", "bogus", "Default", "AUTO", "default "])
def test_push_target_refuses_anything_else_and_names_both_values(value):
    with pytest.raises(Refusal) as err:
        hb.validate_push_target(value)
    assert "'default'" in str(err.value) and "'auto'" in str(err.value)


@pytest.mark.parametrize("value", ["0.0.1", "2.9.7", "2.10.0"])
def test_version_accepts_three_integers(value):
    assert hb.validate_version(value) == value


@pytest.mark.parametrize(
    "value", [None, "", "2.10", "2.10.0.1", "v2.10.0", "2.10.0rc1", " 2.10.0", "2.x.0"]
)
def test_version_refuses_anything_else(value):
    with pytest.raises(Refusal):
        hb.validate_version(value)


def test_version_tuple_orders_numerically_across_a_digit_boundary():
    assert hb.version_tuple("2.9.7") < hb.version_tuple("2.10.0")
    assert hb.version_tuple("2.10.0") == (2, 10, 0)


# --- PyPI sdist ----------------------------------------------------------------------------------


def test_sdist_accepts_the_pythonhosted_url_for_this_version():
    hb.validate_sdist("9.9.0", _PYPI_URL, "d" * 64)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz",
        _PYPI_URL.replace("9.9.0", "9.9.1"),
        _PYPI_URL.replace("job_sluice", "other_pkg"),
        _PYPI_URL + '"; system "x"',
        None,
    ],
)
def test_sdist_refuses_a_url_off_grammar_or_for_another_file(url):
    with pytest.raises(Refusal):
        hb.validate_sdist("9.9.0", url, "d" * 64)


@pytest.mark.parametrize("sha256", [None, "", "d" * 63, "D" * 64, "g" * 64])
def test_sdist_refuses_a_malformed_digest(sha256):
    with pytest.raises(Refusal):
        hb.validate_sdist("9.9.0", _PYPI_URL, sha256)


def test_pick_sdist_returns_the_one_sdist():
    data = {
        "urls": [
            {"packagetype": "bdist_wheel", "url": "https://example.invalid/w.whl"},
            {"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}},
        ]
    }
    assert hb.pick_sdist(data, "9.9.0") == (_PYPI_URL, "d" * 64)


@pytest.mark.parametrize("count", [0, 2])
def test_pick_sdist_refuses_zero_or_two_sdists(count):
    sdist = {"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}}
    with pytest.raises(Refusal):
        hb.pick_sdist({"urls": [sdist] * count}, "9.9.0")


# --- owner, tag and root URL ---------------------------------------------------------------------


def test_tap_owner_lower_cases():
    assert hb.tap_owner("ExampleOwner") == "exampleowner"


@pytest.mark.parametrize("owner", [None, "", "-lead", "has space", "a" * 40, "x/y"])
def test_tap_owner_refuses_a_non_account_name(owner):
    with pytest.raises(Refusal):
        hb.tap_owner(owner)


def test_tag_is_deterministic():
    assert hb.compose_tag("9.9.0", "123", "1") == "job-sluice-9.9.0-123-1"
    assert hb.compose_tag("9.9.0", "123", "1") == hb.compose_tag("9.9.0", "123", "1")


def test_tag_changes_with_the_run_id_alone():
    assert hb.compose_tag("9.9.0", "123", "1") != hb.compose_tag("9.9.0", "124", "1")


def test_tag_changes_with_the_run_attempt_alone():
    assert hb.compose_tag("9.9.0", "123", "1") != hb.compose_tag("9.9.0", "123", "2")


@pytest.mark.parametrize("run_id, attempt", [("", "1"), ("0", "1"), ("12", "0"), ("12", "x"), ("-1", "1")])
def test_tag_refuses_a_non_positive_run_id_or_attempt(run_id, attempt):
    with pytest.raises(Refusal):
        hb.compose_tag("9.9.0", run_id, attempt)


def test_root_url_lower_cases_the_owner():
    assert hb.compose_root_url("ExampleOwner", "job-sluice-9.9.0-1-1") == (
        "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1"
    )


# --- the tap: default branch, bootstrap observable, target -------------------------------------


def test_symref_reads_the_branch_and_tip():
    output = "ref: refs/heads/main\tHEAD\n" + "f" * 40 + "\tHEAD\n"
    assert hb.parse_symref(output) == ("main", "f" * 40)


@pytest.mark.parametrize(
    "output", ["", "ref: refs/heads/main\tHEAD\n", "f" * 40 + "\tHEAD\n", "garbage\n"]
)
def test_symref_refuses_output_missing_either_half(output):
    with pytest.raises(Refusal):
        hb.parse_symref(output)


@pytest.mark.parametrize(
    "status, state",
    [(200, "present"), (404, "absent"), (403, "error"), (500, "error"), (301, "error")],
)
def test_the_formula_state_is_three_valued(status, state):
    assert hb.formula_state_from_status(status) == state


@pytest.mark.parametrize("state", ["present", "absent", "error"])
def test_default_targets_the_default_branch_whatever_the_observable_says(state):
    assert hb.resolve_target("default", state, "main", "9.9.0") == "main"


def test_auto_with_the_formula_present_targets_a_scratch_branch():
    assert hb.resolve_target("auto", "present", "main", "9.9.0") == "bump-9.9.0"


def test_auto_with_the_formula_absent_targets_the_default_branch():
    assert hb.resolve_target("auto", "absent", "main", "9.9.0") == "main"


@pytest.mark.parametrize("state", ["error", "", "unknown"])
def test_auto_with_an_unknown_state_refuses(state):
    with pytest.raises(Refusal):
        hb.resolve_target("auto", state, "main", "9.9.0")


def test_the_target_refuses_an_invalid_push_target():
    with pytest.raises(Refusal):
        hb.resolve_target("bogus", "present", "main", "9.9.0")


def test_the_caller_is_release_or_dry_run():
    assert (hb.caller_for("default"), hb.caller_for("auto")) == ("release", "dry run")


# --- platforms -------------------------------------------------------------------------------------


def test_the_emitted_platforms_are_the_two_pairs_restated_by_hand():
    emitted = json.loads(hb.platforms_json())
    assert {(entry["runner"], entry["tag"]) for entry in emitted} == _EXPECTED_PLATFORMS
    assert len(emitted) == len(_EXPECTED_PLATFORMS)


def test_declared_tags_reads_the_emitted_platforms():
    assert sorted(hb.declared_tags(hb.platforms_json())) == ["arm64_sequoia", "arm64_tahoe"]


@pytest.mark.parametrize(
    "platforms",
    [
        "[]",
        '[{"runner": "macos-15"}]',
        '[{"runner": "a", "tag": "x"}, {"runner": "b", "tag": "x"}]',
        '{"tag": "x"}',
    ],
)
def test_declared_tags_refuses_an_empty_or_malformed_set(platforms):
    with pytest.raises(Refusal):
        hb.declared_tags(platforms)


# --- the expectations stay literal -------------------------------------------------------------

_LITERAL_CONTAINERS = (ast.List, ast.Tuple, ast.Set)


def _is_literal_expression(node, already_validated: set[str]) -> bool:
    """Is `node` built purely from literals (and already-validated `_EXPECTED*` names)?"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in already_validated
    if isinstance(node, _LITERAL_CONTAINERS):
        return all(_is_literal_expression(e, already_validated) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            k is not None
            and _is_literal_expression(k, already_validated)
            and _is_literal_expression(v, already_validated)
            for k, v in zip(node.keys, node.values)
        )
    return False


def test_every_expected_constant_is_built_only_from_literals():
    """Ported from tests/test_homebrew_formula.py::test_every_expected_constant_is_built_only_from_literals.

    This file imports the module under test, so `hb.PLATFORMS` is one attribute read away from an
    expectation. Refusing every `_EXPECTED*` right-hand side that is not built from literals keeps
    the restated pairs a second, independent source.
    """
    constants: dict[str, ast.expr] = {}
    for node in ast.parse(pathlib.Path(__file__).read_text()).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id] if node.value is not None else []
        else:
            continue
        for name in names:
            if name.startswith("_EXPECTED"):
                constants[name] = node.value
    assert constants, "no module-level `_EXPECTED*` constant found; the sweep below proves nothing"
    validated: set[str] = set()
    for name, value in constants.items():
        assert _is_literal_expression(value, validated), (
            f"`{name}` is not built purely from literals: {ast.dump(value)[:300]}. Restate it."
        )
        validated.add(name)
