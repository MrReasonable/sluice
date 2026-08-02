# Vault recursive lead scan — Implementation Plan (PR A of #1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the lead store scan its directory recursively, without resurrecting the notes that
`sluice leads dedupe --merge` archived under `_merged/`.

**Architecture:** `Vault.leads_dir` currently does two jobs — it is both "where leads are read from"
and "where a new lead is written". This PR separates the first out into a *scan set*: every directory
under `leads_dir`, minus a frozenset of directories sluice itself owns. One walk helper defines that
set; `read_leads`, `normalize_all_statuses` and a new name-lookup all consume it, so the exclusion cannot
be applied in one place and forgotten in another. A lead's identity stays its note NAME, so a note
found in a subfolder is reconciled and updated in place rather than re-created.

**Tech Stack:** Python 3.12–3.14, standard library only. pytest. No new dependencies.

## Global Constraints

- **`sluice/` is standard-library only.** No new imports beyond `os`/`re`/`json`-style stdlib.
- **Neutrality: no personal data.** No employer names, locations, contact details, hostnames or
  absolute paths in `sluice/` or `tests/`. Tests use `Acme`/`Foo` and `tests.conftest.LOCATIONS`.
- **Never-clobber.** A re-scrape of an existing lead touches only `last_seen`. No new write path.
- **Never-regress.** An unrecognized status is passed through untouched, never rewritten.
- **Comments explain *why*** — the invariant upheld, the bug prevented. Match the surrounding density.
- **ruff `line-length = 100`**, lint set `E4,E7,E9,F`. Run `.venv/bin/python -m ruff check sluice tests scripts`.
- **Conventional commits** (`feat(vault): ...`, `test(vault): ...`, `docs: ...`).
- Scope is `sluice/core/vault.py` plus tests and docs. **No config key, no new CLI command, no
  directories created.** Those are PR B.
- Spec: `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md`.

## File Structure

| File | Responsibility |
| --- | --- |
| `sluice/core/vault.py` (modify) | All production changes. Adds `_PRIVATE_SUBDIRS`, `_reraise`, `_is_lead_note`, `Vault._walk`, `Vault._scan_dirs`, `Vault._locate`; changes `read_leads`, `normalize_all_statuses`, `_resolve_path`, `_archived_match`. |
| `tests/test_vault_recursive_scan.py` (create) | The scan set: pruning, caching, the lead predicate, the unreadable-directory refusal. |
| `tests/test_vault_subfolder_resolution.py` (create) | The write path across folders: update-in-subfolder, ambiguity refusal, the merged-loser regression. |
| `tests/test_vault_makedirs_scope.py` (create) | The AST scope guard over `os.makedirs` in `vault.py`. |
| `docs/ARCHITECTURE.md` (modify) | The vault section: scan set vs write folder, the lead predicate. |
| `sluice/core/protocols.py` (modify) | `Store.read_leads` docstring: what counts as a lead. |
| `.rulesync/rules/CLAUDE.md` (modify) | One sentence in the never-clobber/#81 paragraph; regenerate outputs. |

---

### Task 1: The scan set — primitives

Introduces the exclusion set, the single walk, the cached directory list, and the lead predicate.
Nothing consumes them yet; Tasks 2–4 do.

**Files:**
- Modify: `sluice/core/vault.py` (constants block ~line 51; new module functions after `_parse_fm_spaced`; new `Vault` methods after `_slug_for`; `Vault.__init__` ~line 124; `_archived_match` ~line 332)
- Test: `tests/test_vault_recursive_scan.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `_PRIVATE_SUBDIRS: frozenset[str]` — directory names under `leads_dir` that sluice owns.
  - `_reraise(exc: OSError) -> None` — always raises; annotated `None`, not `NoReturn` (see Step 4).
  - `_is_lead_note(fm: dict) -> bool`
  - `Vault._walk(self)` — unannotated generator yielding `(dirpath: str, filenames: list[str])`, pruned.
  - `Vault._scan_dirs(self) -> list[str]` — cached directory list, `[leads_dir]` minimum.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_recursive_scan.py`:

```python
"""The scan set: which directories a lead may be read from, and which files in them count
as leads. `_merged/` is excluded EXPLICITLY here -- before this it was invisible only
because os.listdir is non-recursive, which a recursive walk would have undone (#81)."""
import os

import pytest

from sluice.core.vault import (
    _MERGED_SUBDIR, _PRIVATE_SUBDIRS, Vault, _is_lead_note,
)


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _cannot_unread_a_dir():
    # TWO platforms where chmod 000 does not do what these tests need, and they fail in
    # OPPOSITE directions. As uid 0 the mode bits do not bind, so the directory stays
    # readable and the test passes VACUOUSLY -- the dangerous direction. On Windows chmod
    # cannot remove read access from a directory at all, so the walk succeeds and the test
    # fails outright, which is noise rather than a finding. geteuid is absent on Windows;
    # -1 never equals 0, so the order of these two terms does not matter.
    return os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0


# ── the exclusion set ─────────────────────────────────────────────────────────
def test_merged_subdir_is_a_private_subdir():
    """One constant, two consumers: the walk prunes `_PRIVATE_SUBDIRS` under leads_dir and
    _archived_match opens leads_dir/_MERGED_SUBDIR. If they ever name different directories
    every archived loser becomes an active note again."""
    assert _MERGED_SUBDIR in _PRIVATE_SUBDIRS


def test_scan_dirs_includes_user_subfolders_and_excludes_merged(tmp_path):
    leads = _leads_dir(tmp_path)
    (leads / "Active").mkdir(parents=True)
    (leads / "Interview Prep").mkdir()
    (leads / _MERGED_SUBDIR).mkdir()
    dirs = Vault(str(tmp_path))._scan_dirs()
    assert str(leads) in dirs
    assert str(leads / "Active") in dirs
    assert str(leads / "Interview Prep") in dirs          # the user's, so it is scanned
    assert str(leads / _MERGED_SUBDIR) not in dirs        # sluice's, so it is not


def test_scan_dirs_excludes_merged_but_not_a_nested_lookalike(tmp_path):
    """The prune is TOP-LEVEL only, because leads_dir/_merged is the one directory
    merge_cluster writes and _archived_match reads. A same-named directory nested deeper
    is the user's and must stay visible, or its notes are re-created as duplicates."""
    leads = _leads_dir(tmp_path)
    (leads / "Active" / _MERGED_SUBDIR).mkdir(parents=True)
    (leads / _MERGED_SUBDIR).mkdir()
    dirs = Vault(str(tmp_path))._scan_dirs()
    assert str(leads / _MERGED_SUBDIR) not in dirs
    assert str(leads / "Active" / _MERGED_SUBDIR) in dirs


# ── caching ───────────────────────────────────────────────────────────────────
def test_scan_dirs_falls_back_to_the_leads_dir_before_it_exists(tmp_path):
    assert Vault(str(tmp_path))._scan_dirs() == [str(_leads_dir(tmp_path))]


def test_scan_dirs_does_not_cache_the_missing_leads_dir(tmp_path):
    """upsert CREATES leads_dir mid-run, so caching 'it does not exist' would leave every
    later lookup in that run blind to the directory it just wrote into."""
    v = Vault(str(tmp_path))
    assert v._scan_dirs() == [str(_leads_dir(tmp_path))]
    sub = _leads_dir(tmp_path) / "Active"
    sub.mkdir(parents=True)
    assert str(sub) in v._scan_dirs()


def test_scan_dirs_is_cached_once_the_leads_dir_exists(tmp_path):
    """Re-deriving per lead costs ~1.4s per 500-lead run against ~4ms cached."""
    leads = _leads_dir(tmp_path)
    leads.mkdir(parents=True)
    v = Vault(str(tmp_path))
    first = v._scan_dirs()
    (leads / "Added Later").mkdir()
    assert v._scan_dirs() == first      # same instance, same answer


# ── the lead predicate ────────────────────────────────────────────────────────
def test_a_file_with_either_company_or_role_is_a_lead():
    """NEITHER, not EITHER. A hand edit that blanks `role` must not make the note invisible:
    invisible to read_leads is invisible to the write path, so the next scrape re-creates it
    as a duplicate. Requiring both would do exactly that."""
    assert _is_lead_note({"company": "Acme"})
    assert _is_lead_note({"role": "Analyst"})
    assert _is_lead_note({"company": "Acme", "role": "Analyst"})


def test_a_file_with_neither_company_nor_role_is_not_a_lead():
    """A user's interview-prep or research note living alongside the leads."""
    assert not _is_lead_note({})
    assert not _is_lead_note({"status": "new"})
    assert not _is_lead_note({"company": "", "role": ""})


# ── an unreadable directory is loud ───────────────────────────────────────────
@pytest.mark.skipif(_cannot_unread_a_dir(),
                    reason="chmod 000 binds neither uid 0 nor Windows")
def test_an_unreadable_subdirectory_raises_rather_than_reading_as_empty(tmp_path):
    """os.walk's DEFAULT onerror=None silently yields nothing for a directory it cannot
    open. Measured: a 6-note vault reads as 3 notes, no error, no log. Every note in it
    would then be invisible to the write path and re-created -- mass re-ingest arriving
    through a permissions bit."""
    leads = _leads_dir(tmp_path)
    (leads / "Archive").mkdir(parents=True)
    (leads / "Archive" / "Acme - Analyst.md").write_text('---\ncompany: "Acme"\n---\n')
    os.chmod(leads / "Archive", 0o000)
    try:
        with pytest.raises(OSError):
            Vault(str(tmp_path))._scan_dirs()
    finally:
        os.chmod(leads / "Archive", 0o755)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py -q`
Expected: FAIL at import — `cannot import name '_PRIVATE_SUBDIRS' from 'sluice.core.vault'`.

- [ ] **Step 3: Add the constant**

In `sluice/core/vault.py`, immediately after the `_MERGED_SUBDIR` line (~51):

```python
# Directories under leads_dir that SLUICE owns, pruned from the scan set. Everything else
# under leads_dir is the user's and is scanned -- a `_`-prefix rule instead would silently
# swallow a user folder named `_archive`, and a lead invisible to read_leads is invisible to
# the write path too, so every note in it is re-created as a duplicate on the next scrape.
# Before this existed `_merged/` was invisible only INCIDENTALLY (os.listdir is
# non-recursive and `_merged` is a directory, so it failed the `.endswith(".md")` test);
# a recursive walk would have surfaced every archived loser and undone #81 outright.
_PRIVATE_SUBDIRS = frozenset({_MERGED_SUBDIR})
```

- [ ] **Step 4: Add the module-level helpers**

After `_parse_fm_spaced` (~line 95):

```python
def _reraise(exc: OSError) -> None:
    """os.walk's onerror hook. Its DEFAULT is to SWALLOW the error and yield nothing for a
    directory it could not open, which turns one permissions bit into an invisible subtree:
    every lead in it disappears from read_leads AND from the write path's lookup, so the
    next scrape re-creates all of them. The store already refuses to read an unreadable
    dedup file as empty for the same reason -- this is that rule at directory scale."""
    raise exc


def _is_lead_note(fm: dict) -> bool:
    """Does this file's frontmatter make it a LEAD, as opposed to a note the user keeps
    alongside their leads (interview prep, research)? Once the scan is recursive those
    share the tree, and treating every `.md` as a lead would triage them.

    NEITHER, not EITHER. This is the predicate _archived_match already uses, and it is
    right in both places for the SAME reason rather than a mirrored one: skipping too
    eagerly loses a note that really exists. There, a skipped archive entry stops
    suppressing, so a lead a human merged away is resurrected (#81). Here, a skipped file
    drops a lead from read_leads and from _locate, so the next scrape mints a duplicate.
    A hand edit that blanks `role` -- the #16 threat model, a human in Obsidian -- must
    therefore leave the note a lead, so one surviving field is enough."""
    return bool(fm.get("company") or fm.get("role"))
```

**Do not annotate these with `NoReturn` or `Iterator`.** Measured: `ruff 0.15.21 check --select
E4,E7,E9,F` reports `F821 Undefined name` for a **quoted** annotation naming an unimported type, so
`-> "NoReturn"` is a build failure, not a safe hedge. Either import the name at module scope or use
the plain annotations given here. The plain ones are used throughout this plan.

- [ ] **Step 5: Add the cache slot**

In `Vault.__init__`, after `self._name_max_cache: int | None = None` (~line 124):

```python
        # The scan set, computed once per store instance. Re-deriving it per lead costs
        # ~1.4s on a 5500-note vault across a 500-lead run against ~4ms cached (measured).
        # The staleness window is a human creating a subfolder mid-run; the cost is one
        # duplicate note, which is the recoverable direction and the same posture the
        # existing create-race takes.
        self._scan_dirs_cache: list[str] | None = None
```

- [ ] **Step 6: Add `_walk` and `_scan_dirs`**

After `_slug_for` (~line 134):

```python
    # ── the scan set ─────────────────────────────────────────────────────────
    def _walk(self):
        """Yield (dirpath, filenames) for every scanned directory under leads_dir, with
        `_PRIVATE_SUBDIRS` pruned. Unannotated deliberately: the return type needs
        `Iterator`, and a quoted annotation naming an unimported type is ruff F821.

        THE one definition of the scan set: read_leads, normalize_all_statuses and
        _scan_dirs all consume this, so the exclusion cannot be applied in one place and
        forgotten in another -- and forgetting it in read_leads resurrects every note a
        human merged away (#81).

        The prune is applied only when dirpath IS leads_dir, because leads_dir/_merged is
        the single directory merge_cluster writes and _archived_match reads. Pruning the
        name at every depth would instead hide a same-named directory the USER made, whose
        notes would then be re-created as duplicates.

        onerror=_reraise, never the default: see there."""
        for dirpath, dirnames, filenames in os.walk(self.leads_dir, onerror=_reraise):
            if dirpath == self.leads_dir:
                dirnames[:] = [d for d in dirnames if d not in _PRIVATE_SUBDIRS]
            yield dirpath, filenames

    def _scan_dirs(self) -> list[str]:
        """The scan set as a directory list, cached. Falls back to [leads_dir] before that
        directory exists, and does NOT cache that answer: upsert creates leads_dir mid-run,
        so caching 'missing' would leave every later lookup in the same run blind to the
        directory it had just written into."""
        if not os.path.isdir(self.leads_dir):
            return [self.leads_dir]
        if self._scan_dirs_cache is None:
            self._scan_dirs_cache = [dirpath for dirpath, _ in self._walk()]
        return self._scan_dirs_cache
```

`_walk` is never called on a missing `leads_dir` — `_scan_dirs`, `read_leads` and
`normalize_all_statuses` each guard with `os.path.isdir` first — so `onerror` only ever fires for a
directory that exists and cannot be read, which is the case it is for.

- [ ] **Step 7: Share the predicate with `_archived_match`**

In `_archived_match` (~line 332), replace:

```python
                if not fm.get("company") and not fm.get("role"):
```

with:

```python
                if not _is_lead_note(fm):
```

Behaviour-preserving by De Morgan (`not a and not b` ≡ `not (a or b)`), and the existing
`test_company_only_archived_entry_is_still_a_note` and its role sibling already pin it. Sharing the
predicate is what stops the two call sites drifting apart later.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py -q`
Expected: all selected tests pass. (No count: a total pinned in prose goes stale the moment
the file gains a test, and a stale number reads as a real failure.)

- [ ] **Step 9: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts`
Expected: zero failures, ruff clean. Nothing consumes `_walk`/`_scan_dirs` yet, so no
pre-existing test may change. (No absolute count: a total pinned in prose goes stale the
moment any other branch lands a test, and a stale number reads as a real failure.)

- [ ] **Step 10: Mutation witnesses**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak
```

**Mutant A — the loud walk.** In `_walk`, DELETE `, onerror=_reraise` from the `os.walk(...)` call,
returning it to the default. Run:
`.venv/bin/python -m pytest "tests/test_vault_recursive_scan.py::test_an_unreadable_subdirectory_raises_rather_than_reading_as_empty" -q`
Expected: **FAIL** — `DID NOT RAISE`. The unreadable directory reads as empty, which is the whole
defect. (Skipped as root; if the test skips rather than fails, you are uid 0 and this witness proved
nothing — run it as a normal user.)

**Mutant B — `neither` vs `either`.** Restore, then in `_is_lead_note` change `or` to `and`:
`return bool(fm.get("company") and fm.get("role"))`. This is a MOVE of the operator, not an added
check. Run:
`.venv/bin/python -m pytest "tests/test_vault_recursive_scan.py::test_a_file_with_either_company_or_role_is_a_lead" -q`
Expected: **FAIL** on the first two assertions — a note carrying only one field stops being a lead,
which on the read path means it is dropped and the next scrape duplicates it.

**Mutant C — the predicate exists at all.** Restore, then make `_is_lead_note` `return True`. Run:
`.venv/bin/python -m pytest "tests/test_vault_recursive_scan.py::test_a_file_with_neither_company_nor_role_is_not_a_lead" -q`
Expected: **FAIL**.

```bash
cp /tmp/vault.py.bak sluice/core/vault.py && rm /tmp/vault.py.bak
.venv/bin/python -m pytest tests/test_vault_recursive_scan.py -q     # green again
```

- [ ] **Step 11: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_recursive_scan.py
git commit -m "feat(vault): define the scan set, with _merged/ excluded by name

Before this, _merged/ was invisible to the lead scan only incidentally: os.listdir
is non-recursive and the entry is a directory, so it failed the .md test. Nothing
named it. Making the scan recursive without this would surface every loser
merge_cluster archived and undo #81's non-resurrection.

_walk is the ONE definition of the scan set so the exclusion cannot be applied in
one consumer and forgotten in another, and it passes onerror=_reraise because
os.walk's default silently yields nothing for a directory it cannot open --
measured, a 6-note vault reads as 3 with no error and no log.

_is_lead_note is shared with _archived_match rather than restated: neither
company nor role means the file is not a lead. Nothing consumes the new helpers
yet.

Refs #1"
```

---

### Task 2: `read_leads` walks the scan set

**Files:**
- Modify: `sluice/core/vault.py:449-471` (`read_leads`)
- Test: `tests/test_vault_recursive_scan.py` (append), `tests/test_vault_subfolder_resolution.py` (create)

**Interfaces:**
- Consumes: `Vault._walk`, `_is_lead_note` (Task 1).
- Produces: `read_leads` returning `LeadNote`s from any scanned directory, ordered by full path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_recursive_scan.py`:

```python
# ── read_leads over the scan set ──────────────────────────────────────────────
def _write_note(path, company="Acme", role="Analyst", status="new"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ncompany: "{company}"\nrole: "{role}"\nstatus: {status}\n---\n\nbody\n')
    return path


def test_read_leads_returns_notes_from_subfolders(tmp_path):
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Acme - Analyst.md")
    _write_note(leads / "Active" / "Acme - Engineer.md", role="Engineer")
    _write_note(leads / "Archive" / "Acme - Clerk.md", role="Clerk", status="dismiss")
    slugs = {n.slug for n in Vault(str(tmp_path)).read_leads()}
    assert slugs == {"Acme - Analyst", "Acme - Engineer", "Acme - Clerk"}


def test_read_leads_orders_by_full_path(tmp_path):
    """The fixture makes full-path order and BASENAME order diverge, and they come out
    exact reverses of each other (verified: "Active" < "Archive" since c < r, while
    "Acme - Z" > "Acme - A"). Two notes in ONE directory cannot tell the two orders
    apart, so such a fixture would pass under either rule and pin neither."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Z.md", role="Z")
    _write_note(leads / "Archive" / "Acme - A.md", role="A", status="dismiss")
    got = [n.slug for n in Vault(str(tmp_path)).read_leads()]
    assert got == ["Acme - Z", "Acme - A"]   # full-path order
    assert got != sorted(got)                # which basename order would exactly reverse


def test_read_leads_skips_a_note_that_is_not_a_lead(tmp_path):
    """The whole point of motivation 2: a user gets somewhere to put other notes, and
    sluice must not start triaging them."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md")
    prep = leads / "Interview Prep" / "Questions to ask.md"
    prep.parent.mkdir(parents=True)
    prep.write_text("---\ntags: prep\n---\n\nWhat does success look like?\n")
    assert [n.slug for n in Vault(str(tmp_path)).read_leads()] == ["Acme - Analyst"]


def test_read_leads_keeps_a_lead_whose_role_was_blanked(tmp_path):
    """`neither`, not `either`. Dropping this note would make the next scrape re-create it."""
    leads = _leads_dir(tmp_path)
    _write_note(leads / "Active" / "Acme - Analyst.md", role="")
    assert [n.slug for n in Vault(str(tmp_path)).read_leads()] == ["Acme - Analyst"]
```

Create `tests/test_vault_subfolder_resolution.py`:

```python
"""The write path across a subfoldered lead store. A lead's identity is its note NAME, so a
note found in any scanned directory is reconciled in place -- never re-created."""
import os

from sluice.core.leads import Lead
from sluice.core.vault import _MERGED_SUBDIR, Vault
from tests.conftest import LOCATIONS


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _lead(**kw):
    base = dict(
        source="cord", search="Analyst", title="Analyst", company="Acme",
        url="https://ex.invalid/1", location=LOCATIONS[0], salary="",
        job_type="permanent", first_seen="2026-07-07", last_seen="2026-07-07",
    )
    base.update(kw)
    return Lead(**base)


def _two_note_vault(tmp_path):
    """Two on-disk notes at token-DISJOINT locations, so same_opportunity proves them
    DIFFERENT and upsert really seats two notes (an empty location is UNKNOWN evidence and
    would silently merge the second into the first, leaving nothing to merge later)."""
    v = Vault(str(tmp_path))
    v.upsert(_lead(location=LOCATIONS[0], url="https://ex.invalid/1"))
    v.upsert(_lead(location=LOCATIONS[1], url="https://ex.invalid/2"))
    return v


def test_a_merged_away_loser_stays_invisible_to_the_recursive_scan(tmp_path):
    """THE #1/#81 regression. merge_cluster archives the loser under `_merged/`, which the
    old flat os.listdir skipped only because it is a directory. A recursive walk that did
    not prune it by name would return the loser as an active lead again -- and a lead a
    human merged away, re-created and re-applied to, is a second application under their
    name. No test on main could catch this: the walk could not reach an archived note."""
    v = _two_note_vault(tmp_path)
    notes = v.read_leads()
    assert len(notes) == 2
    survivor, loser = notes[0], notes[1]
    archived = v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                               first_seen="2026-07-07", last_seen="2026-07-07")
    assert len(archived) == 1
    assert _MERGED_SUBDIR in archived[0]

    fresh = Vault(str(tmp_path))
    assert [n.slug for n in fresh.read_leads()] == [survivor.slug]
    assert loser.slug not in {n.slug for n in fresh.read_leads()}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py tests/test_vault_subfolder_resolution.py -q`
Expected: the four `read_leads` tests FAIL (subfolder notes not found; the prep note returned).
`test_a_merged_away_loser_stays_invisible_to_the_recursive_scan` PASSES already — it is the
vacuous baseline, and Step 6 proves it stops being vacuous.

- [ ] **Step 3: Rewrite `read_leads`**

Replace the body of `read_leads` (`sluice/core/vault.py:449-471`) with:

```python
    def read_leads(self, statuses: set | None = None) -> list:
        """Every lead note as a VaultNote (frontmatter parsed, status normalized),
        filtered to `statuses` (compared against the normalized status) when
        given. This is the read seam triage consumes; the sink still writes Leads.

        Walks the SCAN SET (see _walk), not one flat directory, so a note the user filed in
        a subfolder is still a lead. Two consequences worth stating because both are load-
        bearing: `_merged/` is pruned by NAME there rather than surviving on the accident
        that os.listdir is flat (#81), and a file carrying neither company nor role is
        skipped, or a user's interview-prep notes would be triaged as leads.

        Ordered by full path. For a flat store that is byte-identical to the previous
        sorted(os.listdir(...)), so nothing downstream sees an ordering change."""
        out: list = []
        if not os.path.isdir(self.leads_dir):
            return out
        want = {_status.normalize(s) for s in statuses} if statuses else None
        paths = []
        for dirpath, filenames in self._walk():
            paths.extend(os.path.join(dirpath, n) for n in filenames if n.endswith(".md"))
        for path in sorted(paths):
            try:
                inner, body = _split_frontmatter(_read(path))
            except OSError:
                continue
            fm = _fm_dict(inner)
            if not _is_lead_note(fm):
                continue
            st = _status.normalize(fm.get("status", ""))
            if want is not None and st not in want:
                continue
            out.append(LeadNote(ref=path, slug=self._slug_for(path),
                                fm=fm, body=body, status=st))
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py tests/test_vault_subfolder_resolution.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts`
Expected: all green. If a pre-existing test breaks on a fixture `.md` file with no
`company`/`role`, that is the predicate working — fix the fixture to carry one, do not weaken
the predicate.

- [ ] **Step 6: Prove the prune is load-bearing (mutation witness)**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak
```

In `_walk`, **delete** the two pruning lines (`if dirpath == self.leads_dir:` and the
`dirnames[:] = ...` beneath it). Deleting, not commenting and not adding a second check —
a check added beside the original is an equivalent mutant and stays green.

```bash
.venv/bin/python -m pytest \
  tests/test_vault_subfolder_resolution.py::test_a_merged_away_loser_stays_invisible_to_the_recursive_scan -q
```
Expected: **FAIL** — the archived loser is returned by `read_leads`.

**Mutant B — the predicate on the read path.** Restore, then DELETE the two lines
`if not _is_lead_note(fm): continue` from `read_leads`. Run:
`.venv/bin/python -m pytest "tests/test_vault_recursive_scan.py::test_read_leads_skips_a_note_that_is_not_a_lead" -q`
Expected: **FAIL** — the user's interview-prep note is returned as a lead. Task 1's unit tests on
`_is_lead_note` do NOT cover this: they prove the predicate computes the right answer, not that
`read_leads` consults it.

```bash
cp /tmp/vault.py.bak sluice/core/vault.py && rm /tmp/vault.py.bak
.venv/bin/python -m pytest tests/test_vault_subfolder_resolution.py tests/test_vault_recursive_scan.py -q
```

Restore from the saved copy, never `git checkout --` — that discards uncommitted work and the
empty diff afterwards hides the loss.

- [ ] **Step 7: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_recursive_scan.py \
        tests/test_vault_subfolder_resolution.py
git commit -m "feat(vault): read_leads walks the scan set

A note the user filed in a subfolder is now a lead, which is what makes the lead
directory usable at thousands of notes. Two guards ride with it: _merged/ is
pruned by NAME rather than surviving on os.listdir being flat, and a file with
neither company nor role is skipped so a user's interview-prep notes are not
triaged as leads.

Witnessed: deleting the prune reddens
test_a_merged_away_loser_stays_invisible_to_the_recursive_scan, which was
vacuous on main because the walk could not reach an archived note.

Refs #1"
```

---

### Task 3: `normalize_all_statuses` walks the scan set

Separate from Task 2 because this one WRITES. Missing the predicate here does not hide a note,
it rewrites a `status:` line inside a user's own note — a clobber.

**Files:**
- Modify: `sluice/core/vault.py:709-759` (`normalize_all_statuses`)
- Test: `tests/test_vault_recursive_scan.py` (append)

**Interfaces:**
- Consumes: `Vault._walk`, `_is_lead_note` (Task 1).
- Produces: no signature change — `normalize_all_statuses(dry_run=False) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_recursive_scan.py`:

```python
# ── normalize_all_statuses over the scan set ──────────────────────────────────────
def test_normalize_statuses_reaches_a_note_in_a_subfolder(tmp_path):
    leads = _leads_dir(tmp_path)
    p = _write_note(leads / "Archive" / "Acme - Clerk.md", role="Clerk", status="Dismissed")
    summary = Vault(str(tmp_path)).normalize_all_statuses()
    assert summary["changed"] == 1
    assert "status: dismiss" in p.read_text()


def test_normalize_statuses_never_writes_into_a_users_own_note(tmp_path):
    """never-clobber. A note carrying a `status:` line that is not a lead's is the user's
    business; rewriting it is exactly the wholesale-clobber sluice exists to remove."""
    leads = _leads_dir(tmp_path)
    prep = leads / "Interview Prep" / "Pipeline.md"
    prep.parent.mkdir(parents=True)
    original = "---\nstatus: Parked\ntags: prep\n---\n\nnotes\n"
    prep.write_text(original)
    Vault(str(tmp_path)).normalize_all_statuses()
    assert prep.read_text() == original


def test_normalize_statuses_never_writes_into_an_archived_loser(tmp_path):
    leads = _leads_dir(tmp_path)
    loser = leads / _MERGED_SUBDIR / "Acme - Clerk.md"
    loser.parent.mkdir(parents=True)
    original = '---\ncompany: "Acme"\nrole: "Clerk"\nstatus: Dismissed\n---\n\nbody\n'
    loser.write_text(original)
    Vault(str(tmp_path)).normalize_all_statuses()
    assert loser.read_text() == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py -k normalize -q`
Expected: `test_normalize_statuses_reaches_a_note_in_a_subfolder` FAILS (`changed == 0`).
The other two PASS vacuously — the flat walk cannot reach either file. Step 4 makes them real.

- [ ] **Step 3: Change the walk and add the predicate**

In `normalize_all_statuses`, replace:

```python
        for name in sorted(os.listdir(self.leads_dir)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(self.leads_dir, name)
            try:
                inner, _ = _split_frontmatter(_read(path))
            except OSError:
                continue
            if inner is None:
```

with:

```python
        paths = []
        for dirpath, filenames in self._walk():
            paths.extend(os.path.join(dirpath, n) for n in filenames if n.endswith(".md"))
        for path in sorted(paths):
            name = os.path.relpath(path, self.leads_dir)
            try:
                inner, _ = _split_frontmatter(_read(path))
            except OSError:
                continue
            if inner is None:
```

…then, immediately AFTER the existing `if inner is None:` block (which increments `unchanged` and
continues), insert the predicate:

```python
            # A file that is not a lead is the USER's -- an interview-prep or research note
            # they filed alongside their leads, now that the scan is recursive. Rewriting a
            # `status:` line inside one is a wholesale clobber of content sluice does not
            # own. Unlike read_leads, skipping here costs nothing: there is no lead to lose.
            if not _is_lead_note(_fm_dict(inner)):
                continue
```

**The order matters and is not cosmetic.** `_fm_dict(None)` returns `{}` (verified), so a
frontmatter-less file is not a lead. Putting the predicate FIRST would `continue` past it instead of
counting it `unchanged`, silently changing a number this method reports. Keeping the `inner is None`
arm first leaves that count byte-identical.

`name` becomes the path relative to `leads_dir`, so the `conflicts`/`skipped` buckets identify a
note in a subfolder unambiguously instead of reporting a bare filename that may now occur twice.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_recursive_scan.py -k normalize -q`
Expected: all selected tests pass. (No count, for the reason given at Step 9 of task 1.)

- [ ] **Step 5: Witness the two guards that were vacuous**

```bash
cp sluice/core/vault.py /tmp/vault.py.bak
```

**Mutant A** — delete the `if not _is_lead_note(_fm_dict(inner)): continue` lines from
`normalize_all_statuses`. Run:
`.venv/bin/python -m pytest tests/test_vault_recursive_scan.py::test_normalize_statuses_never_writes_into_a_users_own_note -q`
Expected: **FAIL** — the user's note is rewritten to `status: parked` or similar.

**Mutant B** — restore, then delete the prune from `_walk` again. Run:
`.venv/bin/python -m pytest tests/test_vault_recursive_scan.py::test_normalize_statuses_never_writes_into_an_archived_loser -q`
Expected: **FAIL** — the archived loser is rewritten.

```bash
cp /tmp/vault.py.bak sluice/core/vault.py && rm /tmp/vault.py.bak
```

- [ ] **Step 6: Run the full suite and lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/core/vault.py tests/test_vault_recursive_scan.py
git commit -m "feat(vault): normalize_all_statuses walks the scan set

Split from read_leads because this pass WRITES. Missing the lead predicate here
does not hide a note, it rewrites a status: line inside a note sluice does not
own -- the wholesale clobber never-clobber exists to prevent. Archived losers are
excluded for the same reason.

The conflicts/skipped buckets now carry the path relative to leads_dir rather
than a bare filename, which can occur twice once subfolders exist.

Witnessed: deleting the predicate reddens the user-note guard; deleting the
prune reddens the archived-loser guard. Both were vacuous before this commit.

Refs #1"
```

---

### Task 4: `_resolve_path` resolves candidates across the scan set

The delicate one — this is #5's candidate walk and #81's archive probe.

**Files:**
- Modify: `sluice/core/vault.py:393-441` (`_resolve_path`), new `_locate` beside `_scan_dirs`
- Test: `tests/test_vault_subfolder_resolution.py` (append)

**Interfaces:**
- Consumes: `Vault._scan_dirs` (Task 1).
- Produces: `Vault._locate(self, name: str) -> list[str]`. `_resolve_path` keeps its
  `(path: str | None, action: str)` return; `action` gains no new member — ambiguity reuses
  the existing `"refuse"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_subfolder_resolution.py`:

```python
def _seed_one(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    return _leads_dir(tmp_path) / "Acme - Analyst.md"


def test_a_note_moved_to_a_subfolder_is_updated_not_recreated(tmp_path):
    """Identity is the note NAME, not its path. This is what lets `leads reconcile` (PR B)
    and #81's documented recovery move a note without the next scrape duplicating it."""
    note = _seed_one(tmp_path)
    archive = _leads_dir(tmp_path) / "Archive"
    archive.mkdir()
    moved = archive / note.name
    note.rename(moved)

    v = Vault(str(tmp_path))                      # fresh: the scan-set cache is per instance
    assert v.upsert(_lead(last_seen="2026-07-08")) == "updated"
    assert "last_seen: 2026-07-08" in moved.read_text()
    assert not note.exists()                      # nothing re-created at the flat name


def test_a_candidate_resolving_to_two_notes_refuses_and_writes_nothing(tmp_path):
    """Two notes claim one identity, so the store cannot know which lead this is. Bumping
    the wrong one leaves the other to rot silently, so it writes nothing and the sink keeps
    the lead out of seen.db to re-report next run."""
    note = _seed_one(tmp_path)
    archive = _leads_dir(tmp_path) / "Archive"
    archive.mkdir()
    twin = archive / note.name
    twin.write_text(note.read_text())             # a hand-made copy

    v = Vault(str(tmp_path))
    assert v.upsert(_lead(last_seen="2026-07-08")) == "refused"
    assert "last_seen: 2026-07-07" in note.read_text()
    assert "last_seen: 2026-07-07" in twin.read_text()


def test_a_new_lead_is_still_created_at_the_leads_dir_root(tmp_path):
    """PR A creates no folders and moves no notes: the write folder is unchanged. PR B is
    what points creates at Active/."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    assert (_leads_dir(tmp_path) / "Acme - Analyst.md").exists()


def test_a_merged_away_lead_is_still_suppressed_when_a_subfolder_exists(tmp_path):
    """#81 through the new lookup: the archive probe runs when the candidate resolves
    NOWHERE in the scan set, which is the same condition as before, not a weaker one."""
    v = _two_note_vault(tmp_path)
    # Keyed on url, never on read_leads' ORDER. read_leads sorts by full path and the
    # location-suffixed note sorts FIRST (" " < "."), so indexing merges away the note the
    # re-scrape below rebuilds -- and the assertion then passes on the wrong lead.
    by_url = {n.fm.get("url"): n for n in v.read_leads()}
    survivor, loser = by_url["https://ex.invalid/1"], by_url["https://ex.invalid/2"]
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                    first_seen="2026-07-07", last_seen="2026-07-07")
    (_leads_dir(tmp_path) / "Archive").mkdir()

    fresh = Vault(str(tmp_path))
    loser_lead = _lead(location=LOCATIONS[1], url="https://ex.invalid/2")
    # `== "merged_away"`, never a disjunction over both arms. The outcome here is
    # DETERMINISTIC: both sides carry the same non-empty url, so the url-proof gate is
    # satisfied. Accepting either arm would leave free the one distinction this whole
    # test exists for -- `merged_away` enters seen.db, which has no removal path, and
    # `merged_away_unproven` must never be recorded.
    assert fresh.upsert(loser_lead) == "merged_away"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_subfolder_resolution.py -q`
Expected: `test_a_note_moved_to_a_subfolder_is_updated_not_recreated` FAILS with `created`
(the flat lookup misses the moved note) and the ambiguity test FAILS with `updated`.

- [ ] **Step 3: Add `_locate`**

Immediately after `_scan_dirs`:

```python
    def _locate(self, name: str) -> list[str]:
        """Every path in the scan set holding a note called `name`. A lead's identity is its
        note NAME; which directory it sits in is not part of it, which is what lets a note be
        filed, archived or restored without the next scrape re-creating it.

        Deliberately does NOT apply _is_lead_note. A hand edit that blanked `company` and
        `role` would make the note un-findable here, and un-findable means re-created as a
        duplicate -- the opposite of what the predicate is for on the read path. A non-lead
        file squatting a lead's exact candidate name is reconciled against as though it were
        a lead, which is unchanged from the flat store and neither introduced nor widened.

        Returns a LIST, not the first hit: two notes at one name is ambiguous identity, and
        _resolve_path must refuse rather than pick one. See there."""
        found = []
        for dirpath in self._scan_dirs():
            path = os.path.join(dirpath, f"{name}.md")
            if os.path.exists(path):
                found.append(path)
        return found
```

- [ ] **Step 4: Change the candidate walk**

In `_resolve_path`, replace:

```python
        names, capped = self._candidate_names(lead.company, lead.title, lead.location)
        for name in names:
            path = os.path.join(self.leads_dir, f"{name}.md")
            if not os.path.exists(path):
```

with:

```python
        names, capped = self._candidate_names(lead.company, lead.title, lead.location)
        for name in names:
            found = self._locate(name)
            if len(found) > 1:
                # Two notes claim one identity, so there is no safe write: bumping either
                # one's last_seen leaves the other to rot unnoticed. Nothing sluice does
                # produces this -- creates go to one directory and (PR B) reconcile refuses
                # a colliding move -- so it arrives by hand, from a copied note or a
                # part-way manual reorganisation. Refuse loudly and let the sink keep the
                # lead out of seen.db so it re-reports until a human merges or renames.
                _log.warning("vault refused lead %r: %r resolves to %d notes (%s)",
                             lead.dedup_key, name, len(found), ", ".join(sorted(found)))
                return None, "refuse"
            if not found:
```

and, in the block that follows, replace the create-path line and the read that comes after the
`if not os.path.exists(path):` block. The whole loop body becomes:

```python
        for name in names:
            found = self._locate(name)
            if len(found) > 1:
                _log.warning("vault refused lead %r: %r resolves to %d notes (%s)",
                             lead.dedup_key, name, len(found), ", ".join(sorted(found)))
                return None, "refuse"
            if not found:
                # #81. Returns None, or one of the TWO outcome strings -- never a bool: the
                # url-PROVEN/weaker distinction decides whether the lead enters seen.db,
                # which is irreversible in one direction, so a bool cannot carry it.
                archived = self._archived_match(names, lead, capped)
                if archived:
                    return None, archived
                # The write folder is still leads_dir itself. PR B is what points a create
                # at Active/; PR A creates no directories and moves no notes.
                return os.path.join(self.leads_dir, f"{name}.md"), "create"
            path = found[0]
            inner, _ = _split_frontmatter(_read(path))
            action, _url_proven = self._reconcile(_fm_dict(inner), lead, capped)
            if action != "advance":
                return path, action
        return None, "refuse"
```

Keep the two existing comments inside that block (the url-proof discard note above
`self._reconcile`, and the `# DIFFERENT location, or a capped-title mismatch -> advance`
trailer) — they explain live behaviour that has not changed.

Also extend `_resolve_path`'s docstring, after the sentence ending `...so path is None. See #5.`:

```python
        A candidate is looked up across the SCAN SET (see _locate), not at one flat path, so
        a note the user filed in a subfolder is found and updated in place. A candidate
        resolving to TWO OR MORE notes is ambiguous identity and refuses -- see _locate.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_subfolder_resolution.py -q`
Expected: all selected tests pass. (No count, for the reason given at Step 9 of task 1.)

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts`
Expected: all green, including `tests/conformance/test_store_contract.py` and
`tests/test_vault_archived_probe.py` untouched.

- [ ] **Step 7: Mutation witnesses**

```bash
cp sluice/core/vault.py /tmp/vault.py.bak
```

**Mutant A** — in `_locate`, replace the loop over `self._scan_dirs()` with a single
`self.leads_dir` lookup (move, not add):

```python
        path = os.path.join(self.leads_dir, f"{name}.md")
        return [path] if os.path.exists(path) else []
```

Run: `.venv/bin/python -m pytest tests/test_vault_subfolder_resolution.py::test_a_note_moved_to_a_subfolder_is_updated_not_recreated -q`
Expected: **FAIL** — `created`, not `updated`.

**Mutant B** — restore, then delete the `if len(found) > 1:` block. Run:
`.venv/bin/python -m pytest tests/test_vault_subfolder_resolution.py::test_a_candidate_resolving_to_two_notes_refuses_and_writes_nothing -q`
Expected: **FAIL** — `updated`, and one file's `last_seen` moved.

Confirm no PRE-EXISTING test is what catches either mutant: with the mutant in place run the
named node id alone, then `.venv/bin/python -m pytest tests/test_vault.py tests/test_vault_archived_probe.py -q`
and check those stay green. A mutation killed by a pre-existing test witnesses nothing about a new one.

```bash
cp /tmp/vault.py.bak sluice/core/vault.py && rm /tmp/vault.py.bak
```

- [ ] **Step 8: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_subfolder_resolution.py
git commit -m "feat(vault): resolve name candidates across the scan set

A lead's identity is its note NAME, so a note the user filed in a subfolder is
found and updated in place rather than re-created at the flat name. That is the
property PR B's \`leads reconcile\` needs, and it is also what keeps #81's
documented recovery working -- a note moved back out of _merged/ is picked up
wherever it lands.

_locate returns a LIST because two notes at one name is ambiguous identity:
bumping either one's last_seen leaves the other to rot unnoticed, so the walk
returns the existing refuse outcome and writes nothing. Nothing sluice does
produces that state; it arrives by hand.

The archive probe's call site, arguments and both merged_away outcomes are
unchanged -- what moved is only how the walk concludes that no active note
exists, from one os.path.exists to a search across the scan set.

Witnessed: reverting _locate to a single-folder lookup reddens the moved-note
test; deleting the ambiguity guard reddens the two-notes test. Neither is caught
by a pre-existing test.

Refs #1"
```

---

### Task 5: The `os.makedirs` scope guard

**Files:**
- Test: `tests/test_vault_makedirs_scope.py` (create)

**Interfaces:**
- Consumes: `_PRIVATE_SUBDIRS` (Task 1).
- Produces: nothing consumed downstream.

- [ ] **Step 1: Write the test**

Create `tests/test_vault_makedirs_scope.py`:

```python
"""Scope guard: every os.makedirs/os.mkdir call in vault.py is accounted for.

_PRIVATE_SUBDIRS names the directories pruned from the scan set. If a later change adds a
directory under leads_dir without adding it there, the walk returns its notes as active
leads -- which for an archive is #81's resurrection. This test cannot know a new call's
intent, so it fails on ANY unrecognised os.makedirs/os.mkdir call and makes the author
classify it.

It asserts on the SCOPE, not on violations: an AST sweep that matched nothing would satisfy
every assertion over an empty set, and for a guard whose success case is 'found nothing
wrong' that is indistinguishable from working.

LIMIT: the sweep is keyed on names bound to os.makedirs/os.mkdir (see _local_dirmakers), so
a directory made via pathlib.Path(...).mkdir() -- a method call, not one of those names --
would evade it entirely. Today's risk is zero: vault.py creates directories only through the
four os.makedirs sites classified below (verified by hand, not by this guard), and nothing
in it calls os.mkdir or pathlib. But that is a fact about the code today, not a guarantee
this test enforces -- a future pathlib-based makedirs call ships unclassified and silent."""
import ast
import pathlib


_VAULT = pathlib.Path(__file__).resolve().parents[1] / "sluice" / "core" / "vault.py"

# Every os.makedirs argument expression in vault.py, and why each is not a scan-set concern.
_EXPECTED = {
    # The Syncthing marker, at the VAULT root -- not under leads_dir, never scanned.
    "os.path.join(self.dir, '.stfolder')": "syncthing marker, vault root",
    # write_document's parent dir, derived from a document key under the vault root.
    "os.path.dirname(path)": "document parent, vault root",
    # The lead write folder itself. Scanned, and it is the root of the scan set.
    "self.leads_dir": "the write folder",
    # leads_dir/_merged -- under leads_dir, and therefore MUST be in _PRIVATE_SUBDIRS.
    "merged_dir": "the merge archive, pruned from the scan set",
}


_DIRMAKERS = {"makedirs", "mkdir"}


def _local_dirmakers(tree):
    """Every local name in vault.py that reaches os.makedirs/os.mkdir, DERIVED from that
    module's own import nodes rather than hand-listed.

    `import os as _o` and `from os import makedirs as _mk` are the same call under a
    different spelling, and a sweep keyed on the literal "os.makedirs" sees neither --
    while the existing calls keep the scope assertion satisfied, so BOTH tests stay green
    and a new unguarded directory ships. Measured: a hard-listed matcher misses four of
    five spellings; this one catches all five and returns the identical sites on the real
    file. That is the documented "hand-listed names lose to an import alias" failure and
    its documented fix."""
    modules, direct = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os":
                    modules.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.module == "os":
            for a in n.names:
                if a.name in _DIRMAKERS:
                    direct.add(a.asname or a.name)
    return {f"{m}.{f}" for m in modules for f in _DIRMAKERS} | direct


def _makedirs_args():
    tree = ast.parse(_VAULT.read_text())
    names = _local_dirmakers(tree)
    return [ast.unparse(n.args[0]) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and ast.unparse(n.func) in names]


def test_the_sweep_actually_finds_the_makedirs_calls():
    """The scope assertion. Without it a matcher that silently stopped matching -- an
    ast.unparse spelling change, a renamed import -- would leave every assertion below
    trivially true."""
    found = _makedirs_args()
    assert len(found) >= 4, f"AST sweep found only {found!r}; the matcher is broken"


def test_every_makedirs_call_is_classified():
    unexpected = set(_makedirs_args()) - set(_EXPECTED)
    assert not unexpected, (
        f"vault.py creates {unexpected}, which this guard does not classify. If it is under "
        f"leads_dir and holds notes sluice owns, add its name to _PRIVATE_SUBDIRS so the scan "
        f"skips it; otherwise add it to _EXPECTED with the reason it is not scanned.")
```

`_MERGED_SUBDIR in _PRIVATE_SUBDIRS` is deliberately NOT re-asserted here —
`tests/test_vault_recursive_scan.py::test_merged_subdir_is_a_private_subdir` owns that pin, and the
same one-line assertion in two files is duplication a reviewer would rightly flag. Import
`_MERGED_SUBDIR` here only if `_EXPECTED` ends up referencing it.

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_vault_makedirs_scope.py -q`
Expected: all selected tests pass. If `test_every_makedirs_call_is_classified` fails, the
`_EXPECTED` keys do not match this checkout's `ast.unparse` output — print `_makedirs_args()`
and correct the keys verbatim rather than loosening the assertion.

- [ ] **Step 3: Witness the scope assertion**

```bash
cp tests/test_vault_makedirs_scope.py /tmp/scope.bak
```

Change `_makedirs_args`'s filter to a name that does not exist (`"os.makedirsX"`), so the sweep
returns `[]`. Run: `.venv/bin/python -m pytest tests/test_vault_makedirs_scope.py -q`
Expected: `test_the_sweep_actually_finds_the_makedirs_calls` **FAILS**, and
`test_every_makedirs_call_is_classified` **passes** — which is exactly the vacuous
pass the scope assertion exists to catch.

```bash
cp /tmp/scope.bak tests/test_vault_makedirs_scope.py && rm /tmp/scope.bak
```

- [ ] **Step 4: Run the full suite, lint, commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add tests/test_vault_makedirs_scope.py
git commit -m "test(vault): pin every directory vault.py creates

_PRIVATE_SUBDIRS only works if it stays complete. A directory added under
leads_dir without a matching entry is returned by the recursive walk as active
leads, and for an archive that is #81's resurrection. The guard cannot know a new
call's intent, so it fails on any unrecognised makedirs and makes the author
classify it.

Asserts on the SCOPE as well as the violations: an AST sweep matching nothing
satisfies every assertion over an empty set, which for a negative guard reads
exactly like success. Witnessed by breaking the matcher -- the scope test goes
red while the violation test stays green.

Refs #1"
```

---

### Task 6: Documentation

**Files:**
- Modify: `sluice/core/protocols.py` (`Store.read_leads`)
- Modify: `docs/ARCHITECTURE.md` (vault section)
- Modify: `.rulesync/rules/CLAUDE.md` (the #81 / never-clobber paragraph)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: State what a lead is, in the contract**

`Store.read_leads` currently has no docstring (`sluice/core/protocols.py`, the
`def read_leads(self, statuses: set | None = None) -> list: ...` line). Give it one:

```python
    def read_leads(self, statuses: set | None = None) -> list:
        """Every stored lead as a LeadNote, filtered to `statuses` when given.

        A store decides for itself what counts as a lead, but it MUST NOT return records it
        does not own. The filesystem store shares its directory with whatever else the user
        keeps there, so it returns only files whose frontmatter carries a company or a role;
        a store with its own table has this by construction.

        A merged-away loser is NOT returned (see upsert). For the vault that exclusion is by
        NAME -- the archive directory is pruned from the scan -- rather than a side effect of
        a flat listing, because the scan is recursive.
        """
        ...
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`**

Find the vault/store section (`grep -n "leads_dir\|Job Leads\|read_leads" docs/ARCHITECTURE.md`)
and add, at the altitude of vault-implementation detail rather than Store contract:

```markdown
The lead scan is **recursive**. `Vault._walk` defines the scan set once — every directory under
`Job Applications/Job Leads`, minus `_PRIVATE_SUBDIRS` (today just `_merged/`) — and `read_leads`,
`normalize_all_statuses` and `_locate` all consume it, so the exclusion cannot be applied in one place
and forgotten in another. Pruning `_merged/` by name is load-bearing: before the scan was recursive
it was invisible only because `os.listdir` is flat, and a walk that reached it would return every
loser `sluice leads dedupe --merge` archived, undoing #81.

Two rules follow from sharing a directory with the user's own notes. A file counts as a lead only
if its frontmatter carries a `company` or a `role` (`_is_lead_note`) — *neither*, not *either*, so a
hand edit that blanks one field does not make a lead invisible and therefore duplicated. And a
lead's identity is its note NAME, not its path: `_locate` searches the whole scan set, so a note the
user files in a subfolder is updated in place. A name resolving to two or more notes is ambiguous
identity and `upsert` refuses.

An unreadable directory in the scan set **raises** (`os.walk(..., onerror=)`). The default swallows
it and yields nothing, which would make every lead beneath it invisible to the read path and to the
write path — i.e. re-created — from one permissions bit.
```

- [ ] **Step 3: Update `.rulesync/rules/CLAUDE.md`**

In the paragraph beginning "**Non-resurrection (#81), in the never-clobber family.**", after the
sentence ending "`_merged/` is load-bearing retention, not scratch: do not prune it.", add:

```markdown
The lead scan is recursive (#1), so `_merged/` is excluded from it BY NAME
(`_PRIVATE_SUBDIRS`) rather than by the accident that a flat `os.listdir` never descended into
it -- deleting that prune resurfaces every archived loser and undoes this invariant outright.
```

- [ ] **Step 4: Regenerate the rulesync outputs and check for drift**

```bash
npm ci --ignore-scripts && npm run rulesync
diff <(tail -n +9 .rulesync/rules/CLAUDE.md) CLAUDE.md && echo "generated outputs in sync"
```

The generated files (`CLAUDE.md`, `AGENTS.md`, `.claude/`) are gitignored — do not stage them.
If the diff is non-empty the regeneration did not run; fix that rather than hand-editing.

- [ ] **Step 5: Run the full suite, lint, commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/core/protocols.py docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "docs: record the scan set, the lead predicate and the _merged/ prune

The Store contract now says a store must not return records it does not own,
which is what the filesystem store's company-or-role predicate implements: it
shares a directory with whatever else the user keeps there.

.rulesync/rules/CLAUDE.md's #81 paragraph gains the sentence that stops a future
reader deleting the prune -- it reads as a redundant filter unless you know the
scan is recursive and that _merged/ used to be invisible by accident.

Refs #1"
```

---

## Definition of Done

- [ ] `.venv/bin/python -m pytest` reports ZERO failures. Not a target count: a number pinned
      here is stale as soon as any other branch lands a test, and it then reads as a failure.
- [ ] `.venv/bin/python -m ruff check sluice tests scripts` clean.
- [ ] Every mutation witness in Tasks 2, 3, 4 and 5 reddens its own named test, run BY NODE ID,
      with the neighbouring pre-existing vault tests confirmed still green under the same mutant.
- [ ] `tests/conformance/test_store_contract.py` passes unchanged — no contract property was added
      or weakened.
- [ ] No `os.listdir` CALL remains on the lead scan. Checked through the parser, not by grep:

  ```bash
  .venv/bin/python -c "import ast; src=open('sluice/core/vault.py').read(); \
  print(sorted((n.lineno, n.func.attr) for n in ast.walk(ast.parse(src)) \
  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr=='listdir'))"
  ```

  Every surviving call must be one of: the undescended-symlink warning, `_archived_match`'s
  archive probe, `read_experience_entries`. A text grep cannot make this check — bare
  `os.listdir` counts comments, and even `os\.listdir\(` matches `read_leads`' own docstring,
  which quotes the call it replaced. Both would pass with the lead-scan calls still in place.
- [ ] No config key, no CLI command, no directory created that did not exist before.
- [ ] `git log --oneline main..HEAD` reads as six coherent commits, each `Refs #1`.
- [ ] Run `/review-pr` BEFORE pushing.
