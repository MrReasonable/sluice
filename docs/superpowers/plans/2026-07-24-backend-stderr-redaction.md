# Backend stderr redaction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrub the ssh `host` and a configured absolute `claude_path` from every `BackendError` message `ClaudeMaxBackend.complete` builds, so an empty-response failure regains a diagnostic and no message leaks a host or username-bearing path into logs or the health report.

**Architecture:** A pure module-level `_redact(text, secrets)` does the replacement (longest-first, guarded); a thin `ClaudeMaxBackend._scrub(text)` method binds it to the two instance secrets; all three `BackendError` construction sites in `complete()` route their interpolated text through `_scrub`. The secret never enters the exception, so all four downstream sinks (FallbackBackend WARNING, both-failed re-raise, `judge.py`, doctor probe) are clean by construction — no per-sink edit.

**Tech Stack:** Python standard library only (`str.replace`, `sorted`, `subprocess`). No new dependency, no config knob. Test framework: pytest with injected fake `runner`s (offline).

**Spec:** `docs/superpowers/specs/2026-07-24-backend-stderr-redaction-design.md` (reviewed twice, all findings folded).

**Plan review:** `/review-plan` on this implementation plan — 5 specialists, **0 findings**; all mutation witnesses independently traced as valid (2026-07-24).

> **AMENDED post-`/review-pr` (commits `c8b9b53`, then the role-based fold).** `/review-pr` folded changes the task bodies below **predate**, so read the shipped code (and the spec) as authoritative where they differ:
> 1. **`_redact` is token-aware and has NO `"claude"` exemption**, not the `len(value) >= 3` / `str.replace` form Task 1 shows. It is `if value: text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", label, text)` (drops the length floor so a short host like `db` is scrubbed as a whole token). The generic-default exemption is now **role-based, in `_scrub`**: it omits `claude_path` from the map only when it is the default `"claude"`, so the default binary name is spared but a host *named* `claude` is still redacted (closing the former value-based residual). The Task-1 snippet and the Task-3 witness-table rows are **superseded** — live witnesses: `re.sub`→`str.replace` reddens `test_redact_short_host_not_matched_inside_a_word`; dropping the `_scrub` role exemption reddens `test_scrub_omits_default_claude_path`.
> 2. **The invocation-failed raise uses `from None`**, not `from e` (Task 2): it severs the argv-bearing `TimeoutExpired` chain that `track/classify.py:96`'s `_log.exception` would otherwise render. Pinned by `test_claudemax_timeout_chain_carries_no_secret`.
>
> The snippets flagged below are annotated inline; the task bodies otherwise stand as the record of the original three-task implementation.

## Global Constraints

- **Stdlib-only in `sluice/`.** No new runtime dependency. (`str.replace`/`sorted`/`subprocess` are stdlib.)
- **No personal data in `sluice/` or `tests/`.** Every host/path fixture that exercises redaction MUST be obviously synthetic — use the RFC-reserved `example.invalid` / `Example` family (`host.example.invalid`, `/home/example/.local/bin/claude`), never a real hostname or absolute path. Each such fixture carries a one-line comment stating it is chosen to be non-real.
- **Neutrality guarantee is runtime, not repo:** `str(BackendError)` carries no secret at all three raise sites, under the production coupling (backend built via `host=`/`claude_path=`, never a divergent explicit `cmd_template`).
- **Mutation discipline (CLAUDE.md):** run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before witnessing; mutate by MOVING/DELETING (never ADDING); witness by node id; **commit the implementation before any git-checkout-restoring witness** — restore mutants via `Edit`, not `git checkout`.
- **Comments explain WHY** — match the file's existing density (it already carries incident-encoding comments).
- **Conventional Commits** for every commit.
- **Tests assert behaviour**, fixtures synthetic and offline.

---

### Task 1: Pure `_redact` helper + its unit tests

The pure, secret-agnostic engine. Independently testable without constructing a backend — this is why it is a separate module-level function, not just method-body code.

**Files:**
- Modify: `sluice/core/backends.py` (insert `_redact` between `_urlopen` (ends line 61) and `class ClaudeMaxBackend:` (line 64))
- Test: `tests/test_backends.py` (add unit tests; add `_redact` to the existing import on lines 2-5)

**Interfaces:**
- Produces: `_redact(text: str, secrets: dict[str, str]) -> str` — replaces each `value` key with its `label`, longest-value-first, skipping empty / `< 3`-char / exact-`"claude"` values. Consumed by Task 2's `_scrub`.

- [ ] **Step 1: Write the failing unit tests**

Add `_redact` to the import block at the top of `tests/test_backends.py`:

```python
from sluice.core.backends import (
    BackendError, ClaudeMaxBackend, FallbackBackend, OpenAiCompatibleBackend,
    AnthropicBackend, make_backend, DEFAULT_MODELS, _redact,
)
```

Append these tests (fixtures are RFC-reserved-synthetic — `.invalid` per RFC 6761, `example` per RFC 2606 — chosen so no real host/path can appear):

```python
def test_redact_strips_host_to_label():
    # host.example.invalid is RFC-reserved (can never resolve) -- a non-real fixture.
    out = _redact("ssh: Could not resolve hostname host.example.invalid: nope",
                  {"host.example.invalid": "<host>"})
    assert out == "ssh: Could not resolve hostname <host>: nope"


def test_redact_strips_configured_path_to_label():
    # 'example' is the conventional placeholder user -- a non-real absolute path.
    out = _redact("bash: /home/example/.local/bin/claude: No such file",
                  {"/home/example/.local/bin/claude": "<path>"})
    assert out == "bash: <path>: No such file"


def test_redact_keeps_default_claude():
    # The default claude_path is exactly 'claude'; stripping it would corrupt the
    # very CLI diagnostics we are trying to preserve. Guarded by value != "claude".
    assert _redact("claude: error: usage", {"claude": "<path>"}) == "claude: error: usage"


def test_redact_keeps_empty_host():
    # A local run leaves host empty -> nothing to strip.
    assert _redact("some diagnostic", {"": "<host>"}) == "some diagnostic"


def test_redact_keeps_short_value():
    # A <3-char value is too generic; replacing it would mangle common substrings.
    assert _redact("a banana", {"an": "<host>"}) == "a banana"


def test_redact_overlap_scrubs_both_longest_first():
    # host is a substring of the path; the dict lists the SHORTER (host) key FIRST,
    # matching _scrub's own {self.host: ..., self.claude_path: ...} construction order.
    # Longest-first replacement catches the path whole before the host can fragment it.
    # Synthetic values (RFC-reserved).
    host = "h7.example.invalid"
    path = "/opt/h7.example.invalid/bin/claude"
    text = f"connect {host} failed; exec {path} missing"
    out = _redact(text, {host: "<host>", path: "<path>"})
    assert host not in out and path not in out
    assert out == "connect <host> failed; exec <path> missing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backends.py -k redact -v`
Expected: collection error / FAIL — `ImportError: cannot import name '_redact'`.

- [ ] **Step 3: Implement `_redact`**

Insert into `sluice/core/backends.py` after the `_urlopen` function (after line 61, before `class ClaudeMaxBackend:`):

```python
def _redact(text: str, secrets: dict[str, str]) -> str:
    """Replace each sensitive value with a label, so a backend error keeps its
    diagnostic shape without disclosing the host or an absolute path -- both reach
    proc.stderr on an ssh/exec failure (and str(a runner exception)) and fan out to
    WARNING logs (FallbackBackend, judge) and the doctor health report. A secret is
    SKIPPED when it is empty (a local run leaves host empty), shorter than 3 chars
    (would mangle common substrings of legitimate stderr), or the exact generic default
    'claude' (a substring of both ordinary CLI diagnostics and of 'claude-max' itself).

    Secrets are replaced LONGEST-first so that when one is a substring of another
    (the host can appear inside the absolute claude_path) the longer is caught whole
    before the shorter can fragment it -- otherwise the shorter replace would alter the
    longer's text and its remaining, possibly username-bearing, fragment would survive.
    """
    for value, label in sorted(secrets.items(), key=lambda kv: len(kv[0]), reverse=True):
        # SUPERSEDED post-review: shipped TOKEN-AWARE with NO "claude" exemption here (the default-
        # path exemption is role-based, in _scrub). A short host (`db`) is scrubbed as a whole token
        # instead of leaking. See the shipped code:
        #     if value:
        #         text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", label, text)
        if value and len(value) >= 3 and value != "claude":
            text = text.replace(value, label)
    return text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_backends.py -k redact -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/backends.py tests/test_backends.py
git commit -m "feat(backends): add pure _redact stderr-scrubbing helper (#41)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: `_scrub` method + wire all three raise sites + behaviour tests

Bind `_redact` to the backend's two secrets and route every `BackendError` in `complete()` through it. This is where finding (1) regains its diagnostic and findings (2) + the timeout branch stop leaking.

**Files:**
- Modify: `sluice/core/backends.py` — add `ClaudeMaxBackend._scrub`; edit the three raise sites in `complete()` (currently lines 94, 96, 112-115); update the block comment (currently lines 98-111)
- Test: `tests/test_backends.py` (add behaviour tests; add `import subprocess` at the top)

**Interfaces:**
- Consumes: `_redact` (Task 1).
- Produces: `ClaudeMaxBackend._scrub(self, text: str) -> str`. Behaviour: every `BackendError` `complete()` raises has a `str()` free of `self.host` and `self.claude_path` (under the production `host=`/`claude_path=` coupling).

- [ ] **Step 1: Write the failing behaviour tests**

Add `import subprocess` at the very top of `tests/test_backends.py` (above the existing `import pytest`). Then append:

```python
# RFC-reserved synthetic fixtures -- non-real host/path, chosen so no real value lands here.
_SYNTH_HOST = "host.example.invalid"            # RFC 6761/2606 reserved: can never resolve
_SYNTH_PATH = "/home/example/.local/bin/claude"  # 'example' is the placeholder user


def _claude(runner, *, host=_SYNTH_HOST, claude_path=_SYNTH_PATH):
    # host=/claude_path= (NOT an explicit cmd_template) so the auto-built argv carries
    # them AND self.host/self.claude_path match -- the way make_backend couples them in
    # production. That coupling is what the neutrality guarantee is scoped to.
    return ClaudeMaxBackend("m", host=host, claude_path=claude_path, runner=runner)


def test_claudemax_timeout_scrubs_host_and_path_from_message():
    # A hung remote host times out (TimeoutExpired.cmd = the argv), routing to the
    # invocation-failed branch -- the leak route a :96/:113-only fix would miss.
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 1)  # cmd is self.cmd_template
    with pytest.raises(BackendError) as ei:
        _claude(boom).complete("x")
    msg = str(ei.value)
    assert _SYNTH_HOST not in msg and _SYNTH_PATH not in msg
    assert "<host>" in msg and "<path>" in msg


def test_claudemax_nonzero_exit_scrubs_host_keeps_diagnostic():
    class R:
        returncode, stdout = 1, ""
        stderr = f"ssh: Could not resolve hostname {_SYNTH_HOST}: nodename nor servname provided"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert _SYNTH_HOST not in msg
    assert "<host>" in msg
    assert "Could not resolve hostname" in msg  # the diagnostic survives the scrub


def test_claudemax_empty_response_regains_scrubbed_diagnostic():
    class R:
        returncode, stdout = 0, "   \n"  # whitespace-only stdout -> empty after strip
        stderr = f"warning: quota low on {_SYNTH_HOST}"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert "no text" in msg                       # existing contract preserved
    assert _SYNTH_HOST not in msg
    assert "warning: quota low on <host>" in msg  # diagnostic regained, scrubbed


def test_claudemax_missing_binary_scrubs_path():
    class R:
        returncode, stdout = 127, ""
        stderr = f"bash: {_SYNTH_PATH}: No such file or directory"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert _SYNTH_PATH not in msg
    assert "<path>" in msg


def test_claudemax_redacts_before_truncating():
    # The [:200] lives at the call site, NOT in _scrub, so this MUST drive complete().
    # The host STRADDLES index 200 of proc.stderr (starts at 180): redact-then-slice
    # removes it whole; slice-then-redact cuts it mid-token, leaving a fragment
    # str.replace(full_host) can never match. Assert a distinctive >=13-char PREFIX
    # (not the full substring) so the surviving fragment reddens the swapped order.
    straddle_host = "HOST-SENTINEL-EXAMPLE.invalid"  # ~29 chars, distinctive, synthetic

    class R:
        returncode, stdout = 1, ""
        stderr = "." * 180 + straddle_host  # host occupies indices 180-208, straddles 200

    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R(), host=straddle_host).complete("x")
    assert "HOST-SENTINEL" not in str(ei.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backends.py -k "timeout or nonzero_exit_scrubs or regains or missing_binary or redacts_before" -v`
Expected: FAIL — `_scrub` not yet applied, so the raw host/path appears in the message (`AssertionError`), and the empty-response test fails because no stderr is appended yet.

- [ ] **Step 3: Add the `_scrub` method**

In `sluice/core/backends.py`, inside `class ClaudeMaxBackend`, add `_scrub` immediately before `def complete`:

```python
    def _scrub(self, text: str) -> str:
        """Strip this backend's own secrets (host, configured claude_path) from any
        text that becomes a BackendError message -- proc.stderr OR str(a runner
        exception), whose TimeoutExpired.cmd / FileNotFoundError forms carry the argv.
        Scrubs by self.host / self.claude_path, which cover the argv only when they
        built it: the production path (make_backend passes host=/claude_path=, never an
        explicit cmd_template). A caller supplying a divergent cmd_template with default
        host/path is out of scope -- not reachable via make_backend, and not made worse
        by this change."""
        # SUPERSEDED post-review: shipped with a ROLE-BASED exemption built here, not a flat map --
        #     secrets = {}
        #     if self.host: secrets[self.host] = "<host>"
        #     if self.claude_path and self.claude_path != "claude": secrets[self.claude_path] = "<path>"
        #     return _redact(text, secrets)
        # so the default claude_path is spared but a host named 'claude' is still redacted.
        return _redact(text, {self.host: "<host>", self.claude_path: "<path>"})
```

- [ ] **Step 4: Wire the three raise sites**

Edit the invocation-failed branch (currently line 93-94):

```python
        except Exception as e:  # timeout, ssh failure, missing binary
            # str(e) carries the argv -- TimeoutExpired.cmd is self.cmd_template
            # (["ssh", host, claude_path, ...]) and FileNotFoundError names the binary.
            # A hung host times out here, NOT at the exit-code branch, so this leak route
            # is real; scrub before the message reaches a WARNING log or the health report.
            # SUPERSEDED post-review (c8b9b53): shipped as `from None`, not `from e` -- `from e`
            # keeps the raw argv-bearing cause chained, which classify.py's _log.exception renders.
            raise BackendError(f"claude-max invocation failed: {self._scrub(str(e))}") from None
```

Edit the nonzero-exit branch (currently line 95-96):

```python
        if proc.returncode != 0:
            raise BackendError(
                f"claude-max exit {proc.returncode}: {self._scrub(proc.stderr)[:200]}")
```

- [ ] **Step 5: Update the block comment and the empty-response raise**

Replace the empty-response block (currently lines 98-115 — the comment plus the `if not text:` raise) with:

```python
        # Exit 0 with no text is a FAILED call wearing a successful one's clothes. Both
        # siblings already refuse it (OpenAiCompatibleBackend, AnthropicBackend); claude-max
        # was the outlier, returning "" for the caller to notice by itself. Raising here means
        # the same underlying condition triggers the documented fallback whichever provider
        # hits it, instead of one raising and one handing back a useless string.
        #
        # The message also appends the SCRUBBED stderr (self._scrub) when present: an exit-0
        # empty response often has a warning on stderr (quota, deprecation) that is the only
        # clue why. Scrubbing at construction is what makes surfacing it safe (see _scrub);
        # the append is conditional so a truly empty stderr keeps the clean "...whitespace)".
        #
        # Only the EMPTY half of the siblings' pair is implemented here. Their other guard keys on
        # finish_reason/stop_reason to catch a TRUNCATION, and this backend has no equivalent
        # because it runs the CLI in TEXT mode -- not because a CLI cannot report one:
        # `claude --print --output-format json` returns exactly that, a `stop_reason` field (plus
        # `is_error`/`subtype`). Adopting it would replace this whole parse path, so it is deferred,
        # not impossible. The emptiness check is text-mode-coupled for the same reason: a JSON
        # envelope is never empty, so under --output-format json a null `result` would sail through
        # this guard untouched.
        if not text:
            detail = self._scrub(proc.stderr).strip()[:200]
            raise BackendError(
                f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace"
                + (f"; stderr: {detail}" if detail else "") + ")"
            )
```

- [ ] **Step 6: Run the new behaviour tests AND the pre-existing claude-max tests**

Run: `python -m pytest tests/test_backends.py -v`
Expected: all pass — the five new behaviour tests PASS, and the three pre-existing tests (`test_claudemax_runner_nonzero_raises`, `test_claudemax_empty_stdout_on_exit_zero_raises`, `test_claudemax_transport_failure_raises_backend_error`) stay green (their `cmd_template=["claude"]` fixtures leave `host=""`/`claude_path="claude"`, both skipped by the guards).

- [ ] **Step 7: Commit**

```bash
git add sluice/core/backends.py tests/test_backends.py
git commit -m "fix(backends): scrub host and claude_path from stderr at the BackendError boundary (#41)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: Verification — ruff, full suite, and the seven-mutant witness sweep

Prove the tests are load-bearing, not inert. The implementation is already committed (Tasks 1-2), so mutants are restored via `Edit`, never `git checkout` (which would wipe uncommitted work — a documented past hazard).

**Files:** none created; this task runs commands and temporarily mutates `sluice/core/backends.py`, restoring each mutant.

- [ ] **Step 1: Lint and full suite**

Run: `ruff check sluice tests`
Expected: clean (0 findings).

Run: `python -m pytest -q`
Expected: suite passes with no failures; record the observed passed count (it should rise by the
number of tests this plan adds — do not hard-assert an exact total, which drifts as the suite grows).

- [ ] **Step 2: Content-address the bytecode caches (once, before witnessing)**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
Expected: no output (success). This makes `sluice/`'s `.pyc` content-addressed so a mutant cannot run stale bytecode and lie green.

- [ ] **Step 3: Run the seven mutation witnesses**

For each mutant: apply the `Edit` (MOVE/DELETE only), run the named test by node id, confirm it **FAILS (red)**, then restore with the inverse `Edit`. Do NOT run the whole suite — run only the named node so the witness is attributable.

| # | Mutation (delete/move in `sluice/core/backends.py`) | Named test — must go RED |
|---|---|---|
| 1 | `_scrub` wrap at invocation-failed → `f"...: {str(e)}"` | `tests/test_backends.py::test_claudemax_timeout_scrubs_host_and_path_from_message` |
| 2 | `_scrub` wrap at nonzero-exit → `{proc.stderr[:200]}` | `tests/test_backends.py::test_claudemax_nonzero_exit_scrubs_host_keeps_diagnostic` |
| 3 | drop the `+ (f"; stderr: {detail}" if detail else "")` append (revert to `"...whitespace)"`) | `tests/test_backends.py::test_claudemax_empty_response_regains_scrubbed_diagnostic` |
| 4 | **[folded]** drop the `_scrub` role exemption (`and self.claude_path != "claude"`) | `tests/test_backends.py::test_scrub_omits_default_claude_path` (`::test_scrub_redacts_host_named_claude` pins the complement) |
| 5 | **[folded c8b9b53]** token-aware `re.sub(...)` → plain `str.replace(value, label)` | `tests/test_backends.py::test_redact_short_host_not_matched_inside_a_word` |
| 6 | remove `sorted(..., reverse=True)` → iterate `secrets.items()` raw | `tests/test_backends.py::test_redact_overlap_scrubs_both_longest_first` |
| 7 | swap order at nonzero-exit → `self._scrub(proc.stderr[:200])` | `tests/test_backends.py::test_claudemax_redacts_before_truncating` |
| 8 | **[folded c8b9b53]** `from None` → `from e` at invocation-failed | `tests/test_backends.py::test_claudemax_timeout_chain_carries_no_secret` |
| 9 | **[folded c8b9b53]** swap order at empty-response → `self._scrub(proc.stderr[:200])...` | `tests/test_backends.py::test_claudemax_empty_response_redacts_before_truncating` |

Run per mutant, e.g.: `python -m pytest "tests/test_backends.py::test_claudemax_redacts_before_truncating" -q`
Expected: `1 failed` while mutated; restore, re-run, `1 passed`.

- [ ] **Step 4: Final green confirmation**

Run: `python -m pytest -q && ruff check sluice tests`
Expected: suite passes (no failures), ruff clean. Working tree contains only the two committed files' state (no leftover mutant).

- [ ] **Step 5: (No commit)** — Task 3 mutates and restores only; nothing new to commit. The witness results are recorded in the PR body / review notes, not in the tree.

---

## Self-review

- **Spec coverage:** `_redact` (spec §Design 1) → Task 1. `_scrub` + three raise sites + comment (§Design 2-4) → Task 2. Synthetic-fixture constraint, straddle-via-`complete()`, overlap shorter-key-first, timeout test, scoped guarantee (§Testing, all folded findings) → Tasks 1-2. Seven mutation witnesses + DoD (§Definition of done) → Task 3. No spec section unmapped.
- **Placeholder scan:** none — every code step shows complete code; every command shows expected output.
- **Type consistency:** `_redact(text, secrets)` and `_scrub(self, text)` signatures identical in plan and spec; labels `<host>`/`<path>`; node ids match the test names defined in Tasks 1-2.
- **Non-goals honoured:** no change to the other three backends, no config knob, no `--output-format json`, no general scrubber.
