"""The drift guard's parser and its process contract.

The two fixtures are captured output from the pinned rulesync, including the emoji and the exact
`All done!` phrasing.

WHAT THAT PROVENANCE DOES AND DOES NOT BUY. It buys one thing, once: at capture time the parser
and `EXPECTED` were validated against what the tool actually emits, rather than against someone's
idea of it. It does NOT make these tests detect a future wording change -- they cannot. A fixture
is a constant in this file, so a rulesync that starts phrasing its summary differently leaves it
untouched and every assertion here keeps passing. An earlier version of this docstring claimed
otherwise; CodeRabbit pointed out on #77 that a copied capture and a hand-authored string are
indistinguishable once frozen, which is correct.

Drift against the real generator is caught in the `rulesync` CI job, which runs the pinned binary
and pipes its ACTUAL stdout into the guard. That is the only place the two can disagree.
"""

import subprocess
import sys
from pathlib import Path

import pytest

# `scripts/` is a package (`scripts/__init__.py`) and the repo root is on sys.path under pytest,
# so this is a plain import -- matching tests/test_guard_no_bypass.py, which does the same. No
# sys.path manipulation.
from scripts import guard_rulesync_drift as guard

GREEN = "🎉 All done! Written 12 file(s) total (2 rules + 5 subagents + 4 skills + 1 hooks)"
# What a malformed .rulesync/hooks.json actually produces. Note it OMITS the hooks term rather
# than printing `0 hooks` -- a parser regexing `(\d+) hooks` finds no match and, if it treats
# "no match" as "skip", reports success on the one input this guard exists to reject.
BROKEN_HOOKS = "🎉 All done! Written 11 file(s) total (2 rules + 5 subagents + 4 skills)"


def test_a_matching_summary_parses_to_the_expected_map():
    assert guard.parse_summary(GREEN) == guard.EXPECTED


def test_an_omitted_feature_term_is_absent_not_zero_padded():
    """The parser reports what it saw; main() is what turns absence into a failure."""
    assert "hooks" not in guard.parse_summary(BROKEN_HOOKS)


def test_no_summary_line_is_a_hard_error():
    with pytest.raises(ValueError, match="no `All done!` summary"):
        guard.parse_summary("Written 3 subagents\nsome unrelated chatter\n")


def test_two_summary_lines_are_a_hard_error():
    with pytest.raises(ValueError, match="expected exactly 1"):
        guard.parse_summary(GREEN + "\n" + GREEN)


def test_a_term_the_parser_cannot_read_is_a_hard_error():
    """A find-all silently DISCARDS what it does not match, so this parsed clean before:
    every expected count was present and correct, and the guard reported success on a summary
    line it had only partly understood. `NONSENSE` stands in for whatever a version bump starts
    appending -- the point is that the parenthetical must be fully consumed."""
    with pytest.raises(ValueError, match="unparsed"):
        guard.parse_summary(GREEN.replace("1 hooks)", "1 hooks + NONSENSE)"))


def test_a_repeated_feature_is_a_hard_error():
    """Last-wins made this yield `hooks: 99` -- one of the two counts silently chosen, the
    other silently dropped. Either could have been the real one."""
    with pytest.raises(ValueError, match="twice"):
        guard.parse_summary(GREEN.replace("1 hooks)", "1 hooks + 99 hooks)"))


def test_terms_running_together_are_a_hard_error():
    """The gap between two terms may not be EMPTY.

    The first strictness pass rejected trailing junk and duplicates but matched the gap with
    `[\\s+]*`, so `2 rules5 subagents4 skills1 hooks` yielded exactly the expected map from
    a line no rulesync has ever emitted -- the guard certifying output it had not understood.
    """
    with pytest.raises(ValueError, match="run together"):
        guard.parse_summary(
            "All done! Written 12 file(s) total (2 rules5 subagents4 skills1 hooks)"
        )


def test_the_edges_of_the_parenthetical_may_be_bare():
    """The counterpart the obvious fix gets wrong.

    Requiring a separator EVERYWHERE rejects every real summary: nothing precedes the first term
    and nothing follows the last, so both edge gaps are empty by construction. A one-line `*` ->
    `+` swap would have turned this guard red on correct input, which is why the separator is
    mandatory only BETWEEN terms.
    """
    assert guard.parse_summary(GREEN) == guard.EXPECTED
    assert guard.parse_summary(GREEN.replace("total (", "total ( ")) == guard.EXPECTED


def test_main_returns_zero_on_a_matching_capture(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN, encoding="utf-8")
    assert guard.main([str(capture)]) == 0


def test_main_rejects_a_silently_dropped_feature(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(BROKEN_HOOKS, encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_a_partial_drop_that_a_non_zero_check_would_pass(tmp_path):
    """4 of 5 subagents. Only an equality comparison catches this; `> 0` does not.

    Uses subagents rather than hooks deliberately: hooks is 1, so nudging it tests the
    ABSENT-feature path, not the partial-drop one these two cases separate."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("5 subagents", "4 subagents"), encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_an_unexpected_feature(tmp_path):
    """A newly-enabled rulesync feature is drift: it emits into paths nothing ignores yet."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("+ 1 hooks", "+ 1 hooks + 3 commands"), encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_a_missing_capture_file(tmp_path):
    assert guard.main([str(tmp_path / "nope.txt")]) != 0


def test_an_empty_capture_file_is_a_hard_error_not_a_pass(tmp_path):
    """The pipefail analysis rests on this and nothing tested it.

    Without `set -o pipefail`, `rulesync | tee` swallows a non-zero rulesync exit -- but tee
    still leaves a 0-BYTE capture. This case is the only reason that swallowed failure still
    surfaces. If an empty file ever parsed as a pass, the CI step would go green on a rulesync
    that never ran.
    """
    capture = tmp_path / "out.txt"
    capture.write_text("", encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_the_module_exits_nonzero_as_a_process(tmp_path):
    """The CI contract IS the exit code. A suite that only calls main() in-process stays green
    when `sys.exit(main(...))` is deleted -- the lesson tests/test_guard_no_bypass.py:26-30
    records. Driven with a FAILING capture: a passing one cannot tell the two apart."""
    capture = tmp_path / "out.txt"
    capture.write_text(BROKEN_HOOKS, encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "guard_rulesync_drift.py"
    proc = subprocess.run([sys.executable, str(script), str(capture)], capture_output=True)
    assert proc.returncode != 0
