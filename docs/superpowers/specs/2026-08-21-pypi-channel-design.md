# PyPI channel (PR 3 of #104) design

Status: design, approved 2026-08-21.

This is PR 3 of the 7-PR packaging sequence locked in
`docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md`. That spec fixes
PR 3's scope to the `pypi` job, the `release-assets` job, and a `workflow_dispatch` TestPyPI dry
run; #104 itself locks the mechanism from the PR #103 planning pass. This document is the
PR-3-specific decisions those two leave open: where the dry run lives (the sequencing spec says
"a `workflow_dispatch` TestPyPI variant" without saying *of what*), what each job's failure
semantics are, and what pins the dry run to the release path it claims to prove.

It also records two decisions the repo owner made on 2026-08-21 that change documents upstream of
this one -- see "Supersedes the sequencing spec's timing section" below.

## Revised after plan review, 2026-08-21

Reviewed by the five-agent roster before implementation: 27 findings, 0 Critical, 14 High. What
changed, recorded because several of these were errors of a KIND rather than of detail:

- **`release-assets` could not run.** `gh release upload` in a job with no checkout cannot resolve
  a repository. Found independently by three reviewers. The draft's own wiring assertion would
  have passed on the dead job -- a guard that watches the wrong half of the thing it guards.
- **The reusable-workflow limitation was used to close an option space it does not reach.** It
  binds the publish step; the duplication at issue is the build sequence, which a composite action
  could share. Verified facts can still be misapplied, and a "Verified" heading makes that harder
  to notice, not easier.
- **The drift pin could pass while comparing nothing.** Copied a guarded idiom and dropped the
  guard.
- **The dry run's upload leg would no-op after its first dispatch**, while the document called it
  the only pre-release proof.
- **The sdist -- public and permanent from this PR on -- had no contents guard**, and ships 166
  test modules that cannot run.
- **Decision 2 had no mechanism** and contradicted decision 1. Reopened rather than papered over,
  then resolved by dropping it (2026-08-21).
- Four claims about the existing test file were simply wrong (five helpers, not two; a defaulted
  path parameter would fail open silently).

What held: every locally-checkable claim in "Verified before designing" was independently
confirmed by all five reviewers, and no hard invariant, dependency rule or neutrality property is
touched. The verification discipline worked; what it could not catch is that a document can be
factually accurate about the codebase and still describe a job that cannot run.

## Verified before designing, not assumed

Each of these was checked against a live source on 2026-08-21, because each one, if wrong, would
invalidate a section below rather than merely a sentence:

- **PyPI trusted publishing does not work from inside a reusable workflow.**
  `pypi/warehouse#11096` ("Trusted publishing: Support for GitHub reusable workflows") and
  `pypa/gh-action-pypi-publish#166` are both still OPEN (last updated 2026-05-02 and 2026-02-01
  respectively). This is what removes the obvious third option -- factoring the publish step into
  a `workflow_call` workflow shared by the release path and the dry run -- from consideration
  entirely. It is not a preference; the mechanism does not exist.
- **`pypa/gh-action-pypi-publish` is at v1.14.2** (published 2026-07-29). Its defaults, read from
  the action's own `action.yml` rather than its README: `repository-url` defaults to
  `https://upload.pypi.org/legacy/`, `packages-dir` to `dist`, `skip-existing` to `'false'`,
  `attestations` to `'true'`, `verify-metadata` to `'true'`.
- **v1.14.2 is an ANNOTATED tag.** Its ref object SHA is `a892a5a61159132606e93a2fa6f4358831b04d26`,
  which is the tag object, not a commit. The commit to pin is
  `dc37677b2e1c63e2034f94d8a5b11f265b73ba33`. Pinning the ref object SHA would not resolve.
- **`release-please-action` v5 exposes a flat `tag_name` output.** This repo configures one package
  at path `.` with `include-component-in-tag: false` (`release-please-config.json`), so the
  outputs are flat and un-prefixed -- the same property PR 2 relied on for `release_created`.
- **`zizmor` already covers a new workflow file with no wiring.** `.github/workflows/ci.yml`'s
  `lint` job runs `zizmor --offline --strict-collection .github/workflows/` -- a DIRECTORY glob,
  not an enumerated file list, so `testpypi.yml` is audited from the moment it exists.
- **No test enumerates `.github/workflows/`.** `tests/test_ci_wiring.py`'s `rglob` walks
  `.rulesync/`, not the workflow directory, so adding a workflow file is purely additive and
  breaks no existing sweep.
- **Neither index has a `job-sluice` project.** `pypi.org/pypi/job-sluice/json` and
  `test.pypi.org/pypi/job-sluice/json` both return 404. Both publishes below therefore CREATE
  their project via the pending publisher, rather than adding to an existing one.
- **The repo has no GitHub environments at all.** `GET /repos/MrReasonable/sluice/environments`
  returns an empty list, so both `pypi` and `testpypi` are new.

## Scope

In: two new jobs in `.github/workflows/release-please.yml` (`pypi`, `release-assets`); one new
output on the existing `release-please` job (`tag_name`); a new
`.github/workflows/testpypi.yml`; extensions to `tests/test_release_publish_wiring.py`; a new
sdist-contents guard in `tests/test_packaging.py` with a `MANIFEST.in` to make it hold; and edits
to two documents this PR's decisions falsify -- `docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md` and `README.md`.

Those last two are diff, not context, and were missing from this list in the reviewed draft. A
scope list that enumerates only code is how a doc edit gets skipped at implementation time and
lands as drift.

The sdist guard is an addition to what the sequencing spec allocated PR 3, and the justification
is specific rather than general: PR 3 is the PR that makes the sdist PUBLIC AND PERMANENT. Before
it, `build`'s sdist expired with the run artifact in a day; after it, it is on an index that
never forgets. A guard whose absence only becomes load-bearing because of this PR belongs to this
PR.

Out: the `docker`, `linux-packages` and `homebrew` jobs (PRs 4-6) and `docs/INSTALL.md` (PR 7),
per the sequencing table. The manual prerequisites are repo-owner-only and listed below rather
than performed here.

## Where the dry run lives: its own file

`testpypi.yml`, triggered on `workflow_dispatch` and nothing else.

The alternative considered was adding `workflow_dispatch` to `release-please.yml` and gating a
dry-run job on `github.event_name`. It proves strictly more -- PyPI binds a trusted publisher to
a workflow FILENAME -- corroborated by the action itself, which on failure prints a settings link
built from `...&workflow_filename=${WORKFLOW_FILENAME}` -- so a dry run in the same file exercises
the same publisher-shaped claim the real release will make, where a separate file necessarily
exercises a different one. It was
rejected anyway, on the cost of getting there: a manual dispatch of `release-please.yml` would
also run the `release-please` job, which mints an App installation token and can open or modify
the release PR, so that job would need gating off dispatch -- and `build`, which today gates on
`needs.release-please.outputs.release_created == 'true'`, could then no longer gate on that
alone. It would need `always() && (... || github.event_name == 'workflow_dispatch')`, and
`always()` BYPASSES the implicit `success()` check on `needs` that PR 2's design chose
deliberately (see that document's "The gate" section). Trading a verified-by-reading gate on the
release path for a stronger proof of a mechanism that is exercised a handful of times is the
wrong direction for this repo.

What the separate file gives up is recovered by a test rather than left implicit -- see "The
drift pin" below.

**A correction to how that verified fact was used.** The reusable-workflow limitation is real and
binds the PUBLISH step. The reviewed draft then let it close the option space entirely, which does
not follow: the duplication the drift pin defends is the BUILD sequence, and nothing about
building an sdist involves trusted publishing. A composite action (`.github/actions/build-dist/`)
runs INLINE in the calling job, leaves `job_workflow_ref` untouched, and would therefore share the
build steps with no bearing on the publisher claim at all. It was never considered, and the
"Verified" bullet was doing rhetorical work it had not earned.

It is still not adopted here, for a different and narrower reason: two copies is not yet
duplication worth an abstraction, and extracting a composite action for two call sites is the
premature abstraction this repo's review roster would flag on the next pass. **PR 4 is the
extraction point, and this document names it so the decision is inherited rather than
rediscovered.** The trigger is concrete: PR 4 adds a third consumer of the same build sequence
(the `docker` channel's own dry run), and a third copy plus a second pairwise pin is where the
drift pin stops being cheaper than the abstraction. Extracting then also requires widening
`ci.yml`'s zizmor invocation from `.github/workflows/` to cover `.github/actions/` -- worth
knowing in advance, since a composite action that no linter audits would silently drop this
repo's SHA-pinning discipline at exactly the point the build sequence became shared.

## Job definitions

### `release-please` (existing job -- gains one more output)

```yaml
    outputs:
      release_created: ${{ steps.release.outputs.release_created }}
      sha: ${{ steps.release.outputs.sha }}
      tag_name: ${{ steps.release.outputs.tag_name }}   # NEW
```

One line, the same shape PR 2 added `release_created` and `sha` in. `release-assets` needs it to
name the release it uploads to; nothing else in the workflow reads it.

### `pypi`

```yaml
  pypi:
    needs: [release-please, build]
    if: success() && needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
```

`needs: [release-please, build]` names `release-please` directly even though `build` already
depends on it, for the reason PR 2's `attest` does: reading `needs.release-please.outputs.*`
requires a direct dependency edge, not a transitive one.

The `permissions:` block names only `id-token: write`. A job-level block is exhaustive rather
than additive, so every other permission -- including the workflow-wide `contents: read` default
-- becomes `none` for this job. That is correct and deliberate, exactly as it is on `attest`:
this job downloads an already-built artifact and never checks out repository source, so it has no
use for contents access. It is the narrowest job in the workflow despite being the one that
publishes.

Three input decisions, all of which are "leave the default, and say why" rather than silent
acceptance:

- **`skip-existing` stays `false`.** A duplicate upload must fail loudly. This repo engineers out
  the quiet-wrong-default bug class deliberately (`_select_backend`'s unknown-name raise is the
  canonical instance), and a release that silently no-ops its own publish -- reporting green
  while shipping nothing -- is that bug class aimed at the one job whose entire purpose is the
  side effect.
- **`attestations` stays `true`.** This produces PEP 740 attestations attached to the files
  hosted on PyPI. It is NOT a duplicate of the `attest` job: that one runs
  `actions/attest-build-provenance`, which attaches Sigstore build provenance to this GitHub
  repository. Different artifacts, different stores, different verifiers. Both need
  `id-token: write`, which this job already holds for the publish itself.
- **`repository-url` is not set at all**, leaving the default real-PyPI endpoint. This is
  load-bearing and is pinned by a test paired with `testpypi.yml`'s -- see below.

**`pypi` does not `need: attest`,** so the two run in parallel. The serialised alternative
(publish only what was already attested) was considered and rejected on the strength of the
`attestations: true` decision above: because `gh-action-pypi-publish` attests the files it
uploads, a published artifact carries an attestation on PyPI whether or not the repo-side
`attest` job succeeded. Serialising would therefore buy no integrity property that is not already
held, while letting a flaky attestation step block a release publish. If `attestations` were ever
turned off, this decision must be revisited with it -- the two are coupled, and that coupling is
the reason both are stated here rather than one being left as an obvious default.

### `release-assets`

```yaml
  release-assets:
    needs: [release-please, build]
    if: success() && needs.release-please.outputs.release_created == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: dist
          path: dist/
      - run: gh release upload "$TAG" dist/*
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GH_REPO: ${{ github.repository }}
          TAG: ${{ needs.release-please.outputs.tag_name }}
```

**`GH_REPO` is load-bearing, not decoration, and the reviewed draft omitted it** -- three
reviewers found the same defect independently. `gh` resolves its target repository from `--repo`,
then `GH_REPO`, then the current directory's git remotes. It does NOT read `GITHUB_REPOSITORY`.
This job deliberately never checks out, so the runner's working directory is not a git repository
and the resolution chain runs out: the step fails before making any API call. `GH_TOKEN`
authenticates but selects nothing. No workflow in this repo invokes `gh` today, so no existing
pattern would have caught the omission by imitation.

The failure lands in the worst possible place. It runs only after release-please has already
tagged and published the GitHub Release, so the release exists publicly with no assets attached
-- and under decision 1 below it first executes on the 1.0.0 merge, beside four other first-run
publish jobs. Passing the repository through `env:` rather than a `--repo` flag also keeps it
consistent with the `TAG` treatment directly above.

The only job in this workflow holding `contents: write`, and it holds it for one API call. The
release-please App token is not reused here: uploading an asset to a release in this same repo is
exactly what the built-in `GITHUB_TOKEN` scoped to `contents: write` is for, and minting an
installation token for it would widen the blast radius of a job that needs nothing else.

`TAG` reaches the script through `env:` rather than being interpolated into the `run:` body.
`tag_name` originates from release-please rather than from anything attacker-controlled, so this
is not closing a live injection -- it is the same house rule `ci.yml` already states at its own
template-injection comment, and zizmor's template-injection audit gates on it regardless of
provenance.

No `--clobber`, for the same reason `skip-existing` stays `false`: an asset that already exists
means something already uploaded, which should surface rather than be silently overwritten.

Independent of `pypi` -- neither needs the other. A PyPI outage still leaves the GitHub release
with a downloadable wheel and sdist; a failed asset upload does not withhold the package from
PyPI.

### `testpypi.yml` (new file)

```yaml
name: TestPyPI dry run

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  testpypi:
    runs-on: ubuntu-latest
    environment: testpypi
    permissions:
      id-token: write
      contents: read
    steps:
      - name: Refuse to publish a non-default branch to a permanent public index
        if: github.ref_name != github.event.repository.default_branch
        run: |
          echo "::error::Dispatch this workflow from the default branch. A TestPyPI upload is
          permanent and public; an unmerged branch must not become the tree of record."
          exit 1
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - name: Stamp a unique dev version so each dispatch exercises index ACCEPTANCE
        run: |
          python -c "
          import os, pathlib, re
          f = pathlib.Path('sluice/__init__.py')
          f.write_text(re.sub(r'(__version__ = \")([^\"]+)(\")',
                              rf'\g<1>\g<2>.dev{os.environ[\"RUN\"]}\g<3>',
                              f.read_text()))
          "
        env:
          RUN: ${{ github.run_number }}
      - run: pip install --require-hashes -r .github/build-requirements.txt
      - run: python -m build --no-isolation
      - run: twine check --strict dist/*
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
        with:
          repository-url: https://test.pypi.org/legacy/
          skip-existing: true
```

The **branch guard** exists because a TestPyPI upload cannot be withdrawn and the reviewed draft
pinned no `ref:` at all -- unlike `build`, which pins `ref: needs.release-please.outputs.sha`. A
`workflow_dispatch` builds whatever ref it was dispatched from, including an unmerged branch, and
the result is permanently public. It fails loudly rather than skipping, because a dry run that
silently does nothing is the failure mode this whole document is trying to avoid.

The **version stamp** exists because `skip-existing: true` alone would let the upload leg go green
while doing nothing. TestPyPI filenames are immutable and `sluice/__init__.py` declares a static
`0.1.0` that decision 1 parks, so without this, index ACCEPTANCE is exercised exactly once, ever
-- and `twine check --strict` does not substitute for it: it validates metadata rendering, not
whether the index accepts the upload. This repo has already been bitten by an index-side
`InvalidConfigError` on a license/classifier pairing that rendered fine locally. `.devN` is valid
PEP 440, sorts below any real release, and the regex rewrites only the quoted value, preserving
the `# x-release-please-version` marker on that line that `tests/test_release_version.py` pins. It
mutates only the ephemeral CI checkout; nothing is committed.

It builds rather than downloading an artifact, because there is no `build` job on this trigger to
download from. `contents: read` IS declared here, unlike on `pypi`, because this job does check
out source.

The dry run also exercises the ATTESTATION path, which was not obvious and is worth stating
because it widens what a dispatch proves: `attestations` defaults to `true` and the action
disables it for indexes other than PyPI by testing the repository URL against the regex
`pypi\.org` -- which `test.pypi.org` matches. So attestation generation runs here too, rather
than being silently skipped as a non-PyPI index.

**The `skip-existing: true` asymmetry is deliberate, and its justification is narrower than the
reviewed draft claimed.** Stated here so a future reader neither "fixes" it nor over-trusts it.

With the version stamp above, each dispatch uploads a filename no dispatch has used, so the
upload genuinely happens and `skip-existing` is BELT-AND-BRACES for a re-run of the same run
number -- not the mechanism that makes repeat dispatches work. The draft had it the other way
round, and that inversion mattered: it left the upload leg permanently skipped after run 1 while
the document called the dry run "the only pre-release proof this channel gets".

Two independent things are proven, and it is worth keeping them separate:

- **The OIDC handshake**, which holds even on a skipped upload. Verified by reading the action's
  own `twine-upload.sh` rather than inferred from its docs: under trusted publishing it runs
  `INPUT_PASSWORD="$(python /app/oidc-exchange.py)"` and only reaches `twine upload` afterwards,
  so a missing or mismatched publisher fails at the exchange with `invalid-publisher` and never
  reaches the skip.
- **Index acceptance** -- that this project's actual metadata is one the index will take. This is
  what the version stamp buys, and what `twine check --strict` cannot give: it validates metadata
  RENDERING, not acceptance.

`pypi` keeps the default `false` because a real publish silently no-opping is a categorically
different event from a dry run tolerating a re-run. A real publish silently
no-opping is a categorically different event, which is why `pypi` keeps the default.

## The drift pin

A separate file means the dry run's build steps are a COPY of the release path's, and a copy can
stop matching the thing it claims to prove without anything going red. That is the standing
failure shape in this repo -- a guard that certifies a mechanism it no longer touches.

So the wiring test reads the build sequence from BOTH files and asserts they agree on four
values: the `pip install --require-hashes -r .github/build-requirements.txt` install, `python -m
build --no-isolation`, `twine check --strict`, and the `setup-python` `python-version`. Both ends
read from the files, neither hardcoded -- the same "both ends read from the file" idiom
`test_release_publish_wiring.py` already uses to compare the artifact name across `build` and
`attest`, and `test_ci_wiring.py` uses for the `npm ci` flags.

**The comparison must be guarded against its own vacuity, and the reviewed draft was not.** Four
equality checks between two extractions pass trivially when both extractions fail: `None ==
None`, or `"" == ""`, is green while the two files build differently -- the precise `all([])`
shape CLAUDE.md's guard-test section catalogues, reproduced in the one assertion whose whole
purpose is catching drift. Both precedents cited above are guarded and the draft copied the idiom
without the guard: `test_the_documented_install_command_is_the_one_ci_runs` carries three
(`assert len(ci_flags) == 1`, `assert expected`, `assert documented`) with an inline comment
naming this failure mode, and `test_build_and_attest_agree_on_the_artifact_name` carries `assert
upload_name and download_name` plus a hardcoded `== "dist"` anchor.

So the pin asserts, BEFORE comparing:

- each of the four extracted values is non-empty, with a message naming the file and the key whose
  match failed -- so "the helper stopped matching" reddens as itself rather than as a pass; and
- the number of `run:` steps enumerated in each build region equals a pinned count: **three** in
  `release-please.yml`'s `build` job, **four** in `testpypi.yml`'s job. The counts differ because
  `testpypi.yml` legitimately carries the version-stamp step, and pinning them separately is what
  stops an unexplained extra step (or a silently dropped one) reading as agreement. The branch
  guard is excluded from the count by being scoped to the region after checkout, so the two
  regions describe the same thing.

`test_build_and_attest_agree_on_the_artifact_name`'s hardcoded `"dist"` anchor is the model for
the first bullet, and the reviewed draft mischaracterised that anchor as redundant duplication
when it is precisely the non-vacuity guard.

## Testing

### The helper widening, and why the path parameter is REQUIRED

The reviewed draft said the two helpers "`_job_directives`/`_step_containing`, which close over
`RELEASE_PLEASE`" get a path parameter. Both halves were wrong, checked against the file:

- There are **five** functions in the chain -- `_rp_text`, `_job_directives`, `_step_containing`,
  `_permissions_block`, `_workflow_wide_directives` -- and they are defined interleaved with the
  tests rather than gathered at the top, so a reader scanning the header does not see them all.
- Only `_rp_text` touches `RELEASE_PLEASE`; the others reach the file THROUGH it, at two call
  sites. The widening therefore threads one parameter down five functions, not two.

**The parameter takes no default.** A defaulted path is a silent fail-open here, not a
convenience, and the reason is specific: `release-please.yml`'s workflow-wide permissions block is
byte-identical to the one `testpypi.yml` will carry (`permissions:\n  contents: read`). So a
forgotten path argument on the top-level-permissions assertion reads `release-please.yml`,
compares it to the value expected of `testpypi.yml`, and PASSES -- pinning nothing about the new
file at all. In the worst case the drift pin compares `release-please.yml` to itself and certifies
perfect agreement. Making the parameter required turns every one of those into a `TypeError` at
collection time, and every existing call site is updated in the same commit.

### Assertions in `tests/test_release_publish_wiring.py`

Every per-job permissions assertion resolves through `_permissions_block`, never an `in`/`not in`
probe over raw job text -- that helper exists precisely because a substring search over a job
block cannot distinguish a permission from a mention of one, and the reviewed draft reverted to
the shape it was written to replace. This matters concretely at PR 4, which adds `packages: write`
to this same file.

1. `release-please` exposes `tag_name: ${{ steps.release.outputs.tag_name }}`.
2. `pypi`'s `if:` is the literal `success() && needs.release-please.outputs.release_created ==
   'true'`, and its `needs:` is exactly `[release-please, build]`.
3. **The environment pair**: `pypi` declares `environment: pypi` and `testpypi` declares
   `environment: testpypi`, asserted together. The environment name is half of each trusted
   publisher's claim, so a swap or a drop breaks authentication with an error that names neither.
   The draft pinned only the first.
4. `pypi`'s permissions block is exactly `id-token: write` -- no `contents:` key AT ALL, not merely
   no `contents: write`. The exhaustive-block reasoning is what makes the absence meaningful.
5. **The endpoint pair**: `pypi`'s publish step carries no `repository-url`, AND `testpypi.yml`'s
   carries `repository-url: https://test.pypi.org/legacy/`. This is what stops the two mixups with
   real consequences -- a dry run reaching production PyPI, or a real release going to TestPyPI
   and never publishing at all.
6. **The `skip-existing` pair**, naming the forbidden value rather than the permitted one: `pypi`'s
   publish step must not set `skip-existing: true` (omitting it, taking the `false` default, is
   what this design does), and `testpypi`'s must set it `true`.
7. `release-assets` is gated identically; its permissions block is exactly `contents: write` with
   no `id-token`; and its upload step reads the tag from `needs.release-please.outputs.tag_name`
   **and sets `GH_REPO`**. The second half is not cosmetic: without it the assertion is satisfied
   by a step that cannot resolve a repository and therefore cannot run.
8. `build`, `attest`, `pypi` and `release-assets` all name the same artifact -- extending the
   existing two-job agreement to four, and KEEPING its hardcoded `== "dist"` anchor. That anchor
   is the non-vacuity guard, not the redundant duplication the draft called it.
9. `testpypi.yml`'s top-level `permissions:` is exactly `contents: read`, position-anchored on
   `\njobs:\n`, resolved with an explicit path argument.
10. `testpypi.yml`'s `on:` block contains `workflow_dispatch` and NO other trigger. A dry-run
    workflow that gained a `push:` trigger would publish to a permanent public index on every
    commit.
11. `testpypi.yml` carries both the default-branch guard step and the version-stamp step. Each
    exists to stop a specific silent failure (a permanent publish of an unmerged tree; an upload
    leg that no-ops forever), so each needs a pin or it can be deleted as apparent clutter.
12. The drift pins, with the non-vacuity guards and the pinned per-file `run:`-step counts set out
    under "The drift pin" above.

### The sdist guard, in `tests/test_packaging.py`

This PR is what makes the sdist public and permanent, and nothing anywhere pins its contents.
Measured on a real build rather than reasoned about: `python -m build` ships **166
`tests/test_*.py` modules** inside the sdist, via distutils' default `optional` glob
(`tests/test*.py`), because there is no `MANIFEST.in` and `packages.find` names only `sluice*`.
`tests/test_packaging.py` never sees this -- it builds `--wheel` explicitly, from a copied tree
containing only `sluice/`, `pyproject.toml`, `LICENSE` and `README.md`.

Today's shipped set is clean, so this is a gate gap rather than a leak. But the shipped subset is
also USELESS: the same non-recursive glob leaves out `conftest.py` and the fixture packages beside
it, so the tests that do ship cannot run. Shipping a broken test tree is worse than either
shipping a working one or shipping none, so:

- **`MANIFEST.in` carries `prune tests`**, and the sdist ships the package, metadata and docs
  only.
- A new guard builds `--sdist` **from the real tree** into a tmpdir (never the repo root -- see
  that module's docstring), opens it with `tarfile`, and asserts the set of top-level entries is
  exactly an allowlist. A positive allowlist, not a negative sweep, so it cannot pass by matching
  nothing; and the enumeration is asserted non-empty first.
- Its falsify partner, per the module's existing convention, rebuilds with `prune tests` removed
  and asserts the guard fires.

Not duplicated here: SHA-pin format and the trailing-version-comment convention remain zizmor's
job, and zizmor reaches `testpypi.yml` automatically because `ci.yml` globs the directory.

### Falsification: a witness per assertion, not a blanket rule

The reviewed draft said every assertion would be witnessed "by DELETING or MOVING the thing it
checks -- never by ADDING". That rule is right for positive assertions and **impossible for
negative ones**: assertions 4, 5, 6 and 7 pin the ABSENCE of a key, and verified against the tree,
none of `environment:`, `skip-existing` or `repository-url` appears anywhere in
`.github/workflows/` today. There is nothing to delete. "MOVING" was also left undefined for a
YAML key.

So the witness is named per assertion instead:

| Assertion | Mutant | Kind |
|---|---|---|
| 1, 2, 7 (tag), 8, 9, 12 | delete the output / the `needs:` entry / the `env:` line / the step | delete |
| 3 | change `environment: pypi` to `environment: testpypi` (and vice versa) | swap |
| 4 | ADD `contents: read` to `pypi`'s permissions block | add |
| 5 | ADD `repository-url` to `pypi`'s publish step | add |
| 6 | ADD `skip-existing: true` to `pypi`'s publish step | add |
| 7 (permissions) | ADD `id-token: write` to `release-assets` | add |
| 10 | ADD `push:` to `testpypi.yml`'s `on:` block | add |
| 11 | delete the branch-guard step; delete the version-stamp step | delete |
| 12 (vacuity) | delete the `setup-python` step from `testpypi.yml` | delete |

An ADD-mutant is legitimate for a negative assertion and is not the equivalent-mutant trap the
general rule warns about -- that trap is adding a check BESIDE an original that still fires. Here
the added key is the violation itself.

Two standing requirements on every row: confirm the named test reddens **by node id**, and
confirm no PRE-EXISTING test catches the same mutant -- a mutation killed by an existing test
witnesses nothing about a new one, and the coarse witness for assertion 1 is already killed by
three of them. Content-address the caches first (`python -m compileall -q -f
--invalidation-mode checked-hash sluice tests scripts`), since these are size-preserving edits.

## Manual prerequisites (repo owner only)

In order, after this PR merges:

1. Create the `testpypi` and `pypi` GitHub environments. **No protection rules** -- the repo
   owner's explicit decision on 2026-08-21, having been offered a required-reviewer rule and
   declined it. The consequence is recorded under Risks.
2. Add a pending publisher at `test.pypi.org/manage/account/publishing/`: owner `MrReasonable`,
   repo `sluice`, workflow **`testpypi.yml`**, environment `testpypi`.
3. Add a pending publisher at `pypi.org/manage/account/publishing/`: owner `MrReasonable`, repo
   `sluice`, workflow **`release-please.yml`**, environment `pypi`.
4. Dispatch the TestPyPI dry run and confirm an upload lands. This is the step that converts
   Trusted Publishing in this repo from assumed to measured; nothing in CI can assert it.

The two workflow filenames differ by design (see "Where the dry run lives"), and a publisher
naming the wrong one fails with `invalid-publisher` rather than falling back -- so step 2 naming
`release-please.yml`, or step 3 naming `testpypi.yml`, is a real and easy mistake with a
confusing error.

## Supersedes the sequencing spec's per-channel-hold MODEL

The reviewed draft said this superseded one SECTION. It supersedes a model, and three further
passages rest on it. Naming only one is how the other three become stale prose that reads as
current. This PR edits all of them:

| Document | Passage | Why it falls |
|---|---|---|
| sequencing spec | "Manual-prerequisite timing" | built entirely on per-channel holds |
| sequencing spec | Sequencing rationale, lines 37-40 | justifies PyPI going first by release ORDERING, which no longer varies |
| sequencing spec | Risks, fourth bullet (lines 168-174) | "Mitigated only by the explicit hold instructions" -- those instructions are gone |
| sequencing spec | GHCR visibility timing bullet | "a late toggle only delays discovery" assumed incremental releases |
| `README.md` | lines 134, 138-139 | "there is no PyPI release yet" / "There is no packaged in..." become false the moment anything publishes |

The Sequencing rationale is rewritten to rest on the dispatch dry run rather than on ordering --
PyPI still belongs first, because it is the channel whose mechanism can be proven before it is
load-bearing, and that argument survives the model change intact.

**1. 1.0.0 releases only after every channel is ready.** PR #124 stays parked until PRs 3-7 have
merged and every manual prerequisite is configured.

This REMOVES the partially-failed-release risk the per-channel holds existed to mitigate, and
REPLACES it with a sharper one: `pypi`, `release-assets`, `docker`, `linux-packages` and
`homebrew` all execute for the first time SIMULTANEOUSLY, on the 1.0.0 merge, with no environment
protection rule to pause any of them. Nothing is proven incrementally by a release that no longer
happens. The dispatch-triggered dry run therefore stops being a nicety and becomes this channel's
only pre-release proof -- and PRs 4-6 should each carry an equivalent rather than inheriting this
one by precedent.

**2. The `job-sluice` name is NOT claimed early. Decided 2026-08-21, reversing the draft.**

Nothing publishes to `pypi.org` until 1.0.0. The name stays unreserved until then, and that is
accepted rather than mitigated.

This reverses what the reviewed draft recorded as settled -- claim the name early with a `0.1.x`
publish, left installable. Four reviewers independently established that **no mechanism existed to
do it**, and the draft cited that publish twice as the only proof of the production path, so the
contradiction propagated into the Risks section:

- `pypi` is gated on `release_created == 'true'`, so only a release-please release can fire it.
- `release-please.yml` has no `workflow_dispatch`, and this document rejects adding one.
- `testpypi.yml` is pinned to `test.pypi.org`.
- The one open release PR is 1.0.0, which decision 1 parks.
- A hand-cut tag is forbidden outright: releases are cut by merging release-please's PR, never by
  tagging from a shell.

The two routes, costed:

**(a) Claim it, via a `Release-As: 0.1.1` commit footer.** Works, and needs decision 1 restated to
say the hold covers 1.0.0 and not a name-claiming 0.1.x. The cost is the changelog: `Release-As`
sweeps every commit accumulated since 0.1.0 into the 0.1.1 release, **including the two BREAKING
config changes** currently staged for 1.0.0. That publishes breaking changes under a patch version
and leaves 1.0.0 with little to say. Recoverable only by hand-editing the release PR's changelog,
which this repo does anyway -- but deliberately, not as cleanup.

**(b) Drop the early claim and accept the risk.** The name stays unreserved until 1.0.0 --
weeks -- while public issue #104 names it. If it is taken, the remedy is another rename, confined
to `pyproject.toml`, `cli.py`'s `prog=`/`--version`, and the two tests that pin the name.

**Route (b) is the decision.** The mechanism in (a) costs a corrupted release history to buy
protection against a low-probability event whose remedy is bounded and mechanical. Breaking
changes published under a patch version are a permanent, public misstatement of what that release
contained; a squatted name costs a rename of three files and two tests.

Two consequences follow, and both are stated here rather than left implicit:

- **Nothing in this document may cite an early publish as a mitigation.** The Risks section is
  written accordingly, and the first risk below -- that `release-please.yml`'s own publisher entry
  is unexercised until the 1.0.0 merge -- is now genuinely unmitigated rather than covered by a
  publish that was never going to happen.
- **The exposure window is a reason to keep PRs 4-7 moving, not a reason to hold them.** The
  window closes when 1.0.0 ships, so every week the remaining channels take is a week the name is
  unreserved. That is the honest cost of decision 1, and it belongs beside decision 1 rather than
  buried in a risk list.

If the name IS taken before 1.0.0, the remedy is a rename confined to `pyproject.toml`'s `name`
and `[project.scripts]`, `cli.py`'s `prog=`/`--version`, and the two tests that pin it
(`test_release_version.py`, `tests/test_docs_claims.py`). The import package, the `SLUICE_*` env
vars and the `~/.config/sluice/` XDG path are unaffected -- they are already independent of the
distribution name by deliberate decision.

## Risks

- **Trusted Publishing on `pypi.org` is proven only by its first real use.** The dry run proves
  the mechanism, the environment wiring and the action version, but against `testpypi.yml`'s
  publisher entry -- a different workflow filename, and therefore a different entry -- so
  `release-please.yml`'s own entry is genuinely first exercised by whatever publishes first. Under
  decisions 1 and 2 that is the 1.0.0 merge, with no earlier publish to prove it. **This is the
  single largest residual risk in the design and it is accepted, not mitigated.** The dry run
  proves everything about the mechanism except the one publisher entry that matters on the day.
- **No environment protection rule.** Declined by the owner. Once the trusted publishers exist,
  any merge of a release PR publishes with no human step; combined with decision 1, the first such
  merge fires five first-run publish jobs at once.
- **A separate dry-run file proves a different publisher entry than the release path uses.**
  Inherent to the reusable-workflow limitation, not designed away. Bounded by the drift pin for
  the build steps.
- **`release-please` tags and cuts the GitHub Release BEFORE any of these jobs run** (carried from
  PR 2). With this PR, a `build` failure now additionally means no PyPI publish and no release
  assets -- a tagged, publicly visible release with nothing attached and nothing published. The
  `GH_REPO` defect this review caught was an instance of exactly that shape, reaching the tag and
  then failing.
- **The sdist's contents are guarded from this PR onward, but everything published before the
  guard lands is unexamined.** Nothing has been published yet, so the exposure is zero today; it
  becomes non-zero the moment anything publishes ahead of the guard.
- **`job-sluice` is unreserved on `pypi.org` until 1.0.0 ships** (decision 2). A pending publisher
  does not reserve a name, and public issue #104 names the target. Accepted deliberately; the
  remedy if it is taken is a bounded rename, and the window shrinks as PRs 4-7 land.

## Definition of done

```bash
# This worktree has no .venv of its own -- the interpreter lives in the main checkout, and a bare
# `python` can silently resolve to a version-manager shim outside an activated venv. Create one
# here first, then call it explicitly:
python3 -m venv .venv && .venv/bin/pip install -e '.[test]' ruff==0.15.21 zizmor

.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v
.venv/bin/python -m pytest tests/test_packaging.py -v
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```

zizmor is named explicitly because this PR adds a workflow file holding `id-token: write` and one
holding `contents: write`, and because "Testing" above leans on it to justify not re-checking
SHA-pin format in pytest.
