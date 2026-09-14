#!/usr/bin/env bash
# The `formula` job's body (#279): render the formula, fill its resources, audit it.
#
# Runs third-party code (PyPI resolution, Homebrew's gems) and references no secret. Filled ONCE: two
# runners resolving minutes apart could land on different resource trees, and every bottle must be
# built from one formula text (spec, section 2). SDIST_URL and SDIST_SHA256 come from `plan`.
set -euo pipefail
# Auto-update off: a brew command that auto-updates would update the tap and, if its default branch
# has moved since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it
# stashed (Homebrew's cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1

: "${GITHUB_WORKSPACE:?}" "${TAP_OWNER:?}" "${VERSION:?}" "${SDIST_URL:?}" "${SDIST_SHA256:?}" "${FORMULA_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

python3 -P "$BOTTLES" render --out "$TAP_FORMULA"

# --ignore-main-package-cooldown is REQUIRED: this runs minutes after the PyPI upload, and the
# resolver otherwise refuses a package that new. It covers our own package only: a dependency floor
# naming a release younger than a day still fails, reported by Homebrew as an unhelpful "Unable to
# determine dependencies". The diagnostic below re-runs the resolution without the cooldown and says
# which case this is. Every lookup in it is guarded: under `set -e` an unguarded failing lookup
# would kill the diagnostic before it printed anything.
if ! brew update-python-resources --version "$VERSION" --ignore-main-package-cooldown "$FORMULA_REF"; then
  echo "::group::diagnosing the resource-resolution failure"
  diag_fail() {
    echo "::endgroup::"
    echo "::error::resource resolution failed, and the cooldown diagnostic could not run: $1. Read Homebrew's own output above; it is the only evidence available for this failure."
    exit 1
  }
  if ! EXTRAS="$(grep -oE 'job-sluice\[[a-z,]+\]' "$TAP_FORMULA" | head -1 | sed 's/.*\[//;s/\]//')" || [ -z "$EXTRAS" ]; then
    diag_fail "could not read the extras from the rendered formula"
  fi
  if ! brew_prefix="$(brew --prefix python@3.14)" || [ -z "$brew_prefix" ]; then
    diag_fail "could not locate the brewed python@3.14 prefix"
  fi
  brew_py="${brew_prefix}/libexec/bin/python"
  [ -x "$brew_py" ] || brew_py="${brew_prefix}/bin/python3.14"
  if [ ! -x "$brew_py" ]; then
    diag_fail "no executable python under ${brew_prefix}"
  fi
  if "$brew_py" -m pip install -q --disable-pip-version-check --dry-run --ignore-installed --report=/dev/null "job-sluice[${EXTRAS}] @ ${SDIST_URL}"; then
    echo "::endgroup::"
    echo "::error::resource resolution failed ONLY under Homebrew's dependency cooldown: a dependency floor in pyproject.toml names a release less than 24 hours old. No fix is needed. Once that release has aged past 24 hours, re-run the calling run's failed jobs (for a release, re-running the whole workflow never reaches these jobs). The pip error above names the package and the versions it could see."
  else
    echo "::endgroup::"
    echo "::error::resource resolution failed with AND without Homebrew's dependency cooldown, so the cooldown is not the cause. Read the pip output above."
  fi
  exit 1
fi

brew audit --strict --online "$FORMULA_REF"

mkdir -p "$FORMULA_OUT"
cp "$TAP_FORMULA" "$FORMULA_OUT/job-sluice.rb"
