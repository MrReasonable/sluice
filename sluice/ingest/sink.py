"""Sinks: where deduped, relevance-passed leads land.

VaultSink stamps first_seen/last_seen, upserts each lead into the Obsidian vault
(never clobbering status). upsert returns one of created/updated/merged/refused/
merged_away/merged_away_unproven; created, updated, merged, and merged_away all mean a
note now EXISTS (merged_away's is archived under _merged/), so only those are recorded
in seen.db. `refused` (a decline with TWO causes the store cannot tell apart: #5's
name collision -- every candidate a note proven DIFFERENT -- or #1's ambiguous identity,
one candidate resolving to SEVERAL notes at once), `merged_away_unproven` (a #81
suppression on a match weaker than a url -- a location-token overlap, or an inconclusive
comparison; see the allowlist below for why it must never be recorded), and `skipped` (a
#24 OSError write failure) stay OUT of seen.db so the next run retries or re-reports them,
rather than aborting the run. JsonSink emits one JSON
object per line - for `--sink json` and the legacy-diff tool. Both return sparse count
dicts (merged/refused/merged_away/merged_away_unproven keys appear only when non-zero).
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
                # created | updated | merged | refused | merged_away | merged_away_unproven
                outcome = self.vault.upsert(lead)
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome in ("created", "updated", "merged", "merged_away"):
                    # Allowlist over "a note now exists", stated positively so an unknown
                    # outcome fails safe: refused (and the OSError->skipped below) stay OUT
                    # of `recorded` -> never enter seen.db -> retried next run. See #5.
                    #
                    # `merged_away` (#81) qualifies: the note exists, ARCHIVED under
                    # _merged/, and the incoming lead carries the SAME non-empty url as
                    # that note -- url-PROVEN identity -- so recording it self-heals the
                    # dedup set and the suppression happens once rather than on every run.
                    # `merged_away_unproven` does NOT and must never be added -- it is a
                    # suppression on weaker evidence (a location-only match, or UNKNOWN),
                    # and seen.db has no removal path (load/save only), so recording it
                    # would make engine.py filter that key forever with no note anywhere.
                    # A same-company/title/location RE-POST carrying a brand-new url is
                    # exactly that case, and it is a real job.
                    recorded.append(lead)
            except OSError as e:
                # A lead the store cannot write (name too long on an odd FS,
                # permissions, disk full) must not sink the batch or the run. Count
                # it, log it, and leave it OUT of `recorded` so it never enters
                # seen.db and is retried next run. See #24. OSError is the filesystem
                # store's failure mode; a future SQLite store would raise sqlite3.Error,
                # so this catch would need widening when that store arrives.
                counts["skipped"] += 1
                # "skipped", not "refused": this is a physical WRITE failure (OSError), a
                # different condition from #5's deliberate name-collision `refused`. Keep the
                # log wording distinct so grepping for one does not surface the other.
                _log.warning("vault skipped lead %r (write failed): %s", lead.dedup_key, e)
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
