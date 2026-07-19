"""Google Jobs (google.com/search?...&ibp=htl;jobs). Searches carry a URL, so the
query->url transform is applied here at import via `_url`. Google job cards carry
no stable outbound link, so these leads dedup by title+company+location (Lead.dedup_key's
hash fallback; location keeps two cities apart -- see #23). Override the built-in example
with your own searches via
`sources.google.searches` in config.
"""
from urllib.parse import quote

from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register


def _url(query: str) -> str:
    return f"https://www.google.com/search?q={quote(query)}&ibp=htl;jobs"


_JS = r"""
(()=>{
    const r=[];
    document.querySelectorAll('div[data-share-url], g-card, div[data-ved]').forEach(c=>{
        const t=c.querySelector('[role="heading"], h2, .KLsYvd, .BjJfJf')?.textContent?.trim()||'';
        const co=c.querySelector('.vNEEBe, .nJlQNd, .sMzDkb')?.textContent?.trim()||'';
        const lo=c.querySelector('.Qk80Jf, .wHYlTd, .MUxGbd')?.textContent?.trim()||'';
        const sn=c.querySelector('.HBvzbc, .WbKhed')?.textContent?.trim()||'';
        if(t&&t.length>5) r.push({title:t, company:co, location:lo, snippet:sn, link:''});
    });
    return r.slice(0,20);
})()
"""

register(BrowserListSource(
    id="google",
    extractor_js=_JS,
    wait=4, scrolls=0, scroll_amount=600,
    searches_spec=[
        ("Google Jobs example", _url("software developer jobs london"), {"job_type": "perm"}),
    ],
))
