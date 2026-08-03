"""The ONE atomic note-move (#1). Two callers, two collision policies, one definition.

`os.replace` alone is atomic but OVERWRITES the destination; `os.link` + `os.unlink` never
overwrites but has a window where a concurrent atomic save of the source is DELETED rather than
moved. CodeRabbit flagged each in turn on #23. The shape that satisfies both is O_EXCL-reserve then
`os.replace`: the reserve is atomic (a concurrent archiver loses it, it does not race), and the
replace moves whatever `src` names AT THAT INSTANT, overwriting only our own zero-byte reservation.
"""
import os

import pytest

from sluice.core.vault import _reserve_and_move


def _note(path, text="---\ncompany: Example Ltd\n---\nbody\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_it_moves_the_file_and_returns_the_destination(tmp_path):
    src = _note(str(tmp_path / "from" / "N.md"), "PAYLOAD")
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)
    got = _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert got == os.path.join(dest_dir, "N.md")
    assert not os.path.exists(src)
    assert open(got, encoding="utf-8").read() == "PAYLOAD"


def test_a_collision_raises_when_suffixing_is_off(tmp_path):
    """Reconcile's policy. A numeric suffix changes the FILENAME, which is the slug, which is the
    IDENTITY -- a renamed note is no longer any candidate `_resolve_path` walks, so the next
    scrape mints a fresh note and orphans the renamed one. Refusing is the only safe answer."""
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    with pytest.raises(FileExistsError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert open(src, encoding="utf-8").read() == "MINE", "the source must be untouched"
    assert open(os.path.join(dest_dir, "N.md"), encoding="utf-8").read() == "THEIRS"


def test_a_refused_collision_leaves_no_reservation_behind(tmp_path):
    """The refusal never RESERVED anything, so it must not unlink anything either -- an
    over-eager cleanup here would delete the colliding note, which is the file we refused in
    order to protect."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    with pytest.raises(FileExistsError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert sorted(os.listdir(dest_dir)) == ["N.md"]


def test_a_collision_takes_the_next_suffix_when_suffixing_is_on(tmp_path):
    """merge_cluster's policy, unchanged: an archived loser's filename is not an identity the
    write path walks, so a suffix there costs nothing, while failing to archive would cost #81."""
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    got = _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=True)
    assert got == os.path.join(dest_dir, "N.1.md")
    assert open(got, encoding="utf-8").read() == "MINE"
    assert open(os.path.join(dest_dir, "N.md"), encoding="utf-8").read() == "THEIRS"


def test_suffixing_walks_past_several_taken_names(tmp_path):
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    for taken in ("N.md", "N.1.md", "N.2.md"):
        _note(os.path.join(dest_dir, taken), "THEIRS")
    assert _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=True) == \
        os.path.join(dest_dir, "N.3.md")


def test_a_failed_move_removes_its_own_reservation(tmp_path, monkeypatch):
    """The reservation is a real zero-byte file. If the replace then fails (disk full,
    permissions, a source deleted under us), leaving it behind seats a zero-byte note at a real
    lead's name -- which `_locate` finds, `_is_note_file` calls a note, and `_resolve_path`
    reconciles against. Ownership is proved by OUR open succeeding, never by os.path.exists."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert os.listdir(dest_dir) == [], "the reservation was left behind"
    assert os.path.exists(src), "the source must survive a failed move"


def test_merge_cluster_still_archives_through_the_shared_primitive(tmp_path):
    """The extraction must not change merge_cluster. Driven through the PUBLIC method, so this
    fails if the refactor lost the suffix policy, the #81 stamp, or the per-loser isolation."""
    from sluice.core.vault import Vault
    v = Vault(str(tmp_path))
    os.makedirs(v.leads_dir, exist_ok=True)
    fm = "---\ncompany: Example Ltd\nrole: Example Role\nstatus: new\nurl: \n---\nbody\n"
    survivor = _note(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"), fm)
    loser = _note(os.path.join(v.leads_dir, "Example Ltd - Example Role 2.md"), fm)
    archived = v.merge_cluster(survivor, [loser], alt_urls=[], first_seen="", last_seen="")
    assert len(archived) == 1
    assert os.path.dirname(archived[0]).endswith("_merged")
    assert not os.path.exists(loser)
    assert "archived_from_note" in open(archived[0], encoding="utf-8").read()


def test_a_close_failure_still_leaves_no_reservation_behind(tmp_path, monkeypatch):
    """Ownership is recorded the INSTANT the open returns a handle, before anything that can
    itself fail. `os.close` can raise, and a close that fails still leaves the 0-byte file on
    disk -- recording ownership after it leaked exactly that: a zero-byte note seated at a real
    lead's name, which `_is_note_file` calls a note and `_resolve_path` reconciles against."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)
    real_close = os.close

    def flaky_close(fd):
        real_close(fd)
        raise OSError(5, "I/O error on close")

    monkeypatch.setattr(os, "close", flaky_close)
    with pytest.raises(OSError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    monkeypatch.undo()
    assert os.listdir(dest_dir) == [], "the reservation leaked when close failed"


def test_cleanup_never_unlinks_a_file_a_racer_put_at_the_destination(tmp_path, monkeypatch):
    """Ownership at RESERVE time is not ownership at CLEANUP time. Between the two a concurrent
    writer can `os.replace` its own file onto that name; unlinking then destroys a note we never
    owned -- a clobber inside a clobber-fix, which is #16's `os.path.exists` lesson one rung
    along. The inode recorded from the open fd is compared with what the name resolves to now."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)
    dest = os.path.join(dest_dir, "N.md")

    real_replace = os.replace

    def racer_then_fail(a, b):
        # A real racer REPLACES, which swaps the inode. An earlier draft of this fixture used
        # `open(dest, "w")`, which TRUNCATES IN PLACE and leaves the inode ours -- so the identity
        # check correctly matched, the reservation was removed, and the test failed for the one
        # reason that is not a defect. A probe that fails to construct its precondition looks
        # exactly like a probe that disproves the claim.
        theirs = os.path.join(dest_dir, ".theirs")
        with open(theirs, "w", encoding="utf-8") as fh:
            fh.write("THEIRS")
        real_replace(theirs, dest)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", racer_then_fail)
    with pytest.raises(OSError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    monkeypatch.undo()
    assert os.path.isfile(dest), "cleanup deleted the racer's file"
    assert open(dest, encoding="utf-8").read() == "THEIRS"


def test_a_reservation_whose_fstat_failed_is_still_removed(tmp_path, monkeypatch):
    """The `ident is None` arm of `_unlink_reservation`, which had a stated rationale and no
    witness -- inverting it to leak instead of unlink survived the whole suite.

    `fstat` failing leaves `reserved` set (ownership is recorded the instant the open returns)
    but `reserved_id` None, so identity cannot be compared. The documented trade is to remove it
    anyway: the window is nanoseconds after our own create, and a leaked zero-byte note at a real
    lead's name -- which `_is_note_file` calls a note and `_resolve_path` reconciles against -- is
    the likelier harm. This pins that decision."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)

    def no_fstat(fd):
        raise OSError(5, "I/O error")

    def boom(a, b):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fstat", no_fstat)
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    monkeypatch.undo()
    assert os.listdir(dest_dir) == [], "the reservation leaked when fstat failed"
