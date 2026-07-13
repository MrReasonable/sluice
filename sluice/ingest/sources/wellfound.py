"""Wellfound (wellfound.com, ex-AngelList) - startup EM roles (permanent).
Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('a[href*="/company/"], a[href*="/jobs/"]').forEach(a=>{const t=a.querySelector('h2,h3,div[class*="title"]')?.textContent?.trim()||a.textContent.trim();const p=a.closest('div,li');const co=p?.querySelector('div[class*="company"], span[class*="company"]')?.textContent?.trim()||'';if(t&&t.length>3&&!r.find(x=>x.title===t))r.push({title:t,company:co,location:'',link:a.href,salary:''})});return r.slice(0,15)})()"""

register(BrowserListSource(
    id="wellfound",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Wellfound example', 'https://wellfound.com/role/r/software-engineer'),
    ],
))
