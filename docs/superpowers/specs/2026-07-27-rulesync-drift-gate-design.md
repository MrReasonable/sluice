# rulesync drift gate — enforce what `.gitignore` currently only asks for (#2)

**Status:** design approved 2026-07-27; revised once after `/review-plan` (5 reviewers: 1 Critical,
9 High, 7 Medium, 10 Low). Every finding is addressed below or explicitly declined.
**Issue:** #2 — `chore: generate AI assistant rules from a single source (rulesync)`. The issue's
headline proposal already shipped; its body is stale and is rewritten in place as part of this work
(see *The issue text is wrong*).
**Sub-apps:** none. This touches `scripts/`, `.github/workflows/ci.yml`, `.github/dependabot.yml`,
`.gitignore`, `tests/`, `.rulesync/rules/CLAUDE.md`, and adds `package.json` / `package-lock.json`.
No `sluice/` change.

**What this document is.** A design, not an executable task list. It fixes the shape, the decisions
and the traps; it deliberately does not carry ordered tasks, per-task owners, or a definition of
done. `superpowers:writing-plans` supplies those next, and every verification command below is
written to be copy-pasteable when it does. Reviewers correctly flagged the absence as a `scope-gap`
— this note is the answer, not a dismissal.

**`.rulesync/` edit is user-authorised.** Decision D8 rewords `.rulesync/rules/CLAUDE.md:14`. That
tree is canonical and normally escalated rather than auto-approved; the user granted approval on
2026-07-27 after review. It is the highest-leverage place in the repo to assert something false, so
the reworded command below was executed end-to-end before being written down.

## Four traps, each of which makes the gate certify nothing

The first three were caught while drafting. The fourth was caught by four of five reviewers, one
paragraph after the document congratulated itself on the first — which is the honest measure of how
easy this class is to reintroduce.

- **A porcelain check alone is provably insufficient.** Measured: a malformed `.rulesync/hooks.json`
  on a fresh clone silently drops 17 files, exits **0**, and leaves `git status --porcelain`
  **empty**. Exit code and porcelain both pass green on the exact failure the gate exists to catch.
- **An absent feature term is a count of zero, not a parse failure to skip.** When hooks drop out,
  rulesync's summary omits the term entirely rather than printing `0 hooks`. A parser that regexes
  `(\d+) hooks`, finds no match, and moves on reports success on the failing input.
- **The counts are only valid on a fresh tree.** rulesync skips writing a file whose content already
  matches. On an already-generated tree the same command reports **14** files, not 243. A guard run
  against a working copy that has been generated in fails for a reason unrelated to any defect.
- **Adding the job to `ci-success`'s `needs:` does NOT make it block.** `ci-success` carries
  `if: always()` (`.github/workflows/ci.yml:48-55`), so `needs:` is only a scheduling edge; the sole
  thing that fails it is the explicit `&&` chain in its run block. Wire the job without extending
  that chain and a **red** gate yields a **green** required check, with every specified test still
  passing. This is the fail-open class the gate exists to close, sitting one layer above it in the
  plumbing that publishes the verdict.

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

This is recorded here and in the rewritten issue body precisely so nobody re-adds the goal.

Grep the claim, not the code. As of 2026-07-27 the stale claim lives only in issue #2's body; no
tracked file asserts it. The obvious check —
`git grep -iE 'no (agent|assistant) (instructions|rules)|tribal knowledge'` — now returns **exactly
one hit, this document's own quotation of the pattern**, and will return more if this paragraph is
edited. State the expected hit count when running it; a bare "returns empty" was true when first
written and was falsified by writing it down.

## Measurements

Every number was measured on 2026-07-27 against rulesync 9.6.3, not read from documentation, and
every one was independently reproduced by a second reviewer against its own fresh clones.

| Probe | Command / mutation | Result |
|---|---|---|
| Baseline, fresh clone | the documented generate command | exit 0, **243** files: `20 rules + 114 subagents + 92 skills + 17 hooks`; porcelain **empty** |
| Determinism | Baseline on a second independent fresh clone | identical count and breakdown; porcelain empty |
| Survives its own change | Baseline with `package.json`, `package-lock.json` and `node_modules/` present | still **243**, porcelain empty — so I2's pin is not invalidated by this plan's own additions |
| Already-generated tree | Baseline on a tree already generated into | exit 0, **14** files (4 rules → `AGENTS.md`, 10 subagents → `.github/agents/`) |
| Skip-if-identical | Delete `CLAUDE.md`, re-run | count 14 → **15**, `CLAUDE.md` reappears, **byte-identical** to the deleted copy |
| Malformed `.rulesync/rules/CLAUDE.md` | Replace frontmatter with invalid YAML | **exit 1**, no files written |
| `.rulesync/` absent | `mv .rulesync` away | **exit 1** |
| Malformed `.rulesync/hooks.json`, fresh clone | `printf '{ broken json' > .rulesync/hooks.json` | **exit 0**, **226** files, `.github/hooks/` never created, porcelain **empty** |
| D8 command, fresh clone | `npm ci && npm run --silent rulesync` | exit 0, **243**, identical breakdown; `./node_modules/.bin/rulesync --version` → `9.6.3` |

Two observations carried forward:

- The rules body is shared across targets — `AGENTS.md` and `.rules` hash identically — and
  `AGENTS.md` is reported written four times in one run. The *mechanism* behind the 14-file rewrite
  on an already-generated tree was **not** isolated; only the observation is relied on, and only for
  the conclusion that the guard must run on a fresh checkout.
- `.rulesync/` has no `commands/` directory, so `commands` never appears in the summary. The expected
  feature set is exactly `{rules, subagents, skills, hooks}`, and a *new* feature appearing is drift
  the guard must fail on, not tolerate.

## The two invariants

Asserted on a fresh checkout, in one CI job:

**I1 — Hygiene.** After running the generate command, `git status --porcelain` is empty. Catches a
gitignore gap when a version bump or newly-enabled feature emits into a fresh path (including into
the tracked `.github/` tree), and catches a force-added generated file **whose committed content
differs from the generated output**. That narrowing is measured, not assumed: a force-added file that
happens to match byte-for-byte is invisible to I1, because rulesync skips writing it and nothing
changes. Do not rely on I1 to detect force-adding as such.

**I2 — Completeness.** The run reports exactly `{rules: 20, subagents: 114, skills: 92, hooks: 17}`.
Catches the fail-open class that I1 provably misses.

I2 is pinned rather than merely non-zero because the output set is fully determined by tracked
inputs — `.rulesync/` **and the lockfile** (see D4). A per-feature non-zero check would pass a partial
drop (16 of 17 hooks). When the counts legitimately change, the one-line diff to the expected map
documents the blast radius, which is the `.gitignore` re-audit the prose asks for and nothing performs.

## Settled decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Guard regen hygiene **and** completeness, not hygiene alone | I1 alone is green on the measured `hooks.json` failure |
| D2 | Pinned per-feature counts, not non-zero and not a path manifest | Catches partial drops; a manifest's only marginal catch (a path moving) is already covered by I1 |
| D3 | A `scripts/` Python guard with a pure parser plus a thin CI wrapper | Follows the `guard_no_bypass.py` precedent; parser unit-testable offline and mutation-testable, since CLAUDE.md's `compileall` line already covers `scripts/` |
| D4 | `package.json` + `package-lock.json` + `npm ci` | **Not** posture-matching. `npx rulesync@9.6.3` pins the direct package but floats its 244-package transitive tree, so the output set would *not* be determined by tracked inputs and I2's pin would be unsound. The lockfile is what makes I2 legitimate |
| D5 | Rewrite issue #2's body in place | Keeps the issue number that memory and the commit trail reference, and records why the original framing was wrong |
| D6 | Add the npm ecosystem to Dependabot | A bump that fails until someone re-audits *is* the enforcement working. **By construction every rulesync bump PR lands red** until `EXPECTED` and the `.gitignore` list are re-audited. That is designed, not a defect — say so in the `EXPECTED` comment so it is not debugged as one |
| D7 | ~~Two-file version pin~~ → a repo-wide version sweep | Superseded by D8. With one literal there is nothing to cross-compare; the residual risk is a *new* hardcoded version reappearing in prose, which a sweep catches and a two-file compare would not |
| D8 | One command, one version literal: an npm script | Resolves the four-site duplication at its root rather than policing it. `9.6.3` lives only in `package.json`/`package-lock.json`; the flags live only in the script. Docs and CI then run a byte-identical command, so a human reproducing a red gate runs exactly what CI ran |
| D9 | `.gitignore` gains `node_modules/` **and** `.npmrc` | `.npmrc` is the credential-bearing member of the toolchain being introduced and has never needed ignoring here before |

## Components

### `package.json` + `package-lock.json`

`rulesync@9.6.3` as the sole *direct* devDependency — 244 packages transitively, ~114 KB lockfile.
The single command lives here as a script, so no caller repeats the flags:

```json
{
  "name": "sluice-tooling",
  "private": true,
  "scripts": { "rulesync": "rulesync generate -t '*' -f '*'" },
  "devDependencies": { "rulesync": "9.6.3" }
}
```

`npm run` puts `node_modules/.bin` on PATH, so the script resolves the **locked** binary with no
registry fallback. This is why the plan does not use bare `npx rulesync`: npx silently fetches from
the registry when the package is absent locally, so any loss or reordering of `npm ci` would run an
unpinned latest while looking green — D4's rationale defeated by the invocation.

### `.gitignore` (D9)

```
node_modules/
.npmrc
```

`node_modules/` is root-anchored like its neighbours. **`.npmrc` deliberately is not** — it follows
the `.memsearch/` reasoning already in the file: root-anchoring exists only to stop a bare
`CLAUDE.md` swallowing the canonical source, a collision a credential file cannot cause, and for a
file that can carry a registry auth token, matching at any depth is the safer default. (Verified
2026-07-27: `.npmrc` is currently *not* ignored here, and a real auth token does live in the
user-level `~/.npmrc`.)

Both need their counterpart in `tests/test_no_leaked_files.py`, or the ignore is unguarded:
`node_modules/` into `FORBIDDEN_PREFIXES`, `.npmrc` into `FORBIDDEN_COMPONENTS` — the latter is
exactly the any-depth treatment that table already gives `.memsearch`.

### `scripts/guard_rulesync_drift.py`

Standard library only. Pure split from impure:

- `parse_summary(text: str) -> dict[str, int]` — pure. Locates the single `All done!` summary line
  and parses its parenthetical into a feature → count mapping.
- `main(argv) -> int` — takes the captured output path as its single positional argument, compares
  against a frozen module-level `EXPECTED`, prints expected-vs-actual on mismatch, returns non-zero.
- `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))` — the process contract.

Four behaviours the parser must get right:

| Input | Required behaviour |
|---|---|
| A feature term absent from the parenthetical | That feature reports `0` → mismatch → fail |
| No `All done!` line at all | Hard error, never a pass |
| An unexpected feature present (e.g. `commands`) | Mismatch → fail |
| More than one summary line | Hard error |

`EXPECTED` carries a comment stating it changes only when `.rulesync/` or the lockfile changes, that
updating it is the moment to re-audit `.gitignore`'s output list, and that a Dependabot rulesync PR
is *expected* to arrive red (D6).

### CI job `rulesync` in `.github/workflows/ci.yml`

```yaml
  rulesync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with:
          node-version: "22"
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: npm ci
      - run: |
          set -euo pipefail
          npm run --silent rulesync | tee "$RUNNER_TEMP/rulesync-output.txt"
          python scripts/guard_rulesync_drift.py "$RUNNER_TEMP/rulesync-output.txt"
          if [ -n "$(git status --porcelain)" ]; then
            git status --porcelain
            exit 1
          fi
```

Load-bearing details, each of which was wrong in an earlier draft:

- **`set -o pipefail` is mandatory.** Verified against GitHub's workflow-syntax reference on
  2026-07-27: a `run:` block with no `shell:` key runs as `bash -e {0}` — `-e` but *not* pipefail.
  (Only an explicit `shell: bash` gives `bash --noprofile --norc -eo pipefail {0}`; every existing
  job in `ci.yml` uses the bare `- run:` form.) In `npm run … | tee`, `$?` is `tee`'s status, so
  without pipefail a rulesync **exit 1** — the measured signal for a malformed rules file or a
  missing `.rulesync/` — is swallowed and the step passes green.
- **`actions/setup-python` is required.** The job calls `python`; both existing Python jobs pin the
  interpreter by SHA. Omitting it fails spuriously red and gets blamed on the guard.
- **The capture file lives in `$RUNNER_TEMP`.** A scratch file beside the checkout is itself
  untracked, so I1 would trip over its own artefact.
- **I2 runs before I1.** A fail-open produces a *clean* tree, so I1 passes on it.

**`ci-success` needs BOTH edits** — the `needs:` entry orders the job; the `&&` chain is what blocks:

```yaml
  ci-success:
    needs: [lint, test, rulesync]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: |
          [ "${{ needs.lint.result }}" = success ] && \
          [ "${{ needs.test.result }}" = success ] && \
          [ "${{ needs.rulesync.result }}" = success ]
```

No `name:` key on the new job — the check-run name is the job id unless `name:` is set, and renaming
a required check breaks the branch-protection binding.

The Dependabot entry (D6) follows the shape already in `.github/dependabot.yml`:
`package-ecosystem: npm`, `directory: /`, weekly, grouped `patterns: ["*"]`, and
`commit-message: {prefix: chore, include: scope}`.

### `.rulesync/rules/CLAUDE.md` and `.gitignore` prose (D8, user-authorised)

Both currently document `npx rulesync@9.6.3 generate -t '*' -f '*'`. Reword both to the single
command CI runs:

```
npm ci && npm run rulesync
```

This is the whole point of D8: after it, no prose names a version, so no prose can drift from one.
The `.gitignore:56-61` comment additionally gains a pointer to `scripts/guard_rulesync_drift.py` as
the thing that now enforces the re-audit it asks for, so the two halves reference each other.

### Tests

Offline and hermetic; the suite never shells out to npm or npx.

**`tests/test_guard_rulesync_drift.py`.** The two fixtures are real captured output, reproduced here
so the tests are self-contained:

```
🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)
🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)
```

The second is what a malformed `hooks.json` actually produces — it omits the term rather than
printing `0 hooks`. Both are literal rulesync 9.6.3 output including the emoji and exact phrasing; a
version bump may change the wording, and the expected failure is then a loud parse error, not a
silent pass. Say so in the fixture's comment.

Parser cases (against `parse_summary`): the 243 line passes; the 226 line reports `hooks: 0`; no
summary line errors; an unexpected feature (`+ 3 commands`) fails; two summary lines error.

**Cases through `main`, which the first draft omitted entirely** — and which are the ones that make
the guard load-bearing. `tests/test_guard_no_bypass.py:26-30` states the lesson in this repo's own
words: *"the hook contract IS the exit code, so deleting `sys.exit(main())` would leave such a suite
green while the guard did nothing."* Four passing parser tests over a `main` that returns 0
unconditionally, or compares a subset, is exactly that. So: a `tmp_path` capture file through `main`
returning 0 on the 243 line; non-zero on a `17 → 16` hooks line (which only equality catches, not
non-zero); non-zero on a missing file; plus one subprocess test pinning the `__main__` → exit-code
contract.

**`tests/test_ci_wiring.py`** — parses `.github/workflows/ci.yml` offline and asserts the properties
above, none of which the first draft tested despite naming all of them load-bearing.
`tests/test_hooks_wiring.py` is the precedent and its docstring records this exact failure: *"A
correct guard that is not wired is inert, and this exact file has already shipped inert once."*

- Every job in `ci-success`'s `needs:` appears as `needs.<id>.result` in its run script — enumerated
  from the parsed YAML, both ends, never hand-listed. This is the check that would have caught the
  Critical.
- The `rulesync` job's script contains `set -euo pipefail`.
- The guard invocation precedes the `git status --porcelain` check.
- The capture path is under `$RUNNER_TEMP`.

**Version sweep (D7, superseded scope).** Grep every tracked file for a rulesync version literal and
assert the only occurrences are in `package.json`/`package-lock.json`. This catches a hardcoded
version reappearing in prose — the drift D8 removed. Derive the repo root from
`Path(__file__).resolve().parents[1]`, never an absolute path and never cwd-relative.

**Linting.** CI runs `ruff check sluice tests` (`.github/workflows/ci.yml:26`), so `scripts/` is
unlinted today — `guard_no_bypass.py` included. Extend it to `ruff check sluice tests scripts`, since
this plan adds a second file to that directory and shipping it unlinted repeats the omission.

This is **not** the one-word edit it looks like, and an earlier draft of this spec claimed it was.
Measured 2026-07-27: `ruff check sluice tests scripts` currently reports **4 errors**, all `E741`
(ambiguous variable name `l`) in the pre-existing `scripts/diff_vs_legacy.py:46-51`, and `E741` is
not in ruff's autofix set. So the change is the one-word edit *plus* four manual renames in a file
otherwise unrelated to this work. That is small and self-contained enough to fold in rather than
defer, but it must be budgeted, and the renames belong in their own `style(scripts):` commit so the
drift-gate diff stays readable.

**Mutation testing.** Mutate by **moving or deleting**, never adding, and run
`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first. **Commit
the guard and its tests before mutating** — the new file is untracked, so a witness script restoring
via `git checkout -- <file>` errors and leaves the mutant in place, and the empty post-run diff hides
it. Enumerate the mutants rather than improvising: delete `sys.exit(...)` from `__main__`; change the
`EXPECTED` comparison from equality to a subset test; delete the absent-term-means-zero branch;
delete the third conjunct from `ci-success`. Each must be killed by a **named new test, confirmed by
node id**, with no pre-existing test in the same file doing the killing.

## Claims this work falsifies elsewhere

When a change falsifies a stated reason, every site that states it needs updating — the reason going
stale is harder to notice than the conclusion going wrong, because nothing fails.

- **`tests/test_hooks_wiring.py`'s docstring** justifies asserting the tracked source rather than the
  generated artefact because *"`.claude/settings.json` is gitignored and produced by `npx rulesync
  generate`, which needs node and network. **CI never runs it**, so asserting the generated artifact
  could not be hermetic."* After this change CI **does** run it. The conclusion stands — the pytest
  suite must stay hermetic, and that job is a separate non-pytest gate — but the stated reason must
  be corrected, or the next reader draws a false inference about what CI can do.
- **`.gitignore:52-64`'s "nothing enforces it"** becomes false in the direction that matters. Point
  it at the enforcer (covered under D8 above).

## Error handling

The guard fails loudly at every ambiguity rather than defaulting: an unparseable summary, a missing
summary, a missing file, and an unexpected feature are all errors, never passes. On failure it prints
expected and actual side by side, plus a hint that the counts are only valid on a fresh checkout.

## Neutrality

No personal data. The lockfile was generated and inspected rather than reasoned about: all `resolved`
URLs are `registry.npmjs.org`, remaining hosts are upstream `funding` links, and there is no home
path, email or token. `package.json` omits `author`. The two fixtures are rulesync summary lines
carrying only counts and feature names. D9's `.npmrc` ignore exists precisely to keep a credential
from ever becoming committable.

## Out of scope

- Tracking the generated outputs (rejected above).
- Any change to which rulesync targets or features are enabled. (D8 rewords one command line in
  `.rulesync/rules/CLAUDE.md`; it changes no rule content.)
- Making a hand-edit of a generated file fail the build — impossible while outputs are gitignored,
  and recorded here so it is not re-attempted.
