"""A scripted stand-in for the Camofox browser client.

Serves canned DOM rows keyed by URL so a shipped `BrowserListSource` runs its
real `fetch()` and its real, pure `parse()` -- the whole reason parsers are
testable offline. It implements ONLY the four methods `BrowserListSource.fetch`
drives (`create_tab` / `evaluate` / `scroll` / `close_tab`), each returning the
`{"result": ...}` envelope the real client returns. A `fetch` that reached for
any other method would `AttributeError` here rather than passing silently, which
is the point: the fake is faithful to the surface it stands in for.

Keying on the URL (not on the extractor JS string) is what lets a shipped
source's real `extractor_js` flow through this fake untouched: whatever script
the source runs, this returns that URL's canned rows.
"""
from sluice.core import plugins

FETCHER_SEAM = "fetcher"
FETCHER_NAME = "scripted"


class ScriptedBrowserClient:
    def __init__(self, pages, *, jd_text="Synthetic job description for the harness."):
        # pages: {url: [row_dict, ...]} -- the rows a board's extractor would return.
        self.pages = {url: list(rows) for url, rows in dict(pages).items()}
        # What `document.body.innerText` yields on a job page -- the dossier cache
        # reads this to build the JD the judge sees. One synthetic body is enough;
        # the judge is stubbed, so only its presence matters, not its content.
        self.jd_text = jd_text
        self._tabs: dict[str, str] = {}
        self._seq = 0
        self.closed: list = []

    def create_tab(self, url):
        self._seq += 1
        tid = f"tab-{self._seq}"
        self._tabs[tid] = url
        return tid

    def evaluate(self, tid, js):
        url = self._tabs.get(tid, "")
        # The source reads back where the tab landed (health/drift signal).
        if js.strip() == "location.href":
            return {"result": url}
        # The dossier cache pulls JD text with exactly this probe (core/app.py's
        # dossier fetch). Match it EXACTLY, not by substring: shipped extractors
        # (wttj, eighty_k) embed `innerText` inside their own extractor JS, and a
        # substring test would hand those sources jd_text instead of their canned
        # rows -- parse() would then see a string and silently yield zero leads.
        if js.strip() == "document.body.innerText":
            return {"result": self.jd_text}
        # Anything else is the extractor JS (or a dismiss no-op, whose result the
        # source ignores): hand back this URL's canned rows.
        return {"result": list(self.pages.get(url, []))}

    def scroll(self, tid, amount):
        return None

    def close_tab(self, tid):
        self.closed.append(self._tabs.pop(tid, None))
        return None


def install_scripted_fetcher(client):
    """Register `client` under the real `fetcher` seam as `scripted`.

    Registered through the public `register()` API rather than injected via a
    constructor override, deliberately: that exercises `plugins.get` and the
    `fetcher` config-key wiring, which a direct injection would leave untested --
    much of the point of an e2e run. (The registry has no enumerating guard, so
    an extra name is safe; the store and backend registries, which DO assert
    exact membership, are the ones a fake must never join -- see the harness
    backend, which is passed via `Sluice(backend=...)`.)"""
    plugins.register(FETCHER_SEAM, FETCHER_NAME, lambda config: client)
    return client
