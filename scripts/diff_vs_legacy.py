#!/usr/bin/env python3
"""Compare sluice vs legacy-scanner output by normalized-URL dedup key.

    diff_vs_legacy.py <sluice.jsonl> <legacy.jsonl>

Both files are JSONL, one lead per line. sluice keys on `url`, legacy on `link`
(either falls back to the other, then to a title|company hash for url-less leads
like Google). Reports:
  * sluice-only  - sluice found it, legacy missed it (sluice winning)
  * legacy-only   - legacy found it, sluice missed it (the COVERAGE GAP that must
                    be ~empty before cutover)
  * per-source counts (note: source ids differ between systems, e.g. eighty_k vs
    80k, google vs google_jobs - the key comparison is source-agnostic).
"""
import json
import sys
from collections import defaultdict

from sluice.core.leads import _norm_url


def _key(lead: dict) -> str:
    url = lead.get("url") or lead.get("link") or ""
    if url:
        return _norm_url(url)
    title = (lead.get("title") or "").lower().strip()
    company = (lead.get("company") or "").lower().strip()
    return f"h:{title}|{company}" if title else ""


def load_leads(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def diff(sluice_leads: list, legacy_leads: list) -> dict:
    jp = {_key(l) for l in sluice_leads if _key(l)}
    lg = {_key(l) for l in legacy_leads if _key(l)}
    per_source = defaultdict(lambda: {"sluice": 0, "legacy": 0})
    for l in sluice_leads:
        per_source[l.get("source", "?")]["sluice"] += 1
    for l in legacy_leads:
        per_source[l.get("source", "?")]["legacy"] += 1
    return {
        "sluice_total": len(jp),
        "legacy_total": len(lg),
        "overlap": len(jp & lg),
        "sluice_only": sorted(jp - lg),
        "legacy_only": sorted(lg - jp),
        "per_source": {k: dict(v) for k, v in per_source.items()},
    }


def format_report(d: dict) -> str:
    lines = [
        f"sluice: {d['sluice_total']} unique | legacy: {d['legacy_total']} unique "
        f"| overlap: {d['overlap']}",
        f"sluice-only (sluice found, legacy missed): {len(d['sluice_only'])}",
        f"legacy-only (COVERAGE GAP): {len(d['legacy_only'])}",
        "",
        f"{'source':18} {'sluice':>8} {'legacy':>8}",
    ]
    for src in sorted(d["per_source"]):
        c = d["per_source"][src]
        flag = "  <-- fewer than legacy" if c["legacy"] > c["sluice"] else ""
        lines.append(f"{src:18} {c['sluice']:>8} {c['legacy']:>8}{flag}")
    if d["legacy_only"]:
        lines += ["", "legacy-only keys (investigate before cutover):"]
        lines += [f"  {k}" for k in d["legacy_only"][:25]]
        if len(d["legacy_only"]) > 25:
            lines.append(f"  ... and {len(d['legacy_only']) - 25} more")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: diff_vs_legacy.py <sluice.jsonl> <legacy.jsonl>", file=sys.stderr)
        return 2
    print(format_report(diff(load_leads(argv[0]), load_leads(argv[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
