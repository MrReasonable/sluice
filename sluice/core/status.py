"""Canonical status vocabulary, shared by every sub-app.

Two lifecycles share one `status` frontmatter key. Triage OWNS the early states
(new -> shortlist/research/needs_review/dismiss) and may rewrite them. The
application tracker OWNS the later states (applied, phone_screen, ...); triage
must never touch a lead once it has entered that lifecycle. `normalize` folds the
historical drift (dismissed/dismiss, quoted/unquoted, Researching/research) to one
canonical token; an unrecognized value is passed through untouched so a genuinely
new state is never silently rewritten.
"""

TRIAGE_OWNED = ("new", "shortlist", "research", "needs_review", "dismiss")
APPLICATION_OWNED = (
    "applied", "phone_screen", "interview", "offer",
    "rejected", "accepted", "withdrawn",
)
CANONICAL = frozenset(TRIAGE_OWNED) | frozenset(APPLICATION_OWNED)

# Drift seen in real-world vault data plus obvious spellings -> canonical.
_ALIASES = {
    "dismissed": "dismiss",
    "researching": "research",
    "shortlisted": "shortlist",
    "needs review": "needs_review",
    "phone screen": "phone_screen",
    "phonescreen": "phone_screen",
    "interviewing": "interview",
}


def normalize(raw: str) -> str:
    t = (raw or "").strip().strip('"').strip("'").strip().lower()
    return _ALIASES.get(t, t)


def is_application_owned(status: str) -> bool:
    return normalize(status) in APPLICATION_OWNED


def is_canonical(status: str) -> bool:
    return normalize(status) in CANONICAL


def can_apply(status: str) -> bool:
    """True iff the lead is in the only state apply may transition from.
    shortlist -> applied is the sole allowed apply transition; every other
    state (including every APPLICATION_OWNED state) is refused."""
    return normalize(status) == "shortlist"


# Application ladder: forward order. Terminals are reachable from any live stage
# but are never advanced out of.
_LADDER = ("applied", "phone_screen", "interview", "offer")
_TERMINAL = ("rejected", "accepted", "withdrawn")
_RANK = {s: i for i, s in enumerate(_LADDER)}


def can_advance(current: str, target: str) -> bool:
    """True iff `target` is a legal forward/terminal move from `current`, both in
    APPLICATION_OWNED. Forward on the ladder, or non-terminal live -> terminal.
    Refuses backward moves, moves out of a terminal, and anything touching a
    non-application (triage-owned) state."""
    c, t = normalize(current), normalize(target)
    if c not in APPLICATION_OWNED or t not in APPLICATION_OWNED:
        return False
    if c in _TERMINAL:
        return False
    if t in _TERMINAL:
        return True
    return _RANK.get(t, -1) > _RANK.get(c, -1)
