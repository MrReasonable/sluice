"""Eligibility + defensive slug resolution for apply. A lead is apply-eligible iff
it is shortlist, has an apply URL, and its tailored_cv is a resolvable sluice-cv
artifact. The live vault holds duplicate shortlist records, so a slug matching more
than one shortlist lead is REFUSED (never silently picks the first, unlike cv)."""
import os

from sluice.core import status as _status
from sluice.core.leads import StalenessPolicy, slug_matches
from sluice.apply.cvfile import parse_artifact, resolve_source


def eligibility(note, cfg, policy=StalenessPolicy()):
    """(ok, reason). reason in {'', not_shortlist, stale, no_url, no_artifact,
    missing_file}. The default policy abstains, so a call site that forgets to thread one
    fails SAFE rather than refusing every lead."""
    if not _status.can_apply(note.status):
        return False, "not_shortlist"
    # #9: BEFORE the artifact checks. A stale lead must read as `stale`, not as
    # `no_artifact` -- the latter sends the user off to run `cv run`, which would itself
    # refuse it, for a reason the message never mentioned. This closes the gap #9's own
    # text leaves: a lead whose CV was composed before it went stale already has a
    # tailored_cv, so run_batch skips it as skipped-has-cv and it never re-reaches the cv
    # gate at all -- prep is the only thing left between it and an application.
    if policy.blocks(note.fm.get("last_seen", "")):
        return False, "stale"
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
    # The store issues the slug; deriving it from a filename here is what pinned the
    # store to a filesystem.
    return note.slug


def select_one(vault, slug, cfg, policy=StalenessPolicy()):
    """(note, '') for exactly one eligible match; else (None, reason)."""
    matches = resolve(vault, slug)
    if not matches:
        return None, "no_match"
    if len(matches) > 1:
        return None, "ambiguous: " + " | ".join(_label(n) for n in matches)
    ok, reason = eligibility(matches[0], cfg, policy)
    return (matches[0], "") if ok else (None, reason)


def select_all(vault, cfg, policy=StalenessPolicy()):
    """(eligible_notes, [(note, reason)]) across all shortlist leads."""
    eligible, skipped = [], []
    for n in vault.read_leads({"shortlist"}):
        ok, reason = eligibility(n, cfg, policy)
        (eligible if ok else skipped).append(n if ok else (n, reason))
    return eligible, skipped
