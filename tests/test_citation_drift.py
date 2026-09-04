"""#191: a `file:line` citation in prose is a drift surface with no guard on it.

The class recurred five times during #174 alone, each time inside the work fixing the
previous instance. Sweeps do not hold it: a sweep fixes what it can see at one moment, and
the next edit above the cited line silently re-breaks every citation below it.

WHY THE OBVIOUS GUARD DOES NOT WORK, and why this one is syntactic instead. A structural
check -- does the file exist, is the line in range -- finds almost nothing, because the
dominant failure is a line number that still resolves and no longer means anything.
Measured on the tree this guard was written against, over live code only, using the
extractor below rather than an ad-hoc script -- an earlier count said "3 mechanically
stale" and was an artefact of a cruder path resolver:

    30 citations   1 blank   0 past EOF   2 ambiguous path   >=5 stale in fact

The mechanical column is the argument. Only ONE citation in the whole live tree points at
a blank line; everything else resolves, and five or more of those still mean nothing.

The two that settle it both pass every structural check. `cv/validate.py` claimed a line of
`cv/engine.py` logged an exception, while that line was a `set_tailored_cv` call -- and the
same dead line was cited from three files. `test_triage_engine.py` cited a line of
`triage/engine.py` as "the classify-pass apply site", where it was a breaker-threshold
constant. A range check passes both, reports the tree clean, and reads as proof.

(Both are described here rather than cited, because rule 1 below binds this file too. That
is deliberate: a guard exempt from its own rule is a guard nobody believes.)

So the rule here is not "the line must resolve" but "do not cite a line at all". A line
number is the part that rots; the file and the symbol are the parts a reader can act on,
and `.rulesync/rules/CLAUDE.md` carries the rule this formalises: grep the CLAIM, never
follow the diff.

TWO RULES, and they are complements rather than alternatives:

  1. No `path:line` in a comment or docstring under the live trees.
  2. A `path::symbol` citation must name a symbol that exists in that file.

Rule 1 needs no resolver, no line arithmetic and no judgement, so unlike a range check it
cannot pass by failing to look. Rule 2 is what makes rule 1 affordable: it gives the
citations that DO have a symbol somewhere to go, and guards them once they get there.

`docs/superpowers/{specs,plans}` are out of scope: `CLAUDE.md` declares them historical and
unmaintained, and all but a couple of the repo's unresolvable citations live there. No exact
figure is given, on purpose: it moves with how a citation is counted, two measurements of it
disagreed, and an unasserted number in prose is the drift this file exists to stop. The
stable part, and the one that matters, is that live code holds only two.

KNOWN BLIND SPOT, reported rather than closed. A citation inside a STRING LITERAL is
invisible here, because `_prose` reads comments and docstrings only. One live instance was
found by review: `scripts/render_homebrew_formula.py` embeds a Ruby formula as a Python
string, and a `#` comment inside it had rotted. Extending the sweep to strings is the
obvious fix and is deliberately NOT taken: it would flag 15 string citations under
`tests/`, twelve of them `test_no_leaked_files.py`'s grep-shaped fixtures and three of them
this file's own parametrize rows. A guard that fires on its own test data is deleted, and
then guards nothing at all. The trade is stated here so the gap is a decision rather than
an oversight.
"""
import ast
import io
import os
import re
import tokenize

import pytest

# The live trees. `docs/` is excluded deliberately -- see the module docstring.
_LIVE_TREES = ("sluice", "tests", "scripts")

# `path.ext:123`. The extension list is what keeps `Note:12` or a bare `foo:1` out; a
# citation names a FILE. `:line:col` forms are caught too, since the first group still
# matches and that is still a line citation.
#
# NO whitespace around the colon, and that is what separates a citation from a COUNT.
# A citation is written tight, filename then colon then digits with nothing between, while
# prose that happens to pair a filename with a number puts a space after the colon -- a
# settings file "with 2 hooks", a manifest "with 3 dependencies".
# Both were flagged by an earlier `\s*:\s*` form. Firing on ordinary sentences is how a
# guard earns its deletion, so the tighter shape is the safer error to make: a citation
# written loosely is missed, which costs one stale line number, where a false positive
# costs the whole rule.
_LINE_CITE = re.compile(r"\b([\w./-]+\.(?:py|md|yaml|yml|toml|json|sh|j2)):(\d+)\b")

# The pytest node-id shape -- a python path, a double colon, an identifier -- which
# this repo already uses in dozens of places.
_SYMBOL_CITE = re.compile(r"\b([\w./-]+\.py)::(\w+)")

# A line number RIDING ON a citation -- a symbol or a path with an orphaned numeric tail
# hanging off it by a hyphen or a comma. This exists because the first
# migration for #191 produced exactly these and rule 1 could not see them: the originals
# were RANGES and LISTS (a start line, then a hyphen or comma and a second number), a scripted
# replacement rewrote only the head, and the orphaned tail is no longer `path` + colon +
# digits so the pattern above does not match. Nine shipped that way in one commit -- the
# very drift this file was opened to end, admitted by the file itself.
#
# Matched against the whole citation rather than a bare `-\d+`, so an ordinary hyphenated
# word or a range in prose ("lines 10-20", "2026-09-04") cannot trip it.
_RESIDUAL_LINE = re.compile(
    r"(?:\.(?:py|md|yaml|yml|toml|json|sh|j2)|::\w+)\s*[-,]\s*(\d+)\b")

# Scope floors. A sweep that enumerates nothing satisfies every negative assertion below
# it, which for a BAN is precisely how the gate dies quietly -- and this exact failure is
# what #191 reports from its own prototype, whose first run said "0 citations / 0 stale"
# because an absolute path matched its own skip-component and it walked no files at all.
# Floors, not equalities: the tree grows, and a guard that fails on every added file
# teaches people to edit the number rather than read the failure.
_MIN_FILES_SWEPT = 300
_MIN_FILES_WITH_PROSE = 250      # per HALF -- comments and docstrings are floored separately
_MIN_SYMBOL_CITATIONS = 55      # 63 live; slack here hides a resolver that stopped working


def _live_python_files():
    out = []
    for tree in _LIVE_TREES:
        for dirpath, dirnames, filenames in os.walk(tree):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            out.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".py"))
    return sorted(out)


def _prose(path):
    """Every COMMENT and DOCSTRING in `path`, as (text, line).

    Read through `tokenize` and `ast`, never as raw text, and that is load-bearing rather
    than tidy. A regex over the file bytes reports 43 citations here where there are 30.
    TWELVE of the extra thirteen are grep-shaped fixture strings inside
    `test_no_leaked_files.py`'s parametrize table -- rows of the form
    "<file> colon <line> colon <content>" that mimic grep output -- which are test DATA, not
    citations. A guard that fails on its own fixtures is a guard someone deletes, and it
    would have been deleted for a true positive rate near zero.

    The THIRTEENTH is not a fixture at all: a genuinely rotted citation inside a string
    literal, described under the blind spot below. An earlier draft of this paragraph
    attributed all thirteen to the fixtures and contradicted that paragraph three
    screens down.
    """
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except (OSError, UnicodeDecodeError):
        return out
    # Consecutive `#` lines are joined into ONE block, because a citation too long for the
    # line limit is WRAPPED across them -- `...::test_parse_refuses_a_section_it_` on one
    # line and `does_not_model` on the next. Read as separate tokens, the first half names
    # a symbol that does not exist and the guard reports drift on a correct citation.
    # Measured: three of this repo's citations wrap, and all three are legitimate.
    comments, run, run_line = [], [], None
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []
    prev_line = None
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        if prev_line is not None and tok.start[0] == prev_line + 1:
            run.append(tok.string)
        else:
            if run:
                comments.append(("\n".join(run), run_line))
            run, run_line = [tok.string], tok.start[0]
        prev_line = tok.start[0]
    if run:
        comments.append(("\n".join(run), run_line))
    out.extend(comments)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.append((doc, getattr(node, "lineno", 1)))
    return out


def _under_live_tree(path):
    """Is `path` really inside one of the live trees?

    `realpath` on both sides, so neither `..` nor a symlink can walk out. Without this the
    first branch of `_resolve` accepted ANY existing path: measured, a citation naming
    `docs/superpowers/specs/<a real file>.py` resolved and its symbols satisfied rule 2 --
    a file this guard's own docstring declares historical and out of scope. Rule 2 claims
    the symbol exists in a file the guard governs, and without this the claim was false."""
    root = os.path.realpath(path)
    return any(root == os.path.realpath(t) or root.startswith(os.path.realpath(t) + os.sep)
               for t in _LIVE_TREES)


def _resolve(cited):
    """The live-tree file a citation names, or None. Accepts a repo-relative path, a
    tree-relative one, or any unambiguous suffix -- citations in this repo are written
    all three ways (`core/paths.py`, `sluice/core/vault.py`, `engine.py`).

    Every branch is filtered through `_under_live_tree`, never just the first: a path that
    exists is not thereby a path this guard governs."""
    if os.path.isfile(cited) and _under_live_tree(cited):
        return cited
    for tree in _LIVE_TREES:
        joined = os.path.join(tree, cited)
        if os.path.isfile(joined) and _under_live_tree(joined):
            return joined
    # This branch needs the check too, and the reasoning that said otherwise was WRONG in an
    # instructive way. It looked structurally inert -- `_live_python_files` walks
    # `_LIVE_TREES` and nothing else, so surely every candidate is already inside one -- and
    # a mutant deleting it SURVIVED, which seemed to confirm that. Both were wrong.
    # `os.walk` does not follow symlinked DIRECTORIES, but it lists symlinked FILES like any
    # other entry, so a link at `sluice/x.py` pointing outside is yielded, matches a suffix
    # citation, and hands rule 2 a file this guard does not govern. Measured end to end: the
    # link is listed, `_resolve` returns it, its realpath is outside, and its symbols satisfy
    # the rule. The mutant survived only because the tree happens to contain no such link --
    # a negative check with nothing to catch, which is this module's whole subject arriving
    # one more time in its own resolver. (CodeRabbit, PR #256.)
    tail = os.sep + cited.replace("/", os.sep)
    matches = [f for f in _live_python_files()
               if f.endswith(tail) and _under_live_tree(f)]
    return matches[0] if len(matches) == 1 else None


def _symbols(path):
    """Every name a `::symbol` citation may legitimately address in `path`: functions,
    classes, and module-level assignments. Constants are included because this repo cites
    them (`core/vault.py::_LEADS_SUBDIR`, `cv/render.py::_CITE_RE`) and a rule that
    admitted only `def`/`class` would push those citations back to line numbers."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    # MODULE and CLASS level only, never `ast.walk`. Walking the whole tree also collects
    # every assignment inside every function body, which makes a local variable citable:
    # measured, single-letter loop variables and intermediate locals in the cv engine were
    # accepted as citation targets, and renaming the real `Sluice.backend` METHOD left a
    # citation of it green because four unrelated
    # `backend = ...` locals still matched. That is a false ACCEPT in the guard written to
    # stop exactly this -- a citation certified green while its target no longer exists.
    # Narrowing costs nothing: it breaks none of the live citations.
    names, bodies = set(), [tree.body]
    while bodies:
        for node in bodies.pop():
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if isinstance(node, ast.ClassDef):
                    bodies.append(node.body)      # methods and class attributes, not locals
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def citation_offences(text):
    """Every rule-1 offence in one piece of prose, as human-readable strings.

    A PURE function taking text, not a file, and that is what makes both halves testable.
    Rule 1 is a NEGATIVE assertion: it passes when it finds nothing, so on a clean tree
    deleting either check costs nothing and the suite stays green. Measured -- removing the
    residual check outright survived a full run. The table test below is therefore what
    holds these two checks in place, and the file sweep is what applies them."""
    out = []
    for m in _LINE_CITE.finditer(text):
        out.append(f"cites {m.group(1)}:{m.group(2)}")
    for m in _RESIDUAL_LINE.finditer(text):
        out.append(f"carries a line number on a citation: {m.group(0)!r}")
    return out


def symbol_offences(text, resolve=None, symbols_of=None):
    """(citations checked, offences) for `::symbol` citations in one piece of prose.

    Pure and injectable for the same reason `citation_offences` is: rule 2 is another
    NEGATIVE assertion, so on a clean tree every branch in it can be deleted for free. Two
    were -- restoring the silent skip on an unresolvable path, and widening the wrap join
    back across blank lines, both SURVIVED a full run once the tree held no instance of
    either. The table test below is what holds them; the file sweep only applies them."""
    resolve = resolve or _resolve
    symbols_of = symbols_of or _symbols
    checked, out = 0, []
    for m in _SYMBOL_CITE.finditer(text):
        target = resolve(m.group(1))
        if target is None:
            # NOT a skip. An unresolvable path means this citation is CHECKED BY NOTHING,
            # and a silent `continue` is the vacuity this file exists to stop: measured,
            # renaming the target of one such citation SURVIVED a full run. It happens when
            # the path is ambiguous (`engine.py` matches three files) or when the path
            # itself WRAPPED across two comment lines -- this commit's own migration
            # produced one. Both are fixed by writing a path that resolves, which is also
            # what makes the citation greppable.
            out.append(f"cites {m.group(1)}::{m.group(2)}, whose path does not resolve to "
                       "exactly one file (ambiguous, or wrapped across lines) -- qualify it")
            continue
        checked += 1
        if not _names_a_symbol(text, m, symbols_of(target)):
            out.append(f"cites {m.group(1)}::{m.group(2)}, which does not exist in {target}")
    return checked, out


def _names_a_symbol(text, match, symbols):
    """Does this `::symbol` match name a real symbol, allowing for a WRAPPED citation?

    A citation too long for the line limit is broken across two comment lines mid-identifier,
    so the regex sees only its first half. Rather than loosen the check to a prefix match --
    which would accept a genuinely deleted symbol whose name merely starts like a live one --
    this reconstitutes the wrap: take the word characters that immediately follow, across the
    newline and any `#` and indentation, and require the JOINED name to exist exactly."""
    if match.group(2) in symbols:
        return True
    rest = text[match.end():]
    # `[ \t]*`, never `\s*`: `\s` matches newlines, so the join reached across a BLANK LINE
    # and glued an identifier to unrelated prose in the next paragraph. Measured -- a `run`
    # citation was accepted against `run_one` because three lines below, a sentence began
    # with `_one`. That is a false ACCEPT, and it would certify a deleted symbol.
    cont = re.match(r"\n[ \t]*#?[ \t]*(\w+)", rest)
    return bool(cont) and (match.group(2) + cont.group(1)) in symbols


def test_the_sweep_actually_reads_the_live_trees():
    """SCOPE, asserted before either rule. Both rules below are NEGATIVE -- they pass when
    they find nothing -- so a sweep that walks no files satisfies them perfectly. #191's own
    prototype did exactly that and reported a clean tree."""
    files = _live_python_files()
    assert len(files) >= _MIN_FILES_SWEPT, (
        f"the sweep enumerated {len(files)} files under {_LIVE_TREES}; it is broken, not "
        "looking at a clean tree")
    # EACH HALF separately, because either alone clears a combined floor with room to
    # spare -- the two halves cover almost the same files, so a combined floor is satisfied
    # by whichever survives. Measured: deleting the COMMENT half outright left every test
    # green under the old combined floor --
    # and a third of the symbol citations live in comments. A combined floor cannot witness
    # the loss of either half, which is the same vacuity this file exists to prevent.
    with_comment = with_docstring = 0
    for f in files:
        kinds = {"comment" if t.lstrip().startswith("#") else "docstring"
                 for t, _line in _prose(f)}
        with_comment += "comment" in kinds
        with_docstring += "docstring" in kinds
    assert with_comment >= _MIN_FILES_WITH_PROSE, (
        f"only {with_comment} files yielded a COMMENT; the comment half of the prose "
        "extractor is failing silently and its citations are unchecked")
    assert with_docstring >= _MIN_FILES_WITH_PROSE, (
        f"only {with_docstring} files yielded a DOCSTRING; the docstring half of the prose "
        "extractor is failing silently and its citations are unchecked")


def test_no_line_number_citations_in_comments_or_docstrings():
    """RULE 1. A line number is the part of a citation that rots, and it rots invisibly:
    an edit anywhere above the cited line moves it, and the citation still resolves.

    Cite the file and the symbol instead -- `core/vault.py::_resolve_path`, or the file plus
    the claim quoted so `grep` finds it. Both survive every edit that does not rename the
    thing, and a rename that breaks a citation is a change worth being told about."""
    offenders = []
    for path in _live_python_files():
        for text, line in _prose(path):
            offenders += [f"{path}:{line} {o}" for o in citation_offences(text)]
    assert not offenders, (
        "line-number citations found in prose (#191). Cite `file.py::symbol`, or the file "
        "plus the claim in words -- never a line number:\n  " + "\n  ".join(sorted(offenders)))


def test_symbol_citations_name_a_symbol_that_exists():
    """RULE 2, and what makes rule 1 affordable: it gives a citation somewhere to go, and
    then holds it there. A `::symbol` breaks only on rename or deletion -- exactly the
    change a reader needs to hear about, and exactly the change a line number hides."""
    citations, offences = 0, []
    for path in _live_python_files():
        for text, line in _prose(path):
            found, offs = symbol_offences(text)
            citations += found
            offences += [f"{path}:{line} {o}" for o in offs]
    # SCOPE again, and specific to THIS rule: rule 1 pushes citations into `::symbol`, so a
    # collapse here means the extractor or the resolver stopped working, not that the
    # convention was abandoned.
    assert citations >= _MIN_SYMBOL_CITATIONS, (
        f"only {citations} resolvable `::symbol` citations found; the sweep is broken, "
        "since rule 1 requires this form and the tree is known to hold many")
    assert not offences, (
        "`::symbol` citations that nothing checks, or that name a symbol which no longer "
        "exists:\n  " + "\n  ".join(sorted(offences)))


@pytest.mark.parametrize("prose,expected", [
    # -- rule 1, first half: a plain line citation --
    ("# see core/vault.py:120 for why", 1),
    ('"""Measured in cv/engine.py:795 -- stale."""', 1),
    ("# see core/vault.py::_resolve_path for why", 0),   # the form rule 1 wants
    ("# ratio was 3:1 and the exit code 2", 0),          # not a file, not a citation
    ("# see docs/USAGE.md:40", 1),                       # non-python files count too
    # -- rule 1, second half: a line number RIDING ON a citation. These are the exact
    #    shapes this branch's own migration produced, from ranges and lists. --
    ("# see core/vault.py::_evidence_dir-1774 for why", 1),
    ("# see ingest/sink.py,31 for why", 1),
    ("# see tests/test_guard_no_bypass.py-30 for why", 1),
    # -- and what must NOT trip it, or the guard is deleted for crying wolf --
    ("# measured 2026-09-04, over lines 10-20 of the spec", 0),
    ("# see core/vault.py::_resolve_path for why", 0),
    ("# a well-known-name and a range of 5-10 items", 0),
    # -- a filename beside a COUNT is prose, not a citation: spaced, so it must not fire --
    ("# rulesync wrote settings.json: 2 hooks", 0),
    ("# package.json: 3 dependencies pinned", 0),
])
def test_the_offence_detector_discriminates(prose, expected):
    """The detector itself, on inputs where the answer is known -- and the ONLY thing
    holding either check in place.

    Both checks live inside a rule that passes when it finds nothing, so on a clean tree
    they can be deleted with the suite staying green: measured, removing the residual check
    outright SURVIVED a full run. Rows here fail the moment a check is removed or its
    pattern stops matching, which is what a negative guard cannot do for itself.

    The false-positive rows are not padding. A guard that fires on a date or an ordinary
    hyphenated word gets deleted, and this repo has already lost a parser refusal that way."""
    assert len(citation_offences(prose)) == expected


# A fake resolver/symbol table, so these rows exercise the LOGIC rather than the tree.
def _fake_resolve(cited):
    return "target.py" if cited in ("pkg/target.py", "target.py") else None


@pytest.mark.parametrize("prose,checked,offences", [
    # resolvable path, symbol exists -> clean
    ("# see pkg/target.py::alive for why", 1, 0),
    # resolvable path, symbol gone -> the drift rule 2 exists to catch
    ("# see pkg/target.py::deleted for why", 1, 1),
    # UNRESOLVABLE path -> checked by nothing. Not a skip: it is the offence.
    ("# see ambiguous.py::alive for why", 0, 1),
    # a symbol WRAPPED across a comment line is reconstituted, so it stays clean
    ("# see pkg/target.py::ali\n# ve for why", 1, 0),
    # ...but a join must not cross a BLANK line and glue on unrelated prose
    ("# see pkg/target.py::ali\n\n# ve is a different paragraph", 1, 1),
])
def test_symbol_offences_discriminates(prose, checked, offences):
    """Rule 2's branches, on inputs where the answer is known -- and the ONLY thing holding
    them in place.

    Rule 2 passes when it finds nothing, so once the tree is clean every branch inside it is
    free to delete. Measured on this branch: restoring the silent `continue` for an
    unresolvable path SURVIVED a full run, and so did widening the wrap join back across
    blank lines. Both are real defects that the file sweep cannot witness for itself, which
    is why they are pinned here instead."""
    got_checked, got = symbol_offences(prose, resolve=_fake_resolve,
                                       symbols_of=lambda _t: {"alive", "alive_ve", "ali_ve"})
    assert (got_checked, len(got)) == (checked, offences), got


def test_a_citation_cannot_resolve_outside_the_live_trees(tmp_path):
    """Rule 2 claims a cited symbol exists in a file THIS GUARD GOVERNS. `_resolve` used to
    accept any existing path before it consulted `_LIVE_TREES`, so the claim was false:
    measured, a citation naming a real file under `docs/superpowers/` -- the tree this
    guard's own docstring declares historical and out of scope -- resolved, and that file's
    symbols satisfied rule 2. (CodeRabbit, PR #256.)

    The SCOPE assertion is the point of this test rather than ceremony: the file is asserted
    to EXIST before it is asserted to be rejected. Without that, the test would pass with
    the containment deleted, proving only that `_resolve` cannot find a file that is not
    there -- the same vacuity the rest of this module is about."""
    outsider = tmp_path / "outsider.py"
    outsider.write_text("def looks_citable():\n    pass\n")
    assert outsider.is_file(), "fixture missing: the rejection below would be vacuous"

    assert not _under_live_tree(str(outsider))
    assert _resolve(str(outsider)) is None

    # ...while a real in-tree file still resolves, so the check narrowed nothing it governs.
    assert _under_live_tree("sluice/core/vault.py")
    assert _resolve("core/vault.py") == os.path.join("sluice", "core", "vault.py")


def test_a_symlinked_file_cannot_smuggle_a_symbol_into_the_live_trees(tmp_path, monkeypatch):
    """`os.walk` does not follow symlinked DIRECTORIES, but it lists symlinked FILES like any
    other entry. So a link inside a live tree pointing outside it is a candidate for suffix
    resolution, and rule 2 would then check a citation against a file this guard does not
    govern -- reporting green on a symbol it has no claim over.

    This row exists because the check it pins was briefly REMOVED as an equivalent mutant:
    deleting it survived a full run, which read as proof that `_live_python_files` already
    constrained the branch. It does not, and the mutant survived only because the tree
    contains no such link. A negative check with nothing to catch cannot witness its own
    deletion -- the module's own subject, arriving in its resolver. (CodeRabbit, PR #256.)

    Hermetic: `_LIVE_TREES` is repointed at a temporary tree, so nothing is written into the
    real `sluice/` and a failure cannot leave a stray link behind."""
    tree = tmp_path / "livetree"
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "genuine.py").write_text("def real_symbol():\n    pass\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "smuggled.py").write_text("def smuggled_symbol():\n    pass\n")
    (tree / "pkg" / "smuggled.py").symlink_to(outside / "smuggled.py")

    monkeypatch.setattr("tests.test_citation_drift._LIVE_TREES", (str(tree),))

    # SCOPE: the walk really does list the symlink, or the rejection below proves nothing.
    listed = _live_python_files()
    assert str(tree / "pkg" / "smuggled.py") in listed, (
        "os.walk did not list the symlinked file; this fixture no longer reproduces")
    assert str(tree / "pkg" / "genuine.py") in listed

    assert _resolve("smuggled.py") is None          # escapes the tree -> not governed
    assert _resolve("genuine.py") == str(tree / "pkg" / "genuine.py")   # still resolves
