import json
from types import SimpleNamespace
from sluice.track.config import TrackConfig
from sluice.track import classify as C


def _lead(company, role, status="applied", path=None):
    return SimpleNamespace(fm={"company": company, "role": role}, status=status,
                           path=path or f"/v/Job Leads/{company} - {role}.md")


class FakeBackend:
    def __init__(self, reply): self.reply = reply
    def complete(self, prompt): return self.reply


def _msg(frm="jobs@company.com", subject="Interview", body="", thread="t1"):
    return {"headers": {"from": frm, "subject": subject}, "body_text": body,
            "thread_id": thread, "attachments": []}


def test_classify_matches_single_lead():
    leads = [_lead("Tidemark", "Banker, DevEx"), _lead("Northwind", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": "2026-07-20T10:00", "links": ["https://x/prep"],
                                 "materials": ["Culture deck"], "summary": "HM interview booked"}))
    ev = C.classify(_msg(subject="Tidemark interview"), leads, be, TrackConfig(), ics=None)
    assert ev.lead_slug is not None and "tidemark" in ev.lead_slug.lower()
    assert ev.type == "interview" and ev.confidence == 0.9
    assert ev.materials == ["Culture deck"] and ev.links == ["https://x/prep"]


def test_ambiguous_match_sets_candidates_and_no_slug():
    leads = [_lead("Ravenbank", "EM Cards"), _lead("Ravenbank", "EM Payments")]
    be = FakeBackend(json.dumps({"lead": "Ravenbank", "type": "rejection", "confidence": 0.8,
                                 "when": None, "links": [], "materials": [], "summary": "rejected"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.lead_slug is None and len(ev.candidates) == 2


def test_not_job_when_no_match():
    leads = [_lead("Tidemark", "Analyst")]
    be = FakeBackend(json.dumps({"lead": None, "type": "not_job", "confidence": 0.99,
                                 "when": None, "links": [], "materials": [], "summary": "newsletter"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.type == "not_job" and ev.lead_slug is None


def test_malformed_llm_output_is_not_job():
    leads = [_lead("Tidemark", "Analyst")]
    ev = C.classify(_msg(), leads, FakeBackend("not json at all"), TrackConfig(), ics=None)
    assert ev.type == "not_job" and ev.confidence == 0.0


def test_wrongtyped_json_degrades_to_not_job():
    leads = [_lead("Tidemark", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": "high",
                                 "links": 5, "materials": None, "summary": "x"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.type == "not_job" and ev.confidence == 0.0


def test_when_falls_back_to_ics_start():
    from sluice.track.ics import IcsEvent
    from datetime import datetime, timezone
    leads = [_lead("Tidemark", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    ics = IcsEvent(uid="u", start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=ics)
    assert ev.when == ics.start.isoformat()


def test_classify_seeds_materials_from_attachments():
    leads = [_lead("Tidemark", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    msg = _msg()
    msg["attachments"] = [{"filename": "Culture Deck.pdf", "mime": "application/pdf", "data": b"x"},
                          {"filename": "invite.ics", "mime": "text/calendar", "data": b"y"}]
    ev = C.classify(msg, leads, be, TrackConfig(), ics=None)
    assert "Culture Deck.pdf" in ev.materials and "invite.ics" not in ev.materials
