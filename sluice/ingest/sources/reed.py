"""Reed (reed.co.uk), contract. Declarative extractor JS + an example search (override via config).

2026-08-27: rebound to reed's `data-qa` hooks. Reed renders result cards with CSS-module
class names (`index-module_jobCard__<hash>`) whose hash suffix rotates on every deploy, so a
`[class*="..."]` selector is not a contract anyone can hold -- and measured on the live page,
the previous class-substring selectors returned NULL on every one of the 25 real cards for
location and salary, while the card selector itself matched 121 nodes because reed's own nav
container class contains the substring `job`. The result was a source reporting 20
healthy-looking rows a run with no location and no salary on any of them -- the "succeeding at
reading the wrong page" failure `core/health.py` exists to catch.

`blank` could not report it, for a reason that STOPS APPLYING with this change and is recorded
because it is the #207 blind spot rather than a property of this board: reed's company
high-water was 0.1, taken from a run whose extractor was already reading the wrong elements,
so the signal sat below `_BLANK_HW_MIN` and the check was switched off for exactly the source
that needed it. With company recovered below, reed's high-water climbs to ~1.0 on its first
post-fix run and reed moves INSIDE `blank`'s reach -- so a company collapse here IS drift from
now on, and should be read as such rather than dismissed.

`data-qa` is reed's test-hook attribute and is stable across the deploys the class hashes are
not. Measured 2026-08-27 over all 25 cards on the shipped example search: `job-card` 25/25,
`job-card-title` 25/25 (an `<a>`, so it carries the posting href too),
`job-metadata-location` 25/25, `job-metadata-salary` 25/25, `job-posted-by` 25/25.

COMPANY TAKES TWO TIERS, and the split matters when reading a rate. `company-name-link` -- the
linked company profile -- is on only 1 of those 25 cards, because reed lets an agency post
without linking one. The other 24 still name the poster in `job-posted-by`, as
`"<date> by <name>"`, and `_ReedSource.parse` recovers the name from it. So the FIELD is
~25/25 even though tier one alone is 1/25: do not read the tier-one rate as the field's, and
do not conclude from "1 of 25" that a low company rate is normal here. It is not.

That recovery lives in `parse`, not in the extractor JS, following `naukrigulf`'s precedent
for the same reason: it is a pure derivation from an already-extracted field, so putting it on
the pure side of the contract makes it reachable by the offline golden-fixture corpus, which
captures the JS OUTPUT and can never execute the JS itself. The extractor's job is to carry
`posted_by` out of the DOM; deciding what it means is this module's.

THE POSTING URL IS UNCHANGED by any of this, which is what keeps existing vault notes safe.
Both the old and new link cascades take the title anchor's href, and `Lead.dedup_key` is
`_norm_url(url)`, so an already-seen reed lead is still filtered by `seen.db` and never
reaches `Vault.upsert`. That matters because recovering `company` changes a lead's NOTE NAME
(`{company} - {title}`), and upsert decides create-vs-update by name: were the url to change
too, every reed note already in a vault would split into a duplicate at `status: new`,
including any already `applied`. Verified by comparing the pre-change extractor's emitted urls
against the live `job-card-title` hrefs -- identical, query string included.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = r"""
(()=>{
    const r=[];
    const fellBackFlags=[];  // parallel to r: whether row i used the unscoped fallback tier
    // The stable hook first, the old class-substring net only if it matches nothing. Keeping
    // the old net as a FALLBACK rather than deleting it is deliberate: if reed ever drops
    // `data-qa`, this still returns rows AND stamps them, so the run classifies as `fallback`
    // (a named reason that withholds the leads) instead of as a bare `zero` -- the exact
    // ambiguity #207 is about. No dominance ratio on this tier, unlike the link cascade
    // below: the card selector is all-or-nothing, so every returned row came from the same
    // tier and there are not two populations to compare.
    let cards = [...document.querySelectorAll('article[data-qa="job-card"]')];
    let cardFallback = false;
    if (!cards.length) {
        // Scoped to nodes that actually contain a POSTING link before the net is trusted.
        // Unscoped, this tier matches reed's nav chrome (its container class contains `job`),
        // so a search that genuinely returns zero results would push nav rows, stamp the
        // marker, and report `fallback` -- a breaker reason -- pointing a human at a selector
        // that is working perfectly. Requiring a `/jobs/` anchor keeps the genuine-rot case
        // (reed renames the element while real cards still link postings) stamping exactly as
        // it did, while an empty page now falls through to the honest `zero`.
        cards = [...document.querySelectorAll('article, .job-result, [data-testid="job-card"], div[class*="job"], div[class*="card"]')]
            .filter(c => c.querySelector('a[href*="/jobs/"]'));
        cardFallback = cards.length > 0;
    }
    cards.forEach(c=>{
        const titleEl = c.querySelector('[data-qa="job-card-title"]')
                     || c.querySelector('h2, h3, a[class*="title"], [class*="title"]');
        const t = titleEl?.textContent?.trim()||'';
        // Tier one only. The `job-posted-by` text is carried out RAW as `posted_by` and
        // turned into a company by `_ReedSource.parse` -- see the module docstring for why
        // that split is deliberate rather than an oversight.
        const co = c.querySelector('[data-qa="company-name-link"]')?.textContent?.trim()
                || c.querySelector('[class*="company"], [class*="employer"], .brand, [class*="brand"]')?.textContent?.trim()||'';
        const pb = c.querySelector('[data-qa="job-posted-by"]')?.textContent?.trim()||'';
        const lo = c.querySelector('[data-qa="job-metadata-location"]')?.textContent?.trim()
                || c.querySelector('[class*="location"], [class*="locality"]')?.textContent?.trim()||'';
        const sa = c.querySelector('[data-qa="job-metadata-salary"]')?.textContent?.trim()
                || c.querySelector('[class*="salary"], [class*="rate"], [class*="price"]')?.textContent?.trim()||'';
        let ln = '', usedFallback = false;
        if(titleEl && titleEl.tagName === 'A') ln = titleEl.href;
        if(!ln) ln = c.querySelector('a[href*="/job/"]')?.href||'';
        if(!ln) ln = c.querySelector('a[href*="job"]')?.href||'';
        // The last tier has no scoping at all -- it accepts ANY anchor in the card. Counted,
        // not stamped per-row (#156 review follow-up): the marker promotes to a SOURCE-level
        // `fallback` drift reason that withholds every lead from this run (BREAKER_REASONS in
        // ingest/engine.py), so one odd card whose markup genuinely differs from its
        // neighbours must not be enough to silence a source that is otherwise reading fine --
        // measured against reed's real page, a single sponsored/ad card was exactly this shape.
        if(!ln){ ln = c.querySelector('a')?.href||''; if(ln) usedFallback = true; }
        // Tracked in a PARALLEL array, not counted directly (#156 review follow-up, second
        // pass): the page can match far more than 20 cards, but only the first 20 PUSHED
        // rows are ever returned below. Counting every card that fell back -- rather than
        // only the ones among the RETURNED rows -- measured dominance against a population
        // the caller never actually sees, in either direction: a page where the fallback
        // hits are concentrated in the first 20 could under-count if diluted by clean cards
        // further down the page; a page where they're concentrated further down could
        // over-count against a returned set that is actually clean.
        if(t&&t.length>5){
            r.push({title:t, company:co, posted_by:pb, location:lo, link:ln, salary:sa});
            fellBackFlags.push(usedFallback);
        }
    });
    // Slice FIRST, measure the SLICE: the dominance ratio must be computed over exactly
    // the rows this function actually returns, matching `fellBackFlags`' own indices.
    const returned = r.slice(0,20);
    const returnedFallbacks = fellBackFlags.slice(0,20).filter(Boolean).length;
    // Stamped only when the unscoped tier carried MOST of the RETURNED page, not one card
    // -- that is the shape a real selector rot takes (title/company/location keep working;
    // only the LINK cascade's scoped tiers stop matching), distinct from an isolated outlier.
    // Row floor of 8, matching `blank`'s own small-sample discipline in `_lead_rates`
    // (review follow-up): without it, a narrow search returning 1-3 rows can trip
    // `1 > 0.5` on a single odd card and withhold real leads over noise, not a rot.
    if(returned.length >= 8 && returnedFallbacks > returned.length / 2) {
        returned[0].degraded = 'link-fallback';
    }
    // Written AFTER the link marker so that when both fire this one survives. Both assign the
    // same property on the same row, so it is plain last-write-wins here -- NOT anything
    // `_first_degraded` does, which only picks the first marked ROW. The order is deliberate:
    // a missing `data-qa` contract is the more upstream cause and names what a human has to
    // go and look at, where `link-fallback` would name its symptom.
    if(cardFallback && returned.length) {
        returned[0].degraded = 'card-fallback';
    }
    return returned;
})()
"""


def _company_from_posted_by(text: str) -> str:
    """`"<date> by <name>"` -> `"<name>"`, or `""` when there is no poster clause.

    Cuts at the FIRST ` by `, so a company whose own name contains ` by ` keeps the rest of
    it. A value with no ` by ` at all is not a poster line, and yields `""` rather than the
    original text: writing a bare date into the company field would be worse than leaving it
    blank, because a date is a plausible-looking company to every downstream consumer and
    would become part of the lead's note name and dedup identity.

    Case-insensitive on the MARKER only. The name keeps its own casing -- it is the value
    that reaches the vault, and normalising it here would make the note name disagree with
    the board.
    """
    lowered = (text or "").lower()
    marker = lowered.find(" by ")
    return "" if marker < 0 else text[marker + len(" by "):].strip()


class _ReedSource(BrowserListSource):
    def parse(self, raw, search):
        # Normalise defensively, matching `BrowserListSource.health_hint`'s own guard: a
        # non-dict `raw` must not raise here any more than it may there.
        raw = raw if isinstance(raw, dict) else {}
        rows = []
        for row in (raw.get("result") or []):
            if isinstance(row, dict) and not (row.get("company") or "").strip():
                row = {**row, "company": _company_from_posted_by(row.get("posted_by") or "")}
            rows.append(row)
        # Delegates rather than reimplementing: `super().parse` is what applies
        # `_row_to_lead`, the title-non-empty filter and the `posting_paths` allowlist, and a
        # `parse` override that skips it silently loses all three.
        return super().parse({**raw, "result": rows}, search)


register(_ReedSource(
    id="reed",
    # #153: reed interleaves sponsored COURSE cards into the jobsearch results page, and
    # the extractor's link cascade ends in "any anchor in the card", so it took them. A
    # course is /courses/<slug>/<id>; a job is /jobs/<slug>/<id>. Such notes reached a
    # production vault and parked in `needs_review` forever -- a course card has no
    # company, so the note scores 0, never resolves, and burns an LLM call on every triage
    # pass while diluting the one queue a human is meant to scan.
    #
    # An ALLOWLIST, not a `/courses/` denylist: the denylist closes only the card type
    # already observed, and a results page can carry sponsored content, profile prompts or
    # anything else reed decides to interleave next.
    posting_paths=("/jobs/",),
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('Reed example', 'https://www.reed.co.uk/jobs/software-developer-jobs-in-london?sortby=datecreated'),
    ],
))
