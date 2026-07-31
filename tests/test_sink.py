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


def test_vaultsink_isolates_a_failing_write(tmp_path, monkeypatch):
    # One lead the store cannot write must not sink the batch: it is counted
    # `skipped`, kept OUT of seen.db (so it retries next run), and its neighbours
    # are still written.
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    good1 = _lead(company="Aye", url="https://a/1")
    bad = _lead(company="Bee", url="https://a/2")
    good2 = _lead(company="Cee", url="https://a/3")

    real_upsert = vault.upsert

    def flaky(lead):
        if lead.url == "https://a/2":
            raise OSError("simulated store refusal")
        return real_upsert(lead)

    monkeypatch.setattr(vault, "upsert", flaky)
    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([good1, bad, good2])

    assert counts == {"created": 2, "updated": 0, "skipped": 1}
    loaded = seen.load()
    assert "https://a/1" in loaded and "https://a/3" in loaded
    assert "https://a/2" not in loaded            # retried next run, not swallowed


def test_vaultsink_records_merged_but_not_refused(tmp_path, monkeypatch):
    # created and merged both mean a note now exists -> recorded in seen.db. refused (a
    # name-collision decline) stays OUT so it retries next run.
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    created = _lead(company="Aye", url="https://example.invalid/1")
    merged = _lead(company="Bee", url="https://example.invalid/2")
    refused = _lead(company="Cee", url="https://example.invalid/3")

    real = vault.upsert
    def fake(lead):
        if lead.url == "https://example.invalid/2":
            return "merged"
        if lead.url == "https://example.invalid/3":
            return "refused"
        return real(lead)
    monkeypatch.setattr(vault, "upsert", fake)

    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([created, merged, refused])
    assert counts.get("created") == 1 and counts.get("merged") == 1 and counts.get("refused") == 1
    loaded = seen.load()
    assert "https://example.invalid/1" in loaded and "https://example.invalid/2" in loaded   # recorded
    assert "https://example.invalid/3" not in loaded                            # refused -> retried


def test_vaultsink_records_url_proven_merged_away(tmp_path, monkeypatch):
    # merged_away (#81) is a URL-PROVEN match: the incoming lead carries the same non-empty
    # url as the note archived under _merged/, so that note already IS this job and this
    # means a note now exists exactly like `merged` does -- recorded in seen.db, which
    # self-heals the dedup set so the suppression happens once rather than every run.
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    lead = _lead(url="https://example.invalid/1")
    monkeypatch.setattr(vault, "upsert", lambda lead: "merged_away")

    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([lead])

    assert counts.get("merged_away") == 1
    assert lead.dedup_key in seen.load()


def test_vaultsink_excludes_unproven_merged_away_from_seen_db(tmp_path, monkeypatch):
    # merged_away_unproven (#81) is every match WEAKER than a url match -- a location-only
    # SAME, or UNKNOWN. seen.db has no removal path (load/save only) and
    # engine.py filters on dedup_key BEFORE the sink on every later run, so recording a
    # weak-evidence suppression would make this lead unreachable forever with no note
    # anywhere. It must stay OUT of seen.db and re-surface every run until a human acts.
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    lead = _lead(url="https://example.invalid/1")
    monkeypatch.setattr(vault, "upsert", lambda lead: "merged_away_unproven")

    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([lead])

    assert counts.get("merged_away_unproven") == 1
    assert lead.dedup_key not in seen.load()
