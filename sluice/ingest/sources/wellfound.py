"""Wellfound (wellfound.com, ex-AngelList) - startup EM roles (permanent).
Declarative extractor JS + an example search (override via config).

`company_from_url` (#109) is a tier-1, free URL-pattern extractor: a Wellfound
job/company URL carries the hiring company as a `/company/<slug>/...` path
segment, delimited by the literal `/company/` segment on one side and a real
URL boundary -- `/`, `?`, `#`, or end of string -- on the other. Unambiguous, so
this abstains (returns None) for any URL shape that does not carry that segment,
rather than guess a split point. Verified against a real `job-sluice ingest
test-source wellfound --raw` capture, not committed from the illustrative
pattern alone: real company cards link to a BARE `/company/<slug>` with no
trailing path at all (the end-of-string boundary), and real job-posting cards
link to `/jobs/<id>-<title-slug>` with no `/company/` segment whatsoever, so
they correctly abstain rather than needing a split.

The trailing `(?=[/?#]|$)` is what makes "delimited" true rather than merely
intended. Without it the slug's `[a-z0-9-]+` simply stops at the first character
it cannot consume and the match SUCCEEDS anyway, so a URL whose segment does not
end where the slug does -- `/company/example-co.invalid` -- would yield
"Example Co" from a host this extractor has no claim over. Abstaining is the
only safe answer for a shape the capture never showed: tier 2 is still there,
and a wrong tier-1 name is written to the lead as if it were proven.
"""
import re
from urllib.parse import urlparse

from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('a[href*="/jobs/"]').forEach(a=>{const t=a.querySelector('h2,h3,div[class*="title"]')?.textContent?.trim()||a.textContent.trim();const p=a.closest('div,li');const co=p?.querySelector('div[class*="company"], span[class*="company"]')?.textContent?.trim()||'';if(t&&t.length>3&&!r.find(x=>x.title===t))r.push({title:t,company:co,location:'',link:a.href,salary:''})});return r.slice(0,15)})()"""

# The host half of the pattern, named rather than inlined: a source plugin must
# hardcode the board it scrapes, but tests/** may not carry a real domain, so
# tests/test_parsers.py substitutes THIS substring for a synthetic host and runs
# the shipped regex against it. Keeping it a named constant means that swap is
# anchored on a symbol (renaming it breaks the test loudly) instead of on a
# literal copied into the suite.
_HOST_RE = r"(?:www\.)?wellfound\.com"
_COMPANY_URL_RE = re.compile(
    rf"^https?://{_HOST_RE}/company/([a-z0-9-]+)(?=[/?#]|$)")


# The extractor's own selector (`_JS` above) now matches ONLY `a[href*="/jobs/"]` anchors.
# `_is_company_card` is retained as defence-in-depth against nested and edge-case DOM shapes:
# a job card's own DOM sometimes nests a company-profile link the extractor cannot distinguish
# at scrape time (a `/company/`-shaped URL), or a future DOM change could reintroduce the
# ambiguity. Rows carrying a `/company/` link and no role text are not leads and must be dropped
# before `_row_to_lead` ever sees them. The filter uses the measured discriminator this module's
# own docstring already records for `company_from_url`: a real job card links `/jobs/<id>-<slug>`,
# a real company card links a BARE `/company/<slug>` with no trailing path.
_COMPANY_CARD_PATH_RE = re.compile(r"^/company/[a-z0-9-]+$")


def _is_company_card(url: str) -> bool:
    """True only for the exact measured company-card shape. The asymmetry that shapes this
    function: a wrong *keep* costs one junk lead a human dismisses in one glance; a wrong
    *drop* silently bins a real job with no trace. So the regex matches only the end-anchored
    bare slug -- anything not byte-shaped exactly like that capture (a trailing `/jobs/...`, a
    trailing slash, an unparseable URL) is kept rather than guessed at.

    Path-only and deliberately host-blind: `parse` only ever hands this rows THIS source's own
    extractor already collected, so re-checking the host adds nothing, and anchoring on the
    real host would make the sanitized `example.com` golden fixture unable to exercise the
    filter at all.
    """
    try:
        return bool(_COMPANY_CARD_PATH_RE.match(urlparse(url or "").path))
    except ValueError:
        return False   # unparseable is not the measured card shape either -- keep the row


class WellfoundSource(BrowserListSource):
    def parse(self, raw, search):
        # Normalise defensively, matching `BrowserListSource.health_hint`'s own guard: a non-
        # dict `raw` must not raise here any more than it may there.
        raw = raw if isinstance(raw, dict) else {}
        rows = [row for row in (raw.get("result") or [])
                if not (isinstance(row, dict) and _is_company_card(row.get("link") or ""))]
        return super().parse({**raw, "result": rows}, search)

    # company_from_url stays exactly as it is: a separate, triage-time hook that resolves a
    # company NAME from a URL that already carries one. This filter runs earlier, at parse
    # time, on rows that carry no role at all -- the two never overlap.
    def company_from_url(self, url: str) -> str | None:
        m = _COMPANY_URL_RE.match(url or "")
        if not m:
            return None
        return m.group(1).replace("-", " ").title() or None


register(WellfoundSource(
    id="wellfound",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Wellfound example', 'https://wellfound.com/role/r/software-engineer'),
    ],
))
