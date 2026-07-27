"""The drift guard's parser and its process contract.

The two fixtures are REAL captured output from the pinned rulesync, including the emoji and the
exact `All done!` phrasing. A version bump may change that wording; the expected failure is then a
loud parse error, never a silent pass. If these strings stop matching, fix the parser -- do not
relax the test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

# `scripts/` is a package (`scripts/__init__.py`) and the repo root is on sys.path under pytest,
# so this is a plain import -- matching tests/test_guard_no_bypass.py, which does the same. No
# sys.path manipulation.
from scripts import guard_rulesync_drift as guard

GREEN = "🎉 All done! Written 243 file(s) total (20 rules + 114 subagents + 92 skills + 17 hooks)"
# What a malformed .rulesync/hooks.json actually produces. Note it OMITS the hooks term rather
# than printing `0 hooks` -- a parser regexing `(\d+) hooks` finds no match and, if it treats
# "no match" as "skip", reports success on the one input this guard exists to reject.
BROKEN_HOOKS = "🎉 All done! Written 226 file(s) total (20 rules + 114 subagents + 92 skills)"


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


def test_main_returns_zero_on_a_matching_capture(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN, encoding="utf-8")
    assert guard.main([str(capture)]) == 0


def test_main_rejects_a_silently_dropped_feature(tmp_path):
    capture = tmp_path / "out.txt"
    capture.write_text(BROKEN_HOOKS, encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_a_partial_drop_that_a_non_zero_check_would_pass(tmp_path):
    """16 of 17 hooks. Only an equality comparison catches this; `> 0` does not."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("17 hooks", "16 hooks"), encoding="utf-8")
    assert guard.main([str(capture)]) != 0


def test_main_rejects_an_unexpected_feature(tmp_path):
    """A newly-enabled rulesync feature is drift: it emits into paths nothing ignores yet."""
    capture = tmp_path / "out.txt"
    capture.write_text(GREEN.replace("+ 17 hooks", "+ 17 hooks + 3 commands"), encoding="utf-8")
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
