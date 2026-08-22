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
    def __init__(self, dir: str, ttl_days: int, fetcher, clock=datetime.now,
                 min_jd_chars: int = 0):
        self.dir = dir
        self.ttl_days = ttl_days
        self.fetcher = fetcher
        self.clock = clock
        # 0 = the near-empty band is OFF, which is the SHIPPED default (#169, spec
        # decision 3): a character count is a judgement about what counts as a real
        # posting, and this repo does not ship one uninvited -- see sluice.yaml.example's
        # `lead_ttl_days` for the same rule stated at length. An EMPTY jd is different in
        # kind: it is a fact, so `jd_arrived` refuses it at every floor including 0.
        # The constructor default is 0 rather than the config default so the bare
        # `DossierCache(dir, ttl, fetcher=...)` constructions across the suite keep
        # today's behaviour exactly.
        self.min_jd_chars = min_jd_chars

    def jd_arrived(self, dossier: dict) -> bool:
        """Did this fetch actually produce a job description?

        The ONE owner of that judgement. `get_or_build` asks it to decide whether to
        PERSIST, and every caller asks it to decide what a miss means for them -- one
        function, two uses, so there is no second copy of the rule to drift (#169).

        A predicate rather than a marker key in the returned dict, deliberately: a marker
        would ride `slim()` into the judge prompt, since `slim` excludes `lead_snapshot`,
        `page_title` and `structured_data` by NAME and would not exclude a new key by
        accident.

        Degrades to False rather than raising on a malformed `jd`, matching what
        `triage/resolve.py:_text` already does with this same field -- a dossier that
        cannot answer the question has not produced a JD either.
        """
        jd = dossier.get("jd")
        markdown = jd.get("markdown") if isinstance(jd, dict) else None
        if not isinstance(markdown, str):
            return False
        text = markdown.strip()
        if not text:
            return False            # a FACT, refused at every floor
        return len(text) >= self.min_jd_chars

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
            cached = json.loads(open(path, encoding="utf-8").read())
            age = self.clock() - datetime.fromisoformat(cached.get("built_at"))
        except (OSError, ValueError, TypeError):
            return False
        if age.days >= self.ttl_days:
            return False
        # Content as well as age (#169). An entry written BEFORE this existed whose JD
        # never arrived is fresh by the clock and useless by content; without this check
        # the fix reaches an existing deployment's cache only after a full TTL, and the
        # issue's manual "delete the sub-200-character entries" step stays manual.
        #
        # At the shipped `min_jd_chars: 0` this re-fetches the EMPTY subset only -- the
        # short-but-not-empty entries need a configured floor, which is the accepted cost
        # recorded in the spec's decision 3 and surfaced by `doctor` (Task 8).
        #
        # If the refetch also fails, nothing is written and this file lingers, inert,
        # re-read and re-rejected each run. That is the intended retry. There is
        # deliberately NO cleanup pass: deleting on a read would make a read a write,
        # which is the exact shape that disarmed the #81 relocation notice.
        return self.jd_arrived(cached)

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
        # Do NOT persist a fetch that produced no JD (#169). Caching one makes every
        # later run serve the failure for the whole TTL: triage judges a lead on a
        # document nobody read, returns "unjudgeable" (a `research` verdict), and the
        # nightly `--status new,research` run re-selects it and pays for the same
        # non-answer until the entry expires. Not writing costs one refetch per run and
        # ends the loop. The FRESHLY FETCHED dossier is still returned, never the
        # rejected cached one, so the caller can answer `jd_arrived` on what it holds.
        if self.jd_arrived(dossier):
            os.makedirs(self.dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dossier, f, ensure_ascii=False)
        return dossier
