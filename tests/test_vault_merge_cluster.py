"""Vault.merge_cluster: union the audit trail onto a SEEDED survivor without clobbering
its state, archive losers reversibly. The survivor is seeded (not empty) — empty-survives-
empty certifies nothing (#23 tst-001)."""
import json
import os

import pytest

from sluice.core.vault import Vault, _fm_dict, _split_frontmatter
from sluice.core.protocols import MalformedNoteField, VaultConflict
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


def _mk3(tmp_path):
    # Three token-disjoint LOCATIONS entries -- same reasoning as _mk: a blank/repeated
    # location would be UNKNOWN evidence under _compare_locations and upsert would merge
    # the second or third lead into an earlier note instead of creating a separate one,
    # leaving fewer than three on-disk notes for per-loser archive isolation to exercise.
    v = Vault(str(tmp_path))
    from sluice.core.leads import Lead
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[0], url="https://ex.invalid/1",
                  first_seen="2026-07-10", last_seen="2026-07-10"))
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[1], url="https://ex.invalid/2",
                  first_seen="2026-07-05", last_seen="2026-07-20"))
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[2], url="https://ex.invalid/3",
                  first_seen="2026-07-01", last_seen="2026-07-15"))
    return v


def _by_url(v, u):
    return next(n for n in v.read_leads() if n.fm.get("url") == u)


def test_survivor_state_survives_only_audit_trail_changes(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    v.update_fields(survivor.ref, {"status": "applied", "score": "9",
                                   "tailored_cv": "CV_ab12.pdf", "applied_date": "2026-07-11"})
    v.append_body_section(survivor.ref, "dedup-body-tag", "## Seeded body\nkeep me\n")
    survivor = _by_url(v, "https://ex.invalid/1")
    before = dict(survivor.fm)
    before_body = _by_url(v, "https://ex.invalid/1").body
    loser = _by_url(v, "https://ex.invalid/2")
    archived = v.merge_cluster(survivor.ref, [loser.ref],
                               alt_urls=["https://ex.invalid/2"],
                               first_seen="2026-07-05", last_seen="2026-07-20")
    after = _by_url(v, "https://ex.invalid/1")
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed
    assert after.body == before_body                   # never-clobber covers the BODY too
    assert json.loads(after.fm["alt_urls"]) == ["https://ex.invalid/2"]
    assert after.fm["first_seen"] == "2026-07-05"      # min
    assert after.fm["last_seen"] == "2026-07-20"       # max
    assert len(v.read_leads()) == 1                    # loser archived out of the active view
    assert archived and archived[0].endswith(".md")


def test_loser_state_survives_the_archive_stamp(tmp_path):
    # Final review, finding 1: before #81, merge_cluster only MOVED a loser -- it never
    # wrote to one. `_stamp_archived_from` (called from inside merge_cluster, AFTER the
    # move) is a NEW write site on never-clobber's own function, and nothing pinned that
    # it is surgical. The sibling test above pins the SURVIVOR's state across a merge;
    # this pins the LOSER's, read back from its ARCHIVED path (read_leads() never sees
    # `_merged/`), since the loser's status/score/tailored_cv/body is the human's ONLY
    # remaining record of a merged-away lead -- and the documented recovery (moving the
    # note back out of `_merged/` by hand, docs/ARCHITECTURE.md) depends on all of it
    # surviving the stamp intact.
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    v.update_fields(loser.ref, {"status": "shortlist", "score": "7",
                                "tailored_cv": "CV_zz99.pdf"})
    v.append_body_section(loser.ref, "loser-body-tag", "## Seeded loser body\nkeep me too\n")
    loser = _by_url(v, "https://ex.invalid/2")
    before_fm = dict(loser.fm)
    before_body = loser.body

    archived = v.merge_cluster(survivor.ref, [loser.ref],
                               alt_urls=["https://ex.invalid/2"],
                               first_seen="2026-07-05", last_seen="2026-07-20")
    assert len(archived) == 1

    with open(archived[0], encoding="utf-8") as f:
        after_text = f.read()
    inner, after_body = _split_frontmatter(after_text)
    after_fm = _fm_dict(inner)

    changed = {k for k in before_fm if before_fm.get(k) != after_fm.get(k)} | (set(after_fm) - set(before_fm))
    assert changed == {"archived_from_note"}, changed   # the ONLY key the stamp may touch
    assert after_body == before_body                    # body byte-for-byte, including the seeded section


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


def test_malformed_alt_urls_rejects_merge(tmp_path):
    # A malformed alt_urls (e.g. a human hand-edit that broke the JSON) must not be
    # silently reset -- that would clobber a value that might carry real information.
    # merge_cluster raises instead, and the raise fires from INSIDE the CAS transform,
    # before _atomic_write and before the archive loop -- so the survivor's malformed
    # value is left exactly as it was and the loser is never archived.
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    v.update_fields(survivor.ref, {"alt_urls": "not json ["})
    before = _by_url(v, "https://ex.invalid/1").fm["alt_urls"]

    with pytest.raises(MalformedNoteField):
        v.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://ex.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")

    after = _by_url(v, "https://ex.invalid/1")
    assert after.fm["alt_urls"] == before                # malformed value UNCHANGED
    assert len(v.read_leads()) == 2                       # loser NOT archived


def test_per_loser_archive_failure_isolated_not_fatal(tmp_path, monkeypatch):
    # inv-r2-002: a failed archive of ONE loser (e.g. a permissions/ENOSPC blip on
    # the archive's os.replace) must not abort the whole cluster and must not be
    # counted as merged -- that loser stays in the active view, while a succeeding
    # loser is still archived and unioned. Three separate notes: one survivor, one
    # loser that archives cleanly, one loser whose archive os.replace is made to fail.
    v = _mk3(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser_ok = _by_url(v, "https://ex.invalid/2")
    loser_fail = _by_url(v, "https://ex.invalid/3")
    fail_basename = os.path.basename(loser_fail.ref)

    import sluice.core.vault as vault_mod
    real_replace = vault_mod.os.replace

    def flaky_replace(src, dst):
        # os.replace is ALSO used by _atomic_write for the SURVIVOR's CAS write, so
        # this must fail ONLY the archive move into _merged/ for loser_fail's
        # basename -- every other os.replace call (the survivor's own write, and
        # loser_ok's archive) must run for real.
        if "_merged" in dst and os.path.basename(dst) == fail_basename:
            raise OSError("simulated: could not archive this loser")
        return real_replace(src, dst)

    monkeypatch.setattr(vault_mod.os, "replace", flaky_replace)

    archived = v.merge_cluster(
        survivor.ref, [loser_ok.ref, loser_fail.ref],
        alt_urls=["https://ex.invalid/2", "https://ex.invalid/3"],
        first_seen="2026-07-01", last_seen="2026-07-20",
    )
    monkeypatch.undo()

    # merge_cluster itself must not raise (asserted implicitly by reaching here) and
    # must report only the ONE loser that actually archived.
    assert len(archived) == 1
    assert archived[0] == os.path.join(v.leads_dir, "_merged", os.path.basename(loser_ok.ref))

    active_urls = {n.fm.get("url") for n in v.read_leads()}
    assert active_urls == {"https://ex.invalid/1", "https://ex.invalid/3"}  # failed loser STILL active
    assert "https://ex.invalid/2" not in active_urls                        # succeeding loser archived away

    survivor_after = _by_url(v, "https://ex.invalid/1")
    assert json.loads(survivor_after.fm["alt_urls"]) == [
        "https://ex.invalid/2", "https://ex.invalid/3",
    ]  # audit trail unions BOTH urls regardless of per-loser archive outcome

    # The O_EXCL reservation for the failed loser must be cleaned up on failure --
    # not left as an orphaned 0-byte file blocking a future archive attempt.
    merged_dir = os.path.join(v.leads_dir, "_merged")
    assert fail_basename not in os.listdir(merged_dir)
