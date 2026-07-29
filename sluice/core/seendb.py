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
        # ordinary `created: N`. It also broke this design's own promise that a dry run
        # touches no disk. Same shape as `DeadLetterDb.open_entries`, for the same
        # reason. A file that EXISTS but is corrupt still falls to the except below.
        if not os.path.exists(self.path):
            return set()
        try:
            db = sqlite3.connect(self.path)
            rows = db.execute("SELECT url FROM seen_jobs").fetchall()
            db.close()
            return {r[0] for r in rows if r[0]}
        except Exception:
            return set()

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
