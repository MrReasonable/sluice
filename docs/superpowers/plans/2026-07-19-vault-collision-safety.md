# Vault collision safety (#5) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two genuinely different jobs sharing a company and title no longer collapse into one note — the vault splits them on a proven location `DIFFERENT`, and never silently loses the second.

**Architecture:** A pure `same_opportunity(note_fm, lead, noise)` verdict (`core/leads.py`) drives a `Vault._resolve_path` candidate walk (`Company - Title.md`, then `Company - Title - {Location}.md`) that advances only on `DIFFERENT`. `upsert` returns four outcomes (created/updated/merged/refused); the sink records only "a note now exists" outcomes so `refused` (and #24's `skipped`) retry. Builds on merged #24 (byte-cap, `OSError→skipped`) and #25 (`_compare_locations`).

**Tech Stack:** Python 3.12+ standard library only. pytest + faker. No new dependencies.

**Design doc:** `docs/superpowers/specs/2026-07-19-vault-collision-safety-design.md` (five-agent plan-reviewed; findings folded).

## Global Constraints

Copied from CLAUDE.md and the spec; every task implicitly includes these:

- **`sluice/` is standard-library only.** `same_opportunity`/`_resolve_path`/`_note_name` use `re`/`os` only. No `hashlib` (no URL-hash candidate). No new dependency.
- **Never-clobber.** UPDATE and MERGE bump **only** `last_seen` (via `_bump_last_seen`); REFUSE writes nothing; CREATE is a genuinely new note. **`_path_for` (candidate 1) stays byte-identical to today** — zero migration, pinned by the existing `test_filename_sanitizes_slashes_and_colons` / `test_byte_clamp_is_a_noop_for_a_name_that_fits` staying green.
- **Empty-config-abstains.** `location_noise_words` defaults `[]` (no subtraction); guarded by an added assertion in `test_ingest_defaults_carry_no_preference`.
- **No personal data.** **Every test location comes from the synthetic seeded `LOCATIONS` constant** in `tests/conftest.py` (from `fake.city()`, three token-disjoint entries), imported via `from tests.conftest import LOCATIONS` — **never a real place typed inline** (`"London"`/`"Manchester"`/`"Paris"` in the task test blocks below are stand-ins; replace each with `LOCATIONS[0]`/`[1]`/`[2]` when implementing). Because `fake.city()` can be multi-word, assert candidate-2 existence with a **glob** (`"X - Y - *.md"`) or a slug **count/set**, not an exact suffixed filename; seed a specific candidate-2 note via `v._note_name("X - Y", LOCATIONS[i])`. `LOCATIONS[0]`/`[1]`/`[2]` are guaranteed token-disjoint, so they read `DIFFERENT` pairwise.
- **No silent failures.** REFUSE logs, is counted, and is kept out of `seen.db` (retried). The sink guard is a commented per-lead allowlist.
- **Comments explain *why*.** Match the existing density.
- **Conventional commits** (`feat(leads):`, `fix(vault):`, `test(vault):`), each ending with the trailer:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Mutation discipline.** Before proving a test is load-bearing, run once:
  `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
  Then mutate by **moving or deleting**, never adding a check beside the original. Use the **same** interpreter (`.venv/bin/python`) for compileall and pytest. Run the mutant in isolation; look at what the function returns; restore; confirm green.
- **Tests are offline, hermetic, behaviour-asserting**, and assert on a **set** of slugs, never a positional `read_leads()[i]`.

**Scope boundary:** merging pre-existing duplicates / the read key / `existing_keys()` → **#23**; extraction quality → **#6**; `cv`'s `notes[0]` pick → its own follow-up. Do not touch them.

---

### Task 1: `same_opportunity` — the pure identity verdict

**Files:**
- Modify: `sluice/core/leads.py` (add the function near `slug_matches`)
- Test: `tests/test_leads_identity.py` (new)

**Interfaces:**
- Consumes: `_norm_url`, `_compare_locations`, `SAME`/`DIFFERENT`/`UNKNOWN` (all in `core/leads.py`).
- Produces: `same_opportunity(note_fm: dict, lead: Lead, noise=frozenset()) -> str` returning `SAME`/`DIFFERENT`/`UNKNOWN`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_leads_identity.py`:

```python
from sluice.core.leads import Lead, same_opportunity, SAME, DIFFERENT, UNKNOWN


def _lead(**kw):
    base = dict(source="b", search="s", title="Analyst", company="Acme",
                url="https://a/1", location="London")
    base.update(kw)
    return Lead(**base)


def test_matching_nonempty_urls_are_proof_of_same():
    fm = {"url": "https://a/1?utm=x", "location": "Manchester"}   # different location, but url proves same
    assert same_opportunity(fm, _lead(url="https://a/1?utm=x"), frozenset()) == SAME


def test_empty_urls_are_never_proof_the_google_trap():
    # Two url-less leads sharing company+title must NOT match on empty urls; defer to location.
    fm = {"url": "", "location": "Manchester"}
    assert same_opportunity(fm, _lead(url="", location="London"), frozenset()) == DIFFERENT


def test_defers_to_location_when_urls_do_not_prove():
    fm = {"url": "https://a/2", "location": "London EC4Y"}         # different url -> not proof
    assert same_opportunity(fm, _lead(url="https://a/1", location="London"), frozenset()) == SAME  # overlap
    fm2 = {"url": "https://a/2", "location": "Manchester"}
    assert same_opportunity(fm2, _lead(url="https://a/1", location="London"), frozenset()) == DIFFERENT


def test_absent_location_is_unknown_never_splits():
    fm = {"url": "https://a/2", "location": ""}
    assert same_opportunity(fm, _lead(url="https://a/1", location="London"), frozenset()) == UNKNOWN


def test_noise_word_flips_a_verdict():
    # 'Remote' vs 'London' is DIFFERENT by default; adding 'remote' to noise empties one side -> UNKNOWN.
    fm = {"url": "", "location": "Remote"}
    assert same_opportunity(fm, _lead(url="", location="London"), frozenset()) == DIFFERENT
    assert same_opportunity(fm, _lead(url="", location="London"), frozenset({"remote"})) == UNKNOWN
```

- [ ] **Step 2: Run — expect fail** (`ImportError: cannot import name 'same_opportunity'`):

Run: `.venv/bin/python -m pytest tests/test_leads_identity.py -v`

- [ ] **Step 3: Implement** — add to `sluice/core/leads.py` (right after `slug_matches`):

```python
def same_opportunity(note_fm: dict, lead: "Lead", noise=frozenset()) -> str:
    """Whether an EXISTING note and an incoming lead are the same opportunity, as a
    SAME/DIFFERENT/UNKNOWN verdict. A matching NON-EMPTY url is the only proof (SAME);
    otherwise defer to the location comparison. DIFFERENT is the only verdict #5 splits
    on, so a wrong SAME/UNKNOWN merely merges (today's behaviour) while a wrong DIFFERENT
    is a visible extra note. Both urls must be non-empty: google leads carry url:"" and
    _norm_url("")==_norm_url(""), so "urls match -> same" would merge every url-less lead
    sharing a company+title -- the exact loss this removes."""
    note_url = note_fm.get("url", "")
    if lead.url and note_url and _norm_url(lead.url) == _norm_url(note_url):
        return SAME
    return _compare_locations(note_fm.get("location", ""), lead.location, noise)
```

- [ ] **Step 4: Run — expect pass:** `.venv/bin/python -m pytest tests/test_leads_identity.py -v`

- [ ] **Step 5: Mutation witness.** `compileall` (see Global Constraints), then:
  - Delete the both-non-empty guard (mutate to `if _norm_url(lead.url) == _norm_url(note_url):`) → `test_empty_urls_are_never_proof_the_google_trap` reddens (`""=="" → SAME`, expected DIFFERENT). Restore.
  - Replace the fall-through with `return SAME` → `test_defers_to_location...` and `test_absent_location...` redden. Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/leads.py tests/test_leads_identity.py
git commit -m "feat(leads): same_opportunity — the pure identity verdict for #5

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: `location_noise_words` on root Config

**Files:**
- Modify: `sluice/core/config.py` (`Config` dataclass + `load_config`)
- Modify: `sluice.yaml.example` (a commented line)
- Test: `tests/test_sluice_neutral_defaults.py` (`test_ingest_defaults_carry_no_preference`), `tests/test_config.py`

**Interfaces:**
- Produces: `Config.location_noise_words: list` (default `[]`); `load_config` reads `location_noise_words`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_sluice_neutral_defaults.py` inside `test_ingest_defaults_carry_no_preference` (beside the other `assert c.<gate> == []` lines):

```python
    assert c.location_noise_words == []          # #5 gate abstains: no noise subtracted by default
```

Add to `tests/test_config.py`:

```python
def test_load_config_reads_location_noise_words(tmp_path, monkeypatch):
    from sluice.core.config import load_config
    p = tmp_path / "s.yaml"
    p.write_text("location_noise_words:\n  - remote\n  - hybrid\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config().location_noise_words == ["remote", "hybrid"]
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: 'Config' object has no attribute 'location_noise_words'`):

Run: `.venv/bin/python -m pytest tests/test_sluice_neutral_defaults.py::test_ingest_defaults_carry_no_preference tests/test_config.py::test_load_config_reads_location_noise_words -v`

- [ ] **Step 3: Implement.** In `sluice/core/config.py`, add the field to `Config` (beside `relevance_keep`):

```python
    location_noise_words: list = field(default_factory=list)
```

In `load_config`'s `return Config(...)`, add:

```python
                  location_noise_words=list(data.get("location_noise_words") or []),
```

In `sluice.yaml.example`, add near the `locations:` block, commented:

```yaml
# Words that decorate a location without locating it, subtracted before comparing two
# postings for #5's split (e.g. so "Remote" and "London" merge instead of splitting).
# Empty by default -> nothing subtracted. Uncomment and edit to taste.
# location_noise_words:
#   - remote
```

- [ ] **Step 4: Run — expect pass** (same command as Step 2).

- [ ] **Step 5: Mutation witness.** `compileall`, then delete the `location_noise_words=` line from `load_config`'s `Config(...)` call → `test_load_config_reads_location_noise_words` reddens (`[] != ["remote","hybrid"]`). Restore. (The default-`[]` assertion is guarded by the dataclass default; deleting `field(default_factory=list)` breaks construction — the neutral-defaults assertion covers a non-empty default.) Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_sluice_neutral_defaults.py tests/test_config.py
git commit -m "feat(config): location_noise_words gate (defaults empty, abstains)

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: `_note_name` — one shared name helper; refactor `_path_for`

**Files:**
- Modify: `sluice/core/vault.py` (add `_SEP`/`_SUFFIX_MAX` constants, `Vault._note_name`, refactor `_path_for`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `_clamp_bytes`, `Vault._name_max` (existing).
- Produces: `Vault._note_name(stem: str, suffix: str = "") -> str`; `_path_for` unchanged in signature and **byte-identical output**.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_vault.py`:

```python
def test_note_name_candidate1_matches_path_for(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    stem = "Acme - Analyst"
    assert v._note_name(stem) == "Acme - Analyst"


def test_note_name_suffix_appends_sanitized_location(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    assert v._note_name("Acme - Analyst", "London/EC4") == "Acme - Analyst - London-EC4"


def test_note_name_bounds_suffix_so_stem_budget_never_negative(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    # a 200-char location is clamped to _SUFFIX_MAX(40); the stem keeps >= 77 chars.
    out = v._note_name("C" * 200, "L" * 200)
    stem, _, suffix = out.rpartition(" - ")
    assert len(suffix) == 40 and len(stem) == 120 - len(" - ") - 40
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: ... '_note_name'`):

Run: `.venv/bin/python -m pytest tests/test_vault.py -k note_name -v`

- [ ] **Step 3: Implement.** In `sluice/core/vault.py`, add module constants near `_DEFAULT_VAULT`:

```python
_SEP = " - "        # identity-determining; stays a literal (see the design's Config-first)
_SUFFIX_MAX = 40    # identity-determining; stays a literal
```

Add the method (next to `_path_for`):

```python
    def _note_name(self, stem: str, suffix: str = "") -> str:
        """The note stem for a lead, sanitized + char-capped + byte-clamped. BOTH name
        candidates go through this one helper so their truncation can never drift (a
        candidate 2 that sanitized differently would mis-key and duplicate). .replace is a
        length-preserving per-char map, so stem.replace()[:120] == today's [:120].replace()
        -- candidate 1 stays byte-identical, so no existing note moves."""
        stem = stem.replace("/", "-").replace(":", "-")
        if suffix:
            suffix = suffix.replace("/", "-").replace(":", "-")[:_SUFFIX_MAX]
            name = stem[:120 - len(_SEP) - len(suffix)] + _SEP + suffix
        else:
            name = stem[:120]
        return _clamp_bytes(name, self._name_max() - len(b".md"))
```

Refactor `_path_for` to call it (keep the docstring):

```python
    def _path_for(self, lead: Lead) -> str:
        """... existing docstring ..."""
        name = self._note_name(f"{lead.company} - {lead.title}")
        return os.path.join(self.leads_dir, f"{name}.md")
```

- [ ] **Step 4: Run — expect pass, including every pre-existing vault test** (zero-migration proof):

Run: `.venv/bin/python -m pytest tests/test_vault.py -v`
Expected: PASS, and specifically `test_filename_sanitizes_slashes_and_colons`, `test_byte_clamp_is_a_noop_for_a_name_that_fits`, and `test_long_non_ascii_name_fits_the_byte_budget` stay green — that is the zero-migration guarantee.

- [ ] **Step 5: Mutation witness.** `compileall`, then in `_note_name` swap the suffix-first bound to compose-then-cap (`name = (stem + _SEP + suffix)[:120]`) → `test_note_name_bounds_suffix...` reddens (suffix truncated away). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): one shared _note_name helper for both name candidates

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: wire `location_noise_words` from Config to the Vault

**Files:**
- Modify: `sluice/core/vault.py` (`Vault.__init__`)
- Modify: `sluice/stores/vault.py` (`_make`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `Config.location_noise_words` (Task 2).
- Produces: `Vault(location_noise_words=...)` storing `self._noise: frozenset`; `_make` passes it.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_vault.py`:

```python
def test_make_threads_noise_words_from_config(tmp_path, monkeypatch):
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    v = store_mod._make(Config(location_noise_words=["remote"]))
    assert v._noise == frozenset({"remote"})
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: ... '_noise'`):

Run: `.venv/bin/python -m pytest tests/test_vault.py -k make_threads_noise -v`

- [ ] **Step 3: Implement.** In `Vault.__init__`, add the parameter and field:

```python
    def __init__(self, dir: str | None = None, *, baseline_rel: str = _MYCV_BASELINE,
                 location_noise_words=()):
        self.dir = dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT)
        self.leads_dir = os.path.join(self.dir, _LEADS_SUBDIR)
        self.baseline_rel = baseline_rel
        self._name_max_cache: int | None = None
        self._noise = frozenset(location_noise_words or ())
```

In `sluice/stores/vault.py`, `_make`:

```python
def _make(config):
    """Build the vault store. ..."""
    return Vault(baseline_rel=config.baseline_rel,
                 location_noise_words=config.location_noise_words)
```

- [ ] **Step 4: Run — expect pass** (same command as Step 2).

- [ ] **Step 5: Mutation witness.** `compileall`, then delete the `location_noise_words=config.location_noise_words` arg from `_make` → `test_make_threads_noise...` reddens (`frozenset() != {"remote"}`). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py sluice/stores/vault.py tests/test_vault.py
git commit -m "fix(vault): thread location_noise_words from Config through _make

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: `_resolve_path` — the candidate walk

**Files:**
- Modify: `sluice/core/vault.py` (add `Vault._resolve_path`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `_note_name` (Task 3), `same_opportunity` (Task 1), `self._noise` (Task 4), `_split_frontmatter`/`_fm_dict`/`_read` (existing).
- Produces: `Vault._resolve_path(lead) -> tuple[str | None, str]` where action is `"create"`/`"update"`/`"merge"`/`"refuse"`; `path` is `None` only for `"refuse"`.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_vault.py` (imports `same_opportunity` not needed; use `Vault`):

```python
def _seed_note(tmp_path, name, location="", url=""):
    from sluice.core.vault import _LEADS_SUBDIR
    d = tmp_path / _LEADS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\ncompany: "X"\nrole: "Y"\nlocation: "{location}"\nurl: "{url}"\n---\n\nbody\n')


def test_resolve_path_free_candidate1_creates(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    path, action = v._resolve_path(_lead(company="X", title="Y", location="London", url="https://a/1"))
    assert action == "create" and path.endswith("X - Y.md")


def test_resolve_path_same_url_updates(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="Manchester", url="https://a/1")
    _, action = v._resolve_path(_lead(company="X", title="Y", location="London", url="https://a/1"))
    assert action == "update"


def test_resolve_path_different_location_advances_to_candidate2_create(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="London", url="https://a/1")
    path, action = v._resolve_path(_lead(company="X", title="Y", location="Manchester", url="https://a/2"))
    assert action == "create" and path.endswith("X - Y - Manchester.md")


def test_resolve_path_absent_location_merges_at_candidate1_never_orphans(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="London", url="")     # note has a location, lead does not
    _, action = v._resolve_path(_lead(company="X", title="Y", location="", url=""))
    assert action == "merge"


def test_resolve_path_refuses_when_frontmatter_contradicts_filename(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="London", url="")             # candidate 1: DIFFERENT from Manchester
    _seed_note(tmp_path, "X - Y - Manchester", location="Paris", url="") # candidate 2 fm contradicts its filename
    path, action = v._resolve_path(_lead(company="X", title="Y", location="Manchester", url=""))
    assert action == "refuse" and path is None
```

Add `_lead` helper at the top of `tests/test_vault.py` if not present — it already has one; reuse it (it builds a `Lead`).

- [ ] **Step 2: Run — expect fail** (`AttributeError: ... '_resolve_path'`):

Run: `.venv/bin/python -m pytest tests/test_vault.py -k resolve_path -v`

- [ ] **Step 3: Implement.** Add `from sluice.core.leads import Lead, _norm_url, same_opportunity, SAME, UNKNOWN` to vault.py's imports (it already imports `Lead, _norm_url`; add `same_opportunity, SAME, UNKNOWN`). Then add:

```python
    def _resolve_path(self, lead: Lead) -> tuple[str | None, str]:
        """Walk the nameable candidates and return (path, action). Candidate 1 is the
        clean `Company - Title` name (always); candidate 2 adds the location suffix (only
        when location is non-empty). Every verdict terminates in place EXCEPT DIFFERENT,
        which advances -- so a note is only ever split on PROVEN difference. Running out
        of candidates (every one a note proven different) is REFUSE: no path can be
        written without clobbering a different job, so path is None. See #5."""
        stem = f"{lead.company} - {lead.title}"
        names = [self._note_name(stem)]
        if lead.location:
            names.append(self._note_name(stem, lead.location))
        for name in names:
            path = os.path.join(self.leads_dir, f"{name}.md")
            if not os.path.exists(path):
                return path, "create"
            inner, _ = _split_frontmatter(_read(path))
            verdict = same_opportunity(_fm_dict(inner), lead, self._noise)
            if verdict == SAME:
                return path, "update"
            if verdict == UNKNOWN:
                return path, "merge"
            # DIFFERENT -> advance to the next nameable candidate
        return None, "refuse"
```

- [ ] **Step 4: Run — expect pass:** `.venv/bin/python -m pytest tests/test_vault.py -k resolve_path -v`

- [ ] **Step 5: Mutation witness.** `compileall`, then:
  - Change the DIFFERENT-advance to terminate: replace the loop's fall-through by adding `return path, "merge"` after the `if verdict == UNKNOWN` block (so DIFFERENT also merges) → `test_resolve_path_different_location_advances...` reddens (`merge` not `create` at candidate 2). Restore.
  - Delete the `if not os.path.exists(path): return path, "create"` line → `test_resolve_path_free_candidate1_creates` reddens. Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): _resolve_path candidate walk — split only on proven difference

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: `Vault.upsert` — four honest outcomes

**Files:**
- Modify: `sluice/core/vault.py` (`Vault.upsert`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `_resolve_path` (Task 5), `_bump_last_seen`, `_render_new`, `_write` (existing), `_log`.
- Produces: `Vault.upsert(lead) -> str` returning `"created"`/`"updated"`/`"merged"`/`"refused"`; signature unchanged.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_vault.py`:

```python
def test_upsert_splits_two_cities_into_two_notes(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v.upsert(_lead(company="X", title="Y", location="London", url="https://a/1")) == "created"
    assert v.upsert(_lead(company="X", title="Y", location="Manchester", url="https://a/2")) == "created"
    names = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
    assert names == {"X - Y.md", "X - Y - Manchester.md"}


def test_upsert_merge_bumps_only_last_seen(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="London", url="")
    before = (_leads_dir(tmp_path) / "X - Y.md").read_text()
    assert v.upsert(_lead(company="X", title="Y", location="", url="", last_seen="2026-07-19")) == "merged"
    after = (_leads_dir(tmp_path) / "X - Y.md").read_text()
    assert "last_seen: 2026-07-19" in after
    assert after.replace("last_seen: 2026-07-19", "X") == \
           (before if "last_seen:" not in before else before)  # body + other keys intact
    # stronger: only last_seen differs
    import re as _re
    assert _re.sub(r"last_seen:.*", "", after) == _re.sub(r"last_seen:.*", "", before)


def test_upsert_refuses_and_writes_nothing(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="London", url="")
    _seed_note(tmp_path, "X - Y - Manchester", location="Paris", url="")
    before = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
    assert v.upsert(_lead(company="X", title="Y", location="Manchester", url="")) == "refused"
    assert {p.name for p in _leads_dir(tmp_path).glob("*.md")} == before   # nothing written
```

- [ ] **Step 2: Run — expect fail** (current `upsert` returns only created/updated; the two-cities test gets `updated` and one note, the refuse test writes/updates):

Run: `.venv/bin/python -m pytest tests/test_vault.py -k "splits_two_cities or upsert_merge or upsert_refuses" -v`

- [ ] **Step 3: Implement.** Replace the body of `Vault.upsert` (keep the `os.makedirs`/`ensure_stfolder` prologue and #24's create-failure unlink guard):

```python
    def upsert(self, lead: Lead) -> str:
        """Create/update/merge/refuse a note. Returns one of
        "created" | "updated" | "merged" | "refused". UPDATE and MERGE bump ONLY
        last_seen (never-clobber); REFUSE writes nothing (every name candidate is a note
        proven different, so writing would clobber one). See #5."""
        os.makedirs(self.leads_dir, exist_ok=True)
        self.ensure_stfolder()
        path, action = self._resolve_path(lead)
        if action == "update":
            self._bump_last_seen(path, lead.last_seen or _today())
            return "updated"
        if action == "merge":
            self._bump_last_seen(path, lead.last_seen or _today())
            return "merged"
        if action == "refuse":
            # Loud, not silent: every name candidate is a note proven DIFFERENT, so no
            # path can be written without clobbering a different job. The sink counts this
            # and keeps the lead out of seen.db, so it is retried (and re-reported) next
            # run rather than lost. See #5.
            _log.warning("vault refused lead %r: all name candidates are notes proven different",
                         lead.dedup_key)
            return "refused"
        try:
            _write(path, self._render_new(lead))
        except OSError:
            # #24: a create whose write fails mid-way leaves a truncated 0-byte file; a
            # later re-scrape would treat the garbage as a real note. Remove it and
            # re-raise so the sink counts it skipped and retries. See #24.
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError as e:
                    _log.warning("could not remove partial note %s: %s", path, e)
            raise
        return "created"
```

- [ ] **Step 4: Run — expect pass:** `.venv/bin/python -m pytest tests/test_vault.py -v` (the whole file; the pre-existing create/update tests still pass).

- [ ] **Step 5: Mutation witness.** `compileall`, then change the `action == "refuse"` branch to fall through to create (delete the `return "refused"` block) → `test_upsert_refuses_and_writes_nothing` reddens (a note is written / `_write(None,...)` errors — the test's "nothing written" set assertion fails). Restore. Then change the `"merge"` branch to rewrite the note (`_write(path, self._render_new(lead))` instead of `_bump_last_seen`) → `test_upsert_merge_bumps_only_last_seen` reddens (body/other keys change). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): upsert returns created/updated/merged/refused

A different job sharing company+title no longer silently overwrites the first;
it splits on a proven location difference, merges on absence of evidence, and
refuses (loud, retried) only when no distinguishing name exists. Merge/update
bump only last_seen; refuse writes nothing.

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 7: sink allowlist — refused/skipped retry

**Files:**
- Modify: `sluice/ingest/sink.py` (`VaultSink.write` loop + module docstring)
- Test: `tests/test_sink.py`

**Interfaces:**
- Consumes: `Vault.upsert` (four outcomes).
- Produces: `VaultSink.write` counts `merged`/`refused` sparsely; records only `created`/`updated`/`merged`.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_sink.py`:

```python
def test_vaultsink_records_merged_but_not_refused(tmp_path, monkeypatch):
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    good = _lead(company="Aye", url="https://a/1")
    refused = _lead(company="Bee", url="https://a/2")

    real = vault.upsert
    def fake(lead):
        return "refused" if lead.url == "https://a/2" else real(lead)
    monkeypatch.setattr(vault, "upsert", fake)

    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([good, refused])
    assert counts.get("created") == 1 and counts.get("refused") == 1
    loaded = seen.load()
    assert "https://a/1" in loaded            # recorded
    assert "https://a/2" not in loaded        # refused -> retried, not swallowed
```

- [ ] **Step 2: Run — expect fail** (current sink records unconditionally, so `https://a/2` lands in seen.db):

Run: `.venv/bin/python -m pytest tests/test_sink.py -k records_merged_but_not_refused -v`

- [ ] **Step 3: Implement.** In `VaultSink.write`, change the record step to an allowlist (keep #24's `try/except OSError`):

```python
            try:
                outcome = self.vault.upsert(lead)  # created | updated | merged | refused
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome in ("created", "updated", "merged"):
                    # allowlist over "a note now exists": refused (and, from the except
                    # below, skipped) stay OUT of recorded -> not in seen.db -> retried
                    # next run. Stated positively so an unknown outcome fails safe. See #5.
                    recorded.append(lead)
            except OSError as e:
                counts["skipped"] += 1
                _log.warning("vault refused lead %r: %s", lead.dedup_key, e)
```

Update the module docstring's VaultSink sentence to name the outcomes:

```python
VaultSink stamps first_seen/last_seen, upserts each lead into the Obsidian vault
(never clobbering status). upsert returns created/updated/merged/refused; only the
first three mean a note now exists, so only those are recorded in seen.db -- refused
(a name-collision decline) and skipped (an OSError write failure) stay un-recorded and
are retried next run. JsonSink emits one JSON object per line. Both return sparse count
dicts (merged/refused keys appear only when non-zero).
```

- [ ] **Step 4: Run — expect pass, and the pre-existing sink tests stay green** (sparse counts preserve their exact-equality assertions):

Run: `.venv/bin/python -m pytest tests/test_sink.py -v`

- [ ] **Step 5: Mutation witness.** `compileall`, then change the allowlist to a denylist (`if outcome != "refused":`) — this still passes the new test, so instead delete the `if outcome in (...)` guard entirely (record unconditionally) → `test_vaultsink_records_merged_but_not_refused` reddens (`https://a/2` in seen.db). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/ingest/sink.py tests/test_sink.py
git commit -m "fix(ingest): sink records only outcomes that mean a note exists

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 8: CLI surfaces `merged` and `refused`

**Files:**
- Modify: `sluice/cli.py` (`_print_report`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `report.written` (sparse dict).
- Produces: the summary line includes `merged`/`refused` when non-zero.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_cli.py`:

```python
def test_print_report_surfaces_merged_and_refused(capsys):
    from sluice.cli import _print_report

    class _R:
        sources = []
        written = {"created": 1, "updated": 0, "merged": 2, "refused": 3, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "2 merged" in err and "3 refused" in err
```

- [ ] **Step 2: Run — expect fail** (current line prints only created/updated/skipped):

Run: `.venv/bin/python -m pytest tests/test_cli.py -k surfaces_merged_and_refused -v`

- [ ] **Step 3: Implement.** In `_print_report`, replace the `written:` print with a sparse builder:

```python
    w = report.written
    parts = [f"{w.get('created', 0)} created", f"{w.get('updated', 0)} updated"]
    if w.get("merged"):
        parts.append(f"{w['merged']} merged")
    if w.get("refused"):
        parts.append(f"{w['refused']} refused")
    parts.append(f"{w.get('skipped', 0)} skipped")
    print("written: " + ", ".join(parts), file=sys.stderr)
```

- [ ] **Step 4: Run — expect pass, and `test_print_report_surfaces_skipped` (Task from #24) stays green:**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "surfaces_merged_and_refused or surfaces_skipped" -v`

- [ ] **Step 5: Mutation witness.** `compileall`, then delete the two `if w.get("merged"/"refused")` blocks → `test_print_report_surfaces_merged_and_refused` reddens. Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py tests/test_cli.py
git commit -m "fix(cli): surface merged and refused in the ingest summary

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: `locations` fixture + conformance probes + `_lead` sourcing

**Files:**
- Modify: `tests/conftest.py` (add `_location_pool`, `LOCATIONS` constant, `locations` fixture)
- Modify: `tests/conformance/test_store_contract.py` (add probes; source `_lead` location)
- Modify: `tests/test_vault.py` (source `_lead` location from the constant; drop `location="London"`)
- Test: the two files above.

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: `tests/conftest.py::LOCATIONS` (a module-level tuple of ≥2 token-disjoint synthetic cities) and a `locations` fixture; conformance never-merge probes.

- [ ] **Step 1: Write the fixture + failing probes.** In `tests/conftest.py`, mirror `_title_pool`/`titles`:

```python
def _location_pool():
    fake = Faker("en_GB")
    Faker.seed(20260719)
    out, seen = [], set()
    while len(out) < 12:
        c = fake.city()
        toks = frozenset(c.lower().split())
        if toks and not (toks & seen):     # keep token-disjoint entries so pairs are provably different
            out.append(c); seen |= toks
    return out


LOCATIONS = tuple(_location_pool()[:3])    # importable: module-level helpers can't take a fixture


@pytest.fixture
def locations():
    return list(LOCATIONS)
```

In `tests/conformance/test_store_contract.py`, source `_lead`'s location from the constant and add the never-merge probes:

```python
from tests.conftest import LOCATIONS   # add near the top imports

# change _lead's default: location=LOCATIONS[0]  (replace the literal "Remote")

def test_two_jobs_differing_in_location_produce_two_notes(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(location=LOCATIONS[0], url="https://example.invalid/1")) == "created"
    assert store.upsert(_lead(location=LOCATIONS[1], url="https://example.invalid/2")) == "created"
    slugs = {n.slug for n in store.read_leads()}
    assert len(slugs) == 2, "two provably-different jobs collapsed into one note"


def test_identical_strings_two_urls_produce_one_note(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1")) == "created"
    assert store.upsert(_lead(url="https://example.invalid/2")) in ("updated", "merged")
    assert len({n.slug for n in store.read_leads()}) == 1


def test_upsert_return_is_within_the_vocabulary(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead()) in ("created", "updated", "merged", "refused")
```

In `tests/test_vault.py`, change the module `_lead` default from `location="London"` to `location=LOCATIONS[0]` (import `from tests.conftest import LOCATIONS`), and update any test that asserted a `London` filename to use `LOCATIONS[0]`.

- [ ] **Step 2: Run — expect fail** where the behaviour isn't yet reachable through the store (they should PASS now that Tasks 1–8 landed; if any fail, the failure names a real gap). Run:

Run: `.venv/bin/python -m pytest tests/conformance/test_store_contract.py tests/test_vault.py -v`

- [ ] **Step 3: Fix any red** — these probes are regression guards over Tasks 1–8; if one is red, the fault is in the earlier task, not the test. Do not weaken a probe to green it.

- [ ] **Step 4: Run — expect pass:** the command above.

- [ ] **Step 5: Mutation witness.** `compileall`, then in `_resolve_path` force the DIFFERENT branch to `merge` (as in Task 5) → `test_two_jobs_differing_in_location_produce_two_notes` reddens (one slug). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/conformance/test_store_contract.py tests/test_vault.py
git commit -m "test(vault): conformance never-merge probes + synthetic locations fixture

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 10: protocol/docs + end-to-end noise + accepted-cost + idempotence

**Files:**
- Modify: `sluice/core/protocols.py` (`Store.upsert` docstring)
- Modify: `docs/ARCHITECTURE.md` (conformance-guarantee list)
- Test: `tests/test_vault.py` (end-to-end noise, accepted cost, idempotence, empty-URL positive control)

**Interfaces:**
- Consumes: everything.
- Produces: the protocol names the four outcomes; the remaining spec probes.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_vault.py`:

```python
def test_noise_word_makes_a_split_merge_end_to_end(tmp_path, monkeypatch):
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    plain = store_mod._make(Config()); plain._name_max_cache = 255
    tuned = store_mod._make(Config(location_noise_words=["remote"])); tuned._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="Remote", url="")
    # plain: Remote vs London -> DIFFERENT -> advances -> creates candidate 2
    assert plain.upsert(_lead(company="X", title="Y", location="London", url="")) == "created"
    # tuned: 'remote' is noise -> the note side empties -> UNKNOWN -> merge at candidate 1
    for f in _leads_dir(tmp_path).glob("X - Y - *.md"):
        f.unlink()
    assert tuned.upsert(_lead(company="X", title="Y", location="London", url="")) == "merged"


def test_accepted_cost_same_location_different_job_reports_updated(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v.upsert(_lead(company="X", title="Y", location="London", url="https://a/1")) == "created"
    # a genuinely different team, same company+title+location, different url -> SAME -> updated (the documented cost)
    assert v.upsert(_lead(company="X", title="Y", location="London", url="https://a/2")) == "updated"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 1


def test_upsert_is_idempotent_across_three_runs_on_the_slug_set(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    lead = _lead(company="X", title="Y", location="London", url="https://a/1")
    first = None
    for _ in range(3):
        v.upsert(lead)
        names = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
        first = first or names
        assert names == first        # no new note minted on repeat runs
    assert first == {"X - Y.md"}
```

- [ ] **Step 2: Run — expect fail then pass.** The three tests are regression guards over Tasks 1–8 and should pass on arrival:

Run: `.venv/bin/python -m pytest tests/test_vault.py -k "noise_word_makes or accepted_cost or idempotent_across_three" -v`

- [ ] **Step 3: Update the protocol + architecture docs.** In `sluice/core/protocols.py`, extend `Store.upsert`'s docstring to define the outcome vocabulary:

```python
    def upsert(self, lead) -> str:
        """Create a new note, or reconcile an incoming lead against the existing ones.
        Returns one of: "created" (a new note), "updated" (an existing note we identified
        as the same opportunity; only last_seen changes), "merged" (an existing note we
        could not prove same-or-different; only last_seen changes), "refused" (no name
        distinguishes this lead from a note proven different, so nothing is written).
        "created"/"updated" are MUST-support; "merged"/"refused" are MAY-return -- a store
        keyed on synthetic ids never merges-on-uncertainty or hits a naming collision.
        A store MUST touch only last_seen on update/merge (never-clobber)."""
        ...
```

In `docs/ARCHITECTURE.md`, add the identity rule to the conformance-guarantee list (find the list naming never-clobber / never-regress and add a bullet):

```markdown
- **never-merge**: two provably-different jobs (a proven location difference) produce two
  notes; a lead is never silently absorbed into a note for a different opportunity (#5).
```

- [ ] **Step 4: Run — expect pass:** `.venv/bin/python -m pytest tests/test_vault.py tests/conformance/test_store_contract.py -v`

- [ ] **Step 5: Mutation witness.** `compileall`, then in `_resolve_path` make the DIFFERENT branch merge → `test_noise_word_makes_a_split_merge_end_to_end` reddens (plain gets `merged` not `created`). Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/protocols.py docs/ARCHITECTURE.md tests/test_vault.py
git commit -m "docs(vault): Store.upsert vocabulary + end-to-end noise/idempotence tests

Refs #5.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Final: full suite + lint

- [ ] **Full suite:** `.venv/bin/python -m pytest` — all pass (existing + new), offline.
- [ ] **Lint:** `ruff check sluice tests` — clean. (`pip install ruff==0.15.21` if absent.)
- [ ] **Pre-push review** per the standing rule: run `/review-pr` BEFORE pushing (CodeRabbit is the scarce resource; the specialist team is free and parallel). Address findings, then push and open the PR with **`Fixes #5`** in the body (the write-path collision is the whole of #5; #23 owns the read key). Task commits use `Refs #5`; only the PR's `Fixes #5` closes the issue on merge.

## Self-Review

**Spec coverage:** same_opportunity → T1; noise config → T2 (+wiring T4); shared name helper → T3; _resolve_path walk → T5; four-outcome upsert → T6; sink allowlist/sparse counts → T7; cli merged/refused → T8; conformance store-general probes + disjoint locations fixture + _lead sourcing → T9; protocol vocabulary + ARCHITECTURE + end-to-end noise/accepted-cost/idempotence → T10. REFUSE via frontmatter-contradicts-filename → T5/T6. Zero-migration (candidate 1 byte-identical) → T3 Step 4. ✓

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. The `.rulesync/rules/CLAUDE.md` note is deliberately deferred to the user (human-gated), stated in the spec DoD, not a task.

**Type consistency:** `same_opportunity(note_fm, lead, noise) -> str` (T1) consumed identically in `_resolve_path` (T5). `_note_name(stem, suffix="") -> str` (T3) consumed in `_path_for` (T3) and `_resolve_path` (T5). `_resolve_path(lead) -> (path|None, action)` (T5) consumed in `upsert` (T6). `upsert -> str` four outcomes (T6) consumed by sink allowlist (T7) and cli (T8). `Config.location_noise_words: list` (T2) → `Vault(location_noise_words=)` → `self._noise: frozenset` (T4) → `same_opportunity` noise (T5). `LOCATIONS` tuple + `locations` fixture (T9). ✓
