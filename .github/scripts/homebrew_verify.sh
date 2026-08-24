#!/usr/bin/env bash
# Render, resource-fill, audit, install and test the job-sluice Homebrew formula, WITHOUT the
# cross-repo write token (#104, PR 6 of 7, split from the original homebrew_bump.sh).
#
# THE SPLIT. The original single script held a `contents: write` token for the OTHER repo
# (MrReasonable/homebrew-tap) in its environment for its entire run -- including
# `brew install --build-from-source`, which executes the pip/PEP 517 build backend of every one
# of the ~45 resource sdists in the closure. A compromised transitive build dependency there
# would see a live write token for a public tap it has no business touching. Splitting at the
# `brew test` boundary removes that: this script does everything through `brew test` and never
# reads TAP_TOKEN at all; homebrew_push.sh does only the commit and push, and is the one place
# the token is minted into and read from. The two share the tap checkout on disk (same job,
# same runner) and the tap directory is a derivation both scripts compute IDENTICALLY from the
# same formula -- see TAP_DIR below -- once each has TAP_OWNER, which itself now crosses rather
# than being independently hardcoded in both (see below). TARGET_BRANCH, DEFAULT_BRANCH and
# TAP_OWNER all need to cross the boundary, since each depends on state only this script
# observes or normalises (the tap's remote-resolved default branch, which of it or a scratch
# branch PUSH_TARGET picks, and the lower-cased owner); all three are exported via $GITHUB_ENV.
# homebrew_push.sh deliberately does not re-derive DEFAULT_BRANCH on its own -- see its own
# header comment for why that would silently reintroduce the bug IMPORTANT-3 closes.
#
# THE TAP IS NOT AN actions/checkout. `brew` resolves a formula through the tap directory under
# $(brew --repository)/Library/Taps/, which is OUTSIDE $GITHUB_WORKSPACE -- and actions/checkout
# refuses a path outside the workspace. So the tap is obtained the way brew obtains one. Cloning
# it here needs no credential at all: it is a public read.
#
# `brew tap-new` is NOT used, for two independent reasons, both measured against the installed
# Homebrew (dev-cmd/tap-new.rb:95-99): it unconditionally writes .github/dependabot.yml and
# three workflows -- only `git init` is behind --no-git -- and one of them runs
# `brew bump --open-pr` daily, which would be a SECOND automated writer of a formula this
# design declares machine-owned. Independently, an App token scoped `contents: write` cannot
# push anything under .github/workflows/ at all.
set -euo pipefail

: "${VERSION:?}"

# TAP_OWNER is deliberately NOT required here alongside VERSION: the PUSH_TARGET validation
# below must be the FIRST thing that can fail, so a caller that got PUSH_TARGET wrong sees THAT
# error rather than an unrelated one about a variable this section does not touch yet --
# tests/test_release_publish_wiring.py executes this script directly with only VERSION and an
# empty PATH set and pins exactly that ordering. TAP_OWNER is required just below, at its own
# first use, once PUSH_TARGET has already been validated.
#
# PUSH_TARGET decides which branch of the tap homebrew_push.sh will push, and it is an
# EXPLICIT, REQUIRED input rather than something inferred from repo state. The two callers of
# this pair of scripts want DIFFERENT answers to the identical observable question "does
# Formula/job-sluice.rb exist in the tap right now": the release job must ALWAYS land on the
# tap's default branch -- nothing merges a `bump-X.Y.Z` branch, and `brew install` resolves a
# tap's DEFAULT branch, so pushing a scratch branch there would leave users on the bootstrap
# version forever with the job reporting green. The dry run, by contrast, wants its first-ever
# invocation (the bootstrap) to land on the default branch and every later invocation to land
# on a scratch branch, because a dry run must never become the tree of record for a release it
# did not cut. One inferred observable cannot serve both answers -- which is exactly the defect
# this replaces: once the dry run had bootstrapped the tap once, a FILE-existence check made
# every later run, including the release job's, take the "formula already exists" branch and
# push a scratch branch nothing merges. Fail loudly here, before any of the expensive work
# below runs, rather than well into a 20+ minute build.
case "${PUSH_TARGET:-}" in
  default | auto) ;;
  *)
    echo "::error::PUSH_TARGET must be 'default' or 'auto', got '${PUSH_TARGET:-<unset>}'." \
      "'default' always pushes the tap's default branch (the release job). 'auto' pushes the" \
      " default branch only when Formula/job-sluice.rb does not yet exist in the tap (the" \
      " bootstrap run) and a bump-\$VERSION scratch branch otherwise (every later dry run)." \
      >&2
    exit 1
    ;;
esac

: "${TAP_OWNER:?}"
# Lower-cased, not re-hardcoded: both workflows already mint the cross-repo App token with
# `owner: ${{ github.repository_owner }}` (exact GitHub casing, e.g. `MrReasonable`), and this
# script used to carry an independent hardcoded `"mrreasonable"` literal -- two derivations of
# the identical fact, the exact drift shape the DEFAULT_BRANCH comment above rejects. `TAP_OWNER`
# now arrives as that same `github.repository_owner` value, passed via this step's `env:`; the
# lower-case here is Homebrew's own convention for a tap's local directory name, applied in
# bash rather than in the YAML (GitHub Actions expressions have no built-in case-folding
# function) so this stays the ONE place the value is transformed.
TAP_OWNER="${TAP_OWNER,,}"
# FIXED derivation, computed identically in homebrew_push.sh -- see this file's header.
TAP_DIR="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap"
FORMULA_REL="Formula/job-sluice.rb"

# The sdist URL and digest for the EXACT released version, from the index that job just
# published to. Never a guessed filename: PyPI's path contains a content hash.
read -r SDIST_URL SDIST_SHA < <(python3 - "$VERSION" <<'PY'
import json, sys, urllib.request
version = sys.argv[1]
with urllib.request.urlopen(f"https://pypi.org/pypi/job-sluice/{version}/json", timeout=60) as r:
    data = json.load(r)
sdists = [u for u in data["urls"] if u["packagetype"] == "sdist"]
if len(sdists) != 1:
    raise SystemExit(f"expected exactly one sdist for {version}, found {len(sdists)}")
print(sdists[0]["url"], sdists[0]["digests"]["sha256"])
PY
)

# A genuine clone failure (tap missing, network) must fail HERE, loudly -- not fall through to
# a non-git directory that then dies twenty-plus minutes later at `git checkout -B` with a
# message that never mentions the clone, and that is indistinguishable from "the tap has no
# formula yet", the observable the `auto` bootstrap arm below keys on. The `|| [ -d ... ]` half
# keeps the ONE legitimate case this tolerates: re-running against a tap this same job (or a
# prior run) already cloned.
git clone "https://github.com/${TAP_OWNER}/homebrew-tap.git" "$TAP_DIR" 2>/dev/null \
  || [ -d "$TAP_DIR/.git" ] \
  || { echo "::error::could not obtain the ${TAP_OWNER}/homebrew-tap checkout (clone failed and no existing .git directory was found at $TAP_DIR)"; exit 1; }
mkdir -p "$TAP_DIR/Formula"

# DEFAULT_BRANCH comes from the REMOTE, never from this checkout's own local HEAD. The clone
# guard above deliberately tolerates a PRE-EXISTING checkout (this same job's own prior run, or
# a prior dry run's) -- and that checkout's local HEAD can be sitting on a `bump-X.Y.Z` scratch
# branch a prior run left it on. Measured: with a tap checkout left on `bump-9.9.9`, a
# local-HEAD read resolves DEFAULT_BRANCH to `bump-9.9.9`, so `PUSH_TARGET=default` -- which
# trusts DEFAULT_BRANCH completely -- targets that scratch branch. That is the exact
# orphan-branch harm PUSH_TARGET was introduced to prevent, reached through a different
# observable. `git remote set-head origin --auto` asks the remote which branch is default and
# writes refs/remotes/origin/HEAD accordingly; safe to re-run against a checkout that already
# has it set, which is why its own failure is swallowed below rather than treated as fatal --
# the `symbolic-ref` read immediately after is what must succeed, and is where this fails
# loudly: an unresolvable default branch must stop the run here rather than silently fall back
# to a guessed "main" that may not even be this tap's actual default branch name.
git -C "$TAP_DIR" remote set-head origin --auto >/dev/null 2>&1 || true
if ! DEFAULT_BRANCH="$(git -C "$TAP_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"; then
  echo "::error::could not determine ${TAP_OWNER}/homebrew-tap's default branch from" \
    "refs/remotes/origin/HEAD (git remote set-head origin --auto did not resolve it)." \
    "Refusing to guess: an unresolved default branch here is exactly the orphan-branch harm" \
    "PUSH_TARGET exists to prevent." >&2
  exit 1
fi
DEFAULT_BRANCH="${DEFAULT_BRANCH#origin/}"

# RESEAT THE CHECKOUT ITSELF, not just the NAME resolved above. The paragraph above fixes what
# DEFAULT_BRANCH resolves TO; it says nothing about what commit this checkout's working tree is
# actually sitting on, and the clone guard's pre-existing-checkout tolerance can leave that on a
# `bump-X.Y.Z` scratch commit from a prior run. Left un-reseated, homebrew_push.sh's
# `checkout -B "$TARGET_BRANCH"` resets a LOCAL branch to whatever HEAD currently is -- so when
# PUSH_TARGET=default, it would reset the tap's real default branch onto that leftover scratch
# commit, and the fast-forward push in push.sh accepts it: a dry run's history becoming the tree
# of record, the exact harm PUSH_TARGET exists to prevent, reached through the base COMMIT
# rather than the branch NAME the paragraph above closes. Fetching and force-checking-out the
# default branch here -- unconditionally, before the bootstrap-observable check below and before
# anything is rendered, regardless of which branch PUSH_TARGET ultimately resolves TARGET_BRANCH
# to -- means a scratch TARGET_BRANCH push.sh creates later is always cut from this known-good,
# just-fetched tip, never from whatever the checkout happened to be sitting on.
#
# ONE ABORT CASE, stated because the paragraph above would otherwise claim more than the code
# does: `git checkout -B` is not a discard. It refuses when a TRACKED file carries local
# modifications the target tree would overwrite. On an ephemeral GitHub-hosted runner that is
# unreachable -- the tap is freshly cloned every run, so nothing is ever locally modified -- but
# on a persistent or self-hosted one, a re-run after a mid-verify failure that left a rendered
# but uncommitted Formula/job-sluice.rb behind aborts HERE, before anything is re-rendered,
# rather than reseating. That is LOUD (`set -e` stops the step and git names the file), and it
# is the direction to fail in: silently discarding a modification nobody asked to discard is the
# clobber this project rejects everywhere else. Recovery is a human's -- clean or delete the tap
# checkout on that runner.
git -C "$TAP_DIR" fetch origin "$DEFAULT_BRANCH"
git -C "$TAP_DIR" checkout -B "$DEFAULT_BRANCH" "origin/$DEFAULT_BRANCH"

if [ "$PUSH_TARGET" = "default" ]; then
  TARGET_BRANCH="$DEFAULT_BRANCH"
elif [ -f "$TAP_DIR/$FORMULA_REL" ]; then
  # BOOTSTRAP OBSERVABLE, auto mode only: whether the FORMULA exists, not whether the repo has
  # commits -- a tap carrying only an auto-created README has commits, so a commit-based check
  # would see those commits, conclude this is NOT the bootstrap, and take the SCRATCH arm
  # instead -- pushing bump-$VERSION while update-python-resources has no existing formula to
  # edit. This observable now decides only which branch `auto` resolves to; `default` (the
  # release job) never consults it at all.
  TARGET_BRANCH="bump-${VERSION}"
else
  TARGET_BRANCH="$DEFAULT_BRANCH"
fi

# Cross the process boundary to homebrew_push.sh, which runs as a later step in the same job.
# DEFAULT_BRANCH crosses too, alongside TARGET_BRANCH: push.sh needs it to choose between a
# fast-forward-only push (the default branch) and --force-with-lease (a scratch branch), and it
# must be the SAME value computed the SAME way -- re-deriving it independently in push.sh would
# reintroduce this exact defect there. This script DOES now check out and reseat DEFAULT_BRANCH
# itself (the RESEAT block above), but push.sh's OWN `checkout -B "$TARGET_BRANCH"` moves local
# HEAD again, onto TARGET_BRANCH -- so a re-derivation from local HEAD at that later point would
# read the wrong branch whenever TARGET_BRANCH differs from DEFAULT_BRANCH (every scratch-branch
# run). Crossing the value explicitly is simpler than depending on that ordering, and two
# independent derivations of one fact can silently drift -- see push.sh's own header comment for
# the fuller history. TAP_OWNER crosses too, ALREADY LOWER-CASED, so push.sh reads the one value
# this script already normalised rather than repeating its own `${TAP_OWNER,,}` transform on the
# workflow's raw `github.repository_owner` -- one transform, one place.
{
  echo "TARGET_BRANCH=${TARGET_BRANCH}"
  echo "DEFAULT_BRANCH=${DEFAULT_BRANCH}"
  echo "TAP_OWNER=${TAP_OWNER}"
} >> "$GITHUB_ENV"

# render() takes no `version` argument -- `$VERSION` is not passed here. See
# render_homebrew_formula.py's `render()` docstring for why: nothing in the rendered formula
# reads it, and a parameter that reads as live but is not is exactly the trap CLAUDE.md's
# "fail loudly at construction" rule targets.
python3 - "$SDIST_URL" "$SDIST_SHA" "$TAP_DIR/$FORMULA_REL" <<'PY'
import sys, pathlib
sys.path.insert(0, ".")
from scripts.render_homebrew_formula import render
url, sha, out = sys.argv[1:4]
pathlib.Path(out).write_text(render(sdist_url=url, sha256=sha))
PY

# --ignore-main-package-cooldown is REQUIRED, not optional: this job runs minutes after the
# PyPI upload and the resolver otherwise refuses a package that new. Homebrew honours the flag
# for non-official taps only, which ours is.
brew update-python-resources --version "$VERSION" --ignore-main-package-cooldown \
  "${TAP_OWNER}/tap/job-sluice"
brew audit --strict --online "${TAP_OWNER}/tap/job-sluice"
brew install --build-from-source "${TAP_OWNER}/tap/job-sluice"
brew test "${TAP_OWNER}/tap/job-sluice"
