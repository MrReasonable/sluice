import os
from sluice.core.vault import Vault
from sluice.triage.apply import apply_classification, apply_verdict


def _note(vault, name, fm_lines):
    leads = os.path.join(vault.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n# body\n")


def test_apply_classification_rejects_to_dismiss(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "A.md", ['company: "Acme"', "status: new", "score: 0",
                      'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    assert apply_classification(v, note, "reject", "IC role") == "applied"
    after = v.read_leads()[0]
    assert after.status == "dismiss"
    assert "IC role" in after.fm["relevance_notes"]


def test_apply_verdict_writes_all_fields(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "B.md", ['company: "Beta"', "status: new", "score: 0",
                      'glassdoor_rating: ""', 'culture_flags: ""',
                      'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    verdict = {"verdict": "shortlist", "relevance_score": 82,
               "fit_reasoning": "Strong single-team fit.",
               "concerns": ["remote-only"], "culture_flags": ["fast-paced"],
               "recommended_next_action": "apply"}
    dossier = {"glassdoor": {"rating": "4.1"}}
    assert apply_verdict(v, note, verdict, dossier) == "applied"
    after = v.read_leads()[0]
    assert after.status == "shortlist"
    assert after.fm["score"] == "82"
    assert after.fm["glassdoor_rating"] == "4.1"
    assert "fast-paced" in after.fm["culture_flags"]
    assert "Strong single-team fit." in after.fm["relevance_notes"]


def test_never_clobbers_application_status(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "C.md", ['company: "Gamma"', "status: applied", "score: 90",
                      'relevance_notes: ""'])
    note = v.read_leads()[0]
    assert apply_verdict(v, note, {"verdict": "dismiss", "relevance_score": 5},
                         {}) == "skipped"
    assert v.read_leads()[0].status == "applied"     # untouched
