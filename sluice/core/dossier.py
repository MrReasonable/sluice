"""Per-lead dossier cache: what the judge needs (JD markdown + Glassdoor rating +
lead snapshot), cached on disk with a TTL so re-runs skip network I/O. The fetcher
(Camofox-backed in production) and clock are injected, so TTL and hit/miss are unit
tested offline. Schema mirrors the legacy schema_version 2 so the existing cached
dossiers are reused as-is."""
import json
import os
import re
from datetime import datetime


def _slug(lead: dict) -> str:
    base = f"{lead.get('company','')}-{lead.get('role','')}".lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:80] or "lead"


def slim(dossier: dict, *, jd_limit: int = 4000) -> dict:
    out = {k: v for k, v in dossier.items() if k != "lead_snapshot"}
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
        return (lead.get("lead_id") or _slug(lead))

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
            "built_at": self.clock().isoformat(),
        }
        os.makedirs(self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dossier, f, ensure_ascii=False)
        return dossier
