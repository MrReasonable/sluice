"""Vault.merge_cluster: union the audit trail onto a SEEDED survivor without clobbering
its state, archive losers reversibly. The survivor is seeded (not empty) — empty-survives-
empty certifies nothing (#23 tst-001)."""
import json

import pytest

from sluice.core.vault import Vault
from sluice.core.protocols import VaultConflict
from tests.conftest import LOCATIONS, racing_read


def _mk(tmp_path):
    # The loser's location must be a SECOND, token-disjoint LOCATIONS entry, not "" -- an
    # empty location is UNKNOWN evidence under _compare_locations (same_opportunity), so
    # upsert silently MERGES the second lead into the first note instead of creating a
    # second one, leaving nothing at url .../2 for merge_cluster to archive. Two non-empty,
    # disjoint locations is a proven-DIFFERENT verdict, which is what actually seeds two
    # on-disk notes for merge_cluster's own behaviour to be exercised against.
    v = Vault(str(tmp_path))
    from sluice.core.leads import Lead
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[0], url="https://ex.invalid/1",
                  first_seen="2026-07-10", last_seen="2026-07-10"))
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[1], url="https://ex.invalid/2",
                  first_seen="2026-07-05", last_seen="2026-07-20"))
    return v


def _by_url(v, u):
    return next(n for n in v.read_leads() if n.fm.get("url") == u)


def test_survivor_state_survives_only_audit_trail_changes(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    v.update_fields(survivor.ref, {"status": "applied", "score": "9",
                                   "tailored_cv": "CV_ab12.pdf", "applied_date": "2026-07-11"})
    survivor = _by_url(v, "https://ex.invalid/1")
    before = dict(survivor.fm)
    loser = _by_url(v, "https://ex.invalid/2")
    archived = v.merge_cluster(survivor.ref, [loser.ref],
                               alt_urls=["https://ex.invalid/2"],
                               first_seen="2026-07-05", last_seen="2026-07-20")
    after = _by_url(v, "https://ex.invalid/1")
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed
    assert json.loads(after.fm["alt_urls"]) == ["https://ex.invalid/2"]
    assert after.fm["first_seen"] == "2026-07-05"      # min
    assert after.fm["last_seen"] == "2026-07-20"       # max
    assert len(v.read_leads()) == 1                    # loser archived out of the active view
    assert archived and archived[0].endswith(".md")


def test_timestamps_never_moved_the_wrong_way(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")   # first_seen 2026-07-10, last_seen 2026-07-10
    loser = _by_url(v, "https://ex.invalid/2")
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                    first_seen="2026-07-30", last_seen="2026-07-01")  # stale params, wrong direction
    after = _by_url(v, "https://ex.invalid/1")
    assert after.fm["first_seen"] == "2026-07-10"   # not raised
    assert after.fm["last_seen"] == "2026-07-10"    # not lowered


def test_alt_urls_round_trips_a_comma_bearing_url(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    url = "https://ex.invalid/j?a=1,2&b=3"
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[url],
                    first_seen="2026-07-05", last_seen="2026-07-20")
    after = _by_url(v, "https://ex.invalid/1")
    assert json.loads(after.fm["alt_urls"]) == [url]


def test_survivor_conflict_archives_zero_losers(tmp_path, monkeypatch):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    racing_read(monkeypatch, survivor.ref,
                lambda: open(survivor.ref, "a").write("\n"), once=False)  # sustained race
    with pytest.raises(VaultConflict):
        v.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://ex.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    assert len(v.read_leads()) == 2      # loser NOT archived — conflict aborted before any archive
