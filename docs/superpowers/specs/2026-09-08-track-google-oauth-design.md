# `job-sluice track auth`: a supported way to obtain the Google OAuth token (#201)

Status: design, agreed 2026-09-08. Revised after `/review-plan` (five reviewers, 30 findings).

## The problem

`track` is a shipped sub-app that cannot be reached. `GoogleClient._creds`
(`sluice/track/google_client.py`) calls `Credentials.from_authorized_user_file` and refreshes what
it finds; nothing in the tree mints a credential, and `google-auth-oauthlib` -- the package
supplying `InstalledAppFlow` -- is in no dependency list. `job-sluice doctor` reports
`track google_token.json SETUP`.

A token-less `track run` does not prompt, and the mechanism matters because the harm is worse than
"it does nothing". `_creds` raises `FileNotFoundError` out of the FIRST Google call,
`client.search_messages`. That call sits OUTSIDE the per-message loop, so `track/engine.py`'s own
search-level `except Exception` arm catches it -- whose comment says so in as many words. That arm
appends a `TrackFailure` carrying `message_id="(gmail search)"` to `rep.failures` and sets
`rep.deadletter_error = True`. It writes **no dead-letter row**; the `message_id` is a field on an
in-memory failure record, not a store write.

`deadletter_error` is what makes this bad rather than merely useless: it HOLDS the lastrun
watermark. Nothing was read, so advancing would skip whatever arrived -- correct in itself, but it
means an install that has never had a token widens its Gmail query window without bound on every
run, for ever, while `cmd_track_run` exits 0.

`docs/INSTALL.md` documents the manual procedure honestly -- bring a Desktop-app OAuth client, pip
`google-auth-oauthlib` into a throwaway virtualenv, run a hand-written `InstalledAppFlow` script,
move the resulting JSON to `track.token_path`. This design replaces the second half of that with a
command, and fixes a defect in the first half.

## Two facts about the world, and how far each is established

Claims about Google's policy rather than about this codebase. The code wins on any disagreement
about the code; on these, Google does.

**A sluice-owned OAuth client is not viable, at any effort level.** `gmail.readonly` is a
RESTRICTED scope. An app that requests one and is published for general use needs Google
verification AND an annual third-party security assessment (CASA), which for restricted scopes is
the penetration-test tier -- repeated every twelve months to retain access. So "whose OAuth
client?", listed as an open question on #201, is not a decision anyone here gets to make. The user
brings their own, which is what INSTALL.md already says.

**A consent screen left in `Testing` issues refresh tokens that expire after 7 days.** Google's
documented behaviour for an external-user-type app in Testing publishing status, unless the scopes
are a subset of name/email/profile. `Testing` is the default for a newly created screen, and
INSTALL.md today says nothing about publishing status -- so a user who follows our own instructions
gets a credential that dies within the week. `_creds` classifies the resulting `invalid_grant` as
`GoogleAuthError`, `cmd_track_run` exits 1, and `docs/TROUBLESHOOTING.md` tells them to delete the
token and produce another one. Weekly, for ever, with nothing anywhere naming the one-time console
setting that fixes it. The remedy is to publish the screen to `In Production`; the app stays
unverified, so consent shows an interstitial the owner clicks through once, and a user cap applies
to how many accounts may grant an unverified app -- irrelevant to a single user. Deliberately no
NUMBER for that cap: Google states it exists without publishing the figure.

What an `In Production`, UNVERIFIED, restricted-scope app does over the long run is **treated here
as unestablished.** Google's documentation is explicit about the Testing cap and silent about this
case, and no reproducible source settles it. The shipped prose therefore states the Testing cap and
names the remedy, and claims nothing beyond what a user will see on the screen. That is the whole
of what this design rests on, and it is enough: the Testing cap alone is what makes the current
instructions defective.

## What ships

    job-sluice track auth --client-secrets PATH [--port N] [--no-browser] [--force]

`cmd_track_auth(args, config)` calls `Sluice(config).track_auth(...)`, which returns a dict the CLI
renders into one line and an exit code -- the `cmd_track_confirm`/`cmd_track_dismiss` shape.

Two things "wired like its siblings" must NOT be read as importing:

- **`load_track_config()` is called WITHOUT `refuse_relocated_seen_db=True`.** Every existing track
  entry point passes it, and that is right for them: they read or write the dedup and dead-letter
  stores, and a relocated `track-seen.db` would make a run report nothing to do. `track auth`
  touches neither store. Passing it would let a moved dedup database refuse to mint a credential it
  has nothing to do with -- and the population that hits this command is precisely the population
  whose `track` has never successfully run.
- **The facade carries the test seam.** `Sluice.track_auth(..., flow_factory=None)`, mirroring
  `Sluice.track(..., client=None)` exactly. Injecting only into `run_consent_flow` would leave the
  path the CLI actually takes constructing the real `InstalledAppFlow`, so the flag-to-parameter
  mapping this design most wants tested would be the one join no test could reach. `Sluice.track`'s
  own docstring settles the seam question verbatim: one shape, no config selecting among providers,
  so this is a test seam and not a `plugins.get` registry. No fifth seam is created.

The token is written to `tcfg.token_path`, the same value `Sluice.track` passes to
`RealGoogleClient`.

### `--client-secrets` is an argument, not a config key

The client secrets file is read exactly once: after consent, `client_id` and `client_secret` are
embedded in the authorized-user JSON, and nothing reads that file again. A
`track.client_secrets_path` key would assert a path consulted on every run, which is false.

Note what this reasoning does NOT rest on: a config key does not imply `core/paths.py`.
`cv.template` and `cv.render_script` are config keys read straight from YAML that never go through
`resolve()`. `resolve` is for RELOCATABLE per-system state with an XDG fallback, which a
user-supplied input file is not. The argument is about lifetime, not about the path module.

### The flow lives in a new `sluice/track/auth.py`

The load-bearing requirement is that the two PROBES stay separate FUNCTIONS -- and that is true
whichever file they live in. `classify_track_google` must not begin depending on
`google-auth-oauthlib`, because `pip install -U job-sluice` does not re-resolve extras: every
existing `[google]` install has `google-api-python-client` and `google-auth` and NOT the new
package, while `track run` keeps working perfectly. Fusing the probes would report SETUP across
that entire population on upgrade.

That argues against fusing the probes. It does NOT by itself argue for a second file, and an earlier
draft of this document claimed it did, on two counts that are false against the code:
`classify_track_google` imports nothing -- it is a pure classifier taking `available` and
`import_error` as parameters -- and every google import in `google_client.py` is already inside a
function body, so putting the flow there could not have made the existing probe require anything.

The file boundary is therefore a COHESION choice, stated as one: `google_client.py` is about USING a
credential on every `track run`, and minting one is a different lifecycle invoked once. That is a
judgement, not a forced constraint, and it is the reviewers' recommendation.

It has a cost that must be paid in the same change. `probe_availability`'s docstring says
`google_client.py` is "the ONE sanctioned site" for the google imports, and warns that a second copy
would silently drift. That sentence becomes false the day `auth.py` ships, so it is a documentation
site of this work, listed below. `auth.py` carries its own `probe_flow_available()`, mirroring
`probe_availability`'s `(ImportError, OSError)` reasoning.

`sluice/` is stdlib-only except for named exceptions, and that list is module-keyed, so
`.rulesync/rules/CLAUDE.md` gains this module by name. Part of the work, not a follow-up.

The entry must state the PROPERTY it is keyed on, not just the module: the `google_auth_oauthlib`
import is **function-local**, exactly like the google imports in `google_client.py`. At module scope
`probe_flow_available` becomes unreachable on an install lacking the package -- the import fails
before the function that exists to report it politely can run -- so the carefully-worded remedy is
replaced by a raw traceback. Tests would not notice: the `sys.modules` stubbing convention keeps them
green either way. A rule that says "this module may import google_auth_oauthlib" without saying WHERE
licenses the broken shape.

### Token writing: one writer, and the refusal moves into it

`google_client._write_token` is promoted to `write_token` -- it acquires a second caller -- and
gains one parameter. `auth.py` calls it rather than growing a sibling: a second write function is a
new CodeQL sink with 0600 creation, the 0700 parent, atomic `os.replace` and `BaseException` temp
cleanup all to re-argue.

**`write_token(..., exclusive=True)` is the refusal, and it must be enforced at the write.** An
earlier draft claimed that checking `os.path.exists` before starting the flow made refuse-by-default
"the behaviour being replaced, not a new restriction". That is false, and the reviewers were right
to call it borrowed language. INSTALL.md's script refuses with `O_EXCL`, in the kernel, at the
moment of the write. A pre-flight `exists()` at t0 followed by an unconditional `os.replace` at t2 --
with a full interactive consent round-trip in between, which under `--no-browser` can block for
minutes with no progress -- is the stale-snapshot shape this repo already knows is byte-identical to
no guard. It is the reason `require_status` could not be hoisted into the caller. Two concurrent
`track auth` runs both clear t0 and the last silently wins, possibly for a different Google account.

**`exclusive=True` uses `O_CREAT|O_EXCL`, NOT `os.link`.** An intermediate draft of this document
reached for `os.link(tmp, path)`, and that is a move this repo has already considered and rejected:
`core/vault.py::_reserve_and_move`'s docstring records `os.link(src, dest) + os.unlink(src)` as
turned down on #23, and names `O_CREAT|O_EXCL` as the shape that satisfies both requirements.
`verify_evidence` models the correct way to revisit such a decision -- cite the rejection, then state
why your harm differs. Making the same move silently is what this section did.

It is also wrong on its own terms, measured rather than argued. `_write_token`'s only
`os.unlink(tmp)` sits inside its `except BaseException` arm, so `os.link` -- which creates a second
name for the same inode rather than consuming the source the way `os.replace` does -- leaves the temp
file behind on every SUCCESSFUL mint: one 0600 copy of the refresh token per run, accumulating in a
directory `core/paths.py` measures as commonly 0755. The existing
`test_an_interrupted_write_leaves_no_stray_temp` cannot see it, because it patches `os.replace` to
raise and therefore only ever exercises the failure arm.

**The shape is `_reserve_and_move`'s, and NOT `_write(exclusive=True)`.** A third draft of this
section cited `core/vault.py::_atomic_write` as "already shipping exactly this under exactly this
parameter name". That was false on both counts, and the way it went wrong is worth recording: the
parameter belongs to `_write(path, text, *, exclusive=False)`, a different function, and the citation
came from reading a docstring next to a grep hit without checking which `def` owned it -- the exact
failure this repo bans line-number citations to prevent.

It is not a naming slip. The two functions have complementary halves and neither has both:

- `_write(exclusive=True)` opens with mode `"x"`, which gives O_CREAT|O_EXCL but **cannot set a
  creation mode**. Measured under umask 022 it yields **0644** -- a world-readable credential
  carrying `gmail.readonly` and read-write `calendar.events`, which is the precise defect
  `_write_token` exists to remove.
- `_atomic_write` gets 0600 from `mkstemp` and does the atomic `os.replace`, but has no exclusivity
  at all.

`_reserve_and_move`'s shape has all three, and is already cited two paragraphs above -- for the
rejection rather than as the answer. Measured end to end: write the payload to a `mkstemp` temp,
reserve the destination with `os.open(dest, O_CREAT|O_EXCL|O_WRONLY, 0o600)`, then
`os.replace(tmp, dest)`. Result: mode 0600, no temp surviving success, `FileExistsError` on a second
mint, and no temp surviving that refusal either.

Because exclusivity is now a MODE-bearing path distinct from the default one, every mode assertion
must be qualified to `exclusive=True`. The existing rows all drive the no-flag path, so an unqualified
new row passes on the default path while the mint path ships 0644.

The pre-flight `exists()` check STAYS, labelled in the code as what it is: a courtesy that avoids
spending a consent round-trip about to be refused. It is not the guard. The guard is at the write.

**`--force` archives before it replaces, and must set the archive's mode explicitly.** The previous
draft named the harm -- "the original unrecoverable" -- and then delivered it. `--force` is also the
remedy TROUBLESHOOTING.md points people at, so it is the well-trodden path, not the exotic one.

Before replacing, the existing token is moved to a sibling named
`google_token.json.replaced-<UTC>` -- `merge_cluster`'s reversible-archive shape applied to a
human-named destruction. **`os.replace` is a rename and does not chmod**, so the archive inherits the
source inode's mode; measured, archiving a 0644 token yields a 0644 archive. That is precisely the
population `--force` serves, since `_write_token`'s own docstring describes tokens an older sluice
left at 0644, so the mode is set explicitly on the archive rather than inherited. The test must start
from a **0644** fixture: starting from 0600, as an earlier draft's bullet did, passes whether or not
the code does anything.

The archive name must be collision-safe. A bare `os.replace` to `.replaced-<UTC>` silently
overwrites an archive from the same second, which is reachable on a fast retry -- and destroying one
recovery artefact with another is the harm this whole paragraph exists to prevent. `merge_cluster`'s
`suffix_on_collision` shape is the precedent, and an earlier draft claimed to follow it while
specifying a plain rename.

**The archive-then-write sequence has a window of its own, and saying so is part of the design.**
Archive succeeds, the exclusive write then fails -- ENOSPC, EACCES, a concurrent racer, a SIGINT
between the two -- and the user has NO token at `track.token_path` and a working credential at a
sibling they have never heard of. `track run` then raises `FileNotFoundError` out of
`search_messages`, the search-level arm holds the watermark and exits 0: exactly the state this
document's opening section cites as its reason to exist, now reachable through the recovery path.

Two requirements follow, and neither is optional. The failure must NAME the archive path in the error
the user sees -- "the archive is what makes that recoverable" is worth nothing if nothing tells them
where it is. And the failure arm needs its own assertion: every `--force` row listed below is a
success-path row, so this window is currently guarded by nothing at all.

Why this matters more than it looks: consent completes against whichever Google account happens to be
signed in. A wrong-account token is SILENT -- `classify_track_google` returns OK on file presence
alone, and `track run` reports `msgs=0` and exits 0 -- and `calendar.events` is read-WRITE, so sluice
would insert and delete events on a stranger's primary calendar. The archive is what makes that
recoverable.

Existing test call sites import the private name (`tests/test_track_google_client.py`, including a
guard called `test_the_refresh_path_writes_through_write_token`); the rename updates them. One
further site is PROSE rather than a call: a docstring in `tests/test_state_file_tiers.py` names
`_write_token` in bare backticks, which `tests/test_citation_drift.py` does not validate because it
checks the `file.py::symbol` form. Nothing will fail if it is missed, which is exactly why it is
listed here.

### Three correctness details, asserted rather than assumed

**`prompt=consent` and `access_type=offline` are passed explicitly.** A re-authorisation by a user
who has already granted can return a credential with no `refresh_token`, which `_creds` requires.
Both are passed rather than relying on `run_local_server` defaults.

**The minted credential is verified BEFORE anything is written.** It must carry a `refresh_token`,
and it must carry both requested scopes. Google's granular consent lets a user deselect a scope on
the screen; a credential holding only `gmail.readonly` parses fine, doctor says OK, and every
calendar call then fails as an ordinary per-message failure with `track run` exiting 0 -- so an
interview is silently never booked.

**The attribute is `granted_scopes`, not `scopes`, and that distinction is the whole check.**
Measured against the real package: `google_auth_oauthlib.helpers.credentials_from_session` constructs
the credential with `scopes=session.scope` -- the list we REQUESTED -- and separately with
`granted_scopes=session.token.get("scope")`, read from the token response. So comparing
`credentials.scopes` against the requested set compares a set with itself and passes in exactly the
granular-consent case this check exists to catch. An earlier draft said "assert the scopes" without
naming the attribute, which would have been written the obvious, useless way.

The stub used in tests must therefore model both attributes distinctly, or the assertion is
untestable by construction.

**An absent or `None` `granted_scopes` is a REFUSAL, not a fallback.** The natural spelling --
`getattr(creds, "granted_scopes", None) or creds.scopes` -- silently restores the vacuous compare
this check exists to remove, and a stub whose two attributes merely differ cannot catch it, because
it never exercises the empty case. sluice cannot tell "the server did not report a grant" from "the
grant was complete", and the whole point here is that guessing wrong is silent for the user, so the
unknown case fails closed and says which attribute was missing.

**Verification precedes the write, not follows it.** Reading the token back through
`Credentials.from_authorized_user_file` after writing would leave an unusable credential on disk when
it fails -- and under `--force`, the working one it replaced is already gone. Since
`classify_track_google` returns OK on presence alone, that unusable token then reads as
`track google OK` for ever. So the credential is validated in memory first, and only a credential
that passes is written.

### Headless and containers

`--port N` binds a FIXED port so it can be forwarded; `--no-browser` prints the URL instead of
launching one. The default stays an ephemeral port with a browser, right on a laptop and needing no
flags. The headless line documents an SSH local forward using `example.invalid` as the host -- the
placeholder this repo already uses in README and TROUBLESHOOTING, named here as a value rather than
left as the word "placeholder" for an implementer to choose. That matters because nothing would catch
a real one: `tests/test_no_leaked_files.py` sweeps every tracked file but only for absolute home
paths, and the fixture-neutrality sweeps read `tests/` plus a narrow README carve-out. The PATH in
that line is gated; the HOST is not. The same two flags cover the Docker image via `-p`.

A fixed port costs the user no extra console setup, and the evidence is our own shipped artefact
rather than a reading of Google's docs: INSTALL.md's current script calls
`run_local_server(port=0)`, which binds a different ephemeral port on every run and works -- so the
loopback redirect cannot be matched on its port, or that script could never have functioned.

There is no no-browser fallback to reach for: Google removed the copy-paste OOB flow in 2022.

### The relocation notice this must not disarm

`google_token.json` is in `core/paths.py`'s `_LEGACY` table, pointing at `./google_token.json`, and
that notice is keyed on the RESOLVED path not existing -- `paths.py`'s own docstring warns that a
writer which creates it silently disarms the notice from then on. A user who followed the pre-XDG
instructions has a live credential in their working directory. `track auth` writing a fresh token at
the resolved path would silence, permanently, the only thing that names the orphaned one.

`load_track_config` ALREADY runs that probe, via `resolve`'s own `_LEGACY` lookup, so what
`track auth` adds is not the detection -- it is the silencing. A single warning line printed
mid-consent-flow is therefore weaker than an earlier draft implied: it scrolls past while the user is
in a browser, and it fires once, at the exact moment the notice is being destroyed for good.

The durable home for it is `doctor`, as a NOTICE row: `doctor` is the command whose whole job is to
report a relocated file, it is the one place CLAUDE.md says never refuses on relocation, and a row
there survives the run that disarmed the original. `track auth` still reports what it found before
minting -- that is the last honest moment to say it -- but the row in `doctor` is what makes the
orphaned credential findable afterwards.

## Dependency

`google-auth-oauthlib` joins the `google` extra, floored at the major boundary (`>=1,<2`) per
`pyproject.toml`'s own reasoning -- the ceiling is what Dependabot tracks, and a floor younger than a
day is invisible to `brew update-python-resources` and fails the `homebrew` release job.

**Two extras probes must gain the module in this change**, and neither is optional. Both hand-list
one top-level import per baked extra, and both degrade silently when they skew:

- `scripts/render_homebrew_formula.py`'s `test do` line, currently
  `import mcp, googleapiclient, argcomplete`. Pinned by `tests/test_homebrew_formula.py`, whose own
  docstring records that a skew here surfaces only as an ImportError the first time a user runs a
  Google-tracker command -- and this exact line failed a past release.
- `.github/workflows/ci.yml`'s docker smoke-import, currently
  `import weasyprint, jinja2, googleapiclient, mcp, argcomplete`. Pinned by `tests/test_ci_wiring.py`.

The import name is `google_auth_oauthlib`, not the distribution name.

That has a consequence for `probe_flow_available`'s remedy text. "Install the `google` extra" is a
WRONG remedy on Homebrew and Docker, where the extra is already installed and the package would be
missing only through this skew. The message names the missing import and the extra, and must not
instruct an action the reader has already taken.

Cost to a user, stated for the channel that pays it: the tap publishes no bottles, so every
`brew install` builds from source.

What is verifiable from here, measured by resolving the package in a throwaway environment:
`google-auth-oauthlib` requires `requests-oauthlib`, which requires `oauthlib` and `requests`. Those
three added distributions are pure Python, so they compile nothing. `cryptography` and `cffi` appear
in that resolution and are NOT pure Python -- but they are required by `google-auth` itself,
unconditionally, and `google-auth` is already in this extra. They are a pre-existing property of
`[google]`, not a cost this change introduces.

What is NOT verifiable from here is the resulting Homebrew resource DELTA. The formula is rendered
into a separate tap repository and its closure is regenerated by `brew update-python-resources` in
the release job, so the honest place to read the delta is that job's diff. Deliberately no list of
what the closure already contains and no count of it: an intermediate draft asserted both, and a
membership claim about a file this tree does not hold is the same unverifiable shape this document
refuses one section earlier when declining to state Google's user cap.

The rejected alternative was a stdlib-only loopback flow using `urllib`, `http.server` and
`webbrowser`, which `tests/test_no_false_consent_flow_claim.py` explicitly anticipates, having added
a Google-endpoint-host check so such a flow could not slip past it. Rejected because the code it
avoids writing is the security-sensitive part: PKCE, state validation and redirect handling, correct
by construction in the library and by review in ours.

## Documentation

Five prose sites, not four. The first four are the roster
`tests/test_no_false_consent_flow_claim.py` names; the fifth falls out of the module decision above.

- **`docs/INSTALL.md`, "Google access for `track`"** -- keeps the Cloud project, enabled APIs and
  Desktop client. Loses the throwaway virtualenv and `get_token.py`, which become one `track auth`
  invocation. Gains the publishing-status step and the SSH/container line. The heading keeps its
  current SLUG, because `tests/test_doc_links_from_code.py` resolves doctor's URL against it. Not
  "byte-identical": that test compares GitHub slugs via its own case-folding, punctuation-dropping
  `_slug`, so it pins slug-equality and an earlier draft overstated the guard.
- **`README.md`** -- the requirements row stops describing a token "which you mint yourself", and the
  Commands table gains `auth` under `track`. NOT the `## Quickstart`:
  `tests/test_readme_quickstart.py` executes every command there and requires each to be offline.
- **`docs/TROUBLESHOOTING.md`** -- the reauth remedy becomes `track auth --force`, and says what the
  archive leaves behind.
- **`sluice/core/doctor.py`** -- `classify_track_google`'s SETUP message names the command. Its
  DOCSTRING is a second edit in the same function: it says `sluice/` is stdlib-only "except for the
  three named, deliberate exceptions", which is already wrong before this change and wronger after.
  Replace the count rather than increment it.
- **`sluice/track/google_client.py`** -- `probe_availability`'s "ONE sanctioned site" docstring, per
  the module section above.

Two more sites that are not prose about the flow but go stale with it:

- **`docs/ARCHITECTURE.md`** -- CLAUDE.md calls it the living module-by-module description; it
  describes the track sub-app with neither `auth.py` nor the new command. TWO places, not one: the
  sub-app description, and its enumerated injected-collaborators roster -- which carries the very
  rule that licenses the `flow_factory` decision above, so the decision and the document stating it
  go stale together. Nothing guards this; the existing sweep binds `Sluice.__init__` keywords only,
  and `flow_factory` is a parameter on a METHOD.
- **`tests/test_track_engine.py`** -- a test docstring asserts sluice "has no consent flow at all"
  and cross-references the guard file. The claim goes false; the reference needs repointing.

`docs/USAGE.md` gains the command and its flags. Note precisely what the existing suite does and does
not do here, because an earlier draft got it backwards in one sentence and right in another:
`test_every_real_command_is_documented_in_usage_md` compares COMMANDS and never looks at flags -- its
sibling's docstring says so outright.

One existing test goes red BY DESIGN and needs a hand edit:
`tests/test_docs_claims.py::test_the_command_tree_walk_is_not_vacuous` pins an exact literal
subcommand total for the non-evidence groups. `track auth` moves it, and its docstring says the edit
IS the review step.

## The guard: narrowed and inverted, not deleted

The previous draft deleted `tests/test_no_false_consent_flow_claim.py` in full, reasoning that
assertion 2 is licensed by assertion 1 and that the rest is machinery serving both. Measured function
by function at review time, that is false in its load-bearing half: **a minority of its tests die
with assertion 1, and the majority survive a narrowing.**

**Do not specify this file by a sorting criterion.** Three drafts tried -- delete everything; keep
what serves assertion 2; keep what does not reference the pattern -- and a reviewer falsified each in
turn. The last one fails because every test that references `_CONSENT_CLAIM` also serves assertion 2,
so the criterion says "survives" while abandoning the pattern rewrites or removes each of them;
applied literally it either raises `NameError` at collection or leaves the pattern armed against the
honest new prose, which the file's own docstring calls the direction that gets a guard deleted.

The file is small. Specify the REPLACEMENT's contents instead, and let the implementer write it
fresh rather than sort the old one.

The distinction the old reasoning missed is between two different claims:

- *"sluice has a consent flow"* -- becomes TRUE. Assertion 1 and its machinery go.
- *"`track run` prompts you through consent"* -- stays FALSE. `track auth` is a separate command in
  a separate module; `_creds` and `engine.run` are untouched by this design.

**Attribution is NOT mechanically expressible as a prose pattern, and a second draft of this section
was wrong to claim it was.** Four reviewers measured it independently. The evidence:

- Every one of the file's `corrected` sentences passes `_CONSENT_CLAIM` today, because none contains
  the word `interactive`. They cannot be a "must keep passing" roster: they cannot distinguish a
  narrowed pattern from an unnarrowed one, and worse, they are DENIALS near-verbatim the live prose
  this very change deletes. Nominating them was backwards.
- One of the sentences that actually shipped attributes consent to bare `track`, not `track run`.
  Keyed on `track run`, a check drops it silently. Keyed on bare `track`, it fails every honest new
  sentence, since they all carry that token. No proximity or negation window separates them -- the
  file's own docstring already records that measurement being tried and rejected.
- A third shipped sentence is `classify_track_google`'s live SETUP string, which this change
  hand-edits. An attribution check cannot catch the inverse claim a partial doc edit leaves printing
  on every fresh install.

So the guard becomes a RATCHET, which is what this repo does when nothing local can classify --
`_REVIEWED_FIXTURE_IDENTITIES` is the same shape. Two things are checkable and both are checked:

1. **The sentences that actually shipped false must not reappear.** They are already in the file, in
   `shipped_and_false`. Compared as normalised exact strings, not as a pattern. A ratchet over known
   values cannot be defeated by phrasing because it makes no claim about phrasing.
2. **`classify_track_google`'s SETUP string must name a command the real parser accepts.** That is
   the one runtime string the original incident was measured on -- and the guard's own docstring
   scopes that packaged-install measurement to this string alone, which an earlier draft of this
   section over-attributed to two sentences.

The replacement file contains exactly these, and nothing that references `_CONSENT_CLAIM`, because
the pattern goes:

- **The ratchet comparator**, over `shipped_and_false` (which stays, as data) against every shipped
  prose file and `sluice/` string constant.
- **`_searchable`/`_identifiers`**, unchanged. They resolve Python implicit concatenation through
  `ast`, so a re-wrapped literal cannot hide a sentence from the comparator -- the hazard the
  original file was built around, and still live under exact-string comparison for the CODE half.
- **Whitespace normalisation on BOTH sides.** The code half arrives pre-joined by `ast`; the prose
  half arrives as raw markdown, and two of the three sentences shipped in README and TROUBLESHOOTING,
  where a reflow moves the line breaks. Comparing raw would silently stop matching. "Normalised" must
  mean collapse-whitespace on the haystack as well as the needle, and an earlier draft did not say so.
- **An anti-vacuity partner.** A planted copy of each `shipped_and_false` sentence must be CAUGHT, in
  both a markdown fixture and a re-wrapped Python literal. Without it a broken comparator finds
  nothing and reads as success -- this repo's signature failure, and the reason the scope assertion
  below is not sufficient on its own.
- **`test_the_sweep_reads_the_files_it_means_to`**, kept: it asserts the SCOPE before any verdict is
  read off the sweep. It asserts what is READ, not what is FOUND, which is why it needs the partner
  above beside it rather than instead of it.
- **The doctor check**: `classify_track_google`'s SETUP string names a command the real parser
  accepts.

**The residual, stated rather than disguised:** a NEW false attribution, worded differently from the
three that shipped, is not caught by this. That is a real loss against the old guard's ambition and
not against its actual reach, since the old pattern could not have distinguished attribution either.
The measurement above belongs in the replacement's docstring, so the next person to reach for a
cleverer pattern finds out why it does not work before spending the afternoon.

The FLAGS remain unguarded by anything, and that gap is real: nothing runs a command in INSTALL.md
against the thing serving it, so a renamed or dropped flag ships green. The closing test takes
`test_every_evidence_add_flag_is_documented`'s generic `_parser_flags`/`_documented_flags` helpers,
with two corrections its model does not need and this does:

- **Bidirectional.** The model diffs `real - documented` only, which catches a flag added to the
  parser but not a flag DROPPED from it while the doc still instructs it. Both directions.
- **A second extractor for the docs that carry the invocation.** `_documented_flags` matches only a
  USAGE.md `### job-sluice <group> <sub>` heading and returns the empty set for any other file, so it
  CANNOT read INSTALL.md -- an earlier draft claimed it generalises and it does not. The INSTALL half
  therefore needs its own extractor: pull the flags out of every `job-sluice track auth ...`
  invocation in the prose docs and assert each is one the real parser accepts. That is a different
  question from USAGE's (is every real flag documented?) and needs different code, not a wider glob.

## Testing

Hermetic and offline. `run_consent_flow` takes an injected `flow_factory` and the facade carries it,
so the shipped path is the tested path; the real `InstalledAppFlow` is never driven by the suite.

**Two seams the previous draft omitted, without which most of the list below cannot be written:**

- `google-auth` is NOT in the `[test]` extra -- `import google` fails in this environment. Every
  success-path assertion traverses that import, so tests use the existing `sys.modules` stubbing
  convention already established in `tests/test_track_google_client.py`.
- Mode assertions need the existing `pinned_umask` fixture. A default umask yields 0600 anyway, so
  without it those assertions are a false green -- that same file records the measurement. Note it is
  FILE-LOCAL to `tests/test_track_google_client.py`, not in `tests/conftest.py`, so a new test module
  either lives beside it or the fixture moves; it is not available by being in the suite.

Assertions:

- The refusal is enforced AT THE WRITE: with the destination present, `write_token(exclusive=True)`
  raises and no bytes are replaced. Separately, the pre-flight courtesy check fires without
  constructing the flow -- asserting the factory was never called, which is what makes the fail-fast
  ordering falsifiable rather than incidental.
- `--force` archives the previous token AND the archive's contents are the OLD credential --
  asserting only that the file exists would pass for an empty one. **The fixture starts at 0644 and
  the archive is asserted 0600.** Measured: `os.replace` preserves the source mode, so a 0600 fixture
  makes this bullet pass whether or not the code sets the mode at all.
- No temp file survives a SUCCESSFUL mint. This is the assertion that would have caught the `os.link`
  shape, and no existing test covers it: `test_an_interrupted_write_leaves_no_stray_temp` patches
  `os.replace` to raise, so it only ever walks the failure arm.
- The written token is 0600 under a 0700 parent, with `pinned_umask`. Stated as what it is -- a
  permissions assertion. It does NOT prove no sibling writer grew, which an earlier draft claimed; a
  sibling that also chmods would pass it.
- A credential with no `refresh_token` is refused and NOTHING is written.
- A credential whose `granted_scopes` omits a requested scope is refused and NOTHING is written --
  driven by a stub whose `scopes` and `granted_scopes` DIFFER, since a stub that conflates them makes
  this assertion pass vacuously.
- The read-back rejects a credential the reader cannot parse, before any write. Only the rejection
  arm is falsifiable under a stubbed reader, and that is stated rather than disguised.
- `prompt` and `access_type` reach the flow rather than being left to defaults.
- `--no-browser` maps to `open_browser=False`; `--port N` to a fixed port; the default to an
  ephemeral one with a browser. Driven through the facade, not just the module.
- A missing `google-auth-oauthlib` produces a message that names the import and does not instruct an
  already-installed extra.
- An absent or unreadable client secrets file fails before the flow is built.
- A legacy `./google_token.json` is reported rather than silently superseded.
- `SCOPES` matches INSTALL.md's scope table, derived on both sides.
- **The upgrade regression needs BOTH halves, and the second is the one that matters.** Not "an
  install without the package" -- that install cannot exist in CI, and describing one is a wish
  rather than a test.
  - *Half one:* monkeypatch `probe_availability` and confirm `probe_flow_available` is never
    consulted. This catches a fusion at the CALLER.
  - *Half two:* the likelier fusion is a one-line edit INSIDE `probe_availability`'s own try block,
    which half one is structurally blind to -- it has monkeypatched away the very function that would
    break. So drive the REAL `probe_availability` with the three google modules stubbed and
    `sys.modules["google_auth_oauthlib"]` set to `None`, and assert it still returns available.
    Measured to work today, so the test is writable now rather than aspirational.

Mutation discipline per the standing cadence: `compileall --invalidation-mode checked-hash` first,
mutate by moving or deleting rather than adding, run each new guard by node id and confirm no
pre-existing sibling already catches the mutant.

## Out of scope

- Any second promotion path for credentials. `track auth` mints the track token and nothing else.
- A device-authorization-grant flow. Google documents it for TV and limited-input DEVICE applications
  over a restricted scope set, and the loopback flow with a forwardable port already covers the
  headless case -- so whether it would serve `gmail.readonly` was never worth establishing. The
  honest reason is that the alternative is unnecessary, not that it was measured and found wanting.
- Verifying the app. See the first section.
- Making `track run` mint a credential. It does not, and the narrowed guard above exists to keep
  anyone from writing that it does.
