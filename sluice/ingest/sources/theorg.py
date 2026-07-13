"""TheOrg (theorg.com). RETIRED 2026-07-07: /jobs/<role> now 404s
(site alive, job-search path changed; low-value org-chart board). Registered
disabled; recheck for the new jobs URL if worth reviving. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div[class*="job"],a[href*="/jobs/"]').forEach(c=>{const el=c.closest('a')||c;const t=c.querySelector('h2,h3,[class*="title"]')?.textContent?.trim()||c.textContent.trim();const co=c.querySelector('[class*="company"]')?.textContent?.trim()||'';const ln=el.href||'';if(t&&t.length>5&&ln)r.push({title:t,company:co,location:'',link:ln,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="theorg",
    enabled=False,  # retired 2026-07-07: /jobs/<role> 404
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('TheOrg example', 'https://theorg.com/jobs/software-engineer'),
    ],
))
