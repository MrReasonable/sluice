# rulesync drift gate — enforce what `.gitignore` currently only asks for (#2)

**Status:** design approved 2026-07-27; revised twice after `/review-plan`.
Round 1 (5 reviewers): 1 Critical, 9 High, 7 Medium, 10 Low. Round 2 (5 reviewers): 24 of 27
round-1 findings confirmed fixed, 3 partial; 0 Critical, 5 High, 9 Medium, 5 Low new. The decisions
are unchanged throughout; several mechanisms underneath them have been replaced twice.
**Issue:** #2 — `chore: generate AI assistant rules from a single source (rulesync)`. The headline
proposal already shipped; the body is stale and is rewritten in place (see *The issue text is wrong*).
**Sub-apps:** none. Touches `scripts/`, `.github/workflows/ci.yml`, `.github/dependabot.yml`,
`.gitignore`, `tests/`, `.rulesync/rules/CLAUDE.md`, `.rulesync/hooks.json`, three `.rulesync/skills/`
files, and adds `package.json` / `package-lock.json`. **No `sluice/` change.**

**What this document is.** A design, not an executable task list. `superpowers:writing-plans` supplies
ordered tasks, owners and a definition of done next; every verification command here is written to be
copy-pasteable when it does.

**D10 is already done** — it was separable and did not need the rest of the gate. `90061b3` clears the
four `E741`s in `scripts/diff_vs_legacy.py`; `b39e0e3` extends CI's lint to `scripts/` and moves the
twelve documented statements of the quality bar in lockstep. Verified: `ruff check sluice tests
scripts` clean, 1209 passed, zizmor clean, regeneration leaves no generated file tracked. Everything
else below is still unimplemented.

**`.rulesync/` edits are user-authorised** (2026-07-27, twice — the second after round 2 surfaced two
more sites). That tree is canonical and normally escalated. Authorised scope: `rules/CLAUDE.md`
(the generate command and the ruff target list), `hooks.json`'s `_comment` cross-reference, and the
`ruff check` target list in the `address-comments`, `path-to-green` and `review-plan` skills. Nothing
else in `.rulesync/` is touched; no rule content changes.

## Five traps, each of which makes the gate certify nothing

The first three were caught while drafting; the fourth and fifth by reviewers, each time inside the
fix for the previous one. That is the honest measure of how easy this class is to reintroduce.

- **A porcelain check alone is provably insufficient.** Measured: a malformed `.rulesync/hooks.json`
  on a fresh clone silently drops 17 files, exits **0**, and leaves `git status --porcelain`
  **empty**.
- **An absent feature term is a count of zero, not a parse failure to skip.** When hooks drop out,
  the summary omits the term rather than printing `0 hooks`. A parser that regexes `(\d+) hooks`,
  finds no match and moves on reports success on the failing input.
- **The counts are only valid on a fresh tree.** rulesync skips writing a file whose content already
  matches; on an already-generated tree the same command reports **14** files, not 243.
- **Adding the job to `ci-success`'s `needs:` does NOT make it block.** `ci-success` carries
  `if: always()` (`.github/workflows/ci.yml:48-55`), so `needs:` is only a scheduling edge; the sole
  thing that fails it is the explicit `&&` chain in its run block. Wire the job without extending
  that chain and a **red** gate yields a **green** required check.
- **`npm run` does not pin the binary — it PREPENDS to PATH.** Measured on a machine with a global
  rulesync: in a directory with no `node_modules/`, `npm run` with a bare `rulesync` silently ran
  **9.2.0** and exited 0. An earlier draft of this document asserted the opposite ("resolves the
  locked binary with no registry fallback") on the strength of a test run *with* `node_modules/`
  present — a non-falsifying test of exactly the shape this repo has a standing lesson about. The
  path into it is this document's own advice: "counts are only valid on a fresh tree" invites
  `git clean -xdf`, which deletes the gitignored `node_modules/`; the human then runs an unpinned
  rulesync, gets outputs in paths absent from `.gitignore`, and "fixes" the tracked `.gitignore` for
  a version this repo does not pin.

## Problem

`.rulesync/` is canonical and tracked; every AI-tool output it generates is a gitignored build
artifact. `.gitignore:52-64` carries the maintenance obligation in prose:

> Re-audit this list on a rulesync version bump (the target set is what changes) AND whenever a new
> rulesync FEATURE is turned on

Nothing enforces it. That comment exists because the obligation has already been discovered the hard
way once — enabling `.rulesync/hooks.json` made the documented command emit 17 hook files across 15
directories where it previously emitted none, and two landed in the **tracked** `.github/` tree.

## The issue text is wrong, and the goal it states is unachievable

Issue #2's body opens "The repo carries **no** agent/assistant instructions at all." True when filed,
false now: `.rulesync/` holds one rules file, five subagents, four skills and `hooks.json`, and
generates 243 files.

More importantly, the issue asks for "generation checked in CI so a hand-edit of a generated file
fails the build." **That cannot be built.** Generated outputs are gitignored, so a hand-edit to
`CLAUDE.md` is a local, untracked change that never reaches CI. Tracking the 243 outputs instead was
considered and rejected — it buys the literal goal at the cost of a large generated diff in every PR.

Grep the claim, not the code. The obvious check —
`git grep -icE 'no (agent|assistant) (instructions|rules)|tribal knowledge'` — returns **exactly one
hit, this document's own quotation of the pattern**. State the expected hit count when running it; a
bare "returns empty" was true when first written and was falsified by writing it down. The version
sweep below repeats this lesson at larger scale.

## Measurements

Measured 2026-07-27 against rulesync 9.6.3, and independently reproduced by a second reviewer against
its own fresh clones.

| Probe | Command / mutation | Result |
|---|---|---|
| Baseline, fresh clone | the documented generate command | exit 0, **243** files: `20 rules + 114 subagents + 92 skills + 17 hooks`; porcelain **empty** |
| Determinism | Baseline on a second independent fresh clone | identical count and breakdown |
| Survives its own change | Baseline with `package.json`, `package-lock.json`, `node_modules/` present | still **243**, porcelain empty — I2's pin is not invalidated by this plan's own additions |
| Already-generated tree | Baseline on a tree already generated into | exit 0, **14** files |
| Skip-if-identical | Delete `CLAUDE.md`, re-run | count 14 → **15**, reappears **byte-identical** |
| Malformed `.rulesync/rules/CLAUDE.md` | frontmatter → invalid YAML | **exit 1**, no files written |
| `.rulesync/` absent | `mv .rulesync` away | **exit 1** |
| Malformed `.rulesync/hooks.json`, fresh clone | `printf '{ broken json'` | **exit 0**, **226** files, `.github/hooks/` never created, porcelain **empty** |
| D8 command, fresh clone | `npm ci && npm run rulesync` | exit 0, **243**, identical breakdown; binary resolves to `9.6.3`; 244 packages, lockfile 113751 B |
| **PATH fallback** | `npm run` with bare `rulesync`, **no** `node_modules/` | **silently ran a global 9.2.0, exit 0** |
| **PATH fallback, fixed** | same, script using `node_modules/.bin/rulesync` | `sh: No such file or directory` — **fails loudly** |
| pipefail, precise behaviour | mutated rules file, no pipefail | chain exits 0, but `tee` leaves a **0-byte** capture, so the guard hard-errors and the step still goes red |
| ruff | `ruff check sluice tests scripts` | 4 `E741` in `scripts/diff_vs_legacy.py` — **fixed in `90061b3`**, now clean |

Two observations carried forward:

- The rules body is shared across targets — `AGENTS.md` and `.rules` hash identically. The
  *mechanism* behind the 14-file rewrite on an already-generated tree was **not** isolated; only the
  observation is relied on, and only for the conclusion that the guard must run on a fresh checkout.
- `.rulesync/` has no `commands/` directory, so `commands` never appears in the summary. The expected
  feature set is exactly `{rules, subagents, skills, hooks}`; a *new* feature appearing is drift the
  guard must fail on.

## The two invariants

**I1 — Hygiene.** After the generate command, `git status --porcelain` is empty. Catches a gitignore
gap when a version bump or newly-enabled feature emits into a fresh path (including the tracked
`.github/` tree), and catches a force-added generated file **whose committed content differs from the
generated output**. That narrowing is measured: a force-added file matching byte-for-byte is
invisible to I1, because rulesync skips writing it. Do not rely on I1 to detect force-adding as such.

**I2 — Completeness.** The run reports exactly `{rules: 20, subagents: 114, skills: 92, hooks: 17}`.
Catches the fail-open class I1 provably misses, and is also what guards the generate *flags* once D8
moves them out of prose — any narrowed `-t`/`-f` moves the counts.

I2 is pinned rather than non-zero because the output set is fully determined by tracked inputs —
`.rulesync/` **and the lockfile** (D4). A per-feature non-zero check would pass a partial drop
(16 of 17 hooks).

## Settled decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Guard hygiene **and** completeness | I1 alone is green on the measured `hooks.json` failure |
| D2 | Pinned per-feature counts | Catches partial drops; a path manifest's only marginal catch is already covered by I1 |
| D3 | A `scripts/` Python guard, pure parser plus thin CI wrapper | Follows the `guard_no_bypass.py` precedent; parser unit-testable offline and mutation-testable |
| D4 | `package.json` + `package-lock.json` + `npm ci` | **Not** posture-matching. `npx rulesync@9.6.3` pins the direct package but floats its 244-package transitive tree, so the output set would *not* be determined by tracked inputs and I2's pin would be unsound. The lockfile is what makes I2 legitimate |
| D5 | Rewrite issue #2's body in place | Keeps the issue number memory and the commit trail reference |
| D6 | npm ecosystem in Dependabot | **By construction every rulesync bump PR lands red** until `EXPECTED` and the `.gitignore` list are re-audited. Designed, not a defect — say so in the `EXPECTED` comment |
| D7 | A scoped version sweep | See *Version sweep* below. The naive form is unrunnable and the obvious repair is vacuous |
| D8 | One command, one version literal, invoked by explicit path | `9.6.3` lives only in `package.json`/`package-lock.json`; the flags only in the script; the script invokes `node_modules/.bin/rulesync` so a missing install fails loudly instead of silently running whatever is on PATH |
| D9 | `.gitignore` gains `node_modules/` **and** `.npmrc` | `.npmrc` is the credential-bearing member of the toolchain being introduced |
| D10 | Extend CI lint to `scripts/` | User decision: it should have been linted from day one. Fixed by renaming, not a per-file-ignore — `pyproject.toml:43` exempts `tests/*` from `E741`, and extending that to `scripts/` would hold a merge-gate script to a lower bar than `sluice/`. **Already implemented** (`90061b3`, `b39e0e3`); see below |

## Components

### `package.json` + `package-lock.json`

`rulesync@9.6.3` as the sole *direct* devDependency — 244 packages transitively, ~114 KB lockfile.
The single command lives here, so no caller repeats the flags:

```json
{
  "name": "sluice-tooling",
  "private": true,
  "scripts": { "rulesync": "node_modules/.bin/rulesync generate -t '*' -f '*'" },
  "devDependencies": { "rulesync": "9.6.3" }
}
```

**The explicit `node_modules/.bin/` path is load-bearing** — see trap 5. `npm run` prepends
`node_modules/.bin` to PATH but does not restrict PATH, so a bare `rulesync` falls through to any
global install. The explicit path exits 127 when the install is missing, which is the loud failure
this needs.

### `.gitignore` (D9)

```
/node_modules/
.npmrc
```

`node_modules/` is **root-anchored** like its neighbours (an earlier draft's snippet omitted the
leading slash while the prose beside it claimed anchoring). **`.npmrc` deliberately is not**: it
follows the `.memsearch/` reasoning already in the file — root-anchoring exists only to stop a bare
`CLAUDE.md` swallowing the canonical source, a collision a credential file cannot cause, and for a
file that can carry a registry auth token, matching at any depth is safer. Verified 2026-07-27:
`.npmrc` is currently not ignored here.

Both need their counterpart in `tests/test_no_leaked_files.py`, or the ignore is unguarded:
`node_modules/` into `FORBIDDEN_PREFIXES`, `.npmrc` into `FORBIDDEN_COMPONENTS` (correct for a file —
`_is_forbidden` splits on `/` and checks every part).

### `scripts/guard_rulesync_drift.py`

Standard library only. Pure split from impure:

- `parse_summary(text) -> dict[str, int]` — pure. Locates the single `All done!` line and parses its
  parenthetical into a feature → count mapping.
- `main(argv) -> int` — takes the captured output path as its single positional argument, compares
  against a frozen module-level `EXPECTED`, prints expected-vs-actual on mismatch, returns non-zero.
- `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))` — the process contract.

| Input | Required behaviour |
|---|---|
| A feature term absent from the parenthetical | reports `0` → mismatch → fail |
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
          npm run rulesync | tee "$RUNNER_TEMP/rulesync-output.txt"
          python scripts/guard_rulesync_drift.py "$RUNNER_TEMP/rulesync-output.txt"
          if [ -n "$(git status --porcelain)" ]; then
            git status --porcelain
            exit 1
          fi
```

`npm run rulesync` **without `--silent`**, so this is byte-identical to the documented command. An
earlier draft used `--silent` in CI and the plain form in the docs while claiming the two were
identical. npm's banner contains no `All done!`, so it cannot confuse the parser.

- **`set -o pipefail`.** A `run:` block with no `shell:` key runs as `bash -e {0}` — `-e` but not
  pipefail (verified against GitHub's workflow-syntax reference; only an explicit `shell: bash` adds
  it). Precisely: without pipefail the failing chain still goes red, because `tee` leaves a 0-byte
  capture and the guard hard-errors on it. pipefail is kept because it makes the *real* cause the
  *reported* cause — not because the step would otherwise pass. An earlier draft overstated this.
- **`actions/setup-python` is required.** The job calls `python`; both existing Python jobs pin it.
- **The capture file lives in `$RUNNER_TEMP`** — a scratch file beside the checkout is itself
  untracked and I1 would trip over its own artefact.
- **I2 runs before I1** — a fail-open produces a *clean* tree.

**`ci-success` needs BOTH edits** — `needs:` orders, the `&&` chain blocks:

```yaml
  ci-success:
    needs: [lint, test, rulesync]
    if: always()
    steps:
      - run: |
          [ "${{ needs.lint.result }}" = success ] && \
          [ "${{ needs.test.result }}" = success ] && \
          [ "${{ needs.rulesync.result }}" = success ]
```

No `name:` key on the new job — the check-run name is the job id unless `name:` is set, and renaming
a required check breaks the branch-protection binding.

`lint` also becomes `ruff check sluice tests scripts` (D10). Dependabot gains
`package-ecosystem: npm`, `directory: /`, weekly, grouped, `commit-message: {prefix: chore, include: scope}`.

### `.rulesync/` and `.gitignore` prose (D8 / D10, user-authorised)

| Site | Change |
|---|---|
| `.rulesync/rules/CLAUDE.md:14` | generate command → `npm ci && npm run rulesync` |
| `.rulesync/rules/CLAUDE.md:25` | `ruff check sluice tests` → `ruff check sluice tests scripts` |
| `.rulesync/hooks.json:2` `_comment` | its "re-verify … when changing the pinned version in `.rulesync/rules/CLAUDE.md`" pointer → `package.json`. **This comment calls itself the ONLY defence against a version bump silently dropping the hook command**; leaving it pointing at a file that no longer names a version is the drift that matters most here |
| `.gitignore:37` | generate command → `npm ci && npm run rulesync` |
| `.gitignore:54` | second `9.6.3` literal, one line below the block D8 edits — reword to name no version |
| `.gitignore:56-61` | add a pointer to `scripts/guard_rulesync_drift.py` as the enforcer |
| `address-comments`, `path-to-green`, `review-plan` skills | 11 `ruff check sluice tests` sites → add `scripts` |

### Version sweep (D7)

The naive form — "assert the only version literals are in `package.json`" — **cannot pass**: measured,
`9.6.3` appears on 22 lines across 9 tracked files, and D8 removes 4. The obvious repair (hardcoding
`9.6.3` in the grep) goes **vacuous** on the first Dependabot bump. So:

- Read the version from `package.json`; assert it parses non-empty (non-vacuity anchor).
- Sweep tracked files **excluding `docs/superpowers/`**, which is a dated record — the repo's
  convention is a dated *superseded* note, never rewriting history. Six historical plans/specs carry
  the old command legitimately, as does this document.
- Assert zero occurrences outside `package.json`/`package-lock.json`, and assert the sweep examined
  a non-zero number of files, so an over-narrow scope fails loudly rather than passing empty.

### Tests

Offline and hermetic; the suite never shells out to npm or npx.

**`tests/test_guard_rulesync_drift.py`.** Fixtures are real captured output, reproduced so the tests
are self-contained:

```
🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)
🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)
```

The second is what a malformed `hooks.json` produces — it omits the term rather than printing
`0 hooks`. Both are literal rulesync 9.6.3 output including the emoji; a version bump may change the
wording, and the expected failure is then a loud parse error, not a silent pass. Say so in the
fixture comment.

Parser cases: 243 passes; 226 reports `hooks: 0`; no summary line errors; an unexpected feature
(`+ 3 commands`) fails; two summary lines error.

**Cases through `main`** — the ones that make the guard load-bearing.
`tests/test_guard_no_bypass.py:26-30` states the lesson in this repo's own words: *"the hook contract
IS the exit code, so deleting `sys.exit(main())` would leave such a suite green while the guard did
nothing."* A `tmp_path` capture through `main`: returns 0 on the 243 line; non-zero on a `17 → 16`
hooks line (which only equality catches, not non-zero); non-zero on a missing file; plus one
subprocess test pinning the `__main__` → exit-code contract.

**`tests/test_ci_wiring.py`** — parses `ci.yml` offline. `tests/test_hooks_wiring.py` is the
precedent; its docstring records this exact failure: *"A correct guard that is not wired is inert, and
this exact file has already shipped inert once."*

- `assert "rulesync" in ci-success.needs` — **separately from** the consistency check below.
  Enumerating `needs:` against the run script is only a *consistency* check: delete the `needs:` entry
  and the conjunct together and both lists stay consistent while the gate is unwired.
- Every job in `ci-success.needs` appears as `needs.<id>.result` in its run script, enumerated from
  parsed YAML, both ends, never hand-listed — plus a non-vacuity assert that `needs` is non-empty.
- The `rulesync` job's script contains `set -euo pipefail`; the guard call precedes the porcelain
  check; the capture path is under `$RUNNER_TEMP`.
- **The invocation contract D4 rests on**, which nothing else asserts offline: `package.json`'s
  `scripts.rulesync` is byte-exact, `npm ci` precedes the guard step, and the job contains no `npx`.
  Substituting `npx rulesync@9.6.3 generate …` back into the run block keeps the counts identical and
  I2 green while silently destroying the locked transitive tree.
- `lint`'s ruff invocation names `scripts` (D10), so the extension cannot be silently reverted.

**`tests/test_no_leaked_files.py`** — `test_the_gate_covers_every_path_gitignore_covers` currently
iterates `FORBIDDEN_EXACT + FORBIDDEN_PREFIXES` and then *hand-asserts* `.memsearch`, so
`FORBIDDEN_COMPONENTS` is unenumerated and `.npmrc` would fall outside coverage entirely. Enumerate
all three tuples and delete the hand-written line — enumerate, don't hand-list.

**Mutation testing.** Mutate by **moving or deleting**, never adding, and run
`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first. **Commit
the guard and its tests before mutating** — the new file is untracked, so a witness restoring via
`git checkout -- <file>` errors and leaves the mutant in place, and the empty post-run diff hides it.
Enumerated mutants: delete `sys.exit(...)` from `__main__`; change the `EXPECTED` comparison from
equality to a subset test; delete the absent-term-means-zero branch; delete the third conjunct from
`ci-success`; delete the `rulesync` entry from `needs:` *and* its conjunct together; delete
`set -euo pipefail`; replace `node_modules/.bin/rulesync` with bare `rulesync`. Each must be killed by
a **named new test, confirmed by node id**, with no pre-existing test in the same file doing the kill.

**Not adopted: `ruff format`.** Measured — it would reformat **189 of 209** files in `sluice`/`tests`.
This repo uses the linter only (`pyproject.toml:32-43`). D10 is a lint extension, not a formatting one.

## Claims this work falsifies elsewhere

When a change falsifies a stated reason, every site that states it needs updating — the reason going
stale is harder to notice than the conclusion going wrong, because nothing fails.

- **`tests/test_hooks_wiring.py`'s docstring** justifies asserting the tracked source because
  *"CI never runs it"*. After this change CI **does** run it. The conclusion stands — the pytest suite
  stays hermetic and the new job is a separate non-pytest gate — but the reason must be corrected.
- **`.rulesync/hooks.json:2`** points at `.rulesync/rules/CLAUDE.md` for the pinned version. D8 moves
  the pin to `package.json`. Covered in the D8 table.
- **`.gitignore:52-64`'s "nothing enforces it"** becomes false in the direction that matters.
- **11 skill sites + `rules/CLAUDE.md:25`** documented `ruff check sluice tests` as the pre-push bar.
  Left stale, an agent following `path-to-green` would pass locally and land red. **Done in
  `b39e0e3`**, in the same commit as the CI change — a lint whose documented bar disagrees with CI is
  worse than no change at all.

## Error handling

The guard fails loudly at every ambiguity: an unparseable summary, a missing summary, a missing file
and an unexpected feature are all errors, never passes. On failure it prints expected and actual side
by side, plus a hint that the counts are only valid on a fresh checkout.

## Neutrality

No personal data. The lockfile was generated and inspected rather than reasoned about: all `resolved`
URLs are `registry.npmjs.org`, remaining hosts are upstream `funding` links, no home path, email or
token. `package.json` omits `author`. Fixtures are rulesync summary lines carrying only counts and
feature names. D9's `.npmrc` ignore exists precisely to keep a credential from becoming committable —
stated structurally, without reproducing any value.

## Out of scope

- Tracking the generated outputs (rejected above).
- Any change to which rulesync targets or features are enabled, or to any rule *content*.
- Adopting `ruff format`.
- Making a hand-edit of a generated file fail the build — impossible while outputs are gitignored,
  and recorded here so it is not re-attempted.
