"""Nobody indexes a store-issued slug by hand.

`core/protocols.py` states slug uniqueness across a returned list as BOUNDED, not absolute:
a store must not itself create two notes at one slug, but the vault's slug IS the note
filename and a human with a filesystem can seat that name in two directories once the scan
is recursive (#1). The obligation that falls on callers is therefore never to index a
returned list by slug with a bare dict comprehension, which silently keeps whichever twin
came LAST -- and for `track` that twin is what a receipt is weighed against, so an `applied`
lands on a stale note while the real lead stays `shortlist` and no forward-only status move
can undo it.

That obligation was violated at all FOUR sites that existed when it was written. Each fix
carries a per-site regression test, and not one of them would say anything about a FIFTH
consumer -- `leads reconcile` (PR B) is already queued to be one. A contract enforced only
by prose plus per-site tests is enforced only until the next caller.

So this sweeps the shipped package instead. `core/leads.py:index_by_slug` is the one
sanctioned way in: it drops both twins and hands the caller the groups it dropped.

LIMITS, stated because a guard whose reach is assumed is a guard that fails open: neither
form of `d.setdefault(n.slug, ...)` is an assignment, so it is not matched here. Today's
risk is zero (no such expression exists in `sluice/`, verified by this sweep's own corpus
rather than by hand), but that is a fact about the code today, not a guarantee this test
enforces.

`dict((n.slug, n) for n in notes)` -- a Call over a generator rather than a DictComp -- was
in that same list of unreached shapes and is now MATCHED. That is a widening, not a
correction: no such expression existed, so nothing was slipping past, and the per-site
regression tests behind the four original consumers still catch each of them directly.
"""
import ast
import pathlib

_SLUICE = pathlib.Path(__file__).resolve().parents[1] / "sluice"

# A source file carrying every forbidden shape, plus a `.ref`-keyed twin of each. The
# positive control: an AST matcher that silently stopped matching -- a renamed node class, a
# Python grammar change -- would satisfy every "no violations" assertion below over an empty
# set, which is precisely the success case for a negative sweep and so is indistinguishable
# from working. The `.ref` twins are the negative half: a matcher that flagged every dict
# comprehension, or every `dict()` call, would also pass the sweep over today's corpus by
# accident and would say nothing about `.slug`.
_CONTROL = """
def f(notes, d):
    by_slug = {n.slug: n for n in notes}
    d[n.slug] = n
    gen = dict((n.slug, n) for n in notes)
    lst = dict([(n.slug, n) for n in notes])
    ok = {n.ref: n for n in notes}
    ok2 = dict((n.ref, n) for n in notes)
    return by_slug, gen, lst, ok, ok2
"""


def _keyed_on_slug(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "slug"


def _violations(tree):
    """Every place a `.slug` attribute is used as a dict KEY, in each shape that keeps the
    LAST twin: a dict comprehension, a subscript assignment in a loop, and a `dict()` call
    over a generator or list of (key, value) pairs."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.DictComp) and _keyed_on_slug(n.key):
            out.append((n.lineno, "dict comprehension keyed on .slug"))
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Subscript) and _keyed_on_slug(target.slice):
                    out.append((n.lineno, "subscript assignment keyed on .slug"))
        # `dict(<genexp or listcomp of (n.slug, n)>)`. Keyed on the FIRST element of the
        # produced tuple, which is the dict key; `dict((n.ref, n) for ...)` is untouched.
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "dict" \
                and len(n.args) == 1 and isinstance(n.args[0], (ast.GeneratorExp,
                                                                ast.ListComp)):
            elt = n.args[0].elt
            if isinstance(elt, ast.Tuple) and elt.elts and _keyed_on_slug(elt.elts[0]):
                out.append((n.lineno, "dict() call keyed on .slug"))
    return out


def _sources():
    return sorted(_SLUICE.rglob("*.py"))


def test_the_matcher_still_matches():
    """SCOPE, on the matcher itself. Every shape must be found in the control, and the
    `.ref`-keyed twins beside them must NOT be -- a matcher that flagged every dict
    comprehension, or every `dict()` call, would also pass the sweep below only by accident
    of the corpus. Counted, not just set-compared: the `dict()` shape has two spellings
    (genexp and listcomp) and both must be reached, so a matcher that handled only one would
    otherwise satisfy a set comparison."""
    found = _violations(ast.parse(_CONTROL))
    kinds = sorted(k for _, k in found)
    assert kinds == ["dict comprehension keyed on .slug",
                     "dict() call keyed on .slug",
                     "dict() call keyed on .slug",
                     "subscript assignment keyed on .slug"], found


def test_the_sweep_reaches_the_whole_package():
    """SCOPE, on the corpus. A sweep that walked no files, or whose glob stopped resolving,
    reports zero violations and reads exactly like a clean bill of health."""
    files = _sources()
    assert len(files) >= 50, f"the sweep found only {len(files)} source files"
    assert any(f.name == "engine.py" and f.parent.name == "track" for f in files), \
        "track/engine.py -- the caller whose defect is irreversible -- is not in the sweep"


def test_no_module_indexes_a_lead_list_by_slug_by_hand():
    offenders = []
    for f in _sources():
        for lineno, kind in _violations(ast.parse(f.read_text())):
            offenders.append(f"{f.relative_to(_SLUICE.parent)}:{lineno}: {kind}")
    assert not offenders, (
        "these keep whichever twin came LAST, silently, once two notes claim one slug "
        "(see core/protocols.py:LeadNote). Use core/leads.py:index_by_slug, which drops "
        "both and returns the groups it dropped:\n  " + "\n  ".join(offenders))
