"""The CV Layout note: its validation, and how evidence entries map onto its roles.

Pure. The store reads the note's YAML and hands the mapping here
(core/vault.py::Vault.read_cv_layout), so `doctor` and the engine share ONE reading of
what a layout means.
"""
import difflib
import re
from dataclasses import dataclass

from sluice.core.names import fold_note_name
from sluice.core.protocols import SECTION_HEADINGS, CvLayout, LayoutError, LayoutRole
from sluice.core.safeout import is_control

ROLE_KEYS = ("heading", "from", "to", "location", "title", "employers", "bullets_max")
TOP_KEYS = ("roles", "skills_max", "certificates", "education", "any_role", "omitted")
_LIST_KEYS = ("certificates", "education", "any_role", "omitted")
# The placeholders docs/CONFIGURATION.md's example uses. A value still carrying one is a
# copy nobody finished editing, and refusing it keeps a literal "<title>" off a CV sent
# under the user's name.
PLACEHOLDERS = ("<MM/YYYY>", "<MM/YYYY or present>", "<location>", "<title>", "<n>",
                "<company>", "<certificate>", "<institution, dates | qualification>")
# [0-9], not \d: \d admits every Unicode decimal digit, which int() then reads as a date.
_DATE_RE = re.compile(r"(0[1-9]|1[0-2])/([0-9]{4})")
_HEADINGS = frozenset(h.casefold() for h in SECTION_HEADINGS)


def _echoable(key):
    """Whether a user-typed key may appear in a problem string."""
    return isinstance(key, str) and len(key) <= 40 and not any(is_control(c) for c in key)


def fold_employer(name):
    """One employer name, folded for matching: the repo's one name fold
    (core/names.py::fold_note_name), whitespace collapsed (so a non-breaking space or a
    doubled space still matches). The fold has ONE home -- never copy it."""
    return " ".join(fold_note_name(name).split())


def parse_layout(mapping):
    """A validated CvLayout, or LayoutError listing every problem in the mapping."""
    if not isinstance(mapping, dict):
        raise LayoutError([
            "frontmatter: expected the layout's keys (roles:, ...) in the note's YAML "
            f"frontmatter between --- lines, found {type(mapping).__name__}"])
    problems = []
    for key in mapping:
        if not isinstance(key, str) or key in TOP_KEYS:
            continue
        if key in ROLE_KEYS:
            problems.append(f"{key}: belongs inside a role, under roles:")
            continue
        # A high cutoff: `description:` and `notes:` are ordinary note metadata and must
        # stay ignored, while `skill_max:` is a typo that would silently lift a cap.
        near = difflib.get_close_matches(key, TOP_KEYS, n=1, cutoff=0.8)
        if near and _echoable(key):
            problems.append(f"{key}: unknown key -- did you mean {near[0]}?")
        elif near:
            problems.append(f"top level: an unknown key resembling {near[0]}")
    roles = []
    raw_roles = mapping.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        problems.append("roles: required -- a list with one entry per heading on the CV")
    else:
        for i, raw in enumerate(raw_roles):
            role = _role(f"roles[{i}]", raw, problems)
            if role is not None:
                roles.append(role)
    skills_max = _cap("skills_max", mapping.get("skills_max"), problems)
    lists = {k: _str_list(k, mapping.get(k), problems, required=False) for k in _LIST_KEYS}
    _contradictions(roles, lists, problems)
    if problems:
        raise LayoutError(problems)
    return CvLayout(roles=tuple(roles), skills_max=skills_max, **lists)


def _text(path, value, problems, *, required, meta=False):
    if value is None:
        if required:
            problems.append(f"{path}: required")
        return ""
    if not isinstance(value, str):
        problems.append(f"{path}: must be text, found {type(value).__name__}")
        return ""
    if required and not value.strip():
        problems.append(f"{path}: must not be blank")
        return ""
    # A layout string is written into lines a `script` renderer re-reads, so it must not be
    # able to forge a line any more than model text can (#364 spec §4.1).
    if any(is_control(c) for c in value):
        problems.append(f"{path}: contains a line break or control character")
        return ""
    hit = next((p for p in PLACEHOLDERS if p in value), None)
    if hit:
        problems.append(f"{path}: still holds the example placeholder {hit}")
        return ""
    if meta and "|" in value:
        problems.append(f"{path}: '|' separates the CV's meta-line fields, so it cannot "
                        "appear here")
        return ""
    if meta and value.strip().casefold() in _HEADINGS:
        problems.append(f"{path}: equals a CV section heading")
        return ""
    return value.strip()


def _date(path, value, problems, *, allow_present):
    text = _text(path, value, problems, required=True)
    if not text:
        return None
    if allow_present and text.casefold() == "present":
        return "present"
    match = _DATE_RE.fullmatch(text)
    if not match or int(match.group(2)) == 0:
        # The value is NOT echoed: this message reaches `doctor`, whose rows go to MCP
        # clients whole, and a layout date is employment history. The path names the field.
        problems.append(f"{path}: is not MM/YYYY"
                        + (" or present" if allow_present else ""))
        return None
    return text


def _order(date):
    if date == "present":
        return (9999, 99)
    month, year = date.split("/")
    return (int(year), int(month))


def _cap(path, value, problems):
    # bool BEFORE int: PyYAML reads `yes` as True, and True is an int.
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append(f"{path}: must be a whole number of 0 or more (0 means none; leave "
                        "the key out for no cap)")
        return None
    return value


def _str_list(path, value, problems, *, required):
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.append(f"{path}: must be a list -- put each item on its own '- ' line")
        return ()
    if required and not value:
        problems.append(f"{path}: must not be empty")
        return ()
    out = []
    for j, item in enumerate(value):
        text = _text(f"{path}[{j}]", item, problems, required=True)
        if text:
            out.append(text)
    return tuple(out)


def _role(path, raw, problems):
    if not isinstance(raw, dict):
        problems.append(f"{path}: must be a mapping (heading:, from:, to:, ...)")
        return None
    for key in raw:
        if key not in ROLE_KEYS:
            near = difflib.get_close_matches(str(key), ROLE_KEYS, n=1, cutoff=0.6)
            # The key is user-typed and this message reaches `doctor` and MCP clients whole,
            # so it is echoed only when it is a near miss of a real key (the top-level
            # branch's rule) and carries no control character or absurd length.
            if near and _echoable(key):
                problems.append(f"{path}.{key}: unknown key -- did you mean {near[0]}?")
            else:
                problems.append(f"{path}: an unknown key -- a role's keys are "
                                + ", ".join(ROLE_KEYS))
    heading = _text(f"{path}.heading", raw.get("heading"), problems, required=True, meta=True)
    start = _date(f"{path}.from", raw.get("from"), problems, allow_present=False)
    end = _date(f"{path}.to", raw.get("to"), problems, allow_present=True)
    if start and end and _order(start) > _order(end):
        problems.append(f"{path}: from is after to")
    location = _text(f"{path}.location", raw.get("location"), problems, required=False,
                     meta=True)
    title = _text(f"{path}.title", raw.get("title"), problems, required=False, meta=True)
    if raw.get("employers") is None:
        employers = (heading,) if heading else ()
    else:
        employers = _str_list(f"{path}.employers", raw["employers"], problems, required=True)
    bullets_max = _cap(f"{path}.bullets_max", raw.get("bullets_max"), problems)
    return LayoutRole(heading=heading, start=start or "", end=end or "", location=location,
                      title=title, employers=employers, bullets_max=bullets_max)


def _contradictions(roles, lists, problems):
    """A company both omitted and placed is a layout that says two opposite things. One
    under both `any_role:` and a role's `employers` says two things too, and since a role
    match outranks `any_role:` (see `place`) the any_role listing would do nothing at all --
    named rather than left silently inert."""
    in_roles = set()
    for role in roles:
        in_roles |= {fold_employer(e) for e in role.employers}
    for j, company in enumerate(lists["any_role"]):
        if fold_employer(company) in in_roles:
            problems.append(f"any_role[{j}]: also listed under a role's employers, which "
                            "wins -- remove it from one of the two")
    if not lists["omitted"]:
        return
    placed = in_roles | {fold_employer(c) for c in lists["any_role"]}
    for j, company in enumerate(lists["omitted"]):
        if fold_employer(company) in placed:
            problems.append(f"omitted[{j}]: also listed under any_role or a role's employers")


_PARTS_RE = re.compile(r"[,;/]")


def employers_of(company):
    """The folded employers an entry's `Company:` names: the whole value AND each `,` `;` `/`
    part. Taking the whole value too keeps an employer whose own name contains a comma
    matchable; the ROLE side is never split (see `place`)."""
    raw = company if isinstance(company, str) else ""
    folded = (fold_employer(p) for p in [raw, *_PARTS_RE.split(raw)])
    return frozenset(f for f in folded if f)


@dataclass(frozen=True)
class Placement:
    reason: str        # "role" | "any_role" | "omitted" | "blank" | "unmatched"
    roles: frozenset   # indexes into layout.roles this entry may be cited under


def place(layout, company):
    """Where one entry may be cited (#364 D6). A blank or unmatched company is citable
    NOWHERE, so the model can never move work under an employer the user did not put it
    under; `any_role:` is the explicit way to make an entry fit every role.

    Precedence, when `Company:`'s parts match more than one list: a role match, then
    `omitted:`, then `any_role:`. `any_role:` is the WIDEST grant, so it applies only when
    no part says anything narrower: tested first, `Example Ghost / Freelance` with the first
    part omitted and the second under `any_role:` would be citable under every role."""
    parts = employers_of(company)
    if not parts:
        return Placement("blank", frozenset())
    # A role's own heading stands in when it lists no employers: parse_layout always fills
    # them, but a LayoutRole built directly must not silently match nothing.
    matched = frozenset(i for i, role in enumerate(layout.roles)
                        if parts & {fold_employer(e) for e in role.employers or (role.heading,)})
    if matched:
        return Placement("role", matched)
    if parts & {fold_employer(c) for c in layout.omitted}:
        return Placement("omitted", frozenset())
    if parts & {fold_employer(c) for c in layout.any_role}:
        return Placement("any_role", frozenset(range(len(layout.roles))))
    return Placement("unmatched", frozenset())


@dataclass(frozen=True)
class Slot:
    id: str              # "R1", "R2", ... in layout order
    role: object         # LayoutRole
    eligible: tuple      # entry ids citable here, in bundle order
    budget: object       # effective cap: None = no cap; 0 = no bullets


def build_slots(layout, entries):
    """The slot table, built ONCE per lead and handed to every stage that needs it, so the
    prompt can never offer a cite the gate then refuses. A slot with no eligible entry has
    an effective budget of 0: shown as heading-only, anything written there trimmed, so it
    never costs a retry."""
    places = {e["id"]: place(layout, e.get("company", "")) for e in entries}
    slots = []
    for i, role in enumerate(layout.roles):
        eligible = tuple(e["id"] for e in entries if i in places[e["id"]].roles)
        slots.append(Slot(f"R{i + 1}", role, eligible, role.bullets_max if eligible else 0))
    return tuple(slots)


def asks_for_bullets(roles):
    """Whether any of these LayoutRoles asks for bullets: a `bullets_max` other than 0
    (absent means no cap). False only for a headings-only CV (#364 spec §5.2, D10), which cites
    nothing -- so it needs no citable entry, and no slot that can carry one. The ONE home
    of that predicate: `cv run`'s prerequisite check and `doctor` both decide on it, and a
    second spelling is how the two came to disagree."""
    return any(role.bullets_max != 0 for role in roles)


def no_citable_slot(slots):
    """True when no slot can carry a bullet although some role asks for bullets -- a
    misconfiguration (say, legal-suffix company names the headings do not match) that
    would otherwise spend a dossier fetch and two compose calls per lead before failing."""
    return (all(s.budget == 0 for s in slots)
            and asks_for_bullets(s.role for s in slots))


def placement_counts(layout, entries):
    """How many of these entries fall under each placement reason `place` can give.
    Every reason is present in the result, with 0 when none matched."""
    counts = dict.fromkeys(("role", "any_role", "omitted", "blank", "unmatched"), 0)
    for e in entries:
        counts[place(layout, e.get("company", "")).reason] += 1
    return counts


def layout_strings(layout):
    """Every non-empty string the layout shows the composer, each its own item. A PHRASE
    search must run over these one at a time, never over their join: core/tokens.py's
    find_term does not break a phrase at a newline, so a two-word term would match the last
    word of one heading and the first word of the next -- text the user never wrote."""
    parts = []
    for role in layout.roles:
        parts += [role.heading, role.location, role.title, *role.employers]
    parts += [*layout.certificates, *layout.education, *layout.any_role, *layout.omitted]
    return tuple(p for p in parts if p)


def layout_text(layout):
    """`layout_strings`, one per line: what the term check must recognise as the user's own
    words. Safe for a WORD set (cv/bundle.py::term_vocabulary), which no join can change;
    a phrase search takes `layout_strings` instead."""
    return "\n".join(layout_strings(layout))
