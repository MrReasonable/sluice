# Stop re-judging leads that stay `unjudgeable` (#402)

## Problem

`unjudgeable` is in `DEFAULT_TRIAGE_STATUSES`, so every `triage run` selects every lead at that
status again. Two producers write it, and they cost different things:

- **The pre-judge gate** (`triage/engine.py`, `dossier_cache.jd_arrived` false): the fetch
  produced no job description. No judge call is made, and `DossierCache.get_or_build` persists
  nothing (#169), so the next run fetches again. Cost per run: one browser fetch.
- **The judge** (a verdict of `unjudgeable`, #300): the fetch produced page chrome long enough to
  clear `min_jd_chars` (a bot check, a sign-in wall, a CV-upload page), so `jd_arrived` passed and
  the dossier was **cached for `ttl_days`**. Every later run inside that window hands the judge the
  same cached bytes and gets `unjudgeable` again. Cost per run: one paid judge call that cannot
  observe a block clearing, because nothing was fetched.

Neither repeat is a no-op on disk. Both writers tag their `relevance_notes` line
`[triage <date>]` and `update_fields` skips the append only when that tag is already present, so a
repeat on a later day appends one more line and lands as `applied` (the judge site also audits
it). Only a same-day rerun writes nothing. These leads therefore already rewrite their note and
grow `relevance_notes` by a line on every run, which the give-up rule also stops.

Measured on one install (a frozen snapshot of its triage data):
0 of 85 `unjudgeable` leads received a real verdict in about four weeks after #301 shipped.

## Decisions (owner delegated them, 2026-10-14)

1. **Give up after N consecutive `unjudgeable` results, counted per FETCH, not per day.** A day
   count measures the calendar, not evidence: with the cron off for two weeks, a lead judged once
   would give up on the first night back. To make each counted result a fresh fetch, a landed
   judge verdict of `unjudgeable` also drops that lead's cached dossier (§4), so the judge path
   refetches every run exactly as the pre-judge path already does. N in a row then means N
   separate fetches each came back as a non-posting.
2. **Eligible again when:** the note carries a pasted job description (#369's `## Job
   description` section), which is never skipped; a human deletes or edits the counter key (a
   blank or deleted key reads `0`, buying N more fetches); or a real verdict or classification
   lands, which clears the counter. Changing the status by hand is NOT a reset: a given-up lead
   moved back to `new` keeps its count, gets one more fetch and judgement, and gives up again if
   that is `unjudgeable` too. That is deliberate (one human nudge buys one retry); the key is the
   full reset.
   Rejected:
   - a new `last_seen` — ingest touches it on every sighting, so it would reset the counter
     every run and the rule would never fire;
   - a new url — never-clobber means ingest never rewrites a lead's url;
   - the dossier ttl — a skipped lead is never fetched, so there is no stale cache to wait out.
3. **The counter is a frontmatter key on the lead, `triage_unjudgeable_count`.** It travels with
   the note, it is visible in Obsidian, and deleting it is the reset. The audit log is not used:
   the pre-judge producer is never audited, a refused write writes no row, and deciding a skip
   from it would mean scanning the whole log on every run.
4. **The threshold is `triage.unjudgeable_give_up_after`, an int, default `0`.** `0` means the
   whole feature is OFF: no counter is incremented, no dossier is dropped, no lead is skipped. The
   one thing that still runs at `0` is §5's clear of a count LEFT OVER from a period the setting
   was on, and it touches only notes that already carry a count. An unconfigured install that
   never turned the setting on behaves exactly as before (empty-config-abstains). The cost, stated:
   turning the setting on starts every lead at zero, so the first skip comes N runs later.
   Considered and rejected: counting even at `0` so history is ready when the setting is turned
   on. With eviction gated (it must be, see §4), a count taken at `0` would count re-reads of
   cached bytes, which is not the per-fetch evidence the threshold is defined over.

## Design

### 1. Reading the counter: `triage/apply.py::unjudgeable_count`

One parser, beside `_stamp_blocked`. Both readers (`triage/apply.py` and `triage/engine.py`) are
in the triage sub-app, and nothing in `core/` reads the key, so it does not go in `core/leads.py`
(where `blocked_stamp` sits only because the `Sluice` facade reads that one):

```python
def unjudgeable_count(fm: dict) -> int | None:
    """0 when absent or blank, the integer for a bare non-negative integer, None otherwise."""
```

- Absent, or blank after `strip()`: `0`.
- `re.fullmatch(r"[0-9]+", raw.strip())`: that integer.
- Anything else is a person's own text and reads `None` ("unreadable"): a word, `-1`, `3.0`,
  `true`, a block scalar (which `note.fm` reads as its `|`/`>` indicator), any value containing
  a newline.
- A QUOTED integer (`"3"`, `'3'`) is a count: `core/vault.py::_fm_dict` strips the quotes before
  the parser sees the value, so it reads `3`, and the next increment writes an unquoted `4`. That
  is the reader every caller goes through, so the parser does not try to see past it.

A hand-typed block LIST reads blank in `note.fm` (the frontmatter reader takes the key's own
line), so it parses as `0`; §2 explains why that is safe. Unreadable never skips (§3) and is
never written over (§2).

### 2. Counting (both producers), only when the setting is on

`apply_classification` and `apply_verdict` take a keyword `count_unjudgeable: bool = False`
(default keeps every existing caller and test unchanged). The ENGINE computes it (`N > 0` and the
result was not decided on the note's own section), so the threshold lives in the engine only.
When it is true and the decision/verdict is
`unjudgeable`, the counter rides the SAME `update_fields` call that writes the status — so it can
only land where `_DECISION_REQUIRE`/`_VERDICT_REQUIRE` permit the write, i.e. from `new` or
`unjudgeable`, never on `research` (which keeps #302's `triage_blocked` stamp and gets no
counter, including through `_stamp_blocked`), and never on an application-owned lead.

- Snapshot reads an integer `k` (`unjudgeable_count` is not None, which includes blank and
  absent as `0`): `fields` gets `triage_unjudgeable_count: k+1`, and
  `require_unchanged["triage_unjudgeable_count"] = raw` is MERGED into the existing dict (beside
  `followup_asked`/`triage_blocked`) ALWAYS, blank snapshot included (changed in PR review:
  an earlier draft skipped the entry on a blank snapshot, which let a stale `1` replace a count
  a human typed into an absent key mid-judge). A refusal of that entry means only the COUNTER
  lost the race, so `triage/apply.py::_write_counted` retries the write once WITHOUT the
  counter's field and CAS entry: the status lands and the human's value stands. That covers a
  count reset or typed mid-judge, and a hand-typed block LIST in the key (blank in the
  snapshot, multi-line fresh), which would otherwise refuse the whole write on every run, a
  paid judge call each time with nothing written. Every other guard rides the retry, so a
  refusal for any other reason refuses again.
- Snapshot unreadable (`None`): the counter is not put in `fields` at all; the status still lands
  and the key is left byte-identical.
- `triage_unjudgeable_count` is ADDED to `_HAND_EDITABLE_KEYS`, as #302 added `triage_blocked`.
  Both writers keep passing `preserve_block_values=_HAND_EDITABLE_KEYS`; there is no second set,
  which would replace the framing keys' #329 protection.
- A write that does not land (refused, `VaultConflict`, `--dry-run`, which never calls the apply
  functions) counts nothing.
- **A result decided on the note's OWN job description counts nothing**, on both producers: the
  pre-judge gate's short-section `unjudgeable` (`i in from_section`) and a judge `unjudgeable` on
  a dossier built from the section. Neither involved a fetch, so counting them would break
  Decision 1's per-fetch meaning: a user who pastes a short section for N runs and then deletes
  it would see the lead given up with no fetch ever counted. The engine passes
  `count_unjudgeable=False` for such a lead.
- **How the verdict step knows a lead came from its section.** `_prefetch_dossiers` returns
  `from_section` as positions in `keeps`, but the verdict loop looks a lead up by `lead_id`
  through `note_by_id` and has no position. So while the dossiers are assembled (where
  `note_by_id` is filled), the engine also records each section-built dossier's `d["lead_id"]`
  into a set `section_ids`, and the verdict step tests `verdict["lead_id"] in section_ids`. The
  pre-gate step runs inside the `enumerate(keeps)` loop and tests `i in from_section` directly.
- It is the existing writer: no new CodeQL sink.

### 3. Skipping

`triage/engine.py::run` reads the threshold off the `TriageConfig` it is already handed
(`cfg.unjudgeable_give_up_after`), exactly as it reads `cfg.batch_size`; it gains no keyword, so
there is one source for the setting. (`dossier_concurrency` is a keyword only because it is a
ROOT key.) Call it `N` below. A lead is GIVEN UP when ALL of:

- `N > 0`;
- its status normalises to `unjudgeable`;
- `unjudgeable_count(note.fm)` is an int `>= N`;
- `jdsection.read(note.body)` (the reader `_prefetch_dossiers` uses; it returns
  `(text, count)`) yields no text. If that read raises, the lead is NOT given up: it continues
  to `_prefetch_dossiers`, whose own handling of an unreadable section (`_SectionUnreadable`)
  applies as before. An error errs toward judging.

Placement, both halves load-bearing:

- **Before `--limit`.** Directly after the `awaiting_reply` filter, the notes are split into
  given-up and the rest, and `--limit` applies to the rest only, so a given-up lead never uses
  up the run's budget. The given-up leads are appended back AFTER the limit cut, so they still
  reach the classify pass. `--limit` therefore caps the leads that may be FETCHED OR JUDGED,
  not the given-up leads' free classify pass; `docs/USAGE.md`'s `--limit` row and the
  `triage run` help text say so.
- **After classify, before `_prefetch_dossiers`.** The classify pass runs on a given-up lead like
  any other: it is free and deterministic, so a reject rule the user added later still dismisses
  it, and that landed classification clears the counter (§5). Only a given-up lead whose
  classification is `keep` is withheld from `keeps`: it is counted under `given_up` INSTEAD of
  `keep` (the `keep` increment in the classify pass is moved behind the withhold check), and is
  never fetched or judged. Counting it under `keep` too would make `cli.py`'s digest say a lead
  "passed the pre-gate" and, under `--no-llm`, that it is "still waiting" for a judge. A given-up lead is also kept out of the classify pass's
  company resolution (tier 2 visits the page, tier 3 spends a call), so `--limit`'s "fetched or
  judged" is exactly true; its `needs_review` classification still lands and clears the count.

`report.counts["given_up"]` joins `TriageReport`'s default counts dict (so the digest-coverage
test sees it) and is never added to `skipped` or `unjudgeable`. `cli.py`'s triage digest gets a
row in `_TRIAGE_FILTERED_WORDS` whose text carries the retry instruction: delete
`triage_unjudgeable_count` from the note, or paste the posting under `## Job description`. To see
WHICH leads gave up: `job-sluice leads list --status unjudgeable` plus the key in each note; no
new listing command.

The skip applies under `--dry-run` too (it reads, it writes nothing) and to an explicit
`--status unjudgeable`: the threshold is the user's configured verdict, and the reset is one key.

### 4. Dropping the cached dossier on a landed judge `unjudgeable`

New `DossierCache.forget(lead: dict) -> None`: removes `_path(lead)`. `FileNotFoundError` is
swallowed (already gone is the goal state); any other `OSError` propagates. The cache stays a
generic store: it knows nothing about verdicts.

The engine calls `dossier_cache.forget(note.fm)` — the note's frontmatter, the same dict
`_prefetch_dossiers` hands `get_or_build`, never the judge's dossier, whose `lead_id` has been
overwritten with the note's slug and would compute a path that does not exist (a silent no-op,
since a missing file is swallowed) — when ALL of: `N > 0`; the judge's verdict status is
`unjudgeable`; `apply_verdict` returned `applied`; the lead is not in `section_ids` (a section
dossier is never cached). A `--dry-run` never reaches it: the engine forces `outcome="skipped"`
and never calls `apply_verdict` there, so `applied` already excludes it and no separate
`dry_run` condition is written (it would be an equivalent mutant). An `OSError` from `forget` is caught per
lead and appended to `report.failures` (the verdict has already landed; one bad file must not
abort the batch). It is never silent: a dossier that stays cached makes the next count a re-read.

- A refused write (`research` -> `blocked`, or `unchanged`) does not forget: no count was taken.
- `cache_key` can be shared by two notes (`_prefetch_dossiers` groups by it). Forgetting the
  entry makes the twin refetch on its next run; it never feeds the twin wrong data.
- `cv run` reads the same cache. An `unjudgeable` lead is not composed. A `cv run` (or a second
  triage process) racing the delete could see `_fresh(path)` true and then fail to open the
  file, so `get_or_build` treats a `FileNotFoundError` on that read as a miss and fetches.

This applies #169's rule (do not keep a fetch that produced no job description) with the
judge's verdict as the test. `jd_arrived` still owns what is PERSISTED; the engine may EVICT.

### 5. Clearing

`apply_verdict` and `apply_classification` clear the counter on a write whose status is not
`unjudgeable`, ONLY when `unjudgeable_count(note.fm)` is an int `> 0`: `fields` gets
`triage_unjudgeable_count: ""` and `require_unchanged` gets the raw snapshot, merged into the same
dicts that clear `followup_asked`/`triage_blocked`. A blank written by the clear reads `0`, so a
cleared lead counts again from zero. A lead that never had the key gets none; an unreadable value
is left alone. Clearing is NOT gated on the setting: a count left from a period when the setting
was on must not survive a real verdict.

The clear's `require_unchanged` is kept, matching `triage_blocked`'s: a stale clear never
blanks a value the human just typed. When it refuses, `_write_counted`'s retry lands the verdict
without the clear, so no judge call is discarded; the count stays on a lead that is no longer
`unjudgeable`, where it is inert. `leads dismiss` and
`leads expire` do not clear it; a dismissed lead is not selected, so a leftover count there is
inert. (The reverdict pass, #223, writes no lead itself; its re-verdict lands through
`apply_classification` and clears like any other classification.)

### 6. Config

`TriageConfig.unjudgeable_give_up_after: int = 0`. `load_triage_config` is a `hasattr`-filtered
`setattr` loop whose only type check fires for bool-defaulted fields, so the validator runs AFTER
that loop, unconditionally, like the `company_resolve_llm` check: reject `bool` first (PyYAML
reads `yes`/`on`/`true` as `True`, a subclass of `int`), then require `type(v) is int` and
`v >= 0`, naming the key in the `ValueError`. The loop is not changed to name the field.
`sluice.yaml.example` ships the key COMMENTED, with the "set YOUR OWN" marker the other opt-in
int knobs use, saying what it does and that `0` means never.

### 7. Residual, stated

A block that clears AFTER a lead has given up is never observed, because a given-up lead is never
fetched again. Accepted on the measurement above (0 of 85 healed); a periodic retry was considered
and rejected as cost with no observed benefit.

A second residual: the FIRST count after the setting is turned on can be taken on a dossier cached
before it was on (eviction only starts with the setting). Every later count is a fresh fetch, so
at any N the lead gets at least N-1 fresh fetches before it gives up.

## Out of scope

- The `research` backlog (#302, shipped). A `research` lead blocked by #302 still re-reads its
  cached dossier nightly; unchanged here.
- Detecting page chrome before the judge (a smarter `jd_arrived`).

## Testing

New `tests/test_triage_give_up.py`, structured like `tests/test_triage_blocked.py`, driven through
the real `Vault` and `engine.run`, offline, synthetic notes whose titles come from the seeded `titles`/`cfg_titles` fixtures (as
`tests/test_triage_blocked.py::_engine_run` does) and whose company, where a rule must match it, is
one obviously synthetic value shared by the note and the rule. Leads in a multi-lead row are given
filenames whose sort order is the order the row needs, because `read_leads` sorts by path.

- **Parser, through a real note** (`read_leads`, never a hand-built dict, so the row sees what
  `_fm_dict` hands production): absent, blank, `0`, `3`, a quoted `"3"` and `'3'` (both read
  `3`), a block list (reads blank, so `0`), and the unreadable set (`five`, `-1`, `3.0`, `true`,
  a block scalar).
- **Off at 0:** with `unjudgeable_give_up_after=0`, a lead at count 99 is judged; an
  `unjudgeable` verdict increments nothing; a pre-seeded cached dossier is not removed.
- **Counting, both producers:** a pre-judge-gate `unjudgeable` and a judge `unjudgeable` each take
  `new`->1 and 1->2.
- **Section results count nothing:** a short pasted section (pre-gate) and a judge `unjudgeable`
  on a section-built dossier each leave the count unchanged. Two witnesses, one per call site:
  delete the pre-gate's `from_section` test (its row goes red); delete the verdict step's
  `section_ids` test (its row goes red).
- **Refused writes count nothing:** a lead moved out of `new`/`unjudgeable` mid-judge; a
  `research` lead judged `unjudgeable` gets `triage_blocked` and no counter; `--dry-run` writes no
  counter.
- **Mid-judge reset is not restored** (deterministic, after
  `test_triage_blocked.py::test_a_restamp_mid_judge_refuses_the_verdict`): snapshot at count 2,
  then the human sets the key to blank via `update_fields`, then `apply_verdict` on the stale
  note: the counter stays blank. Same through `apply_classification`. Witness: delete the
  counter's `require_unchanged` entry; this row goes red.
- **Clear CAS:** snapshot at 3, the human sets 5, then a `shortlist` verdict on the stale note
  lands and the count stays 5. Witness: delete the clear's `require_unchanged`
  entry; the row goes red (the stale clear blanks the 5).
- **Hand-typed values:** a `new` lead with a block-list counter: the counting write abstains
  (`unchanged`) and the note is byte-identical. A count typed into an absent key mid-judge
  stands. Each single-line unreadable value: status lands, key byte-identical, and the lead is
  judged rather than given up.
- **Hand-editable protection:** a hand-typed block list under `triage_next` survives an
  `unjudgeable` verdict that counts (guards the `_HAND_EDITABLE_KEYS` merge).
- **Skip:** two leads, cold cache, a recording fetcher (as `tests/test_triage_engine.py` does) and
  a recording backend. The given-up lead (count N) is never fetched and its slug is in no backend
  prompt; the control (count N-1) IS fetched and judged; `given_up == 1` and `keep == 1`. Rows for
  each predicate condition on its own: a `new` lead at count >= N is judged; a pasted-JD lead at
  count >= N is judged; count N-1 is judged; setting `0` is judged. A section whose read raises is
  not given up.
- **Where the skip applies:** under `--dry-run` the given-up lead is still not fetched and
  `given_up == 1`; under an explicit `statuses={"unjudgeable"}` it is still skipped.
- **Limit:** with `limit=1` and two given-up leads plus two others, exactly one other lead is
  fetched/judged and both given-up leads are classified.
- **Placement:** `limit=1` with the given-up lead sorting first still judges the other lead. A
  given-up lead matching a `reject_companies` rule is dismissed and its counter cleared. Mutation
  witnesses: move the split below `if limit:`; move the withhold below `_prefetch_dossiers`;
  move it above classify. Each goes red.
- **Clearing:** a real verdict on count 3 clears it; a real verdict on a lead with no key adds no
  key (after `test_a_landed_verdict_on_an_unstamped_lead_adds_no_key`); counter and
  `triage_blocked` clear together; an unreadable value survives a real verdict byte-identical.
  With the setting at `0`: a real verdict clears a leftover count, and so does a
  `reject_companies` classification (witness: gate the clear on the setting; both go red). Clear
  CAS through classification too: snapshot 3, human sets 5, a reject classification on the stale
  note lands and the count stays 5 (after
  `test_triage_blocked.py::test_a_restamp_mid_classify_refuses_the_classification`).
- **forget**, every condition with a row that can go red, each using a PRE-SEEDED cache file at the
  lead's own key so a deletion is visible:
  - through `engine.run` twice at N=3, COLD cache, the fake fetcher returning a job description
    long enough to pass `jd_arrived`: a judge-`unjudgeable` lead's fetcher count goes 1 -> 2
    while a `shortlist`-verdict control's stays 1. Witness: pass the judge's dossier to `forget`
    instead of `note.fm`; the row goes red.
  - a refused `research` lead judged `unjudgeable` keeps its file (witness: delete the
    `applied` condition);
  - a pasted-JD lead keeps a pre-seeded file at its key (witness: delete the `from_section`
    condition);
  - setting `0` keeps the file (witness: delete the `N > 0` condition); `--dry-run` keeps the
    file (a behaviour row, not a witness: no separate condition exists, see §4);
  - a twin sharing `cache_key` refetches;
  - `forget` on a missing file does not raise; a non-ENOENT `OSError` lands in `report.failures`
    and the run continues;
  - `get_or_build` treats a file removed between `_fresh` and the read as a miss and fetches.
- **Config:** in `tests/test_sluice_neutral_defaults.py`, beside the `min_jd_chars` block:
  `TriageConfig().unjudgeable_give_up_after == 0`, the loader with no file gives `0`, an absent
  key abstains, a configured `3` round-trips, and `test_example_config_ships_unjudgeable_give_up_after_off`
  (after `test_example_config_ships_min_jd_chars_off`); rejection parametrised over `yes`, `on`,
  `true`, `True`, `-1`, `2.5`, `"3"` (a YAML string). Witnesses: change the dataclass default to
  `3`; uncomment the example line. Each goes red.
- **Digest:** `given_up` is in `TriageReport().counts`; `tests/test_triage_record.py::_COUNTS`
  gains the key; a `cmd_triage_run` test asserts the count and the retry instruction text, and a
  `--no-llm` digest with one given-up lead does not report it as waiting for a judge.
- **Docs guards:** `tests/test_config_example.py`'s example/field binding and the
  CONFIGURATION.md row parity tests must pass with the new key.

## Docs

- `sluice.yaml.example` (commented, §6) and `docs/CONFIGURATION.md` (a row in the triage table).
- `docs/ARCHITECTURE.md`: the triage section; the `status.py` bullet, which says `unjudgeable`
  stays in the default selection because that is the retry (now: until
  `triage.unjudgeable_give_up_after` fetches); and the `dossier.py` bullet, which calls
  `jd_arrived` the single owner of what the cache keeps (now: it owns what is persisted, and the
  triage engine may evict through `forget`). The comment in `core/dossier.py::DossierCache.get_or_build`
  gets the same correction.
- `docs/TROUBLESHOOTING.md`: how to retry a given-up lead.
- `docs/USAGE.md`: the triage section's "a default `triage run` re-judges `new`, `research` and
  `unjudgeable` leads" (now: until they give up), the `given_up` digest line, and the `--limit`
  row (§3).
