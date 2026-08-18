"""Reed (reed.co.uk), contract. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    document.querySelectorAll('article, .job-result, [data-testid="job-card"], div[class*="job"], div[class*="card"]').forEach(c=>{
        const titleEl = c.querySelector('h2, h3, a[class*="title"], [class*="title"]');
        const t = titleEl?.textContent?.trim()||'';
        const co=c.querySelector('[class*="company"], [class*="employer"], .brand, [class*="brand"]')?.textContent?.trim()||'';
        const lo=c.querySelector('[class*="location"], [class*="locality"]')?.textContent?.trim()||'';
        const sa=c.querySelector('[class*="salary"], [class*="rate"], [class*="price"]')?.textContent?.trim()||'';
        let ln = '', degraded = '';
        if(titleEl && titleEl.tagName === 'A') ln = titleEl.href;
        if(!ln) ln = c.querySelector('a[href*="/job/"]')?.href||'';
        if(!ln) ln = c.querySelector('a[href*="job"]')?.href||'';
        // The last tier has no scoping at all -- it accepts ANY anchor in the card, which
        // is a real fallback, not a normal cascade step. Stamped `degraded` (#156) so a
        // future selector rot that pushes every row through this tier reports a `fallback`
        // drift reason instead of a silently healthy count.
        if(!ln){ ln = c.querySelector('a')?.href||''; if(ln) degraded = 'link-fallback'; }
        if(t&&t.length>5){
            const row = {title:t, company:co, location:lo, link:ln, salary:sa};
            if(degraded) row.degraded = degraded;
            r.push(row);
        }
    });
    return r.slice(0,20);
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
