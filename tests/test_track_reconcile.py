import tempfile, pathlib
from datetime import datetime, timezone
import pytest
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.track.config import TrackConfig
from sluice.track.classify import Event, classify
from tests.test_track_classify import RaisingBackend
from sluice.track.ics import IcsEvent
from sluice.track import reconcile as R
from tests.test_track_google_client import FakeGoogleClient


def _vault_with(slug, status):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(f'---\ncompany: "X"\nrole: "Analyst"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _shortlist_with(slug, url, company="Example", status="shortlist"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(
        f'---\ncompany: "{company}"\nrole: "Analyst"\nurl: "{url}"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _receipt_ev(tier, slug, sender="jobs@example.com", subject="Thanks for applying", conf=0.9):
    return Event(type="receipt", receipt_tier=tier, lead_slug=slug, confidence=conf,
                 sender=sender, subject=subject, summary="application received")


def _ics():
    return IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc))


def test_interview_with_ics_auto_advances_and_calendars():
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=_ics(),
               materials=["Deck"], links=["https://x/deck"])
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.action == "applied" and res.status_to == "interview"
    assert res.calendar == "created" and res.materials_written is True
    text = pathlib.Path(path).read_text()
    assert "status: interview" in text and "interview_date" in text and "Deck" in text


def test_cancellation_ics_does_not_advance():
    v, notes, path = _vault_with("Example Tidal - EM", "interview")
    ics = _ics(); ics.method = "CANCEL"
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()


def test_soft_rejection_proposes_not_auto():
    v, notes, path = _vault_with("Example Tidal - EM", "phone_screen")
    ev = Event(lead_slug="Example Tidal - EM", type="rejection", confidence=0.7, summary="on file")  # below auto_reject_min
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed" and "status: phone_screen" in pathlib.Path(path).read_text()


def test_specific_high_conf_rejection_auto():
    v, notes, path = _vault_with("Example Tidal - EM", "phone_screen")
    ev = Event(lead_slug="Example Tidal - EM", type="rejection", confidence=0.95, summary="not moving forward")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "applied" and res.status_to == "rejected"
    assert "status: rejected" in pathlib.Path(path).read_text()


def test_ambiguous_lead_proposes():
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug=None, candidates=["A", "B"], type="interview", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"


def test_never_regress_refuses():
    v, notes, path = _vault_with("Example Tidal - EM", "offer")
    ev = Event(lead_slug="Example Tidal - EM", type="phone_screen", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: offer" in pathlib.Path(path).read_text()


def test_unknown_event_proposes_with_an_honest_label_never_skipped():
    # A classification we could not make (#40) must surface for a human. It is NOT the
    # not_job/update shape that reconcile silently skips, so it proposes -- and with an
    # honest label ("classification failed"), not the misleading "unmatched/ambiguous"
    # that the generic unmatched path would attach.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug=None, type="unknown", summary="")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"
    assert "classification failed" in res.proposal


def test_applied_lead_with_unclassifiable_mail_is_not_silently_unchanged():
    # The exact #40 failure, end to end: a rejection email whose classification THROWS used to
    # become a confident not_job -> reconcile skipped it -> the lead sat at `applied` forever.
    # Now classify yields `unknown`, reconcile proposes it for review, and the note is untouched
    # -- surfaced, never skipped, never regressed.
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    msg = {"headers": {"from": "hr@x", "subject": "Re: your application"}, "body_text": "",
           "thread_id": "t1", "attachments": [], "message_id": "m1"}
    ev = classify(msg, list(notes.values()), RaisingBackend(), TrackConfig())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"
    assert "status: applied" in pathlib.Path(path).read_text()


def test_receipt_proof_advances_shortlist_to_applied():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    text = pathlib.Path(path).read_text()
    assert "status: applied" in text and "## Application receipt" in text


def test_receipt_below_confidence_floor_proposes():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst", conf=0.5)  # below auto_apply_min
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_corroborated_proposes_not_advances():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("corroborated", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed"
    text = pathlib.Path(path).read_text()
    assert "status: shortlist" in text and "## Application receipt" not in text  # absence-of-write


def test_receipt_ambiguous_proposes_neither():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = Event(type="receipt", receipt_tier="corroborated", lead_slug=None,
               candidates=["Example - Analyst", "Example - Manager"], confidence=0.9)
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_cannot_regress_non_shortlist():
    # A receipt whose matched note is already at interview must NOT advance/regress it,
    # and must not PROPOSE it either -- see the next test for why proposing is its own
    # defect rather than a harmless fallback.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1", status="interview")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()
    assert res.action == "skipped"


def test_receipt_for_already_applied_lead_is_skipped_not_proposed():
    # A matched note that can_apply already rules out must not be proposed: the only
    # runnable form of a receipt proposal is `track confirm --to applied`, which routes
    # through that SAME predicate and is refused forever, while the dead-letter row it
    # creates re-surfaces on every future run -- #49's un-runnable-hint shape. The
    # commonest producer is a second receipt for a lead this same run already advanced.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1",
                                  status="applied")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "skipped" and res.proposal is None
    assert res.status_from == "applied" and res.status_to is None
    assert "## Application receipt" not in pathlib.Path(path).read_text()   # absence-of-write


def test_receipt_idempotent_no_double_evidence():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.message_id = "m1"
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    # Re-read the now-applied note; a second identical receipt must not double-write.
    note2 = [n for n in v.read_leads() if n.slug == "Example - Analyst"][0]
    ev2 = _receipt_ev("proof", "Example - Analyst"); ev2.message_id = "m1"
    R.reconcile(ev2, {}, v, TrackConfig(), FakeGoogleClient(),
                shortlist_by_slug={"Example - Analyst": note2})
    assert pathlib.Path(path).read_text().count("## Application receipt") == 1


def test_receipt_advance_writes_no_interview_fields():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.links = ["https://example.com/portal"]
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    text = pathlib.Path(path).read_text()
    assert "interview_date" not in text and "interview_link" not in text


def test_receipt_dry_run_reports_advance_but_writes_nothing():
    # dry_run must report the WOULD-BE outcome (so callers can preview it) while the
    # vault stays untouched -- `--dry-run` writing to a real note is a serious defect,
    # not a cosmetic one, so this pins the `if not dry_run:` guard on the write itself
    # rather than trusting the returned result alone.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    before = pathlib.Path(path).read_text()
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), True, shortlist_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    after = pathlib.Path(path).read_text()
    assert after == before  # byte-unchanged: no frontmatter edit, no evidence section
    assert "status: shortlist" in after and "## Application receipt" not in after


def test_receipt_evidence_survives_a_status_write_conflict():
    # Write ORDER is load-bearing. Status-then-evidence meant a VaultConflict (#16) on
    # the evidence append left the lead already `applied` -- out of the shortlist set
    # match_receipt searches -- so no later run could re-attach the evidence and it was
    # lost unrecoverably. Evidence-then-status makes a conflict on EITHER write leave the
    # lead in `shortlist`: engine.run's per-message except skips seen.add and the whole
    # message retries next run.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")

    class ConflictOnStatus(Vault):
        def update_fields(self, ref, fields):
            raise VaultConflict("concurrent edit")

    boom = ConflictOnStatus(v.dir)
    ev = _receipt_ev("proof", "Example - Analyst"); ev.message_id = "m1"
    with pytest.raises(VaultConflict):
        R.reconcile(ev, {}, boom, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    text = pathlib.Path(path).read_text()
    assert "status: shortlist" in text                  # never left the retryable state
    assert "## Application receipt" in text             # evidence already durable

    # ...and the retry completes it, without double-writing the evidence (idempotent by tag).
    v2 = Vault(v.dir)
    note2 = [n for n in v2.read_leads() if n.slug == "Example - Analyst"][0]
    ev2 = _receipt_ev("proof", "Example - Analyst"); ev2.message_id = "m1"
    res = R.reconcile(ev2, {}, v2, TrackConfig(), FakeGoogleClient(),
                      shortlist_by_slug={"Example - Analyst": note2})
    text2 = pathlib.Path(path).read_text()
    assert res.action == "applied" and "status: applied" in text2
    assert text2.count("## Application receipt") == 1


def test_receipt_confidence_floor_is_inclusive():
    # The design specifies >=, i.e. a receipt AT the floor still advances; a boundary
    # value is required because 0.5/0.9 (the other tests' confidences) sit strictly
    # off the floor and can't distinguish >= from >.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst", conf=TrackConfig().auto_apply_min)
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    assert "status: applied" in pathlib.Path(path).read_text()
