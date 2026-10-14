"""Sluice.followups / mark_asked / record_answer (#378): resolution, the outcome table,
CAS-fresh guards and idempotence."""
import pathlib

import pytest

from sluice.core import followups
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import VaultConflict
from datetime import date, timedelta

import sluice.core.vault as vault_mod
from sluice.core.vault import Vault

# A date the host clock cannot produce (tst-001): a facade method that reads the real clock
# instead of the injected `today` goes red on any day the suite runs.
TODAY = "2031-03-14"


def _ago(days):
    return (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def _role(titles):
    # Titles come from the seeded faker pool, never typed by hand (neu-002).
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, status="research", title=None,
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title or _TITLES[0], company="Example Ltd",
                  url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path, today=TODAY):
    return Sluice(Config(), store=Vault(str(tmp_path)), today=lambda: today)


def _text(tmp_path):
    return pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text(encoding="utf-8")


def _fm(tmp_path):
    return Vault(str(tmp_path)).read_leads()[0].fm


# ── mark_asked ──────────────────────────────────────────────────────────────────

def test_mark_asked_stamps_the_injected_date(tmp_path):
    slug = _seed(tmp_path)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "asked" and _fm(tmp_path)["followup_asked"] == TODAY


def test_mark_asked_same_day_is_unchanged(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path).mark_asked(lead=slug)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "unchanged"


def test_mark_asked_on_a_later_day_restamps(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path, _ago(11)).mark_asked(lead=slug)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "asked"
    assert _fm(tmp_path)["followup_asked"] == TODAY


@pytest.mark.parametrize("status", ["new", "shortlist", "applied"])
def test_mark_asked_refuses_a_non_research_lead_and_writes_nothing(tmp_path, status):
    slug = _seed(tmp_path, status=status)
    before = _text(tmp_path)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_status" and r.status == status
    assert _text(tmp_path) == before


def test_mark_asked_not_found_and_substring(tmp_path):
    slug = _seed(tmp_path)
    assert _app(tmp_path).mark_asked(lead="nothing").outcome == "not_found"
    assert _app(tmp_path).mark_asked(lead=slug.split(" ")[0]).outcome == "not_found"


def test_mark_asked_ambiguous_names_the_candidates(tmp_path):
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    for sub in ("Active", "Archive"):
        (leads / sub).mkdir(parents=True)
        (leads / sub / f"Example Ltd - {_TITLES[0]}.md").write_text(
            '---\ncompany: "Example Ltd"\nstatus: research\n---\n\nB\n')
    r = _app(tmp_path).mark_asked(lead=f"Example Ltd - {_TITLES[0]}")
    assert r.outcome == "ambiguous" and len(r.candidates) == 2


def test_mark_asked_refuses_on_a_status_that_changed_before_the_write(tmp_path, monkeypatch):
    # Witness: delete require_status= from mark_asked's write.
    slug = _seed(tmp_path)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_status" and r.status == "applied"
    assert "followup_asked" not in _text(tmp_path)


def test_mark_asked_refuses_a_block_value_and_keeps_it(tmp_path):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    text = pathlib.Path(ref).read_text(encoding="utf-8")
    pathlib.Path(ref).write_text(text.replace("status: research",
                                              "status: research\nfollowup_asked:\n  - by phone"))
    r = _app(tmp_path).mark_asked(lead=slug)
    assert r.outcome == "refused_unwritable"
    assert "  - by phone" in _text(tmp_path)


def test_mark_asked_refuses_when_the_store_will_not_write_one_line(tmp_path, monkeypatch):
    # rev-005: the second cause the elimination arm names -- the store's PyYAML check.
    slug = _seed(tmp_path)
    monkeypatch.setattr(vault_mod, "_single_line_write_breaks_note", lambda *a, **k: True)
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "refused_unwritable"
    assert "followup_asked" not in _text(tmp_path)


def test_mark_asked_reports_a_conflict(tmp_path, monkeypatch):
    slug = _seed(tmp_path)
    monkeypatch.setattr(Vault, "update_fields",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    assert _app(tmp_path).mark_asked(lead=slug).outcome == "conflict"


def test_mark_asked_leaves_other_keys_and_the_body_intact(tmp_path):
    slug = _seed(tmp_path)
    before = _text(tmp_path)
    _app(tmp_path).mark_asked(lead=slug)
    after = _text(tmp_path)
    assert after.replace(f"followup_asked: {TODAY}\n", "") == before


# ── record_answer ───────────────────────────────────────────────────────────────

def test_record_answer_appends_and_clears(tmp_path):
    slug = _seed(tmp_path, followup_asked=TODAY)
    r = _app(tmp_path).record_answer(lead=slug, text="SENTINEL client answer")
    assert r.outcome == "answered" and r.appended
    note = Vault(str(tmp_path)).read_leads()[0]
    assert note.fm["followup_asked"] == ""
    assert followups.answers(note.body) == f"{TODAY}: SENTINEL client answer"


def test_record_answer_rerun_appends_nothing_and_succeeds(tmp_path):
    slug = _seed(tmp_path, followup_asked=TODAY)
    _app(tmp_path).record_answer(lead=slug, text="no")
    r = _app(tmp_path).record_answer(lead=slug, text="no")
    assert r.outcome == "answered" and not r.appended
    assert _text(tmp_path).count("## Follow-up answers") == 1


def test_the_same_reply_on_a_later_day_is_written_again(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path, _ago(11)).record_answer(lead=slug, text="no")
    _app(tmp_path).record_answer(lead=slug, text="no")
    assert _text(tmp_path).count("## Follow-up answers") == 2


def test_record_answer_on_a_never_asked_lead_succeeds(tmp_path):
    slug = _seed(tmp_path)
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered"


def test_record_answer_refuses_blank_text(tmp_path):
    slug = _seed(tmp_path)
    with pytest.raises(ValueError, match="text"):
        _app(tmp_path).record_answer(lead=slug, text="   ")


def test_record_answer_refuses_a_non_research_lead_and_writes_nothing(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    before = _text(tmp_path)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_status" and _text(tmp_path) == before


def test_status_leaving_research_between_append_and_clear(tmp_path, monkeypatch):
    # Witness: delete require_status= from step 2.
    slug = _seed(tmp_path, followup_asked=TODAY)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "answered_not_cleared" and r.status == "applied"
    fm = _fm(tmp_path)
    assert fm["status"] == "applied" and fm["followup_asked"] == TODAY
    assert "## Follow-up answers" in _text(tmp_path)


def test_status_leaving_research_before_the_append_writes_nothing(tmp_path, monkeypatch):
    # inv-003: step 1 re-reads the status inside the store's own CAS write, so the one write
    # in this feature that could reach an application-owned note abstains instead.
    # Witness: delete `require_status=` from record_answer's append_body_section call.
    slug = _seed(tmp_path, followup_asked=TODAY)
    real = Vault.append_body_section

    def racing(self, ref, tag, section_md, **kw):
        Vault.update_fields(self, ref, {"status": "applied"})
        return real(self, ref, tag, section_md, **kw)

    monkeypatch.setattr(Vault, "append_body_section", racing)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_status" and r.status == "applied" and not r.appended
    assert "## Follow-up answers" not in _text(tmp_path)


def test_a_stamp_the_store_will_not_clear_is_not_reported_answered(tmp_path, monkeypatch):
    # inv-001: the store's PyYAML check can leave a one-line stamp in place. The lead would
    # then stay hidden from triage, so `answered` (exit 0) would be a failed clear reported
    # as success. Witness: delete the `awaiting_reply(fresh.fm)` re-check.
    slug = _seed(tmp_path, followup_asked=TODAY)
    monkeypatch.setattr(vault_mod, "_single_line_write_breaks_note", lambda *a, **k: True)
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "refused_unwritable" and r.appended
    assert _fm(tmp_path)["followup_asked"] == TODAY


def test_an_answer_hidden_by_an_earlier_open_fence_is_reported(tmp_path):
    # inv-002, the hand-typed half: sluice closes a fence it writes, but not one a person left
    # open earlier in the note, which would hide every later answer from the judge.
    # Witness: delete the read-back check.
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    pathlib.Path(ref).write_text(pathlib.Path(ref).read_text(encoding="utf-8")
                                 + "\n```\nmy own unclosed snippet\n")
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "answered_hidden" and r.appended


def test_an_answer_swallowed_into_an_earlier_section_is_reported(tmp_path):
    # Final review: a fence left open inside an EARLIER answers section swallows the new answer
    # into that section's text. A substring read-back still finds it there, so it must be read
    # back as its OWN section. Witness: the substring check over `answers(fresh.body)`.
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    pathlib.Path(ref).write_text(pathlib.Path(ref).read_text(encoding="utf-8")
                                 + "\n## Follow-up answers\nmy note\n```\nunclosed\n")
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "answered_hidden" and r.appended


def test_a_never_asked_lead_leaving_research_is_not_reported_answered(tmp_path, monkeypatch):
    # Witness: check the blank stamp BEFORE the fresh status.
    slug = _seed(tmp_path)
    real = Vault.update_fields

    def racing(self, ref, fields, **kw):
        real(self, ref, {"status": "applied"})
        return real(self, ref, fields, **kw)

    monkeypatch.setattr(Vault, "update_fields", racing)
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered_not_cleared"


def test_record_answer_conflict_at_step_one_writes_nothing(tmp_path, monkeypatch):
    slug = _seed(tmp_path)
    before = _text(tmp_path)
    monkeypatch.setattr(Vault, "append_body_section",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "conflict" and _text(tmp_path) == before


def test_record_answer_conflict_at_step_two_keeps_the_answer(tmp_path, monkeypatch):
    slug = _seed(tmp_path, followup_asked=TODAY)
    monkeypatch.setattr(Vault, "update_fields",
                        lambda self, *a, **kw: (_ for _ in ()).throw(VaultConflict("race")))
    r = _app(tmp_path).record_answer(lead=slug, text="x")
    assert r.outcome == "conflict" and r.appended
    assert "## Follow-up answers" in _text(tmp_path)


def test_record_answer_over_a_block_value_reports_answered_and_keeps_it(tmp_path):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    text = pathlib.Path(ref).read_text(encoding="utf-8")
    pathlib.Path(ref).write_text(text.replace("status: research",
                                              "status: research\nfollowup_asked:\n  - by phone"))
    assert _app(tmp_path).record_answer(lead=slug, text="x").outcome == "answered"
    assert "followup_asked:\n  - by phone" in _text(tmp_path)


def test_record_answer_demotes_a_pasted_heading(tmp_path):
    slug = _seed(tmp_path)
    _app(tmp_path).record_answer(lead=slug, text="a\n## b\nc")
    body = Vault(str(tmp_path)).read_leads()[0].body
    assert followups.answers(body) == f"{TODAY}: a\n### b\nc"


# ── followups ───────────────────────────────────────────────────────────────────

def test_followups_reads_research_leads_with_the_injected_date(tmp_path):
    # Witness: `_today()` for `(self._today or _today)()` in followups() -> red.
    _seed(tmp_path, url="https://example.invalid/1", title=_TITLES[0], first_seen=_ago(10))
    _seed(tmp_path, url="https://example.invalid/2", title=_TITLES[1], first_seen=_ago(10),
          followup_asked=_ago(7))
    _seed(tmp_path, url="https://example.invalid/3", title=_TITLES[2], status="shortlist",
          followup_asked=_ago(7))
    q = _app(tmp_path).followups()
    assert len(q.rows) == 2 and len(q.asked) == 1
    assert q.asked[0].waited_days == 7 and q.oldest_days == 10


def _note_text(tmp_path):
    return pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text(encoding="utf-8")


def test_an_answer_clears_a_triage_blocked_stamp(tmp_path):
    # #302: an answered research task is the person's live decision; a stamp from before the
    # question must not bring it back into `leads unpark`.
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked=_ago(9))
    assert _app(tmp_path).record_answer(lead=slug, text="the band is fine").outcome == "answered"
    assert Vault(str(tmp_path)).read_leads()[0].fm["triage_blocked"] == ""


def test_an_answer_on_an_unstamped_lead_adds_no_triage_blocked_key(tmp_path):
    slug = _seed(tmp_path, followup_asked=_ago(3))
    _app(tmp_path).record_answer(lead=slug, text="the band is fine")
    assert "triage_blocked" not in _note_text(tmp_path)


def test_an_answer_leaves_a_hand_typed_triage_blocked_alone(tmp_path):
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked="my own note")
    _app(tmp_path).record_answer(lead=slug, text="the band is fine")
    assert "triage_blocked: my own note" in _note_text(tmp_path)


def test_a_stamp_turned_into_a_block_list_mid_answer_is_left_intact(tmp_path, monkeypatch):
    # #302: a snapshot holding a date stamp whose fresh value a person turned multi-line
    # passes the snapshot check; the clear's fresh re-read must then leave that block alone
    # while the answer still lands. The racer fires on the ENUMERATION read (it returns
    # pre-edit bytes), so the snapshot still shows the date and the edit lands before the write.
    from tests.conftest import racing_read
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked=_ago(9))
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    path = pathlib.Path(ref)

    def _hand_edit():
        lines = path.read_text(encoding="utf-8").split("\n")
        out = []
        for ln in lines:
            out.append("triage_blocked:\n  - my own note" if ln.startswith("triage_blocked:") else ln)
        path.write_text("\n".join(out), encoding="utf-8")

    state = racing_read(monkeypatch, ref, _hand_edit)
    _app(tmp_path).record_answer(lead=slug, text="the band is fine")

    assert state["fired"]
    text = _note_text(tmp_path)
    assert "triage_blocked:\n  - my own note" in text
    assert Vault(str(tmp_path)).read_leads()[0].fm["followup_asked"] == ""


def test_a_stamp_replaced_by_one_line_of_text_mid_answer_is_left_intact(tmp_path, monkeypatch):
    # #302: the clear compares the fresh value to the snapshot's stamp, so a word a person
    # typed over the stamp after the snapshot survives -- and the answer still lands and
    # returns the lead to triage, since the stamp clear is a write of its own.
    from tests.conftest import racing_read
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked=_ago(9))
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    path = pathlib.Path(ref)

    def _hand_edit():
        lines = path.read_text(encoding="utf-8").split("\n")
        out = ["triage_blocked: my own note" if ln.startswith("triage_blocked:") else ln
               for ln in lines]
        path.write_text("\n".join(out), encoding="utf-8")

    state = racing_read(monkeypatch, ref, _hand_edit)
    result = _app(tmp_path).record_answer(lead=slug, text="the band is fine")

    assert state["fired"]
    assert result.outcome == "answered"
    assert "triage_blocked: my own note" in _note_text(tmp_path)
    assert Vault(str(tmp_path)).read_leads()[0].fm["followup_asked"] == ""
