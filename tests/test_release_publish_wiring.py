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
HOMEBREW_DRY_RUN = ROOT / ".github" / "workflows" / "homebrew-dry-run.yml"


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


def _step_own_directive_keys(path: Path, job: str, needle: str) -> set[str]:
    """The top-level directive keys (`name`, `uses`, `env`, `run`, `if`, ...) the step
    containing `needle` declares on ITSELF, from `_step_containing`'s comment-stripped output.

    Matched at the step's own two possible indents -- either the `- key:` that opens the step
    (six spaces plus the dash-space) or a bare `key:` one level in (eight spaces) -- and no
    deeper, so a key nested inside `env:`/`with:` (`VERSION:`, `TAP_TOKEN:`, ...) is never
    mistaken for a step-level directive.

    This is the ALLOW-LIST mechanism #104 IMPORTANT-2 calls for, in place of a substring
    probe for `if: always()`: a blocklist keyed on that one literal is wrong in both
    directions here. `if: always()` already appears in this repo's own workflow commentary
    (release-please.yml's header prose explains what it is normally FOR), so a substring probe
    would need comment-stripping just to avoid a false positive there -- and even
    comment-stripped, `${{ always() }}`, `!cancelled()` and `success() || failure()` all name a
    step-level `if:` key that a two-item blocklist keyed on the literal string `always()` never
    sees. Keying on the parsed DIRECTIVE NAME (`if`) rather than any particular VALUE closes
    every spelling at once.
    """
    step = _step_containing(path, job, needle)
    return set(re.findall(r"^(?: {6}- | {8})([a-z_-]+):", step, re.MULTILINE))


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
_RELEASE_PLEASE_JOBS = ["release-please", "build", "linux-packages", "attest", "pypi",
                        "release-assets", "docker", "attest-image", "homebrew"]
_TESTPYPI_JOBS = ["testpypi"]
_HOMEBREW_DRY_RUN_JOBS = ["dry-run"]

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


def test_homebrew_dry_run_declares_exactly_the_jobs_this_file_pins():
    """homebrew-dry-run.yml is the file holding the cross-repo write token (see
    `test_the_homebrew_dry_run_has_no_elevated_permissions`'s docstring), and until this pin
    existed nothing enumerated its job roster at all -- the same blind spot `_ROSTER_MESSAGE`
    describes for the other two files, unclosed here."""
    found = _job_names(HOMEBREW_DRY_RUN)
    assert found == _HOMEBREW_DRY_RUN_JOBS, _roster_failure(
        HOMEBREW_DRY_RUN, _HOMEBREW_DRY_RUN_JOBS, found)


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


_NFPM_FETCH_STEP = "Fetch and verify nfpm"
_VERIFY_MODES_STEP = "Verify the packaged directories stay traversable"


def test_linux_packages_job_has_no_elevated_permissions():
    """The pin `_ROSTER_MESSAGE` demands by name for every job added to the roster: "Add the
    job HERE and give it its own equality pin; extending this list alone restores the blind
    spot it exists to close." This job was added to the roster WITHOUT one, which is the
    documented mistake made verbatim.

    It matters more here than for most: `linux-packages` checks out repository source and runs
    a downloaded binary over it, so it is the natural place for someone to later add
    `contents: write` (to upload straight to the release) or `packages: write`. Either would
    falsify the workflow's own claim that `release-assets` is the ONLY holder of
    `contents: write`, and with no equality pin the whole module stays green.
    """
    assert _permissions_block(RELEASE_PLEASE, "linux-packages") == (
        "    permissions:\n      contents: read"
    ), (
        "linux-packages' permissions must be EXACTLY `contents: read`. It produces an artifact "
        "and writes nothing back; `release-assets` is the one job that uploads."
    )


def test_linux_packages_job_is_gated_on_release_created():
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "linux-packages")
    )


def test_linux_packages_job_needs_release_please_and_build_exactly():
    match = re.search(r"\n    needs: (.+)\n",
                      _job_directives(RELEASE_PLEASE, "linux-packages"))
    assert match, "the 'linux-packages' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build]", (
        f"linux-packages' needs: is no longer exactly [release-please, build], it is "
        f"{match.group(1).strip()!r} -- it consumes build's wheel and release-please's version"
    )


def test_linux_packages_checks_out_the_tagged_sha_not_the_trigger_commit():
    """nfpm.yaml, packaging/job-sluice and the staging script must come from the TAGGED tree,
    the same reason `build` and `docker` pin their refs. Checking out the trigger commit would
    package whatever happened to be on the branch when the workflow fired."""
    step = _step_containing(RELEASE_PLEASE, "linux-packages", "actions/checkout@")
    assert "ref: ${{ needs.release-please.outputs.sha }}" in step
    assert "persist-credentials: false" in step


def test_the_nfpm_download_verifies_its_checksum_before_executing_anything():
    """The step's own comment calls `sha256sum --check --strict` the load-bearing part --
    "without --check the hash would merely be PRINTED and the step would pass on any bytes at
    all". Nothing asserted that until this test: `grep -rn 'sha256sum' tests/` returned
    nothing, so deleting the flags, or hoisting the `tar` above the check, was green.

    ORDER is pinned too, not just presence. A checksum verified after the archive is already
    unpacked still fails the build, but only after untrusted bytes have been written to disk;
    and if the extraction itself were ever what produced the binary that later runs, the check
    would be decorative.
    """
    # Anchored on the step's NAME, the same way testpypi.yml's stamp-proof step is, because
    # `_run_block_scalar` slices FORWARD from its needle and then looks for `run: |` -- so the
    # needle has to sit between the step's `- ` and its `run:` key, which only a `name:` does.
    # (`NFPM_SHA256` and `sha256sum --check` each occur twice in the file besides: once in the
    # shell body and once in the env block or the comment explaining the flag.)
    body = _run_block_scalar(RELEASE_PLEASE, _NFPM_FETCH_STEP)
    assert "sha256sum --check --strict" in body, (
        "the nfpm download must VERIFY its checksum, not merely print one"
    )
    assert body.index("sha256sum --check") < body.index("tar -xzf"), (
        "the checksum must be verified BEFORE the archive is unpacked"
    )
    # Scope: a block scalar that failed to extract would make both `in` checks fail loudly,
    # but an empty one would make the ordering comparison raise rather than assert. Pin that
    # the body is the script actually shipped.
    assert "curl" in body and "NFPM_VERSION" in body, (
        f"the extracted run body does not look like the nfpm download step: {body!r}"
    )


def test_linux_packages_stages_the_wheel_and_builds_both_package_formats():
    """The job's `run:` steps, pinned as an ordered SET rather than probed individually.

    Two holes this closes, both created by an earlier revision of this PR. First, the
    assertion tying the workflow to `scripts/build_linux_packages.py` was deleted when the
    workflow probes moved out of tests/test_linux_packages_channel.py, and not replaced -- so
    the import-set guard proved the STAGER never resolves from an index while nothing proved
    the job runs it. Swapping that step for `pip install --target` was green.

    Second, and worse: nothing pinned the two `nfpm package` invocations. Deleting the `-p rpm`
    one leaves the roster, artifact-map, permissions, checkout and checksum pins all green, and
    `upload-artifact` applies `if-no-files-found` to the AGGREGATE of its `path:` patterns --
    so a deb-only run uploads, attests and publishes a release silently missing the .rpm.
    """
    runs = _post_checkout_run_steps(RELEASE_PLEASE, "linux-packages")
    # Scope: the helper returning [] would satisfy every check below. The COUNT is the half the
    # set below cannot do -- `_run_commands` renders every `run: |` block scalar as the literal
    # `"|"`, so the two block-scalar steps (fetch nfpm, verify directory modes) collapse into a
    # single set member and a deleted one would be invisible there. Each has its own test.
    assert len(runs) == 5, (
        f"expected 5 `run:` steps in linux-packages (fetch nfpm, stage, package deb, package "
        f"rpm, verify modes), found {len(runs)}: {runs}"
    )
    # EXACT commands as a SET, not substrings. A substring pin accepts appended arguments, and
    # the ones that matter here are silent: `--out build/other` or `--dist /tmp/elsewhere` on
    # the stager both leave a substring check green while the job stages somewhere nfpm never
    # looks. This module's own `_run_commands` docstring records fixing that class before, and
    # an equality also catches a `-t` respelled as `--target`, which the sibling test's
    # `re.findall(r"-t (\S+)")` cannot see. The `"|"` is the block-scalar fetch step, which
    # `_run_commands` renders that way by design and which has its own test above.
    assert _run_commands(RELEASE_PLEASE, "linux-packages") == {
        "|",
        "python scripts/build_linux_packages.py",
        "./nfpm package -f nfpm.yaml -p deb -t build/",
        "./nfpm package -f nfpm.yaml -p rpm -t build/",
    }, (
        f"linux-packages' run steps are not exactly the staging call and the two nfpm "
        f"invocations: {sorted(_run_commands(RELEASE_PLEASE, 'linux-packages'))}. A missing "
        f"nfpm invocation is SILENT -- the upload's if-no-files-found applies to the aggregate "
        f"of its globs, so the other format still uploads and the release publishes without "
        f"this one."
    )


def test_the_packaged_directories_are_verified_before_upload():
    """The only check on what nfpm actually EMITS, so it needs its own row.

    The offline suite asserts what `stage()` produces; nothing else looks at the package. That
    gap is not hypothetical -- a blanket `file_info.mode` once made nfpm strip the search bit
    from every directory it synthesised, and the package was unusable for non-root users while
    the suite stayed green and three root-only container installs reported it healthy. A
    umask-022 runner also makes the stager's chmod a no-op, so without this step CI exercises
    neither the broken nor the fixed behaviour.

    ORDER is pinned: verifying after the upload would publish the bad artefact first.
    """
    body = _run_block_scalar(RELEASE_PLEASE, _VERIFY_MODES_STEP)
    assert "dpkg-deb -c" in body and "rpm -qlvp" in body, (
        "both package formats must be inspected; checking one leaves the other unverified"
    )
    assert body.count('substr($1, 1, 10) != "drwxr-xr-x"') == 2, (
        "both loops must compare the FULL directory mode against drwxr-xr-x. A `!~ /^drwx/` "
        "test passes on drwx------, which is owner-traversable and unusable by anyone else -- "
        "the property that broke, one permission column over"
    )
    assert "exit " in body, (
        "the step must FAIL on a bad package rather than only printing -- a check whose "
        "findings do not change the exit status is the present-and-inert shape this repo "
        "keeps closing"
    )
    job = _job_directives(RELEASE_PLEASE, "linux-packages")
    assert job.index(_VERIFY_MODES_STEP) < job.index("actions/upload-artifact"), (
        "the mode check must run BEFORE the upload, or a bad package is published first"
    )


def test_the_nfpm_steps_take_their_version_from_release_please():
    """The workflow claims the package version and the git tag "cannot disagree". Nothing
    falsified that until this test: deleting `env: VERSION`, or swapping it to
    `github.ref_name`, both left the whole suite green.

    `ref_name` is the specific trap. It is the TAG (`v1.2.0`), not the version (`1.2.0`), so
    the packages would ship named `job-sluice_v1.2.0_all.deb` -- a different version string
    from the wheel and the image in the same release, and not obviously wrong at a glance.
    """
    job = _job_directives(RELEASE_PLEASE, "linux-packages")
    # Anchored on the line start so `NFPM_VERSION:` -- which ends in the same eight characters
    # -- is not swept up, and capturing to end of line because `${{ needs... }}` contains
    # spaces that `\S+` would truncate. Both were wrong in the first spelling of this line.
    versions = re.findall(r"^\s+VERSION: (.+)$", job, re.MULTILINE)
    assert versions == ["${{ needs.release-please.outputs.version }}"] * 2, (
        f"both nfpm steps must take VERSION from release-please's own `version` output, got "
        f"{versions}. `github.ref_name` is the tag (v1.2.0), not the version (1.2.0)."
    )


def test_the_packaging_output_directory_agrees_across_config_workflow_and_upload():
    """`-t build/`, nfpm.yaml's `src`, the stager's `--out` default and the upload's globs are
    four statements of one path. The module already guards the `dst`/shim pair for the same
    reason: a drift here yields a green run that packages nothing anybody uploads."""
    job = _job_directives(RELEASE_PLEASE, "linux-packages")
    upload = _step_containing(RELEASE_PLEASE, "linux-packages", "actions/upload-artifact")
    # EVERY `-t` target, compared as a set. `"-t build/" in job` was the first spelling and it
    # was inert: with two nfpm invocations, changing one to `-t out/` leaves the other's
    # `-t build/` in the job text and the substring check still passes -- so the format whose
    # target drifted is built somewhere nothing collects, and the release ships without it.
    # Caught by a mutation witness, not by review.
    targets = set(re.findall(r"-t (\S+)", job))
    assert targets == {"build/"}, (
        f"the nfpm invocations disagree on their output directory: {sorted(targets)}. Every "
        f"one must write to the directory the upload globs collect."
    )
    for glob in ("build/*.deb", "build/*.rpm"):
        assert glob in upload, (
            f"the upload does not name {glob!r}, so nfpm's `-t build/` output for that format "
            f"is produced and then never collected"
        )
    # Compared BY VALUE: the stager's default imported as a real object, and nfpm's `src` read
    # from the parsed YAML. Both halves were text matches against raw source until a review
    # round mutated them -- and nfpm.yaml's own header comment mentions the path, so
    # `"./build/linux-packages/lib" in nfpm.yaml.read_text()` was satisfied by the COMMENT and
    # stayed green when the real `src:` was changed. A guard over a file that documents itself
    # cannot be a substring probe.
    import yaml

    from scripts.build_linux_packages import DEFAULT_OUT

    tree = [entry for entry in yaml.safe_load((ROOT / "nfpm.yaml").read_text())["contents"]
            if entry.get("type") == "tree"]
    assert len(tree) == 1, f"expected exactly one `type: tree` entry in nfpm.yaml, got {tree}"
    assert tree[0]["src"].removeprefix("./") == DEFAULT_OUT.as_posix(), (
        f"nfpm.yaml packages {tree[0]['src']!r} but the stager writes to "
        f"{DEFAULT_OUT.as_posix()!r} -- nfpm would package a directory nothing produced"
    )


def test_the_packages_upload_fails_at_the_producer_rather_than_downstream():
    """Scoped to the JOB, not swept over the whole file. A whole-file substring probe passes on
    a match anywhere -- in another job's step, or in a comment saying the opposite -- which is
    how the first version of this guard was written and why it moved here, where
    `_job_directives` bounds it.

    On what `error` actually buys: the default `warn` means "output a warning but do not fail"
    per the action's own action.yml, creating NO artifact, so the run does not go green on an
    empty release -- `attest` and `release-assets` both fail at download-artifact. `error`
    moves the failure to the producer, naming the glob that matched nothing, instead of
    surfacing two jobs later as a missing-artifact error that reads like an infrastructure
    fault.
    """
    step = _step_containing(RELEASE_PLEASE, "linux-packages", "actions/upload-artifact")
    assert "if-no-files-found: error" in step, (
        "the linux-packages upload must set if-no-files-found: error"
    )


def test_the_release_upload_names_every_artifact_directory():
    """`gh release upload "$TAG" dist/* packages/*` -- both globs, pinned.

    Without this, deleting just `packages/*` ships a green release with no .deb or .rpm:
    `_JOB_ARTIFACTS` still matches because the DOWNLOAD step is untouched, and the two other
    tests over this step check only `--clobber`, `TAG` and `GH_REPO`. `attest` got a
    covers-every-directory guard; the user-facing upload had none.
    """
    block = _job_directives(RELEASE_PLEASE, "release-assets")
    marker = "\n    steps:\n"
    parts = re.split(r"\n(?=      - )", block[block.index(marker) + len(marker):])
    downloads = [part for part in parts if "actions/download-artifact" in part]
    # DERIVED from this job's own download paths, not hardcoded, which is the shape
    # `test_attest_downloads_to_every_path_it_scans` already uses and this one lacked. A
    # hardcoded pair closes only the delete-a-glob direction: RENAMING a download path leaves
    # both literals present and green, while `gh release upload` fails on a glob matching
    # nothing -- after `pypi` has already published and the release is public.
    assert len(downloads) == len(_JOB_ARTIFACTS["release-assets"]), (
        f"expected {len(_JOB_ARTIFACTS['release-assets'])} download-artifact steps in "
        f"release-assets, found {len(downloads)}"
    )
    step = _step_containing(RELEASE_PLEASE, "release-assets", "gh release upload")
    for download in downloads:
        path = re.search(r"path:\s*(\S+)", download)
        assert path, f"couldn't find path: in a release-assets download step: {download!r}"
        assert f"{path.group(1)}*" in step, (
            f"release-assets downloads to {path.group(1)!r} but its upload names no glob "
            f"covering it -- those artifacts would be fetched and then silently not published"
        )


def test_attest_job_needs_release_please_build_and_linux_packages_exactly():
    """`linux-packages` joined this list when the .deb/.rpm became attestation subjects. It is
    load-bearing, not incidental: without the dependency this job can start while those
    packages do not yet exist, and `download-artifact` would fail the release AFTER the tag is
    public -- or worse, if the download were ever made non-fatal, attest a release whose
    packages carry no provenance while its wheel does."""
    block = _job_directives(RELEASE_PLEASE, "attest")
    assert re.search(r"^\s*needs:\s*\[release-please,\s*build,\s*linux-packages\]\s*$",
                     block, re.MULTILINE), (
        "attest's needs: is no longer exactly [release-please, build, linux-packages]"
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


# Which artifact each job touches, upload or download. An `_step_containing`-based version of
# this test could not survive #104's PR 5: `attest` and `release-assets` each download TWO
# artifacts now, and that helper asserts exactly one match. Widening it to "the first match"
# would have been the silent fix -- it would still have passed while pinning only whichever
# step came first, so a rename of the SECOND artifact would decouple the jobs unnoticed.
_JOB_ARTIFACTS = {
    "build": {"dist"},
    "linux-packages": {"dist", "linux-packages"},   # downloads the wheel, uploads the packages
    "attest": {"dist", "linux-packages"},
    "pypi": {"dist"},
    "release-assets": {"dist", "linux-packages"},
    "docker": {"dist"},
}


def _artifact_names(path: Path, job: str) -> set[str]:
    """Every artifact `name:` an upload- or download-artifact step of `job` refers to.

    Returns an empty set for a job with no artifact steps rather than asserting, so the test
    below can pin the SCOPE -- which jobs have artifact steps at all -- instead of only
    checking the ones it already thought to name.
    """
    block = _job_directives(path, job)
    marker = "\n    steps:\n"
    if marker not in block:
        return set()
    parts = re.split(r"\n(?=      - )", block[block.index(marker) + len(marker):])
    names = set()
    for part in parts:
        if "actions/upload-artifact" not in part and "actions/download-artifact" not in part:
            continue
        match = re.search(r"name:\s*(\S+)", part)
        assert match, f"an artifact step in the {job!r} job declares no `name:`: {part!r}"
        names.add(match.group(1))
    return names


def test_every_job_agrees_on_the_artifact_names():
    """`build` uploads `dist`; `linux-packages` consumes it and uploads its own. Read from
    every side rather than hardcoded per job, so a rename on one side is caught instead of
    silently decoupling them.

    The mapping is compared as a whole, so it pins the SCOPE too: a job that GAINS an artifact
    step (or loses one) fails here even though every name it uses is spelled correctly. A
    per-job subset probe would have accepted `release-assets` quietly dropping its
    `linux-packages` download -- which publishes a release whose .deb and .rpm are simply
    absent, with every job green.
    """
    found = {
        job: _artifact_names(RELEASE_PLEASE, job)
        for job in _job_names(RELEASE_PLEASE)
        if _artifact_names(RELEASE_PLEASE, job)
    }
    assert found == _JOB_ARTIFACTS, (
        f"jobs disagree with the pinned artifact map. Found {found}, expected "
        f"{_JOB_ARTIFACTS}."
    )


def test_attest_covers_every_published_artifact_directory():
    step = _step_containing(RELEASE_PLEASE, "attest", "actions/attest-build-provenance")
    assert "dist/*" in step and "packages/*" in step, (
        "attest no longer covers both published directories in whole-directory globs -- "
        "enumerated extensions (*.whl, *.tar.gz, *.deb, *.rpm) could miss a further artifact "
        "type later, and every release asset must carry provenance"
    )


def test_attest_downloads_to_every_path_it_scans():
    """EVERY download path must be covered, not just the first one found.

    Now that attest downloads two artifacts, checking one would leave the other free to land
    in a directory no glob names: `attest-build-provenance` would then sign the wheel, report
    success, and the .deb and .rpm would ship unattested with nothing red anywhere.
    """
    block = _job_directives(RELEASE_PLEASE, "attest")
    marker = "\n    steps:\n"
    parts = re.split(r"\n(?=      - )", block[block.index(marker) + len(marker):])
    downloads = [part for part in parts if "actions/download-artifact" in part]
    # Scope: this loop is vacuously true over an empty list, so the count is pinned against
    # the artifact map above rather than left to whatever the split happened to yield.
    assert len(downloads) == len(_JOB_ARTIFACTS["attest"]), (
        f"expected {len(_JOB_ARTIFACTS['attest'])} download-artifact steps in attest, found "
        f"{len(downloads)}"
    )
    subject_step = _step_containing(RELEASE_PLEASE, "attest", "actions/attest-build-provenance")
    for step in downloads:
        download_path = re.search(r"path:\s*(\S+)", step)
        assert download_path, f"couldn't find path: in an attest download-artifact step: {step!r}"
        assert f"{download_path.group(1)}*" in subject_step, (
            f"attest downloads to {download_path.group(1)!r} but no subject-path glob covers "
            f"that directory -- those subjects would be silently unattested"
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


def test_release_assets_job_needs_release_please_build_and_linux_packages_exactly():
    """`pypi` and `attest` each pin their `needs:`; this job's went unpinned. It reads
    `needs.release-please.outputs.tag_name` and downloads both `build`'s and
    `linux-packages`' artifacts, so dropping any dependency makes it run before what it
    consumes exists -- and `release-please` is the easy one to lose, since the `tag_name`
    reference LOOKS like a workflow-level value rather than a job output."""
    match = re.search(r"\n    needs: (.+)\n",
                      _job_directives(RELEASE_PLEASE, "release-assets"))
    assert match, "the 'release-assets' job declares no `needs:`"
    assert match.group(1).strip() == "[release-please, build, linux-packages]", (
        f"release-assets' needs: is no longer exactly [release-please, build, "
        f"linux-packages], it is "
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
    """Step-scoped, and -- added alongside the homebrew-dry-run.yml equivalent, which found
    this same gap -- ORDER-checked too: presence of the refusal text in the right step is not
    enough on its own, since a MOVE of the whole step to the end of `steps:` (after the actual
    `pypa/gh-action-pypi-publish` upload) leaves that step-scoped assertion green while the
    guard runs after the publish it was meant to prevent. Index order over the comment-stripped
    job block is the same idiom
    `test_the_homebrew_release_job_verifies_before_minting_a_token_before_pushing` uses for the
    workflow this design is modelled on -- the pre-split `test_the_homebrew_bump_verifies_
    before_it_pushes` this cross-reference used to name indexed a single SCRIPT body instead,
    and was deleted when #104 IMPORTANT-3 split that script in two.
    """
    step = _step_containing(TESTPYPI, "testpypi", "Refuse to publish a non-default branch")
    assert "if: github.ref_name != github.event.repository.default_branch" in step
    assert "exit 1" in step

    directives = _job_directives(TESTPYPI, "testpypi")
    refusal = directives.index("Refuse to publish a non-default branch")
    publish = directives.index("pypa/gh-action-pypi-publish")
    assert refusal < publish, (
        "the branch-refusal step must PRECEDE the step that publishes to TestPyPI -- a refusal "
        "that runs after the publish has already fired guards nothing"
    )


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
    so cannot mint an OIDC token however hostile a dependency is.

    Nor is it that "every write-holding job holds exactly one KIND of write" -- an earlier
    version of this docstring said so, and that is a grouping rather than a property of the
    file: `attest` and this job each hold TWO write scopes. It is the third wrong reason given
    for a decision that is nonetheless right, which is worth stating so the next reader does not
    reach for a fourth.

    The basis that survives is what this suite ENFORCES: `docker` holds no `id-token`, this job
    holds no `packages`, every job in the file carries an exact `_permissions_block` equality
    pin, and `_RELEASE_PLEASE_JOBS` pins the roster -- so a job holding a registry credential
    AND an OIDC identity cannot land without a human editing both pins on purpose."""
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
    "_text", "_job_directives", "_step_containing", "_step_own_directive_keys",
    "_permissions_block", "_workflow_wide_directives", "_post_checkout_run_steps",
    "_run_commands", "_publish_action_ref", "_python_version", "_job_names",
    "_artifact_retention_days", "_roster_failure", "_run_block_scalar", "_channel_table_rows",
    "_artifact_names",
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


# ---------------------------------------------------------------------------
# The README's channel-status table (#104).
#
# WHY A TABLE AND NOT A SWEEP OVER THE PROSE. This guard exists because README.md asserted
# "there is no Docker image" in two places for a day after the Docker channel shipped in
# 1.1.0, with nothing in the suite able to notice. The obvious fix -- sweep shipped prose for
# a negation near a channel name -- is the shape this codebase keeps getting burned by: a
# heuristic whose failure mode is SILENCE (a phrasing it does not match passes), and one
# needing an allow-list besides, because README.md and docs/TROUBLESHOOTING.md both mention
# Homebrew legitimately when telling a macOS user where cairo/pango come from. So the drift
# SURFACE is removed instead: availability is claimed in exactly one machine-readable place,
# and the prose links to it rather than restating it.
#
# The derivation is bidirectional. A channel the workflow produces but the table does not mark
# shipped fails; a channel the table marks shipped that no job produces fails too.
# ---------------------------------------------------------------------------

README = ROOT / "README.md"

_CHANNEL_TABLE_MARKER = "<!-- channel-status -->"

# A CLOSED vocabulary. Without this, a typo'd status ("shippped") silently reads as
# not-shipped -- the table would go quietly wrong in exactly the direction it exists to catch.
_CHANNEL_STATUSES = {"shipped", "planned"}

# Which roster jobs PUBLISH something a user can install from, and the label the table gives
# each. The other five jobs build, sign or upload -- they produce no channel of their own.
_CHANNEL_JOBS = {"pypi": "PyPI", "docker": "Docker", "linux-packages": "deb / rpm",
                 "homebrew": "Homebrew"}
_NON_CHANNEL_JOBS = {"release-please", "build", "attest", "release-assets", "attest-image"}


def _channel_table_rows(path: Path) -> dict[str, str]:
    """{channel label: status} from the marked table in `path`.

    Anchored on a marker comment rather than "the first table in the file", so that adding an
    unrelated table above it cannot silently retarget this parser at the wrong rows -- the
    same reason `_job_names` bounds on a literal `\\njobs:\\n` instead of a positional guess.

    Asserts the marker is present exactly once rather than returning `{}` when it is absent.
    A parser that answers "no rows" for a missing table is the fail-open shape CLAUDE.md
    names: every assertion made over an empty mapping passes, so deleting the table would
    delete the guard along with it and nothing would go red.
    """
    text = _text(path)
    assert text.count(_CHANNEL_TABLE_MARKER) == 1, (
        f"{path.name} must carry exactly one {_CHANNEL_TABLE_MARKER} marker, found "
        f"{text.count(_CHANNEL_TABLE_MARKER)}. It anchors the channel-status table that is "
        f"the single place this repo states which install channels exist."
    )
    rows: dict[str, str] = {}
    reached = False
    for line in text.split(_CHANNEL_TABLE_MARKER, 1)[1].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if reached:
                break          # the first non-row line after the table ends it
            continue
        reached = True
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        assert len(cells) == 3, (
            f"every row of {path.name}'s channel-status table must have three cells "
            f"(channel, status, install), got {len(cells)}: {stripped!r}"
        )
        channel, status = cells[0], cells[1]
        if channel.casefold() == "channel" or set(channel) <= set("-: "):
            continue           # the header row, or the alignment row under it
        rows[channel] = status
    return rows


def test_every_release_job_is_classified_as_channel_or_infrastructure():
    """The SCOPE half of this guard, and the half that makes it survive a new channel.

    `_CHANNEL_JOBS` is what the table is derived FROM, so a job absent from both mappings is a
    channel nothing compares the table against. Adding a `homebrew` job without touching this
    file would otherwise leave the table's "Homebrew | planned" row standing and correct-
    looking -- the exact defect this whole section exists to prevent, one release later.
    """
    jobs = set(_job_names(RELEASE_PLEASE))
    assert set(_CHANNEL_JOBS) | _NON_CHANNEL_JOBS == jobs, (
        f"every job in {RELEASE_PLEASE.name} must be classified as either publishing a "
        f"channel (_CHANNEL_JOBS) or not (_NON_CHANNEL_JOBS). Unclassified: "
        f"{sorted(jobs - set(_CHANNEL_JOBS) - _NON_CHANNEL_JOBS)}; named here but absent from "
        f"the workflow: {sorted((set(_CHANNEL_JOBS) | _NON_CHANNEL_JOBS) - jobs)}."
    )
    assert not (set(_CHANNEL_JOBS) & _NON_CHANNEL_JOBS), (
        "a job cannot be both a channel publisher and infrastructure: "
        f"{sorted(set(_CHANNEL_JOBS) & _NON_CHANNEL_JOBS)}"
    )


def test_readme_channel_table_parses_with_a_closed_status_vocabulary():
    rows = _channel_table_rows(README)
    # Scope, again: at least the number of produced channels, so a table that parsed down to
    # one stray row cannot satisfy the equality test below by accident.
    assert len(rows) >= len(_CHANNEL_JOBS), (
        f"README.md's channel-status table parsed to {len(rows)} row(s): {rows}. It must list "
        f"at least the {len(_CHANNEL_JOBS)} channel(s) the release workflow produces."
    )
    unknown = set(rows.values()) - _CHANNEL_STATUSES
    assert not unknown, (
        f"README.md's channel-status table uses status(es) {sorted(unknown)}, outside the "
        f"closed vocabulary {sorted(_CHANNEL_STATUSES)}. A status this guard does not "
        f"recognise reads as not-shipped and fails open."
    )


def test_readme_marks_shipped_exactly_the_channels_the_release_workflow_produces():
    """Both directions at once. `==` not `<=`: a subset probe would accept a table that
    silently dropped a shipped channel, and a superset one would accept a channel claimed as
    shipped that no job builds -- an install instruction pointing at nothing.
    """
    rows = _channel_table_rows(README)
    shipped = {channel for channel, status in rows.items() if status == "shipped"}
    assert shipped == set(_CHANNEL_JOBS.values()), (
        f"README.md marks {sorted(shipped)} shipped; {RELEASE_PLEASE.name} produces "
        f"{sorted(_CHANNEL_JOBS.values())}. Claimed but not built: "
        f"{sorted(shipped - set(_CHANNEL_JOBS.values()))}; built but not claimed: "
        f"{sorted(set(_CHANNEL_JOBS.values()) - shipped)}."
    )


def test_the_homebrew_job_has_no_elevated_permissions():
    """Every job in this file carries an exact `_permissions_block` equality pin -- a property
    `attest-image`'s own comment asserts and uses to argue that no future job can hold a
    registry credential and an OIDC identity together. A roster entry WITHOUT this pin is
    worse than useless: `_ROSTER_MESSAGE` says extending the list alone restores the blind
    spot it exists to close.

    `contents: read` and nothing else. The cross-repo write is a SCOPED APP TOKEN for a
    different repository, which is why `release-assets` remains the only holder of
    `contents: write` on this workflow's GITHUB_TOKEN.
    """
    assert _permissions_block(RELEASE_PLEASE, "homebrew") == (
        "    permissions:\n      contents: read"
    )


def test_the_homebrew_job_is_gated_on_release_created():
    """`homebrew` was the only one of the eight roster jobs downstream of `release-please`
    without this pin -- `build`, `attest`, `linux-packages`, `pypi`, `release-assets`, `docker`
    and `attest-image` each have their own copy above. The workflow's `if:` line was already
    correct; nothing in this file asserted it, so deleting it left the whole suite green."""
    assert (
        "if: success() && needs.release-please.outputs.release_created == 'true'"
        in _job_directives(RELEASE_PLEASE, "homebrew")
    )


def test_the_homebrew_job_runs_on_macos():
    """Not a preference. The payoff -- WeasyPrint resolving cairo/pango with no
    DYLD_FALLBACK_LIBRARY_PATH -- comes from Homebrew's CPython patching ctypes' dyld
    fallback, a macOS mechanism. A Linux runner would verify nothing this channel is for."""
    assert "runs-on: macos-latest" in _job_directives(RELEASE_PLEASE, "homebrew")


def test_the_homebrew_job_waits_for_the_pypi_upload():
    """The formula's `url` is a PyPI sdist; it cannot resolve before that upload lands. This
    is the workflow's first cross-channel dependency and it is load-bearing, not stylistic."""
    directives = _job_directives(RELEASE_PLEASE, "homebrew")
    assert re.search(r"needs:\s*\[release-please,\s*pypi\]", directives), directives


def test_the_homebrew_release_job_verifies_before_minting_a_token_before_pushing():
    """THE GATE, restated across the two-script split (#104 IMPORTANT-3) that replaced the
    single homebrew_bump.sh: a cross-repo `contents: write` token for the tap must not be live
    while `brew install --build-from-source` runs the pip/PEP 517 build backend of every one of
    the ~45 resource sdists in the closure. Asserted by INDEX ORDER over the WORKFLOW's steps
    -- verify, THEN the token mint, THEN push -- the same idiom an earlier, single-script
    version of this test used for offsets inside one file, now applied one level up because the
    ordering it protects moved from within a script to between workflow steps.

    Comment-stripped via `_job_directives`, so a comment mentioning a step's command ahead of
    its real position cannot satisfy this.
    """
    directives = _job_directives(RELEASE_PLEASE, "homebrew")
    verify = directives.index("bash .github/scripts/homebrew_verify.sh")
    mint = directives.index("actions/create-github-app-token")
    push = directives.index("bash .github/scripts/homebrew_push.sh")
    assert verify < mint < push, (
        f"the homebrew job must render/audit/install/test the formula BEFORE minting the tap "
        f"token, and mint the token BEFORE pushing; got offsets verify={verify}, mint={mint}, "
        f"push={push}. A token minted earlier is live in the environment of the untrusted "
        f"`brew install --build-from-source` step -- exactly the exposure the split closes."
    )


def test_the_homebrew_dry_run_verifies_before_minting_a_token_before_pushing():
    """The dry run's own copy of the ordering pin above -- it drives the identical two scripts
    (see `test_the_homebrew_dry_run_drives_the_same_verify_and_push_scripts_as_the_release_job`
    below), so an ordering bug here is the same token exposure on the file that ALSO holds the
    cross-repo write token during a manually-dispatched run."""
    directives = _job_directives(HOMEBREW_DRY_RUN, "dry-run")
    verify = directives.index("bash .github/scripts/homebrew_verify.sh")
    mint = directives.index("actions/create-github-app-token")
    push = directives.index("bash .github/scripts/homebrew_push.sh")
    assert verify < mint < push, (
        f"the dry-run job must render/audit/install/test the formula BEFORE minting the tap "
        f"token, and mint the token BEFORE pushing; got offsets verify={verify}, mint={mint}, "
        f"push={push}."
    )


def test_the_homebrew_verify_script_updates_audits_installs_and_tests_in_order():
    """THE GATE, restated over the SCRIPT bodies -- a DIFFERENT layer from the two
    workflow-step-order pins immediately above, and not a substitute for them. Those pins
    assert only that the WORKFLOW's verify STEP precedes its push STEP; a verify step that
    runs and reports success having done nothing (its `brew install`/`brew test` lines
    deleted) still precedes the push step, so that pair alone would stay green while the
    property this whole channel rests on -- 'nothing is ever pushed to the tap that was not
    just installed and tested' -- silently stops holding. This is the split's own replacement
    for the pre-split `test_the_homebrew_bump_verifies_before_it_pushes`, which indexed the
    single homebrew_bump.sh script the same way before #104 IMPORTANT-3 split it in two.

    Verified by execution, each via a `cp`-backed delete-then-restore (never `git checkout`,
    per CLAUDE.md's mutation-testing section): deleting the `brew test ...` line from
    homebrew_verify.sh, and separately deleting the `brew install --build-from-source ...`
    line, both left the full suite green before this test existed.

    Comment-stripped before the offsets are computed -- the same idiom
    `test_the_homebrew_verify_script_bypasses_the_release_cooldown` already applies to this
    file -- so a comment mentioning one of these commands ahead of its real position cannot
    invert the measured order with nothing about the script's actual behaviour changed.
    """
    script = (ROOT / ".github" / "scripts" / "homebrew_verify.sh").read_text()
    body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    update = body.index("brew update-python-resources")
    audit = body.index("brew audit --strict --online")
    install = body.index("brew install --build-from-source")
    test = body.index("brew test ")
    assert update < audit < install < test, (
        f"homebrew_verify.sh must update-python-resources, THEN audit, THEN install, THEN "
        f"test the formula, in that order; got offsets update={update}, audit={audit}, "
        f"install={install}, test={test}. A formula pushed after skipping any of these steps "
        f"is exactly the deb/rpm failure this channel exists not to repeat."
    )


def test_the_homebrew_verify_script_reseats_the_tap_checkout_before_rendering():
    """The tap CHECKOUT, not just the branch NAME -- a different property from
    `test_the_homebrew_verify_script_updates_audits_installs_and_tests_in_order` above and not
    covered by it.

    The clone guard deliberately tolerates a PRE-EXISTING tap checkout (this job's own prior
    run, or a prior dry run's), which can be sitting on a `bump-X.Y.Z` scratch commit. Without
    the fetch-and-reseat pair pinned here, homebrew_push.sh's own `checkout -B "$TARGET_BRANCH"`
    resets that branch to whatever local HEAD happens to be -- so on PUSH_TARGET=default it
    would reset the tap's REAL default branch onto a leftover scratch commit, and push.sh's
    fast-forward push accepts it. A dry run's history silently becomes the tree of record:
    the exact orphan-branch harm PUSH_TARGET exists to prevent, reached through the base COMMIT
    rather than the branch NAME.

    Verified by execution, via a `cp`-backed delete-then-restore (never `git checkout`, per
    CLAUDE.md's mutation-testing section): deleting BOTH reseat lines from homebrew_verify.sh
    left the full suite green before this test existed.

    Both lines are pinned, not just the checkout: `checkout -B ... "origin/$DEFAULT_BRANCH"`
    against a stale remote-tracking ref reseats onto an old tip, so the fetch is half the
    property. The ORDER against the render step matters too -- reseating AFTER the formula is
    written would check out over the rendered file (or abort), so the render must come last.

    Comment-stripped before the offsets are computed, the same idiom as the sibling order pin
    above, so a comment naming one of these commands ahead of its real position cannot invert
    the measured order with nothing about the script's actual behaviour changed.
    """
    script = (ROOT / ".github" / "scripts" / "homebrew_verify.sh").read_text()
    body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    fetch = 'fetch origin "$DEFAULT_BRANCH"'
    reseat = 'checkout -B "$DEFAULT_BRANCH" "origin/$DEFAULT_BRANCH"'
    render_call = 'python3 - "$SDIST_URL"'
    for needle in (fetch, reseat, render_call):
        assert needle in body, (
            f"homebrew_verify.sh no longer contains {needle!r}. Without the fetch/reseat pair "
            f"the tap checkout can still be sitting on a prior run's scratch commit when "
            f"homebrew_push.sh resets the default branch onto it -- a dry run's history "
            f"becoming the tree of record, with the job reporting green."
        )
    fetch_at = body.index(fetch)
    reseat_at = body.index(reseat)
    render_at = body.index(render_call)
    assert fetch_at < reseat_at < render_at, (
        f"homebrew_verify.sh must fetch the tap's default branch, THEN reseat the checkout on "
        f"origin/$DEFAULT_BRANCH, THEN render the formula; got offsets fetch={fetch_at}, "
        f"reseat={reseat_at}, render={render_at}. A reseat after the render checks out over "
        f"the file just written; a reseat that never happens leaves push.sh resetting the "
        f"tap's default branch onto a leftover scratch commit."
    )


def test_the_homebrew_push_script_actually_pushes():
    """The other half of the same gate, on the OTHER script: a push step that runs to
    completion without ever invoking `git push` reports success while the tap never changes
    at all -- the channel becomes a silent no-op, indistinguishable from a real release in the
    job's own logs.

    Verified by execution: replacing BOTH `git -C "$TAP_DIR" push ...` invocations below
    (the fast-forward-only push for the default branch, and the --force-with-lease push for a
    scratch branch) with `:` left the full suite green before this test existed -- and so
    would neutering only ONE of the two, which is why this counts rather than merely checking
    `in`.

    Comment-stripped, same idiom as the sibling script's pin above.
    """
    script = (ROOT / ".github" / "scripts" / "homebrew_push.sh").read_text()
    body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    pushes = re.findall(r'git -C "\$TAP_DIR" push\b[^\n]*', body)
    assert len(pushes) == 2, (
        f"homebrew_push.sh must contain exactly two real `git -C \"$TAP_DIR\" push ...` "
        f"invocations -- the fast-forward-only push for the default branch and the "
        f"--force-with-lease push for a scratch branch. Found {len(pushes)}: {pushes}. A "
        f"push replaced with `:` (or deleted) reports success while never updating the tap."
    )


def test_the_homebrew_release_verify_step_carries_no_token_and_targets_the_default_branch():
    """The token must not be reachable from the step that runs the untrusted build -- checked
    directly on that step's OWN `env:`, not merely on step order above, since order alone would
    not catch a future edit that widened the verify step's env to include TAP_TOKEN anyway.
    PUSH_TARGET: default is the other half of #104 CRITICAL-2: the release job must ALWAYS land
    on the tap's default branch, never the `auto` bootstrap-or-scratch behaviour the dry run
    uses -- `brew install` resolves a tap's default branch, and nothing merges a scratch
    bump-X.Y.Z branch.
    """
    step = _step_containing(RELEASE_PLEASE, "homebrew", "homebrew_verify.sh")
    assert "TAP_TOKEN" not in step, (
        "the verify step's env carries TAP_TOKEN -- it runs "
        "`brew install --build-from-source`, which executes every resource sdist's build "
        "backend, and a live cross-repo write token there is exactly the exposure #104 "
        "IMPORTANT-3 closes"
    )
    assert re.search(r"^\s*PUSH_TARGET: default\s*$", step, re.MULTILINE), (
        f"the release job's verify step must set PUSH_TARGET: default exactly. Step:\n{step}"
    )


def test_the_homebrew_release_push_step_carries_the_token():
    step = _step_containing(RELEASE_PLEASE, "homebrew", "homebrew_push.sh")
    assert "TAP_TOKEN: ${{ steps.tap-token.outputs.token }}" in step, (
        f"the release job's push step no longer receives TAP_TOKEN from the minted App "
        f"token. Step:\n{step}"
    )


# #104 IMPORTANT-2. The two workflow-step-order pins near the top of this section (verify <
# mint < push) hold the property "never push an unverified formula" only TOGETHER with one
# unstated GitHub Actions convention: a step carrying no `if:` runs only when every prior step
# in the job succeeded. `if: always()` (or any other spelling that names `always()`,
# `cancelled()` or an OR of status functions) on the push step defeats that silently -- Actions
# then executes it even after a failed verify step, and every order-only pin in this file stays
# green regardless, because it never inspects whether a step is conditioned to run on failure.
# An ALLOW-LIST of the push step's OWN directive keys is what closes this: see
# `_step_own_directive_keys`'s docstring for why a blocklist keyed on `always()` is wrong in
# both directions on these two files specifically.
_PUSH_STEP_ALLOWED_KEYS = {"name", "env", "run"}


def test_the_homebrew_release_push_step_carries_no_if_key():
    """Verified by execution: adding `if: ${{ always() }}` to this step left the full suite
    (before this test existed) green, including both workflow-step-order pins above."""
    keys = _step_own_directive_keys(RELEASE_PLEASE, "homebrew", "homebrew_push.sh")
    assert keys, "found no directive keys at all on the push step; the matcher is broken"
    assert keys <= _PUSH_STEP_ALLOWED_KEYS, (
        f"the release job's push step carries unexpected directive key(s) "
        f"{sorted(keys - _PUSH_STEP_ALLOWED_KEYS)}. An `if:` key here in particular would let "
        f"the step run even after a failed verify step, silently defeating 'never push an "
        f"unverified formula'. Keys found: {sorted(keys)}"
    )


def test_the_homebrew_dry_run_verify_step_carries_no_token_and_targets_auto():
    """The dry run's copy of the same two-part pin above, with the OTHER value: PUSH_TARGET:
    auto is what lets this workflow's first-ever run bootstrap the tap's default branch and
    every later run land on a scratch branch instead -- see homebrew_verify.sh's own
    PUSH_TARGET comment for why one caller needs `default` and the other needs `auto`."""
    step = _step_containing(HOMEBREW_DRY_RUN, "dry-run", "homebrew_verify.sh")
    assert "TAP_TOKEN" not in step, (
        "the dry run's verify step carries TAP_TOKEN -- see the release job's identical "
        "guard for why that must never happen"
    )
    assert re.search(r"^\s*PUSH_TARGET: auto\s*$", step, re.MULTILINE), (
        f"the dry run's verify step must set PUSH_TARGET: auto exactly. Step:\n{step}"
    )


def test_the_homebrew_dry_run_push_step_carries_the_token():
    step = _step_containing(HOMEBREW_DRY_RUN, "dry-run", "homebrew_push.sh")
    assert "TAP_TOKEN: ${{ steps.tap-token.outputs.token }}" in step, (
        f"the dry run's push step no longer receives TAP_TOKEN from the minted App token. "
        f"Step:\n{step}"
    )


def test_the_homebrew_dry_run_push_step_carries_no_if_key():
    """The dry run's copy of the release job's identical pin above -- this is the file holding
    the cross-repo write token during a manually-dispatched run, so the same silent-defeat
    shape applies here too. Verified by execution: adding `if: ${{ always() }}` to this step
    left the full suite (before this test existed) green."""
    keys = _step_own_directive_keys(HOMEBREW_DRY_RUN, "dry-run", "homebrew_push.sh")
    assert keys, "found no directive keys at all on the push step; the matcher is broken"
    assert keys <= _PUSH_STEP_ALLOWED_KEYS, (
        f"the dry run's push step carries unexpected directive key(s) "
        f"{sorted(keys - _PUSH_STEP_ALLOWED_KEYS)}. An `if:` key here in particular would "
        f"silently defeat 'never push an unverified formula'. Keys found: {sorted(keys)}"
    )


# Both workflows' verify steps, in one loop: the value and the reason are identical in each,
# and a pin that covered only one would leave the other free to drift back to a hardcoded owner.
_VERIFY_STEP_OWNERS = ((RELEASE_PLEASE, "homebrew"), (HOMEBREW_DRY_RUN, "dry-run"))


def test_both_homebrew_verify_steps_pass_the_repository_owner():
    """TAP_OWNER is the ONE derivation of the tap's owner. homebrew_verify.sh lower-cases the
    value this step supplies and exports it via $GITHUB_ENV, so homebrew_push.sh reads the same
    normalised string rather than repeating the transform -- and both scripts compute TAP_DIR
    from it identically. Before that, each script carried its own hardcoded, independently
    lower-cased owner literal, unrelated to the `owner:` the App-token step mints against: two
    derivations of one fact, which can silently disagree.

    Unpinned, deleting this `env:` key leaves the suite green while both scripts die on
    `: "${TAP_OWNER:?}"` -- loud at runtime, but only once a release is already half-published,
    and every sibling wiring fact on these steps (PUSH_TARGET, TAP_TOKEN's absence, step order,
    the push step's no-`if:` allow-list) is pinned here already.

    Pinned to `${{ github.repository_owner }}` specifically, not merely to the key's presence:
    that is the same expression the create-github-app-token step passes as `owner:`, and a
    literal owner here would re-open the two-derivations drift this key exists to close.
    """
    assert _VERIFY_STEP_OWNERS, "the roster is empty; the loop below would pass vacuously"
    for path, job in _VERIFY_STEP_OWNERS:
        step = _step_containing(path, job, "homebrew_verify.sh")
        assert re.search(r"^\s*TAP_OWNER: \$\{\{ github\.repository_owner \}\}\s*$",
                         step, re.MULTILINE), (
            f"{path.name}'s `{job}` verify step must pass "
            f"TAP_OWNER: ${{{{ github.repository_owner }}}} -- the same fact the App-token step "
            f"mints against, derived once rather than hardcoded a second time inside the shell "
            f"scripts. Step:\n{step}"
        )


def test_the_homebrew_verify_script_fails_loudly_on_an_invalid_push_target():
    """This project's rule is 'fail loudly at construction; never fall through to a default'.
    PUSH_TARGET replaced an INFERRED observable (whether Formula/job-sluice.rb exists in the
    tap) that could not tell the release job and the dry run apart -- see homebrew_verify.sh's
    own header comment for the incident that motivated this. An invalid or unset value must
    fail before any of the expensive render/audit/install/test work below it runs, and must
    NAME the two valid values.

    Executed for real, not merely grepped -- the same idiom
    `test_the_stamp_proof_actually_refuses_a_dist_the_stamp_never_reached` (in this same file)
    uses to run a workflow step's own script body rather than trust that its text implies its
    behaviour. An empty PATH keeps this hermetic: the case statement this test pins is ordinary
    bash builtins (`case`, `echo`, `exit`) and sits before the script's first external command,
    so a failure here proves the guard fires at that point rather than later against network or
    `brew`.
    """
    script = ROOT / ".github" / "scripts" / "homebrew_verify.sh"
    bash = shutil.which("bash")
    assert bash, "bash is required to execute the script this test pins"

    for push_target in (None, "bogus", "Default", "AUTO"):
        env = {"VERSION": "1.2.3", "PATH": ""}
        if push_target is not None:
            env["PUSH_TARGET"] = push_target
        proc = subprocess.run(
            [bash, str(script)], env=env, capture_output=True, text=True, timeout=30,
        )
        output = proc.stdout + proc.stderr
        assert proc.returncode != 0, (
            f"PUSH_TARGET={push_target!r} was accepted; the script must fail on anything "
            f"other than exactly 'default' or 'auto'. Output: {output!r}"
        )
        assert "default" in output and "auto" in output, (
            f"the failure for PUSH_TARGET={push_target!r} does not name both valid values "
            f"('default', 'auto'): {output!r}"
        )


def test_the_homebrew_verify_script_bypasses_the_release_cooldown():
    """This job runs minutes after the PyPI upload, and `brew update-python-resources` otherwise
    refuses a package that new. Homebrew honours the flag for non-official taps only -- ours is
    non-official, so it applies. Without it the job fails at EVERY release.

    Comment-stripped before the assertion: the script's own explanatory comment above the real
    command contains this exact flag literal, so grepping the raw text would stay green even if
    the flag were deleted from the actual `brew update-python-resources` invocation.
    """
    script = (ROOT / ".github" / "scripts" / "homebrew_verify.sh").read_text()
    body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "--ignore-main-package-cooldown" in body


# bash 4.0-only constructs. macOS ships bash 3.2.57 -- the last GPLv2 release -- and Apple has
# never shipped a newer one, so a `macos-latest` runner executes these scripts under 3.2. Both
# workflows invoke them as `bash <path>`, which BYPASSES the `#!/usr/bin/env bash` shebang and
# uses /bin/bash, so a Homebrew bash 5 on a developer's machine does not save it either.
#
# THIS GUARD EXISTS BECAUSE NEITHER TOOL WE ALREADY RUN CATCHES IT, measured not assumed:
# `/bin/bash -n` under 3.2 PARSES `${VAR,,}` without complaint (it fails at expansion time, not
# parse time), and `shellcheck` reports nothing even at `--severity=style`, since it targets a
# modern bash by default. So `${TAP_OWNER,,}` passed local lint, passed CI, passed a
# five-specialist review and a CodeRabbit round, and died on the first real dispatch with
# `bad substitution` -- before the token was minted, which is the part that went right.
#
# Each row carries its remedy: a guard that only says "no" sends the next author hunting for
# the answer this comment already has.
_BASH4_ONLY = (
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^?\}", "${VAR^^}/${VAR^}", "tr '[:lower:]' '[:upper:]'"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*,,?\}", "${VAR,,}/${VAR,}", "tr '[:upper:]' '[:lower:]'"),
    (r"\bdeclare\s+-A\b", "declare -A", "parallel arrays, or a temp file"),
    (r"\blocal\s+-A\b", "local -A", "parallel arrays, or a temp file"),
    (r"\bmapfile\b", "mapfile", "while IFS= read -r"),
    (r"\breadarray\b", "readarray", "while IFS= read -r"),
    (r"&>>", "&>>", ">>file 2>&1"),
    (r"\[\[\s+-v\s", "[[ -v VAR ]]", '[ -n "${VAR+x}" ]'),
    (r"\bcoproc\b", "coproc", "a fifo, or restructure"),
)

_MACOS_SHELL_SCRIPTS = ("homebrew_push.sh", "homebrew_verify.sh")


def test_the_homebrew_scripts_use_no_bash_4_only_constructs():
    """Every script a macos-latest runner executes must parse AND EXPAND under bash 3.2.

    SCOPE FIRST: a glob matching nothing would make the loop below vacuously true, and this
    guard would report success having checked no file at all.
    """
    scripts_dir = ROOT / ".github" / "scripts"
    found = tuple(sorted(p.name for p in scripts_dir.glob("homebrew_*.sh")))
    assert found == _MACOS_SHELL_SCRIPTS, (
        f"the bash-3.2 sweep enumerated {list(found)}, expected {list(_MACOS_SHELL_SCRIPTS)}. "
        f"A script added beside these would run on macOS unswept."
    )
    for name in _MACOS_SHELL_SCRIPTS:
        # Comments may DISCUSS these constructs -- the block above does -- so strip whole-line
        # comments first, the same way this file's order pins do.
        body = "\n".join(
            ln for ln in (scripts_dir / name).read_text().splitlines()
            if not ln.lstrip().startswith("#")
        )
        for pattern, construct, remedy in _BASH4_ONLY:
            hit = re.search(pattern, body)
            assert hit is None, (
                f"{name} uses {construct}, which macOS bash 3.2 cannot expand: found "
                f"{hit.group(0)!r}. Use {remedy} instead. Neither `bash -n` nor shellcheck "
                f"catches this -- both were measured against it -- so this sweep is the guard."
            )


def test_the_homebrew_scripts_never_use_tap_new():
    """`brew tap-new` unconditionally writes .github/dependabot.yml and three workflows -- only
    `git init` is behind --no-git (dev-cmd/tap-new.rb:95-99). One runs `brew bump --open-pr`
    daily, which would be a SECOND automated writer of a formula this design declares
    machine-owned. Independently, an App token scoped `contents: write` cannot push anything
    under .github/workflows/, so such a bootstrap would fail at the push regardless. Both
    scripts are checked -- `tap-new` would only ever make sense in the verify half, but a
    forbidden-pattern sweep that only looked at one file after a split is the exact "enumerate
    both ends" gap CLAUDE.md names."""
    for name in ("homebrew_verify.sh", "homebrew_push.sh"):
        script = (ROOT / ".github" / "scripts" / name).read_text()
        body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
        assert "tap-new" not in body, f"{name} must never call `brew tap-new`"


def test_the_homebrew_dry_run_refuses_a_non_default_branch():
    """It pushes to a PUBLIC tap, so an unmerged branch must not become the tree of record.
    `testpypi.yml` carries the identical guard for the identical reason.

    Step-SCOPED via `_step_containing`, the same helper `test_testpypi_refuses_a_non_default_branch`
    uses -- a whole-file substring probe is close to meaningless here: it stays green even after
    RELOCATING the entire refusal step to the end of `steps:`, after the checkout, the version
    resolve, the verify step, the token mint and the push itself, at which point the guard is
    inert because the push has already happened.

    Order is the second half, asserted separately by INDEX over the comment-stripped job block
    -- the same idiom the ordering tests above use. Presence of the right text in the right
    step is not enough on its own: the refusal step's own name must come BEFORE the step that
    invokes homebrew_verify.sh, or a relocation-to-the-end mutation (a MOVE, not a delete)
    passes the step-scoped assertions above while still letting the push run unguarded.
    """
    step = _step_containing(HOMEBREW_DRY_RUN, "dry-run", "Refuse to bump the tap")
    assert "if: github.ref_name != github.event.repository.default_branch" in step
    assert "exit 1" in step

    directives = _job_directives(HOMEBREW_DRY_RUN, "dry-run")
    refusal = directives.index("Refuse to bump the tap from a non-default branch")
    verify = directives.index("bash .github/scripts/homebrew_verify.sh")
    assert refusal < verify, (
        "the branch-refusal step must PRECEDE the step that invokes homebrew_verify.sh -- a "
        "refusal that runs after the render/audit/install/test (and eventual push) has "
        "already fired guards nothing"
    )


def test_the_homebrew_dry_run_has_no_elevated_permissions():
    """This is the file holding the cross-repo write token, and NOTHING forces it to be pinned:
    no test globs .github/workflows, so a fourth workflow file is invisible to the suite unless
    someone writes its pins deliberately."""
    assert _permissions_block(HOMEBREW_DRY_RUN, "dry-run") == (
        "    permissions:\n      contents: read"
    )


def test_the_homebrew_dry_run_triggers_only_on_workflow_dispatch():
    """A `push:` trigger added alongside `workflow_dispatch:` keeps every OTHER test in this
    module green -- this workflow pushes to a PUBLIC tap, so an auto-fire trigger is worse here
    than in `testpypi.yml`, which it is modelled on. Reuses that file's own `on:`-bounding
    logic rather than reinventing it: see `test_testpypi_triggers_only_on_workflow_dispatch`'s
    docstring for why the region must be bounded at the next ZERO-indent key -- `permissions:`
    sits at zero indent too, and its own `contents:` child sits at the same two-space indent as
    a trigger, so an unbounded slice would count `contents` as a second trigger even on this
    exact, CORRECT file.
    """
    block = _workflow_wide_directives(HOMEBREW_DRY_RUN)
    on_match = re.search(r"^on:", block, re.MULTILINE)
    assert on_match, "homebrew-dry-run.yml has no top-level `on:` key"
    rest = block[on_match.start() :]
    end = re.search(r"\n[a-z]", rest)
    on_block = rest[: end.start()] if end else rest
    triggers = re.findall(r"\n  ([a-z_]+):", on_block)
    assert triggers == ["workflow_dispatch"]


def test_the_homebrew_dry_run_workflow_wide_permissions_are_read_only():
    """Only the job-level block was pinned by the first cut of this file -- an elevation at the
    workflow-wide `permissions:` combined with a job that stopped overriding it would go
    unseen. Exactly the slicing `test_testpypi_workflow_wide_permissions_are_read_only` uses,
    pointed at this file instead."""
    block = _workflow_wide_directives(HOMEBREW_DRY_RUN)
    idx = block.index("\npermissions:\n")
    perm_block = block[idx + 1 :]
    assert perm_block == "permissions:\n  contents: read", (
        f"homebrew-dry-run.yml's workflow-wide permissions must be EXACTLY `contents: read`. "
        f"Got: {perm_block!r}"
    )


def test_the_homebrew_dry_run_drives_the_same_verify_and_push_scripts_as_the_release_job():
    """Two scripts, two callers each. A dry run exercising a DIFFERENT path from the release
    job would prove nothing about the release job -- which is the entire purpose of running
    it. Both invocations are checked for both scripts: after the #104 IMPORTANT-3 split, a
    drift where only ONE of the pair stayed shared would leave half the chain unproven by the
    dry run while still reading as "the same script, two callers"."""
    for invocation in (
        "bash .github/scripts/homebrew_verify.sh",
        "bash .github/scripts/homebrew_push.sh",
    ):
        assert invocation in _text(HOMEBREW_DRY_RUN), (
            f"{invocation!r} is missing from homebrew-dry-run.yml"
        )
        assert invocation in _job_directives(RELEASE_PLEASE, "homebrew"), (
            f"{invocation!r} is missing from release-please.yml's homebrew job"
        )
