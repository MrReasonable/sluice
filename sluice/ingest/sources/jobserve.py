"""Jobserve (largest UK IT contract board).

2026-08-25: the board moved and the old extractor stopped matching anything.
`JobSearch.aspx?q=...` now 302s to `JobListing.aspx?shid=<search-id>`, and job
links changed from short codes (`/gaItG`) to full slug URLs ending in the row's
hex id. The previous extractor scanned every `a[href]` for `^/[A-Za-z0-9]{4,8}$`,
which matches nothing on the new markup: zero rows, no error, a bare `zero` drift
verdict, and the runtime auto-retired a board that was serving 11,440 jobs.

Rows now live in `#joblistingcollection .jobListItem`, which also carries company,
location and rate -- fields the short-code extractor left blank. Verified against
the live board 2026-08-25: 20/20 rows on the first page.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

# Anchored on `.jobListItem` rather than an href shape. A row is a row even when the
# board rewrites its URLs again, which is the failure this replaces.
_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('#joblistingcollection .jobListItem').forEach(el=>{
  const a=el.querySelector('a.jobListPosition');if(!a)return;
  const href=a.getAttribute('href')||'';if(!href)return;
  const link=href.startsWith('http')?href:'https://www.jobserve.com'+href;
  if(seen.has(link))return;seen.add(link);
  const txt=s=>{const n=el.querySelector(s);return n?(n.textContent||'').trim().replace(/\s+/g,' '):''};
  // The recruiter branding link is the only place the agency name appears in a summary row.
  const brand=el.querySelector('a[title^="View more information about"]');
  const company=brand?(brand.getAttribute('title')||'').replace(/^View more information about\s*/,'').trim():'';
  const title=(a.textContent||'').trim().replace(/\s+/g,' ');
  if(!title)return;
  // summlocation/summrate ids repeat per row, so they are queried WITHIN the row.
  r.push({title:title,company:company,location:txt('[id="summlocation"]'),
          salary:txt('[id="summrate"]'),link:link});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="jobserve",
    extractor_js=_JS,
    wait=8, scrolls=3, scroll_amount=1200,
    extra={"job_type": "contract"},
    searches_spec=[
        ('Jobserve example', 'https://www.jobserve.com/gb/en/JobSearch.aspx?q=software+developer&js=1'),
    ],
))
