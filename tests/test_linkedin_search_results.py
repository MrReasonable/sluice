"""LinkedIn's signed-in SEARCH-RESULTS page, which replaced its job search on 2026-09-14.

What changed, measured on a live signed-in session that day: `/jobs/search/` now redirects to
`/jobs/search-results/` and drops `location`, `f_WT` and `sortBy` on the way, so an old URL
scrapes a location LinkedIn chose rather than the one asked for. The new page carries hashed
class names only. Each job card is `div[role="button"][componentkey^="job-card-component-ref-"]`,
the job id is that key's trailing digits, and a card's `innerText` reads the title (sometimes
followed by an accessibility twin of itself), the company, the location, then furniture. None
of the old `.artdeco-entity-lockup` markup survives, which is why the old extractor read zero.

The fixture below is SYNTHETIC. It keeps the STRUCTURE of a captured results column -- the
outer `role="button"` card plus the inner `div` that repeats its componentkey, the
`data-display-contents` wrapper, the visible/`aria-hidden` title pair, hashed-looking class
tokens, the line order -- and none of its text: titles come from a seeded Faker, companies from
the reviewed `Example <Word>` roster, places from conftest's `LOCATIONS`, and every id is an
invented `90000000NN`.

There is no JS engine in this suite, so `_run_extractor` stands in for the browser: it selects
with the SAME `_CARD_SELECTOR` constant the shipped extractor is built from and reads each
card's text nodes as its `innerText` lines. That emulation is the part a live DOM could
disagree with. Everything downstream of it -- the job id, the title/company/location/salary
reading, the de-duplication, the page walk -- is the shipped Python, and that is exactly why
the source keeps its extractor DOM-only: logic left in the JS could only be pinned by grepping
it, which is how the old extractor's selectors rotted with the suite green.
"""
import dataclasses
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from faker import Faker

from sluice.core.health import detect_drift
from sluice.ingest import sources as registry
from sluice.ingest.base import Ctx, Search
from sluice.ingest.sources import linkedin as li
from tests.conftest import LOCATIONS

# RFC 2606 host, so no fixture here spells the board's real domain. The PATH and the query
# keys are LinkedIn's real ones, because the path is what the old-URL guard reads.
_SEARCH_URL = ("https://example.invalid/jobs/search-results/?keywords=example%20role"
               "&geoId=1000001&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r604800")
_RETIRED_URL = "https://example.invalid/jobs/search/?keywords=example%20role&location=Alfa"
_LOGGER = "sluice.ingest.linkedin"


# ---- the synthetic results column ---------------------------------------------------------

def _titles(n):
    """`n` distinct Faker job titles. Instance-seeded, so the global Faker seed other tests
    rely on is untouched and the fixture is identical on every run."""
    fake = Faker("en_GB")
    fake.seed_instance(20260914)
    out = []
    while len(out) < n:
        title = fake.job()
        if "," not in title and title not in out:
            out.append(title)
    return out


def _cls(site, n):
    """`n` hashed-looking class tokens, stable per call site -- the live page's `_8a16025f`
    shape. Present so nothing downstream can quietly start depending on a class name."""
    return " ".join("_" + hashlib.sha1(f"{site}-{i}".encode()).hexdigest()[:8] for i in range(n))


def _svg(site):
    return (f'<svg aria-hidden="true" viewBox="0 0 16 16" class="{_cls(site, 4)}">'
            f'<path d="M0 0h16v16H0z"></path></svg>')


def _card(job_id, title, company, location, *, verified=False, twin=False, salary="",
          social="", badges=()):
    """One job card, nested the way the captured column nests it.

    `verified` appends ` (Verified job)` to the first title line; `twin` adds the `aria-hidden`
    copy of the title that some live cards carry and most do not. The componentkey sits on
    BOTH the outer `role="button"` element and the inner `div`, as it does on the live page --
    which is why an unscoped `[componentkey^=...]` query returns two elements per job.
    """
    e = html.escape
    key = f"job-card-component-ref-{job_id}"
    first = f"{title} (Verified job)" if verified else title
    twin_html = (f'<span aria-hidden="true">{e(title)}<span class="{_cls("tw", 2)}"></span>'
                 f'<span class="{_cls("ic", 3)}" aria-hidden="true">{_svg("ic")}</span></span>'
                 if twin else "")
    salary_html = f'<p class="{_cls("sal", 10)}">{e(salary)}</p>' if salary else ""
    social_html = (f'<div class="{_cls("so", 9)}"><figure class="{_cls("sf", 9)}" '
                   f'aria-hidden="true">{_svg("sf")}<img class="{_cls("si", 6)}" alt="" '
                   f'src="https://example.invalid/face.png"></figure>'
                   f'<div class="{_cls("sd", 8)}"><p class="{_cls("sp", 9)}">'
                   f'<span>{e(social)}</span></p></div></div>' if social else "")
    badge_html = "".join(f'<p class="{_cls("bd", 10)}"><span>{e(b)}</span></p>' for b in badges)
    dismiss = hashlib.sha1(job_id.encode()).hexdigest()
    return (
        f'<div role="button" tabindex="0" class="{_cls("card", 14)}" componentkey="{key}">'
        f'<div class="{_cls("inner", 12)}" componentkey="{key}">'
        f'<div class="{_cls("body", 7)}">'
        f'<figure class="{_cls("logo", 8)}" aria-hidden="true">{_svg("logo")}'
        f'<img class="{_cls("li", 6)}" alt="" src="https://example.invalid/logo.png"></figure>'
        f'<div class="{_cls("main", 9)}"><div class="{_cls("head", 7)}">'
        f'<div class="{_cls("text", 9)}"><div class="{_cls("lines", 8)}">'
        f'<div class="{_cls("dc", 2)}" data-display-contents="true">'
        f'<p class="{_cls("title", 11)}"><span class="{_cls("vh", 2)}">{e(first)}</span>'
        f'{twin_html}</p></div>'
        f'<div class="{_cls("co", 8)}"><p class="{_cls("cop", 10)}">{e(company)}</p></div>'
        f'<p class="{_cls("loc", 11)}">{e(location)}</p>{salary_html}'
        f'</div></div>'
        f'<div class="{_cls("act", 7)}"><button class="{_cls("btn", 13)}" type="button" '
        f'componentkey="auto-component-{dismiss[:8]}-{dismiss[8:12]}-{dismiss[12:16]}" '
        f'aria-label="Dismiss {e(title)} job"><span class="{_cls("bs", 21)}">{_svg("x")}'
        f'</span></button></div></div>'
        f'<div class="{_cls("foot", 10)}">{social_html}'
        f'<div class="{_cls("when", 7)}"><p class="{_cls("wp", 10)}">'
        f'<span class="{_cls("ws", 2)}">Posted 2 weeks ago</span>'
        f'<span aria-hidden="true">2 weeks ago</span></p></div>{badge_html}'
        f'</div></div></div></div></div>'
    )


def _column(cards):
    """The results column around `cards`, with the pager the live page renders beneath it."""
    return (
        f'<div class="{_cls("col", 3)}" data-testid="lazy-column" '
        f'data-component-type="LazyColumn" componentkey="SearchResultsMainContent">'
        f'{"".join(cards)}'
        f'<div class="{_cls("pager", 4)}">'
        f'<button aria-label="Page 1" aria-current="true" type="button">1</button>'
        f'<button aria-label="Page 2" aria-current="false" type="button">2</button>'
        f'</div></div>'
    )


_T = _titles(7)
_JOB_A, _JOB_B, _JOB_C, _JOB_D, _JOB_E, _JOB_F, _JOB_G = (f"90000000{n:02d}" for n in range(1, 8))
_SALARY = "55K GBP/yr - 65K GBP/yr"

_CARD_A = _card(_JOB_A, _T[0], "Example Foundry", f"{LOCATIONS[0]} (Hybrid)", verified=True,
                twin=True, social="3 connections work here")
_CARD_B = _card(_JOB_B, _T[1], "Example Systems", LOCATIONS[1], salary=_SALARY,
                badges=("Easy Apply",))
_CARD_C = _card(_JOB_C, _T[2], "Example Telemetry", f"{LOCATIONS[2]} (Remote)", twin=True,
                badges=("Saved", "Promoted"))
_CARD_D = _card(_JOB_D, _T[3], "Example Meridian", f"{LOCATIONS[3]} (On-site)",
                social="12 school alumni work here")
_CARD_E = _card(_JOB_E, _T[4], "Example Data", LOCATIONS[0])
_CARD_F = _card(_JOB_F, _T[5], "Example Cloud", LOCATIONS[1])
_CARD_G = _card(_JOB_G, _T[6], "Example Tidal", LOCATIONS[2])

# Page one carries job A TWICE, as two separate `role="button"` cards, on top of the inner-div
# duplicate every card has -- so the one page exercises both ways an id can repeat.
_PAGE_ONE = _column([_CARD_A, _CARD_B, _CARD_C, _CARD_A, _CARD_D])
# Page two repeats job B from page one, the cross-page repeat pagination has to absorb.
_PAGE_TWO = _column([_CARD_B, _CARD_E, _CARD_F])


def _view(job_id):
    return li._JOB_VIEW_URL.format(job_id)


_ROW_A = {"title": _T[0], "company": "Example Foundry", "location": f"{LOCATIONS[0]} (Hybrid)",
          "link": _view(_JOB_A), "salary": ""}
_ROW_B = {"title": _T[1], "company": "Example Systems", "location": LOCATIONS[1],
          "link": _view(_JOB_B), "salary": _SALARY}
_ROW_C = {"title": _T[2], "company": "Example Telemetry", "location": f"{LOCATIONS[2]} (Remote)",
          "link": _view(_JOB_C), "salary": ""}
_ROW_D = {"title": _T[3], "company": "Example Meridian", "location": f"{LOCATIONS[3]} (On-site)",
          "link": _view(_JOB_D), "salary": ""}
_ROW_E = {"title": _T[4], "company": "Example Data", "location": LOCATIONS[0],
          "link": _view(_JOB_E), "salary": ""}
_ROW_F = {"title": _T[5], "company": "Example Cloud", "location": LOCATIONS[1],
          "link": _view(_JOB_F), "salary": ""}


# ---- the browser stand-in ------------------------------------------------------------------

_VOID = frozenset({"img", "br", "input", "meta", "link", "hr"})


class _Element:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, attrs, []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("#root", {})
        self._open = [self.root]

    def handle_starttag(self, tag, attrs):
        el = _Element(tag, dict(attrs))
        self._open[-1].children.append(el)
        if tag not in _VOID:
            self._open.append(el)

    def handle_startendtag(self, tag, attrs):
        self._open[-1].children.append(_Element(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for depth in range(len(self._open) - 1, 0, -1):
            if self._open[depth].tag == tag:
                del self._open[depth:]
                return

    def handle_data(self, data):
        self._open[-1].children.append(data)


def _selector_matcher(selector):
    """A matcher for the `tag[attr="v"][attr^="v"]` subset of CSS, and nothing wider.

    Refuses anything outside that subset rather than approximating it: a selector this cannot
    read would otherwise match nothing, and every test built on it would pass or fail for a
    reason unrelated to the markup."""
    shape = re.fullmatch(r'([a-z]*)((?:\[[a-z-]+\^?="[^"]*"\])*)', selector)
    assert shape, f"_run_extractor cannot evaluate {selector!r}; extend it before trusting it"
    tag = shape.group(1)
    tests = re.findall(r'\[([a-z-]+)(\^?=)"([^"]*)"\]', shape.group(2))

    def matches(el):
        if tag and el.tag != tag:
            return False
        for name, op, value in tests:
            got = el.attrs.get(name)
            if got is None or (got != value if op == "=" else not got.startswith(value)):
                return False
        return True

    return matches


def _descendants(el):
    """Document order, and nested matches included -- `querySelectorAll`'s own semantics."""
    for child in el.children:
        if isinstance(child, _Element):
            yield child
            yield from _descendants(child)


def _lines(el):
    """Whitespace-collapsed, non-empty text nodes in order: this markup's `innerText` lines."""
    out = []
    for child in el.children:
        if isinstance(child, str):
            text = " ".join(child.split())
            if text:
                out.append(text)
        else:
            out.extend(_lines(child))
    return out


def _parse_html(page):
    tree = _Tree()
    tree.feed(page)
    tree.close()
    return tree.root


def _run_extractor(page):
    """What the shipped extractor returns for `page`: `{key, lines}` per selected card."""
    matches = _selector_matcher(li._CARD_SELECTOR)
    return [{"key": el.attrs.get("componentkey") or "", "lines": _lines(el)}
            for el in _descendants(_parse_html(page)) if matches(el)]


class _PagedCam:
    """Camofox stand-in serving one results column per `start=` offset.

    Keyed on the URL each TAB was opened at, so the page walk is observed through the requests
    it actually made, not through a counter the code under test could satisfy some other way.
    """

    def __init__(self, src, columns, *, probe=False, unreadable=()):
        self.src, self.columns, self.probe = src, columns, probe
        self.unreadable = set(unreadable)
        self.tabs = {}
        self.requested = []
        self.closed = 0

    def create_tab(self, url=""):
        tid = f"t{len(self.requested) + 1}"
        self.tabs[tid] = url
        self.requested.append(url)
        return tid

    def evaluate(self, tid, expr):
        if expr == "location.href":
            return {"result": self.tabs[tid]}
        if expr == self.src.auth_probe_js:
            return {"result": self.probe}
        if expr == self.src.extractor_js:
            start = _start(self.tabs[tid])
            if start in self.unreadable:
                return {"error": "target closed"}
            return {"result": _run_extractor(self.columns.get(start, _column([])))}
        return {"result": 1}

    def scroll(self, tid, amount=0):
        return {}

    def close_tab(self, tid):
        self.closed += 1
        return {}


def _start(url):
    return int(dict(parse_qsl(urlsplit(url).query)).get("start", "0"))


def _ctx(cam):
    return Ctx(camofox=cam, sleep=lambda *_: None,
               config=SimpleNamespace(source=lambda _id: SimpleNamespace(searches=[])))


def _linkedin(**changes):
    src = registry.get("linkedin")
    return dataclasses.replace(src, **changes) if changes else src


def _fetch(columns, *, url=_SEARCH_URL, label="Example search", **cam_kw):
    src = cam_kw.pop("src", None) or _linkedin()
    cam = _PagedCam(src, columns, **cam_kw)
    raw = src.fetch(_ctx(cam), Search(label, url))
    return src, cam, raw


# ---- the fixture is the shape it claims to be ------------------------------------------------

def test_the_fixture_carries_two_componentkey_elements_per_card():
    """Anti-vacuity for the scoping test below: if the fixture stopped repeating the key on the
    inner `div`, an unscoped selector would look exactly as correct as the scoped one."""
    keyed = [el for el in _descendants(_parse_html(_PAGE_ONE))
             if (el.attrs.get("componentkey") or "").startswith("job-card-component-ref-")]
    buttons = [el for el in keyed if el.attrs.get("role") == "button"]
    assert (len(keyed), len(buttons)) == (10, 5)


def test_the_card_selector_is_scoped_to_the_role_button_element():
    """Measured on the live page: the unscoped `[componentkey^="job-card-component-ref-"]`
    returned 50 elements for 25 jobs. One card per `role="button"` element, not two."""
    cards = _run_extractor(_PAGE_ONE)
    assert len(cards) == 5, [c["key"] for c in cards]
    assert [c["key"] for c in cards] == [f"job-card-component-ref-{j}"
                                         for j in (_JOB_A, _JOB_B, _JOB_C, _JOB_A, _JOB_D)]


def test_the_extractor_is_built_from_the_selector_the_stand_in_evaluates():
    """The one join between the JS and this file's emulation. If the shipped extractor stopped
    selecting with `_CARD_SELECTOR`, every test here would go on certifying a selector the
    browser never runs."""
    js = registry.get("linkedin").extractor_js
    assert js == li._JS
    # `json.dumps` is a valid JS string literal for any selector, which is how the source
    # embeds it -- spelled out here rather than calling the source's own helper, so a broken
    # helper cannot vouch for itself.
    assert json.dumps(li._CARD_SELECTOR) in js
    assert "getAttribute('componentkey')" in js and "innerText" in js


# ---- reading one card ---------------------------------------------------------------------

def _row(lines, job_id=_JOB_A):
    return li._card_row({"key": f"job-card-component-ref-{job_id}", "lines": lines})


def test_a_verified_title_and_its_accessibility_twin_read_as_one_title():
    assert _row([f"{_T[0]} (Verified job)", _T[0], "Example Foundry", "Alfa (Hybrid)",
                 "3 connections work here", "Posted 2 weeks ago"]) == {
        "title": _T[0], "company": "Example Foundry", "location": "Alfa (Hybrid)",
        "link": _view(_JOB_A), "salary": ""}


def test_a_card_without_the_twin_line_still_reads_company_then_location():
    """Most live cards carry NO twin line: the capture's title was repeated on 4 cards of 25.
    Dropping line two unconditionally would read every one of those as company=location."""
    row = _row([_T[1], "Example Systems", "Bravo", "Posted 2 weeks ago"])
    assert (row["title"], row["company"], row["location"]) == (_T[1], "Example Systems", "Bravo")


def test_only_a_line_equal_to_the_title_is_collapsed_as_its_twin():
    # The verified suffix is stripped from line one BEFORE the comparison, so the plain twin
    # below it matches; a company that merely starts like the title is not a twin.
    row = _row([f"{_T[2]} (Verified job)", _T[2], f"{_T[2]} Holdings", "Charlie"])
    assert (row["company"], row["location"]) == (f"{_T[2]} Holdings", "Charlie")


def test_a_salary_line_after_the_location_is_picked_up():
    row = _row([_T[1], "Example Systems", "Bravo", "3 connections work here", _SALARY,
                "Easy Apply"])
    assert row["salary"] == _SALARY
    assert row["location"] == "Bravo"


@pytest.mark.parametrize("line", ["55K GBP/yr - 65K GBP/yr", "60K GBP/yr", "45 GBP/hr",
                                  "500 GBP/day", "4K GBP/mo"])
def test_the_salary_shapes_are_recognised(line):
    assert _row([_T[1], "Example Systems", "Bravo", line])["salary"] == line


@pytest.mark.parametrize("line", ["3 connections work here", "12 school alumni work here",
                                  "Posted 2 weeks ago", "2 weeks ago", "Saved", "Promoted",
                                  "Easy Apply", "Be an early applicant",
                                  "GBP/yr"])       # a unit with no figure is not a salary
def test_card_furniture_is_never_read_as_a_salary(line):
    assert _row([_T[1], "Example Systems", "Bravo", line])["salary"] == ""


def test_a_salary_in_the_location_slot_is_a_salary_not_a_location():
    """A card with no location puts its salary line where the location would be. A figure is
    never a place, and a salary written into `location` would reach the lead's note name."""
    row = _row([_T[1], "Example Systems", _SALARY, "Posted 2 weeks ago"])
    assert (row["location"], row["salary"]) == ("", _SALARY)


@pytest.mark.parametrize("key", [
    "job-card-component-ref-",              # no id at all
    "job-card-component-ref-12a",           # digits must be the WHOLE suffix
    "job-card-component-ref-12 ",
    "job-card-component-ref-١٢",            # Arabic-Indic digits: `\d` would accept these
    "xjob-card-component-ref-12",           # the prefix must start the key
    "auto-component-12",
    "", None, 12,
])
def test_a_key_whose_suffix_is_not_plain_digits_yields_no_row(key):
    assert li._card_row({"key": key, "lines": [_T[0], "Example Foundry", "Alfa"]}) is None


@pytest.mark.parametrize("card", [None, "text", [], {"lines": [_T[0]]}])
def test_a_malformed_card_yields_no_row(card):
    assert li._card_row(card) is None


def test_malformed_lines_are_tolerated_rather_than_raised_on():
    # `innerText` is always a string in a browser, but the payload crosses a JSON boundary
    # and `fetch` runs this on every card of every page: one odd card must not kill the run.
    assert _row(None)["title"] == ""
    # A non-breaking space is what `innerText` hands back between a place and its work mode;
    # `str.split()` treats it as whitespace, so the location reads as the page shows it.
    # The whitespace-only line is dropped BEFORE the positional read: kept, it would take the
    # company's slot and push every field after it one place down.
    row = _row([_T[0], 7, None, " \xa0 ", "Example Foundry", "  Alfa\xa0 (Hybrid) "])
    assert (row["company"], row["location"]) == ("Example Foundry", "Alfa (Hybrid)")


def test_the_canonical_link_is_on_the_host_the_source_searches():
    # Derived from the shipped example rather than spelled here, so this file never names the
    # board's real domain -- the same arrangement tests/test_parsers.py uses for wellfound.
    searched = urlsplit(registry.get("linkedin").searches()[0].url).hostname
    link = urlsplit(_view("12345"))
    assert (link.hostname, link.path) == (searched, "/jobs/view/12345/")


# ---- one page and several -----------------------------------------------------------------

def test_one_page_yields_one_row_per_job_however_many_elements_carry_its_id():
    """Job A is on page one as two `role="button"` cards and four keyed elements in all."""
    _src, cam, raw = _fetch({0: _PAGE_ONE}, src=_linkedin(pages=1))
    assert raw["result"] == [_ROW_A, _ROW_B, _ROW_C, _ROW_D]
    assert cam.requested == [_SEARCH_URL]


def test_two_pages_are_read_and_a_job_repeated_across_them_is_kept_once():
    src, cam, raw = _fetch({0: _PAGE_ONE, 25: _PAGE_TWO})
    assert raw["result"] == [_ROW_A, _ROW_B, _ROW_C, _ROW_D, _ROW_E, _ROW_F]
    # Page one is the configured URL VERBATIM; only later pages carry an added offset.
    assert cam.requested[0] == _SEARCH_URL
    assert [_start(u) for u in cam.requested] == [0, 25]
    assert cam.closed == len(cam.requested) == 2
    # The envelope describes the SEARCH, not the last page read: the redirect and login
    # classifiers compare what was asked for with where the first page landed.
    assert raw["requested"] == _SEARCH_URL
    assert raw["landed"] == _SEARCH_URL
    hint = src.health_hint(raw)
    assert hint["count"] == 6, "health must count jobs, not card elements or page rows"
    assert hint["requested_path"] == hint["landed_path"] == "/jobs/search-results/"


def test_the_leads_parsed_from_the_pages_are_the_fixture_jobs():
    src, _cam, raw = _fetch({0: _PAGE_ONE, 25: _PAGE_TWO})
    leads = src.parse(raw, Search("Example search", _SEARCH_URL))
    assert [(lead.title, lead.company, lead.location, lead.salary, lead.url) for lead in leads] == [
        (r["title"], r["company"], r["location"], r["salary"], r["link"])
        for r in (_ROW_A, _ROW_B, _ROW_C, _ROW_D, _ROW_E, _ROW_F)]


def test_a_page_that_adds_no_new_job_ends_the_walk():
    """A page of ids already seen is what LinkedIn serves past the last real page (and what an
    ignored `start=` looks like), so reading on would only repeat it."""
    src = _linkedin(pages=3)
    _src, cam, raw = _fetch({0: _PAGE_ONE, 25: _column([_CARD_C, _CARD_A]), 50: _PAGE_TWO},
                            src=src)
    assert raw["result"] == [_ROW_A, _ROW_B, _ROW_C, _ROW_D]
    assert [_start(u) for u in cam.requested] == [0, 25]


def test_the_walk_never_reads_past_the_configured_page_count():
    src, cam, raw = _fetch({0: _PAGE_ONE, 25: _PAGE_TWO, 50: _column([_CARD_G])})
    assert len(cam.requested) == src.pages
    assert _view(_JOB_G) not in [row["link"] for row in raw["result"]]


def test_the_shipped_page_count_is_small_and_more_than_one():
    # The point of the change is to read past the first 25 results; the bound is politeness.
    assert 1 < registry.get("linkedin").pages <= 3


def test_a_logged_out_first_page_is_reported_as_auth_and_not_paginated():
    src, cam, raw = _fetch({0: _column([])}, probe=True)
    hint = src.health_hint(raw)
    assert hint.get("auth") == "missing"
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("linkedin", hint["count"], signals, baseline=25) == "auth"
    assert len(cam.requested) == 1


def test_an_unreadable_later_page_keeps_the_rows_already_read_and_says_so():
    src, cam, raw = _fetch({0: _PAGE_ONE, 25: _PAGE_TWO}, src=_linkedin(pages=3),
                           unreadable={25})
    assert raw["result"] == [_ROW_A, _ROW_B, _ROW_C, _ROW_D]
    assert raw.get("error") == "target closed", "a page that could not be read must be recorded"
    assert [_start(u) for u in cam.requested] == [0, 25]
    assert cam.closed == 2
    # Recorded, but it cannot turn a run that produced rows into `unreachable`.
    hint = src.health_hint(raw)
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("linkedin", hint["count"], signals, baseline=4) is None


def test_an_unreadable_first_page_is_an_explained_zero():
    src, cam, raw = _fetch({0: _PAGE_ONE}, unreadable={0})
    hint = src.health_hint(raw)
    assert raw["result"] == [] and hint.get("fetch_error") == "target closed"
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("linkedin", hint["count"], signals, baseline=25) == "unreachable"
    assert len(cam.requested) == 1


def test_a_search_with_no_url_reads_one_page_and_does_not_raise():
    # `Search.url` is Optional, and there is nothing to add `start=` to without one -- so even
    # a productive first page is the whole walk.
    src = _linkedin()
    cam = _PagedCam(src, {0: _PAGE_ONE})
    raw = src.fetch(_ctx(cam), Search("Example search", None))
    assert raw["result"] == [_ROW_A, _ROW_B, _ROW_C, _ROW_D]
    assert cam.requested == [None]


@pytest.mark.parametrize("payload", [7, "text", {"key": "job-card-component-ref-1"}])
def test_an_extractor_payload_that_is_not_a_list_yields_no_rows(payload):
    """The base `fetch` passes a non-list extractor result through as-is (`rows or []`), and
    this runs on it for every page -- a scalar there must read as nothing, not raise."""
    class _OddCam(_PagedCam):
        def evaluate(self, tid, expr):
            if expr == self.src.extractor_js:
                return {"result": payload}
            return super().evaluate(tid, expr)

    src = _linkedin()
    cam = _OddCam(src, {})
    raw = src.fetch(_ctx(cam), Search("Example search", _SEARCH_URL))
    assert raw["result"] == []
    assert len(cam.requested) == 1


# ---- the page URL -------------------------------------------------------------------------

def test_a_later_page_adds_its_offset_and_keeps_every_other_byte_of_the_query():
    assert li._page_url(_SEARCH_URL, 1) == _SEARCH_URL + "&start=25"
    assert li._page_url(_SEARCH_URL, 2) == _SEARCH_URL + "&start=50"


def test_an_existing_offset_is_the_base_and_is_replaced_not_duplicated():
    url = "https://example.invalid/jobs/search-results/?start=50&keywords=example%20role&geoId=1"
    assert li._page_url(url, 1) == (
        "https://example.invalid/jobs/search-results/?keywords=example%20role&geoId=1&start=75")


@pytest.mark.parametrize("start", ["", "abc", "-25", "²"])
def test_an_unreadable_offset_counts_from_zero(start):
    url = f"https://example.invalid/jobs/search-results/?keywords=x&start={start}"
    assert li._page_url(url, 1) == (
        "https://example.invalid/jobs/search-results/?keywords=x&start=25")


def test_a_url_with_no_query_gains_one():
    assert li._page_url("https://example.invalid/jobs/search-results/", 1) == (
        "https://example.invalid/jobs/search-results/?start=25")


# ---- the retired /jobs/search/ URL --------------------------------------------------------

@pytest.mark.parametrize("url", [
    _RETIRED_URL,
    "https://example.invalid/jobs/search?keywords=example%20role",
])
def test_a_retired_search_url_warns_by_label_and_says_how_to_fix_it(url, caplog):
    with caplog.at_level("WARNING", logger=_LOGGER):
        _src, _cam, raw = _fetch({0: _PAGE_ONE, 25: _PAGE_TWO}, url=url, label="Example search")
    said = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert len(said) == 1, f"expected one warning per search, not per page: {said}"
    assert "'Example search'" in said[0]
    assert "/jobs/search-results/" in said[0] and "geoId" in said[0]
    # The URL is never echoed: a search URL is keywords plus geography, and a warning travels
    # further (logs, pasted output) than the config file it came from.
    assert "example%20role" not in said[0] and "location=Alfa" not in said[0]
    # Warned, not refused: see `_LinkedInSource.fetch` for why a refusal is the worse failure.
    assert raw["result"], "the search must still run"


@pytest.mark.parametrize("url", [_SEARCH_URL, "https://example.invalid/jobs/search-resultsx/",
                                 "https://example.invalid/jobs/searches/", "http://x"])
def test_a_current_or_unrelated_url_does_not_warn(url, caplog):
    with caplog.at_level("WARNING", logger=_LOGGER):
        _fetch({0: _PAGE_ONE}, url=url)
    assert not [r for r in caplog.records if r.name == _LOGGER]


def test_the_shipped_example_is_the_search_results_shape_with_a_location_id():
    url = urlsplit(registry.get("linkedin").searches()[0].url)
    query = dict(parse_qsl(url.query))
    assert url.path == "/jobs/search-results/"
    assert query.get("geoId", "").isdigit(), "without geoId LinkedIn picks the location itself"
    # `f_TPR` survives only alongside this `origin`, so the pair ships together.
    assert query.get("origin") == "JOB_SEARCH_PAGE_JOB_FILTER" and query.get("f_TPR")
    assert not {"location", "f_WT", "sortBy"} & set(query), "LinkedIn drops these"


def test_an_unparseable_url_is_not_reported_as_retired():
    # `urlsplit` raises on an unbalanced IPv6 bracket. The guard runs before the fetch, so a
    # raise there would take the whole search down over a warning it could not decide.
    assert li._is_retired_search_url("http://[::1") is False


# ---- construction -------------------------------------------------------------------------

@pytest.mark.parametrize("pages", [0, -1, True, 1.5, "2", None])
def test_a_page_count_that_is_not_a_positive_int_refuses_at_construction(pages):
    with pytest.raises(ValueError, match="pages"):
        _linkedin(pages=pages)


def test_the_subclass_still_runs_the_base_contract_validation():
    # `__post_init__` is overridden to check `pages`. Without its `super().__post_init__()`
    # call the base's `posting_paths`/`reprobed`/`searches_spec` checks would silently stop
    # running for this one source -- and a bare-string `posting_paths` admits every url.
    with pytest.raises(ValueError, match="posting_paths"):
        _linkedin(posting_paths="/jobs/")
