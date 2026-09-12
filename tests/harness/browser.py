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
import threading

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
        # #309 made the Fetcher seam concurrent (triage fetches dossiers over a pool), so
        # this fake now stands in for something callers may drive from several threads.
        # `self._seq += 1` is read-modify-write and is NOT atomic, so two threads could be
        # handed the SAME tab id -- and each would then read back the other's url through
        # `self._tabs`, which is the one thing a browser fake must never do: the tests
        # that would break are the ones asserting a tab landed where it was sent.
        #
        # LATENT, and deliberately guarded anyway. Nothing drives this fake concurrently
        # today (the #309 tests inject a plain closure), and the race is not currently
        # REPRODUCIBLE either: a guard written for it passed 5 runs out of 5 against the
        # unsynchronised version, even with sys.setswitchinterval at 1e-6, because CPython
        # does not preempt inside a bump this short. So there is deliberately NO test --
        # one that cannot fail against the defect it names asserts nothing.
        #
        # The lock stays because that atomicity is an accident of today's interpreter, not
        # a language guarantee: nothing in the data model promises it, and a free-threaded
        # build removes it outright. Correctness by construction, on a fake where a lock
        # costs nothing.
        self._lock = threading.Lock()

    def create_tab(self, url):
        with self._lock:
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
