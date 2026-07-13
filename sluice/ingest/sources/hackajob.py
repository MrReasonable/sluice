"""Hackajob (hackajob.co). RETIRED 2026-07-07: hackajob.co/search redirects to
hackajob.com/talent/not-found (404) - the public search path is gone (invite/
match-based platform). Registered disabled. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div[class*="job"],article').forEach(c=>{const t=c.querySelector('h2,h3,[class*="title"]')?.textContent?.trim()||'';const co=c.querySelector('[class*="company"]')?.textContent?.trim()||'';const lo=c.querySelector('[class*="location"]')?.textContent?.trim()||'';const ln=c.querySelector('a[href*="/job/"]')?.href||'';const sa=c.querySelector('[class*="salary"]')?.textContent?.trim()||'';if(t&&t.length>5)r.push({title:t,company:co,location:lo,link:ln,salary:sa})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="hackajob",
    enabled=False,  # retired 2026-07-07: hackajob.co/search -> 404
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('Hackajob example', 'https://hackajob.co/search?query=software+developer&location=London'),
    ],
))
