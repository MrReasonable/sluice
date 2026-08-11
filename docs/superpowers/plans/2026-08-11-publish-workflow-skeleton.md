# Publish Workflow Skeleton (PR 2 of #104) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `build` and `attest` jobs to `.github/workflows/release-please.yml`, gated on
release-please's own `release_created` output (which the job doesn't yet expose), plus a new
`tests/test_release_publish_wiring.py` pinning the wiring — PR 2 of the #104 packaging sequence.

**Architecture:** No new modules. Three additions to one existing workflow file
(`.github/workflows/release-please.yml`): the `release-please` job gains an `id:` on its action
step plus a job-level `outputs:` block; a new `build` job (sdist+wheel, `twine check --strict`,
upload as a run artifact); a new `attest` job (download that artifact, run
`actions/attest-build-provenance` over it). One new test file,
`tests/test_release_publish_wiring.py`, in `tests/test_ci_wiring.py`'s text-matching style
(no YAML parse), with its own file-scoped helpers rather than importing that file's.

**Tech Stack:** GitHub Actions workflow YAML; Python 3.12+ stdlib only in the test file (`re`,
`pathlib`) — no `pyyaml`, per CLAUDE.md's stdlib-only rule for anything a bare install could hit.

## Global Constraints

- No personal data in `sluice/` or `tests/` — N/A here (this plan touches no job-search-domain
  code), but the new test file must not introduce any absolute path, hostname, or credential.
- `python -m pytest` stays fast, offline, and hermetic. The new test file must not need network:
  it reads `.github/workflows/release-please.yml` as plain text, never parses YAML.
- `ruff check sluice tests scripts` stays clean after every task.
- `zizmor --offline --strict-collection .github/workflows/` stays clean after every task that
  touches the workflow file — this is CI's own `lint` job, and this plan edits a workflow file
  directly.
- Conventional Commits for every commit message (`type[(scope)]: description`).
- Every new `uses:` line is SHA-pinned with a trailing `# vX.Y.Z` comment, matching the exact
  convention already in `ci.yml` and `release-please.yml`. `actions/checkout` and
  `actions/setup-python` reuse the identical pins already in `ci.yml`
  (`3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` and
  `5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0`) rather than re-resolving them. The three
  genuinely new actions this PR introduces were resolved fresh against their real current
  released tags before writing this plan (verified two independent ways — `git ls-remote --tags`
  and `gh api repos/<repo>/commits/<tag>` — and cross-checked to agree):
  - `actions/upload-artifact` → `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1`
  - `actions/download-artifact` → `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1`
  - `actions/attest-build-provenance` → `4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2`
- Every new/modified job declares `permissions:` per-job. The workflow-wide `permissions:` block
  (currently `contents: read` only) must never gain an elevated key.
- `checkout` steps carry `persist-credentials: false`, matching the existing convention.
- The spec this plan implements is
  `docs/superpowers/specs/2026-08-10-publish-workflow-skeleton-design.md` — three /review-plan
  rounds, all findings addressed. Read it for the full reasoning behind each decision below; this
  plan does not re-derive that reasoning, only the concrete steps.

---

### Task 1: Fix `release-please`'s missing output, and scaffold the wiring test file

**Files:**
- Modify: `.github/workflows/release-please.yml:69-73` (the `release-please-action` step)
- Create: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Produces: `_job_directives(name: str) -> str` — one job's YAML directives, sliced by
  indentation from `.github/workflows/release-please.yml`, comment-stripped. Raises `ValueError`
  (via `str.index`) if no job named `name` exists yet — that's the expected "fails because it
  doesn't exist" shape for a not-yet-added job, not a clean `AssertionError`.
- Produces: `_step_containing(job: str, needle: str) -> str` — the one step of `job` whose body
  contains `needle`, comment-stripped. Asserts exactly one match.
- Tasks 2 and 3 both call these two functions; do not change either's signature or behavior.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_publish_wiring.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: `test_release_please_job_exposes_the_release_created_output` FAILS on the first
assertion (`id: release` doesn't exist yet in the live file).
`test_release_please_job_keeps_its_original_permissions` PASSES already (the job's permissions
are untouched at this point) — that's expected too; it's a regression guard for the *next* task,
not something this task's own change is meant to newly satisfy.

- [ ] **Step 3: Add the output to the `release-please` job**

Modify `.github/workflows/release-please.yml`, replacing lines 69-73:

```yaml
      - uses: googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7 # v5.0.0
        with:
          token: ${{ steps.app-token.outputs.token }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

with:

```yaml
      - uses: googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7 # v5.0.0
        id: release
        with:
          token: ${{ steps.app-token.outputs.token }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
    outputs:
      release_created: ${{ steps.release.outputs.release_created }}
```

(`id: release` is added to the existing step; the `outputs:` block is new, at the same 4-space
indent as `runs-on:`/`permissions:`/`steps:` — i.e. it's a sibling of `steps:`, not nested under
it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Verify the workflow file still passes lint**

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no new findings. (If `zizmor` or its pinned requirements aren't installed locally, run
`.venv/bin/python -m pip install --require-hashes -r .github/zizmor-requirements.txt` first, per
`ci.yml`'s `lint` job.)

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release-please.yml tests/test_release_publish_wiring.py
git commit -m "fix(ci): expose release-please's release_created job output"
```

---

### Task 2: Add the `build` job

**Files:**
- Modify: `.github/workflows/release-please.yml` (append a new `build:` job immediately after
  the `release-please:` job block Task 1 modified, before end of file)
- Modify: `tests/test_release_publish_wiring.py` (append new tests after Task 1's)

**Interfaces:**
- Consumes: `_job_directives`, `_step_containing` (from Task 1, unchanged).
- Produces: nothing new later tasks import — Task 3's `test_build_and_attest_agree_on_the_
  artifact_name` calls `_step_containing("build", "actions/upload-artifact")` directly, using
  the literal job name `"build"` and step needle `"actions/upload-artifact"` this task
  establishes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_release_publish_wiring.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: the four new tests FAIL — `_job_directives("build")` raises `ValueError` (no job named
`build` exists yet), which pytest reports as an ERROR rather than a plain assertion failure. The
two tests from Task 1 still PASS.

- [ ] **Step 3: Add the `build` job**

Append to `.github/workflows/release-please.yml`, immediately after the `release-please` job's
closing `outputs:` line (i.e. as a new top-level entry under `jobs:`, at 2-space indent, a
sibling of `release-please:`):

```yaml

  build:
    needs: release-please
    if: needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: pip install build twine
      - run: python -m build
      - run: twine check --strict dist/*
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: dist
          path: dist/
          retention-days: 1
```

Deliberately no `--no-isolation` on `python -m build`: this is a real release build on a runner
with network, not the offline hermetic test suite's speed-optimized copy-tree build in
`tests/test_packaging.py` — it should get `python -m build`'s normal isolated behavior (a fresh
ephemeral build environment from the pinned `[build-system]` requirements).

**CORRECTED post-implementation (CodeRabbit finding on PR #116): this reasoning was wrong.**
`python -m build`'s isolated mode installs `[build-system].requires` (`setuptools`) UNVERIFIED
at build time, bypassing the whole point of hash-locking `build`/`twine` in the same job. The
shipped workflow runs `python -m build --no-isolation` against a hash-locked environment that
now also pins `setuptools` — see `.github/build-requirements.txt`'s header and
`docs/superpowers/specs/2026-08-10-publish-workflow-skeleton-design.md`'s matching correction
for the full reasoning and the real-Linux verification. This snippet is left as the ORIGINAL
Task 2 code (per this doc's own nature as a historical record, not a maintained spec); only the
falsified prose claim above is corrected, not the embedded YAML.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: all six tests PASS.

- [ ] **Step 5: Verify the workflow file still passes lint**

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no new findings.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release-please.yml tests/test_release_publish_wiring.py
git commit -m "feat(ci): add the build job for release artifacts"
```

---

### Task 3: Add the `attest` job, and close out the cross-cutting checks

**Files:**
- Modify: `.github/workflows/release-please.yml` (append a new `attest:` job after `build:`)
- Modify: `tests/test_release_publish_wiring.py` (append final tests, including the
  workflow-wide-permissions helper)

**Interfaces:**
- Consumes: `_job_directives`, `_step_containing` (from Task 1, unchanged); the literal job/step
  names `"build"` and `"actions/upload-artifact"` (from Task 2).
- Produces: `_workflow_wide_directives() -> str` — everything in the workflow file above the
  `jobs:` key (name/on/concurrency/permissions), comment-stripped. Nothing later consumes this;
  it's the last new helper this plan adds.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_release_publish_wiring.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: the six new tests FAIL — `_job_directives("attest")` raises `ValueError` (no job named
`attest` exists yet) for the first three; `test_build_and_attest_agree_on_the_artifact_name` and
`test_attest_covers_the_whole_dist_directory` fail the same way via `_step_containing`.
`test_workflow_wide_permissions_stay_read_only` PASSES already (nothing elevated exists yet) —
expected, it's a regression guard for this task's own addition. All six prior tests still PASS.

- [ ] **Step 3: Add the `attest` job**

Append to `.github/workflows/release-please.yml`, immediately after the `build` job (a sibling
of `release-please:` and `build:`, at 2-space indent):

```yaml

  attest:
    needs: [release-please, build]
    if: success() && needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      attestations: write
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: dist
          path: dist/
      - uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2
        with:
          subject-path: dist/*
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: all twelve tests PASS.

- [ ] **Step 5: Full quality bar**

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```

Expected: full suite green, `ruff` clean, `zizmor` clean. This is the same set of checks
`docs/superpowers/specs/2026-08-10-publish-workflow-skeleton-design.md`'s "Definition of done"
section names — this task is the last one, so it's where they all run for real together.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release-please.yml tests/test_release_publish_wiring.py
git commit -m "feat(ci): add the attest job for build provenance"
```
