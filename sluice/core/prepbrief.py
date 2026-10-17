"""The interview prep brief (#195): pure degradations, ordering, rendering and its section finder.

Spec: `docs/superpowers/specs/2026-10-16-interview-prep-design.md`, sections 1-5 and Decision 7.

Placement and import shape. Everything here is pure and imports only `core/rank.py` and
`core/jdsection.py`, so it sits inside `tests/test_core_layering.py`'s checks. It is driven the
other way round from a command package: the facade (`Sluice.prep_brief`) calls it, mcpserver
reads `TAGGED_HEADING` through its isolation allow-list, and `cli.py`'s `leads prep` handler
imports `HEADING` lazily so its stderr names the real heading. No sub-app imports it.

What the facade hands in. Only ONE fact needs something this module does not hold: whether the
JD arrived, which is `DossierCache.jd_arrived` (the one floor rule triage and cv apply) and so
needs the cache the facade built. The rest is passed as primitives the facade read from the
note -- whether it ends inside an open fence, its normalised status, whether `triage_blocked`
holds a stamp, and whether the lead awaits a reply -- and the DECISIONS over them live here
(`rejected_route`), so they are tested without a vault. Passing primitives rather than the
frontmatter is a choice, not a constraint: `core/status`, `core/leads` and `core/followups` are
pure and `tests/test_core_layering.py` would allow them, but reading the note's keys is the
facade's job, and this module stays importing only `rank` and `jdsection`.

The closed line grammar (spec section 4). Every line `render_markdown` emits is one of: the
tagged heading; `### ` or `#### ` plus a single-line value; `- <Label>: <single-line value>`;
`> ` plus body text, or a bare `>`; a blank line; or a fixed sentence template below with its
slots filled. No VALUE ever starts a line, so a scraped company or role beginning with a fence,
`#` or `<!--` cannot open a fence, a heading or an HTML block -- measured, an unlabelled company
line starting with a backtick fence hid a later `## Follow-up answers` section from the judge.
Single-line values have CR and LF replaced by spaces; bodies are split by `jdsection.lines`, the
reader's own rule, and blockquoted line by line; the brief's own headings are levels 3 and 4, so
none can end its section.

A sentence's slots take only counts, kind names, command names and `min_jd_chars`, never a
scraped or vault value -- with ONE stated exception: the lead's slug enters the
`jd_rejected_page` way out (Decision 7), shell-quoted with `shlex.quote` so a pasted command
cannot run a `$(...)` a scraped company carried. Every noun and command naming a kind is derived
from the kind key, so no kind is named literally here.
"""
import shlex
from dataclasses import dataclass

from sluice.core import jdsection
from sluice.core.rank import jd_keywords, matched_keywords, rank

HEADING = "## Interview prep brief (job-sluice)"
# Why a tag as well as the heading: `Store.append_body_section`'s idempotence is "the tag
# appears anywhere in the fresh file", and the heading text alone could sit in a pasted body.
TAG = "<!-- sluice:interview-prep -->"
TAGGED_HEADING = f"{HEADING} {TAG}"
# The cap on `not_found` suggestions (Decision 10): a one-letter fragment must not return the
# whole backlog.
SUGGESTION_LIMIT = 10

# The degradation codes are a CONTRACT (they reach `leads prep --json`), in Decision 7's order.
NO_ENTRIES, NO_JD, JD_BELOW_FLOOR = "no_entries", "no_jd", "jd_below_floor"
JD_NO_KEYWORD, JD_REJECTED_PAGE, NOTE_OPEN_FENCE = (
    "jd_no_keyword", "jd_rejected_page", "note_open_fence")
CODES = (NO_ENTRIES, NO_JD, JD_BELOW_FLOOR, JD_NO_KEYWORD, JD_REJECTED_PAGE, NOTE_OPEN_FENCE)
# A brief carrying either code is in title order (Decision 11): there is nothing usable to rank
# against, and a role-only ranking would claim a relevance the brief cannot back.
UNRANKED = frozenset({NO_JD, JD_NO_KEYWORD})

_PASTE = "Paste the full posting under `## Job description` in the note."
_NO_JD_ORDER = "No job description to match against, so the entries are in title order."
_NO_KEYWORD_ORDER = ("The job description adds no word beyond the role's that the ranker can "
                     "match (it reads words of four or more ASCII letters), so the entries are "
                     "in title order.")

# The `- Job description:` line for each `none` reason (spec section 3). A reason outside this
# map raises rather than rendering a blank: a quiet wrong default is the bug class.
_NONE_LINES = {
    "no_source": "none (no section and no cached copy)",
    "cache_empty": "none (the cached copy holds no job description)",
}
_UNREADABLE = "cache_unreadable"


@dataclass(frozen=True)
class Degradation:
    code: str
    sentence: str


@dataclass
class BriefEntry:
    title: str
    verified: bool
    fields: dict
    matched: list
    body: str


@dataclass
class Brief:
    slug: str
    company: str
    role: str
    status: str
    jd_source: str
    jd_chars: int
    built_at: str
    jd_reason: str
    jd_line: str
    kinds: dict
    degradations: list
    order_note: str = ""


def entry_from_store(e: dict, fields: tuple) -> dict:
    """One `Store.read_evidence` dict as a prep entry, on an allow-list (spec section 2).

    The ONE adapter, called by the facade and by the pure tests alike, so those tests feed
    production code. `verified` becomes a bool (the store carries the date or None); the kind's
    declared `fields` keep their declared order (those the entry carries); `best_for` and
    `category` ride along for `rank` and are never shown -- a hand-typed `Category:` still
    feeds the ranker, and any keyword it matched is listed. `path` is never carried."""
    have = e.get("fields") or {}
    return {
        "title": e.get("title", "") or "",
        "verified": bool(e.get("verified")),
        "fields": {name: have[name] for name in fields if name in have},
        "body": e.get("body", "") or "",
        "best_for": e.get("best_for", "") or "",
        "category": e.get("category", "") or "",
    }


def _one(value) -> str:
    """A single-line value: CR and LF become spaces (a title comes from a filename, and this
    renderer does not trust where its input came from)."""
    return str(value or "").replace("\r", " ").replace("\n", " ")


_ROUTES = ("unpark", "answer", "stamp", "status", "stamp_and_status")


def rejected_route(*, jd_source: str, status: str, stamped: bool,
                   awaiting_reply: bool) -> tuple:
    """`(rejected_page, route)`: whether the JD is a cached page triage would not judge, and,
    when it is, the way out that fits the lead's state (Decision 7). `route` is ""
    when the page is not rejected.

    The inputs are primitives the facade read: `status` is the store-normalised value, never
    the raw `status:` key, so a recased `Unjudgeable` is still the status triage wrote;
    `stamped` is `core/leads.py::blocked_stamp`, never the raw key, since a word a person typed
    into `triage_blocked` is their text, not a stamp; `awaiting_reply` is
    `core/followups.py::awaiting_reply`.

    Only a CACHED JD can be rejected: a section the user pasted is theirs, whatever triage made
    of the fetched page. `leads unpark` reaches only a stamped `research` lead not awaiting a
    reply, `leads answer` the one that is; anything else is a hand edit, and a lead carrying
    BOTH markers is named both, so following the named way out once unblocks the write."""
    if not (jd_source == "cached" and (status == "unjudgeable" or stamped)):
        return False, ""
    if stamped and status == "research":
        return True, "answer" if awaiting_reply else "unpark"
    if stamped and status == "unjudgeable":
        return True, "stamp_and_status"
    return True, "stamp" if stamped else "status"


def _rejected_way_out(route: str, slug: str) -> str:
    """The way out of `jd_rejected_page` for the lead's state, when the posting is gone and the
    user knows the cached copy is real. `rejected_route` picks the route; each names the one
    action that clears the marker it has, and `stamp_and_status` names both, so following the
    named way out once unblocks the write."""
    # shlex.quote, not double quotes: the slug is a filename built from scraped company and
    # title, and a printed command the user pastes must not run a `$(...)` it carries.
    slug = shlex.quote(_one(slug))
    lead_in = " If you know this cached copy is the real posting, "
    if route == "unpark":
        return (f"{lead_in}run `job-sluice leads unpark --apply {slug}`; it clears the stamp "
                f"and also moves the lead to needs_review.")
    if route == "answer":
        return (f"{lead_in}record the reply with "
                f"`job-sluice leads answer --lead {slug} --text ...`; it clears the stamp.")
    if route == "stamp":
        return f"{lead_in}delete the `triage_blocked` value in the note by hand."
    if route == "status":
        return f"{lead_in}move the lead's status off `unjudgeable` by hand."
    if route == "stamp_and_status":
        return (f"{lead_in}delete the `triage_blocked` value in the note and move the lead's "
                f"status off `unjudgeable`, both by hand.")
    raise ValueError(f"unknown rejected_route {route!r}; expected one of {', '.join(_ROUTES)}")


def degradations(*, kinds, jd, jd_source, role, min_jd_chars, jd_arrived, rejected_page,
                 open_fence, rejected_route, slug) -> list:
    """What a `--write` would lock in degraded (Decision 7), each with its fixed sentence, in
    `CODES` order. `kinds` maps each kind to `(entries, pending_count)`.

    `min_jd_chars` only fills the floor sentence's slot: the DECISION is the `jd_arrived` bool
    the facade computed through `DossierCache.jd_arrived`, never a second length comparison
    here, so a JD of exactly the floor passes here exactly as it passes triage and cv."""
    jd = jd or ""
    out = []
    for kind, (entries, pending) in kinds.items():
        if not entries:
            more = (f", or review the {pending} waiting in the inbox with "
                    f"`job-sluice {kind} verify`" if pending else "")
            out.append(Degradation(NO_ENTRIES, f"No {kind} entries to rehearse. Add one with "
                                               f"`job-sluice {kind} add`{more}."))
    if jd_source == "none":
        out.append(Degradation(NO_JD, f"No job description was found. {_PASTE}"))
    else:
        # ONLY WHEN A JD EXISTS (Decision 7): a missing JD reports no_jd alone.
        if not jd_arrived:
            out.append(Degradation(JD_BELOW_FLOOR, (
                f"The job description is shorter than min_jd_chars ({min_jd_chars}), so it may "
                f"be page text rather than a posting. {_PASTE}")))
        if jd_keywords(role, jd) == jd_keywords(role, ""):
            out.append(Degradation(JD_NO_KEYWORD, (
                "The job description adds no word beyond the role's that the ranker can match "
                f"(it reads words of four or more ASCII letters). {_PASTE}")))
        if rejected_page:
            out.append(Degradation(JD_REJECTED_PAGE, (
                "The cached job description is a page triage would not judge: the judge "
                "rejected it, or a later fetch came back empty while this older copy stayed "
                f"cached. {_PASTE}" + _rejected_way_out(rejected_route, slug))))
    if open_fence:
        out.append(Degradation(NOTE_OPEN_FENCE, (
            "The note ends inside an unclosed code fence, so a brief written now would be "
            "hidden inside it. Close that fence with a line of the same character (backticks "
            "or tildes), at least as many as opened it.")))
    return sorted(out, key=lambda d: CODES.index(d.code))


def _jd_line(jd_source: str, jd_chars: int, built_at: str, jd_reason: str,
             jd_reason_text: str, jd_sections: int) -> str:
    if jd_source == "note":
        # A second pasted section (an updated posting, say) is NAMED rather than refused:
        # triage and cv read the first and log the rest, and a brief is written
        # once, so the line says which one it was built on before the user locks it in.
        more = (f"; the note has {jd_sections} job description sections and this is the "
                f"first, the one triage and cv read" if jd_sections > 1 else "")
        return f"from the note ({jd_chars} characters{more})"
    if jd_source == "cached":
        return f"from a cached copy built {_one(built_at)} ({jd_chars} characters)"
    if jd_source != "none":
        raise ValueError(f"unknown jd_source {jd_source!r}; expected note, cached or none")
    if jd_reason == _UNREADABLE:
        return f"none (the cached copy could not be read: {_one(jd_reason_text)})"
    if jd_reason not in _NONE_LINES:
        raise ValueError(f"unknown jd_reason {jd_reason!r} for jd_source 'none'")
    return _NONE_LINES[jd_reason]


def build_brief(*, slug, fm, status, jd, jd_source, jd_reason, jd_reason_text, built_at, kinds,
                degradations, role, jd_sections) -> Brief:
    """The brief, pure. `kinds` maps each kind to `(entries, pending_count)`, the entries from
    `entry_from_store`; `role` is the facade's ONE read of the note's role, the same value it
    handed `degradations`; `jd_sections` is `jdsection.read`'s count of the note's job
    description sections, named on the JD line when a note-section JD has more than one.

    Entries sort by casefolded title first: the reader's listing is byte-sorted, which differs
    from title order exactly when titles differ in case. Unless the codes are `UNRANKED`,
    `rank` follows; it is a stable sort, so ties stay in title order. Whether to rank is read
    from the codes and never recomputed here (Decision 11)."""
    codes = {d.code for d in degradations}
    ranked = not (codes & UNRANKED)
    keywords = jd_keywords(role, jd) if ranked else []
    out_kinds = {}
    for kind, (entries, pending) in kinds.items():
        ordered = sorted(entries, key=lambda e: str.casefold(e.get("title", "")))
        if ranked:
            ordered = rank(ordered, keywords)
        out_kinds[kind] = {"pending": pending, "entries": [
            BriefEntry(title=e.get("title", ""), verified=bool(e.get("verified")),
                       fields=dict(e.get("fields") or {}),
                       matched=matched_keywords(e, keywords) if ranked else [],
                       body=e.get("body", "") or "")
            for e in ordered]}
    order_note = (_NO_JD_ORDER if NO_JD in codes
                  else _NO_KEYWORD_ORDER if JD_NO_KEYWORD in codes else "")
    jd_chars = len(jd or "")
    return Brief(slug=slug, company=fm.get("company", "") or "", role=role or "",
                 status=status or "", jd_source=jd_source, jd_chars=jd_chars,
                 built_at=built_at or "", jd_reason=jd_reason or "",
                 jd_line=_jd_line(jd_source, jd_chars, built_at, jd_reason, jd_reason_text,
                                  jd_sections),
                 kinds=out_kinds, degradations=list(degradations), order_note=order_note)


def _pending_sentence(kind: str, pending: int) -> str:
    # Agreement by count: a written brief is permanent, so a "1 entries are" would sit in the
    # note for good.
    waiting = "entry is" if pending == 1 else "entries are"
    return (f"{pending} {waiting} waiting in the inbox. Run `job-sluice {kind} verify`, then "
            f"delete this section and run `leads prep --write` again, to include "
            f"{'it' if pending == 1 else 'them'}.")


def render_markdown(brief: Brief) -> str:
    """The exact text `--write` appends, on the closed line grammar in the module docstring."""
    ranked = not ({d.code for d in brief.degradations} & UNRANKED)
    out = [TAGGED_HEADING, "",
           f"- Lead: {_one(brief.company)}",
           f"- Role: {_one(brief.role)}",
           f"- Status: {_one(brief.status)}",
           f"- Job description: {_one(brief.jd_line)}"]
    for sentence in [d.sentence for d in brief.degradations] + [brief.order_note]:
        if sentence:
            out += ["", sentence]
    for kind, block in brief.kinds.items():
        entries = block["entries"]
        unverified = sum(1 for e in entries if not e.verified)
        out += ["", f"### {kind.capitalize()}", "",
                f"- Entries: {len(entries)}, {unverified} not yet verified"]
        if block["pending"]:
            out += ["", _pending_sentence(kind, block["pending"])]
        for e in entries:
            out += ["", f"#### {_one(e.title)}", "",
                    f"- Verified: {'yes' if e.verified else 'no'}"]
            out += [f"- {_one(name)}: {_one(value)}" for name, value in e.fields.items()]
            if ranked:
                out.append(f"- Matched: {', '.join(e.matched) if e.matched else '(no keyword)'}")
            if e.body:
                # Split by the reader's own rule (CR, CRLF, LF), so no body line reaches the
                # note unquoted: unquoted, a body's `## Job description` became the note's JD.
                out.append("")
                out += ["> " + line if line else ">" for line in jdsection.lines(e.body)]
    return "\n".join(out)


def to_json(brief: Brief) -> dict:
    """The brief as a dict, on an allow-list whose shape is fixed (`leads prep --json` prints it):
    `verified` is a JSON boolean, never the rendered yes/no, and there is no `path`, no JD text
    and no markdown."""
    return {
        "lead": {"slug": brief.slug, "company": brief.company, "role": brief.role,
                 "status": brief.status},
        "jd": {"source": brief.jd_source, "chars": brief.jd_chars, "built_at": brief.built_at,
               "reason": brief.jd_reason},
        "degradations": [{"code": d.code, "sentence": d.sentence} for d in brief.degradations],
        "kinds": {kind: {"pending": block["pending"],
                         "entries": [{"title": e.title, "verified": bool(e.verified),
                                      "fields": dict(e.fields), "matched": list(e.matched),
                                      "body": e.body}
                                     for e in block["entries"]]}
                  for kind, block in brief.kinds.items()},
    }


def _own_sections(body: str) -> list:
    # The WHOLE tagged heading line, no `tag` argument: jdsection._is_heading then compares the
    # entire line, so an untagged copy of the heading never counts (spec section 1).
    return jdsection.sections(body, TAGGED_HEADING)


def written_section(body: str, markdown: str) -> bool:
    """True when `markdown` minus its heading line, stripped, is one of the note's tagged
    sections: the brief landed as its own section (`record_answer`'s rule)."""
    return markdown.split("\n", 1)[1].strip() in _own_sections(body)


def tagged_section_exists(body: str) -> bool:
    """True when the note holds a section under the exact tagged heading line."""
    return bool(_own_sections(body))
