"""WeWorkRemotely (weworkremotely.com). 2026-07-07: the search page is an empty
shell, so scan category pages; job anchors carry a posting-age token used to skip
nav links. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('a[href*="/remote-jobs/"]').forEach(a=>{
  const h=a.getAttribute('href')||'';
  if(/find-your-plan|\/search|post-a-job|\/new$/i.test(h))return;
  if(seen.has(h))return;seen.add(h);
  const raw=(a.textContent||'').trim().replace(/\s+/g,' ');if(raw.length<8)return;
  if(!/\b\d+[dwmh]\b/.test(raw))return;
  const title=raw.split(/\s\d+[dwmh]\b/)[0].slice(0,90);if(title.length<4)return;
  r.push({title,company:'',location:'Remote',link:'https://weworkremotely.com'+h,salary:''});
});return r.slice(0,30);})()"""

register(BrowserListSource(
    id="weworkremotely",
    # The extractor pushes `company:''` unconditionally: this board's job anchors carry
    # the role only, so there is no company on the page for a selector to have stopped
    # reading. Declared so `ingest list-sources --health` stops printing a permanent
    # UNGUARDED(company) here -- a flag always lit on a benign row is how a column dies.
    unpublished_fields=("company",),
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=1400,
    extra={"location": "Remote"},
    searches_spec=[
        ('WeWorkRemotely example', 'https://weworkremotely.com/categories/remote-programming-jobs'),
    ],
))
