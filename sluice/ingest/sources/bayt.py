"""Bayt (bayt.com), UAE. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('li:has(h2)').forEach(c=>{const h2=c.querySelector('h2');const t=h2?.textContent?.trim()||'';const co=c.querySelector('img[alt]')?.alt?.replace(' logo','')||'';const locLinks=c.querySelectorAll('a[href*="/jobs/"]');const loc=locLinks.length>1?locLinks[1]?.textContent?.trim()||'':locLinks[0]?.textContent?.trim()||'';const ln=h2?.querySelector('a')?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:loc,link:ln,salary:''})});return r.slice(0,20)})()"""

register(BrowserListSource(
    id="bayt",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('Bayt example', 'https://www.bayt.com/en/international/jobs/software-developer-jobs/'),
    ],
))
