# No-bypass guard — design

> **Superseded 2026-07-28:** the regenerate command named below is stale — it is now `npm ci --ignore-scripts && npm run rulesync`, which reads the version AND the target list from `package.json`. Running the `-t '*'` form as written re-creates the ~34 legacy output directories this repo no longer generates. The steps are left as executed; only the command has moved.

- **Date**: 2026-07-16
- **Status**: reviewed (5 specialists, 0 Critical / 2 High / 7 Medium / 10 Low); findings folded in; approved for implementation
- **Origin**: branch `chore/no-bypass-guard` (unmerged); user decision 2026-07-16 to land it, repairing all four rules

## Goal

Land the local no-bypass `PreToolUse` guard onto main **actually wired**, so an agent that drifts
toward `gh pr merge --admin` (or an equivalent bypass) hits an immediate, explanatory non-zero exit
at the moment of temptation, rather than discovering the refusal a round-trip later.

## Background

The guard exists because prose did not work. `.rulesync/skills/path-to-green/SKILL.md` forbade
`gh pr merge --admin` in three separate places, and an agent read a "shepherd these to merge"
instruction as authorisation to run it anyway, reasoning that the reviewer was rate-limited and the
gate was therefore "unsatisfiable". A gate that holds only until it is inconvenient is not a gate.

**The threat model is RATIONALISATION, not EVASION.** The agent this stops is one that wants to
comply and is drifting.

Three facts set the scope:

1. **The server-side gate is already authoritative.** The `qa-gates` ruleset carries
   `bypass_actors: []`, `enforcement: active`, and the rules `deletion`, `non_fast_forward`,
   `required_linear_history`, `pull_request`, `required_status_checks`. This guard is therefore
   **defence in depth, not the defence**. Its marginal value is the immediate, explanatory local
   tripwire — the exit code alone is already guaranteed on the far side of the trust boundary.
2. **The branch is mostly already-merged history.** `chore/no-bypass-guard` diverges *before* the
   pluggable-core squash-merges, so its 13-commit / 61-file diff over-reports. A content audit
   (`git diff --diff-filter=A --name-only main..chore/no-bypass-guard`) shows only four files are
   genuinely absent from main: the three guard files, plus `tests/test_cli_backend_selection.py` — a
   stale pre-rename copy (main has `tests/test_backend_selection.py`) which we explicitly do **not**
   want. This is a cherry-pick of specific files, never a merge of the branch.
3. **The ported artifacts are not fit to land as-is.** Plan review found two false-positive classes
   in the guard's denylist, a crash on valid-JSON non-object payloads, a partly vacuous test suite,
   an untested wiring layer, and a `.gitignore` gap. All are repaired here rather than ported.

## Key finding — the branch's own wiring is wrong

The branch ships `.rulesync/hooks.json` as an inert `{"hooks": {}}` with a comment asserting:

> "rulesync 9.6.3 does NOT support PreToolUse for claudecode -- it skips this event and emits an
> empty hooks object."

**That comment is a misdiagnosis of a schema error.** Verified empirically against rulesync 9.6.3,
and independently reproduced by two reviewers:

- rulesync's canonical input uses its **own camelCase event names** (`preToolUse`, from
  `CLAUDE_HOOK_EVENTS`), *not* Claude Code's native PascalCase `PreToolUse`.
- Hook definitions are **flat** (`{matcher, type, command}`). rulesync generates Claude Code's
  nested `{matcher, hooks: [{type, command}]}` shape itself.
- Feeding the native PascalCase/nested shape produces exactly the branch's symptom: the event is
  filtered out as "not supported" *and* `def.command` reads as `undefined`, so the command is
  silently dropped.
- rulesync's Zod schema **requires a top-level `hooks` record** even when only a tool-scoped
  override is used.

**rulesync fails open on every one of these mistakes.** A wrong shape writes a `command`-less hook
and prints "All done!"; a missing top-level `hooks` prints a Zod error, then `✓ All files are up to
date`, writes nothing, and **exits 0**. This is the same silent-drop shape that produced the inert
guard in the first place, and it is why the wiring gets a test (below) rather than a prose comment.

## Design

Five files.

### 1. `scripts/guard_no_bypass.py` — repaired, not ported

Pure standard library. Reads the `PreToolUse` JSON payload on stdin, inspects `tool_input.command`,
exits `2` with an explanation on a match, `0` otherwise.

**Repair 1 — tokenise, don't regex the raw string.** Both false-positive classes share one root
cause: lookaheads scanning the whole command string with no argument-position or quoting awareness.
Replace the regex denylist with tokenisation, split on shell separators into segments, and match
each segment by its leading tokens and its actual argument tokens. The tokeniser collapses a quoted
`-n` into part of a single message token, which is what kills the false positive at the root.

**Repair 1a — the tokeniser must see operators, or it trades one bug for a worse one.** PR review
caught this: a first pass used `shlex.split`, which splits on **whitespace only**. So `;` glues to
its neighbour (`cd /tmp; gh pr merge --admin` → token `/tmp;`, and `gh` never heads a segment), and
newlines vanish entirely (a multi-line block collapses to one segment, matching only its first
command). That version allowed five forms the *old regex had caught*, because a whole-string regex
does not care where the command starts. It was a strict regression, and its own test corpus pinned
only the conventionally-spaced `&&`, which is why it stayed green. Use
`shlex.shlex(line, posix=True, punctuation_chars=True)` with `whitespace_split=True`, per line,
appending `;` per newline. Pin **every** separator, not one.

**Repair 1b — normalise before matching on position.** An index-based match demands the subcommand
at index 1, so `git -C /path push --force origin main`, `GIT_DIR=x git push ...` and
`gh -R o/r pr merge --admin` all slip through. None are evasion: the harness instructs agents to use
absolute paths, making `git -C` the form a drifting agent naturally types. Strip leading environment
assignments and the global options that precede a subcommand (consuming their values), and compare
flags by their `=`-stripped base so `--admin=true` cannot slip either.

**Repair 2 — `-n` is command-specific.** For `git commit`, `-n` *is* `--no-verify`. For `git push`,
`-n` is `--dry-run` — the safest push there is. The ported rule blocked both. Block `-n` only for
`commit`; block `--no-verify` for both.

**Repair 3 — parse the refspec, don't grep for `main`.** The ported force-push rule blocked
`git push --force-with-lease origin fix/main-menu`, because `--force\b` matches inside
`--force-with-lease` and `\bmain\b` matches inside `fix/main-menu`. Instead: detect a force
indicator (`--force`, `-f`, `--force-with-lease`, `--force-if-includes`, or a leading `+` on the
refspec), then extract the refspec's **destination** (`src:dst` → `dst`, stripping `refs/heads/`)
and compare it to `main` exactly. A bare `git push --force-with-lease` with no refspec is allowed —
the target is undeterminable from the string, and `non_fast_forward` covers main server-side.

**Repair 4 — a valid-JSON non-object must not crash.** `json.load` on `null` returns `None`, and the
ported code then calls `None.get(...)` → `AttributeError` → exit 1 with a traceback. The existing
`try/except` only catches the *decode*. Type-check the payload, the `tool_input`, and the `command`
before use.

Blocks (after repair):

- `gh pr merge --admin`.
- `gh api -X PUT ... /pulls/N/merge` — the same act via the REST endpoint, needing no
  obfuscation. **PUT only**: `gh api` defaults to GET, and `GET /pulls/N/merge` is the
  read-only "is this PR merged?" check (204/404). Matching the path alone refuses that
  harmless read while announcing a merge that is not happening — a version of this guard did
  exactly that, and blocked its own author mid-review with the lie.
- `git commit --no-verify` / `-n`; `git push --no-verify`.
- Force-push whose refspec destination is `main`, including the `+main` form.

Allows (asserted): `gh pr merge --rebase --delete-branch`; reading PRs via `gh api`; ordinary commits
**including `git commit -m "feat: add -n flag docs"`**; `git push -n` (dry-run); `--force-with-lease`
on any non-main branch **including `fix/main-menu`**; bare `git push --force-with-lease`;
`pytest -n auto`.

**Structure for testability.** Expose a pure `blocked_reason(command: str) -> str | None`. `main()`
does I/O only: read stdin, call it, print to stderr, return 2/0. This is what makes the fast
in-process test path possible without losing the process contract (below).

**Docstring.** The current text says the ruleset "carries `bypass_actors: [{RepositoryRole,
bypass_mode: always}]`. Remove it and `--admin` becomes impossible." That removal has since
happened. But the replacement must **not** assert `bypass_actors: []` either: that is mutable
server-side state, and a docstring claiming a protection that was later removed is worse than a
stale one — it manufactures confidence. Phrase it state-independently: describe what this layer *is*
(a local tripwire, subordinate to whatever the ruleset enforces), not what the ruleset currently
holds.

### 2. `tests/test_guard_no_bypass.py` — repaired, not ported

**Hybrid, not wholesale in-process.** Measured on a quiet machine, 5 runs, median: main is 518 tests
/ ~0.97s; the ported all-subprocess suite's 16 spawns add ~0.41–0.46s → ~1.26–1.30s. But converting
*everything* in-process would go inert: **the hook contract is the process exit code**, so deleting
`sys.exit(main())` would leave a wholly in-process suite green while the guard did nothing. Keep a
small number of subprocess cases for the process contract; run the denylist cases against
`blocked_reason` in-process. The hybrid lands at ~1.04s — a marginal ~+0.07s, of which the two
subprocess spawns are ~0.03s each.

Two honesty notes. The suite crossing ~1.0s is **pre-existing on main**, not introduced here; the
"well under a second" line in `.rulesync/rules/CLAUDE.md` is already optimistic, and this change does
not meaningfully move it. And these numbers are load-sensitive: under concurrent work the same suite
measures 1.5–2.1s with or without these tests, so any figure quoted here must come from a quiet
machine and a median, not a single run.

**Assert stderr, not just the exit code.** Two reasons. The explanation *is* the product — the exit
code is already guaranteed server-side, so `_REPORT_NOT_ROUTE_AROUND` could be deleted today and
every ported test would stay green. And `== 2` is not discriminating: **CPython also exits 2 when it
cannot open the script**, so a broken hook path would pass the ported assertion. Asserting an stderr
substring fixes both in one spawn.

**New cases** (all red against the ported guard): `git commit -m "feat: add -n flag docs"`,
`git push -n origin main`, `git push --force-with-lease origin fix/main-menu`, bare
`git push --force-with-lease`, and the non-object payloads `null` / `[]`.

**Synthetic slug.** The ported fixtures hardcode `MrReasonable/sluice`. Three reviewers converged:
this is not a privacy leak — it is the repo's own public identity, and `git remote -v` carries it
anyway — but the matcher never inspects the org, so a synthetic `acme/widget` keeps every case green
*and* proves the regex is not repo-coupled. Free improvement; take it.

### 3. `tests/test_hooks_wiring.py` — new

The wiring is the only genuinely new work here and the only part that has already silently broken
once, and rulesync fails open on every failure mode. Assert the **tracked source**
(`.rulesync/hooks.json`), not the generated `.claude/settings.json` — the latter is gitignored and
CI never runs rulesync, so it cannot be asserted hermetically.

Pins: the top-level `hooks` record exists (Zod-required); the event key is camelCase `preToolUse`
and **not** PascalCase `PreToolUse`; each definition is flat (`type`/`command` present, no nested
`hooks` list); the command references `guard_no_bypass.py`; and the referenced script exists on
disk. Offline, hermetic, stdlib-only. Red today.

### 4. `.rulesync/hooks.json` — new canonical wiring

```json
{
  "version": 1,
  "hooks": {},
  "claudecode": {
    "hooks": {
      "preToolUse": [
        {
          "matcher": "Bash",
          "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR/scripts/guard_no_bypass.py\""
        }
      ]
    }
  }
}
```

- **`claudecode`-scoped, not shared.** The guard parses Claude Code's specific stdin payload shape.
  Verified: nothing leaks into `.codex/` under `generate -t claudecode,codexcli`.
- **`$CLAUDE_PROJECT_DIR`-anchored.** cwd-independent. rulesync only rewrites `./`-relative
  commands, so this string is emitted verbatim and Claude Code expands it at runtime.
- **Top-level `hooks: {}`.** Required by rulesync's Zod schema even when only the tool-scoped
  override carries content.
- A short `_comment` records the flat-camelCase gotcha (verified to survive generation), but the
  *test* is the defence — prose is what failed for `--admin`.

### 5. `.gitignore` — close the `/.github/hooks/` gap

Adding `.rulesync/hooks.json` turns on rulesync's `hooks` feature, so the documented
`generate -t '*' -f '*'` emits 17 hook files across 15 directories where it previously emitted none.
Fifteen are already ignored. **`.github/hooks/copilot-hooks.json` and
`.github/hooks/copilotcli-hooks.json` are not** — `.gitignore` anchors `/.github/agents/` and
`/.github/skills/`, but not `/.github/hooks/`, and `.github/` is tracked (`workflows/ci.yml`).
Verified with `git check-ignore`. Without this the DoD's clean-tree check fails on the first run,
and a `git add -A` would commit build artifacts into the tracked workflows tree — the #20 failure
mode.

Add `/.github/hooks/` under the existing AI-tool-outputs block, root-anchored in the established
style, never a bare `.github/`.

## Non-goals

- **Bumping rulesync.** 9.6.3 suffices.
- **Changing the server-side ruleset.** It is already correct.
- **Making the guard evasion-proof.** A denylist over a shell command *string* is unsound by
  construction: `--ad""min`, `$VAR`, base64, and `python3 -c` all reconstitute a blocked token.
  Tokenising narrows the accidental-false-positive surface; it does not make the guard sound against
  a determined evader, and must not be claimed to. Closing the holes a *well-intentioned* agent
  would plausibly type is the goal.
- **Linting `scripts/` in CI.** The DoD lints the new file explicitly; widening CI's ruff scope
  would newly lint the pre-existing `scripts/diff_vs_legacy.py` and belongs in its own change.

## Definition of done

Every step copy-pasteable:

- `npx rulesync@9.6.3 generate -t '*' -f '*'` then `git status --porcelain` → shows **only** the
  intended tracked files. (This is a *content* check by necessity: rulesync exits 0 on schema
  violations, so an exit-code check would pass a silently-dropped hook. Never relax it.)
- `python3 -c "import json;d=json.load(open('.claude/settings.json'));print(json.dumps(d['hooks']['PreToolUse'],indent=2))"`
  → shows the guard hook with its `command` intact.
- `ruff check sluice tests scripts/guard_no_bypass.py` → clean.
- `python -m pytest` → green; record the added test count and the runtime delta (target: stay under
  ~1s).
- Behaviour spot-check, exact commands:
  - `echo '{"tool_input":{"command":"gh pr merge 1 --admin"}}' | python3 scripts/guard_no_bypass.py; echo "exit=$?"`
    → `exit=2` plus the explanation on stderr.
  - `echo '{"tool_input":{"command":"gh pr merge 1 --rebase --delete-branch"}}' | python3 scripts/guard_no_bypass.py; echo "exit=$?"`
    → `exit=0`.

## Risks and notes

- **The hook loads at session start.** It will not fire in the session that generates it. It
  protects future sessions and fresh clones.
- **Activation depends on the documented regenerate step.** `.claude/settings.json` is gitignored
  and rulesync-generated, so the guard is live only after the post-clone
  `npx rulesync@9.6.3 generate -t '*' -f '*'` that CLAUDE.md already documents as required. This is
  how every AI-tool output in this repo works.
- **rulesync 9.6.3 coupling, two surfaces.** `.gitignore`'s existing "re-audit on version bump" note
  covers the *target set*; the flat/camelCase hook *schema* is a second coupled surface. A version
  bump must re-audit both. Note it alongside the existing comment.
- **`--no-verify` currently guards nothing.** This repo has no local git hooks (only `.sample`
  files, `core.hooksPath` unset, no pre-commit config). The rule is kept — repaired — as
  future-proofing against hooks being added, per the user's "repair all" decision.
- **`.rulesync/` addition: USER-APPROVED** (2026-07-16). Canonical and human-gated; precedent is the
  user-approved `.rulesync/rules/CLAUDE.md` edit in #19.

## Process

Proportionate: brainstorm → `/review-plan` (5 specialists) → direct implement → verify → the full
non-negotiable merge gate (CI green, CodeRabbit `APPROVED` on the head SHA, zero unresolved threads,
`gh pr merge --rebase --delete-branch`). Then `/review-pr` **and** CodeRabbit, per the standing
lesson that CodeRabbit finds real bugs the specialist agents miss.

Commit: `chore(scripts): wire the no-bypass PreToolUse guard through rulesync`.
