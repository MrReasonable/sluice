# Lead Staleness (#9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give leads a notion of age — a `lead_ttl_days` knob, a `sluice leads expire` command that dismisses stale triage-owned leads, and staleness refusals in `cv run` and `apply prep`.

**Architecture:** One frozen `StalenessPolicy` value object in `core/leads.py`, built once by `Sluice.staleness()` from the root config plus the `today` collaborator, and passed whole to three consumers. Never-regress is held by a new `require_status` parameter on `Vault.update_fields` that re-reads status **inside** the CAS transform.

**Tech Stack:** Python 3.12+, stdlib only, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-27-lead-staleness-design.md` (approved, twice reviewed).

## Global Constraints

- **Stdlib only in `sluice/`.** No new runtime dependency. `yaml` only under the existing guarded import.
- **Empty config abstains.** `lead_ttl_days` defaults to `0`; an unconfigured install expires nothing and refuses nothing.
- **Never-regress.** `expire` writes only `dismiss`, only from `{new, shortlist, research, needs_review}`, and the status check happens on **fresh** content inside the CAS transform.
- **No personal data** in `sluice/` or `tests/`. New fixtures use the `example.invalid` family and neutral title literals (`Example Role`). Do NOT introduce new company names outside that family; pre-existing placeholder names elsewhere in `tests/` are out of scope for this change.
- **Comments explain why** — match the surrounding density; several existing comments encode real incidents.
- **Conventional Commits.** `feat(core):`, `feat(cv):`, `test(apply):`, `docs:`.
- **Verification after every task:** `python -m pytest` (all green, offline, ~2s) and `ruff check sluice tests` (ruff==0.15.21).
- **`.rulesync/` is NOT edited.** It is canonical and human-gated.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `sluice/core/leads.py` | **Modify.** Add `StalenessPolicy` — the pure age rule. |
| `sluice/core/config.py` | **Modify.** Add `lead_ttl_days` field + loader validation. |
| `sluice/core/vault.py` | **Modify.** `update_fields` gains `require_status`, returns `bool`. |
| `sluice/core/protocols.py` | **Modify.** Store contract for the above. |
| `sluice/core/app.py` | **Modify.** `Sluice.staleness()`, `expire_report()`, `expire()`; thread the policy into `compose_cv` and `prep`. |
| `sluice/cv/engine.py` | **Modify.** The `skipped-stale` gate in `run_one`. |
| `sluice/apply/select.py` | **Modify.** The `stale` reason in `eligibility`. |
| `sluice/apply/engine.py` | **Modify.** Thread the policy through `prep_one`/`preview_all`. |
| `sluice/cli.py` | **Modify.** `leads expire` subcommand; `--include-stale` on `cv run` and `apply prep`. |
| `sluice.yaml.example` | **Modify.** The new root key, commented out. |
| `docs/ARCHITECTURE.md` | **Modify.** Composition-root ops, `leads` group, the `today` claims at `:277-317`. |
| `tests/test_lead_staleness.py` | **Create.** Policy unit tests. |
| `tests/test_leads_expire.py` | **Create.** Expire behaviour + CLI parse layer. |
| `tests/conformance/test_store_contract.py` | **Modify.** `require_status` contract case. |
| `tests/test_sluice_neutral_defaults.py` | **Modify.** Both default halves + the example-file root-key guard. |
| `tests/test_cv_engine.py`, `tests/test_apply_*.py`, `tests/test_app_injection.py` | **Modify.** Gate + wiring tests. |

---

### Task 1: The `StalenessPolicy` value object

**Files:**
- Modify: `sluice/core/leads.py`
- Test: `tests/test_lead_staleness.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `StalenessPolicy(ttl_days: int = 0, today: str = "", include_stale: bool = False)` with `days(last_seen: str) -> int | None`, `is_stale(last_seen: str) -> bool`, `blocks(last_seen: str) -> bool`. Frozen dataclass. Raises `TypeError` at construction if `today` is not a `str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lead_staleness.py`:

```python
"""#9: the pure age rule. Every case here is a mutation target -- see the witness
table in docs/superpowers/specs/2026-07-27-lead-staleness-design.md."""
import pytest

from sluice.core.leads import StalenessPolicy

TODAY = "2026-07-27"


def _p(ttl=0, today=TODAY, include_stale=False):
    return StalenessPolicy(ttl_days=ttl, today=today, include_stale=include_stale)


def test_older_than_ttl_is_stale():
    assert _p(ttl=90).is_stale("2026-01-01") is True


def test_exactly_ttl_days_old_is_not_yet_stale():
    # Strictly greater. The boundary is a mutation target (`>` -> `>=`).
    assert _p(ttl=90).is_stale("2026-04-28") is False      # exactly 90 days
    assert _p(ttl=90).is_stale("2026-04-27") is True       # 91 days


def test_unconfigured_ttl_abstains_on_an_ANCIENT_lead():
    # The fixture MUST be ancient. With last_seen == today the surviving expression
    # after deleting the `ttl_days <= 0` guard is `0 > 0` -> False, and the mutant
    # lives. This is the 672ad2a blast radius; the witness has to be able to fire.
    assert _p(ttl=0).is_stale("2020-01-01") is False


def test_negative_ttl_abstains():
    assert _p(ttl=-1).is_stale("2020-01-01") is False


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2026-13-01"])
def test_unparseable_or_absent_last_seen_abstains(bad):
    # A missing date is not evidence of age. Notes predating the field exist.
    assert _p(ttl=90).days(bad) is None
    assert _p(ttl=90).is_stale(bad) is False


def test_unparseable_today_abstains_rather_than_raising():
    assert _p(ttl=90, today="garbage").is_stale("2020-01-01") is False


def test_future_last_seen_is_not_stale():
    assert _p(ttl=90).is_stale("2027-01-01") is False


def test_include_stale_makes_blocks_false_while_is_stale_stays_true():
    p = _p(ttl=90, include_stale=True)
    assert p.is_stale("2020-01-01") is True
    assert p.blocks("2020-01-01") is False


def test_blocks_is_true_for_a_stale_lead_by_default():
    assert _p(ttl=90).blocks("2020-01-01") is True


def test_default_policy_abstains():
    # A call site that forgets to pass a policy must fail SAFE.
    assert StalenessPolicy().is_stale("2020-01-01") is False


def test_today_must_be_a_string_and_says_why():
    # `Sluice.today` is a zero-arg CALLABLE. Binding it unwrapped would give
    # date.fromisoformat(<function>) -> TypeError deep in a gate; fail at construction.
    with pytest.raises(TypeError, match="callable"):
        StalenessPolicy(ttl_days=90, today=lambda: TODAY)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lead_staleness.py -q`
Expected: FAIL — `ImportError: cannot import name 'StalenessPolicy'`.

- [ ] **Step 3: Implement the policy**

In `sluice/core/leads.py`, add `from datetime import date` to the imports if absent, and append:

```python
@dataclass(frozen=True)
class StalenessPolicy:
    """The staleness rule in force for ONE invocation (#9), built once in `Sluice` and
    passed whole to cv, apply and expire so none of them can disagree about it.

    The default abstains. A call site that forgets to pass a policy gets ttl_days=0 and
    therefore never marks anything stale -- fail-safe is the only acceptable direction
    here, because the failure this guards is binning a lead the user still wants.
    """
    ttl_days: int = 0
    today: str = ""
    include_stale: bool = False

    def __post_init__(self):
        # `Sluice`'s `today` collaborator is a zero-arg CALLABLE (ingest/sink.py does
        # `today or _today` then calls it), so the tempting `today=self._today` binds a
        # FUNCTION here. That would reach date.fromisoformat(<function>) -> TypeError,
        # which the ValueError guard below does not catch, turning the designed abstain
        # into a traceback on `cv run`/`apply prep`/`leads expire`. Fail at construction
        # naming the fix instead -- the house rule for a wrong-type collaborator.
        if not isinstance(self.today, str):
            raise TypeError(
                "StalenessPolicy.today must be an ISO date string, got "
                f"{type(self.today).__name__}: Sluice's `today` is a zero-arg callable, "
                "so call it (`clock()`) rather than binding it")

    def days(self, last_seen: str) -> int | None:
        """Whole days from `last_seen` to `today`; None when EITHER is absent or
        unparseable. `today` is parsed under the same guard as `last_seen`: a bad
        injected clock must abstain for the same reason bad stored data must."""
        try:
            then = date.fromisoformat((last_seen or "").strip())
            now = date.fromisoformat((self.today or "").strip())
        except ValueError:
            return None
        return (now - then).days

    def is_stale(self, last_seen: str) -> bool:
        """Strictly older than the TTL. `<= 0` (not `== 0`) so a hand-built negative
        abstains rather than expiring the entire vault."""
        if self.ttl_days <= 0:
            return False
        d = self.days(last_seen)
        return d is not None and d > self.ttl_days

    def blocks(self, last_seen: str) -> bool:
        """The single question the cv and apply gates ask, so neither can implement the
        `--include-stale` override differently."""
        return self.is_stale(last_seen) and not self.include_stale
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lead_staleness.py -q` → PASS (12 tests)
Then: `python -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/leads.py tests/test_lead_staleness.py
git commit -m "feat(core): add StalenessPolicy, the pure lead-age rule (#9)"
```

---

### Task 2: The `lead_ttl_days` config knob

**Files:**
- Modify: `sluice/core/config.py`, `sluice.yaml.example`
- Test: `tests/test_sluice_neutral_defaults.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.lead_ttl_days: int` (default `0`); `load_config` raises `ValueError` on `bool`, non-int, or negative.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sluice_neutral_defaults.py`:

```python
def test_lead_ttl_days_dataclass_default_is_off():
    # #9. NOT covered by the #26/#63 sweep: that guard is value-keyed on LIST-defaulting
    # fields because "empty list == abstain" is universal. `0 == abstain` is not universal
    # for ints -- the dossier-cache `ttl_days: int = 7` is a legitimate non-zero default --
    # so widening the sweep would false-positive on it. This knob needs its own guard.
    assert Config().lead_ttl_days == 0


def test_lead_ttl_days_loader_default_is_off(monkeypatch):
    # load_config names every field explicitly (no splat, no loop), so the loader default
    # is an INDEPENDENT literal the dataclass assertion above does not constrain.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_config().lead_ttl_days == 0


def test_lead_ttl_days_absent_key_abstains_rather_than_raising(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("store: vault\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config().lead_ttl_days == 0


def test_lead_ttl_days_configured_value_round_trips(tmp_path, monkeypatch):
    # Pins that a CONFIGURED value survives the loader. Every other config test here
    # pins the OFF state, which a permanently-zero knob would also satisfy.
    p = tmp_path / "c.yaml"
    p.write_text("lead_ttl_days: 90\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config().lead_ttl_days == 90


@pytest.mark.parametrize("value", ["yes", "on", "true", "True"])
def test_lead_ttl_days_rejects_yaml_booleans(tmp_path, monkeypatch, value):
    # bool subclasses int and PyYAML resolves yes/on/true to True, so a plain isinstance
    # check admits `lead_ttl_days: yes` -- the natural thing to type to turn the feature
    # ON -- as a ONE-DAY ttl: every lead stale, no error. Abstain inversion, 672ad2a class.
    p = tmp_path / "c.yaml"
    p.write_text(f"lead_ttl_days: {value}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_ttl_days"):
        load_config()


@pytest.mark.parametrize("value", ["-1", "'90'", "1.5", "[90]"])
def test_lead_ttl_days_rejects_non_negative_ints(tmp_path, monkeypatch, value):
    p = tmp_path / "c.yaml"
    p.write_text(f"lead_ttl_days: {value}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_ttl_days"):
        load_config()


def test_example_config_ships_lead_ttl_days_off():
    # sluice.yaml.example is COPIED VERBATIM by the quickstart, and this file's own
    # pay-floor block ships ACTIVE illustrative non-zero values -- so the nearest local
    # convention is the unsafe one. A copied non-zero silently switches on the cv and
    # apply refusals, neither of which is human-gated the way --expire is.
    text = pathlib.Path("sluice.yaml.example").read_text(encoding="utf-8")
    active = [ln for ln in text.splitlines()
              if ln.strip().startswith("lead_ttl_days:")]
    assert all(ln.split(":", 1)[1].strip() == "0" for ln in active), \
        "lead_ttl_days must ship commented out or 0 in sluice.yaml.example"
```

Add `import pathlib` and `import pytest` at the top of the file if not already present, and make sure `Config` and `load_config` are imported.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -q`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'lead_ttl_days'`.

- [ ] **Step 3: Implement**

In `sluice/core/config.py`, add to the `Config` dataclass after `dedupe_title_noise_words`:

```python
    # How many days since `last_seen` before a lead counts as stale (#9). 0 = OFF, and
    # that is the shipped default: staleness is a judgement, and a shipped non-zero
    # would bin leads on a stranger's idea of stale (the 672ad2a class). Root Config,
    # not a sub-app one -- expire, cv and apply all read it, and a staleness policy that
    # differed between them would be a bug. NB `ttl_days` (cv/config.py, triage/config.py)
    # is the unrelated DOSSIER CACHE ttl; this name is deliberately distinct.
    lead_ttl_days: int = 0
```

In `load_config`, before the `return Config(...)`:

```python
    # ABSENT is the abstain case, not an error. `bool` is checked FIRST because it
    # subclasses int: PyYAML resolves yes/on/true to True, so `lead_ttl_days: yes` --
    # what a user naturally types to turn this ON -- would otherwise load as a valid
    # int and set a ONE-DAY ttl, marking every lead stale with no error at all.
    raw_ttl = data.get("lead_ttl_days")
    raw_ttl = 0 if raw_ttl is None else raw_ttl
    if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, int) or raw_ttl < 0:
        raise ValueError(
            f"lead_ttl_days must be a non-negative integer (0 = off), got {raw_ttl!r}")
```

and add `lead_ttl_days=raw_ttl,` to the `Config(...)` call.

In `sluice.yaml.example`, near the top-level ingest keys, following the `locations` convention:

```yaml
# Days since a lead was last seen in a scrape before `sluice leads expire` proposes
# dismissing it, and before `cv run`/`apply prep` refuse to spend on it. 0 = OFF, which
# is the shipped default. This file is COPIED, so an active value here would hand every
# copier a staleness judgement they never made.
# lead_ttl_days: 90   # <- uncomment and set YOUR OWN
```

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -q` → PASS
Then: `python -m pytest -q && ruff check sluice tests` → all green.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_sluice_neutral_defaults.py
git commit -m "feat(core): add lead_ttl_days, defaulting off (#9)"
```

---

### Task 3: `update_fields(require_status=...)` — the never-regress guard

**Files:**
- Modify: `sluice/core/vault.py:231-251`, `sluice/core/protocols.py`
- Test: `tests/conformance/test_store_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Store.update_fields(ref, fields, *, append_note=None, note_tag=None, require_status: frozenset | None = None) -> bool`. Returns whether a write happened. Existing callers pass nothing and are unaffected.

- [ ] **Step 1: Write the failing test**

Append to `tests/conformance/test_store_contract.py` (matching the file's existing per-implementation parametrisation):

```python
def test_update_fields_require_status_abstains_on_fresh_mismatch(store_factory, tmp_path):
    """#9 never-regress: the status check must happen against the FRESH note inside the
    write, not against whatever the caller enumerated. A caller-side check on the
    in-memory LeadNote is byte-identical to NO check -- probed -- because the snapshot is
    stale by construction: expire's read loop is a window in which a lead can enter the
    application lifecycle via `apply record` or a #10 receipt."""
    store = store_factory(tmp_path)
    store.upsert(_lead(title="Example Role", company="Example Ltd"))
    note = store.read_leads()[0]

    # A lead that has since become application-owned must NOT be written.
    store.update_fields(note.ref, {"status": "applied"})
    wrote = store.update_fields(note.ref, {"status": "dismiss"},
                                require_status=frozenset({"shortlist", "new"}))
    assert wrote is False
    assert store.read_leads()[0].status == "applied"


def test_update_fields_require_status_writes_on_match(store_factory, tmp_path):
    store = store_factory(tmp_path)
    store.upsert(_lead(title="Example Role", company="Example Ltd"))
    note = store.read_leads()[0]
    wrote = store.update_fields(note.ref, {"status": "dismiss"},
                                require_status=frozenset({"new"}))
    assert wrote is True
    assert store.read_leads()[0].status == "dismiss"
```

Reuse the file's existing lead-building helper rather than defining a new one; if it is named differently, use that name.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/conformance/test_store_contract.py -q -k require_status`
Expected: FAIL — `TypeError: update_fields() got an unexpected keyword argument 'require_status'`.

- [ ] **Step 3: Implement**

Replace `sluice/core/vault.py`'s `update_fields` signature, docstring and transform:

```python
    def update_fields(self, ref, fields: dict, *,
                      append_note: str | None = None,
                      note_tag: str | None = None,
                      require_status: frozenset | None = None) -> bool:
        """Surgically set frontmatter keys (literal YAML scalars), body byte-for-byte
        intact. Optionally append a guarded note to relevance_notes (skipped if note_tag
        is present, so re-runs are idempotent). Routed through _cas_write: the edit is
        re-derived from the CURRENT note on each attempt, so a concurrent writer's other
        keys and body survive. May raise VaultConflict on sustained conflict (#16).

        `require_status` (#9): when given, re-read the status from the FRESH note and
        write nothing unless it is in that set. Returns whether a write happened.
        """
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            # Decided HERE, against the fresh bytes, and not by the caller. A caller
            # checking the LeadNote it enumerated checks a snapshot that is stale by
            # construction; probed against a real vault, that guard is byte-identical
            # to having no guard at all -- both write over an `applied` note. Returning
            # `text` unchanged is a genuine no-op, which _cas_write reports as False.
            if require_status is not None and \
                    _status.normalize(_fm_value(inner, "status")) not in require_status:
                return text
            for key, literal in fields.items():
                inner = _set_fm(inner, key, literal)
            if append_note and note_tag:
                current = _fm_value(inner, "relevance_notes")
                if note_tag not in current:
                    merged = (current + " " + append_note).strip()
                    inner = _set_fm(inner, "relevance_notes", f'"{merged}"')
            return f"---\n{inner}\n---\n{body}"
        return _cas_write(ref, transform)
```

In `sluice/core/protocols.py`, update the `Store.update_fields` stub's signature and docstring to match, adding:

```
        `require_status`, when given, is re-read from the FRESH stored note and the write
        is abstained (returns False, nothing written) if the status is not in that set.
        This CANNOT be delegated to the caller: a caller-side check reads a snapshot taken
        before the write and cannot see a concurrent entry into the application lifecycle.
        Returns whether a write happened.
```

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/conformance/test_store_contract.py -q` → PASS
Then: `python -m pytest -q && ruff check sluice tests` → all green (existing callers ignore the new return value).

- [ ] **Step 5: Commit**

```bash
git add sluice/core/vault.py sluice/core/protocols.py tests/conformance/test_store_contract.py
git commit -m "feat(core): add update_fields(require_status=) for fresh-status writes (#9)"
```

---

### Task 4: `Sluice.staleness()`

**Files:**
- Modify: `sluice/core/app.py`
- Test: `tests/test_app_injection.py`

**Interfaces:**
- Consumes: `StalenessPolicy` (Task 1), `Config.lead_ttl_days` (Task 2).
- Produces: `Sluice.staleness(*, include_stale: bool = False) -> StalenessPolicy`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_injection.py`:

```python
def test_staleness_policy_reads_config_and_calls_the_today_collaborator():
    """#9 wiring. Nothing else in the suite can see a dropped `ttl_days=` -- every other
    staleness test pins the OFF state, which a permanently-zero knob also satisfies."""
    s = Sluice(Config(lead_ttl_days=30), today=lambda: "2026-07-27")
    p = s.staleness()
    assert p.ttl_days == 30
    assert p.today == "2026-07-27"      # CALLED, not bound
    assert p.include_stale is False
    assert p.is_stale("2026-01-01") is True


def test_staleness_include_stale_is_per_invocation():
    s = Sluice(Config(lead_ttl_days=30), today=lambda: "2026-07-27")
    assert s.staleness(include_stale=True).blocks("2026-01-01") is False
    assert s.staleness().blocks("2026-01-01") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_app_injection.py -q -k staleness`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'staleness'`.

- [ ] **Step 3: Implement**

In `sluice/core/app.py`, import the policy alongside the other `core.leads` imports and add a method beside `dossier_cache` (it is composition-root state, not an adapter seam, so it does **not** go through `_resolve`):

```python
    def staleness(self, *, include_stale: bool = False) -> StalenessPolicy:
        """The #9 age rule for one invocation. Built HERE, once, so expire, cv and apply
        cannot disagree about it.

        `self._today` is a zero-arg CALLABLE, not a string -- VaultSink does
        `today or _today` and then calls it. It must be CALLED here: binding the function
        into the frozen policy would reach date.fromisoformat(<function>), a TypeError the
        policy's ValueError guard does not catch, turning the designed fail-safe abstain
        into a traceback on three commands. StalenessPolicy.__post_init__ refuses a
        non-str so that mistake fails loudly at construction rather than at first use.
        """
        clock = self._today or _today
        return StalenessPolicy(ttl_days=self.config.lead_ttl_days,
                               today=clock(),
                               include_stale=include_stale)
```

Import `_today` from `sluice.ingest.sink` **lazily inside the method** if it is not already available at module scope in `app.py`; otherwise define a local `def _today(): return date.today().isoformat()` near the top of `app.py`. Do not pull the ingest module to `app.py`'s module scope.

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_app_injection.py -q` → PASS
Then: `python -m pytest -q && ruff check sluice tests` → all green.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_app_injection.py
git commit -m "feat(core): build the staleness policy in the composition root (#9)"
```

---

### Task 5: `sluice leads expire`

**Files:**
- Modify: `sluice/core/app.py`, `sluice/cli.py`
- Test: `tests/test_leads_expire.py` (create)

**Interfaces:**
- Consumes: `Sluice.staleness()` (Task 4), `update_fields(require_status=)` (Task 3).
- Produces: `Sluice.expire_report() -> list[StaleLead]` and `Sluice.expire(slugs=None) -> list[tuple[str, str]]` where the outcome is one of `dismissed` / `refused-signoff` / `no-match` / `conflict` / `skipped`. `StaleLead` is a dataclass with `slug, status, last_seen, first_seen, days, flagged: list[str], refused: str | None`.

`_EXPIRABLE = frozenset({"new", "shortlist", "research", "needs_review"})`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leads_expire.py` covering, at minimum:

```python
def test_unset_ttl_reports_nothing_and_writes_nothing(...)
def test_stale_lead_is_reported_but_not_written_without_the_flag(...)
def test_expire_bulk_dismisses_the_reported_set(...)
def test_expire_named_slug_matches_EXACTLY_not_by_substring(...)
    # Two notes whose slugs are `Example Ltd - Example Role` and
    # `Example Ltd - Example Role Senior`. `--expire "Example Ltd - Example Role"`
    # must dismiss exactly one.
def test_unmatched_named_slug_is_reported_and_exits_non_zero(...)
def test_application_owned_lead_is_never_enumerated(...)
def test_a_lead_that_becomes_applied_MID_SWEEP_is_not_dismissed(...)
    # Uses tests/conftest.py::racing_read, installed so it fires on the ENUMERATION
    # read: racing_read returns PRE-edit bytes, so a racer installed later leaves even
    # a fresh-re-read guard seeing `shortlist`. This is the ONLY witness for
    # require_status; an in-memory note.status guard is byte-identical to no guard.
def test_pending_cv_lead_is_refused_by_bulk_and_by_name(...)
def test_needs_signoff_only_lead_is_NOT_refused(...)
    # Vault.sign_off no-ops without pending_cv, so refusing on needs_signoff alone
    # would strand the lead behind a message whose escape hatch does nothing.
def test_dismiss_status_lead_is_skipped(...)
def test_vault_conflict_on_one_lead_does_not_abort_the_sweep(...)
def test_audit_note_records_the_prior_status_and_is_idempotent_within_a_day(...)
def test_json_report_shape(...)
def test_cli_bare_expire_flag_writes(...)
    # THE PARSE-LAYER TEST. Every other test here sits at the Sluice.expire() level and
    # stays green through a broken parser. `nargs="*", default=None` + `is not None`.
```

Write each of these out fully with real fixtures before implementing. Build fixture notes through `Vault.upsert` so slugs are real store-issued filenames (`Example Ltd - Example Role`), not hand-written hyphenated strings.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_leads_expire.py -q`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'expire_report'`.

- [ ] **Step 3: Implement `Sluice.expire_report` / `Sluice.expire`**

Key points the code must honour, each of which has a test above:

- Read only `_EXPIRABLE` statuses, so application-owned notes are never enumerated.
- Pass `require_status=_EXPIRABLE` on every write. This is the guard that actually holds never-regress.
- Refuse a lead whose fresh frontmatter has `pending_cv` (report-only flags: `needs_signoff`, `tailored_cv`).
- Exact slug equality for named slugs; an unmatched name is an error the CLI turns into a non-zero exit.
- Catch `VaultConflict` per lead, count it, continue.
- Audit note: `f"[expire {today}] stale: last_seen {last_seen} is {days}d old (lead_ttl_days={ttl}). Was: {prior}."` with `note_tag=f"[expire {today}]"`.

- [ ] **Step 4: Implement `cmd_leads_expire` and the parser**

In `sluice/cli.py`, beside the `dedupe` parser:

```python
    ex = leads.add_parser("expire", help="report/dismiss leads stale past lead_ttl_days")
    # NOT dedupe's nargs="+": that REQUIRES an argument, so a bare `--expire` would be an
    # argparse error rather than the bulk case. And NOT dedupe's `if args.merge:` dispatch
    # either -- a bare `--expire` parses to a FALSY [], which would fall through to the
    # report branch and leave the write flag silently inert. `is not None` is the test.
    ex.add_argument("--expire", nargs="*", default=None, metavar="SLUG",
                    help='dismiss the reported leads; name slugs to narrow, e.g. '
                         '--expire "Example Ltd - Example Role"')
    ex.add_argument("--json", action="store_true", help="machine-readable report")
    ex.set_defaults(func=cmd_leads_expire)
```

`cmd_leads_expire` imports `Sluice` inside the function (the lazy-import rule), prints the unset-knob message when `config.lead_ttl_days <= 0`, and returns non-zero when a named slug matched nothing.

- [ ] **Step 5: Verify**

Run: `python -m pytest tests/test_leads_expire.py -q` → PASS
Then: `python -m pytest -q && ruff check sluice tests` → all green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_leads_expire.py
git commit -m "feat(core): add sluice leads expire, report-first and human-gated (#9)"
```

---

### Task 6: The cv guard

**Files:**
- Modify: `sluice/cv/engine.py`, `sluice/core/app.py`, `sluice/cli.py`
- Test: `tests/test_cv_engine.py`, `tests/test_app_injection.py`

**Interfaces:**
- Consumes: `StalenessPolicy`, `Sluice.staleness()`.
- Produces: `CvResult.status == "skipped-stale"`; `run_one(..., policy: StalenessPolicy = StalenessPolicy())`; `run_batch(..., policy=...)`; `Sluice.compose_cv(..., include_stale=False)`; `sluice cv run --include-stale`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stale_lead_is_skipped_before_any_dossier_fetch(...):
    # The ONLY witness for the placement decision. A RecordingCache wrapping the shape of
    # tests/test_cv_engine.py's FakeCache (run_one's 5th positional) asserts
    # get_or_build was never called. Every `skipped-stale` assertion stays green if the
    # check is moved below get_or_build; this one does not.
def test_stale_lead_is_skipped_in_run_batch(...)
def test_include_stale_composes_a_stale_lead(...)
def test_held_and_stale_lead_still_reports_skipped_needs_signoff(...)
    # The check sits AFTER the #60 latch, so it is strictly additive.
def test_compose_cv_skips_stale_through_the_Sluice_layer(...)
    # WIRING. Sluice(Config(lead_ttl_days=30), store=..., backend=..., renderer=...,
    # today=lambda: "2026-07-27") -> compose_cv gives skipped-stale. Calling run_one
    # directly with an explicit policy cannot catch a dropped `policy=` in compose_cv.
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cv_engine.py -q -k stale` → FAIL.

- [ ] **Step 3: Implement**

In `cv/engine.py`, add `policy: StalenessPolicy = StalenessPolicy()` to `run_one` and `run_batch`, extend `CvResult`'s docstring with `skipped-stale`, and insert immediately **after** the `pending_cv` latch and **before** `dossier_cache.get_or_build`:

```python
    # #9: refuse before ANY spend. Placed after the #60 latch so it is strictly additive
    # -- a held lead still reports skipped-needs-signoff -- and before get_or_build so a
    # stale lead costs neither a fetch nor a compose. `blocks`, never `is_stale`: that is
    # what makes --include-stale one decision rather than two.
    if policy.blocks(fm.get("last_seen", "")):
        return CvResult(note.ref, "skipped-stale")
```

`run_batch` forwards `policy=policy` to `run_one`. `Sluice.compose_cv` gains `include_stale=False` and passes `policy=self.staleness(include_stale=include_stale)` to both branches. `cli.py`'s `cv run` gains `--include-stale` and threads it.

- [ ] **Step 4: Verify** — `python -m pytest -q && ruff check sluice tests` → all green.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/engine.py sluice/core/app.py sluice/cli.py tests/test_cv_engine.py tests/test_app_injection.py
git commit -m "feat(cv): refuse a stale lead before any dossier or compose spend (#9)"
```

---

### Task 7: The apply guard

**Files:**
- Modify: `sluice/apply/select.py`, `sluice/apply/engine.py`, `sluice/core/app.py`, `sluice/cli.py`
- Test: `tests/test_apply_select.py` (or the existing apply test module), `tests/test_app_injection.py`

**Interfaces:**
- Consumes: `StalenessPolicy`, `Sluice.staleness()`.
- Produces: `eligibility(note, cfg, policy=StalenessPolicy()) -> (bool, str)` with the new reason `"stale"`; `select_one`/`select_all`/`prep_one`/`preview_all` all take `policy=`; `Sluice.prep(..., include_stale=False)`; `sluice apply prep --include-stale`.

- [ ] **Step 1: Write the failing tests**

```python
def test_eligibility_refuses_a_stale_lead(...)
def test_select_all_reports_stale(...)
def test_dry_run_and_real_run_BOTH_report_stale(...):
    # Assert the shared OUTCOME, not merely that the two agree: "they agree" is also
    # satisfied by the both-inert state, where dropping the policy at core/app.py:630
    # AND :635 leaves both saying "staged". Sluice.prep has THREE branches into
    # selection -- :628 preview_all, :630 select_one DIRECT (dry-run), :635 prep_one.
def test_include_stale_stages_a_stale_lead(...)
def test_prep_skips_stale_through_the_Sluice_layer(...)   # WIRING
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

In `apply/select.py`:

```python
def eligibility(note, cfg, policy=StalenessPolicy()):
    """(ok, reason). reason in {'', not_shortlist, no_url, no_artifact, missing_file,
    stale}. The default policy abstains, so a call site that forgets to thread one
    fails SAFE rather than refusing every lead."""
    if not _status.can_apply(note.status):
        return False, "not_shortlist"
    # #9: before the artifact checks -- a stale lead should read as stale, not as
    # `no_artifact`, which would send the user off to re-run cv.
    if policy.blocks(note.fm.get("last_seen", "")):
        return False, "stale"
    ...
```

Thread `policy` through `select_one`, `select_all`, `prep_one`, `preview_all`, and **all three** `Sluice.prep` branches (`core/app.py:628`, `:630`, `:635`). Add `--include-stale` to `apply prep` in `cli.py`.

- [ ] **Step 4: Verify** — `python -m pytest -q && ruff check sluice tests` → all green.

- [ ] **Step 5: Commit**

```bash
git add sluice/apply sluice/core/app.py sluice/cli.py tests/
git commit -m "feat(apply): refuse a stale lead at prep, across all three branches (#9)"
```

---

### Task 8: Docs, then the mutation witness sweep

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

Three edits: the composition root's operation list gains `staleness()` (in the non-adapter-state clause beside `dossier_cache()`); the `leads` command group gains `expire`; and **`:277-317` must be corrected** — it currently asserts `today` is threaded only to `Ctx`/`VaultSink` and justifies its non-seam status by `last_seen` monotonicity alone. Both claims stop being true.

- [ ] **Step 2: Commit the docs**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: record the staleness policy and leads expire in ARCHITECTURE (#9)"
```

- [ ] **Step 3: Run the mutation witness sweep**

Commit everything first — a witness that restores via `git checkout --` wipes uncommitted work, and the empty post-run diff hides the loss.

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Then, for each row: apply the mutation by **moving or deleting** (never adding), run the named test **by node id**, confirm RED, confirm no pre-existing test in the same file is what catches it, restore, confirm GREEN.

| # | Mutant | Must redden |
| --- | --- | --- |
| 1 | Delete `if self.ttl_days <= 0: return False` | `test_unconfigured_ttl_abstains_on_an_ANCIENT_lead` |
| 2 | `d > self.ttl_days` → `d >= self.ttl_days` | `test_exactly_ttl_days_old_is_not_yet_stale` |
| 3 | Delete the `try`/`except ValueError: return None` (dedent the body) | `test_unparseable_or_absent_last_seen_abstains` |
| 4 | Move the `policy.blocks` check below `dossier_cache.get_or_build` | `test_stale_lead_is_skipped_before_any_dossier_fetch` |
| 5 | Delete `require_status=_EXPIRABLE` from expire's write | `test_a_lead_that_becomes_applied_MID_SWEEP_is_not_dismissed` |
| 6 | Delete `ttl_days=self.config.lead_ttl_days` from `Sluice.staleness()` | the three `Sluice`-layer wiring tests |

Record the result of each row. **If any mutant survives green, the test is inert — fix the test, not the table.** Rows 1, 4 and 5 were each proved inert in an earlier draft; they are the ones most likely to be wrong again.

- [ ] **Step 4: Final verification**

```bash
git status --porcelain     # must be empty
python -m pytest           # all green
ruff check sluice tests    # clean
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: policy → 1; config + example → 2; `Store` contract → 3; `staleness()` → 4; expire (slug exactness, sign-off refusal, argparse, off-state, `--json`) → 5; cv guard → 6; apply guard incl. all three `prep` branches → 7; `ARCHITECTURE.md` + mutation table → 8. Out-of-scope items (triage, `first_seen` gating, the `last_seen`-parseable store obligation) are deliberately unimplemented and stated in the spec.

**Placeholders.** Tasks 1–4 carry complete code. Tasks 5–7 carry complete signatures, the exact guard placement, the exact comment rationale and a named test list, with prose only where the surrounding code must be read first (expire's report formatting, which mirrors `cmd_leads_dedupe`).

**Type consistency.** `StalenessPolicy` is constructed identically everywhere; `policy` is the parameter name at every call site; `blocks()` is what both gates call (never `is_stale`); `require_status` takes a `frozenset` in the store, the protocol and `_EXPIRABLE`.
