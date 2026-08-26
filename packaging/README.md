# `packaging/`

Artefacts that reach a user by some route **other than `pip install`**. Nothing here is imported
by `sluice/`, none of it ships in the wheel, and each file is installed by a different thing —
which is the distinction worth keeping, because it decides who owns the file's lifecycle and what
can be assumed about the environment it runs in.

| File | Installed by | Runs where |
|---|---|---|
| `job-sluice` | the `.deb` / `.rpm`, to `/usr/bin/job-sluice` | the user's machine, on the distro's `python3` |
| `docker-entrypoint.sh` | the `Dockerfile`, baked into the image | inside the container, as its `ENTRYPOINT` |
| `claude-max-ssh-wrapper.sh` | **the user, by hand**, following `docs/INSTALL.md` | the user's machine, as an sshd forced command |

The third row is the odd one and the reason this file exists. It is the only artefact here that
this project never installs: it is fetched from a URL, has its placeholders substituted, and is
named from the user's own `~/.ssh/authorized_keys`. That has two consequences a contributor should
know before editing it.

**It is version-coupled to `sluice/core/backends.py`.** The wrapper reconstructs the argv
`ClaudeMaxBackend` sends, so a flag added on one side and not the other is a silent behaviour
difference on the ssh path only. `tests/test_claude_max_ssh_wrapper.py` binds the two — both the
flag set and the deny-list — and will fail rather than let them drift.

**It cannot assume bash.** sshd runs a forced command under whatever shell the account has, and
this repo has already shipped a bash-4 substitution that macOS's bash 3.2 rejected. Both scripts
here are POSIX `sh`, checked by `shellcheck -s sh` and by a test that runs `sh -n` on them.

A fourth file belongs here if, and only if, it is installed by something other than pip. If it is
imported by `sluice/`, it belongs in `sluice/`; if it is a developer tool, it belongs in
`scripts/`.
