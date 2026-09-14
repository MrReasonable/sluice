# Homebrew bottles — design (#279)

Status: platform set decided by the owner, 2026-09-14 (§1). Revised after the first `/review-plan`
round; every finding's disposition is in §11. Not yet re-reviewed.

Issue: **#279** (the tap publishes no bottles, so every `brew install MrReasonable/tap/job-sluice`
builds the vendored resource tree from source). The measurement this rests on is the issue's
2026-09-07 comment: the libexec venv bottles, Homebrew's own detector picks `cellar: :any`, and a
poured bottle passes `brew test`.

## A note on method

Every Homebrew behaviour this design depends on was either executed or read out of the installed
Homebrew 6.0.22 source, and is cited by file and function where it is used. Reading is weaker
evidence than running, and the first draft proved it: several mechanism claims in it were wrong
as read, and reviewers re-reading the same files corrected them. Everything not yet executed is
listed in §8. The first dry run exists to prove those items; the refuse branches of every check
are proven offline instead (§9), because a green dry run only ever takes the accept branch.

## Decisions

| Decision | Choice | Source |
| --- | --- | --- |
| Platform set | `arm64_sequoia` (`macos-15`) + `arm64_tahoe` (`macos-26`) | owner, 2026-09-14 |
| Intel and macOS 14 | not bottled | §1 |
| Runner labels | pinned; never `macos-latest` | §1 |
| Trust boundary | a job either runs third-party code or holds the tap token, never both | §2 |
| Trusted values | computed once by a `plan` job that runs no third-party code | §2 |
| Workflow shape | one reusable workflow, called by the release and the dry run | §2 |
| Resource fill | once, in the `formula` job | §2 |
| Host | GitHub Releases on the tap repository | §3 |
| Release tag | computed once per run attempt, by `plan` | §3 |
| Upload | every bottle and JSON validated as data first; identical digest skipped, different refused | §3 |
| `bottle do` writer | `brew bottle --merge --write --no-commit --no-rebuild`, in token-free jobs only | §4 |
| Pushed formula | validated as data against the renderer's skeleton plus strict stanzas | §5 |
| Proof | each tag poured on its own runner; every tag fetched from its real URL; both before the push | §6 |
| Decision logic | a pure stdlib script, `scripts/homebrew_bottles.py`, tested offline | §9 |

---

## 0. What changes for a user

Today: the 2.15.1 release job's own install reported `built in 3 minutes 12 seconds` on a GitHub
arm64 runner with every dependency already poured. Pouring the same keg as a bottle took 5s
locally on 2026-09-07.

A user whose macOS matches no bottle tag keeps today's behaviour exactly.
`formula_installer.rb::pour_bottle?` returns false when `formula.bottle_tag?` is false, and the
install builds from source. A user whose tag matches but whose download FAILS does not fall back:
the 2026-09-07 pour attempt against a wrongly named asset died on `curl: (37)`. That asymmetry
drives §3 and §6: a missing bottle degrades to today, a broken bottle breaks the install.

## 1. Platforms and runners

### Facts

- **Runner cost is zero.** Standard GitHub-hosted runners, macOS included, are free for public
  repositories, and both `sluice` and `homebrew-tap` are public.
- **Runner images today** (actions/runner-images README): arm64 `macos-15`, `macos-26`
  (`macos-latest` points here; the 2.15.1 `homebrew` job logged `Image: macos-26-arm64`),
  `macos-14` deprecated. Intel `macos-15-intel`, `macos-26-intel`. macOS 27 exists only as the
  `xcode-27` preview.
- **Homebrew support tiers:** arm64 macOS 27, 26 and 15 are Tier 1; every Intel version is Tier 3,
  and "Homebrew has stopped building new bottles for Intel systems".
- **Older bottles pour on newer macOS.** `extend/os/mac/utils/bottles.rb::find_older_compatible_tag`
  returns the first tag, in the block's order, built for an earlier macOS of the same
  architecture. `bottle_specification.rb#checksums` writes tags newest first. A newer bottle never
  pours on an older macOS.
- **Dependency bottles** (formulae.brew.sh, 2026-09-14): `pydantic` publishes no Intel macOS
  bottle at all; `pango`, `cryptography`, `pillow` and `rpds-py` publish Intel only for `sonoma`.

### The set: `arm64_sequoia` and `arm64_tahoe`

macOS 15 pours `arm64_sequoia`. macOS 26 pours `arm64_tahoe`. macOS 27 also pours `arm64_tahoe`:
it is written first and is the first tag at or below 27. `arm64_sequoia` alone would also pour on
26; building `arm64_tahoe` as well means the macOS most users run receives a bottle that was built
and poured on that macOS.

Considered and not chosen: `arm64_tahoe` alone (macOS 15 keeps building from source) and
`arm64_sequoia` alone (the pour is only ever proven on macOS 15).

### Intel and macOS 14: excluded

An Intel install compiles `pydantic-core` from Rust whatever this tap publishes, because
homebrew-core ships no Intel bottle for it, and an Intel runner would have to do the same compile
before it could build ours. Homebrew removes Intel support in or after September 2027. arm64
macOS 14 is Tier 3 and its runner image is deprecated.

### Pinned labels

With bottles, a runner's macOS decides the tag it produces. When GitHub repoints `macos-latest` at
macOS 27, an unpinned job would start publishing `arm64_golden_gate` with every step green, and
macOS 26 users would silently lose their bottle.

So the `(runner, tag)` pairs are declared once, as a constant in `scripts/homebrew_bottles.py`,
and `plan` emits them for the bottle matrix. Each bottle job checks that the tag `brew bottle`
produced equals the tag its own matrix entry declares, never a value computed on the runner,
which would compare the runner's answer to itself. A relabelled image then fails the release. The
jobs outside the matrix pin their labels literally: `macos-26` for the two that run Homebrew,
`ubuntu-latest` for the three that do not.

## 2. Trust boundary and job graph

### The threat this channel already names

`homebrew_verify.sh`'s header states it: a compromised transitive build dependency, running during
the build, must not reach the tap's write token. Today that is enforced by step order inside one
job, with the token minted after the build step and passed only to the push step.

### Step order inside one job is not a boundary

Steps in a job share one runner, one filesystem and one OS user. GitHub's documentation says a
write to `GITHUB_ENV` is visible to "all subsequent steps in a job", and a `GITHUB_PATH` entry
"automatically makes it available to all subsequent actions in the current job". A build step
that has been compromised can also leave a git hook or a `url.*.insteadOf` in `~/.gitconfig`, or
edit an action bundle the runner already downloaded for a later step. `homebrew_push.sh` then runs
`git push` with the token in its URL. The repo already states the rule in `release-please.yml`'s
`attest` job comment: "a job boundary is what makes the permissions split enforceable, since
permissions are granted per job, not per step."

Two consequences for the channel as it ships today, both closed by this design:

- the sdist builds in `homebrew_verify.sh` share a job with the push, so the split does not
  achieve its own stated threat model;
- the same build step can rewrite `Formula/job-sluice.rb` on disk before it is committed, and every
  user's `brew` evaluates that file.

### The rule

Every job is exactly one of two kinds.

- **Runs third-party code and holds no secret.** Homebrew itself, its runtime gem groups, PyPI
  resolution, sdist build backends and evaluating the formula all count.
- **Holds the tap token and runs none of that.** It checks out this repository at the release
  ref, runs only stdlib Python from `scripts/`, `gh` and `git`, and treats every artifact it
  downloads as untrusted data it validates before use.

The token jobs read job outputs only from `plan`. A step in any other job can append to
`GITHUB_OUTPUT` as easily as to `GITHUB_ENV`, so those outputs are as untrusted as the artifacts.
GitHub's secure-use reference is the ground for treating jobs as separate: "GitHub-hosted runners
execute code within ephemeral and clean isolated virtual machines, meaning there is no way to
persistently compromise this environment."

### The jobs

```
.github/workflows/homebrew.yml      on: workflow_call, and nothing else
inputs:  version, ref, push_target  each required, no default
secrets: the two App secrets        each required

plan      ubuntu-latest  | no token | no third-party code
          validate push_target (still the first thing that can fail); TAP_OWNER from
          github.repository_owner, lower-cased; DEFAULT_BRANCH and BASE_SHA from
          `git ls-remote --symref` (no checkout); TARGET_BRANCH; the sdist URL and sha256 from
          PyPI's JSON API; the release tag and root URL; the (runner, tag) pairs.

formula   macos-26       | no token | third-party code
          tap checked out at BASE_SHA; render; update-python-resources;
          brew audit --strict --online; upload the formula.

bottle    matrix (runner, tag) from plan | no token | third-party code
          build-bottle; test; bottle --json; tag check; single-tag merge; pour proof;
          test again; linkage --test; upload bottle + JSON.

upload    ubuntu-latest  | TOKEN    | no third-party code
          validate every JSON and bottle against plan; mint; create the release; upload each
          asset by the digest rule.

prove     macos-26       | no token | third-party code
          merge every JSON into the formula; brew style; tag-set check on the merged block;
          fetch every tag from its URL; upload the merged formula.

push      ubuntu-latest  | TOKEN    | no third-party code
          validate the merged formula against plan and the release's recorded digests;
          no-op, version and base checks; mint; push.
```

`needs:` edges: `formula` ← `plan`; `bottle` ← `plan`, `formula`; `upload` ← `plan`, `bottle`;
`prove` ← `plan`, `formula`, `bottle`, `upload`; `push` ← `plan`, `upload`, `prove`. No job in
`homebrew.yml` carries a job-level `if:` or `continue-on-error`: every job runs only when all it
needs succeeded.

### Callers

`release-please.yml`'s `homebrew` job becomes a call:
`uses: ./.github/workflows/homebrew.yml`, keeping `needs: [release-please, pypi]` (the formula's
`url` is the PyPI sdist) and its `if:`, passing the version, `needs.release-please.outputs.sha` and
`push_target: default`, and the two App secrets by name.

`homebrew-dry-run.yml` gets one `preflight` job. Its first step is today's branch refusal,
unchanged: a step-level `if:` and `exit 1`. Its second step is today's PyPI version lookup, which
becomes the job's `version` output. The call job `needs: preflight`, reads that output, passes
`push_target: auto`, and carries no status-function `if:`. Reading the output is what makes the
edge impossible to leave out, and a failed refusal therefore means no call. Two other shapes look
equivalent and are not. A refusal job with no `needs:` edge fails while the call still pushes. A
refusal job with the condition lifted to a job-level `if:` is skipped on the default branch, and
GitHub reports a skipped job as "Success" while everything that needs it is skipped too.

### Why a reusable workflow

The graph has a matrix and values crossing between jobs. Two hand-kept copies of it, one per
caller, would be a drift surface of exactly the kind this channel's history is made of. GitHub's
limits shape the callers: a job that `uses:` a workflow may carry only `name`, `uses`, `with`,
`secrets`, `strategy`, `needs`, `if`, `concurrency` and `permissions`; caller-level `env` does not
propagate; and "the `GITHUB_TOKEN` permissions passed from the caller workflow can be only
downgraded". The workflow triggers only on `workflow_call`, because the branch refusal lives in the
dry-run caller: a `workflow_dispatch:` on `homebrew.yml` would run the whole graph with no refusal
and a `push_target` chosen by whoever dispatched it. This is the repo's first reusable workflow.

### Why the formula is filled once

`brew update-python-resources` resolves against live PyPI through a one-day cooldown window. Two
runners resolving minutes apart can land on different resource trees, and bottles built from them
would not match one formula text. Filled once, the formula is an artifact every later job consumes.

### One derivation per fact

Every value more than one job uses is derived in `plan` and nowhere else: TAP_OWNER,
DEFAULT_BRANCH, BASE_SHA, TARGET_BRANCH, the sdist URL and digest, the tag, the root URL and the
platform pairs. The tap checkout, which `formula`, `bottle` and `prove` need, runs from one script
that takes TAP_OWNER and BASE_SHA as required inputs and derives nothing.

`homebrew_verify.sh` is retired. Its PUSH_TARGET validation and derivations move to `plan`, its
render, resource fill and audit to `formula`, and its install and test to `bottle`. Each property
the suite pins inside it moves with its code (§9).

Every workflow input, job output and matrix value reaches a script through `env:`, never as
`${{ }}` inside `run:`. `ci.yml`'s `zizmor --offline --strict-collection` step lints the new file
like every other workflow.

## 3. Hosting, tag, upload and filename

**Host: GitHub Releases on `<TAP_OWNER>/homebrew-tap`.** A release and its assets need only
`contents: write` on the tap, which the App token already carries for the push. GitHub Packages
would need a new `packages: write` grant and Homebrew's OCI upload path.

**Root URL:** `https://github.com/<TAP_OWNER>/homebrew-tap/releases/download/<tag>`, composed by
`plan` from its own TAP_OWNER, never from a literal. It is the shape
`github_releases.rb::GitHubReleases::URL_REGEX` recognises. TAP_OWNER is lower-cased, and GitHub
serves release downloads under a lower-cased owner: measured 2026-09-14 against this repo's own
v2.15.1 wheel, both casings answer `302`, and a missing asset answers `404`.

**The tag is computed once,** by `plan`, from `VERSION`, `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT`.
`brew bottle --json` bakes the root URL into each JSON, and `dev-cmd/bottle.rb`'s
`merge_json_files` deep-merges the JSONs, so two disagreeing root URLs would be resolved silently
by whichever file was read last.

**Why the tag carries the run and the attempt.** Each dry-run dispatch is its own run, so its
uploads never share a tag with a release's or with another dry run's. A dry run re-run in full is
a new attempt and gets a new tag. For the release, re-running the whole workflow never reaches
these jobs: release-please sees the release already cut and `release_created` comes back `false`
(recorded in `release-please.yml`'s `build` artifact comment). Its recovery is re-running failed
jobs, which reuses earlier jobs' outputs and artifacts, the behaviour that workflow's `pypi` and
`release-assets` recoveries already depend on.

**Validation before the token exists.** `upload` refuses to mint unless every JSON and bottle
checks out against `plan`'s outputs:

- the set of JSON files is one per declared tag, and each carries exactly one formula and exactly
  that one tag;
- `root_url` is byte-equal to `plan`'s root URL, and `rebuild` is 0;
- `cellar` is `any` or `any_skip_relocation` (the measured value is `any`); a path-valued cellar is
  refused;
- `filename` equals `job-sluice-<VERSION>.<tag>.bottle.tar.gz` and `local_filename` equals
  `job-sluice--<VERSION>.<tag>.bottle.tar.gz`, each built by the validator from trusted values and
  compared, with no path component allowed;
- the file named by `local_filename` is present, and its sha256 equals the JSON's.

Comparing Homebrew's names against an expectation is different from uploading under an assumed
name. The upload still uses Homebrew's `filename`, and a wrong expectation fails before anything
is published. The 2026-09-07 `curl: (37)` came from a name assumed and uploaded unchecked.
`upload-artifact` applies `if-no-files-found` to the aggregate of its paths (recorded in the
`linux-packages` test's docstring), which is why "JSON present, bottle absent" is refused here.

**The upload rule: skip identical, refuse different.** GitHub refuses an asset whose name already
exists on a release (HTTP 422; "must delete the old file before you can re-upload"). The releases
API reports each asset's `digest` as `sha256:<hex>` (checked against this repo's own v2.15.1
assets). So an existing asset whose digest matches is skipped, one whose digest differs is refused
naming the tag and both digests, and one with no digest is refused. The cases that matter:

- A bottle job failed; re-run failed jobs. The build re-runs and `upload` runs for the first time
  under the same tag. Nothing collides.
- `upload` or a later job failed after uploading; re-run failed jobs. The same artifacts arrive,
  the digests match, and the upload is skipped.
- A bottle already uploaded is rebuilt under the same tag by re-running a specific job that had
  succeeded. The digest differs and the upload refuses. No asset is ever replaced, so a formula
  already pushed against that asset stays valid.

**The release's title and notes are fixed templates** built only from VERSION, the tag and the
calling run's URL: no command output and no generated notes. It targets BASE_SHA.

**Why not `brew pr-upload`,** which pairs `filename` with `local_filename` itself: it is Homebrew,
it installs the `pr_upload` gem group at runtime (`Utils::GemSetup.install_bundler_gems!`), and it
evaluates the formula, all inside the job that must hold the token.

## 4. The `bottle do` block

Written by `brew bottle --merge --write --no-commit`, twice, and shipped once:

- **In each bottle job,** merging that job's one JSON into its local copy of the formula, for its
  pour proof only.
- **In `prove`,** merging every JSON into a fresh copy of the formula artifact. This text is what
  `push` validates and ships.

Both merges run in token-free jobs. `brew bottle` installs the `ast` and `bottle` gem groups and
`brew style` installs `style`; neither appears in a token job.

**`brew bottle --json` runs with `--no-rebuild`.** Without it, `dev-cmd/bottle.rb` takes the
rebuild number from the tap's `origin/HEAD` formula: when that formula already carries the same
version, the rebuild becomes its rebuild plus one. Every dry run targets the version the release
already pushed, so it would build `rebuild 1` and a `.bottle.1.tar.gz` asset, a different shape
from every release. `--no-rebuild` returns 0 unconditionally, and `upload` refuses anything else.

`scripts/render_homebrew_formula.py` does not emit the block. The renderer writes a skeleton that
Homebrew's tools complete (`update-python-resources` already edits it in place), and a Python
writer of the block's format would be a second statement of Homebrew's DSL. `push` does parse the
block, but as a checker: it accepts exactly the expected form and refuses everything else (§5).

`BOTTLE_ERB` in `dev-cmd/bottle.rb` writes `root_url` and a path-valued `cellar` into the formula
unescaped. A JSON a compromised build rewrote is therefore formula code once merged. That is why
the merges run only in token-free jobs, why `upload` validates every JSON field before the token
exists, and why `push` validates the merged text.

Because the merge edits the formula after `brew audit` ran, `brew style --formula` checks the
shipped text. RuboCop cops such as `FormulaAudit/ComponentsOrder` are what a misplaced block trips.

## 5. The pushed formula is validated as data

`formula`, `bottle` and `prove` all ran third-party code on runners that also held the formula
file. Any of them could put Ruby into the text every user's `brew` evaluates. So before `push`
mints its token, the merged formula must be exactly:

- `render(sdist_url=..., sha256=...)` from `plan`'s values, produced by this repository's
  renderer at the release ref; plus
- resource stanzas, each of exactly four lines and one blank line: a name; a
  `https://files.pythonhosted.org/packages/<2 hex>/<2 hex>/<60 hex>/<file>.tar.gz` URL whose
  project name, normalised as PEP 503 does, equals the resource name; a 64-hex `sha256`; `end`;
  plus
- one `bottle do` block whose `root_url` is `plan`'s, with one `sha256` line per declared tag and
  no others, each cellar allowed by §3, each digest equal to the one the releases API reports for
  that tag's asset on `plan`'s release tag, and no `rebuild` line.

Anything else anywhere in the text refuses the push.

**Measured feasibility, 2026-09-14.** The live 2.15.1 tap formula, with its resource stanzas
removed by that grammar, is byte-identical to `render()` for its URL and digest. Every `resource`
in it matched the grammar, and every resource name matches its sdist's project. Two controls, one
with a `system` line injected into a stanza and one with a stanza's URL moved to another host,
each fail the comparison. The block's placement is not yet measured, so the validator's bottle
grammar is fixed from the first local merge (§8).

**What it bounds, and what it cannot.** The validator guarantees the shipped formula is the
renderer's text plus PyPI sdists with digests plus a bottle block consistent with the uploaded
bytes. It cannot tell a correct resource *set* from a tampered one: the set comes from resolution
in `formula`, which runs third-party code, and a resource added there that is a real PyPI package
would pass. A change Homebrew makes to `update-python-resources`' output format fails the push
loudly, which is the intended direction.

## 6. Proof before the release is public

The publication point is the push of `Formula/job-sluice.rb` to the tap's default branch. An
uploaded asset that no formula references reaches no user.

### 6a. The pour, in each bottle job

After that job's single-tag merge:

1. **Negative control.** Run the pour check below against the `--build-bottle` keg still
   installed. It must refuse, since that keg was built.
2. `brew uninstall job-sluice`.
3. Copy the bottle to the path `brew --cache --bottle-tag=<tag> <tap>/job-sluice` prints.
4. `brew install <tap>/job-sluice`: the command a user runs, selecting the bottle through the
   block's tag and checksum.
5. **The pour check.** `brew info --json=v2 <tap>/job-sluice`, without `--installed`, must name the
   tap formula and hold exactly one installed keg, at VERSION, with `poured_from_bottle` true.
6. `brew test <tap>/job-sluice`.
7. `brew linkage --test job-sluice`, which exits non-zero on a missing library.

The check in step 5 is scoped deliberately. `cmd/info.rb`'s `--installed` arm returns every
installed formula whatever name is given, and every dependency on the runner was poured from
homebrew-core, so an unscoped check passes when `job-sluice` itself built from source. When no tag
matches, `pour_bottle?` returns false and Homebrew builds from source without complaint; the check
is what makes that loud.

**Why the seeded file is used while the asset does not exist yet**, as read from the source.
`utils/curl.rb::curl_headers` runs with `--fail`; a `404` makes it retry as GET and then raise.
`curl_download_strategy.rb::resolve_url_basename_time_file_size` rescues that and returns no
modification time, size or content type, so the freshness checks find nothing to invalidate the
cached file. It is kept, and its checksum is still verified. This is read, not run (§8).

`brew install ./<bottle>.tar.gz` is not used. `env_config.rb::forbid_packages_from_paths?` refuses
path installs by default, and a path install bypasses the formula's bottle block, so it would prove
less.

`--build-bottle` skips `post_install` (`formula_installer.rb`: `build_bottle? ||
skip_post_install?`) and a normal pour runs it. The formula has no `post_install` today; if one is
added, steps 4 to 7 exercise it.

### 6b. Every URL, in `prove`

After the merge and `brew style`, and after `upload` has published the assets:

1. **Tag-set check** on the merged block: its tags equal `plan`'s declared set exactly. The declared
   set comes from `plan`, never from the block being checked.
2. For each declared tag: remove any cached copy; `brew fetch --force --bottle-tag=<tag>
   <tap>/job-sluice`; then assert the file at `brew --cache --bottle-tag=<tag>` exists and its
   sha256 equals the block's.

The file assertion is required. `cmd/fetch.rb` answers a tag absent from the block with `opoo
"Bottle for tag ... is unavailable."` and moves on, and `opoo` never marks the command failed, so
the exit status alone proves nothing for a missing tag. For a tag that is present, a wrong asset
name, a wrong release, a disagreeing root URL and a byte mismatch all fail here.

`prove` evaluates the merged formula, which is why it holds no token. A formula tampered with to
fake this job's success still meets `push`'s validation: `prove` answers whether Homebrew builds URLs
that serve these bytes, and `push` answers whether the text is the expected text.

### 6c. The push

In `push`, in order:

1. Validate the merged formula (§5).
2. Fetch the tap's TARGET_BRANCH. If its formula is already byte-identical to ours, finish with
   nothing to do; that is a re-run after this release's push already landed.
3. For the default branch, refuse if the tap's current formula declares a version newer than
   VERSION.
4. `checkout -B <TARGET_BRANCH> <BASE_SHA>`, write the formula, commit.
5. Mint the token and push: fast-forward only for the default branch, `--force-with-lease` for a
   scratch branch, as today.

Building on BASE_SHA is what stops a re-run from rolling the tap back. Re-running an older release's
failed jobs reuses that release's `plan` outputs, so if a newer release landed in between, the push
is a non-fast-forward and is rejected. Step 3 covers a re-run that re-executes `plan` and so reads
the moved tip.

### Where the token is

| Job | Third-party code | Token |
| --- | --- | --- |
| plan | no | no |
| formula | yes | no |
| bottle | yes | no |
| upload | no | yes |
| prove | yes | no |
| push | no | yes |

The dry run runs the same jobs. What differs stays confined to `push_target`, as today.

## 7. Failure modes, from the user's side

| Failure | Caught at | What a user sees |
| --- | --- | --- |
| an invalid `push_target`, or an unresolvable tap | `plan` | nothing |
| resources, audit, build, or the built keg's `brew test` fails | `formula` or `bottle` | nothing |
| a runner produced the wrong tag | `bottle`'s tag check | nothing |
| a bottle does not pour, fails `brew test`, or has broken linkage | 6a | nothing |
| a JSON or bottle is missing, altered or inconsistent | `upload`'s validation | nothing |
| a rebuilt bottle meets an already-uploaded asset | the digest refusal | nothing; recovery is a new release |
| the merged block lost or gained a tag | 6b's tag-set check | nothing; orphan assets |
| a URL, name, release or byte mismatch | 6b | nothing; orphan assets |
| the formula text was altered anywhere | `push`'s validation | nothing; orphan assets |
| the tap moved since `plan`, or an older release re-runs | 6c | nothing; a rejected push |
| homebrew-core later changes a library the keg links by path | **nothing catches it** | a failing pour on that tag until the next release; `brew install --build-from-source` works around it |

The last row is the one risk bottling adds. A source build links against whatever homebrew-core
ships on the day of the install; a bottle's links are fixed on release day. `brew linkage --test`
proves the links hold at release time, and every release re-bottles against current homebrew-core.

macOS 27 pours `arm64_tahoe` (§1), and no generally available runner can prove that pour. That is a
residual, stated here.

## 8. Not yet executed

The first dry run proves these before a release depends on them:

- the reusable workflow called with explicit secrets, and the App token minted inside it;
- the matrix generated from `plan`'s output;
- `brew install` using the seeded cache file while the asset is still missing (§6a);
- `brew bottle --json --no-rebuild` naming and `--merge --write --no-commit` with two JSONs, and
  where the block lands relative to the renderer's skeleton; the §5 bottle grammar is fixed from
  this, which the plan measures locally before writing the validator;
- `brew fetch --force --bottle-tag` for a tag other than the runner's own, from a public release
  asset, and once for a tag deliberately absent from the block, recording the exit status;
- the digest comparison against a real asset;
- the whole chain on `macos-15`; the 2026-09-07 measurement ran on a local macOS 26 machine;
- `brew linkage --test` on both runners;
- the wall-clock time the release gains.

Measured already, and not repeated here: `git ls-remote --symref` resolves the tap's default
branch and its SHA with no checkout, and the §5 grammar against the live formula.

## 9. Tests

### 9a. Decision logic, offline

`scripts/homebrew_bottles.py` (stdlib only, beside `render_homebrew_formula.py`) holds every
decision as a function over parsed data, with a thin CLI the workflow's steps call.
`tests/test_homebrew_bottles.py` drives the functions with synthetic fixtures: root URLs under
`example.invalid`, fake digests, no real owner. Each refuse row below is witnessed by the mutant it
exists to kill.

- **Pour check:** refuses `job-sluice` not poured while its dependencies are; no `job-sluice`
  entry; an empty `installed` list; a keg at another version.
- **Produced tag:** refuses a mismatch; zero tags; two tags; an empty declared tag.
- **Bottle JSON:** refuses a wrong root URL, a non-zero rebuild, a disallowed cellar, either name
  wrong, a path component in either name, a digest mismatch, and a JSON whose bottle is absent;
  refuses a declared tag missing, an extra tag, and an empty declared set.
- **Digest rule:** no asset uploads; a matching `sha256:` digest skips; a different digest refuses;
  a missing digest refuses.
- **Merged tag set:** refuses a missing tag and an extra tag, taking the declared set as an argument.
- **Formula validator:** passes the renderer's text plus stanzas plus a correct block; refuses an
  injected statement anywhere, a foreign resource host, a resource name that is not its sdist's
  project, a wrong root URL, a missing or extra tag, a digest different from the release's, and a
  `rebuild` line.
- **Version check:** refuses a default-branch push when the tap declares a newer version.
- **Platform pairs:** equal a set restated by hand in the test,
  `{("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe")}`, never imported.

### 9b. Workflow wiring

`tests/test_release_publish_wiring.py` reads `release-please.yml`'s `homebrew` job and
`homebrew-dry-run.yml`'s `dry-run` job today; its pins move to `homebrew.yml` and gain these
properties, each witnessed by its mutant:

- `homebrew.yml` triggers only on `workflow_call`; its inputs are required with no default; its
  secrets are exactly the two App secrets, each required.
- Both callers use `./.github/workflows/homebrew.yml` exactly, pass the secrets by name, never
  `secrets: inherit`, and pass `push_target` `default` and `auto` respectively.
- An exact job roster, an exact `permissions` block per job, and a read-only workflow-wide block.
- Exact `needs:` per job, and no job-level `if:` or `continue-on-error` anywhere in the file.
- The App secrets and the token are referenced only in `upload` and `push`, and those two jobs
  invoke no `brew` and run nothing but `scripts/` Python, `gh` and `git`. They read no job output
  other than `plan`'s.
- Every `needs.<job>.outputs.<key>` names a declared output, and every declared output names a step
  that exists.
- The tag, root URL, `update-python-resources`, the renderer call, `GITHUB_RUN_ATTEMPT` and
  `releases/download` each appear in exactly one job or a script only that job invokes.
- The bottle job's sequence, matched as whole lines, counting occurrences, over comment-stripped
  text: `install --build-bottle` < first `test` < `bottle --json --no-rebuild` < `bottle --merge` <
  negative control < `uninstall` < cache seed < `install` < pour check < second `test` <
  `linkage --test`. An index of the first occurrence cannot see the second `brew test`.
- In-job order in `upload` (validate < mint < upload), `prove` (merge < style < tag-set check <
  fetch loop < cache-file check) and `push` (validate < no-op, version and base checks < mint <
  push).
- The dry run's `preflight` job keeps the refusal step before the lookup, has no job-level `if:`,
  and the call job needs it with no status-function `if:`.
- The bash 3.2 sweep's roster is derived from both ends (every script a `homebrew.yml` job invokes
  is in it, and every entry is invoked) and also covers `homebrew.yml`'s own `run:` bodies.
- No `${{ }}` inside any `run:` in `homebrew.yml`.
- The owner-derivation pin moves to `plan`, and no homebrew workflow or script carries an owner
  literal.
- The release's title and notes are the fixed templates.

The plan's review includes `sluice-architect`: this is the repo's first reusable workflow, and it
turns step boundaries into job boundaries.

## 10. Prose that changes

Claims that become false, each owned by a task in the plan:

- `scripts/render_homebrew_formula.py`: the `_EXTRA_PACKAGES` rationale ("this tap publishes NO
  BOTTLES, so every `brew install` builds from source") and the `_PYTHON_FORMULA` comment naming
  `--build-from-source` in `homebrew_verify.sh`. The rule survives on narrower grounds: users whose
  macOS matches no bottle build from source, and the exclusion also carries the prefix-path seam
  `cellar :any` rests on.
- `tests/test_homebrew_formula.py`: the same premise.
- `homebrew_push.sh`: the header's "same job, on the same runner", and the byte-identical no-op
  comment, whose named case (a second dry-run dispatch) can no longer occur; the reachable case is
  a re-run after this release's push landed. Its logic moves to `push` with the checks in §6c.
- `homebrew_verify.sh`: retired; its reasoning moves with its code.
- `release-please.yml`'s `homebrew` job: the recovery, no-token, `--build-from-source` and macOS
  comments.
- `homebrew-dry-run.yml`: the header's "Proves the WHOLE chain" list and the PUSH_TARGET prose.
- Test docstrings naming `--build-from-source` or `macos-latest`.
- `docs/INSTALL.md`'s Homebrew section: which Macs pour a bottle and which still build from source.

## 11. Review round 1: dispositions

All findings were accepted. The one flagged for owner judgement (arch-004, whether gem-running
merge steps may share the token job) is decided by `release-please.yml`'s own rule that only a job
boundary enforces a split, and is resolved more strictly than proposed: no token job runs Homebrew.

| Findings | Disposition |
| --- | --- |
| inv-002, arch-004 | §2 trust rule and job graph; §3 validation; §5 formula validator |
| inv-001, test-001 | §6a scoped pour check and negative control; §9a rows |
| test-002, inv-004 | §6b tag-set check on the merged block and cache-file assertion |
| test-003, arch-001, arch-002 | §2 callers and `preflight`; §9b job-level pins |
| rev-001, inv-003 | §4 `--no-rebuild`; §3 refuses rebuild |
| inv-005 | §6c base SHA and version refusal |
| rev-002 | §6a mechanism restated |
| rev-003, arch-005, test-008 | §10 |
| rev-004, arch-003 | §2 one derivation per fact; `homebrew_verify.sh` retired |
| rev-005 | §1 and §7: macOS 27 pours `arm64_tahoe` |
| rev-006 | §2 `env:` only and zizmor; §9b |
| test-004 | §9b output agreement, callers, secrets |
| test-005 | §1 pairs declared once; §9a restated set |
| test-006 | §9b occurrence-aware sequence pin |
| test-007 | §9b roster and bash 3.2 sweep |
| inv-006, arch-006 | §6a `brew linkage --test` |
| neu-001 | §3 root URL from TAP_OWNER; §9b owner pin |
| neu-002 | §3 fixed release templates |

## Out of scope

- Intel and macOS 14 bottles (§1).
- A post-release Homebrew install check in `post-release.yml`: §6b fetches the exact URLs and bytes
  users receive before the formula that points at them is pushed.
- `brew test-bot` or any workflow inside the tap: `homebrew_verify.sh`'s header records why the tap
  carries none (a second automated writer of a machine-owned formula, and an App token that cannot
  push `.github/workflows/`).
- Deleting the orphan releases a dry run or a failed publish leaves behind. Nothing reads the tap's
  Releases page.
- Proving the resource set itself is the correct closure (§5).
