"""README's Quickstart transcripts, checked against what the commands actually print.

The Quickstart is the one section a reader types VERBATIM, and until this file nothing checked
it. `tests/test_docs_claims.py` already sweeps README hard, but every one of its claims is
shaped like a NOUN -- a command name, a channel, a config key, a link target. It does parse
these same ```console fences (`_shell_blocks`), and only to confirm that commands named inside
them are real and that no line would kill the reader's interactive shell. Nothing compared the
OUTPUT under a `$` line to what the program prints, so the transcripts drifted freely while
every other README guard stayed green. Measured on 2.4.2, three of the four had:

  - step 1 showed `wrote ~/jobhunt/sluice.local.yaml`, reachable only with `SLUICE_CONFIG`
    exported -- which the Quickstart never tells the reader to do. The real default is the XDG
    path, so a reader went looking for a file that was not on their disk.
  - step 3 predated the `EXAMPLE-SEARCH(n/m)` column (#212/#225), so the capture hid the very
    signal the paragraph beneath it describes in prose.
  - step 4 showed `'keep': 1` and explained it as the empty-config-abstains rule working. Steps
    1 to 3 create NO leads, so a reader following the page got all zeros and could not tell the
    abstain rule from a broken install. The document's climax demonstrated nothing.

Only step 2 reproduced, and its surrounding prose was wrong in its own way: it claimed the
summary line differs without the `claude` CLI. There are TWO summary lines -- one per table --
and only the backend one moves. The component total is `1 ok, 1 degraded, 3 dead, 19 notice`
either way.

WHY A TRANSCRIPT AND NOT A SUMMARY LINE. Pinning only the counts would have caught step 4 and
neither of the others: step 1's wrong path and step 3's missing column are both ordinary lines
in the body. The comparison is therefore line-by-line and EXACT, because step 3's drift is a
suffix on an otherwise-correct line and any prefix or substring match reads it as fine.

THE ELISION PROTOCOL, so a README block stays readable without going unchecked:

  - a line that is exactly `...` skips zero or more real lines (the block resumes at the next
    line that matches)
  - a line ENDING in `...` is a prefix match, for output too long to show (doctor's renderer row
    is 1207 characters on one line)
  - every other line must match a real line EXACTLY, after `$HOME` and the working directory are
    rewritten to `~` and `~/jobhunt` the way the README's own note says they are

Both forms are deliberately explicit. An implicit "close enough" match is what let a 58-character
stand-in for a 1207-character row read as a faithful capture.

HERMETICITY. Every Quickstart command is offline by construction, which is what the section
claims in its first line. Two environment pins make the transcript deterministic rather than
merely offline, and both are narrow and stated:

  - `XDG_CONFIG_HOME` is DELETED so `HOME` becomes the rung `paths.resolve` lands on, which is
    what puts the config at `~/.config/sluice/config.yaml` -- the path the README shows. The
    autouse `_pin_paths` fixture sets both, deliberately, because they are consecutive rungs of
    one chain and either can be the live one depending on the machine. Dropping one HERE is not
    a hole in that sandbox: `HOME` is still `tmp_path/home`, so nothing reaches a real config.
  - a stub `claude` is put on `$PATH`, so the `claude-max` row reads `ok` on any machine. The
    row is a `shutil.which` probe under `--offline` (the row says so: "not round-tripped"), so
    the stub is never executed and no backend is contacted.
"""

import os
import pathlib
import re
import shlex
import sys

import pytest

from sluice.cli import main

# Anchored to the repo root rather than the cwd: `quickstart_env` chdirs into the reader's
# working directory, so a bare relative path would resolve inside the sandbox and vanish.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_README = os.path.join(_ROOT, "README.md")

# The fence that carries the sample lead note, under `## What you end up with`. Since #241 the
# reader no longer WRITES this file -- `leads add` does -- so it is no longer a precondition to
# be staged. It is now the OPPOSITE: an expected value, compared against the note the command
# actually wrote (test_the_sample_note_is_what_leads_add_actually_writes). That inversion is
# what makes the block checkable at all. The old staging read the fence and wrote it to disk,
# so the fence could say anything and still "reproduce" -- measured on this very branch, README
# had been missing `role_type_source` since #223 added it and every guard here stayed green.
_NOTE_FENCE = re.compile(r"^```markdown\n(?P<body>.*?)^```", re.M | re.S)

# `first_seen`/`last_seen` are stamped with the run date, so the README's captured values can
# only ever be stale. Blanked on BOTH sides before comparing -- the key must still be present
# and in position, which is the part that can actually drift. The date SHAPE is matched
# explicitly rather than with `.*`: a permissive blank hides a change to the value's form as
# well as its content (measured -- quoting the dates in `_render_new` left this green), and
# an unmatched line simply survives into the comparison, so tightening cannot mask a drift.
_DATED = re.compile(r"^(first_seen|last_seen): \d{4}-\d{2}-\d{2}$", re.M)

_QUICKSTART = re.compile(r"^## Quickstart\s*$(?P<body>.*?)(?=^## )", re.M | re.S)
_CONSOLE = re.compile(r"^```console\n(?P<body>.*?)^```", re.M | re.S)


def _read(rel):
    with open(rel, encoding="utf-8") as fh:
        return fh.read()


def _join_continuations(first, rest):
    """Splice a `\\`-continued shell command into one, returning (command, leftover lines).

    A wrapped invocation is ONE command. Without joining, a block silently truncates at
    the first line and then pins a command the README does not actually tell anyone to
    run -- worse than not checking it, because it reads as coverage.

    The splice is RAW: the backslash and the newline go, and NOTHING is put in their
    place, because that is exactly what the shell does. An earlier cut rstripped each
    side and inserted a space. That is indistinguishable on this README -- the
    continuation lines are indented, so the tokens separate either way, and every test
    here stayed green under both -- and wrong the moment a continuation falls INSIDE a
    quoted argument, where the shell joins the halves with nothing between them. Hence
    `test_a_continuation_inside_a_quoted_argument_splices_the_way_the_shell_does`, which
    is the only thing in this file that can tell the two apart: the fix is unfalsifiable
    against the real page.

    Trimming happens ONCE, on the finished command, so an inner line's own leading
    whitespace survives into the join the way the shell leaves it.
    """
    command, rest = first, list(rest)
    while command.endswith("\\"):
        assert rest, f"a Quickstart command ends in a continuation with nothing after it: {command!r}"
        command = command[:-1] + rest.pop(0)
    return command.strip(), rest


def _quickstart_blocks():
    """[(command, [expected line, ...]), ...] for every ```console fence in the Quickstart.

    A block's first line is its `$ ` command; the rest is the expected transcript.
    """
    section = _QUICKSTART.search(_read(_README))
    assert section, (
        "README has no `## Quickstart` heading. Without it this parse selects nothing, and a "
        "sweep over an empty roster passes every assertion below it")
    blocks = []
    for fence in _CONSOLE.finditer(section.group("body")):
        lines = fence.group("body").rstrip("\n").split("\n")
        assert lines[0].startswith("$ "), (
            f"a Quickstart console block does not open with a `$ ` command line: {lines[0]!r}")
        command, rest = _join_continuations(lines[0][2:], lines[1:])
        blocks.append((command, rest))
    return blocks


def _normalise(line, subs):
    for real, shown in subs:
        line = line.replace(real, shown)
    return line


def _match(expected, actual):
    """(ok, first unmatched expected line, index reached in `actual`).

    ADJACENCY is part of the claim, not just order. Two expected lines with no `...` between
    them must match two ADJACENT real lines; only an explicit `...` licenses a gap. An earlier
    cut skipped freely toward the next match, which made the block blind to output the README
    does not show at all -- and that blindness was live: this branch's own Bases-view commit
    added a third `wrote` line to `init`, and the two-line capture kept passing. A guard that
    cannot see an ADDED line is half a guard, because a transcript drifts by gaining lines at
    least as often as by changing them.

    `may_skip` starts True so a leading banner or an interleaved stderr line cannot break the
    very first match; every later gap has to be asked for.
    """
    i, may_skip = 0, True
    for exp in expected:
        if exp.strip() == "...":            # skip-ahead: resolved by the NEXT expected line
            may_skip = True
            continue
        prefix = exp[:-3] if exp.endswith("...") else None

        def _hit(k):
            return actual[k].startswith(prefix) if prefix is not None else actual[k] == exp

        if may_skip:
            found = False
            while i < len(actual):
                if _hit(i):
                    i, found = i + 1, True
                    break
                i += 1
            if not found:
                return False, exp, i
        else:
            if i >= len(actual) or not _hit(i):
                return False, exp, i
            i += 1
        may_skip = False
    return True, None, i


@pytest.fixture
def quickstart_env(tmp_path, monkeypatch):
    """A reader's shell: an empty working directory, a sandboxed home, a stub `claude`."""
    home, cwd = tmp_path / "home", tmp_path / "jobhunt"
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    # See HERMETICITY in the module docstring for why each of these three moves.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    # Pin the RENDERER too, for the same reason as the `claude` stub below and with the
    # opposite mechanism. `doctor`'s component rows are not machine-independent: measured, a
    # machine with the `render` extra installed reports the renderer `ok`, which moves the
    # component totals and fails this test on a legitimate developer install. (An earlier
    # version of this file, and README's own prose, both claimed the component rows did not
    # vary. Executed: adding a baseline CV alone moves `3 dead` to `2 dead`.)
    #
    # `None` in `sys.modules` makes `import weasyprint` raise ImportError, which is exactly the
    # state of a bare `pip install job-sluice` -- the install README's capture documents. So this
    # pins the transcript to the environment the README claims it was taken in, rather than to
    # whatever happens to be installed where the suite runs.
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(cwd)
    # Longest first: `cwd` is not under `home` here, but a future layout where it is would
    # rewrite the shorter prefix first and leave a half-substituted path behind.
    return [(str(cwd), "~/jobhunt"), (str(home), "~")]


def test_every_quickstart_transcript_reproduces(quickstart_env, capsys):
    """Run the Quickstart the way a reader does, and hold each capture to what it prints."""
    blocks = _quickstart_blocks()

    # No staging step any more. Step 4's precondition used to be "write this note file by hand",
    # performed here on the reader's behalf; since #241 it is `leads add`, which is itself one of
    # the roster's commands and runs in this same loop. So the chain is now genuinely end to end:
    # step 1's config decides where the vault is, step 4a writes into it, step 4b reads it back.
    for command, expected in blocks:
        argv = shlex.split(command)
        assert argv[0] == "job-sluice", f"unexpected program in a Quickstart block: {argv[0]!r}"
        capsys.readouterr()
        main(argv[1:])
        captured = capsys.readouterr()
        actual = [_normalise(ln, quickstart_env)
                  for ln in (captured.out + captured.err).split("\n")]

        ok, missed, reached = _match(expected, actual)
        assert ok, (
            f"README's Quickstart capture for `{command}` no longer reproduces.\n"
            f"  expected line: {missed!r}\n"
            f"  not found in the real output from that point on.\n"
            f"  real output was:\n" + "\n".join(f"    {ln}" for ln in actual[reached:][:25]))


def test_the_sample_note_is_what_leads_add_actually_writes(quickstart_env, capsys):
    """README shows a lead note and calls it "exactly as written". Hold it to that.

    Before #241 the reader hand-authored this block, so the test could only STAGE it and check
    triage accepted it -- which certifies nothing about the block's own accuracy, because the
    fence was both the input and the claim. Now `leads add` writes the note, so the fence is a
    prediction the store can be held to, and the comparison runs the other way.

    Measured while writing this: README's block had been missing `role_type_source` since #223
    added that key to every note `_render_new` writes, and nothing here or in
    tests/test_docs_claims.py could see it -- the noun-shaped sweeps check that keys named in
    prose exist, never that a shown artefact matches a produced one.

    Whole-block and line-by-line, not a spot check on keys someone thought of: an ADDED key is
    the drift that actually happened, and a per-key sweep is blind to it by construction.
    """
    blocks = dict(_quickstart_blocks())
    init = next(c for c in blocks if c.startswith("job-sluice init"))
    add = next(c for c in blocks if c.startswith("job-sluice leads add"))

    def _md():
        return set(pathlib.Path(os.getcwd()).rglob("*.md"))

    capsys.readouterr()
    assert main(shlex.split(init)[1:]) == 0, f"`{init}` did not exit 0"
    # DIFFERENCED against the post-init tree rather than globbed absolutely: `init` writes a
    # Judging Profile of its own, and naming the lead's expected path here would hardcode the
    # layout the store is free to decide (`lead_layout`). The difference is exactly the file
    # THIS command created, whatever the layout.
    before = _md()
    capsys.readouterr()
    assert main(shlex.split(add)[1:]) == 0, f"`{add}` did not exit 0"

    written = sorted(_md() - before)
    assert len(written) == 1, (
        f"expected `leads add` to leave exactly one new note; found {[str(w) for w in written]}")

    shown = _NOTE_FENCE.search(_read(_README))
    assert shown, "README's `## What you end up with` sample note fence did not parse"
    expected = _DATED.sub(r"\1: <date>", shown.group("body")).rstrip("\n").split("\n")
    actual = _DATED.sub(r"\1: <date>",
                        written[0].read_text(encoding="utf-8")).rstrip("\n").split("\n")
    assert actual == expected, (
        "README's sample lead note is not what `job-sluice leads add` writes.\n"
        "  README shows:  " + repr(expected) + "\n"
        "  the store wrote: " + repr(actual))


def test_the_quickstart_roster_is_what_this_file_thinks_it_is(quickstart_env):
    """Pin the SCOPE, because a parse that finds nothing satisfies every assertion above.

    `_match` over an empty `expected` returns True, and a `_quickstart_blocks` that matched no
    fences would walk zero blocks and pass. This is the anti-vacuity check: the commands, and a
    floor on how many EXACT (non-elided) lines each one actually pins.
    """
    blocks = _quickstart_blocks()
    # Compared as ARGV, not as raw strings. The splice is deliberately raw (see
    # `_quickstart_blocks`), so a `\`-continued command carries the README's own
    # indentation inside it; pinning that verbatim would make this tripwire fire on a
    # cosmetic reflow while saying "the Quickstart's commands have changed". argv is
    # the claim that matters -- it is what the reader's shell builds and what this file
    # actually executes -- and it stays EXACT inside quotes, so a changed value still
    # reddens.
    expected = [
        "job-sluice init --no-input --vault ./vault",
        "job-sluice doctor --offline",
        "job-sluice ingest list-sources",
        'job-sluice leads add --url https://example.invalid/jobs/1234 '
        '--company "Example Systems" --role "Senior Engineer" '
        '--location "Example City" --salary "\u00a3500/day" --role-type contract',
        "job-sluice triage run --no-llm",
    ]
    assert [shlex.split(c) for c, _ in blocks] == [shlex.split(c) for c in expected], (
        f"the Quickstart's commands have changed: {[c for c, _ in blocks]}")

    exact = {c: sum(1 for ln in exp if ln.strip() and not ln.endswith("...")) for c, exp in blocks}
    # Floors, not equalities: adding a line to a capture must not fail this, dropping the block's
    # substance must. Each floor is under the count measured when this file was written.
    add_cmd = next(c for c in exact if c.startswith("job-sluice leads add"))
    for command, floor in (("job-sluice init --no-input --vault ./vault", 4),
                           ("job-sluice doctor --offline", 6),
                           ("job-sluice ingest list-sources", 3),
                           # One line, and it must be exact: `created` is the whole claim, and a
                           # prefix match would accept every other outcome upsert can report.
                           (add_cmd, 1),
                           ("job-sluice triage run --no-llm", 0)):
        assert exact[command] >= floor, (
            f"`{command}`'s capture now pins only {exact[command]} exact lines (floor {floor}). "
            "Elide less, or this block has stopped being a transcript.")
    # Step 4's single line is a prefix match by necessity (the real line is ~290 characters), so
    # it contributes 0 exact lines and needs its own claim: the count that carries the meaning.
    step4 = dict(blocks)["job-sluice triage run --no-llm"]
    assert any("'keep': 1" in ln for ln in step4), (
        "step 4's capture no longer shows `'keep': 1`, which is the whole point of the step: it "
        "is what demonstrates the empty-config-abstains rule to a reader")


@pytest.mark.parametrize("drift,why", [
    (["wrote   ~/jobhunt/sluice.local.yaml"], "the real #1 drift: a path only SLUICE_CONFIG gives"),
    (["bayt             browser   enabled"], "the real #3 drift: the EXAMPLE-SEARCH suffix"),
    (["b", "a"], "an out-of-order transcript"),
    (["a", "b"], "an UNDOCUMENTED line between two documented ones"),
])
def test_the_matcher_rejects_a_drifted_transcript(drift, why):
    """Falsify the matcher itself, or `_match` returning True proves nothing about the captures.

    A negative sweep's success case is "found nothing wrong", so the assertions above cannot tell
    a faithful capture from a matcher that accepts anything. Each row here is a line the real
    output does NOT contain, including the two drifts this file was written for.
    """
    # "a" and "b" are NOT adjacent here: the row above them is what a real transcript gaining
    # an undocumented line looks like, and it is the case the old matcher walked straight past.
    actual = ["wrote   ~/.config/sluice/config.yaml",
              "bayt             browser   enabled EXAMPLE-SEARCH(1/1)",
              "a", "an undocumented line", "b"]
    ok, missed, _ = _match(drift, actual)
    assert not ok, f"_match accepted {drift!r}, which it must not: {why}"
    assert missed is not None


def test_a_continuation_inside_a_quoted_argument_splices_the_way_the_shell_does():
    """The ONE case that distinguishes a raw splice from a space-inserting one.

    Every `\\`-continued line in the real README is indented, so both implementations
    yield identical argv there and the whole file stays green under either -- measured.
    A continuation falling inside a QUOTED argument is where they diverge: the shell
    removes the backslash and the newline and joins the halves with nothing between
    them, so this is `ExampleSystems`. The space-inserting version produced
    `Example Systems`, i.e. the harness would have run a command the page does not
    document while reporting that the page reproduced.

    Written because CodeRabbit found the fidelity bug and nothing in the suite could
    have caught the fix being reverted.
    """
    # `--value`, not `--company`, and a sentinel with no identity shape at all: the
    # subject here is the SPLICE, so an employer-shaped token in an employer-shaped
    # option is gratuitous, and this one could not follow the `Example <Word>` roster
    # anyway -- the whole point is that the join eats the space between the halves.
    command, rest = _join_continuations('cmd --value "one\\', ['two" --x 1'])
    assert shlex.split(command) == ["cmd", "--value", "onetwo", "--x", "1"]
    assert rest == []


def test_a_continuation_with_nothing_after_it_is_refused():
    """The assert inside the loop, which is otherwise unreachable on a well-formed page
    and would sit there untested -- an unbounded `pop` from an empty list instead."""
    try:
        _join_continuations("cmd --flag \\", [])
    except AssertionError as exc:
        assert "nothing after it" in str(exc)
    else:
        raise AssertionError("a dangling continuation was accepted")


def test_the_matcher_accepts_the_elision_forms_it_documents():
    """The other direction: the two elision forms must actually work, or a block goes unchecked
    in the safe-looking direction and nobody notices, since the test still passes."""
    actual = ["first", "noise", "second longer than shown", "third"]
    ok, _, _ = _match(["first", "...", "second longer ...", "third"], actual)
    assert ok, "the documented elision forms no longer match a transcript they describe"
