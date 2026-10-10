"""What `mcp install` registers: the installed `job-sluice` launcher, `mcp serve`, `--write`
unless read-only, and sluice's relocating path variables as the install saw them.

The launcher is `abspath(argv0)` and NOT `realpath`: a Homebrew or pipx launcher is a symlink
into a versioned folder the next upgrade deletes, so the link is what survives.

The pinned variables exist because a client started from a desktop never sees the shell's
exports: without them its server would open a different config or vault from the one the user
was running when install said `registered`. `PINNED_ENV` is a literal; the test
`tests/mcpinstall/test_server.py::test_pinned_env_is_every_relocating_path_variable_sluice_reads`
derives the same set from source and fails when they differ. Credentials are never pinned."""
import os
from collections.abc import Mapping
from dataclasses import dataclass

SERVER_NAME = "job-sluice"
# sluice's own argument vocabulary: the only elements of an EXISTING entry's argv the report
# prints in clear (besides the executable), since an entry a user wrote can carry a token.
SLUICE_ARGS = frozenset({"mcp", "serve", "--write"})

PINNED_ENV = (
    "DOSSIER_DIR", "SEEN_DB", "SLUICE_CONFIG", "SLUICE_DISABLED", "SLUICE_FX_CACHE",
    "SLUICE_HEALTH", "SLUICE_USAGE", "TRIAGE_AUDIT", "VAULT_DIR",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
)


@dataclass(frozen=True)
class ServerSpec:
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]   # sorted by name

    @property
    def env_dict(self) -> dict:
        return dict(self.env)


class LauncherError(ValueError):
    """Install was not started as the installed `job-sluice`; `str()` is printable."""


def resolve_launcher(argv0: str, *, windows: bool) -> str:
    path = os.path.abspath(argv0)
    names = ("job-sluice", "job-sluice.exe", "job-sluice.cmd") if windows else ("job-sluice",)
    # Unmeasured on Windows: a console-script launcher may report itself without `.exe`.
    if windows and not os.path.splitext(path)[1] and os.path.isfile(path + ".exe"):
        path += ".exe"
    base = os.path.basename(path).lower() if windows else os.path.basename(path)
    if base not in names or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise LauncherError(
            f"this was started as {os.path.basename(argv0)!r}, which a client cannot start; "
            "run the installed `job-sluice mcp install` instead")
    return path


def _honoured(name: str, value: str) -> bool:
    """`core/paths.py` IGNORES a relative XDG root (the XDG base-directory spec: `~` included,
    since it checks the raw value), so pinning its abspath would point the registered server at
    a store the user's own commands never use. Every other pinned variable is a path sluice
    expands and uses as given."""
    return not name.startswith("XDG_") or os.path.isabs(value)


def build_spec(launcher: str, env: Mapping[str, str], write: bool) -> ServerSpec:
    argv = (launcher, "mcp", "serve") + (("--write",) if write else ())
    pinned = tuple(sorted((k, os.path.abspath(os.path.expanduser(env[k])))
                          for k in PINNED_ENV if env.get(k) and _honoured(k, env[k])))
    return ServerSpec(argv, pinned)
