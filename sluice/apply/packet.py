"""The application packet: what the browser-assisted step needs to fill a form.
Text is slop-clean (no em dashes). The listing host is best-effort and usually the
board, not the company ATS, which is found during the browser step."""
import json
import os
from urllib.parse import urlparse

_HOST_LABELS = [
    ("linkedin.", "linkedin"), ("indeed.", "indeed"),
    ("greenhouse.io", "greenhouse"), ("ashbyhq.com", "ashby"),
    ("lever.co", "lever"), ("workable.com", "workable"),
    ("icims.com", "icims"), ("teamtailor.com", "teamtailor"),
]
_SKILL = "job-application-workflow"


def listing_host(url):
    netloc = urlparse(url or "").netloc.lower()
    for needle, label in _HOST_LABELS:
        if needle in netloc:
            return label
    return "other"


def build_packet(note, cfg, *, cv_staged):
    fm = note.fm
    url = (fm.get("url") or "").strip().strip('"')
    return {
        "company": fm.get("company", ""),
        "role": fm.get("role", ""),
        "location": fm.get("location", ""),
        "salary": fm.get("salary", ""),
        "url": url,
        "listing_host": listing_host(url),
        "cv_path": os.path.join(cfg.camofox_cv_dir, cfg.neutral_name) if cv_staged else None,
        "skill": _SKILL,
    }


def render_text(p):
    lines = [
        f"APPLICATION PACKET: {p['company']} - {p['role']}",
        f"  location: {p['location'] or 'n/a'}   salary: {p['salary'] or 'n/a'}",
        f"  job url: {p['url'] or 'n/a'}",
        f"  listing host: {p['listing_host']} (best-effort; the real ATS is on the company careers page)",
    ]
    if p["cv_path"]:
        lines.append(f"  CV staged for upload: {p['cv_path']}")
    else:
        lines.append("  CV not staged (preview mode). Stage this lead's CV with apply prep before applying.")
    lines += [
        "  RULES:",
        "    - Never use one-click apply. Go to the company's own ATS.",
        "    - Use first names only. No real full names in third-party forms.",
        "    - Never auto-submit. You review on VNC and click submit.",
        f"  Form-fill technique: skill '{p['skill']}'.",
    ]
    return "\n".join(lines)


def render_json(p):
    return json.dumps(p, ensure_ascii=False)
