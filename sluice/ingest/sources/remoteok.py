"""RemoteOK (remoteok.com). Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('tr[class*="job"],.job').forEach(c=>{const t=c.querySelector('h2,a[itemprop="title"]')?.textContent?.trim()||'';const co=c.querySelector('h3[itemprop="hiringOrganization"],td[class*="company"] h3')?.textContent?.trim()||c.querySelector('td[class*="company"]')?.textContent?.trim()||'';const ln=c.querySelector('a[itemprop="url"]')?.href||c.closest('a')?.href||'';if(t&&t.length>5)r.push({title:t,company:co,location:'Remote',link:ln,salary:''})});return r.slice(0,25)})()"""

register(BrowserListSource(
    id="remoteok",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"location": "Remote"},
    searches_spec=[
        ('RemoteOK example', 'https://remoteok.com/remote-software-developer-jobs'),
    ],
))
