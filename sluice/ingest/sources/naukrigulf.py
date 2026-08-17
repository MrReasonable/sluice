"""NaukriGulf (naukrigulf.com), UAE. 2026-07-07: DOM rebound - job cards are
`div.ng-box.srp-tuple` with `a.info-position` (title + detail link) and
`a.info-org` (company). The old `div[class*="job"]`/`a.title` selectors matched
nothing (there is no "job" in the card class), so re-selected from a live probe.

2026-08-17 (#151): when the org-name node is absent, the extractor's `co`
capture comes back empty and the card's title text runs role and company
together with no separator (e.g. "BankerMassive Dynamic"). NaukriGulf's own
listing URL still carries the split, though: every card links to
`.../<role-slug>-jobs-in-<city-slug>-in-<company-slug>-<id>`, so the URL is an
independent, structured second source for exactly the field the DOM lost.
`_NaukrigulfSource.parse` recovers (title, company) from that seam before
delegating to the base class, which is what still runs `_row_to_lead` and
`_demash_company` over the rewritten rows.
"""
import re
from urllib.parse import urlparse

from sluice.core.log import get_logger
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_log = get_logger("ingest.naukrigulf")

_JS = """(()=>{const r=[];document.querySelectorAll('div.srp-tuple').forEach(c=>{const a=c.querySelector('a.info-position');const cl=a?a.cloneNode(true):null;cl?.querySelectorAll('.info-org,[class*="org"]').forEach(e=>e.remove());const t=cl?.textContent?.trim()||'';const co=(c.querySelector('a.info-org')||a?.querySelector('.info-org,[class*="org"]'))?.textContent?.trim()||'';const lo=c.querySelector('.info-loc,[class*="location"],[class*="loc"]')?.textContent?.trim()||'';const ln=a?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:lo||'UAE',link:ln,salary:''})});return r.slice(0,25)})()"""


def _slug(text: str) -> str:
    """Mirrors Lead.slug's character class (core/leads.py) closely enough for URL comparison."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_SEAM_RE = re.compile(r"-jobs-in-")


def _url_has_seam(url: str) -> bool:
    """True when the listing URL carries the "...-jobs-in-..." split seam that
    `_split_mashed_title` requires before it will even attempt a split. Factored out (rather
    than re-deriving the regex at each call site) so `_recover` can tell "no seam at all"
    (nothing proven, nothing to warn about) apart from "seam present but no valid split"
    (the URL proved a split existed and recovery still came up blank -- the case worth an
    operator's attention, #151 fixup)."""
    return bool(_SEAM_RE.search(urlparse(url or "").path))


# Hyphen, slash, en dash, em dash: explicit separators a title can already carry at the
# cut point. The mashing signature this function recovers is the ABSENCE of any separator
# ("BankerAcme"); a title like "Banker-Acme" already has one, and the boundary check must
# reject it the same way it rejects a space -- otherwise "Banker-" is accepted as the role,
# with the delimiter left dangling on it (CodeRabbit finding, PR #152 on 2926e99).
_BOUNDARY_DELIMITERS = "-/–—"


def _split_mashed_title(title: str, url: str) -> tuple[str, str] | None:
    """(role, company) recovered from a title where the board mashed them together with no
    separator, proven by the listing URL's own "...-jobs-in-<city>-in-<company>-..." seam.
    None means abstain -- the title is left exactly as scraped."""
    path = urlparse(url or "").path
    candidates = [path[:m.start()].lstrip("/") for m in _SEAM_RE.finditer(path)]
    if not candidates:
        return None
    best = None
    for i in range(1, len(title)):
        if _slug(title[:i]) not in candidates:
            continue
        if title[i - 1].isspace() or title[i - 1] in _BOUNDARY_DELIMITERS:
            continue                      # the mashing signature: no separator at the cut
        if not title[i].isupper():        # company opens a fresh capitalised token
            continue
        best = i
    if best is None:
        return None
    role, company = title[:best].strip(), title[best:].strip()
    if not role or not company:
        return None
    return role, company


def _recover(row: dict) -> dict:
    if (row.get("company") or "").strip():
        return row   # never touch a populated field
    url = row.get("link") or row.get("url") or ""
    split = _split_mashed_title(row.get("title") or "", url)
    if split is None:
        # Warn on the URL's own signal, not a guess from the title's visual shape. The
        # original `[a-z][A-Z]` heuristic missed the exact abstain case this recovery is
        # built to leave alone: "Site Banker – Example Ventures" mashes role and company
        # across an en dash, which has no camelCase-letter adjacency anywhere, even though
        # the URL seam is right there proving a split exists. "The URL proved a split existed
        # here and we still couldn't recover it" is the operator-relevant condition; it
        # covers that case and the original camelCase one uniformly, and it is exactly what
        # `_split_mashed_title` itself checked before it even tried.
        if _url_has_seam(url):
            _log.warning(
                "naukrigulf: URL %r proved a role/company split exists but recovery could "
                "not locate it in title %r; lead kept as-is", url, row.get("title"))
        return row
    role, company = split
    return {**row, "title": role, "company": company}


class _NaukrigulfSource(BrowserListSource):
    def parse(self, raw, search):
        raw = raw if isinstance(raw, dict) else {}
        rows = [_recover(r) if isinstance(r, dict) else r for r in (raw.get("result") or [])]
        return super().parse({**raw, "result": rows}, search)


register(_NaukrigulfSource(
    id="naukrigulf",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('NaukriGulf example', 'https://www.naukrigulf.com/software-developer-jobs'),
    ],
))
