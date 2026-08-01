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
four os.makedirs sites classified below (verified by hand, not by this guard), and nothing
in it calls os.mkdir or pathlib. But that is a fact about the code today, not a guarantee
this test enforces -- a future pathlib-based makedirs call ships unclassified and silent."""
import ast
import pathlib

_VAULT = pathlib.Path(__file__).resolve().parents[1] / "sluice" / "core" / "vault.py"

# Every os.makedirs argument expression in vault.py, and why each is not a scan-set concern.
_EXPECTED = {
    # The Syncthing marker, at the VAULT root -- not under leads_dir, never scanned.
    "os.path.join(self.dir, '.stfolder')": "syncthing marker, vault root",
    # write_document's parent dir, derived from a document key under the vault root.
    "os.path.dirname(path)": "document parent, vault root",
    # The lead write folder itself. Scanned, and it is the root of the scan set.
    "self.leads_dir": "the write folder",
    # leads_dir/_merged -- under leads_dir, and therefore MUST be in _PRIVATE_SUBDIRS.
    "merged_dir": "the merge archive, pruned from the scan set",
}


_DIRMAKERS = {"makedirs", "mkdir"}


def _local_dirmakers(tree):
    """Every local name in vault.py that reaches os.makedirs/os.mkdir, derived from
    that module's OWN import nodes rather than hand-listed.

    `import os as _o` and `from os import makedirs as _mk` are the same call under
    different spellings, and a sweep keyed on the literal "os.makedirs" sees neither --
    while the four existing calls keep the scope assertion satisfied, so both tests stay
    green and a new unguarded directory ships. That is the documented "hand-listed names
    lose to an import alias" failure, and deriving the bindings is its documented fix."""
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
    return {f"{m}.{f}" for m in modules for f in _DIRMAKERS} | direct


def _makedirs_args():
    tree = ast.parse(_VAULT.read_text())
    names = _local_dirmakers(tree)
    return [ast.unparse(n.args[0]) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and ast.unparse(n.func) in names]


def test_the_sweep_actually_finds_the_makedirs_calls():
    """The scope assertion. Without it a matcher that silently stopped matching -- an
    ast.unparse spelling change, a renamed import -- would leave every assertion below
    trivially true."""
    found = _makedirs_args()
    assert len(found) >= 4, f"AST sweep found only {found!r}; the matcher is broken"


def test_every_makedirs_call_is_classified():
    unexpected = set(_makedirs_args()) - set(_EXPECTED)
    assert not unexpected, (
        f"vault.py creates {unexpected}, which this guard does not classify. If it is under "
        f"leads_dir and holds notes sluice owns, add its name to _PRIVATE_SUBDIRS so the scan "
        f"skips it; otherwise add it to _EXPECTED with the reason it is not scanned.")
