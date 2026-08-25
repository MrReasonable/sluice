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

Read [`docker-compose.yml`](../docker-compose.yml) before your first real run. Its comments are
not decoration — they record two ways a container install loses data silently, both of which come
from state that persists pointing at an artefact that does not. In particular `VAULT_DIR` is
pinned there deliberately, and `docker compose down -v` destroys the volumes that hold the dedup
databases.

Camofox is **not** in the image and cannot be — it is a separate persistent browser service. It
runs on your host, and the compose file points the container at it via
`CAMOFOX_URL=http://host.docker.internal:9377`.

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
| `claude-max` | **no API key** — shells out to the flat-rate `claude` CLI, locally or over SSH |
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
