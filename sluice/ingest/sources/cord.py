"""Cord (cord.com). Job links are /search/u/<company>/jobs/<id>-<slug>; title
and company are parsed straight from the slug (the card DOM is recruiter-chrome
polluted). A CV-upsell modal overlays results, so dismiss it before extracting.

BOTH slug segments are percent-DECODED before their hyphen substitution, and the
symmetry is the point (#160). Only the title was decoded originally, so any
URL-reserved character in the COMPANY segment reached the vault verbatim: a company
named `Example?` is served as the slug `example%3F`, and `company: "example%3F"` is
what got stored. Not cosmetic -- `track` matches incoming mail against the stored
company name, so mail classifying against the readable name never matched its lead,
and already-resolved threads re-surfaced as unmatched on every pass. Any name
carrying `&`, `+`, a space or a `?` hits it identically.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_DISMISS = r"""(()=>{document.querySelectorAll('button').forEach(b=>{const t=(b.textContent||'').toLowerCase();if(/close|dismiss|no thanks|skip|✕|×/.test(t))b.click();});return 1;})()"""
_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('a[href*="/jobs/"]').forEach(a=>{try{
  const h=a.getAttribute('href')||'';
  const m=h.match(/\/u\/([^\/]+)\/jobs\/\d+-([^?]+)/);
  if(!m||seen.has(m[0]))return;seen.add(m[0]);
  const company=decodeURIComponent(m[1]).replace(/-/g,' ');
  const title=decodeURIComponent(m[2].replace(/%2f/gi,'-')).replace(/-/g,' ').trim();
  r.push({title,company,location:'',link:'https://cord.com'+h.split('?')[0],salary:''});
// PER-ROW isolation, not a blanket swallow: `decodeURIComponent` throws URIError on a
// malformed escape (a lone `%`, or `%ZZ`), and an uncaught throw inside forEach aborts the
// WHOLE extractor -- every row of the page lost, reported as an unexplained zero. The
// decode on `title` always carried that risk; decoding `company` too widened it, so the
// isolation lands with it. One unparseable href costs its own row, which `count` and
// `detect_drift`'s `drop` already measure against the source's baseline.
}catch(e){}});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="cord",
    extractor_js=_JS,
    dismiss_js=_DISMISS,
    wait=3, scrolls=2, scroll_amount=1200,
    searches_spec=[
        ('Cord example', 'https://cord.com/search?q=software+developer&location=London'),
    ],
))
