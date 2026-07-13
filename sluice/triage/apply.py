"""Write triage outcomes back to the vault, format-preserving and never clobbering
a lead that has already entered the application lifecycle (applied, phone_screen,
...). Reject maps to the canonical `dismiss`; a plain-language reason and the
judge's reasoning are appended (once) to relevance_notes."""
from datetime import date

from sluice.core import status as _status
from sluice.core.log import get_logger

_log = get_logger("triage.apply")
_DECISION_STATUS = {"reject": "dismiss", "needs_review": "needs_review", "keep": "new"}


def _guarded(note) -> bool:
    if _status.is_application_owned(note.status):
        _log.info("skip %s: application-owned status %s", note.path, note.status)
        return True
    return False


def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    tag = f"[triage {date.today().isoformat()}]"
    vault.update_fields(
        note.path, {"status": new_status},
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
    )
    return "applied"


def apply_verdict(vault, note, verdict, dossier) -> str:
    if _guarded(note):
        return "skipped"
    status = _status.normalize(verdict.get("verdict", "needs_review"))
    score = int(verdict.get("relevance_score", 0) or 0)
    rating = (dossier.get("glassdoor") or {}).get("rating", "")
    flags = ", ".join(verdict.get("culture_flags") or [])
    fields = {
        "status": status,
        "score": str(score),
        "glassdoor_rating": f'"{rating}"',
        "culture_flags": f'"{flags}"',
    }
    tag = f"[triage {date.today().isoformat()}]"
    parts = [verdict.get("fit_reasoning", "")]
    if verdict.get("concerns"):
        parts.append("Concerns: " + "; ".join(verdict["concerns"]))
    if verdict.get("recommended_next_action"):
        parts.append("Next: " + verdict["recommended_next_action"])
    note_text = f"{tag} " + " ".join(p for p in parts if p)
    vault.update_fields(note.path, fields, append_note=note_text.strip(), note_tag=tag)
    return "applied"
