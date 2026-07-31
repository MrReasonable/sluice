"""#81: the write path must honour a human's merge decision.

A lead archived to `_merged/` by `leads dedupe --merge` must not be re-created when the
dedup set is empty. Fixtures are synthetic: LOCATIONS placeholders, abstract company/role,
`.invalid` urls -- no faker (see tests/test_leads_cluster.py's ruling)."""
import os

from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _lead(**kw):
    base = dict(source="cord", search="s", title="Y", company="X",
                url="https://ex.invalid/1", location=LOCATIONS[0],
                first_seen="2026-07-07", last_seen="2026-07-07")
    base.update(kw)
    return Lead(**base)


def _merge_away(v, loser_lead, survivor_lead):
    """Archive `loser_lead`'s note through the REAL merge_cluster, so the fixture cannot
    drift from what the production archive path actually writes."""
    assert v.upsert(survivor_lead) == "created"
    assert v.upsert(loser_lead) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor, loser = notes[survivor_lead.url], notes[loser_lead.url]
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[loser_lead.url],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    return survivor, loser


def test_merged_away_lead_is_not_recreated(tmp_path):
    """The acceptance property. Fails on 10b0cdd, where upsert returns 'created'."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    assert len(v.read_leads()) == 1

    # The dedup set is empty (0-byte/tableless seen.db, fresh machine, retargeted SEEN_DB),
    # so the loser is not filtered at ingest and reaches the write path again.
    assert v.upsert(loser) == "merged_away"
    assert len(v.read_leads()) == 1


def test_proven_different_archived_note_does_not_suppress(tmp_path):
    """A genuinely new job at a merged-away name is still created: DIFFERENT advances."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    # Same name as the archived loser, but a token-disjoint location -> DIFFERENT.
    fresh = _lead(title="Widget Engineer Senior", url="", location=LOCATIONS[1])
    assert v.upsert(fresh) == "created"


def test_unknown_verdict_suppresses_as_unproven(tmp_path):
    """A blank location on either side is UNKNOWN: suppress, but on the UNPROVEN arm."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    blank = _lead(title="Widget Engineer Senior", url="", location="")
    assert v.upsert(blank) == "merged_away_unproven"


def test_bare_prefix_would_over_match_a_different_job(tmp_path):
    """The ANCHOR witness. Two genuinely different jobs whose names share a prefix; the
    LONGER is merged away, then the SHORTER is scraped. A bare `startswith` match would
    suppress it -- and its verdict is UNKNOWN, so the 'treat DIFFERENT as a hit' mutant
    does not reach this case and it needs its own test."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Anchor Survivor", url="https://ex.invalid/9")
    longer = _lead(title="Y II", url="https://ex.invalid/2", location="")
    _merge_away(v, longer, survivor)
    shorter = _lead(title="Y", url="", location="")
    assert v.upsert(shorter) == "created"


def test_loser_archived_under_its_location_suffixed_name_is_found(tmp_path):
    """The probe covers ALL candidates, not just the one the walk stopped at. Candidate 1
    must ALSO be absent -- otherwise the 'probe only where the walk stopped' mutant stays
    green, because the walk itself stops at candidate 2."""
    v = Vault(str(tmp_path))
    third = _lead(title="Third Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    cand1 = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(cand1) == "created"
    # A token-disjoint location at the same name forces the location-suffixed candidate.
    cand2 = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(cand2) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor = notes["https://ex.invalid/9"]
    v.merge_cluster(survivor.ref,
                    [notes["https://ex.invalid/1"].ref, notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/1", "https://ex.invalid/2"],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    assert v.upsert(cand2) == "merged_away"


def test_numeric_suffix_archive_is_found(tmp_path):
    """merge_cluster archives a name-colliding loser as `<stem>.1.md`. Re-upsert B, NOT A:
    A sits at `_merged/<base>.md` and an exact-name probe already catches it, so only B's
    re-upsert exercises the suffix path at all."""
    v = Vault(str(tmp_path))
    third = _lead(title="Suffix Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    a = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(a) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/1"].ref],
                    alt_urls=["https://ex.invalid/1"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    # A proven-DIFFERENT B now takes the same active name, then is merged away too.
    b = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(b) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/2"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert any(e.endswith(".1.md") for e in merged), merged
    assert v.upsert(b) == "merged_away"


def test_hole_in_the_numeric_sequence_does_not_hide_an_archive(tmp_path):
    """Restoring a note out of `_merged/` -- the documented recovery -- punches a hole. A
    sequential `<stem>.N` walk stops there and misses everything behind it; the listdir
    does not."""
    v = Vault(str(tmp_path))
    third = _lead(title="Hole Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    for n, loc in ((1, LOCATIONS[0]), (2, LOCATIONS[1]), (3, LOCATIONS[2])):
        lead = _lead(title="Y", url=f"https://ex.invalid/{n}", location=loc)
        assert v.upsert(lead) == "created"
        notes = {x.fm.get("url"): x for x in v.read_leads()}
        v.merge_cluster(notes["https://ex.invalid/9"].ref,
                        [notes[f"https://ex.invalid/{n}"].ref],
                        alt_urls=[f"https://ex.invalid/{n}"], first_seen="2026-07-01",
                        last_seen="2026-07-07")
    merged_dir = os.path.join(v.leads_dir, "_merged")
    hole = [e for e in os.listdir(merged_dir) if e.endswith(".1.md")]
    assert hole, os.listdir(merged_dir)
    os.replace(os.path.join(merged_dir, hole[0]), os.path.join(v.leads_dir, "restored.md"))
    # The lead behind the hole must still be suppressed.
    behind = _lead(title="Y", url="https://ex.invalid/3", location=LOCATIONS[2])
    assert v.upsert(behind) == "merged_away"


_LONG = "Y" * 150      # forces the 120-char cap, so `capped` is True


def test_capped_title_probe_advances_on_a_lost_title(tmp_path):
    """PR #48's title_lost, reached through the PROBE. Two different jobs sharing the first
    120 chars of their name: the archived one must not suppress the other."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    other = _lead(title=_LONG + "B", url="", location=LOCATIONS[0])
    assert v.upsert(other) == "created"


def test_capped_title_probe_control_arm_suppresses_a_matching_title(tmp_path):
    """The CONTROL for the test above, and it is load-bearing: asserting only `created`
    there is byte-identical to a probe that never matched anything, so that test alone
    passes under a FULLY INERT probe. Same fixture, matching role -> must hit."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    same = _lead(title=_LONG + "A", url="", location=LOCATIONS[0])
    assert v.upsert(same) == "merged_away"


def test_capped_title_probe_url_match_overrides_a_lost_title(tmp_path):
    """A matching non-empty url is same_opportunity's DEFINITIVE proof, so a drifted title
    tail on a url-stable posting must still be suppressed rather than minted anew."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    drifted = _lead(title=_LONG + "B", url="https://ex.invalid/2", location=LOCATIONS[0])
    assert v.upsert(drifted) == "merged_away"
