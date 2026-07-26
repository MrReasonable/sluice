"""The normalized lead - the unit every sub-app passes around."""
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

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


def slug_matches(note, wanted: str) -> bool:
    """Substring match of `wanted` against the note's frontmatter company-role slug or the
    store-issued `note.slug`. (It used to also match `note.path`; the store now issues an
    opaque ref, so path-based matching is gone.) Shared by `sluice cv` and `sluice apply`
    for `--lead <slug>`."""
    import re
    hay = re.sub(r"[^a-z0-9]+", "-",
                 f"{note.fm.get('company','')}-{note.fm.get('role','')}".lower()).strip("-")
    return wanted.lower() in hay or wanted.lower() in note.slug.lower()


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
