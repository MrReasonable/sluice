# Sluice

Sluice is an engineered, config-driven job-hunting pipeline. It scans job
boards into a lead store, triages leads with deterministic rules plus an LLM
judge, composes a fabrication-gated CV tailored to each shortlisted role,
preps and records applications, and reconciles the funnel from email and
calendar signals. Every stage is config-first: sane defaults ship in code,
a single YAML file overrides them, and secrets come from the environment.

Installed as the `job-sluice` command (`pip install -e .` for now — see
[Install](#install)). The PyPI distribution and the console script are both
`job-sluice`; the import package stays `sluice`, so nothing under the hood or
in your config changes because of the name. `job-sluice`, not `sluice`, is
what a fresh checkout gives you on `$PATH` — see [Naming](#naming) if you are
wondering why.

## Ships no preferences

Sluice expresses no opinion about which jobs are good. That is deliberate, and it is
enforced rather than promised:

- `accept_titles` / `reject_titles`, `target_locations` / `reject_locations`,
  `reject_companies`, and the coarse ingest gate (`relevance_keep` / `relevance_drop`)
  all default to **empty**. An unconfigured gate **abstains** and passes every lead
  through, rather than silently filtering your job hunt against a stranger's taste.
  Pay floors default to `0` (off).

  Note that empty means *abstain*, not *match nothing*: an empty `target_locations`
  keeps every lead, it does not reject every lead that names a location. That
  distinction is enforced by a test, because getting it backwards would bin someone's
  entire job hunt in silence.
- The judge's criteria - who you are, what you want, what you refuse - are read at
  runtime from an Obsidian note (`Job Applications/Judging Profile.md`), never from this
  repository. The fallback compiled into the code states only that nothing is configured
  and declines to invent an opinion.
- The test suite generates its own synthetic job titles (seeded `faker`, see
  `tests/conftest.py`), so no real person's preferences are encoded in the fixtures or the
  assertions. `test_shipped_prompt_expresses_no_role_or_culture_preference` fails the
  build if a role or culture preference is ever baked back into the shipped prompt.

If you are contributing: your job search belongs in your config and your vault. It must
not land in this repo.

## Pipeline

```
ingest -> triage -> cv -> apply -> track
```

- **ingest**: scan job boards (via declarative sources) into the lead store, deduping and gating for relevance as it goes.
- **triage**: deterministic classification resolves obvious cases for free; ambiguous leads go to an LLM judge, and verdicts are written back without touching any lead already in the application lifecycle.
- **cv**: select verified source material, compose a tailored CV against a closed bundle, gate it for fabricated claims, render, and serve.
- **apply**: select eligible leads, stage the CV and a prep packet; the actual ATS form-fill is human-driven, this sub-app prepares the material.
- **track**: reconcile the application funnel from email and calendar signals, never regressing a lead's status.

`core/` underlies all five: layered config, the lead/experience store, LLM
backend clients, the shared status vocabulary, the dedup database, and the
resilience helpers (retry, timeout, rate-limit) that every stage wraps its
I/O in.

Four more command groups sit alongside the pipeline rather than inside it: `job-sluice init`
(scaffold a config), `job-sluice doctor` (preflight everything below before you spend an LLM
call finding out it's broken), `job-sluice health` (per-source scrape state), and
`job-sluice leads` (dedupe/expire/reconcile maintenance passes). See [Commands](#commands) for
the full list, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module-by-module
detail.

## Status: work in progress

Sluice currently assumes:

- an Obsidian-style markdown vault as the lead and experience store
- a Claude CLI backend (run locally or shelled out over SSH) as one option for
  the LLM judge and composer, with direct API backends (Anthropic, OpenAI,
  DeepSeek) as the alternative — see `--backend` in
  [`docs/USAGE.md`](docs/USAGE.md)
- a bundled renderer (`cv.renderer: template`, the default) that fills your
  own Jinja2 template -- or the packaged one, if you don't supply one -- with
  the composed CV and turns it into a PDF via WeasyPrint; `script`, shelling
  out to an external render pipeline you supply, remains as a full-control
  escape hatch
- a browser for ATS forms: an automated browser (Camofox) for ingest
  sourcing, and a human at the keyboard for filling in application forms
- a Google OAuth token for track's Gmail and Calendar access

Each of those started life as a seam meant to become a pluggable adapter, and
most of that work is now done rather than planned:

- **LLM backend adapters** — DONE. `sluice/backends/` self-registers four
  providers (`anthropic`, `openai`, `deepseek`, plus the flat-rate `claude-max`
  CLI shell-out); `--backend {auto,primary,fallback}` selects a *role*, and
  which provider fills each role is config (`primary_backend`,
  `fallback_backend`).
- **Bundled renderer** — DONE. `cv.renderer: template` fills a Jinja2 template
  via WeasyPrint; see [Rendering prerequisites](#rendering-prerequisites-cvrenderer-template-only)
  below. `script` (the original external-script renderer) remains as an
  escape hatch.
- **Store adapter** — the seam shipped (`core/protocols.py: Store`,
  `sluice/stores/`, a conformance suite in `tests/conformance/`), with one
  production implementation: the Obsidian vault.
  A second implementation is future work, not yet started.
- **Fetch/browser adapter** — the seam shipped
  (`core/protocols.py: Fetcher`, `sluice/fetchers/camofox.py`), with one
  production implementation: Camofox. Same status as the store seam.
- **Docs and CI** — this file, `docs/ARCHITECTURE.md`, `docs/USAGE.md`,
  `docs/CONFIGURATION.md`, `docs/TROUBLESHOOTING.md`, `CONTRIBUTING.md`,
  `SECURITY.md`; CI runs lint, a 3-Python-version test matrix, and a
  rulesync-drift gate (`.github/workflows/ci.yml`), and release-please cuts
  versioned releases from Conventional Commits.

What's still genuinely ahead: a second store/fetcher implementation (nobody
has needed one yet), and packaged installs beyond source — see
[Install](#install).

## Install

```bash
git clone https://github.com/MrReasonable/sluice.git
cd sluice
pip install -e .
job-sluice --version
```

That gives you the CLI with `pyyaml` as the only runtime dependency —
everything else in `sluice/` is standard library. Two things it does **not**
give you, both opt-in extras:

```bash
pip install -e '.[render]'   # cv.renderer: template (the default) -- see below
pip install -e '.[google]'   # track's Gmail + Calendar access
```

(The path form, not `pip install 'job-sluice[render]'`: there is no PyPI release yet, and once
there is, extras attach to the *distribution* name, `job-sluice`, not the import package --
dropping the `job-` prefix resolves to a different, unrelated package. See [Naming](#naming).)

There is no packaged install yet — no PyPI release, no Docker image, no
Homebrew formula. Source is the only path today; see
[Naming](#naming) for why the distribution will be `job-sluice` rather than
`sluice` once one exists.

### Shell completion

```bash
pip install -e '.[completion]'
```

installs [argcomplete](https://github.com/kislyuk/argcomplete), which completes group and
subcommand names, every flag, and — for `--source`/`ingest enable|disable ID` and
`track confirm --to` — real values, read live from the registered sources and the status
vocabulary rather than a static list that could go stale. Activate it for zsh:

```bash
eval "$(register-python-argcomplete job-sluice)"
```

or drop that line in your `.zshrc` via [`plugins/job-sluice/`](plugins/job-sluice/), which is
shaped as a normal oh-my-zsh/zinit plugin:

```bash
# oh-my-zsh
ln -s "$(pwd)/plugins/job-sluice" "$ZSH_CUSTOM/plugins/job-sluice"
# then add job-sluice to the plugins=(...) array in ~/.zshrc

# zinit -- `pick` is relative to the repo root, since the plugin file lives in a subdirectory
zinit ice pick"plugins/job-sluice/job-sluice.plugin.zsh"
zinit light MrReasonable/sluice
```

Both forms are a no-op until `job-sluice` and `register-python-argcomplete` are both on
`$PATH` — sourcing the plugin before installing the extra does nothing rather than erroring.

### Naming

The PyPI name `sluice` has been squatted since 2015 by an unrelated, dormant
zfs-snapshot tool (last release 2015-08-28) with no console script of its
own, so there's no binary collision — but `pip install sluice` could never
resolve to this project. Rather than ship under a name nobody could install,
the distribution and the console script are both `job-sluice`. The import
package (`import sluice`), the `SLUICE_*` environment variables, and the
`~/.config/sluice/` XDG paths are unaffected: those are invisible to a user
and renaming them would be a breaking **config** change (this project's own
[CHANGELOG](CHANGELOG.md) policy rates that above a breaking API change) for
no user-visible benefit. Only the thing you type at a shell prompt changed.

## Quickstart

```bash
job-sluice init                 # asks a few questions, writes a config and a Judging Profile
job-sluice doctor --offline     # sanity-check config, renderer and store artefacts, no network
job-sluice ingest run --help
job-sluice triage run --help
```

`job-sluice init` resolves the config location for you, so nothing here has to
reason about `XDG_CONFIG_HOME`. It never overwrites an artefact that already
exists -- re-running it is safe, and it reports what it left alone. Every
question is optional except where your vault is: a blank answer leaves that
preference gate UNSET, and an unset gate passes every lead through rather than
filtering on a value you did not choose. `--no-input --vault PATH` does the
whole thing without prompting.

Do **not** copy `sluice.yaml.example` into place instead. It is a catalogue that
ships illustrative values ACTIVE rather than commented, so a verbatim copy
arrives with its title, relevance and pay gates already closed and nothing
saying so -- measured, `is_relevant("Senior Software Engineer")` is `False`
against a fresh copy. Read it to see what a knob does; let `job-sluice init` write
the file.

`job-sluice` reads `$XDG_CONFIG_HOME/sluice/config.yaml` (`~/.config/sluice/config.yaml`
on a default setup) and keeps its own state and caches under the matching XDG
directories, so its config and state no longer follow your working directory.

Your **vault** is the exception, and it is deliberate: it defaults to `./vault`,
relative to wherever you run the command, because it is your own Obsidian
directory rather than per-system state sluice owns. Set `vault_dir` in the config
file (or `VAULT_DIR`) before running from anywhere else, or you will get a second,
empty vault beside you instead of the one you meant.

`$SLUICE_CONFIG` still overrides the config location if you would rather keep the
file elsewhere:

```bash
export SLUICE_CONFIG="$(pwd)/sluice.local.yaml"   # quoted: a path with spaces
job-sluice init                 # writes to $SLUICE_CONFIG when it is set
```

Either way the config file holds personal material (locations, employer lists,
contact details, hosts), so keep it out of any public repo -- `sluice.local.yaml`
is git-ignored for that reason.

Upgrading from a version that kept `seen.db`, `track-seen.db`,
`sluice_health.json`, `sluice_disabled.json`, `triage-audit.jsonl`,
`google_token.json` or `dossiers/` next to where you ran it? sluice never moves
your data. It prints the `mv` commands for each one -- including the companion files
a store has to move with it -- and for the two dedup databases it refuses to run
until you have moved them, because starting with an empty dedup set can re-create
leads you merged away and risks applying to the same job twice. `ingest` refuses
only on a run that would write dedup state, so `--dry-run` and `--sink json` still
work; every `track` command refuses, dry runs included.

That only applies where sluice picked the location itself. If you name a path --
an environment variable or a config key -- it is used as given, with no warning
and no refusal, because there is nothing to migrate from.

## Before you run the pipeline for real

`job-sluice doctor` (offline, then live) is the fast way to find out which of
these you're still missing — see [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
for what a `dead`/`degraded` line means and how to fix it. In outline:

- **A baseline CV** at `My CV/CV.md` in your vault (`baseline_rel`), and at
  least one **verified** entry in `Job Applications/Experience Library/` — the
  fabrication gate's only citable evidence. `cv.name` must be changed from the
  shipped placeholder `Your Name` before `cv run` will compose anything.
- **A backend** for triage's judge and cv's composer: either the `claude`
  CLI on `$PATH` (or reachable over SSH — `triage.claude_max_host`), or an
  API key for one of the direct backends (`ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`). `triage run --no-llm` needs neither.
- **A Camofox server** for `ingest run`/`ingest test-source`, and for `triage`
  and `cv` whenever a job dossier isn't already cached (the two share one
  dossier cache — see `docs/ARCHITECTURE.md`). Camofox is a separate,
  persistent headless-browser service this repository does not bundle — see
  [jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser).
  By default sluice looks for it at `http://127.0.0.1:9377`
  (`CAMOFOX_URL`); see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the
  full set of `CAMOFOX_*` variables. `track` and a non-`--offline` `doctor` still
  reach the network for their own reasons; see the genuinely-offline command
  list in `CHANGELOG.md`.
- **A Google OAuth token** for `track`, obtained on first `track run` via an
  interactive consent flow (needs `pip install -e '.[google]'`).

## Commands

Nine top-level command groups. Full flag reference, exit codes, and which
stream each command writes to: [`docs/USAGE.md`](docs/USAGE.md).

| Command | Purpose |
|---|---|
| `job-sluice init` | scaffold a config and a Judging Profile |
| `job-sluice doctor` | preflight backends, the renderer, cv identity, store artefacts, gate posture |
| `job-sluice ingest` | scrape configured job boards into the lead store (`list-sources`, `run`, `test-source`, `enable`, `disable`) |
| `job-sluice triage` | classify leads: deterministic rules, then an LLM judge (`run`, `normalize-status`) |
| `job-sluice cv` | compose, gate and render a tailored CV, then sign off on it (`run`, `signoff`) |
| `job-sluice apply` | stage a CV + prep packet, then record a submitted application (`prep`, `record`) |
| `job-sluice track` | reconcile the funnel from email + calendar signals (`run`, `confirm`, `dismiss`) |
| `job-sluice leads` | maintenance passes -- report by default, write only when told (`dedupe`, `expire`, `reconcile`) |
| `job-sluice health` | per-source scrape baseline + retire state |

## Rendering prerequisites (`cv.renderer: template` only)

Everything in this section is a prerequisite of ONE renderer -- `template`, the default.
`cv.renderer: script` needs none of it: it shells out to a render script you supply and
never imports jinja2 or WeasyPrint, so if you are on `script` you need neither the
`render` extra nor WeasyPrint's system libraries, and a `script` setup that works today
is unaffected by anything below.

`cv.renderer` defaults to `template`: sluice fills a Jinja2 template -- the packaged
default, or your own via `cv.template`, e.g. `docs/cv-template-example.html.j2` -- with
the parsed CV, then hands the result to WeasyPrint to produce a PDF. The fabrication
gate runs on the composed text *before* any template exists, so the PDF is derived
from gate-approved content rather than identical to it: your own template is free text
sluice does not audit, so it can add prose the gate never saw or a conditional that
drops a gated section, either of which the gate cannot catch after the fact. Rendering
needs an extra, and there is no way to skip it:

```bash
pip install -e '.[render]'
```

...and, separately, WeasyPrint's own **system** libraries -- cairo, pango, and
gdk-pixbuf. Those are **not** a Python dependency and cannot be made one (WeasyPrint
links against them natively), so install them with your platform's package manager
(Homebrew on macOS, `apt`/`dnf` on Linux -- see WeasyPrint's own installation docs for
the exact package names on your system).

**macOS, measured rather than assumed:** with cairo/pango/gdk-pixbuf installed via
Homebrew, `import weasyprint` still failed until the dynamic linker was told where to
find them:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
```

None of this is new work removing a real limitation -- a bare `pip install -e .`
still cannot produce a PDF with `template`, because those system libraries sit outside
pip's reach no matter what this project ships. What changed is *when* the failure
surfaces: `template` with the extra or the libraries missing now raises at renderer
construction, before a CV is ever composed, instead of arriving silently after an LLM
composition and a fabrication-gate pass have already spent tokens on a CV that was
never going to render. `script` gained the same timing for its own, different
precondition -- a `cv.render_script` that is missing or is not a file -- which is why
both renderers fail early even though only one of them has anything to do with
WeasyPrint.

`cv.renderer: script` remains available if you would rather shell out to your own
render pipeline than use `template`; see `sluice.yaml.example`.

More renderer/backend/store/browser failures and their fixes:
[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Configuration

Every config key is optional and falls back to a code default. See
[`sluice.yaml.example`](sluice.yaml.example) for the full catalogue with
inline comments, and [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for a
reference organized by block with each key's default and what leaving it
unset means.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup, the test/lint
commands, and the invariants a change is expected to respect.
[`SECURITY.md`](SECURITY.md) covers vulnerability reporting.

## Releases

Version history and migration notes live in [`CHANGELOG.md`](CHANGELOG.md), and
`job-sluice --version` reports what you have installed.

A breaking **config** change counts for more here than a breaking API change -- nothing
imports sluice as a library, so what you have invested in is your `sluice.yaml` and your
vault. Changes to what an unset value MEANS, to a load-bearing default, to where a file is
read or written, or to what a status transition may do all carry an explicit migration
note, even when no key is renamed.

Releases are cut by [release-please](https://github.com/googleapis/release-please) from
Conventional Commit subjects, with the changelog entry edited by hand in the release PR
before it merges -- a generated subject cannot tell you your config now means something
different, which is the change class that matters most here.

## License

MIT. See [`LICENSE`](LICENSE).
