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
import hashlib
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
    with pytest.raises(Refusal):
        hb.check_produced_tag(data, declared)


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
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=declared)


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
    with pytest.raises(Refusal):
        hb.check_merged_tags(_merged(), declared)


def _block_line(text, startswith):
    return next(line for line in text.splitlines() if line.startswith(startswith))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace(_block_line(t, "    root_url "), _block_line(t, "    root_url ") + "\n    rebuild 1"),
        lambda t: t + "\n  bottle do\n  end\n",
        lambda t: t.replace("  bottle do\n", "  bottle_block do\n"),
        lambda t: t.replace(_block_line(t, "    sha256 cellar:"), _block_line(t, "    sha256 cellar:") + '; system "x"'),
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
