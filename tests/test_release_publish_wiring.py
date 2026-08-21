"""Wiring pins for PR 2 of #104: the `build`/`attest` publish jobs added to
`.github/workflows/release-please.yml`, gated on release-please's own `release_created` output.

Text-matching, not a YAML parse -- pyyaml is a guarded optional import in sluice/ (CLAUDE.md's
stdlib-only rule), so a test needing it could skip itself into uselessness on a bare install.
Mirrors tests/test_ci_wiring.py's own idiom (_job_directives/_step_containing, comment-stripped)
rather than importing it -- file-scoped helpers, matching that file's own convention, for two
small functions that don't warrant cross-file coupling.

See docs/superpowers/specs/2026-08-10-publish-workflow-skeleton-design.md for the full design
reasoning (why release-please needed a job output added, why the gate is a string comparison
not bare truthiness, why the top-level-permissions check is position-anchored on `jobs:` rather
than "the first two-space key").
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
RELEASE_PLEASE = ROOT / ".github" / "workflows" / "release-please.yml"


def _rp_text() -> str:
    return RELEASE_PLEASE.read_text()


def _job_directives(name: str) -> str:
    """One job's YAML, sliced out by indentation, comment-stripped.

    A job key is the only thing at two-space indent; steps and job-level keys (permissions,
    outputs) sit at four or more, so the next two-space key ends the block. Comment-stripped so
    a substring test can't be satisfied by prose EXPLAINING a value rather than the value itself
    -- tests/test_ci_wiring.py's own `_job_directives` docstring records this bug having fired
    once already in this exact repo's workflow files.
    """
    text = _rp_text()
    start = text.index(f"\n  {name}:\n")
    rest = text[start + 1 :]
    end = re.search(r"\n  [a-z][\w-]*:\n", rest)
    block = rest[: end.start()] if end else rest
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def _step_containing(job: str, needle: str) -> str:
    """The ONE step of `job` whose body contains `needle`, comment-stripped.

    Requires EXACTLY one match: zero means the sweep found nothing and every assertion over it
    would be vacuous; two makes it ambiguous which step is being pinned -- e.g. a future
    `id: release-summary` step must never be silently accepted as a match for `id: release`.
    """
    block = _job_directives(job)
    marker = "\n    steps:\n"
    assert marker in block, f"the {job!r} job has no `steps:` key; nothing here can be a step"
    parts = re.split(r"\n(?=      - )", block[block.index(marker) + len(marker) :])
    matches = [part for part in parts if needle in part]
    assert len(matches) == 1, (
        f"expected exactly one step in the {job!r} job containing {needle!r}, found "
        f"{len(matches)}."
    )
    return matches[0]


def test_release_please_job_exposes_the_release_created_output():
    """Without this, every `needs.release-please.outputs.release_created` reference the build/
    attest jobs use resolves to an empty string, `== 'true'` is always false, and neither job
    ever runs on a real release -- silently."""
    step = _step_containing("release-please", "googleapis/release-please-action")
    assert re.search(r"^\s*id:\s*release\s*$", step, re.MULTILINE), (
        "the release-please-action step no longer carries `id: release` -- nothing can "
        "reference its output"
    )
    block = _job_directives("release-please")
    assert "outputs:" in block, "the release-please job has no job-level `outputs:` key"
    assert "release_created: ${{ steps.release.outputs.release_created }}" in block, (
        "the release-please job's `outputs:` block no longer names `release_created` sourced "
        "from the `release` step"
    )


def _permissions_block(job: str) -> str:
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
    block = _job_directives(job)
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
    assert _permissions_block("release-please") == "    permissions:\n      contents: read", (
        "release-please's permissions must be EXACTLY `contents: read` -- an unnamed "
        "elevated permission here would pass a probe over just id-token/attestations "
        "undetected"
    )


def test_release_please_job_exposes_the_release_sha_output():
    block = _job_directives("release-please")
    assert "sha: ${{ steps.release.outputs.sha }}" in block, (
        "the release-please job's outputs no longer expose `sha` -- build would then check "
        "out github.sha (the commit that triggered THIS run) rather than the commit "
        "release-please actually tagged, which can diverge if a prior run failed after "
        "release-please tagged but before build/attest ran"
    )


def test_build_checks_out_the_tagged_sha_not_the_trigger_commit():
    step = _step_containing("build", "actions/checkout")
    assert "ref: ${{ needs.release-please.outputs.sha }}" in step, (
        "build's checkout no longer pins ref: to release-please's own sha output -- it would "
        "silently fall back to github.sha, which is the commit that triggered this run and "
        "can be a DIFFERENT commit than the one release-please just tagged"
    )


def test_build_job_depends_on_release_please():
    block = _job_directives("build")
    assert re.search(r"^\s*needs:\s*release-please\s*$", block, re.MULTILINE), (
        "build's needs: is no longer exactly release-please"
    )


def test_build_job_is_gated_on_release_created():
    block = _job_directives("build")
    assert "if: needs.release-please.outputs.release_created == 'true'" in block, (
        "build no longer gates on release-please's release_created output via an explicit "
        "string comparison -- GitHub Actions treats any non-empty string (including the "
        "literal 'false') as truthy in an if:, so a bare truthiness check would fail open"
    )


def test_build_job_has_no_elevated_permissions():
    assert _permissions_block("build") == "    permissions:\n      contents: read", (
        "build's permissions must be EXACTLY `contents: read` -- an unnamed elevated "
        "permission here would pass a probe over just id-token/attestations undetected"
    )


def test_build_job_runs_twine_check_strict():
    block = _job_directives("build")
    assert "twine check --strict" in block


def test_build_job_installs_from_the_hash_locked_requirements_file():
    block = _job_directives("build")
    assert "pip install --require-hashes -r .github/build-requirements.txt" in block, (
        "build no longer installs build/twine from the hash-locked requirements file -- an "
        "unpinned `pip install build twine` would let a compromised release of either package "
        "execute during CI, in the job whose output attest then signs"
    )


def test_build_job_actually_builds():
    block = _job_directives("build")
    assert re.search(r"^\s*-\s*run:\s*python -m build\b", block, re.MULTILINE), (
        "build no longer runs `python -m build` -- twine check --strict would then run "
        "against a stale or missing dist/, with nothing else pinning that the build step exists"
    )


def test_build_job_builds_without_isolation():
    block = _job_directives("build")
    assert re.search(r"^\s*-\s*run:\s*python -m build --no-isolation\s*$", block, re.MULTILINE), (
        "build no longer runs `python -m build --no-isolation` -- an isolated build installs "
        "[build-system].requires (setuptools) UNVERIFIED at build time, from a fresh ephemeral "
        "environment pip never applies --require-hashes to, bypassing the hash-lock this same "
        "job just installed via pip install --require-hashes -r .github/build-requirements.txt "
        "-- the whole point of that lock"
    )


def _workflow_wide_directives() -> str:
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
    """
    text = _rp_text()
    block = text[: text.index("\njobs:\n")]
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def test_attest_job_is_gated_on_release_created():
    block = _job_directives("attest")
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
    block = _job_directives("attest")
    assert re.search(r"^\s*needs:\s*\[release-please,\s*build\]\s*$", block, re.MULTILINE), (
        "attest's needs: is no longer exactly [release-please, build]"
    )


def test_attest_job_has_the_elevated_permissions_it_needs():
    assert _permissions_block("attest") == (
        "    permissions:\n      id-token: write\n      attestations: write"
    ), (
        "attest's permissions must be EXACTLY id-token: write and attestations: write, "
        "nothing more and nothing less -- id-token: write mints the OIDC token "
        "attest-build-provenance exchanges for a Sigstore cert, and attestations: write "
        "lets it attach the resulting attestation to this repo"
    )


def test_every_job_agrees_on_the_artifact_name():
    """build uploads it; attest, pypi and release-assets each download it. Read from all
    four sides rather than hardcoded four times, so a rename on one side is caught instead
    of silently decoupling the jobs. The `== "dist"` anchor stays: without it, four
    extractions that all failed would compare equal and pass."""
    steps = {
        "build": _step_containing("build", "actions/upload-artifact"),
        "attest": _step_containing("attest", "actions/download-artifact"),
        "pypi": _step_containing("pypi", "actions/download-artifact"),
        "release-assets": _step_containing("release-assets", "actions/download-artifact"),
    }
    names = {}
    for job, step in steps.items():
        match = re.search(r"name:\s*(\S+)", step)
        assert match, f"couldn't find name: in {job}'s artifact step"
        names[job] = match.group(1)
    assert set(names.values()) == {"dist"}, f"jobs disagree on the artifact name: {names}"


def test_attest_covers_the_whole_dist_directory():
    step = _step_containing("attest", "actions/attest-build-provenance")
    assert "subject-path: dist/*" in step, (
        "attest no longer covers the whole dist/ directory in one glob -- two enumerated "
        "extensions (*.whl, *.tar.gz) could miss a third artifact type later"
    )


def test_attest_downloads_to_the_path_it_scans():
    download_step = _step_containing("attest", "actions/download-artifact")
    subject_step = _step_containing("attest", "actions/attest-build-provenance")
    download_path = re.search(r"path:\s*(\S+)", download_step)
    assert download_path, "couldn't find path: in attest's download-artifact step"
    assert f"subject-path: {download_path.group(1)}*" in subject_step, (
        f"attest downloads to {download_path.group(1)!r} but its subject-path glob doesn't "
        f"cover that same directory -- attest would silently find zero subjects"
    )


def test_workflow_wide_permissions_stay_read_only():
    block = _workflow_wide_directives()
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
        in _job_directives("pypi")
    )


def test_pypi_job_needs_release_please_and_build_exactly():
    match = re.search(r"\n    needs: (.+)\n", _job_directives("pypi"))
    assert match, "the 'pypi' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build]"


def test_pypi_job_declares_the_pypi_environment():
    assert "environment: pypi" in _job_directives("pypi")


def test_pypi_job_holds_id_token_and_no_contents_key_at_all():
    """The ABSENCE of `contents:` is what makes the exhaustive-block reasoning bite.

    A job-level `permissions:` block is exhaustive, not additive: every permission not
    named becomes `none`. So `contents: read` appearing here would SILENTLY widen the
    publishing job beyond what it needs, and asserting only "no contents: write" would
    accept it. Resolved through `_permissions_block` rather than an `in` probe over the
    job text, because a probe cannot tell a permission from a mention of one.
    """
    block = _permissions_block("pypi")
    assert "id-token: write" in block
    assert "contents:" not in block


def test_pypi_publishes_to_real_pypi_by_naming_no_repository_url():
    """Paired with the TestPyPI half in Task 3. Together they stop the two mixups with
    real consequences: a dry run reaching production PyPI, or a real release going to
    TestPyPI and never publishing at all."""
    step = _step_containing("pypi", "gh-action-pypi-publish")
    assert "repository-url" not in step


def test_pypi_does_not_skip_existing():
    """A duplicate upload must fail loudly. A release that silently no-ops its own
    publish reports green while shipping nothing -- the quiet-wrong-default bug class
    aimed at the one job whose entire purpose is the side effect. The FORBIDDEN VALUE is
    named rather than the permitted one: omitting the input (the `false` default) is what
    this design does, and stating `false` explicitly would also be fine."""
    step = _step_containing("pypi", "gh-action-pypi-publish")
    assert "skip-existing: true" not in step


def test_release_please_job_exposes_the_tag_name_output():
    assert (
        "tag_name: ${{ steps.release.outputs.tag_name }}"
        in _job_directives("release-please")
    )


def test_release_assets_job_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives("release-assets")
    )


def test_release_assets_holds_contents_write_and_no_id_token():
    block = _permissions_block("release-assets")
    assert "contents: write" in block
    assert "id-token:" not in block


def test_release_assets_upload_names_both_a_tag_and_a_repository():
    """`GH_REPO` is not decoration. `gh` resolves its target repository from `--repo`,
    then `GH_REPO`, then the cwd's git remotes -- it does NOT read `GITHUB_REPOSITORY`.
    This job deliberately never checks out, so without `GH_REPO` the resolution chain runs
    out and the step dies before any API call, AFTER release-please has already tagged and
    published the release. Three reviewers found this independently in the design, where an
    assertion pinning only the tag was satisfied by the dead step."""
    step = _step_containing("release-assets", "gh release upload")
    assert "TAG: ${{ needs.release-please.outputs.tag_name }}" in step
    assert "GH_REPO: ${{ github.repository }}" in step
