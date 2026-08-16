"""Durable dead-letter for track work a human still has to do (#49). A row is recorded here
and re-surfaced every run until a human `confirm`s or `dismiss`es it -- the fix for "surfaced
once, deduped by `seen`, then lost".

THREE kinds of row now live here, and the store knows the difference because `clear_lead`
does. `ev_type` normally draws from classify's vocabulary (interview/offer/receipt/...) and
means "a proposed STATUS change"; the two engine-authored kinds below do not:
  - `EV_TYPE_FAILURE` -- the message could not be processed at all (#139).
  - `EV_TYPE_CALENDAR` -- a calendar action we could not complete or verify, e.g. a
    cancellation we could not match or an insert refused over a truncated window.
A status advance resolves status proposals only, so `clear_lead` skips the other two. It used
to delete everything filed against the lead, which silently discarded a "remove this from your
calendar by hand" instruction when an unrelated rejection arrived weeks later.

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

from sluice.core.paths import absent as paths_absent
from sluice.core.paths import existing_db_uri

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

    The logic lives in `core.paths.absent` because it was written twice and diverged
    twice -- the EACCES fix and then the dangling-ANCESTOR guard each landed on one store
    and missed the other. This wrapper only supplies the wording, so the refusal still
    says what a dead-letter store is and why an empty read is not acceptable here (F1).
    """
    return paths_absent(
        path, what="the dead-letter store",
        why="It holds the proposals no one has acted on yet.")


def deadletter_path(seen_db: str) -> str:
    return seen_db + _DEADLETTER_SUFFIX


# Row kinds that are NOT a status proposal, and which a status advance therefore does not
# resolve. `ev_type` otherwise draws from classify's vocabulary (interview/offer/receipt/...);
# these two are engine-authored and live here, in the module that owns the table, so a future
# classify type colliding with one is visible at the definition rather than at a bare literal.
EV_TYPE_FAILURE = "failure"      # the message could not be processed at all
EV_TYPE_CALENDAR = "calendar"    # a calendar action we could not complete or verify


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
        # No DDL, and CANNOT create: only `record` creates the store. An
        # existing-but-tableless/corrupt file therefore RAISES here (fail-loud), never
        # silently gets its table created on a read.
        #
        # `existing_db_uri` rather than a plain connect, because the caller's `_absent`
        # check and this open are two syscalls: measured by forcing the window open, a
        # store removed in between left a fresh 0-BYTE `.deadletter.db` behind. That file
        # then satisfies `exists(resolved + suffix)` in `paths.resolve`, dropping the
        # sidecar from `stranded` -- so the notice about the real backlog still sitting at
        # the legacy path goes quiet permanently. `SeenDb.load` was fixed for this first
        # and this store was missed, which is why both now share one helper.
        return sqlite3.connect(existing_db_uri(self.path), uri=True)

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

    def record(self, entry: Entry, *, replace: bool = False) -> None:
        """Insert `entry`, or no-op if an IDENTICAL row already holds its message_id.

        `replace=True` UPDATES a differing row instead of raising, preserving `first_seen` and
        `times_surfaced`. That is for a caller re-recording ITS OWN message -- `engine.run`
        re-processes a message whenever it is not yet in `seen`, and a re-derived row is by
        definition the newer truth. Without it the raise below turned a routine re-record into
        a permanent stall: a proposal row whose `proposal` string embeds a model confidence
        differs on the next run, so `record` raised, `deadletter_error` was set on every run,
        `_save_lastrun` never advanced, and `_gmail_query`'s `after:` widened without bound.
        An UPDATE also avoids the delete-then-insert window a clear+record leaves.

        `INSERT OR IGNORE` on a `message_id` PRIMARY KEY silently discards a DIFFERENT row
        for the same key, and that is a hole under every caller: a transient failure row for
        `m1` would swallow the real proposal built on the next run, after which `seen.add`
        runs and the message is never re-queried. A store that exists to prevent silent loss
        must not lose a write silently, so a differing row RAISES -- which routes through
        `_dl_write`, holds the lastrun watermark, and skips `seen.add`.

        An identical re-record stays a quiet no-op: a deterministic failure re-recording the
        same row every run must not raise, or durability turns into an exception per run."""
        db = self._connect()
        try:
            cur = db.execute(
                f"INSERT OR IGNORE INTO track_deadletter ({_COLS}) VALUES (?,?,?,?,?,?,?,?)",
                (entry.message_id, entry.lead, entry.candidates, entry.ev_type,
                 entry.proposal, entry.hint, entry.first_seen, entry.times_surfaced),
            )
            if cur.rowcount == 0:
                existing = db.execute(
                    f"SELECT {_COLS} FROM track_deadletter WHERE message_id = ?",
                    (entry.message_id,)).fetchone()
                # The first SIX columns are the comparison, which excludes TWO fields, for two
                # different reasons -- the previous comment gave only one and would have led a
                # reader to widen the slice:
                #   - `times_surfaced` moves under `bump_surfaced`, independently of any caller.
                #   - `first_seen` moves with the CALENDAR. The caller passes `first_seen=today`,
                #     so including it would make every deterministic failure raise the day after
                #     it started -- the permanent-stall shape, on a far wider trigger than the
                #     one that caused it. Pinned by `test_a_re_record_on_a_LATER_DAY_...`.
                differs = existing and tuple(existing[:6]) != (
                    entry.message_id, entry.lead, entry.candidates, entry.ev_type,
                    entry.proposal, entry.hint)
                if differs and replace:
                    db.execute(
                        "UPDATE track_deadletter SET lead=?, candidates=?, ev_type=?, "
                        "proposal=?, hint=? WHERE message_id=?",
                        (entry.lead, entry.candidates, entry.ev_type, entry.proposal,
                         entry.hint, entry.message_id))
                elif differs:
                    raise ValueError(
                        f"dead-letter row for message_id {entry.message_id!r} already exists "
                        f"with ev_type {existing[3]!r}; refusing to silently discard the new "
                        f"{entry.ev_type!r} row. Clear the stale one first.")
            db.commit()
        finally:
            db.close()

    def check_reachable(self) -> None:
        """PREFLIGHT for `clear_lead`, not a guarantee it will succeed.

        For callers that WRITE something else first and clear afterwards. `engine.confirm`
        advances the lead's status and then clears its row, so a `clear_lead` that raises
        leaves the advance already landed and the row still there -- and the obvious retry
        is refused, because the transition has now happened. Probing first moves that
        failure ahead of the write.

        It is BEST-EFFORT, in the same sense as the vault's compare-and-set: a store that
        becomes unreadable in the window between this call and the DELETE still fails
        late. What that costs is bounded and recoverable -- `sluice track dismiss --lead
        <slug>` clears the row without touching status, and a row stranded this way always
        HAS a lead (`confirm` reached it by resolving one), so the slug is the label
        `track run` already prints. The probe's value is that every PERSISTENT cause (a
        dangling link, a corrupt or tableless file, a read-only or unreadable one) is
        caught before the write instead of after it; a state change inside the window is
        not one of those, and pretending otherwise would be the guarantee this is not.

        It probes the OPERATION, not the path. `_absent` alone answers "does the file
        exist", and every way of being unreadable answers yes: measured, a store at mode
        000, one holding non-sqlite bytes, one with no `track_deadletter` table, and a
        read-only one ALL passed an existence check and then raised in `clear_lead` --
        so the guard had none of the coverage this docstring used to claim.
        """
        if _absent(self.path):
            return
        db = self._open()
        try:
            # `clear_lead`'s own statement, restricted to nothing. One statement settles
            # every way it can fail at once -- openable, the table exists, and the file is
            # WRITABLE -- because it is the very operation being protected.
            #
            # Writability needs the DELETE specifically. `BEGIN IMMEDIATE`/`BEGIN
            # EXCLUSIVE` were both measured NOT to raise on a 0444 store (SQLite defers
            # the write lock), and a `SELECT` never could: a read-only store answers it
            # happily and then fails the DELETE. Rolled back, so a healthy store is
            # untouched -- and `WHERE 0` means there is nothing to roll back anyway.
            db.execute("DELETE FROM track_deadletter WHERE 0")
        finally:
            # NESTED, so `close` runs even when `rollback` raises. Flat in one `finally`,
            # an I/O error from the rollback -- the realistic one, on a store that just
            # failed the DELETE -- skips the close and leaks the handle for the life of
            # the process. The probe exists for stores that are already misbehaving, so
            # that is exactly the case it must not make worse.
            try:
                db.rollback()
            finally:
                db.close()

    def clear_lead(self, slug: str, *, status_only: bool = True) -> int:
        """Clear rows filed against this lead. `status_only` says WHICH.

        Two callers with genuinely different meanings, and collapsing them was a bug:
          - `status_only=True` (default) -- an AUTO-ADVANCE resolved this lead's status
            proposals. It did not remove a stale calendar entry or make a failed message
            succeed, so those rows stay.
          - `status_only=False` -- a HUMAN ran `track dismiss --lead`, which means "I have
            looked at this lead and nothing here needs action". That is entitled to clear
            everything, and must, or the operator is left with rows the `--lead` lever cannot
            reach and nothing telling them to use `--id`.

        Filtering unconditionally also desynced `track_dismiss`'s dry-run counter, which
        counts every row for the lead: `--dry-run` said "would clear 2", the real command
        cleared 1, and `app.py`'s own comment claimed the two "can never disagree".

        The unfiltered `DELETE ... WHERE lead = ?` was a silent loss. It fires on any
        `applied` advance and on any successful `confirm`, so a `calendar` row saying "the
        cancellation could not be matched, remove it from your calendar by hand" was deleted
        by an unrelated rejection email arriving weeks later -- with the message already in
        `seen`, so the instruction was never re-derived and the cancelled interview stayed in
        the calendar with no trace.

        The justification for clearing ("an auto-resolved lead's pending proposals are
        resolved too, the lead is already terminal") holds for STATUS proposals only.
        Advancing a lead to `rejected` does not remove a stale calendar entry, and does not
        make a failed message succeed.
        """
        if _absent(self.path):
            return 0
        db = self._open()
        try:
            if status_only:
                cur = db.execute(
                    "DELETE FROM track_deadletter WHERE lead = ? AND ev_type NOT IN (?,?)",
                    (slug, EV_TYPE_FAILURE, EV_TYPE_CALENDAR))
            else:
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
