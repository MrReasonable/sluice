"""Per-lead dossier cache: what the judge needs (JD markdown + Glassdoor rating +
lead snapshot), cached on disk with a TTL so re-runs skip network I/O. The fetcher
(Camofox-backed in production) and clock are injected, so TTL and hit/miss are unit
tested offline. Schema mirrors the legacy schema_version 2 so the existing cached
dossiers are reused as-is."""
import hashlib
import json
import os
import re
from datetime import datetime


def _slug(lead: dict) -> str:
    base = f"{lead.get('company','')}-{lead.get('role','')}".lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:80] or "lead"


def slim(dossier: dict, *, jd_limit: int = 4000) -> dict:
    # page_title/structured_data (#109) are resolution-only fields, never judge-relevant --
    # structured_data especially can run several KB on some boards, so excluding it here
    # (not at storage time in get_or_build, which resolve.py's _from_dossier still needs
    # to read directly off the cached dict) is what keeps it out of every judge prompt.
    out = {k: v for k, v in dossier.items()
          if k not in ("lead_snapshot", "page_title", "structured_data")}
    jd = dict(out.get("jd") or {})
    if "markdown" in jd:
        jd["markdown"] = jd["markdown"][:jd_limit]
    out["jd"] = jd
    return out


class DossierCache:
    def __init__(self, dir: str, ttl_days: int, fetcher, clock=datetime.now):
        self.dir = dir
        self.ttl_days = ttl_days
        self.fetcher = fetcher
        self.clock = clock

    def cache_key(self, lead: dict) -> str:
        """`lead_id` first, then a stable hash of `url` (#109) -- url does not change
        across the classify-pass company mutation this feature performs, so the
        classify-pass resolution fetch and the later enrich-pass judge fetch land on
        the SAME cache entry instead of double-fetching. Falls back to the
        company/role slug only when neither is available (e.g. a #23 Google lead
        with no url)."""
        lead_id = lead.get("lead_id")
        if lead_id:
            return lead_id
        url = lead.get("url")
        if url:
            return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]
        return _slug(lead)

    def _path(self, lead: dict) -> str:
        return os.path.join(self.dir, f"{self.cache_key(lead)}.json")

    def _fresh(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            built = json.loads(open(path, encoding="utf-8").read()).get("built_at")
            age = self.clock() - datetime.fromisoformat(built)
            return age.days < self.ttl_days
        except (OSError, ValueError, TypeError):
            return False

    def get_or_build(self, lead: dict) -> dict:
        path = self._path(lead)
        if self._fresh(path):
            return json.loads(open(path, encoding="utf-8").read())
        enrich = self.fetcher(lead)
        dossier = {
            "schema_version": 2,
            "lead_id": self.cache_key(lead),
            "company": lead.get("company", ""),
            "position": lead.get("role", ""),
            "location": lead.get("location", ""),
            "role_type": lead.get("role_type", ""),
            "lead_snapshot": dict(lead),
            "jd": enrich.get("jd", {}),
            "glassdoor": enrich.get("glassdoor", {}),
            # #109 tier-2 company resolution reads these two off a fresh dossier
            # directly; defaulting to "" here (not None) is what lets an OLD cached
            # dossier missing them entirely still parse via a plain .get(...) or "".
            "page_title": enrich.get("page_title", ""),
            "structured_data": enrich.get("structured_data", ""),
            "built_at": self.clock().isoformat(),
        }
        os.makedirs(self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dossier, f, ensure_ascii=False)
        return dossier
