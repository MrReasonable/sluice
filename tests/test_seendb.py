import pytest

from sluice.core.leads import Lead
from sluice.core.seendb import SeenDb


def test_save_then_load_roundtrip(tmp_path):
    db = SeenDb(str(tmp_path / "seen.db"))
    db.save([Lead(source="s", search="x", title="t", url="https://a/1")])
    assert "https://a/1" in db.load()


def test_missing_db_loads_empty(tmp_path):
    assert SeenDb(str(tmp_path / "none.db")).load() == set()


def test_save_is_idempotent(tmp_path):
    db = SeenDb(str(tmp_path / "seen.db"))
    lead = Lead(source="s", search="x", title="t", url="https://a/1")
    db.save([lead])
    db.save([lead])
    assert db.load() == {"https://a/1"}


def test_a_corrupt_db_raises_rather_than_reading_as_empty(tmp_path):
    """A silent empty dedup set is the #81 harm by another route.

    `except Exception: return set()` turned an unreadable dedup store into a full,
    silent dedup loss: every lead reads unseen, every human-merged duplicate is
    re-created, and a lead whose twin was already `applied` can produce a second
    application under the user's name -- reported as ordinary `created: N`. Refusing to
    start over a RELOCATED store (#80) while shrugging at an unreadable one is
    incoherent, so this is loud.
    """
    import sqlite3

    p = tmp_path / "seen.db"
    p.write_bytes(b"this is not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SeenDb(str(p)).load()


def test_an_existing_db_with_no_table_reads_as_empty(tmp_path):
    """...but this state is NOT corruption, and must stay tolerated.

    A 0-byte file is a valid empty sqlite database, and #80's own earlier bug left those
    behind (`sqlite3.connect` created one just by opening it). Raising here would turn
    that fix into a hard failure on the next run for precisely the people it is for.
    `save` creates the table.
    """
    p = tmp_path / "seen.db"
    p.write_bytes(b"")
    assert SeenDb(str(p)).load() == set()
