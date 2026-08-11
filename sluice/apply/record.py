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
        "ats": resolved_ats,
        "applied_cv": basename,
    }
    if safe_url:
        fields["applied_url"] = safe_url
    if not dry_run:
        literals = dict(fields)
        if safe_url:
            literals["applied_url"] = f'"{safe_url}"'   # URLs need quoting
        try:
            vault.update_fields(note.ref, literals)
        except VaultConflict:
            # #16: a concurrent edit won the write race; the lead is left in its
            # prior (shortlist) state, so `apply` can be re-attempted.
            return {"ok": False, "reason": "conflict"}
    result = {"ok": True, "fields": fields}
    if url_dropped:
        result["url_dropped"] = True
    return result
