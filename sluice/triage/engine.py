"""Triage orchestrator: load -> classify -> enrich -> judge -> apply -> audit.

Deterministic classify resolves the obvious cases for free (no dossier, no LLM);
only the kept, ambiguous leads are enriched and judged. dry_run computes and
reports but writes nothing (no vault edits, no audit lines). no_llm runs classify
+ apply + audit only. Every lead already in the application lifecycle is skipped
by the apply layer, so triage never clobbers human state.
"""
from dataclasses import dataclass, field
from datetime import date

from sluice.core import status as _status
from sluice.core.log import get_logger
from sluice.triage.apply import apply_classification, apply_verdict
from sluice.triage.audit import render_rejected_note
from sluice.triage.classify import classify
from sluice.triage.judge import judge
from sluice.triage.prompt import build_system_prompt

_log = get_logger("triage.engine")


@dataclass
class TriageReport:
    counts: dict = field(default_factory=lambda: {
        "keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
        "needs_review": 0, "skipped": 0})
    judged: int = 0
    backend: str | None = None
    failures: list = field(default_factory=list)


def run(vault, cfg, backend, dossier_cache, audit, *,
        statuses=("new", "research"), limit=None, dry_run=False, no_llm=False):
    report = TriageReport()
    today = date.today().isoformat()
    notes = vault.read_leads(set(statuses))
    if limit:
        notes = notes[:limit]

    keeps = []          # notes that pass the pre-gate, headed for enrich + judge
    audit_entries = []

    def _audit(entry):
        audit_entries.append(entry)
        if not dry_run:
            audit.append(entry)

    # ── classify pass (free) ──
    for note in notes:
        decision, reason = classify(note.fm, cfg)
        if decision == "keep":
            report.counts["keep"] += 1
            keeps.append(note)
            continue
        outcome = "skipped" if dry_run else apply_classification(
            vault, note, decision, reason)
        key = "skipped" if outcome == "skipped" else (
            "dismiss" if decision == "reject" else "needs_review")
        report.counts[key] = report.counts.get(key, 0) + 1
        _audit({"ts": today, "slug": note.fm.get("url", note.path),
                "company": note.fm.get("company", ""), "role": note.fm.get("role", ""),
                "url": note.fm.get("url", ""), "stage": "classify",
                "decision": decision, "reason": reason, "score": 0})

    # ── enrich + judge (kept, ambiguous) ──
    if keeps and not no_llm:
        dossiers = []
        note_by_id = {}
        for note in keeps:
            try:
                d = dossier_cache.get_or_build(note.fm)
            except Exception as e:
                report.failures.append(f"dossier {note.path}: {e}")
                continue
            dossiers.append(d)
            note_by_id[d["lead_id"]] = note
        # Compose the judge prompt from the candidate's vault-sourced criteria
        # (their editable source of truth), falling back to the baked-in default
        # if it is missing.
        system_prompt = build_system_prompt(vault.dir)
        verdicts = judge(dossiers, backend, batch_size=cfg.batch_size,
                         system_prompt=system_prompt)
        report.judged = len(verdicts)
        report.backend = getattr(backend, "last_backend", None)
        by_id = {d["lead_id"]: d for d in dossiers}
        for verdict in verdicts:
            note = note_by_id.get(verdict.get("lead_id"))
            if note is None:
                continue
            dossier = by_id.get(verdict["lead_id"], {})
            outcome = "skipped" if dry_run else apply_verdict(
                vault, note, verdict, dossier)
            key = "skipped" if outcome == "skipped" else _status.normalize(
                verdict.get("verdict", ""))
            report.counts[key] = report.counts.get(key, 0) + 1
            _audit({"ts": today, "slug": verdict["lead_id"],
                    "company": note.fm.get("company", ""),
                    "role": note.fm.get("role", ""), "url": note.fm.get("url", ""),
                    "stage": "judge", "verdict": verdict.get("verdict"),
                    "reason": verdict.get("fit_reasoning", ""),
                    "score": verdict.get("relevance_score", 0)})

    # ── rendered audit note ──
    if not dry_run and audit_entries:
        render_rejected_note(vault, audit.read_recent(30), cfg.rejected_note)
    return report
