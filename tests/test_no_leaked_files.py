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
import pathlib
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent

# Generated from .rulesync/ by `rulesync generate`, or runtime artefacts of local tooling.
# Kept in step with .gitignore: a gate that guards fewer paths than the ignore file has a hole.
#
# The generated-output entries are NOT a hand-copied wish list. `test_the_gate_covers_every_
# generated_output_gitignore_covers` parses .gitignore's generated-output block and requires
# every rule in it to be gated here, and the companion test requires every entry here to be a
# real ignore rule -- so the two files are pinned to each other in both directions.
FORBIDDEN_EXACT = (
    # THREE of these are reachable under the CURRENT target set, not two. package.json's
    # rulesync script targets `claudecode,agentsmd`, which writes CLAUDE.md and AGENTS.md
    # today; `.mcp.json` joins them the moment `.rulesync/mcp.json` exists -- measured, the
    # narrowed set emits it. Its absence is one source file away from being undone, which is a
    # different thing from a target that can no longer emit at all, and `.gitignore` keeps it in
    # the GENERATED block for that reason. Classifying it as legacy here would put the two files
    # out of step and invite pruning a live guard.
    "CLAUDE.md",
    "AGENTS.md",
    ".mcp.json",
    # Everything below is a LEGACY OUTPUT of the earlier `-t '*'`, which emitted a file for
    # every tool rulesync knew about.
    "GEMINI.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    # ...continued. LEGACY DOES NOT MEAN DEAD, and the inference that it does has already cost
    # this branch a near-miss. "Those targets no longer generate, so these entries can go" is
    # true in its premise and false in its conclusion: every machine that ever ran `-t '*'`
    # still has these files ON DISK. Narrowing `-t` stops them being REGENERATED; it cannot
    # delete what already exists, and an ignore rule plus this gate are the only things keeping
    # them out of the index. Acting on that inference -- deleting .gitignore's parallel block
    # AND trimming these tuples in one change -- let `git add -A` commit 232 files and 46,553
    # lines of build output here with the suite still GREEN. Green precisely BECAUSE the gate
    # that would have caught it was trimmed in the same breath, which is why no future
    # narrowing of `-t` is a reason to prune this list. .gitignore's LEGACY OUTPUTS block
    # carries the same warning; this is its mirror and the two are pruned together or not at
    # all.
    ".goosehints",
    ".hermes.md",
    ".roomodes",
    ".rules",
    "QWEN.md",
    "REASONIX.md",
    "replit.md",
)
FORBIDDEN_PREFIXES = (

    ".claude/",
    ".cursor/",
    "node_modules/",
    ".agents/",
    ".aiassistant/",
    ".augment/",
    ".cline/",
    ".codex/",
    ".deepagents/",
    ".devin/",
    ".factory/",
    # `.github/` itself is TRACKED (workflows/), so only rulesync's generated SUBdirs appear
    # here -- gating a bare `.github/` would fail the build on the CI workflow this test runs in.
    ".github/agents/",
    ".github/hooks/",
    ".github/skills/",
    ".goose/",
    ".grok/",
    ".hermes/",
    ".junie/",
    ".kilo/",
    ".kiro/",
    ".opencode/",
    ".pi/",
    ".qwen/",
    ".reasonix/",
    ".roo/",
    ".rovodev/",
    ".takt/",
    ".vibe/",
    ".warp/",
)
# .memsearch and .npmrc may appear at ANY depth. Both .gitignore rules are deliberately
# unanchored -- for a directory that has already leaked personal data three times, and for a
# file that can carry a registry auth token, catching them anywhere is the safer default.
# `vault` is a COMPONENT, not a root prefix: DEFAULT_VAULT is cwd-relative, so `sluice init`
# from a subdirectory creates one at any depth. Root-anchoring it would miss exactly that.
FORBIDDEN_COMPONENTS = (".memsearch", ".npmrc", "vault")

# The first path component after the home prefix, whatever it is called. This has now been
# wrong TWICE. The first version used `/Users/[a-z]`, which missed /Users/Alice and
# /home/2runner. The second was a NEGATED class written with Python escapes --
# `[^/\s'"`,)\]]+` -- and handed to `git grep -E`, where a bracket expression treats the
# backslash as a LITERAL MEMBER, not an escape: the class terminated at the `\]`, leaving a
# pattern that required one or more literal `]` characters. No real path has those, so the
# gate matched NOTHING for its entire life. Verified by planting a home path in a tracked
# file and watching the gate pass.
#
# So: no escapes inside a bracket expression at all, and the two engines get SEPARATE
# constants, each asserted through the engine that runs it. A single string handed to both
# is what produced the incident above -- a `re`-based regression test compiled the escapes,
# where they work, and certified a pattern git had never matched anything with.
#
# There are exactly two, and both are live: `_GREP_NAME` DISCOVERS (git), and
# `_WIDE_HOME_PATH_RE` PARSES the matched line for the allow-list (Python). An earlier
# ASCII pair sat between them and is gone: once the parser had to see everything git sees,
# a narrower Python class could only create blind spots, and blind spots here are SKIPS.

# What GIT GREP searches for. A NEGATED class, so a component starting with a non-ASCII
# character (`/home/<accented>`) is caught; written with a POSIX `[:space:]` and NO
# BACKSLASHES, because a backslash inside a bracket expression is a literal MEMBER in ERE.
# Python's `re` cannot compile this string at all (`[:space:]` is not a POSIX class there,
# and the `)` inside breaks the expression), which is why it is asserted only through
# `git grep` below. That no-backslash rule is about THIS pattern and the engine it goes
# to; `_WIDE_HOME_PATH_RE` below carries `\t` and `\]` correctly, because Python honours
# them.
#
# `<` and `>` are excluded so an angle-bracket placeholder (`/home/<user>/...`, which the
# design docs use) is not a match at all; without that, widening turned every documented
# placeholder into a hit.
#
# `/` is NOT excluded. It was, and that silently cost discovery: `/home//realname/vault`
# -- the shape naive f-string concatenation produces -- matched nothing at all, so it
# never reached the parser to be judged. Measured over the whole repo, admitting `/` adds
# zero hits, so it closes that hole at no cost.
_GREP_NAME = r"""[^][:space:]'"`,)<>]"""

# The Python mirror, used by `_is_allowed_hit` to decide whether a matched line is a
# documented literal. It must see EVERY path git can find: whatever it cannot represent
# leaves the allow-listed literal as the only match on the line, and the real path beside
# it is skipped.
#
# The whitespace is spelled OUT, not `\s`. Python's `\s` also matches 0x1c-0x1f and
# 0x85, which POSIX `[:space:]` does not -- so git kept matching through those bytes and
# this pattern stopped, going BLIND to `/Users/<0x1f>name/vault` entirely while git
# reported it. Every remaining difference goes the other way (this matches where git does
# not), which fails closed -- and that is not asserted by a chosen table: the sweep in
# `test_the_python_parser_sees_every_line_git_can_find` pins it structurally.
_WIDE_HOME_PATH_RE = re.compile(r"""/(?:Users|home)/[^ \t\n\r\f\v'"`,)<>\]]+""")

# The exact home-rooted strings that legitimately appear in this repo, in full.
#
# Deliberately whole paths and not a set of placeholder USERNAMES, which is what this
# started as: keying on the first component alone accepted anything after it, so
# `/home/example/Documents/<a real vault name>` passed a neutrality gate silently. A
# placeholder root does not make the tail impersonal.
#
# Every entry is a deliberate act. `example` is the RFC-reserved placeholder the
# redaction fixtures use (labelled as such where they are defined); `someone` appears in
# a guard comment naming the shape it rejects; the elided form names nobody at all.
# ...and WHERE each may appear. One table, not two: a separate membership set was
# redundant with this one (a literal absent here has no allowed files, so it is rejected
# either way), and keeping both meant a test whose only job was to hold the redundancy
# in sync. Repo-wide allowance is wider than the reason for it -- a synthetic literal
# earns its exemption in the file that needs it, not everywhere.
_ALLOWED_HOME_PATH_FILES = {
    "/home/example/.local/bin/claude": ("tests/test_backends.py", "docs/"),
    "/Users/someone/vault": ("tests/test_sluice_neutral_defaults.py",),
    "/home/.../claude": ("sluice/core/backends.py",),
}

# Where .gitignore's generated-output list begins and ends. The boundary is drawn
# STRUCTURALLY -- by the markers below, not by a line count and not by hand -- because the
# gate's coverage claim is about `rulesync generate` outputs and nothing else.
#
# HOW TO EXTEND THIS CORRECTLY: a rule that lands BETWEEN these markers is a generated output
# and MUST be gated above; the sweep fails until it is. A rule outside them is out of scope --
# `/sluice.yaml` is a personal overlay, and `cv-out/`, `*.pdf`, `*.db`, `node_modules/`,
# `.npmrc`, `.memsearch/` are runtime artefacts of local tooling. Those are ignored for
# different reasons, they are not written by the generator, and pulling them in would make the
# gate's claim ("we cover what the generator writes") mean something else. Some of them ARE
# gated anyway, deliberately -- gating more than the block is allowed, gating less is not.
_GENERATED_BLOCK_START = "# AI-tool configs are GENERATED from .rulesync/"
_GENERATED_BLOCK_END = "!/.rulesync/**"
# The block is in TWO halves -- currently-written outputs, then LEGACY OUTPUTS -- and the legacy
# half holds 34 of the 41 rules. Pinning that the header sits INSIDE the markers is what makes
# the end marker's position load-bearing rather than incidental. See the moved-marker test.
_LEGACY_HEADER = "# LEGACY OUTPUTS"


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


def _parse_generated_block(lines: list[str]) -> list[str]:
    """Every .gitignore rule that covers a `rulesync generate` output. Pure, given the lines.

    Derived, never transcribed: 41 rules hand-copied into a test are 41 chances to copy 40.

    Takes LINES rather than reading the file, so the moved-marker case below can feed it a
    deliberately broken copy. A parser that can only be run against the one input it is supposed
    to accept cannot be shown to reject anything.
    """
    starts = [i for i, line in enumerate(lines) if line.startswith(_GENERATED_BLOCK_START)]
    ends = [i for i, line in enumerate(lines) if line.strip() == _GENERATED_BLOCK_END]
    assert len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0], (
        f"cannot delimit .gitignore's generated-output block ({len(starts)} start marker(s), "
        f"{len(ends)} end marker(s)). A reworded marker would silently reduce this sweep to "
        "covering nothing, so it fails instead."
    )
    legacy = [i for i, line in enumerate(lines) if line.startswith(_LEGACY_HEADER)]
    assert len(legacy) == 1 and starts[0] < legacy[0] < ends[0], (
        f"the {_LEGACY_HEADER!r} header is not inside .gitignore's generated-output block "
        f"({len(legacy)} header(s); block spans lines {starts[0] + 1}-{ends[0] + 1}). The legacy "
        "half holds 34 of the 41 rules, so an end marker that drifted ABOVE this header leaves "
        "the sweep parsing 4 rules and passing -- a count the `> 1` floor below cannot tell from "
        "a healthy one. Assert the structure, not the size."
    )
    return [
        line.strip()
        for line in lines[starts[0] : ends[0]]
        # `!` is the re-include that closes the block, `#` the prose inside it.
        if line.strip() and not line.strip().startswith(("#", "!"))
    ]


def _generated_output_rules() -> list[str]:
    return _parse_generated_block((REPO / ".gitignore").read_text().splitlines())


def _probe_path(rule: str) -> str:
    """A repo-relative path the rule ignores. `_is_forbidden` classifies PATHS -- what
    `git ls-files` prints -- so a DIRECTORY rule has to be probed with something inside it."""
    bare = rule.lstrip("/")
    return bare + "generated.md" if bare.endswith("/") else bare


# The ONE path deliberately carved out of the `.claude/` prefix gate. `.claude/settings.json`
# carries Claude Code's own `enabledPlugins` key (written by `/plugin marketplace add`, never
# by rulesync) in the same shared file rulesync's hooks feature writes -- tracking it is the
# only way a plugin enable reaches every worktree and contributor. .gitignore's matching
# `!/.claude/settings.json` re-include and `scripts/reset_tracked_hooks.py`'s docstring have
# the full chain, including what it costs the two rulesync CI guards and how that is repaid.
# Checked BEFORE the prefix scan, never by editing FORBIDDEN_PREFIXES itself: `.claude/`
# there still has to keep matching everything else under it -- agents/, skills/, worktrees/,
# scheduled_tasks.lock -- by simple prefix, and a `.claude/*`-shaped entry there would break
# that for all of them at once (see test_prefix_rules_are_root_anchored_and_component_rules_
# are_not for the general form).
_TRACKED_EXCEPTIONS = frozenset({".claude/settings.json"})


def _is_forbidden(path: str) -> bool:
    if path in FORBIDDEN_EXACT:
        return True
    if path in _TRACKED_EXCEPTIONS:
        return False
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


# EMPTY = every tracked file. This was an include-list, and each round bolted another
# entry on -- `*.json`, `scripts`, `run_tests.sh`, `.github` -- which is the unbounded
# shape: "what did we forget?" has no answer, and four rounds each found one more thing.
# Sweeping everything makes it bounded, and it is a no-op today: measured, the same grep
# with no pathspec returns exactly the allow-listed literals in their allowed files.
#
# Narrowing this is what `test_the_gate_leaves_no_tracked_file_unsearched` forbids --
# without it, deleting entries from the old include-list was invisible to the suite.
_GATE_PATHSPEC: tuple = ()


def test_the_gate_actually_uses_the_declared_pathspec():
    """...and the gate must READ that constant, not carry its own.

    The completeness check below constrains `_GATE_PATHSPEC` only: hardcoding a pathspec
    at the grep call site while leaving the constant empty passed every test. Reading the
    source is crude, but it is the connection between the two that was missing.
    """
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    call = src[src.index("out = _git(\"grep\""):]
    call = call[:call.index("allow=(0, 1))")]
    assert "_GATE_PATHSPEC" in call, (
        "the gate no longer derives its pathspec from _GATE_PATHSPEC, so the "
        "completeness guard below constrains nothing")
    assert '"--", "' not in call, f"a literal pathspec is hardcoded at the call site: {call}"


def test_the_gate_leaves_no_tracked_file_unsearched():
    # No conditional: `ls-files -- ` with no pathspec lists everything, so the empty
    # case compares the real thing against the real thing instead of a constant against
    # itself. `splitlines`, not `split`, or a filename with a space in it becomes two.
    everything = set(_git("ls-files").splitlines())
    searched = set(_git("ls-files", "--", *_GATE_PATHSPEC).splitlines())
    missed = everything - searched
    assert not missed, (
        "these tracked files are outside the leak gate's pathspec, so a home path in "
        f"one of them ships unnoticed: {sorted(missed)}")


def _is_allowed_hit(line):
    """True if a `git grep -n` hit line is a documented synthetic literal IN ITS OWN FILE.

    Extracted so it can be given synthetic input: while this was inline, deleting the
    file-scoping clause left the whole suite green, because nothing could exercise the
    predicate without planting files in the real tree.

    `found` must be non-empty. `all([])` is True, so a line git matched and this cannot
    parse must be REPORTED, not skipped -- the patterns are kept in step for exactly this
    reason, and the check stands whether or not they ever drift again.
    """
    path_in_repo = line.split(":", 1)[0]
    # Decided on the matches THEMSELVES, never on a count. Counting only catches a
    # SEPARATE extra match: a match that EXTENDS PAST an allow-listed literal keeps the
    # count at 1, so `<allowed literal>:<real path>` -- or `[`, `@`, `{` -- read as a
    # single permitted match and the real path rode along. Measured: the space-separated
    # shape was caught and the other three were not.
    #
    # Comparing the whole run git saw means a concatenation is simply not the allow-listed
    # literal, and is reported. `rstrip` for trailing sentence punctuation, which the
    # class admits; a leak can only reduce to an allowed literal if its entire tail is
    # `[:.,;]`, which no path has.
    found = [m.rstrip(":.,;") for m in _WIDE_HOME_PATH_RE.findall(line)]
    return bool(found) and all(
        any(path_in_repo.startswith(w)
            for w in _ALLOWED_HOME_PATH_FILES.get(m, ()))
        for m in found)


@pytest.mark.parametrize("line,allowed,why", [
    ("tests/test_backends.py:1: /home/example/.local/bin/claude", True,
     "a documented literal in its own file"),
    ("sluice/core/paths.py:1: /home/example/.local/bin/claude", False,
     "the same literal somewhere it was never exempted"),
    ("tests/test_backends.py:1: /home/realperson/x", False, "not on the allow-list"),
    ("tests/test_backends.py:1: /home/example/.local/bin/claude and /home/other/x", False,
     "one allowed literal does not cover the rest of the line"),
    # BOTH literals are on the allow-list, with DIFFERENT allowed files. Only the first
    # belongs here, so the line must still be reported. This row is the only one that
    # catches SCOPE CONFLATION -- a mutant that keeps `all(...)` but looks up `found[0]`'s
    # allowed files for every match. (An earlier comment credited it with catching a
    # plain `found[:1]` mutant instead; measured, the row above catches that one too,
    # because `found[:1]` never evaluates the second literal at all.)
    ("tests/test_backends.py:1: /home/example/.local/bin/claude then /Users/someone/vault",
     False, "each match is scoped, not just the first"),
    ("tests/test_backends.py:1: nothing here", False,
     "no parseable path -- an unparseable git hit must report, not skip"),
    # The fail-open rows. Every row above is ASCII, so the guard against a real path
    # riding along beside an allowed literal could be DELETED with the suite green --
    # measured, twice, by two reviewers. These are the four separators that reach it;
    # only the first was caught when the guard compared match COUNTS.
    ("tests/test_backends.py:1: /home/example/.local/bin/claude and /home/\u00e9xample/x",
     False, "a real path space-separated from an allowed literal"),
    ("tests/test_backends.py:1: /home/example/.local/bin/claude:/home/\u00e9xample/x",
     False, "...colon-separated, which a count comparison could not see"),
    ("tests/test_backends.py:1: /home/example/.local/bin/claude[/home/\u00e9xample/x",
     False, "...bracket-separated"),
    ("tests/test_backends.py:1: /home/example/.local/bin/claude@/home/\u00e9xample/x",
     False, "...at-sign-separated"),
    ("tests/test_backends.py:1: /home/example/.local/bin/claude.", True,
     "trailing sentence punctuation is not part of the path"),
    # A control byte INSIDE the component. Python's `\s` treats 0x1f as whitespace and
    # POSIX `[:space:]` does not, so this pattern saw nothing here while git saw the whole
    # path -- and a blind spot is a skip.
    ("tests/test_backends.py:1: /home/example/.local/bin/claude and "
     "/Users/\x1fleakedperson/vault", False,
     "a path whose component carries a byte Python calls whitespace and POSIX does not"),
])
def test_the_allowance_is_scoped_to_the_file_that_needs_it(line, allowed, why):
    assert _is_allowed_hit(line) is allowed, why


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
    # `*.json` and `scripts` are here because this branch newly tracks package.json, a
    # 3300-line npm-generated package-lock.json, and scripts/guard_rulesync_drift.py -- all
    # three sat outside the old pathspec, and a generated lockfile is precisely what nobody
    # reads before committing.
    out = _git("grep", "-n", "-I", "-E", re.escape(prefix) + _GREP_NAME,
               *(("--",) + _GATE_PATHSPEC if _GATE_PATHSPEC else ()), allow=(0, 1))
    hits = []
    for line in out.splitlines():
        # this file necessarily contains the strings it is searching for
        if line.startswith("tests/test_no_leaked_files.py:"):
            continue
        # `found` must be non-empty: `all([])` is True, so a line git grep matched but
        # Python's `re` did not would otherwise be dropped in silence -- a gate failing
        # open, which is the bug this whole file just spent a round fixing.
        if _is_allowed_hit(line):
            continue
        hits.append(line)
    assert not hits, f"absolute home path under {prefix!r} in tracked files: {hits}"


def test_the_gate_catches_real_shapes_and_spares_bare_prefixes(tmp_path):
    """The gate's own regression test, run through the engine the GATE uses.

    It has been wrong twice, and the second time is why the discovery half is asserted
    through `git grep` rather than Python's `re`. One pattern was compiled by `re` here AND
    handed to `git grep -E` there, and the two disagree about backslashes inside a bracket
    expression -- so a `re`-based check certified a pattern the gate never actually ran,
    and the gate matched nothing for its entire life. Asserting through the engine that
    RUNS the pattern is the only form that can catch that class of divergence.

    `--no-index` searches a plain directory, so this needs no repo and cannot be confused by
    the real one (which necessarily contains the strings being searched for).
    """
    # One file per shape: a single mixed file passes on ONE match, so a shape that
    # stopped being caught would hide behind its neighbours.
    shapes = {
        "lowercase": "/Users/devuser/.claude/x.jsonl",
        "capitalised": "/Users/ExampleUser/dev",
        "digit-initial": "/home/2runner/work",
        "non-ascii-initial": "/home/\u00c9xample/vault",
        "non-ascii-scandinavian": "/Users/\u00d8xample/y",
        # `/` used to be excluded from the discovery class, so this shape -- what naive
        # f-string concatenation produces -- was never discovered at all.
        "doubled-slash": "/home//example/vault",
    }
    for label, value in shapes.items():
        (tmp_path / f"{label}.txt").write_text(value + "\n", encoding="utf-8")
    (tmp_path / "detectors.txt").write_text(
        "`/Users/`\n`/home/`, `.local`, `ssh`\n", encoding="utf-8")

    def _grep(pattern, name):
        r = subprocess.run(["git", "grep", "--no-index", "-l", "-I", "-E", pattern,
                            "--", name],
                           cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode in (0, 1), f"git grep failed to run: {r.stderr}"
        return r.returncode == 0

    for label in shapes:
        assert _grep(r"/(Users|home)/" + _GREP_NAME, f"{label}.txt"), \
            f"the gate's discovery pattern MISSES a {label} leak under git grep -E"
    assert not _grep(r"/(Users|home)/" + _GREP_NAME, "detectors.txt"), \
        "the gate false-positives on a bare-prefix detector under git grep -E"

    # The Python half, asserted through the pattern the gate ACTUALLY runs. It must see
    # every shape git can find -- including the non-ASCII ones -- because `_is_allowed_hit`
    # decides on these matches: a shape this misses leaves the allow-listed literal as the
    # only match on the line, and the real path beside it is skipped.
    for label, value in shapes.items():
        assert _WIDE_HOME_PATH_RE.search(value), \
            f"the gate's allow-list parser MISSES a {label} leak: {value}"
    for detector in ("`/Users/`", "`/home/`, `.local`, `ssh`"):
        assert not _WIDE_HOME_PATH_RE.search(detector), \
            f"gate false-positives on a detector: {detector}"


def _sweep_verdict(git_hits, python_hits, sep_lines):
    """(narrowed, fail_open) for one sweep. Both are sets of line numbers.

    Extracted for the same reason `_is_allowed_hit` was: a guard whose whole job is to
    catch a FUTURE regression cannot be witnessed by a suite in which that regression does
    not occur. Deleting these two comparisons in place left everything green -- the healthy
    tree simply never produces a non-empty set -- so the logic lives here where synthetic
    inputs can drive it, and the rows below are what actually pin it.

    `narrowed` is the scope half, and it is a SET rather than a count. A threshold (`>
    half the lines`) carried roughly 2x slack, so a narrowing confined to one block --
    discovery quietly dropping everything above 0x200, say -- stayed comfortably above it
    and read as healthy. Everything git legitimately declines to match is a codepoint
    POSIX `[:space:]` calls whitespace and Python's spelled-out class does not; anything
    else is a regression, whatever the count says.
    """
    return (python_hits - git_hits - sep_lines, git_hits - python_hits)


@pytest.mark.parametrize("git,py,seps,narrowed,fail_open,why", [
    ({1, 2, 3}, {1, 2, 3}, set(), set(), set(), "healthy: both engines agree"),
    ({1, 2}, {1, 2, 3}, {3}, set(), set(), "a known whitespace divergence is not a regression"),
    ({1, 2}, {1, 2, 3}, set(), {3}, set(), "the same gap with no divergence to explain it IS"),
    ({1, 2, 3}, {1, 2}, set(), set(), {3}, "git sees a line the parser cannot -- fail OPEN"),
    (set(), {1, 2, 3}, set(), {1, 2, 3}, set(), "discovery found nothing at all"),
    ({1, 2, 3}, set(), set(), set(), {1, 2, 3}, "the parser matched nothing at all"),
])
def test_the_sweep_verdict_separates_narrowing_from_failing_open(
        git, py, seps, narrowed, fail_open, why):
    assert _sweep_verdict(git, py, seps) == (narrowed, fail_open), why


def test_the_python_parser_sees_every_line_git_can_find(tmp_path):
    """The gate's fail-closed property, pinned STRUCTURALLY rather than by a chosen table.

    Discovery (git) and the allow-list parser (Python) are separate patterns in separate
    engines. If git can match a line the parser cannot, the allow-listed literal on that
    line becomes the only match `_is_allowed_hit` sees, and a real path beside it is
    silently skipped -- the documented incident. The rows above are shapes SOMEONE CHOSE,
    and a table whose cases you chose certifies nothing; this sweeps the space instead.

    Asserts on the SCOPE first: a sweep that discovers nothing satisfies a subset check
    vacuously, which for a negative property is exactly how a gate dies quietly.
    """
    # 0x0a/0x0d would end the line and 0x00 would make git call the file binary, so the
    # sweep cannot speak for those three; everything else through 0x2FF, plus every
    # Unicode separator, which is where the two engines' whitespace notions diverge.
    seps = {0xa0, 0x1680, *range(0x2000, 0x200b), 0x2028, 0x2029, 0x202f, 0x205f, 0x3000}
    # A set union, not a concatenation: 0xa0 falls inside the range as well and was
    # planted twice, which quietly weighted one codepoint double in every count below.
    codepoints = sorted({c for c in range(0x01, 0x300) if c not in (0x0a, 0x0d)} | seps)
    lines = [f"/Users/{chr(c)}x{i}/y" for i, c in enumerate(codepoints)]
    (tmp_path / "sweep.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = subprocess.run(["git", "grep", "--no-index", "-n", "-I", "-E",
                        r"/(Users|home)/" + _GREP_NAME, "--", "sweep.txt"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode in (0, 1), f"git grep failed to run: {r.stderr}"
    git_hits = {int(x.split(":")[1]) for x in r.stdout.splitlines() if x.count(":") >= 2}
    python_hits = {i + 1 for i, line in enumerate(lines) if _WIDE_HOME_PATH_RE.search(line)}

    sep_lines = {i + 1 for i, c in enumerate(codepoints) if c in seps}
    narrowed, fail_open = _sweep_verdict(git_hits, python_hits, sep_lines)
    assert not narrowed, (
        "discovery stopped matching codepoints that are not the known whitespace "
        f"divergence: {[hex(codepoints[i - 1]) for i in sorted(narrowed)][:8]}")
    assert not fail_open, (
        "git discovers lines the allow-list parser cannot see, so a real path beside an "
        "allow-listed literal would be skipped: "
        f"{[hex(codepoints[i - 1]) for i in sorted(fail_open)][:8]}")


def test_every_gated_path_is_also_gitignored():
    """The gate and .gitignore must not disagree about what is a build artefact.

    NAMED FOR THE DIRECTION IT CHECKS. Its previous name -- "covers every path gitignore
    covers" -- and its docstring claimed the CONVERSE of the assertion below, and that converse
    was false: 41 generated-output rules existed and 8 were gated, so `_is_forbidden('QWEN.md')`
    returned False. A test whose name asserts more than its body is worse than a missing test,
    because the name is what a reader audits against. The claimed direction is now a real
    assertion, in `test_the_gate_covers_every_generated_output_gitignore_covers` below.

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
        bare = path.strip("/")
        # `.claude/` is gitignored as `/.claude/*`, not a bare `/.claude/`: a plain
        # trailing-slash directory rule excludes the directory ENTRY itself, which would make
        # `!/.claude/settings.json`'s re-include silently inert -- git will not look inside a
        # directory excluded that way. `dir/*` is therefore an equally valid witness that a
        # gated prefix is gitignored, alongside the plain `dir/` shape every other entry uses.
        assert bare in rules or f"{bare}/*" in rules, \
            f"{path} is gated but NOT gitignored -- they must agree"


def test_the_gate_covers_every_generated_output_gitignore_covers():
    """A gate guarding fewer generated outputs than .gitignore is a gate with a hole in it.

    This is the direction that went unasserted while a test NAMED for it sat green: gitignore
    listed 41 generated outputs and the gate knew 8. `.gitignore` only helps a file git is not
    already tracking, which is the whole reason this module exists -- so every path the
    generator writes needs a gate entry too, not just an ignore rule.

    Scoped to the generated-output block (see the markers above), so a future runtime artefact
    added elsewhere in .gitignore does not conscript this test into guarding it.
    """
    rules = _generated_output_rules()
    assert len(rules) > 1, (
        f"only {len(rules)} rule(s) parsed out of .gitignore's generated-output block: the "
        "parse broke badly enough to leave almost nothing, and this sweep would pass having "
        "checked ~nothing. A block that MOVED is caught structurally in _parse_generated_block, "
        "not here -- this floor cannot see it, which is the whole reason that check exists."
    )
    ungated = [rule for rule in rules if not _is_forbidden(_probe_path(rule))]
    assert not ungated, (
        f"{len(ungated)} generated output(s) are gitignored but NOT gated: {ungated}. "
        "Add each to FORBIDDEN_EXACT (a file) or FORBIDDEN_PREFIXES (a directory rule). "
        "An ignore rule does not stop an already-tracked file; only this gate does."
    )


def test_an_end_marker_moved_above_the_legacy_header_fails_loudly():
    """The catastrophic-parse-loss case the `> 1` floor structurally cannot see.

    WITNESSED by MOVING, not deleting. Relocating `!/.rulesync/**` to just above the LEGACY
    OUTPUTS header -- a plausible tidy-up now that the block is split in two, since the re-include
    reads like it belongs with the entries it re-includes -- drops the parse from 41 rules to
    exactly 4 (`/CLAUDE.md`, `/AGENTS.md`, `/.claude/`, `/.mcp.json`) while every assertion in
    this file stays GREEN. 4 is more than 1, so the floor passes; the 34 legacy rules simply stop
    being swept, and the gate silently stops guarding the entries that exist precisely because
    `.gitignore` alone cannot protect an already-tracked file.

    Mutates the REAL `.gitignore` in memory rather than a synthetic stand-in, so the case is the
    one a human would actually commit and not a shape chosen to fail.
    """
    lines = (REPO / ".gitignore").read_text().splitlines()
    end = next(i for i, line in enumerate(lines) if line.strip() == _GENERATED_BLOCK_END)
    lines.pop(end)
    legacy = next(i for i, line in enumerate(lines) if line.startswith(_LEGACY_HEADER))
    lines.insert(legacy, _GENERATED_BLOCK_END)

    # Non-vacuity: the mutation really does leave a parse that the old floor waves through.
    starts = next(i for i, line in enumerate(lines) if line.startswith(_GENERATED_BLOCK_START))
    ends = next(i for i, line in enumerate(lines) if line.strip() == _GENERATED_BLOCK_END)
    survivors = [
        line.strip()
        for line in lines[starts:ends]
        if line.strip() and not line.strip().startswith(("#", "!"))
    ]
    assert len(survivors) == 4 and len(survivors) > 1, survivors

    with pytest.raises(AssertionError, match=_LEGACY_HEADER):
        _parse_generated_block(lines)


def test_prefix_rules_are_root_anchored_and_component_rules_are_not():
    """The two tuples deliberately differ in reach, and nothing pinned the difference.

    FORBIDDEN_PREFIXES mirrors .gitignore's leading `/`: root-anchored, matching the generated
    outputs where they are actually written. A nested `docs/.claude/` is somebody's own
    directory, not a generator artefact, and gating it would be a false positive.

    FORBIDDEN_COMPONENTS mirrors the two deliberately UNANCHORED rules: `.memsearch/` has
    leaked personal data into this repo three times and `.npmrc` can carry a registry auth
    token, so both are caught at ANY depth.
    """
    # `.claude/agents/...`, not `.claude/settings.json`: the latter is the one deliberate,
    # separately-pinned exception (test_the_one_tracked_claude_path_is_narrowly_scoped below),
    # and using it here would make this test pass or fail depending on that exception rather
    # than on the root-anchoring property it exists to check.
    assert _is_forbidden(".claude/agents/reviewer.md")
    assert not _is_forbidden("docs/.claude/agents/reviewer.md")
    assert _is_forbidden("node_modules/rulesync/package.json")
    assert not _is_forbidden("tools/node_modules/rulesync/package.json")

    assert _is_forbidden(".memsearch/session.jsonl")
    assert _is_forbidden("docs/deep/.memsearch/session.jsonl")
    assert _is_forbidden(".npmrc")
    assert _is_forbidden("tools/sub/.npmrc")


def test_the_one_tracked_claude_path_is_narrowly_scoped():
    """POSITIVE CONTROL for `_TRACKED_EXCEPTIONS`.

    Mirrors `test_a_vault_written_into_the_checkout_is_refused`'s shape: the exception was
    added with nothing exercising it, which is how a carve-out that matches nothing --
    or, worse, everything -- survives unnoticed. `.claude/settings.json` itself must be
    ALLOWED (that is the whole point: `enabledPlugins` reaching every worktree), but the
    exception must not widen into a bare `.claude/` allowance -- every neighbouring path
    that gave `.claude/` its FORBIDDEN_PREFIXES entry in the first place must stay refused,
    including a path that merely starts with the same eight characters as the allowed file.
    """
    assert not _is_forbidden(".claude/settings.json")
    assert _is_forbidden(".claude/agents/reviewer.md")
    assert _is_forbidden(".claude/skills/review-pr/SKILL.md")
    assert _is_forbidden(".claude/worktrees/mcp-server-105/sluice/__init__.py")
    assert _is_forbidden(".claude/scheduled_tasks.lock")
    # Same directory, same leading bytes as the allowed file, different name -- the exception
    # is an exact match, not a prefix of its own.
    assert _is_forbidden(".claude/settings.local.json")
    assert _is_forbidden(".claude/settings.json.bak")


def test_the_tracked_settings_file_orders_enabled_plugins_before_hooks():
    """The fixed-point claim `scripts/reset_tracked_hooks.py`'s docstring makes -- and
    `.gitignore` and `.rulesync/rules/CLAUDE.md` repeat -- as prose, pinned as an executable
    check instead: rulesync's hooks writer APPENDS the key it writes rather than preserving
    the file's original position, so `enabledPlugins` must sit BEFORE `hooks` in the committed
    file, or repeated `strip hooks -> npm run rulesync` cycles never reach a byte-identical
    fixed point and `.github/workflows/ci.yml`'s `git status --porcelain` check reds on every
    run. Without this, a reorder (a hand edit, an editor's "sort keys on save") would surface
    only after a full npm/rulesync round-trip in CI, as a bare porcelain diff with nothing
    pointing back to the reason -- exactly the "prose is not a check" gap this repo's own
    CLAUDE.md warns about.
    """
    text = (REPO / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert text.index('"enabledPlugins"') < text.index('"hooks"'), (
        "the tracked .claude/settings.json now orders `hooks` before `enabledPlugins`. "
        "rulesync's hooks writer appends the key it writes rather than preserving original "
        "position, so this order is the only one where repeated regeneration reaches a "
        "stable fixed point -- restore enabledPlugins first or the rulesync CI job's "
        "git-status-porcelain check will red on every run."
    )


def test_the_gate_fails_closed_when_git_fails():
    """The bug CodeRabbit found in v1: a failing subprocess produced empty output, so the gate
    passed having checked nothing."""
    with pytest.raises(AssertionError, match="must NOT pass silently"):
        _git("this-is-not-a-git-command")


def test_a_vault_written_into_the_checkout_is_refused():
    """POSITIVE CONTROL for the `vault/` prefix.

    `DEFAULT_VAULT` is `./vault` and the quickstart runs `sluice init --vault ./vault` from the
    repo root, so a contributor following the docs writes lead notes -- employer names, job URLs,
    verdicts -- into a public checkout. The prefix was added with nothing exercising it, which is
    how a rule that matches nothing survives: `any([])` is False, so the sweep above passes
    whether or not the rule works.
    """
    assert _is_forbidden("vault/Job Applications/Job Leads/Example Co - Analyst.md")
    assert _is_forbidden("vault/Job Applications/Judging Profile.md")
    # ...at ANY depth, because DEFAULT_VAULT is cwd-relative: `sluice init` from a subdirectory
    # creates one there, and a root-anchored rule would leave it tracked.
    assert _is_forbidden("some/subdir/vault/Job Applications/Judging Profile.md")


# ── the neutrality sweep, extended to docs/**/*.j2 ──────────────────────────
#
# Everything above this point catches an absolute home path or a tracked artefact BY
# PATH. A worked-example CV template (docs/cv-template-example.html.j2) is a different
# risk shape entirely: its whole point is to show "real CSS" against real markup, which
# is exactly the pressure that produces a filled-in specimen -- a plausible candidate
# name, a real-sounding employer, a city -- none of which is an absolute path and none
# of which the sweep above would ever see. `docs/` also sits outside CLAUDE.md's stated
# neutrality scope ("no employer names, role preferences, locations, contact details,
# hostnames, or absolute paths in `sluice/` or `tests/`") and outside
# test_the_shipped_template_contributes_no_content in tests/test_renderer_template.py,
# which reads exactly ONE hardcoded path (the packaged default) via importlib.resources
# and would never see a file under docs/ however many are added there.
#
# The check reused below is that same "no content" property, applied to every `.j2`
# file docs/ actually ships rather than one named path: the heading vocabulary is
# DERIVED from cv/compose.py's own `_RULES` (never hand-listed, so it cannot drift from
# what the composer emits), and anything left over after stripping Jinja syntax and HTML
# tags -- with CSS `content:` literals HARVESTED rather than discarded, since those are
# rendered as visible text -- must be one of those headings.
#
# The strip itself lives in `tests/template_content.py`, shared with the shipped-template
# guard in tests/test_renderer_template.py. It was duplicated here, and a review found
# the identical bug in both copies: deleting `<style>...</style>` wholesale hid a planted
# `::after { content: " -- seeking a remote-first role" }` from twelve green assertions.
# One copy, so the next such fix cannot land in one place only.
def test_docs_template_examples_contribute_no_static_content():
    """Extends the neutrality sweep to docs/**/*.j2, asserting on SCOPE first.

    Two separate ways this guard could pass having checked nothing: `_RULES` yielding no
    headings (the sibling guard's own failure mode, so the same assertion is repeated
    here rather than assumed), and the docs/ glob finding no `.j2` file at all -- which
    would happen if the file were ever renamed, moved out of docs/, or given a different
    extension, and `all(... for _ in [])` is `True` regardless. For a NEGATIVE guard like
    this one, an empty sweep and a clean sweep produce the identical "no violations"
    result, so the scope has to be pinned independently of the content check it gates.
    """
    from tests.template_content import composer_headings, leftover_content

    headings = composer_headings()
    assert headings, "derived no headings, so this guard would pass vacuously"

    templates = sorted((REPO / "docs").rglob("*.j2"))
    assert templates, (
        "no docs/**/*.j2 file found -- this guard would pass having swept nothing, and "
        "for a negative guard that reads identically to a clean sweep")

    for path in templates:
        leftover = leftover_content(path.read_text(encoding="utf-8"))
        assert leftover <= headings, (
            f"{path.relative_to(REPO)} contributes content beyond the CvDocument fields "
            f"and structural headings a worked example is limited to: "
            f"{sorted(leftover - headings)}")
