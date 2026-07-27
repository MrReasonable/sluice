# rulesync drift gate — enforce what `.gitignore` currently only asks for (#2)

**Status:** design approved 2026-07-27.
**Issue:** #2 — `chore: generate AI assistant rules from a single source (rulesync)`. The issue's
headline proposal already shipped; its body is stale and is rewritten in place as part of this work
(see *The issue text is wrong* below).
**Sub-apps:** none. This touches `scripts/`, `.github/workflows/ci.yml`, `.github/dependabot.yml`,
`.gitignore`, and `tests/` only. No `sluice/` change.

Three things an implementer will be tempted to write back, each of which makes the gate certify
nothing:

- **A porcelain check alone is provably insufficient.** Measured: a malformed `.rulesync/hooks.json`
  on a fresh clone silently drops 17 files, exits **0**, and leaves `git status --porcelain`
  **empty**. Exit code and porcelain both pass green on the exact failure the gate exists to catch.
- **An absent feature term is a count of zero, not a parse failure to skip.** When hooks drop out,
  rulesync's summary omits the term entirely rather than printing `0 hooks`. A parser that regexes
  `(\d+) hooks`, finds no match, and moves on reports success on the failing input.
- **The counts are only valid on a fresh tree.** rulesync skips writing a file whose content already
  matches. On an already-generated tree the same command reports **14** files, not 243. A guard run
  against a working copy that has been generated in will fail for a reason unrelated to any defect.

## Problem

`.rulesync/` is canonical and tracked; every AI-tool output it generates is a gitignored build
artifact. `.gitignore:52-64` carries the maintenance obligation in prose:

> Re-audit this list on a rulesync version bump (the target set is what changes) AND whenever a new
> rulesync FEATURE is turned on

Nothing enforces it. That comment exists because the obligation has already been discovered the hard
way once — enabling `.rulesync/hooks.json` made the documented command emit 17 hook files across 15
directories where it previously emitted none, and two landed in the **tracked** `.github/` tree. The
next such surprise has nothing to catch it.

## The issue text is wrong, and the goal it states is unachievable

Issue #2's body opens "The repo carries **no** agent/assistant instructions at all." That was true
when filed and is false now: `.rulesync/` holds one rules file, five subagents, four skills, and
`hooks.json`, and generates 243 files.

More importantly, the issue asks for "generation checked in CI so a hand-edit of a generated file
fails the build." **That cannot be built.** Generated outputs are gitignored (`.gitignore:35-101`),
so a hand-edit to `CLAUDE.md` is a local, untracked change that never reaches CI. No job can fail on
it. Tracking the 243 outputs instead was considered and rejected — it buys the literal goal at the
cost of a large generated diff in every PR.

This is recorded here and in the rewritten issue body precisely so nobody re-adds the goal. Grep the
claim, not the code: as of 2026-07-27 the stale claim lives **only** in issue #2's body — no tracked
file repeats it (`git grep -iE 'no (agent|assistant) (instructions|rules)|tribal knowledge'` is
empty).

## Measurements

Every number below was measured on 2026-07-27 against rulesync 9.6.3, not read from documentation.
Each is reproducible by cloning to a temp dir and running the documented command.

| Probe | Command / mutation | Result |
|---|---|---|
| Baseline, fresh clone | `npx rulesync@9.6.3 generate -t '*' -f '*'` | exit 0, **243** files: `20 rules + 114 subagents + 92 skills + 17 hooks`; porcelain **empty** |
| Determinism | Baseline repeated on a second independent fresh clone | identical 243 and identical breakdown; porcelain empty |
| Already-generated tree | Baseline on a tree that has already been generated | exit 0, **14** files (4 rules → `AGENTS.md`, 10 subagents → `.github/agents/`) |
| Skip-if-identical | Delete `CLAUDE.md`, re-run | count 14 → **15**, `CLAUDE.md` reappears in the written list, **byte-identical** to the deleted copy |
| Malformed `.rulesync/rules/CLAUDE.md` | Replace frontmatter with invalid YAML | **exit 1**, no files written |
| `.rulesync/` absent | `mv .rulesync` away | **exit 1** |
| Malformed `.rulesync/hooks.json`, fresh clone | `printf '{ broken json' > .rulesync/hooks.json` | **exit 0**, **226** files, `.github/hooks/` never created, porcelain **empty** |

Two observations carried forward:

- The rules body is shared across targets — `AGENTS.md` and `.rules` hash identically
  (`700d3985…`) — and `AGENTS.md` is reported as written four times in a single run. The *mechanism*
  behind the 14-file rewrite on an already-generated tree was **not** isolated; only the observation
  is relied on, and only for the conclusion that the guard must run on a fresh checkout.
- `.rulesync/` has no `commands/` directory, so `commands` never appears in the summary. The expected
  feature set is exactly `{rules, subagents, skills, hooks}`, and a *new* feature appearing is drift
  the guard must fail on, not tolerate.

## The two invariants

Asserted on a fresh checkout, in one CI job:

**I1 — Hygiene.** After running the documented generate command, `git status --porcelain` is empty.
Catches a gitignore gap when a version bump or a newly-enabled feature emits into a fresh path
(including into the tracked `.github/` tree), and catches a generated file that was force-added into
tracking.

**I2 — Completeness.** The run reports exactly `{rules: 20, subagents: 114, skills: 92, hooks: 17}`.
Catches the fail-open class that I1 provably misses.

I2 is pinned rather than merely non-zero because the version is pinned and `.rulesync/` is tracked,
so the output set is fully determined by tracked inputs. A per-feature non-zero check would pass a
partial drop (16 of 17 hooks). When the counts legitimately change — a new skill, a version bump —
the one-line diff to the expected map documents the blast radius of that edit, which is the
`.gitignore` re-audit the prose currently asks for and nothing performs.

## Settled decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Guard regen hygiene **and** completeness, not hygiene alone | I1 alone is green on the measured `hooks.json` failure |
| D2 | Pinned per-feature counts, not non-zero and not a path manifest | Catches partial drops; a manifest's only marginal catch (a path moving) is already covered by I1 |
| D3 | A `scripts/` Python guard with a pure parser plus a thin CI wrapper | Follows the `guard_no_bypass.py` precedent; keeps the parser unit-testable offline and mutation-testable, since CLAUDE.md's `compileall` line already covers `scripts/` |
| D4 | `package.json` + `package-lock.json` + `npm ci` | Integrity-hash pinning, matching the `--require-hashes` posture used for zizmor and the SHA-pinned actions |
| D5 | Rewrite issue #2's body in place | Keeps the issue number that memory and the commit trail reference, and records why the original framing was wrong |
| D6 | Add the npm ecosystem to Dependabot | A bump that fails until someone re-audits *is* the enforcement working; without it rulesync silently rots |
| D7 | Pin the documented version against `package.json` in an offline test | One small assertion prevents CI and the docs naming different versions |

## Components

### `package.json` + `package-lock.json`

Minimal, `rulesync@9.6.3` as the sole devDependency. Requires a `node_modules/` line in
`.gitignore` — root-anchored, consistent with the surrounding block.

### `scripts/guard_rulesync_drift.py`

Standard library only. Split pure from impure the way the repo does everywhere else:

- `parse_summary(text: str) -> dict[str, int]` — pure. Locates the single `All done!` summary line
  and parses its parenthetical into a feature → count mapping.
- `main` — takes the path to the captured generate output as its single positional argument, compares
  the parsed map against a frozen module-level `EXPECTED`, prints expected-vs-actual on mismatch, and
  exits non-zero. A path argument rather than stdin, so the CI step can `tee` the output into the job
  log *and* a file, keeping the failure readable without re-running the generator.

Four behaviours the parser must get right, each a way to certify nothing:

| Input | Required behaviour |
|---|---|
| A feature term absent from the parenthetical | That feature reports `0` → mismatch → fail |
| No `All done!` line at all | Hard error, never a pass |
| An unexpected feature present (e.g. `commands`) | Mismatch → fail |
| More than one summary line | Hard error |

`EXPECTED` carries a comment stating that it changes only when `.rulesync/` or the version pin
changes, and that updating it is the moment to re-audit `.gitignore`'s output list.

### CI job `rulesync` in `.github/workflows/ci.yml`

Checkout and setup-node at pinned SHAs with `persist-credentials: false`, then `npm ci`, then:

```yaml
- run: |
    set -euo pipefail
    npx rulesync generate -t '*' -f '*' | tee "$RUNNER_TEMP/rulesync-output.txt"
    python scripts/guard_rulesync_drift.py "$RUNNER_TEMP/rulesync-output.txt"
    if [ -n "$(git status --porcelain)" ]; then
      git status --porcelain
      exit 1
    fi
```

Three things in those seven lines are load-bearing, and each was wrong in an earlier draft:

- **`set -o pipefail` is mandatory.** Verified against GitHub's workflow-syntax reference on
  2026-07-27: a `run:` block with no `shell:` key runs as `bash -e {0}` — `-e` but *not* pipefail.
  (Only an explicit `shell: bash` gives `bash --noprofile --norc -eo pipefail {0}`; every existing
  job in `ci.yml` uses the bare `- run:` form.) In `npx … | tee`, `$?` is `tee`'s status, so without
  pipefail a rulesync **exit 1** — the measured signal for a malformed `.rulesync/rules/CLAUDE.md`,
  or for `.rulesync/` going missing — is swallowed and the step passes green. That is the same
  fail-open class the gate exists to close, reintroduced by the plumbing. Setting it inside the block
  works regardless of what the runner default may become.
- **The capture file lives in `$RUNNER_TEMP`, not the work tree.** A scratch file written beside the
  checkout is itself an untracked file, so the I1 check would trip over its own artefact.
- **I2 runs before I1.** A fail-open produces a *clean* tree — I1 passes on it. Running the porcelain
  check first would report success on a run that dropped 17 files.

Added to `ci-success`'s `needs:` so it blocks. **No `name:` key** — the check-run name is the job id
unless `name:` is set, and renaming a required check breaks the branch-protection binding.

The Dependabot entry (D6) follows the established shape in `.github/dependabot.yml`:
`package-ecosystem: npm`, `directory: /`, weekly, grouped `patterns: ["*"]`, and
`commit-message: {prefix: chore, include: scope}` to match the pip entry's conventional-commit
handling under `--rebase` merges.

### Tests — `tests/test_guard_rulesync_drift.py`

Offline and hermetic; the suite never shells out to npx. The two load-bearing fixtures are the real
summary lines captured during the measurements, reproduced here verbatim so the tests are
self-contained rather than dependent on a scratch file that no longer exists:

```
🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)
🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)
```

The second is what a malformed `hooks.json` actually produces — note it omits the term rather than
printing `0 hooks`. Cases:

- the 243 summary → passes
- the 226 summary → fails, **and specifically reports `hooks: 0`**
- no summary line → fails loudly
- an unexpected extra feature (e.g. `+ 3 commands`) → fails

Plus the D7 drift pin: the version in `package.json` matches the version named at
`.rulesync/rules/CLAUDE.md:14` and `.gitignore:37` (both currently `rulesync@9.6.3`). This test reads
the repo's own files, so it stays offline.

The guard is then mutation-tested. Mutate by **moving or deleting**, never by adding, and run
`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first — a
size-preserving edit restored within the same second otherwise runs stale bytecode. Confirm the
named new test catches the mutant **by node id**, and that no pre-existing test in the same file is
what actually kills it.

## Error handling

The guard fails loudly at every ambiguity rather than defaulting: an unparseable summary, a missing
summary, and an unexpected feature are all errors, never passes. This is the bug class the codebase
most consistently engineers out. On failure the guard prints the expected and actual maps side by
side, and a hint that the counts are only valid on a fresh checkout — the trap named at the top.

## Neutrality

No personal data. The guard is generic tooling with no employer names, locations, hostnames, or
absolute paths; captured fixtures are rulesync's own summary lines, which contain none.

## Out of scope

- Tracking the generated outputs (rejected above).
- Any change to `.rulesync/` content, or to which targets and features are enabled.
- Making a hand-edit of a generated file fail the build — impossible while outputs are gitignored,
  and recorded here so it is not re-attempted.
