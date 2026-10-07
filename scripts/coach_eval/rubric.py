"""Deterministic checks over a coach transcript's TOOL CALLS only. Anything that needs the
conversation read (did the coach ask before proposing; were its questions specific to the
role; coaching quality) is the LLM grader's, and labelled as such in the scorecard.

A check's result is True (pass), False (fail) or NOT_EXERCISED (None): the thing it checks never
happened in this run. Run 2 never reached `setup_review`, and its four review checks all read
"pass" on calls that were never made, which looks exactly like a run whose proposals were
clean. None is falsy on purpose, so a consumer that only asks "did it pass?" cannot read a
check that never ran as one that did."""
from sluice.onboard import review

STATUS = "mcp__sluice__setup_status"
REVIEW = "mcp__sluice__setup_review"
NOT_EXERCISED = None


def _results(events) -> dict:
    """tool_use_id -> is_error, from the stream-json `tool_result` blocks the client emits
    on its `user` events. A permission denial arrives as a tool_result with is_error true."""
    out = {}
    for ev in events:
        if ev.get("type") != "user":
            continue
        content = (ev.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                out[block.get("tool_use_id")] = bool(block.get("is_error"))
    return out


def _uses(events):
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                yield block


def tool_calls(events) -> list:
    """SUCCESSFUL calls only: a call with no matching result, or a denied or errored one,
    never happened as far as any check is concerned. Counting a denied call is how a run
    whose every tool was refused scored green on `status_before_review`."""
    results = _results(events)
    return [(b.get("name"), b.get("input") or {}) for b in _uses(events)
            if results.get(b.get("id")) is False]


def failed_calls(events) -> list:
    results = _results(events)
    return [b.get("name") for b in _uses(events) if results.get(b.get("id")) is not False]


def review_reached(events) -> bool:
    """Whether `setup_review` was SUCCESSFULLY called at least once. The scorecard states it
    beside the checks, so a reader sees at a glance which ones had anything to check."""
    return any(n == REVIEW for n, _ in tool_calls(events))


def verdict(result) -> str:
    """How the scorecard spells a check's result."""
    if result is NOT_EXERCISED:
        return "not exercised"
    return "pass" if result else "fail"


def _when(exercised, result):
    return result if exercised else NOT_EXERCISED


def deterministic(events, *, max_turns) -> dict:
    calls = tool_calls(events)
    names = [n for n, _ in calls]
    reviews = [i for n, i in calls if n == REVIEW]
    reached = bool(reviews)
    changes = [c for r in reviews for c in (r.get("changes") or [])]
    status_first = _when(reached, reached and STATUS in names[:names.index(REVIEW)])
    schema_ok = all(not review.parse_changes(r.get("changes"))[1] for r in reviews)
    briefs = [c for c in changes if c.get("kind") == "brief"]
    # Its subject is a PROPOSED BRIEF, not merely a review: a review carrying no brief section
    # has no sources to check either, and used to pass this check vacuously.
    sources_ok = _when(bool(briefs), any(c.get("target") == "Sources consulted"
                                         and (c.get("value") or "").strip() for c in briefs))
    # One `system/init` event per coach invocation, and each invocation is one message the
    # user sees; counting assistant events instead counted every tool round-trip.
    turns = sum(1 for ev in events if ev.get("subtype") == "init")
    failed = failed_calls(events)
    return {
        "status_before_review": (status_first,
                                 "setup_status called before the first setup_review"),
        "schema_valid": (_when(reached, schema_ok), "every setup_review input parses"),
        "brief_cites_sources": (sources_ok, "a proposed brief records its sources"),
        "no_verified": (_when(reached, all((c.get("target") or "").lower() != "verified"
                                           for c in changes)),
                        "no change targets `verified`"),
        "no_tool_denied": (not failed, f"no tool call was denied or errored (saw {failed})"),
        "turns": (turns <= max_turns, f"{turns} coach messages, cap {max_turns}"),
    }
