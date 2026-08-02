"""The write path across a subfoldered lead store. A lead's identity is its note NAME, so a
note found in any scanned directory is reconciled in place -- never re-created."""
from sluice.core.leads import Lead
from sluice.core.vault import _MERGED_SUBDIR, Vault
from tests.conftest import LOCATIONS


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _lead(**kw):
    base = dict(
        source="cord", search="Analyst", title="Analyst", company="Acme",
        url="https://ex.invalid/1", location=LOCATIONS[0], salary="",
        job_type="permanent", first_seen="2026-07-07", last_seen="2026-07-07",
    )
    base.update(kw)
    return Lead(**base)


def _two_note_vault(tmp_path):
    """Two on-disk notes at token-DISJOINT locations, so same_opportunity proves them
    DIFFERENT and upsert really seats two notes (an empty location is UNKNOWN evidence and
    would silently merge the second into the first, leaving nothing to merge later)."""
    v = Vault(str(tmp_path))
    v.upsert(_lead(location=LOCATIONS[0], url="https://ex.invalid/1"))
    v.upsert(_lead(location=LOCATIONS[1], url="https://ex.invalid/2"))
    return v


def test_a_merged_away_loser_stays_invisible_to_the_recursive_scan(tmp_path):
    """THE #1/#81 regression. merge_cluster archives the loser under `_merged/`, which the
    old flat os.listdir skipped only because it is a directory. A recursive walk that did
    not prune it by name would return the loser as an active lead again -- and a lead a
    human merged away, re-created and re-applied to, is a second application under their
    name. No test on main could catch this: the walk could not reach an archived note."""
    v = _two_note_vault(tmp_path)
    notes = v.read_leads()
    assert len(notes) == 2
    # Keyed on url, exactly as the `_still_suppressed` sibling below is and for the same
    # reason: read_leads sorts by full path and the location-suffixed note sorts FIRST
    # (" " is 0x20, "." is 0x2e), so `notes[0], notes[1]` names the two the wrong way round.
    # Harmless in this test -- both assertions hold whichever note is merged away -- but a
    # labelling the sibling documents as a trap must not read as safe practice here.
    by_url = {n.fm.get("url"): n for n in notes}
    survivor, loser = by_url["https://ex.invalid/1"], by_url["https://ex.invalid/2"]
    archived = v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                               first_seen="2026-07-07", last_seen="2026-07-07")
    assert len(archived) == 1
    assert _MERGED_SUBDIR in archived[0]

    fresh = Vault(str(tmp_path))
    assert [n.slug for n in fresh.read_leads()] == [survivor.slug]
    assert loser.slug not in {n.slug for n in fresh.read_leads()}


def _seed_one(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    return _leads_dir(tmp_path) / "Acme - Analyst.md"


def test_a_note_moved_to_a_subfolder_is_updated_not_recreated(tmp_path):
    """Identity is the note NAME, not its path. This is what lets `leads reconcile` (PR B)
    and #81's documented recovery move a note without the next scrape duplicating it."""
    note = _seed_one(tmp_path)
    archive = _leads_dir(tmp_path) / "Archive"
    archive.mkdir()
    moved = archive / note.name
    note.rename(moved)

    v = Vault(str(tmp_path))                      # fresh: the scan-set cache is per instance
    assert v.upsert(_lead(last_seen="2026-07-08")) == "updated"
    assert "last_seen: 2026-07-08" in moved.read_text()
    assert not note.exists()                      # nothing re-created at the flat name


def test_a_note_filed_into_a_new_subfolder_mid_run_is_not_duplicated(tmp_path):
    """The scan-set cache's staleness window, closed on the CREATE arm.

    Measured before the fix: run 1's first lead is `created`, the human moves that note into
    a new subfolder, run 1's next scrape of the SAME lead is `created` again -- sluice's own
    duplicate, at the root name. It does not self-heal the way the create-race does: from the
    next run on both twins are visible, the candidate resolves to two notes and every later
    upsert REFUSES, with last_seen frozen (which, with `lead_ttl_days` set, then ages the lead
    into the stale set and offers a twin for dismissal).

    Witnessed by DELETING the re-derive in `_resolve_path` -- without it this reports
    `created` and leaves two notes on disk.
    """
    # The mkdir is load-bearing, not tidiness. _scan_dirs deliberately does NOT cache
    # "leads_dir is missing", so a vault whose first upsert also creates the directory never
    # has a stale cache to be wedged by, and this test would pass with the fix deleted.
    _leads_dir(tmp_path).mkdir(parents=True)
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"          # warms the cache at [leads_dir]
    note = _leads_dir(tmp_path) / "Acme - Analyst.md"
    active = _leads_dir(tmp_path) / "Active"
    active.mkdir()
    moved = active / note.name
    note.rename(moved)                             # the human, mid-run

    # The SAME instance, never a fresh one: a fresh Vault re-walks on its first lookup, so
    # the assertion below would hold with the fix deleted.
    assert v.upsert(_lead(last_seen="2026-07-08")) == "updated"
    assert not note.exists()                       # no twin minted at the root name
    assert "last_seen: 2026-07-08" in moved.read_text()


def test_the_create_arm_re_derives_the_scan_set_once_per_create_not_per_lead(tmp_path):
    """The cost the cache exists to avoid must not come back by the side door. An UPDATE
    already identified a real note, so a fresher directory list cannot change its answer --
    and paying a walk per lead there is precisely the per-lead walk this cache replaces."""
    _leads_dir(tmp_path).mkdir(parents=True)
    v = Vault(str(tmp_path))
    walks = [0]
    inner = v._walk

    def counting_walk():
        walks[0] += 1
        return inner()
    v._walk = counting_walk

    assert v.upsert(_lead()) == "created"
    after_create = walks[0]
    assert after_create == 2, "one walk to fill the cache, one to re-derive before the create"
    for day in ("2026-07-08", "2026-07-09", "2026-07-10"):
        assert v.upsert(_lead(last_seen=day)) == "updated"
    assert walks[0] == after_create, "an update must not re-walk"


def test_a_candidate_resolving_to_two_notes_refuses_and_writes_nothing(tmp_path):
    """Two notes claim one identity, so the store cannot know which lead this is. Bumping
    the wrong one leaves the other to rot silently, so it writes nothing and the sink keeps
    the lead out of seen.db to re-report next run."""
    note = _seed_one(tmp_path)
    archive = _leads_dir(tmp_path) / "Archive"
    archive.mkdir()
    twin = archive / note.name
    twin.write_text(note.read_text())             # a hand-made copy

    v = Vault(str(tmp_path))
    assert v.upsert(_lead(last_seen="2026-07-08")) == "refused"
    assert "last_seen: 2026-07-07" in note.read_text()
    assert "last_seen: 2026-07-07" in twin.read_text()


def test_a_new_lead_is_still_created_at_the_leads_dir_root(tmp_path):
    """PR A creates no folders and moves no notes: the write folder is unchanged. PR B is
    what points creates at Active/."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    assert (_leads_dir(tmp_path) / "Acme - Analyst.md").exists()


def test_a_merged_away_lead_is_still_suppressed_when_a_subfolder_exists(tmp_path):
    """#81 through the new lookup: the archive probe runs when the candidate resolves
    NOWHERE in the scan set, which is the same condition as before, not a weaker one."""
    v = _two_note_vault(tmp_path)
    # Keyed on url, never on read_leads' ORDER. read_leads sorts by full path and the
    # location-suffixed note sorts FIRST (" " < "."), so indexing merges away the note the
    # re-scrape below rebuilds -- and the assertion then passes on the wrong lead.
    by_url = {n.fm.get("url"): n for n in v.read_leads()}
    survivor, loser = by_url["https://ex.invalid/1"], by_url["https://ex.invalid/2"]
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                    first_seen="2026-07-07", last_seen="2026-07-07")
    archive = _leads_dir(tmp_path) / "Archive"
    archive.mkdir()
    # POPULATED, not merely created. An empty directory is inert here -- delete the mkdir
    # and no outcome moves -- so "when a subfolder exists" would promise a condition the
    # test never exercises. An unrelated lead note gives the scan set a genuinely second
    # populated directory for the candidate walk to stat through before it concludes the
    # candidate resolves NOWHERE and falls to the archive probe.
    (archive / "Foo - Engineer.md").write_text(
        "---\ncompany: Foo\nrole: Engineer\nstatus: new\nurl: https://ex.invalid/9\n---\n")

    fresh = Vault(str(tmp_path))
    # SCOPE, not violations: pin that the subfolder note is really in the scan set. Without
    # it the fixture above could stop being reached (a typo'd path, a pruned directory) and
    # this test would quietly go back to asserting about an empty folder.
    assert "Foo - Engineer" in {n.slug for n in fresh.read_leads()}
    loser_lead = _lead(location=LOCATIONS[1], url="https://ex.invalid/2")
    # `== "merged_away"`, never a disjunction over both arms. The outcome here is
    # DETERMINISTIC: both sides carry the same non-empty url, so the url-proof gate is
    # satisfied. Accepting either arm would leave free the one distinction this whole
    # test exists for -- `merged_away` enters seen.db, which has no removal path, and
    # `merged_away_unproven` must never be recorded.
    assert fresh.upsert(loser_lead) == "merged_away"
