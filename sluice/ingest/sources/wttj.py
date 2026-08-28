"""Welcome to the Jungle (www.welcometothejungle.com), the authenticated matches feed.

2026-08-28: rebuilt as a LIST source. This was a `CarouselSource` reading Otta's app at
`app.welcometothejungle.com/jobs`, which showed one matched job at a time and advanced with a
next-button. WTTJ has since migrated the matches surface to its own site at
`/en-GB/jobs-matches`, which renders every match as a card on one page.

Measured on the live board while logged in, which is also why the old model is not merely
inconvenient but wrong for this surface:

  - 10 job cards on the page at once, against a carousel that yielded one per advance.
  - Scrolling does NOT paginate. From `scrollY` 0 to 2874 of a 2986px document the unique job
    count stayed at 10 and `scrollHeight` never grew, so `scrolls` here is about letting the
    below-fold cards render, not about fetching more.
  - No next-button anywhere in the DOM. The only per-card actions are `Save` and `Not for me`,
    which is the distinction that matters: reading this page consumes nothing, where the old
    carousel's advance was believed to consume the match.

BOTH SURFACES ARE STILL LIVE. The legacy Otta app still renders its carousel, so this is a
CHOICE of surface rather than a forced migration -- and it has to be a choice, because the two
spell the same posting with different URLs (`app.../jobs/<id>` versus
`www.../en-GB/companies/<co>/jobs/<slug>`). `Lead.dedup_key` is `_norm_url`, so scraping both
would not dedup: the same job would land twice in the vault under two names.

AUTH is separate per host, and that is not obvious: signing into the Otta app does NOT sign you
into `www`. The `_otta_session` cookie is set on `.welcometothejungle.com` and so IS sent here,
but `www` runs its own session (`wttj_api_session_key`) and redirects to
`/en-GB/authenticate/signin` without it. No `auth_probe_js` is needed for that: the landed path
carries `authenticate` and `signin`, both already in `core/health.py`'s `_LOGIN_SEGMENTS`, so a
logged-out run classifies as `login` rather than as a bare zero.

The feed is PREFERENCE-driven, not query-driven -- it is the user's own matches, filtered by the
preferences on their account, and the shipped search carries no query string at all. That is why
the example search is a bare path: there is no keyword or location for this repo to have an
opinion about.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    // A posting link is `/<locale>/companies/<company>/jobs/<slug>`. Matched anywhere in the
    // path rather than anchored at the start, because the locale segment is `en-GB` -- an
    // anchored `^/[a-z-]+/` fails on the uppercase `GB` and returns ZERO rows, which is
    // exactly the silent-empty failure this board already cost us once.
    const POSTING=/\/companies\/([^\/]+)\/jobs\//;
    // The card's employee-count chip, used as a positional ANCHOR for location below.
    const EMP=/^\s*\d[\d,\s]*\+?\s*employees?\s*$/i;
    const r=[], seen=new Set();
    document.querySelectorAll('a[href]').forEach(a=>{
        const href=a.getAttribute('href')||'';
        if(!POSTING.test(href)) return;
        // `a.href` (absolute, origin included) rather than the attribute, and the query
        // dropped: each card renders TWICE -- once for the desktop breakpoint and once for
        // mobile -- so the same posting appears as two anchors and must dedup to one row.
        const link=a.href.split('?')[0];
        if(seen.has(link)) return; seen.add(link);
        const title=(a.textContent||'').replace(/\s+/g,' ').trim();
        if(title.length<3) return;
        // Walk out to the repeating card. Bounded, and keyed on the card carrying enough text
        // to BE a card: the class names here are CSS-module hashes (`_root_17p74_2`) that
        // rotate on deploy, so a class selector is not a contract -- the same rot that had
        // reed reading its own nav container.
        let card=a, hops=0;
        while(card && hops<7 && (card.textContent||'').trim().length<80){
            card=card.parentElement; hops++;
        }
        if(!card) return;
        // Company is the element immediately after the title anchor -- the card renders
        // `<a>Title</a><p>Company</p>` as siblings. Falls back to the card's first <p>.
        let company='';
        const sib=a.nextElementSibling;
        if(sib && sib.tagName==='P') company=(sib.textContent||'').replace(/\s+/g,' ').trim();
        if(!company){
            const p=card.querySelector('p');
            if(p) company=(p.textContent||'').replace(/\s+/g,' ').trim();
        }
        // Location lives in an UNCLASSED <span>, so it cannot be selected directly. Taken as
        // the span immediately BEFORE the employee-count chip, which is a structural anchor
        // rather than a vocabulary: the alternative was to identify the location span by
        // eliminating known contract types and remote policies, which would ship this repo an
        // opinion about employment shapes it deliberately does not hold.
        //
        // Measured against all 10 live cards: 9 resolved, 1 has no employee chip and yields
        // "". Deliberately NOT falling back to the URL slug's own `_<place>_` segment even
        // though one exists -- on a real card the slug said `france` where the rendered card
        // said `London`, so the slug is the posting's home office and the span is the
        // location THIS match is offered in. A wrong location is worse than a blank one: it
        // reaches `_norm_location`, and so the lead's identity and every location gate.
        let location='';
        const spans=[...card.querySelectorAll('span')]
            .map(e=>(e.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean);
        const ei=spans.findIndex(s=>EMP.test(s));
        if(ei>0) location=spans[ei-1];
        r.push({title, company, location, link, salary:''});
    });
    return r.slice(0,40);
})()
"""

register(BrowserListSource(
    id="wttj",
    extractor_js=_JS,
    # 10s because the feed is client-rendered and the cards are not in the initial HTML; a
    # shorter wait returns an empty page, which would read as a genuine zero.
    #
    # ONE scroll, and it is not pagination -- measured, the row count and document height are
    # unchanged by scrolling. It is there so cards below the fold render before the extractor
    # reads them; a source that scrolled for MORE rows here would be encoding a belief the
    # board does not support.
    wait=10, scrolls=1, scroll_amount=1500,
    # No `posting_paths`. The abstaining default is right here rather than lazy: the extractor
    # already admits a row only if its href matches `/companies/<co>/jobs/`, so a prefix
    # allowlist would have to be the bare locale (`/en-GB/`) to avoid rejecting real postings
    # -- which admits every page on the site and is a guard in name only. See `admits_path`.
    extra={"job_type": "perm"},
    searches_spec=[
        ('WTTJ matches', 'https://www.welcometothejungle.com/en-GB/jobs-matches'),
    ],
))
