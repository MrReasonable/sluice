"""The normalized lead - the unit every sub-app passes around."""
import hashlib
import re
from dataclasses import dataclass, field


def _norm_url(u: str) -> str:
    """Canonicalize a URL for dedup by dropping only the #fragment, keeping the
    query, case, and trailing slash. Several boards encode the job id in the
    query (eighty_k `?jobId=`, indeed `?jk=`), so stripping it collapses distinct
    jobs into one; and keeping the full link matches the format already stored in
    the legacy seen.db, so the cutover dedups cleanly instead of re-surfacing."""
    return u.strip().split("#")[0]


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
