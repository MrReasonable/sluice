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
`.github/workflows/testpypi.yml`; extensions to `tests/test_release_publish_wiring.py`.

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
          TAG: ${{ needs.release-please.outputs.tag_name }}
```

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
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: pip install --require-hashes -r .github/build-requirements.txt
      - run: python -m build --no-isolation
      - run: twine check --strict dist/*
      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
        with:
          repository-url: https://test.pypi.org/legacy/
          skip-existing: true
```

It builds rather than downloading an artifact, because there is no `build` job on this trigger to
download from. `contents: read` IS declared here, unlike on `pypi`, because this job does check
out source.

The dry run also exercises the ATTESTATION path, which was not obvious and is worth stating
because it widens what a dispatch proves: `attestations` defaults to `true` and the action
disables it for indexes other than PyPI by testing the repository URL against the regex
`pypi\.org` -- which `test.pypi.org` matches. So attestation generation runs here too, rather
than being silently skipped as a non-PyPI index.

**The `skip-existing: true` asymmetry is deliberate and is the one thing in this design that
looks like an inconsistency.** Stated here so a future reader does not "fix" it: TestPyPI
filenames are immutable, and the repo currently declares 0.1.0, so every dispatch after the first
would upload an identical filename and fail. A dry run whose purpose is repeatable proof must be
repeatable. It still proves what it exists to prove, because the OIDC exchange happens BEFORE any
upload. Verified by reading the action's own `twine-upload.sh` rather than inferred from its
docs: under trusted publishing it runs `INPUT_PASSWORD="$(python /app/oidc-exchange.py)"` and
only reaches `twine upload` afterwards, so a missing or mismatched publisher fails at the
exchange with `invalid-publisher` and never reaches the skip. A green run with a skipped file is
therefore still a green OIDC handshake. A real publish silently
no-opping is a categorically different event, which is why `pypi` keeps the default.

## The drift pin

A separate file means the dry run's build steps are a COPY of the release path's, and a copy can
stop matching the thing it claims to prove without anything going red. That is the standing
failure shape in this repo -- a guard that certifies a mechanism it no longer touches.

So the wiring test reads the build sequence from BOTH files and asserts they agree: the
`pip install --require-hashes -r .github/build-requirements.txt` install, `python -m build
--no-isolation`, `twine check --strict`, and the `setup-python` `python-version`. Both ends read
from the files, neither hardcoded -- the same "both ends read from the file" idiom
`test_release_publish_wiring.py` already uses to compare the artifact name across `build` and
`attest`, and `test_ci_wiring.py` uses for the `npm ci` flags. Change the release build and the
dry run can no longer silently certify a build path that no longer exists.

## Testing: extensions to `tests/test_release_publish_wiring.py`

Same file, same text-matching idiom, same comment-stripped `_job_directives`/`_step_containing`
helpers. `testpypi.yml` is a second file, so the existing helpers -- which close over
`RELEASE_PLEASE` -- get a path parameter rather than being copied; that is a widening of an
existing helper, not a new parallel one.

New assertions:

1. `release-please` exposes `tag_name: ${{ steps.release.outputs.tag_name }}`.
2. `pypi`'s `if:` is the literal `success() && needs.release-please.outputs.release_created ==
   'true'`, and its `needs:` is exactly `[release-please, build]`.
3. `pypi` declares `environment: pypi`.
4. `pypi` declares `id-token: write` and declares no `contents:` key AT ALL -- not merely no
   `contents: write`. The exhaustive-block reasoning above is what makes the absence meaningful,
   so the absence is what gets pinned.
5. **The endpoint pair.** `pypi`'s publish step carries no `repository-url` at all, AND
   `testpypi.yml`'s carries `repository-url: https://test.pypi.org/legacy/`. Asserted together,
   both read from the files. This is the assertion that stops the two mixups with real
   consequences: a dry run reaching production PyPI, or a real release going to TestPyPI and
   never publishing at all.
6. **The `skip-existing` pair**, for the same reason and in the same shape. The forbidden value
   is named rather than the permitted one: `pypi`'s publish step must not set `skip-existing:
   true` (omitting the input, taking the `false` default, is what this design does; stating
   `false` explicitly would also be fine), and `testpypi`'s must set it `true`. Pinning it as a
   pair is what stops the asymmetry being "tidied" into consistency in either direction.
7. `release-assets` is gated identically, declares `contents: write`, declares no `id-token`, and
   its upload step reads the tag from `needs.release-please.outputs.tag_name`.
8. `release-assets` and `pypi` download the same artifact `name:` that `build` uploads --
   extending the existing build/attest name-agreement assertion to all four jobs rather than
   adding two more independent hardcoded `"dist"` checks.
9. `testpypi.yml`'s top-level `permissions:` is exactly `contents: read`, position-anchored on
   `\njobs:\n` the same way the existing top-level check is.
10. **`testpypi.yml`'s `on:` block contains `workflow_dispatch` and NO other trigger.** A dry-run
    workflow that gained a `push:` trigger would publish to TestPyPI on every commit to the
    branch. Asserted on the trigger block's contents, not by absence-of-substring across the
    file.
11. The drift pins described above.

Not duplicated here: SHA-pin format and the trailing-version-comment convention remain zizmor's
job, per PR 2's reasoning, and zizmor now reaches `testpypi.yml` automatically (Verified, above).

**Falsification.** Every assertion above is mutation-witnessed by DELETING or MOVING the thing it
checks -- never by adding a competing value beside it, which is an equivalent mutant that leaves
the original firing and the suite green. The `.pyc` caches are content-addressed first
(`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`), since these
are size-preserving edits to YAML and a same-second restore is exactly the shape that has already
cost this repo a debugging session. Assertion 10 in particular must be witnessed by ADDING a
`push:` trigger and confirming it fails -- a negative assertion that has never been seen to fail
is the fail-open shape CLAUDE.md's guard-test section catalogues.

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

## Supersedes the sequencing spec's timing section

Two owner decisions on 2026-08-21 change
`docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md`, which this PR
updates in place rather than leaving to contradict this document:

**1. 1.0.0 releases only after every channel is ready.** The sequencing spec's
"Manual-prerequisite timing" section is built around per-channel holds -- "do not approve the
next release-please PR until [this channel's prerequisite] is done" -- because it assumed
releases would continue at their normal cadence while channels landed one at a time. They will
not. PR #124 (`chore(main): release 1.0.0`) stays parked until PRs 3-7 have all merged and every
manual prerequisite is configured.

This REMOVES the partially-failed-release risk that section exists to mitigate, and REPLACES it
with a sharper one: the `pypi`, `release-assets`, `docker`, `linux-packages` and `homebrew` jobs
will all execute for the first time SIMULTANEOUSLY, on the 1.0.0 merge, with no environment
protection rule to pause any of them. Nothing is proven incrementally by a release that no longer
happens. The dispatch-triggered dry run therefore stops being a nicety and becomes the only
pre-release proof this channel gets -- and PRs 4-6 should each carry an equivalent, for the same
reason, rather than relying on this document's precedent implicitly.

**2. The `job-sluice` name is claimed early.** A pending publisher does NOT reserve a project
name (PyPI's own docs are explicit: it "does not create a project or reserve a project's name
until it is actually used to publish"), and neither index currently has the project (Verified,
above). Holding 1.0.0 for weeks therefore leaves the name unclaimed for weeks -- while issue #104,
which is public, names it. This project already exists under `job-sluice` rather than `sluice`
because `sluice` was squatted, so the risk is demonstrated rather than hypothetical.

Once the `pypi` job is proven, a `0.1.x` publishes to real PyPI to register the name, and is left
INSTALLABLE rather than yanked -- the owner's decision, taken over the yank alternative on
2026-08-21. The version history then reads honestly as what 0.1.x actually was. The cost, stated
plainly because it is real: for the window between that publish and 1.0.0, `pip install
job-sluice` resolves to a build that predates every deployment channel and has no
`docs/INSTALL.md` describing it.

## Risks

- **Trusted Publishing remains the one mechanism no test can assert.** Mitigated only by manual
  prerequisite step 4, and only for TestPyPI -- the real `pypi.org` publisher names a different
  workflow file and is genuinely first exercised by the early-claim publish above. That publish
  is therefore the real proof of the production path, and it should be treated as such rather
  than as a formality.
- **No environment protection rule.** Declined by the owner. Once the trusted publishers exist,
  any merge of a release PR publishes with no human step. Combined with decision 1 above, the
  first such merge fires five first-run publish jobs at once.
- **A separate dry-run file proves a different publisher entry than the release path uses.**
  Inherent to the reusable-workflow limitation, not a choice that can be designed away. Bounded
  by the drift pin for the build steps, and by the early-claim publish actually exercising the
  production entry.
- **`release-please` tags and cuts the GitHub Release BEFORE any of these jobs run** (carried from
  PR 2's design). With this PR, a `build` failure now additionally means no PyPI publish and no
  release assets -- a tagged, publicly visible release with nothing attached and nothing
  published.

## Definition of done

```bash
.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```

zizmor is named explicitly because this PR adds a workflow file holding `id-token: write` and one
holding `contents: write`, and because "Testing" above leans on it to justify not re-checking
SHA-pin format in pytest.
