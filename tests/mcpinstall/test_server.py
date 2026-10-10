import ast
import os
import pathlib

import pytest

import sluice
from sluice.core import paths
from sluice.mcpinstall import server
from tests.conftest import PATH_ENV_VARS


def _exe(path: pathlib.Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_the_launcher_is_argv0_made_absolute(tmp_path, monkeypatch):
    _exe(tmp_path / "bin" / "job-sluice")
    monkeypatch.chdir(tmp_path)
    assert server.resolve_launcher("bin/job-sluice", windows=False) == str(
        tmp_path / "bin" / "job-sluice")


def test_a_launcher_reached_through_a_symlink_keeps_the_link_path(tmp_path):
    target = _exe(tmp_path / "Cellar" / "4.3.1" / "bin" / "job-sluice")
    link = tmp_path / "bin" / "job-sluice"
    link.parent.mkdir()
    link.symlink_to(target)
    assert server.resolve_launcher(str(link), windows=False) == str(link)


@pytest.mark.parametrize("name", ["cli.py", "__main__.py", "python3"])
def test_anything_but_the_installed_launcher_is_refused(tmp_path, name):
    with pytest.raises(server.LauncherError) as exc:
        server.resolve_launcher(_exe(tmp_path / name), windows=False)
    assert "job-sluice" in str(exc.value) and str(tmp_path) not in str(exc.value)


def test_a_launcher_that_is_not_executable_is_refused(tmp_path):
    p = tmp_path / "job-sluice"
    p.write_text("")
    p.chmod(0o644)
    with pytest.raises(server.LauncherError):
        server.resolve_launcher(str(p), windows=False)


def test_the_spec_runs_the_launcher_with_mcp_serve_and_write():
    spec = server.build_spec("/opt/x/job-sluice", {}, write=True)
    assert spec.argv == ("/opt/x/job-sluice", "mcp", "serve", "--write")
    assert server.build_spec("/opt/x/job-sluice", {}, write=False).argv[-1] == "serve"


def test_a_pinned_path_is_made_absolute_and_an_unset_one_is_not_pinned():
    spec = server.build_spec("/opt/x/job-sluice", {"SLUICE_CONFIG": "~/x.yaml",
                                                   "OPENAI_API_KEY": "SENTINEL-NOT-A-SECRET-2"},
                             write=True)
    assert spec.env_dict == {"SLUICE_CONFIG": os.path.join(os.path.expanduser("~"), "x.yaml")}


def test_every_pinned_name_is_carried_not_only_the_config():
    env = {k: f"/state/{k.lower()}" for k in server.PINNED_ENV}
    assert set(server.build_spec("/x/job-sluice", env, write=True).env_dict) == set(
        server.PINNED_ENV)


def _derived_pinned_env() -> set:
    """Every env var sluice reads a relocating path from, derived from source: `env_var=`
    keywords passed to `core/paths.py::resolve` under ANY local binding (aliases included),
    every `os.environ` read of VAULT_DIR, and the XDG base variables `paths._ROOTS` reads."""
    pkg = pathlib.Path(sluice.__file__).parent
    found = set()
    for py in sorted(pkg.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        funcs = {"resolve"} if py.name == "paths.py" and py.parent.name == "core" else set()
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sluice.core.paths":
                funcs |= {a.asname or a.name for a in node.names if a.name == "resolve"}
            if isinstance(node, ast.ImportFrom) and node.module == "sluice.core":
                mods |= {a.asname or a.name for a in node.names if a.name == "paths"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_resolve = (isinstance(f, ast.Name) and f.id in funcs) or (
                isinstance(f, ast.Attribute) and f.attr == "resolve"
                and isinstance(f.value, ast.Name) and f.value.id in mods)
            if is_resolve:
                for kw in node.keywords:
                    if kw.arg == "env_var" and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        found.add(kw.value.value)
            if isinstance(f, ast.Attribute) and f.attr == "get" and ast.unparse(
                    f.value) == "os.environ" and node.args and isinstance(
                    node.args[0], ast.Constant) and node.args[0].value == "VAULT_DIR":
                found.add("VAULT_DIR")
    return found | {var for var, _ in paths._ROOTS.values()}


def test_pinned_env_is_every_relocating_path_variable_sluice_reads():
    derived = _derived_pinned_env()
    # SCOPE: a walk that found nothing, or missed an import form, cannot pass. The regex
    # sweep's roster is a second, independent engine over the same call sites.
    assert {"SLUICE_CONFIG", "SEEN_DB", "DOSSIER_DIR", "VAULT_DIR"} <= derived
    assert set(PATH_ENV_VARS) <= derived
    assert set(server.PINNED_ENV) == derived


def test_no_credential_is_ever_pinned():
    assert not any("KEY" in k or "TOKEN" in k or "TELEGRAM" in k for k in server.PINNED_ENV)


def test_a_relative_xdg_root_is_not_pinned():
    """`core/paths.py` ignores a relative XDG root (the XDG spec), so pinning its abspath would
    send the registered server to a store the user's own commands never use."""
    spec = server.build_spec("/x/job-sluice", {"XDG_STATE_HOME": "rel/state",
                                               "XDG_CACHE_HOME": "~/cache",
                                               "XDG_CONFIG_HOME": "/abs/cfg"}, write=True)
    assert spec.env_dict == {"XDG_CONFIG_HOME": "/abs/cfg"}
