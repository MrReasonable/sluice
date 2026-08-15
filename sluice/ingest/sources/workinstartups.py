"""WorkInStartups. A HEAD pre-check skips the Camofox tab entirely when we're
being 429'd (this board rate-limits aggressively), and a cookie-consent banner
needs dismissing before the extractor runs. Extractor + cookie-dismiss + searches
lifted verbatim from the legacy scanner.
"""
from sluice.core.log import get_logger
from sluice.core.resilience import head_rate_limited
from sluice.ingest.base import BrowserListSource, Search
from sluice.ingest.sources import register

_log = get_logger("workinstartups")

_COOKIE_JS = """(()=>{const b=document.querySelector('button:has-text("Accept")')||document.querySelector('[id*="accept"]');if(b)b.click();return!!b})()"""
_JS = r"""(()=>{const r=[];document.querySelectorAll('h2 a[href*="/details/"]').forEach(a=>{const title=a.textContent.trim();if(title.length<5)return;const card=a.closest('article')||a.parentElement?.parentElement;const fullText=card?.textContent||'';const idx=fullText.indexOf(title);if(idx<0)return;const after=fullText.substring(idx+title.length).replace(/Estimated:|TOP MATCH|ABOVE AVERAGE SALARY|CLOSING SOON|EASY APPLY|REMOTE|NEW/gi,'');const lines=after.split(/[\n•·]/).map(s=>s.trim()).filter(Boolean);const company=lines[0]||'';const rest=lines.slice(1).join(' ');const salMatch=rest.match(/£[\d,]+(?:k)?(?:\s*[-–]\s*£[\d,]+(?:k)?)?/i);r.push({title,company,location:rest.replace(/£[\d,].*/,'').trim(),salary:salMatch?salMatch[0]:'',link:a.href})});return r.slice(0,30)})()"""


class _WorkInStartupsSource(BrowserListSource):
    def fetch(self, ctx, search: Search) -> dict:
        """HEAD-check the search URL first; if the board is 429ing us, skip the
        browser tab entirely rather than burn a Camofox slot on a blocked page."""
        retry_after = head_rate_limited(search.url) if search.url else None
        if retry_after is not None:
            _log.warning("%s rate-limited (retry-after %ss), skipping", search.label, retry_after)
            return {"result": [], "landed": "", "requested": search.url, "skipped": True}
        return super().fetch(ctx, search)

    def health_hint(self, raw: dict) -> dict:
        hint = super().health_hint(raw)
        skipped = bool(raw.get("skipped"))
        hint["markers"] = {"skipped": skipped}
        # ALSO as a signal, because `engine.py` strips `markers` before the classifier sees
        # them -- so the clearest explanation this codebase records ("we did not even look,
        # the board was rate-limiting") never reached `detect_drift` and a 429 retired the
        # source as an unexplained zero. A rate-limit is recoverable, so it defers retirement.
        if skipped:
            hint["blocked"] = "rate-limited"
        return hint


register(_WorkInStartupsSource(
    id="workinstartups",
    extractor_js=_JS,
    dismiss_js=_COOKIE_JS,
    wait=5, scrolls=2, scroll_amount=600,
    searches_spec=[
        ('WorkInStartups example', 'https://workinstartups.com/search?q=software+developer'),
    ],
))
