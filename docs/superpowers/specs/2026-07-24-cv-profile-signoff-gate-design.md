# CV profile audit — an unsupported flag should block human sign-off, not rendering (#60)

> Status: design, CONVERGED after two `/review-plan` rounds (5 specialists each). Round 1: 0 Critical,
> 5 Medium (all folded). Round 2: verified every fold against source, 1 new Medium (arch-004) + polish
> Lows (all folded). Split from #30 (PR #61), which closed the *deterministic* half of profile
> fabrication. This closes the *qualitative* residual #30 deliberately left open.
>
> **Round 1 folds**: the block is now **latching** — a pending lead is sticky so a re-run cannot
> re-roll the non-deterministic audit into send-ready (rev-001); sign-off routes through the **store
> seam** and a **`Store` Protocol** method, not a per-command `Vault()` (arch-001); `needs_signoff` is
> a **single-line JSON scalar** so `mark` is the existing `update_fields` and only `sign_off` is new
> (arch-002, resolving the YAML-corruption and no-delete-primitive Lows); the `require_signoff` default
> is pinned by a **dedicated test** (test-eng-01).
>
> **Round 2 folds**: `sign_off` returns an **outcome string** `promoted | discarded | collision |
> nothing` (like `upsert`), so the store reports its fresh-content verdict and the CLI never
> reconstructs it from a stale snapshot — triple-corroborated (arch-004 + rev-005 + invariant); the
> `app.py` single-lead-overwrite comment is updated + `--discard` named as the recompose path (rev-004);
> `FakeVault` gains `sign_off` and the conformance test pins all four outcomes (test-eng-04); fail-open
> is folded into strengthening the existing `test_advisory_audit_failure_does_not_block_render`, RED by
> node id (test-eng-06).

## Problem

`sluice/cv/audit.py` runs an advisory LLM audit that classifies every CV claim
`supported | paraphrase | unsupported` against the source bundle, and it **does** catch the
qualitative profile fabrication #30 could not — a numberless invented aspiration ("Motivated by
`<X>`"), which is irreducible to a pure/deterministic gate. But the audit is advisory *only*:
`run_audit` runs in `engine.run_one`, its `audit_flags` land in `CvResult`, and the CV renders,
serves, and its `tailored_cv` pointer is set **regardless**. The flags carry **no consequence** —
nothing forces the human to see or act on a fabricated claim before the CV becomes send-ready.

Making the audit block **rendering** was rejected in #30: it would couple the hard, pure, offline
`validate` gate to a backend call and a model's judgment (today `run_audit` reuses the composing
backend — a model grading its own homework), breaking the property that makes the hard gate testable.
That decision stands.

## The governing idea

Keep the audit advisory *to the model*; give exactly one flag one consequence: an **`unsupported`**
claim withholds the **send-ready pointer** until a human signs off. The candidate is the only party
who knows whether "motivated by X" is true, so the judge earns its keep as *advice to a human who can
overrule it*.

The decisive implementation fact: `apply/select.py` only applies a lead whose `tailored_cv`
frontmatter pointer resolves to an existing served file (else `no_artifact`). **The pointer *is*
sluice's "this CV is send-ready" signal.** So withholding the pointer is the whole gate: no `apply`
change, no status-ladder change.

## Rejected approaches (recorded so the choice is legible)

- **Block rendering on the audit.** Rejected in #30 and here: couples the hard gate to a
  non-deterministic backend call. The hard `validate` gate stays pure.
- **Gate the `apply` transition** (cv serves + sets the pointer; `apply` refuses `shortlist ->
  applied` while unacked). Rejected: pushes a new precondition into the never-regress-adjacent
  apply/status path, and is *weaker* — the send-ready record already exists, so a CV that bypasses
  `sluice apply` is unprotected. Its one merit (decision at apply-time) is already surfaced by the
  `needs-signoff` status.
- **Block both.** Belt-and-suspenders: most invariant surface for marginal gain over withholding the
  pointer, which already makes the lead invisible to apply.
- **Scope the block to the PROFILE region only.** Rejected as the *block* criterion: the audit emits
  the model's *paraphrase* of each claim (`verdict⇥claim⇥cited-id`), which does not reliably
  substring-match back to the profile prose, so region attribution is fragile. Blocking on *any*
  `unsupported` claim is robust, simpler, and a strict superset — it also catches a numberless
  invented WORK claim that cites a real entry (passes `validate`, unsupported by that entry, a
  genuine residual; confirmed: `validate.py`'s WORK check verifies numbers-in-cited-entry, not that a
  bare claim is supported). Over-blocking is the safe direction when the harm is asymmetric and human
  sign-off is the overrule valve. (rev-003 confirmed this is a justified widening, not creep — the
  rationale rides into the PR body.)

## Design

Everything lives in `cv` + one new store method + one CLI subcommand routed through `Sluice`. The
hard `validate` gate, rendering, and `apply` are untouched.

### 4.1 The blocking decision — `sluice/cv/audit.py` (pure, deterministic)

```python
def unsupported_claims(flagged):
    """The subset of run_audit's flagged lines whose verdict is `unsupported`.
    `paraphrase` (same fact, reworded) is legitimate tailoring and must NOT block --
    blocking on it would fire on nearly every CV and train rubber-stamping. This is the
    ONLY thing given a consequence; run_audit and CvResult.audit_flags (both verdicts)
    stay advisory and unchanged."""
    # Match the VERDICT token exactly (the first tab field), not a prefix, so a malformed
    # 'unsupportedness' is not read as 'unsupported' and does not block.
    return [ln for ln in flagged if ln.partition("\t")[0].strip().lower() == "unsupported"]
```

The model call (`run_audit`) stays the sole impure part; the gate is a pure function over its output.

### 4.2 `run_one` — sticky skip + withhold the pointer — `sluice/cv/engine.py`

**Sticky skip (rev-001 — the block must LATCH).** A lead already carrying `pending_cv`/`needs_signoff`
must not be recomposed: `run_audit` is non-deterministic, so a re-run could re-roll a clean verdict
and set `tailored_cv` **without** sign-off — the gate would be a dice reroll, not a hold. So *before*
compose (after selection), if the note frontmatter carries `pending_cv`, `run_one` returns
`CvResult(status="skipped-needs-signoff")` without composing. `run_batch`'s existing skip-guard
(`engine.py:137`, currently `tailored_cv`-only) gains the same `pending_cv` check. This holds in
**both** paths (batch and explicit `--lead`); the only ways out are `cv signoff` (accept) or
`cv signoff --discard` (reject → recomposable). The read of the note is behind the store, per the
seam.

**Withhold (unchanged locus).** After render + serve, `blockers = unsupported_claims(audit_flags) if
cvcfg.require_signoff else []`:
- **blockers present** → do *not* call `set_tailored_cv`. Instead one surgical
  `vault.update_fields(note.ref, {"pending_cv": f"{served} ({date.today().isoformat()})",
  "needs_signoff": json.dumps(blockers)})` (body byte-intact). Return
  `CvResult(status="needs-signoff", served=…, audit_flags=…)`. The served PDF stays in `served_dir`
  (it passed the hard gate) but is inert — `apply/select.py` returns `no_artifact` without the pointer.
- **none** → `set_tailored_cv` as today → `"rendered"`.

`needs_signoff` is a **single-line JSON scalar** (arch-002): flat frontmatter, `_fm_dict`-readable,
`_set_fm`-replaceable, and a claim containing a quote or colon can't corrupt the YAML. `CvResult`'s
status-enumeration docstring (`engine.py:23-26`) gains `needs-signoff` and `skipped-needs-signoff`
**in this same code commit** (arch-003). `dry_run` unchanged (returns before serve). `run_batch`
counts the two new statuses.

### 4.3 The `sign_off` store method — `sluice/core/vault.py` + `sluice/core/protocols.py`

`mark` needs **no new store method** — it is the existing `update_fields` (arch-002). Only the
promote/clear is new:

- `Vault.sign_off(ref, *, accept=True) -> str` — a `_cas_write` transform returning an **outcome
  string** (`promoted | discarded | collision | nothing`), the way `upsert` returns its verdict, so
  the store reports what it did on FRESH content and the CLI never reconstructs it from a stale
  snapshot (arch-004 + rev-005 + invariant round-2, triple-corroborated). The transform, with the
  outcome reset at the top of each run: no `pending_cv` → return the text unchanged, `"nothing"`. Else
  delete `pending_cv` + `needs_signoff`, then — `accept=False` → `"discarded"`; `accept=True` and
  `tailored_cv` **absent** in FRESH content → set `tailored_cv = pending_cv`, `"promoted"`;
  `accept=True` but `tailored_cv` **present** → leave the pointer, `"collision"` (a real CV appeared
  since — a direct `sluice cv --lead X` after discard+recompose; mirrors
  `set_tailored_cv(only_if_absent=…)`, the stale markers are cleared but the pointer is NOT clobbered).
  Resetting the outcome per transform run means a `_cas_write` retry reports the final committed
  branch, and the returned string is **distinct from `_cas_write`'s write-happened bool** — the
  collision case WRITES (clears markers) yet is not `"promoted"`. May raise `VaultConflict` (#16).
- New primitive `_del_fm(inner, key)` — line-anchored delete (invariant Low-1; `_set_fm` only
  replaces/appends). Body byte-intact.
- **`Store` Protocol** (`core/protocols.py`, beside `set_tailored_cv`): add
  `def sign_off(self, ref, *, accept: bool = True) -> str: ...` documenting the four outcomes + its
  `VaultConflict` line, and a conformance property that pins `collision` by name (arch-001/arch-004).
  The `FakeVault` test double (`tests/test_cv_engine.py`, "signature must track protocols.Store
  EXACTLY") gains `sign_off` (test-eng-04).

Idempotence note (rev-001): the transform is idempotent *for a fixed note content*; `pending_cv`
embeds `date.today()` and a per-run served sha, so a fresh compose yields a different value — which is
exactly why the sticky skip (§4.2) prevents a recompose from ever running on a pending lead.

### 4.4 `sluice cv signoff --lead X [--discard] [--yes]` — routed through `Sluice`

Mirror `cmd_cv_run → Sluice(config).compose_cv(...)` (arch-001 — no per-command `Vault()`):

- CLI `cmd_cv_signoff(args, config)` → `Sluice(config).sign_off_cv(lead=args.lead,
  discard=args.discard, yes=args.yes)`.
- `Sluice.sign_off_cv(...)` resolves the shortlist lead by slug (as `compose_cv` does), reads its
  `needs_signoff`/`pending_cv` via `self.store()`, and — for accept — prints the flagged claims + the
  served PDF path for review, prompts unless `--yes`, then `self.store().sign_off(ref)`; for
  `--discard`, `self.store().sign_off(ref, accept=False)`. Maps the returned **outcome string**
  straight to a message (`promoted` / `discarded` / `collision`→"already had a CV" /
  `nothing`→"nothing pending") — no stale-snapshot reconstruction. `VaultConflict` → "conflict, retry",
  caught non-fatally (#16), never a traceback. Lazy imports inside the command (per cli.py).

### 4.5 Config — `require_signoff`

`CvConfig.require_signoff: bool = True` (+ `sluice.yaml.example`). A **safety** gate, not a preference
over which jobs are good, so `empty-config-abstains` does not bind; defaulting it True ships the
safeguard live. It changes existing behaviour (a flagged CV that auto-served now needs sign-off) — the
off-switch (`require_signoff: false`) and the visible `needs-signoff` status make that legible. A
**dedicated test** `assert CvConfig().require_signoff is True` pins the default (test-eng-01); it is
list-only-sweep-invisible (`bool`, not `list`), so it does not touch the #26 guard.

## Behaviour surface

| Condition | `tailored_cv` | Frontmatter | `CvResult.status` | apply sees it? |
| --- | --- | --- | --- | --- |
| gate fail | — | unchanged | `skipped-gate` | no |
| pass, no unsupported flag | set | — | `rendered` | yes |
| pass, unsupported flag, `require_signoff` | withheld | `pending_cv`, `needs_signoff` | `needs-signoff` | no |
| **re-run over a pending lead** | withheld (no recompose) | unchanged | `skipped-needs-signoff` | no |
| `cv signoff` | set (promoted) | cleared | (n/a) | yes |
| `cv signoff --discard` | — | cleared | (n/a) | no → recomposable |
| pass, unsupported flag, `require_signoff=false` | set | — | `rendered` | yes |
| audit backend errors | set (fail-open) | — | `rendered` | yes |

## Invariants

- **Never-clobber**: every write is a surgical CAS frontmatter op (body byte-intact) — `update_fields`
  for the mark, `_cas_write` + `_del_fm` for `sign_off`. No body write; the promote reuses the
  already-served PDF (no re-render).
- **Never-regress**: untouched. `needs_signoff`/`pending_cv` are separate keys; `status` stays
  `shortlist`; `can_advance`/`can_apply` unmodified; apply just doesn't select the lead until the
  pointer exists.
- **Hard gate**: untouched — a CV still must pass `validate` to render at all; this is a softer human
  layer strictly after it.
- **Seam**: sign-off reaches the store only via `Sluice.sign_off_cv → self.store().sign_off`; no
  engine or command constructs its own `Vault()` (invariant 11; arch-001). `sign_off` is a first-class
  `Store` Protocol method with a conformance property.
- **Honest bound (stated)**: on a backend error `run_audit` swallows and returns no flags (audit never
  blocks on backend failure, #30) → no blockers → pointer set (fail-open). The gate is best-effort,
  only as strong as the audit ran — a human-assist, the same class of guarantee as #62's CAS.
- **Fail-loudly**: only lines whose verdict token is exactly `unsupported` count; a garbled line is
  not silently treated as a block.

## Tests (behaviour-asserting, offline; use `FakeBackend(audit_out=…)` — test-eng-02)

1. `unsupported_claims` unit: mixed verdicts → only the `unsupported` subset; `paraphrase` never
   blocks; empty in → empty out.
2. `run_one`: an audit report with one `unsupported` line → `status=="needs-signoff"`, `pending_cv`
   set, `needs_signoff` a JSON scalar, `tailored_cv` **absent**, served PDF present. Builds
   `CvConfig()` with the **default** (the natural case; the default value itself is pinned by test 11).
3. `run_one`: `paraphrase`/`supported` only → `rendered`, `tailored_cv` set, no `needs_signoff`.
4. `require_signoff=false` → an `unsupported` line still yields `rendered` + pointer.
5. `sign_off` accept: returns `"promoted"`, moves `pending_cv -> tailored_cv`, clears both markers;
   `apply/select` then selects the lead (harness apply integration).
6. `sign_off` no-pending: returns `"nothing"`, no-op (nothing written).
7. `sign_off` collision: a `tailored_cv` already present in fresh content → returns `"collision"`,
   the existing `tailored_cv` **value is unchanged** (assert the value, not merely that markers
   cleared — W3 condition), and the stale markers are cleared.
8. Fail-open: **strengthen the existing** `test_advisory_audit_failure_does_not_block_render` (not a
   duplicate — test-eng-06) to assert that under `require_signoff` default + an audit backend that
   **raises**, the CV still `rendered` with `tailored_cv` set; assert the audit backend was
   invoked-and-raised (not vacuous — test-eng-03).
9. **sticky**: a second `run_one`/`run_batch` over a `pending_cv` lead → `skipped-needs-signoff`, no
   recompose (assert the backend's compose was **not** called), `tailored_cv` still absent (rev-001).
10. `sign_off(accept=False)` discard: returns `"discarded"`, clears both markers, no `tailored_cv`; a
    subsequent cv run recomposes the lead (sticky released).
11. `require_signoff` default pinned: `assert CvConfig().require_signoff is True` (test-eng-01).
12. YAML safety: a claim containing `"` and `:` round-trips through `needs_signoff` and back via
    `_fm_dict` + `json.loads` intact (invariant Low-2).
13. `Store` conformance: the contract test exercises `sign_off` on all four outcomes
    (`promoted`/`discarded`/`collision`/`nothing`), asserting the whole fresh frontmatter dict per
    branch; `FakeVault` (`tests/test_cv_engine.py`, "signature must track protocols.Store EXACTLY")
    gains a `sign_off` (arch-001 / test-eng-04).
14. Hermeticity: all under `python -m pytest`, no Camofox, no network.

## Mutation witnesses (required; MOVE/DELETE, checked-hash bytecode, RED by node id)

- Delete the withhold branch → test 2 RED.
- `unsupported_claims` includes `paraphrase` → test 3 RED (exclusion is load-bearing).
- Delete the `tailored_cv`-absent guard inside `sign_off` → test 7 RED (asserts the value, so the
  clobber is caught).
- Delete the sticky `pending_cv` skip in `run_one`/`run_batch` → test 9 RED (the block stops latching).
- Flip `require_signoff` default to `False` → test 11 (the **dedicated** default assertion) RED; test 4
  pins the explicit-`False` path separately (test-eng-01).
- Delete the fail-open swallow so the audit error propagates → the strengthened
  `test_advisory_audit_failure_does_not_block_render` RED **by node id** (it is a pre-existing test, so
  confirm the named node reddens rather than relying on another — test-eng-06).
- `sign_off` returns `_cas_write`'s write-happened bool instead of its own outcome → test 7 RED (the
  collision case writes-but-is-not-`promoted`, so the outcome string is load-bearing — arch-004).

## Neutrality

No employer names, locations, or personal claims in `sluice/` or `tests/`. Test audit reports and CV
fixtures are synthetic (`FakeBackend(audit_out=…)`; placeholder prose like `"Motivated by
placeholder"`). `needs_signoff`/`pending_cv` hold the model's flagged strings only in the *live user
vault*, never the repo. No guard test is weakened (the #26 sweep is list-only; a `bool` default is out
of scope).

## Known residuals / out of scope

- A candidate can still hand-send the raw PDF from `served_dir` out-of-band; the gate protects the
  sluice-managed send-ready *record*, not the filesystem. Same bound as any gate.
- Batch "sign off all pending" is out of scope — per-lead human review is the point (YAGNI).
- Region-scoped (PROFILE-only) blocking is not done (see Rejected approaches).
- The audit still reuses the composing backend (a model grading its own homework); auditor
  independence is a separate concern, unchanged here.

## Config / docs impact

- `CvConfig.require_signoff` + `sluice.yaml.example`.
- `Store` Protocol gains `sign_off` (`core/protocols.py`).
- `CvResult` status docstring gains `needs-signoff` + `skipped-needs-signoff` (in the code commit).
- `sluice/core/app.py`'s single-lead comment (~`:366`, "the direct `--lead` path intentionally
  overwrites") is now contradicted by the sticky skip — update it to name `cv signoff --discard` as the
  sanctioned recompose path (rev-004; in the engine/app code commit).
- `docs/ARCHITECTURE.md`: the two new cv statuses and the `cv signoff [--discard]` command.
- `.rulesync/` (human-gated): the CV-gate invariant paragraph may want a line on the advisory→
  sign-off layer above the hard gate. Flagged for the user, not applied.

## Definition of done

- `unsupported_claims` pure + tested; `run_one` sticky-skip + withhold; `Vault.sign_off` + `_del_fm` +
  `Store` Protocol method + conformance; `Sluice.sign_off_cv`; `sluice cv signoff [--discard]`;
  `require_signoff` config (default True) + example.
- `cv signoff --discard` is the sanctioned way to force a recompose of a held lead (the sticky skip
  removes the old implicit `--lead` overwrite; rev-004).
- All tests pass; all seven mutation witnesses RED by node id; suite fast + offline
  (`python -m pytest`; `ruff check sluice tests`; the checked-hash `compileall` gauntlet).
- Pre-push `/review-pr` (invariant + neutrality + reviewer + test-engineer + architect — a seam is
  touched) + CodeRabbit, per the cadence.

## Commits (planned — dependency order fixed, rev-002)

1. `feat(cv): require_signoff config + unsupported_claims filter`
2. `feat(core): Store.sign_off + _del_fm vault helper + conformance` *(helpers before their caller)*
3. `feat(cv): sticky-skip + withhold tailored_cv on an unsupported audit flag`
4. `feat(cv): sluice cv signoff [--discard] via Sluice.sign_off_cv`
5. `test(cv): sign-off gate behaviour + six mutation-witnessed guards`
6. `docs(cv): needs-signoff statuses + cv signoff in ARCHITECTURE`

## Out of scope

Anything touching the hard `validate` gate, the `apply` transition, the status ladder, or the
auditor's model independence.
