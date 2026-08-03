"""The SCAN SET and the WRITE FOLDER are two concepts one field used to conflate (#1).

The scan set is every directory a lead may be READ from; the write folder is the ONE directory a
new note is CREATED in. Separating them is the whole of the layout design.
"""
import os

from sluice.core.leads import ACTIVE_SUBDIR, Lead, layout_subfolder
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1",
          location=""):
    # `search` is REQUIRED (core/leads.py: source, search, title). Shipped helper form is
    # tests/test_leads_expire.py.
    return Lead(source="test", search="q", title=title, company=company, url=url,
                location=location)


def test_the_flat_layout_writes_into_the_leads_dir(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    assert os.path.isfile(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"))


def test_the_active_archive_layout_writes_into_active(tmp_path):
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    assert os.path.isfile(
        os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md"))
    assert not os.path.exists(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"))


def test_a_created_note_is_already_reconciled(tmp_path):
    """`_write_folder` resolves through `layout_subfolder("new", ...)` rather than naming Active/
    directly, so a created note is BY CONSTRUCTION already in the folder its status implies --
    `leads reconcile` has nothing to do with a note ingest just made. A hardcoded Active/ would
    drift the moment the map changed."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    v.upsert(_lead())
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    note = [n for n in v.read_leads() if n.ref == ref][0]
    assert os.path.basename(os.path.dirname(note.ref)) == layout_subfolder(
        note.status, "active_archive")


def test_a_rescrape_updates_the_note_in_active_rather_than_recreating_it(tmp_path):
    """The identity rule: a lead's identity is its note NAME, not its folder. A second scrape must
    find the note in Active/ through the scan set and bump last_seen, not mint a twin at the root.
    This is the regression that would mass-duplicate an opted-in vault."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    assert v.upsert(_lead()) == "updated"
    found = [f for _, _, fs in os.walk(v.leads_dir) for f in fs if f.endswith(".md")]
    assert len(found) == 1, found


def test_a_note_already_at_the_root_is_updated_not_duplicated_into_active(tmp_path):
    """Opting IN on an existing flat vault must not re-create every lead, and must not leave an
    empty Active/ behind either. The scan set covers the root, so the candidate resolves there and
    the note is updated where it sits; moving it is `leads reconcile`'s job, not ingest's
    (decision 2).

    The `not os.path.exists(Active/)` line is the one that catches a write-folder makedirs hoisted
    above the update/merge dispatch -- `upsert`'s leads_dir makedirs sits there and runs on every
    non-refused outcome, so a naive repointing mints Active/ on a pure last_seen bump."""
    flat = Vault(str(tmp_path))
    assert flat.upsert(_lead()) == "created"
    opted_in = Vault(str(tmp_path), lead_layout="active_archive")
    assert opted_in.upsert(_lead()) == "updated"
    assert os.path.isfile(os.path.join(flat.leads_dir, "Example Ltd - Example Role.md"))
    assert not os.path.exists(os.path.join(flat.leads_dir, ACTIVE_SUBDIR))


def test_a_refused_lead_creates_no_write_folder(tmp_path):
    """A lead that writes NOTHING must not leave an empty Active/ behind -- the makedirs sits
    after the refusal check for exactly this reason, and pointing it at a new directory is a fresh
    chance to get that order wrong.

    Two things the fixture has to get right, and both were measured rather than assumed.

    BOTH sides need a non-empty location: `same_opportunity` reaches DIFFERENT only through
    `_compare_locations`, and an EMPTY incoming location is UNKNOWN, which terminates the walk at
    the first candidate with `merge` and never exhausts it. LOCATIONS' members are token-disjoint
    by construction, so any two read DIFFERENT.

    And EVERY candidate must be seated, not just the first. `_candidate_names` yields two here --
    the clean `Company - Title` and the location-suffixed `Company - Title - Bravo` -- so seeding
    only the first advances past it and CREATES at the second. Refusal is "ran out of candidates,
    every one a note proven different", which needs both."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    os.makedirs(v.leads_dir, exist_ok=True)
    names, _capped = v._candidate_names("Example Ltd", "Example Role", LOCATIONS[1])
    assert len(names) == 2, names
    for name in names:
        with open(os.path.join(v.leads_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: Example Ltd\nrole: Example Role\n"
                     f"url: https://example.invalid/other\n"
                     f"location: {LOCATIONS[0]}\n---\nbody\n")
    outcome = v.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[1]))
    # Assert the PRECONDITION separately from the property. If this fixture stops reaching
    # `refused`, this line says so, instead of the test quietly passing because nothing was
    # created for some entirely different reason.
    assert outcome == "refused", f"fixture did not reach the refusal arm: {outcome}"
    assert not os.path.exists(os.path.join(v.leads_dir, ACTIVE_SUBDIR))


def test_a_symlinked_write_folder_refuses_rather_than_creating_an_invisible_note(tmp_path):
    """The create-arm half of the same harm. `_walk` does not follow symlinks, so a note created
    inside a symlinked write folder is invisible to read_leads AND to _locate -- which means the
    next scrape does not find it and creates it again, as a fresh duplicate, every single run.

    Refused loudly (an OSError the ingest sink counts `skipped`, keeping the lead OUT of seen.db
    for a retry) rather than written. `os.makedirs(..., exist_ok=True)` succeeds on a symlink to a
    directory, so nothing else would have caught this."""
    import pytest
    v = Vault(str(tmp_path), lead_layout="active_archive")
    os.makedirs(v.leads_dir, exist_ok=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, os.path.join(v.leads_dir, ACTIVE_SUBDIR))

    with pytest.raises(OSError, match="symlink"):
        v.upsert(_lead())
    assert os.listdir(target) == [], "a note was created behind the symlink"
