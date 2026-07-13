"""Deterministic pre-gate: keep | reject | needs_review, with a plain-language
reason. Runs before any dossier build or LLM call, so obvious cases cost nothing.
The gate stays conservative: it rejects only high-confidence disqualifiers from the
user's own configured lists and hands anything ambiguous to the LLM, because
false-negatives are what the audit catches. It ships with no lists of its own, so an
unconfigured gate abstains rather than applying somebody else's idea of a good role.
"""
import re


def _num(s: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", s or "")
    return int(digits) if digits else None


def classify(lead: dict, cfg) -> tuple[str, str]:
    role = (lead.get("role") or "").lower()
    company = (lead.get("company") or "").strip()
    location = (lead.get("location") or "").lower()
    salary = (lead.get("salary") or "")
    role_type = (lead.get("role_type") or "").lower()

    # The accept list exists to stop a BROAD reject pattern from killing a good
    # title: a bare "manager" reject must not disqualify an accepted "<x> manager".
    # So a reject pattern is ignored only when it is part of the accepted phrase.
    #
    # It must NOT go further and wave the role through wholesale. A title can carry
    # an accept token AND an unrelated disqualifier ("<accepted role> / <rejected
    # role>"), and those mixed titles are exactly what the gate exists to catch.
    # A blanket accept-wins rule let every one of them through.
    matched_accepts = [t for t in cfg.accept_titles if t in role]

    for pat in cfg.reject_titles:
        if pat in role and not any(pat in acc for acc in matched_accepts):
            return "reject", f"Role not a fit: {pat}"

    if any(c in company.lower() for c in cfg.reject_companies):
        return "reject", f"Company skipped: {company}"

    if any(b in location for b in cfg.reject_locations):
        return "reject", "Location outside target geography"

    if location and not any(t in location for t in cfg.target_locations):
        return "reject", "Location outside target geography"

    # Pay floors.
    if "contract" in role_type or "/day" in salary.lower() or "per day" in salary.lower():
        rate = _num(salary)
        if rate is not None and rate < cfg.contract_floor_gbp_day:
            return "reject", f"Day rate below floor: {rate} < {cfg.contract_floor_gbp_day}"
    else:
        amount = _num(salary)
        if amount is not None and amount >= 1000 and amount < cfg.perm_floor_gbp:
            return "reject", f"Salary below floor: {amount} < {cfg.perm_floor_gbp}"

    if not company or company.lower() == "unknown":
        return "needs_review", "No company name; visit URL to identify"

    return "keep", ""
