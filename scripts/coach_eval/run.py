"""Run the career coach against simulated users and write a scorecard per persona.

Dev-only: it spends tokens and is not hermetic, so it never runs in CI. Output goes OUTSIDE
the repository (--out, default a fresh temporary directory) because transcripts carry live
web research and model-played users."""
import argparse
import json
import os
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
SETUP_TOOLS = ("setup_status", "setup_save", "doctor")
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
# Run 8: told to "never invent preferences beyond your situation", the simulated user took the
# sketch as everything it was allowed to say and refused to name any role, history or place, so
# the coach had nothing to work with. The situation is a sketch: the details about the person
# are invented here, at run time, and kept consistent, so the persona files stay neutral. A
# preference the person has no view on may still be declined.
# The persona's town is a neutral placeholder; a simulated user free to invent details once made
# up a whole country for it, which no web search can research, and the run spent its messages
# on invented adverts (2026-10-08, retired-board). So it names a real country when asked.
# Run 9: told to reply DONE "when the coach says the setup is done", the simulated user answered the
# coach's playback question with DONE, which ends the run before any save. A save question gets a
# yes or no; DONE waits for the coach's report of what was saved.
USER_PROMPT = ("You are role-playing a person looking for work, talking to a career coach. "
               "Stay in character. Answer only what you are asked, briefly, as this person "
               "would. Your situation below is a sketch. When the coach asks about your own "
               "work, your history or where you would work, fill in concrete details that fit "
               "it, and keep to the details you have given for the whole conversation. Never "
               "refuse to answer about your own situation. The place named as where you live is a "
               "placeholder: when the coach needs a country or job market to research, name a real "
               "country of your choice and keep to it. On a preference you have no view "
               "on, you may say so. Reply with your next message only. When the coach plays back "
               "what it will save and asks whether to save it, answer yes or no as this person "
               "would; that question is not the end. Reply DONE only after the coach has told "
               "you what was saved, or that nothing was.\n\nYour name: {name}\nWhere you live: {location}\n"
               "Your situation: {situation}\n\nThe conversation so far:\n{transcript}")
# The grader sees only the conversation, never the tool calls, so it is TOLD what setup_save
# received. Run 3 never called the save tool (then `setup_review`) and the grader still scored
# asked_before_proposing 4 ("All setup_review settings were based on user agreement"), reading
# values the coach merely recited in chat as proposals.
GRADER_PROMPT = ("Grade this career-coaching transcript. Reply with JSON only: "
                 '{{"asked_before_proposing": 1-5 or "not exercised", '
                 '"role_specific_questions": 1-5, "coaching_quality": 1-5, "notes": "..."}}. '
                 "asked_before_proposing: every value the coach sent to setup_save had been "
                 "played back to the user, and the user said an explicit yes to it in chat "
                 "before the save. Score it ONLY from setup_save calls that "
                 "actually happened, never from values the coach read back or recorded in chat. "
                 "{save_fact} If there were none, write \"not exercised\" for it. "
                 "role_specific_questions: questions drew on research into the chosen role. "
                 "coaching_quality: would a professional career coach be proud of this.\n\n"
                 "{transcript}")
# A scenario's instruction to the simulated user, appended to its situation. The hesitant line
# is personas.HESITANT_LINE verbatim, the string rubric.scenario_checks looks for. The user model
# is stateless between turns and sees the transcript, so "the first time" is decided from it.
SCENARIO_ADDENDA = {
    "hesitant": ("The FIRST time the coach plays back what it will save and asks whether to "
                 "save it, reply with exactly this line and nothing else: {line} Say it only to "
                 "that question, never to a summary or any other question. If you have already "
                 "sent that line earlier in the conversation, answer the next save question yes. "
                 "Do not pause or end the conversation before you have answered a save question "
                 "twice."),
    # The board's id is filled in at run time (retired_board_id), never written in the persona.
    "retired_board": "The job board you used to use is called {board}.",
}
# The simulated user and the grader run through `claude -p`, which carries Claude Code's own
# agent system prompt. Over a long conversation that prompt won: the simulated user started
# replying as a coding assistant ("This message contains only environment details") and the job
# seeker vanished (2026-10-08, the edited-mid-session and retired-board runs). `--system-prompt`
# REPLACES the agent prompt for these two roles; the coach keeps Claude Code's, as a user's would.
USER_SYSTEM = ("You are role-playing a person looking for work, in a conversation with a career "
               "coach. You are not an assistant. Never mention files, folders, directories, "
               "tools, commands or an environment; reply only as that person would.")
GRADER_SYSTEM = ("You grade career-coaching transcripts. You are not an assistant and take no "
                 "action; reply with the JSON object you are asked for and nothing else.")
NO_SAVE_FACT = "The coach never called setup_save successfully in this run."
SAVE_FACT = ("The coach called setup_save successfully {n} time(s), and the changes it "
             "sent were: {changes}")


def save_fact(events):
    """What setup_save actually received, from the tool calls, for the grader prompt."""
    sent = [i.get("changes") for n, i in rubric.tool_calls(events) if n == rubric.SAVE]
    if not sent:
        return NO_SAVE_FACT
    return SAVE_FACT.format(n=len(sent), changes=json.dumps(sent))


def user_prompt(p, transcript, *, board=None) -> str:
    """The simulated user's prompt for one turn: USER_PROMPT, with the persona's scenario
    instruction (if any) appended to its situation."""
    situation = p.situation
    if p.scenario in SCENARIO_ADDENDA:
        situation += " " + SCENARIO_ADDENDA[p.scenario].format(
            line=personas.HESITANT_LINE, board=board)
    return USER_PROMPT.format(name=personas.persona_name(p), location=p.location,
                              situation=situation, transcript="\n".join(transcript))


def retired_board_id() -> str:
    """The first board in the registry that ships disabled, read at RUN time so the persona file
    names no board and a retirement (or a revival) needs no edit here. Refuses when there is
    none: the scenario would otherwise run with no retired board to ask for."""
    from sluice.ingest.sources import all_sources
    off = [s.id for s in all_sources() if not s.enabled]
    if not off:
        raise SystemExit("coach_eval: retired_board needs a board that ships disabled; "
                         "the registry has none")
    return off[0]


def sandbox_vault(sandbox, vault_env) -> Path:
    """The vault the sandboxed server reads, by its own precedence (stores/vault.py::_make):
    VAULT_DIR, else the config's `vault_dir`, else `./vault` from the server's working
    directory. `~` is the sandbox's HOME (isolation.server_env). Refuses a vault outside the
    sandbox: the harness writes into it, and must never write anywhere else."""
    sandbox = Path(sandbox)
    if vault_env:
        vault = sandbox / "env-vault"
    else:
        import yaml
        cfg = sandbox / "config" / "config.yaml"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.exists() else None
        named = str((data or {}).get("vault_dir") or "") if isinstance(data, dict) else ""
        if named == "~" or named.startswith("~/"):
            vault = sandbox / "home" / named[2:]
        else:
            vault = sandbox / "server-cwd" / (named or "vault")
    real, root = os.path.realpath(vault), os.path.realpath(sandbox)
    if os.path.commonpath([real, root]) != root:
        raise SystemExit("coach_eval: the sandbox server's vault is outside the sandbox; "
                         "refusing to hand-edit it")
    return vault


def hand_edit(sandbox, vault_env) -> str:
    """Append personas.EDIT_MARKER to the vault's Judging Profile, as a person editing it in
    another window would, creating the note (and its folder) when absent. Returns its path
    relative to the sandbox, where copy_setup_files will put it."""
    note = sandbox_vault(sandbox, vault_env).joinpath(*personas.EDIT_NOTE)
    note.parent.mkdir(parents=True, exist_ok=True)
    text = note.read_text(encoding="utf-8") if note.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    note.write_text(text + personas.EDIT_MARKER + "\n", encoding="utf-8")
    return str(note.relative_to(sandbox))


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
    return GRADER_PROMPT.format(save_fact=save_fact(events),
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
    graded = _claude(["--model", model, "--tools", "", "--system-prompt", GRADER_SYSTEM,
                      "--output-format", "stream-json", "--verbose", "-p"], cwd,
                     prompt=grader_prompt(events, transcript))
    failure = None
    try:
        llm = _json_object(_final_text(graded))
    except ValueError:
        llm = {"error": "the grader did not reply with JSON", "raw": _final_text(graded)}
        failure = "the grader did not reply with JSON"
    reached = rubric.save_reached(events)
    # The deterministic fact sits BESIDE the grade, overwriting anything the grader wrote there.
    # And with no save there was nothing to grade for asked_before_proposing: the prompt says
    # to write "not exercised", but a grader that returns a number anyway (run 3 scored 4 with
    # no save call) is overruled here rather than trusted.
    if isinstance(llm, dict):
        llm["setup_save_reached"] = reached
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
    # The sandbox is removed on success AND on the SystemExit an isolation failure or a timeout
    # raises; mkdtemp left one behind per persona per run. Scorecards go to `out_dir`, not here.
    with tempfile.TemporaryDirectory(prefix=f"coach-eval-{p.id}-") as sandbox:
        return _run_in_sandbox(p, out_dir, grader_model, Path(sandbox))


def _run_in_sandbox(p, out_dir, grader_model, sandbox):
    empty = sandbox / "client-cwd"
    empty.mkdir()
    mcp = sandbox / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"sluice": {
        "command": sys.executable,
        "args": [str(ROOT / "scripts" / "coach_eval" / "serve.py"), "--sandbox", str(sandbox)]
                + (["--vault-env"] if p.vault_env else [])}}}))
    events, transcript, session, failure = [], [], None, None
    user_messages, edit, board = [], None, None
    if p.scenario == "retired_board":
        board = retired_board_id()
    message = "/mcp__sluice__career_interview" + (f" {p.focus}" if p.focus else "")
    for turn in range(p.max_turns):
        coach = _claude(coach_args(message, mcp, session), empty)
        init = next((e for e in coach if e.get("subtype") == "init"), {})
        problems = isolation.check_init_event(init, tools=COACH_EXPECTED_TOOLS,
                                              servers={"sluice"})
        if problems:
            raise SystemExit(f"coach_eval: isolation check failed: {problems}")
        session = init.get("session_id") or session
        events += coach
        transcript.append(f"COACH: {_text(coach)}")
        # edited_mid_session: once, straight after the first coach message in which setup_status
        # succeeded, the note changes behind the coach's back -- so the version it holds is stale.
        if (p.scenario == "edited_mid_session" and edit is None
                and any(n == rubric.STATUS for n, _ in rubric.tool_calls(coach))):
            edit = {"invocation": turn, "note": hand_edit(sandbox, p.vault_env)}
        # The long prompt goes in on stdin: a transcript-sized argv is not delivered reliably
        # (the grader once answered "ready" having seen none of it); stdin carried 23 KB intact.
        reply = _claude(["--model", USER_MODEL, "--tools", "", "--system-prompt", USER_SYSTEM,
                         "--output-format",
                         "stream-json", "--verbose", "-p"], empty,
                        prompt=user_prompt(p, transcript, board=board))
        message = _final_text(reply).strip()
        if not message:
            # Sending `-p ""` would burn a turn on nothing; stop and record why.
            failure = "the simulated user returned an empty reply"
            break
        transcript.append(f"USER: {message}")
        user_messages.append(message)
        if message.upper().startswith("DONE"):
            break
    det = rubric.deterministic(events, max_turns=p.max_turns)
    llm, grade_failure = grade(events, transcript, grader_model, empty)
    failure = failure or grade_failure
    reached = rubric.save_reached(events)
    # `result` is a word, never a boolean: a check with nothing to check reads "not exercised",
    # and `setup_save_reached` says up front whether the save checks had a subject at all.
    files_dir = out_dir / f"{p.id}.files"
    files = copy_setup_files(sandbox, files_dir)
    # Read from the COPY, which is what the scorecard's reader opens; the sandbox is removed.
    if edit is not None:
        note = files_dir / edit["note"]
        edit["note_text"] = note.read_text(encoding="utf-8") if note.exists() else None
    config = files_dir / "config" / "config.yaml"
    det.update(rubric.scenario_checks(
        p.scenario, events, user_messages=user_messages, edit=edit, board=board,
        config_text=config.read_text(encoding="utf-8") if config.exists() else None))
    card = {"persona": p.id, "setup_save_reached": reached, "files": files,
            "deterministic": {k: {"result": rubric.verdict(v[0]), "check": v[1]}
                              for k, v in det.items()},
            "llm_graded": llm}
    if p.scenario:
        card["scenario"] = p.scenario
    if board:
        card["retired_board"] = board
    if failure:
        card["failure"] = failure
    # Raw events, so a run whose scorecard looks wrong can be inspected afterwards.
    (out_dir / f"{p.id}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (out_dir / f"{p.id}.transcript.txt").write_text("\n".join(transcript))
    (out_dir / f"{p.id}.scorecard.json").write_text(json.dumps(card, indent=2))
    return card


def copy_setup_files(sandbox, dest) -> list:
    """Copy what the run left in the sandbox -- every note (`*.md`) and the config file
    (`*.yaml`) -- into `dest`, keeping their paths relative to the sandbox, and return those
    paths. With no form to cancel, a run's saves really land, and the files are the result to
    read in Obsidian, as the user would (spec 2026-10-08, Testing). The client's own empty
    working directory is skipped."""
    sandbox, dest = Path(sandbox), Path(dest)
    copied = []
    for path in sorted(sandbox.rglob("*")):
        rel = path.relative_to(sandbox)
        if not path.is_file() or rel.parts[0] == "client-cwd" or path.suffix not in (
                ".md", ".yaml"):
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(path.read_bytes())
        copied.append(str(rel))
    return copied


def _outside_repo(directory, flag):
    path = Path(directory)
    if path.resolve().is_relative_to(ROOT):
        raise SystemExit(f"coach_eval: {flag} must be outside the repository")
    return path


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
    # Every mode writes into its directory (scorecards, regrade cards, grader prompts), and
    # what it writes carries live transcripts: none of it may land where git can see it.
    if args.regrade:
        return regrade(_outside_repo(args.regrade, "--regrade"), args.grader_model)
    if args.print_grader_prompt:
        return print_grader_prompts(_outside_repo(args.print_grader_prompt,
                                                  "--print-grader-prompt"))
    out = _outside_repo(args.out or tempfile.mkdtemp(prefix="coach-eval-out-"), "--out")
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
