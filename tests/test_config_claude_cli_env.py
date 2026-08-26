"""`SLUICE_CLAUDE_HOST`/`SLUICE_CLAUDE_PATH` reach all three sub-apps (#209).

WHY THIS EXISTS. `claude-max` shells out to the `claude` CLI, and the container image carries no
CLI, no Node and no ssh client -- so the flat-rate backend is `dead` there and triage, cv and
track are all blocked. Routing it at a host over SSH needs `claude_max_host`, and until this
change the ONLY way to set that was a mounted `config.yaml`, because none of the three sub-app
key pairs was settable any other way. WHERE an external binary lives is a deployment fact, the
same category as `CAMOFOX_URL`, and it earns an env override for the same reason.

ONE variable pair, not six: the three key pairs stay separate so the sub-apps CAN differ, but
nobody varies the CLI's location per sub-app in the case this exists for.
"""
import os
import textwrap

import pytest

from sluice.cv.config import load_cv_config
from sluice.track.config import load_track_config
from sluice.triage.config import load_triage_config

# (loader, host attribute, path attribute) -- cv spells its pair differently, which is exactly
# the kind of divergence a single hand-written assertion would have missed on one of the three.
_LOADERS = [
    (load_triage_config, "claude_max_host", "claude_max_path"),
    (load_cv_config, "compose_host", "compose_claude_path"),
    (load_track_config, "claude_max_host", "claude_max_path"),
]


def _clear(monkeypatch):
    for var in ("SLUICE_CLAUDE_HOST", "SLUICE_CLAUDE_PATH"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("loader,host_attr,path_attr", _LOADERS)
def test_the_env_pair_reaches_every_sub_app_with_no_config_file(monkeypatch, tmp_path,
                                                                loader, host_attr, path_attr):
    """The container case: environment only, nothing mounted.

    `load_cv_config` keeps the early-return guard that triage's and track's shed in #80, so
    "no config file" leaves by a different exit -- and a tail-position override was measured
    DEAD on exactly this path before the loader was given a single exit. Every loader is
    parametrised rather than spot-checked for that reason.
    """
    _clear(monkeypatch)
    monkeypatch.setenv("SLUICE_CLAUDE_HOST", "example-host")
    monkeypatch.setenv("SLUICE_CLAUDE_PATH", "example-cli")
    cfg = loader(str(tmp_path / "absent.yaml"))
    assert getattr(cfg, host_attr) == "example-host"
    assert getattr(cfg, path_attr) == "example-cli"


@pytest.mark.parametrize("loader,host_attr,path_attr", _LOADERS)
def test_the_env_pair_beats_a_config_file(monkeypatch, tmp_path, loader, host_attr, path_attr):
    """The documented layering: code defaults < YAML < environment.

    Applied AFTER the file pass, so a config file that names a different host does not win. The
    reverse ordering would look identical on an unconfigured machine and silently ignore the
    environment on a configured one.
    """
    _clear(monkeypatch)
    f = tmp_path / "config.yaml"
    f.write_text(textwrap.dedent("""\
        triage:
          claude_max_host: from-file
          claude_max_path: from-file-cli
        cv:
          compose_host: from-file
          compose_claude_path: from-file-cli
        track:
          claude_max_host: from-file
          claude_max_path: from-file-cli
        """), encoding="utf-8")
    assert getattr(loader(str(f)), host_attr) == "from-file", "fixture did not configure the host"

    monkeypatch.setenv("SLUICE_CLAUDE_HOST", "env-wins")
    monkeypatch.setenv("SLUICE_CLAUDE_PATH", "env-wins-cli")
    cfg = loader(str(f))
    assert getattr(cfg, host_attr) == "env-wins"
    assert getattr(cfg, path_attr) == "env-wins-cli"


@pytest.mark.parametrize("loader,host_attr,path_attr", _LOADERS)
def test_an_empty_or_blank_env_var_is_ignored(monkeypatch, tmp_path, loader, host_attr, path_attr):
    """Exporting a variable to the empty string is how a shell says NOTHING.

    Reading it as an instruction would silently undo a configured remote host and send every
    completion to a local `claude` that does not exist in a container -- the quiet-wrong-default
    class this codebase engineers out. Whitespace counts as empty for the same reason.
    """
    _clear(monkeypatch)
    f = tmp_path / "config.yaml"
    # BOTH keys per block. An earlier version configured only the hosts and asserted only
    # `host_attr`, which left the `if path:` half of the helper unwitnessed: a mutant letting a
    # blank `SLUICE_CLAUDE_PATH` overwrite `claude_max_path` with "" passed the whole suite. A
    # blank PATH is the worse of the two -- it would send every completion to a binary named ""
    # rather than merely losing a host.
    f.write_text("triage:\n  claude_max_host: configured\n  claude_max_path: configured-cli\n"
                 "cv:\n  compose_host: configured\n  compose_claude_path: configured-cli\n"
                 "track:\n  claude_max_host: configured\n  claude_max_path: configured-cli\n",
                 encoding="utf-8")
    for blank in ("", "   "):
        monkeypatch.setenv("SLUICE_CLAUDE_HOST", blank)
        monkeypatch.setenv("SLUICE_CLAUDE_PATH", blank)
        cfg = loader(str(f))
        assert getattr(cfg, host_attr) == "configured", f"blank host {blank!r} overrode"
        assert getattr(cfg, path_attr) == "configured-cli", f"blank path {blank!r} overrode"


def test_the_env_names_are_not_silently_renamed():
    """The two names are what docs, compose and .env all spell out; a rename must break here."""
    from sluice.core import config as c
    assert c._CLAUDE_HOST_ENV == "SLUICE_CLAUDE_HOST"
    assert c._CLAUDE_PATH_ENV == "SLUICE_CLAUDE_PATH"
    assert not os.environ.get("SLUICE_CLAUDE_HOST"), "test env leaked a real host"


def test_a_renamed_config_field_raises_rather_than_creating_a_dead_attribute(monkeypatch):
    """The failure this whole file cannot otherwise see.

    Every case above hand-lists the attribute names it then reads back with `getattr`, so a
    RENAME in `cv/config.py` would leave the test writing and reading the same dead literal while
    the env var silently stopped reaching that sub-app. A bare `setattr` creates whatever name it
    is given; only a `hasattr` check can notice.
    """
    from dataclasses import dataclass

    from sluice.core.config import apply_claude_cli_env

    @dataclass
    class Renamed:
        compose_host: str = ""
        # `compose_claude_path` deliberately absent -- this models the rename.

    monkeypatch.setenv("SLUICE_CLAUDE_HOST", "example-host")
    with pytest.raises(AttributeError, match="compose_claude_path"):
        apply_claude_cli_env(Renamed(), host_attr="compose_host",
                             path_attr="compose_claude_path")
