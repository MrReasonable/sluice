"""How each state file behaves when it cannot be read (#80).

`docs/ARCHITECTURE.md` states the convention: RAISE when a silent empty is irreversible,
WARN when it is recoverable but discards an explicit human decision, SILENT when the value
is derived and rebuilds itself. This file is the executable half of that.

It exists because a review round found SIX production behaviour changes shipped with zero
rows between them: a locked database reported as corruption with a destructive remedy, a
dangling symlink read as "no history yet", and, worst, `enable`/`disable` silently going
back to the warning variant and rebuilding the operator's overlay from an empty set. None
of it was hypothetical; none of it was covered.

Each row below names the mutant it kills. Where a row says it was witnessed, it was run;
two rows in the first version of this file were NOT, and both are noted where they sit.
"""
import ast
import os
import pathlib
import sqlite3

import pytest

from sluice import cli
from sluice.core.app import _load_seen
from sluice.core.seendb import SeenDb


# ── the disabled-sources overlay: raising core, warning wrapper ──────────────

@pytest.fixture
def overlay(monkeypatch, tmp_path):
    p = tmp_path / "sluice_disabled.json"
    monkeypatch.setenv("SLUICE_DISABLED", str(p))
    return p


def test_a_missing_overlay_means_nothing_disabled(overlay):
    assert cli._load_disabled() == set()


def test_a_valid_overlay_round_trips(overlay):
    overlay.write_text('["reed", "cord"]', encoding="utf-8")
    assert cli._load_disabled() == {"reed", "cord"}


@pytest.mark.parametrize("body,why", [
    ("{not json", "malformed JSON"),
    ('{"reed": true}', "an object, whose set() would be its KEYS"),
    ('"reed"', "a string, whose set() would be its CHARACTERS"),
    ('["reed", 1]', "a non-string entry"),
    ('["reed", ["cord"]]', "a nested list"),
    ('["reed", null]', "a null entry"),
])
def test_an_unusable_overlay_raises(overlay, body, why):
    """RAISES, because `enable`/`disable` read-modify-write this file.

    The shape checks are not pedantry: `set(json.load(f))` over a dict yields its keys and
    over a string yields its characters, so without them a malformed overlay becomes a
    plausible-looking set of source ids rather than an error. That is the bug class
    `_merge_denylist` already exists for in track/config.py.
    """
    overlay.write_text(body, encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        cli._load_disabled()


def test_a_dangling_overlay_symlink_raises(overlay, tmp_path):
    # `lexists`, not `exists`: a broken link is not an absent file, and treating it as one
    # sends _save_disabled writing THROUGH the link.
    os.symlink(str(tmp_path / "nothere.json"), overlay)
    with pytest.raises(OSError) as e:
        cli._load_disabled()
    # On the MESSAGE, not just the type. `open()` already raises FileNotFoundError, an
    # OSError subclass, so `pytest.raises(OSError)` alone passes with the entire guard
    # deleted -- measured. The guard's whole value is saying which of the two states this
    # is, so that is what gets asserted.
    assert "symlink" in str(e.value)


def test_the_read_only_wrapper_warns_and_continues(overlay, caplog):
    overlay.write_text("{not json", encoding="utf-8")
    assert cli._disabled_or_warn() == set()
    assert "ENABLED" in caplog.text, "the warning must name the CONSEQUENCE, not just the file"


def test_the_read_only_wrapper_still_returns_a_good_overlay(overlay):
    overlay.write_text('["reed"]', encoding="utf-8")
    assert cli._disabled_or_warn() == {"reed"}


def test_every_overlay_writer_reads_through_the_raising_loader():
    """The safety rule, pinned. Nothing else pins it.

    `_load_disabled` raises and `_disabled_or_warn` swallows; which one a caller picks is
    the whole safeguard, and picking wrong is SILENT. A function that writes the overlay
    back after a swallowed read rebuilds it from an empty set and destroys every decision
    the operator ever made, printing success -- measured before this guard existed.

    Enumerated from the AST rather than named, so a new writer is covered on the day it
    lands rather than when someone remembers this rule.
    """
    src = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    writers, checked = [], 0
    for node in ast.walk(tree):
        # AsyncFunctionDef too, and attribute calls as well as bare names: an `async def`
        # writer and a `mod._save_disabled()` writer both evaded the first version of this
        # guard while `checked >= 2` stayed satisfied.
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = {getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                  for c in ast.walk(node) if isinstance(c, ast.Call)}
        if "_save_disabled" not in called:
            continue
        checked += 1
        if "_disabled_or_warn" in called or "_load_disabled" not in called:
            writers.append(node.name)
    assert checked >= 2, (
        f"found only {checked} overlay writers -- expected enable and disable, so this "
        "guard is not looking at what it thinks it is")
    assert not writers, (
        "these functions write the disabled-sources overlay but do not read it through "
        f"the RAISING loader, so a bad read is rebuilt as the new truth: {writers}")


# ── the dedup stores: raise, and say something useful ────────────────────────

def test_a_locked_database_is_not_reported_as_corruption(tmp_path, monkeypatch):
    """`OperationalError` SUBCLASSES `DatabaseError`.

    So a corruption arm placed first swallows "database is locked" -- two overlapping
    ingest runs, a cron beside a manual one -- and tells the user to move or delete a
    perfectly good dedup store, causing exactly the irreversible loss the message warns
    about. The type must survive too, so a caller can tell a retryable lock from a dead
    file.
    """
    p = tmp_path / "seen.db"
    db = sqlite3.connect(str(p))
    try:
        db.execute("CREATE TABLE seen_jobs (url TEXT PRIMARY KEY, scanned_at TEXT)")
        db.execute("INSERT INTO seen_jobs VALUES ('u', 't')")
        db.commit()
    finally:
        db.close()

    # `timeout=0` on the READER, patched in for this row only. `SeenDb.load` calls
    # `sqlite3.connect` untimed, so it inherits the 5s default busy timeout and this test
    # alone cost 5.2s -- a third of a suite CLAUDE.md documents as sub-second. The lock is
    # acquired INSIDE the try, so a SQLITE_BUSY on the way in cannot leak the connection.
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: real_connect(*a, **{**k, "timeout": 0}))
    holder = real_connect(str(p), isolation_level=None, timeout=0)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError) as e:
            SeenDb(str(p)).load()
        assert "Move or delete" not in str(e.value), \
            "a transient lock must not be given a destructive remedy"
    finally:
        holder.close()


def test_a_corrupt_database_names_the_file_and_a_remedy(tmp_path):
    p = tmp_path / "seen.db"
    p.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError) as e:
        SeenDb(str(p)).load()
    assert str(p) in str(e.value) and "SEEN_DB" in str(e.value)


@pytest.mark.parametrize("loader", [
    lambda p: SeenDb(str(p)).load(),
    lambda p: _load_seen(str(p)),
], ids=["SeenDb.load", "_load_seen"])
def test_a_dangling_symlink_is_not_an_absent_store(tmp_path, loader):
    # Both dedup loaders use `lexists`. With `exists` a broken link lands in the MISSING
    # arm -- the one state the guard exists to separate -- and the writer then creates or
    # overwrites through it.
    link = tmp_path / "store.db"
    os.symlink(str(tmp_path / "nothere.db"), link)
    with pytest.raises(Exception):
        loader(link)
    # NB no "and the target was not created" assertion here. The first version had one;
    # it could never fire, because both loaders return before any connect or open. The
    # property that IS load-bearing is that a broken link raises instead of reading as
    # "no history yet", which is what the raises() above pins.


# ── the migration remedy has to name every file it carries ───────────────────

def test_the_refusal_names_the_sidecars_that_must_move_with_the_store(
        monkeypatch, tmp_path, caplog):
    """`.lastrun` and the #49 dead-letter store are derived from `seen_db` by string
    concatenation, so a remedy naming only the database orphans both -- silently.

    Measured before this guard: running the printed command verbatim on a 30-day-old
    install moved the database and left both companions behind, after which receipts in
    the gap were never re-queried and the proposal backlog read as empty.
    """
    from sluice.core import paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in ("track-seen.db", "track-seen.db.lastrun", "track-seen.db.deadletter.db"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var=None, config_value="", kind="state",
                      name="track-seen.db", fatal=True)
    msg = str(e.value)
    for suffix in (".lastrun", ".deadletter.db"):
        assert f"track-seen.db{suffix}" in msg, f"the remedy orphans {suffix}"


def test_the_refusal_names_only_the_companions_that_exist(monkeypatch, tmp_path):
    # A copy-pasteable remedy: naming a file the user never had makes the whole command
    # fail halfway, leaving the migration half-done.
    from sluice.core import paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "track-seen.db").write_text("x", encoding="utf-8")
    (tmp_path / "track-seen.db.lastrun").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var=None, config_value="", kind="state",
                      name="track-seen.db", fatal=True)
    assert ".lastrun" in str(e.value)
    assert ".deadletter.db" not in str(e.value)


def test_a_store_with_no_sidecars_gets_a_single_move(monkeypatch, tmp_path):
    from sluice.core import paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "seen.db").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var=None, config_value="", kind="state",
                      name="seen.db", fatal=True)
    assert str(e.value).count("mv ") == 1


def test_the_sidecar_table_matches_what_the_code_actually_derives():
    """The table is a hand-list; this pins it against the real derivations.

    `core/app.py` appends `.lastrun`, and `track/deadletter.py` owns the other suffix.
    Reading the suffix from that module rather than repeating it means a rename there
    reddens here instead of silently orphaning the file at migration time.
    """
    from sluice.core import paths
    from sluice.track.deadletter import _DEADLETTER_SUFFIX

    app_src = (pathlib.Path(__file__).resolve().parent.parent
               / "sluice" / "core" / "app.py").read_text(encoding="utf-8")
    assert 'seen_db + ".lastrun"' in app_src, \
        "app.py no longer derives .lastrun this way -- the sidecar table may be stale"
    assert set(paths._SIDECARS["track-seen.db"]) == {".lastrun", _DEADLETTER_SUFFIX}


def test_ingest_run_refuses_on_an_unusable_overlay(overlay, monkeypatch, capsys):
    """`run` ACTS on the overlay, so it does not get the warning variant.

    A wrong answer here is not a misprinted status line: it scrapes the sources the
    operator explicitly disabled and writes their leads into the vault. It exits before
    building a fetcher, so nothing is scraped on the way to finding out.
    """
    from sluice.core.config import Config

    overlay.write_text("{not json", encoding="utf-8")

    class _Args:
        source, dry_run, sink, all = None, False, "vault", True

    monkeypatch.setattr(cli, "_selected",
                        lambda *a, **k: pytest.fail("selection ran despite a bad overlay"))
    assert cli.cmd_run(_Args(), Config()) == 1


def test_list_sources_still_only_warns(overlay, capsys):
    # The other half of the split: read-and-PRINT keeps the soft behaviour, so a corrupt
    # overlay does not stop someone inspecting what is registered.
    from sluice.core.config import Config

    overlay.write_text("{not json", encoding="utf-8")

    class _Args:
        health = False

    assert cli.cmd_list_sources(_Args(), Config()) == 0
    assert capsys.readouterr().out, "list-sources printed nothing"


def _refusal_remedy(name="track-seen.db"):
    """The shell command the relocation refusal prints, or None if it did not fire."""
    from sluice.core import paths
    try:
        paths.resolve(env_var=None, config_value="", kind="state", name=name, fatal=True)
        return None
    except RuntimeError as e:
        return str(e).split("run:  ")[1].split("   (")[0]


def test_the_printed_remedy_actually_runs(monkeypatch, tmp_path):
    """Pasted verbatim into a shell, it must succeed and move everything.

    It did not. Both fatal refusals fire BEFORE any writer, so the destination directory
    does not exist yet and a bare `mv` failed with "No such file or directory" -- exit 1,
    nothing moved, against a user who did exactly what they were told.
    """
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    names = ("track-seen.db", "track-seen.db.lastrun", "track-seen.db.deadletter.db")
    for n in names:
        (tmp_path / n).write_text(n, encoding="utf-8")

    cmd = _refusal_remedy()
    assert cmd, "the refusal did not fire, so there is no remedy to run"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, f"the printed remedy failed: {r.stderr.strip()}"

    landed = tmp_path / "state" / "sluice"
    assert sorted(p.name for p in landed.iterdir()) == sorted(names)
    assert not any((tmp_path / n).exists() for n in names), "something was left behind"


def test_the_remedy_moves_the_store_last_so_an_interruption_stays_armed(
        monkeypatch, tmp_path):
    """Order matters, and the dangerous order is the intuitive one.

    The legacy gate is `exists(legacy) and not exists(resolved)`, keyed on the STORE
    alone. Move it first and a chain that then fails leaves the companions orphaned AND
    silences the only notice that names them -- permanently. Moving it last means any
    interruption leaves the refusal armed, so the next run says so again.
    """
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for n in ("track-seen.db", "track-seen.db.lastrun", "track-seen.db.deadletter.db"):
        (tmp_path / n).write_text(n, encoding="utf-8")

    cmd = _refusal_remedy()
    steps = cmd.split(" && ")
    assert "track-seen.db.lastrun" not in steps[-1], \
        "a companion moves last; an interruption before it orphans that file silently"
    assert steps[-1].endswith("track-seen.db"), "the store must move last"

    # Run everything except the final step -- the interruption.
    subprocess.run(" && ".join(steps[:-1]), shell=True, check=True, cwd=tmp_path)
    assert _refusal_remedy() is not None, \
        "an interrupted migration silenced the refusal, orphaning the companions"
