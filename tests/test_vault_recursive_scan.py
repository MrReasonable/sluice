"""The scan set: which directories a lead may be read from, and which files in them count
as leads. `_merged/` is excluded EXPLICITLY here -- before this it was invisible only
because os.listdir is non-recursive, which a recursive walk would have undone (#81)."""
import os

import pytest

from sluice.core.vault import (
    _MERGED_SUBDIR, _PRIVATE_SUBDIRS, Vault, _is_lead_note,
)


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _skip_as_root():
    # chmod 000 does not bind uid 0, so the unreadable-directory test would pass
    # vacuously in a root container. geteuid is absent on Windows; -1 never equals 0.
    return getattr(os, "geteuid", lambda: -1)() == 0


# ── the exclusion set ─────────────────────────────────────────────────────────
def test_merged_subdir_is_a_private_subdir():
    """One constant, two consumers: the walk prunes `_PRIVATE_SUBDIRS` under leads_dir and
    _archived_match opens leads_dir/_MERGED_SUBDIR. If they ever name different directories
    every archived loser becomes an active note again."""
    assert _MERGED_SUBDIR in _PRIVATE_SUBDIRS


def test_scan_dirs_includes_user_subfolders_and_excludes_merged(tmp_path):
    leads = _leads_dir(tmp_path)
    (leads / "Active").mkdir(parents=True)
    (leads / "Interview Prep").mkdir()
    (leads / _MERGED_SUBDIR).mkdir()
    dirs = Vault(str(tmp_path))._scan_dirs()
    assert str(leads) in dirs
    assert str(leads / "Active") in dirs
    assert str(leads / "Interview Prep") in dirs          # the user's, so it is scanned
    assert str(leads / _MERGED_SUBDIR) not in dirs        # sluice's, so it is not


def test_scan_dirs_excludes_merged_but_not_a_nested_lookalike(tmp_path):
    """The prune is TOP-LEVEL only, because leads_dir/_merged is the one directory
    merge_cluster writes and _archived_match reads. A same-named directory nested deeper
    is the user's and must stay visible, or its notes are re-created as duplicates."""
    leads = _leads_dir(tmp_path)
    (leads / "Active" / _MERGED_SUBDIR).mkdir(parents=True)
    (leads / _MERGED_SUBDIR).mkdir()
    dirs = Vault(str(tmp_path))._scan_dirs()
    assert str(leads / _MERGED_SUBDIR) not in dirs
    assert str(leads / "Active" / _MERGED_SUBDIR) in dirs


# ── caching ───────────────────────────────────────────────────────────────────
def test_scan_dirs_falls_back_to_the_leads_dir_before_it_exists(tmp_path):
    assert Vault(str(tmp_path))._scan_dirs() == [str(_leads_dir(tmp_path))]


def test_scan_dirs_does_not_cache_the_missing_leads_dir(tmp_path):
    """upsert CREATES leads_dir mid-run, so caching 'it does not exist' would leave every
    later lookup in that run blind to the directory it just wrote into."""
    v = Vault(str(tmp_path))
    assert v._scan_dirs() == [str(_leads_dir(tmp_path))]
    sub = _leads_dir(tmp_path) / "Active"
    sub.mkdir(parents=True)
    assert str(sub) in v._scan_dirs()


def test_scan_dirs_is_cached_once_the_leads_dir_exists(tmp_path):
    """Re-deriving per lead costs ~1.4s per 500-lead run against ~4ms cached."""
    leads = _leads_dir(tmp_path)
    leads.mkdir(parents=True)
    v = Vault(str(tmp_path))
    # COPIED, never the live object: _scan_dirs returns its cache by reference, so a bare
    # `first = v._scan_dirs()` aliases it and any future refactor that refreshed the cache
    # IN PLACE would leave this comparing the list to itself -- vacuously green.
    first = list(v._scan_dirs())
    (leads / "Added Later").mkdir()
    assert v._scan_dirs() == first      # same instance, same answer


# ── the lead predicate ────────────────────────────────────────────────────────
def test_a_file_with_either_company_or_role_is_a_lead():
    """NEITHER, not EITHER. A hand edit that blanks `role` must not make the note invisible:
    invisible to read_leads is invisible to the write path, so the next scrape re-creates it
    as a duplicate. Requiring both would do exactly that."""
    assert _is_lead_note({"company": "Acme"})
    assert _is_lead_note({"role": "Analyst"})
    assert _is_lead_note({"company": "Acme", "role": "Analyst"})


def test_a_file_with_neither_company_nor_role_is_not_a_lead():
    """A user's interview-prep or research note living alongside the leads."""
    assert not _is_lead_note({})
    assert not _is_lead_note({"status": "new"})
    assert not _is_lead_note({"company": "", "role": ""})


# ── an unreadable directory is loud ───────────────────────────────────────────
@pytest.mark.skipif(_skip_as_root(), reason="chmod 000 does not bind root")
def test_an_unreadable_subdirectory_raises_rather_than_reading_as_empty(tmp_path):
    """os.walk's DEFAULT onerror=None silently yields nothing for a directory it cannot
    open. Measured: a 6-note vault reads as 3 notes, no error, no log. Every note in it
    would then be invisible to the write path and re-created -- mass re-ingest arriving
    through a permissions bit."""
    leads = _leads_dir(tmp_path)
    (leads / "Archive").mkdir(parents=True)
    (leads / "Archive" / "Acme - Analyst.md").write_text('---\ncompany: "Acme"\n---\n')
    os.chmod(leads / "Archive", 0o000)
    try:
        with pytest.raises(OSError):
            Vault(str(tmp_path))._scan_dirs()
    finally:
        os.chmod(leads / "Archive", 0o755)


# ── read_leads over the scan set ──────────────────────────────────────────────
def _write_note(path, company="Acme", role="Analyst", status="new"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ncompany: "{company}"\nrole: "{role}"\nstatus: {status}\n---\n\nbody\n')
    return path


def test_read_leads_returns_notes_from_subfolders(tmp_path):
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Acme - Analyst.md")
    _write_note(leads / "Active" / "Acme - Engineer.md", role="Engineer")
    _write_note(leads / "Archive" / "Acme - Clerk.md", role="Clerk", status="dismiss")
    slugs = {n.slug for n in Vault(str(tmp_path)).read_leads()}
    assert slugs == {"Acme - Analyst", "Acme - Engineer", "Acme - Clerk"}


def test_read_leads_orders_by_full_path(tmp_path):
    """The fixture makes full-path order and BASENAME order diverge, and they come out
    exact reverses of each other (verified: "Active" < "Archive" since c < r, while
    "Acme - Z" > "Acme - A"). Two notes in ONE directory cannot tell the two orders
    apart, so such a fixture would pass under either rule and pin neither."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Z.md", role="Z")
    _write_note(leads / "Archive" / "Acme - A.md", role="A", status="dismiss")
    got = [n.slug for n in Vault(str(tmp_path)).read_leads()]
    assert got == ["Acme - Z", "Acme - A"]   # full-path order
    assert got != sorted(got)                # which basename order would exactly reverse


def test_read_leads_skips_a_note_that_is_not_a_lead(tmp_path):
    """The whole point of motivation 2: a user gets somewhere to put other notes, and
    sluice must not start triaging them."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    prep = leads / "Interview Prep" / "Questions to ask.md"
    prep.parent.mkdir(parents=True)
    prep.write_text("---\ntags: prep\n---\n\nWhat does success look like?\n")
    assert [n.slug for n in Vault(str(tmp_path)).read_leads()] == ["Acme - Analyst"]


def test_read_leads_keeps_a_lead_whose_role_was_blanked(tmp_path):
    """`neither`, not `either`. Dropping this note would make the next scrape re-create it."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md", role="")
    assert [n.slug for n in Vault(str(tmp_path)).read_leads()] == ["Acme - Analyst"]


# ── normalize_all_statuses over the scan set ───────────────────────────────────
def test_normalize_statuses_reaches_a_note_in_a_subfolder(tmp_path):
    leads = _leads_dir(tmp_path)
    p = _write_note(leads / "Archive" / "Acme - Clerk.md", role="Clerk", status="Dismissed")
    summary = Vault(str(tmp_path)).normalize_all_statuses()
    assert summary["changed"] == 1
    assert "status: dismiss" in p.read_text()


def test_normalize_statuses_never_writes_into_a_users_own_note(tmp_path):
    """never-clobber. A note carrying a `status:` line that is not a lead's is the user's
    business; rewriting it is exactly the wholesale-clobber sluice exists to remove."""
    leads = _leads_dir(tmp_path)
    prep = leads / "Interview Prep" / "Pipeline.md"
    prep.parent.mkdir(parents=True)
    original = "---\nstatus: Parked\ntags: prep\n---\n\nnotes\n"
    prep.write_text(original)
    Vault(str(tmp_path)).normalize_all_statuses()
    assert prep.read_text() == original


def test_normalize_statuses_never_writes_into_an_archived_loser(tmp_path):
    leads = _leads_dir(tmp_path)
    loser = leads / _MERGED_SUBDIR / "Acme - Clerk.md"
    loser.parent.mkdir(parents=True)
    original = '---\ncompany: "Acme"\nrole: "Clerk"\nstatus: Dismissed\n---\n\nbody\n'
    loser.write_text(original)
    Vault(str(tmp_path)).normalize_all_statuses()
    assert loser.read_text() == original


def test_normalize_statuses_counts_a_frontmatter_less_file_as_unchanged(tmp_path):
    """The lead predicate must sit AFTER the `inner is None` arm, never before it.
    _fm_dict(None) is {}, so a file with no frontmatter is not a lead -- a predicate
    placed first would `continue` past the unchanged counter and silently change a
    number this method reports. Witnessed by MOVING the predicate above that arm."""
    leads = _leads_dir(tmp_path)
    leads.mkdir(parents=True)
    (leads / "Not a note.md").write_text("just a body, no frontmatter\n")
    summary = Vault(str(tmp_path)).normalize_all_statuses()
    assert summary["unchanged"] == 1
    assert summary["changed"] == 0
