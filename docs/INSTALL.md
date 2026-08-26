# Installing job-sluice

The distribution and the command are both `job-sluice`. The import package stays `sluice`, and so
do the `SLUICE_*` environment variables and the `~/.config/sluice/` paths — see
[Naming](../README.md#naming) for why those three are different things here.

**Which channels exist is stated once**, in [README's channel table](../README.md#install). This
page is how to use each one, what it does and does not include, and what to set up afterwards.

## Picking a channel

| If you want | Use |
|---|---|
| the CLI on your own machine, isolated from your other Python | **uv** or **pipx** |
| PDF rendering working with no host setup at all | **Docker**, **Homebrew**, or **deb/rpm** |
| PDF rendering on macOS with no dynamic-linker export to set | **Homebrew** |
| a system package your distro's tooling manages | **deb/rpm** |
| to hack on sluice | **from source** |

Every channel gives the same CLI. What differs is which optional [extras](#extras) come with it
and who supplies the native libraries PDF rendering needs.

## Requirements

- **Python 3.12 or newer** for the PyPI channels and a source checkout (`requires-python =
  ">=3.12"`). Docker, Homebrew and deb/rpm each bring or declare their own interpreter, so this
  is not something you manage on those.
- **Nothing else is mandatory.** A bare install has two runtime dependencies, `pyyaml` and
  `tzdata`; everything else in `sluice/` is standard library. PDF rendering, Google access, the
  MCP server and shell completion are opt-in extras.

## uv

The shortest path, and the one to reach for first.

```bash
uv tool install job-sluice
job-sluice --version
```

To try it without installing anything:

```bash
uvx job-sluice --help
```

With extras:

```bash
uv tool install 'job-sluice[render,google,mcp,completion]'
```

## pipx

```bash
pipx install job-sluice
job-sluice --version
```

Extras take the same bracket form: `pipx install 'job-sluice[render]'`.

## pip

`pip install` belongs in a virtual environment. Distro Pythons and Homebrew's Python are marked
externally-managed under PEP 668, so a bare `pip install` there is refused rather than done — that
refusal is the reason uv and pipx lead this page, since both make the environment for you.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install job-sluice
```

Extras attach to the *distribution* name: `pip install 'job-sluice[render]'`. Dropping the `job-`
prefix resolves to a different, unrelated package.

## Docker

```bash
docker run --rm ghcr.io/mrreasonable/job-sluice:latest --version
```

Tags are `X.Y.Z`, `X.Y` and `latest`; `X.Y` and `latest` follow the newest release. The image
installs every extra (`render`, `google`, `mcp`, `completion`) and ships WeasyPrint's cairo/pango
libraries already built in, which is the main reason to choose it.

A bare `docker run` is not enough to use sluice for real, because four directories have to survive
the container: your vault, the config, and two pieces of per-system state. Inside the image the
XDG roots are `/app/config`, `/app/state` and `/app/cache`, and the working directory is `/work`.
Rather than retype that per invocation, use the compose file this repository ships — you need the
file itself, which does not come with the image:

```bash
curl -LO https://raw.githubusercontent.com/MrReasonable/sluice/main/docker-compose.yml
docker compose run --rm job-sluice ingest list-sources --health
```

Compose reads an optional `.env` beside it for backend credentials; the file declares it
`required: false`, so an absent one is not an error.

**Choose a backend before running anything that reaches an LLM.** `claude-max` is the shipped
default for `primary_backend`, and the image does not carry the `claude` CLI it shells out to —
that CLI is a ~325MB self-contained binary, and bundling it would more than double the image for a
backend not everyone chooses. `job-sluice doctor` says so plainly rather than failing later:

```text
claude-max  claude-sonnet-4-5  dead  primary: triage, cv, track  CLI 'claude' not on PATH
```

Two ways out. **An API-key provider** is the simpler one: set `primary_backend` to `anthropic`,
`openai` or `deepseek` and put the matching variable from
[Backend credentials](#backend-credentials) in `.env`. Or keep the flat rate by pointing the
container at the CLI already on your machine — see below.

Read [`docker-compose.yml`](../docker-compose.yml) before your first real run. Its comments are
not decoration — they record two ways a container install loses data silently, both of which come
from state that persists pointing at an artefact that does not. In particular `VAULT_DIR` is
pinned there deliberately, and `docker compose down -v` destroys the volumes that hold the dedup
databases.

Camofox is **not** in the image and cannot be — it is a separate persistent browser service. It
runs on your host, and the compose file points the container at it via
`CAMOFOX_URL=http://host.docker.internal:9377`.

### claude-max from a container

The image ships an SSH client, and `claude-max` already knows how to run the CLI on another host
— its command is `ssh <host> <the same argv>` whenever a host is configured. So the container can
use the `claude` you already have, keeping the flat rate. Nothing is baked in: you supply a key,
and your machine decides what that key may do.

The design is deliberately lopsided. **The container holds a key that can do exactly one thing;
your machine holds the credential.** A key that leaked from a container image or a stray volume is
worth one `claude --print`, not a shell on your laptop.

**1. Let your machine accept SSH.** macOS: System Settings → General → Sharing → Remote Login.
Linux: install and start `openssh-server`.

**2. Make a key that is only for this.** Not one you already use somewhere else — the whole point
is that this one is disposable and can be revoked without touching anything else:

```bash
ssh-keygen -t ed25519 -N "" -C "sluice claude-max" -f ~/.ssh/sluice_claude_max
```

No passphrase, because nothing can type one: the container runs unattended. That is exactly why
the key is restricted in step 4 rather than trusted.

**3. Install the wrapper**, which is what makes the key safe. Copy
[`packaging/claude-max-ssh-wrapper.sh`](../packaging/claude-max-ssh-wrapper.sh) somewhere private,
replace its two placeholders, and make it executable:

```bash
mkdir -p ~/.local/libexec
# Fetch it: the Docker setup above downloads only docker-compose.yml, so there is no
# packaging/ directory here unless you also cloned the repository.
#
# REF must name the SAME release as the image you run. The wrapper builds the argv sluice
# expects, so a wrapper from a newer branch can disagree with an older image about which flags
# exist -- the same reason every other channel here names a version.
#
# The default tracks JOB_SLUICE_TAG, which is the same variable docker-compose.yml reads
# (`image: ...:${JOB_SLUICE_TAG:-latest}`), so the two move together: tag unset means image
# `:latest` and wrapper `main`, and `JOB_SLUICE_TAG=v2.1.0` means both are v2.1.0. The one way
# to break that pairing is to pin the image by EDITING docker-compose.yml rather than setting
# the variable -- then the image is a release and REF silently falls back to the moving `main`.
# If you pinned that way, set REF explicitly here. It is echoed so you can see which you got.
REF="${JOB_SLUICE_TAG:-main}"   # or set it outright, e.g. REF=v2.1.0
echo "installing the wrapper from ref: ${REF}"
curl -fsSL -o /tmp/claude-max-ssh-wrapper.sh \
  "https://raw.githubusercontent.com/MrReasonable/sluice/${REF}/packaging/claude-max-ssh-wrapper.sh"
# `command -v` guarded, not a bare substitution: with no claude on PATH the wrapper would
# render CLAUDE="" and every completion would die at exec, far from the cause. Guarded with
# `if`, NOT `|| exit` -- this block is meant to be pasted into your shell, and `exit` in an
# interactive shell closes the terminal rather than stopping the setup.
CLAUDE_BIN=$(command -v claude)
if [ -z "$CLAUDE_BIN" ]; then
  echo "no claude on PATH -- install Claude Code first, then re-run this block"
else
  sed -e "s|__CLAUDE_PATH__|${CLAUDE_BIN}|" \
      -e "s|__TOKEN_FILE__|$HOME/.claude/sluice-oauth-token|" \
      /tmp/claude-max-ssh-wrapper.sh > ~/.local/libexec/sluice-claude-max-ssh-wrapper.sh
  chmod 700 ~/.local/libexec/sluice-claude-max-ssh-wrapper.sh
fi
```

The wrapper *builds* the command it runs rather than trusting the one that arrives — including a
deny-list (`Write`, `Edit`, `NotebookEdit`, `Bash`, `Task`, `WebFetch` and every MCP tool) that is
stricter than sluice's own, because this key is reachable from a container. Validating the
caller's argv instead would leave that deny-list supplied by the caller, and the caller is who it
defends against.

Be clear about what that does and does not buy, since `--permission-mode bypassPermissions` is
allow-by-default: a deny-list cannot name a tool that does not exist yet, so a future release
could add one this list does not mention. What bounds the damage is `restrict` plus the forced
command — the key cannot get a shell at all — not the completeness of the list. If that residual
is not acceptable to you, use an API-key backend in the container instead.

**4. Authorise the key, restricted to that wrapper**, as one line in `~/.ssh/authorized_keys`:

```text
restrict,command="/absolute/path/to/sluice-claude-max-ssh-wrapper.sh" ssh-ed25519 AAAA... sluice claude-max
```

`restrict` turns off port forwarding, agent forwarding, X11 and PTY allocation; `command=` means
the client's own command never runs. Verify it — a shell escape and a file read should both be
refused:

```bash
ssh -i ~/.ssh/sluice_claude_max localhost 'id'          # refused by the wrapper
ssh -i ~/.ssh/sluice_claude_max -tt localhost 'echo hi' # PTY allocation request failed
```

**5. On macOS, give the wrapper a token.** This step is not optional there, and skipping it fails
in a way that looks like something else. macOS keeps the live credential in the login keychain,
and a non-interactive SSH session cannot read a keychain *secret* — measured on one machine,
`security find-generic-password -w` returns `0` in your desktop session and `36`
(interaction not allowed) over SSH. `claude` then falls back to a stale `~/.claude/.credentials.json`
and reports `OAuth session expired and could not be refreshed`, which reads like an expired login
rather than a permissions boundary.

A long-lived token sidesteps the keychain:

```bash
claude setup-token                       # opens a browser; prints a 1-year token
umask 077 && printf '%s\n' 'PASTE-TOKEN-HERE' > ~/.claude/sluice-oauth-token
```

**The token stays on your machine.** The wrapper reads it and exports
`CLAUDE_CODE_OAUTH_TOKEN` for the one command it runs; the container never sees it, and never
needs to. On Linux hosts the credential is already a plain file, so this step is optional.

**6. Point compose at it**, in `.env` beside `docker-compose.yml`:

```bash
SLUICE_CLAUDE_KEY=/absolute/path/to/.ssh/sluice_claude_max
SLUICE_CLAUDE_HOST=host.docker.internal
SLUICE_CLAUDE_SSH_USER=your-account-name-on-this-machine
SLUICE_CLAUDE_PATH=claude
```

`SLUICE_CLAUDE_SSH_USER` is your login name on the host — the container's own user is `sluice`,
which is almost certainly not it. `SLUICE_CLAUDE_PATH` can be any non-empty token while the
forced-command wrapper is in use, since the wrapper decides which binary runs; without a wrapper
it must be an **absolute** path, because `ssh host cmd` gets a minimal `PATH` that rarely
includes `claude`.

Then check it:

```bash
docker compose run --rm job-sluice doctor
```

A working setup reports `claude-max ... ok  round-trip ok`. If it says
`Host key verification failed`, the entrypoint did not run its SSH setup — most often
`SLUICE_CLAUDE_HOST` or `SLUICE_CLAUDE_SSH_USER` is unset, and the message points at ssh rather
than at the missing variable.

Two things worth knowing rather than discovering. The first connection pins the host key
(`StrictHostKeyChecking accept-new`) and refuses a *changed* one afterwards — trust-on-first-use,
against a host on your own machine. That "afterwards" is load-bearing and nearly was not true:
`docker compose run --rm` deletes the container layer, so a `known_hosts` written to the home
directory would start empty on every run and re-accept whatever key was offered, each time. It is
written under the state volume instead, which persists — verified by running twice and seeing the
second run start with the key already known. And `SLUICE_CLAUDE_HOST`/`SLUICE_CLAUDE_PATH` set
`claude_max_host`/`claude_max_path` for `triage`, `cv` **and** `track` together; if you need them
to differ, leave the variables unset and use the config keys.

## Homebrew (macOS)

```bash
brew install MrReasonable/tap/job-sluice
job-sluice --version
```

Use the **fully-qualified** name. Homebrew 6 requires explicit trust for non-official taps, and
installing a fully-qualified formula is what grants trust to that one item — it taps and installs
in a single command. Installing by short name needs the trust granted first:

```bash
brew tap mrreasonable/tap
brew trust --formula mrreasonable/tap/job-sluice
brew install job-sluice
```

The formula installs every extra and declares its own `python@3.x`, `pango` and the rest, so PDF
rendering works with no further setup and **no `DYLD_FALLBACK_LIBRARY_PATH` export** — see
[system libraries](#system-libraries-for-pdf-rendering) for why that export is needed on a pip
install and not here.

The tap carries **only the latest formula**. A tap has no version history, so there is no
Homebrew route to an older release; use another channel for that — see
[pinning an older version](#pinning-an-older-version).

## deb / rpm

One architecture-independent package per release, attached to the GitHub release rather than
served from a hosted apt or yum repository. Download it, then install the local file:

```bash
# Set this to the release you want -- the newest is on the Releases page linked below.
VERSION=2.0.1

# Debian, Ubuntu
curl -LO "https://github.com/MrReasonable/sluice/releases/download/v${VERSION}/job-sluice_${VERSION}_all.deb"
sudo apt install "./job-sluice_${VERSION}_all.deb"

# Fedora
curl -LO "https://github.com/MrReasonable/sluice/releases/download/v${VERSION}/job-sluice-${VERSION}-1.noarch.rpm"
sudo dnf install "./job-sluice-${VERSION}-1.noarch.rpm"
```

The asset filenames carry the version, so there is no `latest` URL to download from — pick the
release from [Releases](https://github.com/MrReasonable/sluice/releases) and set `VERSION` to
match.

Because there is no hosted repository, `apt upgrade` and `dnf upgrade` will not move you to a new
release. Upgrading means downloading the next one and installing it the same way.

Both packages **recommend** WeasyPrint and Jinja2, so an ordinary install pulls the render stack
and its native libraries in for you. A recommendation is not a requirement: `apt
--no-install-recommends`, or dnf configured with `install_weak_deps=False`, skips it silently. If
that is how you install, ask the package manager for them directly —
`sudo apt install weasyprint python3-jinja2`, or `sudo dnf install python3-weasyprint
python3-jinja2`.

The other extras come from your distro too, not from pip: the packaged CLI runs on
`/usr/bin/python3` with the system packages importable, and PEP 668 blocks pip from adding to that
interpreter. `google` and `completion` are available that way —
`python3-googleapi` + `python3-google-auth` on Debian and Ubuntu,
`python3-google-api-client` + `python3-google-auth` on Fedora, and `python3-argcomplete` on both.
**The `mcp` extra is not reachable on this channel**: neither family packages `mcp` today, so
`job-sluice mcp serve` needs one of the other channels.

## From source

```bash
git clone https://github.com/MrReasonable/sluice.git
cd sluice
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
job-sluice --version
```

The virtual environment is not optional on a distro or Homebrew Python, for the reason given
under [pip](#pip): PEP 668 refuses to install into those, editable or not.

Extras use the path form here, because you are installing a checkout rather than naming a
distribution: `pip install -e '.[render]'`.

To work on sluice, install the test extra and run the suite — it is fast, fully offline, and needs
neither Camofox nor a backend:

```bash
pip install -e ".[test]"
python -m pytest
```

[`CONTRIBUTING.md`](../CONTRIBUTING.md) has the rest of the development setup.

## Extras

| Extra | Unlocks | Included by |
|---|---|---|
| `render` | `cv.renderer: template` — the default renderer, which fills a Jinja2 template via WeasyPrint | Docker, Homebrew; recommended by deb/rpm |
| `google` | `track`'s Gmail and Calendar access | Docker, Homebrew; available from distro packages on deb/rpm |
| `mcp` | `job-sluice mcp serve` | Docker, Homebrew |
| `completion` | shell completion of commands, flags and live values — the extra alone is inert until the shell hook is registered, see [README](../README.md#shell-completion) | Docker, Homebrew; available from distro packages on deb/rpm |

`ingest` and `triage` need none of them. `track run` does need `google`: the client libraries are
imported lazily, the first time a run reaches Gmail, so a missing extra surfaces during the run
rather than at startup. `cv run` needs `render` unless you set `cv.renderer: script`, which shells
out to a render script you supply and needs neither the extra nor its system libraries.

## System libraries for PDF rendering

WeasyPrint links natively against cairo, pango and gdk-pixbuf. **pip cannot supply those** — no
Python package can — so this is the one part of the install that differs by platform, and it is
why the packaged channels exist.

- **Docker** ships them in the image.
- **Homebrew** declares them as formula dependencies.
- **deb/rpm** recommend WeasyPrint, whose own dependencies pull them in.
- **pip / uv / pipx / source**: install them with your platform's package manager. WeasyPrint's
  own installation docs list the exact package names per system.

**On macOS, one more step for a pip install.** With cairo, pango and gdk-pixbuf present via
Homebrew, `import weasyprint` still fails under a non-Homebrew Python until the dynamic linker is
pointed at Homebrew's lib directory. What decides this is the **interpreter**, not the libraries:
Homebrew's own CPython patches `ctypes`' library-search fallback to include the Homebrew prefix, so
a `brew install` of job-sluice needs no such export while a pip install under a version-manager
Python does. [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) has the exact failure and the one line
to export — deliberately not repeated here, since a command duplicated across three documents is a
command that will disagree with itself.

## Camofox

`ingest run` and `ingest test-source` drive a persistent, authenticated headless browser server
called Camofox. **This repository does not bundle one** — see
[jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) to stand one up. sluice looks
for it at `http://127.0.0.1:9377` by default (`CAMOFOX_URL`).

`triage` and `cv` reach it too, but only lazily, on a dossier cache miss. Every other command
works without it.

## Backend credentials

Which credentials you need depends on which providers you configured as `primary_backend` and
`fallback_backend`, so there is nothing to set up until you have chosen:

| Provider | Needs |
|---|---|
| `claude-max` | **no API key** — shells out to the flat-rate `claude` CLI, locally or over SSH. Under [Docker](#docker) the image carries no CLI, so it needs the ssh route in [claude-max from a container](#claude-max-from-a-container) |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |

`triage run --no-llm` needs none of them. A keyless *fallback* backend is a sanctioned degrade;
a keyless *primary* is not. `job-sluice doctor` tells you which you have.

## Google access for `track`

`track run` reconciles Gmail and Calendar. It needs the `google` extra **and** an OAuth token at
`track.token_path` (by default `<XDG_STATE_HOME>/sluice/google_token.json`).

**sluice does not obtain that token for you.** There is no consent flow in the codebase: the
Google client reads an existing authorized-user credential and refreshes it, and with no token
present a `track run` reports a failure rather than prompting for anything. Producing the token is
a manual, one-time step you do yourself.

You need an OAuth client of your own — a *Desktop app* client ID from a Google Cloud project with
the Gmail and Calendar APIs enabled. Download its JSON from the Google Cloud console (the button
is *Download JSON* on the credential), save it as `client_secret.json`, and run the script below
from the directory holding it — both filenames are resolved against the working directory, not
against the script.

The consent run grants exactly the access sluice uses:

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | sluice only lists and reads messages and their attachments; it never modifies or deletes mail |
| `https://www.googleapis.com/auth/calendar.events` | sluice lists, creates, updates and deletes events on your primary calendar |

A one-off script using `google-auth-oauthlib` produces the file. sluice does not depend on that
package and no channel installs it, so install it yourself — in a throwaway virtual environment,
which keeps a package you need exactly once out of the environment job-sluice runs in, and is
required outright on a distro or Homebrew Python for the [PEP 668 reason above](#pip):

```bash
python3 -m venv /tmp/sluice-oauth
/tmp/sluice-oauth/bin/pip install google-auth-oauthlib
/tmp/sluice-oauth/bin/python get_token.py
```

```python
# get_token.py -- opens a browser, writes token.json into the working directory.
import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/calendar.events"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)
# Written, not printed. `run_local_server` prints its own "Please visit this URL..." prompt to
# stdout, so redirecting this script's output to a file would capture that line ahead of the
# JSON -- leaving an unparseable credential AND hiding the URL you need when the browser does
# not open on its own, which is exactly the case on a headless box.
# Created 0600 in one step: a plain write followed by a chmod leaves the credential briefly
# world-readable, and `Path.touch(mode=...)` does not re-apply the mode to a file that already
# exists (measured). O_EXCL means a re-run refuses rather than silently overwriting a working
# token -- delete the old one first if you are deliberately re-authorising.
fd = os.open("token.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(creds.to_json())
print("wrote token.json")
```

Move `token.json` to `track.token_path` — by default
`<XDG_STATE_HOME>/sluice/google_token.json`, and [`docs/CONFIGURATION.md`](CONFIGURATION.md) has
the resolution order if you have set the key or moved the XDG root. Then delete
`/tmp/sluice-oauth`; nothing needs it again unless you re-authorise. It is an authorized-user credential — sluice's reader
requires `refresh_token`, `client_id` and `client_secret` in it — and it is a live secret: sluice
writes it `0600` whenever it refreshes it, so store it at least as tightly.

If the token later stops working, [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) distinguishes a
genuinely dead credential (which needs this step again) from a transient network failure (which
does not, and where deleting the token would be the wrong move).

## Upgrading

| Channel | How |
|---|---|
| uv | `uv tool upgrade job-sluice` |
| pipx | `pipx upgrade job-sluice` |
| pip | `pip install -U job-sluice`, in the same virtual environment |
| Docker | `docker pull ghcr.io/mrreasonable/job-sluice:latest` |
| Homebrew | `brew update && brew upgrade job-sluice` |
| deb / rpm | download the new release and install it the same way — see below |
| source | `git pull`, and re-run `pip install -e .` if the dependencies changed |

The deb/rpm row is the one that differs, and it is worth stating rather than leaving to be
discovered: because there is no hosted apt or yum repository, `apt upgrade` and `dnf upgrade` will
never move you to a new release. Every other channel does upgrade in place.

## Pinning an older version

| Channel | How | Available from |
|---|---|---|
| uv | `uv tool install 'job-sluice==1.2.0'` | 1.0.0 |
| pipx | `pipx install 'job-sluice==1.2.0'` | 1.0.0 |
| pip | `pip install 'job-sluice==1.2.0'` | 1.0.0 |
| Docker | `docker run --rm ghcr.io/mrreasonable/job-sluice:1.2.0 --version` | 1.1.0 |
| deb / rpm | download the asset from that release's page under [Releases](https://github.com/MrReasonable/sluice/releases) | 1.2.0 |
| Homebrew | **not possible** — the tap carries only the latest formula | — |
| source | `git checkout v1.2.0 && pip install -e .` | every tag |

The "available from" column is not a formality: the channels were built one after another, so a
release predating a channel has no artefact on it. Older releases stay published on the channels
that carried them.

## Checking the install

```bash
job-sluice doctor --offline   # config-only checks, no network
job-sluice doctor             # adds a live round-trip
```

`doctor` reports each backend, the renderer, the store's artefacts and track's Google adapter, and
names which commands a dead or degraded result blocks. It is the first thing to run after
installing and the first thing to run when something later goes wrong.

## First run

```bash
job-sluice init --vault ./vault
```

`init` writes a config from the question catalogue and creates a Judging Profile in your vault. It
never overwrites an existing file, so re-running it is safe.

**Do not copy `sluice.yaml.example` into place.** It is a catalogue to read, not a template: it
ships illustrative values *active*, so a verbatim copy silently discards nearly every lead and
reads like a broken scraper rather than a closed gate. `init` exists precisely to avoid that — the
config it writes leaves every unanswered key commented out.

From there, [`docs/USAGE.md`](USAGE.md) is the command reference and
[`docs/CONFIGURATION.md`](CONFIGURATION.md) is the config-key reference.
