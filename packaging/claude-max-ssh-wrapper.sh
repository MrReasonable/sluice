#!/bin/sh
# Forced command for the sluice claude-max SSH key (#209).
#
# INSTALL THIS ON THE HOST, not in the container, and name it from the key's authorized_keys
# entry so sshd runs it INSTEAD of whatever the client asked for:
#
#   restrict,command="/absolute/path/to/claude-max-ssh-wrapper.sh" ssh-ed25519 AAAA... sluice
#
# `restrict` disables port/agent/X11 forwarding, PTY allocation and ~/.ssh/rc; `command=` means
# the client's own command never executes. Together they make a stolen container key able to do
# exactly one thing: run one `claude --print`.
#
# WHY THIS CONSTRUCTS THE COMMAND RATHER THAN CHECKING IT. sluice sends a full argv, including
# a `--disallowedTools` deny-list that is the only thing stopping a prompt from running shell
# commands on this machine. That list is NOT restated here: this comment named four tools while
# the exec line below denied seven, for exactly as long as nobody re-read both. The exec line is
# the one place it is written, and `test_the_wrappers_deny_list_covers_everything_sluice_denies`
# is what binds it to sluice's own. If the wrapper merely VALIDATED what
# arrived, then the deny-list would still be supplied by the caller -- and a caller that can
# reach this key is precisely who you are defending against. So the deny-list is written here,
# on the host, and the caller contributes nothing but two identifier-shaped values.
# `-f` disables PATHNAME EXPANSION, and it is load-bearing rather than tidy. The loop below
# splits $SSH_ORIGINAL_COMMAND unquoted on purpose -- and unquoted expansion GLOBS as well as
# splits. Measured before this flag existed, from a scratch copy run in a home directory:
#
#   SSH_ORIGINAL_COMMAND='--model *'      -> model=<a filename in the cwd>, charset-clean, exec'd
#   SSH_ORIGINAL_COMMAND='--effort */*'   -> "refusing --effort: sub/private-notes"
#
# The first silently substitutes a filename for the model. The second is worse: `core/backends.py`
# puts `proc.stderr` into the BackendError it raises, so the refusal message travels back to the
# caller -- turning a key that is supposed to be worth one `claude --print` into a directory
# enumeration primitive against the host account's home. shellcheck exits 0 on the globbing
# version; nothing but this flag catches it.
set -euf

# The claude binary, absolute. `ssh host cmd` runs a NON-INTERACTIVE, NON-LOGIN shell whose PATH
# is minimal, so a bare `claude` is not usually found -- sluice's own config comments say the
# same about claude_max_path. Set this to the output of `which claude` on this host.
CLAUDE="__CLAUDE_PATH__"

# A long-lived token from `claude setup-token`, one per line, mode 0600. REQUIRED on macOS, and
# the reason is measured rather than assumed: claude keeps its live credential in the login
# keychain there, and a non-interactive ssh session cannot read a keychain SECRET. Probed from
# both sides of the same machine -- `security find-generic-password -w` returns rc=0 in the GUI
# session and rc=36 (interaction not allowed) over ssh -- so claude reaches its stale
# `~/.claude/.credentials.json` fallback instead and fails with "OAuth session expired and could
# not be refreshed". A token sidesteps the keychain entirely. On Linux hosts the credential is
# already a plain file and this is optional.
#
# The token stays HERE, on the host, and is never sent to the container: the container holds an
# ssh key that can only run this script, and this script supplies the credential. That split is
# the point -- a stolen container key yields one `claude --print`, not a reusable credential.
TOKEN_FILE="__TOKEN_FILE__"

model=""
effort=""
prev=""
# Unquoted ON PURPOSE: sshd hands the client's command over as one string and this word-splits
# it. Safe only because of two things together -- `set -f` above means the split does not also
# GLOB (without it a `*` becomes a filename from the cwd, which is the host account's home), and
# nothing is executed from the result: the loop only COPIES two values out, each charset-checked
# below, and every other token the client sent is discarded.
for tok in ${SSH_ORIGINAL_COMMAND:-}; do
    case "$prev" in
        --model) model="$tok" ;;
        --effort) effort="$tok" ;;
    esac
    prev="$tok"
done

# Identifier-shaped or refused. This is what stops a value carrying a flag of its own (a leading
# `-`, which claude would read as an option) or shell metacharacters, and it refuses rather than
# falling back to a default so a caller cannot silently downgrade the model.
for pair in "model=$model" "effort=$effort"; do
    name=${pair%%=*}
    value=${pair#*=}
    case "$value" in
        "" | -* | *[!A-Za-z0-9._-]*)
            echo "sluice claude-max wrapper: refusing --$name: ${value:-<empty>}" >&2
            exit 1
            ;;
    esac
done

# Read, never echoed. `.` would run the file; this reads one line and nothing else. An ABSENT or
# empty file exports nothing rather than exporting an empty string: `CLAUDE_CODE_OAUTH_TOKEN=""`
# is still 5th in claude's documented credential precedence and would shadow a working keychain
# login on a host that has one, turning a working setup into an auth failure.
if [ -r "$TOKEN_FILE" ]; then
    CLAUDE_CODE_OAUTH_TOKEN=$(head -n 1 "$TOKEN_FILE" | tr -d '\r\n')
    [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] && export CLAUDE_CODE_OAUTH_TOKEN
fi

# The prompt arrives on stdin and is passed through untouched. Everything security-relevant is
# spelled here rather than accepted from the caller.
# STRICTER than sluice's own claude-max argv, deliberately. sluice denies the four tools that
# matter for a CV compose; this key is reachable from a container, so it also denies Task (which
# can spawn an agent that is not itself constrained by this list), WebFetch, and every MCP tool.
# Verified: adding them does not break a `--print` completion.
#
# THE RESIDUAL, stated because `--permission-mode bypassPermissions` is allow-by-default: a
# deny-list cannot name a tool that does not exist yet, so a future claude release could add a
# host-writing tool this line does not mention. What bounds the damage is the `restrict` in
# authorized_keys plus this forced command -- the key cannot get a shell -- not the completeness
# of this list. If that is not a risk you want, use an API-key backend in the container instead.
exec "$CLAUDE" --print \
    --model "$model" \
    --effort "$effort" \
    --disallowedTools Write Edit NotebookEdit Bash Task WebFetch "mcp__*" \
    --permission-mode bypassPermissions
