"""`post-release.yml`'s wiring, pinned the way `ci.yml`'s and the release workflows' already are.

This workflow is the artefact PR #218 is named for, and until this file existed
`grep -rn post-release tests/*.py` returned exactly one hit -- a docstring -- against 33 tests
for `ci.yml` and 97 for the release/testpypi/homebrew workflows. That gap matters more here than
the line count suggests, because this workflow CANNOT be exercised before it merges: it triggers
on `release: published`, so its first real execution is after a tag is public. Everything a test
can establish beforehand is the only pre-merge evidence there is.

WHY TEXT, NOT A YAML PARSE. The same reason `tests/test_ci_wiring.py` gives: most of what these
guards pin is a command STRING or an interpolation shape, which text matching pins exactly and a
parse would only make harder to read. Where a guard needs YAML's own semantics it should parse
instead -- none here does.

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

ROOT = Path(__file__).parent.parent
POST_RELEASE = ROOT / ".github" / "workflows" / "post-release.yml"

# Jobs that are not themselves an install channel: `version` resolves which release to check,
# `report` is the aggregator doing the checking. Everything else IS a channel and must be
# aggregated. Named rather than inferred so that adding a non-channel helper job is a
# deliberate edit here, and `test_the_non_channel_jobs_all_exist` keeps this honest.
_NON_CHANNEL_JOBS = {"version", "report"}


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
    version is attacker-influenced on both triggers (`release: published` carries the tag, and
    `workflow_dispatch` takes it as an input), which is what makes this the load-bearing one.
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
