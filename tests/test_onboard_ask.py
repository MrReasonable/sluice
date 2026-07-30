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


def test_no_input_asks_no_prose_and_never_opens_an_editor():
    """`--no-input` must reach tier 3 for every heading (the scaffold comment stays), not tier 2."""
    assert collect_profile(NoInputAsker(presets={"vault_dir": "/example/v"})) == {}
