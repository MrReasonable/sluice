"""#223 §2.1: the one-shot marker behind the re-verdict notice.

Every property here is about failing in the LOUD direction. A marker that reads as
present when it is not silently re-verdicts a vault, and `dismiss` is not in
`DEFAULT_TRIAGE_STATUSES`, so those leads are never re-selected and the user never sees
them again. A marker that reads as absent when it is present costs one skipped run.
"""
import os

import pytest

from sluice.core import paths
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


def test_the_same_vault_named_two_ways_is_one_key(tmp_path):
    # `./vault` from inside a directory and its absolute spelling are one vault, so they
    # must share one acknowledgement -- otherwise the notice re-shows on a `cd`.
    path = str(tmp_path / "ack.json")
    vault = tmp_path / "v"
    vault.mkdir()
    reverdict.acknowledge(str(vault), path)
    cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        assert reverdict.acknowledged("v", path) is True
        assert reverdict.acknowledged("./v", path) is True
    finally:
        os.chdir(cwd)


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
