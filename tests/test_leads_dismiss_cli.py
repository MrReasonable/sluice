"""`job-sluice leads dismiss` at the CLI layer (#131 decision 18): dispatch, exit
codes, printed output -- mirroring tests/test_leads_expire_cli.py's own rationale for
why an app-level test alone cannot certify the command (a mutant inside
cmd_leads_dismiss could keep every app-level test green)."""
from sluice.cli import main
from sluice.core.leads import Lead
from sluice.core.vault import Vault


def _seed(tmp_path, *, status="shortlist", title="Example Role",
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title, company="Example Ltd", url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    fields = {"status": status, **extra}
    v.update_fields(note.ref, fields)
    return note.slug


def _run(tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return main(["leads", "dismiss", *argv])


def test_dismisses_and_exits_zero(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "no fit") == 0
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"
    assert slug in capsys.readouterr().err


def test_unknown_lead_exits_1(tmp_path, monkeypatch, capsys):
    assert _run(tmp_path, monkeypatch, "--lead", "nothing", "--reason", "x") == 1
    assert "no lead matching" in capsys.readouterr().err


def test_refused_signoff_hold_names_the_remedy_and_exits_1(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, pending_cv='"CV_deadbeef.pdf (2026-08-14)"')
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "x") == 1
    err = capsys.readouterr().err
    assert "sign-off hold" in err and "cv signoff" in err


def test_same_day_repeat_is_unchanged_and_exits_zero(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "first") == 0
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "second") == 0
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"
