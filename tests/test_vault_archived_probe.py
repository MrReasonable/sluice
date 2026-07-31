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


def test_merged_away_writes_nothing(tmp_path):
    """The refuse arm's own property, applied to the new arm. Note the assertion does NOT
    cover leads_dir: `_merged/` lives INSIDE it, so leads_dir necessarily exists in any
    scenario that can reach the probe -- asserting its absence would be unsatisfiable
    rather than strict. The archive is hand-seeded precisely so `.stfolder` does NOT
    already exist; built through upsert+merge_cluster, the setup itself creates it and the
    branch-placement mutant becomes invisible."""
    v = Vault(str(tmp_path))
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    with open(os.path.join(merged_dir, "X - Y.md"), "w", encoding="utf-8") as f:
        f.write('---\ncompany: "X"\nrole: "Y"\nlocation: "%s"\nurl: ""\n---\n\nbody\n'
                % LOCATIONS[0])
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))

    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])) == "merged_away"

    assert not os.path.exists(os.path.join(v.leads_dir, "X - Y.md"))
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))


def test_dotted_title_is_not_confused_with_a_collision_suffix(tmp_path):
    """_sanitize maps only `<>:"/\\|?*` and C0 controls -- it does NOT map '.'. So a job
    genuinely titled "Y.1" produces the byte-identical filename SHAPE merge_cluster's own
    numeric collision suffix would produce for a genuine "Y" (`X - Y.1.md` either way),
    and the anchored pattern alone cannot tell them apart. The archived note's OWN
    company/role must disambiguate: a merged-away "Y.1" must never suppress a never-seen
    "Y" that merely shares its location, or a genuinely different job silently vanishes
    into the PROVEN arm (same_opportunity's location match) and can never be created."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Dot Survivor", url="https://ex.invalid/9")
    dotted = _lead(title="Y.1", url="https://ex.invalid/2")   # location defaults LOCATIONS[0]
    _merge_away(v, dotted, survivor)
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert merged == ["X - Y.1.md"], merged
    fresh = _lead(title="Y", url="")   # location defaults LOCATIONS[0] -- same as dotted's
    assert v.upsert(fresh) == "created"


def test_numeric_suffix_collision_on_digest_suffixed_name_is_found(tmp_path):
    """Round 3: the disambiguation must compare against ALL of the archived note's OWN
    candidate forms, not just the bare one -- a genuine `.N` collision can land on the
    DIGEST-suffixed name too. Built the way the round-3 reviewer built it: through real
    upsert + merge_cluster, no hand-authored frontmatter for the load-bearing entries.

    `block1` occupies the shared bare name so L1/L2 advance past it (title_lost, since
    all three share the same 120-char-capped prefix but diverge beyond it); `block2a`/
    `block2b` occupy L1's and L2's own location-suffixed names for the same reason, so
    both L1 and L2 fall all the way through to the DIGEST candidate. L1 and L2 carry the
    EXACT same (long) title -- same digest, same candidate-3 name -- but different
    locations, so each is PROVEN DIFFERENT from the other's archive and independently
    creates at that shared name, producing a genuine O_EXCL collision when both are later
    merged away."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Digest Collision Survivor", url="https://ex.invalid/9")
    assert v.upsert(survivor) == "created"

    block1 = _lead(title=_LONG + "BLOCK1", url="https://ex.invalid/91", location="")
    assert v.upsert(block1) == "created"
    block2a = _lead(title=_LONG + "BLOCK2A", url="https://ex.invalid/92", location=LOCATIONS[0])
    assert v.upsert(block2a) == "created"
    block2b = _lead(title=_LONG + "BLOCK2B", url="https://ex.invalid/93", location=LOCATIONS[1])
    assert v.upsert(block2b) == "created"

    l1 = _lead(title=_LONG + "TARGET", url="https://ex.invalid/94", location=LOCATIONS[0])
    assert v.upsert(l1) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/94"].ref],
                    alt_urls=["https://ex.invalid/94"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    l2 = _lead(title=_LONG + "TARGET", url="https://ex.invalid/95", location=LOCATIONS[1])
    assert v.upsert(l2) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/95"].ref],
                    alt_urls=["https://ex.invalid/95"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    plain = [e for e in merged if not e.endswith(".1.md")]
    suffixed = [e for e in merged if e.endswith(".1.md")]
    assert len(plain) == 1 and len(suffixed) == 1, merged
    assert suffixed[0][:-len(".1.md")] == plain[0][:-len(".md")], merged   # genuine collision

    # Re-upsert L2, NOT L1: L1 sits at the EXACT (unsuffixed) archive name, which an
    # exact-name probe already catches with no disambiguation involved -- only L2's
    # re-upsert exercises the suffix-disambiguation path at all (test_numeric_suffix_
    # archive_is_found's own control, applied here).
    again = _lead(title=_LONG + "TARGET", url="https://ex.invalid/95", location=LOCATIONS[1])
    assert v.upsert(again) == "merged_away"


def test_numeric_suffix_collision_on_location_suffixed_name_is_found(tmp_path):
    """The location-suffixed sibling of the digest case above: a genuine `.N` collision on
    candidate 2, not candidate 1 or 3. L1 and L2 share ONE location (so their candidate-2
    name coincides) but different full titles beyond the capped prefix, so `title_lost` --
    not location -- is what proves them different from each other and from the blocker."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Location Collision Survivor", url="https://ex.invalid/9")
    assert v.upsert(survivor) == "created"

    block1 = _lead(title=_LONG + "BLOCK", url="https://ex.invalid/91", location="")
    assert v.upsert(block1) == "created"

    l1 = _lead(title=_LONG + "P", url="https://ex.invalid/92", location=LOCATIONS[0])
    assert v.upsert(l1) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/92"].ref],
                    alt_urls=["https://ex.invalid/92"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    l2 = _lead(title=_LONG + "Q", url="https://ex.invalid/93", location=LOCATIONS[0])
    assert v.upsert(l2) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/93"].ref],
                    alt_urls=["https://ex.invalid/93"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    plain = [e for e in merged if not e.endswith(".1.md")]
    suffixed = [e for e in merged if e.endswith(".1.md")]
    assert len(plain) == 1 and len(suffixed) == 1, merged
    assert suffixed[0][:-len(".1.md")] == plain[0][:-len(".md")], merged   # genuine collision

    again = _lead(title=_LONG + "Q", url="https://ex.invalid/93", location=LOCATIONS[0])
    assert v.upsert(again) == "merged_away"
