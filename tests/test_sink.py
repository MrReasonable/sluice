import io
import json

from sluice.core.leads import Lead
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault
from sluice.ingest.sink import JsonSink, VaultSink


def _lead(**kw):
    base = dict(source="cord", search="Analyst", title="Analyst", company="Acme", url="https://a/1")
    base.update(kw)
    return Lead(**base)


def test_vaultsink_creates_file_stamps_dates_and_records_seendb(tmp_path):
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    sink = VaultSink(vault, seen, today=lambda: "2026-07-07")
    counts = sink.write([_lead()])
    assert counts["created"] == 1
    f = tmp_path / "vault" / "Job Applications" / "Job Leads" / "Acme - Analyst.md"
    assert f.exists()
    assert "first_seen: 2026-07-07" in f.read_text()
    assert "https://a/1" in seen.load()


def test_vaultsink_second_write_updates_not_creates(tmp_path):
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    VaultSink(vault, seen, today=lambda: "2026-07-07").write([_lead()])
    counts = VaultSink(vault, seen, today=lambda: "2026-07-09").write([_lead()])
    assert counts == {"created": 0, "updated": 1, "skipped": 0}
    f = tmp_path / "vault" / "Job Applications" / "Job Leads" / "Acme - Analyst.md"
    assert "last_seen: 2026-07-09" in f.read_text()


def test_jsonsink_writes_one_json_line_per_lead():
    buf = io.StringIO()
    counts = JsonSink(buf).write([_lead(url="https://a/1"), _lead(url="https://a/2")])
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["url"] == "https://a/1"
    assert counts["created"] == 2
