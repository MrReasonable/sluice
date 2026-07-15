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


class HealthStore:
    """JSON-backed per-source run history. One file, whole-object rewrite -
    the data is tiny (a handful of sources, last ~30 runs each)."""

    _KEEP = 30  # cap history per source

    def __init__(self, path: str | None = None):
        # Same pattern as SeenDb: an explicit path wins, otherwise fall back to the
        # env var, otherwise the on-disk default. This is the ONE place that default
        # lives -- app.py's ingest() and cli.py's cmd_health/cmd_list_sources all
        # construct HealthStore() bare and get the same path, so the file `ingest`
        # writes is always the file `health` reads.
        self.path = path or os.environ.get("SLUICE_HEALTH", "./sluice_health.json")
        self._data = self._load()

    def _load(self) -> dict:
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


def _is_dead(run: dict) -> bool:
    return run.get("count", 0) == 0 or bool(run.get("signals", {}).get("error"))


def detect_drift(source_id: str, count: int, signals: dict | None, baseline: float) -> str | None:
    """Classify this run against the source's baseline. Precedence:
    zero > redirect > blocked > drop. Returns the reason, or None if healthy."""
    signals = signals or {}
    if count == 0:
        return "zero"
    requested, landed = signals.get("requested_host"), signals.get("landed_host")
    if requested and landed and requested != landed:
        return "redirect"
    if signals.get("blocked"):
        return "blocked"
    if baseline and count < 0.4 * baseline:
        return "drop"
    return None
