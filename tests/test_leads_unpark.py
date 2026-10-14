"""#302: `leads unpark` -- report-first, human-gated move of stamped research leads to
needs_review. Fixture notes are built through `Vault.upsert` so their slugs are real
store-issued filenames, as in tests/test_leads_expire.py."""
import pathlib

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from tests.conftest import racing_read

TODAY = "2031-03-14"
STAMP = "2031-03-10"


@pytest.fixture(autouse=True)
def _role(titles):
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, n=0, status="research", company="Example Ltd", **extra):
    url = f"https://example.invalid/{n}"
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=_TITLES[n], company=company, url=url))
    note = next(x for x in v.read_leads() if x.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)), today=lambda: TODAY)


def _by_slug(tmp_path, slug):
    return next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == slug)


def _bytes(tmp_path):
    root = pathlib.Path(tmp_path)
    return {p: p.read_bytes() for p in root.rglob("*.md")}


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_lists_stamped_research_leads_with_the_raw_stamp(tmp_path):
    slug = _seed(tmp_path, triage_blocked=STAMP)
    _seed(tmp_path, n=1)                                   # unstamped: not listed
    [row] = _app(tmp_path).unpark_report()
    assert (row.slug, row.stamp, row.host, row.company) == (
        slug, STAMP, "example.invalid", "Example Ltd")


def test_the_report_changes_nothing(tmp_path):
    _seed(tmp_path, triage_blocked=STAMP)
    before = _bytes(tmp_path)
    _app(tmp_path).unpark_report()
    assert _bytes(tmp_path) == before


def test_the_report_skips_a_lead_awaiting_a_reply(tmp_path):
    _seed(tmp_path, triage_blocked=STAMP, followup_asked="2031-03-12")
    assert _app(tmp_path).unpark_report() == []


@pytest.mark.parametrize("value", ["my own note", "|"])
def test_the_report_skips_a_hand_typed_value(tmp_path, value):
    _seed(tmp_path, triage_blocked=value)
    assert _app(tmp_path).unpark_report() == []


def test_the_report_skips_a_stamped_lead_not_at_research(tmp_path):
    _seed(tmp_path, status="needs_review", triage_blocked=STAMP)
    assert _app(tmp_path).unpark_report() == []


def test_an_empty_report_is_empty(tmp_path):
    assert _app(tmp_path).unpark_report() == []


# ── the move ─────────────────────────────────────────────────────────────────

def test_unpark_moves_every_listed_lead_and_clears_the_stamp(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    rows, results = _app(tmp_path).unpark()
    assert sorted(results) == sorted([(a, "moved"), (b, "moved")])
    assert {r.slug for r in rows} == {a, b}
    for slug in (a, b):
        note = _by_slug(tmp_path, slug)
        assert note.status == "needs_review"
        assert note.fm["triage_blocked"] == ""
        assert f"[unpark {TODAY}]" in note.fm["relevance_notes"]


def test_unpark_narrows_to_named_slugs(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    rows, results = _app(tmp_path).unpark(slugs=[a])
    assert results == [(a, "moved")] and [r.slug for r in rows] == [a]
    assert _by_slug(tmp_path, b).status == "research"


def test_an_unknown_slug_is_reported_and_the_named_valid_one_still_moves(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    _, results = _app(tmp_path).unpark(slugs=["Example Nowhere - Nothing", a])
    assert ("Example Nowhere - Nothing", "no-match") in results and (a, "moved") in results


def test_a_slug_two_listed_notes_claim_is_ambiguous(tmp_path, monkeypatch):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    app = _app(tmp_path)
    real = app.unpark_report
    def twins():
        rows = real()
        return [r if r.slug != b else type(r)(**{**r.__dict__, "slug": a}) for r in rows]
    monkeypatch.setattr(app, "unpark_report", twins)
    _, results = app.unpark(slugs=[a])
    assert results == [(a, "ambiguous")]
    assert _by_slug(tmp_path, b).status == "research"


def test_a_restamp_with_a_different_date_after_the_report_abstains(tmp_path, monkeypatch):
    # Witnesses require_unchanged. A DIFFERENT date: a same-day re-stamp is byte-identical.
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"triage_blocked": "2031-03-13"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"]
    assert results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "research"


def test_a_lead_moved_off_research_mid_sweep_abstains(tmp_path, monkeypatch):
    # Witnesses require_status on the move.
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"status": "applied"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"] and results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "applied"


def test_a_lead_marked_asked_mid_sweep_abstains(tmp_path, monkeypatch):
    # Witnesses require_blank on the move.
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"followup_asked": "2031-03-14"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"] and results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "research"


def test_a_vault_conflict_on_one_lead_does_not_stop_the_rest(tmp_path, monkeypatch):
    _seed(tmp_path, triage_blocked=STAMP)
    _seed(tmp_path, n=1, triage_blocked=STAMP)
    app = _app(tmp_path)
    store = app.store()
    real = store.update_fields
    calls = {"n": 0}
    def flaky(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict("synthetic race")
        return real(ref, fields, **kw)
    monkeypatch.setattr(store, "update_fields", flaky)
    _, results = app.unpark()
    assert sorted(o for _, o in results) == ["conflict", "moved"]


def test_unpark_with_nothing_listed_writes_nothing(tmp_path):
    _seed(tmp_path)
    before = _bytes(tmp_path)
    assert _app(tmp_path).unpark() == ([], [])
    assert _bytes(tmp_path) == before
