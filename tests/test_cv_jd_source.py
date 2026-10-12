"""cv reads the lead note's own job description, and every result says where its JD came
from (#369, spec §2)."""
import json
import os

import pytest

from sluice.core.jdsection import HEADING
from sluice.cv.engine import run_batch, run_one
from tests.test_cv_engine import (CLEAN_REPLY, ENTRIES, FakeBackend, FakeRenderer, FakeVault,
                                  HARD_DIRTY_REPLY, Note, _cfg)

_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
_SECTION_JD = "Placeholder posting from the lead note: build and run example services."
_FETCHED_JD = "Placeholder posting text fetched from the board for this example lead."


class SectionCache:
    """Records every get_or_build call's args and kwargs, so a row can tell `get_or_build(fm)`
    from `get_or_build(fm, supplied_jd=...)`. `fetch` is what a FETCH returns: a string, or an
    exception to raise. A supplied JD is returned without touching `fetch`, as the real
    DossierCache does. `jd_arrived` mirrors the real rule, floor included."""

    def __init__(self, fetch=_FETCHED_JD, min_jd_chars=0):
        self.fetch, self.min_jd_chars, self.calls = fetch, min_jd_chars, []

    def get_or_build(self, fm, **kw):
        self.calls.append(((fm,), kw))
        if kw.get("supplied_jd"):
            return {"jd": {"markdown": kw["supplied_jd"]}}
        if isinstance(self.fetch, Exception):
            raise self.fetch
        return {"jd": {"markdown": self.fetch}}

    def fetches(self):
        return [c for c in self.calls if not c[1].get("supplied_jd")]

    def jd_arrived(self, d):
        text = ((d.get("jd") or {}).get("markdown") or "").strip()
        return bool(text) and len(text) >= self.min_jd_chars


def _unserved():
    cfg = _cfg()
    cfg.served_dir = ""
    return cfg


def _run(cache, body="", cfg=None, reply=CLEAN_REPLY):
    note = Note(dict(_FM), body=body)
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg or _unserved(),
                FakeBackend(reply), cache, renderer=FakeRenderer())
    return r, note


def _section(text=_SECTION_JD):
    return f"# Example Foundry - Analyst\n\n{HEADING}\n{text}\n\n## Notes\nmine\n"


def test_a_lead_without_a_section_calls_get_or_build_exactly_as_today():
    # Conditional passing (rev-102, tst-101): roughly ten fakes in tests/ implement
    # get_or_build(self, fm); an unconditional keyword would TypeError inside the dossier
    # try and read as a failed fetch.
    cache = SectionCache()
    _run(cache, body="# just a title\n")
    assert cache.calls == [((dict(_FM),), {})]


def test_a_section_lead_is_composed_from_it_with_no_fetch():
    cache = SectionCache()
    r, _ = _run(cache, body=_section())
    assert r.status == "rendered"
    assert r.jd_source == "note"
    assert r.dossier_failed is False
    assert cache.fetches() == []
    assert cache.calls[0][1] == {"supplied_jd": _SECTION_JD}


@pytest.mark.parametrize("fetch, min_chars, source, failed", [
    pytest.param(_FETCHED_JD, 0, "fetched", False, id="healthy"),
    pytest.param("short", 100, "fetched", True, id="too-short"),
    pytest.param("", 0, "none", True, id="empty"),
    pytest.param(RuntimeError("blocked"), 0, "none", True, id="raised"),
])
def test_jd_source_values(fetch, min_chars, source, failed):
    r, _ = _run(SectionCache(fetch=fetch, min_jd_chars=min_chars))
    assert (r.jd_source, r.dossier_failed) == (source, failed)


def test_an_empty_section_is_no_section_and_the_fetch_decides():
    # rev-201, inv-201: a bare heading is NOT a section.
    cache = SectionCache()
    r, _ = _run(cache, body=f"{HEADING}\n\n## Notes\nx\n")
    assert cache.calls == [((dict(_FM),), {})]
    assert (r.jd_source, r.dossier_failed) == ("fetched", False)


def test_an_empty_section_with_a_raising_fetch_reports_none():
    r, _ = _run(SectionCache(fetch=RuntimeError("blocked")), body=f"{HEADING}\n")
    assert (r.jd_source, r.dossier_failed) == ("none", True)


def test_a_short_section_is_note_and_blind():
    r, _ = _run(SectionCache(min_jd_chars=500), body=_section())
    assert (r.jd_source, r.dossier_failed) == ("note", True)


def test_duplicate_sections_log_a_warning_naming_the_note(caplog):
    body = f"{HEADING}\nfirst text here\n## Other\n{HEADING}\nsecond\n"
    with caplog.at_level("WARNING"):
        r, note = _run(SectionCache(), body=body)
    assert r.jd_source == "note"
    assert any(note.ref in rec.getMessage() and "first" in rec.getMessage()
               for rec in caplog.records)


def test_a_section_reader_that_raises_is_an_error_not_a_failed_fetch(monkeypatch, caplog):
    # tst-002: read BEFORE the dossier try, so a reader bug is not reported as a fetch.
    import sluice.cv.engine as eng
    monkeypatch.setattr(eng.jdsection, "read",
                        lambda body: (_ for _ in ()).throw(ValueError("reader bug")))
    note = Note(dict(_FM), body=_section())
    with caplog.at_level("WARNING"):
        results = run_batch(FakeVault(ENTRIES, notes=[note]), _unserved(),
                            FakeBackend(CLEAN_REPLY), SectionCache(),
                            renderer=FakeRenderer())
    assert [r.status for r in results] == ["error"]
    assert not any("dossier for" in rec.getMessage() for rec in caplog.records)


def test_an_error_result_carries_the_jd_source_stamp():
    class _Boom:
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("render boom")
    note = Note(dict(_FM), body=_section())
    results = run_batch(FakeVault(ENTRIES, notes=[note]), _unserved(),
                        FakeBackend(CLEAN_REPLY), SectionCache(), renderer=_Boom())
    assert (results[0].status, results[0].jd_source) == ("error", "note")


def test_a_skipped_gate_result_carries_jd_source():
    # Review Focus 2.
    r, _ = _run(SectionCache(fetch=""), reply=HARD_DIRTY_REPLY)
    assert r.status == "skipped-gate"
    assert (r.jd_source, r.dossier_failed) == ("none", True)


@pytest.mark.parametrize("fm_extra", [{"status": "new"}, {"pending_cv": "x.pdf (2026-10-11)"}])
def test_an_early_refusal_reports_an_empty_jd_source(fm_extra):
    # Review Focus 3: never a misleading "none" for a lead that never reached the dossier.
    note = Note({**_FM, **fm_extra}, body=_section())
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _unserved(),
                FakeBackend(CLEAN_REPLY), SectionCache(), renderer=FakeRenderer())
    assert r.jd_source == ""


def test_a_dry_run_reports_jd_source():
    note = Note(dict(_FM), body=_section())
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _unserved(),
                FakeBackend(CLEAN_REPLY), SectionCache(), renderer=FakeRenderer(),
                dry_run=True)
    assert (r.status, r.jd_source) == ("dry-run", "note")


def _run_json(cfg):
    out = cfg.output_dir
    found = [os.path.join(d, "run.json") for d, _, files in os.walk(out) if "run.json" in files]
    assert len(found) == 1, found
    return json.load(open(found[0], encoding="utf-8"))


def test_run_json_carries_jd_source_on_a_rendered_result():
    cfg = _unserved()
    _run(SectionCache(), body=_section(), cfg=cfg)
    assert _run_json(cfg)["jd_source"] == "note"


def test_run_json_carries_jd_source_on_an_error_result():
    class _Boom:
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("render boom")
    cfg = _unserved()
    note = Note(dict(_FM))
    run_batch(FakeVault(ENTRIES, notes=[note]), cfg, FakeBackend(CLEAN_REPLY),
              SectionCache(), renderer=_Boom())
    assert _run_json(cfg)["jd_source"] == "fetched"


def test_a_backend_outage_result_carries_the_jd_source_stamp(monkeypatch):
    # tst-002: run_batch's backend-unavailable arm builds its result from the exception,
    # like the catch-all above, and must read `jd_source` back the same way.
    import sluice.cv.engine as eng
    from sluice.core.backends import BackendError

    def _down(*a, **k):
        e = BackendError("down", transient=True)
        e.jd_source = "note"
        raise e
    monkeypatch.setattr(eng, "run_one", _down)
    note = Note(dict(_FM), body=_section())
    results = run_batch(FakeVault(ENTRIES, notes=[note]), _unserved(),
                        FakeBackend(CLEAN_REPLY), SectionCache(), renderer=FakeRenderer())
    assert [(r.status, r.jd_source) for r in results] == [("backend-unavailable", "note")]


def test_a_section_lead_whose_build_raises_is_blind_with_the_arrival_reason(monkeypatch):
    # tst-003: get_or_build raising on the SUPPLIED path (a cache bug, not a fetch) is still
    # blind, sourced from the note, with "no job description arrived" -- never "the fetch
    # failed", since nothing was fetched.
    from tests.test_cv_engine import _served
    _served(monkeypatch)

    class _RaisesOnSupplied(SectionCache):
        def get_or_build(self, fm, **kw):
            if kw.get("supplied_jd"):
                raise RuntimeError("cache bug")
            return super().get_or_build(fm, **kw)
    r, note = _run(_RaisesOnSupplied(), body=_section(), cfg=_cfg())
    assert (r.status, r.jd_source, r.dossier_failed) == ("needs-signoff", "note", True)
    assert json.loads(note.fm["needs_signoff"]) == ["blind\tno job description arrived"]
