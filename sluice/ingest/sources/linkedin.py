"""LinkedIn job search, read from the signed-in SEARCH-RESULTS page.

2026-09-14: LinkedIn replaced its signed-in job search, and the previous extractor (keyed on
`.artdeco-entity-lockup` cards) read zero on every search until the source auto-retired.
Measured on a live signed-in session that day:

- `/jobs/search/?keywords=...&location=...&f_WT=...&sortBy=...` now REDIRECTS to
  `/jobs/search-results/?currentJobId=<id>&keywords=...`, dropping `location`, `f_WT` and
  `sortBy` on the way, and LinkedIn substitutes a location of its own choosing. So an old URL
  does not fail: it quietly scrapes the wrong place. `_LinkedInSource.fetch` warns about one.
- The URL that works is `/jobs/search-results/?keywords=<kw>&geoId=<numeric id>`, and the page
  honours that location. `f_TPR` (date posted, e.g. `r604800` = past week) survives only when
  `origin=JOB_SEARCH_PAGE_JOB_FILTER` rides along with it; `f_WT` and `sortBy` are dropped even
  then, so neither work type nor sort order can be asked for in the URL any more.
- `start=25`, `start=50`, ... paginate, 25 results a page. Scrolling the column loads no more,
  which is why this source walks pages instead of scrolling for them.
- Every class name is hashed and rotates (`_8a16025f ...`), so nothing here keys on one. A job
  card is `div[role="button"][componentkey^="job-card-component-ref-"]`, the job id is that
  key's trailing digits, and the canonical posting is `/jobs/view/<id>/`. The card's inner
  `div` repeats the same componentkey, so the UNSCOPED attribute query returns two elements per
  job -- hence the `role="button"` scope, and de-duplication by id on top of it.
- A card's `innerText`, split into trimmed non-empty lines: the title, sometimes with a trailing
  ` (Verified job)`; on SOME cards (not most) an accessibility twin of the title; the company;
  the location, perhaps with ` (Hybrid)`, ` (On-site)` or ` (Remote)`; then furniture -- a
  salary such as `70K GBP/yr - 90K GBP/yr`, social proof (`N connections work here`),
  `Posted 2 weeks ago`, `Saved`, `Promoted`, `Easy Apply`.

FINDING A geoId. Choose the location in LinkedIn's own job search, run the search, and read
`geoId=` from the address bar: it is a number, and the place name never needs to appear in the
URL. A search then looks like the shipped example below,
`/jobs/search-results/?keywords=software%20developer&geoId=<id>&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r604800`.

WHERE THE READING HAPPENS. The extractor JS is DOM-only: per card it returns the componentkey
and the `innerText` lines, nothing more. Picking out the id, title, company, location and salary
is Python (`_card_row`), run inside `fetch` so the raw payload keeps the ordinary
`{title, company, location, link, salary}` row shape every other source emits, which is also
what `health_hint` counts. The split is for testability and it is not cosmetic: the suite has no
JS engine, so logic left in the JS could only be pinned by grepping for it -- which is how the
previous extractor's selectors went stale while every test stayed green.
"""
import dataclasses
import json
import re
from urllib.parse import urlsplit, urlunsplit

from sluice.core.log import get_logger
from sluice.ingest.base import BrowserListSource, Search
from sluice.ingest.sources import register

_log = get_logger("ingest.linkedin")

_CARD_KEY_PREFIX = "job-card-component-ref-"
# Scoped to the `role="button"` element because the card's inner `div` carries the same
# componentkey: unscoped, the live page returned 50 elements for 25 jobs.
_CARD_SELECTOR = f'div[role="button"][componentkey^="{_CARD_KEY_PREFIX}"]'
# `[0-9]`, NOT `\d`: Python's `\d` also matches Arabic-Indic and every other Unicode digit, and
# a suffix in those is not a LinkedIn job id -- nor something to paste into a posting URL.
_CARD_KEY_RE = re.compile(re.escape(_CARD_KEY_PREFIX) + r"([0-9]+)")
_JOB_VIEW_URL = "https://www.linkedin.com/jobs/view/{}/"
_PAGE_SIZE = 25
_VERIFIED_SUFFIX = " (Verified job)"
# LinkedIn's compact pay format: a figure and a per-period unit (`70K GBP/yr - 90K GBP/yr`).
# Requiring BOTH is what keeps furniture out -- `Posted 2 weeks ago` and `3 connections work
# here` carry a digit and no unit, and a bare unit with no figure is not a salary. `/yr` is
# the measured form; `/hr`, `/mo`, `/wk` and `/day` are the same format's other periods,
# admitted on that shape rather than on a capture. No trailing word boundary: it would refuse
# a spelled-out `/month` or `/days`, and no furniture line carries a figure and a slash-unit.
_SALARY_RE = re.compile(r"[0-9].*/(?:yr|hr|mo|wk|day)")
_RETIRED_SEARCH_PATH = "/jobs/search"


def _js_string(value: str) -> str:
    """`value` as a JavaScript string literal. JSON's string grammar is a subset of JS's, so
    this quotes any selector correctly, including one that carries both quote characters --
    which `_CARD_SELECTOR` does not today, and a hand-quoted `'...'` would break on."""
    return json.dumps(value)


# DOM-only on purpose; see the module docstring. `getAttribute(...)||''` so a card that somehow
# lost its key still yields a row-shaped object, which `_card_row` then declines by name.
_JS = (
    "(()=>Array.from(document.querySelectorAll(" + _js_string(_CARD_SELECTOR) + "),"
    "card=>({key:card.getAttribute('componentkey')||'',"
    "lines:(card.innerText||'').split('\\n').map(s=>s.trim()).filter(Boolean)})))()"
)

# Scroll the LAST card into view rather than setting `scrollTop` on a container: the results
# column is a lazily-filled `LazyColumn`, and which of its hashed ancestors actually scrolls is
# not something the markup lets us name. `scrollIntoView` scrolls whichever ones must move.
# Falls back to the window when no card has rendered yet.
_COLUMN_SCROLL = (
    "(()=>{const cards=document.querySelectorAll(" + _js_string(_CARD_SELECTOR) + ");"
    "if(cards.length){cards[cards.length-1].scrollIntoView({block:'end'});}"
    "else{window.scrollBy(0,900);}return 1;})()"
)


def _job_id(key) -> str | None:
    """The job id: the componentkey's trailing digits, and ONLY when they are the whole suffix.

    Strict rather than "the last run of digits": `job-card-component-ref-12a` is not a shape
    the page has been seen to emit, and a lenient read of an unknown shape would mint a link to
    a posting nobody verified exists."""
    if not isinstance(key, str):
        return None
    match = _CARD_KEY_RE.fullmatch(key)
    return match.group(1) if match else None


def _card_row(card) -> dict | None:
    """One extractor card (`{key, lines}`) as a standard row, or `None` when it has no job id.

    Positional, because the page offers nothing else: every class is hashed, and the title,
    company and location are sibling text with no stable attribute between them.

    - Line one is the title, with ` (Verified job)` stripped -- the badge is not part of the
      role, and leaving it would split one posting into two lead identities.
    - The next line is dropped ONLY when it equals that title. Some cards repeat the title as
      an accessibility twin and most do not, so dropping it unconditionally would read the
      location as the company on the majority of cards.
    - Then the company, then the location -- unless that slot holds a salary-shaped line, which
      is a card with no location rather than a location: a figure written into `location`
      would reach the lead's note name and its dedup identity.
    - The salary is the first salary-shaped line after the company, else "".
    """
    if not isinstance(card, dict):
        return None
    job_id = _job_id(card.get("key"))
    if job_id is None:
        return None
    raw_lines = card.get("lines")
    # Whitespace-collapsed and non-string-tolerant: the payload crosses a JSON boundary, and
    # this runs on every card of every page, so one odd card must not raise the whole search.
    lines = [" ".join(line.split()) for line in (raw_lines if isinstance(raw_lines, list) else [])
             if isinstance(line, str)]
    lines = [line for line in lines if line]
    title = lines[0].removesuffix(_VERIFIED_SUFFIX) if lines else ""
    rest = lines[1:]
    if rest and rest[0] == title:
        rest = rest[1:]
    company = rest[0] if rest else ""
    location = rest[1] if len(rest) > 1 and not _SALARY_RE.search(rest[1]) else ""
    salary = next((line for line in rest[1:] if _SALARY_RE.search(line)), "")
    return {"title": title, "company": company, "location": location,
            "link": _JOB_VIEW_URL.format(job_id), "salary": salary}


def _page_url(url: str, page: int) -> str:
    """`url` for zero-based `page`, by setting `start=`.

    Rewrites the query TEXTUALLY rather than through `parse_qsl`/`urlencode`, which would
    re-encode every other parameter (`%20` becomes `+`) and hand LinkedIn a URL the user never
    wrote. Only `start` is touched: any existing `start=` pairs are removed and one appended.

    An existing plain-digit `start` is the BASE offset, so a search configured to begin at
    `start=50` walks 50, 75, 100. Anything else there (blank, negative, non-ASCII digits) counts
    from zero rather than raising: it is a user's URL, and the first page already ran it as-is.
    """
    parts = urlsplit(url)
    base, kept = 0, []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        if name == "start":
            if value.isascii() and value.isdigit():
                base = int(value)
            continue
        kept.append(pair)
    kept.append(f"start={base + page * _PAGE_SIZE}")
    return urlunsplit(parts._replace(query="&".join(kept)))


def _is_retired_search_url(url) -> bool:
    """Is `url` on the retired `/jobs/search/` path (with or without the trailing slash)?

    An exact path comparison, not a prefix: `/jobs/search-results/` starts with the retired
    path and is the one that works."""
    try:
        path = urlsplit(url or "").path
    except ValueError:
        return False
    return path.rstrip("/") == _RETIRED_SEARCH_PATH


@dataclasses.dataclass
class _LinkedInSource(BrowserListSource):
    """LinkedIn's search-results page: a lazily-filled column, 25 jobs a page.

    Two differences from `BrowserListSource`, each on its own axis. SCROLLING (`_scroll_step`)
    brings the column's cards into view. PAGINATION (`fetch`) walks `start=` offsets by calling
    `super().fetch` once per page -- a wrapper, never a reimplementation. That distinction is
    the lesson of this class's history: it once overrode `fetch` wholesale and shipped without
    the auth probe, because the registration declared one, so everything READ as covered while
    the copied fetch never evaluated it. Delegating per page keeps the tab lifecycle, the waits,
    the error capture, the landed read and the auth probe all in the one implementation.
    """

    # Pages per search. Small on purpose: each page is a full tab load with its own settle wait
    # and scroll steps, so the bound is politeness towards the board as much as run time. More
    # than one, because the first 25 results were all the old source could ever see.
    pages: int = 2

    def __post_init__(self) -> None:
        super().__post_init__()
        # Fail loudly at construction, `validate_posting_paths`' posture. `bool` is refused
        # before `int` because it subclasses it: `pages=True` would otherwise read as one page.
        # Zero or less would skip every fetch and read as a board with no jobs.
        if isinstance(self.pages, bool) or not isinstance(self.pages, int) or self.pages < 1:
            raise ValueError(
                f"source {self.id}: pages must be a positive int, got {self.pages!r}")

    def _scroll_step(self, cam, tid) -> None:
        cam.evaluate(tid, _COLUMN_SCROLL)

    def fetch(self, ctx, search: Search) -> dict:
        """Read up to `pages` pages of `search`, one `super().fetch` each, de-duplicated by id.

        Page one is the configured URL VERBATIM; later pages differ only in `start=`. The walk
        stops early on a page that adds no job not already seen -- what LinkedIn serves past
        the last real page, what an ignored `start=` looks like, and what an unreadable or
        logged-out page yields (no rows). So a failure on page one costs no further requests.

        The returned envelope is page ONE's (`landed`, `requested`, `auth_missing`,
        `auth_probe_error`) with every page's rows: the redirect, login and auth classifiers
        describe the search as configured, and a later page's `start=` URL is not that. The
        one thing carried over from a later page is its `error`, first-found: a page that could
        not be read is the clearest explanation for a short run, and `detect_drift` discards
        `unreachable` whenever the run still produced rows, so recording it cannot turn a
        productive run into a failure.
        """
        if _is_retired_search_url(search.url):
            # A WARNING, not a refusal, and the choice is load-bearing. Every LinkedIn search
            # written before 2026-09-14 is on this path, so whatever happens here happens to
            # every upgrading install at once. A refusal returns zero rows: for a user whose
            # searches are all old-style that is `drift=zero` on every run, and the source
            # auto-retires within a few runs -- a silent switch-off whose only cause is a URL.
            # Refusing with a manufactured `error` instead classifies as `unreachable`, which
            # is recoverable and so defers retirement for ever: the benign-reason-with-
            # unlimited-life hazard `_explained` (core/health.py) warns about. Running the
            # search keeps health honest, and the rows are still real postings; location is
            # triage's gate (`target_locations`) to apply.
            #
            # A per-run `_log.warning` naming the search is the established per-search channel
            # -- `ingest/engine.py`'s failed-fetch warning and `workinstartups`' rate-limit
            # skip both use it -- and it repeats on every run until the URL is fixed, rather
            # than scrolling away once. It names the LABEL and never echoes the URL: a search
            # URL is keywords plus geography, the reason `validate_search_entry` withholds it.
            _log.warning(
                "linkedin search %r uses LinkedIn's retired /jobs/search/ address. LinkedIn "
                "now redirects it to /jobs/search-results/, drops its location, work-type and "
                "sort filters, and picks a location of its own, so this search is reading "
                "jobs from a place you did not choose. Replace it with a /jobs/search-results/ "
                "URL carrying a geoId, e.g. https://www.linkedin.com/jobs/search-results/"
                "?keywords=<keywords>&geoId=<id> -- choose the location in LinkedIn's job "
                "search and copy geoId from the address bar.", search.label)
        first, rows, seen = None, [], set()
        for page in range(self.pages):
            url = search.url if page == 0 else _page_url(search.url, page)
            raw = super().fetch(ctx, dataclasses.replace(search, url=url))
            if first is None:
                first = raw
            elif raw.get("error"):
                first.setdefault("error", raw["error"])
            added = 0
            cards = raw.get("result")
            for card in (cards if isinstance(cards, list) else []):
                row = _card_row(card)
                if row is None or row["link"] in seen:
                    continue
                seen.add(row["link"])
                rows.append(row)
                added += 1
            # No URL means nothing to put `start=` into; one page is all there is.
            if not added or not search.url:
                break
        return {**first, "result": rows}


# Logged out, LinkedIn still serves a full page of jobs -- in GUEST markup (`base-card` /
# `job-search-card`), with none of the signed-in cards the extractor targets. So the extractor
# returns 0 while the page is manifestly working, which is indistinguishable from "no jobs
# matched" and is exactly what auto-retired this source on 2026-08-15.
#
# Requires BOTH halves: guest cards present AND signed-in cards absent. Guest markup alone
# would fire on a page that renders both during a LinkedIn A/B, and "signed-in cards absent"
# alone would fire on a genuinely empty result set.
#
# The signed-in half is the UNSCOPED componentkey query, deliberately wider than the
# extractor's `role="button"` selector: this probe asks whether ANY signed-in card is on the
# page, and a wider "present" can only make it slower to claim "logged out", never quicker. It
# was keyed on `.artdeco-entity-lockup` until 2026-09-14; left that way, a signed-in
# search-results page (which has none) would read as logged out the moment guest-style cards
# appeared on it.
_AUTH_PROBE = (
    "(()=>document.querySelectorAll("
    + _js_string(f'[componentkey^="{_CARD_KEY_PREFIX}"]')
    + ").length === 0 "
    "&& document.querySelectorAll('.base-card, .job-search-card').length > 0)()"
)

register(_LinkedInSource(
    id="linkedin",
    extractor_js=_JS,
    auth_probe_js=_AUTH_PROBE,
    # Per PAGE: each page is its own base `fetch`, so each gets the full settle wait and every
    # scroll step. No `scroll_amount`: `_scroll_step` scrolls a card into view, not by pixels.
    # `pages` is deliberately NOT restated here: the class default is its one home, and a second
    # copy at registration is what left that default unpinned (a mutant changing it survived).
    wait=4, scrolls=8,
    extra={"job_type": "contract"},
    searches_spec=[
        # `f_TPR` needs `origin` beside it to survive; `f_WT`/`sortBy` are dropped by LinkedIn.
        ('LinkedIn example', 'https://www.linkedin.com/jobs/search-results/?keywords=software%20developer&geoId=102257491&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r604800'),
    ],
))
