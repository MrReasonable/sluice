"""Keep a coach eval away from the developer's real job hunt (spec: Scenario evals).

The SERVER gets a sandboxed environment (`server_env`, checked by `isolation_problems`); the
CLIENTS run `claude --restricted --strict-mcp-config` from an empty directory, and every run's
`system/init` event is checked (`check_init_event`) because that isolation was measured on
one Claude Code version. An unmeasured version is refused; re-measure it with the probe in
scripts/coach_eval/README.md and add it here with its date.
"""
import os
import re
import shutil
from pathlib import Path

MEASURED_VERSIONS = {"2.1.292": "2026-10-07", "2.1.294": "2026-10-08",
                     "2.1.295": "2026-10-08"}
SET_VARS = ("SLUICE_CONFIG", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "HOME")
EXTRA_UNSET = ("CAMOFOX_USER", "CAMOFOX_SESSION", "CAMOFOX_URL",
               "SLUICE_TELEGRAM_TOKEN", "SLUICE_TELEGRAM_CHAT", "VAULT_DIR")


def path_env_vars(root) -> set:
    """Every `env_var="..."` paths.resolve consults, read from the source as
    tests/test_path_sandbox.py reads it."""
    found = set()
    for py in Path(root, "sluice").rglob("*.py"):
        found |= set(re.findall(r'env_var\s*=\s*"([A-Z_]+)"', py.read_text(encoding="utf-8")))
    return found


def unset_vars(root) -> set:
    return path_env_vars(root) | set(EXTRA_UNSET)


def provider_env_vars() -> set:
    """Every credential and base-url variable a paid backend reads, derived from the
    registry the backends themselves are built from so a new provider cannot be missed."""
    from sluice.core.app import _PROVIDER_ENV
    return {v for names in _PROVIDER_ENV.values() for v in names}


def _path_without_claude(path) -> str:
    """`path` minus every directory in which `claude` resolves. The claude-max backend
    shells out to it, spending the owner's allowance; the server is exec'd by absolute
    interpreter path and does not need it."""
    keep = [d for d in (path or "").split(os.pathsep)
            if d and not shutil.which("claude", path=d)]
    return os.pathsep.join(keep)


def server_env(base, sandbox, root, *, vault_env=False) -> dict:
    sandbox = Path(sandbox)
    drop = unset_vars(root) | provider_env_vars()
    env = {k: v for k, v in base.items() if k not in drop}
    env["PATH"] = _path_without_claude(base.get("PATH", ""))
    env["SLUICE_CONFIG"] = str(sandbox / "config" / "config.yaml")
    for var, sub in (("XDG_CONFIG_HOME", "xdg-config"), ("XDG_STATE_HOME", "xdg-state"),
                     ("XDG_CACHE_HOME", "xdg-cache"), ("HOME", "home")):
        env[var] = str(sandbox / sub)
    if vault_env:
        env["VAULT_DIR"] = str(sandbox / "env-vault")
    return env


def _inside(path, sandbox):
    real, root = os.path.realpath(path), os.path.realpath(sandbox)
    return os.path.commonpath([real, root]) == root


def isolation_problems(env, sandbox, root, *, vault_env=False) -> list:
    must_set = set(SET_VARS) | ({"VAULT_DIR"} if vault_env else set())
    problems = [f"{v} is set and must not be" for v in sorted(unset_vars(root) - must_set)
                if v in env]
    problems += [f"{v} is set: a paid backend could be reached"
                 for v in sorted(provider_env_vars()) if v in env]
    if shutil.which("claude", path=env.get("PATH", "")):
        problems.append("`claude` resolves on PATH: the claude-max backend could spend")
    for v in sorted(must_set):
        if v not in env:
            problems.append(f"{v} is not set")
        elif not _inside(env[v], sandbox):
            problems.append(f"{v} points outside the sandbox")
    return problems


def check_init_event(event, *, tools, servers) -> list:
    problems = []
    version = event.get("claude_code_version")
    if version not in MEASURED_VERSIONS:
        problems.append(f"Claude Code {version} has not been measured for isolation")
    got_servers = {s.get("name") for s in event.get("mcp_servers") or []}
    if got_servers != set(servers):
        problems.append(f"MCP servers {sorted(got_servers)} are not exactly {sorted(servers)}")
    down = [s.get("name") for s in event.get("mcp_servers") or []
            if s.get("status") != "connected"]
    if down:
        problems.append(f"MCP servers not connected: {down}")
    if set(event.get("tools") or []) != set(tools):
        problems.append(f"tools {sorted(event.get('tools') or [])} are not exactly {sorted(tools)}")
    if any(p.get("path") != "builtin" for p in event.get("plugins") or []):
        problems.append("a non-builtin plugin is loaded")
    return problems
