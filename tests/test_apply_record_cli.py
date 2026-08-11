"""`sluice apply record` at the CLI layer: the --url structural-character guard's drop
must be visible to the human who typed it (#111 follow-up, invariant review of PR
fix/triage-followups-109) -- not just silently absent from the written note."""
from sluice.cli import _build_parser, cmd_apply_record
from sluice.core.config import Config
from sluice.core.vault import Vault


def _lead(tmp_path, fm):
    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")


_SHORTLIST = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
              'url: "https://example-northgate.invalid/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_cmd_apply_record_warns_when_the_url_flag_is_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    note = Vault(str(tmp_path)).read_leads({"shortlist"})[0]

    args = _build_parser().parse_args(
        ["apply", "record", "--lead", note.slug, "--url", 'https://x/apply"; status: applied'])
    assert cmd_apply_record(args, Config()) == 0
    err = capsys.readouterr().err
    assert "applied_url" in err and "dropped" in err


def test_cmd_apply_record_does_not_warn_for_an_ordinary_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    note = Vault(str(tmp_path)).read_leads({"shortlist"})[0]

    args = _build_parser().parse_args(
        ["apply", "record", "--lead", note.slug, "--url", "https://x/apply"])
    assert cmd_apply_record(args, Config()) == 0
    assert "dropped" not in capsys.readouterr().err
