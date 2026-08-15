"""Per-source health: run history, a drift detector, and an auto-retire rule.

Source drift (a site moving/renaming/DOM-changing) is the dominant scanner
failure mode - far more common than session expiry. The engine records each
source's yield + signals here, asks detect_drift whether this run looks wrong
relative to the source's own baseline, and retires a source that has produced
nothing for several runs in a row so it stops wasting a browser slot.
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
        """True once the last `threshold` runs are all dead (zero yield or error)."""
        runs = self._data.get(source_id, {}).get("runs", [])
        if len(runs) < threshold:
            return False
        return all(_is_dead(r) for r in runs[-threshold:])


def _explained(signals: dict) -> str | None:
    """Why this run looks wrong, when we can say -- `None` when we cannot.

    THE one definition of "we know what went wrong", shared by `detect_drift` (which reports
    it) and `_is_dead` (which decides whether to retire). Two copies would drift, and the
    2026-08-15 incident is what a disagreement costs: the drift line said `zero` while the
    retire rule silently concluded `dead`.

    An `error` is deliberately NOT an explanation. It says the fetch blew up, not that the
    page told us something -- there is nothing on the site to go and fix, so an erroring
    source should still retire."""
    requested, landed = signals.get("requested_host"), signals.get("landed_host")
    if requested and landed and requested != landed:
        return "redirect"
    if signals.get("blocked"):
        return "blocked"
    if signals.get("auth"):
        return "auth"
    return None


def _is_dead(run: dict) -> bool:
    """Dead = produced nothing AND we cannot say why (or it errored outright).

    A source we could not READ is BROKEN, not dead, and retiring it deletes the evidence:
    it stops running, so it stops reporting the redirect/block/auth failure, so nobody ever
    learns what to fix. That is precisely how a wrong `CAMOFOX_USER` cost three heavyweight
    sources for eight-plus runs -- the retirement looked like the system working."""
    signals = run.get("signals", {}) or {}
    if signals.get("error"):
        return True
    return run.get("count", 0) == 0 and _explained(signals) is None


def detect_drift(source_id: str, count: int, signals: dict | None, baseline: float) -> str | None:
    """Classify this run against the source's baseline. Returns the reason, or None if healthy.

    Precedence: an EXPLAINED failure (redirect > blocked > auth) outranks a bare `zero`, and
    `zero` outranks `drop`. The explanation is checked FIRST on purpose. Testing `count == 0`
    first -- as this did until 2026-08-15 -- discards the redirect/blocked signals the caller
    already gathered and collapses every distinct failure into the one word that cannot be
    acted on. The whole value of capturing requested/landed host is lost at exactly the
    moment it would have paid."""
    signals = signals or {}
    reason = _explained(signals)
    if reason:
        return reason
    if count == 0:
        return "zero"
    if baseline and count < 0.4 * baseline:
        return "drop"
    return None
