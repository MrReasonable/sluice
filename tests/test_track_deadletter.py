import os, tempfile, pathlib
import pytest
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path, _DEADLETTER_SUFFIX


def _db():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "track-seen.db.deadletter.db")))


def _entry(mid="m1", lead="Tidemark - Analyst", **kw):
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
    db.record(_entry("m1", lead="Tidemark - Analyst"))
    db.record(_entry("m2", lead="Tidemark - Analyst"))
    db.record(_entry("m3", lead="Other - Role"))
    assert db.clear_lead("Tidemark - Analyst") == 2
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
