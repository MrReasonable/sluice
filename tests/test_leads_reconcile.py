"""`leads reconcile` -- the only pass that MOVES a lead note (decision 2, #1).

Four classes are reported and never moved, each for its own reason:
  - non-canonical status -> never-regress passes an unrecognized status through untouched
  - ambiguous slug       -> the store refuses that identity everywhere; moving a twin PICKS one,
                            which is what every other consumer declines to do
  - user-filed note      -> decision 4 says everything under leads_dir that is not sluice's is the
                            user's, and that must hold for WRITES as well as for reads
  - destination taken    -> refused, NEVER suffixed: the filename is the slug is the identity
"""
import os

import pytest

from sluice.core.leads import ACTIVE_SUBDIR, ARCHIVE_SUBDIR
from sluice.core.vault import Vault


def _seed(vault, rel, *, company="Example Ltd", role="Example Role", status="new"):
    path = os.path.join(vault.leads_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\ncompany: {company}\nrole: {role}\nstatus: {status}\n"
                 f"url: \nlast_seen: 2026-01-01\n---\nbody\n")
    return path


def _v(tmp_path, layout="active_archive"):
    v = Vault(str(tmp_path), lead_layout=layout)
    os.makedirs(v.leads_dir, exist_ok=True)
    return v


def test_the_report_moves_nothing(tmp_path):
    """Report-first, like `leads dedupe` and `leads expire`. The default IS the dry run, which is
    why there is no --dry-run flag to be inert."""
    v = _v(tmp_path)
    src = _seed(v, "A - Live.md", role="Live", status="shortlist")
    rep = v.reconcile_layout()
    assert rep["moves"] == [("A - Live", "A - Live.md",
                             os.path.join(ACTIVE_SUBDIR, "A - Live.md"))]
    assert os.path.isfile(src), "the report wrote something"
    assert not os.path.exists(os.path.join(v.leads_dir, ACTIVE_SUBDIR))


def test_apply_files_a_live_lead_into_active(tmp_path):
    """Also THE migration case: the note sits at the leads-dir ROOT, which is where every note in
    a pre-layout vault sits. The root must be in the managed set, and it is not derivable from the
    status->folder map (no canonical status maps to the root under active_archive)."""
    v = _v(tmp_path)
    _seed(v, "A - Live.md", role="Live", status="shortlist")
    rep = v.reconcile_layout(apply=True)
    assert len(rep["moves"]) == 1
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Live.md"))
    assert not os.path.exists(os.path.join(v.leads_dir, "A - Live.md"))


@pytest.mark.parametrize("status", ["dismiss", "rejected", "accepted", "withdrawn"])
def test_apply_files_dismiss_and_every_terminal_into_archive(tmp_path, status):
    v = _v(tmp_path)
    _seed(v, "A - Done.md", role="Done", status=status)
    v.reconcile_layout(apply=True)
    assert os.path.isfile(os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Done.md"))


def test_a_note_already_in_place_is_counted_not_moved(tmp_path):
    """Idempotence. A second run must be a no-op -- the pass is run repeatedly, and a move that
    re-fired would churn the vault and re-report as work done."""
    v = _v(tmp_path)
    _seed(v, "A - Live.md", role="Live", status="shortlist")
    v.reconcile_layout(apply=True)
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert rep["in_place"] == 1


def test_a_user_filed_lead_is_reported_and_left_alone(tmp_path):
    """Decision 6. A lead the user deliberately put in their own folder is NOT relocated: the scan
    reads it, reconcile reports it, and only a human moves it."""
    v = _v(tmp_path)
    src = _seed(v, os.path.join("Research", "A - Filed.md"), role="Filed", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert rep["user_filed"] == [("A - Filed", "Research")]
    assert os.path.isfile(src), "a user-filed note was relocated"
    # The root is MANAGED, so no user_filed entry can ever carry ".". Asserted rather than left to
    # be re-derived: the CLI renders this as "<where>/ is yours, not sluice's", which would read
    # as "./ is yours" for a root-seated note -- an arm that must be unreachable, not just rare.
    assert all(where != "." for _, where in rep["user_filed"])


def test_a_note_in_a_managed_folder_moves_back_out_of_archive(tmp_path):
    """The map is a derived view in BOTH directions -- a lead reopened from `rejected` to
    `shortlist` must come back out of Archive/, or the archive silently becomes one-way."""
    v = _v(tmp_path)
    _seed(v, os.path.join(ARCHIVE_SUBDIR, "A - Back.md"), role="Back", status="shortlist")
    v.reconcile_layout(apply=True)
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Back.md"))


def test_a_non_canonical_status_is_reported_under_unknown_and_never_moved(tmp_path):
    """never-regress. `normalize_all_statuses` reports an unrecognized value rather than
    rewriting it; the layout must not decide a folder for one either. The RAW value is reported,
    not the normalized one -- showing a user a value their note does not contain is showing them
    the wrong thing to go and fix."""
    v = _v(tmp_path)
    src = _seed(v, "A - Odd.md", role="Odd", status="Some_Future_State")
    rep = v.reconcile_layout(apply=True)
    assert rep["unknown"] == [("A - Odd", "Some_Future_State")]
    assert rep["moves"] == []
    assert os.path.isfile(src)


def test_two_notes_claiming_one_slug_are_refused_and_neither_moves(tmp_path):
    """Decision 5. Reconcile cannot repair this -- the slug IS the filename, a rename orphans the
    note from the candidate walk, and picking a survivor is `leads dedupe`'s job. So it refuses
    BOTH and names them, the shape index_by_slug/upsert/select_one already use."""
    v = _v(tmp_path)
    a = _seed(v, os.path.join(ACTIVE_SUBDIR, "A - Twin.md"), role="Twin", status="shortlist")
    b = _seed(v, os.path.join(ARCHIVE_SUBDIR, "A - Twin.md"), role="Twin", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert list(rep["ambiguous"]) == ["A - Twin"]
    assert len(rep["ambiguous"]["A - Twin"]) == 2
    assert os.path.isfile(a) and os.path.isfile(b), "a twin was moved"


def test_a_destination_collision_refuses_that_note_and_continues_the_sweep(tmp_path):
    """Per-note isolation, and NEVER a numeric suffix: the filename is the slug, so a suffixed
    move changes the lead's identity and orphans it from the next scrape.

    The blocker is a NON-LEAD file, and that is the only way this arm is reachable. `_slug_for` is
    the basename, so a LEAD note blocking the destination shares the mover's slug and the
    `ambiguous` arm consumes both first -- an earlier draft of this test seeded exactly that and
    measured `collisions == []`, asserting an outcome its own fixture made unreachable."""
    v = _v(tmp_path)
    _seed(v, os.path.join(ACTIVE_SUBDIR, "A - Clash.md"), role="Clash", status="dismiss")
    blocker = os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Clash.md")
    os.makedirs(os.path.dirname(blocker), exist_ok=True)
    with open(blocker, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: prep\n---\nnot a lead\n")
    _seed(v, "B - Fine.md", company="B", role="Fine", status="shortlist")
    rep = v.reconcile_layout(apply=True)
    # PRECONDITION: if a future change re-routes the mover into the twins arm, say so loudly
    # rather than passing vacuously on an empty collisions list.
    assert rep["ambiguous"] == {}, "the fixture no longer reaches the collision arm"
    assert rep["collisions"] == [("A - Clash", os.path.join(ARCHIVE_SUBDIR, "A - Clash.md"))]
    assert open(blocker, encoding="utf-8").read() == "---\ntitle: prep\n---\nnot a lead\n"
    assert not os.path.exists(os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Clash.1.md"))
    assert len(rep["moves"]) == 1, "the sweep stopped at the collision"


def test_a_move_oserror_is_isolated_and_the_sweep_continues(tmp_path, monkeypatch):
    v = _v(tmp_path)
    _seed(v, "A - Boom.md", role="Boom", status="shortlist")
    _seed(v, "B - Fine.md", company="B", role="Fine", status="shortlist")
    from sluice.core import vault as vaultmod
    real = vaultmod._reserve_and_move

    def flaky(src, dest_dir, base, **kw):
        if "Boom" in base:
            raise OSError(13, "Permission denied")
        return real(src, dest_dir, base, **kw)

    monkeypatch.setattr(vaultmod, "_reserve_and_move", flaky)
    rep = v.reconcile_layout(apply=True)
    assert [s for s, _ in rep["skipped"]] == ["A - Boom"]
    assert len(rep["moves"]) == 1


def test_a_flat_layout_reconciles_nothing(tmp_path):
    """Decision 7, and it lives in the STORE, not the CLI. Under flat there is no layout to
    reconcile against, and FLATTENING would drag every lead out of the user's own subfolders --
    decision 4 pointed the wrong way. So the pass reports its layout and does nothing at all: not
    even the `user_filed` noise a sweep would otherwise produce for a note in `Research/`."""
    v = _v(tmp_path, layout="")
    src = _seed(v, os.path.join("Research", "A - Filed.md"), role="Filed", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["layout"] == ""
    assert rep["moves"] == [] and rep["user_filed"] == [] and rep["in_place"] == 0
    assert os.path.isfile(src)


def test_an_archived_loser_is_never_a_reconcile_source(tmp_path):
    """#81. `_merged/` is pruned from the scan set, so read_leads never returns an archived loser
    -- and reconcile must not reach one by any other route either, or a lead a human merged away
    returns to the active view."""
    v = _v(tmp_path)
    loser = _seed(v, os.path.join("_merged", "A - Gone.md"), role="Gone", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == [] and rep["user_filed"] == []
    assert os.path.isfile(loser)
    assert not os.path.exists(os.path.join(v.leads_dir, ARCHIVE_SUBDIR))


def test_a_non_lead_file_is_never_moved(tmp_path):
    """A user's interview-prep note carries neither company nor role, so read_leads skips it and
    reconcile never sees it. Asserted because the file sits in a MANAGED folder, where a sweep
    that walked the directory instead of read_leads would pick it up."""
    v = _v(tmp_path)
    path = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Prep.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: prep\n---\nnotes\n")
    v.reconcile_layout(apply=True)
    assert os.path.isfile(path)


def test_a_move_that_races_a_status_write_is_reported_by_the_run_that_caused_it(
        tmp_path, monkeypatch):
    """The never-clobber RESIDUAL, made loud. `_cas_write` re-reads for freshness and then
    `_atomic_write` does `os.replace(tmp, path)`; a move landing in that window RE-CREATES the
    source path, leaving two notes at one basename -- one slug, so `upsert` refuses that lead for
    good with both `last_seen` frozen. Reconcile cannot prevent it (no portable stdlib
    atomic-conditional-rename exists), but the run that caused it must NAME it rather than leaving
    a later ingest to surface it as an unexplained refusal.

    The twin has to appear DURING the sweep. Seeding both up front is consumed by the up-front
    `index_by_slug` and witnesses nothing about the post-sweep re-read, while looking exactly like
    a passing test."""
    v = _v(tmp_path)
    src = _seed(v, "A - Raced.md", role="Raced", status="shortlist")
    from sluice.core import vault as vaultmod
    real = vaultmod._reserve_and_move

    def racing_move(s, dest_dir, base, **kw):
        dest = real(s, dest_dir, base, **kw)
        # Exactly what a concurrent `_atomic_write`'s os.replace(tmp, path) does when it lands
        # after the move: the source path exists again, holding the racer's edit.
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: Example Ltd\nrole: Raced\nstatus: applied\n---\nbody\n")
        return dest

    monkeypatch.setattr(vaultmod, "_reserve_and_move", racing_move)
    rep = v.reconcile_layout(apply=True)
    assert len(rep["moves"]) == 1, "the move itself must still have happened"
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Raced.md"))
    assert os.path.isfile(src), "the fixture did not reproduce the resurrected source path"
    assert "A - Raced" in rep["ambiguous"], "the post-sweep re-read did not report the race"
