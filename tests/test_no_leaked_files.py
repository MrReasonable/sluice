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
FORBIDDEN_COMPONENTS = (".memsearch", ".npmrc")

# The first path component after the home prefix, whatever it is called. This has now been
# wrong TWICE. The first version used `/Users/[a-z]`, which missed /Users/Alice and
# /home/2runner. The second was a NEGATED class written with Python escapes --
# `[^/\s'"`,)\]]+` -- and handed to `git grep -E`, where a bracket expression treats the
# backslash as a LITERAL MEMBER, not an escape: the class terminated at the `\]`, leaving a
# pattern that required one or more literal `]` characters. No real path has those, so the
# gate matched NOTHING for its entire life. Verified by planting a home path in a tracked
# file and watching the gate pass.
#
# So: a POSITIVE class, valid and identical in BOTH engines, and no escapes inside a bracket
# expression at all. It still spares the bare-prefix detector forms (a backtick or a quote
# right after the slash is not in the class).
#
# This class is used only by the PYTHON half (parsing a matched line for the allow-list).
# Discovery uses `_GREP_NAME` below, which is a negated class and does catch a component
# starting with a non-ASCII character -- the limit that used to be documented here.
# The hyphen is NOT in here: it has to sit at one END of a bracket expression -- first
# or last -- or it reads as a range. `[A-Za-z0-9._-/]` is the error `_-/`; `[-A-Za-z0-9._]`
# would be fine. Keeping it out of the shared constant and appending it in each pattern
# is what lets both patterns be built from one source without either of them tripping
# that. Caught immediately when this was first factored out, which is the cheap version
# of the mistake that left this gate inert for its whole life.
_NAME_CHARS = r"A-Za-z0-9._"
_NAME = f"[{_NAME_CHARS}-]+"

# What GIT GREP searches for -- deliberately different from the Python pattern, and the
# difference is the point. A NEGATED class catches a component starting with a non-ASCII
# character (`/home/<accented>`), which the ASCII class above misses; it is written with a
# POSIX `[:space:]` and no backslashes at all, because a backslash inside a bracket
# expression is a literal MEMBER in ERE -- the exact mistake that left this gate matching
# nothing for its entire life.
#
# Python's `re` cannot compile this string (`[:space:]` is not a POSIX class there, and
# the `)` inside breaks the expression), which is precisely why the two are separate
# constants tested through their own engines instead of one string handed to both.
#
# The asymmetry FAILS CLOSED: git finds a non-ASCII path, `_FULL_HOME_PATH_RE` does not
# parse it, `found` is empty, and the `found and ...` filter below therefore reports the
# line rather than skipping it. That filter was measured unfirable while both patterns
# shared a character set; widening this one makes it live.
# `<` and `>` are excluded so an angle-bracket placeholder (`/home/<user>/...`, which the
# design docs use) is not a match at all. The ASCII class never reached those because `<`
# is not in it; widening without this turned every documented placeholder into a hit.
_GREP_NAME = r"""[^]/[:space:]'"`,)<>]"""
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/(" + _NAME + ")")
# The WHOLE path, not just its first component, for the allow-list below. Built from the
# SAME character set plus `/`, so it is a strict superset of the pattern handed to git
# grep by construction rather than by coincidence -- which is what makes the
# `found and ...` guard below fail closed. Stated because it is load-bearing: if the two
# ever diverge so grep can match a line this does not, the guard is what stops the line
# being silently skipped.
_FULL_HOME_PATH_RE = re.compile(rf"/(?:Users|home)/[{_NAME_CHARS}/-]+")

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
_ALLOWED_HOME_PATHS = frozenset({
    "/home/example/.local/bin/claude",
    "/Users/someone/vault",
    "/home/.../claude",
})

# ...and WHERE each may appear. Repo-wide allowance is wider than the reason for it: a
# synthetic literal earns its exemption in the file that needs it, not everywhere.
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
    # `*.json` and `scripts` are here because this branch newly tracks package.json, a
    # 3300-line npm-generated package-lock.json, and scripts/guard_rulesync_drift.py -- all
    # three sat outside the old pathspec, and a generated lockfile is precisely what nobody
    # reads before committing.
    out = _git("grep", "-n", "-I", "-E", re.escape(prefix) + _GREP_NAME, "--",
               "sluice", "tests", "docs", "scripts",
               # `*.yaml` does NOT match `sluice.yaml.example`, and that is the one file the
               # quickstart copies verbatim onto a stranger's machine -- so it was outside
               # this gate entirely. Named explicitly rather than widened to `*example*`,
               # which would be a guess about future filenames.
               "*.md", "*.yaml", "*.yml", "*.toml", "*.json", ".gitignore",
               "sluice.yaml.example",
               allow=(0, 1))
    hits = []
    for line in out.splitlines():
        # this file necessarily contains the strings it is searching for
        if line.startswith("tests/test_no_leaked_files.py:"):
            continue
        # `found` must be non-empty: `all([])` is True, so a line git grep matched but
        # Python's `re` did not would otherwise be dropped in silence -- a gate failing
        # open, which is the bug this whole file just spent a round fixing.
        path_in_repo = line.split(":", 1)[0]
        found = _FULL_HOME_PATH_RE.findall(line)
        if found and all(
                m in _ALLOWED_HOME_PATHS
                and any(path_in_repo.startswith(w)
                        for w in _ALLOWED_HOME_PATH_FILES.get(m, ()))
                for m in found):
            continue
        hits.append(line)
    assert not hits, f"absolute home path under {prefix!r} in tracked files: {hits}"


def test_the_gate_catches_real_shapes_and_spares_bare_prefixes(tmp_path):
    """The gate's own regression test, run through the engine the GATE uses.

    It has been wrong twice, and the second time is why this no longer uses Python's `re`.
    `_NAME` is compiled by `re` here AND handed to `git grep -E` there, and the two disagree
    about backslashes inside a bracket expression -- so a `re`-based check certified a pattern
    the gate never actually ran, and the gate matched nothing for its entire life. Asserting
    through `git grep` is the only form that can catch that class of divergence.

    `--no-index` searches a plain directory, so this needs no repo and cannot be confused by
    the real one (which necessarily contains the strings being searched for).
    """
    # One file per shape: a single mixed file passes on ONE match, so a shape that
    # stopped being caught would hide behind its neighbours.
    shapes = {
        "lowercase": "/Users/devuser/.claude/x.jsonl",
        "capitalised": "/Users/ExampleUser/dev",
        "digit-initial": "/home/2runner/work",
        "non-ascii-initial": "/home/\u00c9mile/vault",
        "non-ascii-scandinavian": "/Users/\u00d8yvind/y",
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

    # The Python half is ASCII-only ON PURPOSE: it exists to parse a matched line against
    # the allow-list, and a non-ASCII path it cannot parse yields no match, which the
    # `found and ...` filter turns into a REPORT rather than a skip. Fail closed.
    for leak in ("/Users/devuser/.claude/x.jsonl", "/Users/ExampleUser/dev", "/home/2runner/work"):
        assert _HOME_PATH_RE.search(leak), f"gate would MISS a real leak: {leak}"
    assert not _FULL_HOME_PATH_RE.search("/home/\u00c9mile/vault"), (
        "the ASCII parser now matches non-ASCII, so the fail-closed path this gate "
        "relies on for those is no longer exercised")
    for detector in ("`/Users/`", "`/home/`, `.local`, `ssh`"):
        assert not _HOME_PATH_RE.search(detector), f"gate false-positives on a detector: {detector}"


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
        assert path.strip("/") in rules, f"{path} is gated but NOT gitignored -- they must agree"


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
    assert _is_forbidden(".claude/settings.json")
    assert not _is_forbidden("docs/.claude/settings.json")
    assert _is_forbidden("node_modules/rulesync/package.json")
    assert not _is_forbidden("tools/node_modules/rulesync/package.json")

    assert _is_forbidden(".memsearch/session.jsonl")
    assert _is_forbidden("docs/deep/.memsearch/session.jsonl")
    assert _is_forbidden(".npmrc")
    assert _is_forbidden("tools/sub/.npmrc")


def test_the_gate_fails_closed_when_git_fails():
    """The bug CodeRabbit found in v1: a failing subprocess produced empty output, so the gate
    passed having checked nothing."""
    with pytest.raises(AssertionError, match="must NOT pass silently"):
        _git("this-is-not-a-git-command")
