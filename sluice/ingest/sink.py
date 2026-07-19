"""Sinks: where deduped, relevance-passed leads land.

VaultSink stamps first_seen/last_seen, upserts each lead into the Obsidian vault
(never clobbering status). upsert returns created/updated/merged/refused; only the
first three mean a note now EXISTS, so only those are recorded in seen.db. `refused`
(a #5 name-collision decline) and `skipped` (a #24 OSError write failure) stay OUT of
seen.db so the next run retries them, rather than aborting the run. JsonSink emits one
JSON object per line - for `--sink json` and the legacy-diff tool. Both return sparse
count dicts (merged/refused keys appear only when non-zero).
"""
import json
from dataclasses import asdict
from datetime import date

from sluice.core.log import get_logger

_log = get_logger("ingest.sink")


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
            try:
                outcome = self.vault.upsert(lead)  # created | updated | merged | refused
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome in ("created", "updated", "merged"):
                    # Allowlist over "a note now exists", stated positively so an unknown
                    # outcome fails safe: refused (and the OSError->skipped below) stay OUT
                    # of `recorded` -> never enter seen.db -> retried next run. See #5.
                    recorded.append(lead)
            except OSError as e:
                # A lead the store cannot write (name too long on an odd FS,
                # permissions, disk full) must not sink the batch or the run. Count
                # it, log it, and leave it OUT of `recorded` so it never enters
                # seen.db and is retried next run. See #24. OSError is the filesystem
                # store's failure mode; a future SQLite store would raise sqlite3.Error,
                # so this catch would need widening when that store arrives.
                counts["skipped"] += 1
                _log.warning("vault refused lead %r: %s", lead.dedup_key, e)
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
