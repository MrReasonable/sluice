import json
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


def test_rubric_deterministic_checks():
    events = _flat(_start(), _use(rubric.STATUS), _use(rubric.REVIEW, [
        {"kind": "brief", "target": "Sources consulted", "value": "example.invalid"}]))
    out = rubric.deterministic(events, max_turns=30)
    assert all(v[0] for v in out.values()), out


@pytest.mark.parametrize("events,check", [
    (_flat(_use(rubric.REVIEW, [])), "status_before_review"),
    # a status call that was DENIED does not count as the status having been read
    (_flat(_use(rubric.STATUS, denied=True), _use(rubric.REVIEW, [])), "status_before_review"),
    (_flat(_use(rubric.STATUS), _use(rubric.REVIEW, [{"kind": "brief"}])), "schema_valid"),
    (_flat(_use(rubric.STATUS), _use(rubric.REVIEW, [{"kind": "brief", "target": "Pay structure",
                                                      "value": "x"}])), "brief_cites_sources"),
    (_flat(_use(rubric.STATUS), _use(rubric.REVIEW, [{"kind": "config", "target": "verified",
                                                      "value": "x"}])), "no_verified"),
    (_flat(*[_start() for _ in range(31)]), "turns"),
    (_flat(_use(rubric.STATUS, denied=True)), "no_tool_denied"),
    (_flat(_use(rubric.STATUS, answered=False)), "no_tool_denied"),
])
def test_each_rubric_check_fails_when_its_rule_is_broken(events, check):
    assert rubric.deterministic(events, max_turns=30)[check][0] is False


def test_denied_calls_are_ignored_by_the_checks_that_read_inputs():
    bad = [{"kind": "brief"}]  # would fail schema_valid, and target `verified` below
    events = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, bad, denied=True),
                   _use(rubric.REVIEW, [{"kind": "config", "target": "verified"}], denied=True))
    out = rubric.deterministic(events, max_turns=30)
    assert out["schema_valid"][0] and out["no_verified"][0]
    assert out["no_tool_denied"][0] is False


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
        seen.update(exe=exe, argv=argv, cwd=Path.cwd())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("os.execve", fake)
    serve.main(["--sandbox", str(tmp_path)])
    assert seen["argv"][1:] == ["mcp", "serve", "--write"]
    assert seen["cwd"] == (tmp_path / "server-cwd").resolve()


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
