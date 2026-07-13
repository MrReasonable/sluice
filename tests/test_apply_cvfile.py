import os, tempfile, pathlib, pytest
from sluice.apply.config import ApplyConfig
from sluice.apply import cvfile


def test_parse_artifact_accepts_sluice_cv_form():
    assert cvfile.parse_artifact("CV_deadbeef.pdf (2026-07-09)") == "CV_deadbeef.pdf"
    assert cvfile.parse_artifact("CV_deadbeef.pdf") == "CV_deadbeef.pdf"


def test_parse_artifact_rejects_legacy_skipped_empty():
    assert cvfile.parse_artifact("My CV/CV_Brightmar.pdf") is None
    assert cvfile.parse_artifact("SKIPPED - role above target level") is None
    assert cvfile.parse_artifact("skipped: whatever") is None
    assert cvfile.parse_artifact("") is None
    assert cvfile.parse_artifact(None) is None


def _cfg_with_served(tmp):
    served = pathlib.Path(tmp, "documents"); served.mkdir()
    upload = pathlib.Path(tmp, "cv-host"); upload.mkdir()
    return ApplyConfig(served_dir=str(served), camofox_upload_dir=str(upload))


def test_stage_copies_to_neutral_name_and_verifies():
    tmp = tempfile.mkdtemp()
    cfg = _cfg_with_served(tmp)
    src = pathlib.Path(cfg.served_dir, "CV_deadbeef.pdf")
    src.write_bytes(b"%PDF-1.4\n...content...")
    dest = cvfile.stage("CV_deadbeef.pdf (2026-07-09)", cfg)
    assert dest == os.path.join(cfg.camofox_upload_dir, "CV.pdf")
    assert pathlib.Path(dest).read_bytes().startswith(b"%PDF")


def test_stage_rejects_non_artifact():
    tmp = tempfile.mkdtemp()
    cfg = _cfg_with_served(tmp)
    with pytest.raises(cvfile.CvFileError):
        cvfile.stage("My CV/CV_Brightmar.pdf", cfg)


def test_stage_errors_when_source_missing():
    tmp = tempfile.mkdtemp()
    cfg = _cfg_with_served(tmp)
    with pytest.raises(cvfile.CvFileError):
        cvfile.stage("CV_deadbeef.pdf (2026-07-09)", cfg)


def test_stage_rejects_non_pdf_source():
    tmp = tempfile.mkdtemp()
    cfg = _cfg_with_served(tmp)
    src = pathlib.Path(cfg.served_dir, "CV_deadbeef.pdf")
    src.write_bytes(b"not a pdf at all")
    with pytest.raises(cvfile.CvFileError):
        cvfile.stage("CV_deadbeef.pdf (2026-07-09)", cfg)


def test_stage_wraps_write_failure_in_cvfileerror():
    tmp = tempfile.mkdtemp()
    cfg = _cfg_with_served(tmp)
    src = pathlib.Path(cfg.served_dir, "CV_deadbeef.pdf")
    src.write_bytes(b"%PDF-1.4\nx")
    # make the upload dir un-creatable: its parent is a regular file
    blocker = pathlib.Path(tmp, "blocker"); blocker.write_text("x")
    cfg.camofox_upload_dir = str(blocker / "sub")   # makedirs will fail (NotADirectoryError)
    with pytest.raises(cvfile.CvFileError):
        cvfile.stage("CV_deadbeef.pdf (2026-07-09)", cfg)
