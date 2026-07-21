"""cv handler through the real main(argv). Re-homed from tests/test_cv_cli.py.

The two parser assertions are kept verbatim; added is a real compose through the
scripted backend and the no-match rc-1 branch -- cmd_cv_run's own handler logic
(rendered-path stderr, the "no shortlist lead" refusal) is exercised by nothing
else (e2e drives Sluice.compose_cv directly, never cli.py).
"""
import os

from sluice.cli import _build_parser
from tests.harness import PASSING_CV, ScriptedBackend


def test_cv_run_parses_lead_and_flags():
    args = _build_parser().parse_args(
        ["cv", "run", "--lead", "acme-em", "--dry-run", "--backend", "deepseek"])
    assert args.group == "cv" and args.cmd == "run"
    assert args.lead == "acme-em" and args.dry_run and args.backend == "deepseek"


def test_cv_run_parses_all_shortlist():
    args = _build_parser().parse_args(["cv", "run", "--all-shortlist", "--limit", "3"])
    assert args.all_shortlist and args.limit == 3


def _seed_shortlist_lead(vault_dir, company, role):
    leads = os.path.join(vault_dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    fm = [f'company: "{company}"', f'role: "{role}"', "status: shortlist",
          'url: "https://example.invalid/jobs/1"', "score: 80", 'relevance_notes: ""']
    with open(os.path.join(leads, f"{company} - {role}.md"), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm) + "\n---\n# body\n")


def test_cv_run_composes_and_renders(cli):
    # Company "Example Foundry" -> [EF1] via the harness prefix_map; PASSING_CV cites
    # [EF1] and its numbers appear in the seeded Experience entry, so the gate passes.
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_CV})
    h, run = cli(backend=backend)
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "run", "--lead", "example-foundry"])
    assert rc == 0
    assert "cv:" in err and "rendered" in err              # cmd_cv_run's stderr line
    assert h.recorder.rendered == [PASSING_CV]             # the gate passed, it rendered


def test_cv_run_no_matching_lead_returns_1(cli):
    # A never-called backend, only so compose_cv's up-front backend build (before the
    # empty-notes check, app.py:351) does not hit real construction.
    h, run = cli(backend=ScriptedBackend())
    rc, _out, err = run(["cv", "run", "--lead", "no-such-lead"])
    assert rc == 1
    assert "no shortlist lead matching" in err
