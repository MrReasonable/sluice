"""NaukriGulf (naukrigulf.com), UAE. 2026-07-07: DOM rebound - job cards are
`div.ng-box.srp-tuple` with `a.info-position` (title + detail link) and
`a.info-org` (company). The old `div[class*="job"]`/`a.title` selectors matched
nothing (there is no "job" in the card class), so re-selected from a live probe.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div.srp-tuple').forEach(c=>{const a=c.querySelector('a.info-position');const t=a?.textContent?.trim()||'';const co=c.querySelector('a.info-org')?.textContent?.trim()||'';const lo=c.querySelector('.info-loc,[class*="location"],[class*="loc"]')?.textContent?.trim()||'';const ln=a?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:lo||'UAE',link:ln,salary:''})});return r.slice(0,25)})()"""

register(BrowserListSource(
    id="naukrigulf",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('NaukriGulf example', 'https://www.naukrigulf.com/software-developer-jobs'),
    ],
))
