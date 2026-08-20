import tempfile, pathlib
from sluice.core.vault import Vault
from sluice.core.protocols import CandidateProfile
from sluice.apply.config import ApplyConfig
from sluice.apply import engine

# `profile`/`today` are required keyword-only params on prep_one/preview_all (Task 5),
# resolved once by Sluice.prep in production. This file drives the engine functions
# directly, below that resolution, so it supplies its own fixed pair -- a blank
# profile (no CandidateProfile field is under test here) and one ISO date, used
# unchanged at every call site in this file (no test here varies it). Chosen to
# match _GOOD's own `tailored_cv: CV_deadbeef.pdf (2026-07-09)` date rather than
# for any significance of its own.
_PROFILE = CandidateProfile()
_TODAY = "2026-07-09"


def _vault(notes):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    served = pathlib.Path(root, "documents"); served.mkdir()
    upload = pathlib.Path(root, "up"); upload.mkdir()
    (served / "CV_deadbeef.pdf").write_bytes(b"%PDF-1.4\nx")
    for fname, fm in notes:
        (leads / fname).write_text("---\n" + fm + "\n---\n\nBODY\n")
    return Vault(root), ApplyConfig(served_dir=str(served), camofox_upload_dir=str(upload))


_GOOD = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example-northgate.invalid/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')
_LEGACY = 'company: "B"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: "My CV/CV_B.pdf"'


def test_prep_one_stages_cv_and_builds_packet():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    r = engine.prep_one(v, cfg, "northgate", profile=_PROFILE, today=_TODAY)
    assert r.status == "staged"
    assert r.staged.endswith("CV.pdf")
    assert pathlib.Path(r.staged).exists()
    assert r.packet["cv_path"] == "./cv-uploads/CV.pdf"


def test_prep_one_skips_ineligible():
    v, cfg = _vault([("B.md", _LEGACY)])
    r = engine.prep_one(v, cfg, "b", profile=_PROFILE, today=_TODAY)
    assert r.status == "skipped" and r.reason == "no_artifact"


def test_preview_all_stages_no_cv():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD), ("B.md", _LEGACY)])
    results = engine.preview_all(v, cfg, profile=_PROFILE, today=_TODAY)
    prev = [r for r in results if r.status == "previewed"]
    assert prev and all(r.packet["cv_path"] is None for r in prev)
    # neutral file was never written
    assert not pathlib.Path(cfg.camofox_upload_dir, cfg.neutral_name).exists()
    assert any(r.status == "skipped" for r in results)


def test_record_one_refuses_ambiguous():
    v, cfg = _vault([
        ("Example Meridian - Analyst.md", _GOOD.replace("Example Northgate", "Example Meridian")),
        ("Example MeridianRemote - Analyst.md", _GOOD.replace("Example Northgate", "Example MeridianRemote")),
    ])
    out = engine.record_one(v, cfg, "meridian")
    assert out["ok"] is False and out["reason"].startswith("ambiguous")


def test_prep_one_failed_when_stage_raises(monkeypatch):
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    def _boom(*a, **k):
        raise engine.CvFileError("boom")
    monkeypatch.setattr(engine, "stage", _boom)
    r = engine.prep_one(v, cfg, "northgate", profile=_PROFILE, today=_TODAY)
    assert r.status == "failed" and "boom" in r.reason


def test_record_one_no_match():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    out = engine.record_one(v, cfg, "zzz")
    assert out["ok"] is False and out["reason"] == "no_match"


def test_record_one_success_flips_status():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    out = engine.record_one(v, cfg, "northgate", ats="greenhouse")
    assert out["ok"] is True


def test_preview_all_limit_caps_eligible():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD), ("Beavni - Analyst.md", _GOOD.replace("Example Northgate", "Beavni"))])
    prev = [r for r in engine.preview_all(v, cfg, limit=1, profile=_PROFILE, today=_TODAY)
            if r.status == "previewed"]
    assert len(prev) == 1


def test_preview_all_resilient_to_build_failure(monkeypatch):
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD), ("B.md", _LEGACY)])
    def _boom(*a, **k):
        raise ValueError("bad packet")
    monkeypatch.setattr(engine._packet, "build_packet", _boom)
    results = engine.preview_all(v, cfg, profile=_PROFILE, today=_TODAY)  # must NOT raise
    statuses = {r.status for r in results}
    assert "failed" in statuses and "skipped" in statuses  # loop completed past the failure
