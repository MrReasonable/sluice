"""Eligibility + defensive slug resolution for apply. A lead is apply-eligible iff
it is shortlist, has an apply URL, and its tailored_cv is a resolvable sluice-cv
artifact. The live vault holds duplicate shortlist records, so a slug matching more
than one shortlist lead is REFUSED (never silently picks the first, unlike cv)."""
import os

from sluice.core import status as _status
from sluice.core.leads import slug_matches
from sluice.apply.cvfile import parse_artifact, resolve_source


def eligibility(note, cfg):
    """(ok, reason). reason in {'', not_shortlist, no_url, no_artifact, missing_file}."""
    if not _status.can_apply(note.status):
        return False, "not_shortlist"
    url = (note.fm.get("url") or "").strip().strip('"')
    if not url.startswith("http"):
        return False, "no_url"
    basename = parse_artifact(note.fm.get("tailored_cv"), getattr(cfg, "served_prefix", "CV"))
    if basename is None:
        return False, "no_artifact"
    if not os.path.isfile(resolve_source(basename, cfg)):
        return False, "missing_file"
    return True, ""


def resolve(vault, slug):
    """Shortlist notes whose slug matches `slug`."""
    return [n for n in vault.read_leads({"shortlist"}) if slug_matches(n, slug)]


def _label(note):
    return os.path.basename(note.path)[:-3] if note.path.endswith(".md") else note.path


def select_one(vault, slug, cfg):
    """(note, '') for exactly one eligible match; else (None, reason)."""
    matches = resolve(vault, slug)
    if not matches:
        return None, "no_match"
    if len(matches) > 1:
        return None, "ambiguous: " + " | ".join(_label(n) for n in matches)
    ok, reason = eligibility(matches[0], cfg)
    return (matches[0], "") if ok else (None, reason)


def select_all(vault, cfg):
    """(eligible_notes, [(note, reason)]) across all shortlist leads."""
    eligible, skipped = [], []
    for n in vault.read_leads({"shortlist"}):
        ok, reason = eligibility(n, cfg)
        (eligible if ok else skipped).append(n if ok else (n, reason))
    return eligible, skipped
