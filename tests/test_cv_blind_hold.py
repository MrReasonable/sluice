"""cv.blind_hold (#369, spec §4): a CV composed without a job description is HELD for sign-off,
never served send-ready. Its own key -- not require_signoff (fabrication) and not style_hold
(wording)."""
import json

import pytest

from sluice.core.jdsection import HEADING
from sluice.core.leads import BLIND_TAG
from sluice.cv.engine import run_one
from tests.test_cv_engine import (CLEAN_REPLY, ENTRIES, FakeBackend, FakeRenderer, FakeVault,
                                  Note, _cfg, _served)
from tests.test_cv_jd_source import SectionCache, _SECTION_JD

_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
_FETCH_FAILED = "the job description fetch failed"
_NOT_ARRIVED = "no job description arrived"


def _go(cache, *, blind_hold=True, require_signoff=True, body="", fm=None):
    cfg = _cfg()
    cfg.blind_hold, cfg.require_signoff = blind_hold, require_signoff
    note = Note(dict(fm or _FM), body=body)
    v = FakeVault(ENTRIES, notes=[note])
    # FakeBackend's default audit reply is a clean "supported" line: the audit RUNS and
    # flags nothing, so a hold below can only be the blind one (tst-202).
    r = run_one(note, v, cfg, FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    return r, note, v


@pytest.mark.parametrize("blind_hold", [True, False])
@pytest.mark.parametrize("require_signoff", [True, False])
def test_the_blind_hold_matrix_with_a_clean_audit(monkeypatch, blind_hold, require_signoff):
    _served(monkeypatch)
    r, note, v = _go(SectionCache(fetch=""), blind_hold=blind_hold,
                     require_signoff=require_signoff)
    if blind_hold:
        assert r.status == "needs-signoff"
        assert note.ref not in v.written
        assert json.loads(note.fm["needs_signoff"]) == [f"{BLIND_TAG}\t{_NOT_ARRIVED}"]
    else:
        assert r.status == "rendered"
        assert note.ref in v.written
        assert "needs_signoff" not in note.fm


@pytest.mark.parametrize("cache, body, reason", [
    pytest.param(SectionCache(fetch=RuntimeError("blocked")), "", _FETCH_FAILED, id="raised"),
    pytest.param(SectionCache(fetch=""), "", _NOT_ARRIVED, id="not-arrived"),
    pytest.param(SectionCache(min_jd_chars=500), f"{HEADING}\n{_SECTION_JD}\n",
                 "the job description in the note is shorter than min_jd_chars (500)",
                 id="short-section"),
])
def test_each_producer_writes_its_own_fixed_reason(monkeypatch, cache, body, reason):
    _served(monkeypatch)
    r, note, _ = _go(cache, body=body)
    assert r.status == "needs-signoff"
    assert json.loads(note.fm["needs_signoff"]) == [f"{BLIND_TAG}\t{reason}"]


def test_the_reason_never_carries_exception_text(monkeypatch):
    _served(monkeypatch)
    _, note, _ = _go(SectionCache(fetch=RuntimeError("https://example.invalid/secret")))
    assert "example.invalid" not in note.fm["needs_signoff"]


def test_a_lead_with_a_good_section_is_not_held(monkeypatch):
    _served(monkeypatch)
    r, note, v = _go(SectionCache(), body=f"{HEADING}\n{_SECTION_JD}\n")
    assert (r.status, r.jd_source) == ("rendered", "note")
    assert note.ref in v.written


def test_nothing_served_means_no_hold():
    cfg = _cfg(); cfg.served_dir = ""
    note = Note(dict(_FM))
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg, FakeBackend(CLEAN_REPLY),
                SectionCache(fetch=""), renderer=FakeRenderer())
    assert r.status == "rendered" and "pending_cv" not in note.fm


def test_a_dry_run_holds_nothing():
    note = Note(dict(_FM))
    r = run_one(note, FakeVault(ENTRIES, notes=[note]), _cfg(), FakeBackend(CLEAN_REPLY),
                SectionCache(fetch=""), renderer=FakeRenderer(), dry_run=True)
    assert r.status == "dry-run" and "pending_cv" not in note.fm


def test_a_held_lead_is_latched_until_discarded_then_composes_from_the_section(monkeypatch):
    # rev-001: adding a section does nothing while the hold stands (the #60 latch).
    _served(monkeypatch)
    cache = SectionCache(fetch="")
    r, note, v = _go(cache)
    assert r.status == "needs-signoff"
    note.body = f"{HEADING}\n{_SECTION_JD}\n"
    again = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert again.status == "skipped-needs-signoff"
    assert v.sign_off(note.ref, accept=False) == "discarded"
    third = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert (third.status, third.jd_source) == ("rendered", "note")


def test_a_discarded_lead_whose_section_is_still_short_is_held_again(monkeypatch):
    # Review Focus 4.
    _served(monkeypatch)
    cache = SectionCache(min_jd_chars=500)
    body = f"{HEADING}\n{_SECTION_JD}\n"
    r, note, v = _go(cache, body=body)
    assert r.status == "needs-signoff"
    v.sign_off(note.ref, accept=False)
    again = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer())
    assert again.status == "needs-signoff"
    assert "shorter than min_jd_chars" in note.fm["needs_signoff"]


def test_the_cv_run_count_line_is_reworded(monkeypatch, tmp_path, capsys):
    from sluice.cli import _build_parser, cmd_cv_run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.cv.engine import CvResult
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md",
                      "needs-signoff", dossier_failed=True, jd_source="none")
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])
    args = _build_parser().parse_args(["cv", "run", "--lead", "example-foundry-analyst"])
    cmd_cv_run(args, Config())
    err = capsys.readouterr().err
    assert "cv: 1 lead(s) had no usable job description" in err
    assert "composed blind" not in err


def test_the_count_line_counts_a_lead_that_errored_without_claiming_a_cv(monkeypatch,
                                                                          tmp_path, capsys):
    # CodeRabbit on #394: run_batch keeps `dossier_failed` on an `error` result (so an operator
    # still hears the JD never arrived), and no CV was composed for it. The line counts leads,
    # so it is true of both results here.
    from sluice.cli import _build_parser, cmd_cv_run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.cv.engine import CvResult
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    results = [CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md",
                        "needs-signoff", dossier_failed=True, jd_source="none"),
               CvResult("Job Applications/Job Leads/Example Analytics - Engineer.md", "error",
                        dossier_failed=True)]
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: results)
    cmd_cv_run(_build_parser().parse_args(["cv", "run", "--all-shortlist"]), Config())
    err = capsys.readouterr().err
    assert "cv: 2 lead(s) had no usable job description" in err
    assert "composed without a job description" not in err


def test_a_blind_hold_is_not_a_gate_finding(monkeypatch):
    _served(monkeypatch)
    r, _, _ = _go(SectionCache(fetch=""))
    assert r.violations == []
