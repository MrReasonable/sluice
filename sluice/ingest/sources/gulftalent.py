"""GulfTalent (gulftalent.com), UAE. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('table tr').forEach(row=>{const cells=row.querySelectorAll('td');if(cells.length>=3){const linkEl=cells[0].querySelector('a[href*="/jobs/"]');const t=linkEl?.textContent?.trim()||'';const coEl=cells[0].querySelector('a:not([href*="/jobs/"])');const co=coEl?.textContent?.trim()||'';const locEl=cells[1].querySelector('a');const lo=locEl?.textContent?.trim()||'';const ln=linkEl?.href||'';if(t&&t.length>5&&t!=='Position')r.push({title:t,company:co,location:lo,link:ln,salary:''})}});return r.slice(0,20)})()"""

register(BrowserListSource(
    id="gulftalent",
    extractor_js=_JS,
    wait=5, scrolls=3, scroll_amount=600,
    searches_spec=[
        ('GulfTalent example', 'https://www.gulftalent.com/jobs/search?search_keyword=software+developer'),
    ],
))
