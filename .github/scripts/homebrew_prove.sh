#!/usr/bin/env bash
# The `prove` job's body (#279): merge every bottle into the formula, and fetch every tag from its
# real URL before the formula that points at it is pushed.
#
# Runs third-party code and references no secret: it evaluates the merged formula, which is why `push`
# validates that text again as data (spec, section 6b).
set -euo pipefail
# Auto-update off: a brew command that auto-updates would update the tap and, if its default branch
# has moved since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it
# stashed (Homebrew's cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1

: "${GITHUB_WORKSPACE:?}" "${TAP_OWNER:?}" "${PLATFORMS:?}" "${FORMULA_IN:?}" "${JSON_DIR:?}" "${MERGED_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

cp "$FORMULA_IN" "$TAP_FORMULA"
(cd "$JSON_DIR" && brew bottle --merge --write --no-commit ./*.bottle.json)
brew style --formula "$FORMULA_REF"
python3 -P "$BOTTLES" merged-tags --formula "$TAP_FORMULA"

# Read into a variable first: a failing command inside a `< <(...)` feeding the loop would leave the
# loop silently empty, and `set -e` stops on a failing command substitution in an assignment.
TAGS="$(python3 -P "$BOTTLES" tags)"
for tag in $TAGS; do
  cache_path="$(brew --cache --bottle-tag="$tag" "$FORMULA_REF")"
  rm -f "$cache_path"
  # --force clears any cached copy; the file check below is required because `brew fetch
  # --bottle-tag` exits 0 for a tag the formula does not carry.
  brew fetch --force --bottle-tag="$tag" "$FORMULA_REF"
  python3 -P "$BOTTLES" cache-check --formula "$TAP_FORMULA" --tag "$tag" --file "$cache_path"
done

mkdir -p "$MERGED_OUT"
cp "$TAP_FORMULA" "$MERGED_OUT/job-sluice.rb"
