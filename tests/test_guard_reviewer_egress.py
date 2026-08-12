"""The reviewer-egress guard (#102).

Two independent reviewers (this repo's own `sluice-reviewer` and CodeRabbit's cloud pass) found
the same gap on the same PR: dropping `WebSearch`/`WebFetch` from the five review-team subagents
did nothing to `Bash`, which could still reach the network directly. This guard is what makes
that restriction real rather than aspirational prose -- see `scripts/guard_reviewer_egress.py`'s
module docstring for the full reasoning, including why it is agent_type-gated and why it does
not attempt to be sound against a determined evader (the same stated limitation as
`guard_no_bypass.py`).
"""
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guard_reviewer_egress import REVIEW_AGENTS, blocked_reason, main

GUARD = Path(__file__).parent.parent / "scripts" / "guard_reviewer_egress.py"
SUBAGENTS_DIR = Path(__file__).parent.parent / ".rulesync" / "subagents"

BLOCKED = [
    "curl https://example.invalid/x",
    "wget https://example.invalid/x",
    "nc example.invalid 80",
    "ssh example.invalid",
    "scp file example.invalid:/tmp",
    "rsync -av ./x example.invalid:/tmp",
    "dig example.invalid",
    "nslookup example.invalid",
    "gh pr view",
    "gh api repos/acme/widget/pulls/1",
    "pip install requests",
    "npm install left-pad",
    "uv pip install requests",
    "git fetch origin",
    "git pull origin main",
    "git push origin feat/x",
    "git clone https://example.invalid/repo.git",
    "git ls-remote origin",
    # Global options before the subcommand name the same act as the bare form -- the shared
    # `_normalise` this guard reuses from guard_no_bypass.py is what buys this for free.
    "git -C /repo fetch origin",
    # A blocked command does not become legitimate by sharing a line with a legitimate one.
    "git diff origin/main...HEAD && gh pr view",
    "cd /tmp; curl https://example.invalid",
]

ALLOWED_UNDER_REVIEW_AGENT = [
    "git diff origin/main...HEAD",
    "git log --oneline -5",
    "git blame sluice/core/vault.py",
    "git status",
    "git show HEAD",
    "git checkout sluice/foo.py",  # mutation-testing restore, sluice-test-engineer's own workflow
    "python -m pytest -q",
    "python -m compileall -q -f --invalidation-mode checked-hash sluice tests",
    "ruff check sluice tests",
    "grep -rn foo sluice/",
]


def _run(command, agent_type="sluice-reviewer"):
    payload = {"tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("command", BLOCKED)
@pytest.mark.parametrize("agent", sorted(REVIEW_AGENTS))
def test_network_commands_are_blocked_for_every_review_agent(command, agent):
    assert blocked_reason(command, agent) is not None, f"[{agent}] guard did not block: {command}"


@pytest.mark.parametrize("command", ALLOWED_UNDER_REVIEW_AGENT)
@pytest.mark.parametrize("agent", sorted(REVIEW_AGENTS))
def test_local_work_is_not_blocked_for_a_review_agent(command, agent):
    assert blocked_reason(command, agent) is None, (
        f"[{agent}] guard raised a FALSE POSITIVE on: {command}"
    )


@pytest.mark.parametrize("command", BLOCKED)
def test_the_same_commands_are_allowed_outside_the_review_team(command):
    """The whole point of agent_type-gating: the orchestrating review-pr skill runs `git
    fetch`/`gh pr view` from the MAIN session to build the diff a reviewer is handed. If this
    guard fired there too, it would break the workflow it exists to protect.
    """
    assert blocked_reason(command, None) is None
    assert blocked_reason(command, "Explore") is None
    assert blocked_reason(command, "general-purpose") is None


def test_the_hook_contract_a_blocked_command_exits_2_and_explains():
    proc = _run("curl https://example.invalid", agent_type="sluice-reviewer")
    assert proc.returncode == 2
    # `== 2` alone is not discriminating: CPython also exits 2 when it cannot open the script,
    # so an exit-code-only assertion passes even when the hook path is broken. Pinning the
    # stderr text is what makes this test mean something.
    assert "BLOCKED by scripts/guard_reviewer_egress.py" in proc.stderr


def test_the_hook_contract_a_legitimate_command_exits_0_silently():
    proc = _run("git diff origin/main...HEAD", agent_type="sluice-reviewer")
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_the_hook_contract_the_same_blocked_command_is_silent_outside_the_review_team():
    proc = _run("curl https://example.invalid", agent_type=None)
    assert proc.returncode == 0
    assert proc.stderr == ""


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "null",
        "[]",
        '"a string"',
        "{}",
        '{"tool_input": "not a dict"}',
        '{"tool_input": {}}',
        '{"tool_input": {"command": null}}',
        # agent_type present but the wrong type -- must not raise on a membership test against it
        '{"tool_input": {"command": "curl x"}, "agent_type": 5}',
    ],
)
def test_a_malformed_payload_never_breaks_the_harness(payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert main() == 0


def test_an_unparseable_command_fails_open():
    """Same call as guard_no_bypass.py, for the same reason: a command the tokeniser cannot
    parse is one bash would not run either, so refusing it blindly only produces false
    positives.
    """
    assert blocked_reason('curl "unbalanced', "sluice-reviewer") is None


def test_review_agents_matches_the_rulesync_roster():
    """`REVIEW_AGENTS` is hand-listed in the guard script rather than read from disk at
    hook-invocation time (see its own module comment for why). This is the drift check that
    licenses that choice: it enumerates the REAL subagent source files and asserts the
    hand-listed set names exactly the same agents, so a sixth reviewer added later -- or one
    renamed -- cannot silently go unguarded.
    """
    names = set()
    for path in SUBAGENTS_DIR.glob("*.md"):
        match = re.search(r"^name:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert match, f"{path} has no `name:` frontmatter field to enumerate"
        names.add(match.group(1))
    assert names, "no subagent files found: this sweep would pass vacuously without checking"
    assert REVIEW_AGENTS == names, (
        f"REVIEW_AGENTS in scripts/guard_reviewer_egress.py has drifted from the real "
        f".rulesync/subagents/*.md roster.\n"
        f"  in REVIEW_AGENTS but not on disk: {sorted(REVIEW_AGENTS - names)}\n"
        f"  on disk but not in REVIEW_AGENTS: {sorted(names - REVIEW_AGENTS)}"
    )
