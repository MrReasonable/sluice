import os, tempfile, pathlib, sqlite3
import pytest
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path, _DEADLETTER_SUFFIX


def _db():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "track-seen.db.deadletter.db")))


def _entry(mid="m1", lead="Example Tidal - Analyst", **kw):
    base = dict(message_id=mid, lead=lead, candidates="", ev_type="rejection",
                proposal="rejection (conf 0.60)", hint='sluice track confirm --lead "x" --to rejected',
                first_seen="2026-07-10", times_surfaced=1)
    base.update(kw)
    return Entry(**base)


def test_derived_path_uses_gitignored_db_suffix():
    assert deadletter_path("./track-seen.db") == "./track-seen.db.deadletter.db"
    assert _DEADLETTER_SUFFIX.endswith(".db")   # gitignore *.db coverage is load-bearing


def test_record_then_open_round_trips():
    db = _db()
    db.record(_entry())
    got = db.open_entries()
    assert len(got) == 1
    assert got[0].message_id == "m1" and got[0].hint.endswith("--to rejected")


def test_missing_db_reads_empty_without_creating_it():
    db = _db()
    assert db.open_entries() == []
    assert not os.path.exists(db.path)   # a read must not create the store


def test_open_entries_oldest_first_seen():
    db = _db()
    db.record(_entry("mB", first_seen="2026-07-11"))
    db.record(_entry("mA", first_seen="2026-07-09"))
    assert [e.message_id for e in db.open_entries()] == ["mA", "mB"]


def test_bump_surfaced_increments_existing_only():
    db = _db()
    db.record(_entry("mA", times_surfaced=1))
    db.bump_surfaced()                       # bumps existing mA -> 2
    db.record(_entry("mB", times_surfaced=1))  # recorded AFTER bump -> stays 1
    got = {e.message_id: e.times_surfaced for e in db.open_entries()}
    assert got == {"mA": 2, "mB": 1}


def test_clear_lead_and_clear_id_return_counts():
    db = _db()
    db.record(_entry("m1", lead="Example Tidal - Analyst"))
    db.record(_entry("m2", lead="Example Tidal - Analyst"))
    db.record(_entry("m3", lead="Other - Role"))
    assert db.clear_lead("Example Tidal - Analyst") == 2
    assert [e.message_id for e in db.open_entries()] == ["m3"]
    assert db.clear_id("m3") == 1
    assert db.open_entries() == []


def test_clear_on_missing_db_is_noop_and_creates_no_file():
    db = _db()
    assert db.clear_lead("x") == 0 and db.clear_id("y") == 0
    assert db.open_entries() == []            # bump on missing is a no-op too
    db.bump_surfaced()
    assert not os.path.exists(db.path)        # no-op writes must not create the store


def test_corrupt_db_read_raises_not_silent_empty():
    db = _db()
    os.makedirs(os.path.dirname(db.path), exist_ok=True)
    with open(db.path, "w") as f:
        f.write("not a sqlite file")
    with pytest.raises(Exception):
        db.open_entries()   # corrupt MUST fail loud, never silently drop the backlog


def test_record_write_failure_propagates():
    d = tempfile.mkdtemp()
    dbpath = str(pathlib.Path(d, "x.db"))
    os.mkdir(dbpath)        # a directory where the db file should be -> the write raises
    with pytest.raises(Exception):
        DeadLetterDb(dbpath).record(_entry())


def test_open_entries_on_existing_tableless_db_raises_not_silent_empty():
    # F1: an existing-but-tableless file (a valid sqlite db that simply never got
    # `record`ed into) must RAISE on read, not silently create the table and
    # return []. Only `record` is the store's sole table creator; a read that
    # papered over this would hide a real anomaly (wrong file, botched migration).
    d = tempfile.mkdtemp()
    dbpath = str(pathlib.Path(d, "track-seen.db.deadletter.db"))
    con = sqlite3.connect(dbpath)
    con.execute("CREATE TABLE other(x)")
    con.commit()
    con.close()
    with pytest.raises(Exception):
        DeadLetterDb(dbpath).open_entries()


def test_a_read_never_creates_the_store_when_it_vanishes_mid_check(tmp_path, monkeypatch):
    """`_open` must not be able to CREATE, and the cost here is worse than a lost read.

    The `_absent` check and the open are two syscalls, and a plain `sqlite3.connect`
    creates on open, so a store removed between them left a fresh 0-BYTE
    `.deadletter.db`. That file then satisfies `exists(resolved + suffix)` in
    `paths.resolve`, which drops the sidecar from `stranded` -- so the notice about the
    real backlog still sitting at the legacy path goes quiet PERMANENTLY, and #49's whole
    point is that a proposal re-surfaces until a human acts on it.

    `SeenDb.load` was fixed for this first and this store was missed, which is the same
    way the EACCES and dangling-ancestor gaps happened. Both now share
    `core.paths.existing_db_uri`.

    The window has to be forced open: pointing at an already-absent path proves nothing,
    because `_absent` returns before the open is reached. `stat`/`unlink` are captured
    before patching and the removal is one-shot, or anything in the hook that touches the
    filesystem re-enters it.
    """
    import sluice.core.paths as paths_mod

    store = tmp_path / "track-seen.db.deadletter.db"
    DeadLetterDb(str(store)).record(
        Entry("m1", "Example - Analyst", "", "rejection", "p", "h", "2026-07-10", 1))
    assert store.exists()

    real_stat, real_unlink, fired = os.stat, os.unlink, []

    def _stat_then_vanish(path, *a, **k):
        result = real_stat(path, *a, **k)
        if not fired and str(path) == str(store):
            fired.append(True)
            real_unlink(str(store))
        return result

    monkeypatch.setattr(paths_mod.os, "stat", _stat_then_vanish)
    with pytest.raises(sqlite3.OperationalError):
        DeadLetterDb(str(store)).open_entries()
    assert not store.exists(), (
        "a READ created the dead-letter store; that file silences the migration notice "
        "for the real backlog at the legacy path")
