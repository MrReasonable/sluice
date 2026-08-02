# Merged-Lead Resurrection (#81) Implementation Plan

> **Superseded 2026-07-31 (pre-push review):** the outcome mapping this plan implements changed
> during the review that preceded the push. `_ARCHIVED`/`merged_away` is now gated on a url-PROVEN
> match, not on `same_opportunity`'s SAME verdict -- a location-only SAME falls to
> `merged_away_unproven` and is never recorded in `seen.db`, because a same-company/title/location
> re-post carrying a brand-new url is a real job and `seen.db` has no removal path. `_reconcile`
> accordingly returns `(action, url_proven)` rather than an action alone. The steps are left as
> executed; see `docs/ARCHITECTURE.md` and `core/protocols.py` for the shipped contract.
>
> **Superseded 2026-08-02 (#1, vault subfolders):** the `refused` log line this plan's steps
> quote (`"vault refused lead %r: every name candidate is a note proven different"`) named ONE
> cause. The shipped line names two -- every candidate proven different, or one candidate
> resolving to several notes -- because `_resolve_path` reports both as `refuse` and cannot
> distinguish them. See `core/vault.py:upsert` for all five causes the outcome now covers.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a lead a human merged away via `sluice leads dedupe --merge` from being silently re-created when the dedup set is empty.

**Architecture:** `Vault._resolve_path` gains one branch at the single point that creates: before returning `create`, probe `leads_dir/_merged/` — where `merge_cluster` archives losers — for a note matching any of the lead's name candidates. A hit returns a new no-write outcome instead of a path. The archived note IS the durable record; no new store is added. The verdict logic already inside `_resolve_path`'s loop is extracted to one helper so the probe cannot drift from the active walk.

**Tech Stack:** Python 3.12+, standard library only. pytest. No new dependencies.

**Design spec:** `docs/superpowers/specs/2026-07-31-merged-lead-resurrection-design.md` — twice reviewed (`/review-plan` rounds 1 and 2, 37 findings, all folded). Read it before starting; this plan implements it and does not restate its reasoning.

**Branch:** `fix/merged-lead-resurrection` @ `4e951ae`. Baseline: **1780 tests pass**, `ruff check sluice tests scripts` clean.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency. `re`, `os`, `logging` are already imported in `core/vault.py`.
- **Never-clobber.** No step may make a re-scrape write anything but `last_seen`. Every new outcome writes NOTHING.
- **No personal data in `sluice/` or `tests/`.** Locations come from `tests/conftest.py`'s `LOCATIONS` (`Alfa`/`Bravo`/`Charlie`). Titles/companies use `tests/test_vault.py`'s abstract placeholders (`X`, `Y`, `Acme`). URLs use `.invalid` — `ex.invalid` in `tests/test_vault_merge_cluster.py`, `example.invalid` in `tests/conformance/test_store_contract.py`. **No faker pools** — `tests/test_leads_cluster.py`'s docstring already ruled these fixtures are deliberately non-faker-derived.
- **Run before any mutation testing:** `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- **Interpreter:** use `.venv/bin/python`. A bare `python` hits a broken shim in this environment and fails with `proto::detect::failed`.
- **Commits:** Conventional Commits, `Refs #81` in the body (never `Closes`, until the final task).
- **Every task ends green:** `.venv/bin/python -m pytest` and `.venv/bin/python -m ruff check sluice tests scripts`.

## File Structure

| File | Responsibility |
| --- | --- |
| `sluice/core/vault.py` | `_reconcile` (new, private, shared verdict), `_archived_match` (new, the probe), `_resolve_path` branch, `upsert` branch |
| `sluice/ingest/sink.py` | Allowlist admits `merged_away` only; `merged_away_unproven` stays out of `seen.db` |
| `sluice/cli.py` | Two sparse report lines |
| `sluice/core/protocols.py` | `upsert` and `merge_cluster` docstrings — the contract |
| `tests/test_vault.py` | Walk-side extraction witness; `upsert` branch placement |
| `tests/test_vault_archived_probe.py` | **New.** Every probe behaviour |
| `tests/test_ingest_sink.py` | Both arms' `seen.db` behaviour |
| `tests/conformance/test_store_contract.py` | Non-resurrection property; vocabulary widened to six |
| `docs/ARCHITECTURE.md` | Store contract, dedupe section, create-path cost |

---

### Task 1: Extract the shared verdict helper

**Why first:** every later task consumes it, and it is the only edit that touches `_resolve_path`'s existing behaviour. It must land behaviour-preserving and witnessed before anything is built on it.

**Files:**
- Modify: `sluice/core/vault.py` (`_resolve_path`, currently lines 173-224)
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces: `Vault._reconcile(self, fm: dict, lead: Lead, capped: bool) -> str`, returning `"update"` | `"merge"` | `"advance"`.

- [ ] **Step 1: Write the failing walk-side witness**

This test does not exist today and the mutant it guards currently leaves the **full suite green**.

First, `_seed_note` hardcodes `role: "Y"` and the test needs a MISMATCHED role, so give it a parameter. Replace the existing helper (`tests/test_vault.py:338-343`) with:

```python
def _seed_note(tmp_path, name, location="", url="", role="Y"):
    from sluice.core.vault import _LEADS_SUBDIR
    d = tmp_path / _LEADS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\ncompany: "X"\nrole: "{role}"\nlocation: "{location}"\nurl: "{url}"\n---\n\nbody\n')
```

The default keeps every existing caller byte-identical. Then add the test:

```python
def test_capped_gate_on_title_lost_is_load_bearing(tmp_path):
    """`title_lost` is gated on `capped`; without that gate a SHORT-title lead whose stored
    `role` was hand-corrected in Obsidian (#16's threat model) advances instead of merging,
    so `last_seen` stops advancing and #9's staleness sweep can expire a live posting.
    Deleting `capped and` from _reconcile leaves the rest of the suite green -- this is the
    only test that reddens.

    The note sits at `X - Y` but carries role "Z": url-less and location-less, so the
    verdict is UNKNOWN, and the title is short, so `capped` is False and title_lost MUST
    stay dormant. Under the mutant title_lost fires, the walk advances past the only
    candidate, and upsert refuses instead of merging."""
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="", url="", role="Z")
    assert v.upsert(_lead(company="X", title="Y", location="", url="")) == "merged"
```

- [ ] **Step 2: Run it — it must PASS against unmodified code**

Run: `.venv/bin/python -m pytest tests/test_vault.py::test_capped_gate_on_title_lost_is_load_bearing -v`
Expected: **PASS**. This is a characterisation test of existing behaviour, not a red-first test — the behaviour is already correct, it is merely unwitnessed. The red comes in Step 6.

- [ ] **Step 3: Run the whole file to confirm nothing else broke from the `_seed_note` signature change**

Run: `.venv/bin/python -m pytest tests/test_vault.py -q`
Expected: all pass (the new `role` parameter defaults to `"Y"`, so every existing caller is unchanged).

- [ ] **Step 4: Extract the helper**

In `sluice/core/vault.py`, add this method to `Vault`, immediately above `_resolve_path`:

```python
    def _reconcile(self, fm: dict, lead: Lead, capped: bool) -> str:
        """The ONE verdict, shared by the active walk and #81's archive probe: "update",
        "merge" or "advance". A second copy kept in sync by a comment is the #30 failure
        mode -- a check that must match another check, with prose standing in for the
        guarantee -- so both callers go through here.

        `capped` is the caller's, not re-derived: it measures the CHAR cap on the FULL
        `company - title` stem, which only the caller knows. Deleting the `capped and`
        below leaves the whole suite green except
        test_capped_gate_on_title_lost_is_load_bearing, and it is NOT an equivalent
        mutant -- it makes title_lost fire for short titles, so a human correcting a
        note's `role` in Obsidian turns every later re-scrape into an advance."""
        verdict = same_opportunity(fm, lead, self._noise)
        # A matching non-empty URL is same_opportunity's DEFINITIVE proof of the same
        # posting, so a drifted title tail on a url-stable posting must still update in
        # place rather than mint a digest note per drift.
        url_proven = (bool(lead.url) and bool(fm.get("url"))
                      and _norm_url(lead.url) == _norm_url(fm.get("url", "")))
        # A capped filename can seat a note whose FULL title differs -- only the truncated
        # prefix matched. Treat that as advance, exactly like a proven-different location.
        title_lost = (capped and not url_proven
                      and _title_key(fm.get("role", "")) != _title_key(lead.title))
        if title_lost:
            return "advance"
        if verdict == SAME:
            return "update"
        if verdict == UNKNOWN:
            return "merge"
        return "advance"
```

- [ ] **Step 5: Point `_resolve_path` at it**

In `_resolve_path`, replace the body of the `for name in names:` loop after the `create` return. The block currently reading:

```python
            inner, _ = _split_frontmatter(_read(path))
            fm = _fm_dict(inner)
            verdict = same_opportunity(fm, lead, self._noise)
            url_proven = (bool(lead.url) and bool(fm.get("url"))
                          and _norm_url(lead.url) == _norm_url(fm.get("url", "")))
            title_lost = (capped and not url_proven
                          and _title_key(fm.get("role", "")) != _title_key(lead.title))
            if verdict == SAME and not title_lost:
                return path, "update"
            if verdict == UNKNOWN and not title_lost:
                return path, "merge"
            # DIFFERENT location, or a capped-title mismatch -> advance to the next candidate
```

becomes:

```python
            inner, _ = _split_frontmatter(_read(path))
            action = self._reconcile(_fm_dict(inner), lead, capped)
            if action != "advance":
                return path, action
            # DIFFERENT location, or a capped-title mismatch -> advance to the next candidate
```

Move the two explanatory comments (`url_proven`, `title_lost`) into `_reconcile` as shown in Step 4 — do not leave copies behind, or the reasoning drifts in two places.

- [ ] **Step 6: Witness the extraction — mutate by DELETING**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak          # NOT git checkout: it would wipe uncommitted work
```

Delete `capped and ` from `_reconcile`'s `title_lost` (leaving `title_lost = (not url_proven`). Then:

Run: `.venv/bin/python -m pytest tests/test_vault.py -q`
Expected: **exactly one failure** — `test_capped_gate_on_title_lost_is_load_bearing`.

Then run it by node id alone to confirm no pre-existing test in the file is what catches it:

Run: `.venv/bin/python -m pytest tests/test_vault.py::test_capped_gate_on_title_lost_is_load_bearing -q`
Expected: FAIL (`assert 'refused' == 'merged'` or similar).

Restore: `cp /tmp/vault.py.bak sluice/core/vault.py`

- [ ] **Step 7: Witness the other two terms**

Repeat Step 6's mutate/run/restore for each, confirming each reddens at least one test:
- delete `and not url_proven` from `title_lost`
- delete the `if title_lost: return "advance"` line entirely

Expected: each reddens ≥1 test in `tests/test_vault.py`. Record which.

- [ ] **Step 8: Full suite + lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts`
Expected: a FULLY GREEN run (0 failures) and ruff clean, with one more test collected than before
this step. Do not pin an absolute count: a sentinel keyed to a number that has since moved stops
sentinelling silently, and this plan's own later steps add tests.

- [ ] **Step 9: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "refactor(vault): extract the one verdict shared by walk and probe

_resolve_path's verdict block becomes Vault._reconcile, so #81's archive probe
cannot drift from the active walk -- a second copy kept in sync by a comment is
the #30 failure mode. Behaviour-preserving.

Adds the witness the extraction needs: deleting \`capped and\` from title_lost
left the FULL suite green before this test, and is not an equivalent mutant.

Refs #81"
```

---

### Task 2: The archive probe — anchored match and verdict dispatch

**Files:**
- Modify: `sluice/core/vault.py` (add `_archived_match`; branch in `_resolve_path`)
- Create: `tests/test_vault_archived_probe.py`

**Interfaces:**
- Consumes: `Vault._reconcile(fm, lead, capped) -> str` (Task 1).
- Produces: `Vault._archived_match(self, names: list[str], lead: Lead, capped: bool) -> str | None`, returning `None`, `"merged_away"` or `"merged_away_unproven"`. Module constants `_MERGED_SUBDIR = "_merged"`, `_ARCHIVED = "merged_away"`, `_ARCHIVED_UNPROVEN = "merged_away_unproven"`.

- [ ] **Step 1: Write the failing acceptance test**

Create `tests/test_vault_archived_probe.py`:

```python
"""#81: the write path must honour a human's merge decision.

A lead archived to `_merged/` by `leads dedupe --merge` must not be re-created when the
dedup set is empty. Fixtures are synthetic: LOCATIONS placeholders, abstract company/role,
`.invalid` urls -- no faker (see tests/test_leads_cluster.py's ruling)."""
import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _lead(**kw):
    base = dict(source="cord", search="s", title="Y", company="X",
                url="https://ex.invalid/1", location=LOCATIONS[0],
                first_seen="2026-07-07", last_seen="2026-07-07")
    base.update(kw)
    return Lead(**base)


def _merge_away(v, loser_lead, survivor_lead):
    """Archive `loser_lead`'s note through the REAL merge_cluster, so the fixture cannot
    drift from what the production archive path actually writes."""
    assert v.upsert(survivor_lead) == "created"
    assert v.upsert(loser_lead) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor, loser = notes[survivor_lead.url], notes[loser_lead.url]
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[loser_lead.url],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    return survivor, loser


def test_merged_away_lead_is_not_recreated(tmp_path):
    """The acceptance property. Fails on 10b0cdd, where upsert returns 'created'."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    assert len(v.read_leads()) == 1

    # The dedup set is empty (0-byte/tableless seen.db, fresh machine, retargeted SEEN_DB),
    # so the loser is not filtered at ingest and reaches the write path again.
    assert v.upsert(loser) == "merged_away"
    assert len(v.read_leads()) == 1
```

- [ ] **Step 2: Run it — verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py::test_merged_away_lead_is_not_recreated -v`
Expected: FAIL — `assert 'created' == 'merged_away'`. This is the defect, reproduced.

- [ ] **Step 3: Add the module constants**

In `sluice/core/vault.py`, beside the existing module constants (near `_LEADS_SUBDIR`):

```python
_MERGED_SUBDIR = "_merged"          # where merge_cluster archives losers (#23)
_ARCHIVED = "merged_away"           # #81: proven -- SAME verdict; the sink records it
_ARCHIVED_UNPROVEN = "merged_away_unproven"   # #81: UNKNOWN verdict; NEVER recorded
```

Then use `_MERGED_SUBDIR` in `merge_cluster` (`vault.py:718`) in place of the `"_merged"` literal, so the probe and the archiver cannot disagree about the directory name.

- [ ] **Step 4: Implement `_archived_match`**

Add to `Vault`, immediately below `_reconcile`:

```python
    def _archived_match(self, names, lead: Lead, capped: bool) -> str | None:
        """#81: has a human already merged this lead away? Returns the outcome string, or
        None to let the walk create.

        Probes EVERY name candidate, not just the one the walk stopped at: the walk returns
        at its first ABSENT candidate, but the loser may have been archived under its
        location-suffixed or title-digest name, which the walk would never reach.

        The match is ANCHORED -- exact name, or exact name plus merge_cluster's numeric
        suffix. NOT a bare prefix: same_opportunity compares only url and location, never
        company or title, so in the active walk the exact FILENAME is what carries title
        identity. A bare prefix removes that anchor and cannot get it back -- a merged-away
        `X - Y II` would swallow a genuinely different `X - Y` at the same location, and
        (on the SAME arm) record it in seen.db, so the real job could never be created.
        `title_lost` is no backstop: it is gated on `capped`, dormant under 120 chars.

        A sequential `<stem>.1.md`, `<stem>.2.md` walk is NOT equivalent to the listdir: it
        stops at the first miss, and restoring a note out of `_merged/` -- the documented
        recovery -- punches exactly that hole, hiding every archive behind it."""
        merged_dir = os.path.join(self.leads_dir, _MERGED_SUBDIR)
        try:
            entries = sorted(os.listdir(merged_dir))
        except FileNotFoundError:
            # Never merged: the overwhelmingly common case, and NOT an error. Caught
            # specifically rather than by a bare `except OSError`, which would also swallow
            # an unreadable directory and silently disarm this guard on the vaults where it
            # matters most.
            return None
        for name in names:
            pattern = re.compile(re.escape(name) + r"(?:\.\d+)?\.md\Z")
            for entry in entries:
                if not pattern.match(entry):
                    continue
                path = os.path.join(merged_dir, entry)
                # No `except OSError` here, deliberately. The nearest neighbour, read_leads,
                # does `except OSError: continue` -- copying that shape would make an
                # UNREADABLE archived loser stop suppressing, re-minting the lead as an
                # ordinary `created: N`: resurrection by way of a permissions error. Letting
                # it propagate makes the sink count the lead `skipped` and keep it out of
                # seen.db for a retry next run.
                inner, _ = _split_frontmatter(_read(path))
                fm = _fm_dict(inner)
                # Is this a NOTE at all? Keyed on company/role, never on url/location: a real
                # note can carry url:"" (google leads) AND a blank location, which is exactly
                # the UNKNOWN case this probe exists to suppress. Testing the same keys the
                # verdict consumes would collapse "is this a note" into "what does it say"
                # and skip a legitimate loser. merge_cluster's own O_EXCL reservation leaves
                # a 0-byte file here if the process dies before os.replace, and its cleanup
                # is best-effort, so this arm is reachable in the field.
                if not fm.get("company") and not fm.get("role"):
                    _log.warning("vault: ignoring unreadable archived note %s", path)
                    continue
                action = self._reconcile(fm, lead, capped)
                if action == "update":
                    _log.warning("vault: %r was merged away (archived at %s); not re-created",
                                 lead.dedup_key, path)
                    return _ARCHIVED
                if action == "merge":
                    # UNKNOWN -- suppressed on weak evidence, so it must NEVER enter seen.db.
                    _log.warning("vault: %r may have been merged away (archived at %s, "
                                 "evidence inconclusive); not re-created", lead.dedup_key, path)
                    return _ARCHIVED_UNPROVEN
        return None
```

- [ ] **Step 5: Branch in `_resolve_path`**

Replace the create arm:

```python
            if not os.path.exists(path):
                return path, "create"
```

with:

```python
            if not os.path.exists(path):
                # #81. Returns None, or one of the TWO outcome strings -- never a bool: the
                # SAME/UNKNOWN distinction decides whether the lead enters seen.db, which is
                # irreversible in one direction, so a bool cannot carry it.
                archived = self._archived_match(names, lead, capped)
                if archived:
                    return None, archived
                return path, "create"
```

- [ ] **Step 6: Run the acceptance test**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py -v`
Expected: PASS.

- [ ] **Step 7: Add the remaining probe tests**

Append to `tests/test_vault_archived_probe.py`:

```python
def test_proven_different_archived_note_does_not_suppress(tmp_path):
    """A genuinely new job at a merged-away name is still created: DIFFERENT advances."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    # Same name as the archived loser, but a token-disjoint location -> DIFFERENT.
    fresh = _lead(title="Widget Engineer Senior", url="", location=LOCATIONS[1])
    assert v.upsert(fresh) == "created"


def test_unknown_verdict_suppresses_as_unproven(tmp_path):
    """A blank location on either side is UNKNOWN: suppress, but on the UNPROVEN arm."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    blank = _lead(title="Widget Engineer Senior", url="", location="")
    assert v.upsert(blank) == "merged_away_unproven"


def test_bare_prefix_would_over_match_a_different_job(tmp_path):
    """The ANCHOR witness. Two genuinely different jobs whose names share a prefix; the
    LONGER is merged away, then the SHORTER is scraped. A bare `startswith` match would
    suppress it -- and its verdict is UNKNOWN, so the 'treat DIFFERENT as a hit' mutant
    does not reach this case and it needs its own test."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Anchor Survivor", url="https://ex.invalid/9")
    longer = _lead(title="Y II", url="https://ex.invalid/2", location="")
    _merge_away(v, longer, survivor)
    shorter = _lead(title="Y", url="", location="")
    assert v.upsert(shorter) == "created"


def test_loser_archived_under_its_location_suffixed_name_is_found(tmp_path):
    """The probe covers ALL candidates, not just the one the walk stopped at. Candidate 1
    must ALSO be absent -- otherwise the 'probe only where the walk stopped' mutant stays
    green, because the walk itself stops at candidate 2."""
    v = Vault(str(tmp_path))
    third = _lead(title="Third Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    cand1 = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(cand1) == "created"
    # A token-disjoint location at the same name forces the location-suffixed candidate.
    cand2 = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(cand2) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    survivor = notes["https://ex.invalid/9"]
    v.merge_cluster(survivor.ref,
                    [notes["https://ex.invalid/1"].ref, notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/1", "https://ex.invalid/2"],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    assert v.upsert(cand2) == "merged_away"


def test_numeric_suffix_archive_is_found(tmp_path):
    """merge_cluster archives a name-colliding loser as `<stem>.1.md`. Re-upsert B, NOT A:
    A sits at `_merged/<base>.md` and an exact-name probe already catches it, so only B's
    re-upsert exercises the suffix path at all."""
    v = Vault(str(tmp_path))
    third = _lead(title="Suffix Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    a = _lead(title="Y", url="https://ex.invalid/1", location=LOCATIONS[0])
    assert v.upsert(a) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/1"].ref],
                    alt_urls=["https://ex.invalid/1"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    # A proven-DIFFERENT B now takes the same active name, then is merged away too.
    b = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(b) == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes["https://ex.invalid/9"].ref, [notes["https://ex.invalid/2"].ref],
                    alt_urls=["https://ex.invalid/2"], first_seen="2026-07-01",
                    last_seen="2026-07-07")
    merged = sorted(os.listdir(os.path.join(v.leads_dir, "_merged")))
    assert any(e.endswith(".1.md") for e in merged), merged
    assert v.upsert(b) == "merged_away"


def test_hole_in_the_numeric_sequence_does_not_hide_an_archive(tmp_path):
    """Restoring a note out of `_merged/` -- the documented recovery -- punches a hole. A
    sequential `<stem>.N` walk stops there and misses everything behind it; the listdir
    does not."""
    v = Vault(str(tmp_path))
    third = _lead(title="Hole Survivor", url="https://ex.invalid/9")
    assert v.upsert(third) == "created"
    for n, loc in ((1, LOCATIONS[0]), (2, LOCATIONS[1]), (3, LOCATIONS[2])):
        lead = _lead(title="Y", url=f"https://ex.invalid/{n}", location=loc)
        assert v.upsert(lead) == "created"
        notes = {x.fm.get("url"): x for x in v.read_leads()}
        v.merge_cluster(notes["https://ex.invalid/9"].ref,
                        [notes[f"https://ex.invalid/{n}"].ref],
                        alt_urls=[f"https://ex.invalid/{n}"], first_seen="2026-07-01",
                        last_seen="2026-07-07")
    merged_dir = os.path.join(v.leads_dir, "_merged")
    hole = [e for e in os.listdir(merged_dir) if e.endswith(".1.md")]
    assert hole, os.listdir(merged_dir)
    os.replace(os.path.join(merged_dir, hole[0]), os.path.join(v.leads_dir, "restored.md"))
    # The lead behind the hole must still be suppressed.
    behind = _lead(title="Y", url="https://ex.invalid/3", location=LOCATIONS[2])
    assert v.upsert(behind) == "merged_away"
```

- [ ] **Step 8: Add the capped-title probe tests, WITH the control arm**

Without these, every probe scenario has `capped` false, so `title_lost` and `url_proven` are dormant throughout and a probe that re-implements the verdict without PR #48's refinements is green. Append:

```python
_LONG = "Y" * 150      # forces the 120-char cap, so `capped` is True


def test_capped_title_probe_advances_on_a_lost_title(tmp_path):
    """PR #48's title_lost, reached through the PROBE. Two different jobs sharing the first
    120 chars of their name: the archived one must not suppress the other."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    other = _lead(title=_LONG + "B", url="", location=LOCATIONS[0])
    assert v.upsert(other) == "created"


def test_capped_title_probe_control_arm_suppresses_a_matching_title(tmp_path):
    """The CONTROL for the test above, and it is load-bearing: asserting only `created`
    there is byte-identical to a probe that never matched anything, so that test alone
    passes under a FULLY INERT probe. Same fixture, matching role -> must hit."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    same = _lead(title=_LONG + "A", url="", location=LOCATIONS[0])
    assert v.upsert(same) == "merged_away"


def test_capped_title_probe_url_match_overrides_a_lost_title(tmp_path):
    """A matching non-empty url is same_opportunity's DEFINITIVE proof, so a drifted title
    tail on a url-stable posting must still be suppressed rather than minted anew."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Capped Survivor", url="https://ex.invalid/9")
    loser = _lead(title=_LONG + "A", url="https://ex.invalid/2", location=LOCATIONS[0])
    _merge_away(v, loser, survivor)
    drifted = _lead(title=_LONG + "B", url="https://ex.invalid/2", location=LOCATIONS[0])
    assert v.upsert(drifted) == "merged_away"
```

- [ ] **Step 9: Run them all**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py -v`
Expected: all PASS. If `test_loser_archived_under_its_location_suffixed_name_is_found` or `test_numeric_suffix_archive_is_found` fails on fixture construction rather than the assertion, print `os.listdir(merged_dir)` and check where the archive actually landed — the recipes above are the reviewed ones, but verify the archive is where you think before changing an assertion.

- [ ] **Step 10: Mutation-witness every row**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak       # NOT git checkout: it would wipe uncommitted work
```

For each mutation: apply it by **MOVING or DELETING** (never by adding a check beside the original — that is an equivalent mutant and stays green), run `.venv/bin/python -m pytest tests/test_vault_archived_probe.py -q`, then `cp /tmp/vault.py.bak sluice/core/vault.py`.

| mutation | must redden |
| --- | --- |
| delete the `if archived:` branch from `_resolve_path` | acceptance + several others (expected; it is the whole feature) |
| replace `for name in names:` with `names[:1]` | `..._location_suffixed_name_is_found` |
| drop the `(?:\.\d+)?` group from the pattern | `test_numeric_suffix_archive_is_found` |
| replace the anchored `pattern.match` with `entry.startswith(name)` | `test_bare_prefix_would_over_match_a_different_job` |
| replace the `listdir` with a sequential `<stem>.N` walk stopping at the first miss | `test_hole_in_the_numeric_sequence_does_not_hide_an_archive` |
| delete the `if action == "merge"` arm (UNKNOWN keeps probing) | `test_unknown_verdict_suppresses_as_unproven` |
| change `if action == "update"` to `if action != "advance"` for the DIFFERENT case, i.e. make DIFFERENT a hit | `test_proven_different_archived_note_does_not_suppress` |
| delete the `company`/`role` skip guard | `test_zero_byte_reservation_is_skipped...` (Task 3) |
| return `_ARCHIVED` from the `merge` arm (collapse the two outcomes into one) | `test_unknown_verdict_suppresses_as_unproven` |
| delete `capped` from the `_reconcile` call (pass `False`) | `..._advances_on_a_lost_title` |

Then run each named witness **by node id alone** and confirm it fails on its own, so no sibling is what actually catches the mutant. Record any row that reddens nothing — a row with no witness is a finding, not a pass.

- [ ] **Step 9: Full suite + lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/core/vault.py tests/test_vault_archived_probe.py
git commit -m "fix(vault): do not re-create a lead a human merged away

_resolve_path now probes leads_dir/_merged/ before returning create. The match is
ANCHORED (exact name, or exact name plus merge_cluster's numeric suffix) over one
listdir: a bare prefix would over-match, because same_opportunity compares only url
and location and the exact filename is what carries title identity; a sequential
suffix walk would stop at a hole the documented recovery punches.

Two outcomes, because the SAME/UNKNOWN distinction decides whether the lead enters
seen.db and that is irreversible in one direction.

Refs #81"
```

---

### Task 3: Probe robustness — unreadable and non-note entries

**Files:**
- Test: `tests/test_vault_archived_probe.py`

**Interfaces:** consumes Task 2's `_archived_match`. No production change is expected — these tests pin behaviour Task 2 already wrote. If one fails, the bug is in Task 2's implementation.

- [ ] **Step 1: Write the tests**

```python
def test_zero_byte_reservation_is_skipped_not_treated_as_unknown(tmp_path):
    """merge_cluster's O_EXCL reservation leaves a 0-byte file under a real lead's archived
    name if the process dies before os.replace, and its cleanup is best-effort. Scored as
    UNKNOWN it would suppress every future lead at that name."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(title="Y", url="https://ex.invalid/1")) == "created"
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    open(os.path.join(merged_dir, "X - Y.md"), "w").close()   # the orphaned reservation
    fresh = _lead(title="Y", url="https://ex.invalid/2", location=LOCATIONS[1])
    assert v.upsert(fresh) in ("created", "updated", "merged")


def test_unreadable_archived_entry_raises_rather_than_silently_resurrecting(tmp_path):
    """`except OSError: continue` here -- read_leads' shape -- would turn a permissions
    error into a resurrection. It must propagate so the sink counts the lead `skipped`
    and keeps it OUT of seen.db for a retry."""
    v = Vault(str(tmp_path))
    survivor = _lead(title="Senior Widget Engineer", url="https://ex.invalid/1")
    loser = _lead(title="Widget Engineer Senior", url="https://ex.invalid/2")
    _merge_away(v, loser, survivor)
    merged_dir = os.path.join(v.leads_dir, "_merged")
    archived = os.path.join(merged_dir, os.listdir(merged_dir)[0])
    os.chmod(archived, 0o000)
    try:
        if os.access(archived, os.R_OK):        # root ignores the mode bits
            pytest.skip("running as root: cannot make a file unreadable")
        with pytest.raises(OSError):
            v.upsert(loser)
    finally:
        os.chmod(archived, 0o600)


def test_probe_is_a_no_op_when_nothing_was_ever_merged(tmp_path):
    """_merged/ is created lazily, so on any install that has never merged it does not
    exist. That is the common case and means 'no hit' -- FileNotFoundError specifically,
    never a bare `except OSError` that would also swallow an unreadable directory."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    assert not os.path.exists(os.path.join(v.leads_dir, "_merged"))
    assert v.upsert(_lead(url="https://ex.invalid/2", location=LOCATIONS[1])) == "created"
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py -v`
Expected: all PASS (Task 2 implemented the behaviour; these pin it).

- [ ] **Step 3: Mutation-witness the robustness arms**

Same mutate/run/restore cycle as Task 2 Step 10 (`cp sluice/core/vault.py /tmp/vault.py.bak` first).

| mutation | must redden |
| --- | --- |
| delete the `if not fm.get("company") and not fm.get("role")` skip guard | `test_zero_byte_reservation_is_skipped_not_treated_as_unknown` |
| change the skip guard to key on `url`/`location` instead of `company`/`role` | `test_unknown_verdict_suppresses_as_unproven` (Task 2) — a legitimate url-less, location-less loser is skipped and the lead resurrected |
| wrap the `_read` in `except OSError: continue` | `test_unreadable_archived_entry_raises_rather_than_silently_resurrecting` |
| broaden `except FileNotFoundError` to `except OSError` | nothing in this file — **this is a known blind spot.** Note it and move on; witnessing it needs an unreadable `_merged/` directory, which is a root-dependent fixture. The narrow catch is justified in the code comment instead. |

The last row is recorded deliberately: a mutation table that quietly omits a row it cannot witness reads as complete coverage. State the gap.

- [ ] **Step 4: Full suite + lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add tests/test_vault_archived_probe.py
git commit -m "test(vault): pin the archive probe's non-note and unreadable arms

A 0-byte O_EXCL reservation must SKIP (scored UNKNOWN it would suppress every
future lead at that name); an unreadable entry must RAISE, since read_leads'
\`except OSError: continue\` shape would turn a permissions error into a
resurrection; an absent _merged/ is the common case, not an error.

Refs #81"
```

---

### Task 4: The `upsert` branch and its placement

**Files:**
- Modify: `sluice/core/vault.py` (`upsert`, currently lines 546-600)
- Test: `tests/test_vault_archived_probe.py`

**Interfaces:** consumes `_ARCHIVED` / `_ARCHIVED_UNPROVEN` (Task 2).

- [ ] **Step 1: Write the failing writes-nothing test**

The fixture MUST hand-seed the archive. Built through `upsert` + `merge_cluster` like every other recipe here, `leads_dir` and `.stfolder` already exist, so the whole-tree snapshot is identical with and without the placement mutant — the test would be inert for the property it exists to pin.

```python
def test_merged_away_writes_nothing(tmp_path):
    """The refuse arm's own property, applied to the new arm. Note the assertion does NOT
    cover leads_dir: `_merged/` lives INSIDE it, so leads_dir necessarily exists in any
    scenario that can reach the probe -- asserting its absence would be unsatisfiable
    rather than strict. The archive is hand-seeded precisely so `.stfolder` does NOT
    already exist; built through upsert+merge_cluster, the setup itself creates it and the
    branch-placement mutant becomes invisible."""
    v = Vault(str(tmp_path))
    merged_dir = os.path.join(v.leads_dir, "_merged")
    os.makedirs(merged_dir, exist_ok=True)
    with open(os.path.join(merged_dir, "X - Y.md"), "w", encoding="utf-8") as f:
        f.write('---\ncompany: "X"\nrole: "Y"\nlocation: "%s"\nurl: ""\n---\n\nbody\n'
                % LOCATIONS[0])
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))

    assert v.upsert(_lead(title="Y", url="", location=LOCATIONS[0])) == "merged_away"

    assert not os.path.exists(os.path.join(v.leads_dir, "X - Y.md"))
    assert not os.path.exists(os.path.join(tmp_path, ".stfolder"))
```

- [ ] **Step 2: Run it — verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py::test_merged_away_writes_nothing -v`
Expected: FAIL — `upsert` currently has no branch for the new actions, so it falls through to `_write(None, ...)` and raises `TypeError: expected str, bytes or os.PathLike object, not NoneType`.

- [ ] **Step 3: Add the branch, beside `refuse` and BEFORE the makedirs**

In `upsert`, the existing refusal guard reads:

```python
            if action == "refuse":
                ...
                _log.warning("vault refused lead %r: every name candidate is a note proven different",
                             lead.dedup_key)
                return "refused"
```

Immediately after it — and before the `os.makedirs(self.leads_dir, exist_ok=True)` / `self.ensure_stfolder()` lines — add:

```python
            if action in (_ARCHIVED, _ARCHIVED_UNPROVEN):
                # #81. Beside `refuse`, NOT beside update/merge: those sit AFTER the makedirs
                # below, and a lead that writes nothing must not create the leads dir or the
                # Syncthing marker either. _archived_match has already logged which archive
                # matched. Both strings need this branch -- either one without it falls
                # through to _write(None, ...) and raises TypeError, which the sink's
                # `except OSError` does NOT catch and engine.py calls sink.write outside its
                # per-source try, so the whole ingest run would abort.
                return action
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py::test_merged_away_writes_nothing -v`
Expected: PASS.

- [ ] **Step 5: Witness the placement**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak
```

MOVE the new branch (delete it from its position, re-insert it immediately after `self.ensure_stfolder()`).

Run: `.venv/bin/python -m pytest tests/test_vault_archived_probe.py::test_merged_away_writes_nothing -v`
Expected: **FAIL** on the `.stfolder` assertion. If it PASSES, the fixture is not hand-seeded — go back to Step 1.

Restore: `cp /tmp/vault.py.bak sluice/core/vault.py`

- [ ] **Step 6: Full suite + lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/core/vault.py tests/test_vault_archived_probe.py
git commit -m "fix(vault): return the merged-away outcomes from upsert without writing

The branch sits beside refuse and BEFORE os.makedirs/ensure_stfolder: a lead that
writes nothing must not create the leads dir or the Syncthing marker. Without it,
either new action reaches _write(None, ...) and raises TypeError -- uncaught by the
sink's except OSError, and engine.py calls sink.write outside its per-source try,
so the run would abort.

Refs #81"
```

---

### Task 5: Sink and CLI plumbing

**Files:**
- Modify: `sluice/ingest/sink.py` (allowlist at `:41`, module docstring at `:4-9`)
- Modify: `sluice/cli.py` (`_print_report`, `:222-228`)
- Test: `tests/test_ingest_sink.py`

**Interfaces:** consumes the two outcome strings. `VaultSink.write` returns a sparse count dict; `merged_away` and `merged_away_unproven` appear only when non-zero.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ingest_sink.py` (follow the file's existing fake-store/fake-seendb pattern; if it has none, build a minimal fake whose `upsert` returns a scripted outcome and whose `save` records the leads it was given):

```python
def test_proven_merged_away_enters_seen_db(tmp_path):
    """The SAME arm self-heals the dedup state: suppressed once, then filtered at ingest."""
    saved = []
    sink = VaultSink(_FakeStore("merged_away"), _FakeSeen(saved))
    counts = sink.write([_lead()])
    assert counts["merged_away"] == 1
    assert [l.dedup_key for l in saved] == [_lead().dedup_key]


def test_unproven_merged_away_stays_out_of_seen_db(tmp_path):
    """The UNKNOWN arm is suppressed on weak evidence, so recording it would make
    engine.py filter the key forever -- SeenDb has load/save and no removal. It must
    re-surface and re-report until a human acts."""
    saved = []
    sink = VaultSink(_FakeStore("merged_away_unproven"), _FakeSeen(saved))
    counts = sink.write([_lead()])
    assert counts["merged_away_unproven"] == 1
    assert saved == []
```

- [ ] **Step 2: Run — verify both FAIL**

Run: `.venv/bin/python -m pytest tests/test_ingest_sink.py -k merged_away -v`
Expected: the first FAILS (the outcome is not in the allowlist, so nothing is saved). The second may pass vacuously today — it must still be present, because it is what stops a later "fix" adding both strings to the allowlist.

- [ ] **Step 3: Widen the allowlist**

In `sluice/ingest/sink.py`, change:

```python
                if outcome in ("created", "updated", "merged"):
```

to:

```python
                if outcome in ("created", "updated", "merged", "merged_away"):
```

and extend the comment above it so the widened list is not left under prose that no longer covers it:

```python
                    # Allowlist over "a note now exists", stated positively so an unknown
                    # outcome fails safe: refused (and the OSError->skipped below) stay OUT
                    # of `recorded` -> never enter seen.db -> retried next run. See #5.
                    #
                    # `merged_away` (#81) qualifies: the note exists, ARCHIVED under
                    # _merged/, so recording it self-heals the dedup set and the suppression
                    # happens once rather than on every run. `merged_away_unproven` does NOT
                    # and must never be added -- it is a suppression on UNKNOWN evidence, and
                    # seen.db has no removal path (load/save only), so recording it would
                    # make engine.py filter that key forever with no note anywhere.
```

Update the module docstring (`sink.py:4-9`) in the same commit — it currently enumerates the four outcomes as complete.

- [ ] **Step 4: Add the CLI report lines**

In `sluice/cli.py:_print_report`, after the existing `merged` / `refused` blocks:

```python
    if w.get("merged_away"):
        parts.append(f"{w['merged_away']} merged-away")
    if w.get("merged_away_unproven"):
        parts.append(f"{w['merged_away_unproven']} merged-away (unproven)")
```

Update the comment at `cli.py:222` which names the sparse keys.

- [ ] **Step 5: Run**

Run: `.venv/bin/python -m pytest tests/test_ingest_sink.py -v`
Expected: PASS.

- [ ] **Step 6: Witness the allowlist**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/ingest/sink.py /tmp/sink.py.bak
```

- Mutant A: DELETE `"merged_away"` from the tuple → `test_proven_merged_away_enters_seen_db` must FAIL.
- Mutant B: ADD `"merged_away_unproven"` to the tuple → `test_unproven_merged_away_stays_out_of_seen_db` must FAIL. (This is the one place an ADD is the right mutation: the defect being guarded IS an addition.)

Restore: `cp /tmp/sink.py.bak sluice/ingest/sink.py`

- [ ] **Step 7: Full suite + lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/ingest/sink.py sluice/cli.py tests/test_ingest_sink.py
git commit -m "feat(ingest): report merged-away leads and self-heal the dedup set

Only the PROVEN arm enters seen.db. The unproven arm is a suppression on UNKNOWN
evidence and seen.db has no removal path, so recording it would filter that key
forever with no note anywhere -- it re-surfaces and re-reports instead.

Refs #81"
```

---

### Task 6: The Store contract

**Files:**
- Modify: `sluice/core/protocols.py` (`upsert` docstring `:84-100`, `merge_cluster` docstring `:127-140`)
- Modify: `tests/conformance/test_store_contract.py`

**Interfaces:** consumes the two outcome strings.

- [ ] **Step 1: Write the failing conformance test**

Use the LOCATION-SPLIT shape, modelled on `test_merge_cluster_preserves_survivor_and_removes_losers` (`test_store_contract.py:159`). Do NOT use title word-order drift: that is a vault-filename artefact, so a store keyed on synthetic ids would reconcile both scrapes to one record and the assertions would pass having tested nothing — on exactly the store class the contract exists to constrain.

```python
def test_merged_away_lead_is_never_recreated(store_name, tmp_path, monkeypatch):
    """#81, a SAFETY property in the never-clobber family: a lead merged away via
    merge_cluster is never re-created by upsert. A store that archives losers and then
    creates freely resurrects them, so it must be stated -- a synthetic-id store does NOT
    get this for free.

    SCOPE assertions first: a test that merges nothing would satisfy the property
    trivially. Same shape as test_merge_cluster_preserves_survivor_and_removes_losers --
    two token-disjoint LOCATIONS, no filenames in the test's vocabulary."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[0])) == "created"
    assert store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1])) == "created"
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    loser = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/2")
    store.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://example.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    # SCOPE: the merge actually happened and the loser actually left the active view.
    assert len(store.read_leads()) == 1, "nothing was merged: the property below is vacuous"
    assert all(n.fm.get("url") != "https://example.invalid/2" for n in store.read_leads())

    # THE PROPERTY: the merged-away lead, re-scraped with the dedup set empty.
    outcome = store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1]))
    assert outcome != "created", f"{store_name} re-created a lead a human merged away"
    assert outcome in ("merged_away", "merged_away_unproven")
    assert len(store.read_leads()) == 1
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/conformance/test_store_contract.py -k merged_away -v`
Expected: PASS (Tasks 2-4 implemented it for the `vault` store). If it FAILS on the scope assertion, the fixture is wrong, not the code.

- [ ] **Step 3: Widen the vocabulary test, and pin BOTH strings**

`test_upsert_return_is_always_within_the_vocabulary` (`:495-502`) pins a four-member tuple and stays GREEN unmodified, because its scenario produces neither new string — so it is also green against an under-widening that adds only one of the two. One definition, two enforcement points:

Add a module-level constant near the top of `tests/conformance/test_store_contract.py`, beside `_STORES`:

```python
# One definition, enforced in TWO places. test_upsert_return_is_always_within_the_vocabulary
# checks membership on a scenario that produces neither #81 outcome, so it cannot police an
# under-widening; test_merged_away_lead_is_never_recreated actually produces one and does.
_VOCAB = ("created", "updated", "merged", "refused", "merged_away", "merged_away_unproven")
```

In `test_upsert_return_is_always_within_the_vocabulary`, delete the local `vocab = (...)` line and use `_VOCAB` in both assertions. Update its docstring from "four-outcome vocabulary" to "six-outcome vocabulary".

In `test_merged_away_lead_is_never_recreated` (Step 1), replace the literal tuple in the final membership assertion with `_VOCAB`:

```python
    assert outcome in _VOCAB
```

- [ ] **Step 4: State the contract in `protocols.py`**

In `upsert`'s docstring, after the `"refused"` clause, add:

```
        Two more (#81), both MAY-return: "merged_away" and "merged_away_unproven" -- the
        lead was already merged away by merge_cluster, so nothing is written. They differ
        only in evidence strength, and the caller uses that: the ingest sink records the
        PROVEN one in its dedup store and must never record the unproven one. A store with
        no archive concept never returns either.
```

and, separately from the MAY-return note, the MUST:

```
        MUST-honour for any store implementing merge_cluster: a lead merged away is NEVER
        re-created. That is a safety property in the never-clobber family -- it protects a
        human's decision from being silently undone, and re-creating the lead can mean a
        second application under the user's name. See tests/conformance/test_store_contract.py.
```

In `merge_cluster`'s docstring, state the OBLIGATION, not the vault's mechanism:

```
        A removed loser MUST remain discoverable by `upsert` and invisible to `read_leads`,
        so a later re-scrape of that lead is not re-created (#81). The vault keeps the whole
        note under `_merged/`; a natural-key tombstone satisfies it equally -- retention of
        the note itself is this store's mechanism, not the requirement. The returned handles
        are whatever identifies the removed records to this store; a tombstone id is a handle.
```

- [ ] **Step 5: Run, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add sluice/core/protocols.py tests/conformance/test_store_contract.py
git commit -m "feat(core): make non-resurrection a Store contract property

A store that archives losers and then creates freely resurrects them, so a
synthetic-id store does not get this for free and it must be stated. The
conformance test uses the location-split shape with SCOPE assertions -- the
title-drift shape is a vault-filename artefact and would pass vacuously on
exactly the store class the contract constrains.

merge_cluster's docstring states the OBLIGATION (discoverable by upsert,
invisible to read_leads) rather than the vault's retention mechanism.

Refs #81"
```

---

### Task 7: Docs, and reconcile every stale claim BY GREP

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: whatever the two greps below return.

- [ ] **Step 1: Reconcile the `#81` rationale sites**

```bash
grep -rn "#81" sluice tests docs --include='*.py' --include='*.md' | grep -v superpowers/
```

**Derive the list from this command. Do NOT copy a count from anywhere, including this line** — a
review of this work hand-listed five sites and missed seven, and the count moved again during
execution (the tasks before this one add `#81` references of their own). Read every hit and judge it;
the grep is the enumeration, not a number to match.

What changes is the SCOPE of the claim, not its truth. The refusals stay correct and must NOT be softened — an empty dedup set is still bad, and a merged-away lead can still be re-created when its re-scrape drifts past the name candidates. What goes stale is the unqualified form, *"an empty dedup set re-creates EVERY lead a human merged away"*. `tests/test_path_refusal.py:9` says `_resolve_path` "never consults `leads_dir/_merged/` (#81, true today and out of scope)" — that becomes flatly false.

Every one of the 12 is a comment or docstring. This is a PROSE update only: do not touch an assertion.

- [ ] **Step 2: Reconcile the outcome-vocabulary sites**

```bash
grep -rn 'created.*updated.*merged.*refused\|four-outcome\|four-member' sluice tests docs --include='*.py' --include='*.md' | grep -v superpowers/
```

The four-member vocabulary is asserted as complete in prose in ~11 places across 6 files, including `_resolve_path`'s own docstring (`vault.py:175`), `Vault.upsert`'s (`vault.py:547`), `sink.py:4`/`:9`/`:39`, and `cli.py:222`. None of them reddens. Derive the list, update each.

- [ ] **Step 3: Update `docs/ARCHITECTURE.md`**

Three edits:
1. The store-contract paragraph (~`:293`) — add the non-resurrection property and the six-member vocabulary.
2. The dedupe section (~`:378`) — `_merged/` is now read by the write path, not only written by `merge_cluster`.
3. Record the create-path cost change: it went from ZERO reads to one `os.listdir` of `_merged/` plus a read and frontmatter parse per anchor-matching entry.

- [ ] **Step 4: Verify no assertion was weakened**

```bash
git diff --stat
git diff -- tests/ | grep -E '^\+' | grep -E 'assert' || echo "no assertions added or changed in tests/ -- prose only, as required"
```

Expected: the only `tests/` changes are comments and docstrings.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check sluice tests scripts
git add -A
git commit -m "docs: reconcile the #81 and outcome-vocabulary claims with the fix

Twelve live sites stated #81's rationale as currently-true and eleven stated the
outcome vocabulary as four-member; none reddens. Both lists derived by grep, not
hand-listed -- a review of this work hand-listed five of the twelve. The refusals
themselves stay correct; only the unqualified form of their reason goes stale.

Refs #81"
```

---

## Definition of Done

1. `test_merged_away_lead_is_not_recreated` passes, and **fails on the pre-fix tree** — verify by stashing only the `sluice/` changes (`git stash push sluice/`) and re-running it, not by reasoning about it.
2. `_resolve_path`'s update/merge/refuse arms behaviourally unchanged — established by Task 1's extraction witnesses, NOT by "full suite green", which is provably non-falsifying here.
3. The verdict logic exists in exactly ONE place: `Vault._reconcile`, `vault.py`-private, NOT `core/leads.py` (PR #48 deliberately kept title comparison out of `same_opportunity`).
4. Both outcome strings plumbed through `upsert`, the CLI report and the vocabulary test; only `merged_away` in the sink allowlist.
5. Contract stated in the conformance suite and BOTH `upsert`'s and `merge_cluster`'s docstrings.
6. Every mutation named in this plan reddens **at least** its own witness, and **no witness is inert**. ("Exactly its own witness" is unsatisfiable — deleting the probe branch reddens four tests.)
7. Both grep-derived site lists reconciled; no test assertion weakened.
8. `.venv/bin/python -m pytest` green; `.venv/bin/python -m ruff check sluice tests scripts` clean.
9. `/review-pr` run BEFORE pushing (the standing cadence: CodeRabbit is the scarce resource, the local team is free and parallel).

## Out of Scope

- Routing the re-scrape to the survivor (`merged_into` pointer). Declined; the second-order #9 staleness gap it would close is recorded in the spec's residual.
- A url index over the archive — the probe is name-keyed, so a re-scrape whose title drifts past every candidate is missed. That is #23 territory and changes `upsert`'s cost model.
- A config knob. A silent second application is not a preference.
- Pruning `_merged/`. It grows only when a human runs `leads dedupe --merge`, so it is at human scale.
