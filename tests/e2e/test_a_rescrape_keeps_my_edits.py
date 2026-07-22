"""A re-scrape reaches the vault and touches ONLY last_seen.

The never-clobber invariant: re-seeing an existing lead bumps its `last_seen`
marker and rewrites nothing else -- not status, not enrichment, not the body a
human or agent edited.

The trap this test is built to avoid: `ingest/engine.py` skips a lead already in
`seen.db` BEFORE `sink.write`, so `Vault.upsert` never runs and a re-scrape that
"changed nothing" proves nothing -- a reviewer once hand-edited a note between two
runs and it came back byte-identical because upsert never executed, which "assert
only last_seen moved" scored as a PASS. So this run uses a FRESH seen.db and an
ADVANCED clock, and asserts `written == {updated: 1}` as a PRECONDITION -- upsert
demonstrably ran -- before checking that it touched only the marker.
"""
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]


def test_a_rescrape_keeps_my_edits(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    src = _sources.get("remoteok")
    backend = ScriptedBackend()  # ingest never calls the backend

    # ── run 1: create the note at 2026-07-09 ──
    r1 = h.sluice(backend, today=lambda: "2026-07-09").ingest([src])
    assert r1.written["created"] == 1
    ref = h.vault.read_leads()[0].ref

    # A human/agent then edits real state: a decision (status), an enrichment
    # field, and a body line. None of it may be disturbed by a re-scrape.
    original = _read(ref)
    edited = (original
              .replace("status: new", "status: shortlist")
              .replace("score: 0", "score: 88")
              .replace("**Status:** new", "**Status:** shortlist\nHUMAN NOTE: keep this."))
    _write(ref, edited)
    before = _read(ref)
    assert "last_seen: 2026-07-09" in before  # the only line the re-scrape may move

    # ── run 2: fresh seen.db + advanced clock, so upsert actually runs ──
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "seen-run2.db"))
    r2 = h.sluice(backend, today=lambda: "2026-07-15").ingest([src])
    # PRECONDITION: the lead reached the store and upsert took the update path.
    assert r2.written == {"created": 0, "updated": 1, "skipped": 0}

    after = _read(ref)
    # Exactly the last_seen marker advanced; every other byte is preserved.
    assert after == before.replace("last_seen: 2026-07-09", "last_seen: 2026-07-15")
    assert "status: shortlist" in after       # the decision survived
    assert "score: 88" in after               # the enrichment survived
    assert "HUMAN NOTE: keep this." in after   # the body edit survived
    assert "first_seen: 2026-07-09" in after   # first_seen never regresses or moves


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
