"""`leads rename` (#151) -- the filename-to-frontmatter rename pass.

A lead note created with a blank or sentinel ("Unknown", "Confidential", ...) company is
seated at `" - <role>.md"` or `"Unknown - <role>.md"`. Once triage backfills a real company
into the frontmatter (tasks 2-3 of this plan), the FILENAME and the frontmatter disagree, and
`_resolve_path`'s candidate walk is keyed on the filename, never the frontmatter -- so a
re-scrape of the same posting mints a SECOND note at the fresh company's candidate name rather
than finding the existing one. `Vault.reconcile_names` closes that gap by renaming the note.

Unlike `reconcile_layout` (`tests/test_leads_reconcile.py`), this pass has no `lead_layout`
gate and no `_managed_dirs()` gate -- folder and basename are orthogonal axes, and a note's
folder is never touched here.
"""
import os

import pytest

from sluice.core.leads import Lead, is_placeholder_company
from sluice.core.vault import Vault


def _seed(vault, rel, *, company="Unknown", role="Example Role", location="", status="new",
          url=""):
    path = os.path.join(vault.leads_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\ncompany: {company}\nrole: {role}\nlocation: {location}\n"
                 f"status: {status}\nurl: {url}\nlast_seen: 2026-01-01\n---\nbody\n")
    return path


def _v(tmp_path, layout="active_archive"):
    v = Vault(str(tmp_path), lead_layout=layout)
    os.makedirs(v.leads_dir, exist_ok=True)
    return v


def test_the_report_renames_nothing(tmp_path):
    """Report-first, like `leads dedupe`, `leads expire` and `reconcile_layout`. The default IS
    the dry run, which is why there is no --dry-run flag to be inert."""
    v = _v(tmp_path)
    src = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    rep = v.reconcile_names()
    assert rep["renames"] == [
        ("Unknown - Example Role", "Example Co - Example Role", ".")]
    assert os.path.isfile(src), "the report renamed something"
    assert not os.path.exists(os.path.join(v.leads_dir, "Example Co - Example Role.md"))


def test_a_blank_company_note_renames_to_its_frontmatter_name(tmp_path):
    """The `" - <role>.md"` sentinel this whole feature exists to repair: a note seated with no
    company at all, now carrying a real one in frontmatter."""
    v = _v(tmp_path)
    src = _seed(v, " - Example Role.md", company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == [
        (" - Example Role", "Example Co - Example Role", ".")]
    dest = os.path.join(v.leads_dir, "Example Co - Example Role.md")
    assert os.path.isfile(dest)
    assert not os.path.exists(src)


def test_a_sentinel_named_note_renames_once_company_is_real(tmp_path):
    """The other placeholder population: an honest non-answer ("Unknown"), not a blank."""
    v = _v(tmp_path)
    src = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == [
        ("Unknown - Example Role", "Example Co - Example Role", ".")]
    dest = os.path.join(v.leads_dir, "Example Co - Example Role.md")
    assert os.path.isfile(dest)
    assert not os.path.exists(src)


@pytest.mark.parametrize("stale_name,company", [
    (" - Example Role.md", ""),
    ("Unknown - Example Role.md", "Unknown"),
])
def test_frontmatter_still_offering_no_real_company_is_unresolved(tmp_path, stale_name, company):
    """The frontmatter has not been backfilled yet (still blank, or still the same sentinel) --
    there is nothing safe to rename TO, so the note is reported and left exactly as it is."""
    v = _v(tmp_path)
    src = _seed(v, stale_name, company=company, role="Example Role")
    rep = v.reconcile_names(apply=True)
    slug = stale_name[:-3]
    assert rep["renames"] == []
    assert rep["unresolved"] == [(slug, company)]
    assert os.path.isfile(src)


def test_a_name_this_store_never_minted_is_invisible(tmp_path):
    """The current stem must be a byte-identical RE-DERIVATION from _candidate_names, never a
    " - " prefix heuristic. Here the role in frontmatter has drifted since the note was seated
    (a human edit, or an upstream re-scrape that changed the title text without renaming the
    file) -- re-deriving from the FRESH role no longer reproduces the current filename, so this
    pass must leave it alone entirely rather than guess."""
    v = _v(tmp_path)
    # Precondition: the head really is recognised as a placeholder, so this test exercises the
    # RE-DERIVATION mismatch and not merely the shallower placeholder-head check.
    assert is_placeholder_company("Unknown")
    src = _seed(v, "Unknown - Old Role.md", company="Example Co", role="New Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    assert rep["unresolved"] == []
    assert rep["collisions"] == []
    assert rep["skipped"] == []
    assert os.path.isfile(src)


def test_a_location_suffixed_stale_note_renames_to_the_bare_candidate_one(tmp_path):
    """Candidate 1 is ALWAYS the target, even when the note was originally seated at a
    location-suffixed candidate (candidate 2) because a bare-name collision forced the walk to
    advance at create time. Renaming to anything but candidate 1 would mint a duplicate on the
    very next scrape, since _resolve_path always tries candidate 1 first."""
    v = _v(tmp_path)
    stale = "Unknown - Example Role - Example City.md"
    src = _seed(v, stale, company="Example Co", role="Example Role", location="Example City")
    rep = v.reconcile_names(apply=True)
    target = "Example Co - Example Role"
    assert rep["renames"] == [(stale[:-3], target, ".")]
    dest = os.path.join(v.leads_dir, f"{target}.md")
    assert os.path.isfile(dest)
    assert not os.path.exists(src)

    # And the renamed note is found IN PLACE by a follow-up scrape carrying the fixed company --
    # not re-created as a duplicate.
    lead = Lead(source="test", search="q", title="Example Role", company="Example Co",
                location="Example City", url="")
    assert v.upsert(lead) == "updated"
    assert len([n for n in v.read_leads() if "Example Role" in n.slug]) == 1


def test_idempotence(tmp_path):
    """A second run over an already-renamed note must be a no-op: renaming does not fire again
    just because the pass is re-run."""
    v = _v(tmp_path)
    _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    v.reconcile_names(apply=True)
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    assert rep["unresolved"] == []
    assert rep["collisions"] == []


def test_a_cross_folder_target_collision_is_caught_only_by_the_vault_wide_precheck(tmp_path):
    """`_reserve_and_move`'s O_EXCL reservation is scoped to ONE directory -- the stale note's
    OWN directory, since source dir and destination dir are the same for a rename. A note
    already correctly named in a DIFFERENT folder is therefore invisible to layer 3 alone: this
    test's fixture would let the rename go through (creating two notes at one slug across two
    folders) if the vault-wide `self._locate` precheck were removed."""
    v = _v(tmp_path)
    stale = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    blocker = _seed(v, os.path.join("FolderB", "Example Co - Example Role.md"),
                     company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    assert rep["collisions"] == [("Unknown - Example Role", "Example Co - Example Role")]
    assert os.path.isfile(stale), "the stale note was renamed despite the cross-folder clash"
    assert os.path.isfile(blocker)
    assert not os.path.exists(os.path.join(v.leads_dir, "Example Co - Example Role.md")), (
        "a duplicate was created at the leads-dir root")


def test_a_same_folder_collision_refuses_and_is_never_suffixed(tmp_path):
    v = _v(tmp_path)
    stale = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    blocker = _seed(v, "Example Co - Example Role.md",
                     company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    assert rep["collisions"] == [("Unknown - Example Role", "Example Co - Example Role")]
    assert os.path.isfile(stale) and os.path.isfile(blocker)
    assert not os.path.exists(os.path.join(v.leads_dir, "Example Co - Example Role.1.md"))


def test_two_stale_notes_racing_to_one_target_both_refuse(tmp_path):
    """Neither is picked arbitrarily: two DIFFERENT current names that would both mint the SAME
    fresh target both refuse, and neither moves."""
    v = _v(tmp_path)
    a = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    b = _seed(v, " - Example Role.md", company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    target = "Example Co - Example Role"
    assert sorted(rep["collisions"]) == sorted([
        ("Unknown - Example Role", target), (" - Example Role", target)])
    assert os.path.isfile(a) and os.path.isfile(b)
    assert not os.path.exists(os.path.join(v.leads_dir, f"{target}.md"))


def test_an_archived_loser_is_never_a_rename_source(tmp_path):
    """#81. `_merged/` is pruned from the scan set, so read_leads never returns an archived
    loser -- and this pass must not reach one by any other route either."""
    v = _v(tmp_path)
    loser = _seed(v, os.path.join("_merged", "Unknown - Gone.md"),
                  company="Example Co", role="Gone")
    rep = v.reconcile_names(apply=True)
    assert rep["examined"] == 0
    assert rep["renames"] == [] and rep["unresolved"] == []
    assert os.path.isfile(loser)


def test_a_symlinked_note_is_refused_into_skipped_not_renamed(tmp_path):
    """A rename via os.replace on a symlink would move the LINK, not the file it points to,
    silently detaching the note. Refused into `skipped`, before any move is attempted."""
    v = _v(tmp_path)
    real = tmp_path / "elsewhere.md"
    real.write_text(
        "---\ncompany: Example Co\nrole: Example Role\nlocation: \nstatus: new\n"
        "url: \nlast_seen: 2026-01-01\n---\nbody\n", encoding="utf-8")
    link_path = os.path.join(v.leads_dir, "Unknown - Example Role.md")
    os.symlink(str(real), link_path)

    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == []
    assert rep["skipped"] == [("Unknown - Example Role", "note is a symlink; renaming would "
                                                           "move the link, not the file")]
    assert os.path.islink(link_path), "the symlink itself was moved"
    assert real.read_text(encoding="utf-8") == (
        "---\ncompany: Example Co\nrole: Example Role\nlocation: \nstatus: new\n"
        "url: \nlast_seen: 2026-01-01\n---\nbody\n")


def test_a_raced_rename_produces_resurrected_not_ambiguous(tmp_path, monkeypatch):
    """The raced-move residual `_reserve_and_move` states as accepted (no portable stdlib
    atomic-conditional-rename exists): a concurrent writer's os.replace(tmp, path) can land
    between the move and this pass's own bookkeeping, re-creating the OLD basename. That is a
    DIFFERENT slug from the new one, so index_by_slug's `ambiguous` bucket cannot see it -- this
    pass runs its own probe and must file it under `resurrected` instead, never `ambiguous`."""
    v = _v(tmp_path)
    src = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    from sluice.core import vault as vaultmod
    real = vaultmod._reserve_and_move

    def racing_move(s, dest_dir, base, **kw):
        dest = real(s, dest_dir, base, **kw)
        # Exactly what a concurrent _atomic_write's os.replace(tmp, path) does when it lands
        # after the rename: the OLD source path exists again, holding the racer's edit.
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: Example Co\nrole: Example Role\nstatus: applied\n"
                      "---\nbody\n")
        return dest

    monkeypatch.setattr(vaultmod, "_reserve_and_move", racing_move)
    rep = v.reconcile_names(apply=True)
    assert len(rep["renames"]) == 1, "the rename itself must still have happened"
    assert os.path.isfile(os.path.join(v.leads_dir, "Example Co - Example Role.md"))
    assert os.path.isfile(src), "the fixture did not reproduce the resurrected old path"
    assert rep["resurrected"] == [("Unknown - Example Role", "Example Co - Example Role")]
    assert rep["ambiguous"] == {}, "the two passes' residuals must not be merged into one bucket"


def test_the_moved_notes_bytes_are_identical_before_and_after(tmp_path):
    """A pure filename operation -- never a status write, never any frontmatter write."""
    v = _v(tmp_path)
    src = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    with open(src, "rb") as fh:
        before = fh.read()
    v.reconcile_names(apply=True)
    dest = os.path.join(v.leads_dir, "Example Co - Example Role.md")
    with open(dest, "rb") as fh:
        after = fh.read()
    assert before == after


def test_a_user_filed_note_still_renames_in_place(tmp_path):
    """No `_managed_dirs()` gate: a note the user filed into their own folder keeps that folder
    -- only its basename changes."""
    v = _v(tmp_path)
    src = _seed(v, os.path.join("Research", "Unknown - Example Role.md"),
                company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == [
        ("Unknown - Example Role", "Example Co - Example Role", "Research")]
    dest = os.path.join(v.leads_dir, "Research", "Example Co - Example Role.md")
    assert os.path.isfile(dest)
    assert not os.path.exists(src)


def test_renames_even_with_no_lead_layout_configured(tmp_path):
    """The deliberate divergence from reconcile_layout, which abstains entirely under the flat
    default (decision 7). Basename and folder are orthogonal, so this pass has no such abstain."""
    v = _v(tmp_path, layout="")
    src = _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    rep = v.reconcile_names(apply=True)
    assert rep["renames"] == [
        ("Unknown - Example Role", "Example Co - Example Role", ".")]
    assert os.path.isfile(os.path.join(v.leads_dir, "Example Co - Example Role.md"))
    assert not os.path.exists(src)


def test_a_plain_non_lead_file_is_never_touched(tmp_path):
    """A user's interview-prep note carries neither company nor role, so read_leads skips it and
    this pass never sees it -- even though its name happens to look like a placeholder stem."""
    v = _v(tmp_path)
    path = os.path.join(v.leads_dir, "Unknown - Notes.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: prep\n---\nnotes\n")
    rep = v.reconcile_names(apply=True)
    assert rep["examined"] == 0
    assert rep["renames"] == []
    assert os.path.isfile(path)


def test_a_renamed_note_is_found_in_place_by_the_next_upsert(tmp_path):
    v = _v(tmp_path)
    _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    v.reconcile_names(apply=True)
    lead = Lead(source="test", search="q", title="Example Role", company="Example Co",
                location="", url="")
    outcome = v.upsert(lead)
    assert outcome in ("updated", "merged")
    assert len(v.read_leads()) == 1, "the rename was not found, so a duplicate was created"


def test_a_note_renamed_then_merged_carries_the_new_name_into_the_archive_stamp(tmp_path):
    """`merge_cluster` derives the archived `archived_from_note` stamp from `os.path.basename`
    of the REF it is handed. Passing the note's post-rename ref means a lead merged away after
    this pass ran is recognised by its NEW name -- never the stale one -- on every later
    re-scrape (#81), even from a brand new Vault instance with an empty cache."""
    v = _v(tmp_path)
    _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role",
          url="https://example.test/job/1")
    survivor = _seed(v, "Beta - Other Role.md", company="Beta", role="Other Role")
    v.reconcile_names(apply=True)
    renamed_ref = os.path.join(v.leads_dir, "Example Co - Example Role.md")
    assert os.path.isfile(renamed_ref)

    archived = v.merge_cluster(
        survivor_ref=survivor, loser_refs=[renamed_ref],
        alt_urls=["https://example.test/job/1"], first_seen="2026-01-01",
        last_seen="2026-01-01")
    assert len(archived) == 1
    assert not os.path.exists(renamed_ref), "the loser was not archived"

    # A FRESH store instance, so nothing is carried over via in-memory caches -- only the
    # archive's own stamped name.
    v2 = Vault(str(tmp_path), lead_layout="active_archive")
    lead = Lead(source="test", search="q", title="Example Role", company="Example Co",
                location="", url="https://example.test/job/1")
    assert v2.upsert(lead) == "merged_away"


def test_a_source_still_scraping_a_blank_company_resolves_to_the_old_stale_name(tmp_path):
    """`_candidate_names` is never taught to read frontmatter. A source whose scrape defect
    (issue #151's own root cause) is not yet fixed keeps handing over a blank company, and the
    candidate walk must keep minting the SAME stale-shaped name it always did -- oblivious to
    the fact that a DIFFERENTLY-named note for the same role now sits in the vault, correctly
    filled in, courtesy of this very pass."""
    v = _v(tmp_path)
    _seed(v, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    v.reconcile_names(apply=True)
    assert os.path.isfile(os.path.join(v.leads_dir, "Example Co - Example Role.md"))

    still_broken = Lead(source="test", search="q", title="Example Role", company="",
                        location="", url="")
    assert v.upsert(still_broken) == "created"
    slugs = sorted(n.slug for n in v.read_leads())
    assert slugs == [" - Example Role", "Example Co - Example Role"], (
        "the still-blank scrape either clobbered the renamed note or failed to create its own")
