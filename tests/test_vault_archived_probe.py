"""#81: the write path must honour a human's merge decision.

A lead archived to `_merged/` by `leads dedupe --merge` must not be re-created when the
dedup set is empty. Fixtures are synthetic: LOCATIONS placeholders, abstract company/role,
`.invalid` urls -- no faker (see tests/test_leads_cluster.py's ruling)."""
import os

import pytest

from sluice.core.leads import Lead
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault, _fm_dict, _split_frontmatter
from sluice.ingest.sink import VaultSink
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
    assert v.upsert(survivor_lead).outcome == "created"
    assert v.upsert(loser_lead).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor, loser = notes[survivor_lead.url], notes[loser_lead.url]
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[loser_lead.url],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    return survivor, loser


def _read_fm(path):
    with open(path, encoding="utf-8") as f:
        return _fm_dict(_split_frontmatter(f.read())[0])


def _seed_legacy(v, filename, *, company, role, location, extra=""):
    """Hand-author an archived note carrying NO record of its seated name. The one fixture
    shape this file cannot build through the production flow -- merge_cluster now always
    stamps that name -- so hand-authoring is what makes the pre-upgrade population testable
    at all. Everything else in this file goes through upsert + merge_cluster."""
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    path = os.path.join(merged_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'---\ncompany: "{company}"\nrole: "{role}"\n'
                f'location: "{location}"\nurl: ""\n{extra}---\n\nbody\n')
    return path


def test_merged_away_lead_is_not_recreated(tmp_path):
    """The acceptance property. Fails on 10b0cdd, where upsert returns 'created'."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    assert len(v.read_leads()) == 1

    # The dedup set is empty (0-byte/tableless seen.db, fresh machine, retargeted SEEN_DB),
    # so the loser is not filtered at ingest and reaches the write path again.
    assert v.upsert(loser).outcome == "merged_away"
    assert len(v.read_leads()) == 1


def test_proven_different_archived_note_does_not_suppress(tmp_path):
    """A genuinely new job at a merged-away name is still created: DIFFERENT advances."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    # Same name as the archived loser, but a token-disjoint location -> DIFFERENT.
    fresh = _lead(title="Widget Engineer Senior", url="", location=LOCATIONS[1])
    assert v.upsert(fresh).outcome == "created"


def test_unknown_verdict_suppresses_as_unproven(tmp_path):
    """A blank location on either side is UNKNOWN: suppress, but on the UNPROVEN arm."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    blank = _lead(title="Widget Engineer Senior", url="", location="")
    assert v.upsert(blank).outcome == "merged_away_unproven"


def test_bare_prefix_would_over_match_a_different_job(tmp_path):
    """Two genuinely different jobs whose names share a prefix; the LONGER is merged away,
    then the SHORTER is scraped. It must be CREATED.

    Read the name as a claim about the FILTER, not about the outcome: a bare `startswith`
    would admit `X - Y II.md` for candidate `X - Y`, but the recorded-name comparison then
    rejects it, so the over-match no longer reaches a suppression. This was written as the
    ANCHOR witness and that reason is now false -- measured, loosening the anchor to
    `startswith` leaves this test GREEN; only removing the anchor AND the recorded-name
    comparison together reddens it. The anchor is retained as a cost filter (one read per
    matching entry instead of per archived entry) and as defence in depth, never as the
    guarantee -- see `_archived_match`'s docstring.

    The case still earns its own test: its verdict is UNKNOWN, so the 'treat DIFFERENT as a
    hit' mutant does not reach it, and no other test here pairs prefix-sharing names.

    The second arm is the CONTROL, and it is what makes the first arm mean anything. On its
    own, `created` is satisfied by a probe that matches NOTHING -- both the anchor and the
    recorded-name comparison reject this fixture independently, so no SINGLE mutation to
    either could redden it, which is the shape a guard test fails open in. Re-scraping the
    archived lead ITSELF must be suppressed, so an inert probe (or a seated-name comparison
    broken toward never matching) reddens this test even though the first arm cannot see
    it."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Anchor Survivor", url="https://ex.invalid/9")
    longer = _lead(title="Y II", url="https://ex.invalid/2", location="")
    _merge_away(v, longer, survivor)
    shorter = _lead(title="Y", url="", location="")
    assert v.upsert(shorter).outcome == "created"
    # CONTROL: same vault, same archive -- the lead that WAS merged away. Its url matches
    # the archived note's, so this is the url-proven arm.
    assert v.upsert(longer).outcome == "merged_away"


def test_loser_archived_under_its_location_suffixed_name_is_found(tmp_path):
    """The probe covers ALL candidates, not just the one the walk stopped at. Candidate 1
    must ALSO be absent -- otherwise the 'probe only where the walk stopped' mutant stays
    green, because the walk itself stops at candidate 2."""
    v = Vault(str(tmp_path))
    third = _lead(title="Third Survivor", url="https://ex.invalid/9")
    assert v.upsert(third).outcome == "created"
    cand1 = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(cand1).outcome == "created"
    # A token-disjoint location at the same name forces the location-suffixed candidate.
    cand2 = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(cand2).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor = notes["https://ex.invalid/9"]
    v.merge_cluster(survivor.ref,
                    [notes["https://ex.invalid/1"].ref, notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/1", "https://ex.invalid/2"],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    assert v.upsert(cand2).outcome == "merged_away"


def test_numeric_suffix_archive_is_found(tmp_path):
    """merge_cluster archives a name-colliding loser as `<stem>.1.md`. Re-upsert B, NOT A:
    A sits at `_merged/<base>.md` and an exact-name probe already catches it, so only B's
    re-upsert exercises the suffix path at all."""
    v = Vault(str(tmp_path))
    third = _lead(title="Suffix Survivor", url="https://ex.invalid/9")
    assert v.upsert(third).outcome == "created"
    a = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(a).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/1"].ref],
                    alt_urls=["https://ex.invalid/1"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    # A proven-DIFFERENT B now takes the same active name, then is merged away too.
    b = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(b).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/2"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert any(e.endswith(".1.md") for e in merged), merged
    assert v.upsert(b).outcome == "merged_away"


def test_hole_in_the_numeric_sequence_does_not_hide_an_archive(tmp_path):
    """Restoring a note out of `_merged/` -- the documented recovery -- punches a hole. A
    sequential `<stem>.N` walk stops there and misses everything behind it; the listdir
    does not."""
    v = Vault(str(tmp_path))
    third = _lead(title="Hole Survivor", url="https://ex.invalid/9")
    assert v.upsert(third).outcome == "created"
    for n, loc in ((1, LOCATIONS[0]), (2, LOCATIONS[1]), (3, LOCATIONS[2])):
        lead = _lead(title="Y", url=f"https://ex.invalid/{n}", location=loc)
        assert v.upsert(lead).outcome == "created"
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
    assert v.upsert(behind).outcome == "merged_away"


_LONG = "Y" * 150      # forces the 120-char cap, so `capped` is True


def test_capped_title_probe_advances_on_a_lost_title(tmp_path):
    """PR #48's title_lost, reached through the PROBE. Two different jobs sharing the first
    120 chars of their name: the archived one must not suppress the other."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    other = _lead(title=_LONG + "B", url="", location=LOCATIONS[0])
    assert v.upsert(other).outcome == "created"


def test_capped_title_probe_control_arm_suppresses_a_matching_title(tmp_path):
    """The CONTROL for the test above, and it is load-bearing: asserting only `created`
    there is byte-identical to a probe that never matched anything, so that test alone
    passes under a FULLY INERT probe. Same fixture, matching role -> must hit.

    UNPROVEN, not proven: this lead carries url:"" against an archived note whose url is
    non-empty, so the match rests on the location overlap alone. What it controls for is
    that the probe MATCHED at all, which either outcome string witnesses equally."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    same = _lead(title=_LONG + "A", url="", location=LOCATIONS[0])
    assert v.upsert(same).outcome == "merged_away_unproven"


def test_capped_title_probe_url_match_overrides_a_lost_title(tmp_path):
    """A matching non-empty url is same_opportunity's DEFINITIVE proof, so a drifted title
    tail on a url-stable posting must still be suppressed rather than minted anew."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    drifted = _lead(title=_LONG + "B", url="https://ex.invalid/2", location=LOCATIONS[0])
    assert v.upsert(drifted).outcome == "merged_away"


def test_merged_away_writes_nothing(tmp_path):
    """The refuse arm's own property, applied to the new arm. Note the assertion does NOT
    cover leads_dir: `_merged/` lives INSIDE it, so leads_dir necessarily exists in any
    scenario that can reach the probe -- asserting its absence would be unsatisfiable
    rather than strict. The archive is hand-seeded precisely so `.stfolder` does NOT
    already exist; built through upsert+merge_cluster, the setup itself creates it and the
    branch-placement mutant becomes invisible.

    Which arm this lands on is incidental to what it pins: both archive outcomes return
    through the ONE shared branch in `upsert` (`if action in (_ARCHIVED,
    _ARCHIVED_UNPROVEN)`), placed ABOVE the makedirs, so either arm witnesses the
    placement. It is the UNPROVEN one here only because the hand-seeded entry -- required
    so `.stfolder` does not already exist -- carries url:"" and so does the lead."""
    v = Vault(str(tmp_path))
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    with open(os.path.join(merged_dir, "X - Y.md"), "w", encoding="utf-8") as f:
        f.write('---\ncompany: "X"\nrole: "Y"\nlocation: "%s"\nurl: ""\n---\n\nbody\n'
                % LOCATIONS[0])
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))

    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "merged_away_unproven"

    assert not os.path.exists(os.path.join(v.leads_dir, "X - Y.md"))
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))


def test_dotted_title_is_not_confused_with_a_collision_suffix(tmp_path):
    """_sanitize maps only `<>:"/\\|?*` and C0 controls -- it does NOT map '.'. So a job
    genuinely titled "Y.1" produces the byte-identical filename SHAPE merge_cluster's own
    numeric collision suffix would produce for a genuine "Y" (`X - Y.1.md` either way),
    and the anchored pattern alone cannot tell them apart. The archived note's OWN
    company/role must disambiguate: a merged-away "Y.1" must never suppress a never-seen
    "Y" that merely shares its location, or a genuinely different job silently vanishes
    into a suppression it never earned. Since #81's review that vanishing is onto the
    UNPROVEN arm (the two carry different urls), so it re-reports rather than entering
    seen.db -- still wrong, still worth its own test, no longer irreversible."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Dot Survivor", url="https://ex.invalid/9")
    dotted = _lead(title="Y.1", url="https://ex.invalid/2")   # location defaults LOCATIONS[0]
    _merge_away(v, dotted, survivor)
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert merged == ["X - Y.1.md"], merged
    fresh = _lead(title="Y", url="")   # location defaults LOCATIONS[0] -- same as dotted's
    assert v.upsert(fresh).outcome == "created"


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
    assert v.upsert(survivor).outcome == "created"

    block1 = _lead(title=_LONG + "BLOCK1", url="https://ex.invalid/91", location="")
    assert v.upsert(block1).outcome == "created"
    block2a = _lead(title=_LONG + "BLOCK2A", url="https://ex.invalid/92", location=LOCATIONS[0])
    assert v.upsert(block2a).outcome == "created"
    block2b = _lead(title=_LONG + "BLOCK2B", url="https://ex.invalid/93", location=LOCATIONS[1])
    assert v.upsert(block2b).outcome == "created"

    l1 = _lead(title=_LONG + "TARGET", url="https://ex.invalid/94", location=LOCATIONS[0])
    assert v.upsert(l1).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/94"].ref],
                    alt_urls=["https://ex.invalid/94"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    l2 = _lead(title=_LONG + "TARGET", url="https://ex.invalid/95", location=LOCATIONS[1])
    assert v.upsert(l2).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/95"].ref],
                    alt_urls=["https://ex.invalid/95"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    plain = [e for e in merged if not e.endswith(".1.md")]
    suffixed = [e for e in merged if e.endswith(".1.md")]
    assert len(plain) == 1 and len(suffixed) == 1, merged
    assert suffixed[0][:-len(".1.md")] == plain[0][:-len(".md")], merged   # genuine collision
    # SCOPE, not just shape: pin that the collision landed on the DIGEST-suffixed
    # candidate (index 2), not on candidate 1. Without this the test asserts only "a
    # collision happened somewhere" and would stay green -- testing nothing this file
    # does not already test -- if _CHAR_CAP or _note_name shifted and the fixture
    # quietly stopped reaching candidate 3 at all (which also makes names[2] IndexError).
    cands, capped = v._candidate_names("X", _LONG + "TARGET", LOCATIONS[1])
    assert capped and plain[0][:-len(".md")] == cands[2] != cands[0], (merged, cands)

    # Re-upsert L2, NOT L1: L1 sits at the EXACT (unsuffixed) archive name, which an
    # exact-name probe already catches with no disambiguation involved -- only L2's
    # re-upsert exercises the suffix-disambiguation path at all (test_numeric_suffix_
    # archive_is_found's own control, applied here).
    again = _lead(title=_LONG + "TARGET", url="https://ex.invalid/95", location=LOCATIONS[1])
    assert v.upsert(again).outcome == "merged_away"


def test_numeric_suffix_collision_on_location_suffixed_name_is_found(tmp_path):
    """The location-suffixed sibling of the digest case above: a genuine `.N` collision on
    candidate 2, not candidate 1 or 3. L1 and L2 share ONE location (so their candidate-2
    name coincides) but different full titles beyond the capped prefix, so `title_lost` --
    not location -- is what proves them different from each other and from the blocker."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Location Collision Survivor", url="https://ex.invalid/9")
    assert v.upsert(survivor).outcome == "created"

    block1 = _lead(title=_LONG + "BLOCK", url="https://ex.invalid/91", location="")
    assert v.upsert(block1).outcome == "created"

    l1 = _lead(title=_LONG + "P", url="https://ex.invalid/92", location=LOCATIONS[0])
    assert v.upsert(l1).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/92"].ref],
                    alt_urls=["https://ex.invalid/92"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    l2 = _lead(title=_LONG + "Q", url="https://ex.invalid/93", location=LOCATIONS[0])
    assert v.upsert(l2).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/93"].ref],
                    alt_urls=["https://ex.invalid/93"], first_seen="2026-07-01",
                    last_seen="2026-07-07")

    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    plain = [e for e in merged if not e.endswith(".1.md")]
    suffixed = [e for e in merged if e.endswith(".1.md")]
    assert len(plain) == 1 and len(suffixed) == 1, merged
    assert suffixed[0][:-len(".1.md")] == plain[0][:-len(".md")], merged   # genuine collision
    # SCOPE (see the digest sibling above): pin candidate 2, the LOCATION-suffixed name.
    cands, capped = v._candidate_names("X", _LONG + "Q", LOCATIONS[0])
    assert capped and plain[0][:-len(".md")] == cands[1] != cands[0], (merged, cands)

    again = _lead(title=_LONG + "Q", url="https://ex.invalid/93", location=LOCATIONS[0])
    assert v.upsert(again).outcome == "merged_away"


def _collide(v, survivor_url="https://ex.invalid/9", title="Y", locs=(0, 1)):
    """Build a GENUINE `.N` collision in `_merged/` through the real production flow: two
    proven-different jobs take the same active name in turn and are each merged away, so
    the second archive lands on merge_cluster's own collision counter. Returns the second
    job's Lead -- the only one whose re-upsert exercises the counter at all, since the
    first sits at the exact, unsuffixed archive name."""
    a = _lead(title=title, url="https://ex.invalid/1", location=LOCATIONS[locs[0]])
    assert v.upsert(a).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes[survivor_url].ref, [notes["https://ex.invalid/1"].ref],
                    alt_urls=["https://ex.invalid/1"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    b = _lead(title=title, url="https://ex.invalid/2", location=LOCATIONS[locs[1]])
    assert v.upsert(b).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes[survivor_url].ref, [notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/2"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    return b


def test_collision_file_does_not_suppress_a_job_genuinely_named_like_it(tmp_path):
    """The MIRROR of test_dotted_title_...: a collision counter merge_cluster appended to
    job `Y` produces `_merged/X - Y.1.md`, which is byte-identical to what an archive of a
    job genuinely titled `Y.1` would be called. A never-seen `Y.1` therefore probes with
    candidate `X - Y.1` and matches that file EXACTLY -- no numeric suffix involved, so no
    amount of `.N` grammar can catch it -- and its verdict is SAME (shared location), a
    suppression the job never earned. The archived note records the name it was SEATED at
    (`X - Y`), which does not equal the candidate, so it does not match."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(title="Mirror Survivor", url="https://ex.invalid/9")).outcome == "created"
    _collide(v)
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert merged == ["X - Y.1.md", "X - Y.md"], merged   # the ambiguous filename, for real

    dotted = _lead(title="Y.1", url="", location=LOCATIONS[1])   # LOCATIONS[1] -> SAME
    assert v.upsert(dotted).outcome == "created"
    assert os.path.exists(os.path.join(v.leads_dir, "X - Y.1.md"))


def test_quote_edged_component_still_suppresses(tmp_path):
    """`_sanitize` maps `"` to `-` but leaves `'` alone, while `_fm_dict` ends in
    `.strip('"').strip("'")` -- so a title whose edge character is an apostrophe round-trips
    through frontmatter SHORTENED. Re-deriving the archived note's name from its own
    company/role therefore produced a name it was never seated at, and the archive stopped
    suppressing: a resurrection caused by punctuation. Reading the recorded name instead
    has nothing to re-derive."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(title="Quote Survivor", url="https://ex.invalid/9")).outcome == "created"
    b = _collide(v, title="Y'")
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert merged == ["X - Y'.1.md", "X - Y'.md"], merged
    # The lossy round trip, asserted directly so the fixture cannot stop exercising it.
    archived = _read_fm(os.path.join(v.leads_dir, "_merged", "X - Y'.1.md"))
    assert archived["role"] == "Y" != "Y'"

    assert v.upsert(b).outcome == "merged_away"


def test_post_archive_edit_of_role_still_suppresses(tmp_path):
    """A human correcting an archived note's `role` in Obsidian is the #16 threat model, and
    it silently invalidated every re-derivation of that note's filename -- the note keeps
    the name it was archived under, but its frontmatter no longer produces that name. The
    recorded field is a fact about the past, so an edit to `role` cannot move it."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(title="Edit Survivor", url="https://ex.invalid/9")).outcome == "created"
    b = _collide(v)
    archived = os.path.join(v.leads_dir, "_merged", "X - Y.1.md")
    assert v.update_fields(archived, {"role": "Y, Revised By Hand"})
    assert _read_fm(archived)["role"] == "Y, Revised By Hand"

    assert v.upsert(b).outcome == "merged_away"


def test_legacy_archive_without_the_field_suppresses_by_exact_name(tmp_path):
    """An archive written before the field shipped carries no record of its seated name.
    Its filename still proves ONE thing unaided -- an exact name with no collision counter
    -- and that much must keep working, or shipping this would resurrect every lead a human
    merged away before the upgrade. Hand-seeded because the current code cannot produce an
    entry without the field.

    On the UNPROVEN arm: a legacy entry predates the stamp and this one carries url:"",
    so nothing url-proves the match. Suppression is what the legacy arm owes; recording is
    not, and a pre-upgrade archive is exactly where the evidence is thinnest."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.md", company="X", role="Y", location=LOCATIONS[0])
    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "merged_away_unproven"


def test_legacy_numeric_suffix_archive_is_not_matched(tmp_path):
    """The accepted cost of the legacy arm, pinned rather than left incidental. A legacy
    `X - Y.1.md` is either a collision counter on `X - Y` or an archive of a job genuinely
    titled `Y.1`, and nothing on disk distinguishes them -- so a candidate `X - Y` does NOT
    match it. That MISSES a pre-existing collision and creates a duplicate note, which is
    visible and a human can merge it again; the other direction would suppress a real job
    invisibly, on the arm the sink records irreversibly."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.1.md", company="X", role="Y", location=LOCATIONS[0])
    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "created"
    assert os.path.exists(os.path.join(v.leads_dir, "X - Y.md"))          # the visible duplicate
    assert os.path.exists(os.path.join(v.leads_dir, "_merged", "X - Y.1.md"))   # archive intact


def test_unreadable_recorded_name_degrades_to_the_legacy_arm(tmp_path):
    """A hand edit that breaks the recorded value must fall back to the legacy rule, not be
    parsed leniently: a lenient read of `X - Y` would match candidate `X - Y` and suppress,
    which is the direction that cannot be undone. Same entry, same everything, and the
    answer must be the same as the no-field case above."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.1.md", company="X", role="Y", location=LOCATIONS[0],
                 extra="archived_from_note: X - Y\n")   # unquoted -> not the recorded form
    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "created"


def test_a_failed_stamp_does_not_un_count_a_real_archive(tmp_path, monkeypatch):
    """The stamp is best effort and runs AFTER the loser is counted, because the move has
    already happened by then: reporting a genuinely archived loser as un-archived would
    have the caller treat a note that is no longer in the active view as still needing a
    merge. So a stamp failure must be swallowed, and the entry simply degrades to a LEGACY
    archive -- no recorded name -- which still suppresses by exact filename."""
    import sluice.core.vault as vault_mod
    real_replace = vault_mod.os.replace

    def flaky(src, dst):
        # Fail ONLY the stamp: it replaces a `.sluice-*` temp sibling INTO _merged/. The
        # archive move replaces the loser's own active note (not a temp), and the
        # survivor's CAS write targets a path outside _merged/ -- both must run for real.
        if "_merged" in dst and os.path.basename(src).startswith(".sluice-"):
            raise OSError("simulated: could not record the archived name")
        return real_replace(src, dst)

    v = Vault(str(tmp_path))
    survivor = _lead(title="Stamp Survivor", url="https://ex.invalid/1")
    loser = _lead(title="Stamp Loser", url="https://ex.invalid/2")
    assert v.upsert(survivor).outcome == "created"
    assert v.upsert(loser).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    monkeypatch.setattr(vault_mod.os, "replace", flaky)
    archived = v.merge_cluster(notes["https://ex.invalid/1"].ref,
                               [notes["https://ex.invalid/2"].ref],
                               alt_urls=["https://ex.invalid/2"],
                               first_seen="2026-07-01", last_seen="2026-07-07")
    monkeypatch.undo()

    assert len(archived) == 1                     # still counted: the move really happened
    assert len(v.read_leads()) == 1               # ...and the loser really is out of view
    assert "archived_from_note" not in _read_fm(archived[0])   # the stamp genuinely failed
    assert v.upsert(loser).outcome == "merged_away"       # legacy arm: exact filename still suppresses


def test_zero_byte_reservation_is_skipped_not_treated_as_unknown(tmp_path):
    """merge_cluster's O_EXCL reservation leaves a 0-byte file under a real lead's archived
    name if the process dies before os.replace, and its cleanup is best-effort. Scored as
    UNKNOWN it would suppress every future lead at that name."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(title="Y", url="https://ex.invalid/1")).outcome == "created"
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    open(os.path.join(merged_dir, "X - Y.md"), "w").close()   # the orphaned reservation
    # location=LOCATIONS[1] makes fresh a DIFFERENT lead from the active "Y" note (whose
    # location is the default), so this must create a second note -- accepting "updated"
    # or "merged" would also pass a location-reconciliation regression.
    fresh = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(fresh).outcome == "created"


def test_unreadable_archived_entry_raises_rather_than_silently_resurrecting(tmp_path):
    """`except OSError: continue` here -- read_leads' shape -- would turn a permissions
    error into a resurrection. It must propagate so the sink counts the lead `skipped`
    and keeps it OUT of seen.db for a retry."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    merged_dir = os.path.join(v.leads_dir, "_merged")
    archived = os.path.join(merged_dir, os.listdir(merged_dir)[0])
    os.chmod(archived, 0o000)
    try:
        if os.access(archived, os.R_OK):        # root ignores the mode bits
            pytest.skip("running as root: cannot make a file unreadable")
        with pytest.raises(OSError):
            v.upsert(loser)
    finally:
        os.chmod(archived, 0o600)


def test_blank_url_and_location_archive_is_still_recognized_as_a_note(tmp_path):
    """The skip guard must key on company/role, never on url/location: same_opportunity's
    own docstring records that a REAL note -- a google lead -- can carry url:"" AND a
    blank location at once, which is exactly the UNKNOWN case this probe exists to
    suppress. A guard keyed on url/location instead would test the same fields the
    VERDICT consumes, collapsing "is this a note at all?" into "what does it say?", and
    would skip this legitimate archived loser as if it were the 0-byte reservation --
    resurrecting it. Built through the real upsert + merge_cluster flow: nothing in the
    production write path requires a non-blank url or location, so this fixture needs no
    hand-seeding."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Blank Evidence Survivor", url="https://ex.invalid/9")
    loser = _lead(title="Blank Evidence Loser", url="", location="")
    _merge_away(v, loser, survivor)
    again = _lead(title="Blank Evidence Loser", url="", location="")
    assert v.upsert(again).outcome == "merged_away_unproven"


def test_company_only_archived_entry_is_still_a_note(tmp_path):
    """The "is this a note at all" predicate skips on NEITHER company nor role, never on
    EITHER. A reviewer proposed the either-form; it moves the wrong way, because skipping
    more often means suppressing less often. Here `role` is blank -- what a hand edit in
    Obsidian leaves behind (the #16 threat model) -- and the entry must still be treated as
    a note and still suppress, rather than being mistaken for merge_cluster's 0-byte O_EXCL
    reservation and skipped, which would resurrect the lead."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.md", company="X", role="", location=LOCATIONS[0])
    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "merged_away_unproven"


def test_role_only_archived_entry_is_still_a_note(tmp_path):
    """The mirror of the sibling above: `company` blanked, `role` intact. Both halves are
    pinned so the predicate cannot be flipped to the either-form without a test going red;
    a single half would leave one direction of the flip invisible."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.md", company="", role="Y", location=LOCATIONS[0])
    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])).outcome == "merged_away_unproven"


def test_legacy_wrong_hit_is_suppressed_only_on_the_unproven_arm(tmp_path):
    """The legacy arm's accepted WRONG HIT, and the bound the url-proof gate puts on it. A
    legacy `X - Y.1.md` archiving a job whose title genuinely ends in `.1` is matched by a
    candidate `X - Y.1` -- correct here -- but the SAME file shape also arises as a
    collision counter on a plain `Y`, and a legacy entry carries nothing to tell them apart.
    So a never-seen `Y.1` can be suppressed by an archive of a DIFFERENT job.

    That is the cost `_archived_match`'s docstring accepts. What bounds it: the entry
    archives a different job, so its url cannot be the never-seen lead's, the match is not
    url-proven, and the outcome is the UNPROVEN arm -- out of seen.db, re-reported every run
    until a human acts. Before the url-proof gate this same fixture returned `merged_away`
    and entered the dedup store permanently."""
    v = Vault(str(tmp_path))
    _seed_legacy(v, "X - Y.1.md", company="X", role="Y",
                 location=LOCATIONS[0], extra='url: "https://ex.invalid/archived"\n')
    fresh = _lead(title="Y.1", url="https://ex.invalid/brand-new", location=LOCATIONS[0])
    assert v.upsert(fresh).outcome == "merged_away_unproven"


def test_location_only_same_is_unproven_and_stays_out_of_seen_db(tmp_path):
    """THE url-proof gate, end to end through the real sink and a real seen.db.

    `same_opportunity` returns SAME from a matching url OR from a location-token overlap,
    and only the first is identity. A genuinely new requisition -- same company, same title,
    same location, BRAND-NEW url: a re-post, or a second headcount -- takes the second path.
    Recording it would suppress a real job permanently and invisibly, since seen.db has
    load/save only and no removal path, and no note exists anywhere to reverse it from. So
    it must land on the UNPROVEN arm and stay OUT of the dedup store, re-reporting every run.

    Deleting `and url_proven` from `_archived_match` reddens this test."""
    v = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    survivor = _lead(title="Repost Survivor", url="https://ex.invalid/9")
    loser = _lead(title="Repost Target", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)

    repost = _lead(title="Repost Target", url="https://ex.invalid/3", location=LOCATIONS[0])
    assert v.upsert(repost).outcome == "merged_away_unproven"

    counts = VaultSink(v, seen, today=lambda: "2026-07-31").write([repost])
    assert counts.get("merged_away_unproven") == 1 and not counts.get("created")
    assert repost.dedup_key not in seen.load()   # re-surfaces next run
    assert len(v.read_leads()) == 1              # and nothing was written


def test_url_proven_same_is_recorded_in_seen_db(tmp_path):
    """The CONTROL for the gate above, and it is load-bearing: asserting only that the
    re-post stays out of seen.db is satisfied by a probe that records NOTHING ever, so that
    test alone passes under a gate stuck closed. Same vault, same archive, same everything
    -- except the incoming lead carries the archived note's OWN url, which PROVES identity.
    That one must be recorded, so the suppression self-heals the dedup set and happens once
    rather than on every run."""
    v = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    survivor = _lead(title="Repost Survivor", url="https://ex.invalid/9")
    loser = _lead(title="Repost Target", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)

    again = _lead(title="Repost Target", url="https://ex.invalid/2", location=LOCATIONS[0])
    assert v.upsert(again).outcome == "merged_away"

    counts = VaultSink(v, seen, today=lambda: "2026-07-31").write([again])
    assert counts.get("merged_away") == 1
    assert again.dedup_key in seen.load()
    assert len(v.read_leads()) == 1


def test_probe_is_a_no_op_when_nothing_was_ever_merged(tmp_path):
    """_merged/ is created lazily, so on any install that has never merged it does not
    exist. That is the common case and means 'no hit' -- FileNotFoundError specifically,
    never a bare `except OSError` that would also swallow an unreadable directory."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()).outcome == "created"
    assert not os.path.exists(os.path.join(v.leads_dir, "_merged"))
    assert v.upsert(_lead(url="https://ex.invalid/2", location=LOCATIONS[1])).outcome == "created"
