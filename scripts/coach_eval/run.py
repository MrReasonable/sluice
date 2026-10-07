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
# coach is what is judged, so it gets Sonnet; the simulated user and the grader get Haiku.
COACH_MODEL, USER_MODEL, GRADER_MODEL = "sonnet", "haiku", "haiku"
USER_PROMPT = ("You are role-playing a person looking for work, talking to a career coach. "
               "Stay in character. Answer only what you are asked, briefly, as this person "
               "would. Never invent preferences beyond your situation; if you have no view, say "
               "so. Reply with your next message only. When the coach says the setup is done, "
               "reply DONE.\n\nYour name: {name}\nWhere you live: {location}\nYour situation: "
               "{situation}\n\nThe conversation so far:\n{transcript}")
GRADER_PROMPT = ("Grade this career-coaching transcript. Reply with JSON only: "
                 '{{"asked_before_proposing": 1-5, "role_specific_questions": 1-5, '
                 '"coaching_quality": 1-5, "notes": "..."}}. asked_before_proposing: every value '
                 "the coach proposed to setup_review was one the user had agreed to in chat. "
                 "role_specific_questions: questions drew on research into the chosen role. "
                 "coaching_quality: would a professional career coach be proud of this.\n\n"
                 "{transcript}")


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


def run_persona(p, out_dir):
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
    graded = _claude(["--model", GRADER_MODEL, "--tools", "", "--output-format",
                      "stream-json", "--verbose", "-p"], empty,
                     prompt=GRADER_PROMPT.format(transcript="\n".join(transcript)))
    try:
        llm = _json_object(_final_text(graded))
    except ValueError:
        llm = {"error": "the grader did not reply with JSON", "raw": _final_text(graded)}
        failure = failure or "the grader did not reply with JSON"
    card = {"persona": p.id, "deterministic": {k: {"pass": v[0], "check": v[1]}
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
    ap.add_argument("--persona")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    out = Path(args.out or tempfile.mkdtemp(prefix="coach-eval-out-"))
    if out.resolve().is_relative_to(ROOT):
        raise SystemExit("coach_eval: --out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    chosen = ps if args.all else [p for p in ps if p.id == args.persona]
    if not chosen:
        raise SystemExit("coach_eval: name a --persona or pass --all")
    for p in chosen:
        print(json.dumps(run_persona(p, out)))
    print(f"scorecards in {out}")


if __name__ == "__main__":
    main()
