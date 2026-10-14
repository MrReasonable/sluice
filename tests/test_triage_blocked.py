"""#302: the `triage_blocked` stamp -- what counts as one, where triage writes and clears it,
and how the engine counts a refused `unjudgeable` verdict on a `research` lead."""
import os

import pytest

import sluice.triage.apply as apply_mod
from sluice.core.leads import blocked_stamp
from sluice.core.vault import Vault
from sluice.triage.apply import apply_classification, apply_verdict


@pytest.mark.parametrize("value,expected", [
    ("2031-03-14", "2031-03-14"),
    (" 2031-03-14 ", " 2031-03-14 "),   # the RAW value: require_unchanged compares it as stored
    ("", ""),
    ("blocked", ""),                     # a hand-typed word is the person's own text
    ("|", ""),                           # a block scalar reads as its indicator, not blank
    ("2031-03-14\n", ""),                # never a multi-line value, even one strip() would pass
    ("2031-13-40", ""),
])
def test_blocked_stamp_accepts_only_a_one_line_iso_date(value, expected):
    assert blocked_stamp({"triage_blocked": value}) == expected


def test_blocked_stamp_of_a_note_without_the_key_is_blank():
    assert blocked_stamp({}) == ""
    assert blocked_stamp({"triage_blocked": None}) == ""


TODAY = "2031-03-14"
UNJUDGEABLE = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "the page is a sign-in wall, not a posting"}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """Fix triage's clock to a date the host cannot produce, so a row that read the real
    clock goes red rather than passing on the day it was written."""
    class _Date(apply_mod.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(TODAY)
    monkeypatch.setattr(apply_mod, "date", _Date)


def _note(v, name, status, extra=()):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(['company: "Example Ltd"', f"status: {status}",
                                     "score: 60", 'relevance_notes: ""', *extra])
                + "\n---\n# body\n")
    return next(n for n in v.read_leads() if os.path.basename(str(n.ref)) == name)


def _text(note):
    return open(note.ref, encoding="utf-8").read()


def test_a_refused_unjudgeable_on_a_research_lead_stamps_today_and_nothing_else(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    after = _text(note)
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY
    # Only the one key was added: status, score, body and relevance_notes are untouched.
    assert after.replace(f"triage_blocked: {TODAY}\n", "") == before


@pytest.mark.parametrize("status", ["new", "unjudgeable"])
def test_a_lead_whose_unjudgeable_write_lands_is_not_stamped(tmp_path, status):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", status)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "applied"
    assert "triage_blocked" not in _text(note)


def test_an_application_owned_lead_is_not_stamped(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "applied")
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "skipped"
    assert _text(note) == before


def test_a_new_lead_set_to_research_mid_judge_is_not_stamped(tmp_path):
    # Witnesses the snapshot-`research` gate in _stamp_blocked. A `shortlist` row cannot:
    # the stamp's own require_status refuses that either way. Here the FRESH status is
    # `research`, so only the snapshot gate keeps the stamp off; the next run stamps it.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new")                       # snapshot: new
    v.update_fields(note.ref, {"status": "research"})   # a human moves it mid-judge
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_research_lead_that_becomes_applied_mid_judge_is_not_stamped(tmp_path):
    # Witnesses the stamp's require_status. The snapshot is `research`, so _guarded and the
    # snapshot gate both pass; only the CAS-fresh re-read inside update_fields refuses.
    # `applied`, not `new`: the main verdict cannot land on `applied` either, so the stamp
    # write is the one that decides the outcome.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_lead_marked_asked_mid_judge_is_not_stamped(tmp_path):
    # Witnesses require_blank on the stamp: the human's follow-up question outranks it.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    v.update_fields(note.ref, {"followup_asked": TODAY})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_second_run_the_same_day_is_still_blocked_and_writes_nothing(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    note = v.read_leads()[0]                              # the snapshot now carries TODAY
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    assert _text(note) == before


def test_an_older_stamp_on_a_lead_that_leaves_research_mid_judge_is_unchanged(tmp_path):
    # Witnesses the `== today` arm: a check of "has a stamp" would call this "blocked".
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked: 2031-03-01"])
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert v.read_leads()[0].fm["triage_blocked"] == "2031-03-01"


def test_an_older_stamp_on_a_research_lead_is_advanced_to_today(tmp_path):
    # The stamp's require_unchanged compares against the snapshot's own stamp, so a lead
    # stamped on an earlier run is re-stamped rather than refused.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked: 2031-03-01"])
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY


def test_a_multiline_hand_typed_triage_blocked_is_left_alone(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked:", "  - my own note"])
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked:\n  - my own note" in _text(note)


@pytest.mark.parametrize("value", ["my own note", "|", '"waiting on recruiter"'])
def test_a_single_line_hand_typed_triage_blocked_is_left_alone(tmp_path, value):
    # Witnesses _stamp_blocked's early return on a non-stamp value. preserve_block_values
    # cannot: every value here sits on the key's own line, so a single-line write over it
    # is not a corruption to PyYAML, only a clobber of the person's text.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", [f"triage_blocked: {value}"])
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert _text(note) == before


def test_a_word_typed_into_triage_blocked_mid_judge_is_not_stamped_over(tmp_path):
    # Witnesses the stamp's require_unchanged. The snapshot holds no key, so the early return
    # cannot fire; only the CAS-fresh re-read sees the word the person typed meanwhile.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    v.update_fields(note.ref, {"triage_blocked": "my own note"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert v.read_leads()[0].fm["triage_blocked"] == "my own note"


def test_the_pre_judge_gate_refusal_on_a_research_lead_stamps_too(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    assert apply_classification(v, note, "unjudgeable", "no job description") == "blocked"
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY
    assert v.read_leads()[0].status == "research"


def test_a_refused_unjudgeable_on_a_shortlist_lead_is_unchanged_and_unstamped(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "shortlist")
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)
