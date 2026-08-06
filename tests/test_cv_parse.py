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
    """User content must not vanish silently from a PDF sent under their name.

    Matches the message PREFIX, not merely the header text. Both WORK-loop refusals now
    echo the candidate line (see the next test -- naming it is the whole point of the
    fix), so a `match="PUBLICATIONS"` alone would be satisfied by the generic
    bad-meta-line arm too, and deleting the `_is_header_shaped` branch entirely would
    leave this green. The prefix is what distinguishes them.
    """
    extra = CV.replace("CERTIFICATES", "PUBLICATIONS\n- Example paper, 2021\n\nCERTIFICATES")
    assert "PUBLICATIONS" in extra, "the replace no-opped"
    with pytest.raises(CvParseError, match=r"unmodelled section header 'PUBLICATIONS'"):
        parse_cv(extra)


@pytest.mark.parametrize("offending", [
    "– Coached the platform team",   # an en-dash "bullet": no marker this parser knows
    "1. Coached the platform team",  # a numbered bullet
    "Additional detail available on request.",  # stray prose
])
def test_a_bad_work_entry_refusal_names_the_offending_line_not_the_blank_after_it(offending):
    """The retry can only act on a message that says WHICH line is wrong.

    Measured pre-fix: each of these lines, sitting after a role's bullets, was taken as a
    candidate company; the line AFTER it is the blank separator, so the refusal read
    `unparseable meta line: ''`. The model was handed an empty string and told it was
    unparseable -- the second compose had nothing to work from, and the lead was binned.
    All three are gate-clean (validate() citation-checks only lines starting with
    `-`/`•`/`*`, so none of them is even looked at), which makes this the retry-wasting
    half of the governing bug class rather than a gate disagreement.
    """
    text = CV.replace("- Grew the team from 3 to 8 [ED2]",
                      f"- Grew the team from 3 to 8 [ED2]\n{offending}")
    assert f"\n{offending}\n" in text, "the replace no-opped"
    with pytest.raises(CvParseError) as ei:
        parse_cv(text)
    assert offending in str(ei.value), (
        f"the refusal does not name the offending line; it said: {ei.value}")
    assert "meta line" in str(ei.value), "the refusal no longer says what was expected"


@pytest.mark.parametrize("meta,expected_title", [
    ("03/2021-present | Staff Engineer", "Staff Engineer"),
    ("1/2018–06/2024 | Senior Engineer - Platform", "Senior Engineer - Platform"),
])
def test_parse_accepts_a_meta_line_with_no_location(meta, expected_title):
    """NOTHING can supply a LOCATION, so demanding one pointed the retry at fabrication.

    Measured 2026-08-06: `cv/bundle.py`'s render_bundle emits no location anywhere in the
    source bundle (`'LOCATION' in bundle` is False), validate() never checks for one, and
    a two-field `dates | title` meta line is gate-CLEAN. Requiring three fields therefore
    made this the strictest link in the chain for a field with no source -- and the only
    actionable reading of the refusal is "add the missing field", i.e. invent a city,
    which then ships in a PDF sent under the user's name with the gate never having
    looked at it. A parser refusal that pressures the model toward inventing content is
    the exact harm the fabrication gate exists to prevent, reintroduced downstream of it.

    `location` must still be PRESENT and empty, never absent: `CvDocument` populating
    every declared field is the premise `StrictUndefined` relies on in
    `renderers/template.py`, so a template reading `document.work[0].location` must not
    start raising just because the composer left the field out.
    """
    text = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer", meta)
    assert meta in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].title == expected_title
    assert doc.work[0].location == ""
    # The neighbours must be undisturbed -- a two-field line must not be MIS-SPLIT into
    # `dates | location` with the title lost, which is the failure this could regress to.
    assert doc.work[0].company == "Example Data Co"
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]
    assert doc.work[1].location == "EXAMPLECITY", "the three-field form must still work"


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


@pytest.mark.parametrize("variant", [
    # The SEPARATOR axis: the gate at cv/validate.py:89 matches `\d{2}/(\d{4})\s*[--]`
    # -- EN DASH or hyphen, with optional surrounding whitespace -- and this repo's own
    # CLEAN_CV fixture uses the EN DASH. A parser that took the spec's literal
    # `MM/YYYY-MM/YYYY` would raise on a CV the gate PASSES, sending every lead through
    # a pointless retry and then to skipped-gate: the feature would compose CVs and bin
    # all of them.
    "03/2021-present", "03/2021–present", "03/2021 – present", "03/2021 - present",
    # The TERMINAL-TOKEN axis: validate.py's date check is a plain `re.findall` over
    # START years and never inspects what follows the dash, so nothing upstream pins
    # the open-ended token's casing or spelling. compose.py's `_RULES` gives the model
    # NO slot for an open-ended range at all (`MM/YYYY-MM/YYYY`), so it must improvise
    # one for the current role -- and every CV has a current role. Measured: all of
    # these compose and pass the gate; before this fix, every row but the first here
    # was refused by the parser.
    "03/2021-Present", "03/2021-PRESENT", "03/2021-Current", "03/2021-current",
    "03/2021-now", "03/2021-NOW",
    # Found by the review sweep below, and a DIFFERENT mechanism from the en dash above:
    # the em dash and the word "to" are not in the gate's `[–-]` class AT ALL, so the
    # entry never matches `\d{2}/(\d{4})\s*[–-]`, contributes no start year, and the
    # reverse-chronology check passes VACUOUSLY. Invisible to the gate, and refused here.
    # ` to ` was refused by this parser and by NOTHING else. An em dash additionally trips
    # cv/slop.py's EM-DASH rule, which the engine folds into the same gate_msgs, so that
    # lead was already being re-composed with an actionable message -- accepting it here
    # removes redundant strictness, not an isolated bin. See `_DASH`'s comment for why it
    # is accepted regardless.
    "03/2021—present", "03/2021 — present", "03/2021 to present", "03/2021 TO present",
])
def test_parse_accepts_every_date_dash_the_gate_accepts(variant):
    """Both axes the gate is silent on -- separator character and terminal-token
    spelling/casing -- must be accepted here, or a CV the gate certifies clean composes,
    passes the gate, and is then silently binned right here. Test the AXIS: every
    variant the gate accepts, not one specific string.
    """
    text = CV.replace("03/2021-present", variant)
    assert variant in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].title == "Staff Engineer"
    assert doc.work[0].location == "EXAMPLECITY"


@pytest.mark.parametrize("canonical,drifted", [
    ("PROFILE", "Profile"),
    ("WORK EXPERIENCE", "Work Experience"),
    ("CERTIFICATES", "Certificates"),
    ("EDUCATION", "Education"),
])
def test_parse_accepts_case_drifted_section_headers(canonical, drifted):
    """validate.py:99-108 upper-cases every section header before comparing (`u =
    line.strip().upper()`), and engine.py's own two structural guards (~140, ~147)
    deliberately mirror that -- so a CV titled "Work Experience" (title case) is exactly
    as gate-clean as one titled "WORK EXPERIENCE". Before this fix the four section
    comparisons in parse_cv were exact-string, so every row here PASSED the gate and was
    REFUSED here: composed, gated, wasted the engine's one retry (the retry cannot help,
    since nothing in cv/compose.py's `_RULES` pins the exact casing to reproduce), then
    binned skipped-gate. Test the AXIS -- every header the gate recognises
    case-insensitively -- not one specific string.
    """
    text = CV.replace(canonical, drifted)
    assert drifted in text and canonical not in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.name == "EXAMPLE PERSON"
    assert len(doc.work) == 2
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


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


def test_parse_accepts_an_all_caps_company_name():
    """An acronym-shaped employer -- an all-caps initialism, the ordinary form for a
    broadcaster, a bank or a public agency -- is unremarkable in a real job hunt.
    Refusing it as an 'unmodelled section' merely for being all-caps reasons from two
    synthetic placeholders ("Example Data Co", "Example Analytics Ltd") to a claim about
    every employer, which is not sound -- and the refusal wastes the engine's one retry
    on a CV that was never malformed: there is nothing for a re-composition to fix.
    A line is only genuinely unmodelled when what FOLLOWS it also fails to look like a
    meta line; a well-formed entry must parse regardless of the company's casing.

    The fixture is a synthetic placeholder, and naming real firms even in prose is what
    `tests/` is barred from: three names that looked invented to a local reviewer here
    have each turned out to be really registered.
    """
    text = CV.replace("Example Data Co", "EXAMPLE DATA CO")
    assert "EXAMPLE DATA CO" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].company == "EXAMPLE DATA CO"
    assert doc.work[0].title == "Staff Engineer"


def test_parse_accepts_bullet_and_asterisk_markers():
    """cv/validate.py:120-123: the renderer treats a hyphen, a bullet glyph, and an
    asterisk all as bullet markers, so a WORK bullet composed with either of the other
    two already passes the gate this parser sits downstream of. A parser that only
    recognised a hyphen is STRICTER than the gate -- exactly the en-dash bug's shape: a
    CV the gate passed gets binned here instead."""
    text = CV.replace("- Cut p99 latency to 120ms [ED1]", "• Cut p99 latency to 120ms [ED1]")
    text = text.replace("- Grew the team from 3 to 8 [ED2]", "* Grew the team from 3 to 8 [ED2]")
    assert "• Cut p99" in text and "* Grew the team" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]


def test_parse_swallows_a_wrapped_multi_citation_line():
    """cv/compose.py:11 explicitly permits several citations per bullet ('several
    allowed: [id] [id]'). `_CITE_ONLY_RE` used to fullmatch exactly ONE bracket pair, so
    a multi-citation bullet the composer wrapped onto its own line ('[ED1] [EA1]') fell
    through as an unrecognised line instead of being swallowed the way the single-
    citation case already is."""
    wrapped = CV.replace("- Cut p99 latency to 120ms [ED1]",
                         "- Cut p99 latency to 120ms\n[ED1] [EA1]")
    assert "120ms\n[ED1] [EA1]" in wrapped, "the replace no-opped"
    doc = parse_cv(wrapped)
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]


def test_parse_raises_on_an_empty_meta_field():
    """A meta line's LOCATION and TITLE are free text, not date-shaped, so only an
    explicit non-emptiness check catches a field the composer left blank. Verified: this
    line used to parse cleanly to title="", which renders as a blank field in a PDF sent
    to an employer under the user's name -- the exact silent-misassignment harm spec
    section 0 exists to prevent, just with an empty string instead of a wrong one."""
    text = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer",
                      "03/2021-present | EXAMPLECITY | ")
    assert "03/2021-present | EXAMPLECITY | \n" in text, "the replace no-opped"
    with pytest.raises(CvParseError, match="meta line"):
        parse_cv(text)


@pytest.mark.parametrize("marker", ["•", "*"])
def test_parse_accepts_certificates_and_education_with_every_bullet_marker(marker):
    """cv/validate.py never citation-checks CERTIFICATES/EDUCATION at all (see
    `_BULLET_MARKERS`' comment in parse.py) -- the gate is silent on which marker either
    section uses. But this parser used to accept ONLY `"- "` (hyphen, then a literal
    space) for both, while WORK bullets already accepted a bullet glyph and an asterisk
    too. Measured pre-fix: a `•`/`*` marker in either trailing section yielded ZERO
    entries, and the (also pre-fix) unconditional acceptance of an empty section made
    that indistinguishable from the section genuinely being absent -- so BOTH
    certificates and education vanished from a rendered PDF with nothing logged.
    """
    text = CV.replace("- Example Cloud Practitioner, 2022",
                      f"{marker} Example Cloud Practitioner, 2022")
    text = text.replace("- Example University, 2010-2013 | BSc Computer Science",
                        f"{marker} Example University, 2010-2013 | BSc Computer Science")
    assert f"{marker} Example Cloud" in text and f"{marker} Example University" in text, \
        "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


def test_parse_accepts_a_certificate_marker_with_no_space_after_it():
    """The exact bug measured: `startswith("- ")` requires hyphen AND space, so
    `-Example Cloud Practitioner` (no space) matched neither branch, leaving the
    CERTIFICATES loop parked at that same non-blank line -- so the trailing blank-skip
    never advanced past it, and the subsequent EDUCATION check then failed too (it was
    not looking at "EDUCATION"). One missing space silently deleted BOTH sections from
    the parsed document, and the shipped template's `{% if document.certificates %}`
    guard hid the headings too -- nothing anywhere said a thing was missing.
    """
    text = CV.replace("- Example Cloud Practitioner, 2022", "-Example Cloud Practitioner, 2022")
    assert "-Example Cloud Practitioner" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"], (
        "a malformed CERTIFICATES marker must not also delete EDUCATION")


def test_parse_accepts_a_dash_marker_the_gate_never_inspects():
    """`_TRAILING_MARKERS` is deliberately WIDER than `_BULLET_MARKERS`, and this is the
    row that holds the difference.

    validate() citation-checks a line only inside WORK EXPERIENCE (`in_work`), so it
    never inspects a CERTIFICATES/EDUCATION marker at all -- an en-dash-marked
    certificate is gate-CLEAN. Measured pre-fix: it was nevertheless refused here, which
    is the governing bug class (parser stricter than the gate) and cost a retry the model
    could only spend re-emitting what it had already sent.

    The wider set is safe HERE and would not be safe in the WORK loop: a marker the
    parser accepts and validate.py:123 does not would render an UNCITED bullet into the
    PDF with the citation gate never having looked at it. That is why there are two
    tuples rather than one widened one -- see `_TRAILING_MARKERS`' comment.
    """
    text = CV.replace("- Example Cloud Practitioner, 2022",
                      "– Example Cloud Practitioner, 2022")
    text = text.replace("- Example University, 2010-2013 | BSc Computer Science",
                        "— Example University, 2010-2013 | BSc Computer Science")
    assert "– Example Cloud" in text and "— Example University" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


def test_the_work_bullet_markers_never_exceed_what_the_gate_citation_checks():
    """The other half of the two-tuple split, asserted STRUCTURALLY rather than by
    reading the comment that claims it.

    A WORK marker this parser accepts but validate.py does not is an uncited bullet
    rendered into a PDF the citation gate never checked -- a fabrication-gate BYPASS,
    which is a strictly worse failure than the over-strictness the trailing-section
    widening fixes. Derived from validate.py's own source (the tuple it passes to
    `startswith`), never hand-listed, so widening either side without the other reds.
    """
    import ast
    import inspect

    from sluice.cv import validate as _validate_mod
    from sluice.cv.parse import _BULLET_MARKERS, _TRAILING_MARKERS

    # The one `startswith((...))` call in validate.validate that gates the citation check.
    tree = ast.parse(inspect.getsource(_validate_mod))
    gate_markers = [
        tuple(el.value for el in node.args[0].elts)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "startswith" and node.args
        and isinstance(node.args[0], ast.Tuple)
        and all(isinstance(el, ast.Constant) for el in node.args[0].elts)
    ]
    assert len(gate_markers) == 1, (
        f"expected exactly one bullet-marker startswith() in cv/validate.py, found "
        f"{gate_markers} -- this guard can no longer say which tuple gates the citation "
        "check, so it must not pass having guessed")

    assert set(_BULLET_MARKERS) <= set(gate_markers[0]), (
        f"parse.py accepts WORK bullet markers the gate does not citation-check: "
        f"{sorted(set(_BULLET_MARKERS) - set(gate_markers[0]))}. Such a bullet is "
        "rendered into the PDF UNCITED with the fabrication gate never having looked "
        "at it.")
    assert set(_TRAILING_MARKERS) > set(_BULLET_MARKERS), (
        "the trailing-section tuple is no longer wider than the WORK tuple, so the "
        "en-dash certificate case is back to being refused while gate-clean")


def test_parse_refuses_unrecognised_content_under_a_trailing_header():
    """A CERTIFICATES/EDUCATION header with UNRECOGNISED, non-blank content under it
    (not a marked entry, not blank, not another recognised header) must REFUSE,
    not silently drop that content: an empty section also renders with no heading at
    all (`{% if document.certificates %}`), so a silently-dropped entry and a
    genuinely empty section would otherwise be indistinguishable in the PDF. Uses a
    NUMBERED entry, which neither tuple recognises as a marker.

    It used to use an en dash, which `_TRAILING_MARKERS` now accepts -- see
    test_parse_accepts_a_dash_marker_the_gate_never_inspects for why that stopped being
    a refusal. A numbered list is the same kind of case the en dash used to stand for
    here: content the parser genuinely cannot place, with a refusal message the retry
    can act on (it names the markers that ARE accepted).

    NOTE what this is NOT: "zero entries recognised", full stop, is NOT sufficient
    reason to refuse -- a header followed by blank lines then EOF or another header is
    a legitimately empty section (see test_parse_accepts_a_trailing_header_followed_
    immediately_by_another and its blank-line/EOF sibling below) and must NOT raise.
    An earlier version of this fix refused on zero entries unconditionally, which
    reintroduced the exact governing bug class this feature exists to close: a blank
    line straight after "CERTIFICATES" (which compose.py's own `_RULES` format
    encourages, since it uses identical spacing after "WORK EXPERIENCE") burned a
    wasted retry -- the fed-back message told the model to do exactly what it already
    did -- and then binned a lead the gate had passed clean on the FIRST attempt. This
    test's numbered entry is different in kind: it is content the parser genuinely
    cannot place, not a formatting variant the gate already tolerates.
    """
    text = CV.replace("- Example Cloud Practitioner, 2022",
                      "1. Example Cloud Practitioner, 2022")
    assert "1. Example Cloud Practitioner" in text, "the replace no-opped"
    with pytest.raises(CvParseError, match="CERTIFICATES"):
        parse_cv(text)


def test_parse_accepts_a_blank_line_after_a_trailing_header():
    """Reproduces the re-review's exact finding against this repo's own gate-clean
    fixture: `CLEAN_CV` (tests/test_cv_engine.py) gate=PASSes as-is, and a blank line
    inserted after its CERTIFICATES header must gate=PASS this parser too --
    compose.py's `_RULES` format block blank-separates "WORK EXPERIENCE" from its own
    first entry, so a model mirroring that spacing for CERTIFICATES is the LIKELY
    case, not an exotic one. Using the repo's own gate-clean fixture (rather than the
    synthetic `CV` above) is what makes the "gate passes, parser must too" property
    concrete instead of merely asserted.

    Measured pre-fix (the version of the CERTIFICATES/EDUCATION rewrite that shipped
    before this re-review): this exact input RAISED "CERTIFICATES header present but
    no entries recognised" -- reproduced end-to-end through cv/engine.py's run_one as
    status=skipped-gate after TWO compose calls, because the fed-back message told the
    model to do exactly what it already did.
    """
    from tests.test_cv_engine import CLEAN_CV
    text = CLEAN_CV.replace("CERTIFICATES\n- CSM", "CERTIFICATES\n\n- CSM")
    assert "CERTIFICATES\n\n- CSM" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == ["CSM"]
    assert doc.education == ["Uni"]


def test_parse_accepts_a_trailing_header_followed_immediately_by_another():
    """A CERTIFICATES header with nothing under it before EDUCATION starts has NO
    content to drop -- a candidate who genuinely holds no certificates has no
    gate-clean way to invent one, and validate() never requires either trailing
    section to be non-empty. This must yield an empty list, not a refusal: refusing
    here would feed the model a message it can only satisfy by fabricating content,
    which is the exact harm the fabrication gate exists to prevent.
    """
    text = CV.replace("CERTIFICATES\n- Example Cloud Practitioner, 2022\n\nEDUCATION",
                      "CERTIFICATES\nEDUCATION")
    assert "CERTIFICATES\nEDUCATION" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == []
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


def test_parse_accepts_a_trailing_header_followed_only_by_blank_lines_then_eof():
    """Same axis, at end of document: a CERTIFICATES header with nothing after it but
    blank lines and EOF has no content to drop either.
    """
    text = CV.replace(
        "CERTIFICATES\n- Example Cloud Practitioner, 2022\n\n"
        "EDUCATION\n- Example University, 2010-2013 | BSc Computer Science\n",
        "CERTIFICATES\n\n")
    assert text.rstrip("\n").endswith("CERTIFICATES"), "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == []
    assert doc.education == []


def test_parse_drops_an_empty_certificate_entry():
    """A lone marker with nothing after it (`"-"` alone) strips to the empty string,
    which would otherwise render as a blank `<li>` in the PDF. Dropped rather than
    appended: an empty entry is not content to protect, just noise the model emitted
    alongside a real one.
    """
    text = CV.replace("- Example Cloud Practitioner, 2022",
                      "- Example Cloud Practitioner, 2022\n-")
    assert "2022\n-\n" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]


@pytest.mark.parametrize("meta,expected_dates", [
    ("1/2021-present | EXAMPLECITY | Staff Engineer", "1/2021-present"),
    ("03/2021-9/2024 | EXAMPLECITY | Staff Engineer", "03/2021-9/2024"),
])
def test_parse_accepts_a_single_digit_month(meta, expected_dates):
    """validate.py:89's chronology check is `\\d{2}/(\\d{4})\\s*[--]` -- a literal
    TWO-digit month. A single-digit month does not match that regex at all, so
    `re.findall` omits the entry from the years list entirely and the reverse-
    chronological check passes VACUOUSLY rather than failing -- the gate does not
    merely tolerate this shape, it never notices the entry exists. A parser requiring
    exactly two digits was stricter than a gate that is blind to the distinction --
    the governing bug class again, one field over from the dash and the terminal word.
    """
    text = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer", meta)
    assert meta in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].dates == expected_dates
    assert doc.work[0].title == "Staff Engineer"


def test_parse_accepts_education_before_certificates():
    """validate.py:107 turns `in_work`/`in_profile` off on seeing EITHER header and
    never records which one it saw or in what order -- the gate is completely
    order-agnostic between these two sections, so a CV composing them in the other
    order is exactly as gate-clean as the canonical one. Before this fix the two
    trailing-section readers were two fixed if-blocks in a hard-coded order: a CV
    emitting EDUCATION first parsed EDUCATION but never even looked for CERTIFICATES
    afterwards -- silently dropping it, 'the same effect on certs' the finding this
    fixes names for the malformed-marker case.
    """
    swapped = CV.replace(
        "CERTIFICATES\n- Example Cloud Practitioner, 2022\n\n"
        "EDUCATION\n- Example University, 2010-2013 | BSc Computer Science\n",
        "EDUCATION\n- Example University, 2010-2013 | BSc Computer Science\n\n"
        "CERTIFICATES\n- Example Cloud Practitioner, 2022\n")
    assert swapped != CV, "the replace no-opped"
    doc = parse_cv(swapped)
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


# ── the IMPLICATION sweep: gate-clean  =>  the parser does not refuse ────────────
#
# Every table above enumerates strings SOMEONE CHOSE, and a table whose cases you chose
# certifies nothing. Six separate instances of one bug -- the parser stricter than the
# gate upstream of it -- have now been found on this branch, five of them by adding a row
# to a hand-written table AFTER someone happened to think of the case. Two more (the EM
# DASH and the word "to") survived a table whose own docstring claimed to "test the AXIS".
#
# The property those tables are groping at is a single implication:
#
#     validate(cv, bundle) == []   =>   parse_cv(cv) does not raise
#
# and it is worth stating because its violation is silent and expensive. A CV the gate
# certifies clean has already cost one LLM composition; refusing it here appends a FORMAT
# violation, buys a SECOND composition, and -- when the retry cannot act on the message,
# which is the usual case for a formatting variant nothing upstream pins -- ends at
# `skipped-gate` with the lead binned.
#
# `validate()` is the RIGHT antecedent even though it is not the engine's whole gate,
# and the distinction is measured rather than assumed: `cv/engine.py` folds `cv/slop.py`
# into the same `gate_msgs`, and 36 of the 162 rows below carry an em dash, which trips
# the slop checker's EM-DASH rule independently. Those rows would be re-composed anyway,
# with an actionable message -- so for them a parser refusal is REDUNDANT strictness
# rather than an isolated bin, and the harm above is overstated. Using the wider gate as
# the antecedent would have made 36 rows vacuous and hidden the parser's behaviour on
# them; `validate()` keeps every row live, which is what a drift test between the gate
# and the parser wants. The rows that carry the harm in full are the ones nothing else
# objects to -- ` to `, the single-digit month, the terminal spellings.
#
# So the expectation below is COMPUTED from the gate
# for every row rather than written down beside it: a row nobody thought to check when
# widening the parser reds by itself, which is the only thing that ends the sequence.
#
# The alphabet is still finite (it must be -- the gate is blind to most separators, so
# "every string the gate passes" is not a satisfiable contract for any parser). What
# changes is where the verdict comes from.

# Placed between the start and the terminal token of the first role's date range.
_SEPARATORS = ("-", "–", "—", " - ", " – ", " — ", " to ", " To ", " TO ")
# Every open-ended spelling an LLM reaches for, plus closed ranges of both month widths.
_TERMINALS = ("present", "Present", "PRESENT", "current", "Current", "now", "NOW",
              "06/2024", "6/2024")
# validate.py's `\d{2}/` cannot match a single-digit month, so that entry contributes no
# start year and is invisible to the chronology check -- gate-clean by omission.
_STARTS = ("02/2023", "2/2023")


def _gate_verdict(cv_text):
    """validate()'s own verdict on `cv_text`. Never assumed, always computed.

    Uses the repo's real gate against the same synthetic bundle
    `tests/test_cv_engine.py::test_the_unparseable_fixture_still_passes_the_gate` builds,
    so "the gate passes this" means the shipped gate, not a re-implementation of it here
    -- a second, weaker copy of the gate inside its own drift test would certify
    anything.
    """
    from sluice.cv.bundle import build_bundle, render_bundle
    from sluice.cv.validate import validate
    from tests.test_cv_engine import ENTRIES
    bundle = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[], jd_keywords=[],
        prefix_map={"Example Foundry": "EF"}))
    return validate(cv_text, bundle)


def test_the_gate_helper_can_both_pass_and_fail():
    """NON-VACUITY for the sweep below, in the direction that can actually hide.

    The implication is vacuously true for any row the gate REJECTS, so a `_gate_verdict`
    that returned a violation for everything -- a mis-built bundle, a changed prefix_map,
    an import that silently resolved to a stub -- would leave every row below passing
    having asserted nothing whatsoever about the parser. (The reverse failure, a gate that
    passed everything, makes the sweep STRICTER rather than weaker, so it is not the
    hazard here.) Pin both verdicts.
    """
    from tests.test_cv_engine import CLEAN_CV
    assert _gate_verdict(CLEAN_CV) == [], "the gate no longer certifies its own clean fixture"
    dirty = CLEAN_CV.replace("- Shipped [EF1]", "- Shipped")
    assert "- Shipped\n" in dirty, "the replace no-opped"
    assert _gate_verdict(dirty), "the gate certified an UNCITED bullet clean"


def test_which_separators_the_slop_checker_independently_objects_to():
    """Pins the CLAIM the comments above make about the engine's OTHER gate.

    `_DASH`'s comment and the sweep's block comment both say the em dash is redundantly
    strict (cv/slop.py objects too, actionably) while ` to ` was an isolated bin. That is
    a statement about a module this file does not otherwise touch, and a reason stated in
    a comment goes stale in silence -- so it is executed here. If slop.py ever stops
    flagging the em dash, or starts flagging ` to `, both comments become wrong and this
    reds instead of them quietly misleading the next reader.
    """
    from sluice.cv.slop import check_text
    from tests.test_cv_engine import CLEAN_CV

    assert check_text(CLEAN_CV)[0] == [], (
        "the base fixture is already slop-flagged, so neither row below discriminates")
    em = CLEAN_CV.replace("02/2023–present", "02/2023—present")
    assert check_text(em)[0], "cv/slop.py no longer objects to an em dash on its own"
    to = CLEAN_CV.replace("02/2023–present", "02/2023 to present")
    assert check_text(to)[0] == [], "cv/slop.py now objects to ' to ' as well"


def test_the_separator_alphabet_still_carries_the_measured_regressions():
    """SCOPE. An empty (or quietly trimmed) parametrize list yields zero tests and reads
    exactly like a clean sweep, so the alphabet's own coverage is asserted here rather
    than left to whatever survives a future edit. The two named characters are the ones
    the review actually measured as gate-clean-and-refused; `to` is the word form.
    """
    assert _SEPARATORS and _TERMINALS and _STARTS, "the sweep would run zero rows"
    bare = {s.strip() for s in _SEPARATORS}
    assert {"-", "–", "—", "to"} <= bare, f"missing a measured separator: {sorted(bare)}"
    assert {"present", "PRESENT"} <= set(_TERMINALS)


@pytest.mark.parametrize("start", _STARTS)
@pytest.mark.parametrize("terminal", _TERMINALS)
@pytest.mark.parametrize("separator", _SEPARATORS)
def test_a_date_range_the_gate_certifies_clean_is_never_refused_here(
        separator, terminal, start):
    """The implication itself, one row at a time. See the block comment above.

    Built on CLEAN_CV -- the repo's own gate-clean fixture, shared with
    tests/test_cv_engine.py -- so the two ends of the pipeline cannot drift apart: what
    the gate certifies, this parser must accept.
    """
    from tests.test_cv_engine import CLEAN_CV
    original = "02/2023–present"
    variant = f"{start}{separator}{terminal}"
    text = CLEAN_CV.replace(original, variant)
    assert text.count(variant) == 1, "the replace no-opped"
    if variant != original:
        assert original not in text, "the replace left the original range behind"

    # The ANTECEDENT, computed. A row the gate rejects says nothing about the parser and
    # must not be silently skipped either -- every row in this alphabet is expected to be
    # gate-clean, so a rejection means the FIXTURE broke, not that the row is exempt.
    violations = _gate_verdict(text)
    assert violations == [], (
        f"{variant!r} is no longer gate-clean ({violations}), so this row proves nothing "
        "about the parser. Fix the fixture or drop the row -- do not let it pass "
        "vacuously.")

    # The CONSEQUENT.
    try:
        doc = parse_cv(text)
    except CvParseError as e:
        pytest.fail(
            f"the gate certifies {variant!r} clean and the parser refuses it: {e}. That "
            "costs a composition, a retry the model cannot act on, and then the lead.")
    assert doc.work[0].dates == variant
    assert doc.work[0].title == "Staff Engineer", "the meta line was mis-split"
