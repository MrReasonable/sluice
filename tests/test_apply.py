import os
from sluice.core.vault import Vault
from sluice.triage.apply import apply_classification, apply_verdict, clamp_verdict


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


def test_a_model_verdict_outside_the_judges_vocabulary_becomes_needs_review():
    assert clamp_verdict("shortlist") == "shortlist"
    assert clamp_verdict("research") == "research"
    assert clamp_verdict("dismiss") == "dismiss"
    assert clamp_verdict("nonsense") == "needs_review"
    assert clamp_verdict("") == "needs_review"


def test_a_model_cannot_write_an_application_owned_status():
    # Live hole independent of #169: require_status checks only the status the lead is
    # CURRENTLY in, so a model returning "applied" on a `new` lead wrote it.
    assert clamp_verdict("applied") == "needs_review"
    assert clamp_verdict("rejected") == "needs_review"


def test_apply_verdict_clamps_an_out_of_vocabulary_status_to_needs_review(tmp_path):
    # apply_verdict itself must clamp too, not just the pure helper -- this is the
    # WRITE half of the #169 hole: a model returning "applied" on a `new` lead must
    # not land `status: applied` in the note.
    v = Vault(str(tmp_path))
    _note(v, "F.md", ['company: "Alpha"', "status: new", "score: 0",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    verdict = {"verdict": "applied", "relevance_score": 82, "fit_reasoning": "fit"}
    assert apply_verdict(v, note, verdict, {}) == "applied"    # the WRITE outcome, unchanged
    assert v.read_leads()[0].status == "needs_review"          # the WRITTEN status is clamped


def test_the_judge_may_return_unjudgeable_and_it_survives_the_clamp():
    # #300: the judge had no verdict meaning "the JD never arrived", so prompt.py told it
    # to score conservatively instead. A conservative score on a blocked page lands just
    # above the dismiss threshold and routes to `research`, which means "a human should
    # investigate this" -- so fetch failures accumulated in the human queue forever.
    # Admitting the word is the whole fix; the status, its routing and its nightly
    # re-selection (DEFAULT_TRIAGE_STATUSES) already existed for the pre-gate path.
    assert clamp_verdict("unjudgeable") == "unjudgeable"
    # _status._ALIASES already folds the common misspelling; the clamp must see the
    # normalised token, not the raw one, or the alias table is dead on this path.
    assert clamp_verdict("unjudgable") == "unjudgeable"


def test_a_judge_returned_unjudgeable_lands_on_a_new_lead(tmp_path):
    # #300's forward path, and the one that matters going forward: a blocked page is now
    # named as such on the FIRST judgement, while the lead is still `new`, so it never
    # reaches `research` at all. `unjudgeable` is in DEFAULT_TRIAGE_STATUSES, so the lead
    # is refetched next run instead of waiting on a human who can add nothing to it.
    v = Vault(str(tmp_path))
    _note(v, "R.md", ['company: "Epsilon"', "status: new", "score: 0",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    verdict = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "The JD body is a bot-check page, not a job description."}
    assert apply_verdict(v, note, verdict, {}) == "applied"
    assert v.read_leads()[0].status == "unjudgeable"


def test_a_judge_returned_unjudgeable_must_not_overwrite_a_research_lead(tmp_path):
    # An earlier draft of #300 permitted this, reasoning that a `research` reached by
    # scoring an unreadable page is an artifact rather than a conclusion. The status field
    # cannot support that reasoning: it records WHERE a lead is, never HOW it got there, so
    # a conservative-score artifact and a research task a human set by hand in Obsidian are
    # byte-identical here. Overwriting on that basis breaks the repo's never-clobber rule
    # against exactly the person the queue belongs to.
    #
    # The consequence is deliberate and is NOT a silent one: leads already parked in
    # `research` by the old behaviour stay there, and clearing them is a migration a human
    # opts into, not something a nightly cron does to their queue behind them.
    v = Vault(str(tmp_path))
    _note(v, "R2.md", ['company: "Epsilon"', "status: research", "score: 60",
                       'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"research"})[0]
    verdict = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "The JD body is a bot-check page, not a job description."}
    assert apply_verdict(v, note, verdict, {}) == "unchanged"
    assert v.read_leads()[0].status == "research"


def test_a_judge_returned_unjudgeable_must_not_demote_a_shortlisted_lead(tmp_path):
    # The #169 harm, reached through the judge rather than the pre-gate: a transient block
    # on a lead already shortlisted (and possibly carrying a composed CV pointer, which
    # would be left pointing at nothing) must not erase that conclusion.
    # `apply_classification` already guards its own `unjudgeable` arm via
    # `_DECISION_REQUIRE`; the verdict path had no equivalent, so opening the vocabulary
    # without adding one would reintroduce the measured #169 incident through a new door.
    v = Vault(str(tmp_path))
    _note(v, "S.md", ['company: "Widget"', "status: shortlist", "score: 88",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"shortlist"})[0]
    verdict = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "The JD body is a bot-check page, not a job description."}
    assert apply_verdict(v, note, verdict, {}) == "unchanged"
    assert v.read_leads()[0].status == "shortlist"     # the conclusion survives


def test_a_judge_returned_unjudgeable_must_not_erase_a_dismissal(tmp_path):
    # Same rule, the other side of it. `dismiss` is a conclusion someone reached on
    # evidence; a later blocked fetch is not grounds to reopen it, and reopening would
    # put the lead back into DEFAULT_TRIAGE_STATUSES to be refetched and re-judged
    # nightly forever -- the exact treadmill this issue exists to stop.
    v = Vault(str(tmp_path))
    _note(v, "D.md", ['company: "Delta"', "status: dismiss", "score: 20",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"dismiss"})[0]
    verdict = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "The JD body is a bot-check page, not a job description."}
    assert apply_verdict(v, note, verdict, {}) == "unchanged"
    assert v.read_leads()[0].status == "dismiss"


def test_an_ordinary_verdict_may_still_rewrite_a_shortlisted_lead(tmp_path):
    # The guard must be scoped to `unjudgeable` alone. Re-reading a JD and concluding
    # `dismiss` on a shortlisted lead is a normal, correct re-judgement, and the existing
    # never-regress rule permits it. A guard applied to every verdict would freeze the
    # triage-owned states against each other.
    v = Vault(str(tmp_path))
    _note(v, "T.md", ['company: "Example Ltd"', "status: shortlist", "score: 88",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"shortlist"})[0]
    verdict = {"verdict": "dismiss", "relevance_score": 30,
               "fit_reasoning": "Scope is above the target shape on a full re-read."}
    assert apply_verdict(v, note, verdict, {}) == "applied"
    assert v.read_leads()[0].status == "dismiss"
