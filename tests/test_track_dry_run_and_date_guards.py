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
    v, notes, path = _vault()
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               when="2026-07-16T09:00:00", ics=ics)
    R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert 'interview_date: "2026-07-16T09:00:00"' in path.read_text()


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


def test_the_frontmatter_write_sweep_covers_the_WHOLE_track_package():
    """ENUMERATED across the package, not one function.

    The previous version called itself "enumerated, not hand-listed" and then scoped itself to
    `inspect.getsource(R._advance)` -- so the recurrence it was written to prevent was already
    sitting in `engine.confirm`, structurally invisible to it. A sweep whose boundary is one
    function is a hand-list with extra steps.
    """
    import re

    pkg = pathlib.Path(E.__file__).parent
    offenders = []
    for py in sorted(pkg.glob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            # A quoted frontmatter scalar built by interpolation.
            if not re.search(r'fields\[[^\]]+\]\s*=\s*f[\'"]"', line):
                continue
            # `.isoformat()` on a parsed datetime structurally cannot carry a quote or a
            # backslash. Exempting the TYPE rather than the field name means a later change
            # routing a raw string through the same field is still caught.
            if ".isoformat()" in line:
                continue
            if "safe" not in line:
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, (
        "an untrusted value is interpolated into frontmatter unguarded:\n  "
        + "\n  ".join(offenders))


def test_the_sweep_actually_finds_the_writes_it_is_checking():
    """Guards the sweep. A regex that matches nothing passes silently and forever."""
    import re

    pkg = pathlib.Path(E.__file__).parent
    hits = [1 for py in pkg.glob("*.py")
            for line in py.read_text(encoding="utf-8").splitlines()
            if re.search(r'fields\[[^\]]+\]\s*=\s*f[\'"]"', line)]
    assert len(hits) >= 3, f"the sweep found {len(hits)} frontmatter writes -- it has drifted"


@pytest.mark.parametrize("bad", ['a"b', "a\\b"])
def test_frontmatter_safe_rejects_both_structural_characters(bad):
    """Pinned because two neighbouring comments described this guard differently: a `"` closes
    the quoted scalar early, a backslash opens a YAML escape sequence. Different mechanisms,
    same requirement."""
    from sluice.core.vault import frontmatter_safe

    assert frontmatter_safe(bad) is None
