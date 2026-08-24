#!/usr/bin/env bash
# Commit and push the already-rendered, already-verified job-sluice Homebrew formula (#104,
# PR 6 of 7, split from the original homebrew_bump.sh -- see homebrew_verify.sh's header for
# the full reasoning). This is the ONLY one of the pair that reads TAP_TOKEN: everything
# through `brew test` already ran, in homebrew_verify.sh, with no token in its environment at
# all. Only now. A green job that pushed an unverified formula would repeat the deb/rpm
# failure exactly: three root-only container runs certified a package no ordinary user could
# run.
set -euo pipefail

: "${VERSION:?}" "${TAP_TOKEN:?}" "${TARGET_BRANCH:?}" "${DEFAULT_BRANCH:?}" "${TAP_OWNER:?}"

# TAP_OWNER is NOT re-derived or re-hardcoded here -- it arrives via $GITHUB_ENV, already
# lower-cased by homebrew_verify.sh from the workflow's `github.repository_owner`. This used to
# be a second hardcoded `"mrreasonable"` literal, independent of the first -- exactly the
# two-derivations-of-one-fact drift the DEFAULT_BRANCH paragraph above already rejects for a
# different value, for the identical reason: the two could silently disagree, and the one that
# matters is whichever homebrew_verify.sh computed.
# TAP_DIR is otherwise a FIXED derivation, identical to homebrew_verify.sh's -- the tap checkout
# it produced is still on disk here, in the same job on the same runner.
TAP_DIR="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap"
FORMULA_REL="Formula/job-sluice.rb"

# DEFAULT_BRANCH is NOT re-derived here -- it arrives via $GITHUB_ENV, computed once by
# homebrew_verify.sh. It used to be read again here from this checkout's own local HEAD, on
# the reasoning that "verify.sh never checks anything out in the tap, so the tap's HEAD is
# still its default branch here" -- false in exactly the case verify.sh's own clone-guard
# comment says it tolerates: a PRE-EXISTING checkout left on a `bump-X.Y.Z` scratch branch by a
# prior run. Reading local HEAD in that state would silently take THIS script's own
# fast-forward-only-vs-force-with-lease branch below on a wrong verdict -- forcing a push to
# what is actually the default branch, which the comment on that arm below says must never
# happen. Deriving it identically here (rather than trusting the crossed value) was rejected
# for the same reason: two independent derivations of the same fact can drift, and the one that
# matters is whichever homebrew_verify.sh used to compute TARGET_BRANCH in the first place.

git -C "$TAP_DIR" checkout -B "$TARGET_BRANCH"
git -C "$TAP_DIR" add "$FORMULA_REL"

# A rendered-and-resource-filled formula BYTE-IDENTICAL to the tap's already-committed one is
# the normal second dispatch of the dry run at an unchanged version, or a "Re-run all jobs" on a
# release whose push already landed -- not a failure. Without this, `git commit` below exits 1
# on an empty index and `set -e` fails the whole step after the full 20+ minute
# render/audit/install/test run that already succeeded.
if git -C "$TAP_DIR" diff --cached --quiet; then
  echo "::notice::the rendered formula is byte-identical to ${TAP_OWNER}/homebrew-tap's -- nothing to commit or push."
  exit 0
fi

# GitHub's canonical bot noreply is `<app-id>+<slug>[bot]@users.noreply.github.com`. The
# numeric app id is a secret this repo does not expose in a workflow file, so this uses the
# slug-only form: the commit's author will show as unlinked in GitHub's UI until the id is
# supplied. Only on `commit` -- `checkout -B` makes no commit and needs no identity, so the
# identical `-c user.name`/`-c user.email` pair that used to sit on it there was inert.
git -C "$TAP_DIR" \
  -c user.name="sluice-release-please[bot]" \
  -c user.email="sluice-release-please[bot]@users.noreply.github.com" \
  commit -m "job-sluice ${VERSION}"

PUSH_URL="https://x-access-token:${TAP_TOKEN}@github.com/${TAP_OWNER}/homebrew-tap.git"

if [ "$TARGET_BRANCH" = "$DEFAULT_BRANCH" ]; then
  # The default branch must only ever fast-forward: a rejection here means something else
  # landed on it since this run started, which needs a human to look rather than be forced
  # past.
  git -C "$TAP_DIR" push "$PUSH_URL" "$TARGET_BRANCH"
else
  # --force-with-lease for the SCRATCH branch ONLY, never the default branch above. Without
  # it, a scratch branch left over at a different commit (a prior dry run for the same
  # version, most likely) is a non-fast-forward rejection at this very last line, after the
  # full render/audit/install/test already ran. A scratch branch this job just re-created via
  # `checkout -B` has exactly one possible prior remote state worth overriding -- an earlier
  # attempt at the SAME version -- never a human's independent work, since nothing else pushes
  # to a `bump-*` branch.
  git -C "$TAP_DIR" push --force-with-lease "$PUSH_URL" "$TARGET_BRANCH"
fi
