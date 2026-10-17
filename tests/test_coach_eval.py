import json
import re
from pathlib import Path

import pytest

from scripts.coach_eval import isolation, personas, rubric
from tests.conftest import LOCATIONS, PATH_ENV_VARS

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_claude_spawn(monkeypatch):
    """Every test here fails CLOSED on a real `claude` spawn rather than spending the owner's
    allowance. Most tests patch `run._claude`, and that patch governs only callers that reach
    `_claude` through the module attribute: a by-name import in a runner would call the real one,
    which spawns `claude` on any machine that has it installed. A test that stubs
    `subprocess.run` itself (`_stub_claude`) patches over this one for its own duration. Any
    other command still runs, so a test driving a real subprocess of its own keeps working."""
    import os
    import subprocess

    real = subprocess.run

    def _claude_tripwire(cmd, *a, **k):
        argv = cmd.split() if isinstance(cmd, str) else [str(c) for c in cmd]
        if argv and os.path.basename(argv[0]) == "claude":
            raise AssertionError(f"a test spawned a real claude process: {cmd}")
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", _claude_tripwire)


def test_the_claude_tripwire_is_installed_and_refuses_a_spawn(tmp_path):
    import subprocess

    from scripts.coach_eval import run

    # Checked BEFORE the call below: without the fixture that call would spawn a real claude.
    assert subprocess.run.__name__ == "_claude_tripwire"
    with pytest.raises(AssertionError, match="spawned a real claude"):
        run._claude(["-p", "x"], tmp_path)


def test_the_unset_set_is_derived_and_matches_the_sandbox():
    assert isolation.path_env_vars(ROOT) == set(PATH_ENV_VARS)
    assert set(isolation.EXTRA_UNSET) >= {"CAMOFOX_USER", "CAMOFOX_SESSION", "CAMOFOX_URL",
                                          "SLUICE_TELEGRAM_TOKEN", "SLUICE_TELEGRAM_CHAT",
                                          "VAULT_DIR"}


def test_a_clean_server_env_has_no_problems(tmp_path):
    env = isolation.server_env({"PATH": "/usr/bin"}, tmp_path, ROOT)
    assert isolation.isolation_problems(env, tmp_path, ROOT) == []


@pytest.mark.parametrize("var", sorted(isolation.unset_vars(ROOT) - set(isolation.SET_VARS)))
def test_each_variable_that_must_be_unset_is_reported(tmp_path, var):
    env = isolation.server_env({}, tmp_path, ROOT)
    env[var] = "/elsewhere"
    assert any(var in p for p in isolation.isolation_problems(env, tmp_path, ROOT))


@pytest.mark.parametrize("var", isolation.SET_VARS)
def test_each_variable_that_must_point_into_the_sandbox_is_reported(tmp_path, var):
    env = isolation.server_env({}, tmp_path, ROOT)
    env[var] = "/elsewhere"
    assert any(var in p for p in isolation.isolation_problems(env, tmp_path, ROOT))


def _init(**over):
    ev = {"type": "system", "subtype": "init", "claude_code_version": "2.1.292",
          "mcp_servers": [{"name": "sluice", "status": "connected"}],
          "tools": ["WebSearch"], "plugins": [{"name": "x", "path": "builtin"}]}
    ev.update(over)
    return ev


@pytest.mark.parametrize("over", [{"claude_code_version": "0.0.1"},
                                  {"mcp_servers": [{"name": "sluice"}, {"name": "other"}]},
                                  {"tools": ["WebSearch", "Bash"]},
                                  {"plugins": [{"name": "x", "path": "plugins/x"}]},
                                  {"mcp_servers": [{"name": "sluice", "status": "failed"}]}])
def test_the_init_event_check_refuses_each_leak(over):
    assert isolation.check_init_event(_init(**over), tools={"WebSearch"}, servers={"sluice"})


def test_the_init_event_check_accepts_the_measured_shape():
    assert isolation.check_init_event(_init(), tools={"WebSearch"}, servers={"sluice"}) == []


def test_personas_are_synthetic():
    from sluice.onboard.questions import expresses_a_preference
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    assert len(ps) >= 5 and all(p.location in LOCATIONS for p in ps)
    assert all(expresses_a_preference(p.situation + " " + p.focus) == [] for p in ps)
    assert sum(p.vault_env for p in ps) == 1
    assert personas.persona_name(ps[0]) == personas.persona_name(ps[0])


_N = iter(range(10**6))


def _use(name, changes=None, *, denied=False, answered=True):
    """An assistant tool_use plus, unless `answered` is False, the user-event tool_result the
    client emits for it (is_error true is how a permission denial arrives)."""
    inp = {} if changes is None else {"changes": changes}
    tid = f"toolu_{next(_N)}"
    ev = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}]
    if answered:
        ev.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": denied,
             "content": [{"type": "text", "text": "permission denied" if denied else "ok"}]}]}})
    return ev


def _flat(*groups):
    return [e for g in groups for e in g]


def _start():
    return [{"type": "system", "subtype": "init"}]


def _say(text):
    return [{"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}]


def test_rubric_deterministic_checks():
    # The playback, then a user turn (the second init), then the save.
    events = _flat(_start(), _use(rubric.STATUS), _say("Sources consulted: example.invalid"),
                   _start(), _use(rubric.SAVE, [
                       {"kind": "brief", "target": "Sources consulted",
                        "value": "example.invalid"}]))
    out = rubric.deterministic(events, max_turns=30)
    assert all(v[0] for v in out.values()), out


@pytest.mark.parametrize("events,check", [
    (_flat(_use(rubric.SAVE, [])), "status_before_save"),
    # a status call that was DENIED does not count as the status having been read
    (_flat(_use(rubric.STATUS, denied=True), _use(rubric.SAVE, [])), "status_before_save"),
    (_flat(_use(rubric.STATUS), _use(rubric.SAVE, [{"kind": "brief"}])), "schema_valid"),
    (_flat(_use(rubric.STATUS), _use(rubric.SAVE, [{"kind": "brief", "target": "Pay structure",
                                                      "value": "x"}])), "brief_cites_sources"),
    (_flat(_use(rubric.STATUS), _use(rubric.SAVE, [{"kind": "config", "target": "verified",
                                                      "value": "x"}])), "no_verified"),
    (_flat(*[_start() for _ in range(31)]), "turns"),
    (_flat(_use(rubric.STATUS, denied=True)), "no_tool_denied"),
    (_flat(_use(rubric.STATUS, answered=False)), "no_tool_denied"),
])
def test_each_rubric_check_fails_when_its_rule_is_broken(events, check):
    assert rubric.deterministic(events, max_turns=30)[check][0] is False


_SOURCED = {"kind": "brief", "target": "Sources consulted", "value": "example.invalid"}
_UNSOURCED = {"kind": "brief", "target": "Pay structure", "value": "x"}


def test_denied_calls_are_ignored_by_the_checks_that_read_inputs():
    bad = [{"kind": "brief"}]  # would fail schema_valid, and target `verified` below
    events = _flat(_use(rubric.STATUS), _use(rubric.SAVE, bad, denied=True),
                   _use(rubric.SAVE, [{"kind": "config", "target": "verified"}], denied=True),
                   _use(rubric.SAVE, [_SOURCED]))
    out = rubric.deterministic(events, max_turns=30)
    assert out["schema_valid"][0] is True and out["no_verified"][0] is True
    assert out["no_tool_denied"][0] is False


def test_a_denied_calls_sources_do_not_count_for_brief_cites_sources():
    # The denied save's sourced brief would satisfy the check if denied calls counted; the
    # successful one proposes a brief WITHOUT sources, so the check must fail.
    events = _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_SOURCED], denied=True),
                   _use(rubric.SAVE, [_UNSOURCED]))
    assert rubric.deterministic(events, max_turns=30)["brief_cites_sources"][0] is False


_SAVE_CHECKS = ("status_before_save", "schema_valid", "brief_cites_sources", "no_verified")


@pytest.mark.parametrize("events", [
    [],
    _flat(_start(), _use(rubric.STATUS)),
    # run 2's shape: research done, the session closed, setup_save never called
    _flat(_start(), _use(rubric.STATUS), _use("WebSearch")),
    _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_SOURCED], denied=True)),
    _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_SOURCED], answered=False)),
    # a denied save with an UNSOURCED brief: not a failure either, since it never happened
    _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_UNSOURCED], denied=True)),
])
def test_save_checks_are_not_exercised_without_a_successful_save(events):
    out = rubric.deterministic(events, max_turns=30)
    assert {k: out[k][0] for k in _SAVE_CHECKS} == dict.fromkeys(
        _SAVE_CHECKS, rubric.NOT_EXERCISED)
    assert [rubric.verdict(out[k][0]) for k in _SAVE_CHECKS] == ["not exercised"] * 4
    assert rubric.save_reached(events) is False


def test_brief_cites_sources_is_not_exercised_when_no_brief_was_proposed():
    events = _flat(_use(rubric.STATUS), _use(rubric.SAVE, [
        {"kind": "config", "target": "accept_titles", "value": "x"}]))
    out = rubric.deterministic(events, max_turns=30)
    assert out["brief_cites_sources"][0] is rubric.NOT_EXERCISED
    assert all(out[k][0] is True for k in ("status_before_save", "schema_valid", "no_verified"))
    assert rubric.save_reached(events) is True


def test_verdict_spells_each_state():
    assert [rubric.verdict(v) for v in (True, False, rubric.NOT_EXERCISED)] == [
        "pass", "fail", "not exercised"]


def test_turns_counts_coach_messages_not_assistant_events():
    events = _flat(_start(), *[_use(rubric.STATUS) for _ in range(40)])
    assert rubric.deterministic(events, max_turns=1)["turns"][0] is True


def test_run_refuses_an_output_directory_inside_the_repository():
    from scripts.coach_eval import run

    with pytest.raises(SystemExit, match="outside the repository"):
        run.main(["--persona", "x", "--out", str(ROOT / "x")])


def test_serve_refuses_to_exec_when_the_environment_is_not_sandboxed(tmp_path, monkeypatch):
    from scripts.coach_eval import serve

    real = isolation.server_env

    def leaky(base, sandbox, root, *, vault_env=False):
        env = real(base, sandbox, root, vault_env=vault_env)
        env["SEEN_DB"] = "/elsewhere"  # a path var the sandbox must have dropped
        return env

    def never(*_a, **_k):
        raise AssertionError("the real server was exec'd past a failed isolation check")

    monkeypatch.setattr(isolation, "server_env", leaky)
    monkeypatch.setattr("os.execve", never)
    assert serve.main(["--sandbox", str(tmp_path)]) == 2


def test_serve_execs_the_real_server_from_an_empty_cwd_when_sandboxed(tmp_path, monkeypatch):
    from scripts.coach_eval import serve

    seen = {}

    def fake(exe, argv, env):
        seen.update(exe=exe, argv=argv, env=env, cwd=Path.cwd())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("os.execve", fake)
    serve.main(["--sandbox", str(tmp_path)])
    assert seen["argv"][1:] == ["mcp", "serve", "--write"]
    assert seen["cwd"] == (tmp_path / "server-cwd").resolve()
    # The environment the server is actually started with is the checked sandbox one.
    assert isolation.isolation_problems(seen["env"], tmp_path, ROOT) == []


def _server_tool_names(write=True):
    import asyncio

    from sluice.core.config import Config
    from sluice.mcpserver import build_server

    async def _run():
        from mcp import Client
        async with Client(build_server(Config(), write=write), raise_exceptions=True) as c:
            return {t.name for t in (await c.list_tools()).tools}

    return asyncio.run(_run())


def test_every_server_tool_is_either_kept_or_disallowed_for_the_coach():
    from scripts.coach_eval import run

    # The write level only: it is the one level the setup coach runs at (serve.py's default).
    roster = _server_tool_names(write=True)
    assert roster  # the sweep enumerated something
    kept, gone = set(run.SETUP_TOOLS), set(run.DISALLOWED_SLUICE_TOOLS)
    assert kept | gone == roster and not kept & gone


def test_coach_argv_disallows_every_spending_tool_and_expects_only_the_kept_ones(tmp_path):
    from scripts.coach_eval import run

    args = run.coach_args("hi", tmp_path / "m.json")
    listed = set(args[args.index("--disallowedTools") + 1].split(","))
    assert listed == {f"mcp__sluice__{n}" for n in _server_tool_names() - set(run.SETUP_TOOLS)}
    assert run.COACH_EXPECTED_TOOLS == {"WebSearch", *(f"mcp__sluice__{n}"
                                                       for n in run.SETUP_TOOLS)}
    assert not run.COACH_EXPECTED_TOOLS & listed


def test_init_check_fails_when_a_spending_tool_is_present():
    from scripts.coach_eval import run

    ev = {"claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
          "mcp_servers": [{"name": "sluice", "status": "connected"}], "plugins": [],
          "tools": sorted(run.COACH_EXPECTED_TOOLS | {"mcp__sluice__cv_run"})}
    problems = isolation.check_init_event(ev, tools=run.COACH_EXPECTED_TOOLS, servers={"sluice"})
    assert any("cv_run" in p for p in problems)
    ev["tools"] = sorted(run.COACH_EXPECTED_TOOLS)
    assert isolation.check_init_event(ev, tools=run.COACH_EXPECTED_TOOLS,
                                      servers={"sluice"}) == []


def test_a_hung_claude_fails_loudly(monkeypatch, tmp_path):
    import subprocess

    from scripts.coach_eval import run

    seen = {}

    def hang(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(SystemExit, match="did not finish"):
        run._claude(["-p", "x"], tmp_path)
    assert seen["timeout"] == run.CLAUDE_TIMEOUT_S


def test_an_empty_simulated_user_reply_stops_the_loop_with_a_recorded_failure(
        monkeypatch, tmp_path):
    from scripts.coach_eval import run

    init = {"subtype": "init", "session_id": "s", "claude_code_version":
            next(iter(isolation.MEASURED_VERSIONS)), "plugins": [],
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}
    calls = []

    def fake(args, cwd, prompt=None):
        calls.append(args)
        if "--mcp-config" in args:
            return [init]
        return []  # the simulated user says nothing

    monkeypatch.setattr(run, "_claude", fake)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    card = run.run_persona(p, tmp_path)
    assert card["failure"] == "the simulated user returned an empty reply"
    # coach, [the focus turn's second coach call,] empty user turn, grader: no further coach
    # call on `-p ""`
    assert len(calls) == (4 if p.focus else 3)


def test_provider_env_names_are_derived_and_include_the_anthropic_key():
    names = isolation.provider_env_vars()
    assert names and "ANTHROPIC_API_KEY" in names


@pytest.mark.parametrize("var", sorted(isolation.provider_env_vars()))
def test_server_env_drops_every_provider_credential(var, tmp_path):
    env = isolation.server_env({"PATH": "/usr/bin", var: "secret"}, tmp_path, ROOT)
    assert var not in env


def _fake_claude_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return str(d)


def test_server_env_removes_the_directory_holding_claude_from_path(tmp_path):
    import shutil

    d = _fake_claude_dir(tmp_path)
    env = isolation.server_env({"PATH": f"{d}:/usr/bin"}, tmp_path / "sb", ROOT)
    assert d not in env["PATH"].split(":")
    assert "/usr/bin" in env["PATH"].split(":")
    assert shutil.which("claude", path=env["PATH"]) is None


def test_isolation_problems_flag_a_planted_key_and_a_resolvable_claude(tmp_path):
    env = isolation.server_env({"PATH": "/usr/bin"}, tmp_path, ROOT)
    assert isolation.isolation_problems(env, tmp_path, ROOT) == []
    keyed = {**env, "ANTHROPIC_API_KEY": "x"}
    assert any("ANTHROPIC_API_KEY" in p for p in isolation.isolation_problems(keyed, tmp_path, ROOT))
    d = _fake_claude_dir(tmp_path)
    leaky = {**env, "PATH": d}
    assert any("claude" in p for p in isolation.isolation_problems(leaky, tmp_path, ROOT))


def test_coach_argv_preapproves_exactly_the_expected_tools(tmp_path):
    from scripts.coach_eval import run

    args = run.coach_args("hi", tmp_path / "m.json")
    allowed = set(args[args.index("--allowedTools") + 1].split(","))
    assert allowed == run.COACH_EXPECTED_TOOLS


def _stub_claude(monkeypatch, replies):
    """Patch subprocess.run; `replies(args, input)` returns the stdout lines to emit."""
    import subprocess
    from types import SimpleNamespace

    seen = []

    def fake(cmd, **kw):
        seen.append((cmd, kw))
        return SimpleNamespace(stdout="\n".join(json.dumps(e) for e in replies(cmd, kw)))

    monkeypatch.setattr(subprocess, "run", fake)
    return seen


def test_a_long_prompt_reaches_claude_on_stdin_not_argv(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    seen = _stub_claude(monkeypatch, lambda c, k: [{"type": "result", "result": "tangerine"}])
    big = "x" * 30000
    out = run._claude(["-p"], tmp_path, prompt=big)
    cmd, kw = seen[0]
    assert kw["input"] == big and big not in " ".join(cmd)
    assert run._final_text(out) == "tangerine"


def test_the_final_result_text_wins_over_a_preamble_block():
    from scripts.coach_eval import run

    events = [{"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I'm ready."}]}}, {"type": "result", "result": "the answer"}]
    assert run._final_text(events) == "the answer"


def test_a_grader_that_does_not_reply_with_json_marks_the_scorecard_failed(
        monkeypatch, tmp_path):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init]
        if kw["input"].startswith("Grade"):
            return [{"type": "result", "result": "I'm ready."}]
        return [{"type": "result", "result": "DONE"}]

    _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    card = run.run_persona(p, tmp_path)
    assert card["failure"] == "the grader did not reply with JSON"
    assert (tmp_path / f"{p.id}.events.jsonl").exists()


@pytest.mark.parametrize("coach_calls,reached,save_result", [
    ((rubric.STATUS,), False, "not exercised"),
    ((rubric.STATUS, rubric.SAVE), True, "pass"),
])
def test_the_scorecard_says_whether_setup_save_was_reached(
        monkeypatch, tmp_path, coach_calls, reached, save_result):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    coach_turns = []

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            # The scripted calls happen once, on the first coach turn; a later turn (the
            # persona's focus message) only talks, so each call is counted exactly once.
            coach_turns.append(cmd)
            if len(coach_turns) > 1:
                return [init]
            return [init, *_flat(*[_use(n, [_SOURCED] if n == rubric.SAVE else None)
                                   for n in coach_calls])]
        if kw["input"].startswith("Grade"):
            return [{"type": "result", "result": "{}"}]
        return [{"type": "result", "result": "DONE"}]

    seen = _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    card = run.run_persona(p, tmp_path)
    assert card["setup_save_reached"] is reached
    # the same fact beside the grade, and in the prompt the grader actually received
    assert card["llm_graded"]["setup_save_reached"] is reached
    grader_input = next(kw["input"] for _, kw in seen
                        if (kw.get("input") or "").startswith("Grade"))
    if reached:
        assert run.NO_SAVE_FACT not in grader_input
        assert "successfully 1 time(s)" in grader_input and "example.invalid" in grader_input
    else:
        assert run.NO_SAVE_FACT in grader_input
    results = {k: v["result"] for k, v in card["deterministic"].items()}
    assert {k: results[k] for k in _SAVE_CHECKS} == dict.fromkeys(_SAVE_CHECKS, save_result)
    assert results["no_tool_denied"] == "pass" and results["turns"] == "pass"
    assert json.loads((tmp_path / f"{p.id}.scorecard.json").read_text()) == card


def test_the_grader_prompt_scores_setup_only_from_calls_that_happened():
    from scripts.coach_eval import run

    for fact in (run.NO_SAVE_FACT, run.save_fact(_flat(_use(rubric.SAVE, [_SOURCED])))):
        text = run.GRADER_PROMPT.format(save_fact=fact, transcript="COACH: hi")
        assert fact in text and text.endswith("COACH: hi")
        assert 'write "not exercised" for it' in text
        assert "ONLY from setup_save calls that actually happened" in text
    # a DENIED save is not a call that happened
    assert run.save_fact(_flat(_use(rubric.SAVE, [_SOURCED], denied=True))) == (
        run.NO_SAVE_FACT)


def test_a_grader_value_for_setup_save_reached_is_overwritten_by_the_fact(
        monkeypatch, tmp_path):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init]
        if kw["input"].startswith("Grade"):
            return [{"type": "result", "result": '{"setup_save_reached": true}'}]
        return [{"type": "result", "result": "DONE"}]

    _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    assert run.run_persona(p, tmp_path)["llm_graded"]["setup_save_reached"] is False


@pytest.mark.parametrize("coach_calls,expected", [
    ((rubric.STATUS,), "not exercised"),
    ((rubric.STATUS, rubric.SAVE), 4),
])
def test_asked_before_proposing_is_not_exercised_without_a_save_whatever_the_grader_says(
        monkeypatch, tmp_path, coach_calls, expected):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init, *_flat(*[_use(n, [_SOURCED] if n == rubric.SAVE else None)
                                   for n in coach_calls])]
        if kw["input"].startswith("Grade"):
            return [{"type": "result", "result": json.dumps(
                {"asked_before_proposing": 4, "role_specific_questions": 3,
                 "coaching_quality": 3, "notes": "n"})}]
        return [{"type": "result", "result": "DONE"}]

    _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    llm = run.run_persona(p, tmp_path)["llm_graded"]
    assert llm["asked_before_proposing"] == expected
    assert llm["role_specific_questions"] == 3 and llm["coaching_quality"] == 3


# --- regrade: re-grade a saved run with a chosen grader model -------------------------------

_GRADE = {"asked_before_proposing": 4, "role_specific_questions": 3,
          "coaching_quality": 3, "notes": "n"}


def _saved_run(tmp_path, calls, pid="saved-persona"):
    """A saved run as a live one leaves it: events.jsonl and transcript.txt, built from tools."""
    events = _flat(*[_use(n, [_SOURCED] if n == rubric.SAVE else None) for n in calls])
    transcript = ["COACH: hello", "USER: hi", "COACH: bye", "USER: DONE"]
    (tmp_path / f"{pid}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (tmp_path / f"{pid}.transcript.txt").write_text("\n".join(transcript))
    return pid, events, transcript


def _grader_stub(monkeypatch):
    return _stub_claude(monkeypatch, lambda c, k: [
        {"type": "result", "result": json.dumps(_GRADE)}])


def test_regrade_writes_the_expected_file_and_spends_only_the_grader(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS, rubric.SAVE))
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(tmp_path), "--grader-model", "sonnet"])
    assert len(seen) == 1
    card = json.loads((tmp_path / f"{pid}.regrade-sonnet.json").read_text())
    assert card["persona"] == pid and card["grader_model"] == "sonnet"
    assert card["llm_graded"]["asked_before_proposing"] == 4
    assert card["llm_graded"]["setup_save_reached"] is True


def test_a_regrade_sends_the_prompt_a_live_run_would(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    _, events, transcript = _saved_run(tmp_path, (rubric.STATUS, rubric.SAVE))
    seen = _grader_stub(monkeypatch)
    run.regrade(tmp_path, "haiku")
    live = run.GRADER_PROMPT.format(save_fact=run.save_fact(events),
                                    transcript="\n".join(transcript))
    assert seen[0][1]["input"] == live


def test_the_not_exercised_overwrite_applies_in_a_regrade(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS,))
    _grader_stub(monkeypatch)
    run.regrade(tmp_path, "haiku")
    llm = json.loads((tmp_path / f"{pid}.regrade-haiku.json").read_text())["llm_graded"]
    assert llm["asked_before_proposing"] == "not exercised"
    assert llm["setup_save_reached"] is False


@pytest.mark.parametrize("runner", ["setup", "prep"])
def test_grader_model_reaches_the_argv_and_defaults_to_sonnet(monkeypatch, tmp_path, runner):
    # Owner's decision (2026-10-07), after regrading runs 2-5 with both: the default grader is
    # Sonnet. The explicit flag is a DIFFERENT model, so the default is what the second call shows.
    from scripts.coach_eval import run

    if runner == "setup":
        _saved_run(tmp_path, (rubric.STATUS,))
    else:
        _saved_prep_run(tmp_path, [])
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(tmp_path), "--grader-model", "haiku"])
    run.main(["--regrade", str(tmp_path)])
    models = [cmd[cmd.index("--model") + 1] for cmd, _ in seen]
    assert models == ["haiku", "sonnet"] and run.GRADER_MODEL == "sonnet"


def test_print_grader_prompt_writes_the_exact_prompt_and_calls_nothing(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, events, transcript = _saved_run(tmp_path, (rubric.STATUS, rubric.SAVE))
    _saved_run(tmp_path, (rubric.STATUS,), pid="second")
    seen = _grader_stub(monkeypatch)
    run.main(["--print-grader-prompt", str(tmp_path)])
    assert seen == []  # no claude call of any kind
    text = (tmp_path / f"{pid}.grade-me.txt").read_text()
    assert text == run.grader_prompt(events, transcript)
    assert text.startswith("Grade this") and text.endswith("USER: DONE")
    assert "successfully 1 time(s)" in text
    assert run.NO_SAVE_FACT in (tmp_path / "second.grade-me.txt").read_text()


def test_the_printed_prompt_is_the_one_the_grader_is_sent(monkeypatch, tmp_path):
    # The cross-check is only worth anything if the pasted prompt IS the grader's prompt.
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS, rubric.SAVE))
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(tmp_path)])
    run.main(["--print-grader-prompt", str(tmp_path)])
    assert seen[0][1]["input"] == (tmp_path / f"{pid}.grade-me.txt").read_text()


def test_print_grader_prompt_refuses_a_directory_with_no_saved_run(tmp_path):
    from scripts.coach_eval import run

    with pytest.raises(SystemExit, match="no \\*.transcript.txt"):
        run.main(["--print-grader-prompt", str(tmp_path)])


def test_regrade_is_exclusive_with_persona_and_all(tmp_path):
    from scripts.coach_eval import run

    for other in (["--persona", "x"], ["--all"]):
        with pytest.raises(SystemExit):
            run.main(["--regrade", str(tmp_path), *other])
    for other in (["--persona", "x"], ["--all"], ["--regrade", str(tmp_path)]):
        with pytest.raises(SystemExit):
            run.main(["--print-grader-prompt", str(tmp_path), *other])
    # The prep runner's selectors join the same group: one mode per invocation.
    prep_modes = (["--prep-persona", "x"], ["--prep-all"])
    others = (["--persona", "x"], ["--all"], ["--regrade", str(tmp_path)],
              ["--print-grader-prompt", str(tmp_path)])
    for mode in prep_modes:
        for other in (*others, *[m for m in prep_modes if m != mode]):
            with pytest.raises(SystemExit) as raised:
                run.main([*mode, *other])
            assert raised.value.code == 2   # argparse's usage error, not a run that started


def test_regrade_removes_its_sandbox_and_print_grader_prompt_makes_none(monkeypatch, tmp_path):
    import tempfile as _tempfile
    from scripts.coach_eval import run

    runs = tmp_path / "runs"
    runs.mkdir()
    _saved_run(runs, (rubric.STATUS,))
    made = []
    real_td, real_mk = _tempfile.TemporaryDirectory, _tempfile.mkdtemp

    def td(suffix=None, prefix=None, dir=None, **k):
        t = real_td(suffix, prefix, dir or tmp_path, **k)
        made.append(Path(t.name))
        return t

    def mk(suffix=None, prefix=None, dir=None):
        d = real_mk(suffix, prefix, dir or tmp_path)
        made.append(Path(d))
        return d

    monkeypatch.setattr(_tempfile, "TemporaryDirectory", td)
    monkeypatch.setattr(_tempfile, "mkdtemp", mk)
    _grader_stub(monkeypatch)
    run.main(["--regrade", str(runs)])
    assert made and not any(p.exists() for p in made)
    made.clear()
    run.main(["--print-grader-prompt", str(runs)])
    assert made == []


@pytest.mark.parametrize("runner", ["setup", "prep"])
def test_a_live_runs_grader_model_flag_reaches_the_grader_argv(monkeypatch, tmp_path, runner):
    from scripts.coach_eval import run

    if runner == "setup":
        p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
        tools, flag = run.COACH_EXPECTED_TOOLS, "--persona"
    else:
        from scripts.coach_eval import prep
        p = _prep_personas()[0]
        tools, flag = prep.expected_tools(p.write), "--prep-persona"
    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(tools)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init]
        if (kw.get("input") or "").startswith("Grade"):
            return [{"type": "result", "result": json.dumps(_GRADE)}]
        return [{"type": "result", "result": "DONE"}]

    seen = _stub_claude(monkeypatch, reply)
    run.main([flag, p.id, "--grader-model", "opus", "--out", str(tmp_path / "out")])
    graders = [cmd for cmd, kw in seen if (kw.get("input") or "").startswith("Grade")]
    assert len(graders) == 1 and graders[0][graders[0].index("--model") + 1] == "opus"
    # --out reached the runner too: the scorecard is where the flag said.
    card = json.loads((tmp_path / "out" / f"{p.id}.scorecard.json").read_text())
    assert card.get("runner", "setup") == runner


def test_brief_cites_sources_is_checked_per_save_call_carrying_a_brief():
    """One sourced brief early in the run must not cover an unsourced brief proposed later."""
    events = _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_SOURCED]),
                   _use(rubric.SAVE, [_UNSOURCED]))
    assert rubric.deterministic(events, max_turns=30)["brief_cites_sources"][0] is False
    both = _flat(_use(rubric.STATUS), _use(rubric.SAVE, [_SOURCED]),
                 _use(rubric.SAVE, [_UNSOURCED, _SOURCED]))
    assert rubric.deterministic(both, max_turns=30)["brief_cites_sources"][0] is True


def test_the_sources_section_is_the_tools_own_last_brief_section():
    from sluice.onboard.review import ROLE_BRIEF_SECTIONS
    assert rubric.SOURCES_SECTION == ROLE_BRIEF_SECTIONS[-1] == _SOURCED["target"]


def test_two_persona_seeds_give_two_names():
    """Comparing one call with itself passes even if the seed is ignored."""
    import dataclasses
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    other = dataclasses.replace(p, name_seed=p.name_seed + 1)
    assert personas.persona_name(p) == personas.persona_name(p)
    assert personas.persona_name(p) != personas.persona_name(other)


def test_a_clean_vault_env_server_env_has_no_problems(tmp_path):
    """The vault-env persona's sandbox SETS VAULT_DIR; without `| {"VAULT_DIR"}` in the
    must-set roster it would be reported as a variable that must not be set."""
    env = isolation.server_env({"PATH": "/usr/bin"}, tmp_path, ROOT, vault_env=True)
    assert env["VAULT_DIR"].startswith(str(tmp_path))
    assert isolation.isolation_problems(env, tmp_path, ROOT, vault_env=True) == []


@pytest.mark.parametrize("var", sorted(set(isolation.SET_VARS) | {"VAULT_DIR"}))
def test_each_variable_that_must_be_set_is_reported_when_missing(tmp_path, var):
    env = isolation.server_env({"PATH": "/usr/bin"}, tmp_path, ROOT, vault_env=True)
    del env[var]
    assert f"{var} is not set" in isolation.isolation_problems(env, tmp_path, ROOT,
                                                               vault_env=True)


def test_a_vault_env_pointing_outside_the_sandbox_is_reported(tmp_path):
    env = isolation.server_env({"PATH": "/usr/bin"}, tmp_path / "sb", ROOT, vault_env=True)
    env["VAULT_DIR"] = str(tmp_path / "elsewhere")
    assert "VAULT_DIR points outside the sandbox" in isolation.isolation_problems(
        env, tmp_path / "sb", ROOT, vault_env=True)


@pytest.mark.parametrize("flag", ["--regrade", "--print-grader-prompt"])
def test_saved_run_modes_refuse_a_directory_inside_the_repository(flag):
    """They write regrade cards and grader prompts beside the saved runs, and those carry live
    transcripts, so the same refusal as the live run's --out applies. Hermetic: the refusal
    comes before any read, write or `claude` call."""
    from scripts.coach_eval import run

    with pytest.raises(SystemExit, match="outside the repository"):
        run.main([flag, str(ROOT / "scripts")])


def _sandbox_probe(monkeypatch, init):
    """Stub `_claude` and record the per-persona sandbox it was run in (the client cwd's parent)."""
    from scripts.coach_eval import run
    seen = []

    def fake(args, cwd, prompt=None):
        seen.append(Path(cwd).parent)
        return [init] if "--mcp-config" in args else []

    monkeypatch.setattr(run, "_claude", fake)
    return seen


def test_the_persona_sandbox_is_removed_after_a_run(monkeypatch, tmp_path):
    from scripts.coach_eval import run
    init = {"subtype": "init", "session_id": "s", "claude_code_version":
            next(iter(isolation.MEASURED_VERSIONS)), "plugins": [],
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}
    seen = _sandbox_probe(monkeypatch, init)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    run.run_persona(p, tmp_path)
    assert seen and not seen[0].exists()
    assert (tmp_path / f"{p.id}.scorecard.json").exists()    # scorecards outlive the sandbox


def test_the_persona_sandbox_is_removed_when_the_isolation_check_fails(monkeypatch, tmp_path):
    from scripts.coach_eval import run
    seen = _sandbox_probe(monkeypatch, {"subtype": "init", "tools": ["Bash"]})
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    with pytest.raises(SystemExit, match="isolation check failed"):
        run.run_persona(p, tmp_path)
    assert seen and not seen[0].exists()


# ── the chat-yes rule (spec 2026-10-08): a save answers a user turn, after a playback ──

_SAVED = [{"kind": "config", "target": "lead_ttl_days", "value": "30"},
          {"kind": "search", "target": "example-board", "label": "Example",
           "url": "https://example.invalid/s"},
          {"kind": "profile", "target": "Who this candidate is", "clear": True}]
_PLAYBACK = ("Config: lead_ttl_days 30. Search on example-board: https://example.invalid/s. "
             "Who this candidate is: back to the default. Shall I save?")


def _chat_yes(events):
    out = rubric.deterministic(events, max_turns=40)
    return out["save_after_user_turn"][0], out["saves_played_back"][0]


def test_a_save_after_a_playback_and_a_user_turn_passes_both_checks():
    events = _flat(_start(), _use(rubric.STATUS), _say(_PLAYBACK),
                   _start(), _use(rubric.SAVE, _SAVED))
    assert _chat_yes(events) == (True, True)


def test_a_save_in_the_coachs_first_message_has_no_user_turn_before_it():
    events = _flat(_start(), _say(_PLAYBACK), _use(rubric.SAVE, _SAVED))
    assert _chat_yes(events) == (False, False)


def test_a_save_made_in_the_same_message_as_its_playback_was_not_played_back_first():
    """The user cannot have said yes to text that arrives in the same message as the save."""
    events = _flat(_start(), _say("Tell me about the role."),
                   _start(), _say(_PLAYBACK), _use(rubric.SAVE, _SAVED))
    assert _chat_yes(events) == (True, False)
    assert rubric.unplayed_changes(events) == _SAVED


def test_one_value_missing_from_the_playback_is_named():
    events = _flat(_start(), _say(_PLAYBACK.replace("30", "thirty")),
                   _start(), _use(rubric.SAVE, _SAVED))
    assert rubric.unplayed_changes(events) == [_SAVED[0]]


_SECTION = [{"kind": "profile", "target": "Win patterns and anti-patterns",
             "value": "Clear yes: a role with a salary band shown.\nClear no: a role with none."}]


def test_a_multi_line_value_played_back_as_a_quote_counts_as_played_back():
    """The coach quotes a multi-line section as a Markdown blockquote, one `> ` per line; the
    markers are not part of the value and must not break the match."""
    quoted = "Here it is:\n> Clear yes: a role with a salary band shown.\n> Clear no: a role with none."
    events = _flat(_start(), _say(quoted), _start(), _use(rubric.SAVE, _SECTION))
    assert rubric.unplayed_changes(events) == []


def test_a_quoted_section_whose_words_changed_is_still_not_played_back():
    quoted = "> Clear yes: a role with a pay band shown.\n> Clear no: a role with none."
    events = _flat(_start(), _say(quoted), _start(), _use(rubric.SAVE, _SECTION))
    assert rubric.unplayed_changes(events) == _SECTION


_LIST = [{"kind": "config", "target": "target_locations",
          "value": ["Example Town", "Remote, Example Region"]}]


def test_a_list_value_played_back_item_by_item_counts_as_played_back():
    """A list setting is sent as a JSON list; the coach plays back each item, never the Python
    repr of the list, so each item is what must appear."""
    played = "Locations:\n- Example Town\n- Remote, Example Region\nShall I save?"
    events = _flat(_start(), _say(played), _start(), _use(rubric.SAVE, _LIST))
    assert rubric.unplayed_changes(events) == []


def test_a_list_value_with_one_item_left_out_of_the_playback_is_not_played_back():
    played = "Locations:\n- Example Town\nShall I save?"
    events = _flat(_start(), _say(played), _start(), _use(rubric.SAVE, _LIST))
    assert rubric.unplayed_changes(events) == _LIST


def test_the_chat_yes_checks_are_not_exercised_without_a_successful_save():
    events = _flat(_start(), _say(_PLAYBACK), _use(rubric.SAVE, _SAVED, denied=True))
    assert _chat_yes(events) == (rubric.NOT_EXERCISED, rubric.NOT_EXERCISED)


def test_the_files_a_run_left_are_copied_with_their_paths(tmp_path):
    from scripts.coach_eval import run
    sandbox, dest = tmp_path / "sandbox", tmp_path / "out"
    for rel, text in (("home/vault/Job Applications/Role Brief.md", "brief"),
                      ("config/sluice/config.yaml", "lead_ttl_days: 30\n"),
                      ("client-cwd/stray.md", "not the run's"),
                      ("state/sluice/seen.db", "binary")):
        (sandbox / rel).parent.mkdir(parents=True, exist_ok=True)
        (sandbox / rel).write_text(text)
    copied = run.copy_setup_files(sandbox, dest)
    assert copied == ["config/sluice/config.yaml", "home/vault/Job Applications/Role Brief.md"]
    assert (dest / "home/vault/Job Applications/Role Brief.md").read_text() == "brief"
    assert not (dest / "client-cwd").exists() and not (dest / "state").exists()


def test_a_denied_save_does_not_count_against_the_chat_yes_checks():
    """A save the client denied never happened: here one in the coach's first message, before
    any playback, would fail both checks if it were counted beside the real save that follows."""
    events = _flat(_start(), _use(rubric.SAVE, _SAVED, denied=True), _say(_PLAYBACK),
                   _start(), _use(rubric.SAVE, _SAVED))
    assert _chat_yes(events) == (True, True)


def test_the_simulated_user_fills_in_its_own_situation_and_never_refuses_it():
    """Run 8: told never to invent preferences beyond its situation, the simulated user refused
    to name any role, history or place, and the coach could do nothing. The prompt now says the
    situation is a sketch to fill in consistently, and still lets a preference go unanswered."""
    from scripts.coach_eval import run

    text = " ".join(run.USER_PROMPT.split())
    for phrase in ("Your situation below is a sketch.",
                   "fill in concrete details that fit it",
                   "keep to the details you have given for the whole conversation",
                   "Never refuse to answer about your own situation.",
                   "On a preference you have no view on, you may say so.",
                   "answer yes or no as this person would; that question is not the end.",
                   "Reply DONE only after the coach has told you what was saved",
                   "name a real country of your choice and keep to it"):
        assert phrase in text, phrase
    assert "Never invent preferences" not in text
    filled = run.USER_PROMPT.format(name="N", location="L", situation="S", transcript="T")
    assert "Your name: N" in filled and "Your situation: S" in filled


# ── scenarios (personas.SCENARIOS): scripted events the harness plays and the rubric scores ──

_SCENARIO_CHECKS = ("no_save_on_maybe", "saved_after_yes", "stale_reported",
                    "status_reread_after_stale", "edit_survives", "retired_not_written")


def _tool(name, inp=None, result=None, *, denied=False):
    """A tool call answered with `result` as the JSON text an MCP tool's reply carries."""
    tid = f"toolu_{next(_N)}"
    return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": tid, "name": name, "input": inp or {}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "is_error": denied,
                 "content": [{"type": "text", "text": json.dumps(result or {})}]}]}}]


def _status(version):
    return _tool(rubric.STATUS, None, {"version": version})


def _save(version, outcome="completed", rows=()):
    return _tool(rubric.SAVE, {"changes": [], "version": version},
                 {"outcome": outcome, "changes": list(rows)})


def _sc(scenario, events, **kw):
    return {k: v[0] for k, v in rubric.scenario_checks(scenario, events, **kw).items()}


def test_every_scenario_check_is_on_every_card_and_not_exercised_without_its_scenario():
    events = _flat(_start(), _status("v1"), _start(), _save("v1", "stale"), _start(),
                   _save("v1"))
    out = _sc(None, events, user_messages=[personas.HESITANT_LINE, "the board"],
              edit={"invocation": 0, "note_text": ""}, board="the board")
    assert out == dict.fromkeys(_SCENARIO_CHECKS, rubric.NOT_EXERCISED)


# hesitant: user message k is answered by coach invocation k + 1
_MAYBE_MSGS = ["fine", personas.HESITANT_LINE, "Yes."]


def _hesitant_events(save_in):
    """Four coach invocations; a completed save in the invocation(s) named by `save_in`."""
    return _flat(*[_flat(_start(), _say("..."), *([_save("v")] if i in save_in else []))
                   for i in range(4)])


def test_hesitant_passes_when_the_save_waits_for_the_second_ask():
    out = _sc("hesitant", _hesitant_events({3}), user_messages=_MAYBE_MSGS)
    assert (out["no_save_on_maybe"], out["saved_after_yes"]) == (True, True)


def test_a_save_in_the_message_answering_maybe_fails_no_save_on_maybe():
    out = _sc("hesitant", _hesitant_events({2, 3}), user_messages=_MAYBE_MSGS)
    assert out["no_save_on_maybe"] is False and out["saved_after_yes"] is True


def test_a_maybe_never_followed_by_a_completed_save_fails_saved_after_yes():
    stale_only = _flat(_hesitant_events(set()), _save("v", "stale"))
    out = _sc("hesitant", stale_only, user_messages=_MAYBE_MSGS)
    assert out["no_save_on_maybe"] is True and out["saved_after_yes"] is False
    # a save BEFORE the maybe does not count as saving after it
    out = _sc("hesitant", _hesitant_events({1}), user_messages=_MAYBE_MSGS)
    assert out["saved_after_yes"] is False


def test_hesitant_checks_are_not_exercised_until_the_user_says_maybe():
    out = _sc("hesitant", _hesitant_events({2}), user_messages=["fine", "yes", "DONE"])
    assert (out["no_save_on_maybe"], out["saved_after_yes"]) == (rubric.NOT_EXERCISED,) * 2
    # a maybe the run ended on was answered by no coach message
    out = _sc("hesitant", _hesitant_events(set())[:4],
             user_messages=["a", personas.HESITANT_LINE])  # invocations 0 and 1 only
    assert out["no_save_on_maybe"] is rubric.NOT_EXERCISED


# edited_mid_session: the harness edits the note after invocation 0
_EDITED = {"invocation": 0, "note_text": f"# Judging Profile\n{personas.EDIT_MARKER}\n"}


def test_edited_mid_session_passes_on_stale_then_reread_then_save():
    events = _flat(_start(), _status("v1"), _start(), _save("v1", "stale"), _status("v2"),
                   _start(), _save("v2"))
    out = _sc("edited_mid_session", events, edit=_EDITED)
    assert [out[k] for k in ("stale_reported", "status_reread_after_stale",
                             "edit_survives")] == [True, True, True]


def test_a_save_with_the_pre_edit_version_that_was_not_stale_fails_stale_reported():
    events = _flat(_start(), _status("v1"), _start(), _save("v1"))
    assert _sc("edited_mid_session", events, edit=_EDITED)["stale_reported"] is False


def test_saving_again_after_stale_without_rereading_status_fails():
    events = _flat(_start(), _status("v1"), _start(), _save("v1", "stale"), _save("v1"),
                   _status("v2"))
    assert _sc("edited_mid_session", events, edit=_EDITED)[
        "status_reread_after_stale"] is False
    # and with no save after the stale result at all, a re-read must still happen
    events = _flat(_start(), _status("v1"), _start(), _save("v1", "stale"))
    assert _sc("edited_mid_session", events, edit=_EDITED)[
        "status_reread_after_stale"] is False


@pytest.mark.parametrize("text", [None, "# Judging Profile\n"])
def test_a_lost_hand_edit_fails_edit_survives(text):
    out = _sc("edited_mid_session", [], edit={"invocation": 0, "note_text": text})
    assert out["edit_survives"] is False


def test_edited_checks_are_not_exercised_without_their_subject():
    events = _flat(_start(), _status("v1"), _start(), _save("v1", "stale"), _start(),
                   _save("v1"))
    assert _sc("edited_mid_session", events, edit=None) == dict.fromkeys(
        _SCENARIO_CHECKS, rubric.NOT_EXERCISED)
    # a coach that re-read status before its first save after the edit was never due a
    # stale result; and with no stale result, there is no re-read to check
    reread = _flat(_start(), _status("v1"), _start(), _status("v2"), _save("v2"))
    out = _sc("edited_mid_session", reread, edit=_EDITED)
    assert out["stale_reported"] is rubric.NOT_EXERCISED
    assert out["status_reread_after_stale"] is rubric.NOT_EXERCISED
    assert out["edit_survives"] is True


# retired_board
_BOARD = "exampleboard"


def _row(change, outcome):
    return {"change": change, "outcome": outcome, "reason": "r"}


def test_retired_board_passes_when_the_search_is_set_aside_and_the_config_is_clean():
    events = _flat(_start(), _status("v"), _start(), _save("v", rows=[
        _row(f"search:{_BOARD}:Example:https://example.invalid/s", "set_aside")]))
    out = _sc("retired_board", events, user_messages=[f"use {_BOARD}"], board=_BOARD,
              config_text=f"vault_dir: ~/notes\nnegatives: not {_BOARD}board stuff\n")
    assert out["retired_not_written"] is True


def test_a_written_search_on_the_retired_board_fails():
    events = _flat(_start(), _save("v", rows=[
        _row(f"search:{_BOARD}:Example:https://example.invalid/s", "written")]))
    assert _sc("retired_board", events, user_messages=[f"use {_BOARD}"], board=_BOARD,
               config_text="")["retired_not_written"] is False


def test_a_config_naming_the_retired_board_fails():
    config = f"sources:\n  {_BOARD}:\n    enabled: true\n"
    assert _sc("retired_board", [], user_messages=[f"use {_BOARD}"], board=_BOARD,
               config_text=config)["retired_not_written"] is False


def test_retired_board_is_not_exercised_until_the_board_is_named():
    out = _sc("retired_board", _flat(_start(), _save("v")), user_messages=["hello"],
              board=_BOARD, config_text=f"sources:\n  {_BOARD}: {{}}\n")
    assert out["retired_not_written"] is rubric.NOT_EXERCISED


def _scenario_persona(pid):
    return next(p for p in personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
                if p.id == pid)


def test_each_scenario_has_one_persona_and_plain_personas_have_none():
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    assert sorted(p.scenario for p in ps if p.scenario) == sorted(personas.SCENARIOS)
    assert {p.id for p in ps if not p.scenario} >= {"career-changer", "vault-env"}
    # The owner's budget ruling is 12 coach messages; scenario personas take 20 because a scenario
    # adds turns of its own and all three stopped short of their subject at 12 (README).
    assert all(p.max_turns == (20 if p.scenario else 12) for p in ps)


def test_an_unknown_scenario_is_refused():
    with pytest.raises(ValueError, match="unknown scenario"):
        personas.Persona(id="x", name_seed=1, location=LOCATIONS[0], situation="s",
                         scenario="hesitent")


def test_the_hesitant_users_prompt_carries_the_scripted_line_verbatim():
    from scripts.coach_eval import run
    p = _scenario_persona("hesitant")
    assert personas.HESITANT_LINE in run.user_prompt(p, ["COACH: hi"])
    plain = _scenario_persona("career-changer")
    assert personas.HESITANT_LINE not in run.user_prompt(plain, ["COACH: hi"])


def test_the_retired_board_is_the_registrys_first_disabled_one(monkeypatch):
    from types import SimpleNamespace

    from scripts.coach_eval import run
    from sluice.ingest import sources

    real = sources.all_sources()
    assert any(not s.enabled for s in real), "the real registry ships a disabled board"
    assert run.retired_board_id() == next(s.id for s in real if not s.enabled)
    fake = [SimpleNamespace(id="on", enabled=True), SimpleNamespace(id="off1", enabled=False),
            SimpleNamespace(id="off2", enabled=False)]
    monkeypatch.setattr(sources, "all_sources", lambda: fake)
    assert run.retired_board_id() == "off1"
    monkeypatch.setattr(sources, "all_sources", lambda: fake[:1])
    with pytest.raises(SystemExit, match="ships disabled"):
        run.retired_board_id()


def test_no_persona_file_names_a_board():
    """WHOLE-FILE over both runners' persona directories: every key and value, the prep JD
    templates and story bodies included, since a posting ("apply via ...") is the likeliest
    text to name a board. The ids are read from the registry at run time, never listed."""
    from sluice.ingest.sources import all_sources
    ids = {s.id.lower() for s in all_sources()}
    assert ids
    base = ROOT / "scripts" / "coach_eval" / "personas"
    files = {"setup": sorted(base.glob("*.json")), "prep": sorted((base / "prep").glob("*.json"))}
    # scope: the sweep read files from BOTH directories, so a moved or renamed prep directory
    # cannot leave the guard passing on nothing
    assert files["setup"] and files["prep"], files
    for f in files["setup"] + files["prep"]:
        words = set(re.findall(r"[a-z_]+", f.read_text(encoding="utf-8").lower()))
        assert not ids & words, (
            f"{f.name} names a board id {sorted(ids & words)}. Some ids are ordinary words: "
            "reword the persona, never narrow or change this guard.")


def _coach_init():
    from scripts.coach_eval import run
    return {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}


def test_the_retired_board_run_hands_the_registrys_board_to_the_user(monkeypatch, tmp_path):
    from scripts.coach_eval import run
    prompts = []

    def fake(args, cwd, prompt=None):
        if "--mcp-config" in args:
            return [_coach_init()]
        prompts.append(prompt or "")
        return [{"type": "result", "result": "{}" if (prompt or "").startswith("Grade")
                 else "DONE"}]

    monkeypatch.setattr(run, "_claude", fake)
    board = run.retired_board_id()
    card = run.run_persona(_scenario_persona("retired-board"), tmp_path)
    assert f"called {board}." in prompts[0]
    assert card["retired_board"] == board and card["scenario"] == "retired_board"
    assert card["deterministic"]["retired_not_written"]["result"] == "not exercised"


def test_the_hand_edit_lands_once_right_after_the_first_successful_status(
        monkeypatch, tmp_path):
    from scripts.coach_eval import run
    seen = []   # (coach invocation, the note's text when that invocation STARTED)

    def note(cwd):
        path = run.sandbox_vault(Path(cwd).parent, False).joinpath(*personas.EDIT_NOTE)
        return path.read_text() if path.exists() else None

    script = [[_coach_init(), *_say("hello"), *_tool(rubric.STATUS, denied=True)],
              [_coach_init(), *_status("v1")],
              [_coach_init(), *_status("v1")],
              [_coach_init(), *_say("bye")]]

    def fake(args, cwd, prompt=None):
        if "--mcp-config" in args:
            seen.append(note(cwd))
            return script[len(seen) - 1]
        if (prompt or "").startswith("Grade"):
            return [{"type": "result", "result": "{}"}]
        return [{"type": "result", "result": "DONE" if len(seen) == 4 else "ok"}]

    monkeypatch.setattr(run, "_claude", fake)
    card = run.run_persona(_scenario_persona("edited-mid-session"), tmp_path)
    marker = personas.EDIT_MARKER + "\n"
    # a DENIED status in invocation 0 does not trigger it; the success in 1 does, after it ran
    assert seen == [None, None, marker, marker]
    copied = tmp_path / "edited-mid-session.files" / "server-cwd" / "vault" / Path(
        *personas.EDIT_NOTE)
    assert copied.read_text() == marker
    det = {k: v["result"] for k, v in card["deterministic"].items()}
    assert det["edit_survives"] == "pass" and det["stale_reported"] == "not exercised"


def test_a_plain_persona_run_never_hand_edits(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    def fake(args, cwd, prompt=None):
        if "--mcp-config" in args:
            return [_coach_init(), *_status("v1")]
        return [{"type": "result", "result": "{}" if (prompt or "").startswith("Grade")
                 else "DONE"}]

    monkeypatch.setattr(run, "_claude", fake)
    card = run.run_persona(_scenario_persona("career-changer"), tmp_path)
    assert card["files"] == [] and "scenario" not in card
    assert all(card["deterministic"][k]["result"] == "not exercised" for k in _SCENARIO_CHECKS)


def test_the_hand_edit_changes_the_version_the_real_server_reports(monkeypatch, tmp_path):
    """The scenario only tests anything if the edit lands where the sandboxed server looks:
    resolved by serve.py's own environment, the version must move."""
    from scripts.coach_eval import run
    from sluice import mcpserver

    for k in isolation.unset_vars(ROOT):
        monkeypatch.delenv(k, raising=False)
    for k, v in isolation.server_env({"PATH": "/usr/bin"}, tmp_path, ROOT).items():
        monkeypatch.setenv(k, v)
    (tmp_path / "server-cwd").mkdir()
    monkeypatch.chdir(tmp_path / "server-cwd")
    before = mcpserver.setup_status_or_refusal()["version"]
    run.hand_edit(tmp_path, False)
    assert mcpserver.setup_status_or_refusal()["version"] != before


def test_the_sandbox_vault_follows_the_servers_precedence_and_stays_inside(tmp_path):
    from scripts.coach_eval import run
    assert run.sandbox_vault(tmp_path, True) == tmp_path / "env-vault"
    assert run.sandbox_vault(tmp_path, False) == tmp_path / "server-cwd" / "vault"
    (tmp_path / "config").mkdir()
    cfg = tmp_path / "config" / "config.yaml"
    cfg.write_text("vault_dir: ~/notes\n")
    assert run.sandbox_vault(tmp_path, False) == tmp_path / "home" / "notes"
    assert run.sandbox_vault(tmp_path, True) == tmp_path / "env-vault"   # VAULT_DIR wins
    cfg.write_text(f"vault_dir: {tmp_path.parent / 'elsewhere'}\n")
    with pytest.raises(SystemExit, match="outside the sandbox"):
        run.sandbox_vault(tmp_path, False)


def test_the_simulated_user_and_the_grader_replace_claude_codes_agent_prompt(monkeypatch, tmp_path):
    """With Claude Code's own agent system prompt, the simulated user drifted into replying as a
    coding assistant and the job seeker vanished. Both non-coach roles pass `--system-prompt` (which
    REPLACES that prompt); the coach does not, since a real user's coach runs with it."""
    from scripts.coach_eval import run

    init = {"subtype": "init", "session_id": "s", "claude_code_version":
            next(iter(isolation.MEASURED_VERSIONS)), "plugins": [],
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}
    calls = []

    def fake(args, cwd, prompt=None):
        calls.append(args)
        if "--mcp-config" in args:
            return [init]
        return []

    monkeypatch.setattr(run, "_claude", fake)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    run.run_persona(p, tmp_path)
    # Every coach call (the bare command, then the focus turn when the persona has one), then
    # the simulated user's empty reply and the grader.
    coaches = [a for a in calls if "--mcp-config" in a]
    user, grader = calls[-2:]

    def system(args):
        return args[args.index("--system-prompt") + 1] if "--system-prompt" in args else None

    assert coaches and all(system(c) is None for c in coaches)
    assert system(user) == run.USER_SYSTEM and system(grader) == run.GRADER_SYSTEM


def test_the_first_turn_is_the_bare_command_and_the_focus_is_the_next_message(
        monkeypatch, tmp_path):
    """Claude Code delivers only the first word of text after an MCP prompt command (measured,
    2026-10-09), so every eval used to run with a one-word focus. The command goes alone and
    the persona's focus is its own user message, verbatim."""
    from scripts.coach_eval import run

    init = {"subtype": "init", "session_id": "s", "claude_code_version":
            next(iter(isolation.MEASURED_VERSIONS)), "plugins": [],
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}
    coach_messages = []

    def fake(args, cwd, prompt=None):
        if "--mcp-config" in args:
            coach_messages.append(args[args.index("-p") + 1])
            return [init]
        return []  # the simulated user says nothing, which ends the loop

    monkeypatch.setattr(run, "_claude", fake)
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    p = next(x for x in ps if " " in x.focus)  # scope: a multi-word focus, the case that broke
    run.run_persona(p, tmp_path)
    assert coach_messages[:2] == ["/mcp__sluice__career_interview", p.focus]


# ── the prep coach's runner (#195): scripts/coach_eval/prep.py ─────────────────────────────
# Spec docs/superpowers/specs/2026-10-16-interview-prep-design.md, section 10 and Testing (PR 2),
# "The prep eval runner". Every prep_brief reply below is built by CALLING the real
# `mcpserver.prep_brief` (`_brief_call`) on a vault the real seeder filled, so the envelope keys
# (`outcome`, `brief`, `jd.text`) come from the server, never typed here. The other tools'
# replies are typed, because the rubric reads only their names and whether they errored.

_PREP_DIR = ROOT / "scripts" / "coach_eval" / "personas" / "prep"
_PREP_GRADE = {"gaps_probed": 4, "no_supplied_example": "yes", "unverified_handled": 4,
               "fidelity_feedback": 4, "coaching_quality": 3, "notes": "n"}
_FACT_SCORED = ("gaps_probed", "unverified_handled", "fidelity_feedback")


def _prep_personas():
    ps = personas.load_prep_personas(_PREP_DIR)
    # scope: every prep file loaded, and at least the three the spec names
    assert len(ps) == len(list(_PREP_DIR.glob("*.json"))) >= 3
    return ps


def _prep_where(pred):
    return next(p for p in _prep_personas() if pred(p))


def _with_unverified():
    return _prep_where(lambda p: any(not s["verified"] for s in p.stories))


def _all_verified():
    return _prep_where(lambda p: p.stories and all(s["verified"] for s in p.stories))


def _seeded(tmp_path, p):
    """`p` seeded by the REAL seeder into its own sandbox: (a facade over that vault, slug)."""
    from scripts.coach_eval import prep, run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.core.vault import Vault

    sandbox = tmp_path / f"sandbox-{p.id}"
    sandbox.mkdir()
    slug = prep.seed_vault(p, sandbox)
    return Sluice(Config(), store=Vault(str(run.sandbox_vault(sandbox, False)))), slug


def _brief_call(sluice, lead, *, denied=False):
    """One prep_brief tool call answered with the real module-level tool's reply."""
    from sluice import mcpserver
    return _tool(rubric.PREP, {"lead": lead}, mcpserver.prep_brief(sluice, lead), denied=denied)


def _fragment(slug):
    # The company half of the slug: a guess that misses and draws suggestions.
    return slug.split(" - ")[0]


def _prep_init(write):
    from scripts.coach_eval import prep
    return {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(prep.expected_tools(write))}


def _saved_prep_run(tmp_path, events, pid="prep-saved", runner="prep"):
    """A saved prep run as the live runner leaves it, scorecard included."""
    transcript = ["COACH: hello", "USER: hi", "COACH: bye", "USER: DONE"]
    (tmp_path / f"{pid}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (tmp_path / f"{pid}.transcript.txt").write_text("\n".join(transcript))
    (tmp_path / f"{pid}.scorecard.json").write_text(json.dumps({"runner": runner,
                                                                "persona": pid}))
    return pid, events, transcript


# ── setup is unchanged except where listed ──

def test_coach_args_with_no_keywords_is_the_argv_built_from_runs_own_constants(tmp_path):
    from scripts.coach_eval import run

    m = tmp_path / "m.json"
    built = ["--model", run.COACH_MODEL, "-p", "hi", "--mcp-config", str(m),
             "--tools", run.COACH_TOOLS, "--disallowedTools", run.COACH_DISALLOWED,
             "--allowedTools", ",".join(sorted(run.COACH_EXPECTED_TOOLS)),
             "--output-format", "stream-json", "--verbose"]
    assert run.coach_args("hi", m) == built
    assert run.coach_args("hi", m, "s") == built + ["--resume", "s"]


def test_the_setup_run_writes_its_mcp_config_with_todays_arguments(monkeypatch, tmp_path):
    import sys

    from scripts.coach_eval import run

    seen = []
    real = run.write_mcp_config

    def spy(sandbox, server_args):
        seen.append((Path(sandbox), list(server_args)))
        path = real(sandbox, server_args)
        seen.append(json.loads(path.read_text()))
        return path

    monkeypatch.setattr(run, "write_mcp_config", spy)
    monkeypatch.setattr(run, "_claude", lambda args, cwd, prompt=None: (
        [_coach_init()] if "--mcp-config" in args else []))
    ps = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    for p in (next(x for x in ps if not x.vault_env), next(x for x in ps if x.vault_env)):
        seen.clear()
        run.run_persona(p, tmp_path)
        (sandbox, args), written = seen
        assert args == ["--sandbox", str(sandbox)] + (["--vault-env"] if p.vault_env else [])
        assert written == {"mcpServers": {"sluice": {"command": sys.executable, "args": [
            str(ROOT / "scripts" / "coach_eval" / "serve.py"), *args]}}}


# ── one patch governs both runners ──

def test_one_patch_on_run_claude_governs_the_prep_runner(monkeypatch, tmp_path):
    """prep.py reaches `_claude` BY ATTRIBUTE on run, so this one patch intercepts it; a
    by-name import would call the real `_claude`, which spawns a process and trips the wire."""
    import subprocess

    from scripts.coach_eval import prep, run

    def tripwire(*_a, **_k):
        raise AssertionError("a claude process was spawned past the patch on run._claude")

    monkeypatch.setattr(subprocess, "run", tripwire)
    p = _prep_personas()[0]
    seen = []

    def fake(args, cwd, prompt=None):
        if "--mcp-config" in args:
            seen.append("coach")
            return [_prep_init(p.write), *_say("Which interview is it?")]
        if (prompt or "").startswith("Grade"):
            seen.append("grader")
            return [{"type": "result", "result": json.dumps(_PREP_GRADE)}]
        seen.append("user")
        return [{"type": "result", "result": "DONE"}]

    monkeypatch.setattr(run, "_claude", fake)
    card = prep.run_persona(p, tmp_path)
    assert seen == ["coach", "user", "grader"]
    assert card["runner"] == "prep" and card["persona"] == p.id
    assert json.loads((tmp_path / f"{p.id}.scorecard.json").read_text()) == card


def _prep_imports_in_run():
    """(enclosing function name, or None at module scope) for every import of prep in run.py.
    MODULE SCOPE is whatever runs when run.py is imported: the top level and any `if`/`try`/
    class body under it, but no function body, so the walk does not descend into one."""
    import ast

    def names_prep(node):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            return (mod.endswith("coach_eval.prep") or mod == "prep"
                    or (mod.endswith("coach_eval") or node.level) and any(
                        a.name == "prep" for a in node.names))
        return isinstance(node, ast.Import) and any(
            a.name.split(".")[-1] == "prep" for a in node.names)

    found = []

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk(child, getattr(child, "name", "<lambda>"))
            else:
                if names_prep(child):
                    found.append(func)
                walk(child, func)

    walk(ast.parse((ROOT / "scripts" / "coach_eval" / "run.py").read_text(encoding="utf-8")),
         None)
    return found


def test_run_imports_prep_only_inside_its_functions():
    """prep imports run at module scope, so run must reach prep only from inside a function,
    or the two form an import cycle. An import at the END of run.py works in either import
    order, silently, which is why this reads the source rather than importing anything."""
    found = _prep_imports_in_run()
    # Scope: the walk sees the function-local imports that do exist, so a matcher that found
    # nothing cannot pass.
    assert {"_grading", "main"} <= set(found), found
    assert None not in found, "run.py imports prep at module scope"


def test_the_prep_coach_starts_on_its_own_command_with_its_own_lists(monkeypatch, tmp_path):
    from scripts.coach_eval import prep, run

    for p in (_prep_where(lambda x: x.write), _prep_where(lambda x: not x.write)):
        coach = []

        def fake(args, cwd, prompt=None, p=p, coach=coach):
            if "--mcp-config" in args:
                coach.append(args)
                return [_prep_init(p.write)]
            return [{"type": "result", "result": "{}" if (prompt or "").startswith("Grade")
                     else "DONE"}]

        monkeypatch.setattr(run, "_claude", fake)
        prep.run_persona(p, tmp_path)
        args = coach[0]
        assert args[args.index("-p") + 1] == prep.COMMAND == "/mcp__sluice__interview_prep"
        assert args[args.index("--tools") + 1] == ""   # no built-in tools at all
        assert set(args[args.index("--allowedTools") + 1].split(",")) == prep.expected_tools(
            p.write)
        assert set(args[args.index("--disallowedTools") + 1].split(",")) == {
            f"mcp__sluice__{n}" for n in prep.disallowed(p.write)}
        assert "--resume" not in args


def test_a_read_only_persona_starts_the_server_read_only(tmp_path):
    from scripts.coach_eval import prep

    for p in _prep_personas():
        args = prep.server_args(p, tmp_path)
        assert args == ["--sandbox", str(tmp_path)] + ([] if p.write else ["--read-only"])
    # scope: both levels are among the personas
    assert {p.write for p in _prep_personas()} == {True, False}


# ── rosters, at both levels ──

@pytest.mark.parametrize("write", [False, True])
def test_the_prep_rosters_place_every_server_tool_at_each_level(write):
    from scripts.coach_eval import prep

    roster = _server_tool_names(write=write)
    assert roster  # the sweep enumerated something
    kept, gone = set(prep.kept(write)), set(prep.disallowed(write))
    assert kept | gone == roster and not kept & gone
    assert prep.expected_tools(write) == {f"mcp__sluice__{n}" for n in prep.kept(write)}
    assert ("propose_evidence" in kept) is write


def test_the_two_runners_persona_ids_are_disjoint():
    # They share the `<id>.*` output names in one --out directory.
    setup = {p.id for p in personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")}
    prep_ids = {p.id for p in _prep_personas()}
    assert setup and prep_ids and not setup & prep_ids


# ── no built-ins ──

@pytest.mark.parametrize("write", [False, True])
def test_the_prep_init_check_fails_on_websearch_or_any_disallowed_tool(write):
    from scripts.coach_eval import prep

    want = prep.expected_tools(write)
    ev = _prep_init(write)
    assert isolation.check_init_event(ev, tools=want, servers={"sluice"}) == []
    extras = ["WebSearch", *(f"mcp__sluice__{n}" for n in prep.disallowed(write))]
    assert len(extras) > 1   # scope: the disallowed tools are among them
    for extra in extras:
        ev["tools"] = sorted(want | {extra})
        problems = isolation.check_init_event(ev, tools=want, servers={"sluice"})
        assert any(extra in p for p in problems), extra


@pytest.mark.parametrize("extra", ["WebSearch", "a disallowed sluice tool"])
def test_the_prep_run_stops_and_removes_its_sandbox_when_the_isolation_check_fails(
        monkeypatch, tmp_path, extra):
    """The prep LOOP consults the init check and stops on it: the row above tests the check
    itself, this one that the runner acts on it, before any user or grader call."""
    from scripts.coach_eval import prep

    p = _prep_where(lambda x: x.write)
    tool = extra if extra == "WebSearch" else f"mcp__sluice__{prep.disallowed(p.write)[0]}"
    init = {**_prep_init(p.write), "tools": sorted(prep.expected_tools(p.write) | {tool})}
    seen = _sandbox_probe(monkeypatch, init)
    with pytest.raises(SystemExit, match="isolation check failed"):
        prep.run_persona(p, tmp_path)
    assert len(seen) == 1                    # the coach's first turn, and nothing after it
    assert not seen[0].exists()              # the sandbox is removed on the SystemExit
    assert not list(tmp_path.glob("*.scorecard.json"))


# ── personas ──

def test_load_personas_never_returns_a_prep_persona_and_the_prep_loader_returns_every_file():
    setup = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")
    ps = _prep_personas()   # its own scope assertion: every file, at least three
    assert setup and not any(isinstance(p, personas.PrepPersona) for p in setup)
    assert all(isinstance(p, personas.PrepPersona) for p in ps)
    assert {f.stem for f in _PREP_DIR.glob("*.json")} == {p.id for p in ps}


def _story(**over):
    return {"title": "t", "company": "c", "best_for": "b", "body": "x", "verified": True, **over}


def _prep_persona_with(**over):
    return personas.PrepPersona(**{
        "id": "x", "name_seed": 1, "location": LOCATIONS[0], "company": "c", "role_seed": 1,
        "status": "interview", "write": True, "jd_template": "{company} {role} {location}",
        "stories": [_story()], **over})


def test_a_well_formed_prep_persona_loads():
    assert _prep_persona_with().status == "interview"


def test_a_prep_persona_with_an_unknown_status_is_refused():
    with pytest.raises(ValueError, match="unknown status"):
        _prep_persona_with(status="intervew")


def test_the_required_story_fields_include_the_three_the_seeder_needs():
    # Scope for the row below, which iterates the constant: a roster that lost a key would
    # leave that row green with one row fewer.
    assert set(personas.STORY_REQUIRED) == {"title", "company", "body"}


@pytest.mark.parametrize("field", personas.STORY_REQUIRED)
def test_a_story_without_a_required_field_is_refused(field):
    for missing in (_story(**{field: ""}), {k: v for k, v in _story().items() if k != field}):
        with pytest.raises(ValueError, match=f"has no {field}"):
            _prep_persona_with(stories=[_story(title="other"), missing])


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_a_prep_persona_whose_write_is_not_a_boolean_is_refused(value):
    # "false" is truthy: it would run the read-only persona at the write level.
    with pytest.raises(ValueError, match="write must be true or false"):
        _prep_persona_with(write=value)


@pytest.mark.parametrize("value", ["false", "yes", 0, 1, None, "<missing>"])
def test_a_story_whose_verified_is_not_a_boolean_is_refused(value):
    # "false" is truthy: the seeder would mark the story verified against the persona's word.
    story = _story(title="other")
    if value == "<missing>":
        del story["verified"]
    else:
        story["verified"] = value
    with pytest.raises(ValueError, match="verified must be true or false"):
        _prep_persona_with(stories=[story])


@pytest.mark.parametrize("title", ["../../../escape", "a/b", "a\\b", "a\0b", ".", "..",
                                   ".hidden"])
def test_a_story_title_that_is_not_a_plain_file_name_is_refused(title):
    # The title names the story's note, written by prep.py::seed_vault under the stories folder.
    with pytest.raises(ValueError, match="is not a plain file name"):
        _prep_persona_with(stories=[_story(title=title)])


@pytest.mark.parametrize("second", ["Same", "SAME"])
def test_two_stories_sharing_a_title_are_refused(second):
    # One note would be seeded for two stories, while the simulated user is told both.
    with pytest.raises(ValueError, match="share the title"):
        _prep_persona_with(stories=[_story(title="Same"), _story(title=second, body="y")])


def test_titles_that_differ_beyond_case_are_two_stories():
    # The control for the two rows above: distinct names, inner dots and spaces all load.
    p = _prep_persona_with(stories=[_story(title="Same"), _story(title="Same v2.0 draft")])
    assert len(p.stories) == 2


@pytest.mark.parametrize("field", ["title", "company", "best_for", "body"])
def test_a_placeholder_in_any_story_field_is_refused(field):
    with pytest.raises(ValueError, match=f"{field} carries a placeholder"):
        _prep_persona_with(stories=[_story(**{field: "at {company}"})])


def test_a_jd_template_placeholder_outside_the_three_is_refused():
    with pytest.raises(ValueError, match="placeholder"):
        _prep_persona_with(jd_template="{company} {role} {location} {salary}")


# ── the seeder ──

def _tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))


def test_the_seeder_writes_only_into_the_sandbox_whatever_the_environment_says(
        monkeypatch, tmp_path):
    """A developer's exported VAULT_DIR and SLUICE_CONFIG (here decoys, the config naming a
    vault of its own) must not redirect the seeder: it names its Vault explicitly."""
    from urllib.parse import urlparse

    from scripts.coach_eval import prep, run
    from sluice import mcpserver
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.core.vault import Vault

    decoy_vault, decoy_cfg = tmp_path / "decoy-vault", tmp_path / "decoy-config"
    decoy_vault.mkdir()
    decoy_cfg.mkdir()
    (decoy_cfg / "config.yaml").write_text(f"vault_dir: {decoy_cfg / 'vault'}\n")
    monkeypatch.setenv("VAULT_DIR", str(decoy_vault))
    monkeypatch.setenv("SLUICE_CONFIG", str(decoy_cfg / "config.yaml"))
    before = (_tree(decoy_vault), _tree(decoy_cfg))
    p = _with_unverified()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    slug = prep.seed_vault(p, sandbox)
    assert (_tree(decoy_vault), _tree(decoy_cfg)) == before
    # The read-back with VAULT_DIR gone, through a store on the sandbox vault alone.
    monkeypatch.delenv("VAULT_DIR")
    vault = run.sandbox_vault(sandbox, False)
    assert vault.is_relative_to(sandbox)
    reply = mcpserver.prep_brief(Sluice(Config(), store=Vault(str(vault))), slug)
    assert reply["outcome"] == "ok" and reply["slug"] == slug
    entries = {e["title"]: e["verified"] for b in reply["brief"]["kinds"].values()
               for e in b["entries"]}
    assert entries == {s["title"]: bool(s["verified"]) for s in p.stories}
    assert reply["status"] == p.status
    note = next(n for n in Vault(str(vault)).read_leads() if n.slug == slug)
    assert urlparse(note.fm["url"].strip('"')).hostname == "jobs.example.com"


class _Spy:
    """A store that is a real Vault on the sandbox except for the methods given."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._real, name)


def _spy_rows():
    import dataclasses

    return {
        "upsert not created": (lambda v: {"upsert": lambda lead: dataclasses.replace(
            v.upsert(lead), outcome="updated")}, "upsert"),
        "JD append returned False": (lambda v: {
            "append_body_section": lambda *a, **k: False}, "JD append"),
        "read-back status differs": (lambda v: {
            "update_fields": lambda *a, **k: False}, "status"),
        "read-back has no JD section": (lambda v: {
            "append_body_section": lambda *a, **k: True}, "job description section"),
        "read-back misses a story": (lambda v: {
            "read_evidence": lambda kind, verified_only=True: v.read_evidence(
                kind, verified_only)[1:]}, "not listed"),
        "read-back has no lead": (lambda v: {"read_leads": lambda: []},
                                  "is not there on a read-back"),
    }


@pytest.mark.parametrize("row", sorted(_spy_rows()))
def test_each_seeder_check_raises(tmp_path, row):
    """A seed that silently did nothing, or did something else, must never become an eval of
    an empty or wrong vault: each check raises with its own message (asserted, since every
    one raises the same type)."""
    from scripts.coach_eval import prep, run
    from sluice.core.vault import Vault

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    real = Vault(str(run.sandbox_vault(sandbox, False)))
    over, message = _spy_rows()[row]
    with pytest.raises(SystemExit, match=message):
        prep.seed_vault(_all_verified(), sandbox, store=_Spy(real, **over(real)))


def test_the_seeder_writes_no_story_outside_the_vault_whatever_its_title(tmp_path):
    """The seeder's own containment check, reached past PrepPersona's title refusal: the
    persona's story list is mutated after it loaded, as nothing in a persona file can do."""
    import copy

    from scripts.coach_eval import prep

    p = copy.deepcopy(_all_verified())
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    p.stories[0]["title"] = "../../../../escape"
    with pytest.raises(SystemExit, match="would be written outside the vault"):
        prep.seed_vault(p, sandbox)
    assert not list(tmp_path.rglob("escape.md"))


def test_the_seeder_without_a_spy_seeds_and_returns_the_lead_slug(tmp_path):
    from sluice.core.status import CANONICAL

    for p in _prep_personas():
        sluice, slug = _seeded(tmp_path, p)
        assert p.status in CANONICAL
        assert [n.slug for n in sluice.store().read_leads()] == [slug]


# ── the simulated user ──

def test_the_prep_users_prompt_carries_every_story_body_verbatim():
    from scripts.coach_eval import prep

    for p in _prep_personas():
        text = prep.prep_user_prompt(p, ["COACH: hi"])
        assert p.stories and all(s["body"] in text for s in p.stories), p.id
        assert p.company in text and personas.role_name(p) in text
        assert text.endswith("COACH: hi")


def test_the_prep_users_system_prompt_replaces_the_agent_prompt(monkeypatch, tmp_path):
    from scripts.coach_eval import prep, run

    calls = []

    def fake(args, cwd, prompt=None):
        calls.append(args)
        return [_prep_init(True)] if "--mcp-config" in args else []

    monkeypatch.setattr(run, "_claude", fake)
    card = prep.run_persona(_prep_where(lambda x: x.write), tmp_path)
    assert card["failure"] == "the simulated user returned an empty reply"
    # The empty reply STOPS the loop: one coach turn, one user turn, then the grader, never a
    # further coach turn sent an empty message.
    assert len(calls) == 3 and sum("--mcp-config" in a for a in calls) == 1
    user, grader = calls[-2:]
    assert user[user.index("--system-prompt") + 1] == prep.PREP_USER_SYSTEM
    assert grader[grader.index("--system-prompt") + 1] == run.GRADER_SYSTEM
    assert "--system-prompt" not in calls[0]


# ── the rubric ──

def _det(events, slug, write, max_turns=12):
    from scripts.coach_eval import prep
    out = rubric.prep_deterministic(events, slug=slug, write=write,
                                    kept=prep.expected_tools(write), max_turns=max_turns)
    return {k: v[0] for k, v in out.items()}


def _propose(denied=False):
    return _tool(rubric.PROPOSE, {"kind": "stories"}, {"outcome": "proposed"}, denied=denied)


def test_prep_rubric_deterministic_checks(tmp_path):
    sluice, slug = _seeded(tmp_path, _all_verified())
    events = _flat(_start(), _tool("mcp__sluice__list_leads", {}, {"leads": []}),
                   _brief_call(sluice, slug), _start(), _propose())
    assert all(_det(events, slug, write=True).values())
    # read-only, no proposal: everything passes and the proposal check has no subject
    out = _det(_flat(_start(), _brief_call(sluice, slug)), slug, write=False)
    assert out.pop("propose_only_when_writing") is rubric.NOT_EXERCISED
    assert all(out.values()), out


def test_a_fragment_then_the_real_slug_passes_the_slug_check(tmp_path):
    """Recovering from a guessed fragment through the bounded suggestions is what they are for."""
    sluice, slug = _seeded(tmp_path, _all_verified())
    miss = _brief_call(sluice, _fragment(slug))
    assert rubric._payload(miss[1]["message"]["content"][0]["content"])["suggestions"] == [slug]
    out = _det(_flat(_start(), miss, _brief_call(sluice, slug)), slug, write=True)
    assert out["seeded_slug"] is True and out["prep_brief_ok"] is True


@pytest.mark.parametrize("shape", ["not_found only", "denied only"])
def test_the_slug_check_is_not_exercised_without_a_successful_call(tmp_path, shape):
    # all([]) is True: without `_when`, a run that never read the brief would pass it.
    sluice, slug = _seeded(tmp_path, _all_verified())
    call = (_brief_call(sluice, _fragment(slug)) if shape == "not_found only"
            else _brief_call(sluice, slug, denied=True))
    out = _det(_flat(_start(), call), slug, write=True)
    assert out["seeded_slug"] is rubric.NOT_EXERCISED
    assert out["prep_brief_ok"] is False


def _failing_rows():
    """(build, write, check): `build(s, slug, other)` makes the events from the seeded facade,
    its slug, and `other()`, a real ok call on a SECOND seeded lead."""
    return {
        "no prep_brief": (lambda s, slug, other: _flat(_start()), True, "prep_brief_ok"),
        "a denied prep_brief": (lambda s, slug, other: _brief_call(s, slug, denied=True),
                                True, "prep_brief_ok"),
        "only a not_found prep_brief": (
            lambda s, slug, other: _brief_call(s, _fragment(slug)), True, "prep_brief_ok"),
        "an ok call on another slug": (
            lambda s, slug, other: _flat(_brief_call(s, slug), other()), True, "seeded_slug"),
        "a disallowed sluice tool": (lambda s, slug, other: _flat(_brief_call(s, slug), _tool(
            "mcp__sluice__get_lead")), True, "no_disallowed_tool"),
        "a built-in": (lambda s, slug, other: _flat(_brief_call(s, slug), _tool("WebSearch")),
                       True, "no_disallowed_tool"),
        "propose_evidence at read-only": (
            lambda s, slug, other: _flat(_brief_call(s, slug), _propose()), False,
            "propose_only_when_writing"),
        "a denied propose_evidence when writing": (lambda s, slug, other: _flat(
            _brief_call(s, slug), _propose(denied=True)), True, "no_tool_denied"),
        "too many turns": (lambda s, slug, other: _flat(
            _brief_call(s, slug), *[_start() for _ in range(13)]), True, "turns"),
    }


@pytest.mark.parametrize("row", sorted(_failing_rows()))
def test_each_prep_rubric_check_fails_when_its_rule_is_broken(tmp_path, row):
    sluice, slug = _seeded(tmp_path, _all_verified())
    build, write, check = _failing_rows()[row]

    def other():
        # A second persona's lead in its own sandbox, answered by the real tool: an ok reply
        # on a slug that is not the seeded one.
        s2, slug2 = _seeded(tmp_path, _with_unverified())
        assert slug2 != slug
        return _brief_call(s2, slug2)

    assert _det(build(sluice, slug, other), slug, write=write)[check] is False


# ── the grader ──

def _grade(monkeypatch, events, grade=None):
    from scripts.coach_eval import prep, run

    prompts = []

    def fake(args, cwd, prompt=None):
        prompts.append(prompt)
        return [{"type": "result", "result": json.dumps(grade or _PREP_GRADE)}]

    monkeypatch.setattr(run, "_claude", fake)
    llm, failure = prep.grade_prep(events, ["COACH: hi", "USER: DONE"], "sonnet", "/")
    assert failure is None and len(prompts) == 1
    return llm, prompts[0]


def _titles(brief):
    return [e["title"] for b in brief["kinds"].values() for e in b["entries"]]


def test_the_grader_is_told_the_first_ok_calls_facts(monkeypatch, tmp_path):
    from sluice import mcpserver

    p = _with_unverified()
    sluice, slug = _seeded(tmp_path, p)
    ok = mcpserver.prep_brief(sluice, slug)
    events = _flat(_start(), _brief_call(sluice, _fragment(slug)), _brief_call(sluice, slug))
    llm, prompt = _grade(monkeypatch, events)
    titles = _titles(ok["brief"])
    assert titles and all(t in prompt for t in titles)
    assert all(e["body"] in prompt for b in ok["brief"]["kinds"].values() for e in b["entries"])
    assert ok["jd"]["text"] and ok["jd"]["text"] in prompt
    # the brief holds an unverified entry, so that score stands
    assert llm["unverified_handled"] == 4 and llm["prep_brief_reached"] is True


def test_the_grader_is_told_the_FIRST_ok_call_when_two_succeed(monkeypatch, tmp_path):
    """Two successful calls on two different leads: the facts, and the unverified overwrite,
    follow the first. The first lead holds no unverified entry and the second does, so a
    grader told the second's facts would also keep its `unverified_handled` score."""
    from scripts.coach_eval import prep

    first, second = _all_verified(), _with_unverified()
    a, b = ({s["title"] for s in p.stories} for p in (first, second))
    assert a and b and not a & b           # scope: the two briefs are told apart by title
    sa, slug_a = _seeded(tmp_path, first)
    sb, slug_b = _seeded(tmp_path, second)
    events = _flat(_start(), _brief_call(sa, slug_a), _brief_call(sb, slug_b))
    assert len(rubric.prep_brief_ok(events)) == 2
    fact = prep.brief_fact(events)
    assert all(t in fact for t in a) and not any(t in fact for t in b)
    llm, prompt = _grade(monkeypatch, events)
    assert fact in prompt and llm["unverified_handled"] == "not exercised"


def test_with_no_successful_call_the_grader_is_told_so_and_sees_no_entry(monkeypatch, tmp_path):
    from scripts.coach_eval import prep

    sluice, slug = _seeded(tmp_path, _all_verified())
    _, prompt = _grade(monkeypatch, _flat(_start(), _brief_call(sluice, _fragment(slug))))
    assert prep.NO_BRIEF_FACT in prompt
    every_title = [s["title"] for p in _prep_personas() for s in p.stories]
    assert every_title and not any(t in prompt for t in every_title)


@pytest.mark.parametrize("shape", ["not_found only", "denied only"])
def test_with_no_successful_call_only_the_chat_scores_survive(monkeypatch, tmp_path, shape):
    sluice, slug = _seeded(tmp_path, _with_unverified())
    call = (_brief_call(sluice, _fragment(slug)) if shape == "not_found only"
            else _brief_call(sluice, slug, denied=True))
    llm, _ = _grade(monkeypatch, _flat(_start(), call),
                    grade={**_PREP_GRADE, "no_supplied_example": "no: quoted"})
    assert {k: llm[k] for k in _FACT_SCORED} == dict.fromkeys(_FACT_SCORED, "not exercised")
    assert llm["coaching_quality"] == 3 and llm["no_supplied_example"] == "no: quoted"
    assert llm["notes"] == "n" and llm["prep_brief_reached"] is False


def test_with_no_unverified_entry_unverified_handled_is_not_exercised(monkeypatch, tmp_path):
    sluice, slug = _seeded(tmp_path, _all_verified())
    llm, _ = _grade(monkeypatch, _flat(_start(), _brief_call(sluice, slug)))
    assert llm["unverified_handled"] == "not exercised"
    assert llm["gaps_probed"] == 4 and llm["fidelity_feedback"] == 4


# ── regrade, print, dispatch ──

@pytest.mark.parametrize("persona,shape", [("all_verified", "ok"), ("with_unverified",
                                                                     "not_found")])
def test_both_prep_overwrites_hold_in_a_regrade(monkeypatch, tmp_path, persona, shape):
    from scripts.coach_eval import run

    p = _all_verified() if persona == "all_verified" else _with_unverified()
    sluice, slug = _seeded(tmp_path, p)
    runs = tmp_path / "runs"
    runs.mkdir()
    lead = slug if shape == "ok" else _fragment(slug)
    pid, _, _ = _saved_prep_run(runs, _flat(_start(), _brief_call(sluice, lead)))
    seen = _stub_claude(monkeypatch, lambda c, k: [
        {"type": "result", "result": json.dumps(_PREP_GRADE)}])
    run.regrade(runs, "haiku")
    assert len(seen) == 1
    card = json.loads((runs / f"{pid}.regrade-haiku.json").read_text())
    llm = card["llm_graded"]
    assert card["runner"] == "prep" and llm["unverified_handled"] == "not exercised"
    if shape == "ok":
        assert llm["prep_brief_reached"] is True and llm["gaps_probed"] == 4
    else:
        assert llm["prep_brief_reached"] is False and llm["gaps_probed"] == "not exercised"


def test_a_saved_prep_run_regrades_through_the_prep_grader(monkeypatch, tmp_path):
    from scripts.coach_eval import prep, run

    sluice, slug = _seeded(tmp_path, _with_unverified())
    runs = tmp_path / "runs"
    runs.mkdir()
    _, events, transcript = _saved_prep_run(runs, _flat(_start(), _brief_call(sluice, slug)))
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(runs)])
    assert seen[0][1]["input"] == prep.prep_grader_prompt(events, transcript)
    assert seen[0][1]["input"] != run.grader_prompt(events, transcript)


def test_a_saved_run_naming_an_unknown_runner_is_refused(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    _saved_prep_run(tmp_path, [], runner="prepp")
    seen = _grader_stub(monkeypatch)
    for mode in ("--regrade", "--print-grader-prompt"):
        with pytest.raises(SystemExit, match="unknown runner 'prepp'"):
            run.main([mode, str(tmp_path)])
    assert seen == []   # refused, never graded as setup
    assert not list(tmp_path.glob("*.regrade-*")) and not list(tmp_path.glob("*.grade-me.txt"))


def test_an_unknown_runner_beside_a_valid_run_is_refused_before_any_run_is_graded(
        monkeypatch, tmp_path):
    """Every saved run is checked before the first is graded or printed: the valid run sorts
    FIRST here, so a lazy check would grade it, and write its card, before refusing."""
    from scripts.coach_eval import run

    _saved_run(tmp_path, (rubric.STATUS,), pid="a-valid-setup-run")
    _saved_prep_run(tmp_path, [], pid="z-unknown-runner", runner="prepp")
    seen = _grader_stub(monkeypatch)
    for mode in ("--regrade", "--print-grader-prompt"):
        with pytest.raises(SystemExit, match="unknown runner 'prepp'"):
            run.main([mode, str(tmp_path)])
    assert seen == []
    assert not list(tmp_path.glob("*.regrade-*")) and not list(tmp_path.glob("*.grade-me.txt"))


def test_a_scorecard_without_a_runner_key_is_a_setup_run(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, events, transcript = _saved_run(tmp_path, (rubric.STATUS,))
    (tmp_path / f"{pid}.scorecard.json").write_text(json.dumps({"persona": pid}))
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(tmp_path)])
    assert seen[0][1]["input"] == run.grader_prompt(events, transcript)
    card = json.loads((tmp_path / f"{pid}.regrade-sonnet.json").read_text())
    assert "runner" not in card and card["llm_graded"]["setup_save_reached"] is False


def test_a_saved_prep_runs_printed_prompt_is_the_prep_graders_and_calls_nothing(
        monkeypatch, tmp_path):
    from scripts.coach_eval import prep, run

    sluice, slug = _seeded(tmp_path, _with_unverified())
    runs = tmp_path / "runs"
    runs.mkdir()
    pid, events, transcript = _saved_prep_run(runs, _flat(_start(), _brief_call(sluice, slug)))
    seen = _grader_stub(monkeypatch)
    run.main(["--print-grader-prompt", str(runs)])
    assert seen == []
    assert (runs / f"{pid}.grade-me.txt").read_text() == prep.prep_grader_prompt(
        events, transcript)


def test_an_unknown_prep_persona_is_refused(tmp_path):
    from scripts.coach_eval import run

    with pytest.raises(SystemExit, match="no prep persona"):
        run.main(["--prep-persona", "nobody", "--out", str(tmp_path / "out")])


def test_the_prep_run_copies_the_lead_note_the_stories_and_the_inbox(tmp_path):
    from scripts.coach_eval import prep, run
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import INBOX_SUBDIR

    p = _all_verified()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    slug = prep.seed_vault(p, sandbox)
    vault = run.sandbox_vault(sandbox, False)
    inbox = vault / EVIDENCE_KINDS["stories"].relpath / "_inbox"
    inbox.mkdir()
    (inbox / "Proposed.md").write_text("new")
    # A proposal filed under ANOTHER kind's inbox is copied too; that kind's own entries,
    # outside its inbox, are not the run's to report.
    other = next(k for k in EVIDENCE_KINDS if k != "stories")
    misfiled = vault / EVIDENCE_KINDS[other].relpath / INBOX_SUBDIR
    misfiled.mkdir(parents=True)
    (misfiled / "Misfiled.md").write_text("misfiled")
    (misfiled.parent / "Verified.md").write_text("not the run's")
    (sandbox / "client-cwd").mkdir()
    (sandbox / "client-cwd" / "stray.md").write_text("not the run's")
    copied = prep.copy_prep_files(sandbox, tmp_path / "out")
    rel = lambda path: str(path.relative_to(sandbox))  # noqa: E731
    stories = vault / EVIDENCE_KINDS["stories"].relpath
    assert {rel(inbox / "Proposed.md"), *(rel(stories / f"{s['title']}.md") for s in p.stories)
            } <= set(copied)
    assert any(c.endswith(f"{slug}.md") for c in copied)
    assert not any(c.startswith("client-cwd") for c in copied)
    assert (tmp_path / "out" / rel(inbox / "Proposed.md")).read_text() == "new"
    assert rel(misfiled / "Misfiled.md") in copied
    assert (tmp_path / "out" / rel(misfiled / "Misfiled.md")).read_text() == "misfiled"
    assert rel(misfiled.parent / "Verified.md") not in copied


# ── the read-only server ──

def test_serve_read_only_runs_the_server_without_write(tmp_path, monkeypatch):
    from scripts.coach_eval import serve

    seen = {}

    def fake(exe, argv, env):
        seen.update(exe=exe, argv=argv, env=env, cwd=Path.cwd())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("os.execve", fake)
    serve.main(["--sandbox", str(tmp_path), "--read-only"])
    assert seen["argv"][1:] == ["mcp", "serve"]
    assert seen["cwd"] == (tmp_path / "server-cwd").resolve()
    assert isolation.isolation_problems(seen["env"], tmp_path, ROOT) == []


# ── the persona guard (spec section 10, "Persona content") ──

def test_every_prep_persona_company_and_location_is_on_its_roster():
    from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES

    ps = _prep_personas()
    story_companies = [s["company"] for p in ps for s in p.stories]
    assert story_companies   # scope: the story level was checked, not only the persona's
    for name in [p.company for p in ps] + story_companies:
        assert name in _REVIEWED_FIXTURE_IDENTITIES, name
    assert all(p.location in LOCATIONS for p in ps)


def test_only_the_jd_template_carries_placeholders_and_only_the_three():
    import string

    ps = _prep_personas()
    for p in ps:
        named = {f for _, f, _, _ in string.Formatter().parse(p.jd_template) if f is not None}
        assert named == {"company", "location", "role"}, p.id
        for s in p.stories:
            assert not any(c in str(s[k]) for k in ("title", "company", "best_for", "body")
                           for c in "{}"), (p.id, s["title"])


def test_no_prep_persona_text_expresses_a_preference():
    from sluice.onboard.questions import expresses_a_preference

    texts = [(p.id, "jd_template", p.jd_template) for p in _prep_personas()] + [
        (p.id, f"{s['title']}.{k}", s[k]) for p in _prep_personas() for s in p.stories
        for k in ("title", "best_for", "body")]
    assert len(texts) > len(_prep_personas())   # scope: the story fields were read
    for pid, where, text in texts:
        assert expresses_a_preference(text) == [], (pid, where)


# A reviewed digest per prep persona over its canonical JSON (sorted keys, so formatting and
# key order do not count, but every value does). Free text such as a story body can carry a
# real place or employer no roster can enumerate, so a change here must be READ before it is
# re-digested: the digest only forces that review.
_PREP_PERSONA_DIGESTS = {
    "prep-gaps.json": "45206bef4f7ef22e12914b1d3af2d87f6861fe2e78f5f9c89076522e77f3be27",
    "prep-strong.json": "7b08c5e7a4d8ecbcd7dd98af13810c03ec830392372b29d87c5b54dfbd309e7b",
    "prep-unverified.json": "00c20bcdad0f0fb934cb40ce8b1b8e16ae7c72e548ec4cb5d2c481de38da7afc",
}


def _canonical_digest(path):
    import hashlib
    data = json.loads(path.read_text(encoding="utf-8"))
    canon = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def test_each_prep_persona_matches_its_reviewed_digest():
    found = {f.name: _canonical_digest(f) for f in sorted(_PREP_DIR.glob("*.json"))}
    assert found and len(found) == len(_prep_personas())   # scope
    assert found == _PREP_PERSONA_DIGESTS, (
        "a prep persona changed, was added or was removed. READ the diff for any real "
        "employer, place, person or board name first; only then paste the new digest: "
        f"{found}")
