# Publish workflow skeleton (PR 2 of #104) design

Status: design, approved 2026-08-10.

This is PR 2 of the 7-PR packaging sequence locked in
`docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md`. That spec
already fixes PR 2's scope to exactly two jobs — `build` and `attest` — and defers everything
else (PyPI publish, Docker, deb/rpm, Homebrew, release-asset uploads) to PRs 3-6. Issue #104
already locks the mechanism (job table, permissions, action choices) from the PR #103 planning
pass. This document is the PR-2-specific decisions #104 and the sequencing spec leave open: the
exact job shape, the artifact handoff between `build` and `attest`, the one-time edit the
existing `release-please` job needs before anything can gate on it, and what
`tests/test_release_publish_wiring.py` pins.

## Scope

In: two new jobs in the existing `.github/workflows/release-please.yml` — `build` (sdist+wheel,
`twine check --strict`, upload the result as a run artifact) and `attest` (download that
artifact, run `actions/attest-build-provenance` over it). Both gated on release-please's
`release_created` output so they only run on an actual release, never on an ordinary push to
`main`. That output does not exist on the `release-please` job today (see "Job definitions"
below) — exposing it is in scope for this PR, since nothing downstream can gate on an output
that was never wired to begin with. A new `tests/test_release_publish_wiring.py`, in
`tests/test_ci_wiring.py`'s text-matching style.

Out: the `pypi`, `docker`, `linux-packages`, `release-assets`, and `homebrew` jobs (PRs 3-6, per
the sequencing table). The `build` job's artifact is produced here but not consumed by anything
outside this workflow run until PR 3 adds a job that downloads it — that is expected and matches
the sequencing spec's stated dependency (PRs 3 and 5 both depend on PR 2's `build` job, not on
each other).

## Where the jobs live

Extended in place in `.github/workflows/release-please.yml`, not a second workflow file. #104's
own reasoning for gating on `release-please`'s job output rather than an `on: release: published`
trigger — deterministic wiring, since GitHub does not raise trigger events from
`GITHUB_TOKEN`-authored activity and a separate event-triggered workflow would be silently inert
— only holds if `build`/`attest` can read `needs.release-please.outputs.release_created`
directly, which requires being jobs in the same workflow file. A second file reached via
`workflow_run` is technically possible (that trigger isn't subject to the same
`GITHUB_TOKEN`-recursion restriction, since the push to `main` that starts `release-please.yml`
is a genuine human merge, not machine-authored), but the release-please outputs don't cross a
`workflow_run` boundary for free — a second file would need to re-derive them via the API. Nothing
in #104 or the sequencing spec asks for that complexity, so it's rejected.

## Job definitions

### `release-please` (existing job — gains one output)

Found while reviewing this plan, not assumed from #104's prose: read against the live
`.github/workflows/release-please.yml`, the `release-please` job exposes no `release_created`
output today. Its `googleapis/release-please-action` step carries no `id:` (only the earlier
`app-token` step does), and the job has no job-level `outputs:` key at all. Without an edit,
every `needs.release-please.outputs.release_created` reference below resolves to an empty
string, `== 'true'` is always false, and `build`/`attest` never run on a real release — silently,
which is exactly the failure shape "Where the jobs live" already rejects a `workflow_run`-based
design for, just relocated into the job this plan keeps.

Two additions, nothing else in the job changes:

```yaml
release-please:
  runs-on: ubuntu-latest
  permissions:
    contents: read
  steps:
    - uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
      id: app-token
      with:
        # ... unchanged ...
    - uses: googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7 # v5.0.0
      id: release
      with:
        # ... unchanged ...
  outputs:
    release_created: ${{ steps.release.outputs.release_created }}
```

`id: release` on the action step, and the job-level `outputs:` block naming it. The App-token
minting, the action's own `with:` block, and the job's existing `permissions:` are untouched.

### `build`

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
    - uses: actions/upload-artifact@<sha> # vX.Y.Z
      with:
        name: dist
        path: dist/
        retention-days: 1
```

Python 3.12 (the floor, matching the `lint` job's single-version precedent — there is exactly one
release build to make, not a matrix to prove compatibility across).

**Deliberately NOT `--no-isolation`.** `tests/test_packaging.py`'s `_build_wheel` uses
`--no-isolation` for speed (~0.6s per build, run hundreds of times across the suite) against a
COPIED source tree, and its own docstring is explicit that the offline claim rests on the flag
rather than the suite's network guard, which doesn't reach a subprocess. The real release build
here runs once, on a runner with real network, and should get `python -m build`'s normal
isolated behavior — a fresh ephemeral build environment provisioned from the pinned
`[build-system]` requirements, the same thing a contributor following `docs/INSTALL.md`'s "build
from source" instructions would get. Reusing `--no-isolation`'s speed optimization here would
build against whatever happens to already be on the runner's Python instead.

Artifact name is `dist`, retention 1 day — it's consumed by `attest` (and, from PR 3 on, by
`pypi`/`docker`/`linux-packages`) within the same run, minutes later. Nothing needs it to survive
in the Actions UI: every later channel PR publishes the real thing it produces to PyPI, GHCR, or
GitHub release assets, and download activity against a same-run artifact doesn't consume the
retention window's purpose either way.

### `attest`

```yaml
attest:
  needs: [release-please, build]
  if: success() && needs.release-please.outputs.release_created == 'true'
  runs-on: ubuntu-latest
  permissions:
    id-token: write
    attestations: write
  steps:
    - uses: actions/download-artifact@<sha> # vX.Y.Z
      with:
        name: dist
        path: dist/
    - uses: actions/attest-build-provenance@<sha> # vX.Y.Z
      with:
        subject-path: dist/*
```

`needs: [release-please, build]` — `release-please` is required even though `build` already
depends on it transitively, because referencing `needs.release-please.outputs.*` requires a
direct dependency edge to that job, not merely a transitive one.

`actions/checkout` and `actions/setup-python` reuse the exact SHA pins already in `ci.yml` — those
are known, current values, not something to re-resolve. The three genuinely new actions this PR
introduces — `actions/upload-artifact`, `actions/download-artifact`, and
`actions/attest-build-provenance` — have their exact SHA pins resolved at implementation time
against each action's real current released tag, since this document would otherwise hardcode
version numbers that could already be stale by the time the plan is executed. The trailing
`# vX.Y.Z` comment convention and `persist-credentials: false` on checkout carry forward
unchanged either way.

**Tracked TODO, not final YAML:** every `@<sha> # vX.Y.Z` above is an unresolved placeholder —
three real SHA pins still need to be looked up before this is mergeable. `zizmor` is the backstop
that catches an unresolved or malformed pin left in by mistake (see Definition of done), but that
doesn't excuse skipping the lookup; it's a safety net, not the mechanism that fills it in.

## The gate: string comparison, not bare truthiness

Both jobs repeat `if: needs.release-please.outputs.release_created == 'true'` explicitly, rather
than gating `build` alone and letting `attest`'s `needs: build` implicitly skip-cascade. Two
reasons, one mechanical and one about how the wiring test can pin it:

- GitHub Actions treats *any* non-empty string as truthy in an `if:` condition — including the
  literal text `"false"`. A bare `if: needs.release-please.outputs.release_created` would treat
  that output as "run" the moment it's ever the string `"false"` rather than empty, which is
  exactly the kind of fail-open behavior this repo consistently avoids. The explicit `==
  'true'` string comparison is required, not stylistic. Confirmed against the real
  `release-please-action` v5 docs before writing this: with a single package configured at path
  `.` (this repo's `release-please-config.json`), the action exposes a flat `release_created`
  output with no path prefix, and `== 'true'` is the documented way to gate on it — issue #104's
  own wording already had this right.
- Repeating the gate explicitly means `tests/test_release_publish_wiring.py` can pin it with a
  direct text match on each job's own directives, rather than asserting the ABSENCE of a
  condition on `attest` and reasoning about GitHub's needs-skip-cascade semantics — a weaker,
  more indirect guarantee for a text-matching test to make.

`attest`'s condition additionally leads with `success() &&`. Checked against GitHub's own docs
before writing this: "a default status check of `success()` is applied unless you include one of
these [status] functions" — so a custom `if:` naming none of `success()`/`always()`/`cancelled()`/
`failure()` already has `success()` ANDed in implicitly, and `attest`'s condition as originally
drafted (without the explicit call) was already correctly gated on both of its `needs` — this repo's
own `ci.yml` independently confirms the same semantics (`ci-success` needs an explicit `if:
always()` specifically to *bypass* this default). The explicit `success() &&` here is therefore
belt-and-suspenders, not a correctness fix: it's added for the same reason the `== 'true'` string
comparison above is spelled out rather than left as bare truthiness — this repo's standing
preference for a gate a reader can verify by reading, over one that depends on knowing GitHub
Actions' implicit defaults correctly.

## Permissions

The workflow-wide `permissions:` block stays exactly `contents: read`, unchanged from what
`release-please.yml` already declares. Every elevation is per-job:

- `build`: `contents: read` — no elevation needed (checkout + build only), declared explicitly
  for symmetry with `attest` and so the wiring test can assert its ABSENCE of `id-token`/
  `attestations` directly rather than by omission.
- `attest`: `id-token: write` (for `attest-build-provenance`'s OIDC token) + `attestations: write`
  (to attach the attestation to the repo). Never workflow-wide — the existing
  `release-please` job's own token must never gain these.

## Testing: `tests/test_release_publish_wiring.py`

New file, in `tests/test_ci_wiring.py`'s text-matching style (no YAML parse — `pyyaml` is a
guarded optional import, per CLAUDE.md's stdlib-only rule for `sluice/`). Self-contained
`_job_directives`/`_step_containing`-equivalent helpers scoped to
`.github/workflows/release-please.yml`, not imported from `test_ci_wiring.py` — matching that
file's own file-scoped-helper convention rather than introducing cross-file coupling for two
small helpers. Both helpers carry forward the two properties `test_ci_wiring.py`'s originals earn
their length from, not just their names: **comment-stripping** (a raw substring match hits the
PROSE explaining a rule as readily as the rule itself — this exact file's docstring records that
bug having already fired once) and, for any assertion resolving a single named step (the artifact
name comparison below), an **exactly-one-match** requirement the same way `_step_containing`
enforces it, so a future step gaining its own display `name:` can't silently create a second
match this test can't disambiguate.

A top-level-permissions helper is explicitly **position-anchored** and, like the other two,
**comment-stripped** — not a bare substring search: `contents: read` appears three separate times
once `build` ships (workflow-wide at 2-space indent; once each on `release-please` and `build`,
both at 6-space indent — two depths, three occurrences), so a naive substring search can't tell
them apart. Comment-stripping matters here specifically: the live file has a comment ahead of
`jobs:` reading "needs `contents: write`..." (about the App installation's own permission scope,
unrelated to the workflow-level `permissions:` block below it) — today it says `write`, not
`read`, so it happens not to collide with an unstripped search, but that's the file's current
wording, not a property this helper should depend on.

The anchor is **not** "the first two-space key in the file" — that was tried and is wrong,
verified against the live file: `on:`'s own `push:` key sits at two-space indent too, and appears
before `permissions:` and before `jobs:` even starts, so a search from file position 0 stops
there and never reaches the block it's supposed to bound. `_job_directives` avoids this because it
always searches *forward from a known job's own start index* (past `on:`/`concurrency:`), never
from file position 0 — a helper bounding the region *above every job*, where no job name is known
in advance, can't reuse that trick directly. Anchor on the one literal, unique marker that reliably
separates workflow-level content from job content instead: `text.index("\njobs:\n")`. Everything
before that index — `name:`, `on:`, `concurrency:`, `permissions:` — is the top-level region;
everything at or after it is job content.

Assertions:

1. `release-please`'s directives contain a step whose `id:` is exactly `release` (resolved via
   the exactly-one-match step helper described above, not a bare substring search — `"id: release"
   in block` would still match a future `id: release-summary`) and a job-level `outputs:` block
   naming `release_created: ${{ steps.release.outputs.release_created }}` — the edit "Job
   definitions" above adds. Without this assertion, a future edit could silently drop the output
   the other assertions all assume exists.
2. `build`'s `needs:` value is `release-please`.
3. `build`'s directives contain the literal `if: needs.release-please.outputs.release_created ==
   'true'`.
4. `attest`'s directives contain the literal `if: success() &&
   needs.release-please.outputs.release_created == 'true'`.
5. `attest`'s `needs:` value is exactly `[release-please, build]` — an exact-list match, not
   mere containment (parallel to assertion #2's exact-value pin for `build`'s own `needs:`), so a
   stray third entry can't slip past unnoticed.
6. The top-level `permissions:` block (the position-anchored region described above) is exactly
   `contents: read` — elevated permissions never appear there.
7. `attest`'s directives contain `id-token: write` and `attestations: write`.
8. `build`'s directives contain `contents: read` and do NOT contain `id-token: write` or
   `attestations: write`.
9. `build`'s directives contain `twine check --strict`.
10. The artifact `name:` `build` uploads under and the `name:` `attest` downloads are read from
    both sides and asserted equal — not two independent hardcoded `"dist"` checks — so a rename on
    one side without the other is caught rather than silently decoupling the two jobs. Same "both
    ends read from the file" idiom `test_ci_wiring.py` already uses for the `npm ci` flags
    comparison between CI and the docs. Each side resolved via the exactly-one-match step helper
    above, not a whole-job substring search.
11. `attest`'s directives contain a `subject-path` covering `dist/*` (one glob covering both
    wheel and sdist, not two enumerated extensions that could miss a third artifact type later).
12. `release-please`'s own directives contain `contents: read` and do NOT contain `id-token:
    write` or `attestations: write` — the same shape as assertion #8 for `build`, closing the
    loop on "Permissions" above stating that job's token "must never gain" the elevated pair.
    Without this, a future edit accidentally copying `attest`'s permissions block onto
    `release-please` — the job that actually mints the App token — would go unnoticed.

Not duplicated here: SHA-pin format and the trailing-version-comment convention are already
`zizmor --offline --strict-collection`'s job, already wired into the `lint` CI job. A second,
weaker regex re-check of the same property in this new test file would be redundant coverage, not
defense-in-depth.

## Edge cases and risk

Extends the sequencing spec's existing "partially-failed release" risk note (until now scoped
only to PyPI's missing trusted publisher and Homebrew's missing tap) with a case specific to this
PR: release-please tags and cuts the GitHub Release BEFORE `build` runs. If `python -m build` or
`twine check --strict` fails, `attest` is skipped rather than running against a `dist/` that was
never uploaded — per "The gate" above, this was already correct before the explicit `success()`
was added, which only made the same behavior easier to verify by reading — and the release exists
publicly with no build artifact and no provenance attestation. Today that's the full blast
radius — PRs 3-6 don't exist yet, so nothing else is downstream of `build`'s failure. Once PR 3
lands, a `build` failure additionally means `pypi`/`docker`/`linux-packages` also don't run, which
is the shape the sequencing spec's risk note already names for the trusted-publisher/tap-not-
configured cases; this document doesn't restate that, since it isn't new here.

## Definition of done

```bash
python -m pytest tests/test_release_publish_wiring.py -v
python -m pytest                              # full suite stays green
ruff check sluice tests scripts
zizmor --offline --strict-collection .github/workflows/    # already CI's lint job; run it locally
                                                             # too since this PR edits a workflow
                                                             # file directly, including the new
                                                             # id:/outputs: edit to release-please
```

`zizmor` is named explicitly rather than left implicit: "Testing" above already leans on it to
justify *not* re-checking SHA-pin format in the new test file, so it belongs in the loop that
closes on this PR, not only in CI's own job.

## Deferred to later PRs (unchanged from the sequencing spec)

The `pypi`, `docker`, `linux-packages`, `release-assets`, and `homebrew` jobs; `docs/INSTALL.md`;
the manual prerequisites (PyPI/TestPyPI trusted publishers, the `pypi` GitHub environment, the
`homebrew-tap` repo). None of PR 2's changes require any of those to exist — `build` and `attest`
run and succeed standing alone the first time a release-please merge lands after this PR merges,
producing an attested sdist+wheel that nothing yet consumes outside this workflow run.
