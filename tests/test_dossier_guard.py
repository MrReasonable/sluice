"""The dossier fetch closure's SSRF guard (#18)."""
import pytest

from sluice.core import urlguard
from sluice.core.app import Sluice, _LD_JSON_JS
from sluice.core.config import Config
from tests.harness.config import FIXTURE_ADDR as GLOBAL_ADDR   # the ONE sanctioned
# globally-routable fixture address (RFC 3068, withdrawn by RFC 7526: global, no
# operator) -- defined once in tests/harness/config.py so this file and
# test_app_operations.py don't each hardcode the literal separately.


_UNSET = object()


class _Tab:
    """A fake Fetcher recording its exact probe sequence."""

    def __init__(self, landed="https://jobs.invalid/x", body="JD BODY",
                 landed_result=_UNSET, title="", ld_json=""):
        self.landed, self.body, self.landed_result = landed, body, landed_result
        self.title, self.ld_json = title, ld_json
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
        if js == "document.title":
            return {"result": self.title}
        if js == _LD_JSON_JS:
            return {"result": self.ld_json}
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
        ("evaluate", "document.title"),
        ("evaluate", _LD_JSON_JS),
        ("close_tab", "tab-1"),
    ]


def test_page_title_and_structured_data_are_captured_into_the_dossier(tmp_path, role):
    tab = _Tab(title="Staff Engineer at Example Co | Example Board",
              ld_json='{"@type": "JobPosting"}')
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["page_title"] == "Staff Engineer at Example Co | Example Board"
    assert d["structured_data"] == '{"@type": "JobPosting"}'


def test_a_lead_with_no_url_gets_blank_page_title_and_structured_data(tmp_path, role):
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"company": "Aye", "role": role})
    assert d["page_title"] == "" and d["structured_data"] == ""


def test_a_blocked_url_never_opens_a_tab(tmp_path, role):
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.BLOCKED_ADDRESS
    assert tab.calls == [], "no tab may be opened for a url we already refused"
    # A POLICY refusal, not a transport failure -- must stay a plain DossierBlocked,
    # paired below with the three slugs that ARE DossierUnavailable.
    assert not isinstance(ei.value, urlguard.DossierUnavailable)


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
    assert not isinstance(ei.value, urlguard.DossierUnavailable), "a policy refusal"


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
    # Deleting close_tab from this branch (or from LANDED_UNREADABLE below) leaves the
    # suite green otherwise -- _Tab already records every call and tab is already
    # local here, so there is no excuse for this to go unwitnessed.
    assert ("close_tab", "tab-1") in tab.calls
    # NOT_SETTLED means the blocking-navigate assumption was violated, not that
    # Camofox itself failed -- it is not one of the three DossierUnavailable slugs.
    assert not isinstance(ei.value, urlguard.DossierUnavailable)


@pytest.mark.parametrize("bad", [None, "not-a-dict", {}, {"result": 42}])
def test_an_unreadable_landed_url_is_refused(tmp_path, role, bad):
    tab = _Tab(landed_result=bad)
    cache = _cache(tmp_path, tab)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.get_or_build({"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert str(ei.value) == urlguard.LANDED_UNREADABLE
    assert ("close_tab", "tab-1") in tab.calls
    # A transport failure (Camofox returned an unreadable {"error": ...} envelope),
    # not a policy decision -- see urlguard.DossierUnavailable.
    assert isinstance(ei.value, urlguard.DossierUnavailable)


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
    assert isinstance(ei.value, urlguard.DossierUnavailable)


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
    assert isinstance(ei.value, urlguard.DossierUnavailable)


def test_a_transport_failure_logs_as_failed_not_refused(tmp_path, role, caplog):
    """The wording distinction from the operator's point of view: NO_TAB here means
    Camofox itself did not answer, not that the guard made a policy decision. Logging
    it as "refused" would point an operator at an allowlist that cannot fix a dead
    browser server."""
    class _NoTab(_Tab):
        def create_tab(self, url):
            self.calls.append(("create_tab", url))
            return None
    tab = _NoTab()
    with caplog.at_level("WARNING"):
        with pytest.raises(urlguard.DossierUnavailable):
            _cache(tmp_path, tab).get_or_build(
                {"url": "https://jobs.invalid/x", "company": "Aye", "role": role})
    assert "failed" in caplog.text
    assert "refused" not in caplog.text


def test_a_policy_refusal_logs_as_refused_not_failed(tmp_path, role, caplog):
    """The paired control: an ordinary blocked-address refusal is the guard actually
    deciding something, and must keep its original wording."""
    tab = _Tab()
    cache = _cache(tmp_path, tab, resolve=lambda h: ["127.0.0.1"])
    with caplog.at_level("WARNING"):
        with pytest.raises(urlguard.DossierBlocked) as ei:
            cache.get_or_build({"url": "http://jobs.invalid/x", "company": "Aye", "role": role})
    assert not isinstance(ei.value, urlguard.DossierUnavailable)
    assert "refused" in caplog.text
    assert "failed" not in caplog.text


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


# --- consumer behaviour ------------------------------------------------------
# Why raising beats returning an empty dossier: triage's `except` does `continue`,
# so the lead is kept OUT of the judge batch and counted. A returned empty dossier
# would be judged on an empty JD and a status written from it, with failures=0.

def _triage_run(tmp_path, monkeypatch, role, *, resolve, landed="https://jobs.invalid/x"):
    """Drive a real triage run over one shortlist-able lead, with a stub judge."""
    import os
    from sluice.triage import engine as tengine
    vault_dir = tmp_path / "vault"
    # "Job Leads", not "Leads" -- core/vault.py:29 is
    # _LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads").
    # The wrong path loads ZERO leads, which makes both assertions below pass
    # vacuously and the Step 4 mutant redden with AND without the mutation.
    leads = vault_dir / "Job Applications" / "Job Leads"
    os.makedirs(leads, exist_ok=True)
    (leads / f"Aye - {role}.md").write_text(
        f'---\ncompany: "Aye"\nrole: "{role}"\nstatus: new\n'
        'url: "https://jobs.invalid/x"\nscore: 0\n---\n# body\n')
    monkeypatch.setenv("VAULT_DIR", str(vault_dir))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "dossiers"))
    # Pin the judge's verdict to a status DIFFERENT from the starting one, so the
    # raise->return mutant necessarily writes a different byte rather than
    # coincidentally the same.
    # The keys apply_verdict actually reads (triage/apply.py:36-37): "verdict" and
    # "relevance_score". A stub emitting decision/score lands the lead on
    # needs_review, which silently breaks the positive control that anchors both
    # vacuity guards below.
    monkeypatch.setattr(tengine, "judge", lambda dossiers, backend, **kw: [
        {"lead_id": d["lead_id"], "verdict": "shortlist", "relevance_score": 90,
         "fit_reasoning": "synthetic"} for d in dossiers])
    app = Sluice(Config(), fetcher=_Tab(landed=landed),
                 backend=object(), resolve_host=resolve)
    report = app.triage(statuses=("new",))
    return report, (leads / f"Aye - {role}.md").read_text(), tmp_path / "dossiers"


def test_a_blocked_dossier_leaves_the_lead_untouched(tmp_path, monkeypatch, role):
    report, note, dossier_dir = _triage_run(
        tmp_path, monkeypatch, role, resolve=lambda h: ["127.0.0.1"])
    assert "status: new" in note, "a blocked fetch must not move the lead"
    assert report.failures, "and must be visible in the run summary"
    cached = list(dossier_dir.glob("*.json")) if dossier_dir.exists() else []
    assert cached == [], \
        "no dossier may be cached, or the allowlist remedy is masked for ttl_days"


def test_the_positive_control_does_move_the_lead(tmp_path, monkeypatch, role):
    """Without this, both assertions above pass vacuously -- the dossier dir need
    not exist, and the status is unchanged whenever the lead never reached the
    dossier step at all (a wrong vault path did exactly that in an earlier draft)."""
    report, note, dossier_dir = _triage_run(
        tmp_path, monkeypatch, role, resolve=lambda h: [GLOBAL_ADDR])
    assert "status: shortlist" in note
    assert not report.failures
    assert len(list(dossier_dir.glob("*.json"))) == 1


def test_the_cv_consumer_proceeds_with_an_empty_jd(role, monkeypatch):
    """Raising is NOT behaviourally different for cv -- record that honestly.

    cv/engine.py:66-70 catches Exception, logs, and PROCEEDS with jd = "". So for
    this consumer a raise and a returned empty dossier are indistinguishable: a CV
    is still composed and the fabrication gate still runs. The raise-vs-return
    argument rests entirely on the TRIAGE side (above). Stating it here stops a
    reader inferring that cv skips the lead, which it does not.

    Wired against tests/test_cv_engine.py's existing fake collaborators (FakeVault/
    FakeBackend/FakeRenderer/_cfg) rather than a bespoke stub -- reusing them is
    what keeps this under the ~15-minute budget the task brief set for this test.
    served_dir="" short-circuits the real render.serve() shell-out (irrelevant to
    what is being proven here), same as test_no_serve_renders_but_does_not_mark_lead.
    """
    from sluice.cv import engine as cvengine
    from tests.test_cv_engine import CLEAN_CV, ENTRIES, FakeBackend, FakeRenderer, FakeVault, Note, _cfg

    seen = {}
    monkeypatch.setattr(cvengine, "_jd_keywords", lambda r, jd: seen.setdefault("jd", jd) or [])

    class _BlockedCache:
        """Stands in for Sluice.dossier_cache() after the SSRF guard has refused
        the lead's url -- exactly what get_or_build raises in production."""

        def get_or_build(self, fm):
            raise urlguard.DossierBlocked(urlguard.BLOCKED_ADDRESS)

    cfg = _cfg()
    cfg.served_dir = ""
    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": role})
    result = cvengine.run_one(note, v, cfg, FakeBackend(CLEAN_CV), _BlockedCache(),
                              renderer=FakeRenderer())

    assert result.status == "rendered", "a blocked dossier must not stop composition"
    assert seen["jd"] == "", \
        "a blocked dossier must still let composition proceed on an empty JD"
    # #18: composing blind must be VISIBLE even though it does not change the
    # status -- otherwise "rendered" here is indistinguishable from a CV genuinely
    # tailored to a real job description.
    assert result.dossier_failed is True, \
        "the cv run summary must be able to count CVs composed against no real JD"


def test_the_cv_consumer_records_a_clean_fetch_as_not_blind(role):
    """The paired positive control for the test above: dossier_failed must be False
    when the fetch actually succeeded, or the flag would be True unconditionally and
    the cv run summary's blind-CV count would be meaningless."""
    from sluice.cv import engine as cvengine
    from tests.test_cv_engine import CLEAN_CV, ENTRIES, FakeBackend, FakeCache, FakeRenderer, FakeVault, Note, _cfg

    cfg = _cfg()
    cfg.served_dir = ""
    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": role})
    result = cvengine.run_one(note, v, cfg, FakeBackend(CLEAN_CV), FakeCache(),
                              renderer=FakeRenderer())

    assert result.status == "rendered"
    assert result.dossier_failed is False
