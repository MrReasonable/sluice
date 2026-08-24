"""#7: every declared argparse `dest` must be read by the handler it dispatches to.

This is the sweep the issue asks for. It is PURE and STATIC -- it walks the argparse
tree and the AST of `sluice.cli`, touches no harness, no vault, no browser -- because
the property is "read on SOME path," and a runtime proxy would only observe the branch
a given invocation takes (`cmd_apply_prep` reads different dests down its all_shortlist
/ dry_run / else arms). Static analysis answers the right question.

The bug class: a flag argparse accepts, exits 0 on, and quietly ignores -- `--backend`
parsed but never forwarded; a param no CLI caller can set; a typo'd backend falling
through to a default. Each reads correctly at both ends; only the wiring between them is
absent, and nothing fails when it is. So a machine checks it.

A read is `args.X` or `getattr(args, "X", ...)` with a constant name, in the handler OR
in any module-level helper -- in the handler's OWN module -- that it passes `args` to
(the one real case at HEAD inside `sluice.cli` itself is `cmd_run -> _selected(args,
...)`; the transitive follow is witnessed in the test suite by mutating the read inside
`_selected`). A dynamic `getattr(args, <var>)` is deliberately NOT resolved -- it counts
as unread, so the sweep fails closed, which is the right direction for exactly the
murkiness #7 targets.

A handler need not live IN `sluice.cli` -- #164's evidence commands dispatch from
`sluice/evidence/commands.py`, imported lazily inside `_build_parser`, the same way
every other store-touching command's real work is imported lazily inside its own
function. `_module_funcs` below resolves each leaf's OWN module via `inspect.getmodule`
on the real function OBJECT `_build_parser` wired up, rather than a hand list of "the
modules a handler is allowed to live in" -- so a third command package later needs no
edit here, the same ENUMERATE-don't-hand-list discipline the rest of this sweep follows.
"""
import argparse
import ast
import inspect

from sluice.cli import _build_parser
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.evidence.commands import field_dest

# ── the opt-outs, each justified ─────────────────────────────────────────────
# `--all` is the explicit spelling of the default all-sources path: `_selected`
# reaches "every source" when args.source is falsy, and never reads args.all. It
# is genuinely unread by the handler -- but NOT a silent-degrade, because it is
# mutually exclusive with --source (an ambiguous `run --source X --all` now
# errors). Recorded here rather than passing silently; the second test below
# fails if this entry ever goes stale (flag removed, or becomes read).
OPT_OUT = {
    ("ingest run", "all"): "explicit form of the default; mutually exclusive with --source",
}
# Every per-kind `add` field flag (#164) is genuinely read at runtime, just not via a
# form this sweep can prove: `cmd_evidence_add` reads them through
# `getattr(args, field_dest(f))` inside a loop over `EvidenceKind.fields`, and `f` is a
# variable, not an `ast.Constant` -- exactly the dynamic-getattr shape the module
# docstring says is deliberately left unresolved. DERIVED from the same registry the
# flags themselves are derived from (never hand-listed field by field, kind by kind),
# so a new kind or a new field on an existing kind needs no new line here -- only a
# genuinely un-derivable exemption belongs in the literal dict above.
OPT_OUT.update({
    (f"{kind} add", field_dest(field)):
        "read via a dynamic getattr(args, field_dest(f)) loop over EvidenceKind.fields "
        "(#164) -- the sweep only resolves a literal-constant getattr, by design"
    for kind, spec in EVIDENCE_KINDS.items()
    for field in spec.fields
})

# name -> FunctionDef for one module's own top-level defs, parsed once and cached.
# Keyed by module (not by name alone) so two command modules may each define a
# same-named private helper without colliding -- unlike the single hand-list this
# replaced, which only ever had one module to parse and so never had to consider it.
_MODULE_FUNCS_CACHE: dict = {}


def _module_funcs(module):
    if module not in _MODULE_FUNCS_CACHE:
        _MODULE_FUNCS_CACHE[module] = {
            n.name: n
            for n in ast.parse(inspect.getsource(module)).body
            if isinstance(n, ast.FunctionDef)
        }
    return _MODULE_FUNCS_CACHE[module]


def _iter_leaves(parser, path=()):
    """Yield (path_tuple, leaf_parser) for every parser carrying a `func` default.

    A leaf is a dispatch target (`func` in its _defaults); a non-leaf holds a
    sub-`_SubParsersAction` and is recursed into. This picks out all handlers --
    the two-level `ingest run` and the one-level `health`/`doctor` alike.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                child_path = path + (name,)
                if "func" in child._defaults:
                    yield child_path, child
                else:
                    yield from _iter_leaves(child, child_path)


def _declared_dests(leaf):
    """The user-facing dests of a leaf: its own _actions, minus the auto-added
    -h/--help. `group`/`cmd`/`func` never appear here -- the subparser actions
    belong to the PARENT parser, and `func` is a _defaults entry, not an action --
    so enumerating from _actions (never from a parsed Namespace) excludes the
    structural dests for free."""
    return {
        a.dest
        for a in leaf._actions
        if not isinstance(a, argparse._HelpAction) and a.dest is not argparse.SUPPRESS
    }


def _reads_and_forwards(func_node, arg_name):
    """(dests read directly, [(callee_name, arg_position)]) for one function whose
    args-Namespace parameter is named `arg_name`.

    A direct read is `arg_name.X` or `getattr(arg_name, "X", ...)`. A forward is a
    call that passes `arg_name` itself positionally to another function -- recorded
    with the position so the callee's matching parameter can be followed."""
    reads, forwards = set(), []
    for node in ast.walk(func_node):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == arg_name):
            reads.add(node.attr)
        elif isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == "getattr"
                    and node.args and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == arg_name
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                reads.add(node.args[1].value)
            elif isinstance(f, ast.Name):
                for pos, a in enumerate(node.args):
                    if isinstance(a, ast.Name) and a.id == arg_name:
                        forwards.append((f.id, pos))
    return reads, forwards


def _all_reads(handler):
    """Every dest `handler` reads, transitively through module-level helpers -- in
    handler's OWN module -- that it forwards `args` to. Guards against cycles via a
    visited set.

    Takes the real function OBJECT, not its bare name: `inspect.getmodule` is what
    resolves the RIGHT source to parse (`sluice.cli` for most handlers, a command
    package like `sluice.evidence.commands` for #164's), so a name string alone would
    be ambiguous the moment two modules each define a function of the same name.
    """
    module = inspect.getmodule(handler)
    funcs = _module_funcs(module)
    entry = funcs[handler.__name__]
    # The handler receives (args, config) positionally; its first param is the Namespace.
    reads, seen = set(), set()
    stack = [(handler.__name__, entry.args.args[0].arg)]
    while stack:
        fname, aname = stack.pop()
        if (fname, aname) in seen:
            continue
        seen.add((fname, aname))
        node = funcs.get(fname)
        if node is None:  # a non-cli callee (Sluice, print, ...) -- nothing to follow
            continue
        direct, forwards = _reads_and_forwards(node, aname)
        reads |= direct
        for callee, pos in forwards:
            callee_node = funcs.get(callee)
            if callee_node is not None and pos < len(callee_node.args.args):
                stack.append((callee, callee_node.args.args[pos].arg))
    return reads


def test_every_declared_dest_is_read_by_its_handler():
    dead = []
    for path, leaf in _iter_leaves(_build_parser()):
        label = " ".join(path)
        handler = leaf._defaults["func"]
        opted = {dest for (lbl, dest) in OPT_OUT if lbl == label}
        unread = _declared_dests(leaf) - _all_reads(handler) - opted
        if unread:
            dead.append(
                f"  {label} ({handler.__name__}): declared but never read: {sorted(unread)}")
    assert not dead, (
        "dead flags -- declared in argparse but never read by the handler that "
        "dispatches to them (add a read, or an OPT_OUT entry with justification):\n"
        + "\n".join(dead))


def test_opt_out_entries_are_real_and_genuinely_unread():
    """No stale suppressions. Every OPT_OUT entry must name a real leaf+dest that is
    actually unread; a flag that was removed, or has become read, fails here. This
    closes the #26 escape (a sweep silently dropping what it replaced) at the sweep's
    own level -- the carve-out list cannot rot into silent over-suppression."""
    leaves = {" ".join(p): leaf for p, leaf in _iter_leaves(_build_parser())}
    stale = []
    for (label, dest), _why in OPT_OUT.items():
        leaf = leaves.get(label)
        if leaf is None:
            stale.append(f"  {label} {dest}: no such command")
        elif dest not in _declared_dests(leaf):
            stale.append(f"  {label} {dest}: not a declared dest")
        elif dest in _all_reads(leaf._defaults["func"]):
            stale.append(f"  {label} {dest}: IS read now -- drop it from OPT_OUT")
    assert not stale, "stale opt-out entries:\n" + "\n".join(stale)
