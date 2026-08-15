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


def test_a_raced_move_leaves_the_store_refusing_not_updating(tmp_path, monkeypatch):
    """Pins `self._rescan_dirs()` after an applied sweep, which nothing else does.

    The comment above that line once called it hygiene, reasoning that "found ONCE where a fresh
    list finds TWICE needs two notes at one name, which this pass refuses rather than creates".
    The race arm CREATES exactly that -- so the reasoning was falsified by its own neighbour, and
    deleting the line left all 1997 tests green.

    Measured on ONE store instance in that state (`Sluice.store()` memoizes, so the facade plus a
    later pass reaches it). Shipped: `_locate` sees both paths and `upsert` REFUSES. With the
    re-derive deleted: the stale scan set omits the destination folder, `_locate` sees one, and
    `upsert` returns `merged` -- writing to the RESURRECTED source note while the real moved note
    is never touched and its `last_seen` freezes. That is a never-clobber outcome, so it gets a
    test rather than a comment."""
    from sluice.core.leads import Lead
    from sluice.core import vault as vaultmod

    v = _v(tmp_path)
    # WARM the scan-set cache first, through the public path. Without this the cache is None
    # before the sweep, so deleting `_rescan_dirs()` leaves it None and `_scan_dirs()` simply
    # walks fresh -- both answers agree and the test passes for a reason that has nothing to do
    # with the guard. (An earlier draft omitted it and SURVIVED the mutant, measured.) This
    # create leaves the cache holding [leads_dir] while Active/ exists but is not in it, which
    # is exactly the stale shape the sweep must repair.
    warm = Lead(source="test", search="q", title="Warm", company="B", url="", location="")
    assert v.upsert(warm).outcome == "created"
    assert v._scan_dirs_cache is not None, "the cache was not warmed"
    assert not any(d.endswith(ACTIVE_SUBDIR) for d in v._scan_dirs_cache), \
        "the cache already knows Active/; this fixture no longer reproduces the stale state"

    _seed(v, "A - Raced.md", role="Raced", status="shortlist")
    real = vaultmod._reserve_and_move

    def racing_move(src, dest_dir, base, **kw):
        dest = real(src, dest_dir, base, **kw)
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: A\nrole: Raced\nstatus: applied\nurl: \n---\nbody\n")
        return dest

    monkeypatch.setattr(vaultmod, "_reserve_and_move", racing_move)
    rep = v.reconcile_layout(apply=True)
    assert any(slug == "A - Raced" for slug, _, _ in rep["moves"]), \
        "the fixture did not reach the move path"

    # SAME instance -- the whole point is the cache the sweep just invalidated.
    raced = Lead(source="test", search="q", title="Raced", company="A", url="", location="")
    assert v.upsert(raced).outcome == "refused", (
        "the store resolved a raced twin against a stale scan set: it wrote to the resurrected "
        "note instead of refusing the ambiguous identity")


def test_a_symlinked_destination_is_refused_not_silently_filed(tmp_path):
    """A move into a SYMLINKED managed folder destroys the lead, silently and permanently.

    `_walk` keeps os.walk's followlinks=False, so a symlinked `Archive/` is NOT in the scan set:
    the moved note leaves read_leads AND _locate, every later scrape resolves the same link and
    refuses, and the lead is invisible to triage/cv/apply/track for good. Measured before the
    guard: moves=1, skipped=[], exit 0, ZERO log records, and the lead gone.

    `_warn_undescended_symlinks` does not cover it -- it recorded the link on the PRE-sweep read,
    when the target still held no note, so it never speaks again for that store. This pass is what
    invites subfolders at all, so it must not be the thing that files a lead out of existence."""
    v = _v(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, os.path.join(v.leads_dir, ARCHIVE_SUBDIR))
    src = _seed(v, "A - Doomed.md", role="Doomed", status="dismiss")

    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == [], "a lead was filed into a symlink, out of the scan set"
    assert [slug for slug, _ in rep["skipped"]] == ["A - Doomed"]
    assert os.path.isfile(src), "the note was moved despite the refusal"
    assert os.listdir(target) == [], "the note landed behind the symlink"
    # Still readable, which is the property the refusal protects.
    assert [n.slug for n in v.read_leads()] == ["A - Doomed"]


def test_a_file_named_like_a_managed_folder_is_skipped_not_reported_as_a_collision(tmp_path):
    """`os.makedirs(..., exist_ok=True)` raises FileExistsError when the path exists and is NOT a
    directory. With the makedirs inside the move's try -- whose first arm catches FileExistsError
    as a destination-name COLLISION -- a plain file named `Archive` made every dismissed lead
    report "Archive/<note> is taken -- merge or rename by hand", advice about a path that does not
    exist, while --apply exited 1 forever with the real cause never stated."""
    v = _v(tmp_path)
    with open(os.path.join(v.leads_dir, ARCHIVE_SUBDIR), "w", encoding="utf-8") as fh:
        fh.write("not a directory\n")
    _seed(v, "A - Done.md", role="Done", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["collisions"] == [], "a makedirs failure was misreported as a name collision"
    assert [slug for slug, _ in rep["skipped"]] == ["A - Done"]
    # The real cause, not "merge or rename by hand".
    assert "Archive" in rep["skipped"][0][1]
