"""cv/document.py: the CV sluice builds, and its text forms (spec §7.1, §7.3, §6.4)."""
from sluice.core.layout import Slot
from sluice.core.protocols import CandidateProfile, CvLayout, LayoutRole
from sluice.cv.document import assemble, audit_text, model_lines, to_text
from sluice.cv.reply import Bullet, Reply
from sluice.cv.selection import Selection, select
from tests.test_cv_script_golden import CANONICAL_CV, GOLDEN

CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")
SYSTEMS = LayoutRole("Example Systems", "02/2023", "present", location="Example Location A",
                     title="SYNTHETIC-TITLE-1")
ANALYTICS = LayoutRole("Example Analytics", "06/2020", "01/2023",
                       location="Example Location B", title="SYNTHETIC-TITLE-2")
LAYOUT = CvLayout(roles=(SYSTEMS, ANALYTICS), certificates=("Example Scrum Master",),
                  education=("Example University, 09/2010–07/2014 | BSc Example",))
SLOTS = (Slot("R1", SYSTEMS, ("EF1",), None), Slot("R2", ANALYTICS, ("EF1", "EF2"), None))
SELECTION = Selection(
    profile="I build reliable systems.",
    roles={"R1": (Bullet("Shipped the platform", ("EF1",)),),
           "R2": (Bullet("Grew team from 3 to 8", ("EF1", "EF2")),)},
    skills=("Example Query",))


def _assembled(layout=LAYOUT, slots=SLOTS, selection=SELECTION):
    return assemble(layout, slots, selection, CANDIDATE)


def test_to_text_reproduces_what_a_script_received_from_the_old_pipeline():
    assert to_text(_assembled().document) == GOLDEN


def test_to_text_with_cites_is_the_cited_canonical_form():
    a = _assembled()
    assert to_text(a.document, cites=a.cites) == CANONICAL_CV


def test_the_document_takes_its_structure_from_the_layout_and_the_candidate():
    doc = _assembled().document
    assert doc.name == "JANE ROE"
    assert doc.contact == "+1 555 0100"
    assert [(r.company, r.dates, r.location, r.title) for r in doc.work] == [
        ("Example Systems", "02/2023–present", "Example Location A", "SYNTHETIC-TITLE-1"),
        ("Example Analytics", "06/2020–01/2023", "Example Location B", "SYNTHETIC-TITLE-2")]
    assert doc.certificates == ["Example Scrum Master"]
    assert doc.skills == ["Example Query"]


def test_no_document_string_carries_a_citation():
    """Spec §12.1: cites live in AssembledCv.cites, `to_text(..., cites=True)` and the audit
    excerpt -- never in the document a renderer receives. Checked with cv/render.py's own
    pattern, so a renderer that strips citations finds nothing to strip."""
    from sluice.cv.render import _CITE_RE
    a = _assembled()
    assert any(c for role in a.cites for c in role), "premise: the selection carries cites"
    doc = a.document
    strings = [doc.name, doc.contact, doc.profile, *doc.skills, *doc.certificates,
               *doc.education]
    for role in doc.work:
        strings += [role.company, role.dates, role.location, role.title, *role.bullets]
    assert [s for s in strings if _CITE_RE.search(s)] == []


def test_bullets_land_under_their_own_slot_whatever_the_reply_key_order():
    reply = Reply("I build.", {"R2": (Bullet("Second role work", ("EF2",)),),
                               "R1": (Bullet("First role work", ("EF1",)),)}, ())
    a = _assembled(selection=select(reply, SLOTS, (), None))
    assert [r.bullets for r in a.document.work] == [["First role work"], ["Second role work"]]
    assert a.cites == ((("EF1",),), (("EF2",),))


def test_an_omitted_middle_slot_renders_empty_and_shifts_nothing():
    third = LayoutRole("Example Meridian", "01/2018", "05/2020")
    layout = CvLayout(roles=(SYSTEMS, ANALYTICS, third))
    slots = SLOTS + (Slot("R3", third, ("EG1",), None),)
    reply = Reply("I build.", {"R1": (Bullet("One", ("EF1",)),),
                               "R3": (Bullet("Three", ("EG1",)),)}, ())
    doc = assemble(layout, slots, select(reply, slots, (), None), CANDIDATE).document
    assert [r.bullets for r in doc.work] == [["One"], [], ["Three"]]
    # The audit pairs each bullet with its OWN heading too, and skips the empty role.
    assert audit_text(select(reply, slots, (), None), slots) == (
        "PROFILE\nI build.\n\nExample Systems\n- One [EF1]\n\nExample Meridian\n- Three [EG1]\n")


def test_the_meta_line_is_always_three_positional_fields():
    untitled = LayoutRole("Example Northgate", "08/2001", "present", location="Example Location A")
    unlocated = LayoutRole("Example Beta", "01/2019", "12/2019", title="SYNTHETIC-TITLE-3")
    layout = CvLayout(roles=(untitled, unlocated))
    slots = (Slot("R1", untitled, (), 0), Slot("R2", unlocated, (), 0))
    text = to_text(assemble(layout, slots, Selection("p", {"R1": (), "R2": ()}, ()),
                            CANDIDATE).document)
    lines = text.splitlines()
    assert "08/2001–present | Example Location A | " in lines
    assert "01/2019–12/2019 |  | SYNTHETIC-TITLE-3" in lines


def test_a_role_with_no_bullets_writes_its_heading_and_meta_line_only():
    layout = CvLayout(roles=(SYSTEMS,))
    slots = (Slot("R1", SYSTEMS, (), 0),)
    text = to_text(assemble(layout, slots, Selection("p", {"R1": ()}, ()), CANDIDATE).document)
    block = text.split("WORK EXPERIENCE\n\n", 1)[1]
    assert block.rstrip("\n") == ("Example Systems\n02/2023–present | Example Location A | "
                                  "SYNTHETIC-TITLE-1")


def test_the_text_ends_in_exactly_one_newline_with_no_trailing_section():
    layout = CvLayout(roles=(SYSTEMS,))
    text = to_text(assemble(layout, (SLOTS[0],), Selection("p", {"R1": ()}, ()),
                            CANDIDATE).document)
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_cites_that_do_not_match_the_document_raise_a_named_error():
    import pytest
    doc = _assembled().document
    with pytest.raises(ValueError, match="cites do not match"):
        to_text(doc, cites=((("EF1",),),))


def test_no_section_heading_is_written_for_an_empty_section():
    layout = CvLayout(roles=(SYSTEMS,))
    text = to_text(assemble(layout, (SLOTS[0],), Selection("p", {"R1": ()}, ()),
                            CANDIDATE).document)
    for heading in ("CERTIFICATES", "EDUCATION", "SKILLS"):
        assert heading not in text.splitlines()


def test_the_audit_sees_the_model_text_with_headings_and_cites_and_nothing_else():
    text = audit_text(SELECTION, SLOTS)
    assert text == ("PROFILE\nI build reliable systems.\n\nExample Systems\n"
                    "- Shipped the platform [EF1]\n\nExample Analytics\n"
                    "- Grew team from 3 to 8 [EF1] [EF2]\n")
    for vault_only in ("02/2023", "Example Location A", "SYNTHETIC-TITLE-1",
                       "Example Scrum Master", "Example Query", "JANE ROE"):
        assert vault_only not in text


def test_model_lines_are_the_profile_then_every_kept_bullet_in_slot_order():
    assert model_lines(SELECTION, SLOTS) == [
        (1, "I build reliable systems."), (2, "Shipped the platform"),
        (3, "Grew team from 3 to 8")]
