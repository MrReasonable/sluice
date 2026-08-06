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
# Anything ELSE that is header-shaped there (see `_is_header_shaped`) -- a section this
# parser does not model, or even a stray duplicate PROFILE/WORK EXPERIENCE -- is refused
# by name rather than silently absorbed as a company, which would only fail one line
# later as a bad meta line and hide the real problem behind a misleading one.
_TRAILING_SECTIONS = frozenset({"CERTIFICATES", "EDUCATION"})

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
_DASH = r"\s*[-–]\s*"
_DATE_RANGE_RE = re.compile(rf"^\d{{2}}/\d{{4}}{_DASH}(?:\d{{2}}/\d{{4}}|present)$")

# A citation the composer wrapped onto its own line (rather than trailing the bullet it
# belongs to) still needs to disappear -- see the "wrapped" case in
# test_parse_preserves_line_structure_while_stripping. Fullmatch (not the shared
# `_CITE_RE`'s search/sub semantics) is deliberate: this line must be NOTHING BUT a
# citation to be swallowed as a continuation, or a genuine new company/heading that
# happens to start with "[" would vanish instead of being parsed.
_CITE_ONLY_RE = re.compile(r"^\[[A-Za-z]{2}[0-9]+\]$")


def _is_header_shaped(line: str) -> bool:
    """True for a line the composer would only ever emit as a section heading.

    Every heading this grammar defines (PROFILE, WORK EXPERIENCE, CERTIFICATES,
    EDUCATION) is exact, shipped uppercase -- the fabrication gate itself matches section
    names case-sensitively in exact uppercase. A company name is never all-caps in this
    grammar's own fixtures ("Example Data Co", "Example Analytics Ltd"), so an all-caps,
    letters-and-spaces-only line in the WORK EXPERIENCE body is a section-header ATTEMPT:
    either one of the four modelled headers, or one this parser must refuse by name
    rather than silently absorb as a company (which would then fail one line later,
    misleadingly, as a bad meta line).
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
    while idx < len(lines) and lines[idx].strip() != "PROFILE":
        if lines[idx].strip():
            header_lines.append(lines[idx].strip())
        idx += 1
    if idx >= len(lines):
        raise CvParseError("no PROFILE section found")
    if not header_lines:
        raise CvParseError("no name heading found before PROFILE")
    idx += 1  # consume the "PROFILE" line itself
    name = _strip_cite(header_lines[-1])
    contact = _strip_cite("\n".join(header_lines[:-1]))

    # ---- PROFILE prose, up to WORK EXPERIENCE ----
    profile_lines = []
    while idx < len(lines) and lines[idx].strip() != "WORK EXPERIENCE":
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
        if stripped in _TRAILING_SECTIONS:
            # CERTIFICATES or EDUCATION -- stop here WITHOUT consuming the line, so the
            # section readers below start from it.
            break
        if _is_header_shaped(stripped):
            # All-caps, but not CERTIFICATES/EDUCATION -- e.g. a PUBLICATIONS section the
            # composer emitted, or a stray duplicate heading. Unmodelled either way.
            raise CvParseError(f"unmodelled section header {stripped!r}")

        # A line that is not blank, not a known section, and not header-shaped is a
        # company. The line immediately after it MUST be the meta line -- absorbing a
        # differently-shaped line here (a bullet, a bare title) would silently misassign
        # a field in a CV going to an employer under the user's name, which is the exact
        # harm spec section 0 exists to prevent.
        company = stripped
        idx += 1
        if idx >= len(lines):
            raise CvParseError(f"missing meta line after company {company!r}")
        meta_raw = lines[idx].strip()
        parts = [p.strip() for p in meta_raw.split("|")]
        if len(parts) != 3 or not _DATE_RANGE_RE.match(parts[0]):
            raise CvParseError(
                f"unparseable meta line after company {company!r}: {meta_raw!r}"
            )
        dates, location, title = parts
        idx += 1

        bullets: list[str] = []
        while idx < len(lines):
            line_stripped = lines[idx].strip()
            if line_stripped.startswith("- "):
                bullets.append(_strip_cite(line_stripped[2:]))
                idx += 1
                continue
            if _CITE_ONLY_RE.match(line_stripped):
                # A citation the composer wrapped onto its own line, trailing the bullet
                # just added. It contributes nothing new -- swallow it and keep reading
                # bullets, rather than letting it fall through as a bogus company/header.
                idx += 1
                continue
            break

        work.append(Role(
            company=_strip_cite(company), dates=_strip_cite(dates),
            location=_strip_cite(location), title=_strip_cite(title), bullets=bullets,
        ))

    # ---- CERTIFICATES (optional) ----
    certificates: list[str] = []
    if idx < len(lines) and lines[idx].strip() == "CERTIFICATES":
        idx += 1
        while idx < len(lines) and lines[idx].strip().startswith("- "):
            certificates.append(_strip_cite(lines[idx].strip()[2:]))
            idx += 1
        while idx < len(lines) and not lines[idx].strip():
            idx += 1

    # ---- EDUCATION (optional) ----
    education: list[str] = []
    if idx < len(lines) and lines[idx].strip() == "EDUCATION":
        idx += 1
        while idx < len(lines) and lines[idx].strip().startswith("- "):
            education.append(_strip_cite(lines[idx].strip()[2:]))
            idx += 1

    return CvDocument(name=name, contact=contact, profile=profile, work=work,
                       certificates=certificates, education=education)
