"""Welcome to the Jungle / Otta (app.welcometothejungle.com). The authenticated
feed shows one matched job at a time; advancing via the next-button CONSUMES it
(persists across runs as "you're all caught up"), so this is a CarouselSource that
walks one job at a time. read_js lifted verbatim from the legacy scanner.
"""
from sluice.ingest.base import CarouselSource
from sluice.ingest.sources import register

_READ_JS = r"""(()=>{
  const h=document.querySelector('h1');
  const title=h?h.textContent.trim():'';
  const caught=/all caught up/i.test(document.body.innerText||'');
  const parts=title.split(',');
  const company=parts.length>1?parts.slice(1).join(',').trim():'';
  const body=document.body.innerText||'';
  const sm=body.match(/Salary not provided|[£$€][\d,]+[kK]?(?:\s*[-–]\s*[£$€]?[\d,]+[kK]?)?/);
  const rel=[...document.querySelectorAll('a[href*="/jobs/"]')].map(a=>a.getAttribute('href')).find(Boolean)||'';
  const link=rel?('https://app.welcometothejungle.com'+rel.split('?')[0]):'';
  return {title, company, location:'', salary:sm?sm[0]:'', link, caught};
})()"""

register(CarouselSource(
    id="wttj",
    read_js=_READ_JS,
    advance_selector='[data-testid="next-button"]',
    wait=8, max_jobs=25,
    searches_spec=[
        ('WTTJ example', 'https://app.welcometothejungle.com/jobs?query=software+developer&around=London', {'job_type': 'perm'}),
    ],
))
