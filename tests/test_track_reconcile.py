import tempfile, pathlib
from datetime import datetime, timezone
from sluice.core.vault import Vault
from sluice.track.config import TrackConfig
from sluice.track.classify import Event
from sluice.track.ics import IcsEvent
from sluice.track import reconcile as R
from tests.test_track_google_client import FakeGoogleClient


def _vault_with(slug, status):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(f'---\ncompany: "X"\nrole: "Analyst"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.path.endswith(f"{slug}.md")][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _ics():
    return IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc))


def test_interview_with_ics_auto_advances_and_calendars():
    v, notes, path = _vault_with("Tidemark - EM", "applied")
    ev = Event(lead_slug="Tidemark - EM", type="interview", confidence=0.9, ics=_ics(),
               materials=["Deck"], links=["https://x/deck"])
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.action == "applied" and res.status_to == "interview"
    assert res.calendar == "created" and res.materials_written is True
    text = pathlib.Path(path).read_text()
    assert "status: interview" in text and "interview_date" in text and "Deck" in text


def test_cancellation_ics_does_not_advance():
    v, notes, path = _vault_with("Tidemark - EM", "interview")
    ics = _ics(); ics.method = "CANCEL"
    ev = Event(lead_slug="Tidemark - EM", type="interview", confidence=0.9, ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()


def test_soft_rejection_proposes_not_auto():
    v, notes, path = _vault_with("Tidemark - EM", "phone_screen")
    ev = Event(lead_slug="Tidemark - EM", type="rejection", confidence=0.7, summary="on file")  # below auto_reject_min
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed" and "status: phone_screen" in pathlib.Path(path).read_text()


def test_specific_high_conf_rejection_auto():
    v, notes, path = _vault_with("Tidemark - EM", "phone_screen")
    ev = Event(lead_slug="Tidemark - EM", type="rejection", confidence=0.95, summary="not moving forward")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "applied" and res.status_to == "rejected"
    assert "status: rejected" in pathlib.Path(path).read_text()


def test_ambiguous_lead_proposes():
    v, notes, _ = _vault_with("Tidemark - EM", "applied")
    ev = Event(lead_slug=None, candidates=["A", "B"], type="interview", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"


def test_never_regress_refuses():
    v, notes, path = _vault_with("Tidemark - EM", "offer")
    ev = Event(lead_slug="Tidemark - EM", type="phone_screen", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: offer" in pathlib.Path(path).read_text()
