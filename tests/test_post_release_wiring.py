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
any artefact is published. It was red on every release up to #231 -- deliberately not
enumerated, since the list that stood here went stale as soon as another was cut -- and nothing
here could see it, because nothing here read release-please.yml. The
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
import os
import re
import subprocess
from pathlib import Path

import pytest
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


def _run_bodies() -> list[str]:
    """Every `run:` script in the workflow, taken from the PARSED document.

    NOT a regex over the text, and that is a correction rather than a preference. The pattern
    this replaced required at least one space or tab on every line of a body, so it could not
    match a BLANK line -- a body containing one was silently truncated there and everything
    after it left both sweeps below. Nothing went red; the sweeps simply saw less.

    Measured when the pypi step first grew blank lines: its body swept 9 of 66 lines, and an
    inlined interpolation placed past the blank line -- in the one step whose input is
    attacker-influenced -- passed the injection guard. The same shape in the deb body hid an
    inlined interpolation from this file AND from every other workflow test in the repo. A YAML
    block scalar carries its blank lines by definition, so a parse cannot reproduce that class.
    """
    doc = _parsed(POST_RELEASE)
    bodies = [step["run"]
              for job in doc["jobs"].values()
              for step in job.get("steps", [])
              if "run" in step]
    # SCOPE, derived from the raw text so it is an INDEPENDENT count rather than a restatement
    # of the parse: the file's `run:` keys and the scripts collected must agree. A collection
    # that quietly returned fewer would make every sweep below pass over a subset, which is
    # exactly the failure being corrected here, one level up.
    declared = len(re.findall(r"^\s+run:", _text(), re.M))
    assert len(bodies) == declared, (
        f"parsed {len(bodies)} run bodies but the file declares {declared} `run:` keys -- the "
        f"sweeps below would inspect a subset and pass")
    assert bodies, "no run bodies found -- every sweep over them is inert"
    return bodies


def test_the_run_body_sweep_sees_whole_bodies_including_blank_lines():
    """The anti-vacuity check for `_run_bodies` itself, pinned against the truncation above.

    The sweeps it feeds are NEGATIVE -- finding nothing is their success case -- so a truncated
    body satisfies them silently. This asserts a body containing a blank line is carried through
    WHOLE, keyed on content that sits after the blank lines rather than on a line count, which
    would be its own drift surface.
    """
    bodies = _run_bodies()
    pypi = [b for b in bodies if "job-sluice==" in b]
    assert len(pypi) == 1, f"expected one body installing the artefact, got {len(pypi)}"
    body = pypi[0]
    assert "" in body.splitlines(), (
        "the pypi body no longer contains a blank line, so this guard has stopped exercising "
        "the truncation class it exists for -- check whether a regex sweep was reintroduced")
    assert "--channel" in body, (
        "the pypi body does not reach its smoke invocation, so it is being truncated again")


def test_every_interpolation_reaches_the_shell_through_env():
    """A `${{ }}` inlined into a `run:` body is substituted BEFORE the shell parses the line, so
    a value containing a quote executes as code and validating it afterwards is worthless. The
    version is attacker-influenced -- it is a `workflow_dispatch` input -- which is what makes
    this the load-bearing one.
    """
    offenders = [line.strip()
                 for body in _run_bodies()
                 for line in body.splitlines()
                 if "${{" in line]
    assert not offenders, (
        "these run: lines interpolate directly instead of going through env: -- "
        f"{offenders}")


def test_every_run_body_sets_pipefail():
    """`bash -e {0}` is the Actions default: `-e` but NOT pipefail, so a piped command reports
    the LAST stage's status and a failed install upstream of a pipe is swallowed. That is this
    workflow's own bug class turned on itself."""
    missing = [b.strip().splitlines()[0] for b in _run_bodies()
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
    on every release up to #231 (not enumerated: such a list goes stale), with "no matching
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


# --------------------------------------------------------------------------------------
# The PyPI install retry (#231 follow-up), EXECUTED rather than read.
#
# The check had never passed: 5 runs, 0 successes. #231 fixed WHICH EVENT triggers it, and
# the remaining cause is different -- PyPI's simple index is eventually consistent across CDN
# nodes, so exactly one matrix cell per release got "No matching distribution" for a version
# that had uploaded minutes earlier, a different cell each time (v2.4.1: `3.14, wheel`;
# v2.4.2: `3.13, sdist`).
#
# WHY THESE GUARDS RUN THE SCRIPT INSTEAD OF MATCHING ITS TEXT. This workflow's first real
# execution of any change is against a version that is already public -- the module docstring
# above says so, and it is the reason this file exists. A text match can see that the word
# `sleep` appears; it cannot see that the loop terminates, that it fails CLOSED when the
# retries run out, or that it stops retrying once pip succeeds. Those are the three ways this
# fix goes wrong, and the third-worst outcome here is a loop that exhausts and falls through
# to the smoke run, turning a genuine publish failure green -- strictly worse than the flake
# it absorbs. So the real `run:` body is extracted and executed against a stub `pip`.
# --------------------------------------------------------------------------------------

_PYPI_STEP = "Install from PyPI and smoke it"

# Stub shells. `python` only has to answer `-m venv <dir>`, and it COPIES the two inner stubs
# rather than writing them from a nested heredoc, which is unreadable and easy to get subtly
# wrong. Nothing here is `set -e`: a stub that dies on its own first failing test would be
# indistinguishable from the behaviour under test.
_STUB_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$STUB_DIR/pip_stub" "$3/bin/pip"
  cp "$STUB_DIR/python_stub" "$3/bin/python"
  chmod +x "$3/bin/pip" "$3/bin/python"
fi
exit 0
"""

# Succeeds for `--upgrade pip` (not the artefact under test), and otherwise logs its full argv
# and fails until the configured attempt number -- which is how "the index caught up" is
# simulated without a network.
_STUB_PIP = """#!/bin/sh
for a in "$@"; do
  [ "$a" = "--upgrade" ] && exit 0
done
printf '%s\\n' "$*" >> "$STUB_PIP_LOG"
n=$(wc -l < "$STUB_PIP_LOG" | tr -d ' ')
if [ -n "${STUB_PIP_SUCCEED_AT:-}" ] && [ "$n" -ge "$STUB_PIP_SUCCEED_AT" ]; then
  exit 0
fi
exit 1
"""

_STUB_VENV_PYTHON = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_SMOKE_LOG"
exit 0
"""

_STUB_SLEEP = """#!/bin/sh
printf '%s\\n' "$1" >> "$STUB_SLEEP_LOG"
exit 0
"""


def _pypi_install_script() -> str:
    """The `run:` body of the pypi job's install step, taken from the PARSED workflow.

    Parsed rather than text-sliced because the guards below EXECUTE what this returns: a slice
    that silently caught the wrong step, or half of one, would run something the workflow does
    not run and certify it green. The count assertion is the anti-vacuity check -- a renamed
    step would otherwise yield an empty list and every assertion made over it would hold.
    """
    steps = _parsed(POST_RELEASE)["jobs"]["pypi"]["steps"]
    matching = [s for s in steps if s.get("name") == _PYPI_STEP]
    assert len(matching) == 1, (
        f"expected exactly one pypi step named {_PYPI_STEP!r}, found {len(matching)} -- "
        f"steps present: {[s.get('name') for s in steps]}")
    run = matching[0]["run"]
    assert "${{" not in run, (
        "the pypi install step interpolates directly, so this script cannot be executed as-is "
        "-- and test_every_interpolation_reaches_the_shell_through_env should already be red")
    return run


def _declared_attempts(script: str) -> int:
    """The retry bound, read from the script that is about to run rather than hardcoded here.

    Hardcoding it would make the count assertions below agree with this file instead of with
    the workflow, which is the drift shape this repo keeps finding in its own prose.
    """
    m = re.search(r"^\s*attempts=(\d+)\s*$", script, re.M)
    assert m, "the pypi install step declares no `attempts=` bound -- the retry is unbounded"
    n = int(m.group(1))
    assert 2 <= n <= 10, f"attempts={n} is not a sane bound for a CDN propagation retry"
    return n


def _run_pypi_install(tmp_path, *, form: str, succeed_at: int | None):
    """Execute the real install step with `pip`, `python` and `sleep` stubbed out.

    `succeed_at` is the attempt on which the stub pip starts succeeding; None means it never
    does, which is the genuinely-broken-publish case.
    """
    script = _pypi_install_script()
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    # The step resolves `$PWD/scripts/smoke_installed.py` BEFORE it cd's to the work dir, so
    # this has to exist or the smoke invocation is not what the workflow's would be.
    (root / "scripts" / "smoke_installed.py").write_text("# stub\n")

    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("python", _STUB_PYTHON), ("sleep", _STUB_SLEEP),
                       ("pip_stub", _STUB_PIP), ("python_stub", _STUB_VENV_PYTHON)):
        p = stub / name
        p.write_text(body)
        p.chmod(0o755)

    logs = {k: tmp_path / f"{k}.log" for k in ("pip", "sleep", "smoke")}
    for p in logs.values():
        p.write_text("")

    work = tmp_path / "tmp"
    work.mkdir()
    env = {
        "PATH": f"{stub}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        # `mktemp -d` lands under here, keeping the run inside pytest's tmp_path.
        "TMPDIR": str(work),
        "VERSION": "9.9.9",
        "FORM": form,
        "PY": "3.13",
        "STUB_DIR": str(stub),
        "STUB_PIP_LOG": str(logs["pip"]),
        "STUB_SLEEP_LOG": str(logs["sleep"]),
        "STUB_SMOKE_LOG": str(logs["smoke"]),
    }
    if succeed_at is not None:
        env["STUB_PIP_SUCCEED_AT"] = str(succeed_at)

    path = tmp_path / "step.sh"
    path.write_text(script)
    proc = subprocess.run(["bash", str(path)], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)
    reads = {k: [ln for ln in p.read_text().splitlines() if ln] for k, p in logs.items()}
    return proc, reads


@pytest.mark.parametrize("form", ["wheel", "sdist"])
def test_the_pypi_install_retries_the_declared_number_of_times_and_fails_closed(tmp_path, form):
    """A publish that is genuinely broken must still go red, and must not reach the smoke run.

    This is the assertion the whole retry hangs on. A loop that exhausts its attempts and falls
    through would turn a real "this version does not install" into a green tick -- the exact
    outcome #218 built this workflow to prevent, reintroduced by the fix for its flakiness.

    Both forms, because the sdist branch is the one that failed on v2.4.2 and it takes a
    different code path through the argument setup.
    """
    script = _pypi_install_script()
    attempts = _declared_attempts(script)
    proc, logs = _run_pypi_install(tmp_path, form=form, succeed_at=None)

    assert proc.returncode != 0, (
        f"pip never succeeded, yet the step exited 0 -- a broken publish would report green\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    assert len(logs["pip"]) == attempts, (
        f"expected {attempts} install attempts (the script's own `attempts=`), got "
        f"{len(logs['pip'])}: {logs['pip']}")
    assert not logs["smoke"], (
        "the smoke script ran even though the install never succeeded -- the loop falls "
        f"through instead of failing closed: {logs['smoke']}")
    assert "::error::" in proc.stdout, (
        f"an exhausted retry must annotate why it gave up; stdout was:\n{proc.stdout}")
    # The VERSION UNDER TEST must reach pip on every attempt. The harness sets VERSION=9.9.9, so
    # this pins the pin without naming a real release: a hardcoded version in the workflow would
    # make the check verify some other release and nothing else here would notice.
    assert all("job-sluice==9.9.9" in ln for ln in logs["pip"]), (
        f"an attempt did not install the version under test: {logs['pip']}")
    # Each form must PIN ITS FORM -- the retry is shared, so this is where a flag gets dropped.
    # The wheel arm matters most: a bare `pip install` falls back to building the sdist, and a
    # partially propagated index is exactly when the sdist is there and the wheel is not, so the
    # wheel cell could pass having never installed a wheel.
    if form == "sdist":
        assert all("--no-binary :all:" in ln for ln in logs["pip"]), (
            f"the sdist form lost `--no-binary :all:` in the retry: {logs['pip']}")
    else:
        assert all("--only-binary :all:" in ln for ln in logs["pip"]), (
            f"the wheel form must REFUSE an sdist fallback, or it certifies a channel it never "
            f"exercised: {logs['pip']}")


def test_the_pypi_install_stops_and_reports_once_the_index_catches_up(tmp_path):
    """The recovery case: a late-arriving index must produce a PASS, and say that it was late.

    The warning annotation is the only telemetry that can distinguish "the retry earned its
    place" from "the flake did not recur today" -- without it, a green run after this change
    is evidence of nothing. It also has to STOP: a loop that keeps going after a success would
    reinstall over a working install and hide how long propagation took.
    """
    proc, logs = _run_pypi_install(tmp_path, form="wheel", succeed_at=3)

    assert proc.returncode == 0, (
        f"pip succeeded on attempt 3 but the step failed\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    assert len(logs["pip"]) == 3, (
        f"the loop should stop on the first success, not keep retrying: {logs['pip']}")
    assert len(logs["smoke"]) == 1, (
        f"the smoke script should run exactly once after a successful install: {logs['smoke']}")
    # Extracted and asserted as ONE object. `"attempt 3" in proc.stdout` was satisfied by the
    # separate success echo, so stripping ${n} from the annotation left this green -- asserting a
    # property of one object by looking for its text anywhere in a larger blob.
    warn = [ln for ln in proc.stdout.splitlines() if "::warning::" in ln]
    assert len(warn) == 1, f"expected exactly one warning annotation, got {warn}"
    assert "attempt 3" in warn[0], (
        f"the annotation must name the attempt it succeeded on, or it cannot say how late the "
        f"index was: {warn[0]}")
    # T6 (argv, not just a count): the smoke run must receive the version under test and the
    # channel label, or a green tick certifies a check that ran against something else.
    assert "9.9.9" in logs["smoke"][0] and "pypi-wheel-py3.13" in logs["smoke"][0], (
        f"the smoke run did not receive the version and channel: {logs['smoke'][0]}")


def test_a_first_attempt_install_neither_sleeps_nor_warns(tmp_path):
    """The path that runs on essentially every release, and the one that makes the annotation
    MEAN something.

    The warning is only telemetry if it fires ONLY when the index actually lagged. Measured: with
    no test for this path, DELETING the `if [ "$n" -gt 1 ]` suppression around the annotation left
    every other guard in this file green -- and an annotation on every clean release says nothing
    at all, which is precisely the claim the change's rationale rests on. A mechanism asserted in
    prose with no row that falsifies it is the shape `CLAUDE.md` names first.

    It also pins that the ordinary path pays NO delay: a retry that sleeps before its first
    attempt would add its whole backoff to every release for nothing.
    """
    proc, logs = _run_pypi_install(tmp_path, form="wheel", succeed_at=1)

    assert proc.returncode == 0, f"a first-attempt install failed\nstdout:\n{proc.stdout}"
    assert len(logs["pip"]) == 1, f"expected a single attempt, got {logs['pip']}"
    assert logs["sleep"] == [], (
        f"the ordinary path paid a delay it did not need: {logs['sleep']}")
    assert len(logs["smoke"]) == 1, f"the smoke script must still run: {logs['smoke']}"
    assert "::warning::" not in proc.stdout, (
        f"the annotation fired on a clean first-attempt install, so it can no longer distinguish "
        f"a real propagation lag from an ordinary release:\n{proc.stdout}")


def test_every_pypi_install_attempt_bypasses_pips_http_cache(tmp_path):
    """`--no-cache-dir` on every attempt, because WITHOUT IT THE RETRY IS INERT.

    pip caches the simple index page on disk and serves it WITHOUT revalidating. Measured: with
    a populated cache and the network made unreachable, `pip install job-sluice==2.4.1`
    resolved entirely from cache and never requested `/simple/job-sluice/`, while the same
    command with `--no-cache-dir` failed at exactly that URL. So a retry reusing the cache
    re-reads the SAME stale index it just failed on and fails identically, attempt after
    attempt -- a retry loop that runs, logs, backs off and cannot possibly succeed.

    Asserted over EVERY logged attempt rather than over the script text: the flag mattering on
    the retries is the whole point, and a text match cannot tell which invocation carries it.
    """
    proc, logs = _run_pypi_install(tmp_path, form="wheel", succeed_at=None)
    assert logs["pip"], "no install attempts were logged -- this guard is inert"
    missing = [ln for ln in logs["pip"] if "--no-cache-dir" not in ln]
    assert not missing, (
        f"these install attempts would re-read pip's cached (stale) index: {missing}")


def test_the_retry_backs_off_and_its_total_wait_is_bounded(tmp_path):
    """Backoff, not a fixed poll, and a ceiling on the whole thing.

    A bounded retry that waits half an hour is a blanket sleep with extra steps: it delays the
    verdict on a genuinely broken release and burns a runner doing it. Measured from the sleeps
    the script actually asks for, not from reading the arithmetic.
    """
    script = _pypi_install_script()
    attempts = _declared_attempts(script)
    _proc, logs = _run_pypi_install(tmp_path, form="wheel", succeed_at=None)

    waits = [int(w) for w in logs["sleep"]]
    assert len(waits) == attempts - 1, (
        f"expected a wait between each pair of attempts ({attempts - 1}), got {waits}")
    assert all(b > a for a, b in zip(waits, waits[1:])), (
        f"the delay must grow between attempts, otherwise this is a fixed poll: {waits}")
    assert sum(waits) <= 900, (
        f"total backoff of {sum(waits)}s delays the verdict on a broken release too long")


def test_only_the_pypi_job_retries_its_install():
    """Scope. The deb, rpm and docker jobs fetch from GitHub releases and ghcr, which are not
    the CDN-fronted eventually-consistent index this retry exists for, and none of them has
    ever failed this way. A retry there would mask a real 404 or a missing image -- the failure
    those jobs are for.

    ASSERTED ON THE SCOPE, not only on the violations. Finding nothing is this guard's SUCCESS
    case, so a body slice that came out empty would satisfy every assertion below while
    inspecting nothing at all -- the failure shape `CLAUDE.md` names first. Each sliced body is
    therefore required to still contain the fetch that job exists to perform.
    """
    block = _jobs_block()
    # What each job fetches, so a body that no longer contains it is reported as a bad slice
    # rather than passing as a clean one.
    fetches = {"deb": "curl", "rpm": "curl", "docker": "docker pull"}
    # DERIVED, not hand-listed. Keyed only on the three names above, a new channel job would sit
    # outside this guard entirely and nothing would say so -- the shape this file's own docstring
    # warns about for the `report` job's two hand-lists. `_job_names()` and `_NON_CHANNEL_JOBS`
    # already exist for exactly this.
    expected = _job_names() - _NON_CHANNEL_JOBS - {"pypi"}
    assert set(fetches) == expected, (
        f"the channel roster this guard sweeps ({sorted(fetches)}) has drifted from the "
        f"workflow's own ({sorted(expected)}) -- add the new job's fetch here")
    for job, fetch in fetches.items():
        start = block.index(f"\n  {job}:\n")
        rest = block[start + 1:]
        end = re.search(r"\n  [A-Za-z_][A-Za-z0-9_-]*:\n", rest)
        body = rest[:end.start()] if end else rest
        assert fetch in body, (
            f"the {job} job body sliced out of the workflow does not contain {fetch!r}, so this "
            f"guard is inspecting the wrong text (or none) and would pass on anything")
        # `re.search`, and a wider vocabulary than the two shapes first written here. Measured
        # green against the old line-anchored `sleep |for n in` pair: `curl --retry-all-errors`
        # (a curl-native retry, the most idiomatic form in a curl-based job, and one that retries
        # a 404 under `-f`), and an `until` loop whose `sleep` is not the first token on its line.
        retrying = [ln for ln in body.splitlines()
                    if not ln.lstrip().startswith("#")
                    and re.search(r"\bsleep\s+\d|\b(until|while)\s|\bfor\s+\w+\s+in\b"
                                  r"|--retry\b", ln)]
        assert not retrying, (
            f"the {job} job has grown a retry, which would mask the publish failure it exists "
            f"to catch: {retrying}")


def test_the_artefact_is_installed_by_exactly_one_command():
    """One install invocation, shared by both forms.

    The pre-#231 step had two -- an `if sdist / else` with a full `pip install` in each arm --
    and that shape is how a retry gets added to one branch and not the other. Exactly one cell
    per release failed, so a fix applied to the wrong arm would look indistinguishable from a
    fix that worked until the next release picked the other one.
    """
    script = _pypi_install_script()
    installs = re.findall(r"pip install[^\n]*job-sluice==", script)
    assert len(installs) == 1, (
        f"expected exactly one `pip install ... job-sluice==` in the step so that wheel and "
        f"sdist cannot drift apart, found {len(installs)}: {installs}")
