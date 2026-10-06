"""The sandbox guard in tests/conftest.py must exist and must see what it claims to.

Lives outside conftest.py because pytest does not collect conftest.py: an assertion there
would itself be inert (the same reason tests/test_hermeticity.py exists for the DNS guard).
"""
import os

import tests.conftest as guard


def test_the_watched_set_is_exactly_the_relative_path_defaults_in_sluice():
    # A scope pin, not a violation check: for a NEGATIVE guard, finding nothing is the
    # success case, so the guard must prove it enumerated what it meant to watch. A walk
    # that matched nothing would otherwise pass every test forever.
    assert guard._relative_path_defaults() == frozenset({
        "./cv-home", "./cv-host", "./cv-output", "./cv-served",
        "./scripts/cv_render_v2.py", "./vault",
    })


def test_a_container_side_path_is_excluded_by_name():
    # `apply.camofox_cv_dir` names a path inside the browser container, not on this host.
    assert "./cv-uploads" not in guard._relative_path_defaults()


def test_the_guard_records_a_write_under_a_watched_path(tmp_path, request):
    # The positive control: without it a guard whose hook never fires reads as a clean
    # suite.
    watched = str(tmp_path / "cv-output")
    guard._WATCHED.append(watched)
    try:
        os.makedirs(watched)
        with open(os.path.join(watched, "x.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        mine = [v for v in guard._VIOLATIONS if v[0] == request.node.nodeid]
        assert {v[1] for v in mine} >= {"os.mkdir", "open"}, mine
    finally:
        guard._WATCHED.remove(watched)
        # Clear this control's own records so the guard does not fail the control itself.
        guard._VIOLATIONS[:] = [v for v in guard._VIOLATIONS
                                if v[0] != request.node.nodeid]


def test_a_read_is_never_recorded(tmp_path, request):
    watched = str(tmp_path / "cv-served")
    os.makedirs(watched)
    with open(os.path.join(watched, "x.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    guard._WATCHED.append(watched)
    try:
        with open(os.path.join(watched, "x.txt"), encoding="utf-8") as fh:
            fh.read()
        assert not [v for v in guard._VIOLATIONS if v[0] == request.node.nodeid]
    finally:
        guard._WATCHED.remove(watched)


def test_the_real_watched_set_is_the_derived_defaults_anchored_at_the_session_cwd():
    # Scope in both directions on the REAL list: the controls above append their own path,
    # so an empty or stale `_WATCHED` would otherwise stay green.
    expected = {os.path.normpath(os.path.join(guard._SESSION_CWD, p))
                for p in guard._relative_path_defaults()}
    assert expected, "the derived set is empty: the guard would watch nothing"
    assert set(guard._WATCHED) == expected


def _record(request, event, path):
    """Drive the hook directly for `event`, then return and clear this test's records."""
    guard._guard_audit(event, (path,) if event == "sqlite3.connect" else (path, path))
    mine = [v for v in guard._VIOLATIONS if v[0] == request.node.nodeid]
    guard._VIOLATIONS[:] = [v for v in guard._VIOLATIONS if v[0] != request.node.nodeid]
    return mine


def test_a_rename_or_replace_destination_under_a_watched_path_is_recorded(tmp_path, request):
    watched = str(tmp_path / "cv-home")
    guard._WATCHED.append(watched)
    try:
        src = tmp_path / "src.txt"
        src.write_text("x", encoding="utf-8")
        os.makedirs(os.path.dirname(watched), exist_ok=True)
        os.mkdir(watched)                      # recorded; cleared below
        os.rename(src, os.path.join(watched, "a.txt"))
        (tmp_path / "src2.txt").write_text("x", encoding="utf-8")
        os.replace(tmp_path / "src2.txt", os.path.join(watched, "b.txt"))
        # CPython raises the `os.rename` audit event for os.replace too, so both calls
        # are witnessed through that one event name: two records, one per destination.
        renames = [v for v in guard._VIOLATIONS
                   if v[0] == request.node.nodeid and v[1] == "os.rename"]
        assert [os.path.basename(v[2]) for v in renames] == ["a.txt", "b.txt"], renames
    finally:
        guard._WATCHED.remove(watched)
        guard._VIOLATIONS[:] = [v for v in guard._VIOLATIONS
                                if v[0] != request.node.nodeid]


def test_a_sqlite_connect_to_a_watched_path_is_recorded(tmp_path, request):
    # Driven through the hook's own event rather than a real connect: a real one would
    # create the database file, which is the harm being witnessed.
    watched = str(tmp_path / "cv-served")
    guard._WATCHED.append(watched)
    try:
        mine = _record(request, "sqlite3.connect", os.path.join(watched, "x.db"))
        assert [v[1] for v in mine] == ["sqlite3.connect"], mine
    finally:
        guard._WATCHED.remove(watched)


def test_a_bytes_path_is_recorded_without_raising(tmp_path, request):
    watched = str(tmp_path / "cv-host")
    guard._WATCHED.append(watched)
    try:
        mine = _record(request, "os.mkdir", os.fsencode(watched))
        assert [v[1] for v in mine] == ["os.mkdir"], mine
    finally:
        guard._WATCHED.remove(watched)
