"""`job-sluice leads dismiss` at the CLI layer (#131 decision 18): dispatch, exit
codes, printed output -- mirroring tests/test_leads_expire_cli.py's own rationale for
why an app-level test alone cannot certify the command (a mutant inside
cmd_leads_dismiss could keep every app-level test green)."""
import pathlib

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


def test_ambiguous_slug_names_every_candidate_and_exits_1(tmp_path, monkeypatch, capsys):
    """Round-2 CodeRabbit finding: `ambiguous` had zero CLI coverage. dismiss_lead
    resolves by EXACT slug equality (decision 4), so this branch needs a genuine
    slug COLLISION -- two notes at the same basename in different folders -- not a
    substring match. Written as raw files (not through Vault.upsert, which resolves
    a repeat title/company/url to the SAME note) mirroring
    tests/test_mcpserver.py's test_cv_run_tool_ambiguous_names_slug_candidates."""
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    fm = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/1"')
    for sub in ("Active", "Archive"):
        d = leads / sub
        d.mkdir(parents=True)
        (d / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    slug = "Example Northgate - Analyst"
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "x") == 1
    err = capsys.readouterr().err
    assert "ambiguous: 2 notes claim the slug" in err
    assert "rename or merge" in err


def test_refused_status_names_the_drifted_status_and_exits_1(tmp_path, monkeypatch, capsys):
    """Round-2 CodeRabbit finding: `refused_status` had zero CLI coverage. Reachable
    only via the CAS race dismiss_lead's own require_status guards against: the
    resolve snapshot finds the note TRIAGE_OWNED, then a concurrent writer moves it
    OUTSIDE _DISMISSABLE_FROM before this call's own write lands -- mirroring
    tests/test_leads_dismiss.py's app-layer
    test_cas_proof_refuses_on_a_status_that_changed_between_resolve_and_write, down
    to the CLI's own printed output. A lead simply SEEDED at 'applied' would never
    reach this branch at all: store.read_leads(TRIAGE_OWNED) would drop it from the
    initial resolve and dismiss_lead would report not_found instead."""
    slug = _seed(tmp_path, status="research")
    real_update_fields = Vault.update_fields

    def _racing_update_fields(self, ref, fields, **kwargs):
        real_update_fields(self, ref, {"status": "applied"})   # simulated concurrent writer
        return real_update_fields(self, ref, fields, **kwargs)

    monkeypatch.setattr(Vault, "update_fields", _racing_update_fields)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "x") == 1
    err = capsys.readouterr().err
    assert f"leads dismiss: {slug}: refused (status=applied)" in err


def test_same_day_repeat_is_unchanged_and_exits_zero(tmp_path, monkeypatch, capsys):
    """Deferred #6 (final whole-branch review): `rc == 0` alone cannot
    distinguish "genuinely took the unchanged branch" from "silently no-oped
    some other way that also happens to return 0". Mirrors
    tests/test_leads_dismiss.py's own test_same_day_repeat_... (the app-layer
    version of this exact test) down to the CLI layer: assert `unchanged`
    actually appears in the SECOND call's own captured stderr (cmd_leads_dismiss
    prints result.outcome verbatim), and assert the second call's --reason text
    never landed on disk -- proving the tag-idempotency `unchanged` is supposed
    to reflect, not just a passing exit code."""
    slug = _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "first") == 0
    capsys.readouterr()   # discard the first call's own output
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--reason", "second") == 0
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"
    assert "unchanged" in capsys.readouterr().err
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "second" not in text
