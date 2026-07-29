"""Shared synthetic fixtures.

Role preferences are personal. The suite must not encode any real person's target
or anti-target titles, so it generates its own fictional lists with a fixed seed:
deterministic enough to assert on, and revealing nothing about whoever runs sluice.
"""
import pytest
from faker import Faker


@pytest.fixture(autouse=True)
def _pin_paths(tmp_path, monkeypatch):
    """Sandbox every per-system path the suite can resolve (#80).

    Before #80 the defaults were cwd-relative and the suite ran in `tmp_path`, so it
    could not reach a real home directory. Afterwards it can, and would pass while
    doing it -- which is why this is autouse rather than opt-in.

    All five names are load-bearing, and the two config ones do NOT substitute for each
    other: `$XDG_CONFIG_HOME` and `~` are consecutive rungs of one fallback chain, so
    which is consulted depends on the machine. macOS conventionally leaves `XDG_*`
    unset, so `HOME` is the pin that matters there. `VAULT_DIR` was never pinned at all
    -- a developer with it exported ran the whole suite against their real vault.

    `tests/harness/config.py` pins every per-path env var itself, so the e2e and
    functional tiers never reach these; that is why this cannot be the only guard, and
    why `tests/test_path_sandbox.py` asserts the property directly.
    """
    for var, sub in (("XDG_CONFIG_HOME", "config"),
                     ("XDG_STATE_HOME", "state"),
                     ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / "xdg" / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))


def _title_pool(n=60):
    fake = Faker("en_GB")
    Faker.seed(20260713)          # fixed: assertions must be reproducible
    seen, out = set(), []
    for _ in range(n):
        t = fake.job().lower()
        if "," in t or t in seen:  # keep single-phrase titles, no duplicates
            continue
        seen.add(t)
        out.append(t)
    return out


def _disjoint(pool, k=3):
    """Two lists with no substring overlap, so accept/reject cannot collide."""
    accept = pool[:k]
    reject = [t for t in pool[k:]
              if not any(a in t or t in a for a in accept)][:k]
    return accept, reject


@pytest.fixture(scope="session")
def titles():
    """(accept, reject): synthetic, disjoint, fictional job titles."""
    return _disjoint(_title_pool())


@pytest.fixture
def cfg_titles(titles):
    """A TriageConfig carrying the synthetic lists, plus a permissive geography."""
    from sluice.triage.config import TriageConfig
    accept, reject = titles
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cfg.reject_titles = list(reject)
    cfg.target_locations = ["testville"]
    return cfg


# Synthetic, token-disjoint placeholder locations. #5 makes `location` a note-name
# discriminator that tests assert into filenames, so these must be clearly FICTIONAL
# (never a real place, however a seeded Faker city might land) and pairwise token-disjoint,
# so any two read DIFFERENT under _compare_locations. NATO-phonetic placeholders, matching
# the Aye/Bee/Cee convention for synthetic companies. Module-level (importable) because the
# bare `_lead()` helpers in test_vault.py and the conformance suite cannot receive a fixture.
LOCATIONS = ("Alfa", "Bravo", "Charlie")


@pytest.fixture
def locations():
    """Three synthetic, token-disjoint placeholder locations (the LOCATIONS constant)."""
    return list(LOCATIONS)


def racing_read(monkeypatch, target_path, on_race, *, once=True):
    """Interpose sluice.core.vault._read to simulate a concurrent writer landing in the
    capture->commit window (#16), without threads. `on_race()` performs one out-of-band
    edit to the file. It fires after the FIRST read of target_path (once=True) or on
    EVERY read (once=False, for exhaustion -- on_race must then change the content each
    call). The read returns the PRE-edit bytes, so it is robust to a mutant that deletes
    _cas_write's second (compare) read. Returns the fired-state dict."""
    import sluice.core.vault as vaultmod
    real_read = vaultmod._read
    state = {"fired": False}
    def racer(path):
        text = real_read(path)
        if str(path) == str(target_path) and (not once or not state["fired"]):
            state["fired"] = True
            on_race()
        return text
    monkeypatch.setattr(vaultmod, "_read", racer)
    return state


class DnsUsedInTests(BaseException):
    """Raised when a test tries to resolve a hostname.

    Subclasses BaseException, NOT Exception, and that is load-bearing. A plain
    Exception would be swallowed on the dossier path by whichever consumer calls
    it -- cv/engine.py's per-item `except Exception` (which proceeds with an
    empty JD) or triage/engine.py's per-item `except Exception` (which records
    report.failures and skips the lead). An implementer who forgot to inject
    `resolve_host=` at one of the three test wiring sites would therefore see a
    GREEN suite that was doing real DNS on every run. That happened in review,
    which is why this exists at all.
    """


@pytest.fixture(scope="session", autouse=True)
def _forbid_dns():
    """Make socket.getaddrinfo raise for the whole session.

    Verified before writing: the suite performs zero DNS today, so this changes
    nothing that currently passes. monkeypatch is function-scoped, so the
    set/restore is done by hand.
    """
    import socket
    real = socket.getaddrinfo

    def _raise(*args, **kwargs):
        raise DnsUsedInTests(
            "tests must not resolve DNS -- inject resolve_host= instead")

    socket.getaddrinfo = _raise
    try:
        yield
    finally:
        socket.getaddrinfo = real
