# Homebrew bottles — design (#279)

Status: platform set decided by the owner, 2026-09-14 (§1). Not yet reviewed by `/review-plan`.

Issue: **#279** (the tap publishes no bottles, so every `brew install MrReasonable/tap/job-sluice`
builds the vendored resource tree from source). The measurement this rests on is the issue's
2026-09-07 comment: the libexec venv bottles, Homebrew's own detector picks `cellar: :any`, and a
poured bottle passes `brew test`.

## A note on method

Every Homebrew behaviour this design depends on was either executed (the 2026-09-07 measurement)
or read out of the installed Homebrew 6.0.22 source, and is cited where it is used. Reading is
weaker evidence than running. Everything not yet executed is listed in §7, and the first dry run
exists to prove those items before a release depends on them.

## Decisions

| Decision | Choice | Source |
| --- | --- | --- |
| Platform set | `arm64_sequoia` (`macos-15`) + `arm64_tahoe` (`macos-26`) | owner, 2026-09-14 |
| Intel and macOS 14 | not bottled | §1 |
| Runner labels | pinned; never `macos-latest` | §1 |
| Workflow shape | one reusable workflow, called by the release and the dry run | §2 |
| Resource fill | once, in a formula job; every build consumes that one formula | §2 |
| Host | GitHub Releases on `MrReasonable/homebrew-tap` | §3 |
| Release tag | computed once per run attempt, passed to every job | §3 |
| Upload | same name and same digest is skipped; a different digest is refused | §3 |
| Asset name | the `filename` field of `brew bottle --json`, never assembled | §3 |
| `bottle do` writer | `brew bottle --merge --write --no-commit` | §4 |
| Proof | each tag poured and tested on its own runner before any token exists; every tag fetched from its real URL before the push | §5 |

---

## 0. What changes for a user

Today: the 2.15.1 release job's own install reported `built in 3 minutes 12 seconds` on a GitHub
arm64 runner with every dependency already poured. Pouring the same keg as a bottle took 5s
locally on 2026-09-07.

A user whose macOS matches no bottle tag keeps today's behaviour exactly.
`formula_installer.rb::pour_bottle?` returns false when `formula.bottle_tag?` is false, and the
install builds from source. A user whose tag matches but whose download FAILS does not fall back:
the 2026-09-07 pour attempt against a wrongly named asset died on `curl: (37)`. That asymmetry
drives §3 and §5: a missing bottle degrades to today, a broken bottle breaks the install.

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
  selects a bottle built for an earlier macOS of the same architecture when no exact tag exists. A
  newer bottle never pours on an older macOS.
- **Dependency bottles** (formulae.brew.sh, 2026-09-14): `pydantic` publishes no Intel macOS
  bottle at all; `pango`, `cryptography`, `pillow` and `rpds-py` publish Intel only for `sonoma`.

### The set: `arm64_sequoia` and `arm64_tahoe`

Every Tier 1 arm64 user pours a bottle: macOS 15 gets `arm64_sequoia`, macOS 26 gets
`arm64_tahoe`, and macOS 27 gets one of the two through the older-tag fallback. `arm64_sequoia`
alone would also pour on 26; building `arm64_tahoe` as well means the macOS most users run
receives a bottle that was built and poured on that macOS.

Considered and not chosen: `arm64_tahoe` alone (one job, but macOS 15 keeps building from source)
and `arm64_sequoia` alone (one job, but the pour is only ever proven on macOS 15).

### Intel and macOS 14: excluded

An Intel install compiles `pydantic-core` from Rust whatever this tap publishes, because
homebrew-core ships no Intel bottle for it, and an Intel runner would have to do the same compile
before it could build ours. Homebrew removes Intel support in or after September 2027. arm64
macOS 14 is Tier 3 and its runner image is deprecated.

### Pinned labels

With bottles, a runner's macOS decides the tag it produces. When GitHub repoints `macos-latest` at
macOS 27, an unpinned job would start publishing `arm64_golden_gate` with every step green, and
macOS 26 users would silently lose their bottle. So the build matrix names `macos-15` and
`macos-26`, and each build job asserts that the tag `brew bottle` produced equals the tag its
matrix entry declares. A relabelled image then fails the release, where an unchecked one would
publish two bottles for one macOS and none for the other. The formula and publish jobs pin
`macos-26` for the same reason: they are the ones that run on today's newest image.
`tests/test_release_publish_wiring.py` pins `runs-on: macos-latest` today; that pin moves.

## 2. Workflow shape

### Three jobs, one definition

```
.github/workflows/homebrew.yml   (on: workflow_call)

formula   macos-26, no token
          checkout <ref>; clone + reseat the tap; render; update-python-resources;
          brew audit --strict --online; compute the release tag and root URL;
          upload the formula as an artifact.
          Outputs: tag, root URL, target branch, default branch, tap owner, platform set.

bottle    matrix over the platform set, no token
          checkout <ref>; clone + reseat the tap; install the formula artifact;
          brew install --build-bottle; brew test; brew bottle --json --root-url;
          assert the produced tag is the declared one; merge that one JSON (§4);
          pour proof (§5a); brew linkage (§6); upload bottle + JSON.

publish   macos-26, needs formula + bottle
          download every artifact; assert one JSON per declared tag; clone + reseat the tap;
          install the formula artifact; merge every JSON (§4); brew style;
          mint the App token; upload (§3); URL proof per tag, no token (§5b); push.
```

`release-please.yml`'s `homebrew` job becomes a call: it keeps `needs: [release-please, pypi]` and
its `if:`, and passes the version, `needs.release-please.outputs.sha` and `push_target: default`.
`homebrew-dry-run.yml` keeps its branch refusal and its PyPI version lookup as jobs of their own,
then calls the same workflow with `push_target: auto`.

**Why a reusable workflow.** The graph is three jobs, one of them a matrix, with values crossing
between them. Two hand-kept copies of that graph, one per caller, would be a drift surface of
exactly the kind this channel's history is made of. GitHub's own limits shape the callers: a job
that `uses:` a workflow may carry only `name`, `uses`, `with`, `secrets`, `strategy`, `needs`,
`if`, `concurrency` and `permissions`; caller-level `env` does not propagate; and "the
`GITHUB_TOKEN` permissions passed from the caller workflow can be only downgraded". The App
secrets are passed explicitly by name, never `secrets: inherit`, so the called workflow receives
exactly the two it uses. This is the repo's first reusable workflow.

**Why the formula is filled once.** `brew update-python-resources` resolves against live PyPI
through a one-day cooldown window. Two runners resolving minutes apart can land on different
resource trees, and a publish that merged their bottles would ship a formula whose checksums
belong to builds of a tree other than the one its text declares. Filled once, the formula is an
artifact, and every build and the publish consume the same bytes. The online audit then runs once
per release rather than once per runner.

**Values that cross.** `TARGET_BRANCH`, `DEFAULT_BRANCH` and `TAP_OWNER` cross steps today via
`$GITHUB_ENV`, because each is computed once and must not be re-derived
(`homebrew_verify.sh`'s header). Across jobs they become formula-job outputs, which keeps them one
derivation each. The tap clone-and-reseat, which all three jobs now need, runs from one script
rather than three copies.

**The platform set is declared once.** The formula job emits it; the matrix is generated from it;
the publish job refuses unless the merged block carries exactly that set. A bottle job that
produced nothing must fail the publish, never shrink it. `actions/download-artifact` does not
document what a missing name does, so the refusal checks what arrived rather than trusting the
download step's exit status, and the bottle upload sets `if-no-files-found: error`, which
`release-please.yml`'s `linux-packages` job already uses for the same reason.

### Inside a bottle job

```
brew install --build-bottle <tap>/job-sluice          # was --build-from-source
brew test <tap>/job-sluice                            # the gate on the built keg
brew bottle --json --root-url=<R> <tap>/job-sluice    # R from the formula job (§3)
brew bottle --merge --write --no-commit <json>        # this tag only, for the pour proof
<pour proof>                                          # §5a
```

Measured on 2026-09-07: `brew bottle` refuses a keg installed any other way (`Formula was not
installed with --build-bottle`), and `brew reinstall` rejects the flag. A GitHub-hosted runner
starts with no `job-sluice` installed, so the build is a plain `brew install --build-bottle`; the
uninstall happens later, before the pour.

**`--build-bottle` skips `post_install`, and the pour proof closes that gap.**
`formula_installer.rb` skips it when `build_bottle? || skip_post_install?` and otherwise runs it.
The formula has no `post_install` today. If one is added, the built keg's `brew test` runs without
it, but §5a installs the way a user does, which runs `post_install`, and runs `brew test` again.

## 3. Hosting, tag, upload and filename

**Host: GitHub Releases on `MrReasonable/homebrew-tap`.** A release and its assets need only
`contents: write` on the tap, which the App token the push already mints carries. GitHub Packages
would need a new `packages: write` grant and Homebrew's OCI upload path.

**Root URL:** `https://github.com/MrReasonable/homebrew-tap/releases/download/<tag>`, the shape
`github_releases.rb::GitHubReleases::URL_REGEX` recognises.

**The tag is computed once,** in the formula job, from `VERSION`, `GITHUB_RUN_ID` and
`GITHUB_RUN_ATTEMPT`, and every other job reads it from that job's output. It cannot be computed
per job: `brew bottle --json` bakes the root URL into each JSON, and `dev-cmd/bottle.rb`'s
`merge_json_files` deep-merges the JSONs, so two disagreeing root URLs would be resolved silently
by whichever file was read last.

**Why the tag carries the run and the attempt.** Each dry-run dispatch is its own run, so its
uploads never share a tag with a release's or with another dry run's. A dry run re-run in full is
a new attempt, and gets a new tag. For the release, re-running the whole workflow never reaches
these jobs: release-please sees the release already cut and `release_created` comes back `false`
(recorded in `release-please.yml`'s `build` artifact comment). Its recovery is re-running failed
jobs, which reuses earlier jobs' outputs and artifacts, the behaviour that workflow's `pypi` and
`release-assets` recoveries already depend on.

**Upload rule: skip identical, refuse different.** GitHub refuses an asset whose name already
exists on a release (HTTP 422; "must delete the old file before you can re-upload"). The releases
API reports each asset's `digest` as `sha256:<hex>`, checked against this repo's own v2.15.1
assets. So the upload skips an existing asset whose digest matches the JSON's `sha256`, and
refuses, naming the tag and both digests, when it differs. The cases that matter:

- A bottle job failed; re-run failed jobs. The build re-runs and the publish runs for the first
  time under the same tag. Nothing was uploaded before, so nothing collides.
- The publish failed after uploading; re-run failed jobs. The same artifacts arrive, the digests
  match, and the upload is skipped.
- A bottle already uploaded is rebuilt under the same tag, by re-running a specific job that had
  succeeded. The digest differs and the upload refuses. No asset is ever replaced, so a formula
  already pushed against that asset stays valid.

**Asset name: the JSON's `filename`, copied from its `local_filename`.** `brew bottle` writes the
file as `Bottle::Filename#to_str` (`job-sluice--<v>.<tag>.bottle.tar.gz`, double dash) and records
the download name as `Bottle::Filename#url_encode` (single dash). Both land in the JSON
(`dev-cmd/bottle.rb` writes `"filename"` and `"local_filename"`), and Homebrew's own uploader pairs
them exactly this way: `GitHubReleases#upload_bottles` passes `remote_file: tag_hash["filename"],
local_file: tag_hash["local_filename"]`. The script reads both names out of the JSON and assembles
neither. The 2026-09-07 `curl: (37)` came from a name assumed by hand.

**Why not `brew pr-upload`,** which does that pairing itself: its first act is
`Utils::GemSetup.install_bundler_gems!(groups: ["pr_upload"])`, fetching and running third-party
gems inside the one step that must hold the write token. That is the exposure the verify/push
split exists to prevent. The upload is `gh release create` plus `gh release upload`.

## 4. The `bottle do` block

Written by `brew bottle --merge --write --no-commit`, twice, and shipped once:

- **In each bottle job,** merging that job's one JSON into its local copy of the formula. This copy
  exists only for the pour proof and is never uploaded.
- **In the publish job,** merging every JSON into a fresh copy of the formula artifact. This is
  the text that ships.

`scripts/render_homebrew_formula.py` does not emit the block. The renderer writes a skeleton that
Homebrew's tools complete (`update-python-resources` already edits it in place); a Python copy of
the block's format and placement rules would be a second statement of Homebrew's DSL, free to
drift from the first. The renderer stays pure and changes by a comment only.

The block carries `root_url`, Homebrew's detected `cellar` (`:any`, measured) and one `sha256` per
tag. `rebuild` stays 0: every release renders a fresh formula with no block, and `brew bottle`
increments only a rebuild the formula already declares.

`brew bottle` installs Homebrew's `ast` and `bottle` gem groups at runtime, and `brew style`
installs the `style` group. All of them run before the App token is minted, in both jobs where
they appear.

Because the merge edits the formula after `brew audit` ran, `brew style --formula` checks the
shipped text. RuboCop cops such as `FormulaAudit/ComponentsOrder` are what a misplaced block
trips, and `brew audit` runs the same cops.

## 5. Proof before the release is public

The publication point is the push of `Formula/job-sluice.rb` to the tap's default branch. An
uploaded asset that no formula references reaches no user. So the order is: prove each tag's
bytes pour on their own macOS, mint the token, upload, prove every URL serves those bytes, push.

### 5a. The pour, in each bottle job

After that job's single-tag merge:

1. `brew uninstall job-sluice`.
2. Copy the bottle to the path `brew --cache --bottle-tag=<tag> <tap>/job-sluice` prints.
3. `brew install <tap>/job-sluice`: the command a user runs, against the formula text built from
   the shared artifact, selecting the bottle through the block's tag and checksum.
4. Assert `poured_from_bottle` is true in `brew info --json=v2 --installed` (the field the
   2026-09-07 measurement read).
5. `brew test <tap>/job-sluice`.

Step 4 is what makes this a proof. When no tag matches, `pour_bottle?` returns false, Homebrew
builds from source without complaint, and `brew test` passes; without the assertion that run
could never fail.

**Why the seeded file is used although the asset does not exist yet**, as read from
`download_strategy/curl_download_strategy.rb`: when a cached file exists, a failed URL resolve is
rescued instead of raised, and the size and modification-time freshness checks are skipped for a
`text/` response. GitHub answers a missing release asset with `HTTP/2 404`,
`content-type: text/plain` (measured 2026-09-14). Either way the cached file is kept and its
checksum is still verified. This is read, not run; §7.

`brew install ./<bottle>.tar.gz` is not used. `env_config.rb::forbid_packages_from_paths?` refuses
path installs by default, and a path install bypasses the formula's bottle block, so it would
prove less.

The pour runs `brew test`, which imports the vendored tree, and it runs in a job that never holds
a token.

### 5b. Every URL, in the publish job after the upload

In its own step, with no token in its environment, for each declared tag:

1. Remove any cached copy.
2. `brew fetch --force --bottle-tag=<tag> <tap>/job-sluice`. Homebrew builds the URL from the
   merged formula, downloads it unauthenticated, and verifies the `sha256` in the block.

A wrong asset name, a wrong or missing release, a disagreeing root URL, and a byte mismatch all
fail here, while no user can yet reach the formula. The 2026-09-07 failure is this class.

What 5a and 5b together do not do is pour the merged two-tag text. Each tag's `root_url`,
`cellar` and `sha256` are the values its own runner poured, because both merges read the same
JSON, and 5b checks the merged text's URLs and checksums against the served bytes.

### 5c. The push

`homebrew_push.sh` is unchanged in shape: commit and push. The committed formula now carries the
block.

### Where the token is

| Job | Step | Token in env | Third-party code |
| --- | --- | --- | --- |
| formula | render, resources, audit | no | yes |
| bottle | build, test, bottle, merge, pour, test | no | yes |
| publish | download, merge, style | no | yes (gems) |
| publish | mint App token | — | no |
| publish | upload | yes | no |
| publish | URL proof (`brew fetch`) | no | no |
| publish | push | yes | no |

The dry run runs the same jobs. What differs stays confined to `push_target`, as today.

## 6. Failure modes, from the user's side

| Failure | Caught at | What a user sees |
| --- | --- | --- |
| resources, audit, build or built-keg `brew test` fails | formula or bottle | nothing: no upload, no push |
| a bottle does not pour, or the poured keg fails `brew test` | 5a | nothing |
| a runner produced the wrong tag | bottle job's tag assertion | nothing |
| a bottle job produced no artifact | publish job's tag-set assertion | nothing |
| an upload fails, or a name, release, root URL or byte mismatch | 5b | nothing; possibly an orphan asset |
| a rebuilt bottle meets an already-uploaded asset | upload's digest refusal | nothing; recovery is a new release |
| push rejected | push | nothing; orphan assets, and re-running the failed job reuses them |
| homebrew-core later changes a library the keg links by path | **nothing catches it** | a failing pour on that tag until the next release; `brew install --build-from-source` works around it |

The last row is the one risk bottling adds. A source build links against whatever homebrew-core
ships on the day of the install; a bottle's links are fixed on release day. Each bottle job runs
`brew linkage` on its built keg, so which homebrew-core libraries the vendored C extensions link,
if any beyond the interpreter, is measured rather than assumed. Every release re-bottles against
current homebrew-core.

A macOS 27 install pours one of the two bottles through the older-tag fallback. Which one depends
on the tag order `brew bottle --merge` writes, both are proven on their own macOS, and neither is
proven on 27, since no generally available runner has it. That is a residual, stated here.

## 7. Not yet executed

The first dry run proves these before a release depends on them:

- A reusable workflow called with explicit secrets, minting the App token inside the called
  workflow.
- A matrix generated from a job output, and the publish job's tag-set refusal.
- `brew install` using the seeded cache file while the asset is still missing.
- `brew bottle --merge --write --no-commit` with two JSONs, placing the block where `brew style`
  accepts it.
- `brew fetch --force --bottle-tag` for a tag other than the runner's own, from a public release
  asset, unauthenticated, with checksum verification.
- The digest comparison in the upload step against a real asset.
- The whole chain on `macos-15`; the 2026-09-07 measurement ran on a local macOS 26 machine.
- `brew linkage` on both runners (§6).
- The wall-clock time the release gains.

Partial re-runs of this graph are proven by the first release that needs one; the dry run can
exercise one deliberately.

## 8. Tests that change

`tests/test_release_publish_wiring.py` reads `release-please.yml`'s `homebrew` job and
`homebrew-dry-run.yml`'s `dry-run` job by name today. Its pins move to `homebrew.yml`'s jobs and
gain: both callers pass the right `push_target`; secrets are passed by name; the token appears
only in the publish job's upload and push steps; build runner labels are pinned; the bottle upload
sets `if-no-files-found: error`; the install-order pin moves to `--build-bottle` and extends
through bottle, merge, pour and test; any new script joins `_MACOS_SHELL_SCRIPTS`, the bash 3.2
sweep. The plan enumerates the rows; this document states no count of them.

This is the repo's first reusable workflow and it crosses job boundaries that were step
boundaries, so the plan's review includes `sluice-architect`.

## Out of scope

- Intel and macOS 14 bottles (§1).
- A post-release Homebrew install check in `post-release.yml`: §5b fetches the exact URLs and
  bytes users receive, minutes before the formula that points at them is pushed.
- `brew test-bot` or any workflow inside the tap: `homebrew_verify.sh`'s header records why the tap
  carries none (a second automated writer of a machine-owned formula, and an App token that
  cannot push `.github/workflows/`).
- Deleting the orphan releases a dry run or a failed publish leaves behind. Nothing reads the
  tap's Releases page.
