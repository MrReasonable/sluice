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


def test_help_output_carries_no_escape_sequences(capsys, monkeypatch):
    """argparse colourises help on 3.14, and `main()` now escapes control characters on their way
    out -- so unsuppressed colour would reach the user as literal `\\x1b[1;34m` text on the
    most-run command. Asserting on the rendered output rather than on the kwarg keeps this true
    on 3.12/3.13, where argparse never colours and the kwarg does not exist.

    `PYTHON_COLORS=1` forces `_colorize.can_colorize()` true regardless of the runner: without it
    this test's subject (`cli._ARGPARSE_COLOR`) is never actually exercised under pytest's capture,
    which is not a tty, and CI (no `FORCE_COLOR`) hits exactly that blind spot -- measured by
    deleting `_ARGPARSE_COLOR`'s effect and confirming this test still passed with no color env set
    at all. `PYTHON_COLORS` is checked first in `can_colorize`, ahead of `NO_COLOR`/`TERM=dumb`/the
    isatty probe, so it is the one override that cannot be defeated by the runner's own environment."""
    monkeypatch.setenv("PYTHON_COLORS", "1")
    before = _streams()
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert out, "no help output captured"
    assert "\\x1b" not in out and "\x1b" not in out, (
        "help output carries an escape sequence -- raw would drive the terminal, literal would "
        "render as garbage")
    assert _streams() == before
