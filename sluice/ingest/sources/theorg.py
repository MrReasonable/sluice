"""TheOrg (theorg.com). RETIRED 2026-07-07, retirement UPHELD 2026-08-25 on new grounds.

The original note said `/jobs/<role>` 404s, and inferred the board was gone. Re-probed
2026-08-25: the 404 is real, but the SITE is alive and has rebranded to Orgio. Job
search still exists, advertised as "Job Discovery" at `/job-discovery` -- but that path
is a marketing page behind Log in / Sign up, not a listing surface. So this stays
disabled because reviving it needs an ACCOUNT, not a new URL. That is a different
decision from the one the old note implied, and it should be taken deliberately.
Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('div[class*="job"],a[href*="/jobs/"]').forEach(c=>{const el=c.closest('a')||c;const t=c.querySelector('h2,h3,[class*="title"]')?.textContent?.trim()||c.textContent.trim();const co=c.querySelector('[class*="company"]')?.textContent?.trim()||'';const ln=el.href||'';if(t&&t.length>5&&ln)r.push({title:t,company:co,location:'',link:ln,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="theorg",
    enabled=False,  # retired 2026-07-07: /jobs/<role> 404
    reprobed="2026-08-25",   # see the module docstring for what was found
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('TheOrg example', 'https://theorg.com/jobs/software-engineer'),
    ],
))
