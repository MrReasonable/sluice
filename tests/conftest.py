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


def _location_pool(n=12):
    """Token-disjoint synthetic cities. #5 makes `location` a note-name discriminator,
    so tests assert it into filenames — these must be fictional (not a real place) and
    pairwise token-disjoint, so any two read DIFFERENT under _compare_locations."""
    fake = Faker("en_GB")
    Faker.seed(20260719)          # fixed: assertions must be reproducible
    out, used = [], set()
    for _ in range(400):
        c = fake.city()
        toks = frozenset(c.lower().replace(",", " ").split())
        if toks and not (toks & used):
            out.append(c)
            used |= toks
        if len(out) >= n:
            break
    return out


# Module-level (importable) so the bare module-scope `_lead()` helpers in test_vault.py
# and the conformance suite can source a synthetic location — a pytest fixture cannot
# reach a plain function. Three token-disjoint cities: any pair reads DIFFERENT.
LOCATIONS = tuple(_location_pool()[:3])


@pytest.fixture
def locations():
    """Three synthetic, token-disjoint, fictional cities (the LOCATIONS constant)."""
    return list(LOCATIONS)
