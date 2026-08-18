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
    """A job board plugin: what to search, how to fetch results, and how to parse
    them into Leads. `fetch` is the only impure member -- it drives a `Ctx`'s
    browser client; `parse` is pure, tested offline against golden fixtures under
    tests/fixtures/<id>/raw.json.

    OPTIONAL MEMBER -- `company_from_url(self, url: str) -> str | None`. Not
    declared as a required member below, for the identical reason `Store.preflight`
    and `Renderer.precheck` are not: a Protocol member is a REQUIRED member, and the
    whole point of this hook is that a source may omit it.
    `sluice.triage.resolve.resolve_company` (#109) reaches it via
    `getattr(source, "company_from_url", None)` and treats its absence as tier-1
    abstaining for that source -- the same shape those two other optional seam
    members already use.

    Implement it only where the board's real URL shape unambiguously encodes the
    hiring company with a clear delimiter on both ends of the captured slug --
    never a guessed split point. Must never raise: it runs against live,
    hand-maintained scraped URLs on every triage run, so `resolve_company` isolates
    any exception from it and treats that as an abstain rather than letting one
    source's bug on one unanticipated URL shape crash the whole batch.
    """

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


def _sized(value):
    """`value` if it has a length, else an empty list.

    `health_hint` normalises `raw` to a dict, which makes `raw.get(...)` safe and says nothing
    about what comes back. A payload carrying `None` or a scalar under the count key therefore
    raised `TypeError` from `len()` -- inside the very expression written to tolerate a
    malformed payload. Latent: no shipped source emits that shape today.

    `isinstance(list | tuple)`, NOT `hasattr("__len__")`. A STRING has a length, so the
    permissive form counted `{"result": "text"}` as four rows -- swapping a crash for a
    plausible wrong number, which is worse. The value is a list of extracted rows or it is
    not a payload we can count.
    """
    return value if isinstance(value, (list, tuple)) else []


def _first_degraded(rows) -> str | None:
    """The first truthy `degraded` marker among `rows`, or `None`.

    A row-level marker (`_stepstone.py`'s anchor fallback, `reed.py`'s unscoped link
    tier) is DIRECT evidence that the good path did not run this search, which is why
    `detect_drift` ranks it above the inferred `blank` reason. `_sized` already
    guarantees `rows` is a list here -- this is the row-content half, not the payload
    normalisation half."""
    for row in _sized(rows):
        if isinstance(row, dict) and row.get("degraded"):
            return row["degraded"]
    return None


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
    # JS evaluating truthy when the page is showing its LOGGED-OUT face. Opt-in: a source
    # that declares none reports no auth state, because a source cannot be wrong about a
    # measurement it never took. Only sources whose extractor targets authenticated-only
    # markup need it -- for them, "0 rows" and "logged out" are otherwise indistinguishable,
    # which is what retired linkedin/jobserve/indeed on 2026-08-15.
    auth_probe_js: str | None = None

    def searches(self) -> list:
        return [_mk_search(spec) for spec in self.searches_spec]

    def _scroll_step(self, cam, tid) -> None:
        """One scroll step -- one of TWO sanctioned override points for a list-shaped source,
        the other being `parse` (see below). This one is for HOW the page scrolls; `parse` is
        for WHAT a scraped row means.

        Its result is deliberately NOT folded into `errors` below, and that is a judgement
        rather than an oversight. A failed scroll does not produce the unexplained ZERO this
        branch exists to remove: the extractor still runs and reports whatever was visible, so
        the outcome is a LOW count, which `detect_drift` already classifies against the
        source's baseline. Promoting it to `fetch_error` would classify as `unreachable`,
        which is in `_RECOVERABLE` and therefore defers retirement indefinitely -- buying a
        genuinely dead source unlimited time on a benign scroll hiccup, the opposite and
        quieter failure that `_explained`'s docstring warns about. If the tab itself is
        broken, the extractor evaluate errors too and IS recorded.

        A board that virtualizes its results (LinkedIn) must scroll the results PANEL rather
        than the window. That is its sole difference from this class on the SCROLL axis, so
        it is the only thing it gets to change here. Overriding `fetch` wholesale is how the
        LinkedIn subclass silently shipped without the auth probe: the registration declared
        one, so everything READ as covered while the override never evaluated it.

        `parse` is the other axis, for row-level REPAIR rather than scroll mechanics --
        `_NaukrigulfSource.parse` (recovering a company mashed into the title via the listing
        URL's own seam, #151) and `WellfoundSource.parse` (dropping company-profile-card rows
        the extractor's selector lets through, #151) both override it instead of this method,
        PROVIDED they delegate to `super().parse(...)` so `_row_to_lead` and this class's own
        title-non-empty filter still run underneath the repair -- which both do. A `parse`
        override that skips that delegation reimplements row-shaping from scratch and silently
        loses that filter."""
        cam.scroll(tid, self.scroll_amount)

    def fetch(self, ctx: Ctx, search: Search) -> dict:
        cam, sleep = ctx.camofox, getattr(ctx, "sleep", time.sleep)
        tid = cam.create_tab(search.url)
        if not tid:
            return {"result": [], "landed": "", "requested": search.url, "error": "no-tab"}
        # try/finally from the moment the tab EXISTS. `Camofox._api` turns its own failures
        # into `{"error": ...}` rather than raising, but nothing guarantees that of the
        # transport underneath it, of an injected fake, or of `sleep`. And `_run_source`
        # retries on `Exception`, so a raise here does not leak one tab -- it leaks one PER
        # ATTEMPT, and an exhausted Camofox is exactly the outage that retired every source
        # and produced this PR. `core/app.py` already sets the precedent for the doctor probe:
        # "`finally`, not a `close_tab` call repeated on every branch".
        try:
            sleep(self.wait)
            if self.dismiss_js:
                cam.evaluate(tid, self.dismiss_js)
                sleep(0.5)
            for _ in range(self.scrolls):
                self._scroll_step(cam, tid)
                sleep(0.5)
            result = cam.evaluate(tid, self.extractor_js)
            landed = cam.evaluate(tid, "location.href")
            auth_missing, probe_error = self._read_auth_probe(cam, tid)
        finally:
            cam.close_tab(tid)
        rows = result.get("result") if isinstance(result, dict) else None
        # `Camofox._api` captures every failure as {"error": ...} rather than raising, so an
        # evaluate that failed is indistinguishable from one that returned nothing unless we
        # look. Record it: a browser that could not be read is the single clearest explanation
        # for a zero, and discarding it is what let one outage retire every source at once.
        errors = [r.get("error") for r in (result, landed) if isinstance(r, dict) and r.get("error")]
        landed_result = landed.get("result") if isinstance(landed, dict) else None
        # NOT `or search.url` when the evaluate failed: defaulting landed to the requested URL
        # manufactures "no redirect", which is the one signal that would have explained this.
        landed_url = landed_result or ("" if errors else (search.url or ""))
        out = {"result": rows or [], "landed": landed_url, "requested": search.url,
               "auth_missing": auth_missing}
        if errors:
            out["error"] = errors[0]
        if probe_error:
            out["auth_probe_error"] = probe_error
        return out

    def _read_auth_probe(self, cam, tid) -> tuple:
        """`(auth_missing, probe_error)`, evaluated on the SAME tab as the extractor.

        Same tab deliberately: a second fetch could land elsewhere (redirect, A/B split, rate
        limit), and the probe would then describe a different page than the one that yielded
        nothing.

        Only a clean truthy result counts as logged-out. A probe that errored tells us nothing,
        and claiming "logged out" off a broken probe would suppress the retirement of a
        genuinely dead source -- the opposite failure, and a quieter one. But NOT claiming it
        and NOT saying the probe broke are two different decisions: a probe that silently stops
        working (LinkedIn renames a class, a CSP blocks the expression) disables this whole
        guard while every dashboard stays green. So the error comes back too."""
        if not self.auth_probe_js:
            return False, None
        probe = cam.evaluate(tid, self.auth_probe_js)
        if not isinstance(probe, dict) or probe.get("error"):
            err = probe.get("error") if isinstance(probe, dict) else "probe returned a non-dict"
            return False, err
        return bool(probe.get("result")), None

    def parse(self, raw: dict, search: Search) -> list:
        return [
            _row_to_lead(self.id, search, row, self.extra)
            for row in raw.get("result", [])
            if isinstance(row, dict) and (row.get("title") or "").strip()
        ]

    def health_hint(self, raw: dict) -> dict:
        # Normalise ONCE. The previous shape guarded the count with `isinstance`, then read
        # `raw.get("landed")` unguarded on the next line, then checked `isinstance` again --
        # so a non-dict `raw` raised `AttributeError` on the host lines and the later guard
        # was unreachable. Three guards that add up to no tolerance at all.
        raw = raw if isinstance(raw, dict) else {}
        hint = {
            # `_sized` not `len(...)` directly: normalising `raw` guarantees a DICT,
            # not that the value under the payload key is sized. `{"result": None}`
            # raised TypeError straight past the tolerance the line above exists for.
            "count": len(_sized(raw.get("result"))),
            "landed_host": _host(raw.get("landed", "")),
            "requested_host": _host(raw.get("requested", "")),
            "markers": {},
        }
        # Present only when they actually fired, so `detect_drift` sees keys it can classify
        # on and an ordinary source's signals stay byte-identical to before.
        #
        # `fetch_error` is the load-bearing one: `fetch` has always recorded "no-tab" and this
        # method has always dropped it, so a Camofox outage reached the classifier as an
        # unexplained zero and retired every source at once. A fresh dict built from three
        # keys is exactly how a fourth goes missing.
        if raw.get("error"):
            hint["fetch_error"] = raw["error"]
        if raw.get("auth_missing"):
            hint["auth"] = "missing"
        # Reported but NOT an explanation: a broken probe must not defer retirement (that
        # would keep a genuinely dead source alive), yet it has to be visible or the guard
        # silently disables itself.
        if raw.get("auth_probe_error"):
            hint["auth_probe_error"] = raw["auth_probe_error"]
        # A row the extractor's own fallback stamped is direct evidence of degradation --
        # see `_first_degraded`. Checked on the RAW rows, not the parsed leads: a row-level
        # marker survives even a row `parse` later drops (a blank title), which a signal
        # computed post-parse would miss.
        degraded = _first_degraded(raw.get("result"))
        if degraded:
            hint["degraded"] = degraded
        return hint


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
        # Same try/finally as BrowserListSource, for the same reason. `health_hint` was fixed
        # in one implementation and not the other twice on this branch; a resource leak is no
        # different, so both close together.
        jobs, seen, errors = [], set(), []
        landed = None
        try:
            sleep(self.wait)
            for _ in range(self.max_jobs):
                read = cam.evaluate(tid, self.read_js)
                if isinstance(read, dict) and read.get("error"):
                    # `Camofox._api` captures every failure as `{"error": ...}` rather than
                    # raising, so a failed evaluate is INDISTINGUISHABLE from a page with no
                    # jobs unless we look. This class did not look: it broke out of the loop
                    # and returned an empty `jobs` with no `error`, so `health_hint` had
                    # nothing to propagate, `detect_drift` saw a bare `zero`, and three such
                    # runs retired the source. That is the exact failure this branch removes
                    # -- and it was removed for the list-shaped sources only.
                    errors.append(read["error"])
                    break
                job = read.get("result") if isinstance(read, dict) else None
                if not isinstance(job, dict):
                    break
                sig = job.get("link") or job.get("title")
                if not sig or sig in seen:  # "all caught up" / repeat
                    break
                seen.add(sig)
                jobs.append(job)
                advanced = cam.evaluate(tid, _advance_js(self.advance_selector))
                if isinstance(advanced, dict) and advanced.get("error"):
                    errors.append(advanced["error"])
                    break
                if not (isinstance(advanced, dict) and advanced.get("result")):
                    break
                sleep(0.5)
            landed = cam.evaluate(tid, "location.href")
        finally:
            cam.close_tab(tid)
        if isinstance(landed, dict) and landed.get("error"):
            errors.append(landed["error"])
        landed_result = landed.get("result") if isinstance(landed, dict) else None
        # READ, not assumed. This returned `landed: search.url` unconditionally, which asserts
        # `requested_host == landed_host` -- a manufactured "no redirect" on a run where
        # nothing was read, and redirect is one of the few signals that would have explained
        # the zero. Same reasoning as the base class, which stopped doing this.
        out = {"jobs": jobs, "landed": landed_result or ("" if errors else (search.url or "")),
               "requested": search.url}
        if errors:
            out["error"] = errors[0]
        return out

    def parse(self, raw: dict, search: Search) -> list:
        return [
            _row_to_lead(self.id, search, job, self.extra)
            for job in raw.get("jobs", [])
            if isinstance(job, dict) and (job.get("title") or "").strip()
        ]

    def health_hint(self, raw: dict) -> dict:
        raw = raw if isinstance(raw, dict) else {}   # see BrowserListSource.health_hint
        hint = {
            # `_sized` not `len(...)` directly: normalising `raw` guarantees a DICT,
            # not that the value under the payload key is sized. `{"jobs": None}`
            # raised TypeError straight past the tolerance the line above exists for.
            "count": len(_sized(raw.get("jobs"))),
            "landed_host": _host(raw.get("landed", "")),
            "requested_host": _host(raw.get("requested", "")),
            "markers": {},
        }
        # The same propagation as BrowserListSource, for the same reason. `health_hint` is a
        # PROTOCOL member with two implementations, and the first fix landed on one of them --
        # so a Camofox outage still reached the classifier as an unexplained zero here and
        # retired this source after three runs. Fixing the instance and not the class is how
        # the identical bug survives in the file next door; the conformance test is
        # parameterised over both classes so a third implementation cannot repeat it.
        if raw.get("error"):
            hint["fetch_error"] = raw["error"]
        degraded = _first_degraded(raw.get("jobs"))
        if degraded:
            hint["degraded"] = degraded
        return hint


def _advance_js(selector: str) -> str:
    """JS that clicks the advance control and reports whether it existed."""
    return (
        "(()=>{const b=document.querySelector(" + repr(selector) + ");"
        "if(!b)return false;b.click();return true;})()"
    )
