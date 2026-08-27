"""Hired (hired.com). RETIRED 2026-07-07, retirement CONFIRMED 2026-08-27.

The original note recorded a 302 to lhh.com and inferred the board was defunct -- the
same path-shaped evidence that turned out to be wrong for three of the five boards
retired that day, so it was worth re-probing rather than inheriting.

Re-probed 2026-08-27 on the DOMAIN, not a search path, which is the distinction #207 is
about: `hired.com`, `www.hired.com` and `hired.com/jobs` ALL 302 to
`www.lhh.com/en-us/about-us/our-story` -- an acquirer's corporate About page, not a
listing surface and not a moved board. The domain still resolves, so this is not bwork's
NXDOMAIN case; it is a site that has been wholly redirected away. There is no path left
to fix, which is what separates it from jobserve and hackajob.

LHH runs its own job search, but that would be a NEW source against a different board
with a different extractor, not a revival of this one -- and adding it is a decision
about whether that board is worth scraping, not a drift fix. Registered disabled.
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
