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
# the gate passed gets refused and binned here instead. Scoped to WORK bullets only,
# matching where the cited gate check itself applies (`in_work`) -- CERTIFICATES and
# EDUCATION entries are composed with a hyphen only, per compose.py's own format
# contract, so widening this to those sections would not be modelling anything real.
_BULLET_MARKERS = ("-", "•", "*")

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
# `_CITE_RE`'s search/sub semantics) is deliberate: this line must be NOTHING BUT one or
# more citations to be swallowed as a continuation, or a genuine new company/heading
# that happens to start with "[" would vanish instead of being parsed. ONE-OR-MORE
# (not exactly one): cv/compose.py:11 explicitly permits several citations per bullet
# ("several allowed: [id] [id]"), and a multi-citation bullet is exactly as likely to
# get wrapped onto its own line as a single-citation one.
_CITE_ONLY_RE = re.compile(r"^(?:\[[A-Za-z]{2}[0-9]+\]\s*)+$")


def _is_header_shaped(line: str) -> bool:
    """True for a line that LOOKS LIKE a section heading: exact, shipped uppercase, the
    same way every heading this grammar defines (PROFILE, WORK EXPERIENCE, CERTIFICATES,
    EDUCATION) is -- the fabrication gate itself matches section names case-sensitively
    in exact uppercase.

    This is NOT used to reject a candidate company outright: a real acronym employer
    (IBM, NASA, HSBC, BBC) is all-caps too, and refusing it on casing alone would waste
    the engine's one retry on a CV that was never malformed -- there is nothing for a
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

        # A line that is not blank and not a known trailing section is a CANDIDATE
        # company -- regardless of casing. Whether it really is one is decided by what
        # follows it, not by how it looks: an all-caps real employer (IBM, NASA, HSBC)
        # must parse exactly like any other when a valid meta line follows, and casing
        # is used only below, to pick an error message, once that check has failed.
        company = stripped
        idx += 1
        meta_raw = lines[idx].strip() if idx < len(lines) else None
        parts = [p.strip() for p in meta_raw.split("|")] if meta_raw is not None else []
        valid_meta = (meta_raw is not None and len(parts) == 3
                      and _DATE_RANGE_RE.match(parts[0]) and parts[1] and parts[2])
        if not valid_meta:
            if _is_header_shaped(company):
                # All-caps, and what follows it doesn't look like a meta line either --
                # almost certainly a section header this parser does not model (e.g.
                # PUBLICATIONS) rather than a company whose meta line merely went wrong.
                raise CvParseError(f"unmodelled section header {company!r}")
            # Deliberately does not echo `company` here: it is exactly the untrusted
            # candidate text the branch above is refusing, and a message that echoes it
            # back would let a mutation that deletes that branch still satisfy a
            # `pytest.raises(match=...)` on the header's own text via THIS unrelated
            # path -- measured 2026-08-06, see the mutation witness in the task report.
            # The two refusals must stay distinguishable by message, not just both
            # truthy.
            if meta_raw is None:
                raise CvParseError("missing meta line: WORK EXPERIENCE entry has no "
                                    "line after its company")
            raise CvParseError(f"unparseable meta line: {meta_raw!r}")
        dates, location, title = parts
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
