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
this one -- see "Revises the sequencing spec's release plan" below.

## Revised after two plan-review rounds, 2026-08-21

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

**Round 2 (24 findings, 0 Critical, 13 High) reviewed the REPAIRS**, and found the defect rate had
not dropped -- it had MOVED. Almost every finding was in text written to fix round 1:

- **The version stamp could not fail.** `re.sub` returns its input unchanged on no match, so the
  stamp could silently no-op and hand a green dispatch that uploaded nothing -- round 1's exact
  defect, rebuilt inside its own fix. Three reviewers found it independently.
- **The sdist guard was broken four ways** (vacuous allowlist over a one-element set; a falsify
  partner that could not falsify, measured; two mutually exclusive halves; a docstring cited as
  supporting its own opposite). Five reviewers, seven findings, against scope this document added
  by its own argument -- so it was cut down rather than repaired in place.
- **The witness table watched the wrong half** of the paired assertions it had just introduced,
  and left the drift pin's equality -- its whole purpose -- with no mutant at all.
- **The supersession table** miscited one passage, missed another, and prescribed a rewrite that
  turned a passage into itself while contradicting this document's own Risks section.
- **The rename cost was understated by an order of magnitude** (52 files, not three), and that
  figure was the entire basis for a decision.
- **The README edits were scope creep concealed by a misquote** of the sequencing spec cell that
  allocates them to PR 7.

Two of round 2's findings were resolved by DELETION rather than repair: revising decision 1
restored the sequencing spec's model, which withdrew the supersession table and everything wrong
with it. That is the cheaper kind of fix and it only became visible once the cost basis was
corrected.

The lesson to carry into PR 4: **a repair is the least-reviewed text in a document, and this
codebase's failure mode is a fix that reproduces its own bug one layer up.** Both rounds found
that shape; the second found it in the first's output.

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
`.github/workflows/testpypi.yml`; extensions to `tests/test_release_publish_wiring.py`; a
minimal sdist-contents guard in `tests/test_packaging.py` with a `MANIFEST.in` to make it hold;
and an edit to `docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md`,
whose sequencing this PR revises.

That last one is diff, not context, and was missing from this list in the first draft. A scope
list that enumerates only code is how a doc edit gets skipped at implementation time and lands as
drift.

**`README.md` is NOT in scope, and the second review round is why.** The draft put README's
install claims in this PR on the reasoning that they "become false the moment anything publishes".
Two reviewers established the opposite: those lines sit inside README's `## Install` section, and
the sequencing spec's row 7 allocates "`docs/INSTALL.md` **+ README install section**" to PR 7 --
which this document's own Out list had quoted with the second half dropped, and then taken. The
install docs are now due BEFORE 1.0.0 rather than after every channel (see the sequencing
revision below), but they are still PR 7's work, not this PR's.

**Amended during this PR's review round: README's PyPI NEGATIONS are in scope after all, and
only those.** `pyproject.toml` sets `readme = "README.md"`, so README.md IS the distribution's
`Description` -- verified by building a real sdist and reading `PKG-INFO`, which carried "there
is no PyPI release yet" and "There is no packaged install yet -- no PyPI release" verbatim. That
text is permanent in the uploaded metadata of whatever release ships it, and under decision 1
below that release is 1.0.0, days after this PR. A doc allocated to PR 7 can wait; a false claim
baked into an artefact nobody can withdraw cannot. The rest of the `## Install` section -- the
restructuring, `docs/INSTALL.md`, the per-channel instructions -- stays PR 7's, untouched here.

The sdist guard is an addition to what the sequencing spec allocated PR 3, and it is deliberately
MINIMAL after five reviewers filed seven findings against the draft's more ambitious version. The
justification for keeping any of it is specific rather than general: PR 3 is what makes the sdist
PUBLIC AND PERMANENT. Before it, `build`'s sdist expired with the run artifact in a day; after it,
it is on an index that never forgets.

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

It is still not adopted, and the second review round corrected the reason. The draft deferred
extraction to PR 4 on the grounds that PR 4 would add a third consumer of the build sequence.
**That trigger does not fire.** Checked against the sequencing spec: PRs 4, 5 and 6 all CONSUME
the `build` job's wheel -- Docker installs it, `nfpm` packages it, the Homebrew bump job reuses
it -- and none of them re-runs the build sequence. The only basis for the claimed third copy was
this document's own suggestion that PRs 4-6 each carry their own dry run, which is a "should" it
never adopted. The trigger was circular, and both halves were written here.

So the honest position is simpler: **two copies is the steady state, and the drift pin is the
right answer to it indefinitely.** No extraction is scheduled. If a future PR ever does add a
third copy of the build sequence, that is the moment to reconsider -- and it would also require
widening `ci.yml`'s zizmor invocation from `.github/workflows/` to `.github/actions/`, since a
composite action no linter audits would silently drop this repo's SHA-pinning discipline at
exactly the point the build sequence became shared.

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
-- and under decision 1 below it first executes on the 1.0.0 merge, where `build`, `attest`,
`pypi` and `release-assets` each run for the first time and two of those four publish (`pypi` to
PyPI, this job to the release itself). The "beside four other first-run publish jobs" reading
this passage carried belonged to the WITHDRAWN draft, whose five-channels-at-once risk the
revision to decision 1 removed; Docker, deb/rpm and Homebrew are not in this workflow and land in
1.1.0. Passing the repository through `env:` rather than a `--repo` flag also keeps it consistent
with the `TAG` treatment directly above.

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
          echo "::error::Dispatch this workflow from the default branch -- a TestPyPI upload is permanent and public, so an unmerged branch must not become the tree of record."
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
          import os, pathlib, re, sys
          f = pathlib.Path('sluice/__init__.py')
          text, n = re.subn(r'(__version__ = \")([^\"]+)(\")',
                            rf'\g<1>\g<2>.dev{os.environ[\"RUN\"]}\g<3>',
                            f.read_text())
          if n != 1:
              sys.exit(f'::error::version stamp matched {n} times, expected exactly 1 -- '
                       'sluice/__init__.py no longer has the shape this step assumes')
          f.write_text(text)
          "
        env:
          RUN: ${{ github.run_number }}
      - run: pip install --require-hashes -r .github/build-requirements.txt
      - run: python -m build --no-isolation
      - run: twine check --strict dist/*
      - name: Prove the stamp reached the artefacts before anything is uploaded
        run: |
          ls dist/ | grep -q "\.dev${RUN}" || {
            echo "::error::no built artefact carries .dev${RUN}; the stamp did not take effect"
            exit 1
          }
        env:
          RUN: ${{ github.run_number }}
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
while doing nothing. TestPyPI filenames are immutable, so with a fixed declared version index
ACCEPTANCE is exercised exactly once, ever -- and `twine check --strict` does not substitute for
it: it validates metadata rendering, not whether the index accepts the upload. This repo has
already been bitten by an index-side `InvalidConfigError` on a license/classifier pairing that
rendered fine locally. `.devN` is valid PEP 440, sorts below any real release, and the pattern
rewrites only the quoted value, preserving the `# x-release-please-version` marker that
`tests/test_release_version.py` pins. It mutates only the ephemeral CI checkout; nothing is
committed.

**`re.subn` and the explicit exit are the load-bearing part, and the first draft of this step had
neither.** Three reviewers independently found the same defect: `re.sub` returns its subject
UNCHANGED when the pattern does not match, raises nothing, and the step would have written the
unchanged text back and exited 0. Any drift in that version line -- a quote style, a type
annotation, a move to another module -- would silently disarm the stamp, the build would re-emit
the already-uploaded version, `skip-existing: true` would swallow the duplicate, and the dispatch
would go green having uploaded nothing. That is precisely the failure this step was added to
remove, reproduced one layer up inside its own fix, against what this document calls the channel's
only pre-release proof. `n != 1` therefore exits non-zero and says why.

The separate **"prove the stamp reached the artefacts"** step is the second line of defence, and
it is not redundant with the first: the substitution succeeding says the SOURCE changed, not that
the BUILD consumed it. `pyproject.toml` declares `dynamic = ["version"]` reading
`sluice.__version__`, so the two are coupled today -- but that coupling is exactly the kind of
thing a packaging change alters without anyone noticing, and this check observes the artefact
rather than the intent. It runs after `twine check` and before the publish, so a stamp that did
not take stops the run rather than reaching the index.

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
round, and the inversion is what left the upload leg permanently skipped after run 1.

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
different event from a dry run tolerating a re-run.

## The drift pin

A separate file means the dry run's build steps are a COPY of the release path's, and a copy can
stop matching the thing it claims to prove without anything going red. That is the standing
failure shape in this repo -- a guard that certifies a mechanism it no longer touches.

So the wiring test reads the build sequence from BOTH files and checks five things agree: the
`pip install --require-hashes -r .github/build-requirements.txt` install, `python -m build
--no-isolation`, and `twine check --strict` are each checked for EXACT membership in both files'
extracted run-command sets -- a membership probe, not an equality between two extractions. The
`setup-python` `python-version` and the `pypa/gh-action-pypi-publish` ref are the two genuine
equalities: each is extracted once per file and the two extractions compared directly. The
publish-action ref is the fifth value, added after this section was first drafted, so a version
bump landing on one file and not the other has something to catch it. Both ends read from the
files, neither hardcoded -- the same "both ends read from the file" idiom
`test_release_publish_wiring.py` already uses to compare the artifact name across `build` and
`attest`, and `test_ci_wiring.py` uses for the `npm ci` flags.

**The comparison must be guarded against its own vacuity, and the reviewed draft was not.** An
equality check between two extractions passes trivially when both extractions fail: `None ==
None`, or `"" == ""`, is green while the two files build differently -- the precise `all([])`
shape CLAUDE.md's guard-test section catalogues, reproduced in the one assertion whose whole
purpose is catching drift. Both precedents cited above are guarded and the draft copied the idiom
without the guard: `test_the_documented_install_command_is_the_one_ci_runs` carries three
(`assert len(ci_flags) == 1`, `assert expected`, `assert documented`) with an inline comment
naming this failure mode, and `test_build_and_attest_agree_on_the_artifact_name` carries `assert
upload_name and download_name` plus a hardcoded `== "dist"` anchor.

So the pin's non-vacuity guard differs by check, rather than being one shared bullet across all
five:

- the three build-command checks guard themselves structurally: EXACT membership in an extracted
  set is `False` when that set is empty, so a broken extraction cannot make the two sides agree by
  both having found nothing -- the same shape as the hardcoded `"dist"` anchor below;
- the `python-version` and publish-action-ref extractions each assert their own match before
  returning, naming the file and the job whose extraction failed, so "the helper stopped matching"
  reddens as itself rather than as a pass; and
- the number of `run:` steps in each POST-CHECKOUT region equals a pinned count: **three** in
  `release-please.yml`'s `build` job (install, build, `twine check`), and **five** in
  `testpypi.yml`'s job (version stamp, install, build, `twine check`, stamp-proof) -- a SCOPE
  assertion, not an equality between the two counts: they are deliberately different, because
  `testpypi.yml` legitimately carries the two extra steps that make a dispatch prove something, and
  pinning them separately, rather than comparing them to each other, is what stops an unexplained
  extra step -- or a silently dropped one -- reading as agreement. The branch guard sits BEFORE
  checkout and is outside the region by construction, so the two regions describe the same thing.
  Both counts were independently recounted against the real `build` job and this document's own
  proposed YAML.

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
11. `testpypi.yml` carries the default-branch guard, the version stamp AND the stamp-proof step
    -- and for the stamp, presence is explicitly NOT enough. Pin that it uses `re.subn` and exits
    non-zero unless exactly one substitution occurred, and that a separate step checks the built
    artefacts carry the `.dev` suffix. A step can be present and inert, which is how the draft's
    version of this step recreated the defect it was added to fix; an assertion that only asks
    "is it there?" certifies the inert version just as happily.
12. The drift pins, with the non-vacuity guards and the pinned per-file `run:`-step counts set out
    under "The drift pin" above.

### The sdist guard, in `tests/test_packaging.py`

Deliberately minimal. The draft's more ambitious version drew seven findings from five reviewers
and was broken in four independent ways, so what survives is the smallest check that actually
holds, with the rest deferred to PR 7 alongside `docs/INSTALL.md`.

**What is actually in the sdist**, measured rather than assumed:

```text
LICENSE  MANIFEST.in  PKG-INFO  README.md  job_sluice.egg-info  pyproject.toml  setup.cfg  sluice
```

Two corrections to the draft fall straight out of that list. It omitted `MANIFEST.in` and
`setup.cfg`. And **no docs ship at all** -- `prune tests` alone produces no docs tree, and getting
one would need a `graft` this design does not specify and does not want. The draft's phrase "ships
the package, metadata and docs only" is therefore struck: it described an sdist that does not
exist, and an implementer reading it as a requirement would have added a `graft docs` that
published `docs/superpowers/` -- a tree outside the neutrality rule (which binds `sluice/` and
`tests/`), and one `tests/test_no_leaked_files.py` covers only in part. That file DOES sweep
docs/ content, but reaches exactly two things there: absolute home paths, over every tracked
file (its `_GATE_PATHSPEC` is empty, which means exactly that), and static content left in
`docs/**/*.j2` after Jinja and HTML are stripped. Neither reads docs/ PROSE for the employer
names, locations or contact details the neutrality rule is about, so the tree is partially
covered rather than reviewed. No count is given here on purpose: a file total in prose goes
stale silently, and this one already had. A single loose word in a spec is all that separated
those two outcomes.

**The guard:**

- **Strip the root prefix before asserting anything.** Every member of an sdist is
  `job_sluice-<version>/<path>`, so "the set of top-level entries" is ONE element -- identical
  whether the tarball is clean or carries 166 test modules. The draft's two stated defences
  (a positive allowlist; assert non-empty) neither bite on a one-element set that is exactly
  equal to itself. Derive the prefix from `PKG-INFO`'s parent and assert the set of entries
  BELOW it equals the list above.
- **Both the guard and its falsify partner build from ONE shared helper**, `_build_sdist(dest)`,
  mirroring the existing `_build_wheel`: it copies the TRACKED TREE -- whatever `git ls-files`
  reports -- into a tmpdir, overwrites `MANIFEST.in` last (with the real text, or a partner's
  mutated one), and builds there. This is not stylistic. The draft said the guard should build
  "from the real tree" while its partner "rebuilds with `prune tests` removed" -- and those are
  mutually exclusive, because removing `prune tests` from the real tree means editing the
  repository's own `MANIFEST.in`. It also inverted the module's docstring, which builds from a
  copy precisely to keep `build/` and `.egg-info` out of the repo root. Measured: a
  `_build_wheel`-shaped copy (no `tests/`) ships zero test members with or WITHOUT `prune
  tests`, so the draft's partner would have been red while the guard stayed green -- a falsify
  partner that cannot falsify.
- **Why the tracked tree and not a hand-listed subset**, which is what this section's own first
  form specified -- three trees plus four root files. Measured, the
  hand-list made three real `MANIFEST.in` changes INVISIBLE: `graft scripts` and `graft .github`
  each found nothing to graft, and `include sluice.yaml.example` named a file the copy did not
  contain, so all three left the root-entry equality green while the real tree would have
  shipped 8, 8 and 1 extra members respectively. Copying what git tracks removes the enumeration,
  and with it the unanswerable "which tree did we forget?". The root-entry set comes out
  IDENTICAL either way (135 members, 0.6s per build), so this changed the guard's REACH and not
  its verdict. `__pycache__` needs no explicit ignore any more: git tracks none of it, so its
  absence is structural rather than a rule to remember.
- **Assert scope, not just contents**: the archive's total member count is non-trivial, so a
  build that produced almost nothing is red rather than vacuously compliant.

**What this deliberately does NOT cover**, stated so the gap is known rather than assumed closed:

- **Root MEMBERS are not root CONTENTS.** `sluice` is ONE entry in the set the equality pins, so
  that assertion says nothing whatever about what is inside the package directory, and the
  archive's total member count is too coarse to notice one more file.
  `test_the_sdist_ships_every_packaged_template` closes that for the packaged templates
  specifically -- derived from the tree, so a second template beside the first is swept too --
  and nothing else inside `sluice/` is swept at all.
- **The guard observes what a CLEAN CHECKOUT ships, not what a working tree does**, because it
  copies only what `git ls-files` reports. That is the right side of the trade rather than a
  gap in the released artefact: `release-please.yml`'s `build` job checks out the tagged sha, so
  a clean checkout IS what PyPI receives. The residual it leaves is one step further out --
  `sluice/` membership in a REAL build is a filesystem walk (`packages.find` plus the
  `templates/*.html.j2` package-data glob), so a `python -m build` run locally in a dirty tree
  ships an untracked `.py` file under `sluice/` and nothing here or in
  `tests/test_no_leaked_files.py` (also `git ls-files`-based) would see it. Measured, not
  reasoned about: an untracked `sluice/untracked_probe.py` lands as a member and takes the
  archive from 135 to 136, while the root-entry equality and the member-count floor both stay
  green. Bounded rather than closed, and the bound is that a local build is not the release.
- `--no-isolation` makes membership depend on the build environment, and the `[test]` venv this
  guard runs in is not the release build's hash-locked set.

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
| 1, 2, 8, 9 | delete the output / a `needs:` entry / the artifact `name:` / the permissions line | delete |
| 3 (pypi half) | change `environment: pypi` to `environment: testpypi` | change |
| 3 (testpypi half) | change `environment: testpypi` to `environment: pypi` | change |
| 4 | ADD `contents: read` to `pypi`'s permissions block | add |
| 5 (pypi half) | ADD `repository-url` to `pypi`'s publish step | add |
| 5 (testpypi half) | DELETE `repository-url` from `testpypi`'s publish step | delete |
| 6 (pypi half) | ADD `skip-existing: true` to `pypi`'s publish step | add |
| 6 (testpypi half) | DELETE `skip-existing: true` from `testpypi`'s publish step | delete |
| 7 (permissions) | ADD `id-token: write` to `release-assets` | add |
| 7 (tag) | delete `TAG:` from the upload step's `env:` | delete |
| 7 (repo) | delete `GH_REPO:` from the upload step's `env:` | delete |
| 10 | ADD `push:` to `testpypi.yml`'s `on:` block | add |
| 11 (branch guard) | delete the branch-guard step | delete |
| 11 (stamp fails loudly) | change `re.subn` + exit back to a bare `re.sub` | change |
| 11 (stamp proof) | delete the stamp-proof step | delete |
| 12 (equality) | change `testpypi.yml`'s `python-version` to `"3.13"` | change |
| 12 (count) | delete the `twine check` step from `testpypi.yml` | delete |
| 12 (vacuity) | rename a key the extractor matches, so it returns nothing | change |

**Three rows exist because the second review round found the draft's table watching the wrong
half of what it pinned** -- the same defect class the revision was written to fix, reproduced in
the fix:

- **Assertions 5 and 6 are PAIRS and the draft mutated only the `pypi` side.** Assertion 5's
  unwitnessed half is the one stopping a dry run reaching production PyPI, which is the single
  worst outcome in this design. Assertion 3 got its "and vice versa"; 5 and 6 did not.
- **Assertion 7's `GH_REPO` half was folded into a generic "the `env:` line" row**, which is
  satisfied by deleting `TAG:` -- so the half added in response to round 1's most-corroborated
  finding had no witness of its own.
- **Assertion 12's equality -- the drift pin's entire purpose -- had no mutant at all.** Every row
  was a delete, and deletes are caught by the vacuity guard instead, so the four-way comparison
  was never exercised. A CHANGE mutant is the only shape that witnesses an equality. The draft's
  sole row for 12, "delete the `setup-python` step", is also removed as an equivalent mutant for
  the count pins: `setup-python` is a `uses:` step, so deleting it leaves both `run:` counts
  unchanged.

An ADD-mutant is legitimate for a negative assertion and is not the equivalent-mutant trap the
general rule warns about -- that trap is adding a check BESIDE an original that still fires. Here
the added key is the violation itself.

Two standing requirements on every row: confirm the named test reddens **by node id**, and
confirm no PRE-EXISTING test catches the same mutant -- a mutation killed by an existing test
witnesses nothing about a new one, and the coarse witness for assertion 1 is already killed by
three of them. The stale-bytecode hazard does NOT apply to these witnesses: every mutant here is in a YAML
workflow or `MANIFEST.in`, neither of which has a `.pyc`. `compileall --invalidation-mode
checked-hash` is for mutants in `sluice/` or `scripts/`; running it here would be a step that
cannot help, which teaches a false lesson about when it is needed.

## Manual prerequisites (repo owner only)

In order, after this PR merges:

1. Create the `testpypi` and `pypi` GitHub environments. **No protection rules** -- the repo
   owner's explicit decision on 2026-08-21, having been offered a required-reviewer rule and
   declined it. The consequence is recorded under Risks.
2. Add a pending publisher at `test.pypi.org/manage/account/publishing/`: PyPI Project Name
   **`job-sluice`**, owner `MrReasonable`, repo `sluice`, workflow **`testpypi.yml`**,
   environment `testpypi`.
3. Add a pending publisher at `pypi.org/manage/account/publishing/`: PyPI Project Name
   **`job-sluice`**, owner `MrReasonable`, repo `sluice`, workflow **`release-please.yml`**,
   environment `pypi`.
4. Dispatch the TestPyPI dry run and confirm an upload lands. This is the step that converts
   Trusted Publishing in this repo from assumed to measured; nothing in CI can assert it.

The two workflow filenames differ by design (see "Where the dry run lives"), and a publisher
naming the wrong one fails with `invalid-publisher` rather than falling back -- so step 2 naming
`release-please.yml`, or step 3 naming `testpypi.yml`, is a real and easy mistake with a
confusing error.

**The project name and the repo name deliberately differ, and the form asks for both.** The
distribution is `job-sluice`; the repository is `sluice`. Typing the repo name into the PyPI
Project Name field configures cleanly and then fails the dry run with the same
`invalid-publisher` -- an error that names NEITHER field, so there is nothing in it to point at
the one that is wrong. (`sluice` on PyPI is also not free: it has been squatted since 2015 by an
unrelated, dormant zfs-snapshot tool, which is why the distribution is named `job-sluice` at all.
See CLAUDE.md's conventions, which pin all three of distribution name, import package and
console script.)

### Operational notes for the first real release

**The `dist` artifact's retention is the recovery window, and it is 7 days rather than 1.** If
`pypi` fails at 1.0.0 -- most likely because the trusted publisher above does not exist yet, or
names the wrong workflow filename -- the only automated recovery is **re-running that failed job**
while the artifact `build` uploaded still exists. Re-running the WHOLE workflow does not work:
release-please sees the release already cut, `release_created` comes back `false`, and `build`
never runs to produce a new artifact. So the window has to be long enough to notice the failure,
diagnose it, fix a manual pypi.org configuration step, and re-run -- which 24 hours is not.
Raised from 1 day for exactly this reason: PR 2 set it when the artifact fed only `attest`,
minutes later in the same run, and #104 made it the input to an irreversible publish.

**`gh release upload` is not idempotent, and it is not atomic across assets.** Verified against
`gh` v2.97.0's own source, the version installed here (`pkg/cmd/release/upload/upload.go` sets
`opts.Concurrency = 5` and hands the wheel and the sdist to `shared.ConcurrentUpload`, which runs
them through an `errgroup` -- CONCURRENTLY, not one at a time). Concurrency is not a rollback,
though: a failure that hits one asset after the other has already landed leaves the release with
that ONE asset attached. The retry then fails hard on the one already there -- deliberately, since
the step carries no `--clobber` (see `release-assets` above: an asset that already exists means
something already uploaded, which should surface rather than be silently overwritten). Recovery is
manual: delete the attached asset from the release, then re-run the job. Worth knowing before it
happens, because the error names the existing asset rather than the interrupted upload.

**`pypi` can succeed while `release-assets` fails, and that state is not symmetric.** The two jobs
both declare `needs: [release-please, build]` and neither needs the other, so they run in
PARALLEL -- there is no ordering between them and no gate that stops one when the other fails.
The outcome to plan for is the irreversible half succeeding: the package is on PyPI, public and
permanent, while the GitHub Release for the same tag carries no assets at all. Nothing is broken
for an installing user (`pip install job-sluice` works), and the `attest` job is unaffected --
it is a third parallel job that attaches provenance to this REPO, not to the release page. What
is missing is the release page's own copy of the wheel and sdist.

The recovery is the same shape as the failed-`pypi` case above, and for the same reason:
**re-run the failed job only**, while the artifact `build` uploaded still exists. Re-running the
WHOLE workflow cannot work -- release-please sees the release already cut, `release_created`
comes back `false`, and BOTH `build` and `release-assets` are gated on that string, so no new
artifact is produced and the upload job never runs either. So the bound on this recovery is the
same 7-day artifact retention, and it is the only bound: past it, the assets have to be built
and attached by hand from the tagged commit.

Two things follow that are easy to get wrong in the moment. Re-running `release-assets` alone
must NOT be reached for by re-running `pypi` too -- that job's `skip-existing` is deliberately
left false, so a second upload of an already-published file FAILS, which is correct behaviour
and a confusing thing to trip over while fixing something else. And if the failure was a PARTIAL
upload rather than a total one, the note directly above applies first: delete the asset that did
land before re-running, because the step carries no `--clobber`.

## Revises the sequencing spec's release plan, and RESTORES its per-channel holds

**Decision 1 (2026-08-21, revised the same day after the second review round): 1.0.0 ships once
the PyPI channel is live** -- PR 3 merged, manual prerequisites configured, dry run green. Docker,
deb/rpm and Homebrew land afterwards and ship in 1.1.0.

The earlier form held 1.0.0 until every channel was ready. That version superseded the sequencing
spec's per-channel-hold model and traded its partially-failed-release risk for a sharper one: five
publish jobs firing for the first time simultaneously, with no environment protection rule.

**This revision restores the sequencing spec's model as written.** Releases resume their normal
cadence, each channel's manual prerequisite is configured after its own PR merges and before the
next release, and "Manual-prerequisite timing" applies exactly as that document states it. The
supersession table the previous draft carried is **withdrawn in full**, together with the two
findings against it -- a "why it falls" that rewrote a passage into itself, and a citation table
that missed one dependency and miscited another. The correct edit to the sequencing spec is now a
single paragraph recording that 1.0.0 is scoped to the PyPI channel rather than to all four.

Three consequences, and they are the three largest residual risks the previous draft carried:

- `release-please.yml`'s own trusted-publisher entry is exercised within days of PR 3 rather than
  at the end of the sequence -- so the one mechanism no test can assert stops being a bet held
  open for weeks.
- The five-first-run-jobs-at-once risk disappears. Each channel's first execution is its own
  release, which is what the sequencing spec assumed all along.
- The `job-sluice` name is claimed by a legitimate 1.0.0 almost immediately.

**Decision 2 is withdrawn as moot.** The early-name-claim question existed only because 1.0.0 was
weeks away; it is not. Nothing publishes a `0.1.x`, no `Release-As:` footer is needed, and the
changelog keeps its two BREAKING entries under 1.0.0 where they belong.

The corrected cost figure is recorded anyway, because the draft got it wrong by an order of
magnitude and the error is the instructive part. A rename is **52 tracked files**, not "three
files and two tests": 41 occurrences in `cli.py` alone, 12 `sluice/` modules, 14 test files, and a
PATH rename -- `plugins/job-sluice/job-sluice.plugin.zsh`, a shipped zsh completion whose internal
guards are deliberate no-ops, so it would fail SILENTLY. The draft reached its figure by counting
"the tests that pin the name" and mistaking that for the blast radius. Three reviewers caught it
independently and it was confirmed by direct measurement.

One consequence for PR 7: the install docs are now due BEFORE 1.0.0 rather than after every
channel, since README's install claims become false the moment 1.0.0 publishes. That is a
sequencing change to record in the sequencing spec -- not work for this PR, which is why
`README.md` left this document's Scope.

## Risks

- **Trusted Publishing on `pypi.org` is still proven only by its first real use** -- the dry run
  exercises `testpypi.yml`'s publisher entry, a different workflow filename and therefore a
  different entry. Under the revised decision 1 that first use is the 1.0.0 publish, days after PR
  3 rather than at the end of the sequence, and it is preceded by a green dry run of everything
  except the entry itself. Reduced from the draft's position, not eliminated.
- **No environment protection rule.** Declined by the owner. Once the trusted publishers exist,
  merging a release PR publishes with no human step.
- **A separate dry-run file proves a different publisher entry than the release path uses.**
  Inherent to the reusable-workflow limitation. Bounded by the drift pin for the build steps.
- **`release-please` tags and cuts the GitHub Release BEFORE any of these jobs run** (carried from
  PR 2). A `build` failure means a tagged, publicly visible release with nothing attached and
  nothing published. The `GH_REPO` defect round 1 caught was exactly that shape.
- **The sdist guard's two stated gaps** -- an untracked file under `sluice/` still ships and is
  invisible to both the guard and `test_no_leaked_files.py`; and `--no-isolation` makes membership
  depend on the build environment. Bounded by CI building from a clean checkout, real on a local
  build.

## Definition of done

```bash
# This worktree has no .venv of its own -- the interpreter lives in the main checkout, and a bare
# `python` can silently resolve to a version-manager shim outside an activated venv. Create one
# here first, then call it explicitly:
python3 -m venv .venv && .venv/bin/pip install -e '.[test]' ruff==0.15.21
.venv/bin/pip install --require-hashes -r .github/zizmor-requirements.txt  # the pin CI uses

.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v
.venv/bin/python -m pytest tests/test_packaging.py -v
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```

zizmor is named explicitly because this PR adds a workflow file holding `id-token: write` and one
holding `contents: write`, and because "Testing" above leans on it to justify not re-checking
SHA-pin format in pytest.
