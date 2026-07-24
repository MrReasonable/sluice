# Backend stderr redaction — design

- **Date**: 2026-07-24
- **Status**: implemented + `/review-pr` (5 specialists + CodeRabbit CLI) folded — rev-001 (High,
  `from None` closes the classify.py `_log.exception` chain leak) + CodeRabbit Major (token-aware
  `_redact`, user-chosen) + tst-001 (empty-response straddle) all folded. Earlier: reviewed at plan
  time (5 specialists, 0 Critical / 2 High / 2 Medium / 1 Low; findings folded in);
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
    """... (see the shipped docstring in sluice/core/backends.py for the full text).
    Matching is TOKEN-AWARE -- re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", label, text) --
    so a value is replaced only where it stands as a whole token, never inside a longer
    word. LONGEST-first ordering handles the host-inside-path overlap."""
    for value, label in sorted(secrets.items(), key=lambda kv: len(kv[0]), reverse=True):
        if value:
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", label, text)
    return text
```

- **Pure, no I/O** — matches the module's injected-purity discipline (the runner and HTTP poster are
  already injected so everything is tested offline). Independently unit-testable.
- **Token-aware matching (folded post-`/review-pr`, CodeRabbit Major + user decision).** The earlier
  form guarded `len(value) >= 3` and used a plain `str.replace`. CodeRabbit correctly flagged that
  this leaves a genuinely **short** configured host (`db`, `qa`) unscrubbed — a hole in the change's
  own "scrub the configured host" guarantee. The fix is a word-boundary lookaround
  (`(?<!\w)…(?!\w)`), which redacts a short host *as a whole token* without a length floor mangling
  every `db` inside `database`, and — unlike `\b` — still anchors an absolute path that begins with
  `/`, so the same uniform match covers host and path. The length guard is therefore **removed**.
- **The `"claude"` exemption is ROLE-BASED, in `_scrub` (folded post-`/review-pr`, CodeRabbit
  Major + user decision).** `_redact` itself has **no** `"claude"` exemption — it redacts every
  non-empty token. An earlier draft guarded `value != "claude"` *here*, which was value-based and
  conflated two roles: it correctly spared the default `claude_path` (the CLI's own binary name,
  non-sensitive, a token in ordinary diagnostics) but also spared a host *named* `claude`, which then
  leaked. The exemption now lives in `_scrub`, which omits `claude_path` from the secret map only when
  it is the default `"claude"` — so the default binary name survives a diagnostic, while a host named
  `claude` is a configured, sensitive value and **is** redacted. The former residual is closed.
- **Placeholder style — labeled (`<host>` / `<path>`), settled at `/review-plan`.** No reviewer
  objected; the cross-cutting reviewer noted the dict-keyed-by-value signature "supports either". The
  label tells the operator *which* class of failure occurred (host unresolved vs binary missing),
  which directly serves finding (1)'s diagnosability goal, so labeled wins on the same rationale that
  motivates the whole change. (`/review-pr` can still revisit at diff time.)

### 2. `ClaudeMaxBackend._scrub` — a thin method naming *which* attributes are sensitive

```python
def _scrub(self, text: str) -> str:
    """... (see the shipped docstring). Builds the secret map with a ROLE-BASED exemption:
    host is always added when set; claude_path is added only when configured away from the
    default 'claude', so the default binary name is spared but a host NAMED 'claude' is
    still redacted."""
    secrets: dict[str, str] = {}
    if self.host:
        secrets[self.host] = "<host>"
    if self.claude_path and self.claude_path != "claude":
        secrets[self.claude_path] = "<path>"
    return _redact(text, secrets)
```

Named `_scrub` (not `_stderr_safe`), because it is applied to the invocation-failure branch's
`str(e)` too, which is a runner exception, not stderr. Encapsulates the two sensitive attributes so
all three call sites read cleanly and none restates the secret set. Keeps the sensitivity knowledge on
the backend that owns those attributes; keeps `_redact` general and secret-agnostic.

### 3. The THREE call sites, inside `complete`

```python
# invocation-failed branch (timeout / ssh / missing binary): TimeoutExpired.str embeds the argv
except Exception as e:
    raise BackendError(f"claude-max invocation failed: {self._scrub(str(e))}") from None
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

**`from None`, not `from e` (folded post-`/review-pr`, rev-001).** An earlier draft kept `from e` and
argued the chained `__cause__` (the raw `TimeoutExpired`, argv-bearing) was safe "because no sink logs
with `exc_info`". The cross-cutting reviewer falsified that premise: `sluice/track/classify.py:96`
logs a failed `complete()` with `_log.exception`, which renders the **whole chain** — reproduced,
leaking both host and path on exactly the hung-host route. `from None` clears `__cause__` and sets
`__suppress_context__`, so every traceback-**rendering** sink (`_log.exception`,
`traceback.format_exception`) omits the raw cause — which is the entire realistic leak surface — while
the scrubbed message still carries the diagnostic. (`e` remains *referenced* via `__context__`;
`from None` suppresses its *display*, not the reference, so only a sink that walked that attribute by
hand — which nothing in this codebase does — could still reach it. Scope the guarantee to rendered
output, not to the object graph.) The rendered-chain residual is now **closed at the source**, not
accepted. The `:96`/`:113` raises are in normal flow (no active exception), so they have no chain to
suppress. Pinned by `test_claudemax_timeout_chain_carries_no_secret`, which asserts on the full
`traceback.format_exception` output (what `_log.exception` emits), not merely `str(err)`.

Two ordering invariants are load-bearing:

- **Redact, *then* truncate.** Scrub the full stderr and slice `[:200]` afterward. Slicing first could
  split a secret across the 200-char boundary and leave a fragment that `str.replace` never sees. This
  ordering lives *here*, at the call site, not in `_scrub` — so the pinning test must drive
  `complete()` with a secret that **straddles** the boundary (see Testing, tst-001); a pure test that
  composes `[:200]` itself is inert.
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
local review pass cannot tell a real host from a fake one. Use the `example.invalid` / `Example`
family: host `host.example.invalid`, path `/home/example/.local/bin/claude`. Each such fixture carries
a one-line comment stating it is chosen to be non-real; a new value is *invented*, never borrowed from
a real config.

**Pure `_redact` (unit):**

- strips a host → its label; strips a configured absolute `claude_path` → its label
- **strips a genuinely short host (`db`) as a whole token** — token-awareness means no length floor
  leaves it exposed (folded post-review)
- **does *not* mangle a longer word that merely contains the host** (`db` inside `db2`/`database`
  survives) — the token-awareness witness that replaced the old `<3`-char-skip test
- **`_redact` has *no* `"claude"` exemption** — it redacts a bare `"claude"` token like any other
  (`test_redact_has_no_claude_exemption`); the default-binary exemption is role-based, in `_scrub`
- does **not** strip an empty host (local run)

**`ClaudeMaxBackend._scrub` (role-based exemption — folded post-review):**

- **omits the default `claude_path` (`"claude"`)** from the secret map, so a legit `claude` token in a
  diagnostic survives while the configured host is still scrubbed (`test_scrub_omits_default_claude_path`)
- **redacts a host *named* `"claude"`** — the exemption is for the default *path* role only, so a
  configured host that happens to be `claude` is scrubbed, closing the former value-based residual
  (`test_scrub_redacts_host_named_claude`)
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
- **timeout — the full exception chain carries no secret (rev-001)**: the same timeout, but the test
  asserts on `traceback.format_exception(err)` (what `_log.exception` emits), not just `str(err)` —
  proving `from None` severed the argv-bearing `TimeoutExpired` cause.
- exit≠0 with stderr = host + a real diagnostic → message contains the label **and** the diagnostic,
  **not** the raw host
- exit-0-empty with a host-bearing stderr warning → message carries the *scrubbed* warning (diagnostic
  regained) **and not** the host, and still matches `"no text"`
- `claude_path` leak: exit 127, stderr `bash: <abs-path>/claude: No such file or directory`,
  `claude_path` = that abs path → message shows the path label, not the path
- **redact-before-truncate — driven through `complete()`, secret STRADDLES the boundary (tst-001).**
  The `[:200]` truncation lives *only* at the `:96`/`:113` call sites, **not** in `_scrub`, so the
  ordering can only be witnessed by exercising `complete()` — a pure-`_redact` test that composes
  `[:200]` in its own body applies the correct order regardless of production and is an inert witness.
  A `runner` returns `returncode≠0` with `proc.stderr = "."*180 + host` where `host` is a distinctive
  synthetic value (`HOST-SENTINEL-EXAMPLE.invalid`, ~29 chars) that **straddles** index 200 (starts at
  180, so a `≥ 13`-char distinctive prefix still falls before the cut). Assert that prefix
  (`HOST-SENTINEL`) is **absent** from `str(BackendError)`. Correct order (redact full → `[:200]`):
  the whole host → `<host>` before truncation, no fragment survives → prefix absent → green. Swapped
  order (`self._scrub(proc.stderr[:200])`): the cut leaves `...HOST-SENTINEL-EXAMP`, which
  `str.replace(full_host)` cannot match → the prefix leaks → the test reddens. Starting the host at
  ~197 (round-1 wording) would leave only ~3 surviving chars, too few for a distinctive prefix to
  redden — the offset is load-bearing.

**Load-bearing neutrality guarantee:** for a backend built the way production builds it — via
`host=`/`claude_path=`, so `self.host`/`self.claude_path` track the argv in `cmd_template` (the
`claude_max.py` factory never passes an explicit `cmd_template`; verified) — `str(BackendError)` never
contains the configured host **as a standalone token**, asserted at **all three** raise sites (`:94`
invocation-failed, `:96` exit≠0, `:113` empty). (Token-aware matching redacts the host wherever it
stands as a token; it deliberately leaves a coincidental *substring* of a longer word alone — a `db`
inside `database` is not the host disclosed, and mangling it would corrupt the diagnostic.) This is the property the whole change exists to provide. The guarantee is scoped
to that coupling deliberately (inv-001): a caller that passes a divergent explicit `cmd_template` while
leaving the constructor's host/path defaults is not a production path and is not made worse by this
change; the `_scrub` docstring notes the assumption rather than adding a code guard for an unreachable
surface.

**Mutation witnesses** (each must redden a *newly-added* test by node id; no pre-existing test in
`tests/test_backends.py` configures a host, so none of them accidentally catches these):

- delete the `_scrub` wrap at the invocation-failed raise → the timeout neutrality test reddens
- **`from None` → `from e`** at the invocation-failed raise → `test_claudemax_timeout_chain_carries_no_secret`
  reddens (the chained `TimeoutExpired` re-appears in the formatted traceback) — the rev-001 witness
- delete the `_scrub` wrap at the exit≠0 raise → the exit≠0 neutrality test reddens
- delete the stderr append in finding (1) → the diagnostic-regained test reddens
- delete the role-based exemption in `_scrub` (`and self.claude_path != "claude"`, so the default
  path is always added) → `test_scrub_omits_default_claude_path` reddens (the default `claude` gets
  redacted); `test_scrub_redacts_host_named_claude` pins the complementary direction (a host named
  `claude` must stay redacted — it reddens if a `value != "claude"` guard is re-introduced into `_redact`)
- **token-aware `re.sub(...)` → plain `str.replace(value, label)`** → `test_redact_short_host_not_matched_inside_a_word`
  reddens (a substring replace mangles `db` inside `db2`/`database`) — the token-awareness witness
  that replaced the old `len(value) >= 3` clause
- remove the longest-first `sorted(...)` ordering (iterate `secrets.items()` raw) → the overlap test
  reddens (relies on the pinned shorter-key-first dict above)
- **swap redact/truncate order** at the exit≠0 raise `self._scrub(proc.stderr)[:200]` →
  `self._scrub(proc.stderr[:200])` → `test_claudemax_redacts_before_truncating` reddens; the same swap
  on the empty-response raise → `test_claudemax_empty_response_redacts_before_truncating` reddens (this
  is why each test must exercise `complete()`, not compose `[:200]` in its own body — and why BOTH
  truncating branches now have a straddle witness, tst-001)

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
- **Making redaction evasion-proof.** Token-aware `re.sub` on known values is a disclosure-reduction,
  not a guarantee against a stderr that paraphrases the host some other way. It removes the two values
  known to be sensitive and in hand; it is not claimed to catch an arbitrary echo.

## Definition of done

- `ruff check sluice tests` → clean.
- `python -m pytest` → green; record the observed suite total and the count of tests this change adds
  (do not hard-assert a fixed total, which drifts as the suite grows).
- **Every** mutation witness in the list above reddens its named test by node id (the list grew as
  findings folded — count it from the list, do not hard-assert a total), run after the checked-hash
  `compileall` (note: the size-preserving edits — e.g. the `re.sub`↔`str.replace` swap and the
  redact/truncate order swap — need the content-addressed cache CLAUDE.md's `compileall` line provides).
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
