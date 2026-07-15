import os
from sluice.cli import main


def _note(vault_dir, name, fm_lines):
    leads = os.path.join(vault_dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    open(os.path.join(leads, name), "w").write(
        "---\n" + "\n".join(fm_lines) + "\n---\n# b\n")


def test_normalize_status_cli_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _note(str(tmp_path), "A.md", ['company: "A"', 'status: "dismissed"'])
    rc = main(["triage", "normalize-status", "--dry-run"])
    assert rc == 0
    assert "changed" in capsys.readouterr().out.lower()
    # dry run did not write
    assert 'status: "dismissed"' in open(
        os.path.join(str(tmp_path), "Job Applications", "Job Leads", "A.md")).read()


def test_triage_run_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "dos"))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    _note(str(tmp_path), "dir.md",
          ['company: "Beta"', 'role: "International aid/development worker"', 'location: "London"',
           'salary: ""', 'role_type: "permanent"', 'url: "u"', "status: new",
           "score: 0", 'relevance_notes: ""'])
    # A deterministic reject now requires CONFIGURED criteria: the shipped defaults
    # express no opinion, so nothing is rejected out of the box. (This test used to
    # pass only because target_locations defaulted to ["remote"] and silently binned
    # the London lead -- exactly the hidden preference that default was hiding.)
    cfgfile = tmp_path / "sluice.yaml"
    cfgfile.write_text('triage:\n  reject_titles: ["aid/development worker"]\n')
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgfile))
    # --no-llm avoids any backend call
    rc = main(["triage", "run", "--status", "new", "--no-llm"])
    assert rc == 0
    from sluice.core.vault import Vault
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"


# The former _build_backend field-routing tests (fallback provider/model/url/key,
# and the medium-effort pin) moved to tests/test_backend_selection.py (generic
# role/provider construction + the new end-to-end effort tests) and
# tests/test_app_operations.py::test_triage_threads_the_triage_config_into_the_backend
# (triage's specific config-field mapping into Sluice.backend's kwargs), now that
# triage's backend construction is Sluice.backend() rather than a cli.py wrapper.
