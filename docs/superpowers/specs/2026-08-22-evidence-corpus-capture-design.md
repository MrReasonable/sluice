# Evidence corpus capture — Experience Library, Skills Inventory and STAR Stories as first-class stores (#164)

- **Date**: 2026-08-22
- **Status**: DESIGNED, revised twice after two plan-review rounds (5 reviewers each; 43 findings
  then 48). Not yet implemented.
- **Issue**: **#164**, **narrowed** — capture through the CLI and the `init` wizard, plus a
  read-only MCP tool. The MCP **write** tool #164 lists as item 4 is deferred to its own issue; see
  §"Why the MCP write tool is not in this PR", which is the single most important decision here.
- **Position in a chain**: PR 1 of 3 (#164 → #165 → #168).
- **Scope**: capture only. Nothing here makes Skills Inventory or STAR Stories *used* — that is #165.

## Goal

Three vault stores that answer "what has this person actually done" can only be built with a text
editor, and two are inert. Measured at `62adeae`:

| Store | Read by | Written by |
|---|---|---|
| Experience Library | `cv/engine.py:217`, `Vault.preflight` (`core/vault.py:1388`) | nothing in sluice |
| Skills Inventory | nothing | nothing |
| STAR Stories | nothing | nothing |

`core/vault.py:53-55` defines the three vault-path constants `_LEADS_SUBDIR`, `_EXP_SUBDIR`,
`_MYCV_BASELINE`. (Other store-managed paths exist — `CRITERIA_RELPATH`,
`CANDIDATE_PROFILE_RELPATH`, `_MERGED_SUBDIR` — so this is "the three subdir constants", not every
path the store knows.) At `62adeae`, grepping the tracked tree for either new store's name returned
zero hits repository-wide; this document is the first, so re-measure against that commit.

## Why the MCP write tool is not in this PR

Two review rounds produced 91 findings. One Critical and roughly half the Highs trace to a single
fact: an LLM could author the `name`, `fields` and `body` of a proposed entry.

The decisive one is not about the `verified` key at all, which is what the first two revisions kept
hardening. `propose_evidence`'s **`body`** is spliced verbatim into the bundle the composer and the
gate both read. `cv/validate.py:66` is `nums[cur] = set(...)` — an **assignment**, not `|=` — so a
body line shaped like a real citation code rebinds another entry's permitted numbers. Executed
during review:

```
CLEAN  nums: {'AL1': {'12'}}       POISON nums: {'AL1': {'4200'}}
genuine 12 survived? False         fabricated 4200 now permitted? True
```

A bullet `- Delivered 4200 units [AL1]` then clears the hard gate with zero violations. This is
pre-existing and **already documented as accepted** at `cv/validate.py:32-37`: *"NB this NARROWS the
free-text bypass rather than closing it… the real close is handing validate() the true id list, a
signature change."* It was acceptable because `body` could only be hand-typed. An MCP write tool is
what gives it a caller.

That close is **not tracked** — the issue the comment cites (#31) was closed 2026-07-20 and was
about the negatives block, not the signature change. So the MCP write tool needs a real prerequisite
that does not exist yet, and shipping it here would arm a known gate bypass to reach a rendered PDF
under the user's name.

**Consequence for everything below:** with no LLM caller, the store's input guards are
*integrity* guards against a human typo or a pasted oddity, not a security boundary. They are still
specified and still tested — a later PR adds the hostile caller, and a guard retrofitted then is a
guard nobody designed. But this document no longer rests the fabrication gate on them, which is what
the two previous revisions wrongly did.

## Decisions

1. **One `.md` per entry**, mirroring the Experience Library. Rejected: one file holding many
   entries — no natural `_inbox/`, and shared-file appends need lead-note CAS discipline.
2. **Propose-only writes.** CLI and wizard land in `_inbox/` with no `verified:`. Promotion is only
   the interactive `verify`. No `--verified` flag, and `verified` is not a user field, so the
   flag-generating loop cannot emit one.
3. **Three top-level command groups from one parameterised loop.**
4. **Four generic `Store` members, not nine per-kind ones.** Reviewed twice and upheld.
5. **The third kind is `stories`, not `star`** — `star` reads as a verb, and this repo shortlists.
6. **No MCP write tool** (above). A read-only `list_evidence` ships.

## Design

### The registry, split

**`core/protocols.py`** — `EVIDENCE_KINDS = {"experience": …, "skills": …, "stories": …}`, each
carrying a `"/"`-separated relpath and its **user field names**. That module is already the home for
opaque document keys (`CRITERIA_RELPATH`, `CANDIDATE_PROFILE_RELPATH`) consumed via `_doc_path`.

**`core/vault.py`** — `INBOX_SUBDIR` and the `verified` key name: filesystem mechanism a SQLite store
would not have. Vault's per-kind directory map is **derived** from `EVIDENCE_KINDS` by
comprehension, not hand-listed. v2 proposed "a guard asserts the map covers exactly
`EVIDENCE_KINDS.keys()`"; with one `INBOX_SUBDIR` constant that guard cannot fail, so it is not
written — derivation makes the property structural.

`_EXP_SUBDIR` migrates to the `"/"`-separated form (verified safe: two uses, byte-identical).

### On-disk layout

```
Job Applications/
  Experience Library/   <entry>.md  Company, Category, Best For, Metrics   + _inbox/
  Skills Inventory/     <entry>.md  Proficiency, Domain, Evidence, Signal Value   + _inbox/
  STAR Stories/         <entry>.md  Company, Best For                      + _inbox/
                                    body: ## Situation / ## Task / ## Action / ## Result
```

`verified` is store-managed and appears in no kind's user field list. Field **values** are free
text: no graded proficiency scale ships, because a scale in `core/protocols.py` is a rubric for
ranking a candidate and would sit outside `test_sluice_neutral_defaults.py`'s `sluice/**/config.py`
glob.

S/T/A/R lives in the body because `_parse_fm_spaced` (`core/vault.py:111`) is line-based: a
multi-line value does not round-trip, its continuation lines are re-read as further keys.

### Store contract

```python
read_evidence(kind, verified_only=True)          -> list[dict]
read_pending_evidence(kind)                      -> list[dict]
propose_evidence(kind, *, name, fields, body="") -> str
verify_evidence(kind, name, *, today, reviewed)  -> bool
```

**Return schema — the eight keys as a FLOOR, plus `fields`.** v2 pinned the return to exactly the
eight keys `read_experience_entries` emits (`core/vault.py:1241-1247`: `path`, `title`, `company`,
`category`, `best_for`, `metrics`, `verified`, `body`). Review found that Skills' four user fields
map to **none** of them — the feature would have written four fields per skill and read back zero,
leaving `skills list` able to print only a filename and forcing #165 to bypass the contract. So every
kind returns those eight (absent ones empty, which keeps `_prefix`/`assign_codes`/`rank`/`validate`
working — verified) **plus `fields: dict`** carrying the kind's own frontmatter verbatim.

**`read_experience_entries` stays** as a one-line delegate. Two live consumers (`cv/engine.py:217`,
`Vault.preflight`), a conformance test (`tests/conformance/test_store_contract.py:344`), and entries
in two hand-listed literals (`tests/test_mcpserver.py:1105`, `tests/test_cv_engine.py:1562`).

**Unknown `kind` raises and lists the valid names**, on all four members.

**`_inbox/` stays hidden by the flat listing.** No by-name exclusion ships: the reader is flat
(`core/vault.py:1236-1237`), so an added exclusion sits beside the existing `.endswith(".md")` check
and deleting it stays green — an equivalent mutant. A behaviour test pins that `_inbox/` entries are
invisible to `read_evidence`, and a comment names `_PRIVATE_SUBDIRS` as the change required if the
reader ever becomes recursive. This also keeps `docs/ARCHITECTURE.md:1123-1127`'s "flat-listing
accident" sentence true.

### Writing an entry

**Name.** The store **slugifies first**, then asserts the slug is a bare filename component (no
separator, no `.`/`..`, non-empty). v2 specified a containment check on a path joined from the raw
name *and* slugification elsewhere, without ordering them: slugify-first makes the containment check
unfirable (an equivalent mutant), check-first validates a path that is not the one written. Asserting
the **slug's shape** is falsifiable — break the slugifier and the assertion goes red — and the
written path is the validated one by construction.

**A symlinked `_inbox/` is refused, not resolved.** `os.path.realpath` on the inbox would make a
symlink *at* `_inbox/` structurally invisible (`_inbox -> ..` would put writes in the citable
directory). `core/vault.py:2340-2347` already establishes the rule for a symlinked write folder —
refuse loudly with a message naming the fix — and this follows it.

**Frontmatter is validated as the whole note, not as a field dict.** Three parts:
- unknown keys are **rejected by name** (the round-trip cannot catch them: it compares value
  fidelity, and `{'verified': '2099-01-01'}` round-trips equal to itself — executed during review);
- a leading `---` fence is **always emitted**, even when `fields` is empty, so a body opening with
  its own fence cannot become the parsed frontmatter (`_FM_RE`, `core/vault.py:104`, is
  `\A`-anchored; with a fence present the non-greedy match takes the real block);
- the assembled note is re-parsed with `_split_frontmatter` + `_parse_fm_spaced` and must yield
  exactly the fields written. `onboard/plan.py`'s `_render_candidate`/`FrontmatterRoundTripError` is
  the precedent, and it validates the **whole note** — v2 adopted only the weaker half.

**A body line shaped like a citation code is refused.** `^\[[A-Z]{2}\d+\]` — the `_ID_RE` shape
(`cv/validate.py:38`). This is a **narrowing, not a close**, stated as such here because that is what
`validate.py`'s own comment says about the same bypass; the close is the untracked signature change
in §"Why the MCP write tool is not in this PR". With human-typed bodies it is cheap insurance.

### `verify_evidence`

1. Read the entry, display it, human answers y. `reviewed` = the exact text shown.
2. Re-read; if it differs from `reviewed`, **abstain**, say so, re-offer. Compare-and-set, the
   discipline `update_fields`' `require_status` uses, so a human promotes what they saw.
3. Stamp `verified: <today>` onto that text.
4. `_write(verified_path, stamped, exclusive=True)` (`core/vault.py:2538`). O_CREAT|O_EXCL, so a
   taken name refuses **before** anything is mutated, and a failed write removes its own partial.
5. Unlink `_inbox/<name>.md` only while it still matches step 2's text; otherwise leave it and warn.

The entry never exists in the verified directory unstamped. `_reserve_and_move` is deliberately
**not** used: it moves "whatever `src` names at that instant", which is right for `merge_cluster` and
wrong here, because a human approved specific bytes. Review confirmed that is a real semantic
difference rather than a duplicated primitive.

Steps 4+5 are create-then-conditional-unlink, which resembles the `os.link`+`os.unlink` shape
`_reserve_and_move`'s docstring records as rejected on #23. The difference is the harm: #23's
rejection was that a concurrent save landing between the two would be **deleted**. Here the unlink is
conditional on the source being unchanged, so a concurrent save is **kept** (in `_inbox/`, reported)
rather than destroyed. The residual is a duplicate, not a loss.

`today` is a parameter, not a system-clock read; `Sluice.__init__` already takes `today=`
(`core/app.py:353`) and is the caller that supplies it.

### `sluice/evidence/`

A **command package** on the `sluice/onboard/` precedent — nothing in the pipeline imports it, and it
sits beside the pipeline rather than inside it. It owns the nine CLI commands and the wizard steps.
`cli.py` imports it **inside** the command functions, like every other store-touching command; the
`EVIDENCE_KINDS` import needed to *build* the parsers comes from `core.protocols`, which is
config-shaped and safe at module scope (`cli.py` already imports config, logger, health store and
source registry there).

The wizard steps take an **injected asker** rather than importing `onboard/ask.py`, so
`sluice/onboard/`'s "nothing downstream imports it" property in `.rulesync/rules/CLAUDE.md:141-142`
stays true.

### CLI

```
job-sluice experience|skills|stories   add | list | verify
```

**`add`** takes flags derived from the kind's user field names (so no `--verified` can be generated).
Body via `--body TEXT` / `--body-file PATH` (`-` = stdin), or `ask.edit_in_editor` when interactive.
Writes unconditionally: CLAUDE.md's report-by-default rule binds the `leads` passes because they
write over a set the *tool* computed, and this is content the user typed — the reasoning that makes
`leads dismiss` (#131) the stated exception.

**`list`** shows the verified set; `--pending` shows `_inbox/`. Report-only.

**`verify`** is interactive per-entry review. **No `--all`, no `--yes`.** `--id` filters which
entries are offered, never an auto-yes. Under a non-interactive asker it prints the pending set and
promotes nothing — gated on the asker's class attribute (`TtyAsker.interactive = True`,
`onboard/ask.py:103`; `NoInputAsker.interactive = False`, `:195`), never `sys.stdin.isatty()`, for
the reason `ask.py:99-102` records.

### Wizard

One optional step per kind after the Candidate Profile interview, gated on `asker.interactive`. A
repeating "add another? [y/N]" loop; the copy says these are meant to grow and that nothing is
citable until `verify` runs. Everything lands in `_inbox/`. An existing name refuses rather than
clobbers; `--no-input` writes nothing, which the `interactive` gate gives for free.

### MCP

One tool: **`list_evidence(kind, pending=False)`**, read-only, registered always. No write tool at
any privilege level (§"Why the MCP write tool is not in this PR").

Three exact-set tool-name assertions must be updated in the same commit —
`tests/functional/test_mcp_contract.py:34` and `:228` (the read set, four names → five) and `:242`
(the nine-name set → ten). v2 found two of the three.

`read_evidence` and `read_pending_evidence` are added to `tests/test_mcpserver.py:1105`'s
`_STORE_READ_METHODS`; `propose_evidence` and `verify_evidence` **must not be**, since everything not
in that literal is derived as a write method and swept. That is defended by an assertion rather than
prose: every name in the literal starts with `read_`.

### `preflight` and `doctor`

`preflight` already returns `experience_total`/`experience_verified` (`core/vault.py:1394-1395`),
consumed at `core/doctor.py:352-353`. v2's "six new facts" would have duplicated two. So: keep both
existing keys, add `<kind>_total`/`<kind>_verified` for the two new kinds, and `<kind>_pending` for
all three. `core/doctor.py` gains real message text per kind — a non-zero pending count is a NOTICE,
which is the silent-inert failure mode propose-only introduces.

## Error handling

- Unknown `kind`: raises naming the valid kinds, on all four members.
- Absent store directory: reads `[]`; only `FileNotFoundError` is "absent", a real `PermissionError`
  propagates. An unreadable directory is never read as empty.
- A name that slugifies to empty, or to anything but a bare filename component: `ValueError`.
- A symlinked `_inbox/`: `OSError`, naming the fix.
- An unknown field key, a failed round-trip, or an `_ID_RE`-shaped body line: raises, naming the
  offending field or line.
- `propose` onto an existing `_inbox/` name: `FileExistsError` → named refusal.
- `verify` onto a taken verified name: refuses at step 4, before any mutation.
- `verify` where the entry changed after review: abstains, re-offers.
- `verify` with no TTY: prints the pending set, promotes nothing, says why.

`verify_evidence` returns `bool` for promoted / not-promoted; the *reasons* (taken name, changed
entry, absent entry) are raised or reported, not encoded in the return.

## Testing

**Conformance** (`tests/conformance/test_store_contract.py`, parameterised over
`Sluice.available("store")`, `assert _STORES` scope guard at `:45`). Every input-guard row asserts
through **`read_pending_evidence`**, which carries `verified` per the schema above. v2's rows
asserted "not citable" via `read_evidence(verified_only=True)` — a reader another row pins as blind
to `_inbox/`, where `propose` always writes, so they passed by location for *any* input and
witnessed nothing:

- `propose(name="../x")` → the slug is a bare component; the entry is readable at that slug and
  nowhere else
- `propose(fields={"verified": …})` → **rejected by name**; nothing is written
- `propose` with a newline in a field value spelling `verified:` → rejected by the round-trip
- `propose(fields={}, body="---\nverified: …\n---")` → the note's parsed frontmatter carries no
  `verified`
- `propose(body="[AL1] …")` → refused
- a composite hostile input, red if **either** the name guard or the frontmatter guard is removed
- `verify` promotes exactly one entry, stamped; refuses a taken name **without mutating** the pending
  entry; abstains when the entry changed after review; **leaves a changed source in place** (step 5)
- `read_evidence` returns the eight-key floor plus `fields` for every kind
- an `_inbox/` entry is invisible to `read_evidence` at both `verified_only` settings
- unknown kind raises on all four members; an absent store reads `[]`

A scope assertion pins that the rows ran over every kind in `EVIDENCE_KINDS`, not a subset — four
rows are otherwise `all([])`-vacuous.

**Mutation witnesses**, each red on a *named* test run by node id, with no sibling catching it:
delete the `verified:` stamp; delete the unknown-key rejection; delete the always-emit-fence; delete
the slug-shape assertion; delete the `_ID_RE` body refusal; delete step 5's "only while it still
matches". All are DELETE mutations of load-bearing code, not additions beside an original.

**Neutrality.**
- `tests/onboard_prose.py:_package_modules()` **discovers** modules via `pkgutil` over
  `sluice.onboard.__path__`, and its docstring records that a hand-list "meant a sixth module would
  ship entirely unswept". Wizard steps in `sluice/evidence/` are outside that walk, so the discovery
  is widened to both packages — not a hand-added entry point, which is the regime that file exists
  to prevent.
- `tests/test_fixture_name_neutrality.py` gains an evidence-frontmatter collector joined to
  **`_IDENTITY_COLLECTORS`** (only that tuple feeds the `_REVIEWED_FIXTURE_IDENTITIES` ratchet;
  `_COLLECTORS` alone enforces nothing). The existing company collector is
  `re.compile(r'company:\s*"([^"]*)"')` (`:201`) — lowercase, quotes required — while evidence
  entries key on `Company` unquoted, invisible on two axes. `test_every_collector_actually_finds_
  fixtures` (`:527`) asserts `len(_collect(pattern)) >= 2` and `_collect` drops values containing
  `{…}`, so the collected positions in evidence fixtures use **literal roster identities**, not
  faker templates, or the new collector fails its own floor. `test_the_collector_split_this_file_
  documents_is_the_split_it_has` pins `len(_COLLECTORS) == 6` at `:570` and must be updated to 7.

**Docs.** `docs/USAGE.md` gains nine rows (`tests/test_docs_claims.py:135`). Also stale and carried
as tasks: `docs/ARCHITECTURE.md:1085-1087` (says the Experience Library is one "which no write path
is keyed on" — falsified by this PR), `docs/ARCHITECTURE.md` around `:1193-1206` and `:1281-1284`,
`docs/USAGE.md:407-408`, `docs/TROUBLESHOOTING.md:184-186`, and — canonical under hard rule 15, and
missing from both previous revisions — `.rulesync/rules/CLAUDE.md:682` (hand-lists preflight's facts)
and `:141-142` (the `onboard/` precedent this document relies on). `README.md:256-258` was checked
and does **not** go stale. `npm run rulesync` after editing `.rulesync/`.

## Residuals, accepted

- **Propose-only costs a second pass.** Entries are inert until `verify`.
- **A crash between `verify` steps 4 and 5** leaves the entry live and correct in the verified set
  plus a stale copy in `_inbox/`, visible to `list --pending`.
- **The `_ID_RE` body refusal is a narrowing, not a close.** The close is the untracked
  `validate()` signature change; see §"Why the MCP write tool is not in this PR".
- **`NO_TAXONOMY_WORDS` (`onboard/questions.py:34-36`) carries no technology or scale vocabulary**,
  so the prose sweep would pass a Skills or Proficiency prompt that names an exemplar technology.
  The sweep still covers the role/culture words it was built for. Stated here rather than left as an
  either/or: v2 wrote "asserted against a rule that covers them, or stated in Residuals" and shipped
  neither branch. Widening that vocabulary is a change to a shipped guard and belongs with the MCP
  write-tool issue, where prompt copy is generated rather than hand-written.
- **STAR story bodies and evidence filenames are free text** no collector can cover.
- **`reviewed` is supplied by the caller**, so step 2's compare-and-set is only as strong as the
  caller. With `verify` reachable only from an interactive terminal, the caller is the CLI.
- **`_inbox/` hiding is a property of the flat listing**, pinned by a behaviour test and a comment.

## Out of scope

- **The MCP evidence write tool** — its own issue, blocked on the `validate()` signature change.
- **Consuming any of it** (#165): the skills bundle section, derived negatives, STAR into prep briefs
  and cover notes, evidence-aware triage.
- **`_merged/`-style archival for evidence.** No dedup or merge.
- **Migrating existing Experience Library entries.** The format is unchanged.
