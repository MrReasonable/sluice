"""The scan set: which directories a lead may be read from, and which files in them count
as leads. `_merged/` is excluded EXPLICITLY here -- before this it was invisible only
because os.listdir is non-recursive, which a recursive walk would have undone (#81)."""
import os

import pytest

import sluice.core.vault as _vault_module
from sluice.core.leads import Lead
from sluice.core.vault import (
    _MERGED_SUBDIR, _PRIVATE_SUBDIRS, Vault, _is_lead_note,
)
from tests.conftest import LOCATIONS, UNREADABLE_DIR as _UNREADABLE_DIR


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _lead(**kw):
    base = dict(source="cord", search="Analyst", title="Analyst", company="Acme",
                url="https://ex.invalid/1", location=LOCATIONS[0], salary="",
                job_type="permanent", first_seen="2026-07-07", last_seen="2026-07-07")
    base.update(kw)
    return Lead(**base)


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
    # `list(...)` even though _scan_dirs already returns a copy: this test must not rest on
    # THAT being true, or it goes vacuous (comparing a list to itself) the moment the copy is
    # removed -- which is a change the test below is what catches, not this one.
    first = list(v._scan_dirs())
    (leads / "Added Later").mkdir()
    assert v._scan_dirs() == first      # same instance, same answer


def test_scan_dirs_never_hands_out_the_live_cache(tmp_path):
    """A caller that mutates the returned list must not be able to poison the store.

    The scan set is the store's own state: appending to it makes `_locate` stat directories
    that do not exist, and REMOVING from it makes `_locate` miss the directory a note is
    actually in -- which returns empty, and empty is the branch that CREATES (a duplicate)
    or records `merged_away` in `seen.db` (which has no removal path). Nothing mutates it
    today; this pins that nothing CAN.

    Witnessed by restoring `return self._scan_dirs_cache`: `is` becomes True, and the
    mutation below then reddens the second assertion too."""
    leads = _leads_dir(tmp_path)
    (leads / "Active").mkdir(parents=True)
    v = Vault(str(tmp_path))
    handed_out = v._scan_dirs()
    assert handed_out is not v._scan_dirs_cache
    handed_out.append("/nonexistent")
    handed_out.remove(str(leads / "Active"))
    assert sorted(v._scan_dirs()) == sorted([str(leads), str(leads / "Active")])


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
def _with_unreadable_subdir(tmp_path, call):
    """Seed a note under an unreadable subdirectory and assert `call(vault)` raises OSError,
    restoring the mode whatever happens (a leftover 000 directory breaks tmp_path cleanup)."""
    leads = _leads_dir(tmp_path)
    (leads / "Archive").mkdir(parents=True)
    (leads / "Archive" / "Acme - Analyst.md").write_text('---\ncompany: "Acme"\n---\n')
    os.chmod(leads / "Archive", 0o000)
    try:
        with pytest.raises(OSError):
            call(Vault(str(tmp_path)))
    finally:
        os.chmod(leads / "Archive", 0o755)


@_UNREADABLE_DIR
def test_an_unreadable_subdirectory_raises_rather_than_reading_as_empty(tmp_path):
    """os.walk's DEFAULT onerror=None silently yields nothing for a directory it cannot
    open. Measured: a 6-note vault reads as 3 notes, no error, no log. Every note in it
    would then be invisible to the write path and re-created -- mass re-ingest arriving
    through a permissions bit."""
    _with_unreadable_subdir(tmp_path, lambda v: v._scan_dirs())


@_UNREADABLE_DIR
def test_read_leads_propagates_an_unreadable_subdirectory(tmp_path):
    """Through the PUBLIC method, which is what `core/protocols.py` promises and what every
    caller reaches. Its sibling above drives `_scan_dirs`, a private helper `read_leads` does
    not even call -- so wrapping read_leads' own walk loop in `try/except OSError: pass`,
    leaving `_walk`'s onerror intact, left the whole suite green (mutant M12)."""
    _with_unreadable_subdir(tmp_path, lambda v: v.read_leads())


def _with_unstatable_leads_dir(tmp_path, call):
    """Take away permission on the PARENT, so `os.stat(leads_dir)` itself fails. Restores
    the mode whatever happens (a leftover 000 directory breaks tmp_path cleanup)."""
    parent = tmp_path / "Job Applications"
    (parent / "Job Leads" / "Active").mkdir(parents=True)
    (parent / "Job Leads" / "Active" / "Acme - Analyst.md").write_text(
        '---\ncompany: "Acme"\n---\n')
    os.chmod(parent, 0o000)
    try:
        with pytest.raises(PermissionError):
            call(Vault(str(tmp_path)))
    finally:
        os.chmod(parent, 0o755)


@_UNREADABLE_DIR
def test_an_unstatable_leads_dir_raises_rather_than_reading_as_not_yet_created(tmp_path):
    """`_is_dir` answers False ONLY to FileNotFoundError -- leads_dir before the first
    upsert, which is not an error. os.path.isdir swallows EVERY OSError, so an unstatable
    leads_dir reads as 'not created yet' and the scan set collapses to [leads_dir]: every
    note in every subfolder invisible to read_leads AND to _locate, which re-creates all of
    them. Measured with the parent at mode 000 -- shipped: PermissionError; the os.path.isdir
    mutant: ['<leads_dir>'], silently.

    Asserted on `_scan_dirs`, not on `upsert`, because upsert is NOT discriminating: it
    raises under both, but the mutant's raise comes from an unrelated later os.makedirs, so
    containment there is luck rather than the guard."""
    _with_unstatable_leads_dir(tmp_path, lambda v: v._scan_dirs())


@_UNREADABLE_DIR
def test_read_leads_does_not_read_an_unstatable_leads_dir_as_empty(tmp_path):
    """The same rung on the other side: read_leads' own early return used os.path.isdir, so
    an unstatable leads_dir came back as an EMPTY vault -- no notes, no error, no log --
    through the public seam every sub-app consumes."""
    _with_unstatable_leads_dir(tmp_path, lambda v: v.read_leads())


@_UNREADABLE_DIR
def test_normalize_all_statuses_propagates_an_unreadable_subdirectory(tmp_path):
    """The same exposure on the other public consumer of the scan set. It has the harsher
    version of the failure: an unreadable subtree read as empty means its notes are never
    canonicalized AND the summary reports a clean sweep over a vault it only partly saw."""
    _with_unreadable_subdir(tmp_path, lambda v: v.normalize_all_statuses())


@_UNREADABLE_DIR
def test_normalize_all_statuses_does_not_read_an_unstatable_leads_dir_as_empty(tmp_path):
    """The THIRD scan-set consumer, and the one that WRITES -- so its own early return was
    the one that mattered most and the one the first two fixes missed. Measured before this,
    with the parent at mode 000: read_leads raised PermissionError while this returned
    {'changed': 0, 'unchanged': 0, 'unknown': [], 'conflicts': []} over a vault holding a
    real note, and core/app.py hands that summary to the CLI as a clean sweep. The
    os.path.isdir False also short-circuited BEFORE _walk, so onerror=_reraise -- the guard
    that makes the sibling case above loud -- never got the chance to fire."""
    _with_unstatable_leads_dir(tmp_path, lambda v: v.normalize_all_statuses())


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


def test_read_leads_warns_when_two_notes_claim_one_slug(tmp_path, caplog):
    """On a flat store slug uniqueness held by CONSTRUCTION -- one directory cannot hold two
    files at one basename, and the slug IS the basename. The recursive scan removes that. The
    WRITE path refuses such a candidate and names both colliding paths; the read path has no
    such option, since dropping a lead takes it out of the write path's lookup too and the
    next scrape re-creates it. So it returns BOTH and is loud."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    _write_note(leads / "Archive" / "Acme - Analyst.md")
    with caplog.at_level("WARNING"):
        notes = Vault(str(tmp_path)).read_leads()
    assert [n.slug for n in notes] == ["Acme - Analyst", "Acme - Analyst"]
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("Acme - Analyst" in m and "claimed by 2 notes" in m for m in said), said


def test_a_duplicate_slug_is_warned_about_once_per_store(tmp_path, caplog):
    """Same discipline as the symlink warning, but forward-looking rather than measured: no
    shipped command reads one status set twice through a single store (`apply prep` reads
    once on both its forms; `track run`'s two reads take disjoint sets), so this pins the
    property for the first command that does. The repeated reads below are the test's own
    construction, not a production path.

    Witnessed by deleting the `if key in self._warned_dup_slugs: continue` arm: three lines.
    """
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    _write_note(leads / "Archive" / "Acme - Analyst.md")
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        v.read_leads()
        v.read_leads()
        v.read_leads({"new"})          # the same twins reached through a FILTERED read
    said = [r.getMessage() for r in caplog.records if "claimed by" in r.getMessage()]
    assert len(said) == 1, said


def test_a_second_twin_at_a_warned_slug_is_still_reported(tmp_path, caplog):
    """The dedup key carries the REFS, never the slug alone. Keyed on the slug it would
    suppress a genuinely NEW collision -- a third note filed at the same name after the
    first warning -- which is the failure mode a dedup usually introduces."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    _write_note(leads / "Archive" / "Acme - Analyst.md")
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        v.read_leads()
        _write_note(leads / "Backlog" / "Acme - Analyst.md")
        v.read_leads()
    said = [r.getMessage() for r in caplog.records if "claimed by" in r.getMessage()]
    assert len(said) == 2, said
    assert "claimed by 2 notes" in said[0] and "claimed by 3 notes" in said[1]


def test_read_leads_stays_quiet_when_every_slug_is_unique(tmp_path, caplog):
    """Two notes in two subfolders is the ORDINARY case this feature exists for. A guard
    keyed on the folder count rather than on the slug would fire on all of them."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    _write_note(leads / "Archive" / "Acme - Engineer.md", role="Engineer")
    with caplog.at_level("WARNING"):
        assert len(Vault(str(tmp_path)).read_leads()) == 2
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


# ── a symlinked subfolder is not followed, and says so ────────────────────────
def _symlinked_folder(tmp_path, *, with_note, nested=False):
    """A subfolder of leads_dir that is a SYMLINK to a directory elsewhere -- ordinary
    practice in an Obsidian vault, and invisible to the walk (followlinks=False). Returns
    the TARGET, which is what a caller taking permissions away has to chmod (the link
    itself carries no useful mode).

    `nested` puts the note one directory DOWN inside the target, the layout a recursive
    scan invites and the one a flat listdir of the target could not see."""
    leads = _leads_dir(tmp_path)
    leads.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    if with_note:
        _write_note((target / "2025" if nested else target) / "Acme - Analyst.md")
    else:
        (target / "notes.txt").write_text("not a note\n")
    (leads / "Applied").symlink_to(target, target_is_directory=True)
    return target


def test_a_symlinked_subfolder_holding_notes_is_warned_about(tmp_path, caplog):
    """followlinks=False is the RIGHT default (a symlink loop would spin the walk, and a
    link out of the vault would drag arbitrary directories into the scan set) -- so this is
    made loud rather than fixed by following. Measured before the warning: a note at
    `status: applied` behind such a link came back `created` from upsert, the original
    untouched, with no log line anywhere. os.walk still LISTS the link in `dirnames`, which
    is where it is visible without being followed."""
    _symlinked_folder(tmp_path, with_note=True)
    with caplog.at_level("WARNING"):
        notes = Vault(str(tmp_path)).read_leads()
    assert notes == [], "the note really is invisible; the warning is the whole remedy"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("Applied" in m and "symlink" in m for m in said), said


def test_a_symlinked_subfolder_is_warned_about_once_per_store(tmp_path, caplog):
    """One command walks the tree several times -- a read, the scan set, one re-derive per
    create -- so an un-deduped warning would say the same thing a dozen times in one run,
    which is the noise the empty-symlink case above is deliberately kept out of."""
    _symlinked_folder(tmp_path, with_note=True)
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        v.read_leads()
        v.read_leads()
        v._scan_dirs()
    said = [r.getMessage() for r in caplog.records if "symlink" in r.getMessage()]
    assert len(said) == 1, said


def test_a_symlinked_subfolder_whose_note_is_nested_is_warned_about(tmp_path, caplog):
    """The probe is RECURSIVE. A flat listdir of the link target saw nothing when the notes
    sat one directory down -- which is the layout a recursive scan invites, so it is the
    layout the warning most needed to cover. Measured before this, with the note at
    `<target>/2025/`: upsert returned `created`, a fresh note appeared at `new`, the
    `applied` original stayed untouched behind the link, and ZERO records reached the
    sluice.core.vault logger."""
    _symlinked_folder(tmp_path, with_note=True, nested=True)
    with caplog.at_level("WARNING"):
        notes = Vault(str(tmp_path)).read_leads()
    assert notes == [], "the nested note really is invisible; the warning is the remedy"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("Applied" in m and "symlink" in m for m in said), said


def test_a_symlinked_subfolder_without_notes_stays_quiet(tmp_path, caplog):
    """A warning that fires on every walk for a harmless link is one users learn to ignore,
    which is how the real one gets missed. Only links HOLDING notes are reported."""
    _symlinked_folder(tmp_path, with_note=False)
    with caplog.at_level("WARNING"):
        Vault(str(tmp_path)).read_leads()
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


def test_a_symlinked_subfolder_is_probed_once_per_store_even_when_it_holds_no_note(
        tmp_path, monkeypatch):
    """The memo is keyed on the PROBE, not on the report -- and the quiet link is the case
    that needs it most. `_holds_a_note` short-circuits on the first `.md`, which cannot help
    when there is no `.md` to stop at: the harmless link is the one that pays a FULL
    recursive walk of its target, and keyed on the report it was never memoised at all.

    Counted at `_holds_a_note`, because one call there IS one walk of the linked tree, which
    is the quantity in question. Measured before the fix with this exact fixture: 8 walks.
    The sibling above pins that this link stays quiet; a memo that suppressed the WARNING
    without suppressing the walk would satisfy that one and not this."""
    _symlinked_folder(tmp_path, with_note=False)
    walks = []
    real = _vault_module._holds_a_note
    monkeypatch.setattr(_vault_module, "_holds_a_note",
                        lambda p: (walks.append(p), real(p))[1])
    v = Vault(str(tmp_path))
    for i in range(5):
        v.upsert(_lead(title=f"Role {i}", url=f"https://example.invalid/{i}"))
    v.read_leads()
    v.read_leads()
    assert len(walks) == 1, f"the linked tree was walked {len(walks)} times, not once"


@_UNREADABLE_DIR
def test_an_unreadable_symlink_target_is_reported_and_does_not_abort_the_read(tmp_path,
                                                                             caplog):
    """The warning path must never raise: it is best-effort, and the caller is the ONE
    definition of the scan set, so a warning that aborts a read is worse than the thing it
    warns about. And 'cannot tell' must not read as 'nothing there' -- the notes behind an
    unreadable link are just as invisible as the ones behind a readable one.

    Both halves were unwitnessed: every other symlink test uses a READABLE target, so
    deleting the try/except (letting the OSError out of `_walk`) and deleting the
    unreadable-target report each left the whole suite green."""
    target = _symlinked_folder(tmp_path, with_note=True)
    os.chmod(target, 0o000)
    try:
        with caplog.at_level("WARNING"):
            notes = Vault(str(tmp_path)).read_leads()   # RETURNS -- must not raise
    finally:
        os.chmod(target, 0o755)
    assert notes == []
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("Applied" in m and "cannot read" in m for m in said), said


def test_a_real_subfolder_holding_notes_stays_quiet(tmp_path, caplog):
    """The discriminator is the SYMLINK, not the notes. A guard keyed on the note count
    alone would warn about every ordinary subfolder -- which is the feature."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Applied" / "Acme - Analyst.md")
    with caplog.at_level("WARNING"):
        assert len(Vault(str(tmp_path)).read_leads()) == 1
    assert [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"] == []


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
