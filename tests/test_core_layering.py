"""#212 round 3 (arc-r3-001/arc-r3-002): `core/config.py` and `ingest/base.py` each carry
a comment asserting "no `core/` module imports a sub-app at module scope" as the whole
justification for where `validate_search_entry` lives -- the grammar behind
`sources.<id>.searches`/`searches_spec` sits in `core/config.py` rather than beside
`validate_posting_paths`/`validate_reprobed` in `ingest/base.py` precisely because that
direction is supposed to be closed. This file is the guard that makes the claim checkable
rather than merely asserted -- a comment stating a mechanism needs a row that falsifies it.

#212 round 4 (arc-r4-002/tst-r4-002/rev-r4-001/tst-r4-003) REPLACED the original AST
matcher wholesale rather than patching it again. The matcher recognised 2 of 6 import
spellings -- caught `import sluice.triage as t` and `from sluice.triage import classify`,
missed `from sluice import triage`, `from sluice import ingest as _ing`, both relative
forms (`from ..ingest import base`), and `importlib.import_module(...)` -- and could not
see a TRANSITIVE eager drag (`core/X` -> a non-core module -> a sub-app) at all, because it
only ever inspected one file's own AST.

Two properties, checked two different ways because they need different instruments:

  P1. No `core/` module EAGERLY imports a sub-app (directly or transitively). Checked by a
      RUNTIME WITNESS: a fresh subprocess imports every `core/*.py` module and reports
      which `sluice.<subapp>` modules that put into `sys.modules`. This is strictly
      stronger than pattern-matching source text -- it is blind to spelling BY
      CONSTRUCTION (whatever Python's own import machinery actually did is what gets
      checked, not a guess about which syntax forms exist), and it catches a transitive
      drag as easily as a direct one. Runs in a subprocess because the test session
      itself has already imported every sub-app for its own tests; checking
      `sys.modules` in-process would report on this whole session, not on `core/`.
  P2. Only `core/app.py` -- the composition root -- may name a sub-app AT ALL, lazily or
      otherwise. This is the actual substantive rule the placement comments in
      `core/config.py` and `ingest/base.py` depend on (P1 alone would still let a second
      core module import a sub-app lazily, which is not what those comments claim), and a
      small static AST sweep is the right instrument for it: "does this file's source
      TEXT reference a sub-app" does not care whether the reference is eager, so there is
      no nesting classification to get wrong, no mirror tests for it, and no coupling to
      `app.py`'s current import count.
"""
import ast
import pathlib
import subprocess
import sys

import sluice

_CORE_DIR = pathlib.Path(sluice.__file__).parent / "core"

# The five PIPELINE sub-apps, per CLAUDE.md's own canonical taxonomy ("Pipeline: ingest
# -> triage -> cv -> apply -> track... plus two COMMAND packages, neither a sixth
# sub-app" -- onboard, evidence). This is the repo's documented pipeline definition, not
# an enumeration invented for this test: the seam-implementation packages
# (backends/, fetchers/, renderers/, stores/) are adapter implementations `core/app.py`
# resolves BY NAME through its own seam registries, not sub-apps, and `core/` may
# legitimately import them -- including at module scope.
_SUB_APPS = ("ingest", "triage", "cv", "apply", "track")


def _core_modules() -> list:
    """Every `.py` file under `core/`, WALKED rather than hand-listed -- a module added
    later is covered the day it lands."""
    return sorted(_CORE_DIR.glob("*.py"))


def test_every_named_sub_app_is_a_real_package():
    """Anti-vacuity for `_SUB_APPS` itself (tst-r4-003a): a hand-list has no protection
    against silently dropping a member -- say `"ingest"`, the one sub-app this whole file
    exists for -- which would leave every sweep below checking a narrower rule than its
    docstring claims while staying green."""
    sluice_dir = pathlib.Path(sluice.__file__).parent
    for sa in _SUB_APPS:
        assert (sluice_dir / sa).is_dir(), (
            f"_SUB_APPS names {sa!r}, which is not a real sub-app directory under "
            f"{sluice_dir} -- _SUB_APPS itself has gone stale")


def test_core_module_discovery_finds_the_real_fleet():
    """Anti-vacuity for `_core_modules()` (tst-r4-003b): a `>=` floor a few short of the
    real count lets a module silently drop out of discovery -- measured before, `>= 20`
    against a true 22 would still pass with `core/config.py` missing from the walk. An
    exact count, with the same "if a module was added, update this number" instruction
    `test_config_shapes.py`'s own per-block counts use, cannot hide that."""
    modules = _core_modules()
    names = {p.name for p in modules}
    assert len(modules) == 23, (
        f"core/ module discovery found {len(modules)} files, expected 23: {sorted(names)}.\n"
        "If a module was ADDED or REMOVED, update this count. If it changed and this "
        "count did not, discovery is silently checking a different fleet than it claims.")


# ── P1: a runtime witness -- what Python's own import machinery actually did ────────────

_WITNESS_SCRIPT = """
import importlib
import pathlib
import sys

import sluice

core_dir = pathlib.Path(sluice.__file__).parent / "core"
for path in sorted(core_dir.glob("*.py")):
    importlib.import_module(f"sluice.core.{path.stem}")

sub_apps = ("ingest", "triage", "cv", "apply", "track")
leaked = sorted(
    name for name in sys.modules
    if any(name == f"sluice.{sa}" or name.startswith(f"sluice.{sa}.") for sa in sub_apps)
)
print(",".join(leaked))
"""


def test_importing_every_core_module_never_drags_in_a_sub_app():
    """The runtime witness for P1. A fresh subprocess imports every `core/*.py` module
    and reports which `sluice.<subapp>` modules that put into `sys.modules` as a side
    effect. Catches every import spelling the old AST matcher missed -- `from sluice
    import triage`, a relative `from ..ingest import base`, `importlib.import_module(...)`
    -- and a transitive drag through a non-core module, all identically, because none of
    them is pattern-matched: whatever actually got imported is what gets checked."""
    result = subprocess.run(
        [sys.executable, "-c", _WITNESS_SCRIPT],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        f"the witness subprocess itself failed (stderr below), not the property "
        f"under test:\n{result.stderr}")
    leaked = [n for n in result.stdout.strip().split(",") if n]
    assert leaked == [], (
        f"importing every core/ module eagerly loaded sub-app module(s): {leaked} -- "
        f"core/ may reach a sub-app only LAZILY, inside a function or method body")


_WITNESS_LIVENESS_SCRIPT = """
import sys

import sluice.triage  # planted: simulate a core module eagerly dragging in a sub-app

sub_apps = ("ingest", "triage", "cv", "apply", "track")
leaked = sorted(
    name for name in sys.modules
    if any(name == f"sluice.{sa}" or name.startswith(f"sluice.{sa}.") for sa in sub_apps)
)
print(",".join(leaked))
"""


def test_the_witness_detection_logic_reddens_on_a_planted_import():
    """Anti-vacuity for the runtime witness above: proves the `sys.modules` scan actually
    FIRES when a sub-app genuinely gets imported, rather than the empty result on the real
    tree meaning the scan itself is inert. Never edits `core/` -- the plant lives entirely
    inside this synthetic subprocess script."""
    result = subprocess.run(
        [sys.executable, "-c", _WITNESS_LIVENESS_SCRIPT],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    leaked = [n for n in result.stdout.strip().split(",") if n]
    assert "sluice.triage" in leaked, (
        "the witness's detection logic did not flag a deliberately planted sub-app "
        "import -- it would report a clean core/ vacuously")


# ── P2: only app.py may name a sub-app at all, lazily or otherwise ──────────────────────

def _names_a_sub_app(dotted: str) -> bool:
    return any(dotted == f"sluice.{sa}" or dotted.startswith(f"sluice.{sa}.")
               for sa in _SUB_APPS)


def _resolve_import_from(node: ast.ImportFrom, pkg=("sluice", "core")) -> list:
    """Every dotted module name `node` could refer to, resolving a RELATIVE import
    (`node.level > 0`) against `pkg` -- the package every `core/` module lives in (core/
    is flat: nothing here lives deeper than `sluice.core`). `level=1` ("from . import x")
    means "relative to this package"; `level=2` ("from .. import x") means "relative to
    its parent" -- CPython's own rule, mirrored here rather than reimplemented from
    scratch."""
    if node.level == 0:
        base = node.module
    else:
        parts = list(pkg)[: len(pkg) - (node.level - 1)] if node.level - 1 <= len(pkg) else []
        base = ".".join(parts + ([node.module] if node.module else [])) or None
    if base is None:
        return []
    if base == "sluice":
        # `from sluice import triage` (or its relative equivalent `from .. import
        # triage`) names the sub-app through the ALIAS, not through `module` -- each
        # imported NAME is itself a candidate dotted path.
        return [f"{base}.{alias.name}" for alias in node.names]
    return [base]


def _sub_app_reference_nodes(tree):
    """Every `Import`/`ImportFrom` node ANYWHERE in `tree` (module scope or nested --
    P2 does not distinguish the two, unlike the retired matcher's P1 half) that names a
    sub-app or a submodule of one."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_names_a_sub_app(alias.name) for alias in node.names):
                yield node
        elif isinstance(node, ast.ImportFrom):
            if any(_names_a_sub_app(d) for d in _resolve_import_from(node)):
                yield node


def test_only_app_py_names_a_sub_app_in_core():
    modules = _core_modules()
    hits = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _sub_app_reference_nodes(tree):
            hits.append((path.name, node.lineno))

    # Anti-vacuity (LIVENESS): `core/app.py` is the composition root with known sub-app
    # references. If the matcher finds none there, it is broken -- not evidence the
    # codebase is clean.
    assert hits, "matcher found zero sub-app references anywhere in core/ -- broken, not clean"
    assert all(name == "app.py" for name, _ in hits), (
        f"a core/ module other than app.py names a sub-app: {hits} -- only app.py, the "
        f"composition root, may reference a sub-app at all")


def test_the_static_matcher_reddens_on_every_spelling_the_runtime_witness_would_also_catch():
    """The static half's own liveness check, one row per spelling the retired matcher
    missed (rev-r4-001/tst-r4-002) -- proving P2 does not repeat that gap."""
    sources = [
        "import sluice.triage as t\n",
        "from sluice.triage import classify\n",
        "from sluice import triage\n",
        "from sluice import ingest as _ing\n",
        "from ..ingest import base\n",
        "from .. import triage\n",
    ]
    for src in sources:
        tree = ast.parse(src, filename="<planted>")
        hits = list(_sub_app_reference_nodes(tree))
        assert hits, f"the matcher did not detect a planted sub-app reference: {src!r}"
