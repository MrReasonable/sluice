# `sluice init` Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `sluice init`, a setup wizard that writes a neutral config and scaffolds a Judging Profile, so a fresh install has a path from "installed" to "configured" that cannot express a preference the user did not state.

**Architecture:** A new `sluice/onboard/` package split pure-from-impure: a declarative question catalogue and a pure `build_plan(answers) -> InitPlan` producing two artefact texts, plus an impure asker (TTY prompt, `$EDITOR`, board walk, non-TTY refusal) injected as a parameter. `cli.cmd_init` preflights destinations, asks, plans, then writes — config via exclusive create, profile through the **store seam**.

**Tech Stack:** Python 3.12+, standard library only in `sluice/`, pytest, ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-07-30-sluice-init-design.md` (revised after review round 1)

**Supersedes:** `docs/superpowers/plans/2026-07-30-sluice-init.md` — regenerated, not patched, after 50 review findings.

## Global Constraints

- **`sluice/` is standard-library only.** No new runtime dependency. `yaml` only under a guarded `try/except ImportError`; the emitter must not need it.
- **Empty config abstains.** Every preference question skips on blank. The vault question is the sole exception and **its default is parsed like any typed answer**.
- **Never-clobber.** No artefact overwritten, ever. No `--force`. Writes use `O_CREAT|O_EXCL`.
- **Neutrality.** No employer, role preference, location, contact, hostname or absolute path in `sluice/` or `tests/`. Test values use `Example …`, `example.invalid`, and `tmp_path` — **never `/tmp/...` literals**.
- **The `"./"` DoD grep stays at 9.** `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l`
- **No running test totals** anywhere in this plan. State per-file counts only, or "all green" — a stale global count costs the implementer a false alarm.
- **Every pasted code block must be ruff-clean.** Lint select is `["E4","E7","E9","F"]` and `tests/*` is not exempt from `F`: no unused imports.
- Conventional commits, trailer `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Before any witness: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`. **Mutate by MOVING or DELETING.** Run the named test **by node id** and confirm no pre-existing test is the killer.
- Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/ruff check sluice tests scripts`.

## Task order (changed from v1, and the reason)

`sluice.yaml.example` work comes **first**. Task 5's documentation sweep asserts every emitted key is documented there, and `triage.target_locations` is measurably absent today — so in v1 that sweep was red on arrival because the example fix sat four tasks later.

## File Structure

**Create:** `sluice/onboard/{__init__,emit,questions,plan,ask}.py`; `tests/test_onboard_{emit,questions,plan,profile,ask,sources}.py`; `tests/onboard_prose.py` (the shipped-prose roster + its completeness guard); `tests/test_config_retired_locations.py`; `tests/test_no_copy_instruction.py`; `tests/harness/initdriver.py`; `tests/functional/test_init.py`; `tests/e2e/test_init_to_verdicts.py`

**Modify:** `sluice/core/{protocols,vault,config}.py`, `sluice/triage/prompt.py`, `sluice/cli.py`, `sluice.yaml.example`, `README.md`, `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`, `tests/conformance/test_store_contract.py`, `tests/test_config.py`, `tests/test_sluice_neutral_defaults.py`

---

### Task 1: Retire `Config.locations`, relocate its documentation

**Files:** Modify `sluice/core/config.py`, `sluice.yaml.example`, `tests/test_config.py`, `tests/test_sluice_neutral_defaults.py`

**Interfaces:** Produces `sluice.core.config.refuse_retired_locations(data: dict) -> None`

- [ ] **Step 1: Write the failing tests** (new file `tests/test_config_retired_locations.py`, so no line-numbered edits to an existing file)

```python
"""`locations` was declared, documented, and read by NOTHING -- its own comment called it a
loaded gun. `sluice init` would have been the consumer that finally populated it, so it is
retired the way #80 retired triage.dossier_dir: loudly, in BOTH spellings."""
import pytest

from sluice.core.config import load_config


def test_a_config_that_sets_locations_refuses_and_names_the_replacement(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("locations: [Alfa]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


def test_the_env_spelling_refuses_too(tmp_path, monkeypatch):
    """Raise in a file and silence in a shell is the asymmetry the fail-loudly rule exists to
    prevent -- three reviewers found this independently."""
    monkeypatch.setenv("SLUICE_LOCATIONS", "Alfa")
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


@pytest.mark.parametrize("source", ["file", "env"])
def test_neither_message_echoes_the_value(tmp_path, monkeypatch, source):
    """Geography is personal, and an exception travels further than the file it came from --
    logs, bug reports, pasted tracebacks. Same ruling as refuse_retired_dossier_dir."""
    path = tmp_path / "c.yaml"
    if source == "file":
        path.write_text("locations: [Alfa]\n", encoding="utf-8")
    else:
        path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
        monkeypatch.setenv("SLUICE_LOCATIONS", "Alfa")
    with pytest.raises(ValueError) as exc:
        load_config(str(path))
    assert "Alfa" not in str(exc.value)


def test_a_config_without_it_loads(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    assert load_config(str(path)) is not None


def test_the_example_documents_target_locations_and_not_the_retired_key():
    """The wizard routes geography to triage.target_locations, the retirement message names it,
    and Task 5's sweep asserts every emitted key is documented -- so the catalogue has to carry
    it. Measured before this task: 0 matches."""
    example = open("sluice.yaml.example", encoding="utf-8").read()
    assert "target_locations:" in example
    for line in example.splitlines():
        assert not line.lstrip("# ").strip().startswith("locations:"), \
            "sluice.yaml.example still documents the retired root `locations` key"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_retired_locations.py -q`
Expected: FAIL — `DID NOT RAISE ValueError`

- [ ] **Step 3: Add the refusal** beside `refuse_retired_dossier_dir` in `sluice/core/config.py`

```python
def refuse_retired_locations(data: dict) -> None:
    """Raise if a config still sets the retired root `locations` key (#8).

    Declared, documented in `sluice.yaml.example`, and read by NOTHING -- this module's own
    comment called it "a loaded gun rather than a live bug, since the first consumer to wire it
    into a search or a gate would have inherited a stranger's 'remote only'". `sluice init` would
    have been that consumer.

    BOTH spellings, because the loader also honoured `$SLUICE_LOCATIONS`. Raising on the file and
    staying silent on the environment is the asymmetry the fail-loudly rule exists to remove: a
    user who configured geography in their shell would watch it quietly stop being read.

    The VALUE is never echoed -- personal, and an exception travels further than the file it came
    from. Same ruling as `refuse_retired_dossier_dir` and `dossier_allow_hosts`.
    """
    if "locations" in data or os.environ.get("SLUICE_LOCATIONS"):
        raise ValueError(
            "config key `locations` (and $SLUICE_LOCATIONS) was never read by anything and has "
            "been retired. Geography is a triage concern -- move your value to "
            "`triage.target_locations`.")
```

- [ ] **Step 4: Call it and delete the field**

In `load_config`, immediately after the config data is loaded, before any field is read: `refuse_retired_locations(data)`.

Delete: `_DEFAULT_LOCATIONS` (config.py:18-24), the `locations` dataclass field (:78), the `locations = ...` derivation including its `SLUICE_LOCATIONS` branch (:160-163), and `locations=locations` from the `Config(...)` construction (:208).

- [ ] **Step 5: Update the existing guard file BY CONTENT, not line number**

In `tests/test_sluice_neutral_defaults.py`, delete the two `locations == []` assertions (they assert a field that no longer exists — legitimate, ruled independently by two reviewers, because the discovery-based sweep loses no scope). Then:
- rewrite the 6-line comment block above the first one so it records the **retirement** and names `triage.target_locations` as where geography is now guarded, rather than explaining a guard that is gone;
- delete the now-dead `monkeypatch.delenv("SLUICE_LOCATIONS")` and fix the comment above it that counts two overrides.

- [ ] **Step 6: Relocate the documentation in `sluice.yaml.example`**

Delete the root `locations` comment block. Insert into the `triage:` block, beside `accept_titles`:

```yaml
  # Where you are willing to work. Empty = no geographic gate at all, which is the shipped
  # default. Commented rather than set because this file is COPIED: an active value would hand
  # every copier a judgement about geography they never made.
  # target_locations: [Remote]   # <- uncomment and set YOUR OWN
```

- [ ] **Step 7: Run and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 8: Witness — delete the refusal CALL** (not the function: a coarser mutant reddens for the wrong reason)

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/config.py /tmp/config.py.bak
# DELETE the `refuse_retired_locations(data)` call in load_config
.venv/bin/python -m pytest "tests/test_config_retired_locations.py::test_a_config_that_sets_locations_refuses_and_names_the_replacement" -v
# Expected: FAIL
# Then the env arm SEPARATELY -- an aggregate cannot distinguish one-armed from two-armed:
# restore, then DELETE only `or os.environ.get("SLUICE_LOCATIONS")` from the condition
.venv/bin/python -m pytest "tests/test_config_retired_locations.py::test_the_env_spelling_refuses_too" -v
# Expected: FAIL
cp /tmp/config.py.bak sluice/core/config.py
git diff --stat   # MUST be empty
```

- [ ] **Step 9: Commit**

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_config_retired_locations.py tests/test_sluice_neutral_defaults.py
git commit -m "fix(core): retire the dead \`locations\` config key (#8)

Declared, documented, read by nothing -- the module's own comment called
it a loaded gun. Retired the way #80 retired triage.dossier_dir, in BOTH
spellings: raising on the file and staying silent on \$SLUICE_LOCATIONS is
the asymmetry fail-loudly exists to remove.

Its documentation is RELOCATED onto a commented triage.target_locations,
which was measurably absent from the example -- so the wizard's
documentation sweep has something to find and the retirement message
names a key the catalogue explains.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: Shared constants, never-clobber `write_document`, store conformance

**Files:** Modify `sluice/core/protocols.py`, `sluice/core/vault.py:32-33,317-336`, `sluice/triage/prompt.py:20`, `tests/conformance/test_store_contract.py`. Create `tests/test_vault_write_document.py`

**Interfaces:** Produces `protocols.CRITERIA_RELPATH: str`; `vault.DEFAULT_VAULT: str`; `Store.write_document(rel, text, *, only_if_absent=False) -> str` returning the path, or `""` when it existed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vault_write_document.py
"""`only_if_absent=True` is the never-clobber primitive `sluice init` scaffolds through. A
parameter on the existing writer, not a second write function: CodeQL reads a new write function
as a new sink (#9's `require_status` precedent)."""
import pytest

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import DEFAULT_VAULT, Vault


def test_creates_when_absent(tmp_path):
    v = Vault(str(tmp_path))
    assert v.write_document(CRITERIA_RELPATH, "first", only_if_absent=True)
    assert v.read_criteria() == "first"


def test_abstains_and_leaves_the_file_byte_identical(tmp_path):
    v = Vault(str(tmp_path))
    v.write_document(CRITERIA_RELPATH, "human wrote this", only_if_absent=True)
    assert v.write_document(CRITERIA_RELPATH, "SCAFFOLD", only_if_absent=True) == ""
    assert v.read_criteria() == "human wrote this"


def test_the_default_still_overwrites_so_the_digest_caller_is_unchanged(tmp_path):
    v = Vault(str(tmp_path))
    v.write_document("Job Applications/Digest.md", "old")
    path = v.write_document("Job Applications/Digest.md", "new")
    assert open(path, encoding="utf-8").read() == "new"


def test_escape_guard_still_fires_under_only_if_absent(tmp_path):
    with pytest.raises(ValueError, match="escapes the store root"):
        Vault(str(tmp_path)).write_document("../outside.md", "x", only_if_absent=True)


def test_the_criteria_path_has_one_home():
    import sluice.core.vault as vault_mod
    import sluice.triage.prompt as prompt_mod
    assert vault_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert prompt_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert DEFAULT_VAULT == "./vault"
```

Append to `tests/conformance/test_store_contract.py`. **Match that file's actual convention**, which is a module-level `pytestmark = pytest.mark.parametrize("store_name", _STORES)` (line 41) plus a `_make_store(store_name, tmp_path, monkeypatch)` helper (line 44) — *not* a `store` fixture:

```python
def test_write_document_only_if_absent_creates_then_abstains(store_name, tmp_path, monkeypatch):
    """On the CONTRACT, not on Vault. protocols.py's own docstring says never-clobber lives here
    precisely because 'a second store would ship without them', and #1 (CORRECTION, 2026-08-02: #1 is
the vault's folder LAYOUT, not the store seam -- it ships no second store, so this row still runs
once) is the
    next backlog item -- so the second store is not hypothetical. `require_status`, the precedent
    this parameter follows, got three conformance rows.

    Asserted through read_criteria(), never a path: a store need not have one."""
    from sluice.core.protocols import CRITERIA_RELPATH
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.write_document(CRITERIA_RELPATH, "first", only_if_absent=True)
    assert store.write_document(CRITERIA_RELPATH, "second", only_if_absent=True) == ""
    assert store.read_criteria() == "first"
```

`_STORES` has one entry today (`vault` is the only registered store — verified), so this row runs once now and multiplies for free when #1 lands. That is the point of putting it here rather than in the Vault test file.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vault_write_document.py tests/conformance/test_store_contract.py -q`
Expected: FAIL — `ImportError: cannot import name 'CRITERIA_RELPATH'`

- [ ] **Step 3: Add the constant to `core/protocols.py`** (add `import os` if absent)

```python
# Where the judge's criteria live inside a store. Here, in the contract module, because it IS
# part of the Store contract -- the document `read_criteria` serves. It was previously two
# independent literals (`core/vault.py`, `triage/prompt.py`); `sluice init` would have made three,
# and a divergence means init writes a profile the judge never reads, silently, because a missing
# profile falls back to the shipped default rather than raising.
#
# A non-filesystem store treats this as an opaque DOCUMENT KEY, not a path -- the separator is
# incidental, and nothing here may assume a filesystem.
CRITERIA_RELPATH = os.path.join("Job Applications", "Judging Profile.md")
```

Update the `Store` protocol signature to `def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:` with a docstring stating that `only_if_absent=True` returns `""` and writes nothing when the document exists.

- [ ] **Step 4: Point both modules at it**

`sluice/core/vault.py` — replace the line-32 literal:

```python
from sluice.core.protocols import CRITERIA_RELPATH

_CRITERIA_RELPATH = CRITERIA_RELPATH
# Public: `sluice init` offers this as the vault question's default. Imported by `cli.py` and
# PASSED to the catalogue rather than imported by it -- the pure question data must not depend on
# a concrete store. A second `"./vault"` literal would also take the DoD grep from 9 to 10.
DEFAULT_VAULT = "./vault"
_DEFAULT_VAULT = DEFAULT_VAULT
```

`sluice/triage/prompt.py` — replace the line-20 literal with the same import plus `_CRITERIA_RELPATH = CRITERIA_RELPATH`.

- [ ] **Step 5: Implement `only_if_absent`**

In `Vault.write_document`, replace **lines 334-336** (the `os.makedirs`, `_atomic_write`, `return path` trio — not just the write line) with:

```python
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if only_if_absent:
            # O_CREAT|O_EXCL, not exists()-then-write: the check and the write are two syscalls,
            # and the racer on the other side is a human editing the note in Obsidian, who takes
            # no lock (#16). An exclusive create makes never-clobber a property of the open.
            try:
                _write(path, text, exclusive=True)
            except FileExistsError:
                return ""
            return path
        _atomic_write(path, text)
        return path
```

and change the signature to `def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:`.

- [ ] **Step 6: Run and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 7: DoD grep unchanged**

Run: `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l` → `9`

- [ ] **Step 8: Witness M2 — against the CONFORMANCE row, not only the Vault test**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak
# DELETE the `if only_if_absent:` block, leaving the `_atomic_write` fall-through
.venv/bin/python -m pytest "tests/conformance/test_store_contract.py::test_write_document_only_if_absent_creates_then_abstains" -v
# Expected: FAIL  (this is what makes the CONTRACT sentence enforceable rather than aspirational)
cp /tmp/vault.py.bak sluice/core/vault.py
git diff --stat   # MUST be empty
```

- [ ] **Step 9: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py sluice/triage/prompt.py tests/test_vault_write_document.py tests/conformance/test_store_contract.py
git commit -m "feat(core): never-clobber write_document + one home for the criteria path (#8)

CRITERIA_RELPATH was two independent literals; init would have made three,
and a divergence means init writes a profile the judge never reads --
silent, since a missing profile falls back to the shipped default.

only_if_absent is a parameter on the existing writer, not a second write
function (#9's require_status precedent -- CodeQL reads a new write
function as a new sink), and uses O_CREAT|O_EXCL because the racer is a
human in Obsidian (#16). Conformance rows make it a property of the STORE
CONTRACT rather than of Vault, which is what protocols.py's own docstring
asks for and what #1 will need.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: The YAML scalar emitter

**Files:** Create `sluice/onboard/__init__.py`, `sluice/onboard/emit.py`, `tests/test_onboard_emit.py`

**Interfaces:** Produces `emit.scalar(value) -> str`, `emit.flow_list(values) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_emit.py
"""The config init writes is a template WITH COMMENTS, so safe_dump is out (it destroys them) and
ruamel is out (standard-library only). Values are injected by a conservative emitter, and this is
the round trip that proves it -- without it a company name with an apostrophe writes a config that
fails to parse."""
import pytest
import yaml

from sluice.onboard.emit import flow_list, scalar

NASTY = ["O'Example", "Foo: Bar", "#hash", "yes", "no", "on", "null", "~", "!tag",
         "back\\slash", 'quote"inside', "line\nbreak", "  padded  ", "café-münster",
         "*anchor", "&ref", "%directive", "@at", "`tick", "[bracket]", "{brace}", "- dash", ""]


@pytest.mark.parametrize("value", NASTY)
def test_string_scalars_round_trip(value):
    assert yaml.safe_load(f"k: {scalar(value)}")["k"] == value


@pytest.mark.parametrize("value", [0, 1, 90, 450, 90000])
def test_int_scalars_round_trip_as_ints(value):
    loaded = yaml.safe_load(f"k: {scalar(value)}")["k"]
    assert loaded == value and isinstance(loaded, int)


def test_bools_emit_as_yaml_bools():
    assert yaml.safe_load(f"k: {scalar(True)}")["k"] is True
    assert yaml.safe_load(f"k: {scalar(False)}")["k"] is False


def test_flow_list_round_trips_the_whole_corpus():
    assert yaml.safe_load(f"k: {flow_list(NASTY)}")["k"] == NASTY


def test_empty_flow_list():
    assert yaml.safe_load(f"k: {flow_list([])}")["k"] == []


def test_a_string_that_looks_like_an_int_stays_a_string():
    loaded = yaml.safe_load(f"k: {scalar('2024')}")["k"]
    assert loaded == "2024" and isinstance(loaded, str)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard'`

- [ ] **Step 3: Create the package**

`sluice/onboard/__init__.py` — docstring only; nothing imports from the package root:

```python
"""`sluice init` -- the setup wizard (#8).

A COMMAND package, not a sixth pipeline sub-app: nothing downstream imports it, and it sits beside
the five sub-apps rather than inside ingest -> triage -> cv -> apply -> track.

Split pure-from-impure on purpose. `questions` and `plan` are pure functions over a dict, so the
property that matters -- a run that answers nothing produces a config that expresses nothing -- is
a unit test rather than a wizard transcript. `ask` holds every prompt, every terminal read and the
one subprocess call.
"""
```

`sluice/onboard/emit.py`:

```python
"""Emit YAML scalars by hand.

The config `sluice init` writes is a TEMPLATE WITH COMMENTS -- the guidance under each key is most
of its value -- so `yaml.safe_dump` cannot produce it and a round-tripping loader like ruamel is
barred by the standard-library-only rule.

Strings are ALWAYS double-quoted, never bare and never single-quoted. A bare scalar changes meaning
with its content (`yes`/`on` load as booleans, `2024` as an int, a leading `#` starts a comment, a
`:` splits a mapping), and single-quoted YAML has one escape (`''`) that covers neither backslashes
nor control characters. The double-quoted form has a total escape grammar, so this is safe rather
than lucky -- which the tests prove by loading every emission back with a real parser instead of
inspecting the string.
"""

# Double-quoted YAML understands JSON's escapes. `\` FIRST: escaping it after `"` would re-escape
# the backslashes this table itself introduces.
_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t"))


def scalar(value) -> str:
    """One YAML scalar for `value`.

    `bool` is checked BEFORE `int` because it subclasses it -- the same ordering trap as
    `lead_ttl_days`' validator (#75). Without it `True` emits as `1`.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return f'"{text}"'


def flow_list(values) -> str:
    """A flow sequence. Flow rather than block style so a value fits on one template LINE, which
    keeps the surrounding comment attached to the key it explains."""
    return "[" + ", ".join(scalar(v) for v in values) + "]"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py -q`
Expected: all pass

- [ ] **Step 5: Witness M5**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/emit.py /tmp/emit.py.bak
# In `scalar`, DELETE the escape loop and the quotes: `return str(value)`
.venv/bin/python -m pytest tests/test_onboard_emit.py -q
# Expected: FAIL on the `Foo: Bar`, `#hash`, `yes` and `2024` rows
cp /tmp/emit.py.bak sluice/onboard/emit.py
git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/ tests/test_onboard_emit.py
git commit -m "feat(onboard): hand-rolled YAML scalar emitter (#8)

safe_dump destroys the comments that are most of the template's value, and
ruamel is barred by the standard-library-only rule. Strings are always
double-quoted: a bare scalar changes meaning with its content, and
single-quoted YAML cannot escape a backslash. Proved by loading every
emission back with a real parser over a nasty corpus.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: The question catalogue

**Files:** Create `sluice/onboard/questions.py`, `tests/test_onboard_questions.py`

**Interfaces:** Produces `Question` (frozen dataclass: `key`, `prompt`, `parse`, `writes_to: tuple`, `section`, `hint`, `default`, `consequence`); `catalogue(*, default_vault: str) -> tuple[Question, ...]`; `BadAnswer(ValueError)`; parsers `parse_text`, `parse_csv`, `parse_int`, `parse_path`, `parse_choice(*allowed)`, `parse_url`; `NO_TAXONOMY_WORDS: tuple[str, ...]`; `expresses_a_preference(text: str) -> list[str]`

**Note on `catalogue(*, default_vault=…)`:** the default is a **parameter**, not an import. A pure question catalogue must not depend on `core/vault.py`, the concrete store — that would pin the wizard's one non-skipping default to the vault store even when `store:` names something else. `cli.py` supplies it, where the other store imports already live.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_questions.py
"""The catalogue is pure data. Its load-bearing property is that a BLANK answer is a SKIP for every
preference question -- a wizard that fills a gate because someone fumbled a prompt is 672ad2a with
a friendly face."""
import pytest

from sluice.onboard.questions import (BadAnswer, catalogue, expresses_a_preference, parse_choice,
                                      parse_csv, parse_int, parse_path, parse_url)

VAULT = "./vault"


def test_every_question_except_the_vault_skips_on_blank():
    for q in catalogue(default_vault=VAULT):
        if q.key == "vault_dir":
            continue
        assert q.default is None, f"{q.key} would fill a gate the user did not state"


def test_the_vault_question_takes_the_default_it_was_GIVEN():
    """A parameter, not an import: a pure catalogue must not depend on the concrete store."""
    qs = [q for q in catalogue(default_vault="/example/elsewhere") if q.key == "vault_dir"]
    assert len(qs) == 1 and qs[0].default == "/example/elsewhere"


def test_parse_int_rejects_bool_words_before_parsing_a_number():
    """PyYAML resolves yes/on/true to True and bool subclasses int, so `lead_ttl_days: yes` would
    load as a ONE DAY ttl and mark every lead stale with nothing raising (#75)."""
    for word in ("yes", "no", "on", "off", "true", "false", "YES", "True"):
        with pytest.raises(BadAnswer):
            parse_int(word)
    assert parse_int("90") == 90 and parse_int(" 450 ") == 450
    for bad in ("ninety", "-1", ""):
        with pytest.raises(BadAnswer):
            parse_int(bad)


def test_parse_csv_splits_strips_and_drops_empties():
    assert parse_csv("a, b ,,c") == ["a", "b", "c"]
    assert parse_csv("  ") == []


def test_parse_url_requires_http_and_never_resolves_dns():
    assert parse_url("https://example.invalid/jobs") == "https://example.invalid/jobs"
    for bad in ("example.invalid/jobs", "ftp://example.invalid", "file:///etc/passwd", ""):
        with pytest.raises(BadAnswer):
            parse_url(bad)


def test_parse_path_expands_and_absolutises(tmp_path, monkeypatch):
    """A RELATIVE vault_dir is the 'second empty vault beside you' hazard README warns about,
    reintroduced by the wizard itself."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert parse_path("~/notes") == str(tmp_path / "notes")
    assert parse_path("./vault") == str(tmp_path / "vault")


def test_parse_choice_lists_the_valid_names():
    p = parse_choice("script", "weasyprint")
    assert p("weasyprint") == "weasyprint"
    with pytest.raises(BadAnswer, match="script"):
        p("wkhtmltopdf")


def test_the_backend_choices_match_the_registry():
    """Hand-listing a name-keyed registry is a second copy of it: register a fifth backend and the
    wizard silently cannot offer it. Same discovery shape as the fan-out sweep (#63)."""
    from sluice.core.app import Sluice
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["primary_backend"]
    assert set(q.parse.allowed) == set(Sluice.available("backend"))


def test_the_renderer_choices_match_the_registry():
    from sluice.core.app import Sluice
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["renderer"]
    assert set(q.parse.allowed) == set(Sluice.available("renderer"))


# ── the neutrality SMOKE TEST (named honestly; see the helper's docstring) ────
def test_the_preference_helper_rejects_a_synthetic_offender():
    """POSITIVE CONTROL. Without it the sweep below could pass because the helper never fires."""
    assert expresses_a_preference("Most people put a platform role here.")


def test_the_helper_matches_whole_words_only():
    """`senior` must not fire on `seniority` -- a bare substring match failed on the scaffold's own
    prose, and the tempting fix (deleting the word) shrinks the guard instead of the bug."""
    assert not expresses_a_preference("your background and seniority")


def test_no_shipped_prose_names_an_exemplar():
    """Sweeps EVERY surface this package puts in front of a user or into their files -- not just
    the catalogue. Round 1 flagged that `_HEADER` and `_SECTION_BLURB` land in every user's config
    and were covered by nothing; the first fix corrected the MATCHING and left the SCOPE alone,
    which is the same enumeration failure one round later."""
    from tests.onboard_prose import shipped_prose
    surfaces = shipped_prose()
    assert len(surfaces) >= 20                 # SCOPE: a sweep over nothing passes
    for label, text in surfaces:
        assert not expresses_a_preference(text), f"{label} names an exemplar"


def test_catalogue_keys_are_unique():
    keys = [q.key for q in catalogue(default_vault=VAULT)]
    assert len(keys) == len(set(keys))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_questions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.questions'`

- [ ] **Step 3: Write the catalogue**

```python
# sluice/onboard/questions.py
"""The question catalogue: pure data plus the parsers that validate an answer.

A BLANK answer is a SKIP, and a skipped question writes nothing -- the empty-config-abstains
invariant expressed at the wizard. A wizard that fills a gate because someone fumbled a prompt bins
a stranger's job hunt exactly as `672ad2a` did. The vault is the sole exception, because the
profile has to be written somewhere and "skip" is not an answer to that.

Nothing here proposes a taxonomy of good jobs. Every prior neutrality risk in this repo was a
shipped VALUE; a wizard adds a new surface, the shipped QUESTION, and "startup or enterprise?"
ships an opinion just as surely as a default would.
"""
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass


class BadAnswer(ValueError):
    """An answer that cannot be used. On a TTY the asker re-asks; unreachable under `--no-input`,
    which supplies no answers at all."""


# PyYAML resolves all of these to a bool, and `bool` subclasses `int`, so an int-typed key answered
# `yes` would load as 1 with nothing raising. `lead_ttl_days: yes` is the natural way to think you
# are turning staleness ON, and it would instead declare every lead stale after one day (#75).
_BOOL_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false"}

# A SMOKE TEST's vocabulary, and named as one. It cannot be complete -- an exemplar this list does
# not contain sails past -- so it must never be treated as the neutrality guarantee, and no real
# check may be deleted on its strength. What it does catch is the cheap regression: someone
# reaching for a concrete example while writing a prompt. Held HERE, in one place, so the unit and
# e2e tiers sweep the same vocabulary rather than two copies that drift.
NO_TAXONOMY_WORDS = ("startup", "scaleup", "enterprise", "fintech", "platform", "infrastructure",
                     "senior", "junior", "staff", "principal", "manager", "engineer", "developer",
                     "remote-first", "hybrid")


def expresses_a_preference(text: str) -> list:
    """Which taxonomy words `text` names, matched on WORD BOUNDARIES.

    Boundaries, not substrings: `senior` inside `seniority` is not an exemplar, and a bare `in`
    check fails on the scaffold's own prose. Deleting the word to make that pass would shrink the
    guard rather than the problem.
    """
    low = (text or "").lower()
    return [w for w in NO_TAXONOMY_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]


def parse_text(raw: str) -> str:
    return raw.strip()


def parse_csv(raw: str) -> list:
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_int(raw: str) -> int:
    text = raw.strip()
    if text.lower() in _BOOL_WORDS:
        raise BadAnswer(f"{text!r} is a yes/no word and this key takes a number. YAML loads it as "
                        f"true, which counts as 1 -- give a number, or leave it blank to skip.")
    try:
        value = int(text)
    except ValueError:
        raise BadAnswer(f"{text!r} is not a whole number.") from None
    if value < 0:
        raise BadAnswer("must not be negative (0 means the gate is off).")
    return value


def parse_path(raw: str) -> str:
    """Absolute, always. A relative `vault_dir` follows the working directory, which is how a user
    ends up with a second empty vault beside the one they meant -- and the wizard would be the
    thing that wrote it."""
    return os.path.abspath(os.path.expanduser(raw.strip()))


def parse_choice(*allowed: str) -> Callable:
    """A closure that also EXPOSES its allowed set as `.allowed`, so a test can compare it against
    the live registry. Hand-listing a name-keyed registry is a second copy of it; the exposure is
    what lets a completeness test redden when a provider is added."""
    def _parse(raw: str) -> str:
        text = raw.strip()
        if text not in allowed:
            raise BadAnswer(f"{text!r} is not one of: {', '.join(sorted(allowed))}")
        return text
    _parse.allowed = tuple(allowed)
    return _parse


def parse_url(raw: str) -> str:
    """Scheme and shape only. Deliberately NOT `core/urlguard.py`, which resolves DNS: that guard
    stops the dossier fetcher reaching a private address, and using it here would make `init`
    non-hermetic and could hang behind a slow resolver while someone is setting up."""
    text = raw.strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadAnswer(f"{text!r} is not an http(s) URL.")
    return text


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    parse: Callable
    writes_to: tuple = ()
    section: str = ""
    hint: str = ""
    # None means "blank skips me". Only the vault question sets it.
    default: object = None
    # Printed by the post-write report when this key ends up set, so the user learns what their
    # config will DO rather than only what was written.
    consequence: str = ""


def catalogue(*, default_vault: str) -> tuple:
    """Every question, in ask order.

    `default_vault` is a PARAMETER: a pure catalogue must not import from `core/vault.py`, the
    concrete store, or the wizard's one non-skipping default would be pinned to the vault
    implementation even when `store:` names something else. `cli.py` supplies it.

    Ask order matters in one place: the coarse ingest gate is LAST. It is the most dangerous key in
    the file -- a keep-list discards every title that does not match, before dedup and before any
    LLM call -- so it is asked once the user has seen what the downstream gates do, and its prompt
    states the consequence outright.
    """
    from sluice.core.app import Sluice
    backends = tuple(sorted(Sluice.available("backend")))
    renderers = tuple(sorted(Sluice.available("renderer")))
    return (
        Question("vault_dir", "Where is your Obsidian vault?", parse_path, ("vault_dir",),
                 "Vault", default=default_vault,
                 hint="Where sluice reads your judging criteria and writes lead notes.",
                 consequence="vault: {value}"),

        Question("cv_name", "What name should appear on a tailored CV?", parse_text,
                 ("cv.name",), "You"),
        Question("cv_contact", "Contact block for the CV (email, phone, links)?", parse_text,
                 ("cv.contact",), "You", hint="One line; edit the config for a multi-line block."),
        Question("cv_employers", "Places you have worked, comma-separated?", parse_csv,
                 ("cv.employers",), "You",
                 hint="Used to check a composed CV only cites places you worked."),

        Question("accept_titles", "Which job titles do you want, comma-separated?", parse_csv,
                 ("triage.accept_titles",), "Want",
                 hint="Blank leaves the gate open: every lead reaches the judge.",
                 consequence="accept only titles matching: {value}"),
        Question("reject_titles", "Which titles disqualify a role, comma-separated?", parse_csv,
                 ("triage.reject_titles",), "Want",
                 consequence="reject titles matching: {value}"),
        Question("target_locations", "Where are you willing to work, comma-separated?", parse_csv,
                 ("triage.target_locations",), "Want",
                 hint="Blank means no geographic gate at all.",
                 consequence="require a location matching: {value}"),
        Question("reject_companies", "Any companies to skip, comma-separated?", parse_csv,
                 ("triage.reject_companies",), "Want", consequence="always skip: {value}"),
        Question("contract_floor", "Minimum day rate for contract work (GBP)?", parse_int,
                 ("triage.contract_floor_gbp_day",), "Want", hint="0 or blank means no floor.",
                 consequence="drop contract roles under GBP {value}/day"),
        Question("perm_floor", "Minimum salary for permanent work (GBP)?", parse_int,
                 ("triage.perm_floor_gbp",), "Want", hint="0 or blank means no floor.",
                 consequence="drop permanent roles under GBP {value}"),
        Question("lead_ttl_days", "Drop leads not seen in a scrape for how many days?", parse_int,
                 ("lead_ttl_days",), "Want",
                 hint="0 or blank turns staleness off, which is the shipped default.",
                 consequence="treat leads unseen for {value} days as stale"),

        Question("relevance_keep", "Keep only titles containing these words, comma-separated?",
                 parse_csv, ("relevance_keep",), "Cost",
                 hint="A cheap filter at scrape time, BEFORE anything else runs. Anything not "
                      "matching is discarded and never judged. Leave blank unless you want that.",
                 consequence="keep ONLY titles containing: {value} "
                             "(everything else dropped before triage)"),
        Question("relevance_drop", "Discard titles containing these words?", parse_csv,
                 ("relevance_drop",), "Cost", consequence="discard titles containing: {value}"),

        Question("primary_backend", f"Primary LLM backend -- {', '.join(backends)}?",
                 parse_choice(*backends),
                 ("triage.primary_backend", "cv.primary_backend", "track.primary_backend"),
                 "Providers", hint="Set once; written into all three blocks that take one."),
        Question("fallback_backend", f"Fallback LLM backend -- {', '.join(backends)}?",
                 parse_choice(*backends),
                 ("triage.fallback_backend", "cv.fallback_backend", "track.fallback_backend"),
                 "Providers"),
        Question("renderer", f"CV renderer -- {', '.join(renderers)}?", parse_choice(*renderers),
                 ("cv.renderer",), "Providers",
                 hint="weasyprint is bundled: pip install 'sluice[render]'."),
    )
```

- [ ] **Step 3b: Write the prose roster and its completeness guard** (`tests/onboard_prose.py`)

```python
"""Every string `sluice/onboard/` puts in front of a user or into their files.

A ROSTER plus a completeness guard, the shape `tests/conftest.py` already uses for
`PATH_ENV_VARS`: the roster is hand-listed so the sweep stays legible, and the guard pins it
against what the source actually declares, so a new constant cannot ship unswept.

Discovery alone was rejected: it would sweep `NO_TAXONOMY_WORDS` (the vocabulary itself, which
contains every banned word by construction and would fail always) and `_DEFAULT_CRITERIA`
(imported into `plan`'s namespace, authored elsewhere, governed by its own guard). Both need a
NAMED exemption, and once exemptions exist a bare `dir()` sweep is no simpler than a roster.
"""
import inspect

# Module-level string constants that are NOT shipped prose, each with its reason.
_NOT_PROSE = {
    # The banned vocabulary itself. Sweeping it is a guaranteed self-hit.
    ("sluice.onboard.questions", "NO_TAXONOMY_WORDS"),
    # Authored in triage/prompt.py, imported here; governed by
    # test_shipped_prompt_expresses_no_role_or_culture_preference. Exempt on PROVENANCE, not to
    # hide a failure -- measured, it trips zero words in NO_TAXONOMY_WORDS. Re-measure before
    # widening this set: an exemption that would otherwise fire is a suppressed finding.
    ("sluice.onboard.plan", "_DEFAULT_CRITERIA"),
    ("sluice.onboard.plan", "PROFILE_HEADINGS"),   # derived FROM the above
}


def shipped_prose():
    """[(label, text), ...] for every surface a user reads."""
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    from sluice.onboard.questions import catalogue

    out = []
    for q in catalogue(default_vault="/example/vault"):
        for attr in ("prompt", "hint", "consequence"):
            out.append((f"catalogue[{q.key}].{attr}", getattr(q, attr)))
    out.append(("plan._HEADER", plan_mod._HEADER))
    for section, blurb in plan_mod._SECTION_BLURB.items():
        out.append((f"plan._SECTION_BLURB[{section}]", blurb))
    for heading, (_key, prompt) in plan_mod._PROFILE_PROMPTS.items():
        out.append((f"plan._PROFILE_PROMPTS[{heading}]", prompt))
    for key, prompt in ask_mod._PROFILE_QUESTIONS:
        out.append((f"ask._PROFILE_QUESTIONS[{key}]", prompt))
    return out


def _declared_string_constants():
    """Module-level str / dict-of-str / tuple-of-pairs constants across the package."""
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    import sluice.onboard.questions as q_mod

    found = set()
    for mod in (ask_mod, plan_mod, q_mod):
        for name, value in vars(mod).items():
            if name.startswith("__") or inspect.ismodule(value) or callable(value):
                continue
            if isinstance(value, (str, dict, tuple, list)) and value:
                found.add((mod.__name__, name))
    return found
```

```python
# in tests/test_onboard_questions.py
def test_the_prose_roster_covers_every_declared_constant():
    """A new module-level constant must be either swept or NAMED as not-prose. Without this the
    roster is an enumeration, and this repo's enumerations have leaked four times."""
    from tests.onboard_prose import _NOT_PROSE, _declared_string_constants, shipped_prose
    declared = _declared_string_constants()
    assert declared, "the constant sweep found nothing"
    swept = {lbl.split("[")[0].split(".")[-1] for lbl, _ in shipped_prose()}
    swept |= {"catalogue"}
    for module, name in sorted(declared):
        if (module, name) in _NOT_PROSE:
            continue
        assert name in swept or name.lstrip("_") in swept, \
            f"{module}.{name} is neither swept as prose nor named in _NOT_PROSE"
```

- [ ] **Step 4: Run and lint**

Run: `.venv/bin/python -m pytest tests/test_onboard_questions.py -q && .venv/bin/ruff check sluice tests`
Expected: all green

- [ ] **Step 5: Run the FALSIFYING witness on the smoke test before trusting it**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/questions.py /tmp/questions.py.bak
cp sluice/onboard/plan.py /tmp/plan.py.bak
cp sluice/onboard/ask.py /tmp/ask.py.bak

# (a) the catalogue surface -- add to the accept_titles hint:
#     "Most people put a platform role here."
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_no_shipped_prose_names_an_exemplar" -v
# Expected: FAIL -- if it PASSES the guard is inert and must not be trusted or cited.
cp /tmp/questions.py.bak sluice/onboard/questions.py

# (b) a surface the ROUND-1 guard did not cover. Witness each separately: an aggregate cannot
#     distinguish "three surfaces swept" from "one swept and two ignored".
#     Add to plan._SECTION_BLURB["Want"]: " Most people start with a platform role."
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_no_shipped_prose_names_an_exemplar" -v
# Expected: FAIL
cp /tmp/plan.py.bak sluice/onboard/plan.py

# (c) and the terminal prompts, likewise uncovered in round 1.
#     Change ask._PROFILE_QUESTIONS' "who" prompt to mention "a platform engineer".
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_no_shipped_prose_names_an_exemplar" -v
# Expected: FAIL
cp /tmp/ask.py.bak sluice/onboard/ask.py

# (d) the completeness guard: add a new module-level constant that is neither swept nor exempt,
#     e.g. `_EXTRA_BLURB = "hello"` in plan.py
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_the_prose_roster_covers_every_declared_constant" -v
# Expected: FAIL
cp /tmp/plan.py.bak sluice/onboard/plan.py

git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/questions.py tests/test_onboard_questions.py
git commit -m "feat(onboard): the question catalogue, blank-means-skip (#8)

Every preference question skips on blank; the vault is the one exception
and takes its default as a PARAMETER, so a pure catalogue does not import
the concrete store.

parse_int rejects yes/on/true BEFORE parsing a number (#75). parse_url is
a scheme check, deliberately not core/urlguard, which resolves DNS. The
backend and renderer choices are derived from the live registries and
pinned by completeness tests, rather than being a second hand-list of a
name-keyed registry.

The neutrality guard is named a SMOKE TEST because that is what it is:
word-boundary matched, one shared vocabulary, a positive control, a scope
assertion, and a falsifying witness run before it is trusted.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: `build_plan` — the config half

**Files:** Create `sluice/onboard/plan.py`, `tests/test_onboard_plan.py`

**Interfaces:** Produces `InitPlan` (frozen: `config_dest`, `config_text`, `profile_dest`, `profile_text`, `notes: tuple`); `build_plan(answers, *, config_dest, profile_dest, default_vault, profile_answers=None, sources=None) -> InitPlan`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_plan.py
"""`build_plan` is a pure function from a dict to two strings, which is what lets the load-bearing
property be a unit test instead of a wizard transcript."""
import dataclasses
import re

import pytest
import yaml

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.onboard.plan import build_plan
from sluice.onboard.questions import catalogue
from sluice.track.config import load_track_config
from sluice.triage.config import load_triage_config

VAULT = "/example/vault"
LOADERS = (load_config, load_triage_config, load_cv_config, load_track_config)


def _plan(tmp_path, answers=None, **kw):
    return build_plan(answers or {}, config_dest=str(tmp_path / "config.yaml"),
                      profile_dest=str(tmp_path / "Profile.md"), default_vault=VAULT, **kw)


def _written(tmp_path, answers=None, **kw):
    path = tmp_path / "config.yaml"
    path.write_text(_plan(tmp_path, answers, **kw).config_text, encoding="utf-8")
    return str(path)


# ── the enumerated differential (replaces v1's 13-field hand-list) ───────────
@pytest.mark.parametrize("loader", LOADERS, ids=lambda f: f.__name__)
def test_an_unanswered_wizard_writes_a_config_identical_to_no_config_at_all(tmp_path, loader):
    """Field-for-field against the code defaults, ENUMERATED not hand-listed -- so a future
    catalogue key rendered with a value cannot slip past, and nothing has to be kept in step by
    hand. `vault_dir` is the one legitimate difference: it is the wizard's only required answer."""
    emitted = dataclasses.asdict(loader(_written(tmp_path)))
    baseline = dataclasses.asdict(loader(None))
    fields = set(emitted)
    assert fields, "the field sweep enumerated nothing"          # SCOPE
    for name in sorted(fields - {"vault_dir"}):
        assert emitted[name] == baseline[name], f"{loader.__name__}.{name} was overridden"


def test_the_template_contains_every_catalogue_key_COMMENTED(tmp_path):
    """SCOPE, paired with the differential above: that assertion passes just as happily on an
    EMPTY file, since the loaders would return the neutral code defaults and every gate would
    abstain for the wrong reason -- the all([]) shape that has shipped three times here.

    No `#?`: the key must be demonstrably COMMENTED, not merely present. And the match is anchored
    to a key LINE, so a comment that merely mentions the key mid-sentence cannot satisfy it."""
    text = _plan(tmp_path).config_text
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#\s*{re.escape(leaf)}:", text, re.M), \
                f"{dotted} is not present-and-commented in the template init writes"


def test_prose_mentioning_a_key_does_NOT_satisfy_the_scope_matcher():
    """NEGATIVE CONTROL. Widening the matcher to `^[#\\s]*` would let an explanatory comment stand
    in for the key -- the matched-by-adjacent-prose bug this repo already shipped."""
    prose = "# set accept_titles: to whatever you like\n"
    assert not re.search(r"^\s*#\s*accept_titles:", prose, re.M)


def test_answers_become_active_keys(tmp_path):
    path = _written(tmp_path, {"accept_titles": ["example role"], "perm_floor": 90000,
                               "lead_ttl_days": 90})
    assert load_triage_config(path).accept_titles == ["example role"]
    assert load_triage_config(path).perm_floor_gbp == 90000
    assert load_config(path).lead_ttl_days == 90


def test_one_backend_answer_fans_out_to_every_block(tmp_path):
    path = _written(tmp_path, {"primary_backend": "openai", "fallback_backend": "anthropic"})
    for loader in (load_triage_config, load_cv_config, load_track_config):
        assert loader(path).primary_backend == "openai"
        assert loader(path).fallback_backend == "anthropic"


def test_the_fan_out_covers_every_config_declaring_a_backend():
    """DISCOVERED, reusing the neutral-defaults sweep's own helper rather than a second, weaker
    copy. #63's lesson: a hand-list of dataclasses leaks exactly like the hand-list of fields it
    replaced -- four were named there and there were six."""
    from tests.test_sluice_neutral_defaults import _discover_config_dataclasses
    declared = {cls.__module__.split(".")[1]
                for cls in _discover_config_dataclasses()
                if "primary_backend" in {f.name for f in dataclasses.fields(cls)}}
    assert declared, "the sweep found no config declaring primary_backend"
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["primary_backend"]
    assert {d.split(".")[0] for d in q.writes_to} == declared


def test_every_emitted_key_is_documented_in_the_example_config():
    example = open("sluice.yaml.example", encoding="utf-8").read()
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#?\s*{re.escape(leaf)}:", example, re.M), \
                f"{dotted} is written by init but undocumented in sluice.yaml.example"


def test_no_answer_emits_a_scalar_that_loads_as_a_bool_where_an_int_is_meant(tmp_path):
    data = yaml.safe_load(_plan(tmp_path, {"lead_ttl_days": 1, "perm_floor": 1}).config_text)
    assert data["lead_ttl_days"] is not True and isinstance(data["lead_ttl_days"], int)
    assert isinstance(data["triage"]["perm_floor_gbp"], int)


def test_nasty_answers_still_yield_loadable_yaml(tmp_path):
    path = _written(tmp_path, {"cv_name": 'O\'Example: "the #1"',
                               "accept_titles": ["yes", "#hash", "back\\slash"]})
    assert load_cv_config(path).name == 'O\'Example: "the #1"'
    assert load_triage_config(path).accept_titles == ["yes", "#hash", "back\\slash"]


def test_each_section_header_appears_once(tmp_path):
    """A fan-out question writes three blocks; without hoisting, its section header and hint were
    emitted three times."""
    text = _plan(tmp_path).config_text
    assert text.count("-- Providers") == 1
    assert text.count("-- Want") == 1


def test_notes_explain_what_a_configured_gate_will_do(tmp_path):
    notes = "\n".join(_plan(tmp_path, {"relevance_keep": ["example role"]}).notes)
    assert "example role" in notes and "dropped before triage" in notes


def test_an_unanswered_run_reports_no_gates(tmp_path):
    assert not any("keep ONLY" in n for n in _plan(tmp_path).notes)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.plan'`

- [ ] **Step 3: Write the config half of `plan.py`**

```python
# sluice/onboard/plan.py
"""Pure planning: answers in, two artefact texts out. No I/O, no prompts, no clock.

That purity is the point. The property this feature lives or dies by -- a run that answers nothing
produces a config that expresses nothing -- is then a table test over a dict rather than something
observable only by driving a wizard and reading files back.

The config is RENDERED FROM THE CATALOGUE rather than being a static template with substitution
holes, which makes "every key the wizard can write appears in the file it writes" true by
construction instead of by review.
"""
from dataclasses import dataclass

from sluice.onboard.emit import flow_list, scalar
from sluice.onboard.questions import catalogue

_SECTION_BLURB = {
    "Vault": "Where your notes live.",
    "You": "Identity used when composing a tailored CV.",
    "Want": "What you are looking for. EVERY key here is optional, and an unset gate passes every\n"
            "lead through rather than filtering on a value you did not choose.",
    "Cost": "Cheap filters applied at scrape time, before anything expensive runs.",
    "Providers": "Which model fills each role. API keys come from the environment, never this\n"
                 "file.",
}

_HEADER = """\
# sluice configuration, written by `sluice init`.
#
# Every key is optional and falls back to a code default. A COMMENTED key is unset, and an unset
# preference gate abstains -- it passes every lead through rather than filtering on a value you did
# not choose. Uncomment a key to turn that gate on.
#
# This file holds personal material, so keep it out of any public repo. Secrets (API keys, private
# hostnames) belong in the environment, not here.
#
# `sluice.yaml.example` in the repo documents every knob, including the ones this wizard does not
# ask about.
"""


@dataclass(frozen=True)
class InitPlan:
    config_dest: str
    config_text: str
    profile_dest: str
    profile_text: str
    notes: tuple = ()


def _unset(value):
    return value is None or value == [] or value == ""


def _render_value(value):
    return flow_list(value) if isinstance(value, list) else scalar(value)


def _render_key(leaf, q, value, indent):
    out = []
    if q.hint:
        out += [f"{indent}# {line}" for line in q.hint.split("\n")]
    if _unset(value):
        # Commented, because an unset key is how a gate abstains. The `<- uncomment` marker matches
        # the convention `sluice.yaml.example` already uses.
        out.append(f"{indent}# {leaf}:   # <- uncomment and set YOUR OWN")
    else:
        out.append(f"{indent}{leaf}: {_render_value(value)}")
    return out


def _grouped(answers, default_vault):
    """Every catalogue key by its top-level YAML block, in ask order. A question can write more
    than one block (`primary_backend` writes three), so this walks `writes_to`."""
    out = {}
    for q in catalogue(default_vault=default_vault):
        for dotted in q.writes_to:
            parts = dotted.split(".")
            block = parts[0] if len(parts) > 1 else ""
            out.setdefault(block, []).append((parts[-1], q, answers.get(q.key)))
    return out


def _render_config(answers, sources, default_vault):
    lines = [_HEADER]
    grouped = _grouped(answers, default_vault)
    # HOISTED out of the per-block loop: a fan-out question appears in three blocks, and a per-block
    # set emitted its section header, blurb and hint once per block.
    sections_seen = set()

    for block in [""] + [b for b in grouped if b]:
        entries = grouped.get(block, [])
        if not entries:
            continue
        indent = "  " if block else ""
        body = []
        for leaf, q, value in entries:
            if q.section and q.section not in sections_seen:
                sections_seen.add(q.section)
                body.append("")
                body.append(f"{indent}# -- {q.section} " + "-" * max(0, 56 - len(q.section)))
                body += [f"{indent}# {ln}" for ln in _SECTION_BLURB.get(q.section, "").split("\n")
                         if ln]
            body += _render_key(leaf, q, value, indent)
        if block:
            # A bare `triage:` with only comments beneath parses as `{'triage': None}`, and relying
            # on each loader to treat that as an empty mapping is a coupling nobody asked for. So
            # the HEADER is commented when every key under it is unset.
            #
            # ONLY the header. Every line in `body` is already a comment, and re-prefixing them
            # produced `#   # accept_titles:` -- which defeated the scope guard's own matcher on 16
            # of 19 keys while the neutrality half stayed green, so the implementer saw one red test
            # whose message was false. Widening the matcher instead would let a comment ABOUT a key
            # stand in for the key: the matched-by-adjacent-prose bug this repo has already shipped.
            active = any(not _unset(v) for _, _, v in entries)
            lines.append("")
            lines.append(f"{block}:" if active else f"# {block}:")
            lines += body
        else:
            lines += body

    lines += _render_sources(sources)
    return "\n".join(lines).rstrip() + "\n"
```

(`_render_sources`, `_render_profile`, `_notes` and `build_plan` land in Tasks 6 and 7. For this task, stub `_render_sources` to `return []`, `_render_profile` to `return ""`, and define `build_plan` with `notes=()` so the config tests run; Task 6 replaces the profile stub and Task 7 the sources stub.)

Add for this task:

```python
def _render_sources(sources):
    return []                       # Task 7 replaces this


def _notes(answers, sources, default_vault):
    """What the config will DO, in plain terms. Written because the shipped example once handed
    every copier an active `relevance_keep` that discarded every title but one, and nothing
    anywhere said so."""
    out = []
    for q in catalogue(default_vault=default_vault):
        value = answers.get(q.key)
        if _unset(value) or value == 0 or not q.consequence:
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        out.append(q.consequence.format(value=shown))
    return tuple(out)


def build_plan(answers, *, config_dest, profile_dest, default_vault,
               profile_answers=None, sources=None) -> InitPlan:
    """The two artefacts `sluice init` writes, as text.

    `answers` holds only the questions the user actually answered -- a skipped question is ABSENT,
    never present-and-empty, so a blank cannot be mistaken downstream for a deliberate empty list.
    """
    sources = sources or {}
    return InitPlan(config_dest=config_dest,
                    config_text=_render_config(answers, sources, default_vault),
                    profile_dest=profile_dest,
                    profile_text=_render_profile(profile_answers),
                    notes=_notes(answers, sources, default_vault))
```

with a temporary `def _render_profile(_): return ""`.

- [ ] **Step 4: Run and lint**

Run: `.venv/bin/python -m pytest tests/test_onboard_plan.py -q && .venv/bin/ruff check sluice tests`
Expected: all green

- [ ] **Step 5: Witness M1 (re-aimed) and M8**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/plan.py /tmp/plan.py.bak

# M1 -- the mutant that ACTUALLY falsifies neutrality. v1 aimed it at a catalogue default, which
# build_plan never reads (it reads answers.get), so that mutant was EQUIVALENT and left the test
# green. DELETE the `if _unset(value):` arm in _render_key so an unset key emits an active value.
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_an_unanswered_wizard_writes_a_config_identical_to_no_config_at_all" -v
# Expected: FAIL on every loader
cp /tmp/plan.py.bak sluice/onboard/plan.py

# M8 -- restore the double-commenting: `lines += body if active else [f"# {ln}" for ln in body]`
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_the_template_contains_every_catalogue_key_COMMENTED" -v
# Expected: FAIL   (and confirm the differential test STAYS GREEN -- that is why the pair exists)
cp /tmp/plan.py.bak sluice/onboard/plan.py

# M9 -- discovery, not hand-list. Temporarily add `primary_backend: str = "x"` to ApplyConfig.
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_the_fan_out_covers_every_config_declaring_a_backend" -v
# Expected: FAIL  (a hand-listed variant would stay green -- that is the point)
git checkout -- sluice/apply/config.py 2>/dev/null || true

git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/plan.py tests/test_onboard_plan.py
git commit -m "feat(onboard): pure build_plan, config rendered from the catalogue (#8)

Rendering from the catalogue makes 'every key the wizard can write appears
in the file it writes' true by construction.

The neutrality guard is an ENUMERATED DIFFERENTIAL -- loader(emitted) is
field-for-field equal to loader(None) except vault_dir -- not a 13-field
hand-list, in a module that uses discovery for the fan-out two tests
below. Paired with a scope assertion, because the differential alone
passes on an EMPTY file.

An inactive block is commented ONCE. Double-commenting defeated the scope
matcher on 16 of 19 keys while neutrality stayed green, and widening the
matcher instead would let a comment ABOUT a key stand in for the key.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: The profile — defaults from `_DEFAULT_CRITERIA`

**Files:** Modify `sluice/onboard/plan.py`. Create `tests/test_onboard_profile.py`

**Interfaces:** Produces `plan.default_sections() -> dict[str, str]`; `plan.PROFILE_HEADINGS: tuple[str, ...]` (**derived**, not hand-written)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_profile.py
"""The round-1 CRITICAL. `build_system_prompt_from` falls back to `_DEFAULT_CRITERIA` only when the
criteria are missing or EMPTY, and the scaffold is always non-empty -- so a scaffold of bare
headings permanently strips the judge's abstain instructions, while the surrounding scaffold still
tells it to treat the profile as authoritative and not to hedge into research. Running the
onboarding command would make an unconfigured install STOP abstaining."""
import re

from sluice.onboard.plan import PROFILE_HEADINGS, build_plan, default_sections
from sluice.triage.prompt import _DEFAULT_CRITERIA, build_system_prompt_from

ABSTAIN_MARKERS = ("prefer `research`", "do not score on role shape",
                   "Do not assume a culture preference", "never invent or assume")


def _profile(**kw):
    return build_plan({}, config_dest="/example/c.yaml", profile_dest="/example/p.md",
                      default_vault="/example/vault", **kw).profile_text


def test_an_unanswered_profile_still_carries_every_abstain_instruction():
    """THE regression this task exists for."""
    prompt = build_system_prompt_from(_profile())
    for marker in ABSTAIN_MARKERS:
        assert marker in prompt, f"the scaffold dropped: {marker!r}"


def test_an_unanswered_profile_is_not_treated_as_configured():
    assert "No Judging Profile has been configured yet" in build_system_prompt_from(_profile())


def test_an_answered_heading_replaces_the_default_prose_for_that_heading_only():
    text = _profile(profile_answers={"target_shape": "Example target shape."})
    assert "Example target shape." in text
    # ...and the OTHER headings keep their defaults, so answering one does not disarm the rest.
    assert "Do not assume a culture preference" in text


def test_the_headings_are_DERIVED_from_the_scaffold_not_restated():
    """v1 hand-copied five headings and pinned them by equality against the source. Splitting the
    source removes the duplicate entirely -- there is no second list to drift."""
    assert PROFILE_HEADINGS == tuple(default_sections())
    scaffold = re.findall(r"^#{2,3} .+$", _DEFAULT_CRITERIA, re.M)
    assert list(PROFILE_HEADINGS) == scaffold
    assert len(PROFILE_HEADINGS) == 5                      # SCOPE: a split that found nothing
    assert all(default_sections()[h].strip() for h in PROFILE_HEADINGS)   # ...or empty bodies


def test_every_heading_appears_in_the_written_profile():
    text = _profile()
    for heading in PROFILE_HEADINGS:
        assert heading in text


def test_the_profile_carries_no_frontmatter():
    """`_strip_frontmatter` drops a leading `---` block, so emitting one writes something the judge
    is guaranteed never to see."""
    assert not _profile().startswith("---")


def test_the_scaffold_prompts_name_no_exemplar():
    from sluice.onboard.questions import expresses_a_preference
    text = _profile()
    assert text.strip()                                     # SCOPE
    # The DEFAULT prose is `_DEFAULT_CRITERIA`, already governed by its own guard; sweep only the
    # HTML-comment prompts this module adds.
    for prompt in re.findall(r"<!--(.*?)-->", text, re.S):
        assert not expresses_a_preference(prompt)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_profile.py -q`
Expected: FAIL — `ImportError: cannot import name 'PROFILE_HEADINGS'`

- [ ] **Step 3: Replace the `_render_profile` stub in `sluice/onboard/plan.py`**

Add `import re` and `from sluice.triage.prompt import _DEFAULT_CRITERIA` at the top, then:

```python
def default_sections() -> dict:
    """`_DEFAULT_CRITERIA` split on its own headings: heading -> the shipped prose under it.

    DERIVED, so there is no second copy of the heading list to drift. v1 hand-wrote the five and
    pinned them by equality against this source; splitting the source removes the duplicate
    instead of testing for it.
    """
    parts = re.split(r"^(#{2,3} .+)$", _DEFAULT_CRITERIA, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


PROFILE_HEADINGS = tuple(default_sections())

# heading -> (answer key, the prompt shown when it is unanswered). The prompts ask what the judge
# needs and propose no answer: a wizard suggesting "a startup, or an enterprise?" would ship an
# opinion exactly as a default would.
_PROFILE_PROMPTS = {
    "## Who this candidate is": (
        "who", "Replace the paragraph above with your background and what you are optimising\n"
               "this search for. The judge treats it as authoritative for who you are."),
    "### Target and wrong shape": (
        "target_shape", "Replace the paragraph above with the shape of role you want and the\n"
                        "shape that is wrong. Scope, level and titles are all fair game -- the\n"
                        "judge reads this as prose."),
    "### Background grounding": (
        "grounding", "Replace the paragraph above with history the judge should assume you\n"
                     "already satisfy, so it stops raising those as concerns."),
    "## Win patterns and anti-patterns": (
        "patterns", "Replace the paragraph above with wording in a job ad that attracts you and\n"
                    "wording that repels you. Quote what you actually see."),
    "## Industry filter (judgement-based, not categorical)": (
        "industry", "Replace the paragraph above with sectors you will and will not work in.\n"
                    "Leave it as-is if you have no sector view."),
}


def _render_profile(profile_answers):
    """Every heading present. An UNANSWERED heading keeps `_DEFAULT_CRITERIA`'s own prose.

    That is the round-1 Critical. `build_system_prompt_from` falls back to `_DEFAULT_CRITERIA` only
    when the criteria text is missing or EMPTY, and this file is never empty -- so emitting bare
    headings would permanently strip the four instructions telling the judge to abstain ("prefer
    `research`", "do not score on role shape", "do not assume a culture preference", "never invent
    past employers") while the surrounding scaffold still tells it to treat the profile as
    authoritative and to be willing to dismiss. An unconfigured install would stop abstaining: the
    672ad2a class, delivered by the feature built to fix onboarding.

    Carrying the default prose means the shipped abstain instructions stay live until a human
    replaces them -- the default IS used when the user does not answer.

    No frontmatter: `_strip_frontmatter` drops a leading `---` block before the judge sees it.
    """
    sections = default_sections()
    out = ["# Judging Profile", "",
           "The criteria sluice judges every lead against. Edit it in Obsidian whenever your",
           "search changes; the next run picks it up with no code change.",
           "",
           "Nothing here is shipped by sluice as an opinion about which jobs are good. The text",
           "below each heading is the neutral default: it tells the judge to abstain where it has",
           "no information. Replace it with your own and the judge starts using yours.",
           ""]
    for heading in PROFILE_HEADINGS:
        key, prompt = _PROFILE_PROMPTS[heading]
        answer = (profile_answers or {}).get(key)
        out += [heading, ""]
        if answer:
            out += [answer.strip(), ""]
        else:
            out += [sections[heading], "", "<!--", prompt, "-->", ""]
    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Step 4: Run and lint**

Run: `.venv/bin/python -m pytest tests/test_onboard_profile.py tests/test_onboard_plan.py -q && .venv/bin/ruff check sluice tests`
Expected: all green

- [ ] **Step 5: Witness M3 — the drift pin, mutating the SOURCE**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/triage/prompt.py /tmp/prompt.py.bak
# In _DEFAULT_CRITERIA, RENAME "### Background grounding" to "### Background"
.venv/bin/python -m pytest "tests/test_onboard_profile.py::test_the_headings_are_DERIVED_from_the_scaffold_not_restated" -v
# Expected: PASS -- the headings are DERIVED, so they follow the source. That is the design.
# The pin that must redden is the PROMPTS map, which is keyed by heading:
.venv/bin/python -m pytest tests/test_onboard_profile.py -q
# Expected: FAIL with KeyError on the renamed heading -- a heading change forces a prompt update.
cp /tmp/prompt.py.bak sluice/triage/prompt.py

# And the Critical itself:
cp sluice/onboard/plan.py /tmp/plan.py.bak
# In _render_profile, DELETE `sections[heading], "",` from the unanswered arm
.venv/bin/python -m pytest "tests/test_onboard_profile.py::test_an_unanswered_profile_still_carries_every_abstain_instruction" -v
# Expected: FAIL
cp /tmp/plan.py.bak sluice/onboard/plan.py
git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/plan.py tests/test_onboard_profile.py
git commit -m "fix(onboard): an unanswered profile heading carries the shipped default (#8)

The round-1 Critical. build_system_prompt_from falls back to
_DEFAULT_CRITERIA only when the criteria are missing or EMPTY, and a
scaffold is never empty -- so bare headings would have permanently
stripped the judge's abstain instructions while the surrounding scaffold
still told it to treat the profile as authoritative and not to hedge into
research. Running the onboarding command would have made an unconfigured
install stop abstaining.

Headings are now DERIVED by splitting _DEFAULT_CRITERIA rather than
hand-copied and pinned by equality -- there is no second list to drift.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 7: The board walk

**Files:** Modify `sluice/onboard/plan.py`, `sluice/onboard/ask.py` (created in Task 8 — **order this task after Task 8** if the asker does not exist yet; the renderer half below is independent and can land first). Create `tests/test_onboard_sources.py`

**Interfaces:** Produces `plan._render_sources(sources: dict) -> list[str]`; `ask.collect_sources(asker, source_ids) -> dict`

`sources` shape: `{source_id: {"enabled": bool, "searches": [[label, url], ...]}}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_sources.py
"""The board walk. Folded in after round 1 flagged `build_plan(sources=)` as a parameter with no
caller -- the premature abstraction the seams doctrine warns against."""
import yaml

from sluice.core.config import load_config
from sluice.onboard.plan import build_plan

SRC = {"reed": {"enabled": True,
                "searches": [["Example search", "https://example.invalid/jobs"]]},
       "remoteok": {"enabled": False, "searches": []}}


def _text(sources=None):
    return build_plan({}, config_dest="/example/c.yaml", profile_dest="/example/p.md",
                      default_vault="/example/vault", sources=sources).config_text


def test_no_sources_emits_only_the_commented_example(tmp_path):
    """The abstain default: every source runs its own neutral example search."""
    text = _text()
    assert "# sources:" in text
    assert yaml.safe_load(text).get("sources") is None


def test_a_walked_source_round_trips_through_the_real_loader(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(_text(SRC), encoding="utf-8")
    cfg = load_config(str(path))
    assert cfg.sources["reed"].enabled is True
    assert cfg.sources["remoteok"].enabled is False
    assert cfg.sources["reed"].searches == [["Example search", "https://example.invalid/jobs"]]


def test_a_search_label_with_yaml_metacharacters_survives(tmp_path):
    nasty = {"reed": {"enabled": True,
                      "searches": [["O'Example: #1, \"remote\"", "https://example.invalid/j?a=b"]]}}
    path = tmp_path / "c.yaml"
    path.write_text(_text(nasty), encoding="utf-8")
    assert load_config(str(path)).sources["reed"].searches[0][0] == "O'Example: #1, \"remote\""


def test_the_walk_is_offline_and_sees_every_registered_board():
    """Measured: all 22 load with no Camofox import."""
    import sys
    from sluice.ingest import sources as registry
    assert len(registry.all_sources()) >= 20
    assert not [m for m in sys.modules if "camofox" in m]
```

Add to `tests/test_onboard_ask.py` (Task 8) once the asker exists:

```python
def test_collect_sources_takes_ids_then_label_url_pairs_until_a_blank_label():
    from sluice.onboard.ask import collect_sources
    script = "reed\nExample search\nhttps://example.invalid/jobs\n\n"
    got = collect_sources(_tty(script), ["reed", "remoteok"])
    assert got == {"reed": {"enabled": True,
                            "searches": [["Example search", "https://example.invalid/jobs"]]}}


def test_a_bad_search_url_is_re_asked_not_dropped():
    """A mistyped board URL that is silently skipped is a source the user believes is configured
    and is not."""
    script = "reed\nExample search\nnot-a-url\nhttps://example.invalid/jobs\n\n"
    got = collect_sources(_tty(script), ["reed"])
    assert got["reed"]["searches"] == [["Example search", "https://example.invalid/jobs"]]


def test_no_selection_means_no_sources_block():
    assert collect_sources(_tty("\n"), ["reed"]) == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_sources.py -q`
Expected: FAIL — `sources` key absent from the emitted config

- [ ] **Step 3: Replace the `_render_sources` stub in `plan.py`**

```python
def _render_sources(sources):
    """`sources:` is a mapping keyed by source id, shaped unlike every other block, so it renders
    separately rather than being forced through `_grouped`."""
    out = ["", "# -- Sources " + "-" * 56,
           "# Which boards to scrape, and the searches to run on each. A source with no `searches`",
           "# override runs its own neutral example search."]
    if not sources:
        out += ["# sources:",
                "#   example_source:",
                "#     searches:",
                '#       - ["Example search", "https://example.invalid/jobs"]']
        return out
    out.append("sources:")
    for sid in sorted(sources):
        spec = sources[sid]
        out.append(f"  {sid}:")
        out.append(f"    enabled: {scalar(bool(spec.get('enabled', True)))}")
        searches = spec.get("searches") or []
        if searches:
            out.append("    searches:")
            out += [f"      - [{scalar(label)}, {scalar(url)}]" for label, url in searches]
    return out
```

- [ ] **Step 4: Add `collect_sources` to `sluice/onboard/ask.py`**

```python
def collect_sources(asker, source_ids) -> dict:
    """Two passes, not one 22-deep interrogation: select boards, then collect each board's searches.

    A search is a label plus a URL pasted from a browser, so the URL is re-asked on a parse failure
    rather than dropped -- a mistyped board URL that is silently skipped leaves the user believing a
    source is configured when it is not.

    An empty selection returns `{}`, which emits the commented example block and lets every source
    run its own neutral example search: the abstain default, unchanged.
    """
    picked = asker.ask_ids("Which boards do you want to scrape?", source_ids)
    out = {}
    for sid in picked:
        searches = []
        while True:
            label = asker.ask_text_plain(f"{sid} -- search label (blank to finish)?")
            if not label:
                break
            url = asker.ask_url(f"{sid} -- URL for {label!r}?")
            if url:
                searches.append([label, url])
        out[sid] = {"enabled": True, "searches": searches}
    return out
```

with `ask_ids`, `ask_text_plain` and `ask_url` on both askers (`NoInputAsker` returns `[]`/`None`, so `--no-input` writes no `sources:` block and the enter-through equivalence holds exactly).

- [ ] **Step 5: Run and lint**

Run: `.venv/bin/python -m pytest tests/test_onboard_sources.py tests/test_onboard_plan.py -q && .venv/bin/ruff check sluice tests`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/plan.py sluice/onboard/ask.py tests/test_onboard_sources.py
git commit -m "feat(onboard): the board walk (#8)

Folded in rather than deferred -- round 1 correctly flagged
build_plan(sources=) as a parameter with no caller, the premature
abstraction the seams doctrine warns against.

Two passes, not one 22-deep interrogation: select boards, then collect
label/URL pairs. A bad URL is re-asked rather than dropped, because a
silently skipped search is a source the user believes is configured and is
not. An empty selection writes no sources block, so every source runs its
own neutral example search -- the abstain default, unchanged.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 8: The asker

**Files:** Create `sluice/onboard/ask.py`, `tests/test_onboard_ask.py`

**Interfaces:** Produces `MissingAnswer(RuntimeError)`; `NoInputAsker(presets=None)`; `TtyAsker(stdin, stdout, editor=None)`; `collect(asker, questions) -> dict`; `collect_profile(asker) -> dict`; `edit_in_editor(prompt, *, editor, run=subprocess.call) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_ask.py
"""The asker is the only impure half. Its load-bearing property is that the TTY path and the
--no-input path CONVERGE: the wizard is friendlier, not different."""
import io
import os

import pytest

from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect, collect_profile,
                                edit_in_editor)
from sluice.onboard.questions import catalogue

VAULT = "./vault"


def _tty(script, editor=None):
    return TtyAsker(stdin=io.StringIO(script), stdout=io.StringIO(), editor=editor)


def _cat():
    return catalogue(default_vault=VAULT)


def test_a_blank_tty_vault_answer_is_PARSED_like_a_typed_one(tmp_path, monkeypatch):
    """The round-1 High. v1 returned q.default unparsed, so a fresh-install TTY run wrote a
    cwd-relative vault_dir into a per-system config -- and its convergence test compared the buggy
    value to itself, passing BECAUSE of the bug."""
    monkeypatch.chdir(tmp_path)
    got = collect(_tty("\n" * (len(_cat()) + 4)), _cat())
    assert os.path.isabs(got["vault_dir"])
    assert got["vault_dir"] == str(tmp_path / "vault")


def test_the_tty_and_flag_paths_agree_on_an_INDEPENDENTLY_stated_answer(tmp_path):
    """Seeded from a literal both sides are given, never from the other arm's output -- v1 fed the
    TTY's own answer into the flag path, so the test could not see them diverge."""
    typed = str(tmp_path / "notes")
    tty = collect(_tty(typed + "\n" + "\n" * (len(_cat()) + 4)), _cat())
    flags = collect(NoInputAsker(presets={"vault_dir": typed}), _cat())
    assert tty == flags == {"vault_dir": typed}


def test_blank_answers_skip_every_preference_question(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert set(collect(_tty("\n" * (len(_cat()) + 4)), _cat())) == {"vault_dir"}


def test_no_input_without_the_vault_refuses_rather_than_hanging():
    """Never block on a pipe: a wizard waiting on stdin in CI is a hung job with no diagnosis."""
    with pytest.raises(MissingAnswer, match="--vault"):
        collect(NoInputAsker(presets={}), _cat())


def test_no_input_never_auto_takes_a_default():
    """A default must never be written into a config nobody was asked about."""
    got = collect(NoInputAsker(presets={"vault_dir": "/example/v"}), _cat())
    assert got == {"vault_dir": "/example/v"}


def test_answers_are_parsed_not_stored_raw(tmp_path):
    script = "\n".join([str(tmp_path), "", "", "", "example role, other role", "", "", "", "450"]
                       + [""] * len(_cat()))
    got = collect(_tty(script), _cat())
    assert got["accept_titles"] == ["example role", "other role"]
    assert got["contract_floor"] == 450 and isinstance(got["contract_floor"], int)


def test_a_bad_answer_is_re_asked_on_a_tty(tmp_path):
    script = "\n".join([str(tmp_path), "", "", "", "", "", "", "", "yes", "450"]
                       + [""] * len(_cat()))
    asker = _tty(script)
    assert collect(asker, _cat())["contract_floor"] == 450
    assert "number" in asker.stdout.getvalue()


def test_editor_content_is_returned_when_the_editor_succeeds():
    def fake_run(argv):
        with open(argv[-1], "w", encoding="utf-8") as fh:
            fh.write("Example prose the user typed.\n")
        return 0
    assert edit_in_editor("prompt", editor="vi", run=fake_run) == "Example prose the user typed."


def test_every_editor_failure_mode_falls_back_to_the_scaffold():
    assert edit_in_editor("p", editor="vi", run=lambda a: 1) is None      # non-zero exit
    assert edit_in_editor("p", editor="vi", run=lambda a: 0) is None      # unchanged
    assert edit_in_editor("p", editor=None, run=lambda a: 0) is None      # unset
    def boom(argv):
        raise OSError("not found")
    assert edit_in_editor("p", editor="nope", run=boom) is None           # not installed


def test_editor_command_is_split_not_shelled():
    seen = {}
    def fake_run(argv):
        seen["argv"] = argv
        return 1
    edit_in_editor("p", editor="code --wait", run=fake_run)
    assert seen["argv"][:2] == ["code", "--wait"]


def test_collect_profile_returns_only_answered_headings():
    got = collect_profile(_tty("Example background.\n\n\n\n\n"))
    assert got["who"] == "Example background."
    assert "target_shape" not in got
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_ask.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.ask'`

- [ ] **Step 3: Write the asker**

Key points, each guarding a round-1 finding:

```python
    def ask(self, q):
        ...
            if not raw.strip():
                # PARSE the default too. Returning it raw made the fresh-install TTY run write a
                # cwd-relative vault_dir, and it is the only question with a default -- so the most
                # common path was the unprotected one.
                return q.parse(q.default) if q.default is not None else None
```

and in `NoInputAsker.ask`, only two arms — the preset, and the `vault_dir` refusal, then `return None`. The v1 middle arm (`q.default is not None and q.key != "vault_dir"`) was unreachable *and* the wrong rule: the moment a future question gains a default, `--no-input` would silently write it into a config nobody was asked about.

Otherwise as in the spec: `edit_in_editor` returns `None` on every failure mode, uses `shlex.split`, never `shell=True`; `collect` drops `None`/`""`/`[]` so a skipped question is absent rather than present-and-empty.

**The profile prompts are shipped prose and are swept by Task 4's roster** (`tests/onboard_prose.py` reads `ask._PROFILE_QUESTIONS`), so they must name no exemplar. Round 1 caught the v1 wording here — "your background and seniority" — via a substring match on `senior`; the fix is word boundaries, *and* prose that does not lean on a level word at all:

```python
# Keyed to `plan._PROFILE_PROMPTS`, in ask order. Shipped prose: swept by
# tests/onboard_prose.py, so no exemplar, no proposed taxonomy.
_PROFILE_QUESTIONS = (
    ("who", "In a sentence or two: your background, and how much scope you carry?"),
    ("target_shape", "The shape of role you want, and the shape that is wrong?"),
    ("grounding", "What should the judge assume you already satisfy?"),
    ("patterns", "Wording in a job ad that attracts you, and wording that repels you?"),
    ("industry", "Sectors you will or will not work in?"),
)
```

The keys must match `plan._PROFILE_PROMPTS`'s, since `collect_profile`'s output is passed straight to `build_plan(profile_answers=…)`. Add a test asserting the two key sets are equal — a mismatch means a typed answer is silently dropped:

```python
def test_the_asker_and_the_renderer_agree_on_the_profile_answer_keys():
    from sluice.onboard.ask import _PROFILE_QUESTIONS
    from sluice.onboard.plan import _PROFILE_PROMPTS
    assert {k for k, _ in _PROFILE_QUESTIONS} == {k for k, _ in _PROFILE_PROMPTS.values()}
```

- [ ] **Step 4: Run and lint**

Run: `.venv/bin/python -m pytest tests/test_onboard_ask.py -q && .venv/bin/ruff check sluice tests`
Expected: all green

- [ ] **Step 5: Witness M6 and M7 (re-aimed)**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/ask.py /tmp/ask.py.bak

# M6: DELETE the `raise MissingAnswer(...)` arm, leaving `return None`
.venv/bin/python -m pytest "tests/test_onboard_ask.py::test_no_input_without_the_vault_refuses_rather_than_hanging" -v
# Expected: FAIL
cp /tmp/ask.py.bak sluice/onboard/ask.py

# M7 RE-AIMED. v1 mutated parse_path, which this path never calls -- a false green. Mutate the
# blank branch instead: `return q.default if q.default is not None else None`
.venv/bin/python -m pytest "tests/test_onboard_ask.py::test_a_blank_tty_vault_answer_is_PARSED_like_a_typed_one" -v
# Expected: FAIL
# Confirm the NEW test is the killer, not a pre-existing one:
.venv/bin/python -m pytest tests/test_onboard_questions.py -q      # Expected: still all green
cp /tmp/ask.py.bak sluice/onboard/ask.py
git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/ask.py tests/test_onboard_ask.py
git commit -m "feat(onboard): TTY asker, \$EDITOR prose, non-TTY refusal (#8)

A blank answer is PARSED like a typed one. Returning the default raw made
the fresh-install TTY run write a cwd-relative vault_dir -- the only
question with a default, so the commonest path was the unprotected one --
and the convergence test compared the buggy value to itself.

That test now seeds both arms from an independently stated literal, so it
can actually see them diverge.

NoInputAsker never auto-takes a default: the moment a future question
gains one, --no-input would otherwise write it into a config nobody was
asked about. A missing required answer RAISES naming --vault rather than
blocking on stdin.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: `cmd_init`, CLI wiring, docs

**Files:** Modify `sluice/cli.py`, `README.md`, `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`. Create `tests/harness/initdriver.py`, `tests/functional/test_init.py`

**Interfaces:** Produces `cli.cmd_init(args, config, *, asker=None) -> int`

**The fixture, and why not `cli`:** `tests/functional/conftest.py`'s `cli` calls `build_harness`, which writes a config and `setenv`s `SLUICE_CONFIG` and `VAULT_DIR` (`tests/harness/config.py:213-215`) — *after* any `delenv` a test does. `cmd_init` would always take the skip branch, six of ten tests would fail and two would pass vacuously, including M6's named killer. `tests/conftest.py:46` states the fact outright. `init` needs no browser, renderer, backend or seeded vault, so it gets a thin driver under the autouse `_pin_paths` sandbox alone.

- [ ] **Step 1: Write the driver**

```python
# tests/harness/initdriver.py
"""A `main(argv)` driver for `sluice init` ONLY.

Deliberately NOT the `cli` fixture: that one calls `build_harness`, which writes a config and
setenvs SLUICE_CONFIG and VAULT_DIR -- so `config_file()` would always resolve to an existing file
and `cmd_init` would always take the skip branch. `init` needs no browser, renderer, backend or
seeded vault, so it runs under the autouse `_pin_paths` sandbox alone, which is the only tier that
can witness XDG resolution at all (`tests/conftest.py:46`).
"""
import pytest

from sluice.cli import main


@pytest.fixture
def run_init(capsys, monkeypatch):
    """`run_init(argv) -> (rc, out, err)`, plus `run_init.config_dest` derived from the resolver
    rather than written as a literal."""
    def _run(argv):
        capsys.readouterr()
        rc = main(argv)
        cap = capsys.readouterr()
        return rc, cap.out, cap.err
    from sluice.core.paths import config_file
    _run.config_dest = config_file
    _run.monkeypatch = monkeypatch
    return _run
```

- [ ] **Step 2: Write the failing functional tests**

```python
# tests/functional/test_init.py
"""`sluice init` through the real `main(argv)`."""
import os

import pytest

from sluice.core.paths import config_file
from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import Vault
from tests.harness.initdriver import run_init          # noqa: F401  (fixture)


def test_init_writes_both_artefacts(run_init, tmp_path):
    vault = tmp_path / "notes"
    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert os.path.exists(config_file())
    assert (vault / CRITERIA_RELPATH).exists()
    assert "wrote" in out


def test_the_profile_lands_where_the_judge_reads_it(run_init, tmp_path):
    """Asserted by CALLING read_criteria, not by checking a path."""
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    assert "Judging Profile" in Vault(str(vault)).read_criteria()


def test_a_re_run_clobbers_nothing_and_exits_zero(run_init, tmp_path):
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    (vault / CRITERIA_RELPATH).write_text("MY REAL CRITERIA", encoding="utf-8")
    before = open(config_file(), encoding="utf-8").read()

    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert (vault / CRITERIA_RELPATH).read_text(encoding="utf-8") == "MY REAL CRITERIA"
    assert open(config_file(), encoding="utf-8").read() == before
    assert "exists" in out


def test_no_vault_and_no_terminal_refuses_writing_nothing(run_init, monkeypatch):
    """The autouse `_pin_paths` SETS VAULT_DIR, so without this delenv the test passes for the
    wrong reason -- init would find a vault in the environment and never reach the refusal."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    rc, _out, err = run_init(["init", "--no-input"])
    assert rc == 2
    assert "--vault" in err
    assert not os.path.exists(config_file())


def test_vault_flag_disagreeing_with_the_env_refuses(run_init, tmp_path, monkeypatch):
    """stores/vault.py:_make is env-first, so the seam route would otherwise write to $VAULT_DIR
    while the report names --vault. The two answers contradict each other and only the user knows
    which they meant."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "from-env"))
    rc, _out, err = run_init(["init", "--vault", str(tmp_path / "from-flag"), "--no-input"])
    assert rc == 2
    assert "VAULT_DIR" in err
    assert not os.path.exists(config_file())


def test_an_existing_config_is_kept_and_the_profile_still_scaffolds(run_init, tmp_path):
    dest = config_file()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# hand written\n")
    vault = tmp_path / "notes"
    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert open(dest, encoding="utf-8").read() == "# hand written\n"
    assert (vault / CRITERIA_RELPATH).exists()
    assert "exists" in out


def test_sluice_config_retargets_the_written_config(run_init, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("SLUICE_CONFIG", str(elsewhere))
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0 and elsewhere.exists() and str(elsewhere) in out


def test_init_creates_nothing_under_the_state_or_cache_roots(run_init, tmp_path):
    """#80: a stray file under the state root disarms a relocation notice -- a 0-byte seen.db is
    enough. Asserted against the resolver's own roots, not a literal."""
    from sluice.core.paths import resolve
    run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    for kind in ("state", "cache"):
        root = os.path.dirname(resolve(env_var=None, config_value="", kind=kind, name="probe"))
        assert not os.path.exists(root) or os.listdir(root) == []


def test_a_new_vault_directory_is_reported_as_created(run_init, tmp_path):
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "brand-new"), "--no-input"])
    assert rc == 0 and "created" in out.lower()


def test_a_vault_path_that_is_a_file_refuses(run_init, tmp_path):
    afile = tmp_path / "not-a-dir"
    afile.write_text("x", encoding="utf-8")
    rc, _out, err = run_init(["init", "--vault", str(afile), "--no-input"])
    assert rc == 2 and "not a directory" in err


def test_the_written_config_loads_and_abstains(run_init, tmp_path):
    from sluice.core.config import load_config
    from sluice.triage.config import load_triage_config
    run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    path = config_file()
    assert load_config(path).relevance_keep == []
    assert load_triage_config(path).accept_titles == []
    assert load_config(path).lead_ttl_days == 0
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/functional/test_init.py -q`
Expected: FAIL — `argument group: invalid choice: 'init'`

- [ ] **Step 4: Write `cmd_init`** above `cmd_doctor` in `sluice/cli.py`

Structure (full body follows the v1 shape with these corrections):

```python
def cmd_init(args, config, *, asker=None) -> int:
    """Scaffold a config and a Judging Profile (#8).

    Preflight resolves BOTH destinations before a single question is asked: a wizard that
    interviews someone for five minutes and then says "config already exists" wasted their time to
    learn something it knew at the start.
    """
    import dataclasses
    import sys

    from sluice.core.app import Sluice
    from sluice.core.paths import config_file
    from sluice.core.protocols import CRITERIA_RELPATH
    from sluice.core.vault import DEFAULT_VAULT
    from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect,
                                    collect_profile, collect_sources)
    from sluice.onboard.plan import build_plan
    from sluice.onboard.questions import catalogue
    ...
```

The corrections, each against a round-1 finding:

- **The vault flag/env disagreement refuses.** `stores/vault.py:_make` is env-first, so routing through the seam with both set would write to `$VAULT_DIR` while the report names `--vault`:
  ```python
  env_vault = os.environ.get("VAULT_DIR")
  if args.vault and env_vault and os.path.abspath(os.path.expanduser(env_vault)) != \
          os.path.abspath(os.path.expanduser(args.vault)):
      print("sluice init: --vault and $VAULT_DIR name different directories. Unset one, or pass "
            "the one you mean.", file=sys.stderr)
      return 2
  ```
- **The profile writes through the SEAM**, not `Vault(...)`:
  ```python
  store = Sluice(dataclasses.replace(config, vault_dir=vault_dir)).store()
  handle = store.write_document(CRITERIA_RELPATH, plan.profile_text, only_if_absent=True)
  ```
- **`DEFAULT_VAULT` is passed in**, never imported by the catalogue: `catalogue(default_vault=DEFAULT_VAULT)`.
- **A profile that appears mid-interview is not silently discarded.** When `write_document` abstains *and* `profile_answers` is non-empty, write the composed text to a sibling `Judging Profile.init-scaffold.md` (itself `only_if_absent`) and say so. Do not overwrite; do not stay quiet.
- Config write stays `open(dest, "x")`; partial failure reports both outcomes, exits non-zero, rolls nothing back.

- [ ] **Step 5: Wire the parser**, before `doctor`:

```python
    init = top.add_parser("init", help="scaffold a config and a Judging Profile")
    init.add_argument("--vault", help="your Obsidian vault directory")
    init.add_argument("--no-input", action="store_true",
                      help="take every default; never prompt")
    init.set_defaults(func=cmd_init)
```

- [ ] **Step 6: Run**

Run: `.venv/bin/python -m pytest tests/functional/test_init.py tests/functional/test_cli_contract.py -q`
Expected: all green (the #7 dest-sweep sees `args.vault`/`args.no_input` read directly in `cmd_init`)

- [ ] **Step 7: Docs — all three, and the guard that keeps them true**

- `README.md` — replace the quickstart block **and** line 107's `cp -n sluice.yaml.example sluice.local.yaml`, both with `sluice init`.
- `docs/ARCHITECTURE.md` — add the `onboard/` section (command package, not a sixth sub-app).
- `.rulesync/rules/CLAUDE.md` — **human-gated**: replace the `cp` in "Running the pipeline" with `sluice init`, and amend line 109's "Five sub-apps" to name `onboard/` as a command package beside them. Then `npm run rulesync`.
- Add `tests/test_no_copy_instruction.py`:

```python
"""No shipped doc may instruct a `cp sluice.yaml.example`. The file ships ACTIVE gates -- measured,
`is_relevant("Senior Software Engineer")` is False against a verbatim copy -- so an instruction to
copy it hands a stranger a closed gate with nothing saying so. `sluice init` exists to replace it."""
import glob
import re


def test_no_shipped_doc_tells_anyone_to_copy_the_example():
    docs = ["README.md", ".rulesync/rules/CLAUDE.md", "docs/ARCHITECTURE.md"]
    docs += glob.glob("docs/*.md")
    checked = 0
    for path in docs:
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        checked += 1
        assert not re.search(r"^\s*cp\b.*sluice\.yaml\.example", text, re.M), \
            f"{path} instructs a copy of the example config"
    assert checked >= 3, "the sweep read nothing"          # SCOPE
```

- [ ] **Step 8: Run everything**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add sluice/cli.py tests/harness/initdriver.py tests/functional/test_init.py tests/test_no_copy_instruction.py README.md docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "feat(cli): sluice init (#8)

Preflight resolves both destinations before asking anything. Per-artefact
never-clobber, so an existing config still lets the profile scaffold.
Partial failure is never rolled back: deleting a file we just wrote, to
tidy up after a failure the user can see and retry, is a destructive act.

The profile writes through the STORE SEAM, and --vault disagreeing with
\$VAULT_DIR REFUSES -- _make is env-first, so the naive seam route would
write to the env path while the report named the flag.

Tests use a thin init driver, not the cli fixture: that one calls
build_harness, which setenvs SLUICE_CONFIG and VAULT_DIR, so cmd_init
would always take the skip branch and M6's killer would show false green.

No shipped doc instructs a cp of sluice.yaml.example any more, guarded.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 10: The acceptance scenario

**Files:** Create `tests/e2e/test_init_to_verdicts.py`

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_init_to_verdicts.py
"""Issue #8's acceptance criterion, in TWO ARMS -- a single arm passes even if the profile is
ignored entirely. Same attribution shape as S1 in #58."""
from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import Vault
from sluice.onboard.questions import expresses_a_preference
from sluice.triage.prompt import build_system_prompt_from
from tests.harness.initdriver import run_init          # noqa: F401  (fixture)

FILLED = """\
## Who this candidate is

An example practitioner of the example trade, with example standing.

### Target and wrong shape

Target: an example-shaped role. Wrong: anything at example-lead scope.

## Win patterns and anti-patterns

Attracts: 'example win phrase'. Repels: 'example anti phrase'.
"""


def test_a_scaffolded_profile_still_tells_the_judge_to_abstain(run_init, tmp_path):
    """Arm 1. v1 asserted the OPPOSITE of this and called it the acceptance criterion."""
    vault = tmp_path / "notes"
    assert run_init(["init", "--vault", str(vault), "--no-input"])[0] == 0
    prompt = build_system_prompt_from(Vault(str(vault)).read_criteria())
    assert "No Judging Profile has been configured yet" in prompt
    assert "prefer `research`" in prompt


def test_an_install_with_no_profile_at_all_abstains_identically(tmp_path):
    """Arm 2, the attribution half: scaffolding must not CHANGE the judge's behaviour until a
    human writes something. If arm 1 passed while this failed, arm 1 would prove nothing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "No Judging Profile has been configured yet" in \
        build_system_prompt_from(Vault(str(empty)).read_criteria())


def test_a_filled_profile_reaches_the_judge_verbatim(run_init, tmp_path):
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    (vault / CRITERIA_RELPATH).write_text(FILLED, encoding="utf-8")
    prompt = build_system_prompt_from(Vault(str(vault)).read_criteria())
    assert "example win phrase" in prompt and "example anti phrase" in prompt
    assert "No Judging Profile has been configured yet" not in prompt


def test_the_scaffold_smuggles_no_exemplar_into_the_judge_prompt(run_init, tmp_path):
    """Same shared vocabulary as the unit tier, imported not re-listed."""
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    criteria = Vault(str(vault)).read_criteria()
    assert criteria.strip()                                  # SCOPE
    import re
    for prompt in re.findall(r"<!--(.*?)-->", criteria, re.S):
        assert not expresses_a_preference(prompt)
```

- [ ] **Step 2: Run and lint**

Run: `.venv/bin/python -m pytest tests/e2e/test_init_to_verdicts.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_init_to_verdicts.py
git commit -m "test(e2e): #8's acceptance criterion, in two arms

Arm 1 asserts the scaffold still tells the judge to abstain -- v1 asserted
the OPPOSITE and called it the acceptance criterion, which is how the
Critical would have shipped green. Arm 2 is the attribution half:
scaffolding must not change the judge's behaviour until a human writes
something.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 11: Definition of done

- [ ] **Step 1:** `.venv/bin/python -m pytest -q` → all pass, 0 skipped on Linux/macOS
- [ ] **Step 2:** `.venv/bin/ruff check sluice tests scripts` → `All checks passed!`
- [ ] **Step 3:** `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l` → `9`
- [ ] **Step 4:** `npm ci --ignore-scripts && npm run rulesync && git status --porcelain` → no drift
- [ ] **Step 5: The wizard runs for real, offline, in a throwaway home**

```bash
tmp=$(mktemp -d)
# -u flags FIRST (see Step 6), and -u EDITOR too, or an interactive run would open the developer's
# editor. Measured on macOS: with the -u flags after the assignments this exits 127 before running.
env -u VAULT_DIR -u SLUICE_CONFIG -u EDITOR \
    HOME="$tmp" XDG_CONFIG_HOME="$tmp/.config" XDG_STATE_HOME="$tmp/.local/state" \
    XDG_CACHE_HOME="$tmp/.cache" \
    .venv/bin/python -m sluice.cli init --vault "$tmp/notes" --no-input
cat "$tmp/.config/sluice/config.yaml"
cat "$tmp/notes/Job Applications/Judging Profile.md"
find "$tmp/.local/state" "$tmp/.cache" -type f 2>/dev/null   # MUST be empty
```

- [ ] **Step 6: The refusal refuses on a pipe, and does not hang**

```bash
# -u BEFORE the assignments: BSD/macOS env requires options first and exits 127 otherwise
# ("env: -u: No such file or directory"), which reads as a missing binary rather than a usage error.
env -u VAULT_DIR -u SLUICE_CONFIG HOME="$tmp" \
    .venv/bin/python -m sluice.cli init --no-input < /dev/null
echo "exit: $?"    # MUST be 2
```

- [ ] **Step 7: The scaffolded profile still abstains** — the Critical, checked end to end

```bash
.venv/bin/python -c "
from sluice.core.vault import Vault
from sluice.triage.prompt import build_system_prompt_from
p = build_system_prompt_from(Vault('$tmp/notes').read_criteria())
assert 'No Judging Profile has been configured yet' in p, 'THE CRITICAL REGRESSED'
print('abstain instructions intact')"
```

- [ ] **Step 8: Run `/review-plan` on this document, then `/review-pr` BEFORE pushing.** Round-2 findings historically live inside round-1's fixes.

## Self-review

**Spec coverage.** Every spec section maps to a task: retirement + example relocation → 1; store seam constants + conformance → 2; emitter → 3; catalogue + neutrality smoke test → 4; config rendering + differential + scope + fan-out → 5; the Critical → 6; board walk → 7; asker + refusals → 8; `cmd_init` + seam + docs → 9; acceptance → 10; DoD → 11.

**All nine mutants are witnessed in the task that adds their guard:** M1/M8/M9 in Task 5, M2 in Task 2, M3 in Task 6, M5 in Task 3, M6/M7 in Task 8, plus the retirement's two-armed witness in Task 1 and the neutrality falsifying witness in Task 4.

**Known ordering constraint:** Task 7's `collect_sources` needs `ask.py` from Task 8. The renderer half of Task 7 is independent and may land first; if executing strictly in order, do Task 8 before Task 7's Step 4.

**Second ordering constraint, found while executing Task 4 (2026-07-30).** Task 4's Step 3b roster (`tests/onboard_prose.py`) sweeps three modules — `questions` (Task 4), `plan` (Task 5) and `ask` (Task 8) — and `shipped_prose()` imports all three, so it raises `ModuleNotFoundError` until the LAST of them exists. Task 4 therefore cannot land `tests/onboard_prose.py`, `test_no_shipped_prose_names_an_exemplar`, `test_the_prose_roster_covers_every_declared_constant`, or Step 5's four falsifying witnesses (a)–(d), which all target those two tests. **Land the roster, both tests, and all four witnesses in Task 8**, after `ask.py`. Task 4 keeps the catalogue's own properties plus the preference helper's positive control and word-boundary test, and its file says outright that the shipped-prose surfaces are NOT yet swept — so the gap cannot be mistaken for coverage.

**Task 9: two more, and one of them was a comment nothing could falsify.** (i) The autouse `_pin_paths` SETS `VAULT_DIR`, and v2's new `--vault`/`$VAULT_DIR` disagreement refusal then fires for every test that passes `--vault` — measured, seven of eleven took the refusal instead of the branch they were about. The plan applied the `delenv` to only one test; it belongs in the driver fixture, with the disagreement test setting the variable back itself. (ii) Swapping `open(config_dest, "x")` for `"w"` left every init test GREEN, because the `config_exists` branch means the open is never reached when a config is already there — so the exclusive create, and `cmd_init`'s comment claiming never-clobber "is a property of the open, not of the check above it", were untested prose. Added a test that injects a racer inside `os.makedirs` (the call immediately before the open, so the existence check has already seen nothing); it reddens on `"w"` and nothing else does. **A second line of defence needs a test that reaches PAST the first one.**

**Task 7's two test defects, both found by running them rather than reading them.** `yaml.safe_load(_text())` is `None`, not a mapping: an unanswered run emits a document that is ALL comments, so `.get("sources")` AttributeErrors. The stronger true assertion is that the whole document loads as `None` — abstain at full strength, no active key to override anything. And `assert not [m for m in sys.modules if "camofox" in m]` is process-global: it passes when `tests/test_onboard_sources.py` runs alone and FAILS in the full suite, because eight other test files import camofox first. It now runs the probe in a fresh interpreter, which is the only way the claim means what it says. **An order-dependent assertion that is green in isolation is the shape most likely to be believed.**

**Task 4's `parse_int` test was inert as written, and the witness is what found it.** `pytest.raises(BadAnswer)` around `parse_int("yes")` passes with the `_BOOL_WORDS` guard DELETED, because none of those words parses as an int and the not-a-number arm raises `BadAnswer` too. The guard's whole effect is the message naming the #75 trap, so the test must assert the message (`match="yes/no word"`). Fixed in Task 4; the same shape is worth checking wherever a test asserts only an exception TYPE against a guard that shares its exception with the fall-through path.

**Placeholder scan:** none. Task 5 declares three explicit stubs, each naming the task that replaces it.

**Type consistency:** `catalogue(*, default_vault)`, `Question.hint`/`.writes_to` (tuple of dotted strings)/`.consequence`, `parse_choice(...).allowed`, `InitPlan.config_text`/`.profile_text`/`.notes`, `build_plan(answers, *, config_dest, profile_dest, default_vault, profile_answers=None, sources=None)`, `write_document(rel, text, *, only_if_absent=False) -> str` returning `""` on abstain, `expresses_a_preference(text) -> list`, `default_sections() -> dict` — consistent across Tasks 1-10.
