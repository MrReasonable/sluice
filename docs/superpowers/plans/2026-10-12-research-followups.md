# Research Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver every `research` verdict's `Next:` instruction as a worklist, report the standing research queue in the triage digest, let the user mark a lead asked (skipped by the nightly re-judge) and record the reply, which the judge then reads.

**Architecture:** A pure `core/followups.py` owns the answers-section grammar (built on a generalised `core/jdsection.sections()`), the one `awaiting_reply` predicate and the queue builder. `triage/apply.py` writes `triage_next` and clears a stale `followup_asked`; `triage/engine.py` skips awaiting leads and adds answers to each lead's own dossier copy. `Sluice` gains `followups()`, `mark_asked()` and `record_answer()`; `cli.py` gains three `leads` subcommands and one digest line.

**Tech Stack:** Python 3.12+, standard library only in `sluice/`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-research-followups-design.md` (commit `76f52449`).

## Global Constraints

- `sluice/` is standard-library only; no new dependency.
- No config keys are added (the spec says so; CONFIGURATION.md states it).
- Fixtures are synthetic: job TITLES come from the seeded-faker `titles` fixture (never typed by hand, not even `Example Role`); company names are `Example ...`; free text is SENTINEL-style placeholder text; every count asserted is built by the test itself.
- Pinned dates are ones the host clock cannot produce (`TODAY = "2031-03-14"`), so a function that reads the real clock instead of the injected one goes red on any day the suite runs.
- Every commit passes the whole suite on its own (Task 8 checks each one), so a doc a guard ties to a command lands in the same commit as the command.
- New frontmatter keys: `triage_next`, `followup_asked`. Neither joins `core/leads.py::FRAMING_KEYS`.
- Answers heading: `## Follow-up answers`, bare; per-answer tag on its own line: `<!-- sluice:followup-answer <12 hex> -->`, the hex being sha256 of `"<date>\n<text>"`.
- Outcome names: `asked`, `answered`, `unchanged`, `refused_status`, `refused_unwritable`, `answered_not_cleared`, `answered_hidden`, `not_found`, `ambiguous`, `conflict`. Exit 0 only for `asked`, `answered`, `unchanged`.
- Commands: `leads followups [--json]`, `leads mark-asked --lead SLUG`, `leads answer --lead SLUG --text TEXT`.
- Dates in the facade come from `Sluice`'s injected `today` callable (`self._today or _today`), called.
- Conventional Commits, no `!`; every commit body ends with `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Never cite a line number in a comment (`tests/test_citation_drift.py`).
- Commit before every mutation witness; restore with Edit, never `git checkout` onto a dirty file.

## Review Focus

1. A `followup_asked` typed by hand in a non-date form (`yes`, `asked`) -- the lead still counts as awaiting, and its "waiting" age prints `?` rather than raising. (Test in Task 1.)
2. A lead whose status is typed with different case or an alias (`Research`) -- read as `research` everywhere, because every reader goes through `status.normalize`. (Test in Task 1.)
3. A slug with spaces and punctuation passed to `--lead` -- resolved exactly, never by substring. (Test in Task 5.)
4. An answer pasted with several lines and its own `## ` heading -- demoted, so the section is not cut short and the judge sees all of it. (Test in Task 1 round-trip and Task 4.)
5. A vault with no `research` leads at all -- the digest gains no line and `leads followups` says the queue is empty rather than printing two empty headings. (Tests in Task 5.)

---

### Task 0: Worktree environment

**Files:** none tracked.

- [ ] **Step 1: Build a venv for this worktree** (a symlinked venv broke a path test in #369)

Run from the worktree's top level. `uv` is whatever real `uv` binary the machine has; a
version-manager shim can fail silently outside a venv, so resolve it to a real path first.

```bash
cd "$(git rev-parse --show-toplevel)"
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -e ".[test]" ruff==0.15.21
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
.venv/bin/python -m pytest -q tests/test_no_leaked_files.py   # the plan itself is a tracked file
```

- [ ] **Step 2: Baseline suite**

Run: `.venv/bin/python -m pytest -q -x 2>&1 | tail -3`
Expected: all pass (some skips on macOS for normalization-sensitive rows).

---

### Task 1: `jdsection.sections()` and `core/followups.py`

**Files:**
- Modify: `sluice/core/jdsection.py`
- Create: `sluice/core/followups.py`
- Modify: `tests/test_core_layering.py` (`expected = 35` -> `36`)
- Test: `tests/test_jdsection.py`, create `tests/test_followups.py`

**Interfaces:**
- Produces: `jdsection.sections(body: str, heading: str, tag: str = "") -> list[str]`; `jdsection.demote(text: str) -> str`; `jdsection.neutralise(text: str) -> str` (demote, then close a fence the text leaves open); `followups.ANSWERS_HEADING`; `followups.answer_tag(text: str, today: str) -> str`; `followups.render_answer(text: str, today: str) -> str`; `followups.answers(body: str) -> str`; `followups.awaiting_reply(fm: dict) -> bool`; `followups.is_research(fm: dict) -> bool`; `followups.FollowupRow`, `followups.FollowupQueue` (with `.rows`, `.never_asked`, `.asked`, `.oldest_days`); `followups.build_queue(notes, today: str) -> FollowupQueue`.

- [ ] **Step 1: Write the failing jdsection tests** (append to `tests/test_jdsection.py`)

```python
def test_sections_returns_every_section_in_order():
    body = "## Notes\nx\n## Other\nfirst\n## Other\nsecond\n# End\n"
    assert jdsection.sections(body, "## Other") == ["first", "second"]


def test_sections_default_empty_tag_still_matches_a_bare_heading():
    # `bare[:-len("")]` is `bare[:0]`, the empty string: an unguarded strip would match nothing.
    assert jdsection.sections("## Other\nkept\n", "## Other") == ["kept"]


def test_sections_honours_fences():
    body = "```\n## Other\nfenced\n```\n## Other\nreal\n"
    assert jdsection.sections(body, "## Other") == ["real"]


def test_sections_a_heading_with_other_trailing_text_is_not_a_section():
    assert jdsection.sections("## Other:\nx\n", "## Other") == []


def test_read_is_sections_reduced_to_the_first_and_the_count():
    body = f"{HEADING} {TAG}\nfirst\n{HEADING}\nsecond\n"
    assert read(body) == ("first", 2)
    assert jdsection.sections(body, HEADING, TAG) == ["first", "second"]


def test_demote_rewrites_only_breaking_lines():
    assert jdsection.demote("a\n## b\n  # c\n### d") == "a\n### b\n  ### c\n### d"


def test_neutralise_closes_a_fence_the_text_leaves_open():
    # inv-002: an unclosed fence runs to the end of the body, so every LATER section heading
    # would read as fenced code and be skipped. Witness: return `demote(text)` unclosed.
    body = "## Other\n" + jdsection.neutralise("x\n~~~~\ny") + "\n## Other\nlater\n"
    assert jdsection.sections(body, "## Other") == ["x\n~~~~\ny\n~~~~", "later"]


def test_neutralise_leaves_a_closed_fence_alone():
    assert jdsection.neutralise("```\n## a\n```") == "```\n### a\n```"


def test_a_job_description_left_open_no_longer_hides_a_later_section():
    body = render("posting\n```") + "\n\n## Other\nlater\n"
    assert jdsection.sections(body, "## Other") == ["later"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_jdsection.py -q 2>&1 | tail -3`
Expected: FAIL with `AttributeError: module 'sluice.core.jdsection' has no attribute 'sections'` (and `demote`, `neutralise`), and the open-fence JD row red on the assertion.

- [ ] **Step 3: Implement in `sluice/core/jdsection.py`**

Change the module docstring's first line from `"""The ONE grammar of a lead note's \`## Job description\` section (#369).` to `"""The one grammar of a lead note's body sections: \`## Job description\` (#369) and \`## Follow-up answers\` (#378).` and add to its second paragraph: `` `core/followups.py` reads its own section through `sections()` below, so a fence or a level-1/2 heading means the same thing in both. `` Replace `_outside_fences`, `_is_heading` and `read`, and split the demotion out of `render`:

```python
def _scan_fences(lines: list) -> tuple:
    """`(flags, opener)`: for each line, False when it is a fence line or inside fenced code;
    and the opener still open at the end ("" when none). A fence closes on a run of the SAME
    character at least as long as the opener with nothing after it; an unclosed fence runs
    to the end of the body, as CommonMark has it."""
    flags, opener = [], ""
    for line in lines:
        m = _FENCE.match(line)
        if not opener:
            if m:
                opener = m.group(1)
            flags.append(not m)
            continue
        if m and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) \
                and not line[m.end():].strip():
            opener = ""
        flags.append(False)
    return flags, opener


def _outside_fences(lines: list) -> list:
    return _scan_fences(lines)[0]


def _is_heading(line: str, heading: str, tag: str) -> bool:
    bare = line.rstrip()
    # Guarded on a non-empty tag: `bare[:-len("")]` is `bare[:0]`, the empty string, so an
    # unguarded strip of an empty tag would match no heading at all.
    if tag and bare.endswith(tag):
        bare = bare[: -len(tag)].rstrip()
    return bare.casefold() == heading.casefold()


def sections(body: str, heading: str, tag: str = "") -> list:
    """The text of EVERY section headed `heading` (optionally followed by `tag` on the heading
    line), each stripped, in body order. A section ends at the next level-1 or level-2 heading
    outside fenced code -- including another `heading`, so consecutive sections stay apart."""
    lines = _lines(body)
    outside = _outside_fences(lines)
    found = []
    for start, line in enumerate(lines):
        if not (outside[start] and _is_heading(line, heading, tag)):
            continue
        text = []
        for i in range(start + 1, len(lines)):
            if outside[i] and breaks_section(lines[i]):
                break
            text.append(lines[i])
        found.append("\n".join(text).strip())
    return found


def read(body: str) -> tuple:
    """`(text, count)`: the first section's text, stripped, and how many `HEADING` lines the
    body holds -- so the CALLER can warn about duplicates. `("", 0)` with no section. A
    hand-typed heading without the tag reads the same as a written one; a heading with any
    other text on its line (`## Job description:`) is not the section."""
    found = sections(body, HEADING, TAG)
    return (found[0], len(found)) if found else ("", 0)


def demote(text: str) -> str:
    """`text` with every breaking line demoted to level 3 (its leading `#` run replaced by
    `###`, indentation kept), so pasted text cannot end the section it is written into."""
    return "\n".join(_HASHES.sub(r"\1###", line) if breaks_section(line) else line
                     for line in _lines(text))


def neutralise(text: str) -> str:
    """`text` made safe to write as one section: `demote`d, then, when it leaves a fence
    open, closed with that same opener. Without the close, an unclosed fence runs to the end
    of the BODY, so every later section's heading reads as fenced code and is skipped -- a
    `## Follow-up answers` written after a pasted job description would never reach the
    judge (#378)."""
    out = demote(text)
    opener = _scan_fences(_lines(out))[1]
    return f"{out}\n{opener}" if opener else out


def render(text: str) -> str:
    """The section to append: the tagged heading, a blank line, then `text` neutralised (see
    `neutralise`). Heading levels change and an open fence is closed; nothing is refused. A
    `## Job description` line inside the text is demoted like any other, so a written note
    carries exactly one section."""
    return f"{HEADING} {TAG}\n\n" + neutralise(text)
```

- [ ] **Step 4: Run jdsection tests**

Run: `.venv/bin/python -m pytest tests/test_jdsection.py tests/test_triage_jd_section.py tests/test_leads_create_jd.py tests/test_cv_jd_source.py -q 2>&1 | tail -3`
Expected: PASS (the existing tests pin `read()`'s behaviour unchanged).

- [ ] **Step 5: Write the failing followups tests** (create `tests/test_followups.py`)

```python
"""core/followups.py (#378): the answers-section grammar, the one awaiting predicate and the
queue the worklist and the triage digest both read."""
from datetime import date, timedelta

import pytest

from sluice.core import followups
from sluice.core.protocols import LeadNote

# A date the host clock cannot produce, so a function reading the real clock instead of the
# `today` it is handed goes red on whatever day the suite runs (tst-001).
TODAY = "2031-03-14"


def _ago(days):
    return (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()


def _note(slug, fm, body=""):
    return LeadNote(ref=slug, slug=slug, fm=fm, body=body, status=fm.get("status", ""))


def test_a_rendered_answer_reads_back():
    body = "# Lead\n\n" + followups.render_answer("Client is Example Ltd", TODAY) + "\n"
    assert followups.answers(body) == f"{TODAY}: Client is Example Ltd"


def test_several_answers_concatenate_in_body_order():
    body = ("# Lead\n\n" + followups.render_answer("first", _ago(11)) + "\n\n## Notes\nmine\n\n"
            + followups.render_answer("second", TODAY) + "\n")
    assert followups.answers(body) == f"{_ago(11)}: first\n\n{TODAY}: second"


def test_an_unclosed_fence_in_one_answer_does_not_hide_the_next():
    # inv-002. Witness: render_answer calling `jdsection.demote` instead of `neutralise`.
    body = ("# Lead\n\n" + followups.render_answer("pasted\n```", _ago(1)) + "\n\n"
            + followups.render_answer("SENTINEL-SECOND", TODAY) + "\n")
    assert f"{TODAY}: SENTINEL-SECOND" in followups.answers(body)


def test_tag_lines_are_dropped_and_a_hand_typed_section_reads():
    body = "## Follow-up answers\nSENTINEL hand-typed answer\n"
    assert followups.answers(body) == "SENTINEL hand-typed answer"
    assert "sluice:followup-answer" not in followups.answers(
        followups.render_answer("x", TODAY))


def test_a_pasted_heading_is_demoted_so_the_whole_answer_is_read():
    text = "line one\n## Their notes\nline three"
    out = followups.answers(followups.render_answer(text, TODAY))
    assert out == f"{TODAY}: line one\n### Their notes\nline three"


def test_a_heading_with_trailing_text_is_not_the_section():
    assert followups.answers("## Follow-up answers:\nx\n") == ""


def test_the_tag_covers_date_and_text():
    assert followups.answer_tag("no", _ago(11)) != followups.answer_tag("no", TODAY)
    assert followups.answer_tag("no", TODAY) == followups.answer_tag("no", TODAY)
    assert followups.answer_tag("no", TODAY) != followups.answer_tag("yes", TODAY)


@pytest.mark.parametrize("fm, expected", [
    ({"status": "research", "followup_asked": TODAY}, True),
    ({"status": "Research", "followup_asked": TODAY}, True),     # Review Focus 2
    ({"status": "research", "followup_asked": "yes"}, True),     # Review Focus 1
    ({"status": "research", "followup_asked": ""}, False),
    ({"status": "research", "followup_asked": "   "}, False),
    ({"status": "research"}, False),
    ({"status": "new", "followup_asked": TODAY}, False),
    ({"status": "shortlist", "followup_asked": TODAY}, False),
])
def test_awaiting_reply(fm, expected):
    assert followups.awaiting_reply(fm) is expected


def test_build_queue_groups_ages_and_orders_newest_first():
    notes = [
        _note("Old", {"status": "research", "first_seen": _ago(41),
                      "triage_next": "ask recruiter: client name"}),
        _note("New", {"status": "research", "first_seen": _ago(2), "triage_next": ""}),
        _note("Asked", {"status": "research", "first_seen": _ago(11),
                        "followup_asked": _ago(7), "triage_next": "ask: band"}),
        _note("Typed", {"status": "research", "first_seen": "nope", "followup_asked": "yes"}),
        _note("Stale", {"status": "shortlist", "first_seen": _ago(11),
                        "followup_asked": _ago(7)}),
    ]
    q = followups.build_queue(notes, TODAY)
    assert [r.slug for r in q.never_asked] == ["New", "Old"]
    assert [r.slug for r in q.asked] == ["Asked", "Typed"]
    rows = {r.slug: r for r in q.rows}
    assert rows["Old"].age_days == 41 and rows["Old"].next_action == "ask recruiter: client name"
    assert rows["Asked"].waited_days == 7
    assert rows["Typed"].age_days is None and rows["Typed"].waited_days is None
    assert "Stale" not in rows
    assert q.oldest_days == 41


def test_an_empty_queue_has_no_oldest():
    q = followups.build_queue([], TODAY)
    assert q.rows == () and q.oldest_days is None


def test_module_is_pure():
    src = open(followups.__file__, encoding="utf-8").read()
    # The clock is a parameter: a real-clock read here would make every age depend on the
    # host date, which the pinned TODAY above exists to catch.
    for banned in ("import os", "open(", "import sys", "date.today", "datetime.now"):
        assert banned not in src
```

- [ ] **Step 6: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_followups.py -q 2>&1 | tail -3`
Expected: FAIL with `ImportError: cannot import name 'followups'`.

- [ ] **Step 7: Create `sluice/core/followups.py`**

```python
"""Research follow-ups (#378): the `## Follow-up answers` grammar, the ONE `awaiting_reply`
predicate, and the queue `leads followups` and the triage digest both read.

A `research` verdict ends in a question for a human to ask (`triage_next`). Marking a lead
asked stamps `followup_asked`, and triage then skips it; recording the reply appends a
`## Follow-up answers` section and clears the stamp, and the next triage run shows the judge
the answers. Pure and stdlib-only, beside `core/jdsection.py`, whose section grammar this
reuses: triage, the facade and the CLI all read it, and `core/` may not import a sub-app.

Every reader of "is this lead awaiting a reply" calls `awaiting_reply`, so the worklist's
grouping and triage's skip cannot disagree. A hand-typed BLOCK value under `followup_asked`
reads blank through the store's single-line read, so such a lead is judged rather than
skipped -- the visible direction -- and every writer of the key preserves it.
"""
import hashlib
import re
from dataclasses import dataclass
from datetime import date

from sluice.core import jdsection
from sluice.core import status as _status

ANSWERS_HEADING = "## Follow-up answers"
_TAG_PREFIX = "<!-- sluice:followup-answer "
_TAG_LINE = re.compile(r"^<!-- sluice:followup-answer [0-9a-f]{12} -->$")


def answer_tag(text: str, today: str) -> str:
    """The idempotence tag for one answer. Hashes the date AND the text: the same words on
    the same day are a re-run and append nothing, while the same words on a later day (a
    second "no") are a new reply and are written."""
    digest = hashlib.sha256(f"{today}\n{text}".encode("utf-8")).hexdigest()[:12]
    return f"{_TAG_PREFIX}{digest} -->"


def render_answer(text: str, today: str) -> str:
    """One answer as its own section: a bare heading (exactly what a person would type), the
    tag on its own line, then the dated text neutralised -- breaking lines demoted and an
    open fence closed, so neither this answer nor a later one is cut short or hidden."""
    return f"{ANSWERS_HEADING}\n\n{answer_tag(text, today)}\n{today}: {jdsection.neutralise(text)}"


def answers(body: str) -> str:
    """Every `## Follow-up answers` section's text, tag lines dropped, joined in body order.
    EVERY section, not the first: each answer is its own section, and taking one would
    silently drop every later reply."""
    parts = []
    for text in jdsection.sections(body, ANSWERS_HEADING):
        kept = "\n".join(line for line in text.split("\n")
                         if not _TAG_LINE.match(line.strip())).strip()
        if kept:
            parts.append(kept)
    return "\n\n".join(parts)


def is_research(fm: dict) -> bool:
    return _status.normalize(fm.get("status", "") or "") == "research"


def awaiting_reply(fm: dict) -> bool:
    """At `research` and marked asked. The one predicate (see the module docstring)."""
    return is_research(fm) and bool((fm.get("followup_asked", "") or "").strip())


def _days_since(value: str, today: str):
    """Whole days from `value` to `today`, or None when `value` is not an ISO date. A
    hand-typed value prints as `?` in the worklist; it is never raised."""
    try:
        return (date.fromisoformat(today) - date.fromisoformat((value or "").strip()[:10])).days
    except ValueError:
        return None


@dataclass(frozen=True)
class FollowupRow:
    slug: str
    company: str
    role: str
    age_days: int | None
    next_action: str
    asked: str                 # the `followup_asked` stamp; "" when never asked
    waited_days: int | None


@dataclass(frozen=True)
class FollowupQueue:
    rows: tuple = ()

    @property
    def never_asked(self) -> tuple:
        return tuple(r for r in self.rows if not r.asked)

    @property
    def asked(self) -> tuple:
        return tuple(r for r in self.rows if r.asked)

    @property
    def oldest_days(self):
        ages = [r.age_days for r in self.rows if r.age_days is not None]
        return max(ages) if ages else None


def build_queue(notes, today: str) -> FollowupQueue:
    """Every `research` lead among `notes`, newest first (an undatable lead last)."""
    rows = []
    for n in notes:
        if not is_research(n.fm):
            continue
        asked = (n.fm.get("followup_asked", "") or "").strip() if awaiting_reply(n.fm) else ""
        rows.append(FollowupRow(
            slug=n.slug, company=n.fm.get("company", "") or "", role=n.fm.get("role", "") or "",
            age_days=_days_since(n.fm.get("first_seen", ""), today),
            next_action=(n.fm.get("triage_next", "") or "").strip(),
            asked=asked, waited_days=_days_since(asked, today) if asked else None))
    rows.sort(key=lambda r: (r.age_days is None, r.age_days or 0))
    return FollowupQueue(rows=tuple(rows))
```

- [ ] **Step 8: Bump the core module count**

In `tests/test_core_layering.py`, `test_core_module_discovery_finds_the_real_fleet`: `expected = 35` -> `expected = 36`.

- [ ] **Step 9: Run**

Run: `.venv/bin/python -m pytest tests/test_followups.py tests/test_jdsection.py tests/test_core_layering.py -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add sluice/core/jdsection.py sluice/core/followups.py tests/test_jdsection.py tests/test_followups.py tests/test_core_layering.py
git commit -F - <<'EOF'
feat(core): read and write research follow-up answers (#378)

jdsection.sections() returns every section under a heading; read() is now
that reduced to the first. core/followups.py owns the answers section, the
one awaiting_reply predicate and the queue the worklist and digest read.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 2: `apply_verdict` writes `triage_next` and clears a stale stamp

**Files:**
- Modify: `sluice/triage/apply.py` (`_HAND_EDITABLE_KEYS`, `apply_verdict`)
- Modify: `sluice/core/protocols.py` (`Store.update_fields` declares `require_unchanged`)
- Modify: `sluice/triage/engine.py` (the verdict-site comment on `unchanged`)
- Test: `tests/test_apply.py`; `tests/conformance/test_store_contract.py`

**Interfaces:**
- Consumes: `Vault.update_fields(..., require_unchanged=...)`, which exists (#223) but is not yet on the Store Protocol; this task puts it there, since a correctness rule now leans on it.
- Produces: frontmatter `triage_next` on every verdict; `followup_asked` blanked by a verdict on a stamped snapshot, guarded by `require_unchanged`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_apply.py`)

```python
def _research(v, name, extra=()):
    _note(v, name, ['company: "Example Ltd"', "status: research", "score: 60",
                    'relevance_notes: ""', *extra])
    return v.read_leads()[0]


def test_a_verdict_writes_its_next_action_to_triage_next(tmp_path):
    v = Vault(str(tmp_path))
    note = _research(v, "N.md")
    verdict = {"verdict": "research", "relevance_score": 65,
               "recommended_next_action": "ask recruiter: client name"}
    assert apply_verdict(v, note, verdict, {}) == "applied"
    assert v.read_leads()[0].fm["triage_next"] == "ask recruiter: client name"


def test_a_later_verdict_with_no_action_blanks_triage_next(tmp_path):
    v = Vault(str(tmp_path))
    note = _research(v, "N.md", ['triage_next: "ask: band"'])
    assert apply_verdict(v, note, {"verdict": "research", "relevance_score": 61}, {}) == "applied"
    assert v.read_leads()[0].fm["triage_next"] == ""


def test_an_unsafe_next_action_blanks_triage_next_with_a_warning(tmp_path, caplog):
    v = Vault(str(tmp_path))
    note = _research(v, "N.md", ['triage_next: "ask: band"'])
    verdict = {"verdict": "research", "relevance_score": 62,
               "recommended_next_action": 'ask "them"'}
    assert apply_verdict(v, note, verdict, {}) == "applied"
    assert v.read_leads()[0].fm["triage_next"] == ""      # never the stale instruction
    assert "triage_next" in caplog.text


def test_triage_next_and_followup_asked_are_not_cv_framing():
    assert "triage_next" not in FRAMING_KEYS and "followup_asked" not in FRAMING_KEYS


def test_a_verdict_on_a_stamped_note_blanks_the_stamp(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "S.md", ['company: "Example Ltd"', "status: new", "score: 0",
                      "followup_asked: 2026-09-01", 'relevance_notes: ""'])
    note = v.read_leads()[0]
    assert apply_verdict(v, note, {"verdict": "research", "relevance_score": 60}, {}) == "applied"
    assert v.read_leads()[0].fm["followup_asked"] == ""


def test_a_verdict_on_an_unstamped_note_writes_no_followup_asked_key(tmp_path):
    v = Vault(str(tmp_path))
    note = _research(v, "U.md")
    apply_verdict(v, note, {"verdict": "research", "relevance_score": 60}, {})
    assert "followup_asked" not in open(note.ref, encoding="utf-8").read()


def test_a_stamp_landing_mid_judge_on_an_unstamped_note_survives(tmp_path):
    # Race (a): the realistic one. Witness: make the blank unconditional.
    v = Vault(str(tmp_path))
    note = _research(v, "R.md")                      # snapshot: no stamp
    v.update_fields(note.ref, {"followup_asked": "2026-10-12"})   # marked asked mid-judge
    assert apply_verdict(v, note, {"verdict": "research", "relevance_score": 60}, {}) == "applied"
    assert v.read_leads()[0].fm["followup_asked"] == "2026-10-12"


def test_a_restamp_landing_mid_judge_on_a_stale_stamp_refuses_the_verdict(tmp_path):
    # Race (b): the contrived one. Witness: delete `require_unchanged=`.
    v = Vault(str(tmp_path))
    _note(v, "R.md", ['company: "Example Ltd"', "status: new", "score: 0",
                      "followup_asked: 2026-09-01", 'relevance_notes: ""'])
    note = v.read_leads()[0]                          # snapshot: stale stamp
    v.update_fields(note.ref, {"status": "research", "followup_asked": "2026-10-12"})
    assert apply_verdict(v, note, {"verdict": "research", "relevance_score": 60}, {}) == "unchanged"
    assert v.read_leads()[0].fm["followup_asked"] == "2026-10-12"


def test_verdict_preserves_a_block_valued_followup_asked(tmp_path):
    # The OUTCOME, pinned: a hand-typed block list under followup_asked survives a verdict.
    # No witness can turn this red today, and the plan says so rather than claiming one: the
    # block value reads blank, so a verdict writes no followup_asked key at all, and a
    # snapshot that DID carry a stamp meets `require_unchanged` against the fresh blank and is
    # refused whole. `followup_asked` in `_HAND_EDITABLE_KEYS` is defence in depth behind those
    # two, and its comment must say exactly that.
    v = Vault(str(tmp_path))
    _note(v, "B.md", ['company: "Example Ltd"', "status: new", "score: 0",
                      "followup_asked:", "  - asked twice", 'relevance_notes: ""'])
    note = v.read_leads()[0]
    apply_verdict(v, note, {"verdict": "research", "relevance_score": 60}, {})
    assert "followup_asked:\n  - asked twice" in open(note.ref, encoding="utf-8").read()


def test_verdict_preserves_a_block_valued_triage_next(tmp_path):
    # Never-clobber outranks freshness: a hand-typed block list under triage_next survives a
    # verdict that carries an action. Witness: drop "triage_next" from _HAND_EDITABLE_KEYS.
    v = Vault(str(tmp_path))
    note = _research(v, "T.md", ["triage_next:", "  - item"])
    verdict = {"verdict": "research", "relevance_score": 63,
               "recommended_next_action": "ask: band"}
    apply_verdict(v, note, verdict, {})
    assert "triage_next:\n  - item" in open(note.ref, encoding="utf-8").read()
```

Append to `tests/conformance/test_store_contract.py`, beside the `require_blank` rows:

```python
def test_update_fields_require_unchanged_abstains_on_a_changed_value(store_name, tmp_path,
                                                                     monkeypatch):
    """#223/#378: `require_unchanged` is re-read from the FRESH record. Triage clears a stale
    asked stamp under it, so a store that dropped it would let a verdict erase a fresh one."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"followup_asked": "2031-03-14"})
    wrote = store.update_fields(ref, {"followup_asked": '""'},
                                require_unchanged={"followup_asked": "2031-03-01"})
    assert wrote is False
    assert store.read_leads()[0].fm["followup_asked"] == "2031-03-14"


def test_update_fields_require_unchanged_writes_when_the_value_still_matches(
        store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"followup_asked": "2031-03-01"})
    assert store.update_fields(ref, {"followup_asked": '""'},
                               require_unchanged={"followup_asked": "2031-03-01"}) is True
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_apply.py -q 2>&1 | tail -5`
Expected: FAIL on the `triage_next` rows (`KeyError: 'triage_next'`) and the stamped-note row. The block-valued `triage_next` row passes before the change (no write touches the key yet); it is witnessed after (see Step 5). The two conformance rows pass already against `Vault`; they pin the contract the Protocol is about to state.

- [ ] **Step 3: Implement in `sluice/triage/apply.py`**

Replace the `_HAND_EDITABLE_KEYS` block:

```python
# The frontmatter keys a person is invited to hand-edit: the CV composer's framing roster
# (#329, `core.leads.FRAMING_KEYS`, derived rather than re-typed) PLUS the triage-only keys
# #378 adds. A multi-line value typed into one is left alone by a triage write rather than
# corrupted by one; see `core/vault.py::_holds_multiline_value`. The #378 keys are added
# HERE and never to FRAMING_KEYS: that roster is what `cv/engine.py` feeds the composer, so a
# research question there would reach every CV prompt. A hand-typed block under `triage_next`
# therefore outlives a newer verdict's action: never-clobber outranks freshness. `followup_asked` is defence in depth:
# a block value there reads blank, so `apply_verdict` never writes the key over one today
# (see its stale-stamp rule), and this keeps a later writer from corrupting it either.
_HAND_EDITABLE_KEYS = frozenset(FRAMING_KEYS) | {"triage_next", "followup_asked"}
```

In `apply_verdict`, after the `for key, raw in (...)` loop and before `tag = ...`:

```python
    # #378: the verdict's `Next:` question as its own key, replaced on every verdict, so the
    # worklist reads it without parsing `relevance_notes`. Blank when the model gave none, AND
    # blank when the text is unsafe for frontmatter: unlike the list keys above, keeping the
    # earlier value would leave an instruction the latest verdict no longer gives. The one
    # exception is a hand-typed multi-line value, which `_HAND_EDITABLE_KEYS` leaves alone.
    next_action = (verdict["recommended_next_action"] or "").strip()
    safe_next = frontmatter_safe(next_action) if next_action else ""
    if next_action and not safe_next:
        _log.warning("triage: triage_next blanked for %s -- unsafe for frontmatter", note.slug)
        safe_next = ""
    fields["triage_next"] = f'"{safe_next}"'
    # #378: a stale "asked" stamp. An awaiting research lead is never judged, so a judged
    # lead carrying one is a lead a human moved off `research`; left in place, a later
    # `research` verdict would hide it again with nobody having asked. Two rules:
    #   - a BLANK snapshot writes no key, so a stamp added while this lead was being judged
    #     (the realistic race) is never touched;
    #   - a STAMPED snapshot blanks it under `require_unchanged`, so a re-stamp on a different
    #     date during the judge refuses this whole write (`unchanged`) instead of erasing it.
    #     Residual, stated: a re-stamp on the SAME date as the stale stamp passes the guard.
    stamp = note.fm.get("followup_asked", "") or ""
    require_unchanged = None
    if stamp.strip():
        fields["followup_asked"] = '""'
        require_unchanged = {"followup_asked": stamp}
```

and add `require_unchanged=require_unchanged,` to the `vault.update_fields(...)` call.

`sluice/core/protocols.py`, `Store.update_fields`: add `require_unchanged: dict | None = None,` to the signature after `blank_values`, and to the docstring after the `blank_values` paragraph:

```
        `require_unchanged` (#223, #378): a {key: expected} map re-read from the FRESH stored
        record; abstain -- nothing written, returns False -- unless every key still holds
        exactly `expected`, compared RAW (the value a caller read from `LeadNote.fm`). It fits
        a caller REPLACING a value, as `require_blank` fits one filling a hole. A value spread
        over several lines never compares equal (#329). Same delegation argument as above:
        triage clears a stale asked stamp under it, and a store that ignored it would let a
        verdict erase a stamp the user set while the lead was being judged.
```

`sluice/triage/engine.py`, the judge-pass comment beside `key = "skipped" if outcome in ("skipped", "unchanged") else clamp_verdict(`: replace

```
            # Symmetric with the classify-pass site above, including #118: `unchanged`
            # is grouped with `skipped` for counting/audit purposes only -- it is never
            # reported in report.failures, since it is reachable only via a benign
            # require_status no-op, never a real content collision.
```

with

```
            # Symmetric with the classify-pass site above, including #118: `unchanged`
            # is grouped with `skipped` for counting/audit purposes only -- it is never
            # reported in report.failures. Two causes reach it: a benign require_status
            # no-op, and (#378) apply_verdict's `require_unchanged` refusing because the
            # user re-marked the lead asked while it was being judged. The second IS a
            # content collision, and skipping is right for it too: the user's newer stamp
            # stands and the lead is judged on a later run.
```

Check the classify-pass site's own comment (`grep -n "benign require_status" sluice/triage/engine.py`) and reword it the same way only if `apply_verdict` is reachable from that pass.

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_apply.py tests/test_frontmatter_write_sweep.py tests/test_cv_triage_framing.py -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit, then witnesses**

```bash
git add sluice/triage/apply.py sluice/triage/engine.py sluice/core/protocols.py tests/test_apply.py tests/conformance/test_store_contract.py
git commit -F - <<'EOF'
feat(triage): record each verdict's next action and clear a stale asked stamp (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

Witnesses, each by Edit then restore by Edit, one at a time, running `tests/test_apply.py`:
(a) make the blank unconditional (`if True:` for `if stamp.strip():`) -> `test_a_stamp_landing_mid_judge_on_an_unstamped_note_survives` red;
(b) delete `require_unchanged=require_unchanged,` -> `test_a_restamp_landing_mid_judge_on_a_stale_stamp_refuses_the_verdict` red;
(c) drop `"triage_next"` from `_HAND_EDITABLE_KEYS` -> `test_verdict_preserves_a_block_valued_triage_next` red.
`followup_asked`'s membership has no reachable witness (see the row's comment); ledger that as stated, not as a gap.

---

### Task 3: Triage engine skips awaiting leads, shows the judge the answers, digest row

**Files:**
- Modify: `sluice/triage/engine.py` (`TriageReport.counts`, `run`)
- Modify: `sluice/triage/prompt.py` (`_SCAFFOLD_TAIL`)
- Modify: `sluice/cli.py` (`_TRIAGE_FILTERED_WORDS`)
- Test: create `tests/test_triage_followups.py`; `tests/test_triage_run_cli.py` (existing guard)

**Interfaces:**
- Consumes: `followups.awaiting_reply(fm)`, `followups.answers(body)` (Task 1).
- Produces: `TriageReport.counts["awaiting_reply"]`; dossier key `followup_answers`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_triage_followups.py`)

```python
"""#378 in the triage engine: an awaiting lead is skipped and counted; answers reach the judge
on that lead's own dossier copy, never the shared cache file."""
import json
import os
from datetime import datetime

from sluice.core import followups
from sluice.core.dossier import DossierCache
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
import sluice.triage.engine as eng
from tests.test_triage_engine import _Backend, _fields

TODAY = "2031-03-14"


def _cache(tmp_path):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7,
                        fetcher=lambda lead: {"jd": {"markdown": "an example posting"}},
                        clock=lambda: datetime(2031, 3, 14))


def _write(v, name, fm_lines, body="# Lead\n"):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def _run(tmp_path, titles, notes, *, statuses=("research",), limit=0, vault=None):
    accept, _ = titles
    v = vault or Vault(str(tmp_path / "vault"))
    for name, company, url, status, extra, body in notes:
        _write(v, name, _fields(company, accept[0].title(), status, url=url) + list(extra), body)
    cfg = TriageConfig(); cfg.accept_titles = list(accept)
    backend = _Backend()
    report = eng.run(v, cfg, backend, _cache(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
                     statuses=statuses, limit=limit)
    return report, v, "\n".join(backend.prompts)


def test_an_awaiting_lead_is_not_judged_and_is_counted(tmp_path, titles):
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research",
         [f"followup_asked: {TODAY}"], "# Lead\n"),
        ("b.md", "Example Beta", "https://example.invalid/b", "research", [], "# Lead\n")])
    assert report.counts["awaiting_reply"] == 1
    assert "Example Alpha" not in prompts and "Example Beta" in prompts


def test_limit_is_not_spent_on_an_awaiting_lead(tmp_path, titles):
    # tst-003: the filter runs BEFORE `--limit`. Witness: move it below the `if limit:` slice.
    # The awaiting lead is named first so it sorts first in the read (`a.md` < `b.md`).
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research",
         [f"followup_asked: {TODAY}"], "# Lead\n"),
        ("b.md", "Example Beta", "https://example.invalid/b", "research", [], "# Lead\n")],
        limit=1)
    assert "Example Beta" in prompts


def test_stamp_then_hand_move_to_new_then_research_again_is_judged_next_run(tmp_path, titles):
    # rev-005, the spec's sequence: a stamped lead moved to `new` by hand is judged (the stamp
    # does not hide it), the verdict clears the stale stamp, so when that verdict is `research`
    # the NEXT run judges it again rather than skipping it as awaiting.
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "new",
         ["followup_asked: 2031-02-01"], "# Lead\n")], statuses=("new",))
    assert "Example Alpha" in prompts
    note = v.read_leads()[0]
    assert note.fm["followup_asked"] == ""                   # the verdict cleared it
    v.update_fields(note.ref, {"status": "research"})       # stand-in for a research verdict
    report2, _, prompts2 = _run(tmp_path, titles, [], vault=v)
    assert report2.counts["awaiting_reply"] == 0 and "Example Alpha" in prompts2


def test_a_stale_stamp_on_a_new_lead_does_not_hide_it(tmp_path, titles):
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "new",
         ["followup_asked: 2026-09-01"], "# Lead\n")], statuses=("new",))
    assert report.counts["awaiting_reply"] == 0 and "Example Alpha" in prompts
    assert v.read_leads()[0].fm["followup_asked"] == ""        # cleared by the verdict


def test_answers_reach_the_judge_for_a_fetched_lead(tmp_path, titles):
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-1 client named", TODAY) + "\n"
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research", [], body)])
    assert "SENTINEL-ANSWER-1" in prompts and "followup_answers" in prompts


def test_answers_reach_the_judge_for_a_section_sourced_lead(tmp_path, titles):
    body = ("# Lead\n\n## Job description\nan example posting from the note\n\n"
            + followups.render_answer("SENTINEL-ANSWER-2", TODAY) + "\n")
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "", "research", [], body)])
    assert "SENTINEL-ANSWER-2" in prompts


def test_followup_answers_never_reach_the_dossier_cache_file(tmp_path, titles):
    # Witness: move the answers insertion ahead of get_or_build (into the cached dict).
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-3", TODAY) + "\n"
    _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research", [], body)])
    cached = [open(os.path.join(tmp_path / "dos", f), encoding="utf-8").read()
              for f in os.listdir(tmp_path / "dos")]
    assert cached and not any("SENTINEL-ANSWER-3" in c or "followup_answers" in c
                              for c in cached)


def test_two_leads_sharing_a_url_only_the_answered_one_carries_answers(tmp_path, titles):
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-4", TODAY) + "\n"
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/same", "research", [], body),
        ("b.md", "Example Beta", "https://example.invalid/same", "research", [], "# Lead\n")])
    dossiers = [json.loads(block) for block in
                (chunk.split("```json\n", 1)[1].split("\n```", 1)[0]
                 for chunk in prompts.split("## Dossier ")[1:])]
    by_company = {d["company"]: d for d in dossiers}
    assert "SENTINEL-ANSWER-4" in by_company["Example Alpha"].get("followup_answers", "")
    assert "followup_answers" not in by_company["Example Beta"]


def test_the_prompt_explains_followup_answers():
    from sluice.triage.prompt import SYSTEM_PROMPT
    assert "followup_answers" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_triage_followups.py -q 2>&1 | tail -5`
Expected: FAIL (`KeyError: 'awaiting_reply'`, sentinels absent, prompt lacks the field).

- [ ] **Step 3: Implement**

`sluice/triage/engine.py`: add `from sluice.core import followups` beside the other `sluice.core` imports. In `TriageReport.counts`' default add `"awaiting_reply": 0`. In `run`, replace

```python
    notes = vault.read_leads(set(statuses))
    if limit:
```

with

```python
    notes = vault.read_leads(set(statuses))
    # #378: a research lead the user has marked asked is waiting on a third party. Judging
    # it again would reach the same verdict for the same unanswered reason, so it is left
    # out BEFORE `--limit` (which would otherwise spend its budget on leads it then skips)
    # and counted, so the digest says how many were waiting rather than dropping them.
    awaiting = sum(1 for n in notes if followups.awaiting_reply(n.fm))
    if awaiting:
        report.counts["awaiting_reply"] += awaiting
        notes = [n for n in notes if not followups.awaiting_reply(n.fm)]
    if limit:
```

After the `d = {**d, "lead_id": ..., "role_type": ...}` block and before `dossiers.append(d)`:

```python
            # #378: the user's recorded replies to earlier research questions. Added HERE, to
            # this lead's own copy -- after `get_or_build` (so never into the shared cache
            # file, which serves later runs and other leads) and after the `{**d, ...}`
            # rebind above (so never onto a result another lead with the same cache key was
            # handed). Both the fetched and the section-sourced path pass through this line.
            answers = followups.answers(note.body)
            if answers:
                d["followup_answers"] = answers
```

`sluice/triage/prompt.py`, in `_SCAFFOLD_TAIL` after the paragraph ending `...unless the JD is rich enough to judge.`, add:

```
A dossier may also carry `followup_answers`: the candidate's own notes of replies to questions an earlier verdict asked them to put. Read them as facts about the role, alongside the JD. A question they answer is no longer open, so do not ask it again in `recommended_next_action`.
```

`sluice/cli.py`, `_TRIAGE_FILTERED_WORDS`: add after the `skipped` row:

```python
    # #378: THIS run's skips of leads marked asked. Worded apart from the standing
    # "Research queue" line, which counts the whole queue, so the two never read as one
    # number disagreeing with itself.
    ("awaiting_reply", "{n} skipped while you wait for a reply"),
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_triage_followups.py tests/test_triage_engine.py tests/test_triage_run_cli.py tests/test_prompt.py tests/test_triage_jd_section.py -q 2>&1 | tail -3`
Expected: PASS. If a test asserts an exact `counts` dict, add `"awaiting_reply": 0` to its literal and ledger a ruling naming the test.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/triage/engine.py sluice/triage/prompt.py sluice/cli.py tests/test_triage_followups.py
git commit -F - <<'EOF'
feat(triage): skip leads awaiting a reply and show the judge recorded answers (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

Witnesses, each a plausible regression rather than an arbitrary edit, restored by Edit:
(a) in `_prefetch_dossiers._one`, attach answers to the SHARED result before the fan-out
(`got = dossier_cache.get_or_build(note.fm); got["followup_answers"] = followups.answers(note.body); return got`)
-> `test_two_leads_sharing_a_url_only_the_answered_one_carries_answers` red (the leader's answers are deep-copied to its twin);
(b) in `_one`, pass the answers in through the lead dict (`dossier_cache.get_or_build({**note.fm, "followup_answers": followups.answers(note.body)})`)
-> `test_followup_answers_never_reach_the_dossier_cache_file` red, because `_assemble` caches the lead as `lead_snapshot`. If it stays green, read `_assemble`, ledger what it stores, and find the regression that does reach the file; a supplied-JD (section) dossier is never written to the cache, so that path cannot be the witness;
(c) delete the `awaiting_reply` filter -> `test_an_awaiting_lead_is_not_judged_and_is_counted` red;
(d) move the filter below the `if limit:` slice -> `test_limit_is_not_spent_on_an_awaiting_lead` red. If it stays green, `read_leads` did not return `a.md` first: print the order, ledger it, and name the awaiting lead so it sorts first.

---

### Task 4: Facade: `followups()`, `mark_asked()`, `record_answer()`

**Files:**
- Modify: `sluice/core/app.py` (new `AskResult`, `AnswerResult` beside `DismissResult`; methods after `dismiss_lead`)
- Modify: `sluice/core/protocols.py` (`Store.append_body_section` gains `require_status`)
- Modify: `sluice/core/vault.py` (`Vault.append_body_section` honours it)
- Test: create `tests/test_followups_facade.py`; `tests/conformance/test_store_contract.py`

**Interfaces:**
- Consumes: Task 1's `followups` API; `core.leads.index_by_slug`; `_reread_lead`.
- Produces (store seam): `Store.append_body_section(ref, tag, section_md, *, require_status: frozenset | None = None) -> bool` -- abstains (False, nothing written) unless the FRESH normalized status is in the set.
- Produces: `Sluice.followups() -> FollowupQueue`; `Sluice.mark_asked(*, lead: str) -> AskResult`; `Sluice.record_answer(*, lead: str, text: str) -> AnswerResult`. `AskResult(outcome, slug="", status="", candidates=[])`; `AnswerResult(outcome, slug="", status="", candidates=[], appended=False)`. `record_answer` outcomes: `answered`, `answered_not_cleared`, `refused_unwritable`, `answered_hidden`, `refused_status`, `not_found`, `ambiguous`, `conflict`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_followups_facade.py`)

```python
"""Sluice.followups / mark_asked / record_answer (#378): resolution, the outcome table,
CAS-fresh guards and idempotence."""
import pathlib

import pytest

from sluice.core import followups
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import VaultConflict
from datetime import date, timedelta

import sluice.core.vault as vault_mod
from sluice.core.vault import Vault

# A date the host clock cannot produce (tst-001): a facade method that reads the real clock
# instead of the injected `today` goes red on any day the suite runs.
TODAY = "2031-03-14"


def _ago(days):
    return (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def _role(titles):
    # Titles come from the seeded faker pool, never typed by hand (neu-002).
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, status="research", title=None,
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title or _TITLES[0], company="Example Ltd",
                  url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path, today=TODAY):
    return Sluice(Config(), store=Vault(str(tmp_path)), today=lambda: today)


def _text(tmp_path):
    return pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text(encoding="utf-8")


def _fm(tmp_path):
    return Vault(str(tmp_path)).read_leads()[0].fm


# ── mark_asked ──────────────────────────────────────────────────────────────────

def test_mark_asked_stamps_the_injected_date(tmp_path):
    slug = _seed(tmp_path)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "asked" and _fm(tmp_path)["followup_asked"] == TODAY


def test_mark_asked_same_day_is_unchanged(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path).mark_asked(lead=slug)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "unchanged"


def test_mark_asked_on_a_later_day_restamps(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path, _ago(11)).mark_asked(lead=slug)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "asked"
    assert _fm(tmp_path)["followup_asked"] == TODAY


@pytest.mark.parametrize("status", ["new", "shortlist", "applied"])
def test_mark_asked_refuses_a_non_research_lead_and_writes_nothing(tmp_path, status):
    slug = _seed(tmp_path, status=status)
    before = _text(tmp_path)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_status" and r.status == status
    assert _text(tmp_path) == before


def test_mark_asked_not_found_and_substring(tmp_path):
    slug = _seed(tmp_path)
    assert _app(tmp_path).mark_asked(lead="nothing").outcome == "not_found"
    assert _app(tmp_path).mark_asked(lead=slug.split(" ")[0]).outcome == "not_found"


def test_mark_asked_ambiguous_names_the_candidates(tmp_path):
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    for sub in ("Active", "Archive"):
        (leads / sub).mkdir(parents=True)
        (leads / sub / f"Example Ltd - {_TITLES[0]}.md").write_text(
            '---\ncompany: "Example Ltd"\nstatus: research\n---\n\nB\n')
    r = _app(tmp_path).mark_asked(lead=f"Example Ltd - {_TITLES[0]}")
    assert r.outcome == "ambiguous" and len(r.candidates) == 2


def test_mark_asked_refuses_on_a_status_that_changed_before_the_write(tmp_path, monkeypatch):
    # Witness: delete require_status= from mark_asked's write.
    slug = _seed(tmp_path)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_status" and r.status == "applied"
    assert "followup_asked" not in _text(tmp_path)


def test_mark_asked_refuses_a_block_value_and_keeps_it(tmp_path):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    text = pathlib.Path(ref).read_text(encoding="utf-8")
    pathlib.Path(ref).write_text(text.replace("status: research",
                                              "status: research\nfollowup_asked:\n  - by phone"))
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_unwritable"
    assert "  - by phone" in _text(tmp_path)


def test_mark_asked_refuses_when_the_store_will_not_write_one_line(tmp_path, monkeypatch):
    # rev-005: the second cause the elimination arm names -- the store's PyYAML check.
    slug = _seed(tmp_path)
    monkeypatch.setattr(vault_mod, "_single_line_write_breaks_note", lambda *a, **k: True)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "refused_unwritable"
    assert "followup_asked" not in _text(tmp_path)


def test_mark_asked_reports_a_conflict(tmp_path, monkeypatch):
    slug = _seed(tmp_path)
    monkeypatch.setattr(Vault, "update_fields",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "conflict"


def test_mark_asked_leaves_other_keys_and_the_body_intact(tmp_path):
    slug = _seed(tmp_path)
    before = _text(tmp_path)
    _app(tmp_path).mark_asked(lead=slug)
    after = _text(tmp_path)
    assert after.replace(f"followup_asked: {TODAY}\n", "") == before


# ── record_answer ───────────────────────────────────────────────────────────────

def test_record_answer_appends_and_clears(tmp_path):
    slug = _seed(tmp_path, followup_asked=TODAY)
    r = _app(tmp_path).record_answer(lead=slug, text="SENTINEL client answer")
    assert r.outcome == "answered" and r.appended
    note = Vault(str(tmp_path)).read_leads()[0]
    assert note.fm["followup_asked"] == ""
    assert followups.answers(note.body) == f"{TODAY}: SENTINEL client answer"


def test_record_answer_rerun_appends_nothing_and_succeeds(tmp_path):
    slug = _seed(tmp_path, followup_asked=TODAY)
    _app(tmp_path).record_answer(lead=slug, text="no")
    r = _app(tmp_path).record_answer(lead=slug, text="no")
    assert r.outcome == "answered" and not r.appended
    assert _text(tmp_path).count("## Follow-up answers") == 1


def test_the_same_reply_on_a_later_day_is_written_again(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path, _ago(11)).record_answer(lead=slug, text="no")
    _app(tmp_path).record_answer(lead=slug, text="no")
    assert _text(tmp_path).count("## Follow-up answers") == 2


def test_record_answer_on_a_never_asked_lead_succeeds(tmp_path):
    slug = _seed(tmp_path)
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered"


def test_record_answer_refuses_blank_text(tmp_path):
    slug = _seed(tmp_path)
    with pytest.raises(ValueError, match="text"):
        _app(tmp_path).record_answer(lead=slug, text="   ")


def test_record_answer_refuses_a_non_research_lead_and_writes_nothing(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    before = _text(tmp_path)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_status" and _text(tmp_path) == before


def test_status_leaving_research_between_append_and_clear(tmp_path, monkeypatch):
    # Witness: delete require_status= from step 2.
    slug = _seed(tmp_path, followup_asked=TODAY)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "answered_not_cleared" and r.status == "applied"
    fm = _fm(tmp_path)
    assert fm["status"] == "applied" and fm["followup_asked"] == TODAY
    assert "## Follow-up answers" in _text(tmp_path)


def test_status_leaving_research_before_the_append_writes_nothing(tmp_path, monkeypatch):
    # inv-003: step 1 re-reads the status inside the store's own CAS write, so the one write
    # in this feature that could reach an application-owned note abstains instead.
    # Witness: delete `require_status=` from record_answer's append_body_section call.
    slug = _seed(tmp_path, followup_asked=TODAY)
    real = Vault.append_body_section

    def racing(self, ref, tag, section_md, **kw):
        Vault.update_fields(self, ref, {"status": "applied"})
        return real(self, ref, tag, section_md, **kw)

    monkeypatch.setattr(Vault, "append_body_section", racing)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_status" and r.status == "applied" and not r.appended
    assert "## Follow-up answers" not in _text(tmp_path)


def test_a_stamp_the_store_will_not_clear_is_not_reported_answered(tmp_path, monkeypatch):
    # inv-001: the store's PyYAML check can leave a one-line stamp in place. The lead would
    # then stay hidden from triage, so `answered` (exit 0) would be a failed clear reported
    # as success. Witness: delete the `awaiting_reply(fresh.fm)` re-check.
    slug = _seed(tmp_path, followup_asked=TODAY)
    monkeypatch.setattr(vault_mod, "_single_line_write_breaks_note", lambda *a, **k: True)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_unwritable" and r.appended
    assert _fm(tmp_path)["followup_asked"] == TODAY


def test_an_answer_hidden_by_an_earlier_open_fence_is_reported(tmp_path):
    # inv-002, the hand-typed half: sluice closes a fence it writes, but not one a person left
    # open earlier in the note, which would hide every later answer from the judge.
    # Witness: delete the read-back check.
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    pathlib.Path(ref).write_text(pathlib.Path(ref).read_text(encoding="utf-8")
                                 + "\n```\nmy own unclosed snippet\n")
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "answered_hidden" and r.appended


def test_a_never_asked_lead_leaving_research_is_not_reported_answered(tmp_path, monkeypatch):
    # Witness: check the blank stamp BEFORE the fresh status.
    slug = _seed(tmp_path)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered_not_cleared"


def test_record_answer_conflict_at_step_one_writes_nothing(tmp_path, monkeypatch):
    slug = _seed(tmp_path)
    before = _text(tmp_path)
    monkeypatch.setattr(Vault, "append_body_section",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "conflict" and _text(tmp_path) == before


def test_record_answer_conflict_at_step_two_keeps_the_answer(tmp_path, monkeypatch):
    slug = _seed(tmp_path, followup_asked=TODAY)
    monkeypatch.setattr(Vault, "update_fields",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "conflict" and r.appended
    assert "## Follow-up answers" in _text(tmp_path)


def test_record_answer_over_a_block_value_reports_answered_and_keeps_it(tmp_path):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    text = pathlib.Path(ref).read_text(encoding="utf-8")
    pathlib.Path(ref).write_text(text.replace("status: research",
                                              "status: research\nfollowup_asked:\n  - by phone"))
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered"
    assert "followup_asked:\n  - by phone" in _text(tmp_path)


def test_record_answer_demotes_a_pasted_heading(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path).record_answer(lead=slug, text="a\n## b\nc")
    body = Vault(str(tmp_path)).read_leads()[0].body
    assert followups.answers(body) == f"{TODAY}: a\n### b\nc"


# ── followups ───────────────────────────────────────────────────────────────────

def test_followups_reads_research_leads_with_the_injected_date(tmp_path):
    # Witness: `_today()` for `(self._today or _today)()` in followups() -> red.
    _seed(tmp_path, url="https://example.invalid/1", title=_TITLES[0], first_seen=_ago(10))
    _seed(tmp_path, url="https://example.invalid/2", title=_TITLES[1], first_seen=_ago(10),
          followup_asked=_ago(7))
    _seed(tmp_path, url="https://example.invalid/3", title=_TITLES[2], status="shortlist",
          followup_asked=_ago(7))
    q = _app(tmp_path).followups()
    assert len(q.rows) == 2 and len(q.asked) == 1
    assert q.asked[0].waited_days == 7 and q.oldest_days == 10
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_followups_facade.py -q 2>&1 | tail -3`
Expected: FAIL with `AttributeError: 'Sluice' object has no attribute 'mark_asked'`.

- [ ] **Step 3: Implement in `sluice/core/app.py`**

After `DismissResult`:

```python
@dataclass
class AskResult:
    """#378: outcome is one of asked | unchanged | refused_status | refused_unwritable |
    not_found | ambiguous | conflict. `status` is the FRESH status behind a refusal."""
    outcome: str
    slug: str = ""
    status: str = ""
    candidates: list = field(default_factory=list)


@dataclass
class AnswerResult:
    """#378: outcome is one of answered | answered_not_cleared | refused_unwritable |
    answered_hidden | refused_status | not_found | ambiguous | conflict. Only `answered` means
    the answer reached the note, will be read, and the lead is back in triage. `appended` is
    False when this exact answer (same date, same text) was already in the note, or when
    nothing was written."""
    outcome: str
    slug: str = ""
    status: str = ""
    candidates: list = field(default_factory=list)
    appended: bool = False
```

After `dismiss_lead`:

```python
    def _resolve_exact(self, lead: str):
        """`(store, note, candidates)` for `lead` by EXACT store-issued slug over every
        status, the way `dismiss_lead` resolves (never a substring: the caller may be an
        LLM). `candidates` is non-empty when the slug names two or more notes; `note` is
        None when it names none. Every status, so a lead at `applied` is refused by name
        rather than reported missing."""
        from sluice.core.leads import index_by_slug
        store = self.store()
        index, dropped = index_by_slug(store.read_leads())
        if lead in dropped:
            return store, None, sorted(n.slug for n in dropped[lead])
        return store, index.get(lead), []

    def followups(self):
        """The research queue (#378): the ONE reader behind `leads followups` and the triage
        digest's standing line, so the two cannot count differently."""
        from sluice.core import followups
        return followups.build_queue(self.store().read_leads(frozenset({"research"})),
                                     (self._today or _today)())

    def mark_asked(self, *, lead: str) -> AskResult:
        """Stamp `followup_asked` with today's date on a `research` lead (#378). Writes on
        every call, like `dismiss_lead`: what it records is the user's own action. A `False`
        from the write is classified from a FRESH read, status first."""
        from sluice.core.protocols import VaultConflict
        store, note, candidates = self._resolve_exact(lead)
        if candidates:
            return AskResult(outcome="ambiguous", candidates=candidates)
        if note is None:
            return AskResult(outcome="not_found")
        if _status.normalize(note.status) != "research":
            return AskResult(outcome="refused_status", slug=note.slug, status=note.status)
        today = (self._today or _today)()
        try:
            wrote = store.update_fields(note.ref, {"followup_asked": today},
                                        require_status=frozenset({"research"}),
                                        preserve_block_values=frozenset({"followup_asked"}))
        except VaultConflict as e:
            _log.warning("mark_asked: %s lost the write race: %s", note.ref, e)
            return AskResult(outcome="conflict", slug=note.slug)
        if wrote:
            return AskResult(outcome="asked", slug=note.slug, status="research")
        fresh = _reread_lead(store, note)
        if fresh is None:
            return AskResult(outcome="not_found", slug=note.slug)
        if _status.normalize(fresh.status) != "research":
            return AskResult(outcome="refused_status", slug=note.slug, status=fresh.status)
        if (fresh.fm.get("followup_asked", "") or "").strip() == today:
            return AskResult(outcome="unchanged", slug=note.slug, status="research")
        # Still at research and still not today's date: by ELIMINATION the store's
        # `preserve_block_values` abstained -- a hand-typed block value, or a note PyYAML says
        # a single-line write would break. Named as both rather than guessed as one, and
        # never read off vault internals from here.
        return AskResult(outcome="refused_unwritable", slug=note.slug, status="research")

    def record_answer(self, *, lead: str, text: str) -> AnswerResult:
        """Record the reply to a research lead's question and return it to triage (#378).
        Two writes in order, each re-reading the status inside the store's own CAS write: the
        answer section (idempotent by its date+text tag), then the stamp cleared. Every partial
        state is safe and a re-run completes it. One fresh read afterwards classifies the
        result, and only `answered` claims all three things the user is relying on: the answer
        is in the note, the judge will read it, and the lead is back in triage."""
        from sluice.core import followups
        from sluice.core.protocols import VaultConflict
        if not text or not text.strip():
            raise ValueError("text must not be blank")
        text = text.strip()
        store, note, candidates = self._resolve_exact(lead)
        if candidates:
            return AnswerResult(outcome="ambiguous", candidates=candidates)
        if note is None:
            return AnswerResult(outcome="not_found")
        if _status.normalize(note.status) != "research":
            return AnswerResult(outcome="refused_status", slug=note.slug, status=note.status)
        today = (self._today or _today)()
        research = frozenset({"research"})
        try:
            appended = store.append_body_section(note.ref, followups.answer_tag(text, today),
                                                 followups.render_answer(text, today),
                                                 require_status=research)
        except VaultConflict as e:
            _log.warning("record_answer: %s lost the write race: %s", note.ref, e)
            return AnswerResult(outcome="conflict", slug=note.slug)
        if not appended:
            # False is either "this exact answer is already there" (a re-run) or "the status
            # left research before the write" -- told apart by a fresh read, status FIRST.
            fresh = _reread_lead(store, note)
            if fresh is None:
                return AnswerResult(outcome="not_found", slug=note.slug)
            if _status.normalize(fresh.status) != "research":
                return AnswerResult(outcome="refused_status", slug=note.slug,
                                    status=fresh.status)
        try:
            store.update_fields(note.ref, {"followup_asked": '""'}, require_status=research,
                                preserve_block_values=frozenset({"followup_asked"}))
        except VaultConflict as e:
            _log.warning("record_answer: %s lost the write race clearing the stamp: %s",
                         note.ref, e)
            return AnswerResult(outcome="conflict", slug=note.slug, appended=appended)
        fresh = _reread_lead(store, note)
        if fresh is None:
            return AnswerResult(outcome="not_found", slug=note.slug, appended=appended)
        # Status FIRST: a never-asked lead's blank stamp must not report `answered` for a lead
        # that has just moved to `applied`.
        if _status.normalize(fresh.status) != "research":
            return AnswerResult(outcome="answered_not_cleared", slug=note.slug,
                                status=fresh.status, appended=appended)
        # Still at research with a stamp the store would not clear: by elimination its
        # single-line-write check abstained (a block value reads blank, so it never gets here).
        # The lead would stay hidden from triage, so this is not `answered`.
        if followups.awaiting_reply(fresh.fm):
            return AnswerResult(outcome="refused_unwritable", slug=note.slug,
                                status="research", appended=appended)
        # Read back through the reader the judge uses. Sluice closes any fence it writes, but a
        # fence a person left open EARLIER in the note runs to the end of the body and hides
        # every later answer; the user is told, since the judge would never see the reply.
        if f"{today}: {jdsection.neutralise(text)}" not in followups.answers(fresh.body):
            return AnswerResult(outcome="answered_hidden", slug=note.slug, status="research",
                                appended=appended)
        return AnswerResult(outcome="answered", slug=note.slug, status="research",
                            appended=appended)
```

In `record_answer` add `from sluice.core import jdsection` beside the `followups` import. Confirm `_status` and `field` are already imported in `core/app.py` (`grep -n "^from sluice.core import status\|^from dataclasses" sluice/core/app.py`); add whichever is missing.

`sluice/core/protocols.py`, `Store.append_body_section`: widen the signature to `(self, ref, tag: str, section_md: str, *, require_status: frozenset | None = None) -> bool` and append to its docstring:

```
        `require_status` (#378) carries `update_fields`' identical obligation: re-read the
        status from the FRESH stored note, NORMALIZED, and abstain -- nothing written, returns
        False -- unless it is in the set. A caller appending to a lead it read at one status
        cannot see a concurrent entry into the application lifecycle; only the store can.
```

`sluice/core/vault.py`, `Vault.append_body_section`: same signature; in `transform`, before the tag check:

```python
            if require_status is not None:
                inner, _ = _split_frontmatter(text)
                # Same fresh re-read as update_fields' guard, same reason (#378): the caller's
                # snapshot predates the window this exists to cover.
                if _status.normalize(_fm_value(inner or "", "status")) not in require_status:
                    return text
```

and add its sentence to the docstring.

`tests/conformance/test_store_contract.py`, beside the `require_blank` rows:

```python
def test_append_body_section_require_status_abstains_on_a_fresh_status(store_name, tmp_path,
                                                                        monkeypatch):
    """#378: the status is re-read inside the store's own write. A caller-side check reads a
    snapshot, which cannot see a lead entering the application lifecycle."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"status": "applied"})
    wrote = store.append_body_section(ref, "<!-- t -->", "## S\n<!-- t -->\nx",
                                      require_status=frozenset({"research"}))
    assert wrote is False and "<!-- t -->" not in store.read_leads()[0].body


def test_append_body_section_require_status_writes_on_a_matching_status(store_name, tmp_path,
                                                                        monkeypatch):
    """The other half, normalized: a drifted `Research` still matches."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"status": "Research"})
    assert store.append_body_section(ref, "<!-- t -->", "## S\n<!-- t -->\nx",
                                     require_status=frozenset({"research"})) is True
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_followups_facade.py tests/test_leads_dismiss.py tests/conformance/test_store_contract.py -q 2>&1 | tail -3`
Expected: PASS. If `test_mark_asked_leaves_other_keys_and_the_body_intact` fails because the vault writes the key in a different place, assert instead that every line of `before` survives in order in `after` and that exactly one new line, `followup_asked: <TODAY>`, was added; ledger it.

- [ ] **Step 5: Commit, then witnesses**

```bash
git add sluice/core/app.py sluice/core/protocols.py sluice/core/vault.py tests/test_followups_facade.py tests/conformance/test_store_contract.py
git commit -F - <<'EOF'
feat(core): mark a research lead asked and record its answer (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

Witnesses: (a) delete `require_status=` from `mark_asked`'s write -> its race row red; (b) delete it from `record_answer`'s step 2 -> `test_status_leaving_research_between_append_and_clear` red; (c) move the status check below a `if not (fresh.fm...followup_asked...).strip(): return answered` -> `test_a_never_asked_lead_leaving_research_is_not_reported_answered` red; (d) `_today()` for `(self._today or _today)()` in `followups()` -> `test_followups_reads_research_leads_with_the_injected_date` red; (e) delete `require_status=research` from the `append_body_section` call -> `test_status_leaving_research_before_the_append_writes_nothing` red; (f) delete the transform's `require_status` block in `Vault.append_body_section` -> the conformance abstain row red; (g) delete the `awaiting_reply(fresh.fm)` re-check -> `test_a_stamp_the_store_will_not_clear_is_not_reported_answered` red; (h) delete the read-back check -> `test_an_answer_hidden_by_an_earlier_open_fence_is_reported` red. Restore each by Edit.

---

### Task 5: CLI: `leads followups`, `leads mark-asked`, `leads answer`, digest standing line

**Files:**
- Modify: `sluice/cli.py` (three handlers near `cmd_leads_dismiss`; three parsers after `dismiss`; `_research_queue_line`; `cmd_triage_run`)
- Modify: `docs/USAGE.md`, `README.md` (Commands table) -- in THIS commit, because `tests/test_docs_claims.py` ties the parser tree and the docs in both directions, so a command without its docs (or the reverse) is red (rev-002)
- Test: create `tests/test_leads_followups_cli.py`; append to `tests/test_triage_run_cli.py`

**Interfaces:**
- Consumes: Task 4's facade and result types; Task 1's `FollowupQueue`.
- Produces: the three commands; `cli._research_queue_line(queue) -> str`.

- [ ] **Step 1: Write the failing CLI tests** (create `tests/test_leads_followups_cli.py`)

```python
"""`leads followups`, `leads mark-asked`, `leads answer` at the CLI layer (#378)."""
import json
import pathlib

import pytest

from sluice.cli import main
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault


@pytest.fixture(autouse=True)
def _role(titles):
    # Titles come from the seeded faker pool, never typed by hand (neu-002).
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, status="research", title=None,
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title or _TITLES[0], company="Example Ltd",
                  url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _main(tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return main(["leads", *argv])


def test_mark_asked_then_followups_groups_it(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, triage_next='"ask recruiter: client name"')
    _seed(tmp_path, url="https://example.invalid/2", title=_TITLES[1])
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 0
    assert slug in capsys.readouterr().err
    assert _main(tmp_path, monkeypatch, "followups") == 0
    out = capsys.readouterr().out
    assert "Never asked (1):" in out and "Asked, waiting (1):" in out
    assert "ask recruiter: client name" in out


def test_a_slug_with_spaces_and_punctuation_resolves_exactly(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, title=f"{_TITLES[0]}, ({_TITLES[1]})")
    assert " " in slug
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 0
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug[:-3]) == 1


def test_followups_json_shape(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, first_seen="2026-10-01")
    assert _main(tmp_path, monkeypatch, "followups", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"total", "asked", "oldest_days", "rows"}
    assert data["rows"][0]["slug"] == slug
    assert set(data["rows"][0]) == {"slug", "company", "role", "age_days", "next_action",
                                    "asked", "waited_days"}


def test_followups_on_an_empty_queue_says_so(tmp_path, monkeypatch, capsys):
    assert _main(tmp_path, monkeypatch, "followups") == 0
    out = capsys.readouterr().out
    assert "No research leads" in out and "Never asked" not in out


def test_followups_hints_at_expiry_only_when_ttl_is_off(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    _main(tmp_path, monkeypatch, "followups")
    assert "lead_ttl_days" in capsys.readouterr().out
    # The converse arm (tst-004). Witness: delete `if not config.lead_ttl_days`.
    from sluice.cli import cmd_leads_followups, _build_parser
    cfg = Config(); cfg.lead_ttl_days = 30
    cmd_leads_followups(_build_parser().parse_args(["leads", "followups"]), cfg)
    assert "lead_ttl_days" not in capsys.readouterr().out


def test_a_hand_typed_asked_mark_prints_a_question_mark(tmp_path, monkeypatch, capsys):
    # tst-005, Review Focus 1 at the CLI: a non-date stamp still counts as asked, and its
    # wait prints `?` -- never `None`. Witness: format `r.waited_days` directly.
    slug = _seed(tmp_path, followup_asked="yes")
    _main(tmp_path, monkeypatch, "followups")
    assert "asked ?d ago" in capsys.readouterr().out


def test_an_unparseable_age_prints_a_question_mark(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, first_seen="sometime")
    _main(tmp_path, monkeypatch, "followups")
    assert f"- {slug} (?d)" in capsys.readouterr().out


def test_answer_clears_and_exits_zero(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug)
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "client named") == 0
    assert Vault(str(tmp_path)).read_leads()[0].fm["followup_asked"] == ""


def test_answer_blank_text_is_a_usage_error(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "  ") == 2


def test_refusals_exit_one_and_name_the_status(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, status="shortlist")
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 1
    assert "status=shortlist" in capsys.readouterr().err
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "x") == 1
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", "nothing") == 1


def test_ambiguous_exits_one(tmp_path, monkeypatch, capsys):
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    for sub in ("Active", "Archive"):
        (leads / sub).mkdir(parents=True)
        (leads / sub / f"Example Ltd - {_TITLES[0]}.md").write_text(
            '---\ncompany: "Example Ltd"\nstatus: research\n---\n\nB\n')
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead",
                 f"Example Ltd - {_TITLES[0]}") == 1
    assert "ambiguous" in capsys.readouterr().err


def test_an_answer_the_judge_cannot_see_exits_one_and_says_why(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    pathlib.Path(ref).write_text(pathlib.Path(ref).read_text(encoding="utf-8")
                                 + "\n```\nunclosed\n")
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "x") == 1
    assert "code fence" in capsys.readouterr().err
```

Append to `tests/test_triage_run_cli.py`:

```python
def _queue_push(monkeypatch, tmp_path, titles, *argv):
    from sluice.core.leads import Lead
    from sluice.core.vault import Vault
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: TriageReport())
    v = Vault(str(tmp_path))
    for i, asked in enumerate(("", "2026-10-05", "")):
        v.upsert(Lead(source="s", search="q", title=titles[0][i].title(), company="Example Ltd",
                      url=f"https://example.invalid/{i}"))
        note = next(n for n in v.read_leads() if n.fm["url"].endswith(f"/{i}"))
        v.update_fields(note.ref, {"status": "research", "followup_asked": asked})
    assert cmd_triage_run(_build_parser().parse_args(["triage", "run", *argv]), Config()) == 0
    return sent[0]


def test_the_digest_reports_the_standing_research_queue(monkeypatch, tmp_path, titles):
    assert "Research queue: 3 leads, 1 marked asked" in _queue_push(
        monkeypatch, tmp_path, titles, "--no-llm")


def test_the_queue_line_prints_on_a_status_new_run_and_a_dry_run(monkeypatch, tmp_path, titles):
    assert "Research queue:" in _queue_push(monkeypatch, tmp_path, titles,
                                            "--status", "new", "--dry-run")


def test_no_queue_line_when_there_are_no_research_leads(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: TriageReport())
    cmd_triage_run(_build_parser().parse_args(["triage", "run", "--no-llm"]), Config())
    assert "Research queue" not in sent[0]


def test_a_queue_read_failure_omits_the_line_and_keeps_the_exit_code(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: TriageReport())
    monkeypatch.setattr(Sluice, "followups",
                        lambda self: (_ for _ in ()).throw(OSError("unreadable")))
    assert cmd_triage_run(_build_parser().parse_args(["triage", "run", "--no-llm"]),
                          Config()) == 0
    assert "Research queue" not in sent[0]


def test_the_queue_line_is_singular_for_one_lead():
    from sluice.core.followups import FollowupQueue, FollowupRow
    row = FollowupRow(slug="a", company="", role="", age_days=1, next_action="", asked="",
                      waited_days=None)
    assert cli._research_queue_line(FollowupQueue(rows=(row,))) == (
        "Research queue: 1 lead, 0 marked asked; oldest 1 day.")
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_leads_followups_cli.py tests/test_triage_run_cli.py -q 2>&1 | tail -3`
Expected: FAIL (`invalid choice: 'mark-asked'`, no `_research_queue_line`).

- [ ] **Step 3: Implement in `sluice/cli.py`**

After `cmd_leads_dismiss`:

```python
def _days(n) -> str:
    return "?" if n is None else str(n)


def cmd_leads_followups(args, config) -> int:
    """#378: the research worklist. Read-only, so no --dry-run and no flag to act."""
    import json as _json
    from sluice.core.app import Sluice
    queue = Sluice(config).followups()
    if args.json:
        print(_json.dumps({
            "total": len(queue.rows), "asked": len(queue.asked),
            "oldest_days": queue.oldest_days,
            "rows": [{"slug": r.slug, "company": r.company, "role": r.role,
                      "age_days": r.age_days, "next_action": r.next_action,
                      "asked": r.asked, "waited_days": r.waited_days}
                     for r in queue.rows]}))
        return 0
    if not queue.rows:
        print("No research leads are waiting on a question.")
        return 0
    # The slug alone, not company and role beside it: it is "Company - Role" already, and it
    # is what `mark-asked`/`answer --lead` take next. `--json` carries both fields.
    if queue.never_asked:
        print(f"Never asked ({len(queue.never_asked)}):")
        for r in queue.never_asked:
            print(f"- {r.slug} ({_days(r.age_days)}d): "
                  f"{r.next_action or '(no Next: recorded yet)'}")
    if queue.asked:
        print(f"Asked, waiting ({len(queue.asked)}):")
        for r in queue.asked:
            print(f"- {r.slug} ({_days(r.age_days)}d, asked {_days(r.waited_days)}d ago): "
                  f"{r.next_action or '(no Next: recorded yet)'}")
    tail = (" Set lead_ttl_days and run `job-sluice leads expire` to age out leads left "
            "waiting too long." if not config.lead_ttl_days else "")
    print(f"{len(queue.rows)} research lead{'' if len(queue.rows) == 1 else 's'}.{tail}")
    return 0


def _lead_refusal(cmd: str, result, lead: str) -> int | None:
    """The outcomes `mark-asked` and `answer` share with `leads dismiss`, printed the same
    way. None when `result` is not one of them."""
    if result.outcome == "not_found":
        print(f"leads {cmd}: no lead matching '{lead}'", file=sys.stderr)
        return 1
    if result.outcome == "ambiguous":
        print(f"leads {cmd}: ambiguous: {len(result.candidates)} notes claim the slug "
              f"'{lead}' ({' | '.join(result.candidates)}) -- rename or merge them "
              f"first (job-sluice leads dedupe)", file=sys.stderr)
        return 1
    if result.outcome == "refused_status":
        print(f"leads {cmd}: {result.slug}: refused (status={result.status}) -- only a "
              f"lead at research is waiting on a question", file=sys.stderr)
        return 1
    if result.outcome == "conflict":
        print(f"leads {cmd}: {result.slug}: conflict -- the note changed while this ran; "
              f"run the same command again", file=sys.stderr)
        return 1
    return None


def cmd_leads_mark_asked(args, config) -> int:
    from sluice.core.app import Sluice
    result = Sluice(config).mark_asked(lead=args.lead)
    refused = _lead_refusal("mark-asked", result, args.lead)
    if refused is not None:
        return refused
    if result.outcome == "refused_unwritable":
        print(f"leads mark-asked: {result.slug}: refused -- followup_asked holds a value "
              f"written over several lines, or one a single-line write would break; edit "
              f"it in the note by hand", file=sys.stderr)
        return 1
    print(f"leads mark-asked: {result.slug}: {result.outcome}", file=sys.stderr)
    return 0


def cmd_leads_answer(args, config) -> int:
    from sluice.core.app import Sluice
    if not args.text.strip():
        print("leads answer: --text must not be blank", file=sys.stderr)
        return 2
    result = Sluice(config).record_answer(lead=args.lead, text=args.text)
    refused = _lead_refusal("answer", result, args.lead)
    if refused is not None:
        if result.outcome == "conflict" and result.appended:
            print(f"leads answer: {result.slug}: the answer WAS recorded; only clearing "
                  f"the asked mark failed", file=sys.stderr)
        return refused
    if result.outcome == "answered_not_cleared":
        print(f"leads answer: {result.slug}: the answer was recorded, but the lead moved to "
              f"status={result.status} before the asked mark could be cleared",
              file=sys.stderr)
        return 1
    if result.outcome == "refused_unwritable":
        print(f"leads answer: {result.slug}: the answer was recorded, but followup_asked "
              f"could not be cleared safely, so triage still skips the lead; clear it in the "
              f"note by hand", file=sys.stderr)
        return 1
    if result.outcome == "answered_hidden":
        print(f"leads answer: {result.slug}: the answer was recorded, but a code fence left "
              f"open earlier in the note hides it from triage; close that fence (a line of "
              f"three backticks) in the note", file=sys.stderr)
        return 1
    print(f"leads answer: {result.slug}: answered"
          f"{'' if result.appended else ' (already recorded)'}", file=sys.stderr)
    return 0
```

After the `ds.set_defaults(func=cmd_leads_dismiss)` line:

```python
    fu = leads.add_parser("followups",
                          help="list research leads and the question each is waiting on")
    fu.add_argument("--json", action="store_true", help="machine-readable output")
    fu.set_defaults(func=cmd_leads_followups)

    ma = leads.add_parser("mark-asked",
                          help="record that you asked a research lead's question; triage "
                               "skips it until you record the answer")
    ma.add_argument("--lead", required=True, metavar="SLUG",
                    help='exact store-issued slug, e.g. --lead "Example Ltd - Example Role"')
    ma.set_defaults(func=cmd_leads_mark_asked)

    an = leads.add_parser("answer",
                          help="record the reply to a research lead's question; the next "
                               "triage run judges the lead with it")
    an.add_argument("--lead", required=True, metavar="SLUG",
                    help='exact store-issued slug, e.g. --lead "Example Ltd - Example Role"')
    an.add_argument("--text", required=True, help="the reply, in your own words")
    an.set_defaults(func=cmd_leads_answer)
```

Before `cmd_triage_run`:

```python
def _research_queue_line(queue) -> str:
    """The digest's standing line (#378): the whole research queue, read from the vault
    rather than the run, so the per-run name cap cannot hide it. "" when the queue is
    empty."""
    n = len(queue.rows)
    if not n:
        return ""
    oldest = queue.oldest_days
    tail = (f"; oldest {oldest} day{'' if oldest == 1 else 's'}"
            if oldest is not None else "")
    return (f"Research queue: {n} lead{'' if n == 1 else 's'}, "
            f"{len(queue.asked)} marked asked{tail}.")


def _research_queue_line_for(app) -> str:
    try:
        return _research_queue_line(app.followups())
    except Exception as e:  # noqa: BLE001 -- the run has already written; an unreadable
        # queue costs this one digest line, never the run's exit code or its push.
        _log.warning("triage: could not read the research queue: %s", e)
        return ""
```

In `cmd_triage_run`: change `report = Sluice(config).triage(` to `app = Sluice(config)` then `report = app.triage(`, and right after `body = _format_triage_digest(report, alert, dry_run=args.dry_run)`:

```python
    queue_line = _research_queue_line_for(app)
    if queue_line:
        body += "\n" + queue_line
```

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_leads_followups_cli.py tests/test_triage_run_cli.py tests/test_leads_dismiss_cli.py -q 2>&1 | tail -3`
Expected: PASS for these files; `tests/test_docs_claims.py` FAILS naming the three new commands as undocumented. Do Step 5 next.

- [ ] **Step 5: USAGE.md and the README Commands table**

USAGE.md -- under the `leads` section, after `leads dismiss`, add three subsections in the file's existing style:
  - `leads followups [--json]`: lists every `research` lead in "Never asked" and "Asked, waiting", each with its age and the question its verdict asked (`triage_next`); read-only.
  - `leads mark-asked --lead SLUG`: stamps `followup_asked`; `triage run` skips the lead until an answer is recorded; refuses a lead not at `research`.
  - `leads answer --lead SLUG --text TEXT`: appends a dated `## Follow-up answers` section and clears the mark; the next triage run shows the judge the answers; the same text on the same day is recorded once. Exits non-zero, saying why, when the answer was written but the lead would still be skipped or the judge could not read it.
Add one sentence to the `triage run` section: the digest ends with a `Research queue:` line counting every research lead and how many are marked asked.

README.md -- in the Commands table's `leads` row, add `followups`, `mark-asked`, `answer` to the subcommand cell.

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: PASS -- the whole suite, since this commit must stand alone.

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py tests/test_leads_followups_cli.py tests/test_triage_run_cli.py docs/USAGE.md README.md
git commit -F - <<'EOF'
feat(leads): list research follow-ups, mark them asked and record answers (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 6: MCP `get_lead` names the answers section as the user's own notes

**Files:**
- Modify: `sluice/mcpserver.py` (`_GET_LEAD_CONTENT_WARNING`)
- Test: `tests/test_mcpserver.py` (the new row, and `_ISOLATION_ALLOWED_MODULES`)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_get_lead_warning_names_the_follow_up_answers_section(tmp_path):
    # Asserted on the literal heading, never against the module constant, so deleting the
    # sentence turns this red.
    slug = _seed(tmp_path, status="research")
    out = get_lead(_app(tmp_path), slug)
    assert "## Follow-up answers" in out["content_warning"]
    assert "own notes" in out["content_warning"]
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/test_mcpserver.py -k follow_up -q 2>&1 | tail -3`
Expected: FAIL.

- [ ] **Step 3: Implement** -- add `from sluice.core.followups import ANSWERS_HEADING` beside `mcpserver.py`'s other module-scope `sluice.core` imports. `tests/test_mcpserver.py`'s isolation sweep accepts only modules in `_ISOLATION_ALLOWED_MODULES`, so add there, in the file's style:

```python
    # The follow-up answers grammar (#378): get_lead's warning names its heading. Pure
    # section parsing and a queue builder; no write path.
    "sluice.core.followups",
```

In `_GET_LEAD_CONTENT_WARNING`, change the opening `f"Everything in fm and body, except the keys named next, "` to `f"Everything in fm and body, except what is named next, "` (the new carve-out is a body section, not a key), and insert after the whole `blind` sentence -- the line ending `composed without a job description. "` -- and before `f"Every other \`needs_signoff\` entry ..."`:

```python
    f"A `{ANSWERS_HEADING}` section in body is the user's own notes of replies to "
    f"questions sluice asked them to put, not scraped text. "
```

If a test pins the old opening phrase (`grep -rn "except the keys named next" tests`), update it in this commit and ledger the ruling.

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_mcpserver.py tests/functional/test_mcp_contract.py -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/mcpserver.py tests/test_mcpserver.py
git commit -F - <<'EOF'
fix(mcp): label the follow-up answers section as the user's own notes (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 7: Docs and the operating manual

**Files:**
- Modify: `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`, `.rulesync/rules/CLAUDE.md` (USAGE.md and README landed with the commands in Task 5)

- [ ] **Step 1: CONFIGURATION.md** -- no keys; add one sentence where `lead_ttl_days` is described: research leads left waiting age out through it like any other triage-owned lead; `leads followups` reminds you when it is 0.

- [ ] **Step 2: ARCHITECTURE.md**
  - In the `core/jdsection.py` paragraph: it is now the grammar of a lead note's body sections; `sections()` returns every section under a heading, `read()` is the first.
  - Add a `core/followups.py` paragraph: the answers section, `awaiting_reply` as the one predicate, the queue; `triage_next`/`followup_asked` and why neither is in `FRAMING_KEYS`; the verdict's stale-stamp rule and its two races; answers added to each lead's own dossier copy after the cache.
  - Find the paragraph calling `leads dismiss` one of "the TWO `leads` passes that write unconditionally" and reword it as a property with no count: the `leads` commands that record the user's own words or action on one lead they named write on every call (there is no tool-computed set to preview). Do not say "what the user typed": `mark-asked` writes a date sluice supplies.

- [ ] **Step 3: TROUBLESHOOTING.md** -- add `## Research leads keep coming back every night`: each run re-judges `research` leads; run `job-sluice leads followups`, ask the question, `leads mark-asked`, then `leads answer` with the reply; or set `lead_ttl_days` so `leads expire` ages them out.

- [ ] **Step 4: `.rulesync/rules/CLAUDE.md`** -- in "The `leads` passes report by default", replace from `**Exception: \`leads dismiss\` (#131) and \`leads add\`` through the end of the sentence `... not something the tool computed, so there is no computed set to preview first.` with:

```
**Exception: the `leads` commands that record the user's own words or action on one lead
they named -- `leads dismiss` (#131), `leads add` (#241), `leads mark-asked` and `leads
answer` (#378) -- write unconditionally on every call**, like the pipeline commands, not like
their `leads` siblings: what each records is the user's (a reason, a new lead's fields, that
they asked, the reply), not a set the tool computed, so there is nothing to preview first.
```

Keep the paragraph's remaining sentences (from `Note that is a property of the CONTENT` on), and re-read them against the new wording: none may restate "what the user typed" as the property, since `mark-asked` writes a date sluice supplies. Then regenerate:

```bash
npm ci --ignore-scripts && npm run rulesync
```

- [ ] **Step 5: Run the doc guards and the whole suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3` and `.venv/bin/ruff check sluice tests scripts`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add docs .rulesync/rules/CLAUDE.md
git commit -F - <<'EOF'
docs: describe the research follow-up worklist (#378)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 8: Whole-branch verification

- [ ] **Step 1:** `.venv/bin/python -m pytest -q` under `TZ=Asia/Dubai` and `TZ=Pacific/Kiritimati` (the facade's dates are injected; this checks nothing else reads the host clock).
- [ ] **Step 2:** `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q` (environment-dependence).
- [ ] **Step 3:** Each commit on the branch runs green on its own (`git rebase --exec ".venv/bin/python -m pytest -q -x" origin/main` in a scratch worktree with its own venv). First multiply one full-suite run by the commit count and say the total; if it is over an hour, run the per-commit loop in the background and do Step 4 meanwhile.
- [ ] **Step 4:** Live check against a synthetic vault: `VAULT_DIR=$(mktemp -d)`; `leads add` two leads, set one to `research` by hand, `leads followups`, `leads mark-asked`, `triage run --no-llm --dry-run` shows `Research queue:` and `skipped while you wait for a reply`, `leads answer`, `leads followups` again.
