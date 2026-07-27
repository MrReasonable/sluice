# rulesync drift gate — enforce what `.gitignore` currently only asks for (#2)

**Status:** design approved 2026-07-27; revised three times after `/review-plan` (5 reviewers each
round). R1: 1 Critical, 9 High, 7 Medium, 10 Low. R2: 5 High, 9 Medium, 5 Low. R3: 4 High, 6 Medium,
5 Low. The decisions have not changed; several mechanisms underneath them have been replaced twice.
**Issue:** #2. The headline proposal already shipped; the body is stale and is rewritten in place.
**Sub-apps:** none. No `sluice/` change.

**What this document is.** A design, not an executable task list. `superpowers:writing-plans` supplies
ordered tasks, owners and a definition of done next.

**What has already shipped on this branch** (do not re-plan it — an earlier draft listed these as
pending and a generated task list would have re-edited canonical `.rulesync/` for finished work):

| Commit | Work |
|---|---|
| `90061b3` | `style(scripts):` clears four `E741`s in `scripts/diff_vs_legacy.py` by rename |
| `b39e0e3` | `ci(lint):` extends CI's lint to `scripts/` and moves every documented statement of the bar in lockstep (D10) |
| `428606a` | `test(ci):` `tests/test_ci_wiring.py` pins CI's lint targets against the docs that state them |

Everything else below is unimplemented.

**`.rulesync/` edits are user-authorised** (2026-07-27). Authorised scope: `rules/CLAUDE.md` (the
generate command and the ruff target list), `hooks.json`'s `_comment` cross-reference, and the ruff
target list in the `address-comments`, `path-to-green` and `review-plan` skills. No rule *content*
changes. D7 below is deliberately designed so `hooks.json`'s **other** version literal needs no edit
at all, keeping the change inside this scope.

## Five traps, each of which makes the gate certify nothing

The first three were caught while drafting; the last two by reviewers, each inside the fix for its
predecessor. That is the honest measure of how easy this class is to reintroduce.

- **A porcelain check alone is provably insufficient.** A malformed `.rulesync/hooks.json` on a fresh
  clone silently drops 17 files, exits **0**, and leaves `git status --porcelain` **empty**.
- **An absent feature term is a count of zero, not a parse failure to skip.** When hooks drop out the
  summary omits the term rather than printing `0 hooks`.
- **The counts are only valid on a fresh tree.** rulesync skips writing a file whose content already
  matches; on an already-generated tree the same command reports **14** files, not 243.
- **Adding the job to `ci-success`'s `needs:` does NOT make it block.** `ci-success` carries
  `if: always()`, so `needs:` is only a scheduling edge; the sole thing that fails it is the explicit
  `&&` chain in its run block.
- **`npm run` does not pin the binary — it PREPENDS to PATH.** Measured: with no `node_modules/`, a
  bare `rulesync` silently ran a global **9.2.0** and exited 0. An earlier draft asserted the opposite
  on the strength of a test run *with* `node_modules/` present — a non-falsifying check. The path in is
  this document's own advice: "counts are only valid on a fresh tree" invites `git clean -xdf`, which
  deletes the gitignored `node_modules/`.

## Problem

Every AI-tool output rulesync generates is a gitignored build artifact. `.gitignore:52-64` carries
the maintenance obligation in prose — re-audit on a version bump, and whenever a feature is turned
on — and nothing enforces it. That comment exists because the obligation has already been discovered
the hard way once: enabling `.rulesync/hooks.json` made the documented command emit 17 hook files
across 15 directories, two into the **tracked** `.github/` tree.

**What the gate does and does not do.** It does not *enforce* the re-audit — no automated check can
decide whether a newly-emitted path belongs in `.gitignore`. It forces a human to look when one is
due, by failing on the two symptoms a skipped re-audit produces. That is the whole claim.

## The issue text is wrong, and the goal it states is unachievable

Issue #2's body opens "The repo carries **no** agent/assistant instructions at all." True when filed,
false now. More importantly it asks for "generation checked in CI so a hand-edit of a generated file
fails the build." **That cannot be built** — generated outputs are gitignored, so a hand-edit never
reaches CI. Tracking the 243 outputs was considered and rejected: it buys the literal goal at the
cost of a large generated diff in every PR.

Grep the claim, not the code — and **state the reproducing command, never a count**. This document
has falsified its own measured counts three times simply by containing the string it was counting.
Every claim below names the command to re-run instead of a number that rots on the next edit.

## Measurements

Measured 2026-07-27 against rulesync 9.6.3, independently reproduced by reviewers on their own fresh
clones.

| Probe | Result |
|---|---|
| Baseline, fresh clone | exit 0, **243** files: `20 rules + 114 subagents + 92 skills + 17 hooks`; porcelain **empty** |
| Determinism | identical on a second independent fresh clone |
| Survives its own change | still 243 with `package.json`, lockfile and `node_modules/` present |
| Already-generated tree | exit 0, **14** files |
| Skip-if-identical | delete `CLAUDE.md`, re-run → 14 → 15, reappears **byte-identical** |
| Malformed rules file / `.rulesync/` absent | **exit 1** |
| Malformed `hooks.json`, fresh clone | **exit 0**, **226** files, `.github/hooks/` never created, porcelain **empty** |
| D8 command, fresh clone | `npm ci --ignore-scripts && npm run rulesync` → exit 0, 243, binary resolves to `9.6.3` |
| PATH fallback | `npm run` + bare `rulesync`, no `node_modules/` → **silently ran global 9.2.0, exit 0** |
| PATH fallback, fixed | script using `node_modules/.bin/rulesync` → `sh: No such file or directory`, **loud** |
| **Real version delta** | rulesync **9.2.0** against a 9.6.3-audited `.gitignore` → **238** files and porcelain **EMPTY**. D1 reproduced on a genuine bump rather than a synthetic mutation: I1 alone certifies nothing |
| **Summary stream** | `All done!` goes to **stdout**, so `\| tee` captures it; byte-identical piped vs under a pty apart from `\r\n` |
| pipefail, precise behaviour | without pipefail the chain exits 0, but `tee` leaves a **0-byte** capture, so the guard hard-errors and the step still goes red |
| **Install-time scripts** | `tldjs@2.3.2` is the **only** package in the pinned tree declaring one: `postinstall: node ./bin/postinstall.js` |
| `npm audit` | 4 vulnerabilities (2 high, 2 moderate) on the pinned tree; devDependency-only, never shipped to users |

Two observations carried forward: the rules body is shared across targets (the *mechanism* behind the
14-file rewrite was **not** isolated — only the observation is relied on, and only to conclude the
guard must run on a fresh checkout); and `.rulesync/` has no `commands/`, so the expected feature set
is exactly `{rules, subagents, skills, hooks}` and a *new* feature appearing is drift.

## The two invariants

**I1 — Hygiene.** After the generate command, `git status --porcelain` is empty. Catches a gitignore
gap when a bump or newly-enabled feature emits into a fresh path, and a force-added generated file
**whose committed content differs from the generated output** — a byte-identical force-add is
invisible to I1, because rulesync skips writing it.

**I2 — Completeness.** The run reports exactly `{rules: 20, subagents: 114, skills: 92, hooks: 17}`.
Catches the fail-open class I1 provably misses — demonstrated on a real version delta above, where
238 files left the tree clean. I2 is also what guards the generate *flags* once D8 moves them out of
prose: any narrowed `-t`/`-f` moves the counts.

Pinned rather than non-zero because the output set is fully determined by tracked inputs —
`.rulesync/` **and the lockfile** (D4). A per-feature non-zero check passes a partial drop.

## Settled decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Guard hygiene **and** completeness | I1 alone is green on the measured `hooks.json` failure and on a real 9.2.0 delta |
| D2 | Pinned per-feature counts | Catches partial drops; a path manifest's marginal catch is covered by I1 |
| D3 | A `scripts/` guard, pure parser plus thin CI wrapper | Follows the `guard_no_bypass.py` precedent |
| D4 | `package.json` + lockfile + `npm ci --ignore-scripts` | `npx rulesync@9.6.3` pins the direct package but floats its 244-package transitive tree, so I2's pin would be unsound. `--ignore-scripts` closes the one execution path the lockfile does *not* determine — see D11 |
| D5 | Rewrite issue #2's body in place | Keeps the issue number memory and the commit trail reference |
| D6 | npm ecosystem in Dependabot | **Every rulesync bump PR lands red by construction** until `EXPECTED` and the `.gitignore` list are re-audited. Designed, not a defect — say so in the `EXPECTED` comment |
| D7 | Version sweep with a checked exception | See below. The naive form cannot pass and the obvious repairs are lossy or vacuous |
| D8 | One command, one version literal, invoked by explicit path | `9.6.3` lives in `package.json`/lockfile; flags only in the script; `node_modules/.bin/rulesync` so a missing install fails loudly |
| D9 | `.gitignore` gains `/node_modules/` **and** `.npmrc` | `.npmrc` is credential-bearing, and is also how `npm_config_*` could switch on the D11 script |
| D10 | Extend CI lint to `scripts/` | **Shipped** (`90061b3`, `b39e0e3`, `428606a`). Fixed by renaming, not a per-file-ignore: `.rulesync/rules/CLAUDE.md` already classes `scripts/` as production code under the mutation-testing bar, so an ignore block would sit *below* a bar that file asserts |
| D11 | `npm ci --ignore-scripts` | `tldjs@2.3.2`'s `postinstall` is the only install-time script in the tree. It is **env-gated** (`npm_config_tldjs_update_rules === 'true'`) so it does not fetch by default — this is defence-in-depth, not an open hole. It still executes outside the lockfile's determination, which is exactly what D4 claims it does not. Verified: `npm ci --ignore-scripts` exits 0 and rulesync still resolves to 9.6.3 |

## Components

### `package.json` + `package-lock.json`

```json
{
  "name": "sluice-tooling",
  "private": true,
  "scripts": { "rulesync": "node_modules/.bin/rulesync generate -t '*' -f '*'" },
  "devDependencies": { "rulesync": "9.6.3" }
}
```

**The explicit `node_modules/.bin/` path is load-bearing** (trap 5). `npm run` prepends
`node_modules/.bin` to PATH but does not restrict it, so a bare `rulesync` falls through to any
global install; the explicit path exits 127 when absent.

### `.gitignore` (D9)

```gitignore
/node_modules/
.npmrc
```

`/node_modules/` root-anchored like its neighbours. **`.npmrc` deliberately is not**, following the
`.memsearch/` reasoning already in the file: root-anchoring exists only to stop a bare `CLAUDE.md`
swallowing the canonical source, a collision a credential file cannot cause. Both need their
counterpart in `tests/test_no_leaked_files.py` or the ignore is unguarded — `node_modules/` into
`FORBIDDEN_PREFIXES`, `.npmrc` into `FORBIDDEN_COMPONENTS` (correct for a file: `_is_forbidden`
splits on `/` and checks every part).

### `scripts/guard_rulesync_drift.py`

Standard library only.

- `parse_summary(text) -> dict[str, int]` — pure; locates the single `All done!` line and parses its
  parenthetical into feature → count.
- `main(argv) -> int` — takes the capture path as its single positional argument, compares against a
  frozen `EXPECTED`, prints expected-vs-actual on mismatch, returns non-zero.
- `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

| Input | Required behaviour |
|---|---|
| A feature term absent from the parenthetical | reports `0` → mismatch → fail |
| No `All done!` line, **or an empty file** | Hard error, never a pass |
| An unexpected feature (e.g. `commands`) | Mismatch → fail |
| More than one summary line | Hard error |

The empty-file row is load-bearing: it is what makes the pipefail analysis true, since a swallowed
pipeline failure surfaces as a 0-byte capture and nothing else.

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
      - run: npm ci --ignore-scripts
      - run: |
          set -euo pipefail
          npm run rulesync | tee "$RUNNER_TEMP/rulesync-output.txt"
          python scripts/guard_rulesync_drift.py "$RUNNER_TEMP/rulesync-output.txt"
          if [ -n "$(git status --porcelain)" ]; then
            git status --porcelain
            exit 1
          fi
```

`npm run rulesync` without `--silent`, byte-identical to the documented command; npm's banner
contains no `All done!` so it cannot confuse the parser.

- **`set -o pipefail`.** A `run:` with no `shell:` key runs as `bash -e {0}` — `-e` but not pipefail.
  Precisely: without it the failing chain *still* goes red, via the 0-byte capture. pipefail is kept
  because it makes the real cause the reported cause, not because it is the difference between red
  and green. An earlier draft overstated this.
- **`setup-python` required** — the job calls `python`.
- **Capture in `$RUNNER_TEMP`** — a scratch file beside the checkout is untracked and trips I1.
- **I2 before I1** — a fail-open produces a clean tree.

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

No `name:` key on the new job. Dependabot gains `package-ecosystem: npm`, `directory: /`, weekly,
grouped, `commit-message: {prefix: chore, include: scope}`.

### `.rulesync/` and `.gitignore` prose (D8, user-authorised — **pending**)

| Site | Change |
|---|---|
| `.rulesync/rules/CLAUDE.md` generate command | → `npm ci --ignore-scripts && npm run rulesync` |
| `.rulesync/hooks.json` `_comment` trailing pointer | "…the pinned version in `.rulesync/rules/CLAUDE.md`" → `package.json`. That comment calls itself **the ONLY defence** against a bump silently dropping the hook command; leaving it pointing at a file that no longer names a version is the drift that matters most |
| `.gitignore` generate command | → `npm ci --ignore-scripts && npm run rulesync` |
| `.gitignore` second version literal | the "…rulesync 9.6.3 knows about" clause — reword to name no version |
| `.gitignore:56-61` | add a pointer to `scripts/guard_rulesync_drift.py` as the enforcer |

The ruff target-list rows that used to sit in this table have shipped (`b39e0e3`) and are removed.

### Version sweep (D7)

The naive form — "assert the only version literals are in `package.json`" — **cannot pass**: run
`git grep -n '9\.6\.3'` to see the live set. After D8 one legitimate literal survives, at the head of
`.rulesync/hooks.json`'s `_comment`: *"This is rulesync 9.6.3's CANONICAL schema"*. Three reviewers
proposed three repairs and each loses something:

- *Reword it* — loses which version the hook schema was verified against, in the one comment that is
  the only defence against a bump silently dropping the command.
- *Exclude `.rulesync/`* — vacuous exactly where it matters.
- *Sweep `rulesync@<version>` instead of the bare literal* — never sees that clause at all.

So: **assert it, don't erase it.** The sweep reads the version from `package.json` and requires

- zero occurrences outside `package.json`, `package-lock.json` and `.rulesync/hooks.json`;
- `.rulesync/hooks.json`'s literal **equals** `package.json`'s version.

The record survives, drift becomes detectable, and on a bump the sweep goes red until a human
re-verifies the emitted `.claude/settings.json` — which is precisely what that comment demands and
today has no way to enforce. It also needs no edit to the clause, keeping D8 inside its authorised
scope.

Non-vacuity at both ends: assert `package.json`'s version parses non-empty, and that the sweep
examined a non-zero number of files. Exclude `docs/superpowers/`, a dated record — the convention is
a dated *superseded* note, never rewriting history.

### Tests

Offline and hermetic; the suite never shells out to npm or npx.

**`tests/test_guard_rulesync_drift.py`.** Fixtures are real captured output:

```text
🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)
🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)
```

The second is what a malformed `hooks.json` produces — it omits the term rather than printing
`0 hooks`. Both are captured output including the emoji.

**Correction (2026-07-27, after CodeRabbit's review of #77).** An earlier revision of this section
claimed the fixtures' realness meant a version bump "produces a loud parse error, not a silent
pass" *in these tests*. That is false, and the argument was used to reject a review finding before
being reversed. A fixture is a constant in a test file: once frozen, a copied capture and a
hand-authored string are indistinguishable, and neither notices that rulesync has started phrasing
its summary differently. Provenance buys exactly one thing — at capture time the parser and
`EXPECTED` were validated against real output rather than an assumption. Drift against the *live*
generator is caught only by the `rulesync` CI job, which pipes the pinned binary's actual stdout
into the guard. Say that in the fixture comment, not the stronger claim.

Parser cases: 243 passes; 226 reports `hooks: 0`; no summary line errors; an unexpected feature
fails; two summary lines error.

**Cases through `main`.** `tests/test_guard_no_bypass.py:26-30` states the lesson in this repo's own
words: *"the hook contract IS the exit code, so deleting `sys.exit(main())` would leave such a suite
green while the guard did nothing."* Via a `tmp_path` capture: 0 on the 243 line; non-zero on
`17 → 16` (which only equality catches); non-zero on a missing file; **non-zero on an empty file**
(`test_an_empty_capture_file_is_a_hard_error_not_a_pass` — the pipefail rationale rests on this and
nothing tested it); plus a subprocess test pinning `__main__` → exit code, driving a **failing**
capture, since a passing one cannot distinguish `sys.exit(main())` from a bare `main()`.

**`tests/test_ci_wiring.py`** — already exists (`428606a`) with the lint-bar half. The gate adds:

- `assert "rulesync" in ci-success.needs`, **separately** from the consistency check — enumerating
  `needs:` against the run script is only a *consistency* check, and deleting the entry and its
  conjunct together leaves both sides consistent on an unwired gate.
- Every job in `needs:` appears as `needs.<id>.result` in the run script, enumerated from parsed
  YAML, plus a non-vacuity assert that `needs` is non-empty.
- `set -euo pipefail` present; capture path under `$RUNNER_TEMP`.
- **Ordering by index lookup, not substring presence** — the guard call must appear *before* the
  porcelain check. A substring test passes when either is deleted.
- The invocation contract D4 rests on: `scripts.rulesync` byte-exact, `npm ci --ignore-scripts`
  preceding the guard, and no `npx` anywhere in the job. Substituting `npx rulesync@9.6.3 generate …`
  back keeps counts identical and I2 green while silently destroying the locked transitive tree.

**`tests/test_no_leaked_files.py`** — `test_the_gate_covers_every_path_gitignore_covers` iterates
`FORBIDDEN_EXACT + FORBIDDEN_PREFIXES` then *hand-asserts* `.memsearch`, so `FORBIDDEN_COMPONENTS`
is unenumerated and `.npmrc` would fall outside coverage. Enumerate all three tuples and delete the
hand-written line.

**Mutation testing.** Mutate by **moving or deleting**, never adding; run
`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first; **commit
before mutating** — an untracked new file cannot be restored by `git checkout --`, and the empty
post-run diff hides it. Enumerated mutants: delete `sys.exit(...)` from `__main__`; make the
`EXPECTED` comparison a subset test; delete the absent-term-means-zero branch; delete the empty-file
guard; delete `ci-success`'s third conjunct; delete the `rulesync` `needs:` entry *and* its conjunct
together; delete `set -euo pipefail`; replace `node_modules/.bin/rulesync` with bare `rulesync`;
delete the porcelain block. Each must be killed by a **named new test, confirmed by node id**, with
no pre-existing test in the same file doing the killing.

**Not adopted: `ruff format`** — it would reformat the great majority of `sluice`/`tests`. This repo
uses the linter only (`pyproject.toml:32-43`).

## Claims this work falsifies elsewhere

A stale *reason* is harder to notice than a wrong conclusion, because nothing fails.

- **`tests/test_hooks_wiring.py`'s docstring** justifies asserting the tracked source because
  *"CI never runs it"*. After this change CI **does**. The conclusion stands — the pytest suite stays
  hermetic and the new job is a separate non-pytest gate — but the reason must be corrected.
- **`.rulesync/hooks.json`'s pointer** at `rules/CLAUDE.md` for the pinned version (D8 table).
- **`.gitignore`'s "nothing enforces it"** becomes false in the direction that matters.

## Error handling

The guard fails loudly at every ambiguity — unparseable summary, missing summary, missing file, empty
file, unexpected feature. On failure it prints expected and actual side by side, plus a hint that the
counts are only valid on a fresh checkout.

## Neutrality

No personal data. The lockfile was generated and inspected: all `resolved` URLs are
`registry.npmjs.org`, remaining hosts are upstream `funding` links, no home path, email or token.
`package.json` omits `author`. Fixtures carry only counts and feature names. D9's `.npmrc` ignore
exists to keep a credential from becoming committable, stated structurally without reproducing any
value.

## Out of scope

- Tracking the generated outputs; changing which targets or features are enabled; any rule *content*
  change; adopting `ruff format`.
- Making a hand-edit of a generated file fail the build — impossible while outputs are gitignored.
