#!/usr/bin/env python3
"""PreToolUse guard: block obvious network-capable Bash commands for the review team only.

WHY THIS EXISTS
----------------
The five review-team subagents (`.rulesync/subagents/sluice-*.md`, #102) each carry prose
saying they have no `WebSearch`/`WebFetch` and "everything needed to judge a diff is already
in front of you." That claim was FALSE as shipped: dropping the two purpose-built search
tools does nothing to `Bash`, which can still reach the network directly -- `curl`, `git
fetch`, `gh api`, `pip install`, and a dozen others all still worked. Two independent
reviewers (this repo's own `sluice-reviewer` and CodeRabbit's cloud pass) found the same gap
independently, from different angles, on the same PR. This closes it for real, the same way
`guard_no_bypass.py` closes its gap: a `PreToolUse` hook, not a sentence the model is trusted
to honour.

SCOPED TO THE REVIEW TEAM, NOT THE SESSION
-------------------------------------------
This is NOT a blanket network ban. Claude Code's `PreToolUse` payload carries `agent_type`
only when the tool call happened inside a dispatched subagent -- absent for the main session.
The orchestrating `review-pr` skill runs `git fetch`/`gh pr view` from the MAIN session, to
build the diff a reviewer is handed; blocking those would break the workflow this guard exists
to protect. So the check is agent_type-gated FIRST, before any command parsing: anything that
is not one of the five review agents is waved through untouched, on line one.

THE SAME LIMITATION AS `guard_no_bypass.py`, STATED THE SAME WAY
-------------------------------------------------------------------
A denylist over a shell command is unsound by construction. `python3 -c "import
urllib.request; urllib.request.urlopen(...)"` reaches the network and this guard cannot see
it, short of interpreting arbitrary Python -- which is not a hook's job. The threat model here
is IDENTICAL to `guard_no_bypass.py`'s: a reviewer that wants to comply and reaches for an
obvious tool (`curl`, `gh`, `pip install`) by habit, not one actively trying to exfiltrate
data through an interpreter. Front-running the obvious path is still worth doing, and claiming
more than that would repeat the exact overclaim this guard was written to fix.

WHY A SEPARATE SCRIPT RATHER THAN EXTENDING `guard_no_bypass.py`
---------------------------------------------------------------------
That guard's threat model is bypass-of-a-repo-gate (force-push, --no-verify, --admin merge)
and applies to EVERY Bash call in the session. This one's threat model is
egress-from-a-read-only-reviewer and applies to FIVE agent types only. Folding them into one
script would mean every call pays the agent_type check, and a change to one guard's denylist
risks the other's test suite -- two independent concerns, two independent files, exactly the
existing split's own reasoning.

Tokenising and segmenting are reused from `guard_no_bypass.py` rather than reimplemented: that
tokeniser is the one already hardened against `cd /tmp; rm x`, multi-line blocks, and
`git -C`-prefixed forms (see its own docstring). A second, less-careful implementation here
would silently miss a case the first one already covers.
"""
import json
import sys
from pathlib import Path

# Claude Code invokes this script directly (`python3 ".../scripts/guard_reviewer_egress.py"`),
# which puts `scripts/` itself at sys.path[0] -- not the repo root -- so `import
# scripts.guard_no_bypass` fails with ModuleNotFoundError in exactly that invocation, the one
# production actually uses. It does NOT fail under pytest, which puts the repo root on
# sys.path first, so this gap is invisible unless the script is actually run as a subprocess --
# which `tests/test_guard_reviewer_egress.py`'s hook-contract tests do, and that is what caught
# it. Insert the repo root explicitly so the import resolves the same way under both callers.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.guard_no_bypass import _normalise, _segments, _starts_with, _tokenise  # noqa: E402

# The five review-team subagents wired into `/review-pr` (Step 2 of
# `.rulesync/skills/review-pr/SKILL.md`). Hand-listed, not derived from the filesystem at
# hook-invocation time -- a hook that reads `.rulesync/subagents/*.md` on every Bash call adds
# a filesystem dependency to a security-relevant hot path for no real benefit. The drift risk
# that hand-listing creates (a sixth reviewer added later, silently unguarded) is closed by
# `tests/test_guard_reviewer_egress.py::test_review_agents_matches_the_rulesync_roster`
# instead, which enumerates the real subagent files and asserts this set against them.
REVIEW_AGENTS = frozenset({
    "sluice-reviewer",
    "sluice-invariant-reviewer",
    "sluice-neutrality-reviewer",
    "sluice-architect",
    "sluice-test-engineer",
})

# Tools whose entire purpose is reaching outside the local checkout. Not exhaustive -- see the
# module docstring -- but every one here is a command a complying reviewer would reach for
# BY HABIT, not by intent to evade: `curl`/`wget` to "just check the URL", `gh` to look up
# issue context the prompt already contains, `pip`/`npm`/`uv` out of reflex, `dig`/`nslookup`
# because they feel like harmless diagnostics (both can exfiltrate via a DNS query name).
_NETWORK_TOOLS = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "telnet", "ftp", "sftp", "ssh", "scp", "rsync",
    "dig", "nslookup", "host", "gh", "pip", "pip3", "npm", "npx", "yarn", "pnpm", "uv",
})

# git subcommands that leave the local checkout. Deliberately NOT the whole `git` binary --
# `diff`/`log`/`blame`/`show`/`status` are exactly what a reviewer's job requires, and blocking
# `git` outright would break every reviewer's own "How you work" instructions.
_NETWORK_GIT_SUBCOMMANDS = frozenset({"fetch", "pull", "push", "clone", "ls-remote"})

_WHY = (
    "A review subagent has everything it needs already in its prompt -- the diff, the changed"
    " files, the hard rules. This command reaches (or could reach) outside the local checkout,"
    " which is exactly what a review role should never need to do. If the review genuinely"
    " requires more context, say so in the findings instead of fetching it."
)


def blocked_reason(command, agent_type):
    """The reason `command` is refused for `agent_type`, or None if it is allowed.

    Pure and tested in-process, mirroring `guard_no_bypass.blocked_reason`'s own shape.
    """
    if agent_type not in REVIEW_AGENTS:
        return None
    try:
        tokens = _tokenise(command)
    except ValueError:
        # Same call as guard_no_bypass.py: a command the tokeniser cannot parse is one bash
        # would not run either, so refusing it blindly would only produce false positives.
        return None
    for raw_segment in _segments(tokens):
        segment = _normalise(raw_segment)
        if not segment:
            continue
        if segment[0] in _NETWORK_TOOLS:
            return _WHY
        if (
            _starts_with(segment, "git")
            and len(segment) >= 2
            and segment[1] in _NETWORK_GIT_SUBCOMMANDS
        ):
            return _WHY
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # never break the harness on a malformed payload

    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0
    agent_type = payload.get("agent_type")
    if not isinstance(agent_type, str):
        return 0

    why = blocked_reason(command, agent_type)
    if why is None:
        return 0
    print(f"BLOCKED by scripts/guard_reviewer_egress.py\n\n{why}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
