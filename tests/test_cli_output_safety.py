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
