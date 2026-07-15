# `sluice doctor`: prove the configured backends actually work

**Date:** 2026-07-16
**Status:** accepted
**Issue:** #4 — `feat(cli): sluice doctor — prove the configured backends actually work`

## The problem

Nothing ever checks that a configured backend is *usable* until the moment it is needed — and for
the fallback, that moment is by definition when the primary has already failed.

This is not hypothetical. A per-token fallback can be configured, look correct in every inspection,
and still be dead: the API key can be present in the container's environment yet never reach the
subprocess that actually runs sluice. The result is a backend that is silently non-functional for
months, discovered only when the primary goes down and the run fails instead of degrading.

`sluice health` exists but covers **source** health (scraper baselines, auto-retire). There is no
equivalent for backends. A fallback that cannot run is worse than no fallback, because it is
*believed in*. `doctor` turns "we think we have a safety net" into a testable claim, cheap enough to
run from cron so the answer stays current.

## What the code already gives us

The pluggable-core seam work (#15/#17/#19) landed exactly the machinery this needs:

- **`Sluice.available("backend")`** → `[anthropic, claude-max, deepseek, openai]`, the registry of
  provider factories (`sluice/backends/`).
- **`make_backend(name, model, *, api_key, base_url, effort, claude_host, claude_path, ...)`** builds
  one provider by name. A per-token provider with **no key raises `BackendError` at construction**
  (`sluice/backends/anthropic.py`, `deepseek.py`, `openai.py`); `claude-max` needs no key. An
  unknown name raises `BackendError` listing the valid names. This is the fail-loud-at-construction
  contract `doctor` reads its verdicts from.
- **`_provider_creds(name)`** (`core/app.py`) resolves `(api_key, base_url)` from the environment for
  a provider — the exact "are the credentials present *in this process*" check the issue asks for.
- **The three sub-app configs** (`triage`, `cv`, `track`) each carry
  `primary_backend`/`fallback_backend` plus the per-role model/effort/host fields. `apply` has no
  backend (offline by contract). The default is `primary_backend: claude-max` (no key needed) +
  `fallback_backend: deepseek` (needs `DEEPSEEK_API_KEY`), and `_make_fallback` already treats a
  keyless fallback as a **sanctioned degrade to primary-only** (it warns and returns `None`), *not*
  an error.

So `doctor` is wiring over existing primitives, not new backend logic — the same shape the façade PRs
established: a pure module for the rules, a thin `Sluice` method for the impure build+probe, a thin
`cmd_*` for formatting and the exit code.

## Scope

**Backends only.** The issue is "prove the configured *backends* actually work." Store/fetcher/
renderer health is a real but separate concern (a `vault-writable` / `camofox-reachable` check) and is
explicitly **out of scope** for this PR — YAGNI until asked. `doctor` reads existing config; it adds
**no new config knob** and **no new runtime dependency** (`sluice/` stays stdlib-only).

## Surface

```
sluice doctor [--offline] [--strict]
```

- **Default is live.** A minimal round-trip per *distinct* configured backend. The issue is explicit:
  make the live call **opt-out, not opt-in** — "the check nobody runs is worthless." A round-trip
  costs a token or two on a per-token backend; `claude-max` is flat-rate.
- **`--offline`** — config-only preflight: is the provider name known, is a model resolved, are the
  creds present in this process, does the `claude` CLI exist on PATH? No network, no subprocess
  round-trip. A weaker check by nature (it cannot detect a key that is present-but-wrong, or a
  retired model id) — hence not the default.
- **`--strict`** — escalate any **degraded** result to a non-zero exit. This is the cron mode that
  *enforces* a believed-in fallback: "tell me the moment my safety net stops working."

Output goes to **stdout** (matching `health` and `list-sources`), one line per distinct backend,
annotated with the sub-app roles it serves:

```
sluice doctor  (live round-trip)

claude-max  claude-sonnet-4-5   ok        primary · triage, cv, track   (0.4s)
deepseek    deepseek-v4-flash   degraded  fallback · triage, cv, track  DEEPSEEK_API_KEY unset — primary-only

1 ok, 1 degraded, 0 dead
```

The exit code is `report.exit_code(strict)`; the summary line is a flat count
(`N ok, N degraded, N dead`).

## Three states, role-aware (the crux)

The design decision that shapes everything: how a broken or absent backend maps to a state and an exit
code. A naive "any configured backend that isn't ok → non-zero" would red-flag almost every install,
because the **default** ships a keyless `deepseek` fallback that `_make_fallback` already sanctions as
primary-only. So the classification is **role-aware**, mirroring the existing auto-vs-fallback role
logic exactly:

| Situation | State | Rationale |
|---|---|---|
| Provider name not in the registry (config typo) | **dead** | Caught even `--offline`; a run would raise `BackendError`. |
| Per-token backend, **no key**, as **primary** | **dead** | A run cannot happen — `make_backend` raises at construction. |
| Per-token backend, **no key**, as **fallback** | **degraded** | The sanctioned primary-only path (`_make_fallback` warns + returns `None`). |
| `claude-max`, local (no host), CLI not on PATH | **dead** | The subprocess would fail "command not found". |
| Live round-trip raises `BackendError` (401 / DNS / timeout / retired model id / CLI nonzero-exit) | **dead** | You configured it to work; it does not. **This is the silent-death bug the tool exists to catch.** |
| Round-trip succeeds (or `--offline` and every static check passes) | **ok** | |

**Exit code:** any **dead** → non-zero (`1`); with `--strict`, any **degraded** also → non-zero; else
`0`.

The key consequence: a **keyless** fallback is *degraded* (exit 0 by default — a legitimate
primary-only install stays green), but a **keyed-but-broken** fallback is *dead* (exit non-zero — the
exact "the key never reached the subprocess" scenario the issue describes). `--strict` is for the
operator who wants even the keyless-primary-only case to fail.

## Where it lives

Following the pure-module + thin-`Sluice`-method split the codebase already uses (`health.py` and
`dossier.py` are pure; the `Sluice.*` methods are wiring; `cmd_*` formats):

### `sluice/core/doctor.py` — pure, zero I/O

- Dataclasses: `BackendTarget` (provider, model, host, claude_path, roles-and-sub-apps it serves,
  whether it needs a key + which env var), `BackendCheck` (the target + its `state` + a `detail`
  string + optional elapsed seconds), `DoctorReport` (the list of checks + `exit_code(strict: bool)`).
- `enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list[BackendTarget]` — derives every
  sub-app × role target from the three config objects, then **dedupes** identical
  `(provider, model, host, claude_path)` targets into one, recording which sub-app roles each covers. This keeps
  the *live round-trip* to one call per distinct backend (cost discipline) while still enumerating
  per sub-app, so a per-sub-app model override (e.g. `cv.compose_model` retired while triage's is
  live) is caught — exactly the issue's "live model id, per sub-app". **Effort is excluded from the
  dedup key**: it changes cost/quality, not whether the backend works, so folding
  triage(medium)+cv(max) into one `claude-max` probe is correct and cheaper.
- `classify(target, *, key_present, cli_present, offline, probe_error) -> BackendCheck` — the pure
  rules table above, given the resolved facts. `probe_error` is `None` when the round-trip was not run
  (offline, or short-circuited on a missing key) or succeeded, else the `BackendError` message.

### `Sluice.doctor(*, offline=False, probe=None) -> DoctorReport` — impure wiring

Loads the three sub-app configs, calls `enumerate_targets`, and for each target resolves creds via the
existing `_provider_creds`, checks `shutil.which(claude_path)` for a local `claude-max`, and — in live
mode, when there is something to probe — **builds the single provider directly** via
`make_backend(provider, ...)` (not the role composite, so there is no `FallbackBackend` to
disentangle) and runs `probe(backend)`. Hands the resolved facts to `classify` and assembles the
`DoctorReport`.

- `probe` is the **test seam**: it defaults to `lambda b: b.complete(PROBE_PROMPT)`; a test injects a
  fake that records the call and optionally raises `BackendError`, so the whole method runs offline.
- `PROBE_PROMPT` is tiny (e.g. `"Reply with the single word: ok"`); the response is discarded — only
  "did it raise" matters. `max_tokens` is **not** tightly capped: the OpenAI-compatible backend treats
  `finish_reason=length` as a hard error, so an over-tight cap would manufacture a false *dead*.
- A missing-key per-token target is classified **without building or probing** (there is nothing to
  test, and `make_backend` would raise) — so the round-trip is only ever attempted for a target that
  has what it needs to succeed.

### `cmd_doctor(args, config) -> int` — thin CLI

Builds `Sluice(config)`, calls `.doctor(offline=args.offline)`, prints the grouped report, and returns
`report.exit_code(strict=args.strict)`. Registered as a top-level `sluice doctor` subcommand alongside
`sluice health`.

## Testing

Hermetic and offline, per the suite's contract (no Camofox, no network):

- **`tests/test_doctor.py`** — the pure `core/doctor.py`: `enumerate_targets` dedup + per-sub-app model
  split; `classify` for every row of the rules table; `DoctorReport.exit_code` with and without
  `--strict`. No I/O.
- **`Sluice.doctor` with an injected `probe`** (in `tests/test_app_operations.py` or a sibling) +
  monkeypatched env for creds + a `claude_path` pointed at a tmp file for the `which` check. Cases:
  live-ok; live-**dead** (keyed provider whose probe raises — the target bug); keyless-primary → dead;
  keyless-fallback → degraded; unknown-provider → dead; `--offline` skips the probe entirely.
- **CLI**: `cmd_doctor` maps states to the right exit code (dead → non-zero; degraded → 0 without
  `--strict`, non-zero with it; all-ok → 0) and never constructs a browser or a store (the offline
  guarantee — `doctor` touches only the backend seam).

## Non-goals

- Store/fetcher/renderer/vault/Camofox health (separate concern; separate issue if wanted).
- A model *registry* / retired-alias list. Retired model ids are detected the honest way: the live
  round-trip fails. `--offline` cannot detect them, and says so by being the weaker mode.
- Any change to `make_backend`, `Sluice.backend`, the role/alias tables, or any `*Config` default.
  `doctor` is a **reader**; it changes no run-path behaviour and touches no invariant.
