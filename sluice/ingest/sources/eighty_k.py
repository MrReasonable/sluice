"""80,000 Hours (jobs.80000hours.org). 2026-07-07: each card's only stable anchor
is the "Discuss with AI" bubble carrying ?jobId=NNNN; walk up to the card for the
title. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('a[href*="jobId="]').forEach(a=>{
  const m=(a.getAttribute('href')||'').match(/jobId=(\d+)/);if(!m)return;const id=m[1];
  if(seen.has(id))return;seen.add(id);
  let el=a,card=null;for(let i=0;i<8&&el;i++){el=el.parentElement;if(el&&el.innerText&&el.innerText.length>40){card=el;break;}}
  let txt=(card?.innerText||'').replace(/\s+/g,' ').replace(/Highlighted role/gi,'').replace(/Discuss this opportunity with AI\.?/gi,'').trim();
  if(txt.length<5)return;
  r.push({title:txt.slice(0,120),company:'',location:'',link:'https://jobs.80000hours.org/?jobId='+id,salary:''});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="eighty_k",
    extractor_js=_JS,
    wait=5, scrolls=2, scroll_amount=1400,
    searches_spec=[
        ('80k Hours example', 'https://jobs.80000hours.org/?query=software+developer'),
    ],
))
