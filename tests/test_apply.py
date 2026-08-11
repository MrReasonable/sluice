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


def test_apply_classification_returns_unchanged_on_a_status_change_between_read_and_write(tmp_path):
    """#118: this outcome is reachable ONLY through require_status refusing on the
    fresh read -- never through a real content collision (that raises VaultConflict,
    a separate, already-correctly-handled path). So it is a benign no-op, not a race:
    the lead simply already left TRIAGE_OWNED by the time the write was attempted, and
    nothing was lost. `"skipped-race"` was a misnomer for exactly this outcome."""
    v = Vault(str(tmp_path))
    _note(v, "D.md", ['company: "Delta"', "status: new", "score: 0",
                      'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    # Simulate a receipt/manual `apply record` landing between read_leads() and
    # this write -- the lead has already left TRIAGE_OWNED by the time the write
    # is attempted, but `note.status` (frozen at read time) still reads "new".
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_classification(v, note, "reject", "IC role") == "unchanged"
    assert v.read_leads()[0].status == "applied"     # the real status survives untouched


def test_apply_verdict_returns_unchanged_on_a_status_change_between_read_and_write(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "E.md", ['company: "Epsilon"', "status: new", "score: 0",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    v.update_fields(note.ref, {"status": "applied"})
    verdict = {"verdict": "shortlist", "relevance_score": 82, "fit_reasoning": "fit"}
    assert apply_verdict(v, note, verdict, {}) == "unchanged"
    assert v.read_leads()[0].status == "applied"
