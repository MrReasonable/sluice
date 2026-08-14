"""Never-clobber apply transition. shortlist -> applied is the only move; every
other status is refused. Stamps additive provenance frontmatter."""
from datetime import date

from sluice.core import status as _status
from sluice.core.protocols import VaultConflict
from sluice.core.vault import frontmatter_safe
from sluice.apply.cvfile import parse_artifact
from sluice.apply.packet import listing_host


def record(vault, note, cfg, *, ats=None, url=None, dry_run=False):
    if not _status.can_apply(note.status):
        return {"ok": False, "reason": note.status}
    basename = parse_artifact(note.fm.get("tailored_cv"), getattr(cfg, "served_prefix", "CV")) or ""
    resolved_ats = ats or listing_host((note.fm.get("url") or "").strip().strip('"'))
    # #131 decision 8: resolved_ats defaults to a value derived from the lead's own
    # scraped url even when nobody passes --ats, so it's reachable from scraped data
    # today -- and over MCP it becomes agent-supplied for the first time. Mirrors
    # url's #111 guard exactly: unsafe -> dropped, never written, prior value on disk
    # untouched (never-clobber).
    safe_ats = frontmatter_safe(resolved_ats) if resolved_ats else None
    ats_dropped = bool(resolved_ats) and not safe_ats
    # #111: url is a CLI --url flag value -- human-typed, but a pasted URL could still
    # carry a stray structural character and corrupt the note's frontmatter on write.
    # Abstain on this one field rather than refuse the whole apply. url_dropped tells
    # the caller a url WAS given and NOT recorded (distinct from no url at all), so
    # cmd_apply_record can surface it -- a silent drop is invisible to the human who
    # typed it (invariant review).
    safe_url = frontmatter_safe(url) if url else None
    url_dropped = bool(url) and not safe_url
    fields = {
        "status": "applied",
        "applied_date": date.today().isoformat(),
        "applied_cv": basename,
    }
    if safe_ats:
        fields["ats"] = safe_ats
    if safe_url:
        fields["applied_url"] = safe_url
    if not dry_run:
        literals = dict(fields)
        if safe_ats:
            literals["ats"] = f'"{safe_ats}"'          # ats needs quoting, same as applied_url
        if safe_url:
            literals["applied_url"] = f'"{safe_url}"'
        try:
            # #131 decision 8: can_apply above reads a SNAPSHOT (note.status, resolved
            # before this call) -- byte-identical to no guard at all against a lead
            # that leaves shortlist between that read and this write, materially more
            # reachable once apply_record lives inside a long-lived MCP process.
            # require_status re-reads FRESH inside the CAS transform and refuses to
            # write if it no longer matches -- mirrors the identical fix
            # triage/apply.py already took for its own snapshot gap.
            wrote = vault.update_fields(note.ref, literals,
                                        require_status=frozenset({"shortlist"}))
        except VaultConflict:
            # #16: a concurrent edit won the write race; the lead is left in its
            # prior (shortlist) state, so `apply` can be re-attempted.
            return {"ok": False, "reason": "conflict"}
        if not wrote:
            # wrote is False (no exception) means require_status's fresh check failed:
            # reaching update_fields at all means the snapshot said shortlist, so this
            # is unambiguous -- the note left shortlist between record's read and this
            # write.
            return {"ok": False, "reason": "raced"}
    result = {"ok": True, "fields": fields}
    if url_dropped:
        result["url_dropped"] = True
    if ats_dropped:
        result["ats_dropped"] = True
    return result
