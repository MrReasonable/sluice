"""The normalized lead - the unit every sub-app passes around."""
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, fields
from datetime import date

from sluice.core import status as _status

# The verdict vocabulary, shared with #5's `same_opportunity`. Strings, not an enum -- core/status.py
# sets that convention. DIFFERENT is the ONLY verdict a caller may split on.
SAME = "same"
DIFFERENT = "different"
UNKNOWN = "unknown"


def _norm_url(u: str) -> str:
    """Canonicalize a URL for dedup by dropping only the #fragment, keeping the
    query, case, and trailing slash. Several boards encode the job id in the
    query (eighty_k `?jobId=`, indeed `?jk=`), so stripping it collapses distinct
    jobs into one; and keeping the full link matches the format already stored in
    the legacy seen.db, so the cutover dedups cleanly instead of re-surfacing."""
    return u.strip().split("#")[0]


def _norm_location(s: str) -> str:
    """Canonicalize a location for comparison: NFKD-fold, casefold, drop combining marks, then
    collapse runs of non-word characters to a single space. Blank-ish input becomes "", which is
    what lets an absent location abstain rather than read as evidence (`bool("   ")` is True).

    Both halves are load-bearing, for DIFFERENT reasons -- conflating them is how the guard test
    for this goes inert. The NFKD fold makes 'Zürich' and 'Zurich' one token; without it they share
    no token and SPLIT. The unicode-aware `\\W` (not `[^a-z0-9]`) keeps letters that NFKD cannot
    fold whole: 'ø' is a distinct letter, not an accented 'o', so `[^a-z0-9]` would shred
    'københavn' into 'k benhavn'. Neither witnesses the other; see tests/test_leads_location.py.

    NFKD runs BEFORE casefold, and that ordering is itself load-bearing: 663 codepoints decompose
    to an uppercase letter that a casefold which already ran can never reach ('№' -> 'No'). Folding
    in the wrong order leaves '№5' as 'No5', which shares no token with 'no5' and SPLITS -- the one
    verdict #5 acts on. Cheap to get backwards, and neither fold test above witnesses it.
    """
    s = unicodedata.normalize("NFKD", s).casefold()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\W+", " ", s).strip()


def _compare_locations(a: str, b: str, noise=frozenset()) -> str:
    """Compare two locations by token OVERLAP. Returns DIFFERENT only on positive evidence of
    difference -- disjoint, non-empty token sets. Overlapping evidence is SAME; absent evidence is
    UNKNOWN. **DIFFERENT is the only verdict #5 acts on**, so UNKNOWN and SAME are both safe to be
    wrong about and DIFFERENT is not: a wrong DIFFERENT manufactures a second note for an ordinary
    cross-board re-post, while a wrong SAME merges, which is what today already does.

    Overlap, not subset or containment, and that is measured rather than chosen: boards decorate a
    city differently on every re-post ('London', 'London EC4Y', 'London ∙ Choose area'), so neither
    side is usually a subset of the other and token-subset splits 15 of 21 real same-city pairs.
    Every rendering shares the CITY token; the rest is decoration. Overlap keys on the signal.
    See docs/superpowers/specs/2026-07-16-location-identity-evidence.py to re-derive the numbers.

    `noise` is vocabulary that decorates a location without locating it. It is fed through
    _norm_location and TOKENIZED rather than used raw, because raw subtraction gives a knob that
    silently does nothing: {'UK'} never matches the token 'uk' (case), and {'United Kingdom'} equals
    no single token (arity). A bare str raises rather than iterating into characters (shape).
    """
    if isinstance(noise, str):
        raise TypeError(f"noise must be a set of words, not a str: {noise!r}")
    drop = {tok for w in noise for tok in _norm_location(w).split()}
    ta = set(_norm_location(a).split()) - drop
    tb = set(_norm_location(b).split()) - drop
    # Emptiness is checked AFTER subtraction, deliberately: noise can empty a side, and that must
    # abstain. Hoisting this check above the subtraction makes _compare_locations('Remote',
    # 'Remote', {'remote'}) return DIFFERENT -- splitting two identical locations.
    if not ta or not tb:
        return UNKNOWN
    return SAME if ta & tb else DIFFERENT


@dataclass(frozen=True)
class StalenessPolicy:
    """The staleness rule in force for ONE invocation (#9), built once in `Sluice` and
    passed whole to cv, apply and expire so none of them can disagree about it.

    The default abstains. A call site that forgets to pass a policy gets ttl_days=0 and
    therefore never marks anything stale -- fail-safe is the only acceptable direction
    here, because the failure this guards is binning a lead the user still wants.

    Pure: no clock, no store, no config. `today` is handed in, which is what makes every
    consumer's staleness test deterministic.
    """
    ttl_days: int = 0
    today: str = ""
    include_stale: bool = False

    def __post_init__(self):
        # `Sluice`'s `today` collaborator is a zero-arg CALLABLE -- VaultSink does
        # `today or _today` and then CALLS it (ingest/sink.py:26,31), and every test
        # injects `lambda: "2026-07-07"`. So the tempting `today=self._today` binds a
        # FUNCTION here, which reaches date.fromisoformat(<function>) -> TypeError. The
        # ValueError guard in days() does NOT catch that, so the designed fail-safe
        # abstain would become a traceback on `cv run`/`apply prep`/`leads expire`.
        # Fail loudly at construction naming the fix instead (the house rule) -- and
        # not by silently abstaining, which would hide a programming error as a feature
        # that quietly does nothing.
        if not isinstance(self.today, str):
            raise TypeError(
                "StalenessPolicy.today must be an ISO date string, got "
                f"{type(self.today).__name__}: Sluice's `today` is a zero-arg callable, "
                "so call it (`clock()`) rather than binding it")

    def days(self, last_seen: str) -> int | None:
        """Whole days from `last_seen` to `today`; None when EITHER is absent or
        unparseable. `today` is parsed under the same guard as `last_seen`: a bad
        injected clock must abstain for the same reason bad stored data must.

        No quote-stripping is claimed here. `_fm_dict` (core/vault.py) already strips
        surrounding quotes before any caller sees `note.fm["last_seen"]`, so a strip in
        this layer would be a no-op carrying one store's frontmatter convention into a
        pure value object. `.strip()` is whitespace only.
        """
        try:
            then = date.fromisoformat((last_seen or "").strip())
            now = date.fromisoformat((self.today or "").strip())
        except ValueError:
            return None
        return (now - then).days

    def is_stale(self, last_seen: str) -> bool:
        """Strictly older than the TTL -- a lead last seen exactly `ttl_days` ago is not
        yet stale. `<= 0` rather than `== 0` so a hand-built negative abstains instead of
        expiring the entire vault; that clause is what makes an unconfigured install
        expire nothing at all."""
        if self.ttl_days <= 0:
            return False
        d = self.days(last_seen)
        return d is not None and d > self.ttl_days

    def blocks(self, last_seen: str) -> bool:
        """The single question the cv and apply gates ask, so neither can implement the
        `--include-stale` override differently."""
        return self.is_stale(last_seen) and not self.include_stale


@dataclass
class Lead:
    source: str
    search: str
    title: str
    company: str = ""
    location: str = ""
    salary: str = ""
    url: str = ""
    job_type: str = ""          # "contract" | "permanent" | ""
    first_seen: str = ""         # ISO date; set when the lead is first created
    last_seen: str = ""          # ISO date; bumped on every re-scrape
    raw_meta: dict = field(default_factory=dict)

    def __post_init__(self):
        """Coerce a None in any `str`-annotated field to "", at the type boundary.

        `None` is outside this dataclass's own annotation, but it arrives: a store driven
        directly (a script, a test, a future source plugin) can pass one, and `_render_new`
        then writes it into the note as the literal string `None`. Measured before this, on a
        real vault: `Lead(company=None, title=None)` was `created` at `None - None.md`, and
        `read_leads` RETURNED it -- the vault's blank-note refusal decides on the RENDERED
        frontmatter, where `"None"` is a perfectly good identity, so the one guard positioned
        to catch it cannot. `Acme - None.md` and `None - Analyst.md` were created too.

        Closing it HERE rather than in the vault is the point. The alternative is a second
        field-level normalisation inside the store, which would leave every other store
        implementing the same rule for itself and the value object still able to hand out a
        `Lead` whose `.company` is None -- `dedup_key` calls `.lower()` on it and would raise.
        One coercion at construction makes `None - None.md` unreachable through the type
        rather than merely rejected downstream, and the vault's refusal then fires on the
        empty strings, which is the case it was written for.

        COERCE, never raise, and that is deliberate rather than lax. `core/vault.py:upsert`
        depends on this path not raising: an exception from a lead's own fields is not caught
        by the ingest sink's `except OSError`, and `engine.py` calls `sink.write` outside its
        per-source try, so a single malformed row would abort the whole ingest run. Empty is
        also what `ingest/base.py` already produces for a missing field (`(row.get(...) or
        "").strip()`), so this makes the direct-construction path agree with the live scrape
        rather than inventing a third behaviour. `Ctx.__post_init__` in `ingest/base.py` takes
        the same shape for the same reason.

        Empty, never stripped: stripping here would move the identity of every lead carrying
        a padded field, which is a change this coercion has no business making. The vault
        strips independently when it decides the refusal.

        The field list is DERIVED from the dataclass (see `_LEAD_STR_FIELDS`), never
        hand-listed, so a field added above is covered without anyone remembering to. The
        post-construction `setattr` loop in `ingest/base.py:_row_to_lead` is outside this --
        `__post_init__` cannot intercept attribute writes -- but it applies a source's `extra`
        and a search's `params`, which in every shipped source set `job_type` alone, and no
        name candidate reads that field.
        """
        for name in _LEAD_STR_FIELDS:
            if getattr(self, name) is None:
                setattr(self, name, "")

    @property
    def dedup_key(self) -> str:
        """Stable identity. Prefer the normalized URL; fall back to a hash of
        title+company+LOCATION so URL-less leads still dedup.

        Location is in the key because without it the engine collapses two jobs sharing a
        title+company but at DIFFERENT cities BEFORE the store's #5 location split can run,
        silently dropping one a layer too early (#23). It is fed through _norm_location --
        the same canonicalisation the store compares with -- so pure case/NFKD variants of
        one city still share a key (collapse), while genuinely different cities split.
        Over-splitting here is safe: two same-city leads that slip through merge at the
        store. Note this changes the key for existing URL-less seen.db entries, so they
        re-surface ONCE (a no-op never-clobber update), then re-key stably.

        The fields are LENGTH-PREFIXED (netstring-style) rather than `|`-joined so a
        delimiter inside a field cannot forge a collision: title="a"/company="b|c" and
        title="a|b"/company="c" both flatten to "a|b|c" under a naive join, which would
        silently drop one genuinely different lead as "already seen"."""
        if self.url:
            return _norm_url(self.url)
        fields = (self.title.lower(), self.company.lower(), _norm_location(self.location))
        key = "".join(f"{len(v)}:{v}" for v in fields)
        return "h:" + hashlib.sha1(key.encode()).hexdigest()

    @property
    def slug(self) -> str:
        """Filesystem-safe basename for the vault lead file."""
        base = f"{self.company}-{self.title}".strip("-")
        s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
        return s[:80] or "lead"


# The `str`-annotated field names of `Lead`, for its None-coercion. DERIVED from the
# dataclass, never hand-listed: a hand-listed set silently stops covering the next field
# added, and the failure is invisible (a note seated at the literal `None`).
#
# Both spellings of the annotation are accepted because they are not interchangeable at
# runtime: `f.type` is the `str` CLASS today, but adding `from __future__ import annotations`
# to this module turns every annotation into the STRING "str" -- and a check written for only
# one spelling would then match nothing, the coercion would silently become a no-op, and the
# tuple would be empty. `test_leads_none_coercion.py` pins that it is not.
_LEAD_STR_FIELDS = tuple(f.name for f in fields(Lead) if f.type in (str, "str"))


def slug_matches(note, wanted: str) -> bool:
    """Substring match of `wanted` against the note's frontmatter company-role slug or the
    store-issued `note.slug`. (It used to also match `note.path`; the store now issues an
    opaque ref, so path-based matching is gone.) Shared by `sluice cv` and `sluice apply`
    for `--lead <slug>`."""
    import re
    hay = re.sub(r"[^a-z0-9]+", "-",
                 f"{note.fm.get('company','')}-{note.fm.get('role','')}".lower()).strip("-")
    return wanted.lower() in hay or wanted.lower() in note.slug.lower()


def index_by_slug(notes) -> tuple[dict, dict]:
    """Index notes by their store-issued slug, DROPPING every slug two or more notes claim,
    and return `(index, dropped)` -- `dropped` mapping each dropped slug to its members.

    `{n.slug: n for n in notes}` silently keeps whichever twin came last. On a flat lead
    store that could not happen -- one directory cannot hold two files at one basename, and
    the vault derives the slug from the basename -- so uniqueness held by CONSTRUCTION. A
    recursive scan (#1) removes that guarantee, and the consequence is not a cosmetic one:
    the surviving twin is the note a receipt match, or a named `leads expire`, then acts on,
    so an `applied` can land on a stale twin while the real lead stays `shortlist` and track
    stops tracking it -- and `apply`'s batch path, which keys on nothing at all, lists that
    one job TWICE in the ready queue `apply prep --all-shortlist` prints, so a human working
    down it works the same job twice. (That path prints; it stages nothing and sends
    nothing.) A wrong `applied` is irreversible; a lead that goes quiet until a human
    renames a note is not.

    So this refuses rather than picks, which is the shape `apply/select.py:select_one` and
    `track confirm` already use for an ambiguous `--lead`: both twins are dropped and the
    ambiguity is logged, never resolved by an invented rule. Callers that must REPORT the
    ambiguity (rather than merely not act on it) take it from the second element; the store
    itself warns as well, on the read that produced the twins.

    PURE: this decides, it does not report. The grouping it computed is RETURNED rather than
    discarded, because every caller wanted it back and each was rebuilding it from the input
    list -- `apply` to name the colliding refs in its skip reason, `track` to probe the twins
    a receipt must not advance. Membership tests still read the same (`slug in dropped`), so
    a caller that only wants the set is not made to pay for the groups. Callers log, through
    their own logger and via `ambiguous_slug_warnings` for the wording; the `what` label that
    used to be a parameter here existed only to decorate a log line, which is a side effect
    this module -- the pure verdict module every sub-app imports -- should not have had.
    """
    grouped: dict = {}
    for n in notes:
        grouped.setdefault(n.slug, []).append(n)
    index, dropped = {}, {}
    for slug, members in grouped.items():
        if len(members) == 1:
            index[slug] = members[0]
        else:
            dropped[slug] = members
    return index, dropped


def ambiguous_slug_warnings(what: str, dropped: dict) -> list[str]:
    """One warning line per slug `index_by_slug` dropped, ready for the CALLER's logger.

    Pure: it formats, nothing writes. It lives beside `index_by_slug` so its call sites
    (five as of #109's triage/engine.py) cannot drift into as many different ways of saying
    the same thing -- which is the drift that moving the logging out to them would otherwise
    have bought. Count deliberately not pinned as a literal here: this docstring itself is
    the site that went stale twice before (see docs/ARCHITECTURE.md's note on this), because
    prose numbers do not go red when a new call site lands.

    The refs, never the slug alone: these notes collide BY slug, so repeating it names
    nothing a human can act on, whereas the paths name the two files to rename or merge.
    """
    return [
        f"{what}: slug {slug!r} is claimed by {len(members)} notes "
        f"({', '.join(sorted(str(n.ref) for n in members))}); "
        f"leaving it alone until a human renames or merges them"
        for slug, members in dropped.items()
    ]


def same_opportunity(note_fm: dict, lead: "Lead", noise=frozenset()) -> str:
    """Whether an EXISTING note and an incoming lead are the same opportunity, as a
    SAME/DIFFERENT/UNKNOWN verdict. A matching NON-EMPTY url is the only proof (SAME);
    otherwise defer to the location comparison. DIFFERENT is the only verdict #5 splits
    on, so a wrong SAME/UNKNOWN merely merges (today's behaviour) while a wrong DIFFERENT
    is a visible extra note. Both urls must be non-empty: google leads carry url:"" and
    _norm_url("") == _norm_url(""), so "urls match -> same" would merge every url-less
    lead sharing a company+title -- the exact loss this removes."""
    note_url = note_fm.get("url", "")
    if lead.url and note_url and _norm_url(lead.url) == _norm_url(note_url):
        return SAME
    return _compare_locations(note_fm.get("location", ""), lead.location, noise)


def _norm_tokens(s: str) -> set:
    """Token SET of a string under the exact fold `_norm_location` implements
    (NFKD, casefold, drop combining marks, unicode-aware \\W split). Shared by
    title and company clustering so both reuse the one pinned normalization."""
    return set(_norm_location(s).split())


def _location_cliques(members, location_noise):
    """Partition members (already same company+role) into complete-linkage location
    cliques, WITHOUT letting a blank-location member bridge two different cities
    (#5, #23 arc-r2-001) and WITHOUT letting one ambiguous blank discard an
    otherwise-valid subclique elsewhere in the group (#23 round-2, CodeRabbit).

    A member is BLANK iff its location's tokens (after noise subtraction) are
    empty -- the same test _compare_locations already applies, reused via
    _compare_locations(loc, loc, noise) == UNKNOWN so "blank" can never drift
    from what the comparison actually treats as evidence-free. Everything else
    is KNOWN.

    SEEDS are the connected components of the compatibility graph
    (_compare_locations != DIFFERENT) restricted to KNOWN members, kept only
    when the component is itself a CLIQUE -- a chain of SAME edges that spans a
    DIFFERENT pair is not a clique and is dropped (safe under-merge: those
    members simply seed nothing). Seeds of size 1 are kept too, because a
    lone KNOWN member can still be the sole anchor a blank attaches to.

    Blanks attach by how many seeds exist to receive them:
      - exactly one seed -> every blank joins it (unambiguous).
      - zero seeds -> >=2 blanks form their own all-blank clique; 0-1 blanks
        cluster nothing.
      - >=2 seeds -> every blank is compatible with every seed (UNKNOWN, not
        DIFFERENT), so which one it belongs to is genuinely undecidable; every
        blank is left unclustered rather than guessed into one, which is
        exactly the bridge this function must refuse to build.

    Only seeds (with blanks attached) that end up size >= 2 are returned, in
    member-order-then-discovery order, so the cluster id (a hash of sorted
    member slugs) is stable. Deterministic in member order throughout."""
    n = len(members)
    compat = [[True] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            ok = _compare_locations(members[i].fm.get("location", ""),
                                    members[j].fm.get("location", ""),
                                    location_noise) != DIFFERENT
            compat[i][j] = compat[j][i] = ok

    def is_blank(i):
        loc = members[i].fm.get("location", "")
        return _compare_locations(loc, loc, location_noise) == UNKNOWN

    known = [i for i in range(n) if not is_blank(i)]
    blanks = [i for i in range(n) if is_blank(i)]

    seen, seeds = set(), []
    for start in known:
        if start in seen:
            continue
        comp, stack = [], [start]
        while stack:                       # DFS the compatibility component, KNOWN members only
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            comp.append(k)
            stack.extend(m for m in known if m not in seen and compat[k][m])
        if all(compat[a][b] for a in comp for b in comp if a != b):
            seeds.append(sorted(comp))
        # else: a chain of SAME edges spans a DIFFERENT pair -- not a clique, so this
        # component seeds nothing (its members stay out unless another seed claims them,
        # which compatibility makes impossible since they were only ever mutually reachable
        # through this now-discarded component).

    if len(seeds) == 1:
        seeds[0] = sorted(seeds[0] + blanks)          # unambiguous: only one place to go
    elif not seeds and len(blanks) >= 2:
        seeds = [sorted(blanks)]                       # no known anchor: an all-blank clique
    # else: zero seeds with < 2 blanks (nothing to cluster), or >= 2 seeds -- in the
    # latter case every blank is compatible with every seed, so attaching to any one of
    # them would be an arbitrary, non-deterministic guess. Leave every blank unclustered
    # instead: it never bridges two different cities (arc-r2-001).

    return [[members[k] for k in s] for s in seeds if len(s) >= 2]


def cluster_duplicates(notes, *, title_noise=(), location_noise=()):
    """Group lead notes into suspected-duplicate clusters (size >= 2), for the
    human-gated `sluice leads dedupe`. Two notes cluster iff same firm
    (`_norm_tokens(company)` equal), same role (`_norm_tokens(role)` minus the
    configured title-noise tokens equal), and a complete-linkage location clique
    (`_location_cliques`). A note whose company or role token set is EMPTY (blank
    `company`, or a role wholly consumed by configured title-noise) carries no
    identity evidence and is skipped -- otherwise two such notes share the same
    empty frozenset key and cluster as duplicates despite having nothing in
    common (CodeRabbit round-2). PROPOSES only; merging is human-gated, so
    recall-leaning is acceptable. See docs/.../read-path-dedup-design.md #1."""
    tnoise = {t for w in title_noise for t in _norm_tokens(w)}
    groups: dict = {}
    for note in notes:
        company = frozenset(_norm_tokens(note.fm.get("company", "")))
        role = frozenset(_norm_tokens(note.fm.get("role", "")) - tnoise)
        if not company or not role:
            continue   # no identity evidence: empty company/role token sets must never match
        groups.setdefault((company, role), []).append(note)
    clusters = []
    for members in groups.values():
        if len(members) >= 2:
            clusters.extend(_location_cliques(members, location_noise))
    return clusters


def cluster_id(members) -> str:
    """Stable short id for a cluster: a hash of its sorted member slugs. Same
    membership -> same id (a report id matches what `--merge` recomputes); any
    membership change -> a new id, so a stale `--merge` id is refused (#23 §4)."""
    key = "\n".join(sorted(n.slug for n in members))
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def pick_survivor(members, winner_status):
    """Among the members holding `winner_status`, the survivor note: highest
    `last_seen`, then slug (deterministic tie-break). See #23 §2.

    Precondition: members' `.status` must already be NORMALIZED -- callers on the
    live path get this from `read_leads`, which normalizes, so `holders` is
    non-empty. A caller passing raw/un-normalized status would need to normalize
    first, or `max([])` raises on an empty `holders`."""
    holders = [n for n in members if n.status == winner_status]
    return max(holders, key=lambda n: (n.fm.get("last_seen", ""), n.slug))


# ── the lead layout (#1) ─────────────────────────────────────────────────────
# Folder = a DERIVED VIEW of status, never a second source of truth. The note's frontmatter stays
# authoritative; a folder that disagrees is drift `sluice leads reconcile` repairs, and drift
# between runs is harmless because the scan is recursive -- a note in the "wrong" folder is still
# read, still written to, still applied for. That is what makes the pass safe to be manual.
ACTIVE_SUBDIR = "Active"
ARCHIVE_SUBDIR = "Archive"
# "" is the flat default and is FIRST, so an unconfigured install is byte-identical to the pre-#1
# store. A name here is a promise: `Vault._write_folder` and `Vault.reconcile_layout` both resolve
# through `layout_subfolder`, so adding an entry without teaching that function raises rather than
# degrading to flat.
LEAD_LAYOUTS = ("", "active_archive")

# The reconcile report's key set, in ONE place. It lives HERE, beside the layout vocabulary,
# rather than in the concrete vault store: `cmd_leads_reconcile`'s knob-unset arm emits an empty
# document too, and importing it from `core/vault.py` put the concrete store on the import path of
# the one command that explicitly handles a store WITHOUT a layout -- the coupling
# `Sluice._layout_store`'s `getattr` exists to avoid. This module is pure and store-agnostic, so
# both the store and the CLI can build from it.
#
# Consumers DEEP-COPY it. A shallow `dict(...)` shares every mutable bucket, which is safe only
# while each one happens to be overridden -- and this comment invites adding a bucket, which would
# then be aliased across every call in the process.
EMPTY_RECONCILE_REPORT = {"layout": "", "moves": [], "in_place": 0, "ambiguous": {},
                          "unknown": [], "user_filed": [], "collisions": [], "skipped": []}


def layout_subfolder(status: str, layout: str) -> str | None:
    """Which subfolder of the leads dir a lead in `status` belongs in under `layout`.

    Returns "" for the leads-dir root, a subfolder name, or None meaning NEVER MOVE THIS NOTE.

    None is not "the root", and the distinction is load-bearing: never-regress passes an
    unrecognized status through untouched everywhere else, so the layout must not decide a folder
    for one either. Answering "" for a non-canonical status would make reconcile drag it out of
    wherever a human deliberately put it. Callers must test `is None` before truthiness.

    The Archive set is DERIVED -- `dismiss` plus `status.is_terminal` -- and that is the whole
    reason this is a function rather than a dict literal. A terminal added to `core/status.py`
    later archives automatically; a hand-listed set would leave it in Active with nothing red.
    `dismiss` is named explicitly because it is TRIAGE-owned, so no application-lifecycle
    predicate can reach it.

    An unknown layout RAISES and lists the valid names rather than falling through to flat --
    `_select_backend`'s rule. A typo'd `lead_layout: activearchive` that silently behaved as flat
    would leave a user believing their vault was being filed when nothing was.
    """
    if layout not in LEAD_LAYOUTS:
        raise ValueError(
            f"unknown lead_layout {layout!r}; valid: "
            + ", ".join(repr(n) for n in LEAD_LAYOUTS))
    if not _status.is_canonical(status):
        return None
    if not layout:
        return ""
    s = _status.normalize(status)
    return ARCHIVE_SUBDIR if (s == "dismiss" or _status.is_terminal(s)) else ACTIVE_SUBDIR
