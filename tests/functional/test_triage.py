"""triage handlers through the real main(argv). Re-homed from tests/test_triage_cli.py.

The run test is a neutral-defaults witness. It MUST run with target_locations=() --
build_harness defaults it to ("remote",), the literal 672ad2a bug value, under which a
location-bearing lead would be binned by the LOCATION gate and the `dismiss` assertion
would still pass while quietly demonstrating the historical bug. Two tests give the
attribution the original lacked: with a reject title the lead is dismissed; with NO
reject the SAME location-bearing lead survives -- so the empty location gate demonstrably
abstains, not the reject accidentally covering for it.
"""
import os

from sluice.core.vault import Vault

# A location-BEARING lead (location "Alfa", a neutral synthetic place from conftest's
# LOCATIONS convention) whose role carries a synthetic, opinion-free reject substring.
_LOCATION_BEARING_LEAD = [
    'company: "Example Systems"',
    'role: "Synthetic Reject Target"',
    'location: "Alfa"',
    'salary: ""',
    'role_type: "permanent"',
    'url: "https://example.invalid/u"',
    "status: new",
    "score: 0",
    'relevance_notes: ""',
]


def _seed_lead(vault_dir, name, fm_lines):
    leads = os.path.join(vault_dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n# body\n")


def test_normalize_status_dry_run_does_not_write(cli):
    h, run = cli()
    _seed_lead(h.paths["vault"], "A.md", ['company: "Example Systems"', 'status: "dismissed"'])
    rc, out, _err = run(["triage", "normalize-status", "--dry-run"])
    assert rc == 0
    assert "changed" in out.lower()
    note = os.path.join(h.paths["vault"], "Job Applications", "Job Leads", "A.md")
    with open(note, encoding="utf-8") as f:
        assert 'status: "dismissed"' in f.read()   # dry-run did NOT write


def test_triage_run_dismisses_via_reject_title(cli):
    # target_locations=() so the location gate abstains; the reject title is the cause.
    h, run = cli(target_locations=(), reject_titles=["reject target"])
    _seed_lead(h.paths["vault"], "dir.md", _LOCATION_BEARING_LEAD)
    rc, _out, _err = run(["triage", "run", "--status", "new", "--no-llm"])
    assert rc == 0
    assert Vault(h.paths["vault"]).read_leads()[0].status == "dismiss"


def test_triage_run_empty_location_gate_abstains(cli):
    # The 672ad2a witness (inv-001): with BOTH target_locations and reject_titles empty,
    # the SAME location-bearing lead is NOT dismissed -- an unconfigured location gate
    # passes it through. If the empty gate ever binned it, this test reddens.
    h, run = cli(target_locations=(), reject_titles=())
    _seed_lead(h.paths["vault"], "dir.md", _LOCATION_BEARING_LEAD)
    rc, _out, _err = run(["triage", "run", "--status", "new", "--no-llm"])
    assert rc == 0
    assert Vault(h.paths["vault"]).read_leads()[0].status != "dismiss"
