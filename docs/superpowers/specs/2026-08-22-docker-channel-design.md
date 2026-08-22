# Docker channel (PR 4 of #104) design

Status: design, 2026-08-22.

This is PR 4 of the 7-PR packaging sequence locked in
`2026-08-09-packaging-distribution-sequencing-design.md`. That spec fixes PR 4's scope to a
`Dockerfile`, a `.dockerignore` and a `docker` job, and names its one hard invariant; #104 itself
locks the mechanism from the PR #103 planning pass. This document is the PR-4-specific decisions
those two leave open: what the image is called, what it contains, how the wheel reaches it, where
provenance is signed, and what proves the image works before a tag is public.

PR 3 (PyPI) merged and released `job-sluice` 1.0.0 on 2026-08-22. Docker, deb/rpm and Homebrew
ship in **1.1.0**, per that spec's Release-scope decision.

## Revised after a plan-review round, 2026-08-22

Reviewed by the five-agent roster before the remaining implementation: **16 findings -- 1
Critical, 5 High, 9 Medium, 1 Low.** What changed, recorded because several were errors of a KIND
rather than of detail:

- **1 Critical.** The compose file now PINS `VAULT_DIR`. Nothing in this design refused a wrong
  vault path, and the container's persistent-`seen.db`/ephemeral-vault split turned that into the
  #81 harm by a route `core/paths.py` cannot guard. See the compose section.
- **The `attest-image` justification was FALSE and is rewritten.** It claimed `docker build` is
  the same arbitrary-code-execution surface as `python -m build`. It is not -- a BuildKit `RUN`
  step has no `ACTIONS_ID_TOKEN_REQUEST_TOKEN` in its environment. The decision survives on a
  different, checkable basis. This is the same "verified fact misapplied" shape PR 3's review
  recorded, committed again under the same "Verified" heading, which is worth stating plainly.
- **The hard invariant's guard would have been red on day one.** Its clean fixture -- the real
  Dockerfile -- contains the forbidden phrase in the comment that explains the invariant. The
  guard strips comments; the tempting narrowing would have hidden the problem instead.
- **`--version` + `import weasyprint` was not enough.** `pip install` only warns on an unknown
  extra, so three of the four could have shipped missing with nothing red.
- **Four reviewers independently flagged a scheme-less `CAMOFOX_URL`** in the prose. The strongest
  corroboration this round produced, on the smallest defect. A name-only env-var test could never
  have caught it, so the test now pins the value.
- **One "Verified" bullet asserted a measurement only partly run** (the sdist check, claimed with
  `docker-compose.yml` tracked when the file did not yet exist). Re-run properly rather than
  reworded.
- **Three release-path-only defects** in the job definitions (`path: dist/`, the job-level
  `outputs:` mapping, `subject-name`), each of which would have failed only after the tag was
  public.

## Verified before designing, not assumed

Each was measured on 2026-08-22 against a live source or a real build, because each one, if
wrong, invalidates a section below rather than a sentence.

- **A Dockerfile `FROM` rejects a trailing comment.** `FROM <ref>  # 3.13-slim` is a parse error
  ("FROM requires either one or three arguments"), not a lint warning. So the
  `uses: ...@<sha>  # vX.Y.Z` idiom every workflow file here uses does **not** transfer to a base
  image. `name:tag@digest` is valid and records the same fact inside the reference.
- **`*` + `!dist/*.whl` is sufficient on its own.** Measured by building a real context
  containing `dist/*.whl`, `dist/*.tar.gz`, `secret.yaml`, `.git/config` and `sub/nested.txt`:
  the only file `COPY . /ctx` saw was the wheel. No `!dist` / `dist/*` re-include pair is needed,
  which an earlier draft of this design assumed it would be.
- **`python:3.13-slim` resolves to index digest
  `sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`**, whose manifest
  list carries `linux/amd64` and `linux/arm64/v8` among others. It is the multi-arch INDEX
  digest, not a per-platform one -- which is what lets a single `FROM` serve both target
  platforms.
- **`pip install "<path-to-wheel>[extras]"` works.** The image installs
  `render,google,mcp,completion` off the local wheel; `weasyprint 69.0`, `mcp 2.0.0`,
  `googleapiclient`, `argcomplete` and `jinja2` all import in the built image.
- **`job-sluice doctor --offline` exits 1 in any container, and always will.**
  `DoctorReport.exit_code(strict=False)` returns 1 iff any check is DEAD, and `claude-max` is
  DEAD whenever the `claude` CLI is not on PATH -- which it never is in this image. A bare vault
  is dead too. This is correct behaviour, not a defect, and it means **the CI smoke must not
  assert exit 0 on `doctor`**. See "The smoke assertions" below.
- **`tests/test_no_leaked_files.py` fires on a Dockerfile comment.** Confirmed by running it, not
  by reading the regex: an explanatory comment containing `useradd -m -d /home/<name>` spelled
  with a real name was caught on two lines. `_WIDE_HOME_PATH_RE` excludes `<` and `>`, so the
  angle-bracket placeholder form is exempt and a concrete one is not.
- **New top-level files do not disturb the sdist.** `tests/test_packaging.py`'s
  `SDIST_ROOT_MEMBERS` is an exact set equality, and `_tracked_files` drives it off
  `git ls-files` -- so the measurement is only worth anything with the files actually TRACKED.
  Re-run with all three of `Dockerfile`, `.dockerignore` and `docker-compose.yml` staged, the
  packaging suite stays green. (An earlier draft of this bullet claimed that measurement while
  `docker-compose.yml` did not yet exist. A reviewer caught it. Recorded because "I asserted a
  measurement I had only partly run" is this repo's most-repeated review finding, and the
  correction is cheap only when someone checks.) setuptools' default sdist rules
  do not sweep arbitrary root files -- the same reason `package.json` and `run_tests.sh` are
  tracked yet absent. **Never add a `graft`/`include` to `MANIFEST.in` for them.**
- **`googleapis/release-please-action` v5 exposes `version`, `major`, `minor` and `patch`**, flat
  and un-prefixed for a root single-package config -- the same property `release_created`,
  `sha` and `tag_name` already rely on. Read from the action's own README's Outputs table.
- **Both target platforms build.** `docker buildx build --platform linux/amd64,linux/arm64
  --output type=cacheonly` completes, so multi-arch is proven without publishing anything.
- **`VAULT_DIR` outranks a configured `vault_dir`.** `sluice/stores/vault.py:_make` is
  `Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None, ...)`. Read from the factory,
  not inferred: it is what lets the compose file immunise the container against a stale absolute
  host path left in a bind-mounted `config.yaml`, by construction rather than by convention.
- **The tolerant invariant pattern matches this Dockerfile's own COMMENT.** Measured: 1 hit over
  the raw source (line 39, the comment explaining the invariant), 0 after comment lines are
  stripped. The clean fixture and the forbidden pattern collide, so the guard must strip comments
  -- and must NOT be narrowed to anchor on `RUN`, which would trade away the tolerance the
  sequencing spec mandates while turning the fixture green and hiding the trade.
- **`docker/build-push-action` exposes a `digest` step output**, read from its own `action.yml`.
  A job-level `outputs:` mapping is still required to cross the job boundary into `attest-image`;
  `id: push` alone does not.

## Scope

In: `Dockerfile`; `.dockerignore`; `docker-compose.yml`; two new jobs in
`.github/workflows/release-please.yml` (`docker`, `attest-image`) plus three new outputs on the
existing `release-please` job; a new `docker` job in `.github/workflows/ci.yml` and its
`ci-success` conjunct; a `docker` ecosystem in `.github/dependabot.yml`; a new
`tests/test_docker_channel.py`; extensions to `tests/test_release_publish_wiring.py` and
`tests/test_ci_wiring.py`.

Three of those exceed the sequencing table's row 4, each for a stated reason:

- **`docker-compose.yml`** -- requested directly by the repo owner. It is not the thing the
  sequencing spec puts out of scope; see the boundary note below.
- **The `ci.yml` `docker` job** -- see "Why a pre-release build job exists".
- **The Dependabot `docker` ecosystem** -- a digest pin with no updater freezes permanently and
  accrues CVEs while *looking* well-secured. That is the argument `dependabot.yml`'s own header
  already makes for action pins (#3); adding the pin without the updater would make it a
  liability rather than a control.

Out, each with its reason:

- **The `claude-cli` sidecar.** The sequencing spec (lines 68-76) puts it outside #104 entirely
  -- a second container, `sshd`, a credential volume and an interactive login flow, described
  there as unproven and needing to be "built and logged into for real before being documented as
  supported". It gets its own follow-up issue, filed once this PR ships. **A compose file for the
  `job-sluice` service itself is a different thing and is not covered by that boundary**; the
  spec scopes out one *service*, not the *file*.
- **`README.md`.** Lines 114 and 141 say there is no Docker image. That stays TRUE until 1.1.0
  publishes one. Editing them here would assert something unverified -- the exact principle the
  sequencing spec invokes for install docs. The README install section and `docs/INSTALL.md` are
  PR 7's.
- **An HTTP transport for `mcp serve`.** A feature change to `sluice/mcpserver.py`, not packaging.

## The image

`ghcr.io/mrreasonable/job-sluice`.

#104's job table says `ghcr.io/mrreasonable/sluice`. That predates the `job-sluice` rename
settling, and the reason PyPI forced `job-sluice` -- a 2015 squat -- has no analogue on GHCR,
which namespaces by owner. CLAUDE.md already treats distribution name, import package and
console-script name as three independent things; the image name is a fourth, and choosing it
freely is the point of that rule rather than an exception to it. Everything a user types is
`job-sluice`, including this image's own ENTRYPOINT, so the image matches.

**Contents: `render,google,mcp,completion` -- all four extras.** `render` is the image's reason
to exist: WeasyPrint links natively against cairo/pango/gdk-pixbuf, `pip install` can never
supply them (README.md:345, docs/TROUBLESHOOTING.md:11 both say so), and this is the one channel
that solves it once for everybody. `google` makes `track` usable, without which the image is a
CLI missing a pipeline stage. `mcp` makes `job-sluice mcp serve` a first-class container use.
Adding `mcp` to the image does not weaken CLAUDE.md's rule that nothing outside `mcp serve` may
cause it to load: that rule is about the **lazy import** inside `build_server()`, which is
untouched. The measured cost of all four is a 565MB image.

**The wheel is COPYed from the build context's `dist/`, never installed from PyPI.** This is the
sequencing spec's one hard invariant for this PR: `pip install job-sluice` races the `pypi` job
in the same release and would either fail or silently ship the previous version under this
release's tag. The release job downloads the `build` job's `dist` artifact -- the exact bytes
that `attest` signs and `pypi` publishes -- into the context.

The install step **asserts the wheel count is exactly one** before installing. A glob matching
nothing does not expand to nothing in POSIX sh; it expands to its own literal text, so an
unguarded loop runs once over a nonexistent filename and "proves" there was one wheel. That is
the identical present-and-inert failure `shopt -s nullglob` exists to close in `testpypi.yml`,
in a shell with no nullglob to switch on.

**Paths.** `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` are set to absolute paths
under `/app`, because `sluice/core/paths.py` **ignores a relative XDG value** -- it warns and
falls back, so a relative value would silently relocate every piece of state with only a log line
to say so. `WORKDIR` is `/work`, kept distinct from that state because this codebase deliberately
leaves the five CV working directories, the render script and `DEFAULT_VAULT` cwd-relative
(`docs/CONFIGURATION.md`: "a workspace you're standing in, not per-system state").

The XDG directories are pre-created and owned by the runtime user so that a fresh Docker **named
volume** mounted at one inherits that ownership instead of being created root-owned. Docker seeds
an empty named volume from the image's directory at that path, ownership included. There is no
equivalent for a bind mount, whose ownership comes from the host -- hence the uid note in
`docker-compose.yml`.

**The runtime user's home is `/app`, not under the usual home root**, because
`tests/test_no_leaked_files.py` sweeps every tracked file for such paths with only three
allow-listed literals, none usable here. This was not deduced -- the guard caught this very
Dockerfile's explanatory comment during development.

## `docker-compose.yml`

Two services over one YAML-anchored block.

### `VAULT_DIR` is PINNED, and it is the load-bearing line in the file

**This closes a Critical found in review, by a route none of the codebase's own refusals can
cover.** The chain was confirmed by reading each link, not argued:

1. The config directory is bind-mounted from the host, so a `job-sluice init` run on the host
   leaves an absolute HOST path in `vault_dir` -- meaningless inside the container.
2. `Vault` never checks that its directory exists, and `upsert`'s create arm runs
   `os.makedirs(self.leads_dir, exist_ok=True)` (`core/vault.py:2299`). A wrong path is therefore
   CREATED, in the container's ephemeral layer, rather than refused.
3. Leads land there as `created`, and `created` is on `ingest/sink.py`'s allowlist
   (`sink.py:50`), so they are recorded in `seen.db` -- which is a PERSISTENT named volume.
4. Remove the container: the notes are gone, `seen.db` still remembers them, and `seen.db` has no
   removal path. Those leads are suppressed forever, with no note anywhere, after a run that
   printed a plausible `created: N`.

That is the #81 harm, reached by a route `core/paths.py` structurally cannot guard: the vault
deliberately does not relocate to an XDG root, so it is not in the `_LEGACY` table the relocation
refusal consults. Setting `VAULT_DIR` to the mount target fixes it BY CONSTRUCTION, because
`stores/vault.py:_make` puts the env var ahead of `config.vault_dir`.

### Credentials pass by NAME, and are never enumerated here

`env_file:` with `required: false`, so an absent `.env` is not a startup error. The file
deliberately does NOT list `ANTHROPIC_API_KEY`/`DEEPSEEK_API_KEY`/`OPENAI_API_KEY`: which ones
matter depends on the configured `primary_backend`/`fallback_backend`, and an enumeration here
would be a second source of truth that goes stale the moment a provider is added to
`core/app.py`'s `_PROVIDER_ENV`. No secret value appears in the file, only the mechanism.

### The services


- **`job-sluice`** -- bind-mounts the vault and the config directory (user-owned, and the user
  must be able to see them); **named volumes** for state and cache (tool-owned). The state mount
  carries a comment about why it matters: losing `seen.db` makes every already-known lead read as
  unseen, which is the #81 harm the whole dedup store is built around. `CAMOFOX_URL` defaults to
  `http://host.docker.internal:9377` -- **with the scheme**, because `core/camofox.py` concatenates
  the value straight into a `urllib` request and a bare `host:port` is parsed as a scheme and
  raises `unknown url type` -- plus `extra_hosts: host-gateway` so Linux behaves like macOS --
  Camofox is an external HTTP service this repo does not bundle and which lives on the host.
- **`mcp`** -- `profiles: [mcp]`, `stdin_open: true`, **no `ports:`**, run as
  `docker compose run --rm -T mcp`.

  **`mcp serve` is stdio-only.** `cli.py:1702` registers it as "run the MCP server (stdio
  transport)" and `mcpserver.py:3` says "over stdio"; there is no HTTP or SSE transport in the
  module. A service publishing a port would be a file that lies about its own mechanism. What
  compose legitimately adds here is the volume wiring, which is identical to the main service's
  and which an MCP client would otherwise have to reproduce by hand in a long `docker run -i`.

Both reference an image that does not exist until 1.1.0 publishes it. The file is inert until
then, exactly like the `docker` job beside it -- stated here rather than left to be discovered.

**A host install's existing `seen.db` is NOT migrated**, and the refusal that exists for exactly
this cannot see it: `core/paths.py` compares paths inside the container and never the host's. The
first container run therefore starts with an empty dedup set. With the vault correctly mounted --
which the `VAULT_DIR` pin above guarantees -- that is a re-scrape rather than a re-application:
`upsert` finds the existing notes and returns `updated`, so the cost is work, not duplicate
applications under the user's name. The compose file says so at the mount.

## Job definitions

### `release-please` (existing job -- gains three more outputs)

`version`, `major` and `minor`, in the same one-line shape `tag_name` was added in. `docker` reads
them to build the `X.Y.Z` / `X.Y` / `latest` tag set. Nothing else in the workflow reads them.

Computing the tags from outputs rather than from `docker/metadata-action` keeps one fewer action
in a workflow whose every pin is a supply-chain surface, and puts the values in a `with:` input
rather than a `run:` body, which is what zizmor's template-injection audit requires.

### `docker`

`needs: [release-please, build]`, gated
`if: success() && needs.release-please.outputs.release_created == 'true'`, permissions
**`contents: read` + `packages: write`** and nothing else.

`needs` names `release-please` directly even though `build` already depends on it, for the reason
`attest` and `pypi` do: reading `needs.release-please.outputs.*` requires a direct dependency
edge, not a transitive one.

It also declares a job-level `outputs:` mapping, `digest: ${{ steps.push.outputs.digest }}`.
**`id: push` alone is not enough** -- step outputs do not cross a job boundary, so without the
mapping `needs.docker.outputs.digest` is empty and `attest-image` would sign nothing while
reporting success. `digest` is a real output of `docker/build-push-action`, read from its
`action.yml`.

Steps: checkout at `needs.release-please.outputs.sha` (`persist-credentials: false`) -- the
Dockerfile itself must come from the commit that was tagged, not from whatever triggered the run
-- then `download-artifact` with **`name: dist` AND `path: dist/`**, `setup-qemu-action`,
`setup-buildx-action`, `login-action` against `ghcr.io` with the built-in `GITHUB_TOKEN`, and
`build-push-action` with `platforms: linux/amd64,linux/arm64`, the three tags, and `id: push`.

`path: dist/` is load-bearing rather than stylistic consistency with the other four consumers:
the Dockerfile's `COPY dist/*.whl` and `.dockerignore`'s `!dist/*.whl` both name that exact
directory, so without it the artifact unpacks into the workspace root where the build context
cannot see it. This job is the fifth consumer of that artifact and joins the test that pins all
five agree on its name.

Note what these two corrections have in common: **both fail only on the release path**, which is
the one path nothing exercises until a tag is already public. That is precisely the risk the
`ci.yml` job below exists to reduce, and precisely the part of it that job does NOT cover.

The wheel is `py3-none-any`, so multi-arch costs only the base image and the apt layer; nothing
is cross-compiled. QEMU emulation makes the arm64 apt layer slow, which is acceptable in a job
that runs once per release.

### `attest-image`

`needs: [release-please, docker]`, permissions `id-token: write` + `attestations: write` and
**no `packages:` key**, attesting `needs.docker.outputs.digest` via
`actions/attest-build-provenance` with **both `subject-name` and `subject-digest`** -- an OCI
subject is not identified by a digest alone, and the name is what `gh attestation verify
oci://...` matches against.

**It is a separate job on purpose, and the reason matters more than the decision** -- an earlier
draft of this spec justified it by citing the existing `attest` job's principle and claiming
`docker build` is "the same arbitrary-code-execution surface" as `python -m build`. **That claim is
false, and it is recorded here rather than quietly deleted** because it is the same failure PR 3's
review named: a verified fact misapplied to close an option space it does not reach, under a
"Verified" heading that makes it harder to spot. `python -m build --no-isolation` runs the build
backend in the RUNNER's own process, where `ACTIONS_ID_TOKEN_REQUEST_TOKEN` and `_URL` are in
reach -- which is precisely what makes withholding `id-token: write` from `build` load-bearing. A
BuildKit `RUN` step has none of that environment, so a malicious dependency during the image's
`pip install` cannot mint an OIDC token. The principle does not transfer.

The split stands on a different and checkable basis. Every write-holding job in this workflow holds
exactly one kind of write -- `pypi` an OIDC identity, `release-assets` a contents write, `attest` a
signing pair and nothing else -- and folding attestation into `docker` would, for the first time in
this file, put a REGISTRY CREDENTIAL and an OIDC IDENTITY in the same job. That is a property a
reader can verify against the file in a few seconds, unlike a claim about what BuildKit exposes.
The attestation is repo-side (Sigstore, verifiable with `gh attestation verify oci://...`), so it
needs no registry write and this job holds none.

`attest-image` names `release-please` in `needs` because it reads
`needs.release-please.outputs.release_created` in its own `if:` gate -- the direct-edge rule stated
above. The gate is belt-and-braces (a skipped `docker` already skips this job), spelled out for the
same reason `attest` and `pypi` spell theirs out.

## Why a pre-release build job exists

Recorded from PR 3, and the reason that PR's TestPyPI dispatch was genuinely load-bearing rather
than ceremonial: **`build` and `twine check --strict` had never executed in this repo's CI**
before 1.0.0, because both were gated on `release_created`. Their first run was after the tag was
public. The `docker` job has exactly that shape, and the recorded consequence of getting it wrong
is a publicly visible partially-failed release.

`ci.yml` therefore gains a `docker` job: build single-arch, no push, then smoke-run. It runs on
every pull request with **no `paths:` filter** -- `ci-success` is the required status check for
the `qa-gates` ruleset, and a path-filtered job makes that check dishonest on the runs where it
does not fire.

Its wheel comes from a plain `pip install build` + `python -m build --wheel`, deliberately **not**
the hash-pinned `.github/build-requirements.txt` the release build uses. The reason is stated in
the job so it does not read as unexplained drift: this wheel is a smoke fixture that is thrown
away at the end of the job, not an artefact anyone publishes, and hash-pinning it would couple a
pull-request check to the release supply chain for no property gained. It also avoids making a
third copy of the release build's command set, which would reopen a composite-action extraction
the PyPI PR deliberately deferred.

### The smoke assertions

1. `job-sluice --version` -- exits 0.
2. `--entrypoint python ... -c "import weasyprint, jinja2, googleapiclient, mcp, argcomplete"` --
   **the highest-value one, and it covers ALL FOUR extras rather than just `render`.** Two reasons.
   Missing cairo/pango is precisely the failure this image exists to prevent and is completely
   invisible to `--version`, which passes on an image with no system libraries at all. And
   **`pip install` only WARNS on an unknown extra** -- it does not fail -- so a typo'd or renamed
   extra would silently ship an image missing `google`, `mcp` or `completion` entirely, with
   nothing red anywhere. A `render`-only smoke would not see that.
3. `job-sluice doctor --offline` -- it exits 1 in any container by design (measured above), so the
   assertion is on its OUTPUT, not its status. But "no traceback" alone is a **bare negative that
   passes against a container which prints nothing, or dies before `doctor` runs at all**, so the
   check is positive-and-negative: the output must CONTAIN a stable row the report always emits,
   AND must not contain a traceback. The exit status is explicitly not asserted in either
   direction -- asserting 0 would fail every run, and asserting 1 would pin a degraded state as
   correct and start lying the moment a check changed.

   The step must not be written with a trailing `|| true`, which is the natural way to stop a
   non-zero exit failing the job and would green-light a wholly broken image. Capture the output,
   let the command's status be ignored explicitly, then assert on what was captured.

## Testing

Extends the two existing packaging-guard idioms; no third one is invented.

**`tests/test_release_publish_wiring.py`** (text matching, no YAML parse). `_RELEASE_PLEASE_JOBS`
is a **list equality**, so `docker` and `attest-image` go in at their file-order position, and --
per that constant's own `_ROSTER_MESSAGE` -- each also gets its own `_permissions_block(...) ==`
equality pin, because extending the list alone restores the blind spot it exists to close. Gate
and `needs:` pins take the established `re.search` + `.strip() ==` form. `docker` joins
`test_every_job_agrees_on_the_artifact_name` as the fifth `dist` consumer.

**Two assertion MESSAGES must be reworded in this PR.** Lines 434-436 and 480-481 both read
"`packages: write` ... no job here holds it today". A GHCR-pushing job falsifies that sentence.
The standing rule is to grep the CLAIM, not just the code that changed -- a reason stated in a
comment goes stale silently.

**`tests/test_docker_channel.py`** (new, assert-then-falsify):

- **The hard invariant**, in the form the sequencing spec (lines 143-155) requires: a *tolerant*
  match for a `pip3? install` line naming the bare package `job-sluice` with no local wheel/dist
  path, regardless of interposed flags or quoting -- never a fixed contiguous literal, which
  `pip install --no-cache-dir job-sluice` silently defeats.

  **It matches over COMMENT-STRIPPED source, and that is not a detail.** Measured: the pattern
  scores exactly one hit on the real Dockerfile -- line 39, the comment that EXPLAINS the
  invariant -- and zero once comment lines are removed. So the clean fixture and the forbidden
  pattern collide, and the guard would have been red on its first run. The repair must be
  comment-stripping and NOT anchoring on `RUN` or excluding a leading `#` at the pattern level:
  those turn the fixture green while quietly discarding the tolerance the sequencing spec
  mandates, which is the worse failure because it looks identical from the outside.

  Three evasion fixtures, each asserted to fire: an interposed flag
  (`pip install --no-cache-dir job-sluice`), `python -m pip install 'job-sluice[render]'`, and
  **the line-39 comment text moved onto a `RUN` line** -- the third exists specifically so the
  comment-stripping cannot be over-widened into stripping real content.
- The Dockerfile installs from a local wheel path, with a non-vacuity anchor.
- A drift pin: the image reference in `docker-compose.yml` equals the one the `docker` job pushes.
  **Both extractions are asserted non-empty before being compared** -- the recorded failure of
  this exact idiom is a pin that passes because both sides failed and `None == None`.
- **Every extra the Dockerfile installs**, extracted from a backslash-continued multi-line `RUN`,
  is pinned as an **EQUALITY against the expected set** and then checked as a subset of
  `pyproject.toml`'s `[project.optional-dependencies]` keys. The equality is what supplies the
  non-vacuity: a subset check alone is satisfied by an empty extraction (`all([])` is `True`),
  and this is the single hardest shape in the file to extract from.
- Every environment variable `docker-compose.yml` sets appears as a quoted literal somewhere under
  `sluice/`, with a non-empty anchor. **The claim is deliberately no stronger than that.** A
  "sluice actually READS this variable" sweep cannot be honest here: `core/app.py`'s
  `_PROVIDER_ENV` reads its keys through a VARIABLE (`os.environ.get(key_var, "")`), so a
  literal-based derivation cannot see them, and `DOSSIER_DIR` arrives through an `env_var=`
  keyword rather than an `os.environ` call. A literal-presence check still catches the failure
  that actually happens -- a misspelled name like `SLUICE_CONFIG_FILE` -- without overstating what
  it proves.
- **No mount source in `docker-compose.yml` is an absolute or `~`-rooted path.** This closes the
  one real gap in the existing neutrality sweep: `tests/test_no_leaked_files.py` covers every
  tracked file, so a `/Users/...` or `/home/...` vault path in compose is already caught, but its
  `_WIDE_HOME_PATH_RE` is anchored on those two roots and so cannot see `~/vault` or a
  drive-lettered path.
- **`CAMOFOX_URL`'s value carries a scheme, and its port matches `core/camofox.py`'s
  `_DEFAULT_URL`.** A name-only env-var check cannot catch a wrong VALUE, which is exactly how
  the scheme-less spelling survived four reviewers' first pass in the prose.
- The base image is digest-pinned; `.dockerignore`'s first effective rule denies everything;
  `dependabot.yml` declares a `docker` ecosystem.

**`tests/test_ci_wiring.py`**: the `docker` job runs a real `docker build`, carries the
all-four-extras import smoke, and **does NOT reference `.github/build-requirements.txt`**. That
last one looks odd and is the point: the CI job's divergence from the hash-pinned release build is
deliberate, but nothing else pins it, and "make CI consistent with the release build" is the most
likely review suggestion this job will ever attract. Taking that suggestion silently creates a
third copy of the release build SEQUENCE and fires the composite-action extraction the PyPI PR
deferred. The assertion turns a silent drift into a failing test that names the reason.

`test_every_needed_job_is_checked_in_the_success_chain` already enforces the `ci-success`
`needs:`/conjunct pair in both directions once the job is added. **Falsification is per assertion, not one blanket rule.** Positive assertions are witnessed by
MOVING or DELETING; the negative ones -- pinning the ABSENCE of a PyPI install, of a `packages:`
key on `attest-image` -- leave nothing to delete, so an ADD-mutant is the correct witness there
and is not the equivalent-mutant trap. Run `compileall --invalidation-mode checked-hash` once
first, commit before witnessing, and confirm each mutant is killed by the NAMED test by node id:
a kill by a pre-existing sibling witnesses nothing about a new test.

## Manual prerequisites (repo owner only)

**After this PR merges and the first image pushes at 1.1.0:** set the GHCR package visibility to
public. The sequencing spec (line 122) calls this the one genuinely benign case -- a late toggle
only delays public discovery of an already-published image; nothing fails. Unlike PyPI's trusted
publisher, there is nothing to configure *before* the first release: `packages: write` on the
built-in `GITHUB_TOKEN` is sufficient to create the package.

## Risks

- **The GHCR push is unproven until 1.1.0.** The CI job proves the image builds and runs; it does
  not prove registry login, multi-arch push, or attestation against a real digest. This is the
  same residual PR 3 carried, minus the irreversibility: a bad image can be overwritten, a PyPI
  upload cannot.
- **`latest` moves on every release.** Correct for a single-branch release-please repo, and
  wrong the moment a patch is ever cut for an older minor. Nothing in this repo does that today.
- **The `ci.yml` job adds minutes to every pull request.** Accepted deliberately; the alternative
  is discovering a broken Dockerfile after the tag is public.
- **QEMU arm64 emulation is slow.** Acceptable in a release-only job. Native arm runners are the
  fallback if it bites, and are deliberately not adopted pre-emptively.

## Definition of done

Suite green, `ruff` clean, `zizmor --offline --strict-collection` clean, a real local
`docker build` + the three smoke assertions passing, both platforms proven via
`--output type=cacheonly`, a witness run per new assertion, `/review-plan` rounds addressed, and
`/review-pr` run before the PR is marked ready.
