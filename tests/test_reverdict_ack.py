"""#223 §2.1: the one-shot marker behind the re-verdict notice.

Every property here is about failing in the LOUD direction. A marker that reads as
present when it is not silently re-verdicts a vault, and `dismiss` is not in
`DEFAULT_TRIAGE_STATUSES`, so no default run re-selects those leads. A marker that reads as
absent when it is present costs one skipped run.
"""
import os

import pytest

from sluice.core import paths
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.vault import Vault
from sluice.triage import reverdict

_VAULT = "/vaults/alpha"
_OTHER = "/vaults/beta"


def test_a_fresh_install_has_not_been_told(tmp_path):
    assert reverdict.acknowledged(_VAULT, str(tmp_path / "nope.json")) is False


def test_acknowledging_is_what_makes_it_true(tmp_path):
    path = str(tmp_path / "ack.json")
    assert reverdict.acknowledge(_VAULT, path) is True
    assert reverdict.acknowledged(_VAULT, path) is True


def test_acknowledging_one_vault_does_not_silence_another(tmp_path):
    # The notice is a claim about ONE vault's accumulated notes. A single global flag
    # meant acknowledging on vault A silenced it for vault B, which then re-verdicted in
    # silence -- the same harm, through a different door.
    path = str(tmp_path / "ack.json")
    reverdict.acknowledge(_VAULT, path)
    assert reverdict.acknowledged(_OTHER, path) is False


def test_acknowledging_a_second_vault_does_not_forget_the_first(tmp_path):
    # Read-modify-write. A blind overwrite would send vault A back into the notice loop
    # every time vault B was triaged, and vice versa, forever.
    path = str(tmp_path / "ack.json")
    reverdict.acknowledge(_VAULT, path)
    reverdict.acknowledge(_OTHER, path)
    assert reverdict.acknowledged(_VAULT, path) is True
    assert reverdict.acknowledged(_OTHER, path) is True


# ── what production actually hands the key (#324) ────────────────────────────
# `acknowledged`/`acknowledge` receive `Sluice._reverdict_scope`'s output, never a bare
# path. The rows above pass opaque strings, which is right for the marker mechanics they
# test. The rows below DERIVE their input from that producer instead, because a
# hand-written plausible value is how #324 shipped green: the cwd-stability row these
# replace passed a bare path, where `abspath` was correct, while production passed
# `vault:<dir>`, where `abspath` prepended the process cwd.
#
# Each row computes the scope AGAIN after changing directory, because that is what a new
# process does: `_reverdict_scope` runs in whatever cwd and environment the run starts in.

class _NoDir:
    """A store that declares no `dir` -- `_reverdict_scope`'s fallback branch."""


def _scope(store):
    return Sluice(Config())._reverdict_scope(store)


def _two_directories(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    return tmp_path / "a", tmp_path / "b"


@pytest.mark.parametrize("spelling", ["v", "./v", "a/../v"])
def test_a_relative_and_an_absolute_spelling_of_one_vault_share_one_acknowledgement(
        tmp_path, monkeypatch, spelling):
    # `./vault` -- the shipped default -- from inside its parent and the same directory
    # named in full are one vault, so they must share one acknowledgement, or the notice
    # re-shows on a `cd`. `./` and `..` are here because a scope that merely JOINED the cwd
    # on would keep them and key each spelling apart. Read back from a DIFFERENT cwd than
    # it was written in, so a key that still depended on the cwd cannot pass by coincidence.
    path = str(tmp_path / "ack.json")
    _, elsewhere = _two_directories(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert reverdict.acknowledge(_scope(Vault(spelling)), path) is True
    monkeypatch.chdir(elsewhere)
    assert reverdict.acknowledged(_scope(Vault(str(tmp_path / "v"))), path) is True


@pytest.mark.parametrize("branch", ["named", "dir-less"])
def test_one_relative_spelling_from_two_directories_is_two_vaults(
        tmp_path, monkeypatch, branch):
    # The SILENT direction. `v` from `a/` and `v` from `b/` are different directories, so
    # acknowledging one must not silence the other -- the per-vault harm the key exists
    # to prevent. A fix that simply stopped absolutising would give both the scope
    # `vault:v` and one shared key.
    monkeypatch.setenv("VAULT_DIR", "v")      # what the dir-less branch reads
    store = (lambda: Vault("v")) if branch == "named" else _NoDir
    path = str(tmp_path / "ack.json")
    a, b = _two_directories(tmp_path)
    monkeypatch.chdir(a)
    assert reverdict.acknowledge(_scope(store()), path) is True
    monkeypatch.chdir(b)
    assert reverdict.acknowledged(_scope(store()), path) is False


@pytest.mark.parametrize("branch", ["named", "dir-less"])
def test_a_symlink_repointed_at_another_vault_does_not_inherit_its_acknowledgement(
        tmp_path, monkeypatch, branch):
    # The SILENT direction through a NAME rather than a cwd. A vault reached through a
    # symlink is acknowledged, and the link is then pointed at a different vault. The
    # spelling never changes, so a key built from the spelling alone hands the second vault
    # the first one's acknowledgement -- and its affected leads are dismissed unannounced.
    path = str(tmp_path / "ack.json")
    a, b = _two_directories(tmp_path)
    link = tmp_path / "vault"
    monkeypatch.setenv("VAULT_DIR", str(link))      # what the dir-less branch reads
    store = (lambda: Vault(str(link))) if branch == "named" else _NoDir
    link.symlink_to(a, target_is_directory=True)
    assert reverdict.acknowledge(_scope(store()), path) is True
    link.unlink()
    link.symlink_to(b, target_is_directory=True)
    assert reverdict.acknowledged(_scope(store()), path) is False


def test_a_store_dir_is_keyed_as_given_not_expanded(tmp_path, monkeypatch):
    # `dir` names the location the store itself opens. A store handed `~/v` that does not
    # expand it opens a directory literally named `~` under whatever cwd the run starts in
    # -- a different directory from each cwd. Expanding it here would key all of them on
    # `$HOME/v`, so the first to acknowledge would silence the notice for the rest: the
    # silent direction. Resolved as given they stay apart, and a store that DOES expand `~`
    # has to expose the expanded path, as `Vault` does.
    class _TildeDir:
        dir = "~/v"

    path = str(tmp_path / "ack.json")
    a, b = _two_directories(tmp_path)
    monkeypatch.chdir(a)
    assert reverdict.acknowledge(_scope(_TildeDir()), path) is True
    monkeypatch.chdir(b)
    assert reverdict.acknowledged(_scope(_TildeDir()), path) is False


@pytest.mark.parametrize("configured", ["", "~/v", "{tmp}/v"],
                         ids=["unset", "tilde", "absolute"])
def test_a_dir_less_store_keeps_its_acknowledgement_across_directories(
        tmp_path, monkeypatch, configured):
    # The fallback branch reads `VAULT_DIR` raw, and each of these names ONE store
    # whatever the cwd, so each must key identically from anywhere. Two of them are traps
    # for the obvious fix of absolutising unconditionally: `abspath("")` IS the cwd, and
    # `abspath` does not expand `~`, so `~/v` absolutised alone lands under the cwd too.
    # `unset` sharing ONE key from every directory is correct only under the `Store`
    # contract that a store whose location is not fully determined by its name plus
    # `VAULT_DIR`/`vault_dir` exposes `dir` -- see `core/protocols.py` -- so this row pins
    # that contract's consequence, not a fact about every possible store.
    configured = configured.format(tmp=tmp_path)
    if configured:
        monkeypatch.setenv("VAULT_DIR", configured)
    else:
        monkeypatch.delenv("VAULT_DIR", raising=False)
    path = str(tmp_path / "ack.json")
    a, b = _two_directories(tmp_path)
    monkeypatch.chdir(a)
    assert reverdict.acknowledge(_scope(_NoDir()), path) is True
    monkeypatch.chdir(b)
    assert reverdict.acknowledged(_scope(_NoDir()), path) is True


def test_a_corrupt_marker_reads_as_not_yet_told(tmp_path):
    # Truncated by a crash, half-synced, hand-edited. Showing the notice a second time
    # is a repeat; skipping it is unrecoverable.
    path = tmp_path / "ack.json"
    path.write_text("{not json", encoding="utf-8")
    assert reverdict.acknowledged(_VAULT, str(path)) is False


def test_a_marker_that_is_not_an_object_reads_as_not_yet_told(tmp_path):
    # Valid JSON, wrong shape -- a distinct failure from the one above, and the one a
    # bare `json.load` succeeding would wave through.
    path = tmp_path / "ack.json"
    path.write_text("[]", encoding="utf-8")
    assert reverdict.acknowledged(_VAULT, str(path)) is False


def test_a_corrupt_marker_is_replaced_rather_than_merged_into(tmp_path):
    # It reads as "not shown" for every vault it names, so keeping it would strand all
    # of them in a permanent notice loop.
    path = tmp_path / "ack.json"
    path.write_text("[]", encoding="utf-8")
    assert reverdict.acknowledge(_VAULT, str(path)) is True
    assert reverdict.acknowledged(_VAULT, str(path)) is True


# `getattr`, not a bare call: `os.geteuid` does not exist on Windows, the package
# declares `Operating System :: OS Independent`, and pytest evaluates this decorator at
# IMPORT -- so a bare call fails COLLECTION of the whole module rather than skipping one
# row. The fallback is a non-zero uid, i.e. "not root, run the test".
@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="mode 0o500 does not stop root")
def test_a_marker_that_cannot_be_written_says_so_rather_than_raising(tmp_path):
    """The livelock guard, and the whole reason this returns a bool.

    The caller returns early -- doing nothing at all -- on the strength of "they will
    see this again next run", and the marker is the only thing making the next run
    different from this one. Measured against a read-only state directory before this
    signal existed: the notice re-showed and `run()` returned early on EVERY invocation,
    forever, exiting 0 and looking like an idle run.
    """
    unwritable = tmp_path / "ro"
    unwritable.mkdir()
    os.chmod(unwritable, 0o500)
    target = str(unwritable / "sub" / "ack.json")
    try:
        assert reverdict.acknowledge(_VAULT, target) is False
        assert reverdict.acknowledged(_VAULT, target) is False
    finally:
        os.chmod(unwritable, 0o700)


def test_an_explicit_path_beats_the_environment(tmp_path):
    # `path or resolve(...)`, the order `HealthStore` and `SeenDb` both state: reversed,
    # every test passing a tmp_path would silently retarget a developer's real state
    # file and stay green while doing it.
    explicit = str(tmp_path / "mine.json")
    assert reverdict._path(explicit) == explicit


def test_the_default_home_is_the_xdg_state_directory():
    # Pins WHERE, because the whole test suite depends on it: conftest sandboxes
    # XDG_STATE_HOME per test, so every engine test starts with the notice unshown. A
    # marker resolving anywhere else would make those tests read a developer's real
    # state -- passing or failing on what happened to be on their disk.
    resolved = reverdict._path()
    assert resolved.startswith(paths._xdg_path("state", "", warn=False).rstrip("/"))
    assert resolved.endswith("role_type_reverdict_ack.json")
