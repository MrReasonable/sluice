"""Turn a composed CV (plain text) into structured data a Jinja2 template can lay out.

Pure and deterministic: no I/O, no rendering, and deliberately NO validation of facts --
the fabrication gate (`cv/validate.py`) has already run on this text by the time
`parse_cv` sees it, and re-checking facts here would be a second, weaker gate and a way
around the real one (spec section 1). What this module refuses is SHAPE, not content: an
unmodelled section, or a role line that does not match the meta grammar. Refusing here
feeds the engine's existing single retry (the model is asked to fix its own formatting,
which an LLM is reliably good at); it does not kill the lead.

All the risk in the template-renderer design lives in this function, which is why it is
pure -- every case is a table-driven unit test in tests/test_cv_parse.py, with no
fixtures, no subprocess, no PDF.
"""
import re
from dataclasses import dataclass

# Shared, not re-declared: this repo has already paid once for a check that restated a
# pattern it was supposed to match instead of importing it (see CLAUDE.md's neutrality-gate
# incident). `_CITE_RE`'s leading `\s*` is exactly why citations must be stripped PER FIELD
# below rather than over the whole text up front -- see the citation-continuation handling
# in the WORK EXPERIENCE loop.
from sluice.cv.render import _CITE_RE


class CvParseError(ValueError):
    """The composed CV does not match the grammar's SHAPE.

    Never raised for a fact the gate could have caught -- only for structure the gate does
    not model: an unmodelled section header, or a role line that cannot be split into the
    three meta-grammar fields. `cv/engine.py` feeds this into the SAME retry loop it feeds
    a gate violation into, in the same shape, so the model gets one chance to fix its own
    formatting before the lead is skipped like any other gate failure.
    """


@dataclass
class Role:
    """One WORK EXPERIENCE entry. Field names are the PUBLIC CONTRACT a user's Jinja2
    template writes against (`sluice/templates/cv_plain.html.j2` already depends on this
    exact shape) -- renaming a field is a breaking change for every user template."""
    company: str
    dates: str
    location: str
    title: str
    bullets: list[str]


@dataclass
class CvDocument:
    """The whole parsed CV. Same public-contract rule as `Role` above."""
    name: str
    contact: str
    profile: str
    work: list[Role]
    certificates: list[str]
    education: list[str]


# The only two headers legal immediately after a WORK EXPERIENCE role's bullets end.
# A line that is neither of these AND fails to be followed by a valid meta line (see
# `_is_header_shaped`'s use below) is refused by name rather than silently absorbed as a
# company, which would only fail one line later as a bad meta line and hide the real
# problem behind a misleading one.
_TRAILING_SECTIONS = frozenset({"CERTIFICATES", "EDUCATION"})

# cv/validate.py:120-123: "the renderer treats '-', '•', and '*' all as bullets, so
# a WORK bullet composed with '•' or '*' is delivered in the rendered PDF and MUST
# be citation-checked here too" -- so a CV using either of those markers has ALREADY
# passed the gate this parser sits downstream of. Recognising only a hyphen would make
# this parser STRICTER than the gate -- the exact shape of the en-dash bug above: a CV
# the gate passed gets refused and binned here instead. The gate's OWN citation check is
# scoped to WORK bullets only (`in_work`) -- CERTIFICATES and EDUCATION carry no
# citations, and compose.py's format contract asks for a hyphen only there -- but this
# PARSER's marker recognition is intentionally not scoped the same way: the
# CERTIFICATES/EDUCATION reader below reuses this exact tuple too, because a malformed
# marker in either of THOSE sections is the identical silent-drop harm even though the
# gate never inspects their markers at all (never citation-checked, so never gate-tested
# either way -- see the finding this fixes for the measured before/after).
_BULLET_MARKERS = ("-", "•", "*")

# CERTIFICATES/EDUCATION only, and DELIBERATELY WIDER than `_BULLET_MARKERS`. Widening
# the shared tuple above would be a gate bypass: validate.py:123 citation-checks a WORK
# line only when it starts with one of `-`/`•`/`*`, so a marker this parser accepted
# there and the gate did not would render an UNCITED bullet into the PDF with the
# citation gate never having looked at it. That reasoning does not reach the two trailing
# sections, because the gate never citation-checks them AT ALL (`in_work` is false
# throughout) -- there is no check here for a wider marker to slip past, so the only
# effect of accepting one is that a gate-clean CV stops being refused.
#
# The en dash earns its place by measurement: `– CSM` under CERTIFICATES passes the gate
# untouched and, before this, was refused by the loop below. That is the governing bug
# class -- stricter here than upstream -- and it cost a retry the model could only spend
# re-emitting what it had already sent. The em dash is here for the same reason as in
# `_DASH`: the two are equally plausible outputs and equally invisible to the gate.
_TRAILING_MARKERS = _BULLET_MARKERS + ("–", "—")

# The gate at cv/validate.py:89 matches `\d{2}/(\d{4})\s*[–-]` -- EN DASH (U+2013) or
# ASCII hyphen, with optional surrounding whitespace -- and this repo's own gate-clean
# fixture (CLEAN_CV in tests/test_cv_engine.py) uses the en dash exclusively. The spec's
# literal grammar (`MM/YYYY-MM/YYYY`, ASCII hyphen only) is wrong as a sole rule: a parser
# that accepted only the hyphen would raise on CVs the gate has already certified clean,
# so every lead would compose, pass the gate, and then be silently binned right here.
# Do not narrow this to one character, and do not refactor a shared constant out of
# validate.py -- the spec's Out of scope forbids touching the fabrication gate, and this
# behavioural pin (see tests/test_cv_parse.py's CLEAN_CV import) is stronger than a shared
# literal would be anyway.
#
# EM DASH (U+2014) and the word "to" are here for the OPPOSITE reason to the en dash, and
# the difference matters: the en dash is IN the gate's character class, so a CV using it
# is checked and passes. Neither of these two is -- so `\d{2}/(\d{4})\s*[–-]` does not
# match that entry AT ALL, `re.findall` records no start year for it, and an omitted year
# can never break `years == sorted(years, reverse=True)`. The gate passes VACUOUSLY, the
# same way it does for a single-digit month below. Being invisible to the gate is not
# being rejected by it: measured 2026-08-06, `02/2023—present` and `02/2023 to present`
# are both gate-CLEAN and were both refused here. `to` requires whitespace on BOTH sides
# so it cannot match inside a token, and `re.IGNORECASE` on the compiled pattern covers
# `To`/`TO` without a second alternative.
_DASH = r"(?:\s*[-–—]\s*|\s+to\s+)"

# `present` is the only open-ended terminal the spec's literal grammar names, but
# compose.py's own `_RULES` format block (`MM/YYYY-MM/YYYY`) gives the model NO slot for
# an open-ended range at all -- it must improvise one for whichever role is current, and
# every real CV has a current role, so this branch fires on every lead. validate.py never
# inspects the terminal token itself (its date check is a plain `re.findall` over start
# years, blind to what follows the dash), so nothing upstream pins its casing or its
# spelling -- an LLM asked to improvise reaches for "Present", "Current" or "now" as
# readily as "present" (measured: all compose and pass the gate). Matching only the
# lowercase literal made THIS the one strict link in the chain for a date every CV is
# guaranteed to carry -- the same shape as the en-dash fix just above, on the terminal
# token instead of the separator. `re.IGNORECASE` plus the `current`/`now` synonyms closes
# it. This check's real job is catching a MIS-SPLIT line (e.g. one missing a pipe): that
# is fully carried by the `\d{2}/\d{4}<dash>` prefix regardless of which terminal token
# follows, and `parts[1]`/`parts[2]` below are already accepted as arbitrary non-empty
# free text -- so strictness concentrated on `parts[0]`'s terminal word alone was never
# buying anything the prefix does not already buy.
#
# The MONTH itself is `\d{1,2}`, not `\d{2}`: validate.py:89's chronology check is
# `\d{2}/(\d{4})\s*[--]` -- a literal TWO-digit month -- so a single-digit month
# ("1/2020-present") simply does not match that regex at all. `re.findall` then finds
# no start year for that entry and silently omits it from the years list; an omitted
# year can never break the `years == sorted(years, reverse=True)` check, so the gate
# passes VACUOUSLY rather than failing. A parser requiring exactly two digits was
# therefore stricter than a gate that does not even notice the entry exists -- the
# governing bug class again, one field over. `\d{1,2}` closes it for both months in
# the range (start and end), since nothing pins the end month's width either.
_DATE_RANGE_RE = re.compile(
    rf"^\d{{1,2}}/\d{{4}}{_DASH}(?:\d{{1,2}}/\d{{4}}|present|current|now)$", re.IGNORECASE)

# A citation the composer wrapped onto its own line (rather than trailing the bullet it
# belongs to) still needs to disappear -- see the "wrapped" case in
# test_parse_preserves_line_structure_while_stripping. Fullmatch (not the shared
# `_CITE_RE`'s search/sub semantics) is deliberate: this line must be NOTHING BUT one or
# more citations to be swallowed as a continuation, or a genuine new company/heading
# that happens to start with "[" would vanish instead of being parsed. ONE-OR-MORE
# (not exactly one): cv/compose.py:11 explicitly permits several citations per bullet
# ("several allowed: [id] [id]"), and a multi-citation bullet is exactly as likely to
# get wrapped onto its own line as a single-citation one.
_CITE_ONLY_RE = re.compile(r"^(?:\[[A-Za-z]{2}[0-9]+\]\s*)+$")


def _is_section(line: str, name: str) -> bool:
    """True if `line` is exactly the section header `name`, compared the way the gate
    compares it: validate.py:99-108 upper-cases before comparing, and engine.py's own two
    structural guards (~140, ~147) deliberately mirror that so they fire in exactly the
    cases validate() silently skips. `casefold()` rather than `.upper()` is the more
    correct general tool for case-insensitive comparison (it folds more of Unicode than
    `.upper()` does) and is behaviourally identical to `.upper()` for the fixed ASCII
    header set this grammar defines (PROFILE, WORK EXPERIENCE, CERTIFICATES, EDUCATION).

    Comparing any more strictly than the gate does is the same shape of bug as the
    en-dash and bullet-marker fixes: nothing in compose.py's `_RULES` pins the exact
    casing the model must reproduce, so a CV titled "Work Experience" (title case)
    passes the gate and -- before this fix -- was refused right here.
    """
    return line.strip().casefold() == name.casefold()


def _is_header_shaped(line: str) -> bool:
    """True for a line that LOOKS LIKE a section heading: exact, shipped uppercase, the
    same way every heading this grammar defines (PROFILE, WORK EXPERIENCE, CERTIFICATES,
    EDUCATION) is normally emitted. This is a narrower, purely-visual check than
    `_is_section` above: it is never used to decide whether a line IS a recognised
    header (that comparison must stay case-insensitive, tracking the gate -- see
    `_is_section`), only to pick an ERROR MESSAGE once a candidate company has ALREADY
    failed to be followed by a valid meta line.

    This is NOT used to reject a candidate company outright: an acronym-shaped employer
    -- an all-caps initialism, the ordinary written form for a broadcaster, a bank or a
    public agency -- looks identical to a heading, and refusing it on casing alone would
    waste the engine's one retry on a CV that was never malformed: there is nothing for a
    re-composition to fix. It is used only to choose an ERROR MESSAGE once a candidate
    company has ALREADY failed to be followed by a valid meta line: at that point, an
    all-caps candidate is very likely a section header the composer emitted that this
    parser does not model (e.g. PUBLICATIONS) rather than a company whose meta line
    merely went wrong, so naming it "unmodelled" beats a generic "bad meta line" message
    that hides the real problem. See the `if not valid_meta:` branch below.
    """
    stripped = line.strip()
    return bool(stripped) and stripped == stripped.upper() and any(c.isalpha() for c in stripped)


def _strip_cite(field: str) -> str:
    """Strip a citation from ONE already-extracted field. Never call `_CITE_RE` on the
    whole text: its leading `\\s*` eats newlines, so a stand-alone citation line would
    delete itself AND the newline before it, changing the line count the rest of this
    parser depends on. Line structure is read first; this is applied per field, after."""
    return _CITE_RE.sub("", field).strip()


def parse_cv(text: str) -> CvDocument:
    """Parse the grammar in spec section 0. Raises `CvParseError` on any shape this
    parser does not model -- never on a fact, since the fabrication gate already ran."""
    lines = text.split("\n")
    idx = 0

    # ---- <contact line(s)> then <NAME>, both before PROFILE ----
    # Contact is OPTIONAL (CLEAN_CV, this repo's own gate-clean fixture, has none -- its
    # first line is the name heading with nothing above it): whatever non-blank lines
    # precede PROFILE, the LAST one is the name and everything before that is contact.
    header_lines = []
    while idx < len(lines) and not _is_section(lines[idx], "PROFILE"):
        if lines[idx].strip():
            header_lines.append(lines[idx].strip())
        idx += 1
    if idx >= len(lines):
        raise CvParseError("no PROFILE section found")
    if not header_lines:
        raise CvParseError("no name heading found before PROFILE")
    idx += 1  # consume the "PROFILE" line itself
    # MINOR, accepted trade-off: this takes the LAST pre-PROFILE line as the name and
    # everything before it as contact, matching `_RULES`' documented order (contact,
    # then name heading, then PROFILE). A model that emits the conventional CV order
    # instead -- name first, contact details after -- yields `name="Email: ..."` and a
    # contact block containing the real name, with nothing to catch it: both fields are
    # free text with no shape to validate against. Not fixed here because there is no
    # reliable SHAPE test to tell the two orderings apart (a name can look like
    # anything, contact details are not universally regex-shaped), and guessing wrong
    # would trade one silent misassignment for another rather than removing it.
    name = _strip_cite(header_lines[-1])
    contact = _strip_cite("\n".join(header_lines[:-1]))

    # ---- PROFILE prose, up to WORK EXPERIENCE ----
    profile_lines = []
    while idx < len(lines) and not _is_section(lines[idx], "WORK EXPERIENCE"):
        if lines[idx].strip():
            profile_lines.append(lines[idx].strip())
        idx += 1
    if idx >= len(lines):
        raise CvParseError("no WORK EXPERIENCE section found")
    idx += 1  # consume the "WORK EXPERIENCE" line itself
    profile = _strip_cite("\n".join(profile_lines))

    # ---- WORK EXPERIENCE: repeated <Company> / <meta line> / <bullets>* ----
    work: list[Role] = []
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if any(_is_section(stripped, h) for h in _TRAILING_SECTIONS):
            # CERTIFICATES or EDUCATION, compared case-insensitively like every other
            # header (see `_is_section`) -- stop here WITHOUT consuming the line, so the
            # section reader below starts from it.
            break

        # A line that is not blank and not a known trailing section is a CANDIDATE
        # company -- regardless of casing. Whether it really is one is decided by what
        # follows it, not by how it looks: an acronym-shaped employer (an all-caps
        # initialism) must parse exactly like any other when a valid meta line follows,
        # and casing is used only below, to pick an error message, once that check has
        # failed.
        company = stripped
        idx += 1
        meta_raw = lines[idx].strip() if idx < len(lines) else None
        parts = [p.strip() for p in meta_raw.split("|")] if meta_raw is not None else []
        # TWO parts (`dates | title`) is as legal as three. Nothing upstream can supply a
        # LOCATION: measured 2026-08-06, `cv/bundle.py`'s render_bundle emits no location
        # anywhere in the source bundle (`'LOCATION' in bundle` is False), validate()
        # never checks for one, and a two-field meta line is gate-CLEAN. Demanding three
        # made this the strictest link in the chain for a field the model has no source
        # for -- and the retry's only actionable reading of "unparseable meta line" is
        # "add the missing field", i.e. INVENT a city, which then ships unchecked because
        # the gate does not model the meta line at all. Aiming fabrication pressure at the
        # feature whose whole job is preventing fabrication is worse than the strictness
        # itself. Mis-split detection does not depend on the field count: it is carried
        # entirely by `parts[0]`'s `\d{1,2}/\d{4}<dash>` prefix, which a line missing a
        # pipe cannot satisfy. `parts[1:]` must all be non-empty either way -- an
        # explicitly BLANK field is a composer slip, not a field that was never emitted.
        valid_meta = (meta_raw is not None and len(parts) in (2, 3)
                      and _DATE_RANGE_RE.match(parts[0]) and all(parts[1:]))
        if not valid_meta:
            if _is_header_shaped(company):
                # All-caps, and what follows it doesn't look like a meta line either --
                # almost certainly a section header this parser does not model (e.g.
                # PUBLICATIONS) rather than a company whose meta line merely went wrong.
                raise CvParseError(f"unmodelled section header {company!r}")
            # NAME THE CANDIDATE LINE. `meta_raw` alone is very often the blank separator
            # AFTER the offending line (an en-dash bullet, a `1.` numbered bullet, or
            # stray prose all reach here with `meta_raw == ''`), so a message carrying
            # only `meta_raw` pointed the retry at an empty string and told the model
            # nothing it could act on.
            #
            # The two refusals stay distinguishable by their PREFIX, which is what the
            # mutation witness now keys on. An earlier version withheld `company` from
            # this arm instead, so that deleting the `_is_header_shaped` branch above
            # could not still satisfy a `pytest.raises(match="PUBLICATIONS")` through
            # here -- but withholding the one piece of actionable text was too high a
            # price for that. `tests/test_cv_parse.py::test_parse_refuses_a_section_it_
            # does_not_model` matches on "unmodelled section header" (not on the header
            # text alone), which keeps the witness and costs the message nothing.
            if meta_raw is None:
                raise CvParseError(
                    f"missing meta line: WORK EXPERIENCE entry {company!r} is the last "
                    f"line of the CV, with no meta line after it")
            raise CvParseError(
                f"unparseable meta line under WORK EXPERIENCE entry {company!r}: expected "
                f"'MM/YYYY-MM/YYYY | LOCATION | Role' (LOCATION may be omitted, leaving "
                f"'MM/YYYY-MM/YYYY | Role') on the next line, got {meta_raw!r}")
        # Unpack by WIDTH: `dates | location | title` when three, `dates | title` with an
        # empty location when two. A template renders `document.work[].location` either
        # way -- `CvDocument` always populates every field it declares, which is the
        # premise StrictUndefined relies on in renderers/template.py.
        if len(parts) == 3:
            dates, location, title = parts
        else:
            (dates, title), location = parts, ""
        idx += 1

        bullets: list[str] = []
        while idx < len(lines):
            line_stripped = lines[idx].strip()
            if line_stripped.startswith(_BULLET_MARKERS):
                bullets.append(_strip_cite(line_stripped[1:].lstrip()))
                idx += 1
                continue
            if _CITE_ONLY_RE.match(line_stripped):
                # One or more citations the composer wrapped onto their own line,
                # trailing the bullet just added. Contributes nothing new -- swallow it
                # and keep reading bullets, rather than letting it fall through as a
                # bogus company/header.
                idx += 1
                continue
            break

        work.append(Role(
            company=_strip_cite(company), dates=_strip_cite(dates),
            location=_strip_cite(location), title=_strip_cite(title), bullets=bullets,
        ))

    # ---- CERTIFICATES / EDUCATION (both optional, in EITHER order) ----
    # validate.py:107 turns `in_work`/`in_profile` off on seeing EITHER header and never
    # records which one it saw or in what order -- the gate is completely order-agnostic
    # between these two sections. A parser that hard-coded CERTIFICATES-then-EDUCATION
    # would be stricter than the gate on an axis the gate never checks at all: composing
    # them in the other order is exactly as gate-clean as the canonical order. Read
    # whichever recognised header comes next, in a loop, rather than two fixed
    # if-blocks -- see the reversed-order case in tests/test_cv_parse.py.
    sections: dict[str, list[str]] = {}
    while idx < len(lines):
        # Skip a blank run before looking for the next header. compose.py's `_RULES`
        # format block blank-separates "WORK EXPERIENCE" from its own first entry
        # (see the fixture header lines above), so a model mirroring that spacing for
        # CERTIFICATES/EDUCATION is the LIKELY case, not an exotic one -- measured: this
        # is what a blank line straight after "CERTIFICATES" used to do before this fix,
        # since the entries loop below never saw past it to find the real entries.
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            break

        header = next((h for h in _TRAILING_SECTIONS if _is_section(lines[idx], h)), None)
        if header is None or header in sections:
            # Neither a recognised header nor a repeat of one already read. MINOR,
            # accepted asymmetry: a line shaped this way BETWEEN two WORK roles raises
            # ("unmodelled section header") because `_is_header_shaped` catches it, but
            # here it is silently left unconsumed -- `parse_cv` simply returns without
            # looking at it. Not fixed: unlike the WORK loop (which must decide whether
            # a line is a new role or an error to keep parsing at all), nothing past
            # this point ever gets parsed either way, so raising would only change WHEN
            # the same "unmodelled trailing content" case is reported, not whether it
            # silently drops content that was never captured as a field.
            break
        idx += 1  # consume the header line

        # Skip blank line(s) between the header and its first entry -- see the comment
        # at the top of this loop. Measured pre-fix: without this, a header immediately
        # followed by a blank line made the entries loop below see the blank line first,
        # read zero entries, and (in the version of this fix that shipped before the
        # re-review caught it) RAISE -- burning a second LLM call to ask the model to
        # do exactly what it already did, then binning a lead the gate had passed clean.
        while idx < len(lines) and not lines[idx].strip():
            idx += 1

        # `_TRAILING_MARKERS` (see its definition: the WORK set PLUS the en and em dash)
        # and the identical optional-space handling WORK bullets use (`[1:].lstrip()`),
        # rather than the narrower `startswith("- ")` this used to be. Measured pre-fix: a
        # missing space ("-Example Cloud Practitioner"), a bullet glyph, an asterisk, or
        # an en dash marker each yielded ZERO entries here while passing the fabrication
        # gate untouched (this content is never citation-checked) -- so a CV with a
        # malformed marker in either section shipped a PDF silently missing it.
        #
        # An earlier revision of this comment claimed all four cases were fixed while the
        # loop still read `_BULLET_MARKERS`, under which the en dash was NOT a marker at
        # all: it fell through to the refusal below. The comment was false for one of the
        # four cases it named, which is this repo's named defect class -- a stated reason
        # going stale in silence. It is true now because the tuple changed, not because
        # the sentence was reworded, and
        # `test_parse_accepts_a_dash_marker_the_gate_never_inspects` is what holds it.
        entries: list[str] = []
        while idx < len(lines) and lines[idx].strip().startswith(_TRAILING_MARKERS):
            entry = _strip_cite(lines[idx].strip()[1:].lstrip())
            if entry:
                # A lone marker with nothing after it ("-" alone) strips to "", which
                # would otherwise render as an empty `<li>` in the PDF -- dropped rather
                # than appended, since an empty entry is not content to protect, just
                # noise the model emitted.
                entries.append(entry)
            idx += 1

        # Skip a trailing blank run before deciding what is next (another header, or
        # nothing left).
        while idx < len(lines) and not lines[idx].strip():
            idx += 1

        # Refuse ONLY when unrecognised, non-blank content sits directly under this
        # header -- that is the one case that actually hides real content from the PDF.
        # A header followed by nothing but blanks, then another header or EOF, has NO
        # content to drop: it is a legitimately empty section (a candidate who genuinely
        # holds no certificates has no gate-clean way to satisfy any other verdict here,
        # and validate() never requires one to exist), so this must yield an empty list,
        # not a refusal that pressures the model toward inventing content to satisfy a
        # message it cannot otherwise gate-cleanly answer -- the exact harm the
        # fabrication gate exists to prevent, reintroduced by a parser refusal.
        if idx < len(lines) and lines[idx].strip() and not any(
                _is_section(lines[idx], h) for h in _TRAILING_SECTIONS):
            raise CvParseError(
                f"{header}: unrecognised line {lines[idx].strip()!r} -- expected each "
                f"entry to start with one of {_TRAILING_MARKERS!r}")
        sections[header] = entries
    certificates = sections.get("CERTIFICATES", [])
    education = sections.get("EDUCATION", [])

    return CvDocument(name=name, contact=contact, profile=profile, work=work,
                       certificates=certificates, education=education)
