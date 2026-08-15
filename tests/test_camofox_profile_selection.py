"""Which Camofox profile a run uses, and saying so when the config cannot get what it asked for.

2026-08-15 incident. The production runner set:

    export CAMOFOX_SESSION=contract-scanner

intending to select the `contract-scanner` browser profile, which existed and held 335
cookies. The Camofox persistence plugin keys profiles on `sha256(userId)` and IGNORES
sessionKey entirely, so the setting was inert: the run used the `default` profile, which had
no LinkedIn `li_at`. LinkedIn returned zero rows for eight-plus runs and auto-retired, taking
jobserve and indeed with it for the same reason.

The mistake was invited by this module's own docstring, which claimed CAMOFOX_SESSION is
"which named browser profile to drive" and lets "an operator point at their own authenticated
session". Someone followed that exactly. The config looked deliberate and did nothing.

So: the docstring must not claim it, and setting CAMOFOX_SESSION without CAMOFOX_USER must be
LOUD -- that is the only configuration shape which is always a mistake.
"""
import pytest

from sluice.core.camofox import Camofox


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("CAMOFOX_URL", "CAMOFOX_USER", "CAMOFOX_SESSION"):
        monkeypatch.delenv(k, raising=False)


def test_camofox_user_selects_the_profile(monkeypatch):
    monkeypatch.setenv("CAMOFOX_USER", "ian")
    assert Camofox().user == "ian"


def test_session_without_user_warns_because_it_selects_nothing(monkeypatch, caplog):
    # The exact production config. It must not pass silently: the operator believes they have
    # chosen a profile, and they have not.
    monkeypatch.setenv("CAMOFOX_SESSION", "contract-scanner")
    with caplog.at_level("WARNING", logger="sluice.core.camofox"):
        c = Camofox()
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.camofox"]
    assert said, "setting CAMOFOX_SESSION alone must warn"
    joined = " ".join(said)
    assert "CAMOFOX_SESSION" in joined and "CAMOFOX_USER" in joined, joined
    assert "contract-scanner" in joined, "the warning must name the value that did nothing"
    # ...and the client still works, on the profile it actually uses.
    assert c.user == "default"
    assert c.session == "contract-scanner"


def test_session_WITH_user_does_not_warn(monkeypatch, caplog):
    # Setting both is coherent: the user picked a profile and also named a session. Warning
    # here would train the reader to ignore the line.
    monkeypatch.setenv("CAMOFOX_USER", "ian")
    monkeypatch.setenv("CAMOFOX_SESSION", "contract-scanner")
    with caplog.at_level("WARNING", logger="sluice.core.camofox"):
        Camofox()
        # Positive control: the logger and capture really are wired up, so the empty
        # assertion below cannot pass because nothing was listening.
        from sluice.core.camofox import _log
        _log.warning("probe")
    said = [r.getMessage() for r in caplog.records
            if r.name == "sluice.core.camofox" and r.getMessage() != "probe"]
    assert said == [], said


def test_neither_set_does_not_warn(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="sluice.core.camofox"):
        Camofox()
    assert [r for r in caplog.records if r.name == "sluice.core.camofox"] == []


def test_an_explicit_user_argument_still_wins_when_no_env(monkeypatch):
    assert Camofox(user="explicit").user == "explicit"


def test_the_docstring_does_not_claim_session_selects_the_profile():
    """The proximate cause was documentation, so the documentation is part of the fix.

    Asserted structurally rather than by exact wording: the module must not pair
    CAMOFOX_SESSION with profile-selection language, because that pairing is what a reader
    acts on.
    """
    import sluice.core.camofox as mod

    doc = (mod.__doc__ or "").lower()
    assert "camofox_session" in doc, "the knob should still be documented"
    claim_words = ("which named browser profile", "point at their own authenticated session")
    for phrase in claim_words:
        assert phrase not in doc, (
            "module docstring still claims CAMOFOX_SESSION selects the profile: {!r}".format(phrase))
    assert "camofox_user" in doc, "the docstring must name the knob that DOES select the profile"
