# Track Dead-Letter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every un-acted-on `proposed` track outcome durable in a SQLite dead-letter that re-surfaces each run until a human confirms or dismisses it, so a proposal never vanishes after one report (#49).

**Architecture:** A new `DeadLetterDb` in the track sub-app persists each `proposed` outcome keyed by Gmail message-id. `engine.run` records new proposals and bumps carried ones, emitting the full open set every run; `engine.confirm` clears a lead's entries only on a successful advance; a new `sluice track dismiss` clears by id or lead. Failure semantics are deliberately asymmetric — writes RAISE (so `engine`'s per-message `except` skips `seen.add` and the message re-processes) and a corrupt read fails loudly (never a silent empty, which would drop the whole backlog).

**Tech Stack:** Python 3.12+ stdlib only (`sqlite3`, `dataclasses`, `os`, `datetime`); pytest + faker for tests.

**Spec:** `docs/superpowers/specs/2026-07-19-track-dead-letter-design.md` (cleared two `/review-plan` rounds; 0 Critical/High/Medium outstanding).

## Global Constraints

- **Stdlib only in `sluice/`** — `sqlite3`, `os`, `datetime`, `dataclasses`. No new runtime dependency.
- **Never-regress** — the dead-letter writes no `status`; `confirm` stays the sole status-writer via `_status.can_advance`.
- **Never-clobber** — no vault write is added or changed.
- **Neutrality** — the store is local, gitignored runtime state (`*.db` is gitignored); no personal data in `sluice/` or `tests/`; fixtures are synthetic (`Tidemark` is the established synthetic company; slugs/titles via seeded faker where new ones are needed).
- **Failure semantics (load-bearing)** — writes RAISE on failure; a *corrupt* read RAISES; only a *missing* db reads empty; no-op writes (bump/clear on a missing db) create no file.
- **Fail loudly at construction** — `deadletter` is a required keyword-only argument to `engine.run`/`engine.confirm` (a missing one is a `TypeError`, never a silent no-durability).
- **Tests** — offline/hermetic (no Gmail, no backend); a real temp SQLite `DeadLetterDb` by default, a deliberate fault-injecting double only for the failure-path tests; every guard mutation-witnessed (move/delete → red → restore byte-identical).
- **Mutation-witness hygiene** — run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once before mutating production code, per `CLAUDE.md`.
- **Commits** — Conventional Commits (`feat(track): ...`, `docs(track): ...`).

## File Structure

- **Create** `sluice/track/deadletter.py` — `DeadLetterDb`, `Entry`, `deadletter_path`, `_DEADLETTER_SUFFIX`. One responsibility: durable persistence + retrieval of un-acted-on proposals.
- **Create** `tests/test_track_deadletter.py` — `DeadLetterDb` unit + failure-semantics tests.
- **Modify** `sluice/track/engine.py` — `RunReport.open_proposals` (replaces `proposals`); `run()` gains `deadletter`, records/bumps/emits; `confirm()` gains `deadletter`, clears on success.
- **Modify** `sluice/core/app.py` — `track()`/`track_confirm()` construct + inject the store; new `track_dismiss()`.
- **Modify** `sluice/cli.py` — `cmd_track_run` prints the open set; new `cmd_track_dismiss` + `track dismiss` subparser.
- **Modify** `tests/test_track_engine.py` — thread `deadletter` through calls; migrate 5 hint guards to `Entry.hint`; add durability/ordering/dry-run/clear tests.
- **Modify** `tests/test_track_cli.py` — `dismiss` arg parsing + a `track_dismiss` behaviour test.
- **Modify** `docs/ARCHITECTURE.md` — track sub-app paragraph, owned-state sentence, method enumeration.

---

### Task 1: `DeadLetterDb` store (isolated)

**Files:**
- Create: `sluice/track/deadletter.py`
- Test: `tests/test_track_deadletter.py`

**Interfaces:**
- Produces:
  - `_DEADLETTER_SUFFIX: str = ".deadletter.db"`
  - `deadletter_path(seen_db: str) -> str`
  - `@dataclass(frozen=True) Entry(message_id, lead, candidates, ev_type, proposal, hint, first_seen, times_surfaced)` — all `str` except `times_surfaced: int`
  - `DeadLetterDb(path: str)` with `open_entries() -> list[Entry]`, `bump_surfaced() -> None`, `record(entry: Entry) -> None`, `clear_lead(slug: str) -> int`, `clear_id(message_id: str) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_track_deadletter.py
import os, tempfile, pathlib
import pytest
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path, _DEADLETTER_SUFFIX


def _db():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "track-seen.db.deadletter.db")))


def _entry(mid="m1", lead="Tidemark - Analyst", **kw):
    base = dict(message_id=mid, lead=lead, candidates="", ev_type="rejection",
                proposal="rejection (conf 0.60)", hint='sluice track confirm --lead "x" --to rejected',
                first_seen="2026-07-10", times_surfaced=1)
    base.update(kw)
    return Entry(**base)


def test_derived_path_uses_gitignored_db_suffix():
    assert deadletter_path("./track-seen.db") == "./track-seen.db.deadletter.db"
    assert _DEADLETTER_SUFFIX.endswith(".db")   # gitignore *.db coverage is load-bearing


def test_record_then_open_round_trips():
    db = _db()
    db.record(_entry())
    got = db.open_entries()
    assert len(got) == 1
    assert got[0].message_id == "m1" and got[0].hint.endswith("--to rejected")


def test_missing_db_reads_empty_without_creating_it():
    db = _db()
    assert db.open_entries() == []
    assert not os.path.exists(db.path)   # a read must not create the store


def test_open_entries_oldest_first_seen():
    db = _db()
    db.record(_entry("mB", first_seen="2026-07-11"))
    db.record(_entry("mA", first_seen="2026-07-09"))
    assert [e.message_id for e in db.open_entries()] == ["mA", "mB"]


def test_bump_surfaced_increments_existing_only():
    db = _db()
    db.record(_entry("mA", times_surfaced=1))
    db.bump_surfaced()                       # bumps existing mA -> 2
    db.record(_entry("mB", times_surfaced=1))  # recorded AFTER bump -> stays 1
    got = {e.message_id: e.times_surfaced for e in db.open_entries()}
    assert got == {"mA": 2, "mB": 1}


def test_clear_lead_and_clear_id_return_counts():
    db = _db()
    db.record(_entry("m1", lead="Tidemark - Analyst"))
    db.record(_entry("m2", lead="Tidemark - Analyst"))
    db.record(_entry("m3", lead="Other - Role"))
    assert db.clear_lead("Tidemark - Analyst") == 2
    assert [e.message_id for e in db.open_entries()] == ["m3"]
    assert db.clear_id("m3") == 1
    assert db.open_entries() == []


def test_clear_on_missing_db_is_noop_and_creates_no_file():
    db = _db()
    assert db.clear_lead("x") == 0 and db.clear_id("y") == 0
    assert db.open_entries() == []            # bump on missing is a no-op too
    db.bump_surfaced()
    assert not os.path.exists(db.path)        # no-op writes must not create the store


def test_corrupt_db_read_raises_not_silent_empty():
    db = _db()
    os.makedirs(os.path.dirname(db.path), exist_ok=True)
    with open(db.path, "w") as f:
        f.write("not a sqlite file")
    with pytest.raises(Exception):
        db.open_entries()   # corrupt MUST fail loud, never silently drop the backlog


def test_record_write_failure_propagates():
    d = tempfile.mkdtemp()
    dbpath = str(pathlib.Path(d, "x.db"))
    os.mkdir(dbpath)        # a directory where the db file should be -> the write raises
    with pytest.raises(Exception):
        DeadLetterDb(dbpath).record(_entry())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_track_deadletter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.track.deadletter'`.

- [ ] **Step 3: Write the implementation**

```python
# sluice/track/deadletter.py
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
    on a system that never recorded a proposal leaves nothing behind.
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


def deadletter_path(seen_db: str) -> str:
    return seen_db + _DEADLETTER_SUFFIX


class DeadLetterDb:
    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        # Used by every write and by a read of an EXISTING db. Creates the parent
        # dir and the table on demand (mirrors SeenDb._init) -- the first `record`
        # is what materialises the store.
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS track_deadletter ("
            "message_id TEXT PRIMARY KEY, lead TEXT, candidates TEXT, ev_type TEXT, "
            "proposal TEXT, hint TEXT, first_seen TEXT, times_surfaced INTEGER)"
        )
        return db

    def open_entries(self) -> list[Entry]:
        # MISSING db -> empty (first run), without creating it. CORRUPT/unreadable
        # db -> RAISE (the SELECT below throws); never a silent empty.
        if not os.path.exists(self.path):
            return []
        db = self._connect()
        try:
            rows = db.execute(
                f"SELECT {_COLS} FROM track_deadletter ORDER BY first_seen, message_id"
            ).fetchall()
        finally:
            db.close()
        return [Entry(*r) for r in rows]

    def bump_surfaced(self) -> None:
        if not os.path.exists(self.path):   # nothing to bump; do not create the store
            return
        db = self._connect()
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

    def clear_lead(self, slug: str) -> int:
        if not os.path.exists(self.path):
            return 0
        db = self._connect()
        try:
            cur = db.execute("DELETE FROM track_deadletter WHERE lead = ?", (slug,))
            db.commit()
            return cur.rowcount
        finally:
            db.close()

    def clear_id(self, message_id: str) -> int:
        if not os.path.exists(self.path):
            return 0
        db = self._connect()
        try:
            cur = db.execute("DELETE FROM track_deadletter WHERE message_id = ?", (message_id,))
            db.commit()
            return cur.rowcount
        finally:
            db.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_track_deadletter.py -q`
Expected: PASS (all).

- [ ] **Step 5: Mutation-witness the two crux guards**

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
- Delete the `if not os.path.exists(self.path): return []` line in `open_entries`. Run `python -m pytest tests/test_track_deadletter.py::test_missing_db_reads_empty_without_creating_it -q` → expect FAIL. Restore.
- Change `open_entries`'s body to swallow (`try: ... except Exception: return []`). Run `test_corrupt_db_read_raises_not_silent_empty` → expect FAIL. Restore byte-identical.

- [ ] **Step 6: Commit**

```bash
git add sluice/track/deadletter.py tests/test_track_deadletter.py
git commit -m "feat(track): DeadLetterDb store for un-acted-on proposals (#49)"
```

---

### Task 2: `run()` records + surfaces; `RunReport.open_proposals`

**Files:**
- Modify: `sluice/track/engine.py` (`RunReport`, `run`)
- Modify: `sluice/core/app.py` (`track()` — construct + inject the store)
- Modify: `sluice/cli.py` (`cmd_track_run` — print the open set)
- Test: `tests/test_track_engine.py` (thread `deadletter`; migrate 5 hint guards; add new tests)

**Interfaces:**
- Consumes: `DeadLetterDb`, `Entry`, `deadletter_path` from Task 1.
- Produces:
  - `RunReport.open_proposals: list[Entry]` (replaces `proposals: list[str]`); `proposed: int` unchanged (this run's new count).
  - `engine.run(vault, cfg, client, backend, *, seen, deadletter, now_iso, since_iso=None, dry_run=False) -> RunReport`.

- [ ] **Step 1: Add the field and thread `deadletter` through the test harness (make the suite compile)**

In `tests/test_track_engine.py`, add the helper and imports near the top (after the existing imports):

```python
from sluice.track.deadletter import DeadLetterDb, deadletter_path, Entry


def _dl():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "track-seen.db.deadletter.db")))
```

Add `deadletter=_dl()` to every existing `E.run(...)` call — lines **55, 65, 66, 74, 93, 101, 115, 125, 133, 141**. Example (line 55):

```python
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=seen, deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
```

(Leave the two `E.confirm(...)` calls at lines 80, 82 unchanged — `confirm` gains `deadletter` in Task 3.)

- [ ] **Step 2: Migrate the 5 hint guard assertions to `Entry.hint`**

`test_proposal_carries_real_confirm_command` (was line 102):

```python
    assert rep.open_proposals
    assert "--to rejected" in rep.open_proposals[0].hint
    assert "<status>" not in rep.open_proposals[0].hint
```

`test_update_proposal_has_no_broken_command` (was lines 116-118):

```python
    assert rep.open_proposals
    assert "<status>" not in rep.open_proposals[0].hint
    assert "review" in rep.open_proposals[0].hint.lower()  # a manual-review note, not a fake command
```

`test_unmatched_proposal_has_no_fake_lead_command` (was line 134):

```python
    assert rep.open_proposals
    assert '--lead "?"' not in rep.open_proposals[0].hint
    assert '--lead "Zzz"' not in rep.open_proposals[0].hint
```

- [ ] **Step 3: Run to verify the suite fails (field/param not yet in engine)**

Run: `python -m pytest tests/test_track_engine.py -q`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'deadletter'` (and `AttributeError: 'RunReport' object has no attribute 'open_proposals'`).

- [ ] **Step 4: Implement the engine change**

In `sluice/track/engine.py`, add the import beside the others:

```python
from sluice.track.deadletter import Entry
```

In `RunReport`, replace the `proposals` field:

```python
    open_proposals: list = field(default_factory=list)  # every currently-open dead-letter Entry
```

Replace the whole `run(...)` function body with (signature gains `deadletter`; records/bumps/emits):

```python
def run(vault, cfg, client, backend, *, seen, deadletter, now_iso, since_iso=None, dry_run=False) -> RunReport:
    rep = RunReport()
    today = datetime.fromisoformat(now_iso).date().isoformat()
    leads = [n for n in vault.read_leads(set(_status.APPLICATION_OWNED))
             if n.status in _INFLIGHT]
    note_by_slug = {n.slug: n for n in leads}
    try:
        ids = client.search_messages(_gmail_query(cfg, now_iso, since_iso))
    except GoogleAuthError:
        rep.auth_error = True
        return rep
    # Bump carried entries before any new record, so a row first recorded THIS run
    # stays at times_surfaced=1. Outside the per-message try on purpose: a raise
    # here (corrupt/unwritable store) aborts the run before seen/lastrun save --
    # fail-safe, since nothing has been processed or seen.add'd yet.
    if not dry_run:
        deadletter.bump_surfaced()
    new_entries = []
    for mid in ids:
        if mid in seen:
            continue
        rep.msgs += 1
        try:
            msg = client.get_message(mid)
            msg["message_id"] = mid
            ics = None
            for att in msg.get("attachments", []):
                if att.get("filename", "").lower().endswith(".ics") or "calendar" in att.get("mime", "").lower():
                    ics = parse_ics(att.get("data", b"").decode("utf-8", "replace"))
                    break
            ev = classify(msg, leads, backend, cfg, ics=ics)
            rep.classified += 1
            res = reconcile(ev, note_by_slug, vault, cfg, client, dry_run=dry_run)
            rep.results.append(res)
            # Never-regress across messages in one run: reflect the just-written
            # status back into the snapshot. Only meaningful when something was
            # actually written (never in a dry-run preview).
            if not dry_run and res.status_to and ev.lead_slug in note_by_slug:
                note_by_slug[ev.lead_slug].status = res.status_to
            if res.action == "applied":
                rep.auto += 1
            elif res.action == "proposed":
                rep.proposed += 1
                target = _PROPOSE_TARGET.get(ev.type, "")
                if ev.lead_slug and target:
                    hint = f'sluice track confirm --lead "{ev.lead_slug}" --to {target}'
                elif ev.candidates:
                    opts = "; ".join(f'--lead "{c}" --to {target or "<status>"}' for c in ev.candidates)
                    hint = f"(ambiguous lead; pick one: sluice track confirm {opts})"
                else:
                    hint = f'(no runnable action for type "{ev.type}" / lead "{res.lead}"; review manually)'
                entry = Entry(message_id=mid, lead=ev.lead_slug or "",
                              candidates=",".join(ev.candidates), ev_type=ev.type,
                              proposal=res.proposal or ev.type, hint=hint,
                              first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                # record BEFORE seen.add: a write failure raises, the `except`
                # below skips seen.add, and the message re-processes next run.
                # SUPERSEDED by "Post-review refinement" at the end of this plan:
                # skipping seen.add alone does NOT guarantee re-processing (the
                # watermark still advances), so the shipped code routes this write
                # through `_dl_write`, which also holds the watermark.
                if not dry_run:
                    deadletter.record(entry)
            if res.calendar in ("created", "updated"):
                rep.calendar_added += 1
            if not dry_run:
                seen.add(mid)
        except GoogleAuthError:
            rep.auth_error = True
            break
        except Exception:
            rep.failures += 1
    # Emit the full open set. Non-dry: the store already holds this run's new rows,
    # so it is the single source of truth. Dry: union the persisted set with this
    # run's computed-new (keyed by message_id, persisted wins), recording nothing.
    if dry_run:
        by_id = {e.message_id: e for e in new_entries}
        for e in deadletter.open_entries():
            by_id[e.message_id] = e
        rep.open_proposals = sorted(by_id.values(), key=lambda e: (e.first_seen, e.message_id))
    else:
        rep.open_proposals = deadletter.open_entries()
    return rep
```

- [ ] **Step 5: Wire `app.py` to construct + inject the store (keeps `run()` callable in production)**

In `sluice/core/app.py`, inside `track()`, add the import with the other lazy imports at the top of the method and construct the store:

```python
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
```

Then, beside the `seen`/`since_iso` loads:

```python
        seen = _load_seen(tcfg.seen_db)
        deadletter = DeadLetterDb(deadletter_path(tcfg.seen_db))
        since_iso = _load_lastrun(lastrun_path)
```

And pass it into the run call:

```python
        rep = track_engine.run(self.store(), tcfg, client, backend, seen=seen,
                               deadletter=deadletter, now_iso=now_iso,
                               since_iso=since_iso, dry_run=dry_run)
```

- [ ] **Step 6: Update `cmd_track_run` to print the open set (the field rename requires this)**

In `sluice/cli.py`, replace the summary print + proposals loop in `cmd_track_run`:

```python
    print(f"track: msgs={rep.msgs} classified={rep.classified} auto={rep.auto} "
          f"proposed={rep.proposed} calendar_added={rep.calendar_added} "
          f"failures={rep.failures} open={len(rep.open_proposals)}", file=sys.stderr)
    if rep.open_proposals:
        print("  OPEN PROPOSALS (awaiting action):", file=sys.stderr)
        for e in rep.open_proposals:
            tag = " (new)" if e.times_surfaced <= 1 else ""
            label = e.lead or e.candidates or "?"
            print(f"  [{e.first_seen} x{e.times_surfaced}{tag}] {label}: {e.proposal} :: {e.hint}",
                  file=sys.stderr)
```

- [ ] **Step 7: Add the new behavioural tests**

Append to `tests/test_track_engine.py`. First a two-soft-rejection client for the mixed/dry-run tests:

```python
class TwoSoftRejectClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "mA": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                   "body_text": "an update on your application", "thread_id": "t", "attachments": []},
            "mB": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                   "body_text": "an update on your application", "thread_id": "t", "attachments": []},
        }, events=[])


def _soft_reject_backend():
    # low confidence -> reconcile returns `proposed`, not an auto-advance
    return FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.6,
                                   "when": None, "links": [], "materials": [], "summary": "soft"}))


def test_proposal_survives_across_runs_until_dismissed():
    v, _ = _vault("phone_screen")
    dl = _dl()
    seen = set()
    r1 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    assert r1.open_proposals and r1.open_proposals[0].times_surfaced == 1
    # run 2: the message is in `seen` (skipped), but the dead-letter re-surfaces it, bumped
    r2 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")
    assert r2.msgs == 0                                   # message skipped (in seen)
    assert r2.open_proposals and r2.open_proposals[0].times_surfaced == 2
    # dismiss clears it; run 3 shows an empty backlog
    dl.clear_id(r2.open_proposals[0].message_id)
    r3 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-12T12:00:00+00:00")
    assert r3.open_proposals == []


def test_times_surfaced_mixed_carried_and_new_in_one_run():
    v, _ = _vault("phone_screen")
    dl = _dl()
    # run 1: mB pre-seen, so only mA is new -> record mA (times_surfaced=1)
    E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
          seen={"mB"}, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    # run 2 (fresh seen): mA carried (in seen), mB now new -> bump mA->2, record mB->1
    r2 = E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
               seen={"mA"}, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")
    got = {e.message_id: e.times_surfaced for e in r2.open_proposals}
    assert got == {"mA": 2, "mB": 1}


def test_dry_run_unions_persisted_and_computed_new_without_recording():
    v, _ = _vault("phone_screen")
    dl = _dl()
    # persist mA via a real run (mB pre-seen so only mA records)
    E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
          seen={"mB"}, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    # dry-run (fresh seen): mA carried (in seen), mB new -> union shows both, records nothing
    r = E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
              seen={"mA"}, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00", dry_run=True)
    ids = sorted(e.message_id for e in r.open_proposals)
    assert ids == ["mA", "mB"]                            # mB appears (computed-new)
    assert [e.message_id for e in dl.open_entries()] == ["mA"]  # ...but was NOT recorded


class BoomRecordDL(DeadLetterDb):
    def record(self, entry):
        raise sqlite3.OperationalError("disk full")


def test_record_failure_skips_seen_so_message_reprocesses():
    v, _ = _vault("phone_screen")
    seen = set()
    rep = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
                seen=seen, deadletter=BoomRecordDL(_dl().path),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.failures == 1        # the raise was caught per-message
    assert "m1" not in seen         # ...and seen.add was skipped -> re-processes next run
```

Add `import sqlite3` to the top of `tests/test_track_engine.py` (for `BoomRecordDL`).

- [ ] **Step 8: Run the full track suite**

Run: `python -m pytest tests/test_track_engine.py tests/test_track_deadletter.py -q`
Expected: PASS (all).

- [ ] **Step 9: Mutation-witness the ordering + skip-seen guards**

`python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
- Move `deadletter.record(entry)` to *after* `seen.add(mid)` (delete it from the proposed block, add it after the `if not dry_run: seen.add(mid)` line). Run `test_record_failure_skips_seen_so_message_reprocesses` → expect FAIL (`m1` now in seen). Restore byte-identical.
- Move `deadletter.bump_surfaced()` to *after* the loop (before report assembly). Run `test_times_surfaced_mixed_carried_and_new_in_one_run` → expect FAIL (mB bumped to 2). Restore.

- [ ] **Step 10: Full suite + lint, then commit**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: PASS, clean.

```bash
git add sluice/track/engine.py sluice/core/app.py sluice/cli.py tests/test_track_engine.py
git commit -m "feat(track): run() durably records and re-surfaces proposals (#49)"
```

---

### Task 3: `confirm()` clears the dead-letter on a successful advance

**Files:**
- Modify: `sluice/track/engine.py` (`confirm`)
- Modify: `sluice/core/app.py` (`track_confirm` — construct + inject the store)
- Test: `tests/test_track_engine.py`

**Interfaces:**
- Produces: `engine.confirm(vault, cfg, slug, to, *, deadletter, when=None, dry_run=False) -> dict` (adds required kw-only `deadletter`; clears `note.slug`'s entries only after `can_advance` passes and the write succeeds).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_track_engine.py`:

```python
def _seed(dl, mid="m1", lead="Tidemark - Analyst", candidates=""):
    dl.record(Entry(message_id=mid, lead=lead, candidates=candidates, ev_type="rejection",
                    proposal="soft", hint="h", first_seen="2026-07-10", times_surfaced=1))


def test_confirm_clears_dead_letter_on_success():
    v, _ = _vault("phone_screen")
    dl = _dl(); _seed(dl)
    out = E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl)
    assert out["ok"] is True
    assert dl.open_entries() == []                 # the lead's proposals are resolved


def test_confirm_dry_run_does_not_clear():
    v, _ = _vault("phone_screen")
    dl = _dl(); _seed(dl)
    E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl, dry_run=True)
    assert len(dl.open_entries()) == 1             # a preview clears nothing


def test_confirm_refused_advance_does_not_clear():
    v, _ = _vault("interview")
    dl = _dl(); _seed(dl)
    out = E.confirm(v, TrackConfig(), "Tidemark - Analyst", "phone_screen", deadletter=dl)  # backward
    assert out["ok"] is False
    assert len(dl.open_entries()) == 1             # a refused confirm must NOT delete the row


def test_confirm_lead_does_not_clear_ambiguous_candidates_entry():
    v, _ = _vault("phone_screen")
    dl = _dl()
    _seed(dl, mid="mAmb", lead="", candidates="Tidemark - Analyst,Other - Role")  # ambiguous: lead=""
    E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl)
    assert len(dl.open_entries()) == 1             # exact-match clear misses it; dismiss --id clears it
```

Update the two existing `E.confirm(...)` calls (lines 80, 82 in `test_confirm_never_clobber`) to pass `deadletter=_dl()`:

```python
    assert E.confirm(v, TrackConfig(), "Tidemark - Analyst", "offer", deadletter=_dl())["ok"] is True
    assert "status: offer" in pathlib.Path(path).read_text()
    assert E.confirm(v, TrackConfig(), "Tidemark - Analyst", "phone_screen", deadletter=_dl())["ok"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_track_engine.py -k confirm -q`
Expected: FAIL — `TypeError: confirm() got an unexpected keyword argument 'deadletter'`.

- [ ] **Step 3: Implement the `confirm` change**

In `sluice/track/engine.py`, replace `confirm(...)`:

```python
def confirm(vault, cfg, slug, to, *, deadletter, when=None, dry_run=False) -> dict:
    matches = [n for n in vault.read_leads() if slug_matches(n, slug)]
    if not matches:
        return {"ok": False, "reason": "no_match"}
    if len(matches) > 1:
        return {"ok": False, "reason": "ambiguous"}
    note = matches[0]
    if not _status.can_advance(note.status, to):
        return {"ok": False, "reason": note.status}
    if not dry_run:
        fields = {"status": _status.normalize(to), "last_signal": date.today().isoformat()}
        if when:
            fields["interview_date"] = f'"{when}"'
        vault.update_fields(note.ref, fields)
        # Clear only after can_advance passed AND the write happened: a refused
        # confirm returned above and never reaches here, so it never deletes a row
        # (deleting on a refused confirm would be #49's silent loss on the clear path).
        deadletter.clear_lead(note.slug)
    return {"ok": True, "from": note.status, "to": _status.normalize(to)}
```

- [ ] **Step 4: Wire `app.py` `track_confirm` to inject the store**

In `sluice/core/app.py`, replace `track_confirm`:

```python
    def track_confirm(self, *, lead, to, when=None, dry_run=False):
        """Run the track sub-app's confirm step: apply an operator-approved
        proposal (a status advance the engine flagged rather than auto-applied),
        clearing that lead's dead-letter entries on a successful advance."""
        from sluice.track import engine as track_engine
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        tcfg = load_track_config()
        return track_engine.confirm(self.store(), tcfg, lead, to,
                                    deadletter=DeadLetterDb(deadletter_path(tcfg.seen_db)),
                                    when=when, dry_run=dry_run)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_track_engine.py -q`
Expected: PASS (all).

- [ ] **Step 6: Mutation-witness the advance-gated clear**

`python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
- Move `deadletter.clear_lead(note.slug)` up above the `if not _status.can_advance(...)` guard (delete from the write block, insert right after `note = matches[0]`). Run `test_confirm_refused_advance_does_not_clear` → expect FAIL (row cleared). Restore byte-identical.

- [ ] **Step 7: Commit**

```bash
git add sluice/track/engine.py sluice/core/app.py tests/test_track_engine.py
git commit -m "feat(track): confirm clears the dead-letter on a successful advance (#49)"
```

---

### Task 4: `sluice track dismiss` command

**Files:**
- Modify: `sluice/core/app.py` (new `track_dismiss`)
- Modify: `sluice/cli.py` (new `cmd_track_dismiss` + `track dismiss` subparser)
- Test: `tests/test_track_cli.py`

**Interfaces:**
- Consumes: `DeadLetterDb`, `deadletter_path`, `Entry`.
- Produces:
  - `Sluice.track_dismiss(*, message_id=None, lead=None, dry_run=False) -> dict` returning `{"cleared": int, "dry_run": bool}`.
  - `cmd_track_dismiss(args, config) -> int`; `track dismiss --id/--lead` (mutually exclusive, required) `[--dry-run]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_track_cli.py` (add `import pytest` and the deadletter imports at the top):

```python
import pytest
from sluice.track.deadletter import DeadLetterDb, deadletter_path, Entry


def test_track_dismiss_parses_mutually_exclusive_required():
    a = _build_parser().parse_args(["track", "dismiss", "--id", "m1"])
    assert a.group == "track" and a.cmd == "dismiss" and a.id == "m1"
    a2 = _build_parser().parse_args(["track", "dismiss", "--lead", "tidemark", "--dry-run"])
    assert a2.lead == "tidemark" and a2.dry_run
    with pytest.raises(SystemExit):                       # both -> mutually exclusive
        _build_parser().parse_args(["track", "dismiss", "--id", "m1", "--lead", "x"])
    with pytest.raises(SystemExit):                       # neither -> required
        _build_parser().parse_args(["track", "dismiss"])


def test_track_dismiss_by_id_and_by_lead(monkeypatch, tmp_path):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    pathlib.Path(cfgp).write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry("m1", "Tidemark - Analyst", "", "rejection", "x", "h", "2026-07-10", 1))
    dl.record(Entry("m2", "", "A,B", "unknown", "y", "h", "2026-07-10", 1))
    app = Sluice(Config())
    # dry-run reports the count, deletes nothing
    assert app.track_dismiss(message_id="m1", dry_run=True) == {"cleared": 1, "dry_run": True}
    assert len(dl.open_entries()) == 2
    # real dismiss by id (the only lever for the no-lead entry m2 is --id)
    assert app.track_dismiss(message_id="m1") == {"cleared": 1, "dry_run": False}
    assert {e.message_id for e in dl.open_entries()} == {"m2"}
    assert app.track_dismiss(message_id="m2")["cleared"] == 1
    assert dl.open_entries() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_track_cli.py -k dismiss -q`
Expected: FAIL — argparse has no `dismiss` subcommand / `Sluice` has no `track_dismiss`.

- [ ] **Step 3: Implement `Sluice.track_dismiss`**

In `sluice/core/app.py`, add after `track_confirm`:

```python
    def track_dismiss(self, *, message_id=None, lead=None, dry_run=False):
        """Clear a dead-letter entry a human decided needs no action. `message_id`
        is the only lever for a no-lead entry (a classify-failure or an unmatched
        proposal); `lead` clears a lead's entries without advancing status. A
        dry-run reports the count it would delete without deleting."""
        from sluice.track.config import load_track_config
        from sluice.track.deadletter import DeadLetterDb, deadletter_path
        tcfg = load_track_config()
        dl = DeadLetterDb(deadletter_path(tcfg.seen_db))
        if dry_run:
            entries = dl.open_entries()
            n = sum(1 for e in entries
                    if (message_id is not None and e.message_id == message_id)
                    or (lead is not None and e.lead == lead))
            return {"cleared": n, "dry_run": True}
        n = dl.clear_id(message_id) if message_id is not None else dl.clear_lead(lead)
        return {"cleared": n, "dry_run": False}
```

- [ ] **Step 4: Implement `cmd_track_dismiss` + the subparser**

In `sluice/cli.py`, add the command function after `cmd_track_confirm`:

```python
def cmd_track_dismiss(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).track_dismiss(message_id=args.id, lead=args.lead, dry_run=args.dry_run)
    verb = "would clear" if out["dry_run"] else "cleared"
    noun = "entry" if out["cleared"] == 1 else "entries"
    print(f"track-dismiss: {verb} {out['cleared']} {noun}", file=sys.stderr)
    return 0
```

In `_build_parser`, after the `tconf` block (the `track confirm` subparser), add:

```python
    tdis = track.add_parser("dismiss", help="clear a dead-letter proposal (no status change)")
    tdg = tdis.add_mutually_exclusive_group(required=True)
    tdg.add_argument("--id", help="Gmail message-id of the dead-letter entry to clear")
    tdg.add_argument("--lead", help="clear a lead's dead-letter entries without advancing status")
    tdis.add_argument("--dry-run", action="store_true")
    tdis.set_defaults(func=cmd_track_dismiss)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_track_cli.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_track_cli.py
git commit -m "feat(track): add \`track dismiss\` to clear dead-letter proposals (#49)"
```

---

### Task 5: Update `docs/ARCHITECTURE.md`

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Extend the track sub-app paragraph**

Replace the `5. **track**` bullet (currently ending "a status can only move forward)."):

```markdown
5. **track** (`sluice/track/`): fetch Gmail and Google Calendar since the
   last run, classify each message into an `Event` (refuse rather than
   guess on ambiguity), and reconcile it against lead status
   (never-regress: a status can only move forward). Un-acted-on proposals
   are durably surfaced via `track/deadletter.py` -- a sqlite dead-letter
   re-emitted every run until `track confirm`/`track dismiss` clears it --
   so a proposal never vanishes after a single report.
```

- [ ] **Step 2: Extend the `core/app.py` method enumeration + owned-state sentence**

Replace the sentence listing the methods and owned state:

```markdown
   operations as value-returning methods: `ingest()`, `triage()`, `compose_cv()`,
   `prep()`, `record()`, `track()`, `track_confirm()`, `track_dismiss()`,
   `normalize_statuses()`. It also owns the state those operations need that is
   not itself an adapter: the dossier cache (`dossier_cache()`), and track's
   file-backed seen-message set, last-successful-run watermark, and dead-letter
   store of un-acted-on proposals. Adapters are built lazily on first use, so an
```

- [ ] **Step 3: Verify the docs match reality + full suite green**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: PASS, clean.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs(track): document the dead-letter store and track dismiss (#49)"
```

---

## Self-Review

**1. Spec coverage:**
- §1 `DeadLetterDb` (store, schema, path constant, asymmetric failure semantics) → Task 1. ✓
- §2 run-loop (bump-first, record proposed, emit open set, dry-run union) → Task 2. ✓
- §3 clearing (confirm auto-clear gated on advance; `dismiss --id/--lead`; exact-match caveat) → Task 3 (confirm) + Task 4 (dismiss). ✓
- §4 report format + `RunReport.open_proposals` + reader migration (`cli.py`, 5 guard tests) → Task 2. ✓
- Invariant interactions (never-regress, never-clobber, neutrality, abstain) → upheld across Tasks 1-4; no status/vault write added. ✓
- Testing section (unit, failure semantics, record→seen ordering, times_surfaced mixed, durability, clearing paths, dismiss, dry-run union, migrated guards, mutation-witness) → Tasks 1-4. ✓
- "Docs to update" → Task 5. ✓
- Out of scope (auto-retry, conformance elevation) → not implemented. ✓

**2. Placeholder scan:** No TBD/TODO; every code and test step carries full content. ✓

**3. Type consistency:** `Entry` fields, `DeadLetterDb` method names (`open_entries`/`bump_surfaced`/`record`/`clear_lead`/`clear_id`), `deadletter_path`, and the `run`/`confirm` signatures (`*, deadletter, ...`) are identical across Tasks 1-4. `RunReport.open_proposals` is used consistently in engine, cli, and tests. ✓

**Note for the implementer:** `deadletter` is a *required* keyword-only argument on both `run` and `confirm` (fail-loud, per Global Constraints). Every call site — including the ~10 existing `E.run`/`E.confirm` calls in `tests/test_track_engine.py` — must pass it; Task 2 Step 1 and Task 3 Step 1 enumerate them.

---

## Post-review refinement (pre-push `/review-pr`, shipped in `fix(track): hold watermark …`)

The pre-push review found that Task 2's failure contract, as written above, does **not** deliver its own
"the message re-processes next run" guarantee, and Task 1's "record is the sole creator" claim was not
true of the *table*. Both were fixed before the branch was pushed; the shipped code differs from the
Task 2/Task 1 listings above in exactly these ways.

**1. A caught dead-letter write failure holds the `lastrun` watermark.**
Skipping `seen.add(mid)` is necessary but not sufficient: the per-message `except` swallows the raise, the
run completes, and `app.py` advances the watermark — so the next Gmail `after:` query no longer returns
the un-persisted message and it is lost anyway. Shipped instead:

- `RunReport` gains `deadletter_error: bool = False`.
- A module-level `_dl_write(rep, op)` helper wraps the **two in-loop** dead-letter writes — `record(entry)`
  in the proposed branch and `clear_lead(ev.lead_slug)` in the auto-advance branch. On failure it sets
  `rep.deadletter_error = True` and re-raises (so the existing per-message `except` still skips `seen.add`).
- `app.py`'s `track()` gates the watermark save: `if not rep.auth_error and not rep.deadletter_error:
  _save_lastrun(...)`. `_save_seen` stays unconditional — `seen` only ever grew by successfully-processed
  ids, so holding just the watermark is correct.
- The wrapper is deliberately scoped to those two writes. A non-dead-letter per-message error (a Gmail
  `get_message` hiccup) must leave `deadletter_error` False, or one transient Gmail error would hold the
  whole watermark and force a needless re-scan.
- `bump_surfaced()` needs no flag: it runs at run start, outside the per-message `try`, so a failure
  propagates straight out of `run()` and `app.py` never reaches either save — already fail-safe.

Regression tests: a `record` failure sets the flag and keeps the id out of `seen`; a `clear_lead` failure
does the same on the auto-advance path; a Gmail-error message leaves the flag False (the anti-over-reach
pin); a `bump_surfaced` failure hard-aborts the run; and the app-level watermark gate is pinned alongside
the existing `auth_error` case.

**2. Reads run no schema DDL.**
`open_entries`/`bump_surfaced`/`clear_*` originally went through `_connect()`, which runs
`CREATE TABLE IF NOT EXISTS` — a read performing DDL, and it meant a read could silently create the table
on an existing-but-tableless file. Shipped: those four use a plain `_open()` (no DDL) and only `record`
uses `_connect()`, so `record` is the sole creator of both the file and the table, and an anomalous
tableless file now raises instead of self-healing. Pinned by a regression test.
