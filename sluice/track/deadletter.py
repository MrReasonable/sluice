"""Durable dead-letter for un-acted-on track proposals (#49). A `proposed`
outcome is recorded here and re-surfaced every run until a human `confirm`s or
`dismiss`es it -- the fix for "surfaced once, deduped by `seen`, then lost".

Failure semantics are DELIBERATELY ASYMMETRIC and load-bearing (this is the crux
of #49, not a `SeenDb` copy). Every dead-lettered id is already in `seen`, so a
silently-empty read would drop the whole backlog permanently. Therefore:
  - writes RAISE on failure, so the engine's per-message `except` skips
    `seen.add(mid)` and the message re-processes next run;
  - a CORRUPT read RAISES; only a MISSING db reads empty;
  - no-op writes (bump/clear on a missing db) create no file, so a `confirm`
    on a system that never recorded a proposal leaves nothing behind;
  - `record` is the store's SOLE table creator (`_connect`, with DDL) -- every
    read/bump/clear goes through `_open` (no DDL), so an existing-but-tableless
    file RAISES on a read instead of silently getting its table created (F1).
"""
import os
import sqlite3
from dataclasses import dataclass

# The store lives beside the track seen file at `<seen_db>.deadletter.db`. The
# `.db` ending is load-bearing: `.gitignore`'s `*.db` keeps this private runtime
# state (message-ids, slugs, proposal text) out of the public repo. `.lastrun` is
# NOT ignored -- a rename to a non-`.db` suffix would silently leak personal data.
_DEADLETTER_SUFFIX = ".deadletter.db"

_COLS = "message_id, lead, candidates, ev_type, proposal, hint, first_seen, times_surfaced"


@dataclass(frozen=True)
class Entry:
    message_id: str
    lead: str
    candidates: str
    ev_type: str
    proposal: str
    hint: str
    first_seen: str
    times_surfaced: int


def _absent(path: str) -> bool:
    """True if the store genuinely does not exist yet; RAISES if it is unreachable.

    Every reader here asks this question, and each used to ask it with `os.path.exists`,
    which FOLLOWS a symlink -- so a dangling one read as "first run" and the method
    quietly did nothing. For `open_entries` that discards the whole backlog of proposals
    nobody has acted on; for `clear_*` it reports success having cleared nothing. Both are
    the silent empty this module's F1 rule forbids.

    It is reachable because `.deadletter.db` is a migration companion (#80) and `mv` moves
    a link rather than its target. One helper rather than the check repeated four times,
    so the next reader cannot be added without it.
    """
    # `lstat`/`stat`, NOT `os.path.lexists`/`exists`: those return False on ANY OSError,
    # so a store under a directory the user cannot traverse read as "absent" and the
    # backlog came back empty -- the same silent empty by a different route. Measured.
    # Here only FileNotFoundError means absent; EACCES and friends propagate.
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    try:
        os.stat(path)          # the name exists; does it resolve?
    except FileNotFoundError:
        raise FileNotFoundError(
            f"the dead-letter store at {path} is a symlink to something that does not "
            f"exist. Fix or remove the link; it holds the proposals no one has acted on "
            f"yet.") from None
    return False


def deadletter_path(seen_db: str) -> str:
    return seen_db + _DEADLETTER_SUFFIX


class DeadLetterDb:
    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        # Used ONLY by `record` -- the store's sole table creator. Creates the
        # parent dir and the table on demand (mirrors SeenDb._init); every guarded
        # read/bump/clear below goes through `_open` instead, which runs no DDL.
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS track_deadletter ("
            "message_id TEXT PRIMARY KEY, lead TEXT, candidates TEXT, ev_type TEXT, "
            "proposal TEXT, hint TEXT, first_seen TEXT, times_surfaced INTEGER)"
        )
        return db

    def _open(self):
        # No DDL: the caller has already guarded os.path.exists, and only `record`
        # creates the store. An existing-but-tableless/corrupt file therefore RAISES
        # here (fail-loud), never silently gets its table created on a read.
        return sqlite3.connect(self.path)

    def open_entries(self) -> list[Entry]:
        # MISSING db -> empty (first run), without creating it. CORRUPT/unreadable
        # db, or an existing file with no track_deadletter table -> RAISE (the
        # SELECT below throws); never a silent empty (F1).
        #
        if _absent(self.path):
            return []
        db = self._open()
        try:
            rows = db.execute(
                f"SELECT {_COLS} FROM track_deadletter ORDER BY first_seen, message_id"
            ).fetchall()
        finally:
            db.close()
        return [Entry(*r) for r in rows]

    def bump_surfaced(self) -> None:
        if _absent(self.path):              # nothing to bump; do not create the store
            return
        db = self._open()
        try:
            db.execute("UPDATE track_deadletter SET times_surfaced = times_surfaced + 1")
            db.commit()
        finally:
            db.close()

    def record(self, entry: Entry) -> None:
        db = self._connect()
        try:
            db.execute(
                f"INSERT OR IGNORE INTO track_deadletter ({_COLS}) VALUES (?,?,?,?,?,?,?,?)",
                (entry.message_id, entry.lead, entry.candidates, entry.ev_type,
                 entry.proposal, entry.hint, entry.first_seen, entry.times_surfaced),
            )
            db.commit()
        finally:
            db.close()

    def check_reachable(self) -> None:
        """Raise if the store exists but cannot be read; silent if it is genuinely absent.

        For callers that WRITE something else first and clear afterwards. `engine.confirm`
        advances the lead's status and then clears its row: once `clear_lead` could raise,
        a dangling or unreadable store meant the status write landed, the error escaped,
        and the row became unclearable -- the re-run is refused because the transition has
        already happened. Probing first moves the failure ahead of the write.
        """
        _absent(self.path)

    def clear_lead(self, slug: str) -> int:
        if _absent(self.path):
            return 0
        db = self._open()
        try:
            cur = db.execute("DELETE FROM track_deadletter WHERE lead = ?", (slug,))
            db.commit()
            return cur.rowcount
        finally:
            db.close()

    def clear_id(self, message_id: str) -> int:
        if _absent(self.path):
            return 0
        db = self._open()
        try:
            cur = db.execute("DELETE FROM track_deadletter WHERE message_id = ?", (message_id,))
            db.commit()
            return cur.rowcount
        finally:
            db.close()
