"""How each state file behaves when it cannot be read (#80).

`docs/ARCHITECTURE.md` states the convention: RAISE when a silent empty is irreversible,
WARN when it is recoverable but discards an explicit human decision, SILENT when the value
is derived and rebuilds itself. This file is the executable half of that.

It exists because a review round found SIX production behaviour changes shipped with zero
rows between them: a locked database reported as corruption with a destructive remedy, a
dangling symlink read as "no history yet", and, worst, `enable`/`disable` silently going
back to the warning variant and rebuilding the operator's overlay from an empty set. None
of it was hypothetical; none of it was covered.

Where a row explains itself, that explanation was run, not reasoned.
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


@pytest.mark.parametrize("loader,exc,fragment", [
    # SeenDb says WHICH state this is; `_load_seen` has no message of its own, so it is
    # pinned on the type alone. `raises(Exception)` for both passed with SeenDb's message
    # emptied, which is the half that tells a user what to fix.
    #
    # `FileNotFoundError`, not `sqlite3.DatabaseError`: detection moved into
    # `core.paths.absent`, shared with `DeadLetterDb`, because writing it twice let the
    # EACCES fix and the dangling-ANCESTOR guard each land on one store and miss the
    # other. The two stores now raise the SAME type for the same shape, which is the
    # point; nothing in `sluice/` caught the old type, and this one is still an OSError.
    # The consequence clause is pinned separately below, because sharing the detector is
    # exactly how a message turns generic without anything going red.
    (lambda p: SeenDb(str(p)).load(), FileNotFoundError, "symlink"),
    # FileNotFoundError's str carries the path, so this is a free non-vacuous pin --
    # `fragment=""` made the assertion below a tautology.
    (lambda p: _load_seen(str(p)), OSError, "store.db"),
], ids=["SeenDb.load", "_load_seen"])
def test_a_dangling_symlink_is_not_an_absent_store(tmp_path, loader, exc, fragment):
    # Both dedup loaders use `lexists`. With `exists` a broken link lands in the MISSING
    # arm -- the one state the guard exists to separate -- and the writer then creates or
    # overwrites through it.
    link = tmp_path / "store.db"
    os.symlink(str(tmp_path / "nothere.db"), link)
    with pytest.raises(exc) as e:
        loader(link)
    assert fragment in str(e.value)
    # NB no "and the target was not created" assertion here. The first version had one;
    # it could never fire, because both loaders return before any connect or open. The
    # property that IS load-bearing is that a broken link raises instead of reading as
    # "no history yet", which is what the raises() above pins.


@pytest.mark.parametrize("build,what,why", [
    (lambda p: SeenDb(str(p)).load(), "dedup database", "empty dedup set"),
    (lambda p: __import__("sluice.track.deadletter", fromlist=["DeadLetterDb"])
     .DeadLetterDb(str(p)).open_entries(), "dead-letter store", "no one has acted on"),
], ids=["SeenDb", "DeadLetterDb"])
def test_each_store_still_says_what_refusing_costs(tmp_path, build, what, why):
    """Detection is shared; the MESSAGE is not, and this is what stops that drifting.

    Both stores now route through `core.paths.absent`, which is the fix for the same
    guard being written twice and diverging twice. The risk it introduces is the opposite
    one: a single generic message that names neither the state nor the consequence.
    Emptying either store's `what=`/`why=` leaves the shared detector working perfectly
    and every other row green.
    """
    link = tmp_path / "store.db"
    os.symlink(str(tmp_path / "nothere.db"), link)
    with pytest.raises(OSError) as e:
        build(link)
    assert what in str(e.value), "the message does not say WHICH store this is"
    assert why in str(e.value), "the message does not say what an empty read would cost"


# ── the migration remedy has to name every file it carries ───────────────────

def test_the_refusal_names_the_sidecars_that_must_move_with_the_store(
        monkeypatch, tmp_path, caplog):
    """`.lastrun` and the #49 dead-letter store are derived from `seen_db` by string
    concatenation, so a remedy naming only the database orphans both -- silently.

    Two rows below execute the remedy and its ordering; this one only checks the text.
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


def test_the_printed_remedy_survives_a_state_root_with_a_space(monkeypatch, tmp_path):
    """`shlex.quote` on every operand, pinned.

    Deleting all three quote calls left the whole suite green: `tmp_path` never contains
    a space, so the quoting is a no-op everywhere the suite otherwise looks. A home
    directory with a space in it is ordinary, and unquoted the command silently means
    something else.
    """
    import subprocess

    root = tmp_path / "Some One" / "st ate"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    names = ("track-seen.db", "track-seen.db.lastrun", "track-seen.db.deadletter.db")
    for n in names:
        (tmp_path / n).write_text(n, encoding="utf-8")

    cmd = _refusal_remedy()
    assert cmd
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, f"unquoted operands broke the remedy: {r.stderr.strip()}"
    assert sorted(p.name for p in (root / "sluice").iterdir()) == sorted(names)


def test_the_remedy_does_not_clobber_a_companion_at_the_destination(
        monkeypatch, tmp_path):
    """A companion already at the destination must STOP the chain, not be skipped.

    `mv -n` was the first attempt and is wrong: it skips and exits 0, so the chain
    carries on and moves the store anyway -- leaving an old store beside a foreign
    watermark, `exists(legacy)` now false, and the refusal disarmed for good. A skip is
    not an interruption. Asserting only that the destination file survived could not see
    that; the properties that matter are that the chain HALTED and the store stayed put.
    """
    import subprocess

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    for n in ("track-seen.db", "track-seen.db.lastrun"):
        (tmp_path / n).write_text("OLD", encoding="utf-8")
    dest = root / "sluice"
    dest.mkdir(parents=True)
    (dest / "track-seen.db.lastrun").write_text("NEWER", encoding="utf-8")

    cmd = _refusal_remedy()
    assert cmd
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode != 0, "a destination collision must stop the chain, not skip it"
    assert (dest / "track-seen.db.lastrun").read_text(encoding="utf-8") == "NEWER"
    assert (tmp_path / "track-seen.db").exists(), "the store moved after a halted step"
    assert not (dest / "track-seen.db").exists()
    assert _refusal_remedy() is not None, "the refusal was disarmed by a partial run"


def test_the_state_directory_the_remedy_creates_stays_private(monkeypatch, tmp_path):
    """`mkdir -p` then an explicit `chmod 700`.

    The same directory holds the OAuth token, and `_write_token`'s
    `makedirs(mode=0o700, exist_ok=True)` no-ops once it exists -- so the parent's mode
    has to be set by whoever gets there first. `mkdir -m` cannot do it: that flag applies
    only to directories mkdir CREATES, and by the time a user runs this the directory
    usually exists already, which is why this row plants it first.
    """
    import stat
    import subprocess

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    (tmp_path / "track-seen.db").write_text("x", encoding="utf-8")

    # The directory ALREADY EXISTS at 0755 -- the ordinary case, since six plain
    # `makedirs(exist_ok=True)` writers create it. `mkdir -p -m 700` applies its mode
    # only to directories it creates, so it was a no-op here and this row could not fail
    # until it planted the directory first.
    (root / "sluice").mkdir(parents=True)
    os.chmod(root / "sluice", 0o755)

    old = os.umask(0o022)
    try:
        cmd = _refusal_remedy()
        assert cmd
        subprocess.run(cmd, shell=True, check=True, cwd=tmp_path)
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(root / "sluice").st_mode)
    assert mode == 0o700, f"the remedy left the token's parent at {oct(mode)}"



# Every public method that READS the store. `record` is excluded because it creates the
# store deliberately, which is its job.
_DEADLETTER_READERS = {
    "open_entries": lambda db: db.open_entries(),
    "bump_surfaced": lambda db: db.bump_surfaced(),
    "clear_lead": lambda db: db.clear_lead("x"),
    "clear_id": lambda db: db.clear_id("x"),
    "check_reachable": lambda db: db.check_reachable(),
}


def test_the_dead_letter_reader_roster_is_complete():
    """The roster above is a hand-list, so this pins it against the real class.

    Its predecessor's docstring claimed "a fifth reader added without the check is
    caught". Measured: adding a fifth reader on `os.path.exists` left the suite green,
    because four lambdas are not an enumeration. This is.
    """
    import inspect

    from sluice.track.deadletter import DeadLetterDb

    public = {n for n, _ in inspect.getmembers(DeadLetterDb, inspect.isfunction)
              if not n.startswith("_")}
    assert public - {"record"} == set(_DEADLETTER_READERS), (
        "DeadLetterDb's public readers changed; every one must be checked for the "
        f"dangling-store guard. Class has {sorted(public)}")


@pytest.mark.parametrize("name", sorted(_DEADLETTER_READERS), ids=sorted(_DEADLETTER_READERS))
def test_every_dead_letter_reader_refuses_a_dangling_store(tmp_path, name):
    """All of them, not just the one a review happened to name.

    The guard first landed on `open_entries` alone; the other three kept following the
    symlink and quietly did nothing -- `clear_*` reporting success having cleared nothing.
    """
    from sluice.track.deadletter import DeadLetterDb

    link = tmp_path / "track-seen.db.deadletter.db"
    os.symlink(str(tmp_path / "nothere.db"), link)
    with pytest.raises(OSError) as e:
        _DEADLETTER_READERS[name](DeadLetterDb(str(link)))
    assert "symlink" in str(e.value)


@pytest.mark.parametrize("depth", [1, 2], ids=["parent", "grandparent-production-layout"])
@pytest.mark.parametrize("name", sorted(_DEADLETTER_READERS), ids=sorted(_DEADLETTER_READERS))
def test_every_dead_letter_reader_refuses_a_dangling_ANCESTOR(tmp_path, name, depth):
    """A broken link ON THE WAY to the store is the same silent empty as a broken store.

    `os.lstat(store)` raises ENOENT for this exactly as it does for a genuinely-absent
    store, so before `_broken_ancestor` every reader read it as "first run": measured,
    `open_entries` returned `[]` and the entire un-actioned backlog vanished.

    BOTH DEPTHS, and the second one is the one that matters: production is
    `<XDG state>/sluice/track-seen.db.deadletter.db`, so the link a user actually
    relocates is the GRANDparent. With only the depth-1 row, replacing the walk's
    step-up branch with `return None` -- reducing the loop to a single-parent check --
    left the whole suite green while a depth-2 dangling link still returned `[]`.
    """
    from sluice.track.deadletter import DeadLetterDb

    os.symlink(str(tmp_path / "moved-away"), str(tmp_path / "state"))
    store = tmp_path / "state"
    for _ in range(depth - 1):
        store = store / "sluice"
    store = store / "track-seen.db.deadletter.db"
    with pytest.raises(OSError) as e:
        _DEADLETTER_READERS[name](DeadLetterDb(str(store)))
    assert "symlink" in str(e.value)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root traverses a 0o000 directory, so this route cannot be staged")
def test_an_unreadable_dead_letter_store_is_not_an_absent_one(tmp_path):
    """`os.path.lexists` returns False on ANY OSError, so a store under a directory the
    user cannot traverse read as "absent" and the backlog came back empty -- the same
    silent empty by a different route. `lstat` distinguishes them.
    """
    from sluice.track.deadletter import DeadLetterDb

    locked = tmp_path / "locked"
    locked.mkdir()
    store = locked / "track-seen.db.deadletter.db"
    store.write_bytes(b"")
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(PermissionError):
            DeadLetterDb(str(store)).open_entries()
    finally:
        os.chmod(locked, 0o755)


# What each reader returns when the store is genuinely absent. Keyed by the SAME roster,
# with the completeness assertion below, so a new reader cannot be added to one and
# forgotten in the other -- which is what let `check_reachable` slip past the hand-list
# this replaced (it named four readers and omitted the fifth).
_MISSING_STORE_RESULT = {
    "open_entries": [],
    "bump_surfaced": None,
    "clear_lead": 0,
    "clear_id": 0,
    "check_reachable": None,
}


def test_the_missing_store_expectations_cover_every_reader():
    assert set(_MISSING_STORE_RESULT) == set(_DEADLETTER_READERS)


@pytest.mark.parametrize("name", sorted(_DEADLETTER_READERS), ids=sorted(_DEADLETTER_READERS))
def test_every_dead_letter_reader_still_tolerates_a_missing_store(tmp_path, name):
    # The other arm: absent is the ordinary first-run state for all of them. Asserting the
    # store is still absent afterwards is the #80 half -- a read that CREATES the file
    # disarms the relocation refusal for every later run.
    #
    # KEEP the no-create line even though no SINGLE-edit mutant kills it uniquely (every
    # candidate raises on the return value first, so that assertion fires before this one).
    # It is the only place the #80 disarm property is stated as a check rather than as
    # prose, and the two-edit mutant that reaches it -- drop a reader's `_absent` guard AND
    # make `_open` create -- is exactly the drift it exists to catch.
    from sluice.track.deadletter import DeadLetterDb

    store = tmp_path / "nope.db"
    assert _DEADLETTER_READERS[name](DeadLetterDb(str(store))) == _MISSING_STORE_RESULT[name]
    assert not os.path.lexists(store), f"{name} created the store on a read"


@pytest.mark.parametrize("name", sorted(_DEADLETTER_READERS), ids=sorted(_DEADLETTER_READERS))
def test_a_missing_parent_chain_is_still_just_a_first_run(tmp_path, name):
    # The counterpart to the dangling-ancestor row: `<state>/sluice/` legitimately does
    # not exist before the first `record`, so the ancestor walk must step PAST missing
    # directories and only judge one that exists without resolving. A guard that refused
    # here would fail every genuine first run.
    from sluice.track.deadletter import DeadLetterDb

    store = tmp_path / "not" / "created" / "yet" / "track-seen.db.deadletter.db"
    assert _DEADLETTER_READERS[name](DeadLetterDb(str(store))) == _MISSING_STORE_RESULT[name]

