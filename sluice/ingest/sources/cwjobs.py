"""CWJobs (cwjobs.co.uk), UK's biggest contract IT board. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    document.querySelectorAll('.job, .job-result, article, [data-testid="job-card"], div[class*="job"], li[class*="result"]').forEach(c=>{
        const t=c.querySelector('h2, h3, [data-testid="job-title"], a[class*="title"]')?.textContent?.trim()||'';
        const co=c.querySelector('[data-testid="company-name"], .company, .employer, [class*="company"], [class*="employer"]')?.textContent?.trim()||'';
        const lo=c.querySelector('[data-testid="location"], .location, [class*="location"]')?.textContent?.trim()||'';
        const sa=c.querySelector('[data-testid="salary"], .salary, .daily-rate, [class*="salary"]')?.textContent?.trim()||'';
        const ln=c.querySelector('a[href*="/job/"], a[class*="title"]')?.href||'';
        if(t&&t.length>5) r.push({title:t, company:co, location:lo, link:ln, salary:sa});
    });
    if(r.length===0){
        document.querySelectorAll('a[href*="/job/"]').forEach(a=>{
            const t=a.textContent.trim();
            if(t.length>8) r.push({title:t, company:'', location:'', link:a.href, salary:''});
        });
    }
    return r.slice(0,25);
})()
"""

register(BrowserListSource(
    id="cwjobs",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('CWJobs example', 'https://www.cwjobs.co.uk/jobs/software-developer/in-london?sort=date'),
    ],
))
