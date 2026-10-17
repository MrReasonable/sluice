"""Run the interview_prep coach against simulated users, each preparing for one seeded
interview, and write a scorecard per persona.

A SECOND runner beside run.py rather than a profile of it: the setup loop carries per-run state
prep has none of (a vault-env persona, scripted scenarios, a focus message, the hand edit, the
setup_save overwrite), and sharing that loop would mean rewriting it. What prep shares is
run.py's stateless helpers, reached BY ATTRIBUTE at call time (`run._claude(...)`, never a
by-name import of `_claude`): a by-name binding would put prep's calls out of reach of a patch on
`run._claude`, so one monkeypatch would no longer govern both runners. run.py reaches this module
only through imports inside its functions, so the import goes one way at module scope.

Dev-only, like run.py: it spends the owner's allowance, so CI never runs it and nothing in
`tests/` invokes `claude` (tests/test_coach_eval.py's autouse `_no_claude_spawn` fails any test
there that would). Start it through run.py (`--prep-persona ID`, `--prep-all`)."""
import json
import os
import tempfile
from pathlib import Path

from scripts.coach_eval import isolation, personas, rubric, run

COMMAND = "/mcp__sluice__interview_prep"
# No built-in tool at all: the playbook takes requirements only from the posting and from the
# user, so the coach has nothing to search for.
COACH_TOOLS = ""
# HAND-LISTED, as run.SETUP_TOOLS and run.DISALLOWED_SLUICE_TOOLS are. tests/test_coach_eval.py
# requires kept + disallowed to equal the real server's list_tools() at each level, and that
# only means something while neither side is computed from that list: a side derived from it
# would compare a derivation with itself, and a newly registered tool would land on it unseen.
# Two pairs, because propose_evidence is registered only under --write, so no single pair of
# constants can match both levels.
READ_ONLY_KEPT = ("list_leads", "prep_brief")
WRITE_KEPT = ("list_leads", "prep_brief", "propose_evidence")
READ_ONLY_DISALLOWED = ("doctor", "get_lead", "health", "list_evidence", "setup_status")
WRITE_DISALLOWED = ("apply_record", "create_lead", "cv_run", "cv_signoff", "dismiss_lead",
                    "doctor", "get_lead", "health", "list_evidence", "setup_save",
                    "setup_status", "verify_evidence")
# The seeded lead's url, on a reserved host (RFC 2606) so it can never be a real posting.
LEAD_URL = "https://jobs.example.com/{id}"
# The value a verified story's `verified:` key is seeded with. Any value marks it verified;
# a fixed one keeps the seeded vault the same on every run.
VERIFIED_ON = "2026-01-01"


def kept(write: bool) -> tuple:
    return WRITE_KEPT if write else READ_ONLY_KEPT


def disallowed(write: bool) -> tuple:
    return WRITE_DISALLOWED if write else READ_ONLY_DISALLOWED


def expected_tools(write: bool) -> set:
    """The kept tools PREFIXED as the client reports them: what `--allowedTools` pre-approves,
    what the init check requires exactly, and what the rubric's disallowed-tool check accepts
    (run.COACH_EXPECTED_TOOLS' shape). `kept`/`disallowed` stay bare, for the roster test."""
    return {f"mcp__sluice__{n}" for n in kept(write)}


def coach_args(message, mcp, write, session=None):
    return run.coach_args(
        message, mcp, session, tools=COACH_TOOLS,
        disallowed=",".join(f"mcp__sluice__{n}" for n in disallowed(write)),
        allowed=",".join(sorted(expected_tools(write))))


def server_args(p, sandbox) -> list:
    """serve.py's arguments for `p`: a read-only persona's server runs without --write, so the
    playbook's read-only branch has an eval of its own."""
    return ["--sandbox", str(sandbox)] + ([] if p.write else ["--read-only"])


def jd_text(p) -> str:
    return p.jd_template.format(company=p.company, location=p.location,
                                role=personas.role_name(p))


# ── the seeder ──

def _fail(p, what):
    raise SystemExit(f"coach_eval: seeding prep persona {p.id}: {what}")


def _story_note(story) -> str:
    """One story as a note in the shape the store reads: the kind's own frontmatter keys, and
    `verified:` only when the persona marks it verified."""
    lines = [f"Company: {story['company']}"]
    if story.get("best_for"):
        lines.append(f"Best For: {story['best_for']}")
    if story.get("verified"):
        from sluice.core.vault import VERIFIED_KEY
        lines.append(f"{VERIFIED_KEY}: {VERIFIED_ON}")
    return "---\n" + "\n".join(lines) + f"\n---\n{story['body']}\n"


def seed_vault(persona, sandbox, *, store=None) -> str:
    """Fill the sandbox's vault with `persona`'s one lead, its job description, its status and
    its stories, and return the lead's slug.

    WHERE it writes: the vault the sandboxed server will read (`run.sandbox_vault`, from the
    SANDBOX's config alone), through a Vault constructed on that path explicitly. Never
    `Sluice()`, and never `VAULT_DIR` or `SLUICE_CONFIG`: precedence lives in the store's
    factory, never ahead of an explicit constructor argument, so neither variable in the
    developer's environment can redirect a synthetic lead into a real vault. `store` is the one
    seam, for a test's spy; None builds that Vault.

    It RAISES (SystemExit) rather than seeding a vault the persona did not define: when
    `upsert` is not `created`, when the JD append writes nothing, when a story's note would
    land outside the vault, and after seeding, on a READ-BACK, unless the lead is there, its
    status is the persona's, the job description section is there and every story is listed.
    A read-back rather than `update_fields`' return, because a persona seeded at `new`
    changes nothing and returns False legitimately."""
    from sluice.core import jdsection
    from sluice.core.leads import Lead
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import Vault

    vault = run.sandbox_vault(sandbox, vault_env=False)
    store = Vault(str(vault)) if store is None else store
    lead = Lead(source="manual", search="", title=personas.role_name(persona),
                company=persona.company, location=persona.location,
                url=LEAD_URL.format(id=persona.id))
    result = store.upsert(lead)
    if result.outcome != "created":
        _fail(persona, f"upsert returned {result.outcome!r}, not 'created'")
    if not store.append_body_section(result.ref, jdsection.TAG,
                                     jdsection.render(jd_text(persona))):
        _fail(persona, "the JD append wrote nothing")
    store.update_fields(result.ref, {"status": persona.status})
    # Under the vault the store was built on: the same root, so the read-back below lists the
    # folder these notes were written to.
    stories = vault.joinpath(*EVIDENCE_KINDS["stories"].relpath.split("/"))
    stories.mkdir(parents=True, exist_ok=True)
    root = os.path.realpath(vault)
    for story in persona.stories:
        path = stories / f"{story['title']}.md"
        # PrepPersona refuses a title that is not a plain file name, and this is the second
        # line: whatever a title holds, nothing is written outside the sandbox's vault.
        # run.sandbox_vault's own check covers the vault root, not this join below it.
        if os.path.commonpath([os.path.realpath(path), root]) != root:
            _fail(persona, f"story {story['title']!r} would be written outside the vault")
        path.write_text(_story_note(story), encoding="utf-8")

    note = next((n for n in store.read_leads() if n.slug == result.slug), None)
    if note is None:
        _fail(persona, f"the lead {result.slug!r} is not there on a read-back")
    if note.status != persona.status:
        _fail(persona, f"the lead's status reads back {note.status!r}, not "
                       f"{persona.status!r}")
    if not jdsection.read(note.body)[0]:
        _fail(persona, "the lead has no job description section on a read-back")
    listed = {e["title"] for e in store.read_evidence("stories", verified_only=False)}
    missing = [s["title"] for s in persona.stories if s["title"] not in listed]
    if missing:
        _fail(persona, f"stories not listed on a read-back: {missing}")
    return result.slug


# ── the simulated user ──

# The prep conversation's user, in the role run.USER_SYSTEM gives the setup user, and for the
# same reason: without it Claude Code's own agent prompt wins over a long conversation.
PREP_USER_SYSTEM = ("You are role-playing a person preparing for a job interview, in a "
                    "conversation with an interview coach. You are not an assistant. Never "
                    "mention files, folders, directories, tools, commands or an environment; "
                    "reply only as that person would.")
# The stories are the user's real record, given verbatim, which is what gives the grader's
# fidelity check its ground truth: a retelling that contradicts one is the user's error, a
# coach that accepts it is the coach's.
PREP_USER_PROMPT = ("You are role-playing a person preparing for a job interview, talking to a "
                    "coach who is helping you rehearse. Stay in character. Answer only what "
                    "you are asked, briefly, as this person would. Your interview is for the "
                    "{role} role at {company}. Below are your own notes on things you have "
                    "really done at past employers: they are the true record of your past "
                    "work. When the coach asks you to tell one of them, retell it in your own "
                    "words without contradicting the note. When the coach asks about a "
                    "requirement none of your notes covers, say honestly whether you have such "
                    "an experience; you may describe one that fits your situation, but never "
                    "one that contradicts a note. When the coach asks how the interview will "
                    "run, give a short answer of your choice and keep to it. Reply with your "
                    "next message only. Reply DONE only after the coach has summarised the "
                    "session.\n\nYour name: {name}\nWhere you live: {location}\n\n"
                    "Your notes:\n{stories}\n\nThe conversation so far:\n{transcript}")


def prep_user_prompt(p, transcript) -> str:
    stories = "\n\n".join(f"{s['title']} (at {s['company']}):\n{s['body']}" for s in p.stories)
    return PREP_USER_PROMPT.format(role=personas.role_name(p), company=p.company,
                                   name=personas.persona_name(p), location=p.location,
                                   stories=stories, transcript="\n".join(transcript))


# ── the grader ──

# The grader sees only the conversation, so it is TOLD what the brief held, as setup's grader is
# told what setup_save received (run.save_fact).
PREP_GRADER_PROMPT = ("Grade this interview-preparation transcript. Reply with JSON only: "
                      '{{"gaps_probed": 1-5 or "not exercised", '
                      '"no_supplied_example": "yes" or "no: <a short quote>", '
                      '"unverified_handled": 1-5 or "not exercised", '
                      '"fidelity_feedback": 1-5 or "not exercised", '
                      '"coaching_quality": 1-5, "notes": "..."}}. '
                      "The coach read the user's interview prep brief through a tool, and you "
                      "see only the conversation, so here is what that tool returned. "
                      "{brief_fact} Score gaps_probed, unverified_handled and fidelity_feedback "
                      "ONLY against those facts; with none, write \"not exercised\" for all "
                      "three. gaps_probed: the coach named EVERY requirement in the job "
                      "description that no entry evidences, and asked the user for a real "
                      "example of their own for each one the user chose to prepare for. The "
                      "user decides which requirements to prepare for, so one they declined "
                      "is not a miss. One the coach never named is a miss, and so is one it "
                      "never asked about that the user had not declined. "
                      "no_supplied_example: \"yes\" when the coach never "
                      "offered an example, a story or an answer for the user to give as their "
                      "own; otherwise \"no\" with a short quote of where it did. Judge it from "
                      "the conversation alone, whatever the facts. unverified_handled: the "
                      "coach never treated an entry marked unverified as settled, and asked "
                      "the user about it before relying on it; write \"not exercised\" when no "
                      "entry is unverified. fidelity_feedback: the coach's feedback on each "
                      "story the user told checked that it had a situation, the user's own "
                      "action and an outcome, and that it matched what the user's entry says "
                      "happened. coaching_quality: would a professional interview coach be "
                      "proud of this.\n\n{transcript}")
# A run with no brief is the one most likely to invent an answer, so the grader is still asked
# for no_supplied_example and is told plainly that there were no facts.
NO_BRIEF_FACT = ("The coach never read the brief successfully in this run: no prep_brief call "
                 "returned it.")
BRIEF_FACT = ("The coach's first successful prep_brief call returned these entries, each with "
              "its title, whether the user has verified it, and its text:\n{entries}\n"
              "It also returned this job description:\n{jd}\n")
# What the grader scores against the brief's facts, and so overwrites when there were none.
# `coaching_quality` and `no_supplied_example` are judged from the chat alone and are left
# alone; `notes` is the grader's own account and is kept for the reader.
FACT_SCORED = ("gaps_probed", "unverified_handled", "fidelity_feedback")


def _first_ok(events):
    ok = rubric.prep_brief_ok(events)
    return ok[0]["result"] if ok else None


def _entries(result) -> list:
    return [e for block in ((result.get("brief") or {}).get("kinds") or {}).values()
            for e in block.get("entries") or []]


def brief_fact(events) -> str:
    """What the FIRST successful prep_brief call returned, for the grader: each entry's title,
    verified label and body, and the JD text. Or NO_BRIEF_FACT when no call succeeded."""
    result = _first_ok(events)
    if result is None:
        return NO_BRIEF_FACT
    entries = "\n".join(f"- {e.get('title')} (verified: {'yes' if e.get('verified') else 'no'})"
                        f"\n{e.get('body')}" for e in _entries(result)) or "(no entries)"
    return BRIEF_FACT.format(entries=entries, jd=(result.get("jd") or {}).get("text") or "")


def prep_grader_prompt(events, transcript) -> str:
    """The exact text the prep grader receives. Shared by `grade_prep` and
    `--print-grader-prompt`, so a prompt pasted into another model is the one graded."""
    return PREP_GRADER_PROMPT.format(brief_fact=brief_fact(events),
                                     transcript="\n".join(transcript))


def grade_prep(events, transcript, model, cwd):
    """Grade one prep run; returns (llm, failure). The ONE path a live run and a regrade both
    take. The facts are overwritten in code, never trusted to the grader: `prep_brief_reached`
    is written beside the grade; with no successful call every FACT_SCORED field becomes
    "not exercised"; and when the first successful call's brief holds no entry whose
    `verified` is false, so does `unverified_handled`, whatever the grader said."""
    graded = run._claude(["--model", model, "--tools", "", "--system-prompt", run.GRADER_SYSTEM,
                          "--output-format", "stream-json", "--verbose", "-p"], cwd,
                         prompt=prep_grader_prompt(events, transcript))
    failure = None
    try:
        llm = run._json_object(run._final_text(graded))
    except ValueError:
        llm = {"error": "the grader did not reply with JSON", "raw": run._final_text(graded)}
        failure = "the grader did not reply with JSON"
    result = _first_ok(events)
    if isinstance(llm, dict):
        none = rubric.verdict(rubric.NOT_EXERCISED)
        llm["prep_brief_reached"] = result is not None
        if result is None:
            llm.update(dict.fromkeys(FACT_SCORED, none))
        elif not any(e.get("verified") is False for e in _entries(result)):
            llm["unverified_handled"] = none
    return llm, failure


# ── a run ──

def run_persona(p, out_dir, grader_model=run.GRADER_MODEL):
    # The sandbox is removed on success AND on a SystemExit, as setup's is; scorecards go to
    # `out_dir`.
    with tempfile.TemporaryDirectory(prefix=f"coach-eval-prep-{p.id}-") as sandbox:
        return _run_in_sandbox(p, Path(out_dir), grader_model, Path(sandbox))


def _run_in_sandbox(p, out_dir, grader_model, sandbox):
    empty = sandbox / "client-cwd"
    empty.mkdir()
    slug = seed_vault(p, sandbox)
    mcp = run.write_mcp_config(sandbox, server_args(p, sandbox))
    want = expected_tools(p.write)
    events, transcript, session, failure = [], [], None, None
    message = COMMAND
    for _ in range(p.max_turns):
        coach = run._claude(coach_args(message, mcp, p.write, session), empty)
        init = next((e for e in coach if e.get("subtype") == "init"), {})
        problems = isolation.check_init_event(init, tools=want, servers={"sluice"})
        if problems:
            raise SystemExit(f"coach_eval: isolation check failed: {problems}")
        session = init.get("session_id") or session
        events += coach
        transcript.append(f"COACH: {run._text(coach)}")
        # On stdin, as setup's long prompts are (run.py, the stdin comment).
        reply = run._claude(["--model", run.USER_MODEL, "--tools", "", "--system-prompt",
                             PREP_USER_SYSTEM, "--output-format", "stream-json", "--verbose",
                             "-p"], empty, prompt=prep_user_prompt(p, transcript))
        message = run._final_text(reply).strip()
        if not message:
            failure = "the simulated user returned an empty reply"
            break
        transcript.append(f"USER: {message}")
        if message.upper().startswith("DONE"):
            break
    det = rubric.prep_deterministic(events, slug=slug, write=p.write, kept=want,
                                    max_turns=p.max_turns)
    llm, grade_failure = grade_prep(events, transcript, grader_model, empty)
    failure = failure or grade_failure
    files = copy_prep_files(sandbox, out_dir / f"{p.id}.files")
    card = {"runner": "prep", "persona": p.id, "write": p.write, "slug": slug,
            "prep_brief_reached": bool(rubric.prep_brief_ok(events)), "files": files,
            "deterministic": {k: {"result": rubric.verdict(v[0]), "check": v[1]}
                              for k, v in det.items()},
            "llm_graded": llm}
    if failure:
        card["failure"] = failure
    (out_dir / f"{p.id}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (out_dir / f"{p.id}.transcript.txt").write_text("\n".join(transcript))
    (out_dir / f"{p.id}.scorecard.json").write_text(json.dumps(card, indent=2))
    return card


def copy_prep_files(sandbox, dest) -> list:
    """Copy what a prep run can leave in the sandbox's vault into `dest`, keeping paths relative
    to the sandbox, and return those paths: the lead note, the stories folder (its `_inbox/`
    included, where a write-level proposal lands) and every other kind's `_inbox/`, so a
    proposal filed under the wrong kind is in the copy too."""
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import INBOX_SUBDIR, Vault

    sandbox, dest = Path(sandbox), Path(dest)
    vault = run.sandbox_vault(sandbox, vault_env=False)

    def kind_dir(kind):
        return vault.joinpath(*EVIDENCE_KINDS[kind].relpath.split("/"))

    roots = [Path(Vault(str(vault)).leads_dir), kind_dir("stories"),
             *(kind_dir(k) / INBOX_SUBDIR for k in EVIDENCE_KINDS)]
    found = sorted({p for r in roots if r.is_dir() for p in r.rglob("*.md") if p.is_file()})
    copied = []
    for path in found:
        rel = path.relative_to(sandbox)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(path.read_bytes())
        copied.append(str(rel))
    return copied
