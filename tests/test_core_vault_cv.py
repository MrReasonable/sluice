import pathlib, tempfile
from sluice.core.vault import Vault

def _vault_with(entries, baseline="BASELINE"):
    root = tempfile.mkdtemp()
    exp = pathlib.Path(root, "Job Applications", "Experience Library")
    (exp / "_inbox").mkdir(parents=True)
    for name, fm, body in entries:
        (exp / f"{name}.md").write_text("---\n" + fm + "\n---\n\n" + body)
    (exp / "_inbox" / "draft.md").write_text("---\nCompany: X\nverified: 2026-01-01\n---\nbody")
    mycv = pathlib.Path(root, "My CV"); mycv.mkdir(parents=True)
    (mycv / "CV.md").write_text(baseline)
    return Vault(root), root

def test_read_experience_verified_only_skips_unverified_and_inbox():
    v, _ = _vault_with([
        ("good", 'Company: "Example Foundry"\nBest For: "leadership"\nMetrics: "3 8"\nverified: 2026-07-01', "Grew team 3 to 8."),
        ("bad", 'Company: "Example Systems"\nBest For: "leadership"', "130-person programme."),
    ])
    entries = v.read_experience_entries(verified_only=True)
    titles = [e["title"] for e in entries]
    assert titles == ["good"]
    assert entries[0]["company"] == "Example Foundry"
    assert entries[0]["best_for"] == "leadership"
    assert entries[0]["metrics"] == "3 8"
    assert entries[0]["body"] == "Grew team 3 to 8."

def test_read_experience_parses_block_list_category():
    v, _ = _vault_with([
        ("blocklist", 'Company: "Example Foundry"\nCategory:\n  - Process\n  - Leadership\nverified: 2026-07-01', "Body."),
    ])
    e = v.read_experience_entries(verified_only=True)[0]
    assert e["category"] and "Process" in e["category"] and "Leadership" in e["category"]

def test_read_baseline():
    v, _ = _vault_with([], baseline="Phone number: +44\nJANE ROE")
    assert "JANE ROE" in v.read_baseline()

def test_set_tailored_cv_is_additive_and_preserves_body():
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    note = leads / "Acme - Analyst.md"
    note.write_text('---\ncompany: "Acme"\nstatus: shortlist\n---\n\nBODY TEXT\n')
    v = Vault(root)
    v.set_tailored_cv(str(note), "Jane_Roe_CV_ab12cd34.pdf (2026-07-08)")
    text = note.read_text()
    assert "tailored_cv: Jane_Roe_CV_ab12cd34.pdf (2026-07-08)" in text
    assert "status: shortlist" in text  # untouched
    assert "BODY TEXT" in text          # body preserved
