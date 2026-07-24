# Read-Path Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-gated `sluice leads dedupe` command that reconciles duplicate lead notes (the same role reaching the vault as several notes when company/title strings drift), without ever silently merging two genuinely different roles.

**Architecture:** Two pure functions (`cluster_duplicates` in `core/leads.py`, `resolve_merge_status` in `core/status.py`) plus one Store-Protocol mutation (`merge_cluster`, `Vault` impl), wired by a thin `leads dedupe` CLI. Ingest is unchanged. Clustering is complete-linkage (a blank location never bridges two different cities); the merge keeps the survivor untouched (never-clobber) and archives losers to `_merged/` (reversible). The design doc is `docs/superpowers/specs/2026-07-24-read-path-dedup-design.md`.

**Tech Stack:** Python 3.12–3.14, standard library only (`json`, `hashlib`, `os` — all stdlib), argparse CLI, pytest + faker.

## Global Constraints

- **Stdlib only in `sluice/`** — `json`, `hashlib`, `os` are stdlib; add no runtime dependency.
- **No personal data in `sluice/` or `tests/`** — synthetic fixtures only. Locations use conftest's `LOCATIONS` (`Alfa`/`Bravo`/`Charlie`), never a real place. Titles/companies are faker-derived or `foo`-family, never hardcoded role strings.
- **Never-clobber** — the survivor's status/scores/enrichment/body are never touched by a merge; only `alt_urls`/`first_seen`/`last_seen` change, and only through the CAS path.
- **Never-regress** — a merge never lowers a status; `resolve_merge_status`'s survivor is by construction the note already holding the winning status; a genuine ambiguity is `conflict`, refused.
- **Config-driven** — the one new tunable (`dedupe_title_noise_words`) goes in the root `Config` dataclass **and** `sluice.yaml.example`, defaults `[]` (abstain).
- **Conventional Commits** — `feat(leads): …`, `test(leads): …`, `refactor(core): …`, `docs: …`.
- **Mutation-witness discipline** — before witnessing, run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once; mutate by MOVING/DELETING a branch (never ADDING); confirm the *named new* test reddens by node id and that no pre-existing test is what catches it.
- **Verification commands:** `python -m pytest`; `ruff check sluice tests` (ruff is NOT in `[test]`; `pip install ruff==0.15.21`, the CI pin).

---

## File Structure

- `sluice/core/status.py` — add `resolve_merge_status(statuses)` (pure verdict; Task 1).
- `sluice/core/leads.py` — add `_norm_tokens`, `cluster_duplicates`, `_location_cliques`, `cluster_id`, `pick_survivor` (pure; Tasks 2, 6).
- `sluice/core/protocols.py` — remove `existing_keys` from the `Store` protocol (Task 3); add `merge_cluster` (Task 4).
- `sluice/core/vault.py` — remove `Vault.existing_keys` (Task 3); add `Vault.merge_cluster` (Task 4).
- `sluice/core/config.py` — add `dedupe_title_noise_words` to root `Config` + loader (Task 5).
- `sluice/core/app.py` — add `Sluice.dedupe_report`/`dedupe_merge` + `DedupeCluster` (Task 6).
- `sluice/cli.py` — add the `leads dedupe` parser + `cmd_leads_dedupe` (Task 6).
- `sluice.yaml.example` — commented `dedupe_title_noise_words` line (Task 5).
- `tests/` — `test_status_merge.py`, `test_leads_cluster.py`, `test_vault_merge_cluster.py`, edits to `test_vault.py` / conformance / neutral-defaults, `test_leads_dedupe_cli.py` (per task).
- `docs/ARCHITECTURE.md` — the new command + merge semantics + Store-contract surface (Task 7).

---

## Task 1: `resolve_merge_status` — the N-ary status verdict

**Files:**
- Modify: `sluice/core/status.py`
- Test: `tests/test_status_merge.py` (create)

**Interfaces:**
- Consumes: `normalize`, `APPLICATION_OWNED`, `TRIAGE_OWNED`, `CANONICAL`, `_TERMINAL`, `_LADDER`, `_RANK` (all already in `status.py`).
- Produces: `resolve_merge_status(statuses: Iterable[str]) -> tuple[str | None, str]` — `(winner, outcome)`, `outcome ∈ {"ok","conflict"}`, `winner` is `None` on conflict.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status_merge.py`:

```python
"""resolve_merge_status: the order-independent N-ary status verdict for #23 dedup.

No total order spans the two lifecycles, and clusters are size >= 2, so the verdict
reads the SET of member statuses. Every case is asserted; the 3-member cases are
asserted over ALL permutations, because a single ordering catches only a left-fold
(a right-fold hits a different intermediate state).
"""
import itertools

import pytest

from sluice.core.status import resolve_merge_status


@pytest.mark.parametrize("statuses,winner,outcome", [
    (["shortlist", "new"], "shortlist", "ok"),          # new is the floor
    (["rejected", "shortlist"], "rejected", "ok"),       # app-owned beats triage
    (["applied", "interview"], "interview", "ok"),        # both live -> ladder rank
    (["offer", "offer"], "offer", "ok"),                 # equal
    (["rejected", "interview"], None, "conflict"),        # terminal + live -> conflict
    (["rejected", "accepted"], None, "conflict"),         # two terminals
    (["shortlist", "dismiss"], None, "conflict"),         # two non-new triage
    (["weird", "shortlist"], None, "conflict"),           # non-canonical + different
])
def test_pairwise_both_orders(statuses, winner, outcome):
    assert resolve_merge_status(statuses) == (winner, outcome)
    assert resolve_merge_status(list(reversed(statuses))) == (winner, outcome)


@pytest.mark.parametrize("statuses,winner,outcome", [
    (["new", "new", "rejected"], "rejected", "ok"),
    (["shortlist", "dismiss", "applied"], "applied", "ok"),   # app dominates both triage
    (["applied", "interview", "rejected"], None, "conflict"), # terminal + live present
    (["rejected", "accepted", "new"], None, "conflict"),
])
def test_three_member_all_permutations(statuses, winner, outcome):
    for perm in itertools.permutations(statuses):
        assert resolve_merge_status(list(perm)) == (winner, outcome)


def test_all_equal_noncanonical_is_agreement_not_conflict():
    assert resolve_merge_status(["weird", "weird"]) == ("weird", "ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_status_merge.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_merge_status'`.

- [ ] **Step 3: Write the implementation**

Append to `sluice/core/status.py`:

```python
def resolve_merge_status(statuses):
    """Order-independent verdict over a duplicate cluster's member statuses.
    Returns (winner, outcome), outcome one of "ok"|"conflict", winner the
    surviving status (None on conflict). See docs/.../read-path-dedup-design.md #2.

    There is no total order across the two lifecycles and clusters are size >= 2,
    so this reads the SET of distinct statuses rather than folding pairwise (a fold
    is order-dependent around the conflict sentinel). Application-owned dominates
    triage (you cannot un-apply); a terminal beside a LIVE application is a
    reject-then-reapply -> conflict, refused rather than silently archiving the live
    attempt (#23)."""
    s = {normalize(x) for x in statuses}
    if len(s) == 1:
        return next(iter(s)), "ok"          # all agree (incl. all-non-canonical)
    if s - CANONICAL:
        return None, "conflict"             # an unrankable status disagrees
    app = s & set(APPLICATION_OWNED)
    if app:
        # triage members are dominated; drop them BEFORE any triage-vs-triage judging
        term = app & set(_TERMINAL)
        live = app & set(_LADDER)
        if len(term) >= 2:
            return None, "conflict"          # two different terminals
        if term and live:
            return None, "conflict"          # reject-then-reapply (round-2 inv-r2-001)
        if term:
            return next(iter(term)), "ok"    # the sole terminal (no live) wins
        return max(live, key=_RANK.__getitem__), "ok"   # all live -> highest ladder rank
    nonnew = (s & set(TRIAGE_OWNED)) - {"new"}
    if not nonnew:
        return "new", "ok"                   # only new + itself: new is the floor
    if len(nonnew) == 1:
        return next(iter(nonnew)), "ok"
    return None, "conflict"                  # two different non-new triage states
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_status_merge.py -q`
Expected: PASS (all cases + permutations).

- [ ] **Step 5: Mutation-witness the terminal+live conflict branch**

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
DELETE the line `if term and live: return None, "conflict"` in `status.py`, then:
Run: `python -m pytest "tests/test_status_merge.py::test_pairwise_both_orders[statuses4-None-conflict]" -q`
Expected: FAIL (the `[rejected, interview]` case reddens — proves this branch is load-bearing). Restore the line; re-run → PASS.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/status.py tests/test_status_merge.py
git commit -m "feat(status): resolve_merge_status — N-ary merge verdict for dedup (#23)"
```

---

## Task 2: `cluster_duplicates` — complete-linkage clustering

**Files:**
- Modify: `sluice/core/leads.py`
- Test: `tests/test_leads_cluster.py` (create)

**Interfaces:**
- Consumes: `_norm_location`, `_compare_locations`, `DIFFERENT` (already in `leads.py`); `LeadNote` (`.fm` dict with `company`/`role`/`location`).
- Produces: `_norm_tokens(s: str) -> set[str]`; `cluster_duplicates(notes, *, title_noise=(), location_noise=()) -> list[list[LeadNote]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_leads_cluster.py`:

```python
"""cluster_duplicates: complete-linkage clustering of duplicate lead notes (#23 §1).

Fixtures are synthetic. Titles are constructed from a faker base plus synthetic
tokens (never hardcoded role strings); companies use a faker base + a synthetic
suffix; LOCATIONS are conftest's Alfa/Bravo/Charlie placeholders.
"""
from types import SimpleNamespace

from sluice.core.leads import cluster_duplicates
from tests.conftest import LOCATIONS


def _note(slug, *, company="foo", role="engineer", location=""):
    return SimpleNamespace(slug=slug,
                           fm={"company": company, "role": role, "location": location})


def _slugs(clusters):
    return sorted(sorted(n.slug for n in c) for c in clusters)


def test_drifted_title_clusters_only_via_configured_noise():
    a = _note("a", role="engineer remote")
    b = _note("b", role="engineer")
    assert cluster_duplicates([a, b]) == []                       # no noise -> not clustered
    assert _slugs(cluster_duplicates([a, b], title_noise=["remote"])) == [["a", "b"]]


def test_distinct_seniority_never_clusters():
    a = _note("a", role="senior engineer")
    b = _note("b", role="engineer")
    assert cluster_duplicates([a, b], title_noise=["remote", "hybrid"]) == []


def test_prefix_company_never_clusters():
    a = _note("a", company="foo")
    b = _note("b", company="foo industries")
    assert cluster_duplicates([a, b]) == []


def test_same_role_different_city_never_clusters():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[1])
    assert cluster_duplicates([a, b]) == []


def test_blank_location_clusters_both_orders():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location="")
    assert _slugs(cluster_duplicates([a, b])) == [["a", "b"]]
    assert _slugs(cluster_duplicates([b, a])) == [["a", "b"]]


def test_positive_two_clique():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[0])
    assert _slugs(cluster_duplicates([a, b])) == [["a", "b"]]


def test_blank_bridge_yields_no_cluster():
    # Alfa ~ blank ~ Bravo: connected via blank, but Alfa/Bravo DIFFERENT -> not a
    # clique -> no cluster (never bridges two different cities). arc-r2-001.
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location="")
    c = _note("c", location=LOCATIONS[1])
    assert cluster_duplicates([a, b, c]) == []


def test_two_disjoint_cliques_in_one_group():
    a = _note("a", location=LOCATIONS[0])
    a2 = _note("a2", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[1])
    b2 = _note("b2", location=LOCATIONS[1])
    assert _slugs(cluster_duplicates([a, a2, b, b2])) == [["a", "a2"], ["b", "b2"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_leads_cluster.py -q`
Expected: FAIL — `ImportError: cannot import name 'cluster_duplicates'`.

- [ ] **Step 3: Write the implementation**

Append to `sluice/core/leads.py`:

```python
def _norm_tokens(s: str) -> set:
    """Token SET of a string under the exact fold `_norm_location` implements
    (NFKD, casefold, drop combining marks, unicode-aware \\W split). Shared by
    title and company clustering so both reuse the one pinned normalization."""
    return set(_norm_location(s).split())


def _location_cliques(members, location_noise):
    """Partition members (already same company+role) into complete-linkage location
    cliques. A cluster is a CONNECTED COMPONENT of the compatibility graph
    (_compare_locations != DIFFERENT) that is itself a CLIQUE. A component a chain
    of UNKNOWN (blank) edges makes span a DIFFERENT pair is not a clique -> no
    cluster (its members stay singletons), so a blank location never bridges two
    different cities (#5, #23 arc-r2-001). Deterministic in member order."""
    n = len(members)
    compat = [[True] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            ok = _compare_locations(members[i].fm.get("location", ""),
                                    members[j].fm.get("location", ""),
                                    location_noise) != DIFFERENT
            compat[i][j] = compat[j][i] = ok
    seen, out = set(), []
    for start in range(n):
        if start in seen:
            continue
        comp, stack = [], [start]
        while stack:                       # DFS the compatibility component
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            comp.append(k)
            stack.extend(m for m in range(n) if m not in seen and compat[k][m])
        if len(comp) >= 2 and all(compat[a][b] for a in comp for b in comp if a != b):
            out.append([members[k] for k in sorted(comp)])
    return out


def cluster_duplicates(notes, *, title_noise=(), location_noise=()):
    """Group lead notes into suspected-duplicate clusters (size >= 2), for the
    human-gated `sluice leads dedupe`. Two notes cluster iff same firm
    (`_norm_tokens(company)` equal), same role (`_norm_tokens(role)` minus the
    configured title-noise tokens equal), and a complete-linkage location clique
    (`_location_cliques`). PROPOSES only; merging is human-gated, so recall-leaning
    is acceptable. See docs/.../read-path-dedup-design.md #1."""
    tnoise = {t for w in title_noise for t in _norm_tokens(w)}
    groups: dict = {}
    for note in notes:
        company = frozenset(_norm_tokens(note.fm.get("company", "")))
        role = frozenset(_norm_tokens(note.fm.get("role", "")) - tnoise)
        groups.setdefault((company, role), []).append(note)
    clusters = []
    for members in groups.values():
        if len(members) >= 2:
            clusters.extend(_location_cliques(members, location_noise))
    return clusters
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_leads_cluster.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation-witness the clique guard**

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
In `_location_cliques`, DELETE the `and all(compat[a][b] ...)` conjunct from the `if` (leaving `if len(comp) >= 2:`), then:
Run: `python -m pytest tests/test_leads_cluster.py::test_blank_bridge_yields_no_cluster -q`
Expected: FAIL (the blank-bridged trio now wrongly clusters — proves the clique guard closes the #5 bridge). Restore; re-run → PASS.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/leads.py tests/test_leads_cluster.py
git commit -m "feat(leads): cluster_duplicates — complete-linkage dedup clustering (#23)"
```

---

## Task 3: Remove the dead `existing_keys` from the Store contract

**Files:**
- Modify: `sluice/core/protocols.py` (remove the `existing_keys` stub), `sluice/core/vault.py` (remove `Vault.existing_keys`), `tests/test_vault.py:104-112` (remove its two unit tests), `tests/conformance/test_store_contract.py:325` (remove the assertion).

**Interfaces:**
- Consumes: nothing.
- Produces: a `Store` protocol with no `existing_keys`; no caller anywhere (verified: only these four sites reference it).

- [ ] **Step 1: Confirm there is no production caller**

Run: `grep -rn "existing_keys" sluice/ tests/`
Expected: exactly the protocol stub, the `Vault` method, the two `test_vault.py` tests, and the conformance assertion at `test_store_contract.py:325`. No call site in `sluice/ingest/`, `sluice/cli.py`, or `sluice/core/app.py`.

- [ ] **Step 2: Remove the two `test_vault.py` unit tests**

Delete `test_existing_keys_returns_dedup_keys` and `test_existing_keys_empty_when_no_vault` (`tests/test_vault.py:104-112`).

- [ ] **Step 3: Remove the conformance assertion**

In `tests/conformance/test_store_contract.py`, `test_reading_an_empty_store_is_not_an_error` currently reads:

```python
def test_reading_an_empty_store_is_not_an_error(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.read_leads() == []
    assert store.existing_keys() == set()
```

Delete the last line so the body ends at `assert store.read_leads() == []`.

- [ ] **Step 4: Run the tests to verify the suite still passes without those tests**

Run: `python -m pytest tests/test_vault.py tests/conformance/test_store_contract.py -q`
Expected: PASS (the deleted tests are gone; nothing else referenced them).

- [ ] **Step 5: Remove the `Vault.existing_keys` method**

Delete the whole `def existing_keys(self) -> set[str]:` method (the `# ── dedup ──` block) from `sluice/core/vault.py`.

- [ ] **Step 6: Remove the protocol stub**

In `sluice/core/protocols.py`, delete the `def existing_keys(self) -> set: ...` line from the `Store` protocol.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. (If any `existing_keys` reference remains, an `AttributeError`/`NameError` surfaces here.)

- [ ] **Step 8: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py tests/test_vault.py tests/conformance/test_store_contract.py
git commit -m "refactor(core): remove dead Store.existing_keys (#23)"
```

---

## Task 4: `merge_cluster` — the Store-Protocol merge mutation

**Files:**
- Modify: `sluice/core/vault.py` (add `Vault.merge_cluster`), `sluice/core/protocols.py` (add `merge_cluster` to the `Store` protocol), `tests/conformance/test_store_contract.py` (conformance test).
- Test: `tests/test_vault_merge_cluster.py` (create — vault-specific behaviour).

**Interfaces:**
- Consumes: `_cas_write`, `_split_frontmatter`, `_fm_value`, `_set_fm`, `_log` (in `vault.py`); `VaultConflict` (in `protocols.py`); `json`, `os` (stdlib).
- Produces: `Vault.merge_cluster(survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen) -> list[str]` (archived loser paths).

- [ ] **Step 1: Write the failing vault test**

Create `tests/test_vault_merge_cluster.py`:

```python
"""Vault.merge_cluster: union the audit trail onto a SEEDED survivor without clobbering
its state, archive losers reversibly. The survivor is seeded (not empty) — empty-survives-
empty certifies nothing (#23 tst-001)."""
import json

import pytest

from sluice.core.vault import Vault
from sluice.core.protocols import VaultConflict
from tests.conftest import LOCATIONS, racing_read


def _mk(tmp_path):
    # Two DISTINCT notes for merge_cluster to act on. They must NOT share
    # company+title+compatible-location: upsert would MERGE those into one note
    # (same_opportunity UNKNOWN/SAME). A token-disjoint LOCATIONS[1] makes
    # _compare_locations return DIFFERENT, so upsert creates a genuine second note.
    # (merge_cluster itself ignores location, so this doesn't weaken any assertion.)
    v = Vault(str(tmp_path))
    from sluice.core.leads import Lead
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[0], url="https://ex.invalid/1",
                  first_seen="2026-07-10", last_seen="2026-07-10"))
    v.upsert(Lead(source="b", search="s", title="Analyst", company="Foo",
                  location=LOCATIONS[1], url="https://ex.invalid/2",
                  first_seen="2026-07-05", last_seen="2026-07-20"))
    return v


def _by_url(v, u):
    return next(n for n in v.read_leads() if n.fm.get("url") == u)


def test_survivor_state_survives_only_audit_trail_changes(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    v.update_fields(survivor.ref, {"status": "applied", "score": "9",
                                   "tailored_cv": "CV_ab12.pdf", "applied_date": "2026-07-11"})
    survivor = _by_url(v, "https://ex.invalid/1")
    before = dict(survivor.fm)
    loser = _by_url(v, "https://ex.invalid/2")
    archived = v.merge_cluster(survivor.ref, [loser.ref],
                               alt_urls=["https://ex.invalid/2"],
                               first_seen="2026-07-05", last_seen="2026-07-20")
    after = _by_url(v, "https://ex.invalid/1")
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed
    assert json.loads(after.fm["alt_urls"]) == ["https://ex.invalid/2"]
    assert after.fm["first_seen"] == "2026-07-05"      # min
    assert after.fm["last_seen"] == "2026-07-20"       # max
    assert len(v.read_leads()) == 1                    # loser archived out of the active view
    assert archived and archived[0].endswith(".md")


def test_timestamps_never_moved_the_wrong_way(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")   # first_seen 2026-07-10, last_seen 2026-07-10
    loser = _by_url(v, "https://ex.invalid/2")
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[],
                    first_seen="2026-07-30", last_seen="2026-07-01")  # stale params, wrong direction
    after = _by_url(v, "https://ex.invalid/1")
    assert after.fm["first_seen"] == "2026-07-10"   # not raised
    assert after.fm["last_seen"] == "2026-07-10"    # not lowered


def test_alt_urls_round_trips_a_comma_bearing_url(tmp_path):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    url = "https://ex.invalid/j?a=1,2&b=3"
    v.merge_cluster(survivor.ref, [loser.ref], alt_urls=[url],
                    first_seen="2026-07-05", last_seen="2026-07-20")
    after = _by_url(v, "https://ex.invalid/1")
    assert json.loads(after.fm["alt_urls"]) == [url]


def test_survivor_conflict_archives_zero_losers(tmp_path, monkeypatch):
    v = _mk(tmp_path)
    survivor = _by_url(v, "https://ex.invalid/1")
    loser = _by_url(v, "https://ex.invalid/2")
    racing_read(monkeypatch, survivor.ref,
                lambda: open(survivor.ref, "a").write("\n"), once=False)  # sustained race
    with pytest.raises(VaultConflict):
        v.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://ex.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    assert len(v.read_leads()) == 2      # loser NOT archived — conflict aborted before any archive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vault_merge_cluster.py -q`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute 'merge_cluster'`.

- [ ] **Step 3: Write the implementation**

Add to the `Vault` class in `sluice/core/vault.py` (import `json` at the top of the module if not present — it is not; add `import json` beside `import hashlib`):

```python
    def merge_cluster(self, survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen):
        """Merge a human-vetted duplicate cluster (#23). Union the audit trail onto
        the survivor -- never touching its status/scores/enrichment/body
        (never-clobber) -- and archive each loser to `_merged/` (reversible,
        invisible to read_leads). Timestamps are RE-DERIVED against the fresh
        survivor inside the CAS transform, so a caller's stale min/max can never
        regress them. The survivor write happens FIRST; losers are archived only on
        its success, so a VaultConflict archives nothing. A per-loser archive OSError
        is logged and skipped (isolated), so that loser stays in the active view and
        the next run re-merges it -- never counted as merged. Returns the archived
        loser paths. See docs/.../read-path-dedup-design.md #3."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            existing = _fm_value(inner, "alt_urls")
            current = []
            if existing:
                try:
                    current = json.loads(existing)
                except ValueError:
                    current = []
            merged = list(dict.fromkeys([*current, *alt_urls]))   # order-stable union
            inner = _set_fm(inner, "alt_urls", json.dumps(merged))
            fresh_first = _fm_value(inner, "first_seen")
            if first_seen and (not fresh_first or first_seen < fresh_first):
                inner = _set_fm(inner, "first_seen", first_seen)
            fresh_last = _fm_value(inner, "last_seen")
            if last_seen and (not fresh_last or last_seen > fresh_last):
                inner = _set_fm(inner, "last_seen", last_seen)   # monotonic: only advance
            return f"---\n{inner}\n---\n{body}"
        _cas_write(survivor_ref, transform)   # raises VaultConflict BEFORE any archive
        merged_dir = os.path.join(self.leads_dir, "_merged")
        os.makedirs(merged_dir, exist_ok=True)
        archived = []
        for ref in loser_refs:
            base = os.path.basename(ref)
            stem = base[:-3] if base.endswith(".md") else base
            dest = os.path.join(merged_dir, base)
            n = 1
            while os.path.exists(dest):     # collision-safe numeric suffix
                dest = os.path.join(merged_dir, f"{stem}.{n}.md")
                n += 1
            try:
                os.replace(ref, dest)
                archived.append(dest)
            except OSError as e:
                # Per-loser isolation: a failed archive leaves that loser active,
                # so it is not counted as merged and the next run re-merges it.
                _log.warning("dedupe: could not archive loser %s: %s", ref, e)
        return archived
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_vault_merge_cluster.py -q`
Expected: PASS (all four).

- [ ] **Step 5: Add `merge_cluster` to the Store protocol + a conformance test**

In `sluice/core/protocols.py`, add to the `Store` protocol (near `update_fields`):

```python
    def merge_cluster(self, survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen) -> list: ...
```

In `tests/conformance/test_store_contract.py`, add (after the never-clobber tests, reusing `_enrich`):

```python
def test_merge_cluster_preserves_survivor_and_removes_losers(store_name, tmp_path, monkeypatch):
    """merge_cluster unions the audit trail onto a SEEDED survivor without touching its
    state, and reversibly removes the losers from the active set. Store-agnostic (#23)."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[0],
                              first_seen="2026-07-10", last_seen="2026-07-10")) == "created"
    assert store.upsert(_lead(url="https://example.invalid/2", location="",
                              first_seen="2026-07-05", last_seen="2026-07-20")) == "created"
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    _enrich(store, survivor.ref)
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    before = dict(survivor.fm)
    loser = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/2")
    store.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://example.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    after = next(iter(store.read_leads()))
    assert len(store.read_leads()) == 1                                  # loser removed
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed   # never-clobber
```

- [ ] **Step 6: Run the conformance suite**

Run: `python -m pytest tests/conformance/test_store_contract.py -q`
Expected: PASS (the new test runs against `vault`).

- [ ] **Step 7: Mutation-witness the survivor-first ordering**

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
In `merge_cluster`, MOVE the `_cas_write(survivor_ref, transform)` line to AFTER the archive loop, then:
Run: `python -m pytest tests/test_vault_merge_cluster.py::test_survivor_conflict_archives_zero_losers -q`
Expected: FAIL (losers get archived before the conflict — proves survivor-first is load-bearing). Restore; re-run → PASS.

- [ ] **Step 8: Commit**

```bash
git add sluice/core/vault.py sluice/core/protocols.py tests/test_vault_merge_cluster.py tests/conformance/test_store_contract.py
git commit -m "feat(vault): merge_cluster — Store-contract dedup merge (#23)"
```

---

## Task 5: `dedupe_title_noise_words` config knob

**Files:**
- Modify: `sluice/core/config.py` (Config field + loader), `sluice.yaml.example` (commented line), `tests/test_sluice_neutral_defaults.py` (traveling assertion).

**Interfaces:**
- Consumes: `_str_list` (already in `config.py`).
- Produces: `Config.dedupe_title_noise_words: list` (default `[]`), loaded from the YAML file.

- [ ] **Step 1: Write the failing test**

In `tests/test_sluice_neutral_defaults.py`, inside `test_ingest_defaults_carry_no_preference` (the root-`Config` test), add the traveling assertion beside the other root-config assertions:

```python
    assert c.dedupe_title_noise_words == []   # #23: strictest clustering, abstain toward not-merging
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py::test_ingest_defaults_carry_no_preference -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'dedupe_title_noise_words'`.

- [ ] **Step 3: Add the field + loader**

In `sluice/core/config.py`, add to the `Config` dataclass (beside `location_noise_words`):

```python
    # Title-noise tokens stripped before #23's dedup clustering compares two roles. Empty by
    # default -> strictest clustering (nothing stripped), erring toward NOT merging (safe).
    dedupe_title_noise_words: list = field(default_factory=list)
```

In `load_config`, extend the `return Config(...)` call with:

```python
                  dedupe_title_noise_words=_str_list(data.get("dedupe_title_noise_words"),
                                                     "dedupe_title_noise_words"))
```

(Move the closing `)` — the previous last argument `location_noise_words=...` gets a trailing comma.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -q`
Expected: PASS (the traveling assertion + the auto-sweep both green).

- [ ] **Step 5: Add the commented example line**

In `sluice.yaml.example`, below the commented `location_noise_words:` block, add:

```yaml
# Tokens stripped from a job TITLE before `sluice leads dedupe` decides two roles are the
# same posting. Empty by default (strictest: only near-identical titles cluster). Add board
# decorations you observe. Generic examples only — never your real filter list.
# dedupe_title_noise_words:
#   - remote
#   - hybrid
#   - contract
```

- [ ] **Step 6: Verify the loader rejects a YAML scalar**

Run: `python -c "from sluice.core.config import _str_list; _str_list('remote','dedupe_title_noise_words')"`
Expected: `ValueError: dedupe_title_noise_words must be a list of strings, got 'remote'` (the `_str_list` guard already covers the new key).

- [ ] **Step 7: Commit**

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_sluice_neutral_defaults.py
git commit -m "feat(config): dedupe_title_noise_words clustering knob (#23)"
```

---

## Task 6: `sluice leads dedupe` — CLI + orchestration

**Files:**
- Modify: `sluice/core/leads.py` (`cluster_id`, `pick_survivor`), `sluice/core/app.py` (`DedupeCluster`, `Sluice.dedupe_report`/`dedupe_merge`), `sluice/cli.py` (`cmd_leads_dedupe` + parser).
- Test: `tests/test_leads_dedupe_cli.py` (create — orchestration + e2e + idempotence).

**Interfaces:**
- Consumes: `cluster_duplicates`, `resolve_merge_status`, `merge_cluster`, `is_application_owned`, `VaultConflict`.
- Produces: `cluster_id(members) -> str`; `pick_survivor(members, winner_status) -> LeadNote`; `Sluice.dedupe_report() -> list[DedupeCluster]`; `Sluice.dedupe_merge(ids) -> list[tuple[str,str]]`.

- [ ] **Step 1: Write the failing orchestration test**

Create `tests/test_leads_dedupe_cli.py`:

```python
"""sluice leads dedupe orchestration: report, targeted merge, conflict/stale refusal,
never-regress, idempotence. Synthetic; locations are Alfa/Bravo placeholders."""
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _app(tmp_path, monkeypatch, **cfg):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return Sluice(Config(**cfg))


# A clusterable duplicate pair CANNOT be two notes with the same company+title+compatible
# location: upsert MERGES those into one note (same_opportunity UNKNOWN/SAME -> merge). The
# #23 duplicate arises from STRING DRIFT -- two notes whose filenames differ (so upsert
# creates both) but whose normalized tokens match under clustering. So the survivor and its
# drifted re-post differ by a configured title-noise token ("Analyst" vs "Analyst remote",
# with dedupe_title_noise_words=["remote"]); both sit at the SAME location so they cluster
# (a DIFFERENT location would make cluster_duplicates split them -- the opposite failure).
def _seed(v, url, *, title="Analyst", location=LOCATIONS[0], status=None,
          last_seen="2026-07-10", **fm):
    v.upsert(Lead(source="b", search="s", title=title, company="Foo",
                  location=location, url=url, first_seen="2026-07-10", last_seen=last_seen))
    note = next(n for n in v.read_leads() if n.fm.get("url") == url)
    fields = dict(fm)
    if status:
        fields["status"] = status
    if fields:
        v.update_fields(note.ref, fields)
    return note


def _seed_pair(v, *, s1=None, s2=None, ls1="2026-07-10", ls2="2026-07-10"):
    """Two notes for one drifted opportunity: base title vs the same title + a noise token,
    same firm+location -> two distinct notes at upsert that cluster under noise=['remote']."""
    _seed(v, "https://ex.invalid/1", title="Analyst", status=s1, last_seen=ls1)
    _seed(v, "https://ex.invalid/2", title="Analyst remote", status=s2, last_seen=ls2)


def test_report_clusters_drifted_duplicate(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    report = app.dedupe_report()
    assert len(report) == 1 and not report[0].conflict
    assert {n.fm["url"] for n in report[0].members} == {"https://ex.invalid/1", "https://ex.invalid/2"}
    assert report[0].flagged_losers == []   # both `new` (triage) -> no loser flag


def test_merge_then_idempotent(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    cid = app.dedupe_report()[0].id
    assert app.dedupe_merge([cid]) == [(cid, "merged")]
    assert len(v.read_leads()) == 1                 # one survivor
    assert app.dedupe_report() == []                # idempotent: nothing left to cluster


def test_never_regress_survivor_stays_rejected(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="rejected", ls1="2026-07-05")   # /2 is a fresh `new` re-post
    cid = app.dedupe_report()[0].id
    assert app.dedupe_merge([cid]) == [(cid, "merged")]
    survivors = v.read_leads()
    assert len(survivors) == 1 and survivors[0].status == "rejected"   # not resurrected


def test_conflict_cluster_is_refused(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="rejected", s2="interview")     # terminal + live -> conflict
    c = app.dedupe_report()[0]
    assert c.conflict
    assert app.dedupe_merge([c.id]) == [(c.id, "conflict")]
    assert len(v.read_leads()) == 2                  # nothing merged


def test_stale_id_is_refused(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    assert app.dedupe_merge(["deadbeef"]) == [("deadbeef", "stale")]


def test_loser_flag_fires_on_app_owned_loser(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="interview", s2="applied", ls1="2026-07-20")
    c = app.dedupe_report()[0]
    assert not c.conflict and c.survivor.fm["url"] == "https://ex.invalid/1"   # interview > applied
    assert [n.fm["url"] for n in c.flagged_losers] == ["https://ex.invalid/2"]  # app-owned loser flagged
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_leads_dedupe_cli.py -q`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'dedupe_report'`.

- [ ] **Step 3: Add the pure helpers to `leads.py`**

Append to `sluice/core/leads.py`:

```python
def cluster_id(members) -> str:
    """Stable short id for a cluster: a hash of its sorted member slugs. Same
    membership -> same id (a report id matches what `--merge` recomputes); any
    membership change -> a new id, so a stale `--merge` id is refused (#23 §4)."""
    key = "\n".join(sorted(n.slug for n in members))
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def pick_survivor(members, winner_status):
    """Among the members holding `winner_status`, the survivor note: highest
    `last_seen`, then slug (deterministic tie-break). See #23 §2."""
    holders = [n for n in members if n.status == winner_status]
    return max(holders, key=lambda n: (n.fm.get("last_seen", ""), n.slug))
```

- [ ] **Step 4: Add the orchestration to `app.py`**

In `sluice/core/app.py`, add a `DedupeCluster` dataclass near the top (beside the other dataclasses) and two methods on `Sluice`:

```python
@dataclass
class DedupeCluster:
    id: str
    members: list          # list[LeadNote]
    survivor: object       # LeadNote, or None on conflict
    conflict: bool
    flagged_losers: list   # losers carrying a CV/sign-off hold or an application-owned status
```

```python
    def _dedupe_report(self, store):
        from sluice.core.leads import cluster_duplicates, cluster_id, pick_survivor
        from sluice.core.status import resolve_merge_status, is_application_owned
        clusters = cluster_duplicates(
            store.read_leads(),
            title_noise=getattr(self.config, "dedupe_title_noise_words", []),
            location_noise=getattr(self.config, "location_noise_words", []))
        out = []
        for members in clusters:
            winner, outcome = resolve_merge_status([n.status for n in members])
            survivor = pick_survivor(members, winner) if outcome == "ok" else None
            flagged = [n for n in members if n is not survivor and (
                n.fm.get("tailored_cv") or n.fm.get("needs_signoff")
                or n.fm.get("pending_cv") or is_application_owned(n.status))]
            out.append(DedupeCluster(id=cluster_id(members), members=members,
                                     survivor=survivor, conflict=(outcome != "ok"),
                                     flagged_losers=flagged))
        return out

    def dedupe_report(self):
        """The #23 read-path dedup REPORT: suspected-duplicate clusters, each with a
        stable id, computed survivor, conflict flag, and flagged losers. Changes
        nothing."""
        return self._dedupe_report(self.store())

    def dedupe_merge(self, ids):
        """Merge the human-vetted clusters named by `ids`. Recomputes the report
        fresh and matches by id: a stale id (membership changed) -> 'stale'; a
        conflict cluster -> 'conflict' (refused); a sustained write race ->
        'conflict-race'. Returns [(id, outcome)]. Nothing merges without an id."""
        from sluice.core.protocols import VaultConflict
        store = self.store()
        by_id = {c.id: c for c in self._dedupe_report(store)}
        results = []
        for cid in ids:
            c = by_id.get(cid)
            if c is None:
                results.append((cid, "stale"))
                continue
            if c.conflict:
                results.append((cid, "conflict"))
                continue
            losers = [n for n in c.members if n is not c.survivor]
            try:
                # first_seen aggregates only PRESENT values -- a missing one is not "" (which
                # sorts before every real date and would defeat the minimisation). merge_cluster
                # returns the archived loser paths: fewer than requested -> a partial archive,
                # reported distinctly rather than as a full "merged".
                seens = [n.fm.get("first_seen") for n in c.members if n.fm.get("first_seen")]
                archived = store.merge_cluster(
                    c.survivor.ref, [n.ref for n in losers],
                    alt_urls=[n.fm["url"] for n in losers if n.fm.get("url")],
                    first_seen=min(seens) if seens else "",
                    last_seen=max(n.fm.get("last_seen", "") for n in c.members))
                results.append((cid, "merged" if len(archived) == len(losers) else "partial"))
            except MalformedNoteField:
                results.append((cid, "malformed"))   # a human-mangled alt_urls; nothing written
            except VaultConflict:
                results.append((cid, "conflict-race"))
        return results
```
(`MalformedNoteField` is imported from `sluice.core.protocols`, beside `VaultConflict`.)

- [ ] **Step 5: Run the orchestration tests**

Run: `python -m pytest tests/test_leads_dedupe_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Add the CLI command + parser**

In `sluice/cli.py`, add the command function (beside `cmd_triage_normalize`, lazy-importing `Sluice`):

```python
def cmd_leads_dedupe(args, config) -> int:
    from sluice.core.app import Sluice
    app = Sluice(config)
    if args.merge:
        for cid, outcome in app.dedupe_merge(args.merge):
            print(f"dedupe: {cid} {outcome}", file=sys.stderr)
        return 0
    report = app.dedupe_report()
    if args.json:
        print(json.dumps([{
            "id": c.id, "conflict": c.conflict,
            "survivor": (c.survivor.slug if c.survivor else None),
            "members": [{"slug": n.slug, "status": n.status, "url": n.fm.get("url", "")}
                        for n in c.members],
            "flagged_losers": [n.slug for n in c.flagged_losers],
        } for c in report]))
    else:
        for c in report:
            tag = " CONFLICT" if c.conflict else ""
            flag = " ⚑losers" if c.flagged_losers else ""
            print(f"[{c.id}]{tag}{flag} survivor={c.survivor.slug if c.survivor else '-'}")
            for n in c.members:
                print(f"    {n.status:12} {n.slug}  {n.fm.get('url','')}")
        if not report:
            print("dedupe: no duplicate clusters", file=sys.stderr)
    return 0
```

In `_build_parser` (beside the `triage`/`track` groups), add:

```python
    leads = top.add_parser("leads", help="lead maintenance").add_subparsers(
        dest="cmd", required=True)
    dd = leads.add_parser("dedupe", help="find/merge duplicate lead notes")
    dd.add_argument("--merge", nargs="+", metavar="ID",
                    help="merge the named vetted clusters (from a prior report)")
    dd.add_argument("--json", action="store_true", help="machine-readable report")
    dd.set_defaults(func=cmd_leads_dedupe)
```

- [ ] **Step 7: Add a CLI smoke test**

Append to `tests/test_leads_dedupe_cli.py`:

```python
def test_cli_report_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    from sluice.cli import _build_parser, cmd_leads_dedupe
    from sluice.core.config import Config
    v = Vault(str(tmp_path))
    _seed_pair(v)
    args = _build_parser().parse_args(["leads", "dedupe"])
    assert cmd_leads_dedupe(args, Config(dedupe_title_noise_words=["remote"])) == 0
    assert "survivor=" in capsys.readouterr().out
```

- [ ] **Step 8: Run the full CLI test + the whole suite**

Run: `python -m pytest tests/test_leads_dedupe_cli.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 9: Mutation-witness the stale-id refusal**

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
In `dedupe_merge`, DELETE the `if c is None: results.append((cid,"stale")); continue` guard, then:
Run: `python -m pytest tests/test_leads_dedupe_cli.py::test_stale_id_is_refused -q`
Expected: FAIL (a stale id now crashes/mis-handles instead of refusing). Restore; re-run → PASS.

- [ ] **Step 10: Commit**

```bash
git add sluice/core/leads.py sluice/core/app.py sluice/cli.py tests/test_leads_dedupe_cli.py
git commit -m "feat(leads): sluice leads dedupe — human-gated read-path dedup (#23)"
```

---

## Task 7: Docs + surface the human-gated `.rulesync/` edit

**Files:**
- Modify: `docs/ARCHITECTURE.md`.
- Surface (do NOT auto-edit): `.rulesync/subagents/sluice-invariant-reviewer.md:90`.

**Interfaces:** none (documentation).

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

Add a short subsection (near the store/never-clobber material) documenting: the `sluice leads dedupe` command; role-level, human-gated, archive-not-delete merge semantics; that a loser's downstream state (scores/notes/CV/sign-off) is intentionally dropped from the active view and recovered only by un-archiving `_merged/`; and the Store-contract surface change (`merge_cluster` in, `existing_keys` out). Do not describe any change to the ingest/never-clobber/never-regress contracts — the merge upholds them.

- [ ] **Step 2: Verify no stale `existing_keys` reference remains in shipped docs**

Run: `grep -rn "existing_keys" docs/`
Expected: no hits (or, if any, update them in this step).

- [ ] **Step 3: Surface the canonical `.rulesync/` edit to the user (do not auto-apply)**

`.rulesync/subagents/sluice-invariant-reviewer.md:90` names `Vault.existing_keys` as the dedup mechanism (and is stale — ingest uses `seen.load()`). `.rulesync/` is canonical and human-gated. **Stop and ask the user** to approve editing that line (e.g. to "Dedup uses the ingest `seen.db` cache (`seen.load()`); the vault is reconciled by `sluice leads dedupe`."), then regenerate with `npx rulesync@9.6.3 generate -t '*' -f '*'`. Do not edit `.rulesync/` without that approval.

- [ ] **Step 4: Commit the docs (rulesync edit handled separately, per user approval)**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: sluice leads dedupe + Store-contract surface (#23)"
```

---

## Final verification (run before opening the PR)

- [ ] `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- [ ] `python -m pytest -q` → all green
- [ ] `ruff check sluice tests` → clean (`pip install ruff==0.15.21` first if needed)
- [ ] Per the standing rule: run `/review-pr` **before** pushing the branch (CodeRabbit is the scarce resource; the specialist team is free and parallel).
