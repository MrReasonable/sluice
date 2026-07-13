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
