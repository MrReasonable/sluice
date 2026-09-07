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
        for node in tree.body:
            # Skip defs and classes BEFORE walking. `ast.walk` on a top-level FunctionDef
            # descends into its body, so without this the sweep reports every `file=sys.stderr`
            # in the tree -- measured, 93 hits, and the `== []` target could never pass. A
            # capture inside a def happens when that def RUNS, which is the case this sweep
            # documents as out of scope.
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
