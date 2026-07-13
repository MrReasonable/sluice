"""Hired (hired.com). RETIRED 2026-07-07: hired.com now 302-redirects to lhh.com
(LHH acquisition) - the standalone job board is defunct. Registered disabled.
Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div[class*="job"],li[class*="job"]').forEach(c=>{const t=c.querySelector('h2,h3,[class*="title"]')?.textContent?.trim()||'';const co=c.querySelector('[class*="company"]')?.textContent?.trim()||'';const ln=c.querySelector('a[href*="/job/"]')?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:'',link:ln,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="hired",
    enabled=False,  # retired 2026-07-07: hired.com -> lhh.com (defunct)
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('Hired example', 'https://hired.com/jobs/software-engineer'),
    ],
))
