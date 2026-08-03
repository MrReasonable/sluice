"""The load-bearing invariants, re-asserted with `lead_layout` ON (#1).

PR A's lesson: `test_slug_is_issued_stable_and_unique` asserted a property the recursive scan broke
and still passed, because its fixture seeded two different companies and could never collide. A
guard whose fixture cannot produce the failing case is inert -- so these run the SAME properties
through the layout that could plausibly break them.

No new Store-contract property is added. Folders are a vault mechanism; per the #48 ruling, if only
THIS store's mechanism provides it, it is implementation detail, not a conformance guarantee.
"""
import os

from sluice.core.leads import ACTIVE_SUBDIR, ARCHIVE_SUBDIR, Lead
from sluice.core.vault import Vault


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    # `search` is REQUIRED (core/leads.py: source, search, title).
    return Lead(source="test", search="q", title=title, company=company, url=url, location="")


def test_a_merged_away_lead_is_not_recreated_with_the_layout_on(tmp_path):
    """#81 non-resurrection, under `active_archive`. `_merged/` is pruned at the TOP LEVEL of
    leads_dir, and the write folder is now a SUBFOLDER of leads_dir -- so this checks the prune
    and the probe still agree once creates no longer land beside `_merged/`."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    assert v.upsert(_lead(title="Example Role 2")) == "created"
    active = os.path.join(v.leads_dir, ACTIVE_SUBDIR)
    survivor = os.path.join(active, "Example Ltd - Example Role.md")
    loser = os.path.join(active, "Example Ltd - Example Role 2.md")
    assert os.path.isfile(survivor) and os.path.isfile(loser)

    archived = v.merge_cluster(survivor, [loser], alt_urls=[], first_seen="", last_seen="")
    assert len(archived) == 1, archived
    assert not os.path.exists(loser)

    # A fresh store, so nothing is carried in a cache: the re-scrape must be suppressed by the
    # ARCHIVE, not by in-memory state.
    v2 = Vault(str(tmp_path), lead_layout="active_archive")
    assert v2.upsert(_lead(title="Example Role 2")) == "merged_away"
    assert not os.path.exists(loser), "a merged-away lead was resurrected"


def test_a_rescrape_after_reconcile_touches_only_last_seen(tmp_path):
    """never-clobber, across a MOVE. Reconcile relocates a note; the next scrape must find it in
    its new folder and bump last_seen only -- never re-create it, never rewrite its status."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    v.upsert(_lead())
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    v.update_fields(ref, {"status": "dismiss", "score": "7"})
    v.reconcile_layout(apply=True)
    moved = os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "Example Ltd - Example Role.md")
    assert os.path.isfile(moved)
    before = open(moved, encoding="utf-8").read()

    assert v.upsert(_lead()) in ("updated", "merged")
    after = open(moved, encoding="utf-8").read()
    assert "status: dismiss" in after and "score: 7" in after

    # The precise property: every line EXCEPT last_seen is byte-identical. Written as a comparison
    # of filtered line lists rather than a substring check, because "status: dismiss is still in
    # there" is satisfied by a note that also gained or lost ten other lines.
    def _except_last_seen(text):
        return [ln for ln in text.splitlines() if not ln.startswith("last_seen")]

    assert _except_last_seen(after) == _except_last_seen(before)


def test_reconcile_never_writes_a_status(tmp_path):
    """never-regress. Reconcile moves directory entries and nothing else -- if it ever wrote a
    status it would be a second owner of the key core/status.py governs.

    The note is seeded in ARCHIVE so the sweep actually MOVES it. An earlier draft seeded
    `applied` where upsert had already put it (Active/), so the pass short-circuited on
    `in_place` and `_reserve_and_move` was never called -- the fixture asserted byte-stability for
    a note reconcile never touched, which is the cannot-produce-the-failing-case shape this
    module's own docstring cites as PR A's lesson."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    archive = os.path.join(v.leads_dir, ARCHIVE_SUBDIR)
    os.makedirs(archive, exist_ok=True)
    ref = os.path.join(archive, "Example Ltd - Example Role.md")
    with open(ref, "w", encoding="utf-8") as fh:
        fh.write("---\ncompany: Example Ltd\nrole: Example Role\nstatus: applied\n"
                 "score: 7\nurl: \nlast_seen: 2026-01-01\n---\nbody\n")
    before = open(ref, encoding="utf-8").read()

    rep = v.reconcile_layout(apply=True)
    # `applied` is LIVE, not terminal, so it belongs in Active/ -- the pass must move it there.
    assert len(rep["moves"]) == 1, f"the fixture did not reach the move path: {rep}"
    moved = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    assert os.path.isfile(moved)
    assert not os.path.exists(ref)
    # ...and the bytes that arrived are the bytes that left. No status written, nothing re-rendered.
    assert open(moved, encoding="utf-8").read() == before
