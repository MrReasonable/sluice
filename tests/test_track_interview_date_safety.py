"""#141: `interview_date` writes an LLM-supplied value into frontmatter unsanitised.

`_advance` writes `interview_date: "<ev.when>"` where `ev.when` comes straight from the model
(`classify.py`: `data.get("when")`). Three lines below, `ev.links[0]` -- untrusted in exactly
the same way, and for exactly the same reason -- IS run through `frontmatter_safe`. That
asymmetry inside one function is the tell.

A `"` or a backslash in the value closes the double-quoted scalar early and corrupts the
note's frontmatter. This is the #111 class, one field over: same file, same function, fixed
for one field and not its neighbour.

`frontmatter_safe` ABSTAINS rather than mangling, and abstaining on one field is the
established behaviour here -- the status advance itself must still land, because losing the
interview signal to a bad date string would be a worse failure than a missing date.
"""
import pathlib
import tempfile

from sluice.core.vault import Vault
from sluice.track import reconcile as R
from sluice.track.classify import Event
from sluice.track.config import TrackConfig
from tests.test_track_google_client import FakeGoogleClient


def _vault(status="applied"):
    root = pathlib.Path(tempfile.mkdtemp())
    leads = root / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    p = leads / "Example Tidal - EM.md"
    p.write_text(f'---\ncompany: "Example Tidal"\nrole: "EM"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(str(root))
    notes = {n.slug: n for n in v.read_leads() if n.slug == "Example Tidal - EM"}
    return v, notes, p


def _advance_with(when):
    v, notes, path = _vault()
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, when=when)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    return res, path.read_text()


def test_a_quote_in_the_llm_date_does_not_corrupt_the_frontmatter():
    res, text = _advance_with('2026-07-15" \nstatus: rejected\nx: "')
    assert res.status_to == "interview", "the advance itself must still land"
    # The injected key must not have become a real field, and the status must be the one
    # sluice decided on -- not the one smuggled through the date.
    assert "status: interview" in text
    assert "status: rejected" not in text, "frontmatter injection via interview_date"
    assert "\nx:" not in text


def test_a_backslash_in_the_llm_date_is_abstained_on():
    res, text = _advance_with("2026-07-15\\")
    assert res.status_to == "interview"
    assert "2026-07-15\\" not in text


def test_an_ordinary_date_is_still_written():
    # Abstaining must be narrow: the common case has to keep working.
    _res, text = _advance_with("2026-07-15T10:00:00")
    assert 'interview_date: "2026-07-15T10:00:00"' in text


def test_the_ics_derived_date_needs_no_sanitising_but_is_still_written():
    """The `elif` branch builds the value from a parsed `datetime`, so it cannot carry a
    structural character. Pinned so a later refactor cannot quietly route an untrusted string
    through it."""
    from datetime import datetime, timezone

    from sluice.track.ics import IcsEvent

    v, notes, path = _vault()
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics)
    R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert 'interview_date: "2026-07-15"' in path.read_text()


def test_every_llm_sourced_frontmatter_write_in_advance_is_guarded():
    """ENUMERATED, not hand-listed.

    #111 fixed `interview_link` and left `interview_date`; the way to stop that recurring is
    to assert over the writes rather than remember them. Any future `fields[...] = f'"{...}"'`
    fed from `ev.` must go through `frontmatter_safe`.
    """
    import inspect
    import re

    src = inspect.getsource(R._advance)
    # Lines that write a QUOTED frontmatter scalar interpolating an `ev.`-sourced value.
    writes = [ln.strip() for ln in src.splitlines()
              if re.search(r'fields\[[^\]]+\]\s*=\s*f?[\'"]"', ln)]
    assert writes, "the scan found no frontmatter writes -- it has drifted from the code"
    for ln in writes:
        if "ev." not in ln:
            continue
        # `.isoformat()` on a parsed datetime is exempt: it can only ever produce
        # `YYYY-MM-DD[THH:MM:SS...]`, so it structurally cannot carry a quote or backslash.
        # Exempting the TYPE rather than the field name means a future write that routes a
        # raw string through the same field is still caught.
        if ".isoformat()" in ln:
            continue
        assert "safe" in ln, (
            f"an LLM-sourced value is written to frontmatter unguarded: {ln}")
