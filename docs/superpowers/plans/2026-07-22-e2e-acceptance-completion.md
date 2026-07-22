# Complete the e2e acceptance suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tests/e2e/` a comprehensive acceptance suite — one user-named end-to-end scenario per load-bearing product promise, over the existing `tests/harness/`.

**Architecture:** Each scenario drives the real `Sluice` composition root over the shared harness (fakes only at the true I/O boundaries: browser client, renderer, backend, Google client; real `Vault` on `tmp_path`). These are acceptance tests of *existing, correct* behaviour, so the cycle is **write → verify it PASSES → mutation-witness (RED named test, then isolate) → restore → commit**, NOT write-failing-first. No production code changes.

**Tech Stack:** Python 3.12–3.14, stdlib + `pytest` + `faker`, the `tests/harness/` factory (`build_harness`, `ScriptedBackend`, `FakeGoogleClient`, recording renderer).

## Global Constraints

- **No production change.** This PR touches only `tests/` and the design/plan docs (this plan, the 2026-07-22 design spec, and the 2026-07-20 arc-doc §PR 3 pointer). `sluice.yaml.example` untouched (no new tunable).
- **Mutation-witness discipline (per CLAUDE.md + `domain_test_layers.md`):** before mutating, run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`. **Mutate by MOVING or DELETING, never ADDING.** Run every witness **twice**: (a) the named new test goes RED; (b) the suite with that test file deselected isolates whether a pre-existing test also catches it (honest integration-vs-unique framing). Restore the source **byte-identically** and re-run the full suite green.
- **Neutrality:** every fixture uses `build_harness` conventions — `Example …` companies, `example.invalid` domains/emails, the `Remote` work-arrangement token, `conftest.LOCATIONS` (`Alfa`/`Bravo`/`Charlie`) for locations, synthetic round pay floors. No real employer/role/location/path/contact/salary. No title literals (S1 attributes via the location gate on purpose).
- **Suite must stay offline / no-browser / no-network.** The harness fakes every I/O boundary.
- **Commits:** Conventional Commits, `test(e2e): …`. End every commit message with the `MrReasonable <4990954+MrReasonable@users.noreply.github.com>` trailer.
- **Verification commands:** `python -m pytest` (full suite, ~1.4s), `ruff check sluice tests` (ruff not in `[test]`; `pip install ruff==0.15.21`). CI runs the full suite on Python 3.12/3.13/3.14 automatically (`testpaths=["tests"]`), so the DoD gate is green-on-matrix.
- **Spec:** `docs/superpowers/specs/2026-07-22-e2e-acceptance-completion-design.md` (converged after three `/review-plan` rounds). Every scenario, precondition and witness below is grounded there.

---

## File Structure

- `tests/e2e/test_a_clean_lead_reaches_rejected.py` — renamed from `test_full_pipeline.py`; **+ S5** filesystem assertion.
- `tests/e2e/test_a_rescrape_keeps_my_edits.py` — renamed from `test_rescrape.py` (content unchanged).
- `tests/e2e/test_triage_leaves_my_application.py` — renamed from `test_triage_never_regress.py` (content unchanged).
- `tests/e2e/test_an_empty_config_bins_nothing.py` — **new** (S1).
- `tests/e2e/test_a_rejection_clears_my_backlog.py` — **new** (S2).
- `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py` — **new** (S3).
- `tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py` — **new** (S4).

No harness change: S1–S5 are all expressible against the current `build_harness` / `ScriptedBackend`.

---

### Task 1: Rename the three pipeline tests to user-promise names

**Files:**
- Rename: `tests/e2e/test_full_pipeline.py` → `tests/e2e/test_a_clean_lead_reaches_rejected.py`
- Rename: `tests/e2e/test_rescrape.py` → `tests/e2e/test_a_rescrape_keeps_my_edits.py`
- Rename: `tests/e2e/test_triage_never_regress.py` → `tests/e2e/test_triage_leaves_my_application.py`

**Interfaces:** Produces the renamed files consumed by no other task (S5 edits the first one in Task 6).

- [ ] **Step 1: `git mv` the three files**

```bash
cd ~/projects/sluice
git mv tests/e2e/test_full_pipeline.py       tests/e2e/test_a_clean_lead_reaches_rejected.py
git mv tests/e2e/test_rescrape.py            tests/e2e/test_a_rescrape_keeps_my_edits.py
git mv tests/e2e/test_triage_never_regress.py tests/e2e/test_triage_leaves_my_application.py
```

- [ ] **Step 2: Rename the test functions to match (behaviour-neutral)**

In `test_a_clean_lead_reaches_rejected.py`: `def test_full_pipeline_walk(` → `def test_a_clean_lead_reaches_rejected(`.
In `test_a_rescrape_keeps_my_edits.py`: `def test_rescrape_touches_only_last_seen(` → `def test_a_rescrape_keeps_my_edits(`.
In `test_triage_leaves_my_application.py`: `def test_triage_leaves_an_application_owned_lead_untouched(` → `def test_triage_leaves_my_application(`.

No other line changes. Do NOT touch assertions or the module docstrings' meaning.

- [ ] **Step 3: Run the suite to confirm the renames collect and pass**

Run: `python -m pytest tests/e2e -q`
Expected: PASS (same test count as before, new names).

- [ ] **Step 4: Run the full suite + ruff**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: 741 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
test(e2e): rename the pipeline tests to user-promise names

Three git mv's plus the matching test-function renames so the e2e suite reads
as a user-promise acceptance catalog. Behaviour-neutral: no assertion changed.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 2: S1 — an empty config bins nothing

**Files:**
- Create: `tests/e2e/test_an_empty_config_bins_nothing.py`

**Interfaces:**
- Consumes: `build_harness(tmp_path, monkeypatch, *, board_url, rows, target_locations=(), accept_titles=(), reject_titles=(), perm_floor_gbp=0, contract_floor_gbp_day=0) -> Harness`; `Harness.sluice(backend) -> Sluice`; `Harness.vault`; `ScriptedBackend(*, default_verdict="shortlist")`; `sluice.ingest.sources.get("remoteok")`; `Sluice.ingest([src])`; `Sluice.triage(statuses=("new",))`.
- Produces: nothing consumed downstream.

**Design (spec §S1):** the empty location gate must ABSTAIN. Two arms, each on its OWN tmp subdir (reusing one collides: `seen.db` skips the re-scrape and the note is no longer `new`; `build_harness` writes the config before it seeds the vault, so a shared uncreated subdir would `FileNotFoundError`). The lead carries `location="Remote"` because the `remoteok` source applies `extra={"location":"Remote"}` at parse (`base.py:117`); the harness's extractor JS is inert. Witness and attribution both use the **location** gate (neutral tokens `"Remote"`/`"Alfa"`, no title literal).

- [ ] **Step 1: Write the test**

```python
"""An unconfigured sluice bins nothing.

Empty-config-abstains, end to end: with every preference gate explicitly empty,
a lead that a *configured* gate would reject is NOT dismissed. The trap this is
built to avoid is `build_harness`'s default `target_locations=("remote",)` -- the
literal 672ad2a bug value -- so both arms set the gates explicitly. The anti-
vacuity proof is the attribution arm: the SAME board-ingested lead, run once more
with `target_locations=("Alfa",)` (a neutral conftest token that does NOT match
"Remote"), comes back `dismiss`. That proves the location gate would bin this
exact lead, so the empty-gate pass is abstention -- not a bland lead. Witness and
attribution are aligned on the location gate; the lead carries "Remote" via the
source's parse-time `extra`, so deleting the guard reddens the abstain arm.
"""
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]


def _ingest_and_triage(h):
    app = h.sluice(ScriptedBackend(default_verdict="shortlist"))
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))
    return h.vault.read_leads()[0]


def test_an_empty_config_bins_nothing(tmp_path, monkeypatch):
    # ── Arm 1: every gate empty -> the located lead is NOT dismissed (abstain) ──
    abstain_dir = tmp_path / "abstain"
    abstain_dir.mkdir()
    h = build_harness(abstain_dir, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                      target_locations=(), accept_titles=(), reject_titles=(),
                      perm_floor_gbp=0, contract_floor_gbp_day=0)
    lead = _ingest_and_triage(h)
    assert lead.fm.get("location") == "Remote"   # precondition: lead is located
    assert lead.status != "dismiss"              # the empty location gate abstained

    # ── Arm 2 (attribution): same lead, a non-matching neutral target -> dismiss.
    # Proves the location gate WOULD bin it, so Arm 1 is abstention, not a bland lead.
    attrib_dir = tmp_path / "attrib"
    attrib_dir.mkdir()
    h2 = build_harness(attrib_dir, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                       target_locations=("Alfa",), accept_titles=(), reject_titles=(),
                       perm_floor_gbp=0, contract_floor_gbp_day=0)
    assert _ingest_and_triage(h2).status == "dismiss"
```

- [ ] **Step 2: Run it to verify it PASSES on correct code**

Run: `python -m pytest tests/e2e/test_an_empty_config_bins_nothing.py -q`
Expected: PASS (empty gate abstains; the `("Alfa",)` arm dismisses).

- [ ] **Step 3: Mutation-witness — prep the bytecode**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
Expected: no output (success).

- [ ] **Step 4: Mutate by DELETING the guard conjunct**

In `sluice/triage/classify.py:123`, change:

```python
    if cfg.target_locations and location and not any(
            t in location for t in cfg.target_locations):
```
to (delete the `cfg.target_locations and` conjunct):
```python
    if location and not any(
            t in location for t in cfg.target_locations):
```

- [ ] **Step 5: Run the witness twice**

Run: `python -m pytest tests/e2e/test_an_empty_config_bins_nothing.py -q`
Expected: **FAIL** — Arm 1's `status != "dismiss"` reddens (the empty gate now rejects "Remote").

Run: `python -m pytest -q --deselect tests/e2e/test_an_empty_config_bins_nothing.py --ignore=tests/e2e/test_an_empty_config_bins_nothing.py`
Expected: also FAIL in `tests/test_sluice_neutral_defaults.py` and/or the functional triage-abstain test — confirms this invariant is **also** unit/functional-witnessed, so S1 is **composition-root integration coverage**, not a unique catch. Record that framing in the PR body.

- [ ] **Step 6: Restore byte-identically and confirm green**

Restore `classify.py:123` to the original two-conjunct form. Run:
`python -m pytest -q`
Expected: 742 passed (741 + S1), ruff clean.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_an_empty_config_bins_nothing.py
git commit -m "$(cat <<'EOF'
test(e2e): an empty config bins nothing

Empty-config-abstains end to end: every preference gate explicitly empty, a
located lead is not dismissed; the attribution arm (target_locations=("Alfa",))
proves the location gate would bin the same lead, so the pass is abstention.
Integration coverage of the 672ad2a invariant through the composition root.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 3: S2 — a rejection clears my backlog

**Files:**
- Create: `tests/e2e/test_a_rejection_clears_my_backlog.py`

**Interfaces:**
- Consumes: `build_harness(...) -> Harness`; `Harness.sluice(backend)`; `Harness.paths["vault"]`; `Harness.vault`; `ScriptedBackend(*, track_response=[(marker, dict), ...])`; `FakeGoogleClient(messages)`; `Sluice.track(client=..., now_iso=...) -> RunReport` (`.proposed`, `.auto`, `.open_proposals`).
- Produces: nothing downstream.

**Design (spec §S2):** dead-letter durability (#49) — resolving a lead clears its follow-up backlog. Two `track` runs on one lead seeded **in-flight** (`applied`; the engine filters to `_INFLIGHT`, `engine.py:64-65`). Run 1: a **matched low-confidence** rejection (`confidence < auto_reject_min=0.9`) resolves to `action="proposed"` with `lead_slug` set (`reconcile.py:107-119`), recording a dead-letter Entry keyed under the slug — assert `Entry.lead == slug`, not merely non-empty (an *ambiguous* signal records `lead=""`, `engine.py:119`, which `clear_lead(slug)`'s `WHERE lead=?` never matches). Run 2: a **high-confidence** rejection (`≥0.9`) auto-advances (`action="applied"`) and fires `clear_lead(ev.lead_slug)` (`engine.py:108`). The two emails carry **distinct message ids** — the runs share a persisted `seen` (`engine.py:80`).

- [ ] **Step 1: Write the test**

```python
"""Resolving a lead clears its follow-up backlog (#49).

Dead-letter durability end to end. Run 1 records a proposal (a matched, LOW-
confidence rejection -> action="proposed" with the lead slug set), so the store
holds a clearable entry -- asserted by `Entry.lead == slug`, the anti-vacuity
precondition (clear_lead on an empty store, or an entry keyed `lead=""`, is a
no-op). Run 2's HIGH-confidence rejection auto-advances the lead to `rejected`
and clears its entry. The two emails carry distinct message ids because the runs
share a persisted `seen` set.
"""
import os

from tests.harness import FakeGoogleClient, ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"

_APPLIED_NOTE = """---
base: "[[Job Leads.base]]"
company: "Example Foundry"
role: "Staff Engineer"
location: "Remote"
status: applied
score: 0
url: "https://remoteok.example/jobs/1"
applied_date: 2026-07-01
ats: example-ats
relevance_notes: ""
---

# Example Foundry - Staff Engineer

Application in flight.
"""

# Run 1: a soft/low-confidence rejection -> proposed (records a dead-letter entry).
# Run 2: a high-confidence rejection -> auto-advance to rejected (clears it).
_PROPOSAL = {"lead": "Example Foundry", "type": "rejection", "confidence": 0.5,
             "when": None, "links": [], "materials": [], "summary": "maybe not moving forward"}
_AUTO_REJECT = {"lead": "Example Foundry", "type": "rejection", "confidence": 0.95,
                "when": None, "links": [], "materials": [], "summary": "not moving forward"}


def _msg(subject, marker):
    return {"headers": {"from": "noreply@example.invalid", "subject": f"{subject} {marker}"},
            "body_text": f"Update on your application. {marker}",
            "thread_id": "th-1", "attachments": []}


def test_a_rejection_clears_my_backlog(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=[])
    leads_dir = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads_dir, exist_ok=True)
    with open(os.path.join(leads_dir, "Example Foundry - Staff Engineer.md"),
              "w", encoding="utf-8") as f:
        f.write(_APPLIED_NOTE)
    slug = h.vault.read_leads()[0].slug

    backend = ScriptedBackend(track_response=[("PROPOSAL-SIGNAL", _PROPOSAL),
                                              ("REJECTION-SIGNAL", _AUTO_REJECT)])
    app = h.sluice(backend)

    # ── run 1: a low-confidence rejection is PROPOSED and dead-lettered ──
    rep1 = app.track(client=FakeGoogleClient({"msg-1": _msg("Following up", "PROPOSAL-SIGNAL")}),
                     now_iso="2026-07-10T00:00:00+00:00")
    assert rep1.proposed == 1
    assert [e.lead for e in rep1.open_proposals] == [slug]   # a CLEARABLE entry exists
    assert h.vault.read_leads()[0].status == "applied"       # proposed never advances

    # ── run 2: a high-confidence rejection auto-advances AND clears the backlog ──
    rep2 = app.track(client=FakeGoogleClient({"msg-2": _msg("Decision", "REJECTION-SIGNAL")}),
                     now_iso="2026-07-15T00:00:00+00:00")
    assert rep2.auto == 1
    assert h.vault.read_leads()[0].status == "rejected"
    assert rep2.open_proposals == []                         # the backlog cleared
```

- [ ] **Step 2: Run it to verify it PASSES**

Run: `python -m pytest tests/e2e/test_a_rejection_clears_my_backlog.py -q`
Expected: PASS. (If run 1 shows `proposed == 0`, the confidence or lead name is wrong; if run 2's `open_proposals` is non-empty on correct code, the entry was keyed wrong — re-check `Entry.lead == slug`.)

- [ ] **Step 3: Mutation-witness — prep bytecode**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`

- [ ] **Step 4: Mutate by DELETING the clear-on-advance call**

In `sluice/track/engine.py`, delete the auto-advance clear (lines 107–108):

```python
                if not dry_run and ev.lead_slug:
                    _dl_write(rep, lambda: deadletter.clear_lead(ev.lead_slug))
```
Remove both lines. (`res.action == "applied"` still increments `rep.auto`; only the clear is gone.)

- [ ] **Step 5: Run the witness twice**

Run: `python -m pytest tests/e2e/test_a_rejection_clears_my_backlog.py -q`
Expected: **FAIL** — run 2's `rep2.open_proposals == []` reddens (the entry survives the auto-advance).

Run: `python -m pytest -q --ignore=tests/e2e/test_a_rejection_clears_my_backlog.py`
Expected: check whether any `test_track_engine.py` test also reddens. Record the result — the store-level `clear_lead` is unit-witnessed (`test_track_deadletter`); if the *engine wiring* is not, S2 is a **unique** end-to-end witness for clear-on-auto-advance; otherwise integration. State the honest outcome in the PR body.

- [ ] **Step 6: Restore and confirm green**

Restore `engine.py:107-108`. Run: `python -m pytest -q`
Expected: 743 passed (+S2), ruff clean.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_a_rejection_clears_my_backlog.py
git commit -m "$(cat <<'EOF'
test(e2e): a rejection clears the dead-letter backlog

#49 durability end to end: run 1's low-confidence rejection records a proposal
(Entry.lead == slug, the anti-vacuity precondition); run 2's high-confidence
rejection auto-advances the lead to rejected and clears its dead-letter entry.
Distinct message ids across the two runs (shared persisted seen).

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 4: S3 — a CV citing an unbacked figure never ships

**Files:**
- Create: `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py`

**Interfaces:**
- Consumes: `build_harness(...)`; `Harness.sluice(backend)`; `Harness.recorder`; `ScriptedBackend(*, cv_by_company={company: cv_text}, default_verdict="shortlist")`; `Sluice.ingest`, `Sluice.triage`, `Sluice.compose_cv(all_shortlist=True) -> [CvResult]` (`.status`, `.violations`).
- Produces: nothing downstream.

**Design (spec §S3):** the CV gate's **numeric arm**. The canned CV has the correct `WORK EXPERIENCE` header (so the gate RUNS — this is not the structural-drift arm `test_a_clean_lead_reaches_rejected` uses) and a bullet citing a figure absent from the cited bundle entry. It must carry **exactly one** violation (the uncited figure) — any other gate failure would keep it `skipped-gate` under the numeric-check mutation, making the witness inert. The bundle's single entry (Example Foundry → `[EF1]`, metrics `3 8`) allows `{3, 8}`; the bullet cites `42`. The retry keeps the same first line (`compose.py:61-64` appends violations to the tail), so `ScriptedBackend` returns the same violating CV → `skipped-gate`.

- [ ] **Step 1: Write the test**

```python
"""A CV citing a figure absent from the bundle never ships.

The fabrication gate's NUMERIC arm, end to end (distinct from the structural-drift
arm that `test_a_clean_lead_reaches_rejected` exercises). The composed CV has the
correct WORK EXPERIENCE header, so the citation gate RUNS; its one and only
violation is a bullet citing "42", a figure in no cited bundle entry (the single
[EF1] entry allows {3, 8}). The engine retries once -- the retry re-keys the same
canned CV, since compose appends violations past the prompt's first line -- then
skips. Exactly one violation is load-bearing: any other failure would keep the CV
skipped-gate under the numeric-check mutation and the witness would go inert.
"""
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]

# PASSING_CV with ONE bullet changed to cite 42 -- absent from the cited [EF1]
# entry (metrics "3 8"). Everything else is clean: reverse-chronological, every
# bullet cited, no AI-slop tokens, correct header. So the ONLY violation is 42.
NUMERIC_VIOLATION_CV = "\n".join([
    "JANE ROE", "",
    "WORK EXPERIENCE", "",
    "Example Systems",
    "02/2023–present | Remote | Staff Engineer",
    "- Cut deploy time by 42 percent [EF1]",
    "",
    "Example Analytics",
    "06/2020–01/2023 | Remote | Senior Engineer",
    "- Grew the team from 3 to 8 engineers [EF1]",
    "",
    "CERTIFICATES", "- CSM",
    "EDUCATION", "- Example University, 2015 | BSc",
])


def test_a_cv_citing_an_unbacked_figure_never_ships(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    app = h.sluice(ScriptedBackend(
        cv_by_company={"Example Foundry": NUMERIC_VIOLATION_CV},
        default_verdict="shortlist"))
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))

    results = app.compose_cv(all_shortlist=True)
    assert len(results) == 1
    r = results[0]
    assert r.status == "skipped-gate"
    assert any("INVENTED METRIC" in v and "42" in v for v in r.violations)
    assert h.recorder.rendered == []          # nothing was ever rendered
```

- [ ] **Step 2: Run it to verify it PASSES**

Run: `python -m pytest tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py -q`
Expected: PASS. (If `r.status == "rendered"`, the CV had no violation — check the `42` bullet. If it fails with a *different* violation, the canned CV tripped another check — fix it so `42` is the ONLY violation.)

- [ ] **Step 3: Mutation-witness — prep bytecode**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`

- [ ] **Step 4: Mutate by DELETING the numeric check**

In `sluice/cv/validate.py`, delete the invented-metric block (lines 95–96):

```python
            if invented:
                v.append(f"INVENTED METRIC {sorted(invented)} not in {cites}: {prose.strip()[:50]}")
```
Remove both lines. (`invented` is computed at :94 but now unused — that is fine; do not add a suppression, just delete the `if`/append.)

- [ ] **Step 5: Run the witness twice**

Run: `python -m pytest tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py -q`
Expected: **FAIL** — with no numeric violation the CV passes the gate and renders; `r.status == "skipped-gate"` and `recorder.rendered == []` both redden.

Run: `python -m pytest -q --ignore=tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py`
Expected: `tests/test_cv_validate.py` also reddens — the numeric arm is unit-witnessed, so S3 is **integration coverage**; its distinct contribution is exercising the numeric arm + retry-once-then-skip through the composition root. State that framing in the PR body.

- [ ] **Step 6: Restore and confirm green**

Restore `validate.py:95-96`. Run: `python -m pytest -q`
Expected: 744 passed (+S3), ruff clean.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py
git commit -m "$(cat <<'EOF'
test(e2e): a CV citing an unbacked figure never ships

The fabrication gate's numeric arm end to end: a composed CV with the correct
WORK EXPERIENCE header but a bullet citing a figure absent from the cited bundle
entry is caught, retried once, and skipped -- never rendered. Exactly one
violation keeps the witness live; integration coverage of the retry-then-skip
wiring the structural-drift path never reaches.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 5: S4 — a rejected lead cannot be dragged back

**Files:**
- Create: `tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py`

**Interfaces:**
- Consumes: `build_harness(...)`; `Harness.paths["vault"]`; `Harness.vault`; `Harness.sluice(backend)`; `Sluice.track_confirm(*, lead, to, when=None, dry_run=False) -> dict` (`["ok"]`).
- Produces: nothing downstream.

**Design (spec §S4):** never-regress terminal — a `rejected` lead is never advanced out. Reachable end-to-end **only** via `track_confirm` (email signals filter to `_INFLIGHT`, so a rejected lead never reaches `reconcile`). `can_advance("rejected", "offer")` is `False` via the terminal guard (`status.py:66`). No backend call is made by `confirm`.

- [ ] **Step 1: Write the test**

```python
"""A rejected lead cannot be dragged back onto the ladder.

Never-regress terminal, end to end. A `rejected` lead is a terminal; confirming
it forward to `offer` must be refused (`can_advance` returns False for a move out
of a terminal). This is reachable end to end only through `track_confirm` -- an
email rejection filters to _INFLIGHT and never reaches reconcile for a terminal
lead. The note must come back byte-for-byte unchanged.
"""
import os

from tests.harness import ScriptedBackend, build_harness

_REJECTED_NOTE = """---
base: "[[Job Leads.base]]"
company: "Example Foundry"
role: "Staff Engineer"
location: "Remote"
status: rejected
score: 0
url: "https://remoteok.example/jobs/1"
applied_date: 2026-07-01
ats: example-ats
relevance_notes: ""
---

# Example Foundry - Staff Engineer

Application closed.
"""


def test_a_rejected_lead_cannot_be_dragged_back(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/x",
                      rows=[])
    leads_dir = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads_dir, exist_ok=True)
    note_path = os.path.join(leads_dir, "Example Foundry - Staff Engineer.md")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(_REJECTED_NOTE)
    before = open(note_path, encoding="utf-8").read()

    app = h.sluice(ScriptedBackend())          # confirm makes no backend call
    slug = h.vault.read_leads()[0].slug
    out = app.track_confirm(lead=slug, to="offer")

    assert out["ok"] is False                                    # terminal refused
    assert h.vault.read_leads()[0].status == "rejected"          # status intact
    assert open(note_path, encoding="utf-8").read() == before    # byte-for-byte
```

- [ ] **Step 2: Run it to verify it PASSES**

Run: `python -m pytest tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py -q`
Expected: PASS. (If `out["ok"] is True`, the confirm advanced a terminal — the terminal guard is missing or the seeded status is wrong.)

- [ ] **Step 3: Mutation-witness — prep bytecode**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`

- [ ] **Step 4: Mutate by DELETING the terminal guard**

In `sluice/core/status.py`, delete lines 66–67 of `can_advance`:

```python
    if c in _TERMINAL:
        return False
```
Remove both lines. Now a move out of a terminal falls through to the rank check (`rejected` is not in `_LADDER`, rank -1, so any laddered target ranks higher → returns True).

- [ ] **Step 5: Run the witness twice**

Run: `python -m pytest tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py -q`
Expected: **FAIL** — `confirm` advances the rejected lead; `out["ok"] is False` and the status/byte assertions redden.

Run: `python -m pytest -q --ignore=tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py`
Expected: check `tests/test_core_status_advance.py` / `test_track_engine.py`. The terminal-EXIT guard may be uniquely reached end-to-end via `confirm` (the confirm-backward unit test exercises the *rank* guard, a different branch). Record whether S4 is unique or integration in the PR body from what actually reddens.

- [ ] **Step 6: Restore and confirm green**

Restore `status.py:66-67`. Run: `python -m pytest -q`
Expected: 745 passed (+S4), ruff clean.

- [ ] **Step 7: Commit**

```bash
git add tests/e2e/test_a_rejected_lead_cannot_be_dragged_back.py
git commit -m "$(cat <<'EOF'
test(e2e): a rejected lead cannot be dragged back

Never-regress terminal end to end: track confirm refuses to advance a rejected
lead to offer (can_advance bars a move out of a terminal), and the note is
unchanged byte-for-byte. Reachable end to end only via confirm, since an email
rejection filters to _INFLIGHT and never reaches reconcile for a terminal lead.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 6: S5 — assert no gated CV reaches the output directory

**Files:**
- Modify: `tests/e2e/test_a_clean_lead_reaches_rejected.py` (the renamed full_pipeline test, in the cv-hop block)

**Interfaces:**
- Consumes: `sluice.cv.engine._slug(company, role) -> str` (module-level, `cv/engine.py:36`); `Harness.paths["cv_output"]`.
- Produces: nothing downstream.

**Design (spec §S5):** the on-disk form of "a fabricated CV never reaches the output directory, but the good one did." The recording renderer writes a real PDF to `{cv_output}/{_slug(company, role)}/CV.pdf` for every gate-*passing* CV (`cv/engine.py:103,108`, `renderer.py:35-42`); a `skipped-gate` lead returns before render (`engine.py:88`) and writes nothing. So the clean lead's subdir is present and the gate-failing lead's is absent. (A global `glob("*") == []` would be WRONG — the clean lead renders, so `cv_output` is non-empty; this was a three-reviewer round-1 finding.)

- [ ] **Step 1: Add the `_slug` import at the top of the file**

Add near the existing imports:

```python
from sluice.cv.engine import _slug
```

- [ ] **Step 2: Add the filesystem assertion in the cv-hop block**

Immediately after the existing recorder assertion (`assert h.recorder.rendered == [PASSING_CV]`) in the cv hop, add:

```python
    # On disk: the clean lead's CV reached the output dir; the gate-failing lead's
    # never did. (A global "output dir empty" check would be wrong -- the clean
    # lead renders a real PDF there.)
    cv_out = h.paths["cv_output"]
    assert os.path.isdir(os.path.join(cv_out, _slug("Example Foundry", "Staff Engineer")))
    assert not os.path.exists(os.path.join(cv_out, _slug("Example Telemetry", "Senior Engineer")))
```

(`os` is already imported in this file.)

- [ ] **Step 3: Run it to verify it PASSES**

Run: `python -m pytest tests/e2e/test_a_clean_lead_reaches_rejected.py -q`
Expected: PASS — the clean lead's subdir exists; the gate-failing lead's does not.

- [ ] **Step 4: Mutation-witness — prep bytecode**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests`

- [ ] **Step 5: Mutate the structural gate so the drifted CV would render**

In `sluice/cv/engine.py`, delete the structural-guard block that prepends the STRUCTURAL violation (the lines around `engine.py:78-81` that add `"STRUCTURAL: composed CV lacks the exact 'WORK EXPERIENCE' header ..."`). With the guard gone, the gate-failing (drifted-header) lead has no violations and renders, creating its subdir.

- [ ] **Step 6: Run the witness twice**

Run: `python -m pytest tests/e2e/test_a_clean_lead_reaches_rejected.py -q`
Expected: **FAIL** — the gate-failing lead now renders, so its `_slug("Example Telemetry", "Senior Engineer")` subdir exists and the `not os.path.exists(...)` assertion reddens (the existing `recorder.rendered == [PASSING_CV]` reddens too — S5 is the on-disk restatement of the same gate promise).

Run: `python -m pytest -q --ignore=tests/e2e/test_a_clean_lead_reaches_rejected.py`
Expected: `tests/test_cv_engine.py` structural-header tests also redden — the gate is unit-witnessed; S5 strengthens the existing e2e assertion to an on-disk fact. Note this in the PR body.

- [ ] **Step 7: Restore and confirm green**

Restore `cv/engine.py`'s structural guard. Run: `python -m pytest -q && ruff check sluice tests`
Expected: 745 passed; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add tests/e2e/test_a_clean_lead_reaches_rejected.py
git commit -m "$(cat <<'EOF'
test(e2e): assert no gated CV reaches the output directory

Strengthen the full-pipeline gate assertion to an on-disk fact: the clean lead's
CV subdir is present under cv.output_dir while the gate-failing lead's is absent.
Complements the recorder check; the fabricated CV never reaches disk, the good
one does.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Self-Review

**Spec coverage** — every spec §maps to a task:
- §File plan renames → Task 1. §S1 → Task 2. §S2 → Task 3. §S3 → Task 4. §S4 → Task 5. §S5 → Task 6. ✓ No task is missing; no task exceeds "complete the e2e acceptance suite."

**Placeholder scan** — no TBD/TODO; every test body is complete code; every mutation names exact lines and before/after; every command has expected output. ✓

**Type/name consistency** — `build_harness`, `ScriptedBackend(*, cv_by_company, triage_verdicts, default_verdict, track_response)`, `FakeGoogleClient`, `Harness.sluice/vault/recorder/paths`, `Sluice.ingest/triage/compose_cv/track/track_confirm`, `_slug(company, role)` all match the harness and `sluice/` sources read at `eb68b73`. ✓

**Test-count arithmetic** — starts at 741; +1 per new scenario file (Tasks 2–5) → 745 after Task 5; Task 6 modifies an existing test (no new count). Renames (Task 1) preserve the count. If the local number differs, trust `pytest`'s own PASS/FAIL, not the arithmetic.

**Open item deferred to the PR body (honest framing, not a plan gap):** the run-twice isolation step for S2 and S4 has a genuinely-unknown outcome (unique vs integration) that only running it resolves — the plan says to *record what actually reddens*, never to assert it in advance.

## Post-implementation (not tasks — the standing cadence)

After Task 6, before pushing: `/review-plan` is already done (converged); run `/review-pr` **and** CodeRabbit per the merge gate, then `path-to-green`. Decide with the user whether the `.rulesync/rules/CLAUDE.md` stale lines ("no runtime selection exercised yet"; `fetch` for the `fetcher` key) are addressed here or stay human-gated (they stay noted, not applied).
