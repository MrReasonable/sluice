# Troubleshooting

`job-sluice doctor` (offline, then live) is the first move for almost everything below — it
preflights backends, the renderer, `cv.name`/`cv.contact` identity, the store's artefacts, and
track's Google adapter, and names which commands each dead/degraded result blocks. This page
is what to do once it has told you what's wrong.

## Rendering fails at construction (`cv.renderer: template`)

`template` (the default) needs the `render` extra plus WeasyPrint's own **system** libraries —
cairo, pango, gdk-pixbuf. Neither pip install alone is enough, and there is no way around
installing both:

```bash
pip install -e '.[render]'
```

...and then cairo/pango/gdk-pixbuf via your platform's package manager (Homebrew on macOS,
`apt`/`dnf` on Linux — WeasyPrint's own install docs list the exact package names per distro).

**Measured on macOS**: even with all three installed via Homebrew, `import weasyprint` still
raised `OSError: cannot load library 'libgobject-2.0-0'` — not `ImportError` — until the
dynamic linker was pointed at Homebrew's lib directory:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
```

Put that in your shell profile so it survives new sessions. This isn't a limitation this
project introduces or can remove — `pip install job-sluice` can never make cairo/pango/
gdk-pixbuf appear, because WeasyPrint links against them natively. What the renderer
construction check buys you is *when* the failure surfaces: at `cv run` startup, before any
LLM composition or fabrication-gate pass has spent tokens on a CV that was never going to
render — not silently after.

If you'd rather avoid all of this, `cv.renderer: script` needs neither the extra nor the
system libraries — it shells out to a render script you supply. See `sluice.yaml.example`.

## `cv run` refuses with `skipped-config`

`cv.name` is still the shipped placeholder `Your Name`. This name becomes the composed CV's
`<h1>`, so a compose is refused *before any LLM spend* rather than producing a PDF headlined
with a literal placeholder — set `cv.name` in your config.

## A gate-clean CV is still refused (a renderer `precheck` violation)

The hard fabrication gate (`cv/validate.py`) can pass while the `template` renderer's own
grammar check (`precheck`) still refuses — a formatting mismatch, not a fabrication one. The
engine folds both into the same one retry the LLM gets, so this usually self-corrects; if it
doesn't, the refusal message names the specific formatting rule it hit (a date-range
separator, a missing field, a mis-cased section header, and so on — see
`docs/ARCHITECTURE.md`'s "A renderer's `precheck`" section for the full list and why it exists
as a *second*, narrower gate rather than being folded into `validate.py`). If the refusal
asks for something that can't be answered without inventing content (a `LOCATION` field
nothing upstream supplied, for instance), that is a known, deliberately-left gap — see
`docs/ARCHITECTURE.md` — not something to work around by guessing a value.

## Ingest/dossier fetch fails: Camofox unreachable

`ingest run`/`ingest test-source` drive a live Camofox session, and `cv`/`triage` reach it
lazily on a dossier cache miss. Camofox is a separate, persistent headless-browser service —
see [jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) — that
this repository does not bundle or start for you. Confirm it's actually running and reachable
at `$CAMOFOX_URL` (default `http://127.0.0.1:9377`):

```bash
curl -s --connect-timeout 3 --max-time 5 "${CAMOFOX_URL:-http://127.0.0.1:9377}"
```

`job-sluice doctor` doesn't check this (it never opens a browser, by design — a relocated
store is what you run `doctor` to hear about, not a live round-trip on every seam). Every
other command — `triage run --no-llm`, `leads`, `health`, `init`, `doctor --offline` — is fully
offline and unaffected.

## A backend is `dead` or `degraded` in `doctor`'s output

- **`dead`, `<KEY_VAR> unset`** on a role used as *primary* anywhere: set the key
  (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`), or switch that role to
  `claude-max`, which needs no key — it shells out to a local or SSH-reachable `claude` CLI.
- **`degraded`, `<KEY_VAR> unset - primary-only`**: the *fallback* role has no key. This is a
  sanctioned degrade — `auto` still runs on the primary alone — but `doctor --strict` fails the
  build on it, and `--backend fallback` (an explicit, non-`auto` request) hard-errors rather
  than degrading, since there's nothing left to fall back to.
- **`dead`, `CLI '<path>' not on PATH`** (under `--offline`): the configured `claude_max_path`
  isn't found locally. Either install/alias it there or point `claude_max_host` at a machine
  where it is.
- **`dead`, `unknown backend '<name>'`**: a typo'd `primary_backend`/`fallback_backend`. Valid
  names are listed in the error.

A live (non-`--offline`) `doctor` round-trips one token per distinct backend to confirm it
actually answers, not just that a key is present.

## Store / vault problems

- **`dead`, vault missing**: `vault_dir` (or `$VAULT_DIR`) doesn't resolve to a real directory.
  Blocks every pipeline command. `job-sluice init --vault PATH` creates one.
- **`dead`, baseline CV unreadable**: `baseline_rel` (default `My CV/CV.md`, relative to the
  store root) isn't there. Blocks `cv`.
- **`degraded`, Judging Profile absent**: `triage` falls back to the shipped neutral default,
  which states only that nothing is configured and prefers `research` over a confident
  verdict. Not fatal, just under-informed — fill in `Job Applications/Judging Profile.md`.
- **`notice`, Experience Library counts**: informational only — `<verified> verified / <total>
  total`. Zero verified entries means every CV fails the fabrication gate (no citable source
  material), which is a `cv run` failure, not a `doctor` one.
- **A command refuses citing a relocated state file** (`seen.db`, `track-seen.db`,
  `sluice_health.json`, and friends): see "Upgrading from a pre-XDG install" in
  `docs/CONFIGURATION.md` — the fix is the printed `mv` command, not a config change. This is
  deliberately loud rather than silent: starting a dedup pass from an empty set can re-create
  a lead you'd merged away, or apply to the same job twice.

## `track` reauth needed

`track run` exits 1 with `track: google reauth needed (token refresh failed)` when the stored
OAuth token is genuinely dead — Google REFUSED the refresh, or the file is present but
unparseable. Delete the file at `track.token_path` (see `docs/CONFIGURATION.md`; default
`<XDG_STATE_HOME>/sluice/google_token.json`) and re-run — `track` will walk you through the
interactive consent flow again. Needs `pip install -e '.[google]'`.

**A network problem does not produce this.** A dropped connection, a DNS failure, a Google
5xx or a disk-full error while writing the refreshed token are reported as ordinary run
failures — named in the digest, recorded in the dead-letter store, and retried — precisely so
that deleting a perfectly good credential is never the remedy for a Wi-Fi blip (#142). If you
see a transport error rather than this message, re-run rather than re-authorising.

## Shell completion isn't offering anything

`job-sluice[completion]` (argcomplete) needs its shell hook activated — see the README's Shell
completion section. Two independent things must both be true: `job-sluice` itself on `$PATH`,
and `register-python-argcomplete` on `$PATH` (only present once the `completion` extra is
actually installed, separately from the base package). The plugin wrapper
(`plugins/job-sluice/job-sluice.plugin.zsh`) checks both and stays silent — no error — if
either is missing, which is the expected shape for a shell plugin whose prerequisite isn't met
yet, but it also means a missing prerequisite gives no visible signal that something needs
installing.
