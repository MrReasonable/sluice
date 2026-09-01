"""`post-release.yml`'s wiring, pinned the way `ci.yml`'s and the release workflows' already are.

This workflow is the artefact PR #218 is named for, and until this file existed
`grep -rn post-release tests/*.py` returned exactly one hit -- a docstring -- against 33 tests
for `ci.yml` and 97 for the release/testpypi/homebrew workflows. That gap matters more here than
the line count suggests, because this workflow CANNOT be exercised before it merges: its first
real execution is against a version that is already public. Everything a test can establish
beforehand is the only pre-merge evidence there is.

WHAT THIS FILE MISSED, and now pins. Every guard below reads post-release.yml ALONE, and the
defect that shipped lived in the relationship between two files: the workflow triggered on
`release: published`, which fires when release-please cuts the release -- its FIRST job, before
any artefact is published. It was red on v2.2.1, v2.3.0 and v2.4.0, every release since it was
added, and nothing here could see it, because nothing here read release-please.yml. The
correspondence guards at the end of this file are the fix, and they derive both ends.

WHY TEXT, NOT A YAML PARSE. The same reason `tests/test_ci_wiring.py` gives: most of what these
guards pin is a command STRING or an interpolation shape, which text matching pins exactly and a
parse would only make harder to read. Where a guard needs YAML's own semantics it should parse
instead, and the trigger guards at the end of this file now do: this file's own prose quotes
`release: published` several times, and a text match cannot tell the trigger from a comment
explaining why the trigger is gone.

THE HOLE THIS FILE WAS OPENED FOR is the `report` job. It aggregates the channel jobs through
TWO hand-lists -- the `R_*` env bindings and the `for r in ...` word list -- layered over a third
(`needs:`). A fifth channel job added to `needs:` and to neither list fails while `report` exits
0, which is the identical "can fail while the aggregate stays green" shape that
`test_every_real_job_is_aggregated_by_ci_success` was added on this same branch to close for
`ci.yml`. Closing it there and not here would have been fixing one instance of a rule and calling
it done.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
POST_RELEASE = ROOT / ".github" / "workflows" / "post-release.yml"
RELEASE_PLEASE = ROOT / ".github" / "workflows" / "release-please.yml"

# Jobs that are not themselves an install channel: `version` resolves which release to check,
# `report` is the aggregator doing the checking. Everything else IS a channel and must be
# aggregated. Named rather than inferred so that adding a non-channel helper job is a
# deliberate edit here, and `test_the_non_channel_jobs_all_exist` keeps this honest.
_NON_CHANNEL_JOBS = {"version", "report"}

# Which release-please.yml job PUBLISHES the thing each channel job here installs. The
# dispatcher must wait on every one of these, or the check races the publish it is checking.
# `deb` and `rpm` both name `release-assets` rather than `linux-packages`: the latter BUILDS
# the packages, the former uploads them to the release, and the release is where these jobs
# curl them from. Naming the builder would be the same off-by-one the dispatcher exists to
# fix. Keyed by channel so `test_every_channel_names_its_producing_job` can require this
# mapping to cover the channel set EXACTLY -- a new channel cannot be added without one.
_CHANNEL_PRODUCER = {"pypi": "pypi", "deb": "release-assets", "rpm": "release-assets",
                     "docker": "docker"}


def _text() -> str:
    return POST_RELEASE.read_text()


def _jobs_block() -> str:
    """Everything under the top-level `jobs:` key.

    Scoped rather than swept over the whole file: an unscoped two-space key match also picks up
    the trigger names under `on:` (`workflow_dispatch:` is one), and the roster would then be
    compared against a set that is not job names at all. That exact mistake was made and caught
    in `tests/test_ci_wiring.py`'s equivalent sweep.
    """
    text = _text()
    assert "\njobs:\n" in text, "post-release.yml has no top-level `jobs:` block"
    return text.split("\njobs:\n", 1)[1]


def _job_names() -> set[str]:
    """Every top-level job id.

    `[A-Za-z_][A-Za-z0-9_-]*` because that is what GitHub actually permits -- a lowercase-only
    pattern silently omits any other job from BOTH sides of every comparison below, so it could
    never be reported missing. Measured on `ci.yml`'s equivalent guard: renaming a job to
    `build_packages` while dropping it from the aggregate left the whole suite green.
    """
    return set(re.findall(r"^  ([A-Za-z_][A-Za-z0-9_-]*):$", _jobs_block(), re.M))


def _report_block() -> str:
    block = _jobs_block()
    assert "\n  report:\n" in block, "post-release.yml has no `report` job"
    return block.split("\n  report:\n", 1)[1]


def test_the_job_sweep_finds_the_real_roster():
    """SCOPE for everything below: a sweep that matched nothing satisfies every assertion made
    over it, and `all([])` is True. Named members as well as a floor, so a pattern that still
    matches SOMETHING but lost a shape is caught too."""
    jobs = _job_names()
    assert len(jobs) >= 5, f"the job sweep found only {sorted(jobs)} -- it is not working"
    for expected in ("version", "pypi", "deb", "rpm", "docker", "report"):
        assert expected in jobs, f"{expected!r} missing from the sweep: {sorted(jobs)}"


def test_the_non_channel_jobs_all_exist():
    """`_NON_CHANNEL_JOBS` is a hand-list, and a hand-list naming something that no longer
    exists silently WIDENS the channel set it is subtracted from -- or, worse, narrows it if a
    real channel is ever added to it by mistake."""
    jobs = _job_names()
    missing = sorted(_NON_CHANNEL_JOBS - jobs)
    assert not missing, f"exempted job(s) that do not exist: {missing}"


def test_every_channel_job_is_aggregated_by_the_report_job():
    """THE guard this file was opened for.

    `report` decides the workflow's verdict from two hand-lists -- the `R_*` env bindings and
    the `for r in ...` word list -- over a third (`needs:`). A channel job present in `needs:`
    and absent from either list runs, fails, and is never looked at: the loop that fails the
    step iterates only over the words it was given, so `report` exits 0 and the summary table
    simply has no row for it. A green post-release run would then mean "every channel I
    remembered to list passed".

    All three ends are derived from the file, so the only way to satisfy this is to wire a new
    channel up properly.
    """
    channels = _job_names() - _NON_CHANNEL_JOBS
    assert channels, "no channel jobs found -- the subtraction removed everything"

    block = _report_block()
    needed = set(re.search(r"needs:\s*\[([^\]]*)\]", block).group(1).replace(" ", "").split(","))

    bound = {m.lower() for m in re.findall(r"R_([A-Z0-9_]+):\s*\$\{\{\s*needs\.", block)}
    loop = re.search(r"for r in ([^\n;]+)", block)
    assert loop, "the `report` job no longer has a `for r in ...` verdict loop"
    iterated = {m.lower() for m in re.findall(r"R_([A-Z0-9_]+)", loop.group(1))}

    assert channels <= needed, (
        f"channel job(s) not in `report`'s needs, so their result is invisible to it: "
        f"{sorted(channels - needed)}")
    assert channels == bound, (
        f"channel job(s) with no R_* binding (never reported): {sorted(channels - bound)}; "
        f"R_* bindings with no such job: {sorted(bound - channels)}")
    assert channels == iterated, (
        f"channel job(s) missing from the verdict loop -- they can FAIL while `report` exits 0: "
        f"{sorted(channels - iterated)}; iterated but not a channel: {sorted(iterated - channels)}")


def test_the_report_job_runs_even_when_a_channel_fails():
    """`always()`, or the aggregate is skipped by the very failure it exists to announce, and
    the run ends with a blank summary rather than a named bad channel."""
    assert re.search(r"if:\s*always\(\)", _report_block()), (
        "`report` must be `if: always()` -- without it a failed channel skips the summary")


def test_the_report_job_fails_when_a_channel_did():
    """A green summary sitting above a red job is worse than no summary: it is read as a pass.
    The workflow's own comment claims this behaviour, so it needs a row that falsifies it."""
    block = _report_block()
    assert "exit 1" in block, (
        "`report` must exit non-zero when a channel failed, or the workflow reports success "
        "over a failed install")


def test_every_interpolation_reaches_the_shell_through_env():
    """A `${{ }}` inlined into a `run:` body is substituted BEFORE the shell parses the line, so
    a value containing a quote executes as code and validating it afterwards is worthless. The
    version is attacker-influenced -- it is a `workflow_dispatch` input -- which is what makes
    this the load-bearing one.
    """
    offenders = []
    for block in re.findall(r"run:\s*\|\s*\n((?:[ \t]+.*\n?)+)", _text()):
        for i, line in enumerate(block.splitlines()):
            if "${{" in line:
                offenders.append(line.strip())
    assert not offenders, (
        "these run: lines interpolate directly instead of going through env: -- "
        f"{offenders}")


def test_every_run_body_sets_pipefail():
    """`bash -e {0}` is the Actions default: `-e` but NOT pipefail, so a piped command reports
    the LAST stage's status and a failed install upstream of a pipe is swallowed. That is this
    workflow's own bug class turned on itself."""
    bodies = re.findall(r"run:\s*\|\s*\n((?:[ \t]+.*\n?)+)", _text())
    assert bodies, "no `run: |` bodies found -- this guard is inert"
    missing = [b.strip().splitlines()[0] for b in bodies
               if "set -euo pipefail" not in b and "bash -eux" not in b]
    assert not missing, f"run bodies without `set -euo pipefail`: {missing}"


def test_the_image_reference_is_lowercased():
    """`github.repository_owner` preserves the account's real casing, and a registry reference
    must be lowercase -- `docker pull` rejects a mixed-case name at PARSE, so the job fails on
    every run and `report` (needs: docker, always()) reds the whole workflow while the image
    channel is never exercised once. That shipped, and was caught only by reading the fold.

    Asserted as the FOLD rather than as a literal owner: hardcoding the name was deliberately
    removed from this file as a drift surface.
    """
    block = _jobs_block()
    assert "tr '[:upper:]' '[:lower:]'" in block, (
        "the ghcr reference must lowercase the owner -- `github.repository_owner` is exact-case "
        "and docker rejects an uppercase repository name at parse")


def test_the_container_jobs_declare_no_sparse_checkout():
    """Neither container image ships `git` (measured), so `actions/checkout` falls back to a
    REST tarball download and a sparse pattern has nothing to act on -- the whole repository
    lands regardless. A setting that reads as doing something and does not is drift, which this
    repo treats as a defect rather than a harmless extra.

    Scoped to the `container:` jobs only: the host-runner jobs keep theirs, where git is present
    and the patterns work.
    """
    block = _jobs_block()
    for job in ("deb", "rpm"):
        start = block.index(f"\n  {job}:\n")
        rest = block[start + 1:]
        end = re.search(r"\n  [A-Za-z_][A-Za-z0-9_-]*:\n", rest)
        body = rest[:end.start()] if end else rest
        assert "container:" in body, (
            f"the {job} job no longer runs in a container -- this guard's premise is gone, and "
            "sparse-checkout may now be honoured after all")
        # The KEY on a non-comment line, not the word anywhere: the first version of this
        # assertion matched the prose in the workflow explaining WHY the key is absent, so it
        # failed on the very state it exists to require. A guard that cannot tell code from a
        # comment about code is not reading the thing it claims to.
        declared = [ln for ln in body.splitlines()
                    if re.match(r"\s*sparse-checkout(-cone-mode)?\s*:", ln)]
        assert not declared, (
            f"the {job} job declares sparse-checkout, but its image ships no git so the pattern "
            f"is inert: {declared}")


def _parsed(path: Path) -> dict:
    """A workflow file as YAML.

    PARSED, not text-matched, for the guards below only -- see the module docstring. YAML 1.1
    reads a bare `on` key as the boolean True, and PyYAML follows it, so the trigger block
    lands under `True` rather than `"on"`. Both are looked for: a quoted `"on":` in the file
    would key it as the string, and a guard that knew only one spelling would report "no
    triggers" and pass every assertion made over an empty mapping.
    """
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), f"{path.name} did not parse as a mapping"
    return doc


def _triggers(path: Path) -> dict:
    doc = _parsed(path)
    for key in (True, "on"):
        if key in doc:
            block = doc[key]
            assert block, f"{path.name}'s `on:` block is empty"
            assert isinstance(block, dict), (
                f"{path.name}'s `on:` is {type(block).__name__}, not a mapping -- the trigger "
                f"guards compare its KEYS, and a scalar would compare as its characters")
            return block
    raise AssertionError(f"{path.name} has no `on:` block at all")


def test_post_release_is_not_triggered_by_the_release_event():
    """THE regression pin, and the one defect this file previously could not see.

    `release: published` fires when release-please CUTS the release, which is its first job --
    every publishing job in release-please.yml declares `needs: release-please` and runs after
    it. So the event arrives at the start of the publish fan-out, and the check raced it: red
    on v2.2.1, v2.3.0 and v2.4.0, every release since the workflow was added, with "no matching
    distribution" from PyPI, 404 for the .deb and .rpm, and "manifest unknown" for the image.

    A check that fails on every release is one nobody reads, so this is not cosmetic: it is the
    difference between the next genuine publish failure being noticed and being assumed to be
    the usual noise.
    """
    triggers = _triggers(POST_RELEASE)
    assert "release" not in triggers, (
        "post-release.yml must not trigger on `release:` -- that event fires when the release "
        "is cut, BEFORE any artefact is published, so every channel check races the publish it "
        "is meant to verify. release-please.yml's `post-release` job dispatches this workflow "
        "once the publishes it names in `needs:` have finished."
    )
    # The COMPLETE set, not a membership probe. `workflow_dispatch` being present says nothing
    # about what else is, and `version` is `required: true`: a `push:` or `schedule:` added
    # alongside would fire with no `inputs.version`, so every such run reds on the version job's
    # regex check -- permanent noise on the one workflow whose entire defect was being noise.
    #
    # `release` is asserted separately ABOVE rather than left to this line. Both are needed: the
    # equality catches every extra trigger, and the specific one names the regression that
    # actually shipped, so the failure message points at the history instead of a set diff.
    #
    # This is the pin `test_testpypi_triggers_only_on_workflow_dispatch` and
    # `test_the_homebrew_dry_run_triggers_only_on_workflow_dispatch` already carry for the other
    # two dispatch-only workflows. Parsed rather than regex-bounded, which is why this one needs
    # none of their indent gymnastics -- see their docstrings for what those work around.
    assert set(triggers) == {"workflow_dispatch"}, (
        f"post-release.yml must trigger on `workflow_dispatch` and NOTHING else -- it is how "
        f"release-please.yml starts it, and the only way to re-run the check against an "
        f"already-published version. Found: {sorted(triggers)}"
    )


def _dispatcher() -> dict:
    """release-please.yml's `post-release` job."""
    jobs = _parsed(RELEASE_PLEASE)["jobs"]
    assert "post-release" in jobs, (
        "release-please.yml has no `post-release` job, so nothing dispatches the install check "
        "and it never runs at all -- post-release.yml's only trigger is workflow_dispatch"
    )
    return jobs["post-release"]


def test_every_channel_names_its_producing_job():
    """SCOPE for the guard below. `_CHANNEL_PRODUCER` is a hand-list, and a hand-list that has
    fallen behind the channel set silently shrinks what the next test checks: a channel with no
    entry contributes no producer, so nothing requires the dispatcher to wait for it. Requiring
    the mapping to cover the channels EXACTLY is what makes adding a channel force the choice.
    """
    channels = _job_names() - _NON_CHANNEL_JOBS
    assert channels, "no channel jobs found -- the subtraction removed everything"
    assert set(_CHANNEL_PRODUCER) == channels, (
        f"every channel job must name the release-please.yml job that publishes what it "
        f"installs. Channels with no producer named: {sorted(channels - set(_CHANNEL_PRODUCER))}; "
        f"named here but not a channel: {sorted(set(_CHANNEL_PRODUCER) - channels)}"
    )


def test_the_dispatcher_waits_for_every_producing_job():
    """The ordering itself, derived from BOTH ends rather than restated.

    The channel set comes from post-release.yml, its producers from `_CHANNEL_PRODUCER`, and
    `needs:` from release-please.yml. A channel whose producer is missing from `needs:` is a
    check that can start before its artefact exists -- which is the shipped defect, one channel
    at a time instead of all four.
    """
    needs = _dispatcher().get("needs") or []
    assert isinstance(needs, list), f"post-release's `needs:` must be a list, got {needs!r}"
    # EQUALITY, not a subset, and the two holes a subset leaves are both silent.
    #
    # `release-please` is not a channel producer, so a subset over `_CHANNEL_PRODUCER.values()`
    # never requires it -- yet this job's own `if:` reads
    # `needs.release-please.outputs.release_created`. Drop it from `needs:` and that expression
    # resolves against a job this one no longer depends on: it comes back empty, the condition
    # is false, and the dispatch NEVER FIRES. The install check would stop running with nothing
    # red anywhere, which is this workflow's original defect wearing a different hat.
    #
    # An EXTRA entry is not free either: if the added job is skipped, `post-release` is skipped
    # with it, for the same silent result. So `homebrew`'s absence is part of the claim rather
    # than an accident -- the job's own comment says why the check does not wait on a channel it
    # never exercises.
    expected = {"release-please"} | set(_CHANNEL_PRODUCER.values())
    assert set(needs) == expected, (
        f"release-please.yml's `post-release` job must declare exactly {sorted(expected)}. A "
        f"missing entry lets the check start before that artefact is published; an extra one can "
        f"skip the job entirely. Channel -> producer: {_CHANNEL_PRODUCER}; needs: {needs}"
    )


def test_the_dispatcher_only_fires_when_a_release_was_actually_created():
    """Every push to main runs release-please.yml. Without this gate the dispatch fires on
    pushes that released nothing, starting an install check against whatever version was last
    released -- which passes, and trains the reader that a green check means nothing.

    EQUALITY, not a substring probe over `release_created`. Two rewrites both CONTAIN that
    substring and both break the guarantee this workflow exists to give:

    - `success() || needs...release_created == 'true'` dispatches whenever the left arm holds,
      so the release gate stops binding at all.
    - `always() && needs...release_created == 'true'` dispatches even when a PRODUCER job
      FAILED. That is the original defect by another route -- the check runs against artefacts
      that were never published, and reds for a reason that has nothing to do with the code it
      is checking.

    So both halves are load-bearing and both are pinned. The literal is restated rather than
    derived because there is nothing here to derive it from: it IS the property under test.
    """
    expected = "success() && needs.release-please.outputs.release_created == 'true'"
    condition = (_dispatcher().get("if") or "").strip()
    assert condition == expected, (
        f"post-release's `if:` must be exactly {expected!r}. The `success() &&` half is what "
        f"stops it dispatching after a failed publish; the `release_created` half is what stops "
        f"it dispatching on a push that released nothing. Got: {condition!r}"
    )
