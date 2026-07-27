"""No generated artefact or personal-data file may EVER be tracked in this repo.

This is a CI gate, not a rule, because the rule already existed and did not work.

`.memsearch/` (absolute home paths, session UUIDs) reached this public repository THREE
times. The `.gitignore` rule was present and correct every time, and irrelevant every time:
**gitignore only applies to files git is not ALREADY TRACKING.** The file became tracked via
a `git stash -u` taken on a branch whose .gitignore predated the rule; popping that stash
restored it *already staged*, and every `git add -A` then carried it past a rule that could
no longer see it.

So the defence is an assertion that fails the build, on every PR, whatever route a file took
into the index.

And it FAILS CLOSED. A guard that silently passes when its own subprocess errors is worse
than no guard, because it manufactures confidence. CodeRabbit caught precisely that in the
first version of this file: a `git ls-files` failure produced empty output, the comprehension
found nothing, and CI went green having checked nothing at all -- the same fails-open class
this repo spends its life engineering out, reproduced inside the test written to prevent it.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent

# Generated from .rulesync/ by `rulesync generate`, or runtime artefacts of local tooling.
# Kept in step with .gitignore: a gate that guards fewer paths than the ignore file has a hole.
FORBIDDEN_EXACT = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".mcp.json",
    ".cursorrules",
    ".github/copilot-instructions.md",
)
FORBIDDEN_PREFIXES = (".claude/", ".cursor/", "node_modules/")
# .memsearch and .npmrc may appear at ANY depth. Both .gitignore rules are deliberately
# unanchored -- for a directory that has already leaked personal data three times, and for a
# file that can carry a registry auth token, catching them anywhere is the safer default.
FORBIDDEN_COMPONENTS = (".memsearch", ".npmrc")

# The first path component after the home prefix, whatever it is called. The first version of
# this gate used `/Users/[a-z]`, which missed /Users/Alice and /home/2runner -- a personal path
# walking straight through.
_NAME = r"[^/\s'\"`,)\]]+"
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/" + _NAME)


def _git(*args: str, allow: tuple = (0,)) -> str:
    """Run git and FAIL CLOSED. An unexpected exit code raises, rather than yielding the empty
    output that would let this gate pass without having checked anything."""
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30)
    if proc.returncode not in allow:
        raise AssertionError(
            f"git {' '.join(args)} failed ({proc.returncode}); the leak gate could not run and "
            f"must NOT pass silently: {proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def _is_forbidden(path: str) -> bool:
    if path in FORBIDDEN_EXACT:
        return True
    if any(path.startswith(p) for p in FORBIDDEN_PREFIXES):
        return True
    return any(part in FORBIDDEN_COMPONENTS for part in path.split("/"))


def test_no_generated_or_personal_artefact_is_tracked():
    tracked = _git("ls-files").splitlines()
    assert tracked, "git ls-files returned nothing: the gate would be passing without checking"

    leaked = sorted(f for f in tracked if _is_forbidden(f))
    assert not leaked, (
        f"{len(leaked)} generated/personal file(s) are TRACKED in a public repo: {leaked}\n"
        f"gitignore does not help once a file is tracked. Remove them from the index:\n"
        f"    git rm -r --cached {' '.join(leaked)}"
    )


@pytest.mark.parametrize("prefix", ["/Users/", "/home/"])
def test_no_absolute_home_path_is_tracked_in_source_or_config(prefix):
    """An absolute home path names a person and their machine. This repo promises neither.

    A NAME is required after the prefix, not a bare "/Users/": the neutrality-reviewer agent
    definition legitimately lists `/Users/` and `/home/` among the patterns it hunts for, and a
    detector must be allowed to name what it detects. `/Users/` is a pattern; `/Users/someone`
    is a leak.
    """
    # git grep exits 1 for "no matches" -- the SUCCESS case here. Any other code means it failed
    # to run, and must not be read as "clean".
    out = _git("grep", "-l", "-I", "-E", re.escape(prefix) + _NAME, "--",
               "sluice", "tests", "docs", "*.md", "*.yaml", "*.yml", "*.toml", ".gitignore",
               allow=(0, 1))
    hits = [f for f in out.splitlines()
            # this file necessarily contains the strings it is searching for
            if not f.endswith("test_no_leaked_files.py")]
    assert not hits, f"absolute home path under {prefix!r} in tracked files: {hits}"


def test_the_gate_catches_real_shapes_and_spares_bare_prefixes():
    """The gate's own regression test. It has already been wrong once: lowercase-ASCII only."""
    for leak in ("/Users/iandominey/.claude/x.jsonl", "/Users/Alice/dev", "/home/2runner/work"):
        assert _HOME_PATH_RE.search(leak), f"gate would MISS a real leak: {leak}"
    for detector in ("`/Users/`", "`/home/`, `.local`, `ssh`"):
        assert not _HOME_PATH_RE.search(detector), f"gate false-positives on a detector: {detector}"


def test_the_gate_covers_every_path_gitignore_covers():
    """A gate guarding fewer paths than .gitignore is a gate with a hole in it.

    Enumerates all THREE tuples. The previous version iterated two and hand-asserted
    `.memsearch`, so a later addition to FORBIDDEN_COMPONENTS -- `.npmrc`, which stops a
    registry credential becoming committable -- would have sat outside the check entirely.
    Enumerate, never hand-list.

    Compares against parsed RULE lines, not a raw substring-of-the-whole-file check: a plain
    `path in ignored` matched the explanatory comment above the `.npmrc` rule -- which names
    `.npmrc` in its own prose -- and stayed green with the rule line itself deleted. That
    checked the comment describing the rule, not the rule. Comparing with `.strip("/")` on
    both sides lets an anchored rule (`/node_modules/`) match an unanchored gate entry
    (`node_modules/`) and vice versa.
    """
    ignored = (REPO / ".gitignore").read_text()
    rules = {
        line.strip().strip("/")
        for line in ignored.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    gated = FORBIDDEN_EXACT + FORBIDDEN_PREFIXES + FORBIDDEN_COMPONENTS
    assert gated, "no paths gated: this test would pass without checking anything"
    for path in gated:
        assert path.strip("/") in rules, f"{path} is gated but NOT gitignored -- they must agree"


def test_the_gate_fails_closed_when_git_fails():
    """The bug CodeRabbit found in v1: a failing subprocess produced empty output, so the gate
    passed having checked nothing."""
    with pytest.raises(AssertionError, match="must NOT pass silently"):
        _git("this-is-not-a-git-command")
