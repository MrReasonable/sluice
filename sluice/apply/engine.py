"""Orchestration: prep_one (stage one application), preview_all (ready queue, no
CV staged), record_one (never-clobber transition). Batch previews are per-lead
resilient."""
from dataclasses import dataclass

from sluice.core.leads import StalenessPolicy
from sluice.apply import select as _select
from sluice.apply import packet as _packet
from sluice.apply.cvfile import stage, CvFileError
from sluice.apply.record import record


@dataclass
class PrepResult:
    lead: str
    status: str            # staged | previewed | skipped | failed
    staged: str | None = None
    packet: dict | None = None
    reason: str = ""


def _label(note):
    return note.slug


def prep_one(vault, cfg, slug, policy=StalenessPolicy()):
    note, reason = _select.select_one(vault, slug, cfg, policy)
    if note is None:
        return PrepResult(lead=slug, status="skipped", reason=reason)
    try:
        dest = stage(note.fm.get("tailored_cv"), cfg)
    except CvFileError as e:
        return PrepResult(lead=_label(note), status="failed", reason=str(e))
    pkt = _packet.build_packet(note, cfg, cv_staged=True)
    return PrepResult(lead=_label(note), status="staged", staged=dest, packet=pkt)


def preview_all(vault, cfg, *, limit=None, policy=StalenessPolicy()):
    """Ready-queue preview: eligible leads become 'previewed' (no CV staged),
    ineligible become 'skipped'. Per-lead resilient: an error building one lead's
    packet becomes a 'failed' result and does not abort the rest. `limit` caps the
    eligible previews only."""
    eligible, skipped = _select.select_all(vault, cfg, policy)
    if limit is not None:
        eligible = eligible[:limit]
    results = []
    for n in eligible:
        try:
            pkt = _packet.build_packet(n, cfg, cv_staged=False)
            results.append(PrepResult(lead=_label(n), status="previewed", packet=pkt))
        except Exception as e:  # per-lead resilience: never abort the batch
            results.append(PrepResult(lead=_label(n), status="failed", reason=str(e)))
    results += [PrepResult(lead=_label(n), status="skipped", reason=r) for n, r in skipped]
    return results


def record_one(vault, cfg, slug, *, ats=None, url=None, dry_run=False):
    matches = _select.resolve(vault, slug)
    if not matches:
        return {"ok": False, "reason": "no_match"}
    if len(matches) > 1:
        return {"ok": False, "reason": "ambiguous: " + " | ".join(_label(n) for n in matches)}
    return record(vault, matches[0], cfg, ats=ats, url=url, dry_run=dry_run)
