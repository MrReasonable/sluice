"""Hackajob (hackajob.com).

REVIVED 2026-08-25. The 2026-07-07 retirement read a 404 on one path as the board
having gone invite-only. It had not: the DOMAIN moved, `.co` -> `.com`, and the old
`hackajob.co/search` lands on `hackajob.com/talent/not-found`. The public board is
at `/jobs`, unauthenticated, advertising 9,054 live roles.

`?search=` and `?country=` are honoured in the URL (9,054 -> 464 for one pairing),
though matching is loose rather than a strict keyword filter. `?page=N` paginates.
Rows are `article.job-row` under `ul.jobs-rows`. Verified against the live board
2026-08-25: 12/12 rows on the first page, with company, salary and location.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('article.job-row').forEach(card=>{
  const a=card.querySelector('.job-row__title a')||card.querySelector('a[href]');if(!a)return;
  const href=a.getAttribute('href')||'';if(!href)return;
  const link=href.startsWith('http')?href:'https://hackajob.com'+href;
  if(seen.has(link))return;seen.add(link);
  const txt=s=>{const n=card.querySelector(s);return n?(n.textContent||'').trim().replace(/\s+/g,' '):''};
  const title=txt('.job-row__title');if(!title)return;
  // Company, salary and location are rendered as sibling lines rather than tagged
  // nodes, so they are read positionally off the card's own text.
  const bits=(card.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
  const money=bits.find(b=>/[£$€]|\/year|per annum/i.test(b))||'';
  r.push({title:title,company:txt('.job-row__company')||bits[1]||'',
          location:txt('.job-row__location')||'',salary:money,link:link});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="hackajob",
    extractor_js=_JS,
    wait=6, scrolls=3, scroll_amount=1000,
    searches_spec=[
        ('Hackajob example', 'https://hackajob.com/jobs?search=software+developer&country=United+Kingdom'),
    ],
))
