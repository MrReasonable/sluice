"""Escape the City (escapethecity.org) - permanent, mission-driven EM roles.
RETIRED 2026-07-07: the /opportunities?q= search now 302s to /search/jobs and the
`a[href*="/opportunities/"]` extractor matches nothing (same drift the legacy perm
scanner has). Registered disabled; needs a new URL + selector to revive.
Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('a[href*="/opportunities/"]').forEach(a=>{const t=a.querySelector('h2,h3,strong')?.textContent?.trim()||a.textContent.trim();const p=a.closest('div,li,article');const co=p?.querySelector('.company, .org-name, small')?.textContent?.trim()||'';if(t&&t.length>5)r.push({title:t,company:co,location:'',link:a.href,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="escape_city",
    enabled=False,  # retired 2026-07-07: /opportunities search 302s to /search/jobs
    extractor_js=_JS,
    wait=3, scrolls=1, scroll_amount=600,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Escape the City example', 'https://www.escapethecity.org/opportunities?q=software+developer'),
    ],
))
