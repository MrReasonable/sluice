"""B-Work (b-work.io). RETIRED 2026-07-07: b-work.io returns HTTP 500 (server
down / dead domain). Registered disabled so it's excluded from runs but revivable
if the site returns. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div[class*="job"],li[class*="job"]').forEach(c=>{const t=c.querySelector('h2,h3,a[class*="title"]')?.textContent?.trim()||'';const co=c.querySelector('[class*="company"]')?.textContent?.trim()||'';const lo=c.querySelector('[class*="location"]')?.textContent?.trim()||'';const ln=c.querySelector('a[href*="/job/"]')?.href||c.closest('a')?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:lo,link:ln,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="bwork",
    enabled=False,  # retired 2026-07-07: b-work.io HTTP 500
    extractor_js=_JS,
    wait=5, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('B Work example', 'https://b-work.io/jobs/search/software+developer?city=London'),
    ],
))
