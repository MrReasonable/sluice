# Terminal Output Safety Implementation Plan (#280)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Escape terminal-dangerous characters in untrusted scraped, model- and email-derived text at two chokepoints nothing can opt out of, so a scraped `\x1b` can no longer drive the operator's terminal.

**Architecture:** One pure policy function in `sluice/core/safeout.py`, applied at a stream wrapper installed in `cli.py::main()` (covering every `print`) and at an escaping `logging.Formatter` in `core/log.py` (covering every log record, which the stream wrapper cannot reach because a handler binds its stream at construction). Tab is untouched; newline is a documented residual.

**Tech Stack:** Python standard library only — `contextlib`, `logging`, `traceback`, `ast` for the guards. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-07-terminal-output-safety-design.md`

## Global Constraints

- **Standard library only in `sluice/`.** This change adds no dependency.
- **No personal data in `sluice/` or `tests/`.** Fixture control characters go in a `url` using a reserved domain (`example.invalid`), never in `company`/`title` — measured, `company: "Example Co\x1b"` collects as an identity and fails `tests/test_fixture_name_neutrality.py`.
- **Never cite a line number in a comment or docstring.** Cite `file.py::symbol`.
- **Conventional Commits** on every commit; a mistyped type changes what release-please ships.
- **Threat set:** C0 except `\n` and `\t`; DEL (`\x7f`); C1 (`\x80`-`\x9f`); lone surrogates (`\ud800`-`\udfff`); U+2028; U+2029.
- **`\t` and `\n` are NEVER escaped.** `\t` is not a terminal-control character and carries the `audit_flags`/`voice_flags` field contract; `\n` is indistinguishable from sluice's own formatting at the stream.
- **Guard mutants are made by MOVING or DELETING, never ADDING** — an added check leaves the original firing and the suite green.
- Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` **once** before any mutation step.

---

### Task 1: `core/safeout.py` — the pure policy

**Files:**
- Create: `sluice/core/safeout.py`
- Test: `tests/test_safeout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_control(ch: str) -> bool`; `hex_escape(ch: str) -> str`; `escape_for_terminal(text: str) -> str`. Task 2 imports the first two; Tasks 3 and 5 import the third.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_safeout.py
"""The terminal-output escaping policy (#280).

Rows here pin the FOUR properties the design calls load-bearing: the character set,
idempotence, statelessness, and the two characters deliberately left alone. Each is
falsifiable by a MOVE/DELETE mutant on `_TERMINAL_KEEP` or on `is_control`'s ranges.
"""
import pytest

from sluice.core.safeout import escape_for_terminal, hex_escape, is_control


@pytest.mark.parametrize("ch", [
    "\x00", "\x07", "\x08", "\x0b", "\x0c", "\r", "\x1b", "\x1f",   # C0 minus \n \t
    "\x7f",                                                          # DEL
    "\x80", "\x9b", "\x9f",                                          # C1, incl. CSI
    "\u2028", "\u2029",                                              # line separators
    "\ud800", "\udfff",                                              # lone surrogates
])
def test_every_threat_character_is_escaped(ch):
    out = escape_for_terminal(f"a{ch}b")
    assert ch not in out, f"{ch!r} survived escaping"
    assert out.startswith("a") and out.endswith("b")


@pytest.mark.parametrize("ch", ["\n", "\t"])
def test_newline_and_tab_pass_through_untouched(ch):
    """The stated residual (\\n) and the audit_flags/voice_flags field contract (\\t).

    Asserted so a later 'tightening' of the character set is a deliberate decision that
    reddens here, rather than an accident that silently breaks tab-separated columns.
    """
    assert escape_for_terminal(f"a{ch}b") == f"a{ch}b"


def test_escaping_is_idempotent():
    """A log record passes through BOTH the Formatter and the wrapped stream, so a second
    pass must change nothing. Holds because the escaped form is backslash, x/u and hex
    digits -- none in the threat set -- and because backslash is deliberately NOT escaped."""
    once = escape_for_terminal("title: Engineer\x1b[2J\x9b end")
    assert escape_for_terminal(once) == once


def test_escaping_is_stateless_per_chunk():
    """`print` calls `write()` twice -- once for the text, once for the newline -- so a
    line-oriented implementation would be broken by construction."""
    assert escape_for_terminal("a\x1b") + escape_for_terminal("\n") == \
        escape_for_terminal("a\x1b\n")


def test_hex_escape_uses_four_digits_above_one_byte():
    """`\\xNN` takes exactly two hex digits, so it cannot express U+2028: `\\x2028` reads
    back as `\\x20` followed by a literal '28'."""
    assert hex_escape("\x1b") == "\\x1b"
    assert hex_escape("\u2028") == "\\u2028"


def test_is_control_rejects_ordinary_text():
    assert not any(is_control(c) for c in "Senior Engineer (remote) - 45k+ / cafe")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_safeout.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.core.safeout'`

- [ ] **Step 3: Write the implementation**

```python
# sluice/core/safeout.py
r"""Escape characters that would drive the operator's TERMINAL rather than print to it (#280).

`sluice` prints scraped board text, LLM output about a composed CV, and Gmail message fields
verbatim. A terminal is not a display surface: a `\x1b` byte in that text is an instruction --
it can recolour, reposition the cursor, clear the screen, set the window title, and on a
permissive terminal write the clipboard via OSC 52.

The character class is `onboard/emit.py::_needs_hex`'s, which was MEASURED against a real
PyYAML parser rather than reasoned about; this module is now its one home and `emit.py` imports
from here. The terminal set is that set MINUS `\n` and `\t`:

- `\t` is not a terminal-control character. It advances to the next tab stop and can do nothing
  else -- it cannot recolour, reposition arbitrarily, hide output or reach the clipboard. It is
  also load-bearing: `cv/audit.py` and `cv/voice.py` emit tab-separated records and `cmd_cv_run`
  prints them with the tabs intact as columns. A blanket control strip would destroy that.
- `\n` is the one character a STREAM cannot judge. `cli.py` legitimately prints "\nNext:", and an
  injected newline inside a scraped title is byte-identical. Escaping it here would turn every
  deliberate blank line into a literal `\n`. The residual is stated in the design doc: an
  injected newline forges an output LINE, which is bounded -- it cannot hide prior output,
  recolour or reposition, all of which need CR or ESC and all of which this module closes.

Backslash is deliberately NOT escaped. YAML must round-trip so `emit.py` escapes it; terminal
output does not, and escaping it would mangle every path sluice prints. That choice is also what
makes `escape_for_terminal` IDEMPOTENT, which matters because a log record passes through both
the Formatter and the wrapped stream.
"""
import sys
import traceback
from contextlib import contextmanager

# Written as escapes, never as literals: U+2028/U+2029 are invisible in an editor, and a literal
# one actually SPLITS the source line -- Python treats it as a line break.
_TERMINAL_KEEP = ("\n", "\t")


def is_control(ch: str) -> bool:
    r"""Is `ch` a control character a reader is entitled to reject?

    C0 (< 0x20), DEL, the WHOLE C1 block (0x80-0x9f -- not just NEL at 0x85), lone surrogates
    (no valid encoding), and the two Unicode line separators. Moved here from
    `onboard/emit.py::_needs_hex`, whose docstring carries the measurement that produced it.
    """
    o = ord(ch)
    return (o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F or 0xD800 <= o <= 0xDFFF
            or ch in ("\u2028", "\u2029"))


def hex_escape(ch: str) -> str:
    r"""The narrowest escape form that can hold `ch`.

    `\xNN` takes exactly two hex digits, so it cannot express U+2028: `\x2028` reads back as
    `\x20` followed by a literal "28" -- a silent corruption, and the same class of bug as the
    raw character it was meant to fix. Measured, which is how the two-digit assumption was caught.
    """
    o = ord(ch)
    if o <= 0xFF:
        return f"\\x{o:02x}"
    return f"\\u{o:04x}" if o <= 0xFFFF else f"\\U{o:08x}"


def escape_for_terminal(text: str) -> str:
    r"""`text` with every threat-set character replaced by its `\xNN`/`\uNNNN` escape.

    ESCAPE rather than strip: stripping silently modifies the evidence an operator is looking
    at, and a scraped payload that contained an escape sequence is a fact worth seeing.
    """
    return "".join(
        hex_escape(ch) if is_control(ch) and ch not in _TERMINAL_KEEP else ch
        for ch in text
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_safeout.py -q`
Expected: PASS

- [ ] **Step 5: Witness the guard by DELETING, not adding**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Then delete `and ch not in _TERMINAL_KEEP` from `escape_for_terminal` and run
`.venv/bin/python -m pytest tests/test_safeout.py -q`.
Expected: `test_newline_and_tab_pass_through_untouched` FAILS for both params. Restore the code.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/safeout.py tests/test_safeout.py
git commit -m "feat(core): add the terminal output escaping policy"
```

---

### Task 2: Give the character class one home

**Files:**
- Modify: `sluice/onboard/emit.py` (delete `_needs_hex` and `_hex_escape`, import from core)
- Modify: `sluice/onboard/plan.py` (one docstring cites `emit._needs_hex`)

**Interfaces:**
- Consumes: `is_control`, `hex_escape` from Task 1.
- Produces: nothing new. `emit.py::scalar` behaviour is unchanged.

- [ ] **Step 1: Confirm the existing tests are green before touching anything**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py -q`
Expected: PASS. This suite is the regression check — `tests/test_onboard_emit.py::CONTROLS` spans
the whole predicate domain and loads every emission back through a real parser, so an unchanged
green IS the evidence the extraction is behaviour-identical. No new test is needed.

- [ ] **Step 2: Delete the two functions from `emit.py` and import them**

Replace the bodies of `_needs_hex` and `_hex_escape` in `sluice/onboard/emit.py` with an import
near the top of the module:

```python
from sluice.core.safeout import hex_escape, is_control
```

and change the one call site (`emit.py::scalar`, the line reading
`text = "".join(_hex_escape(ch) if _needs_hex(ch) else ch for ch in text)`) to:

```python
    text = "".join(hex_escape(ch) if is_control(ch) else ch for ch in text)
```

Use the public names rather than `import ... as _needs_hex`: an alias is exactly what defeats a
name-keyed sweep, which is a hazard this repo has already been bitten by.

Leave `_ESCAPES` where it is. It runs BEFORE the predicate, so `\n`, `\r` and `\t` never reach
`is_control` — which is why moving the predicate cannot change their handling.

- [ ] **Step 3: Update the docstring that cites the old symbol**

`sluice/onboard/plan.py` has a docstring reading "a control character `scalar()` hex-escapes
(`emit._needs_hex`)". Change `emit._needs_hex` to `safeout.is_control`.

Run: `grep -rn '_needs_hex\|_hex_escape' sluice/ tests/ --include='*.py'`
Expected: no output.

- [ ] **Step 4: Run the emit suite and the citation guard**

Run: `.venv/bin/python -m pytest tests/test_onboard_emit.py tests/test_citation_drift.py -q`
Expected: PASS — identical to Step 1.

- [ ] **Step 5: Commit**

```bash
git add sluice/onboard/emit.py sluice/onboard/plan.py
git commit -m "refactor(onboard): read the control-character class from core.safeout"
```

---

### Task 3: The stream wrapper and the `installed()` context manager

**Files:**
- Modify: `sluice/core/safeout.py`
- Test: `tests/test_safeout.py`

**Interfaces:**
- Consumes: `escape_for_terminal` from Task 1.
- Produces: `installed()` — a context manager that replaces `sys.stdout`/`sys.stderr` with escaping wrappers and restores them on every exit. Task 4 calls it.

**Design note the implementer must not "simplify" away:** the traceback is escaped by CATCHING
inside this context manager, not by installing a `sys.excepthook`. A hook installed and restored
alongside the wrapper is INERT — measured: the `finally` restores it during unwinding, before the
interpreter ever calls it, and the traceback then printed a live screen-clear.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_safeout.py
import io
import sys

import pytest

from sluice.core import safeout
from sluice.core.safeout import installed


def test_print_is_escaped_while_installed(capsys):
    with installed():
        print("title: Engineer\x1b[2J")
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\\x1b[2J" in out


def test_streams_are_restored_on_normal_exit():
    before = (sys.stdout, sys.stderr)
    with installed():
        assert sys.stdout is not before[0]
    assert (sys.stdout, sys.stderr) == before


def test_streams_are_restored_when_the_body_raises():
    before = (sys.stdout, sys.stderr)
    with pytest.raises(SystemExit):
        with installed():
            raise RuntimeError("boom")
    assert (sys.stdout, sys.stderr) == before


def test_an_uncaught_exception_prints_an_escaped_traceback_then_exits_one(capsys):
    """The hole round 1's own try/finally opened: `finally` runs during unwinding, so the
    streams are restored BEFORE the interpreter prints an uncaught traceback -- which then
    carries whatever scraped value is interpolated into the exception message straight to the
    terminal. Measured before the fix: a live ESC[2J screen clear."""
    with pytest.raises(SystemExit) as ei:
        with installed():
            raise RuntimeError("scraped title: Engineer\x1b[2J\x9b[31m")
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x9b" not in err
    assert "\\x1b[2J" in err and "\\x9b[31m" in err


def test_system_exit_passes_through_unchanged():
    """argparse raises SystemExit on --help and on a bad command. It carries no derived text
    and has established exit-code behaviour, so it must not be rewritten to SystemExit(1)."""
    with pytest.raises(SystemExit) as ei:
        with installed():
            raise SystemExit(2)
    assert ei.value.code == 2


def test_keyboard_interrupt_propagates_as_itself():
    """The row that falsifies the `except BaseException` comment. Ctrl-C carries no derived
    text and its exit behaviour is the shell's business, so it is re-raised rather than
    converted -- and swapping the guard to catch it would redden here."""
    with pytest.raises(KeyboardInterrupt):
        with installed():
            raise KeyboardInterrupt


def test_nesting_restores_the_outer_wrapper_not_the_original():
    original = sys.stdout
    with installed():
        outer = sys.stdout
        with installed():
            assert sys.stdout is not outer
        assert sys.stdout is outer
    assert sys.stdout is original


def test_the_wrapper_delegates_buffer_and_fileno():
    """`mcp`'s stdio transport calls `stream.buffer.fileno()`. With genuine delegation it dups
    fd 1 and serves the wire from a private binary duplicate, bypassing this wrapper entirely.
    WITHOUT `.buffer`, `_claim_fd`'s fallback `return stream.buffer, None` raises unguarded and
    `job-sluice mcp serve` dies at startup while the in-memory contract test stays green."""
    wrapped = safeout._Escaped(sys.__stdout__)
    assert wrapped.buffer is sys.__stdout__.buffer
    assert wrapped.fileno() == sys.__stdout__.fileno()
    assert wrapped.encoding == sys.__stdout__.encoding


def test_writelines_is_escaped_too():
    """`__getattr__` would delegate `writelines` straight to the inner stream, bypassing the
    escaping. It is overridden for that reason."""
    sink = io.StringIO()
    safeout._Escaped(sink).writelines(["a\x1b", "b\n"])
    assert sink.getvalue() == "a\\x1bb\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_safeout.py -q -k "installed or wrapper or escaped_traceback or delegates or writelines or nesting or restored or system_exit or keyboard"`
Expected: FAIL — `AttributeError: module 'sluice.core.safeout' has no attribute 'installed'`

- [ ] **Step 3: Write the implementation**

Append to `sluice/core/safeout.py`:

```python
class _Escaped:
    """A text stream that escapes the threat set on the way out.

    Delegation is by `__getattr__` and must stay GENUINE rather than a partial `TextIOBase`
    subclass: `mcp`'s stdio transport reaches through for `.buffer.fileno()`, and a wrapper
    without `.buffer` kills `job-sluice mcp serve` at startup (see `test_the_wrapper_delegates_
    buffer_and_fileno`). Nothing in `sluice/` itself reaches through -- measured with a
    must-be-present control -- so the requirement comes entirely from that third party.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(escape_for_terminal(text))
        # The PRE-escape length: callers that check a return value are asking how much of
        # THEIR string was accepted, not how many bytes the escaping happened to produce.
        return len(text)

    def writelines(self, lines) -> None:
        # Overridden rather than delegated: `__getattr__` would hand this to the inner stream
        # and every line would bypass the escaping.
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextmanager
def installed():
    r"""Escape everything printed to stdout/stderr for the duration, restoring on every exit.

    Covers every `print` site with no call-site change, so a print added later is protected by
    construction rather than by remembering to call a helper.

    The traceback is escaped HERE, by catching, rather than by a `sys.excepthook`. A hook
    installed and restored alongside the wrapper is inert: the `finally` restores it during
    unwinding, BEFORE the interpreter calls it, and the traceback then reaches the terminal raw.
    Measured -- a `RuntimeError` carrying a scraped title delivered a live screen-clear that way.
    An uncaught exception therefore becomes `SystemExit(1)`, which prints no second traceback.

    `SystemExit` and `KeyboardInterrupt` are re-raised untouched: neither carries derived text,
    and both have exit behaviour a caller depends on (argparse's `--help` is a `SystemExit(0)`).
    """
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Escaped(saved_out), _Escaped(saved_err)
    try:
        yield
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_safeout.py -q`
Expected: PASS

- [ ] **Step 5: Witness the traceback row by DELETING the escape path**

Delete the `except BaseException:` arm (both its lines) so the exception propagates. Run
`.venv/bin/python -m pytest tests/test_safeout.py -q`.
Expected: `test_an_uncaught_exception_prints_an_escaped_traceback_then_exits_one` FAILS.
Restore the code.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/safeout.py tests/test_safeout.py
git commit -m "feat(core): add the escaping stream wrapper and its install context manager"
```

---

### Task 4: Install it in `cli.py::main()`

**Files:**
- Modify: `sluice/cli.py` (`main`)
- Test: `tests/test_cli_output_safety.py`

**Interfaces:**
- Consumes: `installed()` from Task 3.
- Produces: nothing new; `main`'s signature and return codes are unchanged for every path except an uncaught non-`ValueError`, which now exits 1 via `SystemExit` instead of propagating.

**Verify the assumption first.** Only `SystemExit` is expected out of `main()` today — measured
by AST walk, 3 sites in 2 files, and the `pytest.raises(ValueError)` sites in
`tests/test_sluice_neutral_defaults.py` call `load_config()` directly, not `main()`. Re-run that
check before changing anything (Step 1); if a test expects some other exception from `main()`,
stop and report rather than editing the test.

- [ ] **Step 1: Re-derive the assumption**

Run:

```bash
.venv/bin/python - <<'EOF'
import ast, pathlib, collections
hits = collections.Counter()
for p in pathlib.Path("tests").rglob("*.py"):
    src = p.read_text(encoding="utf-8")
    if "main(" not in src:
        continue
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.With):
            for item in n.items:
                c = item.context_expr
                if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "raises" and c.args:
                    if "main(" in ast.unparse(n):
                        hits[ast.unparse(c.args[0])] += 1
print(dict(hits))
EOF
```

Expected: `{'SystemExit': 3}`. Any other key means stop and report.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_cli_output_safety.py
"""`cli.py::main` installs the output filter, and restores the streams on EVERY exit (#280).

Guard 4. `main()` has three exits, not the two the design first claimed: normal return, the
ValueError->exit-2 arm, and SystemExit out of `parse_args` (--help, a bad flag, a missing
subcommand). The SystemExit row is the one that matters -- measured, a correct try/finally AND a
broken restore-before-each-return both pass without it, while the broken one leaks the wrapper on
exactly that path.
"""
import sys

import pytest

from sluice import cli


def _streams():
    return (sys.stdout, sys.stderr)


def test_streams_are_restored_after_a_normal_command():
    """`ingest list-sources` is offline and needs no config or vault -- verified, exit 0."""
    before = _streams()
    assert cli.main(["ingest", "list-sources"]) == 0
    assert _streams() == before


def test_streams_are_restored_after_system_exit_from_argparse():
    before = _streams()
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert _streams() == before


def test_streams_are_restored_after_an_unknown_command():
    before = _streams()
    with pytest.raises(SystemExit):
        cli.main(["no-such-command"])
    assert _streams() == before


def test_streams_are_restored_after_the_value_error_arm(monkeypatch):
    """The `job-sluice: <message>` / exit 2 path."""
    def boom():
        raise ValueError("lead_ttl_days must be an int")
    monkeypatch.setattr(cli, "load_config", boom)
    before = _streams()
    assert cli.main(["doctor"]) == 2
    assert _streams() == before


def test_a_command_error_traceback_is_escaped(monkeypatch, capsys):
    """An uncaught non-ValueError carries whatever slug or scraped value the message
    interpolates -- sluice's own messages use %s, not %r -- so the traceback is the last
    unescaped path out of the process."""
    def boom():
        raise RuntimeError("lead: Engineer\x1b[2J\x9b[31m")
    monkeypatch.setattr(cli, "load_config", boom)
    before = _streams()
    with pytest.raises(SystemExit) as ei:
        cli.main(["doctor"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x9b" not in err
    assert "\\x1b[2J" in err
    assert _streams() == before
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_output_safety.py -q`
Expected: `test_a_command_error_traceback_is_escaped` FAILS (`RuntimeError` propagates instead of
`SystemExit`). The restore tests PASS already — nothing wraps a stream today, so they are green
from birth and only become meaningful once Step 4 lands. That is why the traceback row is the
witness for this task.

- [ ] **Step 4: Wire it into `main()`**

In `sluice/cli.py`, add to the module-scope imports (beside the existing config/logger imports,
which are deliberately eager):

```python
from sluice.core import safeout
```

Then wrap `main`'s body. The install must precede `_build_parser()`: `parse_args` raises
`SystemExit` on `--help` or a bad command, and `argcomplete.autocomplete(parser)` exits the
process outright when `_ARGCOMPLETE` is set — installing after either would leave those paths
unwrapped.

```python
def main(argv=None) -> int:
    # Installed BEFORE the parser is built: `parse_args` exits on --help or a bad command, and
    # `argcomplete.autocomplete` exits the process outright, so anything installed after them
    # would miss those paths entirely.
    with safeout.installed():
        parser = _build_parser()
        if argcomplete is not None:
            argcomplete.autocomplete(parser)
        args = parser.parse_args(argv)
        try:
            config = load_config()
            return args.func(args, config)
        except ValueError as exc:
            # (existing comment block preserved verbatim)
            print(f"job-sluice: {exc}", file=sys.stderr)
            return 2
```

Keep the existing `except ValueError` comment block exactly as it is — it records the #120
widening and is unrelated to this change.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_output_safety.py -q`
Expected: PASS

- [ ] **Step 6: Run the whole suite — this is the task most likely to break unrelated tests**

Run: `.venv/bin/python -m pytest -q`
Expected: same pass count as before the branch. If a test now fails on an exact-output assertion
containing a threat-set character, **update the expected string to its escaped form. Never narrow
a guard or relax an assertion to make it pass.**

- [ ] **Step 7: Commit**

```bash
git add sluice/cli.py tests/test_cli_output_safety.py
git commit -m "fix(cli): escape terminal control characters in all command output"
```

---

### Task 5: The logging Formatter

**Files:**
- Modify: `sluice/core/log.py` (`get_logger`)
- Test: `tests/test_log_output_safety.py`

**Interfaces:**
- Consumes: `escape_for_terminal` from Task 1.
- Produces: nothing new. `get_logger(name)` keeps its signature.

**Why a second chokepoint:** a Formatter escapes whatever stream its handler ends up holding; a
`main()`-scoped wrapper can only escape the stream object it replaced. `logging.StreamHandler`
binds its stream at construction and importing `sluice.cli` instantiates loggers before `main()`
runs, so those handlers hold the original stderr and the wrapper never sees their records.

**Mutation note — the binding decides the outcome.** Measured:

| handler stream bound | formatter deleted | wrapper deleted |
|---|---|---|
| **before** the wrapper (production) | raw ESC — **mutant killed** | survives |
| **after** (a logger created inside a test) | survives | survives |

So the Formatter's mutant must be witnessed on a handler holding an unwrapped stream. Two further
traps: `caplog` records `LogRecord.getMessage()`, the RAW message, so it cannot observe this
chokepoint at all; and `capsys` cannot see import-time loggers, whose handlers hold the pre-pytest
stderr. Use an explicit `StringIO` handler.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_log_output_safety.py
"""Log records are escaped by their Formatter, not by the stream wrapper (#280).

`sluice/core/vault.py`'s rename failure interpolates a lead slug with %s, and a slug can hold a
control character when a human placed the note by hand. Every `*log.<level>` call site in
`sluice/` is on this channel.
"""
import io
import logging

from sluice.core.log import get_logger


def _logger_writing_to(sink, name):
    """A logger writing to `sink`, keeping the formatter `get_logger` attached.

    Reusing the SHIPPED formatter is the point: building one here would pass even if
    `get_logger` attached nothing. Read it BEFORE swapping the handler -- the formatter lives
    on the handler, so stripping first leaves nothing to read.
    """
    log = get_logger(name)
    shipped = _shipped_formatter(log)
    log.handlers = [h for h in log.handlers if not isinstance(h, logging.StreamHandler)]
    handler = logging.StreamHandler(sink)
    handler.setFormatter(shipped)
    log.addHandler(handler)
    return log


def _shipped_formatter(log):
    for h in log.handlers:
        if h.formatter is not None:
            return h.formatter
    raise AssertionError(f"get_logger attached no formatter to {log.name}")


def test_a_log_record_is_escaped_by_its_formatter():
    sink = io.StringIO()
    log = _logger_writing_to(sink, "test_escape_probe")
    log.warning("could not rename %s -> %s", "lead\x1b[2J", "other\x9b[31m")
    out = sink.getvalue()
    assert "\x1b" not in out and "\x9b" not in out
    assert "\\x1b[2J" in out and "\\x9b[31m" in out


def test_tab_and_newline_survive_a_log_record():
    sink = io.StringIO()
    log = _logger_writing_to(sink, "test_keep_probe")
    log.warning("a\tb")
    assert "a\tb" in sink.getvalue()


def test_every_sluice_logger_with_a_handler_formats_through_the_escaper():
    """Guard 5. The roster is DERIVED -- a hand-written count was wrong once already -- but a
    derived roster needs a FLOOR, or a mis-keyed filter enumerates nothing and passes: measured,
    the `sluice.*` roster is EMPTY at test start and only populates once `sluice.cli` is
    imported. So assert the floor first, then the property.
    """
    import sluice.cli  # noqa: F401  -- what populates the roster

    from sluice.core.log import _EscapingFormatter

    named = {n: o for n, o in logging.root.manager.loggerDict.items()
             if n.startswith("sluice.") and isinstance(o, logging.Logger)}
    with_handlers = {n: o for n, o in named.items() if o.handlers}

    # The floor, hand-written: this set is not allowed to be empty, and this member is not
    # allowed to be missing. Without these two lines a broken filter is indistinguishable from
    # a clean sweep, because `all([])` is True.
    assert with_handlers, "enumerated no sluice loggers -- the filter is broken, not the tree"
    assert "sluice.cli" in with_handlers

    for name, log in with_handlers.items():
        for handler in log.handlers:
            assert isinstance(handler.formatter, _EscapingFormatter), (
                f"{name} formats through {type(handler.formatter).__name__}, "
                "so its records reach stderr unescaped")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_log_output_safety.py -q`
Expected: FAIL — `ImportError: cannot import name '_EscapingFormatter'`, and the escape rows fail
on the raw ESC.

- [ ] **Step 3: Write the implementation**

In `sluice/core/log.py`, add above `get_logger`:

```python
class _EscapingFormatter(logging.Formatter):
    """Escapes terminal control characters in the formatted record (#280).

    The second of two chokepoints, and it is not redundant with the stream wrapper
    `cli.py::main` installs. `logging.StreamHandler` binds its stream AT CONSTRUCTION, and
    importing `sluice.cli` instantiates loggers before `main()` runs -- so those handlers hold
    the ORIGINAL stderr and the wrapper never sees their records. A Formatter escapes whatever
    stream its handler ends up holding; a `main()`-scoped wrapper can only escape the stream
    object it replaced.

    Escaping the FORMATTED record, not the message, so the level, name and timestamp are
    covered too. Double escaping is harmless: `escape_for_terminal` is idempotent, which is what
    lets a record pass through both this and the wrapped stream unchanged.
    """

    def format(self, record: logging.LogRecord) -> str:
        return escape_for_terminal(super().format(record))
```

Add the import at the top of `core/log.py`:

```python
from sluice.core.safeout import escape_for_terminal
```

and change the one line in `get_logger` that reads
`handler.setFormatter(logging.Formatter(_LOG_FORMAT))` to:

```python
        handler.setFormatter(_EscapingFormatter(_LOG_FORMAT))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_log_output_safety.py -q`
Expected: PASS

- [ ] **Step 5: Witness the Formatter mutant, and check nothing pre-existing kills it**

Change `_EscapingFormatter(_LOG_FORMAT)` back to `logging.Formatter(_LOG_FORMAT)` and run:

```bash
.venv/bin/python -m pytest tests/test_log_output_safety.py -q
.venv/bin/python -m pytest tests/test_log.py tests/functional/test_cli_contract.py -q
```

Expected: the first FAILS on all three rows; the second still PASSES — confirming the new tests
are what catch this, not a pre-existing one. A mutation killed by an existing test witnesses
nothing about a new test. Restore the code.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/log.py tests/test_log_output_safety.py
git commit -m "fix(core): escape terminal control characters in log records"
```

---

### Task 6: Stop `apply prep --json` emitting raw C1

**Files:**
- Modify: `sluice/apply/packet.py` (`render_json`)
- Test: `tests/test_apply_packet.py` (extend)

**Interfaces:**
- Consumes: `escape_for_terminal` from Task 1 — the test applies the policy directly rather
  than driving `main()`, because what it asserts is a property of the ESCAPING, and a row
  that needed the whole CLI installed would be slower and no more conclusive.
- Produces: nothing new. `render_json(p) -> str` keeps its signature.

**Why this ships with the escaping and not separately:** `json.dumps(..., ensure_ascii=False)`
escapes C0 only. DEL, the whole C1 block — including `\x9b`, a direct CSI — and U+2028 pass
through raw. The wrapper then rewrites a raw `\x9b` to the four characters `\x9b` inside a JSON
string, and `\x` is not a legal JSON escape, so `json.loads` raises `Invalid \escape`. Without
this fix, installing the wrapper makes the documented machine-readable channel unparseable on one
scraped byte.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_apply_packet.py
import json

from sluice.apply import packet
from sluice.core.safeout import escape_for_terminal


def test_render_json_output_is_parseable_after_terminal_escaping():
    """`json.dumps(..., ensure_ascii=False)` leaves DEL, C1 and U+2028 raw -- JSON mandates
    escaping C0 only, which is why a probe using a lone ESC reports the whole class safe. The
    terminal filter then turns a raw C1 byte into a `\\x` sequence JSON has no escape for, and
    the documented machine-readable channel stops parsing on one scraped byte.

    The control characters sit in `role`, never in `company`: a control character in an identity
    field is collected by `tests/test_fixture_name_neutrality.py` and fails its roster check.
    """
    p = {"company": "Example Co", "role": "Engineer\x9b[2J\x7f\u2028",
         "location": "", "salary": "", "url": "", "listing_host": "", "cv_path": ""}
    json.loads(escape_for_terminal(packet.render_json(p)))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_apply_packet.py::test_render_json_output_is_parseable_after_terminal_escaping -q`
Expected: FAIL — `json.decoder.JSONDecodeError: Invalid \escape`

- [ ] **Step 3: Drop `ensure_ascii=False`**

In `sluice/apply/packet.py`, change `render_json`:

```python
def render_json(p):
    # DEFAULT ensure_ascii, deliberately (#280). At `ensure_ascii=False` this emitted raw DEL,
    # C1 and U+2028 -- JSON mandates escaping C0 only -- and the terminal filter `cli.py::main`
    # installs would then rewrite a raw \x9b to a `\x` sequence JSON cannot parse, making the
    # documented machine-readable channel unparseable on one scraped byte. The visible cost is
    # non-ASCII emitted as \uNNNN, which is still valid JSON and still round-trips.
    return json.dumps(p)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_apply_packet.py -q`
Expected: PASS, including the pre-existing `test_render_json_roundtrips` — it asserts the round
trip rather than the byte form, so it holds either way.

- [ ] **Step 5: Add the sink sweep (guard 8)**

```python
# tests/test_json_sink_scope.py
"""No `ensure_ascii=False` call site may be on a print path (#280).

The design's prose roster of these sites was written by hand twice and was wrong in BOTH
directions both times -- it named a site that is at the default and omitted one whose call is
`json.dump` rather than `json.dumps`, which the roster's own grep could not match. This sweep
is what makes the claim safe to rely on.
"""
import ast
import pathlib

# Hand-written TARGET. The probe alphabet below is derived by walking the AST; this value is
# not, deliberately -- a target derived from the same matcher cannot see a deletion.
EXPECTED_SINKS = {
    # (path, INNERMOST enclosing function) -> what it writes to, and why that is not a terminal.
    # Derived once by running the sweep, then hand-written here. Note `core/vault.py`'s site sits
    # in a nested `transform`, which is why attribution must be to the innermost function: walking
    # every enclosing FunctionDef reports one site twice and the target silently stops matching.
    ("sluice/core/dossier.py", "get_or_build"): "the dossier cache file",
    ("sluice/core/vault.py", "transform"): "note frontmatter",
    ("sluice/triage/audit.py", "append"): "the audit JSONL file",
    ("sluice/triage/judge.py", "_build_prompt"): "the judge prompt",
}


def _sites():
    """Every `ensure_ascii=False` call site, keyed by its INNERMOST enclosing function."""
    found = set()
    for path in sorted(pathlib.Path("sluice").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node.name
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg == "ensure_ascii" and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False):
                        found.add((str(path), fn))
            for child in ast.iter_child_nodes(node):
                visit(child, fn)

        visit(tree, "<module>")
    return found


def test_the_sweep_found_the_sites_it_meant_to():
    """Anti-vacuity: a broken walk enumerates nothing and every assertion over it passes."""
    assert len(_sites()) == len(EXPECTED_SINKS), (
        "the sweep enumerated a different number of sites than the hand-written target -- "
        "check the walk before editing the target")


def test_no_ensure_ascii_false_site_is_on_a_print_path():
    assert _sites() == set(EXPECTED_SINKS), (
        "an ensure_ascii=False call site was added or moved. If it writes to a FILE or a PROMPT, "
        "add it to EXPECTED_SINKS with a note saying which. If it can reach a stream, it must "
        "not use ensure_ascii=False -- see sluice/apply/packet.py::render_json.")
```

`EXPECTED_SINKS` above is the real roster, derived at `3ae9021a` and hand-written here — after
Task 6 Step 3 removes `render_json`'s, four sites remain. Verify the guard can fail: temporarily add `ensure_ascii=False` to a `json.dumps` in
`cli.py`, run the test, confirm it reddens, and remove it.

- [ ] **Step 6: Run and commit**

```bash
.venv/bin/python -m pytest tests/test_apply_packet.py tests/test_json_sink_scope.py -q
git add sluice/apply/packet.py tests/test_apply_packet.py tests/test_json_sink_scope.py
git commit -m "fix(apply): emit ASCII-safe JSON so terminal escaping cannot corrupt it"
```

---

### Task 7: The two repo-wide sweeps

**Files:**
- Test: `tests/test_output_safety_sweeps.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

Both guards are **green from birth** — nothing wraps a stream today and `sluice/` holds no raw
control character. Neither can ever have been observed to fail against the real tree, so each
carries an in-test positive control, which is the only thing that proves its scanner fires.
Say so in the test rather than implying it was seen to fail.

- [ ] **Step 1: Write the guards**

```python
# tests/test_output_safety_sweeps.py
"""Two repo-wide sweeps supporting the terminal-escaping policy (#280).

BOTH are green against the tree from the moment they are written -- there is nothing to find
today. That makes the in-test positive control the load-bearing part: it is the only thing that
distinguishes 'clean' from 'the scanner never ran'. A negative guard whose matcher breaks
enumerates nothing and passes every assertion over it, because `all([])` is True.
"""
import ast
import pathlib

# The SAME class `core/safeout.py::is_control` uses, minus the two characters the policy
# deliberately keeps. Written as a separate expression rather than importing `is_control`:
# a guard that derives its target from the code under test cannot see that code narrowing.
def THREAT(o):
    return ((o < 0x20 and o not in (0x09, 0x0A)) or o == 0x7F or 0x80 <= o <= 0x9F
            or 0xD800 <= o <= 0xDFFF or o in (0x2028, 0x2029))


def _scan(text):
    return [hex(ord(c)) for c in text if THREAT(ord(c))]


def test_the_byte_scanner_fires():
    """Positive control. Without this row a broken scanner reports a clean tree."""
    assert _scan("a\x1bb") == ["0x1b"]
    assert _scan("a\u2028b") == ["0x2028"]   # the class is wider than C0
    assert _scan("plain text\n\tand a tab") == []


def test_no_literal_control_character_in_sluice_source():
    """Keeps the policy's premise true over time: sluice never legitimately EMITS these, so a
    stream-level filter over the threat set has no false positives.

    A byte scan, not a grep: a regex over file bytes is the wrong engine for a question about
    bytes, and this repo has twice been bitten by a sweep that silently under-reported.
    """
    files = sorted(pathlib.Path("sluice").rglob("*.py"))
    assert len(files) > 50, "the walk found almost nothing -- it is broken, not the tree"
    offenders = {}
    for path in files:
        found = _scan(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path)] = found
    assert not offenders, f"raw control characters in source: {offenders}"


def _module_scope_captures():
    """Names bound at MODULE scope from a stream or a stream-holding constructor.

    A SYNTACTIC proxy for 'captured before `cli.py::main` installs the wrapper', and the proxy
    is knowingly incomplete: a stream captured inside a function that RUNS at import time is
    invisible to it. `core/log.py::get_logger` is exactly that case, and it is covered by the
    Formatter chokepoint rather than by this sweep -- which is why the Formatter is not
    redundant with the wrapper.
    """
    hits = []
    for path in sorted(pathlib.Path("sluice").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                      # module scope only, deliberately
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, "id", "") or getattr(inner.func, "attr", "")
                    if name in ("TtyAsker", "StreamHandler"):
                        hits.append(f"{path}: module-scope {name}(...)")
                if (isinstance(inner, ast.Attribute) and inner.attr in ("stdout", "stderr")
                        and getattr(inner.value, "id", "") == "sys"):
                    hits.append(f"{path}: module-scope sys.{inner.attr}")
    return hits


def test_the_bypass_sweep_fires(tmp_path):
    """Positive control, run against a synthetic module rather than the tree."""
    probe = tmp_path / "probe.py"
    probe.write_text("import sys\nOUT = sys.stdout\n", encoding="utf-8")
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    found = [n for node in tree.body for n in ast.walk(node)
             if isinstance(n, ast.Attribute) and n.attr == "stdout"]
    assert found, "the AST probe does not detect a module-scope sys.stdout capture"


def test_nothing_captures_a_stream_before_the_wrapper_is_installed():
    """Hand-written target: empty. A stream captured at module scope keeps the ORIGINAL, so
    every write through it would bypass the filter `cli.py::main` installs."""
    assert _module_scope_captures() == []
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_output_safety_sweeps.py -q`
Expected: PASS (all four rows).

- [ ] **Step 3: Verify each sweep can actually fail**

Temporarily add `_PROBE = sys.stdout` at module scope in `sluice/cli.py` and run the file:
`test_nothing_captures_a_stream_before_the_wrapper_is_installed` must FAIL. Remove it.

Temporarily paste a real ESC byte into a string in any `sluice/` module and run the file:
`test_no_literal_control_character_in_sluice_source` must FAIL. Remove it.

- [ ] **Step 4: Commit**

```bash
git add tests/test_output_safety_sweeps.py
git commit -m "test(core): sweep for raw control characters and pre-install stream captures"
```

---

### Task 8: Prove `mcp serve` still serves through the wrapper

**Files:**
- Test: `tests/functional/test_mcp_stdio_smoke.py`

**Interfaces:**
- Consumes: the wrapper installed by Task 4.
- Produces: nothing.

**Why this exists and why the obvious test does not cover it.** `job-sluice mcp serve` is
dispatched by `main()`, so the wrapper is installed before the stdio transport starts. Read in the
installed `mcp/server/stdio.py`, the transport does
`_claim_fd(1, sys.stdout, "wb", ...)` -> `_is_backed_by_fd(stream, 1)` -> `stream.buffer.fileno()`.
With genuine delegation that returns 1, mcp dups fd 1 and serves the wire from a private binary
duplicate — bypassing the text wrapper entirely. **Without `.buffer`, `_is_backed_by_fd` swallows
the `AttributeError` and returns False, and `_claim_fd`'s fallback line `return stream.buffer, None`
then touches `.buffer` again UNGUARDED — so `mcp serve` dies at startup.**

`tests/functional/test_mcp_contract.py` cannot see any of this: its own docstring says "No
subprocess, no stdio, no network" and every test drives `build_server(...)` directly, so
`sys.stdout` is never wrapped. It is green either way. End-to-end rather than liveness-only,
because `pyproject.toml` pins the runtime extra as a RANGE (`mcp>=2.0.0,<3`) while only the test
extra pins a version — the dup-and-divert mechanism is a third-party detail this repo does not
control, so assert the OBSERVABLE property (a frame round-trips) rather than the mechanism.

- [ ] **Step 1: Write the failing test**

```python
# tests/functional/test_mcp_stdio_smoke.py
"""`job-sluice mcp serve` still serves real frames with the output filter installed (#280).

Deliberately a SUBPROCESS over real stdio. The in-memory contract test never wraps `sys.stdout`,
so it is green whether or not the wrapper delegates `.buffer` -- and a wrapper that does not
delegate kills `mcp serve` at startup.

The subprocess INHERITS the ambient environment on purpose -- no `env=` dict. `tests/conftest.py`
sandboxes `SLUICE_CONFIG`, `XDG_*`, `HOME` and `VAULT_DIR` via `monkeypatch.setenv`, which mutates
`os.environ` and so reaches the child. Passing an explicit `env=` would escape that sandbox and let
the child read a developer's real config and vault.
"""
import json
import subprocess
import sys

import pytest

pytest.importorskip("mcp")


def test_mcp_serve_starts_and_answers_over_real_stdio(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "sluice.cli", "mcp", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,          # no env= : inherit the conftest-sandboxed environment
    )
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "smoke", "version": "0"}}}
    try:
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
    finally:
        proc.kill()
        proc.wait(timeout=10)

    assert line, (
        "mcp serve produced no frame. If the wrapper stopped delegating `.buffer`, "
        "`_claim_fd`'s fallback raises AttributeError and the server dies at startup: "
        f"stderr={proc.stderr.read()[:400]!r}")
    assert json.loads(line)["id"] == 1
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/functional/test_mcp_stdio_smoke.py -q`
Expected: PASS. It is **green from birth** — it passes both before and after Task 4, because a
correctly delegating wrapper is bypassed and no wrapper at all is equally fine. Its value is as a
regression guard on the delegation, so witness it rather than trusting it (Step 3).

- [ ] **Step 3: Witness it by BREAKING the delegation**

In `sluice/core/safeout.py`, temporarily replace `_Escaped.__getattr__`'s body with
`raise AttributeError(name)`. Run the test.
Expected: FAIL — no frame, and the assertion message quotes the child's `AttributeError`.
Restore `__getattr__`.

This is the only step that distinguishes a wrapper that delegates from one that does not, so do
not skip it: without it the test is green in both worlds and reads as coverage it does not have.

- [ ] **Step 4: Commit**

```bash
git add tests/functional/test_mcp_stdio_smoke.py
git commit -m "test(mcp): drive mcp serve over real stdio with the output filter installed"
```

---

### Task 9: Update everything that now asserts something false

**Files:**
- Modify: `sluice/cli.py` (the `Sanitising THIS loop alone` comment in `cmd_cv_run`)
- Modify: `docs/ARCHITECTURE.md`
- Modify: `.rulesync/rules/CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Rewrite the comment that is now false**

Find it: `grep -n 'Sanitising THIS loop alone' sluice/cli.py`. It currently asserts *"nothing in
sluice sanitises terminal output at any site"* and that sanitising one loop *"would leave the
others raw while reading as coverage"*. Both are now false. Replace that portion with:

```python
        # ESCAPED, at the stream rather than here (#280). These lines embed a raw slice of the
        # composed CV -- LLM output derived from an attacker-controlled job description, which
        # `mcpserver.py` classifies as UNTRUSTED DERIVED CONTENT -- so a terminal control
        # sequence that survived into a CV would otherwise reach the operator's terminal
        # verbatim. `cli.py::main` installs `core/safeout.py`'s filter over stdout and stderr for
        # the whole invocation, so no print site opts in and none can opt out.
        #
        # TAB is deliberately NOT escaped, which is what keeps `audit_flags`
        # ("<verdict>\t<claim>\t<cited-id>") and `voice_flags` ("flag\t<phrase>\t<why>") readable
        # as columns here: a tab advances to the next tab stop and cannot recolour, reposition,
        # hide output or reach the clipboard. NEWLINE is not escaped either, and that one IS a
        # residual: an injected newline forges an extra output line. It is bounded -- hiding or
        # repositioning needs CR or ESC, both of which are escaped -- and only the call site
        # could tell an injected newline from this file's own formatting.
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`**

Two edits:

1. In the `core/` module enumeration, add `safeout.py` to the existing catch-all bullet that
   already names `log.py` (it reads as a list of the small modules). Describe it as "the terminal
   output escaping policy: the control-character class, and the stream wrapper `cli.py::main`
   installs."
2. In the section describing `cli.py`, record that `main()` installs a process-wide output filter
   for the whole invocation **and** that `core/log.py::get_logger` carries a second chokepoint for
   log records. Documenting only the wrapper would give a one-chokepoint page for a
   two-chokepoint design, which is its own drift. State the `\n` residual in one sentence.

Note `core/stem.py` is already absent from that page. Adding it in the same pass is optional and
unrelated to #280 — do it or not, but do not describe `safeout.py` as the page's first omission.

- [ ] **Step 3: Update `.rulesync/rules/CLAUDE.md` and regenerate**

Add a short paragraph to the Invariants section: the policy, the two chokepoints, that `\t` and
`\n` are never escaped and why, and that `render_json` must not go back to `ensure_ascii=False`.
This is the operating manual every agent reads, and the `\t`/`\n` distinction is exactly what a
later change would otherwise undo by accident.

Then regenerate — `.rulesync/` is canonical and its outputs are gitignored:

```bash
npm ci --ignore-scripts && npm run rulesync
```

- [ ] **Step 4: Full verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check sluice tests scripts
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q
```

The third run is not redundant: this machine has tools installed that CI does not, and four tests
once passed here while failing on all three CI Pythons for exactly that reason.

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "docs(core): record the terminal output escaping policy and its residuals"
```

---

## Definition of done

- `.venv/bin/python -m pytest -q` passes, with the same count as `main` plus the new rows.
- `.venv/bin/ruff check sluice tests scripts` is clean.
- `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q` passes.
- `grep -rn '_needs_hex\|_hex_escape' sluice/ tests/` returns nothing.
- `grep -n 'Sanitising THIS loop alone' sluice/cli.py` returns nothing.
- Every guard has been witnessed by a MOVE/DELETE mutant, or carries an in-test positive control
  and says in its docstring that it is green from birth.
