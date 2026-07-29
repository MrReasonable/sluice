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
        # The refusal is scoped to the commands that actually WRITE dedup state, so it
        # is `Sluice.ingest` that resolves with fatal=True and hands the result in --
        # this constructor is also reached by read-only callers, which must not be
        # refused. An explicit argument short-circuits resolution entirely, so nothing
        # is resolved twice.
        self.path = path or resolve(env_var="SEEN_DB", config_value="",
                                    kind="state", name="seen.db")

    def load(self) -> set[str]:
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
