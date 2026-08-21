"""De-mash company+location for boards (Indeed) that render both in one DOM node
with no separator, so the extractor captures 'Example FoundryPalmerburgh' with location 'Palmerburgh'."""
from sluice.ingest.base import Search, _demash_company, _row_to_lead


def test_strips_jammed_location_suffix():
    assert _demash_company("Example Systems ABMPalmerburgh", "Palmerburgh") == "Example Systems ABM"
    assert _demash_company("Example FoundryPalmerburgh", "Palmerburgh") == "Example Foundry"
    assert _demash_company("Example GroupHybrid work in Palmerburgh", "Hybrid work in Palmerburgh") == "Example Group"
    assert _demash_company("Example Analytics EdgePotterburgh ZZ9Z", "Potterburgh ZZ9Z") == "Example Analytics Edge"
    assert (_demash_company("Example Telemetry Asset Management ServicesClarkefurt", "Clarkefurt")
            == "Example Telemetry Asset Management Services")
    assert _demash_company("Example VenturesHybrid work in Palmerburgh", "Hybrid work in Palmerburgh") == "Example Ventures"


def test_preserves_legitimate_trailing_token_with_space():
    # A real separating space means it is NOT a mashing artifact.
    assert _demash_company("Example Capital ABM", "ABM") == "Example Capital ABM"
    assert _demash_company("Example Palmerburgh Systems", "Palmerburgh") == "Example Palmerburgh Systems"


def test_noops_when_no_location_or_no_match():
    assert _demash_company("EXAMPLE FOUNDRY NATURAL RESOURCES", "Ellery Kestrelburgh") == "EXAMPLE FOUNDRY NATURAL RESOURCES"
    assert _demash_company("Example Systems", "") == "Example Systems"
    assert _demash_company("", "Palmerburgh") == ""


def test_never_empties_company():
    assert _demash_company("Palmerburgh", "Palmerburgh") == "Palmerburgh"  # equal -> no strip


def test_row_to_lead_applies_demash():
    lead = _row_to_lead("indeed", Search(label="x"),
                        {"title": "Analyst", "company": "Example FoundryPalmerburgh",
                         "location": "Palmerburgh", "link": "u"}, None)
    assert lead.company == "Example Foundry"
    assert lead.location == "Palmerburgh"
