# Homebrew tap channel — design (#104 PR 6 of 7)

**Status:** design, revised after `/review-plan` round 1 (5 reviewers, 39 findings: 0 Critical,
11 High, 17 Medium, 11 Low). **Date:** 2026-08-23. **Issue:** #104.

## Goal

Ship `brew install MrReasonable/tap/job-sluice` as the fourth publish channel, bumped
automatically by the existing release workflow, and prove — by execution, in CI — that it
resolves WeasyPrint's native libraries on macOS without the `DYLD_FALLBACK_LIBRARY_PATH`
workaround every other install path needs.

## Why this channel exists, measured rather than assumed

#104 claims Homebrew is "the only channel that resolves cairo/pango natively on macOS,
killing the `DYLD_FALLBACK_LIBRARY_PATH` workaround". `README.md` separately records, as a
measurement, that with cairo/pango/gdk-pixbuf installed **via Homebrew** `import weasyprint`
still failed until that variable was set. Those read as contradictory, so the claim was
measured before any of this was designed.

Three cells, `DYLD_FALLBACK_LIBRARY_PATH` removed from the environment with `env -u`, arm64
macOS, WeasyPrint 69.0 in all three:

| Interpreter | `DYLD_FALLBACK_LIBRARY_PATH` | Result |
| --- | --- | --- |
| Homebrew `python@3.13` | unset | `find_library('gobject-2.0')` resolved under the Homebrew prefix; a real PDF was written (3528 bytes, `%PDF-` magic) |
| non-Homebrew CPython 3.14 (version-manager build) | unset | **failed** — "WeasyPrint could not import some external libraries" |
| the same non-Homebrew CPython | set to the Homebrew prefix's `lib` | imported; PDF written |

**Both claims are true, and the variable neither of them names is the interpreter.**
Homebrew's CPython formula patches the standard library at build time:

```ruby
# Formula/p/python@3.14.rb, homebrew-core
inreplace "./Lib/ctypes/macholib/dyld.py" do |f|
  f.gsub! "DEFAULT_LIBRARY_FALLBACK = [",
          "DEFAULT_LIBRARY_FALLBACK = [ '#{HOMEBREW_PREFIX}/lib', ..."
```

Verified against the installed artefact, not only the formula source: every Homebrew-built
CPython on the test machine carries the Homebrew prefix at the head of
`ctypes.macholib.dyld.DEFAULT_LIBRARY_FALLBACK`; the version-manager build has only
`~/lib`, `/usr/local/lib`, `/lib` and therefore cannot see a Homebrew-installed dylib at all.

Two consequences bind this design:

1. **The payoff is real, and its mechanism is `depends_on "python@3.x"`** — nothing else.
   The earlier working hypothesis, that the formula would need `bin.env_script_all_files`
   to export the loader path, is **false**: homebrew-core's own `weasyprint.rb` is
   `def install; virtualenv_install_with_resources; end` with no wrapper, and its `test do`
   block renders a PDF in CI.
2. **The verification job must run on macOS.** The patch is a macOS mechanism; a Linux
   runner would exercise nothing this channel is for.

## Decisions taken

| Decision | Choice | Who |
| --- | --- | --- |
| Formula scope | all four extras — `render,google,mcp,completion`, matching the container image | owner, 2026-08-23 |
| Dependency strategy | approach B: `depends_on` the bottled library formulas, vendor the rest | owner, 2026-08-23 |
| Bump mechanism | regenerate, then **install and test it**, and push only if that passes | owner, 2026-08-23 |
| Formula source of truth | the tap only; no second copy in this repo | owner, 2026-08-23 |
| Extras list | **hand-listed**, matching `tests/test_docker_channel.py` | revision 1 |
| Dispatch proof scope | the **whole chain**, to a scratch branch of the tap | revision 1 |
| Scheduled tap `brew test` | yes — weekly, in the tap repo | revision 1 |
| Intel macOS | **not verified**; arm64 only, stated as an accepted residual | revision 1 |
| Regeneration | `brew update-python-resources`, never a hand edit | #104 |

## Measured inputs

Resolved closure of `job-sluice[render,google,mcp,completion]==1.2.0` on CPython 3.14:
**61 packages** (60 dependencies plus `job-sluice`).

- **All 60 publish an sdist.** Homebrew `resource` blocks require sdists, so there is no
  hard blocker. Checked against the PyPI JSON API, per package and per exact version.
- **11 have a genuine homebrew-core formula**, all bottled: `weasyprint`, `pillow`,
  `cryptography`, `pydantic`, `rpds-py`, `fonttools`, `cffi`, `pycparser`, `certifi`,
  `httpx2`, `uvicorn`.
- **5 name matches are different software and must not be depended on.** This is the trap
  this section exists to record:

  | Formula | homebrew-core ships | the Python package is |
  | --- | --- | --- |
  | `click` 0.6.3 | a Kubernetes CLI controller | `click` 8.4.2 |
  | `brotli` 1.2.0 | Google's C library | the Python binding, **also 1.2.0** |
  | `zopfli` 1.0.3 | Google's C compressor | the Python binding 0.4.3 |
  | `protobuf` 36.0 | Google's C++ implementation | `protobuf` 7.36.0 |
  | `httpx` 1.10.0 | ProjectDiscovery's Go HTTP toolkit | ours is `httpx2` — one suffix away |

  `brotli` is the dangerous one: the version strings are identical, so a version comparison
  would certify the wrong package as a match. **`httpx` is a different kind of hazard and the
  distinction matters**, because a rule phrased as "the formula name matches a package in our
  closure" does not cover it: our package is `httpx2`, so no name matches at all. It is a
  formula one suffix away from ours whose content is a Go security scanner. Both belong on the
  same forbidden list; only one of them is a name *match*, and the constant's comment must say
  so rather than assert a rule its own membership falsifies.

- **Only some of those 11 are importable from another formula's virtualenv**, and the
  difference is visible in each formula's `install` method:
  - `pythons.each { pip install . }` installs into the brewed interpreter's site-packages
    and IS importable: `pillow`, `pydantic`, `cryptography`, `cffi`, `rpds-py`.
  - `virtualenv_install_with_resources` builds a private `libexec` venv and is NOT:
    `weasyprint`, `fonttools`.

  `virtualenv_install_with_resources` defaults to `system_site_packages: true`, which is
  what makes the first group reachable and why `exclude_packages` works at all.

Net effect of approach B: **all three Rust builds** (`pydantic-core`, `rpds-py`,
`cryptography`) and the two heaviest C builds (`pillow`, `cffi`) leave our tree. What is
vendored is essentially pure Python plus `brotli`, `zopfli`, `markupsafe` and `pyyaml`, all
small. No bottling pipeline is needed to get a fast install.

## Design

### 1. The formula (lives only in the tap; fully derived, never hand-edited)

```ruby
class JobSluice < Formula
  include Language::Python::Virtualenv

  desc     "Engineered, config-driven job-hunting pipeline"
  homepage "https://github.com/MrReasonable/sluice"
  url      "https://files.pythonhosted.org/packages/../job_sluice-X.Y.Z.tar.gz"
  sha256   "..."
  license  "MIT"

  depends_on "python@3.14"     # THE payoff mechanism -- see "Why this channel exists"
  depends_on "pango"           # WeasyPrint's native tree (pulls cairo, glib, harfbuzz)
  depends_on "cffi"            # the five importable library formulas; each one removes a
  depends_on "cryptography"    # compiled build from the vendored tree
  depends_on "pillow"
  depends_on "pydantic"
  depends_on "rpds-py"
  uses_from_macos "libffi"

  pypi_packages package_name:     "job-sluice[render,google,mcp,completion]",
                exclude_packages: %w[cffi cryptography pillow pydantic rpds-py]

  resource "..." do ... end    # ~45 stanzas, generated by brew, never authored

  def install
    virtualenv_install_with_resources
  end

  test do
    # The ambient environment is NOT clean: SLUICE_CONFIG, VAULT_DIR and the XDG variables
    # would otherwise point a local `brew test` at the maintainer's real vault and read
    # their live config. Sandbox them before anything runs.
    ENV.delete("SLUICE_CONFIG")
    ENV.delete("VAULT_DIR")
    ENV["HOME"] = testpath
    ENV["XDG_CONFIG_HOME"] = testpath/"config"
    ENV["XDG_STATE_HOME"]  = testpath/"state"

    assert_match version.to_s, shell_output("#{bin}/job-sluice --version")

    # `doctor --offline` exits 1 on ANY unconfigured machine and that is BY DESIGN -- no
    # vault directory and no `claude` CLI are both DEAD rows, and `exit_code` returns 1 on
    # any DEAD. Measured: two DEAD rows and EXIT=1 under `env -i` with a scratch HOME.
    # `ci.yml` records the same fact for the container smoke and asserts the status in
    # NEITHER direction. Asserting success here would fail every release; asserting the
    # code is 1 pins the designed behaviour and still proves the report ran end to end.
    report = shell_output("#{bin}/job-sluice doctor --offline", 1)
    assert_match "job-sluice doctor", report

    # THE PAYOFF, through the product's own probe. POSITIVE, not a refutation of "dead":
    # `core/app.py`'s `if cv_cfg is not None:` drops the renderer row ENTIRELY on any
    # load_cv_config() error, with exit 1 and the banner intact -- so a `refute_match` on
    # "dead" passes when the row is merely ABSENT. A negative guard that finds nothing is
    # indistinguishable from success; this one fails closed.
    # Row format is `f"{component:12} {subject:32} {state:9} ..."` (cli.py:1537).
    assert_match(/renderer\s+cv\.renderer\s+ok/, report)

    # ...and independently, without going through sluice at all, so a change to doctor's
    # output format cannot silently retire the payoff check above.
    system libexec/"bin/python", "-c",
           "import weasyprint; weasyprint.HTML(string='<p>x</p>').write_pdf('t.pdf')"
    assert_predicate testpath/"t.pdf", :exist?
  end
end
```

Three properties are load-bearing and easy to lose:

- **`pypi_packages package_name:` must carry the extras inline.** Homebrew's PyPI parser
  sets `extras = []` whenever it derives a package from a URL, so the `url` above can never
  imply them. A formula that loses the extras installs core-only, and both the `--version`
  and the `doctor --offline` assertions still pass — the renderer-row `assert_match` and the
  WeasyPrint line are what catch it.
- **`exclude_packages` must equal the importable-library `depends_on` set.** Excluding a
  package nothing supplies produces an `ImportError` at runtime; depending on one that is
  not excluded vendors a second copy.
- **`depends_on "python@3.x"` is the entire payoff.** Deleting it leaves a formula that
  still builds, still passes `--version`, and fails only the two payoff assertions. §5 pins
  its presence in the rendered text for that reason.

### 2. `scripts/render_homebrew_formula.py`

The job does not hand-write YAML-embedded Ruby. A script renders the skeleton, in the same
idiom as the existing `scripts/build_linux_packages.py`.

**Pure by construction.** It takes version, sdist URL and sha256 as arguments and returns
formula text. It reads no files — including `pyproject.toml`, deliberately (see below) — so
it is unit-testable offline and mutation-testable (CLAUDE.md's `compileall` line already
covers `scripts`).

**The extras are hand-listed here, and the EXPECTATION lives in the test — not imported
from this module.** This is the third attempt at the same guard and the first that can fail.
Revision 0 derived the list from `pyproject.toml` and asserted the rendered string against a
parse of the same file. Revision 1 moved the list into a constant — but left the constant in
this *producer* and asserted the rendered text equalled it, which is the identical defect one
layer up: delete `"mcp"` and the formula and the expectation move together, green.

The precedent's non-vacuity does not come from "a constant" — it comes from **two independent
sources**. `tests/test_docker_channel.py:50` declares `_EXPECTED_EXTRAS` in the TEST and
compares it against a separately hand-authored `Dockerfile`. Here the formula is machine-
generated, so the second source must be the test's own literal, restated by a human and never
imported from `scripts/`. Round 2 raised this independently three times; it is the one repair
in this document with unanimous corroboration.

Per this repo's own rule — when a narrowing needs a third patch, stop patching and change the
structure — the rule is now a property rather than a comparison: **no test may import a name
from `render_homebrew_formula.py` that it also asserts on.**

**That property is enforced by a TEST, not by a grep in the definition of done.** Revision 2
wrote it as a DoD step, which is a discipline rather than a control — and this is the third
consecutive round in which a control of that class has failed. The repo already has the
idiom: `test_every_module_level_helper_takes_path_first_with_no_default`
(`tests/test_release_publish_wiring.py:1461`) uses `inspect` plus a scope EQUALITY so a
matcher that enumerated nothing cannot pass vacuously. The homebrew equivalent walks the test
module's own `Import`/`ImportFrom` nodes with `ast`, derives each local binding from
`asname or name` — a hand-listed sweep keyed on the original name is exactly what
`from x import y as _z` walks past, which CLAUDE.md already records — and asserts the set of
names imported from the renderer against an explicit allow-list.

```python
# Shipping scope. The TEST restates this list independently (see tests/test_homebrew_formula.py)
# and MUST NOT import it from here -- that is what makes the guard falsifiable at all. Deriving
# it from pyproject, or importing this tuple, both collapse the two sources into one and the
# assertion can no longer fail.
_SHIPPED_EXTRAS = ("render", "google", "mcp", "completion")

# A homebrew-core formula whose `install` runs `pip install` against each brewed
# interpreter lands in that interpreter's site-packages, so a virtualenv created with
# system_site_packages=True (Homebrew's default) can import it. Depending on one removes
# its build from our vendored tree -- the point, since three of them are Rust.
#
# A formula whose `install` is `virtualenv_install_with_resources` gets a PRIVATE libexec
# venv and is NOT importable, so `weasyprint` and `fonttools` are absent here on purpose.
_IMPORTABLE_CORE_FORMULAE = ("cffi", "cryptography", "pillow", "pydantic", "rpds-py")

# homebrew-core formulae that must NEVER be depended on. Two distinct hazards, kept in one
# tuple because the consequence is identical and split in the comment because the RULE is not:
#   - a NAME MATCH with different content: click (a Kubernetes CLI), brotli and zopfli (the
#     Google C libraries, not the Python bindings), protobuf (the C++ implementation).
#     `brotli` ships the SAME version string as the Python binding, so a version check would
#     certify it as correct.
#   - a NEAR MATCH with no name collision at all: our package is `httpx2`; homebrew-core's
#     `httpx` is ProjectDiscovery's Go toolkit. Nothing in our closure is named `httpx`, so a
#     match-based rule does not reach it -- which is exactly why it is listed by hand.
_FORBIDDEN_FORMULAE = ("click", "brotli", "zopfli", "protobuf", "httpx")
```

`depends_on` lines and `exclude_packages` are both emitted from `_IMPORTABLE_CORE_FORMULAE`,
so they cannot disagree *with each other*. Revision 1 presented that as a virtue; round 2
pointed out it is also why an equality test between them proves nothing, and why dropping
`pydantic` from the tuple silently re-vendors a Rust build with every proposed assertion still
green. One source needs one INDEPENDENT expectation, not a second view of itself — §5 supplies
it test-side.

**`depends_on "python@3.14"` cannot be derived from anything in this repository**, and
revision 0 was wrong to imply otherwise while stating a "never hand-list" rule beside it.
It tracks homebrew-core's default CPython, which is a Homebrew concern with no local source
of truth. What is available: `brew audit --strict` fails on a deprecated or missing
`python@` formula, and §5 pins that the rendered text names *some* `python@` version and
that it is at or above `pyproject.toml`'s `requires-python` floor. That is a weaker
guarantee than derivation and is stated as such rather than dressed up.

### 3. The `homebrew` job in `.github/workflows/release-please.yml`

```yaml
homebrew:
  needs: [release-please, pypi]
  if: success() && needs.release-please.outputs.release_created == 'true'
  runs-on: macos-latest
  permissions:
    contents: read      # justified: this job DOES check out, for the renderer script
```

- **`needs: pypi` is a real ordering constraint, not style.** The formula's `url` is a PyPI
  sdist; it cannot resolve before that upload lands. This is the workflow's first
  cross-channel dependency.
- **`runs-on: macos-latest`** for the reason given above. Free for public repositories.
- **ONE `actions/checkout`, for sluice only.** It supplies
  `scripts/render_homebrew_formula.py` and pins
  `ref: ${{ needs.release-please.outputs.sha }}` — the commit release-please TAGGED, for the
  same reason `build`, `docker` and `linux-packages` pin it — with `persist-credentials: false`,
  as all eight checkouts in this tree do (`ci.yml:28` gates on zizmor). SHA-pinned with a
  trailing `# vX.Y.Z`.
- **The tap is NOT an `actions/checkout`, and cannot be.** `brew update-python-resources`,
  `audit`, `install` and `test` resolve a formula through the tap directory under
  `$(brew --repository)/Library/Taps/`, which is outside `$GITHUB_WORKSPACE` — and
  `actions/checkout` refuses a path outside the workspace. Revisions 1 and 2 asked for a
  second checkout at that path with `persist-credentials: false`; those two requirements are
  not merely awkward together, they describe a step that cannot exist. The job instead obtains
  the tap the way `brew` does (`brew tap MrReasonable/tap`, or a `git clone` into that path
  when the tap has no formula yet — see §4), works in place, and applies the App token **only
  to the push URL**, so no credential is written to `.git/config` and the property
  `persist-credentials: false` exists to protect is preserved by construction rather than by a
  flag that does not apply here.
- **Auth:** `actions/create-github-app-token` with `owner`/`repositories` scoped to
  `homebrew-tap` alone and `permission-contents: write`, minted per run, never stored —
  the same shape as the `release-please` job. The App (`sluice-release-please`) is installed
  on both repositories with read+write access to code (verified 2026-08-23 from the App's
  installation settings).
- **`release-assets` remains the only holder of `contents: write` on the GITHUB_TOKEN.**
  This job's `contents: write` is on a *scoped App installation token* for a *different
  repository*, so that job's comment stays true. Revision 0 did not say so; it does now,
  because a reader checking that claim would otherwise conclude this job falsified it.
- **Steps:** read the sdist URL and sha256 from the PyPI JSON API for the exact released
  version → render the skeleton → `brew update-python-resources --version X.Y.Z
  --ignore-main-package-cooldown` → `brew audit --strict --online` →
  `brew install --build-from-source` → `brew test` → **only then** commit and push.
- **`--ignore-main-package-cooldown` is required, not optional.** This job runs minutes
  after the PyPI upload, and the resolver otherwise refuses a package that new. Homebrew
  honours the flag for non-official taps only; ours is non-official, so it applies.
- **The push is gated on the install and the test.** A green job that pushed an unverified
  formula would repeat the deb/rpm failure exactly: three root-only container runs certified
  a package that no ordinary user could run.

### 4. Proving the whole chain before a release depends on it

Revision 0 proposed proving only the App-token mint, and invoked `testpypi.yml` as the
precedent. That was a misreading of the precedent: `testpypi.yml` is not a credential probe
— it stamps a unique dev version, builds, and uploads to a real index, exercising the entire
publish path. A mint-only probe would leave render, `update-python-resources`, `audit`,
`install --build-from-source` and `brew test` all first executing during a real release,
after §5 has already flipped the README to `shipped`.

The dispatch therefore runs **the whole chain** against the currently released version. It is
a `workflow_dispatch` on `macos-latest`, and it stays on `main` afterwards — as `testpypi.yml`
did — so the chain can be re-proven after any App, permission or homebrew-core change without
waiting for a release.

**It cannot run before this PR merges, and revision 1's task order was impossible.**
`workflow_dispatch` only fires for a workflow file already on the default branch;
`testpypi.yml:17-21` makes the same constraint explicit with a refusal step
(`if: github.ref_name != github.event.repository.default_branch` → `exit 1`). The precedent
this design invokes therefore ran its dispatch **after** PR 3 merged and **before** the first
release — which is exactly where this one belongs, and matches how #104 already records the
TestPyPI dry run as a post-merge owner step.

**The same dispatch bootstraps the tap, which is empty.** Nothing in revision 1 wrote the
tap's first formula, and every path assumed one already existed: a scratch branch has no base,
and `brew update-python-resources` edits a formula rather than creating one.

**The observable is whether `Formula/job-sluice.rb` exists — not whether the repository has
commits.** Revision 2 used the latter, which is the wrong question: a tap carrying only an
auto-created README has commits, so it would take the scratch arm while
`update-python-resources` edited nothing; and a false "empty" verdict pushes to the tap's
default branch from a run meant to touch nothing users install. Branch on the artefact the
next step actually needs.

**`brew tap-new` must NOT be used.** Verified against the installed Homebrew
(`dev-cmd/tap-new.rb:95-99`): it unconditionally writes `.github/dependabot.yml` and three
workflows — only `git init` sits behind `--no-git`. One of them, `autobump.yml`, runs
`brew bump --open-pr` on a daily schedule, which would make it a **second automated writer of
the formula this design declares machine-owned**, racing the release job. Independently, an
App installation token scoped `permission-contents: write` cannot push files under
`.github/workflows/` at all, so a `tap-new` bootstrap would fail at the push even if the extra
files were wanted. The job therefore creates `Formula/job-sluice.rb` in the tap directory
itself and commits only that.

The first dispatch is the bootstrap and pushes the default branch; every later one pushes a
scratch branch and touches nothing users install.

### 5. Guards

Adding `"homebrew": "Homebrew"` to `_CHANNEL_JOBS` in
`tests/test_release_publish_wiring.py` does two things at once: it satisfies
`test_every_release_job_is_classified_as_channel_or_infrastructure`, and — because that file
asserts `shipped == set(_CHANNEL_JOBS.values())` — it forces the README's Homebrew row to
flip from `planned` to `shipped` in the same commit. Neither can land without the other.

**That classification test is a set EQUALITY against the live job roster, in both
directions.** So the `_CHANNEL_JOBS` entry and the workflow job must land in the SAME
commit: adding the entry without the job fails on the "named here but absent from the
workflow" arm. Revision 0 claimed these tasks were independent; they are not.

New pins, in the file's existing text-matching idiom:

| Pin | The failure it prevents |
| --- | --- |
| `_RELEASE_PLEASE_JOBS` gains `homebrew` in file order | the roster list-equality failing, and the job's position going unpinned |
| a per-job `_permissions_block` equality pin | see below — the roster entry ALONE is worse than useless |
| any new module-level helper joins `_MODULE_HELPER_NAMES` and takes `path` first, required | `test_every_module_level_helper_takes_path_first_with_no_default` pins that set by EQUALITY; a new helper fails it, and a defaulted `path` would silently read whichever file the default names |
| the dispatch workflow gets a `Path` constant beside `RELEASE_PLEASE`/`TESTPYPI` and pins written against it | nothing enumerates `.github/workflows/*` by glob — verified, there is no such sweep — so adding a fourth file breaks NOTHING and is simply invisible to the suite. This is not a case of satisfying an existing equality; the pins must be written deliberately, and this one holds the cross-repo write token |
| the dispatch workflow gets its own `_permissions_block` equality pin and a default-branch refusal pin | `testpypi.yml` — the precedent this design invokes — carries both (`:17-21`); revision 2 gave the new file neither |
| gated on `release_created` | the job firing on a non-release push |
| `needs: pypi` | resolving a formula URL before the sdist exists |
| `runs-on: macos-latest` | verifying on a platform where the payoff mechanism is absent |
| App token scoped to `homebrew-tap`, `permission-contents: write` | a broader token than the job needs |
| checkout pins `needs.release-please.outputs.sha`, `persist-credentials: false` | rendering from a different commit than the one tagged |
| `--ignore-main-package-cooldown` present | the job failing at every release |
| push gated behind audit + install + test | publishing an unverified formula |

`_ROSTER_MESSAGE` in that file states the trap in its own words, and it is sharper than "add
both": *"a job absent from this roster is one nothing in the suite has ever looked at — its
`permissions:` block included. Add the job HERE and give it its own equality pin; extending
this list alone restores the blind spot it exists to close."* So adding `homebrew` to
`_RELEASE_PLEASE_JOBS` without also writing its `_permissions_block` equality pin does not
half-solve the problem — it makes the roster assert coverage the suite does not have.

`attest-image`'s comment (`release-please.yml`) separately asserts as a LIVE property that
"every job in this file has an exact `_permissions_block` equality pin", used to argue that no
future job can hold a registry credential and an OIDC identity together. The `homebrew` job
holds `contents: read` and neither `id-token` nor `packages`, so it preserves that property —
but only if its pin is written.

**The push gate is asserted by STEP ORDER, not by a condition reference.** Revision 1
specified the pin as asserting the push step references `steps.<id>.outcome` of the test
step. In GitHub Actions that reference is only meaningful when the referenced step sets
`continue-on-error: true`; without it a failing step already ends the job, the reference is
unreachable, and **deleting it changes nothing** — so revision 1's pin, and the mutation
witness it named in the DoD to prove the pin worked, were both equivalent mutants. A repair
for a finding about unfalsifiability that was itself unfalsifiable, twice over.

This file already has the right idiom in
`test_the_packaged_directories_are_verified_before_upload`: assert INDEX ORDER over the job
block — `brew install` < `brew test` < the push. Witness by MOVING the push step above
`brew test`, which is a real reordering the order assertion must catch.

**The companion check is an ALLOW-LIST over parsed directives, not a substring blocklist.**
Revision 2 proposed forbidding `if: always()` and `continue-on-error:`. That is wrong in both
directions: the literal `if: always()` already appears in COMMENTS at `ci.yml:70` and
`release-please.yml:260`, so a substring sweep false-positives on correct files; and
`${{ always() }}`, `!cancelled()`, `success() || failure()` and a trailing `|| true` all slip
past it while pushing after a failure. Instead, parse the job block, strip comments, and
assert the push step's `if:` is one of an explicitly allowed set (in practice: absent, or the
release gate the other jobs use), with `if: ${{ !cancelled() }}` added as a second mutation
witness alongside the reorder.

Unit tests for `scripts/render_homebrew_formula.py`, offline, against the RENDERED TEXT:

**Every expected value below is a literal in the test file. None is imported from
`scripts/render_homebrew_formula.py`.** That is the whole non-vacuity mechanism, and both
earlier revisions lost it — once by deriving from `pyproject.toml`, once by importing the
producer's own constant. A test that imports what it asserts on cannot fail.

- The rendered extras equal the TEST's own `_EXPECTED_EXTRAS` literal (equality first — the
  non-vacuity anchor), then are a subset of `pyproject.toml`'s declared extras, with an
  assertion that the extraction found a non-empty set. Mirrors
  `test_the_dockerfile_installs_exactly_the_expected_extras` in shape AND in source
  independence. Witness: delete an entry from the renderer's `_SHIPPED_EXTRAS` — the test's
  literal still names it, so the equality reddens.
- The rendered `depends_on` set equals the TEST's own literal for it, and the rendered
  `exclude_packages` set equals the test's literal for that. Both directions matter: a name
  excluded but not depended on is an `ImportError` at runtime, and a name depended on but not
  excluded vendors a second copy — §1 names both and revision 1's subset check saw only one.
  Witness: delete `pydantic` from `_IMPORTABLE_CORE_FORMULAE`, which removes it from both
  emissions at once and silently re-vendors a Rust build; the test's literals redden.
- **The rendered text contains `depends_on "python@`**, and the named version is at or above
  `pyproject.toml`'s `requires-python` floor AND is a version `pyproject.toml` declares a
  classifier for — a one-sided floor accepts a python that does not exist yet. This closes
  round 1's sharpest finding: nothing before it caught deleting the payoff mechanism.
- No name from the test's own forbidden literal appears in the rendered `depends_on` set.
- A scope assertion on every set-valued extraction, since `set() == set()` passes and
  `all([])` is `True`.

**Named gap, accepted:** no test in this repository ever reads the formula actually shipped
in the tap. The generator plus the workflow pins constrain what the job *writes*; nothing
constrains what the tap *holds* — a hand-edit there, or a push from anywhere else, is
invisible to this suite. The compensating control is the scheduled `brew test` in §7, which
exercises the live formula rather than the rendered one.

### 6. Documentation

This PR makes a currently-true claim false in several places, so the claim is grepped rather
than only the changed code.

| File | Change |
| --- | --- |
| `README.md` channel table | Homebrew row → `shipped`, with the install command (currently `—`) |
| `README.md` ~113 | "the install channels still marked *planned*" goes false once none is |
| `README.md` ~133 | "Rows marked *planned* are tracked in #104" goes false likewise |
| `README.md` macOS rendering note | correct the incomplete measurement: the operative variable is the **interpreter**, not the libraries |
| `docs/TROUBLESHOOTING.md` | name the Homebrew answer beside the export workaround |
| `sluice/renderers/template.py` | carries "even once Homebrew has installed them" — **narrow**, since it stays true for a pip install on a non-Homebrew interpreter |

`sluice/core/doctor.py` is **not** in this list. Revision 0 claimed it carried the same
clause; it does not — it says only "the `DYLD_FALLBACK_LIBRARY_PATH` note on macOS", which
remains accurate. Both files were read and the two strings conflated.

Also folded in, per the note left by PR 5: the `Dockerfile` comment that still names
`3.13-slim` while the `FROM` line below it reads 3.14. Fixed by removing the version from
the comment, not by correcting it — correcting re-rots at 3.15.

`docs/INSTALL.md` is **out of scope**: it is PR 7. `docs/ARCHITECTURE.md` documents no
publish channel, so it needs no change — checked, not assumed.

### 7. Scheduled `brew test` in the tap

Approach B couples us to homebrew-core: a `pydantic` or `cryptography` major bump can break
the installed venv between our releases. Revision 0 said detection would be "the next
release's `brew test`". **That is the wrong order — installed users break first, and our
pipeline finds out later.** A weekly `schedule:` workflow in the tap runs
`brew install --build-from-source` and `brew test` against the live formula and opens an
issue on failure. Without it, this risk has no mitigation and revision 0's claim that it did
was false.

**It lives where this repository's guards are blind, and that is stated rather than glossed.**
`pytest`, `ruff`, `zizmor` and the roster pins in `tests/test_release_publish_wiring.py` all
operate on this repo; none of them can see a workflow in the tap. §5's named gap and this
mitigation are therefore in the same blind spot, which is an honest limitation of putting the
formula in the tap at all — not something a pin here can fix.

**The owner adds this workflow by hand, and that is a constraint rather than a preference.**
An App installation token scoped `permission-contents: write` cannot push files under
`.github/workflows/`, so the release job could not create or update it even if it wanted to.
Revision 2 asserted the tap would hold "a machine-written formula plus exactly one
hand-written workflow"; that was stated in the indicative without being checked, and it is
only true if the tap is created WITHOUT `brew tap-new`, which unconditionally adds a
`dependabot.yml` and three workflows of its own (§4). Created as §4 specifies, the tap holds
the formula and this one workflow — but that is a consequence of the bootstrap choice, not a
property of taps.

## Tasks

**In this PR:**

1. `scripts/render_homebrew_formula.py` plus its offline unit tests, whose expected values are
   test-side literals. Start from a failing test.
2. **One commit:** the `homebrew` job in `release-please.yml`, the `_CHANNEL_JOBS` /
   `_RELEASE_PLEASE_JOBS` / `_permissions_block` / `_MODULE_HELPER_NAMES` pins, and the README
   channel row. The roster equality makes these inseparable.
3. The `workflow_dispatch` whole-chain proof workflow (the FILE; running it comes later).
4. The documentation narrowings, plus the Dockerfile comment.

Tasks 2 and 3 both depend on task 1 — each invokes the renderer. Task 4 is independent.

**After merge, owner-executed, before the next release:**

5. Dispatch the whole-chain workflow from the default branch. This bootstraps the tap (writes
   its first formula and its default branch) and proves render → `update-python-resources` →
   audit → install → test → push end to end.
6. Add the scheduled `brew test` workflow to the tap.

Steps 5 and 6 cannot be in this PR: `workflow_dispatch` only fires for a file already on the
default branch, and step 6's file lives in another repository. Revision 1 placed the dispatch
"early" among the in-PR tasks, which was not merely optimistic but impossible. This ordering
is the same one #104 already records for the TestPyPI dry run.

Owners are implementers, not reviewers — revision 0 named review agents as task owners.

## Definition of done

```bash
.venv/bin/python -m pytest                                  # full suite, offline
.venv/bin/ruff check sluice tests scripts                   # ruff==0.15.21, the CI pin
.venv/bin/zizmor --offline --strict-collection .github/workflows/   # CI's lint job runs this
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Plus:

- A **mutation witness per new assertion**, mutating by MOVING or DELETING, run by node id,
  confirming no sibling test catches the mutant. Explicitly including: delete
  `depends_on "python@` from the renderer; delete an entry from `_SHIPPED_EXTRAS`; delete
  `pydantic` from `_IMPORTABLE_CORE_FORMULAE`; and MOVE the push step above `brew test`.
  Each must be checked for equivalence first — revision 1's named witness (deleting an
  unreachable `steps.<id>.outcome` reference) changed nothing the assertion read.
- The import-independence property is asserted by a TEST (§5), not checked by hand. A grep in
  a definition of done is the same class of control that failed in each of the three review
  rounds this document has been through.
- `gh pr view` shows CodeRabbit APPROVED on head, per the standing merge gate.

**Not in the merge gate, because it cannot be:** the whole-chain dispatch runs after merge
(see Tasks). Until it has, the `homebrew` job is unproven end to end and no formula exists in
the tap.

**The README row is nevertheless correct at merge, by the table's own definition.** It states:
*""Shipped" means the release workflow builds and publishes that channel*, so a row becomes
shipped when its job lands and takes effect from the next release onward; it is not a claim
that every past release carries it." That is a claim about the roster, which is exactly what
lands here — so the guard coupling forcing the row and the job into one commit is not merely
tolerable, it is the semantics the table already publishes. The same reading covered PR 3
between its merge and its TestPyPI dispatch.

What the definition does NOT cover is the row's Install CELL, which will carry a `brew install`
command that fails until the tap holds a formula. That window is closed by running task 5
immediately after merge rather than at leisure, and it is the reason task 5 is sequenced first
among the post-merge steps rather than listed as general follow-up.

The end-to-end proof is the next release-please merge producing a bumped tap formula that a
stranger can `brew install`, with a real PDF written — verified from the published artefact,
not from job status.

## Risks and accepted residuals

- **Regeneration resolves ~45 dependencies at release time, unreviewed.** Mitigated, not
  removed, by installing and testing before pushing: a resolution that breaks the build
  fails the job and nothing reaches the tap. A resolution that installs but is subtly wrong
  still ships. Accepted; a PR on the tap makes releases non-automatic and fails toward a
  silently stale formula, which is worse.
- **homebrew-core can move a depended-on formula under us, and installed users feel it
  first.** Approach B's cost. Mitigated by §7's scheduled `brew test`, which bounds the
  detection window to a week rather than to our next release.
- **The formula carries only the latest version.** A tap has no version history; pinning an
  older release means another channel. To be stated plainly in PR 7's `docs/INSTALL.md`.
- **Intel macOS is not verified.** `macos-latest` is arm64, and the Homebrew prefix differs
  on Intel (`/usr/local`). The dyld patch applies to both, so the mechanism should hold, but
  this ships unverified there. Named, not overlooked.
- **macOS runner minutes** are free for public repositories today. If that changes, this job
  and §7's schedule are the first things to feel it.
- **The tap is not bottled**, so users build ~45 pure-Python resources from source. Fast,
  but not instant. Bottling is deliberately deferred (approach C), not overlooked.
- **Nothing in this repository reads the shipped formula.** See §5's named gap.

## Out of scope

- `docs/INSTALL.md` and the README install prose beyond the channel table — PR 7.
- Bottling the tap (`brew test-bot`).
- Homebrew core submission: this project does not meet the notability threshold, per #104.
- Any change to the PyPI, Docker or deb/rpm channels.
