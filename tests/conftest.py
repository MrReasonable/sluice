"""Shared synthetic fixtures.

Role preferences are personal. The suite must not encode any real person's target
or anti-target titles, so it generates its own fictional lists with a fixed seed:
deterministic enough to assert on, and revealing nothing about whoever runs sluice.
"""
import pytest
from faker import Faker


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
