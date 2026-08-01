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
    first = v._scan_dirs()
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
