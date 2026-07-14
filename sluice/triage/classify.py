"""Deterministic pre-gate: keep | reject | needs_review, with a plain-language
reason. Runs before any dossier build or LLM call, so obvious cases cost nothing.
The gate stays conservative: it rejects only high-confidence disqualifiers from the
user's own configured lists and hands anything ambiguous to the LLM, because
false-negatives are what the audit catches. It ships with no lists of its own, so an
unconfigured gate abstains rather than applying somebody else's idea of a good role.
"""
import re

# Boards advertise pay in wildly inconsistent shapes: "£60k", "£30,000-£40,000",
# "up to £75k", "£500/day", "£50,000 + 10% bonus". The old parser stripped every
# non-digit and concatenated whatever was left, which broke the floors in BOTH
# directions:
#   "£30,000-£40,000"  -> 3000040000, sailing over any floor (the gate silently did
#                         nothing, so a configured floor still showed you £30k jobs)
#   "£1.5k/day"        -> 15, binned by any floor (the gate silently rejected a lead
#                         the user wanted -- the failure that actually costs)
# Parse the money tokens properly instead, and abstain when nothing credible parses.
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

# A bare digit run is NOT money. A salary field reading "Ref 50000" or "postcode 1234"
# would otherwise parse as an advertised salary and be REJECTED by a floor -- binning a
# lead whose pay was never stated at all. That is the fails-closed direction, i.e. exactly
# the bug class this module exists to remove, coming back in through the parser.
#
# So a number only counts as money when it carries MONEY CONTEXT: a currency symbol, or a
# k suffix (which is itself unambiguous). Anything else is ignored, and a string with no
# money in it yields no opinion -- and no opinion never rejects.
_MONEY_RE = re.compile(
    r"[£$€]\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b"   # £60k, £30,000, $120,000
    r"|(\d[\d,]*(?:\.\d+)?)\s*([kK])\b"           # 60k -- the suffix IS the context
)

# Boards routinely write a range with ONE symbol: "£30,000-40,000". The upper bound then
# carries no money context of its own, so _MONEY_RE alone reads the ceiling as 30,000 and a
# £35k floor REJECTS a role paying up to £40k -- fails-closed, the expensive direction. A
# bare number is money when it is the tail of a range whose head was money.
_RANGE_TAIL_RE = re.compile(
    r"\s*(?:-|\u2013|\u2014|to)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b", re.I)

# Below these, a parse is not a real offer -- it is a mis-parse. Abstain rather than
# reject: a wrong reject bins a lead the user never sees, the expensive direction.
_MIN_CREDIBLE_DAY_RATE = 50
_MIN_CREDIBLE_SALARY = 1000


def _salary_amounts(s: str) -> list[int]:
    """Every money amount in `s`, with k-notation expanded ("60k" -> 60000).

    Percentages are stripped first, so "£50,000 + 10% bonus" does not contribute a
    spurious 10.
    """
    if not s:
        return []
    def _to_int(raw: str, k: str) -> int | None:
        try:
            value = float(raw.replace(",", ""))
        except ValueError:  # pragma: no cover - regex only yields parseable numbers
            return None
        # round(), not int(): int() truncates a float product toward zero, and 2.01 is not
        # exactly representable, so int(2.01 * 1000) is 2009. A rate sitting on a floor
        # would flip keep->reject on representation error, not on the pay.
        return round(value * 1000) if k else int(value)

    out: list[int] = []
    text = _PERCENT_RE.sub(" ", s)
    for m in _MONEY_RE.finditer(text):
        cur_raw, cur_k, k_raw, k_suffix = m.groups()
        v = _to_int(cur_raw or k_raw, cur_k or k_suffix)
        if v is not None:
            out.append(v)
        # ...and the tail of a single-symbol range ("£30,000-40,000") is money too.
        tail = _RANGE_TAIL_RE.match(text, m.end())
        if tail:
            tv = _to_int(tail.group(1), tail.group(2))
            if tv is not None:
                out.append(tv)
    return out


def _salary_ceiling(s: str) -> int | None:
    """The TOP of the advertised pay, or None when nothing parses.

    The top, not the bottom, because a floor check must fail OPEN. Rejecting an
    "£80,000-£120,000" lead against a £90k floor on the strength of its lower bound
    would bin a job the user wants; only when even the best case is under the floor is
    the reject safe. `None` means "no opinion" and never rejects.
    """
    amounts = _salary_amounts(s)
    return max(amounts) if amounts else None


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

    # Guarded on target_locations being set. Without the guard an empty list makes
    # `not any(...)` true for every located lead, so an unconfigured install would
    # reject EVERY job that names a location -- the opposite of abstaining.
    if cfg.target_locations and location and not any(
            t in location for t in cfg.target_locations):
        return "reject", "Location outside target geography"

    # Pay floors. Both branches abstain on an implausible parse rather than trusting
    # it. The perm branch always had its credibility guard; the contract branch did
    # not, which is why a "£1.5k/day" lead was silently rejected while the identical
    # mistake on the perm side was harmless.
    if "contract" in role_type or "/day" in salary.lower() or "per day" in salary.lower():
        rate = _salary_ceiling(salary)
        if (rate is not None and rate >= _MIN_CREDIBLE_DAY_RATE
                and rate < cfg.contract_floor_gbp_day):
            return "reject", f"Day rate below floor: {rate} < {cfg.contract_floor_gbp_day}"
    else:
        amount = _salary_ceiling(salary)
        if (amount is not None and amount >= _MIN_CREDIBLE_SALARY
                and amount < cfg.perm_floor_gbp):
            return "reject", f"Salary below floor: {amount} < {cfg.perm_floor_gbp}"

    if not company or company.lower() == "unknown":
        return "needs_review", "No company name; visit URL to identify"

    return "keep", ""
