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


def test_a_file_named_like_the_write_folder_raises_rather_than_burning_the_race_retries(tmp_path):
    """`os.makedirs(..., exist_ok=True)` raises FileExistsError for a NON-directory. With the
    makedirs inside the create arm's try -- whose arm reads FileExistsError as the #16 create RACE
    -- a plain file named `Active` burned every retry and refused the lead with "create raced
    repeatedly", a mechanism that never fired and a cause never stated. Outside the try it
    propagates as an ordinary OSError carrying the real errno and path, which the ingest sink
    counts `skipped` and keeps out of seen.db for a retry."""
    import pytest
    v = Vault(str(tmp_path), lead_layout="active_archive")
    os.makedirs(v.leads_dir, exist_ok=True)
    with open(os.path.join(v.leads_dir, ACTIVE_SUBDIR), "w", encoding="utf-8") as fh:
        fh.write("not a directory\n")
    # Assert the SOURCE, not just the type. `_write(..., exclusive=True)` on the #16 create race
    # raises FileExistsError too, so `pytest.raises(FileExistsError)` alone cannot tell the guard
    # from the path it precedes -- this repo's own recorded lesson about a guard raising the same
    # type as its neighbour. The filename pins it to the makedirs.
    with pytest.raises(FileExistsError) as exc:
        v.upsert(_lead())
    assert exc.value.filename and exc.value.filename.endswith(ACTIVE_SUBDIR), (
        f"FileExistsError came from {exc.value.filename!r}, not the write-folder makedirs")


def test_a_symlinked_leads_dir_still_works_under_the_flat_default(tmp_path):
    """The flat half of the symlink guard, and the case an earlier draft BROKE.

    Under `lead_layout: ""` the write folder IS leads_dir, and a symlinked leads_dir is perfectly
    scannable: `os.walk` scandirs its TOP argument directly, and followlinks=False governs descent
    into the `dirnames` it discovers, not the root it was handed. A guard that did not compare
    against leads_dir hard-failed every lead on this working configuration -- {'created': 0,
    'skipped': 3} through the sink, no note written, every run, with a reason that was false here.

    The whole suite stayed GREEN under that regression, because every other symlink test builds
    `active_archive`. This is the row that was missing."""
    real = tmp_path / "real-leads"
    real.mkdir()
    v = Vault(str(tmp_path))
    os.makedirs(os.path.dirname(v.leads_dir), exist_ok=True)
    os.symlink(real, v.leads_dir)

    assert v.upsert(_lead()) == "created"
    assert os.path.isfile(os.path.join(str(real), "Example Ltd - Example Role.md"))
    assert [n.slug for n in v.read_leads()] == ["Example Ltd - Example Role"]
    # ...and a re-scrape reconciles against it rather than minting a twin.
    assert v.upsert(_lead()) == "updated"
