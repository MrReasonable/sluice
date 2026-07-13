"""Cord (cord.com). Job links are /search/u/<company>/jobs/<id>-<slug>; title
and company are parsed straight from the slug (the card DOM is recruiter-chrome
polluted). A CV-upsell modal overlays results, so dismiss it before extracting.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_DISMISS = r"""(()=>{document.querySelectorAll('button').forEach(b=>{const t=(b.textContent||'').toLowerCase();if(/close|dismiss|no thanks|skip|✕|×/.test(t))b.click();});return 1;})()"""
_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('a[href*="/jobs/"]').forEach(a=>{
  const h=a.getAttribute('href')||'';
  const m=h.match(/\/u\/([^\/]+)\/jobs\/\d+-([^?]+)/);
  if(!m||seen.has(m[0]))return;seen.add(m[0]);
  const company=m[1].replace(/-/g,' ');
  const title=decodeURIComponent(m[2].replace(/%2f/gi,'-')).replace(/-/g,' ').trim();
  r.push({title,company,location:'',link:'https://cord.com'+h.split('?')[0],salary:''});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="cord",
    extractor_js=_JS,
    dismiss_js=_DISMISS,
    wait=3, scrolls=2, scroll_amount=1200,
    searches_spec=[
        ('Cord example', 'https://cord.com/search?q=software+developer&location=London'),
    ],
))
