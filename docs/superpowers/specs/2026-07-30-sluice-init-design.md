# `sluice init` — a setup wizard that scaffolds a config and a Judging Profile (#8)

**Status:** design approved 2026-07-30. **Revised after `/review-plan` round 1** — 5 reviewers,
**50 findings (1 Critical, 17 High, 21 Medium, 11 Low)**. The Critical and four Highs were
design-level and are folded below; the rest are plan-level and live in
`docs/superpowers/plans/2026-07-30-sluice-init.md`.

**What round 1 corrected, and the single cause under it.** Every finding that hurt was a test I
wrote that would have passed while verifying nothing — the same failure this repo's rules call THE
LESSON, quoted in the plan's own Global Constraints. Four instances, each measured by a reviewer
rather than argued:

- **The scaffold disarmed the judge's abstain default** (Critical), and the acceptance test asserted
  that outcome as the success criterion.
- **`TtyAsker` returned its default unparsed**, so the fresh-install TTY run wrote a cwd-relative
  `vault_dir` — and the anti-drift test compared the buggy value to itself, passing *because* of the
  bug.
- **M1 left the neutrality test green.** `build_plan` reads `answers.get(...)` and the defaults live
  in the asker, so the mutant was equivalent. The spec demanded in writing that M1 witness the
  neutrality half; the plan retargeted it and tabulated it as satisfied.
- **The functional tier ran on a fixture that sets `SLUICE_CONFIG` and `VAULT_DIR`**, so `cmd_init`
  always took the skip branch. `tests/conftest.py:46` says the functional tier never reaches those
  variables — a fact already recorded in this project's memory.

**Issue:** #8 · **Depends on:** #80 (merged, PR #82 — settled where the config lives)
**Related:** #81 (`_merged/` blindness — orthogonal, init creates no dedup state)
**Sub-apps:** new `sluice/onboard/`, plus `cli`, `core/config`, `core/protocols`, `core/vault`,
`triage/prompt`

## The problem, and the part of it that was not in the issue

Issue #8 states the cliff correctly: sluice ships no preferences, so out of the box it is correct
and useless — with no Judging Profile the judge returns `research` for nearly everything, and there
is no path from "installed" to "configured".

While designing this, a second and worse form of the same problem was found **in the documented
onboarding path**, and measured rather than reasoned about. `README.md:88` tells a new user to:

```bash
cp -n sluice.yaml.example "$config_dir/config.yaml"
```

`sluice.yaml.example` ships *active*, uncommented placeholder gates. Loaded from that copy:

```
'Senior Software Engineer'  -> is_relevant = False
'Engineering Manager'       -> is_relevant = False
'Horticultural Consultant'  -> is_relevant = True
accept_titles = ['horticultural consultant', 'geoscientist']
contract_floor_gbp_day = 450     perm_floor_gbp = 90000
```

`relevance_keep` drops non-matching titles at ingest, before dedup and before any LLM call. So the
onboarding sluice currently documents reproduces the `672ad2a` shape: the empty-config-abstains
invariant is held in the *code defaults* and given away by the *file the user is told to copy*.
`locations` and `lead_ttl_days` already ship commented for exactly this reason — their comments say
"this file is COPIED" — and the title, relevance and pay gates never got the same treatment.

This reframes #8. It is not only "add a scaffolder"; it is "make the blessed onboarding path one
that cannot express a preference the user did not state".

## Decisions

Four, taken during brainstorming, each with the alternatives that were rejected.

1. **`init` owns a neutral template it generates; `sluice.yaml.example` is unchanged.** The example
   stays the annotated catalogue with illustrative values. Rejected: copying the example verbatim
   (ships the measured gate above, and attaches a CLI command to it); neutralising the example and
   copying it (one file, no drift — but the example loses its "here is what a filled-in value looks
   like" quality). README's quickstart switches from `cp sluice.yaml.example …` to `sluice init`, so
   nobody is *told* to copy the file with live gates in it.
2. **Interactive when stdin is a TTY, flags otherwise.** Rejected: refusing to guess and requiring
   `--vault` always (safest, least friendly); defaulting to `./vault` with a warning (this
   codebase's own doctrine is that warn-and-continue on a path is how state silently goes missing).
3. **The wizard covers identity, taste, providers and per-source searches.** The full walk, not a
   subset.
4. **The Judging Profile is short answers plus `$EDITOR` for prose**, degrading to the plain
   scaffold. Rejected: terminal-only interview (career prose at a readline prompt); scaffold-only
   (the one artefact that most needs filling in is the one the wizard would not help with).

## Architecture

```
sluice/onboard/
  questions.py   PURE    the question catalogue + per-question parsers
  plan.py        PURE    answers -> InitPlan (artefact text + destinations)
  ask.py         IMPURE  TTY prompting, $EDITOR, non-TTY refusal
cli.py: cmd_init         preflight -> ask -> build_plan -> write
```

`onboard/`, not `init/`: a package named `init` is confusable with `__init__.py` in tracebacks. The
command is still `sluice init`. It is a command package, not a sixth pipeline sub-app, and
`docs/ARCHITECTURE.md` should say so.

`cmd_init` is lazy-imported inside the command function like every other handler, so offline
commands still never touch Camofox, the vault or a backend.

**The split earns its keep at `build_plan(answers) -> InitPlan`** — a pure function from a dict to
two strings and two paths. The load-bearing property becomes a unit test rather than a wizard
transcript:

```python
build_plan({})       # enter-through: every question skipped
                     # -> a config whose every gate abstains, asserted by LOADING it
```

Rejected: one `wizard.py` that prompts and writes as it goes (every test then needs the prompt
driver, and twenty answer combinations become too expensive to table-test, so nobody does).
Rejected: generating the config by parsing `sluice.yaml.example` and commenting out its active
values — round-tripping YAML *with comments intact* needs `ruamel`, and `sluice/` is standard-library
only. Replaced by a cheaper guard: a test that every key the wizard can emit is documented in the
example, enumerated from the catalogue rather than hand-listed.

**The asker is a constructor parameter, not a fifth plugin seam.** `cmd_init(args, config, *,
ask=None)` defaults to the TTY asker; tests pass a scripted one — the `Sluice(sleep=…, today=…)`
idiom from PR 0. The four adapter seams are backend/store/renderer/fetcher, and a prompt is not an
adapter.

### Data flow

```
cmd_init
  |
  +- preflight    resolve BOTH destinations, see what already exists
  |               both exist -> report, exit 0, ask NOTHING
  |               one exists -> skip that artefact's questions
  |
  +- ask          flags first, then TTY prompt for gaps
  |               not a TTY and an answer missing -> exit 2, write NOTHING
  |
  +- build_plan   PURE. answers -> (config.yaml text, Judging Profile text)
  |
  +- write        config  -> paths.config_file(), exclusive create
                  profile -> Store.write_document(rel, text, only_if_absent=True)
```

### The profile goes through the store seam

Not the filesystem. `Store.write_document` already exists, already guards path escape (`realpath` +
`commonpath`, so a symlink inside the store cannot escape), and routing through it keeps `init`
honest for #1's future non-vault store.

**And through the SEAM, not `Vault(...)` by hand** (round-1 High, found independently by the
architect and the invariant reviewer). The first draft constructed `Vault(vault_dir)` directly,
which would have made `cmd_init` the only handler in `cli.py` hardwiring a store implementation —
and would have made the ARCHITECTURE.md sentence this design adds false the day it landed. It
resolves `Sluice(dataclasses.replace(config, vault_dir=vault_dir)).store()` instead.

That route has a trap worth stating, because taking it naively reintroduces the bug it fixes:
**`stores/vault.py:_make` is env-first** (`os.environ.get("VAULT_DIR") or config.vault_dir`), so an
exported `VAULT_DIR` would win over the `--vault` the user just typed, and the report would name a
directory the profile did not go to. `cmd_init` therefore **refuses when `--vault` and `$VAULT_DIR`
disagree** rather than silently preferring either. A refusal, not a precedence rule: the two
answers contradict each other and only the user knows which they meant.

**The widened contract needs store-agnostic rows.** `core/protocols.py`'s own docstring says
never-clobber properties live on the contract precisely because "a second store would ship without
them", and `require_status` — the precedent cited above — got three conformance rows. So
`only_if_absent` gets rows in `tests/conformance/test_store_contract.py`, parametrised over
`store_name`, asserted through `read_criteria()` rather than a path. #1 is the next backlog item,
so the second store is not hypothetical.

It needs one change. It currently calls `_atomic_write`, which overwrites. Add
`only_if_absent: bool = False`, mirroring `set_tailored_cv`'s existing parameter of the same name.
Returns the path written, or `""` when the document existed and was skipped — the same
abstain-by-falsy convention the other never-clobber writers use.

**A parameter on the existing writer, never a second write function.** This is #9's `require_status`
precedent: CodeQL reads a new write function as a new sink. The implementation uses the existing
`_write(..., exclusive=True)` (`O_CREAT|O_EXCL`, from #16), not check-then-write, so the
never-clobber is TOCTOU-free rather than merely likely.

## The question catalogue

Every question is `Question(key, prompt, parse, writes_to)`. **Blank input is a skip, never a
value** — with exactly one exception, called out because it is the kind of thing that gets
"simplified" later: the **vault question takes a default** (`./vault`) when blank, because the
profile has to land somewhere and there is no such thing as skipping it. Every *preference* question
skips. The default is offered only on a TTY, where the user can see and reject it; under
`--no-input` the vault must be named explicitly or init refuses (below).

That default is **imported, not re-spelled**. `core/vault.py:33` already holds `"./vault"` and it is
one of the nine `"./"` literals the definition-of-done grep permits; a second copy in `onboard/`
would take the count to ten and read as drift. Promote it to a public `DEFAULT_VAULT` alongside the
`CRITERIA_RELPATH` move and import it.

| section | asks | writes |
|---|---|---|
| Vault | where your vault is *(the one required answer)* | `vault_dir` |
| You | name · contact block · employers | `cv.name`, `cv.contact`, `cv.employers` |
| Want | titles you want · titles that disqualify · where you'll work · companies to skip · contract floor · perm floor · staleness TTL | `triage.accept_titles`, `.reject_titles`, `.target_locations`, `.reject_companies`, `.contract_floor_gbp_day`, `.perm_floor_gbp`, `lead_ttl_days` |
| Cost | coarse ingest title filter *(asked last, consequence stated in the prompt)* | `relevance_keep`, `relevance_drop` |
| Providers | primary backend · fallback · CV renderer | fanned out to **all three** of `triage`/`cv`/`track` |
| Sources | which of the 22 registered boards · label+URL per search | `sources.<id>.enabled`, `.searches` |
| Profile | one line per judge heading, or `$EDITOR` | the vault note |

**Not asked, deliberately:** the ~50 mechanics fields (batch sizes, model ids, efforts, the five cv
artefact directories, the auto-advance thresholds, the Gmail/Calendar windows), and the two
`ats_relay_domains` / `job_board_domains` denylists — those are *safety* lists where a wrong answer
makes receipt-matching more permissive, not less, so they are not a preference and not a prompt.
Also not asked: `cv.fabrication_decoys`, which has no answer a user can give without an explanation
longer than the wizard.

### Two findings that shaped the catalogue

**`Config.locations` is a dead key.** `core/config.py:21` says so in its own comment — "nothing reads
`Config.locations` yet … a loaded gun rather than a live bug". Grep confirms the only consumers are
three assertions in the test suite. The wizard therefore asks geography **once** and writes
`triage.target_locations`, which is live. A wizard that populates a dead key is `triage.dossier_dir`
again: declared, read by nothing, and setting it did nothing, silently.

**Decision: retire it with a raise**, the same treatment #80 gave `triage.dossier_dir` — setting
`locations` errors at load with a message pointing at `triage.target_locations`, and the key comes
out of `sluice.yaml.example`. Self-contained, precedent in the PR that just shipped, and it stops
the wizard being the thing that finally populates it.

**The two title gates are different and both live.** `relevance_keep`/`relevance_drop` is the coarse
ingest gate ("make ingest cheap", before dedup and any LLM call); `triage.accept_titles`/
`reject_titles` is downstream role-shape screening. Asking both is correct, but `relevance_keep` is
the most dangerous key in the file — it is the one measured above turning `Senior Software Engineer`
into `False`. It is asked last, and its prompt states the consequence.

### The board walk (folded in after round 1)

The first plan deferred the per-source walk and left `build_plan(sources=…)` with no caller —
an unreachable parameter, which the architect correctly called out as the premature abstraction
the seams doctrine warns against. It is folded in instead.

The walk is shaped by one fact about the data: **a search is a label plus a URL the user pastes
from a browser**, and there are 22 registered boards. So it is two passes, not one 22-deep
interrogation:

1. **Select.** Print the boards in columns with an index, take a comma-separated list of ids or
   indices. Empty means "leave sources unset", which emits the commented example block and lets
   every source run its own neutral example search — the abstain default, unchanged.
2. **Per selected board, collect searches.** Label, then URL, repeating until a blank label. A URL
   that fails `parse_url` is re-asked rather than dropped, because a mistyped board URL that is
   silently skipped is a source the user believes is configured and is not.

Under `--no-input` the walk is skipped entirely and no `sources:` block is written — the wizard's
enter-through equivalence is preserved exactly.

Enumerating the boards is offline: measured, `sluice.ingest.sources.all_sources()` loads all 22
with no Camofox import. The import stays inside the command function like every other handler's.

### Flags, and why there are almost none

Only `--vault DIR` and `--no-input`. The vault is the sole *required* answer; everything else
defaults to skip. That makes the non-interactive path and the enter-through path **the same path**:

```
sluice init --vault ~/Notes --no-input   ==   a TTY run with Enter pressed through every question
                                         ==   the neutral config + the scaffolded profile
```

This is what removes the drift risk inherent in a TTY/flags split: there is no second code path to
drift, and the functional tier drives the whole thing through `main(argv)` with no prompt injection.

Rejected: a flag per question (~15 flags); `--answers FILE` (a second config format for a job the
config file already does).

### Emitting YAML without a YAML writer

The config is a template with comments, so `yaml.safe_dump` is out (it destroys them) and `ruamel`
is out (stdlib-only). Values are injected by a deliberately conservative emitter: strings always
double-quoted with `\` and `"` escaped, lists as flow sequences of double-quoted scalars, integers
bare. Double-quoted YAML scalars have a total escape grammar, so this is safe rather than lucky.

Pinned by a round-trip test over a nasty corpus — `O'Example`, `Foo: Bar`, `#hash`, `yes`, `!tag`,
backslashes, newlines, non-ASCII. Without it, a company name with an apostrophe writes a config that
fails to parse.

**An inactive block is commented ONCE** (round-1 High, found by three reviewers and measured by
two). The first draft commented the block header *and* re-prefixed each already-commented body
line, producing `#   # accept_titles:` — which defeated the scope guard's own `^\s*#?\s*{leaf}:`
matcher on 16 of 19 keys while the neutrality half stayed green. The implementer would have seen
one red test whose message ("absent from the template") was false, which is an invitation to weaken
exactly the guard this design spent three paragraphs justifying.

The fix is the renderer, not the regex: body lines are already comments, so only the header needs
commenting. Widening the matcher to `^[#\s]*` was rejected — that is the matched-by-adjacent-prose
bug this repo has already shipped, and it would let a comment *about* a key satisfy a check meant to
find the key. The guard gains a negative control: a prose line containing `<leaf>:` mid-sentence
must not satisfy the matcher.

Note `yaml` is already an `ImportError`-guarded import in each config module, and the emitter does
not need it — it templates and quotes. No new runtime dependency.

## The Judging Profile

The judge reads this as prose; **nothing parses the headings**. So if init's headings drift from what
`_SCAFFOLD_TAIL`'s "Final reminders" refer to ("the profile's target shape", "win patterns",
"anti-patterns"), nothing errors — the judge quietly gets a profile organised around headings its
own instructions do not reference, and verdict quality degrades with no signal anywhere.

The five headings, extracted from `_DEFAULT_CRITERIA`:

```
## Who this candidate is
### Target and wrong shape
### Background grounding
## Win patterns and anti-patterns
## Industry filter (judgement-based, not categorical)
```

**The drift pin** follows #30's `_CITE_RE` precedent — a check that must match what another module
delivers shares the constant and asserts equality, because a comment saying "matches the scaffold"
is not a check:

```python
scaffold = set(re.findall(r'^#{2,3} .+$', _DEFAULT_CRITERIA, re.M))
written  = set(re.findall(r'^#{2,3} .+$', build_plan({}).profile, re.M))
assert scaffold == written
assert scaffold == PINNED_FIVE      # scope: set() == set() must not pass
```

The second assertion is load-bearing. Without it the test is the `all([])` shape — if the regex ever
stops matching on both sides it passes for the emptiest possible reason. It also means adding a
sixth heading forces the author to touch both sides, which is the point.

### An unanswered heading carries the shipped default's prose (round-1 Critical)

The first design emitted a heading plus an HTML comment addressed to the *user*. That is always a
non-empty file, and `build_system_prompt_from` falls back to `_DEFAULT_CRITERIA` **only when the
criteria text is missing or empty** (`triage/prompt.py:146`). So from the first `sluice init` the
judge would permanently lose the only shipped text telling it to abstain — "prefer `research`", "do
not score on role shape", "do not assume a culture preference", "never invent past employers" —
while the surrounding scaffold still instructs it to *"treat them as authoritative"* and *"be
willing to dismiss; do not hedge into research"*. Running the onboarding command would make an
unconfigured install stop abstaining: the `672ad2a` class, delivered by the feature built to fix
onboarding.

**So each unanswered heading is rendered with `_DEFAULT_CRITERIA`'s own prose for that heading,
followed by the `<!-- prompt -->`.** The abstain instructions stay live until a human replaces
them, which is precisely "use the default when the user does not answer".

This also **dissolves `PROFILE_HEADINGS` as a hand-copied duplicate.** The template is built by
splitting `_DEFAULT_CRITERIA` on its own headings, so the heading set is derived rather than
restated, and the drift pin stops being an equality assertion between two hand-maintained lists.
The pin remains, now as a scope check that the split found all five and that no heading is empty.

**Content generation is three tiers, degrading safely:**

1. A one-line answer → written under the heading.
2. Blank on a TTY with `$EDITOR` set → a temp file pre-filled with that heading's prompt, opened via
   `shlex.split($EDITOR)` (never `shell=True`, so `code --wait` works). Editor exits non-zero, or
   the file comes back unchanged → treated as no answer, tier 3.
3. Anything else — `--no-input`, no TTY, `$EDITOR` unset or not found → the scaffold comment stays,
   exactly as the issue describes ("prompts rather than answers under each heading").

So the profile is never *worse* than the scaffold, and the wizard cannot fail because of an editor.

### One consolidation folded in

`_CRITERIA_RELPATH` is currently **duplicated** — `core/vault.py:32` and `triage/prompt.py:20`, two
independent literals for one path. Adding a third copy in `onboard/` makes it three literals that
must agree, where a divergence means init writes a profile the judge never reads — silently, since a
missing profile just falls back to the opinion-free default.

Promote it to one public `CRITERIA_RELPATH` in `core/protocols.py`, where `Store`, `LeadNote` and
`VaultConflict` already live: it *is* part of the store contract, the document `read_criteria`
serves. Imported by `core/vault.py`, `triage/prompt.py` and `onboard/`. No layering inversion
(`core` does not import from `triage`), and it removes the duplication rather than tripling it.

## Error handling and refusals

Preflight resolves everything **before asking anything**, so a wizard is never sat through only to
have its answers discarded.

| config exists | profile exists | behaviour |
|---|---|---|
| no | no | full wizard |
| yes | no | skip the config questions, ask the profile ones |
| no | yes | ask the config questions, skip the profile one |
| yes | yes | report both, **exit 0**, ask nothing |

**Hard refusals** — exit 2, nothing written:

- No vault answer and `--no-input`, or stdin is not a TTY. Exits naming `--vault`; **never hangs on
  a pipe**.
- The vault path exists but is not a directory.

**Deliberately not a refusal:** a vault directory that does not exist yet is created — that is the
legitimate first-run case. The report distinguishes *"created new vault at X"* from *"using existing
vault at X"*, and that one word is what catches a typo'd path before the user wonders where their
notes went.

**Deliberately not preflighted:** writability. `os.access` lies under ACLs and network filesystems,
so the write is allowed to fail and report. A check that can be wrong in the permissive direction is
worse than no check.

**Partial failure never rolls back.** Config written, profile write fails → both outcomes reported,
exit non-zero, config stays. Deleting a file just written to the user's disk to "clean up" is a
destructive act against a failure the user can see and retry, and a re-run skips the config and
retries the profile. That is what idempotence is for, and it matches the repo's doctrine that sluice
never moves or removes your data.

**Answer validation is local and offline.** Pay floors and the TTL parse as `int` and reject
bool-ish input *first* — #75's trap one layer up: a user typing `yes` for `lead_ttl_days` must not
become `lead_ttl_days: yes`, which PyYAML loads as `True`, which is `1`, which marks every lead
stale with nothing raising anywhere. Search URLs get a `urllib.parse` scheme check only — **not**
`core/urlguard.py`, which resolves DNS: that would make init non-hermetic and could block a
legitimate board behind a slow resolver.

`vault_dir` is written **absolute** (`expanduser` → `abspath`). A relative one is the "second empty
vault beside you" hazard `README.md:97` warns about, reintroduced by the wizard itself.

### Post-write report

init prints what the config will actually *do* — not just what it wrote:

```
wrote  ~/.config/sluice/config.yaml
wrote  ~/Notes/Job Applications/Judging Profile.md

Your config will:
  keep only titles containing: eng manager
    (everything else dropped before triage)
  skip leads unseen for 90 days
  need $DEEPSEEK_API_KEY exported

Next:
  1. fill in the profile headings
  2. sluice ingest list-sources --health
  3. sluice triage run --no-llm
```

Rejected: also running `doctor --offline`. That would make init depend on doctor's output shape and
gain a failure mode with nothing to do with scaffolding.

**No secrets in the config.** The repo's rule is that API keys come from the environment, so the
wizard asks *which provider* and prints the `export` line. It never stores a key.

### #80 and #81 obligations, explicit

- init creates nothing under the state or cache roots — asserted, not assumed.
- The config destination has no `_LEGACY` entry (`core/paths.py:55` omits it deliberately), so
  writing it cannot disarm a relocation notice.
- Only keys the user actually answered are emitted, so no path key silently gains a non-empty value
  that would short-circuit `resolve`'s chain. `vault_dir` is the deliberate exception and is not in
  that chain — its precedence lives in `stores/vault.py:_make` as `env or config or default`.
- `$SLUICE_CONFIG`, if set, wins (the #80 short-circuit). The report names the resolved path so
  there is no ambiguity about where the file went.
- **#81 is orthogonal:** init creates no dedup state and no `_merged/`.

## Test plan

**Pure unit** (`tests/test_onboard_plan.py`) — the load-bearing tier, since `build_plan` is a
function from a dict to two strings:

- Enter-through neutrality, asserted by **loading** the emitted config through all five loaders and
  checking every gate abstains. Behaviour, not text.

  **This assertion is vacuous on its own and must be paired.** A `build_plan({})` that emitted an
  empty file — or no file — passes it, because the loaders would then return the (neutral) code
  defaults and every gate would abstain for the wrong reason. This is the `all([])` shape that has
  already shipped three times in this repo. So it is paired with a **scope** assertion: the emitted
  template must contain every key in the catalogue, commented.

  **Round 1 found the pair itself insufficient, and it was right.** The neutrality half hand-listed
  thirteen fields — an enumeration, in a design that uses discovery two tests below for the backend
  fan-out. So it becomes an **enumerated differential**: for every loader, `loader(emitted)` is
  field-for-field equal to `loader(None)` except `vault_dir`, plus a scope assertion that the field
  sweep enumerated something. A future catalogue key rendered with a value now cannot pass, and
  nothing has to be kept in step by hand.

  **And M1 was aimed at the wrong test.** `build_plan` reads `answers.get(...)` while the defaults
  live in the asker, so a default on `accept_titles` leaves the neutrality assertion green — an
  equivalent mutant, measured. The mutant that actually falsifies neutrality is **deleting the
  `if value is None …` arm in `_render_key`**, so an unset key emits an active value. The plan's
  witness table names that, and stops claiming M1 witnesses a test it does not.
- YAML emitter round-trip over the nasty corpus.
- No answer can emit a scalar that loads as `bool` where an `int` is expected.
- Heading drift pin, both-sides extraction plus the scope assertion.
- Backend fan-out completeness, **discovered** not hand-listed: the sweep finds the config
  dataclasses carrying `primary_backend` (measured: `CvConfig`, `TrackConfig`, `TriageConfig`) and
  asserts the plan writes each. Narrowing the roster must redden — #63's lesson that a hand-list of
  dataclasses leaks exactly like the hand-list of fields it replaced.
- Every key the catalogue can emit is documented in `sluice.yaml.example`.

**Functional** (`tests/functional/`, real `main(argv)`): both files land; a re-run skips both and
leaves them byte-identical; missing `--vault` under `--no-input` exits 2 writing nothing;
`$SLUICE_CONFIG` retargets the config; the state root stays untouched. The profile is verified by
**calling `read_criteria()`**, not by checking a path — that is what proves init wrote where the
judge reads.

**Not through the `cli` fixture** (round-1 High, found by two reviewers). That fixture calls
`build_harness`, which writes a config and `setenv`s `SLUICE_CONFIG` and `VAULT_DIR`
(`tests/harness/config.py:213-215`) — *after* each test's own `delenv`. So `config_file()` would
always resolve to an existing file, `cmd_init` would always take the skip branch, six of ten tests
would fail and two would pass vacuously, including M6's named killer. `tests/conftest.py:46` states
the underlying fact outright: the functional tier never reaches those variables.

`init` needs no browser, renderer, backend or seeded vault, so it gets a thin `main(argv)` driver
running under the autouse `_pin_paths` sandbox alone. Asserted paths are derived from
`paths.config_file()`, never written as literals. This also takes the `cli`-fixture move off the
critical path entirely.

**Prompt path** (`tests/test_onboard_ask.py`): a scripted asker drives the whole catalogue including
the source walk and a substituted `$EDITOR`, and asserts the TTY path and the `--no-input` path
**converge on the same plan** for equivalent answers. The anti-drift property stated as a test
rather than a promise.

**E2E acceptance** (`tests/e2e/`) — issue #8's own acceptance criterion, in **two arms**, because a
single arm passes even if the profile is ignored entirely: with a filled profile the lead gets
`shortlist`/`dismiss`; with no profile the same lead gets `research`. Same attribution shape as S1 in
#58.

### Mutation witnesses

Each mutates live production code, one site at a time, run by node id, with the rest of the file
checked to confirm the *new* test is the killer (a mutation killed by a pre-existing test witnesses
nothing about a new one). Mutate by moving or deleting, never by adding. Run
`compileall --invalidation-mode checked-hash` first.

| | mutant | should redden |
|---|---|---|
| M1 | **delete the `if value is None …` arm in `_render_key`** so an unset key emits an active value | enter-through **differential** |
| M2 | drop `only_if_absent=True` at the call site | never-clobber re-run |
| M3 | change one heading in `_DEFAULT_CRITERIA` | drift pin (heading split) |
| M4 | delete one of the three fan-out destinations | fan-out completeness |
| M5 | emitter → naive interpolation | round-trip corpus |
| M6 | delete the non-TTY refusal | refusal |
| M7 | **make `TtyAsker` return `q.default` unparsed** on the blank branch | blank-TTY absolute vault |
| M8 | double-comment an inactive block's body lines again | scope guard |
| M9 | give `ApplyConfig` a `primary_backend` field | fan-out **discovery** (must redden while the hand-listed half stays green) |

M1 and M7 were both mis-aimed in round 1 — M1 at a test it could not falsify, M7 at `parse_path`,
which the failing path never calls. Each now mutates the site the assertion actually depends on.

### Neutrality

Every test answer synthetic (`Example …`, `example.invalid` URLs, seeded faker for titles). No real
employer, location, contact detail or absolute home path in `sluice/` or `tests/`.

And the wizard's **question text must itself express no preference** — it asks which titles you
want; it never proposes a taxonomy of good jobs. This is a new surface for the neutrality invariant:
every prior leak risk was a *value*, and this one is a *question*.

**Round 1 rejected the first guard for it**, and the reasoning generalises. A hand-listed
banned-word blocklist is the enumeration-instead-of-a-sweep failure, and it is the approach
`tests/test_sluice_neutral_defaults.py:15-22` explicitly rules out. Measured: a hint reading "Most
people put a platform or infrastructure role here" leaves it green, and its bare substring match
also fails on its own scaffold (`senior` ⊂ `seniority`). It is therefore:

- **word-boundary matched** (`\b{word}\b`), not substring;
- held in **one place** both tiers import, rather than duplicated verbatim into the e2e test;
- swept over **everything shipped**, not just `q.prompt`/`q.hint` — `_HEADER`, `_SECTION_BLURB`, the
  profile prompts and `ask._PROFILE_QUESTIONS` all land in a user's files and none was covered;
- given a **scope assertion** before the loop and a **positive control** proving a synthetic string
  containing a banned word is rejected by the same helper;
- and **named honestly as a smoke test**, so nobody deletes a real check on the strength of it.

The falsifying witness gets run *first*, before the guard is trusted.

## Definition of done

- `sluice init` exists, lazy-imported, with `--vault` and `--no-input`.
- `build_plan({})` produces a config that is field-for-field identical, through every loader, to no
  config at all — except `vault_dir`.
- A re-run clobbers nothing and exits 0.
- Non-TTY without `--vault` exits 2 having written nothing.
- A blank Enter at the vault prompt yields an **absolute** path, in the answers and in the file.
- The profile lands where `read_criteria()` reads it, written through the **store seam**, with an
  unanswered heading carrying `_DEFAULT_CRITERIA`'s prose.
- `--vault` disagreeing with `$VAULT_DIR` refuses rather than silently preferring either.
- The board walk configures `sources.<id>.enabled`/`.searches`; `--no-input` writes no `sources:`.
- `only_if_absent` has store-agnostic rows in `tests/conformance/test_store_contract.py`.
- `Config.locations` is retired with a raise — in the config file **and** via `SLUICE_LOCATIONS` —
  and its documentation is relocated onto a commented `triage.target_locations`.
- `CRITERIA_RELPATH` has one home; `DEFAULT_VAULT` reaches the catalogue as a parameter, not as an
  import from the store implementation.
- No shipped doc instructs a `cp sluice.yaml.example`, asserted by a guard test. README,
  `docs/ARCHITECTURE.md` and `.rulesync/rules/CLAUDE.md` (human-gated) are all updated.
- All **nine** mutation witnesses run and redden their named test, each verified to be the killer.
- `ruff check sluice tests scripts` clean; full suite green; the `"./"` DoD grep still at 9 lines.

## Out of scope

- Wiring `Config.locations` to a real consumer — that is a feature with its own geography-preference
  risk, and its own issue.
- #81's `_merged/` blindness.
- Any change to `sluice.yaml.example`'s illustrative values. It remains the annotated catalogue; the
  fix is that nobody is told to copy it.

  **Amended after round 1:** the file may still gain and lose *documentation* for keys the wizard
  writes. `triage.target_locations` is measurably absent from it today (so is `reject_locations`),
  which made the original scope self-contradictory three ways: the documentation sweep could not
  pass on its first run, the retirement message redirected users to an undocumented key, and Task 7
  deleted the only geography key the catalogue did document. So the retired `locations` block's
  "this file is COPIED" guidance is **relocated** into the `triage:` block as a commented
  `target_locations`. That is a move of existing documentation, not a new illustrative value, and it
  keeps the no-new-preferences rule intact.
- A `--force` flag. A flag that can overwrite a filled-in Judging Profile is the never-clobber
  breach this feature exists to avoid; deleting the file is already the way to redo it.
