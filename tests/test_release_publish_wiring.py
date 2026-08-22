"""Wiring pins for #104's PyPI publishing channel, across BOTH release workflow files:

- `.github/workflows/release-please.yml` (PR 2): the `build`/`attest`/`pypi`/`release-assets`
  jobs, gated on release-please's own `release_created` output, plus the `sha`/`tag_name`
  outputs those jobs consume.
- `.github/workflows/testpypi.yml` (PR 3): the dispatch-triggered TestPyPI dry run -- a
  separate file rather than a job in the first, since it builds from whatever ref it was
  dispatched from rather than release-please's tagged sha, and publishes to a different index.

EVERY module-level helper here takes the target file's `Path` as its FIRST, REQUIRED argument
-- required rather than defaulted to either file, because the two workflows' workflow-wide
`permissions:` blocks are byte-identical, so a defaulted/forgotten argument would silently read
the wrong file and still pass. Deliberately no count and no roster: this sentence used to name
five helpers by hand, and had gone stale two commits later in this same branch (seven, then
nine) without anything going red -- the standing hazard with a number in prose.

Text-matching, not a YAML parse -- because what is pinned here is command strings, action
pins and permission blocks, which text matching pins exactly. NOT because pyyaml is unavailable:
it is a hard runtime dependency (`pyproject.toml`'s `dependencies`), and an earlier version of
this line claimed otherwise. See tests/test_ci_wiring.py's docstring for when to parse instead.
Mirrors tests/test_ci_wiring.py's own idiom (_job_directives/_step_containing, comment-stripped)
rather than importing it -- file-scoped helpers, matching that file's own convention, for two
small functions that don't warrant cross-file coupling.

See docs/superpowers/specs/2026-08-10-publish-workflow-skeleton-design.md (PR 2: why
release-please needed a job output added, why the gate is a string comparison not bare
truthiness, why the top-level-permissions check is position-anchored on `jobs:` rather than
"the first two-space key") and docs/superpowers/specs/2026-08-21-pypi-channel-design.md (PR 3:
the TestPyPI dry run's own design -- the branch guard, the version stamp, the drift pin) for the
full design reasoning.
"""
import inspect
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
RELEASE_PLEASE = ROOT / ".github" / "workflows" / "release-please.yml"
TESTPYPI = ROOT / ".github" / "workflows" / "testpypi.yml"


def _text(path: Path) -> str:
    return path.read_text()


def _job_directives(path: Path, name: str) -> str:
    """One job's YAML, sliced out by indentation, comment-stripped.

    A job key is the only thing at two-space indent; steps and job-level keys (permissions,
    outputs) sit at four or more, so the next two-space key ends the block. Comment-stripped so
    a substring test can't be satisfied by prose EXPLAINING a value rather than the value itself
    -- tests/test_ci_wiring.py's own `_job_directives` docstring records this bug having fired
    once already in this exact repo's workflow files.

    The end-boundary class is `[A-Za-z_]`, not `[a-z]`, and that is the same one-character fix
    `_job_names` carries for the same reason: a job id is a USER-CHOSEN identifier, and GitHub
    accepts `Sneaky:` and `_sneaky:` as readily as `sneaky:`. Under the narrow class such a job
    does not TERMINATE the preceding block -- it is absorbed into it, so the block this returns
    silently spans two jobs and every equality pinned on it reads the wrong text. Contrast
    `_permissions_block`'s inner boundary, deliberately left `[a-z]`: the keys IT bounds on
    (`steps:`, `outputs:`, `runs-on:`, ...) are GitHub's own schema names, fixed lowercase and
    not open to a future author's choice, so widening there would buy nothing.
    """
    text = _text(path)
    start = text.index(f"\n  {name}:\n")
    rest = text[start + 1 :]
    end = re.search(r"\n  [A-Za-z_][\w-]*:\n", rest)
    block = rest[: end.start()] if end else rest
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def _step_containing(path: Path, job: str, needle: str) -> str:
    """The ONE step of `job` whose body contains `needle`, comment-stripped.

    Requires EXACTLY one match: zero means the sweep found nothing and every assertion over it
    would be vacuous; two makes it ambiguous which step is being pinned -- e.g. a future
    `id: release-summary` step must never be silently accepted as a match for `id: release`.
    """
    block = _job_directives(path, job)
    marker = "\n    steps:\n"
    assert marker in block, f"the {job!r} job has no `steps:` key; nothing here can be a step"
    parts = re.split(r"\n(?=      - )", block[block.index(marker) + len(marker) :])
    matches = [part for part in parts if needle in part]
    assert len(matches) == 1, (
        f"expected exactly one step in the {job!r} job containing {needle!r}, found "
        f"{len(matches)}."
    )
    return matches[0]


def _job_names(path: Path) -> list[str]:
    """Every job key in `path`, in file order, comment-stripped.

    Sliced from the literal `\\njobs:\\n` marker -- the same unique anchor
    `_workflow_wide_directives` bounds ON rather than "the first top-level key", for the
    reason that helper's docstring gives -- and matched at EXACTLY two-space indent, the depth
    only a job key sits at. Job-level keys (`permissions:`, `outputs:`, `steps:`) sit at four,
    steps at six, and a block scalar's body deeper still.

    The leading class is `[A-Za-z_]`, not `[a-z]`, and the one character is the whole point.
    A GitHub Actions job id may start with a letter of EITHER case or with `_`, so the narrow
    class this had could not see `Sneaky:` or `_sneaky:` -- and a roster that cannot see a job
    cannot report it as unexpected. Measured: the round-1 mutant this roster exists to catch (a
    job carrying `contents: write` plus `packages: write` appended to either file) survived
    verbatim under those two spellings, one character away from the one it does catch. A
    roster's failure mode is silence, so its matcher must be at least as permissive as the
    thing it enumerates.
    """
    text = _text(path)
    body = text[text.index("\njobs:\n") + len("\njobs:\n"):]
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    return re.findall(r"^  ([A-Za-z_][\w-]*):$", body, re.MULTILINE)


# The SCOPE half of every permissions guard in this file. Each of those guards is keyed on a
# job NAME it already knows, and a sweep keyed on names it knows cannot see a name it does
# not: measured before these two pins existed, a whole extra job carrying `contents: write`
# and `packages: write` appended to EITHER file left this entire module green. Pinning the
# roster is what makes the per-job equality pins exhaustive in combination rather than merely
# individually correct.
_RELEASE_PLEASE_JOBS = ["release-please", "build", "attest", "pypi", "release-assets",
                        "docker", "attest-image"]
_TESTPYPI_JOBS = ["testpypi"]

_ROSTER_MESSAGE = (
    "Every other permissions guard in this file is keyed on a job NAME, so a job absent from "
    "this roster is one nothing in the suite has ever looked at -- its `permissions:` block "
    "included. Add the job HERE and give it its own equality pin; extending this list alone "
    "restores the blind spot it exists to close."
)


def _roster_failure(path: Path, expected: list[str], found: list[str]) -> str:
    return (
        f"{path.name}'s job roster is {found}, expected {expected}. Unexpected: "
        f"{sorted(set(found) - set(expected))}; missing: {sorted(set(expected) - set(found))}. "
        f"{_ROSTER_MESSAGE}"
    )


def test_release_please_declares_exactly_the_jobs_this_file_pins():
    found = _job_names(RELEASE_PLEASE)
    assert found == _RELEASE_PLEASE_JOBS, _roster_failure(
        RELEASE_PLEASE, _RELEASE_PLEASE_JOBS, found)


def test_testpypi_declares_exactly_the_jobs_this_file_pins():
    found = _job_names(TESTPYPI)
    assert found == _TESTPYPI_JOBS, _roster_failure(TESTPYPI, _TESTPYPI_JOBS, found)


def test_release_please_job_exposes_the_release_created_output():
    """Without this, every `needs.release-please.outputs.release_created` reference the build/
    attest jobs use resolves to an empty string, `== 'true'` is always false, and neither job
    ever runs on a real release -- silently."""
    step = _step_containing(RELEASE_PLEASE, "release-please", "googleapis/release-please-action")
    assert re.search(r"^\s*id:\s*release\s*$", step, re.MULTILINE), (
        "the release-please-action step no longer carries `id: release` -- nothing can "
        "reference its output"
    )
    block = _job_directives(RELEASE_PLEASE, "release-please")
    assert "outputs:" in block, "the release-please job has no job-level `outputs:` key"
    assert "release_created: ${{ steps.release.outputs.release_created }}" in block, (
        "the release-please job's `outputs:` block no longer names `release_created` sourced "
        "from the `release` step"
    )


def _permissions_block(path: Path, job: str) -> str:
    """The exact `permissions:` mapping of `job`, trailing-comment-stripped, bounded by the
    next same-indent key.

    An `in`/`not in` probe over individual permission names lets an unnamed THIRD
    permission (e.g. `packages: write`) slip in unnoticed -- the same gap
    `test_workflow_wide_permissions_stay_read_only` closes for the workflow-wide block,
    applied here one level deeper: a job's `permissions:` isn't necessarily the last key in
    its block (`steps:`/`outputs:` can follow), so the boundary must be found, not assumed.
    Trailing inline comments (e.g. `id-token: write  # mints ...`) are stripped here,
    narrowly: `_job_directives`'s own comment-stripping only removes FULL-LINE comments, and
    every permission value in this file is a bare identifier that can never itself contain
    `#`, so a blanket `# to end-of-line` strip is safe in this one context without needing to
    touch that shared, locked helper.
    """
    block = _job_directives(path, job)
    match = re.search(r"\n( +)permissions:\n", block)
    assert match, f"the {job!r} job has no `permissions:` key"
    indent = match.group(1)
    start = match.end()
    end = re.search(rf"\n{indent}[a-z][\w-]*:\n", block[start:])
    body = block[start : start + end.start()] if end else block[start:]
    body = "\n".join(re.sub(r"\s*#.*$", "", ln) for ln in body.splitlines())
    return f"{indent}permissions:\n{body}"


def test_release_please_job_keeps_its_original_permissions():
    """A future edit accidentally copying attest's elevated permissions onto release-please --
    the job that mints the App token -- would go unnoticed without this."""
    assert _permissions_block(RELEASE_PLEASE, "release-please") == (
        "    permissions:\n      contents: read"
    ), (
        "release-please's permissions must be EXACTLY `contents: read` -- an unnamed "
        "elevated permission here would pass a probe over just id-token/attestations "
        "undetected"
    )


def test_release_please_job_exposes_the_release_sha_output():
    block = _job_directives(RELEASE_PLEASE, "release-please")
    assert "sha: ${{ steps.release.outputs.sha }}" in block, (
        "the release-please job's outputs no longer expose `sha` -- build would then check "
        "out github.sha (the commit that triggered THIS run) rather than the commit "
        "release-please actually tagged, which can diverge if a prior run failed after "
        "release-please tagged but before build/attest ran"
    )


def test_build_checks_out_the_tagged_sha_not_the_trigger_commit():
    step = _step_containing(RELEASE_PLEASE, "build", "actions/checkout")
    assert "ref: ${{ needs.release-please.outputs.sha }}" in step, (
        "build's checkout no longer pins ref: to release-please's own sha output -- it would "
        "silently fall back to github.sha, which is the commit that triggered this run and "
        "can be a DIFFERENT commit than the one release-please just tagged"
    )


def test_build_job_depends_on_release_please():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert re.search(r"^\s*needs:\s*release-please\s*$", block, re.MULTILINE), (
        "build's needs: is no longer exactly release-please"
    )


def test_build_job_is_gated_on_release_created():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert "if: needs.release-please.outputs.release_created == 'true'" in block, (
        "build no longer gates on release-please's release_created output via an explicit "
        "string comparison -- GitHub Actions treats any non-empty string (including the "
        "literal 'false') as truthy in an if:, so a bare truthiness check would fail open"
    )


def test_build_job_has_no_elevated_permissions():
    assert _permissions_block(RELEASE_PLEASE, "build") == (
        "    permissions:\n      contents: read"
    ), (
        "build's permissions must be EXACTLY `contents: read` -- an unnamed elevated "
        "permission here would pass a probe over just id-token/attestations undetected"
    )


def test_build_job_runs_twine_check_strict():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert "twine check --strict" in block


def test_build_job_installs_from_the_hash_locked_requirements_file():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert "pip install --require-hashes -r .github/build-requirements.txt" in block, (
        "build no longer installs build/twine from the hash-locked requirements file -- an "
        "unpinned `pip install build twine` would let a compromised release of either package "
        "execute during CI, in the job whose output attest then signs"
    )


def test_build_job_actually_builds():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert re.search(r"^\s*-\s*run:\s*python -m build\b", block, re.MULTILINE), (
        "build no longer runs `python -m build` -- twine check --strict would then run "
        "against a stale or missing dist/, with nothing else pinning that the build step exists"
    )


def test_build_job_builds_without_isolation():
    block = _job_directives(RELEASE_PLEASE, "build")
    assert re.search(r"^\s*-\s*run:\s*python -m build --no-isolation\s*$", block, re.MULTILINE), (
        "build no longer runs `python -m build --no-isolation` -- an isolated build installs "
        "[build-system].requires (setuptools) UNVERIFIED at build time, from a fresh ephemeral "
        "environment pip never applies --require-hashes to, bypassing the hash-lock this same "
        "job just installed via pip install --require-hashes -r .github/build-requirements.txt "
        "-- the whole point of that lock"
    )


def _workflow_wide_directives(path: Path) -> str:
    """Everything above `jobs:` -- name/on/concurrency/permissions -- comment-stripped.

    Anchored on the literal `\\njobs:\\n` marker, not "the first two-space key in the file": the
    workflow's own `on:` block has a `push:` key at two-space indent too, and it appears BEFORE
    `permissions:` and before `jobs:` even starts -- a naive first-match search from file
    position 0 stops there and never reaches the block this helper is meant to bound. Verified
    against the live file before writing this: `text.index("\\njobs:\\n")` is the one literal,
    unique marker that separates workflow-level content from job content.

    Comment-stripped for the same reason `_job_directives` is: the live file has a comment
    reading "needs `contents: write`..." (about the App installation's own permission scope,
    unrelated to the workflow-level `permissions:` block) ahead of `jobs:` -- it happens to say
    `write` not `read` today, so it wouldn't collide with an unstripped search, but that's the
    file's current wording, not a property this helper should depend on.

    The `path` parameter is REQUIRED and must never gain a default. `release-please.yml`'s
    workflow-wide block is BYTE-IDENTICAL to `testpypi.yml`'s (`permissions:\\n  contents: read`),
    so a forgotten path argument would read the wrong file, compare it to the value expected of
    the other, and PASS -- pinning nothing. In the worst case the drift pin compares a file to
    itself and certifies perfect agreement. Required means a forgotten argument is a TypeError
    at collection time instead.
    """
    text = _text(path)
    block = text[: text.index("\njobs:\n")]
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def test_attest_job_is_gated_on_release_created():
    block = _job_directives(RELEASE_PLEASE, "attest")
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'" in block
    ), (
        "attest no longer gates on both success() and release-please's release_created output. "
        "The explicit success() is belt-and-suspenders (GitHub Actions already ANDs it in "
        "implicitly for any custom if: that doesn't name a status function -- verified against "
        "GitHub's own docs and this repo's own ci.yml precedent, where ci-success needs an "
        "explicit `if: always()` specifically to BYPASS that default), spelled out for the same "
        "reason the == 'true' string comparison is spelled out rather than left implicit."
    )


def test_attest_job_needs_release_please_and_build_exactly():
    block = _job_directives(RELEASE_PLEASE, "attest")
    assert re.search(r"^\s*needs:\s*\[release-please,\s*build\]\s*$", block, re.MULTILINE), (
        "attest's needs: is no longer exactly [release-please, build]"
    )


def test_attest_job_has_the_elevated_permissions_it_needs():
    assert _permissions_block(RELEASE_PLEASE, "attest") == (
        "    permissions:\n      id-token: write\n      attestations: write"
    ), (
        "attest's permissions must be EXACTLY id-token: write and attestations: write, "
        "nothing more and nothing less -- id-token: write mints the OIDC token "
        "attest-build-provenance exchanges for a Sigstore cert, and attestations: write "
        "lets it attach the resulting attestation to this repo"
    )


def test_every_job_agrees_on_the_artifact_name():
    """build uploads it; attest, pypi, release-assets and docker each download it. Read from
    all five sides rather than hardcoded five times, so a rename on one side is caught instead
    of silently decoupling the jobs. The `== "dist"` anchor stays: without it, five
    extractions that all failed would compare equal and pass."""
    steps = {
        "build": _step_containing(RELEASE_PLEASE, "build", "actions/upload-artifact"),
        "attest": _step_containing(RELEASE_PLEASE, "attest", "actions/download-artifact"),
        "pypi": _step_containing(RELEASE_PLEASE, "pypi", "actions/download-artifact"),
        "release-assets": _step_containing(
            RELEASE_PLEASE, "release-assets", "actions/download-artifact"
        ),
        "docker": _step_containing(
            RELEASE_PLEASE, "docker", "actions/download-artifact"
        ),
    }
    names = {}
    for job, step in steps.items():
        match = re.search(r"name:\s*(\S+)", step)
        assert match, f"couldn't find name: in {job}'s artifact step"
        names[job] = match.group(1)
    assert set(names.values()) == {"dist"}, f"jobs disagree on the artifact name: {names}"


def test_attest_covers_the_whole_dist_directory():
    step = _step_containing(RELEASE_PLEASE, "attest", "actions/attest-build-provenance")
    assert "subject-path: dist/*" in step, (
        "attest no longer covers the whole dist/ directory in one glob -- two enumerated "
        "extensions (*.whl, *.tar.gz) could miss a third artifact type later"
    )


def test_attest_downloads_to_the_path_it_scans():
    download_step = _step_containing(RELEASE_PLEASE, "attest", "actions/download-artifact")
    subject_step = _step_containing(RELEASE_PLEASE, "attest", "actions/attest-build-provenance")
    download_path = re.search(r"path:\s*(\S+)", download_step)
    assert download_path, "couldn't find path: in attest's download-artifact step"
    assert f"subject-path: {download_path.group(1)}*" in subject_step, (
        f"attest downloads to {download_path.group(1)!r} but its subject-path glob doesn't "
        f"cover that same directory -- attest would silently find zero subjects"
    )


def test_workflow_wide_permissions_stay_read_only():
    block = _workflow_wide_directives(RELEASE_PLEASE)
    idx = block.index("\npermissions:\n")
    perm_block = block[idx + 1 :]
    assert perm_block == "permissions:\n  contents: read", (
        "the workflow-wide permissions block must be EXACTLY `contents: read` and nothing "
        f"else -- an elevated or additional permission here would silently leak into every "
        f"job in this file, including release-please's own App-token-minting job. Got: "
        f"{perm_block!r}"
    )


def test_pypi_job_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "pypi")
    )


def test_pypi_job_needs_release_please_and_build_exactly():
    match = re.search(r"\n    needs: (.+)\n", _job_directives(RELEASE_PLEASE, "pypi"))
    assert match, "the 'pypi' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build]"


def test_pypi_job_declares_the_pypi_environment():
    """WHOLE-LINE, not a substring. The environment name is half of the trusted publisher's
    claim on pypi.org, and a substring probe is satisfied by any name this one PREFIXES --
    `environment: pypi-staging` passed it -- which is a different environment, a failed OIDC
    exchange, and an error naming neither."""
    assert re.search(r"^\s*environment: pypi[ \t]*$",
                     _job_directives(RELEASE_PLEASE, "pypi"), re.MULTILINE), (
        "pypi's `environment:` is no longer EXACTLY `pypi`"
    )


def test_pypi_job_holds_id_token_and_no_contents_key_at_all():
    """The ABSENCE of `contents:` is what makes the exhaustive-block reasoning bite.

    A job-level `permissions:` block is exhaustive, not additive: every permission not
    named becomes `none`. So `contents: read` appearing here would SILENTLY widen the
    publishing job beyond what it needs, and asserting only "no contents: write" would
    accept it. Resolved through `_permissions_block` rather than an `in` probe over the
    job text, because a probe cannot tell a permission from a mention of one.
    """
    assert _permissions_block(RELEASE_PLEASE, "pypi") == (
        "    permissions:\n      id-token: write"
    ), (
        "pypi's permissions must be EXACTLY `id-token: write` -- one line, because the "
        "absence of every other key is the claim. An `in`/`not in` probe over individual "
        "names is what `_permissions_block`'s own docstring exists to rule out: it lets an "
        "unnamed THIRD permission slip into the narrowest and most dangerous job here "
        "unnoticed -- measured green before this was tightened. `packages: write` is the "
        "named example because it is the shape a later channel PR would plausibly want in "
        "this file; the docker job holds it, and no other job here does"
    )


def test_pypi_publishes_to_real_pypi_by_naming_no_repository_url():
    """Paired with the TestPyPI half in Task 3. Together they stop the two mixups with
    real consequences: a dry run reaching production PyPI, or a real release going to
    TestPyPI and never publishing at all."""
    step = _step_containing(RELEASE_PLEASE, "pypi", "gh-action-pypi-publish")
    assert "repository-url" not in step


def test_pypi_does_not_skip_existing():
    """A duplicate upload must fail loudly. A release that silently no-ops its own
    publish reports green while shipping nothing -- the quiet-wrong-default bug class
    aimed at the one job whose entire purpose is the side effect. The FORBIDDEN VALUE is
    named rather than the permitted one: omitting the input (the `false` default) is what
    this design does, and stating `false` explicitly would also be fine."""
    step = _step_containing(RELEASE_PLEASE, "pypi", "gh-action-pypi-publish")
    assert "skip-existing: true" not in step


def test_release_please_job_exposes_the_tag_name_output():
    assert (
        "tag_name: ${{ steps.release.outputs.tag_name }}"
        in _job_directives(RELEASE_PLEASE, "release-please")
    )


def test_release_assets_job_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "release-assets")
    )


def test_release_assets_holds_contents_write_and_no_id_token():
    assert _permissions_block(RELEASE_PLEASE, "release-assets") == (
        "    permissions:\n      contents: write"
    ), (
        "release-assets' permissions must be EXACTLY `contents: write` -- one line, because "
        "a job-level block is exhaustive and the absence of every other key is the claim. "
        "An `in`/`not in` probe over individual names accepts an unnamed THIRD permission, "
        "which is the gap `_permissions_block`'s own docstring was written to close. "
        "`packages: write` is the named example because it is the shape a later channel PR "
        "would plausibly want in this file; the docker job holds it, and no other job here does"
    )


def test_release_assets_job_needs_release_please_and_build_exactly():
    """`pypi` and `attest` each pin their `needs:`; this job's went unpinned. It reads
    `needs.release-please.outputs.tag_name` and downloads `build`'s artifact, so dropping
    either dependency makes it run before what it consumes exists -- and `release-please` is
    the easy one to lose, since the `tag_name` reference LOOKS like a workflow-level value
    rather than a job output."""
    match = re.search(r"\n    needs: (.+)\n",
                      _job_directives(RELEASE_PLEASE, "release-assets"))
    assert match, "the 'release-assets' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build]", (
        f"release-assets' needs: is no longer exactly [release-please, build], it is "
        f"{match.group(1).strip()!r}"
    )


def test_release_assets_upload_does_not_clobber_an_existing_asset():
    """The ABSENCE is the design, and it was stated only in a comment.

    Its reasoning-pair -- `skip-existing` left off `pypi` -- IS pinned, by
    test_pypi_does_not_skip_existing; this half was not, so the asymmetry could be 'tidied'
    from one side. An asset already on the release means something already uploaded, which
    must surface rather than be silently overwritten. Recovery is deliberately manual: delete
    the attached asset, then re-run the job.

    `_step_containing` strips full-line comments, so the step's own `# No --clobber:` note
    cannot satisfy this -- the value is what is being read, not the prose about it.
    """
    step = _step_containing(RELEASE_PLEASE, "release-assets", "gh release upload")
    assert "--clobber" not in step, (
        "`gh release upload` gained --clobber. An asset that already exists means something "
        "already uploaded to this release; overwriting it silently is exactly what the "
        "no-skip-existing decision on `pypi` refuses on the other channel"
    )


def test_release_assets_upload_names_both_a_tag_and_a_repository():
    """`GH_REPO` is not decoration. `gh` resolves its target repository from `--repo`,
    then `GH_REPO`, then the cwd's git remotes -- it does NOT read `GITHUB_REPOSITORY`.
    This job deliberately never checks out, so without `GH_REPO` the resolution chain runs
    out and the step dies before any API call, AFTER release-please has already tagged and
    published the release. Three reviewers found this independently in the design, where an
    assertion pinning only the tag was satisfied by the dead step."""
    step = _step_containing(RELEASE_PLEASE, "release-assets", "gh release upload")
    assert "TAG: ${{ needs.release-please.outputs.tag_name }}" in step
    assert "GH_REPO: ${{ github.repository }}" in step


def test_testpypi_triggers_only_on_workflow_dispatch():
    """A dry-run workflow that gained a `push:` trigger would publish to a permanent,
    public index on every commit. Asserted on the trigger block's own contents, not by
    absence-of-substring across the whole file.

    The `on:` region is bounded at the NEXT top-level (zero-indent) key rather than left to
    run to the end of the workflow-wide block. `permissions:` sits at zero indent too, and
    its OWN `contents:` key sits at the same two-space indent as a trigger -- an unbounded
    region can't tell "a second trigger under on:" from "a different top-level block's own
    child key", and would count `contents` as a trigger on this exact file (verified: the
    unbounded slice yields ['workflow_dispatch', 'contents'] against the real, CORRECT file,
    which is not a defect in the file).
    """
    block = _workflow_wide_directives(TESTPYPI)
    # Start-of-LINE anchored, not `index("\non:")`: that spelling needs a preceding newline,
    # so removing the workflow's `name:` key would make `on:` the first line and raise
    # ValueError -- a crash reading as a broken test rather than as the trigger regression
    # this exists to report.
    on_match = re.search(r"^on:", block, re.MULTILINE)
    assert on_match, "testpypi.yml has no top-level `on:` key"
    rest = block[on_match.start() :]
    end = re.search(r"\n[a-z]", rest)
    on_block = rest[: end.start()] if end else rest
    triggers = re.findall(r"\n  ([a-z_]+):", on_block)
    assert triggers == ["workflow_dispatch"]


def test_testpypi_workflow_wide_permissions_are_read_only():
    """Exactly the slicing `test_workflow_wide_permissions_stay_read_only` already uses,
    pointed at the other file -- both workflows put `permissions:` last before `jobs:`."""
    block = _workflow_wide_directives(TESTPYPI)
    idx = block.index("\npermissions:\n")
    perm_block = block[idx + 1 :]
    assert perm_block == "permissions:\n  contents: read", (
        f"testpypi.yml's workflow-wide permissions must be EXACTLY `contents: read`. "
        f"Got: {perm_block!r}"
    )


def test_testpypi_job_holds_id_token_and_contents_read():
    """The dry run's job-level `permissions:` was pinned by NOTHING.

    All five `_permissions_block` call sites targeted `release-please.yml`, and this is the
    job that mints an OIDC token for a real publish to a permanent, public index. Measured
    before this existed: adding `packages: write` to this block left the whole module green,
    while the identical mutation on `pypi` was killed -- asymmetry, not an accepted gap.

    `contents: read` here is CORRECT and deliberate, unlike on `pypi` and `attest`, where its
    absence is the claim. Those two only download an already-built artifact; this job runs
    `actions/checkout` and builds FROM SOURCE, so it genuinely needs read access to the
    repository. Equality, not a probe, for the usual reason: a job-level block is exhaustive,
    so an unnamed third permission is invisible to any `in`/`not in` check over the two named
    here.
    """
    assert _permissions_block(TESTPYPI, "testpypi") == (
        "    permissions:\n      id-token: write\n      contents: read"
    ), (
        "the dry run's permissions must be EXACTLY `id-token: write` plus `contents: read` -- "
        "id-token because it publishes via Trusted Publishing, contents: read because unlike "
        "`pypi` it checks out and builds from source. Anything more is a widened token in the "
        "job that uploads to a public index"
    )


def test_testpypi_declares_the_testpypi_environment():
    """Paired with `test_pypi_job_declares_the_pypi_environment`. The environment name is
    half of each trusted publisher's claim, so a swap breaks authentication with an error
    that names neither."""
    assert re.search(r"^\s*environment: testpypi[ \t]*$",
                     _job_directives(TESTPYPI, "testpypi"), re.MULTILINE), (
        "the dry run's `environment:` is no longer EXACTLY `testpypi` -- matched whole-line "
        "for the same reason as its `pypi` twin: a substring probe accepts any name this one "
        "prefixes, which is a different environment and a failed OIDC exchange"
    )


def test_testpypi_publishes_to_testpypi_not_real_pypi():
    """WHOLE-LINE for the same reason the environment pins above are: a substring probe is
    satisfied by anything APPENDED to the value, and the URL this input carries is the one
    thing standing between a dry run and production PyPI."""
    step = _step_containing(TESTPYPI, "testpypi", "gh-action-pypi-publish")
    assert re.search(r"^\s*repository-url: https://test\.pypi\.org/legacy/[ \t]*$",
                     step, re.MULTILINE), (
        "the dry run's `repository-url:` is no longer EXACTLY TestPyPI's upload endpoint"
    )


def test_testpypi_skips_existing():
    """The asymmetry with `pypi` is deliberate: a dry run must tolerate a re-run of the
    same run number. It is BELT-AND-BRACES, not the mechanism that makes repeat dispatches
    work -- the version stamp is. Pinned as a pair with the `pypi` half so the asymmetry
    cannot be 'tidied' into consistency in either direction."""
    step = _step_containing(TESTPYPI, "testpypi", "gh-action-pypi-publish")
    assert "skip-existing: true" in step


def test_testpypi_refuses_a_non_default_branch():
    step = _step_containing(TESTPYPI, "testpypi", "Refuse to publish a non-default branch")
    assert "if: github.ref_name != github.event.repository.default_branch" in step
    assert "exit 1" in step


def test_the_version_stamp_fails_loudly_when_it_matches_nothing():
    """Presence is NOT enough, and this is the assertion that says so.

    `re.sub` returns its subject UNCHANGED on no match and raises nothing. A stamp built on
    it silently no-ops the moment that version line drifts: the build re-emits an
    already-uploaded filename, `skip-existing: true` swallows the duplicate, and the
    dispatch goes green having uploaded nothing -- the exact defect the stamp exists to
    remove, rebuilt inside its own fix. Three reviewers found that in the design. So pin the
    `subn` and the exit, not the step's existence."""
    step = _step_containing(TESTPYPI, "testpypi", "Stamp a unique dev version")
    assert "re.subn(" in step
    assert "if n != 1:" in step
    assert "sys.exit(" in step


_STAMP_PROOF_STEP = "Prove the stamp reached the artefacts"

# (dist/ filename or None for an empty dist/, must the step pass?, what the case represents).
# Every FAILING row is a distinct way the stamp can have not taken effect, and each one exists
# because a specific mutant survives without it -- see the test's docstring for which.
_STAMP_PROOF_CASES = (
    ("job_sluice-1.0.0.dev42-py3-none-any.whl", True, "a wheel carrying THIS run's stamp"),
    ("job_sluice-1.0.0-py3-none-any.whl", False, "an unstamped wheel"),
    ("job_sluice-1.0.0.dev41-py3-none-any.whl", False, "a wheel stamped for a DIFFERENT run"),
    (None, False, "an empty dist/"),
)


def _run_block_scalar(path: Path, needle: str) -> str:
    """The literal body of the `run: |` block scalar on the ONE step whose text holds `needle`.

    Read from the RAW file, NOT through `_step_containing`: that helper strips full-line
    comments, and a `#` line inside a shell script is CODE, not prose. Executing a body with
    those lines removed would run something the workflow does not, which is the one thing an
    executing test may never do -- it would certify a script that exists nowhere.

    Bounded by the file's own step boundary (`\\n      - `, the same idiom `_step_containing`
    splits on) so a step carrying no `run:` cannot silently borrow the NEXT step's one. The
    body's own indentation is derived from its first non-blank line rather than assumed to be
    the key's plus two, and dedented by exactly that -- a block scalar's indentation is
    whatever its first line sets, and guessing it wrong yields a script bash would reject for
    reasons that have nothing to do with what is being tested.
    """
    text = _text(path)
    assert text.count(needle) == 1, (
        f"expected exactly one occurrence of {needle!r} in {path.name}, found "
        f"{text.count(needle)} -- zero leaves nothing to execute and every assertion over the "
        f"result vacuous; two makes it ambiguous which step is being run"
    )
    region = text[text.index(needle):]
    boundary = re.search(r"\n      - ", region)
    if boundary:
        region = region[: boundary.start()]
    match = re.search(r"\n( +)run: \|\n", region)
    assert match, (
        f"the step containing {needle!r} in {path.name} no longer carries a `run: |` block "
        f"scalar, so there is no script here to execute"
    )
    key_indent = match.group(1)
    lines = region[match.end():].splitlines()
    first = next((ln for ln in lines if ln.strip()), None)
    assert first is not None, f"the `run: |` block scalar for {needle!r} in {path.name} is empty"
    body_indent = first[: len(first) - len(first.lstrip())]
    assert len(body_indent) > len(key_indent), (
        f"the `run: |` body for {needle!r} in {path.name} is not indented past its own key, so "
        f"nothing here is a block scalar body: {first!r}"
    )
    body = []
    for line in lines:
        if not line.strip():
            body.append("")
        elif line.startswith(body_indent):
            body.append(line[len(body_indent):])
        else:
            break
    return "\n".join(body) + "\n"


def test_the_stamp_proof_actually_refuses_a_dist_the_stamp_never_reached(tmp_path):
    """The stamp proof is EXECUTED here against constructed `dist/` directories, not read.

    A successful substitution says the SOURCE changed, not that the BUILD consumed it. They
    are coupled today by `dynamic = ["version"]` reading `sluice.__version__`, but that
    coupling is exactly what a packaging change alters unnoticed, so the workflow observes the
    artefact -- and this test observes the workflow OBSERVING it.

    WHY EXECUTION RATHER THAN PATTERN-MATCHING, which is what this replaced. A step can be
    present and INERT, and a token probe certifies the inert version just as happily as the
    working one. That is not a hypothetical: the token probes here pinned `shopt -s nullglob`,
    exactly one `dist/` line, that line matching `.dev${RUN}`, and `exit 1` in the step -- and
    never the comparison those four exist to serve. Measured on the real file, all three of
    these left the whole module GREEN:

    - `-eq 0` -> `-ne 0`: a perfectly INVERTED gate. Run for real it exits 1 on a correctly
      stamped `dist/` and 0 on an unstamped one.
    - `-eq 0` -> `-lt 0` (never true): fully inert. Exits 0 on stamped, unstamped and empty
      alike, the upload proceeds, `skip-existing: true` swallows TestPyPI's duplicate
      rejection, and the dispatch is green having proved nothing.
    - `-eq 0` -> `-gt 99999`: the same, by another spelling.

    No count of token probes closes that class, because the defect is that a token is not a
    behaviour. Running the script is the only assertion that binds the comparison.

    WHAT EACH ROW WITNESSES. The unstamped and empty rows also kill deleting `shopt -s
    nullglob`: without it a glob matching nothing expands to its own literal text, the array
    holds that one element, the count is 1, and the step reports success against a `dist/`
    carrying no stamped artefact at all. The `.dev41` row is the one that binds `${RUN}`
    itself -- without it a predicate widened to `dist/*.dev*` passes every remaining row while
    accepting an artefact stamped for some other run. The stamped row is the only one that can
    catch an inverted or unconditionally-failing gate.

    HERMETIC BY CONSTRUCTION, not by assertion. `tmp_path`, an explicit `bash` (never the
    ambient `$SHELL`), and an environment of exactly `RUN` and an EMPTY `PATH`. The empty PATH
    is the load-bearing half: the repo's session-wide `_forbid_dns` fixture patches
    `socket.getaddrinfo` IN THIS PROCESS and a subprocess inherits none of it, so hermeticity
    has to come from somewhere else. With no PATH, bash can execute nothing but its own
    builtins -- `shopt`, `[`, `echo`, `exit` and globbing, which is the entire body today. A
    future body reaching for `curl`, `pip` or `python` fails LOUDLY with "No such file or
    directory" rather than quietly acquiring network access inside the test suite.
    """
    body = _run_block_scalar(TESTPYPI, _STAMP_PROOF_STEP)
    assert body.strip(), "the stamp-proof step's script is empty; there is nothing to execute"
    bash = shutil.which("bash")
    assert bash, "bash is required to execute the workflow step this test pins"

    for index, (filename, should_pass, description) in enumerate(_STAMP_PROOF_CASES):
        workdir = tmp_path / f"case{index}"
        (workdir / "dist").mkdir(parents=True)
        if filename is not None:
            (workdir / "dist" / filename).write_bytes(b"")
        # `-e` mirrors the shell GitHub Actions runs a `run:` step under (`bash -e {0}`), so
        # the exit status observed here is the one that would decide the real job.
        proc = subprocess.run(
            [bash, "-e", "-c", body],
            cwd=workdir, env={"RUN": "42", "PATH": ""},
            capture_output=True, text=True, timeout=60,
        )
        output = proc.stdout + proc.stderr
        if should_pass:
            assert proc.returncode == 0, (
                f"the stamp proof rejects {description} (exit {proc.returncode}). A gate that "
                f"fails on a correctly stamped dist/ blocks every dispatch; an INVERTED one "
                f"(`-ne 0`) fails exactly here. Output: {output!r}"
            )
        else:
            assert proc.returncode != 0, (
                f"the stamp proof ACCEPTS {description}, so the dispatch would upload an "
                f"artefact the stamp never reached -- already present on TestPyPI, silently "
                f"skipped by `skip-existing: true`, and green having proved nothing. "
                f"Output: {output!r}"
            )
            assert output.strip(), (
                f"the stamp proof rejects {description} but says NOTHING about why. A bare "
                f"non-zero exit gives a human staring at a failed dispatch no annotation to "
                f"read, which is the difference between a diagnosis and a mystery"
            )


def test_the_stamp_proof_is_given_this_run_s_number_by_the_workflow():
    """The one thing executing the step CANNOT show, kept as a text assertion for that reason.

    The test above supplies `RUN=42` itself, so it proves what the script does with a run
    number and nothing at all about where a real dispatch gets one. Drop this `env:` and
    `${RUN}` expands to the empty string: the predicate becomes `dist/*.dev*`, which accepts
    an artefact stamped for any run whatsoever -- and the stamp step above, reading the same
    variable, would have died first with a KeyError. Pinned on the value as well as the key,
    because a `RUN` sourced from anything other than `github.run_number` is not the number the
    stamp used.
    """
    step = _step_containing(TESTPYPI, "testpypi", _STAMP_PROOF_STEP)
    assert re.search(r"^\s*RUN: \$\{\{ github\.run_number \}\}[ \t]*$", step, re.MULTILINE), (
        "the stamp-proof step no longer receives RUN from github.run_number, so the marker it "
        "looks for is not the one the stamp wrote"
    )

def _artifact_retention_days(path: Path, job: str) -> int:
    """The `retention-days:` value on `job`'s upload-artifact step, as an int."""
    step = _step_containing(path, job, "actions/upload-artifact")
    match = re.search(r"^\s*retention-days:\s*(\d+)[ \t]*$", step, re.MULTILINE)
    assert match, (
        f"{path.name}'s {job!r} job pins no retention-days on its upload-artifact step, so "
        f"the window falls back to a repository- or organization-level setting that lives "
        f"outside this file and nothing here can vouch for"
    )
    return int(match.group(1))


def test_the_dist_artifact_outlives_a_failed_first_publish():
    """`build`'s upload carries ten lines of comment saying this retention is the ONLY
    automated recovery path when `pypi` fails: re-running the whole workflow does not work,
    because release-please then sees the release already cut, `release_created` comes back
    `false`, and `build` never runs to produce a new artifact. Reverting the value to `1`
    reddened nothing -- a comment that states a mechanism needs a row that falsifies it.

    A FLOOR, not an equality. Raising the window is not a regression and must not fail the
    build; shortening it back past the point where a manual pypi.org configuration fix fits
    inside it is exactly the change this catches.
    """
    days = _artifact_retention_days(RELEASE_PLEASE, "build")
    assert days >= 7, (
        f"the dist artifact is retained for {days} day(s). `pypi` and `release-assets` both "
        f"consume it and a PyPI upload cannot be withdrawn, so if `pypi` fails -- most likely "
        f"because the trusted-publisher entry does not exist yet or names the wrong workflow "
        f"filename -- re-running that job while this artifact still exists is the only "
        f"automated recovery. A day is too tight for a failure whose diagnosis is a manual "
        f"pypi.org configuration step"
    )


_BUILD_COMMANDS = (
    "pip install --require-hashes -r .github/build-requirements.txt",
    "python -m build --no-isolation",
    "twine check --strict dist/*",
)


def _post_checkout_run_steps(path: Path, job: str) -> list[str]:
    """Every step of `job` at or after its `actions/checkout` step that carries a `run:`.

    Split on the file's OWN step boundary -- the same `\n(?=      - )` idiom
    `_step_containing` uses -- and then ask each resulting PART whether a `run:` appears
    anywhere in it. The bespoke per-step regex this replaces (`- ` then an optional
    `name:` line then `run:`) could not bind across an intervening `if:` line, so an
    `if:`-gated run step was invisible to it whether or not it was named. Measured: adding
    one such step to EACH job left both counts below reading their expected 3 and 5 while
    a real extra step sat in each file. Splitting on the boundary has no such blind spot,
    because it never has to enumerate which job-level keys may precede `run:`.

    Anchored past checkout so `testpypi.yml`'s branch guard -- which deliberately runs
    BEFORE checkout, so a wrong branch is refused before any source is fetched -- sits
    outside the region by construction, letting the two regions describe the same thing.
    """
    block = _job_directives(path, job)
    marker = "actions/checkout@"
    assert marker in block, f"the {job!r} job in {path.name} has no checkout to anchor on"
    parts = re.split(r"\n(?=      - )", block[block.index(marker):])
    return [part for part in parts if re.search(r"(^|\n)\s*-?\s*run: ", part)]


def _run_commands(path: Path, job: str) -> set[str]:
    """The EXACT command each post-checkout `run:` step of `job` names, as written.

    Exact, because `_BUILD_COMMANDS` membership was tested with `in` over the whole job
    block, and a substring survives an APPENDED flag. Measured: appending `--wheel` to
    `testpypi.yml`'s `python -m build --no-isolation` left the drift pin green while the
    dry run silently stopped building an sdist at all -- so it stopped proving index
    acceptance of the newly-permanent artefact this whole PR exists to add.
    `release-please.yml`'s half was already covered by
    `test_build_job_builds_without_isolation`'s `$`-anchored regex; nothing covered the copy.

    A block scalar (`run: |`) yields the literal `"|"` here, which is exactly right: the two
    steps written that way are the dry run's own stamp and stamp-proof, pinned by their own
    tests above and deliberately not part of the sequence being compared.
    """
    commands = set()
    for step in _post_checkout_run_steps(path, job):
        for line in step.splitlines():
            match = re.match(r"\s*-?\s*run: (.+)$", line)
            if match:
                commands.add(match.group(1).strip())
    return commands


def _publish_action_ref(path: Path, job: str) -> str:
    """The `pypa/gh-action-pypi-publish` ref `job` pins, as a bare ref (SHA, no comment)."""
    step = _step_containing(path, job, "gh-action-pypi-publish")
    match = re.search(r"pypa/gh-action-pypi-publish@(\S+)", step)
    assert match, f"no pypa/gh-action-pypi-publish pin found in {path.name}'s {job!r} job"
    return match.group(1)


def _python_version(path: Path, job: str) -> str:
    match = re.search(r"python-version: \"([^\"]+)\"", _job_directives(path, job))
    assert match, f"no python-version pinned in {path.name}'s {job!r} job"
    return match.group(1)


def test_the_dry_run_builds_exactly_the_way_the_release_build_does():
    """The cost of a separate dry-run file is that its build steps are a COPY, and a copy
    can stop matching what it claims to prove without anything going red.

    What ships here: three EXACT-command membership probes per side (`_BUILD_COMMANDS`
    against `_run_commands`), then two equalities between two extractions -- the pinned
    `python-version` and the pinned `pypa/gh-action-pypi-publish` ref. The scope assertion is
    NOT a third such equality: it is two separate checks, each side's count of post-checkout
    `run:` steps against its own pinned literal (3 and 5), deliberately different from each
    other rather than compared to one another.

    GUARDED AGAINST ITS OWN VACUITY, because an equality between two extractions passes
    trivially when both extractions fail -- `None == None` is green while the two files
    build differently, which is the `all([])` shape this repo has a standing rule about. The
    design's first draft omitted these guards while citing two precedents that both carry
    them. Each guard is structural rather than a separate assertion bolted on: an exact
    membership probe over an EMPTY set is False, and `_python_version`/`_publish_action_ref`
    each assert their own match before returning, so no extraction here can fail quietly.
    """
    release = _run_commands(RELEASE_PLEASE, "build")
    dry_run = _run_commands(TESTPYPI, "testpypi")

    for command in _BUILD_COMMANDS:
        # EXACT membership, never `in` over the job text: a substring probe survives an
        # APPENDED flag, and `--wheel` appended to the dry run's build was measured green
        # while that run stopped producing an sdist at all. It doubles as the non-vacuity
        # guard -- `command in set()` is False, so a broken extraction cannot make the two
        # sides agree by both having found nothing.
        assert command in release, (
            f"release-please.yml's `build` job no longer runs this as an exact command: "
            f"{command!r}. It runs: {sorted(release)}")
        assert command in dry_run, (
            f"testpypi.yml's `testpypi` job no longer runs this as an exact command: "
            f"{command!r}. It runs: {sorted(dry_run)}")

    assert _python_version(RELEASE_PLEASE, "build") == _python_version(TESTPYPI, "testpypi")

    # ...and on the publish action itself, as a PAIR. A version bump landing on one file and
    # not the other leaves the dry run proving index acceptance through a DIFFERENT action
    # version than the release path takes -- precisely what this pin exists to prevent, and
    # one line outside the scope it originally had.
    assert _publish_action_ref(RELEASE_PLEASE, "pypi") == _publish_action_ref(
        TESTPYPI, "testpypi"
    ), (
        "the two workflows pin different pypa/gh-action-pypi-publish refs, so the dry run "
        "exercises a different publish action than the real release does"
    )

    # Scope: pin how many run: steps each region has, so an unexplained extra step -- or a
    # silently dropped one -- cannot read as agreement. They differ because the dry run
    # legitimately carries the two steps that make a dispatch prove something.
    _NOT_THE_NUMBER = (
        "FIX THE EXTRACTION OR THE WORKFLOW, NEVER THIS NUMBER. It has been derived from "
        "the real file independently, more than once; a count edited to make a broken "
        "extractor go green is exactly the defect this assertion exists to catch, installed "
        "in the assertion."
    )
    release_steps = _post_checkout_run_steps(RELEASE_PLEASE, "build")
    assert len(release_steps) == 3, (
        f"release-please.yml's `build` job has {len(release_steps)} post-checkout `run:` "
        f"steps, expected 3. {_NOT_THE_NUMBER} Found: {release_steps}"
    )
    dry_run_steps = _post_checkout_run_steps(TESTPYPI, "testpypi")
    assert len(dry_run_steps) == 5, (
        f"testpypi.yml's `testpypi` job has {len(dry_run_steps)} post-checkout `run:` "
        f"steps, expected 5 (the three shared build commands plus the stamp and its proof). "
        f"{_NOT_THE_NUMBER} Found: {dry_run_steps}"
    )


# ── the Docker channel's two jobs (#104 PR 4) ────────────────────────────────


def test_docker_job_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "docker")
    )


def test_docker_job_needs_release_please_and_build_exactly():
    """It reads `needs.release-please.outputs.{version,major,minor,sha}` for its tags and its
    checkout ref, and consumes `build`'s artifact. `release-please` is named directly rather
    than relied on transitively through `build`, for the reason `attest` and `pypi` do it:
    reading `needs.<job>.outputs.*` requires a direct dependency edge."""
    match = re.search(r"\n    needs: (.+)\n", _job_directives(RELEASE_PLEASE, "docker"))
    assert match, "the 'docker' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build]", (
        f"docker's needs: is no longer exactly [release-please, build], it is "
        f"{match.group(1).strip()!r}"
    )


def test_docker_job_holds_contents_read_and_packages_write_exactly():
    """`contents: read` is NOT redundant with the workflow-wide default here, which is the
    trap this equality exists to hold. A job-level block is exhaustive, so naming
    `packages: write` alone sets contents to `none` and the checkout this job performs fails
    -- and it is the only publishing job in this file that checks out at all, because the
    Dockerfile is repository source rather than a built artefact.

    Resolved through `_permissions_block` rather than an `in` probe for the reason its own
    docstring gives: a probe cannot see an unnamed THIRD permission, and this is now the
    widest-permissioned job in the workflow."""
    assert _permissions_block(RELEASE_PLEASE, "docker") == (
        "    permissions:\n      contents: read\n      packages: write"
    ), (
        "docker's permissions must be EXACTLY `contents: read` + `packages: write`. It is the "
        "only job in this file holding a registry credential, and the absence of every other "
        "key -- `id-token: write` above all -- is the claim: signing lives in `attest-image`, "
        "so that a registry credential and an OIDC identity are never held by one job"
    )


def test_docker_job_downloads_the_artifact_into_the_dist_directory():
    """`path: dist/` is load-bearing, not consistency for its own sake. The Dockerfile's
    `COPY dist/*.whl` and `.dockerignore`'s `!dist/*.whl` both name that exact directory, so
    without it the artifact unpacks into the workspace root, the build context is empty, and
    the failure appears as a confusing COPY error at image-build time. It fails only on the
    release path, which nothing exercises until a tag is already public."""
    step = _step_containing(RELEASE_PLEASE, "docker", "actions/download-artifact")
    assert re.search(r"^\s*path: dist/[ \t]*$", step, re.MULTILINE), (
        "docker's download-artifact step must set `path: dist/` -- the Dockerfile and "
        ".dockerignore both hard-require the wheel at that exact location"
    )


def test_docker_job_exposes_the_pushed_digest_as_a_job_output():
    """`id: push` on the step is NOT enough, and this is the whole point of the test: step
    outputs do not cross a job boundary. Without the job-level `outputs:` mapping,
    `needs.docker.outputs.digest` is the empty string, and `attest-image` signs NOTHING while
    reporting success -- a green attestation over no subject, which is worse than no
    attestation at all because it looks like coverage."""
    block = _job_directives(RELEASE_PLEASE, "docker")
    assert "digest: ${{ steps.push.outputs.digest }}" in block, (
        "docker must expose the pushed digest as a JOB output; `id: push` alone leaves "
        "needs.docker.outputs.digest empty"
    )
    assert re.search(r"^\s*id: push[ \t]*$", block, re.MULTILINE), (
        "the job output above references `steps.push`, so a step must carry `id: push`"
    )


def test_docker_job_builds_both_target_platforms():
    """A single-arch image silently excludes every Apple Silicon and arm64 Linux user, and
    nothing about the published tag says so -- they get an emulated image or a manifest
    error, depending on their client."""
    step = _step_containing(RELEASE_PLEASE, "docker", "docker/build-push-action")
    assert re.search(r"^\s*platforms: linux/amd64,linux/arm64[ \t]*$", step, re.MULTILINE), (
        "docker must build linux/amd64 AND linux/arm64"
    )


def test_attest_image_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "attest-image")
    )


def test_attest_image_needs_release_please_and_docker_exactly():
    match = re.search(r"\n    needs: (.+)\n", _job_directives(RELEASE_PLEASE, "attest-image"))
    assert match, "the 'attest-image' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, docker]", (
        f"attest-image's needs: is no longer exactly [release-please, docker], it is "
        f"{match.group(1).strip()!r}"
    )


def test_attest_image_holds_the_signing_pair_and_no_registry_credential():
    """The ABSENCE of `packages:` is the claim, and it is the reason this is a separate job
    rather than two more steps on `docker`.

    The justification is NOT that a BuildKit `RUN` is an arbitrary-code-execution surface the
    way `python -m build --no-isolation` is -- that was an earlier draft's reasoning and it is
    false, because a BuildKit step has no ACTIONS_ID_TOKEN_REQUEST_TOKEN in its environment and
    so cannot mint an OIDC token however hostile a dependency is. The real basis is a property
    a reader can check against this file in seconds: every write-holding job here holds exactly
    one KIND of write, and folding these permissions into `docker` would put a registry
    credential and an OIDC identity in the same job for the first time."""
    assert _permissions_block(RELEASE_PLEASE, "attest-image") == (
        "    permissions:\n      id-token: write\n      attestations: write"
    ), (
        "attest-image's permissions must be EXACTLY id-token + attestations. No `packages:` "
        "key: the attestation is repo-side Sigstore, so nothing is written back to the "
        "registry. No `contents:` key either -- this job checks nothing out"
    )


def test_attest_image_names_both_the_subject_name_and_the_pushed_digest():
    """An OCI subject is not identified by a digest alone. `subject-name` is what
    `gh attestation verify oci://...` matches against, and the digest must be the one
    `docker` actually PUSHED rather than one recomputed here -- otherwise the attestation
    covers an image nobody pulled."""
    step = _step_containing(RELEASE_PLEASE, "attest-image", "actions/attest-build-provenance")
    assert "subject-name: ghcr.io/mrreasonable/job-sluice" in step, (
        "attest-image must name the OCI subject it is signing"
    )
    assert "subject-digest: ${{ needs.docker.outputs.digest }}" in step, (
        "attest-image must sign the digest docker pushed, read from that job's output"
    )


_MODULE_HELPER_NAMES = {
    "_text", "_job_directives", "_step_containing", "_permissions_block",
    "_workflow_wide_directives", "_post_checkout_run_steps", "_run_commands",
    "_publish_action_ref", "_python_version", "_job_names", "_artifact_retention_days",
    "_roster_failure", "_run_block_scalar",
}


def test_every_module_level_helper_takes_path_first_with_no_default():
    """Enforces the module docstring's claim -- every `_`-prefixed helper takes `path` as its
    first, required parameter -- rather than leaving it as prose no test can falsify. A
    defaulted `path` would let a forgotten argument silently read whichever file the default
    names; the two workflows' workflow-wide blocks are byte-identical, so that mistake would
    PASS every other check in this file rather than fail one.
    """
    helpers = {
        name: fn for name, fn in globals().items()
        if inspect.isfunction(fn) and fn.__module__ == __name__ and name.startswith("_")
    }
    # Pin the SCOPE first: a matcher that silently enumerated nothing (or the wrong set)
    # would make the loop below vacuously true, `all([])`-style.
    assert helpers.keys() == _MODULE_HELPER_NAMES, sorted(helpers)
    for name, fn in helpers.items():
        first = next(iter(inspect.signature(fn).parameters.values()))
        assert first.name == "path" and first.default is inspect.Parameter.empty, (
            f"{name}'s first parameter must be a required `path`, got {first!r}"
        )
