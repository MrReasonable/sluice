"""Reed (reed.co.uk), contract. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    const fellBackFlags=[];  // parallel to r: whether row i used the unscoped fallback tier
    document.querySelectorAll('article, .job-result, [data-testid="job-card"], div[class*="job"], div[class*="card"]').forEach(c=>{
        const titleEl = c.querySelector('h2, h3, a[class*="title"], [class*="title"]');
        const t = titleEl?.textContent?.trim()||'';
        const co=c.querySelector('[class*="company"], [class*="employer"], .brand, [class*="brand"]')?.textContent?.trim()||'';
        const lo=c.querySelector('[class*="location"], [class*="locality"]')?.textContent?.trim()||'';
        const sa=c.querySelector('[class*="salary"], [class*="rate"], [class*="price"]')?.textContent?.trim()||'';
        let ln = '', usedFallback = false;
        if(titleEl && titleEl.tagName === 'A') ln = titleEl.href;
        if(!ln) ln = c.querySelector('a[href*="/job/"]')?.href||'';
        if(!ln) ln = c.querySelector('a[href*="job"]')?.href||'';
        // The last tier has no scoping at all -- it accepts ANY anchor in the card. Counted,
        // not stamped per-row (#156 review follow-up): the marker promotes to a SOURCE-level
        // `fallback` drift reason that withholds every lead from this run (BREAKER_REASONS in
        // ingest/engine.py), so one odd card whose markup genuinely differs from its
        // neighbours must not be enough to silence a source that is otherwise reading fine --
        // measured against reed's real page, a single sponsored/ad card was exactly this shape.
        if(!ln){ ln = c.querySelector('a')?.href||''; if(ln) usedFallback = true; }
        // Tracked in a PARALLEL array, not counted directly (#156 review follow-up, second
        // pass): the page can match far more than 20 cards, but only the first 20 PUSHED
        // rows are ever returned below. Counting every card that fell back -- rather than
        // only the ones among the RETURNED rows -- measured dominance against a population
        // the caller never actually sees, in either direction: a page where the fallback
        // hits are concentrated in the first 20 could under-count if diluted by clean cards
        // further down the page; a page where they're concentrated further down could
        // over-count against a returned set that is actually clean.
        if(t&&t.length>5){
            r.push({title:t, company:co, location:lo, link:ln, salary:sa});
            fellBackFlags.push(usedFallback);
        }
    });
    // Slice FIRST, measure the SLICE: the dominance ratio must be computed over exactly
    // the rows this function actually returns, matching `fellBackFlags`' own indices.
    const returned = r.slice(0,20);
    const returnedFallbacks = fellBackFlags.slice(0,20).filter(Boolean).length;
    // Stamped only when the unscoped tier carried MOST of the RETURNED page, not one card
    // -- that is the shape a real selector rot takes (title/company/location keep working;
    // only the LINK cascade's scoped tiers stop matching), distinct from an isolated outlier.
    // Row floor of 8, matching `blank`'s own small-sample discipline in `_lead_rates`
    // (review follow-up): without it, a narrow search returning 1-3 rows can trip
    // `1 > 0.5` on a single odd card and withhold real leads over noise, not a rot.
    if(returned.length >= 8 && returnedFallbacks > returned.length / 2) {
        returned[0].degraded = 'link-fallback';
    }
    return returned;
})()
"""

register(BrowserListSource(
    id="reed",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('Reed example', 'https://www.reed.co.uk/jobs/software-developer-jobs-in-london?sortby=datecreated'),
    ],
))
