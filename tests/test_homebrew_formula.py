"""Offline pins for the Homebrew formula renderer (#104, PR 6 of 7).

EVERY expected value below is a literal restated HERE by a human. None is imported from the
renderer module -- this file binds it exactly once, via the one `import` line below. That is
not style: the formula is machine-generated, so if the expectation came from the generator both
sides would move together and no assertion could ever fail. Four review rounds of this design
lost that property in four different ways -- deriving from pyproject, then importing the
producer's constant, then enforcing it by a grep in a checklist, then (found live, after all
three were closed) an AST sweep keyed on the exact import spelling, which a submodule alias one
level up (`from scripts import X as _r`) walked straight past.
TWO guards hold the property between them, and neither is sufficient alone.
`test_the_expectations_are_not_imported_from_the_renderer` forbids a second textual spelling of
the renderer module's name anywhere in this file, with a SOURCE-TEXT occurrence count instead of
an enumerated list of import spellings -- see that test's own docstring for why, and for why
this docstring says "the renderer module" rather than naming it: that count would catch a
second, wholly innocent prose mention exactly like it catches a sneaky import, so the name is
deliberately spelled out only once in this whole file. What the count CANNOT see is a reach
through the one binding this file legitimately holds -- `render.__globals__[...]` names no
module at all -- so `test_every_expected_constant_is_built_only_from_literals` holds that half,
by refusing any `_EXPECTED*` right-hand side that is not built purely from literals.

Mirrors tests/test_linux_packages_channel.py, the sibling channel's guard: import the script's
FUNCTION, restate its expected OUTPUT independently, compare.
"""
import ast
import os
import pathlib
import re
import shlex
import tomllib

import pytest

from scripts.render_homebrew_formula import render

ROOT = pathlib.Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"

# Fixture release metadata. Synthetic and offline -- no network, no real release needed. An
# RFC 2606 reserved domain rather than a real PyPI host (files.pythonhosted.org): CodeRabbit
# flagged the real host as an unnecessary use of live infrastructure in a fixture that only
# needs to look like a plausible sdist URL, never to resolve. Shape preserved -- scheme, path
# depth, a job_sluice-<version>.tar.gz filename -- so the swap/value assertions below still mean
# the same thing.
# No "version" key: render() takes none -- see its docstring for why a version argument would
# be a parameter nothing reads. The "9.9.9" in the URL is just part of a realistic sdist
# filename, not something any assertion below reads back out under that name.
FIXTURE = {
    "sdist_url": "https://example.invalid/packages/ab/cd/job_sluice-9.9.9.tar.gz",
    "sha256": "0" * 64,
}

# The shipping scope, restated. The renderer module has its own copy; these two must agree,
# and the ONLY way to make them disagree is a human editing one of them.
_EXPECTED_EXTRAS = {"render", "google", "mcp", "completion"}


def _pyproject_extras() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    return set(data["project"]["optional-dependencies"])


def test_the_formula_declares_exactly_the_shipped_extras():
    """EQUALITY against the test's own literal supplies non-vacuity; the subset against
    pyproject catches an extra that does not exist. Same two-step as
    test_the_dockerfile_installs_exactly_the_expected_extras, and for the same reason."""
    formula = render(**FIXTURE)
    match = re.search(r'package_name:\s*"job-sluice\[([a-z,]+)\]"', formula)
    assert match, (
        "could not find `pypi_packages package_name:` with a bracketed extras list in the "
        "rendered formula; every assertion below would pass vacuously on an empty set. "
        f"Rendered:\n{formula}"
    )
    found = set(match.group(1).split(","))
    assert found == _EXPECTED_EXTRAS, (
        f"the formula ships {sorted(found)}, expected {sorted(_EXPECTED_EXTRAS)}"
    )
    declared = _pyproject_extras()
    assert declared, "parsed no extras from pyproject.toml; the subset check below is vacuous"
    assert found <= declared, (
        f"the formula names extras pyproject.toml does not declare: {sorted(found - declared)}"
    )


# The formulae depended on AND excluded, restated independently of the renderer's own tuple.
# Both directions are asserted below because they fail differently: a name excluded but not
# depended on is an ImportError the moment a user runs the CLI, while one depended on but not
# excluded silently vendors a second copy -- re-adding a Rust build the whole approach exists
# to remove.
_EXPECTED_IMPORTABLE = {"cffi", "cryptography", "pillow", "pydantic", "rpds-py"}
# Emitted as `depends_on` but NOT excluded: the interpreter and the native tree are not Python
# packages, so `exclude_packages` has nothing to say about them.
# Emitted as `depends_on` but NOT excluded: these are native libraries, not Python
# packages, so `exclude_packages` has nothing to say about them. `libyaml` joined when
# `brew audit` named it for pyyaml's C extension -- restated here BY HAND, which is the
# point: a new native dependency must be a deliberate edit in this file, not something
# that follows the renderer automatically.
_EXPECTED_NON_PACKAGE_DEPENDS = {"pango", "libyaml"}


def _depends_on(formula: str) -> set[str]:
    found = set(re.findall(r'^\s*depends_on "([^"]+)"', formula, re.MULTILINE))
    assert found, f"no depends_on lines parsed; every check on them is vacuous:\n{formula}"
    return found


def _exclude_packages(formula: str) -> set[str]:
    match = re.search(r"exclude_packages:\s*%w\[([^\]]*)\]", formula)
    assert match, f"no exclude_packages stanza parsed; checks on it are vacuous:\n{formula}"
    found = set(match.group(1).split())
    assert found, "exclude_packages parsed as empty"
    return found


def test_every_excluded_package_is_also_depended_on():
    """Excluded-but-not-depended-on is an ImportError at runtime: `exclude_packages` tells brew
    not to vendor it, so something else must supply it."""
    formula = render(**FIXTURE)
    excluded = _exclude_packages(formula)
    assert excluded == _EXPECTED_IMPORTABLE, (
        f"excluded {sorted(excluded)}, expected {sorted(_EXPECTED_IMPORTABLE)}"
    )
    assert excluded <= _depends_on(formula), (
        f"excluded but not depended on: {sorted(excluded - _depends_on(formula))}. Nothing "
        f"would supply these at runtime."
    )


def test_every_python_package_depended_on_is_also_excluded():
    """The other direction, which a subset check alone misses: a python package depended on but
    not excluded gets vendored a SECOND time from source -- re-adding a Rust build."""
    formula = render(**FIXTURE)
    depends = _depends_on(formula)
    python_pkg_depends = depends - _EXPECTED_NON_PACKAGE_DEPENDS
    python_pkg_depends = {d for d in python_pkg_depends if not d.startswith("python@")}
    assert python_pkg_depends == _EXPECTED_IMPORTABLE, (
        f"python-package depends_on is {sorted(python_pkg_depends)}, expected "
        f"{sorted(_EXPECTED_IMPORTABLE)}; anything here that is not excluded gets vendored twice"
    )


# Never depended on. Restated here rather than imported, same as everything else in this file.
# `httpx` is on the list for a DIFFERENT reason from the other four and the distinction matters:
# nothing in our closure is named `httpx` at all (ours is `httpx2`), so a rule phrased as "the
# formula name matches a package we use" would not reach it.
_EXPECTED_FORBIDDEN = {"click", "brotli", "zopfli", "protobuf", "httpx"}

# The only names this module may import from the renderer. `render` is the SUBJECT under test;
# an expectation imported from the producer would make every assertion above unfalsifiable.
_ALLOWED_RENDERER_IMPORTS = {"render"}


def test_the_formula_depends_on_a_brewed_python():
    """THE payoff mechanism. Homebrew's CPython patches ctypes' dyld fallback to include the
    Homebrew prefix; that is the entire reason this channel resolves cairo/pango on macOS
    without DYLD_FALLBACK_LIBRARY_PATH. Deleting this line leaves a formula that still builds
    and still passes `--version`, so nothing else here would catch it."""
    formula = render(**FIXTURE)
    match = re.search(r'depends_on "python@(\d+)\.(\d+)"', formula)
    assert match, (
        "the formula names no `depends_on \"python@X.Y\"`. Without a BREWED interpreter the "
        "renderer row goes dead on macOS and this channel has no reason to exist."
    )
    major, minor = int(match.group(1)), int(match.group(2))
    data = tomllib.loads(PYPROJECT.read_text())

    floor = data["project"]["requires-python"]
    floor_match = re.search(r"(\d+)\.(\d+)", floor)
    assert floor_match, f"could not parse a floor from requires-python {floor!r}"
    assert (major, minor) >= (int(floor_match.group(1)), int(floor_match.group(2))), (
        f"the formula depends on python@{major}.{minor}, below requires-python {floor}"
    )

    # The floor alone is one-sided: it accepts a python that does not exist yet. Classifier
    # membership is the upper bound, and pyproject declares one per supported version.
    supported = {
        tuple(int(p) for p in m.groups())
        for c in data["project"]["classifiers"]
        if (m := re.fullmatch(r"Programming Language :: Python :: (\d+)\.(\d+)", c))
    }
    assert supported, "parsed no per-version Python classifiers; the check below is vacuous"
    assert (major, minor) in supported, (
        f"the formula depends on python@{major}.{minor}, which pyproject.toml declares no "
        f"classifier for. Supported: {sorted(supported)}"
    )


def test_the_depends_on_lines_are_alphabetical():
    """`brew audit --strict` runs RuboCop, and FormulaAudit/DependencyOrder requires
    alphabetical `depends_on`. That auditor only runs on a macOS runner at dispatch or
    release time, so without this pin the first feedback is a failed job -- measured: the
    first real dispatch drew five separate "should be put before" errors at once.

    Compares the rendered lines against their own sort rather than against a literal list,
    because the ORDER is the property; the membership is pinned by the tests above.
    """
    lines = [ln for ln in render(**FIXTURE).splitlines() if ln.strip().startswith("depends_on ")]
    assert lines, "no depends_on lines rendered; this check would pass vacuously"
    assert lines == sorted(lines), (
        "`depends_on` must be alphabetical for FormulaAudit/DependencyOrder. Got:\n"
        + "\n".join(lines)
    )


def test_the_formula_depends_on_libyaml_for_pyyaml():
    """`brew update-python-resources` puts pyyaml in the resource tree, and its C extension
    links against libyaml -- `brew audit` names the missing dependency explicitly rather than
    letting the build fail later. Pinned here because the audit is macOS-only."""
    assert 'depends_on "libyaml"' in render(**FIXTURE)


def test_no_forbidden_formula_is_depended_on():
    """homebrew-core carries formulae whose names match ours but whose content is different
    software. `brotli` ships the SAME version string as the Python binding, so a version
    comparison would certify the wrong one."""
    formula = render(**FIXTURE)
    hit = _depends_on(formula) & _EXPECTED_FORBIDDEN
    assert not hit, (
        f"the formula depends on {sorted(hit)}, which in homebrew-core is different software "
        f"from the Python package of that name"
    )


def test_a_forbidden_formula_landing_in_depends_raises(monkeypatch):
    """The renderer's `_FORBIDDEN_FORMULAE` tuple was pure documentation until this test and
    the raise it pins existed: eight lines of comment nothing read, since `render()` built
    `depends` from the OTHER three tuples alone. Two reviewers independently flagged it as dead
    code -- ruff does not warn on an unused module-level constant.

    This is the producer-side second line of defence, behind (not instead of)
    `test_no_forbidden_formula_is_depended_on` above, which only catches the defect on the
    rendered TEXT, after the fact. `render()` must refuse to emit anything at all.

    Injected via `render.__globals__` -- the running module's own namespace, reached through an
    attribute of the one binding this file is allowed to hold -- rather than a second import of
    the renderer, which `test_the_expectations_are_not_imported_from_the_renderer` below forbids.
    `forbidden` is a TEST-side literal, matching the module docstring's discipline for
    everything else in this file.
    """
    forbidden = "click"
    monkeypatch.setitem(render.__globals__, "_NATIVE_FORMULAE", (forbidden,))
    with pytest.raises(ValueError, match=forbidden):
        render(**FIXTURE)


def test_the_expectations_are_not_imported_from_the_renderer():
    """The property that makes every other test in this file able to fail.

    A test that imports the value it asserts on compares a constant to itself. This design lost
    that property FOUR times: deriving the extras from pyproject, then importing the renderer's
    own constant, then writing the rule into a checklist instead of a test, and then -- found
    live, after all three of those were closed -- an AST sweep keyed on the exact import
    spelling (an `ImportFrom` naming the renderer's FULL dotted module path, or a dotted
    `Import`), which walks straight past a `from scripts import <the renderer's own leaf name>
    as _r`-shaped alias: that statement's `ImportFrom.module` is just `"scripts"`, not the full
    dotted path, so the matcher never sees it. Witnessed by execution: adding exactly that
    shape of import to this file, with `_EXPECTED_EXTRAS` swapped for the renderer's own
    shipped-extras constant read through that alias, and `mcp` dropped from the renderer's own
    tuple, passed the full file -- 11 green -- the precise defect this test exists to prevent.

    THE CHECK: assert that this module's own SOURCE TEXT contains the renderer module's bare
    name exactly ONCE. WHAT THAT GUARANTEES, precisely and no more: no SECOND textual spelling
    of that name appears anywhere in this file. Every import that NAMES the module -- a
    submodule alias, a dotted `Import`, `importlib.import_module("scripts.render_...")` --
    necessarily spells it out somewhere in the source, so one substring count covers all of
    those without enumerating them, which is exactly what the fourth defect's spelling-keyed
    AST sweep failed to do.

    WHAT IT CANNOT SEE. An earlier version of this docstring called the count "total" and said
    it "closes every spelling in one assertion". Both were false, and witnessed false: the
    count cannot see a reach through an ALREADY-IMPORTED object's own attributes, because such
    a reach spells no module name at all. `render.__globals__["_SHIPPED_EXTRAS"]` is the live
    example -- and note this file uses `render.__globals__` legitimately one test above, so the
    shape is not even foreign to it -- while `sys.modules[render.__module__]` and
    `importlib.import_module(render.__module__)` are the same shape by a different route.
    Measured: replacing `_EXPECTED_EXTRAS` with that globals read and dropping `mcp` from the
    renderer's own tuple left every other test in this file green, with this count still
    reading 1 -- the shipped extras and the expectation had become one source again, and
    nothing here could tell.

    That surviving shape is closed by a DIFFERENT guard below, not by this one:
    `test_every_expected_constant_is_built_only_from_literals` walks this file's module-level
    `_EXPECTED*` assignments and refuses any right-hand side not built purely from literals --
    no call, no attribute access, no subscript. The two are complementary and neither subsumes
    the other: this test forbids a second BINDING to the producer, that one forbids an
    expectation reaching the producer through the one binding this file legitimately holds.

    The count is also blunter than the AST sweep it replaces in the other direction: it cannot
    say WHERE a second occurrence came from, and it fires just as readily on a second, wholly
    innocent PROSE mention of the renderer's name -- which is why no comment or docstring
    anywhere else in this file spells it out again; every other mention says "the renderer" or
    "the renderer module" instead. It cannot tell a legitimate second mention from a sneaky
    import, which is exactly why this file avoids ever writing one.

    The module name is derived from `render.__module__` (the renderer's own dotted path, known
    only at runtime) rather than written out as a literal here -- writing it as a literal would
    itself be the second occurrence the assertion below exists to forbid.
    """
    module_name = render.__module__
    module_leaf = module_name.rsplit(".", 1)[-1]
    source = pathlib.Path(__file__).read_text()
    occurrences = source.count(module_leaf)
    assert occurrences == 1, (
        f"the renderer module's own name appears {occurrences} times in this file's source; "
        f"expected exactly 1 (this file's own `from {module_name} import render` line). A "
        f"second occurrence -- an aliased or submodule import, an `importlib.import_module` "
        f"call, or even a stray prose mention -- is exactly what this guard exists to catch: an "
        f"EXPECTATION reachable from a second binding to the producer makes every assertion in "
        f"this file unfalsifiable, the defect that has now shipped four times in this design's "
        f"review."
    )

    # Kept alongside the count above because it names WHICH bindings were imported, a
    # diagnostic the count alone cannot give -- belt on top of the suspenders above; the count
    # is what actually holds the property. Compares against `module_name` (derived above, not
    # written out a second time), so keeping this check does not itself add a second occurrence
    # of the name the assertion above forbids. Local bindings come from `asname or name`, not
    # the imported name: a sweep keyed on the original walks straight past
    # `from x import _SHIPPED_EXTRAS as _EXPECTED`, the exact alias hazard CLAUDE.md records.
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(module_name):
                    imported.add(alias.asname or alias.name)
    # SCOPE first: a sweep matching nothing would make the equality below vacuously true.
    assert imported, (
        "this sweep found no import from the renderer module at all, but this module imports "
        "`render`. The matcher is broken, and the equality below proves nothing."
    )
    assert imported == _ALLOWED_RENDERER_IMPORTS, (
        f"this module imports {sorted(imported)} from the renderer; only "
        f"{sorted(_ALLOWED_RENDERER_IMPORTS)} is allowed. An EXPECTATION imported from the "
        f"producer makes the assertion that uses it unfalsifiable -- that defect shipped four "
        f"times in this design's review. Restate the value here instead."
    )


# The container nodes an expectation may be built from. `ast.Name` is handled separately in
# `_is_literal_expression` -- allowed ONLY where it names another `_EXPECTED*` constant this
# sweep has ALREADY validated, so an alias chain cannot launder a producer read through one
# extra hop. Everything absent from this handling is refused BY OMISSION rather than by a
# blocklist: `ast.Call`, `ast.Attribute` and `ast.Subscript` are the three shapes that actually
# reach the producer -- `set(render.__globals__["_SHIPPED_EXTRAS"])` is all three at once -- but
# a fourth shape nobody thought of is refused too, which a blocklist naming those three would
# not be. That is the whole reason this is written as an allow-list; the enumerate-the-spellings
# approach is precisely what failed in this design's fourth regression.
_LITERAL_CONTAINERS = (ast.Set, ast.Tuple, ast.List)


def _is_literal_expression(node, already_validated: set[str]) -> bool:
    """Is `node` built purely from literals (and already-validated `_EXPECTED*` names)?"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in already_validated
    if isinstance(node, _LITERAL_CONTAINERS):
        # `node.elts` rather than `ast.iter_child_nodes`: the latter also yields the container's
        # `ctx` (an `ast.Load`), which is not a literal and would refuse every list and tuple in
        # this file. A `*other` element is an `ast.Starred`, absent from the handling above, so
        # it is refused -- unpacking is a reach through another object.
        return all(_is_literal_expression(e, already_validated) for e in node.elts)
    if isinstance(node, ast.Dict):
        # A `**other` entry has a None key. Refused rather than walked past, same reasoning.
        return all(
            k is not None
            and _is_literal_expression(k, already_validated)
            and _is_literal_expression(v, already_validated)
            for k, v in zip(node.keys, node.values)
        )
    return False


def test_every_expected_constant_is_built_only_from_literals():
    """The half of the two-source property the occurrence count above cannot hold.

    That count forbids a second textual SPELLING of the renderer module's name in this file.
    It says nothing about an expectation reached through the ONE binding this file legitimately
    holds -- `render.__globals__["_SHIPPED_EXTRAS"]` names no module and so keeps the count at
    1. Witnessed: rewriting `_EXPECTED_EXTRAS` as that read, and dropping `mcp` from the
    renderer's own tuple, left every other test in this file green -- this one is what goes
    red on it.

    So this guard works on the SHAPE of every module-level `_EXPECTED*` right-hand side
    instead: literals only. `ast.Call`, `ast.Attribute` and `ast.Subscript` are exactly what
    that read is made of, and all three are refused -- along with anything else not on the
    allow-list in `_is_literal_expression` above, which is what stops this becoming a fifth
    enumerated-spellings guard of the kind that has already failed here four times.

    An `ast.Name` is permitted only when it refers to an `_EXPECTED*` constant validated
    EARLIER in the file, which is why this walks the module body in source order and grows
    `validated` as it goes: `_EXPECTED_A = something_impure` followed by `_EXPECTED_B =
    _EXPECTED_A` must fail at A and never license B.
    """
    source = pathlib.Path(__file__).read_text()
    constants: dict[str, ast.expr] = {}
    # Module BODY, not `ast.walk`: what this property is about is the module-level constants
    # the assertions read. A local variable inside one test is scoped to that test and cannot
    # silently become another test's expectation.
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id] if node.value is not None else []
        else:
            continue
        for name in names:
            if name.startswith("_EXPECTED"):
                constants[name] = node.value
    # SCOPE FIRST. A matcher that enumerated nothing -- a renamed prefix convention, a walk
    # that stopped seeing `ast.Assign` -- would make the loop below vacuously true, `all([])`
    # style, and this file's whole falsifiability would rest on a check running zero times.
    assert constants, (
        "this sweep found no module-level `_EXPECTED*` assignment at all, but this file "
        "defines several. The matcher is broken and the loop below proves nothing."
    )
    validated: set[str] = set()
    for name, value in constants.items():
        assert _is_literal_expression(value, validated), (
            f"`{name}` is not built purely from literals; its right-hand side is "
            f"{ast.dump(value)[:300]}. A call, attribute access or subscript here can reach "
            f"the renderer's own constants through this file's one legitimate binding "
            f"(`render.__globals__[...]`, `sys.modules[render.__module__]`, ...) without ever "
            f"spelling the module's name -- so the occurrence count above stays green while "
            f"the expectation and the producer become one source again, which is the exact "
            f"defect that has now shipped four times in this design's review. Restate the "
            f"value here as a literal."
        )
        validated.add(name)


# ---------------------------------------------------------------------------
# Four mutants a full run of this file, before these four tests existed, did not catch --
# measured, not assumed: each was produced by DELETING (never adding) the real line from the
# renderer script, restoring via a `cp` backup rather than `git checkout`, and confirming the
# suite went green regardless. See CLAUDE.md's mutation-testing section for why the restore
# mechanism matters (a `git checkout` mid-witness can wipe uncommitted changes).
# ---------------------------------------------------------------------------


def test_the_url_and_sha256_lines_are_not_swapped():
    """render()'s own docstring names this exact defect as the reason for its keyword-only
    signature: three same-typed strings in a row is the shape where a positional swap (or,
    equivalently, an accidental transposition inside the f-string body) produces a
    plausible-looking formula carrying the WRONG digest for the right URL, or vice versa --
    and nothing before `brew audit --strict --online` would catch it, by which point the
    release is already public."""
    formula = render(**FIXTURE)
    url_match = re.search(r'^\s*url "([^"]*)"', formula, re.MULTILINE)
    sha_match = re.search(r'^\s*sha256 "([^"]*)"', formula, re.MULTILINE)
    assert url_match, f"no `url \"...\"` line found in the rendered formula:\n{formula}"
    assert sha_match, f"no `sha256 \"...\"` line found in the rendered formula:\n{formula}"
    assert url_match.group(1) == FIXTURE["sdist_url"], (
        f"the `url` line reads {url_match.group(1)!r}, expected the sdist url "
        f"{FIXTURE['sdist_url']!r} -- this is the sha256 value, or something else, in url's "
        f"place"
    )
    assert sha_match.group(1) == FIXTURE["sha256"], (
        f"the `sha256` line reads {sha_match.group(1)!r}, expected {FIXTURE['sha256']!r} -- "
        f"this is the sdist url, or something else, in sha256's place"
    )


def test_the_sha256_stanza_is_present():
    """A DELETED `sha256` stanza is a formula `brew audit`/`brew install` cannot verify the
    download against -- distinct from the swap above (which leaves a stanza present but wrong),
    this closes the stanza's outright absence."""
    formula = render(**FIXTURE)
    assert re.search(r'^\s*sha256 "[0-9a-f]{64}"', formula, re.MULTILINE), (
        f"no `sha256 \"...\"` stanza found at all in the rendered formula; `brew audit` and "
        f"`brew install` verify the download against this line, so its absence means the "
        f"sdist is installed with no integrity check whatsoever:\n{formula}"
    )


def test_the_formula_includes_the_virtualenv_language_module():
    """`install`'s `virtualenv_install_with_resources` call is a method `Language::Python::
    Virtualenv` mixes in -- delete the `include` and the formula fails to load at all, before
    `brew audit` gets far enough to report anything specific."""
    formula = render(**FIXTURE)
    assert "include Language::Python::Virtualenv" in formula, (
        f"the formula no longer `include`s Language::Python::Virtualenv, so `install`'s "
        f"`virtualenv_install_with_resources` has no method to resolve:\n{formula}"
    )


def test_the_formula_has_a_test_do_block_with_its_payoff_assertion():
    """A deleted `test do` block is a strictly larger version of the missing-`python@` defect
    `test_the_formula_depends_on_a_brewed_python` exists to catch: `brew test` would then run
    NOTHING at all rather than running the wrong thing, and a negative check (`test do` absent)
    is exactly the shape CLAUDE.md warns finds-nothing-passes on, so this checks for the
    block's own PAYOFF content rather than only the `test do` label, which a mutant could in
    principle leave behind on its own."""
    formula = render(**FIXTURE)
    assert re.search(r"\n\s*test do\b", formula), (
        f"no `test do` block found in the rendered formula; `brew test` would run nothing at "
        f"all:\n{formula}"
    )
    assert 'assert_path_exists testpath/"t.pdf"' in formula, (
        f"the formula's `test do` block is missing its payoff assertion (the rendered-PDF "
        f"check); `brew test` would then exercise nothing this channel actually exists to "
        f"prove:\n{formula}"
    )


# The exit status the `test do` block asserts `doctor --offline` returns. Restated here as a
# literal like every other expectation in this file -- and then, unlike every other expectation
# in this file, checked against the program itself. A literal restated by a human is exactly
# what the renderer already carried, in a comment, when this broke.
_EXPECTED_DOCTOR_EXIT_CODE = 0

# BOTH spellings, with the code optional -- an omitted one means 0, which is `shell_output`'s
# documented default. An earlier cut of this guard demanded the explicit two-argument form, on
# the reasoning that a claim worth making is worth writing down. That was wrong in a way only
# execution showed: `brew audit`'s RuboCop pass refuses a redundant `, 0`
# (`FormulaAudit/Test`), so the form this guard required was the one form the release job
# rejects, and 2.9.1's `homebrew` job failed on it. Requiring a spelling is what broke it;
# reading either and comparing the MEANING is what this needs to do.
_DOCTOR_EXIT_ASSERTION = re.compile(
    r'shell_output\("#\{bin\}/job-sluice doctor(?P<flags>[^"]*)"(?:,\s*(?P<code>\d+))?\)')

# The row assertion the formula makes about doctor's TABLE. Captured so the guard below can run
# doctor the way the formula does and check such a row is actually there -- see that test for
# why the state token is relaxed.
_DOCTOR_ROW_ASSERTION = re.compile(
    r'assert_match\(/(?P<pattern>[^/]+)/, report\)')


def test_the_formula_expects_the_real_clean_install_exit_code(monkeypatch, tmp_path, capsys):
    r"""The formula asserts an exit STATUS, and nothing here checked it against the program.

    `shell_output(cmd, N)` IS an assertion: `brew test` fails unless the command exits N. That
    number was `1`, justified by a comment ending "Measured." -- true when written, and
    unfalsifiable afterwards. 2.7.0's `feat(doctor): a verdict by default, and exit 0 on a clean
    install` inverted the contract (#243: a component the user has not SUPPLIED yet is SETUP and
    never reaches the exit code, so a fresh machine exits 0), the literal did not move, and the
    `homebrew` job failed on 2.7.0 and 2.8.0 -- the public tap stayed at the last version whose
    job passed, while PyPI, Docker and deb/rpm all shipped from those same runs. Every guard in
    this file stayed green throughout, and the reason is structural rather than bad luck: all of
    them read the rendered TEXT, and none ran the program the text makes a claim about.

    So this one does both, and neither half is redundant:

      * the code the formula asserts equals `_EXPECTED_DOCTOR_EXIT_CODE`, a literal restated
        here by a human -- this file's whole discipline, and what stops the renderer drifting;
      * `_EXPECTED_DOCTOR_EXIT_CODE` is what `doctor --offline` REALLY returns on a clean
        install -- which a human-restated literal cannot give you, since a literal copied from
        a stale comment is precisely what was already there.

    THE SANDBOX IS THE TEST. Reaching a clean install needs THREE things, and the guard is inert
    without any one of them -- measured, by reverting #243 on the program side (`core/doctor.py`
    stamping the `vault_dir` row DEAD unconditionally instead of `DEAD if explicit else SETUP`):

      * `VAULT_DIR` deleted. `tests/conftest.py` NAMES a per-test path and does not create it,
        and #80's rule is that an explicitly-named path is taken as given -- so naming one that
        is absent means it MOVED (DEAD, exit 1), where naming none means it was never set up.
      * the cwd moved. Deleting the variable drops to `core/vault.py`'s SHIPPED DEFAULT, the
        cwd-relative `./vault`, which is the one path `_pin_paths` cannot sandbox. This repo's
        own root holds a gitignored `vault/` that CLAUDE.md's quickstart creates, so without
        `chdir` the run reads the developer's REAL Obsidian vault, `preflight` reports the vault
        present, no `vault_dir` row is emitted at all, and the mutant above SURVIVES 17/17
        green. With the `chdir` it is killed. `tests/test_doctor_verdict.py::
        test_an_unconfigured_vault_at_the_shipped_default_is_setup_and_exits_zero` is the
        sibling that already had to learn this, and says so in its own docstring.
      * every `SLUICE_*`/`CAMOFOX_*` variable gone, by PREFIX. The `test do` block sweeps them
        with `/\A(SLUICE|CAMOFOX)_/` and conftest hand-lists instead, which is narrower:
        `SLUICE_CLAUDE_HOST`, `SLUICE_CLAUDE_PATH` and `SLUICE_LOCATIONS` are read by `sluice/`
        and pinned by nothing. Hand-listed names lose, so this sweeps the same way the formula
        does rather than restating a list that has already been out of date once.

    WHAT THIS DELIBERATELY DOES NOT RESTATE, because two reviewers have now asked: the rest of
    the `test do` block's sandbox is already supplied by `tests/conftest.py::_pin_paths`, which
    is autouse. Measured at the point `main()` runs below, `SEEN_DB`, `TRIAGE_AUDIT`,
    `DOSSIER_DIR`, `SLUICE_CONFIG`, `SLUICE_HEALTH` and `SLUICE_DISABLED` are all unset, and
    `HOME` plus all three `XDG_*_HOME` rungs are already inside `tmp_path`. Re-setting them here
    would put the sandbox in two places: they could drift apart, and this test would then keep
    passing through a `_pin_paths` regression that broke every other test in the suite.
    `tests/test_path_sandbox.py::test_the_sandbox_covers_every_path_env_var` is what stops that
    fixture silently narrowing -- it re-derives the path variables from `sluice/` and fails if
    one is missing. The three PREFIX cases above are the exception precisely because they are
    NOT path variables, so that guard does not see them.

    `main` is imported in the body rather than at module scope, matching
    tests/test_doctor_verdict.py: this module is otherwise a set of offline pins over rendered
    text, and nothing else in it needs the CLI.
    """
    from sluice.cli import main

    formula = render(**FIXTURE)
    match = _DOCTOR_EXIT_ASSERTION.search(formula)
    assert match, (
        f"the formula's `test do` block no longer runs `doctor --offline` through "
        f"`shell_output` at all, so nothing asserts its exit status any more:\n{formula}"
    )
    # An absent code is `shell_output`'s default, which IS 0 -- not "unasserted".
    expected_in_formula = int(match.group("code")) if match.group("code") else 0
    assert expected_in_formula == _EXPECTED_DOCTOR_EXIT_CODE, (
        f"the formula expects `doctor --offline` to exit {expected_in_formula}, but a clean "
        f"install exits {_EXPECTED_DOCTOR_EXIT_CODE} (#243). `brew test` fails the release's "
        f"`homebrew` job on a mismatch, and the tap then goes on serving the PREVIOUS version "
        f"while every other channel ships -- which is what 2.7.0 and 2.8.0 did."
    )

    # The formula's own sandbox, reproduced. All three parts are load-bearing; see the docstring
    # for the mutant each one is what kills.
    for name in [k for k in os.environ if k.startswith(("SLUICE_", "CAMOFOX_"))]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    real = main(["doctor", *shlex.split(match.group("flags"))])
    capsys.readouterr()
    assert real == _EXPECTED_DOCTOR_EXIT_CODE, (
        f"`doctor --offline` exits {real} on a clean install, but the formula (and the literal "
        f"above) expect {_EXPECTED_DOCTOR_EXIT_CODE}. Whichever moved, `brew test` now fails "
        f"every release and the Homebrew channel silently stops updating -- fix both together."
    )



def test_the_formula_asserts_a_doctor_row_that_the_view_it_asks_for_actually_prints(
        monkeypatch, tmp_path, capsys):
    r"""The formula asserts a TABLE ROW. Nothing checked the table was in the output.

    `assert_match(/renderer\s+cv\.renderer\s+ok/, report)` is the payoff of this whole channel:
    it proves WeasyPrint loaded, positively, rather than refuting "dead" (a row that is merely
    ABSENT would satisfy a negative check). But it can only match a view that actually contains
    rows, and #243 moved them: `doctor` prints a VERDICT by default and the table now needs
    `--verbose`. Worse, the default view lists only rows still NEEDING ACTION, so a renderer
    that is `ok` -- precisely the state being asserted -- appears nowhere in it. The assertion
    was unsatisfiable by construction and `brew test` failed the whole release.

    That is the SECOND assertion in this block broken by that one upstream change; the exit
    code above was the first. Fixing the first without auditing the second is what let it
    repeat, so this guard runs doctor THE WAY THE FORMULA ASKS FOR IT -- flags taken from the
    rendered text, not restated here -- and checks a matching row comes back.

    THE STATE TOKEN IS RELAXED, deliberately. The formula wants `ok`, which needs the `render`
    extra AND its native libraries; a bare test environment has neither and reports `setup`.
    Pinning `ok` here would assert the machine's configuration rather than the formula's
    correctness, and would fail in CI for a reason that has nothing to do with the formula.
    What is checkable offline, and what actually broke, is whether the requested view emits a
    row of that SHAPE at all -- measured: one occurrence under `--verbose`, zero without it.
    """
    from sluice.cli import main

    formula = render(**FIXTURE)
    invocation = _DOCTOR_EXIT_ASSERTION.search(formula)
    assert invocation, "the formula no longer runs doctor through shell_output"
    row = _DOCTOR_ROW_ASSERTION.search(formula)
    assert row, (
        f"the formula's `test do` block no longer asserts a doctor row. That assertion is this "
        f"channel's positive proof that WeasyPrint loaded; without it `brew test` proves "
        f"nothing this channel exists for:\n{formula}")

    # The formula's own pattern, with the trailing state token relaxed -- see the docstring.
    shape = re.sub(r"ok$", r"\\w+", row.group("pattern")).replace("\\\\", "\\")

    for name in [k for k in os.environ if k.startswith(("SLUICE_", "CAMOFOX_"))]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    main(["doctor", *shlex.split(invocation.group("flags"))])
    out = capsys.readouterr().out
    assert re.search(shape, out), (
        f"the formula asserts a row matching {row.group('pattern')!r}, but running "
        f"`job-sluice doctor{invocation.group('flags')}` prints no row of that shape. #243 "
        f"moved the table behind `--verbose` and left the default view listing only rows that "
        f"need action -- so an `ok` row appears in neither. `brew test` fails the release on "
        f"this, which is how the Homebrew channel broke a third time.")

def test_the_formula_probes_the_non_render_extras_against_the_brewed_interpreter():
    """`_IMPORTABLE_CORE_FORMULAE` makes the venv depend on homebrew-core's OWN
    pydantic/rpds-py/cffi/... for THIS interpreter's site-packages rather than vendoring them
    from source -- and `mcp` in particular carries a hard pydantic version floor. A skew
    between what this formula ships and what a brewed interpreter's homebrew-core dependencies
    actually provide surfaces as a user-facing ImportError on the mcp/google/completion
    extras, with `brew test` still reporting green, UNLESS something actually imports those
    extras' top-level modules against the same brewed interpreter the WeasyPrint probe (proven
    by `test_the_formula_has_a_test_do_block_with_its_payoff_assertion` above) already proves
    the `render` extra against. Unlike the WeasyPrint probe, a skew here degrades SILENTLY --
    there is no rendered artefact whose absence a later assertion could catch, only an
    ImportError the very first time a user runs `job-sluice mcp serve` or a Google-tracker
    command.

    Verified by execution: DELETING this line (a DELETE, per CLAUDE.md's mutation-testing
    rule -- an ADDED equivalent probe beside it would leave the original still firing and
    prove nothing about THIS assertion) left the full suite green before this test existed.

    The expected import list is a TEST-side literal, restated independently of the renderer --
    this file's whole discipline; see the module docstring and
    `test_the_expectations_are_not_imported_from_the_renderer`.
    """
    formula = render(**FIXTURE)
    expected_imports = "mcp, googleapiclient, argcomplete"
    probe = f'system libexec/"bin/python", "-c", "import {expected_imports}"'
    assert probe in formula, (
        f"the formula's `test do` block no longer contains {probe!r} -- a skew between the "
        f"shipped extras and a brewed interpreter's homebrew-core dependencies would then "
        f"surface as a silent, user-facing ImportError on mcp/google/completion with this job "
        f"still green:\n{formula}"
    )


# ---------------------------------------------------------------------------
# The `test do` block's environment sandbox.
#
# A local `brew test` runs in the MAINTAINER's shell environment. Every one of these variables
# points sluice at real state: a vault of real job notes, a real config, the dedup and health
# stores, the dossier cache, a live camofox server -- and SLUICE_TELEGRAM_TOKEN/CHAT are a
# CREDENTIAL pair core/log.py reads ahead of config and POSTS with. Measured, before this test
# existed: deleting the `\A(SLUICE|CAMOFOX)_` sweep, deleting the explicit `%w[...]` list, and
# deleting XDG_CACHE_HOME each left the whole suite green.
#
# DERIVED, not hand-listed. The check below AST-walks sluice/ for the variables the shipped code
# actually reads and asserts the rendered block covers each one -- CLAUDE.md's "hand-listed
# names lose" lesson, applied to the one place where losing means reading a maintainer's real
# data. The expectations either side of that derivation stay TEST-side literals, like everything
# else in this file.
# ---------------------------------------------------------------------------

# The Ruby prefix sweep, restated. Asserted PRESENT before any name is exempted on the strength
# of it: exempting SLUICE_*/CAMOFOX_* names against a sweep that has been deleted is precisely
# the finds-nothing-passes shape CLAUDE.md warns about.
_EXPECTED_ENV_SWEEP_LINE = r"ENV.delete(k) if k.match?(/\A(SLUICE|CAMOFOX)_/)"

# The same fact in Python, applied to the derived names. Two spellings of one rule, which is
# unavoidable here -- one is Ruby the formula runs, one is Python this test runs -- so the line
# above pins the Ruby and this pins what the exemption below actually means.
_EXPECTED_ENV_SWEEP_PREFIXES = ("SLUICE_", "CAMOFOX_")

# All three rungs, because core/paths.py falls through to the matching one the instant the
# explicitly-named variable is deleted: state (SEEN_DB, SLUICE_HEALTH, TRIAGE_AUDIT,
# SLUICE_DISABLED), config (SLUICE_CONFIG) and cache (DOSSIER_DIR). Leaving any one unset lets
# that rung resolve into the maintainer's REAL XDG directory instead of the sandbox.
_EXPECTED_ENV_XDG_RUNGS = ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")

# Variables sluice reads that deliberately need NO sandboxing: none is path-shaped and none
# names any of sluice's state, so an ambient value changes nothing `brew test` asserts and
# reaches no file of the maintainer's.
#   EDITOR             -- cli.py hands it to the onboarding asker, which `brew test` never runs.
#   *_API_KEY/_BASE_URL -- provider credentials read as INPUTS by core/app.py's backend
#                        construction. `brew test` makes no backend call (`doctor --offline`),
#                        so a key present in the environment is used for nothing. These are a
#                        FORWARD-LOOKING exemption rather than one the sweep exercises today:
#                        core/app.py reaches them through `_PROVIDER_ENV`'s indirection rather
#                        than a literal argument, so the derivation below does not currently
#                        see them at all. Listed anyway so that turning one into a direct
#                        `os.environ.get("ANTHROPIC_API_KEY")` does not fail this test for a
#                        variable that was always fine.
_EXPECTED_ENV_NEEDS_NO_SANDBOX = {
    "EDITOR",
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
}


def _is_os_environ(node) -> bool:
    """Is `node` an `os.environ`-shaped attribute access?

    Keyed on the ATTRIBUTE name rather than on `os.environ` written out, so `import os as _os`
    is still seen -- the same alias hazard CLAUDE.md records for import sweeps.
    """
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _env_names_read_by_sluice() -> tuple[set[str], int]:
    """Every environment variable sluice/ names as a LITERAL, and how many files were walked.

    Three shapes, all of which occur in the shipped tree: `os.environ["X"]`,
    `os.environ.get("X")`/`"X" in os.environ`, and an `env_var="X"` keyword argument. The last
    is keyed on the KEYWORD, not on the callee's name, so `core/paths.py`'s `resolve` and
    `core/app.py`'s `_resolve_path` wrapper are both seen without either being named here.

    A name reached through an indirection (a variable, a dict of names) is invisible to this,
    by construction -- see `_EXPECTED_ENV_NEEDS_NO_SANDBOX` for the one such family that exists
    today and why it is exempt anyway.
    """
    names: set[str] = set()
    files = sorted((ROOT / "sluice").rglob("*.py"))
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg == "env_var" and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        names.add(kw.value.value)
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "get"
                        and _is_os_environ(func.value) and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    names.add(node.args[0].value)
            elif (isinstance(node, ast.Subscript) and _is_os_environ(node.value)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                names.add(node.slice.value)
            elif (isinstance(node, ast.Compare) and len(node.ops) == 1
                    and isinstance(node.ops[0], ast.In)
                    and isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str)
                    and node.comparators and _is_os_environ(node.comparators[0])):
                names.add(node.left.value)
    return names, len(files)


def test_the_test_block_sandboxes_every_env_var_sluice_reads():
    """What stops a local `brew test` reading the maintainer's real vault and credentials.

    Verified by execution, each via a `cp`-backed delete-then-restore (never `git checkout`,
    per CLAUDE.md's mutation-testing section): deleting the prefix sweep, deleting the explicit
    `%w[...]` list, and deleting the XDG_CACHE_HOME assignment each left the full suite green
    before this test existed.

    The variables are DERIVED from sluice/'s own source rather than restated, which is the one
    place in this file that reads the producer side of anything -- and it is a different
    producer: `sluice/`, not the renderer. Nothing here imports the renderer's constants; the
    rule that the rendered formula must be compared against test-side literals is untouched.
    """
    formula = render(**FIXTURE)

    # SCOPE, in the strong direction: every later exemption rests on one of these three
    # mechanisms being present in the rendered text. Assert them BEFORE using them, or a
    # deleted sweep silently turns "SLUICE_* is covered" into a claim about nothing.
    assert _EXPECTED_ENV_SWEEP_LINE in formula, (
        f"the `test do` block no longer sweeps {_EXPECTED_ENV_SWEEP_LINE!r}. Every SLUICE_*/"
        f"CAMOFOX_* variable -- including the SLUICE_TELEGRAM_TOKEN/CHAT credential pair -- "
        f"would then survive into `brew test`:\n{formula}"
    )
    listed_match = re.search(r"%w\[([^\]]*)\]\.each do \|k\|", formula)
    assert listed_match, (
        f"the `test do` block has no `%w[...].each do |k|` deletion list. The path variables "
        f"outside the SLUICE_/CAMOFOX_ prefix shape (the vault, the dedup store, the triage "
        f"audit, the dossier cache) would then point at the maintainer's real state:\n{formula}"
    )
    listed = set(listed_match.group(1).split())
    assert listed, "the `%w[...]` deletion list parsed as empty; every check on it is vacuous"
    for rung in _EXPECTED_ENV_XDG_RUNGS:
        assert f'ENV["{rung}"] = testpath/' in formula, (
            f"the `test do` block does not point {rung} at the sandbox. core/paths.py falls "
            f"through to that rung the instant the explicitly-named variable above it is "
            f"deleted, so leaving it unset relocates the fallback into the maintainer's real "
            f"XDG directory rather than into testpath:\n{formula}"
        )
    assert 'ENV["HOME"] = testpath' in formula, (
        f"the `test do` block does not redirect HOME. It is what the `~`-rooted XDG defaults "
        f"expand against whenever a rung above is unset or relative:\n{formula}"
    )

    read, walked = _env_names_read_by_sluice()
    # SCOPE FIRST, both halves: a walk that found no files, or a matcher that found no names,
    # would make the comparison below pass over an empty set -- the `all([])` shape, in the
    # guard whose success case is "nothing unsandboxed".
    assert walked, "walked no files under sluice/; this derivation is broken"
    assert read, (
        f"AST-walked {walked} files under sluice/ and derived NO environment variable names "
        f"at all. sluice reads several, so the matcher is broken and every assertion below is "
        f"vacuous."
    )

    unsandboxed = sorted(
        name for name in read
        if not name.startswith(_EXPECTED_ENV_SWEEP_PREFIXES)
        and name not in listed
        and name not in _EXPECTED_ENV_NEEDS_NO_SANDBOX
    )
    assert not unsandboxed, (
        f"sluice reads {unsandboxed} from the environment, and the formula's `test do` block "
        f"neither sweeps them by prefix nor deletes them by name. A local `brew test` would "
        f"run against whatever the maintainer has exported -- a real vault, a real dedup or "
        f"health store, a real dossier cache. Add each to the `%w[...]` list in the renderer, "
        f"or to `_EXPECTED_ENV_NEEDS_NO_SANDBOX` above with a reason it is safe."
    )

    # The other direction: a name deleted by the formula that nothing in sluice/ reads any more
    # is a stale entry, and a stale list is how the real one stops being trusted.
    stale = sorted(listed - read)
    assert not stale, (
        f"the formula's `%w[...]` list deletes {stale}, which nothing under sluice/ reads. "
        f"Either the read moved behind an indirection this derivation cannot see (say so in "
        f"the renderer's comment) or the entry is dead and should go."
    )
