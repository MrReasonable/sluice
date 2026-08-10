"""Wellfound (wellfound.com, ex-AngelList) - startup EM roles (permanent).
Declarative extractor JS + an example search (override via config).

`company_from_url` (#109) is a tier-1, free URL-pattern extractor: a Wellfound
job/company URL carries the hiring company as a `/company/<slug>/...` path
segment, delimited by the literal `/company/` segment on one side and the next
`/` (or end of string) on the other -- unambiguous, so this abstains (returns
None) for any URL shape that does not carry that segment, rather than guess a
split point. Verified against a real `job-sluice ingest test-source wellfound
--raw` capture, not committed from the illustrative pattern alone: real company
cards link to a BARE `/company/<slug>` with no trailing path at all (the
end-of-string boundary), and real job-posting cards link to
`/jobs/<id>-<title-slug>` with no `/company/` segment whatsoever, so they
correctly abstain rather than needing a split.
"""
import re

from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('a[href*="/company/"], a[href*="/jobs/"]').forEach(a=>{const t=a.querySelector('h2,h3,div[class*="title"]')?.textContent?.trim()||a.textContent.trim();const p=a.closest('div,li');const co=p?.querySelector('div[class*="company"], span[class*="company"]')?.textContent?.trim()||'';if(t&&t.length>3&&!r.find(x=>x.title===t))r.push({title:t,company:co,location:'',link:a.href,salary:''})});return r.slice(0,15)})()"""

_COMPANY_URL_RE = re.compile(r"^https?://(?:www\.)?wellfound\.com/company/([a-z0-9-]+)")


class WellfoundSource(BrowserListSource):
    def company_from_url(self, url: str) -> str | None:
        m = _COMPANY_URL_RE.match(url or "")
        if not m:
            return None
        return m.group(1).replace("-", " ").title() or None


register(WellfoundSource(
    id="wellfound",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Wellfound example', 'https://wellfound.com/role/r/software-engineer'),
    ],
))
