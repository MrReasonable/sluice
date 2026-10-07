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


_SOURCED = {"kind": "brief", "target": "Sources consulted", "value": "example.invalid"}
_UNSOURCED = {"kind": "brief", "target": "Pay structure", "value": "x"}


def test_denied_calls_are_ignored_by_the_checks_that_read_inputs():
    bad = [{"kind": "brief"}]  # would fail schema_valid, and target `verified` below
    events = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, bad, denied=True),
                   _use(rubric.REVIEW, [{"kind": "config", "target": "verified"}], denied=True),
                   _use(rubric.REVIEW, [_SOURCED]))
    out = rubric.deterministic(events, max_turns=30)
    assert out["schema_valid"][0] is True and out["no_verified"][0] is True
    assert out["no_tool_denied"][0] is False


def test_a_denied_calls_sources_do_not_count_for_brief_cites_sources():
    # The denied review's sourced brief would satisfy the check if denied calls counted; the
    # successful one proposes a brief WITHOUT sources, so the check must fail.
    events = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_SOURCED], denied=True),
                   _use(rubric.REVIEW, [_UNSOURCED]))
    assert rubric.deterministic(events, max_turns=30)["brief_cites_sources"][0] is False


_REVIEW_CHECKS = ("status_before_review", "schema_valid", "brief_cites_sources", "no_verified")


@pytest.mark.parametrize("events", [
    [],
    _flat(_start(), _use(rubric.STATUS)),
    # run 2's shape: research done, the session closed, setup_review never called
    _flat(_start(), _use(rubric.STATUS), _use("WebSearch")),
    _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_SOURCED], denied=True)),
    _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_SOURCED], answered=False)),
    # a denied review with an UNSOURCED brief: not a failure either, since it never happened
    _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_UNSOURCED], denied=True)),
])
def test_review_checks_are_not_exercised_without_a_successful_review(events):
    out = rubric.deterministic(events, max_turns=30)
    assert {k: out[k][0] for k in _REVIEW_CHECKS} == dict.fromkeys(
        _REVIEW_CHECKS, rubric.NOT_EXERCISED)
    assert [rubric.verdict(out[k][0]) for k in _REVIEW_CHECKS] == ["not exercised"] * 4
    assert rubric.review_reached(events) is False


def test_brief_cites_sources_is_not_exercised_when_no_brief_was_proposed():
    events = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [
        {"kind": "config", "target": "accept_titles", "value": "x"}]))
    out = rubric.deterministic(events, max_turns=30)
    assert out["brief_cites_sources"][0] is rubric.NOT_EXERCISED
    assert all(out[k][0] is True for k in ("status_before_review", "schema_valid", "no_verified"))
    assert rubric.review_reached(events) is True


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


@pytest.mark.parametrize("coach_calls,reached,review_result", [
    ((rubric.STATUS,), False, "not exercised"),
    ((rubric.STATUS, rubric.REVIEW), True, "pass"),
])
def test_the_scorecard_says_whether_setup_review_was_reached(
        monkeypatch, tmp_path, coach_calls, reached, review_result):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init, *_flat(*[_use(n, [_SOURCED] if n == rubric.REVIEW else None)
                                   for n in coach_calls])]
        if kw["input"].startswith("Grade"):
            return [{"type": "result", "result": "{}"}]
        return [{"type": "result", "result": "DONE"}]

    seen = _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    card = run.run_persona(p, tmp_path)
    assert card["setup_review_reached"] is reached
    # the same fact beside the grade, and in the prompt the grader actually received
    assert card["llm_graded"]["setup_review_reached"] is reached
    grader_input = next(kw["input"] for _, kw in seen
                        if (kw.get("input") or "").startswith("Grade"))
    if reached:
        assert run.NO_REVIEW_FACT not in grader_input
        assert "successfully 1 time(s)" in grader_input and "example.invalid" in grader_input
    else:
        assert run.NO_REVIEW_FACT in grader_input
    results = {k: v["result"] for k, v in card["deterministic"].items()}
    assert {k: results[k] for k in _REVIEW_CHECKS} == dict.fromkeys(_REVIEW_CHECKS, review_result)
    assert results["no_tool_denied"] == "pass" and results["turns"] == "pass"
    assert json.loads((tmp_path / f"{p.id}.scorecard.json").read_text()) == card


def test_the_grader_prompt_scores_setup_only_from_calls_that_happened():
    from scripts.coach_eval import run

    for fact in (run.NO_REVIEW_FACT, run.review_fact(_flat(_use(rubric.REVIEW, [_SOURCED])))):
        text = run.GRADER_PROMPT.format(review_fact=fact, transcript="COACH: hi")
        assert fact in text and text.endswith("COACH: hi")
        assert 'write "not exercised" for it' in text
        assert "ONLY from setup_review calls that actually happened" in text
    # a DENIED review is not a call that happened
    assert run.review_fact(_flat(_use(rubric.REVIEW, [_SOURCED], denied=True))) == (
        run.NO_REVIEW_FACT)


def test_a_grader_value_for_setup_review_reached_is_overwritten_by_the_fact(
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
            return [{"type": "result", "result": '{"setup_review_reached": true}'}]
        return [{"type": "result", "result": "DONE"}]

    _stub_claude(monkeypatch, reply)
    p = personas.load_personas(ROOT / "scripts" / "coach_eval" / "personas")[0]
    assert run.run_persona(p, tmp_path)["llm_graded"]["setup_review_reached"] is False


@pytest.mark.parametrize("coach_calls,expected", [
    ((rubric.STATUS,), "not exercised"),
    ((rubric.STATUS, rubric.REVIEW), 4),
])
def test_asked_before_proposing_is_not_exercised_without_a_review_whatever_the_grader_says(
        monkeypatch, tmp_path, coach_calls, expected):
    from scripts.coach_eval import run

    init = {"type": "system", "subtype": "init", "session_id": "s", "plugins": [],
            "claude_code_version": next(iter(isolation.MEASURED_VERSIONS)),
            "mcp_servers": [{"name": "sluice", "status": "connected"}],
            "tools": sorted(run.COACH_EXPECTED_TOOLS)}

    def reply(cmd, kw):
        if "--mcp-config" in cmd:
            return [init, *_flat(*[_use(n, [_SOURCED] if n == rubric.REVIEW else None)
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
    events = _flat(*[_use(n, [_SOURCED] if n == rubric.REVIEW else None) for n in calls])
    transcript = ["COACH: hello", "USER: hi", "COACH: bye", "USER: DONE"]
    (tmp_path / f"{pid}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    (tmp_path / f"{pid}.transcript.txt").write_text("\n".join(transcript))
    return pid, events, transcript


def _grader_stub(monkeypatch):
    return _stub_claude(monkeypatch, lambda c, k: [
        {"type": "result", "result": json.dumps(_GRADE)}])


def test_regrade_writes_the_expected_file_and_spends_only_the_grader(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS, rubric.REVIEW))
    seen = _grader_stub(monkeypatch)
    run.main(["--regrade", str(tmp_path), "--grader-model", "sonnet"])
    assert len(seen) == 1
    card = json.loads((tmp_path / f"{pid}.regrade-sonnet.json").read_text())
    assert card["persona"] == pid and card["grader_model"] == "sonnet"
    assert card["llm_graded"]["asked_before_proposing"] == 4
    assert card["llm_graded"]["setup_review_reached"] is True


def test_a_regrade_sends_the_prompt_a_live_run_would(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    _, events, transcript = _saved_run(tmp_path, (rubric.STATUS, rubric.REVIEW))
    seen = _grader_stub(monkeypatch)
    run.regrade(tmp_path, "haiku")
    live = run.GRADER_PROMPT.format(review_fact=run.review_fact(events),
                                    transcript="\n".join(transcript))
    assert seen[0][1]["input"] == live


def test_the_not_exercised_overwrite_applies_in_a_regrade(monkeypatch, tmp_path):
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS,))
    _grader_stub(monkeypatch)
    run.regrade(tmp_path, "haiku")
    llm = json.loads((tmp_path / f"{pid}.regrade-haiku.json").read_text())["llm_graded"]
    assert llm["asked_before_proposing"] == "not exercised"
    assert llm["setup_review_reached"] is False


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

    pid, events, transcript = _saved_run(tmp_path, (rubric.STATUS, rubric.REVIEW))
    _saved_run(tmp_path, (rubric.STATUS,), pid="second")
    seen = _grader_stub(monkeypatch)
    run.main(["--print-grader-prompt", str(tmp_path)])
    assert seen == []  # no claude call of any kind
    text = (tmp_path / f"{pid}.grade-me.txt").read_text()
    assert text == run.grader_prompt(events, transcript)
    assert text.startswith("Grade this") and text.endswith("USER: DONE")
    assert "successfully 1 time(s)" in text
    assert run.NO_REVIEW_FACT in (tmp_path / "second.grade-me.txt").read_text()


def test_the_printed_prompt_is_the_one_the_grader_is_sent(monkeypatch, tmp_path):
    # The cross-check is only worth anything if the pasted prompt IS the grader's prompt.
    from scripts.coach_eval import run

    pid, _, _ = _saved_run(tmp_path, (rubric.STATUS, rubric.REVIEW))
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


def test_brief_cites_sources_is_checked_per_review_call_carrying_a_brief():
    """One sourced brief early in the run must not cover an unsourced brief proposed later."""
    events = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_SOURCED]),
                   _use(rubric.REVIEW, [_UNSOURCED]))
    assert rubric.deterministic(events, max_turns=30)["brief_cites_sources"][0] is False
    both = _flat(_use(rubric.STATUS), _use(rubric.REVIEW, [_SOURCED]),
                 _use(rubric.REVIEW, [_UNSOURCED, _SOURCED]))
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
