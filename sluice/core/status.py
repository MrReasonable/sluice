"""Canonical status vocabulary, shared by every sub-app.

Two lifecycles share one `status` frontmatter key. Triage OWNS the early states
(new -> shortlist/research/needs_review/dismiss) and may rewrite them. The
application tracker OWNS the later states (applied, phone_screen, ...); triage
must never touch a lead once it has entered that lifecycle. The single
exception into the application lifecycle is `shortlist -> applied`, made by apply (on
send) and by track (on a confirmation receipt); both go through `can_apply`.
`normalize` folds the historical drift (dismissed/dismiss, quoted/unquoted,
Researching/research) to one canonical token; an unrecognized value is passed
through untouched so a genuinely new state is never silently rewritten.
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
    """True iff the lead is in the only state `shortlist -> applied` may start from.
    Both apply (on send) and track (on a confirmation receipt, via `can_transition`)
    advance shortlist -> applied through this predicate; every other state (including
    every APPLICATION_OWNED state) is refused."""
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


def is_terminal(status: str) -> bool:
    """True iff `status` is an application TERMINAL -- a state never advanced out of.

    Public because the #1 lead layout derives its Archive set from it (`dismiss` plus every
    terminal) rather than hand-listing one: a terminal added to `_TERMINAL` later must archive
    automatically instead of silently staying in Active, which is the quiet-wrong-default this
    codebase engineers out. `can_advance` already reads `_TERMINAL` for the same vocabulary;
    this exposes the membership test without exposing the tuple.

    Normalizes first, like every other predicate here: a real note carries `status: "rejected"`
    with the quotes, so a raw `in` test would answer False for the value on disk.
    """
    return normalize(status) in _TERMINAL


def can_transition(current: str, target: str) -> bool:
    """Route a requested status change to the correct never-regress predicate.
    `applied` is reachable only via `can_apply` (shortlist -> applied); every other
    target is an on-ladder move governed by `can_advance`. This is the shared entry
    point `track confirm` uses, since it accepts an arbitrary `--to` target; the
    reconcile receipt branch calls `can_apply` directly because it already knows the
    target is `applied`. Routing lives here because status.py owns the ladder."""
    if normalize(target) == "applied":
        return can_apply(current)
    return can_advance(current, target)


def resolve_merge_status(statuses):
    """Order-independent verdict over a duplicate cluster's member statuses.
    Returns (winner, outcome), outcome one of "ok"|"conflict", winner the
    surviving status (None on conflict). See docs/.../read-path-dedup-design.md #2.

    There is no total order across the two lifecycles and clusters are size >= 2,
    so this reads the SET of distinct statuses rather than folding pairwise (a fold
    is order-dependent around the conflict sentinel). Application-owned dominates
    triage (you cannot un-apply); a terminal beside a LIVE application is a
    reject-then-reapply -> conflict, refused rather than silently archiving the live
    attempt (#23)."""
    s = {normalize(x) for x in statuses}
    if len(s) == 1:
        return next(iter(s)), "ok"          # all agree (incl. all-non-canonical)
    if s - CANONICAL:
        return None, "conflict"             # an unrankable status disagrees
    app = s & set(APPLICATION_OWNED)
    if app:
        # triage members are dominated; drop them BEFORE any triage-vs-triage judging
        term = app & set(_TERMINAL)
        live = app & set(_LADDER)
        if len(term) >= 2:
            return None, "conflict"          # two different terminals
        if term and live:
            return None, "conflict"          # reject-then-reapply (round-2 inv-r2-001)
        if term:
            return next(iter(term)), "ok"    # the sole terminal (no live) wins
        return max(live, key=_RANK.__getitem__), "ok"   # all live -> highest ladder rank
    nonnew = (s & set(TRIAGE_OWNED)) - {"new"}
    if len(nonnew) == 1:
        return next(iter(nonnew)), "ok"
    return None, "conflict"                  # two different non-new triage states
