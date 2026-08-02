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

`d.setdefault(n.slug, ...)` is now matched too, and the way its exemption used to read is
worth keeping as a warning. It was disclosed as an unreached shape whose "risk is zero (no
such expression exists in `sluice/`, verified by this sweep's own corpus rather than by
hand)" -- and that verification never happened, because the sweep did not look at
`setdefault` at all. Two such expressions existed the whole time
(`core/leads.py:index_by_slug` and `core/vault.py:read_leads`). A claim of having checked,
made by the thing that cannot check, is worse than an open limit honestly stated.

Both of those are the GROUPING form -- `setdefault(key, <empty container>)` followed by a
mutation -- which keeps EVERY twin and is the shape `index_by_slug` itself is built from. So
the exemption is keyed on the DEFAULT: an empty container is a grouping and is allowed;
anything else is `setdefault(n.slug, n)`, which keeps the FIRST twin. That is the same defect
as the dict comprehension's last-twin-wins, differing only in which twin survives -- and
"which one" was never the point, since neither is the note a human chose.

LIMITS, stated because a guard whose reach is assumed is a guard that fails open: only a
plain-name binding is followed, so a slug used as a key through a container, a helper
function, or an object attribute is not matched. That is a fact about this matcher, not a
claim about the corpus, and nothing here has verified the corpus is free of them.

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
    d.setdefault(n.slug, n)
    d.setdefault(n.slug)
    ok = {n.ref: n for n in notes}
    ok2 = dict((n.ref, n) for n in notes)
    ok3 = d.setdefault(n.ref, n)
    d.setdefault(n.slug, []).append(n)
    d.setdefault(n.slug, {})[n.ref] = n
    d.setdefault(n.slug, set()).add(n)
    return by_slug, gen, lst, ok, ok2, ok3
"""

# The container constructors a GROUPING seeds with. `setdefault(key, <one of these>)` keeps
# every twin (the caller then appends to it), which is what `index_by_slug` itself does and
# the opposite of the defect this file is about.
_EMPTY_CTORS = {"list", "dict", "set", "tuple"}


def _is_empty_container(node) -> bool:
    """An empty `[]`/`{}`/`()`, or a no-arg `list()`/`dict()`/`set()`/`tuple()`.

    Emptiness is required, not just the type: `setdefault(n.slug, [n])` is not a grouping
    seed -- nothing appends to it, and it keeps the FIRST twin exactly as `setdefault(k, n)`
    does. `ast.Set` has no empty literal (`{}` parses as a Dict), so it is reachable only
    through `set()`."""
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _EMPTY_CTORS and not node.args and not node.keywords)


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
        # `d.setdefault(n.slug, <not an empty container>)`, which keeps the FIRST twin.
        # The grouping form -- an empty container the caller then mutates -- is exempt: it
        # keeps every twin, and it is what index_by_slug is built from.
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "setdefault" and n.args and _keyed_on_slug(n.args[0]):
            if len(n.args) < 2 or not _is_empty_container(n.args[1]):
                out.append((n.lineno, "setdefault keyed on .slug"))
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
                     "setdefault keyed on .slug",
                     "setdefault keyed on .slug",
                     "subscript assignment keyed on .slug"], found


def test_the_grouping_setdefault_is_not_flagged():
    """The exemption, pinned on its own so it cannot silently widen. `setdefault(k, [])`
    and friends keep EVERY twin -- they are the shape `index_by_slug` is built from, and
    two live sites use it (`core/leads.py`, `core/vault.py`). A matcher that flagged every
    `setdefault` would redden the sweep over `sluice/` and the obvious fix would be to
    delete the check, so this pins the boundary instead.

    The three seeded forms above (`[]`, `{}`, `set()`) must all be exempt, and `[n]` must
    NOT be: a non-empty default is not a grouping seed, and it keeps the first twin exactly
    as `setdefault(k, n)` does."""
    exempt = "def f(notes, d):\n    d.setdefault(n.slug, []).append(n)\n"
    assert _violations(ast.parse(exempt)) == []
    for seed in ("[]", "{}", "set()", "dict()", "list()", "tuple()", "()"):
        src = f"def f(notes, d):\n    d.setdefault(n.slug, {seed})\n"
        assert _violations(ast.parse(src)) == [], seed
    for seed in ("[n]", "{n.ref: n}", "n", "None", "{n}"):
        src = f"def f(notes, d):\n    d.setdefault(n.slug, {seed})\n"
        assert [k for _, k in _violations(ast.parse(src))] == \
            ["setdefault keyed on .slug"], seed


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
        "these silently keep ONE of the twins once two notes claim one slug -- the LAST for "
        "a dict comprehension or a subscript assignment, the FIRST for a setdefault. Which "
        "one is not the point: neither is the note a human chose (see "
        "core/protocols.py:LeadNote). Use core/leads.py:index_by_slug, which drops both and "
        "returns the groups it dropped:\n  " + "\n  ".join(offenders))
