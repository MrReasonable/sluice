"""Per-source health: run history, a drift detector, and an auto-retire rule.

Source drift (a site moving/renaming/DOM-changing) is a dominant scanner failure mode, but
NOT the only one -- on 2026-08-15 a single wrong browser-profile setting produced eight-plus
zero-yield runs across three sources, and the module was built on the assumption that could
not happen. The engine records each source's yield + signals here, asks detect_drift whether
this run looks wrong relative to the source's own baseline, and retires a source that has
produced nothing, FOR NO REASON WE CAN NAME, several runs running.

Two things to understand about `should_retire` before changing it:

WHAT RETIREMENT ACTUALLY DOES. `ingest/engine.py` sets `source.enabled = False` on the
in-memory registry, and that is never persisted -- `cli._save_disabled` is reached only from
`ingest enable`/`disable`, and `_is_enabled` re-reads `getattr(src, "enabled", True)`, which
is True again in the next process. So retirement does NOT stop a source running tomorrow. Its
real value is as the only CUMULATIVE, DURABLE signal the system has: the `RETIRE` flag in
`ingest list-sources --health`. Anything that suppresses retirement must therefore replace
that signal, or it trades a loud wrong answer for a quiet permanent one -- see
`explained_streak`, which exists for exactly that reason.

RECOVERABLE VS NOT. Suppressing retirement is right only when an operator action brings the
source back (an expired login, a rate-limit, a browser server that is down). It is wrong when
the site has MOVED: the evidence never changes, and this repo's entire auto-retire history is
that case (`sources/hired.py`, `sources/hackajob.py`, both retired by hand after a redirect).
"""
import json
import os
from statistics import median

from sluice.core.paths import resolve


class HealthStore:
    """JSON-backed per-source run history. One file, whole-object rewrite -
    the data is tiny (a handful of sources, last ~30 runs each)."""

    _KEEP = 30  # cap history per source

    def __init__(self, path: str | None = None):
        # Same pattern as SeenDb: an explicit path wins, otherwise resolution decides
        # (env var, then the per-system state root). This is the ONE place that lives
        # -- app.py's ingest() and cli.py's cmd_health/cmd_list_sources all construct
        # HealthStore() bare and get the same path, so the file `ingest` writes is
        # always the file `health` reads.
        #
        # `path or resolve(...)` and not the other order: an explicit constructor
        # argument must beat the environment, or every `HealthStore(str(tmp_path/...))`
        # in the suite would retarget a developer's real file and stay green while
        # doing it. It also means `resolve` is not called at all when a caller names a
        # path, so an explicit caller can never trip the migration warning.
        self.path = path or resolve(env_var="SLUICE_HEALTH", config_value="",
                                    kind="state", name="sluice_health.json")
        self._data = self._load()

    def _load(self) -> dict:
        # SILENT on any failure, and that is the right tier for this file (see
        # docs/ARCHITECTURE.md): run history is DERIVED telemetry that rebuilds itself
        # on the next run, so a wrong answer costs a drift-detection baseline rather
        # than data. `ingest/engine.py` rules the same way on the write side. Do not
        # copy this into a store whose empty read gets written back as truth.
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def record(self, source_id: str, count: int, signals: dict | None = None) -> None:
        runs = self._data.setdefault(source_id, {"runs": []})["runs"]
        runs.append({"count": count, "signals": signals or {}})
        self._data[source_id]["runs"] = runs[-self._KEEP:]
        self._save()

    def counts(self, source_id: str, n: int = 7) -> list:
        runs = self._data.get(source_id, {}).get("runs", [])
        return [r["count"] for r in runs[-n:]]

    def baseline(self, source_id: str) -> float:
        """Median of the last 7 run counts - robust to the odd bumper/empty run."""
        counts = self.counts(source_id, 7)
        return float(median(counts)) if counts else 0.0

    def should_retire(self, source_id: str, threshold: int = 3) -> bool:
        """True once the last `threshold` runs are all dead -- produced nothing for a reason
        we cannot name, or one no operator action will undo. See `_is_dead`."""
        runs = self._data.get(source_id, {}).get("runs", [])
        if len(runs) < threshold:
            return False
        return all(_is_dead(r) for r in runs[-threshold:])

    def explained_streak(self, source_id: str) -> tuple:
        """`(reason, n)` for a source stuck on the SAME named failure -- `(None, 0)` otherwise.

        This is what makes suppressing retirement safe. Retirement's real value was never
        stopping the source (it does not persist -- see the module docstring); it was being the
        one CUMULATIVE signal an operator could see days later. Exempting explained failures
        without this would swap a loud wrong answer for a silent permanent one: the 2026-08-15
        sources would sit at `baseline=0` with no RETIRE flag and nothing saying why.

        Reads the signals `record` already persists, so it costs no new state. Counts back only
        while the reason is UNCHANGED: a source that flips auth -> redirect -> auth is a
        different, noisier problem than one wedged on the same thing since Tuesday."""
        runs = self._data.get(source_id, {}).get("runs", [])
        reason, n = None, 0
        for run in reversed(runs):
            if run.get("count", 0) != 0:
                break
            this = _explained(run.get("signals", {}) or {})
            if this is None or (reason is not None and this != reason):
                break
            reason, n = this, n + 1
        return (reason, n) if reason else (None, 0)


# The signal keys that CARRY an explanation, as opposed to describing the run. `ingest/engine`
# makes exactly these sticky across a source's searches, because a reason found on search 1
# must not be overwritten by search 3's honest zero. Lives here, beside `_explained`, so the
# producer's notion of "this key explains something" cannot drift from the classifier's.
EXPLAINING_SIGNALS = ("fetch_error", "blocked", "auth", "auth_probe_error")


def _dewww(host: str) -> str:
    """`host` with a leading `www.` removed -- ONE prefix strip, not a registrable-domain
    parse. Named for what it does: an earlier `_registrable` promised eTLD+1, which would have
    misled the next reader to pass it `jobs.80000hours.org` expecting `80000hours.org`.

    An apex -> `www` redirect is normal, permanent and benign, and nine registered sources
    request an apex host (cord.com, hired.com, remoteok.com, wellfound.com, ...). Treating
    that hop as a "redirect" would report drift on every single run for nine sources that are
    working perfectly."""
    return host[4:] if host.startswith("www.") else host


def _explained(signals: dict) -> str | None:
    """Why this run looks wrong, when we can say -- `None` when we cannot.

    THE one definition of "we know what went wrong", shared by `detect_drift` (which reports
    it) and `_is_dead` (which defers retirement). Two copies would drift. Note this is NOT a
    past disagreement being repaired: before 2026-08-15 neither function had the concept at
    all, so they agreed by having the same blind spot. Centralising it is what stops the next
    reason being added to one and not the other.

    An `error` is deliberately NOT an explanation. It says the fetch blew up, not that the
    page told us something -- there is nothing on the site to go and fix, so an erroring
    source that also yielded nothing should still retire.

    CAUTION when adding a reason here. Every entry is both a report AND a deferral of
    retirement, so a reason that fires benignly buys a dead source time it has not earned --
    see `_dewww` for the case that nearly happened. `should_retire`'s bound is the
    backstop, not a licence to be loose here."""
    # FIRST, because it explains every other signal's absence: if the browser never gave us a
    # tab, or the page evaluate failed, we did not look at the site at all. Discarding this was
    # how a single Camofox outage could record a bare `zero` for all ~23 sources at once and
    # retire the lot -- the clearest "could not read" in the system, thrown away one layer
    # before the classifier saw it.
    if signals.get("fetch_error"):
        return "unreachable"
    requested, landed = signals.get("requested_host"), signals.get("landed_host")
    if requested and landed and _dewww(requested) != _dewww(landed):
        return "redirect"
    if signals.get("blocked"):
        return "blocked"
    if signals.get("auth"):
        return "auth"
    return None


# Explanations an OPERATOR ACTION can undo, and which therefore defer retirement. The
# distinction is whether the run is evidence or a corpse: an expired login or a rate-limit
# comes back once someone fixes the config, so killing the source deletes the very signal that
# would prompt the fix. A `redirect` does not come back -- the board has MOVED, and the
# evidence never changes no matter how many more times we look.
#
# This repo's entire auto-retire history is the redirect case: `sources/hired.py` (hired.com
# 302s to lhh.com after the LHH acquisition) and `sources/hackajob.py` (hackajob.co/search ->
# a 404 page). Both were retired BY HAND. Treating redirect as recoverable would mean the
# automatic rule could never reach that conclusion again, and a relocated board would burn a
# browser slot on every run forever -- which is the exact waste `should_retire` exists to stop.
_RECOVERABLE = ("auth", "blocked", "unreachable")


def _is_dead(run: dict) -> bool:
    """Dead = produced nothing AND we cannot say why. An outright error is not a "why", so an
    errored zero IS dead -- but an errored run that still returned rows is not. Nor is a
    reason no operator action would undo: a relocated board is dead however well we can
    explain it.

    A source we could not READ because of a fixable condition is BROKEN, not dead, and
    retiring it deletes the evidence: it stops running, so it stops reporting the auth/block
    failure, so nobody ever learns what to fix. That is precisely how a wrong `CAMOFOX_USER`
    cost three heavyweight sources for eight-plus runs -- the retirement looked like the
    system working.

    A relocated board is the opposite case, and `detect_drift` still REPORTS `redirect` for
    it, so nothing is lost from the run report by retiring it."""
    signals = run.get("signals", {}) or {}
    # No `error` short-circuit. It would be redundant AND wrong. Redundant because `error` is
    # deliberately not an explanation, so a zero-yield error already falls through to dead
    # below. Wrong because `_run_source` REASSIGNS `signals` per search rather than merging,
    # so a source whose LAST search errored while earlier ones succeeded carries both a
    # positive count and an error -- and a source that just returned rows is not dead.
    return run.get("count", 0) == 0 and _explained(signals) not in _RECOVERABLE


def detect_drift(source_id: str, count: int, signals: dict | None, baseline: float) -> str | None:
    """Classify this run against the source's baseline. Returns the reason, or None if healthy.

    Precedence: an EXPLAINED failure (redirect > blocked > auth) outranks a bare `zero`, and
    `zero` outranks `drop`. The explanation is checked FIRST on purpose. Testing `count == 0`
    first -- as this did until 2026-08-15 -- discards the redirect/blocked signals the caller
    already gathered and collapses every distinct failure into the one word that cannot be
    acted on.

    Two separate justifications, kept separate on purpose: the precedence reversal is right on
    general principle, but it is NOT what would have rescued the 2026-08-15 LinkedIn case.
    Logged-out LinkedIn serves guest markup at the SAME url, so there was no redirect signal
    to discard -- only the new `auth` probe surfaces that one."""
    signals = signals or {}
    reason = _explained(signals)
    if count == 0:
        # The explanation replaces "zero", which is the one classification nobody can act on.
        return reason or "zero"
    # The run PRODUCED rows, so it is not a failure to explain. Only the two reasons that stay
    # interesting alongside a successful fetch survive here -- both pre-existing behaviour.
    # `auth`/`unreachable` deliberately do not: gating them on count would otherwise let a
    # 200-row run report drift and fire a notification off one search's stale signal.
    if reason in ("redirect", "blocked"):
        return reason
    if baseline and count < 0.4 * baseline:
        return "drop"
    return None
