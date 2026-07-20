# tests/test_cv_bundle.py
import re

from sluice.cv import bundle as B

# An explicit map is REQUIRED for any fixture using more than one `Example ...`
# company: `_prefix` derives its code from the first two letters, so every one of
# them derives to "EX" and they would share a sequence (EX1/EX2/EX3) rather than
# getting one each. That fails loudly here, but a new fixture that omits the map
# would be asserting against codes it did not intend.
PREFIX = {"Example Systems": "ES", "Example Foundry": "EF", "Example Telemetry": "ET"}
ENTRIES = [
    {"title": "Grew team", "company": "Example Foundry", "best_for": "leadership",
     "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
    {"title": "Shipped MVP", "company": "Example Systems", "best_for": "delivery",
     "category": "delivery", "metrics": "3 months", "body": "Concept to live."},
    {"title": "Was CTO", "company": "Example Telemetry", "best_for": "leadership",
     "category": "leadership", "metrics": "15", "body": "Led 15 people."},
]

def test_codes_are_short_company_prefixed_and_sequenced():
    coded = B.assign_codes(ENTRIES, PREFIX)
    by_co = {e["company"]: e["id"] for e in coded}
    assert by_co["Example Foundry"] == "EF1"
    assert by_co["Example Systems"] == "ES1"
    assert by_co["Example Telemetry"] == "ET1"

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
    assert "[EF1] (Example Foundry) Grew team | metrics=3 8" in text
    assert "Grew from 3 to 8." in text            # body included for the number gate
    assert "No Larkspur Health." in text           # negatives block

def test_unknown_company_gets_two_letter_fallback():
    coded = B.assign_codes([{"title": "x", "company": "Acme Corp", "metrics": "", "body": ""}], {})
    assert coded[0]["id"] == "AC1"

def test_same_company_entries_are_sequenced_not_hardcoded_to_one():
    two_at_one_company = [
        {"title": "Grew team", "company": "Example Foundry", "best_for": "leadership",
         "category": "people", "metrics": "3 8", "body": "Grew from 3 to 8."},
        {"title": "Cut costs", "company": "Example Foundry", "best_for": "delivery",
         "category": "delivery", "metrics": "20%", "body": "Cut costs by 20%."},
    ]
    coded = B.assign_codes(two_at_one_company, PREFIX)
    assert [e["id"] for e in coded] == ["EF1", "EF2"]

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
