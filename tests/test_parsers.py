"""Source-plugin tests.

Two layers:
  * test_all_expected_sources_register / test_source_is_well_formed - run offline,
    now: every plugin imports, self-registers, and its parser tolerates an empty
    payload. This is the guard while golden fixtures are still being captured.
  * test_parser_yields_valid_leads - parametrized over whatever golden fixtures
    exist under tests/fixtures/<id>/raw.json. Empty today (fixtures are captured
    from a live Camofox run, once per source), so it simply lights up per source
    as each fixture lands.
"""
import json
from pathlib import Path

import pytest

from sluice.ingest import sources

FIX = Path(__file__).parent / "fixtures"
_FIXTURE_IDS = sorted(p.parent.name for p in FIX.glob("*/raw.json")) if FIX.exists() else []

# Every plugin we expect registered. Technojobs & Jobsearch are intentionally NOT
# ported (dead domains as of 2026-07-07).
EXPECTED_IDS = {
    "cord", "wttj", "jobserve", "workinstartups", "linkedin", "indeed", "reed",
    "cwjobs", "totaljobs", "google", "bayt", "gulftalent", "hackajob",
    "naukrigulf", "weworkremotely", "remoteok", "hired", "theorg", "eighty_k",
    "bwork", "escape_city", "wellfound",
}


def test_all_expected_sources_register():
    got = {s.id for s in sources.all_sources()}
    missing = EXPECTED_IDS - got
    assert not missing, f"sources failed to register: {sorted(missing)}"


@pytest.mark.parametrize("sid", sorted(EXPECTED_IDS))
def test_source_is_well_formed(sid):
    src = sources.get(sid)
    assert src.id == sid
    assert src.searches(), f"{sid} has no searches"
    # parse is pure and must tolerate an empty payload (both raw shapes).
    assert src.parse({"result": [], "jobs": []}, src.searches()[0]) == []


@pytest.mark.parametrize("sid", _FIXTURE_IDS)
def test_parser_yields_valid_leads(sid):
    raw = json.loads((FIX / sid / "raw.json").read_text())
    src = sources.get(sid)
    leads = src.parse(raw, src.searches()[0])
    # google leads legitimately have no url (dedup by title+company); require url
    # for everyone else.
    assert leads, f"{sid} parsed no leads from its fixture"
    assert all(l.title for l in leads)
    if sid != "google":
        assert all(l.url for l in leads)
