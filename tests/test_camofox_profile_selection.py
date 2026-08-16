"""Which Camofox profile a run uses, and saying so when the config cannot get what it asked for.

2026-08-15 incident. The production runner set:

    export CAMOFOX_SESSION=example-session

intending to select the `example-session` browser profile, which existed and held 335
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
    monkeypatch.setenv("CAMOFOX_USER", "example-user")
    assert Camofox().user == "example-user"


def test_session_without_user_warns_because_it_selects_nothing(monkeypatch, caplog):
    # The exact production config. It must not pass silently: the operator believes they have
    # chosen a profile, and they have not.
    monkeypatch.setenv("CAMOFOX_SESSION", "example-session")
    with caplog.at_level("WARNING", logger="sluice.core.camofox"):
        c = Camofox()
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.camofox"]
    assert said, "setting CAMOFOX_SESSION alone must warn"
    joined = " ".join(said)
    assert "CAMOFOX_SESSION" in joined and "CAMOFOX_USER" in joined, joined
    assert "example-session" in joined, "the warning must name the value that did nothing"
    # ...and the client still works, on the profile it actually uses.
    assert c.user == "default"
    assert c.session == "example-session"


_CONTROL = "control-probe"


def _warnings_with_control(caplog, body):
    """Run `body()`; return the camofox warnings it emitted, with capture PROVEN live.

    A bare `assert caplog.records == []` establishes nothing here. `sluice.core.log.get_logger`
    sets `propagate=False`, so a capture path that never attaches produces an empty record list
    for the same reason real silence does -- the assertion passes hardest in exactly the case it
    is meant to exclude.

    A control record separates them, and ASSERTING the control is the half that gets forgotten:
    an earlier draft of the test below emitted one, filtered it out, and checked only the
    remainder, which is no better than not having it. So the assert lives in the helper, and a
    silence test cannot be written here without it.
    """
    from sluice.core.camofox import _log
    with caplog.at_level("WARNING", logger="sluice.core.camofox"):
        body()
        _log.warning(_CONTROL)
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.camofox"]
    assert _CONTROL in said, "caplog never reached the camofox logger -- the silence proves nothing"
    return [m for m in said if m != _CONTROL]


def test_session_WITH_user_does_not_warn(monkeypatch, caplog):
    # Setting both is coherent: the user picked a profile and also named a session. Warning
    # here would train the reader to ignore the line.
    monkeypatch.setenv("CAMOFOX_USER", "example-user")
    monkeypatch.setenv("CAMOFOX_SESSION", "example-session")
    assert _warnings_with_control(caplog, Camofox) == []


def test_neither_set_does_not_warn(monkeypatch, caplog):
    assert _warnings_with_control(caplog, Camofox) == []


def test_an_explicit_user_argument_still_wins_when_no_env(monkeypatch):
    assert Camofox(user="explicit").user == "explicit"


_CLAIM_PHRASES = (
    "which named browser profile",
    "point at their own authenticated session",
    "named authenticated browser profile",
)


def test_the_module_docstring_does_not_claim_session_selects_the_profile():
    """The proximate cause was documentation, so the documentation is part of the fix.

    Asserted structurally rather than by exact wording: the module must not pair
    CAMOFOX_SESSION with profile-selection language, because that pairing is what a reader
    acts on.
    """
    import sluice.core.camofox as mod

    doc = (mod.__doc__ or "").lower()
    assert "camofox_session" in doc, "the knob should still be documented"
    for phrase in _CLAIM_PHRASES:
        assert phrase not in doc, (
            "module docstring still claims CAMOFOX_SESSION selects the profile: {!r}".format(phrase))
    assert "camofox_user" in doc, "the docstring must name the knob that DOES select the profile"


def test_NO_shipped_doc_claims_session_selects_the_profile():
    """The same claim, swept across every shipped doc and module — not just this one file.

    The first cut of this guard read only `sluice.core.camofox.__doc__`, and review found the
    identical false sentence still sitting in `docs/CONFIGURATION.md` ("Camofox's named
    authenticated browser profile"). Fixing one instance of a wrong claim while an equally
    load-bearing copy survives is how the original mistake gets made twice: a reader who
    follows the config table lands in exactly the same place.

    So the guard sweeps the class, not the instance.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    targets = sorted(root.glob("docs/*.md")) + sorted((root / "sluice").rglob("*.py")) + [
        root / "README.md", root / "sluice.yaml.example"]
    checked, offenders = 0, []
    for p in targets:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        if "camofox_session" not in text:
            continue
        checked += 1
        for phrase in _CLAIM_PHRASES:
            if phrase in text:
                offenders.append(f"{p.relative_to(root)}: {phrase!r}")
    assert checked >= 2, (
        f"only {checked} shipped file(s) mention CAMOFOX_SESSION — the sweep has probably "
        f"stopped finding them, which would make this guard vacuous")
    assert not offenders, (
        "these shipped files still tell the reader CAMOFOX_SESSION selects the profile:\n  "
        + "\n  ".join(offenders))
