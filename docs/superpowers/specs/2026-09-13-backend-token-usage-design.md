# Backend token usage — design (#308)

Status: approved in chat, not yet reviewed by `/review-plan`.

Issue: **#308** (`complete()` returns a bare string, so every provider's `usage` block is parsed
past and discarded; a run's LLM spend is unobservable).

## A note on method

Every per-provider field name in §2 was read out of that provider's own documentation during
design, not recalled. That matters more here than usual: the three shapes disagree about what
their input counter *means*, and the disagreement is invisible in the field names. Copying each
provider's own "input tokens" field into one normalised field would compute a cache hit rate
above 100% on Anthropic — see §2.1. Where a provider's documentation does **not** settle a
relation, this design routes around needing it rather than assuming it (§2.3).

No count of call sites, stages or test fakes appears in this document. `tests/test_usage_wiring.py`
(§6) derives the stage roster and fails in both directions, which is the only form of that claim
that can go stale loudly.

---

## 1. The problem

`core/backends.py::OpenAiCompatibleBackend.complete` reads `data["choices"][0]` for
`finish_reason` and `message.content` and drops the rest of the response body, `usage` included.
`AnthropicBackend.complete` does the same with `data["content"]`. Nothing anywhere in `sluice/`
records a token count: `triage/config.py::TriageConfig.audit_jsonl` records a verdict, a reason
and a score per lead, and no tokens.

So four questions have no answer today, and the fourth is the sharpest:

- What did last night's triage run cost?
- Which stage is expensive — judge, compose, audit, voice, classify?
- Is a prompt change making things better or worse?
- Are we getting prefix cache hits?

DeepSeek prices a cached input token and an uncached one two orders of magnitude apart, and the
hit/miss split is already sitting in responses this code discards. Cache behaviour is the largest
single lever on what sluice spends, and sluice cannot see it.

Token counts are also the early-warning signal for a prompt that has quietly grown, and for
context creeping toward a model's limit before it begins truncating.

## 2. The seam

`core/backends.py` gains two frozen dataclasses, beside `BackendError`, which every provider
module already imports from:

```python
@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage | None = None
    unserved_usage: tuple[Usage, ...] = ()
```

`complete(prompt) -> Completion`. Every field of `Usage` past the two identifying strings defaults
to `None`, **never `0`**: "the provider did not report this" and "this genuinely cost nothing" are
different facts, and a zero conflates them. That is why `ClaudeMaxBackend` is not special-cased
out of the seam — it answers `usage=None`, which is a true statement about a flat-rate CLI run in
text mode, and the summariser reports how many calls answered that way.

`provider` and `model` are stamped by the **leaf** that served, which is what makes attribution
through `FallbackBackend` structural rather than reconstructed (§3).

### 2.1 `input_tokens` is defined, not copied

**`Usage.input_tokens` is the total input for the call, INCLUDING any tokens served from cache.**
Each provider's parse normalises into that definition. The definition is load-bearing because the
providers disagree:

| provider | total input incl. cached | cache read | cache write |
| --- | --- | --- | --- |
| Anthropic Messages | `usage.input_tokens` **excludes** cache → sum of all three | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` |
| OpenAI chat/completions | `usage.prompt_tokens` **includes** cached | `usage.prompt_tokens_details.cached_tokens` | `usage.prompt_tokens_details.cache_write_tokens` |
| DeepSeek chat/completions | `prompt_cache_hit_tokens + prompt_cache_miss_tokens` | `usage.prompt_cache_hit_tokens` | not reported |
| claude-max (`claude --print`) | not reported | not reported | not reported |

Anthropic's `input_tokens` counts **uncached tokens only** — the cache counters sit beside it, not
inside it. A direct copy would therefore report a denominator with the cached tokens missing, and
a hit rate of `cache_read / (input − cache_read)`, which exceeds 1.0 on exactly the well-cached
call the number exists to celebrate. `tests/test_backends_usage.py` carries a row that fails if
the normalisation is ever "simplified" back to a copy.

There is no `total_tokens` field. It is `input + output`, and a stored total is a second value
free to disagree with its own parts; `core/usage.py::summarize` derives it.

`completion_tokens_details.reasoning_tokens` is deliberately not captured: reasoning tokens are
already inside `completion_tokens`, so recording both invites double counting for no new fact.

### 2.2 `BackendError.usage`

`BackendError` gains an optional `usage` keyword. A provider that parsed a usage block and *then*
judged the response unusable — `finish_reason=length`, `stop_reason=max_tokens` — attaches it.
This is the only route by which #308's third requirement can be met at all: a return value cannot
carry the spend of a call that raised, because on that path there is no return value. A transport
failure (timeout, HTTP error, missing binary) has no body to parse and attaches nothing.

### 2.3 The DeepSeek relation this design does not need

DeepSeek documents `prompt_cache_hit_tokens` as "tokens in the input of this request that resulted
in a cache hit" and `prompt_cache_miss_tokens` as those that did not, but states nothing about how
either relates to `prompt_tokens`. Rather than assume `prompt_tokens == hit + miss`, the
OpenAI-compatible parse prefers `hit + miss` as `input_tokens` **when both keys are present** —
self-consistent by construction, no relation assumed — and falls back to `prompt_tokens` with
`prompt_tokens_details.cached_tokens` otherwise, which OpenAI's own documentation does settle.

That also keeps one parser correct for the whole family `OpenAiCompatibleBackend` serves, which is
not two providers but "any OpenAI-compatible endpoint" including a local server that reports
neither.

## 3. `FallbackBackend`

The leaf stamps its own `provider`/`model`, so the fallback has nothing to attribute by hand: it
returns the serving leg's `Completion` unchanged. `last_backend` is untouched and stays exactly as
it is — see §7.

The one thing it must do is not swallow spend. Today a primary that raises is caught, logged at
WARNING, and the fallback's answer returned; a primary that burned tokens before raising would
disappear inside that `except`. So when the caught `BackendError` carries a `usage`, the fallback
threads it onto the returned completion's `unserved_usage`, and `MeteredBackend` writes it as its
own record with `served: false`. A leg that bills on every call while never serving one is then
visible rather than free.

## 4. Recording — `core/usage.py`

A new module, separate from `core/backends.py`: the clients and the telemetry sink are different
concerns, and that file is already long.

```python
def meter(log, backend, stage, *, lead=None):
    """Wrap `backend` so each completion's usage is recorded under `stage`.

    Returns `backend` UNCHANGED when `log` is None, so an install with no usage log
    constructs no wrapper and every call site's off path is one comparison."""
    return backend if log is None else MeteredBackend(backend, log, stage, lead)
```

`MeteredBackend.complete` delegates, records `c.usage` and each `c.unserved_usage`, and re-raises
a `BackendError` after recording its `usage`. It exposes `last_backend` as a property delegating
to the wrapped backend: three lines that close a silent trap, since `getattr(wrapper,
"last_backend", None)` would otherwise read `None` for whoever wires the next stage and report a
healthy run as an outage (`cli.py::_format_triage_digest` documents `report.backend`'s three
legitimate nulls, and a fourth spurious one would be indistinguishable from them).

**Every wrap carries an explicit stage.** There is deliberately no default and no
wrap-with-stage-later: a wrapper holding `stage=None` would record rows a reader cannot group, and
nothing static could catch a call site that forgot to re-label.

Wrap sites, almost all at the application boundary, which is where this repo already puts the
decision of what a process may spend:

- `core/app.py` — `triage-judge`, `triage-resolve`, `track-classify`, `doctor-probe`. Each of
  those backends serves exactly one stage, so the stage is known where it is constructed.
- `cv/engine.py` — `cv-compose`, `cv-audit`, `cv-voice`, each with `lead=note.ref`. cv is the one
  place a single backend serves three stages, and the only place the lead is in scope, so it
  receives the log as a keyword alongside the collaborators `run_batch` already takes.

`doctor-probe` is included because `job-sluice doctor` round-trips every configured backend
unless `--offline`; omitting it would make "what did I spend" wrong by however many probes were
run, in the direction of under-reporting.

`triage-resolve` records no lead. Tier-3 company resolution is a bulk pass and threading a lead
id through `triage/resolve.py` buys little against the wiring it costs. Stated, so it is a known
gap rather than a silent one.

### 4.1 A write failure must not fail the run

`UsageLog.append` catches `OSError` and warns. This differs from `triage/audit.py::AuditLog`
deliberately: by the time a usage record is written the money is already spent and the verdict
already earned, so failing a triage run because a telemetry append hit a full disk destroys work
to protect a measurement of it.

## 5. Config and the file

Root `Config.usage_jsonl: str = ""`, resolved inside `load_config` as

    env SLUICE_USAGE  ->  config key  ->  XDG state / sluice_usage.jsonl

which is the shape `seen_db` and `triage.audit_jsonl` already use. The `""` default is required
rather than stylistic: a non-empty default is always truthy and would short-circuit the chain so
the XDG location is never reached, leaving the feature inert with nothing red.

**On by default**, like `audit_jsonl` and `sluice_health.json`, rather than opt-in. The first
question anyone asks is about a run that has already happened, and an opt-in log answers it with
"no data". No rotation, matching audit.

One line per call:

```json
{"ts": "2026-09-13T21:04:11", "stage": "cv-compose", "lead": "Example Co - Example Role",
 "provider": "deepseek", "model": "deepseek-v4-flash", "served": true,
 "input_tokens": 18442, "output_tokens": 1290,
 "cache_read_tokens": 17920, "cache_write_tokens": null}
```

`lead` is absent rather than null where the stage has none. The file records counts and a lead
name; no prompt text and no completion text ever reach it.

## 6. Tests

- **`tests/conformance/test_backend_contract.py`** — every registered provider returns a
  `Completion` whose `text` is non-empty and whose `usage` is `Usage | None`. Added to the existing
  parametrized suite and its per-provider payload tables, so the next provider inherits it.
- **`tests/test_backends_usage.py`** — one case per shape in §2.1 plus both-absent. The Anthropic
  case is the mutation-load-bearing one (§2.1).
- **`meter(None, b, "x") is b`** — an identity assertion, so the off path cannot quietly grow a
  wrapper.
- **Fallback attribution**, measured on both legs: the record names the leg that served, and a
  primary that burned tokens before raising leaves its own `served: false` record.
- **`tests/test_usage_wiring.py`** — the both-ends guard. AST-collect every `.complete(` call site
  in `sluice/` and every `meter(...)` stage literal, and assert each against a hand-written roster:
  `_CALL_SITES` maps (module, innermost function) to the stage that meters it, or to None with a
  stated reason for the metering plumbing itself (`FallbackBackend` delegating to its own legs,
  `MeteredBackend` delegating inward); `_STAGES` names every stage and the runtime test that
  witnesses it recording. Plus the join in both directions. Both derived sets are asserted
  non-empty: for a guard whose success case is finding no violation, a sweep that discovers nothing
  is indistinguishable from a sweep that is broken. The rosters are hand-written and the probes
  derived, never the reverse — a roster derived from the thing it checks compares the code against
  itself, sweeps fewer after a deletion, and stays green.

  **Corrected after implementation.** This section first specified "assert each module holding a
  call site also meters", with two allow-listed exemptions. That assertion is FALSE BY DESIGN:
  `meter` wraps where a backend is HANDED to a stage, which is a different module from where the
  call happens and often a different sub-app — the metering for `cv/compose.py::compose` lives in
  `cv/engine.py`, and for `triage/judge.py::judge` in `core/app.py`. Written as specified it would
  have had to be narrowed until it checked nothing, which is this repo's documented way of turning
  a guard into decoration. The roster shape above is what replaced it, and unlike the original it
  states plainly what it cannot check: whether the backend reaching a given call site was
  ACTUALLY metered is a dataflow question, which is why each stage names a runtime witness.
- **`summarize`** is pure, so totals, the hit rate, the no-usage footer and a malformed line are
  tested without touching a file.

## 7. Non-goals, stated so they are not read as oversights

- **`last_backend` survives.** #308 names it as the strain that motivates the widening, and for
  *usage* attribution it is answered — usage now rides on the completion that produced it.
  `report.backend` is a separate consumer: `triage/engine.py` sets it after `judge()` returns, and
  `judge()`'s contract is deliberately verdicts-in-verdicts-out with the reconciliation done
  outside it. Retiring `last_backend` means widening that return, which is its own change.
- **No price table, no money column.** Acceptance asks to say what a run cost without inference;
  this ships tokens and a cache hit rate, which are facts sluice measured. A shipped price map is
  a claim about the world that rots silently and then reports wrong money — the failure shape a
  disabled source's `reprobed` date exists to prevent, with the wrong answer denominated in
  currency. A user-supplied price map remains available later as an opt-in root key, empty means
  abstain, and nothing here forecloses it.
- **No `Backend` Protocol in `core/protocols.py`.** The backend seam's contract lives in
  `tests/conformance/test_backend_contract.py` and is unchanged in kind by this work. Adding the
  Protocol is a worthwhile tidy and is not this issue.

## 8. Change classification

Not breaking. `CHANGELOG.md`'s own rule turns on whether a user's install can see it: nothing
imports `sluice` as a library, no config key is removed or re-meaningfully-defined, and no CLI
behaviour changes. `feat(backends):` for the seam, `feat(cli):` for the command.
