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
    survivor, loser = notes[0], notes[1]
    archived = v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                               first_seen="2026-07-07", last_seen="2026-07-07")
    assert len(archived) == 1
    assert _MERGED_SUBDIR in archived[0]

    fresh = Vault(str(tmp_path))
    assert [n.slug for n in fresh.read_leads()] == [survivor.slug]
    assert loser.slug not in {n.slug for n in fresh.read_leads()}
