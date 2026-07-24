# Backend stderr redaction — design

- **Date**: 2026-07-24
- **Status**: brainstormed; awaiting `/review-plan`
- **Origin**: issue #41 (two findings from PR #37 review: `tst-004` empty-response error drops its
  only diagnostic; the neutrality reviewer's out-of-scope note that the sibling error leaks a
  hostname). User decision 2026-07-24: fix approach = **redact at the boundary**; scrub scope =
  **host + configured `claude_path`**; placeholder style **deferred to the review agents**.

## Goal

`ClaudeMaxBackend.complete` builds two `BackendError` messages from a subprocess result. One
interpolates raw `proc.stderr`; the other deliberately omits it. Make **both** carry stderr that has
been scrubbed of the two values known to be sensitive and in hand — the ssh `host` and a configured
absolute `claude_path` — so the empty-response failure regains a diagnostic *and* neither message
discloses a host or a username-bearing path into logs or the health report.

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

Boundary redaction dissolves the filed tension entirely: once stderr is scrubbed at construction,
finding (1)'s message can safely *regain* stderr as a diagnostic while finding (2) stops leaking.
This is the issue's option 1 ("redact at the boundary — most work, best outcome"); the wider surface
makes it dominant over the other three options, none of which close routes 1/2/4.

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
- **Placeholder style — deferred to the review agents.** The recommendation carried into review is
  **labeled** placeholders (`<host>` / `<path>`): one extra token of code, but it tells the operator
  *which* class of failure occurred (host unresolved vs binary missing), which directly serves
  finding (1)'s diagnosability goal. The alternative is a single `<redacted>`. The `/review-plan` and
  `/review-pr` agents rule; implementation follows their consensus. The dict-keyed-by-value signature
  supports either (labels are just the dict values).

### 2. `ClaudeMaxBackend._stderr_safe` — a thin method naming *which* attributes are sensitive

```python
def _stderr_safe(self, stderr: str) -> str:
    return _redact(stderr, {self.host: "<host>", self.claude_path: "<path>"})
```

Encapsulates the two sensitive attributes so both call sites read cleanly and neither restates the
secret set. Keeps the sensitivity knowledge on the backend that owns those attributes; keeps `_redact`
general and secret-agnostic.

### 3. The two call sites, inside `complete`

```python
# finding (2): the sibling stops leaking
if proc.returncode != 0:
    raise BackendError(
        f"claude-max exit {proc.returncode}: {self._stderr_safe(proc.stderr)[:200]}")
...
# finding (1): the empty-response error REGAINS a diagnostic
if not text:
    detail = self._stderr_safe(proc.stderr).strip()[:200]
    raise BackendError(
        f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace"
        + (f"; stderr: {detail}" if detail else "") + ")")
```

Two ordering invariants are load-bearing:

- **Redact, *then* truncate.** Scrub the full stderr and slice `[:200]` afterward. Slicing first could
  split a secret across the 200-char boundary and leave a fragment that `str.replace` never sees. A
  test pins this (secret at the tail of a >200-char stderr).
- **Append the diagnostic only when present.** On exit-0-empty with no stderr, the message stays the
  clean `"...whitespace)"` and preserves the `"no text"` substring the existing test matches; when
  stderr *is* present (the interesting case — a warning printed before the empty return), it is
  appended, scrubbed.

### 4. Comment update

The block comment at `:98-111` currently explains why finding (1)'s guard exists and notes stderr was
omitted. Update it to record that the message now carries *scrubbed* stderr and why the boundary scrub
makes that safe (the four-sink fan-out, closed at source).

### Data flow after the fix

The sensitive value never enters the `BackendError`. All four sinks — the FallbackBackend WARNING
(`:217`), the both-failed re-raise (`:224`), `judge.py:61`, `app.py:697` — are clean *by
construction*. No per-sink edit is made or needed; that is the whole point of fixing at the boundary.

## Testing

Behaviour-asserting, offline, mutation-witnessed (per CLAUDE.md: run
`compileall --invalidation-mode checked-hash` once, mutate by MOVING/DELETING, witness by node id).

**Pure `_redact` (unit):**

- strips a host → its label; strips a configured absolute `claude_path` → its label
- does **not** strip the default `"claude"` (a stderr containing `claude` as legitimate text, with
  `claude_path` left at default, survives untouched)
- does **not** strip an empty host (local run)
- does **not** strip a `< 3`-char value
- **redact-before-truncate**: a secret at the tail of a `> 200`-char stderr is fully gone from the
  `[:200]` result — no fragment
- **overlap**: when the host is a substring of the `claude_path`, both are fully scrubbed (the
  longest-first ordering catches the path whole before the host can fragment it), and no
  username-bearing fragment survives

**`ClaudeMaxBackend.complete` via the injected runner (behaviour):**

- exit≠0 with stderr = host + a real diagnostic → message contains the label **and** the diagnostic,
  **not** the raw host
- exit-0-empty with a host-bearing stderr warning → message carries the *scrubbed* warning (diagnostic
  regained) **and not** the host, and still matches `"no text"`
- `claude_path` leak: exit 127, stderr `bash: <abs-path>/claude: No such file or directory`,
  `claude_path` = that abs path → message shows the path label, not the path

**Load-bearing neutrality guarantee:** for a configured host, `str(BackendError)` never contains the
host substring — asserted at **both** raise sites (this is the property the whole change exists to
provide).

**Mutation witnesses** (each must redden a named test by node id):

- delete the `_stderr_safe` wrap at `:96` → the exit≠0 neutrality test reddens
- delete the stderr inclusion in finding (1) → the diagnostic-regained test reddens
- delete the `value != "claude"` guard clause → the default-`claude`-survives test reddens
- delete the `len(value) >= 3` clause → the short-value test reddens
- remove the longest-first `sorted(...)` ordering (iterate `secrets.items()` raw) → the overlap test
  reddens

**Existing tests unaffected (verified against current source):**
`test_claudemax_runner_nonzero_raises` (stderr `"boom"`, no host → unchanged) and
`test_claudemax_empty_stdout_on_exit_zero_raises` (stderr `""`, `"no text"` preserved) both stay
green because the changes are additive to behaviour.

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
- Mutation witnesses above each reddens its named test by node id, run after the checked-hash
  `compileall`.
- Behaviour spot-check (offline, no live CLI): construct `ClaudeMaxBackend` with a fake `runner`
  returning `returncode=1, stderr="ssh: Could not resolve hostname <h>: ..."` and a configured
  `host`, call `complete`, assert the raised `str` contains the host label and not the host.

## Process

Proportionate: brainstorm → `/review-plan` (specialists; they also rule on placeholder style) →
subagent-driven implementation → verify → `/review-pr` **before push** → CodeRabbit cloud → the
non-negotiable merge gate (`reviewDecision == APPROVED` on the head SHA, `mergeState == CLEAN`, CI
pending 0, base unmoved → `gh pr merge --rebase --delete-branch`).

Commit: `fix(backends): scrub host and claude_path from stderr at the BackendError boundary (#41)`.
