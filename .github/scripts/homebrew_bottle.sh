#!/usr/bin/env bash
# The `bottle` job's body (#279): build, bottle, and prove the bottle pours on this runner.
#
# Runs third-party code and references no secret. The steps and their reasons are the spec's sections 4
# and 6a; tests/test_release_publish_wiring.py pins their order, occurrence by occurrence.
set -euo pipefail
# Auto-update off: `brew install` would otherwise update the tap and, if its default branch has moved
# since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it stashed, so the
# bottle would be built from the tap's formula rather than this run's (cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1
# Autoremove off: `brew uninstall` below would otherwise remove every dependency the build just installed,
# and the pour would download them all again.
export HOMEBREW_NO_AUTOREMOVE=1

: "${GITHUB_WORKSPACE:?}" "${RUNNER_TEMP:?}" "${TAP_OWNER:?}" "${VERSION:?}" "${ROOT_URL:?}" "${DECLARED_TAG:?}" "${FORMULA_IN:?}" "${BOTTLE_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

cp "$FORMULA_IN" "$TAP_FORMULA"
brew install --build-bottle "$FORMULA_REF"
brew test "$FORMULA_REF"

mkdir -p "$BOTTLE_OUT"
cd "$BOTTLE_OUT"
# --no-rebuild: otherwise rebuild is derived from the tap's origin/HEAD formula, and a dry run of an
# already-published version would build a differently named asset (spec, section 4).
brew bottle --json --no-rebuild --root-url="$ROOT_URL" "$FORMULA_REF"
set -- ./*.bottle.json
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "::error::expected exactly one bottle JSON in $BOTTLE_OUT, found: $*"
  exit 1
fi
BOTTLE_JSON="$1"
python3 -P "$BOTTLES" produced-tag --json "$BOTTLE_JSON"
brew bottle --merge --write --no-commit "$BOTTLE_JSON"

# The negative control: the built keg must read as built, proving the pour check can refuse.
brew info --json=v2 "$FORMULA_REF" > "$RUNNER_TEMP/info-built.json"
python3 -P "$BOTTLES" pour-check --info "$RUNNER_TEMP/info-built.json" --expect-built
brew uninstall job-sluice

# Seed Homebrew's download cache: the release asset does not exist yet, and Homebrew keeps a cached
# file when the URL cannot be resolved (spec, section 6a).
set -- ./*.bottle.tar.gz
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "::error::expected exactly one bottle in $BOTTLE_OUT, found: $*"
  exit 1
fi
BOTTLE_TAR="$1"
CACHE_PATH="$(brew --cache --bottle-tag="$DECLARED_TAG" "$FORMULA_REF")"
mkdir -p "$(dirname "$CACHE_PATH")"
cp "$BOTTLE_TAR" "$CACHE_PATH"
brew install "$FORMULA_REF"
brew info --json=v2 "$FORMULA_REF" > "$RUNNER_TEMP/info-poured.json"
python3 -P "$BOTTLES" pour-check --info "$RUNNER_TEMP/info-poured.json"
brew test "$FORMULA_REF"
brew linkage --test job-sluice
