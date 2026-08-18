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
    login_wall,
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
    """company_rate/link_rate over PARSED leads, not raw extractor rows -- what actually
    reaches the vault, which is what incident 1's harm was actually made of. Measured
    (#156): naukrigulf's raw company_rate is 0.385 (a URL-seam repair inside `parse`
    recovers most of it), which alone gives healthy sources a 62% chance of a false `blank`
    alarm per 30-run window; the same source measured on its PARSED leads is 0.962, giving
    0%.

    `_run_source` calls this ONCE per run, over every search's leads combined -- never
    per search. A per-search call would reintroduce the identical "last search wins" hole
    `EXPLAINING_SIGNALS`'s stickiness already exists to close for `fetch_error`/`blocked`/
    `auth`: a source whose first search fell back or measured low and whose last search
    came back clean would silently lose the signal for the whole run.

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


# Drift reasons that WITHHOLD a source's leads from the sink for this run, rather than
# merely reporting them (#156). Incident 1's real cost was ~185 blank-companied notes
# burned into `seen.db`, which has no removal path -- reporting alone leaves that hole
# open for however long it takes a human to read the digest. Deliberately narrower than
# the full drift vocabulary, and each exclusion for its OWN reason, not one blanket claim:
#   - `auth`/`unreachable`/`zero` are structurally count==0-only -- `detect_drift`'s
#     count>0 allowlist does not include them, so there is nothing to withhold.
#   - `blocked`'s one shipped producer (`workinstartups.py`'s HEAD-precheck) always
#     returns zero rows when it fires, so it is count==0 in practice today -- but NOT
#     structurally: `detect_drift` permits `blocked` to survive a positive count for a
#     future source, and this set would need revisiting if one ships.
#   - `redirect` genuinely CAN carry a positive count (a cross-host redirect landing on a
#     page that still parses rows -- `test_offdomain_redirect_flags` proves this at
#     count=5), and is deliberately left OUT anyway: it predates this PR, and withholding
#     on it is a real, separate scope decision for a follow-up, not a side effect of
#     shipping fallback/login/blank.
#   - `drop` is a bare row-count comparison, the LOWEST-confidence signal here --
#     suppressing a real day's leads on a false `drop` is a worse failure than a late
#     report.
# `fallback`/`login`/`blank` are the three NEW, content-inspected reasons this PR adds.
BREAKER_REASONS = frozenset({"fallback", "login", "blank"})


@dataclass
class SourceResult:
    source_id: str
    status: str = "ok"          # "ok" | "error"
    fetched: int = 0            # rows parsed, before dedup/relevance
    fresh: int = 0              # leads FOUND this run (found, not necessarily written)
    withheld: int = 0           # of `fresh`, how many were withheld -- see BREAKER_REASONS
    # "zero" | "drop" | "blank" | "fallback" | "login" | "redirect" | "blocked" | "auth" |
    # "unreachable" | None. Keep in step with core/health.py's `_explained`/`detect_drift` --
    # this comment is the only place the vocabulary is enumerated, so a new reason that
    # misses it is invisible.
    drift: str | None = None
    retired: bool = False
    error: str | None = None
    # Set only when `_update_health` itself raised (a HealthStore read/write failure, not a
    # fetch failure) -- distinct from `drift`, because `drift` staying None here means
    # "could not classify," not "classified as healthy." Consumed by `run()`'s withhold
    # check below: BREAKER_REASONS exists to stop a rotted run's leads reaching the vault,
    # and a health-pipeline failure is the one case that can't even say whether the leads
    # ARE rotted -- writing them through on "couldn't tell" would defeat the breaker via
    # its own failure mode rather than its absence.
    health_error: str | None = None


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

        # `_update_health` records health FIRST regardless -- run history must reflect what
        # was actually fetched, or the next run's baseline/high-water desync from reality.
        # Only the WRITE is suppressed here, never the measurement -- EXCEPT when the
        # measurement itself failed (`health_error`, review-found): `result.drift` then
        # stays None, which is indistinguishable from "classified healthy" to the check
        # below unless `health_error` is also consulted. Failing OPEN there would write a
        # run through un-vetted specifically because the vetting broke -- the exact silent-
        # write-of-unclassified-rot shape BREAKER_REASONS exists to close, reached through
        # its own blind spot instead of around it.
        if fresh and (result.drift in BREAKER_REASONS or result.health_error):
            result.withheld = len(fresh)
            # Un-claim these keys from the RUN-LOCAL dedup set too, not only from the
            # persisted seen.db counterpart. `_run_source` added them to `seen_keys` while
            # parsing, before this source's drift was known -- so leaving them claimed would
            # make a withheld source silently suppress a HEALTHY sibling source's identical
            # lead later in this same run (the same job posted on two boards), which is
            # exactly the silent-lead-loss failure #156 exists to close, just moved one layer
            # over. Never touches the persisted `seen.db`: `seen.load()` was read once at the
            # top of this run, and a withheld lead is simply never passed to `sink.write()`,
            # so `seendb.save()` never runs for it and it never enters seen.db -- the next
            # run re-fetches and re-evaluates it from scratch, no special-case recovery path
            # needed, the same discipline `sink.py`'s own `refused`/`skipped`/
            # `merged_away_unproven` outcomes already follow.
            seen_keys.difference_update(lead.dedup_key for lead in fresh)
        elif fresh:
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
    explained = {}    # explanation keys seen on ANY search; see the loop below
    degraded = None   # the first `degraded` marker seen on ANY search; sticky, like `explained`
    run_leads = []    # every PARSED lead from every search this run; see _lead_rates below
    login_paths = None  # the first search's OWN (requested_path, landed_path) pair that
                        # `login_wall` confirmed; sticky, frozen as ONE atomic pair -- see below
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
        run_leads.extend(leads)
        # `degraded` (#156) is sticky across searches, for the identical reason
        # `_EXPLAINING_SIGNALS` is below: `signals` is reassigned per search, so without
        # this a source whose FIRST search fell back to a degraded extractor path and whose
        # LAST search came back clean would report no `fallback` at all -- the marker
        # silently overwritten by a later, unrelated search. First-found wins, matching
        # `explained.setdefault` below; a search that never degrades never touches it.
        if degraded is None and hint.get("degraded"):
            degraded = hint["degraded"]
        # A login wall is sticky too (#156 review follow-up), for the identical reason
        # `degraded` is: without it, a source whose FIRST search lands on a login wall and
        # whose LAST search is clean reports no `login` at all for the run. Unlike hosts
        # (still last-search-wins, see the comment below), this freezes the WHOLE pair from
        # the search that actually confirmed it -- `login_wall` is called here, once, on
        # that search's OWN atomic (requested_path, landed_path), so the frozen pair can
        # never mix halves from two different searches the way persisting the raw fields
        # independently would.
        if login_paths is None and login_wall(hint.get("requested_path", ""),
                                              hint.get("landed_path", "")):
            login_paths = (hint.get("requested_path", ""), hint.get("landed_path", ""))
        # An EXPLANATION is sticky across searches; counts and hosts are not. Hosts stay
        # last-search-wins deliberately rather than by oversight: they are a matched pair,
        # and with the `{**explained, **signals}` merge below, persisting either host
        # independently could pair one search's requested half with another's landed half
        # and invent a redirect that never happened. See `EXPLAINING_SIGNALS` in
        # `core/health.py` for the full asymmetry. `redirect` therefore keeps the same
        # multi-search blind spot `login` had until this fix -- accepted for `redirect`
        # since it predates #156 and widening it is a separate scope decision.
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
    # keys a later search dropped. `company_rate`/`link_rate` are computed ONCE here, over
    # every PARSED lead from every search this run -- not per search and reassigned, which
    # would have the identical "last search wins" hole `explained` exists to close, and
    # would also apply `_RATE_ROW_FLOOR` per search rather than to the run's real total (a
    # 2-search source returning 5 leads each clears the floor in aggregate but not alone).
    final = {**explained, **signals, **_lead_rates(run_leads)}
    if degraded:
        final["degraded"] = degraded
    # Overwrites whatever the FINAL search's own (possibly clean) path pair left in
    # `signals` -- the frozen pair from the search that actually confirmed a login wall
    # wins, matching `degraded`'s override just above. `_explained` re-derives `login`
    # from these two keys unchanged, so nothing downstream needs to know this pair may
    # not be the last search's own.
    if login_paths is not None:
        final["requested_path"], final["landed_path"] = login_paths
    return total, final


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
    except Exception as e:
        # Best-effort -- never RAISE and abort the run over this -- but no longer silent:
        # `health_error` lets the caller in `run()` withhold this source's leads rather
        # than write them through unclassified (review-found; see the field's own comment
        # on `SourceResult`). `result.drift` is deliberately left at its None default here
        # rather than set to a synthetic reason: `drift`'s vocabulary is the enumerated set
        # `detect_drift` can actually return, and this failure never reached that function.
        result.health_error = str(e)
        _log.warning("health update failed for %s: %s", source.id, e)
