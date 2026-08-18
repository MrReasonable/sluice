"""The ingestion orchestrator.

run() drives every source through fetch (wrapped in a hard timeout + retry so a
slow or flaky site can't stall the whole run), parse, dedup, and the relevance
gate, then writes the fresh leads to the sink. Every source is isolated: one that
raises is recorded as an error and never aborts the others. Per source it records
health, classifies drift against the source's own baseline, and flags auto-retire.
"""
from dataclasses import dataclass, field

from sluice.core.health import (
    EXPLAINING_SIGNALS as _EXPLAINING_SIGNALS,
    RATE_SIGNALS as _RATE_SIGNALS,
    detect_drift,
)
from sluice.core.log import get_logger
from sluice.core.relevance import is_relevant
from sluice.core.resilience import run_with_timeout, with_retry
from sluice.ingest.base import searches_for

_log = get_logger("engine")

# Below this many PARSED leads (not raw rows -- see `_lead_rates`), no rate is computed at
# all: a single comma-less title on a small carousel read can swing a 1-2 row source's rate
# from 0 to 1, which is noise, not signal. Measured (#156): a floor-less version false-
# alarmed 40-74% of wttj's healthy 30-run windows; at this floor, ~0-2%.
_RATE_ROW_FLOOR = 8


def _lead_rates(leads) -> dict:
    """company_rate/link_rate over a search's PARSED leads, not raw extractor rows -- what
    actually reaches the vault, which is what incident 1's harm was actually made of.
    Measured (#156): naukrigulf's raw company_rate is 0.385 (a URL-seam repair inside
    `parse` recovers most of it), which alone gives healthy sources a 62% chance of a false
    `blank` alarm per 30-run window; the same source measured on its PARSED leads is 0.962,
    giving 0%.

    Returns {} below `_RATE_ROW_FLOOR` -- the row-floor gate, applied here rather than in
    `detect_drift`, because "no rate was computed" and "a rate of exactly 0.0" must stay
    distinguishable at every layer, the same discipline `_row_signals`-shaped helpers
    elsewhere in this codebase already follow for a count>0-only signal."""
    if len(leads) < _RATE_ROW_FLOOR:
        return {}
    return {
        "company_rate": sum(1 for lead in leads if lead.company) / len(leads),
        "link_rate": sum(1 for lead in leads if lead.url) / len(leads),
    }


@dataclass
class SourceResult:
    source_id: str
    status: str = "ok"          # "ok" | "error"
    fetched: int = 0            # rows parsed, before dedup/relevance
    fresh: int = 0              # leads handed to the sink
    # "zero" | "drop" | "blank" | "fallback" | "login" | "redirect" | "blocked" | "auth" |
    # "unreachable" | None. Keep in step with core/health.py's `_explained`/`detect_drift` --
    # this comment is the only place the vocabulary is enumerated, so a new reason that
    # misses it is invisible.
    drift: str | None = None
    retired: bool = False
    error: str | None = None


@dataclass
class RunReport:
    sources: list = field(default_factory=list)  # list[SourceResult]
    written: dict = field(default_factory=lambda: {"created": 0, "updated": 0, "skipped": 0})

    @property
    def degraded(self) -> list:
        """(source_id, reason) for every source that drifted this run."""
        return [(r.source_id, r.drift) for r in self.sources if r.drift]


def run(sources, ctx, sink, seen, health, *, fetch_timeout=60, retries=3):
    report = RunReport()
    seen_keys = set(seen.load()) if hasattr(seen, "load") else set(seen)
    for source in sources:
        result = SourceResult(source_id=source.id)
        fresh: list = []
        try:
            count, signals = _run_source(
                source, ctx, seen_keys, fresh, result, fetch_timeout, retries
            )
        except Exception as e:  # belt-and-suspenders; searches are already isolated
            result.status, result.error = "error", str(e)
            _log.warning("source %s failed hard: %s", source.id, e)
            count, signals = 0, {"error": str(e)}

        _update_health(source, result, health, count, signals)

        if fresh:
            written = sink.write(fresh)
            for key, value in written.items():
                report.written[key] = report.written.get(key, 0) + value
        result.fresh = len(fresh)
        report.sources.append(result)
    return report


def _run_source(source, ctx, seen_keys, fresh, result, fetch_timeout, retries):
    """Run all of a source's searches, appending fresh (new + relevant) leads to
    `fresh`. Returns (count, signals) for health/drift. Marks result.status=error
    only if EVERY search failed to fetch."""
    searches = list(searches_for(source, getattr(ctx, "config", None)))
    total, signals, ok, last_error = 0, {}, 0, None
    explained = {}   # explanation keys seen on ANY search; see the loop below
    for search in searches:
        try:
            raw = run_with_timeout(
                lambda s=search: with_retry(
                    lambda: source.fetch(ctx, s), tries=retries, on=(Exception,)
                ),
                fetch_timeout,
            )
        except Exception as e:
            last_error = str(e)
            signals = {"error": str(e)}
            _log.warning("fetch failed for %s/%s: %s", source.id, search.label, e)
            continue
        ok += 1
        hint = source.health_hint(raw)
        total += hint.get("count", 0)
        signals = {k: v for k, v in hint.items() if k != "markers"}
        leads = source.parse(raw, search)
        # `company_rate`/`link_rate` (#156), computed here rather than in `health_hint`: the
        # rate must be measured on what `parse` actually recovers (naukrigulf's `parse`
        # repairs a company mashed into the title via the listing URL's own seam), not on
        # the raw payload `health_hint` sees, or the signal reports on an intermediate the
        # operator never sees rather than on what reaches the vault.
        signals.update(_lead_rates(leads))
        # An EXPLANATION is sticky across searches; counts, hosts and paths are not. Hosts and
        # paths are excluded deliberately rather than overlooked: each is a matched pair, and
        # with the `{**explained, **signals}` merge below, persisting either independently
        # could pair one search's requested half with another's landed half and invent a
        # redirect or a login wall that never happened. See `EXPLAINING_SIGNALS` in
        # `core/health.py` for the full asymmetry.
        #
        # `signals` is reassigned per search, so without this a source whose first search came
        # back logged-out and whose last returned an honest zero reports a bare `zero` -- the
        # explanation is silently overwritten by a later search, and the source retires. The
        # shipped sources use one search each, but `sources.<id>.searches` is the documented
        # way to configure a real list, so the mechanism is weakest on exactly the setup the
        # docs steer people toward.
        for key in _EXPLAINING_SIGNALS:
            if signals.get(key):
                explained.setdefault(key, signals[key])
        for lead in leads:
            key = lead.dedup_key
            if key in seen_keys or not is_relevant(lead.title, ctx.config):
                continue
            seen_keys.add(key)  # de-dup within the run too, across searches/sources
            fresh.append(lead)
    result.fetched = total
    if searches and ok == 0:
        result.status, result.error = "error", last_error
    # `signals` last so the final search's count/hosts still win; `explained` only supplies
    # keys a later search dropped.
    return total, {**explained, **signals}


def _update_health(source, result, health, count, signals):
    try:
        baseline = health.baseline(source.id)
        # Read BEFORE `record()`, same as `baseline` -- so this run's own low rate never
        # contaminates the high-water it is being compared against, and a first-ever run
        # sees {}/None for every key (the health-store analogue of empty-config-abstains).
        rate_highs = health.rate_highs(source.id)
        rate_priors = {key: health.prior_rate(source.id, key) for key in _RATE_SIGNALS}
        health.record(source.id, count, signals)
        result.drift = detect_drift(source.id, count, signals, baseline,
                                    rate_highs=rate_highs, rate_priors=rate_priors)
        if health.should_retire(source.id):
            result.retired = True
            source.enabled = False
    except Exception as e:  # health is best-effort; never fail a run over it
        _log.warning("health update failed for %s: %s", source.id, e)
