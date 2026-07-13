"""Sinks: where deduped, relevance-passed leads land.

VaultSink stamps first_seen/last_seen, upserts each lead into the Obsidian vault
(never clobbering status), then records it in seen.db so the next run dedups it.
JsonSink emits one JSON object per line - for `--sink json` and the legacy-diff
tool. Both return {created, updated, skipped}.
"""
import json
from dataclasses import asdict
from datetime import date


def _today() -> str:
    return date.today().isoformat()


class VaultSink:
    def __init__(self, vault, seendb, *, today=None):
        self.vault = vault
        self.seendb = seendb
        self._today = today or _today

    def write(self, leads) -> dict:
        counts = {"created": 0, "updated": 0, "skipped": 0}
        recorded = []
        for lead in leads:
            stamp = self._today()
            if not lead.first_seen:
                lead.first_seen = stamp
            lead.last_seen = stamp
            outcome = self.vault.upsert(lead)  # "created" | "updated"
            counts[outcome] = counts.get(outcome, 0) + 1
            recorded.append(lead)
        if recorded:
            # Record everything the sink touched so the next run dedups it - some
            # updated leads (pre-existing vault notes) may not yet be in seen.db.
            self.seendb.save(recorded)
        return counts


class JsonSink:
    def __init__(self, stream):
        self.stream = stream

    def write(self, leads) -> dict:
        counts = {"created": 0, "updated": 0, "skipped": 0}
        for lead in leads:
            self.stream.write(json.dumps(asdict(lead)) + "\n")
            counts["created"] += 1
        return counts
