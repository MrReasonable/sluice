import json
import os
import socket
import threading
from datetime import date
from pathlib import Path
import pytest
from sluice.triage.audit import AuditLog, render_rejected_note
from sluice.core.vault import Vault


def test_append_writes_one_json_line_per_entry(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    log.append({"slug": "a", "stage": "classify", "decision": "reject",
                "reason": "IC role", "ts": "2026-07-07"})
    log.append({"slug": "b", "stage": "judge", "verdict": "shortlist",
                "ts": "2026-07-07"})
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["reason"] == "IC role"


def test_read_recent_filters_by_age(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    log.append({"slug": "old", "ts": "2026-01-01"})
    log.append({"slug": "new", "ts": "2026-07-07"})
    recent = log.read_recent(30, clock=lambda: date(2026, 7, 8))
    assert [e["slug"] for e in recent] == ["new"]


def test_render_rejected_note_groups_rejects(tmp_path):
    v = Vault(str(tmp_path))
    entries = [
        {"slug": "a", "company": "Acme", "role": "Director", "url": "u1",
         "stage": "classify", "decision": "reject", "reason": "m-of-m",
         "score": 0, "ts": "2026-07-07"},
        {"slug": "b", "company": "Beta", "role": "Analyst", "url": "u2",
         "stage": "judge", "verdict": "dismiss", "reason": "weak fit",
         "score": 20, "ts": "2026-07-07"},
        {"slug": "c", "company": "Gamma", "role": "Analyst", "url": "u3",
         "stage": "judge", "verdict": "shortlist", "score": 80, "ts": "2026-07-07"},
    ]
    path = render_rejected_note(v, entries, "Job Applications/Rejected Leads Audit.md")
    text = open(path, encoding="utf-8").read()
    assert "Acme" in text and "Beta" in text
    assert "Gamma" not in text          # shortlist is not a reject
    assert "\u2014" not in text         # no em dashes


def test_checking_whether_the_log_can_be_appended_creates_nothing(tmp_path):
    # `core/paths.py`'s `_LEGACY` warns about a left-behind `./triage-audit.jsonl` only while
    # the new path does NOT exist, so a check that created the file or its directory would
    # silence that warning on every real run.
    log = AuditLog(str(tmp_path / "state" / "sluice" / "triage-audit.jsonl"))
    assert log.cannot_append() == ""
    assert not (tmp_path / "state").exists()


def test_an_ancestor_that_is_a_file_means_the_log_cannot_be_appended(tmp_path):
    # A regular FILE where a directory must be, rather than a chmod: the check has to hold for
    # root too, which a permission bit does not.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    log = AuditLog(str(blocker / "sluice" / "triage-audit.jsonl"))
    assert log.cannot_append()
    assert blocker.read_text(encoding="utf-8") == "not a directory\n"


def test_a_dotdot_after_a_missing_directory_is_refused_even_where_the_append_would_work(tmp_path):
    """Where such a `..` lands depends on the directories `append`'s `makedirs` creates before
    `open` resolves it, which the check does not work out, so it refuses the path: a run whose
    append would have worked stops, with this reason, and nothing is created."""
    log = AuditLog(str(tmp_path / "missing" / os.pardir / "triage-audit.jsonl"))
    assert "does not exist yet" in log.cannot_append()
    assert not (tmp_path / "missing").exists()
    # ...and the append it refused does work.
    log.append({"slug": "a", "ts": "2026-07-07"})
    assert (tmp_path / "triage-audit.jsonl").exists()


# Path shapes for `cannot_append`: what each of its clauses refuses, and what it must not.
_SHAPES = ["empty_path", "nul_path", "unencodable_path", "trailing_slash", "dot_suffix",
           "dotdot_suffix", "name_ending_in_dot", "hidden_log", "three_dots", "fifo_with_reader",
           "absent_log",
           "existing_log", "missing_parents",
           "relative_log_missing_dir",
           "file_ancestor", "executable_file_ancestor", "is_directory", "socket",
           "dangling_link", "dangling_link_relative", "dangling_link_two_hops",
           "dangling_link_missing_dir", "dangling_link_under_file", "dangling_link_ancestor",
           "relative_log_dangling_bare", "link_loop", "link_loop_three", "readonly_parent",
           "readonly_log", "readonly_link_target", "noexec_parent", "noexec_link_target",
           "pardir_past_missing_dir", "pardir_into_readonly_dir",
           "pardir_onto_directory_it_creates", "pardir_into_file", "pardir_after_existing_dir",
           "relative_leading_pardir", "relative_pardir_before_missing_dirs"]
# The shapes that take a permission bit away, which uid 0 writes through.
_PERMISSION_SHAPES = {"readonly_parent", "readonly_log", "readonly_link_target", "noexec_parent",
                      "noexec_link_target", "pardir_past_missing_dir", "pardir_into_readonly_dir"}


def _seat(base, shape, monkeypatch, request):
    """Build `shape` under `base` and return the audit-log path it describes."""
    if shape in ("empty_path", "nul_path", "unencodable_path"):
        # From a writable directory, so a read-only cwd cannot make the check refuse for the
        # wrong reason and hide a deleted clause. This table cannot see the empty clause
        # deleted, since the directory-name clause also refuses "": the reason assertion in
        # test_an_empty_audit_log_path_stops_the_run_before_any_lead_changes pins it.
        monkeypatch.chdir(base)
    if shape == "empty_path":
        # The literal empty string: `Path("")` normalises to ".", which is another shape.
        return ""
    if shape == "nul_path":
        # `open` refuses a NUL byte anywhere in the path, with ValueError rather than OSError.
        return "triage-audit" + chr(0) + ".jsonl"
    if shape == "unencodable_path":
        # A lone surrogate, which a POSIX file-system encoding refuses, so `open` raises before
        # the filesystem sees the name.
        return "triage-audit" + chr(0xD800) + ".jsonl"
    if shape == "trailing_slash":
        # Plain strings: `str(Path)` drops a trailing separator. `append` creates the missing
        # directory, then cannot open it as a file.
        return str(base / "state") + "/"
    if shape == "dot_suffix":
        return str(base / "state") + "/."
    if shape == "dotdot_suffix":
        return str(base / "state") + "/.."
    log = base / "triage-audit.jsonl"
    if shape == "name_ending_in_dot":
        # Legitimate names the directory-name clause must NOT refuse.
        log = base / "triage-audit."
    elif shape == "hidden_log":
        log = base / ".triage-audit.jsonl"
    elif shape == "three_dots":
        log = base / "..."
    elif shape == "fifo_with_reader":
        # Not refused, because a reader makes the append succeed. The read end stays open until
        # the row ends, so opening the FIFO to write never waits, whoever opens it first.
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs on this platform")
        os.mkfifo(log)
        reader = os.open(log, os.O_RDONLY | os.O_NONBLOCK)
        request.addfinalizer(lambda: os.close(reader))
    if shape == "existing_log":
        log.write_text("", encoding="utf-8")
    elif shape == "missing_parents":
        log = base / "a" / "b" / "triage-audit.jsonl"
    elif shape == "pardir_past_missing_dir":
        # A `..` after a directory that does not exist yet: `open` resolves it once `makedirs`
        # has created that directory, so the log lands in read-only `ro`, not in `child`.
        (base / "ro" / "child").mkdir(parents=True)
        (base / "ro").chmod(0o555)
        log = base / "ro" / "child" / "missing" / os.pardir / os.pardir / "triage-audit.jsonl"
    elif shape == "pardir_into_readonly_dir":
        # ...here it returns to `base` and lands in `ro`, which already exists.
        (base / "ro").mkdir()
        (base / "ro").chmod(0o555)
        log = base / "missing" / os.pardir / "ro" / "triage-audit.jsonl"
    elif shape == "pardir_onto_directory_it_creates":
        # ...and here `makedirs` creates `state/triage-audit.jsonl` as a directory, which is where
        # the log lands. A directory, not a permission bit, so this row holds for root.
        log = base / "state" / "triage-audit.jsonl" / os.pardir / "triage-audit.jsonl"
    elif shape == "pardir_into_file":
        # ...and here it returns to `base` and meets the FILE `blocker` where `makedirs` needs a
        # directory. No permission bit, so this row holds for root.
        (base / "blocker").write_text("not a directory\n", encoding="utf-8")
        log = base / "missing" / os.pardir / "blocker" / "triage-audit.jsonl"
    elif shape == "pardir_after_existing_dir":
        # Must append: `state` exists, so the filesystem resolves the `..` after it.
        (base / "state").mkdir()
        log = base / "state" / os.pardir / "triage-audit.jsonl"
    elif shape == "relative_leading_pardir":
        # Must append: a relative path climbing out of the cwd, as `TRIAGE_AUDIT=../x` reaches it.
        (base / "sub").mkdir()
        monkeypatch.chdir(base / "sub")
        log = Path(os.pardir) / "triage-audit.jsonl"
    elif shape == "relative_pardir_before_missing_dirs":
        # Must append: the `..` comes before `state/sluice`, which `append` creates, as a first
        # run with `TRIAGE_AUDIT=../state/sluice/triage-audit.jsonl` reaches it.
        (base / "sub").mkdir()
        monkeypatch.chdir(base / "sub")
        log = Path(os.pardir) / "state" / "sluice" / "triage-audit.jsonl"
    elif shape == "file_ancestor":
        (base / "blocker").write_text("not a directory\n", encoding="utf-8")
        log = base / "blocker" / "triage-audit.jsonl"
    elif shape == "relative_log_missing_dir":
        # A relative log path whose directory does not exist yet: `append` creates it.
        monkeypatch.chdir(base)
        log = Path("logs") / "triage-audit.jsonl"
    elif shape == "executable_file_ancestor":
        # An executable FILE where a directory must be, so `os.access(W_OK | X_OK)` on it says
        # yes and only the directory check refuses it.
        (base / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
        (base / "tool").chmod(0o755)
        log = base / "tool" / "triage-audit.jsonl"
    elif shape == "is_directory":
        log.mkdir()
    elif shape == "socket":
        # Bound by a RELATIVE name: a unix socket's path is length-limited, and tmp_path on
        # macOS is longer than the limit.
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("no unix sockets on this platform")
        monkeypatch.chdir(base)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(log.name)
    elif shape == "dangling_link":
        # `open(path, "a")` follows the link and creates its target. Measured: `os.access` on
        # the link reads the missing target and said "not writable".
        (base / "real").mkdir()
        log.symlink_to(base / "real" / "triage-audit.jsonl")
    elif shape == "dangling_link_relative":
        # A relative target resolves against the LINK's directory, never the cwd, so the row
        # runs from somewhere else.
        (base / "real").mkdir()
        (base / "elsewhere").mkdir()
        log.symlink_to(os.path.join("real", "triage-audit.jsonl"))
        monkeypatch.chdir(base / "elsewhere")
    elif shape == "dangling_link_missing_dir":
        log.symlink_to(base / "missing" / "triage-audit.jsonl")
    elif shape == "dangling_link_under_file":
        # An executable file, so `os.access(W_OK | X_OK)` on it says yes.
        (base / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
        (base / "tool").chmod(0o755)
        log.symlink_to(base / "tool" / "triage-audit.jsonl")
    elif shape == "dangling_link_two_hops":
        (base / "real").mkdir()
        (base / "hop").symlink_to(os.path.join("real", "triage-audit.jsonl"))
        log.symlink_to("hop")
    elif shape == "link_loop":
        # Resolves to itself: `open` gives up, and so must the check -- without raising.
        log.symlink_to(log.name)
    elif shape == "dangling_link_ancestor":
        # A state directory symlinked onto a volume that is not mounted: the walk must stop at
        # the link, which `lexists` sees and `exists` does not.
        (base / "state").symlink_to(base / "unmounted")
        log = base / "state" / "sluice" / "triage-audit.jsonl"
    elif shape == "relative_log_dangling_bare":
        # A relative log path with no directory part, linked to a file that does not exist yet.
        monkeypatch.chdir(base)
        os.symlink("real.jsonl", "triage-audit.jsonl")
        log = Path("triage-audit.jsonl")
    elif shape == "link_loop_three":
        # A loop longer than one link, so the walk can end on a link other than the log.
        os.symlink("a", str(log))
        os.symlink("b", str(base / "a"))
        os.symlink(log.name, str(base / "b"))
    elif shape == "readonly_parent":
        (base / "ro").mkdir()
        log = base / "ro" / "triage-audit.jsonl"
        (base / "ro").chmod(0o555)
    elif shape == "readonly_log":
        log.write_text("", encoding="utf-8")
        log.chmod(0o444)
    elif shape == "readonly_link_target":
        (base / "ro").mkdir()
        log.symlink_to(base / "ro" / "triage-audit.jsonl")
        (base / "ro").chmod(0o555)
    elif shape == "noexec_parent":
        # Writable but not searchable: creating a file in a directory needs both.
        (base / "nx").mkdir()
        log = base / "nx" / "triage-audit.jsonl"
        (base / "nx").chmod(0o666)
    elif shape == "noexec_link_target":
        (base / "nx").mkdir()
        log.symlink_to(base / "nx" / "triage-audit.jsonl")
        (base / "nx").chmod(0o666)
    return log


def _answer(log):
    """`cannot_append`'s answer, asked on a thread so a walk that never ends fails the row
    rather than hanging the suite; anything it raises is raised here."""
    box = {}

    def ask():
        try:
            box["reason"] = log.cannot_append()
        except BaseException as exc:          # re-raised below, on the test's own thread
            box["error"] = exc

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(10)
    if "error" in box:
        raise box["error"]
    assert "reason" in box, "cannot_append did not return within 10s"
    return box["reason"]


# Umasks under which `append` creates what it needs: the ordinary one, and the two that strip the
# owner's OWN write and search bits (#332). Under those, `os.makedirs` created a directory the log
# could not be opened in, and `open` created a log the next append could not open, while the
# check -- which reads permissions on what already exists -- said yes to both.
_UMASKS = [None, 0o200, 0o100]


@pytest.mark.parametrize("umask", _UMASKS, ids=lambda m: "umask-unchanged" if m is None
                         else f"umask-{m:04o}")
@pytest.mark.parametrize("shape", _SHAPES)
def test_the_check_says_an_append_would_fail_exactly_when_it_does(tmp_path, monkeypatch, request,
                                                                  shape, umask):
    """The check stands in for `append`, so it is pinned to `append` rather than to
    `os.access`: each shape is asked first, then really appended to, and the two must agree.
    The permission shapes are skipped under root, which writes through permission bits, so
    `os.access` rightly says the append would succeed, and on Windows, as the repo's other
    mode-bit tests are; every other shape holds for root.

    Appended TWICE, because a run appends a line per rejected lead, each after that lead's
    write: a log the first append creates and the second cannot open fails one lead late,
    which is the failure the check exists to stop. The umask is set only around the check and
    the appends, so `_seat` builds the same shape under every umask."""
    if shape in _PERMISSION_SHAPES and (
            os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0):
        pytest.skip("mode bits bind neither uid 0 nor Windows")
    log = AuditLog(str(_seat(tmp_path, shape, monkeypatch, request)))
    previous = None if umask is None else os.umask(umask)
    try:
        reason = _answer(log)
        try:
            log.append({"slug": "a", "ts": "2026-07-07"})
            log.append({"slug": "b", "ts": "2026-07-07"})
        except (OSError, ValueError):     # ValueError: a path `open` refuses outright
            appended = False
        else:
            appended = True
    finally:
        if previous is not None:
            os.umask(previous)
        for locked in ("ro", "nx"):
            if (tmp_path / locked).exists():
                (tmp_path / locked).chmod(0o755)
    assert (reason == "") is appended, (shape, reason)


def test_a_log_the_append_creates_reads_back_under_a_umask_that_strips_the_owners_read(tmp_path):
    """A run reads its own log back, mid-run to de-duplicate a role_type conflict and at its end
    to render the Rejected Leads note, so a log `append` creates must be readable by its owner
    too. Measured before #332: under umask 0400 the appends worked and `read_recent` raised."""
    log = AuditLog(str(tmp_path / "state" / "triage-audit.jsonl"))
    previous = os.umask(0o400)
    try:
        log.append({"slug": "a", "ts": "2026-07-07"})
        entries = log.read_recent(30, clock=lambda: date(2026, 7, 8))
    finally:
        os.umask(previous)
    assert [e["slug"] for e in entries] == ["a"]


def test_append_leaves_the_mode_of_a_log_that_already_exists_alone(tmp_path):
    """Only what `append` creates is given its owner's bits: an existing log keeps the mode it
    was given. Write-only, so a non-root append still succeeds and a change would show."""
    if os.name == "nt":
        pytest.skip("mode bits do not bind on Windows")
    path = tmp_path / "triage-audit.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o200)
    try:
        AuditLog(str(path)).append({"slug": "a", "ts": "2026-07-07"})
        assert path.stat().st_mode & 0o777 == 0o200
    finally:
        path.chmod(0o600)



# Each sibling writes somewhere the audit log's path passes through: into its own directory,
# or -- the dossier cache, under configured paths -- beside it under a parent that does not
# exist yet either.
def _usage_writes(root, monkeypatch):
    from sluice.core.usage import UsageLog
    UsageLog(str(root / "state" / "sluice" / "usage.jsonl")).append({"stage": "triage-judge"})
    return (root / "state" / "sluice" / "usage.jsonl").exists()


def _marker_writes(root, monkeypatch):
    from sluice.triage import reverdict
    return reverdict.acknowledge("vault:example", str(root / "state" / "sluice" / "ack.json"))


def _dossier_writes(root, monkeypatch):
    from sluice.core.dossier import DossierCache
    cache = DossierCache(str(root / "cache" / "sluice" / "dossiers"), ttl_days=7,
                         fetcher=lambda lead: {"jd": {"markdown": "x" * 900}})
    cache.get_or_build({"company": "Example Co", "role": "Analyst"})
    return bool(os.listdir(cache.dir))


def _fx_writes(root, monkeypatch):
    from sluice.core import fx

    class _Rates:
        def fetch(self, timeout):
            return {"EUR": 0.5}

    monkeypatch.setenv("SLUICE_FX_CACHE", str(root / "state" / "sluice" / "fx-rates.json"))
    monkeypatch.setattr(fx, "_cache", None)
    return fx.refresh(_Rates())


@pytest.mark.parametrize("umask", [0o200, 0o100], ids=["umask-0200", "umask-0100"])
@pytest.mark.parametrize("sibling", [_usage_writes, _marker_writes, _dossier_writes, _fx_writes],
                         ids=["usage-log", "marker", "dossier-cache", "fx-cache"])
def test_a_sibling_writer_creating_a_directory_leaves_the_log_appendable(
        tmp_path, monkeypatch, umask, sibling):
    """A triage run writes these into or beside the audit log's directory, most of them AFTER
    the pre-write check, so whichever writer creates a directory decides whether the audit
    append can land. Measured before #332's second half: under these umasks a sibling's plain
    `makedirs` left a directory the owner could not write or search, so the check -- yes before
    the sibling wrote -- refused after it, and the appends failed. For the writers that run
    after the check that is a lead written and its audit line lost; for the fx refresh, which
    runs before it, a run refused every time. So: yes before, yes after, the sibling's own
    write landed, and both appends land."""
    root = tmp_path / "x"
    log = AuditLog(str(root / "state" / "sluice" / "triage-audit.jsonl"))
    previous = os.umask(umask)
    try:
        before = _answer(log)
        wrote = sibling(root, monkeypatch)
        after = _answer(log)
        assert (before, after) == ("", "")
        assert wrote
        log.append({"slug": "a", "ts": "2026-07-07"})
        log.append({"slug": "b", "ts": "2026-07-07"})
    finally:
        os.umask(previous)
    lines = (root / "state" / "sluice" / "triage-audit.jsonl").read_text(encoding="utf-8")
    assert len(lines.splitlines()) == 2
