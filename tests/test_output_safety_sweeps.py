"""Repo-wide sweeps supporting the terminal-escaping policy (#280).

Every sweep here is green against the tree from the moment it is written -- there is nothing to
find today. That makes the in-test positive control the load-bearing part: it is the only thing
that distinguishes 'clean' from 'the scanner never ran'. A negative guard whose matcher breaks
enumerates nothing and passes every assertion over it, because `all([])` is True.
"""
import ast
import pathlib

import sluice

# Anchored on the installed package, not the cwd `pathlib.Path("sluice")` a bare relative walk
# would use: from any cwd other than the repo root that resolves to zero files, and a sweep with
# no floor over it passes having enumerated nothing (measured from /tmp -- see the review finding
# behind `test_nothing_captures_a_stream_before_the_wrapper_is_installed` below).
_PKG = pathlib.Path(sluice.__file__).resolve().parent


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


def _module_scope_captures(root=_PKG):
    """Names bound at MODULE scope from a stream or a stream-holding constructor.

    A SYNTACTIC proxy for 'captured before `cli.py::main` installs the wrapper', and the proxy
    is knowingly incomplete: a stream captured inside a function that RUNS at import time is
    invisible to it. `core/log.py::get_logger` is exactly that case, and it is covered by the
    Formatter chokepoint rather than by this sweep -- which is why the Formatter is not
    redundant with the wrapper.

    `root` defaults to the real tree (`_PKG`, anchored on `sluice.__file__` rather than the cwd);
    the positive control below passes a `tmp_path` holding a synthetic module instead, so it
    exercises this exact matcher rather than a copy of part of it.
    """
    hits = []
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            # Skip defs and classes BEFORE walking. `ast.walk` on a top-level FunctionDef
            # descends into its body, so without this the sweep reports every `file=sys.stderr`
            # in the tree -- a three-figure hit count against a target of `[]`, so it could
            # never pass. A capture inside a def happens when that def RUNS, which is the case
            # this sweep documents as out of scope.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
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
    """Positive control, run against a synthetic module rather than the tree -- and through the
    real matcher (`_module_scope_captures`), not a reimplementation of part of it. A control that
    hand-rolls its own ad hoc AST check can stay green while the real matcher's own arm is
    deleted; calling the function under test is what makes that impossible.

    Plants BOTH arms `_module_scope_captures` matches, in one synthetic module: a module-scope
    `sys.stdout` attribute capture, and module-scope `TtyAsker(...)`/`StreamHandler(...)` calls.
    Each is asserted individually, so deleting either arm of the matcher reddens this control.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "OUT = sys.stdout\n"
        "ASKER = TtyAsker()\n"
        "HANDLER = StreamHandler()\n",
        encoding="utf-8",
    )
    found = _module_scope_captures(tmp_path)
    assert any("module-scope sys.stdout" in hit for hit in found), (
        f"the sys.stdout attribute arm did not fire: {found}"
    )
    assert any("module-scope TtyAsker(...)" in hit for hit in found), (
        f"the TtyAsker call arm did not fire: {found}"
    )
    assert any("module-scope StreamHandler(...)" in hit for hit in found), (
        f"the StreamHandler call arm did not fire: {found}"
    )


def test_nothing_captures_a_stream_before_the_wrapper_is_installed():
    """Hand-written target: empty. A stream captured at module scope keeps the ORIGINAL, so
    every write through it would bypass the filter `cli.py::main` installs.

    Asserts its own scope floor rather than trusting `_module_scope_captures`'s default: a
    cwd-relative walk with no floor passes vacuously from any cwd but the repo root (measured
    from /tmp -- zero files enumerated, `== []` trivially true), which is exactly the shape its
    two sibling sweeps in this file avoid.
    """
    files = sorted(_PKG.rglob("*.py"))
    assert len(files) > 50, "the walk found almost nothing -- it is broken, not the tree"
    assert _module_scope_captures() == []


def _unescaped_input_calls(root=_PKG):
    """Every `input(...)` call in `root` whose prompt argument is not provably escaped.

    `input()`'s prompt is written by CPython's C-level `PyOS_Readline` path when stdin/stdout
    report tty file descriptors -- bypassing `core/safeout.py::_Escaped.write` entirely, unlike
    `print`/logging, which the installed wrapper covers by construction (#280 IMPORTANT 1). So an
    `input(...)` call must escape its OWN argument; nothing upstream can do it for the call site.

    'Provably escaped' is narrow on purpose: a plain string literal (`ast.Constant` holding a
    `str`, which cannot carry an interpolated value), or a call whose outermost function is named
    `escape_for_terminal` -- matched by NAME (`Name.id` for a bare import, `Attribute.attr` for
    `safeout.escape_for_terminal(...)`), the same matching style `_module_scope_captures` uses
    above and for the identical reason: a hand-listed import path loses to an alias, a name does
    not. Anything else -- an f-string, `.format()`, `%`, string concatenation, a bare variable --
    is flagged, because each of those can carry a value this sweep cannot trace back to a source,
    and the cost of a false positive (rewrap a genuinely-safe prompt) is far below the cost of a
    false negative (a live escape sequence at a real tty).
    """
    hits = []
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "input"):
                continue
            if not node.args:
                continue  # a bare input() with no prompt carries nothing to escape
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                continue
            if isinstance(arg, ast.Call):
                name = getattr(arg.func, "id", "") or getattr(arg.func, "attr", "")
                if name == "escape_for_terminal":
                    continue
            hits.append(f"{path}: input(...) with an unescaped prompt")
    return hits


def test_the_input_sweep_fires(tmp_path):
    """Positive control, run through the real matcher against a synthetic module. Plants one
    unsafe shape (an f-string prompt, the shape `cmd_cv_signoff`'s prompt actually was before the
    #280 IMPORTANT-1 fix) beside two safe ones -- a plain literal and an `escape_for_terminal`-
    wrapped prompt -- so the matcher's negative case is exercised too, not only its positive one.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def unsafe(slug):\n"
        "    return input(f'sign off {slug}? ')\n"
        "def safe_literal():\n"
        "    return input('continue? ')\n"
        "def safe_escaped(slug):\n"
        "    return input(escape_for_terminal(f'sign off {slug}? '))\n"
        "def safe_aliased(slug):\n"
        "    return input(safeout.escape_for_terminal(f'sign off {slug}? '))\n"
        "def safe_no_prompt():\n"
        "    return input()\n",
        encoding="utf-8",
    )
    found = _unescaped_input_calls(tmp_path)
    assert found == [f"{probe}: input(...) with an unescaped prompt"], found


def test_no_input_call_in_sluice_interpolates_an_unescaped_value():
    """Hand-written target: empty. `cli.py::cmd_cv_signoff` is the only `input()` in `sluice/`
    (grep `\\binput(` confirms it), reachable from a scraped company string via the note slug it
    prompts with, and its prompt is wrapped in `safeout.escape_for_terminal` for exactly that
    reason -- see the IMPORTANT-1 comment beside that call.
    """
    files = sorted(_PKG.rglob("*.py"))
    assert len(files) > 50, "the walk found almost nothing -- it is broken, not the tree"
    assert _unescaped_input_calls() == []
