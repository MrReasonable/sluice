# sluice/cv/bundle.py
"""Closed, verified-only CV source bundle. The composer, the validate gate, and the
strip step all share the short company-prefixed [id] codes assigned here. The FULL
verified set is emitted (JD keywords order/emphasise, never exclude) so the
employer-completeness gate is always satisfiable from cited entries."""
import re


def _prefix(company: str, prefix_map: dict) -> str:
    """Two-uppercase-letter company prefix. Coerces ANY source (a prefix_map
    override or the derived fallback) to exactly two A-Z letters so the citation
    code always matches the strip regex and can never leak into the rendered PDF."""
    raw = prefix_map.get(company) or company
    letters = re.sub(r"[^A-Za-z]", "", raw).upper()
    return (letters[:2] or "XX").ljust(2, "X")


def assign_codes(entries: list[dict], prefix_map: dict) -> list[dict]:
    """Attach a stable [id] (e.g. NC1, SF2) to each entry, sequenced per company prefix."""
    seq: dict[str, int] = {}
    out = []
    for e in entries:
        p = _prefix(e.get("company", ""), prefix_map)
        seq[p] = seq.get(p, 0) + 1
        out.append({**e, "id": f"{p}{seq[p]}"})
    return out


def rank(entries: list[dict], jd_keywords: list[str]) -> list[dict]:
    kw = [k.lower() for k in jd_keywords]

    def score(e):
        hay = f"{e.get('best_for','')} {e.get('category','')} {e.get('title','')}".lower()
        return sum(k in hay for k in kw)

    return sorted(entries, key=score, reverse=True)


def build_bundle(entries, baseline, negatives, jd_keywords, prefix_map) -> dict:
    ranked = rank(entries, jd_keywords)
    return {"baseline": baseline, "entries": assign_codes(ranked, prefix_map),
            "negatives": list(negatives)}


def render_bundle(bundle: dict) -> str:
    lines = ["=== BASELINE CV (authoritative for dates/employers/certs) ===",
             bundle["baseline"], "",
             "=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ==="]
    for e in bundle["entries"]:
        lines.append(f"[{e['id']}] ({e.get('company','')}) {e.get('title','')} "
                     f"| metrics={e.get('metrics','')}")
        if e.get("body"):
            lines.append(e["body"])
        lines.append("")
    lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
    lines += [f"- {n}" for n in bundle["negatives"]]
    return "\n".join(lines)
