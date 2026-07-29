"""seen.db dedup store - reuses the existing scanner schema so dedup history
carries over the cutover unchanged."""
import datetime
import os
import sqlite3
from collections.abc import Iterable

from sluice.core.leads import Lead
from sluice.core.paths import resolve


class SeenDb:
    def __init__(self, path: str | None = None):
        # `path or resolve(...)`, in that order: an explicit constructor argument beats
        # the environment, or every SeenDb(str(tmp_path / ...)) in the suite would
        # retarget a developer's real dedup store, green throughout.
        #
        # Non-fatal HERE even though a relocated seen.db is the one path that refuses.
        # Refusing is a policy of the COMMAND, not of the store: `Sluice.ingest` -- the
        # only production construction -- resolves with fatal= keyed on whether this run
        # actually writes dedup state, and hands the result in. A store that refused on
        # its own would also refuse for every test and future caller that constructs one
        # directly. An explicit argument short-circuits resolution, so nothing is
        # resolved twice.
        self.path = path or resolve(env_var="SEEN_DB", config_value="",
                                    kind="state", name="seen.db")

    def load(self) -> set[str]:
        # MISSING db -> empty, WITHOUT creating it. `sqlite3.connect` creates a 0-byte
        # file just by opening, and that byte-less file is enough to disarm the #80
        # relocation refusal permanently: `paths.resolve` only refuses while the
        # resolved path does not exist. The reachable sequence was an ordinary cautious
        # one -- `ingest run --dry-run` (which resolves non-fatally, but still loads the
        # dedup set, correctly, or a dry run would lie about what it had seen) leaves
        # the empty file behind, and the REAL run that follows then proceeds with an
        # empty dedup set instead of refusing. That re-creates every lead a human merged
        # away and can mean a second application under their name (#81), reported as
        # ordinary `created: N`.
        #
        # A CORRUPT db raises rather than reading as empty, which is the same harm by a
        # different route: `except Exception: return set()` turned an unreadable dedup
        # store into a silent full dedup loss, and refusing to start over a RELOCATED
        # store while shrugging at an unreadable one is incoherent. `DeadLetterDb`
        # already rules that way for its own store (F1, "never a silent empty").
        #
        # It is NOT identical to that sibling, though, and the difference is deliberate:
        # an existing db with no `seen_jobs` table reads as EMPTY here, where
        # `DeadLetterDb` raises. That state is a real one users have -- the bug above
        # left 0-byte files behind, and a 0-byte file is a valid empty sqlite db -- so
        # raising on it would turn this fix into a hard failure on the next run for
        # exactly the people the fix is for. `save` creates the table.
        #
        # Asked as a question rather than caught as an exception: a bare `except` here is
        # what hid the corruption case, and `OperationalError` covers "database is
        # locked" as well as "no such table", so discriminating on the exception would
        # silently swallow a transient lock too.
        if not os.path.exists(self.path):
            # `lexists` distinguishes TRULY absent from a DANGLING SYMLINK. Without this
            # arm a broken link reads as "no dedup history yet" and the run proceeds with
            # an empty set -- the #81 harm -- when what it actually means is that someone's
            # store has been moved or deleted out from under a link that still points at
            # it. (Nothing is CREATED by the read either way -- both loaders return
            # before any connect. Creation is on the write path.)
            if os.path.lexists(self.path):
                raise sqlite3.DatabaseError(
                    f"the dedup database path {self.path} is a symlink to something that "
                    f"does not exist. sluice will not run with an empty dedup set; fix or "
                    f"remove the link.")
            return set()
        db = sqlite3.connect(self.path)
        try:
            # Wrapped so the failure names the FILE and a remedy. Unwrapped, a user meets
            # `sqlite3.DatabaseError: file is not a database` with no path in it, ten
            # lines from a relocation refusal that names both.
            known = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='seen_jobs'"
            ).fetchone()
            if not known:
                return set()
            rows = db.execute("SELECT url FROM seen_jobs").fetchall()
        except sqlite3.OperationalError:
            # FIRST, and it must stay first: OperationalError SUBCLASSES DatabaseError,
            # so the corruption arm below would otherwise swallow "database is locked" --
            # two overlapping ingest runs -- and tell the user to move or delete a
            # perfectly good dedup store, causing the exact irreversible loss that
            # message warns about. Re-raised untouched so the type survives for a caller
            # that wants to retry; flattening it into DatabaseError makes an
            # `isinstance(e, OperationalError)` check downstream false.
            raise
        except sqlite3.DatabaseError as e:
            raise sqlite3.DatabaseError(
                f"the dedup database at {self.path} is unreadable ({e}). sluice will not "
                f"run with an empty dedup set, because that re-creates leads you merged "
                f"away and can apply to the same job twice. Move or delete it, or point "
                f"SEEN_DB at a good copy."
            ) from e
        finally:
            db.close()
        return {r[0] for r in rows if r[0]}

    def _init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS seen_jobs (url TEXT PRIMARY KEY, scanned_at TEXT)"
        )
        db.commit()
        db.close()

    def save(self, leads: Iterable[Lead]) -> int:
        self._init()
        db = sqlite3.connect(self.path)
        now = datetime.datetime.now().isoformat()
        saved = 0
        for lead in leads:
            key = lead.dedup_key
            if key:
                db.execute(
                    "INSERT OR IGNORE INTO seen_jobs (url, scanned_at) VALUES (?, ?)",
                    (key, now),
                )
                saved += 1
        db.commit()
        db.close()
        return saved
