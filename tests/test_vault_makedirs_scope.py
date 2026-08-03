"""Scope guard: every os.makedirs/os.mkdir call in vault.py is accounted for.

_PRIVATE_SUBDIRS names the directories pruned from the scan set. If a later change adds a
directory under leads_dir without adding it there, the walk returns its notes as active
leads -- which for an archive is #81's resurrection. This test cannot know a new call's
intent, so it fails on ANY unrecognised os.makedirs/os.mkdir call and makes the author
classify it.

It asserts on the SCOPE, not on violations: an AST sweep that matched nothing would satisfy
every assertion over an empty set, and for a guard whose success case is 'found nothing
wrong' that is indistinguishable from working.

LIMIT: the sweep is keyed on names bound to os.makedirs/os.mkdir (see _local_dirmakers), so
a directory made via pathlib.Path(...).mkdir() -- a method call, not one of those names --
would evade it entirely. Today's risk is zero: vault.py creates directories only through the
os.makedirs sites classified in _EXPECTED below (verified by hand, not by this guard), and
nothing in it calls os.mkdir or pathlib. But that is a fact about the code today, not a
guarantee this test enforces -- a future pathlib-based makedirs call ships unclassified and
silent. (Stated as "the set classified below" rather than a COUNT on purpose: the count was
four, then five, then six as #1 landed, and a number in prose beside a list that grows is a
claim that goes stale silently.)

A SECOND limit used to sit beside it and is now closed: a name bound at RUNTIME rather than
at import (`_d = os.makedirs`, then `_d(x)`) walked straight past a matcher that derived its
names from import nodes alone. _local_dirmakers now follows assignments to a fixpoint, so
chains (`_a = os.makedirs; _b = _a`) are covered too. What is still NOT followed is a
callable smuggled through anything other than a plain name binding -- stored in a dict or a
list, passed as an argument, or reached as an attribute of an object -- and `_CONTROL_NEG`
pins the other half, that binding something else entirely is not mistaken for a dir-maker."""
import ast
import pathlib

_VAULT = pathlib.Path(__file__).resolve().parents[1] / "sluice" / "core" / "vault.py"

# Every os.makedirs argument expression in vault.py, and why each is not a scan-set concern.
_EXPECTED = {
    # The Syncthing marker, at the VAULT root -- not under leads_dir, never scanned.
    "ensure_stfolder:os.path.join(self.dir, '.stfolder')": "syncthing marker, vault root",
    # write_document's parent dir, derived from a document key under the vault root.
    "write_document:os.path.dirname(path)": "document parent, vault root",
    # The leads dir itself. Scanned, and it is the root of the scan set. Made on every
    # non-refused upsert outcome (it sits above the update/merge/create fan-out), which is
    # exactly why the layout's write folder below is a SEPARATE call rather than a repointing
    # of this one -- repointing would mint an empty Active/ on a pure last_seen bump.
    "upsert:self.leads_dir": "the leads dir, root of the scan set",
    # The lead WRITE FOLDER (#1) -- leads_dir under the flat default, leads_dir/Active under
    # `active_archive`. Made only on the CREATE arm, and OUTSIDE its try (makedirs raises
    # FileExistsError for a non-directory, which that try reads as the #16 create race). Scanned,
    # being inside the scan set, so it must NOT be in _PRIVATE_SUBDIRS: pruning it would hide
    # every lead sluice itself creates from read_leads AND from _locate, re-creating all of them
    # on the next scrape.
    "upsert:write_dir": "the write folder (create arm only)",
    # reconcile's destination (#1) -- leads_dir/<Active|Archive>, derived from layout_subfolder.
    # SCANNED, so it must NOT be in _PRIVATE_SUBDIRS: pruning it would hide every reconciled note
    # from read_leads AND from _locate, re-creating all of them on the next scrape.
    #
    # NB this key is a BARE LOCAL NAME, so a second `os.makedirs(dest_dir)` anywhere in vault.py --
    # however that local is derived -- would be absorbed by this classification silently. Three of
    # the six keys now have that shape (`write_dir`, `dest_dir`, `merged_dir`), which is why it is
    # RECORDED rather than fixed: the guard's job is to make an author classify each new call, and
    # a bare name cannot tell two call sites apart. Stated so the limit is known, not assumed.
    "reconcile_layout:dest_dir": "a reconcile destination folder, scanned",
    # leads_dir/_merged -- under leads_dir, and therefore MUST be in _PRIVATE_SUBDIRS.
    "merge_cluster:merged_dir": "the merge archive, pruned from the scan set",
}


_DIRMAKERS = {"makedirs", "mkdir"}


# Every spelling that reaches os.makedirs/os.mkdir under a name the literal "os.makedirs"
# does not match. The POSITIVE control: a matcher that silently stopped matching would leave
# the classification assertion satisfied over an empty set, which for a guard whose success
# case is "found nothing unclassified" is indistinguishable from working.
_CONTROL_POS = """
import os
import os as _o
from os import makedirs as _mk
from os import mkdir as _mkd

_module_alias = os.makedirs
_chained = _module_alias

def f(p):
    _fn_alias = _o.mkdir
    _annotated: object = _mk
    _o.makedirs("module_alias_import")
    _mk("direct_import")
    _mkd("direct_mkdir_import")
    _module_alias("module_level_binding")
    _chained("chained_binding")
    _fn_alias("function_level_binding")
    _annotated("annotated_binding")
"""

# The NEGATIVE half. A matcher that treated EVERY assignment's target as a dir-maker, or that
# flagged every call through a local name, would also pass the positive control -- and would
# then demand that vault.py classify its `os.path.join` calls.
_CONTROL_NEG = """
import os

_joiner = os.path.join
_not_a_call = os.makedirs

def g(p):
    _joiner("joined")
    os.path.dirname("dirname")
    open("opened")
"""


def _local_dirmakers(tree):
    """Every local name in `tree` that reaches os.makedirs/os.mkdir, derived from that
    module's OWN import and assignment nodes rather than hand-listed.

    `import os as _o` and `from os import makedirs as _mk` are the same call under
    different spellings, and a sweep keyed on the literal "os.makedirs" sees neither --
    while the four existing calls keep the scope assertion satisfied, so both tests stay
    green and a new unguarded directory ships. That is the documented "hand-listed names
    lose to an import alias" failure, and deriving the bindings is its documented fix.

    A RUNTIME binding is the same failure one rung further along: `_d = os.makedirs` needs
    no import alias at all, and `_d(x)` then evades a matcher built from import nodes only.
    So assignments are followed too, to a FIXPOINT -- `_a = os.makedirs; _b = _a` rebinds
    through an intermediate, and a single pass would see `_b = _a` before `_a` was known.
    `ast.walk` reaches module-level and function-level bindings alike, so both are covered
    by the same loop; AnnAssign is included because `_d: Callable = os.makedirs` is the same
    binding wearing a type."""
    modules, direct = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os":
                    modules.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.module == "os":
            for a in n.names:
                if a.name in _DIRMAKERS:
                    direct.add(a.asname or a.name)
    names = {f"{m}.{f}" for m in modules for f in _DIRMAKERS} | direct
    # Propagate through plain name bindings until nothing new appears. Bounded: each pass
    # either grows `names` (finite -- one entry per assignment target in the file) or stops.
    while True:
        grown = False
        for n in ast.walk(tree):
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            # A bare `x: T` annotation has no value; and only a name/attribute VALUE can be
            # an alias -- `_d = os.makedirs(p)` is a Call, which binds the RESULT, not the
            # callable, and must not make `_d` a dir-maker.
            if n.value is None or not isinstance(n.value, (ast.Name, ast.Attribute)):
                continue
            if ast.unparse(n.value) not in names:
                continue
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in names:
                    names.add(t.id)
                    grown = True
        if not grown:
            return names


def _makedirs_args(tree, qualify=True):
    """Every dir-making call, keyed on (enclosing function, argument expression).

    The FUNCTION half is load-bearing. Three of the six keys are bare LOCAL NAMES
    (`write_dir`, `dest_dir`, `merged_dir`), and on a bare name alone a NEW
    `os.makedirs(dest_dir, ...)` added to a DIFFERENT method inherits an existing
    classification silently -- which is exactly the escape this guard exists to close. An
    earlier revision weakened `self._write_folder()` to `write_dir` when the call was hoisted,
    and that is what surfaced it.

    `qualify=False` is for the synthetic controls below, whose point is the matcher's NAME
    resolution rather than call-site identity."""
    names = _local_dirmakers(tree)
    out = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if (isinstance(child, ast.Call) and ast.unparse(child.func) in names
                    and child.args):
                arg = ast.unparse(child.args[0])
                out.append(f"{fn}:{arg}" if qualify and fn else arg)
            visit(child, fn)

    visit(tree, None)
    return out


def _vault_makedirs_args():
    return _makedirs_args(ast.parse(_VAULT.read_text()))


def test_the_matcher_reaches_every_aliased_spelling():
    """SCOPE, on the matcher itself. Every binding in the control must be found -- counted,
    not set-compared, because the argument strings are distinct and a matcher that reached
    only some spellings would otherwise satisfy a membership check."""
    found = sorted(_makedirs_args(ast.parse(_CONTROL_POS), qualify=False))
    assert found == ["'annotated_binding'", "'chained_binding'", "'direct_import'",
                     "'direct_mkdir_import'", "'function_level_binding'",
                     "'module_alias_import'", "'module_level_binding'"], found


def test_the_matcher_does_not_flag_an_unrelated_binding():
    """The negative half: binding something that is NOT a dir-maker, and calling it, must
    stay invisible -- otherwise this guard would demand vault.py classify os.path.join."""
    assert _makedirs_args(ast.parse(_CONTROL_NEG), qualify=False) == []


def test_the_sweep_actually_finds_the_makedirs_calls():
    """The scope assertion. Without it a matcher that silently stopped matching -- an ast.unparse
    spelling change, a renamed import -- would leave every assertion below trivially true.

    Compared BOTH WAYS against the classification table rather than against a number. A `>= 4`
    floor is the same stale-count problem the module docstring above avoids, one layer down: a
    matcher that regressed to finding four of six would still pass it. And the reverse direction
    is what catches a classification for a call site that no longer EXISTS -- measured, deleting
    the write-folder makedirs outright left this file green while six other tests went red."""
    found = set(_vault_makedirs_args())
    assert found, "AST sweep found nothing; the matcher is broken"
    stale = set(_EXPECTED) - found
    assert not stale, (
        f"_EXPECTED classifies {stale}, which vault.py no longer creates. Remove the entry, or "
        f"restore the call if its deletion was accidental.")


def test_every_makedirs_call_is_classified():
    unexpected = set(_vault_makedirs_args()) - set(_EXPECTED)
    assert not unexpected, (
        f"vault.py creates {unexpected}, which this guard does not classify. If it is under "
        f"leads_dir and holds notes sluice owns, add its name to _PRIVATE_SUBDIRS so the scan "
        f"skips it; otherwise add it to _EXPECTED with the reason it is not scanned.")
