# Sluice

Sluice is a config-driven job-hunting pipeline for people who keep their job search in an
Obsidian-style markdown vault. It scrapes job boards into lead notes, triages them against
criteria you write in your own vault, composes a CV tailored to each shortlisted role behind a
hard anti-fabrication gate, and reconciles the funnel from your email and calendar.

```
ingest -> triage -> cv -> apply -> track
```

It ships **no opinion about which jobs are good.** Every preference gate defaults to empty, and
an empty gate passes every lead through rather than filtering your search against a stranger's
taste. What the judge is looking for is read at runtime from a note in your vault, never from
this repository.

Installed as the `job-sluice` command — see [Install](#install), and the
[FAQ](https://github.com/MrReasonable/sluice/blob/main/docs/FAQ.md#why-is-the-command-job-sluice-and-not-sluice) for why it isn't `sluice`.

## Contents

[How it works](#how-it-works) · [Start today](#start-today) ·
[What you end up with](#what-you-end-up-with) · [What you need](#what-you-need) ·
[Install](#install) · [Quickstart](#quickstart) · [What it guarantees](#what-it-guarantees) ·
[Commands](#commands) · [MCP server](#mcp-server) · [Configuration](#configuration) ·
[FAQ](#faq) · [Documentation](#documentation) · [Contributing](#contributing) ·
[Releases](#releases) · [License](#license)

## How it works

Five stages. Each writes its result into your vault and stops, so you can run one, inspect what it
did, and run the next — or never run the later ones at all.

| Stage | What it does |
|---|---|
| **ingest** | Drives a headless browser over the job boards you enable and writes each posting into your vault as a markdown note. Already-seen leads are skipped. |
| **triage** | Scores each new lead — deterministic rules first, then an LLM judge reading the criteria *you* wrote in your vault — and sets its status. |
| **cv** | For a shortlisted lead, composes a CV tailored to that posting from evidence you have verified, behind a gate that refuses to let it invent anything, then renders a PDF. |
| **apply** | Stages the CV and a prep packet for the application. You press send. |
| **track** | Reads your email and calendar and moves leads along the funnel as replies, rejections and interviews arrive. |

Stop after `triage` and you have a scored, searchable pipeline of jobs worth reading. Stop after
`cv` and you have tailored applications you send by hand.

## Start today

Two ways in. Both reach judged leads and a tailored CV the same day.

### Let an AI set it up for you

Point a coding agent (Claude Code, Codex, Gemini CLI, whichever you use) at this repository and
paste:

> Read `docs/AI-SETUP.md` and set sluice up for me.

It installs sluice, interviews you for your judging criteria and your CV details, proposes your
evidence entries from your existing CV, and runs the first pass.
[`docs/AI-SETUP.md`](https://github.com/MrReasonable/sluice/blob/main/docs/AI-SETUP.md) is the
contract it follows, and it is worth skimming yourself: it is mostly a list of things the agent is
forbidden to do on your behalf. Three stay yours by design, because each is a decision no tool
should make under your name: logging into job boards, verifying your evidence, and pressing send.

Working from an install rather than a clone? Any agent that can fetch a URL can read that file
without one.

### Or run it yourself

[Quickstart](#quickstart) is four offline commands and takes a few minutes. Then work back through
[What you need](#what-you-need) for whichever stages you want.

## What you end up with

Plain markdown in your own vault, editable in Obsidian, which sluice reads back on the next run.
A newly-created lead note, exactly as written (illustrative values):

```markdown
---
base: "[[Job Leads.base]]"
company: "Example Systems"
role: "Senior Engineer"
location: "Example City"
status: new
score: 0
source: "manual"
salary: "£500/day"
role_type: "contract"
role_type_source: "declared"
url: "https://example.invalid/jobs/1234"
glassdoor_rating: ""
culture_flags: ""
relevance_notes: ""
first_seen: 2026-08-29
last_seen: 2026-08-29
---

# Example Systems - Senior Engineer

**Status:** new
**Location:** Example City | **Salary:** £500/day
**URL:** https://example.invalid/jobs/1234
```

The three blank keys are enrichment slots triage fills in and then owns. `role_type_source` records
where the pay basis came from: `declared` because it was typed at `leads add` below, `observed`
when the advert's own text stated it, `assumed` otherwise.

`status` is the spine, and it has two owners: triage until an application is sent, then `track`,
which triage may never touch again. Your own edits to a note survive every later run — see
[What it guarantees](#what-it-guarantees).

## What you need

Sluice orchestrates things it does not bundle. This is the honest list, so you can decide before
installing rather than after. **Nothing here is a hard stop** — each gap costs you one stage, and
the rest still runs.

| You supply | Needed by | Without it |
|---|---|---|
| A vault directory | everything | `job-sluice init` creates one |
| Ten minutes of answers about what you want | the judge's criteria | every gate abstains, so nothing is filtered out |
| An LLM backend — an API key, or the `claude` CLI | `triage`'s judge, `cv`'s composer | `triage run --no-llm` still classifies deterministically |
| A [Camofox](https://github.com/jo-inc/camofox-browser) browser server | `ingest run`, `ingest test-source`, job-description fetches | no scraping; `job-sluice leads add` files a job you found yourself, and the rest runs on leads already in the vault |
| A baseline CV at `baseline_rel` (default `My CV/CV.md`) | `cv run` | refused before any fetch or backend call |
| At least one **verified** experience entry | `cv run` | refused before any spend, naming the two commands that fix it |
| A Candidate Profile note with a name and contact details | `cv run`, `apply prep` | `cv run` refuses (`skipped-config`); `apply prep` builds the packet with your identity simply absent |
| cairo, pango and gdk-pixbuf, plus the `render` extra | PDF output | set `cv.renderer: script` to shell out to your own renderer |
| A [Google OAuth token](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md#google-access-for-track), which you mint yourself | `track` | `track run` logs a failure and exits 0 |

`job-sluice doctor --offline` reports which of these you are missing and which commands each gap
blocks. Running it immediately after installing is the fastest way to see where you stand. It
exits `0` on a fresh install and lists what is still waiting on you; a non-zero exit means
something you actually configured is broken.

## Install

<!-- channel-status -->

| Channel | Status | Install |
| --- | --- | --- |
| PyPI | shipped | `pip install job-sluice` |
| Docker | shipped | `docker run --rm ghcr.io/mrreasonable/job-sluice --help` |
| deb / rpm | shipped | download from the [latest release](https://github.com/MrReasonable/sluice/releases/latest), then `apt install ./job-sluice_*_all.deb` or `dnf install ./job-sluice-*.noarch.rpm` |
| Homebrew | shipped | `brew install MrReasonable/tap/job-sluice` |

That table is the single place this repository states which channels exist; prose elsewhere links
here rather than restating it, and `tests/test_release_publish_wiring.py` fails the build if a row
disagrees with the jobs declared in `.github/workflows/release-please.yml`, in either direction.
**"Shipped" means the release workflow builds and publishes that channel**, so a row takes effect
from the next release onward rather than claiming every past release carries it.

Use the **fully-qualified** name in the Homebrew command: Homebrew 6 requires explicit trust for
non-official taps, and installing a fully-qualified formula grants trust to that one item, tapping
and installing in a single step.

A bare install gives you the CLI with `pyyaml` and `tzdata` as its only runtime dependencies —
everything else in `sluice/` is standard library. Four opt-in extras add the rest: `render`,
`google`, `mcp` and `completion`.

`render` is necessary but **not sufficient**: WeasyPrint links natively against cairo, pango and
gdk-pixbuf, which no Python package can supply — see
[system libraries for PDF rendering](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md#system-libraries-for-pdf-rendering).
The packaged channels install them for you, which is the main reason to prefer one over pip.

[`docs/INSTALL.md`](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md) has the rest:
per-channel instructions, installing from a checkout, the extras table, the platform-specific
library names, the macOS dynamic-linker step, shell completion, Camofox, credentials, and how to
pin an older release.

## Quickstart

Everything below is offline. No backend, no browser, no credentials.

**1. Scaffold a config and a vault.**

```console
$ job-sluice init --no-input --vault ./vault
  wrote   ~/.config/sluice/config.yaml
  wrote   ~/jobhunt/vault/Job Applications/Judging Profile.md
  wrote   ~/jobhunt/vault/Job Applications/Job Leads/Job Leads.base

created a new vault directory at ~/jobhunt/vault
if you meant an existing one, re-run with --vault pointing at it
...
```

(`~` stands in for your home directory, `...` for output cut for length. `init` prints the paths
fully resolved and goes on to summarise your config.)

The config lands in the XDG config directory; export `SLUICE_CONFIG` to keep it beside your vault
instead. `Job Leads.base` is an Obsidian Bases view, so your leads open as a sortable table rather
than a folder of files.

`init` never overwrites an existing artefact, so re-running it is safe. Drop `--no-input` to be
asked the questions — every one is optional except where your vault is, and a blank answer leaves
that gate **unset**, which passes every lead through. Answering nothing also writes no Candidate
Profile, which `cv run` and `apply prep` both need.

Do **not** copy `sluice.yaml.example` into place instead. It is a catalogue to read, not a
template: it ships illustrative values *active*, so a verbatim copy arrives with its title,
relevance and pay gates already closed and nothing saying so.

**2. See where you stand.**

```console
$ job-sluice doctor --offline
job-sluice doctor  (offline)

Ready now:    scrape job boards, triage leads, send applications
Needs setup:  tailored CVs, track replies

Still to set up:
  cv.renderer
      renderer 'template' could not load its rendering backend: pip install
...
  baseline_rel
      baseline CV not found, or empty, at the configured path -- cv run cannot
      compose without it
  Experience Library
      0 verified / 0 total entries -- only verified entries are citable by the CV
      fabrication gate -- cv run refuses to compose without at least one
  Candidate Profile
      no name or no contact details -- cv run refuses to compose (skipped-config)
      before any backend call
  google client libs
      not importable (No module named 'google') -- track run cannot reconcile
...

Nothing is broken.
...
Run `job-sluice doctor --verbose` for every check.
```

A fresh install exits `0`. Nothing above is a fault: each row is something you have not supplied
yet, and the two lines at the top say which of the five things sluice does that actually costs
you. `Ready now` means nothing `doctor` checks is blocking it — not a promise it will work, since
an offline run never dials a backend or a browser. Fix what you need and ignore the rest.

By default `doctor` exits non-zero only when something you *did* configure does not work, which
makes the exit code safe in a setup script or a cron alert — `--strict` also fails on `degraded`,
and `--require` fails on any capability you name that is not ready. **That capture is one machine, not the answer** —
that one had the `claude` CLI on `$PATH` and no `render` extra, and yours will differ.
[`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md#job-sluice-doctor---offline---strict---verbose---require-capability)
has the flags, the five row states, and why a monitor should alert on `--require` rather than on
the exit code.

**3. Look at the boards.**

```console
$ job-sluice ingest list-sources
bayt             browser   enabled EXAMPLE-SEARCH(1/1)
bwork            browser   disabled
cord             browser   enabled EXAMPLE-SEARCH(1/1)
...
wttj             browser   enabled EXAMPLE-SEARCH(1/1)
```

Boards ship enabled; a few are disabled, each recording why and the date its retirement was last
checked against the live web. `EXAMPLE-SEARCH(n/m)` says `n` of that source's `m` searches are still
the shipped example rather than yours — so a fresh install shows it everywhere. Your real searches
belong in `sources.<id>.searches`. Add `--health` for per-source scrape state once you have run
something.

**4. Give it a lead, then triage.**

Nothing has scraped yet, so the vault holds no leads and triage would have nothing to classify.
`leads add` is the offline way in — for a job you found yourself, with no browser server running:

```console
$ job-sluice leads add --url https://example.invalid/jobs/1234 \
    --company "Example Systems" --role "Senior Engineer" \
    --location "Example City" --salary "£500/day" --role-type contract
leads add: Example Systems - Senior Engineer: created
```

That writes exactly the note under [What you end up with](#what-you-end-up-with), through the same
store path a scrape uses — so re-running it reports `updated` and bumps `last_seen` rather than
overwriting anything you changed. Then:

```console
$ job-sluice triage run --no-llm
triage: {'keep': 1, 'shortlist': 0, 'research': 0, 'dismiss': 0, 'needs_review': 0, 'skipped': 0, ...
```

`--no-llm` runs the deterministic tiers only — no backend call, nothing billed. On an unconfigured
install every lead lands in `keep`, which is the empty-config-abstains rule working: nothing was
discarded because nothing was configured to discard it.

**Next:** fill in the headings in `Job Applications/Judging Profile.md`, add your own searches, and
work back through the table under [What you need](#what-you-need) for whichever
stages you want. [`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md)
is the full command reference.

## What it guarantees

Five properties, enforced by tests rather than promised in prose. Each guards a failure that is
silent, asymmetric and hard to undo.

- **Your edits survive.** A re-scrape touches only `last_seen` — never your status, never your
  notes. Every other write is a surgical compare-and-set against the *current* note, committed
  atomically, so a concurrent editor's changes survive rather than being clobbered, and a
  sustained race abstains rather than overwriting. Best-effort rather than a lock, deliberately:
  the writer sluice is racing is *you*, editing in Obsidian, and you take no lock.
- **Status never regresses out of the application lifecycle.** Triage may never touch a lead that
  has been applied to, terminals are never advanced out of, and a lead you merged away is not
  silently re-created by a later scrape.
- **The CV cannot invent things.** Every bullet must cite real evidence, and every number must
  appear in the entry it cites. A figure that appears nowhere in your source material blocks
  rendering outright.
- **An empty setting abstains.** Unconfigured means "no opinion", never "match nothing". Getting
  this backwards would bin an entire job hunt in silence — it happened once, and a test now fails
  the build if it recurs.
- **No personal data in this repository.** Your search belongs in your config and your vault.

[`docs/GUARANTEES.md`](https://github.com/MrReasonable/sluice/blob/main/docs/GUARANTEES.md) has the mechanics of each, including the
limits — where a guarantee is best-effort, and the one thing the CV gate cannot see.

## Commands

| Command | Purpose |
|---|---|
| `job-sluice init` | scaffold a config, a Judging Profile, and a Candidate Profile when answered interactively |
| `job-sluice doctor` | preflight backends, renderer, cv identity, store artefacts, gate posture |
| `job-sluice ingest` | scrape configured boards into the lead store (`list-sources`, `run`, `test-source`, `enable`, `disable`) |
| `job-sluice triage` | classify leads: deterministic rules, then an LLM judge (`run`, `normalize-status`) |
| `job-sluice cv` | compose, gate and render a tailored CV, then sign off on it (`run`, `signoff`) |
| `job-sluice apply` | stage a CV and a prep packet, then record a submitted application (`prep`, `record`) |
| `job-sluice track` | reconcile the funnel from email and calendar signals (`run`, `confirm`, `dismiss`) |
| `job-sluice leads` | add a lead by hand, then the maintenance passes over the store (`add`, `dedupe`, `expire`, `dismiss`, `reconcile`, `rename`) |
| `job-sluice experience` | capture and verify experience evidence — the CV gate's only citable source (`add`, `list`, `verify`) |
| `job-sluice skills` | capture and verify skills evidence, shown to the composer as framing (`add`, `list`, `verify`) |
| `job-sluice stories` | capture and verify STAR stories (`add`, `list`, `verify`) |
| `job-sluice health` | per-source scrape baseline and retire state |
| `job-sluice mcp` | run a Model Context Protocol server over stdio (`serve`, plus `--write` for the write tools) |

The `leads` passes **report by default** and change nothing until told otherwise (`--merge`,
`--expire`, `--apply`), because they write over a set the tool computed. `add` and `dismiss`
are the exceptions: both write on every call, because what they write is what you typed — the
new lead's own fields, or the dismissal reason. The pipeline commands invert that and write by
default.

Full flag reference, exit codes and which stream each command writes to:
[`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md).

## MCP server

`job-sluice mcp serve` runs sluice as a Model Context Protocol server over stdio, so an agent can
call `list_leads`/`get_lead`/`doctor`/`health`/`list_evidence` directly instead of parsing CLI
output. Read-only by default; needs `pip install 'job-sluice[mcp]'`.

```bash
claude mcp add job-sluice -- job-sluice mcp serve
```

`--write` additionally registers the write tools, as a per-registration trust decision rather than
a per-call one. Nothing at any level can mark evidence verified — that stays a human action at a
prompt. [`docs/MCP.md`](https://github.com/MrReasonable/sluice/blob/main/docs/MCP.md) has the tool tables and the reasoning.

## Configuration

Every key is optional and falls back to a code default, so sluice runs with no config file at all.
Config resolves as code defaults < the YAML file at `$SLUICE_CONFIG` (else
`$XDG_CONFIG_HOME/sluice/config.yaml`) < environment variables.

Most paths relocate to the XDG directories. Your **vault** does not: it defaults to `./vault`,
relative to wherever you run the command, because it is your Obsidian directory rather than state
sluice owns — so set `vault_dir` before running from elsewhere, or you will get a second, empty
vault beside you.

Sluice never migrates your state for you. It prints the `mv` commands, and the commands that would
write dedup state refuse to start until you have moved them: starting from an empty dedup set can
re-create leads you merged away and risks applying to the same job twice.

- [`sluice.yaml.example`](https://github.com/MrReasonable/sluice/blob/main/sluice.yaml.example) — the full catalogue with inline
  comments. **A catalogue to read, not a file to copy.**
- [`docs/CONFIGURATION.md`](https://github.com/MrReasonable/sluice/blob/main/docs/CONFIGURATION.md) — every key by block, with its
  default and what leaving it unset means.

## FAQ

**Why `job-sluice` and not `sluice`?** The PyPI name is squatted by an unrelated dormant package.
The import package and your `~/.config/sluice/` paths are unchanged; only what you type differs.

**Does it apply to jobs for me?** No. It stages the application; you press send. That is one of
three things reserved to you, with logging into job boards and verifying your evidence.

**Do I need Obsidian?** No — the vault is plain markdown files in a directory. Obsidian is just the
nicest way to browse them.

**Can I run it without an LLM?** Partly. `triage run --no-llm` classifies deterministically, and
`apply` needs no LLM at all. `ingest` and `track` do not either, but they need their own things —
a browser server and Google access respectively. See [What you need](#what-you-need).

[`docs/FAQ.md`](https://github.com/MrReasonable/sluice/blob/main/docs/FAQ.md) has these in full, plus why it needs a browser server.

## Documentation

| | |
|---|---|
| [`docs/AI-SETUP.md`](https://github.com/MrReasonable/sluice/blob/main/docs/AI-SETUP.md) | the contract an AI agent follows to set sluice up for you, and what it may never do |
| [`docs/INSTALL.md`](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md) | per-channel install, extras, system libraries, shell completion, Camofox, credentials |
| [`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md) | command reference: flags, exit codes, output streams |
| [`docs/CONFIGURATION.md`](https://github.com/MrReasonable/sluice/blob/main/docs/CONFIGURATION.md) | config keys by block |
| [`docs/GUARANTEES.md`](https://github.com/MrReasonable/sluice/blob/main/docs/GUARANTEES.md) | the five invariants and their mechanics |
| [`docs/MCP.md`](https://github.com/MrReasonable/sluice/blob/main/docs/MCP.md) | the MCP server, its tools, and the read/write boundary |
| [`docs/FAQ.md`](https://github.com/MrReasonable/sluice/blob/main/docs/FAQ.md) | naming, what sluice will not do for you, and why |
| [`docs/TROUBLESHOOTING.md`](https://github.com/MrReasonable/sluice/blob/main/docs/TROUBLESHOOTING.md) | specific failures and their fixes |
| [`docs/ARCHITECTURE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/ARCHITECTURE.md) | module-by-module, the adapter seams, the store contract |

## Contributing

[`CONTRIBUTING.md`](https://github.com/MrReasonable/sluice/blob/main/CONTRIBUTING.md) has the dev
setup, the test and lint commands, and the invariants a change is expected to respect.
[`SECURITY.md`](https://github.com/MrReasonable/sluice/blob/main/SECURITY.md) covers vulnerability
reporting. The test suite is fully offline and hermetic — no browser, no network.

## Releases

Version history and migration notes are in [`CHANGELOG.md`](https://github.com/MrReasonable/sluice/blob/main/CHANGELOG.md);
`job-sluice --version` reports what you have installed. A breaking **config** change counts for
more here than a breaking API change — nothing imports sluice as a library, so what you have
invested in is your config and your vault. [`CONTRIBUTING.md`](https://github.com/MrReasonable/sluice/blob/main/CONTRIBUTING.md#releases)
states what earns a migration note.

## License

MIT. See [`LICENSE`](https://github.com/MrReasonable/sluice/blob/main/LICENSE).
