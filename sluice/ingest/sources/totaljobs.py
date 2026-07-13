"""TotalJobs (totaljobs.co.uk), UK board with a contract filter. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    document.querySelectorAll('article, .job, .job-result, [data-testid="job-card"], div[class*="job"], div[class*="card"]').forEach(c=>{
        const titleEl = c.querySelector('h2, h3, a[class*="title"], [class*="title"], .job-title');
        const t = titleEl?.textContent?.trim()||'';
        const co=c.querySelector('[class*="company"], [class*="employer"], .brand, .job-company')?.textContent?.trim()||'';
        const lo=c.querySelector('[class*="location"], [class*="locality"], .job-location')?.textContent?.trim()||'';
        const sa=c.querySelector('[class*="salary"], [class*="rate"], .job-salary')?.textContent?.trim()||'';
        let ln = '';
        if(titleEl && titleEl.tagName === 'A') ln = titleEl.href;
        if(!ln) ln = c.querySelector('a[href*="/job/"]')?.href||'';
        if(!ln) ln = c.querySelector('a[href*="job"]')?.href||'';
        if(!ln) ln = c.querySelector('a')?.href||'';
        if(t&&t.length>5) r.push({title:t, company:co, location:lo, link:ln, salary:sa});
    });
    return r.slice(0,20);
})()
"""

register(BrowserListSource(
    id="totaljobs",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('TotalJobs example', 'https://www.totaljobs.com/jobs/software-developer/in-london?sort=date'),
    ],
))
