"""Escape the City (escapethecity.org) - permanent, mission-driven roles.

2026-08-25: the URL and selector are fixed, but this stays DISABLED, for a
different and better-evidenced reason than the 2026-07-07 note gave.

That note said the board had drifted and left it at that. The board is in fact
alive and listing: `/search/jobs` -- the very path the old note recorded the 302
pointing at -- serves rows today, and the extractor below reads 12/12 of them
with title, company, location and salary (verified live 2026-08-25).

What does NOT work is targeting it. `?q=` is not free text: it takes structured
`field=value` filters (only `org-name=` is discoverable from the rendered page),
and a free-text `?q=software+developer` is silently DROPPED, normalising back to
an unfiltered `/search/jobs`. So a run here cannot search; it can only scrape the
latest-jobs page and hand every row to triage. Enabling that spends triage budget
at a fixed rate per run on an unfiltered feed.

Flip `enabled=True` to take it on those terms -- the extractor is ready and there
is no code left to write. Better still, find the filter grammar `?q=` accepts
beyond `org-name=` and put real searches in config first.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""(()=>{const r=[];const seen=new Set();
document.querySelectorAll('.job-card').forEach(card=>{
  const a=card.querySelector('a[href^="/opportunity/"]');if(!a)return;
  const href=(a.getAttribute('href')||'').split('?')[0];if(!href)return;
  if(seen.has(href))return;seen.add(href);
  const txt=s=>{const n=card.querySelector(s);return n?(n.textContent||'').trim().replace(/\s+/g,' '):''};
  const title=txt('.job-card__title');if(!title)return;
  // The card's own anchor text is the "View job" button, so the title comes from
  // .job-card__title rather than from the link. That is what broke the old extractor.
  r.push({title:title,company:txt('.job-card__org-name'),
          location:txt('.job-card__location')||txt('[class*="location"]'),
          salary:txt('[class*="salary"]'),
          link:'https://www.escapethecity.org'+href});
});return r.slice(0,25);})()"""

register(BrowserListSource(
    id="escape_city",
    enabled=False,  # 2026-08-25: board is live and the extractor works; `?q=` cannot carry a keyword search
    reprobed="2026-08-25",   # see the module docstring for what was found
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=800,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Escape the City example', 'https://www.escapethecity.org/search/jobs'),
    ],
))
