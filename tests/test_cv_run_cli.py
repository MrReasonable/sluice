"""`sluice cv run` at the CLI layer (#167 Task 16): `CvResult.slop` has had NO reader
since it was added, and `CvResult.voice_flags` is a brand-new field threaded through
cv/engine.py's retry loop by Task 14 -- neither a style phrase match nor a model-judged
voice finding reaches the user unless `cmd_cv_run` prints them. Mirrors this repo's
`test_<command>_cli.py` convention (`test_apply_record_cli.py`, `test_triage_run_cli.py`,
`test_health_cli.py`): `Sluice.compose_cv` is monkeypatched to a canned result so this
file tests `cmd_cv_run`'s OWN printing, not cv/engine.py's retry machinery -- that is
already pinned directly against `run_one` in tests/test_cv_engine.py.
"""
from sluice.cli import _build_parser, cmd_cv_run
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.cv.engine import CvResult


def _args():
    return _build_parser().parse_args(["cv", "run", "--lead", "example-foundry-analyst"])


def test_cmd_cv_run_prints_the_style_and_voice_findings(monkeypatch, tmp_path, capsys):
    # The populated case, not just a field that EXISTS or is empty on a clean run --
    # either of those is indistinguishable from a broken reader (see this task's own
    # brief). One genuinely style-dirty finding and one genuinely voice-dirty finding,
    # both already SLOP/flag-prefixed exactly as cv/engine.py's retained-draft path
    # (Task 16) hands them back.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf",
        slop=["SLOP leverage: I leverage strong delivery patterns."],
        voice_flags=["flag\tThis reads like a press release."])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "SLOP leverage: I leverage strong delivery patterns." in err
    assert "flag\tThis reads like a press release." in err


def test_cmd_cv_run_prints_nothing_extra_when_slop_and_voice_flags_are_empty(
        monkeypatch, tmp_path, capsys):
    # The other half of the populated-case discipline: a genuinely clean run must not
    # grow a spurious "SLOP"/"VOICE" line just because the fields now have a reader.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf")
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "SLOP" not in err
    assert "VOICE" not in err
    assert "slop=0" in err and "voice_flags=0" in err
