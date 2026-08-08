# Packaging distribution (#104) ships as 7 sequenced PRs, not one

Status: design, approved 2026-08-09.

This spec does not restate issue #104's mechanism-level detail (the exact workflow YAML,
`Dockerfile` contents, `nfpm.yaml` shape, formula stanzas) — that's already locked there from
the PR #103 planning pass and stays the source of truth for *what* each channel does. This
spec answers the two things #104 leaves open: **what order the work lands in**, and **two
scope boundaries #104 doesn't draw** (whether this is one PR or several, and whether the
`claude-cli` Docker Compose service belongs in this issue at all).

## Why decompose

#104 touches four independent distribution channels, a CI workflow with per-job OIDC
permissions, a brand-new public repo (`homebrew-tap`), and repo-owner-only manual setup
(trusted publishers, a GitHub environment, GHCR visibility). The project's standing rule is
fold work into the current PR when it's self-contained and file a follow-up only for
genuinely design-laden work. Four channels that don't depend on each other, landing behind a
release gate that has never fired in this repo, is the opposite of self-contained: a defect in
one channel's job (say, a malformed `nfpm.yaml`) has no reason to block the other three from
being reviewable and mergeable on their own schedule. Sequenced PRs also mean each one gets
its own `/review-pr` pass rather than one review trying to hold seven different mechanisms in
its head at once.

## Sequencing

| # | PR | Scope (see #104 for mechanism) | Depends on |
|---|---|---|---|
| 1 | `pyproject.toml` metadata | `license`, `readme`, `authors`, `classifiers`, `keywords`, full URL set; extends `tests/test_packaging.py` in its existing assert-then-falsify idiom | — |
| 2 | Publish workflow skeleton | `build` + `attest` jobs only (sdist+wheel, `twine check --strict`, provenance attestation), gated on release-please's `release_created` output; new `tests/test_release_publish_wiring.py` in `test_ci_wiring.py`'s text-matching style, pinning the gate and per-job (not workflow-wide) permissions | 1 |
| 3 | PyPI channel | `pypi` job (Trusted Publishing, environment `pypi`), `release-assets` job (wheel+sdist), a `workflow_dispatch` TestPyPI variant | 2 |
| 4 | Docker channel | `Dockerfile`, `.dockerignore`, `docker` job — installs the **build job's wheel**, never `pip install job-sluice` from PyPI (that races the `pypi` job) | 2 |
| 5 | deb/rpm channel | `nfpm.yaml`, `linux-packages` job, release-asset uploads of `.deb`/`.rpm` | 2 |
| 6 | Homebrew channel | New `MrReasonable/homebrew-tap` repo, `Formula/job-sluice.rb`, a bump job that regenerates via `brew update-python-resources` (never hand-edited, or the resource tree silently goes stale) | 4 |
| 7 | Install docs | `docs/INSTALL.md` + README install section | 3, 4, 5, 6 |

PyPI is first among the channels because it's the one mechanism #104 itself calls genuinely
unproven — Trusted Publishing has never run in this repo — so it gets proven earliest, via a
TestPyPI dry run, rather than discovered broken at the first real release alongside three
other new channels. deb/rpm depends only on the `build` job's wheel (PR 2) — the same artifact
Docker consumes, not Docker's `Dockerfile` itself — so it can branch and land in parallel with
the Docker channel; the table's "Depends on: 2" is correct as written, and nothing orders it
after PR 4. Homebrew is the one channel that genuinely depends on Docker (dependency "4" in the
table): its bump job reuses the exact "install the built artifact, not the published one"
pattern PR 4 establishes once, rather than reinventing it. Homebrew also lands last for an
independent reason: #104 calls it the highest-maintenance channel (hand outside code owns
cairo/pango/gdk-pixbuf plus WeasyPrint's whole resource tree), and it's the one channel with the
least reason to be time-critical — pipx/uv already give every platform a one-command install.

PR 1's `authors` field ships in the public sdist/wheel metadata, so it is pinned to the same
noreply identity this repo already uses in every commit trailer (`MrReasonable
<4990954+MrReasonable@users.noreply.github.com>`), never a personal email address; PR 1's
`tests/test_packaging.py` extension asserts the built wheel's `METADATA` carries that identity
and no other email.

Install docs land **last**, once every channel is real, matching #104's own principle: "written
alongside the mechanism that makes them true." Documenting a channel before it has shipped a
real release through it would be asserting something unverified.

## Scope boundary: `claude-cli` Docker Compose service is NOT in #104

#104's own text flags this service — a second container, `sshd`, a credential volume, and a
new interactive-login flow to let `claude-max` authenticate inside Docker — as unproven,
saying in terms that it "must be built and logged into for real before being documented as
supported." That's a design-laden second thing bolted onto a packaging issue, not a
detail of the Docker channel. It gets its own follow-up issue, filed once PR 4 (Docker) ships,
scoped from a real build-and-login attempt rather than from the paragraph in #104 — the same
"review, don't assume" posture #28's own arc took toward claude-max's agentic behavior.

## Gap found in #104's manual-prerequisite list: TestPyPI needs its own trusted publisher

#104 lists a real-`pypi.org` trusted publisher and a `pypi` GitHub environment as manual
prerequisites, and separately lists a TestPyPI `workflow_dispatch` dry run under
*Verification* — but TestPyPI is a distinct service from PyPI with its own project registry,
its own pending-publisher configuration, and its own OIDC audience (`testpypi`, not `pypi`).
Confirmed against PyPI's own trusted-publisher docs before writing this down, not assumed.
Without a **separate** entry at `test.pypi.org/manage/account/publishing/`, the TestPyPI
dispatch job in PR 3 cannot authenticate at all, and the dry run #104 relies on to prove the
OIDC handshake before the first real release would fail for a reason that has nothing to do
with whether the handshake actually works.

## Manual-prerequisite timing

Every manual step is repo-owner-only (GitHub UI actions this agent cannot take), so each is
sequenced right after the PR that needs it merges, and before the next event that would
exercise it:

- **After PR 3 merges**, before the next release-please merge: add the pending trusted
  publisher on `pypi.org` (owner `MrReasonable`, repo `sluice`, workflow
  `release-please.yml`, environment `pypi`), create the `pypi` GitHub environment, **and**
  add the separate pending trusted publisher on `test.pypi.org` (the gap above). Then
  manually trigger the `workflow_dispatch` TestPyPI dry run to prove the OIDC handshake for
  real before it's load-bearing for an actual release. **Do not approve the next
  release-please PR until this is done** — release-please merges are routine and
  semi-automatic, and a merge landing first runs the `pypi` job straight into a missing
  trusted publisher while `build`, `attest`, `docker` and `linux-packages` in that same
  release still succeed, producing a publicly visible partially-failed release instead of a
  clean first publish.
- **After PR 4 merges**, once the first image has pushed: set the GHCR package visibility to
  public.
- **After PR 6 merges**: create `MrReasonable/homebrew-tap` (public, empty) and install the
  release-please GitHub App on it with `contents: write`, mirroring how the App is already
  installed on `sluice` itself.

None of these block a PR's own CI — the jobs they gate only run on a release-please merge — so
a delay here delays the first real publish event on that channel, not code review.

## Testing approach

Each channel PR extends the two existing packaging-guard idioms rather than inventing a third:
`tests/test_packaging.py`'s assert-then-falsify style (build the artifact, assert the
property, then rebuild with the property stripped and assert the guard catches it) for
anything that inspects a built wheel/deb/rpm/formula, and `tests/test_ci_wiring.py`'s
text-matching style (no YAML parse, since `pyyaml` is a guarded optional import) for anything
that inspects the workflow file itself — gating conditions, per-job permissions, which install
command in `docs/INSTALL.md` names a channel the workflow actually produces.

The Docker channel's one hard invariant — the `docker` job installs the `build` job's wheel,
never `pip install job-sluice` from PyPI — falls between those two idioms (not a built artifact
the assert-then-falsify style inspects, not the workflow file the text-matching style
inspects), so PR 4 names its own check rather than leaving the gap implicit: a text-match
assertion against the `Dockerfile`'s own source, asserting it does not contain the literal
string `pip install job-sluice` and does reference the wheel path the `build` job produces. No
real `docker build` runs inside the offline pytest suite — pulling a base image needs network,
which this suite deliberately does not have — so this stays a text check on the `Dockerfile`
source, the same shape `test_ci_wiring.py` already uses for the workflow YAML, not an executed
build.

PR 6's Homebrew formula lives in a separate repository (`homebrew-tap`), which could have left
its correctness outside both idioms entirely. It doesn't: the formula is generated from a
template committed in `sluice` itself and rendered via `brew update-python-resources` during
the bump job, so its content is asserted with the same assert-then-falsify idiom PR 1 already
uses for wheel metadata — assert the rendered formula names the expected `depends_on` set and
resource versions, then strip one and assert the guard catches it — before the bump job ever
pushes the rendered file to `homebrew-tap` over the App token. `homebrew-tap` carries no test
suite of its own; it only ever receives an artifact already verified in `sluice`.

## Risks

First three carried from #104 unchanged; the fourth is specific to this sequencing:

- The `pypi` Trusted Publishing job is the one mechanism proven only by the TestPyPI dry run,
  not by anything CI can assert statically.
- Homebrew's resource list silently drifts if a future bump is hand-edited instead of
  regenerated.
- The `claude-cli` service, now explicitly out of #104's scope, remains unbuilt and
  undocumented until its own follow-up issue lands.
- A release-please merge landing before the PyPI/TestPyPI trusted publishers are configured
  produces a partially-failed release — every job but `pypi` succeeds, and the failure is
  publicly visible on a release the maintainer approved without realizing the manual step was
  still outstanding. Mitigated only by the explicit hold instruction in Manual-prerequisite
  timing above; nothing in CI enforces it.
