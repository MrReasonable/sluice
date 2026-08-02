# Lead layout + `sluice leads reconcile` (#1 PR B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Ship the config-gated `Active/`/`Archive/` lead layout (OFF by default) and the human-gated
`sluice leads reconcile` pass that files notes into it, closing issue #1.

**Architecture:** One root-`Config` knob (`lead_layout`) picks a named layout. The vault gains a *write
folder* distinct from its *scan set* — `leads_dir` under the flat default, `leads_dir/Active` under
`active_archive` — so a new note is created already-reconciled. A pure `layout_subfolder(status, layout)`
in `core/leads.py` derives the folder from status (Archive = `dismiss` + every terminal, read from
`core/status.py`, never hand-listed). `Vault.reconcile_layout()` sweeps the *managed* folders only and
moves notes with the same `O_EXCL`-reserve + `os.replace` primitive `merge_cluster` already ships,
extracted so there is one definition of an atomic note move.

**Tech Stack:** Python 3.12–3.14 stdlib only (`os`, `ast`, `argparse`). pytest + faker for tests.
`ruff==0.15.21` for lint. No new dependencies.

## Global Constraints

Copied verbatim from `.rulesync/rules/CLAUDE.md` and the design spec. Every task's requirements
implicitly include this section.

- **`sluice/` is standard-library only.** Sole exceptions are `yaml` (guarded `try/except ImportError`
  in each config module) and the Google client libraries (lazy, in `track/google_client.py`). Add no
  runtime dependency.
- **Neutrality: no personal data in `sluice/` or `tests/`.** No employer names, role preferences,
  locations, contact details, hostnames, or absolute paths. Tests generate synthetic job titles with
  seeded `faker` (`tests/conftest.py`). `Active`/`Archive` are structural folder names, not preferences.
- **Never-clobber.** A re-scrape touches only `last_seen`. Every *modify*-write goes through
  `core/vault.py`'s surgical compare-and-set path. **A move is not a write to the note's bytes** — this
  plan adds no new content writer.
- **Never-regress (status).** Reconcile **never writes a status**. A non-canonical status is passed
  through untouched and reported under `unknown`, mirroring `normalize_all_statuses`.
- **Non-resurrection (#81).** `_merged/` stays pruned from the scan set at the TOP LEVEL only, and is
  never a reconcile source or destination. `tests/conformance/test_store_contract.py::test_merged_away_lead_is_never_recreated`
  must keep passing, and Task 8 adds the same property with `lead_layout` enabled.
- **Empty config means abstain.** `lead_layout` defaults to `""` (flat) — an unconfigured install is
  byte-identical to today. An **unknown** value raises at construction and lists the valid names
  (`_select_backend`'s precedent); it must not degrade silently to flat.
- **The `leads` passes report by default.** `leads reconcile` prints and changes nothing until
  `--apply`. **No `--dry-run`** — the default *is* the dry run, and a flag that does nothing is drift.
- **Comments explain *why***: the invariant upheld, the bug prevented, the trade taken. Match the
  surrounding density; do not strip it.
- **Conventional commits** (`feat(vault): ...`, `test: ...`, `docs: ...`). Every commit body ends with
  the trailer block the repo uses.
- **Mutation testing discipline.** Run
  `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
  ONCE before any mutation run. **Mutate by MOVING or DELETING, never by ADDING** — a check added
  beside the original is an equivalent mutant and stays green. Witness each mutant by node id and
  confirm no *pre-existing* test in the same file is what catches it.
- **The `ruff` on PATH is a broken proto shim, and so is bare `python`.** Use `.venv/bin/python -m ruff
  check sluice tests scripts`, `.venv/bin/python -m pytest`, and `.venv/bin/sluice` for the CLI.
  **Never bare `python` or bare `ruff` anywhere in this repo.** There is no `sluice/__main__.py`, so
  `python -m sluice` does not work either — the entry point is the `sluice = "sluice.cli:main"`
  console script (verified).
- **How to check the suite after each task.** Baseline is **1921** on `main` @ `54bbb11`. Each task
  states the DELTA it adds, in COLLECTED ITEMS (`@pytest.mark.parametrize` cases counted
  individually, not test *functions* — the first draft of this plan counted functions and was 9 low
  on task one alone). Verify a task's own delta with:

  ```bash
  .venv/bin/python -m pytest --collect-only -q <the task's test files> | tail -1
  ```

  The load-bearing check is **not** the absolute total, which rots the moment any task's test count
  is adjusted; it is: **the new total equals the previous total plus this task's measured delta, and
  no PRE-EXISTING test changed its result.** If the arithmetic disagrees, find out which side is
  wrong before touching either — never overwrite the expected number with whatever pytest printed,
  which is how a sentinel silently stops sentinelling for every later task.
- **`Lead` requires three positional fields** — `source`, `search`, `title` (`core/leads.py:142`).
  The shipped helper form is `tests/test_leads_expire.py:23`:
  `Lead(source="s", search="q", title=title, company=company, url=url)`. Omitting `search` is a
  `TypeError` before any assertion runs.
- **Placeholder locations come from `tests/conftest.py:LOCATIONS`** (`("Alfa", "Bravo", "Charlie")`) —
  module-level precisely so bare seed helpers that cannot take a fixture can import it. They are
  clearly fictional and pairwise token-disjoint, so any two read `DIFFERENT` under
  `_compare_locations`. **Never invent a fresh place-name literal.**

## Decisions this plan implements

Four are settled in `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md` and are **not**
re-opened here:

1. Sluice does not own the layout, it offers one. Config-gated, OFF by default.
2. Only `leads reconcile` moves a note. No pipeline command relocates anything.
3. The Archive set is **derived** — `dismiss` plus every terminal read from `core/status.py`.
4. The scan skips only directories sluice itself creates (today just `_merged`).

Three more were open and are settled here:

5. **A slug two notes claim is refused, not filed** (user decision, 2026-08-02). Reconcile cannot repair
   it: the slug *is* the filename, so a rename orphans the note from `_resolve_path`'s candidate walk,
   and picking a survivor is `leads dedupe`'s job via `resolve_merge_status`. Both twins are reported
   under `ambiguous` and neither moves — the same refuse-rather-than-pick shape `index_by_slug`,
   `upsert` and `select_one` already use.
6. **Reconcile relocates only notes in the MANAGED folders** (user decision, 2026-08-02): the leads-dir
   root, `Active/`, and `Archive/`. A lead the user filed into their own subfolder is reported under
   `user_filed` and left alone. This keeps decision 4 true for *writes* as well as for reads, and
   migration from a flat vault still works because every note in one sits at the root.
7. **`lead_layout` unset makes reconcile a no-op that says so**, exactly like `leads expire` under
   `lead_ttl_days: 0`: print the knob-unset line, emit `[]`/`{}` on `--json`, exit 0 for a report and
   exit 1 for a `--apply` that was asked to write and did not. The alternative — treating flat as a
   layout and flattening the vault — would yank every lead out of the user's own subfolders, which is
   decision 4 pointed the wrong way.

## Stale premises found while planning — correct, do not propagate

Verified against `gh issue view 1` and the shipped code on `main` @ `54bbb11`:

- **Issue #1's body is stale in three places.** It names `existing_keys()`, which PR #66 REMOVED from the
  Store protocol; it gives `Archive/` as `{dismiss, rejected}`, which decision 3 supersedes; and its
  step 4 is a one-off migration, which decision 1 supersedes (a flat vault is maximal drift and
  reconcile repairs it — one code path, not two). Plan against the design doc and the shipped code.
- **`sluice/cli.py:674`, `:686`, `:735` and `docs/superpowers/plans/2026-07-31-sluice-init-v2.md:258` all
  claim issue #1 lands "a real second store" / "the store seam".** It does not — #1 is vault subfolders.
  The deferral trigger those comments name never fires. Task 9 corrects the prose; the *reasoning* for
  not inventing `Store.display_location()` is still sound and is kept.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `sluice/core/status.py` | + `is_terminal(status)` — the public terminal predicate beside `is_application_owned`/`is_canonical`/`can_apply` | 1 |
| `sluice/core/leads.py` | + `LEAD_LAYOUTS`, `ACTIVE_SUBDIR`, `ARCHIVE_SUBDIR`, `layout_subfolder(status, layout)` — the pure status→folder verdict | 1 |
| `sluice/core/config.py` | + `Config.lead_layout: str = ""`, named in `load_config` | 2 |
| `sluice/stores/vault.py` | `_make` passes `lead_layout=config.lead_layout` | 2 |
| `sluice/core/vault.py` | `Vault.__init__` validates + stores the layout; `_write_folder()`; create arm and `upsert` makedirs point at it; `_reserve_and_move` extracted; `reconcile_layout()` | 2,3,4,5 |
| `sluice/core/app.py` | `Sluice.reconcile_report()` / `Sluice.reconcile()` + the store-capability guard | 6 |
| `sluice/cli.py` | `cmd_leads_reconcile` + the `leads reconcile` subparser; #8 comment corrections | 7,9 |
| `sluice/triage/prompt.py` | remove the test-only filesystem-reaching surface | 9 |
| `sluice.yaml.example` | `lead_layout` documented COMMENTED | 2 |
| `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`, the design spec | the layout, the pass, and the PR B status | 8 |
| `tests/test_lead_layout_map.py` | the pure map + the derived-Archive enumeration guard | 1 |
| `tests/test_lead_layout_config.py` | the knob: defaults, validation, round-trip, example file | 2 |
| `tests/test_vault_write_folder.py` | new notes land in the write folder | 3 |
| `tests/test_vault_atomic_move.py` | the shared move primitive, both collision policies | 4 |
| `tests/test_leads_reconcile.py` | the sweep: scope, buckets, refusals, isolation, cache | 5 |
| `tests/test_leads_reconcile_cli.py` | facade + CLI: report-first, exit codes, `--json`, knob-unset | 6,7 |
| `tests/test_vault_makedirs_scope.py` | `_EXPECTED` gains the two new makedirs targets | 3,5 |

---

### Task 1: The pure status→folder map

The Archive set must be **derived** from `core/status.py`, never hand-listed, so a terminal added there
later archives automatically instead of silently staying Active (decision 3).

**Files:**
- Modify: `sluice/core/status.py` (add `is_terminal` beside `can_apply`, ~line 52)
- Modify: `sluice/core/leads.py` (add the layout constants + `layout_subfolder`, after `index_by_slug`)
- Test: `tests/test_lead_layout_map.py` (create)

**Interfaces:**
- Consumes: `sluice.core.status._TERMINAL`, `normalize`, `is_canonical` (all existing).
- Produces:
  - `sluice.core.status.is_terminal(status: str) -> bool`
  - `sluice.core.leads.LEAD_LAYOUTS: tuple[str, ...]` — `("", "active_archive")`
  - `sluice.core.leads.ACTIVE_SUBDIR: str` — `"Active"`
  - `sluice.core.leads.ARCHIVE_SUBDIR: str` — `"Archive"`
  - `sluice.core.leads.layout_subfolder(status: str, layout: str) -> str | None` — the subfolder a lead
    in `status` belongs in, `""` for the leads-dir root, `None` for a non-canonical status (never moved).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lead_layout_map.py`:

```python
"""The pure status -> folder verdict, and the guard that keeps the Archive set DERIVED.

Decision 3: Archive is `dismiss` plus every terminal, READ from core/status.py. A hand-listed
set silently keeps a newly-added terminal in Active, which is the quiet-wrong-default this
codebase engineers out. The enumeration guard below is what makes that unfakeable."""
import pytest

from sluice.core import status as _status
from sluice.core.leads import (
    ACTIVE_SUBDIR,
    ARCHIVE_SUBDIR,
    LEAD_LAYOUTS,
    layout_subfolder,
)


def test_is_terminal_answers_every_terminal_and_nothing_else():
    """SCOPE first: the predicate must agree with status.py's own ladder, both ways.
    Asserting only the True half would pass for `lambda s: True`."""
    for s in _status._TERMINAL:
        assert _status.is_terminal(s), s
    for s in set(_status.CANONICAL) - set(_status._TERMINAL):
        assert not _status.is_terminal(s), s


def test_is_terminal_normalizes_before_deciding():
    """`normalize` folds quoting and drift; a predicate that skipped it would answer False
    for the value a real note carries."""
    assert _status.is_terminal(' "rejected" ')
    assert not _status.is_terminal(' "shortlist" ')


@pytest.mark.parametrize("status", ["new", "shortlist", "research", "needs_review",
                                    "applied", "phone_screen", "interview", "offer"])
def test_a_live_status_is_active(status):
    assert layout_subfolder(status, "active_archive") == ACTIVE_SUBDIR


@pytest.mark.parametrize("status", ["dismiss", "rejected", "accepted", "withdrawn"])
def test_dismiss_and_every_terminal_are_archive(status):
    assert layout_subfolder(status, "active_archive") == ARCHIVE_SUBDIR


def test_the_archive_set_is_derived_from_status_not_hand_listed():
    """THE decision-3 guard. Every canonical status is classified, and the Archive set is
    computed here the way the spec states it -- `dismiss` plus status.py's own `_TERMINAL` --
    then compared with what the shipped map answers. A hand-listed literal inside
    `layout_subfolder` passes today and diverges the moment `_TERMINAL` grows; this
    comparison cannot.

    It asserts on SCOPE too (`len(CANONICAL) == 12`): a sweep over an empty vocabulary would
    satisfy every membership check below, and for a guard whose success case is 'nothing was
    mis-filed' that is indistinguishable from working."""
    assert len(_status.CANONICAL) == 12, sorted(_status.CANONICAL)
    expected_archive = {"dismiss"} | set(_status._TERMINAL)
    got_archive = {s for s in _status.CANONICAL
                   if layout_subfolder(s, "active_archive") == ARCHIVE_SUBDIR}
    got_active = {s for s in _status.CANONICAL
                  if layout_subfolder(s, "active_archive") == ACTIVE_SUBDIR}
    assert got_archive == expected_archive
    assert got_active == set(_status.CANONICAL) - expected_archive
    assert got_active | got_archive == set(_status.CANONICAL), "a canonical status is unclassified"


def test_a_non_canonical_status_is_never_moved():
    """never-regress: an unrecognized status is passed through untouched everywhere else, so
    the layout must not decide a folder for it either. None means 'leave it where it is'."""
    assert layout_subfolder("some_future_state", "active_archive") is None
    assert layout_subfolder("", "active_archive") is None


def test_the_flat_layout_puts_every_status_at_the_root():
    for s in _status.CANONICAL:
        assert layout_subfolder(s, "") == ""


def test_the_flat_layout_still_declines_a_non_canonical_status():
    """Even flat must answer None rather than "": "" means 'the root is where this belongs',
    which would make reconcile MOVE an unknown-status note out of a subfolder. None is the
    only answer that means 'do not touch this note'."""
    assert layout_subfolder("some_future_state", "") is None


def test_an_unknown_layout_raises_and_lists_the_valid_names():
    """Fail loudly at construction. A typo'd layout must not fall through to flat -- see
    `_select_backend`. Matched on the MESSAGE, not just the type: ValueError is what a
    dozen other things in this module raise, so asserting the type alone would pass with
    the guard deleted."""
    with pytest.raises(ValueError, match="active_archive"):
        layout_subfolder("new", "activearchive")


def test_lead_layouts_names_the_flat_default_first():
    assert LEAD_LAYOUTS[0] == ""
    assert "active_archive" in LEAD_LAYOUTS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lead_layout_map.py -v`
Expected: FAIL — `ImportError: cannot import name 'ACTIVE_SUBDIR' from 'sluice.core.leads'`

- [ ] **Step 3: Add `is_terminal` to `core/status.py`**

Insert immediately after `can_advance` (which is the first function that reads `_TERMINAL`, so the
predicate sits with the ladder it describes):

```python
def is_terminal(status: str) -> bool:
    """True iff `status` is an application terminal -- a state never advanced out of.
    Public because the #1 lead layout derives its Archive set from it (`dismiss` plus every
    terminal) rather than hand-listing one: a terminal added to `_TERMINAL` later must archive
    automatically instead of silently staying in Active. `can_advance` already reads `_TERMINAL`
    for the same vocabulary; this exposes the membership test without exposing the tuple."""
    return normalize(status) in _TERMINAL
```

- [ ] **Step 4: Add the layout map to `core/leads.py`**

Add `from sluice.core import status as _status` to the imports (`core/status.py` imports nothing from
`sluice`, so there is no cycle), then append after `index_by_slug`:

```python
# ── the lead layout (#1) ─────────────────────────────────────────────────────
# Folder = a DERIVED VIEW of status, never a second source of truth. The note's frontmatter
# stays authoritative; a folder that disagrees is drift `sluice leads reconcile` repairs, and
# drift between runs is harmless because the scan is recursive.
ACTIVE_SUBDIR = "Active"
ARCHIVE_SUBDIR = "Archive"
# "" is the flat default and is FIRST, so an unconfigured install is byte-identical to the
# pre-#1 store. A name here is a promise: `_write_folder` and `reconcile_layout` both resolve
# through `layout_subfolder`, so adding an entry without teaching that function raises rather
# than degrading to flat.
LEAD_LAYOUTS = ("", "active_archive")


def layout_subfolder(status: str, layout: str) -> str | None:
    """Which subfolder of the leads dir a lead in `status` belongs in under `layout`.

    Returns "" for the leads-dir root, a subfolder name, or None meaning NEVER MOVE THIS NOTE.

    None is not "the root": never-regress passes an unrecognized status through untouched
    everywhere else, so the layout must not decide a folder for one either. Answering "" for a
    non-canonical status would make reconcile drag it out of wherever a human deliberately put
    it -- so the two are distinct values and callers must test `is None` before truthiness.

    The Archive set is DERIVED -- `dismiss` plus `status.is_terminal` -- and that is the whole
    reason this is a function rather than a dict literal. A terminal added to `core/status.py`
    later archives automatically; a hand-listed set would leave it in Active with nothing red.
    `dismiss` is named explicitly because it is TRIAGE-owned, so no application-lifecycle
    predicate can reach it.

    An unknown layout RAISES and lists the valid names rather than falling through to flat --
    `_select_backend`'s rule. A typo'd `lead_layout: activearchive` that silently behaved as
    flat would leave a user believing their vault was being filed when nothing was.
    """
    if layout not in LEAD_LAYOUTS:
        raise ValueError(
            f"unknown lead_layout {layout!r}; valid: "
            + ", ".join(repr(n) for n in LEAD_LAYOUTS))
    if not _status.is_canonical(status):
        return None
    if not layout:
        return ""
    s = _status.normalize(status)
    return ARCHIVE_SUBDIR if (s == "dismiss" or _status.is_terminal(s)) else ACTIVE_SUBDIR
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lead_layout_map.py -v`
Expected: all PASS

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: **1921 + 20 = 1941** passed, ruff clean. Twenty COLLECTED ITEMS, not eleven functions:
this file has ten test functions, and its two `@pytest.mark.parametrize` decorators expand 8 and 4.
Verify with `.venv/bin/python -m pytest --collect-only -q tests/test_lead_layout_map.py | tail -1`.

If the count differs, reconcile it before continuing — a changed *pre-existing* count means this task
moved behaviour it should not have. **Do not simply overwrite the number with whatever pytest
printed**: that is how a sentinel keyed to a stale count stops sentinelling for every later task.
Work out which side is wrong first.

- [ ] **Step 7: Witness the mutants**

Run `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Then, one at a time — each is a DELETE or a MOVE, never an add — apply, run the named test **by node
id**, confirm RED, and restore:

| Mutation | Test that must redden |
| --- | --- |
| Replace `_status.is_terminal(s)` in `layout_subfolder` with the literal `s in ("rejected",)` | `test_lead_layout_map.py::test_the_archive_set_is_derived_from_status_not_hand_listed` |
| Delete `if not _status.is_canonical(status): return None` | `test_lead_layout_map.py::test_a_non_canonical_status_is_never_moved` |
| Change `return None` to `return ""` in that same arm | `test_lead_layout_map.py::test_the_flat_layout_still_declines_a_non_canonical_status` |
| Delete the `if layout not in LEAD_LAYOUTS: raise` guard | `test_lead_layout_map.py::test_an_unknown_layout_raises_and_lists_the_valid_names` |
| Delete `normalize(status) in` from `is_terminal`, leaving `status in _TERMINAL` | `test_lead_layout_map.py::test_is_terminal_normalizes_before_deciding` |

For each: confirm the reddening test is the NEW one, not a pre-existing test in another file.

- [ ] **Step 8: Commit**

```bash
git add sluice/core/status.py sluice/core/leads.py tests/test_lead_layout_map.py
git commit -m "$(cat <<'EOF'
feat(core): derive the lead layout's Archive set from status.py (#1)

`layout_subfolder(status, layout)` is the one pure verdict the write folder and
`leads reconcile` both resolve through. Archive is `dismiss` plus every terminal
READ from core/status.py via the new public `is_terminal`, never hand-listed: a
terminal added there later must archive automatically rather than silently staying
in Active. A non-canonical status answers None (never move) rather than "" (the
root), because never-regress passes an unrecognized status through untouched and
"" would drag it out of wherever a human put it. An unknown layout raises and
lists the valid names -- a typo must not degrade to flat.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 2: The `lead_layout` knob

Root `Config` field, validated where the store is constructed, documented COMMENTED in the example
file. No behaviour change yet — the write folder still resolves to `leads_dir` because Task 3 has not
pointed it anywhere.

**Files:**
- Modify: `sluice/core/config.py` (add the field near `lead_ttl_days:97`; name it in `load_config` ~`:246`)
- Modify: `sluice/stores/vault.py` (`_make`, pass it through)
- Modify: `sluice/core/vault.py` (`Vault.__init__` ~`:265`)
- Modify: `sluice.yaml.example` (after the `lead_ttl_days` block, ~line 21)
- Modify: `tests/test_sluice_neutral_defaults.py` (the three ABSTAIN guards land here, not in the
  feature file — see Step 5b)
- Test: `tests/test_lead_layout_config.py` (create — construction, validation and the factory wire)

**Interfaces:**
- Consumes: `sluice.core.leads.LEAD_LAYOUTS`, `layout_subfolder` (Task 1).
- Produces:
  - `sluice.core.config.Config.lead_layout: str = ""`
  - `Vault(dir, *, baseline_rel=..., location_noise_words=..., lead_layout: str = "")`, exposing
    `self.lead_layout: str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lead_layout_config.py`:

```python
"""`lead_layout` needs its OWN guards. The #26/#63 neutral-defaults sweep is value-keyed on
LIST-defaulting fields (`isinstance(getattr(cls(), f.name), list)`), so a str field is invisible
to it -- the same gap `lead_ttl_days` documents for ints. Measured: adding
`lead_layout: str = "active_archive"` to Config leaves that sweep entirely green."""
import pathlib

import pytest
import yaml

from sluice.core.config import Config, load_config
from sluice.core.vault import Vault

EXAMPLE = pathlib.Path(__file__).parent.parent / "sluice.yaml.example"


def test_lead_layout_dataclass_default_is_flat():
    assert Config().lead_layout == ""


def test_lead_layout_loader_default_is_flat(monkeypatch):
    """The dataclass default and the LOADER default are two different things -- only
    `load_config` names its fields explicitly, so a field can default correctly on the class
    and still be dropped on the way through. Same split the lead_ttl_days pair exists for."""
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_config(None).lead_layout == ""


def test_lead_layout_round_trips_through_load_config(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: active_archive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config(None).lead_layout == "active_archive"


def test_the_example_file_ships_lead_layout_commented(tmp_path):
    """`sluice.yaml.example` is a CATALOGUE, and this file is COPIED. An ACTIVE
    `lead_layout: active_archive` would hand every copier a filing decision they never made and
    silently start relocating their notes -- the `lead_ttl_days`/`locations` precedent, stated
    in the example file itself. Asserted through yaml.safe_load, which is blind to a comment:
    an active key would appear here."""
    data = yaml.safe_load(EXAMPLE.read_text()) or {}
    assert "lead_layout" not in data, "lead_layout must ship COMMENTED, not active"
    assert "lead_layout:" in EXAMPLE.read_text(), "lead_layout must be documented at all"


def test_the_vault_defaults_to_the_flat_layout(tmp_path):
    assert Vault(str(tmp_path)).lead_layout == ""


def test_the_vault_accepts_a_valid_layout(tmp_path):
    assert Vault(str(tmp_path), lead_layout="active_archive").lead_layout == "active_archive"


def test_the_vault_refuses_an_unknown_layout_at_construction(tmp_path):
    """Fail loudly at CONSTRUCTION, listing the valid names. Matched on the MESSAGE: the
    constructor can raise ValueError for other reasons, so `pytest.raises(ValueError)` alone
    would pass with the validation deleted."""
    with pytest.raises(ValueError, match="active_archive"):
        Vault(str(tmp_path), lead_layout="activearchive")


def test_the_store_factory_passes_the_configured_layout(tmp_path, monkeypatch):
    """The knob is inert unless the FACTORY carries it. Enumerating the dataclass field and the
    constructor parameter separately would leave the wire between them untested -- which is
    exactly how a config key ships dead."""
    from sluice.stores.vault import _make
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    assert _make(cfg).lead_layout == "active_archive"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lead_layout_config.py -v`
Expected: FAIL — `TypeError: Config.__init__() got an unexpected keyword argument 'lead_layout'`

- [ ] **Step 3: Add the field to `sluice/core/config.py`**

Immediately after `lead_ttl_days: int = 0`:

```python
    # Which named folder layout the lead store files notes into (#1). "" = flat, exactly as
    # before this existed; "active_archive" = Active/ + Archive/. Lives on the ROOT Config for
    # the same reason `location_noise_words` does: `Sluice.store()` resolves the store from
    # `self.config`, so a key the STORE must honour cannot sit in a sub-app block. OFF by
    # default -- sluice does not own the layout, it offers one, and an unconfigured install
    # must be byte-identical to the flat store. Validated in `Vault.__init__` rather than here:
    # the failure is an unknown NAME, which is `_select_backend`'s shape, so it is checked where
    # the name is resolved -- and that also covers the ~150 direct `Vault(...)` constructions
    # a loader-only check would miss.
    lead_layout: str = ""
```

- [ ] **Step 3a: Validate in `load_config` TOO — both checks, not either/or**

The constructor check (Step 4) and a loader check are **complementary, not alternatives**, and the
first draft of this plan treated them as an either/or. A loader-only check is an equivalent mutant
for every one of the ~150 direct `Vault(...)` constructions; a constructor-only check gives a raw
`ValueError` traceback out of `args.func` where the sibling knob `lead_ttl_days` gives a clean
`sluice: ...` usage error with rc 2 (`core/config.py:223` + `cli.py:993-1000`).

In `load_config`, beside the `lead_ttl_days` validator:

```python
    # Validated HERE as well as in Vault.__init__, and the two are not redundant. A loader-only
    # check is an equivalent mutant for every direct `Vault(...)` construction (the suite has
    # ~150); a constructor-only check lets a typo in the YAML reach the user as an uncaught
    # traceback, where `lead_ttl_days` above renders a usage error and exits 2. Same knob shape,
    # same failure surface.
    raw_layout = data.get("lead_layout")
    if raw_layout is not None and raw_layout not in LEAD_LAYOUTS:
        raise ValueError(
            f"lead_layout must be one of {', '.join(repr(n) for n in LEAD_LAYOUTS)}, "
            f"got {raw_layout!r}")
```

…and in the `Config(...)` call, beside `lead_ttl_days=raw_ttl,`:

```python
                  lead_layout=raw_layout or "",
```

Import `LEAD_LAYOUTS` from `sluice.core.leads` at the top of `core/config.py`.

Add to `tests/test_lead_layout_config.py`:

```python
def test_load_config_rejects_an_unknown_layout(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: activearchive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_layout"):
        load_config(None)


def test_main_renders_an_unknown_layout_as_a_usage_error(tmp_path, monkeypatch, capsys):
    """rc 2 and a sentence, not a traceback -- what `lead_ttl_days` already does. Asserted
    through `main`, because that is the only place the ValueError is turned into an exit code."""
    from sluice.cli import main
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: activearchive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert main(["leads", "reconcile"]) == 2
    assert "lead_layout" in capsys.readouterr().err
```

- [ ] **Step 4: Wire the factory and validate in the constructor**

`sluice/stores/vault.py`, in `_make`'s return:

```python
    return Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None,
                 baseline_rel=config.baseline_rel,
                 location_noise_words=config.location_noise_words,
                 lead_layout=config.lead_layout)
```

`sluice/core/vault.py`, add `lead_layout: str = ""` to `Vault.__init__`'s keyword-only parameters and,
after `self._noise = ...`:

```python
        # #1. Validated HERE, at construction, and by calling the pure map rather than
        # re-testing membership: a second copy of the "is this a known layout" check is a second
        # thing to keep in sync, which is the #30 failure mode. `layout_subfolder` raises and
        # lists the valid names for an unknown one, so a typo'd `lead_layout: activearchive`
        # cannot degrade silently to flat and leave a user believing their vault is being filed.
        # The probe status is "new" because that is what a created note carries (see _render),
        # so this is the same call `_write_folder` makes -- if it raises there it raises here,
        # at the earliest possible moment, on every command that builds a store.
        layout_subfolder("new", lead_layout)
        self.lead_layout = lead_layout
```

Add `layout_subfolder` to the existing `from sluice.core.leads import ...` line.

- [ ] **Step 5: Document it COMMENTED in `sluice.yaml.example`**

Insert after the `lead_ttl_days: 90` line (~line 21):

```yaml
# How the lead store FILES your notes on disk (#1). "" (the shipped default) is flat: every
# note in one directory, exactly as before this existed. "active_archive" splits them into
# Active/ and Archive/ -- Archive holds dismissed leads and every finished application
# (rejected, accepted, withdrawn) -- and NOTHING moves on its own: `sluice leads reconcile`
# reports the drift and only `--apply` files anything. The scan is recursive either way, so
# subfolders you make yourself are read as leads and are never relocated. Commented out
# rather than set, for the same reason as `lead_ttl_days` above: this file is COPIED, and an
# active value would hand every copier a filing decision they never made.
# lead_layout: active_archive   # <- uncomment to opt in
```

- [ ] **Step 5b: Move the three ABSTAIN guards into the neutrality file**

`test_lead_layout_dataclass_default_is_flat`, `test_lead_layout_loader_default_is_flat` and
`test_the_example_file_ships_lead_layout_commented` belong in
**`tests/test_sluice_neutral_defaults.py`**, not the feature file. That file is the one the docs and
the review agents point at as THE neutrality guard, and it is where both other sweep-invisible knobs
already live — the `#9: lead staleness` block for `lead_ttl_days` and the `#80` block for the root
path keys — each under its own banner. Its own comment records the failure of guarding a preference
somewhere reviewers do not look. How a user organises their job hunt is exactly such a preference.

Add them under a new banner, carrying the same one-paragraph note the `#9`/`#80` blocks carry:

```python
# ── #1: the lead layout ──────────────────────────────────────────────────────
# `lead_layout` needs its OWN guards for the same reason `lead_ttl_days` does, one type along.
# The #26/#63 sweep below is value-keyed on LIST-defaulting fields
# (`isinstance(getattr(cls(), f.name), list)`), so a `str` field is invisible to it, and
# `test_path_keys_dataclass_defaults_are_blank` derives only fields ending `_dir`. Measured:
# adding `lead_layout: str = "active_archive"` to Config leaves this entire file green.
```

Keep the constructor-validation and factory-wire tests in `tests/test_lead_layout_config.py` — those
are feature mechanics, not neutrality.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lead_layout_config.py tests/test_sluice_neutral_defaults.py -v`
Expected: all PASS

- [ ] **Step 7: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+10** (eight in `tests/test_lead_layout_config.py`, minus the three moved
to `tests/test_sluice_neutral_defaults.py`, which still count, plus the two `load_config`-validation
tests from Step 3a), ruff clean. No pre-existing test may change.

- [ ] **Step 8: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Change `Config.lead_layout` default to `"active_archive"` | `test_lead_layout_config.py::test_lead_layout_dataclass_default_is_flat` |
| Delete `lead_layout=...` from `load_config`'s `Config(...)` call | `test_lead_layout_config.py::test_lead_layout_round_trips_through_load_config` |
| Delete `lead_layout=config.lead_layout` from `stores/vault.py:_make` | `test_lead_layout_config.py::test_the_store_factory_passes_the_configured_layout` |
| Delete the `layout_subfolder("new", lead_layout)` line from `Vault.__init__` | `test_lead_layout_config.py::test_the_vault_refuses_an_unknown_layout_at_construction` |
| Uncomment `lead_layout: active_archive` in `sluice.yaml.example` | `test_lead_layout_config.py::test_the_example_file_ships_lead_layout_commented` |

Also confirm the #26/#63 sweep is genuinely blind to this field (this is the *reason* the file
exists, so it must be checked, not asserted): temporarily set the default to `"active_archive"` and
run `.venv/bin/python -m pytest tests/test_sluice_neutral_defaults.py` — expected **all green**,
proving the named guard above is load-bearing. Restore.

- [ ] **Step 9: Commit**

```bash
git add sluice/core/config.py sluice/core/vault.py sluice/stores/vault.py \
        sluice.yaml.example tests/test_lead_layout_config.py
git commit -m "$(cat <<'EOF'
feat(config): add the lead_layout knob, off by default (#1)

Root Config field (`Sluice.store()` resolves the store from self.config, so a key
the store must honour cannot live in a sub-app block), threaded through
stores/vault.py:_make into Vault. "" is the default, so an unconfigured install is
byte-identical to the flat store. Validated in the CONSTRUCTOR by calling the pure
map -- one definition of "is this a known layout" -- so a typo raises and lists the
valid names instead of degrading to flat, and the ~150 direct Vault(...)
constructions are covered too.

Documented COMMENTED in sluice.yaml.example: that file is copied, and an active
value would hand every copier a filing decision they never made. Carries its own
guards because the #26/#63 neutral-defaults sweep is list-keyed and blind to a str
field -- measured, not assumed.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 3: The write folder — new notes land in `Active/`

PR A left the marker: `sluice/core/vault.py:850` reads *"The write folder is still leads_dir itself.
PR B is what points a create at Active/."* This task is that.

**Files:**
- Modify: `sluice/core/vault.py` (add `_write_folder`; `_resolve_candidates`' create arm ~`:850`;
  `upsert`'s makedirs `:1456`)
- Modify: `tests/test_vault_makedirs_scope.py` (`_EXPECTED` ~`:33-42`)
- Test: `tests/test_vault_write_folder.py` (create)

**Interfaces:**
- Consumes: `self.lead_layout` (Task 2), `layout_subfolder` (Task 1).
- Produces: `Vault._write_folder() -> str` — the one directory a create writes into.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_write_folder.py`:

```python
"""The SCAN SET and the WRITE FOLDER are two concepts one field used to conflate. The scan set
is every directory a lead may be READ from; the write folder is the ONE directory a new note is
CREATED in. Separating them is the whole of the layout design."""
import os

from sluice.core.leads import ACTIVE_SUBDIR, Lead
from sluice.core.vault import Vault

from tests.conftest import LOCATIONS


# `search` is REQUIRED (core/leads.py:142 -- source, search, title). The shipped helper form is
# tests/test_leads_expire.py:23; omitting `search` is a TypeError before any assertion runs.
def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1",
          location=""):
    return Lead(source="test", search="q", title=title, company=company, url=url,
                location=location)


def test_the_flat_layout_writes_into_the_leads_dir(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    assert os.path.isfile(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"))


def test_the_active_archive_layout_writes_into_active(tmp_path):
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    assert os.path.isfile(
        os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md"))
    assert not os.path.exists(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"))


def test_a_created_note_is_already_reconciled(tmp_path):
    """`_write_folder` resolves through `layout_subfolder("new", ...)` rather than naming
    Active/ directly, so a created note is BY CONSTRUCTION already in the folder its status
    implies -- `leads reconcile` has nothing to do with a note ingest just made. A hardcoded
    Active/ would drift the moment the map changed."""
    from sluice.core.leads import layout_subfolder
    v = Vault(str(tmp_path), lead_layout="active_archive")
    v.upsert(_lead())
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    note = [n for n in v.read_leads() if n.ref == ref][0]
    assert os.path.basename(os.path.dirname(note.ref)) == layout_subfolder(
        note.status, "active_archive")


def test_a_rescrape_updates_the_note_in_active_rather_than_recreating_it(tmp_path):
    """The identity rule: a lead's identity is its note NAME, not its folder. A second scrape
    must find the note in Active/ through the scan set and bump last_seen, not mint a twin at
    the root. This is the regression that would mass-duplicate an opted-in vault."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    assert v.upsert(_lead()) == "updated"
    found = [p for d, _, fs in os.walk(v.leads_dir) for p in fs if p.endswith(".md")]
    assert len(found) == 1, found


def test_a_note_already_at_the_root_is_updated_not_duplicated_into_active(tmp_path):
    """Opting IN on an existing flat vault must not re-create every lead. The scan set covers
    the root, so the candidate resolves there and the note is updated where it sits; moving it
    is `leads reconcile`'s job, not ingest's (decision 2)."""
    flat = Vault(str(tmp_path))
    assert flat.upsert(_lead()) == "created"
    opted_in = Vault(str(tmp_path), lead_layout="active_archive")
    assert opted_in.upsert(_lead()) == "updated"
    assert os.path.isfile(os.path.join(flat.leads_dir, "Example Ltd - Example Role.md"))
    assert not os.path.exists(os.path.join(flat.leads_dir, ACTIVE_SUBDIR))


def test_a_refused_lead_creates_no_write_folder(tmp_path):
    """A lead that writes NOTHING must not leave an empty Active/ behind -- the makedirs sits
    after the refusal check for exactly this reason, and pointing it at a new directory is a
    fresh chance to get that order wrong."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    os.makedirs(v.leads_dir, exist_ok=True)
    # Seat a note at every candidate name that is PROVEN DIFFERENT, so the walk exhausts.
    # BOTH sides need a non-empty location: `same_opportunity` reaches DIFFERENT only through
    # `_compare_locations`, and an EMPTY incoming location is UNKNOWN, which terminates the walk
    # at the first candidate with `merge` and never exhausts it. LOCATIONS' members are
    # token-disjoint by construction, so any two read DIFFERENT.
    name = "Example Ltd - Example Role"
    with open(os.path.join(v.leads_dir, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ncompany: Example Ltd\nrole: Example Role\n"
                 f"url: https://example.invalid/other\nlocation: {LOCATIONS[0]}\n---\nbody\n")
    outcome = v.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[1]))
    # Assert the PRECONDITION separately from the property. If this fixture stops reaching
    # `refused` -- a change to `same_opportunity`, to the candidate walk, to _compare_locations
    # -- this line says so, instead of the test quietly passing because nothing was created for
    # some entirely different reason.
    assert outcome == "refused", f"fixture did not reach the refusal arm: {outcome}"
    assert not os.path.exists(os.path.join(v.leads_dir, ACTIVE_SUBDIR))
```

> **Note for the implementer:** the `assert outcome == "refused"` line is a PRECONDITION check, not
> the property under test. The fixture above is the corrected one — the first draft gave the
> incoming lead `location=""`, which is UNKNOWN, so the walk terminated at the first candidate with
> `merge` and the test failed with `fixture did not reach the refusal arm: merged`. **Do not weaken
> that line to a membership test to make it pass**: a probe that fails to construct its precondition
> looks identical to a probe that disproves the claim, and a loose assertion is what hides the
> difference.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_write_folder.py -v`
Expected: FAIL — `test_the_active_archive_layout_writes_into_active` asserts the note is in
`Active/`; it is at the root.

- [ ] **Step 3: Add `_write_folder` to `Vault`**

Insert immediately after `_scan_dirs` / `_rescan_dirs` (the scan-set block), so the two concepts
sit side by side:

```python
    def _write_folder(self) -> str:
        """The ONE directory a NEW note is created in -- as opposed to `_scan_dirs`, every
        directory a note may be READ from. One field used to be both; separating them is the
        whole of the #1 layout design.

        Resolved through `layout_subfolder` at the status a created note actually carries
        ("new" -- see the rendered frontmatter in `upsert`), never by naming Active/ here. Two
        things follow, and both are the point. A created note is BY CONSTRUCTION already in the
        folder its status implies, so `leads reconcile` has nothing to do with a note ingest
        just made. And there is one definition of the status->folder map, so a change to it
        cannot leave the write folder pointing somewhere reconcile immediately moves the note
        out of -- which would relocate every freshly-ingested lead on the next pass.

        Under the flat default this returns `self.leads_dir` unchanged, so an unconfigured
        store is byte-identical to the pre-#1 one. It is a METHOD rather than a cached
        attribute because it is called once per create, which is bounded by the run's lead
        count, and a cached path is one more thing that can disagree with `self.lead_layout`.
        """
        sub = layout_subfolder("new", self.lead_layout)
        # `sub` is never None here: "new" is canonical, and the layout name was validated at
        # construction. Guarding it anyway would be an unreachable branch wearing a comment
        # claiming it fires -- the shape `track/receipt.py` deleted.
        return os.path.join(self.leads_dir, sub) if sub else self.leads_dir
```

- [ ] **Step 4: Point the create arm and the makedirs at it**

In `_resolve_candidates` (~`:850`), replace the create-arm return and its stale marker comment:

```python
                # The write folder, NOT leads_dir: under `active_archive` a create lands in
                # Active/, which is where a `status: new` note belongs, so the note is already
                # reconciled the moment it exists. `_locate` searched the whole SCAN SET above,
                # so a note the user (or a previous flat install) left at the root was already
                # found and updated in place -- opting in never re-creates an existing lead.
                return os.path.join(self._write_folder(), f"{name}.md"), "create", True
```

In `upsert`, **leave `os.makedirs(self.leads_dir, exist_ok=True)` at `:1456` exactly where it is** and
add a second makedirs *inside the create arm only*, immediately before the exclusive write:

```python
            try:
                # The WRITE FOLDER, made HERE and not beside the leads_dir makedirs above, which
                # sits ABOVE the update/merge/create fan-out and therefore runs on every
                # non-refused outcome. Measured: a second upsert of the same lead reaches that
                # line and returns "updated" -- so repointing it would mint an empty Active/ in
                # the user's Obsidian vault on a pure last_seen bump, for a lead that already
                # exists at the root. Only a CREATE needs the write folder to exist.
                #
                # The leads_dir makedirs stays because update and merge legitimately need the
                # directory (and the Syncthing marker beside it); it is still after the refusal
                # check, so a lead that writes nothing leaves the filesystem untouched.
                os.makedirs(self._write_folder(), exist_ok=True)
                # The SAME string the blank-note guard above ran the read's predicate over --
                # re-rendering here would put a second, unchecked set of bytes on disk.
                _write(path, rendered, exclusive=True)
                return "created"
```

(The existing `except FileExistsError: continue` arm below it is unchanged.)

- [ ] **Step 5: Classify the new makedirs target in the scope guard**

`tests/test_vault_makedirs_scope.py`, in `_EXPECTED`, **KEEP the existing `"self.leads_dir"` entry**
(that call still exists, on the update/merge path) and ADD one. The expression string is what the AST
sweep unparses, so it must match exactly:

```python
    # The lead WRITE FOLDER -- leads_dir under the flat default, leads_dir/Active under
    # `active_archive`. Made only on the CREATE arm (see upsert). Scanned, being inside the scan
    # set, so it must NOT be in _PRIVATE_SUBDIRS: pruning it would hide every lead sluice itself
    # creates from read_leads AND from _locate, re-creating all of them on the next scrape.
    "self._write_folder()": "the write folder (create arm only)",
```

Also update the module docstring's count: it says *"vault.py creates directories only through the
**four** os.makedirs sites classified below"*, and this PR makes it **six** (Task 5 adds reconcile's).
This is a repo where a comment stating a mechanism needs a row that falsifies it — reword it to name
the classification set rather than a number that will drift again.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_write_folder.py tests/test_vault_makedirs_scope.py -v`
Expected: all PASS

- [ ] **Step 7: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+6**, ruff clean. The ~150 existing `Vault(str(tmp_path))`
constructions default to flat, so **no pre-existing test may change**. If one does, the flat path
is not byte-identical and that is a defect, not a fixture to update.

- [ ] **Step 8: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Revert the create arm to `os.path.join(self.leads_dir, f"{name}.md")` | `test_vault_write_folder.py::test_the_active_archive_layout_writes_into_active` |
| DELETE the create-arm `os.makedirs(self._write_folder(), ...)` | `test_vault_write_folder.py::test_the_active_archive_layout_writes_into_active` (create raises FileNotFoundError) |
| **HOIST** the create-arm makedirs up beside `os.makedirs(self.leads_dir, ...)`, above the update/merge dispatch | `test_vault_write_folder.py::test_a_note_already_at_the_root_is_updated_not_duplicated_into_active` |
| Hardcode `_write_folder` to `os.path.join(self.leads_dir, "Active")` (dropping the flat arm) | `test_vault_write_folder.py::test_the_flat_layout_writes_into_the_leads_dir` |
| Move the create-arm makedirs ABOVE the `if action == "refuse"` block | `test_vault_write_folder.py::test_a_refused_lead_creates_no_write_folder` |
| Delete the `"self._write_folder()"` entry from `_EXPECTED` | `test_vault_makedirs_scope.py::test_every_makedirs_call_is_classified` |

The HOIST row is the one this task's first draft got wrong: it proposed *replacing* the leads_dir
makedirs, which sits above the fan-out and therefore runs on update and merge too — so opting in
would have minted an empty `Active/` on every `last_seen` bump. Measured red against
`test_a_note_already_at_the_root_is_updated_not_duplicated_into_active`.

- [ ] **Step 9: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_write_folder.py tests/test_vault_makedirs_scope.py
git commit -m "$(cat <<'EOF'
feat(vault): point creates at the write folder, not the leads dir (#1)

`_write_folder()` separates the two concepts one field conflated: the SCAN SET is
every directory a note may be read from, the WRITE FOLDER is the one directory a
create writes into. It resolves through layout_subfolder at "new" -- the status a
created note carries -- rather than naming Active/ directly, so a created note is
by construction already in the folder its status implies and `leads reconcile` has
nothing to do with it.

Opting in on an existing flat vault re-creates nothing: `_locate` already searched
the whole scan set, so a note at the root is found and updated where it sits.
Moving it is reconcile's job (decision 2). The makedirs stays AFTER the refusal
check, so a lead that writes nothing leaves no empty Active/ behind.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 4: One atomic note-move primitive

`merge_cluster` already ships the primitive that survived two CodeRabbit rounds: `O_EXCL`-reserve the
destination, then `os.replace`. Reconcile needs the same move with a different **collision policy** —
a numeric suffix changes the filename, which is the slug, which is the identity, so reconcile must
refuse where merge_cluster suffixes. Extract it once rather than copying it.

**Files:**
- Modify: `sluice/core/vault.py` (extract from `merge_cluster` `:1604-1641`; add the helper beside
  `_write`/`_atomic_write` ~`:1657`)
- Test: `tests/test_vault_atomic_move.py` (create)

**Interfaces:**
- Produces: module-level
  `_reserve_and_move(src: str, dest_dir: str, base: str, *, suffix_on_collision: bool) -> str` —
  returns the destination path actually used. Raises `FileExistsError` when the destination is taken
  and `suffix_on_collision` is False; raises the underlying `OSError` on any other failure, having
  first removed its own orphaned reservation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_atomic_move.py`:

```python
"""The ONE atomic note-move. Two callers, two collision policies, one definition.

os.replace alone is atomic but OVERWRITES the destination; os.link + os.unlink never overwrites
but has a window where a concurrent atomic save of the source is DELETED rather than moved.
The shape that satisfies both is O_EXCL-reserve then os.replace: the reserve is atomic (a
concurrent archiver loses it, it does not race), and the replace moves whatever `src` names AT
THAT INSTANT, overwriting only our own zero-byte reservation."""
import os

import pytest

from sluice.core.vault import _reserve_and_move


def _note(path, text="---\ncompany: Example Ltd\n---\nbody\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_it_moves_the_file_and_returns_the_destination(tmp_path):
    src = _note(str(tmp_path / "from" / "N.md"), "PAYLOAD")
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)
    got = _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert got == os.path.join(dest_dir, "N.md")
    assert not os.path.exists(src)
    assert open(got, encoding="utf-8").read() == "PAYLOAD"


def test_a_collision_raises_when_suffixing_is_off(tmp_path):
    """Reconcile's policy. A numeric suffix changes the FILENAME, which is the slug, which is
    the IDENTITY -- a renamed note is no longer any candidate `_resolve_path` walks, so the next
    scrape mints a fresh note and orphans the renamed one. Refusing is the only safe answer."""
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    with pytest.raises(FileExistsError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert open(src, encoding="utf-8").read() == "MINE", "the source must be untouched"
    assert open(os.path.join(dest_dir, "N.md"), encoding="utf-8").read() == "THEIRS"


def test_a_refused_collision_leaves_no_reservation_behind(tmp_path):
    """The refusal never RESERVED anything, so it must not unlink anything either -- an
    over-eager cleanup here would delete the colliding note, which is the file we refused in
    order to protect."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    with pytest.raises(FileExistsError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert sorted(os.listdir(dest_dir)) == ["N.md"]


def test_a_collision_takes_the_next_suffix_when_suffixing_is_on(tmp_path):
    """merge_cluster's policy, unchanged: an archived loser's filename is not an identity the
    write path walks, so a suffix there costs nothing and losing an archive would cost #81."""
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    _note(os.path.join(dest_dir, "N.md"), "THEIRS")
    got = _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=True)
    assert got == os.path.join(dest_dir, "N.1.md")
    assert open(got, encoding="utf-8").read() == "MINE"
    assert open(os.path.join(dest_dir, "N.md"), encoding="utf-8").read() == "THEIRS"


def test_suffixing_walks_past_several_taken_names(tmp_path):
    src = _note(str(tmp_path / "from" / "N.md"), "MINE")
    dest_dir = str(tmp_path / "to")
    for taken in ("N.md", "N.1.md", "N.2.md"):
        _note(os.path.join(dest_dir, taken), "THEIRS")
    assert _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=True) == \
        os.path.join(dest_dir, "N.3.md")


def test_a_failed_move_removes_its_own_reservation(tmp_path, monkeypatch):
    """The reservation is a real zero-byte file. If the replace then fails (disk full,
    permissions, a source deleted under us), leaving it behind seats a zero-byte note at a real
    lead's name -- which `_locate` finds, `_is_note_file` calls a note, and `_resolve_path`
    reconciles against. Ownership is proved by OUR open succeeding, never by os.path.exists."""
    src = _note(str(tmp_path / "from" / "N.md"))
    dest_dir = str(tmp_path / "to")
    os.makedirs(dest_dir)

    def boom(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _reserve_and_move(src, dest_dir, "N.md", suffix_on_collision=False)
    assert os.listdir(dest_dir) == [], "the reservation was left behind"
    assert os.path.exists(src), "the source must survive a failed move"


def test_merge_cluster_still_archives_through_the_shared_primitive(tmp_path):
    """The extraction must not change merge_cluster. Driven through the PUBLIC method, so this
    fails if the refactor lost the suffix policy, the stamp, or the per-loser isolation."""
    from sluice.core.vault import Vault
    v = Vault(str(tmp_path))
    os.makedirs(v.leads_dir, exist_ok=True)
    fm = "---\ncompany: Example Ltd\nrole: Example Role\nstatus: new\nurl: \n---\nbody\n"
    survivor = _note(os.path.join(v.leads_dir, "Example Ltd - Example Role.md"), fm)
    loser = _note(os.path.join(v.leads_dir, "Example Ltd - Example Role 2.md"), fm)
    archived = v.merge_cluster(survivor, [loser], alt_urls=[], first_seen="", last_seen="")
    assert len(archived) == 1
    assert os.path.dirname(archived[0]).endswith("_merged")
    assert not os.path.exists(loser)
    assert "archived_from_note" in open(archived[0], encoding="utf-8").read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_atomic_move.py -v`
Expected: FAIL — `ImportError: cannot import name '_reserve_and_move' from 'sluice.core.vault'`

- [ ] **Step 3: Extract the helper**

Add beside `_write` / `_atomic_write` (module level, ~`:1657`):

```python
def _reserve_and_move(src: str, dest_dir: str, base: str, *,
                      suffix_on_collision: bool) -> str:
    """Atomically move the note at `src` into `dest_dir` under the name `base`. Returns the
    destination path actually used.

    The primitive, in one place, because two callers need it with different collision policies
    and a second copy is a second thing to keep correct. `os.replace(src, dest)` alone is a
    single atomic move but OVERWRITES `dest`; `os.link(src, dest) + os.unlink(src)` never
    overwrites but has a window in which a concurrent atomic save of `src` -- a human hitting
    save in Obsidian -- lands between the two and is DELETED rather than moved. CodeRabbit
    flagged each in turn on #23. The shape that satisfies both: reserve `dest` with
    O_CREAT|O_EXCL (atomic, so a concurrent reserver loses rather than races), then
    `os.replace` whatever `src` names AT THAT INSTANT into it -- so a concurrent save is
    carried, and the only thing overwritten is our own zero-byte reservation.

    COLLISION POLICY is the caller's, and the two are not interchangeable:

    - `suffix_on_collision=True` (merge_cluster) takes `<stem>.<n>.md`. An archived loser's
      filename is not an identity the write path walks, so a suffix costs nothing there, while
      failing to archive would leave the loser active and undo #81.
    - `suffix_on_collision=False` (leads reconcile) raises FileExistsError. A suffix changes the
      FILENAME, which is the slug, which is the IDENTITY: the renamed note matches no candidate
      `_resolve_path` walks, so the next scrape mints a fresh note and orphans the renamed one.
      Refusing that note and reporting it is the only safe answer.

    On any OSError the reservation this function created is removed before the error
    propagates, so a failed move never seats a zero-byte file at a real lead's name -- which
    `_is_note_file` would call a note and `_resolve_path` would reconcile against. Ownership is
    proved by OUR open having returned a handle, never by `os.path.exists`: a concurrent writer
    landing a file in the window makes the path exist without us owning it, and unlinking it
    then is a clobber inside a clobber-fix (#16).

    The REFUSAL path reserved nothing, so it cleans up nothing -- unlinking there would delete
    the very note the refusal exists to protect.
    """
    stem = base[:-3] if base.endswith(".md") else base
    dest = os.path.join(dest_dir, base)
    n = 1
    reserved = None
    try:
        while True:
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                reserved = dest
                break
            except FileExistsError:
                if not suffix_on_collision:
                    raise            # nothing reserved -> nothing to clean up
                dest = os.path.join(dest_dir, f"{stem}.{n}.md")
                n += 1
        os.replace(src, dest)        # atomic; overwrites only our own 0-byte reservation
        return dest
    except OSError:
        if reserved:
            try:
                os.unlink(reserved)
            except OSError:
                pass
        raise
```

- [ ] **Step 4: Rewrite `merge_cluster`'s loop to call it**

Replace `merge_cluster`'s inline reserve/replace block (`:1604-1641`) with:

```python
        for ref in loser_refs:
            base = os.path.basename(ref)
            stem = base[:-3] if base.endswith(".md") else base
            try:
                # suffix_on_collision=True: an archived loser's filename is not an identity the
                # write path walks, so a numeric suffix costs nothing -- while failing to
                # archive would leave the loser active and undo #81. See _reserve_and_move.
                dest = _reserve_and_move(ref, merged_dir, base, suffix_on_collision=True)
            except OSError as e:
                # per-loser isolation: leave the loser active (it self-heals next run). The
                # helper has already removed any reservation it created. `continue`, so this
                # loser is neither counted nor stamped.
                _log.warning("dedupe: could not archive loser %s: %s", ref, e)
                continue
            archived.append(dest)
            # #81: record the name this loser was seated at, AFTER it is counted. `stem` is
            # that name, and the helper derives its suffixed candidates from the same string,
            # so the probe's filename pre-filter and the value it reads cannot disagree.
            # Stamping BEFORE the move would put the key on an ACTIVE note and leave it there
            # if the move then failed.
            _stamp_archived_from(dest, stem)
        return archived
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_atomic_move.py -v`
Expected: all PASS

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+7**, ruff clean. **Every pre-existing `merge_cluster` and `#81` test
must still pass unchanged** — this is a refactor, and `tests/test_vault_archived_probe.py`,
`tests/test_leads_dedupe*.py` and `tests/conformance/test_store_contract.py` are the ones that say so.

- [ ] **Step 7: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Delete `if not suffix_on_collision: raise` (always suffix) | `test_vault_atomic_move.py::test_a_collision_raises_when_suffixing_is_off` |
| Delete the `if reserved: os.unlink(reserved)` cleanup | `test_vault_atomic_move.py::test_a_failed_move_removes_its_own_reservation` |
| Move the cleanup so it also runs on the refusal path (`reserved = dest` before the policy check) | `test_vault_atomic_move.py::test_a_refused_collision_leaves_no_reservation_behind` |
| Replace the reserve+replace pair with a bare `os.replace(src, dest)` | `test_vault_atomic_move.py::test_a_collision_raises_when_suffixing_is_off` |
| Delete `_stamp_archived_from(dest, stem)` from `merge_cluster` | `test_vault_atomic_move.py::test_merge_cluster_still_archives_through_the_shared_primitive` |

- [ ] **Step 8: Watch item — CodeQL**

`#16`'s lesson: *a NEW write function makes CodeQL re-flag long-standing behaviour as a new sink.*
This extraction moves file-creating code into a new module-level function. It writes no lead
CONTENT (the `O_EXCL` open is closed immediately with zero bytes; `os.replace` moves an inode), so
`py/clear-text-storage-sensitive-data` should not fire. **If CI's CodeQL job flags it, do not
dismiss the alert** — resolve it the way #16 did, by folding the behaviour into an existing helper
rather than adding a second one. Note the outcome in the PR body either way.

- [ ] **Step 9: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_atomic_move.py
git commit -m "$(cat <<'EOF'
refactor(vault): extract the one atomic note-move primitive (#1)

`_reserve_and_move` is merge_cluster's O_EXCL-reserve + os.replace, lifted so
`leads reconcile` uses the same move rather than a copy of it. The collision policy
is the parameter, because the two callers genuinely differ: merge_cluster suffixes
(an archived loser's filename is not an identity the write path walks, and failing
to archive would undo #81), while reconcile must REFUSE (a suffix changes the
filename, which is the slug, which is the identity -- the renamed note matches no
candidate and the next scrape orphans it).

The refusal path reserved nothing and therefore cleans up nothing; unlinking there
would delete the note the refusal exists to protect. Any other OSError removes the
reservation this function created before propagating, so a failed move never seats
a zero-byte file at a real lead's name.

merge_cluster's behaviour is unchanged, driven through the public method by a test
that would fail if the refactor lost the suffix policy, the #81 stamp, or the
per-loser isolation.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 5: `Vault.reconcile_layout()` — the sweep

Report by default, move on `apply=True`. Managed folders only (decision 6). Ambiguous slugs refuse
(decision 5). Non-canonical statuses are never moved.

**Files:**
- Modify: `sluice/core/vault.py` (add `reconcile_layout` + `_managed_dirs` after `normalize_all_statuses`)
- Modify: `tests/test_vault_makedirs_scope.py` (`_EXPECTED`)
- Test: `tests/test_leads_reconcile.py` (create)

**Interfaces:**
- Consumes: `layout_subfolder`, `ACTIVE_SUBDIR`, `ARCHIVE_SUBDIR` (Task 1); `self.lead_layout`,
  `_write_folder` (Tasks 2–3); `_reserve_and_move` (Task 4); `read_leads`, `_rescan_dirs` (shipped).
- Produces: `Vault.reconcile_layout(*, apply: bool = False) -> dict` with keys:
  - `"layout"`: `str` — the configured layout name
  - `"moves"`: `list[tuple[str, str, str]]` — `(slug, src_rel, dst_rel)`, planned when
    `apply=False`, **completed** when `apply=True`
  - `"in_place"`: `int` — leads already in the right folder
  - `"ambiguous"`: `dict[str, list[str]]` — slug → sorted refs, never moved (decision 5)
  - `"unknown"`: `list[tuple[str, str]]` — `(slug, raw_status)`, non-canonical, never moved
  - `"user_filed"`: `list[tuple[str, str]]` — `(slug, dir_rel)`, outside the managed folders (decision 6)
  - `"collisions"`: `list[tuple[str, str]]` — `(slug, dst_rel)`, refused
  - `"skipped"`: `list[tuple[str, str]]` — `(slug, error)`, per-note `OSError` isolation

All paths in the report are **relative to `leads_dir`**, so nothing prints an absolute path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leads_reconcile.py`:

```python
"""`leads reconcile` -- the only pass that MOVES a lead note (decision 2).

Three refusals are load-bearing and each has a distinct reason:
  - ambiguous slug   -> the store already refuses that identity everywhere; moving a twin
                        picks one, which is what every other consumer declines to do
  - user-filed note  -> decision 4 says everything under leads_dir that is not sluice's is the
                        user's; that must hold for WRITES as well as for reads
  - non-canonical    -> never-regress passes an unrecognized status through untouched
"""
import os

import pytest

from sluice.core.leads import ACTIVE_SUBDIR, ARCHIVE_SUBDIR
from sluice.core.vault import Vault


def _seed(vault, rel, *, company="Example Ltd", role="Example Role", status="new"):
    path = os.path.join(vault.leads_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\ncompany: {company}\nrole: {role}\nstatus: {status}\n"
                 f"url: \nlast_seen: 2026-01-01\n---\nbody\n")
    return path


def _v(tmp_path, layout="active_archive"):
    v = Vault(str(tmp_path), lead_layout=layout)
    os.makedirs(v.leads_dir, exist_ok=True)
    return v


def test_the_report_moves_nothing(tmp_path):
    """Report-first, like `leads dedupe` and `leads expire`. The default IS the dry run, which
    is why there is no --dry-run flag to be inert."""
    v = _v(tmp_path)
    src = _seed(v, "A - Live.md", role="Live", status="shortlist")
    rep = v.reconcile_layout()
    assert rep["moves"] == [("A - Live", "A - Live.md",
                             os.path.join(ACTIVE_SUBDIR, "A - Live.md"))]
    assert os.path.isfile(src), "the report wrote something"
    assert not os.path.exists(os.path.join(v.leads_dir, ACTIVE_SUBDIR))


def test_apply_files_a_live_lead_into_active(tmp_path):
    v = _v(tmp_path)
    _seed(v, "A - Live.md", role="Live", status="shortlist")
    rep = v.reconcile_layout(apply=True)
    assert len(rep["moves"]) == 1
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Live.md"))
    assert not os.path.exists(os.path.join(v.leads_dir, "A - Live.md"))


@pytest.mark.parametrize("status", ["dismiss", "rejected", "accepted", "withdrawn"])
def test_apply_files_dismiss_and_every_terminal_into_archive(tmp_path, status):
    v = _v(tmp_path)
    _seed(v, "A - Done.md", role="Done", status=status)
    v.reconcile_layout(apply=True)
    assert os.path.isfile(os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Done.md"))


def test_a_note_already_in_place_is_counted_not_moved(tmp_path):
    """Idempotence. A second run must be a no-op -- the pass is run repeatedly and a move that
    re-fires would churn the vault (and, worse, re-report as work done)."""
    v = _v(tmp_path)
    _seed(v, "A - Live.md", role="Live", status="shortlist")
    v.reconcile_layout(apply=True)
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert rep["in_place"] == 1


def test_a_user_filed_lead_is_reported_and_left_alone(tmp_path):
    """Decision 6. A lead the user deliberately put in their own folder is NOT relocated: the
    scan reads it, reconcile reports it, and only a human moves it."""
    v = _v(tmp_path)
    src = _seed(v, os.path.join("Research", "A - Filed.md"), role="Filed", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert rep["user_filed"] == [("A - Filed", "Research")]
    assert os.path.isfile(src), "a user-filed note was relocated"
    # The root is MANAGED, so no user_filed entry can ever carry ".". Asserted rather than
    # left to be re-derived: the CLI renders this value as "<where>/ is yours, not sluice's",
    # which would read as "./ is yours" for a root-seated note -- an arm that must be
    # unreachable, not merely unlikely.
    assert all(where != "." for _, where in rep["user_filed"])


def test_a_note_in_a_managed_folder_is_moved_even_from_archive_back_to_active(tmp_path):
    """The map is a derived view in BOTH directions -- a lead reopened from `rejected` to
    `shortlist` must come back out of Archive/, or the archive silently becomes one-way."""
    v = _v(tmp_path)
    _seed(v, os.path.join(ARCHIVE_SUBDIR, "A - Back.md"), role="Back", status="shortlist")
    v.reconcile_layout(apply=True)
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Back.md"))


def test_a_non_canonical_status_is_reported_under_unknown_and_never_moved(tmp_path):
    """never-regress. normalize_all_statuses reports an unrecognized value rather than
    rewriting it; the layout must not decide a folder for one either."""
    v = _v(tmp_path)
    src = _seed(v, "A - Odd.md", role="Odd", status="some_future_state")
    rep = v.reconcile_layout(apply=True)
    assert rep["unknown"] == [("A - Odd", "some_future_state")]
    assert rep["moves"] == []
    assert os.path.isfile(src)


def test_two_notes_claiming_one_slug_are_refused_and_neither_moves(tmp_path):
    """Decision 5. Reconcile cannot repair this -- the slug IS the filename, a rename orphans
    the note from the candidate walk, and picking a survivor is `leads dedupe`'s job. So it
    refuses BOTH and names them, the shape index_by_slug/upsert/select_one already use."""
    v = _v(tmp_path)
    a = _seed(v, os.path.join(ACTIVE_SUBDIR, "A - Twin.md"), role="Twin", status="shortlist")
    b = _seed(v, os.path.join(ARCHIVE_SUBDIR, "A - Twin.md"), role="Twin", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == []
    assert list(rep["ambiguous"]) == ["A - Twin"]
    assert len(rep["ambiguous"]["A - Twin"]) == 2
    assert os.path.isfile(a) and os.path.isfile(b), "a twin was moved"


def test_a_destination_collision_refuses_that_note_and_continues_the_sweep(tmp_path):
    """Per-note isolation, and NEVER a numeric suffix: the filename is the slug, so a suffixed
    move changes the lead's identity and orphans it from the next scrape.

    The blocker is a NON-LEAD file, and that is the only way this arm is reachable. `_slug_for`
    is the basename, so a LEAD note blocking the destination shares the mover's slug and the
    `ambiguous` arm consumes both first -- the first draft of this test seeded exactly that and
    measured `collisions == []`, asserting an outcome its own fixture made unreachable. A file
    carrying neither company nor role is skipped by `read_leads`, so the mover keeps a unique
    slug and actually reaches the move."""
    v = _v(tmp_path)
    _seed(v, os.path.join(ACTIVE_SUBDIR, "A - Clash.md"), role="Clash", status="dismiss")
    blocker = os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Clash.md")
    os.makedirs(os.path.dirname(blocker), exist_ok=True)
    with open(blocker, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: prep\n---\nnot a lead\n")
    _seed(v, "B - Fine.md", company="B", role="Fine", status="shortlist")
    rep = v.reconcile_layout(apply=True)
    # PRECONDITION: if a future change re-routes the mover into the twins arm, say so loudly
    # rather than passing vacuously on an empty collisions list.
    assert rep["ambiguous"] == {}, "the fixture no longer reaches the collision arm"
    assert rep["collisions"] == [("A - Clash", os.path.join(ARCHIVE_SUBDIR, "A - Clash.md"))]
    assert open(blocker, encoding="utf-8").read() == "---\ntitle: prep\n---\nnot a lead\n"
    assert not os.path.exists(os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "A - Clash.1.md"))
    assert len(rep["moves"]) == 1, "the sweep stopped at the collision"


def test_a_move_oserror_is_isolated_and_the_sweep_continues(tmp_path, monkeypatch):
    v = _v(tmp_path)
    _seed(v, "A - Boom.md", role="Boom", status="shortlist")
    _seed(v, "B - Fine.md", company="B", role="Fine", status="shortlist")
    from sluice.core import vault as vaultmod
    real = vaultmod._reserve_and_move

    def flaky(src, dest_dir, base, **kw):
        if "Boom" in base:
            raise OSError(13, "Permission denied")
        return real(src, dest_dir, base, **kw)
    monkeypatch.setattr(vaultmod, "_reserve_and_move", flaky)
    rep = v.reconcile_layout(apply=True)
    assert [s for s, _ in rep["skipped"]] == ["A - Boom"]
    assert len(rep["moves"]) == 1


def test_a_flat_layout_reconciles_nothing(tmp_path):
    """Decision 7. Under the flat default there is no layout to reconcile against, and
    FLATTENING would drag every lead out of the user's own subfolders -- decision 4 pointed the
    wrong way. So the pass reports its layout and does nothing."""
    v = _v(tmp_path, layout="")
    src = _seed(v, os.path.join("Research", "A - Filed.md"), role="Filed", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["layout"] == ""
    assert rep["moves"] == [] and rep["user_filed"] == [] and rep["in_place"] == 0
    assert os.path.isfile(src)


def test_an_archived_loser_is_never_a_reconcile_source(tmp_path):
    """#81. `_merged/` is pruned from the scan set, so read_leads never returns an archived
    loser -- and reconcile must not reach one by any other route either, or a lead a human
    merged away returns to the active view."""
    v = _v(tmp_path)
    loser = _seed(v, os.path.join("_merged", "A - Gone.md"), role="Gone", status="dismiss")
    rep = v.reconcile_layout(apply=True)
    assert rep["moves"] == [] and rep["user_filed"] == []
    assert os.path.isfile(loser)
    assert not os.path.exists(os.path.join(v.leads_dir, ARCHIVE_SUBDIR))


def test_a_non_lead_file_is_never_moved(tmp_path):
    """A user's interview-prep note carries neither company nor role, so read_leads skips it and
    reconcile never sees it. Asserted because the file sits in a MANAGED folder, where a sweep
    that walked the directory instead of read_leads would pick it up."""
    v = _v(tmp_path)
    path = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Prep.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: prep\n---\nnotes\n")
    v.reconcile_layout(apply=True)
    assert os.path.isfile(path)


def test_a_move_that_races_a_status_write_is_reported_by_the_run_that_caused_it(
        tmp_path, monkeypatch):
    """The never-clobber RESIDUAL, made loud. `_cas_write` re-reads for freshness and then
    `_atomic_write` does `os.replace(tmp, path)`; a move landing in that window RE-CREATES the
    source path, leaving two notes at one basename -- one slug, so `upsert` refuses that lead
    for good with both `last_seen` frozen. Reconcile cannot prevent it (no portable stdlib
    atomic-conditional-rename exists), but the run that caused it must NAME it rather than
    leaving a later ingest to surface it as an unexplained refusal.

    The twin has to appear DURING the sweep. Seeding both up front instead -- the first draft --
    is consumed by the up-front `index_by_slug` and witnesses nothing about the post-sweep
    re-read, while looking exactly like a passing test."""
    v = _v(tmp_path)
    src = _seed(v, "A - Raced.md", role="Raced", status="shortlist")
    from sluice.core import vault as vaultmod
    real = vaultmod._reserve_and_move

    def racing_move(s, dest_dir, base, **kw):
        dest = real(s, dest_dir, base, **kw)
        # Exactly what a concurrent `_atomic_write`'s os.replace(tmp, path) does when it lands
        # after the move: the source path exists again, holding the racer's edit.
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: Example Ltd\nrole: Raced\nstatus: applied\n---\nbody\n")
        return dest

    monkeypatch.setattr(vaultmod, "_reserve_and_move", racing_move)
    rep = v.reconcile_layout(apply=True)
    assert len(rep["moves"]) == 1, "the move itself must still have happened"
    assert os.path.isfile(os.path.join(v.leads_dir, ACTIVE_SUBDIR, "A - Raced.md"))
    assert os.path.isfile(src), "the fixture did not reproduce the resurrected source path"
    assert "A - Raced" in rep["ambiguous"], "the post-sweep re-read did not report the race"
```

> **Deleted from this task's first draft: `test_apply_leaves_the_scan_set_cache_truthful`.**
> It was measured GREEN with `if moved_anything: self._rescan_dirs()` deleted, so it certified a
> guard it could not falsify — the precise shape this repo deletes rather than ships. Step 3a below
> says what to do instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile.py -v`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute 'reconcile_layout'`

- [ ] **Step 3: Implement `reconcile_layout`**

Add after `normalize_all_statuses` (it is the other whole-vault sweep, so they read together):

```python
    def _managed_dirs(self) -> set:
        """The directories reconcile may move a note OUT OF, as paths.

        The leads-dir ROOT, plus every folder the configured layout can file into. Decision 6:
        a lead the user deliberately filed into a folder of their own is REPORTED and left
        alone, because decision 4 ("everything under leads_dir that sluice does not own is the
        user's") has to hold for writes as well as for reads.

        The root is its OWN term and is NOT derivable from the layout map -- that is the whole
        point of spelling it separately, and getting it wrong made this feature inert in the
        first draft of this plan. Under `active_archive` every canonical status maps to `Active`
        or `Archive`, so `{layout_subfolder(s, layout) for s in CANONICAL}` can never contain
        `""`; the root was silently excluded, every note in a flat vault reported `user_filed`
        at ".", and nothing ever moved -- on the only vault shape a user opting in actually has.
        The root is managed because it is where a PRE-layout vault's notes sit, not because any
        status implies it.

        The SUBFOLDERS stay derived from `layout_subfolder` rather than hand-listed
        {Active, Archive}: a layout that later files into a third folder becomes managed
        automatically, and a hand-list would leave notes stranded there with nothing red.

        `_merged/` is not here and cannot be: it is pruned from the scan set, so `read_leads`
        never yields a note in it (#81)."""
        subs = {layout_subfolder(s, self.lead_layout) for s in _status.CANONICAL}
        return {self.leads_dir} | {os.path.join(self.leads_dir, s) for s in subs if s}

    def reconcile_layout(self, *, apply: bool = False) -> dict:
        """File lead notes into the folders their statuses imply. REPORTS by default; `apply`
        is what moves anything -- the default IS the dry run, which is why there is no
        `dry_run` parameter to be inert (`leads dedupe`/`leads expire` are the same shape).

        The ONLY pass that moves a lead note (decision 2). No pipeline command relocates
        anything, and folder-vs-status drift between runs is harmless because the scan is
        recursive: a note in the "wrong" folder is still read, still written to, still applied
        for. That is what makes this safe to be manual.

        It never writes a note's BYTES -- only its directory entry, via `_reserve_and_move`. No
        status is read-modify-written, no frontmatter key is set, no body is re-rendered.

        That is NOT the same as "never-clobber holds by construction", which is what this
        docstring claimed in the first draft and is measurably false. `_cas_write` re-reads for
        freshness and then `_atomic_write` calls `os.replace(tmp, path)`; a move landing in that
        window RE-CREATES the source path. The result is two notes at one basename -- one slug,
        so `_locate` returns two, `upsert` REFUSES that lead permanently, both notes' `last_seen`
        freeze, and the status edit is stranded on the resurrected copy while the moved note
        keeps the old one. A wider interleaving instead raises FileNotFoundError out of
        `_cas_write`, i.e. a lost modify-write arriving as an OSError rather than a
        VaultConflict. This is the same class of residual `_resolve_path` states for its own
        cache and `_cas_write` states for its compare->replace micro-window: no portable stdlib
        atomic-conditional-rename exists, so it is DOCUMENTED and made LOUD rather than closed.
        `merge_cluster` shares the primitive but not the exposure -- its destination is pruned
        from the scan set and its basename differs, so the same race there yields a visible
        duplicate rather than a self-collision.

        Made loud two ways: the CLI help says reconcile must not be run concurrently with a
        pipeline command, and after an applied sweep this re-reads and reports any basename now
        claimed by two paths into `ambiguous` (below) -- so the run that CAUSED it names it,
        instead of leaving it for a later ingest to discover as an unexplained refusal.

        FOUR classes are reported and never moved, each for its own reason:

        - `unknown`  -- a non-canonical status. never-regress passes an unrecognized value
          through untouched everywhere else, so the layout must not decide a folder for one.
          Mirrors `normalize_all_statuses`' own `unknown` bucket.
        - `ambiguous` -- a slug two or more notes claim. This cannot be REPAIRED here: the slug
          IS the filename, so renaming orphans the note from `_resolve_path`'s candidate walk
          and the next scrape mints a fresh one; and choosing which twin survives is
          `leads dedupe`'s job, via `resolve_merge_status`. Moving one twin would pick, which is
          precisely what `index_by_slug`, `upsert` and `select_one` all decline to do. Both are
          named so a human can merge or rename.
        - `user_filed` -- a lead outside the managed folders (see `_managed_dirs`).
        - `collisions` -- the destination name is taken. Refused, NEVER suffixed: a suffix
          changes the filename, which is the slug, which is the identity.

        Under the flat layout every canonical status maps to the root, so nothing is ever out of
        place and the sweep is a no-op that says so. Flattening instead would drag every lead
        out of the user's own subfolders -- decision 4 pointed the wrong way.

        Per-note `OSError` isolation, like `merge_cluster`'s per-loser arm and
        `normalize_all_statuses`' per-note one: one unmovable note must not abort the sweep.
        Not atomic across notes, deliberately -- an interrupted run leaves partial drift, which
        is the pass's normal input, and re-running converges.
        """
        summary = dict(_EMPTY_RECONCILE, layout=self.lead_layout,
                       moves=[], ambiguous={}, unknown=[], user_filed=[],
                       collisions=[], skipped=[])
        # Decision 7, and it lives HERE rather than in the CLI. Under the flat layout there is
        # nothing to reconcile against, and FLATTENING would drag every lead out of the user's
        # own subfolders -- decision 4 pointed the wrong way. Putting this in `cmd_leads_reconcile`
        # alone (the first draft) made the store and its own CLI disagree about what flat means:
        # the store still bucketed every user-filed note while the CLI said "nothing to
        # reconcile", and `Sluice.reconcile()` -- which every non-CLI caller goes through --
        # inherited the store's answer, not the CLI's. A behavioural rule about the layout
        # belongs to the thing that owns the layout.
        if not self.lead_layout:
            return summary
        notes = self.read_leads()      # prunes _merged/ (#81) and skips non-lead files
        managed = self._managed_dirs()
        # `index_by_slug`, never a hand-rolled dict: it is the one sanctioned way in
        # (core/leads.py), it DROPS both twins rather than keeping whichever came last, and the
        # `dropped` mapping it returns IS this pass's ambiguous bucket by construction. The
        # shipped guard `tests/test_slug_indexing_discipline.py` names `leads reconcile` as its
        # anticipated FIFTH consumer -- it was written for exactly this code -- and it must stay
        # GREEN. Do not relax its matcher.
        index, dropped = index_by_slug(notes)
        for slug, twins in dropped.items():
            summary["ambiguous"][slug] = sorted(
                os.path.relpath(t.ref, self.leads_dir) for t in twins)
        moved_anything = False
        for n in index.values():
            # The RAW value, not n.status: read_leads normalizes, and reporting the normalized
            # form for an unrecognized status would show the user a value their note does not
            # contain -- the thing they have to go and fix.
            raw = n.fm.get("status", "")
            sub = layout_subfolder(raw, self.lead_layout)
            if sub is None:
                summary["unknown"].append((n.slug, raw))
                continue
            src_dir = os.path.dirname(n.ref)
            if src_dir not in managed:
                summary["user_filed"].append(
                    (n.slug, os.path.relpath(src_dir, self.leads_dir)))
                continue
            dest_dir = os.path.join(self.leads_dir, sub) if sub else self.leads_dir
            if os.path.normpath(src_dir) == os.path.normpath(dest_dir):
                summary["in_place"] += 1
                continue
            base = os.path.basename(n.ref)
            dst_rel = os.path.join(sub, base) if sub else base
            src_rel = os.path.relpath(n.ref, self.leads_dir)
            if not apply:
                summary["moves"].append((n.slug, src_rel, dst_rel))
                continue
            try:
                os.makedirs(dest_dir, exist_ok=True)
                # suffix_on_collision=False: see _reserve_and_move. A FileExistsError here is a
                # REFUSAL, not a failure, so it is caught before the generic OSError arm and
                # reported in its own bucket -- conflating the two would tell a human to check
                # permissions when what they actually have is two notes at one name.
                _reserve_and_move(n.ref, dest_dir, base, suffix_on_collision=False)
            except FileExistsError:
                summary["collisions"].append((n.slug, dst_rel))
                _log.warning("reconcile: %s -> %s refused: destination is taken "
                             "(merge or rename by hand; a numeric suffix would change the slug)",
                             src_rel, dst_rel)
                continue
            except OSError as e:
                summary["skipped"].append((n.slug, str(e)))
                _log.warning("reconcile: could not move %s -> %s: %s", src_rel, dst_rel, e)
                continue
            summary["moves"].append((n.slug, src_rel, dst_rel))
            moved_anything = True
        if moved_anything:
            # Re-derive the scan-set cache after a sweep that created directories and moved
            # notes into them, so the store's own view matches the disk for any later call on
            # this instance.
            #
            # This is HYGIENE, not the thing that prevents a duplicate, and the first draft of
            # this comment claimed otherwise. Measured: deleting this line leaves the whole
            # suite green, because `_resolve_path` already re-derives on its miss branch and
            # reconcile can only ADD directories -- so a stale set is a strict SUBSET, `_locate`
            # can only find FEWER notes, and finding fewer is exactly the `missed=True` branch
            # that re-derives. The other direction (found ONCE where fresh finds TWICE) needs
            # two notes at one name, which this pass refuses rather than creates. Kept because
            # it costs one walk per applied sweep and leaves the instance truthful; NOT kept
            # with a test asserting it prevents something it does not.
            self._rescan_dirs()
            # The never-clobber residual (see the docstring), reported by the run that caused
            # it. A move racing a concurrent `_cas_write`'s `os.replace(tmp, path)` re-creates
            # the source path, and the resulting same-basename pair would otherwise surface much
            # later as an unexplained `upsert` refusal with no note anywhere saying why. This
            # re-read is the cheapest place to name it: the sweep is a manual, human-gated pass
            # and has already walked the tree once.
            _, raced = index_by_slug(self.read_leads())
            for slug, twins in raced.items():
                summary["ambiguous"].setdefault(slug, sorted(
                    os.path.relpath(t.ref, self.leads_dir) for t in twins))
        return summary
```

Add `index_by_slug` to the `from sluice.core.leads import ...` line alongside `layout_subfolder`
(`ACTIVE_SUBDIR`/`ARCHIVE_SUBDIR` are needed by the tests, not by `vault.py` itself, since the
folders are only ever reached through `layout_subfolder`). `from sluice.core import status as
_status` is already imported at `vault.py:26`.

Define the empty-report shape ONCE, at module level beside `_PRIVATE_SUBDIRS`, so the store and the
CLI cannot drift:

```python
# The reconcile report's key set, in one place. `cmd_leads_reconcile`'s knob-unset arm emits a
# document too (a consumer parsing stdout must not have to tell "no output" from "empty result"),
# and a second hand-written literal there is a shape nothing keeps in sync -- add a bucket here and
# that arm would silently stop carrying it while its test, which only checks ["layout"], stayed
# green. Both sides build from this.
_EMPTY_RECONCILE = {"layout": "", "moves": [], "in_place": 0, "ambiguous": {},
                    "unknown": [], "user_filed": [], "collisions": [], "skipped": []}
```

`reconcile_layout` seeds `summary` from it with fresh mutable containers (the `dict(_EMPTY_RECONCILE,
...)` call above); the CLI reuses it verbatim for the unset arm.

- [ ] **Step 4: Classify reconcile's makedirs in the scope guard**

`tests/test_vault_makedirs_scope.py`, add to `_EXPECTED`:

```python
    # reconcile's destination -- leads_dir/<Active|Archive>, derived from `layout_subfolder`.
    # SCANNED, so it must NOT be in _PRIVATE_SUBDIRS: pruning it would hide every reconciled
    # note from read_leads and from _locate, re-creating all of them on the next scrape.
    #
    # NB this key is a BARE LOCAL NAME, so a second `os.makedirs(dest_dir)` anywhere in vault.py
    # -- however that local is derived -- would be absorbed by this classification silently.
    # `merged_dir` above has the same shape, which is why this is recorded rather than fixed:
    # the guard's job is to make an author classify each new call, and a bare name cannot tell
    # two call sites apart. Stated so the limit is known rather than assumed.
    "dest_dir": "a reconcile destination folder, scanned",
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile.py tests/test_vault_makedirs_scope.py -v`
Expected: all PASS

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+16** (15 test functions, one of them parametrized 4-way; the cache test
of the first draft is deleted -- see Step 7), ruff clean. No pre-existing test may change -- in
particular `tests/test_slug_indexing_discipline.py` must stay GREEN.

- [ ] **Step 7: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| **DELETE the `{self.leads_dir} |` term from `_managed_dirs`** | `test_leads_reconcile.py::test_apply_files_a_live_lead_into_active` |
| Delete the `if not self.lead_layout: return summary` short-circuit | `test_leads_reconcile.py::test_a_flat_layout_reconciles_nothing` |
| Replace `index_by_slug(notes)` with `{n.slug: n for n in notes}`, dropping the ambiguous bucket | `test_leads_reconcile.py::test_two_notes_claiming_one_slug_are_refused_and_neither_moves` |
| Delete the `if src_dir not in managed:` arm | `test_leads_reconcile.py::test_a_user_filed_lead_is_reported_and_left_alone` |
| Delete the `if sub is None:` arm | `test_leads_reconcile.py::test_a_non_canonical_status_is_reported_under_unknown_and_never_moved` |
| Move the `if not apply:` guard BELOW the `_reserve_and_move` call | `test_leads_reconcile.py::test_the_report_moves_nothing` |
| Change `suffix_on_collision=False` to `True` | `test_leads_reconcile.py::test_a_destination_collision_refuses_that_note_and_continues_the_sweep` |
| Replace `continue` with `raise` in the `except OSError` arm | `test_leads_reconcile.py::test_a_move_oserror_is_isolated_and_the_sweep_continues` |
| Delete the post-sweep `index_by_slug(self.read_leads())` re-read | `test_leads_reconcile.py::test_a_move_that_races_a_status_write_is_reported_by_the_run_that_caused_it` |
| Replace `raw = n.fm.get("status", "")` with `raw = n.status` | `test_leads_reconcile.py::test_a_non_canonical_status_is_reported_under_unknown_and_never_moved` (reports the normalized value) |
| Replace `self.read_leads()` with a direct `self._walk()` file loop | `test_leads_reconcile.py::test_a_non_lead_file_is_never_moved` |

**Two rows are deliberately absent, and their absence is the finding this table was corrected for.**

*"Replace `_managed_dirs`' derivation with the literal `{ACTIVE_SUBDIR, ARCHIVE_SUBDIR}`"* was in the
first draft and is a **proven equivalent mutant**: under `active_archive` the shipped derivation
computes exactly that set, so the mutant is byte-equivalent to the original and comes back green —
reading as "this test is inert", which is how a real guard gets deleted here. The honest mutant is
the `{self.leads_dir} |` DELETE now at the top of the table, and it must be witnessed red before the
row is trusted.

*"Delete the `if moved_anything: self._rescan_dirs()` block"* had no witness: measured, deleting it
leaves the whole suite green, because `_resolve_path` already re-derives on its miss branch. The call
is kept as hygiene with a comment saying exactly that, and no test claims otherwise. See Step 3a.

- [ ] **Step 3a: Before trusting the `_rescan_dirs()` call, search for a case it actually saves**

Run the search rather than reasoning about it. Warm the cache with a real `_locate`, apply a sweep,
then look for ANY lead whose `_resolve_path` verdict differs between the stale and fresh directory
lists. The hypothesis is that none exists: reconcile only ADDS directories, so a stale set is a
strict subset, `_locate` can only find FEWER notes, and finding fewer is precisely the `missed=True`
branch that already re-derives — while the other direction (found once where fresh finds twice)
needs two notes at one name, which this pass refuses rather than creates.

If you find a case, pin it with a test and say so in the comment. If you do not, leave the comment as
written (hygiene, not prevention) and add **no** test — an assertion that certifies a guard it cannot
falsify is worse than no assertion.

- [ ] **Step 8: Commit**

```bash
git add sluice/core/vault.py tests/test_leads_reconcile.py tests/test_vault_makedirs_scope.py
git commit -m "$(cat <<'EOF'
feat(vault): add reconcile_layout, the only pass that moves a lead note (#1)

Reports by default; `apply=True` moves. Never writes a note's BYTES -- only its
directory entry, through the shared _reserve_and_move -- so never-clobber and
never-regress are untouched by construction.

Four classes are reported and never moved, each for its own reason: a non-canonical
status (never-regress passes it through untouched); a slug two notes claim (the
slug IS the filename, so a rename orphans the note and picking a survivor is
`leads dedupe`'s job -- both are refused and named, the shape index_by_slug and
upsert already use); a lead outside the managed folders (decision 4 must hold for
writes as well as reads); and a taken destination (refused, never suffixed).

The managed set is DERIVED from layout_subfolder over the canonical vocabulary, so
a layout that later files into a third folder becomes managed automatically. It
includes the leads-dir root, which is what makes migration from a flat vault the
same code path as ordinary drift rather than a second mechanism.

An applied sweep re-derives the scan-set cache: it has just created directories and
moved notes, and a stale list sends the next _locate into the `if not found:`
branch -- a duplicate, or a merged_away seen.db row with no removal path.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 6: The `Sluice` facade

`reconcile_layout` is **not** on the `Store` protocol. Folders are a vault mechanism; a store keyed on
synthetic ids has none, and adding the method would invent an obligation every other store must
pretend to honour — the exact thing `ensure_stfolder` was moved OUT of the protocol to avoid, and what
`cmd_init` declines to do with `Store.display_location()`. The facade therefore checks for the
capability and fails loudly, naming the configured store.

**Files:**
- Modify: `sluice/core/app.py` (add after `dedupe_merge` ~`:700`)
- Test: `tests/test_leads_reconcile_cli.py` (create; the CLI half lands in Task 7)

**Interfaces:**
- Consumes: `Vault.reconcile_layout` (Task 5), `Sluice.store()` (shipped).
- Produces:
  - `Sluice.reconcile_report() -> dict` — `reconcile_layout(apply=False)`
  - `Sluice.reconcile(apply: bool = False) -> dict`
  - `sluice.core.app.StoreHasNoLayout(RuntimeError)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leads_reconcile_cli.py` with the facade half:

```python
"""The facade + CLI for `sluice leads reconcile`."""
import os

import pytest

from sluice.core.app import Sluice, StoreHasNoLayout
from sluice.core.config import Config
from sluice.core.leads import ACTIVE_SUBDIR


def _seed(leads_dir, rel, *, company="Example Ltd", role="Example Role", status="new"):
    path = os.path.join(leads_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\ncompany: {company}\nrole: {role}\nstatus: {status}\n"
                 f"url: \nlast_seen: 2026-01-01\n---\nbody\n")
    return path


def _app(tmp_path, layout="active_archive", monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.delenv("VAULT_DIR", raising=False)
    app = Sluice(Config(vault_dir=str(tmp_path), lead_layout=layout))
    leads = app.store().leads_dir
    os.makedirs(leads, exist_ok=True)
    return app, leads


def test_the_facade_report_changes_nothing(tmp_path, monkeypatch):
    app, leads = _app(tmp_path, monkeypatch=monkeypatch)
    src = _seed(leads, "A - Live.md", role="Live", status="shortlist")
    rep = app.reconcile_report()
    assert len(rep["moves"]) == 1
    assert os.path.isfile(src)


def test_the_facade_applies(tmp_path, monkeypatch):
    app, leads = _app(tmp_path, monkeypatch=monkeypatch)
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    app.reconcile(apply=True)
    assert os.path.isfile(os.path.join(leads, ACTIVE_SUBDIR, "A - Live.md"))


def test_a_store_without_a_layout_fails_loudly_and_names_it(tmp_path, monkeypatch):
    """The capability check. It is inert today -- `vault` is the only registered store -- but
    unlike track/receipt.py's deleted guard it is WITNESSABLE through the store seam, which is
    the discriminator read_leads' duplicate-slug comment sets out. Without it a second store
    gets an AttributeError traceback instead of a sentence telling the user what is wrong."""
    class _NoLayout:
        def read_leads(self, statuses=None):
            return []

    # Through the PUBLIC seam-override kwarg, not by poking `_overrides`: `Sluice.__init__`
    # validates the seam name against `_SEAMS`, so this also proves the injection point is real.
    app = Sluice(Config(vault_dir=str(tmp_path), lead_layout="active_archive"),
                 store=_NoLayout())
    with pytest.raises(StoreHasNoLayout, match="_NoLayout"):
        app.reconcile_report()


def test_the_cli_renders_a_layoutless_store_as_a_usage_error(tmp_path, capsys, monkeypatch):
    """The guard's whole stated benefit is "a sentence instead of a traceback", and raising alone
    does not deliver it: `cli.py:main` catches only ValueError around `load_config` and then
    returns `args.func(args, config)` bare, so a RuntimeError propagates as an uncaught
    traceback and the user swaps one traceback for another. rc 2 matches the usage-error
    convention `main`'s config arm and `cmd_init` already use."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    from sluice.cli import cmd_leads_reconcile

    class _NoLayout:
        def read_leads(self, statuses=None):
            return []

    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    monkeypatch.setattr("sluice.core.app.Sluice.store", lambda self: _NoLayout())
    assert cmd_leads_reconcile(_Args(), cfg) == 2
    assert "no folder layout" in capsys.readouterr().err
```

> **Implementer note:** `_Args` is defined in the CLI half of this file (Task 7). If Task 6 is run
> before Task 7, move this test down to Task 7's block rather than duplicating `_Args`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'StoreHasNoLayout'`

- [ ] **Step 3: Implement the facade**

In `sluice/core/app.py`, near the other module-level exception imports:

```python
class StoreHasNoLayout(RuntimeError):
    """The configured store has no folder layout, so `leads reconcile` has nothing to do.

    Raised rather than silently reporting an empty sweep: an empty report and "this store does
    not have folders" look identical to a user, and the second is the one that needs saying.
    """
```

After `dedupe_merge`:

```python
    def _layout_store(self):
        """The store, if it implements the vault-only layout pass.

        `reconcile_layout` is deliberately NOT on the Store protocol. Folders are a vault
        MECHANISM -- a store keyed on synthetic ids has none -- and putting it on the contract
        would make every other implementation pretend to honour a concept it does not have.
        That is the leak `ensure_stfolder` was moved out of the protocol to remove, and the
        surface `cmd_init` declines to invent for `Store.display_location()`. So the coupling is
        concrete and CHECKED, rather than hypothetical and abstracted: when a second store lands
        and has an opinion about layout, that is the moment to reconsider.

        `getattr` rather than `isinstance(store, Vault)`: importing the concrete Vault into the
        facade to type-test it would put the store implementation back on the composition root's
        import path, which `cli.py`'s lazy-import discipline exists to keep off it.
        """
        store = self.store()
        fn = getattr(store, "reconcile_layout", None)
        if not callable(fn):
            raise StoreHasNoLayout(
                f"the configured store ({type(store).__name__}) has no folder layout, so "
                f"`leads reconcile` has nothing to reconcile")
        return fn

    def reconcile_report(self) -> dict:
        """The #1 layout REPORT: which lead notes are not in the folder their status implies.
        Changes nothing. See `Vault.reconcile_layout`."""
        return self._layout_store()(apply=False)

    def reconcile(self, apply: bool = False) -> dict:
        """File lead notes into their status-implied folders. `apply=False` (the default) is the
        report -- the same report-first shape as `dedupe_report`/`expire_report`, where a
        mistyped invocation prints a list rather than moving a hundred notes."""
        return self._layout_store()(apply=apply)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+3**, ruff clean.

- [ ] **Step 6: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Delete the `if not callable(fn): raise` guard | `test_leads_reconcile_cli.py::test_a_store_without_a_layout_fails_loudly_and_names_it` |
| Change `reconcile`'s default to `apply=True` | `test_leads_reconcile_cli.py::test_the_facade_report_changes_nothing` |
| Change `reconcile_report` to call `apply=True` | `test_leads_reconcile_cli.py::test_the_facade_report_changes_nothing` |

- [ ] **Step 7: Commit**

```bash
git add sluice/core/app.py tests/test_leads_reconcile_cli.py
git commit -m "$(cat <<'EOF'
feat(core): expose reconcile through the Sluice facade (#1)

`reconcile_layout` stays OFF the Store protocol: folders are a vault mechanism, and
putting it on the contract would make every other store pretend to honour a concept
it does not have -- the leak ensure_stfolder was moved out of the protocol to
remove, and the surface cmd_init declines to invent. So the coupling is concrete
and checked: a store without the method raises StoreHasNoLayout naming itself,
rather than an AttributeError traceback or -- worse -- an empty report that reads
exactly like "nothing to do".

The guard is inert today (vault is the only registered store) but WITNESSABLE
through the seam, which is the discriminator read_leads' duplicate-slug comment
sets out for keeping a guard rather than deleting it.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 7: `sluice leads reconcile`

**Files:**
- Modify: `sluice/cli.py` (add `cmd_leads_reconcile` after `cmd_leads_expire` ~`:334`; subparser after
  the `ex` block ~`:966`)
- Test: `tests/test_leads_reconcile_cli.py` (extend)

**Interfaces:**
- Consumes: `Sluice.reconcile`, `StoreHasNoLayout` (Task 6).
- Produces: `cmd_leads_reconcile(args, config) -> int`; `sluice leads reconcile [--apply] [--json]`.

**Exit codes**, mirroring `cmd_leads_expire` exactly — a report is always 0; a write that was asked
for and did not happen is 1:

| Situation | `--apply` | exit |
| --- | --- | --- |
| any run, report only | no | 0 |
| every planned move completed | yes | 0 |
| any `collisions`, `skipped`, or `ambiguous` | yes | 1 |
| `lead_layout` unset | no | 0 |
| `lead_layout` unset | yes | 1 |

`ambiguous` counts as a failure under `--apply` for the same reason `expire`'s does: the user asked
for a write on a set that includes a lead nothing was written for, and a silent 0 is the exact no-op
this report-first shape exists to avoid. `unknown` and `user_filed` do **not** — those are reported
states the pass is designed to leave alone, not failures to act.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leads_reconcile_cli.py`:

```python
# ── the CLI ───────────────────────────────────────────────────────────────────
import json as _json

from sluice.cli import cmd_leads_reconcile


class _Args:
    def __init__(self, **kw):
        self.apply = kw.get("apply", False)
        self.json = kw.get("json", False)


def test_the_cli_report_exits_zero_and_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    leads = Sluice(cfg).store().leads_dir
    src = _seed(leads, "A - Live.md", role="Live", status="shortlist")
    assert cmd_leads_reconcile(_Args(), cfg) == 0
    assert os.path.isfile(src)
    assert "A - Live" in capsys.readouterr().err


def test_the_cli_apply_moves_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    leads = Sluice(cfg).store().leads_dir
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 0
    assert os.path.isfile(os.path.join(leads, ACTIVE_SUBDIR, "A - Live.md"))


def test_an_apply_that_refused_a_note_exits_non_zero(tmp_path, monkeypatch):
    """A silent 0 on a write the user asked for and did not get is the no-op this report-first
    shape exists to avoid -- the `_FAILED` rule `cmd_leads_expire` states."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    leads = Sluice(cfg).store().leads_dir
    _seed(leads, os.path.join(ACTIVE_SUBDIR, "A - Twin.md"), role="Twin", status="shortlist")
    _seed(leads, "A - Twin.md", role="Twin", status="dismiss")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 1


def test_an_unset_layout_says_so_and_reports_zero(tmp_path, capsys, monkeypatch):
    """Decision 7, and the `lead_ttl_days: 0` precedent verbatim: NOT '0 to move', which is
    indistinguishable from 'nothing is out of place' and would let a user believe a knob they
    never configured is filing their vault."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="")
    assert cmd_leads_reconcile(_Args(), cfg) == 0
    assert "lead_layout is unset" in capsys.readouterr().err


def test_an_unset_layout_exits_non_zero_when_apply_was_asked_for(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 1


def test_the_unset_layout_json_arm_still_emits_a_document(tmp_path, capsys, monkeypatch):
    """A consumer parsing stdout must not have to tell 'no output' from 'empty result' --
    `cmd_leads_expire`'s rule."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="")
    cmd_leads_reconcile(_Args(json=True), cfg)
    doc = _json.loads(capsys.readouterr().out)
    assert doc["layout"] == ""
    # The SAME key set the layout-ON arm carries. Without this, a bucket added to
    # reconcile_layout later leaves this document short and nothing says so -- a consumer
    # parsing stdout would see a different shape depending on a knob.
    assert set(doc) >= {"layout", "moves", "in_place", "ambiguous", "unknown",
                        "user_filed", "collisions", "skipped"}


def test_the_json_report_carries_every_bucket(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    leads = Sluice(cfg).store().leads_dir
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    cmd_leads_reconcile(_Args(json=True), cfg)
    doc = _json.loads(capsys.readouterr().out)
    assert set(doc) >= {"layout", "moves", "in_place", "ambiguous", "unknown",
                        "user_filed", "collisions", "skipped"}


def test_the_subparser_registers_apply_and_has_no_dry_run(tmp_path):
    """No --dry-run: the default IS the dry run, and a flag that does nothing is drift
    (`leads dedupe` and `leads expire` are the same shape). Asserted through the real parser,
    because a flag added later would otherwise ship unnoticed."""
    from sluice.cli import _build_parser
    p = _build_parser()
    ns = p.parse_args(["leads", "reconcile", "--apply"])
    assert ns.apply is True and ns.func is cmd_leads_reconcile
    with pytest.raises(SystemExit):
        p.parse_args(["leads", "reconcile", "--dry-run"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'cmd_leads_reconcile' from 'sluice.cli'`

- [ ] **Step 3: Implement the command**

Add after `cmd_leads_expire`:

```python
def cmd_leads_reconcile(args, config) -> int:
    from sluice.core.app import Sluice, StoreHasNoLayout

    if not config.lead_layout:
        # NOT "0 to move": that is indistinguishable from "nothing is out of place", and would
        # let a user believe a knob they never configured is filing their vault. The
        # `lead_ttl_days is unset` arm above is the same sentence for the same reason.
        print("reconcile: lead_layout is unset -- the flat layout is in use, nothing to "
              "reconcile (set lead_layout: active_archive to opt in)", file=sys.stderr)
        # Still emit a document on --json: a consumer parsing stdout must not have to
        # distinguish "no output" from "empty result".
        if args.json:
            # From the store's own constant, never a second hand-written literal: a bucket added
            # to reconcile_layout must not silently stop appearing here.
            from sluice.core.vault import _EMPTY_RECONCILE
            print(json.dumps(_EMPTY_RECONCILE))
        # An --apply that wrote nothing is a failure, not a success -- the silent no-op this
        # report-first command is shaped to avoid.
        return 1 if args.apply else 0

    try:
        rep = Sluice(config).reconcile(apply=args.apply)
    except StoreHasNoLayout as exc:
        # A sentence and rc 2, not a traceback. `main` catches only ValueError (around
        # load_config) and then calls args.func bare, so without this the RuntimeError reaches
        # the user as a stack trace -- which is exactly what the capability check exists to
        # avoid, so leaving it unhandled would make the guard's justification pure prose.
        # rc 2 is the usage-error convention main's config arm and cmd_init already use.
        print(f"sluice: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep))
    else:
        verb = "moved" if args.apply else "would move"
        for _slug, src, dst in rep["moves"]:
            print(f"reconcile: {verb} {src} -> {dst}", file=sys.stderr)
        for slug, refs in sorted(rep["ambiguous"].items()):
            print(f"reconcile: {slug}: NOT moved -- {len(refs)} notes claim this slug "
                  f"({', '.join(refs)}); merge them (sluice leads dedupe) or rename one",
                  file=sys.stderr)
        for slug, raw in rep["unknown"]:
            print(f"reconcile: {slug}: left in place -- status {raw!r} is not canonical",
                  file=sys.stderr)
        for slug, where in rep["user_filed"]:
            print(f"reconcile: {slug}: left in place -- {where}/ is yours, not sluice's",
                  file=sys.stderr)
        for slug, dst in rep["collisions"]:
            print(f"reconcile: {slug}: NOT moved -- {dst} is taken (a numeric suffix would "
                  f"change the slug, which is the identity)", file=sys.stderr)
        for slug, err in rep["skipped"]:
            print(f"reconcile: {slug}: NOT moved -- {err}", file=sys.stderr)
        print(f"reconcile: layout={rep['layout']} {verb}={len(rep['moves'])} "
              f"in_place={rep['in_place']} ambiguous={len(rep['ambiguous'])} "
              f"unknown={len(rep['unknown'])} user_filed={len(rep['user_filed'])} "
              f"collisions={len(rep['collisions'])} skipped={len(rep['skipped'])}"
              f"{'' if args.apply else ' (report only -- pass --apply to move)'}",
              file=sys.stderr)
    if not args.apply:
        return 0
    # Only the buckets where a MOVE was attempted-or-owed and did not happen. `unknown` and
    # `user_filed` are states this pass is DESIGNED to leave alone, so counting them would make
    # a correct run exit 1 forever. `ambiguous` does count: the user asked for a write over a
    # set including a lead nothing was written for, which is precisely what they must notice.
    return 1 if (rep["collisions"] or rep["skipped"] or rep["ambiguous"]) else 0
```

Register the subparser after the `ex` block:

```python
    rc = leads.add_parser(
        "reconcile",
        help="report/file lead notes into their status-implied folders",
        description="Report, or with --apply move, each lead note into the folder its status "
                    "implies. Do NOT run --apply concurrently with a pipeline command "
                    "(ingest/triage/cv/apply/track): a move landing inside another writer's "
                    "compare-and-set window re-creates the source path, leaving two notes at "
                    "one name. That state is reported under `ambiguous` rather than prevented.")
    # No --dry-run: the default IS the dry run (nothing moves without --apply), and a flag that
    # does nothing is drift. Same shape as `leads dedupe --merge` and `leads expire --expire`.
    rc.add_argument("--apply", action="store_true",
                    help="actually move the notes this would otherwise only report")
    rc.add_argument("--json", action="store_true", help="machine-readable report")
    rc.set_defaults(func=cmd_leads_reconcile)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_reconcile_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+9** (eight CLI tests plus the `StoreHasNoLayout` rc-2 twin), ruff clean.

- [ ] **Step 6: Drive it by hand, end to end**

The suite is hermetic; this is the one step that runs the real command. Build a throwaway vault
under the scratch directory (never the user's real vault), and check the OUTPUT a human reads:

```bash
SLUICE="$PWD/.venv/bin/sluice"          # run from the repo root FIRST; see the note below
cd "$(mktemp -d)" && mkdir -p v/"Job Applications/Job Leads"
printf -- '---\ncompany: Example Ltd\nrole: Example Role\nstatus: dismiss\nurl: \n---\nbody\n' \
  > v/"Job Applications/Job Leads/Example Ltd - Example Role.md"
printf 'vault_dir: %s/v\nlead_layout: active_archive\n' "$PWD" > c.yaml
SLUICE_CONFIG=$PWD/c.yaml "$SLUICE" leads reconcile ; echo "rc=$?"
SLUICE_CONFIG=$PWD/c.yaml "$SLUICE" leads reconcile --apply ; echo "rc=$?"
find v -name '*.md'
SLUICE_CONFIG=$PWD/c.yaml "$SLUICE" leads reconcile --apply ; echo "rc=$?"   # idempotent
```

**Not `python -m sluice`** — there is no `sluice/__main__.py`, so that fails with *"No module named
sluice.__main__"* (measured). The entry point is the `sluice = "sluice.cli:main"` console script at
`.venv/bin/sluice`. And not bare `python`/`ruff` either: both are the broken proto shim on this
machine, which is why `SLUICE` is captured from the repo root before the `cd`.

Expected: the report names the move and writes nothing (rc 0); `--apply` moves it to
`Archive/` (rc 0); the second `--apply` reports `moved=0 in_place=1` (rc 0). Confirm no line
prints an absolute path.

- [ ] **Step 7: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Change `Sluice(config).reconcile(apply=args.apply)` to `apply=True` | `test_leads_reconcile_cli.py::test_the_cli_report_exits_zero_and_writes_nothing` |
| Delete `rep["ambiguous"]` from the exit-code expression | `test_leads_reconcile_cli.py::test_an_apply_that_refused_a_note_exits_non_zero` |
| Delete the `if not config.lead_layout:` block | `test_leads_reconcile_cli.py::test_an_unset_layout_says_so_and_reports_zero` |
| Change `return 1 if args.apply else 0` to `return 0` in that block | `test_leads_reconcile_cli.py::test_an_unset_layout_exits_non_zero_when_apply_was_asked_for` |
| Delete the `if args.json:` arm inside that block | `test_leads_reconcile_cli.py::test_the_unset_layout_json_arm_still_emits_a_document` |
| Add `rc.add_argument("--dry-run", action="store_true")` **(the marked ADD exception: the property under test is that no such flag exists, so adding it IS the mutation)** | `test_leads_reconcile_cli.py::test_the_subparser_registers_apply_and_has_no_dry_run` |

- [ ] **Step 8: Commit**

```bash
git add sluice/cli.py tests/test_leads_reconcile_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add `sluice leads reconcile` (#1)

Reports by default, moves on --apply, and has no --dry-run because the default IS
the dry run and a flag that does nothing is drift -- the shape `leads dedupe` and
`leads expire` already use, for the reason those share: a `leads` pass writes over
a set the TOOL computed, so a mistyped one must print a list rather than move a
hundred notes.

Exit codes mirror cmd_leads_expire's `_FAILED` rule: a report is always 0, and an
--apply is 1 if any note the user asked to move did not. `ambiguous` counts;
`unknown` and `user_filed` do not, because those are states the pass is designed to
leave alone and counting them would make a correct run exit 1 forever.

An unset lead_layout says so rather than printing "0 to move", which is
indistinguishable from "nothing is out of place" -- the same sentence
`lead_ttl_days: 0` prints, for the same reason.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 8: Conformance re-check and docs

The design says PR B adds **no new Store-contract property** (folders are a vault mechanism, per the
#48 ruling), but it must prove the existing ones still hold with the layout ON — a guard whose
fixture cannot produce the failing case is inert, which is exactly how PR A's slug-uniqueness
conformance test passed while the recursive scan broke it.

**Files:**
- Read (Step 3, NOT modified): `tests/conformance/test_store_contract.py` -- inspected for
  fixture adequacy. The layout-on variant lands in the new file below; parametrising the
  conformance suite on layout would make the layout a contract property the #48 ruling says it is
  not.
- Modify: `docs/ARCHITECTURE.md`
- Modify: `.rulesync/rules/CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md` (status line)
- Test: `tests/test_lead_layout_invariants.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lead_layout_invariants.py`:

```python
"""The load-bearing invariants, re-asserted with `lead_layout` ON.

PR A's lesson: `test_slug_is_issued_stable_and_unique` asserted a property the recursive scan
broke and still passed, because its fixture seeded two different companies and could never
collide. A guard whose fixture cannot produce the failing case is inert -- so these run the
SAME properties through the layout that could plausibly break them."""
import os

from sluice.core.leads import ACTIVE_SUBDIR, ARCHIVE_SUBDIR, Lead
from sluice.core.vault import Vault


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    # `search` is REQUIRED (core/leads.py:142). See the Global Constraints.
    return Lead(source="test", search="q", title=title, company=company, url=url, location="")


def test_a_merged_away_lead_is_not_recreated_with_the_layout_on(tmp_path):
    """#81 non-resurrection, under `active_archive`. `_merged/` is pruned at the TOP LEVEL of
    leads_dir, and the write folder is now a SUBFOLDER of leads_dir -- so this checks the prune
    and the probe still agree once creates no longer land beside `_merged/`."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    assert v.upsert(_lead()) == "created"
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    other = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role 2.md")
    with open(other, "w", encoding="utf-8") as fh:
        fh.write("---\ncompany: Example Ltd\nrole: Example Role 2\nstatus: new\n"
                 "url: https://example.invalid/1\n---\nbody\n")
    v.merge_cluster(ref, [other], alt_urls=[], first_seen="", last_seen="")
    assert v.upsert(Lead(source="test", search="q", title="Example Role 2",
                         company="Example Ltd", url="https://example.invalid/1",
                         location="")) == "merged_away"
    assert not os.path.exists(
        os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role 2.md"))


def test_a_rescrape_after_reconcile_touches_only_last_seen(tmp_path):
    """never-clobber, across a MOVE. Reconcile relocates a note; the next scrape must find it in
    its new folder and bump last_seen only -- never re-create it, never rewrite its status."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    v.upsert(_lead())
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    v.update_fields(ref, {"status": "dismiss", "score": "7"})
    v.reconcile_layout(apply=True)
    moved = os.path.join(v.leads_dir, ARCHIVE_SUBDIR, "Example Ltd - Example Role.md")
    assert os.path.isfile(moved)
    before = open(moved, encoding="utf-8").read()
    assert v.upsert(_lead()) in ("updated", "merged")
    after = open(moved, encoding="utf-8").read()
    assert "status: dismiss" in after and "score: 7" in after

    # The precise property: every line EXCEPT last_seen is byte-identical. Written as a
    # comparison of filtered line lists rather than a substring check, because "status: dismiss
    # is still in there" is satisfied by a note that also gained or lost ten other lines.
    def _except_last_seen(text):
        return [ln for ln in text.splitlines() if not ln.startswith("last_seen")]

    assert _except_last_seen(after) == _except_last_seen(before)


def test_reconcile_never_writes_a_status(tmp_path):
    """never-regress. Reconcile moves directory entries and nothing else -- if it ever wrote a
    status it would be a second owner of the key core/status.py governs."""
    v = Vault(str(tmp_path), lead_layout="active_archive")
    v.upsert(_lead())
    ref = os.path.join(v.leads_dir, ACTIVE_SUBDIR, "Example Ltd - Example Role.md")
    v.update_fields(ref, {"status": "applied"})
    before = open(ref, encoding="utf-8").read()
    v.reconcile_layout(apply=True)
    # `applied` is live, not terminal, so it stays in Active/ -- and the bytes are untouched.
    assert open(ref, encoding="utf-8").read() == before
```

- [ ] **Step 2: Run to verify they fail (or reveal a real defect)**

Run: `.venv/bin/python -m pytest tests/test_lead_layout_invariants.py -v`

Expected: these assert properties that **should already hold** after Tasks 1–7. If any fails, that
is a real defect in the earlier tasks — fix the code, not the test. **Print the intermediate state
before concluding**: a probe whose fixture fails to construct its precondition (an unresolvable ref,
an outcome that never reaches the arm under test) looks identical to a probe that disproves the
claim. Confirm `upsert` returned what you expected before trusting any assertion downstream of it.

- [ ] **Step 3: Check what the conformance suite already asserts**

```bash
grep -n "def test_" tests/conformance/test_store_contract.py
```

For each, ask PR A's question: *can this fixture produce the failing case under `lead_layout`?*
`test_merged_away_lead_is_never_recreated` and `test_slug_is_issued_stable_and_unique` are the two
that touch identity. If either constructs its store through a fixture that hardcodes the flat
layout, note it in the PR body — do **not** widen the conformance suite to parametrise on layout,
which would make the layout a contract property the #48 ruling says it is not. The vault-level
tests above are the right altitude.

- [ ] **Step 4: Update `docs/ARCHITECTURE.md`**

**First, correct two shipped passages this PR makes actively FALSE.** Both promise that reconcile
will *repair* an ambiguous slug; decision 5 settles the opposite, and a stale architecture doc that
is believed is the worst kind — it would tell the next agent to implement repair inside reconcile.

- **`:543-545`** — *"Repairing the state, rather than declining to act on it, still belongs with the
  `leads reconcile` pass #1 has yet to ship, which walks the whole tree anyway."*
- **`:610`** — *"— and repairing it belongs with the `leads reconcile` pass."*

Rewrite both to name `sluice leads dedupe --merge` (or a hand rename) as the repair path, with
reconcile named as the pass that REPORTS the ambiguity under `ambiguous` and declines to act — and
say why it cannot repair: the slug IS the filename, so a rename orphans the note from
`_resolve_path`'s candidate walk, and choosing a survivor is a merge decision `resolve_merge_status`
owns.

Then check **`:425-475`** (the scan-set section) still reads correctly now that a write FOLDER exists
distinct from the scan set — that section currently describes one concept where there are two.

Then ADD: the scan-set/write-folder split, `lead_layout`'s two values, the derived Archive set,
`leads reconcile`'s report-first shape and its four never-moved classes, the never-clobber residual
under a concurrent `_cas_write` (documented, not closed — see Task 5's docstring), and the recovery
note that a `_merged/` restore still works unchanged (identity is the note NAME, so the restored note
is found in whatever folder it lands in).

- [ ] **Step 5: Update `.rulesync/rules/CLAUDE.md`, then regenerate**

Two edits, both in the invariants section:
- The `leads` passes paragraph gains `leads reconcile` beside `dedupe` and `expire`.
- The never-clobber paragraph gains one sentence: a **move** is not a write to a note's bytes, so
  reconcile upholds never-clobber and never-regress by construction rather than by a check.

Then regenerate and verify no drift:

```bash
npm ci --ignore-scripts && npm run rulesync
git status --porcelain   # CLAUDE.md/AGENTS.md/.claude are gitignored; the tree must stay clean
```

**`.rulesync/rules/CLAUDE.md` is human-gated.** Two claims in it already describe PR A and were
merged with CodeRabbit approval but no separate human read. Flag both the PR A claims and these new
edits explicitly in the PR body for the user's review rather than treating them as settled.

- [ ] **Step 6: Mark the design spec's PR B section shipped**

In `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md`, update the `Status` line and add a
dated note recording the three decisions settled during planning (5, 6, 7 above) and the stale
premises found — the issue body's three, and the `#1 lands a second store` misreading. Do not rewrite
the original text; append, the way the #5 spec's SUPERSEDED note does.

- [ ] **Step 7: Run everything**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: previous total **+3**, ruff clean, and `git status --porcelain` clean after rulesync.

- [ ] **Step 8: Commit**

```bash
git add tests/test_lead_layout_invariants.py docs/ARCHITECTURE.md \
        .rulesync/rules/CLAUDE.md docs/superpowers/specs/2026-08-01-vault-subfolders-design.md
git commit -m "$(cat <<'EOF'
test: re-assert the invariants with the layout on (#1)

No new Store-contract property -- folders are a vault mechanism, so per the #48
ruling this stays implementation detail rather than a conformance guarantee. What
it does add is the vault-level proof that the EXISTING properties still hold with
lead_layout enabled: #81 non-resurrection now that the write folder is a subfolder
of leads_dir and no longer sits beside `_merged/`; never-clobber across a move; and
never-regress, since reconcile writes directory entries and nothing else.

PR A's lesson is why these exist at all: test_slug_is_issued_stable_and_unique
asserted a property the recursive scan broke and still passed, because its fixture
could never collide.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

### Task 9: The two #8 deferrals, on a corrected premise

The design lists these as landing in PR B. **Their stated trigger is false** and must be corrected
rather than acted on as written: `sluice/cli.py:674`, `:686`, `:735` and
`docs/superpowers/plans/2026-07-31-sluice-init-v2.md:258` all say issue #1 lands "a real second
store" / "the store seam". Verified against `gh issue view 1`: #1 is vault subfolders. It adds no
store. Leaving those comments would send the next reader looking for a second store that will never
arrive, and would leave the real deferral with no trigger at all.

**Files:**
- Modify: `sluice/cli.py` (`:668-689`, `:733-738`)
- Modify: `sluice/triage/prompt.py` (`:113-148`)
- Modify: `tests/test_prompt.py`
- Modify: `docs/superpowers/plans/2026-07-31-sluice-init-v2.md:258`

**Item 1 — `cmd_init`'s display-only path join. KEEP the code, CORRECT the prose.** The reasoning
for not inventing `Store.display_location()` is sound and unchanged: it is API surface for one
implementation, which is the premature abstraction this codebase keeps removing (and which Task 6
declines again for `reconcile_layout`). Only the "#1 will trigger this" claim is wrong.

**Item 2 — `triage/prompt.py`'s test-only surface. DELETE it.** Enumerated:
`build_system_prompt_from` is what `triage/engine.py:92` calls; `SYSTEM_PROMPT` is live
(`triage/judge.py:10,51`) but is built with `build_system_prompt(None)`, which never touches a
filesystem; `load_criteria(<a real dir>)` and `build_system_prompt(<a real dir>)` have **no
non-test caller**. Those two paths are the last filesystem reach left in the judge module — exactly
what the store-seam refactor removed from the engine — so keeping them preserves the trap that
refactor closed. The house precedent is to delete inert surface (`existing_keys` was removed from
the protocol; `track/receipt.py` deleted its unreachable guard).

- [ ] **Step 1: Re-verify the enumeration before deleting anything**

```bash
grep -rn "load_criteria\|build_system_prompt\b" sluice/ tests/ --include="*.py"
```

Expected: `build_system_prompt` appears only at its definition, at `SYSTEM_PROMPT =
build_system_prompt(None)`, and in `tests/test_prompt.py`. `load_criteria` appears only at its
definition, inside `build_system_prompt`, and in `tests/test_prompt.py`. **If any other caller
exists, stop and re-plan this item** — the deletion premise is the enumeration.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_prompt.py`:

```python
def test_the_prompt_module_no_longer_reaches_a_filesystem():
    """The store-seam refactor moved the judge off `build_system_prompt(vault.dir)` and onto
    `build_system_prompt_from(store.read_criteria())`, because reaching THROUGH the store to a
    path is what put a store-implementation detail on the judge's critical path. The
    directory-taking forms survived as test-only surface, which keeps that trap available to
    the next caller. Asserted on the MODULE, so a re-introduction under any name is caught."""
    import sluice.triage.prompt as prompt
    assert not hasattr(prompt, "load_criteria")
    assert not hasattr(prompt, "build_system_prompt")
    assert prompt.SYSTEM_PROMPT, "the baked-in default prompt must survive"
    src = open(prompt.__file__, encoding="utf-8").read()
    assert "open(" not in src, "triage/prompt.py opened a file"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_prompt.py::test_the_prompt_module_no_longer_reaches_a_filesystem -v`
Expected: FAIL — `assert not hasattr(prompt, "load_criteria")`

- [ ] **Step 4: Delete the surface**

**Rewrite `tests/test_prompt.py`'s module-level import FIRST**, before deleting anything. Line `:2-3`
binds `build_system_prompt`, `load_criteria` and `_CRITERIA_RELPATH`; deleting the names without
touching that line takes the WHOLE FILE red at collection — including
`test_shipped_prompt_expresses_no_role_or_culture_preference` (`:25`) and
`test_default_prompt_has_mechanics_but_no_opinion` (`:14`), the two shipped-prompt **neutrality
guards**. Those two must survive **verbatim**; only the import line changes. Re-run both by node id
after the edit and confirm they pass.

In `sluice/triage/prompt.py`: delete `load_criteria` and `build_system_prompt` entirely, drop the
now-unused `import os`, and replace the `SYSTEM_PROMPT` line.

**KEEP `_CRITERIA_RELPATH`.** The first draft said "drop it if nothing else reads them (check
first)" — checking finds that something does:
`tests/test_vault_write_document.py:46` asserts `prompt_mod._CRITERIA_RELPATH is CRITERIA_RELPATH`, a
shipped drift pin between two modules. Half-deleting a drift pin is a guard change, not maintenance,
and the pin costs one line.

Also rewrite the **module docstring**: its paragraph 2 states as a mechanism that criteria are
"loaded from the Obsidian vault at `Job Applications/Judging Profile.md`" *by this module*. After the
deletion that loading lives only in `core/vault.py:read_criteria`, so the sentence describes code the
module no longer contains — beside a neutrality claim, in the one place a reader checks the property.
Say instead that criteria arrive as TEXT from the store
(`build_system_prompt_from(store.read_criteria())`) and that this module never reaches a filesystem.
Keep the neutrality paragraph verbatim.

```python
# The baked-in default prompt -- `judge()`'s default, and what any caller importing the constant
# gets. Built from the shipped criteria directly rather than through a vault_dir-taking helper:
# the engine reads criteria through the STORE (`build_system_prompt_from(store.read_criteria())`,
# triage/engine.py), and the directory-taking forms that used to live here had no non-test caller
# left. Keeping them kept alive the exact reach-through-the-store-to-a-path shape the seam
# refactor removed, on a module a second store must never make assumptions about.
SYSTEM_PROMPT = build_system_prompt_from(_DEFAULT_CRITERIA)
```

Update `tests/test_prompt.py`: the tests importing the deleted names must move to
`build_system_prompt_from` with criteria text, or go if `build_system_prompt_from`'s own tests
already cover the property. **Do not delete a test that asserts a property nothing else asserts** —
check `test_prompt.py:43,44,50,53,61` one at a time and re-express each against
`build_system_prompt_from` / `Vault.read_criteria` rather than dropping it.

The successors, enumerated so each removal is a decision rather than a loss (all verified present):

| Property | Successor that already holds it |
| --- | --- |
| missing criteria file → neutral default | `tests/conformance/test_store_contract.py::test_read_criteria_abstains_when_unset` |
| empty criteria → neutral default | `test_prompt.py::test_build_system_prompt_from_abstains_when_criteria_are_absent` |
| frontmatter stripped | `test_prompt.py::test_build_system_prompt_from_strips_frontmatter` |
| HTML / `%%` comments stripped | `tests/test_onboard_profile.py` (through `build_system_prompt_from`) |
| user criteria reach the judge verbatim | `tests/e2e/test_init_to_verdicts.py` |

`test_missing_vault_file_falls_back_to_default` (`:43-44`) is the only test of `load_criteria`'s
`except OSError` arm; its successor is the first row, and that is the one to name explicitly in the
commit rather than leave implied.

- [ ] **Step 5: Correct the false-premise comments**

`sluice/cli.py:674` — replace the trailing sentence:

```python
    # `.init-scaffold.md` they never needed. (This used to say #1 would land that store; it does
    # not -- #1 is the vault's folder LAYOUT and adds no store. The seam is real either way, and
    # reading through it is right regardless of when a second implementation arrives.)
```

`sluice/cli.py:686` — replace:

```python
    # Not fixed by adding a `Store.display_location()`: that is API surface invented for one
    # implementation, which is the premature abstraction this codebase keeps removing (#1's
    # `reconcile_layout` declines the same thing, and `ensure_stfolder` was moved OUT of the
    # protocol for it). The trigger is a SECOND STORE with a concrete opinion about what a user
    # should be shown -- not any particular issue number. #1 was named here and was the wrong
    # guess: it ships the vault's folder layout and no store at all.
```

`sluice/cli.py:735` — replace `and #1 makes the second store real rather than hypothetical` with
`and the seam is the contract regardless of how many implementations exist today (#1 was named
here as the thing that would make a second store real; it does not -- it is the vault's folder
layout)`.

`docs/superpowers/plans/2026-07-31-sluice-init-v2.md:258` — append to the sentence:
`(CORRECTION, 2026-08-02: #1 is the vault's folder LAYOUT, not the store seam. It ships no second
store, so this row still runs once.)`

- [ ] **Step 6: Run everything**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check sluice tests scripts`
Expected: green, ruff clean. Reconcile the count against the previous total plus whatever
`tests/test_prompt.py` nets — deletions may reduce it, which is expected here and must be
explained in the commit rather than absorbed silently.

- [ ] **Step 7: Witness the mutants**

| Mutation | Test that must redden |
| --- | --- |
| Re-add `def load_criteria(vault_dir): return _DEFAULT_CRITERIA` **(a marked ADD exception: the property under test is that the surface does not exist)** | `test_prompt.py::test_the_prompt_module_no_longer_reaches_a_filesystem` (the `hasattr` half) |
| Re-add a `load_criteria` that actually calls `open(...)` **(the same ADD exception)** | `test_prompt.py::test_the_prompt_module_no_longer_reaches_a_filesystem` (the `"open(" not in src` half — a *different* assertion, and the row above does not witness it) |
| DELETE the `or _DEFAULT_CRITERIA` fallback from `build_system_prompt_from` | `test_prompt.py::test_build_system_prompt_from_abstains_when_criteria_are_absent` |

**Not in this table, and the reason matters.** The first draft's second row was *"change
`SYSTEM_PROMPT` to `build_system_prompt_from("")`"* — a **proven equivalent mutant**:
`build_system_prompt_from` falls back to `_DEFAULT_CRITERIA` on empty input, so the two produce
byte-identical output (measured, 7366 chars, equal). Its hedge (*"confirm one does, and if none does,
add it"*) would have sent the implementer writing a NEW test to catch a mutation that changes
nothing. The `or _DEFAULT_CRITERIA` DELETE above is the honest mutant for that property.

- [ ] **Step 8: Commit**

```bash
git add sluice/cli.py sluice/triage/prompt.py tests/test_prompt.py \
        docs/superpowers/plans/2026-07-31-sluice-init-v2.md
git commit -m "$(cat <<'EOF'
refactor(triage): drop prompt.py's test-only filesystem surface (#1)

`load_criteria(vault_dir)` and `build_system_prompt(vault_dir)` had no non-test
caller -- the engine calls `build_system_prompt_from(store.read_criteria())` and
SYSTEM_PROMPT is built from the shipped criteria directly. They were the last
filesystem reach in the judge module, i.e. exactly the
reach-through-the-store-to-a-path shape the seam refactor removed, kept alive and
available to the next caller. Enumerated before deleting.

Also corrects a false premise the #8 work left in four places: cli.py:674, :686,
:735 and the sluice-init plan all claim issue #1 lands "a real second store" / "the
store seam". Verified against the issue: #1 is the vault's folder LAYOUT and adds
no store, so that deferral trigger never fires. The REASONING for not inventing
`Store.display_location()` is unchanged and kept -- it is API surface for one
implementation, which is what this PR's own `reconcile_layout` declines too. Only
the trigger is corrected: a second store with a concrete opinion, not an issue
number.

Refs #1

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Nu2yM6Xm77aLLCekDegEYM
EOF
)"
```

---

## Before pushing

Per the standing cadence — **`/review-pr` runs BEFORE the branch is pushed, not after the PR opens.**
CodeRabbit is the scarce resource (~1h per attempt, and `dismiss_stale_reviews_on_push` makes every
post-review fix a fresh cycle); the specialist team is free and parallel.

- [ ] `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- [ ] `.venv/bin/python -m pytest` — full suite green, count reconciled against the per-task totals
- [ ] `.venv/bin/python -m ruff check sluice tests scripts` — clean
- [ ] `npm ci --ignore-scripts && npm run rulesync` — no drift
- [ ] Run each new test file BOTH alone and in the full suite (a `sys.modules`-sensitive assertion can
      be green alone and red in-suite, and the reverse)
- [ ] `/review-pr` — the five specialists plus the CodeRabbit CLI pass
- [ ] Fix everything found. **Sequence corrections BEFORE approval**: a post-approval push dismisses
      the approval, and a thread reply is what re-triggers it.
- [ ] PR body: flag `.rulesync/rules/CLAUDE.md` for the user's read (PR A's two claims plus Task 8's
      edits — human-gated, and merged so far on CodeRabbit approval alone); note the CodeQL outcome
      on Task 4's extracted move helper; record the three decisions settled during planning and the
      four stale premises corrected.
- [ ] `Closes #1` on the **PR body only** — every commit says `Refs #1`. PR A used `Refs`, which is
      why the issue is still open; putting the closing keyword on a mid-stack commit (Task 7, with
      Tasks 8 and 9 still to come) would make a commit that is not the last word on the issue read
      as if it were.

## Self-review notes

Checked against `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md`'s **PR B** section:

| Spec requirement | Task |
| --- | --- |
| `lead_layout` root-`Config` field, `""` default | 2 |
| Unknown value raises, lists valid names | 1 (map), 2 (constructor) |
| `sluice.yaml.example`, COMMENTED | 2 |
| Deliberately NOT in `sluice init`'s catalogue | 2 — nothing is added to `sluice/onboard/questions.py`; a fresh install has no notes to organise |
| `core/status.py` gains `is_terminal` | 1 |
| Archive = `dismiss` + every terminal, derived | 1 (+ the enumeration guard) |
| Non-canonical status never moved, `unknown` bucket | 1, 5 |
| `leads reconcile` reports by default, `--apply` moves, no `--dry-run` | 7 |
| Move primitive = `O_EXCL`-reserve + `os.replace`, reconcile REFUSES a collision | 4 |
| Reconcile does NOT refuse a `pending_cv` hold | 5 — no sign-off check exists anywhere in `reconcile_layout`; a move discards nothing and breaks no pointer |
| Two new mutation witnesses (hand-listed statuses; dropped collision refusal) | 1 (`test_the_archive_set_is_derived_from_status_not_hand_listed`), 4 (`test_a_collision_raises_when_suffixing_is_off`) |
| No new Store-contract property; the existing one holds with the layout on | 6 (facade, not protocol), 8 |
| The two #8 deferrals | 9 — landed, on a corrected premise |

## Revision 2 — what `/review-plan` changed (2026-08-02)

Five specialists reviewed revision 1; the test-engineer applied Tasks 1–5 to an isolated copy and
ran them. **40 findings.** Everything below was a defect in revision 1, not a nit:

| Fixed | Found by |
| --- | --- |
| `_managed_dirs` never yielded the leads-dir root under `active_archive`, so **migration filed nothing and `--apply` exited 0** — the feature inert on the only vault shape an opting-in user has. The root is not derivable; it is now its own term. | 4 routes (invariant, reviewer, test-engineer, architect) + my own probe |
| **"Never-clobber by construction" was false.** A move racing `_cas_write`'s `os.replace(tmp, path)` re-creates the source path → two notes at one basename → permanent `upsert` refusal. Now stated as a residual and REPORTED by the run that causes it. | invariant |
| Task 5 would have tripped the shipped guard `test_no_module_indexes_a_lead_list_by_slug_by_hand`, whose docstring names `leads reconcile` as its anticipated fifth consumer. Now uses `index_by_slug`, whose `dropped` return IS the ambiguous bucket. | test-engineer, architect |
| The collision test could not reach the collision arm — two lead notes at one basename are slug twins, so `ambiguous` consumed them first. `suffix_on_collision=False` had no reconcile-level witness. Blocker is now a non-lead file. | test-engineer, reviewer |
| `upsert`'s makedirs sits above the update/merge fan-out, so repointing it minted an empty `Active/` on every `last_seen` bump. Now made on the create arm only. | invariant, test-engineer, architect |
| Decision 7 lived only in the CLI, so `Sluice.reconcile()` disagreed with `sluice leads reconcile`. Now an early return in the store. | invariant, test-engineer, architect |
| `test_apply_leaves_the_scan_set_cache_truthful` was measured GREEN with the guard deleted — it certified a guard it could not falsify. Deleted; the call is kept as honestly-labelled hygiene. | test-engineer (confirming revision 1's own flagged risk) |
| Every `Lead(...)` fixture omitted the required `search` field — ten test bodies would have errored before any assertion. | test-engineer, architect |
| Task 3's refusal fixture reached `merged`, not `refused` (an empty incoming location is UNKNOWN, which terminates the walk). | test-engineer |
| Task 1 collected 20 items, not 11 — parametrize multiplies — leaving all eight cumulative totals 9 low. Absolute totals replaced with per-task deltas, which cannot rot. | reviewer, test-engineer |
| Task 9's second mutant was byte-identical to the original; nothing witnessed the `"open(" not in src` half. | test-engineer |
| `_CRITERIA_RELPATH` has a live drift pin at `tests/test_vault_write_document.py:46`; "drop it if nothing reads it" would have half-deleted a guard. | test-engineer |
| Deleting `prompt.py`'s names without rewriting `test_prompt.py`'s import takes two **neutrality guards** red at collection. | neutrality, test-engineer |
| `StoreHasNoLayout` propagated as an uncaught traceback, so the capability check did not deliver its stated benefit. Now rc 2 with a sentence. | architect |
| `ARCHITECTURE.md:543` and `:610` both promise reconcile will *repair* an ambiguous slug — decision 5 reverses it, and Task 8 did not name them. | architect |
| A hardcoded place-name literal where `tests/conftest.py:LOCATIONS` exists for exactly that. | neutrality |
| The abstain guards sat in the feature file, not `tests/test_sluice_neutral_defaults.py` — the file the docs and review agents point at as THE neutrality guard. | neutrality |
| `python -m sluice` does not exist (no `__main__.py`); `docs+test:` is not a Conventional Commit type; `Closes #1` sat on a mid-stack commit; the unset-`--json` arm hand-copied the report shape. | reviewer, invariant |

**Validated, no change:** the Store-protocol decision — the architect gave a sharper reason than
revision 1's, that `merge_cluster` is on the contract because #81 non-resurrection is a
store-agnostic *obligation* a tombstone can satisfy, whereas folders are a mechanism carrying no such
obligation. Also validated: the `getattr` discriminator is genuinely seam-witnessable (unlike
`receipt.py`'s deleted branch); no import cycle for `core/leads.py` → `core/status.py`;
`_write_folder`'s `"new"` probe matches the rendered frontmatter; and `_reserve_and_move` writes no
lead content, so the CodeQL exposure is low.

Open items carried into implementation:

1. **Task 4's CodeQL exposure.** A new module-level file-creating function may re-flag baseline
   behaviour. If it fires, consolidate (as #16 did); do not dismiss. Record the outcome either way.
2. **Task 5 Step 3a.** Search for a case where the stale and fresh scan sets give different verdicts
   after an applied sweep. If none exists, ship no test for it — the comment already says it is
   hygiene.
3. **Decisions 5, 6 and 7** were settled during planning (5 and 6 by the user; 7 by the
   `lead_ttl_days` precedent). Flag all three in the PR body.
