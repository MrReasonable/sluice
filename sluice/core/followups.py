"""Research follow-ups (#378): the `## Follow-up answers` grammar, the ONE `awaiting_reply`
predicate, and the queue `leads followups` and the triage digest both read.

A `research` verdict ends in a question for a human to ask (`triage_next`). Marking a lead
asked stamps `followup_asked`, and triage then skips it; recording the reply appends a
`## Follow-up answers` section and clears the stamp, and the next triage run shows the judge
the answers. Pure and stdlib-only, beside `core/jdsection.py`, whose section grammar this
reuses: triage and the facade both read it (the CLI through the facade), and `core/` may not
import a sub-app.

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


def dated_answer(text: str, today: str) -> str:
    """The answer's text exactly as written into its section, and so as `answers` reads it
    back: `<date>: <text>`, neutralised AS ONE TEXT. Neutralising the bare text and then
    prefixing the date miscounts fences -- a fence on the first line stops being one once the
    date sits in front of it, so the text's own closing fence would open one instead and run
    to the end of the body."""
    return jdsection.neutralise(f"{today}: {text}")


def render_answer(text: str, today: str) -> str:
    """One answer as its own section: a bare heading (exactly what a person would type), the
    tag on its own line, then the dated text neutralised -- breaking lines demoted and an
    open fence closed, so neither this answer nor a later one is cut short or hidden."""
    return f"{ANSWERS_HEADING}\n\n{answer_tag(text, today)}\n{dated_answer(text, today)}"


def answer_sections(body: str) -> list:
    """Each `## Follow-up answers` section's text, tag lines dropped, in body order; empty
    sections omitted. A written answer reads back as exactly its `dated_answer`, which is how
    `Sluice.record_answer` tells an answer that is its own section from one swallowed into an
    earlier section's text."""
    parts = []
    for text in jdsection.sections(body, ANSWERS_HEADING):
        kept = "\n".join(line for line in text.split("\n")
                         if not _TAG_LINE.match(line.strip())).strip()
        if kept:
            parts.append(kept)
    return parts


def answers(body: str) -> str:
    """Every `## Follow-up answers` section's text, joined in body order. EVERY section, not
    the first: each answer is its own section, and taking one would silently drop every later
    reply."""
    return "\n\n".join(answer_sections(body))


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
