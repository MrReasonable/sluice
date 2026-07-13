import os
from sluice.cli import main, _build_backend
from sluice.core.backends import DEFAULT_BASE_URLS


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


def test_backend_fallback_targets_deepseek_direct(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)  # exercise the default
    from sluice.triage.config import TriageConfig
    be = _build_backend(TriageConfig())
    assert be.fallback.model == "deepseek-v4-flash"
    # Assert the default endpoint via the constant, not a live URL literal:
    # pins that the provider default is applied and the path appended.
    assert be.fallback.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.fallback.api_key == "sk-test"


def test_triage_backend_primary_uses_medium_effort(monkeypatch):
    # Triage judges a large backlog; medium keeps a full run from taking hours.
    # This would fail if _build_backend silently kept the "max" default.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")  # a configured fallback exists
    from sluice.triage.config import TriageConfig
    be = _build_backend(TriageConfig())
    ct = be.primary.cmd_template
    assert ct[ct.index("--effort") + 1] == "medium"
