import json
import re
from pathlib import Path

import pytest

from scripts.coach_eval import isolation, personas, rubric
from tests.conftest import LOCATIONS, PATH_ENV_VARS

ROOT = Path(__file__).resolve().parent.parent


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


def _server_tool_names():
    import asyncio

    from sluice.core.config import Config
    from sluice.mcpserver import build_server

    async def _run():
        from mcp import Client
        async with Client(build_server(Config(), write=True), raise_exceptions=True) as c:
            return {t.name for t in (await c.list_tools()).tools}

    return asyncio.run(_run())


def test_every_server_tool_is_either_kept_or_disallowed_for_the_coach():
    from scripts.coach_eval import run

    roster = _server_tool_names()
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
    assert len(calls) == 3  # coach, empty user turn, grader: no second coach call on `-p ""`


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

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
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


def test_grader_model_reaches_the_argv_and_defaults_to_sonnet(monkeypatch, tmp_path):
    # Owner's decision (2026-10-07), after regrading runs 2-5 with both: the default grader is
    # Sonnet. The explicit flag is a DIFFERENT model, so the default is what the second call shows.
    from scripts.coach_eval import run

    _saved_run(tmp_path, (rubric.STATUS,))
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


def test_a_live_runs_grader_model_flag_reaches_the_grader_argv(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init]
        if (kw.get("input") or "").startswith("Grade"):
            return [{"type": "result", "result": json.dumps(_GRADE)}]
        return [{"type": "result", "result": "DONE"}]

    seen = _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    run.main(["--persona", p.id, "--grader-model", "opus", "--out", str(tmp_path / "out")])
    graders = [cmd for cmd, kw in seen if (kw.get("input") or "").startswith("Grade")]
    assert len(graders) == 1 and graders[0][graders[0].index("--model") + 1] == "opus"


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
    from sluice.ingest.sources import all_sources
    ids = {s.id.lower() for s in all_sources()}
    assert ids
    for f in (ROOT / "scripts" / "coach_eval" / "personas").glob("*.json"):
        words = set(re.findall(r"[a-z_]+", f.read_text(encoding="utf-8").lower()))
        assert not ids & words, f.name


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
    coach, user, grader = calls

    def system(args):
        return args[args.index("--system-prompt") + 1] if "--system-prompt" in args else None

    assert system(coach) is None
    assert system(user) == run.USER_SYSTEM and system(grader) == run.GRADER_SYSTEM
