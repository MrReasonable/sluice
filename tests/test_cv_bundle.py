# tests/test_cv_bundle.py
import re

from sluice.cv import bundle as B

PREFIX = {"Novacraft": "NC", "Solarflux": "SF", "Trueverse": "TV"}
ENTRIES = [
    {"title": "Grew team", "company": "Solarflux", "best_for": "leadership",
     "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
    {"title": "Shipped MVP", "company": "Novacraft", "best_for": "delivery",
     "category": "delivery", "metrics": "3 months", "body": "Concept to live."},
    {"title": "Was CTO", "company": "Trueverse", "best_for": "leadership",
     "category": "leadership", "metrics": "15", "body": "Led 15 people."},
]

def test_codes_are_short_company_prefixed_and_sequenced():
    coded = B.assign_codes(ENTRIES, PREFIX)
    by_co = {e["company"]: e["id"] for e in coded}
    assert by_co["Solarflux"] == "SF1"
    assert by_co["Novacraft"] == "NC1"
    assert by_co["Trueverse"] == "TV1"

def test_full_set_included_ranking_orders_not_excludes():
    b = B.build_bundle(ENTRIES, "BASELINE", ["Larkspur"], ["leadership"], PREFIX)
    # all 3 entries present even though only 2 match the keyword
    assert len(b["entries"]) == 3
    # leadership-matching entries rank first
    assert b["entries"][0]["best_for"] == "leadership"

def test_render_bundle_has_codes_and_negatives_and_bodies():
    b = B.build_bundle(ENTRIES, "BASELINE CV TEXT", ["No Larkspur Health."], ["leadership"], PREFIX)
    text = B.render_bundle(b)
    assert "BASELINE CV TEXT" in text
    assert "[SF1] (Solarflux) Grew team | metrics=3 8" in text
    assert "Grew from 3 to 8." in text            # body included for the number gate
    assert "No Larkspur Health." in text           # negatives block

def test_unknown_company_gets_two_letter_fallback():
    coded = B.assign_codes([{"title": "x", "company": "Acme Corp", "metrics": "", "body": ""}], {})
    assert coded[0]["id"] == "AC1"

def test_same_company_entries_are_sequenced_not_hardcoded_to_one():
    two_solarflux = [
        {"title": "Grew team", "company": "Solarflux", "best_for": "leadership",
         "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
        {"title": "Cut costs", "company": "Solarflux", "best_for": "delivery",
         "category": "delivery", "metrics": "20%", "body": "Cut costs by 20%."},
    ]
    coded = B.assign_codes(two_solarflux, PREFIX)
    assert [e["id"] for e in coded] == ["SF1", "SF2"]

def test_single_alpha_unmapped_company_still_yields_two_letter_code():
    coded = B.assign_codes([{"title": "x", "company": "3M", "metrics": "", "body": ""}], {})
    assert re.match(r"^[A-Z]{2}[0-9]+$", coded[0]["id"])

def test_prefix_map_override_is_coerced_to_two_letters():
    # a 1-char and a 3-char override must both become exactly-2-letter codes,
    # so a malformed prefix_map (e.g. from sluice.yaml) can never produce a
    # citation code that escapes the render-step strip regex.
    coded = B.assign_codes(
        [{"title": "x", "company": "Foo", "metrics": "", "body": ""},
         {"title": "y", "company": "Bar", "metrics": "", "body": ""}],
        {"Foo": "X", "Bar": "ABC"})
    ids = [e["id"] for e in coded]
    assert all(re.match(r"^[A-Z]{2}[0-9]+$", i) for i in ids), ids
