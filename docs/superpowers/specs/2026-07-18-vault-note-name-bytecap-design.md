# Vault note-name byte-cap + lead-level write isolation — design

- **Date**: 2026-07-18
- **Status**: draft (brainstormed with user; scope = "crash fix + lead isolation", collision filed separately)
- **Origin**: issue #24 (`fix(vault): the 120-char note-name cap counts characters, not bytes`), surfaced by the #5 plan review. Pre-existing; neither introduced nor worsened by #5.

## Goal

Two defects, one narrow fix each:

1. A lead whose `company` or `title` is long **and** non-ASCII produces a note filename that exceeds
   the filesystem's byte limit, raising `ENAMETOOLONG`. The note-name cap counts **characters**; the
   limit is in **bytes**.
2. That `OSError` aborts the **entire ingest run** — not the one lead, not the one source. Every
   source scheduled after it is skipped, and the batch's `seen.db` record never happens.

Fix both while changing the identity of **zero** existing notes. The collision class (two distinct
leads truncating to one filename) is explicitly out of scope and filed separately.

## Background — the exact failure path

`Vault._path_for` (`sluice/core/vault.py:83`) names a note by:

```python
safe = f"{lead.company} - {lead.title}"[:120].replace("/", "-").replace(":", "-")
return os.path.join(self.leads_dir, f"{safe}.md")
```

`[:120]` caps at 120 **characters**. `ext4`/APFS `NAME_MAX` is 255 **bytes**. A 120-character
CJK name reaches ~360 bytes (363 measured on a real sample), so the cap does not prevent
`ENAMETOOLONG`.

The blast radius is the real problem, and it is worse than "aborts the run":

- `VaultSink.write` (`sluice/ingest/sink.py:23`) loops over leads calling `self.vault.upsert(lead)`,
  and records the **whole batch** into `seen.db` only *after* the loop (`sink.py:37`).
- The engine's per-source `try` closes **before** `sink.write` is called (`sluice/ingest/engine.py`:
  the `try/except` wraps `_run_source`; `sink.write(fresh)` at line 60 is outside it).

So an `ENAMETOOLONG` on lead 3 of 10 leaves leads 1–2 **written but never recorded in `seen.db`**,
leads 4–10 **unprocessed**, the whole batch's `seen.db` save **skipped**, and **every later source**
skipped. That contradicts the per-item isolation the rest of the pipeline is careful about: one bad
lead must not abort the rest, one broken source must not sink the registry.

## The inversion trap — why the filed proposal duplicates notes

The issue proposes "cap on **encoded bytes** rather than characters." Read literally — a byte-cap
**instead of** the `[:120]` char-cap — it silently violates never-clobber:

- Today a 200-character ASCII `company - title` truncates to **120 chars** on disk.
- A 255-byte cap would keep **~200 chars** → a **different filename**.
- Next scrape's `os.path.exists(path)` misses the old note → a **second note is created**.

Every existing note whose full `company - title` is 121–255 chars would get a duplicate on its next
re-scrape. That is exactly the wholesale-duplication never-clobber exists to prevent.

### The correct order: char-cap FIRST, then byte-clamp

Apply `[:120]` first, then clamp the result to the byte budget **only if it still exceeds it**. This
is provably duplication-safe for the existing vault:

- **Any note already on disk** was created via `[:120]` and did **not** crash *on this filesystem*, so
  its stem is ≤ (NAME_MAX − 3) bytes here. The byte-clamp condition is therefore **false** for it → the
  clamp is a no-op → the path is byte-identical → **zero duplicates**. One premise: `NAME_MAX` is
  stable. A note synced in from a larger-limit filesystem — this *is* a Syncthing vault — could carry a
  stem between this FS's limit and its origin's and be re-truncated → a duplicate on re-scrape. That is
  not a regression (such a note `ENAMETOOLONG`s on *every* re-scrape here today) and it falls to #5, but
  it qualifies the word "proven".
- The **only** names whose identity changes are ones that `ENAMETOOLONG` *today* — i.e. names with
  **no note to duplicate**. They move from crashing to being stored under a valid truncated name.

What is load-bearing is **retaining the 120-char cap**, not the textual order of the two operations.
Both `[:120]` and the byte-clamp are prefix truncations, and composing two prefix truncations is
commutative — `clamp(s[:120], b) == clamp(s, b)[:120]` for all inputs (verified across ASCII/CJK/mixed
at several budgets). So *reordering* the two is harmless; the one way this fix silently bins a vault is
dropping `[:120]` and byte-capping **instead**, which keeps 121–255-char ASCII names longer than today
and renames every such existing note into a duplicate.

## Design

### Piece 1 — `_path_for` naming fix (`core/vault.py`)

```python
def _path_for(self, lead: Lead) -> str:
    """Match the old pipeline's naming exactly, so an existing note for the same
    company+role is UPDATED in place rather than duplicated.

    The cap is applied as (1) 120 CHARACTERS then (2) a byte-clamp to the
    filesystem's NAME_MAX. The order is load-bearing: char-cap first makes the
    byte-clamp a no-op for every name already on disk (all ≤ NAME_MAX bytes, or
    they would never have been written), so no existing note's identity moves.
    Only names that ENAMETOOLONG today change — and they have no note to duplicate.
    """
    safe = f"{lead.company} - {lead.title}"[:120].replace("/", "-").replace(":", "-")
    safe = _clamp_bytes(safe, self._name_max() - len(b".md"))
    return os.path.join(self.leads_dir, f"{safe}.md")
```

Two new helpers:

```python
def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    A non-positive budget holds nothing -> "" (a negative slice would keep all but the
    last few bytes). encode -> slice -> decode(errors='ignore') drops any incomplete
    trailing multibyte sequence, which IS the 'never split a codepoint' guarantee."""
    if limit <= 0:
        return ""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
```

```python
def _name_max(self) -> int:
    """The filesystem's max filename length in BYTES for the leads dir, cached.
    255 fallback where pathconf is unsupported (some network/FUSE mounts) OR returns a
    non-positive/too-small value: pathconf RETURNS -1 (a value, not an exception) for an
    indeterminate limit, which uncaught would drive the byte budget negative."""
    if self._name_max_cache is None:
        try:
            n = os.pathconf(self.leads_dir, "PC_NAME_MAX")
        except (OSError, ValueError, AttributeError):
            n = -1
        self._name_max_cache = n if n > len(b".md") else 255
    return self._name_max_cache
```

Notes:

- `self._name_max_cache` is initialised to `None` in `__init__`. One syscall per Vault, not per lead.
- `_name_max()` is only reached from `_path_for`, which is only reached from `upsert`, which calls
  `os.makedirs(self.leads_dir, exist_ok=True)` first — so `leads_dir` exists when `pathconf` runs.
- `.replace("/","-").replace(":","-")` is byte-length-preserving (ASCII→ASCII), so char-cap →
  replace → byte-clamp and char-cap → byte-clamp → replace produce the same byte count. Keep the
  replace before the clamp to match the current textual order.
- The byte budget subtracts `len(b".md")` (3) because `NAME_MAX` bounds the **whole** filename,
  extension included.

With this in place, `ENAMETOOLONG` from an over-long name cannot occur — the byte-clamp prevents it.

### Piece 2 — lead-level write isolation (`ingest/sink.py`)

```python
def write(self, leads) -> dict:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    recorded = []
    for lead in leads:
        stamp = self._today()
        if not lead.first_seen:
            lead.first_seen = stamp
        lead.last_seen = stamp
        try:
            outcome = self.vault.upsert(lead)  # "created" | "updated"
            counts[outcome] = counts.get(outcome, 0) + 1
            recorded.append(lead)
        except OSError as e:
            # A lead the store cannot write (name too long, permissions, disk full)
            # must not sink the batch or the run. Count it, log it, and leave it OUT
            # of `recorded` so it never enters seen.db and is retried next run.
            counts["skipped"] += 1
            _log.warning("vault refused lead %r: %s", lead.dedup_key, e)
    if recorded:
        self.seendb.save(recorded)
    return counts
```

- Reuses the `skipped` slot that already exists in the returned dict and is **never incremented
  today** — no new vocabulary, no dependency on #5's parked `"refused"` outcome.
- Isolates at **lead** granularity: strictly better than merely moving `sink.write` inside the
  per-source `try`, which would only isolate at source granularity and still lose a source's
  remaining leads.
- With Piece 1 landed, this catches only *residual* `OSError`s (permissions, disk full) — the
  name-length case no longer reaches it. Belt-and-suspenders, as the issue's second proposal intended.
- **A create that fails mid-write leaves no partial note.** `_write` opens `"w"`, which creates a
  0-byte file before bytes land; a residual `OSError` would otherwise leave a partial note that a later
  re-scrape treats as real (`exists` → `"updated"`, `last_seen` bumped on garbage, never re-created). So
  `upsert`'s create path unlinks the partial and re-raises, and the sink then counts it `skipped`.
  (Surfaced by plan review — the guard above is precisely what makes this path reachable-and-silent.)
- **`skipped` is surfaced, not just logged.** `cli._print_report` gains the count, so a run that
  refuses leads no longer prints a clean-looking created/updated-only summary.
- `VaultSink` needs a module logger. Add at module scope, matching `backends.py:21`'s dotted style:
  `from sluice.core.log import get_logger` and `_log = get_logger("ingest.sink")`. (`get_logger`
  prefixes `sluice.`, so the full name is `sluice.ingest.sink`.)

### Explicit non-goals

Kept out to honour the agreed minimal scope:

- **The collision class** — two *distinct* leads whose first-120-char `company - title` match collide
  onto one filename; the second is treated as a re-scrape of the first (last_seen bumped, no note of
  its own). This exists **today** with the char-cap. The byte-clamp leaves every *existing* note's
  collisions untouched (those are among names that already have notes; never-clobber holds) — but it
  truncates *tighter* than 120 chars for dense non-ASCII names, so it can **widen** the collision
  surface among names that formerly `ENAMETOOLONG`ed (which had no note on disk). Still #5's class, not
  a new one, but the widening is real and #5 should account for it.
  **This is already issue #5** (`a note must never silently absorb a different job`, parked, write-path
  only after the #23 rescope). #5 explicitly owns the truncation instance — "two different long titles
  sharing a 120-char prefix do not collide" is one of its own tests — and its open design question is
  the discriminator itself (location / team / URL-suffix / refuse-and-surface). Do not fold in, and do
  **not** file a duplicate.
- **Broader sink isolation** — moving `sink.write` inside the per-source `try`, and surviving a
  `seendb.save` failure. A separate isolation concern beyond #24's naming crash.

## Tests (behaviour-asserting, synthetic fixtures)

1. **Byte-cap holds.** A lead with a long non-ASCII `company`/`title` (seeded faker or an explicit
   synthetic CJK/emoji string) yields a filename whose UTF-8 byte length ≤ the FS limit, and the
   truncated stem `decode`s cleanly (no split codepoint). Assert on `len(os.path.basename(path).encode())`.
2. **Duplication-safety — the never-clobber guard.** A name that fits within (NAME_MAX − 3) bytes
   produces the **identical** path with and without the byte-clamp. This pins that char-cap-first
   makes the clamp a no-op for the existing corpus. The single most important test given the trap.
3. **Truncation never splits a multibyte char.** `_clamp_bytes` unit test across boundary offsets:
   for every `limit` from 0..len(encoded), the result is valid UTF-8 and is a prefix of `s`.
4. **Lead isolation.** In a batch where one lead's `upsert` raises `OSError`, assert: that lead is
   counted `skipped`, is **not** in `seen.db`, the surrounding leads are written, and a later source's
   leads are still written (the run does not abort). Inject the failure by monkeypatching `upsert` to
   raise on a sentinel lead, or by forcing `_name_max()` low enough that a synthetic lead overflows.

## Mutation discipline (per CLAUDE.md)

Every guard proven to die on exactly its own mutation, mutating by **moving/deleting** (never adding
a check beside the original — an equivalent mutant stays green), on a hash-based `.pyc` cache:

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Witnesses to prove non-inert:

- Delete the `_clamp_bytes` call in `_path_for` → test 1 reddens (long non-ASCII overflows).
- Drop `[:120]` → test 2 reddens (a mid-band ASCII name gets a different path). This is the
  inversion trap made a test. (Reordering byte-clamp and char-cap does NOT redden — the two are
  prefix truncations that commute, so reordering is a provably equivalent mutant; see lines 72-77.)
- Change `_clamp_bytes` to a naive `s[:limit]` (chars) or `s.encode()[:limit].decode()` without
  `errors="ignore"` → test 3 reddens (split codepoint / `UnicodeDecodeError`).
- Remove the `try/except OSError` in `VaultSink.write` → test 4 reddens (run aborts).

## Risks

- **`pathconf` variance.** `PC_NAME_MAX` can differ across mounts (eCryptfs=143, some network FS).
  Querying the actual `leads_dir` (not a constant) handles this; the 255 fallback covers platforms
  where `pathconf` is unsupported. A vault on a 143-byte-limit FS gets shorter names — correct, not a
  regression.
- **Cached `_name_max`.** A Vault instance is short-lived (one CLI invocation), so caching cannot go
  stale within a run. If a future long-lived Vault appears, the cache would need revisiting.
- **Logger wiring.** `VaultSink` does not currently import a logger; adding
  `_log = get_logger("ingest.sink")` at module scope is a small, isolated addition.

## Relationship to #5 (both touch `_path_for`)

Both #24 and #5 edit the same function. #24 is the narrow, ready-now change (byte-clamp +
isolation); #5 is parked and blocked on the discriminator design decision. #24 lands first.
When #5 resumes it rebases onto a main that already carries #24's byte-clamp, and its
discriminator composes with it — the byte-clamp decides *how far* the name is truncated, the
discriminator decides *what distinguishes* two leads whose truncated names still match. Neither
obviates the other. No new issue is filed from this pass; the collision concern's home is #5.
