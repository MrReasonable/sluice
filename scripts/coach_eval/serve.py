"""The MCP server the eval's coach client launches. Claude Code passes its own environment
through to a server it spawns, so the sandbox is applied HERE: the environment is rebuilt,
checked, and only then is the real server exec'd, from an empty working directory."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Launched by absolute path from the client's empty working directory, so the repository root
# is put on sys.path here rather than relying on an MCP-config `cwd` key nobody has measured.
sys.path.insert(0, str(ROOT))

from scripts.coach_eval import isolation  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--vault-env", action="store_true")
    args = ap.parse_args(argv)
    sandbox = Path(args.sandbox)
    env = isolation.server_env(os.environ, sandbox, ROOT, vault_env=args.vault_env)
    problems = isolation.isolation_problems(env, sandbox, ROOT, vault_env=args.vault_env)
    if problems:
        # Refuse rather than exec: a server that reached the owner's real vault or dedup
        # store would make the eval write to live state.
        print("coach_eval: refusing to start the server: " + "; ".join(problems),
              file=sys.stderr)
        return 2
    cwd = sandbox / "server-cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    os.chdir(cwd)
    exe = os.path.join(os.path.dirname(sys.executable), "job-sluice")
    os.execve(exe, [exe, "mcp", "serve", "--write"], env)


if __name__ == "__main__":
    sys.exit(main())
