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


def test_release_please_job_keeps_its_original_permissions():
    """A future edit accidentally copying attest's elevated permissions onto release-please --
    the job that mints the App token -- would go unnoticed without this."""
    block = _job_directives("release-please")
    assert "contents: read" in block
    assert "id-token: write" not in block
    assert "attestations: write" not in block


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
    block = _job_directives("build")
    assert "contents: read" in block
    assert "id-token: write" not in block
    assert "attestations: write" not in block


def test_build_job_runs_twine_check_strict():
    block = _job_directives("build")
    assert "twine check --strict" in block


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
    block = _job_directives("attest")
    assert "id-token: write" in block, (
        "attest needs id-token: write for attest-build-provenance's OIDC token"
    )
    assert "attestations: write" in block, (
        "attest needs attestations: write to attach the attestation"
    )


def test_build_and_attest_agree_on_the_artifact_name():
    upload_step = _step_containing("build", "actions/upload-artifact")
    download_step = _step_containing("attest", "actions/download-artifact")
    upload_name = re.search(r"name:\s*(\S+)", upload_step)
    download_name = re.search(r"name:\s*(\S+)", download_step)
    assert upload_name and download_name, "couldn't find name: in the upload/download steps"
    assert upload_name.group(1) == download_name.group(1) == "dist", (
        f"build uploads {upload_name.group(1)!r} but attest downloads "
        f"{download_name.group(1)!r} -- a rename on one side silently decouples the two jobs"
    )


def test_attest_covers_the_whole_dist_directory():
    step = _step_containing("attest", "actions/attest-build-provenance")
    assert "subject-path: dist/*" in step, (
        "attest no longer covers the whole dist/ directory in one glob -- two enumerated "
        "extensions (*.whl, *.tar.gz) could miss a third artifact type later"
    )


def test_workflow_wide_permissions_stay_read_only():
    block = _workflow_wide_directives()
    assert "contents: read" in block
    assert "id-token: write" not in block, (
        "an elevated permission leaked into the workflow-wide block -- every job in this file "
        "would silently inherit it, including release-please's own App-token-minting job"
    )
    assert "attestations: write" not in block
