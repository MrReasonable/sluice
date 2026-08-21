# PyPI Channel (PR 3 of #104) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `job-sluice` to PyPI on every release-please release, upload the same artefacts to the GitHub Release, and prove the Trusted Publishing handshake ahead of time with a dispatch-triggered TestPyPI dry run.

**Architecture:** Two new jobs (`pypi`, `release-assets`) in the existing `.github/workflows/release-please.yml`, both gated on release-please's `release_created` output and both consuming PR 2's `build` artifact. A separate `workflow_dispatch`-only `.github/workflows/testpypi.yml` proves the mechanism against TestPyPI; because it necessarily duplicates the build sequence, a drift pin reads that sequence from BOTH files and asserts they agree. Wiring is pinned by text-matching tests, never a YAML parse.

**Tech Stack:** GitHub Actions, `pypa/gh-action-pypi-publish` v1.14.2 (PyPI Trusted Publishing / OIDC), `gh` CLI, setuptools + `build`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-pypi-channel-design.md` — read it before starting. It carries the reasoning for every decision below and records two review rounds' worth of defects that this plan must not reintroduce.

**Worktree:** `.worktrees/pypi-104`, branch `feat/pypi-channel-104`, draft PR #166.

## Global Constraints

- **Text-matching, never a YAML parse.** `pyyaml` is a guarded optional import; a test needing it could skip itself into uselessness on a bare install.
- **Every helper is comment-stripped.** A raw substring match hits the prose EXPLAINING a rule as readily as the rule itself. This has already fired once in this repo's workflow files.
- **Assert on SCOPE, not merely on absence of violations.** `all([])` is `True`; a sweep that enumerated nothing satisfies every assertion over it.
- **Actions are SHA-pinned with a trailing `# vX.Y.Z`.** Reuse the exact pins already in the live workflows: checkout `3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`, setup-python `5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0`, download-artifact `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1`. The one new pin is `pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2` — that is the COMMIT; `v1.14.2` is an annotated tag whose ref object `a892a5a…` would not resolve.
- **Workflow-wide `permissions:` stays exactly `contents: read`.** Every elevation is per-job. A job-level `permissions:` block is exhaustive, not additive.
- **Conventional Commits** on every commit (`ci:`, `test:`, `docs:`, `build:`).
- **Commit BEFORE witnessing a mutant.** Witness scripts restore with `git checkout`, which wipes uncommitted work — this has cost ~5 files of review fixes in this repo before.
- **`python -m compileall` is NOT part of the witness loop here.** Every mutant in this plan is in YAML or `MANIFEST.in`, which have no bytecode cache. The stale-`.pyc` hazard applies to mutants in `sluice/` or `scripts/`; do not cargo-cult the step where it cannot help.
- **Venv:** this worktree has none. `python3 -m venv .venv && .venv/bin/pip install -e '.[test]' ruff==0.15.21` then `.venv/bin/pip install --require-hashes -r .github/zizmor-requirements.txt`. Call `.venv/bin/python` explicitly — a bare `python` can resolve to a version-manager shim.

---

### Task 1: The `pypi` publish job

**Files:**
- Modify: `.github/workflows/release-please.yml` (append a job after `attest`)
- Test: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: PR 2's `build` job (uploads artifact `dist`) and `release-please`'s `release_created` output.
- Produces: a `pypi` job later tasks assert alongside (`release-assets` shares its gate shape; the drift pin and endpoint/skip-existing pairs in Task 3 read its publish step).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_release_publish_wiring.py`:

```python
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
```

- [ ] **Step 2: Run them and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -k pypi_job -v`
Expected: FAIL — `_job_directives` raises `ValueError: substring not found` on `\n  pypi:\n`, because the job does not exist yet.

- [ ] **Step 3: Add the job**

Append to `.github/workflows/release-please.yml`, after the `attest` job:

```yaml

  pypi:
    needs: [release-please, build]
    if: success() && needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    # The environment is half of the trusted publisher's claim on pypi.org: the publisher
    # entry names owner/repo/workflow-filename AND this environment, and a mismatch fails
    # the OIDC exchange with `invalid-publisher` rather than falling back to anything.
    environment: pypi
    # No `contents:` key, deliberately. A job-level block is exhaustive, so this job holds
    # id-token and NOTHING else -- narrower than the workflow-wide `contents: read` it would
    # otherwise inherit. It downloads an already-built artifact and never reads repo source.
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: dist
          path: dist/
      # Three inputs are left at their defaults, each deliberately:
      #   skip-existing (false) -- a duplicate upload must FAIL, never silently no-op.
      #   attestations (true)   -- PEP 740 attestations on the PyPI-hosted files. NOT a
      #                            duplicate of the `attest` job, which attaches Sigstore
      #                            build provenance to this REPO. Different store, different
      #                            verifier. This is also why `pypi` does not need `attest`:
      #                            a published file is attested either way, so serialising
      #                            would buy nothing and let a flaky attest block a release.
      #                            If attestations is ever turned off, revisit that.
      #   repository-url        -- unset means real PyPI. Pinned by a test paired with
      #                            testpypi.yml's, because getting it wrong is catastrophic.
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Lint the workflow**

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no findings. If it flags the new job, fix the workflow — do not silence the audit.

- [ ] **Step 6: Commit** (before witnessing — a witness restores with `git checkout`)

```bash
git add .github/workflows/release-please.yml tests/test_release_publish_wiring.py
git commit -m "ci(release): publish to PyPI via Trusted Publishing (#104)"
```

- [ ] **Step 7: Witness each new assertion**

For each row, apply the mutant, run the named test BY NODE ID, confirm it FAILS, then `git checkout -- .github/workflows/release-please.yml` and confirm it passes again. Confirm no OTHER test in the file catches the mutant — a mutation killed by a pre-existing test witnesses nothing about a new one.

| Test | Mutant | Kind |
|---|---|---|
| `..._is_gated_on_release_created` | delete the `if:` line | delete |
| `..._needs_release_please_and_build_exactly` | change `needs:` to `[build]` | change |
| `..._declares_the_pypi_environment` | change `environment: pypi` to `environment: testpypi` | change |
| `..._holds_id_token_and_no_contents_key_at_all` | **ADD** `contents: read` to the block | add |
| `..._by_naming_no_repository_url` | **ADD** `repository-url: https://test.pypi.org/legacy/` | add |
| `..._does_not_skip_existing` | **ADD** `skip-existing: true` | add |

The last three are ADD-mutants because those assertions pin an ABSENCE — there is nothing to delete. That is not the equivalent-mutant trap (which is adding a check BESIDE an original that still fires); here the added key IS the violation.

---

### Task 2: `tag_name` output and the `release-assets` job

**Files:**
- Modify: `.github/workflows/release-please.yml` (one output on `release-please`; one new job)
- Test: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: `release-please`'s outputs; the `dist` artifact.
- Produces: `needs.release-please.outputs.tag_name`, available to any later job.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -k "tag_name or release_assets" -v`
Expected: FAIL — no `release-assets` job, no `tag_name` output.

- [ ] **Step 3: Add the output**

In `.github/workflows/release-please.yml`, in the `release-please` job's existing `outputs:` block, after the `sha:` entry:

```yaml
      # Read by `release-assets` to name the release it uploads to. Flat and un-prefixed
      # because release-please-config.json declares ONE package at path "." with
      # include-component-in-tag: false -- the same property `release_created` relies on.
      tag_name: ${{ steps.release.outputs.tag_name }}
```

- [ ] **Step 4: Add the job**

Append after `pypi`:

```yaml

  release-assets:
    needs: [release-please, build]
    if: success() && needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    # The only job in this workflow holding contents: write, and it holds it for one API
    # call. The release-please App token is deliberately NOT reused: uploading an asset to a
    # release in this same repo is exactly what the built-in GITHUB_TOKEN is for, and
    # minting an installation token would widen the blast radius of a job needing nothing else.
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: dist
          path: dist/
      # No --clobber: an asset that already exists means something already uploaded, which
      # should surface rather than be silently overwritten -- the same reasoning as
      # skip-existing on `pypi`.
      #
      # GH_REPO is load-bearing. `gh` resolves the repo from --repo, then GH_REPO, then the
      # cwd's git remotes, and never GITHUB_REPOSITORY. This job has no checkout, so without
      # it `gh` cannot determine a base repository and fails AFTER the release is public.
      # Both values go through env: rather than being interpolated into the script body --
      # the house rule ci.yml already states, which zizmor's template-injection audit gates on.
      - run: gh release upload "$TAG" dist/*
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GH_REPO: ${{ github.repository }}
          TAG: ${{ needs.release-please.outputs.tag_name }}
```

- [ ] **Step 5: Extend the artifact-name agreement from two jobs to four**

The file already has `test_build_and_attest_agree_on_the_artifact_name`. Now that `pypi` and
`release-assets` both download the same artifact, widen it rather than adding two more
independent hardcoded `"dist"` checks. **Keep the `== "dist"` anchor** — it is the
non-vacuity guard, not redundant duplication:

```python
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
```

Delete the old two-job test, which this supersedes.

- [ ] **Step 6: Run the tests and verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: PASS, all of them.

- [ ] **Step 7: Lint**

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/release-please.yml tests/test_release_publish_wiring.py
git commit -m "ci(release): upload the built wheel and sdist to the GitHub Release (#104)"
```

- [ ] **Step 9: Witness**

| Test | Mutant | Kind |
|---|---|---|
| `..._exposes_the_tag_name_output` | delete the `tag_name:` output line | delete |
| `..._release_assets_job_is_gated...` | delete the `if:` line | delete |
| `..._holds_contents_write_and_no_id_token` | **ADD** `id-token: write` to the block | add |
| `..._names_both_a_tag_and_a_repository` (tag half) | delete the `TAG:` env line | delete |
| `..._names_both_a_tag_and_a_repository` (repo half) | delete the `GH_REPO:` env line | delete |
| `test_every_job_agrees_on_the_artifact_name` | change `release-assets`'s download `name:` to `dists` | change |

The last two are separate rows on purpose. Folding them into one "delete an `env:` line" row makes the row satisfiable by deleting `TAG:` alone, leaving the `GH_REPO` half — the fix for the most-corroborated finding in the whole review — with no witness of its own.

---

### Task 3: `testpypi.yml`, and widening the helpers to reach a second file

**Files:**
- Create: `.github/workflows/testpypi.yml`
- Modify: `tests/test_release_publish_wiring.py` (widen five helpers; add assertions)

**Interfaces:**
- Consumes: `.github/build-requirements.txt` (already pins `build==1.5.0`, `twine==7.0.0`, `setuptools==84.0.0`).
- Produces: helper signatures every later task uses — `_text(path)`, `_job_directives(path, name)`, `_step_containing(path, job, needle)`, `_permissions_block(path, job)`, `_workflow_wide_directives(path)`; and the module constant `TESTPYPI`.

- [ ] **Step 1: Widen the helpers — the path parameter is REQUIRED, never defaulted**

In `tests/test_release_publish_wiring.py`, add the constant and thread `path` through all five helpers. Replace `_rp_text()` with `_text(path)`:

```python
TESTPYPI = ROOT / ".github" / "workflows" / "testpypi.yml"


def _text(path: Path) -> str:
    return path.read_text()
```

Then change the four remaining signatures to take `path: Path` as their FIRST parameter and pass it down:

```python
def _job_directives(path: Path, name: str) -> str:
    text = _text(path)
    ...

def _step_containing(path: Path, job: str, needle: str) -> str:
    block = _job_directives(path, job)
    ...

def _permissions_block(path: Path, job: str) -> str:
    block = _job_directives(path, job)
    ...

def _workflow_wide_directives(path: Path) -> str:
    text = _text(path)
    ...
```

Add this to `_workflow_wide_directives`'s docstring, because it is the reason the parameter has no default:

```
    The `path` parameter is REQUIRED and must never gain a default. `release-please.yml`'s
    workflow-wide block is BYTE-IDENTICAL to `testpypi.yml`'s (`permissions:\n  contents: read`),
    so a forgotten path argument would read the wrong file, compare it to the value expected of
    the other, and PASS -- pinning nothing. In the worst case the drift pin compares a file to
    itself and certifies perfect agreement. Required means a forgotten argument is a TypeError
    at collection time instead.
```

Update **every call site in this same commit**, each passing `RELEASE_PLEASE` explicitly —
including the tests Tasks 1 and 2 just added, which use the pre-widening signatures.

**Do not work to a fixed count.** The file had 25 call sites when this plan was written, before
Tasks 1 and 2 added roughly ten more, so any number quoted here is stale by the time you read it.
That is precisely why the parameter is required rather than defaulted: a missed call site is a
`TypeError` at collection, so the interpreter enumerates them for you. Run the file and fix what
it names until it is green.

- [ ] **Step 2: Run the whole file — the widening must be behaviour-neutral**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: PASS, same count as before the widening. A `TypeError` here means a call site was missed — which is exactly what the required parameter is for.

- [ ] **Step 3: Write the failing tests for the new workflow**

```python
def test_testpypi_triggers_only_on_workflow_dispatch():
    """A dry-run workflow that gained a `push:` trigger would publish to a permanent,
    public index on every commit. Asserted on the trigger block's own contents, not by
    absence-of-substring across the whole file."""
    block = _workflow_wide_directives(TESTPYPI)
    triggers = re.findall(r"\n  ([a-z_]+):", block[block.index("\non:") :])
    assert triggers == ["workflow_dispatch"]


def test_testpypi_workflow_wide_permissions_are_read_only():
    """Exactly the slicing `test_workflow_wide_permissions_stay_read_only` already uses,
    pointed at the other file -- both workflows put `permissions:` last before `jobs:`."""
    block = _workflow_wide_directives(TESTPYPI)
    idx = block.index("\npermissions:\n")
    perm_block = block[idx + 1 :]
    assert perm_block == "permissions:\n  contents: read", (
        f"testpypi.yml's workflow-wide permissions must be EXACTLY `contents: read`. "
        f"Got: {perm_block!r}")


def test_testpypi_declares_the_testpypi_environment():
    """Paired with `test_pypi_job_declares_the_pypi_environment`. The environment name is
    half of each trusted publisher's claim, so a swap breaks authentication with an error
    that names neither."""
    assert "environment: testpypi" in _job_directives(TESTPYPI, "testpypi")


def test_testpypi_publishes_to_testpypi_not_real_pypi():
    step = _step_containing(TESTPYPI, "testpypi", "gh-action-pypi-publish")
    assert "repository-url: https://test.pypi.org/legacy/" in step


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


def test_the_stamp_is_proven_against_the_built_artefacts():
    """A successful substitution says the SOURCE changed, not that the BUILD consumed it.
    They are coupled today by `dynamic = ["version"]` reading `sluice.__version__`, but that
    coupling is exactly what a packaging change alters unnoticed. This observes the artefact."""
    step = _step_containing(TESTPYPI, "testpypi", "Prove the stamp reached the artefacts")
    assert "dist/" in step
    assert "exit 1" in step
```

- [ ] **Step 4: Run and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -k testpypi -v`
Expected: FAIL — `FileNotFoundError` on `.github/workflows/testpypi.yml`.

- [ ] **Step 5: Create the workflow**

Create `.github/workflows/testpypi.yml` with exactly the YAML in the spec's "`testpypi.yml` (new file)" section. Do not retype it from memory — copy it from the spec.

- [ ] **Step 6: Run and verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v`
Expected: PASS.

- [ ] **Step 7: Lint — the new file is audited automatically**

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no findings. `ci.yml` passes the DIRECTORY, so `testpypi.yml` is covered with no extra wiring.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/testpypi.yml tests/test_release_publish_wiring.py
git commit -m "ci(release): add a dispatch-triggered TestPyPI dry run (#104)"
```

- [ ] **Step 9: Witness**

| Test | Mutant | Kind |
|---|---|---|
| `..._triggers_only_on_workflow_dispatch` | **ADD** `push:` to the `on:` block | add |
| `..._workflow_wide_permissions_are_read_only` | change to `contents: write` | change |
| `..._declares_the_testpypi_environment` | change to `environment: pypi` | change |
| `..._publishes_to_testpypi_not_real_pypi` | **delete** the `repository-url` line | delete |
| `..._skips_existing` | **delete** the `skip-existing: true` line | delete |
| `..._refuses_a_non_default_branch` | delete the guard step | delete |
| `..._fails_loudly_when_it_matches_nothing` | change `re.subn` back to a bare `re.sub` and drop the `n != 1` block | change |
| `..._proven_against_the_built_artefacts` | delete the stamp-proof step | delete |

Rows 4 and 5 are the TestPyPI halves of the endpoint and skip-existing pairs, whose `pypi` halves were witnessed in Task 1. Both halves are witnessed because a pair with one witnessed side is exactly the "watches the wrong half" defect this plan's review found twice.

Additionally, confirm the required-parameter guarantee: temporarily change `test_testpypi_workflow_wide_permissions_are_read_only` to pass `RELEASE_PLEASE` instead of `TESTPYPI` and confirm it **fails**. Before the widening it would have PASSED, because the two blocks are byte-identical. Restore it.

---

### Task 4: The drift pin

**Files:**
- Modify: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: `_job_directives(path, name)` from Task 3, both workflow files.
- Produces: nothing further.

- [ ] **Step 1: Write the failing test**

```python
_BUILD_COMMANDS = (
    "pip install --require-hashes -r .github/build-requirements.txt",
    "python -m build --no-isolation",
    "twine check --strict dist/*",
)


def _post_checkout_run_steps(path: Path, job: str) -> list[str]:
    """Every `run:` step of `job` at or after its `actions/checkout` step.

    Anchored past checkout so `testpypi.yml`'s branch guard -- which deliberately runs
    BEFORE checkout, so a wrong branch is refused before any source is fetched -- sits
    outside the region by construction, letting the two regions describe the same thing.
    """
    block = _job_directives(path, job)
    marker = "actions/checkout@"
    assert marker in block, f"the {job!r} job in {path.name} has no checkout to anchor on"
    return re.findall(r"\n      - (?:name:.*\n(?:  )*)?\s*run: .+", block[block.index(marker):])


def _python_version(path: Path, job: str) -> str:
    match = re.search(r"python-version: \"([^\"]+)\"", _job_directives(path, job))
    assert match, f"no python-version pinned in {path.name}'s {job!r} job"
    return match.group(1)


def test_the_dry_run_builds_exactly_the_way_the_release_build_does():
    """The cost of a separate dry-run file is that its build steps are a COPY, and a copy
    can stop matching what it claims to prove without anything going red.

    GUARDED AGAINST ITS OWN VACUITY, because four equality checks between two extractions
    pass trivially when both extractions fail -- `None == None` is green while the two files
    build differently, which is the `all([])` shape this repo has a standing rule about. The
    design's first draft omitted these guards while citing two precedents that both carry
    them.
    """
    release = _job_directives(RELEASE_PLEASE, "build")
    dry_run = _job_directives(TESTPYPI, "testpypi")

    for command in _BUILD_COMMANDS:          # non-vacuity: each side really contains it
        assert command in release, f"release build no longer runs: {command}"
        assert command in dry_run, f"dry run no longer runs: {command}"

    assert _python_version(RELEASE_PLEASE, "build") == _python_version(TESTPYPI, "testpypi")

    # Scope: pin how many run: steps each region has, so an unexplained extra step -- or a
    # silently dropped one -- cannot read as agreement. They differ because the dry run
    # legitimately carries the two steps that make a dispatch prove something.
    assert len(_post_checkout_run_steps(RELEASE_PLEASE, "build")) == 3
    assert len(_post_checkout_run_steps(TESTPYPI, "testpypi")) == 5
```

- [ ] **Step 2: Run and verify it passes immediately**

Run: `.venv/bin/python -m pytest tests/test_release_publish_wiring.py::test_the_dry_run_builds_exactly_the_way_the_release_build_does -v`
Expected: PASS. This test is written against files Tasks 1–3 already created, so there is no red phase — which is precisely why **Step 4's witnesses are the only evidence it works.** Do not skip them.

If `_post_checkout_run_steps`' regex matches nothing or the wrong number, **fix the regex, never
the expected counts.** The counts (3 and 5) were derived by reading the real `build` job and this
plan's own `testpypi.yml`, and were independently recounted during design review. A count edited
to make a broken extractor go green is the exact failure this test exists to prevent, installed
in the test itself.

- [ ] **Step 3: Commit**

```bash
git add tests/test_release_publish_wiring.py
git commit -m "test(ci): pin the dry run's build sequence against the release build's (#104)"
```

- [ ] **Step 4: Witness — four separate mutants, because this test makes four claims**

| Claim | Mutant | Expected |
|---|---|---|
| equality | change `testpypi.yml`'s `python-version` to `"3.13"` | FAIL |
| command presence | delete the `twine check --strict dist/*` line from `testpypi.yml` | FAIL |
| count (dry run) | delete the `twine check` STEP from `testpypi.yml` | FAIL on the count |
| vacuity | rename `python-version` to `python_version` in BOTH files | FAIL on `_python_version`'s assert, naming the file |

The **equality mutant is the one that matters most** and the design's first draft had none: every row it listed was a delete, and deletes are caught by the non-vacuity guards instead, so the four-way comparison — the pin's entire purpose — was never exercised. Note also that the draft's only row, "delete the `setup-python` step", is absent here: `setup-python` is a `uses:` step, so deleting it leaves both `run:` counts unchanged, making it an equivalent mutant for the count claims.

---

### Task 5: `MANIFEST.in` and the sdist contents guard

**Files:**
- Create: `MANIFEST.in`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing further.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_packaging.py`, mirroring `_build_wheel`'s copy-then-build idiom:

```python
SDIST_ROOT_MEMBERS = {
    "LICENSE", "MANIFEST.in", "PKG-INFO", "README.md",
    "job_sluice.egg-info", "pyproject.toml", "setup.cfg", "sluice",
}


def _build_sdist(dest, manifest_text=None):
    """Build a REAL sdist from a COPY of the tree and return its member names.

    A copy, not the real tree, for the same two reasons `_build_wheel`'s docstring gives --
    the build drops `build/` and `.egg-info` beside pyproject.toml, which must not land in
    the repo root -- plus a third specific to this guard: the falsify partner below needs to
    build with a MUTATED MANIFEST.in, and it must not edit the repository's real one to do
    it. Both tests share this helper for exactly that reason. Measured during design review:
    a copy WITHOUT `tests/` ships zero test members whether or not `prune tests` is present,
    so a guard and partner that build from differently-shaped trees prove nothing.

    SCOPE. The copy is the TRACKED TREE -- whatever `git ls-files` reports -- rather than the
    hand-listed subset this plan first specified. Measured, that hand-list made three real
    MANIFEST.in changes INVISIBLE: `graft scripts` and `graft .github` each found nothing to
    graft, and `include sluice.yaml.example` named a file the copy did not contain, so all
    three left the root-entry equality green while the real tree would have shipped 8, 8 and 1
    extra members respectively. The root-entry set comes out IDENTICAL either way, so this
    changed the guard's reach and not its verdict. Updated here to match what shipped rather
    than left as the plan's superseded first form -- but tests/test_packaging.py carries the
    authoritative version of both this docstring and this body, and the code wins on any
    disagreement.
    """
    # `_tracked_files()` is a sibling helper: `git ls-files -z` for this repo, failing loudly
    # on a non-zero exit or an empty result rather than returning [] and building from nothing.
    for rel in _tracked_files():
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            continue   # tracked but deleted in the working tree; nothing to copy
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
    # LAST, and unconditionally: MANIFEST.in is itself tracked, so the loop above has already
    # placed the real one. A falsify partner's MUTATED text must overwrite that copy.
    with open(f"{dest}/MANIFEST.in", "w", encoding="utf-8") as f:
        f.write(manifest_text if manifest_text is not None
                else open(f"{ROOT}/MANIFEST.in", encoding="utf-8").read())
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation",
         "--outdir", f"{dest}/out"],
        cwd=dest, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"sdist build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    tarballs = glob.glob(f"{dest}/out/*.tar.gz")
    assert tarballs, f"the build reported success but produced no sdist in {dest}/out"
    with tarfile.open(tarballs[0]) as tf:
        return tf.getnames()


def _sdist_root_entries(names):
    """The entry names one level below the sdist's single root directory.

    Every member of an sdist is `job_sluice-<version>/<path>`, so "the set of top-level
    entries" is ONE element -- identical whether the tarball is clean or carries 166 test
    modules. An allowlist over that set is exactly-equal and blind. Derive the prefix from
    PKG-INFO's parent and assert BELOW it.
    """
    prefix = next(n for n in names if n.endswith("/PKG-INFO")).rsplit("/", 1)[0]
    assert prefix, "could not derive the sdist root prefix from PKG-INFO"
    return {n[len(prefix) + 1:].split("/", 1)[0] for n in names if n != prefix}


def test_the_sdist_ships_the_package_and_metadata_and_no_tests(tmp_path):
    """The sdist becomes PUBLIC AND PERMANENT with the PyPI channel. Before it, `build`'s
    sdist expired with the run artifact in a day.

    `tests/` is pruned rather than shipped because the subset that would ship is USELESS:
    distutils' default `tests/test*.py` glob is non-recursive, so `conftest.py` and the
    fixture packages beside it stay out and the shipped tests cannot run. Shipping a broken
    test tree is worse than shipping either a working one or none.
    """
    names = _build_sdist(str(tmp_path))
    assert len(names) > 20, "the sdist is implausibly small; the build produced almost nothing"
    assert _sdist_root_entries(names) == SDIST_ROOT_MEMBERS
    assert not [n for n in names if "/tests/" in n], "tests must not ship in the sdist"


def test_the_sdist_guard_is_falsified_by_dropping_the_prune(tmp_path):
    """The guard above must be FALSIFIABLE, not merely green.

    Built from the SAME helper with only MANIFEST.in mutated -- the guard and its partner
    must observe identically-shaped trees, or the partner proves nothing about the guard.
    """
    original = open(f"{ROOT}/MANIFEST.in", encoding="utf-8").read()
    assert "prune tests" in original, (
        "MANIFEST.in is not written as this guard expects, so dropping the prune would "
        "SILENTLY NO-OP and this test would pass for the wrong reason")
    names = _build_sdist(str(tmp_path), manifest_text=original.replace("prune tests", ""))
    assert [n for n in names if "/tests/" in n], (
        "removing `prune tests` did not make tests ship, so the guard above is not "
        "actually what keeps them out")
```

Add `import tarfile` to the module's imports.

- [ ] **Step 2: Run and verify they fail**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -k sdist -v`
Expected: FAIL — `FileNotFoundError` on `MANIFEST.in`.

- [ ] **Step 3: Create `MANIFEST.in`**

```
# The sdist is public and permanent from the PyPI channel (#104) onward. `tests/` is pruned
# because the subset distutils would otherwise ship is unusable: its default `tests/test*.py`
# glob is non-recursive, so conftest.py and the fixture packages beside it stay behind and the
# shipped tests cannot run. `tests/test_packaging.py` pins the resulting member list.
#
# Nothing grafts `docs/`. The neutrality rule binds `sluice/` and `tests/`, not `docs/`, and
# while `tests/test_no_leaked_files.py` DOES sweep that tree's content, it reaches only two
# specific things there: absolute home paths, over every tracked file (its `_GATE_PATHSPEC` is
# empty, which means exactly that), and static content left in `docs/**/*.j2` after Jinja and
# HTML are stripped. Neither reads docs/ PROSE for the employer names, locations or contact
# details the neutrality rule is about. So the tree is partially covered, not reviewed, and
# publishing it would put the uncovered remainder on an index that never forgets.
prune tests
```

- [ ] **Step 4: Run and verify they pass**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add MANIFEST.in tests/test_packaging.py
git commit -m "build(packaging): prune tests from the sdist and pin its contents (#104)"
```

- [ ] **Step 6: Witness**

| Test | Mutant | Expected |
|---|---|---|
| `..._ships_the_package_and_metadata_and_no_tests` | remove `prune tests` from the real `MANIFEST.in` | FAIL |
| `..._ships_the_package_and_metadata_and_no_tests` | add `graft docs` to the real `MANIFEST.in` | FAIL — `docs` is not in the allowlist |
| `..._guard_is_falsified_by_dropping_the_prune` | replace `prune tests` with `prune tests/` (a spelling the `assert "prune tests" in original` still accepts) | the guard stays green and the partner must still pass — if it does not, the two are not observing the same tree |

`git checkout -- MANIFEST.in` after each.

---

### Task 6: Sequencing-spec edit, two spec corrections, and hand off the PR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-pypi-channel-design.md`

- [ ] **Step 1: Record the release-scope change in the sequencing spec**

Add one paragraph to its "Manual-prerequisite timing" section. Its per-channel-hold model is **not** superseded — it is restored — so the edit records only that 1.0.0 is scoped to the PyPI channel:

```markdown
**Release scope (decided 2026-08-21).** 1.0.0 ships once the PyPI channel is live -- PR 3
merged, its manual prerequisites configured, the TestPyPI dry run green. Docker, deb/rpm and
Homebrew follow afterwards and ship in 1.1.0. The per-channel hold instructions in this
section apply as written; an earlier draft of PR 3's design proposed holding 1.0.0 until every
channel was ready, which would have superseded them, and that was withdrawn. One consequence
for PR 7: README's install claims become false the moment 1.0.0 publishes, so the README half
of PR 7 is now due BEFORE 1.0.0 rather than after every channel.
```

- [ ] **Step 2: Correct two claims in PR 3's own spec, found while planning**

In `docs/superpowers/specs/2026-08-21-pypi-channel-design.md`:

(a) The Falsification section instructs content-addressing the `.pyc` caches before witnessing. **Every mutant in this plan is YAML or `MANIFEST.in`, which have no bytecode cache.** Replace that sentence with:

```markdown
The stale-bytecode hazard does NOT apply to these witnesses: every mutant here is in a YAML
workflow or `MANIFEST.in`, neither of which has a `.pyc`. `compileall --invalidation-mode
checked-hash` is for mutants in `sluice/` or `scripts/`; running it here would be a step that
cannot help, which teaches a false lesson about when it is needed.
```

(b) The spec says the drift pin's `run:`-step counts are "three" and "five". Confirm against the file as built; if Tasks 1–4 changed either region, fix the spec rather than the test.

- [ ] **Step 3: Run the full suite and the linters**

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```
Expected: all green. Note the pre-change baseline is 4366 tests; the count should rise, never fall.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/
git commit -m "docs: scope 1.0.0 to the PyPI channel, and correct two spec claims (#104)"
```

- [ ] **Step 5: Verify each commit is independently green**

For each commit on the branch, check it out detached and run the suite. A branch whose commits are green only in aggregate has a broken bisect — this has caught a real defect here before (a first commit failing on an import the second commit added).

- [ ] **Step 6: Hand off — do NOT mark the PR ready without saying so**

Report to the user: the branch is ready, and marking PR #166 ready for review is what spends a CodeRabbit slot (`.coderabbit.yaml` sets `auto_review.drafts: false`, so the draft has cost nothing so far). Recommend running `/review-pr` FIRST — the local specialist team is free and parallel, CodeRabbit refills at ~1/hour and is adaptive to 7-day volume.

The manual prerequisites (both GitHub environments, both trusted publishers, the dry-run dispatch) are the user's to perform after merge and are listed in the spec.
