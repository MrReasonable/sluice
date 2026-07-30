import json
from types import SimpleNamespace
from sluice.track.config import TrackConfig
from sluice.track import classify as C


def _lead(company, role, status="applied", slug=None):
    slug = slug or f"{company} - {role}"
    return SimpleNamespace(fm={"company": company, "role": role}, status=status,
                           ref=f"/v/Job Leads/{slug}.md", slug=slug)


class FakeBackend:
    def __init__(self, reply): self.reply = reply
    def complete(self, prompt): return self.reply


class RaisingBackend:
    # The whole point of #40: a backend that dies mid-classification must not be
    # answered as a confident "not a job".
    def complete(self, prompt): raise RuntimeError("backend down")


def _msg(frm="jobs@company.com", subject="Interview", body="", thread="t1"):
    return {"headers": {"from": frm, "subject": subject}, "body_text": body,
            "thread_id": thread, "attachments": []}


def test_classify_matches_single_lead():
    leads = [_lead("Example Tidal", "Banker, DevEx"), _lead("Example Northgate", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example Tidal", "type": "interview", "confidence": 0.9,
                                 "when": "2026-07-20T10:00", "links": ["https://x/prep"],
                                 "materials": ["Culture deck"], "summary": "HM interview booked"}))
    ev = C.classify(_msg(subject="Example Tidal interview"), leads, be, TrackConfig(), ics=None)
    assert ev.lead_slug is not None and "example tidal" in ev.lead_slug.lower()
    assert ev.type == "interview" and ev.confidence == 0.9
    assert ev.materials == ["Culture deck"] and ev.links == ["https://x/prep"]


def test_ambiguous_match_sets_candidates_and_no_slug():
    leads = [_lead("Ravenbank", "EM Cards"), _lead("Ravenbank", "EM Payments")]
    be = FakeBackend(json.dumps({"lead": "Ravenbank", "type": "rejection", "confidence": 0.8,
                                 "when": None, "links": [], "materials": [], "summary": "rejected"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.lead_slug is None and len(ev.candidates) == 2


def test_not_job_when_no_match():
    leads = [_lead("Example Tidal", "Analyst")]
    be = FakeBackend(json.dumps({"lead": None, "type": "not_job", "confidence": 0.99,
                                 "when": None, "links": [], "materials": [], "summary": "newsletter"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.type == "not_job" and ev.lead_slug is None


def test_malformed_llm_output_is_unknown():
    # No parseable JSON is a classification FAILURE, not a confident not_job (#40): the
    # model returned nothing we can read, so we have no evidence about this email at all.
    leads = [_lead("Example Tidal", "Analyst")]
    ev = C.classify(_msg(), leads, FakeBackend("not json at all"), TrackConfig(), ics=None)
    assert ev.type == "unknown" and ev.confidence == 0.0


def test_wrongtyped_json_is_unknown_not_a_confident_not_job():
    # A partially-corrupt response (confidence is not a number) can't be trusted for ANY
    # field, including the type it claims -- so we surface `unknown` rather than manufacture
    # a confident not_job that reconcile would silently skip (#40).
    leads = [_lead("Example Tidal", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example Tidal", "type": "interview", "confidence": "high",
                                 "links": 5, "materials": None, "summary": "x"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.type == "unknown" and ev.confidence == 0.0


def test_classify_failure_returns_unknown_not_a_confident_not_job():
    # #40: a backend error must surface as `unknown`, never as the confident default that
    # reconcile reads as "not a job email" and silently skips -- the path by which a
    # rejection email vanishes and its lead sits at `applied` forever.
    leads = [_lead("Example Tidal", "Analyst")]
    ev = C.classify(_msg(), leads, RaisingBackend(), TrackConfig(), ics=None)
    assert ev.type == "unknown"
    assert ev.lead_slug is None and ev.candidates == []


def test_not_job_is_only_returned_on_evidence_never_on_exception():
    # Pin the invariant behind #40: not_job is a CLAIM about the email, only ever returned
    # when the model actually said so. An exception must never manufacture that claim.
    leads = [_lead("Example Tidal", "Analyst")]
    said = FakeBackend(json.dumps({"lead": None, "type": "not_job", "confidence": 0.9,
                                   "when": None, "links": [], "materials": [], "summary": "newsletter"}))
    assert C.classify(_msg(), leads, said, TrackConfig()).type == "not_job"
    assert C.classify(_msg(), leads, RaisingBackend(), TrackConfig()).type == "unknown"


def test_model_returned_unknown_maps_to_not_job():
    # `unknown` is a code-internal sentinel for a classification FAILURE (#40), and the failure
    # signal must be un-forgeable from model output: a model that literally emits type="unknown"
    # is a completed, evidence-bearing response, so the _TYPES clamp folds it to not_job and it
    # never reaches reconcile's failure branch. Only the except path may produce `unknown`.
    leads = [_lead("Example Tidal", "Analyst")]
    be = FakeBackend(json.dumps({"lead": None, "type": "unknown", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.type == "not_job"


def test_when_falls_back_to_ics_start():
    from sluice.track.ics import IcsEvent
    from datetime import datetime, timezone
    leads = [_lead("Example Tidal", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example Tidal", "type": "interview", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    ics = IcsEvent(uid="u", start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=ics)
    assert ev.when == ics.start.isoformat()


def test_classify_seeds_materials_from_attachments():
    leads = [_lead("Example Tidal", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example Tidal", "type": "interview", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    msg = _msg()
    msg["attachments"] = [{"filename": "Culture Deck.pdf", "mime": "application/pdf", "data": b"x"},
                          {"filename": "invite.ics", "mime": "text/calendar", "data": b"y"}]
    ev = C.classify(msg, leads, be, TrackConfig(), ics=None)
    assert "Culture Deck.pdf" in ev.materials and "invite.ics" not in ev.materials


def test_receipt_typed_and_llm_lead_ignored():
    # A receipt: the LLM may still name a lead, but classify must NOT resolve it into the
    # AUTHORITATIVE fields -- the deterministic matcher (engine) owns lead_slug/candidates
    # for receipts. (Its guess is not thrown away -- see llm_lead_slug below.)
    leads = [_lead("Example", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    ev = C.classify(_msg(frm="jobs@example.com", subject="Thanks for applying"),
                    leads, be, TrackConfig(), ics=None)
    assert ev.type == "receipt"
    assert ev.lead_slug is None and ev.candidates == []      # NOT resolved by name
    assert ev.sender == "jobs@example.com" and ev.subject == "Thanks for applying"
    assert ev.receipt_tier is None                            # engine sets this later


def test_receipt_llm_fallback_resolution_stored_separately():
    # #10 fix-round-1: the LLM's own name-based guess for a receipt is kept, but ONLY in
    # llm_lead_slug/llm_candidates -- fields the write path never reads. engine.run uses
    # this solely to decide whether to SURFACE (dead-letter, never advance) a receipt about
    # a lead match_receipt structurally cannot see (one already past shortlist).
    leads = [_lead("Example", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    ev = C.classify(_msg(frm="jobs@example.com", subject="Thanks for applying"),
                    leads, be, TrackConfig(), ics=None)
    assert ev.lead_slug is None and ev.candidates == []       # authoritative fields untouched
    assert ev.llm_lead_slug is not None and "example" in ev.llm_lead_slug.lower()
    assert ev.llm_candidates == []


def test_prompt_teaches_receipt_definition_and_unlisted_company_permission():
    # Finding 1 (whole-branch review): the prompt's original framing told the model to
    # classify "against the in-flight applications below" and to "only match an
    # application actually listed" -- but every receipt this feature exists to catch
    # belongs to a SHORTLIST lead, which engine._INFLIGHT structurally excludes from
    # that list. Under the old wording, the honest response to a real receipt was
    # not_job (which reconcile silently skips), making the feature inert against a
    # real backend while every offline test's fake backend hardcodes type="receipt".
    # Pin both required clauses so a later edit cannot silently drop either one.
    leads = [_lead("Example", "Analyst")]
    prompt = C.build_prompt(_msg(), leads, TrackConfig())
    # (a) defines receipt as an application confirmation/acknowledgement
    assert "receipt is an automated acknowledgement" in prompt
    assert "application was submitted or received" in prompt
    # (b) explicit permission to use it even when the company is unlisted, with lead: null
    assert "even when its company is not in the list below" in prompt
    assert "setting lead to null in that case" in prompt
    # shortlist leads must never appear in the prompt itself -- lead-resolution for a
    # receipt is owned by deterministic domain matching, not this prompt (unchanged
    # design decision; only the wording changed).
    assert "shortlist" not in prompt.lower()


def test_receipt_llm_fallback_ambiguous_sets_candidates_and_no_slug():
    # Mirrors test_ambiguous_match_sets_candidates_and_no_slug, but for the fallback fields:
    # two same-company leads means the LLM's guess cannot resolve uniquely either.
    leads = [_lead("Example", "EM Cards"), _lead("Example", "EM Payments")]
    be = FakeBackend(json.dumps({"lead": "Example", "type": "receipt", "confidence": 0.8,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    ev = C.classify(_msg(), leads, be, TrackConfig(), ics=None)
    assert ev.llm_lead_slug is None and len(ev.llm_candidates) == 2
    assert ev.lead_slug is None and ev.candidates == []       # authoritative fields untouched


def test_none_valued_headers_never_reach_the_receipt_evidence():
    # A present-but-None From/Subject reached reconcile as a literal None and was written
    # into the receipt evidence section as the string "None"; a None headers dict raised
    # AttributeError out of build_prompt. `.get(key, "")` only covers a MISSING key --
    # `or ""` covers both. Asserting type == "receipt" (not the `unknown` an exception
    # would produce) is what pins the no-raise half.
    leads = [_lead("Example", "Analyst")]
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    ev = C.classify({"headers": {"from": None, "subject": None}, "body_text": None,
                     "thread_id": "t", "attachments": []}, leads, be, TrackConfig(), ics=None)
    assert ev.type == "receipt" and ev.sender == "" and ev.subject == ""
    ev2 = C.classify({"headers": None, "body_text": None, "thread_id": "t", "attachments": []},
                     leads, be, TrackConfig(), ics=None)
    assert ev2.type == "receipt" and ev2.sender == "" and ev2.subject == ""
