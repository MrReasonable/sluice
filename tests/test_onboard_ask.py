"""The asker is the only impure half. Its load-bearing property is that the TTY path and the
--no-input path CONVERGE: the wizard is friendlier, not different."""
import io
import os

import pytest

from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect, collect_profile,
                                edit_in_editor)
from sluice.onboard.questions import catalogue

VAULT = "./vault"


def _tty(script, editor=None):
    return TtyAsker(stdin=io.StringIO(script), stdout=io.StringIO(), editor=editor)


def _cat():
    return catalogue(default_vault=VAULT)


def test_a_blank_tty_vault_answer_is_PARSED_like_a_typed_one(tmp_path, monkeypatch):
    """The round-1 High. v1 returned q.default unparsed, so a fresh-install TTY run wrote a
    cwd-relative vault_dir into a per-system config -- and its convergence test compared the buggy
    value to itself, passing BECAUSE of the bug."""
    monkeypatch.chdir(tmp_path)
    got = collect(_tty("\n" * (len(_cat()) + 4)), _cat())
    assert os.path.isabs(got["vault_dir"])
    assert got["vault_dir"] == str(tmp_path / "vault")


def test_the_tty_and_flag_paths_agree_on_an_INDEPENDENTLY_stated_answer(tmp_path):
    """Seeded from a literal both sides are given, never from the other arm's output -- v1 fed the
    TTY's own answer into the flag path, so the test could not see them diverge."""
    typed = str(tmp_path / "notes")
    tty = collect(_tty(typed + "\n" + "\n" * (len(_cat()) + 4)), _cat())
    flags = collect(NoInputAsker(presets={"vault_dir": typed}), _cat())
    assert tty == flags == {"vault_dir": typed}


def test_blank_answers_skip_every_preference_question(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert set(collect(_tty("\n" * (len(_cat()) + 4)), _cat())) == {"vault_dir"}


def test_no_input_without_the_vault_refuses_rather_than_hanging():
    """Never block on a pipe: a wizard waiting on stdin in CI is a hung job with no diagnosis."""
    with pytest.raises(MissingAnswer, match="--vault"):
        collect(NoInputAsker(presets={}), _cat())


def test_no_input_never_auto_takes_a_default():
    """A default must never be written into a config nobody was asked about."""
    got = collect(NoInputAsker(presets={"vault_dir": "/example/v"}), _cat())
    assert got == {"vault_dir": "/example/v"}


def test_no_input_refuses_a_default_on_a_question_it_was_not_given():
    """The arm the test above cannot reach, and the one the module comment is about.

    `NoInputAsker.ask` ends `return None` precisely so that a FUTURE question gaining a default
    does not get written into a config nobody was asked about. Against the real catalogue that is
    unfalsifiable: `vault_dir` is the only defaulting question and it always arrives as a preset,
    so the final arm never runs. Measured -- changing that `return None` to `return q.default` left
    the whole suite green, including the test above.

    A synthetic question is the only way to exercise it, so the guard is asserted against one."""
    from sluice.onboard.questions import Question, parse_csv
    future = Question("future_gate", "Something a later version asks?", parse_csv,
                      ("triage.future_gate",), "Want", default=["a value nobody chose"])
    assert collect(NoInputAsker(presets={}), (future,)) == {},         "--no-input took a default for a question it was never given an answer to"


def test_answers_are_parsed_not_stored_raw(tmp_path):
    script = "\n".join([str(tmp_path), "", "", "", "example role, other role", "", "", "", "450"]
                       + [""] * len(_cat()))
    got = collect(_tty(script), _cat())
    assert got["accept_titles"] == ["example role", "other role"]
    assert got["contract_floor"] == 450 and isinstance(got["contract_floor"], int)


def test_a_bad_answer_is_re_asked_on_a_tty(tmp_path):
    script = "\n".join([str(tmp_path), "", "", "", "", "", "", "", "yes", "450"]
                       + [""] * len(_cat()))
    asker = _tty(script)
    assert collect(asker, _cat())["contract_floor"] == 450
    assert "number" in asker.stdout.getvalue()


def test_editor_content_is_returned_when_the_editor_succeeds():
    def fake_run(argv):
        with open(argv[-1], "w", encoding="utf-8") as fh:
            fh.write("Example prose the user typed.\n")
        return 0
    assert edit_in_editor("prompt", editor="vi", run=fake_run) == "Example prose the user typed."


def test_every_editor_failure_mode_falls_back_to_the_scaffold():
    assert edit_in_editor("p", editor="vi", run=lambda a: 1) is None      # non-zero exit
    assert edit_in_editor("p", editor="vi", run=lambda a: 0) is None      # unchanged
    assert edit_in_editor("p", editor=None, run=lambda a: 0) is None      # unset

    def boom(argv):
        raise OSError("not found")
    assert edit_in_editor("p", editor="nope", run=boom) is None           # not installed


def test_a_users_markdown_headings_survive_the_editor():
    """The scaffold is written as `#` comment lines and stripped afterwards -- but stripping EVERY
    line starting with `#` silently deleted the user's own Markdown headings, in the one file whose
    entire purpose is prose they wrote. Only the exact scaffold lines are removed."""
    def fake_run(argv):
        with open(argv[-1], "w", encoding="utf-8") as fh:
            fh.write("# My background\n\nExample prose.\n\n## Detail\n\nMore.\n")
        return 0
    got = edit_in_editor("prompt", editor="vi", run=fake_run)
    assert got is not None
    assert "# My background" in got and "## Detail" in got and "Example prose." in got


def test_a_malformed_EDITOR_falls_back_rather_than_raising():
    """`shlex.split` raises ValueError on an unbalanced quote, and $EDITOR is user-supplied. The
    docstring promises EVERY failure mode returns None; without catching it, a stray quote in the
    variable crashed `sluice init` instead."""
    import shlex
    with pytest.raises(ValueError):                       # precondition: shlex really does raise
        shlex.split('vi "unbalanced')

    def never(argv):
        raise AssertionError("a malformed $EDITOR must not reach the runner")
    assert edit_in_editor("p", editor='vi "unbalanced', run=never) is None


def test_editor_command_is_split_not_shelled():
    seen = {}

    def fake_run(argv):
        seen["argv"] = argv
        return 1
    edit_in_editor("p", editor="code --wait", run=fake_run)
    assert seen["argv"][:2] == ["code", "--wait"]


def test_collect_profile_returns_only_answered_headings():
    got = collect_profile(_tty("Example background.\n\n\n\n\n"))
    assert got["who"] == "Example background."
    assert "target_shape" not in got


def test_the_asker_and_the_renderer_agree_on_the_profile_answer_keys():
    """A mismatch means a typed answer is silently dropped: `collect_profile`'s output goes straight
    to `build_plan(profile_answers=...)`, which looks each heading up by ITS key."""
    from sluice.onboard.ask import _PROFILE_QUESTIONS
    from sluice.onboard.plan import _PROFILE_PROMPTS
    assert {k for k, _ in _PROFILE_QUESTIONS} == {k for k, _ in _PROFILE_PROMPTS.values()}


def test_collect_sources_takes_ids_then_label_url_pairs_until_a_blank_label():
    from sluice.onboard.ask import collect_sources
    script = "example_board_a\nExample search\nhttps://example.invalid/jobs\n\n"
    got = collect_sources(_tty(script), ["example_board_a", "example_board_b"])
    assert got == {"example_board_a": {"enabled": True,
                            "searches": [["Example search", "https://example.invalid/jobs"]]}}


def test_a_bad_search_url_is_re_asked_not_dropped():
    """A mistyped board URL that is silently skipped is a source the user believes is configured
    and is not."""
    from sluice.onboard.ask import collect_sources
    script = "example_board_a\nExample search\nnot-a-url\nhttps://example.invalid/jobs\n\n"
    got = collect_sources(_tty(script), ["example_board_a"])
    assert got["example_board_a"]["searches"] == [["Example search", "https://example.invalid/jobs"]]


def test_no_selection_means_no_sources_block():
    from sluice.onboard.ask import collect_sources
    assert collect_sources(_tty("\n"), ["example_board_a"]) == {}


def test_an_unregistered_board_id_is_re_asked_not_silently_dropped():
    """Same reasoning as the URL: a typo'd id accepted-and-ignored leaves the user believing the
    board is selected."""
    from sluice.onboard.ask import collect_sources
    asker = _tty("example_board_x\nexample_board_a\n\n")
    assert set(collect_sources(asker, ["example_board_a", "example_board_b"])) == {"example_board_a"}
    assert "not a registered source" in asker.stdout.getvalue()


def test_no_input_selects_no_boards_so_the_two_paths_still_converge():
    from sluice.onboard.ask import collect_sources
    assert collect_sources(NoInputAsker(presets={}), ["example_board_a"]) == {}


def test_no_input_asks_no_prose_and_never_opens_an_editor(monkeypatch):
    """`--no-input` must reach tier 3 for every heading (the scaffold comment stays), not tier 2.

    Asserts the MECHANISM, not just the return value: `== {}` alone is satisfied by an
    implementation that shells out to `$EDITOR` and discards the result, so the second half of this
    test's name was previously unasserted. `subprocess.call` is the one door out."""
    import subprocess

    def boom(*a, **kw):
        raise AssertionError("--no-input must never launch a subprocess")

    monkeypatch.setattr(subprocess, "call", boom)
    assert collect_profile(NoInputAsker(presets={"vault_dir": "/example/v"})) == {}


def test_the_tty_asker_is_the_only_thing_that_can_open_an_editor(monkeypatch):
    """The hermeticity that actually holds today comes from `cmd_init` passing `editor=` only to
    `TtyAsker`, and nothing pinned it. With no editor configured, not even the TTY path spawns."""
    import io
    import subprocess

    def boom(*a, **kw):
        raise AssertionError("no editor is configured, so nothing may be launched")

    monkeypatch.setattr(subprocess, "call", boom)
    asker = TtyAsker(stdin=io.StringIO("\n" * 8), stdout=io.StringIO(), editor=None)
    assert collect_profile(asker) == {}
