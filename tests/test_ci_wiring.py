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

The third half pins the coverage reporting added for #11, whose failure modes are all QUIET.
Coverage does not gate, so it can stop being measured, stop being published, or start measuring
the wrong tree with the build still green -- and losing branch coverage makes the number go UP,
which reads as an improvement. Each of those is asserted here rather than left to a reader.

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
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
RULESYNC = ROOT / ".rulesync"
PYPROJECT = ROOT / "pyproject.toml"

# The lint targets CI enforces. `scripts/` is here because guard scripts are production code --
# `.rulesync/rules/CLAUDE.md` puts `scripts/` under the mutation-testing bar -- and holding a
# merge gate to a lower standard than the code it guards is the wrong way round.
REQUIRED_TARGETS = ("sluice", "tests", "scripts")

# What coverage must measure: the same production tree the lint bar covers, minus the tests
# themselves. DERIVED from REQUIRED_TARGETS rather than re-listed, so a target added to the lint
# bar cannot silently stay unmeasured -- a second hand-written tuple is a second thing to forget.
COVERAGE_SOURCES = tuple(t for t in REQUIRED_TARGETS if t != "tests")

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


def _ci_directives() -> str:
    """The whole of ci.yml with COMMENT LINES REMOVED -- `_job_directives` widened to the file.

    For assertions about the ABSENCE of something anywhere in CI, where scoping to one job would
    miss it. Comment-stripped for the reason `_job_directives` documents, and this one was not
    hypothetical: `--cov-fail-under` appears in a comment stating that CI deliberately does not
    pass it, so the first version of the check below found the prose warning against the thing
    and failed on it.
    """
    return "\n".join(
        ln for ln in _ci_text().splitlines() if not ln.lstrip().startswith("#")
    )


def _step_containing(job: str, needle: str) -> str:
    """The ONE step of `job` whose body contains `needle`, comment lines already removed.

    Whole-job substring assertions are satisfied by pieces sitting in DIFFERENT steps, which for
    a shell block is the same bug `_job_directives` fixed one level up: `set -euo pipefail` in
    step 3 does nothing for a redirect in step 5, and a redirect to $GITHUB_STEP_SUMMARY in some
    unrelated step does not mean the coverage report is what reaches it. A step is the unit that
    shares a shell, so it is the unit these assertions belong to.

    A step begins at the only six-space `- ` in a job; its keys sit at eight. Requires EXACTLY
    one match: zero means the sweep found nothing and every assertion over it would be vacuous,
    and two means the caller cannot say which step it is asserting about.
    """
    parts = re.split(r"\n(?=      - )", _job_directives(job))
    matches = [part for part in parts if needle in part]
    assert len(matches) == 1, (
        f"expected exactly one step in the {job!r} job containing {needle!r}, found "
        f"{len(matches)}. Zero makes every assertion over the result vacuous; two makes it "
        "ambiguous which step is being pinned."
    )
    return matches[0]


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


def test_the_emitted_content_guard_runs_after_generation():
    """The drift guard counts FILES, so it cannot close the failure it cites as its motivation.

    `.rulesync/hooks.json` records that rulesync can write a hook with NO `command` key while
    printing "All done!" and exiting 0. The file count is IDENTICAL in both cases, so no number
    makes that check work. Same for a RENAMED review agent: `EXPECTED` fixes 5 subagents and 4
    skills, and renaming one on the way out leaves it 5 and 4 while a review this repo's merge
    gate is built from silently never runs. Only the emitted artifact can tell either apart.

    This test pins the WIRING only -- that the job invokes the content guard, and does so after
    generation is confirmed complete. What the guard actually asserts is covered offline by
    `tests/test_guard_emitted_outputs.py`, where each case can be built and mutated. That split
    is deliberate: the checks used to be a heredoc in this workflow, where they were invisible
    to `ruff check` and could not be unit-tested at all.
    """
    block = _job_directives("rulesync")
    assert "scripts/guard_emitted_outputs.py" in block, (
        "the rulesync job no longer runs the emitted-hook guard. Without it, rulesync "
        "dropping a hook's `command` key ships the no-bypass guard INERT with the file count "
        "intact, the tree clean, and nothing red anywhere."
    )
    assert block.index("guard_rulesync_drift.py") < block.index("guard_emitted_outputs.py"), (
        "the artifact check must run AFTER generation is confirmed complete: on a run that "
        "wrote nothing, a stale .claude/ from the checkout would satisfy it"
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


def _pyproject() -> dict:
    """pyproject.toml, parsed. tomllib is stdlib on every version this repo supports
    (requires-python >= 3.12), so unlike the yaml case in the module docstring there is no
    optional import to skip a test into uselessness."""
    return tomllib.loads(PYPROJECT.read_text())


def _coverage_config() -> dict:
    return _pyproject().get("tool", {}).get("coverage", {})


# The config files coverage.py consults AHEAD of pyproject.toml, in its own precedence order.
# Each maps to the section that would make it a coverage config; `.coveragerc` exists for
# nothing else, so its mere presence counts.
_SHADOWING_COVERAGE_CONFIGS = {
    ".coveragerc": None,
    "setup.cfg": "[coverage:",
    "tox.ini": "[coverage:",
}


def test_pyproject_is_the_effective_coverage_config():
    """Every other coverage guard here reads pyproject. A higher-precedence file makes them all
    assertions about a file coverage.py never opens.

    Measured, not reasoned: a `.coveragerc` carrying `[report] fail_under = 99` gated the build
    at 99% while pyproject said nothing of the kind -- and `test_coverage_measures_every_linted_
    production_target` and `..._branches_not_only_lines` both stayed GREEN throughout, because
    the values they assert on were still sitting in a file that had stopped being read. That is
    the worst shape a guard can take: green, specific, and about the wrong thing.

    So this is asserted at the level of WHICH FILE WINS, which is the only place it can be.
    (`COVERAGE_RCFILE` can override from the environment too; nothing in a repo can guard an env
    var, and CI sets none -- the point here is that no file in the TREE silently takes over.)
    """
    shadowing = []
    for name, section in _SHADOWING_COVERAGE_CONFIGS.items():
        path = ROOT / name
        if not path.exists():
            continue
        if section is None or section in path.read_text():
            shadowing.append(name)
    assert not shadowing, (
        f"{shadowing} outrank pyproject.toml in coverage.py's config search, so the coverage "
        "settings this module asserts on are no longer the ones in effect. Move the settings, "
        "or delete the shadowing file."
    )


def test_the_coverage_report_names_its_uncovered_lines():
    """`show_missing` IS #11's acceptance criterion, not a formatting preference.

    #11 asks that "the uncovered lines in `core/` and `triage/` are visible enough to be worth
    acting on". Without this key the report still renders, still prints a percentage per file,
    and simply drops the `Missing` column -- so it keeps saying WHICH files are thin and stops
    saying WHERE, which is the half a human can act on. Witnessed: deleting it leaves the whole
    suite green.
    """
    assert _coverage_config().get("report", {}).get("show_missing") is True, (
        "[tool.coverage.report] show_missing is no longer true: the per-file breakdown still "
        "renders, minus the line numbers that make it actionable -- and nothing else goes red"
    )


def test_coverage_stores_relative_paths():
    """Without `relative_files`, coverage records the checkout's ABSOLUTE path -- in the report
    it publishes and inside the `.coverage` data file itself.

    That is a leak vector, not a cosmetic one: locally the prefix is a real home directory, and
    `.coverage` is a binary SQLite file, so `.gitignore` protects it only until the day it is
    already tracked -- which is exactly the route `.memsearch/` took into this public repo three
    times (tests/test_no_leaked_files.py records it). Witnessed both ways: with the key, paths
    read `sluice/core/app.py`; without it, the full checkout path.
    """
    assert _coverage_config().get("run", {}).get("relative_files") is True, (
        "[tool.coverage.run] relative_files is no longer true: the published report and the "
        "`.coverage` data file both start recording the checkout's absolute path"
    )


def test_the_derived_coverage_source_list_is_neither_empty_nor_the_whole_lint_bar():
    """Non-vacuity for the derivation itself, asserted before anything is compared against it.

    `COVERAGE_SOURCES` filters `tests` out of `REQUIRED_TARGETS`. If that filter ever matched
    everything the expectation would be the empty tuple, and `source == list(COVERAGE_SOURCES)`
    would then be satisfied by a pyproject that measures NOTHING. If it matched nothing, the
    expectation would demand coverage of the test suite itself. Pin both ends.
    """
    assert COVERAGE_SOURCES, "COVERAGE_SOURCES is empty: every guard below would assert nothing"
    assert "tests" not in COVERAGE_SOURCES, "the test suite must not be a coverage source"
    assert set(COVERAGE_SOURCES) < set(REQUIRED_TARGETS), (
        "COVERAGE_SOURCES stopped being a strict subset of the lint bar: the derivation is no "
        "longer dropping anything, so it is no longer derived from REQUIRED_TARGETS in any "
        "meaningful sense"
    )


def test_coverage_measures_every_linted_production_target():
    """Exact set equality against the lint bar, not containment.

    Dropping `scripts` here would leave the merge gate's own guard scripts unmeasured while the
    build stays green -- and `.rulesync/rules/CLAUDE.md` holds those scripts to the mutation-
    testing bar precisely because an inert guard is this repo's most expensive bug shape.
    Losing the whole key is worse still: `pytest --cov` with no configured source measures every
    module that happens to get imported, site-packages included, which is a report nobody reads.
    """
    source = _coverage_config().get("run", {}).get("source")
    assert source is not None, (
        f"{PYPROJECT.name} no longer sets [tool.coverage.run] source. `--cov` with no source "
        "measures whatever gets imported -- including site-packages -- so the report survives "
        "and stops being about this project."
    )
    assert sorted(source) == sorted(COVERAGE_SOURCES), (
        f"coverage measures {sorted(source)} but CI lints {sorted(REQUIRED_TARGETS)} as "
        f"production code (expected {sorted(COVERAGE_SOURCES)}). A target linted but never "
        "measured reads as covered and is not."
    )


def test_coverage_measures_branches_not_only_lines():
    """Turning branch coverage off makes the number go UP, which is why it needs a guard.

    A regression that silently IMPROVES the headline metric has nothing to alert on. And the
    distinction is the whole point of #11: a fully-executed `if` with only one arm ever taken is
    a path that merely executes rather than one that is asserted, and line coverage calls it 100%.
    """
    assert _coverage_config().get("run", {}).get("branch") is True, (
        "[tool.coverage.run] branch is no longer true: partially-taken branches would be "
        "reported as fully covered, and total coverage would RISE on the change that did it"
    )


def test_the_test_job_measures_coverage():
    """Scoped to the job, and read from the directives so a COMMENT mentioning `--cov` cannot
    satisfy it -- the same trap `_job_directives` was written for on the rulesync job, and one
    this file's own workflow comments would spring, since they name the flag verbatim."""
    block = _job_directives("test")
    assert "python -m pytest --cov" in block, (
        "the test job no longer runs pytest under coverage. Nothing goes red when it stops: "
        "coverage reports and does not gate, so the only symptom is a summary that quietly "
        "stops appearing."
    )


def test_the_test_job_publishes_the_coverage_report_to_the_run_summary():
    """#11's acceptance is that the per-file breakdown is "visible enough to be worth acting
    on". Buried in a job log it is not, so the render to $GITHUB_STEP_SUMMARY is the acceptance
    criterion itself and not decoration.

    Ordering asserted by index, not by presence: a presence-only check passes when EITHER half
    is deleted, and `coverage report` run BEFORE pytest would render whatever stale `.coverage`
    the checkout happened to carry -- or, on a clean checkout, fail for a reason that says
    nothing about the change under test.

    Both halves key on the INVOCATION (`python -m coverage report`), never on the bare phrase
    "coverage report". Witnessed: with the phrase, deleting the invocation outright left this
    test GREEN, because the step's own `name:` -- "Publish the coverage report to the run
    summary" -- contains it. A step named after the thing it no longer does is precisely the
    shape this assertion exists to catch, and it was satisfied by the name for its first draft.

    And the redirect is asserted INSIDE the step that renders the report, not anywhere in the
    job. Searching the job independently passes when an unrelated step writes to
    $GITHUB_STEP_SUMMARY while the coverage report goes only to the log -- the two halves would
    both be present and connected to nothing.
    """
    block = _job_directives("test")
    assert "python -m coverage report" in block, (
        "the summary step no longer renders a coverage report. It may still be NAMED for one: "
        "assert the invocation, never the phrase."
    )
    step = _step_containing("test", "python -m coverage report")
    assert "$GITHUB_STEP_SUMMARY" in step, (
        "the step that renders the coverage report does not redirect it to the run summary; a "
        "per-file breakdown that exists only in the job log is not what #11 asked for"
    )
    assert block.index("python -m pytest --cov") < block.index("python -m coverage report"), (
        "the report must be rendered AFTER the run that collects it: reversed, it renders a "
        "stale or absent data file"
    )


def test_the_summary_step_refuses_an_unset_variable():
    """`-u` is the flag that matters here, and the first version of this test said why wrongly.

    It claimed an unset `$GITHUB_STEP_SUMMARY` would redirect into a file named by the empty
    string and still exit 0. Measured, that is false: the redirect fails on a compound command
    and the step exits 1 under plain `bash` with no flags at all. Two reviewers caught it.

    What DOES pass silently is an unset `$PYTHON_VERSION` -- the heading renders as
    `## Coverage (Python )` and the step exits 0. Measured both ways. `-u` is what turns that
    into an unbound-variable error, so it is the real reason the flags are there, and this
    assertion is scoped to the step that interpolates the variable rather than to the job.

    (`-e` is already GitHub's default `bash -e {0}`, and `pipefail` is inert with no pipe in the
    block. Neither is load-bearing today; the full form is kept so a later pipe cannot silently
    swallow a failure, which is exactly what it guards in the rulesync job.)
    """
    step = _step_containing("test", "$PYTHON_VERSION")
    assert "set -euo pipefail" in step, (
        "the step interpolating $PYTHON_VERSION no longer sets `-u`: an unset variable would "
        "render the heading `## Coverage (Python )` and the step would pass"
    )


def test_coverage_reports_and_does_not_gate():
    """#11 asks for a number to LOOK at, deliberately not one to pass.

    Its reasoning, verbatim: "A coverage threshold invites tests written to raise the number
    rather than to catch bugs, which is the failure mode this project can least afford." Three
    of this repo's worst bugs -- a CLI flag parsed and never forwarded, a fallback backend
    believed in and non-functional, a default that binned every located lead -- were all
    invisible to a green suite, and a floor rewards padding it rather than closing them.

    This is a SPEED BUMP over a recorded decision, not a permanent law: #11 itself contemplates
    failing on a DECREASE once a baseline exists, which is a different mechanism from a floor
    and would arrive with its own reasoning. Deleting this test as part of that change is
    correct; tripping over it by adding `--cov-fail-under` to a CI line is what it is for.

    ALL THREE routes to a threshold are checked, because two of them were found unguarded by
    review after the first draft covered only the CI command line. The `addopts` one is not the
    exotic case: this same diff hangs the coverage rationale off `[tool.pytest.ini_options]`,
    which makes that table read like where coverage is configured. Witnessed there --
    `addopts = "-q --cov-fail-under=99"` gated the build with every test in this file green.
    """
    threshold = _coverage_config().get("report", {}).get("fail_under")
    assert threshold is None, (
        f"[tool.coverage.report] fail_under is set to {threshold!r}. #11 asks for a report, not "
        "a gate: a floor invites tests written to raise the number rather than to catch bugs."
    )
    addopts = _pyproject().get("tool", {}).get("pytest", {}).get("ini_options", {}).get(
        "addopts", ""
    )
    assert "--cov-fail-under" not in addopts, (
        f"[tool.pytest.ini_options] addopts passes --cov-fail-under ({addopts!r}). addopts "
        "applies to EVERY pytest invocation, so this gates CI and every local run at once."
    )
    assert "--cov-fail-under" not in _ci_directives(), (
        "CI passes --cov-fail-under. #11 asks for a report, not a gate; see this test's reason."
    )


def test_package_json_runs_the_locked_binary_by_path():
    """`npm run` PREPENDS node_modules/.bin to PATH, it does not restrict PATH. Measured: with
    no node_modules, a bare `rulesync` silently ran a global 9.2.0 and exited 0."""
    manifest = json.loads((ROOT / "package.json").read_text())
    assert manifest["scripts"]["rulesync"] == (
        "node_modules/.bin/rulesync generate -t 'claudecode,agentsmd' -f '*'"
    )
