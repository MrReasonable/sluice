# Homebrew bottles — design (#279)

Status: owner decisions 2026-09-14: the platform set (§1), and one PR carrying both the bottles and
the rebuild of the channel's trust boundary. Revised after two `/review-plan` rounds; every
finding's disposition is in §11 and §12. The next review targets the implementation plan.

Issue: **#279** (the tap publishes no bottles, so every `brew install MrReasonable/tap/job-sluice`
builds the vendored resource tree from source). The measurement this rests on is the issue's
2026-09-07 comment: the libexec venv bottles, Homebrew's own detector picks `cellar: :any`, and a
poured bottle passes `brew test`.

## A note on method

Every Homebrew and GitHub behaviour this design depends on was either executed or read from the
installed Homebrew 6.0.22 source or GitHub's documentation, and is cited where it is used. Reading
is weaker evidence than running, and the first draft proved it: several mechanism claims in it were
wrong as read, and reviewers re-reading the same files corrected them. Everything not yet executed
is listed in §8, which also names the decisions no dry run can reach. Those unreachable decisions
are proven offline (§9), because a green dry run only ever takes the accept branch of what it
reaches.

## Decisions

| Decision | Choice | Source |
| --- | --- | --- |
| Platform set | `arm64_sequoia` (`macos-15`) + `arm64_tahoe` (`macos-26`) | owner, 2026-09-14 |
| Scope | one PR: bottles and the channel's trust boundary together | owner, 2026-09-14 |
| Intel and macOS 14 | not bottled | §1 |
| Runner labels | tag-producing jobs pinned; never `macos-latest` | §1 |
| Trust rule | a job whose secrets or outputs a token job consumes runs no third-party code; a job that runs third-party code references no secret | §2 |
| Trusted values | derived once, by `plan` | §2 |
| Workflow shape | one reusable workflow, called by the release and the dry run | §2 |
| Resource fill | once, in `formula` | §2 |
| Host | GitHub Releases on the tap repository | §3 |
| Release tag | computed once per run attempt, by `plan` | §3 |
| Release lifecycle | looked up among all releases; created as a draft only when absent; accepted only when it matches; published after its assets | §3 |
| Upload | every bottle and JSON validated as data first; identical digest skipped, different refused | §3 |
| `bottle do` writer | `brew bottle --json --no-rebuild`, then `brew bottle --merge --write --no-commit`, in token-free jobs only | §4 |
| Pushed formula | validated as data against the renderer's text plus strict stanzas | §5 |
| Proof | each tag poured on its own runner; every tag fetched from its real URL; both before the push | §6 |
| Decision logic | pure functions in `scripts/homebrew_bottles.py`, tested offline | §9 |

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

So the `(runner, tag)` pairs are declared once, as a constant in `scripts/homebrew_bottles.py`.
`plan` emits them as JSON; the bottle matrix is generated from that JSON, with `runs-on` taken from
the entry's runner key and the expected tag from its tag key. Each bottle job checks that the tag
`brew bottle` produced equals its own matrix entry's tag, never a value computed on the runner,
which would compare the runner's answer to itself. A relabelled image then fails the release.

`formula` and `prove` also run Homebrew and pin `macos-26`. `plan`, `upload` and `push` run on
`ubuntu-latest`: they produce no bottle tag and run only stdlib Python, which runs `git` itself, so a moving
image changes nothing this design depends on, and every other job in this repository's workflows
already runs there.

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

GitHub's secure-use reference is the ground for treating jobs as separate: "GitHub-hosted runners
execute code within ephemeral and clean isolated virtual machines, meaning there is no way to
persistently compromise this environment." Within that:

- **A job whose secrets or outputs a token job consumes runs no third-party code.** These are the
  trusted jobs: `plan`, `upload` and `push` in `homebrew.yml`, and the dry run's `preflight`. The
  release's `release-please` job is trusted by exception: its `sha` output is the `ref` the token jobs
  check out, and it runs the SHA-pinned `googleapis/release-please-action`, which already writes to this
  repository with its own App token, so its steps are pinned whole rather than rostered. Of
  them, only `upload` and `push` hold the tap token. Homebrew itself, its runtime gem groups, PyPI
  resolution, sdist build backends, evaluating the formula, and any action outside the roster below
  all count as third-party code.
- **A job that runs third-party code references no secret.** `formula`, `bottle` and `prove`. They still
  hold a `contents: read` `GITHUB_TOKEN` and the run's artifact token, which can create an artifact
  under any name. Nothing they
  produce reaches a token job except as an artifact that job validates as data, and token jobs read
  no output of theirs: a step can append to `GITHUB_OUTPUT` as easily as to `GITHUB_ENV`.

**What a trusted job may run.** In `homebrew.yml`, every `run:` line is `python3 -P` on a script under
this repository's `scripts/` by absolute path, and that script runs `git` itself. The dry run's
`preflight` has no checkout and so no such script: its two `run:` bodies, the branch refusal and a
standard-library PyPI lookup, are pinned whole instead. A trusted job's `uses:` steps come from an exact
roster at pinned SHAs: `actions/checkout` of this repository at the release ref (all three
`homebrew.yml` trusted jobs), `actions/download-artifact` (`upload`, `push`), and
`actions/create-github-app-token` (`upload`, `push` only). `actions/cache` or a `setup-*` action with
caching would restore an entry a third-party job can write, in this run or in an earlier run on the same branch (GitHub scopes caches by branch, not by workflow), which is why the roster
is exact, and why no workflow in the repository restores a cache. Several actions cache unless a use sets
their caching inputs to false: the release's docker job ran qemu and buildx setup actions that did, until
the post-merge review of #339 found them. `python3 -P` removes the script's own directory from
`sys.path` (measured with the working directory and the script's directory apart and `PYTHONPATH` unset:
without `-P` the script's directory is listed, with it neither is, and `sys.flags.safe_path` is true).
The helper loads the renderer from its file, so it adds no entry of its own.

**Artifacts in a token job.** Every artifact downloads into a fresh directory under `$RUNNER_TEMP`,
outside `$GITHUB_WORKSPACE` and every git work tree, before any clone. Nothing from that directory is
executed, imported, sourced or used as a working directory. `push` writes the single validated
formula into a fresh tap clone by content, never by copying a tree. Every `git` invocation in a
token job carries `-c core.hooksPath=/dev/null`. An artifact can otherwise carry a `.git/hooks/pre-push`
that receives the token-bearing push URL, or a script that shadows the validator.

### The jobs

```text
.github/workflows/homebrew.yml      on: workflow_call, and nothing else
inputs:  version, ref, push_target  each required, no default
secrets: the two App secrets        each required

plan      ubuntu-latest  | trusted  | no token
          validate push_target, then VERSION (the first things that can fail); TAP_OWNER from
          github.repository_owner, lower-cased; DEFAULT_BRANCH and BASE_SHA from
          `git ls-remote --symref`; for auto, the formula's presence at BASE_SHA; TARGET_BRANCH;
          the sdist URL and sha256 from PyPI's JSON API, validated; the release tag and root URL;
          the (runner, tag) pairs.

formula   macos-26       | third-party code | no secret
          tap at BASE_SHA; render; update-python-resources; brew audit --strict --online;
          upload the formula.

bottle    matrix from plan's pairs | third-party code | no secret
          build-bottle; test; bottle --json --no-rebuild; tag check; single-tag merge; pour proof;
          test again; linkage --test; upload bottle + JSON.

upload    ubuntu-latest  | trusted  | TOKEN
          validate every JSON and bottle against plan; mint; find or create the draft release;
          upload each asset by the digest rule; publish.

prove     macos-26       | third-party code | no secret
          merge every JSON into the formula; brew style; tag-set check on the merged block;
          fetch every tag from its URL; upload the merged formula.

push      ubuntu-latest  | trusted  | TOKEN
          validate the merged formula against plan and the release's recorded digests; clone the
          tap; target presence, no-op and version checks; commit on BASE_SHA; mint; push.
```

`needs:` edges: `formula` ← `plan`; `bottle` ← `plan`, `formula`; `upload` ← `plan`, `bottle`;
`prove` ← `plan`, `formula`, `bottle`, `upload`; `push` ← `plan`, `upload`, `prove`. No job and no
step in `homebrew.yml` carries `if:` or `continue-on-error`: every job runs only when all it needs
succeeded, and every step runs only when the steps before it did.

### `plan`

**Inputs are validated before anything is emitted.** `push_target` is `default` or `auto`, refused
otherwise with both valid values named. `VERSION` matches `^\d+\.\d+\.\d+$`. From PyPI's JSON API,
the sdist URL must match §5's `files.pythonhosted.org` grammar with the file
`job_sluice-<VERSION>.tar.gz`, and its sha256 must be 64 hex characters. The renderer puts the URL
into a Ruby string unescaped, so an unchecked value would pass straight through §5's comparison.

**The bootstrap observable for `auto`.** `auto` pushes the default branch only when
`Formula/job-sluice.rb` does not exist yet. `git ls-remote` lists refs and cannot see a file. `plan`
reads it from the contents API through `urllib`,
`GET /repos/<TAP_OWNER>/homebrew-tap/contents/Formula/job-sluice.rb?ref=<BASE_SHA>`,
authenticated with the workflow's own `GITHUB_TOKEN`, at BASE_SHA so the observable and the base
commit cannot disagree. The answer has three values: `200` is present, so TARGET_BRANCH is
`bump-<VERSION>`; `404` is absent, so it is DEFAULT_BRANCH; anything else refuses. A rate-limit `403`
read as "absent" would push a dry run to the default branch, the harm `push_target` exists to prevent.
Measured 2026-09-14 on the live tap: `200` for the formula at its tip, `404` for a path that does not
exist. `default` never consults the observable.

### Callers

`release-please.yml`'s `homebrew` job becomes a call: `uses: $/.github/workflows/homebrew.yml`,
keeping `needs: [release-please, pypi]` (the formula's `url` is the PyPI sdist) and its `if:`,
passing `version` and `ref` from `release-please`'s outputs, `push_target: default`, and the two App
secrets by name.

`homebrew-dry-run.yml` gets one `preflight` job, a trusted job with no `uses:` steps. Its first step is
today's branch refusal, unchanged: a step-level `if:` and `exit 1`. Its second step is today's PyPI
version lookup, which becomes the job's `version` output. The call job `needs: preflight` and
passes that `version`, `ref: ${{ github.sha }}` (the refusal has already confined it to the default
branch), `push_target: auto`, and the secrets by name. It carries no status-function `if:`. Two other
shapes look equivalent and are not. A refusal job with no `needs:` edge fails while the call still
pushes. A refusal job with the condition lifted to a job-level `if:` is skipped on the default
branch, and GitHub reports a skipped job as "Success" while everything that needs it is skipped too.

The two callers run the same jobs. Their inputs differ in `push_target`, in where `version` comes
from, and in `ref`: the release passes its tagged commit, the dry run the dispatched default-branch
commit.

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

Each value is derived in one place and read everywhere else:

- TAP_OWNER, DEFAULT_BRANCH, BASE_SHA, TARGET_BRANCH, the sdist URL and digest, the tag, the root URL
  and the platform pairs: in `plan` only. `push` reads them from `plan`'s outputs, and runs no
  `ls-remote --symref`, `remote set-head` or `symbolic-ref`.
- The run attempt (`GITHUB_RUN_ATTEMPT` or `github.run_attempt`): read in `plan` only.
- `update-python-resources` and the render that WRITES the formula: in `formula` only.
- `render()` has exactly one other caller, deliberately: `push`'s validator re-renders at the release
  ref to build the text it compares against.
- The token mints keep `owner: ${{ github.repository_owner }}`, the expression `plan` derives
  TAP_OWNER from, and a pin holds the two to that one expression.

The tap checkout that `formula`, `bottle` and `prove` need runs from one script, which clones into
Homebrew's `Taps` directory at BASE_SHA and derives nothing. `push` never runs Homebrew, so it clones
the tap with plain `git` into `$RUNNER_TEMP`.

`homebrew_verify.sh` is retired: its PUSH_TARGET validation and derivations move to `plan`, its
render, resource fill, audit and cooldown diagnostic to `formula`, and its install and test to
`bottle`. The cooldown diagnostic reuses `plan`'s sdist URL rather than querying PyPI again, and its
recovery text names re-running the caller's failed jobs. §9b maps every existing test to its
successor.

Every workflow input, job output and matrix value reaches a script through `env:`, never as
`${{ }}` inside `run:`. `ci.yml`'s `zizmor --offline --strict-collection` step lints the new file
like every other workflow.

## 3. Hosting, tag, upload and filename

**Host: GitHub Releases on `<TAP_OWNER>/homebrew-tap`.** A release and its assets need only
`contents: write` on the tap, which the App token already carries for the push. GitHub Packages
would need a new `packages: write` grant and Homebrew's OCI upload path.

**Root URL:** `https://github.com/<TAP_OWNER>/homebrew-tap/releases/download/<tag>`, composed by
`plan` from its own TAP_OWNER. It is the shape `github_releases.rb::GitHubReleases::URL_REGEX`
recognises. TAP_OWNER is lower-cased, and GitHub serves release downloads under a lower-cased owner:
measured 2026-09-14 against this repo's own v2.15.1 wheel, both casings answer `302`, and a missing
asset answers `404`.

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
`release-assets` recoveries already depend on. For a digest clash, or a push rejected because the
tap's default branch moved after `plan`, that re-run fails the same way every time; re-running the
`homebrew / plan` job may recover both, since `plan` would plan again under a new run attempt and so
give a new tag, release and BASE_SHA, but that is unverified (§8), and the next release is the
fallback. That re-running failed jobs reuses outputs inside a called workflow is read, not measured
(§8).

**Validation before the token exists.** `upload` refuses to mint unless every JSON and bottle
checks out against `plan`'s outputs:

- the set of JSON files is one per declared tag, and each carries exactly one formula and exactly
  that one tag, refused unless declared before any name is built from it or any file is read: the
  tag is the JSON's own key, so a name composed from it and compared with the same JSON's
  `local_filename` would have the JSON on both sides of the comparison;
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

**The release lifecycle.** GitHub's "get a release by tag name" returns "a published release", and
"only users with push access will receive listings for draft releases". So `upload`, holding the App
token, lists the tap's releases (paginated) and matches `tag_name`:

1. **Absent:** create a draft targeting BASE_SHA, with the fixed title and notes.
2. **Present, draft or published:** accept only if its target is BASE_SHA and its title and notes
   equal the fixed templates; refuse otherwise, naming what differs.
3. **Any failed lookup:** refuse.

Then each asset follows the digest rule below, and a draft is published last. A partial upload
therefore leaves a draft the re-run completes, `prove` only ever fetches from a complete published
release, and the order stays valid if the tap ever enables immutable releases.

**The digest rule: skip identical, refuse different.** GitHub refuses an asset whose name already
exists on a release (HTTP 422; "must delete the old file before you can re-upload"). The releases
API reports each asset's `digest` as `sha256:<hex>` (checked against this repo's own v2.15.1
assets). So an existing asset whose digest matches is skipped, one whose digest differs is refused
naming the tag and both digests, and one with no digest is refused. The recovery cases:

- A bottle job failed; re-run failed jobs. The build re-runs and `upload` runs for the first time
  under the same tag. Nothing collides.
- `upload` failed after creating the draft or uploading some assets; re-run failed jobs. The draft is
  found and accepted, the same artifacts arrive, uploaded assets are skipped by digest, and the
  rest upload.
- A bottle already uploaded is rebuilt under the same tag by re-running a specific job that had
  succeeded. The release is accepted, the digest differs, and the upload refuses. No asset is ever
  replaced, so a formula already pushed against that asset stays valid. Re-running failed jobs meets
  the same clash; re-running the `homebrew / plan` job may recover it under a new tag, release and
  BASE_SHA, but that is unverified (§8), and the next release is the fallback.

**The release's title and notes are fixed templates,** built by a pure function of VERSION, the tag,
the caller (release or dry run, from `push_target`) and the calling run's URL: no command output and
no generated notes. The release and its assets are created, edited and published only by `upload`'s
subcommand of `scripts/homebrew_bottles.py`.

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

**`brew bottle --json` runs with `--no-rebuild`.** Without it, `dev-cmd/bottle.rb` takes the rebuild
number from the tap's `origin/HEAD` formula: when that formula already carries the same version, the
rebuild becomes its rebuild plus one. Every dry run targets the version the release already pushed,
so it would build `rebuild 1` and a `.bottle.1.tar.gz` asset, a different shape from every release.
`args.no_rebuild?` is read only on that bottling path, returning 0 unconditionally; the merge takes
rebuild from the JSON, and `upload` refuses anything but 0.

`scripts/render_homebrew_formula.py` does not emit the block. The renderer writes a skeleton that
Homebrew's tools complete (`update-python-resources` already edits it in place), and a Python
writer of the block's format would be a second statement of Homebrew's DSL. `push` does parse the
block, but as a checker: it accepts exactly the expected form and refuses everything else (§5).

`BOTTLE_ERB` in `dev-cmd/bottle.rb` writes `root_url` and a path-valued `cellar` into the formula
unescaped. A JSON a compromised build rewrote is therefore formula code once merged. That is why
the merges run only in token-free jobs, why `upload` validates every JSON field before the token
exists, and why `push` validates the merged text.

Because the merge edits the formula after `brew audit` ran, `brew style --formula` checks the
shipped text; it changes nothing without `--fix`. RuboCop cops such as
`FormulaAudit/ComponentsOrder` are what a misplaced block trips.

## 5. The pushed formula is validated as data

`formula`, `bottle` and `prove` all ran third-party code on runners that also held the formula
file. Any of them could put Ruby into the text every user's `brew` evaluates. So before `push`
mints its token, the merged formula must be exactly:

- `render(sdist_url=..., sha256=...)` with `plan`'s values, produced by this repository's renderer at
  the release ref; plus
- resource stanzas, each of exactly four lines and one blank line, every line anchored at both
  ends: `resource "<name>" do`; a `url "https://files.pythonhosted.org/packages/<2 hex>/<2 hex>/<60
  hex>/<project>-<version>.tar.gz"` line; `sha256 "<64 hex>"`; `end`. `<name>` and `<project>` use
  only the PEP 508 name characters (`A-Z a-z 0-9 . _ -`), `<version>` only `A-Z a-z 0-9 . + !`, and the
  project, normalised as PEP 503 does, equals the name, normalised the same way; plus
- one `bottle do` block whose `root_url` is `plan`'s, with one `sha256` line per declared tag and no
  others, each cellar allowed by §3, each digest equal to the one the releases API reports for that
  tag's asset on `plan`'s release, and no `rebuild` line.

Anything else anywhere in the text refuses the push. The character sets are what keep a quoted field
from carrying Ruby: a double-quoted string interpolates `#{...}` when the formula loads, and `#`, `{`,
`}`, `"` and `;` appear in no allowed set.

**Its inputs are arguments, never parsed from the artifact.** The validator takes the sdist URL and
digest, the root URL, the declared tags and the release's digests as arguments. `push` supplies them
from `plan`'s outputs and from its own releases API read for `plan`'s tag. A validator that re-read
the top-level `url` out of the file under test would compare the artifact with itself.

**Measured feasibility, 2026-09-14.** The live 2.15.1 tap formula, with its resource stanzas removed
by that grammar, is byte-identical to `render()` for its URL and digest. Every `resource` in it
matched the grammar, and every resource name matches its sdist's project. Two controls, one with a
`system` line injected into a stanza and one with a stanza's URL moved to another host, each fail
the comparison. The bottle block's placement is not yet measured: the plan's first task produces a
real merge locally, and its sanitised output (`example.invalid` URLs, fake digests) becomes the
validator's accept fixture, so the accept rows certify measured text rather than a guess.

**What it bounds, and what it cannot.** The validator guarantees the shipped formula is the
renderer's text plus PyPI sdists with digests plus a bottle block consistent with the uploaded
bytes. It cannot tell a correct resource *set* from a tampered one: the set comes from resolution in
`formula`, which runs third-party code, and a resource added there that is a real PyPI package would
pass. A change Homebrew makes to `update-python-resources`' output format fails the push loudly,
which is the intended direction.

**Where the stanzas and the block sit, and how the text is read (plan review).** The stanzas must
form one contiguous run directly above `def install`: `render()` emits no resource, so
`update-python-resources` takes `utils/ast.rb::replace_resource_stanzas`' insert arm, which writes the
group there. The bottle block must directly follow the `license` line and its blank line, where
`brew bottle --merge` adds it. Without a position, a run or a block moved below the class's closing
`end` would still reduce to the renderer's text. The formula is read as bytes, and a carriage return anywhere refuses: `read_text()`
turns a lone CR into LF, so the text validated would not be the bytes pushed, and Ruby does not end a
line at a lone CR.

## 6. Proof before the release is public

The publication point is the push of `Formula/job-sluice.rb` to the tap's default branch. An
uploaded asset that no formula references reaches no user.

### 6a. The pour, in each bottle job

After that job's single-tag merge:

1. **Negative control.** Run the pour check in its `--expect-built` mode against the `--build-bottle`
   keg still installed. The mode exits non-zero by itself when the keg reads as poured; a `!`-inverted
   command anywhere but a step's last line never fails a `bash -e` step.
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
cached file. It is kept, and its checksum is still verified. The plan's first task ran this locally,
on a local machine's Homebrew; it has not yet run on a runner (§8).

`brew install ./<bottle>.tar.gz` is not used. `env_config.rb::forbid_packages_from_paths?` refuses
path installs by default, and a path install bypasses the formula's bottle block, so it would prove
less.

`--build-bottle` skips `post_install` (`formula_installer.rb`: `build_bottle? ||
skip_post_install?`) and a normal pour runs it. The formula has no `post_install` today; if one is
added, steps 4 to 7 exercise it.

### 6b. Every URL, in `prove`

`prove` downloads the formula and the JSONs, never a bottle tarball, and nothing in it writes under
`brew --cache` except the fetch. After the merge and `brew style`, and after `upload` has published
the release:

1. **Tag-set check** on the merged block: its tags equal `plan`'s declared set exactly. The declared
   set comes from `plan`, never from the block being checked.
2. For each declared tag, in order: remove any cached copy; `brew fetch --force --bottle-tag=<tag>
   <tap>/job-sluice`; then assert the file at `brew --cache --bottle-tag=<tag>` exists and its sha256
   equals the block's.

The file assertion is required. `cmd/fetch.rb` answers a tag absent from the block with `opoo
"Bottle for tag ... is unavailable."` and moves on, and `opoo` never marks the command failed, so the
exit status alone proves nothing for a missing tag. `--force` is what clears a cached copy
(`formula.clear_cache if args.force?`), so a seeded cache cannot stand in for a download. For a tag
that is present, a wrong asset name, a wrong release, a disagreeing root URL and a byte mismatch all
fail here.

`prove` evaluates the merged formula, which is why it references no secret. A formula tampered with to
fake this job's success still meets `push`'s validation: `prove` answers whether Homebrew builds URLs
that serve these bytes, and `push` answers whether the text is the expected text.

### 6c. The push

In `push`, in order:

1. **Validate** the merged formula (§5).
2. **Clone** the tap with plain `git` into `$RUNNER_TEMP`, hooks disabled.
3. **Target presence:** `git ls-remote --exit-code origin refs/heads/<TARGET_BRANCH>`. Exit 0 is
   present only when it lists exactly that one branch, whose commit is the scratch push's lease; any
   other listing refuses. `ls-remote` matches a pattern against the tail of every ref name, so a tag
   stored as `refs/tags/refs/heads/<TARGET_BRANCH>` would otherwise read as the branch, and the push
   would move the tag. Exit 2 is absent, which is normal for the first dry run of a version and
   refused for the default branch; any other exit refuses. A dry run whose target is the default
   branch refuses when git lists the formula at BASE_SHA: `plan` sends a dry run there only while the
   contents API reads the formula as absent (the bootstrap), and git's read here cross-checks that
   observable.
4. **No-op:** if the target is present and its formula is byte-identical to ours, finish with nothing
   to do. That is a re-run after this release's push already landed. The comparison reads the remote
   tip, never what step 6 builds.
5. **Version (default branch only):** read `git show <BASE_SHA>:Formula/job-sluice.rb`. A present
   commit without the file is the bootstrap and passes. Otherwise parse the version from the `url`
   line's `job_sluice-<version>.tar.gz` (the renderer writes no `version` stanza) into a tuple of
   integers; an unparseable file refuses; a version newer than VERSION refuses. Integers, because this
   project's releases cross 2.9.x to 2.10.0, where string order is backwards.
6. **Commit on BASE_SHA, with no work tree:** hash the validated bytes from a file outside the clone,
   which no tap attribute can match (`--no-filters` is a defence that keeps that true if the file ever
   moves inside the clone), read BASE_SHA (from `plan`) into the index, set `Formula/job-sluice.rb` to
   that blob as a regular file, and `commit-tree` the result with BASE_SHA as its parent, as
   `sluice-release-please[bot]` with the message `job-sluice <VERSION>`. A tree equal to BASE_SHA's
   refuses: it is compared with the base, not the target, and step 4 already found the target without
   these bytes, so a no-op reported here would leave the formula unpublished behind a green step. A
   checked-out tree would let the tap's own content decide what is stored (a `.gitattributes`
   working-tree encoding) or where the bytes land (a symlink committed at the formula's path, followed
   out of the clone).
7. **Mint** the token and **push**: fast-forward only for the default branch, `--force-with-lease` for
   a scratch branch.

Steps 3 to 5 are one pure function over what was read, so they are tested offline (§9a).

Building on BASE_SHA is what stops a re-run from rolling the tap back. Re-running an older release's
failed jobs reuses that release's `plan` outputs, so if a newer release landed in between, the push
is a non-fast-forward and is rejected. Step 5 covers a re-run that re-executes `plan` and so reads
the moved tip.

### Where the token is

| Job | Kind | Token |
| --- | --- | --- |
| preflight (dry run) | trusted | no |
| plan | trusted | no |
| formula | third-party code | no |
| bottle | third-party code | no |
| upload | trusted | yes |
| prove | third-party code | no |
| push | trusted | yes |

## 7. Failure modes, from the user's side

| Failure | Caught at | What a user sees |
| --- | --- | --- |
| an invalid `push_target` or VERSION, a bad PyPI answer, an unresolvable tap, or a failed bootstrap lookup | `plan` | nothing |
| resources, audit, build, or the built keg's `brew test` fails | `formula` or `bottle` | nothing |
| a runner produced the wrong tag | `bottle`'s tag check | nothing |
| a bottle does not pour, fails `brew test`, or has broken linkage | 6a | nothing |
| a JSON or bottle is missing, altered or inconsistent | `upload`'s validation | nothing |
| an existing release that does not match, or a failed release lookup | `upload`'s lifecycle | nothing |
| a rebuilt bottle meets an already-uploaded asset | the digest refusal | nothing; re-running the `homebrew / plan` job may recover it with a new tag, release and BASE_SHA, but that is unverified (§8), and the next release is the fallback |
| the merged block lost or gained a tag | 6b's tag-set check | nothing; orphan assets |
| a URL, name, release or byte mismatch | 6b | nothing; orphan assets |
| the formula text was altered anywhere | `push`'s validation | nothing; orphan assets |
| an artifact carrying a git hook or a shadowing script | §2's artifact rule | nothing |
| the tap moved since `plan`, or an older release re-runs | 6c | nothing; a refused or rejected push. For a tap that moved, re-running the `homebrew / plan` job may recover it by planning again under a new run attempt, with a new tag, release and BASE_SHA; whether GitHub offers that job re-run inside a called workflow, and re-runs the jobs after it, is unverified (§8), and the next release is the fallback |
| homebrew-core later changes a library the keg links by path | **nothing catches it** | a failing pour on that tag until the next release; `brew install --build-from-source` works around it |

The last row is the one risk bottling adds. A source build links against whatever homebrew-core
ships on the day of the install; a bottle's links are fixed on release day. `brew linkage --test`
proves the links hold at release time, and every release re-bottles against current homebrew-core.

macOS 27 pours `arm64_tahoe` (§1), and no generally available runner can prove that pour. That is a
residual, stated here.

## 8. Not yet executed

**The first dry run proves these** before a release depends on them:

- the reusable workflow called with explicit secrets, and the App token minted inside it;
- the matrix generated from `plan`'s output;
- `plan`'s contents API read with the workflow's `GITHUB_TOKEN`;
- `brew install` using the seeded cache file while the asset is still missing (§6a);
- `brew bottle --json --no-rebuild` naming, and `--merge --write --no-commit` with two JSONs;
- `brew fetch --force --bottle-tag` for a tag other than the runner's own, from a public release
  asset, and once for a tag deliberately absent from the block, recording the exit status;
- creating a draft release, uploading assets to it through its upload URL, and publishing it, with
  the App token, and the digest comparison against a real asset;
- the whole chain on `macos-15`; the 2026-09-07 measurement ran on a local macOS 26 machine;
- `brew linkage --test` on both runners;
- Homebrew's tap-trust gate: every macOS job trusts the tap in `homebrew_tap_checkout.sh`, so
  `prove`'s merge loads the formula. Read from Homebrew 6.0.22's `Library/Homebrew/trust.rb`; locally
  the plan's first task needed `brew trust` before `brew tap`;
- the wall-clock time the release gains.

**Measured locally before the validator is written:** a real two-tag `--merge --write --no-commit`,
to fix where the block lands relative to the renderer's text (§5).

**Read, not measured: re-run semantics inside a called workflow.** That re-running failed jobs reuses
the outputs and artifacts of jobs that succeeded holds for `release-please.yml`'s own jobs; that it
holds for jobs inside `homebrew.yml` is assumed. BASE_SHA's rollback protection and the §3 recovery
cases depend on it. A deliberate job-level re-run of a dry run's `upload` exercises it; re-running
failed jobs and re-running the `homebrew / plan` job, the operations the recovery comments name,
stay read, not measured.

**Read, not measured: re-running the `homebrew / plan` job inside a called workflow.** Whether GitHub
offers a single-job re-run for a called workflow's job, whether that re-run also re-runs the jobs
after it, and whether `github.run_attempt` increments: GitHub's re-run documentation addresses none of
them. The release's recovery comment names that re-run only as a possibility, with the next release as
the fallback.

**Read, not measured: secret delivery.** That a called workflow's secrets reach only the jobs whose
steps reference them is assumed. The wiring tests pin that no untrusted job references one; no dry
run can show a secret absent from a runner.

**Reached by no dry run, proven only offline (§9a):** the version refusal, the byte-identical no-op,
a non-fast-forward on a moved tip, `auto`'s absent arm once the tap holds a formula, an existing
release that does not match, and the digest refusal.

**Measured already:** `git ls-remote --symref` resolves the tap's default branch and its SHA; the
contents API answers `200` and `404` for present and absent paths at a commit; the §5 grammar against
the live formula; `python3 -P`.

## 9. Tests

### 9a. Decision logic, offline

`scripts/homebrew_bottles.py` (stdlib only, beside `render_homebrew_formula.py`) holds every decision
as a pure function, with one named CLI subcommand per job step that calls it.
`tests/test_homebrew_bottles.py` drives the functions with synthetic fixtures: root URLs under
`example.invalid`, fake digests, owner `ExampleOwner`. Every function has at least one accept row
built from realistic input and the refuse rows below. The accept rows are witnessed by an
always-refuse mutant. The file ports
`tests/test_homebrew_formula.py::test_every_expected_constant_is_built_only_from_literals` over its
own expected constants.

- **Inputs (`plan`):** refuses an invalid or differently-cased `push_target`, naming both valid
  values, as the first failure when executed with an empty `PATH`; refuses a non-`X.Y.Z` VERSION; a
  sdist URL off-grammar or for another file; a non-hex or short sha256; an output value with a
  newline, and an output name that is not a lower-case identifier.
- **Target:** `default` gives DEFAULT_BRANCH whatever the observable says; `auto` with the formula
  present gives `bump-<VERSION>`; `auto` absent gives DEFAULT_BRANCH; `auto` with a failed lookup
  refuses.
- **Tag and root URL:** equal inputs give an equal tag; changing only the run id, or only the
  attempt, changes it; owner `ExampleOwner` gives a lower-cased owner segment.
- **Platform pairs:** the JSON the `plan` subcommand actually emits, parsed, gives exactly the set
  restated by hand in the test, `{("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe")}`, never
  imported.
- **Pour check:** accepts one poured keg at VERSION; refuses `job-sluice` not poured while its
  dependencies are, no `job-sluice` entry, an empty `installed` list, and a keg at another version.
  `--expect-built` accepts a built keg and refuses a poured one.
- **Produced tag:** accepts one tag equal to the declared one; refuses a mismatch, zero tags, two
  tags, and an empty declared tag.
- **Bottle JSON:** accepts two valid JSONs with their bottles; refuses a wrong root URL, a non-zero
  rebuild, a disallowed cellar, either name wrong, a path component in either name, a digest
  mismatch, a JSON whose bottle is absent, a declared tag missing, an extra tag, a path-shaped
  undeclared tag (refused before any file is read), and an empty declared set.
- **Release lifecycle:** absent creates a draft; present and matching is accepted, draft or
  published; present with another target, title or notes refuses; a failed lookup refuses.
- **Digest rule:** no asset uploads; a matching `sha256:` digest skips; a different digest refuses; a
  missing digest refuses.
- **Release templates:** the title and notes equal a template restated by hand, for each caller.
- **Merged tag set:** accepts the declared set; refuses a missing tag, an extra tag, and an empty
  declared set, taking the declared set as an argument.
- **Formula validator:** accepts the measured merge fixture. Its injection rows are generated from
  that fixture, never hand-picked, with the case count asserted first: for every line, a statement
  inserted before and after it, `; system "x"` appended to it, and `#{1}` inserted inside each quoted
  value on it, each refused. Also refuses a top-level `url` and a top-level `sha256` each differing
  from the arguments; a foreign resource host; a resource whose project is not its name, including a
  prefix collision (`alpha` against `alpha-beta-1.0.tar.gz`); a wrong root URL; a missing or extra
  tag; a digest different from the release's; and a `rebuild` line. Accepts PEP 503 variants
  (`alpha_beta-1.0.tar.gz` for `alpha-beta`, and a case variant).
- **Push decision:** from the remote formula (present or absent), ours, whether the target is the
  default branch, the version at BASE_SHA and VERSION. Identical bytes are a no-op; different bytes
  push; an absent remote pushes; tap `9.10.0` against VERSION `9.9.0` on the default branch refuses;
  equal and older push; a newer version on a scratch target pushes; the file absent at BASE_SHA
  passes as the bootstrap; an unparseable `url` refuses; an absent default branch refuses. A dry run
  targeting the default branch with the formula at BASE_SHA refuses; a dry run with no formula at
  BASE_SHA (the bootstrap) pushes, and so does a dry run targeting a scratch branch.
- **Push, against local repositories:** a tag whose name tail-matches the target branch refuses; a
  `.gitattributes` working-tree encoding in the tap leaves the pushed bytes unchanged; a symlink at
  the formula's path is replaced, never followed; a symlinked `Formula` directory refuses with
  nothing written outside the clone; the pushed commit's parent is BASE_SHA and its only change is
  the formula.

### 9b. Workflow wiring

`tests/test_release_publish_wiring.py` gains these properties, each witnessed by its mutant:

- **Triggers and inputs:** `homebrew.yml` triggers only on `workflow_call`; its inputs are required
  with no default; its secrets are exactly the two App secrets, each required.
- **Callers:** both use `$/.github/workflows/homebrew.yml` exactly, pass the secrets by name, never
  `secrets: inherit`, and pass `push_target` `default` and `auto` respectively. The dry run passes
  `ref: ${{ github.sha }}`; its call job needs `preflight` and carries no status-function `if:`.
- **Rosters and permissions:** exact job rosters for `homebrew.yml` and the dry run, an exact
  `permissions` block per job, and read-only workflow-wide blocks.
- **Edges and keys:** exact `needs:` per job. No job and no step in `homebrew.yml` carries `if:` or
  `continue-on-error`: step keys are an allow-list, `{name, id, env, run}` and `{name, id, uses, with}`.
- **Runners:** `bottle`'s `runs-on` is the matrix runner key and its tag check reads the matrix tag
  key, both named as in `plan`'s emitted JSON; every other job's `runs-on` equals an exact map.
- **Trusted jobs:** `plan`, `upload`, `push` and `preflight` have exact `uses:` rosters at pinned SHAs;
  every `run:` in `plan`, `upload` and `push` is `python3 -P <absolute scripts/ path> <subcommand>`, and
  `preflight`'s two `run:` bodies are pinned whole.
- **Secrets:** any reference to the `secrets` context (`secrets.`, `secrets[`, `toJSON(secrets)`)
  appears only in `upload` and `push`, and `create-github-app-token` only there.
- **Artifacts in token jobs:** every `download-artifact` in `upload` and `push` has an explicit path
  under `runner.temp`; every `git` invocation in them carries `-c core.hooksPath=/dev/null`.
- **Outputs:** token jobs read no `needs.<job>.outputs` except `plan`'s. Every
  `needs.<job>.outputs.<key>` names a declared output, and every declared output names a step that
  exists.
- **One derivation per fact,** swept over `homebrew.yml` and every script its jobs invoke, checked by
  AST over `homebrew_bottles.py` subcommands: the tag and root URL composition and both spellings of
  the run attempt are reachable only from `plan`'s subcommand; `update-python-resources` appears only
  in `formula`; `render()` is called only by `formula`'s render step and by the validator; `push` runs
  no `ls-remote --symref`, `remote set-head` or `symbolic-ref`, and its one `ls-remote` is
  `--exit-code` against `refs/heads/` of `plan`'s TARGET_BRANCH. The mints' `owner:` and `plan`'s
  TAP_OWNER source are the same `github.repository_owner` expression.
- **`formula`'s order:** tap at BASE_SHA < render < `update-python-resources` carrying
  `--ignore-main-package-cooldown` < `brew audit --strict --online` < upload.
- **`bottle`'s sequence,** matched as whole lines, counting occurrences, over comment-stripped text:
  `install --build-bottle` < first `test` < `bottle --json --no-rebuild` < `bottle --merge` <
  `--expect-built` check < `uninstall` < cache seed < `install` < pour check < second `test` <
  `linkage --test`.
- **No inverted checks:** no `run:` line in `homebrew.yml` or a script it invokes begins with `!` followed by a space, and
  no check invocation is followed by `|| true`.
- **`upload`'s order:** validate < mint < release lifecycle < asset uploads < publish.
- **`prove`:** its downloads name the formula and the JSONs only; nothing writes under `brew --cache`
  but the fetch; per tag, removal < `fetch --force` < cache-file check; merge < style < tag-set check
  < fetch loop.
- **`push`'s order:** validate < clone < target presence < no-op < version < commit built on
  `plan`'s BASE_SHA without a work tree < mint < push; exactly two push arms, fast-forward for the default
  branch and `--force-with-lease` for a scratch branch; the commit's identity and message are the
  fixed ones.
- **Releases:** the release is created, edited, uploaded to and published only from `upload`'s
  subcommand, with its title and notes from the template function, and no generated notes.
- **Shell:** the bash 3.2 sweep's roster is derived from both ends (every script a `homebrew.yml` job
  invokes is in it, and every entry is invoked) and also covers `homebrew.yml`'s own `run:` bodies; no
  `${{ }}` inside any `run:`.
- **Owner:** swept over `homebrew.yml` (comment-stripped `run:` bodies and `with:` values) and every
  script its jobs invoke, with the owner read at test time from `pyproject.toml`'s
  `[project.urls] Source`, never typed into the test. The allowed occurrences are listed exactly (the
  renderer's `_HOMEPAGE`, which is the upstream project's homepage), with a control that fails if
  the list grows.

**Where every existing test goes.** The Homebrew tests in `tests/test_release_publish_wiring.py`
today, and the constants they use:

| Existing test or constant | Successor |
| --- | --- |
| `test_homebrew_dry_run_declares_exactly_the_jobs_this_file_pins`, `_HOMEBREW_DRY_RUN_JOBS` | the dry-run roster, now `preflight` and the call job; plus `homebrew.yml`'s roster |
| `test_the_homebrew_job_has_no_elevated_permissions` | the caller's permissions pin, plus per-job pins in `homebrew.yml` |
| `test_the_homebrew_job_is_gated_on_release_created` | unchanged: the caller keeps its `if:` |
| `test_the_homebrew_job_runs_on_macos` | retired: a `uses:` job carries no `runs-on`; replaced by the runner pins |
| `test_the_homebrew_job_waits_for_the_pypi_upload` | unchanged: the caller keeps `needs: pypi` |
| `test_the_homebrew_release_job_verifies_before_minting_a_token_before_pushing`, `test_the_homebrew_dry_run_verifies_before_minting_a_token_before_pushing` | the `needs:` edges plus `upload`'s and `push`'s in-job orders |
| `test_the_homebrew_verify_script_updates_audits_installs_and_tests_in_order` | `formula`'s order and `bottle`'s sequence |
| `test_the_homebrew_verify_script_reseats_the_tap_checkout_before_rendering` | retired: replaced by tap at BASE_SHA < render in `formula`, and `push`'s commit built on `plan`'s BASE_SHA |
| `test_the_homebrew_push_script_actually_pushes` | `push`'s two push arms |
| `test_the_homebrew_release_verify_step_carries_no_token_and_targets_the_default_branch`, `test_the_homebrew_dry_run_verify_step_carries_no_token_and_targets_auto` | the callers' `push_target` pins and the secrets-context pin |
| `test_the_homebrew_release_push_step_carries_the_token`, `test_the_homebrew_dry_run_push_step_carries_the_token` | the secrets-context pin |
| `test_the_homebrew_release_push_step_carries_no_if_key`, `test_the_homebrew_dry_run_push_step_carries_no_if_key`, `_PUSH_STEP_ALLOWED_KEYS` | the step-key allow-list over every step in `homebrew.yml` |
| `test_both_homebrew_verify_steps_pass_the_repository_owner`, `_VERIFY_STEP_OWNERS` | the `owner:` expression pin |
| `test_the_homebrew_verify_script_fails_loudly_on_an_invalid_push_target` | the executed `push_target` refusal row (§9a) |
| `test_the_homebrew_verify_script_bypasses_the_release_cooldown` | `formula`'s order pin, which carries the flag |
| `test_the_homebrew_scripts_use_no_bash_4_only_constructs`, `_MACOS_SHELL_SCRIPTS` | the two-ended bash 3.2 roster |
| `test_the_homebrew_scripts_never_use_tap_new` | the same ban over the tap-checkout script, which carries its reasoning |
| `test_the_homebrew_dry_run_refuses_a_non_default_branch` | `preflight`: refusal step before the lookup |
| `test_the_homebrew_dry_run_has_no_elevated_permissions` | per-job permissions in the dry run |
| `test_the_homebrew_dry_run_triggers_only_on_workflow_dispatch`, `test_the_homebrew_dry_run_workflow_wide_permissions_are_read_only` | unchanged |
| `test_the_homebrew_dry_run_drives_the_same_verify_and_push_scripts_as_the_release_job` | both callers use `$/.github/workflows/homebrew.yml` |

The plan's review includes `sluice-architect`: this is the repo's first reusable workflow, and it
turns step boundaries into job boundaries.

## 10. Prose that changes

Claims that become false, each owned by a task in the plan:

- `scripts/render_homebrew_formula.py`: the `_EXTRA_PACKAGES` rationale ("this tap publishes NO
  BOTTLES, so every `brew install` builds from source") and the `_PYTHON_FORMULA` comment naming
  `--build-from-source` in `homebrew_verify.sh`. The rule survives on narrower grounds: users whose
  macOS matches no bottle build from source.
- `tests/test_homebrew_formula.py`: the same premise.
- `homebrew_push.sh`: retired into `push`. Its header's "same job, on the same runner" goes; its
  byte-identical no-op comment's named case (a second dry-run dispatch) can no longer occur, and the
  reachable case is a re-run after this release's push landed.
- `homebrew_verify.sh`: retired; its reasoning moves with its code, including the `brew tap-new`
  ban's two reasons, which move to the tap-checkout script, and the cooldown diagnostic's recovery
  text.
- `release-please.yml`'s `homebrew` job: the recovery, no-token, `--build-from-source` and macOS
  comments.
- `homebrew-dry-run.yml`: the header's "Proves the WHOLE chain" list and the PUSH_TARGET prose.
- Test docstrings naming `--build-from-source` or `macos-latest`.
- `docs/INSTALL.md`'s Homebrew section: which Macs pour a bottle and which still build from source.

## 11. Review round 1: dispositions

All findings were accepted. The one flagged for owner judgement (arch-004, whether gem-running
merge steps may share the token job) is decided by `release-please.yml`'s own rule that only a job
boundary enforces a split, and is resolved more strictly than proposed: no token job runs Homebrew.
Round 2 judged five of these partly fixed; their remainders are closed by the round-2 rows below.

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

## 12. Review round 2: dispositions

All findings were accepted.

| Findings | Disposition |
| --- | --- |
| rev2-001, arch2-005, inv2-004 | §3 release lifecycle: list, draft only when absent, accept only on match, publish last; §9a rows |
| inv2-002, rev2-002, arch2-002, test2-004 | §2 `plan`: three-valued contents API read at BASE_SHA; §9a target rows; `push` clones with plain `git` |
| inv2-001 | §2 artifacts in token jobs: runner-temp downloads, hooks disabled, `python3 -P`, formula written by content; §9b pins |
| arch2-001, test2-002 | §2 trust rule by what a job feeds, `plan` and `preflight` classified, exact `uses:` roster; §9b step-key allow-list, secrets-context sweep, command heads |
| test2-001 | §5 anchored lines, character sets, arguments never parsed from the artifact, measured accept fixture; §9a generated injection rows, top-level `url`/`sha256` rows, PEP 503 rows |
| test2-003, inv2-003, rev2-004, rev2-005 | §6c target presence, no-op, integer version from BASE_SHA, commit identity; §8 unreachable-by-dry-run list; §9a push-decision rows |
| rev2-003, arch2-003, neu2-001 | §2 one derivation per fact, restated per fact with `render()`'s one other caller named; §9b AST-scoped pin and owner pin |
| arch2-004, test2-006 | §2 cooldown diagnostic reuses `plan`'s URL; §9b `formula`'s order and the successor table; §10 `tap-new` reasoning |
| arch2-006, inv2-005 | §2 `plan` validates VERSION and PyPI values; the edge-by-output overclaim deleted |
| arch2-007 | §2 the dry run's `ref`; the callers' differences stated |
| test2-005 | §1 matrix keys; §9a emitted-JSON row; §9b runner pins; ported literal-constant guard |
| test2-007 | §6a `--expect-built` mode; §9b no inverted checks |
| test2-008 | §6b `prove`'s downloads and cache; §9b pins |
| test2-009 | §9a an accept row for every function |
| test2-010 | §3 template function and single writer; §9b releases pin |
| neu2-002 | §6c fixed commit identity and message; §9b |
| rev2-006 | Decisions and §4: `--no-rebuild` on `--json` only |
| rev2-007 | §1 `ubuntu-latest` reasoning; counts removed |
| inv2-006 | Out of scope: which releases are safe to delete; §3 caller in the title |

## Out of scope

- Intel and macOS 14 bottles (§1).
- A post-release Homebrew install check in `post-release.yml`: §6b fetches the exact URLs and bytes
  users receive before the formula that points at them is pushed.
- `brew test-bot` or any workflow inside the tap: the tap-checkout script records why the tap carries
  none (a second automated writer of a machine-owned formula, and an App token that cannot push
  `.github/workflows/`).
- Deleting releases. A release is safe to delete only if no tap commit has ever named its tag
  (`git log -S<tag>` in the tap): a machine whose tap has not auto-updated still fetches the bottles
  an older formula names, and a failed bottle download does not fall back. The release title records
  whether a release or a dry run created it.
- Proving the resource set itself is the correct closure (§5).

## 13. Plan review round 1: what changed in the design

The implementation plan's first review added these, each argued in the plan where it lands:

- The Homebrew fixtures live in `tests/homebrew_fixtures/`, with their own closure and content pins:
  `tests/test_fixture_name_neutrality.py` admits only captured board payloads under `tests/fixtures/`.
- `formula`, `bottle` and `prove` export `HOMEBREW_NO_AUTO_UPDATE=1`. Auto-update can rebase a tap
  checkout off BASE_SHA and leave the formula copied into it stashed (`cmd/update.sh::merge_or_rebase`).
- Every `upload-artifact` step carries `overwrite: true`: a re-run of a failed job uploads under a
  name an earlier attempt may already have used, and token jobs validate whatever they read.
- Each step's `env:` is pinned against the variables its command and its script read, beside §9b's
  `owner:` expression pin for what the retired owner-pin test guarded; the untrusted jobs' `run:` lines
  are pinned to the rostered scripts; each script's first command is pinned to `set -euo pipefail`,
  and the swallow ban covers `|| :`, `|| exit 0`, `|| echo`, `set +e`, `shopt -u` and `trap`.
- A failed tree read in `push` refuses rather than reading as an absent formula (§6c), and the
  resource run's contiguity and position, and the bottle block's position, are checked (§5).
- Accept rows are witnessed by an always-refuse mutant (§9a), and the `push-prepare` and
  `push-publish` subcommands by CLI rows that record every argument they pass.
- No workflow in the repository restores an Actions cache, pinned by a test: the untrusted jobs' token
  can save an entry that a later run on the same branch could restore (§2).
- Sections 1, 2, 5, 8 and 9b were edited in place to match: trusted jobs run only `python3 -P` on
  `scripts/homebrew_bottles.py`, which runs `git`; `plan` reads the contents API through `urllib`;
  `preflight`'s two run bodies are pinned whole; untrusted jobs reference no secret but hold a
  `contents: read` token and the artifact token; and secret delivery is recorded as read, not measured.

## 14. Execution: what changed in the design

Task reviews during execution found these, and "A note on method" and sections 2, 3, 6a, 6c, 7, 8, 9a,
9b and 10 were edited in place to match:

- `upload`'s validator refuses a bottle JSON whose one tag is undeclared before it builds a file name
  from that tag or reads a file (§3). Reproduced first: a path-shaped tag with a matching
  `local_filename` made the token job read and hash a file outside the downloaded artifacts.
- `plan` refuses an output name that is not a lower-case identifier, beside an output value with a
  newline: GitHub reads both `name=value` and `name<<DELIMITER`. Not reachable before the change,
  since every name is a literal in `build_plan`, but closed in the one function that exists for it.
- `push` reads its target as present only when `ls-remote` lists exactly that branch, and builds its
  commit without a work tree (§6c). Reproduced first, each with content already in a tap: a tag named
  `refs/heads/<branch>` read as the branch and was moved by the push; a `.gitattributes` encoding
  re-encoded the pushed bytes; and a symlink at the formula's path sent the write outside the clone.
- Both callers use GitHub's self-repository form, `uses: $/.github/workflows/homebrew.yml` (§2), which
  GitHub documents as the recommended reference to a workflow in the same repository and which
  zizmor's `self-repository` audit requires. Like `./`, it resolves to the caller's own commit.
- Every macOS job trusts the tap before its first `brew` command that loads the formula
  (`homebrew_tap_checkout.sh`). The whole-branch review read, in Homebrew 6.0.22's source, that
  `prove`'s merge names no formula on its command line and so would be refused by the tap-trust gate,
  after `upload` had published the release; `bottle` passed only because its install records trust and
  its uninstall removes it again. The dry run is the first run on a runner.
- The release recovery names re-running the `plan` job for a tap that moved after `plan` ran and for
  a digest clash, where re-running failed jobs would fail the same way every time. That `plan` re-run
  is unverified (§8), so the recovery names it as a possibility, with the next release as the
  fallback.
- `push` refuses a dry run targeting the tap's default branch when git lists the formula at BASE_SHA
  (§6c). `plan` sends a dry run there only while the contents API reads the formula as absent, and
  nothing re-checked that answer, although the token job already reads the same tree through git; a
  wrong 404 would have made a dry run's formula, whose bottles live in a dry-run release, the tap's
  tree of record.
- `VERSION`, the tag and the formula's top-level URL accept ASCII digits only; `\d` also matches every
  other Unicode decimal digit.
