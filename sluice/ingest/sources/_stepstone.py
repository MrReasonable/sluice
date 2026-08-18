"""Shared result-card extractor for the StepStone-platform UK boards.

CWJobs and TotalJobs are the same product behind different brands: a CWJobs search
returns totaljobs.com links, and both render results with identical markup. They had
separate, separately-rotted copies of the extractor, so this holds the one copy.

Underscore-prefixed on purpose: `sources/__init__.py`'s autoloader skips modules starting
with "_", so this is a helper rather than a registered source. It also deliberately contains
no URL literals, because `test_job_board_defaults_cover_every_shipped_source_host` scrapes
every http(s) URL in this directory and requires a matching job-board denylist entry. A URL
written in PROSE here counts: that guard has no way to tell an example from a real one.

WHAT THE MARKUP LOOKS LIKE, and why the selectors are shaped this way (measured live
2026-08-18): each result is `[data-testid="job-card-content"]` and the title sits on
`a[data-testid="job-item-title"]`. Company, location and salary carry ONLY hashed class
names (`res-ewgtgq`, `res-14nrdsm`, ...) which are regenerated per front-end deploy, so
matching on them would rot again within weeks. They are read off `data-genesis-element`
instead, which is the platform's own semantic attribute:

  - company  the leaf BASE node whose text is not the title (the title is wrapped in a
             BASE node too, hence a comparison rather than an index)
  - location } both are TEXT nodes, told apart by whether the text looks like pay, since
  - salary   } the platform gives them no distinguishing attribute

The previous selectors (`.job, .job-result, article, [data-testid="job-card"]`) matched
nothing at all, so every run fell through to the bare-anchor fallback. That fallback cannot
see a company, which is why 185 CWJobs leads in a fortnight arrived with `company: ""`, and
why the nav link "Related Jobs" was ingested as a vacancy.
"""

# Kept as one string used by both boards. `limit` differed between them (25 vs 20) and is
# passed in rather than baked, so neither board's behaviour changes by adopting this.
def extractor_js(limit: int = 25) -> str:
    return r"""
(()=>{
    const NAV=/^(related jobs|similar jobs|more jobs|all jobs|browse jobs|saved jobs)$/i;
    const PAY=/[£$€]|\bper (day|hour|week|month|annum|year)\b|\bp\.?a\.?\b|\d\s*k\b/i;
    const r=[];
    const seen=new Set();
    document.querySelectorAll('[data-testid="job-card-content"]').forEach(c=>{
        const a=c.querySelector('a[data-testid="job-item-title"]');
        if(!a) return;
        const title=(a.textContent||'').replace(/\s+/g,' ').trim();
        const link=(a.href||'').split('?')[0];
        if(!title||title.length<5||NAV.test(title)||!link||seen.has(link)) return;
        seen.add(link);
        let company='';
        c.querySelectorAll('[data-genesis-element="BASE"]').forEach(e=>{
            if(company||e.children.length) return;
            const t=(e.textContent||'').replace(/\s+/g,' ').trim();
            if(t&&t!==title&&t.length<90) company=t;
        });
        let location='', salary='';
        c.querySelectorAll('[data-genesis-element="TEXT"]').forEach(e=>{
            if(e.children.length) return;
            const t=(e.textContent||'').replace(/\s+/g,' ').trim();
            if(!t||t.length>70) return;
            if(PAY.test(t)){ if(!salary) salary=t; }
            else if(!location) location=t;
        });
        r.push({title, company, location, link, salary});
    });
    if(r.length===0){
        // Degraded fallback for a future card-markup change: anchors alone cannot yield a
        // company, so leads arrive blank-companied. Nav links are excluded so the fallback
        // cannot reintroduce "Related Jobs" as a vacancy.
        document.querySelectorAll('a[href*="/job/"]').forEach(a=>{
            const t=(a.textContent||'').replace(/\s+/g,' ').trim();
            const link=(a.href||'').split('?')[0];
            if(t.length>8&&!NAV.test(t)&&!seen.has(link)){
                seen.add(link);
                r.push({title:t, company:'', location:'', link, salary:''});
            }
        });
    }
    return r.slice(0, LIMIT);
})()
""".replace("LIMIT", str(int(limit)))
