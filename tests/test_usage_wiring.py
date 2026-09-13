"""Every LLM call site is accounted for, and every stage label is declared (#308).

WHAT THIS GUARD IS FOR. `core/usage.py::meter` records what a call cost, and a call site
nobody wrapped is invisible in `job-sluice usage` -- silently, because a missing row and a
free call look identical in the report. There is no runtime signal for it at all: the run
succeeds, the number is just smaller than the truth, in the direction that flatters.

WHAT IT CANNOT CHECK, stated first so it is not mistaken for more than it is. Whether the
backend that reaches a given `.complete(` was actually metered is a DATAFLOW question --
`meter` wraps where a backend is HANDED to a stage, which is a different module from where
the call happens, and often a different sub-app. So "every module holding a `.complete(`
also calls `meter`" is FALSE BY DESIGN here and would be the wrong assertion: the metering
for `cv/compose.py::compose` lives in `cv/engine.py`, and for `triage/judge.py::judge` in
`core/app.py`. The first draft of this guard asserted exactly that and would have had to be
narrowed until it checked nothing.

What it checks instead is both ENDS of the roster, each against a hand-written target:

  1. every `.complete(` call site in `sluice/`, counted per (module, innermost function), is
     named in `_CALL_SITES` together with the stage that meters it and HOW MANY calls that
     function holds -- or is exempted there with a stated reason. Counted, because a set lets
     a second call inside an already-declared function pass unnoticed.
  2. every stage literal passed to `meter(...)` in `sluice/` is in `_STAGES`, with `meter`'s
     local bindings derived from each module's own imports rather than assumed to be the bare
     name -- an aliased or attribute-qualified call is invisible to a name-keyed sweep.
  3. the two agree: each declared stage meters at least one declared call site, and each
     metered call site names a stage that some `meter(...)` actually passes.

A NEW `.complete(` is then red until someone writes down which stage covers it, which is the
review step -- and the case that produced this file is exactly that: a `.complete(` in
`core/app.py::doctor` that a COMMIT MESSAGE claimed was metered and was not. Prose asserted
the mechanism; nothing executable did.

The targets are HAND-WRITTEN and the probes derived, never the reverse. A roster derived from
the thing it checks compares the code against itself: it would sweep fewer sites after a
deletion and stay green, which is the failure mode this repo has hit before.

The RUNTIME half -- that a real run actually writes rows under these stages -- is
`tests/e2e/test_a_run_reports_what_it_spent.py` and the per-stage assertions named in
`_STAGES` below. Static and runtime are complements: this file proves the wiring is declared,
those prove it fires.
"""
import ast
import pathlib
from collections import Counter

import pytest

_SLUICE = pathlib.Path(__file__).resolve().parent.parent / "sluice"

# Every stage label `meter(...)` is called with, hand-written. Each maps to how its firing is
# witnessed at RUNTIME, so a stage cannot be declared here without somewhere that proves it
# actually records.
_STAGES = {
    "triage-judge": "tests/e2e/test_a_run_reports_what_it_spent.py",
    "triage-resolve": "tests/test_app_operations.py"
                      "::test_triage_threads_the_resolve_backend_into_engine_run",
    "cv-compose": "tests/e2e/test_a_run_reports_what_it_spent.py",
    "cv-audit": "tests/e2e/test_a_run_reports_what_it_spent.py",
    "cv-voice": "tests/test_cv_engine.py::test_the_voice_check_call_is_metered_as_its_own_stage",
    "track-classify": "tests/e2e/test_a_run_reports_what_it_spent.py",
    "doctor-probe": "tests/test_doctor.py::test_the_live_probe_records_its_own_spend",
}

# Every `.complete(` call site in `sluice/`, keyed (module-relative path, innermost enclosing
# function) -> the stage whose `meter(...)` covers it, or None with a reason below.
#
# A None row is not "an unmetered call site we tolerate" -- it is the plumbing metering is built
# out of, and wrapping it would double-count or recurse. Deliberately not counted here: an
# earlier version of this comment said "three" when there were two.
#
#   core/backends.py::complete  x2  FallbackBackend delegating to its own two legs. The wrapper
#                                   sits OUTSIDE it, so the leg's call is the same call already
#                                   counted; metering here would record every fallback twice.
#   core/usage.py::complete         MeteredBackend itself, delegating inward. Metering it would
#                                   be the recursion.
#
# The `x2` above is not this comment's claim: it is in the target below as a NUMBER the guard
# compares, because a set-keyed roster lets a second call inside an already-declared function
# pass unnoticed (see `_complete_call_sites`).
_CALL_SITES = {
    ("core/app.py", "doctor"): ("doctor-probe", 1),
    ("core/backends.py", "complete"): (None, 2),
    ("core/usage.py", "complete"): (None, 1),
    ("cv/audit.py", "run_audit"): ("cv-audit", 1),
    ("cv/compose.py", "compose"): ("cv-compose", 1),
    ("cv/voice.py", "run_voice"): ("cv-voice", 1),
    ("track/classify.py", "classify"): ("track-classify", 1),
    ("triage/judge.py", "judge"): ("triage-judge", 1),
    ("triage/resolve.py", "resolve_company"): ("triage-resolve", 1),
}


def _walk(fn):
    """Apply `fn(node, enclosing_function_name)` over every node under `sluice/`.

    Attribution is to the INNERMOST enclosing function, which is what makes a site's key
    stable: walking every enclosing `FunctionDef` instead reports one site under several
    names, and the target then silently stops matching. `tests/test_json_sink_scope.py` learnt
    the same lesson on the same shape.
    """
    for path in sorted(_SLUICE.rglob("*.py")):
        rel = path.relative_to(_SLUICE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, enclosing):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = node.name
            fn(rel, node, enclosing)
            for child in ast.iter_child_nodes(node):
                visit(child, enclosing)

        visit(tree, "<module>")


def _complete_call_sites():
    """Every `<something>.complete(...)` under `sluice/`, as a Counter of (module, function).

    Matched on the ATTRIBUTE name, so it finds the call however the receiver is spelled --
    `backend.complete`, `self.inner.complete`, `resolve_backend.complete`. A sweep keyed on a
    receiver NAME would walk straight past the next one someone invents, which is this repo's
    documented hand-listing hazard.

    A COUNTER, not a set, and that is load-bearing rather than tidy. Keyed as a set, a SECOND
    `.complete(` added inside an already-declared function collapses into the existing key and
    the roster stays green -- and the function it would most plausibly be added to is
    `core/app.py::doctor`, the exact site whose unmetered call is why this file exists. Measured
    against a set: adding one there changed nothing. This is the repo's #304 lesson (a set
    absorbs an addition that shares a key; `Counter` equality catches add, remove AND rename).
    """
    found = Counter()

    def check(rel, node, enclosing):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "complete"):
            found[(rel, enclosing)] += 1

    _walk(check)
    return found


def _meter_bindings(tree):
    """The names `core/usage.py::meter` is bound to IN THIS MODULE, read off its own imports.

    Derived, never assumed to be the bare word `meter`. A sweep keyed on `node.func.id ==
    "meter"` sees neither `from sluice.core import usage` + `usage.meter(...)` nor
    `from sluice.core.usage import meter as _meter` -- measured: both yield no stage at all and
    are not even reported as computed, so both roster guards stay green while a whole stage goes
    unrostered. `tests/test_paths.py` derives `resolve`'s local bindings the same way, for the
    same reason and after the same kind of miss.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sluice.core.usage":
            names |= {a.asname or a.name for a in node.names if a.name == "meter"}
        elif isinstance(node, ast.ImportFrom) and node.module == "sluice.core":
            # `from sluice.core import usage [as u]` -> `u.meter(...)`
            names |= {f"{a.asname or a.name}.meter" for a in node.names if a.name == "usage"}
        elif isinstance(node, ast.Import):
            names |= {f"{a.asname or a.name}.meter" for a in node.names
                      if a.name == "sluice.core.usage"}
    return names


def _callee_name(func):
    """`meter` for a bare call, `usage.meter` for an attribute call, else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _meter_stage_literals():
    """Every stage literal passed to `meter(...)` under `sluice/`, as ({stage}, {computed}).

    The stage is `meter`'s third POSITIONAL parameter, and only a literal counts: a computed
    stage would make the label unreviewable, which is the whole reason the roster exists. A
    non-literal is reported in the second set (`test_every_stage_is_a_literal`) rather than
    silently skipped -- skipping is how a sweep comes to check nothing.
    """
    found = set()
    computed = set()

    for path in sorted(_SLUICE.rglob("*.py")):
        rel = path.relative_to(_SLUICE).as_posix()
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        bindings = _meter_bindings(tree)
        if not bindings:
            continue

        def check(node, enclosing):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = node.name
            if isinstance(node, ast.Call) and _callee_name(node.func) in bindings:
                stage = node.args[2] if len(node.args) > 2 else None
                if isinstance(stage, ast.Constant) and isinstance(stage.value, str):
                    found.add(stage.value)
                else:
                    computed.add((rel, enclosing))
            for child in ast.iter_child_nodes(node):
                check(child, enclosing)

        check(tree, "<module>")
    return found, computed


# ----------------------------------------------------------------- anti-vacuity first

def test_the_sweeps_find_something():
    """For a guard whose success case is "nothing unaccounted for", a sweep that matches
    NOTHING is indistinguishable from a clean tree -- `all([])` is True and every assertion
    over an empty set passes. This is the row that makes the rest mean anything, and it is
    first because it has to hold before any of them is evidence."""
    sites = _complete_call_sites()
    stages, _ = _meter_stage_literals()
    assert sites, "found no .complete( call sites under sluice/ -- the AST walk is broken"
    assert stages, "found no meter( stage literals under sluice/ -- the AST walk is broken"


# ------------------------------------------------------------------ both ends agree

def test_every_llm_call_site_is_declared():
    """A `.complete(` the roster does not name is a call whose spend nobody decided about.

    This is the row that would have caught `core/app.py::doctor`: the probe round-trips every
    configured backend on a default `doctor` run, a commit message said it was metered, and no
    `meter(...)` call existed. The report was short by however many probes had been run, with
    nothing red anywhere.

    Compared as a COUNTER, so a second call added inside an ALREADY-DECLARED function reds too.
    As a set it did not: measured, adding a second unmetered `.complete(` to
    `core/app.py::doctor` -- the very site above -- left this assertion green, because the key
    was already present. That is the #304 lesson (a set absorbs an addition sharing a key)
    reproduced inside the guard written to stop this exact class.
    """
    found = _complete_call_sites()
    declared = Counter({site: count for site, (_stage, count) in _CALL_SITES.items()})
    assert found == declared, (
        "the LLM call sites under sluice/ and _CALL_SITES disagree.\n"
        f"  found: {dict(sorted(found.items()))}\n"
        f"  declared: {dict(sorted(declared.items()))}\n"
        "A NEW site needs a row here naming the stage whose meter(...) covers it -- or None "
        "with a reason, if it is metering plumbing rather than a billable call. A changed "
        "COUNT for an existing row means a call was added or removed inside that function.")


def test_every_metered_stage_is_declared():
    stages, _ = _meter_stage_literals()
    assert stages == set(_STAGES), (
        "the stage labels passed to meter(...) and _STAGES disagree.\n"
        f"  new, undeclared: {sorted(stages - set(_STAGES))}\n"
        f"  declared but gone: {sorted(set(_STAGES) - stages)}\n"
        "A stage is what groups the usage report, so adding or renaming one is a change to a "
        "user-visible label: declare it here, with the test that witnesses it firing.")


def test_every_stage_is_a_literal():
    """A computed stage cannot be reviewed and cannot be rostered, so the two guards above
    would both pass while the label became whatever a variable held at runtime."""
    _, computed = _meter_stage_literals()
    assert not computed, (
        f"meter(...) called with a non-literal stage at {sorted(computed)} -- the stage must "
        "be a literal so it is reviewable and so _STAGES can pin it")


def test_each_declared_call_site_names_a_real_stage():
    """The join. A row naming `cv-komposer` would satisfy both roster guards independently
    while pointing at a stage no `meter(...)` passes, so the site would be declared and
    unmetered -- exactly the state this file exists to make impossible."""
    named = {stage for stage, _count in _CALL_SITES.values() if stage is not None}
    unknown = named - set(_STAGES)
    assert not unknown, (
        f"_CALL_SITES names stage(s) {sorted(unknown)} that no meter(...) call passes")


def test_each_declared_stage_covers_at_least_one_call_site():
    """The other direction: a stage that meters nothing is either a leftover after a call site
    was deleted, or a wrap someone added to the wrong object."""
    covered = {stage for stage, _count in _CALL_SITES.values() if stage is not None}
    orphans = set(_STAGES) - covered
    assert not orphans, (
        f"stage(s) {sorted(orphans)} are passed to meter(...) but cover no declared call site")


@pytest.mark.parametrize("stage", sorted(_STAGES))
def test_every_stage_names_a_runtime_witness_that_exists(stage):
    """`_STAGES` maps each stage to the test that proves it actually RECORDS, because this
    file only proves the wiring is declared. A pointer at a test that does not exist is the
    coverage claim that reads as proof and is not -- so the path, and the node id where one is
    given, are both resolved here."""
    target = _STAGES[stage]
    path, _, node = target.partition("::")
    f = pathlib.Path(__file__).resolve().parent.parent / path
    assert f.exists(), f"{stage} names witness {path}, which does not exist"
    if node:
        assert f"def {node}(" in f.read_text(encoding="utf-8"), (
            f"{stage} names witness {target}, but {path} defines no such test")
