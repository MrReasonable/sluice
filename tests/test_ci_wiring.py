"""CI's quality bar, pinned against the docs that tell a human to run it.

A lint CI enforces but the docs understate is worse than no change: an agent following
`path-to-green` runs the documented command, passes locally, and lands red. A lint the docs claim
but CI does not enforce is worse still -- the bar reads as covered and isn't.

So the first half of this module asserts the lint bar and the docs AGREE. `scripts/` became a CI
lint target ahead of the docs sweep below, so its guard lands in `REQUIRED_TARGETS` from the
start rather than being widened in later.

The second half, added once the `rulesync` job existed to test, pins that job's wiring itself:
that `ci-success` actually CHECKS its result rather than merely ordering after it, that its
failure modes fail CLOSED (`set -euo pipefail`, the fail-open git-status form kept out), that
generation runs the locked binary rather than `npx` (which can fetch an unpinned rulesync), and
that the emitted `.claude/settings.json` is checked for the no-bypass hook -- a file COUNT alone
cannot see a hook rulesync wrote with no `command` key. `tests/test_hooks_wiring.py` records what
a missing assertion like that costs: "a correct guard that is not wired is inert, and this exact
file has already shipped inert once".

WHY TEXT, NOT A YAML PARSE: pyyaml is a guarded optional import in `sluice/` (CLAUDE.md's
stdlib-only rule), so a test needing it is a test that can skip itself into uselessness on a bare
install. What is being pinned is a command STRING, which text matching pins exactly.

THE THREE CASES, and why prose is not simply skipped. `ruff check` appears in the docs as a
command (`ruff check sluice tests scripts`), and as prose that names no targets at all -- a
graphviz node label reading `ruff check + pytest`. Skipping every no-target match would open the
hole this module exists to close: a command degraded to a bare `ruff check` would be reclassified
as prose and silently pass. So a match with no targets is only tolerated when a CONNECTOR follows
(`+`, `and`), which a shell command cannot start with; anything else with no targets fails.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
RULESYNC = ROOT / ".rulesync"

# The lint targets CI enforces. `scripts/` is here because guard scripts are production code --
# `.rulesync/rules/CLAUDE.md` puts `scripts/` under the mutation-testing bar -- and holding a
# merge gate to a lower standard than the code it guards is the wrong way round.
REQUIRED_TARGETS = ("sluice", "tests", "scripts")

# Stop at anything that ends a shell word list: markdown/code punctuation, or a shell operator.
_RUFF_CHECK = re.compile(r"ruff check(?P<rest>[^\n`|&;\"')]*)")
# Prose connectors a real invocation can never begin with. `&` is deliberately absent: it is a
# SHELL operator (`&&`), never a natural-language connector the way `+` and `and` are, and
# `_RUFF_CHECK`'s stop-class already excludes it from `rest` -- so a bare `ruff check` before
# `&&` reaches here with an EMPTY rest, not one starting with `&`. Admitting `&` here would sit
# inert today and, the moment someone widened the stop-class to let `&` through, would silently
# reclassify that exact degraded, target-less invocation as tolerated prose -- the regression
# this module exists to catch.
_CONNECTORS = ("+", "and")

# `npm ci` and its flags. Same stop-class as `_RUFF_CHECK`, and for the same reason: the
# documented form is `npm ci <flags> && npm run rulesync`, so the flag list ends at the `&&`.
_NPM_CI = re.compile(r"npm ci(?P<flags>[^\n`|&;\"')]*)")


def _targets(rest: str) -> list[str]:
    """Target paths in a `ruff check` argument string, with flags (`--fix`) dropped.

    Truncate at the first `#` before splitting: `_RUFF_CHECK`'s stop-class does not include `#`,
    so a trailing shell comment (`ruff check sluice tests   # scripts is linted in CI too`) runs
    on into `rest`, and a comment word (`scripts`, above) would otherwise be indistinguishable
    from a real lint target -- reporting a two-target command as a complete three-target one.
    """
    return [tok for tok in rest.split("#", 1)[0].split() if not tok.startswith("-")]


def _assert_bar_is_complete(text: str, where: str) -> int:
    """Assert every `ruff check` in `text` names EXACTLY REQUIRED_TARGETS. Returns the match count."""
    matches = list(_RUFF_CHECK.finditer(text))
    for match in matches:
        rest = match.group("rest").strip()
        # Classify BEFORE counting targets: a connector like `+` is not a `-` flag, so it would
        # otherwise be counted as a lint target and the match misreported as a broken command.
        if rest.startswith(_CONNECTORS):
            continue  # Prose naming the tool ("ruff check + pytest"), not stating the bar.
        targets = _targets(rest)
        if not targets:
            # An inline code span -- `ruff check` wrapped in backticks -- names the tool in a
            # sentence. A command degraded to a bare `ruff check` is NOT wrapped, so the wrapping
            # is what separates prose from the regression this test exists to catch.
            wrapped = text[match.start() - 1 : match.start()] == "`" and (
                text[match.end() : match.end() + 1] == "`"
            )
            assert wrapped, (
                f"{where}: `ruff check` names no lint targets and is not an inline code span "
                f"(rest={rest!r}). A command degraded to a bare `ruff check` lints nothing."
            )
            continue
        # Exact set equality, not mere containment. A `for required in REQUIRED_TARGETS: assert
        # required in targets` only ever checks the missing direction, so a doc naming a target
        # BEYOND the three ("... scripts extra") passed just as readily as the real command --
        # overstating the bar CI actually enforces is the same kind of drift as understating it.
        missing = [t for t in REQUIRED_TARGETS if t not in targets]
        assert not missing, (
            f"{where}: quality bar drops {missing!r} (targets={targets}). "
            "An agent following it passes locally and lands red in CI."
        )
        extra = sorted(set(targets) - set(REQUIRED_TARGETS))
        assert not extra, (
            f"{where}: quality bar names targets beyond what CI enforces: {extra!r} "
            f"(targets={targets}). REQUIRED_TARGETS is the authoritative list."
        )
    return len(matches)


def test_trailing_comment_words_are_not_counted_as_lint_targets():
    """Regression: this exact line used to PASS `_assert_bar_is_complete` even though the real
    command only names two of the three required targets -- `scripts` came from the comment, not
    the command. Same shape as the bug this whole module exists to catch: a documented bar that
    looks complete but isn't."""
    line = "ruff check sluice tests   # scripts is linted in CI too"
    with pytest.raises(AssertionError, match="scripts"):
        _assert_bar_is_complete(line, "synthetic")


def test_trailing_comment_after_a_genuine_three_target_command_still_passes():
    """Companion to the regression above, using the real line from `.rulesync/rules/CLAUDE.md`:
    truncating at `#` must not turn a genuinely complete command into a false failure just
    because its trailing comment happens to repeat one of the target names."""
    line = (
        "ruff check sluice tests scripts         "
        "# NB: ruff is NOT in [test]; pip install ruff==0.15.21 (the CI pin)"
    )
    assert _assert_bar_is_complete(line, "synthetic") == 1


def test_extra_targets_beyond_required_are_not_silently_accepted():
    """Regression: a containment-only check (`required in targets` for each REQUIRED target)
    passes a command naming a target BEYOND the three just as readily as the real one --
    `ruff check sluice tests scripts extra` used to pass. That overstates what CI actually
    lints, which is just as misleading to a reader as understating it."""
    line = "ruff check sluice tests scripts extra"
    with pytest.raises(AssertionError, match="extra"):
        _assert_bar_is_complete(line, "synthetic")


def test_ampersand_led_command_fails_as_broken_not_tolerated_as_prose():
    """Pins the choice above: `&` is dropped from `_CONNECTORS`, not admitted into the regex.

    `ruff check && pytest` names no lint targets at all -- a genuinely broken, degraded
    invocation, not prose naming the tool the way `ruff check + pytest` is. It must fail with
    the same "names no lint targets" reason as any other target-less command, not be silently
    tolerated as a connector-led exception.
    """
    with pytest.raises(AssertionError, match="names no lint targets"):
        _assert_bar_is_complete("ruff check && pytest", "synthetic")


def test_ci_lints_every_required_target():
    # Non-vacuity: without this, a rename that stops the regex matching passes silently.
    assert _assert_bar_is_complete(CI.read_text(), str(CI)), (
        f"no `ruff check` found in {CI}: this gate would pass without having checked anything"
    )


def test_every_documented_quality_bar_matches_ci():
    """The docs an agent actually follows must name the same targets CI enforces.

    Enumerated from the canonical tree, never hand-listed: a written-out file list goes stale the
    moment a skill is added, and stale-and-green is exactly the failure this module prevents.
    """
    total = 0
    for path in sorted(RULESYNC.rglob("*")):
        if path.is_file():
            total += _assert_bar_is_complete(path.read_text(), str(path.relative_to(ROOT)))
    assert total, (
        f"no `ruff check` found anywhere under {RULESYNC}: either the docs stopped stating the "
        "quality bar, or this sweep stopped finding it -- both leave the bar unguarded"
    )


def _ci_text() -> str:
    return CI.read_text()


def _job_directives(name: str) -> str:
    """One job's YAML, sliced out of ci.yml by indentation, with COMMENT LINES REMOVED.

    Sliced, because whole-file substring assertions pass on the wrong job: `set -euo pipefail`
    matched ANY job, so moving it to `lint` -- where it does nothing for the rulesync
    pipeline -- kept every such assertion green. A job key is the only thing at two-space
    indent; steps sit at four or more, so the next two-space key ends the block.

    Comment-stripped, because a substring test over raw text matches the PROSE EXPLAINING a
    rule as readily as the rule. tests/test_no_leaked_files.py records this exact bug from the
    other side, and it fired here immediately: the comment above this job's tree check quotes
    the fail-open form verbatim, so a check for its absence found it in the warning against it.
    """
    text = _ci_text()
    start = text.index(f"\n  {name}:\n")
    rest = text[start + 1 :]
    end = re.search(r"\n  [a-z][\w-]*:\n", rest)
    block = rest[: end.start()] if end else rest
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def test_ci_success_requires_the_rulesync_job():
    """Membership asserted SEPARATELY from the consistency check below.

    Consistency alone is not enough: deleting the `needs:` entry AND its conjunct together
    leaves both sides agreeing while the gate is unwired.
    """
    text = _ci_text()
    assert "rulesync" in text.split("ci-success:")[1].split("if:")[0], (
        "ci-success does not depend on the rulesync job"
    )
    assert 'needs.rulesync.result }}" = success' in text, (
        "ci-success does not CHECK the rulesync result. `if: always()` means `needs:` only "
        "orders the job -- the && chain is the only thing that can fail ci-success, so a red "
        "gate would yield a green required check."
    )


def test_every_needed_job_is_checked_in_the_success_chain():
    """Both ends enumerated from the file, never hand-listed."""
    block = _ci_text().split("ci-success:")[1]
    needed = re.search(r"needs:\s*\[([^\]]*)\]", block).group(1)
    jobs = [j.strip() for j in needed.split(",") if j.strip()]
    assert jobs, "ci-success declares no needs: this test would pass without checking anything"
    for job in jobs:
        assert f'needs.{job}.result }}}}" = success' in block, (
            f"ci-success needs {job!r} but never checks its result"
        )


def test_the_rulesync_job_sets_pipefail():
    """`bash -e {0}` is the default -- `-e` but NOT pipefail, so `cmd | tee` reports tee's
    status and a rulesync exit 1 would be swallowed.

    Scoped to the job, not the file: pipefail set anywhere else protects nothing here.
    """
    assert "set -euo pipefail" in _job_directives("rulesync")


def test_the_capture_file_is_outside_the_work_tree():
    """A scratch file beside the checkout is itself untracked and trips the porcelain check."""
    assert "$RUNNER_TEMP/rulesync-output.txt" in _job_directives("rulesync")


def test_the_tree_check_fails_closed_when_git_fails():
    """`if [ -n "$(git status --porcelain)" ]` DISCARDS the substitution's exit status, and
    `set -e` is suppressed inside an `if` condition. Verified in bash: when git cannot run, that
    form reports a CLEAN tree and exits 0, so a gitignore gap ships green -- the fails-open class
    this repo spends its life engineering out, and one the Python side already closed
    (tests/test_no_leaked_files.py's `test_the_gate_fails_closed_when_git_fails`).

    Assign first, check the status, THEN test the value.
    """
    block = _job_directives("rulesync")
    assert 'if ! dirty=$(git status --porcelain); then' in block, (
        "the tree check no longer captures git's exit status separately from its output: a "
        "failing git would be read as a clean tree"
    )
    assert '[ -n "$dirty" ]' in block, "the tree check no longer tests the captured output"
    assert 'if [ -n "$(git status --porcelain)" ]' not in block, (
        "the fail-OPEN form is back: a command substitution inside `[ -n ... ]` throws away the "
        "exit status, and `set -e` does not fire inside an `if` condition"
    )


def test_the_emitted_hook_command_is_asserted_after_generation():
    """The drift guard counts FILES, so it cannot close the failure it cites as its motivation.

    `.rulesync/hooks.json`'s own comment records that rulesync can write a hook with NO
    `command` key while printing "All done!" and exiting 0. The file count is IDENTICAL in both
    cases, so no number makes that check work -- which is why this one reads content instead.
    Only the emitted `.claude/settings.json` can tell them apart, and the rulesync job is the
    first environment in this repo where node exists and the generator has run, so the artifact
    can finally be checked. This test is what keeps that check wired.

    It is also why that comment no longer calls itself the ONLY defence against a version bump:
    a check on the generated ARTIFACT survives an input-schema rename that would leave the
    offline suite -- which can only assert the input -- entirely green.
    """
    block = _job_directives("rulesync")
    assert ".claude/settings.json" in block, (
        "the rulesync job no longer inspects the emitted .claude/settings.json. Without it, "
        "rulesync dropping a hook's `command` key ships the no-bypass guard INERT and green."
    )
    assert "guard_no_bypass.py" in block, (
        "the settings.json check no longer names the command it is looking for, so it can pass "
        "on a settings.json with hooks and no commands"
    )
    assert "PreToolUse" in block, (
        "the settings.json check is no longer STRUCTURAL -- it names no hook event, so it is "
        "back to proving only that the file CONTAINS the string. Measured: a settings.json "
        "whose command had been re-nested under PostToolUse still satisfies `grep -q "
        "guard_no_bypass.py` while Claude Code, which reads hooks.PreToolUse[*].hooks[*]."
        "command and nothing else, runs nothing at all."
    )
    assert block.index("guard_rulesync_drift.py") < block.index(".claude/settings.json"), (
        "the artifact check must run AFTER generation is confirmed complete: on a run that "
        "wrote nothing, a stale settings.json from the checkout would satisfy it"
    )


def test_the_emitted_agent_and_skill_names_are_asserted_against_the_source():
    """A file COUNT cannot see a RENAMED agent, and these agents are the merge gate.

    Same argument the hook-command check already rests on, applied to the other content the
    drift guard pins by number alone. `EXPECTED` fixes 5 subagents and 4 skills; rename one on
    the way out and it is still 5 and still 4. Measured against a real generated tree: the
    guard stays green, the tree stays clean, and a review this repo's merge gate is built from
    silently never runs. Only comparing the emitted NAMES against `.rulesync/` can tell.

    Both ends enumerated from the filesystem in the job itself, never hand-listed here -- a
    written-out roster is stale the moment an agent is added.
    """
    block = _job_directives("rulesync")
    for path in (".claude/agents", ".rulesync/subagents", ".claude/skills", ".rulesync/skills"):
        assert path in block, (
            f"the rulesync job no longer compares {path}. A renamed or dropped review agent or "
            "skill ships with the file count intact and nothing red anywhere."
        )


def test_the_guard_runs_before_the_porcelain_check():
    """Index comparison, not substring presence: a substring test passes when EITHER is
    deleted. A fail-open produces a CLEAN tree, so porcelain-first would pass on it."""
    text = _ci_text()
    guard_at = text.index("guard_rulesync_drift.py")
    porcelain_at = text.index("git status --porcelain")
    assert guard_at < porcelain_at, "the completeness guard must run before the tree check"


def test_the_job_uses_the_locked_binary_not_npx():
    """Substituting `npx rulesync@<pinned-version> generate ...` back keeps the counts identical
    and the guard green while silently discarding the locked transitive tree the pin exists for.

    Deliberately no literal version digit here: tests/test_rulesync_version_pin.py enforces that
    package.json is the ONLY tracked file allowed to name the pinned rulesync version, and this
    docstring is not on its allowlist.
    """
    text = _ci_text()
    assert "npm ci --ignore-scripts" in text
    assert "npx" not in text, "the CI job must not invoke npx: it can fetch an unpinned rulesync"
    assert text.index("npm ci --ignore-scripts") < text.index("guard_rulesync_drift.py")


def _npm_ci_flags(text: str) -> list[str]:
    """The flag string of every `npm ci` in `text`, whitespace-normalised."""
    return [" ".join(match.group("flags").split()) for match in _NPM_CI.finditer(text)]


def _doc_sources() -> list[Path]:
    """Every canonical doc that could state the regenerate command.

    Enumerated from the tree, never hand-listed. `docs/superpowers/` is deliberately absent:
    those are dated design records, and this repo's convention is to supersede one with a dated
    note rather than rewrite what it said at the time.
    """
    return [ROOT / ".gitignore", *sorted(p for p in RULESYNC.rglob("*") if p.is_file())]


def test_the_documented_install_command_is_the_one_ci_runs():
    """The docs and CI must install IDENTICALLY, not merely similarly.

    This has now drifted twice. The decision that introduced the documented command made
    byte-identity with CI its entire stated purpose; the later decision that added
    `--ignore-scripts` to CI changed only CI. A human or agent following the canonical rules
    then ran install scripts CI deliberately skips -- a different tree than the one the gate
    downstream is asserting counts against.

    Both ends are read from the files, so a change to either side has to be a change to both.
    """
    ci_flags = _npm_ci_flags(_ci_text())
    assert len(ci_flags) == 1, (
        f"expected exactly one `npm ci` in {CI}, found {len(ci_flags)}: {ci_flags}. This test "
        "cannot say which one the docs must match."
    )
    expected = ci_flags[0]
    # Non-vacuity: with no flags on either side, every doc would agree on the empty string and
    # this test would pass without pinning anything. (`--ignore-scripts` itself is pinned by
    # test_the_job_uses_the_locked_binary_not_npx.)
    assert expected, f"CI's `npm ci` names no flags; {CI} no longer states an install contract"

    documented = 0
    for path in _doc_sources():
        for flags in _npm_ci_flags(path.read_text()):
            documented += 1
            assert flags == expected, (
                f"{path.relative_to(ROOT)} documents `npm ci {flags}` but CI runs "
                f"`npm ci {expected}`. Someone following the docs installs differently from "
                "the gate that judges them."
            )
    assert documented, (
        "no `npm ci` found in any canonical doc: either the regenerate command stopped being "
        "documented, or this sweep stopped finding it -- both leave the two ends unpinned"
    )


def test_package_json_runs_the_locked_binary_by_path():
    """`npm run` PREPENDS node_modules/.bin to PATH, it does not restrict PATH. Measured: with
    no node_modules, a bare `rulesync` silently ran a global 9.2.0 and exited 0."""
    manifest = json.loads((ROOT / "package.json").read_text())
    assert manifest["scripts"]["rulesync"] == (
        "node_modules/.bin/rulesync generate -t 'claudecode,agentsmd' -f '*'"
    )
