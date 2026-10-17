"""`core/prepbrief.py` (#195): the pure interview prep brief -- degradations, ordering, the
closed line grammar, the JSON shape, and the brief's own section finder.

Every row is offline and pure, but its inputs are REAL notes read back through a real `Vault`
(spec, Testing (PR 1), "Fixtures are real notes"): stories are written as literal note text
carrying `Company: Example Ltd`, the lead as literal note text carrying a
`company: "Example Ltd"` frontmatter line (Amendment A2; see `_lead`), and both are read back
and adapted through `prepbrief.entry_from_store`, the adapter the facade calls, so these rows
test production code rather than a hand-built copy of its input. Roles come from the seeded
`titles` fixture, and a slug the pure rows need is composed from `COMPANY` and that role
(`_slug`). The hostile-value rows are the one exception: their company base is `COMPANY`, a
literal roster value asserted to be on `_REVIEWED_FIXTURE_IDENTITIES` where each row uses it,
and prefixed with markup.
"""
import os
import re
import shlex

import pytest

from sluice.core import followups, jdsection, prepbrief
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.rank import jd_keywords
from sluice.core.vault import Vault
from tests.test_coach_prompt import _leaks
from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES

KIND = "stories"
FIELDS = EVIDENCE_KINDS[KIND].fields
# A literal roster value, asserted on `_REVIEWED_FIXTURE_IDENTITIES` where a row relies on it.
COMPANY = "Example Ltd"
# A JD that adds keywords beyond any role: four distinct `[a-z]{4,}` words.
JD = "Build the data pipeline and deploy it with monitoring."


def _slug(role):
    """A lead slug for the pure rows, composed from the roster company and a seeded role, so
    no identity is typed by hand."""
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    return f"{COMPANY} - {role}"


@pytest.fixture
def role(titles):
    # Titles come from the seeded faker pool, never typed by hand.
    return titles[0][0].title()


def _story(root, title, best_for="", body="Body.", verified=True, category=""):
    """One story note, as literal note text whose `Company:` line the evidence collector in
    `tests/test_fixture_name_neutrality.py` reads."""
    base = os.path.join(str(root), *EVIDENCE_KINDS[KIND].relpath.split("/"))
    os.makedirs(base, exist_ok=True)
    lines = ["Company: Example Ltd"]
    if best_for:
        lines.append(f"Best For: {best_for}")
    if category:
        lines.append(f"Category: {category}")
    if verified:
        lines.append("verified: 2026-01-01")
    with open(os.path.join(base, f"{title}.md"), "w", encoding="utf-8") as fh:
        fh.write("---\n" + "\n".join(lines) + f"\n---\n{body}\n")


def _entries(root):
    """The entries exactly as the facade adapts them: every note in the kind's folder."""
    return [prepbrief.entry_from_store(e, FIELDS)
            for e in Vault(str(root)).read_evidence(KIND, verified_only=False)]


def _lead(root, role, status="interview"):
    """One lead note, as literal note text read back through a real `Vault`.

    Literal text rather than `Vault.upsert(Lead(..., company=...))` (Amendment A2): measured,
    no `_IDENTITY_COLLECTORS` member in `tests/test_fixture_name_neutrality.py` reads a
    `company=` kwarg -- its frontmatter collector needs `company:` with a colon -- so a real
    employer typed into that call would have shipped green. The quoted `company:` line below
    is the shape that collector does read, and the filename the one its lead-note filename
    collector reads."""
    leads = os.path.join(str(root), "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, f"Example Ltd - {role}.md"), "w", encoding="utf-8") as fh:
        fh.write('---\ncompany: "Example Ltd"\n'
                 f'role: "{role}"\n'
                 f"status: {status}\n"
                 'source: "s"\n'
                 'url: "https://example.invalid/1"\n'
                 f"---\n\n# Example Ltd - {role}\n")
    (note,) = Vault(str(root)).read_leads()
    return note


def _degr(kinds, *, jd=JD, jd_source="note", role="", min_jd_chars=0, jd_arrived=True,
          rejected_page=False, open_fence=False, rejected_route="", slug=""):
    return prepbrief.degradations(
        kinds=kinds, jd=jd, jd_source=jd_source, role=role, min_jd_chars=min_jd_chars,
        jd_arrived=jd_arrived, rejected_page=rejected_page, open_fence=open_fence,
        rejected_route=rejected_route, slug=slug)


def _codes(ds):
    return [d.code for d in ds]


def _build(note, kinds, *, jd=JD, jd_source="note", role=None, jd_reason="",
           jd_reason_text="", built_at="", fm=None, jd_sections=1, **degr):
    role = note.fm.get("role", "") if role is None else role
    ds = _degr(kinds, jd=jd, jd_source=jd_source, role=role, slug=note.slug, **degr)
    return prepbrief.build_brief(
        slug=note.slug, fm=note.fm if fm is None else fm, status=note.status, jd=jd,
        jd_source=jd_source, jd_reason=jd_reason, jd_reason_text=jd_reason_text,
        built_at=built_at, kinds=kinds, degradations=ds, role=role, jd_sections=jd_sections)


def _titles_in(brief):
    return [e.title for e in brief.kinds[KIND]["entries"]]


def _matched_line(markdown, title):
    """The `- Matched:` line of the entry headed `title`."""
    block = markdown.split(f"#### {title}\n", 1)[1].split("\n#### ", 1)[0]
    return next(line for line in block.split("\n") if line.startswith("- Matched: "))


# ── the adapter ─────────────────────────────────────────────────────────────────

def test_entry_from_store_carries_the_allow_list_and_no_path(tmp_path):
    _story(tmp_path, "Told", best_for="pipeline", category="ops", verified=True)
    _story(tmp_path, "Typed", best_for="deploy", verified=False)
    raw = Vault(str(tmp_path)).read_evidence(KIND, verified_only=False)
    assert all("path" in e for e in raw)  # the store does carry one; the adapter drops it
    told, typed = (prepbrief.entry_from_store(e, FIELDS) for e in raw)
    assert set(told) == {"title", "verified", "fields", "body", "best_for", "category"}
    assert told["verified"] is True and typed["verified"] is False
    assert list(told["fields"]) == [f for f in FIELDS if f in told["fields"]]
    assert told["fields"] == {"Company": "Example Ltd", "Best For": "pipeline"}
    assert told["category"] == "ops" and told["best_for"] == "pipeline"


# ── degradations, each alone ────────────────────────────────────────────────────

def test_no_entries_alone(tmp_path):
    ds = _degr({KIND: ([], 0)})
    assert _codes(ds) == [prepbrief.NO_ENTRIES]
    assert f"`job-sluice {KIND} add`" in ds[0].sentence
    assert "verify" not in ds[0].sentence


def test_no_entries_with_pending_names_verify(tmp_path):
    ds = _degr({KIND: ([], 2)})
    assert _codes(ds) == [prepbrief.NO_ENTRIES]
    assert f"`job-sluice {KIND} verify`" in ds[0].sentence and "2 waiting" in ds[0].sentence


@pytest.mark.parametrize("floor", [0, 500])
def test_no_jd_alone_at_any_floor(tmp_path, role, floor):
    _story(tmp_path, "Told", best_for="pipeline")
    # A missing JD can never have arrived, at any floor: the facade passes False.
    ds = _degr({KIND: (_entries(tmp_path), 0)}, jd="", jd_source="none", role=role,
               min_jd_chars=floor, jd_arrived=False)
    assert _codes(ds) == [prepbrief.NO_JD]


def test_jd_below_floor_alone(tmp_path, role):
    _story(tmp_path, "Told", best_for="pipeline")
    assert jd_keywords(role, JD) != jd_keywords(role, "")  # the JD adds keywords
    ds = _degr({KIND: (_entries(tmp_path), 0)}, role=role, min_jd_chars=500,
               jd_arrived=False)
    assert _codes(ds) == [prepbrief.JD_BELOW_FLOOR]
    assert "min_jd_chars (500)" in ds[0].sentence


def test_jd_no_keyword_alone_on_tbd(tmp_path, role):
    _story(tmp_path, "Told", best_for="pipeline")
    assert jd_keywords(role, "TBD") == jd_keywords(role, "")
    ds = _degr({KIND: (_entries(tmp_path), 0)}, jd="TBD", role=role)
    assert _codes(ds) == [prepbrief.JD_NO_KEYWORD]


_ROUTES = {
    # route -> (phrases the sentence must hold, phrases it must not)
    # `{q}` is the slug as `shlex.quote` renders it, filled in by each row.
    "unpark": (["`job-sluice leads unpark --apply {q}`", "needs_review"],
               ["by hand", "leads answer"]),
    "answer": (["`job-sluice leads answer --lead {q} --text ...`"],
               ["unpark", "by hand"]),
    "stamp": (["`triage_blocked`", "by hand"], ["unpark", "leads answer", "`unjudgeable`"]),
    "status": (["`unjudgeable`", "by hand"], ["unpark", "leads answer", "`triage_blocked`"]),
    "stamp_and_status": (["`triage_blocked`", "`unjudgeable`", "by hand"],
                         ["unpark", "leads answer"]),
}


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_jd_rejected_page_names_its_route(tmp_path, role, route):
    _story(tmp_path, "Told", best_for="pipeline")
    slug = _slug(role)
    ds = _degr({KIND: (_entries(tmp_path), 0)}, jd_source="cached", role=role,
               rejected_page=True, rejected_route=route, slug=slug)
    assert _codes(ds) == [prepbrief.JD_REJECTED_PAGE]
    present, absent = _ROUTES[route]
    for phrase in present:
        phrase = phrase.format(q=shlex.quote(slug))
        assert phrase in ds[0].sentence, (route, phrase)
    for phrase in absent:
        assert phrase not in ds[0].sentence, (route, phrase)
    # The stale-copy case is named, not only the judge's rejection.
    assert "older copy stayed cached" in ds[0].sentence


def test_the_route_table_is_the_modules_vocabulary():
    # The rows above and the A9 sweep below iterate THIS dict, so a route the module gains
    # without a row here would escape both, and the sweep's own scope check counts this copy.
    assert set(_ROUTES) == set(prepbrief._ROUTES)


# (jd_source, normalised status, stamped, awaiting_reply) -> (rejected_page, route)
_ROUTE_CASES = [
    ("cached", "research", True, False, (True, "unpark")),
    ("cached", "research", True, True, (True, "answer")),
    ("cached", "interview", True, False, (True, "stamp")),
    ("cached", "interview", True, True, (True, "stamp")),
    ("cached", "unjudgeable", False, False, (True, "status")),
    ("cached", "unjudgeable", True, False, (True, "stamp_and_status")),
    # Only a CACHED JD is ever rejected: the same markers with the note's own section, or none.
    ("note", "unjudgeable", True, False, (False, "")),
    ("note", "research", True, False, (False, "")),
    ("none", "unjudgeable", True, False, (False, "")),
    # Neither marker: not rejected, at any status, awaiting a reply or not.
    ("cached", "research", False, True, (False, "")),
    ("cached", "interview", False, False, (False, "")),
]


@pytest.mark.parametrize("jd_source, status, stamped, awaiting, expected", _ROUTE_CASES)
def test_rejected_route(jd_source, status, stamped, awaiting, expected):
    """The jd_rejected_page decision and its way out, pure, over the primitives
    the facade reads (the facade rows drive the same function through a real note)."""
    assert prepbrief.rejected_route(jd_source=jd_source, status=status, stamped=stamped,
                                    awaiting_reply=awaiting) == expected


def test_every_route_is_reachable():
    # Scope, through production code: the case table's inputs reach every route the module
    # can name, so no route sits behind a branch no row exercises, and a route `rejected_route`
    # can no longer produce shows up here as missing.
    reached = {prepbrief.rejected_route(jd_source=src, status=st, stamped=sp,
                                        awaiting_reply=aw)[1]
               for src, st, sp, aw, _ in _ROUTE_CASES}
    assert reached - {""} == set(prepbrief._ROUTES)


def test_an_unknown_rejected_route_raises(tmp_path):
    with pytest.raises(ValueError, match="unpark"):
        _degr({KIND: ([{"title": "t"}], 0)}, jd_source="cached", rejected_page=True,
              rejected_route="bogus")


def test_a_hostile_slug_is_only_inside_single_quotes(tmp_path):
    # Composed per the hostile-value rule: a roster base carrying `$(`, a backtick and `"`.
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    slug = f'{COMPANY} $(x) `y` "z"'
    ds = _degr({KIND: ([{"title": "t"}], 0)}, jd_source="cached", rejected_page=True,
               rejected_route="unpark", slug=slug)
    assert f"--apply '{slug}'`" in ds[0].sentence
    assert f'"{slug}"' not in ds[0].sentence


def test_note_open_fence_alone(tmp_path, role):
    _story(tmp_path, "Told", best_for="pipeline")
    ds = _degr({KIND: (_entries(tmp_path), 0)}, role=role, open_fence=True)
    assert _codes(ds) == [prepbrief.NOTE_OPEN_FENCE]


def test_two_at_once_in_codes_order(tmp_path, role):
    ds = _degr({KIND: ([], 0)}, jd="", jd_source="none", role=role, jd_arrived=False)
    assert _codes(ds) == [prepbrief.NO_ENTRIES, prepbrief.NO_JD]
    assert [c for c in prepbrief.CODES if c in _codes(ds)] == _codes(ds)


def test_one_decision_not_two(tmp_path, role):
    """The floor is the `jd_arrived` bool the facade computed through the cache, never a
    second `len(jd)` comparison here: deliberately inconsistent inputs prove which one rules."""
    _story(tmp_path, "Told", best_for="pipeline")
    kinds = {KIND: (_entries(tmp_path), 0)}
    assert len(JD) > 10 and len(JD) < 500
    assert _codes(_degr(kinds, role=role, min_jd_chars=10, jd_arrived=False)) == [
        prepbrief.JD_BELOW_FLOOR]
    assert _codes(_degr(kinds, role=role, min_jd_chars=500, jd_arrived=True)) == []


def _every_sentence(slug):
    """One degradation of every code and every rejected route, each rendered through
    production code. Returns code -> sentences."""
    by_code = {
        prepbrief.NO_ENTRIES: _degr({KIND: ([], 0)}) + _degr({KIND: ([], 3)}),
        prepbrief.NO_JD: _degr({KIND: ([{"title": "t"}], 0)}, jd="", jd_source="none",
                               jd_arrived=False),
        prepbrief.JD_BELOW_FLOOR: _degr({KIND: ([{"title": "t"}], 0)}, min_jd_chars=900,
                                        jd_arrived=False),
        prepbrief.JD_NO_KEYWORD: _degr({KIND: ([{"title": "t"}], 0)}, jd="TBD"),
        prepbrief.JD_REJECTED_PAGE: [d for r in _ROUTES for d in _degr(
            {KIND: ([{"title": "t"}], 0)}, jd_source="cached", rejected_page=True,
            rejected_route=r, slug=slug)],
        prepbrief.NOTE_OPEN_FENCE: _degr({KIND: ([{"title": "t"}], 0)}, open_fence=True),
    }
    return {c: [d.sentence for d in ds if d.code == c] for c, ds in by_code.items()}


def test_every_degradation_sentence_names_a_way_out(role):
    for code, sentences in _every_sentence(_slug(role)).items():
        assert sentences, code
        for s in sentences:
            if code == prepbrief.NO_ENTRIES:
                assert f"`job-sluice {KIND} add`" in s
            elif code == prepbrief.NOTE_OPEN_FENCE:
                assert "Close that fence" in s
            else:
                assert "Paste the full posting under `## Job description`" in s, (code, s)


def test_every_fixed_sentence_names_no_preference(tmp_path, role):
    """A9: the brief's fixed sentences are shipped prose, so they carry the coach's
    no-preference rule. Scope first: every code, every route, both order notes, pending."""
    slug = _slug(role)
    by_code = _every_sentence(slug)
    assert set(by_code) == set(prepbrief.CODES) and all(by_code.values())
    assert sum(len(v) for v in by_code.values()) >= len(prepbrief.CODES) + len(_ROUTES)
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    kinds = {KIND: (_entries(tmp_path), 2)}
    no_jd = _build(note, kinds, jd="", jd_source="none", jd_reason="no_source",
                   jd_arrived=False)
    no_kw = _build(note, kinds, jd="TBD")
    notes = [no_jd.order_note, no_kw.order_note]
    assert all(notes) and notes[0] != notes[1]
    pending = [ln for ln in prepbrief.render_markdown(no_kw).split("\n")
               if "waiting in the inbox" in ln]
    assert len(pending) == 1
    for text in [s for v in by_code.values() for s in v] + notes + pending:
        # The quoted slug is the one stated vault-value slot (Decision 7), not template prose,
        # so it is lifted out before the sweep; everything left is fixed text.
        assert _leaks(text.replace(shlex.quote(slug), "")) == [], text


# ── ordering ────────────────────────────────────────────────────────────────────

def test_casefold_title_order(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "alpha", best_for="pipeline")
    _story(tmp_path, "Beta", best_for="pipeline")
    entries = _entries(tmp_path)
    assert [e["title"] for e in entries] == ["Beta", "alpha"]  # the reader byte-sorts
    brief = _build(note, {KIND: (entries, 0)}, jd="", jd_source="none",
                   jd_reason="no_source", jd_arrived=False)
    assert _titles_in(brief) == ["alpha", "Beta"]


def test_ranked_with_a_cased_tie(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Beta", best_for="pipeline")
    _story(tmp_path, "alpha", best_for="deploy")
    _story(tmp_path, "Zeta", best_for="pipeline deploy")
    brief = _build(note, {KIND: (_entries(tmp_path), 0)})
    assert brief.degradations == []
    # Zeta scores 2; alpha and Beta tie at 1 and stay in casefold title order.
    assert _titles_in(brief) == ["Zeta", "alpha", "Beta"]
    assert [e.matched for e in brief.kinds[KIND]["entries"]] == [
        ["deploy", "pipeline"], ["deploy"], ["pipeline"]]


@pytest.mark.parametrize("arm", ["no_jd", "no_keyword"])
def test_unranked_briefs_keep_title_order(tmp_path, role, arm):
    """The entry that sorts LAST by title holds a role word, so ranking by the role alone
    would move it first; the brief keeps title order and says why."""
    role_words = jd_keywords(role, "")
    assert role_words  # the precondition needs a role word to tempt the ranker
    note = _lead(tmp_path, role)
    _story(tmp_path, "alpha", best_for="pipeline")
    _story(tmp_path, "Zeta", best_for=role_words[0])
    kinds = {KIND: (_entries(tmp_path), 0)}
    if arm == "no_jd":
        brief = _build(note, kinds, jd="", jd_source="none", jd_reason="no_source",
                       jd_arrived=False)
        sentence = ("No job description to match against, so the entries are in title "
                    "order.")
    else:
        brief = _build(note, kinds, jd="TBD")
        sentence = ("The job description adds no word beyond the role's that the ranker "
                    "can match (it reads words of four or more ASCII letters), so the "
                    "entries are in title order.")
    assert _titles_in(brief) == ["alpha", "Zeta"]
    assert brief.order_note == sentence
    md = prepbrief.render_markdown(brief)
    assert f"\n\n{sentence}\n" in md and "- Matched:" not in md


def test_haystack_parity(tmp_path, role):
    """Three stories, each matching JD words in exactly ONE field: one word in Best For,
    two in a hand-typed Category:, three in the title. Each word shows on its own story's
    Matched: line, and the order is three, two, one."""
    jd = "pipeline deploy monitoring budgeting forecasting telemetry"
    assert set(jd_keywords(role, "")).isdisjoint(jd_keywords("", jd))
    note = _lead(tmp_path, role)
    _story(tmp_path, "Alpha", best_for="pipeline")
    _story(tmp_path, "Gamma", category="deploy monitoring")
    _story(tmp_path, "Budgeting forecasting telemetry")
    brief = _build(note, {KIND: (_entries(tmp_path), 0)}, jd=jd)
    assert brief.degradations == []
    assert _titles_in(brief) == ["Budgeting forecasting telemetry", "Gamma", "Alpha"]
    md = prepbrief.render_markdown(brief)
    assert _matched_line(md, "Alpha") == "- Matched: pipeline"
    assert _matched_line(md, "Gamma") == "- Matched: deploy, monitoring"
    assert _matched_line(md, "Budgeting forecasting telemetry") == (
        "- Matched: budgeting, forecasting, telemetry")


def test_matched_with_a_keyword_less_role(tmp_path, role):
    """No role key: the role is "", never None, which would stem to `none`."""
    note = _lead(tmp_path, role)
    fm = {k: v for k, v in note.fm.items() if k != "role"}
    _story(tmp_path, "Told", best_for="none pipeline")
    assert "none" not in jd_keywords("", JD)
    brief = _build(note, {KIND: (_entries(tmp_path), 0)}, role="", fm=fm)
    md = prepbrief.render_markdown(brief)
    assert _matched_line(md, "Told") == "- Matched: pipeline"


def test_ranked_with_no_match_says_so(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="gardening")
    md = prepbrief.render_markdown(_build(note, {KIND: (_entries(tmp_path), 0)}))
    assert _matched_line(md, "Told") == "- Matched: (no keyword)"


# ── the jd line ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source, reason, text, expected", [
    ("note", "", "", f"from the note ({len(JD)} characters)"),
    ("cached", "", "", f"from a cached copy built 2026-01-02T03:04:05 ({len(JD)} characters)"),
    ("none", "no_source", "", "none (no section and no cached copy)"),
    ("none", "cache_empty", "", "none (the cached copy holds no job description)"),
    ("none", "cache_unreadable", "PermissionError: denied",
     "none (the cached copy could not be read: PermissionError: denied)"),
])
def test_jd_line(tmp_path, role, source, reason, text, expected):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    jd = "" if source == "none" else JD
    brief = _build(note, {KIND: (_entries(tmp_path), 0)}, jd=jd, jd_source=source,
                   jd_reason=reason, jd_reason_text=text, built_at="2026-01-02T03:04:05",
                   jd_arrived=bool(jd))
    assert brief.jd_line == expected
    assert brief.jd_chars == len(jd)
    assert f"\n- Job description: {expected}\n" in prepbrief.render_markdown(brief)


@pytest.mark.parametrize("source, sections, expected", [
    ("note", 2, f"from the note ({len(JD)} characters; the note has 2 job description "
                "sections and this is the first, the one triage and cv read)"),
    ("note", 1, f"from the note ({len(JD)} characters)"),
    # The count describes the NOTE's sections, so a cached JD never names it.
    ("cached", 2, f"from a cached copy built 2026-01-02T03:04:05 ({len(JD)} characters)"),
])
def test_jd_line_names_a_second_section(tmp_path, role, source, sections, expected):
    """Informational, not a degradation, so the brief stays writable."""
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    brief = _build(note, {KIND: (_entries(tmp_path), 0)}, jd_source=source,
                   built_at="2026-01-02T03:04:05", jd_sections=sections)
    assert brief.jd_line == expected and brief.degradations == []
    md = prepbrief.render_markdown(brief)
    assert f"\n- Job description: {expected}\n" in md and _grammatical(brief, md) == []


def test_an_unknown_jd_source_raises(tmp_path, role):
    # A VALID reason, so only the source guard can raise: with an empty reason the reason
    # guard below it raised too, and deleting the source guard stayed green.
    note = _lead(tmp_path, role)
    with pytest.raises(ValueError, match="unknown jd_source 'fetched'"):
        _build(note, {KIND: ([], 0)}, jd_source="fetched", jd_reason="no_source")


# ── rendering ───────────────────────────────────────────────────────────────────

_GRAMMAR = [
    re.compile(r"^" + re.escape(prepbrief.TAGGED_HEADING) + r"$"),
    re.compile(r"^#{3,4} [^\r\n]*$"),
    re.compile(r"^- [A-Za-z ]+: [^\r\n]*$"),
    re.compile(r"^> [^\r\n]*$|^>$"),
    re.compile(r"^$"),
    re.compile(r"^(?:1 entry is|\d+ entries are) waiting in the inbox\. Run "
               r"`job-sluice [a-z]+ verify`, then delete this section and run "
               r"`leads prep --write` again, to include (?:it|them)\.$"),
]


def _grammatical(brief, markdown):
    templates = {d.sentence for d in brief.degradations} | {brief.order_note}
    return [ln for ln in markdown.split("\n")
            if ln not in templates and not any(p.match(ln) for p in _GRAMMAR)]


_HOSTILE_PREFIXES = ["```", "~~~ ", "# ", "<!-- ", "## Follow-up answers "]
_HOSTILE_ROLES = ["```x", "~~~ x", "# x", "<!-- x", "## Follow-up answers"]


@pytest.mark.parametrize("prefix, hostile_role", list(zip(_HOSTILE_PREFIXES, _HOSTILE_ROLES)))
def test_line_grammar_holds_for_hostile_values(tmp_path, role, prefix, hostile_role):
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline", body="**Situation.** it\n\nthen")
    _story(tmp_path, "Typed", best_for="deploy", verified=False)
    company = prefix + COMPANY
    fm = {**note.fm, "company": company}
    brief = _build(note, {KIND: (_entries(tmp_path), 1)}, fm=fm, role=hostile_role)
    md = prepbrief.render_markdown(brief)
    assert _grammatical(brief, md) == []
    assert company in md and hostile_role in md
    # Read back as the section it is, with nothing escaping it.
    assert jdsection.sections(md, prepbrief.TAGGED_HEADING) == [md.split("\n", 1)[1].strip()]
    assert jdsection.read(md) == ("", 0)
    after = md + "\n\n" + followups.render_answer("yes", "2031-03-14")
    assert followups.answer_sections(after) == ["2031-03-14: yes"]


def test_golden_render(tmp_path, role):
    note = _lead(tmp_path, role)
    assert set(jd_keywords(role, "")).isdisjoint({"data", "pipeline", "deploy"})
    _story(tmp_path, "Outcome reached", best_for="pipeline, data",
           body="**Situation.** Built it.\n\nShipped.")
    _story(tmp_path, "Hand placed", best_for="deploy", verified=False)
    brief = _build(note, {KIND: (_entries(tmp_path), 1)})
    expected = (
        "## Interview prep brief (job-sluice) <!-- sluice:interview-prep -->\n"
        "\n"
        "- Lead: Example Ltd\n"
        f"- Role: {role}\n"
        "- Status: interview\n"
        f"- Job description: from the note ({len(JD)} characters)\n"
        "\n"
        "### Stories\n"
        "\n"
        "- Entries: 2, 1 not yet verified\n"
        "\n"
        "1 entry is waiting in the inbox. Run `job-sluice stories verify`, then delete "
        "this section and run `leads prep --write` again, to include it.\n"
        "\n"
        "#### Outcome reached\n"
        "\n"
        "- Verified: yes\n"
        "- Company: Example Ltd\n"
        "- Best For: pipeline, data\n"
        "- Matched: data, pipeline\n"
        "\n"
        "> **Situation.** Built it.\n"
        ">\n"
        "> Shipped.\n"
        "\n"
        "#### Hand placed\n"
        "\n"
        "- Verified: no\n"
        "- Company: Example Ltd\n"
        "- Best For: deploy\n"
        "- Matched: deploy\n"
        "\n"
        "> Body."
    )
    assert prepbrief.render_markdown(brief) == expected


@pytest.mark.parametrize("pending, sentence", [
    (1, "1 entry is waiting in the inbox. Run `job-sluice stories verify`, then delete this "
        "section and run `leads prep --write` again, to include it."),
    (2, "2 entries are waiting in the inbox. Run `job-sluice stories verify`, then delete this "
        "section and run `leads prep --write` again, to include them."),
])
def test_the_pending_sentence_agrees_with_its_count(tmp_path, role, pending, sentence):
    # A written brief keeps this line for good, so "1 entries are" would never be corrected.
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    brief = _build(note, {KIND: (_entries(tmp_path), pending)})
    md = prepbrief.render_markdown(brief)
    assert sentence in md.split("\n") and _grammatical(brief, md) == []


def test_degradations_render_as_their_own_paragraphs(tmp_path, role):
    note = _lead(tmp_path, role)
    brief = _build(note, {KIND: ([], 0)}, jd="", jd_source="none", jd_reason="no_source",
                   jd_arrived=False)
    md = prepbrief.render_markdown(brief)
    for d in brief.degradations:
        assert f"\n\n{d.sentence}\n" in md
    assert _grammatical(brief, md) == []


def test_cr_and_lf_cannot_open_a_section(tmp_path, role):
    """A bare CR before a level-2 heading is the shape that separates the two split rules:
    single-line values have CR/LF replaced, bodies are split by `jdsection`'s own rule."""
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    note = _lead(tmp_path, role)
    evil = "\r## Job description\rx"
    # A real read-back entry, its values composed with the hostile suffix at run time (the
    # frontmatter reader cannot carry a CR, a filename can).
    _story(tmp_path, "Told", best_for="pipeline")
    entry = _entries(tmp_path)[0]
    assert entry["fields"] and all(v for v in entry["fields"].values())
    entry = {**entry, "title": entry["title"] + evil, "body": "a" + evil,
             "fields": {k: v + evil for k, v in entry["fields"].items()}}
    brief = _build(note, {KIND: ([entry], 0)}, fm={**note.fm, "company": COMPANY + evil},
                   role="r" + evil)
    md = prepbrief.render_markdown(brief)
    assert jdsection.read(md) == ("", 0)
    assert jdsection.sections(md, prepbrief.TAGGED_HEADING) == [md.split("\n", 1)[1].strip()]
    assert "\r" not in md


def test_an_empty_body_renders_no_blockquote(tmp_path, role):
    """An entry with no body ends at its last `- ` line: no blank line and no bare `>`, which
    would render as an empty quote."""
    note = _lead(tmp_path, role)
    _story(tmp_path, "Bare", best_for="pipeline", body="")
    _story(tmp_path, "Told", best_for="deploy")
    entries = _entries(tmp_path)
    assert {e["title"]: e["body"] for e in entries}["Bare"] == ""   # scope: really empty
    brief = _build(note, {KIND: (entries, 0)})
    md = prepbrief.render_markdown(brief)
    bare = md.split("#### Bare\n", 1)[1].split("\n#### ", 1)[0]
    assert not any(ln.startswith(">") for ln in bare.split("\n"))
    assert bare.rstrip("\n").split("\n")[-1].startswith("- Matched: ")
    assert _grammatical(brief, md) == []


def test_level_four_entry_headings_keep_the_section_whole(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    _story(tmp_path, "Typed", best_for="deploy")
    md = prepbrief.render_markdown(_build(note, {KIND: (_entries(tmp_path), 0)}))
    section = jdsection.sections(md, prepbrief.TAGGED_HEADING)[0]
    assert "#### Typed" in section and section.endswith("> Body.")


# ── to_json ─────────────────────────────────────────────────────────────────────

def test_to_json_shape(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    _story(tmp_path, "Typed", best_for="deploy", verified=False)
    jd = JD + " ZZ-SENTINEL-77"
    brief = _build(note, {KIND: (_entries(tmp_path), 1)}, jd=jd, jd_source="cached",
                   built_at="2026-01-02T03:04:05")
    out = prepbrief.to_json(brief)
    assert set(out) == {"lead", "jd", "degradations", "kinds"}
    assert out["lead"] == {"slug": note.slug, "company": "Example Ltd", "role": role,
                           "status": "interview"}
    assert out["jd"] == {"source": "cached", "chars": len(jd),
                         "built_at": "2026-01-02T03:04:05", "reason": ""}
    assert out["degradations"] == []
    assert set(out["kinds"]) == {KIND}
    block = out["kinds"][KIND]
    assert set(block) == {"pending", "entries"} and block["pending"] == 1
    for e in block["entries"]:
        assert set(e) == {"title", "verified", "fields", "matched", "body"}
    assert {e["title"]: e["verified"] for e in block["entries"]} == {"Told": True,
                                                                     "Typed": False}
    flat = repr(out)
    assert "ZZ-SENTINEL-77" not in flat
    assert str(tmp_path) not in flat and prepbrief.TAG not in flat
    # No `path` KEY at any depth. A key walk, not a substring check: a seeded title such as
    # "osteopath" in a value would fail a substring check for the wrong reason.
    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)
    assert "fields" in set(keys(out))   # scope: the walk reaches the entries
    assert "path" not in set(keys(out))


def test_to_json_carries_degradations(tmp_path, role):
    note = _lead(tmp_path, role)
    brief = _build(note, {KIND: ([], 0)}, jd="", jd_source="none", jd_reason="no_source",
                   jd_arrived=False)
    out = prepbrief.to_json(brief)
    assert [d["code"] for d in out["degradations"]] == [prepbrief.NO_ENTRIES, prepbrief.NO_JD]
    assert all(set(d) == {"code", "sentence"} for d in out["degradations"])
    assert out["jd"]["reason"] == "no_source"


# ── the brief's own section ─────────────────────────────────────────────────────

def test_written_section_and_tagged_section_exists(tmp_path, role):
    note = _lead(tmp_path, role)
    _story(tmp_path, "Told", best_for="pipeline")
    md = prepbrief.render_markdown(_build(note, {KIND: (_entries(tmp_path), 0)}))
    body = note.body + "\n" + md + "\n"
    assert prepbrief.written_section(body, md) and prepbrief.tagged_section_exists(body)
    assert not prepbrief.tagged_section_exists(note.body)
    inner = md.split("\n", 1)[1]
    untagged = note.body + "\n" + prepbrief.HEADING + "\n" + inner + "\n"
    assert not prepbrief.tagged_section_exists(untagged)
    assert not prepbrief.written_section(untagged, md)
    two_spaces = note.body + f"\n{prepbrief.HEADING}  {prepbrief.TAG}\n{inner}\n"
    assert not prepbrief.tagged_section_exists(two_spaces)
    # The stated residual: a recased copy of the WHOLE tagged line still reads as sluice's.
    recased = note.body + "\n" + prepbrief.TAGGED_HEADING.upper() + "\n" + inner + "\n"
    assert prepbrief.tagged_section_exists(recased)
    # A section that is not this render's content is present but not written.
    other = note.body + "\n" + prepbrief.TAGGED_HEADING + "\n\nsomething older\n"
    assert prepbrief.tagged_section_exists(other) and not prepbrief.written_section(other, md)


def test_constants():
    assert prepbrief.TAGGED_HEADING == f"{prepbrief.HEADING} {prepbrief.TAG}"
    assert prepbrief.SUGGESTION_LIMIT == 10
    assert prepbrief.UNRANKED == frozenset({prepbrief.NO_JD, prepbrief.JD_NO_KEYWORD})
    assert len(set(prepbrief.CODES)) == len(prepbrief.CODES) == 6
