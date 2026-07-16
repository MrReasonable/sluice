"""The no-bypass guard.

A guard that can be evaded is worse than no guard, because it manufactures confidence. A
security review of the first version found five evasions, three of which needed no
obfuscation at all -- a well-intentioned agent would have typed them by accident. Those are
the ones that matter, and they are pinned below.

What is deliberately NOT pinned: evasion by shell metaprogramming (`--ad""min`, `$VAR`,
base64, `python3 -c`). A denylist over a shell command is unsound by construction and cannot
be made sound. This guard's threat model is RATIONALISATION, not EVASION -- it stops an
agent that wants to comply and is drifting. The authoritative gate is the repository
ruleset, on the far side of the trust boundary, where the agent gets no vote.

The line between those two sets is "would a COMPLYING agent type this?", and it is easy to
draw in the wrong place. `cd /tmp; gh pr merge --admin`, a multi-line bash block, and
`git -C /path push --force origin main` all look like evasion tricks and are not: they are
ordinary forms, and an earlier version of this suite pinned none of them, so the guard
allowed all of them while every test stayed green.

FALSE POSITIVES ARE PINNED AS HARD AS THE BLOCKS. The first version blocked
`git commit -m "feat: add -n flag docs"` and `git push --force-with-lease origin
fix/main-menu`, each with a message confidently describing a bypass that was not happening.
An agent that reads a wrong explanation learns the guard is noise -- the precise belief it
needs to rationalise past the next block. Those cases are regression tests now.

SHAPE: the denylist decision is pure (`blocked_reason`) and tested in-process; only the
process contract -- `__main__` -> `sys.exit(main())` -> exit code -- is worth a subprocess.
A wholesale in-process port would be cheaper still and WRONG: the hook contract IS the exit
code, so deleting `sys.exit(main())` would leave such a suite green while the guard did
nothing.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guard_no_bypass import blocked_reason, main

GUARD = Path(__file__).parent.parent / "scripts" / "guard_no_bypass.py"

# Synthetic throughout: the guard's matcher never inspects the org, so a placeholder proves
# the rule is not coupled to this repo -- which the real slug could not.
BLOCKED = [
    # The command I actually ran.
    "gh pr merge 16 --rebase --admin --delete-branch",
    # No obfuscation needed: this reaches the same REST endpoint without going near
    # `gh pr merge`. It was the biggest hole in v1. Only PUT merges, in every spelling.
    "gh api -X PUT repos/acme/widget/pulls/16/merge -f merge_method=rebase",
    "gh api --method PUT repos/acme/widget/pulls/16/merge",
    "gh api --method=PUT repos/acme/widget/pulls/16/merge",
    "gh api -XPUT repos/acme/widget/pulls/16/merge",
    # -n is the short form of --no-verify for `commit`.
    "git commit -n -m 'x'",
    "git commit --no-verify -m 'x'",
    "git push --no-verify origin feature",
    # +refspec force-pushes without ever saying --force.
    "git push origin +main",
    "git push --force origin main",
    "git push -f origin HEAD:main",
    "git push -f origin HEAD:refs/heads/main",
    "git push --force-with-lease origin main",
    # A blocked command does not become legitimate by sharing a line with a legitimate one.
    # EVERY separator is pinned, not just the conventionally-spaced `&&`: the first version
    # of this suite pinned only `&&`, which is why it stayed green while `;` and newline
    # forms walked straight through.
    "cd /tmp && gh pr merge 1 --admin",
    "cd /tmp; gh pr merge 1 --admin",  # `;` glues to `/tmp` without punctuation_chars
    "gh pr merge 1 --admin; echo done",  # ...and to `--admin` on the other side
    "git push --force origin main; true",  # ...and to the refspec
    "cd /repo\ngh pr merge 16 --admin",  # a newline is whitespace to shlex.split
    "(gh pr merge 1 --admin)",  # a subshell would leave `(` at the head of the segment
    # Global options before the subcommand name the same act as the bare form. `git -C` is
    # what an agent told to use absolute paths types by default.
    "git -C /repo push --force origin main",
    "git -C /repo commit --no-verify -m 'x'",
    "gh -R acme/widget pr merge 1 --admin",
    "GIT_DIR=/repo/.git git push --force origin main",
    # The `=value` spelling is the same flag.
    "gh pr merge 16 --admin=true",
    # `--all` and `--mirror` push main without ever naming it, so the refspec parser below
    # never sees a destination to compare. They are NOT the ambiguous bare-push case: `--all`
    # pushes every local branch and `--mirror` every ref, so main is included by definition,
    # not by a `push.default` that has to be guessed.
    "git push --all --force origin",
    "git push --force --all origin",  # flag order is not a semantic
    "git push -f --all origin",
    "git push --force-with-lease --all origin",
    "git push --all --force",  # the remote is optional; the danger is not
    # `git push -h` says, verbatim: `--[no-]branches   alias of --all`. Matching the string
    # `--all` alone closes one spelling of the escape and leaves its twin open, which is the
    # over-claiming this guard exists to avoid. Verified against git 2.55.0: `git push
    # --branches --dry-run origin` pushes main.
    "git push --branches --force origin",
    "git push --branches -f origin",
    # A wildcard refspec names main without spelling it. `refs/heads/*:refs/heads/*` parses to
    # destination `*`, which is not the string `main` but matches it. Verified: the dry-run
    # pushes main.
    "git push --force origin refs/heads/*:refs/heads/*",
    "git push --force origin *:*",
    "git push origin +refs/heads/*:refs/heads/*",  # the + form needs no --force
    # --mirror needs no force flag: it force-pushes every ref AND deletes remote refs that
    # are absent locally. It is the most destructive spelling here and the only one that
    # says nothing about force at all.
    "git push --mirror origin",
    "git push --mirror",
]

ALLOWED = [
    "gh pr merge 17 --rebase --delete-branch",  # the RIGHT way to merge
    "gh api repos/acme/widget/pulls/17/comments",  # reading a PR is not merging it
    # `gh api` defaults to GET, and GET /pulls/N/merge is the read-only "is it merged?"
    # check (204/404). An earlier version matched the PATH alone and refused this while
    # claiming the caller was merging -- it blocked its own author, mid-review, saying so.
    "gh api repos/acme/widget/pulls/17/merge",
    "gh api -X GET repos/acme/widget/pulls/17/merge",
    'git commit -m "fix(triage): thing"',
    "git commit -am 'x'",
    "git push -u origin chore/no-bypass-guard",
    "git push --force-with-lease origin feat/some-branch",  # fine: not main
    "python -m pytest -n auto",  # -n here is xdist, not --no-verify
    "ruff check sluice tests",
    # --- regressions: every one of these was blocked by v1, with a wrong explanation ---
    # The `-n` lived inside the commit message, not in an argument position.
    'git commit -m "feat: add -n flag docs"',
    # For `push`, `-n` is --dry-run: the safest push there is.
    "git push -n origin main",
    # `main` lived inside the branch name, and `--force` inside `--force-with-lease`.
    "git push --force-with-lease origin fix/main-menu",
    # No refspec: the target is undeterminable from the string, so the ruleset decides.
    # This is the form path-to-green and address-comments actually use.
    "git push --force-with-lease",
    # Splitting on separators must not turn a neighbour into a false positive either.
    "cd /repo && git push -u origin feat/x",
    "git -C /repo push -u origin feat/x",
    # --all WITHOUT a force flag rewrites nothing: it is an ordinary fast-forward push of
    # every branch, and a non-fast-forward is what the server is for. Blocking it would make
    # the guard refuse a routine command while claiming the caller was rewriting main --
    # the v1 mistake this suite's regression block exists to prevent. `--mirror` is NOT
    # paired here, deliberately: it force-pushes with no flag, so it has no safe spelling.
    "git push --all origin",
    "git push --all",
    "git push --branches origin",  # the alias is not dangerous either, without force
    # A wildcard that cannot match `main` must stay allowed: the destination is parsed and
    # matched, not grepped for `main`. `feat/*` is the glob equivalent of the fix/main-menu
    # regression below -- it is why this uses fnmatch on the parsed destination rather than
    # treating every `*` as main.
    "git push --force origin refs/heads/feat/*:refs/heads/feat/*",
]


def _run(command):
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("command", BLOCKED)
def test_the_bypass_is_blocked(command):
    assert blocked_reason(command) is not None, f"guard did not block: {command}"


@pytest.mark.parametrize("command", ALLOWED)
def test_legitimate_work_is_not_blocked(command):
    assert blocked_reason(command) is None, f"guard raised a FALSE POSITIVE on: {command}"


def test_the_explanation_names_the_specific_act():
    """The explanation is the product: the exit code is already guaranteed server-side.

    Without this, `_REPORT_NOT_ROUTE_AROUND` -- the payload answering the exact
    rationalisation behind the original incident -- could be deleted and every other test
    would stay green.
    """
    assert "--admin bypasses the branch ruleset" in blocked_reason("gh pr merge 1 --admin")
    assert "REST API" in blocked_reason("gh api -X PUT repos/acme/widget/pulls/1/merge")
    assert "--no-verify bypasses" in blocked_reason("git commit -n -m 'x'")
    assert "rewrites shared history" in blocked_reason("git push --force origin main")
    # --mirror earns its own sentence. The generic force-push message is true of it but
    # incomplete, and its parenthetical points at the +main refspec form -- an unrelated
    # spelling. --mirror also DELETES remote refs that are absent locally, which no other
    # blocked form does, and an agent told only "force-pushing main rewrites shared history"
    # has not been told the thing that actually makes --mirror the worst of them.
    assert "--mirror" in blocked_reason("git push --mirror origin")
    assert "delete" in blocked_reason("git push --mirror origin").lower()


def test_the_hook_contract_a_blocked_command_exits_2_and_explains():
    proc = _run("gh pr merge 16 --rebase --admin --delete-branch")
    assert proc.returncode == 2
    # `== 2` ALONE IS NOT DISCRIMINATING: CPython also exits 2 when it cannot open the
    # script, so an exit-code-only assertion passes even when the hook path is broken and
    # the guard never runs. Pinning the stderr text is what makes this test mean something.
    assert "BLOCKED by scripts/guard_no_bypass.py" in proc.stderr
    assert "FACT TO REPORT TO THE HUMAN" in proc.stderr


def test_the_hook_contract_a_legitimate_command_exits_0_silently():
    proc = _run("gh pr merge 17 --rebase --delete-branch")
    assert proc.returncode == 0
    assert proc.stderr == ""


@pytest.mark.parametrize(
    "payload",
    [
        "not json",  # not JSON at all
        "null",  # valid JSON, decodes to None -- v1 crashed here with a traceback
        "[]",  # valid JSON, wrong type
        '"a string"',
        "{}",  # no tool_input
        '{"tool_input": "not a dict"}',
        '{"tool_input": {}}',  # no command
        '{"tool_input": {"command": null}}',
    ],
)
def test_a_malformed_payload_never_breaks_the_harness(payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert main() == 0


def test_an_unparseable_command_fails_open(monkeypatch):
    """An unbalanced quote is not something a denylist can reason about.

    Failing open is deliberate: the threat model is a drifting agent, not an evader, and a
    guard that refused every command it could not tokenise would become the problem.
    """
    assert blocked_reason('git commit -m "unbalanced') is None
