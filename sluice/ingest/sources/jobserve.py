"""Jobserve (largest UK IT contract board). 2026-07-07: the working GET search is
JobSearch.aspx?q=...&jt=Contract&js=1, returning short-code job anchors (e.g.
/gaItG). It renders its featured match late, so it needs a longer initial wait.
Extractor + searches lifted verbatim from the legacy scanner.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('a[href]').forEach(a=>{
  const h=a.getAttribute('href')||'';
  if(!/^\/[a-zA-Z0-9]{4,8}(\?|$)/.test(h))return;
  const key=h.split('?')[0];if(seen.has(key))return;
  const t=(a.textContent||'').trim().replace(/\s+/g,' ');
  if(t.length<6||t.length>90)return;seen.add(key);
  r.push({title:t,company:'',location:'',link:'https://www.jobserve.com'+key,salary:''});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="jobserve",
    extractor_js=_JS,
    wait=8, scrolls=3, scroll_amount=1200,
    extra={"job_type": "contract"},
    searches_spec=[
        ('Jobserve example', 'https://www.jobserve.com/gb/en/JobSearch.aspx?q=software+developer&js=1'),
    ],
))
