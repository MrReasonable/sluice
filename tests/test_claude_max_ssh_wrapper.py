"""The forced-command wrapper, exercised as a program (#209).

WHY THIS EXISTS. `packaging/claude-max-ssh-wrapper.sh` is the single thing standing between a key
that lives inside a container and a shell on the user's machine. It parses `SSH_ORIGINAL_COMMAND`,
which is supplied by whoever holds that key -- i.e. by exactly the party it defends against -- and
nothing else in this suite can see it: `pytest` and `ruff` read Python, shellcheck is a manual
step, and CI runs neither against `.sh`. Two independent reviewers called the absence of coverage
out on the same round, which is the corroboration signal worth listening to.

HOW IT IS TESTED. As a program: render the shipped template into a temp dir with `$CLAUDE`
pointed at a recording stub, run it under `/bin/sh` with a chosen `SSH_ORIGINAL_COMMAND`, and
assert on the exit status and the argv the stub recorded. That exercises the real control flow --
the word-splitting loop, the charset guard, the token export, the final `exec` -- rather than a
Python re-implementation of it, which would only prove the re-implementation right.

Offline and hermetic: no ssh, no network, no claude. The stub is the only thing executed.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WRAPPER = ROOT / "packaging" / "claude-max-ssh-wrapper.sh"

# The exact argv sluice's ClaudeMaxBackend sends, minus the leading binary. Kept here as the
# thing a caller PRESENTS, so a test can hand the wrapper a realistic command line.
_SLUICE_ARGV = ("--print --model claude-sonnet-4-5 --effort max "
                "--disallowedTools Write Edit NotebookEdit Bash "
                "--permission-mode bypassPermissions")


@pytest.fixture
def wrapper(tmp_path):
    """The shipped wrapper, rendered against a stub that records its argv instead of running.

    Returns `(run, argv_file, token_file)` where `run(ssh_original_command)` executes it.
    """
    stub = tmp_path / "claude-stub"
    argv_file = tmp_path / "argv.txt"
    # Records argv one-per-line and exits 0. Deliberately not a mock of claude's behaviour --
    # nothing here asserts on what claude DOES, only on what the wrapper decides to run.
    stub.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "{argv_file}"\n'
                    f'echo "TOKEN=${{CLAUDE_CODE_OAUTH_TOKEN:-<unset>}}" >> "{argv_file}"\n')
    stub.chmod(0o700)

    token_file = tmp_path / "token"
    rendered = tmp_path / "wrapper.sh"
    rendered.write_text(
        WRAPPER.read_text()
        .replace("__CLAUDE_PATH__", str(stub))
        .replace("__TOKEN_FILE__", str(token_file)))
    rendered.chmod(0o700)

    def run(ssh_original_command, env=None):
        e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
        if ssh_original_command is not None:
            e["SSH_ORIGINAL_COMMAND"] = ssh_original_command
        e.update(env or {})
        return subprocess.run(["/bin/sh", str(rendered)], capture_output=True, text=True,
                              env=e, input="prompt", timeout=30)

    return run, argv_file, token_file


def test_the_wrapper_ships_with_its_placeholders_unrendered():
    """SCOPE. Every test below renders the template; if the shipped file were already rendered,
    they would silently be testing someone's machine-specific copy rather than what we ship."""
    text = WRAPPER.read_text()
    assert "__CLAUDE_PATH__" in text and "__TOKEN_FILE__" in text
    assert os.access(WRAPPER, os.X_OK), "the wrapper must ship executable"


def test_a_normal_sluice_invocation_runs_claude_with_the_hosts_own_deny_list(wrapper):
    """The happy path, and the security property in one assertion.

    The caller's `--disallowedTools` is DISCARDED and the host's own is used. That is the whole
    design: the wrapper constructs the argv rather than validating it, so the deny-list cannot be
    supplied by the party it defends against.
    """
    run, argv_file, _ = wrapper
    r = run(f"/whatever/claude {_SLUICE_ARGV}")
    assert r.returncode == 0, r.stderr
    argv = argv_file.read_text().splitlines()
    assert argv[:5] == ["--print", "--model", "claude-sonnet-4-5", "--effort", "max"]
    denied = argv[argv.index("--disallowedTools") + 1:argv.index("--permission-mode")]
    # A SUPERSET of sluice's four -- the container-reachable key gets the stricter list.
    assert {"Write", "Edit", "NotebookEdit", "Bash"} <= set(denied)
    assert {"Task", "WebFetch", "mcp__*"} <= set(denied)
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


@pytest.mark.parametrize("command,why", [
    ("", "empty SSH_ORIGINAL_COMMAND"),
    ("/bin/sh", "a bare shell, which is what a key-holder would try first"),
    ("id; cat example-secret", "shell metacharacters"),
    ("/whatever/claude --print", "claude with no --model"),
    ("/whatever/claude --print --model", "--model with nothing after it"),
    ("/whatever/claude --print --model m", "a model but no --effort"),
])
def test_anything_that_is_not_a_complete_claude_invocation_is_refused(wrapper, command, why):
    """Refusal, not a default. Falling back to a default model on a malformed command would let a
    caller silently downgrade what runs; refusing is the only safe direction here."""
    run, argv_file, _ = wrapper
    r = run(command)
    assert r.returncode != 0, f"{why}: expected refusal, got 0"
    assert "refusing" in r.stderr, r.stderr
    assert not argv_file.exists(), f"{why}: claude was executed anyway"


@pytest.mark.parametrize("value", [
    "-oProxyCommand=id",     # a leading dash: claude would read it as an option
    "m;id",                  # command separator
    "m$(id)",                # substitution
    "m`id`",                 # legacy substitution
    "../../example-secret",  # traversal
])
def test_a_hostile_model_value_is_refused_rather_than_passed_on(wrapper, value):
    """The charset guard, against the shapes a key-holder would actually try.

    `exec` uses an argv array, so a metacharacter could not reach a shell even if it got through
    -- but a LEADING DASH genuinely would be read as a flag by claude, and that is the one this
    must catch. The rest are pinned because a future refactor could reintroduce an `eval`.
    """
    run, argv_file, _ = wrapper
    r = run(f"/whatever/claude --print --model {value} --effort max")
    assert r.returncode != 0, f"{value!r} was accepted"
    assert "refusing" in r.stderr
    assert not argv_file.exists()


def test_an_extra_token_after_the_model_is_discarded_not_forwarded(wrapper):
    """A space in the value cannot smuggle an extra argv entry, and the reason is the design.

    `--model m id` word-splits into three tokens; the loop copies only the one after `--model`
    and every other token is dropped, because the wrapper BUILDS the final argv rather than
    passing the caller's through. Measured: claude receives `--model m` and no `id` anywhere.

    Asserted as a discard rather than a refusal because that is what actually happens -- an
    earlier version of this test expected a non-zero exit, and the wrapper was right where the
    test was wrong. Pinning the true behaviour is what protects it; pinning an imagined one would
    have driven a change that made the wrapper stricter for no security gain.
    """
    run, argv_file, _ = wrapper
    r = run("/whatever/claude --print --model m id --effort max")
    assert r.returncode == 0, r.stderr
    argv = argv_file.read_text().splitlines()
    assert argv[argv.index("--model") + 1] == "m"
    assert "id" not in argv, f"an extra token reached claude: {argv}"


def test_the_token_is_exported_when_present_and_not_when_absent(wrapper):
    """The credential stays on the host and reaches claude by environment, never by argv.

    The absent case matters as much: an EMPTY `CLAUDE_CODE_OAUTH_TOKEN` sits 5th in claude's
    documented credential precedence and would shadow a working keychain login, so the wrapper
    must export nothing rather than export empty.
    """
    run, argv_file, token_file = wrapper

    r = run(f"/whatever/claude {_SLUICE_ARGV}")
    assert r.returncode == 0
    assert "TOKEN=<unset>" in argv_file.read_text(), "exported a token with no token file present"
    assert "sk-" not in argv_file.read_text()

    argv_file.unlink()
    token_file.write_text("example-token-value\n")
    token_file.chmod(0o600)
    r = run(f"/whatever/claude {_SLUICE_ARGV}")
    assert r.returncode == 0
    recorded = argv_file.read_text()
    assert "TOKEN=example-token-value" in recorded, "token file present but not exported"
    assert "example-token-value" not in recorded.split("TOKEN=")[0], "token leaked into argv"

    # An empty token file must behave like no token at all.
    argv_file.unlink()
    token_file.write_text("")
    r = run(f"/whatever/claude {_SLUICE_ARGV}")
    assert r.returncode == 0
    assert "TOKEN=<unset>" in argv_file.read_text(), "empty token file exported an empty token"


def test_the_wrappers_deny_list_covers_everything_sluice_denies():
    """The two argv builders are separate sources of truth; this is what binds them.

    `ClaudeMaxBackend` builds one argv and the wrapper builds another, and nothing else notices if
    they drift. The wrapper may be STRICTER -- it is, deliberately -- but a tool sluice denies and
    the wrapper does not would mean the ssh path is more permissive than the local one, which is
    exactly backwards for the path that crosses a trust boundary.
    """
    import re
    backends = (ROOT / "sluice" / "core" / "backends.py").read_text()
    # `[\w*-]`, not `[A-Za-z]`: a tool name with an underscore, digit or wildcard (`mcp__*` is
    # already such a name on the wrapper side) would otherwise TRUNCATE the parse at the first
    # one, silently shrinking the set this guard compares.
    # A tool name may contain `_`, a digit or `*` (`mcp__*`), but must NOT start with `-` --
    # otherwise the run continues straight past the tool list into the NEXT flag, and
    # `--permission-mode`/`bypassPermissions` join the "tools sluice denies" set. Measured: they
    # did, and the comparison against the wrapper then failed for a wholly invented reason.
    m = re.search(r'"--disallowedTools",\s*((?:"[\w*][\w*-]*",\s*)+)', backends)
    assert m, "could not find sluice's own deny-list; this guard would be vacuous"
    sluice_denies = set(re.findall(r'"([\w*][\w*-]*)"', m.group(1)))
    assert {"Write", "Edit", "NotebookEdit", "Bash"} <= sluice_denies, (
        f"sluice's deny-list no longer contains the four tools this guard was written against: "
        f"{sorted(sluice_denies)}")

    # The EXEC line, anchored on leading whitespace so a comment mentioning the flag cannot match.
    w = re.search(r"^\s+--disallowedTools ([^\\\n]+)", WRAPPER.read_text(), re.M)
    assert w, "could not find the wrapper's deny-list"
    wrapper_denies = {t.strip('"') for t in w.group(1).split()}

    missing = sluice_denies - wrapper_denies
    assert not missing, (
        f"the wrapper does not deny {sorted(missing)}, which sluice denies on the local path. "
        f"The ssh path crosses a trust boundary and must not be the more permissive one.")


def test_the_install_guides_deny_list_matches_the_wrapper():
    """`docs/INSTALL.md` enumerates the deny-list in prose; this is what keeps that true.

    The list is worth spelling out for a reader deciding whether to hand a container an ssh key,
    so the fix for a prose enumeration is not always to delete it -- but an unasserted one drifts
    silently, and this file already carried a stale one: a comment in the wrapper named four tools
    while its own exec line denied seven, and nothing went red for as long as that was so. The
    wrapper's exec line is the single source; the doc is compared against it.

    `mcp__*` is matched through the phrase rather than the token: the doc says "every MCP tool",
    which is what a human needs, and spelling the glob into the sentence would be worse prose for
    no added guarantee.
    """
    import re
    w = re.search(r"^\s+--disallowedTools ([^\\\n]+)", WRAPPER.read_text(), re.M)
    assert w, "could not find the wrapper's deny-list"
    wrapper_denies = {t.strip('"') for t in w.group(1).split()}
    assert wrapper_denies, "empty deny-list parsed; this guard would be vacuous"

    doc = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    m = re.search(r"including a\s+deny-list \(([^)]*)\)", doc, re.S)
    assert m, ("could not find the deny-list sentence in docs/INSTALL.md -- if it was reworded, "
               "re-anchor this guard rather than deleting it")
    phrase = m.group(1)
    documented = set(re.findall(r"`([^`]+)`", phrase))
    if "every MCP tool" in phrase:
        documented.add("mcp__*")

    assert documented == wrapper_denies, (
        f"docs/INSTALL.md and the wrapper disagree about what is denied.\n"
        f"  only in the doc:     {sorted(documented - wrapper_denies)}\n"
        f"  only in the wrapper: {sorted(wrapper_denies - documented)}")


def test_a_glob_cannot_become_a_filename_from_the_hosts_home(wrapper, tmp_path, monkeypatch):
    """`set -f`, pinned against the exploit that existed before it.

    Unquoted expansion GLOBS as well as splits, and the wrapper runs with the host account's home
    as its cwd. Measured on the version without `set -f`, from a directory holding two files:

        --model *      -> model became a real filename, charset-clean, and was exec'd
        --effort */*   -> "refusing --effort: sub/private-notes"

    The second is the serious one. `core/backends.py` puts `proc.stderr` into the BackendError it
    raises, so the refusal message travels back to whoever holds the key -- turning a key that is
    supposed to be worth one `claude --print` into a way to enumerate the host's home directory.
    shellcheck exits 0 either way; only this test and the flag stand between them.

    Run with cwd set INSIDE a populated directory, because a glob that matches nothing expands to
    itself and the bug would look absent.
    """
    run, argv_file, _ = wrapper
    home = tmp_path / "victim-home" / "sub"
    home.mkdir(parents=True)
    (home.parent / "Alpha-secret-file").touch()
    (home / "private-notes").touch()
    monkeypatch.chdir(home.parent)

    r = run("/whatever/claude --print --model * --effort max")
    assert r.returncode != 0, "a glob was accepted as the model"
    assert "refusing --model: *" in r.stderr, r.stderr
    assert "Alpha-secret-file" not in r.stderr, "a filename leaked through the refusal message"
    assert not argv_file.exists()

    r = run("/whatever/claude --print --model ok --effort */*")
    assert r.returncode != 0
    assert "private-notes" not in r.stderr, "a path leaked through the refusal message"
    assert "sub/" not in r.stderr


def test_the_wrapper_handles_every_flag_sluice_sends():
    """The two argv builders are separate sources of truth, and this binds their FLAG SETS.

    The wrapper discards every token it does not recognise -- correct, and the reason a hostile
    caller cannot smuggle options through. The cost is that a flag sluice ADDS later is silently
    dropped on the ssh path and nowhere else: the local path would carry it, the container path
    would not, and the difference would surface as a behaviour discrepancy nobody could see.

    Each of sluice's flags must be either COPIED (the wrapper reads its value) or CONSTRUCTED
    (the wrapper writes its own). A new flag in `core/backends.py` fails here until someone
    decides which it is.
    """
    import re
    backends = (ROOT / "sluice" / "core" / "backends.py").read_text()
    base = re.search(r'claude_path,\s*"--print",(.*?)\]', backends, re.S)
    assert base, "could not find ClaudeMaxBackend's argv; this guard would be vacuous"
    # `[A-Za-z-]`, not `[a-z-]`. claude's flag namespace is camelCase (`--disallowedTools`,
    # `--allowedTools`, `--outputFormat`), so a lowercase-only class silently dropped the ONE
    # flag this guard exists for -- and the floor below was `>= 4`, the blind count, so it
    # certified its own blindness. Asserted BY NAME now: a count cannot notice which flag is
    # missing, which is the whole failure it just had.
    sluice_flags = {"--print"} | set(re.findall(r'"(--[A-Za-z-]+)"', base.group(1)))
    assert {"--print", "--model", "--effort", "--disallowedTools",
            "--permission-mode"} <= sluice_flags, (
        f"sluice's argv no longer contains the flags this guard was written against: "
        f"{sorted(sluice_flags)}. If a flag was renamed or removed, update this set "
        f"deliberately -- do not widen it to whatever happens to parse.")

    wrapper = WRAPPER.read_text()
    copied = set(re.findall(r"^\s+(--[A-Za-z-]+)\)", wrapper, re.M))       # the `case` arms
    # The whole exec statement, continuations included. Anchored on `exec` rather than on line
    # starts: `--print` sits on the same line as `exec "$CLAUDE"`, so a line-start match missed
    # it and reported the wrapper as dropping a flag it plainly constructs.
    exec_stmt = re.search(r'exec "\$CLAUDE"(.*?)(?:\n\n|\Z)', wrapper, re.S)
    assert exec_stmt, "could not find the wrapper's exec; this guard would be vacuous"
    constructed = set(re.findall(r"(--[A-Za-z-]+)", exec_stmt.group(1)))
    handled = copied | constructed
    assert "--print" in constructed, "sanity: the exec must construct --print"

    missing = sluice_flags - handled
    assert not missing, (
        f"sluice sends {sorted(missing)} and the wrapper neither copies nor constructs it, so "
        f"the ssh path silently drops it while the local path carries it. Add it to the case "
        f"arms (to forward its value) or to the exec (to pin it host-side).")


def test_the_wrapper_needs_no_bashisms():
    """It is invoked by sshd as a forced command, whose shell is not guaranteed to be bash.

    macOS ships bash 3.2 and this repo has already been bitten by a bash-4 substitution in a
    release script that `bash -n` parsed happily and shellcheck did not flag.
    """
    assert shutil.which("sh"), "no /bin/sh to check against"
    r = subprocess.run(["/bin/sh", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode == 0, f"not valid POSIX sh: {r.stderr}"
