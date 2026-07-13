import tempfile, pathlib, textwrap, pytest
from types import SimpleNamespace
from sluice.cli import _build_parser, cmd_apply_prep, cmd_apply_record


# ---- parser wiring (mirrors tests/test_cv_cli.py: test the parser, not main()) ----
def test_apply_prep_parses_lead_and_flags():
    args = _build_parser().parse_args(["apply", "prep", "--lead", "northwind", "--json", "--dry-run"])
    assert args.group == "apply" and args.cmd == "prep"
    assert args.lead == "northwind" and args.json and args.dry_run


def test_apply_prep_parses_all_shortlist():
    args = _build_parser().parse_args(["apply", "prep", "--all-shortlist", "--limit", "5"])
    assert args.all_shortlist and args.limit == 5


def test_apply_prep_requires_lead_or_all_shortlist():
    with pytest.raises(SystemExit):        # mutually-exclusive group is required
        _build_parser().parse_args(["apply", "prep"])


def test_apply_record_parses_args():
    args = _build_parser().parse_args(
        ["apply", "record", "--lead", "northwind", "--ats", "greenhouse", "--url", "https://x/apply"])
    assert args.group == "apply" and args.cmd == "record"
    assert args.lead == "northwind" and args.ats == "greenhouse" and args.url == "https://x/apply"


# ---- command functions against a temp vault (call cmd_* directly, not main()) ----
_GOOD = ('company: "Northwind"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://northwind.example/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def _env(monkeypatch):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    served = pathlib.Path(root, "documents"); served.mkdir()
    upload = pathlib.Path(root, "up"); upload.mkdir()
    (served / "CV_deadbeef.pdf").write_bytes(b"%PDF-1.4\nx")
    (leads / "Northwind - Analyst.md").write_text("---\n" + _GOOD + "\n---\n\nBODY\n")
    cfgp = pathlib.Path(root, "config.yaml")
    cfgp.write_text(textwrap.dedent(f"""
        apply:
          served_dir: {served}
          camofox_upload_dir: {upload}
    """))
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))
    monkeypatch.setenv("VAULT_DIR", root)   # Vault() reads VAULT_DIR (core/vault.py:73)
    return root, str(upload)


def test_cmd_apply_prep_lead_stages_and_prints(monkeypatch, capsys):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead="northwind", all_shortlist=False, limit=None, json=False, dry_run=False)
    assert cmd_apply_prep(args, None) == 0
    assert pathlib.Path(upload, "CV.pdf").exists()
    assert "APPLICATION PACKET" in capsys.readouterr().out


def test_cmd_apply_prep_all_shortlist_stages_no_cv(monkeypatch, capsys):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead=None, all_shortlist=True, limit=None, json=False, dry_run=False)
    assert cmd_apply_prep(args, None) == 0
    assert not pathlib.Path(upload, "CV.pdf").exists()


def test_cmd_apply_record_flips_status(monkeypatch):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead="northwind", ats="greenhouse", url=None, dry_run=False)
    assert cmd_apply_record(args, None) == 0
    text = pathlib.Path(root, "Job Applications", "Job Leads", "Northwind - Analyst.md").read_text()
    assert "status: applied" in text


def test_cmd_apply_prep_dry_run_touches_no_filesystem(monkeypatch, capsys):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead="northwind", all_shortlist=False, limit=None, json=False, dry_run=True)
    assert cmd_apply_prep(args, None) == 0
    assert not pathlib.Path(upload, "CV.pdf").exists()   # dry-run stages no CV
    assert "APPLICATION PACKET" in capsys.readouterr().out


def test_cmd_apply_prep_lead_no_match_returns_1(monkeypatch, capsys):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead="zzz-nomatch", all_shortlist=False, limit=None, json=False, dry_run=False)
    assert cmd_apply_prep(args, None) == 1
    assert "skipped" in capsys.readouterr().err   # digest goes to stderr


def test_cmd_apply_record_refused_returns_1(monkeypatch, capsys):
    root, upload = _env(monkeypatch)
    args = SimpleNamespace(lead="zzz-nomatch", ats=None, url=None, dry_run=False)
    assert cmd_apply_record(args, None) == 1
    assert "refused" in capsys.readouterr().err
