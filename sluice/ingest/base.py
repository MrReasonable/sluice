"""The Source contract and the two base classes that cover almost every board.

A Source splits impure I/O (`fetch`, which drives the browser) from a pure
transform (`parse`, raw dict -> list[Lead]) so parsers are tested offline against
golden fixtures with no Camofox. `BrowserListSource` covers scroll-a-list boards;
`CarouselSource` covers one-job-at-a-time carousels (WTTJ/Otta). Anything weirder
subclasses / duck-types `Source` directly.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from sluice.core.leads import Lead

HealthSignals = dict


@dataclass
class Search:
    label: str
    url: str | None = None
    params: dict | None = None


@dataclass
class Ctx:
    """What a source needs to run: the browser client, the loaded config, and an
    injectable sleep so tests don't actually wait for page settle."""
    camofox: object
    config: object = None
    # None means "nothing to inject, give me the real one" -- the same tolerance
    # VaultSink(today=None) already has. Without it a caller holding an optional
    # sleep must build a conditional kwargs dict, and the obvious tidy-up of that
    # (`sleep=self._sleep`) passes None straight through: the suite stays green
    # while every real ingest run dies on the first `ctx.sleep(wait)`. Cheaper to
    # make the value safe here than to guard every construction site.
    sleep: Callable | None = None

    def __post_init__(self):
        if self.sleep is None:
            self.sleep = time.sleep


class Source(Protocol):
    id: str
    enabled: bool
    kind: str

    def searches(self) -> list: ...
    def fetch(self, ctx: Ctx, search: Search) -> dict: ...
    def parse(self, raw: dict, search: Search) -> list: ...
    def health_hint(self, raw: dict) -> dict: ...


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _mk_search(spec) -> Search:
    """A searches_spec entry is (label, url) or (label, url, params) - the optional
    params carry per-search metadata (e.g. {"job_type": "perm"}) so the one engine
    covers perm + contract just by varying search terms/params, not code."""
    label, url = spec[0], spec[1]
    params = spec[2] if len(spec) > 2 else None
    return Search(label=label, url=url, params=params)


def searches_for(source, config=None) -> list:
    """The searches a source should run: a per-source config override
    (`sources.<id>.searches`) if the operator set one, else the source's built-in
    example searches. Config-driving these keeps a user's personal search list out
    of the code. Override entries use the same [label, url, params?] shape as a
    built-in searches_spec entry."""
    if config is not None:
        try:
            override = getattr(config.source(source.id), "searches", None)
        except Exception:
            override = None
        if override:
            return [_mk_search(spec) for spec in override]
    return list(source.searches())


def _demash_company(company: str, location: str) -> str:
    """Some boards (Indeed) render company and location in one DOM node with no
    separator, so the extractor captures e.g. 'EniLondon' with location 'London'.
    Strip the location suffix ONLY when it is jammed on with no separating space
    (the mashing signature) and something is left; never a legitimate trailing
    token like 'Capital One UK'."""
    if location and len(company) > len(location) and company.endswith(location):
        boundary = company[: len(company) - len(location)]
        if boundary and not boundary[-1].isspace():
            return boundary.strip()
    return company


def _row_to_lead(source: str, search: Search, row: dict, extra: dict | None) -> Lead:
    """Map an extractor row {title, company?, location?, link, salary?} to a Lead.
    Source-level `extra` sets defaults; the search's own params override them (so
    a perm search on a contract-default source still tags the lead job_type=perm)."""
    location = (row.get("location") or "").strip()
    company = _demash_company((row.get("company") or "").strip(), location)
    lead = Lead(
        source=source,
        search=search.label,
        title=(row.get("title") or "").strip(),
        company=company,
        location=location,
        salary=(row.get("salary") or "").strip(),
        url=row.get("link") or row.get("url") or "",
    )
    for key, value in {**(extra or {}), **(search.params or {})}.items():
        setattr(lead, key, value)
    return lead


@dataclass
class BrowserListSource:
    """A board that renders a scrollable list; one extractor JS returns all rows."""
    id: str
    searches_spec: list           # [(label, url), ...]
    extractor_js: str
    kind: str = "browser"
    enabled: bool = True
    wait: float = 3
    scrolls: int = 2
    scroll_amount: int = 800
    dismiss_js: str | None = None
    extra: dict | None = None

    def searches(self) -> list:
        return [_mk_search(spec) for spec in self.searches_spec]

    def fetch(self, ctx: Ctx, search: Search) -> dict:
        cam, sleep = ctx.camofox, getattr(ctx, "sleep", time.sleep)
        tid = cam.create_tab(search.url)
        if not tid:
            return {"result": [], "landed": "", "requested": search.url, "error": "no-tab"}
        sleep(self.wait)
        if self.dismiss_js:
            cam.evaluate(tid, self.dismiss_js)
            sleep(0.5)
        for _ in range(self.scrolls):
            cam.scroll(tid, self.scroll_amount)
            sleep(0.5)
        result = cam.evaluate(tid, self.extractor_js)
        landed = cam.evaluate(tid, "location.href")
        cam.close_tab(tid)
        rows = result.get("result") if isinstance(result, dict) else None
        landed_url = (landed.get("result") if isinstance(landed, dict) else None) or search.url or ""
        return {"result": rows or [], "landed": landed_url, "requested": search.url}

    def parse(self, raw: dict, search: Search) -> list:
        return [
            _row_to_lead(self.id, search, row, self.extra)
            for row in raw.get("result", [])
            if isinstance(row, dict) and (row.get("title") or "").strip()
        ]

    def health_hint(self, raw: dict) -> dict:
        return {
            "count": len(raw.get("result", []) if isinstance(raw, dict) else []),
            "landed_host": _host(raw.get("landed", "")),
            "requested_host": _host(raw.get("requested", "")),
            "markers": {},
        }


@dataclass
class CarouselSource:
    """A one-job-at-a-time carousel (WTTJ/Otta): read the visible job, click the
    advance button, repeat until it stops yielding new jobs. Advancing CONSUMES
    the job, so we stop as soon as a read repeats or the advance button is gone."""
    id: str
    read_js: str
    advance_selector: str
    searches_spec: list
    kind: str = "carousel"
    enabled: bool = True
    wait: float = 3
    max_jobs: int = 40
    extra: dict | None = None

    def searches(self) -> list:
        return [_mk_search(spec) for spec in self.searches_spec]

    def fetch(self, ctx: Ctx, search: Search) -> dict:
        cam, sleep = ctx.camofox, getattr(ctx, "sleep", time.sleep)
        tid = cam.create_tab(search.url)
        if not tid:
            return {"jobs": [], "landed": "", "requested": search.url, "error": "no-tab"}
        sleep(self.wait)
        jobs, seen = [], set()
        for _ in range(self.max_jobs):
            read = cam.evaluate(tid, self.read_js)
            job = read.get("result") if isinstance(read, dict) else None
            if not isinstance(job, dict):
                break
            sig = job.get("link") or job.get("title")
            if not sig or sig in seen:  # "all caught up" / repeat
                break
            seen.add(sig)
            jobs.append(job)
            advanced = cam.evaluate(tid, _advance_js(self.advance_selector))
            if not (isinstance(advanced, dict) and advanced.get("result")):
                break
            sleep(0.5)
        cam.close_tab(tid)
        return {"jobs": jobs, "landed": search.url, "requested": search.url}

    def parse(self, raw: dict, search: Search) -> list:
        return [
            _row_to_lead(self.id, search, job, self.extra)
            for job in raw.get("jobs", [])
            if isinstance(job, dict) and (job.get("title") or "").strip()
        ]

    def health_hint(self, raw: dict) -> dict:
        return {
            "count": len(raw.get("jobs", []) if isinstance(raw, dict) else []),
            "landed_host": _host(raw.get("landed", "")),
            "requested_host": _host(raw.get("requested", "")),
            "markers": {},
        }


def _advance_js(selector: str) -> str:
    """JS that clicks the advance control and reports whether it existed."""
    return (
        "(()=>{const b=document.querySelector(" + repr(selector) + ");"
        "if(!b)return false;b.click();return true;})()"
    )
