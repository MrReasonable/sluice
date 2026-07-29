"""The suite's own path sandbox (#80).

Before this change every path default was cwd-relative and the suite ran in `tmp_path`,
so nothing could reach a real home directory. Afterwards an unpinned variable sends
resolution to the developer's ACTUAL `~/.config/sluice/` -- and the test passes while
doing it. Two escapes, and the read one is the dangerous half: seven test files pin
config isolation by ABSENCE via `delenv("SLUICE_CONFIG")`, which after this change no
longer means "no config file" but "read `~/.config/sluice/config.yaml`" -- the file
`sluice init` (#8) writes.

`XDG_CONFIG_HOME` and `HOME` are BOTH load-bearing, and neither substitutes for the
other: they are two rungs of one fallback chain, so whichever is consulted depends on
the machine. On macOS -- which this design deliberately puts on XDG -- `XDG_CONFIG_HOME`
is conventionally unset, so `HOME` is the only pin standing between the neutrality guard
and a real config.
"""
import os
import pathlib
import re

import pytest

from sluice.core import paths
from tests.conftest import PATH_ENV_VARS


def _under(child, parent):
    return os.path.commonpath([os.path.realpath(child),
                               os.path.realpath(parent)]) == os.path.realpath(parent)


def test_config_resolution_stays_inside_the_sandbox(tmp_path):
    """Asserts resolution lands UNDER a pinned root rather than planting a file
    anywhere real -- a guard that has to write into a home directory to prove itself
    is not a guard."""
    out = paths.resolve(env_var=None, config_value="", kind="config",
                        name="config.yaml")
    assert _under(out, tmp_path), f"config resolution escaped the sandbox: {out}"


def test_every_kind_stays_inside_the_sandbox(tmp_path):
    for kind in ("config", "state", "cache"):
        out = paths.resolve(env_var=None, config_value="", kind=kind, name="n")
        assert _under(out, tmp_path), f"{kind} escaped the sandbox: {out}"


def test_home_is_pinned_so_the_xdg_unset_branch_cannot_escape(tmp_path, monkeypatch):
    """The macOS-relevant half. With XDG_CONFIG_HOME unset, resolution falls to
    `~/.config` -- so an unpinned HOME reaches the real one."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    out = paths.resolve(env_var=None, config_value="", kind="config",
                        name="config.yaml")
    assert _under(out, tmp_path), f"the ~ fallback escaped the sandbox: {out}"


def test_vault_dir_is_pinned(tmp_path):
    """Nothing pinned VAULT_DIR before this change. A developer with it exported ran
    the suite against their real vault, where `normalize_all_statuses` rewrites every
    note's status -- and CI, which has no VAULT_DIR, stayed green throughout."""
    from sluice.core.vault import Vault
    assert _under(Vault().dir, tmp_path)


def test_the_sandbox_covers_every_path_env_var():
    """`PATH_ENV_VARS` is a hand-list, so this pins it against the source.

    `paths.resolve` consults `env_var` BEFORE the config key and before the XDG root, so
    each of these outranks every pin the autouse fixture sets. A developer with
    `SLUICE_CONFIG` or `TRIAGE_AUDIT` exported ran the whole suite against a real config
    file or audit log -- passing, because nothing here looked. Every other sandbox test in
    this file passes `env_var=None`, which is exactly why none of them could see it.

    Discovery reads the call sites rather than importing, so it cannot be satisfied by a
    variable that is merely mentioned: the pattern is the `env_var=` keyword itself.
    """
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sluice"
    found = set()
    for py in sorted(pkg.rglob("*.py")):
        found |= set(re.findall(r'env_var\s*=\s*"([A-Z_]+)"', py.read_text(encoding="utf-8")))
    assert found, "discovery found no path env vars at all -- the pattern stopped matching"
    assert found == set(PATH_ENV_VARS), (
        "sluice/ resolves env vars the sandbox does not neutralise, so a developer with "
        f"one exported runs the suite against a real file: {sorted(found ^ set(PATH_ENV_VARS))}"
    )


@pytest.mark.parametrize("var", PATH_ENV_VARS)
def test_no_path_env_var_survives_into_a_test(var):
    # The behavioural half: the roster above can be complete and the fixture still not
    # apply it. Reads the live environment, so it fails on the machine that actually has
    # one exported rather than only in review.
    assert var not in os.environ, (
        f"{var} is set during a test; it outranks every sandbox pin, so this run is "
        f"resolving against whatever it points at")
