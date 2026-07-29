"""Per-system path resolution (#80).

Every test here controls its own environment explicitly rather than relying on the
autouse fixture, because these are the tests OF the resolver: a test that inherited
its answer from the sandbox could not tell a working resolver from a broken one.
"""
import os

import pytest

from sluice.core import paths


def test_state_file_uses_xdg_state_home(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    assert paths.resolve(env_var=None, config_value="", kind="state",
                         name="seen.db") == "/x/state/sluice/seen.db"


@pytest.mark.parametrize("kind,var,root", [
    ("config", "XDG_CONFIG_HOME", "/x/cfg"),
    ("state", "XDG_STATE_HOME", "/x/state"),
    ("cache", "XDG_CACHE_HOME", "/x/cache"),
])
def test_each_kind_resolves_under_its_own_xdg_root(monkeypatch, kind, var, root):
    monkeypatch.setenv(var, root)
    assert paths.resolve(env_var=None, config_value="", kind=kind,
                         name="n") == f"{root}/sluice/n"


@pytest.mark.parametrize("kind,tail", [
    ("config", ".config"), ("state", ".local/state"), ("cache", ".cache"),
])
def test_falls_back_under_home_when_xdg_unset(monkeypatch, kind, tail):
    """The macOS-relevant branch: XDG_* is conventionally unset there, so this is
    the path most users actually get."""
    for v in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", "/h")
    assert paths.resolve(env_var=None, config_value="", kind=kind,
                         name="n") == f"/h/{tail}/sluice/n"


def test_env_var_wins_over_both_config_and_xdg(monkeypatch):
    """Sets env AND config on the SAME call, so swapping the two operands in the
    chain reddens this. A row that sets only one cannot see the inversion."""
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.setenv("SEEN_DB", "/from/env")
    assert paths.resolve(env_var="SEEN_DB", config_value="/from/config",
                         kind="state", name="seen.db") == "/from/env"


def test_config_value_wins_over_xdg_when_env_unset(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.delenv("SEEN_DB", raising=False)
    assert paths.resolve(env_var="SEEN_DB", config_value="/from/config",
                         kind="state", name="seen.db") == "/from/config"


def test_empty_config_value_abstains(monkeypatch):
    """`""` means unset, so resolution falls through to XDG -- the blanked-default
    property the whole sweep depends on. A non-empty default short-circuits here and
    the XDG location is never reached."""
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.delenv("SEEN_DB", raising=False)
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db") == "/x/state/sluice/seen.db"


def test_resolving_creates_nothing(monkeypatch, tmp_path):
    """No writes at resolution time, or `--dry-run` would touch the disk."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    out = paths.resolve(env_var=None, config_value="", kind="state", name="seen.db")
    assert not os.path.exists(os.path.dirname(out))
    assert not os.path.exists(out)


def _legacy_setup(monkeypatch, tmp_path):
    """A planted legacy file and a resolved location that does not exist yet.

    Every negative row below plants the SAME file: without it, "does not raise" is
    satisfied by there being nothing to raise about, and the row passes for the wrong
    reason no matter what the resolver does.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.write_text("dedup state", encoding="utf-8")
    return str(legacy)


def test_legacy_file_warns_and_uses_the_resolved_path(monkeypatch, tmp_path, caplog):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                        name="seen.db", legacy=legacy)
    assert out == str(tmp_path / "state" / "sluice" / "seen.db")
    assert legacy in caplog.text and out in caplog.text


def test_legacy_file_is_never_moved(monkeypatch, tmp_path):
    """Never-move, pinned rather than asserted in a comment."""
    legacy = _legacy_setup(monkeypatch, tmp_path)
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                        name="seen.db", legacy=legacy)
    assert open(legacy, encoding="utf-8").read() == "dedup state"
    assert not os.path.exists(out)


def test_fatal_legacy_raises_naming_both_paths(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=legacy, fatal=True)
    assert legacy in str(e.value) and "mv" in str(e.value)
    assert open(legacy, encoding="utf-8").read() == "dedup state"


def test_fatal_legacy_silent_when_env_var_names_a_path(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("SEEN_DB", "/named/by/env")
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db", legacy=legacy, fatal=True) == "/named/by/env"


def test_fatal_legacy_silent_when_config_names_a_path(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    assert paths.resolve(env_var="SEEN_DB", config_value="/named/by/config",
                         kind="state", name="seen.db", legacy=legacy,
                         fatal=True) == "/named/by/config"


def test_legacy_silent_when_the_resolved_path_already_exists(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    resolved = tmp_path / "state" / "sluice" / "seen.db"
    resolved.parent.mkdir(parents=True)
    resolved.write_text("migrated", encoding="utf-8")
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db", legacy=legacy, fatal=True) == str(resolved)


def test_unknown_kind_raises_and_lists_the_valid_ones():
    """Fail loudly at construction: an unknown name raises and names the valid set
    rather than falling through to a default."""
    with pytest.raises(ValueError) as e:
        paths.resolve(env_var=None, config_value="", kind="data", name="n")
    assert "data" in str(e.value)
    for valid in ("config", "state", "cache"):
        assert valid in str(e.value)
