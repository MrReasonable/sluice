"""The normalized lead - the unit every sub-app passes around."""
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

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
        title+company so URL-less leads still dedup."""
        if self.url:
            return _norm_url(self.url)
        digest = hashlib.sha1(f"{self.title}|{self.company}".lower().encode()).hexdigest()
        return "h:" + digest

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
