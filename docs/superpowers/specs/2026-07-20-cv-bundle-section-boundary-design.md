# CV bundle parser — the negatives block must not widen the INVENTED METRIC allowlist

- **Date**: 2026-07-20
- **Status**: DRAFT — awaiting `/review-plan`.
- **Origin**: issue #31. Pre-existing; independent of #30, which is filed against the same file.
- **Scope decision (user-confirmed)**: fix the section-boundary defect only. The BASELINE half of the
  same walk (§"Behaviour surface") is **pinned by a test, not changed** — permitting baseline numbers
  is #30's design question, not this one. Test port to renderer-backed fixtures **is** in scope
  (user-chosen over the additive-only option), sequenced as its own commit.

## Problem

`_bundle_ids_and_nums` in `sluice/cv/validate.py` attributes every number it sees to the most recent
`[id]` line. `render_bundle` in `sluice/cv/bundle.py` appends the **negative constraints** block after
the last entry:

```python
lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
lines += [f"- {n}" for n in bundle["negatives"]]
```

Those `- ...` lines match neither the `[id]` pattern nor any boundary the parser recognises, so they
fall through to the `elif cur:` branch while `cur` still points at the **last-ranked entry**. Every
number in the do-not-say list is unioned into that entry's permitted-number set.

`validate()` then computes `invented = bullet_nums - union`, so a WORK bullet citing that entry may
carry a figure that appears **only** in a negative constraint and still pass.

The anti-fabrication mechanism slightly widens the hole it exists to close, and it does so for exactly
the class of number most likely to reappear: negative constraints exist *because* the model has
hallucinated those figures before.

### Verified, not assumed

Both halves were reproduced against the real renderer before this spec was written. Parser level:

```
[SO1] (Solarflux) EM  | metrics=3 8      -> nums={'3','8'}
[TR1] (Trueverse) CTO | metrics=90       -> nums={'90','500','99'}   <- 500 and 99 are NEGATIVES
```

End to end, with `negatives=["never claim 500 users"]` and the bullet
`- Scaled the platform to 500 users [TR1]`:

```
violations: []
```

The hard gate returns clean and the fabricated figure renders. This is the defect, confirmed at the
level that matters rather than inferred from the code.

### Why the existing tests did not catch it

`tests/test_cv_validate.py` builds its `BUNDLE` by hand:

```python
BUNDLE = "\n".join([
    "[SF3] (Solarflux) Grew team from 3 to 8 | metrics=3 8",
    ...
])
```

That fixture has no `===` section headers, no entry bodies, no baseline and no negatives. The parser
has therefore **never been exercised against real `render_bundle` output**. The unrealism of the
fixture is the reason a defect in the parser/renderer contract survived a suite that otherwise covers
this gate well.

## The governing rule

**A number is attributable to an entry only while the walk is genuinely inside that entry.** `cur` is
set exclusively by an `[id]` line; it must be cleared by anything that ends the entry list.

## Design

### 1. Section headers clear `cur` — `sluice/cv/validate.py`

Add one branch to `_bundle_ids_and_nums`, ahead of the `elif cur:` fallthrough:

```python
# render_bundle emits `=== SECTION ===` headers; the NEGATIVE CONSTRAINTS block is
# the one that lands AFTER the last [id]. Without this reset its lines fall through
# to `elif cur` and union the do-not-say numbers into the last-ranked entry's
# allowlist -- so the one guard aimed at those figures stops firing on exactly them.
# Requiring `===` at BOTH ends keeps a stray body line from resetting mid-entry; if
# one ever did, the entry's later numbers are dropped and a legitimate bullet is
# flagged INVENTED METRIC -- visible and fixable, never a silent pass. (#31)
_SECTION = re.compile(r"^\s*={3,}.*={3,}\s*$")
```

```python
for line in bundle_text.splitlines():
    if _SECTION.match(line):
        cur = None
        continue
    m = re.match(r"\[([^\]]+)\]", line)
    ...
```

Chosen over two alternatives:

- **Bucket non-entry sections under sentinel keys.** Retains baseline/negatives numbers addressably
  for #30 to consume. Rejected as speculative: #30 has no design yet, and this rule is a strict
  prerequisite for that version too, so deferring costs #30 nothing.
- **Special-case the `NEGATIVE CONSTRAINTS` header.** Over-fits to one caller; any future section
  emitted between the entries and the negatives would leak again.

### 2. Behaviour surface

| Bundle region | Before | After |
| --- | --- | --- |
| Entry `[id]` line and its body | attributed to that entry | **unchanged** |
| `=== NEGATIVE CONSTRAINTS ===` block | attributed to the last-ranked entry | not attributed |
| `=== BASELINE CV ===` block | contributes no numbers (`cur` is `None` throughout) | **unchanged** |

The baseline row is unchanged in both columns by design. §"Scope decision" records why, and Test 3
pins it so #30 must change it deliberately.

`validate()`'s signature, purity and determinism are untouched; this is a fix inside one private
helper.

## Tests — `tests/test_cv_validate.py`

All new tests build their bundle through `build_bundle` + `render_bundle`, so the parser is checked
against the renderer's real output and the two cannot drift apart again.

1. **The regression.** A bullet citing the last-ranked entry, using a number that appears only in
   `negatives`, produces an `INVENTED METRIC` violation. Fails on today's tree (returns `[]`).
2. **The fix is not over-broad.** A legitimate bullet citing that same entry, using a number from its
   own `metrics=`, still passes. Guards against "fixed it by narrowing the allowlist to nothing."
3. **Baseline numbers stay non-permitted.** A bullet citing an entry, using a number that appears only
   in the baseline block, is flagged. Pins today's behaviour explicitly so #30 changes it on purpose.
4. **Port the existing tests** to renderer-backed fixtures — own commit, after 1–3 are green.

### Port discipline

The port is where this plan can quietly go wrong, so it is constrained:

- **Every existing test function survives by name and keeps its assertion.** No test is dropped,
  merged or weakened to make the port tidy. This is the trap #26 records (a sweep that replaced an
  enumeration silently lost `assert not c.baseline_rel.startswith("/")`); it must not recur inside a
  diff that cites the same lesson.
- **Ids change and that is expected.** `assign_codes` sequences per prefix from 1, so the hand-chosen
  `SF3`/`TV1`/`TV4` become `SF1`/`TV1`/`TV2` under a `prefix_map`. Ids are arbitrary labels; the
  assertions are what matter.
- **The port must be proven inert.** Each ported test is mutation-witnessed — the guard it covers is
  broken, the test observed red, the guard restored byte-identically. A port that silently stopped
  asserting is otherwise indistinguishable from a passing one.

### Mutation witnesses (required, per CLAUDE.md)

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` **once first**.

| Mutation | Expected |
| --- | --- |
| Delete the `cur = None` line | Test 1 red |
| Delete the whole `_SECTION` branch | Test 1 red |
| Move the `_SECTION` branch *after* the `elif cur:` arm | Test 1 red (unreachable branch) |

Mutate by **moving or deleting**, never by adding: a check added beside the original leaves the
original firing and the suite green, which reads as "this test is inert."

## Definition of done

- `python -m pytest` green (712 tests today, +3 expected before the port).
- `ruff check sluice tests` clean.
- Every mutation in the table witnessed red, then restored byte-identically.
- Every test function present in `tests/test_cv_validate.py` before the port is still present after it.
- No change to `validate()`'s signature or to any config, so no `sluice.yaml.example` or
  `docs/ARCHITECTURE.md` update is required. (Confirm at review: this claim is checkable, not assumed.)

## Out of scope

- **#30** — gating the PROFILE section. Needs its own design; this fix is a prerequisite for the
  version of it that reuses `nums`.
- **#28** — falsified as written; unrelated to this file.
- Any change to `render_bundle`'s output format. The parser is what is wrong here, not the renderer.

## Commits

1. `fix(cv): a bundle section header ends the entry it follows (#31)` — the fix + tests 1–3.
2. `test(cv): build validate fixtures through render_bundle (#31)` — the port.
