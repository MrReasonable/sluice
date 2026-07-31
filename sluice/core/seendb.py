"""seen.db dedup store - reuses the existing scanner schema so dedup history
carries over the cutover unchanged."""
import datetime
import os
import sqlite3
from collections.abc import Iterable

from sluice.core.leads import Lead
from sluice.core.paths import absent, existing_db_uri, resolve


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
        # empty dedup set instead of refusing. Every already-known lead then reads as
        # unseen and is silently re-submitted to the write path. `Vault.upsert` now
        # probes `_merged/` by name (#81) before creating, so a merged-away lead usually
        # self-heals instead of being re-created -- but the probe is name-keyed, so one
        # whose title has drifted past every candidate still slips through, and that can
        # mean a second application under their name, reported as ordinary `created: N`.
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
        # `core.paths.absent`, shared with `DeadLetterDb`, because this question was
        # written twice and diverged twice -- each fix landed on one store and missed the
        # other. Two of the three routes into a silent empty dedup set arrived that way:
        # `os.path.exists` returning False on EACCES, and a dangling ANCESTOR reading as
        # "no dedup history yet". Both produced `set()` with nothing said, which is the
        # #81 harm this method exists to refuse.
        if absent(self.path, what="the dedup database",
                  why="sluice will not run with an empty dedup set."):
            return set()
        # `existing_db_uri`, so opening CANNOT create. The docstring above warns that
        # `sqlite3.connect` creates a 0-byte file merely by opening and that the file
        # permanently disarms the #80 relocation notice -- and a plain connect left a
        # window in which exactly that happens, because the check above and this open are
        # two syscalls. It raises `OperationalError` on that race instead, which the
        # arm below re-raises untouched.
        db = sqlite3.connect(existing_db_uri(self.path), uri=True)
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
