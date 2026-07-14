from types import SimpleNamespace
from sluice.core.leads import slug_matches


def _note(company, role, slug="Whatever"):
    # A store hands back the slug it issued. slug_matches used to fall back to
    # substring-matching the note PATH, which is one of the places the store seam
    # leaked a filesystem into callers that had no business knowing about one.
    # `ref` is OPAQUE and carries no slug -- an integer, as a row-id store would issue.
    # It used to be f"/v/Job Leads/{slug}.md", which meant a regression back to matching
    # note.ref still passed this test. It could not detect the very thing it guards.
    return SimpleNamespace(fm={"company": company, "role": role}, ref=4711, slug=slug)


def test_slug_matches_on_company_role():
    n = _note("Northwind", "Chief Financial Officer")
    assert slug_matches(n, "northwind") is True          # company
    assert slug_matches(n, "chief-financial-officer") is True   # role, slugified
    assert slug_matches(n, "cobalt") is False


def test_slug_matches_on_store_issued_slug():
    # Frontmatter is empty, so the only thing left to match on is the store's slug.
    n = _note("", "", slug="flowline - Banker")
    assert slug_matches(n, "flowline") is True
