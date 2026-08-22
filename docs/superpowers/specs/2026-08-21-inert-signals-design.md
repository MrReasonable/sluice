# Inert signals — enforce the discarded CV style warnings, and stop caching failed JD fetches

- **Date**: 2026-08-21
- **Status**: DESIGNED, revised after two plan-review rounds (5 reviewers each; round 1: 45 findings,
  22 High; round 2: 23 findings, 13 High, **all of them defects in round 1's own fixes**). Not yet
  implemented. See **Revision history**.
- **Next artefact**: an implementation plan (`superpowers:writing-plans`). This document is a DESIGN
  spec — no numbered task breakdown, no owners, no definition of done; those belong to the plan.
- **Issues**: **#167** (`cv/engine.py` binds the slop linter's phrase matches and never reads them, so
  only the em-dash/`--` rules can block) and **#169** (`DossierCache.get_or_build` caches a failed JD
  fetch exactly like a successful one, so triage re-judges page chrome nightly until the TTL expires).
  Tackled together on the user's explicit call.
- **Why one branch.** Both are the same defect wearing different clothes: a signal is computed and then
  discarded. #167 discards `_warns`; #169 discards the fact that nothing arrived. Neither shares code
  with the other, so this is a shared *shape*, not a shared abstraction.
- **Measurements** come from issue #169's own table; cited, never re-derived.

## Goal

- The CV linter's phrase matches stop being discarded: they reach the composer's one retry, so the
  model is actually asked to remove them.
- A JD fetch that produced nothing is no longer written to the dossier cache, no longer costs a judge
  call, and no longer hides inside `research` where a human is asked to action something no human can.

**The governing safety property**, which round 1 showed the first draft broke and round 2 showed was
worded too narrowly:

> No lead that renders a CV today may fail to render one after this change, **and no lead that becomes
> send-ready today may fail to become send-ready** — the withheld pointer, not the render, is what the
> STYLE tier actually costs, and a rendered CV with no `tailored_cv` is inert to `apply/select`.

That second clause is why the STYLE **hold** is opt-in (decision 8) while the STYLE **retry** is not.

## Decisions taken (and the options rejected)

1. **A surviving style finding never bins a lead.** Rejected: folding `warns` into `gate_msgs`, which
   is #167 §1's literal wording. The compose loop is `for _ in range(2)` (`cv/engine.py:224`) and a
   survivor returns `skipped-gate` with no CV. Promoting 40 fuzzy stems (`leverage`, `elevate`,
   `realm`, `not just`) to hard blocks with a one-retry budget creates a second irreversible
   bin-the-lead path — and #167 names source material as a vector, since the composer is told to reuse
   the bundle's wording, so a phrase in an Experience Library entry would re-fail forever. Also
   rejected: curating a blocking subset, a new hand-list that will drift.
2. **`unjudgeable` is a sixth `TRIAGE_OWNED` status.** Rejected: a frontmatter marker leaving
   `status: research`, which leaves these leads in the human's research queue. Rejected: reusing
   `needs_review`, which inverts the meaning.
3. **The near-empty floor ships OFF (`min_jd_chars: 0`), and `job-sluice init` asks.** *(Reversed in
   round 1.)* An empty JD always fails whatever the floor — a fact. A *character count* is a judgement,
   and `sluice.yaml.example:14-21` states the rule for exactly that case: `lead_ttl_days` ships
   commented because "an active value would hand every copier a judgement about what counts as stale
   that they never made". **Accepted cost:** at `0` the sub-200 entries in #169's table stay
   cached. The wizard question and the `doctor` distribution (decision 9) exist to close it.
4. **The per-source signal is derived from the vault, not stored — hosted in `health_report()`.**
   Rejected: extending `HealthStore` (whole-object rewrite, ingest-only writer today). Rejected: a new
   store (a relocatable path with #80/#81 obligations). Rejected: `Vault.preflight()`, which
   `core/protocols.py:277-287` forbids from walking the lead scan set.
5. **The cache owns the "did the JD arrive?" verdict and reports it; callers decide consequences.**
   Rejected: raising from `core/app.py`'s `fetch()` — that puts a content judgement in the
   transport/SSRF layer and binds only the Camofox path. Rejected: three callers each checking length,
   which leaves the cache still writing the failed entry.
6. **The LLM voice check ships OFF (`cv.voice_check: false`).** *(Reversed in round 1.)*
   `require_signoff` gates a *consequence* of a call made anyway; it is not precedent for a NEW call.
   `company_resolve_llm` (`triage/config.py:77`) is, and defaults `False`
   (`tests/test_sluice_neutral_defaults.py:525`). This does not make #167's fix inert: the
   deterministic half still reaches the retry.
7. **`cv.slop_allow` is a per-phrase escape hatch — and it is NOT abstain-shaped.** *(Round 2
   correction: the first revision called it abstain-shaped three times.)* It SUBTRACTS from a hardcoded
   list, so empty means full enforcement — the `dossier_allow_hosts` polarity `core/doctor.py:460-476`
   already names. What makes the default safe is decision 8, not this list's emptiness. Two rules
   follow: an entry absent from `_PHRASES` **raises at config load** listing the valid stems (rule 8,
   fail loudly) rather than being silently dropped, which closes the stem/inflection trap where
   `leveraged` would match nothing; and the compose prompt renders `_PHRASES - slop_allow`, so an
   allowed phrase is not still instructed against on every compose.
8. **The STYLE hold is opt-in (`cv.style_hold: bool = false`); the STYLE retry is not.** New in round 2,
   and the most consequential change of either round. `require_signoff` defaults `True`
   (`cv/config.py:47`), so riding it would mean that at shipped defaults a hard-clean CV containing any
   of ~40 case-insensitive stems in PROFILE prose or any WORK bullet has `tailored_cv` withheld and
   drops out of the apply path until a human runs `job-sluice cv signoff` — and via the source-material
   vector, one phrase in an Experience Library entry holds EVERY lead composed from it. Decision 1
   called the hold "mild" on the grounds that the CV still renders; a rendered CV with no pointer is
   inert to `apply/select`, so that was wrong. At shipped defaults the findings feed the retry and are
   reported on `CvResult`, which IS #167's complaint answered; withholding the pointer is a separate,
   opted-in step. `require_signoff` continues to gate the fabrication hold alone — its default was
   chosen for fabrication, and this borrows it for nothing.
9. **The `doctor` report is a DISTRIBUTION, not a threshold verdict.** *(Round 2: three reviewers found
   the first revision's NOTICE had no working producer, and that at `min_jd_chars: 0` its count was
   identically zero — inert exactly where decision 3 relies on it.)* It reports what is there
   ("N cached dossiers; N empty, N under 200 chars, N under 800"), which is descriptive rather than a
   gate: it changes nothing about which leads are judged, so it is not the shipped preference a
   threshold verdict would be. It is never inert, and it is precisely the evidence a user needs to
   answer the wizard's `min_jd_chars` question.
10. **The sign-off hold has ONE kind for refusal purposes.** *(Round 2: the kind-aware
    dismiss/expire refusal is DROPPED.)* `require_blank` (`core/vault.py:1159-1161`) is a frozenset of
    FIELD NAMES re-read CAS-fresh inside the write transform; it cannot correlate `pending_cv` with a
    kind stored in `needs_signoff`. Making it kind-aware needs either a third guard parameter or a
    hoist out of the CAS transform — the guard-read-before-the-write shape this repo has fixed three
    times (#9, #109, #131) — and every hold stamped before this change carries no kind, so six existing
    pins would go red with weakening the refusal as the only way to green them. What it bought was one
    command in a rare workflow (`cv signoff --discard` already exists). A STYLE hold therefore blocks
    `leads expire`/`leads dismiss` exactly as a fabrication hold does today, and no existing pin
    changes. If style holds prove annoying in practice, the distinction can be added later with
    evidence; removing a safety weakening after it ships is much harder.

## Part 1 — #169: the dossier and triage track

### The cache contract

`DossierCache.__init__` gains `min_jd_chars`, defaulting to `0`. One public predicate owns the
judgement:

```python
cache.jd_arrived(dossier) -> bool
```

- Empty after stripping is **always** a failure, whatever the floor.
- Non-empty but **stripped** length below a non-zero floor is a failure (stripped on both sides, so 300
  spaces cannot pass a floor of 200).
- `min_jd_chars == 0` disables the band; the empty case still holds.
- A missing/non-dict `jd`, or non-`str` `markdown`, is a failure — the degrade-to-failure posture
  `triage/resolve.py:_text` already takes on this field.

`get_or_build` calls the same predicate to decide whether to persist. **On the not-persisted path it
returns the freshly fetched dossier**, never the rejected cached one, so `jd_arrived` is answerable on
what the caller is holding.

A predicate on the cache rather than a marker key in the dict: a marker would flow through `slim()`
into the judge prompt. `DossierCache` is composition-root state, not a seam member (`core/app.py:468`),
so this creates no protocol obligation — but it widens the duck type every test double must satisfy.

### Pre-existing poisoned entries self-heal — to the extent the floor allows

`_fresh()` applies the predicate, so an entry whose JD is empty reads as not-fresh and is re-fetched.
**At the shipped `min_jd_chars: 0` this closes the EMPTY subset only** — #169's sub-200 entries stay
cached until a floor is configured, which is decision 3's accepted cost, surfaced by decision 9's
distribution. With a floor set, the same mechanism re-fetches the short entries too, and #169's manual
"delete the sub-200 entries" step goes away.

If the refetch also fails, nothing is written: the stale file lingers, inert, re-read and re-rejected
each run. No cleanup pass — deleting on a read makes a read a write, the shape that disarmed the #81
relocation notice.

**Cost, stated because it is not obvious:** an entry that is never persisted is fetched once per
`get_or_build` CALLER per run, not once per TTL. `triage/resolve.py:522` and `triage/engine.py:291`
deliberately share one cache entry via `cache_key`'s url hash — that is #109's double-fetch saving —
so a consent-walled lead now costs two live page loads per run on an install that has opted into
`company_resolve_fetch` (default `False`, `triage/config.py:68`).

### The three callers diverge

| Caller | On `not jd_arrived(d)` |
|---|---|
| `triage/engine.py:291` | Mark `unjudgeable`, count it, `continue` before `dossiers.append(d)` — no judge call. Under `dry_run` it counts and reports but writes nothing, matching the two existing write sites. `TriageReport.counts` gains an `unjudgeable` row, since counts rows are lead OUTCOMES that `cmd_triage_run` prints and `notify()` sends verbatim. |
| `cv/engine.py:198` | Set the `dossier_failed` flag it already carries (#18). Control flow unchanged. |
| `triage/resolve.py:522` | Code untouched; **behaviour is not** — see the double-fetch cost above. Tier-2 resolution still runs, because `page_title`/`structured_data` are often present when the JD body is a consent wall. |

### The write reuses the existing sink

`triage/apply.py`'s `_DECISION_STATUS` gains `"unjudgeable": "unjudgeable"`, and the engine calls the
existing `apply_classification` with a reason naming the fact and the floor it was measured against. No
new write function: CodeQL flags one as a new sink. `_guarded` and
`require_status=frozenset(TRIAGE_OWNED)` protect this write unchanged.

### Status vocabulary, and the selection default

`TRIAGE_OWNED` becomes `("new", "shortlist", "research", "needs_review", "dismiss", "unjudgeable")`.
`_ALIASES` gains `"unjudgable"` → `"unjudgeable"`.

**The selection default has FOUR homes; changing one leaves the feature inert.** Verified against the
tree: `cli.py:1554` (`--status` argparse default), `cli.py:652` (the same literal as fallback),
`core/app.py:1070`, `triage/engine.py:81` (dead on the production path), plus `docs/USAGE.md:84`.

**The new value is literally `("new", "research", "unjudgeable")`**, and the constant is **SHARED, not
derived** — it is one hand-picked retry subset with one home. It is *not* computable from
`TRIAGE_OWNED`, which also holds `shortlist`, `needs_review` and `dismiss`; an implementer taking
"derived" literally would widen the default selection to re-judge shortlisted and dismissed leads on
every run. It lives in `core/status.py` (which imports nothing, so no cycle, and `core/app.py:83`
already imports `_status` at module scope for `_EXPIRABLE`), with the CLI rendering it to a comma
string.

Three consequences follow automatically because those sets ARE derived:

- `_EXPIRABLE` (`core/app.py:83`) gains it — `leads expire` can age a permanently unfetchable lead to
  `dismiss`. Inert by default (`lead_ttl_days: 0`).
- `_DISMISSABLE_FROM` (`core/app.py:95`) gains it.
- `resolve_merge_status`'s `nonnew` branch gains conflict pairs: `research` + `unjudgeable` in one
  dedupe cluster now conflict. Correct, but a behaviour change in `leads dedupe`.

The implementation must ENUMERATE every `TRIAGE_OWNED`/`CANONICAL` consumer rather than trusting this
list; `tests/test_lead_layout_map.py:56` pins `len(CANONICAL) == 12`.

### One hardening comes with it

`apply_verdict` writes any model-returned verdict string as the status — `normalize` passes an
unrecognised value through untouched. That is a live hole: a model returning `verdict: "applied"`
writes an application-owned status today, because `require_status` checks only the CURRENT status.

The judge's own vocabulary is **three** verdicts — `shortlist | research | dismiss`
(`triage/prompt.py:60`, `triage/judge.py:44`) — and `apply_verdict` is clamped to it, falling back to
`needs_review`.

**The clamp needs a channel to the reporting, and the plan must name it.** `report.counts` keys on
`_status.normalize(verdict.get("verdict"))` (`triage/engine.py:386-387`) and the audit row writes
`verdict.get("verdict")` (`:393`) — both the RAW model string, computed in the engine, OUTSIDE
`apply_verdict`. So the clamp is exposed as a **pure helper in `triage/apply.py`** that both
`apply_verdict` and the engine's counts/audit call: one copy of the rule, no change to
`apply_verdict`'s return (which `outcome in ("skipped","unchanged")` depends on at both call sites).

`unjudgeable` stays **engine-determined only**. The judge prompt is not taught the word.

### Per-source visibility (#169 §2)

Hosted in `Sluice.health_report()` (`core/app.py:1049`), behind a parameter
(`health_report(*, include_leads=False)`) so the MCP `health` tool and `cli.py:386` keep today's cost by
default. That method does NO vault I/O today — it reads the source registry and `HealthStore` — so this
is a new cost, accepted because its own docstring already places it in the report idiom of
`dedupe_report`/`expire_report`/`reconcile_report`, all of which walk leads. `core/vault.py:1004`
returns `[]` on a missing `leads_dir`, so a fresh install does not break.

Both terms come from one `read_leads()` pass **at the same lifecycle stage**:

- **numerator** — leads with `status: unjudgeable` and `source == X`
- **denominator** — leads from source X in the SHARED selection set above (`new`/`research`/
  `unjudgeable`), derived from `core/status.py`, never hand-listed

The denominator is deliberately NOT `read_leads()` unfiltered: that is all-time
(`core/vault.py:984`, including `dismiss`, `applied` and terminals), so a source 100% broken today
would be diluted by its entire history and the classification could structurally never fire. One pass
guarantees one point in TIME, not one point in the LIFECYCLE — the round-1 wording conflated them,
which is the #156 mistake in a new costume.

## Part 2 — #167: the CV style track

### Two tiers

| Tier | Members | Scanned over | Consequence |
|---|---|---|---|
| HARD | citation violations, structural guards, renderer `precheck`, `slop.HARD` (em-dash, literal `--`) | whole document | Feeds the retry; a survivor returns `skipped-gate`. **Unchanged.** |
| STYLE | `slop._PHRASES` minus `cv.slop_allow`, plus LLM voice findings | PROFILE prose and WORK bullets | Feeds the retry always; withholds the send-ready pointer only under `cv.style_hold`. |

**STYLE is scoped; HARD is not.** `slop.check_text` scans every line, including employer, certificate
and header lines. A `SLOP leverage: <employer line>` arriving in the retry under "Fix these and re-emit
the FULL CV" (`cv/compose.py:80-83`) is answerable only by renaming the employer — a style rule turned
into fabrication pressure, the LOCATION-refusal shape CLAUDE.md records as the worst case this codebase
has shipped. Punctuation is different: an em dash anywhere is fixable without inventing anything.

**The section helper DOES NOT EXIST and must be extracted — as a step of its own.** Round 2 found the
first revision asserted a mechanism instead of checking for one. `cv/validate.py` defines exactly two
module-level functions, `_bundle_ids_and_nums` (`:52`) and `validate` (`:80`); the PROFILE/WORK split is
an inline `in_work`/`in_profile` state machine inside `validate`'s line loop (`:97-110`), entangled with
the WORK bullet-marker citation check (`:122`) and the PROFILE `_CITE_RE` strip (`:116`). `_SECTION_RE`
(`:22`) is a `===`-fence matcher and is not that loop.

So: extract `section_spans()` from that loop, preserving its exact terminators — `in_work` ends **only**
on `CERTIFICATES`/`EDUCATION`, which a naive generic splitter drops, silently stopping citation checks
on bullets under `PUBLICATIONS`/`PROJECTS` and weakening the fabrication gate while scoping a style
rule. Call it from `cv/engine.py`, where the tier policy and `slop_allow` already live — **not** by
importing `validate.py` into `slop.py`, which is currently pure and dependency-free. `cv/parse.py`'s
wider `_TRAILING_MARKERS`/`_BULLET_MARKERS` stays separate, as CLAUDE.md requires.

Because this refactors the pure gate, the Risk register no longer claims `cv/validate.py` is
"behaviourally byte-identical"; it claims **behaviourally equivalent, pinned by an equivalence test**
(named under Testing).

### The loop keeps the last hard-clean draft

Round 1's headline fix, and round 2 found it incomplete.

```python
retry_msgs = None                 # compose() takes prior_violations=None on attempt 1
best = None                       # (cv_text, style_msgs) of the last HARD-clean attempt
for _ in range(2):
    cv_text = compose(prior_violations=retry_msgs)
    ... compute hard_msgs, style_msgs ...
    if not hard_msgs:
        best = (cv_text, style_msgs)
        if not style_msgs:
            break
    retry_msgs = hard_msgs + style_msgs

if best is None:
    return skipped-gate           # no attempt was ever hard-clean — identical to today
cv_text, style_msgs = best        # REBIND before anything downstream reads cv_text
```

**The rebind is the round-2 fix and it is load-bearing.** The plan previously named one post-loop
consumer — render. `cv_text` is also read by `run_audit(...)`, whose flags drive `unsupported_claims` →
`hold_for_signoff` → the withheld `tailored_cv`. Without the rebind, with attempt 1 hard-clean/style-
dirty and attempt 2 hard-dirty, the engine renders attempt 1 while auditing attempt 2: a fabricated
claim in the *served* CV goes un-held, `set_tailored_cv` writes it send-ready, and the run reports
`rendered / audit flags: 0`. Every post-loop consumer — audit, renderer, served pointer, `CvResult` —
reads the rebound pair.

**The voice check runs only when `hard_msgs` is empty.** Cost with `voice_check` on: at most 2 composes
+ 2 voice checks + 1 audit, against today's 2 composes + 1 audit.

### `cv/voice.py`

Shaped like `cv/audit.py`: a pure `build_voice_prompt(cv_text)` and `run_voice(backend, cv_text) ->
(report, findings)`, through `core/backends`. Kept separate because the two have opposite blocking
semantics and separate config gates. It **fails open** exactly as the audit does — a backend error is
swallowed and logged, yielding no findings, because a gate must never be harder than the check that ran.

This answers #167's own objection that a phrase list cannot catch novel slop.

### Config

- `cv.voice_check: bool = False` — whether the LLM call happens (decision 6).
- `cv.style_hold: bool = False` — whether a surviving STYLE finding withholds the send-ready pointer
  (decision 8). The retry happens regardless.
- `cv.slop_allow: list[str] = []` — per-phrase escape hatch (decision 7). Entries not in `_PHRASES`
  raise at config load.
- `cv.require_signoff` is untouched and continues to gate the fabrication hold alone.

### Killing the prompt/list drift

`compose.py:14` bans `drove`, absent from `_PHRASES`: banned in prose, unchecked in code. The prompt's
banned-word sentence is rendered from **`_PHRASES - cv.slop_allow`**, and the test pins that equality.
With an empty `slop_allow` it reduces to `== _PHRASES`.

Equality, not subset: the prompt names *inflections* (`spearheaded`) while `_PHRASES` holds *stems*
(`spearhead`), so a subset test would fail on wording that is not in disagreement. Rendering stems makes
them identical by construction. `drove` joins `_PHRASES`.

### Recording the hold

`hold_for_signoff(ref, *, pending, claims)` keeps its Store-protocol signature
(`core/protocols.py:461`). `claims` is a JSON **array** and `core/app.py:1323` reads it as
`parsed if isinstance(parsed, list) else [str(parsed)]`, so a wrapped object would collapse into one
bogus claim. The payload stays a flat array of strings with a per-entry kind prefix, serving exactly one
consumer: `cli.py:761`, which prints `"{slug} has {len(claims)} unsupported claim(s)"` and becomes
kind-aware so a style hold is not announced as a fabrication risk. **An unprefixed entry — every hold
stamped before this change — keeps today's wording.** Per decision 10, no refusal reads the kind.

`CvResult` gains `voice_flags` (leaving `audit_flags` fabrication-only); `slop` carries the
deterministic findings and, round 1 found, **has no reader today**. Both must be surfaced by
`cmd_cv_run` AND by the MCP `cv_run` projection (`sluice/mcpserver.py:316-318`), which already returns
`audit_flags` and `violations` under `UNTRUSTED_DERIVED_CONTENT_WARNING` — `voice_flags` is
model-derived text and needs the same framing.

## Cross-cutting

### `min_jd_chars` is a ROOT key

`_dossier_dir()` is one root key by the #80 fix, but `ttl_days` is passed per sub-app
(`core/app.py:1125`, `:1210`). A per-sub-app floor would make the SHARED directory persist or refuse the
same entry depending on which sub-app touched it last — the "shared only by coincidence of a default"
hazard `_dossier_dir`'s docstring exists to kill.

So it resolves once on `Sluice` and reaches both `dossier_cache(...)` calls, with a test asserting it
reaches **both**. The `ttl_days` asymmetry stays: two sub-apps wanting different cache LIFETIMES over
one directory is coherent; two wanting different VALIDITY rules is not, because only one writes.

Obligations: `load_config` must NAME the root field (the four sub-app loaders are `hasattr`-filtered
`setattr` loops and must not be "fixed"), and it needs `lead_ttl_days`' **bool-before-int** validator,
since PyYAML resolves `min_jd_chars: yes` to `True` and `bool` subclasses `int`.

### Named guards for the new defaults

`min_jd_chars: 0`, `cv.voice_check: False` and `cv.style_hold: False` are abstain-shaped and agree with
the neutral-defaults posture. `cv.slop_allow: []` is NOT (decision 7) — what makes it safe by default is
`style_hold` being off, and its guard should pin that relationship, not just `slop_allow == []`. All
need named guards, because the sweep is keyed on **list** fields and sees neither an `int` nor a `bool`.
The sweep is not widened to scalars: `0 == abstain` is not universal (`ttl_days: int = 7`).

`job-sluice init` gains a `min_jd_chars` question, rendered commented when unanswered.

### The `doctor` distribution

Gathered in `Sluice.doctor`'s IMPURE half — which already collects facts this way for the backends,
renderer and Camofox, and where `self._dossier_dir()` is reachable — and classified purely in
`core/doctor.py`. **Not** `Vault.preflight()`: the dossier dir is composition-root state invisible to
`Vault`, and counting entries means parsing every cached JSON, the walk `preflight`'s contract forbids.

It reports counts, not a verdict (decision 9). If the scan cost proves material, BOUND it and report the
bound — never truncate silently, since a capped count reads as a complete one.

### Existing tests this changes

Each is a legitimate pin update with its reason recorded, so an implementer does not read red as "the
new check is too aggressive":

- `tests/test_lead_layout_map.py:56` — `len(CANONICAL) == 12` becomes 13. Update the number, never the
  assertion; it is the scope guard.
- `tests/test_dossier.py:85-105` — the legacy-schema fixture seeds `jd: {markdown: ""}` (`:93`) and is
  fresh by time, so `_fresh()`'s content check re-fetches it and both assertions redden. Give the
  fixture a non-empty JD so it keeps testing a pre-#109 entry missing `page_title`/`structured_data`.
  **Deleting the content check to green this removes #169's self-healing half.**
- `tests/test_config_paths.py:331-345` — `_dossier_dirs_used`'s `_capture(dossier_dir, ttl_days)` takes
  exactly two positional parameters and both production sites pass positionally, so a third argument
  TypeErrors inside `app.triage(no_llm=True)`, reddening all four #80 one-root-key guards (`:352`,
  `:361`, `:369`, `:376`). Widen `_capture` to record BOTH values, so the new "reaches both
  constructions" assertion lands in the fixture that already pins the directory half. **Do not "fix" it
  to `lambda *a, **k:`** — that greens the four directory pins while making the floor assertion
  unwritable.
- Every test double standing in for `DossierCache` must grow `jd_arrived`.

**UNCHANGED by design, named so they are not disturbed:** the six sign-off refusal pins that seed
`pending_cv` with no `needs_signoff` payload — `tests/test_leads_expire.py:238,249`,
`tests/test_leads_dismiss.py:76,180`, `tests/test_leads_dismiss_cli.py:40`,
`tests/test_leads_expire_cli.py:143,167`, `tests/test_mcpserver.py:478` — plus
`test_needs_signoff_WITHOUT_pending_cv_is_NOT_refused` (`tests/test_leads_expire.py:254`). Decision 10
exists so that all seven stay green untouched.

### Documentation

- `docs/ARCHITECTURE.md` — the cache contract, the two CV tiers, the sixth triage state,
  `health_report`'s optional lead walk.
- `docs/CONFIGURATION.md` and `sluice.yaml.example` — all four new keys, commented.
- `docs/USAGE.md:84` — the `--status` default.
- `core/app.py`'s `health_report` docstring (implies no vault I/O), `sluice/mcpserver.py:143`'s
  "Per-source scrape baseline + retire state" docstring, `SourceHealth`'s shape, and `cmd_health`'s
  fixed print format — all four change.
- The MCP `cv_run` projection.
- `.rulesync/rules/CLAUDE.md` — the invariant text, never the generated `CLAUDE.md`; then regenerate.

### Testing

- `jd_arrived`: empty always fails; below a non-zero floor fails; `0` disables the band but not the
  empty case; non-dict `jd` and non-`str` `markdown` fail; the not-persisted path returns the FRESH
  dossier.
- A poisoned pre-existing entry is re-fetched on the HIT path.
- An unjudgeable lead reaches **no** judge call, asserted on the backend spies' prompt CONTENT
  (`tests/harness/backend.py:70`, `tests/test_cv_engine.py:214`) in a MIXED batch — `prompts == []` is
  vacuous, since a run that judged nothing satisfies it.
- **The safety property as ONE structural assertion**: `skipped-gate` iff no attempt was hard-clean.
  Round 2 corrected the round-1 plan here — the loop is two attempts, so the space is *sequences*, not
  per-attempt outcomes, and a four-combination table only samples what one assertion states. The
  sequence that matters most: hard-clean attempt 1, style-dirty, hard-dirty attempt 2 → renders attempt
  1.
- **The audit runs over the RENDERED draft**, not the last attempt — the inv-r2-001 regression.
- `test_the_section_span_helper_reproduces_validates_own_profile_and_work_line_sets`, parametrized over
  `tests/test_cv_validate.py`'s existing fixtures, asserting the extracted helper's spans equal the
  lines `validate` actually applies each check to — **including the `CERTIFICATES`/`EDUCATION` reset
  arm**, the one a naive extraction drops.
- A STYLE finding on an employer or certificate line does not reach the retry.
- With `style_hold: False` (shipped) a style-dirty CV still becomes send-ready; with it `True` the
  pointer is withheld. Also `require_signoff: False` × style-dirty.
- `slop_allow` with an entry absent from `_PHRASES` raises at load, naming the valid stems.
- A voice-check backend error degrades to no findings.
- The root `min_jd_chars` reaches both `dossier_cache(...)` constructions.
- The `--status` default equals the shared constant, asserted by walking the real parser — modelled on
  `tests/test_docs_claims.py:64-69`, and **copying its scope guard** (`:92`
  `test_the_command_tree_walk_is_not_vacuous`), because a walk that finds nothing satisfies every
  assertion over it.
- A garbage model verdict yields `counts["needs_review"]` and an audit row reading `needs_review`, never
  the raw string.
- The rendered compose prompt's word list equals `_PHRASES - slop_allow`.
- `voice_flags`/`slop` surfaced by `cmd_cv_run` and by the MCP projection under the existing untrusted-
  content warning, with `tests/functional/test_mcp_contract.py` updated.
- Forward-looking hardening (NOT a pin this change breaks): `tests/e2e/test_an_empty_config_bins_nothing.py`
  arm 1 asserts only `status != "dismiss"`, which a future non-zero floor's `unjudgeable` would satisfy
  vacuously. Assert a judged status. At the shipped `0` nothing here reddens.

Round 1's `_EXPIRABLE` bullet is **dropped**: `tests/test_leads_expire.py:104` asserts
`_EXPIRABLE == frozenset(TRIAGE_OWNED) - {"dismiss"}`, derived, so it already kills that mutant.

Load-bearing tests get **mutation witnesses** — mutate by moving or deleting, run
`compileall --invalidation-mode checked-hash` first, run the named test by node id, and confirm no
sibling already catches it. Verified for the headline one: deleting the `best = (cv_text, style_msgs)`
assignment is a genuine witness, because today's loop breaks on the first hard-clean attempt and no
existing fixture produces a hard-clean-then-hard-dirty sequence.

### Risk register

- **Never-regress.** A sixth `TRIAGE_OWNED` member is legal — triage owns and may rewrite its own states
  — but it widens `_EXPIRABLE` and `_DISMISSABLE_FROM` and adds `resolve_merge_status` conflict pairs.
  The application lifecycle is untouched: no change to `_LADDER`, `_TERMINAL`, `can_apply`,
  `can_advance`, `can_transition`. The `apply_verdict` clamp NARROWS what a model can write.
- **Never-clobber.** Reusing `apply_classification` keeps the CAS path and `require_status` intact. No
  new write function, no new sink. Decision 10 keeps `require_blank` CAS-fresh and unextended.
- **The CV fabrication gate.** `cv/validate.py` is refactored (the `section_spans()` extraction) and so
  is **behaviourally equivalent, pinned by an equivalence test** — not "byte-identical", which the round-1
  revision claimed while also requiring the extraction. The STYLE tier is additive and, with the
  retained-draft rule, cannot cause a `skipped-gate` that would not have happened anyway. It lives in
  the engine, not a renderer `precheck`, so it binds every renderer alike.

### Out of scope

- Widening the neutral-defaults sweep to scalar fields.
- Any cleanup pass over already-cached poisoned dossiers beyond `_fresh`'s check.
- Teaching the judge prompt the word `unjudgeable`.
- Moving `ttl_days` to a root key.
- A kind-aware dismiss/expire refusal (decision 10) — revisitable later, with evidence.

## Revision history

**Round 1 (2026-08-21)** — 45 findings: 0 Critical, 22 High. Three were corroborated by three reviewers
each: the retry shipped inert (four homes for the selection default); the loop split created the
bin-the-lead path decision 1 exists to prevent; `preflight()` was the wrong host for the per-source
tally. Two decisions were reversed on verified corrections to the draft's own reasoning
(`min_jd_chars` → `0`, `voice_check` → `False`). Two reviewer recommendations were declined, with
reasons recorded, and both were subsequently accepted by the reviewer in round 2.

**Round 2 (2026-08-21)** — 23 findings: 0 Critical, 13 High, **every High a defect in a round-1 fix.**
Three more triple-corroborated failures, all in machinery the branch grew DURING review: the `doctor`
NOTICE had no working producer; the "shared section helper" did not exist; the kind-aware refusal could
not be built on `require_blank` and would have reddened six pins. The single most serious finding of
either round was inv-r2-001 — the post-loop audit reading the discarded draft, so a fabricated claim in
the SERVED CV would go un-held and be written send-ready. Round 2 also produced decision 8 (the STYLE
hold becomes opt-in), after establishing that the round-1 safety property was worded on render alone
while the tier's real cost is the withheld send-ready pointer.

Round 2's citation sweep verified every one of the ~25 code references round 1 added.

The lesson recorded for the implementation plan: **the added machinery, not the original fix, is where
both rounds found their worst defects.** Decision 10 drops one such addition outright.
