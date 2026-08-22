# Inert Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `DossierCache` caching JD fetches that produced nothing, and stop `cv/engine.py` discarding the slop linter's phrase matches.

**Architecture:** Two independent tracks on one branch. Track A (#169) gives `DossierCache` a `jd_arrived` predicate it uses to decide whether to persist, adds a sixth triage status `unjudgeable` for leads whose JD never arrived, and surfaces per-source counts. Track B (#167) splits the CV gate into a blocking HARD tier and a non-binding STYLE tier that feeds the composer's one retry. They share no code — different sub-apps — so tasks 1-9 and 10-17 can be reviewed independently.

**Tech Stack:** Python 3.12+, standard library only in `sluice/` (guarded `yaml` import in config modules). pytest, ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-08-21-inert-signals-design.md` — read it alongside this plan. Every task argues from a decision recorded there.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency in any task.
- **New tunables go in the `*Config` dataclass AND `sluice.yaml.example`** (commented), and `docs/CONFIGURATION.md`.
- **Conventional Commits.** Every commit message below is already conformant; do not reword the type or scope.
- **Never-clobber:** every modify-write goes through `vault.update_fields`. No task adds a new write function — CodeQL flags one as a new sink.
- **Never-regress:** no task touches `_LADDER`, `_TERMINAL`, `can_apply`, `can_advance` or `can_transition`.
- **The fabrication gate is hard:** no task may let a CV render with `validate()` violations outstanding. Retry stays exactly once.
- **Comments explain *why*.** This codebase is dense with them and several encode real incidents. Match that density; a bare restatement of the code is not a comment.
- **Run before every commit:** `.venv/bin/python -m pytest` and `.venv/bin/python -m ruff check sluice tests scripts`. Both must be clean.
- **Before any mutation witness:** `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`, and COMMIT first — a witness script that restores via `git checkout` wipes uncommitted work.

---

# Track A — #169: the dossier and triage track

### Task 1: `DossierCache.jd_arrived` and the not-persisted path

**Files:**
- Modify: `sluice/core/dossier.py:32-98`
- Test: `tests/test_dossier.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DossierCache.__init__(dir, ttl_days, fetcher, clock=datetime.now, min_jd_chars=0)` and `DossierCache.jd_arrived(dossier: dict) -> bool`. Tasks 3, 5 and 12 depend on both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dossier.py`:

```python
def _cache(tmp_path, jd_markdown, *, min_jd_chars=0):
    return DossierCache(str(tmp_path), ttl_days=7,
                        fetcher=lambda lead: {"jd": {"markdown": jd_markdown}, "glassdoor": {}},
                        clock=_clock(datetime(2026, 7, 8)), min_jd_chars=min_jd_chars)


def test_an_empty_jd_never_arrives_whatever_the_floor(tmp_path):
    # Empty is a FACT, not a judgement, so it fails at every floor including the
    # shipped 0 -- that is what makes `min_jd_chars: 0` a real fix rather than an
    # inert one (spec decision 3).
    for floor in (0, 200):
        dc = _cache(tmp_path, "   \n  ", min_jd_chars=floor)
        assert dc.jd_arrived(dc.get_or_build({"lead_id": f"empty-{floor}"})) is False


def test_a_short_jd_arrives_at_floor_zero_and_not_above_it(tmp_path):
    dc0 = _cache(tmp_path, "x" * 35, min_jd_chars=0)
    assert dc0.jd_arrived(dc0.get_or_build({"lead_id": "short-0"})) is True
    dc200 = _cache(tmp_path, "x" * 35, min_jd_chars=200)
    assert dc200.jd_arrived(dc200.get_or_build({"lead_id": "short-200"})) is False


def test_whitespace_cannot_pass_a_floor(tmp_path):
    # Stripped on BOTH sides of the comparison: 300 spaces must not clear a floor of 200.
    dc = _cache(tmp_path, " " * 300, min_jd_chars=200)
    assert dc.jd_arrived(dc.get_or_build({"lead_id": "spaces"})) is False


def test_a_malformed_jd_field_fails_rather_than_raising(tmp_path):
    # Same degrade-to-failure posture triage/resolve.py:_text already takes on this field.
    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=lambda lead: {"glassdoor": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    assert dc.jd_arrived({"jd": None}) is False
    assert dc.jd_arrived({"jd": {"markdown": 42}}) is False
    assert dc.jd_arrived({}) is False


def test_a_jd_that_did_not_arrive_is_not_persisted(tmp_path):
    dc = _cache(tmp_path, "", min_jd_chars=0)
    dc.get_or_build({"lead_id": "nothing"})
    assert not (tmp_path / "nothing.json").exists()


def test_the_not_persisted_path_returns_the_FRESH_dossier(tmp_path):
    # The caller must be able to answer jd_arrived on what it is holding, so the
    # rejected cached entry is never what comes back.
    (tmp_path / "stale.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "stale", "company": "Example Old Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": ""}, "glassdoor": {},
        "built_at": datetime(2026, 7, 7).isoformat()}))
    dc = _cache(tmp_path, "", min_jd_chars=0)
    d = dc.get_or_build({"lead_id": "stale", "company": "Example New Co"})
    assert d["company"] == "Example New Co"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dossier.py -k "arrived or persisted or whitespace or malformed or FRESH" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'min_jd_chars'`.

- [ ] **Step 3: Implement**

In `sluice/core/dossier.py`, extend `__init__` and add the predicate:

```python
    def __init__(self, dir: str, ttl_days: int, fetcher, clock=datetime.now,
                 min_jd_chars: int = 0):
        self.dir = dir
        self.ttl_days = ttl_days
        self.fetcher = fetcher
        self.clock = clock
        # 0 = the near-empty band is OFF, which is the SHIPPED default (#169, spec
        # decision 3): a character count is a judgement about what counts as a real
        # posting, and this repo does not ship one uninvited -- see sluice.yaml.example's
        # `lead_ttl_days` for the same rule stated at length. An EMPTY jd is different in
        # kind: it is a fact, so `jd_arrived` refuses it at every floor including 0.
        # The constructor default is 0 rather than the config default so the bare
        # `DossierCache(dir, ttl, fetcher=...)` constructions across the suite keep
        # today's behaviour exactly.
        self.min_jd_chars = min_jd_chars

    def jd_arrived(self, dossier: dict) -> bool:
        """Did this fetch actually produce a job description?

        The ONE owner of that judgement. `get_or_build` asks it to decide whether to
        PERSIST, and every caller asks it to decide what a miss means for them -- one
        function, two uses, so there is no second copy of the rule to drift (#169).

        A predicate rather than a marker key in the returned dict, deliberately: a marker
        would ride `slim()` into the judge prompt, since `slim` excludes `lead_snapshot`,
        `page_title` and `structured_data` by NAME and would not exclude a new key by
        accident.

        Degrades to False rather than raising on a malformed `jd`, matching what
        `triage/resolve.py:_text` already does with this same field -- a dossier that
        cannot answer the question has not produced a JD either.
        """
        jd = dossier.get("jd")
        markdown = jd.get("markdown") if isinstance(jd, dict) else None
        if not isinstance(markdown, str):
            return False
        text = markdown.strip()
        if not text:
            return False            # a FACT, refused at every floor
        return len(text) >= self.min_jd_chars
```

Then in `get_or_build`, gate the write (leave the `dossier` dict build exactly as it is):

```python
        # Do NOT persist a fetch that produced no JD (#169). Caching one makes every
        # later run serve the failure for the whole TTL: triage judges a lead on a
        # document nobody read, returns "unjudgeable" (a `research` verdict), and the
        # nightly `--status new,research` run re-selects it and pays for the same
        # non-answer until the entry expires. Not writing costs one refetch per run and
        # ends the loop. The FRESHLY FETCHED dossier is still returned, never the
        # rejected cached one, so the caller can answer `jd_arrived` on what it holds.
        if self.jd_arrived(dossier):
            os.makedirs(self.dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dossier, f, ensure_ascii=False)
        return dossier
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_dossier.py -v`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Full suite, lint, commit**

Run: `.venv/bin/python -m pytest` then `.venv/bin/python -m ruff check sluice tests scripts`

```bash
git add sluice/core/dossier.py tests/test_dossier.py
git commit -m "fix(dossier): refuse to cache a fetch that produced no JD (#169)"
```

---

### Task 2: `_fresh()` applies the predicate, so poisoned entries self-heal

**Files:**
- Modify: `sluice/core/dossier.py` (`_fresh`)
- Test: `tests/test_dossier.py:85-105` (fixture update) and new tests

**Interfaces:**
- Consumes: `jd_arrived` from Task 1.
- Produces: nothing new; changes `_fresh`'s meaning from "fresh by time" to "fresh by time AND content".

- [ ] **Step 1: Write the failing test**

```python
def test_a_cached_entry_whose_jd_never_arrived_is_refetched(tmp_path):
    # Fresh BY TIME (1 day old, ttl 7) but empty by content. Without this the fix does
    # nothing to an existing deployment's cache for a full TTL.
    (tmp_path / "poisoned.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "poisoned", "company": "Example Stale Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": ""}, "glassdoor": {},
        "built_at": datetime(2026, 7, 7).isoformat()}))
    calls = []

    def _fetch(lead):
        calls.append(lead)
        return {"jd": {"markdown": "A real job description, at last."}, "glassdoor": {}}

    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=_fetch,
                      clock=_clock(datetime(2026, 7, 8)))
    d = dc.get_or_build({"lead_id": "poisoned"})
    assert calls, "a poisoned entry must be refetched, not served"
    assert d["jd"]["markdown"].startswith("A real job description")


def test_a_healthy_cached_entry_is_still_served_without_refetching(tmp_path):
    (tmp_path / "healthy.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "healthy", "company": "Example Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": "A real job description."}, "glassdoor": {},
        "built_at": datetime(2026, 7, 7).isoformat()}))
    calls = []
    dc = DossierCache(str(tmp_path), ttl_days=7,
                      fetcher=lambda lead: calls.append(lead) or {"jd": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    dc.get_or_build({"lead_id": "healthy"})
    assert calls == [], "a healthy entry must not be refetched"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dossier.py -k "refetched or healthy_cached" -v`
Expected: FAIL — `assert calls` is empty; the poisoned entry is served.

- [ ] **Step 3: Implement**

```python
    def _fresh(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            cached = json.loads(open(path, encoding="utf-8").read())
            age = self.clock() - datetime.fromisoformat(cached.get("built_at"))
        except (OSError, ValueError, TypeError):
            return False
        if age.days >= self.ttl_days:
            return False
        # Content as well as age (#169). An entry written BEFORE this existed whose JD
        # never arrived is fresh by the clock and useless by content; without this check
        # the fix reaches an existing deployment's cache only after a full TTL, and the
        # issue's manual "delete the sub-200-character entries" step stays manual.
        #
        # At the shipped `min_jd_chars: 0` this re-fetches the EMPTY subset only -- the
        # short-but-not-empty entries need a configured floor, which is the accepted cost
        # recorded in the spec's decision 3 and surfaced by `doctor` (Task 8).
        #
        # If the refetch also fails, nothing is written and this file lingers, inert,
        # re-read and re-rejected each run. That is the intended retry. There is
        # deliberately NO cleanup pass: deleting on a read would make a read a write,
        # which is the exact shape that disarmed the #81 relocation notice.
        return self.jd_arrived(cached)
```

- [ ] **Step 4: Fix the legacy-schema fixture, which now legitimately reddens**

`tests/test_dossier.py:85-105`'s `test_get_or_build_loads_a_legacy_cached_dossier_missing_the_new_fields` seeds `"jd": {"markdown": ""}`. That entry is now not-fresh, so it is refetched and both assertions fail. Give it a real JD so it keeps testing what it means to test — a pre-#109 entry missing `page_title`/`structured_data`:

```python
             "lead_snapshot": {}, "jd": {"markdown": "A legacy job description."},
```

**Do not** "fix" this by deleting `_fresh`'s content check. That check is #169's entire self-healing half; the red here is the new behaviour working.

- [ ] **Step 5: Run, lint, commit**

Run: `.venv/bin/python -m pytest tests/test_dossier.py -v` then the full suite and ruff.

```bash
git add sluice/core/dossier.py tests/test_dossier.py
git commit -m "fix(dossier): treat a cached entry with no JD as stale, so it self-heals (#169)"
```

---

### Task 3: `min_jd_chars` as a ROOT config key reaching both cache constructions

**Files:**
- Modify: `sluice/core/config.py` (`Config` dataclass + `load_config`)
- Modify: `sluice/core/app.py` (`dossier_cache` signature, both call sites at `:1125` and `:1210`)
- Modify: `sluice.yaml.example`, `docs/CONFIGURATION.md`
- Test: `tests/test_config_paths.py:331-345` (widen `_capture`), `tests/test_sluice_neutral_defaults.py`

**Interfaces:**
- Consumes: `DossierCache(..., min_jd_chars=...)` from Task 1.
- Produces: `Config.min_jd_chars: int = 0`; `Sluice.dossier_cache(dossier_dir, ttl_days, min_jd_chars)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_paths.py`, widen the existing capture fixture to record BOTH values (the four `#80` one-root-key guards call it, and it takes exactly two positional parameters today, so a third argument would TypeError inside `app.triage(...)`):

```python
def _dossier_dirs_used(app, monkeypatch):
    """The directory and floor each sub-app hands to dossier_cache, in call order."""
    seen = []

    def _capture(dossier_dir, ttl_days, min_jd_chars):
        seen.append((dossier_dir, min_jd_chars))
        return _NullCache()
    ...
    return seen
```

Update the four existing consumers (`:352`, `:361`, `:369`, `:376`) to read `d for d, _ in _dossier_dirs_used(...)`. **Do not** widen `_capture` to `lambda *a, **k:` — that greens the four directory pins while making the new floor assertion below unwritable.

Then add:

```python
def test_the_root_min_jd_chars_reaches_both_sub_apps(tmp_path, monkeypatch):
    # A per-sub-app floor would make the SHARED cache directory persist or refuse the
    # same entry depending on which sub-app touched it last -- the "shared only by
    # coincidence of a default" hazard _dossier_dir's own docstring exists to kill.
    app = _app_with(tmp_path, monkeypatch, root={"min_jd_chars": 200})
    floors = [f for _, f in _dossier_dirs_used(app, monkeypatch)]
    assert floors == [200, 200], floors
```

In `tests/test_sluice_neutral_defaults.py`:

```python
def test_min_jd_chars_dataclass_default_is_off():
    # The list-keyed neutral-defaults sweep does not see an int field, so this knob
    # carries its own named guard -- same reason lead_ttl_days and lead_layout do.
    assert Config().min_jd_chars == 0


def test_min_jd_chars_rejects_a_bool_before_checking_int():
    # PyYAML resolves `min_jd_chars: yes` to True and bool SUBCLASSES int, so the
    # natural thing to type to turn this on would otherwise load as a 1-character floor.
    with pytest.raises(ValueError, match="min_jd_chars"):
        _load_root_with(min_jd_chars=True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config_paths.py tests/test_sluice_neutral_defaults.py -k "min_jd_chars" -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'min_jd_chars'`.

- [ ] **Step 3: Implement the config field and validator**

In `sluice/core/config.py`'s `Config`, beside `lead_ttl_days`:

```python
    # The floor below which a fetched JD is treated as NOT HAVING ARRIVED (#169), so the
    # dossier cache refuses to persist it and triage refuses to spend a judge call on it.
    # 0 = the band is OFF and is the SHIPPED default: an EMPTY jd always fails (a fact),
    # but a character count is a judgement about what counts as a real posting, and an
    # active value would hand every copier one they never made -- the same rule
    # sluice.yaml.example states at length for `lead_ttl_days`. `job-sluice init` asks.
    # ROOT, not per-sub-app, because triage and cv SHARE one dossier directory: two
    # different floors over one directory means whichever sub-app ran last decides
    # whether an entry exists.
    min_jd_chars: int = 0
```

In `load_config`, beside the `lead_ttl_days` validator (bool FIRST and separately, for the reason its neighbour states):

```python
    raw_floor = data.get("min_jd_chars")
    raw_floor = 0 if raw_floor is None else raw_floor
    if isinstance(raw_floor, bool) or not isinstance(raw_floor, int) or raw_floor < 0:
        raise ValueError(
            f"min_jd_chars must be a non-negative integer (0 = off), got {raw_floor!r}")
```

…and NAME the field in the explicit `Config(...)` construction — this loader names every field explicitly, so an unnamed one is dead.

- [ ] **Step 4: Thread it through `app.py`**

```python
    def dossier_cache(self, dossier_dir, ttl_days, min_jd_chars):
```

…returning `DossierCache(dossier_dir, ttl_days, fetcher=fetch, min_jd_chars=min_jd_chars)`, and both call sites pass `self.config.min_jd_chars`:

```python
        cache = self.dossier_cache(self._dossier_dir(), tcfg.ttl_days,
                                   self.config.min_jd_chars)
```

- [ ] **Step 5: Document it**

`sluice.yaml.example` (commented, following `lead_ttl_days`' shape and reasoning) and `docs/CONFIGURATION.md`.

- [ ] **Step 6: Run, lint, commit**

```bash
git add sluice/core/config.py sluice/core/app.py sluice.yaml.example docs/CONFIGURATION.md tests/test_config_paths.py tests/test_sluice_neutral_defaults.py
git commit -m "feat(config): add the root min_jd_chars floor, off by default (#169)"
```

---

### Task 4: `unjudgeable` status and the ONE shared selection default

**Files:**
- Modify: `sluice/core/status.py`
- Modify: `sluice/cli.py:652`, `sluice/cli.py:1554`, `sluice/core/app.py:1070`, `sluice/triage/engine.py:81`
- Modify: `docs/USAGE.md:84`
- Test: `tests/test_status.py`, `tests/test_lead_layout_map.py:56`, new parser-walk guard

**Interfaces:**
- Consumes: nothing.
- Produces: `status.TRIAGE_OWNED` with a sixth member; `status.DEFAULT_TRIAGE_STATUSES = ("new", "research", "unjudgeable")`. Task 5 writes the status; Task 7 uses the tuple as a denominator.

- [ ] **Step 1: Write the failing tests**

```python
def test_unjudgeable_is_triage_owned_and_not_application_owned():
    assert "unjudgeable" in _status.TRIAGE_OWNED
    assert not _status.is_application_owned("unjudgeable")
    assert not _status.is_terminal("unjudgeable")


def test_the_common_misspelling_normalises():
    assert _status.normalize("unjudgable") == "unjudgeable"


def test_the_selection_default_has_ONE_home_and_the_parser_uses_it():
    # The value lived in FOUR places before this (cli.py:652, cli.py:1554,
    # core/app.py:1070, triage/engine.py:81) and only the last was ever changed by an
    # earlier draft -- which would have written `unjudgeable` and then never re-read it.
    from sluice.cli import _build_parser
    actions = _walk_actions(_build_parser())
    status_defaults = [a.default for a in actions if "--status" in (a.option_strings or [])]
    assert status_defaults, "the walk found no --status option: the sweep is vacuous"
    expected = ",".join(_status.DEFAULT_TRIAGE_STATUSES)
    assert set(status_defaults) == {expected}, status_defaults


def test_the_selection_default_is_not_derivable_from_the_vocabulary():
    # It is a hand-picked RETRY subset, not a computed one. An implementer who derives it
    # from TRIAGE_OWNED would silently re-judge shortlisted and dismissed leads every run.
    assert set(_status.DEFAULT_TRIAGE_STATUSES) < set(_status.TRIAGE_OWNED)
    assert "shortlist" not in _status.DEFAULT_TRIAGE_STATUSES
    assert "dismiss" not in _status.DEFAULT_TRIAGE_STATUSES
```

Model `_walk_actions` on `tests/test_docs_claims.py:64-69`, and copy its scope guard (`:92 test_the_command_tree_walk_is_not_vacuous`) — a walk that finds nothing satisfies every assertion over it.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_status.py -k "unjudge or selection" -v`
Expected: FAIL — `"unjudgeable" not in TRIAGE_OWNED`.

- [ ] **Step 3: Implement in `core/status.py`**

```python
TRIAGE_OWNED = ("new", "shortlist", "research", "needs_review", "dismiss", "unjudgeable")
```

```python
    "unjudgable": "unjudgeable",     # the common misspelling
```

```python
# What `triage run --status` selects when the user names nothing. A hand-picked RETRY
# subset with ONE home, deliberately NOT derived from TRIAGE_OWNED -- which also holds
# shortlist, needs_review and dismiss, so a derivation would re-judge leads the user has
# already decided about, every run. It lives here rather than in cli.py because the value
# had FOUR homes before #169 (cli.py twice, core/app.py, triage/engine.py) and an earlier
# draft of that fix changed only the one that is dead on the production path -- writing
# `unjudgeable` onto leads that nothing would ever re-read.
#
# `unjudgeable` is in it because that IS the retry: the lead's JD never arrived, the
# cache no longer serves the failure (#169), so the next run refetches.
DEFAULT_TRIAGE_STATUSES = ("new", "research", "unjudgeable")
```

- [ ] **Step 4: Point all four homes at it**

- `sluice/triage/engine.py:81` → `statuses=_status.DEFAULT_TRIAGE_STATUSES`
- `sluice/core/app.py:1070` → `statuses=_status.DEFAULT_TRIAGE_STATUSES`
- `sluice/cli.py:1554` → `default=",".join(_status.DEFAULT_TRIAGE_STATUSES)`
- `sluice/cli.py:652` → `args.status or ",".join(_status.DEFAULT_TRIAGE_STATUSES)`

`core/status.py` imports nothing, so there is no cycle; `core/app.py:83` already imports `_status` at module scope for `_EXPIRABLE`, so this is not a rule-12 lazy-import breach.

- [ ] **Step 5: Update the vocabulary scope pin and the docs**

`tests/test_lead_layout_map.py:56` — `assert len(_status.CANONICAL) == 12` becomes `13`. Change the NUMBER only; the assertion is the scope guard that stops the sweep passing over an empty vocabulary.

`docs/USAGE.md:84` — the `--status` default cell becomes `new,research,unjudgeable`.

- [ ] **Step 6: Sweep every other consumer**

Run `grep -rn 'TRIAGE_OWNED\|CANONICAL' sluice/ tests/` and check each hit. Do not trust a list of three: `_EXPIRABLE` and `_DISMISSABLE_FROM` gain the member automatically (both are derived), but `resolve_merge_status` gains new conflict pairs and `mcpserver.py` has three references.

- [ ] **Step 7: Run, lint, commit**

```bash
git add sluice/core/status.py sluice/cli.py sluice/core/app.py sluice/triage/engine.py docs/USAGE.md tests/
git commit -m "feat(status): add the unjudgeable triage state and one shared selection default (#169)"
```

---

### Task 5: Triage short-circuits an unjudgeable lead before the judge

**Files:**
- Modify: `sluice/triage/apply.py` (`_DECISION_STATUS`)
- Modify: `sluice/triage/engine.py:253-300` (the enrich loop), `:55-62` (`TriageReport.counts`)
- Test: `tests/test_triage_engine.py`

**Interfaces:**
- Consumes: `jd_arrived` (Task 1), `unjudgeable` in `TRIAGE_OWNED` (Task 4).
- Produces: `TriageReport.counts["unjudgeable"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_lead_whose_jd_never_arrived_is_marked_unjudgeable_and_never_judged(tmp_path):
    # Asserted on the backend spy's prompt CONTENT in a MIXED batch: `prompts == []` is
    # vacuous, because a run that judged NOTHING would satisfy it too.
    vault, backend = _vault_with(tmp_path, leads=["good-lead", "blocked-lead"]), _SpyBackend()
    cache = _CacheWhere({"blocked-lead": "", "good-lead": "A real JD, long enough."})
    report = run(vault, _cfg(), backend, cache, _audit(), statuses=("new",))
    assert report.counts["unjudgeable"] == 1
    assert vault.read_one("blocked-lead").status == "unjudgeable"
    joined = "\n".join(backend.prompts)
    assert "good-lead" in joined, "the healthy lead must still reach the judge"
    assert "blocked-lead" not in joined, "an unjudgeable lead must cost no judge call"


def test_a_dry_run_counts_an_unjudgeable_lead_but_writes_nothing(tmp_path):
    vault = _vault_with(tmp_path, leads=["blocked-lead"])
    report = run(vault, _cfg(), _SpyBackend(), _CacheWhere({"blocked-lead": ""}),
                 _audit(), statuses=("new",), dry_run=True)
    assert report.counts["unjudgeable"] == 1
    assert vault.read_one("blocked-lead").status == "new"


def test_an_application_owned_lead_is_never_marked_unjudgeable(tmp_path):
    # apply_classification's _guarded refuses; never-regress holds unchanged.
    vault = _vault_with(tmp_path, leads=["applied-lead"], status="applied")
    run(vault, _cfg(), _SpyBackend(), _CacheWhere({"applied-lead": ""}), _audit(),
        statuses=("applied",))
    assert vault.read_one("applied-lead").status == "applied"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_engine.py -k unjudge -v`
Expected: FAIL — `KeyError: 'unjudgeable'`.

- [ ] **Step 3: Implement**

`sluice/triage/apply.py`:

```python
_DECISION_STATUS = {"reject": "dismiss", "needs_review": "needs_review", "keep": "new",
                    "unjudgeable": "unjudgeable"}
```

`sluice/triage/engine.py`, in `TriageReport.counts`'s default factory, add `"unjudgeable": 0` — a counts row of its own, because these rows are lead OUTCOMES that `cmd_triage_run` prints and `notify()` sends to Telegram verbatim, and without one the rows stop summing to the total a human reads on a phone.

In the enrich loop, immediately after `d = dossier_cache.get_or_build(note.fm)`:

```python
            # The JD never arrived (#169). Spending a judge call here buys a verdict on
            # page chrome -- and because "unjudgeable" used to collapse into `research`,
            # the nightly `--status new,research` run re-selected the lead and paid for
            # the same non-answer every night until the cache entry expired. Nothing was
            # cached this run (see DossierCache.get_or_build), so the next run refetches;
            # marking the lead `unjudgeable` is what separates "the pipeline should retry
            # this" from "a human should investigate this", which is what `research` means.
            if not dossier_cache.jd_arrived(d):
                report.counts["unjudgeable"] = report.counts.get("unjudgeable", 0) + 1
                if not dry_run:
                    apply_classification(
                        vault, note, "unjudgeable",
                        f"no job description was fetched (floor: "
                        f"{dossier_cache.min_jd_chars} chars)")
                continue
```

`continue` before `dossiers.append(d)` is the whole point: the lead never enters the batch, so it costs no judge call.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_triage_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Witness the guard**

Commit first, then delete the `continue` line, run
`.venv/bin/python -m pytest tests/test_triage_engine.py::test_a_lead_whose_jd_never_arrived_is_marked_unjudgeable_and_never_judged -v`, confirm it FAILS on the `"blocked-lead" not in joined` assertion, and restore. Deleting (not adding) is the only mutation shape that proves anything here.

- [ ] **Step 6: Run, lint, commit**

```bash
git add sluice/triage/apply.py sluice/triage/engine.py tests/test_triage_engine.py
git commit -m "fix(triage): mark a lead unjudgeable rather than judging an empty JD (#169)"
```

---

### Task 5b: `cv/engine.py` flags a dossier that produced no JD

**Files:**
- Modify: `sluice/cv/engine.py:195-205`
- Test: `tests/test_cv_engine.py`

**Interfaces:**
- Consumes: `jd_arrived` (Task 1).
- Produces: nothing new — `CvResult.dossier_failed` already exists (#18).

The third of the spec's three callers. Numbered `5b` so Tasks 6-18's "Consumes: … from Task N" references stay valid.

- [ ] **Step 1: Write the failing test**

```python
def test_a_cv_composed_without_a_JD_is_flagged_rather_than_silently_tailored(tmp_path):
    # #18 added dossier_failed for a fetch that RAISED. A fetch that succeeds and returns
    # page chrome is the same fact wearing different clothes: without the flag,
    # "status: rendered" is indistinguishable from a CV genuinely tailored to a real job
    # description. Control flow is deliberately unchanged -- composing from the bundle
    # alone is degraded, not wrong, and skipping the lead here would be a bigger
    # behaviour change than this issue should carry.
    res = _run_one(tmp_path, jd_markdown="")
    assert res.status == "rendered"
    assert res.dossier_failed is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -k silently_tailored -v`
Expected: FAIL — `assert False is True`.

- [ ] **Step 3: Implement**

Beside the existing `except` that already sets `dossier_failed` when `get_or_build` raised:

```python
        # A fetch that SUCCEEDED and produced no JD is the same fact as one that raised
        # (#18), so it earns the same flag. Not the same control flow, though: the
        # `except` arm below composes with jd="" and so does this, because a CV built
        # from the verified bundle alone is degraded rather than fabricated.
        if not dossier_cache.jd_arrived(d):
            dossier_failed = True
```

- [ ] **Step 4: Run, lint, commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "fix(cv): flag a CV composed without a job description (#169)"
```

---

### Task 6: Clamp `apply_verdict` to the judge's vocabulary, with one copy of the rule

**Files:**
- Modify: `sluice/triage/apply.py` (new pure helper + `apply_verdict`)
- Modify: `sluice/triage/engine.py:386-395` (counts key + audit row)
- Test: `tests/test_triage_apply.py`, `tests/test_triage_engine.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `triage.apply.clamp_verdict(raw: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_model_verdict_outside_the_judges_vocabulary_becomes_needs_review():
    assert clamp_verdict("shortlist") == "shortlist"
    assert clamp_verdict("research") == "research"
    assert clamp_verdict("dismiss") == "dismiss"
    assert clamp_verdict("nonsense") == "needs_review"
    assert clamp_verdict("") == "needs_review"


def test_a_model_cannot_write_an_application_owned_status():
    # Live hole independent of #169: require_status checks only the status the lead is
    # CURRENTLY in, so a model returning "applied" on a `new` lead wrote it.
    assert clamp_verdict("applied") == "needs_review"
    assert clamp_verdict("rejected") == "needs_review"


def test_the_clamped_status_is_what_gets_COUNTED_and_AUDITED(tmp_path):
    # counts and the audit row are computed in the engine, OUTSIDE apply_verdict, and
    # both keyed on the RAW model string -- so a clamp that only fixed the write would
    # report a verdict that never landed (the #109/#118 bug class).
    vault = _vault_with(tmp_path, leads=["a-lead"])
    audit = _audit()
    report = run(vault, _cfg(), _BackendReturning(verdict="applied"), _GoodCache(),
                 audit, statuses=("new",))
    assert report.counts["needs_review"] == 1
    assert report.counts.get("applied", 0) == 0
    assert audit.rows[-1]["verdict"] == "needs_review"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_apply.py -k clamp -v`
Expected: FAIL — `ImportError: cannot import name 'clamp_verdict'`.

- [ ] **Step 3: Implement the pure helper**

```python
# The judge's OWN vocabulary -- three verdicts, exactly what triage/prompt.py:60 and
# triage/judge.py:44 ask the model for.
_JUDGE_VERDICTS = frozenset({"shortlist", "research", "dismiss"})


def clamp_verdict(raw: str) -> str:
    """The model's verdict, or `needs_review` if it said something else.

    `normalize` passes an unrecognised value through untouched, and `apply_verdict` wrote
    whatever came back straight into `status`. That is a live hole: `require_status`
    checks only the status the lead is CURRENTLY in, so a model returning
    `verdict: "applied"` on a `new` lead wrote an APPLICATION-OWNED status from triage --
    the never-regress invariant, reachable from model output.

    Pure, and shared: the engine's counts row and audit trail call this too, so the run
    reports the status that was actually WRITTEN rather than the raw string. A second
    copy in the engine would be the hand-list drift this codebase keeps engineering out.
    """
    s = _status.normalize(raw or "")
    return s if s in _JUDGE_VERDICTS else "needs_review"
```

In `apply_verdict`, replace `status = _status.normalize(verdict.get("verdict", "needs_review"))` with `status = clamp_verdict(verdict.get("verdict", ""))`.

- [ ] **Step 4: Give the clamp its channel to the reporting**

In `sluice/triage/engine.py`, at `:386` and `:393`:

```python
            key = "skipped" if outcome in ("skipped", "unchanged") else clamp_verdict(
                verdict.get("verdict", ""))
```

```python
                        "stage": "judge", "verdict": clamp_verdict(verdict.get("verdict", "")),
```

`apply_verdict`'s RETURN is deliberately unchanged — `outcome in ("skipped", "unchanged")` depends on it at both call sites, and changing it would emit audit rows for `_guarded` leads.

- [ ] **Step 5: Run, lint, commit**

```bash
git add sluice/triage/apply.py sluice/triage/engine.py tests/
git commit -m "fix(triage): clamp a model verdict to the judge's own vocabulary (#169)"
```

---

### Task 7: Per-source unjudgeable rate in `health_report`

**Files:**
- Modify: `sluice/core/app.py:1049-1068` (`health_report`), `sluice/core/doctor.py`
- Modify: `sluice/cli.py` (`cmd_health` print format), `sluice/mcpserver.py:143` (docstring)
- Test: `tests/test_health_cli.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `DEFAULT_TRIAGE_STATUSES` (Task 4).
- Produces: `SourceHealth.unjudgeable` and `.selected` (numerator, denominator).

- [ ] **Step 1: Write the failing test**

```python
def test_the_unjudgeable_rate_counts_numerator_and_denominator_at_the_SAME_stage(tmp_path):
    # read_leads() unfiltered is ALL-TIME (dismiss, applied, terminals included), so an
    # all-time denominator dilutes a source that is 100% broken today by its entire
    # history and the classification could structurally never fire. Both terms come from
    # the SHARED selection set instead -- one point in the LIFECYCLE, not just in time.
    vault = _vault_with_sources(tmp_path, {
        "board-a": [("new", 1), ("unjudgeable", 3)],
        "board-b": [("dismiss", 50), ("unjudgeable", 0)],
    })
    got = {h.id: (h.unjudgeable, h.selected)
           for h in _app(vault).health_report(include_leads=True)}
    assert got["board-a"] == (3, 4)
    assert got["board-b"] == (0, 0), "dismissed leads are not in the selection set"


def test_health_report_does_no_vault_io_by_default(tmp_path):
    # doctor and the MCP health tool must keep today's cost.
    vault = _CountingVault(tmp_path)
    _app(vault).health_report()
    assert vault.read_leads_calls == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_health_cli.py -k unjudgeable -v`
Expected: FAIL — `TypeError: health_report() got an unexpected keyword argument 'include_leads'`.

- [ ] **Step 3: Implement**

Add `unjudgeable: int = 0` and `selected: int = 0` to `SourceHealth`, then:

```python
    def health_report(self, *, include_leads: bool = False) -> list:
```

```python
        # Off by default: this method does NO vault I/O today (source registry +
        # HealthStore only), and `doctor` plus the MCP `health` tool are things a user
        # runs often and cheaply. #169 §2 needs the walk, so it is a parameter rather
        # than a new cost imposed on every caller.
        #
        # Both terms come from ONE read_leads() pass over the SAME lifecycle stage: the
        # numerator is `unjudgeable` for source X, the denominator is source X's leads in
        # DEFAULT_TRIAGE_STATUSES. NOT read_leads() unfiltered, which is all-time and
        # would dilute a currently-broken source by its whole history -- the #156 mistake
        # (numerator and denominator drawn from different populations) in a new costume.
        rates = {}
        if include_leads:
            for n in self.store().read_leads(frozenset(_status.DEFAULT_TRIAGE_STATUSES)):
                src = n.fm.get("source", "")
                bad, total = rates.get(src, (0, 0))
                rates[src] = (bad + (1 if _status.normalize(n.status) == "unjudgeable"
                                     else 0), total + 1)
```

…and pass `unjudgeable`/`selected` into each `SourceHealth`. `core/vault.py:1004` returns `[]` on a missing `leads_dir`, so a fresh install does not break.

Classification stays pure in `core/doctor.py`, mirroring `classify_gate`'s shape.

- [ ] **Step 4: Update the surfaces and their docs**

`cmd_health`'s fixed print format, `sluice/mcpserver.py:143`'s "Per-source scrape baseline + retire state" docstring, and `health_report`'s own docstring — which currently implies no vault I/O.

- [ ] **Step 5: Run, lint, commit**

```bash
git add sluice/core/app.py sluice/core/doctor.py sluice/cli.py sluice/mcpserver.py tests/
git commit -m "feat(health): report each source's unjudgeable rate (#169)"
```

---

### Task 8: `doctor` reports the cached-JD distribution

**Files:**
- Modify: `sluice/core/app.py` (`Sluice.doctor`'s impure half), `sluice/core/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `_dossier_dir()`.
- Produces: `classify_dossier_cache(counts: dict) -> ComponentCheck`.

- [ ] **Step 1: Write the failing test**

```python
def test_doctor_reports_the_cache_distribution_as_a_NOTICE_not_a_verdict():
    # A distribution is descriptive: it changes nothing about which leads are judged, so
    # it is not the shipped judgement a threshold verdict would be. And it is never
    # inert -- at min_jd_chars: 0 a threshold count would be identically zero, leaving
    # decision 3's accepted cost invisible, which is how #169 happened in the first place.
    check = classify_dossier_cache({"total": 1336, "empty": 12, "under_200": 141,
                                    "under_800": 426})
    assert check.state == NOTICE
    assert "141" in check.detail and "1336" in check.detail
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -k distribution -v`
Expected: FAIL — `ImportError: cannot import name 'classify_dossier_cache'`.

- [ ] **Step 3: Implement**

Gather in `Sluice.doctor`'s IMPURE half — where `self._dossier_dir()` is reachable and the other component facts are already collected. **Not** `Vault.preflight()`: the dossier dir is composition-root state invisible to `Vault`, and counting entries means parsing every cached JSON, which is exactly the walk `core/protocols.py:277-287` forbids preflight from doing.

If the scan proves costly, BOUND it and report the bound in the detail string — never truncate silently, since a capped count reads as a complete one.

- [ ] **Step 4: Run, lint, commit**

```bash
git add sluice/core/app.py sluice/core/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): report the cached JD length distribution (#169)"
```

---

### Task 9: `job-sluice init` asks for `min_jd_chars`

**Files:**
- Modify: `sluice/onboard/` (the question catalogue)
- Test: `tests/test_onboard_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_an_unanswered_min_jd_chars_renders_COMMENTED(tmp_path):
    # The wizard's contract: an unanswered run writes a file field-for-field equal to no
    # config at all except vault_dir. An active floor would be a judgement the user did
    # not make.
    rendered = render_config(answers={}, vault_dir=str(tmp_path))
    assert "# min_jd_chars:" in rendered
    assert not re.search(r"^min_jd_chars:", rendered, re.M)
```

- [ ] **Step 2: Run to verify it fails**, then add the question following the catalogue's existing shape, with help text pointing at `job-sluice doctor`'s distribution as the evidence for choosing a value.

- [ ] **Step 3: Run, lint, commit**

```bash
git add sluice/onboard tests/test_onboard_plan.py
git commit -m "feat(onboard): ask for the JD floor during init (#169)"
```

---

# Track B — #167: the CV style track

### Task 10: Extract `section_spans()` from `validate`'s inline loop

**Files:**
- Modify: `sluice/cv/validate.py:97-123`
- Test: `tests/test_cv_validate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `cv.validate.section_spans(cv_text) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]` — `(profile_lines, work_bullet_lines)`, 1-indexed line numbers. Task 12 consumes it.

**This is a refactor of the PURE fabrication gate.** It is its own task precisely so a reviewer can reject it independently, and it ships with an equivalence pin rather than an argument.

- [ ] **Step 1: Write the equivalence test FIRST**

```python
@pytest.mark.parametrize("cv_text", _ALL_CV_FIXTURES)
def test_the_section_span_helper_reproduces_validates_own_profile_and_work_line_sets(cv_text):
    # The helper must return EXACTLY the lines `validate` applies each check to. The arm
    # a naive extraction drops is the reset: `in_work` ends ONLY on CERTIFICATES /
    # EDUCATION, so a generic section splitter would silently stop citation-checking
    # bullets under PUBLICATIONS or PROJECTS -- weakening the fabrication gate while
    # scoping a style rule.
    profile, work = section_spans(cv_text)
    assert [n for n, _ in profile] == _lines_validate_profile_checks(cv_text)
    assert [n for n, _ in work] == _lines_validate_citation_checks(cv_text)


def test_a_bullet_under_PUBLICATIONS_is_still_a_WORK_bullet():
    cv = ("PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
          "- did a thing [e1]\n\nPUBLICATIONS\n- a paper [e1]\n")
    _, work = section_spans(cv)
    assert len(work) == 2, "PUBLICATIONS does not end the WORK section"


def test_CERTIFICATES_ends_the_WORK_section():
    cv = ("PROFILE\nProse.\n\nWORK EXPERIENCE\n01/2020-present | X | Role\n"
          "- did a thing [e1]\n\nCERTIFICATES\n- a cert\n")
    _, work = section_spans(cv)
    assert len(work) == 1, "CERTIFICATES ends the WORK section"
```

`_lines_validate_profile_checks` / `_lines_validate_citation_checks` are test-local reimplementations copied verbatim from `validate`'s loop **as it is before this task** — that is what makes the test an equivalence pin rather than a restatement of the new code.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py -k section_span -v`
Expected: FAIL — `ImportError: cannot import name 'section_spans'`.

- [ ] **Step 3: Extract**

```python
def section_spans(cv_text):
    """The PROFILE prose lines and the WORK bullet lines, 1-indexed.

    Extracted from `validate`'s own loop so the STYLE tier (#167) scopes itself to the
    exact lines the gate reasons about, rather than a second copy of the split that would
    drift from it. `validate` now calls this too -- one state machine, two consumers.

    The terminator set is `CERTIFICATES`/`EDUCATION` and NOTHING else, deliberately: a
    generic "any all-caps line ends the section" splitter would stop citation-checking
    bullets under PUBLICATIONS or PROJECTS, which the gate checks today. This is the arm
    a naive extraction drops, and `tests/test_cv_validate.py` pins it.

    NOT merged with `cv/parse.py`'s `_TRAILING_MARKERS`/`_BULLET_MARKERS`: that tuple is
    deliberately WIDER, and CLAUDE.md is explicit the two must not be shared -- a marker
    the gate does not citation-check but the parser accepts would render an UNCITED
    bullet into the PDF ungated.
    """
    profile, work = [], []
    in_work = in_profile = False
    for i, line in enumerate(cv_text.splitlines(), 1):
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile = True
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile = False, False
        if in_profile:
            profile.append((i, line))
        if in_work and line.lstrip().startswith(("-", "•", "*")):
            work.append((i, line))
    return profile, work
```

Then rewrite `validate`'s loop to consume it, leaving every check's BODY byte-identical.

- [ ] **Step 4: Run the whole CV suite**

Run: `.venv/bin/python -m pytest tests/test_cv_validate.py tests/test_cv_engine.py tests/test_cv_parse.py -v`
Expected: PASS. Any red here is the refactor changing gate behaviour — fix the extraction, never the gate's tests.

- [ ] **Step 5: Run, lint, commit**

```bash
git add sluice/cv/validate.py tests/test_cv_validate.py
git commit -m "refactor(cv): extract section_spans from validate's loop, pinned by equivalence (#167)"
```

---

### Task 11: The three new CV config keys

**Files:**
- Modify: `sluice/cv/config.py` (`CvConfig` + `load_cv_config`)
- Modify: `sluice.yaml.example`, `docs/CONFIGURATION.md`
- Test: `tests/test_sluice_neutral_defaults.py`, `tests/test_cv_config.py`

**Interfaces:**
- Produces: `CvConfig.voice_check: bool = False`, `CvConfig.style_hold: bool = False`, `CvConfig.slop_allow: list = []`.

- [ ] **Step 1: Write the failing tests**

```python
def test_voice_check_and_style_hold_ship_off():
    # voice_check: an unconfigured install must never start spending LLM calls the moment
    # it upgrades -- the company_resolve_llm precedent (triage/config.py:77).
    # style_hold: require_signoff defaults True and gates FABRICATION; riding it would
    # withhold tailored_cv on ~40 stems at shipped defaults, and a rendered CV with no
    # pointer is inert to apply/select.
    assert CvConfig().voice_check is False
    assert CvConfig().style_hold is False


def test_slop_allow_rejects_a_phrase_that_is_not_in_the_list_it_subtracts_from():
    # slop_allow SUBTRACTS, so an entry that matches nothing is silently inert -- and
    # because _PHRASES holds STEMS, `leveraged` is exactly the entry a user would write.
    # Fail loudly at construction, naming the valid stems.
    with pytest.raises(ValueError, match="leveraged"):
        _load_cv_with(slop_allow=["leveraged"])
    assert _load_cv_with(slop_allow=["leverage"]).slop_allow == ["leverage"]
```

- [ ] **Step 2: Run to verify they fail**, then implement in `CvConfig`:

```python
    # Whether the model-judged VOICE check runs at all (#167). OFF by default: an
    # unconfigured install must never start spending LLM calls the moment it upgrades --
    # the company_resolve_llm precedent. This does NOT make #167's fix inert: the
    # deterministic phrase matches still reach the composer's retry either way, which is
    # the issue's actual complaint.
    voice_check: bool = False
    # Whether a STYLE finding that survives the retry WITHHOLDS the send-ready pointer
    # (#167). OFF by default, and deliberately NOT riding `require_signoff`, whose True
    # default was chosen for FABRICATION. Riding it would mean a hard-clean CV containing
    # any of ~40 case-insensitive stems in PROFILE prose or a WORK bullet has
    # `tailored_cv` withheld at shipped defaults -- and a rendered CV with no pointer is
    # inert to apply/select, so "the CV still renders" understates the cost. Via the
    # source-material vector (the composer is told to reuse the bundle's wording), one
    # phrase in an Experience Library entry would hold EVERY lead composed from it.
    style_hold: bool = False
    # Phrases from slop._PHRASES this candidate legitimately uses in their own voice.
    # NB this is NOT abstain-shaped: it SUBTRACTS from a hardcoded list, so empty means
    # FULL enforcement -- the dossier_allow_hosts polarity. What makes the shipped
    # default safe is `style_hold` being off, not this list being empty.
    slop_allow: list = field(default_factory=list)
```

In `load_cv_config`, validate against `_PHRASES` and raise listing the valid stems.

- [ ] **Step 3: Document, run, lint, commit**

```bash
git add sluice/cv/config.py sluice.yaml.example docs/CONFIGURATION.md tests/
git commit -m "feat(cv): add voice_check, style_hold and slop_allow, all inert by default (#167)"
```

---

### Task 12: The scoped STYLE tier

**Files:**
- Modify: `sluice/cv/slop.py`
- Test: `tests/test_cv_slop.py`

**Interfaces:**
- Consumes: `section_spans` (Task 10), `slop_allow` (Task 11).
- Produces: `slop.check_hard(text) -> list`, `slop.check_phrases(lines, *, allow=()) -> list`. `check_text` stays as a thin wrapper for its existing test callers.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_phrases_reports_the_matched_stem_with_its_line_number():
    lines = [(4, "- Cut latency by leveraging a cache [e1]")]
    assert [(n, p.lower()) for n, p, _ in check_phrases(lines)] == [(4, "leverage")]


def test_check_phrases_sees_ONLY_the_lines_it_is_given():
    # This function has no opinion about scoping -- it matches whatever it is handed.
    # An employer line handed to it WOULD match, which is precisely why the engine must
    # not hand it one (pinned in Task 13): `SLOP leverage: <employer line>` arrives in
    # the retry under "Fix these and re-emit the FULL CV" and is answerable only by
    # renaming the employer, turning a style rule into fabrication pressure -- the
    # LOCATION-refusal shape CLAUDE.md records as the worst case this codebase shipped.
    employer_line = [(1, "Leverage Partners Ltd")]
    assert check_phrases(employer_line) != [], "no scoping happens in this function"
    assert check_phrases([]) == []


def test_an_allowed_phrase_is_not_reported():
    lines = [(4, "- Leveraged a cache [e1]")]
    assert check_phrases(lines) != []
    assert check_phrases(lines, allow=("leverage",)) == []


def test_check_hard_still_scans_every_line():
    # HARD is NOT scoped: an em dash in an employer line is always fixable without
    # inventing anything, unlike a phrase.
    assert check_hard("Example Co — Ltd") != []
```

- [ ] **Step 2: Run to verify they fail**, then implement — keeping `slop.py` pure and dependency-free (the engine calls `section_spans`, not this module):

```python
def check_hard(text: str):
    """The BLOCKING tier: em dash and literal `--`, over the WHOLE document."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for label, rx in HARD:
            if rx.search(line):
                out.append((i, label, line.strip()[:80]))
    return out


def check_phrases(lines, *, allow=()):
    """The STYLE tier, over the (lineno, text) pairs the caller chose to scope it to.

    Takes LINES rather than a document, deliberately: this module stays pure and
    dependency-free, and the PROFILE/WORK scoping lives in `cv/engine.py`, which already
    owns the tier policy and the allow list. Importing `cv/validate.py` here to do the
    split would invert the layering for no gain.
    """
    lowered = {a.lower() for a in allow}
    out = []
    for lineno, line in lines:
        for m in _PHRASE_RE.finditer(line):
            if m.group(1).lower() not in lowered:
                out.append((lineno, m.group(1), line.strip()[:80]))
    return out


def check_text(text: str):
    """Back-compat wrapper: (hard errors, phrase warns over EVERY line).

    Retained only for the fixture-cleanliness guards in tests/test_cv_engine.py and
    tests/test_cv_parse.py. Production reads `check_hard` and `check_phrases`.
    """
    return check_hard(text), check_phrases(list(enumerate(text.splitlines(), 1)))
```

- [ ] **Step 3: Run, lint, commit**

```bash
git add sluice/cv/slop.py tests/test_cv_slop.py
git commit -m "feat(cv): split the slop linter into a blocking and a scoped style tier (#167)"
```

---

### Task 13: The loop retains the last hard-clean draft — and rebinds before the audit

**Files:**
- Modify: `sluice/cv/engine.py:223-410`
- Test: `tests/test_cv_engine.py`

**Interfaces:**
- Consumes: `check_hard`, `check_phrases` (Task 12), `section_spans` (Task 10), `style_hold` (Task 11).
- Produces: nothing new externally; changes which draft downstream consumers see.

**The highest-risk task in the plan.** Read the spec's "The loop keeps the last hard-clean draft" before starting.

- [ ] **Step 1: Write the failing tests**

```python
def test_skipped_gate_IFF_no_attempt_was_ever_hard_clean(tmp_path):
    # The safety property as ONE structural assertion. The loop is two attempts, so the
    # space is SEQUENCES, not per-attempt outcomes -- a four-combination table only
    # samples what this states.
    for seq, expect_render in [
        (["hard-dirty", "hard-dirty"], False),
        (["hard-dirty", "clean"], True),
        (["clean", "clean"], True),
        (["hard-clean-style-dirty", "hard-dirty"], True),   # the regression
    ]:
        res = _run_one_with_drafts(tmp_path, seq)
        assert (res.status != "skipped-gate") == expect_render, (seq, res.status)


def test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty(tmp_path):
    # Today the loop BREAKS on the first hard-clean attempt and that text renders. A
    # naive "both tiers must be clean" loop discards it to chase a phrase, and a
    # hard-dirty attempt 2 then bins a lead that renders today.
    res = _run_one_with_drafts(tmp_path, ["hard-clean-style-dirty", "hard-dirty"])
    assert res.status != "skipped-gate"
    assert "leverag" in _rendered_text(res).lower(), "attempt 1 is what must render"


def test_a_phrase_in_an_EMPLOYER_line_never_reaches_the_retry(tmp_path):
    # The scoping guarantee, pinned where the scoping actually happens. `check_phrases`
    # itself has no opinion (Task 12); the engine is what must hand it only PROFILE prose
    # and WORK bullets. A retry message naming an employer line is answerable only by
    # renaming the employer -- a style rule turned into fabrication pressure.
    prompts = _run_one_capturing_prompts(
        tmp_path, cv_with_employer="Leverage Partners Ltd")
    assert not any("Leverage Partners" in p for p in prompts), prompts


def test_the_audit_runs_over_the_RENDERED_draft_not_the_discarded_one(tmp_path):
    # cv_text is read post-loop by run_audit, whose flags drive unsupported_claims ->
    # hold_for_signoff -> the withheld tailored_cv. Auditing the discarded attempt while
    # rendering the retained one means a fabricated claim in the SERVED CV goes un-held,
    # is written send-ready, and the run reports "audit flags: 0".
    audited = []
    res = _run_one_with_drafts(tmp_path, ["hard-clean-style-dirty", "hard-dirty"],
                               on_audit=audited.append)
    assert audited, "the audit must still run"
    assert audited[-1] == _rendered_text(res)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_engine.py -k "hard_clean or RENDERED or IFF" -v`
Expected: FAIL on the two `hard-clean-style-dirty` cases.

- [ ] **Step 3: Implement**

First replace `sluice/cv/engine.py:19`'s import — `check_text` is no longer what production
reads:

```python
from sluice.cv.slop import check_hard as _slop_hard
from sluice.cv.slop import check_phrases as _slop_phrases
from sluice.cv.validate import section_spans, validate as _validate
```

Then the loop:

```python
        retry_msgs = None      # compose() takes prior_violations=None on attempt 1
        best = None            # (cv_text, style_msgs) of the last HARD-clean attempt
        for _ in range(2):
            cv_text = _compose.compose(backend, bundle_text, jd, company, role,
                                       name=cv_name, contact=cv_contact,
                                       employers=cvcfg.employers,
                                       prior_violations=retry_msgs)
            violations = _validate(...)
            ...                                  # structural guards + precheck, unchanged
            hard_msgs = violations + [f"SLOP {lbl}: {snip}"
                                      for _ln, lbl, snip in _slop_hard(cv_text)]
            profile_lines, work_lines = section_spans(cv_text)
            style_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip in
                          _slop_phrases(profile_lines + work_lines,
                                        allow=cvcfg.slop_allow)]
            if not hard_msgs:
                # Remember it. A CV that clears the HARD gate renders today, and must
                # still render after this change: attempt 2 is an unconstrained,
                # non-deterministic compose, so letting a STYLE finding send us back
                # without keeping this draft would bin a lead over a phrase -- exactly
                # the outcome the decision to hold rather than block exists to avoid.
                best = (cv_text, style_msgs)
                if not style_msgs:
                    break
            retry_msgs = hard_msgs + style_msgs

        backend_used = getattr(backend, "last_backend", None)
        if best is None:
            # No attempt was ever HARD-clean -- identical to today's skipped-gate.
            return CvResult(note.ref, "skipped-gate", violations=violations,
                            slop=[s[2] for s in _slop_hard(cv_text)], backend=backend_used,
                            dossier_failed=dossier_failed)
        # REBIND before ANYTHING downstream reads cv_text. The audit below, the renderer,
        # the served pointer and CvResult must every one of them see the draft that is
        # actually shipped. Auditing the discarded attempt while rendering the retained
        # one lets a fabricated claim in the SERVED CV go un-held and be written
        # send-ready, with the run reporting "rendered / audit flags: 0".
        cv_text, style_msgs = best
```

- [ ] **Step 4: Run the tests**, then the full suite.

- [ ] **Step 5: Witness the rebind**

Commit first. Then DELETE the `cv_text, style_msgs = best` line and run
`.venv/bin/python -m pytest tests/test_cv_engine.py::test_the_audit_runs_over_the_RENDERED_draft_not_the_discarded_one -v`.
Expected: FAIL. Confirm no sibling test fails with it — today's loop breaks on the first hard-clean attempt, so no pre-existing fixture produces a hard-clean-then-hard-dirty sequence and none of them can witness this.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "fix(cv): render the last hard-clean draft, and audit the draft that ships (#167)"
```

---

### Task 14: `cv/voice.py` and its wiring

**Files:**
- Create: `sluice/cv/voice.py`
- Modify: `sluice/cv/engine.py`
- Test: `tests/test_cv_voice.py`

**Interfaces:**
- Produces: `voice.build_voice_prompt(cv_text) -> str`, `voice.run_voice(backend, cv_text) -> tuple[str, list]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_voice_returns_the_flagged_lines():
    backend = _BackendReturning("flag\tThis reads like a press release.\n")
    _report, findings = run_voice(backend, "PROFILE\nProse.\n")
    assert findings == ["flag\tThis reads like a press release."]


def test_a_backend_error_degrades_to_no_findings_rather_than_blocking(tmp_path):
    # Fails OPEN, exactly as the fabrication audit does: a gate must never be harder
    # than the check that actually ran.
    res = _run_one(tmp_path, voice_check=True, backend=_BackendRaising())
    assert res.status == "rendered"
    assert res.voice_flags == []


def test_the_voice_check_does_not_run_while_the_hard_gate_is_dirty(tmp_path):
    # No point spending a call on a CV about to be recomposed for citation reasons.
    calls = _run_one(tmp_path, voice_check=True, drafts=["hard-dirty", "clean"]).backend_calls
    assert calls.count("voice") == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_voice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.cv.voice'`.

- [ ] **Step 3: Create `sluice/cv/voice.py`**

```python
# sluice/cv/voice.py
"""Model-judged VOICE check. Flags AI-tell phrasing a fixed blocklist cannot catch.

Deliberately a separate module from `cv/audit.py` rather than a second prompt inside it:
the two have opposite consequences (the fabrication audit NEVER blocks; a voice finding
holds the send-ready pointer when `cv.style_hold` is on) and separate config gates, so
folding them together would put both behaviours behind one name.

Opt-in (`cv.voice_check`, default False): an unconfigured install must never start
spending LLM calls the moment it upgrades. The deterministic phrase tier runs either way,
so #167's actual complaint -- that the linter's matches were computed and discarded -- is
closed regardless of this module.

The model call goes through core/backends, never a hardcoded host path.
"""


def build_voice_prompt(cv_text: str) -> str:
    return (
        "You are judging the VOICE of a CV, not its accuracy. Flag lines that read as "
        "machine-generated: inflated register, empty intensifiers, corporate cliche, "
        "hollow abstraction, or a claim shaped like a slogan.\n"
        "Judge the writing ONLY. Do not comment on whether a claim is true, and do not "
        "suggest new content.\n"
        "Output one line per finding: flag\\t<the offending phrase>\\t<why, in under 12 "
        "words>. Output nothing at all if the writing is clean.\n\n"
        "=== CV ===\n" + cv_text + "\n"
    )


def run_voice(backend, cv_text: str):
    """(raw report, flagged lines). Pure over the backend's reply."""
    report = backend.complete(build_voice_prompt(cv_text))
    flagged = [line for line in report.splitlines()
               if line.strip().lower().startswith("flag")]
    return report, flagged
```

- [ ] **Step 4: Wire it into the loop**

Inside `if not hard_msgs:`, before `best` is assigned, and only when `cvcfg.voice_check`:

```python
                voice_flags = []
                if cvcfg.voice_check:
                    # Only while the HARD gate is clean: there is no point spending a
                    # call on a CV that is about to be recomposed for citation reasons.
                    #
                    # Fails OPEN, exactly as the fabrication audit does -- a backend
                    # error or timeout must not make the gate HARDER than the check that
                    # actually ran. Swallow and log; never propagate.
                    try:
                        _report, voice_flags = run_voice(backend, cv_text)
                    except Exception as e:
                        _log.warning("voice check failed for %s: %s", note.ref, e)
                        voice_flags = []
                style_msgs = style_msgs + [f"VOICE: {f}" for f in voice_flags]
```

- [ ] **Step 5: Run, lint, commit**

```bash
git add sluice/cv/voice.py sluice/cv/engine.py tests/test_cv_voice.py
git commit -m "feat(cv): add an opt-in model-judged voice check (#167)"
```

---

### Task 15: The style hold, and what it records

**Files:**
- Modify: `sluice/cv/engine.py`, `sluice/cli.py:761`
- Test: `tests/test_cv_engine.py`, `tests/test_leads_expire.py` (regression only)

- [ ] **Step 1: Write the failing tests**

```python
def test_a_style_finding_does_not_withhold_the_pointer_by_default(tmp_path):
    res = _run_one(tmp_path, drafts=["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "rendered"
    assert _note(tmp_path).fm.get("tailored_cv"), "style_hold is off by default"


def test_style_hold_withholds_the_pointer_when_enabled(tmp_path):
    res = _run_one(tmp_path, style_hold=True,
                   drafts=["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "needs-signoff"
    assert not _note(tmp_path).fm.get("tailored_cv")


def test_the_signoff_prompt_names_the_kind(capsys, tmp_path):
    # An UNPREFIXED entry -- every hold stamped before this change -- keeps today's
    # wording, so no existing note is re-described.
    _confirm("slug", "cv.pdf", ["unsupported\tclaim", "style\tSLOP leverage: ..."])
    assert "1 unsupported claim(s)" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**, then implement. `hold_for_signoff(ref, *, pending, claims)` keeps its Store-protocol signature: `claims` is a JSON **array** and `core/app.py:1323` reads it as `parsed if isinstance(parsed, list) else [str(parsed)]`, so a wrapped object would collapse into one bogus claim. Prefix each ENTRY instead.

**No refusal reads the kind.** `leads expire` and `leads dismiss` keep refusing any `pending_cv` hold exactly as today — `require_blank` is a frozenset of FIELD NAMES re-read CAS-fresh inside the write transform and cannot correlate `pending_cv` with a kind stored in `needs_signoff`; making it do so needs either a third guard parameter or a hoist out of the CAS transform, which is the guard-read-before-the-write shape this repo has fixed three times.

- [ ] **Step 3: Confirm the seven refusal pins are UNTOUCHED**

Run: `.venv/bin/python -m pytest tests/test_leads_expire.py tests/test_leads_dismiss.py tests/test_leads_dismiss_cli.py tests/test_leads_expire_cli.py tests/test_mcpserver.py -v`
Expected: PASS, unmodified. They seed `pending_cv` with no `needs_signoff` payload; if any needs editing, the kind has leaked into a refusal and the change is wrong.

- [ ] **Step 4: Run, lint, commit**

```bash
git add sluice/cv/engine.py sluice/cli.py tests/
git commit -m "feat(cv): hold a style finding for sign-off when style_hold is on (#167)"
```

---

### Task 16: Give `voice_flags` and `slop` readers

**Files:**
- Modify: `sluice/cv/engine.py` (`CvResult`), `sluice/cli.py` (`cmd_cv_run`), `sluice/mcpserver.py:316-318`
- Test: `tests/test_cv_engine.py`, `tests/functional/test_mcp_contract.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_cmd_cv_run_prints_the_style_and_voice_findings(capsys, tmp_path):
    # `slop` has had NO reader since it was added; a field nothing reads is a dead flag
    # dressed as reporting.
    _cli_cv_run(tmp_path, drafts=["hard-clean-style-dirty", "hard-dirty"])
    assert "SLOP" in capsys.readouterr().out


def test_the_mcp_projection_carries_voice_flags_under_the_untrusted_warning():
    payload = _mcp_cv_run()
    assert "voice_flags" in payload
    assert UNTRUSTED_DERIVED_CONTENT_WARNING in payload["voice_flags_note"]
```

- [ ] **Step 2: Run to verify they fail**, then add `voice_flags: list = field(default_factory=list)` to `CvResult` (leaving `audit_flags` meaning fabrication only), print both in `cmd_cv_run`, and add them to the MCP projection under the same `UNTRUSTED_DERIVED_CONTENT_WARNING` that already guards `audit_flags` — `voice_flags` is model-derived text and needs identical framing.

- [ ] **Step 3: Run, lint, commit**

```bash
git add sluice/cv/engine.py sluice/cli.py sluice/mcpserver.py tests/
git commit -m "feat(cv): surface the style and voice findings on both surfaces (#167)"
```

---

### Task 17: Render the compose prompt's ban list from `_PHRASES`

**Files:**
- Modify: `sluice/cv/compose.py:14`, `sluice/cv/slop.py` (`_PHRASES` gains `drove`)
- Test: `tests/test_prompt.py` or `tests/test_cv_compose.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_prompt_names_exactly_the_phrases_the_gate_enforces():
    # compose.py banned `drove` while _PHRASES did not hold it: banned in prose,
    # unchecked in code, with nothing keeping the two in step. Equality, not subset --
    # the prompt named INFLECTIONS (spearheaded) while _PHRASES holds STEMS
    # (spearhead), so a subset test would fail on wording that is not in disagreement.
    assert _phrases_named_in(build_prompt(...)) == set(_PHRASES)


def test_an_allowed_phrase_is_not_instructed_against_either():
    # Otherwise slop_allow suppresses the hold while the model is still told to avoid the
    # phrase on every compose -- the candidate's own voice composed out regardless.
    prompt = build_prompt(..., slop_allow=["leverage"])
    assert "leverage" not in _phrases_named_in(prompt)
```

- [ ] **Step 2: Run to verify they fail**, then render the sentence from `_PHRASES - slop_allow` and add `drove` to `_PHRASES` — low-risk precisely because the tier holds rather than blocks, and off by default.

- [ ] **Step 3: Run, lint, commit**

```bash
git add sluice/cv/compose.py sluice/cv/slop.py tests/
git commit -m "fix(cv): render the prompt's ban list from the enforced phrase list (#167)"
```

---

### Task 18: Documentation and rulesync

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`
- Regenerate: `CLAUDE.md`, `AGENTS.md`, `.claude/`

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`** — the cache contract and `jd_arrived`, the sixth triage state and the one selection default, `health_report`'s optional lead walk, and the two CV tiers.

- [ ] **Step 2: Update `.rulesync/rules/CLAUDE.md`** — never the generated `CLAUDE.md`, which is gitignored drift. Assert nothing that is not true of the code as merged.

- [ ] **Step 3: Regenerate and verify no drift**

```bash
npm ci --ignore-scripts && npm run rulesync
```

- [ ] **Step 4: Run, lint, commit**

```bash
git add docs/ARCHITECTURE.md .rulesync/
git commit -m "docs: describe the JD-arrival contract and the two CV gate tiers (#167, #169)"
```

---

## Definition of done

- [ ] `.venv/bin/python -m pytest` — all green (4403 at branch point, plus this plan's additions).
- [ ] `.venv/bin/python -m ruff check sluice tests scripts` — clean.
- [ ] `.venv/bin/python -m pytest --cov` — reviewed, not gated.
- [ ] The seven sign-off refusal pins listed in Task 15 pass **unmodified**.
- [ ] `/review-pr` run before pushing, then CodeRabbit.
- [ ] PR #172 taken out of draft.
