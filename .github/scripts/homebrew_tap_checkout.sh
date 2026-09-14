#!/usr/bin/env bash
# Check out the tap for an untrusted job (#279): formula, bottle and prove.
#
# DERIVES NOTHING. TAP_OWNER and BASE_SHA arrive from `plan`, the one job that derives them
# (docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 2). Checking out BASE_SHA,
# not a freshly fetched tip, keeps every job on the commit `plan` observed.
#
# THE TAP IS NOT AN actions/checkout. `brew` resolves a formula through the tap directory under
# $(brew --repository)/Library/Taps/, outside $GITHUB_WORKSPACE, where actions/checkout refuses to
# write. Cloning needs no credential: the tap is public.
#
# `brew tap-new` is never used. It unconditionally writes .github/dependabot.yml and three
# workflows, one running `brew bump --open-pr` daily: a second automated writer of a formula this
# channel owns. Independently, an App token scoped `contents: write` cannot push workflows at all.
set -euo pipefail
# Auto-update off, as in every other macOS script: no `brew` command in this job may update the taps
# and move this fresh clone off BASE_SHA, whichever brew command a later edit adds here.
export HOMEBREW_NO_AUTO_UPDATE=1

: "${TAP_OWNER:?}" "${BASE_SHA:?}"

TAP_DIR="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap"
if [ -e "$TAP_DIR" ]; then
  echo "::error::$TAP_DIR already exists; this job expects a fresh runner."
  exit 1
fi
git clone --no-checkout "https://github.com/${TAP_OWNER}/homebrew-tap.git" "$TAP_DIR"
git -C "$TAP_DIR" checkout --detach "$BASE_SHA"
# Homebrew refuses to load a formula from a tap that is not trusted
# (Library/Homebrew/trust.rb::require_trusted_formula!) unless the command line names it in full.
# `prove`'s merge names only bottle JSONs, and `bottle`'s `brew uninstall` removes the per-formula trust
# its install recorded, so the whole tap is trusted here, once, for every job. A tap-level entry survives
# that uninstall (Library/Homebrew/cmd/uninstall.rb keeps a formula's entry when its tap is trusted).
# The refusal itself names `brew trust`, so a Homebrew without that command has no such gate to pass.
if brew trust --help >/dev/null 2>&1; then
  brew trust --tap "${TAP_OWNER}/tap"
fi
mkdir -p "$TAP_DIR/Formula"
