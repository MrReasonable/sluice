# Candidate Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `cv.name`/`cv.contact` config with a `Job Applications/Candidate Profile.md` vault note read through the Store contract, and feed its 36 fields into `apply prep`'s packet — closing #107 (blank contact burns a backend call to fail) and #133 (apply has no candidate-side form data).

**Architecture:** A new `CandidateProfile` dataclass in `core/protocols.py` (36 flat `str` fields, all defaulting to `""`), a new MUST-support `Store.read_candidate_profile()`, and a new pure `core/candidate.py` holding the derivations (`full_name`, `contact_block`, `age_from_dob`, `has_any_declared`). `cv/engine.py` and `apply/packet.py` consume the derived values where they previously read `CvConfig`. Onboarding gains a `collect_candidate` interview that mirrors the Judging Profile's, and `cmd_init` gates it on a store probe at the point the store actually exists.

**Tech Stack:** Python 3.12+, standard library only in `sluice/`. pytest. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-08-19-candidate-profile-design.md`

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency. `yaml` stays behind its existing guarded `try/except ImportError`.
- **No personal data in `sluice/` or `tests/`.** No employer names, role preferences, locations, contacts, hostnames, absolute paths, credentials. This is a public repo.
- **Empty config abstains.** Every one of `CandidateProfile`'s 36 fields defaults to `""`. An undeclared field is never inferred, defaulted, or guessed.
- **Fail loudly at construction.** A legacy `cv.name`/`cv.contact` in YAML raises and names the vault note. Never a silent drop.
- **The CV fabrication gate is hard and pure.** `cv/validate.py` is not touched by any task in this plan. Retry stays exactly once, then skip.
- **Never-clobber.** No task changes a lead-note write path. `write_document(..., only_if_absent=True)` is the only new write, and it never overwrites.
- **Conventional Commits.** Every commit message is `type(scope): description`. Scope is the sub-app (`cv`, `apply`, `core`, `onboard`, `doctor`, `docs`, `test`).
- **The suite is green at every task boundary.** Run `python -m pytest` before every commit. Tasks 1-8 are additive; Task 9 is the atomic removal.
- **Lint:** `ruff check sluice tests scripts` must pass. ruff is not in `[test]`; `pip install ruff==0.15.21`.
- **Exact field list — 36 keys, used verbatim everywhere:**
  `forenames`, `surname`, `email`, `mobile`, `linkedin`,
  `address_line1`, `address_line2`, `town`, `county`, `postcode`, `country`,
  `requires_uk_work_permit`, `right_to_work_uk`, `currently_employed_by_them`, `previously_employed_by_them`, `referred_by_current_employee`,
  `how_heard_default`, `how_heard_detail_from_lead_source`,
  `gender_identity`, `identifies_as_trans`, `ethnicity`, `religion`, `sexual_orientation`, `preferred_pronouns`, `disability`, `neurodivergent`, `open_about_orientation_at_work`,
  `date_of_birth`, `title`, `marital_status`, `nationality`, `dual_nationality`, `first_language`, `served_armed_forces`, `caring_responsibility`, `worked_in_construction`

**Task order is load-bearing.** Tasks 1-8 add the new path while `CvConfig.name`/`.contact` still exist, so the suite stays green throughout. Task 9 removes them in one atomic change. Do not reorder.

---

### Task 1: `CandidateProfile` and the pure derivations

> **SUPERSEDED IN PART — read this before the code blocks below.** Task 1's review upheld five
> Important findings, four of them against these code blocks rather than against the
> implementation. The spec was amended accordingly (`5363ac8`, markers `rev5-001`/`002`/`003`) and
> the controller's rulings R1-R9 are recorded at the end of
> `.superpowers/sdd/2026-08-19-candidate-profile/task-1-brief.md`. Where they disagree with what
> follows, **they win**. In particular: a blank `date_of_birth` abstains SILENTLY (the warning
> below fires on the designed default of an optional field, i.e. on every lead of every run); a
> non-`str` `today` RAISES naming `today` instead of being caught and misreported as
> `date_of_birth`; a `dob` later than `today` abstains instead of returning a negative age; the
> `contact_block` and `has_any_declared` docstrings below assert present-tense facts about code
> that Tasks 3 and 7 have not written yet; and the roster test pins the field COUNT but not the
> 36 field NAMES, so a rename ships green. Step 5's "16 tests" is also a miscount —
> `parametrize` expands them to 18.

**Files:**
- Modify: `sluice/core/protocols.py` (add `CandidateProfile`, `CANDIDATE_PROFILE_RELPATH`)
- Create: `sluice/core/candidate.py`
- Test: `tests/test_candidate.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `CandidateProfile` (frozen=False dataclass, 36 `str` fields all `= ""`), `CANDIDATE_PROFILE_RELPATH = "Job Applications/Candidate Profile.md"`, and from `sluice/core/candidate.py`: `full_name(profile) -> str`, `contact_block(profile) -> str`, `age_from_dob(dob: str, today: str) -> int | None`, `has_any_declared(profile) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_candidate.py`:

```python
"""core/candidate.py: the pure derivations over a CandidateProfile.

Fixtures are synthetic. `Example` names and RFC 2606 domains only -- this
file's values are read by tests/test_fixture_name_neutrality.py's sweep.
"""
import dataclasses
import logging

import pytest

from sluice.core.candidate import age_from_dob, contact_block, full_name, has_any_declared
from sluice.core.protocols import CandidateProfile


def test_candidate_profile_has_thirty_six_fields_all_defaulting_blank():
    fields = dataclasses.fields(CandidateProfile())
    assert len(fields) == 36, "the field roster changed; update the spec and the packet list"
    # Scope assertion: a broken dataclasses.fields() call returning [] would make
    # the all() below vacuously true. Assert the COUNT first, then the property.
    assert all(f.default == "" for f in fields)
    assert all(f.type is str or f.type == "str" for f in fields)


def test_full_name_joins_declared_parts_only():
    assert full_name(CandidateProfile(forenames="Ada", surname="Example")) == "Ada Example"
    assert full_name(CandidateProfile(forenames="Ada")) == "Ada"
    assert full_name(CandidateProfile(surname="Example")) == "Example"
    assert full_name(CandidateProfile()) == ""


def test_full_name_leaves_no_stray_whitespace():
    assert full_name(CandidateProfile(forenames="  Ada  ", surname="  Example  ")) == "Ada Example"
    assert full_name(CandidateProfile(forenames="   ")) == ""


def test_contact_block_emits_bare_values_in_mobile_email_linkedin_order():
    p = CandidateProfile(mobile="+44 20 7946 0000", email="ada@example.invalid",
                         linkedin="https://www.linkedin.com/in/example/")
    assert contact_block(p) == ("+44 20 7946 0000\n"
                               "ada@example.invalid\n"
                               "https://www.linkedin.com/in/example/")


def test_contact_block_omits_undeclared_lines_rather_than_emitting_blanks():
    p = CandidateProfile(email="ada@example.invalid")
    assert contact_block(p) == "ada@example.invalid"
    assert "\n" not in contact_block(p)
    assert contact_block(CandidateProfile()) == ""


def test_contact_block_carries_no_labels():
    # The labels in sluice.yaml.example's cv.contact catalogue are a USER's
    # formatting choice in a value they can edit. Moving them into core/ would
    # make them an unoverridable shipped preference.
    out = contact_block(CandidateProfile(mobile="+44 20 7946 0000"))
    assert out == "+44 20 7946 0000"


@pytest.mark.parametrize("dob,today,expected", [
    ("1990-06-15", "2026-06-15", 36),   # exactly on the birthday
    ("1990-06-15", "2026-06-14", 35),   # day before
    ("1990-06-15", "2026-06-16", 36),   # day after
    ("2000-02-29", "2026-02-28", 25),   # leap-day birth, non-leap year
])
def test_age_from_dob_computes_whole_years(dob, today, expected):
    assert age_from_dob(dob, today) == expected


@pytest.mark.parametrize("dob", ["", "   ", "15/06/1990", "1990-13-01", "not-a-date"])
def test_age_from_dob_abstains_on_blank_or_unparseable(dob):
    assert age_from_dob(dob, "2026-06-15") is None


def test_age_from_dob_abstains_on_a_non_string_rather_than_raising():
    # The clock trap: core/app.py's `self._today` is a zero-arg CALLABLE. A caller
    # that passes it unresolved reaches date.fromisoformat(<function>) -> TypeError.
    # That must surface as an abstain, not a crash mid-packet-build.
    assert age_from_dob("1990-06-15", lambda: "2026-06-15") is None
    assert age_from_dob(None, "2026-06-15") is None


def test_age_from_dob_warning_never_names_the_raw_dob(caplog):
    # A log is a plausible place for a sensitive value to leak into a bug report.
    with caplog.at_level(logging.WARNING):
        age_from_dob("15/06/1990", "2026-06-15")
    assert caplog.records, "the abstain must be audible, not silent"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "15/06/1990" not in joined
    assert "date_of_birth" in joined


def test_has_any_declared_is_true_for_a_single_non_identity_field():
    # This predicate is what `sluice init` gates its write AND its existence probe
    # on. A user who answers only `email` produces a note that exists and is
    # useful but whose full_name is blank -- a full_name-based probe would re-ask
    # forever and deadlock the interview.
    assert has_any_declared(CandidateProfile()) is False
    assert has_any_declared(CandidateProfile(email="ada@example.invalid")) is True
    assert has_any_declared(CandidateProfile(ethnicity="SYNTHETIC-ETHNICITY-1")) is True
    assert has_any_declared(CandidateProfile(forenames="   ")) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_candidate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.core.candidate'`

- [ ] **Step 3: Add `CandidateProfile` and the relpath to `core/protocols.py`**

Add beside the existing `LeadNote` / `UpsertResult` dataclasses and the existing `CRITERIA_RELPATH`. `core/protocols.py` is interface-only — a dataclass with no methods belongs here; anything with a body does not.

```python
CANDIDATE_PROFILE_RELPATH = "Job Applications/Candidate Profile.md"
"""The candidate's own identity and application-form data. Like CRITERIA_RELPATH
this is an opaque DOCUMENT KEY, not a path -- nothing here may assume a filesystem."""


@dataclass
class CandidateProfile:
    """Every field is a plain `str` defaulting to "" -- no bool fields, deliberately.

    `core/vault.py`'s `_fm_dict` is a regex line-scanner, not a YAML loader, so
    `right_to_work_uk: true` and `disability: No` both arrive as the literal
    strings "true" and "No". Forcing a Python bool would buy nothing (nothing
    downstream needs boolean logic beyond the one
    `how_heard_detail_from_lead_source` check, which is an explicit string
    comparison) and would risk the bool-subclasses-int / PyYAML-coerces-`yes`
    trap this codebase is already careful about for fields that DO go through a
    real YAML loader.

    "" means UNDECLARED, and an undeclared field is never inferred, defaulted or
    guessed -- see the spec's "Presence semantics". The all-blank default is what
    makes an unconfigured install abstain rather than assert.
    """
    # Identity & contact -- feeds cv, via full_name()/contact_block()
    forenames: str = ""
    surname: str = ""
    email: str = ""
    mobile: str = ""
    linkedin: str = ""
    # Address -- feeds apply, one packet key per field
    address_line1: str = ""
    address_line2: str = ""
    town: str = ""
    county: str = ""
    postcode: str = ""
    country: str = ""
    # Right to work & employment history -- feeds apply
    requires_uk_work_permit: str = ""
    right_to_work_uk: str = ""
    currently_employed_by_them: str = ""
    previously_employed_by_them: str = ""
    referred_by_current_employee: str = ""
    # How you heard about the role -- feeds apply
    how_heard_default: str = ""
    how_heard_detail_from_lead_source: str = ""
    # Equal-opportunities monitoring -- feeds apply, special-category data
    gender_identity: str = ""
    identifies_as_trans: str = ""
    ethnicity: str = ""
    religion: str = ""
    sexual_orientation: str = ""
    preferred_pronouns: str = ""
    disability: str = ""
    neurodivergent: str = ""
    open_about_orientation_at_work: str = ""
    # Other -- feeds apply
    date_of_birth: str = ""
    title: str = ""
    marital_status: str = ""
    nationality: str = ""
    dual_nationality: str = ""
    first_language: str = ""
    served_armed_forces: str = ""
    caring_responsibility: str = ""
    worked_in_construction: str = ""
```

- [ ] **Step 4: Create `sluice/core/candidate.py`**

```python
"""Pure derivations over a CandidateProfile. Mirrors core/criteria.py: the
contract TYPE lives in core/protocols.py (interface only, no logic); anything
with a body lives here.

No I/O, no store handle, no config -- every function takes what it needs.
"""
import dataclasses
from datetime import date

from sluice.core.log import get_logger
from sluice.core.protocols import CandidateProfile

_log = get_logger("core.candidate")


def full_name(profile: CandidateProfile) -> str:
    """The CV header's name line. Joins whichever parts are declared, so a user
    who gave only a surname still gets a name rather than a stray space."""
    return " ".join(p for p in (profile.forenames.strip(), profile.surname.strip()) if p)


def contact_block(profile: CandidateProfile) -> str:
    """The CV header's contact block: the BARE declared value, one per line, in
    mobile/email/linkedin order, undeclared lines omitted rather than emitted empty.

    Bare, not labelled. `sluice.yaml.example` illustrates the old `cv.contact` with
    labels ("Phone number: ..."), but those are one user's formatting choice living
    in a value they could edit. Moving them here would make them a shipped constant
    with no override, which is a formatting preference in code. A user who wants a
    label puts it in the field value -- the field is free text.

    This is also the value cv/engine.py's #99/#100 STRUCTURAL guard compares the
    composed CV's header block against, so whatever this returns is what the
    composer is told to emit and what the guard expects back.
    """
    lines = [v for v in (profile.mobile.strip(), profile.email.strip(),
                         profile.linkedin.strip()) if v]
    return "\n".join(lines)


def has_any_declared(profile: CandidateProfile) -> bool:
    """True when ANY of the 36 fields is declared.

    `sluice init` uses this for BOTH its write gate and its existence probe, and
    that sameness is the point: if the write happened, the probe returns True, so
    the interview gate always closes. A `full_name`-based probe would re-ask
    forever for a user who answered only `email` -- the note exists, the write
    refuses, the answers go to .init-scaffold.md, and the run after that reports
    `failed` with the real note still empty.
    """
    return any(getattr(profile, f.name).strip() for f in dataclasses.fields(profile))


def age_from_dob(dob: str, today: str) -> int | None:
    """Whole years between two ISO 8601 (YYYY-MM-DD) dates, or None.

    `today` is a `str`, not a `date`, deliberately matching Sluice.staleness()'s
    existing pattern: `self._today` is a zero-arg CALLABLE returning a string,
    never a string itself, and core/app.py:464-483's docstring already records the
    trap of binding the unresolved callable into a typed value.

    Catches ValueError and TypeError ONLY. ValueError is the malformed or
    out-of-range date; TypeError is the non-str case the clock trap produces. A
    bare `except Exception` would swallow the very TypeError that proves a caller
    passed the callable instead of calling it -- the failure this parameter's type
    exists to surface.

    Returns None rather than a guess: a malformed date must not crash
    packet-building, and None is an honest "couldn't compute", not a value.
    The warning names the FIELD, never the raw value -- a log is a plausible place
    for a sensitive value to leak into a bug report.
    """
    try:
        born = date.fromisoformat(dob)
        now = date.fromisoformat(today)
    except (ValueError, TypeError):
        _log.warning("candidate: date_of_birth is blank or not ISO 8601 (YYYY-MM-DD); "
                     "age omitted from the application packet")
        return None
    return now.year - born.year - ((now.month, now.day) < (born.month, born.day))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_candidate.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 6: Run the full suite and lint**

Run: `python -m pytest && ruff check sluice tests scripts`
Expected: all green. Nothing is wired up yet, so nothing else can regress.

- [ ] **Step 7: Commit**

```bash
git add sluice/core/protocols.py sluice/core/candidate.py tests/test_candidate.py
git commit -m "feat(core): add CandidateProfile and its pure derivations"
```

---

### Task 2: The Store contract method and `Vault.read_candidate_profile()`

**Files:**
- Modify: `sluice/core/protocols.py` (add `read_candidate_profile` to the `Store` Protocol)
- Modify: `sluice/core/vault.py` (add `Vault.read_candidate_profile`, add a public `parse_frontmatter`)
- Modify: `tests/conformance/seeds.py` (add a `candidate=` seeder)
- Modify: `tests/conformance/test_store_contract.py` (add the parametrized entry)
- Modify: `tests/test_mcpserver.py` (add to `_STORE_READ_METHODS`)
- Test: `tests/test_vault_candidate_profile.py` (create)

**Interfaces:**
- Consumes: `CandidateProfile`, `CANDIDATE_PROFILE_RELPATH` (Task 1).
- Produces: `Store.read_candidate_profile() -> CandidateProfile` (MUST-support, not optional); `sluice.core.vault.parse_frontmatter(text: str) -> dict` (public wrapper over the existing module-private `_fm_dict`, needed by `onboard/plan.py` in Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_candidate_profile.py`:

```python
"""Vault.read_candidate_profile(): the frontmatter read, and the parser's limits."""
import os

from sluice.core.candidate import full_name
from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH, CandidateProfile
from sluice.core.vault import Vault


def _write_note(tmp_path, body):
    dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body)
    return Vault(str(tmp_path))


def test_a_missing_note_returns_an_all_blank_profile_rather_than_raising(tmp_path):
    # Same "unset means empty string, caller falls back" shape read_criteria has.
    v = Vault(str(tmp_path))
    assert v.read_candidate_profile() == CandidateProfile()


def test_only_the_declared_keys_come_back_declared(tmp_path):
    v = _write_note(tmp_path, "---\nforenames: Ada\nsurname: Example\n---\n\nbody prose\n")
    p = v.read_candidate_profile()
    assert p.forenames == "Ada"
    assert p.surname == "Example"
    assert p.email == ""
    assert full_name(p) == "Ada Example"


def test_unknown_frontmatter_keys_are_ignored(tmp_path):
    v = _write_note(tmp_path, "---\nforenames: Ada\nage_range: 35-44\nnonsense: x\n---\n")
    p = v.read_candidate_profile()
    assert p.forenames == "Ada"
    assert not hasattr(p, "age_range")


def test_a_key_outside_fm_dicts_character_class_is_dropped(tmp_path):
    # _fm_dict's key regex is [A-Za-z0-9_]+. A field name added later that does
    # not match is SILENTLY invisible, not loud. Pinned so the parser choice is a
    # tested fact rather than a comment.
    v = _write_note(tmp_path, "---\nForenames: Ada\nfore-names: Bea\nforenames: Cy\n---\n")
    assert v.read_candidate_profile().forenames == "Cy"


def test_the_body_is_never_read_as_data(tmp_path):
    v = _write_note(tmp_path, "---\nforenames: Ada\n---\n\nsurname: NotAField\n")
    assert v.read_candidate_profile().surname == ""


def test_parse_frontmatter_is_public_and_matches_the_reader(tmp_path):
    # onboard/plan.py verifies its own render through THIS function, so it must be
    # the same parser the vault reads with -- not a second implementation.
    from sluice.core.vault import parse_frontmatter
    assert parse_frontmatter("---\nforenames: Ada\n---\n") == {"forenames": "Ada"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_vault_candidate_profile.py -v`
Expected: FAIL — `AttributeError: 'Vault' object has no attribute 'read_candidate_profile'`

- [ ] **Step 3: Add the contract method to `core/protocols.py`'s `Store` Protocol**

Add beside `read_baseline` / `read_criteria`, in the MUST-support group — not the optional `preflight`/`precheck` group.

```python
    def read_candidate_profile(self) -> CandidateProfile:
        """The candidate's own identity and application-form data.

        MUST-support, like read_baseline/read_criteria -- NOT optional like
        preflight/precheck. An optional member would push a `getattr` None-branch
        into four callers and hand cv a "the store cannot say" case with no safe
        answer: composing without a name is the fabrication risk #99 exists to
        stop, and refusing on a store that merely did not implement the hook
        would be a silent feature-off.

        A store with no such document returns an all-blank CandidateProfile --
        abstain, not raise, the same shape read_criteria already has.
        """
        ...
```

- [ ] **Step 4: Add `parse_frontmatter` and `Vault.read_candidate_profile` to `core/vault.py`**

Add the public wrapper at module level, next to `_fm_dict`:

```python
def parse_frontmatter(text: str) -> dict:
    """Public wrapper over `_fm_dict` for callers OUTSIDE this module.

    `onboard/plan.py` renders a Candidate Profile note and must verify its own
    output round-trips through the REAL reader before writing it. Exposing the
    reader is strictly better than a second frontmatter parser in `onboard/`,
    which would drift from this one -- and drift is exactly what the verification
    exists to catch.

    Takes a WHOLE note (with its `---` fences), not the inner block, so a caller
    verifies the same bytes it is about to write.
    """
    return _fm_dict(_split_frontmatter(text)[0])
```

> **Implementer note:** `core/vault.py` already has a helper that splits a note into
> `(frontmatter_inner, body)`. Find it (`grep -n "def _split_frontmatter\|---" sluice/core/vault.py`)
> and use the existing one — do not add a second splitter. If its name differs, use the
> real name and keep this function's behaviour: whole note in, flat dict out.

Then the method on `Vault`, beside `read_criteria`:

```python
    def read_candidate_profile(self) -> CandidateProfile:
        """See Store.read_candidate_profile. Reads the note's frontmatter once via
        `_fm_dict` and builds a CandidateProfile from the known keys, ignoring
        anything else present.

        `_fm_dict`, not `_parse_fm_spaced` (which read_experience_entries uses):
        this note is machine-written and machine-read, and its keys are all
        lowercase-with-underscores by construction. That is a CHOICE, and it has a
        cost -- `_fm_dict`'s key regex is [A-Za-z0-9_]+, so a key it cannot match
        is silently dropped rather than raising. tests/test_vault_candidate_profile.py
        pins that as a tested fact.

        A missing note is an all-blank profile, not a raise.
        """
        try:
            with open(os.path.join(self.dir, CANDIDATE_PROFILE_RELPATH),
                      encoding="utf-8") as fh:
                text = fh.read()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return CandidateProfile()
        fm = _fm_dict(_split_frontmatter(text)[0])
        known = {f.name for f in dataclasses.fields(CandidateProfile)}
        return CandidateProfile(**{k: v for k, v in fm.items() if k in known})
```

> **Implementer note:** a real `PermissionError` must NOT be caught here — this module's
> standing rule is that an unreadable file is loud, never read as empty. Only the three
> "genuinely absent" errors above are folded into the blank profile.

- [ ] **Step 5: Add the conformance seeder and test**

In `tests/conformance/seeds.py`, `_seed_vault` currently accepts `experience`/`criteria`/`conflicted_status`. Add a `candidate=None` parameter that writes `CANDIDATE_PROFILE_RELPATH` with the given dict rendered as flat frontmatter.

In `tests/conformance/test_store_contract.py`, add — parametrized over every registered `store_name`, exactly like `test_read_criteria_abstains_when_unset`, and covering **both** directions:

```python
def test_read_candidate_profile_abstains_when_unset(store_name, store):
    # The abstain direction: no document, all-blank profile, no raise.
    assert store.read_candidate_profile() == CandidateProfile()


def test_read_candidate_profile_round_trips_a_declared_value(store_name, store):
    # The round-trip direction is NOT optional. Asserting only the abstain half
    # passes an amnesiac store that never reads the user's profile at all.
    seed(store_name, store, candidate={"forenames": "Ada", "email": "ada@example.invalid"})
    p = store.read_candidate_profile()
    assert p.forenames == "Ada"
    assert p.email == "ada@example.invalid"
    assert p.surname == ""
```

- [ ] **Step 6: Add the new read to `tests/test_mcpserver.py`'s read set**

`_STORE_WRITE_METHODS` is derived as `vars(Store)` minus a **hand-listed** `_STORE_READ_METHODS`. Adding a read to the Protocol without updating that list silently reclassifies it as a WRITE in the MCP isolation sweep.

```python
_STORE_READ_METHODS = {"read_leads", "read_experience_entries", "read_baseline",
                       "read_criteria", "read_candidate_profile"}
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_vault_candidate_profile.py tests/conformance/ tests/test_mcpserver.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite and lint**

Run: `python -m pytest && ruff check sluice tests scripts`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py tests/conformance/ tests/test_mcpserver.py tests/test_vault_candidate_profile.py
git commit -m "feat(core): add read_candidate_profile to the Store contract"
```

---

### Task 3: `cv/engine.py` reads the derived name and contact

**Files:**
- Modify: `sluice/cv/engine.py:104-232` (the `skipped-config` check, the `compose()` call, the #99/#100 STRUCTURAL guard)
- Modify: `tests/test_cv_engine.py` (the `_cfg()` helper at :118-133 and the #99/#100 tests)
- Test: `tests/test_cv_engine.py` (add the new refusal tests)

**Interfaces:**
- Consumes: `full_name`, `contact_block` (Task 1); `store.read_candidate_profile()` (Task 2).
- Produces: `cv/engine.py` no longer reads `cvcfg.name` or `cvcfg.contact`. `CvConfig` still HAS those fields after this task — they are removed in Task 9.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_engine.py`:

```python
def test_a_blank_derived_name_refuses_before_any_backend_spend(tmp_path):
    """#107: the refusal must happen BEFORE the backend call, not after a compose
    that fails the gate. Asserting the result alone would pass even if the engine
    composed first and refused after -- the whole point is no spend."""
    vault = _vault_with_candidate(tmp_path, {})       # all-blank profile
    backend = _CountingBackend()
    res = run_one(vault, _cfg(), _note(), backend=backend)
    assert res.status == "skipped-config"
    assert backend.calls == 0, "a blank identity must cost no backend call"


def test_a_declared_name_with_blank_contact_also_refuses_before_spend(tmp_path):
    # #107's actual reported shape: the name was fine, the CONTACT was blank.
    vault = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example"})
    backend = _CountingBackend()
    res = run_one(vault, _cfg(), _note(), backend=backend)
    assert res.status == "skipped-config"
    assert backend.calls == 0


def test_a_fully_declared_identity_reaches_the_backend(tmp_path):
    vault = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example",
                                             "email": "ada@example.invalid"})
    backend = _CountingBackend()
    run_one(vault, _cfg(), _note(), backend=backend)
    assert backend.calls >= 1
```

> **Implementer note — the real signature is `run_one(note, vault, cvcfg, backend,
> dossier_cache, *, renderer, dry_run=False, ...)`.** The calls sketched above are
> written argument-name-first for readability and WILL NOT RUN as written: fix the order
> and supply `dossier_cache` and the keyword-only `renderer` the way the existing tests in
> this file already do. Read `_cfg()` at :118-133 and the surrounding helpers first and
> match their style.
>
> `_vault_with_candidate` and `_CountingBackend` are helpers you add to this file.
> `_CountingBackend` wraps the existing test backend fake and counts invocations.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cv_engine.py -k derived_name -v`
Expected: FAIL.

- [ ] **Step 3: Replace the placeholder check in `cv/engine.py`**

At `cv/engine.py:125-126`, replace:

```python
    if cvcfg.name.strip() == CvConfig.name:
        return CvResult(note.ref, "skipped-config")
```

with:

```python
    # #107: the identity now comes from the vault, not config. This is strictly
    # simpler than the old sentinel comparison -- a blank derived name just IS
    # blank, so no "Your Name" placeholder trick is needed to tell a configured
    # value from an unconfigured one. And it is the direct fix: a blank CONTACT
    # now aborts here, before any dossier fetch or backend call, instead of
    # composing and reporting skipped-gate on every attempt forever.
    profile = vault.read_candidate_profile()
    cv_name = full_name(profile)
    cv_contact = contact_block(profile)
    if not cv_name.strip() or not cv_contact.strip():
        return CvResult(note.ref, "skipped-config")
```

- [ ] **Step 4: Point `compose()` and the STRUCTURAL guard at the derived values**

At `cv/engine.py:159`, change `name=cvcfg.name, contact=cvcfg.contact` to `name=cv_name, contact=cv_contact`.

At `cv/engine.py:221`, change `expected_contact = [ln.strip() for ln in cvcfg.contact.splitlines() if ln.strip()]` to read `cv_contact.splitlines()`.

At `cv/engine.py:229` and `:232`, change `cvcfg.name` to `cv_name`.

No other change to that guard's logic — it still compares the composed header block against the configured name/contact exactly as before. Only where "configured" comes from moves.

- [ ] **Step 5: Update the docstring at `cv/engine.py:39`**

It currently says `skipped-config (#99: cv.name is still the shipped placeholder default, refused ...)`. Replace with a description of the new condition: the derived name or contact block is blank, refused before any dossier fetch or backend call (#107).

- [ ] **Step 6: Fix `tests/test_cv_engine.py`'s `_cfg()` and the #99/#100 tests**

`_cfg()` at :118-133 sets `c.name = "Jane Roe"`. The engine no longer reads it. Every #99/#100 test that asserts on a populated `cvcfg.name`/`cvcfg.contact` must seed the vault's Candidate Profile instead. Leave `CvConfig.name` alone — Task 9 removes it.

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_cv_engine.py -v`
Expected: PASS.

- [ ] **Step 8: Full suite and lint**

Run: `python -m pytest && ruff check sluice tests scripts`

- [ ] **Step 9: Commit**

```bash
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "fix(cv): refuse before spend when the vault identity is blank"
```

---

### Task 4: `apply/packet.py` — the new keys, the resolver, and `render_text`

**Files:**
- Modify: `sluice/apply/packet.py:24-58`
- Test: `tests/test_apply_packet.py`

**Interfaces:**
- Consumes: `CandidateProfile` (Task 1), `age_from_dob` (Task 1).
- Produces: `build_packet(note, cfg, *, profile, today, cv_staged)` — **all three keyword-only**; `resolve_how_heard(profile, listing_host) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_apply_packet.py`:

```python
_SYNTHETIC_EO = {
    # Obviously-synthetic tokens, enforced by tests/test_fixture_name_neutrality.py's
    # fifth collector (Task 10). Nothing local can tell a real demographic category
    # from an invented one, so the token SHAPE is what makes this reviewable.
    "gender_identity": "SYNTHETIC-GENDER_IDENTITY-1",
    "identifies_as_trans": "SYNTHETIC-IDENTIFIES_AS_TRANS-1",
    "ethnicity": "SYNTHETIC-ETHNICITY-1",
    "religion": "SYNTHETIC-RELIGION-1",
    "sexual_orientation": "SYNTHETIC-SEXUAL_ORIENTATION-1",
    "preferred_pronouns": "SYNTHETIC-PREFERRED_PRONOUNS-1",
    "disability": "SYNTHETIC-DISABILITY-1",
    "neurodivergent": "SYNTHETIC-NEURODIVERGENT-1",
    "open_about_orientation_at_work": "SYNTHETIC-OPEN_ABOUT_ORIENTATION_AT_WORK-1",
}


def test_a_declared_field_reaches_the_packet_and_an_undeclared_one_is_absent():
    p = CandidateProfile(town="Example Town")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    assert pkt["town"] == "Example Town"
    # ABSENT, not "" -- the form-filling skill must be able to tell "sluice has
    # nothing for this" from "sluice knows this is blank".
    assert "county" not in pkt


def test_age_is_computed_and_omitted_when_the_dob_does_not_parse():
    good = build_packet(_note(), _cfg(), profile=CandidateProfile(date_of_birth="1990-06-15"),
                        today="2026-06-15", cv_staged=False)
    assert good["age"] == 36
    bad = build_packet(_note(), _cfg(), profile=CandidateProfile(date_of_birth="15/06/1990"),
                       today="2026-06-15", cv_staged=False)
    assert "age" not in bad
    assert "date_of_birth" not in bad, "the raw DOB is never a packet key"


@pytest.mark.parametrize("prefer,host,default,expected", [
    ("true",  "greenhouse", "A referral", "greenhouse"),   # lead source wins
    ("true",  "other",      "A referral", "A referral"),   # unresolved host -> default
    ("true",  "other",      "",           None),           # nothing to say
    ("false", "greenhouse", "A referral", "A referral"),   # default wins
    ("",      "greenhouse", "A referral", "A referral"),   # blank means no
    ("false", "greenhouse", "",           None),           # nothing to say
])
def test_how_heard_resolves_across_all_three_axes(prefer, host, default, expected):
    p = CandidateProfile(how_heard_default=default, how_heard_detail_from_lead_source=prefer)
    assert resolve_how_heard(p, host) == expected


def test_how_heard_is_omitted_from_the_packet_when_it_resolves_to_none():
    p = CandidateProfile(how_heard_default="", how_heard_detail_from_lead_source="false")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    assert "how_heard" not in pkt, "omitted, never written as a null"


def test_identity_fields_never_reach_the_packet():
    # render_text's own RULES block says "Use first names only. No real full names
    # in third-party forms." The CV upload is the name/contact channel.
    p = CandidateProfile(forenames="Ada", surname="Example", email="ada@example.invalid",
                         mobile="+44 20 7946 0000", linkedin="https://example.invalid/in/x")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    for k in ("forenames", "surname", "email", "mobile", "linkedin"):
        assert k not in pkt


def test_render_text_renders_declared_fields_and_omits_undeclared_ones():
    # Asserting only on the packet DICT would pass while the default output path
    # rendered none of it -- render_text is what `apply prep` prints without --json.
    p = CandidateProfile(town="Example Town", right_to_work_uk="Yes")
    out = render_text(build_packet(_note(), _cfg(), profile=p, today="2026-08-19",
                                   cv_staged=False))
    assert "Example Town" in out
    assert "Yes" in out
    assert "county" not in out.lower()


def test_render_text_groups_the_monitoring_fields_under_their_own_heading():
    p = CandidateProfile(**_SYNTHETIC_EO)
    out = render_text(build_packet(_note(), _cfg(), profile=p, today="2026-08-19",
                                   cv_staged=False))
    assert "MONITORING" in out
    for value in _SYNTHETIC_EO.values():
        assert value in out
    # The heading must precede the values, so what they are is visible where they appear.
    assert out.index("MONITORING") < min(out.index(v) for v in _SYNTHETIC_EO.values())


def test_render_text_omits_the_monitoring_block_entirely_when_nothing_is_declared():
    out = render_text(build_packet(_note(), _cfg(), profile=CandidateProfile(),
                                   today="2026-08-19", cv_staged=False))
    assert "MONITORING" not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_apply_packet.py -v`
Expected: FAIL — `build_packet() got an unexpected keyword argument 'profile'`.

- [ ] **Step 3: Change `build_packet`'s signature and add the new keys**

```python
_PASSTHROUGH_KEYS = (
    "address_line1", "address_line2", "town", "county", "postcode", "country",
    "requires_uk_work_permit", "right_to_work_uk", "currently_employed_by_them",
    "previously_employed_by_them", "referred_by_current_employee",
    "gender_identity", "identifies_as_trans", "ethnicity", "religion",
    "sexual_orientation", "preferred_pronouns", "disability", "neurodivergent",
    "open_about_orientation_at_work",
    "title", "marital_status", "nationality", "dual_nationality", "first_language",
    "served_armed_forces", "caring_responsibility", "worked_in_construction",
)
_MONITORING_KEYS = (
    "gender_identity", "identifies_as_trans", "ethnicity", "religion",
    "sexual_orientation", "preferred_pronouns", "disability", "neurodivergent",
    "open_about_orientation_at_work",
)
_DETAIL_KEYS = tuple(k for k in _PASSTHROUGH_KEYS if k not in _MONITORING_KEYS)


def resolve_how_heard(profile, listing_host):
    """Prefer the computed lead source over the stored default when the caller
    asked for that AND the host actually resolved to something specific.
    None means "nothing to say" -- the caller OMITS the key rather than writing a null."""
    prefer_lead = profile.how_heard_detail_from_lead_source.strip().lower() == "true"
    if prefer_lead and listing_host not in ("", "other"):
        return listing_host
    return profile.how_heard_default.strip() or None


def build_packet(note, cfg, *, profile, today, cv_staged):
    """`profile` and `today` are KEYWORD-ONLY, joining the `*` this function
    already used. Two new required POSITIONAL parameters would silently transpose
    at any call site passing positionally, and there are three in sluice/ plus
    several in tests/.

    `today` is an ISO 8601 string, not a date -- see core/candidate.py:age_from_dob
    for why, and core/app.py for the single place the clock is resolved.

    Every profile-derived key is included ONLY when declared. An undeclared field
    is absent from the dict entirely, never present as "", so the form-filling step
    can tell "sluice has nothing to offer" from "sluice knows this is blank".
    """
    fm = note.fm
    url = (fm.get("url") or "").strip().strip('"')
    host = listing_host(url)
    pkt = {
        "company": fm.get("company", ""),
        "role": fm.get("role", ""),
        "location": fm.get("location", ""),
        "salary": fm.get("salary", ""),
        "url": url,
        "listing_host": host,
        "cv_path": os.path.join(cfg.camofox_cv_dir, cfg.neutral_name) if cv_staged else None,
        "skill": _SKILL,
    }
    for key in _PASSTHROUGH_KEYS:
        value = getattr(profile, key).strip()
        if value:
            pkt[key] = value
    age = age_from_dob(profile.date_of_birth, today)
    if age is not None:
        pkt["age"] = age
    how_heard = resolve_how_heard(profile, host)
    if how_heard is not None:
        pkt["how_heard"] = how_heard
    return pkt
```

- [ ] **Step 4: Render the new keys in `render_text`**

Insert between the `listing host` line and the CV-staged line:

```python
    details = [(k, p[k]) for k in _DETAIL_KEYS if k in p]
    if "age" in p:
        details.append(("age", p["age"]))
    if "how_heard" in p:
        details.append(("how_heard", p["how_heard"]))
    if details:
        lines.append("  DETAILS:")
        lines += [f"    {k}: {v}" for k, v in details]
    monitoring = [(k, p[k]) for k in _MONITORING_KEYS if k in p]
    if monitoring:
        # Printed by DEFAULT, not withheld behind a flag: the user asked sluice to
        # fill these forms, and withholding the answers leaves them retyping the
        # exact fields #133 is about. The heading is the mitigation -- what this
        # data is, stated where it appears. `apply prep --json` exists for anyone
        # piping the packet somewhere it will be retained.
        lines.append("  MONITORING (special-category; optional on most forms):")
        lines += [f"    {k}: {v}" for k, v in monitoring]
```

And add to the `RULES` block:

```python
        "    - Never guess a value for a field that is not in this packet. If an ATS",
        "      asks something with no matching field here, leave it for the human.",
        "    - Every MONITORING answer is optional. If the form offers 'prefer not to",
        "      say' and this packet has no value for it, choose that.",
```

- [ ] **Step 5: Update the three in-tree call sites so the suite still runs**

`sluice/apply/engine.py:34`, `sluice/apply/engine.py:49`, `sluice/core/app.py:1580`. Task 5 threads the real values; for now pass `profile=CandidateProfile(), today="1970-01-01"` **only if** you cannot complete Task 5 in the same task. Prefer to leave these three broken-but-typed and let Task 5 fix them — **no**: the suite must be green at the boundary. Pass the blank profile here, and Task 5 replaces it. Add a `# TASK 5:` comment on each so the temporary value is findable.

- [ ] **Step 6: Run the tests, full suite, lint**

Run: `python -m pytest && ruff check sluice tests scripts`

- [ ] **Step 7: Commit**

```bash
git add sluice/apply/packet.py sluice/apply/engine.py sluice/core/app.py tests/test_apply_packet.py
git commit -m "feat(apply): carry candidate profile fields into the application packet"
```

---

### Task 5: Thread the profile and the clock through `prep()`

**Files:**
- Modify: `sluice/core/app.py:1573-1585` (`prep`)
- Modify: `sluice/apply/engine.py:26-52` (`prep_one`, `preview_all`)
- Test: `tests/test_apply_engine.py` or `tests/test_app_injection.py` (whichever holds the existing prep tests)

**Interfaces:**
- Consumes: `build_packet(..., profile=, today=)` (Task 4); `store.read_candidate_profile()` (Task 2).
- Produces: `prep_one(vault, cfg, slug, policy=..., *, profile, today)` and `preview_all(vault, cfg, *, limit=None, policy=..., profile, today)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_candidate_profile_is_read_exactly_once_per_prep_call(tmp_path):
    """Contents-only assertions look identical under an accidental per-lead
    re-fetch. Count the calls."""
    store = _CountingStore(_vault_with_shortlist(tmp_path, n=3))
    app = _app(store)
    app.prep(all_shortlist=True)
    assert store.candidate_reads == 1


@pytest.mark.parametrize("kwargs", [
    {"lead": "example-lead"},
    {"lead": "example-lead", "dry_run": True},
    {"all_shortlist": True},
])
def test_every_prep_call_path_reads_the_profile_once(tmp_path, kwargs):
    store = _CountingStore(_vault_with_shortlist(tmp_path, n=2))
    _app(store).prep(**kwargs)
    assert store.candidate_reads == 1


def test_the_clock_callable_is_invoked_once_not_twice(tmp_path):
    """prep() already resolves the clock inside self.staleness(). Resolving it a
    second time beside that call could straddle midnight and give one prep() two
    different dates."""
    calls = []
    def clock():
        calls.append(1)
        return "2026-08-19"
    app = _app(_vault_with_shortlist(tmp_path, n=1), today=clock)
    app.prep(all_shortlist=True)
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_app_injection.py -k prep -v`

- [ ] **Step 3: Resolve the profile and the clock once in `prep()`**

`core/app.py`'s `prep` already calls `self.staleness(include_stale=include_stale)` at :1573, and `staleness()` internally does `clock = self._today or _today; today = clock()`. **Do not resolve the clock a second time.** Have `staleness()` return the resolved date alongside the policy, or read it back off the returned `StalenessPolicy` (it carries `today` as a field). Then:

```python
        policy = self.staleness(include_stale=include_stale)
        # ONE read per prep() call, not one per lead: preview_all loops the whole
        # shortlist, and a per-lead re-fetch would re-read the same note N times.
        profile = self.store().read_candidate_profile()
        # The clock is ALREADY resolved inside staleness(). Reading it back off the
        # frozen policy is what keeps one prep() on one date -- calling self._today
        # a second time here could straddle midnight.
        today = policy.today
```

and pass `profile=profile, today=today` to `prep_one` / `preview_all` / the dry-run `build_packet` at :1580.

- [ ] **Step 4: Thread them through `apply/engine.py`**

```python
def prep_one(vault, cfg, slug, policy=StalenessPolicy(), *, profile, today):
    ...
    pkt = _packet.build_packet(note, cfg, profile=profile, today=today, cv_staged=True)


def preview_all(vault, cfg, *, limit=None, policy=StalenessPolicy(), profile, today):
    ...
            # profile and today are resolved ONCE by the caller and passed in --
            # never re-read inside this loop.
            pkt = _packet.build_packet(n, cfg, profile=profile, today=today, cv_staged=False)
```

- [ ] **Step 5: Remove the `# TASK 5:` placeholders from Task 4**

- [ ] **Step 6: Run the tests, full suite, lint**

- [ ] **Step 7: Commit**

```bash
git add sluice/core/app.py sluice/apply/engine.py tests/
git commit -m "feat(apply): resolve the candidate profile once per prep run"
```

---

### Task 6: The onboarding interview and the frontmatter renderer

**Files:**
- Modify: `sluice/onboard/ask.py` (add `collect_candidate`)
- Modify: `sluice/onboard/plan.py` (add `_render_candidate`, extend `build_plan`, add round-trip verification)
- Test: `tests/test_onboard_candidate.py` (create)

**Interfaces:**
- Consumes: `parse_frontmatter` (Task 2), `CandidateProfile` (Task 1).
- Produces: `collect_candidate(asker) -> dict` keyed `cv_forenames`/`cv_surname`/`cv_email`/`cv_mobile`/`cv_linkedin`; `build_plan(answers, *, profile_answers=None, candidate_answers=None, sources=None)` gaining `plan.candidate_text`; `FrontmatterRoundTripError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_five_identity_questions_map_onto_frontmatter_keys():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={
        "cv_forenames": "Ada", "cv_surname": "Example",
        "cv_email": "ada@example.invalid", "cv_mobile": "+44 20 7946 0000",
        "cv_linkedin": "https://example.invalid/in/x"})
    fm = parse_frontmatter(plan.candidate_text)
    assert fm["forenames"] == "Ada"
    assert fm["surname"] == "Example"
    assert fm["email"] == "ada@example.invalid"


def test_all_thirty_six_keys_are_present_even_when_unanswered():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": "Ada"})
    fm = parse_frontmatter(plan.candidate_text)
    known = {f.name for f in dataclasses.fields(CandidateProfile)}
    assert known.issubset(set(fm)), "an unanswered field is present-but-empty, not absent"
    assert fm["surname"] == ""


def test_the_body_carries_prose_and_no_data():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": "Ada"})
    body = plan.candidate_text.split("---", 2)[2]
    assert "Candidate Profile" in body
    assert "Judging Profile" in body       # the backlink
    assert "Ada" not in body               # data lives in frontmatter, never the body


@pytest.mark.parametrize("hostile", ["Ada'", '"Ada"', "'Ada'", "Ada\x00Example"])
def test_a_value_that_does_not_survive_the_round_trip_is_refused_not_written(hostile):
    """_fm_dict ends in .strip().strip('"').strip("'") and unescapes nothing, so a
    lossy round trip corrupts the value and then compares the corrupted value
    against itself in cv/engine.py's #99 guard -- the PDF headline ships wrong with
    every guard green. There is no escaping scheme here: the REAL reader is the
    oracle."""
    with pytest.raises(FrontmatterRoundTripError) as exc:
        build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": hostile})
    assert "forenames" in str(exc.value)


def test_an_ordinary_value_with_an_internal_quote_survives():
    # Only LEADING/TRAILING quotes are stripped by _fm_dict. An internal one is fine,
    # and refusing it would be over-refusal.
    plan = build_plan({"vault_dir": "/example"},
                      candidate_answers={"cv_forenames": "Ada O'Example"})
    assert parse_frontmatter(plan.candidate_text)["forenames"] == "Ada O'Example"


def test_collect_candidate_asks_exactly_the_five_identity_questions():
    asked = []
    plan_answers = collect_candidate(_RecordingAsker(asked))
    assert len(asked) == 5
    assert set(plan_answers) == {"cv_forenames", "cv_surname", "cv_email",
                                 "cv_mobile", "cv_linkedin"}
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Add `collect_candidate` to `onboard/ask.py`**

Mirror `collect_profile`'s shape exactly — a separate interview returning its own dict, never a slice of the shared `answers` dict. That separateness is what makes the gate independent.

```python
_CANDIDATE_PROMPTS = (
    ("cv_forenames", "What forename(s) should appear on a tailored CV?"),
    ("cv_surname",   "And the surname?"),
    ("cv_email",     "Email address for the CV header?"),
    ("cv_mobile",    "Phone number for the CV header?"),
    ("cv_linkedin",  "LinkedIn or personal site URL (blank to skip)?"),
)


def collect_candidate(asker):
    """The five identity fields cv/ composes with. A SEPARATE interview from
    collect_profile, returning its own dict -- exactly like collect_profile, and
    for the same reason: cmd_init gates each interview on the artefact IT writes,
    and a shared dict would couple the two gates."""
    return {key: asker.ask_text_plain(prompt) for key, prompt in _CANDIDATE_PROMPTS}
```

- [ ] **Step 4: Add `_render_candidate` and the round-trip guard to `onboard/plan.py`**

```python
class FrontmatterRoundTripError(ValueError):
    """A candidate answer does not survive core/vault.py's frontmatter reader."""


_CANDIDATE_KEY_BY_ANSWER = {
    "cv_forenames": "forenames", "cv_surname": "surname", "cv_email": "email",
    "cv_mobile": "mobile", "cv_linkedin": "linkedin",
}


def _render_candidate(candidate_answers):
    """Every one of the 36 keys present; answered ones carry their value, the rest
    are present-but-empty (the spec's "undeclared" shape).

    Every value is verified through core/vault.py's REAL reader before this
    function returns. There is deliberately no escaping scheme: _fm_dict strips
    surrounding quotes and unescapes nothing, so any scheme invented here would be
    a second implementation of its rules and would drift from them. Refusing what
    does not survive is both simpler and exact.
    """
    answers = candidate_answers or {}
    values = {field: "" for field in _CANDIDATE_FIELD_ORDER}
    for answer_key, field in _CANDIDATE_KEY_BY_ANSWER.items():
        values[field] = (answers.get(answer_key) or "").strip()
    lines = ["---"] + [f"{k}: {v}" for k, v in values.items()] + ["---", ""]
    lines += [
        "# Candidate Profile",
        "",
        "The identity and application-form data sluice fills forms with. Edit it in",
        "Obsidian whenever something changes; the next run picks it up with no code",
        "change. Every field above is optional: an empty one is simply never offered",
        "to a form, and sluice never guesses a value it was not given.",
        "",
        "`cv run` needs at least one name part and at least one contact channel",
        "before it will compose. Everything else feeds `apply prep`.",
        "",
        "See also: [[Judging Profile]].",
        "",
    ]
    text = "\n".join(lines)
    parsed = parse_frontmatter(text)
    for field, wanted in values.items():
        if parsed.get(field, "") != wanted:
            raise FrontmatterRoundTripError(
                f"the answer for {field!r} does not survive sluice's frontmatter "
                f"reader (leading/trailing quotes and control characters are lost). "
                f"Re-enter it without those characters.")
    return text
```

> **Implementer note:** `_CANDIDATE_FIELD_ORDER` is the 36 field names in the order they
> appear on `CandidateProfile`. Derive it — `[f.name for f in dataclasses.fields(CandidateProfile)]`
> — do not hand-list it.

- [ ] **Step 5: Extend `build_plan`**

Add `candidate_answers=None` and set `plan.candidate_text = _render_candidate(candidate_answers)` on the returned `InitPlan`. Add the field to the `InitPlan` dataclass.

- [ ] **Step 6: Run the tests, full suite, lint**

- [ ] **Step 7: Commit**

```bash
git add sluice/onboard/ tests/test_onboard_candidate.py
git commit -m "feat(onboard): render a Candidate Profile note from a verified interview"
```

---

### Task 7: Wire the interview into `cmd_init`

**Files:**
- Modify: `sluice/cli.py:1073-1158`
- Test: `tests/functional/test_init.py`

**Interfaces:**
- Consumes: `has_any_declared` (Task 1), `read_candidate_profile` (Task 2), `collect_candidate` / `build_plan(candidate_answers=)` (Task 6).
- Produces: `cmd_init` writes `CANDIDATE_PROFILE_RELPATH` under the same conditions as `CRITERIA_RELPATH`, plus one deliberate difference (the conditional write).

**Read `sluice/cli.py:1015-1174` in full before starting.** Three prior design rounds each specified this wrong by reasoning from prose.

- [ ] **Step 1: Write the failing tests**

Add to `tests/functional/test_init.py`, using the existing `initdriver` harness:

```python
def test_no_input_writes_no_candidate_profile_note(init_driver, tmp_path):
    """--no-input runs no interview, so there are no answers, so nothing is written.

    Writing an all-blank note here is the DEADLOCK: the note would exist so
    write_document(only_if_absent=True) refuses forever, but has_any_declared stays
    False so the gate never closes -- every later run re-asks, parks the answers in
    .init-scaffold.md, and the run after that reports `failed` with the real note
    still empty."""
    init_driver.run("--no-input", "--vault", str(tmp_path / "vault"))
    assert not (tmp_path / "vault" / CANDIDATE_PROFILE_RELPATH).exists()


def test_a_second_no_input_run_still_writes_none(init_driver, tmp_path):
    init_driver.run("--no-input", "--vault", str(tmp_path / "vault"))
    init_driver.run("--no-input", "--vault", str(tmp_path / "vault"))
    assert not (tmp_path / "vault" / CANDIDATE_PROFILE_RELPATH).exists()


def test_an_interactive_run_with_every_identity_question_skipped_writes_none(init_driver, tmp_path):
    init_driver.run_interactive(answers=_skip_all_questions(), vault=tmp_path / "vault")
    assert not (tmp_path / "vault" / CANDIDATE_PROFILE_RELPATH).exists()


def test_a_populated_note_is_left_alone_and_the_questions_are_not_re_asked(init_driver, tmp_path):
    vault = tmp_path / "vault"
    _seed_candidate_note(vault, {"forenames": "Ada", "email": "ada@example.invalid"})
    before = (vault / CANDIDATE_PROFILE_RELPATH).read_bytes()
    asked = init_driver.run_interactive(answers=_answer_everything(), vault=vault)
    assert (vault / CANDIDATE_PROFILE_RELPATH).read_bytes() == before
    assert not any(q.startswith("cv_") for q in asked), "the interview must be gated"


def test_a_note_declaring_only_email_still_closes_the_gate(init_driver, tmp_path):
    """has_any_declared, not full_name: a user who answered only `email` has a note
    that exists and is useful. A full_name probe would re-ask forever."""
    vault = tmp_path / "vault"
    _seed_candidate_note(vault, {"email": "ada@example.invalid"})
    asked = init_driver.run_interactive(answers=_answer_everything(), vault=vault)
    assert not any(q.startswith("cv_") for q in asked)


def test_a_write_collision_after_the_interview_parks_the_answers_in_the_spare(init_driver, tmp_path):
    vault = tmp_path / "vault"
    result = init_driver.run_interactive(answers=_answer_everything(), vault=vault,
                                         collide_on=CANDIDATE_PROFILE_RELPATH)
    spare = CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    assert (vault / spare).exists()
    assert spare in result.stdout


def test_both_the_note_and_the_spare_occupied_reports_failed(init_driver, tmp_path):
    vault = tmp_path / "vault"
    spare = CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    result = init_driver.run_interactive(answers=_answer_everything(), vault=vault,
                                         collide_on=(CANDIDATE_PROFILE_RELPATH, spare))
    assert "FAILED" in result.stderr
    assert "were NOT saved" in result.stderr
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Add the probe beside `profile_exists`**

At `cli.py:1074`, after `profile_exists = bool(store.read_criteria())`:

```python
    # Computed INDEPENDENTLY of profile_exists and of config_exists, and computed
    # HERE because it needs `store`, which needs vault_dir, which is itself an
    # answer from the collect() above. This is also why the five identity questions
    # are NOT catalogue questions: line 1039 filters the catalogue to vault_dir
    # alone when a config exists, so a migrating user (config present, note absent)
    # would be asked nothing and get a bare note written -- verbatim the bug the
    # comment below records as already fixed once for the Judging Profile.
    candidate_exists = has_any_declared(store.read_candidate_profile())
    candidate_dest = os.path.join(vault_dir, CANDIDATE_PROFILE_RELPATH)
    candidate_answers = {}
```

- [ ] **Step 4: Add the interview beside `collect_profile`**

At `cli.py:1103-1104`, inside the existing `if interactive:` block:

```python
        if not candidate_exists:
            candidate_answers = collect_candidate(asker)
```

- [ ] **Step 5: Pass it to `build_plan`**

```python
    plan = build_plan(answers, profile_answers=profile_answers,
                      candidate_answers=candidate_answers, sources=sources)
```

- [ ] **Step 6: Add the conditional write block after the Judging Profile block**

```python
    # CONDITIONAL, unlike the Judging Profile above -- the one deliberate
    # difference. _render_profile always emits headings plus DEFAULT_CRITERIA's own
    # prose, so bool(read_criteria()) is True on the next run and that gate closes.
    # An all-blank Candidate Profile frontmatter block has no such content: writing
    # one would leave has_any_declared False FOREVER, so the note exists (the write
    # refuses) but the gate never closes -- every later run re-asks, parks the
    # answers in the spare, and the run after that reports `failed` with the real
    # note still empty. Gating the write on "at least one declared answer" makes the
    # write gate and the existence probe the SAME predicate on both sides of the
    # round trip, which is what makes that deadlock impossible rather than unlikely.
    if any((v or "").strip() for v in candidate_answers.values()):
        try:
            os.makedirs(vault_dir, exist_ok=True)
            handle = store.write_document(CANDIDATE_PROFILE_RELPATH, plan.candidate_text,
                                          only_if_absent=True)
            if handle:
                written.append(handle)
            else:
                skipped.append(candidate_dest)
                # Same rescue as CRITERIA_RELPATH's: the user typed answers and the
                # note turned up already there. Do not overwrite, do not bin them.
                spare = CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
                if store.write_document(spare, plan.candidate_text, only_if_absent=True):
                    written.append(os.path.join(vault_dir, spare))
                else:
                    failed.append(f"{os.path.join(vault_dir, spare)}: already exists, so "
                                  f"the answers you just typed were NOT saved -- copy them "
                                  f"out of the terminal, or move that file aside and re-run")
        except OSError as exc:
            failed.append(f"{candidate_dest}: {exc}")
```

- [ ] **Step 7: Handle `FrontmatterRoundTripError` from `build_plan`**

`build_plan` now raises when an answer will not survive the reader. Catch it around the `build_plan` call and, when `interactive`, re-prompt for the offending field; otherwise append to `failed` and continue. Never write silently.

- [ ] **Step 8: Run the tests, full suite, lint**

- [ ] **Step 9: Commit**

```bash
git add sluice/cli.py tests/functional/test_init.py
git commit -m "feat(onboard): gate the candidate interview on the note it writes"
```

---

### Task 8: The doctor check, fed by `preflight()`

**Files:**
- Modify: `sluice/core/vault.py:1306+` (`preflight` gains two facts)
- Modify: `sluice/core/doctor.py:314+` (`classify_store` gains the check)
- Modify: `sluice/core/app.py:1748` (guard the `load_cv_config()` call)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `full_name`, `contact_block` (Task 1); `read_candidate_profile` (Task 2).
- Produces: `preflight()` facts `candidate_name_present: bool` and `candidate_contact_present: bool`; a `ComponentCheck("store", "Candidate Profile", ...)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_blank_candidate_profile_is_dead_and_blocks_cv():
    checks = classify_store({"vault_exists": True, "baseline_exists": True,
                             "criteria_present": True, "experience_verified": 3,
                             "candidate_name_present": False,
                             "candidate_contact_present": False})
    c = _one(checks, "Candidate Profile")
    assert c.severity is DEAD
    assert c.blocks == ("cv",)


def test_a_declared_name_with_blank_contact_is_still_dead():
    checks = classify_store({"vault_exists": True, "baseline_exists": True,
                             "criteria_present": True, "experience_verified": 3,
                             "candidate_name_present": True,
                             "candidate_contact_present": False})
    assert _one(checks, "Candidate Profile").severity is DEAD


def test_a_fully_declared_identity_is_ok():
    checks = classify_store({"vault_exists": True, "baseline_exists": True,
                             "criteria_present": True, "experience_verified": 3,
                             "candidate_name_present": True,
                             "candidate_contact_present": True})
    assert _one(checks, "Candidate Profile").severity is OK


def test_the_dead_message_does_not_nudge_disclosure_of_the_other_fields():
    """doctor reports what blocks a command. "Fill in the rest for better apply
    automation" reads as a prompt to supply ethnicity, religion, sexual orientation
    and disability to a tool telling you something is wrong."""
    c = _one(classify_store({"vault_exists": True, "baseline_exists": True,
                             "criteria_present": True, "experience_verified": 3,
                             "candidate_name_present": False,
                             "candidate_contact_present": False}), "Candidate Profile")
    lowered = c.detail.lower()
    for word in ("ethnicity", "monitoring", "equal-opportunit", "the rest", "apply"):
        assert word not in lowered


def test_preflight_reports_the_two_identity_facts(tmp_path):
    _seed_candidate_note(tmp_path, {"forenames": "Ada", "email": "ada@example.invalid"})
    facts = Vault(str(tmp_path)).preflight()
    assert facts["candidate_name_present"] is True
    assert facts["candidate_contact_present"] is True


def test_sluice_doctor_feeds_a_real_preflight_result_into_the_candidate_check(tmp_path):
    """Successor to test_sluice_doctor_wires_the_loaded_cv_config_into_cv_identity,
    whose docstring cites a real prior bug: hardcoding the classifier's inputs left
    the whole suite green while the wiring was broken."""
    _seed_candidate_note(tmp_path, {})           # present but all blank
    report = _sluice(tmp_path).doctor()
    assert _one(report.checks, "Candidate Profile").severity is DEAD
    _seed_candidate_note(tmp_path, {"forenames": "Ada", "email": "ada@example.invalid"})
    report = _sluice(tmp_path).doctor()
    assert _one(report.checks, "Candidate Profile").severity is OK


def test_doctor_reports_a_legacy_cv_name_rather_than_tracebacking(tmp_path, monkeypatch):
    """core/app.py:1748 calls load_cv_config() UNGUARDED, before the deliberately
    guarded constructions below it. Once that raises on a legacy cv.name, doctor --
    the command a user runs precisely because something is wrong -- dies on the
    migration it exists to diagnose."""
    cfg = tmp_path / "sluice.local.yaml"
    cfg.write_text('cv:\n  name: "Someone"\n')
    monkeypatch.setenv("SLUICE_CONFIG", str(cfg))
    report = _sluice(tmp_path).doctor()          # must not raise
    assert any("Candidate Profile" in c.detail or "cv.name" in c.detail
               for c in report.checks if c.severity is DEAD)
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Add the two facts to `Vault.preflight`**

```python
        profile = self.read_candidate_profile()
        facts["candidate_name_present"] = bool(full_name(profile).strip())
        facts["candidate_contact_present"] = bool(contact_block(profile).strip())
```

Computed INSIDE `preflight`, where the store already has the note. `classify_store(facts: dict)` is pure and reads primitives — it never holds a store handle, so this is the only wiring that works without reshaping it.

- [ ] **Step 4: Add the check to `classify_store`**

```python
    if not (facts.get("candidate_name_present") and facts.get("candidate_contact_present")):
        out.append(ComponentCheck(
            "store", "Candidate Profile", DEAD,
            "no name or no contact details -- cv run refuses to compose "
            "(skipped-config) before any backend call", blocks=("cv",)))
    else:
        out.append(ComponentCheck("store", "Candidate Profile", OK, "found"))
```

Message names ONLY what blocks `cv`. It must not mention the other 31 fields.

- [ ] **Step 5: Guard `load_cv_config()` in `core/app.py:1748`**

Wrap it in the same try/except-and-report shape its neighbours already use, turning the migration `ValueError` into a DEAD `ComponentCheck` rather than a traceback.

- [ ] **Step 6: Run the tests, full suite, lint**

- [ ] **Step 7: Commit**

```bash
git add sluice/core/vault.py sluice/core/doctor.py sluice/core/app.py tests/test_doctor.py
git commit -m "feat(doctor): report the Candidate Profile and survive the cv config migration"
```

---

### Task 9: Remove `CvConfig.name` / `CvConfig.contact` (atomic)

**Files (find the full set by grep — the list below is known non-obvious hits, not the whole set):**
- Modify: `sluice/cv/config.py`, `sluice/core/doctor.py`, `sluice/onboard/questions.py`, `sluice/cli.py:675-686`
- Modify: `tests/harness/config.py`, `tests/test_sluice_neutral_defaults.py`, `tests/test_onboard_emit.py`, `tests/functional/test_cv.py`, `tests/test_doctor.py:63`, `tests/test_cv_config.py:10`, `tests/test_app_injection.py:317`, `tests/test_cv_engine.py:812-830`, `tests/test_onboard_plan.py:175`

**This is the one task where the suite is red mid-task. It is atomic by necessity: removing the fields breaks every reader at once.**

- [ ] **Step 1: Find the complete change surface**

```bash
git grep -n -E 'cv\.name|cv\.contact|cvcfg\.name|cvcfg\.contact|CvConfig\(|Your Name|classify_cv_identity'
```

Every hit is in scope. Three prior rounds hand-listed this and all three lists were incomplete — trust the grep, not this plan.

- [ ] **Step 2: Remove the fields and add the migration guard to `cv/config.py`**

Delete `name: str = "Your Name"` (:24) and `contact: str = ""` (:29). Add, beside the existing `baseline_rel` guard at :131:

```python
    for moved in ("name", "contact"):
        if moved in data:
            raise ValueError(
                f"cv.{moved} has moved to the vault. sluice now reads your identity "
                f"from 'Job Applications/Candidate Profile.md' (frontmatter keys: "
                f"forenames, surname, email, mobile, linkedin). Remove cv.{moved} "
                f"from the `cv:` block and put the value in that note."
            )
```

Keys on `in data`, **not** on the value being truthy: `cv/config.py` already carries both spellings deliberately, and a `cv.name: ""` left behind by a half-finished migration must be as loud as a populated one.

- [ ] **Step 3: Remove `classify_cv_identity` and its call site**

Delete `core/doctor.py:284-312` and the call in `core/app.py`'s doctor assembly. Task 8's `store`/`Candidate Profile` check replaces it.

- [ ] **Step 4: Remove the two onboarding questions**

Delete `cv_name` (:142) and `cv_contact` (:144) from `onboard/questions.py`'s `catalogue()`. They are replaced by `collect_candidate`, not by new catalogue entries.

- [ ] **Step 5: Fix the user-facing string at `sluice/cli.py:683`**

It currently prints `cv: cv.name is still the shipped placeholder 'Your Name' -- set ...`. Point it at the vault note instead. **This is shipped code, not docs** — three doc-focused sweeps missed it because it reads like documentation.

- [ ] **Step 6: Stop the harness emitting `cv.name`**

`tests/harness/config.py:195` writes `"name": cv_name` into the emitted `cv:` block on **every** `build_harness` call — the new guard would redden the whole e2e and functional tier at once. Remove the key and the `cv_name` parameter. Nothing replaces it: the identity now comes from the store the harness already builds. Where a harness test needs an identity, seed the Candidate Profile note.

- [ ] **Step 7: Retarget the two neutral-defaults tests**

- `test_cv_defaults_carry_no_pii`: drop the two `CvConfig().name`/`.contact` assertions.
- `test_config_overlay_restores_neutralized_defaults` (:101-128) writes `cv:\n  name: "Someone"` and asserts the round-trip. It exists to prove neutralized defaults cost no override capability, so **retarget, don't delete**: keep the `cv:` block proving a still-live key round-trips (`negatives`, `prefix_map`), and add a sibling proving the same property for the vault note — a declared `forenames`/`surname` comes back out of `read_candidate_profile()`.

- [ ] **Step 8: Retarget `tests/test_onboard_emit.py`'s control-character test**

`test_a_control_character_survives_the_whole_config_render` rides on `cv_contact` and is the only end-to-end proof that an arbitrary paste reaching `init` survives the emitter. Retarget it onto the **frontmatter** emitter, where it becomes the regression test for Task 6's round-trip verification. The hostile-input case is now more load-bearing, not less.

- [ ] **Step 9: Retire, don't substitute, the sentinel tests**

- `tests/test_cv_engine.py:812-830` `test_the_shipped_default_name_is_refused_before_any_spend` — retired. It exercises the non-blank `"Your Name"` sentinel, an expression `CvConfig` no longer has. Task 3's blank-`full_name` test replaces its coverage.
- `tests/test_doctor.py`'s two `classify_cv_identity` unit tests and the report-level `cv.name`/`cv.contact` assertions — retired, replaced by Task 8's equivalents.
- `tests/functional/test_cv.py::test_cv_run_shipped_default_name_returns_1` — **kept**, retargeted. It is the same property at the CLI layer and its docstring states the split; an engine-level test cannot establish `rc == 1`.

- [ ] **Step 10: Fix the remaining mechanical hits**

`tests/test_doctor.py:63` (`dataclasses.replace(CvConfig(), name=..., contact=...)` in the autouse `_harmless_components` fixture — a `TypeError` erroring all 63 tests in the file), `tests/test_cv_config.py:10`, `tests/test_app_injection.py:317`, `tests/test_onboard_plan.py:175`.

- [ ] **Step 11: Run the full suite and lint**

Run: `python -m pytest && ruff check sluice tests scripts`
Expected: green. If anything is still red, the grep in Step 1 missed it — re-run it.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat(cv)!: move cv.name and cv.contact to the vault

BREAKING CHANGE: cv.name and cv.contact are removed from sluice.yaml. Identity
now lives in Job Applications/Candidate Profile.md. A config still carrying
either key raises at load with the destination named."
```

---

### Task 10: The protected-characteristic fixture ratchet

**Files:**
- Modify: `tests/test_fixture_name_neutrality.py` (add a fifth collector)

**Interfaces:**
- Consumes: the `SYNTHETIC-<FIELD>-<N>` tokens introduced in Task 4's fixtures.
- Produces: a standing guard that a real demographic value cannot land in `tests/` unnoticed.

- [ ] **Step 1: Write the failing test**

```python
def test_every_equal_opportunities_fixture_value_is_an_obvious_synthetic_token():
    """Nothing local can tell a real demographic category from an invented one, so
    the token SHAPE is what makes this reviewable -- the same ratchet logic as
    _REVIEWED_FIXTURE_IDENTITIES, applied to a category that roster does not cover.

    Prose ("obviously-synthetic placeholders, documented as such") is not a check.
    """
    found = _collect(_EO_PATTERN)
    bad = sorted(v for v in found if not _SYNTHETIC_TOKEN.match(v))
    assert not bad, (
        "equal-opportunities fixture values must look like SYNTHETIC-<FIELD>-<N>:\n  "
        + "\n  ".join(bad))
```

- [ ] **Step 2: Add the collector to `_COLLECTORS`**

```python
_SYNTHETIC_TOKEN = re.compile(r"^SYNTHETIC-[A-Z_]+-\d+$")
_EO_FIELDS = ("gender_identity", "identifies_as_trans", "ethnicity", "religion",
              "sexual_orientation", "preferred_pronouns", "disability",
              "neurodivergent", "open_about_orientation_at_work")
_EO_PATTERN = re.compile(
    r"""["'](?:""" + "|".join(_EO_FIELDS) + r""")["']\s*:\s*["']([^"']+)["']""")

_COLLECTORS = (
    ...,                                    # the four existing entries, unchanged
    ("equal-opportunities values", _EO_PATTERN),
)
```

Adding it to `_COLLECTORS` gives it the file's existing `test_every_collector_actually_finds_fixtures` parametrization for free — **that is the anti-vacuity guard**, and it is why the collector goes in the tuple rather than standing alone. A sweep that matches nothing passes every assertion over it.

- [ ] **Step 3: Run the tests, full suite, lint**

- [ ] **Step 4: Commit**

```bash
git add tests/test_fixture_name_neutrality.py
git commit -m "test(neutrality): ratchet the equal-opportunities fixture values"
```

---

### Task 11: The `CandidateProfile` neutral-defaults guard, and the docs sweep

**Files:**
- Modify: `tests/test_sluice_neutral_defaults.py`
- Modify: `sluice.yaml.example`, `.rulesync/rules/CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/USAGE.md`, `docs/CONFIGURATION.md`, `docs/TROUBLESHOOTING.md`, `README.md`

- [ ] **Step 1: Add the derived PII guard**

```python
def test_candidate_profile_defaults_carry_no_pii():
    """DERIVED, not hand-listed: field 37 is covered the day it is added. Hand-listing
    36 names is the enumeration failure this file's own comments already record twice.

    CandidateProfile cannot simply join _SWEPT_CONFIGS:
    test_swept_configs_covers_every_config_dataclass asserts
    `discovered == set(_SWEPT_CONFIGS)` as an EQUALITY against
    _discover_config_dataclasses(), which globs sluice/**/config.py for *Config --
    so appending a class that lives in core/protocols.py and is not named *Config
    reddens THAT guard instead. This is its own derived sweep beside the others.
    """
    fields = dataclasses.fields(CandidateProfile())
    # Scope assertion FIRST: a broken dataclasses.fields() call returning [] would
    # make every assertion below vacuously true. This is a negative guard, so
    # finding nothing is the success case and cannot be the completeness check.
    assert len(fields) == 36
    for f in fields:
        assert f.default == "", f"{f.name} ships a non-empty default"
```

- [ ] **Step 2: Run the docs sweep**

```bash
git grep -n -E 'cv\.name|cv\.contact|cvcfg\.name|cvcfg\.contact|Your Name'
```

Expected after Task 9: hits only in docs and `sluice.yaml.example`.

- [ ] **Step 3: Update each hit**

- `sluice.yaml.example`: replace the `name:`/`contact:` catalogue lines (~:193-197) with a comment pointing at `Job Applications/Candidate Profile.md`.
- `.rulesync/rules/CLAUDE.md`: update the #99/#100 section's `cvcfg.name`/`cvcfg.contact` references to `full_name(profile)`/`contact_block(profile)`, and drop the `"Your Name"` sentinel description — the new check is a direct blank check.
- `docs/ARCHITECTURE.md`, `docs/USAGE.md`: the `doctor` description, and `USAGE`'s `apply prep` entry (the new `DETAILS`/`MONITORING` blocks and the `--json` note).
- `docs/CONFIGURATION.md`: remove the `cv.name`/`cv.contact` rows, document the vault note and its 36 keys.
- `README.md`: the fabrication-gate passage naming `cv.name`.
- `docs/TROUBLESHOOTING.md`: the preflight description and the placeholder-name fix instruction.

- [ ] **Step 4: Regenerate the rulesync outputs**

```bash
npm ci --ignore-scripts && npm run rulesync
```

`.rulesync/` is canonical; `CLAUDE.md`/`AGENTS.md`/`.claude/` are generated and gitignored. CI fails the build on drift.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest && ruff check sluice tests scripts`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: describe the vault-sourced candidate identity"
```

---

## Self-Review

**Spec coverage:** every spec section maps to a task — fields/derivations → 1; Store contract + conformance → 2; `cv/` changes → 3, 9; `apply/` changes incl. `render_text` → 4, 5; `doctor.py` changes → 8, 9; `sluice init` → 6, 7; docs and example config → 11; the neutrality guards → 10, 11; the frontmatter round-trip → 2, 6.

**Type consistency:** `full_name`/`contact_block`/`age_from_dob`/`has_any_declared` keep one signature throughout. `build_packet(note, cfg, *, profile, today, cv_staged)` is keyword-only from Task 4 onward and every call site is updated in the task that introduces it.

**Known red window:** Task 9 only. Tasks 1-8 are additive and end green; Task 9 removes the fields and repairs every reader in one commit.
