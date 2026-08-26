"""The container entrypoint, exercised as a program (#209).

WHY. `packaging/docker-entrypoint.sh` decides whether ssh is configured at all, and every failure
mode it has looks like something else from the outside: a skipped setup surfaces later as ssh's
"Host key verification failed", which points at host keys rather than at a variable that was never
read. Two of its three guards were added only after a reviewer found the silent-skip path, and the
`-r`/`-s` distinction was found by an ordinary API-key install refusing to start. None of that is
reachable from `pytest` unless the script is actually run.

Run under `/bin/sh` with `job-sluice` stubbed, so the assertions are about the entrypoint's own
decisions rather than the CLI's. Offline: no docker, no ssh, no network.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ENTRYPOINT = ROOT / "packaging" / "docker-entrypoint.sh"


@pytest.fixture
def entrypoint(tmp_path):
    """`run(env, key=...)` executes the entrypoint with `job-sluice` stubbed onto PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "ran.txt"
    stub = bin_dir / "job-sluice"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{marker}"\n')
    stub.chmod(0o700)

    home = tmp_path / "home"
    home.mkdir()
    key_target = tmp_path / "key"

    def run(env=None, key_contents=None):
        # The mount target the entrypoint reads. Absent by default; `/dev/null`-shaped (empty)
        # when compose has no key configured; real content when one is mounted.
        if key_contents is None:
            key_target.unlink(missing_ok=True)
        else:
            key_target.write_text(key_contents)
        script = ENTRYPOINT.read_text().replace(
            "KEY_SRC=/run/secrets/sluice_claude_key", f"KEY_SRC={key_target}")
        rendered = tmp_path / "entrypoint.sh"
        rendered.write_text(script)
        rendered.chmod(0o700)
        e = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home),
             "XDG_STATE_HOME": str(tmp_path / "state")}
        e.update(env or {})
        r = subprocess.run(["/bin/sh", str(rendered), "--version"],
                           capture_output=True, text=True, env=e, timeout=30)
        return r, home, marker

    return run


def test_the_entrypoint_ships_reading_a_fixed_container_path():
    """SCOPE, and the property that closed a real bug.

    `SLUICE_CLAUDE_KEY` names a path on the HOST and is compose's mount SOURCE. When the
    entrypoint also read it as an override, `env_file: .env` -- which injects everything in .env
    regardless of the `environment:` block -- handed it a host path that does not exist in the
    container, so setup was skipped silently. The target is fixed for that reason; if this
    assertion fails because someone reintroduced an override, that bug is back.
    """
    text = ENTRYPOINT.read_text()
    assert "KEY_SRC=/run/secrets/sluice_claude_key" in text
    assert "${SLUICE_CLAUDE_KEY" not in text, "the entrypoint must not read the host-side variable"


def test_no_key_is_a_clean_no_op(entrypoint):
    """The default install. An API-key user pays nothing for this machinery being present."""
    r, home, marker = entrypoint()
    assert r.returncode == 0, r.stderr
    assert marker.read_text().strip() == "--version", "did not exec job-sluice"
    # A `Host *` BatchMode floor IS written even with no key, deliberately: `claude_max_host` can
    # reach sluice from a mounted config.yaml without touching any variable the guards key on,
    # and this image now ships an ssh client that would otherwise sit on a prompt with no tty.
    # What must NOT exist is a key or a per-host block.
    cfg = (home / ".ssh" / "config").read_text()
    assert cfg.splitlines()[0] == "Host *" and "BatchMode yes" in cfg
    assert "IdentityFile" not in cfg, "wrote a per-host block with no key supplied"
    assert not (home / ".ssh" / "id_sluice_claude").exists()


def test_an_empty_key_file_is_treated_as_no_key(entrypoint):
    """`/dev/null` is what compose mounts when no key is configured, and it IS readable.

    An `[ -r ]` test therefore fired on every run and refused to start an ordinary API-key
    install -- measured, not hypothetical. `[ -s ]` is what distinguishes them.
    """
    r, home, marker = entrypoint(key_contents="")
    assert r.returncode == 0, r.stderr
    assert marker.exists(), "an empty key file blocked startup"
    assert not (home / ".ssh" / "id_sluice_claude").exists(), "installed an empty key"
    assert "IdentityFile" not in (home / ".ssh" / "config").read_text()


def test_a_key_without_a_host_refuses_rather_than_ignoring_it(entrypoint):
    r, _home, marker = entrypoint(key_contents="KEY")
    assert r.returncode != 0
    assert "SLUICE_CLAUDE_HOST is not" in r.stderr, r.stderr
    assert not marker.exists(), "started anyway"


def test_a_key_without_an_ssh_user_refuses(entrypoint):
    """The container user is `sluice`; a host account is almost never named that, and ssh would
    offer the wrong username with an error that names neither variable."""
    r, _home, marker = entrypoint({"SLUICE_CLAUDE_HOST": "example-host"}, key_contents="KEY")
    assert r.returncode != 0
    assert "SLUICE_CLAUDE_SSH_USER is not" in r.stderr, r.stderr
    assert not marker.exists()


def test_a_host_without_a_key_refuses_rather_than_failing_later(entrypoint):
    """The configuration that used to do nothing at all.

    Both other guards sit inside the have-a-key branch, so a host set WITHOUT a key skipped setup
    silently and surfaced as an ssh authentication failure with nothing pointing at the cause.
    """
    r, home, marker = entrypoint({"SLUICE_CLAUDE_HOST": "example-host",
                                  "SLUICE_CLAUDE_SSH_USER": "example-user"})
    assert r.returncode != 0
    assert "no ssh key is mounted" in r.stderr, r.stderr
    assert not marker.exists()
    assert not (home / ".ssh" / "id_sluice_claude").exists()


def test_a_complete_configuration_writes_ssh_config_and_execs(entrypoint, tmp_path):
    """The working path, asserting the properties each line exists for."""
    r, home, marker = entrypoint({"SLUICE_CLAUDE_HOST": "example-host",
                                  "SLUICE_CLAUDE_SSH_USER": "example-user"},
                                 key_contents="PRIVATE-KEY-BODY")
    assert r.returncode == 0, r.stderr
    assert marker.read_text().strip() == "--version", "did not exec job-sluice"

    key = home / ".ssh" / "id_sluice_claude"
    assert key.read_text() == "PRIVATE-KEY-BODY"
    assert oct(key.stat().st_mode & 0o777) == "0o600", "key must not be group/world readable"
    assert oct((home / ".ssh").stat().st_mode & 0o777) == "0o700"

    cfg = (home / ".ssh" / "config").read_text()
    assert "Host example-host" in cfg and "User example-user" in cfg
    assert "IdentitiesOnly yes" in cfg, "without this ssh offers every key it can find"
    assert "BatchMode yes" in cfg, "a password prompt in a container reads as a hang"
    # PERSISTED under the state root, not $HOME: `docker compose run --rm` deletes the container
    # layer, so a known_hosts in $HOME starts empty every run and accept-new re-trusts blindly.
    assert f"UserKnownHostsFile {tmp_path / 'state'}/sluice/ssh_known_hosts" in cfg
    assert (tmp_path / "state" / "sluice").is_dir(), "ssh will not create the parent itself"


def test_the_ssh_config_is_rewritten_each_start(entrypoint):
    """A `--rm` container can be re-run against a different host; a stale config would persist
    silently if it were only written when absent."""
    r, home, _ = entrypoint({"SLUICE_CLAUDE_HOST": "first-host",
                             "SLUICE_CLAUDE_SSH_USER": "example-user"}, key_contents="K")
    assert r.returncode == 0 and "Host first-host" in (home / ".ssh" / "config").read_text()
    r, home, _ = entrypoint({"SLUICE_CLAUDE_HOST": "second-host",
                             "SLUICE_CLAUDE_SSH_USER": "example-user"}, key_contents="K")
    cfg = (home / ".ssh" / "config").read_text()
    assert "Host second-host" in cfg and "first-host" not in cfg, "stale config survived"


def test_the_entrypoint_needs_no_bashisms():
    """It is the image's ENTRYPOINT and runs under whatever `/bin/sh` the base provides."""
    r = subprocess.run(["/bin/sh", "-n", str(ENTRYPOINT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_image_actually_uses_this_entrypoint():
    """Every other test here runs the SCRIPT; this one pins that the IMAGE runs it.

    Predicted green by a reviewer and confirmed: deleting the `COPY` and restoring
    `ENTRYPOINT ["job-sluice"]` left all nine behavioural tests passing, because none of them can
    see the Dockerfile. Testing the component while nothing binds it to its call site is this
    repo's recurring failure (#170), and it had reappeared in the file written to close a
    coverage gap.
    """
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY --chown=sluice:sluice packaging/docker-entrypoint.sh" in dockerfile, (
        "the Dockerfile no longer copies the entrypoint, so the image runs without any of the "
        "ssh setup these tests exercise")
    assert 'ENTRYPOINT ["/usr/local/bin/sluice-entrypoint"]' in dockerfile
    # .dockerignore is deny-all; without the re-include the COPY cannot see the file at all.
    assert "!packaging/docker-entrypoint.sh" in (ROOT / ".dockerignore").read_text()


def test_the_key_mount_target_matches_what_the_entrypoint_reads():
    """`/run/secrets/sluice_claude_key` is written in two files that never see each other.

    compose mounts TO it; the entrypoint reads FROM it. Nothing connected them, so renaming
    either side left the suite green and the key silently unread -- which is exactly the failure
    this PR already shipped once, by a different route (the host-path/container-path collision).
    """
    import re
    ep = (ROOT / "packaging" / "docker-entrypoint.sh").read_text()
    m = re.search(r"^KEY_SRC=(\S+)", ep, re.M)
    assert m, "could not find KEY_SRC; this guard would be vacuous"
    target = m.group(1)
    compose = (ROOT / "docker-compose.yml").read_text()
    assert f"target: {target}" in compose, (
        f"the entrypoint reads {target} but docker-compose.yml mounts the key somewhere else; "
        f"the key would never be read and ssh would fail on authentication")


def test_every_ssh_gets_a_batchmode_floor_even_with_no_key(entrypoint):
    """`BatchMode yes` must bind ssh however the host was configured, not only via the env vars.

    Every refusal in this script keys on `SLUICE_CLAUDE_HOST`, but `claude_max_host` in a mounted
    `config.yaml` reaches sluice without touching it -- and `docs/CONFIGURATION.md` says a mounted
    file works. On that route none of the guards fire and no per-host block is written. Before
    this image shipped an ssh client that failed instantly; now the client exists, so without a
    floor it can sit waiting on a passphrase or a host-key confirmation with no tty to answer,
    bounded only by the backend's timeout and repeated once per role.
    """
    r, home, _marker = entrypoint()
    assert r.returncode == 0, r.stderr
    cfg = (home / ".ssh" / "config").read_text()
    assert cfg.splitlines()[0] == "Host *", f"the floor must come first: {cfg!r}"
    assert "BatchMode yes" in cfg

    # And it survives a fully-configured run, with the specific block appended after it.
    r, home, _ = entrypoint({"SLUICE_CLAUDE_HOST": "example-host",
                             "SLUICE_CLAUDE_SSH_USER": "example-user"}, key_contents="K")
    assert r.returncode == 0, r.stderr
    cfg = (home / ".ssh" / "config").read_text()
    assert cfg.index("Host *") < cfg.index("Host example-host"), (
        "the specific block must come AFTER the floor, or ssh_config's first-match-wins would "
        "let the floor shadow it")
    assert cfg.count("BatchMode yes") >= 1
