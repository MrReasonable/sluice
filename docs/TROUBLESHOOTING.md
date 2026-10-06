# Troubleshooting

`job-sluice doctor` (offline, then live) is the first move for almost everything below — it
preflights backends, the renderer, the store's artefacts (including the Candidate Profile
note's own declared name/contact — #133/#107 — and the CV Layout note), and track's Google adapter, and names which
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

The printed line names which note refused.

**The CV Layout note disappeared after the run began.** `cv run` checks for
`Job Applications/CV Layout.md` once, before it starts (an absent note at that point stops the
whole run with exit 2 instead), so this result means the note was moved or deleted while the
run was going. Put it back and re-run.

**The Candidate Profile has no name or no contact.** The candidate's identity — read from `Job Applications/Candidate Profile.md` in your vault,
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
  REPLY: R2 bullet 1 contains a bracket -- put entry ids in "cites", never in the text
  WRONG EMPLOYER: R1 bullet 2 cites EB1, which belongs to Example Beta - Cut build time by 40%
```

Read the category that opens each line:

- `REPLY` — the model's reply itself: not one JSON object, a field missing or the wrong
  type, a slot the CV Layout does not have, a bracket or a line break inside a text, or no
  bullets in any role that can carry them. The retry is told exactly which; nothing in your
  vault needs to change.

  Some `REPLY` lines are about how the model wrote a **number** or a **word**, because the
  gate can only check text it reads the same way the PDF shows it. The profile or a bullet is
  refused when it writes a number without the digits 0-9 (a Roman, circled or CJK numeral, a
  vulgar fraction), puts an unusual character between two digits (`8·3`), writes a decimal
  with a comma (`2,5x`), groups a number with a plain space (`3 100`, which could be one
  number or two), or uses a look-alike letter: a full-width or mathematical letter, a ligature,
  a word that mixes Latin with another script, or a non-Latin letter written against a digit
  (a Cyrillic or Greek O in `8O%`). Each would let a figure or a name slip past
  the checks below while the PDF showed it plainly. The fix is the model's to make on the
  retry (`3,100` or `3.5`, plain letters); your vault text is never refused for it. One
  residual: an ASCII letter against a digit (`8O%` with a Latin O, `2l0`) is not refused,
  since `5G` and `O2` are real text, so only the digits of it are checked.
- `INVENTED METRIC` / `UNCITED BULLET` / `BAD CITATION` — the citation gate on **WORK
  bullets**. The figure or bullet is not derivable from the entry that bullet cites, so
  the fix is the citation or the figure.

  A figure is read whole, digits joined across thousands separators: `50,000` is the one
  number 50000 (as are `50 000` written with a non-breaking or thin space), so an entry
  saying `50,000` licenses `50,000` in a bullet but not `50`. A decimal must match exactly:
  `3.50` in an entry does not license `3.5`. When your own entry groups a number with a plain
  space, both readings count from it, since only you know which you meant.
- `INVENTED PROFILE METRIC` — the same question asked of **PROFILE prose**, which carries
  no per-bullet citations at all. The figure has to appear somewhere in the whole source
  set (every verified entry), not in a cited entry — so adding an `[id]` to
  profile prose does not answer it, and is not meant to: the gate refuses to let prose
  launder a citation.
- `WRONG EMPLOYER` — a **WORK bullet** citing an entry that does not belong to the role it
  sits under: the entry belongs to another role on the CV, to a company left off it, or to no
  company at all. The fix is the citation, or the entry's `Company:` and the CV Layout.
- `MISATTRIBUTED TOOL` — a **WORK bullet** naming a tool that some verified entry lists in
  `Tools:` but none of the entries it cites lists or mentions. Right tool, wrong role: the
  fix is the citation. When the quoted "tool" is an ordinary word rather than a named tool,
  the fix is your `Tools:` instead: see "Many leads skipped with `MISATTRIBUTED TOOL` on an
  ordinary word" below.
- `FABRICATED` — a term you listed in `cv.fabrication_decoys`, found as a whole term in the
  profile or a bullet the model wrote.

`violations=0` on a `skipped-gate` row does **not** mean there is nothing to read. The
blocking tier is `violations` *plus* the slop linter's HARD findings, so an em dash or a
literal `--` in the profile or a bullet the model wrote (never in your own vault text, which renders as written) bins the lead on its own, and the only indented lines
are `SLOP EM-DASH:` / `SLOP DOUBLE-HYPHEN-DASH:`. Those are always answerable without
inventing anything — rewrite the punctuation. Note the same block also carries the
non-blocking STYLE tier (`SLOP <phrase>:`), so not every `SLOP` line you see is the reason
the lead was binned; the two HARD labels are.

These are the **last** attempt's findings and only those. The engine composes at most
twice, feeding the first attempt's findings into the second, but it re-derives the list
from scratch against each draft rather than accumulating — so a line here may be one the
model was already shown and did not fix, or one the retry introduced while fixing
something else. The result line cannot tell you which, but the run's diagnostic artefacts
can. In the lead's working directory, `<cv.output_dir>/<slug>/`, `prompt.attempt-2.txt` is
the retry prompt and ends with the first attempt's findings verbatim, `reply.attempt-1.txt` and
`reply.attempt-2.txt` are the two replies they were found in, exactly as received, and `run.json` lists the bundle
entry ids the composer was given (see `cv run` in `docs/USAGE.md`). Repetition **across
runs** still says something one run's artefacts cannot: a category that keeps coming back on
fresh invocations points upstream of the model — the composer prompt, the evidence corpus,
the Candidate Profile note, or the lead's own `culture_flags`/`triage_concerns` (fixed by editing the
note) — rather than at a one-off bad draft.

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

## LinkedIn warns that a search uses the retired `/jobs/search/` address

LinkedIn replaced its signed-in job search. A `https://www.linkedin.com/jobs/search/?...` URL now
redirects to `/jobs/search-results/`, and the redirect drops `location=`, `f_WT=` (remote,
hybrid, on-site) and `sortBy=`. LinkedIn then picks a location itself, so the search still
returns jobs, just not from the place you configured. `ingest run` and `ingest test-source` log
a warning naming each such search by its label, on every run until it is replaced. The search
still runs: refusing it would leave a source whose every search is old-style reporting zero
jobs until it auto-retired.

To replace one, rebuild it in LinkedIn's own job search:

1. Search for the role, choose the location from LinkedIn's location box, and set a
   date-posted filter if you want one.
2. Read `keywords=` and `geoId=` from the address bar. `geoId` is a number standing for the
   place you chose; the place name does not need to appear in the URL.
3. Put them into a `/jobs/search-results/` URL under `sources.linkedin.searches`:

```yaml
sources:
  linkedin:
    searches:
      - ["Example role, city", "https://www.linkedin.com/jobs/search-results/?keywords=software%20developer&geoId=<id>&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r604800"]
```

`f_TPR` (date posted; `r604800` is the past week) survives only with
`origin=JOB_SEARCH_PAGE_JOB_FILTER` beside it. There is no URL filter for work type or sort
order any more: LinkedIn drops `f_WT` and `sortBy` even then. You do not need `start=`; sluice
sets it itself to read past the first page of 25 results.

## A backend is `setup`, `dead` or `degraded` in `doctor`'s output

- **`setup`, `<KEY_VAR> unset`**: a stage's `backend` (or `triage.resolve_backend`, when tier-3
  company resolution is on) has no key, so every stage listed beside it cannot run. Set the key
  (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`), or switch that stage to
  `claude-max`, which needs no key — it shells out to a local or SSH-reachable `claude` CLI.
  There is no fallback provider to cover it (#333).
- **`CLI '<path>' not on PATH`**: the `claude` binary isn't found locally. The STATE depends on
  whether you named the path, and so does the exit code. Left at the shipped default `claude`,
  it is `setup` (exit 0) — you have not installed it yet. If you set `claude_max_path` /
  `compose_claude_path` to a path of your own and it isn't there, that is `dead` (exit 1): you
  told sluice where the binary is and it isn't. Either install/alias it there or point
  `claude_max_host` at a machine where it is. Checked in both `--offline` and live runs, so the
  two modes agree.
- **`dead`, `unknown backend '<name>'`**: a typo'd `backend`/`resolve_backend`. Valid names are
  listed in the error.

A live (non-`--offline`) `doctor` round-trips one token per distinct backend to confirm it
actually answers, not just that a key is present.

## Store / vault problems

- **vault missing**: the vault directory doesn't exist. Blocks every pipeline command, and the
  STATE again turns on whether you named it. With nothing configured it is `setup` (exit 0) —
  you haven't run `job-sluice init --vault PATH` yet, which creates one. With `vault_dir` set in
  your config, or `$VAULT_DIR` exported, it is `dead` (exit 1): the vault you named has moved or
  been deleted — an unmounted drive, a renamed Obsidian folder, a Syncthing path change. Point
  the config at where it actually is, or bring the volume back.
- **`degraded`, Judging Profile absent**: `triage` falls back to the shipped neutral default,
  which states only that nothing is configured and prefers `research` over a confident
  verdict. Not fatal, just under-informed — fill in `Job Applications/Judging Profile.md`.
- **Experience Library / Skills Inventory / STAR Stories counts** (#164): one row per evidence
  corpus — `<verified> verified / <total> total entries`. Zero verified `experience` entries
  **blocks `cv`** (#242), as a `setup` row, while your CV Layout asks for bullets: `cv run`
  refuses such a vault, once for the run and before any fetch or backend call, so `doctor`
  grades it as the blocker it is rather than reporting it informationally. A layout whose every
  role has `bullets_max: 0` renders headings only and cites nothing, so `cv run` composes it
  with no entry, and the row is then a `notice` that blocks nothing. (It used to say the opposite — that this was "a `cv run`
  failure, not a `doctor` one" — which left `doctor` calling an install fine about the very
  thing that stopped the next command.) The other two stay `notice`: a verified `skills` entry
  reaches the composer as framing and its name can appear in a CV's SKILLS section, but it is
  citable by nothing, and a verified `stories` entry is consumed by nothing yet, so an empty
  one of either blocks nothing. A non-zero
  PENDING count also gets `; <pending> proposed and awaiting review (job-sluice <kind> verify)`
  — an entry `<kind> add` captured sits in `_inbox/` doing nothing until a human runs that
  command.
- **`cv_layout`**: `setup` when `Job Applications/CV Layout.md` does not exist yet, `dead`
  when it is malformed (each problem listed with its place) or cannot be read. Every state but
  `ok` blocks `cv`, in step with `cv run`, which refuses such a vault before any spend. The
  note's shape and rules are in the CV Layout section of `docs/CONFIGURATION.md`.
- **`dead`, `Experience Library (Tools)`**: a count of verified entries whose `Tools:` holds an
  item the gate cannot use, usually a word that starts with a digit. `cv run` names the entry
  and the item; `job-sluice experience list` shows every entry's `Tools:`.
- **`dead`, an evidence corpus that cannot be read**: `<Corpus> | dead | cannot be read — …`,
  and this one genuinely is `dead` rather than `setup` — the directory exists and the store
  cannot read it, which is a fault rather than an unfinished setup step, so it exits 1.
  one row per affected kind and only for that kind. The usual cause is a symlinked directory:
  the store refuses to read or write through one anywhere below the vault root, because
  promoting an entry from behind it would make content from outside your vault citable — and
  `verify`'s cleanup would then delete a file outside your vault. Move the real folder into the
  vault. Only the `experience` row names `blocks: cv`: an unreadable `skills` corpus is
  composed without (no framing, no skill names), and nothing reads `stories` yet. An interactive `job-sluice init` reports the same cause as a `FAILED` line, still writes
  your config and Judging Profile, and skips the capture step rather than offering it against a
  corpus it could not read; `--no-input` never reads the corpus at all.
- **A command refuses citing a relocated state file** (`seen.db`, `track-seen.db`,
  `sluice_health.json`, and friends): see "Upgrading from a pre-XDG install" in
  `docs/CONFIGURATION.md` — the fix is the printed `mv` command, not a config change. This is
  deliberately loud rather than silent: starting a dedup pass from an empty set can re-create
  a lead you'd merged away, or apply to the same job twice.

## `doctor` counts entries "not on your CV" or with "no company"

A verified experience entry is cited only under a role its `Company:` matches (see the CV
Layout section of `docs/CONFIGURATION.md`). Run `job-sluice experience list` to see each
entry's company, then either add that company to a role's `employers` (listing the heading
too, if other entries name it), list it under
`any_role:` or `omitted:`, or give the entry the `Company:` it happened at. When no role can
cite any verified entry at all, `cv run` refuses the whole run before any spend and says so,
and `doctor` reports it as a `cv_layout (no citable entry)` row under "Not working", blocking
`cv`.

## `doctor` counts verified skill notes with no `Label:`

A CV's SKILLS section shows a verified skill note under its `Label:`, else its title. Since
4.0, `skills add` keeps the name you typed in `Label:` because the note's filename is a slug;
a note it made before 4.0 has no `Label:`, so a CV would list it under the slug. `doctor`
counts only those: a note with no `Label:` whose title is slug-shaped (lowercase letters and
digits joined by hyphens), since a note you titled with its real name already reaches a CV
under that name. Run `job-sluice skills list` -- a slug title on a line ending
`Label: (none)` is one of them -- and add a `Label:` line to that note's frontmatter with the
name as a CV should show it. Nothing is broken meanwhile: the CV still composes.

## `doctor` says the cv attribution check is off

No verified experience entry declares `Tools:`, while some still carry the retired `Skills:`.
sluice no longer reads `Skills:`. Copy the named tools from each entry's `Skills:` into
`Tools:` (tools, technologies, languages, platforms, standards, named methods) and leave
practice words such as `security` or `coaching` out; see the next section for why. Once any
entry declares one, a bullet naming a tool must cite an entry that lists it. `cv run` logs the
same sentence once per run, and each result line says `attribution_check_off=True`.

## Many leads skipped with `MISATTRIBUTED TOOL` on an ordinary word

The `MISATTRIBUTED TOOL` lines under the `skipped-gate` rows quote words like `security`,
`pairing` or `architecture` rather than a product name. Some verified entry declares that word
in `Tools:`, usually because it was copied from the retired `Skills:` on upgrade. Every declared
item is matched as a whole term, case-sensitively as declared, in every WORK bullet, so any
bullet using the word must cite an entry that declares it or names it in its own title or body.
A hyphenated compound still counts, because a hyphen is not part of a word: `security-focused`
matches a declared `security`. Ordinary prose rarely cites such an entry, and when the one retry
draws the same finding the lead is skipped.

Run `job-sluice experience list` to see each entry's `Tools:`, and keep only named tools,
technologies, languages, platforms, standards and named methods (`Terraform`, `React`, `WCAG`,
`Scrum`). Move a practice you want shown under SKILLS into a Skills Inventory note
(`job-sluice skills add`); its `Label:` can appear in SKILLS and is not checked in bullets.
`docs/CONFIGURATION.md` has the rule under "`Tools:` on experience entries".

## `track` reauth needed

`track run` exits 1 with `track: google reauth needed (token refresh failed)` when the stored
OAuth token is genuinely dead — Google REFUSED the refresh, or the file is present but
unparseable. Mint a replacement:

```bash
job-sluice track auth --client-secrets <your client_secret.json> --force
```

`--force` is required because the dead token is still sitting at `track.token_path` (see
`docs/CONFIGURATION.md`; default `<XDG_STATE_HOME>/sluice/google_token.json`), and it archives
that file beside itself rather than discarding it. The client secrets JSON is the one you
downloaded from the Google Cloud console — [`docs/INSTALL.md`](INSTALL.md#google-access-for-track)
has where it comes from and the scopes. Needs the `google` extra. `track run` itself still prompts
for nothing: it only reads and refreshes an existing credential, and `track auth` is the only
command that runs a consent.

**If this comes back roughly every week, the credential is not dying — it is expiring on a
schedule.** An *External* consent screen left in *Testing* issues refresh tokens that expire after
seven days, so re-authorising buys another seven and no more. Do the step titled *Set the consent
screen's publishing status to In Production* in
[INSTALL's Google section](INSTALL.md#google-access-for-track) before minting the next one; the
seven-day cap is a property of that status, not of the token.

**A network problem does not produce this.** A dropped connection, a DNS failure, a Google
5xx or a disk-full error while writing the refreshed token are reported as ordinary run
failures — named in the digest, recorded in the dead-letter store, and retried — precisely so
that deleting a perfectly good credential is never the remedy for a Wi-Fi blip (#142). If you
see a transport error rather than this message, re-run rather than re-authorising.

**A leftover credential at the pre-XDG `./google_token.json` is reported only under
`--verbose`.** `job-sluice doctor --verbose` names it once `track.token_path` itself holds a good
token — it changes nothing and blocks nothing, so it is not in the default view; delete the old
file once you are sure nothing else still reads it.

## Shell completion isn't offering anything

`job-sluice[completion]` (argcomplete) needs its shell hook activated — see the README's Shell
completion section. Two independent things must both be true: `job-sluice` itself on `$PATH`,
and `register-python-argcomplete` on `$PATH` (only present once the `completion` extra is
actually installed, separately from the base package). The plugin wrapper
(`plugins/job-sluice/job-sluice.plugin.zsh`) checks both and stays silent — no error — if
either is missing, which is the expected shape for a shell plugin whose prerequisite isn't met
yet, but it also means a missing prerequisite gives no visible signal that something needs
installing.
