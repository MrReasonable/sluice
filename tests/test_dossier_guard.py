"""The dossier fetch closure's SSRF guard (#18)."""
import pytest

from sluice.core import urlguard
from sluice.core.app import Sluice
from sluice.core.config import Config

GLOBAL_ADDR = "192.88.99.1"     # RFC 3068, withdrawn by RFC 7526: global, no operator


_UNSET = object()


class _Tab:
    """A fake Fetcher recording its exact probe sequence."""

    def __init__(self, landed="https://jobs.invalid/x", body="JD BODY",
                 landed_result=_UNSET):
        self.landed, self.body, self.landed_result = landed, body, landed_result
        self.calls = []

    def create_tab(self, url):
        self.calls.append(("create_tab", url))
        return "tab-1"

    def evaluate(self, tid, js):
        self.calls.append(("evaluate", js))
        if js == "location.href":
            if self.landed_result is not _UNSET:
                return self.landed_result
            return {"result": self.landed}
        return {"result": self.body}

    def scroll(self, tid, amount):
        self.calls.append(("scroll", amount))

    def close_tab(self, tid):
        self.calls.append(("close_tab", tid))


@pytest.fixture
def role(titles):
    """A synthetic job title from the seeded pool, matching the convention in
    test_app_operations.py's dossier tests. The repo generates titles rather than
    hardcoding them so no real person's preferences leak into the suite."""
    return titles[0][0]


def _cache(tmp_path, fetcher, *, resolve=None, allow=()):
    cfg = Config()
    cfg.dossier_allow_hosts = list(allow)
    app = Sluice(cfg, fetcher=fetcher,
                 resolve_host=resolve or (lambda h: [GLOBAL_ADDR]))
    return app.dossier_cache(str(tmp_path), ttl_days=7)


def test_an_allowed_url_fetches_and_probes_in_order(tmp_path, role):
    """The positive control every absence assertion below is paired with."""
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"
    assert tab.calls == [
        ("create_tab", "https://jobs.invalid/x"),
        ("evaluate", "location.href"),
        ("evaluate", "document.body.innerText"),
        ("close_tab", "tab-1"),
    ]


def test_a_blocked_url_never_opens_a_tab(tmp_path, role):
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.BLOCKED_ADDRESS
    assert tab.calls == [], "no tab may be opened for a url we already refused"


def test_a_redirect_to_a_blocked_host_discards_the_body(tmp_path, role):
    def _resolve(host):
        return ["127.0.0.1"] if host == "internal.invalid" else [GLOBAL_ADDR]
    tab = _Tab(landed="http://internal.invalid/admin")
    cache = _cache(tmp_path, tab, resolve=_resolve)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.LANDED_BLOCKED
    probes = [c for c in tab.calls if c[0] == "evaluate"]
    assert probes == [("evaluate", "location.href")], \
        "the body must never be read from a blocked destination"
    assert ("close_tab", "tab-1") in tab.calls


@pytest.mark.parametrize("landed", ["", "about:blank"])
def test_an_unnavigated_tab_is_refused(tmp_path, role, landed):
    """Camofox's navigate awaits page.goto, so the tab is never at about:blank when
    we probe. This asserts that assumption rather than trusting it: if a different
    fetcher or a changed server ever violates it, we must fail closed rather than
    check a url the browser never went to."""
    tab = _Tab(landed=landed)
    cache = _cache(tmp_path, tab)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.NOT_SETTLED


@pytest.mark.parametrize("bad", [None, "not-a-dict", {}, {"result": 42}])
def test_an_unreadable_landed_url_is_refused(tmp_path, role, bad):
    tab = _Tab(landed_result=bad)
    cache = _cache(tmp_path, tab)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.LANDED_UNREADABLE


def test_a_tab_that_never_opens_is_refused(tmp_path, role):
    """Previously fell through to a cached empty dossier -- see the closure comment."""
    class _NoTab(_Tab):
        def create_tab(self, url):
            self.calls.append(("create_tab", url))
            return None
    tab = _NoTab()
    with pytest.raises(urlguard.DossierBlocked) as ei:
        _cache(tmp_path, tab).get_or_build(
            {"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.NO_TAB


def test_an_unreadable_body_is_refused(tmp_path, role):
    """Ditto: a non-string body must not become an empty JD nobody can distinguish
    from a real one."""
    class _BadBody(_Tab):
        def evaluate(self, tid, js):
            self.calls.append(("evaluate", js))
            if js == "location.href":
                return {"result": self.landed}
            return {"result": None}
    tab = _BadBody()
    with pytest.raises(urlguard.DossierBlocked) as ei:
        _cache(tmp_path, tab).get_or_build(
            {"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE


def test_a_lead_with_no_url_is_unchanged(tmp_path, role):
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "" and tab.calls == []


def test_the_allowlist_admits_a_private_host(tmp_path, role):
    tab = _Tab(landed="http://jobs.invalid/x")
    cache = _cache(tmp_path, tab, resolve=lambda h: ["10.0.0.1"],
                   allow=["jobs.invalid"])
    d = cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"


def test_dossier_blocked_carries_no_host_or_url(tmp_path, role):
    """cv/engine.py:70 logs str(e) verbatim -- the #67 leak shape."""
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "http://secret-host.invalid/path?token=x",
                            "company": "Aye", "role": role})
    msg = str(ei.value)
    assert "secret-host" not in msg and "token" not in msg and "://" not in msg


def test_a_production_shaped_sluice_fetches(tmp_path, role):
    """cli.py builds `Sluice(config)` -- no injected collaborators at all.

    The closure must touch nothing that is None in production. A previous draft
    reached for self._sleep, which IS None there, and would have raised TypeError
    on the first cache miss of every real run while this suite stayed green.
    """
    tab = _Tab()
    cfg = Config()
    app = Sluice(cfg, fetcher=tab)          # no sleep=, today=, resolve_host=
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    # The real resolver would be used, so the DNS guard fires -- that is the point:
    # it proves the closure got all the way to resolution without an attribute error.
    from tests.conftest import DnsUsedInTests
    with pytest.raises(DnsUsedInTests):
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
