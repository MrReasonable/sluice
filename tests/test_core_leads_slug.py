from types import SimpleNamespace
from sluice.core.leads import slug_matches


def _note(company, role, path="/v/Job Leads/Whatever.md"):
    return SimpleNamespace(fm={"company": company, "role": role}, path=path)


def test_slug_matches_on_company_role():
    n = _note("Northwind", "Chief Financial Officer")
    assert slug_matches(n, "northwind") is True          # company
    assert slug_matches(n, "chief-financial-officer") is True   # role, slugified
    assert slug_matches(n, "cobalt") is False


def test_slug_matches_on_path():
    n = _note("", "", path="/v/Job Leads/flowline - Banker.md")
    assert slug_matches(n, "flowline") is True
