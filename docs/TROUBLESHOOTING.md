# Troubleshooting

`job-sluice doctor` (offline, then live) is the first move for almost everything below — it
preflights backends, the renderer, the store's artefacts (including the Candidate Profile
note's own declared name/contact — #133/#107), and track's Google adapter, and names which
commands each dead/degraded result blocks. This page is what to do once it has told you what's
wrong.

## Rendering fails at construction (`cv.renderer: template`)

`template` (the default) needs the `render` extra plus WeasyPrint's own **system** libraries —
cairo, pango, gdk-pixbuf. A pip install alone is never enough, because pip cannot ship native
libraries.

**A packaged install usually avoids this entirely** — see the channel table under
[Install](../README.md#install) for what exists. The `.deb`/`.rpm` *recommend* WeasyPrint, so a
default `apt`/`dnf` install pulls cairo and pango for you; the container image ships them
already built in. The deb/rpm caveat is that a recommendation is not a requirement:
`apt --no-install-recommends`, or dnf with `install_weak_deps=False`, skips it. If that is how
you installed, ask the package manager for the render stack directly rather than reaching for
pip — the packaged install puts sluice on a distro-managed Python, where PEP 668 blocks
`pip install` anyway:

```bash
sudo apt install weasyprint python3-jinja2        # Debian, Ubuntu
sudo dnf install python3-weasyprint python3-jinja2  # Fedora
```

What follows is for a pip install.

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

**If you installed via `brew install`, you don't need this export.** The failure above is
specific to a non-Homebrew Python: Homebrew's own CPython patches `ctypes`' library-search
fallback to include the Homebrew prefix, so `import weasyprint` resolves cairo/pango/
gdk-pixbuf with no export set. The variable that decides this is the *interpreter*, not the
libraries — a `pip install` under a version-manager Python still needs it.

If you'd rather avoid all of this, `cv.renderer: script` needs neither the extra nor the
system libraries — it shells out to a render script you supply. See `sluice.yaml.example`.

## `cv run` refuses with `skipped-config`

The candidate's identity — read from `Job Applications/Candidate Profile.md` in your vault,
not `sluice.yaml` — has no declared name or no declared contact channel (mobile, email or
LinkedIn). **This is a behaviour change from before #133/#107**: a config that left
`cv.contact` blank on purpose (because your own `cv.template` hardcodes contact details)
used to compose fine, name-only. That case now also refuses — a blank contact block is no
longer distinguished from a blank name, so declare at least one contact channel even if your
template never renders it. The derived name becomes the composed CV's `<h1>`, so a compose is
refused *before any LLM spend* rather than producing a PDF headlined with a blank line. Fill
in `forenames`/
`surname` and at least one of `mobile`/`email`/`linkedin` directly in the note's frontmatter, in
Obsidian. `job-sluice init` only helps here when the note is wholly undeclared — its interview
gate is *anything* declared (`has_any_declared`, `core/candidate.py`), not "every identity field
is filled in", so a user who already declared, say, only `email` satisfies that gate and `init`
skips the interview on every future run, leaving this refusal unresolved until you edit the note
by hand. If the note exists but is entirely blank, `init` *does* re-ask — but its write refuses
(never-clobber: the note already exists) and your answers land in `Candidate
Profile.init-scaffold.md` beside it instead; merge that file's frontmatter into the real note and
delete the scaffold. If you're seeing this after upgrading from an older config that set
`cv.name`/`cv.contact` instead, `cv run` and `job-sluice doctor` — the two commands that load the
`cv:` block — will have already raised a louder error naming the same fix; see the next section.

## `cv.name`/`cv.contact` in `sluice.yaml` (a config from before #133/#107)

These two config keys are **removed** — every sub-app that loads `cv:` (via `load_cv_config`)
now raises at load if either is still present, naming `Job Applications/Candidate
Profile.md` and its five identity frontmatter keys (`forenames`, `surname`, `email`, `mobile`,
`linkedin`). Move the values into that note (as plain frontmatter, `key: value`) and delete
both keys from the `cv:` block. This is the one config change every existing installation must
make: a composed CV also loses its contact **labels** as a result (`contact_block` emits the
bare declared values — mobile, then email, then LinkedIn — one per line, undeclared lines
omitted; the old `cv.contact` catalogue's labels, e.g. "Phone number: ...", were a formatting
choice living in a value you could edit, and there is nowhere left in config to put one — write
the label into the field's own value if you want it back).

## A config that used to load now refuses: "must be a YAML list" (#176)

A key that takes a **list** or a **mapping** was written as a bare scalar. That used to load
and silently mis-configure whatever read it; it now refuses at load, so the run stops instead.

```text
job-sluice: triage.target_locations must be a YAML list, but got a str.
Write it as `target_locations: [first, second]`, or one `- first` per line.
A bare `target_locations: value` is a STRING, and sluice would read it one
CHARACTER at a time.
```

The fix is one edit — write the value as a list. Before, the broken scalar form:

```yaml
# before: a string
triage:
  target_locations: remote
```

After, the corrected list form:

```yaml
# after
triage:
  target_locations: [remote]
```

Or, equivalently, spelled as a multi-line list:

```yaml
# after, equivalent multi-line spelling
triage:
  target_locations:
    - remote
```

**Why it refuses rather than guessing.** A scalar was read one character at a time, which is
why this matters more than a formatting nit. Measured on the affected keys: `relevance_drop:
senior` became `['s','e','n','i','o','r']` and dropped **every** lead at ingest, before dedup
and before any note was written; `triage.target_locations: remote` matched almost every
location and behaved exactly like an unconfigured filter. Nothing said so in either case.

Coercing `remote` into `[remote]` would fix the one-word case and quietly break the likelier
one: `target_locations: London, Berlin` is a single YAML string, and coerced it becomes one
token matching nothing — every located lead rejected, still silently. Refusing is the only
answer that cannot guess wrong.

**`sources.<id>.searches` is the exception to the wording.** Its entries are themselves lists,
so the generic advice does not apply and the message says so:

```yaml
sources:
  reed:
    searches:
      - ["My label", "https://example.invalid/jobs"]
      - ["Another", "https://example.invalid/other", {job_type: perm}]
```

Nothing shipped is affected: `sluice.yaml.example` and anything `job-sluice init` writes
already use list syntax throughout.

## `cv run` reports `skipped-gate` (no CV was produced)

`skipped-gate` means no composition attempt ever cleared the hard fabrication gate, so
nothing was rendered for that lead. The reasons print as indented lines directly under the
per-result summary line (#258 — before it, only the `violations=<N>` count did, at every
log level):

```text
cv: skipped-gate Job Applications/Job Leads/... served=None violations=2 audit_flags=0 ...
  UNSOURCED SKILL 'Widget, Gadget': not in the bundle
  STRUCTURAL: composed CV lacks the exact 'PROFILE' header, so the profile fabrication check did not run
```

Read the category that opens each line:

- `UNSOURCED SKILL` — a **SKILLS** line whose text is not in the bundle's source blocks.
  Often a *formatting* mismatch rather than an invented skill: the whole stripped line is
  compared as one token sequence, so a category label or a reordering is refused even when
  every term is real.
- `MISATTRIBUTED SKILL` — a **WORK bullet** naming a skill that some entry in the corpus
  does declare, but not one of the entries that bullet cites. Right skill, wrong role: the
  fix is the citation, not the skill.
- `INVENTED METRIC` / `UNCITED BULLET` / `BAD CITATION` — the citation gate on **WORK
  bullets**. The figure or bullet is not derivable from the entry that bullet cites, so
  the fix is the citation or the figure.
- `INVENTED PROFILE METRIC` — the same question asked of **PROFILE prose**, which carries
  no per-bullet citations at all. The figure has to appear somewhere in the whole source
  set (the baseline plus every entry), not in a cited entry — so adding an `[id]` to
  profile prose does not answer it, and is not meant to: the gate refuses to let prose
  launder a citation.
- `FABRICATED` / `MISSING EMPLOYER` / `NOT REVERSE-CHRONOLOGICAL` — whole-document checks.
- `STRUCTURAL` — the composed CV's shape, not its content: a missing `PROFILE` /
  `WORK EXPERIENCE` header, or a header block that does not match the Candidate Profile
  note's declared name and contact block.
- `FORMAT` — the `template` renderer's own grammar; see the next section. A `precheck`
  refusal reports as `skipped-gate` like any other, even though the CV cleared the
  fabrication gate itself.

`violations=0` on a `skipped-gate` row does **not** mean there is nothing to read. The
blocking tier is `violations` *plus* the slop linter's HARD findings, so an em dash or a
literal `--` anywhere in the document bins the lead on its own, and the only indented lines
are `SLOP EM-DASH:` / `SLOP DOUBLE-HYPHEN-DASH:`. Those are always answerable without
inventing anything — rewrite the punctuation. Note the same block also carries the
non-blocking STYLE tier (`SLOP <phrase>:`), so not every `SLOP` line you see is the reason
the lead was binned; the two HARD labels are.

These are the **last** attempt's findings and only those. The engine composes at most
twice, feeding the first attempt's findings into the second, but it re-derives the list
from scratch against each draft rather than accumulating — so a line here may be one the
model was already shown and did not fix, or one the retry introduced while fixing
something else. The first attempt's findings are neither printed nor logged, so a single
run cannot tell you which. What does distinguish them is repetition **across runs**: a
category that keeps coming back on fresh invocations points upstream of the model — the
composer prompt, the evidence corpus, or the Candidate Profile note — rather than at a
one-off bad draft.

## A gate-clean CV is still refused (a renderer `precheck` violation)

The hard fabrication gate (`cv/validate.py`) can pass while the `template` renderer's own
grammar check (`precheck`) still refuses — a formatting mismatch, not a fabrication one. The
engine folds both into the same one retry the LLM gets, so this usually self-corrects; if it
doesn't, the refusal prints as a `FORMAT:` line under the `skipped-gate` summary (see the
section above) naming the specific formatting rule it hit: a date-range separator, a missing
field, a mis-cased section header, and so on — see `docs/ARCHITECTURE.md`'s "A renderer's
`precheck`" section for the full list and why it exists as a *second*, narrower gate rather
than being folded into `validate.py`. If the refusal
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

## A source reports `drift=blank` or `drift=fallback` (leads withheld)

The board is returning rows, but the content itself has degraded — a rotted card selector
that no longer finds a company, or an extractor's own fallback path filling in blanks (see
`docs/ARCHITECTURE.md`'s ingest section for the full classifier). Both reasons **withhold**
that source's leads from the vault for the run rather than writing them — the digest and any
Telegram notify show a non-zero `withheld` count.

- **`drift=fallback`**: an extractor's own degraded code path fired (e.g. an anchor-only
  fallback when the card markup it targets matched nothing). Fix the extractor's selectors;
  `job-sluice ingest test-source ID --raw` prints the raw fetch payload so you can see what
  the page actually rendered.
- **`drift=blank`**: the source's own company/link completeness rate collapsed relative to
  its historical high-water. This needs the source to have had at least one healthy run on
  record — a brand-new source, or one already broken when you started, cannot trip this (see
  ARCHITECTURE.md's note on it being a regression detector, not a retroactive one). Two
  consecutive low runs are required before it fires, so a single bad fetch will not withhold
  anything.

**Recovery is automatic once the extractor is fixed**: a withheld lead is never recorded in
`seen.db`, so the very next run re-fetches and re-evaluates it from scratch. There is nothing
to manually re-queue.

## A source reports `drift=login`

The board redirected the search to (or otherwise landed on) a login/auth-wall page — visible
even when the wall still renders a handful of chrome rows, which is exactly the shape a bare
zero-row check cannot see. This is usually one of:

- **An expired or logged-out Camofox profile.** Check `job-sluice doctor`'s `camofox` row for
  which profile the run used, and re-authenticate it if needed.
- **The board genuinely requires a login it did not before.** Whether this auto-retires
  depends on the SHAPE of the login wall. A wall returning **zero** rows is `login`'s only
  route to retirement: `login` is deliberately excluded from `_RECOVERABLE`, so three
  consecutive zero-row login runs retire the source exactly like an unexplained zero
  would, and `job-sluice health`'s cumulative `BROKEN reason=login xN` streak is the
  signal for it. A wall that still renders a handful of chrome rows (the shape incidents
  3/4 actually were) never retires — `_is_dead` requires a zero count before it even
  looks at the reason — and there is no cumulative counter for that case either; the
  per-run digest and any Telegram notify are the signal instead, since `drift=login`
  fires and withholds on every affected run. Either way, if the board has permanently
  moved behind a login wall and it is NOT auto-retiring, disable it by hand (`ingest
  disable ID`).

`drift=login` also withholds that run's leads, for the same reason and with the same
automatic recovery as `blank`/`fallback` above — **provided the source is still enabled**.
That recovery is about `seen.db` only (a withheld lead is never recorded as seen, so any
future run re-fetches it), not about the source running at all: a source you disabled by
hand (`ingest disable ID`, above) stays disabled until you `ingest enable` it again, and
re-authenticating alone will not bring it back.

## A backend is `setup`, `dead` or `degraded` in `doctor`'s output

- **`setup`, `<KEY_VAR> unset`** on a role used as *primary* anywhere: set the key
  (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`), or switch that role to
  `claude-max`, which needs no key — it shells out to a local or SSH-reachable `claude` CLI.
- **`degraded`, `<KEY_VAR> unset - primary-only`**: the *fallback* role has no key. This is a
  sanctioned degrade — `auto` still runs on the primary alone — but `doctor --strict` fails the
  build on it, and `--backend fallback` (an explicit, non-`auto` request) hard-errors rather
  than degrading, since there's nothing left to fall back to.
- **`CLI '<path>' not on PATH`**: the `claude` binary isn't found locally. The STATE depends on
  whether you named the path, and so does the exit code. Left at the shipped default `claude`,
  it is `setup` (exit 0) — you have not installed it yet. If you set `claude_max_path` /
  `compose_claude_path` to a path of your own and it isn't there, that is `dead` (exit 1): you
  told sluice where the binary is and it isn't. Either install/alias it there or point
  `claude_max_host` at a machine where it is. Checked in both `--offline` and live runs, so the
  two modes agree. On a role used only as *fallback* it is `degraded` instead — `auto` still
  runs primary-only — which `--strict` fails on.
- **`dead`, `unknown backend '<name>'`**: a typo'd `primary_backend`/`fallback_backend`. Valid
  names are listed in the error.

A live (non-`--offline`) `doctor` round-trips one token per distinct backend to confirm it
actually answers, not just that a key is present.

## Store / vault problems

- **vault missing**: the vault directory doesn't exist. Blocks every pipeline command, and the
  STATE again turns on whether you named it. With nothing configured it is `setup` (exit 0) —
  you haven't run `job-sluice init --vault PATH` yet, which creates one. With `vault_dir` set in
  your config, or `$VAULT_DIR` exported, it is `dead` (exit 1): the vault you named has moved or
  been deleted — an unmounted drive, a renamed Obsidian folder, a Syncthing path change. Point
  the config at where it actually is, or bring the volume back.
- **baseline CV not found or empty**: `baseline_rel` (default `My CV/CV.md`, relative to the
  store root) isn't there, or has no content. Blocks `cv`, and follows the same rule: at the
  shipped default it is `setup` (exit 0, you haven't written one yet), while a `baseline_rel`
  you set yourself that isn't there is `dead` (exit 1) — you told sluice where your CV is, so
  it has been renamed or moved. A baseline that IS there but
  cannot be READ does not produce a `baseline_rel` row at all — the permissions error is allowed
  to propagate rather than be read as "absent", so the whole store section collapses to a single
  `store | preflight | dead` row carrying the real error. Fix the permissions and the ordinary
  rows come back. (A baseline that is a symlink pointing outside the vault reads `ok`; sluice
  reads through it. The symlink refusals are on the evidence directories, not on this file.)
- **`degraded`, Judging Profile absent**: `triage` falls back to the shipped neutral default,
  which states only that nothing is configured and prefers `research` over a confident
  verdict. Not fatal, just under-informed — fill in `Job Applications/Judging Profile.md`.
- **Experience Library / Skills Inventory / STAR Stories counts** (#164): one row per evidence
  corpus — `<verified> verified / <total> total entries`. Zero verified `experience` entries
  **blocks `cv`** (#242), as a `setup` row: `cv run` refuses such a vault outright, once for the run
  and before any fetch or backend call, so `doctor` grades it as the blocker it is rather than
  reporting it informationally. (It used to say the opposite — that this was "a `cv run`
  failure, not a `doctor` one" — which left `doctor` calling an install fine about the very
  thing that stopped the next command.) The other two stay `notice`: since #165 a verified
  `skills` entry reaches the composer as framing (citable by nothing) and a verified `stories`
  entry is consumed by nothing yet, so an empty one of either blocks nothing. A non-zero
  PENDING count also gets `; <pending> proposed and awaiting review (job-sluice <kind> verify)`
  — an entry `<kind> add` captured sits in `_inbox/` doing nothing until a human runs that
  command.
- **`dead`, an evidence corpus that cannot be read**: `<Corpus> | dead | cannot be read — …`,
  and this one genuinely is `dead` rather than `setup` — the directory exists and the store
  cannot read it, which is a fault rather than an unfinished setup step, so it exits 1.
  one row per affected kind and only for that kind. The usual cause is a symlinked directory:
  the store refuses to read or write through one anywhere below the vault root, because
  promoting an entry from behind it would make content from outside your vault citable — and
  `verify`'s cleanup would then delete a file outside your vault. Move the real folder into the
  vault. Only the `experience` row names `blocks: cv`; nothing composes off `skills`/`stories`
  yet. An interactive `job-sluice init` reports the same cause as a `FAILED` line, still writes
  your config and Judging Profile, and skips the capture step rather than offering it against a
  corpus it could not read; `--no-input` never reads the corpus at all.
- **A command refuses citing a relocated state file** (`seen.db`, `track-seen.db`,
  `sluice_health.json`, and friends): see "Upgrading from a pre-XDG install" in
  `docs/CONFIGURATION.md` — the fix is the printed `mv` command, not a config change. This is
  deliberately loud rather than silent: starting a dedup pass from an empty set can re-create
  a lead you'd merged away, or apply to the same job twice.

## `track` reauth needed

`track run` exits 1 with `track: google reauth needed (token refresh failed)` when the stored
OAuth token is genuinely dead — Google REFUSED the refresh, or the file is present but
unparseable. Delete the file at `track.token_path` (see `docs/CONFIGURATION.md`; default
`<XDG_STATE_HOME>/sluice/google_token.json`) and produce a fresh one the same way you produced
the first: sluice reads and refreshes the token but never runs the OAuth consent flow itself, so
nothing here will prompt you. [`docs/INSTALL.md`](INSTALL.md#google-access-for-track) has the
procedure and the scopes. Needs the `google` extra.

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
