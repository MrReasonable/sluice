"""`parse_cv` -- pure, no I/O, no fact validation.

All the risk in the template-renderer design lives in this function, which is why it is
pure: every case below is table-driven with no fixtures, no subprocess and no PDF.

Fixtures are synthetic and use the `Example ...`/`example.invalid` family. EXAMPLECITY
rather than a real place name: `tests/` is bound by the no-personal-data rule.
"""
import pytest

from sluice.cv.parse import CvDocument, CvParseError, Role, parse_cv

CV = """\
Phone number: +44 20 7946 0000
Email: someone@example.invalid

EXAMPLE PERSON

PROFILE
Engineer with nine years building data pipelines and the teams that run them.

WORK EXPERIENCE

Example Data Co
03/2021-present | EXAMPLECITY | Staff Engineer
- Cut p99 latency to 120ms [ED1]
- Grew the team from 3 to 8 [ED2]

Example Analytics Ltd
01/2018-02/2021 | EXAMPLECITY | Senior Engineer
- Shipped 4 services [EA1]

CERTIFICATES
- Example Cloud Practitioner, 2022

EDUCATION
- Example University, 2010-2013 | BSc Computer Science
"""


def test_parse_reads_every_section():
    doc = parse_cv(CV)
    assert doc.name == "EXAMPLE PERSON"
    assert "someone@example.invalid" in doc.contact
    assert doc.profile.startswith("Engineer with nine years")
    assert len(doc.work) == 2
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


def test_parse_reads_multiple_roles_and_their_bullets():
    doc = parse_cv(CV)
    first, second = doc.work
    assert first.company == "Example Data Co"
    assert first.dates == "03/2021-present"
    assert first.location == "EXAMPLECITY"
    assert first.title == "Staff Engineer"
    assert first.bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]
    assert second.company == "Example Analytics Ltd"
    assert second.title == "Senior Engineer"
    assert second.bullets == ["Shipped 4 services"]


def test_parse_raises_on_an_unparseable_meta_line():
    """A meta line missing its pipes must RAISE, not be absorbed into a neighbour.

    Refusing is the whole argument of spec section 0: the CV has passed the fabrication
    gate, so its facts are sound and only its shape is in doubt -- but the artefact goes
    to an employer under the user's name, and a date landing where a title belongs is
    wrong in a way the user does not see until after sending.
    """
    broken = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer",
                        "03/2021-present Staff Engineer")
    assert "03/2021-present Staff Engineer" in broken, "the replace no-opped"
    with pytest.raises(CvParseError, match="meta line"):
        parse_cv(broken)


def test_parse_strips_citations_from_bullets():
    """The [id] tokens are an INTERNAL artefact of the fabrication gate and must never
    reach an employer. Stripping happens INSIDE parse_cv, so no renderer has to remember
    to do it -- the obligation was previously duplicated per-renderer."""
    doc = parse_cv(CV)
    every_bullet = [b for role in doc.work for b in role.bullets]
    assert every_bullet, "no bullets parsed, so this assertion proves nothing"
    for bullet in every_bullet:
        assert "[" not in bullet and "]" not in bullet


def test_parse_preserves_line_structure_while_stripping():
    """Strip PER FIELD, after the line structure has been read -- never over whole text.

    `_CITE_RE`'s leading `\\s*` matches newlines. Measured 2026-08-06: for a CV whose
    citation sits alone on its own line, stripping the whole text first DELETES that
    line (6 lines vs 7), so the parser would be reading a different document from the
    one the composer emitted. Here the stand-alone citation must not become a third,
    empty bullet, and must not swallow the bullet after it.
    """
    wrapped = CV.replace("- Cut p99 latency to 120ms [ED1]",
                         "- Cut p99 latency to 120ms\n[ED1]")
    assert "120ms\n[ED1]" in wrapped, "the replace no-opped"
    doc = parse_cv(wrapped)
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms",
                                   "Grew the team from 3 to 8"]


def test_parse_refuses_a_section_it_does_not_model():
    """User content must not vanish silently from a PDF sent under their name."""
    extra = CV.replace("CERTIFICATES", "PUBLICATIONS\n- Example paper, 2021\n\nCERTIFICATES")
    assert "PUBLICATIONS" in extra, "the replace no-opped"
    with pytest.raises(CvParseError, match="PUBLICATIONS"):
        parse_cv(extra)


@pytest.mark.parametrize("mutation,replacement,field,expected", [
    # A date must not be absorbed into the title.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-06/2024 | EXAMPLECITY | Staff Engineer", "dates", "03/2021-06/2024"),
    # A title containing a hyphen must survive intact.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-present | EXAMPLECITY | Staff Engineer - Platform", "title",
     "Staff Engineer - Platform"),
    # A multi-word location must not be split.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-present | EXAMPLECITY REGION | Staff Engineer", "location",
     "EXAMPLECITY REGION"),
])
def test_parse_does_not_silently_misassign_fields(mutation, replacement, field, expected):
    """Parsing WRONGLY is the harm the refusal argument rests on, and the first draft of
    the spec specified no case for it. A date absorbed into `title`, a bullet swallowed
    as a company, a meta line read as a heading: each raises nothing and produces a wrong
    PDF. Assert the whole field, not merely that it parsed."""
    text = CV.replace(mutation, replacement)
    assert replacement in text, "the replace no-opped"
    doc = parse_cv(text)
    assert getattr(doc.work[0], field) == expected
    # The neighbouring fields must be undisturbed by the mutation.
    assert doc.work[0].company == "Example Data Co"
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]


@pytest.mark.parametrize("dash", ["-", "–", " – ", " - "])
def test_parse_accepts_every_date_dash_the_gate_accepts(dash):
    """The gate at cv/validate.py:89 matches `\\d{2}/(\\d{4})\\s*[--]` -- EN DASH or
    hyphen, with optional surrounding whitespace -- and this repo's own CLEAN_CV fixture
    uses the EN DASH. A parser that took the spec's literal `MM/YYYY-MM/YYYY` would raise
    on a CV the gate PASSES, sending every lead through a pointless retry and then to
    skipped-gate: the feature would compose CVs and bin all of them.
    """
    text = CV.replace("03/2021-present", f"03/2021{dash}present")
    assert f"03/2021{dash}present" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].title == "Staff Engineer"
    assert doc.work[0].location == "EXAMPLECITY"


def test_parse_accepts_the_repos_own_gate_clean_fixture():
    """The behavioural drift pin, and the reason it is behavioural: spec Out of scope
    forbids touching the gate, so there is no shared constant to assert on.

    `tests/test_cv_engine.py::test_clean_cv_is_actually_clean` already proves the GATE
    passes CLEAN_CV, and CLEAN_CV's date ranges use the EN DASH. Importing that exact
    fixture here closes the loop from the other end: whatever the gate certifies clean,
    parse_cv must accept. If these two ever disagree the lead is composed, gated, then
    binned -- so a single shared fixture is what keeps them honest.
    """
    from tests.test_cv_engine import CLEAN_CV
    assert "–" in CLEAN_CV, "the fixture no longer exercises the en dash; re-pick one"
    doc = parse_cv(CLEAN_CV)
    assert [r.title for r in doc.work] == [
        "Staff Engineer", "Senior Engineer", "Engineer", "Junior Engineer"]


def test_parse_returns_the_documented_types():
    """CvDocument is the PUBLIC CONTRACT a template author writes against; changing a
    field name is a breaking change for every user template."""
    doc = parse_cv(CV)
    assert isinstance(doc, CvDocument) and isinstance(doc.work[0], Role)
    assert [f for f in ("name", "contact", "profile", "work", "certificates", "education")
            if not hasattr(doc, f)] == []
    assert [f for f in ("company", "dates", "location", "title", "bullets")
            if not hasattr(doc.work[0], f)] == []
