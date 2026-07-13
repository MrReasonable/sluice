"""Indeed (uk/ae), contract-filtered. Declarative extractor JS + an example search (override via config).
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    document.querySelectorAll('.job_seen_beacon, .cardOutline, [data-testid="jobCard"]').forEach(c=>{
        const t=c.querySelector('h2 a, .jobTitle a, a[data-jk]')?.textContent?.trim()||'';
        const co=c.querySelector('[data-testid="company-name"], .companyName, .company_location')?.textContent?.trim()||'';
        const lo=c.querySelector('[data-testid="text-location"], .companyLocation')?.textContent?.trim()||'';
        const ln=c.querySelector('a[data-jk], h2 a')?.href||'';
        const sa=c.querySelector('.salary-snippet-container, .salaryOnly, [data-testid="salary-snippet"]')?.textContent?.trim()||'';
        if(t&&t.length>3) r.push({title:t, company:co, location:lo, link:ln, salary:sa});
    });
    return r.slice(0,25);
})()
"""

register(BrowserListSource(
    id="indeed",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=800,
    extra={"job_type": "contract"},
    searches_spec=[
        ('Indeed example', 'https://uk.indeed.com/jobs?q=software+developer&l=London&sort=date'),
    ],
))
