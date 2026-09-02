"""Three findings that share a shape: a guard, a claim, or a sweep that stopped one short.

- `--dry-run` sent a real Telegram message and printed a durability claim that is false on
  exactly that path.
- `_advance`'s `frontmatter_safe` abstention skipped the ICS fallback it was sitting next to.
- `confirm` writes `interview_date` too, and the "enumerated, not hand-listed" test that was
  supposed to prevent exactly this scanned one function.
"""
import pathlib
import tempfile
from datetime import datetime, timezone

import pytest

from sluice import cli
from sluice.core.config import Config
from sluice.core.vault import Vault
from sluice.track import engine as E
from sluice.track import reconcile as R
from sluice.track.classify import Event
from sluice.track.config import TrackConfig
from sluice.track.deadletter import DeadLetterDb
from sluice.track.engine import RunReport, TrackFailure
from sluice.track.ics import IcsEvent
from tests.test_track_google_client import FakeGoogleClient


# ---- --dry-run must not reach outside the machine -----------------------------------------

class _Args:
    dry_run = True
    backend = None


def _drive(monkeypatch, rep, dry_run=True):
    sent = []

    class _Sluice:
        def __init__(self, config):
            pass

        def track(self, **kw):
            return rep

    args = _Args()
    args.dry_run = dry_run
    monkeypatch.setattr("sluice.core.app.Sluice", _Sluice)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: (sent.append(body), "sent")[1])
    code = cli.cmd_track_run(args, Config())
    return code, sent


def test_a_dry_run_does_NOT_send_a_telegram_message(monkeypatch):
    """`--dry-run` is documented and tested repo-wide as writing nothing.

    The notify block had no dry-run guard, so a preview run pushed a real external message --
    the one side effect a preview must never have.
    """
    rep = RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")])
    _code, sent = _drive(monkeypatch, rep, dry_run=True)
    assert sent == [], "a preview run sent a real notification"


def test_a_REAL_run_still_notifies(monkeypatch):
    # The guard must not disable the alerting this branch exists to add.
    rep = RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")])
    _code, sent = _drive(monkeypatch, rep, dry_run=False)
    assert sent and "m1" in sent[0]


def test_a_dry_run_does_not_claim_the_failures_were_recorded(monkeypatch, capsys):
    """`engine.run` records the row only `if not dry_run`, so the fallback line's promise --
    "recorded in the dead-letter store and re-surface every run" -- was false on this path."""
    rep = RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")])
    _drive(monkeypatch, rep, dry_run=True)
    err = capsys.readouterr().err
    assert "dead-letter store and re-surface" not in err, err


# ---- the abstention must fall through to the ICS date -------------------------------------

def _vault(status="applied"):
    root = pathlib.Path(tempfile.mkdtemp())
    leads = root / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    p = leads / "Example Tidal - EM.md"
    p.write_text(f'---\ncompany: "Example Tidal"\nrole: "EM"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(str(root))
    return v, {n.slug: n for n in v.read_leads() if n.slug == "Example Tidal - EM"}, p


def test_an_unsafe_model_date_falls_back_to_the_ics_DTSTART():
    """The `elif` bound to `ev.when` being falsy, not to the guard rejecting it.

    So an invite carrying an authoritative DTSTART, plus a model `when` with a quote in it,
    wrote no `interview_date` at all -- discarding the junk AND the good value. The comment
    three lines up says losing the interview signal would be the worse failure; the code did
    exactly that.
    """
    v, notes, path = _vault()
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               when='2026-07-15 10:00 "BST"', ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    text = path.read_text()
    assert res.status_to == "interview"
    assert 'interview_date: "2026-07-15"' in text, (
        "the authoritative DTSTART was discarded along with the model's junk")
    assert "BST" not in text, "the unsafe value must not be written"


def test_an_unsafe_model_date_with_NO_ics_still_abstains_without_blocking_the_advance():
    v, notes, path = _vault()
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               when='2026-07-15 "x"')
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to == "interview", "abstain on the FIELD, never the advance"
    text = path.read_text()
    assert "interview_date" not in text, "no key at all beats an empty or corrupt one"


def test_a_safe_model_date_still_WINS_over_the_ics_date():
    # The model's `when` is the more specific signal when it is usable; the fallback must not
    # become an override.
    #
    # The two now name the same DAY at different times, which is what this test has always
    # been about -- which SOURCE wins, not what happens when they disagree about when the
    # interview is. Its fixture used to say 16 July against a 15 July DTSTART; since #202 a
    # day disagreement is arbitrated before this precedence rule is ever reached (neither
    # date is written, and nothing is booked), so that fixture would have been exercising
    # the conflict path under a name that promises precedence. The test below pins that
    # newer rule explicitly.
    v, notes, path = _vault()
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               when="2026-07-15T09:00:00", ics=ics)
    R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert 'interview_date: "2026-07-15T09:00:00"' in path.read_text()


def test_a_model_date_on_a_DIFFERENT_DAY_no_longer_wins_it_is_withheld():
    """The boundary between the precedence rule above and #202's arbitration.

    Precedence answers "which source is more specific". It cannot answer "which source is
    RIGHT", and on the invite #202 was filed for the structured header was the wrong one --
    so preferring either silently is a coin-flip that reproduces the failure half the time.
    A day disagreement therefore stops being a precedence question: nothing is booked, and
    neither date is written to the note.
    """
    v, notes, path = _vault()
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               when="2026-07-16T09:00:00", ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    text = path.read_text()
    assert res.needs_review == "calendar-date-conflict"
    assert "interview_date" not in text, "a disputed date must reach neither the calendar nor the note"
    assert "status: interview" in text, "the advance is still right -- only WHEN is unsettled"


# ---- confirm() is the third write site ----------------------------------------------------

def _dl():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "dl.db")))


def test_confirm_does_not_write_an_unsafe_when_into_frontmatter():
    """#141's commit said the class was closed. `engine.confirm` wrote the same field with
    the same quoting from `--when`, unguarded, one module over."""
    v, _notes, path = _vault("interview")
    out = E.confirm(v, TrackConfig(), "Example Tidal - EM", "offer",
                    deadletter=_dl(), when='Tuesday 3pm "BST"')
    assert out["ok"] is True, "the transition itself must still land"
    text = path.read_text()
    assert "status: offer" in text
    assert 'BST"' not in text, "frontmatter injection via confirm --when"
    assert out["when_dropped"] is True, "a silent drop is invisible to whoever typed it"


def test_confirm_still_writes_an_ordinary_when():
    v, _notes, path = _vault("interview")
    out = E.confirm(v, TrackConfig(), "Example Tidal - EM", "offer",
                    deadletter=_dl(), when="2026-07-15")
    assert out["when_dropped"] is False
    assert 'interview_date: "2026-07-15"' in path.read_text()


# The package-wide sweep that used to live here moved to
# `tests/test_frontmatter_write_sweep.py`. It called itself "the WHOLE track package"
# and that boundary was the bug: `sluice/triage/apply.py` was writing the model's own
# `culture_flags` into a quoted scalar unguarded, invisible to a track-only scan.


@pytest.mark.parametrize("bad", ['a"b', "a\\b"])
def test_frontmatter_safe_rejects_both_structural_characters(bad):
    """Pinned because two neighbouring comments described this guard differently: a `"` closes
    the quoted scalar early, a backslash opens a YAML escape sequence. Different mechanisms,
    same requirement."""
    from sluice.core.vault import frontmatter_safe

    assert frontmatter_safe(bad) is None


def test_confirm_TELLS_the_operator_their_when_was_dropped(monkeypatch, capsys, tmp_path):
    """The consumer, not just the flag.

    `engine.confirm` returns `when_dropped` and the test above asserts it -- at the engine
    boundary. Deleting `if out.get("when_dropped"):` from `cmd_track_confirm` was green, which
    is the state the guard's own comment calls out: "A guard whose result nothing consumes is
    the silent drop it was added to prevent."
    """
    out = {"ok": True, "from": "interview", "to": "offer", "when_dropped": True}
    monkeypatch.setattr("sluice.core.app.Sluice.track_confirm", lambda self, **k: out)

    class _A:
        def __getattr__(self, _n):
            return None
    args = _A()
    cli.cmd_track_confirm(args, Config())
    err = capsys.readouterr().err
    assert "interview_date dropped" in err, f"the operator was not told: {err!r}"


def test_confirm_stays_QUIET_when_the_when_was_written(monkeypatch, capsys):
    # The inverse: a line printed unconditionally would train the reader to ignore it.
    out = {"ok": True, "from": "interview", "to": "offer", "when_dropped": False}
    monkeypatch.setattr("sluice.core.app.Sluice.track_confirm", lambda self, **k: out)

    class _A:
        def __getattr__(self, _n):
            return None
    cli.cmd_track_confirm(_A(), Config())
    assert "dropped" not in capsys.readouterr().err
