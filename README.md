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

Installed as the `job-sluice` command — see [Install](#install), and [Naming](#naming) for why
it isn't `sluice`.

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
[What it costs to run](#what-it-costs-to-run) for whichever stages you want.

### What to have ready

| | Needed for | If you skip it |
|---|---|---|
| Your current CV, as markdown | tailored CVs | triage still works; `cv` does not |
| Ten minutes of answers about what you want | the judge | every gate abstains, so nothing is filtered out |
| An LLM backend: the `claude` CLI, or an API key | the judge, the composer | `triage run --no-llm` still classifies deterministically |
| Docker, and one `make build` | scraping job boards | paste job ads in by hand instead |

Nothing here is a hard stop. Skipping all four still leaves a working lead store you can file by
hand, which is why `doctor` reports what is missing rather than refusing to start.

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

The three blank keys are enrichment slots triage fills in and then owns.

`status` is the spine, and it has two owners. Triage owns `new`, `shortlist`, `research`,
`needs_review`, `dismiss` and `unjudgeable`; once an application is sent, the lead crosses into
the set `track` owns — `applied`, `phone_screen`, `interview`, `offer`, and the terminals
`accepted`, `rejected` and `withdrawn` — which triage may never touch again. Your own edits to a
note survive every later run — see [What it guarantees](#what-it-guarantees).

## What it costs to run

Sluice orchestrates things it does not bundle. This is the honest list, so you can decide before
installing rather than after:

| You supply | Needed by | Without it |
|---|---|---|
| A vault directory | everything | `job-sluice init` creates one |
| An LLM backend — an API key, or the `claude` CLI | `triage`'s judge, `cv`'s composer | `triage run --no-llm` still classifies deterministically |
| A [Camofox](https://github.com/jo-inc/camofox-browser) browser server | `ingest run`, `ingest test-source`, and job-description fetches in `triage`/`cv` | no scraping; the rest of the pipeline works on leads already in the vault |
| At least one **verified** experience entry | `cv run` | refused before any fetch or backend call, naming the two commands that fix it. It used to reach the composer first |
| A baseline CV at `baseline_rel` (default `My CV/CV.md`) | `cv run` | same, and once for the run rather than once per lead. It never cost a composer call, but it did raise |
| A Candidate Profile note with a name and contact details | `cv run`, `apply prep` | `cv run` refuses before any fetch or backend call (`skipped-config`); `apply prep` does not refuse — it builds the packet with your identity simply absent |
| cairo, pango and gdk-pixbuf, plus the `render` extra | PDF output | set `cv.renderer: script` to shell out to your own renderer instead |
| A [Google OAuth token](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md#google-access-for-track), which you mint yourself | `track` | `track run` logs a failure and exits 0 |

`job-sluice doctor --offline` reports which of these you are missing and which commands each gap
blocks. Running it immediately after installing is the fastest way to see where you stand — a
bare install honestly reports several dead components, and that is expected rather than a broken
install.

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

Use the **fully-qualified** name in the Homebrew command. Homebrew 6 requires explicit trust for
non-official taps, and installing a fully-qualified formula grants trust to that one item, tapping
and installing in a single step.

From a checkout:

```bash
git clone https://github.com/MrReasonable/sluice.git
cd sluice
pip install -e .
job-sluice --version
```

That gives you the CLI with `pyyaml` and `tzdata` as the only runtime dependencies — everything
else in `sluice/` is standard library. Opt-in extras add the rest:

```bash
pip install -e '.[render]'      # PDF rendering (cv.renderer: template, the default)
pip install -e '.[google]'      # track's Gmail + Calendar access
pip install -e '.[mcp]'         # job-sluice mcp serve
pip install -e '.[completion]'  # shell completion
```

The `render` extra is necessary but **not sufficient**: WeasyPrint links natively against cairo,
pango and gdk-pixbuf, which no Python package can supply — see
[system libraries for PDF rendering](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md#system-libraries-for-pdf-rendering).
The packaged channels install them for you, which is the main reason to prefer one over pip.

Per-channel instructions, the platform-specific library names, the macOS dynamic-linker step, and
how to pin an older release are all in
[`docs/INSTALL.md`](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md).

### Shell completion

```bash
pip install -e '.[completion]'
eval "$(register-python-argcomplete job-sluice)"
```

Completes command and flag names, and — for `--source`, `ingest enable|disable` and
`track confirm --to` — real values read live from the registered sources and the status
vocabulary, rather than a static list that could go stale. There is an oh-my-zsh/zinit plugin at
[`plugins/job-sluice/`](https://github.com/MrReasonable/sluice/tree/main/plugins/job-sluice); both
forms are a no-op until `job-sluice` and `register-python-argcomplete` are on `$PATH`, so sourcing
before installing does nothing rather than erroring.

### Naming

The PyPI name `sluice` has been squatted since 2015 by an unrelated, dormant zfs-snapshot tool with
no console script of its own — no binary collision, but `pip install sluice` could never resolve
here. So the distribution and the console script are both `job-sluice`. The import package
(`import sluice`), the `SLUICE_*` environment variables and the `~/.config/sluice/` paths are
unchanged: those are invisible to a user, and renaming them would be a breaking **config** change
for no user-visible benefit. Only what you type at a shell prompt is different.

Extras attach to the distribution name, so it is `pip install 'job-sluice[render]'` from a release
— dropping the `job-` prefix resolves to that unrelated package.

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

(`init` prints those paths fully resolved, and goes on to summarise your config and list what to
do next; `~` above stands in for your home directory, and `...` for output cut for length.)

The config lands in the XDG config directory. Export `SLUICE_CONFIG` with a path of your own to
keep it beside your vault instead, before every command rather than just `init`.

`Job Leads.base` is an Obsidian Bases view: open it in Obsidian and your leads are a sortable,
filterable table rather than a folder of files. Every lead note sluice writes links to it.

`init` never overwrites an existing artefact, so re-running it is safe. Every question is optional
except where your vault is, and a blank answer leaves that gate **unset** — which passes every lead
through. Drop `--no-input` to be asked. The config it writes has every unanswered key commented
out, so it is field-for-field equivalent to having no config file at all except for `vault_dir`.
Answering nothing also means no Candidate Profile is written, and `cv run` and `apply prep` both
need one; re-run without `--no-input` when you want to fill it in.

Do **not** copy `sluice.yaml.example` into place instead. It is a catalogue to read, not a
template: it ships illustrative values *active*, so a verbatim copy arrives with its title,
relevance and pay gates already closed and nothing saying so.

**2. See where you stand.**

```console
$ job-sluice doctor --offline
job-sluice doctor  (offline)

claude-max  claude-sonnet-4-5    ok        primary: triage, cv, track  (offline: not round-tripped)
deepseek    deepseek-v4-flash    degraded  fallback: triage, cv, track  DEEPSEEK_API_KEY unset - primary-only

1 ok, 1 degraded, 0 dead

renderer     cv.renderer                      dead      renderer 'template' could not load its ...
store        baseline_rel                     dead      baseline CV not found at the configured path ...
store        Judging Profile                  ok        found
store        Experience Library               notice    0 verified / 0 total entries -- only verified ...
...
store        Candidate Profile                dead      no name or no contact details -- cv run refuses ...
...
gates        Config.dossier_allow_hosts       notice    no exceptions granted (empty)
...
gates        TriageConfig.accept_titles       notice    abstaining (empty)
...
1 ok, 1 degraded, 3 dead, 19 notice
```

Dead components on a fresh install are the expected state, not a fault, though `doctor` still exits
non-zero while any component is dead, so a fresh install exits `1`. Each names the command it
blocks, so you only fix what you need — above, every one of them blocks `cv`, while `ingest` and
`triage` need none of them.

There are two summary lines, and they count different things: the first totals the backends above
it, the second totals the components below it. Both move with your machine, so treat the capture
as one install rather than as the answer. That one had the `claude` CLI on `$PATH`, so
`claude-max` reads `ok`; without it you get `dead  CLI 'claude' not on PATH` and a backend total
of `0 ok, 1 degraded, 1 dead`. It also had no `render` extra, which is why the renderer is `dead`
— install that and the component totals move too, exactly as they do when you add a baseline CV.

The `gates` rows sweep every **list-valued** setting and report what its current value means:
`abstaining (empty)` for a preference gate, and its own posture for the settings where empty
means something else, of which there is more than one kind. The numeric pay floors (`contract_floor_gbp_day`, `perm_floor_gbp`) get no
row here at all; they default to `0`, which is off.

**3. Look at the boards.**

```console
$ job-sluice ingest list-sources
bayt             browser   enabled EXAMPLE-SEARCH(1/1)
bwork            browser   disabled
cord             browser   enabled EXAMPLE-SEARCH(1/1)
...
wttj             browser   enabled EXAMPLE-SEARCH(1/1)
```

Boards ship enabled by default; a few are disabled, each module recording why and the date its
retirement was last checked against the live web. Add `--health` for per-source scrape state once
you have run something. Each source ships one neutral example search; your real searches belong in
`sources.<id>.searches` in your config. `EXAMPLE-SEARCH(n/m)` says `n` of that source's `m` searches
are still the shipped example rather than yours, so a fresh install shows it on every enabled board.

**4. Give it a lead, then triage.**

Nothing has scraped yet, so the vault holds no leads and triage would have nothing to classify.
Save the note from [What you end up with](#what-you-end-up-with) as
`vault/Job Applications/Job Leads/Example Systems - Senior Engineer.md`, then:

```console
$ job-sluice triage run --no-llm
triage: {'keep': 1, 'shortlist': 0, 'research': 0, 'dismiss': 0, 'needs_review': 0, 'skipped': 0, ...
```

`--no-llm` runs the deterministic tiers only — no backend call, nothing billed. On an unconfigured
install every lead lands in `keep`, which is the empty-config-abstains rule working: nothing was
discarded because nothing was configured to discard it.

**Next:** fill in the headings in `Job Applications/Judging Profile.md`, add your own searches, and
work back through the table under [What it costs to run](#what-it-costs-to-run) for whichever
stages you want. [`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md)
is the full command reference.

## What it guarantees

These are enforced by tests, not promised in prose. Each one guards a failure that is silent and
hard to undo.

- **Your edits survive.** A re-scrape of a lead you already have touches only its `last_seen`
  marker — never its status, never your notes in the body. Every other write is a surgical
  compare-and-set against the *current* note, committed atomically, so a concurrent editor's
  changes survive rather than being clobbered — best-effort rather than a lock, and a sustained
  race abstains instead of overwriting. Rewriting notes wholesale is the fragility sluice exists
  to remove.
- **Status never regresses out of the application lifecycle.** Triage may never touch a lead that has entered the application
  lifecycle, terminal states are never advanced out of, and an unrecognised status is passed
  through untouched rather than rewritten. A lead you merged away is not re-created by a later
  scrape that still matches the identity recorded at merge time — and where the posting's identity
  has drifted past that, it is re-created *visibly* as a duplicate rather than silently discarded.
- **The CV cannot invent things.** Every work-experience bullet must cite a real entry from a
  closed evidence bundle, and every number in that bullet must appear in the entry it cites; the
  profile prose, which carries no per-bullet citations, is held to a numeric floor over the whole
  source set. A figure that appears
  nowhere in your source material blocks rendering outright. The gate is handed its source set
  rather than re-parsing text, so no line of free text can mint a citable source. Above it sits an
  advisory LLM audit that withholds the send-ready CV pointer for your sign-off rather than
  blocking. Note the gate runs on the composed *text*: a custom Jinja2 template is free text sluice
  does not audit, so it can add prose the gate never saw.
- **An empty setting abstains.** Unconfigured means "no opinion", never "match nothing". Getting
  this backwards would bin an entire job hunt in silence — it happened once, and a test now fails
  the build if it recurs.
- **No personal data in this repository.** No employer names, locations, contact details or
  preferences in `sluice/` or `tests/`. Fixtures are synthetic and swept by a guard. Your search
  belongs in your config and your vault.

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
| `job-sluice leads` | maintenance passes (`dedupe`, `expire`, `dismiss`, `reconcile`, `rename`) |
| `job-sluice experience` | capture and verify experience evidence — the CV gate's only citable source (`add`, `list`, `verify`) |
| `job-sluice skills` | capture and verify skills evidence, shown to the composer as framing (`add`, `list`, `verify`) |
| `job-sluice stories` | capture and verify STAR stories (`add`, `list`, `verify`) |
| `job-sluice health` | per-source scrape baseline and retire state |
| `job-sluice mcp` | run a Model Context Protocol server over stdio (`serve`, plus `--write` for the write tools) |

The `leads` passes **report by default** and change nothing until told otherwise (`--merge`,
`--expire`, `--apply`), because they write over a set the tool computed. `dismiss` is the
exception: it writes on every call, because the verdict is the one you typed. The pipeline
commands invert that and write by default.

Full flag reference, exit codes and which stream each command writes to:
[`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md).

## MCP server

`job-sluice mcp serve` runs sluice as a Model Context Protocol server over stdio, so an agent can
call `list_leads`/`get_lead`/`doctor`/`health`/`list_evidence` directly instead of parsing CLI
output. Read-only by default; needs `pip install 'job-sluice[mcp]'`.

```bash
claude mcp add job-sluice -- job-sluice mcp serve
```

`--write` additionally registers the write tools (`dismiss_lead`, `apply_record`, `cv_run`,
`cv_signoff`, `create_lead`), each a thin layer over one facade method rather than a raw store
write. It is a per-registration trust decision: a read-only server's `tools/list` genuinely omits
their names and schemas rather than refusing them at call time. Nothing at any level can mark
evidence verified — that stays a human action at a prompt.

## Configuration

Every key is optional and falls back to a code default, so sluice runs with no config file at all.
Config resolves as code defaults < the YAML file at `$SLUICE_CONFIG` (else
`$XDG_CONFIG_HOME/sluice/config.yaml`) < environment variables.

Most paths relocate to the XDG directories. Your **vault** does not: it defaults to `./vault`,
relative to wherever you run the command, because it is your Obsidian directory rather than state
sluice owns. Set `vault_dir` before running from elsewhere, or you will get a second, empty vault
beside you. The CV working directories (`cv.output_dir`, `cv.served_dir`, `cv.render_home`, and
apply's upload directory) behave the same way, for the same reason — they name a workspace you are
standing in — so `cv run` from an unexpected directory leaves its artefacts there too.

Sluice never migrates your state for you. If you are upgrading from a version that kept its state
next to your working directory, it prints the `mv` commands — and the commands that would write
dedup state refuse to start until you have moved them, because starting from an empty dedup set can
re-create leads you merged away and risks applying to the same job twice. That refusal is
deliberately uneven: `ingest run --dry-run` and `--sink json` proceed, every `track` command
refuses including its dry runs, and `doctor` only reports, since a relocated file is exactly what
you run it to hear about. (The user-invoked `leads` passes do move notes inside your vault — that
is what you asked them to do.)

- [`sluice.yaml.example`](https://github.com/MrReasonable/sluice/blob/main/sluice.yaml.example) —
  the full catalogue with inline comments. A catalogue to read, not a file to copy.
- [`docs/CONFIGURATION.md`](https://github.com/MrReasonable/sluice/blob/main/docs/CONFIGURATION.md)
  — every key by block, with its default and what leaving it unset means.

## Documentation

| | |
|---|---|
| [`docs/AI-SETUP.md`](https://github.com/MrReasonable/sluice/blob/main/docs/AI-SETUP.md) | the contract an AI agent follows to set sluice up for you, and what it may never do |
| [`docs/INSTALL.md`](https://github.com/MrReasonable/sluice/blob/main/docs/INSTALL.md) | per-channel install, extras, system libraries, Camofox, credentials |
| [`docs/USAGE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md) | command reference: flags, exit codes, output streams |
| [`docs/CONFIGURATION.md`](https://github.com/MrReasonable/sluice/blob/main/docs/CONFIGURATION.md) | config keys by block |
| [`docs/TROUBLESHOOTING.md`](https://github.com/MrReasonable/sluice/blob/main/docs/TROUBLESHOOTING.md) | specific failures and their fixes |
| [`docs/ARCHITECTURE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/ARCHITECTURE.md) | module-by-module, the adapter seams, the store contract |

## Contributing

[`CONTRIBUTING.md`](https://github.com/MrReasonable/sluice/blob/main/CONTRIBUTING.md) has the dev
setup, the test and lint commands, and the invariants a change is expected to respect.
[`SECURITY.md`](https://github.com/MrReasonable/sluice/blob/main/SECURITY.md) covers vulnerability
reporting. The test suite is fully offline and hermetic — no browser, no network.

## Releases

Version history and migration notes are in
[`CHANGELOG.md`](https://github.com/MrReasonable/sluice/blob/main/CHANGELOG.md);
`job-sluice --version` reports what you have installed.

A breaking **config** change counts for more here than a breaking API change — nothing imports
sluice as a library, so what you have invested in is your config and your vault. Changes to what an
unset value means, to a load-bearing default, to where a file is read or written, or to what a
status transition may do all carry an explicit migration note, even when no key is renamed.

## License

MIT. See [`LICENSE`](https://github.com/MrReasonable/sluice/blob/main/LICENSE).
