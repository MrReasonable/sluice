"""Never-clobber apply transition. shortlist -> applied is the only move; every
other status is refused. Stamps additive provenance frontmatter."""
from datetime import date

from sluice.core import status as _status
from sluice.apply.cvfile import parse_artifact
from sluice.apply.packet import listing_host


def record(vault, note, cfg, *, ats=None, url=None, dry_run=False):
    if not _status.can_apply(note.status):
        return {"ok": False, "reason": note.status}
    basename = parse_artifact(note.fm.get("tailored_cv"), getattr(cfg, "served_prefix", "CV")) or ""
    resolved_ats = ats or listing_host((note.fm.get("url") or "").strip().strip('"'))
    fields = {
        "status": "applied",
        "applied_date": date.today().isoformat(),
        "ats": resolved_ats,
        "applied_cv": basename,
    }
    if url:
        fields["applied_url"] = url
    if not dry_run:
        literals = dict(fields)
        if url:
            literals["applied_url"] = f'"{url}"'   # URLs need quoting
        vault.update_fields(note.path, literals)
    return {"ok": True, "fields": fields}
