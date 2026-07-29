import os
import sqlite3

import pytest

from sluice.core.leads import Lead
from sluice.core.seendb import SeenDb


def test_save_then_load_roundtrip(tmp_path):
    db = SeenDb(str(tmp_path / "seen.db"))
    db.save([Lead(source="s", search="x", title="t", url="https://a/1")])
    assert "https://a/1" in db.load()


def test_missing_db_loads_empty(tmp_path):
    assert SeenDb(str(tmp_path / "none.db")).load() == set()


def test_save_is_idempotent(tmp_path):
    db = SeenDb(str(tmp_path / "seen.db"))
    lead = Lead(source="s", search="x", title="t", url="https://a/1")
    db.save([lead])
    db.save([lead])
    assert db.load() == {"https://a/1"}


def test_a_corrupt_db_raises_rather_than_reading_as_empty(tmp_path):
    """A silent empty dedup set is the #81 harm by another route.

    `except Exception: return set()` turned an unreadable dedup store into a full,
    silent dedup loss: every lead reads unseen, every human-merged duplicate is
    re-created, and a lead whose twin was already `applied` can produce a second
    application under the user's name -- reported as ordinary `created: N`. Refusing to
    start over a RELOCATED store (#80) while shrugging at an unreadable one is
    incoherent, so this is loud.
    """
    import sqlite3

    p = tmp_path / "seen.db"
    p.write_bytes(b"this is not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SeenDb(str(p)).load()


def test_an_existing_db_with_no_table_reads_as_empty(tmp_path):
    """...but this state is NOT corruption, and must stay tolerated.

    A 0-byte file is a valid empty sqlite database, and #80's own earlier bug left those
    behind (`sqlite3.connect` created one just by opening it). Raising here would turn
    that fix into a hard failure on the next run for precisely the people it is for.
    `save` creates the table.
    """
    p = tmp_path / "seen.db"
    p.write_bytes(b"")
    assert SeenDb(str(p)).load() == set()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root traverses a 0o000 directory, so this route cannot be staged")
def test_an_unreadable_db_raises_rather_than_reading_as_empty(tmp_path):
    """`os.path.exists` returns False on ANY OSError, so a store under a directory the
    user cannot traverse read as "absent" and the run proceeded with an EMPTY dedup set.

    `DeadLetterDb` was fixed for exactly this and this loader was missed, which is the
    worse of the two places to miss it: an empty dedup set re-creates every lead a human
    merged away and can mean a second application under their name (#81). Measured before
    the fix: `load()` returned `set()` with nothing said.
    """
    locked = tmp_path / "locked"
    locked.mkdir()
    store = locked / "seen.db"
    SeenDb(str(store)).save([])          # a real store, then made unreachable
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(OSError):
            SeenDb(str(store)).load()
    finally:
        os.chmod(locked, 0o755)


def test_reading_never_creates_the_store_when_it_vanishes_mid_check(tmp_path, monkeypatch):
    """The read path must not be able to CREATE, because a 0-byte file disarms the #80
    relocation refusal permanently -- `paths.resolve` only refuses while the resolved path
    does not exist.

    The existence check and the open are two syscalls and a plain `sqlite3.connect`
    creates on open, so a store removed between them left exactly that file behind. The
    window has to be forced open to be tested at all: pointing the loader at an
    already-absent path proves nothing, because the check returns before the connect is
    reached -- measured, that version stayed green with the no-create open reverted. So
    the removal happens INSIDE the check, which is what a real race does.
    """
    import sluice.core.seendb as mod

    store = tmp_path / "racy.db"
    SeenDb(str(store)).save([])
    assert store.exists()

    # Captured BEFORE patching, and the removal is one-shot: anything inside the hook
    # that consults the filesystem (`Path.exists` and friends all route through
    # `os.stat`) would re-enter it, and the first version of this test recursed to death
    # doing exactly that.
    real_stat, real_unlink, fired = os.stat, os.unlink, []

    def _stat_then_vanish(path, *a, **k):
        result = real_stat(path, *a, **k)
        if not fired and str(path) == str(store):
            fired.append(True)
            real_unlink(str(store))     # the window between the check and the open
        return result

    monkeypatch.setattr(mod.os, "stat", _stat_then_vanish)
    with pytest.raises(sqlite3.OperationalError):
        SeenDb(str(store)).load()
    assert not store.exists(), "a READ created the dedup store, disarming the #80 refusal"


@pytest.mark.parametrize("name,why", [
    ("se?en#db.db", "`?` and `#` would truncate the URI into a different file"),
    ("se en.db", "a space is not legal in a URI unencoded"),
    ("se%20en.db", "a literal % must not be read back as an escape"),
    ("séen.db", "non-ASCII must survive the round trip"),
])
def test_a_path_with_uri_metacharacters_still_loads(tmp_path, name, why):
    # The `mode=rw` open builds a URI, so an unquoted path would silently address a
    # DIFFERENT file -- or a query string -- rather than failing.
    store = tmp_path / name
    db = SeenDb(str(store))
    db.save([Lead(source="s", search="x", title="t", url="https://a/1")])
    assert "https://a/1" in db.load(), why
    assert store.exists(), f"{why}: the store was written somewhere else"


def test_a_path_with_a_leading_double_slash_still_loads(tmp_path):
    """A leading `//` is a legal POSIX path, and building a URI made it fatal.

    `resolve` hands `SEEN_DB` through unnormalised, so `SEEN_DB=//var/lib/sluice/seen.db`
    -- which a plain `sqlite3.connect` opened without complaint -- became `file:` +
    `//var/...`, where `var` parses as a URI AUTHORITY: `OperationalError: invalid uri
    authority: var`. A no-create open must not cost anyone a store that used to open.
    """
    real = tmp_path / "seen.db"
    SeenDb(str(real)).save([Lead(source="s", search="x", title="t", url="https://a/1")])
    doubled = "/" + str(real)          # //private/var/... -- same file, legal spelling
    assert doubled.startswith("//")
    assert "https://a/1" in SeenDb(doubled).load()
