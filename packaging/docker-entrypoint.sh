#!/bin/sh
# Container entrypoint (#209): prepare SSH for the claude-max backend, then run the CLI.
#
# WHY THIS EXISTS AT ALL. `claude-max` is the shipped default for primary_backend and shells out
# to the `claude` CLI, which this image deliberately does not carry. `ClaudeMaxBackend` reaches
# one on another host with plain `ssh <host> <argv>` -- no flags of its own -- so everything ssh
# needs must already be in place: a key it will find, a username, and a known_hosts policy.
#
# WHY A COPY RATHER THAN A BIND MOUNT STRAIGHT TO ~/.ssh. Two reasons, both measured. A bind
# mount carries the HOST's uid, and ssh refuses a private key it does not consider owned by the
# user running it. And mounting a FILE into ~/.ssh makes docker create that directory root-owned,
# so ssh cannot then write known_hosts -- which surfaces as "Host key verification failed" even
# with StrictHostKeyChecking=accept-new, a message that sends you looking at the wrong thing.
#
# Everything here is a no-op unless a non-empty key is mounted at KEY_SRC below, so an install that
# uses an API-key backend is untouched.
set -eu

# FIXED, not overridable, and that is the fix rather than a limitation. `SLUICE_CLAUDE_KEY`
# names a path on the HOST and is compose's bind-mount SOURCE; this is the TARGET inside the
# container. Letting one variable mean both was a collision a comment could not hold: compose's
# `env_file: .env` injects everything in .env regardless of the `environment:` block, and
# docs/INSTALL.md tells the reader to put SLUICE_CLAUDE_KEY there -- so the documented setup
# handed this line a host path that does not exist in the container, `-s` was false, the whole
# setup was skipped, and ssh failed with "Host key verification failed". Verified with
# `docker compose config`, which rendered the host path straight into the container environment.
KEY_SRC=/run/secrets/sluice_claude_key

# A floor for EVERY ssh this container makes, written before the checks below and regardless of
# how -- or whether -- the claude host was configured.
#
# The refusals further down all key on SLUICE_CLAUDE_HOST, but that is not the only route: a
# `claude_max_host` in a mounted config.yaml reaches sluice without any of them firing, and
# docs/CONFIGURATION.md says in terms that a mounted file works. On that route no per-host block
# is written at all -- and because this image now ships an ssh CLIENT, what used to fail
# instantly ("no ssh binary") became a client that can sit waiting on a passphrase or a host-key
# confirmation with no tty to answer it, bounded only by the backend's timeout and repeated per
# role. `BatchMode yes` turns every such prompt into an immediate error instead.
#
# A `Host *` block, so the specific block appended below still wins where it applies.
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
{
    printf 'Host *\n'
    printf '  BatchMode yes\n'
} > "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"

# `-s` (exists AND non-empty), not `-r`. docker-compose.yml mounts /dev/null here when no key is
# configured -- a bind mount source must exist, and /dev/null is the portable "nothing" -- and
# /dev/null IS readable, so `-r` fired on every run and refused to start with the message below.
# Measured, not reasoned about: `docker compose run --rm job-sluice --version` failed on a
# perfectly ordinary API-key install.
if [ -n "${SLUICE_CLAUDE_HOST:-}" ] && [ ! -s "$KEY_SRC" ]; then
    echo "sluice: SLUICE_CLAUDE_HOST is set but no ssh key is mounted at $KEY_SRC." >&2
    echo "        sluice will try to run 'ssh $SLUICE_CLAUDE_HOST ...' and fail on" >&2
    echo "        authentication. Set SLUICE_CLAUDE_KEY to the key's path on your machine" >&2
    echo "        (compose mounts it here), or unset SLUICE_CLAUDE_HOST." >&2
    exit 1
fi

if [ -s "$KEY_SRC" ]; then
    if [ -z "${SLUICE_CLAUDE_HOST:-}" ]; then
        echo "sluice: an ssh key is mounted but SLUICE_CLAUDE_HOST is not -- the key would" >&2
        echo "        never be used. Set the host, or unset the key." >&2
        exit 1
    fi
    if [ -z "${SLUICE_CLAUDE_SSH_USER:-}" ]; then
        echo "sluice: an ssh key is mounted but SLUICE_CLAUDE_SSH_USER is not. The container" >&2
        echo "        user is 'sluice'; your host account is almost certainly named otherwise," >&2
        echo "        and ssh would offer the wrong username with no useful error." >&2
        exit 1
    fi

    # Both values are interpolated into ssh_config below, one per line, so a NEWLINE in either
    # injects further directives -- `ProxyCommand` included. No trust boundary is crossed (the
    # values come from the operator's own .env, not from the container's peer), but the failure
    # is silent either way: a stray space yields a `Host` block that never matches and surfaces
    # much later as "Permission denied (publickey)". Refusing here names the cause.
    for pair in "SLUICE_CLAUDE_HOST=$SLUICE_CLAUDE_HOST" \
                "SLUICE_CLAUDE_SSH_USER=$SLUICE_CLAUDE_SSH_USER"; do
        value=${pair#*=}
        case "$value" in
            # WHITESPACE and CONTROL characters only, plus a leading dash. An earlier version
            # allow-listed `[A-Za-z0-9._-]`, which is wider than the harm it names: it refused
            # an IPv6 literal (`::1`, `fe80::1%eth0`) outright, and the message listed permitted
            # characters, so the only actionable reading for that user was "stop using ssh". The
            # stated harms are a newline (which injects further ssh_config directives) and a
            # stray space (which yields a Host block that never matches and surfaces much later
            # as "Permission denied"); neither reaches a colon.
            *[[:space:][:cntrl:]]*)
                echo "sluice: ${pair%%=*} must not contain whitespace or control characters." >&2
                echo "        It is written into ssh_config one directive per line, where a" >&2
                echo "        newline would silently change what ssh does and a stray space" >&2
                echo "        yields a Host block that never matches." >&2
                exit 1
                ;;
            # A leading dash is read by ssh as an OPTION rather than a destination. sluice's own
            # ClaudeMaxBackend already refuses this at construction (`option_like`), so this is
            # defence in depth rather than the only check -- but the entrypoint writes the value
            # into ssh_config before sluice ever sees it.
            -*)
                echo "sluice: ${pair%%=*} must not begin with '-'; ssh reads a leading dash as" >&2
                echo "        an option rather than a destination." >&2
                exit 1
                ;;
        esac
    done

    # The state directory may not exist yet on a first run; ssh will not create the parent of
    # UserKnownHostsFile for itself, and failing to write it degrades silently to no host-key
    # memory at all -- the exact thing this is here to prevent.
    mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/sluice"
    install -m 600 "$KEY_SRC" "$HOME/.ssh/id_sluice_claude"

    # Written every start rather than only when absent: the host or user can change between runs
    # of a `--rm` container, and a stale config would silently keep the old one.
    {
        printf 'Host %s\n' "$SLUICE_CLAUDE_HOST"
        printf '  User %s\n' "$SLUICE_CLAUDE_SSH_USER"
        printf '  IdentityFile %s/.ssh/id_sluice_claude\n' "$HOME"
        printf '  IdentitiesOnly yes\n'
        # accept-new, not `no`: it pins the host key on first contact and refuses a CHANGED one
        # thereafter, where `no` would accept any key forever. The residual is trust-on-first-use
        # against a host on your own machine; `docs/INSTALL.md` says so rather than implying the
        # connection is authenticated from the start.
        printf '  StrictHostKeyChecking accept-new\n'
        # PERSISTED, under the state volume rather than in ~/.ssh. `docker compose run --rm`
        # deletes the container layer, so a known_hosts in $HOME starts EMPTY on every run --
        # measured, one entry after each of two consecutive runs -- which makes accept-new
        # trust-on-first-use EVERY time and silently accepts a changed host key. Writing it
        # under XDG_STATE_HOME/sluice, which compose already backs with a named volume, is what
        # makes "refuses a changed key" true rather than merely documented.
        printf '  UserKnownHostsFile %s/sluice/ssh_known_hosts\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
        # No prompting, ever: a hung password prompt inside a container looks like a hang, not a
        # misconfiguration, and the backend's own timeout is what would eventually fire.
        printf '  BatchMode yes\n'
    } >> "$HOME/.ssh/config"
    chmod 600 "$HOME/.ssh/config"
fi

exec job-sluice "$@"
