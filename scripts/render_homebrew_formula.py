"""Render the Homebrew formula for job-sluice (#104, PR 6 of 7).

The tap holds no hand-written formula. This renders the skeleton fresh from release metadata
on every bump, and `brew update-python-resources` fills the ~45 resource stanzas afterwards.
Nothing here is edited by hand, which is what makes "the formula lives only in the tap"
coherent rather than merely tidy.

PURE: takes version, sdist url and sha256, returns text. It reads NO files -- not even
pyproject.toml -- so the tests run offline and no ambient file can mask a mutant. The expected
values live in tests/test_homebrew_formula.py as literals a human restated; that test must
never import them from here, because two independent sources is the only thing that makes the
assertions falsifiable at all.
"""

# Shipping scope. tests/test_homebrew_formula.py restates this independently and MUST NOT
# import it -- see this module's docstring. Deriving it from pyproject.toml (the first design)
# collapses the two sources into one and the guard can no longer fail.
_SHIPPED_EXTRAS = ("render", "google", "mcp", "completion")

# homebrew-core formulae whose `install` runs `pip install` against each brewed interpreter,
# so they land in that interpreter's site-packages and a virtualenv created with
# system_site_packages=True (Homebrew's default) can import them. Depending on one removes its
# build from our vendored tree, which is the point: pydantic, cryptography and rpds-py are Rust
# builds and pillow and cffi are C.
#
# A formula whose `install` is `virtualenv_install_with_resources` gets a PRIVATE libexec venv
# and is NOT importable, so `weasyprint` and `fonttools` are deliberately absent here.
#
# These are emitted BOTH as `depends_on` lines and as `exclude_packages`. A name excluded but
# not depended on is an ImportError at runtime; depended on but not excluded vendors a second
# copy.
_IMPORTABLE_CORE_FORMULAE = ("cffi", "cryptography", "pillow", "pydantic", "rpds-py")

# Never depend on these. Two distinct hazards, one tuple because the consequence is identical:
#   - NAME MATCH, different content: `click` is a Kubernetes CLI, `brotli` and `zopfli` are the
#     Google C libraries rather than the Python bindings, `protobuf` is the C++ implementation.
#     `brotli` ships the SAME version string as the Python binding, so a version check would
#     certify it as correct.
#   - NEAR MATCH, no collision at all: our package is `httpx2`; homebrew-core's `httpx` is
#     ProjectDiscovery's Go toolkit. Nothing in our closure is named `httpx`, so a
#     match-based rule cannot reach it -- which is exactly why it is listed by hand.
#
# ENFORCED, not merely documented: `render()` below raises if any of these ever lands in
# `depends` -- a producer-side second line of defence behind
# tests/test_homebrew_formula.py::test_no_forbidden_formula_is_depended_on, which only catches
# the DEFECT after the fact, on the rendered text. Two reviewers independently flagged this
# tuple as dead: ruff does not warn on an unused module-level constant, and nothing before this
# fix ever read it.
_FORBIDDEN_FORMULAE = ("click", "brotli", "zopfli", "protobuf", "httpx")

# WeasyPrint's native tree. `pango` pulls cairo, glib and harfbuzz transitively; mirrors
# homebrew-core's own weasyprint formula rather than guessing a wider set.
# `libyaml` is required by `brew audit` once the resource tree carries pyyaml: its C
# extension links against it, and the audit says so by name ("Add `depends_on` lines above
# for \"libyaml\"") rather than leaving it to fail at build time.
_NATIVE_FORMULAE = ("pango", "libyaml")

# THE PAYOFF MECHANISM, and the single most load-bearing line this file emits. Homebrew's
# CPython patches Lib/ctypes/macholib/dyld.py to put HOMEBREW_PREFIX/lib at the head of
# DEFAULT_LIBRARY_FALLBACK, which is what lets WeasyPrint find cairo/pango on macOS with no
# DYLD_FALLBACK_LIBRARY_PATH set. Every other macOS install path needs that variable; this
# channel exists because this line removes the need for it.
#
# It cannot be derived from anything in this repository -- it tracks homebrew-core's default
# CPython, which has no local source of truth. `brew audit --strict` fails on a deprecated or
# missing python@ formula, and the test pins that SOME python@ is named and that its version is
# both at or above pyproject's requires-python floor and among its declared classifiers.
#
# The constraint that ACTUALLY binds this value is a different one `brew audit` does not cover
# at all: `_IMPORTABLE_CORE_FORMULAE` makes the venv depend on homebrew-core's own
# pillow/cryptography/pydantic/rpds-py/cffi formulae for THIS interpreter's site-packages
# (`exclude_packages` above), and each of those must actually build against it. `brew audit`'s
# deprecated-dependency check only asks whether the named `python@` formula itself still
# exists and is current -- it says nothing about whether homebrew-core's OTHER formulae still
# build for it. `brew install --build-from-source` followed by `brew test` (both in
# homebrew_verify.sh) is what actually exercises that chain, which is why the release process
# never skips straight from audit to push.
_PYTHON_FORMULA = "python@3.14"

_DESC = "Engineered, config-driven job-hunting pipeline"
_HOMEPAGE = "https://github.com/MrReasonable/sluice"
_LICENSE = "MIT"


def render(*, sdist_url: str, sha256: str) -> str:
    """The formula skeleton, as text.

    Keyword-only on purpose: two same-typed strings in a row is exactly the signature where a
    positional swap produces a plausible-looking formula carrying `sha256`'s value in `url`'s
    place (or vice versa) -- a formula with the wrong digest for the right URL -- and nothing
    downstream would catch it before `brew audit` -- after the release is already public.

    No `version` parameter: nothing in the rendered formula ever reads one -- see the `# No
    `version "..."` stanza` comment below, which is emitted INTO the formula text itself. A
    parameter earlier versions of this function accepted but never consumed measured
    byte-identical between `render(sdist_url=..., sha256=..., version="9.9.9")` and the same
    call with `version="NOT-A-VERSION-AT-ALL"` -- a trap for exactly the reason CLAUDE.md's
    "fail loudly at construction" rule exists: a parameter nothing reads invites a caller to
    believe passing it does something.
    """
    # SORTED, because `brew audit --strict` runs RuboCop and FormulaAudit/DependencyOrder
    # requires alphabetical `depends_on`. Measured: emitting `python@3.14` first drew five
    # separate "should be put before" errors on the first real dispatch. Sorting here rather
    # than reordering the tuples keeps each tuple grouped by MEANING (interpreter, native
    # libraries, importable core formulae) for a human, while the emitted file is ordered
    # for the auditor.
    depends = sorted([_PYTHON_FORMULA, *_NATIVE_FORMULAE, *_IMPORTABLE_CORE_FORMULAE])
    # Fail loudly at construction (CLAUDE.md) rather than emit a formula that would only be
    # caught later by `brew audit`/`brew install`, or not at all: homebrew-core ships DIFFERENT
    # software under each of these names -- see `_FORBIDDEN_FORMULAE`'s comment above.
    forbidden_hit = set(depends) & set(_FORBIDDEN_FORMULAE)
    if forbidden_hit:
        raise ValueError(
            f"refusing to render a formula depending on {sorted(forbidden_hit)}: "
            f"homebrew-core ships different software under that name than the Python "
            f"package sluice actually needs -- see _FORBIDDEN_FORMULAE's comment above for "
            f"which."
        )
    depends_lines = "\n".join(f'  depends_on "{name}"' for name in depends)
    excludes = " ".join(_IMPORTABLE_CORE_FORMULAE)
    extras = ",".join(_SHIPPED_EXTRAS)
    return f'''class JobSluice < Formula
  include Language::Python::Virtualenv

  desc "{_DESC}"
  homepage "{_HOMEPAGE}"
  url "{sdist_url}"
  sha256 "{sha256}"
  license "{_LICENSE}"

  # No `version "..."` stanza here, deliberately. Homebrew's canonical component order is
  # `url, mirror, version, sha256, license` (`FormulaAudit/ComponentsOrder`, a plain cop that
  # fires even without --strict), which the emitted `url`/`sha256`/`license` above already
  # satisfy in the absence of a `version` line -- but a `version` line, wherever placed, would
  # ALSO be flagged by `resource_auditor.rb` as "redundant with version scanned from URL":
  # `Version.detect` on a PyPI sdist filename returns the identical string, so `brew audit
  # --strict --online` fails either way. Homebrew's own `version` -- this Formula's DSL-level
  # accessor, POPULATED by that same `Version.detect` call against the `url` above, not
  # anything this file passes in -- is what `test do`'s `assert_match version.to_s, ...` below
  # reads, so nothing here needs to emit a version a second time.

{depends_lines}
  uses_from_macos "libffi"

  pypi_packages package_name:     "job-sluice[{extras}]",
                exclude_packages: %w[{excludes}]

  def install
    virtualenv_install_with_resources
  end

  test do
    # The ambient environment is NOT clean: any SLUICE_*/CAMOFOX_* variable, or one of this
    # project's other path-shaped env vars, would point a local `brew test` at the
    # maintainer's real vault, config, dedup state, health/audit state, dossier cache, or a
    # real camofox server -- SLUICE_TELEGRAM_TOKEN and SLUICE_TELEGRAM_CHAT in particular are
    # a CREDENTIAL pair sluice/core/log.py reads ahead of config and POSTS with. Swept by NAME
    # PATTERN rather than hand-listed: an earlier version of this block named only
    # SLUICE_CONFIG and VAULT_DIR while this very comment already stated the general
    # principle -- two reviewers independently caught the gap, and CLAUDE.md's "hand-listed
    # names lose" lesson applies here exactly as it does to a Python AST sweep.
    # `to_h` snapshots before iterating. Measured on this Ruby, deleting from ENV during a
    # bare `each_key` is fine -- but depending on a collection's mutation-during-iteration
    # semantics is a hazard worth not taking, and the snapshot costs one allocation. `each_key`
    # rather than `keys.each` because `brew audit --strict` runs Style/HashEachMethods.
    ENV.to_h.each_key do |k|
      ENV.delete(k) if k.match?(/\\A(SLUICE|CAMOFOX)_/)
    end
    # Explicitly-named path variables outside that prefix shape -- never hand-guessed, and
    # NOT enumerated from sluice/core/paths.py, which an earlier version of this comment
    # named: that module DEFINES `resolve` and names no variable of its own. The names come
    # from the `resolve(env_var="...")`/`_resolve_path(env_var="...")` CALL SITES and the
    # direct `os.environ.get("...")` reads, which live in other modules under sluice/.
    # Deliberately not listed here by file: tests/test_homebrew_formula.py's
    # `test_the_test_block_sandboxes_every_env_var_sluice_reads` re-derives the whole set by
    # AST-walking sluice/ and fails if a name it finds is neither swept by the pattern above,
    # nor listed below, nor named in that test's own short allow-list of variables that need no
    # sandboxing at all. So this list cannot silently go stale -- and a file list beside it
    # would be one more thing that could.
    %w[VAULT_DIR SEEN_DB TRIAGE_AUDIT DOSSIER_DIR].each do |k|
      ENV.delete(k)
    end
    ENV["HOME"] = testpath
    # All three XDG rungs. sluice/core/paths.py's `resolve()` falls through to the matching
    # rung the instant the explicitly-named var above is deleted: SEEN_DB/SLUICE_HEALTH/
    # TRIAGE_AUDIT/SLUICE_DISABLED to XDG_STATE_HOME, SLUICE_CONFIG to XDG_CONFIG_HOME, and
    # DOSSIER_DIR to XDG_CACHE_HOME -- leaving any one of these three unset here would let that
    # rung fall through to the maintainer's REAL XDG directory instead of this sandbox.
    ENV["XDG_CONFIG_HOME"] = testpath/"config"
    ENV["XDG_STATE_HOME"] = testpath/"state"
    ENV["XDG_CACHE_HOME"] = testpath/"cache"

    assert_match version.to_s, shell_output("#{{bin}}/job-sluice --version")

    # `doctor --offline` exits 0 on a clean, unconfigured machine, which is exactly what this
    # one is. #243's contract, stated in sluice/core/doctor.py::DoctorReport.exit_code: a
    # component the user has not SUPPLIED yet is SETUP and never reaches the exit code, so no
    # vault directory, no `claude` CLI and no `render` extra are all still 0. Non-zero means
    # something they DID configure is broken.
    #
    # THIS LITERAL WAS `1` FOR TWO RELEASES, and the cost was the whole channel. 2.7.0's
    # `feat(doctor): a verdict by default, and exit 0 on a clean install` inverted the
    # contract; this number did not move; `brew test` then failed the `homebrew` job on 2.7.0
    # and 2.8.0 while every other channel shipped from those same runs, so the public tap went
    # on serving the last version whose job passed. The justification lived only in a comment
    # here that ended "Measured." -- true when written, and nothing could tell when it stopped
    # being true, which is CLAUDE.md's "a comment that states a mechanism needs a row that
    # falsifies it" applied to a release channel.
    # tests/test_homebrew_formula.py::test_the_formula_expects_the_real_clean_install_exit_code
    # is that row: it RUNS `doctor --offline` and compares, so the formula's expectation and
    # the program's behaviour can no longer drift apart in silence.
    #
    # The code is passed EXPLICITLY even though 0 is `shell_output`'s default. The claim being
    # made is that a clean install exits 0, and a claim this channel has already been broken by
    # should be written down where it can be read and checked, not left implicit in a default.
    #
    # This is the only place a release RUNS the shipped binary on a fresh machine and holds it
    # to a status. ci.yml's container smoke deliberately asserts the status in neither
    # direction -- it checks the report is positively present instead -- so it could not have
    # caught this, and `release-please.yml` runs no doctor at all.
    report = shell_output("#{{bin}}/job-sluice doctor --offline", 0)
    assert_match "job-sluice doctor", report

    # THE PAYOFF, POSITIVE rather than a refutation of "dead": core/app.py's
    # `if cv_cfg is not None:` drops the renderer row ENTIRELY on any load_cv_config error,
    # with exit 1 and the banner intact -- so refuting "dead" passes when the row is merely
    # ABSENT. A negative guard that finds nothing is indistinguishable from success.
    # Row format is `f"{{component:12}} {{subject:32}} {{state:9}} ..."` (cli.py::_print_doctor).
    assert_match(/renderer\\s+cv\\.renderer\\s+ok/, report)

    # ...and independently of sluice's own output format, so a change to doctor's printing
    # cannot silently retire the check above.
    system libexec/"bin/python", "-c",
           "import weasyprint; weasyprint.HTML(string='<p>x</p>').write_pdf('t.pdf')"
    assert_path_exists testpath/"t.pdf"

    # The WeasyPrint probe above proves only the `render` extra. `exclude_packages` above
    # relies on the BREWED interpreter's own site-packages to supply pydantic/rpds-py/cffi --
    # and `mcp` in particular carries a hard pydantic version floor -- so a skew between what
    # this formula ships and what a brewed interpreter's homebrew-core dependencies actually
    # provide would surface as a user-facing ImportError on `mcp`/`google`/`completion` with
    # this job still green. Import each of the other three extras' top-level module the same
    # way the render extra is proven above, against the SAME installed libexec interpreter.
    system libexec/"bin/python", "-c", "import mcp, googleapiclient, argcomplete"
  end
end
'''
