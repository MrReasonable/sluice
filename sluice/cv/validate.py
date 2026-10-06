# sluice/cv/validate.py
"""The deterministic checks over a SELECTION (#364/#365/#368 spec §6.1): `entry_facts` derives
what each bundle entry licenses (its figures, its `Tools:`, where the CV Layout places it) and
`check_selection` holds the profile and the kept bullets to those facts. Pure. A non-empty
result HARD-blocks rendering. Every kept bullet must cite a bundle entry the slot may cite,
every figure in it must appear in a cited entry, and a tool it names must be licensed by one.

`decoys` (the `cv.fabrication_decoys` config list) is supplied by the caller rather than
hardcoded here: an empty list skips the known-hallucination-string check. The text the model
did NOT write (the vault's own, which is the user's) is never inspected."""
from dataclasses import dataclass

from sluice.core.layout import place
from sluice.core.tokens import figures, find_term, tool_items


@dataclass(frozen=True)
class EntryFacts:
    """What one bundle entry licenses (#364/#365/#368 spec §6.1): its figures, normalised
    to ASCII from its OWN source lines; its Tools: items; the title and body text that may
    license a tool it never listed; and where the CV Layout places it. The title and body are
    kept APART in `texts`: core/tokens.py::find_term does not break a phrase at a newline, so
    their join let a title ending in one word and a body opening with the next license a
    two-word tool the entry never names."""
    figures: frozenset
    tools: tuple
    texts: tuple
    placement: str        # core/layout.py::Placement.reason
    role_headings: tuple  # headings of the roles it may be cited under


def entry_facts(bundle, layout):
    """Map each bundle entry id to the `EntryFacts` it licenses. Raises `ValueError` on a
    duplicate id, since each id keys its own allowlist."""
    from sluice.cv.bundle import _entry_block
    out = {}
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in out:
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]          # drop the leading `[{eid}]`
        where = place(layout, e.get("company", ""))
        out[eid] = EntryFacts(
            figures=figures("\n".join(block)), tools=tuple(tool_items(e)),
            texts=(e.get("title", ""), e.get("body", "")), placement=where.reason,
            role_headings=tuple(layout.roles[i].heading for i in sorted(where.roles)))
    return out


def _spans(text, tools):
    return [span for tool in tools for occurrence in find_term(text, tool)
            for span in occurrence]


def _licenses(fact, tool):
    # Declared (in any case: the declaration is the user's), or named by the entry's own
    # title or body under the trigger's case rule -- so `go-live` never licenses `Go`, while
    # a sentence-initial `Coaching` licenses a declared `coaching`.
    return (tool.casefold() in {t.casefold() for t in fact.tools}
            or any(find_term(text, tool, case_sensitive=True, lower_accepts_capital=True)
                   for text in fact.texts))


def _belongs(fact):
    if fact.placement in ("role", "any_role"):
        return f"belongs to {', '.join(fact.role_headings)}"
    if fact.placement == "blank":
        return "has no company"
    return "is not on your CV"


def check_selection(selection, slots, facts, *, decoys=()):
    """The HARD findings for one selection. Reads only the text the model wrote -- the
    profile and the kept bullets -- never vault text, which is the user's (#364 spec §2). The em
    dash and double-hyphen rows (cv/slop.py::check_hard) run beside this in the engine, over
    the same texts, and report as `slop` the way they always have.

    ONE accumulator, extended only by `v.append(...)` with the category spelled inline:
    tests/test_docs_claims.py derives the gate's categories from exactly that shape, and
    docs/TROUBLESHOOTING.md must explain every one."""
    v = []
    vocabulary = sorted({t for f in facts.values() for t in f.tools})
    every_tool = [t for f in facts.values() for t in f.tools]
    every_figure = frozenset().union(*(f.figures for f in facts.values())) if facts else frozenset()
    profile = selection.profile
    for decoy in decoys:
        if find_term(profile, decoy):
            v.append(f"FABRICATED: contains '{decoy}'")
    # The profile has no cites, so any entry's figures license it, and only entries' TOOLS
    # (never a Skills Inventory name) blank a digit inside a name (#165).
    for n in sorted(figures(profile, remove=_spans(profile, every_tool)) - every_figure):
        v.append(f"INVENTED PROFILE METRIC {n} not in your evidence: {profile.strip()[:50]}")
    for slot in slots:
        for i, bullet in enumerate(selection.roles.get(slot.id, ()), 1):
            where, snip = f"{slot.id} bullet {i}", bullet.text.strip()[:50]
            if not bullet.cites:
                v.append(f"UNCITED BULLET: {where}: {bullet.text.strip()[:60]}")
                continue
            bad = [c for c in bullet.cites if c not in facts]
            if bad:
                v.append(f"BAD CITATION {bad}: not bundle entries - {where}: {snip}")
                continue
            wrong = [c for c in bullet.cites if c not in slot.eligible]
            for c in wrong:
                v.append(f"WRONG EMPLOYER: {where} cites {c}, which {_belongs(facts[c])} "
                         f"- {snip}")
            if wrong:
                continue
            cited = [facts[c] for c in bullet.cites]
            licensed = frozenset().union(*(f.figures for f in cited))
            invented = sorted(figures(bullet.text,
                                      remove=_spans(bullet.text, [t for f in cited for t in f.tools]))
                              - licensed)
            if invented:
                v.append(f"INVENTED METRIC {invented} not in {list(bullet.cites)}: {snip}")
            for tool in vocabulary:
                if (find_term(bullet.text, tool, case_sensitive=True)
                        and not any(_licenses(f, tool) for f in cited)):
                    v.append(f"MISATTRIBUTED TOOL {tool!r} not in {list(bullet.cites)}: {snip}")
            for decoy in decoys:
                if find_term(bullet.text, decoy):
                    v.append(f"FABRICATED: contains '{decoy}'")
    return v
