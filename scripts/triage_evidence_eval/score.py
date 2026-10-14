"""Pure scoring for the #196 live eval -- offline-tested, no backend."""
from statistics import median

_RANK = {"dismiss": 0, "research": 1, "shortlist": 2}


def _valid(run) -> bool:
    """A run fit to score: a known verdict AND an integer score. `bool` is refused
    explicitly because it subclasses `int`; normalisation should already guarantee an int,
    and this guard is what keeps a run that slipped past it from crashing `median`."""
    return (run is not None and run.get("verdict") in _RANK
            and isinstance(run.get("relevance_score"), int)
            and not isinstance(run.get("relevance_score"), bool))


def _band(runs):
    return median(_RANK[r["verdict"]] for r in runs)


def lead_result(baseline, with_record, lead) -> str:
    """INVALID if the no-record baseline cannot be measured against (a missing or
    `unjudgeable` run, a run with no integer score, or a median verdict that is not the expected one). FAIL if the
    record moved the lead: an `unjudgeable`, unscored or missing with-record run, a lower median
    band, a median score below the baseline median by more than the baseline's own spread,
    or the gap named in `concerns` by fewer than a majority of runs. Noise can trip a FAIL
    on an innocent record; that errs safe (the docs then do not recommend the key)."""
    if not all(_valid(r) for r in baseline):
        return "INVALID"
    if _band(baseline) != _RANK[lead.expected]:
        return "INVALID"
    if not all(_valid(r) for r in with_record):
        return "FAIL"
    if _band(with_record) < _band(baseline):
        return "FAIL"
    b_scores = [r["relevance_score"] for r in baseline]
    spread = max(b_scores) - min(b_scores)
    if median(r["relevance_score"] for r in with_record) < median(b_scores) - spread:
        return "FAIL"
    term = lead.gap_term.lower()
    named = sum(any(term in str(c).lower() for c in r.get("concerns") or ())
                for r in with_record)
    if named * 2 <= len(with_record):
        return "FAIL"
    return "PASS"


def overall(results, leads) -> str:
    if "FAIL" in results.values():
        return "FAIL"
    # EVERY non-gap lead, as a list: a dict keyed by role kept one lead per role, so with
    # two borderline leads an INVALID one could be overwritten by its valid sibling and the
    # run read PASS on a borderline case it never measured.
    non_gap = [results.get(lead.id) for lead in leads if lead.role != "gap"]
    valid = [v for v in results.values() if v != "INVALID"]
    if "INVALID" in non_gap or len(valid) * 2 < len(results):
        return "INCONCLUSIVE"
    return "PASS"
