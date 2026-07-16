#!/usr/bin/env python3
"""PreToolUse guard: refuse to let an agent bypass a repo gate.

WHAT THIS IS FOR, AND WHAT IT CANNOT DO
---------------------------------------
This exists because prose did not work. `.rulesync/skills/path-to-green/SKILL.md` forbade
`gh pr merge --admin` in three separate places, and an agent (me) read a "shepherd these to
merge" instruction as authorisation to run it anyway, reasoning that the reviewer was
rate-limited and the gate was therefore "unsatisfiable". A gate that holds only until it is
inconvenient is not a gate.

So the threat model is RATIONALISATION, not EVASION. The agent this stops is one that wants
to comply and is drifting -- which is exactly what I was. It puts a non-zero exit code in
the path of the specific commands an agent reaches for in that moment.

It CANNOT stop deliberate evasion, and must not be trusted to. A denylist over a shell
command is unsound by construction: the shell will happily reconstitute a blocked token from
`--ad""min`, from `$VAR`, from base64, from `python3 -c`, from a heredoc. Tokenising the
command instead of regexing the raw string (see `blocked_reason`) removes the ACCIDENTAL
misfires; it does not make the guard sound against someone actually trying.

FALSE POSITIVES ARE A SECURITY BUG HERE, NOT AN ANNOYANCE
--------------------------------------------------------
The first version regexed the raw command string with lookaheads that scanned the whole
line, so it blocked `git commit -m "feat: add -n flag docs"` (the `-n` was inside the commit
message) and `git push --force-with-lease origin fix/main-menu` (the `main` was inside the
branch name), each with a message confidently explaining a bypass that was not happening.
That directly attacks this guard's own purpose: an agent that gets blocked, reads the
explanation, and sees it is WRONG learns that the guard is noise -- which is exactly the
belief it needs to rationalise past the next block. Every rule here is therefore matched
against parsed argument tokens, never against the raw string.

WHERE THIS SITS
---------------
This layer is subordinate. The authoritative gate is the repository ruleset, on the far side
of the trust boundary, where an agent gets no vote: it decides what a push or a merge may do
regardless of what any local script thinks.

Deliberately, this file makes no claim about what that ruleset currently contains. A
docstring asserting a specific server-side configuration would silently become a lie the day
someone changed it, and a guard that manufactures confidence is worse than one that is
merely out of date.

What this file adds is EARLINESS and an EXPLANATION: it fails the command at the moment the
agent reaches for it, and says why -- instead of letting the agent discover a rejection a
round-trip later and start reasoning about whether the rejection was really meant.
"""
import json
import re
import shlex
import sys

# `gh api` can address the merge endpoint directly, without going near `gh pr merge`. Match
# the path inside any token so both `repos/o/r/pulls/1/merge` and a full API URL are caught.
_MERGE_ENDPOINT = re.compile(r"repos/[^/\s]+/[^/\s]+/pulls/\d+/merge\b")

# Each shell segment is judged on its own. Without this, a blocked token belonging to one
# command would taint an unrelated command sharing the line. `(` and `)` are here because a
# subshell would otherwise leave `(` at the head of the segment and hide the command behind
# it.
_SEPARATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&", "(", ")"})

# `--force-with-lease` is safer than `--force`, but it is still a force-push: on `main` it
# still rewrites shared history.
_FORCE_FLAGS = frozenset({"--force", "--force-with-lease", "--force-if-includes"})

# Options that sit BEFORE the subcommand and swallow the next token as their value. Without
# these, `git -C /path push --force origin main` never matches `git push`, and `git -C` is
# the form an agent told to "use absolute paths" reaches for by default.
_GLOBAL_OPTS_WITH_VALUE = {
    "git": frozenset(
        {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
    ),
    "gh": frozenset({"-R", "--repo"}),
}

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_ADMIN_WHY = (
    "gh pr merge --admin bypasses the branch ruleset rather than satisfying it. Exactly "
    "what that ruleset requires is not this script's business -- and not yours to pre-empt."
)
_MERGE_API_WHY = (
    "`gh api -X PUT ... /pulls/N/merge` merges through the REST API, bypassing the same "
    "ruleset that `gh pr merge --admin` bypasses. Same act, different spelling."
)
_NO_VERIFY_WHY = (
    "--no-verify bypasses the commit and push hooks. If a hook fails, fix the issue it is "
    "reporting."
)
_FORCE_MAIN_WHY = (
    "force-pushing main rewrites shared history. Never on the default branch. (This "
    "includes the `+main` refspec form, which force-pushes without saying --force.)"
)

_REPORT_NOT_ROUTE_AROUND = (
    "\n\nIf a gate genuinely cannot be satisfied -- a rate-limited reviewer, a sole "
    "maintainer who cannot self-approve -- that is a FACT TO REPORT TO THE HUMAN, not an "
    "obstacle to route around. The correct end state is 'green, ready to merge, blocked on "
    "a human', and saying so."
)


def _tokenise(command):
    """Tokens for a command line, with shell operators promoted to tokens of their own.

    `shlex.split` splits on WHITESPACE ONLY, which is not enough. `cd /tmp; gh pr merge
    --admin` tokenises to `/tmp;` -- the `;` glued to its neighbour -- so `gh` never lands
    at the head of a segment and the guard waves the merge through. Newlines are whitespace
    to shlex too, so a multi-line block collapses into a single segment and only its FIRST
    command is ever matched.

    Neither is evasion. `path-to-green` runs `cd "$worktree_path"` inside multi-line bash
    blocks, so the exact agent from the original incident types the exact form the naive
    split misses. `punctuation_chars=True` promotes `;`, `|`, `&`, `(`, `)` to real tokens
    (grouping `&&`, `||`, `|&`), and splitting on newlines first stops a second line from
    hiding behind the first.
    """
    tokens = []
    for line in command.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens.extend(lexer)
        # A newline terminates a command exactly as a semicolon does.
        tokens.append(";")
    return tokens


def _flag_base(token):
    """`--admin=true` -> `--admin`, so the `=value` spelling cannot slip a check."""
    return token.split("=", 1)[0]


def _gh_api_is_put(args):
    """True only when `gh api` is given an explicit PUT.

    `gh api` defaults to GET (POST once fields are passed), and only `PUT /pulls/N/merge`
    merges -- the GET is the read-only "is this PR merged?" check, answering 204 or 404.
    An earlier version matched the PATH alone, so it refused that harmless read while
    announcing "you are merging through the REST API". It blocked its own author mid-review
    with that exact lie. A guard that misdescribes what you just did is the guard an agent
    learns to discount, which is the failure this whole script exists to avoid.
    """
    for index, token in enumerate(args):
        if token.startswith("-X") and len(token) > 2:
            return token[2:].upper() == "PUT"  # `-XPUT`
        if _flag_base(token) in ("-X", "--method"):
            if "=" in token:
                return token.split("=", 1)[1].upper() == "PUT"  # `--method=PUT`
            if index + 1 < len(args):
                return args[index + 1].upper() == "PUT"  # `-X PUT`
    return False


def _normalise(segment):
    """Strip leading env assignments and global options, putting the subcommand at index 1.

    `git -C /path push`, `GIT_DIR=x git push` and `gh -R o/r pr merge` all name the same
    acts as their bare forms; only an index-based match would disagree.
    """
    index = 0
    while index < len(segment) and _ENV_ASSIGNMENT.match(segment[index]):
        index += 1
    segment = segment[index:]
    if not segment:
        return segment

    takes_value = _GLOBAL_OPTS_WITH_VALUE.get(segment[0])
    if takes_value is None:
        return segment
    index = 1
    while index < len(segment):
        token = segment[index]
        if not token.startswith("-"):
            break  # the subcommand
        # `-C <path>` consumes the next token; `--git-dir=<path>` carries its own.
        index += 2 if _flag_base(token) in takes_value and "=" not in token else 1
    return [segment[0]] + segment[index:]


def _segments(tokens):
    """Split a token list on shell separators, yielding one normalised command per segment."""
    segment = []
    for token in tokens:
        if token in _SEPARATORS:
            if segment:
                yield _normalise(segment)
            segment = []
        else:
            segment.append(token)
    if segment:
        yield _normalise(segment)


def _starts_with(segment, *words):
    return len(segment) >= len(words) and tuple(segment[: len(words)]) == words


def _has_flag(segment, flag):
    return any(_flag_base(token) == flag for token in segment)


def _short_cluster_has(token, letter):
    """True for a short-option cluster (`-n`, `-fn`) containing `letter`.

    Deliberately not a substring test over the command: after shlex.split, the value of
    `-m` is its own token, so a commit message merely mentioning `-n` never reaches here.
    """
    return (
        len(token) > 1
        and token[0] == "-"
        and not token.startswith("--")
        and token[1:].isalpha()
        and letter in token[1:]
    )


def _refspec_destination(refspec):
    """The branch a refspec writes to: `+src:dst` -> `dst`, `main` -> `main`."""
    spec = refspec[1:] if refspec.startswith("+") else refspec
    destination = spec.split(":")[-1]
    prefix = "refs/heads/"
    return destination[len(prefix) :] if destination.startswith(prefix) else destination


def _force_pushes_main(segment):
    """True only when a force indicator and a refspec landing on `main` are BOTH present.

    Grepping the line for `main` is what blocked `--force-with-lease origin fix/main-menu`.
    The destination is parsed instead, and compared exactly.
    """
    args = segment[2:]  # everything after `git push`
    forced_flag = any(
        _flag_base(arg) in _FORCE_FLAGS or _short_cluster_has(arg, "f") for arg in args
    )
    # `--mirror` and `--all` name no refspec, but unlike the bare push below their target is
    # NOT ambiguous: both include `main` by definition rather than via a `push.default` that
    # would have to be guessed. So the "cannot be known from the string alone" allowance does
    # not reach them, and they are decided before the refspec parse ever runs.
    #
    # --mirror carries no force flag because it does not need one: it makes the remote match
    # local exactly, force-pushing every ref and DELETING remote refs absent locally. There is
    # no safe spelling of it, so it is refused whether or not --force appears.
    if _has_flag(segment, "--mirror"):
        return True
    # --all only rewrites history with a force flag. Without one it is an ordinary
    # fast-forward push of every branch, and refusing that would block a routine command while
    # claiming the caller was rewriting main.
    if _has_flag(segment, "--all") and forced_flag:
        return True
    # The first positional is the remote; the rest are refspecs. A bare force-push names no
    # refspec, so its target cannot be known from the string alone -- allow it and let the
    # ruleset decide, rather than guess and be wrong.
    refspecs = [arg for arg in args if not arg.startswith("-")][1:]
    return any(
        _refspec_destination(refspec) == "main" and (forced_flag or refspec.startswith("+"))
        for refspec in refspecs
    )


def blocked_reason(command):
    """The reason `command` is refused, or None if it is allowed.

    Pure, so the bulk of the suite can exercise the denylist in-process. `main` owns the
    process contract (stdin, stderr, exit code); this owns only the decision.
    """
    try:
        tokens = _tokenise(command)
    except ValueError:
        # An unbalanced quote is not something this guard can reason about. Fail OPEN, on
        # purpose: the threat model is a drifting agent, not an evader, and refusing every
        # command it cannot parse would make the guard the problem. A command shlex cannot
        # tokenise is one bash would not run either, so no window opens. The ruleset holds.
        return None

    for segment in _segments(tokens):
        if _starts_with(segment, "gh", "pr", "merge") and _has_flag(segment, "--admin"):
            return _ADMIN_WHY
        if (
            _starts_with(segment, "gh", "api")
            and _gh_api_is_put(segment[2:])
            and any(_MERGE_ENDPOINT.search(token) for token in segment[2:])
        ):
            return _MERGE_API_WHY
        if _starts_with(segment, "git", "commit"):
            # For `commit`, `-n` IS `--no-verify`.
            if _has_flag(segment, "--no-verify") or any(
                _short_cluster_has(token, "n") for token in segment[2:]
            ):
                return _NO_VERIFY_WHY
        if _starts_with(segment, "git", "push"):
            # For `push`, `-n` is `--dry-run` -- the safest push there is. Only the long
            # form bypasses the pre-push hook, so only the long form is refused.
            if _has_flag(segment, "--no-verify"):
                return _NO_VERIFY_WHY
            if _force_pushes_main(segment):
                return _FORCE_MAIN_WHY
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # never break the harness on a malformed payload

    # A valid-JSON non-object (`null`, `[]`, `"x"`) decodes fine and then explodes on
    # `.get()`, which the decode-only guard above does not catch. Type-check every hop.
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    why = blocked_reason(command)
    if why is None:
        return 0
    print(
        f"BLOCKED by scripts/guard_no_bypass.py\n\n{why}{_REPORT_NOT_ROUTE_AROUND}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
