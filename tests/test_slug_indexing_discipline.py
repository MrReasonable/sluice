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

LIMITS, stated because a guard whose reach is assumed is a guard that fails open:
`dict((n.slug, n) for n in notes)` is a Call over a generator, not a DictComp, and neither
form of `d.setdefault(n.slug, ...)` is an assignment -- none of the three is matched here.
Today's risk is zero (no such expression exists in `sluice/`, verified by this sweep's own
corpus rather than by hand), but that is a fact about the code today, not a guarantee this
test enforces.
"""
import ast
import pathlib

_SLUICE = pathlib.Path(__file__).resolve().parents[1] / "sluice"

# A source file carrying BOTH forbidden shapes. The positive control: an AST matcher that
# silently stopped matching -- a renamed node class, a Python grammar change -- would
# satisfy every "no violations" assertion below over an empty set, which is precisely the
# success case for a negative sweep and so is indistinguishable from working.
_CONTROL = """
def f(notes, d):
    by_slug = {n.slug: n for n in notes}
    d[n.slug] = n
    ok = {n.ref: n for n in notes}
    return by_slug, ok
"""


def _violations(tree):
    """Every place a `.slug` attribute is used as a dict KEY, in either shape that keeps
    the LAST twin: a dict comprehension, and a subscript assignment in a loop."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.DictComp) and isinstance(n.key, ast.Attribute) \
                and n.key.attr == "slug":
            out.append((n.lineno, "dict comprehension keyed on .slug"))
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Subscript) \
                        and isinstance(target.slice, ast.Attribute) \
                        and target.slice.attr == "slug":
                    out.append((n.lineno, "subscript assignment keyed on .slug"))
    return out


def _sources():
    return sorted(_SLUICE.rglob("*.py"))


def test_the_matcher_still_matches():
    """SCOPE, on the matcher itself. Both shapes must be found in the control, and the
    `.ref`-keyed comprehension beside them must NOT be -- a matcher that flagged every dict
    comprehension would also pass the sweep below only by accident of the corpus."""
    found = _violations(ast.parse(_CONTROL))
    kinds = sorted(k for _, k in found)
    assert kinds == ["dict comprehension keyed on .slug",
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
