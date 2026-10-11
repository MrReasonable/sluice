# Blind CV hold and a lead-supplied job description (#369) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cv run` holds a CV composed against no job description for sign-off instead of serving it, and a lead note's own `## Job description` section is read by triage and cv in place of a fetch, and can be written by `leads add` / MCP `create_lead`.

**Architecture:** One pure module, `sluice/core/jdsection.py`, owns the section grammar (read and render). The dossier cache gains a `supplied_jd` arm that never fetches or caches; cv and triage read the section and pass it only when it has text. cv reports where its JD came from (`jd_source`) and adds a `blind\t<reason>` blocker under a new `cv.blind_hold` key (default on), which rides the existing `hold_for_signoff` path. The create path appends the section through the existing `Store.append_body_section`, using a new `UpsertResult.ref`.

**Tech Stack:** Python 3.12+ stdlib, pytest, seeded faker fixtures.

**Spec:** `docs/superpowers/specs/2026-10-11-cv-blind-hold-design.md` (commit `453c6135`). Read it beside this plan; where the two disagree, stop and ask.

## Global Constraints

- `sluice/` is standard-library only; no new dependency.
- Fixtures are synthetic: JD text is placeholder prose, companies from `Example ...` names already used in each file, job titles from the `titles`/`cfg_titles` fixtures wherever the title reaches a gate (triage accept/reject); elsewhere the file's existing generic title (`Analyst`, `Example Role`) is kept, matching each file's idiom; no contact, salary or city line anywhere.
- Never cite a line number in a comment or docstring; cite `file.py::symbol`.
- Comments explain why, at the density of the surrounding code.
- Conventional Commits. Every commit body ends with the line `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`. No `!` anywhere (spec Decisions: `feat(cv)`, not breaking).
- Hold reasons are exactly these three phrases, never exception text:
  `the job description fetch failed` / `no job description arrived` /
  `the job description in the note is shorter than min_jd_chars (N)`.
- `CreateLeadResult.job_description_note` is exactly one of:
  `""` / `the lead already existed, or was not created` /
  `the store did not return a reference to the new note` /
  `a job description section was already present` /
  `the note changed before the job description could be added`.
- `jd_source` is exactly one of `"note"`, `"fetched"`, `"none"`, or `""` (the lead never reached the dossier step).
- Mutation witnesses: commit first, run `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`, mutate by MOVE or DELETE only, run the named row, see it red for the named reason, then `git checkout -- <file>` and re-run green. A witness that deletes a line also deletes anything that line's removal would leave syntactically or semantically dangling (an empty `if` body, a later read of the deleted name), so the row goes red for the NAMED reason, never on an `IndentationError` or `UnboundLocalError`.
- `.rulesync/rules/CLAUDE.md` is canonical; never edit the generated `CLAUDE.md`. Each claim lands in the task that makes it true.

## Review Focus

1. **A hand-typed heading with punctuation** (`## Job Description:`) is NOT read as the section (the grammar is exact, case-insensitive). A user expects either it to work or to be told. Pinned as a non-match in Task 1 and stated in `docs/USAGE.md` in Task 3.
2. **A `skipped-gate` result for a blind lead** still carries `jd_source` and `dossier_failed`, so a run that bins the lead says why it was blind. Pinned in Task 3.
3. **An early refusal** (`skipped-selection`, `skipped-stale`, `skipped-config`, `skipped-needs-signoff`) reports `jd_source == ""`, never a misleading `none`. Pinned in Task 3.
4. **A held blind lead re-run after `cv signoff --discard` with a still-too-short section** is held again, with the short-section phrase, not served. Pinned in Task 4.
5. **`leads add --jd-file` re-run for a lead that already exists** prints the not-written note and points at the hand edit, exit code unchanged (`updated` → 0). Pinned in Task 7.

---

### Task 0: Worktree environment

The worktree has no `.venv`. The main checkout's venv has an editable install of the MAIN tree, so using it here would test the wrong code.

- [ ] **Step 1: Create the venv**

```bash
# from the worktree root (.claude/worktrees/cv-blind-369)
uv venv .venv
uv pip install --python .venv/bin/python -e ".[test]" ruff==0.15.21
```

- [ ] **Step 2: Prove it imports this tree**

Run: `.venv/bin/python -I -c "import sluice, os; print(os.path.realpath(sluice.__file__))"`
Expected: a path under `.claude/worktrees/cv-blind-369/sluice/`.

- [ ] **Step 3: Baseline**

Run: `.venv/bin/python -m pytest -q -x -p no:cacheprovider 2>&1 | tail -3`
Expected: all pass. Record the count. Nothing to commit.

---

### Task 1: `core/jdsection.py` — the section grammar

**Files:**
- Create: `sluice/core/jdsection.py`
- Test: `tests/test_jdsection.py`

**Interfaces:**
- Produces: `HEADING: str = "## Job description"`, `TAG: str = "<!-- sluice:job-description -->"`, `breaks_section(line: str) -> bool`, `read(body: str) -> tuple[str, int]`, `render(text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
"""core/jdsection.py: the ONE grammar for a lead note's `## Job description` section (#369).

The reader (where reading stops) and the writer (what writing must demote) share
`breaks_section`, so a drift between them cannot silently truncate a posting."""
import pytest

from sluice.core import jdsection
from sluice.core.jdsection import HEADING, TAG, breaks_section, read, render

_JD = "We are hiring an engineer.\nYou will build placeholder systems."


@pytest.mark.parametrize("line", ["#", "##", "# X", "## X", "#\tX", "  ## X", "   # X",
                                  "##   "])
def test_level_one_and_two_atx_headings_break(line):
    assert breaks_section(line)


@pytest.mark.parametrize("line", ["### X", "#tag", "##tag", "    ## X", "", "text ## X",
                                  "#### X"])
def test_other_lines_do_not_break(line):
    assert not breaks_section(line)


def test_no_section_reads_empty_and_zero():
    assert read("# Title\n\nbody text\n") == ("", 0)


def test_an_empty_section_reads_empty_and_one():
    assert read(f"{HEADING}\n\n## Next\nmore\n") == ("", 1)


@pytest.mark.parametrize("heading", [
    HEADING, "## job description", "## JOB DESCRIPTION", f"{HEADING}   ",
    f"{HEADING} {TAG}", f"## Job Description {TAG}  "])
def test_heading_spellings_that_read(heading):
    assert read(f"intro\n{heading}\n{_JD}\n") == (_JD, 1)


def test_a_heading_with_trailing_punctuation_is_not_the_section():
    # Review Focus 1: the grammar is exact (case-insensitive). docs/USAGE.md says so.
    assert read(f"## Job Description:\n{_JD}\n") == ("", 0)


@pytest.mark.parametrize("ender", ["# X", "## X", "#\tX", "  ## X", "##"])
def test_the_section_ends_at_a_level_one_or_two_heading(ender):
    assert read(f"{HEADING}\n{_JD}\n{ender}\nnot part of it\n") == (_JD, 1)


@pytest.mark.parametrize("inner", ["### Requirements", "#hashtag", "    ## indented code"])
def test_the_section_does_not_end_at_other_lines(inner):
    text, count = read(f"{HEADING}\n{_JD}\n{inner}\ntail line\n")
    assert text == f"{_JD}\n{inner}\ntail line" and count == 1


@pytest.mark.parametrize("eol", ["\r\n", "\r"])
def test_crlf_and_lone_cr_bodies_read_like_lf(eol):
    body = f"intro{eol}{HEADING}{eol}{_JD.replace(chr(10), eol)}{eol}## Next{eol}x{eol}"
    assert read(body) == (_JD, 1)


def test_the_section_runs_to_end_of_body():
    assert read(f"{HEADING}\n{_JD}") == (_JD, 1)


def test_two_sections_return_the_first_text_and_count_two():
    body = f"{HEADING}\nfirst one\n## Other\n{HEADING}\nsecond one\n"
    assert read(body) == ("first one", 2)


def test_render_shape():
    assert render(_JD) == f"{HEADING} {TAG}\n\n{_JD}"


# Every spelling `read` breaks on, plus the heading itself. A `render` that demoted only
# `startswith(("# ", "## "))` lines would leave `#\tX`, a bare `##` and `  ## X` live, and
# `read` would then stop at them -- silently truncating the posting.
_EDGE = "\n".join(["Intro line.", "# Top", "## Mid", "#\tTabbed", "##", "  ## Indented",
                   HEADING, "Closing line."])


def test_render_then_read_round_trips_every_breaking_spelling():
    text, count = read(render(_EDGE))
    assert count == 1
    assert text == "\n".join(["Intro line.", "### Top", "### Mid", "###\tTabbed", "###",
                              "  ### Indented", "### Job description", "Closing line."])


def test_render_leaves_non_breaking_lines_alone():
    t = "### Already three\n#hashtag\n    ## code"
    assert render(t) == f"{HEADING} {TAG}\n\n{t}"


def test_render_normalises_line_endings():
    assert render("a\r\nb\rc") == f"{HEADING} {TAG}\n\na\nb\nc"


def test_module_is_pure():
    # No I/O imports: the module is shared by core, triage and cv.
    src = open(jdsection.__file__, encoding="utf-8").read()
    for banned in ("import os", "open(", "import sys"):
        assert banned not in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_jdsection.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'sluice.core.jdsection'`.

- [ ] **Step 3: Implement**

```python
"""The ONE grammar of a lead note's `## Job description` section (#369).

A lead with no posting to fetch (a referral, an InMail) carries its job description in the
note body instead, under `HEADING`. Two sides read this grammar -- the reader, deciding where
the section stops, and the writer (`Sluice.create_lead`), deciding what in pasted text must be
neutralised so the reader does not stop early. They share `breaks_section` and live in one
module because two copies of "which line ends the section" would drift, and a drift silently
truncates the posting the composer and the judge are shown.

Pure and stdlib-only, beside `core/names.py` and `core/tokens.py` for the same reason they
sit in `core/`: triage and cv both import it, and neither may import the other.

Residuals, stated: setext headings (a line underlined with `===`/`---`) and fenced code are
not parsed. Setext is harmless -- the reader does not treat it as a break, so nothing is
truncated. A `## ` line inside a fence DOES break on read and IS demoted on write, so the two
sides agree and the writer never produces a section the reader truncates.
"""
import re

HEADING = "## Job description"
# Why a tag as well as the heading: `Store.append_body_section`'s idempotence is "the tag
# appears anywhere in the fresh file". The heading text alone could appear in a frontmatter
# title or a pasted body, turning the append into a no-op on a note with no section. An HTML
# comment is invisible in Obsidian's reading view, and is what `track/reconcile.py`'s tags
# already look like.
TAG = "<!-- sluice:job-description -->"

# A CommonMark ATX heading of level 1 or 2: up to three leading spaces, one or two `#`, then a
# space, a tab, or end of line. Four spaces is an indented code block, `###` is a level-3
# heading inside the posting, and `#tag` is an Obsidian tag -- none of those end the section.
_BREAK = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")
_HASHES = re.compile(r"^( {0,3})#{1,2}")


def _lines(text: str) -> list:
    # CRLF and lone CR both normalised, so a note saved on another platform reads the same.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def breaks_section(line: str) -> bool:
    """True for a level-1 or level-2 ATX heading: the line that ends the section."""
    return bool(_BREAK.match(line))


def _is_heading(line: str) -> bool:
    bare = line.rstrip()
    if bare.endswith(TAG):
        bare = bare[: -len(TAG)].rstrip()
    return bare.casefold() == HEADING.casefold()


def read(body: str) -> tuple:
    """`(text, count)`: the first section's text, stripped, and how many `HEADING` lines the
    body holds -- so the CALLER can warn about duplicates. `("", 0)` with no section. A
    hand-typed heading without the tag reads the same as a written one; a heading with any
    other text on its line (`## Job description:`) is not the section."""
    lines = _lines(body)
    starts = [i for i, line in enumerate(lines) if _is_heading(line)]
    if not starts:
        return "", 0
    out = []
    for line in lines[starts[0] + 1:]:
        if breaks_section(line):
            break
        out.append(line)
    return "\n".join(out).strip(), len(starts)


def render(text: str) -> str:
    """The section to append: the tagged heading, a blank line, then `text` with every
    breaking line demoted to level 3 (its leading `#` run replaced by `###`, indentation
    kept). Heading levels change; nothing is refused. A `## Job description` line inside the
    text is demoted like any other, so a written note carries exactly one section."""
    demoted = [_HASHES.sub(r"\1###", line) if breaks_section(line) else line
               for line in _lines(text)]
    return f"{HEADING} {TAG}\n\n" + "\n".join(demoted)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_jdsection.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check sluice tests scripts
git add sluice/core/jdsection.py tests/test_jdsection.py
git commit -F - <<'EOF'
refactor(core): add the job-description section grammar (#369)

One module owns where a note's "## Job description" section stops and
what pasted text must be demoted so it cannot stop early.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 6: Witnesses** (commit is in place; compileall first, then each mutation, then `git checkout -- sluice/core/jdsection.py`)

1. In `render`, replace `if breaks_section(line)` with `if line.startswith(("# ", "## "))`. Run `test_render_then_read_round_trips_every_breaking_spelling`: red (the text is truncated at `#\tTabbed`).
2. In `render`, replace the comprehension's value `_HASHES.sub(r"\1###", line) if breaks_section(line) else line` with `line`. Same row: red.

---

### Task 2: Dossier cache — the supplied arm

**Files:**
- Modify: `sluice/core/dossier.py` (`DossierCache.get_or_build`, new `_assemble`)
- Test: `tests/test_dossier_supplied_jd.py`

**Interfaces:**
- Produces: `DossierCache.get_or_build(lead: dict, *, supplied_jd: str = "") -> dict`. A non-empty `supplied_jd` returns a dossier whose `jd.markdown` is that text, with no fetcher call, no cache read and no cache write. `jd_arrived` judges it exactly as a fetched one.

- [ ] **Step 1: Write the failing tests**

```python
"""DossierCache's supplied arm (#369): a JD from the lead note wins over the cache and the
fetcher, and is never written to the cache."""
import json
import os
from datetime import datetime

import pytest

from sluice.core.dossier import DossierCache

_LEAD = {"company": "Example Foundry", "role": "Analyst", "url": "https://example.invalid/1"}
_A = "Cached posting text A, long enough to pass the floor set below."
_B = "Supplied posting text B, from the lead note itself, also long enough."
_NOW = datetime(2026, 10, 11, 12, 0, 0)


class _CountingFetcher:
    def __init__(self, markdown=""):
        self.calls, self.markdown = 0, markdown

    def __call__(self, lead):
        self.calls += 1
        return {"jd": {"markdown": self.markdown}}


def _cache(tmp_path, fetcher, floor=10):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                        clock=lambda: _NOW, min_jd_chars=floor)


def _seed_fresh_entry(cache, markdown):
    os.makedirs(cache.dir, exist_ok=True)
    path = os.path.join(cache.dir, f"{cache.cache_key(_LEAD)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "jd": {"markdown": markdown},
                   "built_at": _NOW.isoformat()}, f)
    return path


def test_supplied_jd_beats_a_fresh_cache_entry_and_leaves_it_untouched(tmp_path):
    fetcher = _CountingFetcher()
    cache = _cache(tmp_path, fetcher)
    path = _seed_fresh_entry(cache, _A)
    # CONTROL (tst-102): the entry really is fresh -- without a supplied JD the cache
    # serves A and never fetches. Without this, the row below could pass on a stale entry.
    assert cache.get_or_build(_LEAD)["jd"]["markdown"] == _A
    assert fetcher.calls == 0
    before = open(path, "rb").read()

    d = cache.get_or_build(_LEAD, supplied_jd=_B)

    assert d["jd"]["markdown"] == _B
    assert fetcher.calls == 0
    assert open(path, "rb").read() == before


def test_supplied_jd_on_an_empty_cache_fetches_nothing_and_writes_nothing(tmp_path):
    fetcher = _CountingFetcher()
    cache = _cache(tmp_path, fetcher)
    d = cache.get_or_build(_LEAD, supplied_jd=_B)
    assert d["jd"]["markdown"] == _B
    assert fetcher.calls == 0
    assert not os.path.exists(cache.dir) or os.listdir(cache.dir) == []


@pytest.mark.parametrize("floor, arrived", [(1000, False), (10, True)])
def test_min_jd_chars_applies_to_a_supplied_jd(tmp_path, floor, arrived):
    cache = _cache(tmp_path, _CountingFetcher(), floor=floor)
    assert cache.jd_arrived(cache.get_or_build(_LEAD, supplied_jd=_B)) is arrived


def test_both_arms_build_the_same_key_set(tmp_path):
    fetched = _cache(tmp_path, _CountingFetcher(_A)).get_or_build(_LEAD)
    supplied = _cache(tmp_path / "other", _CountingFetcher()).get_or_build(
        _LEAD, supplied_jd=_B)
    assert set(supplied) == set(fetched)


def test_no_supplied_jd_is_todays_behaviour(tmp_path):
    fetcher = _CountingFetcher(_A)
    cache = _cache(tmp_path, fetcher)
    assert cache.get_or_build(_LEAD)["jd"]["markdown"] == _A
    assert fetcher.calls == 1
    assert len(os.listdir(cache.dir)) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dossier_supplied_jd.py -q`
Expected: the supplied rows FAIL with `TypeError: ... unexpected keyword argument 'supplied_jd'`; `test_no_supplied_jd_is_todays_behaviour` passes.

- [ ] **Step 3: Implement**

In `sluice/core/dossier.py`, move the dict literal out of `get_or_build` into a method, keeping every comment inside the literal with it:

```python
    def _assemble(self, lead: dict, enrich: dict) -> dict:
        """The ONE builder of a dossier's key set, used by both of `get_or_build`'s arms,
        so the supplied arm (#369) cannot grow a second copy that drifts from this one."""
        jd = enrich.get("jd", {})
        return {
            "schema_version": 2,
            # ... the existing literal, every key and comment unchanged ...
            "built_at": self.clock().isoformat(),
        }

    def get_or_build(self, lead: dict, *, supplied_jd: str = "") -> dict:
        # #369: a job description the lead note carries wins over the cache AND the
        # fetcher, and is never written to the cache -- checked FIRST, before `_path` and
        # `_fresh`, so an edit to the section takes effect on the next run and a stale
        # cached fetch can never shadow it. A keyword rather than reading the note here:
        # this cache is handed frontmatter dicts and must not learn what a note is.
        # `jd_arrived` judges the result exactly as it judges a fetch, so `min_jd_chars`
        # applies to a supplied JD too.
        if supplied_jd:
            return self._assemble(lead, {"jd": {"markdown": supplied_jd}})
        path = self._path(lead)
        if self._fresh(path):
            return json.loads(open(path, encoding="utf-8").read())
        dossier = self._assemble(lead, self.fetcher(lead))
        # (the existing "Do NOT persist" comment and write block, unchanged)
        if self.jd_arrived(dossier):
            with open_for_owner(path, "w") as f:
                json.dump(dossier, f, ensure_ascii=False)
        return dossier
```

`_assemble`'s literal reads `enrich.get("glassdoor", {})`, `enrich.get("page_title", "")`, `enrich.get("structured_data", "")` exactly as before; for the supplied arm those default to empty.

- [ ] **Step 4: Run to verify it passes, plus the existing dossier suites**

Run: `.venv/bin/python -m pytest tests/test_dossier_supplied_jd.py tests/test_dossier*.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/dossier.py tests/test_dossier_supplied_jd.py
git commit -F - <<'EOF'
refactor(core): let the dossier cache take a job description it is handed (#369)

A supplied job description is returned without a fetch, a cache read or
a cache write, and is judged by the same jd_arrived floor.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 6: Witness**

Move the `if supplied_jd:` block below the `if self._fresh(path):` block. Run `test_supplied_jd_beats_a_fresh_cache_entry_and_leaves_it_untouched`: red (returns A). Restore.

---

### Task 3: cv reads the note's JD and reports `jd_source`

**Files:**
- Modify: `sluice/cv/engine.py` (`CvResult`, `_run_one`, `run_batch`), `sluice/cv/artefacts.py` (`RunArtefacts.begin`, the run.json record), `sluice/core/app.py` (`Sluice.compose_cv`'s two `except` arms), `sluice/mcpserver.py` (`cv_run`'s `out`), `sluice/cli.py` (`cmd_cv_run`)
- Modify tests: `tests/test_cv_engine.py` (`Note`), `tests/structured_cv.py` (`Note`), `tests/test_app_operations.py` (`_Note`), `tests/test_cv_run_artefacts.py` (three `begin(...)` calls), `tests/test_docs_claims.py` (the pinned key set)
- Modify docs: `docs/USAGE.md`
- Test: `tests/test_cv_jd_source.py`

**Interfaces:**
- Consumes: `jdsection.read`, `jdsection.HEADING` (Task 1); `get_or_build(fm, *, supplied_jd=...)` (Task 2).
- Produces: `CvResult.jd_source: str = ""`; `_run_one` locals `dossier_failed: bool`, `jd_source: str`, `blind_reason: str` (Task 4 reads `blind_reason`); exceptions leaving `_run_one`'s inner `try` carry `e.jd_source`; `RunArtefacts.begin(..., jd_source)`; run.json key `jd_source`; MCP `cv_run` key `jd_source`.

- [ ] **Step 1: Give the test `Note` fakes the contract's `body`**

`LeadNote` declares `body`; `_run_one` will now read it. In `tests/test_cv_engine.py`:

```python
class Note:
    def __init__(self, fm, path="Job Applications/Job Leads/Acme - Analyst.md", body=""):
        # A store hands back an opaque `ref` and the slug it issued; it never hands
        # back a path for the caller to parse. `body` is a LeadNote member, read by
        # cv/engine.py for the note's own job description (#369).
        self.fm = fm; self.ref = path; self.slug = path.split("/")[-1][:-3]
        self.body = body
```

In `tests/structured_cv.py`, add `body=""` to `Note.__init__` and `self.body = body`. In `tests/test_app_operations.py`'s `_Note`, add `self.body = ""`.

- [ ] **Step 2: Write the failing tests**

```python
"""cv reads the lead note's own job description, and every result says where its JD came
from (#369, spec §2)."""
import json
import os

import pytest

from sluice.core.jdsection import HEADING
from sluice.cv.engine import run_batch, run_one
from tests.test_cv_engine import (CLEAN_REPLY, ENTRIES, FakeBackend, FakeRenderer, FakeVault,
                                  HARD_DIRTY_REPLY, Note, _cfg)

_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
_SECTION_JD = "Placeholder posting from the lead note: build and run example services."
_FETCHED_JD = "Placeholder posting text fetched from the board for this example lead."


class SectionCache:
    """Records every get_or_build call's args and kwargs, so a row can tell `get_or_build(fm)`
    from `get_or_build(fm, supplied_jd=...)`. `fetch` is what a FETCH returns: a string, or an
    exception to raise. A supplied JD is returned without touching `fetch`, as the real
    DossierCache does. `jd_arrived` mirrors the real rule, floor included."""

    def __init__(self, fetch=_FETCHED_JD, min_jd_chars=0):
        self.fetch, self.min_jd_chars, self.calls = fetch, min_jd_chars, []

    def get_or_build(self, fm, **kw):
        self.calls.append(((fm,), kw))
        if kw.get("supplied_jd"):
            return {"jd": {"markdown": kw["supplied_jd"]}}
        if isinstance(self.fetch, Exception):
            raise self.fetch
        return {"jd": {"markdown": self.fetch}}

    def fetches(self):
        return [c for c in self.calls if not c[1].get("supplied_jd")]

    def jd_arrived(self, d):
        text = ((d.get("jd") or {}).get("markdown") or "").strip()
        return bool(text) and len(text) >= self.min_jd_chars


def _unserved():
    cfg = _cfg()
    cfg.served_dir = ""
    return cfg


def _run(cache, body="", cfg=None, reply=CLEAN_REPLY):
    note = Note(dict(_FM), body=body)
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg or _unserved(),
                FakeBackend(reply), cache, renderer=FakeRenderer())
    return r, note


def _section(text=_SECTION_JD):
    return f"# Example Foundry - Analyst\n\n{HEADING}\n{text}\n\n## Notes\nmine\n"


def test_a_lead_without_a_section_calls_get_or_build_exactly_as_today():
    # Conditional passing (rev-102, tst-101): roughly ten fakes in tests/ implement
    # get_or_build(self, fm); an unconditional keyword would TypeError inside the dossier
    # try and read as a failed fetch.
    cache = SectionCache()
    _run(cache, body="# just a title\n")
    assert cache.calls == [((dict(_FM),), {})]


def test_a_section_lead_is_composed_from_it_with_no_fetch():
    cache = SectionCache()
    r, _ = _run(cache, body=_section())
    assert r.status == "rendered"
    assert r.jd_source == "note"
    assert r.dossier_failed is False
    assert cache.fetches() == []
    assert cache.calls[0][1] == {"supplied_jd": _SECTION_JD}


@pytest.mark.parametrize("fetch, min_chars, source, failed", [
    pytest.param(_FETCHED_JD, 0, "fetched", False, id="healthy"),
    pytest.param("short", 100, "fetched", True, id="too-short"),
    pytest.param("", 0, "none", True, id="empty"),
    pytest.param(RuntimeError("blocked"), 0, "none", True, id="raised"),
])
def test_jd_source_values(fetch, min_chars, source, failed):
    r, _ = _run(SectionCache(fetch=fetch, min_jd_chars=min_chars))
    assert (r.jd_source, r.dossier_failed) == (source, failed)


def test_an_empty_section_is_no_section_and_the_fetch_decides():
    # rev-201, inv-201: a bare heading is NOT a section.
    cache = SectionCache()
    r, _ = _run(cache, body=f"{HEADING}\n\n## Notes\nx\n")
    assert cache.calls == [((dict(_FM),), {})]
    assert (r.jd_source, r.dossier_failed) == ("fetched", False)


def test_an_empty_section_with_a_raising_fetch_reports_none():
    r, _ = _run(SectionCache(fetch=RuntimeError("blocked")), body=f"{HEADING}\n")
    assert (r.jd_source, r.dossier_failed) == ("none", True)


def test_a_short_section_is_note_and_blind():
    r, _ = _run(SectionCache(min_jd_chars=500), body=_section())
    assert (r.jd_source, r.dossier_failed) == ("note", True)


def test_duplicate_sections_log_a_warning_naming_the_note(caplog):
    body = f"{HEADING}\nfirst text here\n## Other\n{HEADING}\nsecond\n"
    with caplog.at_level("WARNING"):
        r, note = _run(SectionCache(), body=body)
    assert r.jd_source == "note"
    assert any(note.ref in rec.getMessage() and "first" in rec.getMessage()
               for rec in caplog.records)


def test_a_section_reader_that_raises_is_an_error_not_a_failed_fetch(monkeypatch, caplog):
    # tst-002: read BEFORE the dossier try, so a reader bug is not reported as a fetch.
    import sluice.cv.engine as eng
    monkeypatch.setattr(eng.jdsection, "read",
                        lambda body: (_ for _ in ()).throw(ValueError("reader bug")))
    note = Note(dict(_FM), body=_section())
    with caplog.at_level("WARNING"):
        results = run_batch(FakeVault(ENTRIES, notes=[note]), _unserved(),
                            FakeBackend(CLEAN_REPLY), SectionCache(),
                            renderer=FakeRenderer())
    assert [r.status for r in results] == ["error"]
    assert not any("dossier for" in rec.getMessage() for rec in caplog.records)


def test_an_error_result_carries_the_jd_source_stamp():
    class _Boom:
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("render boom")
    note = Note(dict(_FM), body=_section())
    results = run_batch(FakeVault(ENTRIES, notes=[note]), _unserved(),
                        FakeBackend(CLEAN_REPLY), SectionCache(), renderer=_Boom())
    assert (results[0].status, results[0].jd_source) == ("error", "note")


def test_a_skipped_gate_result_carries_jd_source():
    # Review Focus 2.
    r, _ = _run(SectionCache(fetch=""), reply=HARD_DIRTY_REPLY)
    assert r.status == "skipped-gate"
    assert (r.jd_source, r.dossier_failed) == ("none", True)


@pytest.mark.parametrize("fm_extra", [{"status": "new"}, {"pending_cv": "x.pdf (2026-10-11)"}])
def test_an_early_refusal_reports_an_empty_jd_source(fm_extra):
    # Review Focus 3: never a misleading "none" for a lead that never reached the dossier.
    note = Note({**_FM, **fm_extra}, body=_section())
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _unserved(),
                FakeBackend(CLEAN_REPLY), SectionCache(), renderer=FakeRenderer())
    assert r.jd_source == ""


def test_a_dry_run_reports_jd_source():
    note = Note(dict(_FM), body=_section())
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _unserved(),
                FakeBackend(CLEAN_REPLY), SectionCache(), renderer=FakeRenderer(),
                dry_run=True)
    assert (r.status, r.jd_source) == ("dry-run", "note")


def _run_json(cfg):
    out = cfg.output_dir
    found = [os.path.join(d, "run.json") for d, _, files in os.walk(out) if "run.json" in files]
    assert len(found) == 1, found
    return json.load(open(found[0], encoding="utf-8"))


def test_run_json_carries_jd_source_on_a_rendered_result():
    cfg = _unserved()
    _run(SectionCache(), body=_section(), cfg=cfg)
    assert _run_json(cfg)["jd_source"] == "note"


def test_run_json_carries_jd_source_on_an_error_result():
    class _Boom:
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("render boom")
    cfg = _unserved()
    note = Note(dict(_FM))
    run_batch(FakeVault(ENTRIES, notes=[note]), cfg, FakeBackend(CLEAN_REPLY),
              SectionCache(), renderer=_Boom())
    assert _run_json(cfg)["jd_source"] == "fetched"
```

Add to `tests/test_cv_run_cli.py` (its `_args` runs `cv run --lead`, and `Sluice.compose_cv` is stubbed):

```python
@pytest.mark.parametrize("source, counted", [("note", True), ("fetched", False),
                                             ("none", False)])
def test_cmd_cv_run_prints_jd_source_and_counts_note_jds(monkeypatch, tmp_path, capsys,
                                                         source, counted):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
                      served="Example_CV_deadbeef.pdf", jd_source=source)
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])
    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert f"jd_source={source} " in err
    line = "cv: 1 CV(s) used the job description from the lead note"
    assert (line in err) is counted
```

(Add `import pytest` to that file if it lacks it.) And to `tests/test_mcpserver.py`, beside `test_cv_run_tool_reports_whether_the_runs_artefacts_were_written`:

```python
@pytest.mark.parametrize("source", ["note", "fetched", "none", ""])
def test_cv_run_tool_reports_jd_source(monkeypatch, tmp_path, source):
    from sluice.cv.engine import CvResult
    result = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
                      served="Example_CV_deadbeef.pdf", jd_source=source)
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])
    out = cv_run(_cv_app(Vault(str(tmp_path))), "Example Foundry - Analyst")
    assert out["jd_source"] == source
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_jd_source.py -q`
Expected: FAIL — `AttributeError: 'CvResult' object has no attribute 'jd_source'` on every row except `test_a_lead_without_a_section_calls_get_or_build_exactly_as_today`, which PASSES now: today's engine already calls `get_or_build(fm)` with no keyword, so that row is a regression guard and witness 1 is what proves it load-bearing.

- [ ] **Step 4: Implement the engine**

`sluice/cv/engine.py` imports: add `from sluice.core import jdsection` and `from sluice.core.dossier import jd_text`.

`CvResult`, directly after `dossier_failed`:

```python
    # #369: where this CV's job description TEXT came from -- "note" (the lead note's own
    # `## Job description` section, sluice/core/jdsection.py), "fetched" (a fetch returned
    # text, even text below `min_jd_chars`), "none" (no text at all) -- or "" when the lead
    # never reached the dossier step (an early refusal). Whether the text SUFFICED is
    # `dossier_failed`'s job, not this one's. Reported so a JD taken from the note is never
    # invisible. Stamped onto the exception like `dossier_failed`, for the same reason.
    jd_source: str = ""
```

`_run_one`, replacing the block from `jd, dossier_failed = "", False` to the end of its `except`:

```python
    # #369: the note's own job description, read BEFORE the dossier try. Inside it, a
    # reader bug would be swallowed and reported as "the job description fetch failed";
    # out here it reaches run_batch's per-lead catch-all as `error`, which is what it is.
    supplied, sections = jdsection.read(note.body)
    if sections > 1:
        _log.warning("cv: %s has %d %r sections; the first was used",
                     note.ref, sections, jdsection.HEADING)
    # A section counts only when it has text (rev-201): a bare heading is no section, and
    # the keyword is passed ONLY then, so every cache double written as
    # `get_or_build(self, fm)` keeps receiving today's exact call.
    jd, dossier_failed, blind_reason = "", False, ""
    jd_source = "note" if supplied else "none"
    try:
        d = (dossier_cache.get_or_build(fm, supplied_jd=supplied) if supplied
             else dossier_cache.get_or_build(fm))
        jd = (d.get("jd") or {}).get("markdown", "")
        if not supplied and jd_text(d):
            jd_source = "fetched"
        # (keep the existing #18/#169 comment here, and append:)
        # #369: the gate still checks every bullet against the bundle either way, so a
        # short JD costs tailoring QUALITY, not correctness -- which is why the CV is
        # composed. It is HELD rather than served under `cv.blind_hold` (see the blockers
        # below), because a blind CV looks exactly like a tailored one at the moment it
        # is sent.
        if not dossier_cache.jd_arrived(d):
            dossier_failed = True
            blind_reason = (
                f"the job description in the note is shorter than min_jd_chars "
                f"({dossier_cache.min_jd_chars})" if supplied
                else "no job description arrived")
    except Exception as e:
        _log.warning("dossier for %s failed: %s", note.ref, e)
        dossier_failed = True
        # Fixed phrases only, never `e`: this reason becomes frontmatter in the user's note
        # (needs_signoff), and an exception message can carry a url or page text. The
        # exception itself is logged above.
        blind_reason = ("no job description arrived" if supplied
                        else "the job description fetch failed")
```

Update the comment beneath it ("`dossier_failed` is a LOCAL...") to say "`dossier_failed` and `jd_source` are LOCALS".

`record.begin(...)`: add `jd_source=jd_source`.

The `skipped-gate` `CvResult(...)` and the `report = dict(...)`: add `jd_source=jd_source` beside `dossier_failed=dossier_failed`.

The inner `except` at the bottom of `_run_one`:

```python
    except Exception as e:
        e.dossier_failed = dossier_failed
        e.jd_source = jd_source
        raise
```

`run_batch`: both `CvResult(...)` constructions in the `except` gain `jd_source=getattr(e, "jd_source", "")`, and the catch-all's comment gains "`jd_source` crosses the same way".

- [ ] **Step 5: Implement artefacts, compose_cv, MCP and CLI**

`sluice/cv/artefacts.py::RunArtefacts.begin`: signature `begin(self, out_dir, *, lead, entry_ids, dossier_failed, jd_source, skills_unreadable)`; `self._facts` gains `"jd_source": jd_source`; the run.json `record` gains `"jd_source": self._facts["jd_source"],` after `"dossier_failed"`. Update the three `begin(...)` calls in `tests/test_cv_run_artefacts.py` to pass `jd_source=""`.

`sluice/core/app.py::Sluice.compose_cv`: both `except` arms' `CvResult(...)` gain `jd_source=getattr(e, "jd_source", "")`.

`sluice/mcpserver.py::cv_run`: `out` gains `"jd_source": r.jd_source` after `"dossier_failed"`, and the `cv_run` docstring list of returned keys (the one naming `served/dossier_failed/skills_unreadable/...`) and the registered tool's matching list gain `jd_source`.

`sluice/cli.py::cmd_cv_run`: after the `dossier_failed={r.dossier_failed} ` fragment add `f"jd_source={r.jd_source} "`. After the `blind` count block add:

```python
    # #369: a JD taken from the lead note is reported, never invisible.
    from_note = sum(1 for r in results if r.jd_source == "note")
    if from_note:
        print(f"cv: {from_note} CV(s) used the job description from the lead note",
              file=sys.stderr)
```

`tests/test_docs_claims.py::test_the_cv_summary_key_extraction_is_not_vacuous`: add `"jd_source"` to the expected set.

`docs/USAGE.md`: in the cv per-result-line paragraph add `jd_source=<note|fetched|none>` beside `dossier_failed=<bool>`, with one sentence: "`jd_source` says where the job description came from; `note` is the lead note's own `## Job description` section." In the `run.json` table row, add `jd_source` after `dossier_failed`. Add a short subsection near the `cv run` reference, "A job description in the lead note":

````markdown
A lead with no posting to fetch (a referral, an InMail) can carry its job description in its
note, under a heading spelled exactly `## Job description` (any letter case; nothing else on
the line). Everything below it, up to the next `#` or `##` heading, is used by `cv run` in place
of a fetch:

```markdown
## Job description
We are hiring a placeholder engineer to build example services.
```

A heading with anything else on its line, such as `## Job description:`, is not recognised.
````

- [ ] **Step 6: Run the new tests and the full suite**

Run: `.venv/bin/python -m pytest tests/test_cv_jd_source.py tests/test_cv_run_cli.py tests/test_mcpserver.py tests/test_docs_claims.py -q` then `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass. Any `AttributeError: ... 'body'` is a note fake missing the contract member: add `body=""` to it the same way as Step 1, and list each one in the commit body.

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check sluice tests scripts
git add -A sluice tests docs/USAGE.md
git commit -F - <<'EOF'
feat(cv): compose from the lead note's job description and report its source (#369)

A non-empty "## Job description" section is handed to the dossier cache
instead of fetching. Every result, run.json, the cv run line and MCP
cv_run report jd_source: note, fetched or none.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 8: Witnesses** (restore `sluice/cv/engine.py` / `sluice/cv/artefacts.py` after each)

1. Replace the conditional call with `dossier_cache.get_or_build(fm, supplied_jd=supplied)`. `test_a_lead_without_a_section_calls_get_or_build_exactly_as_today`: red.
2. Delete the `if not supplied and jd_text(d):` line AND its body `jd_source = "fetched"` (deleting the body alone leaves an empty `if`, an `IndentationError` that fails the whole engine's import). `test_jd_source_values[too-short]`: red (reads `none`).
3. Delete `e.jd_source = jd_source`. `test_an_error_result_carries_the_jd_source_stamp`: red (reads `""`).
4. Change `jd_source = "note" if supplied else "none"` to `jd_source = "note" if sections else "none"`, and the call condition `if supplied` to `if sections`. `test_an_empty_section_is_no_section_and_the_fetch_decides` and `test_an_empty_section_with_a_raising_fetch_reports_none`: red.
5. Move the `jdsection.read(...)` line, its `if sections > 1` block AND the `jd_source = "note" if supplied else "none"` line to just inside the `try:` (in that order). Moving the read alone leaves `jd_source` reading an unbound `supplied` above the `try`, which raises outside it and keeps the row GREEN. `test_a_section_reader_that_raises_is_an_error_not_a_failed_fetch`: red (the warning says "dossier for").
6. In `artefacts.py`, delete the `"jd_source": self._facts["jd_source"],` line. Both run.json rows: red.

---

### Task 4: `cv.blind_hold` — hold a CV composed without a job description

**Files:**
- Modify: `sluice/cv/config.py` (`CvConfig.blind_hold`), `sluice/core/leads.py` (`BLIND_TAG`, `split_blind`, shared splitter), `sluice/cv/engine.py` (blockers, `CvResult` comments), `sluice/cli.py` (`_print_signoff_claims`, the blind count line), `sluice/mcpserver.py` (`cv_signoff`, `_GET_LEAD_CONTENT_WARNING`, the registered `cv_signoff_tool` docstring)
- Modify config/docs: `sluice.yaml.example`, `docs/CONFIGURATION.md`, `docs/USAGE.md`, `docs/TROUBLESHOOTING.md`, `.rulesync/rules/CLAUDE.md`
- Tests: `tests/test_cv_blind_hold.py` (new), `tests/test_sluice_neutral_defaults.py`, `tests/test_cv_config.py`, `tests/test_core_leads_framing.py` or wherever `split_framing` is tested (`grep -rln split_framing tests`), `tests/test_mcpserver.py`, `tests/test_cv_signoff_cli.py` or wherever `_print_signoff_claims` is tested (`grep -rln _print_signoff_claims tests`)

**Interfaces:**
- Consumes: `dossier_failed`, `blind_reason` (Task 3).
- Produces: `CvConfig.blind_hold: bool = True`; `core.leads.BLIND_TAG = "blind"`; `core.leads.split_blind(entries) -> tuple[list, list]` returning `(blind_reasons, other_entries)`; MCP `cv_signoff` key `blind` at every arm.

- [ ] **Step 1: Write the failing tests**

`tests/test_cv_blind_hold.py`:

```python
"""cv.blind_hold (#369, spec §4): a CV composed without a job description is HELD for sign-off,
never served send-ready. Its own key -- not require_signoff (fabrication) and not style_hold
(wording)."""
import json

import pytest

from sluice.core.jdsection import HEADING
from sluice.core.leads import BLIND_TAG
from sluice.cv.engine import run_one
from tests.test_cv_engine import (CLEAN_REPLY, ENTRIES, FakeBackend, FakeRenderer, FakeVault,
                                  Note, _cfg, _served)
from tests.test_cv_jd_source import SectionCache, _SECTION_JD

_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
_FETCH_FAILED = "the job description fetch failed"
_NOT_ARRIVED = "no job description arrived"


def _go(cache, *, blind_hold=True, require_signoff=True, body="", fm=None):
    cfg = _cfg()
    cfg.blind_hold, cfg.require_signoff = blind_hold, require_signoff
    note = Note(dict(fm or _FM), body=body)
    v = FakeVault(ENTRIES, notes=[note])
    # FakeBackend's default audit reply is a clean "supported" line: the audit RUNS and
    # flags nothing, so a hold below can only be the blind one (tst-202).
    r = run_one(note, v, cfg, FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    return r, note, v


@pytest.mark.parametrize("blind_hold", [True, False])
@pytest.mark.parametrize("require_signoff", [True, False])
def test_the_blind_hold_matrix_with_a_clean_audit(monkeypatch, blind_hold, require_signoff):
    _served(monkeypatch)
    r, note, v = _go(SectionCache(fetch=""), blind_hold=blind_hold,
                     require_signoff=require_signoff)
    if blind_hold:
        assert r.status == "needs-signoff"
        assert note.ref not in v.written
        assert json.loads(note.fm["needs_signoff"]) == [f"{BLIND_TAG}\t{_NOT_ARRIVED}"]
    else:
        assert r.status == "rendered"
        assert note.ref in v.written
        assert "needs_signoff" not in note.fm


@pytest.mark.parametrize("cache, body, reason", [
    pytest.param(SectionCache(fetch=RuntimeError("blocked")), "", _FETCH_FAILED, id="raised"),
    pytest.param(SectionCache(fetch=""), "", _NOT_ARRIVED, id="not-arrived"),
    pytest.param(SectionCache(min_jd_chars=500), f"{HEADING}\n{_SECTION_JD}\n",
                 "the job description in the note is shorter than min_jd_chars (500)",
                 id="short-section"),
])
def test_each_producer_writes_its_own_fixed_reason(monkeypatch, cache, body, reason):
    _served(monkeypatch)
    r, note, _ = _go(cache, body=body)
    assert r.status == "needs-signoff"
    assert json.loads(note.fm["needs_signoff"]) == [f"{BLIND_TAG}\t{reason}"]


def test_the_reason_never_carries_exception_text(monkeypatch):
    _served(monkeypatch)
    _, note, _ = _go(SectionCache(fetch=RuntimeError("https://example.invalid/secret")))
    assert "example.invalid" not in note.fm["needs_signoff"]


def test_a_lead_with_a_good_section_is_not_held(monkeypatch):
    _served(monkeypatch)
    r, note, v = _go(SectionCache(), body=f"{HEADING}\n{_SECTION_JD}\n")
    assert (r.status, r.jd_source) == ("rendered", "note")
    assert note.ref in v.written


def test_nothing_served_means_no_hold():
    cfg = _cfg(); cfg.served_dir = ""
    note = Note(dict(_FM))
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg, FakeBackend(CLEAN_REPLY),
                SectionCache(fetch=""), renderer=FakeRenderer())
    assert r.status == "rendered" and "pending_cv" not in note.fm


def test_a_dry_run_holds_nothing():
    note = Note(dict(_FM))
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _cfg(), FakeBackend(CLEAN_REPLY),
                SectionCache(fetch=""), renderer=FakeRenderer(), dry_run=True)
    assert r.status == "dry-run" and "pending_cv" not in note.fm


def test_a_held_lead_is_latched_until_discarded_then_composes_from_the_section(monkeypatch):
    # rev-001: adding a section does nothing while the hold stands (the #60 latch).
    _served(monkeypatch)
    cache = SectionCache(fetch="")
    r, note, v = _go(cache)
    assert r.status == "needs-signoff"
    note.body = f"{HEADING}\n{_SECTION_JD}\n"
    again = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert again.status == "skipped-needs-signoff"
    assert v.sign_off(note.ref, accept=False) == "discarded"
    third = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert (third.status, third.jd_source) == ("rendered", "note")


def test_a_discarded_lead_whose_section_is_still_short_is_held_again(monkeypatch):
    # Review Focus 4.
    _served(monkeypatch)
    cache = SectionCache(min_jd_chars=500)
    body = f"{HEADING}\n{_SECTION_JD}\n"
    r, note, v = _go(cache, body=body)
    assert r.status == "needs-signoff"
    v.sign_off(note.ref, accept=False)
    again = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert again.status == "needs-signoff"
    assert "shorter than min_jd_chars" in note.fm["needs_signoff"]


def test_the_cv_run_count_line_is_reworded(monkeypatch, tmp_path, capsys):
    from sluice.cli import _build_parser, cmd_cv_run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.cv.engine import CvResult
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md",
                      "needs-signoff", dossier_failed=True, jd_source="none")
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])
    args = _build_parser().parse_args(["cv", "run", "--lead", "example-foundry-analyst"])
    cmd_cv_run(args, Config())
    err = capsys.readouterr().err
    assert "cv: 1 CV(s) composed without a job description" in err
    assert "composed blind" not in err


def test_a_blind_hold_is_not_a_gate_finding(monkeypatch):
    _served(monkeypatch)
    r, _, _ = _go(SectionCache(fetch=""))
    assert r.violations == []
```

`tests/test_sluice_neutral_defaults.py`, after the `voice_check`/`style_hold` block:

```python
# #369: cv.blind_hold ships ON, deliberately unlike style_hold. It withholds a pointer and
# filters no lead, so it is a safety valve rather than a preference gate -- require_signoff's
# reasoning: the failure it prevents (a CV tailored to nothing, sent as if tailored) is
# invisible at the moment it matters. A bool, so the list-keyed sweep above cannot see it.
def test_blind_hold_dataclass_default_is_on():
    assert CvConfig().blind_hold is True


def test_blind_hold_loader_default_is_on(tmp_path, monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_cv_config(None).blind_hold is True


def test_the_example_config_ships_blind_hold_commented():
    import yaml  # this file imports it per row, like the style_hold row; no module-scope import
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "blind_hold:" in text, "blind_hold must be documented at all"
    doc = yaml.safe_load(text) or {}
    assert "blind_hold" not in (doc.get("cv") or {}), "blind_hold must ship COMMENTED"
```

`tests/test_cv_config.py`: add `"blind_hold"` to both `@pytest.mark.parametrize("field", [...])` lists.

Beside the existing `split_framing` tests:

```python
def test_split_blind_takes_only_blind_entries_and_keeps_the_rest_in_order():
    from sluice.core.leads import BLIND_TAG, split_blind
    entries = [f"{BLIND_TAG}\tno job description arrived", "unsupported\tx\tEF1",
               "framing\tconcerns: y", 3, "blind"]
    assert split_blind(entries) == (["no job description arrived"],
                                    ["unsupported\tx\tEF1", "framing\tconcerns: y", 3, "blind"])
```

Beside the existing `_print_signoff_claims` tests (copy that file's capture idiom):

```python
def test_a_blind_entry_prints_its_own_group_and_is_not_an_unsupported_claim(capsys):
    from sluice.cli import _print_signoff_claims
    _print_signoff_claims("example-slug", ["blind\tno job description arrived"])
    err = capsys.readouterr().err
    assert "composed WITHOUT a job description (no job description arrived)" in err
    assert "--discard" in err and "## Job description" in err
    assert "unsupported claim" not in err
```

`tests/test_mcpserver.py`, beside the framing rows (reuse `_seed`, `_app`, `_PENDING`):

```python
def _hold_blind(v, note, reason="no job description arrived", pending=_PENDING):
    v.hold_for_signoff(note.ref, pending=pending,
                       claims=json.dumps([f"blind\t{reason}", "unsupported claim"]))


def test_cv_signoff_needs_confirmation_returns_blind_apart(tmp_path):
    v = Vault(str(tmp_path)); slug = _seed(tmp_path, status="shortlist")
    _hold_blind(v, v.read_leads()[0])
    out = cv_signoff(_app(tmp_path), slug)
    assert out["outcome"] == "needs_confirmation"
    assert out["blind"] == ["no job description arrived"]
    assert out["claims"] == ["unsupported claim"]


def test_cv_signoff_stale_confirmation_returns_blind_apart(tmp_path):
    v = Vault(str(tmp_path)); slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    _hold_blind(v, note, "no job description arrived")
    app = _app(tmp_path)
    first = cv_signoff(app, slug)
    v.sign_off(note.ref, accept=False)
    # ONLY the blind entry changes (tst-203): the token must still go stale.
    _hold_blind(v, note, "the job description fetch failed")
    second = cv_signoff(app, slug, confirm_token=first["confirm_token"])
    assert second["outcome"] == "stale_confirmation"
    assert second["blind"] == ["the job description fetch failed"]
    assert second["claims"] == ["unsupported claim"]


def test_cv_signoff_discard_returns_blind_apart(tmp_path):
    v = Vault(str(tmp_path)); slug = _seed(tmp_path, status="shortlist")
    _hold_blind(v, v.read_leads()[0])
    out = cv_signoff(_app(tmp_path), slug, discard=True)
    assert out["outcome"] == "discarded"
    assert out["blind"] == ["no job description arrived"]
    assert out["claims"] == ["unsupported claim"]


def test_get_lead_warning_names_blind_entries_as_sluices_own_text():
    from sluice.mcpserver import _GET_LEAD_CONTENT_WARNING
    assert "`blind`" in _GET_LEAD_CONTENT_WARNING
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_blind_hold.py tests/test_sluice_neutral_defaults.py tests/test_cv_config.py tests/test_mcpserver.py -q -k "blind or bool_cv_field"`
Expected: FAIL — `ImportError: cannot import name 'BLIND_TAG'`, then `AttributeError` on `blind_hold`.

- [ ] **Step 3: Implement the tag**

`sluice/core/leads.py`, refactor beside `FRAMING_TAG`:

```python
# #369: the tag marking a sign-off hold entry as BLIND -- the CV was composed without a job
# description. sluice's own fixed text (one of three phrases, cv/engine.py), never derived
# from a page or a model. One constant for the writer (cv/engine.py) and both readers
# (cli.py, mcpserver.py), which already import the framing tag's helpers from here.
BLIND_TAG = "blind"


def _split_tag(entries, tag):
    """`(tagged_rests, other_entries)`: entries tagged `tag` with the tag stripped, and every
    other entry untouched and in order. The array is hand-editable YAML, so an entry may be
    any JSON value: a non-string is never tagged and never raises."""
    tagged, other = [], []
    for entry in entries:
        kind, sep, rest = entry.partition("\t") if isinstance(entry, str) else ("", "", "")
        if sep and kind == tag:
            tagged.append(rest)
        else:
            other.append(entry)
    return tagged, other


def split_framing(entries):
    """`(framing_lines, other_entries)` from a parsed `needs_signoff` array (#329)."""
    return _split_tag(entries, FRAMING_TAG)


def split_blind(entries):
    """`(blind_reasons, other_entries)` from a parsed `needs_signoff` array (#369)."""
    return _split_tag(entries, BLIND_TAG)
```

Keep `split_framing`'s existing docstring sentences that are still true (move the "non-string" sentence to `_split_tag`).

- [ ] **Step 4: Implement the config key**

`sluice/cv/config.py`, after `style_hold`:

```python
    # Whether a CV composed WITHOUT a job description (the fetch failed, none arrived, or
    # the lead note's own section is under `min_jd_chars`) WITHHOLDS the send-ready pointer
    # until a human signs off (#369). Its OWN key: `require_signoff` was chosen for
    # fabrication and `style_hold` for wording, and a blind CV is neither -- it passes the
    # gate honestly. ON by default for `require_signoff`'s reason: the failure it prevents
    # is invisible at the moment it matters, since a blind CV looks exactly like a tailored
    # one when it is sent. It filters no lead (it withholds a pointer), so it is a safety
    # valve, not a preference gate.
    blind_hold: bool = True
```

In the same file, `require_signoff`'s comment ends "set False to restore the old auto-serve. The hard validate gate is unaffected either way.", which goes false for a blind CV. Replace those two sentences with: "set False to serve a CV the audit flagged. It governs the FABRICATION hold only: a CV composed without a job description is held separately under `blind_hold`. The hard validate gate is unaffected either way."

The bool guard in `load_cv_config` is type-keyed and needs no change; its comment's roster "(`voice_check`, `style_hold`, `require_signoff` today)" becomes "(`voice_check`, `style_hold`, `require_signoff`, `blind_hold` today)".

`sluice.yaml.example`, after the `style_hold` lines, in their comment style:

Its `require_signoff` comment's last line, `#   # Ships on (a safety valve, not a preference); set false to restore auto-serve.`, becomes `#   # Ships on (a safety valve, not a preference); set false to serve a flagged CV. A CV
#   # composed without a job description is held separately, under blind_hold.`

```yaml
#   # Whether a CV composed WITHOUT a job description (the fetch failed, nothing
#   # arrived, or the note's own "## Job description" section is under min_jd_chars) is
#   # HELD for `job-sluice cv signoff` instead of served (#369). ON by default.
#   blind_hold: true
```

`docs/CONFIGURATION.md`, after the `style_hold` row:

```markdown
| `blind_hold` | `true` | #369: whether a CV composed **without a job description** (the fetch failed, none arrived, or the lead note's own `## Job description` section is under `min_jd_chars`) **withholds** the send-ready pointer until `job-sluice cv signoff`. Its own key, deliberately not `require_signoff` (fabrication) or `style_hold` (wording). It filters no lead. Rejects non-bool values |
```

- [ ] **Step 5: Implement the hold**

`sluice/cv/engine.py`: import `BLIND_TAG` from `sluice.core.leads` (extend the existing import). In the blockers assembly:

```python
        unaudited = [f"unaudited\t{audit_unavailable}"] if audit_unavailable else []
        # #369: a CV composed without a job description is HELD under `cv.blind_hold`, as a
        # `blind\t<reason>` entry whose reason is one of three fixed phrases set beside
        # `dossier_failed` above. OUTSIDE the `require_signoff` conditional on purpose: that
        # key answers "hold for a fabrication risk?", and a blind CV passed the gate honestly.
        # Not a gate finding either -- `violations` stays empty.
        blind = ([f"{BLIND_TAG}\t{blind_reason}"]
                 if dossier_failed and cvcfg.blind_hold else [])
        blockers = (
            (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
            + blind + style_blockers)
```

Extend the comment above `style_blockers` ("`sluice/cli.py::_print_signoff_claims` is the roster of the tags") to name `blind\t` beside `style\t`/`term\t`.

Rewrite the `CvResult.dossier_failed` comment's last sentences: replace "This does NOT change control flow (...) only visibility: ..." with "Since #369 it also drives a hold: under `cv.blind_hold` (on by default) a served CV with this set is held for sign-off as a `blind\t<reason>` entry, never written send-ready -- without that, "status: rendered" is indistinguishable from a CV genuinely tailored to a real job description." In the `skills_unreadable` and `artefacts_failed` comments, change "the shape `dossier_failed` above established" / "`dossier_failed`'s shape" to name the shape directly: "Visibility, never control flow -- the shape `dossier_failed` had before #369 made it drive the blind hold".

`run_batch`'s catch-all comment and `core/app.py::compose_cv`'s `VaultConflict` comment both quote `"N CV(s) composed blind"`; change that quote to `"N CV(s) composed without a job description"`.

- [ ] **Step 6: Implement the readers**

`sluice/cli.py::_print_signoff_claims`: import `split_blind` with `split_framing`; after the framing block:

```python
    # #369: a `blind\t<reason>` entry is the CV composed WITHOUT a job description -- not a
    # claim (nothing was flagged) and not wording. Its own group, never counted as an
    # unsupported claim, and it names how to get a TAILORED CV, since accepting here
    # promotes the generalist one as it is.
    blind, claims = split_blind(claims)
    if blind:
        print(f"cv signoff: {slug} was composed WITHOUT a job description ({blind[0]}) -- "
              f"it is not tailored to this role. To tailor it: --discard, add a "
              f'"## Job description" section to the note, and re-run cv run.',
              file=sys.stderr)
```

Add `blind\t` to the docstring's tag roster ("A `blind\\t` entry (#369, `core/leads.py::split_blind`) prints its own group ...").

`sluice/cli.py::cmd_cv_run`, the `blind` count block: replace its comment's "does not stop composition ... without changing cv's control flow, which is a bigger change than this guard should carry" with "does not stop composition (the gate still runs), and under `cv.blind_hold` the CV is held rather than served (#369); this line counts them either way, including with the hold off." and the print with:

```python
        print(f"cv: {blind} CV(s) composed without a job description", file=sys.stderr)
```

`sluice/mcpserver.py`: import `split_blind`. In `cv_signoff`'s `aborted` arm, after `framing, claims = split_framing(stored)`: `blind, claims = split_blind(claims)`; add `"blind": blind` to both returned dicts. In the `promoted|discarded|collision` arm, after its `split_framing`: `blind, claims = split_blind(claims)` and `if blind: out["blind"] = blind`. `_GET_LEAD_CONTENT_WARNING`:

```python
_GET_LEAD_CONTENT_WARNING = (
    f"Everything in fm and body, except the keys named next, {UNTRUSTED_SCRAPED_CONTENT_WARNING} "
    f"Each of fm's {_FRAMING_KEY_NAMES}, and each `framing` "
    f"entry in `needs_signoff`, {TRIAGE_FRAMING_CONTENT_WARNING} "
    f"Each `blind` entry in `needs_signoff` is sluice's own fixed text saying the CV was "
    f"composed without a job description. "
    f"Every other `needs_signoff` entry {UNTRUSTED_DERIVED_CONTENT_WARNING}")
```

The registered `cv_signoff_tool` docstring gains: "`blind` lists why the CV was composed without a job description (sluice's own text, not a claim): promoting accepts an untailored CV; discard, add a `## Job description` section to the lead note and re-run cv_run to tailor it."

- [ ] **Step 7: Docs and the operating manual**

`docs/USAGE.md`: replace the sentence about the count line ("when any dossier fetch failed and composition proceeded blind", find it with `grep -n "proceeded blind" docs/USAGE.md`) with: "`cv: N CV(s) composed without a job description` -- printed when any CV's job description failed to arrive. Under `cv.blind_hold` (on by default) each such CV is held for `job-sluice cv signoff` rather than served."

In the `job-sluice cv signoff` section, the opening sentence's list of hold causes ("held back by the softer advisory audit (`cv.require_signoff`, on by default) or by a style or term finding (`cv.style_hold`, off by default)") gains ", or because it was composed without a job description (`cv.blind_hold`, on by default)"; and after the `cv.style_hold` sentence add: "A hold placed by `cv.blind_hold` says the CV was composed WITHOUT a job description and how to tailor it instead."

`docs/TROUBLESHOOTING.md`, new entry:

```markdown
### `cv run` reports `needs-signoff` and `cv signoff` says "composed WITHOUT a job description"

The CV was composed from your evidence alone: the job description fetch failed, nothing came
back, or the lead note's own `## Job description` section is shorter than `min_jd_chars`. It is
held under `cv.blind_hold` so it is not sent as if it were tailored. Two ways out:

- Accept the untailored CV: `job-sluice cv signoff --lead SLUG`.
- Tailor it: `job-sluice cv signoff --lead SLUG --discard`, add a `## Job description` section
  to the lead note (see `docs/USAGE.md`), then `job-sluice cv run --lead SLUG`. Adding the
  section without discarding first does nothing: a held lead is not recomposed.
```

`.rulesync/rules/CLAUDE.md`, "The advisory audit and the holds" paragraph, append after the `style_hold` sentence: "`cv.blind_hold` (ON by default, #369) is a third, separate key: a CV composed without a job description (the fetch failed, none arrived, or the lead note's own section is under `min_jd_chars`) is held as a `blind\t<reason>` entry, one of three fixed phrases, never exception text -- not a gate finding, and not `require_signoff`'s question." Then regenerate:

```bash
npm ci --ignore-scripts && npm run rulesync
git status --short   # only .rulesync/rules/CLAUDE.md is tracked; the regenerated outputs are gitignored
```

- [ ] **Step 8: Run the full suite; fix what the new default breaks**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: the new rows pass. Existing rows that compose a blind lead with a served dir now see `needs-signoff`. Fix each one by one (tst-003, tst-101, tst-203), in ANY file (`tests/test_config_paths.py`'s `_NullCache` returns an empty JD, for one): FIRST add an assertion of the lead's `jd_source` (so a broken fake cannot hide behind the fix), THEN set `blind_hold = False` on that test's config or give the fake a real JD. Never flip `_cfg()`'s default. List every test touched in the commit body.

Then the GREEN-for-the-wrong-reason audit (tst-405, inv-404): a row asserting a `require_signoff`, `unaudited` or `style_hold` hold may now pass only because the blind hold also withholds the pointer. List them:

```bash
grep -rln "needs-signoff\|pending_cv\|not in .*written" tests | xargs grep -ln "_BlockedCache\|_VariableJdCache\|_NullCache\|markdown\": \"\""
```

For every hold-asserting row in those files whose cache yields no job description, either give the cache a real JD or add `assert not any(str(e).startswith("blind\t") for e in <its needs_signoff entries>)`, so the row still witnesses the hold it was written for. Name each in the commit body.

Two existing test comments go false and are reworded here: `tests/test_cv_engine.py::test_a_cv_composed_without_a_JD_is_flagged_rather_than_silently_tailored` ("Control flow is deliberately unchanged -- ... a bigger behaviour change than this issue should carry") becomes "This row runs with no served dir, so the #369 blind hold, which withholds the served pointer, does not apply; tests/test_cv_blind_hold.py covers it."; and the `tests/test_dossier_guard.py` comment saying composing blind "does not change the status" gets the same qualification (`grep -n "does not change the status\|status is unchanged" tests/test_dossier_guard.py`).

- [ ] **Step 9: Commit**

```bash
.venv/bin/ruff check sluice tests scripts
git add -A sluice tests sluice.yaml.example docs .rulesync/rules/CLAUDE.md
git commit -F - <<'EOF'
feat(cv): hold a CV composed without a job description for sign-off (#369)

cv.blind_hold, on by default, withholds tailored_cv from a CV composed
without a job description and records a fixed blind reason in
needs_signoff. cv signoff and MCP cv_signoff show it apart from claims,
with both ways out.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

The release PR's changelog gets a `Changed` note (spec Decisions); that edit happens in the release PR, not here.

- [ ] **Step 10: Witnesses** (restore after each)

1. Move `+ blind` inside the `require_signoff` parenthesis (`(unsupported_claims(audit_flags) + unaudited + blind if cvcfg.require_signoff else [])`). The matrix cell with `blind_hold=True, require_signoff=False` (run `-k matrix -v` and read the parameter values): red.
2. In the `except` arm, change `("no job description arrived" if supplied else "the job description fetch failed")` to `"no job description arrived"`. `test_each_producer_writes_its_own_fixed_reason[raised]`: red.
3. Flip `blind_hold: bool = True` to `False`. `test_blind_hold_dataclass_default_is_on`: red.
4. In `mcpserver.py`'s `promoted|discarded|collision` arm, delete the `blind, claims = split_blind(claims)` line AND the `if blind: out["blind"] = blind` line together (deleting the split alone leaves an `UnboundLocalError`). `test_cv_signoff_discard_returns_blind_apart`: red (`KeyError: 'blind'`).
5. In the `aborted` arm, delete its `blind, claims = split_blind(claims)` line AND the `"blind": blind` entry in both returned dicts. `test_cv_signoff_needs_confirmation_returns_blind_apart`: red (`KeyError: 'blind'`).

---

### Task 5: `UpsertResult.ref` — the one Store-contract change

**Files:**
- Modify: `sluice/core/protocols.py` (`UpsertResult`), `sluice/core/vault.py` (`Vault.upsert`'s three slug-bearing returns)
- Test: `tests/conformance/test_store_contract.py`

**Interfaces:**
- Produces: `UpsertResult.ref: object = None`, set exactly when `slug` is (`created`/`updated`/`merged`); it is the same opaque handle `read_leads` issues for that note.

- [ ] **Step 1: Write the failing conformance rows**

```python
def test_upsert_result_ref_names_the_note_read_leads_issues(store_name, tmp_path, monkeypatch):
    """#369: `ref` is populated exactly when `slug` is, and is the SAME opaque handle
    `read_leads` issues for that note -- the form every write method takes. Proven by USING
    it: append_body_section through the result's ref must land on that slug's note."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    created = store.upsert(_lead())
    assert created.outcome == "created" and created.ref is not None
    note = next(n for n in store.read_leads() if n.slug == created.slug)
    assert created.ref == note.ref
    assert store.append_body_section(created.ref, "<!-- t -->", "## Probe\nx") is True
    assert "## Probe" in next(n for n in store.read_leads() if n.slug == created.slug).body
    again = store.upsert(_lead())
    assert again.outcome in ("updated", "merged") and again.ref == note.ref
```

In `test_upsert_result_slug_is_blank_for_every_no_write_outcome`, beside each `assert ... .slug == ""`, add `assert <same>.ref is None`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/conformance/test_store_contract.py -q -k "ref or no_write_outcome"`
Expected: FAIL with `AttributeError: 'UpsertResult' object has no attribute 'ref'`.

- [ ] **Step 3: Implement**

`sluice/core/protocols.py::UpsertResult`: add `ref: object = None` after `slug`, and append to the docstring: "`ref` (#369) is populated exactly when `slug` is, and is the same opaque handle `read_leads` would issue for that note -- the form the write methods take, so a caller that must write to the note it just created (`Sluice.create_lead` appending a job description) never reconstructs a handle from the slug, which is wrong under `lead_layout`. `None` for the three no-write outcomes." In the same file, `Store.upsert`'s own docstring (what a second store's author reads first) gains one sentence: "The returned `UpsertResult` carries `ref` exactly when it carries `slug` -- the handle `read_leads` issues for that note (#369)."

`sluice/core/vault.py::Vault.upsert`: the `created` return becomes `UpsertResult(outcome="created", slug=self._slug_for(path), ref=path)`; the `update` and `merge` returns become `UpsertResult(outcome=outcome, slug=self._slug_for(path) if outcome != "refused" else "", ref=path if outcome != "refused" else None)`.

- [ ] **Step 4: Run, then commit**

Run: `.venv/bin/python -m pytest tests/conformance -q && .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass.

```bash
git add sluice/core/protocols.py sluice/core/vault.py tests/conformance/test_store_contract.py
git commit -F - <<'EOF'
refactor(core): report the note ref from Store.upsert (#369)

UpsertResult.ref is the handle read_leads issues for the note the call
created or resolved to, so a caller can write to it without rebuilding
a path from the slug.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 5: Witness**

Delete `, ref=path` from the `created` return. `test_upsert_result_ref_names_the_note_read_leads_issues`: red. Restore.

---

### Task 6: Triage reads the note's JD

**Files:**
- Modify: `sluice/triage/engine.py` (`_SectionUnreadable`, `_prefetch_dossiers`, its caller in `run`)
- Modify: `.rulesync/rules/CLAUDE.md` (the `min_jd_chars` paragraph)
- Test: `tests/test_triage_jd_section.py`

**Interfaces:**
- Consumes: `jdsection.read` (Task 1), `get_or_build(fm, *, supplied_jd=...)` (Task 2).
- Produces: `_prefetch_dossiers(...) -> tuple[dict, set]` — `(results, from_section)`; `results[i]` is a dossier, `_FetchFailed` or `_SectionUnreadable`; `from_section` is the set of `keeps` indices built from a section.

- [ ] **Step 1: Write the failing tests**

```python
"""triage reads the lead note's own job description (#369, spec §2). On a REAL DossierCache
with a counting fetcher that returns an EMPTY JD (tst-201): the existing stand-in caches
return a JD whatever the lead holds, so a section row run on them stays green with the
feature deleted. Every lead's company is already set, so company resolution never fetches
and every fetcher call is the prefetch's."""
import logging
import os
from datetime import datetime

import pytest

from sluice.core.dossier import DossierCache
from sluice.core.jdsection import HEADING
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
import sluice.triage.engine as eng
from tests.test_triage_engine import _Backend, _fields

_SENTINEL = "SENTINEL-POSTING-7f3a placeholder duties for an example role."


class _Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, lead):
        self.calls += 1
        return {"jd": {"markdown": ""}}


def _cache(tmp_path, fetcher, floor=0):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                        clock=lambda: datetime(2026, 10, 11), min_jd_chars=floor)


def _write(v, name, fm_lines, body):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def _section(text=_SENTINEL):
    return f"# Lead\n\n{HEADING}\n{text}\n\n## Notes\nmine\n"


def _run(tmp_path, titles, notes, *, floor=0, concurrency=1):
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    for name, company, url, body in notes:
        _write(v, name, _fields(company, accept[0].title(), url=url), body)
    cfg = TriageConfig(); cfg.accept_titles = list(accept)
    fetcher, backend = _Counting(), _Backend()
    report = eng.run(v, cfg, backend, _cache(tmp_path, fetcher, floor),
                     AuditLog(str(tmp_path / "audit.jsonl")), statuses=("new",),
                     dossier_concurrency=concurrency)
    after = {n.fm["company"]: n for n in v.read_leads()}
    return report, after, fetcher, "\n".join(backend.prompts)


def test_a_section_lead_with_no_url_is_judged_from_the_section(tmp_path, titles):
    report, after, fetcher, prompts = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "", _section())])
    assert after["Example Alpha"].status == "shortlist"
    assert _SENTINEL in prompts
    assert fetcher.calls == 0


def test_a_url_lead_with_a_section_costs_no_fetch(tmp_path, titles):
    _, after, fetcher, prompts = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "https://example.invalid/1", _section())])
    assert fetcher.calls == 0 and _SENTINEL in prompts
    assert after["Example Alpha"].status == "shortlist"


def test_control_the_same_lead_without_a_section_fetches_once(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "https://example.invalid/1", "# Lead\n")])
    assert fetcher.calls == 1
    assert after["Example Alpha"].status == "unjudgeable"


def test_an_empty_section_is_no_section(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "https://example.invalid/1", f"{HEADING}\n\n## Notes\n")])
    assert fetcher.calls == 1
    assert after["Example Alpha"].status == "unjudgeable"
    assert "no job description was fetched" in after["Example Alpha"].fm["relevance_notes"]


@pytest.mark.parametrize("concurrency", [1, 4])
def test_two_notes_sharing_a_cache_key_each_get_their_own_section(tmp_path, titles,
                                                                   concurrency):
    url = "https://example.invalid/shared"
    cache = _cache(tmp_path, _Counting())
    # tst-007: assert the shared key FIRST, or the row could pass on two distinct keys.
    assert cache.cache_key({"url": url}) == cache.cache_key({"url": url, "company": "x"})
    _, after, fetcher, prompts = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", url, _section("ALPHA-ONLY posting text here.")),
         ("b.md", "Example Beta", url, _section("BETA-ONLY posting text here."))],
        concurrency=concurrency)
    assert fetcher.calls == 0
    assert "ALPHA-ONLY" in prompts and "BETA-ONLY" in prompts
    assert {after["Example Alpha"].status, after["Example Beta"].status} == {"shortlist"}


def test_a_section_read_that_raises_skips_only_that_lead(tmp_path, titles, monkeypatch):
    real = eng.jdsection.read

    def flaky(body):
        if "BROKEN" in body:
            raise ValueError("reader bug")
        return real(body)
    monkeypatch.setattr(eng.jdsection, "read", flaky)
    report, after, _, prompts = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "", _section("BROKEN marker text here.")),
         ("b.md", "Example Beta", "", _section())])
    assert any(f.startswith("section ") and "reader bug" in f for f in report.failures)
    assert not any(f.startswith("dossier ") for f in report.failures)
    assert after["Example Beta"].status == "shortlist"
    assert after["Example Alpha"].status == "new"


def test_a_short_section_is_unjudgeable_with_the_note_reason(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "", _section())], floor=1000)
    assert fetcher.calls == 0
    assert after["Example Alpha"].status == "unjudgeable"
    assert ("the job description in the note is shorter than min_jd_chars (1000)"
            in after["Example Alpha"].fm["relevance_notes"])


def test_a_section_that_states_a_pay_basis_is_observed_like_a_fetched_posting(tmp_path, titles):
    # inv-401, owner's ruling (a): a job description in the note IS the posting, for the
    # #223 role_type observation too -- a pasted posting saying "contract" corrects a
    # blank-provenance role_type exactly as the same text fetched would. _fields seeds
    # role_type "permanent" with no role_type_source, which ranks below `observed`.
    _, after, fetcher, _ = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "", _section(_SENTINEL + " This is a fixed-term contract."))])
    assert fetcher.calls == 0
    assert after["Example Alpha"].fm["role_type"] == "contract"
    assert after["Example Alpha"].fm["role_type_source"] == "observed"


def test_duplicate_sections_log_a_warning(tmp_path, titles, caplog):
    body = f"{HEADING}\nfirst text\n## X\n{HEADING}\nsecond\n"
    with caplog.at_level(logging.WARNING):
        _run(tmp_path, titles, [("a.md", "Example Alpha", "", body)])
    assert any("sections; the first was used" in r.getMessage() for r in caplog.records)
```

Before running, check how `apply_classification` records the `unjudgeable` reason (`grep -n "def apply_classification" -A30 sluice/triage/*.py`). If the reason lands in a key other than `relevance_notes`, change the three `fm["relevance_notes"]` reads to that key.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_jd_section.py -q`
Expected: the section rows FAIL (status `unjudgeable`, sentinel absent); the control row passes.

- [ ] **Step 3: Implement**

`sluice/triage/engine.py`: `from sluice.core import jdsection`. Beside `_FetchFailed`:

```python
class _SectionUnreadable:
    """A lead note's `## Job description` section that could not be READ (#369): a reader
    failure, never a fetch. Its own type so the caller reports it as `section <ref>: ...`,
    not as `dossier <ref>: ...` -- the masquerade cv/engine.py avoids by reading the section
    outside its dossier `try`. Same shape as `_FetchFailed`, for its reasons."""

    __slots__ = ("message",)

    def __init__(self, exc):
        self.message = str(exc)
```

`_prefetch_dossiers`: after `targets = [...]`, before `by_key`:

```python
    results, from_section, pooled = {}, set(), []
    # #369: a lead note's own job description wins over a fetch. Read per lead, each in its
    # own `try` (one bad note must not abort the prefetch), over the SAME twin-filtered
    # `targets`, so the index contract above is unchanged. A lead whose section has text is
    # built directly -- no fetcher call, no pool slot -- and NOT grouped by cache_key: two
    # notes sharing a key may carry different sections. Everything else goes through the
    # grouped pool exactly as before.
    for i, note in targets:
        try:
            supplied, sections = jdsection.read(note.body)
        except Exception as e:   # noqa: BLE001 -- reported per lead, see _SectionUnreadable
            results[i] = _SectionUnreadable(e)
            continue
        if sections > 1:
            _log.warning("triage: %s has %d %r sections; the first was used",
                         note.ref, sections, jdsection.HEADING)
        if not supplied:
            pooled.append((i, note))
            continue
        from_section.add(i)
        try:
            results[i] = dossier_cache.get_or_build(note.fm, supplied_jd=supplied)
        except Exception as e:   # noqa: BLE001 -- reduced to _FetchFailed, see its docstring
            results[i] = _FetchFailed(e)
    by_key = {}
    for i, note in pooled:
        by_key.setdefault(dossier_cache.cache_key(note.fm), []).append((i, note))
```

Delete the old `results = {}` and the old `by_key` loop over `targets`. Every `return results` becomes `return results, from_section`. The docstring's first paragraph becomes: "Returns ``({index into keeps: dossier | _FetchFailed | _SectionUnreadable}, from_section)`` -- `from_section` is the set of indices whose dossier was built from the lead note's own job description (#369), so the caller can word an `unjudgeable` reason correctly. A set of indices rather than a marker key: a marker would ride `slim()` into the judge prompt." Add a paragraph naming the section arm.

In `run`: `prefetched, from_section = _prefetch_dossiers(...)`. After the `_FetchFailed` check:

```python
            if isinstance(fetched, _SectionUnreadable):
                report.failures.append(f"section {note.ref}: {fetched.message}")
                continue
```

The `unjudgeable` reason:

```python
                reason = (
                    f"the job description in the note is shorter than min_jd_chars "
                    f"({dossier_cache.min_jd_chars})" if i in from_section
                    else f"no job description was fetched (floor: "
                         f"{dossier_cache.min_jd_chars} chars)")
```

`.rulesync/rules/CLAUDE.md`, the `min_jd_chars` paragraph: append "A job description the lead note carries (its `## Job description` section, `core/jdsection.py`, #369) obeys the same floor: `DossierCache.get_or_build(..., supplied_jd=...)` returns it without a fetch or a cache write, and `jd_arrived` judges it as it judges a fetch. It IS the posting for every other reading too, by the owner's ruling: triage's #223 `role_type` observation reads it, so a section stating a pay basis is recorded as `observed`."

`docs/USAGE.md`, the "A job description in the lead note" subsection (Task 3): "is used by `cv run` in place\nof a fetch" becomes "is used by `triage run` and `cv run` in place of a fetch, and is read as the posting itself (a pay basis it states is recorded as the lead's `role_type`)". Regenerate (`npm run rulesync`).

- [ ] **Step 4: Run, then commit**

Run: `.venv/bin/python -m pytest tests/test_triage_jd_section.py tests/test_triage_engine.py -q && .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass.

```bash
.venv/bin/ruff check sluice tests scripts
git add sluice/triage/engine.py tests/test_triage_jd_section.py .rulesync/rules/CLAUDE.md docs/USAGE.md
git commit -F - <<'EOF'
feat(triage): judge a lead from its note's job description (#369)

A non-empty "## Job description" section is used instead of a fetch,
per lead and outside the cache-key grouping. A section that cannot be
read is reported as such, and a short one is unjudgeable with its own
reason.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 5: Witnesses** (restore after each)

1. Delete the section arm's body after `from_section.add(i)` and replace with `pooled.append((i, note)); continue` (drop the supplied arm). `test_a_section_lead_with_no_url_is_judged_from_the_section`: red.
2. Change `if not supplied:` to `if not sections:`. `test_an_empty_section_is_no_section`: red on the `relevance_notes` assertion (the note-floor reason replaces "no job description was fetched", because `from_section` now holds the lead). The fetch count is still 1: `get_or_build(fm, supplied_jd="")` falls through to the fetcher, so do not expect zero fetches.
3. Group section notes by key: in the section arm, replace `results[i] = dossier_cache.get_or_build(note.fm, supplied_jd=supplied)` with `results[i] = next((results[j] for j in sorted(from_section) if j != i and dossier_cache.cache_key(keeps[j].fm) == dossier_cache.cache_key(note.fm)), None) or dossier_cache.get_or_build(note.fm, supplied_jd=supplied)` (a later section note reuses an earlier one's dossier at the same key). Both `test_two_notes_sharing_a_cache_key_each_get_their_own_section` rows: red (one sentinel missing). This one ADDS code, so it is a structural witness for the "not grouped" property, not a removal mutant; it is the only way to model the regression the spec names, and it is accepted as such: the repo's MOVE-or-DELETE rule exists to stop an equivalent mutant (a check added BESIDE the original), and this is a behaviour change, not a duplicate check.
4. Delete `from_section.add(i)`. `test_a_short_section_is_unjudgeable_with_the_note_reason`: red (the fetched reason).
5. Delete the `try`/`except` around `jdsection.read` (leave the read bare). `test_a_section_read_that_raises_skips_only_that_lead`: red (the run raises).
6. In `sluice/core/dossier.py::_assemble`, replace the `"role_type_observed": observe_role_type(jd_text({"jd": jd})),` line with `"role_type_observed": "",` (option (b), the ruling NOT taken). `test_a_section_that_states_a_pay_basis_is_observed_like_a_fetched_posting`: red (`role_type` stays `permanent`). Pre-existing #223 rows go red too; what this witness checks is that the NEW row does.

---

### Task 7: Write the section on create — facade, CLI, MCP

**Files:**
- Modify: `sluice/core/app.py` (`CreateLeadResult`, `Sluice.create_lead`), `sluice/cli.py` (`cmd_leads_add`, the `leads add` parser), `sluice/mcpserver.py` (`create_lead`, `create_lead_tool`)
- Modify: `tests/functional/test_mcp_contract.py` (pinned input set), `tests/test_app_injection.py` (its `upsert` fake, only if a new row needs it), `docs/USAGE.md`, `.rulesync/rules/CLAUDE.md` (the `leads add` paragraph)
- Test: `tests/test_leads_create_jd.py`, additions to `tests/test_leads_add_cli.py` and `tests/test_mcpserver.py`

**Interfaces:**
- Consumes: `jdsection.TAG`, `jdsection.render`, `jdsection.read` (Task 1); `UpsertResult.ref` (Task 5).
- Produces: `Sluice.create_lead(..., job_description: str | None = None) -> CreateLeadResult`; `CreateLeadResult.job_description_written: bool = False`, `CreateLeadResult.job_description_note: str = ""`; `leads add --jd-file PATH|-`; MCP `create_lead(..., job_description: str | None = None)` returning `job_description_written` and `job_description_note`.

- [ ] **Step 1: Write the failing facade tests**

```python
"""Sluice.create_lead(job_description=...) (#369, spec §3): the section is appended on
CREATE only, through Store.append_body_section with the store-issued UpsertResult.ref."""
import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.jdsection import read
from sluice.core.protocols import UpsertResult, VaultConflict
from sluice.core.vault import Vault

_URL = "https://example.invalid/jobs/1"
_JD = "Placeholder posting: build example services.\n## Benefits\nexample perks"
_OUTCOMES = ("updated", "merged", "refused", "merged_away", "merged_away_unproven")


def _app(store):
    return Sluice(Config(), store=store)


@pytest.mark.parametrize("layout", ["", "active_archive"])
def test_created_with_a_jd_appends_the_section(tmp_path, layout):
    store = Vault(str(tmp_path), lead_layout=layout)
    r = _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                job_description=_JD)
    assert (r.outcome, r.job_description_written, r.job_description_note) == (
        "created", True, "")
    note = next(n for n in store.read_leads() if n.slug == r.slug)
    text, count = read(note.body)
    assert count == 1
    assert text == "Placeholder posting: build example services.\n### Benefits\nexample perks"


class _SpyStore:
    """Answers upsert with a chosen outcome and records every append. `append` decides what
    append_body_section does: a bool to return, or an exception to raise. Raises TypeError on
    a None ref, as the vault's _cas_write path would."""
    def __init__(self, outcome="created", ref="ref-1", append=True):
        self.outcome, self.ref, self.append, self.appends = outcome, ref, append, []

    def upsert(self, lead):
        has = self.outcome in ("created", "updated", "merged")
        return UpsertResult(outcome=self.outcome, slug="Example Ltd - Example Role" if has else "",
                            ref=self.ref if has else None)

    def append_body_section(self, ref, tag, section_md):
        self.appends.append(ref)
        if ref is None:
            raise TypeError("expected str, bytes or os.PathLike object, not NoneType")
        if isinstance(self.append, Exception):
            raise self.append
        return self.append


def _create(store, jd=_JD):
    return _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                   job_description=jd)


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_no_append_for_any_outcome_but_created(outcome):
    store = _SpyStore(outcome=outcome)
    r = _create(store)
    assert store.appends == []
    assert r.job_description_written is False
    assert r.job_description_note == "the lead already existed, or was not created"


@pytest.mark.parametrize("append, note", [
    (False, "a job description section was already present"),
    (VaultConflict("raced"), "the note changed before the job description could be added"),
    (FileNotFoundError("moved"), "the note changed before the job description could be added"),
])
def test_an_append_that_does_not_land_keeps_the_created_outcome(append, note):
    r = _create(_SpyStore(append=append))
    assert (r.outcome, r.slug) == ("created", "Example Ltd - Example Role")
    assert (r.job_description_written, r.job_description_note) == (False, note)


def test_created_with_no_ref_attempts_no_append():
    store = _SpyStore(ref=None)
    r = _create(store)
    assert store.appends == []
    assert r.outcome == "created"
    assert (r.job_description_written, r.job_description_note) == (
        False, "the store did not return a reference to the new note")


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_jd_is_refused_before_anything_is_written(tmp_path, blank):
    store = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="job_description"):
        _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                job_description=blank)
    assert store.read_leads() == []


def test_no_jd_is_todays_behaviour():
    store = _SpyStore()
    r = _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL)
    assert store.appends == []
    assert (r.outcome, r.job_description_written, r.job_description_note) == (
        "created", False, "")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_create_jd.py -q`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'job_description'`.

- [ ] **Step 3: Implement the facade**

`CreateLeadResult`:

```python
    # #369: whether `job_description` was written as the note's `## Job description`
    # section, and if not, why -- one of a fixed set of phrases (Sluice.create_lead). The
    # lead itself is reported by `outcome`/`slug` either way.
    job_description_written: bool = False
    job_description_note: str = ""
```

`Sluice.create_lead`: signature gains `job_description: str | None = None`. Docstring gains a paragraph: "`job_description` (#369): `None` means none was given. A string is written as the note's `## Job description` section (`core/jdsection.py`) through `Store.append_body_section`, with the store-issued `UpsertResult.ref`, ONLY when this call CREATED the note -- `updated`/`merged` never touch an existing note's body (never-clobber). A blank string is refused with the other invalid fields, before anything is written: a caller that asked for a job description and silently got a lead without one is the quiet wrong default. Two writes, so a lead can exist without its section; `job_description_written` and `job_description_note` say which."

In the validation block, after the `url` check:

```python
        if job_description is not None and not job_description.strip():
            bad = sorted(set(bad) | {"job_description"})
```

Change the `ValueError` message to name a blank job description too: `f"unsafe or invalid field(s): {bad} (must be printable, contain no \" or \\, url must be present and http(s), and job_description, when given, must not be blank)"`. Check `tests/test_leads_create.py` for a test pinning the old message text by regex; it matches on field names, so it stays green.

Replace the tail:

```python
        if result.outcome not in ("created", "updated", "merged"):
            out = CreateLeadResult(outcome=result.outcome)
        else:
            out = CreateLeadResult(outcome=result.outcome, slug=result.slug)
        if job_description is None:
            return out
        out.job_description_note = self._write_job_description(store, result,
                                                               job_description)
        out.job_description_written = not out.job_description_note
        return out

    @staticmethod
    def _write_job_description(store, result, job_description) -> str:
        """Append the section to a note this call CREATED; return "" when written, else the
        fixed reason it was not (#369)."""
        from sluice.core import jdsection
        from sluice.core.protocols import VaultConflict
        if result.outcome != "created":
            return "the lead already existed, or was not created"
        if result.ref is None:
            # A store (or a fake) that does not honour UpsertResult.ref. Without this arm,
            # append_body_section(None, ...) raises TypeError, which neither caught type
            # below covers, and a created lead would read as a failed create.
            _log.warning("create_lead: the store returned no ref for %s; "
                         "the job description was not written", result.slug)
            return "the store did not return a reference to the new note"
        try:
            written = store.append_body_section(result.ref, jdsection.TAG,
                                                jdsection.render(job_description))
        except (VaultConflict, OSError) as e:
            # Caught HERE, not propagated: the lead WAS created, and letting this escape
            # would report a failed create for a note that exists, inviting a retry that
            # lands on `updated` and never writes the job description. OSError covers the
            # note being moved or renamed between create and append (`leads reconcile`,
            # Obsidian, Syncthing).
            _log.warning("create_lead: could not add the job description to %s: %s",
                         result.slug, e)
            return "the note changed before the job description could be added"
        return "" if written else "a job description section was already present"
```

- [ ] **Step 4: Run the facade tests**

Run: `.venv/bin/python -m pytest tests/test_leads_create_jd.py tests/test_leads_create.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing CLI and MCP tests**

`tests/test_leads_add_cli.py`, using its `_add` and `_notes` helpers:

```python
_JD_TEXT = "Placeholder posting: build example services."


def test_jd_file_writes_the_section(tmp_path, monkeypatch, capsys):
    f = tmp_path / "jd.md"; f.write_text(_JD_TEXT, encoding="utf-8")
    assert _add(tmp_path / "v", monkeypatch, "--jd-file", str(f)) == 0
    from sluice.core.jdsection import read
    assert read(_notes(tmp_path / "v")[0].body) == (_JD_TEXT, 1)


def test_jd_file_dash_reads_stdin(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(_JD_TEXT))
    assert _add(tmp_path / "v", monkeypatch, "--jd-file", "-") == 0
    from sluice.core.jdsection import read
    assert read(_notes(tmp_path / "v")[0].body)[0] == _JD_TEXT


def test_an_unreadable_jd_file_exits_1_and_creates_nothing(tmp_path, monkeypatch, capsys):
    rc = _add(tmp_path / "v", monkeypatch, "--jd-file", str(tmp_path / "missing.md"))
    assert rc == 1
    assert "could not read --jd-file" in capsys.readouterr().err
    assert _notes(tmp_path / "v") == []


def test_a_non_utf8_jd_file_exits_1_and_creates_nothing(tmp_path, monkeypatch, capsys):
    f = tmp_path / "jd.md"; f.write_bytes(b"\xff\xfe\x00bad")
    assert _add(tmp_path / "v", monkeypatch, "--jd-file", str(f)) == 1
    assert "could not read --jd-file" in capsys.readouterr().err
    assert _notes(tmp_path / "v") == []


@pytest.mark.parametrize("content", ["", "   \n\t"])
def test_a_blank_jd_file_exits_2_naming_the_field(tmp_path, monkeypatch, capsys, content):
    f = tmp_path / "jd.md"; f.write_text(content, encoding="utf-8")
    assert _add(tmp_path / "v", monkeypatch, "--jd-file", str(f)) == 2
    assert "job_description" in capsys.readouterr().err
    assert _notes(tmp_path / "v") == []


def test_a_jd_for_an_existing_lead_prints_the_not_written_note(tmp_path, monkeypatch, capsys):
    # Review Focus 5.
    f = tmp_path / "jd.md"; f.write_text(_JD_TEXT, encoding="utf-8")
    assert _add(tmp_path / "v", monkeypatch) == 0
    capsys.readouterr()
    rc = _add(tmp_path / "v", monkeypatch, "--jd-file", str(f))
    err = capsys.readouterr().err
    assert rc == 0
    assert "the lead already existed, or was not created" in err
    assert "## Job description" in err
```

If `main` with a `ValueError` raises `SystemExit(2)` rather than returning 2, wrap the blank-file row's call in `pytest.raises(SystemExit)` and assert its `code == 2`; check how an existing invalid-`--url` test in this file asserts it and copy that.

`tests/test_mcpserver.py`, beside the existing `create_lead` rows:

```python
def test_create_lead_writes_the_job_description_and_reports_it(tmp_path):
    out = create_lead(_app(tmp_path), "Example Role", "Example Ltd",
                      "https://example.invalid/1", job_description="Placeholder posting.")
    assert out["outcome"] == "created"
    assert (out["job_description_written"], out["job_description_note"]) == (True, "")


def test_create_lead_with_a_blank_job_description_creates_nothing(tmp_path):
    with pytest.raises(ValueError, match="job_description"):
        create_lead(_app(tmp_path), "Example Role", "Example Ltd",
                    "https://example.invalid/1", job_description="  ")
    assert Vault(str(tmp_path)).read_leads() == []


def test_create_lead_without_a_job_description_reports_no_jd_keys(tmp_path):
    # arc-402: the keys appear only when a job description was GIVEN, as the CLI's note
    # does; `job_description_written: false` beside `outcome: created` would read as a
    # failed write to a model that never asked for one.
    out = create_lead(_app(tmp_path), "Example Role", "Example Ltd", "https://example.invalid/1")
    assert out["outcome"] == "created"
    assert "job_description_written" not in out and "job_description_note" not in out
```

`tests/functional/test_mcp_contract.py`: the pinned `create_lead` set gains `"job_description"`.

- [ ] **Step 6: Implement the CLI and MCP**

Parser, after `--role-type`'s `add_argument`:

```python
    # #369: the posting's text, for a lead with no page to fetch (a referral, an InMail).
    # Written as the note's `## Job description` section on CREATE only; triage and cv read
    # it in place of a fetch. `-` reads stdin, matching the evidence commands' --body-file.
    ad.add_argument("--jd-file", metavar="PATH",
                    help="a file holding the job description (- for stdin), written into "
                         "the new note's '## Job description' section")
```

`cmd_leads_add`, before `Sluice(config).create_lead(...)`:

```python
    job_description = None
    if args.jd_file:
        # Read BEFORE create_lead, in its own try, so a bad path or a non-UTF-8 file stops
        # here with nothing written -- the `--body-file` shape
        # (sluice/evidence/commands.py::cmd_evidence_add). UnicodeDecodeError is a
        # ValueError, which that precedent's `except OSError` does not catch. A blank file
        # is NOT refused here: it is passed through, and create_lead's own validation
        # refuses it (exit 2 via main), the one home of that rule for the CLI and MCP alike.
        try:
            if args.jd_file == "-":
                job_description = sys.stdin.read()
            else:
                with open(args.jd_file, encoding="utf-8") as fh:
                    job_description = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"leads add: could not read --jd-file {args.jd_file!r}: {e}",
                  file=sys.stderr)
            return 1
```

Pass `job_description=job_description` to `create_lead`. After the existing `print(prefix + detail, ...)`:

```python
    if job_description is not None and not result.job_description_written:
        print(f"leads add: the job description was NOT written "
              f"({result.job_description_note}) -- add a '## Job description' section to "
              f"the note by hand", file=sys.stderr)
```

The return line is unchanged.

`sluice/mcpserver.py::create_lead`: signature gains `job_description: str | None = None`; pass it through to `sluice.create_lead`. After `out` is built, set both keys ONLY when a job description was given (arc-402: matching the CLI's condition and the tool's existing sparse-key shape, so a call that never asked for one is not told `written: false`):

```python
    if job_description is not None:
        out["job_description_written"] = result.job_description_written
        out["job_description_note"] = result.job_description_note
```

Docstring gains: "`job_description` is written as the new note's `## Job description` section only when the lead is CREATED; `job_description_written`/`job_description_note` are returned only when a `job_description` was given, and report whether it was written and why not. A blank one is refused. Triage and cv read that text as untrusted page text, exactly like a fetched posting."

`create_lead_tool`: add `job_description: str | None = None` to the signature and the call; its docstring gains: "job_description: the posting's text, for a lead with no page to fetch. Written only when the lead is CREATED, and treated as untrusted page text by the triage judge and the CV composer."

`docs/USAGE.md`: in the `leads add` reference, add `--jd-file PATH|-` with one line: "the job description, written into the new note's `## Job description` section (created leads only; a blank file is refused)." In the "A job description in the lead note" subsection, append: "`leads add --jd-file` and MCP `create_lead`'s `job_description` write this section when they create a lead."

The two Task 3 comments now become true and gain their clause (rev-402): `sluice/cv/engine.py::CvResult`'s `jd_source` comment, after "Reported so a JD taken from the note is never invisible", adds ": a model can write that section through MCP create_lead, and that is ruled report-only (spec Decisions)"; `sluice/cli.py::cmd_cv_run`'s note-JD count comment gains " -- a model can write that section through MCP create_lead, which is ruled report-only (spec Decisions)".

`.rulesync/rules/CLAUDE.md`, the `leads add` paragraph (starts "`leads add` is the only route into the lead store"), append: "`--jd-file` (#369) writes the posting's text as the new note's `## Job description` section, through `Store.append_body_section` with the store-issued `UpsertResult.ref`, on `created` only; a blank one is refused by `create_lead` itself, so the CLI and MCP share the rule." Regenerate.

- [ ] **Step 7: Run, then commit**

Run: `.venv/bin/python -m pytest tests/test_leads_create_jd.py tests/test_leads_add_cli.py tests/test_mcpserver.py tests/functional/test_mcp_contract.py tests/test_docs_claims.py -q && .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass.

```bash
.venv/bin/ruff check sluice tests scripts
git add -A sluice tests docs/USAGE.md .rulesync/rules/CLAUDE.md
git commit -F - <<'EOF'
feat(leads): write a job description when adding a lead (#369)

leads add --jd-file and MCP create_lead(job_description=...) write the
note's "## Job description" section when the lead is created, and say
when and why it was not written. A blank job description is refused
before anything is written.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 8: Witnesses** (restore after each)

1. Delete `if result.outcome != "created": return ...` from `_write_job_description`. `test_no_append_for_any_outcome_but_created[updated]`: red (an append recorded).
2. Narrow `except (VaultConflict, OSError)` to `except VaultConflict`. The `FileNotFoundError` row of `test_an_append_that_does_not_land_keeps_the_created_outcome` (`-v` shows it): red (it raises).
3. Delete the `if result.ref is None:` block. `test_created_with_no_ref_attempts_no_append`: red (`TypeError` escapes).
4. Delete the two validation lines `if job_description is not None and not job_description.strip():` / `bad = sorted(set(bad) | {"job_description"})`. `test_a_blank_jd_is_refused_before_anything_is_written`, `test_a_blank_jd_file_exits_2_naming_the_field`, `test_create_lead_with_a_blank_job_description_creates_nothing`: red (a lead is created).
5. In `cmd_leads_add`, delete the `try:`/`except` around the read (leave the read bare). `test_an_unreadable_jd_file_exits_1_and_creates_nothing`: red (a traceback, not the named message).
6. In `mcpserver.py::create_lead`, delete the `if job_description is not None:` line and dedent its two assignments. `test_create_lead_without_a_job_description_reports_no_jd_keys`: red.

---

### Task 8: Architecture doc, comment sweep, final verification

**Files:**
- Modify: `docs/ARCHITECTURE.md`, any stale comment found below
- No new tests

- [ ] **Step 1: `docs/ARCHITECTURE.md`**

- In the dossier description: the supplied arm (`get_or_build(..., supplied_jd=...)`: no fetch, no cache read or write; `jd_arrived` judges it) and `core/jdsection.py` as the one grammar shared by triage, cv and `create_lead`.
- Where it says a thin or missing JD costs only quality (find with `grep -n -i "quality" docs/ARCHITECTURE.md`): extend with "-- and under `cv.blind_hold` (on by default) such a CV is held for sign-off rather than served (#369)".
- In the store-contract description: `UpsertResult.ref`, populated exactly when `slug` is.
- The cv holds paragraph in section "3. cv" (the one naming `cv.require_signoff`, the #333 unaudited hold, `cv.style_hold` and `framing\t` entries): add `cv.blind_hold` -- on by default, its own key, a `blind\t<reason>` entry from three fixed phrases, not a gate finding and not `require_signoff`'s question -- and reword "neither signoff flag touches the pure hard gate" to name all three keys (`require_signoff`, `style_hold`, `blind_hold`).
- The `leads add` paragraph: `--jd-file` / `create_lead(job_description=...)` is a SECOND write (`append_body_section` through `UpsertResult.ref`) made only when the lead was `created`, so a lead can exist without its section; `job_description_written`/`job_description_note` say which.

- [ ] **Step 2: Sweep for stale claims**

```bash
grep -rn --exclude-dir=superpowers "composed blind\|visibility, never control flow\|Visibility, never control flow\|proceeded blind\|auto-serve\|held back by\|Control flow is deliberately unchanged\|does not change the status" sluice tests docs .rulesync sluice.yaml.example
grep -rn "get_or_build(self, fm)" tests | wc -l
```

`docs/superpowers/` is excluded: specs and plans are historical. Every remaining "composed blind" in comments or docs is rewritten to "composed without a job description". Every "auto-serve", "held back by", "Control flow is deliberately unchanged" or "does not change the status" hit must already carry Task 4's rewording; fix any that does not. Every "visibility, never control flow" that cites `dossier_failed` as the precedent is reworded per Task 4 Step 5. The `_prefetch_dossiers` docstring was updated in Task 6; confirm it.

- [ ] **Step 3: Full verification**

```bash
.venv/bin/ruff check sluice tests scripts
.venv/bin/python -m pytest -q -p no:cacheprovider
TZ=Asia/Dubai .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dossier_supplied_jd.py tests/test_triage_jd_section.py
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q -p no:cacheprovider
npm run rulesync && git status --short
```

Expected: ruff clean; every suite green; `git status` shows only the files this task edits (rulesync outputs are gitignored).

- [ ] **Step 4: Commit**

```bash
git add -A docs sluice tests
git commit -F - <<'EOF'
docs: describe the lead-supplied job description and the blind hold (#369)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

- [ ] **Step 5: Hand off to review**

Run `/review-pr` locally before pushing (owner's rule), fold its findings, then push and open the PR with `Closes #369` in the body, and send the push notification that the PR is open.
