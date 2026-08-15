"""LinkedIn contract search. LinkedIn virtualizes its results list, so a plain
window scroll won't load more jobs - this subclass scrolls the results *panel* 8x.

2026-07-07: extractor rebound to the current DOM. LinkedIn cards are
`.artdeco-entity-lockup` with `__title strong` (title, de-duped from the
visible+a11y doubling), `__subtitle` (company), `__caption` (location); the job
link is the card's `a[href*="/jobs/view/"]` (numeric-only now - the old URL-slug
company recovery is obsolete, and DOM extraction gets company directly, so the
snapshot fallback is unneeded).
"""
import time

from sluice.ingest.base import BrowserListSource, Search
from sluice.ingest.sources import register

# Scroll LinkedIn's virtualized results container (falls back to window scroll).
_PANEL_SCROLL = r"""(()=>{const panel=document.querySelector('.jobs-search__results-list, .scaffold-layout__list, [class*="jobs-search-results-list"]');if(panel){panel.scrollTop=panel.scrollHeight;}else{window.scrollBy(0,900);}return 1;})()"""

_JS = r"""
(()=>{
    const r=[];
    const seen=new Set();
    // LinkedIn renders title twice (visible strong + a11y span); collapse "X X" -> "X".
    const dedupe=(s)=>{s=(s||'').replace(/\s+/g,' ').trim();const n=s.length;if(n%2===1){const h=(n-1)/2;if(s[h]===' '&&s.slice(0,h)===s.slice(h+1))return s.slice(0,h);}return s;};
    document.querySelectorAll('.artdeco-entity-lockup').forEach(lk=>{
        const a=lk.querySelector('a[href*="/jobs/view/"]');
        if(!a)return;
        const link=a.href.split('?')[0];
        if(seen.has(link))return;seen.add(link);
        let t=(lk.querySelector('.artdeco-entity-lockup__title strong')||{}).textContent||'';
        t=t.trim().replace(/\s+/g,' ');
        if(!t) t=dedupe((lk.querySelector('.artdeco-entity-lockup__title')||{}).textContent||'');
        const co=((lk.querySelector('.artdeco-entity-lockup__subtitle')||{}).textContent||'').replace(/\s+/g,' ').trim();
        const lo=((lk.querySelector('.artdeco-entity-lockup__caption')||{}).textContent||'').replace(/\s+/g,' ').trim();
        if(t&&t.length>4)r.push({title:t,company:co,location:lo,link,salary:''});
    });
    return r.slice(0,50);
})()
"""


class _LinkedInSource(BrowserListSource):
    def fetch(self, ctx, search: Search) -> dict:
        cam, sleep = ctx.camofox, getattr(ctx, "sleep", time.sleep)
        tid = cam.create_tab(search.url)
        if not tid:
            return {"result": [], "landed": "", "requested": search.url, "error": "no-tab"}
        sleep(self.wait)
        for _ in range(self.scrolls):  # scroll the results PANEL, not the window
            cam.evaluate(tid, _PANEL_SCROLL)
            sleep(0.5)
        result = cam.evaluate(tid, self.extractor_js)
        landed = cam.evaluate(tid, "location.href")
        # This override exists only to scroll the results PANEL, so it must otherwise keep
        # the base class's contract -- including the auth probe. Omitting it here is how a
        # subclass silently opts out of a safety signal it looks like it has.
        auth_missing = False
        if self.auth_probe_js:
            probe = cam.evaluate(tid, self.auth_probe_js)
            auth_missing = bool(probe.get("result")) if isinstance(probe, dict) and "error" not in probe else False
        cam.close_tab(tid)
        rows = result.get("result") if isinstance(result, dict) else None
        landed_url = (landed.get("result") if isinstance(landed, dict) else None) or search.url or ""
        return {"result": rows or [], "landed": landed_url, "requested": search.url,
                "auth_missing": auth_missing}


# Logged out, LinkedIn still serves a full page of jobs -- in GUEST markup (`base-card` /
# `job-search-card`), with none of the `artdeco-entity-lockup` cards the extractor above
# targets. So the extractor returns 0 while the page is manifestly working, which is
# indistinguishable from "no jobs matched" and is exactly what auto-retired this source on
# 2026-08-15. Measured on the live browser that day: artdeco 0, guest cards 60.
#
# Requires BOTH halves: guest cards present AND authenticated cards absent. Guest markup
# alone would fire on a page that renders both during a LinkedIn A/B, and "artdeco absent"
# alone would fire on a genuinely empty result set.
_AUTH_PROBE = (
    "(()=>document.querySelectorAll('.artdeco-entity-lockup').length === 0 "
    "&& document.querySelectorAll('.base-card, .job-search-card').length > 0)()"
)

register(_LinkedInSource(
    id="linkedin",
    extractor_js=_JS,
    auth_probe_js=_AUTH_PROBE,
    wait=4, scrolls=8, scroll_amount=900,
    extra={"job_type": "contract"},
    searches_spec=[
        ('LinkedIn example', 'https://www.linkedin.com/jobs/search/?keywords=software%20developer&location=London%2C%20England%2C%20United%20Kingdom&sortBy=DD'),
    ],
))
