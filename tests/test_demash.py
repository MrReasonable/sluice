"""De-mash company+location for boards (Indeed) that render both in one DOM node
with no separator, so the extractor captures 'GlobexPalmerburgh' with location 'Palmerburgh'."""
from sluice.ingest.base import Search, _demash_company, _row_to_lead


def test_strips_jammed_location_suffix():
    assert _demash_company("Acme UKPalmerburgh", "Palmerburgh") == "Acme UK"
    assert _demash_company("GlobexPalmerburgh", "Palmerburgh") == "Globex"
    assert _demash_company("GTSHybrid work in Palmerburgh", "Hybrid work in Palmerburgh") == "GTS"
    assert _demash_company("Hooli EdgePotterburgh EC4Y", "Potterburgh EC4Y") == "Hooli Edge"
    assert (_demash_company("Umbrella Asset Management ServicesClarkefurt", "Clarkefurt")
            == "Umbrella Asset Management Services")
    assert _demash_company("VanteraHybrid work in Palmerburgh", "Hybrid work in Palmerburgh") == "Vantera"


def test_preserves_legitimate_trailing_token_with_space():
    # A real separating space means it is NOT a mashing artifact.
    assert _demash_company("Stark Capital UK", "UK") == "Stark Capital UK"
    assert _demash_company("Palmerburgh Gate Systems", "Palmerburgh") == "Palmerburgh Gate Systems"


def test_noops_when_no_location_or_no_match():
    assert _demash_company("GLOBEX NATURAL RESOURCES", "Abu Dhabi") == "GLOBEX NATURAL RESOURCES"
    assert _demash_company("Acme", "") == "Acme"
    assert _demash_company("", "Palmerburgh") == ""


def test_never_empties_company():
    assert _demash_company("Palmerburgh", "Palmerburgh") == "Palmerburgh"  # equal -> no strip


def test_row_to_lead_applies_demash():
    lead = _row_to_lead("indeed", Search(label="x"),
                        {"title": "Analyst", "company": "GlobexPalmerburgh",
                         "location": "Palmerburgh", "link": "u"}, None)
    assert lead.company == "Globex"
    assert lead.location == "Palmerburgh"
