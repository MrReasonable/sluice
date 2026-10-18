"""Deterministic checks over a coach transcript's TOOL CALLS, and, for the chat-yes rule, the
order of the coach's messages around them. Anything that needs the conversation read (did the
user's reply mean yes; were the questions specific to the role; coaching quality) is the LLM
grader's, and labelled as such in the scorecard.

A check's result is True (pass), False (fail) or NOT_EXERCISED (None): the thing it checks never
happened in this run. Run 2 never reached the save tool (then `setup_review`), and its four
save checks all read "pass" on calls that were never made, which looks exactly like a run whose
proposals were clean. None is falsy on purpose, so a consumer that only asks "did it pass?"
cannot read a check that never ran as one that did."""
import json
import re

import yaml

from scripts.coach_eval import personas
from sluice.onboard import review

STATUS = "mcp__sluice__setup_status"
SAVE = "mcp__sluice__setup_save"
NOT_EXERCISED = None
# The Role Brief's sources section, from the tool's own roster rather than a second spelling.
SOURCES_SECTION = review.ROLE_BRIEF_SECTIONS[-1]


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
    whose every tool was refused scored green on `status_before_review` (now
    `status_before_save`)."""
    results = _results(events)
    return [(b.get("name"), b.get("input") or {}) for b in _uses(events)
            if results.get(b.get("id")) is False]


def failed_calls(events) -> list:
    results = _results(events)
    return [b.get("name") for b in _uses(events) if results.get(b.get("id")) is not False]


def _by_invocation(events):
    """(invocation index, event) for every event. Each coach invocation opens with one
    `system/init` event and answers one user message; invocation 0 answers the opening slash
    command, so a later index means a user turn came first."""
    i = -1
    for ev in events:
        if ev.get("subtype") == "init":
            i += 1
        yield max(i, 0), ev


def _said_by_invocation(events) -> dict:
    """invocation index -> everything the coach said in it, whitespace collapsed."""
    out = {}
    for i, ev in _by_invocation(events):
        if ev.get("type") != "assistant":
            continue
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "text":
                out[i] = out.get(i, "") + " " + b.get("text", "")
    return {i: " ".join(_unquote(t).split()) for i, t in out.items()}


# A Markdown blockquote prefixes every line with "> ", so a multi-line value the coach plays back
# quoted reads "line one > line two" once whitespace is collapsed, and the value no longer matches
# (2026-10-08, retired-board: four values played back verbatim as quotes, all reported unplayed).
# The marker is stripped at line starts only; any other change to the text still fails the match.
_QUOTE_MARK = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)


def _unquote(text: str) -> str:
    return _QUOTE_MARK.sub("", text)


def saves_by_invocation(events) -> list:
    """(invocation index, input) for every SUCCESSFUL setup_save call."""
    results = _results(events)
    return [(i, b.get("input") or {}) for i, ev in _by_invocation(events)
            if ev.get("type") == "assistant"
            for b in (ev.get("message") or {}).get("content") or []
            if b.get("type") == "tool_use" and b.get("name") == SAVE
            and results.get(b.get("id")) is False]


def played_back_texts(change) -> list:
    """What the playback must have shown for one change, each text whitespace-collapsed as the
    coach's is: its value, a search's url, or for a clear the target it returns to the default.
    A list setting's value is a JSON list sent item by item, and EACH item must have been played
    back: `str()` of the list (`['a', 'b']`) is never what a coach writes, so matching it would
    report every list value unplayed, and joining the items would let one missing item pass."""
    if not isinstance(change, dict):
        return []
    if change.get("kind") == "search":
        texts = [change.get("url") or ""]
    elif change.get("clear"):
        texts = [(change.get("target") or "").lstrip("#")]
    elif isinstance(change.get("value"), list):
        texts = [str(item) for item in change["value"]]
    else:
        texts = [change.get("value") or ""]
    return [t for t in (" ".join(str(x).split()) for x in texts) if t]


def unplayed_changes(events) -> list:
    """Every change a successful save sent whose played-back text the coach had not said in
    an EARLIER message, so the user could not have seen it before the yes the save answers.
    A heuristic, stated: the coach may reformat a long section for chat, which reads here as
    not played back, so a failure is a pointer to the transcript, never a verdict on its own."""
    said = _said_by_invocation(events)
    missing = []
    for k, inp in saves_by_invocation(events):
        before = " ".join(said.get(i, "") for i in range(k))
        missing += [c for c in inp.get("changes") or []
                    if any(t not in before for t in played_back_texts(c))]
    return missing


def save_reached(events) -> bool:
    """Whether `setup_save` was SUCCESSFULLY called at least once. The scorecard states it
    beside the checks, so a reader sees at a glance which ones had anything to check."""
    return any(n == SAVE for n, _ in tool_calls(events))


def verdict(result) -> str:
    """How the scorecard spells a check's result."""
    if result is NOT_EXERCISED:
        return "not exercised"
    return "pass" if result else "fail"


def _when(exercised, result):
    return result if exercised else NOT_EXERCISED


def deterministic(events, *, max_turns, finished) -> dict:
    calls = tool_calls(events)
    names = [n for n, _ in calls]
    saves = [i for n, i in calls if n == SAVE]
    reached = bool(saves)
    changes = [c for r in saves for c in (r.get("changes") or [])]
    status_first = _when(reached, reached and STATUS in names[:names.index(SAVE)])
    schema_ok = all(not review.parse_changes(r.get("changes"))[1] for r in saves)
    # Its subject is each PROPOSED BRIEF, one per setup_save call carrying a brief section:
    # a save with no brief section has no sources to check (it used to pass vacuously), and
    # one sourced brief anywhere in the run used to cover every unsourced one after it.
    briefs_per_call = [b for b in ([c for c in (r.get("changes") or [])
                                    if isinstance(c, dict) and c.get("kind") == "brief"]
                                   for r in saves) if b]
    sources_ok = _when(bool(briefs_per_call), all(
        any(c.get("target") == SOURCES_SECTION and (c.get("value") or "").strip() for c in b)
        for b in briefs_per_call))
    # One `system/init` event per coach invocation, and each invocation is one message the
    # user sees; counting assistant events instead counted every tool round-trip.
    turns = sum(1 for ev in events if ev.get("subtype") == "init")
    failed = failed_calls(events)
    return {
        "status_before_save": (status_first,
                               "setup_status called before the first setup_save"),
        "schema_valid": (_when(reached, schema_ok), "every setup_save input parses"),
        "brief_cites_sources": (sources_ok, "every setup_save call proposing a brief "
                                            "section records its sources"),
        "no_verified": (_when(reached, all((c.get("target") or "").lower() != "verified"
                                           for c in changes)),
                        "no change targets `verified`"),
        # The chat-yes rule (spec 2026-10-08): a save answers a user message, never the
        # coach's own playback in the same message, and every value it writes was shown first.
        "save_after_user_turn": (_when(reached, all(k >= 1 for k, _ in
                                                    saves_by_invocation(events))),
                                 "every setup_save came after a user turn"),
        "saves_played_back": (_when(reached, not unplayed_changes(events)),
                              "every saved value was played back in an earlier coach message"),
        "no_tool_denied": (not failed, f"no tool call was denied or errored (saw {failed})"),
        "turns": (turns <= max_turns, f"{turns} coach messages, cap {max_turns}"),
        # `turns` alone could not fail in a live run, since the loop never exceeds its cap; a
        # run cut off there read all-pass while every phase after the cut went untested.
        "finished": (bool(finished), "the run ended on the user's DONE, not at the turn cap "
                                     "or on a failure"),
    }


# ── scenario checks: one scripted turn of events per persona `scenario` (personas.SCENARIOS) ──
# Every check is on every scorecard. Without its scenario, or when its scripted event never
# happened in the run, it reads NOT_EXERCISED -- never pass -- for the reason in this module's
# docstring.

def _payload(content):
    """A tool_result's content as the dict the sluice tool returned, else None. Claude Code
    carries an MCP tool's reply as a string or as a list of text blocks holding its JSON."""
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text")
    try:
        out = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _calls(events) -> list:
    """Every tool call in stream order: name, input, invocation, the event index of the call
    (`at`), whether it succeeded (`ok`, as `tool_calls` counts it), and the event index and
    parsed payload of its result (`result_at`, `result`; None when unanswered)."""
    calls, by_id = [], {}
    for at, (inv, ev) in enumerate(_by_invocation(events)):
        content = (ev.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if ev.get("type") == "assistant" and block.get("type") == "tool_use":
                call = {"name": block.get("name"), "input": block.get("input") or {},
                        "inv": inv, "at": at, "ok": False, "result_at": None, "result": None}
                calls.append(call)
                by_id[block.get("id")] = call
            elif ev.get("type") == "user" and block.get("type") == "tool_result":
                call = by_id.get(block.get("tool_use_id"))
                if call is not None:
                    call.update(ok=not block.get("is_error"), result_at=at,
                                result=_payload(block.get("content")))
    return calls


def _outcome(call) -> str:
    return (call.get("result") or {}).get("outcome") or ""


def _hesitant(calls, user_messages, invocations):
    # User message k (0-based) is answered by coach invocation k + 1. A live run can no longer
    # end on an unanswered "Maybe.": at the cap, run.cap_reached drops a reply that is not DONE
    # before it is recorded. The `k < invocations` filter stays as the guard for a hand-built or
    # older saved event list, which can still hold that shape.
    maybe = [k + 1 for k, m in enumerate(user_messages)
             if (m or "").startswith(personas.HESITANT_LINE)]
    answered = [k for k in maybe if k < invocations]
    saves = [c for c in calls if c["name"] == SAVE and c["ok"]]
    no_save = _when(bool(answered), not any(c["inv"] in answered for c in saves))
    # `_when` takes its result eagerly, so the guard is repeated inside: maybe[0] needs one.
    saved = _when(bool(maybe), bool(maybe) and any(
        c["inv"] > maybe[0] and _outcome(c) == "completed" for c in saves))
    return {
        "no_save_on_maybe": (no_save, f"no setup_save in the coach message answering "
                                      f"\"{personas.HESITANT_LINE}\""),
        "saved_after_yes": (saved, "a setup_save completed in a later coach message"),
    }


def _edited(calls, edit):
    """`edit`: {"invocation": the coach invocation after which the harness edited the note,
    "note_text": the note as the run left it, or None when it is gone}; None = no edit made."""
    if edit is None:
        none = NOT_EXERCISED
        return {"stale_reported": (none, _STALE_CHECK), "status_reread_after_stale": (
            none, _REREAD_CHECK), "edit_survives": (none, _SURVIVES_CHECK)}
    k = edit["invocation"]
    # The tokens the coach held when the note changed: every version a status call returned
    # in or before the edit's invocation. A save sending one of them is a save the edit made
    # stale; a save sending a token read afterwards is a correct re-read, and not this subject.
    held = {(c.get("result") or {}).get("version") for c in calls
            if c["name"] == STATUS and c["ok"] and c["inv"] <= k} - {None}
    due = [c for c in calls if c["name"] == SAVE and c["ok"] and c["inv"] > k
           and (c["input"].get("version") in held)]
    stale = _when(bool(due), all(_outcome(c) == "stale" for c in due))
    first = next((c for c in calls if c["name"] == SAVE and c["ok"]
                  and _outcome(c) == "stale"), None)
    reread = NOT_EXERCISED
    if first is not None:
        # The next save CALL after the stale result, made at all (even one later denied):
        # the status must come between the two.
        nxt = next((c["at"] for c in calls if c["name"] == SAVE
                    and c["at"] > first["result_at"]), float("inf"))
        reread = any(c["name"] == STATUS and c["ok"] and first["result_at"] < c["at"] < nxt
                     for c in calls)
    text = edit.get("note_text")
    return {
        "stale_reported": (stale, _STALE_CHECK),
        "status_reread_after_stale": (reread, _REREAD_CHECK),
        "edit_survives": (bool(text) and personas.EDIT_MARKER in text, _SURVIVES_CHECK),
    }


_STALE_CHECK = "a setup_save sent with a version read before the hand edit came back stale"
_REREAD_CHECK = "setup_status re-read after the first stale result, before the next save"
_SURVIVES_CHECK = "the hand edit is still in the Judging Profile at the end"


def _yaml_mentions(node, board) -> bool:
    if isinstance(node, dict):
        return any(_yaml_mentions(k, board) or _yaml_mentions(v, board) for k, v in node.items())
    if isinstance(node, list):
        return any(_yaml_mentions(v, board) for v in node)
    return isinstance(node, str) and node.strip().lower() == board.lower()


def _retired(calls, user_messages, board, config_text):
    """`board`: the disabled source id the harness handed the simulated user (None = not this
    scenario). Exercised once the user named it or a save targeted it."""
    check = "no change on the retired board was written, and the config does not name it"
    if not board:
        return {"retired_not_written": (NOT_EXERCISED, check)}
    low = board.lower()
    saves = [c for c in calls if c["name"] == SAVE and c["ok"]]
    named = (any(low in (m or "").lower() for m in user_messages)
             or any(low in json.dumps(c["input"]).lower() for c in saves))
    written = [r for c in saves for r in (c.get("result") or {}).get("changes") or []
               if isinstance(r, dict) and r.get("outcome") == "written"
               and low in str(r.get("change") or "").lower().split(":")]
    # Parsed, not grepped: a key or a whole value equal to the id (a `sources:` block, a
    # search target) is a mention; the id inside some other prose value is not one.
    try:
        in_config = _yaml_mentions(yaml.safe_load(config_text or "") or {}, board)
    except yaml.YAMLError:
        in_config = True   # an unreadable config cannot be shown NOT to name it
    return {"retired_not_written": (_when(named, not written and not in_config), check)}


def scenario_checks(scenario, events, *, user_messages=(), edit=None, board=None,
                    config_text=None) -> dict:
    """The scenario checks, all of them on every scorecard: a check whose scenario is not this
    run's is NOT_EXERCISED. `user_messages` are the simulated user's replies in order."""
    calls = _calls(events)
    user_messages = list(user_messages)
    invocations = sum(1 for ev in events if ev.get("subtype") == "init")
    out = _hesitant(calls, user_messages if scenario == "hesitant" else [], invocations)
    out.update(_edited(calls, edit if scenario == "edited_mid_session" else None))
    out.update(_retired(calls, user_messages, board if scenario == "retired_board" else None,
                        config_text))
    return out


# ── the prep coach (prep.py): the interview_prep prompt over the prep_brief tool ──
# This module never imports prep: the kept set arrives as an argument, already prefixed.

PREP = "mcp__sluice__prep_brief"
PROPOSE = "mcp__sluice__propose_evidence"


def prep_brief_ok(events) -> list:
    """The prep_brief calls that SUCCEEDED, defined once: answered without `is_error` AND
    carrying outcome `ok`. Every outcome the facade classifies is a structured reply (the SDK
    discards a raised tool error's message), so a guessed slug's `not_found`, an `ambiguous` or
    an `entries_unreadable` arrives as a plain result, and the not-`is_error` test the setup
    checks use would count it as the brief having been read. A read failure the facade does
    not classify still raises and arrives as `is_error`, which this predicate also refuses. The grader's facts,
    `prep_brief_reached`, both grade overwrites and the rubric's first two checks all read this
    one predicate, so they cannot disagree on whether the brief was reached."""
    return [c for c in _calls(events) if c["name"] == PREP and c["ok"] and _outcome(c) == "ok"]


def saved_stories(events) -> list:
    """Every story the coach SAVED: a propose_evidence call of kind `stories` that answered
    without error AND with outcome `proposed`, in stream order. The outcome, not only `ok`: the
    tool reports a name clash as a structured `refused` that wrote nothing, which is not an
    error either (prep_brief_ok's docstring argues the same class). One reader for the
    deterministic checks and the grader's save facts, so the two cannot disagree."""
    return [c for c in _calls(events)
            if c["name"] == PROPOSE and c["ok"] and c["input"].get("kind") == "stories"
            and _outcome(c) == "proposed"]


def prep_deterministic(events, *, slug, write, kept, max_turns, finished, untold,
                       notes) -> dict:
    """The prep coach's checks, from tool calls only. `slug` is the seeded lead's, as the seeder
    returned it; `kept` is the set of tool names the coach may use, PREFIXED as the client
    reports them (prep.expected_tools), so any other name attempted is a breach, a built-in
    included. `untold` says the persona carries a story its notes do not hold, which is what
    makes the save path a subject at the write level rather than a chance; `notes` are the
    seeded notes' titles, since saving one of those again is not saving a new story."""
    calls = _calls(events)
    ok = prep_brief_ok(events)
    outside = [c["name"] for c in calls if c["name"] not in kept]
    proposed = [c for c in calls if c["name"] == PROPOSE]
    stories = saved_stories(events)
    seeded = {str(t).casefold() for t in notes}
    # Which story was saved is not matched against the persona's untold text: the coach saves
    # the user's own telling, in their words, so a match would be a fuzzy classifier. What this
    # pins is narrower, and the check says only that: the save path ran, under a title no seeded
    # note has. A retelling of a note saved under a new title still passes; the grader's
    # `save_handled`, told what was saved, judges whether it was the user's telling.
    new = [c for c in stories if str(c["input"].get("name") or "").casefold() not in seeded]
    said = _said_by_invocation(events)

    def best_for(call):
        return " ".join(str((call["input"].get("fields") or {}).get("Best For") or "").split())

    def shown(call):
        # The message the user said yes to is the coach's LAST one before the save: the call
        # sits in the invocation answering that yes. Any earlier message would not do, since
        # Best For is a requirement in the posting's words and the coach quotes those when it
        # names the gaps, so a check reaching further back could not fail. Its limit, stated:
        # the coach also quotes the requirement when it asks for the story, so a save made
        # straight after the telling, with no offer, passes this too. Requiring the story's
        # own name there would fail good runs (the first live run named stories in its own
        # words); the graded `save_handled`, told each save, judges the offer and the yes.
        value = best_for(call).casefold()
        return bool(value) and value in said.get(call["inv"] - 1, "").casefold()

    failed = failed_calls(events)
    turns = sum(1 for ev in events if ev.get("subtype") == "init")
    return {
        "prep_brief_ok": (bool(ok), "a prep_brief call returned the brief (outcome ok)"),
        # `_when`: with no successful call `all([])` is True, and a check whose subject never
        # happened must not read pass (this module's docstring). A guessed fragment's
        # not_found before the real call is not counted: recovering through the bounded
        # suggestions is what they are for.
        "seeded_slug": (_when(bool(ok), all(c["input"].get("lead") == slug for c in ok)),
                        "every successful prep_brief call named the seeded lead's slug"),
        "no_disallowed_tool": (not outside, f"no tool outside the kept set was attempted "
                                            f"(saw {outside})"),
        # Every attempt counts, a denied one included: at read-only the playbook gives the
        # `stories add` command instead, so reaching for the tool at all is the failure.
        "propose_only_when_writing": (_when(bool(proposed), bool(write)),
                                      "propose_evidence was called only at the write level"),
        "saved_new_story": (_when(bool(untold) and bool(write), bool(new)),
                            "at the write level, with a story the notes do not hold, the coach "
                            "saved a story under a title no seeded note has (propose_evidence, "
                            "kind stories)"),
        # Never in the coach's first message, which answers the opening slash command: a save
        # there had no user turn to say yes in. Setup's `save_after_user_turn` is the same rule.
        "save_after_user_turn": (_when(bool(stories), all(c["inv"] >= 1 for c in stories)),
                                 "every saved story came after a user turn"),
        "best_for_set": (_when(bool(stories), all(best_for(c) for c in stories)),
                         "every saved story carries a Best For"),
        "best_for_shown": (_when(bool(stories), all(shown(c) for c in stories)),
                           "the coach's message before each save showed that story's Best For"),
        "no_tool_denied": (not failed, f"no tool call was denied or errored (saw {failed})"),
        "turns": (turns <= max_turns, f"{turns} coach messages, cap {max_turns}"),
        # `turns` alone could not fail in a live run, since the loop never exceeds its cap; a
        # run cut off there read all-pass while every phase after the cut went untested.
        "finished": (bool(finished), "the run ended on the user's DONE, not at the turn cap "
                                     "or on a failure"),
    }
