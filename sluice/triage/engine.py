"""Triage orchestrator: load -> classify -> resolve -> enrich -> judge -> apply -> audit.

Deterministic classify resolves the obvious cases for free (no dossier, no LLM).
A lead classify() leaves at blank-company needs_review gets ONE resolution
attempt (#109): a free URL-pattern tier 1, then -- opt-in via
cfg.company_resolve_fetch, independent of no_llm -- a real, no-LLM page-visit
tier 2, reusing the same fetch/cache the enrich pass needs anyway. Only the
kept, ambiguous leads are enriched and judged. dry_run computes and reports but
writes nothing (no vault edits, no audit lines) -- resolution's COMPUTATION
still runs under dry_run, only its write is skipped. no_llm runs classify +
(tier-1-only) resolve + apply + audit only. Every lead already in the
application lifecycle is skipped by the apply layer, so triage never clobbers
human state.
"""
from dataclasses import dataclass, field
from datetime import date

from sluice.core import status as _status
from sluice.core.log import get_logger
from sluice.core.protocols import VaultConflict
from sluice.triage import resolve
from sluice.triage.apply import apply_classification, apply_verdict
from sluice.triage.audit import render_rejected_note
from sluice.triage.classify import classify
from sluice.triage.judge import judge
from sluice.triage.prompt import build_system_prompt_from

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
        statuses=("new", "research"), limit=None, dry_run=False, no_llm=False,
        get_source=None):
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

    # ── classify pass (free unless resolution's tier 2 visits a page) ──
    for note in notes:
        company = (note.fm.get("company") or "").strip()
        decision, reason = classify(note.fm, cfg)
        # #109: resolution attempted only for classify()'s OWN blank-company
        # needs_review branch, never ahead of its existing title/location/pay
        # rejects (which don't depend on company at all) -- so a lead classify
        # would reject regardless never triggers a tier-2 page visit.
        if decision == "needs_review" and not company:
            resolved = resolve.resolve_company(
                note.fm, get_source, dossier_cache,
                no_llm=no_llm, company_resolve_fetch=cfg.company_resolve_fetch)
            if resolved:
                wrote = False
                if not dry_run:
                    try:
                        # require_blank, alongside require_status: this decision ("company
                        # is blank, so filling it in is safe") was made from the read_leads
                        # snapshot, and tier 2 spends SECONDS on a real page load before
                        # getting here. A human typing the company into Obsidian in that
                        # window must win -- never-clobber -- so the blankness check has to
                        # be a FRESH re-read inside the CAS transform, exactly like
                        # require_status beside it. A caller-side check on `company` above
                        # is stale by construction and would be an equivalent mutant.
                        wrote = vault.update_fields(
                            note.ref, {"company": f'"{resolved}"'},
                            require_status=frozenset(_status.TRIAGE_OWNED),
                            require_blank=frozenset({"company"}))
                    except VaultConflict as e:
                        report.failures.append(f"company-resolve {note.ref}: {e}")
                    else:
                        if not wrote:
                            report.failures.append(
                                f"company-resolve {note.ref}: company write did not land "
                                "(status changed, or company was already set)")
                if wrote or dry_run:
                    note.fm["company"] = resolved
                    decision, reason = classify(note.fm, cfg)
        if decision == "keep":
            report.counts["keep"] += 1
            keeps.append(note)
            continue
        if dry_run:
            outcome = "skipped"
        else:
            try:
                outcome = apply_classification(vault, note, decision, reason)
            except VaultConflict as e:
                # #16: a concurrent edit won the write race; leave the lead as-is,
                # retried next run. except VaultConflict (not broad Exception) so a
                # real apply-layer logic bug is not silently counted as a transient
                # conflict. continue skips the counting/audit below for this lead.
                report.failures.append(f"apply {note.ref}: {e}")
                continue
        if outcome == "skipped-race":
            # #109 round 3 (arch3-001/inv3-001): apply_classification's own
            # require_status guard already stopped the vault write -- this closes
            # the remaining gap, a PERSISTED audit-log entry claiming a decision
            # that never actually applied, which render_rejected_note would
            # otherwise render into a human-facing summary as if it had.
            report.failures.append(
                f"apply-race {note.ref}: status changed before the {decision} write "
                "landed (or the value was already current)")
        key = "skipped" if outcome in ("skipped", "skipped-race") else (
            "dismiss" if decision == "reject" else "needs_review")
        report.counts[key] = report.counts.get(key, 0) + 1
        if outcome != "skipped-race":
            _audit({"ts": today, "slug": note.slug,
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
                report.failures.append(f"dossier {note.ref}: {e}")
                continue
            # #109: get_or_build SNAPSHOTS `company` off the lead at BUILD time, and the
            # classify pass above resolves a blank one into note.fm AFTER that -- while
            # Task 1's url-hash cache_key makes both passes land on the SAME entry, which
            # is exactly the double-fetch saving it was added for. So the cheaper fetch
            # and a stale judge input are the same fact, and the entry keeps serving that
            # stale blank for the whole ttl (7 days by default), on precisely the leads
            # this feature exists to give a company to. Re-derived here, at the point the
            # dossier is handed to the judge, rather than written back into the cache:
            # the cached JSON stays a faithful record of what was fetched, and every
            # consumer of it that cares reads the note, which is the source of truth.
            d = {**d, "company": note.fm.get("company", "") or d.get("company", "")}
            dossiers.append(d)
            note_by_id[d["lead_id"]] = note
        # Compose the judge prompt from the candidate's vault-sourced criteria
        # (their editable source of truth), falling back to the baked-in default
        # if it is missing.
        system_prompt = build_system_prompt_from(vault.read_criteria())
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
            if dry_run:
                outcome = "skipped"
            else:
                try:
                    outcome = apply_verdict(vault, note, verdict, dossier)
                except VaultConflict as e:
                    # Symmetric with the classify-pass site above.
                    report.failures.append(f"apply {note.ref}: {e}")
                    continue
            if outcome == "skipped-race":
                # Symmetric with the classify-pass site above.
                report.failures.append(
                    f"apply-race {note.ref}: status changed before the verdict write "
                    "landed (or the value was already current)")
            key = "skipped" if outcome in ("skipped", "skipped-race") else _status.normalize(
                verdict.get("verdict", ""))
            report.counts[key] = report.counts.get(key, 0) + 1
            if outcome != "skipped-race":
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
