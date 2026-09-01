"""#223 §2.2: every `Lead(...)` construction in `sluice/` folds `job_type` first.

An earlier draft of the design claimed that calling `normalise_role_type` from
`_row_to_lead` made `Lead.job_type` canonical for every store. That was false: #223 §1.4
names THREE origins, and `Sluice.create_lead` bypasses `_row_to_lead` entirely -- it
builds the `Lead` by hand and calls `store.upsert` directly, so seen.db stays untouched
(decision 11). Two normalising sites therefore exist, and the correctness of the closed
set depends on there never being a third that forgets.

That is a hand-list, and hand-lists go stale -- so this ENUMERATES the constructions
instead. A fourth origin added later fails here until it folds, rather than quietly
seating a third spelling of `contract` that `classify`'s branch selector then compares
against a closed set it does not belong to.

The bindings are DERIVED per file from its own `ImportFrom` nodes, never keyed on the
string "Lead": `from sluice.core.leads import Lead as _Lead` walks straight past a
name-matched sweep, and `StaleLead(` -- a real, unrelated construction in `core/app.py`
-- is exactly the false positive a substring match would produce.
"""
import ast
import pathlib

import pytest

import sluice

_PKG = pathlib.Path(sluice.__file__).resolve().parent
_TARGET = "Lead"
_NORMALISER = "normalise_role_type"


def _local_names(tree: ast.AST, wanted: str) -> set[str]:
    """Every local binding of `wanted` in this module, alias included."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == wanted:
                    out.add(a.asname or a.name)
    return out


def _calls(node: ast.AST, names: set[str]) -> list[ast.Call]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in names]


def _constructions():
    """(rel path, line, enclosing function, folds?) for every `Lead(...)` in sluice/."""
    out = []
    for py in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        lead_names = _local_names(tree, _TARGET)
        norm_names = _local_names(tree, _NORMALISER)
        if not lead_names:
            continue
        rel = py.relative_to(_PKG).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            folds = bool(norm_names and _calls(fn, norm_names))
            for call in _calls(fn, lead_names):
                out.append((rel, call.lineno, fn.name, folds))
        # A construction outside any function body has no normalising path at all.
        in_fn = {id(c) for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for c in _calls(fn, lead_names)}
        for call in _calls(tree, lead_names):
            if id(call) not in in_fn:
                out.append((rel, call.lineno, "<module scope>", False))
    return out


def test_the_sweep_finds_the_constructions_it_is_checking():
    """SCOPE, and it is load-bearing rather than decorative: the verdict below is `all
    the constructions fold`, and `all([])` is True. A matcher that stopped resolving
    `Lead` -- a rename, a moved import, `from __future__ import annotations` -- would
    enumerate nothing and certify everything.

    Pins the exact SET, not a floor. A count alone cannot see one site being swapped for
    another, and the two here are the two origins §1.4 names as reaching a store.
    """
    found = {(rel, fn) for rel, _line, fn, _folds in _constructions()}
    assert found == {("ingest/base.py", "_row_to_lead"),
                     ("core/app.py", "create_lead")}, (
        f"Lead() construction discovery found {sorted(found)}.\n"
        "If an origin was ADDED or REMOVED, update this set -- and make sure the new "
        "one folds job_type through core/roletype.py before it reaches a store.")


def test_the_sweep_does_not_match_an_unrelated_class_by_substring():
    """`core/app.py` constructs `StaleLead(...)`, which ends in `Lead` and has nothing to
    do with this. A substring matcher would report it as an unnormalising origin, and the
    fix a reader would then reach for is to widen the exemption rather than the sweep.

    Asserted against the MATCHER, on synthetic source. An earlier version filtered
    `_constructions()` for the enclosing function `stale_leads` -- but `StaleLead` is
    built inside `expire_report`, so that filter matched nothing whether the matcher was
    AST-bound or substring-based, and the row could not fail. The live protection is
    `test_the_sweep_finds_the_constructions_it_is_checking`, which would see
    `("core/app.py", "expire_report")` appear in its pinned set.
    """
    unrelated = ast.parse("from sluice.core.app import StaleLead\n"
                          "def f():\n    return StaleLead(a=1)\n")
    real = ast.parse("from sluice.core.leads import Lead\n"
                     "def g():\n    return Lead(source='x')\n")
    # The pair is what makes this falsifiable. A matcher keyed on the string "Lead"
    # collects BOTH -- "Lead" is a substring of "StaleLead" -- so a single row proves
    # nothing about which matcher is in use.
    assert _calls(unrelated, _local_names(unrelated, _TARGET)) == []
    assert len(_calls(real, _local_names(real, _TARGET))) == 1


@pytest.mark.parametrize(
    "rel,lineno,fn,folds", _constructions(),
    ids=[f"{rel}:{fn}" for rel, _l, fn, _f in _constructions()])
def test_every_lead_construction_folds_job_type_first(rel, lineno, fn, folds):
    assert folds, (
        f"{rel}:{lineno} constructs a Lead inside {fn}() without calling "
        f"{_NORMALISER}. #223 §1.4: a job_type has three origins and each must fold to "
        "the closed set BEFORE the value reaches a store -- `classify` compares "
        "role_type against that set, so a third spelling is judged by neither branch.")


def test_an_alias_import_is_still_seen():
    """The failure mode this sweep is written against, pinned on synthetic source rather
    than on the live tree (which has no alias today). `from ... import Lead as _Lead`
    walks straight past a matcher keyed on the string "Lead"."""
    tree = ast.parse("from sluice.core.leads import Lead as _Lead\n"
                     "def f():\n    return _Lead(source='x')\n")
    names = _local_names(tree, _TARGET)
    assert names == {"_Lead"}
    assert len(_calls(tree, names)) == 1
