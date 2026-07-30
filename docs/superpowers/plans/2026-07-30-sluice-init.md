# `sluice init` Implementation Plan — **SUPERSEDED, DO NOT EXECUTE**

> **STOP.** `/review-plan` round 1 (5 reviewers, 2026-07-30) returned **50 findings: 1 Critical,
> 17 High, 21 Medium, 11 Low** against this document. It must be regenerated from the revised spec
> at `docs/superpowers/specs/2026-07-30-sluice-init-design.md`, not patched.
>
> Executing it as written would ship the Critical: the profile scaffold permanently strips the
> judge's abstain instructions, so running the onboarding command makes an unconfigured install stop
> abstaining. Four Highs also make it unrunnable as written — the functional tier is built on a
> fixture that sets `SLUICE_CONFIG`/`VAULT_DIR` so `cmd_init` always takes the skip branch; the
> scope guard fails on 16 of 19 keys through its own renderer's double-commenting; `TtyAsker`
> returns its default unparsed so a blank Enter writes a cwd-relative `vault_dir`; and
> `triage.target_locations` is absent from `sluice.yaml.example`, so Task 4's sweep is red on
> arrival.
>
> Two mutation witnesses were mis-aimed and would have shown false green: **M1** at a test it cannot
> falsify (`build_plan` reads `answers.get(...)`; the defaults live in the asker, so the mutant is
> equivalent), and **M7** at `parse_path`, which the failing path never calls.
>
> The findings are on disk at
> `~/.cache/sluice/review-plan/2026-07-30-sluice-init/20260730T094207Z-4841/findings/`.
> The spec now carries every design-level correction plus the folded-in board walk.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `sluice init`, a setup wizard that writes a neutral config and scaffolds a Judging Profile, so a fresh install has a path from "installed" to "configured" that cannot express a preference the user did not state.

**Architecture:** A new `sluice/onboard/` package split pure-from-impure: a declarative question catalogue and a pure `build_plan(answers) -> InitPlan` producing two artefact texts, plus an impure asker (TTY prompt, `$EDITOR`, non-TTY refusal) injected as a constructor parameter. `cli.cmd_init` preflights destinations, asks, plans, then writes — config via exclusive create, profile via the store seam.

**Tech Stack:** Python 3.12+, standard library only in `sluice/` (`yaml` stays an `ImportError`-guarded import and the emitter does not need it), pytest, ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-07-30-sluice-init-design.md`

## Global Constraints

- **`sluice/` is standard-library only.** No new runtime dependency. `yaml` may only be imported under a guarded `try/except ImportError`; the config emitter must not need it at all.
- **Empty config means abstain.** Every preference question defaults to skip. The one exception is the vault question, which takes `DEFAULT_VAULT` when blank on a TTY.
- **Never-clobber.** No artefact is overwritten, ever. No `--force` flag. Writes use `O_CREAT|O_EXCL`, not check-then-write.
- **Neutrality.** No employer names, role preferences, locations, contact details, hostnames or absolute paths in `sluice/` or `tests/`. Test answers use `Example …`, `example.invalid`, and seeded faker. The wizard's *question text* must express no preference.
- **The `"./"` definition-of-done grep stays at 9 lines.** Verify with `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l`. Import `DEFAULT_VAULT`; never re-spell the literal.
- **Comments explain *why*.** Match the surrounding density; several existing comments encode real incidents.
- **Conventional commits**, trailer `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- **Run once before any mutation witness:** `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- **Mutate by MOVING or DELETING, never by ADDING.** Run the named test **by node id** and confirm no pre-existing test in the same file is what kills it.
- Tests run with `.venv/bin/python -m pytest`; lint with `.venv/bin/ruff check sluice tests scripts`.

## File Structure

**Create**
- `sluice/onboard/__init__.py` — exports `build_plan`, `InitPlan`, `catalogue`
- `sluice/onboard/emit.py` — YAML scalar/flow-list emission (pure)
- `sluice/onboard/questions.py` — the `Question` record, parsers, `catalogue()` (pure)
- `sluice/onboard/plan.py` — `build_plan()`, the config renderer, the profile template (pure)
- `sluice/onboard/ask.py` — `TtyAsker`, `NoInputAsker`, `$EDITOR` handling (impure)
- `tests/test_onboard_emit.py`, `tests/test_onboard_questions.py`, `tests/test_onboard_plan.py`, `tests/test_onboard_ask.py`
- `tests/functional/test_init.py`, `tests/e2e/test_init_to_verdicts.py`

**Modify**
- `sluice/core/protocols.py` — add `CRITERIA_RELPATH`; `write_document` gains `only_if_absent`
- `sluice/core/vault.py` — import both constants; `write_document` honours `only_if_absent`
- `sluice/triage/prompt.py` — import `CRITERIA_RELPATH`
- `sluice/core/config.py` — retire `locations`
- `sluice/cli.py` — `cmd_init` + parser wiring
- `sluice.yaml.example`, `README.md`, `docs/ARCHITECTURE.md`

> **Refinement on the spec:** the spec named three modules; the YAML emitter is split into a fourth (`emit.py`) because it has its own round-trip corpus and no dependency on the catalogue.

---

### Task 1: Shared constants and a never-clobber `write_document`

**Files:**
- Modify: `sluice/core/protocols.py`, `sluice/core/vault.py:32-33`, `sluice/core/vault.py:317-336`, `sluice/triage/prompt.py:20`
- Test: `tests/test_vault_write_document.py`

**Interfaces:**
- Consumes: nothing
- Produces: `sluice.core.protocols.CRITERIA_RELPATH: str`; `sluice.core.vault.DEFAULT_VAULT: str`; `Store.write_document(rel: str, text: str, *, only_if_absent: bool = False) -> str` returning the written path, or `""` when the document existed and was skipped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vault_write_document.py
"""`write_document(only_if_absent=True)` is the never-clobber primitive `sluice init`
scaffolds through. A parameter on the existing writer, not a second write function:
CodeQL reads a new write function as a new sink (#9's `require_status` precedent)."""
import os

import pytest

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import DEFAULT_VAULT, Vault


def test_only_if_absent_creates_when_missing(tmp_path):
    v = Vault(str(tmp_path))
    path = v.write_document(CRITERIA_RELPATH, "first", only_if_absent=True)
    assert path
    assert open(path, encoding="utf-8").read() == "first"


def test_only_if_absent_abstains_and_leaves_the_file_byte_identical(tmp_path):
    v = Vault(str(tmp_path))
    path = v.write_document(CRITERIA_RELPATH, "human wrote this", only_if_absent=True)
    before = open(path, encoding="utf-8").read()

    assert v.write_document(CRITERIA_RELPATH, "SCAFFOLD", only_if_absent=True) == ""
    assert open(path, encoding="utf-8").read() == before


def test_default_overwrites_so_the_existing_caller_is_unchanged(tmp_path):
    v = Vault(str(tmp_path))
    v.write_document("Job Applications/Digest.md", "old")
    v.write_document("Job Applications/Digest.md", "new")
    assert open(os.path.join(str(tmp_path), "Job Applications/Digest.md"),
                encoding="utf-8").read() == "new"


def test_escape_guard_still_fires_under_only_if_absent(tmp_path):
    v = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="escapes the store root"):
        v.write_document("../outside.md", "x", only_if_absent=True)


def test_the_two_constants_have_one_home_each():
    """Three modules must agree on where the Judging Profile lives, or init writes a
    profile the judge never reads -- silently, since a missing profile just falls back
    to the shipped opinion-free default."""
    import sluice.core.vault as vault_mod
    import sluice.triage.prompt as prompt_mod
    assert vault_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert prompt_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert DEFAULT_VAULT == "./vault"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vault_write_document.py -v`
Expected: FAIL — `ImportError: cannot import name 'CRITERIA_RELPATH'`

- [ ] **Step 3: Add the shared constant to `core/protocols.py`**

Add near the top of the module, after the imports:

```python
# Where the judge's criteria live inside a store. Here, in the contract module, because
# it IS part of the Store contract -- the document `read_criteria` serves -- and because
# it was previously spelled as two independent literals (`core/vault.py`,
# `triage/prompt.py`). `sluice init` would have made it three, and a divergence means
# init writes a profile the judge never reads: silent, because a missing profile falls
# back to the shipped default rather than raising.
CRITERIA_RELPATH = os.path.join("Job Applications", "Judging Profile.md")
```

Add `import os` at the top of `protocols.py` if absent.

Update the `Store` protocol's `write_document` signature:

```python
    def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:
        """Write a store-managed document; return an opaque handle.

        `only_if_absent=True` makes this a CREATE that never clobbers: it returns "" and
        writes nothing when the document already exists. `sluice init` scaffolds through
        it, and a filled-in Judging Profile is exactly the thing a re-run must not eat.
        """
```

- [ ] **Step 4: Point `core/vault.py` and `triage/prompt.py` at the constant**

In `sluice/core/vault.py`, replace line 32's literal:

```python
from sluice.core.protocols import CRITERIA_RELPATH

# Kept as a module-level alias so the existing internal call sites read unchanged.
_CRITERIA_RELPATH = CRITERIA_RELPATH
# Public: `sluice init` offers this as the vault question's default. Imported rather
# than re-spelled -- a second `"./vault"` literal would take the definition-of-done
# grep from 9 to 10 and read as exactly the drift that grep exists to catch.
DEFAULT_VAULT = "./vault"
_DEFAULT_VAULT = DEFAULT_VAULT
```

In `sluice/triage/prompt.py`, replace line 20's literal:

```python
from sluice.core.protocols import CRITERIA_RELPATH

_CRITERIA_RELPATH = CRITERIA_RELPATH
```

- [ ] **Step 5: Implement `only_if_absent` in `Vault.write_document`**

Replace the body's write line (`_atomic_write(path, text)`) with:

```python
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if only_if_absent:
            # O_CREAT|O_EXCL, not exists()-then-write: the check and the write are two
            # syscalls, and the thing on the other side of that window is a human editing
            # the note in Obsidian, who takes no lock (#16). An exclusive create makes the
            # never-clobber a property of the open, not of the timing.
            try:
                _write(path, text, exclusive=True)
            except FileExistsError:
                return ""
            return path
        _atomic_write(path, text)
        return path
```

and change the signature to `def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_write_document.py -v`
Expected: 5 passed

- [ ] **Step 7: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: 1549 passed, ruff clean

- [ ] **Step 8: Verify the DoD grep did not move**

Run: `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l`
Expected: `9`

- [ ] **Step 9: Witness M2 — drop the never-clobber**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/vault.py /tmp/vault.py.bak
# DELETE the `if only_if_absent:` block (lines through `return path`), leaving only
# the `_atomic_write(path, text)` fall-through.
.venv/bin/python -m pytest tests/test_vault_write_document.py::test_only_if_absent_abstains_and_leaves_the_file_byte_identical -v
# Expected: FAIL
cp /tmp/vault.py.bak sluice/core/vault.py
git diff --stat   # MUST be empty: a restore list that misses a file leaves the mutant in the tree
```

- [ ] **Step 10: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py sluice/triage/prompt.py tests/test_vault_write_document.py
git commit -m "feat(core): never-clobber write_document + one home for the criteria path (#8)

CRITERIA_RELPATH was two independent literals; \`sluice init\` would have
made it three, and a divergence means init writes a profile the judge
never reads -- silent, since a missing profile falls back to the shipped
default. DEFAULT_VAULT goes public for the same reason: a second \"./vault\"
literal takes the DoD grep from 9 to 10.

only_if_absent is a parameter on the existing writer, not a second write
function (#9's require_status precedent -- CodeQL reads a new write
function as a new sink), and uses O_CREAT|O_EXCL rather than
exists()-then-write because the racer is a human in Obsidian (#16).

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: The YAML scalar emitter

**Files:**
- Create: `sluice/onboard/__init__.py`, `sluice/onboard/emit.py`
- Test: `tests/test_onboard_emit.py`

**Interfaces:**
- Consumes: nothing
- Produces: `sluice.onboard.emit.scalar(value: object) -> str` and `flow_list(values: list) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_emit.py
"""The config `sluice init` writes is a template WITH COMMENTS, so `yaml.safe_dump` is
out (it destroys them) and ruamel is out (`sluice/` is standard-library only). Values are
injected by a conservative emitter instead, and this is the round trip that proves it --
without it, a company name with an apostrophe writes a config that fails to parse."""
import pytest
import yaml

from sluice.onboard.emit import flow_list, scalar

NASTY = [
    "O'Brien",
    'Foo: Bar',
    "#hash",
    "yes",
    "no",
    "on",
    "null",
    "~",
    "!tag",
    "back\\slash",
    'quote"inside',
    "line\nbreak",
    "  leading and trailing  ",
    "café-münster",
    "*anchor",
    "&ref",
    "%directive",
    "@at",
    "`tick",
    "[bracket]",
    "{brace}",
    "- dash",
    "",
]


@pytest.mark.parametrize("value", NASTY)
def test_string_scalars_round_trip(value):
    loaded = yaml.safe_load(f"k: {scalar(value)}")
    assert loaded["k"] == value


@pytest.mark.parametrize("value", [0, 1, 90, 450, 90000, -3])
def test_int_scalars_round_trip_as_ints(value):
    loaded = yaml.safe_load(f"k: {scalar(value)}")
    assert loaded["k"] == value
    assert isinstance(loaded["k"], int)


def test_bools_are_emitted_as_yaml_bools():
    assert yaml.safe_load(f"k: {scalar(True)}")["k"] is True
    assert yaml.safe_load(f"k: {scalar(False)}")["k"] is False


def test_flow_list_round_trips_the_whole_nasty_corpus():
    loaded = yaml.safe_load(f"k: {flow_list(NASTY)}")
    assert loaded["k"] == NASTY


def test_empty_flow_list():
    assert yaml.safe_load(f"k: {flow_list([])}")["k"] == []


def test_a_string_that_looks_like_an_int_stays_a_string():
    """`accept_titles: ["2024"]` must not load as a number."""
    assert yaml.safe_load(f"k: {scalar('2024')}")["k"] == "2024"
    assert isinstance(yaml.safe_load(f"k: {scalar('2024')}")["k"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard'`

- [ ] **Step 3: Create the package and the emitter**

`sluice/onboard/__init__.py`:

```python
"""`sluice init` -- the setup wizard (#8).

A COMMAND package, not a sixth pipeline sub-app: nothing downstream imports it, and it
sits beside the five sub-apps rather than inside the ingest -> triage -> cv -> apply ->
track chain.

Split pure-from-impure on purpose. `questions` and `plan` are pure functions over a
dict, so the property that matters -- a run that answers nothing produces a config that
expresses nothing -- is a unit test rather than a wizard transcript. `ask` holds every
prompt, every terminal read and the one subprocess call.
"""
```

`sluice/onboard/emit.py`:

```python
"""Emit YAML scalars by hand.

The config `sluice init` writes is a TEMPLATE WITH COMMENTS -- the guidance under each
key is most of its value -- so `yaml.safe_dump` cannot produce it (it drops comments)
and a round-tripping loader like ruamel is barred by the standard-library-only rule.
So values are injected into a hand-written template, and this module is the part that
has to be exactly right.

Strings are ALWAYS double-quoted, never bare and never single-quoted. A bare scalar
changes meaning based on its content (`yes` and `on` load as booleans, `2024` as an int,
a leading `#` starts a comment, a `:` splits a mapping), and single-quoted YAML has only
one escape (`''`) which does not cover backslashes or control characters. The
double-quoted form has a total escape grammar, so this is safe rather than lucky --
which `tests/test_onboard_emit.py` proves by loading every emission back with a real
YAML parser rather than by inspecting the string.
"""

# Double-quoted YAML understands JSON's escapes. `\` FIRST: escaping it after `"` would
# re-escape the backslashes this table itself introduces.
_ESCAPES = (
    ("\\", "\\\\"),
    ('"', '\\"'),
    ("\n", "\\n"),
    ("\r", "\\r"),
    ("\t", "\\t"),
)


def scalar(value) -> str:
    """One YAML scalar for `value`.

    `bool` is checked BEFORE `int` because `bool` subclasses it -- the same ordering
    trap as `lead_ttl_days`' validator (#75). Without it `True` emits as `1`, and a
    boolean config key silently becomes a number.
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
    """A flow sequence, e.g. `["a", "b"]`.

    Flow rather than block style so a value can be injected into a single template LINE,
    which keeps the surrounding comment attached to the key it explains.
    """
    return "[" + ", ".join(scalar(v) for v in values) + "]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py -q`
Expected: 31 passed

- [ ] **Step 5: Witness M5 — naive interpolation**

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
double-quoted: a bare scalar changes meaning with its content (yes/on load
as bools, 2024 as an int, a leading # starts a comment), and single-quoted
YAML cannot escape a backslash. Proved by loading every emission back with
a real parser over a nasty corpus, not by inspecting the string.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: The question catalogue

**Files:**
- Create: `sluice/onboard/questions.py`
- Test: `tests/test_onboard_questions.py`

**Interfaces:**
- Consumes: `sluice.core.vault.DEFAULT_VAULT`
- Produces:
  - `Question` frozen dataclass with fields `key: str`, `prompt: str`, `parse: Callable[[str], object]`, `writes_to: tuple[str, ...]`, `section: str`, `hint: str`, `default: object | None`
  - `catalogue() -> tuple[Question, ...]`
  - `BadAnswer(ValueError)`
  - parsers `parse_text`, `parse_csv`, `parse_int`, `parse_path`, `parse_choice(*allowed)`, `parse_url`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_questions.py
"""The catalogue is pure data. Its load-bearing property is that a BLANK answer is a
SKIP for every preference question -- a wizard that fills a gate because the user fumbled
a prompt is 672ad2a with a friendly face."""
import pytest

from sluice.core.vault import DEFAULT_VAULT
from sluice.onboard.questions import (BadAnswer, catalogue, parse_choice, parse_csv,
                                      parse_int, parse_path, parse_url)


def test_every_question_except_the_vault_skips_on_blank():
    for q in catalogue():
        if q.key == "vault_dir":
            continue
        assert q.default is None, f"{q.key} would fill a gate the user did not state"


def test_the_vault_question_is_the_one_exception_and_defaults_to_the_shared_constant():
    """The profile has to land somewhere, so this one cannot skip. It takes the SAME
    constant the store's own default uses -- a second literal would be drift."""
    vault = [q for q in catalogue() if q.key == "vault_dir"]
    assert len(vault) == 1
    assert vault[0].default == DEFAULT_VAULT


def test_parse_int_rejects_bool_words_before_parsing_a_number():
    """PyYAML resolves yes/on/true to True, and bool subclasses int -- so
    `lead_ttl_days: yes` would load as a ONE DAY ttl and mark every lead stale with
    nothing raising anywhere (#75)."""
    for word in ("yes", "no", "on", "off", "true", "false", "YES", "True"):
        with pytest.raises(BadAnswer):
            parse_int(word)
    assert parse_int("90") == 90
    assert parse_int(" 450 ") == 450
    with pytest.raises(BadAnswer):
        parse_int("ninety")
    with pytest.raises(BadAnswer):
        parse_int("-1")


def test_parse_csv_splits_and_strips_and_drops_empties():
    assert parse_csv("a, b ,,c") == ["a", "b", "c"]
    assert parse_csv("  ") == []


def test_parse_url_requires_an_http_scheme_and_never_resolves_dns():
    assert parse_url("https://example.invalid/jobs") == "https://example.invalid/jobs"
    assert parse_url("http://example.invalid/jobs") == "http://example.invalid/jobs"
    for bad in ("example.invalid/jobs", "ftp://example.invalid", "file:///etc/passwd", ""):
        with pytest.raises(BadAnswer):
            parse_url(bad)


def test_parse_path_expands_and_absolutises(tmp_path, monkeypatch):
    """A RELATIVE vault_dir is the 'second empty vault beside you' hazard README warns
    about, reintroduced by the wizard itself."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert parse_path("~/notes") == str(tmp_path / "notes")
    assert parse_path("./vault") == str(tmp_path / "vault")
    assert parse_path(str(tmp_path / "x")) == str(tmp_path / "x")


def test_parse_choice_lists_the_valid_names_on_a_bad_one():
    """Fail loudly at construction, naming the valid options -- this repo's rule."""
    p = parse_choice("script", "weasyprint")
    assert p("weasyprint") == "weasyprint"
    with pytest.raises(BadAnswer, match="script"):
        p("wkhtmltopdf")


def test_no_question_text_expresses_a_preference():
    """A new surface for the neutrality invariant: every prior leak risk was a VALUE,
    this one is a QUESTION. The wizard asks which titles you want; it must never propose
    a taxonomy of good jobs."""
    banned = ("startup", "scale-up", "enterprise", "remote-first", "fintech",
              "senior", "manager", "engineer")
    for q in catalogue():
        blob = f"{q.prompt} {q.hint}".lower()
        for word in banned:
            assert word not in blob, f"{q.key} proposes a taxonomy: {word!r}"


def test_the_catalogue_is_not_empty_and_keys_are_unique():
    """Scope: every assertion above quantifies over catalogue(), and all() over an empty
    sequence is True."""
    keys = [q.key for q in catalogue()]
    assert len(keys) >= 15
    assert len(keys) == len(set(keys))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_questions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.questions'`

- [ ] **Step 3: Write the catalogue**

```python
# sluice/onboard/questions.py
"""The question catalogue: pure data plus the parsers that validate an answer.

A BLANK answer is a SKIP, and a skipped question writes nothing. That is the
empty-config-abstains invariant expressed at the wizard: an unconfigured gate must pass
every lead through, and a wizard that fills a gate because someone fumbled a prompt bins
a stranger's job hunt exactly as `672ad2a` did.

The single exception is the vault, because the Judging Profile has to be written
somewhere and "skip" is not an answer to that. It takes the store's own default.

Nothing here proposes a taxonomy of good jobs. Every prior neutrality risk in this repo
was a shipped VALUE; a wizard adds a new surface, the shipped QUESTION, and a question
like "startup or enterprise?" would ship an opinion just as surely as a default would.
"""
import os
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from sluice.core.vault import DEFAULT_VAULT


class BadAnswer(ValueError):
    """An answer that cannot be used. On a TTY the asker re-asks; there is no way to
    reach it under `--no-input`, which supplies no answers at all."""


# PyYAML resolves all of these to a bool, and `bool` subclasses `int` -- so an int-typed
# key answered `yes` would load as 1 with nothing raising. `lead_ttl_days: yes` is the
# natural way to think you are turning staleness ON, and it would instead declare every
# lead stale after one day (#75).
_BOOL_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false"}


def parse_text(raw: str) -> str:
    return raw.strip()


def parse_csv(raw: str) -> list:
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_int(raw: str) -> int:
    text = raw.strip()
    if text.lower() in _BOOL_WORDS:
        raise BadAnswer(
            f"{text!r} is a yes/no word, and this key takes a number. YAML would load it "
            f"as true, which counts as 1 -- give a number, or leave it blank to skip.")
    try:
        value = int(text)
    except ValueError:
        raise BadAnswer(f"{text!r} is not a whole number.") from None
    if value < 0:
        raise BadAnswer("must not be negative (0 means the gate is off).")
    return value


def parse_path(raw: str) -> str:
    """Absolute, always. A relative `vault_dir` follows the working directory, which is
    how a user ends up with a second empty vault beside them instead of the one they
    meant (README's own warning) -- and the wizard would be the thing that wrote it."""
    return os.path.abspath(os.path.expanduser(raw.strip()))


def parse_choice(*allowed: str) -> Callable[[str], str]:
    def _parse(raw: str) -> str:
        text = raw.strip()
        if text not in allowed:
            raise BadAnswer(f"{text!r} is not one of: {', '.join(allowed)}")
        return text
    return _parse


def parse_url(raw: str) -> str:
    """Scheme and shape only. Deliberately NOT `core/urlguard.py`, which resolves DNS:
    that guard exists to stop the dossier fetcher reaching a private address, and using
    it here would make `init` non-hermetic and could hang behind a slow resolver while
    someone is setting up."""
    text = raw.strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadAnswer(f"{text!r} is not an http(s) URL.")
    return text


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    parse: Callable[[str], object]
    writes_to: tuple = ()
    section: str = ""
    hint: str = ""
    # None means "blank skips me". Only the vault question sets this.
    default: object = None
    # Printed by the post-write report when this key ends up set, so the user learns what
    # their config will DO rather than only what was written.
    consequence: str = ""


def catalogue() -> tuple:
    """Every question, in ask order.

    Ask order matters in one place: the coarse ingest gate is LAST. It is the most
    dangerous key in the file -- a keep-list drops every title that does not match, before
    dedup and before any LLM call -- so it is asked once the user has seen what the
    downstream gates already do, and its prompt states the consequence outright.
    """
    return (
        Question("vault_dir", "Where is your Obsidian vault?", parse_path,
                 ("vault_dir",), "Vault", default=DEFAULT_VAULT,
                 hint="Where sluice reads your judging criteria and writes lead notes.",
                 consequence="vault: {value}"),

        Question("cv_name", "What name should appear on a tailored CV?", parse_text,
                 ("cv.name",), "You"),
        Question("cv_contact", "Contact block for the CV (email, phone, links)?",
                 parse_text, ("cv.contact",), "You",
                 hint="One line; edit the config later for a multi-line block."),
        Question("cv_employers", "Employers on your CV, comma-separated?", parse_csv,
                 ("cv.employers",), "You",
                 hint="Used to check a composed CV only cites places you worked."),

        Question("accept_titles", "Which job titles do you want, comma-separated?",
                 parse_csv, ("triage.accept_titles",), "Want",
                 hint="Blank leaves the gate open: every lead reaches the judge.",
                 consequence="accept only titles matching: {value}"),
        Question("reject_titles", "Which titles disqualify a role, comma-separated?",
                 parse_csv, ("triage.reject_titles",), "Want",
                 consequence="reject titles matching: {value}"),
        Question("target_locations", "Where are you willing to work, comma-separated?",
                 parse_csv, ("triage.target_locations",), "Want",
                 hint="Blank means no geographic gate at all.",
                 consequence="require a location matching: {value}"),
        Question("reject_companies", "Any companies to skip, comma-separated?",
                 parse_csv, ("triage.reject_companies",), "Want",
                 consequence="always skip: {value}"),
        Question("contract_floor", "Minimum day rate for contract work (GBP)?",
                 parse_int, ("triage.contract_floor_gbp_day",), "Want",
                 hint="0 or blank means no floor.",
                 consequence="drop contract roles under GBP {value}/day"),
        Question("perm_floor", "Minimum salary for permanent work (GBP)?",
                 parse_int, ("triage.perm_floor_gbp",), "Want",
                 hint="0 or blank means no floor.",
                 consequence="drop permanent roles under GBP {value}"),
        Question("lead_ttl_days", "Drop leads not seen in a scrape for how many days?",
                 parse_int, ("lead_ttl_days",), "Want",
                 hint="0 or blank turns staleness off, which is the shipped default.",
                 consequence="treat leads unseen for {value} days as stale"),

        Question("relevance_keep",
                 "Keep only titles containing these words, comma-separated?",
                 parse_csv, ("relevance_keep",), "Cost",
                 hint="A cheap filter at scrape time, BEFORE anything else runs. "
                      "Anything not matching is discarded and never judged. "
                      "Leave blank unless you want that.",
                 consequence="keep ONLY titles containing: {value} "
                             "(everything else dropped before triage)"),
        Question("relevance_drop", "Discard titles containing these words?",
                 parse_csv, ("relevance_drop",), "Cost",
                 consequence="discard titles containing: {value}"),

        Question("primary_backend", "Primary LLM backend?",
                 parse_choice("anthropic", "openai", "claude-max", "deepseek"),
                 ("triage.primary_backend", "cv.primary_backend",
                  "track.primary_backend"), "Providers",
                 hint="Set once; written into all three blocks that take one."),
        Question("fallback_backend", "Fallback LLM backend?",
                 parse_choice("anthropic", "openai", "claude-max", "deepseek"),
                 ("triage.fallback_backend", "cv.fallback_backend",
                  "track.fallback_backend"), "Providers"),
        Question("renderer", "CV renderer -- script or weasyprint?",
                 parse_choice("script", "weasyprint"), ("cv.renderer",), "Providers",
                 hint="weasyprint is bundled: pip install 'sluice[render]'."),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_onboard_questions.py -q`
Expected: 9 passed

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/questions.py tests/test_onboard_questions.py
git commit -m "feat(onboard): the question catalogue, blank-means-skip (#8)

Every preference question defaults to skip; the vault is the one exception,
because the profile has to land somewhere, and it takes the store's own
DEFAULT_VAULT rather than a second literal.

parse_int rejects yes/on/true BEFORE parsing a number: PyYAML resolves them
to True and bool subclasses int, so \`lead_ttl_days: yes\` would load as a
one-day TTL and mark every lead stale with nothing raising (#75). parse_url
is a scheme check, deliberately not core/urlguard -- that resolves DNS and
would make init non-hermetic.

A wizard adds a new neutrality surface: the shipped QUESTION. Guarded.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: `build_plan` — the pure planner

**Files:**
- Create: `sluice/onboard/plan.py`
- Test: `tests/test_onboard_plan.py`

**Interfaces:**
- Consumes: `emit.scalar`, `emit.flow_list`, `questions.catalogue`, `sluice.core.protocols.CRITERIA_RELPATH`
- Produces:
  - `InitPlan` frozen dataclass: `config_dest: str`, `config_text: str`, `profile_dest: str`, `profile_text: str`, `notes: tuple[str, ...]`
  - `build_plan(answers: dict, *, config_dest: str, profile_dest: str, profile_answers: dict | None = None) -> InitPlan`
  - `PROFILE_HEADINGS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_plan.py
"""`build_plan` is a pure function from a dict to two strings, which is what lets the
load-bearing property be a unit test instead of a wizard transcript."""
import dataclasses
import glob
import importlib
import os
import re

import yaml

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.onboard.plan import PROFILE_HEADINGS, build_plan
from sluice.onboard.questions import catalogue
from sluice.track.config import load_track_config
from sluice.triage.config import load_triage_config
from sluice.triage.prompt import _DEFAULT_CRITERIA


def _plan(answers=None, tmp_path=None, **kw):
    return build_plan(answers or {},
                      config_dest=str((tmp_path or "/nowhere") and
                                      os.path.join(str(tmp_path), "config.yaml")),
                      profile_dest=os.path.join(str(tmp_path), "Profile.md"), **kw)


def _write_and_load(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ── the paired property: neutrality AND scope ────────────────────────────────
def test_enter_through_writes_a_config_that_overrides_nothing(tmp_path):
    """Loaded through every loader, an unanswered wizard leaves every gate abstaining."""
    path = _write_and_load(tmp_path, _plan(tmp_path=tmp_path).config_text)

    root = load_config(path)
    assert root.relevance_keep == []
    assert root.relevance_drop == []
    assert root.lead_ttl_days == 0
    assert root.dossier_allow_hosts == []

    tri = load_triage_config(path)
    assert tri.accept_titles == []
    assert tri.reject_titles == []
    assert tri.target_locations == []
    assert tri.reject_companies == []
    assert tri.contract_floor_gbp_day == 0
    assert tri.perm_floor_gbp == 0

    cv = load_cv_config(path)
    assert cv.employers == []
    assert cv.require_signoff is True

    trk = load_track_config(path)
    assert trk.seen_db == ""


def test_the_template_actually_contains_every_catalogue_key(tmp_path):
    """SCOPE, and it is not optional. The assertion above passes just as happily on an
    EMPTY file -- the loaders would return the (neutral) code defaults and every gate
    would abstain for entirely the wrong reason. That is the all([]) shape that has
    already shipped three times in this repo. Neutrality proves the template overrides
    nothing; this proves there IS a template."""
    text = _plan(tmp_path=tmp_path).config_text
    for q in catalogue():
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#?\s*{re.escape(leaf)}:", text, re.M), \
                f"{dotted} is absent from the template init writes"


def test_answers_become_active_keys(tmp_path):
    answers = {"accept_titles": ["example role"], "perm_floor": 90000,
               "lead_ttl_days": 90, "primary_backend": "openai"}
    path = _write_and_load(tmp_path, _plan(answers, tmp_path=tmp_path).config_text)

    assert load_triage_config(path).accept_titles == ["example role"]
    assert load_triage_config(path).perm_floor_gbp == 90000
    assert load_config(path).lead_ttl_days == 90


def test_one_backend_answer_fans_out_to_every_config_that_takes_one(tmp_path):
    path = _write_and_load(
        tmp_path, _plan({"primary_backend": "openai",
                         "fallback_backend": "anthropic"}, tmp_path=tmp_path).config_text)
    assert load_triage_config(path).primary_backend == "openai"
    assert load_cv_config(path).primary_backend == "openai"
    assert load_track_config(path).primary_backend == "openai"
    assert load_triage_config(path).fallback_backend == "anthropic"
    assert load_cv_config(path).fallback_backend == "anthropic"
    assert load_track_config(path).fallback_backend == "anthropic"


def test_the_fan_out_covers_every_config_dataclass_that_declares_a_backend():
    """DISCOVERED, not hand-listed. #63's lesson: a hand-list of dataclasses leaks
    exactly like the hand-list of fields it replaced -- four were named there and there
    were six. Narrowing `writes_to` must redden here."""
    declared = set()
    for module_path in sorted(glob.glob("sluice/**/config.py", recursive=True)):
        name = module_path[:-3].replace(os.sep, ".")
        mod = importlib.import_module(name)
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (dataclasses.is_dataclass(obj)
                    and getattr(obj, "__module__", "") == name
                    and attr.endswith("Config")):
                fields = {f.name for f in dataclasses.fields(obj)}
                if "primary_backend" in fields:
                    declared.add(name.split(".")[1])

    assert declared, "the sweep found no config declaring primary_backend"
    q = {x.key: x for x in catalogue()}["primary_backend"]
    covered = {d.split(".")[0] for d in q.writes_to}
    assert covered == declared


def test_every_emitted_key_is_documented_in_the_example_config():
    """The example file stays the annotated catalogue, so a key the wizard can write and
    the catalogue does not explain is a documentation hole with a CLI attached."""
    example = open("sluice.yaml.example", encoding="utf-8").read()
    for q in catalogue():
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#?\s*{re.escape(leaf)}:", example, re.M), \
                f"{dotted} is written by init but undocumented in sluice.yaml.example"


def test_no_answer_can_emit_a_scalar_that_loads_as_a_bool_where_an_int_is_meant(tmp_path):
    text = _plan({"lead_ttl_days": 1, "perm_floor": 1}, tmp_path=tmp_path).config_text
    data = yaml.safe_load(text)
    assert data["lead_ttl_days"] is not True
    assert isinstance(data["lead_ttl_days"], int)
    assert isinstance(data["triage"]["perm_floor_gbp"], int)


def test_the_emitted_config_is_valid_yaml_even_with_nasty_answers(tmp_path):
    answers = {"cv_name": 'O\'Brien: "the #1"', "cv_employers": ["Example: Foundry"],
               "accept_titles": ["yes", "#hash", "back\\slash"]}
    path = _write_and_load(tmp_path, _plan(answers, tmp_path=tmp_path).config_text)
    assert load_cv_config(path).name == 'O\'Brien: "the #1"'
    assert load_triage_config(path).accept_titles == ["yes", "#hash", "back\\slash"]


# ── the profile ──────────────────────────────────────────────────────────────
def test_the_profile_headings_match_the_judge_scaffold_exactly(tmp_path):
    """The judge reads this as PROSE; nothing parses the headings. So a drift between
    what init writes and what the scaffold's Final Reminders refer to errors NOWHERE --
    the judge just quietly gets a profile organised around headings its own instructions
    do not mention. #30's _CITE_RE precedent: share the source, assert equality."""
    scaffold = set(re.findall(r"^#{2,3} .+$", _DEFAULT_CRITERIA, re.M))
    written = set(re.findall(r"^#{2,3} .+$", _plan(tmp_path=tmp_path).profile_text, re.M))
    assert written == scaffold
    # SCOPE: without this, `set() == set()` passes if the regex ever stops matching on
    # both sides -- the same vacuity one level up.
    assert scaffold == set(PROFILE_HEADINGS)
    assert len(PROFILE_HEADINGS) == 5


def test_an_unanswered_profile_is_the_scaffold_with_prompts(tmp_path):
    text = _plan(tmp_path=tmp_path).profile_text
    assert "<!--" in text
    for heading in PROFILE_HEADINGS:
        assert heading in text


def test_profile_answers_are_written_under_their_headings(tmp_path):
    plan = _plan(tmp_path=tmp_path,
                 profile_answers={"target_shape": "Example target shape."})
    assert "Example target shape." in plan.profile_text
    assert "### Target and wrong shape" in plan.profile_text


def test_the_profile_carries_no_frontmatter(tmp_path):
    """`_strip_frontmatter` would drop it, so emitting any would be writing something the
    judge never sees."""
    assert not _plan(tmp_path=tmp_path).profile_text.startswith("---")


# ── the report ───────────────────────────────────────────────────────────────
def test_notes_explain_what_a_configured_gate_will_do(tmp_path):
    notes = "\n".join(_plan({"relevance_keep": ["example role"]},
                            tmp_path=tmp_path).notes)
    assert "example role" in notes
    assert "dropped before triage" in notes


def test_an_unanswered_run_reports_no_gates(tmp_path):
    assert not any("keep ONLY" in n for n in _plan(tmp_path=tmp_path).notes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.plan'`

- [ ] **Step 3: Write the planner**

```python
# sluice/onboard/plan.py
"""Pure planning: answers in, two artefact texts out. No I/O, no prompts, no clock.

That purity is the point. The property this feature lives or dies by -- a run that
answers nothing produces a config that expresses nothing -- is then a table test over a
dict rather than something you can only observe by driving a wizard and reading files
back. The impure half (`ask`) supplies the dict; `cli.cmd_init` supplies the destinations
and does the writing.

The config is RENDERED FROM THE CATALOGUE rather than being a static template with
substitution holes. That makes "every key the wizard can write appears in the file it
writes" true by construction instead of by review.
"""
import re
from dataclasses import dataclass, field

from sluice.onboard.emit import flow_list, scalar
from sluice.onboard.questions import catalogue

# The judge's own headings. Duplicated here as literals rather than derived from
# `_DEFAULT_CRITERIA`, because deriving prose from prose would make both unreadable --
# so `tests/test_onboard_plan.py` extracts from BOTH sides and asserts equality instead,
# the way #30 pinned the citation regex it had to match. A comment claiming they agree
# would not be a check.
PROFILE_HEADINGS = (
    "## Who this candidate is",
    "### Target and wrong shape",
    "### Background grounding",
    "## Win patterns and anti-patterns",
    "## Industry filter (judgement-based, not categorical)",
)

# heading -> (answer key, the prompt left in place when it is unanswered). The prompts
# ask what the judge needs to know and propose no answer: a wizard that suggested "a
# startup, or an enterprise?" would ship an opinion exactly as a default would.
_PROFILE_PROMPTS = {
    "## Who this candidate is": (
        "who", "Your background and seniority, and what you are optimising this search\n"
               "for. The judge treats this as authoritative for who you are."),
    "### Target and wrong shape": (
        "target_shape", "The shape of role you want, and the shape that is wrong.\n"
                        "Name the scope and level, not job titles."),
    "### Background grounding": (
        "grounding", "Employment history the judge should assume you already satisfy,\n"
                     "so it stops raising them as concerns."),
    "## Win patterns and anti-patterns": (
        "patterns", "Phrases in a job ad that attract you, and phrases that repel you.\n"
                    "Quote the wording you actually see."),
    "## Industry filter (judgement-based, not categorical)": (
        "industry", "Sectors you will and will not work in, and why. Leave blank if you\n"
                    "have no sector view -- the judge then does not filter on one."),
}

_SECTION_BLURB = {
    "Vault": "Where your notes live.",
    "You": "Identity used when composing a tailored CV.",
    "Want": "What you are looking for. EVERY key here is optional, and an unset gate\n"
            "passes every lead through rather than filtering it out.",
    "Cost": "Cheap filters applied at scrape time, before anything expensive runs.",
    "Providers": "Which model fills each role. API keys come from the environment,\n"
                 "never this file.",
}

_HEADER = """\
# sluice configuration, written by `sluice init`.
#
# Every key is optional and falls back to a code default. A COMMENTED key is unset, and
# an unset preference gate abstains -- it passes every lead through rather than filtering
# on a value you did not choose. Uncomment a key to turn that gate on.
#
# This file holds personal material, so keep it out of any public repo. Secrets (API
# keys, private hostnames) belong in the environment, not here.
#
# `sluice.yaml.example` in the repo documents every knob, including the ones this wizard
# does not ask about.
"""


@dataclass(frozen=True)
class InitPlan:
    config_dest: str
    config_text: str
    profile_dest: str
    profile_text: str
    notes: tuple = ()


def _blocks(answers):
    """Group every catalogue key by its top-level YAML block, preserving ask order.

    A question can write more than one block (`primary_backend` writes three), so this
    walks `writes_to` rather than the question.
    """
    grouped = {}
    for q in catalogue():
        for dotted in q.writes_to:
            parts = dotted.split(".")
            block = parts[0] if len(parts) > 1 else ""
            leaf = parts[-1]
            grouped.setdefault(block, []).append((leaf, q, answers.get(q.key)))
    return grouped


def _render_value(value):
    return flow_list(value) if isinstance(value, list) else scalar(value)


def _render_key(leaf, q, value, indent):
    out = []
    if q.hint:
        for line in q.hint.split("\n"):
            out.append(f"{indent}# {line}")
    if value is None or value == [] or value == "":
        # Commented, because an unset key is how a gate abstains. The `<- uncomment`
        # marker matches the convention `sluice.yaml.example` already uses.
        out.append(f"{indent}# {leaf}:   # <- uncomment and set YOUR OWN")
    else:
        out.append(f"{indent}{leaf}: {_render_value(value)}")
    return out


def _render_config(answers, sources):
    lines = [_HEADER]
    grouped = _blocks(answers)

    # Root keys first, then each sub-app block, so the file reads top-down like the
    # example does.
    for block in [""] + [b for b in grouped if b]:
        entries = grouped.get(block, [])
        if not entries:
            continue
        sections_seen = set()
        body = []
        for leaf, q, value in entries:
            if q.section and q.section not in sections_seen:
                sections_seen.add(q.section)
                body.append("")
                body.append(f"# -- {q.section} " + "-" * max(0, 60 - len(q.section)))
                for line in _SECTION_BLURB.get(q.section, "").split("\n"):
                    if line:
                        body.append(f"# {line}")
            body.extend(_render_key(leaf, q, value, "  " if block else ""))
        if block:
            # The block header is commented when EVERY key under it is unset. A bare
            # `triage:` with only comments beneath it parses as `{'triage': None}`, and
            # relying on each loader to treat that as an empty mapping is a coupling
            # nobody asked for.
            active = any(v not in (None, [], "") for _, _, v in entries)
            lines.append("")
            lines.append(f"{block}:" if active else f"# {block}:")
            lines.extend(body if active else [f"# {ln}" if ln else "#" for ln in body])
        else:
            lines.extend(body)

    lines.extend(_render_sources(sources))
    return "\n".join(lines).rstrip() + "\n"


def _render_sources(sources):
    """`sources:` is shaped differently from every other block -- a mapping keyed by
    source id -- so it renders separately rather than being forced into `_blocks`."""
    out = ["", "# -- Sources " + "-" * 49,
           "# Which boards to scrape, and the searches to run on each. A source with no",
           "# `searches` override runs its own neutral example search."]
    if not sources:
        out.append("# sources:")
        out.append("#   example_source:")
        out.append("#     searches:")
        out.append('#       - ["Example search", "https://example.invalid/jobs"]')
        return out
    out.append("sources:")
    for sid in sorted(sources):
        spec = sources[sid]
        out.append(f"  {sid}:")
        out.append(f"    enabled: {scalar(bool(spec.get('enabled', True)))}")
        searches = spec.get("searches") or []
        if searches:
            out.append("    searches:")
            for label, url in searches:
                out.append(f"      - [{scalar(label)}, {scalar(url)}]")
    return out


def _render_profile(profile_answers):
    """Every heading always present, answered or not.

    An unanswered heading keeps its prompt as an HTML comment, so the file is a
    documented contract with the judge rather than a guessing game -- which is the part
    of #8 that the config half cannot deliver, since `sluice.yaml.example` already
    documents the config and nothing documents this.

    No frontmatter: `_strip_frontmatter` drops a leading `---` block before the judge
    ever sees it, so emitting one would be writing something guaranteed to be ignored.
    """
    out = ["# Judging Profile",
           "",
           "The criteria sluice judges every lead against. Edit it in Obsidian whenever",
           "your search changes; the next run picks it up with no code change.",
           "",
           "Nothing here is shipped by sluice -- these are your words, and they are the",
           "only place your preferences live.",
           ""]
    for heading in PROFILE_HEADINGS:
        key, prompt = _PROFILE_PROMPTS[heading]
        out.append(heading)
        answer = (profile_answers or {}).get(key)
        if answer:
            out.append("")
            out.append(answer.strip())
        else:
            out.append("")
            out.append("<!--")
            for line in prompt.split("\n"):
                out.append(line)
            out.append("-->")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _notes(answers, sources):
    """What the config will DO, in plain terms.

    Written because the shipped `sluice.yaml.example` once handed every copier an active
    `relevance_keep` that discarded every title but one, and nothing anywhere said so.
    A user should not have to read YAML to find out their gate is closed.
    """
    out = []
    for q in catalogue():
        value = answers.get(q.key)
        if value in (None, [], "", 0) or not q.consequence:
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        out.append(q.consequence.format(value=shown))
    if sources:
        enabled = [s for s, spec in sources.items() if spec.get("enabled", True)]
        out.append(f"scrape {len(enabled)} board(s): {', '.join(sorted(enabled))}")
    return tuple(out)


def build_plan(answers, *, config_dest, profile_dest,
               profile_answers=None, sources=None) -> InitPlan:
    """The two artefacts `sluice init` writes, as text.

    `answers` holds only the questions the user actually answered -- a skipped question
    is ABSENT, never present-and-empty, so there is no way for a blank to be mistaken for
    a deliberate empty list downstream.
    """
    sources = sources or {}
    return InitPlan(
        config_dest=config_dest,
        config_text=_render_config(answers, sources),
        profile_dest=profile_dest,
        profile_text=_render_profile(profile_answers),
        notes=_notes(answers, sources),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_onboard_plan.py -q`
Expected: 15 passed

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 6: Witness M1, M3 and M4 — one site at a time**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/questions.py /tmp/questions.py.bak
cp sluice/onboard/plan.py /tmp/plan.py.bak

# M1: a preference question fills a gate. MOVE the default onto accept_titles.
#     (edit questions.py: `default=["example role"]` on the accept_titles Question)
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_every_question_except_the_vault_skips_on_blank" -v
# Expected: FAIL
cp /tmp/questions.py.bak sluice/onboard/questions.py

# M3: change ONE heading in plan.py's PROFILE_HEADINGS
#     ("### Background grounding" -> "### Background")
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_the_profile_headings_match_the_judge_scaffold_exactly" -v
# Expected: FAIL
cp /tmp/plan.py.bak sluice/onboard/plan.py

# M4: DELETE one fan-out destination ("track.primary_backend" from writes_to)
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_the_fan_out_covers_every_config_dataclass_that_declares_a_backend" -v
# Expected: FAIL
cp /tmp/questions.py.bak sluice/onboard/questions.py

# The scope half must be independently live: DELETE the catalogue loop body in
# _render_config so the template emits only the header.
.venv/bin/python -m pytest "tests/test_onboard_plan.py::test_the_template_actually_contains_every_catalogue_key" -v
# Expected: FAIL -- and note the neutrality test STAYS GREEN, which is exactly why the
# pair is needed.
cp /tmp/plan.py.bak sluice/onboard/plan.py

git diff --stat   # MUST be empty
```

- [ ] **Step 7: Commit**

```bash
git add sluice/onboard/plan.py tests/test_onboard_plan.py
git commit -m "feat(onboard): pure build_plan, config rendered from the catalogue (#8)

Rendering from the catalogue rather than substituting into a static
template makes 'every key the wizard can write appears in the file it
writes' true by construction.

Two paired guards, because either alone is vacuous: the neutrality half
(load the emitted config through all five loaders, every gate abstains)
passes just as happily on an EMPTY file, since the loaders would then
return the neutral code defaults. The scope half pins that a template
exists. Witnessed: deleting the render loop reddens scope and leaves
neutrality green.

Profile headings are pinned against the judge scaffold by extracting from
BOTH sides (#30's _CITE_RE precedent) -- the judge reads prose and parses
no headings, so a drift would error nowhere.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: The asker — TTY, `$EDITOR`, and the non-TTY refusal

**Files:**
- Create: `sluice/onboard/ask.py`
- Test: `tests/test_onboard_ask.py`

**Interfaces:**
- Consumes: `questions.Question`, `questions.BadAnswer`, `questions.catalogue`
- Produces:
  - `MissingAnswer(RuntimeError)`
  - `NoInputAsker` — `.ask(q) -> object | None`, returns the preset or `None`; raises `MissingAnswer` for a question with a preset requirement it cannot satisfy
  - `TtyAsker(stdin, stdout, editor=None)` — same interface, prompts
  - `collect(asker, questions) -> dict` and `collect_profile(asker) -> dict`
  - `edit_in_editor(prompt: str, *, editor: str | None, run=subprocess.call) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboard_ask.py
"""The asker is the only impure half. Its load-bearing property is that the TTY path and
the --no-input path CONVERGE: the wizard is friendlier, not different."""
import io

import pytest

from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect,
                                collect_profile, edit_in_editor)
from sluice.onboard.questions import catalogue


def _tty(answers_text, editor=None):
    return TtyAsker(stdin=io.StringIO(answers_text), stdout=io.StringIO(), editor=editor)


def test_no_input_answers_nothing_and_that_is_the_enter_through_run():
    got = collect(NoInputAsker(presets={"vault_dir": "/tmp/v"}), catalogue())
    assert got == {"vault_dir": "/tmp/v"}


def test_no_input_without_the_vault_refuses_rather_than_hanging():
    """Never block on a pipe. A wizard that waits for stdin in CI is a hung job with no
    diagnosis."""
    with pytest.raises(MissingAnswer, match="--vault"):
        collect(NoInputAsker(presets={}), catalogue())


def test_blank_answers_on_a_tty_skip_every_preference_question():
    """Enter pressed through the whole wizard. The vault takes its default; nothing else
    is set -- so this run and `--no-input` produce the SAME answers."""
    blanks = "\n" * (len(catalogue()) + 4)
    got = collect(_tty(blanks), catalogue())
    assert set(got) == {"vault_dir"}


def test_the_tty_path_and_no_input_converge_on_the_same_answers():
    """The anti-drift property, stated as a test rather than promised in a comment: a
    TTY/flags split has two code paths, and the flag path is the one tests usually cover,
    so the prompt path is where drift hides."""
    blanks = "\n" * (len(catalogue()) + 4)
    tty = collect(_tty(blanks), catalogue())
    flags = collect(NoInputAsker(presets={"vault_dir": tty["vault_dir"]}), catalogue())
    assert tty == flags


def test_answers_are_parsed_not_stored_raw():
    text = "\n".join(["/tmp/v", "", "", "", "example role, other role", "", "", "",
                      "450"] + [""] * len(catalogue()))
    got = collect(_tty(text), catalogue())
    assert got["accept_titles"] == ["example role", "other role"]
    assert got["contract_floor"] == 450
    assert isinstance(got["contract_floor"], int)


def test_a_bad_answer_is_re_asked_on_a_tty():
    text = "\n".join(["/tmp/v", "", "", "", "", "", "", "", "yes", "450"]
                     + [""] * len(catalogue()))
    asker = _tty(text)
    got = collect(asker, catalogue())
    assert got["contract_floor"] == 450
    assert "number" in asker.stdout.getvalue()


def test_editor_content_is_returned_when_the_editor_succeeds(tmp_path):
    def fake_run(argv):
        path = argv[-1]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Example prose the user typed.\n")
        return 0
    assert edit_in_editor("prompt", editor="vi", run=fake_run) == \
        "Example prose the user typed."


def test_an_editor_that_fails_or_changes_nothing_falls_back_to_the_scaffold():
    assert edit_in_editor("prompt", editor="vi", run=lambda argv: 1) is None
    assert edit_in_editor("prompt", editor="vi", run=lambda argv: 0) is None
    assert edit_in_editor("prompt", editor=None, run=lambda argv: 0) is None


def test_editor_command_is_split_not_shelled():
    """`EDITOR='code --wait'` must work, and nothing from the environment may reach a
    shell."""
    seen = {}

    def fake_run(argv):
        seen["argv"] = argv
        return 1
    edit_in_editor("prompt", editor="code --wait", run=fake_run)
    assert seen["argv"][:2] == ["code", "--wait"]


def test_collect_profile_returns_only_answered_headings():
    text = "\n".join(["Example background.", "", "", "", ""])
    got = collect_profile(_tty(text))
    assert got["who"] == "Example background."
    assert "target_shape" not in got
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_onboard_ask.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.onboard.ask'`

- [ ] **Step 3: Write the asker**

```python
# sluice/onboard/ask.py
"""Every prompt, every terminal read, and the one subprocess call.

Two askers, and the property that keeps them honest is that they CONVERGE: pressing
Enter through the whole wizard produces exactly the answers `--no-input` produces. A
TTY/flags split otherwise has two code paths, and since the flag path is the one tests
naturally cover, the prompt path is precisely where drift hides. So blank input skips a
question in the TTY asker for the same reason `NoInputAsker` never answers one: they are
the same rule, not two rules that happen to agree today.

Nothing here refuses to answer by BLOCKING. A wizard that waits on stdin because it was
run from a pipe is a hung CI job with no diagnosis, so a missing required answer raises
and names the flag that supplies it.
"""
import os
import shlex
import subprocess
import tempfile

from sluice.onboard.questions import BadAnswer, Question

_PROFILE_ORDER = ("who", "target_shape", "grounding", "patterns", "industry")

_PROFILE_QUESTIONS = (
    ("who", "In a sentence or two: your background and seniority?"),
    ("target_shape", "The shape of role you want, and the shape that is wrong?"),
    ("grounding", "What should the judge assume you already satisfy?"),
    ("patterns", "Wording in a job ad that attracts you, and wording that repels you?"),
    ("industry", "Sectors you will or will not work in?"),
)


class MissingAnswer(RuntimeError):
    """A required answer is unavailable and cannot be asked for."""


class NoInputAsker:
    """Answers from flags only. Anything not preset is skipped."""

    def __init__(self, presets=None):
        self.presets = dict(presets or {})

    def ask(self, q: Question):
        if q.key in self.presets:
            return self.presets[q.key]
        if q.default is not None and q.key != "vault_dir":
            return q.default
        if q.key == "vault_dir":
            # The one answer with no safe fallback here. On a TTY the default is OFFERED
            # and the user can see and reject it; with no terminal, silently creating a
            # vault in whatever directory the command happened to run from is how someone
            # ends up with an empty vault beside the one they meant.
            raise MissingAnswer(
                "I do not know where your vault is, and there is no terminal to ask. "
                "Pass --vault DIR (or set VAULT_DIR, or vault_dir in the config).")
        return None

    def ask_text(self, prompt: str):
        return None


class TtyAsker:
    """Prompts, re-asking on a bad answer. Blank takes the question's default, which is
    `None` -- a skip -- for every question but the vault."""

    def __init__(self, stdin, stdout, editor=None):
        self.stdin = stdin
        self.stdout = stdout
        self.editor = editor

    def _write(self, text):
        self.stdout.write(text)

    def _readline(self):
        line = self.stdin.readline()
        if line == "":          # EOF: stop asking rather than spinning forever
            raise EOFError
        return line.rstrip("\n")

    def ask(self, q: Question):
        if q.hint:
            self._write(f"\n  {q.hint}\n")
        while True:
            suffix = f" [{q.default}]" if q.default is not None else " [skip]"
            self._write(f"{q.prompt}{suffix} ")
            try:
                raw = self._readline()
            except EOFError:
                return q.default
            if not raw.strip():
                return q.default
            try:
                return q.parse(raw)
            except BadAnswer as exc:
                self._write(f"  {exc}\n")

    def ask_text(self, prompt: str):
        self._write(f"{prompt} [skip, or Enter to use $EDITOR] ")
        try:
            raw = self._readline()
        except EOFError:
            return None
        if raw.strip():
            return raw.strip()
        return edit_in_editor(prompt, editor=self.editor)


def edit_in_editor(prompt, *, editor=None, run=subprocess.call):
    """Open `$EDITOR` on a scratch file pre-filled with `prompt`; return what came back,
    or None.

    None on every failure mode -- no editor configured, editor not found, non-zero exit,
    file unchanged -- so the profile is never WORSE than the scaffold and the wizard can
    never fail because of an editor.

    `shlex.split`, never `shell=True`: `EDITOR='code --wait'` is ordinary, and passing an
    environment variable to a shell is not.
    """
    if not editor:
        return None
    argv = shlex.split(editor)
    if not argv:
        return None
    marker = "\n".join(f"# {line}" for line in prompt.split("\n"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sluice-profile.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(marker + "\n\n")
        before = open(path, encoding="utf-8").read()
        try:
            code = run(argv + [path])
        except OSError:
            return None
        if code != 0:
            return None
        after = open(path, encoding="utf-8").read()
        if after == before:
            return None
        body = "\n".join(line for line in after.splitlines()
                         if not line.startswith("#")).strip()
        return body or None


def collect(asker, questions) -> dict:
    """Ask every question; return only the ones that produced an answer.

    A skipped question is ABSENT from the result, never present-and-empty. `build_plan`
    relies on that: `{}` has to mean "nothing was chosen", and an empty list that reached
    it as a real answer would be indistinguishable from a deliberate one.
    """
    out = {}
    for q in questions:
        value = asker.ask(q)
        if value is None or value == "" or value == []:
            continue
        out[q.key] = value
    return out


def collect_profile(asker) -> dict:
    out = {}
    for key, prompt in _PROFILE_QUESTIONS:
        value = asker.ask_text(prompt)
        if value:
            out[key] = value
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_onboard_ask.py -q`
Expected: 11 passed

- [ ] **Step 5: Witness M6 — delete the non-TTY refusal**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/ask.py /tmp/ask.py.bak
# DELETE the `raise MissingAnswer(...)` arm in NoInputAsker.ask, leaving `return None`
.venv/bin/python -m pytest "tests/test_onboard_ask.py::test_no_input_without_the_vault_refuses_rather_than_hanging" -v
# Expected: FAIL
cp /tmp/ask.py.bak sluice/onboard/ask.py
git diff --stat   # MUST be empty
```

- [ ] **Step 6: Commit**

```bash
git add sluice/onboard/ask.py tests/test_onboard_ask.py
git commit -m "feat(onboard): TTY asker, \$EDITOR prose, non-TTY refusal (#8)

The TTY path and --no-input CONVERGE by construction: blank skips for the
same reason NoInputAsker never answers, so pressing Enter through the
wizard produces byte-identical answers to the flag path. Pinned by a test,
because the flag path is the one tests naturally cover and the prompt path
is where a TTY/flags split hides its drift.

A missing required answer RAISES naming --vault rather than blocking on
stdin: a wizard that waits on a pipe is a hung CI job with no diagnosis.
\$EDITOR failures all degrade to the scaffold, so the profile is never
worse than not asking, and shlex.split keeps 'code --wait' working without
handing an env var to a shell.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: `cmd_init`, CLI wiring, and the docs it makes true

**Files:**
- Modify: `sluice/cli.py` (new `cmd_init` above `_build_parser`; parser entry beside `doctor`), `README.md:77-113`, `docs/ARCHITECTURE.md`
- Test: `tests/functional/test_init.py`

**Interfaces:**
- Consumes: `build_plan`, `catalogue`, `collect`, `collect_profile`, `NoInputAsker`, `TtyAsker`, `MissingAnswer`, `CRITERIA_RELPATH`, `paths.config_file`
- Produces: `cmd_init(args, config, *, asker=None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/functional/test_init.py
"""`sluice init` through the real `main(argv)`. The functional tier is where the refusals
and the never-clobber actually have to hold."""
import os

import pytest

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import Vault


def _config_path(tmp_path):
    return tmp_path / "xdg" / "config" / "sluice" / "config.yaml"


def test_init_writes_both_artefacts(cli, tmp_path):
    _harness, run = cli()
    vault = tmp_path / "notes"
    rc, out, _err = run(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert _config_path(tmp_path).exists()
    assert (vault / CRITERIA_RELPATH).exists()
    assert "wrote" in out


def test_the_profile_lands_where_the_judge_reads_it(cli, tmp_path):
    """Asserted by CALLING read_criteria, not by checking a path -- that is what proves
    init wrote where the judge looks rather than merely somewhere plausible."""
    _harness, run = cli()
    vault = tmp_path / "notes"
    run(["init", "--vault", str(vault), "--no-input"])
    assert "Judging Profile" in Vault(str(vault)).read_criteria()


def test_a_re_run_clobbers_nothing_and_exits_zero(cli, tmp_path):
    _harness, run = cli()
    vault = tmp_path / "notes"
    run(["init", "--vault", str(vault), "--no-input"])

    (vault / CRITERIA_RELPATH).write_text("MY REAL CRITERIA", encoding="utf-8")
    cfg_before = _config_path(tmp_path).read_text(encoding="utf-8")

    rc, out, _err = run(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert (vault / CRITERIA_RELPATH).read_text(encoding="utf-8") == "MY REAL CRITERIA"
    assert _config_path(tmp_path).read_text(encoding="utf-8") == cfg_before
    assert "exists" in out


def test_no_vault_and_no_terminal_refuses_writing_nothing(cli, tmp_path, monkeypatch):
    """NB the autouse `_pin_paths` fixture SETS VAULT_DIR, so without this delenv the
    test would pass for entirely the wrong reason -- init would find a vault in the
    environment and never reach the refusal."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    _harness, run = cli()
    rc, _out, err = run(["init", "--no-input"])
    assert rc == 2
    assert "--vault" in err
    assert not _config_path(tmp_path).exists()


def test_an_existing_config_is_kept_and_the_profile_still_scaffolds(cli, tmp_path):
    _harness, run = cli()
    cfg = _config_path(tmp_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("# hand written\n", encoding="utf-8")
    vault = tmp_path / "notes"

    rc, out, _err = run(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert cfg.read_text(encoding="utf-8") == "# hand written\n"
    assert (vault / CRITERIA_RELPATH).exists()
    assert "exists" in out


def test_sluice_config_retargets_the_written_config(cli, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("SLUICE_CONFIG", str(elsewhere))
    _harness, run = cli()
    rc, out, _err = run(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0
    assert elsewhere.exists()
    assert str(elsewhere) in out


def test_init_creates_nothing_under_the_state_or_cache_roots(cli, tmp_path):
    """#80: init scaffolds config and a vault note, and touches no per-system state. A
    stray file under the state root is how a relocation notice gets disarmed -- a 0-byte
    seen.db is enough."""
    _harness, run = cli()
    run(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    for root in ("state", "cache"):
        stray = tmp_path / "xdg" / root / "sluice"
        assert not stray.exists() or list(stray.iterdir()) == []


def test_a_new_vault_directory_is_reported_as_created(cli, tmp_path):
    """The word that catches a typo'd path before someone wonders where their notes
    went."""
    _harness, run = cli()
    rc, out, _err = run(["init", "--vault", str(tmp_path / "brand-new"), "--no-input"])
    assert rc == 0
    assert "created" in out.lower()


def test_a_vault_path_that_is_a_file_refuses(cli, tmp_path):
    afile = tmp_path / "not-a-dir"
    afile.write_text("x", encoding="utf-8")
    _harness, run = cli()
    rc, _out, err = run(["init", "--vault", str(afile), "--no-input"])
    assert rc == 2
    assert "not a directory" in err


def test_the_written_config_loads_and_abstains(cli, tmp_path):
    """End to end: the file init actually put on disk is neutral, not just the string
    build_plan returned."""
    from sluice.core.config import load_config
    from sluice.triage.config import load_triage_config
    _harness, run = cli()
    run(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    path = str(_config_path(tmp_path))
    assert load_config(path).relevance_keep == []
    assert load_triage_config(path).accept_titles == []
    assert load_config(path).lead_ttl_days == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/functional/test_init.py -q`
Expected: FAIL — `argument group: invalid choice: 'init'`

- [ ] **Step 3: Write `cmd_init` in `sluice/cli.py`**

Insert above `def cmd_doctor`:

```python
def cmd_init(args, config, *, asker=None) -> int:
    """Scaffold a config and a Judging Profile (#8).

    Preflight resolves BOTH destinations before a single question is asked. A wizard that
    interviews someone for five minutes and then says "config already exists" has wasted
    their time to learn something it knew at the start.
    """
    import sys

    from sluice.core.paths import config_file
    from sluice.core.protocols import CRITERIA_RELPATH
    from sluice.core.vault import Vault
    from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect,
                                    collect_profile)
    from sluice.onboard.plan import build_plan
    from sluice.onboard.questions import catalogue

    config_dest = config_file()
    config_exists = os.path.exists(config_dest)

    presets = {}
    vault_arg = args.vault or os.environ.get("VAULT_DIR") or config.vault_dir
    if vault_arg:
        presets["vault_dir"] = os.path.abspath(os.path.expanduser(vault_arg))

    interactive = not args.no_input and sys.stdin.isatty()
    if asker is None:
        asker = (TtyAsker(sys.stdin, sys.stdout, editor=os.environ.get("EDITOR"))
                 if interactive else NoInputAsker(presets))

    # A preset must win over a prompt even on a TTY: someone who passed --vault has
    # already answered.
    questions = tuple(q for q in catalogue() if q.key not in presets)

    try:
        answers = dict(presets)
        answers.update(collect(asker, questions))
    except MissingAnswer as exc:
        print(f"sluice init: {exc}", file=sys.stderr)
        return 2

    vault_dir = answers["vault_dir"]
    if os.path.exists(vault_dir) and not os.path.isdir(vault_dir):
        print(f"sluice init: {vault_dir} is not a directory.", file=sys.stderr)
        return 2
    vault_created = not os.path.exists(vault_dir)

    profile_dest = os.path.join(vault_dir, CRITERIA_RELPATH)
    profile_exists = os.path.exists(profile_dest)

    profile_answers = {}
    if interactive and not profile_exists:
        profile_answers = collect_profile(asker)

    plan = build_plan(answers, config_dest=config_dest, profile_dest=profile_dest,
                      profile_answers=profile_answers)

    written, skipped, failed = [], [], []

    if config_exists:
        skipped.append(config_dest)
    else:
        os.makedirs(os.path.dirname(config_dest), exist_ok=True)
        try:
            # "x": an exclusive create cannot truncate a config a concurrent shell just
            # wrote. Never-clobber is a property of the open, not of the check above it.
            with open(config_dest, "x", encoding="utf-8") as fh:
                fh.write(plan.config_text)
            written.append(config_dest)
        except FileExistsError:
            skipped.append(config_dest)
        except OSError as exc:
            failed.append(f"{config_dest}: {exc}")

    try:
        os.makedirs(vault_dir, exist_ok=True)
        handle = Vault(vault_dir).write_document(
            CRITERIA_RELPATH, plan.profile_text, only_if_absent=True)
        (written if handle else skipped).append(profile_dest)
    except OSError as exc:
        failed.append(f"{profile_dest}: {exc}")

    for path in written:
        print(f"  wrote   {path}")
    for path in skipped:
        print(f"  exists  {path}  (left alone)")
    for line in failed:
        print(f"  FAILED  {line}", file=sys.stderr)

    if vault_created:
        print(f"\ncreated a new vault directory at {vault_dir}")
        print("if you meant an existing one, re-run with --vault pointing at it")
    else:
        print(f"\nusing the existing vault at {vault_dir}")

    if plan.notes:
        print("\nYour config will:")
        for note in plan.notes:
            print(f"  {note}")

    print("\nNext:")
    print("  1. fill in the headings in your Judging Profile")
    print("  2. sluice ingest list-sources --health")
    print("  3. sluice triage run --no-llm")

    # Nothing is rolled back on a partial failure. Deleting a file we just wrote to
    # someone's disk, to tidy up after a failure they can see and retry, is a
    # destructive act -- and a re-run skips what landed and retries what did not.
    return 1 if failed else 0
```

- [ ] **Step 4: Wire the parser**

In `_build_parser`, immediately before the `doctor` parser:

```python
    init = top.add_parser("init", help="scaffold a config and a Judging Profile")
    init.add_argument("--vault", help="your Obsidian vault directory")
    init.add_argument("--no-input", action="store_true",
                      help="take every default; never prompt")
    init.set_defaults(func=cmd_init)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/functional/test_init.py -q`
Expected: 10 passed

- [ ] **Step 6: Run the CLI-contract sweep, which now sees a new command**

Run: `.venv/bin/python -m pytest tests/functional/test_cli_contract.py -q`
Expected: PASS. If the #7 dest-sweep reports `--vault`/`--no-input` as never read, the sweep is right — confirm `cmd_init` reads `args.vault` and `args.no_input` directly.

- [ ] **Step 7: Update README's quickstart**

Replace the `pip install -e .` block at `README.md:79-91` with:

```bash
pip install -e .
sluice init --vault ~/path/to/your/obsidian/vault
```

and replace the paragraph after it with:

```markdown
`sluice init` writes `$XDG_CONFIG_HOME/sluice/config.yaml`
(`~/.config/sluice/config.yaml` on a default setup) and scaffolds
`Job Applications/Judging Profile.md` in your vault. On a terminal it walks you
through the settings that are personal; `--no-input` takes every default instead.
It never overwrites either file, so re-running it is safe.

The config it writes has **every preference commented out**, because an unset gate
abstains and passes every lead through. That matters:
[`sluice.yaml.example`](sluice.yaml.example) is a *catalogue* with illustrative
values filled in, so copying it wholesale hands you somebody else's job search --
its `relevance_keep` alone would discard every title that is not a horticultural
consultant. Read it for what each knob does; let `init` write your actual config.
```

- [ ] **Step 8: Add `onboard/` to `docs/ARCHITECTURE.md`**

Add to the module list, after the five sub-apps:

```markdown
### `onboard/` — the `sluice init` wizard

A command package, not a sixth pipeline sub-app: nothing downstream imports it.
Split pure-from-impure — `questions.py` and `plan.py` are pure functions over a dict
(so "an unanswered wizard writes a config that expresses nothing" is a unit test, not
a wizard transcript), `ask.py` holds every prompt and the one `$EDITOR` subprocess.
The config it emits is rendered *from the catalogue*, which is what makes "every key
the wizard can write appears in the file it writes" true by construction. It writes
the profile through `Store.write_document(..., only_if_absent=True)` rather than the
filesystem, so a future non-vault store inherits it.
```

- [ ] **Step 9: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 10: Witness M7 — drop the abspath on the vault**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/onboard/questions.py /tmp/questions.py.bak
# In parse_path, DELETE the abspath wrapper: `return os.path.expanduser(raw.strip())`
.venv/bin/python -m pytest "tests/test_onboard_questions.py::test_parse_path_expands_and_absolutises" -v
# Expected: FAIL
cp /tmp/questions.py.bak sluice/onboard/questions.py
git diff --stat   # MUST be empty
```

- [ ] **Step 11: Commit**

```bash
git add sluice/cli.py tests/functional/test_init.py README.md docs/ARCHITECTURE.md
git commit -m "feat(cli): sluice init (#8)

Preflight resolves both destinations BEFORE asking anything -- a wizard
that interviews someone and then says 'config already exists' wasted their
time to learn something it knew at the start. Per-artefact never-clobber,
so an existing config still lets the profile scaffold, and a re-run exits 0.

A partial failure is never rolled back: deleting a file we just wrote, to
tidy up after a failure the user can see and retry, is a destructive act,
and a re-run retries exactly what did not land.

README's quickstart stops telling people to copy sluice.yaml.example --
that file ships ACTIVE placeholder gates, and its relevance_keep alone
discards every title but one, measured. The example stays the annotated
catalogue; init writes the neutral config.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 7: Retire `Config.locations`

**Files:**
- Modify: `sluice/core/config.py:18-24`, `:78`, `:160-163`, `:208`; `sluice.yaml.example:6-14`
- Test: `tests/test_config.py` (replace the three `locations` assertions), `tests/test_sluice_neutral_defaults.py:57,87`

**Interfaces:**
- Consumes: nothing
- Produces: `sluice.core.config.refuse_retired_locations(data: dict) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_config.py
"""`locations` was declared, documented in sluice.yaml.example, and read by NOTHING --
its own comment called it a loaded gun. `sluice init` would have been the thing that
finally populated it, so it is retired the way #80 retired triage.dossier_dir: loudly."""
import pytest

from sluice.core.config import load_config


def test_a_config_that_still_sets_locations_refuses_and_names_the_replacement(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("locations: [Alfa]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


def test_the_message_does_not_echo_the_value(tmp_path):
    """Geography is personal and an exception travels further than the file it came from
    -- logs, bug reports, pasted tracebacks. Same rule as refuse_retired_dossier_dir."""
    path = tmp_path / "c.yaml"
    path.write_text("locations: [Alfa]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(str(path))
    assert "Alfa" not in str(exc.value)


def test_a_config_without_locations_loads_fine(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    assert load_config(str(path)) is not None


def test_the_example_config_no_longer_advertises_the_retired_key():
    example = open("sluice.yaml.example", encoding="utf-8").read()
    for line in example.splitlines():
        stripped = line.lstrip("# ").strip()
        assert not stripped.startswith("locations:"), \
            "sluice.yaml.example still documents the retired `locations` key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -q -k retired or locations`
Expected: FAIL — `DID NOT RAISE ValueError`

- [ ] **Step 3: Add the refusal to `core/config.py`**

Beside `refuse_retired_dossier_dir`:

```python
def refuse_retired_locations(data: dict) -> None:
    """Raise if a config still sets the retired root `locations` key (#8).

    It was declared, documented in `sluice.yaml.example`, and read by NOTHING -- this
    module's own comment called it "a loaded gun rather than a live bug, since the first
    consumer to wire it into a search or a gate would have inherited a stranger's 'remote
    only'". `sluice init` would have been that consumer, filling a key that does nothing.

    Geography is a triage concern and `triage.target_locations` is the live key, so the
    message names it. The VALUE is never echoed: it is personal, and an exception travels
    further than the file it came from -- logs, bug reports, pasted tracebacks. Same
    ruling as `refuse_retired_dossier_dir` and `dossier_allow_hosts`.
    """
    if "locations" in data:
        raise ValueError(
            "config key `locations` was never read by anything and has been retired. "
            "Geography is a triage concern -- move your value to `triage.target_locations`.")
```

- [ ] **Step 4: Call it, and drop the field**

In `load_config`, immediately after the config data is loaded and before any field is read:

```python
    refuse_retired_locations(data)
```

Delete `_DEFAULT_LOCATIONS` (lines 18-24), the `locations` dataclass field (line 78), the `locations = ...` derivation (lines 160-163, including the env-var branch), and `locations=locations` from the `Config(...)` construction (line 208).

- [ ] **Step 5: Update the three existing assertions**

In `tests/test_config.py`, delete `assert cfg.locations == []` and the two tests asserting `locations` parses from YAML and from the env var — the key no longer exists. In `tests/test_sluice_neutral_defaults.py`, delete lines 57 and 87 (`assert c.locations == []` / `assert loaded.locations == []`).

- [ ] **Step 6: Remove it from `sluice.yaml.example`**

Delete the `locations` comment block (the paragraph beginning "Geography is personal…" through `# locations: [Remote]   # <- uncomment and set YOUR OWN`).

- [ ] **Step 7: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 8: Witness — delete the refusal**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp sluice/core/config.py /tmp/config.py.bak
# DELETE the `refuse_retired_locations(data)` CALL in load_config
.venv/bin/python -m pytest "tests/test_config.py::test_a_config_that_still_sets_locations_refuses_and_names_the_replacement" -v
# Expected: FAIL
cp /tmp/config.py.bak sluice/core/config.py
git diff --stat   # MUST be empty
```

- [ ] **Step 9: Commit**

```bash
git add sluice/core/config.py sluice.yaml.example tests/test_config.py tests/test_sluice_neutral_defaults.py
git commit -m "fix(core): retire the dead \`locations\` config key (#8)

Declared, documented in sluice.yaml.example, and read by nothing -- the
module's own comment called it a loaded gun, since the first consumer to
wire it in would inherit a stranger's 'remote only'. \`sluice init\` would
have been that consumer, so it is retired the way #80 retired
triage.dossier_dir: raise at load, name triage.target_locations as the
live key, and never echo the value.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 8: The acceptance scenario — init to confident verdicts

**Files:**
- Create: `tests/e2e/test_init_to_verdicts.py`

**Interfaces:**
- Consumes: `tests/harness` (`build_harness`, `ScriptedBackend`), `cli` driver conventions from `tests/functional/conftest.py`
- Produces: nothing

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_init_to_verdicts.py
"""Issue #8's own acceptance criterion, end to end.

TWO ARMS, and the second is what makes the first mean anything: a single-arm version
passes even if the profile is ignored entirely, because the harness backend could be
returning `shortlist` for its own reasons. The pair proves the PROFILE is what moved the
verdict. Same attribution shape as S1 in #58.
"""
import os

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import Vault
from sluice.triage.prompt import build_system_prompt_from

FILLED_PROFILE = """\
## Who this candidate is

An example practitioner of the example trade, with example seniority.

### Target and wrong shape

Target: an example-shaped role. Wrong: anything at example-director scope.

## Win patterns and anti-patterns

Attracts: 'example win phrase'. Repels: 'example anti phrase'.
"""


def test_init_scaffolds_a_profile_the_judge_actually_consumes(cli, tmp_path):
    """Arm 1: after init, the scaffold is what the judge's system prompt is built from."""
    _harness, run = cli()
    vault = tmp_path / "notes"
    rc, _out, _err = run(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0

    criteria = Vault(str(vault)).read_criteria()
    prompt = build_system_prompt_from(criteria)
    assert "Judging Profile" in prompt
    assert "No Judging Profile has been configured yet" not in prompt


def test_an_unconfigured_install_still_falls_back_to_the_opinion_free_default(tmp_path):
    """Arm 2, the attribution half: with NO profile the judge gets the shipped default,
    which states that nothing is configured and declines to invent an opinion. If arm 1
    passed with this also passing on the same input, arm 1 would be proving nothing."""
    empty = tmp_path / "empty-vault"
    empty.mkdir()
    prompt = build_system_prompt_from(Vault(str(empty)).read_criteria())
    assert "No Judging Profile has been configured yet" in prompt


def test_a_filled_profile_reaches_the_judge_verbatim(cli, tmp_path):
    """The user's own words, not the scaffold's prompts, are what the judge is given --
    which is the whole point of scaffolding a file they then edit in Obsidian."""
    _harness, run = cli()
    vault = tmp_path / "notes"
    run(["init", "--vault", str(vault), "--no-input"])

    path = os.path.join(str(vault), CRITERIA_RELPATH)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FILLED_PROFILE)

    prompt = build_system_prompt_from(Vault(str(vault)).read_criteria())
    assert "example win phrase" in prompt
    assert "example anti phrase" in prompt
    assert "No Judging Profile has been configured yet" not in prompt


def test_the_scaffold_does_not_smuggle_an_opinion_into_the_judge_prompt(cli, tmp_path):
    """The scaffold ships to every user, so it is held to the same bar as
    `_DEFAULT_CRITERIA`: it may describe WHAT to write and must never propose an answer."""
    _harness, run = cli()
    vault = tmp_path / "notes"
    run(["init", "--vault", str(vault), "--no-input"])

    criteria = Vault(str(vault)).read_criteria().lower()
    for word in ("startup", "scale-up", "enterprise", "fintech", "remote-first",
                 "senior", "engineer", "manager"):
        assert word not in criteria, f"the scaffold proposes {word!r}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/e2e/test_init_to_verdicts.py -q`
Expected: FAIL if the `cli` fixture is not visible from `tests/e2e/`

- [ ] **Step 3: Make the `cli` fixture available to the e2e tier**

`tests/e2e/conftest.py` does not define `cli`. Move the `cli` fixture body from `tests/functional/conftest.py` into `tests/harness/driver.py` as `make_cli(tmp_path, monkeypatch, capsys)`, and have both conftests expose a thin `cli` fixture calling it. Do **not** duplicate the fixture — the subclass-patch comment in `tests/functional/conftest.py` documents a design call that must not be copy-pasted into a second home.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/e2e/test_init_to_verdicts.py -q`
Expected: 4 passed

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests scripts`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_init_to_verdicts.py tests/harness/driver.py tests/functional/conftest.py tests/e2e/conftest.py
git commit -m "test(e2e): #8's acceptance criterion, in two arms

A single-arm version passes even if the profile is ignored entirely, so
the attribution half (no profile -> the shipped opinion-free default) is
what makes the first arm mean anything. Same shape as S1 in #58.

The cli fixture moves to tests/harness/driver.py rather than being copied:
its subclass-patch comment records a design call, and a second copy is a
second place for it to go stale.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: Definition of done

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, ~1590 tests, 0 skipped on Linux/macOS

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check sluice tests scripts`
Expected: `All checks passed!`

- [ ] **Step 3: The `"./"` grep is still at 9**

Run: `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py | wc -l`
Expected: `9`

- [ ] **Step 4: rulesync is clean**

Run: `npm ci --ignore-scripts && npm run rulesync && git status --porcelain`
Expected: no drift in generated outputs

- [ ] **Step 5: The wizard runs for real, offline, in a throwaway home**

```bash
tmp=$(mktemp -d)
env HOME="$tmp" XDG_CONFIG_HOME="$tmp/.config" XDG_STATE_HOME="$tmp/.local/state" \
    XDG_CACHE_HOME="$tmp/.cache" -u VAULT_DIR -u SLUICE_CONFIG \
    .venv/bin/python -m sluice.cli init --vault "$tmp/notes" --no-input
cat "$tmp/.config/sluice/config.yaml"
cat "$tmp/notes/Job Applications/Judging Profile.md"
find "$tmp/.local/state" "$tmp/.cache" -type f 2>/dev/null   # MUST be empty
```
Expected: both files written; the state and cache roots hold no files.

- [ ] **Step 6: The refusal actually refuses on a pipe**

```bash
env HOME="$tmp" -u VAULT_DIR .venv/bin/python -m sluice.cli init --no-input < /dev/null
echo "exit: $?"    # MUST be 2, and MUST NOT hang
```

- [ ] **Step 7: Run `/review-pr` BEFORE pushing**

The standing rule: the specialist team is free and parallel; CodeRabbit is the scarce resource. Fold every finding, then push once and open the PR.

## Self-review

**Spec coverage.** Every section maps to a task: architecture → 2-5; store seam + constants → 1; catalogue and the two findings → 3, 7; emitter → 2; `build_plan` + both paired guards + drift pin + fan-out → 4; asker, `$EDITOR`, refusals → 5; preflight, never-clobber, partial failure, report, #80 obligations → 6; docs → 6, 7; acceptance → 8; DoD → 9. All seven mutants are witnessed in the task that adds their guard (M1/M3/M4 in task 4, M2 in 1, M5 in 2, M6 in 5, M7 in 6).

**Gap found and closed while reviewing:** the spec's test plan named the sources walk in the catalogue, but tasks 3 and 5 do not implement per-source prompting — `build_plan` accepts a `sources` mapping and renders it (task 4), and the commented example block is what an unanswered run emits. **The interactive board walk is deliberately deferred**, because it is the one part of the wizard that needs a paged multi-select UI and 22 boards × N searches of terminal input, and every other task delivers value without it. It should be a follow-up task in this same PR once tasks 1-9 are green — the `sources=` parameter and its renderer exist precisely so that lands as a UI change, not a data change. Flag this to the user rather than silently dropping it.

**Placeholder scan:** none — every step carries the code or the exact command.

**Type consistency:** `Question.hint` (not `help`), `Question.writes_to` as a tuple of dotted strings, `InitPlan.config_text`/`profile_text`/`notes`, `build_plan(answers, *, config_dest, profile_dest, profile_answers=None, sources=None)`, `write_document(rel, text, *, only_if_absent=False) -> str` returning `""` on abstain — consistent across tasks 1, 3, 4, 5, 6.
