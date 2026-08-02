"""Eligibility + defensive slug resolution for apply. A lead is apply-eligible iff
it is shortlist, has an apply URL, and its tailored_cv is a resolvable sluice-cv
artifact. The live vault holds duplicate shortlist records, so a slug matching more
than one shortlist lead is REFUSED (never silently picks the first, unlike cv) --
on BOTH paths: `select_one` refuses the slug the user typed, `select_all` refuses
the slug two notes CLAIM."""
import os

from sluice.core import status as _status
from sluice.core.leads import StalenessPolicy, index_by_slug, slug_matches
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
    """(eligible_notes, [(note, reason)]) across all shortlist leads.

    A slug two notes CLAIM is skipped with an `ambiguous:` reason, exactly as select_one
    refuses a slug two notes MATCH. The two are one policy reached by different keys, and
    this half was the one that mattered: select_one keys on the string the user typed, so
    hardening the slug-keyed dicts in `track` and `leads expire` never reached this loop --
    it iterates notes directly. Both twins were therefore eligible and `apply run --all`
    sent ONE job twice, under the user's name, with no way to unsend. (Measured before this
    guard, with a resolvable artefact so eligibility was actually reached:
    eligible == ['Example - Analyst', 'Example - Analyst'], skipped == [].)

    Skipped, never silently dropped: a lead that vanishes from both lists is the mirror
    failure -- a real application suppressed with nothing said -- and `preview_all` turns
    this list into the user's report."""
    notes = vault.read_leads({"shortlist"})
    # index_by_slug is the shared verdict (track and `leads expire` take the same one), so
    # the four call sites cannot drift into four different opinions about what ambiguous
    # means. Only the second element is wanted here: this pass walks notes, not slugs.
    _, ambiguous = index_by_slug(notes, what="apply: shortlisted lead")
    # The colliding REFS, for the reason string. `_label` would print one slug twice --
    # these notes collide BY slug -- naming nothing a human can act on, whereas the refs
    # name the two files to rename or merge. index_by_slug's own warning says it that way
    # for the same reason. Built only when something collided, which is the rare case.
    colliding: dict = {}
    if ambiguous:
        for n in notes:
            if n.slug in ambiguous:
                colliding.setdefault(n.slug, []).append(str(n.ref))
    eligible, skipped = [], []
    for n in notes:
        if n.slug in ambiguous:
            # BEFORE eligibility, and its own reason: what is wrong here is the IDENTITY,
            # which no eligibility check inspects. Folding it into one of those reasons
            # would tell the user the lead has no CV when in fact two of it do.
            skipped.append((n, "ambiguous: " + " | ".join(sorted(colliding[n.slug]))))
            continue
        ok, reason = eligibility(n, cfg, policy)
        (eligible if ok else skipped).append(n if ok else (n, reason))
    return eligible, skipped
