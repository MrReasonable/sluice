"""Offline tests of the decisions the Homebrew bottle channel makes (#279).

Design: docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 9a. A green dry run
only ever takes a check's accept branch, so refuse branches are tested here, and every function also
has an accept row: a check that always refuses would otherwise pass every refuse row.

EVERY expected value is restated here by hand. Module-level `_EXPECTED*` constants are guarded by
`test_every_expected_constant_is_built_only_from_literals`, ported from
tests/test_homebrew_formula.py for the same reason: an expectation read out of the module under
test compares that module with itself.

Fixtures are synthetic: `example.invalid` URLs where the host is not the property under test, fake
digests, owner `ExampleOwner`. Resource-stanza URLs keep the files.pythonhosted.org host because
that host is exactly what the formula grammar checks. tests/homebrew_fixtures/ holds sanitised real
Homebrew output. Its formula keeps the renderer's `homepage` line, which names the upstream project's
owner, because the validator compares that text with `render()`. Its resource stanzas stay exactly as
`brew update-python-resources` wrote them: public PyPI metadata for the formula's dependencies, with no
personal data in it, whose real spellings (`charset_normalizer`'s sdist under a resource named
`charset-normalizer`) reach the validator's PEP 503 name comparison without having been written to fit
it. The identity-bearing values around them are the synthetic ones. A change to the renderer's template
makes that fixture stale: recapture it on a Mac with a real `brew bottle --merge`, as
docs/superpowers/plans/2026-09-14-homebrew-bottles.md's first task did, never by hand.
"""
import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

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


def test_install_md_names_the_lowest_macos_a_bottle_is_built_for():
    """docs/INSTALL.md tells a user which Macs pour a bottle, and a platform pair added or dropped here
    would leave it wrong. Read from `hb.PLATFORMS` on purpose: the property is that the doc agrees with
    what ships, not that the module agrees with itself. Only the "or later" bound is derived. Apple's
    numbering jumps from 15 to 26, so the "or earlier" bound for the Macs that build from source is not
    derivable from the lowest version."""
    majors = []
    for runner, _tag in hb.PLATFORMS:
        match = re.fullmatch(r"macos-([0-9]+)", runner)
        assert match, f"runner label {runner!r} names no macOS version"
        majors.append(int(match[1]))
    assert majors, "no platform pair; the check below proves nothing"
    text = (ROOT / "docs" / "INSTALL.md").read_text()
    start = text.index("\n## Homebrew (macOS)\n")
    end = text.find("\n## ", start + 1)
    # Markdown wraps prose at any space, so the section is compared with its whitespace collapsed.
    section = " ".join(text[start: end if end != -1 else len(text)].split())
    assert f"macOS {min(majors)} or later" in section, section


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


# --- the measured fixtures -----------------------------------------------------------------------


def test_the_fixture_directory_holds_exactly_the_three_measured_files():
    """This directory sits outside tests/fixtures/, whose scope guard admits only captured board
    payloads, so it carries its own closure: a file added here would sit in no pin below."""
    found = sorted(path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*"))
    assert found == ["bottle.json", "info_poured.json", "merged_formula.rb"], found


def test_the_json_fixtures_carry_only_the_sanitised_keys_and_values():
    """Real `brew bottle --json` and `brew info` output also carry the build machine's OS, Xcode and
    CLT versions, timestamps, and the tap's remote and revision. A verbatim recapture must fail here,
    by name, rather than commit them."""
    bottle = json.loads((FIXTURES / "bottle.json").read_text())
    cellar = bottle.get("exampleowner/tap/job-sluice", {}).get("bottle", {}).pop("cellar", None)
    assert cellar in ("any", "any_skip_relocation"), cellar
    assert bottle == {
        "exampleowner/tap/job-sluice": {
            "formula": {"name": "job-sluice", "pkg_version": "9.9.0"},
            "bottle": {
                "root_url": "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1",
                "rebuild": 0,
                "tags": {
                    "arm64_tahoe": {
                        "filename": "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
                        "local_filename": "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz",
                        "sha256": "a" * 64,
                    }
                },
            },
        }
    }
    assert json.loads((FIXTURES / "info_poured.json").read_text()) == {
        "formulae": [{"full_name": "exampleowner/tap/job-sluice",
                      "installed": [{"version": "9.9.0", "poured_from_bottle": True}]}]
    }


# --- the pour check ---------------------------------------------------------------------------

_FULL_NAME = "exampleowner/tap/job-sluice"


def _info(*formulae):
    return {"formulae": list(formulae)}


def _formula(full_name=_FULL_NAME, *kegs):
    return {"full_name": full_name, "installed": list(kegs)}


def _keg(version="9.9.0", poured=True):
    return {"version": version, "poured_from_bottle": poured}


def test_the_measured_brew_info_passes_the_pour_check():
    info = json.loads((FIXTURES / "info_poured.json").read_text())
    hb.check_pour(info, full_name=_FULL_NAME, version="9.9.0")


def test_one_poured_keg_at_the_version_passes():
    hb.check_pour(_info(_formula(_FULL_NAME, _keg())), full_name=_FULL_NAME, version="9.9.0")


@pytest.mark.parametrize(
    "info",
    [
        # job-sluice built from source while a dependency beside it was poured: the case an
        # unscoped `--installed` check reads as success.
        _info(_formula("pango", _keg("1.0.0", True)), _formula(_FULL_NAME, _keg(poured=False))),
        _info(_formula(_FULL_NAME, _keg(poured=False))),
        _info(_formula("pango", _keg("1.0.0", True))),
        _info(_formula(_FULL_NAME)),
        _info(_formula(_FULL_NAME, _keg("9.8.0", True))),
        _info(_formula(_FULL_NAME, _keg(), _keg())),
        _info(_formula(_FULL_NAME, {"version": "9.9.0"})),
        # One poured keg at VERSION under another name: refused by the name check and nothing else.
        _info(_formula("exampleowner/tap/other", _keg())),
        _info(_formula("pango", _keg())),
        _info(),
        {},
    ],
)
def test_the_pour_check_refuses_anything_but_one_poured_keg_of_this_formula(info):
    with pytest.raises(Refusal):
        hb.check_pour(info, full_name=_FULL_NAME, version="9.9.0")


def test_expect_built_passes_a_built_keg():
    hb.check_pour(_info(_formula(_FULL_NAME, _keg(poured=False))), full_name=_FULL_NAME,
                  version="9.9.0", expect_built=True)


@pytest.mark.parametrize("poured", [True, None])
def test_expect_built_refuses_a_poured_or_unknown_keg(poured):
    keg = {"version": "9.9.0"} if poured is None else _keg(poured=poured)
    with pytest.raises(Refusal):
        hb.check_pour(_info(_formula(_FULL_NAME, keg)), full_name=_FULL_NAME, version="9.9.0",
                      expect_built=True)


# --- the produced tag -------------------------------------------------------------------------


def _fixture_bottle_json():
    return json.loads((FIXTURES / "bottle.json").read_text())


def test_the_measured_bottle_json_names_the_real_file_shapes():
    """Task 1 measured these: single dash for the download name, double dash on disk."""
    (entry,) = _fixture_bottle_json().values()
    (tag_hash,) = entry["bottle"]["tags"].values()
    assert (tag_hash["filename"], tag_hash["local_filename"]) == (
        "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
        "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz",
    )


def test_the_produced_tag_passes_when_it_is_the_declared_one():
    hb.check_produced_tag(_fixture_bottle_json(), "arm64_tahoe")


def _with_tags(tags):
    data = _fixture_bottle_json()
    (entry,) = data.values()
    (tag_hash,) = entry["bottle"]["tags"].values()
    entry["bottle"]["tags"] = {tag: tag_hash for tag in tags}
    return data


@pytest.mark.parametrize(
    "data, declared",
    [
        (_with_tags(["arm64_tahoe"]), "arm64_sequoia"),
        (_with_tags([]), "arm64_tahoe"),
        (_with_tags(["arm64_tahoe", "arm64_sequoia"]), "arm64_tahoe"),
        (_with_tags(["arm64_tahoe"]), ""),
        ({}, "arm64_tahoe"),
    ],
)
def test_the_produced_tag_refuses_a_mismatch_zero_two_or_an_empty_declaration(data, declared):
    with pytest.raises(Refusal) as err:
        hb.check_produced_tag(data, declared)
    if not declared:
        # The tag comparison refuses this row too, so only the guard's own message shows it refused.
        assert "the declared bottle tag is empty" in str(err.value)


# --- bottle JSONs, as upload reads them --------------------------------------------------------

_ROOT_URL = "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1"
_BOTH_TAGS = ["arm64_sequoia", "arm64_tahoe"]


def _bottle_dir(tmp_path, tags=("arm64_sequoia", "arm64_tahoe"), mutate=None):
    """One JSON and one bottle file per tag, shaped like the measured fixture."""
    (entry,) = _fixture_bottle_json().values()
    (template,) = entry["bottle"]["tags"].values()
    paths = []
    for tag in tags:
        payload = f"bottle bytes for {tag}".encode()
        tag_hash = dict(
            template,
            filename=f"job-sluice-9.9.0.{tag}.bottle.tar.gz",
            local_filename=f"job-sluice--9.9.0.{tag}.bottle.tar.gz",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        doc = {_FULL_NAME: {"formula": dict(entry["formula"]),
                            "bottle": dict(entry["bottle"], tags={tag: tag_hash})}}
        if mutate is not None:
            mutate(tag, doc)
        (tmp_path / f"job-sluice--9.9.0.{tag}.bottle.tar.gz").write_bytes(payload)
        path = tmp_path / f"job-sluice--9.9.0.{tag}.bottle.json"
        path.write_text(json.dumps(doc))
        paths.append(path)
    return paths


def test_two_valid_bottle_jsons_yield_their_assets_in_declared_order(tmp_path):
    paths = _bottle_dir(tmp_path)
    assets = hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)
    assert [(a.tag, a.remote_name, a.local_path.name) for a in assets] == [
        ("arm64_sequoia", "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz",
         "job-sluice--9.9.0.arm64_sequoia.bottle.tar.gz"),
        ("arm64_tahoe", "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
         "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz"),
    ]
    assert assets[0].sha256 == hashlib.sha256(b"bottle bytes for arm64_sequoia").hexdigest()


def _bottle(doc):
    (entry,) = doc.values()
    return entry["bottle"]


def _only_tag_hash(doc):
    (tag_hash,) = _bottle(doc)["tags"].values()
    return tag_hash


_JSON_MUTANTS = {
    "wrong root url": lambda tag, doc: _bottle(doc).update(root_url=_ROOT_URL + "x"),
    "non-zero rebuild": lambda tag, doc: _bottle(doc).update(rebuild=1),
    "path-valued cellar": lambda tag, doc: _bottle(doc).update(cellar="/opt/homebrew/Cellar"),
    "missing cellar": lambda tag, doc: _bottle(doc).pop("cellar"),
    "download name with double dash": lambda tag, doc: _only_tag_hash(doc).update(
        filename=f"job-sluice--9.9.0.{tag}.bottle.tar.gz"),
    "path component in local name": lambda tag, doc: _only_tag_hash(doc).update(
        local_filename=f"../job-sluice--9.9.0.{tag}.bottle.tar.gz"),
    "digest mismatch": lambda tag, doc: _only_tag_hash(doc).update(sha256="0" * 64),
    "malformed digest": lambda tag, doc: _only_tag_hash(doc).update(sha256="Z" * 64),
    "second formula": lambda tag, doc: doc.update({"other/tap/x": {}}),
}


@pytest.mark.parametrize("mutant", sorted(_JSON_MUTANTS))
def test_a_bottle_json_off_contract_is_refused(tmp_path, mutant):
    paths = _bottle_dir(tmp_path, mutate=_JSON_MUTANTS[mutant])
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)


def test_a_json_whose_bottle_is_absent_is_refused(tmp_path):
    paths = _bottle_dir(tmp_path)
    (tmp_path / "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz").unlink()
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)


@pytest.mark.parametrize(
    "made, declared",
    [
        (("arm64_tahoe",), _BOTH_TAGS),
        (("arm64_sequoia", "arm64_tahoe"), ["arm64_tahoe"]),
        (("arm64_sequoia", "arm64_tahoe"), []),
    ],
)
def test_the_json_set_must_equal_the_declared_tags(tmp_path, made, declared):
    paths = _bottle_dir(tmp_path, tags=made)
    with pytest.raises(Refusal) as err:
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=declared)
    if not declared:
        # The undeclared-tag check refuses this row too, so only the guard's own message shows it refused.
        assert "the declared tag set is empty" in str(err.value)


def test_two_jsons_for_one_tag_are_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    paths = _bottle_dir(tmp_path / "a", tags=("arm64_tahoe",)) + _bottle_dir(
        tmp_path / "b", tags=("arm64_tahoe",))
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=["arm64_tahoe"])


def test_an_undeclared_tag_is_refused_before_any_file_is_read(tmp_path):
    """The tag is the JSON's own key, and the names and the bottle's path are built from it, so an
    undeclared tag must refuse before either exists: a tag with path components would otherwise read,
    and report the digest of, a file outside the bottles directory."""
    bottles = tmp_path / "bottles"
    bottles.mkdir()
    outside = tmp_path / "outside.bottle.tar.gz"
    outside.write_bytes(b"outside the bottles directory")
    tag = "/../../outside"
    (bottles / "job-sluice--9.9.0.").mkdir()
    (entry,) = _fixture_bottle_json().values()
    doc = {_FULL_NAME: {"formula": dict(entry["formula"]), "bottle": dict(entry["bottle"], tags={tag: {
        "filename": f"job-sluice-9.9.0.{tag}.bottle.tar.gz",
        "local_filename": f"job-sluice--9.9.0.{tag}.bottle.tar.gz",
        "sha256": "0" * 64}})}}
    path = bottles / "crafted.bottle.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(Refusal) as err:
        hb.validate_bottle_jsons([path], version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)
    assert hashlib.sha256(outside.read_bytes()).hexdigest() not in str(err.value)
    assert "undeclared tag" in str(err.value)


# --- the merged bottle block -------------------------------------------------------------------


def _merged():
    return (FIXTURES / "merged_formula.rb").read_text()


def test_the_measured_merge_parses_to_both_tags():
    root_url, tags, _ = hb.parse_bottle_block(_merged())
    assert root_url == _ROOT_URL
    assert {tag: sha for tag, (_cellar, sha) in tags.items()} == {
        "arm64_tahoe": "a" * 64,
        "arm64_sequoia": "b" * 64,
    }


def test_the_merged_tag_set_passes_when_it_equals_the_declared_one():
    hb.check_merged_tags(_merged(), _BOTH_TAGS)


@pytest.mark.parametrize("declared", [["arm64_tahoe"], _BOTH_TAGS + ["arm64_golden_gate"], []])
def test_the_merged_tag_set_refuses_a_missing_extra_or_empty_declaration(declared):
    with pytest.raises(Refusal) as err:
        hb.check_merged_tags(_merged(), declared)
    if not declared:
        # The set comparison refuses this row too, so only the guard's own message shows it refused.
        assert "the declared tag set is empty" in str(err.value)


def _block_line(text, startswith):
    return next(line for line in text.splitlines() if line.startswith(startswith))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace(_block_line(t, "    root_url "), _block_line(t, "    root_url ") + "\n    rebuild 1"),
        lambda t: t + "\n  bottle do\n  end\n",
        lambda t: t.replace("  bottle do\n", "  bottle_block do\n"),
        lambda t: t.replace(_block_line(t, "    sha256 cellar:"), _block_line(t, "    sha256 cellar:") + '; system "x"'),
        lambda t: re.sub(r"cellar: :[a-z_]+", "cellar: :other", t, count=1),
    ],
)
def test_a_bottle_block_off_shape_is_refused(mutate):
    with pytest.raises(Refusal):
        hb.parse_bottle_block(mutate(_merged()))


def test_the_cache_file_passes_when_its_digest_is_the_blocks():
    formula = _merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest())
    hb.check_cache_file(b"payload", formula, "arm64_tahoe")


@pytest.mark.parametrize("tag, data", [("arm64_tahoe", b"other"), ("arm64_golden_gate", b"payload")])
def test_the_cache_file_refuses_other_bytes_or_an_undeclared_tag(tag, data):
    formula = _merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest())
    with pytest.raises(Refusal):
        hb.check_cache_file(data, formula, tag)


# --- release templates and lifecycle --------------------------------------------------------------

_TAG = "job-sluice-9.9.0-123-1"
_RUN_URL = "https://github.com/ExampleOwner/sluice/actions/runs/123"
_BASE_SHA = "f" * 40

_EXPECTED_RELEASE_TITLE = "job-sluice 9.9.0 bottles (release)"
_EXPECTED_DRY_RUN_TITLE = "job-sluice 9.9.0 bottles (dry run)"
_EXPECTED_RELEASE_NOTES = (
    "Bottles for job-sluice 9.9.0, published by "
    "https://github.com/ExampleOwner/sluice/actions/runs/123 (release).\n"
    "Release tag: job-sluice-9.9.0-123-1\n"
)


def test_the_release_templates_are_fixed_text_for_each_caller():
    assert hb.release_templates(version="9.9.0", tag=_TAG, caller="release", run_url=_RUN_URL) == (
        _EXPECTED_RELEASE_TITLE, _EXPECTED_RELEASE_NOTES)
    title, notes = hb.release_templates(version="9.9.0", tag=_TAG, caller="dry run", run_url=_RUN_URL)
    assert title == _EXPECTED_DRY_RUN_TITLE
    assert notes == _EXPECTED_RELEASE_NOTES.replace("(release)", "(dry run)")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"caller": "pull request"},
        {"run_url": "https://example.invalid/actions/runs/123"},
        {"run_url": _RUN_URL + "\nextra"},
        {"tag": "job-sluice-9.9.0"},
        {"version": "9.9"},
    ],
)
def test_the_release_templates_refuse_unexpected_inputs(kwargs):
    arguments = {"version": "9.9.0", "tag": _TAG, "caller": "release", "run_url": _RUN_URL}
    arguments.update(kwargs)
    with pytest.raises(Refusal):
        hb.release_templates(**arguments)


def _release(**overrides):
    release = {"id": 7, "tag_name": _TAG, "target_commitish": _BASE_SHA,
               "name": _EXPECTED_RELEASE_TITLE, "body": _EXPECTED_RELEASE_NOTES, "draft": True}
    release.update(overrides)
    return release


def _decide(releases):
    return hb.release_decision(releases, tag=_TAG, base_sha=_BASE_SHA,
                               title=_EXPECTED_RELEASE_TITLE, notes=_EXPECTED_RELEASE_NOTES)


def test_an_absent_release_is_to_be_created():
    assert _decide([_release(tag_name="job-sluice-9.8.0-1-1")]) is None
    assert _decide([]) is None


@pytest.mark.parametrize("draft", [True, False])
def test_a_matching_release_is_accepted_draft_or_published(draft):
    assert _decide([_release(draft=draft)])["id"] == 7


@pytest.mark.parametrize(
    "overrides",
    [{"target_commitish": "e" * 40}, {"name": "other"}, {"body": "other"}],
)
def test_a_release_that_differs_is_refused(overrides):
    with pytest.raises(Refusal) as err:
        _decide([_release(**overrides)])
    assert next(iter(overrides)) in str(err.value)


def test_two_releases_under_one_tag_are_refused():
    with pytest.raises(Refusal):
        _decide([_release(), _release(id=8)])


def test_an_absent_asset_is_uploaded():
    assert hb.asset_decision([{"name": "other"}], name="n", sha256="a" * 64) == "upload"


def test_an_identical_asset_is_skipped():
    assert hb.asset_decision([{"name": "n", "digest": "sha256:" + "a" * 64}], name="n",
                             sha256="a" * 64) == "skip"


@pytest.mark.parametrize("digest", ["sha256:" + "b" * 64, None, "", "a" * 64, "md5:abc"])
def test_a_different_or_missing_digest_is_refused(digest):
    asset = {"name": "n"} if digest is None else {"name": "n", "digest": digest}
    with pytest.raises(Refusal) as err:
        hb.asset_decision([asset], name="n", sha256="a" * 64)
    if digest is None:
        assert "Delete that asset" in str(err.value)


def _assets():
    return [
        {"name": "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz", "digest": "sha256:" + "a" * 64},
        {"name": "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz", "digest": "sha256:" + "b" * 64},
    ]


def test_release_digests_map_each_declared_tag_to_its_assets_digest():
    assert hb.release_digests(_assets(), version="9.9.0", tags=_BOTH_TAGS) == {
        "arm64_sequoia": "b" * 64, "arm64_tahoe": "a" * 64}


@pytest.mark.parametrize(
    "assets",
    [
        _assets()[:1],
        [dict(_assets()[0], digest="sha256:" + "A" * 64), _assets()[1]],
        [dict(_assets()[0], digest=None), _assets()[1]],
        _assets() + _assets()[:1],
    ],
)
def test_release_digests_refuse_a_missing_malformed_or_duplicated_asset(assets):
    with pytest.raises(Refusal):
        hb.release_digests(assets, version="9.9.0", tags=_BOTH_TAGS)


# --- the formula validator ---------------------------------------------------------------------

_FIXTURE_SDIST = "https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz"
_DIGESTS = {"arm64_tahoe": "a" * 64, "arm64_sequoia": "b" * 64}


def _validate(text, **overrides):
    arguments = {"sdist_url": _FIXTURE_SDIST, "sha256": "c" * 64, "root_url": _ROOT_URL,
                 "tags": _BOTH_TAGS, "release_digests": dict(_DIGESTS)}
    arguments.update(overrides)
    hb.validate_formula(text, **arguments)


def test_the_measured_merge_is_accepted():
    _validate(_merged())


def _refused(text, **overrides):
    try:
        _validate(text, **overrides)
    except Refusal:
        return True
    return False


def test_every_injection_into_every_line_of_the_measured_merge_is_refused():
    """Generated from the fixture, never hand-picked, so no line is left untested."""
    lines = _merged().split("\n")
    cases = []
    for index, line in enumerate(lines):
        before = lines[:index] + ['  system "x"'] + lines[index:]
        after = lines[: index + 1] + ['  system "x"'] + lines[index + 1 :]
        appended = lines[:index] + [line + '; system "x"'] + lines[index + 1 :]
        cases += [("statement before", index, before), ("statement after", index, after),
                  ("appended", index, appended)]
        for quote in range(0, line.count('"') // 2):
            position = [i for i, ch in enumerate(line) if ch == '"'][2 * quote]
            interpolated = line[: position + 1] + "#{1}" + line[position + 1 :]
            cases.append(("interpolation", index, lines[:index] + [interpolated] + lines[index + 1 :]))
    quoted_values = sum(line.count('"') // 2 for line in lines)
    assert len(cases) == 3 * len(lines) + quoted_values and quoted_values > 0, (
        "the generator produced the wrong number of cases; the loop below would prove less than it claims"
    )
    accepted = [(kind, index) for kind, index, candidate in cases if not _refused("\n".join(candidate))]
    assert accepted == [], f"injections the validator accepted: {accepted[:20]}"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sdist_url": _FIXTURE_SDIST.replace("9.9.0", "9.9.1")},
        {"sha256": "d" * 64},
        {"root_url": _ROOT_URL.replace("exampleowner", "someoneelse")},
        {"tags": ["arm64_tahoe"]},
        {"tags": _BOTH_TAGS + ["arm64_golden_gate"]},
        {"tags": []},
        {"release_digests": dict(_DIGESTS, arm64_tahoe="f" * 64)},
        {"release_digests": {"arm64_tahoe": "a" * 64}},
    ],
)
def test_arguments_that_disagree_with_the_text_are_refused(overrides):
    with pytest.raises(Refusal) as err:
        _validate(_merged(), **overrides)
    if overrides == {"tags": []}:
        # The tag-set comparison refuses this row too, so only the guard's own message shows it refused.
        assert "the declared tag set is empty" in str(err.value)


def test_a_character_in_place_of_the_blank_line_after_the_bottle_block_is_refused():
    """The newline directly after the block's `  end` line is replaced by `x`. Removing the block for the
    comparison also strips the one character after it as if it were that separator, so the comparison
    with the renderer's text passes while the text that would be pushed still carries the `x`. Only the
    blank-line check refuses it."""
    text = _merged()
    block_end = text.index("  end\n", text.index("  bottle do\n")) + len("  end\n")
    with pytest.raises(Refusal, match="not followed by the blank line"):
        _validate(text[:block_end] + "x" + text[block_end + 1:])


_STANZA_URL = "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/"


def _replace_first_stanza(text, name, filename):
    start = text.index('  resource "')
    end = text.index("  end\n", start) + len("  end\n")
    stanza = (f'  resource "{name}" do\n    url "{_STANZA_URL}{filename}"\n'
              f'    sha256 "{"9" * 64}"\n  end\n')
    return text[:start] + stanza + text[end:]


@pytest.mark.parametrize(
    "name, filename",
    [("alpha-beta", "alpha_beta-1.0.tar.gz"), ("Alpha-Beta", "alpha.beta-1.0.tar.gz")],
)
def test_a_resource_named_as_pep_503_normalises_its_project_is_accepted(name, filename):
    _validate(_replace_first_stanza(_merged(), name, filename))


@pytest.mark.parametrize(
    "name, filename",
    [("alpha", "alpha-beta-1.0.tar.gz"), ("alpha", "other-1.0.tar.gz")],
)
def test_a_resource_whose_project_is_not_its_name_is_refused(name, filename):
    with pytest.raises(Refusal):
        _validate(_replace_first_stanza(_merged(), name, filename))


def test_a_resource_on_a_foreign_host_is_refused():
    text = _merged()
    start = text.index('    url "https://files.pythonhosted.org/')
    with pytest.raises(Refusal):
        _validate(text[:start] + text[start:].replace("files.pythonhosted.org", "example.invalid", 1))


def test_a_rebuild_line_in_the_block_is_refused():
    text = _merged()
    root_line = _block_line(text, "    root_url ")
    with pytest.raises(Refusal):
        _validate(text.replace(root_line, root_line + "\n    rebuild 1"))


def _resource_run(text):
    """The measured merge's resource run: from its first stanza to the end of its last."""
    start = text.index('  resource "')
    last = text.rindex('  resource "')
    return start, text.index("  end\n\n", last) + len("  end\n\n")


def test_the_resource_run_moved_below_the_final_end_is_refused():
    """Removing a contiguous run restores the renderer's text wherever the run sits, so only its
    position refuses this: `resource` below the class's `end` is undefined when the formula loads."""
    text = _merged()
    start, end = _resource_run(text)
    with pytest.raises(Refusal):
        _validate(text[:start] + text[end:] + text[start:end])


def test_a_resource_stanza_split_from_its_run_is_refused():
    """One stanza moved above `test do`, at the start of its own line: removing every stanza still
    restores the renderer's text, so only the run's contiguity refuses this."""
    text = _merged()
    start, _ = _resource_run(text)
    first_end = text.index("  end\n\n", start) + len("  end\n\n")
    stanza, rest = text[start:first_end], text[:start] + text[first_end:]
    anchor = rest.index("  test do\n")
    with pytest.raises(Refusal):
        _validate(rest[:anchor] + stanza + rest[anchor:])


def _bottle_block(text):
    """The measured merge's bottle block with the blank line after it, and the text without them."""
    start = text.index("  bottle do\n")
    end = text.index("  end\n", start) + len("  end\n") + 1
    return text[start:end], text[:start] + text[end:]


def test_the_bottle_block_moved_below_the_final_end_is_refused():
    """Removing the block and its blank line restores the renderer's text wherever the block sits, so
    only its position refuses this: `bottle` below the class's `end` breaks every install."""
    block, rest = _bottle_block(_merged())
    with pytest.raises(Refusal):
        _validate(rest + block)


def test_the_bottle_block_moved_between_two_resource_stanzas_is_refused():
    """Removing the block leaves the stanzas contiguous again, so the run's contiguity cannot see this."""
    block, rest = _bottle_block(_merged())
    start, _ = _resource_run(rest)
    first_end = rest.index("  end\n\n", start) + len("  end\n\n")
    with pytest.raises(Refusal):
        _validate(rest[:first_end] + block + rest[first_end:])


# --- the push decision ----------------------------------------------------------------------------


def _formula_at(version):
    return _merged().replace("job_sluice-9.9.0.tar.gz", f"job_sluice-{version}.tar.gz").encode()


def test_the_version_is_read_from_the_top_level_url_as_integers():
    assert hb.parse_formula_version(_formula_at("9.10.0").decode()) == (9, 10, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        _merged().replace("job_sluice-9.9.0.tar.gz", "job_sluice-9.9.tar.gz"),
        _merged().replace('  url "', '  url  "', 1),
        _merged() + '  url "https://example.invalid/job_sluice-1.0.0.tar.gz"\n',
    ],
)
def test_an_unreadable_version_is_refused(text):
    with pytest.raises(Refusal):
        hb.parse_formula_version(text)


# Three Arabic-Indic digits, built with chr so no escape sequence has to survive a write. `\d` matches
# every Unicode decimal digit, and int() reads these as 1, 2 and 3.
_NON_ASCII_DIGITS = "".join(chr(0x0661 + i) for i in range(3))


@pytest.mark.parametrize(
    "check",
    [
        lambda digits: hb.version_tuple(".".join(digits)),
        lambda digits: hb.release_templates(version="9.9.0", tag=f"job-sluice-{'.'.join(digits)}-123-1",
                                            caller="release", run_url=_RUN_URL),
    ],
    ids=["version", "tag"],
)
def test_a_version_in_non_ascii_digits_is_refused(check):
    with pytest.raises(Refusal):
        check(_NON_ASCII_DIGITS)


def test_a_top_level_url_with_non_ascii_digits_is_not_read_as_a_version():
    """Refused either way, so the message is what shows `_TOP_URL_RE` itself refuses: with `\\d` the line
    matches and `validate_version` refuses it instead, with a different message."""
    text = _merged().replace("job_sluice-9.9.0.tar.gz", f"job_sluice-{'.'.join(_NON_ASCII_DIGITS)}.tar.gz")
    with pytest.raises(Refusal) as err:
        hb.parse_formula_version(text)
    assert "found 0 top-level url lines" in str(err.value)


def _push(**overrides):
    arguments = {"target_state": "present", "remote_formula": _formula_at("9.8.0"),
                 "ours": _formula_at("9.9.0"), "target_is_default": True,
                 "base_formula": _formula_at("9.8.0"), "version": "9.9.0", "dry_run": False}
    arguments.update(overrides)
    return hb.push_decision(**arguments)


def test_a_dry_run_refuses_the_default_branch_when_git_lists_a_formula_at_the_base():
    """`plan` sends a dry run to the default branch only when the contents API reads the formula as
    absent at BASE_SHA. A wrong 404 would otherwise make a dry run's formula, whose bottles live in a
    dry-run release, the tap's tree of record."""
    with pytest.raises(Refusal) as err:
        _push(dry_run=True, base_formula=_formula_at("9.8.0"))
    assert "tree of record" in str(err.value)


def test_a_dry_run_bootstrap_pushes():
    assert _push(dry_run=True, base_formula=None) == "push"


def test_a_dry_run_on_a_scratch_branch_pushes_whatever_the_base_holds():
    assert _push(dry_run=True, target_is_default=False, base_formula=_formula_at("9.8.0")) == "push"


def test_identical_bytes_at_the_target_are_a_no_op():
    assert _push(remote_formula=_formula_at("9.9.0")) == "noop"


def test_different_bytes_at_the_target_push():
    assert _push() == "push"


def test_an_absent_scratch_branch_pushes():
    assert _push(target_state="absent", remote_formula=None, target_is_default=False) == "push"


def test_a_target_without_the_formula_pushes():
    assert _push(remote_formula=None) == "push"


@pytest.mark.parametrize("base", ["9.9.0", "9.8.9", "2.9.7"])
def test_an_equal_or_older_base_version_pushes(base):
    assert _push(base_formula=_formula_at(base)) == "push"


def test_a_newer_base_version_on_the_default_branch_is_refused():
    """9.10.0 against 9.9.0: a string comparison would call 9.10.0 older and roll the tap back."""
    with pytest.raises(Refusal):
        _push(base_formula=_formula_at("9.10.0"))


def test_a_newer_base_version_on_a_scratch_branch_pushes():
    assert _push(target_is_default=False, base_formula=_formula_at("9.10.0")) == "push"


def test_the_bootstrap_with_no_formula_at_the_base_pushes():
    assert _push(target_state="present", remote_formula=None, base_formula=None) == "push"


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_formula": b'  url "https://example.invalid/unversioned.tar.gz"\n'},
        {"base_formula": b"\xff\xfe"},
        {"target_state": "absent", "remote_formula": None},
        {"target_state": "error"},
        {"version": "9.9"},
    ],
)
def test_an_unparseable_base_an_absent_default_or_a_bad_state_is_refused(overrides):
    with pytest.raises(Refusal):
        _push(**overrides)


# --- the CLI: plan -------------------------------------------------------------------------------


def test_the_plan_subcommand_refuses_a_bad_push_target_before_any_external_command():
    """Executed with an empty PATH: the refusal must come before git or the network, so a caller
    passing a bad push target fails before anything reads the tap. Every other variable `plan`
    requires before its first git call is set, so only statement order stands between the bad value
    and git."""
    proc = subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "plan"],
        env={"PATH": "", "PUSH_TARGET": "bogus", "VERSION": "1.2.3", "REPOSITORY_OWNER": "ExampleOwner"},
        capture_output=True, text=True, timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert proc.stdout.startswith("::error::"), output
    assert "'default'" in output and "'auto'" in output


_SYMREF = "ref: refs/heads/main\tHEAD\n" + "f" * 40 + "\tHEAD\n"
_PYPI = {"urls": [{"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}}]}

# Restated by hand; built with string repetition, so deliberately not an `_EXPECTED*` name.
_PLAN_FOR_DEFAULT = {
    "tap_owner": "exampleowner",
    "default_branch": "main",
    "base_sha": "f" * 40,
    "target_branch": "main",
    "sdist_url": "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz",
    "sdist_sha256": "d" * 64,
    "tag": "job-sluice-9.9.0-123-1",
    "root_url": "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-123-1",
    "run_url": "https://github.com/ExampleOwner/sluice/actions/runs/123",
    "caller": "release",
    "platforms": '[{"runner": "macos-15", "tag": "arm64_sequoia"}, '
                 '{"runner": "macos-26", "tag": "arm64_tahoe"}]',
}


def _plan(**overrides):
    arguments = {"push_target": "default", "version": "9.9.0", "repository_owner": "ExampleOwner",
                 "run_id": "123", "run_attempt": "1", "run_url": _RUN_URL,
                 "ls_remote_output": _SYMREF, "pypi_json": _PYPI, "contents_status": None}
    arguments.update(overrides)
    return hb.build_plan(**arguments)


def test_the_default_plan_emits_every_output():
    assert _plan() == _PLAN_FOR_DEFAULT


@pytest.mark.parametrize("status, target", [(200, "bump-9.9.0"), (404, "main")])
def test_the_auto_plan_targets_by_the_observable(status, target):
    plan = _plan(push_target="auto", contents_status=status)
    assert (plan["target_branch"], plan["caller"]) == (target, "dry run")


@pytest.mark.parametrize(
    "overrides",
    [
        {"push_target": "auto", "contents_status": 403},
        {"push_target": "auto", "contents_status": None},
        {"run_url": "https://example.invalid/runs/1"},
        {"version": "9.9"},
        {"repository_owner": "has space"},
        {"ls_remote_output": ""},
        {"pypi_json": {"urls": []}},
    ],
)
def test_a_plan_with_a_bad_input_or_an_unknown_observable_is_refused(overrides):
    with pytest.raises(Refusal):
        _plan(**overrides)


def test_outputs_are_written_one_per_line(tmp_path):
    path = tmp_path / "output"
    hb.write_outputs({"a": "1", "b": "two"}, str(path))
    assert path.read_text() == "a=1\nb=two\n"


def test_an_output_with_a_newline_is_refused(tmp_path):
    with pytest.raises(Refusal):
        hb.write_outputs({"a": "1\nb=2"}, str(tmp_path / "output"))


def test_an_output_with_a_carriage_return_is_refused(tmp_path):
    """The value carries a CR and no LF, so only the CR arm of the check can refuse it. Nothing is
    written."""
    path = tmp_path / "output"
    with pytest.raises(Refusal, match="the output a contains a newline"):
        hb.write_outputs({"a": "1\rb=2"}, str(path))
    assert not path.exists()


@pytest.mark.parametrize("key", ["a\nb", "a<<EOF", "a=b", ""])
def test_an_output_name_that_is_not_an_identifier_is_refused(tmp_path, key):
    """GitHub reads `name=value` and `name<<DELIMITER`, so a name is as much an injection surface as
    a value. Nothing is written when any name is refused."""
    path = tmp_path / "output"
    with pytest.raises(Refusal):
        hb.write_outputs({"first": "1", key: "2"}, str(path))
    assert not path.exists()


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request):
        self.calls.append((request.get_method(), request.full_url, dict(request.header_items())))
        response = self.routes.get((request.get_method(), request.full_url))
        if response is None:
            raise AssertionError(f"unexpected request {request.get_method()} {request.full_url}")
        return response


def _fake_git(stdout):
    def fake(*args, cwd=None):
        return subprocess.CompletedProcess(["git", *args], 0, stdout.encode(), b"")
    return fake


@pytest.mark.parametrize(
    "push_target, status, target", [("default", None, "main"), ("auto", 200, "bump-9.9.0"),
                                    ("auto", 404, "main")],
)
def test_plan_writes_its_outputs_and_reads_the_contents_api_only_for_auto(
    tmp_path, monkeypatch, push_target, status, target
):
    monkeypatch.setattr(hb, "git", _fake_git(_SYMREF))
    routes = {("GET", "https://pypi.org/pypi/job-sluice/9.9.0/json"): (200, json.dumps(_PYPI).encode())}
    contents = ("https://api.github.com/repos/exampleowner/homebrew-tap/contents/Formula/"
                "job-sluice.rb?ref=" + "f" * 40)
    if status is not None:
        routes[("GET", contents)] = (status, b"{}")
    http = _FakeHttp(routes)
    output = tmp_path / "output"
    env = {"PUSH_TARGET": push_target, "VERSION": "9.9.0", "REPOSITORY_OWNER": "ExampleOwner",
           "RUN_ID": "123", "RUN_ATTEMPT": "1", "RUN_URL": _RUN_URL,
           "GITHUB_TOKEN": "workflow-token", "GITHUB_OUTPUT": str(output)}
    assert hb.main(["plan"], env=env, http=http) == 0
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert outputs["target_branch"] == target
    contents_calls = [call for call in http.calls if call[1] == contents]
    assert len(contents_calls) == (0 if push_target == "default" else 1)
    if contents_calls:
        assert contents_calls[0][2]["Authorization"] == "Bearer workflow-token"


# --- the CLI: the untrusted jobs' checks ------------------------------------------------------------


def test_render_writes_the_renderers_text(tmp_path):
    from scripts.render_homebrew_formula import render

    out = tmp_path / "job-sluice.rb"
    env = {"SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64}
    assert hb.main(["render", "--out", str(out)], env=env) == 0
    assert out.read_text() == render(sdist_url=_FIXTURE_SDIST, sha256="c" * 64)


def test_tags_prints_one_declared_tag_per_line(capsys):
    assert hb.main(["tags"], env={"PLATFORMS": hb.platforms_json()}) == 0
    assert capsys.readouterr().out.split() == ["arm64_sequoia", "arm64_tahoe"]


def test_a_missing_environment_variable_is_a_refusal(capsys):
    assert hb.main(["tags"], env={}) == 1
    assert "PLATFORMS" in capsys.readouterr().out


def test_produced_tag_exits_by_the_check(tmp_path, capsys):
    path = tmp_path / "bottle.json"
    path.write_text((FIXTURES / "bottle.json").read_text())
    assert hb.main(["produced-tag", "--json", str(path)], env={"DECLARED_TAG": "arm64_tahoe"}) == 0
    assert hb.main(["produced-tag", "--json", str(path)], env={"DECLARED_TAG": "arm64_sequoia"}) == 1
    assert "::error::" in capsys.readouterr().out


def test_pour_check_scopes_to_the_tap_formula_and_has_an_expect_built_mode(tmp_path):
    path = tmp_path / "info.json"
    path.write_text((FIXTURES / "info_poured.json").read_text())
    env = {"TAP_OWNER": "exampleowner", "VERSION": "9.9.0"}
    assert hb.main(["pour-check", "--info", str(path)], env=env) == 0
    assert hb.main(["pour-check", "--info", str(path), "--expect-built"], env=env) == 1
    other_owner = {"TAP_OWNER": "otherowner", "VERSION": "9.9.0"}
    assert hb.main(["pour-check", "--info", str(path)], env=other_owner) == 1


def test_merged_tags_and_cache_check_exit_by_their_checks(tmp_path):
    formula = tmp_path / "job-sluice.rb"
    formula.write_text(_merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest()))
    bottle = tmp_path / "bottle"
    bottle.write_bytes(b"payload")
    platforms = {"PLATFORMS": hb.platforms_json()}
    assert hb.main(["merged-tags", "--formula", str(formula)], env=platforms) == 0
    check = ["cache-check", "--formula", str(formula), "--tag", "arm64_tahoe", "--file"]
    assert hb.main(check + [str(bottle)], env={}) == 0
    assert hb.main(check + [str(tmp_path / "missing")], env={}) == 1


# --- the release upload --------------------------------------------------------------------------

_RELEASES = "https://api.github.com/repos/exampleowner/homebrew-tap/releases"


class _FakeGitHub:
    """Just enough of the releases API for upload_bottles and validate-formula."""

    def __init__(self, releases=(), assets=(), fail=None):
        self.releases = [dict(release) for release in releases]
        self.assets = {release["id"]: [dict(a) for a in assets] for release in self.releases}
        self.fail = fail or {}
        self.calls = []

    def __call__(self, request):
        method, url = request.get_method(), request.full_url
        self.calls.append((method, url, request.data))
        if (method, url.split("?")[0]) in self.fail:
            return self.fail[(method, url.split("?")[0])], b"{}"
        if method == "GET" and url.startswith(_RELEASES + "?"):
            page = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps(self.releases if page == 1 else []).encode()
        listing = re.fullmatch(re.escape(_RELEASES) + r"/(\d+)/assets\?per_page=100&page=(\d+)", url)
        if method == "GET" and listing:
            page = self.assets.get(int(listing[1]), []) if listing[2] == "1" else []
            return 200, json.dumps(page).encode()
        if method == "POST" and url == _RELEASES:
            release = dict(json.loads(request.data), id=99)
            self.releases.append(release)
            self.assets[99] = []
            return 201, json.dumps(release).encode()
        upload = re.fullmatch(
            r"https://uploads\.github\.com/repos/exampleowner/homebrew-tap/releases/(\d+)/assets"
            r"\?name=(.+)", url)
        if method == "POST" and upload:
            digest = "sha256:" + hashlib.sha256(request.data).hexdigest()
            self.assets[int(upload[1])].append({"name": upload[2], "digest": digest})
            return 201, b"{}"
        patch = re.fullmatch(re.escape(_RELEASES) + r"/(\d+)", url)
        if method == "PATCH" and patch:
            release = next(r for r in self.releases if r["id"] == int(patch[1]))
            release.update(json.loads(request.data))
            return 200, json.dumps(release).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    def writes(self):
        return [(method, url) for method, url, _ in self.calls if method != "GET"]


def _assets_for(tmp_path):
    return hb.validate_bottle_jsons(_bottle_dir(tmp_path), version="9.9.0", root_url=_ROOT_URL,
                                    tags=_BOTH_TAGS)


def _upload(fake, assets):
    hb.upload_bottles(http=fake, token="tap-token", owner="exampleowner", tag=_TAG,
                      base_sha=_BASE_SHA, title=_EXPECTED_RELEASE_TITLE,
                      notes=_EXPECTED_RELEASE_NOTES, assets=assets)


def _uploaded(asset):
    return {"name": asset.remote_name, "digest": "sha256:" + asset.sha256}


def test_an_absent_release_is_created_as_a_draft_filled_then_published(tmp_path):
    fake = _FakeGitHub()
    _upload(fake, _assets_for(tmp_path))
    created = json.loads(next(data for method, url, data in fake.calls
                              if method == "POST" and url == _RELEASES))
    assert created == {"tag_name": _TAG, "target_commitish": _BASE_SHA,
                       "name": _EXPECTED_RELEASE_TITLE, "body": _EXPECTED_RELEASE_NOTES,
                       "draft": True}
    assert sorted(a["name"] for a in fake.assets[99]) == [
        "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz", "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz"]
    assert fake.writes()[-1] == ("PATCH", f"{_RELEASES}/99")
    assert fake.releases[-1]["draft"] is False


def test_a_partial_draft_is_completed_by_the_rerun(tmp_path):
    assets = _assets_for(tmp_path)
    fake = _FakeGitHub(releases=[_release(draft=True)], assets=[_uploaded(assets[0])])
    _upload(fake, assets)
    uploads = [url for method, url in fake.writes() if method == "POST"]
    assert len(uploads) == 1 and uploads[0].endswith("arm64_tahoe.bottle.tar.gz")
    assert fake.writes()[-1] == ("PATCH", f"{_RELEASES}/7")


def test_a_complete_published_release_writes_nothing(tmp_path):
    assets = _assets_for(tmp_path)
    fake = _FakeGitHub(releases=[_release(draft=False)], assets=[_uploaded(a) for a in assets])
    _upload(fake, assets)
    assert fake.writes() == []


def test_a_release_that_differs_refuses_before_any_write(tmp_path):
    fake = _FakeGitHub(releases=[_release(target_commitish="e" * 40)])
    with pytest.raises(Refusal):
        _upload(fake, _assets_for(tmp_path))
    assert fake.writes() == []


def test_a_different_asset_digest_refuses_before_any_upload(tmp_path):
    assets = _assets_for(tmp_path)
    clash = {"name": assets[1].remote_name, "digest": "sha256:" + "0" * 64}
    fake = _FakeGitHub(releases=[_release(draft=True)], assets=[clash])
    with pytest.raises(Refusal):
        _upload(fake, assets)
    assert fake.writes() == []


@pytest.mark.parametrize("fail", [{("GET", _RELEASES): 500}, {("POST", _RELEASES): 422}])
def test_a_failed_listing_or_create_is_a_refusal(tmp_path, fail):
    with pytest.raises(Refusal):
        _upload(_FakeGitHub(fail=fail), _assets_for(tmp_path))


def test_a_failed_asset_upload_leaves_the_release_a_draft(tmp_path):
    uploads = "https://uploads.github.com/repos/exampleowner/homebrew-tap/releases/99/assets"
    fake = _FakeGitHub(fail={("POST", uploads): 500})
    with pytest.raises(Refusal):
        _upload(fake, _assets_for(tmp_path))
    assert not [call for call in fake.writes() if call[0] == "PATCH"]
    assert fake.releases[-1]["draft"] is True


def test_a_failed_publish_is_a_refusal(tmp_path):
    """Read as success, the upload job would stay green with the release left a draft."""
    fake = _FakeGitHub(fail={("PATCH", f"{_RELEASES}/99"): 500})
    with pytest.raises(Refusal, match="publishing release 99 failed"):
        _upload(fake, _assets_for(tmp_path))
    assert fake.releases[-1]["draft"] is True


def test_a_base_sha_that_is_not_a_commit_id_refuses_before_any_write(tmp_path):
    """The draft release is created targeting BASE_SHA, so a value that is not a commit id must refuse
    before anything is written to the releases API."""
    fake = _FakeGitHub()
    with pytest.raises(Refusal, match="BASE_SHA 'not-a-commit' is not a commit id"):
        hb.upload_bottles(http=fake, token="tap-token", owner="exampleowner", tag=_TAG,
                          base_sha="not-a-commit", title=_EXPECTED_RELEASE_TITLE,
                          notes=_EXPECTED_RELEASE_NOTES, assets=_assets_for(tmp_path))
    assert fake.writes() == []


@pytest.mark.parametrize("status", [500, 200])
def test_a_failed_or_non_list_asset_listing_is_a_refusal(status):
    """Either answer read as "no assets" would decide every asset `upload`. The fake answers `{}`, so the
    200 row is a body that is not a list."""
    fake = _FakeGitHub(fail={("GET", f"{_RELEASES}/7/assets"): status})
    with pytest.raises(Refusal, match="listing release 7's assets failed"):
        hb.list_assets(fake, "tap-token", "exampleowner", 7)


def test_releases_and_assets_are_read_past_the_first_page():
    """`_FakeGitHub` serves everything on page 1, so a reader that stops there passes every other row.
    Here page 1 is a full page of 100, the `per_page` both functions ask for, and page 2 holds an entry
    that a lookup reading one page would take as absent."""
    pages = {1: [{"id": number} for number in range(1, 101)], 2: [{"id": 101}]}
    requested = []

    def http(request):
        requested.append(request.full_url)
        return 200, json.dumps(pages.get(int(request.full_url.rsplit("page=", 1)[1]), [])).encode()

    releases = hb.list_releases(http, "tap-token", "exampleowner")
    assert {"id": 1} in releases and {"id": 101} in releases, len(releases)
    assert requested and all(url.startswith(_RELEASES + "?") for url in requested), requested
    requested.clear()
    assets = hb.list_assets(http, "tap-token", "exampleowner", 7)
    assert {"id": 1} in assets and {"id": 101} in assets, len(assets)
    assert requested and all(url.startswith(f"{_RELEASES}/7/assets?") for url in requested), requested


def test_upload_bottles_from_the_cli_validates_then_publishes(tmp_path):
    _bottle_dir(tmp_path)
    fake = _FakeGitHub()
    env = {"BOTTLES_DIR": str(tmp_path), "VERSION": "9.9.0", "ROOT_URL": _ROOT_URL,
           "PLATFORMS": hb.platforms_json(), "TAP_TOKEN": "tap-token", "TAP_OWNER": "exampleowner",
           "TAG": _TAG, "BASE_SHA": _BASE_SHA, "CALLER": "release", "RUN_URL": _RUN_URL}
    assert hb.main(["validate-bottles"], env=env, http=fake) == 0
    assert fake.calls == []
    assert hb.main(["upload-bottles"], env=env, http=fake) == 0
    assert fake.releases[-1]["draft"] is False


def test_validate_formula_reads_the_published_releases_digests(tmp_path):
    formula = tmp_path / "job-sluice.rb"
    formula.write_text(_merged())
    env = {"MERGED_FORMULA": str(formula), "SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64,
           "ROOT_URL": _ROOT_URL, "PLATFORMS": hb.platforms_json(), "TAP_OWNER": "exampleowner",
           "TAG": "job-sluice-9.9.0-1-1", "VERSION": "9.9.0", "GITHUB_TOKEN": "workflow-token"}
    published = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=False)],
                            assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=published) == 0
    draft_only = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=True)],
                             assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=draft_only) == 1


@pytest.mark.parametrize("newline", ["\r", "\r\n"])
def test_a_carriage_return_in_the_merged_formula_is_refused(tmp_path, newline):
    """`read_text()` would turn a lone CR into LF, so the text validated would not be the bytes pushed,
    and Ruby does not end a line at a lone CR. The reader both subcommands share takes the bytes and
    refuses a CR."""
    formula = tmp_path / "job-sluice.rb"
    formula.write_bytes(_merged().replace("\n", newline, 1).encode())
    env = {"MERGED_FORMULA": str(formula), "SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64,
           "ROOT_URL": _ROOT_URL, "PLATFORMS": hb.platforms_json(), "TAP_OWNER": "exampleowner",
           "TAG": "job-sluice-9.9.0-1-1", "VERSION": "9.9.0", "GITHUB_TOKEN": "workflow-token"}
    published = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=False)],
                            assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=published) == 1
    with pytest.raises(Refusal):
        hb._read_formula(str(formula))


# --- the tap push, against local repositories -------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    """Real git runs in this file. An inherited GIT_DIR or GIT_INDEX_FILE (pytest started from a git
    hook) would aim it at another repository, and a system or global `core.hooksPath` would make the
    hook test below pass without the helper's doing."""
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


def _git_run(cwd, *args):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _make_tap(tmp_path, formula):
    """A bare 'origin' with a main branch, optionally holding Formula/job-sluice.rb."""
    origin = tmp_path / "origin.git"
    _git_run(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git_run(tmp_path, "clone", origin.as_uri(), str(seed))
    (seed / "README.md").write_text("tap\n")
    if formula is not None:
        (seed / "Formula").mkdir()
        (seed / "Formula" / "job-sluice.rb").write_bytes(formula)
    _git_run(seed, "add", "-A")
    _git_run(seed, "commit", "-m", "seed")
    _git_run(seed, "push", "origin", "HEAD:refs/heads/main")
    return origin.as_uri(), seed, _git_run(seed, "rev-parse", "HEAD").stdout.decode().strip()


def _commit_to(seed, branch, formula):
    _git_run(seed, "checkout", "-B", branch)
    (seed / "Formula").mkdir(exist_ok=True)
    (seed / "Formula" / "job-sluice.rb").write_bytes(formula)
    _git_run(seed, "add", "-A")
    _git_run(seed, "commit", "-m", f"to {branch}")
    _git_run(seed, "push", "--force", "origin", f"HEAD:refs/heads/{branch}")


def _remote_tip(seed, branch):
    out = _git_run(seed, "ls-remote", "origin", f"refs/heads/{branch}").stdout.decode().split()
    return out[0] if out else None


def _remote_formula(seed, branch):
    _git_run(seed, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return _git_run(seed, "show", f"refs/remotes/origin/{branch}:Formula/job-sluice.rb").stdout


def _prepare(tmp_path, url, base_sha, **overrides):
    arguments = {"remote_url": url, "workdir": tmp_path / "work", "target_branch": "main",
                 "default_branch": "main", "base_sha": base_sha, "version": "9.9.0",
                 "formula": _formula_at("9.9.0"), "dry_run": False}
    arguments.update(overrides)
    return hb.prepare_push(**arguments)


def test_a_new_version_is_committed_on_the_base_and_pushed(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_formula(seed, "main") == _formula_at("9.9.0")
    log = _git_run(seed, "log", "-1", "--format=%an|%s", "origin/main").stdout.decode().strip()
    assert log == "sluice-release-please[bot]|job-sluice 9.9.0"


def test_identical_bytes_are_a_no_op_and_publish_pushes_nothing(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.9.0"))
    assert _prepare(tmp_path, url, base) == "noop"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_tip(seed, "main") == base


def test_a_tree_equal_to_the_bases_refuses_when_the_target_does_not_hold_the_formula(tmp_path):
    """The built tree is compared with BASE_SHA's, not the target's. Here the base already holds ours
    while the default branch has moved to another formula, so a no-op reported here would leave the
    formula unpublished behind a green step. Nothing is left for publish to push."""
    url, seed, base = _make_tap(tmp_path, _formula_at("9.9.0"))
    _commit_to(seed, "main", _formula_at("9.8.5"))
    with pytest.raises(Refusal) as err:
        _prepare(tmp_path, url, base)
    assert "refusing rather than reporting a push" in str(err.value)
    assert not (tmp_path / "work" / "push-state.json").exists()


def test_a_newer_tap_version_refuses_before_committing(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.10.0"))
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base)
    assert _remote_tip(seed, "main") == base


def test_a_default_branch_that_moved_since_plan_rejects_the_push(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "main", _formula_at("9.8.5"))
    moved = _remote_tip(seed, "main")
    assert _prepare(tmp_path, url, base) == "push"
    with pytest.raises(Refusal):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_tip(seed, "main") == moved


def test_the_first_scratch_push_creates_the_branch(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == _formula_at("9.9.0")


def test_a_stale_scratch_branch_is_replaced_under_its_lease(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "bump-9.9.0", b"stale\n")
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == _formula_at("9.9.0")


def test_a_scratch_branch_that_moves_after_prepare_is_not_clobbered(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "bump-9.9.0", b"stale\n")
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    _commit_to(seed, "bump-9.9.0", b"someone else\n")
    with pytest.raises(Refusal):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == b"someone else\n"


def test_a_failed_git_command_is_named_past_its_config_pairs(tmp_path):
    with pytest.raises(Refusal) as err:
        hb._git_ok("-c", "user.name=Example", "definitely-not-a-git-command", cwd=tmp_path)
    assert "git definitely-not-a-git-command failed" in str(err.value)


def test_a_relative_workdir_still_stages_the_formula(tmp_path, monkeypatch):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    monkeypatch.chdir(tmp_path)
    assert _prepare(tmp_path, url, base, workdir=pathlib.Path("work")) == "push"


def test_an_absent_default_branch_refuses(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base, target_branch="trunk", default_branch="trunk")


def test_a_failed_tree_read_refuses_rather_than_reading_as_the_bootstrap(tmp_path, monkeypatch):
    """With 9.8.0 at the base this would push. A failed `ls-tree` of the BASE, taken as "no formula
    there", would reach the bootstrap arm and skip the default branch's version refusal, so it must
    refuse. Only the base read fails here, so the target read cannot refuse in its place."""
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    real_git = hb.git

    def failing_ls_tree(*args, cwd=None):
        if args and args[0] == "ls-tree" and base in args:
            return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: simulated")
        return real_git(*args, cwd=cwd)

    monkeypatch.setattr(hb, "git", failing_ls_tree)
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base)


def test_a_tap_with_no_formula_at_the_base_is_the_bootstrap_and_pushes(tmp_path):
    url, seed, base = _make_tap(tmp_path, None)
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_formula(seed, "main") == _formula_at("9.9.0")


def test_a_tag_whose_name_tail_matches_the_target_branch_is_refused(tmp_path):
    """`ls-remote` matches its pattern against the tail of every ref name, so a tag called
    `refs/heads/bump-9.9.0` is listed for that branch. Read as the branch, the lease and the push would
    land on the tag instead of creating the branch."""
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _git_run(seed, "update-ref", "refs/tags/refs/heads/bump-9.9.0", base)
    _git_run(seed, "push", "origin", "refs/tags/refs/heads/bump-9.9.0:refs/tags/refs/heads/bump-9.9.0")
    with pytest.raises(Refusal) as err:
        _prepare(tmp_path, url, base, target_branch="bump-9.9.0")
    assert "expected exactly that one branch" in str(err.value)


def test_publish_refuses_a_push_state_prepared_for_another_branch(tmp_path):
    """Pushed anyway, a commit prepared for the default branch would land on the branch named here as a
    plain push, with no lease."""
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    with pytest.raises(Refusal, match="push-state.json was prepared for 'main'"):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_tip(seed, "bump-9.9.0") is None


def test_publish_refuses_a_push_state_with_an_unknown_decision(tmp_path):
    """Every decision other than `noop` would otherwise push."""
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    state = tmp_path / "work" / "push-state.json"
    state.write_text(json.dumps(dict(json.loads(state.read_text()), decision="pushed")))
    with pytest.raises(Refusal, match="unknown decision 'pushed'"):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_tip(seed, "main") == base


def test_an_unexpected_ls_remote_exit_refuses(tmp_path, monkeypatch):
    """Only exit 0 (listed) and exit 2 (no such ref) say whether the target exists. Another exit read as
    absent would take a failed probe for the first dry run of a version and push a scratch branch."""
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    real_git = hb.git

    def failing_ls_remote(*args, cwd=None):
        if args and args[0] == "ls-remote":
            return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: simulated")
        return real_git(*args, cwd=cwd)

    monkeypatch.setattr(hb, "git", failing_ls_remote)
    with pytest.raises(Refusal, match="could not tell whether bump-9.9.0 exists"):
        _prepare(tmp_path, url, base, target_branch="bump-9.9.0")


def _commit_seed(seed):
    """Commit and push whatever the test put in the seed checkout to main; return the new tip."""
    _git_run(seed, "add", "-A")
    _git_run(seed, "commit", "-m", "tap content")
    _git_run(seed, "push", "origin", "HEAD:refs/heads/main")
    return _git_run(seed, "rev-parse", "HEAD").stdout.decode().strip()


def test_the_taps_attributes_cannot_change_the_committed_bytes(tmp_path):
    """A working-tree encoding named in the tap's `.gitattributes` re-encodes a file committed through
    a work tree. The pushed blob must be exactly the validated bytes."""
    url, seed, _ = _make_tap(tmp_path, None)
    (seed / ".gitattributes").write_text("Formula/*.rb working-tree-encoding=UTF-16LE\n")
    base = _commit_seed(seed)
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_formula(seed, "main") == _formula_at("9.9.0")


def test_a_symlink_at_the_formula_path_is_replaced_and_never_followed(tmp_path):
    """Written through a work tree, the formula would follow the link and overwrite the file it names,
    outside the clone, while git staged the unchanged link and read the push as a no-op."""
    url, seed, _ = _make_tap(tmp_path, None)
    outside = tmp_path / "outside.rb"
    outside.write_bytes(b"outside the clone\n")
    (seed / "Formula").mkdir()
    os.symlink(outside, seed / "Formula" / "job-sluice.rb")
    base = _commit_seed(seed)
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert outside.read_bytes() == b"outside the clone\n"
    assert _remote_formula(seed, "bump-9.9.0") == _formula_at("9.9.0")
    listing = _git_run(seed, "ls-tree", "refs/remotes/origin/bump-9.9.0", "--", "Formula/job-sluice.rb")
    assert listing.stdout.decode().split()[0] == "100644"


def test_a_symlinked_formula_directory_refuses_without_writing_outside(tmp_path):
    url, seed, _ = _make_tap(tmp_path, None)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, seed / "Formula")
    base = _commit_seed(seed)
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base, target_branch="bump-9.9.0")
    assert list(outside.iterdir()) == []


def test_the_pushed_commit_changes_only_the_formula_on_top_of_the_base(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    _git_run(seed, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    assert _git_run(seed, "rev-parse", "refs/remotes/origin/main^").stdout.decode().strip() == base
    changed = _git_run(seed, "diff", "--name-only", base, "refs/remotes/origin/main").stdout.decode()
    assert changed == "Formula/job-sluice.rb\n"


def test_a_hook_left_in_the_clone_does_not_run(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "work" / "tap" / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    os.chmod(hook, 0o755)
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert not marker.exists()


def test_the_same_hook_runs_for_a_push_without_the_helper(tmp_path):
    """The control for the test above: in the same clone, a plain push DOES run the hook, so its
    absence there is the helper's doing."""
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "work" / "tap" / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    os.chmod(hook, 0o755)
    _git_run(tmp_path / "work" / "tap", "push", url, "HEAD:refs/heads/main")
    assert marker.exists()


def test_a_failed_push_does_not_print_the_token(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    with pytest.raises(Refusal) as err:
        hb.publish_push(workdir=tmp_path / "work", push_url=url + "-SECRET-TOKEN-VALUE",
                        target_branch="main", redact="SECRET-TOKEN-VALUE")
    assert "SECRET-TOKEN-VALUE" not in str(err.value)
    assert "***" in str(err.value), "the token never reached git's message, so its redaction is unproven"


def test_push_publish_from_the_cli_pushes_with_the_token_and_redacts_it(tmp_path, monkeypatch):
    """The token reaches the push URL and the redaction only through this subcommand, and a real push
    to the tap is the first place either would otherwise be exercised."""
    recorded = {}
    monkeypatch.setattr(hb, "publish_push", lambda **kwargs: recorded.update(kwargs))
    token = "tap-token-value"
    env = {"TAP_TOKEN": token, "TAP_OWNER": "ExampleOwner", "TARGET_BRANCH": "bump-9.9.0",
           "PUSH_WORKDIR": str(tmp_path / "work")}
    assert hb.main(["push-publish"], env=env) == 0
    # The URL is composed from `token` rather than spelled out: a literal token followed by
    # `@github.com` is email-shaped, and
    # tests/test_fixture_name_neutrality.py::test_every_email_domain_in_test_fixtures_is_reserved_or_allowlisted
    # would read it as an address at a real host.
    assert recorded == {
        "workdir": tmp_path / "work",
        "push_url": f"https://x-access-token:{token}@github.com/exampleowner/homebrew-tap.git",
        "target_branch": "bump-9.9.0",
        "redact": token,
    }


def test_push_prepare_from_the_cli_maps_each_variable_to_its_argument(tmp_path, monkeypatch, capsys):
    recorded = {}

    def record(**kwargs):
        recorded.update(kwargs)
        return "noop"

    monkeypatch.setattr(hb, "prepare_push", record)
    formula = tmp_path / "job-sluice.rb"
    formula.write_bytes(b"formula bytes\n")
    env = {"TAP_OWNER": "ExampleOwner", "PUSH_WORKDIR": str(tmp_path / "work"),
           "TARGET_BRANCH": "bump-9.9.0", "DEFAULT_BRANCH": "main", "BASE_SHA": "f" * 40,
           "VERSION": "9.9.0", "MERGED_FORMULA": str(formula), "CALLER": "dry run"}
    assert hb.main(["push-prepare"], env=env) == 0
    assert recorded == {
        "remote_url": "https://github.com/exampleowner/homebrew-tap.git",
        "workdir": tmp_path / "work",
        "target_branch": "bump-9.9.0",
        "default_branch": "main",
        "base_sha": "f" * 40,
        "version": "9.9.0",
        "formula": b"formula bytes\n",
        "dry_run": True,
    }
    assert capsys.readouterr().out == "push-prepare decided: noop\n"


def test_push_prepare_from_the_cli_refuses_an_unknown_caller(tmp_path, monkeypatch, capsys):
    """The caller decides whether push_decision applies the dry run's default-branch refusal, so a value
    that is neither caller refuses before anything is prepared."""
    recorded = {}
    monkeypatch.setattr(hb, "prepare_push", lambda **kwargs: recorded.update(kwargs) or "noop")
    formula = tmp_path / "job-sluice.rb"
    formula.write_bytes(b"formula bytes\n")
    env = {"TAP_OWNER": "ExampleOwner", "PUSH_WORKDIR": str(tmp_path / "work"),
           "TARGET_BRANCH": "main", "DEFAULT_BRANCH": "main", "BASE_SHA": "f" * 40,
           "VERSION": "9.9.0", "MERGED_FORMULA": str(formula), "CALLER": "Dry Run"}
    assert hb.main(["push-prepare"], env=env) == 1
    assert capsys.readouterr().out.startswith("::error::")
    assert recorded == {}


# --- one derivation per fact, over the script's own source --------------------------------------


def _functions():
    return {node.name: node for node in ast.walk(ast.parse(SCRIPT.read_text()))
            if isinstance(node, ast.FunctionDef)}


def _body(function):
    """A function's statements without its docstring, which may mention what it must not do."""
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return [node for statement in body for node in ast.walk(statement)]


def _callers(name):
    return {fname for fname, function in _functions().items()
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
                   for n in _body(function))}


def _holding(text):
    return {fname for fname, function in _functions().items()
            if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and text in n.value
                   for n in _body(function))}


def test_render_has_exactly_two_callers():
    """formula renders to WRITE the formula; push's validator re-renders to COMPARE (spec, §2)."""
    assert _callers("_render") == {"cmd_render", "validate_formula"}


def test_the_tag_root_url_run_attempt_and_default_branch_are_derived_only_for_plan():
    assert _callers("compose_tag") == {"build_plan"}
    assert _callers("compose_root_url") == {"build_plan"}
    assert _callers("build_plan") == {"cmd_plan"}
    assert _holding("RUN_ATTEMPT") == {"cmd_plan"}
    assert _holding("--symref") == {"cmd_plan"}
    assert _holding("releases/download") == {"compose_root_url"}
    assert _holding("--exit-code") == {"prepare_push"}
    assert _holding("symbolic-ref") == set() and _holding("set-head") == set()


def test_release_writes_happen_only_inside_upload_bottles():
    calls = [(fname, n) for fname, function in _functions().items() for n in _body(function)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "github_request"]
    assert calls, "found no github_request call; the sweep below proves nothing"
    assert all(len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) for _, n in calls), (
        "every github_request call must pass its method as a literal, or this sweep cannot see it")
    writers = {fname for fname, n in calls if n.args[1].value != "GET"}
    assert writers == {"_create_draft_release", "_upload_asset", "_publish_release"}
    for writer in writers:
        assert _callers(writer) == {"upload_bottles"}
    assert _callers("upload_bottles") == {"cmd_upload_bottles"}


def test_the_network_is_reached_only_through_github_request_and_plans_pypi_read():
    """`http(...)` is the one network seam, and `http_request` behind it the one `urlopen`. A bare call
    anywhere else could write to a release without passing the sweep above; `cmd_plan`'s is a GET to
    PyPI, with no body and no method."""
    calls = {}
    for fname, function in _functions().items():
        for node in _body(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "http":
                calls.setdefault(fname, []).append(node)
    assert set(calls) == {"github_request", "cmd_plan"}, sorted(calls)
    (call,) = calls["cmd_plan"]
    (request,) = call.args
    assert isinstance(request, ast.Call) and len(request.args) == 1
    assert not any(keyword.arg in ("method", "data") for keyword in request.keywords)
    urlopens = {fname for fname, function in _functions().items() for node in _body(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "urlopen"}
    assert urlopens == {"http_request"}, sorted(urlopens)
    assert _callers("http_request") == set(), "http_request is injected as `http`, never called by name"


def test_both_formula_subcommands_read_the_same_bytes():
    assert _callers("_read_formula") == {"cmd_validate_formula", "cmd_push_prepare"}
    assert _holding("MERGED_FORMULA") == {"cmd_validate_formula", "cmd_push_prepare"}


def test_every_git_command_goes_through_the_hook_disabling_helper():
    tree = ast.parse(SCRIPT.read_text())
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert len(runs) == 1, f"expected the one subprocess call inside git(), found {len(runs)}"
    # Same parse: ast nodes compare by identity, so a node from a second parse is never `in` it.
    assert any(node is runs[0] for node in ast.walk(functions["git"])), "the subprocess call is not in git()"
    command = runs[0].args[0]
    assert [e.value for e in command.elts[:3]] == ["git", "-c", "core.hooksPath=/dev/null"]


def test_the_helper_and_what_it_imports_are_standard_library_only():
    """The token jobs run `python3 -P scripts/homebrew_bottles.py` on a bare runner Python, so every
    import it reaches, following its `scripts.*` imports, must be the standard library or one of these
    two files. Collected from every import statement, function-local ones included."""
    local = {"scripts.homebrew_bottles": SCRIPT, "scripts.render_homebrew_formula": ROOT / "scripts" / "render_homebrew_formula.py"}
    seen, pending, third_party, reached = set(), ["scripts.homebrew_bottles"], [], set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        for node in ast.walk(ast.parse(local[module].read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] == "scripts":
                    assert name in local, f"{module} imports {name}, which this roster does not cover"
                    reached.add(name)
                    pending.append(name)
                elif name.split(".")[0] not in sys.stdlib_module_names:
                    third_party.append((module, name))
    assert reached == {"scripts.render_homebrew_formula"}, reached
    assert not third_party, third_party
