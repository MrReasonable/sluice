"""Choose what a reply may render: skills from the pool, bullets within budget (#364/#365/#368).

Pure. Its output is ONE Selection -- what the hard checks and the style tier inspect, what
the engine retains, assembles, audits and renders. A trimmed bullet or a dropped pick is
never checked, so it can never cost a retry or a lead (spec §2: over budget is trimmed and
reported, never refused).
"""
from dataclasses import dataclass

from sluice.core.tokens import find_term, tool_items


@dataclass(frozen=True)
class Selection:
    profile: str
    roles: dict            # slot id -> tuple[Bullet, ...], a key for EVERY slot
    skills: tuple          # kept picks, in the pool's spelling, in the model's order
    skills_dropped: tuple = ()
    bullets_trimmed: tuple = ()


def cv_name(entry):
    """A skill note's name on a CV: its Label:, else its title (D13 -- the filename of a
    note `skills add` created is a slug, so the typed name lives in Label:)."""
    label = str((entry.get("fields") or {}).get("Label") or "").strip()
    return label or str(entry.get("title") or "").strip()


def pool_kinds():
    from sluice.core.protocols import EVIDENCE_KINDS
    return tuple(k for k, spec in EVIDENCE_KINDS.items() if spec.names_in_skills_pool)


def named_entries(read_evidence):
    """Verified entries of every kind whose names a CV may list (D12), read through the
    Store's own `read_evidence`. Keyed on the flag, never on a kind's name, so the flag is
    what decides -- and the execution-derived test can catch a hard-wired kind."""
    return [e for kind in pool_kinds() for e in read_evidence(kind, verified_only=True)]


def _key(item):
    # A pick with a trailing full stop or stray whitespace is the same skill.
    return item.strip().rstrip(".").strip().casefold()


def build_pool(named, experience_entries, decoys=()):
    """The closed list a CV's SKILLS section may draw from: verified skill names first
    (so their spelling wins), then verified entries' Tools:, de-duplicated. An item a
    `fabrication_decoys` term matches never enters it: a ban beats the user's own list."""
    out, seen = [], set()
    candidates = [cv_name(e) for e in named]
    candidates += [t for e in experience_entries for t in tool_items(e)]
    for item in candidates:
        key = _key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        if any(find_term(item, d) for d in decoys or ()):
            continue
        out.append(item)
    return tuple(out)


def skills_requested(pool, skills_max):
    """Skills are asked for only when there is something to pick and a cap above zero."""
    return bool(pool) and skills_max != 0


def select(reply, slots, pool, skills_max):
    kept, dropped = [], []
    if skills_requested(pool, skills_max):
        index = {_key(p): p for p in pool}
        if reply.skills_malformed:
            dropped.append("the skills list was not a list of strings, so none was used")
        used = set()
        for pick in reply.skills:
            key = _key(pick)
            if key not in index:
                dropped.append(f"{pick!r}: not one of your skills")
            elif key in used:
                dropped.append(f"{pick!r}: listed twice")
            else:
                used.add(key)
                kept.append(index[key])
        # The cap applies AFTER off-pool and duplicate drops, so a rejected pick never
        # uses up a place the user allowed.
        if skills_max is not None and len(kept) > skills_max:
            dropped += [f"{extra!r}: over skills_max ({skills_max})" for extra in kept[skills_max:]]
            kept = kept[:skills_max]
    roles, trimmed = {}, []
    for slot in slots:
        bullets = tuple(reply.roles.get(slot.id, ()))
        keep = bullets if slot.budget is None else bullets[:slot.budget]
        if len(keep) < len(bullets):
            trimmed.append(f"{slot.id} ({slot.role.heading}): kept {len(keep)} of "
                           f"{len(bullets)}")
        roles[slot.id] = keep
    return Selection(reply.profile, roles, tuple(kept), tuple(dropped), tuple(trimmed))


def zero_bullet_findings(selection, slots):
    """A CV with no work bullets at all is refused -- unless every slot's budget is 0,
    which is a headings-only CV the user configured (D10). Over the SELECTION, so bullets
    written only into 0-budget slots do not count."""
    capable = [s for s in slots if s.budget != 0]
    if capable and not any(selection.roles.get(s.id) for s in capable):
        return ["REPLY: no bullets in any role that can carry them -- write bullets for the "
                "roles that list citable entries"]
    return []
