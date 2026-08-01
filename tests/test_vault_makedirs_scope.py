"""Scope guard: every directory vault.py CREATES is accounted for.

_PRIVATE_SUBDIRS names the directories pruned from the scan set. If a later change adds a
directory under leads_dir without adding it there, the walk returns its notes as active
leads -- which for an archive is #81's resurrection. This test cannot know a new call's
intent, so it fails on ANY unrecognised makedirs and makes the author classify it.

It asserts on the SCOPE, not on violations: an AST sweep that matched nothing would satisfy
every assertion over an empty set, and for a guard whose success case is 'found nothing
wrong' that is indistinguishable from working."""
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


def _makedirs_args():
    tree = ast.parse(_VAULT.read_text())
    return [ast.unparse(n.args[0]) for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ast.unparse(n.func) in ("os.makedirs", "os.mkdir")]


def test_the_sweep_actually_finds_the_makedirs_calls():
    """The scope assertion. Without it a matcher that silently stopped matching -- an
    ast.unparse spelling change, a renamed import -- would leave every assertion below
    trivially true."""
    found = _makedirs_args()
    assert len(found) >= 4, f"AST sweep found only {found!r}; the matcher is broken"


def test_every_directory_vault_creates_is_classified():
    unexpected = set(_makedirs_args()) - set(_EXPECTED)
    assert not unexpected, (
        f"vault.py creates {unexpected}, which this guard does not classify. If it is under "
        f"leads_dir and holds notes sluice owns, add its name to _PRIVATE_SUBDIRS so the scan "
        f"skips it; otherwise add it to _EXPECTED with the reason it is not scanned.")
