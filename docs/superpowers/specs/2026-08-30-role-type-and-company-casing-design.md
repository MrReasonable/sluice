# `role_type` provenance and company-name casing — design (#223, #205, #212, #216)

Status: proposed, **after two `/review-plan` rounds** — round 1: 54 findings (1 Critical, 26 High);
round 2: 43 findings (2 Critical, 18 High). **Both of round 2's Criticals and five of its Highs were
defects in round 1's own fixes**, which is this repo's standing pattern and is why each correction
below is stated with the measurement that forced it. Reviewing stops here: the residual uncertainty
is in mechanisms that are cheaper to settle by writing code and tests than by a third pass over
prose. Delivery is three PRs (§6); each gets its own implementation plan and its own review.

Issues: **#223** (`role_type` records which search found a lead, not what the posting says),
**#205** (board-verbatim company casing creates case-variant duplicates and wedges replication),
**#212** (an unconfigured source silently runs its shipped example search) and **#216** (one
documentation sentence; §6).

**Every claim about current behaviour here was produced by running the shipped code.** Both rounds
caught places where that was untrue — §7 keeps them.

---

## 1. The problem

### 1.1 #223: two harms, in opposite directions

`ingest/base.py:_row_to_lead` sets `Lead.job_type` from the source's `extra` and the search's
`params`; `core/vault.py:_render_new` writes it as `role_type`. Nothing reads the posting.
`triage/classify.py` branches the pay floor on it.

Measured against the real `classify()`, `perm_floor_gbp=80000`, `contract_floor_gbp_day=500`
(illustrative probe parameters chosen for this measurement, not the maintainer's own configuration):

| `role_type` | `salary` | verdict |
|---|---|---|
| `permanent` | `£45,000` | reject — `Salary below floor: 45000 < 80000` |
| `contract` *(same job, contract-tagged search)* | `£45,000` | **keep** |
| `""` *(search set nothing)* | `£45,000` | reject |
| `contract` *(true)* | `£1,200 per diem` | keep |
| `perm` *(same job, perm-tagged search)* | `£1,200 per diem` | **reject** — `1200 < 80000` |

- **A false KEEP.** An assumed `contract` switches `perm_floor_gbp` off. Six shipped sources declare
  `extra={"job_type": "contract"}` (`cwjobs`, `indeed`, `jobserve`, `linkedin`, `reed`,
  `totaljobs`) — most of the fleet.
- **A false REJECT.** An assumed `perm` on a genuine day rate is judged against the annual floor.
  `apply_classification` writes `dismiss`, and `dismiss` is not in
  `_status.DEFAULT_TRIAGE_STATUSES`, so it is never re-selected.

Row 3 decides the fix: **blank already behaves correctly.** A wrong value is worse than no value.

### 1.2 #223: the label compensates for a gap in the day-rate marker set

`classify.py` recognises a day rate by `"/day"` or `"per day"` only. Measured, same floors,
`role_type` blank: `£1,200 per diem`, `£1,200 p/d`, `£1,200 pd`, `£1,200 daily` and `£1,200 a day`
all **reject**; `£1,200/day` keeps.

**`£65 per hour` and `£250 per week` do NOT belong in that list, and an earlier draft of this
section put them there.** Measured, they **keep** today: both are under `_MIN_CREDIBLE_SALARY =
1000`, so the annual branch abstains rather than rejecting. They are a different problem with the
opposite sign — see §2.3 and §9.

So provenance alone is insufficient: a genuine contract lead found by a *correctly* tagged search
survives today on the label, and demoting the label without settling the pay basis flips it to
`reject`.

### 1.3 #223: `role_type`'s stored vocabulary is not a closed set

`Lead.job_type` declares `"contract" | "permanent" | ""`. **No shipped source writes `permanent`**:
`grep -rn '"permanent"' sluice/` returns exactly 1 hit, the annotation itself. (Unquoted,
`grep -rn permanent sluice/` returns 52 — ordinary prose. The quoted form is the claim.) Ten
declarations exist: six `contract`, four `perm` (`escape_city`, `wellfound`, `wttj` via `extra`;
`google` on its example search; `escape_city` is `enabled=False`, so three are live). Nothing
coerces on write, so a vault holds mixed spellings and casings, and `classify.py` substring-tests a
lowercased copy — `Permanent` passes, `Contract-to-perm` matches the contract branch.

### 1.4 #223: a job type has THREE origins, and only one is the tool guessing

Round 1 found the first of these (rev-001); round 2 found the third (arc-r2-004).

1. **A scraped row** — `_row_to_lead` applies `{**(extra or {}), **(search.params or {})}` and
   `setattr`s each key (`ingest/base.py:_row_to_lead`'s merge). The source's `extra` is the
   tool's guess.
2. **A user's own config.** `sluice.yaml.example:9-10` documents a search's `params` as "where your
   personal search list lives"; `:109-110` shows `{job_type: perm}` there. That is the user's
   assertion. But a SHIPPED EXAMPLE search carries `params` too (`google`'s does), so `params`
   alone does not prove authorship — only whether the SEARCH was user-configured does, and
   `ingest/base.py:searches_for` erases exactly that by returning either list as a plain
   `list[Search]`. **That is #212, and #223 cannot be correct without it.**
3. **Manual creation.** `Sluice.create_lead` (`core/app.py:1722`, the MCP `create_lead` write tool)
   takes `job_type` as a parameter and builds `Lead(...)` by hand at `:1794`, calling `store.upsert`
   directly — never touching `_row_to_lead`. This is the case where the value is *unambiguously* the
   user's, and an earlier draft would have demoted it to a guess.

**The merge at `ingest/base.py:_row_to_lead` is lossy**: after it, nothing can tell which dict a
key came from. Provenance must therefore be computed BEFORE the merge, per key — never inferred
afterward.

### 1.5 #205: the duplicate is filesystem-dependent, and the issue does not say so

Three `upsert` calls differing only in company casing, real `Vault`:

| filesystem | outcomes | notes on disk |
|---|---|---|
| case-**insensitive** APFS | `created`, `updated`, `updated` | **1** |
| case-**sensitive** APFS | `created`, `created`, `created` | **3** |

`_locate` probes with a stat, so its answer is whatever the filesystem's comparison says. **The
store's identity semantics are filesystem-dependent — that is the root defect**, and it is a
Store-contract property. The duplicate is minted wherever ingest runs on a case-sensitive
filesystem; Syncthing carries the pair to a case-insensitive machine that cannot hold both, reports
`state=idle`, and never delivers one.

On the case-insensitive run the returned `UpsertResult.slug` is the lead's derived name while the
file keeps the first spelling (`vault.py:2913` sets `slug` from the path just seated). See §7.1 for
the consequence an earlier draft invented for that.

---

## 2. #223 — design

### 2.1 Provenance is explicit, stamped at each origin

`Lead` gains `job_type_source`, one of `"observed" | "declared" | "assumed" | ""`.
The note gains `role_type_source`.

| origin | provenance | why |
|---|---|---|
| `search.params` of a **user-configured** search | `declared` | the user's assertion |
| `Sluice.create_lead` (manual/MCP) | `declared` | the user typed it |
| `search.params` of a **shipped example** search | `assumed` | the tool's example |
| source `extra` | `assumed` | the tool's default |
| derived from the JD (§2.4) | `observed` | the posting says so |

**Computed per KEY and BEFORE the `{**extra, **params}` merge** (§1.4). `declared` requires BOTH
that `search.params` itself carries `job_type` AND that the search was user-configured — the flag
`#212` direction 1 adds to `Search`. Deriving it after the merge, or from the search alone, means a
user who configures `searches` with no `job_type` param inherits the source's shipped guess as
`declared`, reopening §1.1's false KEEP for six sources.

Stamped **after** `_row_to_lead`'s `setattr` loop so a source's own dict cannot set it, but
**decided** before the merge.

**A note with no `role_type_source` key reads as `assumed`.** Fail toward not trusting. This
re-verdicts an existing store: a configured `perm_floor_gbp` begins applying to leads it was
skipping, so the next `triage run` dismisses a batch it previously kept, and `dismiss` is not
re-selected. **Delivery requirement (§4 has the test):** the first run that would apply this must
PRINT the affected leads and write nothing unless re-invoked. `--dry-run` is not sufficient on its
own — it requires the user to know to use it. No backfill of existing notes (#223 scopes it out).

### 2.2 A closed set, normalised at every origin

`normalise_role_type(value) -> str` folds to `contract | permanent | ""`. Unrecognised input folds
to `""` **and warns**; never raises, matching `_safe_or_blank`'s per-item-isolation discipline.

An earlier draft claimed calling it from `_row_to_lead` made `Lead.job_type` "canonical for every
store". **False** — §1.4's third origin bypasses `_row_to_lead` entirely. It is called at **each**
of the three origins, plus on the READ side in `classify` for legacy notes. Because that is a
hand-list and hand-lists go stale, §4 carries a guard that enumerates `Lead(` constructions and
fails when one appears outside a normalising path.

Deliberately **not** in `Lead.__post_init__`: that runs *before* `_row_to_lead`'s `setattr` loop, so
it would never see the value the loop applies — the trap that docstring already names for the
`None`-coercion case.

### 2.3 The gate decides on evidence — and the basis set stays two-valued

`_pay_basis(salary, role_type, source) -> "day" | "annual" | None`:

1. **Day markers**, widened: `/day`, `per day`, `per diem`, `p/d`, `pd`, `a day`, `daily`,
   `day rate`. Matched with the `(?<!\w)…(?!\w)` idiom already in the file — bare `pd`/`pa` under
   plain containment would match inside ordinary words, which is #128's bug class through a new
   door.
2. **Annual markers**: `per annum`, `p.a.`, `pa`, `/year`, `per year`, `annually`.
3. **An `observed` or `declared` `role_type`, read from the note's persisted `role_type_source`
   key and NEVER re-derived from config** (§2.5 has the reasoning). An `assumed` one is not
   consulted.
4. **Otherwise `None`, falling through to the EXISTING annual branch** — byte-for-byte today's
   behaviour for an unmarked salary.

**Two things are deliberately absent, each for a measured reason.**

**No magnitude step.** An earlier draft proposed low/high thresholds with an abstaining band,
justified as "parsing facts of the same kind as `_MIN_CREDIBLE_DAY_RATE`". The analogy is false:
those constants appear only inside the reject conjunction, so they are **monotone** — they can only
turn a reject into an abstain. A step that SELECTS the basis is bidirectional:

```text
£300     annual-branch=keep     day-branch=reject   <-- NEW REJECT
£450     annual-branch=keep     day-branch=reject   <-- NEW REJECT
£1,200   annual-branch=reject   day-branch=keep
```

The day branch's reject window is `[_MIN_CREDIBLE_DAY_RATE, contract_floor_gbp_day)`, exactly where
small unmarked numbers land, so the **low** threshold manufactures rejects. (One reviewer proposed
keeping the low threshold *because* it "can only fail open"; the probe above is why that was not
taken. Right diagnosis, inverted fix.) The high threshold is inert. Both go.

**No hourly or weekly basis, and no `per hour`/`per week` markers.** A later draft added them "as
their own bases" — which the two-valued signature cannot represent, so they would route to `day`.
Measured, three reviewers independently:

```text
£65 per hour     today=keep    as-day=reject   <-- NEW REJECT
£250 per week    today=keep    as-day=reject   <-- NEW REJECT
```

No new number is added; two existing ones are **re-scoped**, because the applicable credibility
floor swaps from `_MIN_CREDIBLE_SALARY=1000` to `_MIN_CREDIBLE_DAY_RATE=50` and the reject window
moves down onto exactly those values. That is the same bidirectional harm the magnitude step was
deleted for, three paragraphs later, in the same section. Every realistic hourly rate sits below a
day floor, so this is systematic rather than an edge case. Real hourly/weekly support needs
per-basis floors or hours-per-day conversion constants — new shipped numbers, a separate decision.
**They stay unrecognised, and §9 names it as a residual.**

Consequence: §2.3 introduces **no new tunable numbers**, so no `*Config` field and no
`sluice.yaml.example` entry. The marker vocabularies are parsing facts — they encode how boards
SPELL pay, not which pay is desirable, a claim round 1 checked and accepted — and are named
module-level constants carrying a comment that they are English/UK-board idiom, so a non-UK board's
spellings are a visible gap rather than a silent misread.

### 2.4 Observation, and where it lives

A new pure module `sluice/core/roletype.py`: the closed set, `normalise_role_type`, and
`observe_role_type(jd_text)`. Judged justified rather than premature in both rounds — it has three
consumers, all in PR 2. **Its JD vocabulary is specified in the implementation plan, not deferred**;
an unspecified vocabulary is where a role taxonomy enters unreviewed.

`DossierCache.get_or_build` calls it on the fetched JD and stamps `observed`. **A JD carrying
evidence for both abstains.**

`triage/engine.py` writes back through `update_fields` with
`require_status=frozenset(_status.TRIAGE_OWNED)` — the set the three sibling triage writers use, and
the reason is the read→write gap spanning a JD fetch, during which the lead may enter the
application lifecycle. `update_fields` returns `False` for a refusal and for a no-op alike, so the
caller cannot distinguish them: the write is therefore **best-effort and unreported**, retried on
the next run. Stated because an earlier draft named `require_status` without saying what happens
when it refuses.

**Ordering limit, stated not closed:** classify runs at `triage/engine.py:118`, the dossier build at
`:291`. An observation cannot reach the gate on the run that fetches it, and a lead the gate
dismisses never reaches the dossier. §2.3 is what makes that acceptable.

### 2.5 Declared vs. observed: precedence

Neither round settled what happens when a `declared` value and a later `observed` one (§2.4)
disagree on the same lead, and §2.4's write-back cannot be planned without an answer.

**The ladder is `observed` > `declared` > `assumed` > `""`.** The posting is ground truth about what
the job IS; a `declared` value is the user's assertion about a SEARCH, and §1.1's premise is that a
search label is not a fact about a posting — a contract-tagged search can return a permanent role.
So a JD observation overrides an earlier declaration. `declared` still outranks `assumed`; nothing
here revises §2.1's table.

Two obligations on whoever implements this, and no more than two, because the rest cannot be
specified honestly in prose:

- **Read provenance from the persisted `role_type_source` key, never re-derive it from config.**
  §2.1 backfills nothing, so a note predating the feature carries no key and reads `assumed` even
  where its value did come from a user-configured search. Deciding `declared` by asking whether the
  source's search is configured TODAY would consult that legacy value and recreate §1.1's false KEEP.
- **A disagreement is surfaced, not silently overridden.** A user who declared `job_type: contract`
  and had it quietly overwritten has no way to learn their search's premise was wrong.

**Everything else about this is #223's to settle, against real code.** What a refused write-back
leaves in force, what governs on which run, how a disagreement is surfaced: each depends on
behaviour that does not exist yet, so specifying it here produces prose no test can check. An
earlier draft of this section did specify it, at length, and drew six consecutive rounds of review
correcting claims that were true of one case and written as though they covered all of them. That is
the argument for stopping at the ladder and the two obligations above.

---

## 3. #205 — design

### 3.1 Resolution first; renaming depends on it

`_locate` uses a **live directory listing to DISCOVER case-variant names, and keeps
`_is_note_file` to VALIDATE each match.** Both halves are load-bearing and an earlier draft dropped
the second:

- A directory named `<name>.md` is a name match under a listing and is rejected by `S_ISREG`.
  Measured: `listdir` sees it, `S_ISREG` is `False`.
- On an `r--` directory, `os.listdir` **succeeds** where `os.stat` raises `PermissionError` — the
  propagation `_is_note_file` exists for, so an unstatable path never reads as absent (absent is the
  branch that creates and that records `merged_away` in `seen.db`).
- **Existing guards cannot catch this regression.** They use mode `0o000` on the parent, where
  `listdir` and `stat` both raise. Measured, all three cases. A guard for this must use `r--`.

Live, not cached: a cached name index would break `upsert`'s create-race loop, which terminates only
because a re-resolve can see a note a concurrent writer created.

**Match resolution**, stated exhaustively because an earlier draft left the ordinary case undecided:

| casefold matches | exact matches | verdict |
|---|---|---|
| 0 | — | not found (create / archive probe) |
| 1 | 1 | that note |
| 1 | 0 | **that note** — the ordinary case after a §3.2 rename |
| >1 | 1 | the exact one |
| >1 | 0 or >1 | ambiguous → refuse |

The `1 casefold / 0 exact` row is every one of the 380 renamed notes. An earlier draft's "only a
cluster with no unique exact match is ambiguous" left it undecided, and the literal reading refuses
all of them permanently.

**`_archived_match` needs BOTH of its comparison sites folded.** `vault.py:886` builds
`re.compile(re.escape(name) + r"(?:\.\d+)?\.md\Z")` and `:888` `continue`s past a non-matching entry
**before** the `seated != name` decision at `:938`. Folding only the decision is inert, and that
function's docstring steers an implementer wrong by calling the pre-filter "never the decision".
**Two is the complete set** — `same_opportunity` compares url and location, never company. (Round 1
said three; round 2 corrected it.) #81 resurrection is the arm that must never fail open.

**Consequence:** on an ingest host holding wedged pairs, those leads begin *refusing* rather than
silently updating one twin. Honest, and it is what surfaces them.

### 3.2 Normalisation, deliberately timid

`normalise_company_case()` title-cases **only all-lowercase tokens**, keeps minor words lowercase
mid-string, and leaves any token carrying uppercase alone — acronym, CamelCase-brand and
dotted-brand shapes untouched. #205's measurement is the argument: naive `str.title()` would have
rewritten 1996 notes and corrupted 552 acronyms and 242 brand names, against 380 the timid rule
touches. The minor-word list is an English-orthography assumption and is a named constant saying so.

**This spends a documented guarantee, and it is in live code, not `CLAUDE.md`.**
`grep -c "zero migration" CLAUDE.md` returns **0**; the claim that candidate 1 is byte-identical to
the pre-#5 `_path_for` lives at `sluice/core/vault.py:761`. Both need updating and `vault.py:761` is
the load-bearing one.

Verified: the rule is a no-op over the entire golden corpus, so no fixture digest churns; and
`Lead.dedup_key`/`slug` already lowercase company, so `seen.db` does not re-key.

### 3.3 Repair belongs to TWO existing passes, not one new one

Round 1 found the hand-rolled survivor rule wrong. Round 2 found the *rebuild* still wrong: it
replaced the rule with `resolve_merge_status` but kept the automatic merge, and
`resolve_merge_status` was never the thing protecting `leads dedupe`. Measured:

```text
['applied', 'applied']      -> ('applied', 'ok')       one live application archived
['phone_screen', 'applied'] -> ('phone_screen', 'ok')  the `applied` twin archived
['shortlist', 'dismiss']    -> (None, 'conflict')
```

`pick_survivor` archives the loser into `_merged/`, `_walk` prunes it, and track stops tracking a
live application. The one mechanism that actually gates the shipped pass is **`dedupe_merge`'s
"nothing merges without an id"** (`cli.py`'s `leads dedupe --merge` id gate), which makes a human
name each cluster. **`flagged_losers` (`core/app.py:787-789`) is NOT a second gate** — it is
populated and printed and never consulted; see the correction below, which measures that. Both
rounds cited these two lines together as protections; that reading was wrong about one of them, and
this paragraph asserted it until CodeRabbit disproved it on PR #225.

`docs/ARCHITECTURE.md:1299` already settles the ownership: repairing a duplicate pair "belongs with
`job-sluice leads dedupe --merge` (or a hand rename)". The neighbouring `:1301` says `leads
reconcile` "REPORTS such a pair and declines to move either note … it must not pick a survivor" —
that sentence is about the LAYOUT pass, not the naming one, but `:1303` introduces `leads rename` as
"the file-*name* analogue of `leads reconcile`" on an orthogonal axis, so the same restraint applies
for the same reason. So:

- **`leads rename` owns the single-note case** — one note, wrong casing, no cluster. It needs a
  **case-only rename primitive**, because `_reserve_and_move`'s `O_CREAT|O_EXCL` refuses a
  case-only rename on the filesystem that has the problem. Measured:
  `case-insensitive → FileExistsError`, `case-sensitive → succeeded`. A two-step move through a
  temporary name, or an explicit same-inode check. It also needs a **second qualifier**, not a
  widened one: `_frontmatter_name` returns `(None, None)` unless the basename head is a placeholder
  (`vault.py:1018`), and that gate is what keeps automated renames off human-renamed notes.
- **Case-variant CLUSTERS are REPORTED, and routing them to `leads dedupe --merge ID` is NOT YET
  SAFE.** `flagged_losers` is a REPORT field, not a gate — it appears in exactly four places:
  `core/app.py:149` (the field), `core/app.py:792` (populated by `_dedupe_report`), `cli.py:681`
  (the JSON output) and `cli.py:686` (the `⚑losers` render) — and `dedupe_merge`
  (`core/app.py:929`) never reads it; the write path checks only `c.conflict` and proceeds. The id
  gate confirms WHICH CLUSTER a human named, not WHICH SURVIVOR the tool picks — that decision
  stays `pick_survivor`'s alone. And `resolve_merge_status` does not treat two live application
  statuses as a conflict: the rows measured above this bullet —
  `['applied', 'applied'] -> ('applied', 'ok')` and
  `['phone_screen', 'applied'] -> ('phone_screen', 'ok')` — both return `"ok"`, so `pick_survivor`
  picks one, `merge_cluster` archives the other into `_merged/`, `_walk` prunes it, and track
  silently stops tracking a live application. Today, a human reading the `⚑losers` marker before
  typing `--merge ID` is the only thing standing between a case-variant cluster and that outcome —
  nothing in the code enforces it.

  **PR 3 (#205) must close this before §3.3 ships**, by one of three shapes — named here, not
  chosen, since it is PR 3's own call once the case-only rename primitive above is designed:
  (a) refuse a cluster outright when any member is application-owned; (b) make `dedupe_merge`
  itself refuse whenever `c.flagged_losers` is non-empty, which is wider than (a) — it also
  catches a triage-owned loser carrying a `pending_cv`/`tailored_cv`/`needs_signoff` hold, not
  only an application-owned status; or (c) require an EXPLICIT survivor argument whenever a
  cluster has flagged losers, so a human's choice — not `pick_survivor`'s ranking — decides which
  note is archived. No new merge path and no new mover under any of the three, and the `O_EXCL`
  blocker does not arise for the cluster case either way.

---

## 4. Tests

**Two conformance assertions, each labelled with the filesystem it reddens on.** Measured on both:

| assertion | case-insensitive | case-sensitive (**CI**) |
|---|---|---|
| `res.slug in {n.slug for n in read_leads()}` | **red** | green |
| `len(read_leads()) == 1` | green | **red** |

All six `runs-on:` in `.github/workflows/ci.yml` are `ubuntu-latest`, so the SECOND carries the CI
leg and the first covers the developer's machine. An earlier draft proposed only the first, which is
green on every CI runner — reproducing the very inversion §4 opens by diagnosing. Neither alone is
sufficient; both go in, conjoined, and `tests/conformance/test_store_contract.py`'s docstring
forbids filesystem vocabulary, so neither carries a `skipif`.

- **The `assumed` sweep needs a constructible flip row.** Steps 1–2 (markers) run before step 3
  (provenance), so a row whose salary carries any marker can never flip. Measured, a flip row exists
  only with an UNMARKED salary — `£1,200`, `£45,000`, `£300`. Note all five existing contract rows
  in `tests/test_classify.py` use `/day`, so **step 3 has zero coverage today**. Scope assertion is
  "the sweep reached step 3", not mere non-emptiness: `all([])` is `True`.
- **The `Lead(` construction guard** (§2.2): enumerate `Lead(` constructions across `sluice/` and
  fail when one appears outside a normalising path. Derived, never hand-listed.
- **The mass re-verdict** (§2.1) needs a test that the first affected run PRINTS and writes nothing.
- **Three previously untested hazards**: both `_archived_match` sites; the `r--` directory case for
  §3.1's discover/validate split (`0o000` cannot see it); §2.4's `require_status` across the gap.
- **Fixtures are DERIVED** by re-casing an already-reviewed roster value — never invented, and
  never taken from the population §3.2's counts summarise. Those counts are quoted from public
  issue #205; the acronyms and brand names they COUNT live in the maintainer's vault, not in this
  repository, which is precisely why a fixture must never be sourced from them. Measured
  complication: all five `_IDENTITY_COLLECTORS` (`tests/test_fixture_name_neutrality.py`; the
  wider `_COLLECTORS` tuple that adds the equal-opportunities and candidate-identity collectors is
  seven) return `[]` for a bare `normalise_company_case` unit test, and 3 of the 4 shapes are absent
  from `_all_fixture_identities()`, so adding them to the
  roster alone **fails** `test_the_reviewed_roster_carries_no_identity_the_fixtures_stopped_using`.
  The fixtures must therefore be used from a collector-visible position. **Widening a #27 collector
  to make this pass is forbidden** — that is the guard-narrowing failure mode, inverted.
- **PR 1 ships its own tests** for `Search`'s configured flag. The `declared` vs `assumed`
  tests belong to PR 2 — `job_type_source` does not exist until then, so PR 1 cannot assert on
  it. (An earlier draft assigned both to PR 1.)
- The `_pay_basis` table asserts through the real `classify()`, not the helper alone.
- **§2.5's precedence needs both conflict directions** — `declared=permanent`/`observed=contract`
  and `declared=contract`/`observed=permanent` — each asserting the STORED `role_type`/
  `role_type_source` fields after the write-back AND `classify()`'s verdict on the re-derived
  value, not only one or the other. Belongs to PR 2, same as the `declared` vs `assumed` tests
  above, for the identical reason: `job_type_source` does not exist before it.

---

## 5. Out of scope

- Backfilling existing notes' `role_type` (#223 says so).
- Deriving `role_type` at ingest from the board row's text.
- #212 directions 2 and 3. **Only direction 1 is in scope.** What stays unsatisfied, and should be
  said rather than implied: an unconfigured source still runs a shipped role-and-city filter instead
  of abstaining, which is the one place this codebase inverts empty-config-abstains. Direction 3 is
  where that gets decided; the reasoning is recorded on the issue.
- Hourly and weekly pay bases (§2.3, §9).
- Building `fetch_mutates_remote` for #216. `CarouselSource` was retired at #217 with its last
  producer, and no shipped source performs a deliberate write-back: grepping `sluice/ingest/` for
  browser verbs (`click`, `type`, `fill`, `submit`, `press`, `select_option`) returns two hits, a
  modal dismiss in `cord.py` and a cookie accept in `workinstartups.py`, neither of which touches
  the user's account on the board. So the declaration would today be an interface for zero
  implementations. It does not follow that a fetch is invisible to the far side: `linkedin` and
  `wttj` browse INSIDE AN AUTHENTICATED SESSION, so a run is not an anonymous GET. What a board
  then records against that account is not something this repo measures, and the one surface where
  it WAS examined cuts the reassuring way -- `wttj.py`'s docstring reports that the matches page's
  only per-card actions are `Save` and `Not for me`, so reading it consumes nothing. That is the
  precise residual: unmeasured, not known-harmful. It is why §6's dry-run row bounds what sluice
  WRITES rather than promising nothing happens remotely.

### 5.1 Drift found while writing this spec, folded into the branch

`sluice/core/dossier.py:222` and `sluice/triage/engine.py:297` describe "the nightly
`--status new,research` run". `_status.DEFAULT_TRIAGE_STATUSES` has been
`("new", "research", "unjudgeable")` since #169. `docs/USAGE.md` is correct. Fixed by naming the
symbol, not by correcting the literal.

---

## 6. Delivery: three PRs, in this order

1. **#212 direction 1** (+ #216's sentence). `searches_for()` marks a `Search` as configured or
   example; the run summary and `ingest list-sources` surface it. Ships its own tests.
   Unblocks `declared`. #216 adds one sentence to `docs/USAGE.md`'s ingest `--dry-run` row: a dry run
   bounds what sluice WRITES, not what a run does to the far side, and `test-source` calls `fetch()`
   with no sink — mirroring how the triage row already names its billed call.
2. **#223.** §2 in full, resting on PR 1's flag.
3. **#205.** §3, once the case-only rename primitive and the second qualifier are designed. Its own
   `/review-plan` round before implementation.

---

## 7. Claims this document got wrong

Kept rather than deleted: a claim that was wrong once is what a reader of an earlier draft restates.

### 7.1 An invented consequence (round 1, arc-006)

"The sink records a slug naming a file that does not exist" — **false**.
`sluice/ingest/sink.py:48` uses `result.outcome` for the `seen.db` allowlist and never reads
`.slug`; the consumers are `cli.py` and `mcpserver.py`. The OBSERVATION was executed; the
CONSEQUENCE was inferred and asserted as measured, in a document claiming everything was executed.

### 7.2 Two misattributions (round 1)

- "`CLAUDE.md` guarantees candidate 1 is byte-identical" — `grep -c` returns 0; it is
  `sluice/core/vault.py:761`.
- "The two issues are file-disjoint apart from `core/leads.py`" — false; both touch
  `core/vault.py`. Superseded by §6.

### 7.3 Three fixes that were themselves defects (round 2)

- **`per hour`/`per week` as day markers** re-scoped two existing constants and manufactured the
  exact reject window the magnitude deletion removed (§2.3).
- **`resolve_merge_status` was never the protection.** Replacing the survivor rule while keeping the
  automatic merge carried round 1's Critical forward unchanged (§3.3).
- **`res.slug in read_leads()` is green on every CI runner** — the replacement for a vacuous guard
  was vacuous in the environment that gates merges (§4).

### 7.4 Three self-contradictions (round 2)

- §1.2 listed `£65 per hour`/`£250 per week` among spellings that "all reject". They keep.
- §2.1 cited a §4 delivery requirement that did not exist; `grep -i report` over §4 found nothing.
- §1.3's "only occurrence of the string" needed the quoted form: 1 hit for `'"permanent"'`, 52 for
  `permanent`.
- §2.2 claimed `_row_to_lead` canonicalises for every store; `Sluice.create_lead` bypasses it.

### 7.5 The "already protected" claim was wrong a second time (CodeRabbit cloud review, PR #225)

§3.3's closing bullet claimed `leads dedupe --merge ID` "already has `flagged_losers`, the id
gate, `resolve_merge_status`'s conflict refusal and `pick_survivor`'s precondition" protecting a
case-variant cluster merge. **False, the same shape §7.3 already records once for this exact
section**: `flagged_losers` is populated and printed (`core/app.py:792`, `cli.py:681,686`) but
never consulted by `dedupe_merge` (`core/app.py:929`), which checks only `c.conflict`. The id gate
confirms which cluster a human named, not which survivor is chosen. §3.3 now states plainly that
routing a case-variant cluster there is not yet safe and names PR 3's three closing options
instead of asserting a protection that does not exist. Also §2 gained §2.5 in the same round: the
document specified `role_type`'s provenance origins but never said which wins when a `declared`
value and a later `observed` one disagree on the same lead.

---

## 8. Reproducing the measurements

`classify()` measurements need no fixtures: build a `TriageConfig`, set the two floors, call it with
a plain dict. Vault, `O_EXCL` and conformance measurements need a case-sensitive filesystem, which
macOS does not provide by default:

```bash
hdiutil create -size 20m -fs "Case-sensitive APFS" -volname CS -quiet cs.dmg
hdiutil attach cs.dmg -nobrowse -quiet          # mounts at /Volumes/CS
# ... run the probe against a temp dir inside /Volumes/CS, then against an ordinary one ...
hdiutil detach /Volumes/CS -quiet
```

The `r--` measurement in §3.1 needs `os.chmod(dir, 0o400)` — **not** `0o000`, where both `listdir`
and `stat` raise and the distinction disappears.

---

## 9. Accepted residuals

- **Hourly and weekly pay are unrecognised** (§2.3) -- and unrecognised is NOT abstained, which is
  what an earlier draft of this line claimed. The basis is never parsed: neither matches the contract
  branch (`"contract" in role_type`, `/day`, `per day`), so both fall through to the ANNUAL branch
  and the bare number is compared against `perm_floor_gbp`. What decides the outcome is therefore
  MAGNITUDE, not basis. Executed against `classify` with `perm_floor_gbp=80000`:

  ```text
  £65 per hour      ->  keep     (65 < _MIN_CREDIBLE_SALARY, so the floor is skipped)
  £250 per week     ->  keep     (§2.3's measured value)
  £999 per week     ->  keep
  £1,000 per week   ->  reject   Salary below floor: 1000 < 80000
  £2,000 per week   ->  reject   binning a ~£104k role
  £1,500 per hour   ->  reject
  ```

  So §2.3's `£65 per hour`/`£250 per week` keep because they sit under `_MIN_CREDIBLE_SALARY`
  (1000), not because anything abstains on their behalf; every realistic WEEKLY figure clears that
  guard and is then judged against an ANNUAL floor it cannot meet. Real support needs new shipped
  constants; until then this is a live silent-rejection risk, not a benign gap.
- **A bare unmarked amount is judged as annual** (§2.3). Today's behaviour, deliberately unchanged
  rather than improved by a guess.
- **`role_type` is correct one run late** (§2.4). The gate still DEPENDS on it: with
  `contract_floor_gbp_day=400` and `perm_floor_gbp=80000`, flipping `role_type` between `contract`
  and `""` flips the verdict in BOTH directions -- `£300` rejects as contract and keeps as annual,
  `£2,000` keeps as contract and rejects as annual. What #223 changes is that the gate stops relying
  on an ASSUMED value; a persisted `observed` or `declared` one stays load-bearing.
- **A lead the gate dismisses never gets an observed `role_type`.** Reversible by hand.
- **§2.4's write-back is best-effort and unreported** — `update_fields` cannot distinguish a refusal
  from a no-op. Retried next run.
- **Ingest refuses on wedged pairs until repair runs** (§3.1). Loud rather than silent.
- **A declared/observed disagreement's surfacing mechanism is unspecified** (§2.5) — only that the
  write-back must not be silent about the CONFLICT case is required here; whether that is a note
  field, a run-summary line or a log record is PR 2's decision.
- **An all-caps company keeps its casing** (§3.2). Cosmetic; the alternative corrupts acronyms at a
  measured 552-to-380 ratio.
