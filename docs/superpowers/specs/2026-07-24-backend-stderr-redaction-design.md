# Backend stderr redaction — design

- **Date**: 2026-07-24
- **Status**: reviewed (5 specialists, 0 Critical / 2 High / 2 Medium / 1 Low; findings folded in);
  ready for implementation. Placeholder style: labeled `<host>` / `<path>` (review consensus, no
  objection).
- **Origin**: issue #41 (two findings from PR #37 review: `tst-004` empty-response error drops its
  only diagnostic; the neutrality reviewer's out-of-scope note that the sibling error leaks a
  hostname). User decision 2026-07-24: fix approach = **redact at the boundary**; scrub scope =
  **host + configured `claude_path`**; placeholder style **deferred to the review agents**.

## Goal

`ClaudeMaxBackend.complete` builds `BackendError` messages from a subprocess result. One interpolates
raw `proc.stderr`; one deliberately omits it; and a third (found in review) interpolates a runner
exception whose `str()` embeds the argv. Scrub **every** raise site of the two values known to be
sensitive and in hand — the ssh `host` and a configured absolute `claude_path` — so the empty-response
failure regains a diagnostic *and* no message discloses a host or a username-bearing path into logs or
the health report, whichever failure mode fired.

## Background

Issue #41 pairs two findings that, as filed, "pull in opposite directions":

1. **The empty-response error drops its only diagnostic** (`backends.py:112-115`). An exit-0-empty
   failure's one piece of evidence about *why* is `proc.stderr`, and the message discards it,
   interpolating only `len(proc.stdout)` (an `int`). The operator gets "returned no text" and no way
   to learn whether the CLI warned, hit a quota, or wrote a diagnostic before returning nothing.

2. **Its sibling two lines up leaks a hostname** (`backends.py:96`,
   `f"claude-max exit {proc.returncode}: {proc.stderr[:200]}"`). For an ssh transport failure stderr
   is exactly where a real hostname appears (`ssh: Could not resolve hostname <host>: ...`).

The filed tension: fixing (1) the obvious way — append `proc.stderr[:200]` — walks straight into (2),
doubling the leak surface rather than halving it.

### Re-diagnosis against current code (2026-07-24)

Both sites are confirmed at the filed line numbers in the current `sluice/core/backends.py`. Two
facts sharpen the issue as filed:

- **The disclosure surface is ~4× wider than the issue documents.** The issue names only
  `judge.py:61`. A `BackendError` carrying host-bearing stderr actually reaches:
  1. `backends.py:217` — `_log.warning("primary backend failed, falling back: %s", e)`. This is the
     *most likely* route: claude-max is the flat-rate **primary**, an ssh hostname-resolution failure
     is precisely what triggers the documented fallback, and `FallbackBackend` logs the primary error
     at WARNING before degrading.
  2. `backends.py:224` — re-raised into `both backends failed: primary={e}; ...` when the fallback
     also dies, then propagated upward.
  3. `judge.py:61` — `_log.warning("batch %d backend error: %s", n, e)` (the filed route).
  4. `app.py:697` — `probe_error = str(e)` in the doctor probe, surfaced in the health report.

  Patching individual log/display sites is whack-a-mole across four call sites and fragile against a
  fifth being added. The only fix that closes all of them is scrubbing stderr **where the
  `BackendError` is constructed**, so the sensitive value never enters the exception object.

- **The value surface is wider than the issue's `host`-only framing.** The identical `:96` route also
  leaks `claude_path`. The code's own comment (`:81-84`) states that on a configured (remote) host
  `claude_path` "should be the absolute path", because `claude` is commonly not on a remote host's
  non-interactive PATH. A missing/again-relocated binary makes the remote shell return
  `bash: /home/<user>/.local/bin/claude: No such file or directory` (exit 127) on stderr — and that
  absolute path, frequently carrying a username, is interpolated exactly like the hostname.

- **There are THREE `BackendError` construction sites in `complete()`, not two** (found in
  `/review-plan`, reviewer + architect, independently). The re-diagnosis enumerated the *sinks*
  thoroughly but hand-listed the *sources* — the standing "enumerate, don't hand-list" lesson biting
  exactly as warned. The third is `backends.py:94`:
  `except Exception as e: raise BackendError(f"claude-max invocation failed: {e}") from e`. On a
  **timeout** — the module docstring's *own named* primary-failure mode — `self.runner` raises
  `subprocess.TimeoutExpired`, whose `str()` is `Command '{cmd}' timed out after {t} seconds` with
  `cmd = self.cmd_template = ["ssh", host, claude_path, ...]`, leaking **both** secrets; a missing
  absolute binary raises `FileNotFoundError` naming the executable. Both verified by running them. A
  *hung* remote host (this fix's own motivating scenario) times out rather than exiting nonzero, so it
  takes route 94 — **bypassing a fix that only covered `:96`/`:113`** — and the leaked `e` then flows
  to the `:217` WARNING and onward. So the scrub must cover all three sources, and the guarantee must
  be worded to match.

Boundary redaction dissolves the filed tension entirely: once every raise site scrubs at
construction, finding (1)'s message can safely *regain* stderr as a diagnostic while finding (2) — and
the timeout branch — stop leaking. This is the issue's option 1 ("redact at the boundary — most work,
best outcome"); the wider surface makes it dominant over the other three options, none of which close
routes 1/2/4.

**Neutrality note (per the standing lesson).** This is a *runtime* disclosure route, not a Rule 5
repo-neutrality one: the message *templates* are neutral, nothing personal is compiled into the repo,
and `host`/`claude_path` arrive from config. The fix strengthens the runtime posture; it does not
change the repo-neutrality guarantee. Per memory, this spec describes the check structurally and does
**not** spell out any real host or path value.

## Design

Two files: `sluice/core/backends.py` (production) and `tests/test_backends.py` (tests). Stdlib-only,
no config knob, no new dependency. None of the four load-bearing invariants
(never-clobber / never-regress / hard CV gate / empty-config-abstains) is touched; neutrality is
strengthened.

### 1. `_redact` — a pure module-level helper

```python
def _redact(text: str, secrets: dict[str, str]) -> str:
    """Replace each sensitive value with a label, so a backend error keeps its
    diagnostic shape without disclosing the host or an absolute path -- both reach
    proc.stderr on an ssh/exec failure and fan out to WARNING logs (FallbackBackend,
    judge) and the doctor health report. A secret is SKIPPED when it is empty (a local
    run leaves host empty; the default claude_path is generic), shorter than 3 chars
    (would mangle common substrings), or the exact generic default 'claude' (a
    substring of both legitimate CLI diagnostics and of 'claude-max' itself).

    Secrets are replaced LONGEST-first so that when one is a substring of another
    (e.g. the host appears inside the absolute claude_path) the longer is caught whole
    before the shorter can fragment it -- otherwise the shorter replace would alter the
    longer's text and its remaining, possibly username-bearing, fragment would survive."""
    for value, label in sorted(secrets.items(), key=lambda kv: len(kv[0]), reverse=True):
        if value and len(value) >= 3 and value != "claude":
            text = text.replace(value, label)
    return text
```

- **Pure, no I/O** — matches the module's injected-purity discipline (the runner and HTTP poster are
  already injected so everything is tested offline). Independently unit-testable.
- **Guard rationale.** Empty → nothing to strip (local run / default). `len < 3` → too generic;
  replacing a 1–2 char token would mangle common substrings of legitimate stderr. `== "claude"` → the
  `claude_path` default is exactly `"claude"`; it is a substring of both `claude-max` and ordinary CLI
  diagnostics, so stripping it would corrupt the very diagnostic we are trying to preserve.
- **Placeholder style — labeled (`<host>` / `<path>`), settled at `/review-plan`.** No reviewer
  objected; the cross-cutting reviewer noted the dict-keyed-by-value signature "supports either". The
  label tells the operator *which* class of failure occurred (host unresolved vs binary missing),
  which directly serves finding (1)'s diagnosability goal, so labeled wins on the same rationale that
  motivates the whole change. (`/review-pr` can still revisit at diff time.)

### 2. `ClaudeMaxBackend._scrub` — a thin method naming *which* attributes are sensitive

```python
def _scrub(self, text: str) -> str:
    """Strip this backend's own secrets (host, configured claude_path) from any text
    that becomes a BackendError message -- proc.stderr OR str(a runner exception),
    whose TimeoutExpired/FileNotFoundError forms carry the argv."""
    return _redact(text, {self.host: "<host>", self.claude_path: "<path>"})
```

Named `_scrub` (not `_stderr_safe`), because it is applied to the invocation-failure branch's
`str(e)` too, which is a runner exception, not stderr. Encapsulates the two sensitive attributes so
all three call sites read cleanly and none restates the secret set. Keeps the sensitivity knowledge on
the backend that owns those attributes; keeps `_redact` general and secret-agnostic.

### 3. The THREE call sites, inside `complete`

```python
# invocation-failed branch (timeout / ssh / missing binary): TimeoutExpired.str embeds the argv
except Exception as e:
    raise BackendError(f"claude-max invocation failed: {self._scrub(str(e))}") from e
...
# finding (2): the sibling stops leaking
if proc.returncode != 0:
    raise BackendError(
        f"claude-max exit {proc.returncode}: {self._scrub(proc.stderr)[:200]}")
...
# finding (1): the empty-response error REGAINS a diagnostic
if not text:
    detail = self._scrub(proc.stderr).strip()[:200]
    raise BackendError(
        f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace"
        + (f"; stderr: {detail}" if detail else "") + ")")
```

The `from e` chaining is kept (matching the module's other raises). Note it precisely: the guarantee
is that **`str(BackendError)`** carries no secret — the chained `__cause__` (the raw
`TimeoutExpired`) would only surface in a full traceback, and none of the four sinks logs with
`exc_info`/`_log.exception` (all use `%s`/`str(e)`). If a future sink starts logging tracebacks, the
chained cause becomes a fresh route; that is out of scope here and noted, not closed.

Two ordering invariants are load-bearing:

- **Redact, *then* truncate.** Scrub the full stderr and slice `[:200]` afterward. Slicing first could
  split a secret across the 200-char boundary and leave a fragment that `str.replace` never sees. A
  test pins this with a secret that **straddles** the boundary (see Testing, tst-001) — the tail
  wording certifies nothing.
- **Append the diagnostic only when present.** On exit-0-empty with no stderr, the message stays the
  clean `"...whitespace)"` and preserves the `"no text"` substring the existing test matches; when
  stderr *is* present (the interesting case — a warning printed before the empty return), it is
  appended, scrubbed.

### 4. Comment update

The block comment at `:98-111` currently explains why finding (1)'s guard exists and notes stderr was
omitted. Update it to record that the message now carries *scrubbed* stderr and why the boundary scrub
makes that safe (the four-sink fan-out, closed at source).

### Data flow after the fix

With **all three** raise sites scrubbed, no secret enters the `str()` of any `BackendError`
`complete()` produces. All four sinks — the FallbackBackend WARNING (`:217`), the both-failed re-raise
(`:224`), `judge.py:61`, `app.py:697` — are clean *by construction*, whether the failure was a
nonzero exit, an empty response, **or a timeout/ssh/missing-binary invocation error**. No per-sink
edit is made or needed; that is the whole point of fixing at the source.

## Testing

Behaviour-asserting, offline, mutation-witnessed (per CLAUDE.md: run
`compileall --invalidation-mode checked-hash` once, mutate by MOVING/DELETING, witness by node id).

**Synthetic-fixture constraint (neu-001, inv-001).** Every host/`claude_path` value used to *exercise*
redaction MUST be obviously synthetic and MUST NOT be a real hostname or a real absolute path — a
local review pass cannot tell a real host from a fake one, and the `≥ 3`-char / `≠ "claude"` guards
force the fixtures to be substantive, which is exactly where a real value could slip into `tests/`.
Use the `example.invalid` / `Example` family: host `host.example.invalid`, path
`/home/example/.local/bin/claude`. Each such fixture carries a one-line comment stating it is chosen
to be non-real. The existing `host="h"` fixture is `< 3` chars and cannot exercise the redaction path,
so a new longer value is *invented*, never borrowed from a real config.

**Pure `_redact` (unit):**

- strips a host → its label; strips a configured absolute `claude_path` → its label
- does **not** strip the default `"claude"` (a stderr containing `claude` as legitimate text, with
  `claude_path` left at default, survives untouched)
- does **not** strip an empty host (local run)
- does **not** strip a `< 3`-char value
- **redact-before-truncate — the secret STRADDLES the boundary (tst-001).** A distinctive host begins
  at index ~197 of a `> 200`-char text, so it spans the `[:200]` cut. Redact-then-slice removes the
  raw bytes before the cut → no fragment survives; slice-then-redact cuts the host mid-token, leaving
  a fragment (e.g. `...HOST-SENTINEL-EXAM`) that `str.replace(full_host)` never matches → it would
  leak. The test asserts a *distinctive prefix* of the host (not the full substring) is absent from
  `self._scrub(text)[:200]`. Worded at the tail (secret entirely past 200) this would pass under both
  orderings and certify nothing — straddling is what makes it discriminating.
- **overlap — dict pinned SHORTER-key-first (tst-002).** The host is a substring of the `claude_path`
  and the test's secrets dict inserts the *host* (shorter) key first, matching `_scrub`'s own
  `{self.host: ..., self.claude_path: ...}` order. Longest-first `sorted` catches the path whole →
  both scrubbed. Raw insertion-order iteration would replace the host first, fragment the path, and
  leave a username-bearing tail → the test reddens. If the dict were written path-first, insertion
  order would already equal longest-first and the `sorted`-removal mutant would stay green (an
  equivalent mutant) — so the ordering is pinned deliberately.

**`ClaudeMaxBackend.complete` via the injected runner (behaviour):**

- **invocation-failure / timeout (rev-001, arc-001)**: a `runner` that raises
  `subprocess.TimeoutExpired(cmd=self.cmd_template, timeout=…)` on a backend constructed with a
  configured synthetic `host` *and* `claude_path` (so the auto-built `cmd_template` carries them and
  `self.host`/`self.claude_path` match) → the raised `str` contains the `<host>`/`<path>` labels and
  **neither** the host nor the path. Uses the `host=`/`claude_path=` constructor path, not an explicit
  `cmd_template`, so the two stay coupled the way production couples them.
- exit≠0 with stderr = host + a real diagnostic → message contains the label **and** the diagnostic,
  **not** the raw host
- exit-0-empty with a host-bearing stderr warning → message carries the *scrubbed* warning (diagnostic
  regained) **and not** the host, and still matches `"no text"`
- `claude_path` leak: exit 127, stderr `bash: <abs-path>/claude: No such file or directory`,
  `claude_path` = that abs path → message shows the path label, not the path

**Load-bearing neutrality guarantee:** for a configured host, `str(BackendError)` never contains the
host substring — asserted at **all three** raise sites (`:94` invocation-failed, `:96` exit≠0, `:113`
empty). This is the property the whole change exists to provide.

**Mutation witnesses** (each must redden a *newly-added* test by node id; no pre-existing test in
`tests/test_backends.py` configures a host, so none of them accidentally catches these):

- delete the `_scrub` wrap at `:94` → the timeout/invocation-failure neutrality test reddens
- delete the `_scrub` wrap at `:96` → the exit≠0 neutrality test reddens
- delete the stderr inclusion in finding (1) → the diagnostic-regained test reddens
- delete the `value != "claude"` guard clause → the default-`claude`-survives test reddens
- delete the `len(value) >= 3` clause → the short-value test reddens
- remove the longest-first `sorted(...)` ordering (iterate `secrets.items()` raw) → the overlap test
  reddens (relies on the pinned shorter-key-first dict above)
- **swap redact/truncate order** `self._scrub(x)[:200]` → `self._scrub(x[:200])` → the straddle test
  reddens (this is the witness the tail-worded version lacked)

**Existing tests unaffected (verified against current source):**
`test_claudemax_runner_nonzero_raises` (stderr `"boom"`, `cmd_template=["claude"]` so `host=""` and
`claude_path="claude"` are both skipped → unchanged), `test_claudemax_empty_stdout_on_exit_zero_raises`
(stderr `""` → `detail=""`, no `; stderr:` appended, `"no text"` preserved), and
`test_claudemax_transport_failure_raises_backend_error` (its `OSError` message is synthetic and its
`cmd_template=["claude"]` skips both secrets → unchanged) all stay green because the changes are
additive to behaviour.

## Non-goals

- **Redacting stderr from the other backends' errors.** `OpenAiCompatibleBackend` and
  `AnthropicBackend` build their messages from `finish_reason`/`stop_reason` and their own
  exception text, not from a host-bearing subprocess stderr. Only `ClaudeMaxBackend` shells a remote
  process whose stderr carries a configured host/path. Widening would be speculative.
- **A general secret-scrubbing layer / redaction of API keys.** Keys are not interpolated into any
  `BackendError` today (the HTTP path attaches response *bodies*, not request headers). Adding a
  broad scrubber is a different, larger change; YAGNI here.
- **Structured `--output-format json` parsing.** The existing comment already defers the
  stop_reason-based truncation guard for the same reason; unrelated to this fix.
- **Making redaction evasion-proof.** `str.replace` on known values is a disclosure-reduction, not a
  guarantee against a stderr that paraphrases the host some other way. It removes the two values known
  to be sensitive and in hand; it is not claimed to catch an arbitrary echo.

## Definition of done

- `ruff check sluice tests` → clean.
- `python -m pytest` → green; record the added test count (existing 868 unaffected).
- All seven mutation witnesses above each redden their named test by node id, run after the
  checked-hash `compileall` (note: the two size-preserving edits — the guard-clause deletions — need
  the content-addressed cache CLAUDE.md's `compileall` line provides).
- Behaviour spot-check (offline, no live CLI), **both leak routes**:
  - exit≠0: fake `runner` returning `returncode=1, stderr="ssh: Could not resolve hostname <h>: ..."`
    on a backend with a configured synthetic `host` → raised `str` contains the host label, not the
    host.
  - timeout: fake `runner` raising `subprocess.TimeoutExpired(cmd=backend.cmd_template, timeout=1)` on
    a backend built with a configured synthetic `host`/`claude_path` → raised `str` contains the
    labels, not the host or the path.

## Process

Proportionate: brainstorm → `/review-plan` (specialists; they also rule on placeholder style) →
subagent-driven implementation → verify → `/review-pr` **before push** → CodeRabbit cloud → the
non-negotiable merge gate (`reviewDecision == APPROVED` on the head SHA, `mergeState == CLEAN`, CI
pending 0, base unmoved → `gh pr merge --rebase --delete-branch`).

Commit: `fix(backends): scrub host and claude_path from stderr at the BackendError boundary (#41)`.
