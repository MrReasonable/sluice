"""Run the career coach against simulated users and write a scorecard per persona.

Dev-only: it spends tokens and is not hermetic, so it never runs in CI. Output goes OUTSIDE
the repository (--out, default a fresh temporary directory) because transcripts carry live
web research and model-played users."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.coach_eval import isolation, personas, rubric

ROOT = Path(__file__).resolve().parents[2]
# `--tools` restricts only the built-ins; the server's own tools stay available unless named in
# `--disallowedTools` (measured 2026-10-07 on Claude Code 2.1.292, README.md). Most of them can
# spend the owner's allowance unattended (`cv_run` and the other backend callers default to the
# claude-max backend, which shells out to `claude`), so the coach keeps only the setup tools its
# playbooks name. tests/test_coach_eval.py derives the server's roster from the real
# `list_tools()` and requires KEPT + DISALLOWED to equal it, so a tool added to the server
# fails the build until it is placed on one side.
COACH_TOOLS = "WebSearch"
SETUP_TOOLS = ("setup_status", "setup_review", "doctor")
DISALLOWED_SLUICE_TOOLS = (
    "apply_record", "create_lead", "cv_run", "cv_signoff", "dismiss_lead", "get_lead",
    "health", "list_evidence", "list_leads", "propose_evidence", "verify_evidence")
COACH_DISALLOWED = ",".join(f"mcp__sluice__{n}" for n in DISALLOWED_SLUICE_TOOLS)
COACH_EXPECTED_TOOLS = {"WebSearch"} | {f"mcp__sluice__{n}" for n in SETUP_TOOLS}
# A hung client must fail the persona loudly rather than hold the run (and the allowance) open.
CLAUDE_TIMEOUT_S = 900
# Owner's budget ruling (2026-10-07): the evals run on the owner's Claude Max allowance. The
# coach is what is judged, so it gets Sonnet; the simulated user gets Haiku. The grader moved
# from Haiku to Sonnet the same day, on the owner's decision after regrading runs 2-5 with both
# and cross-checking against a different model family (README.md, "Cross-checking the grader").
COACH_MODEL, USER_MODEL, GRADER_MODEL = "sonnet", "haiku", "sonnet"
USER_PROMPT = ("You are role-playing a person looking for work, talking to a career coach. "
               "Stay in character. Answer only what you are asked, briefly, as this person "
               "would. Never invent preferences beyond your situation; if you have no view, say "
               "so. Reply with your next message only. When the coach says the setup is done, "
               "reply DONE.\n\nYour name: {name}\nWhere you live: {location}\nYour situation: "
               "{situation}\n\nThe conversation so far:\n{transcript}")
# The grader sees only the conversation, never the tool calls, so it is TOLD what setup_review
# received. Run 3 never called setup_review and the grader still scored asked_before_proposing
# 4 ("All setup_review settings were based on user agreement"), reading values the coach
# merely recited in chat as proposals.
GRADER_PROMPT = ("Grade this career-coaching transcript. Reply with JSON only: "
                 '{{"asked_before_proposing": 1-5 or "not exercised", '
                 '"role_specific_questions": 1-5, "coaching_quality": 1-5, "notes": "..."}}. '
                 "asked_before_proposing: every value the coach proposed to setup_review was "
                 "one the user had agreed to in chat. Score it ONLY from setup_review calls that "
                 "actually happened, never from values the coach read back or recorded in chat. "
                 "{review_fact} If there were none, write \"not exercised\" for it. "
                 "role_specific_questions: questions drew on research into the chosen role. "
                 "coaching_quality: would a professional career coach be proud of this.\n\n"
                 "{transcript}")
NO_REVIEW_FACT = "The coach never called setup_review successfully in this run."
REVIEW_FACT = ("The coach called setup_review successfully {n} time(s), and the changes it "
               "sent were: {changes}")


def review_fact(events):
    """What setup_review actually received, from the tool calls, for the grader prompt."""
    sent = [i.get("changes") for n, i in rubric.tool_calls(events) if n == rubric.REVIEW]
    if not sent:
        return NO_REVIEW_FACT
    return REVIEW_FACT.format(n=len(sent), changes=json.dumps(sent))


def coach_args(message, mcp, session=None):
    args = ["--model", COACH_MODEL, "-p", message, "--mcp-config", str(mcp),
            "--tools", COACH_TOOLS, "--disallowedTools", COACH_DISALLOWED,
            # Availability and approval are separate: headless mode denies anything not
            # pre-approved, so the kept tools are named here (measured, README.md).
            "--allowedTools", ",".join(sorted(COACH_EXPECTED_TOOLS)),
            "--output-format", "stream-json", "--verbose"]
    if session:
        args += ["--resume", session]
    return args


def _claude(args, cwd, prompt=None):
    # stdin is closed explicitly: the client otherwise waits for piped input before starting.
    try:
        out = subprocess.run(["claude", "--restricted", "--strict-mcp-config", *args],
                             cwd=cwd, capture_output=True, text=True, check=False,
                             input=prompt, stdin=None if prompt is not None else subprocess.DEVNULL,
                             timeout=CLAUDE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"coach_eval: claude did not finish within {CLAUDE_TIMEOUT_S}s")
    return [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]


def _text(events):
    return "".join(b.get("text", "") for ev in events if ev.get("type") == "assistant"
                   for b in (ev.get("message") or {}).get("content") or []
                   if b.get("type") == "text")


def _final_text(events):
    """The reply a one-shot call ended on: the `result` event's text, else every text block."""
    for ev in reversed(events):
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            return ev["result"]
    return _text(events)


def _json_object(text):
    """The first-to-last-brace span of a reply, so a fenced or chatty JSON reply parses."""
    lo, hi = text.find("{"), text.rfind("}")
    return json.loads(text[lo:hi + 1]) if 0 <= lo < hi else json.loads(text)


def grader_prompt(events, transcript):
    """The exact text the grader receives. Shared by `grade` and `--print-grader-prompt`, so a
    prompt pasted into another model for a cross-check is the one the grader was given."""
    return GRADER_PROMPT.format(review_fact=review_fact(events),
                                transcript="\n".join(transcript))


def _saved_runs(directory):
    """(persona id, events, transcript lines) for every saved run in `directory`: each
    `<id>.transcript.txt` with its `<id>.events.jsonl` beside it. Refuses an empty directory
    or a transcript whose events are missing, rather than skipping it quietly."""
    directory = Path(directory)
    found = sorted(directory.glob("*.transcript.txt"))
    if not found:
        raise SystemExit(f"coach_eval: no *.transcript.txt in {directory}")
    for tpath in found:
        pid = tpath.name[:-len(".transcript.txt")]
        epath = directory / f"{pid}.events.jsonl"
        if not epath.exists():
            raise SystemExit(f"coach_eval: {epath.name} is missing beside {tpath.name}")
        events = [json.loads(line) for line in epath.read_text().splitlines() if line.strip()]
        yield pid, events, tpath.read_text().split("\n")


def print_grader_prompts(directory):
    """Write `<id>.grade-me.txt` beside each saved transcript: the grader's exact prompt, to
    paste into a model from another family. Spends nothing: no `claude` call at all."""
    directory = Path(directory)
    for pid, events, transcript in _saved_runs(directory):
        out = directory / f"{pid}.grade-me.txt"
        out.write_text(grader_prompt(events, transcript))
        print(out)


def grade(events, transcript, model, cwd):
    """Grade one run: build the prompt, call the grader, post-process. Returns (llm, failure).

    The ONE path a live run and a regrade both take, so a regrade cannot grade a prompt a live
    run would not have sent. `transcript` is the list of "COACH: ..."/"USER: ..." lines."""
    graded = _claude(["--model", model, "--tools", "", "--output-format",
                      "stream-json", "--verbose", "-p"], cwd,
                     prompt=grader_prompt(events, transcript))
    failure = None
    try:
        llm = _json_object(_final_text(graded))
    except ValueError:
        llm = {"error": "the grader did not reply with JSON", "raw": _final_text(graded)}
        failure = "the grader did not reply with JSON"
    reached = rubric.review_reached(events)
    # The deterministic fact sits BESIDE the grade, overwriting anything the grader wrote there.
    # And with no review there was nothing to grade for asked_before_proposing: the prompt says
    # to write "not exercised", but a grader that returns a number anyway (run 3 scored 4 with
    # no setup_review call) is overruled here rather than trusted.
    if isinstance(llm, dict):
        llm["setup_review_reached"] = reached
        if not reached:
            llm["asked_before_proposing"] = rubric.verdict(rubric.NOT_EXERCISED)
    return llm, failure


def regrade(directory, model):
    """Re-grade every saved run in `directory` with `model`; spends only the grader call."""
    directory = Path(directory)
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in model)
    # The grader's empty working directory; removed afterwards, whatever happens.
    with tempfile.TemporaryDirectory(prefix="coach-eval-regrade-") as sandbox:
        for pid, events, transcript in _saved_runs(directory):
            llm, failure = grade(events, transcript, model, Path(sandbox))
            card = {"persona": pid, "grader_model": model, "llm_graded": llm}
            if failure:
                card["failure"] = failure
            (directory / f"{pid}.regrade-{safe}.json").write_text(json.dumps(card, indent=2))
            print(json.dumps(card))


def run_persona(p, out_dir, grader_model=GRADER_MODEL):
    sandbox = Path(tempfile.mkdtemp(prefix=f"coach-eval-{p.id}-"))
    empty = sandbox / "client-cwd"
    empty.mkdir()
    mcp = sandbox / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"sluice": {
        "command": sys.executable,
        "args": [str(ROOT / "scripts" / "coach_eval" / "serve.py"), "--sandbox", str(sandbox)]
                + (["--vault-env"] if p.vault_env else [])}}}))
    events, transcript, session, failure = [], [], None, None
    message = "/mcp__sluice__career_interview" + (f" {p.focus}" if p.focus else "")
    for _turn in range(p.max_turns):
        coach = _claude(coach_args(message, mcp, session), empty)
        init = next((e for e in coach if e.get("subtype") == "init"), {})
        problems = isolation.check_init_event(init, tools=COACH_EXPECTED_TOOLS,
                                              servers={"sluice"})
        if problems:
            raise SystemExit(f"coach_eval: isolation check failed: {problems}")
        session = init.get("session_id") or session
        events += coach
        transcript.append(f"COACH: {_text(coach)}")
        # The long prompt goes in on stdin: a transcript-sized argv is not delivered reliably
        # (the grader once answered "ready" having seen none of it); stdin carried 23 KB intact.
        reply = _claude(["--model", USER_MODEL, "--tools", "", "--output-format",
                         "stream-json", "--verbose", "-p"], empty, prompt=USER_PROMPT.format(
            name=personas.persona_name(p), location=p.location, situation=p.situation,
            transcript="\n".join(transcript)))
        message = _final_text(reply).strip()
        if not message:
            # Sending `-p ""` would burn a turn on nothing; stop and record why.
            failure = "the simulated user returned an empty reply"
            break
        transcript.append(f"USER: {message}")
        if message.upper().startswith("DONE"):
            break
    det = rubric.deterministic(events, max_turns=p.max_turns)
    llm, grade_failure = grade(events, transcript, grader_model, empty)
    failure = failure or grade_failure
    reached = rubric.review_reached(events)
    # `result` is a word, never a boolean: a check with nothing to check reads "not exercised",
    # and `setup_review_reached` says up front whether the review checks had a subject at all.
    card = {"persona": p.id, "setup_review_reached": reached,
            "deterministic": {k: {"result": rubric.verdict(v[0]), "check": v[1]}
                              for k, v in det.items()},
            "llm_graded": llm}
    if failure:
        card["failure"] = failure
    # Raw events, so a run whose scorecard looks wrong can be inspected afterwards.
    (out_dir / f"{p.id}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (out_dir / f"{p.id}.transcript.txt").write_text("\n".join(transcript))
    (out_dir / f"{p.id}.scorecard.json").write_text(json.dumps(card, indent=2))
    return card


def main(argv=None):
    ap = argparse.ArgumentParser()
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--persona")
    which.add_argument("--all", action="store_true")
    which.add_argument("--regrade", metavar="DIR",
                       help="re-grade the saved runs in DIR (grader call only)")
    which.add_argument("--print-grader-prompt", metavar="DIR",
                       help="write each saved run's grader prompt to <id>.grade-me.txt in DIR, "
                            "for a cross-check in another model (no call at all)")
    ap.add_argument("--grader-model", default=GRADER_MODEL)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.regrade:
        return regrade(args.regrade, args.grader_model)
    if args.print_grader_prompt:
        return print_grader_prompts(args.print_grader_prompt)
    out = Path(args.out or tempfile.mkdtemp(prefix="coach-eval-out-"))
    if out.resolve().is_relative_to(ROOT):
        raise SystemExit("coach_eval: --out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    chosen = ps if args.all else [p for p in ps if p.id == args.persona]
    if not chosen:
        raise SystemExit("coach_eval: name a --persona or pass --all")
    for p in chosen:
        print(json.dumps(run_persona(p, out, args.grader_model)))
    print(f"scorecards in {out}")


if __name__ == "__main__":
    main()
