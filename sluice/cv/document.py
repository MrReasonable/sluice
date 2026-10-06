"""Build the CV document from the vault and a selection, and write its text forms
(#364/#365/#368 spec §7).

Pure. Every vault-sourced field (name, contact, headings, dates, locations, titles,
certificates, education) comes from the CV Layout and the Candidate Profile; the model
supplies only the profile, the bullets and its skill picks -- already checked, and kept in
the pool's spelling, by the time they reach here.
"""
from dataclasses import dataclass

from sluice.core.candidate import contact_block, full_name
from sluice.core.protocols import SECTION_HEADINGS, CvDocument, Role

__all__ = ["SECTION_HEADINGS", "AssembledCv", "assemble", "audit_text", "format_dates",
           "model_lines", "to_text"]


@dataclass(frozen=True)
class AssembledCv:
    document: CvDocument
    cites: tuple           # cites[r][b]: entry ids behind bullet b of work role r


def format_dates(role):
    return f"{role.start}–{role.end}"


def assemble(layout, slots, selection, candidate):
    """One Role per layout role, in layout order, each filled from ITS OWN slot by id --
    never by zipping the reply's slots against the layout, whose key order the model
    chooses (a zip would put one employer's work under another's heading)."""
    work, cites = [], []
    for slot in slots:
        bullets = selection.roles.get(slot.id, ())
        work.append(Role(company=slot.role.heading, dates=format_dates(slot.role),
                         location=slot.role.location, title=slot.role.title,
                         bullets=[b.text for b in bullets]))
        cites.append(tuple(b.cites for b in bullets))
    document = CvDocument(
        # Upper-cased, keeping today's output: the composer was asked for the name in
        # capitals and the old parser kept that line as the PDF headline.
        name=full_name(candidate).upper(), contact=contact_block(candidate),
        profile=selection.profile, work=work, skills=list(selection.skills),
        certificates=list(layout.certificates), education=list(layout.education))
    return AssembledCv(document, tuple(cites))


def to_text(document, *, cites=None):
    """The canonical CV text: what a `script` renderer receives (without cites) and what
    cv.rendered.md records (with them). The meta line is POSITIONAL -- always
    `dates | location | title`, an empty field written as empty -- so a script reading
    fields by position never reads a location as a title."""
    if cites is not None:
        # A caller contract violation should fail by name, not as an IndexError mid-write.
        shape = [len(r.bullets) for r in document.work]
        if [len(c) for c in cites] != shape:
            raise ValueError(f"cites do not match the document: expected bullet counts "
                             f"{shape} per work role, got {[len(c) for c in cites]}")
    lines = [ln for ln in document.contact.splitlines() if ln.strip()]
    lines += ["", document.name, "", SECTION_HEADINGS[0], document.profile, "",
              SECTION_HEADINGS[1], ""]
    for r, role in enumerate(document.work):
        lines += [role.company, f"{role.dates} | {role.location} | {role.title}"]
        for b, text in enumerate(role.bullets):
            suffix = "".join(f" [{c}]" for c in cites[r][b]) if cites is not None else ""
            lines.append(f"- {text}{suffix}")
        lines.append("")
    for heading, items in ((SECTION_HEADINGS[2], document.certificates),
                           (SECTION_HEADINGS[3], document.education),
                           (SECTION_HEADINGS[4], document.skills)):
        if items:
            lines += [heading, *[f"- {item}" for item in items], ""]
    # Exactly one trailing newline whatever sections exist: a script renderer needs a stable
    # ending, and the golden (the byte-for-byte evidence) ends this way.
    return "\n".join(lines).rstrip("\n") + "\n"


def audit_text(selection, slots):
    """What the advisory audit reads: the text the MODEL wrote, each bullet under its role
    heading with its cites. Never a date, location, title, certificate, education line or
    skill: those are the user's own vault data, the auditor has no truth for them, and
    auditing them would hold almost every CV (spec §6.4)."""
    lines = [SECTION_HEADINGS[0], selection.profile]
    for slot in slots:
        bullets = selection.roles.get(slot.id, ())
        if bullets:
            lines += ["", slot.role.heading]
            lines += [f"- {b.text}" + "".join(f" [{c}]" for c in b.cites) for b in bullets]
    return "\n".join(lines) + "\n"


def model_lines(selection, slots):
    """The profile and every kept bullet as (n, text) pairs: what the style tier reads."""
    texts = [selection.profile]
    for slot in slots:
        texts += [b.text for b in selection.roles.get(slot.id, ())]
    return list(enumerate(texts, 1))
