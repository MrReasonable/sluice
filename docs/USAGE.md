# Usage

Full command reference for the `job-sluice` CLI: every command, every flag, what stream it
writes to, and what its exit code means. `docs/CONFIGURATION.md` covers what goes in the YAML
file and the environment; this page covers what you type.

Built with `argparse`. `job-sluice --help`, `job-sluice <group> --help` and
`job-sluice <group> <cmd> --help` are always the ground truth if this page and the installed
version ever disagree — see `tests/test_docs_claims.py`, which walks the real parser and
fails the build if this page falls out of sync with it.

`job-sluice --version` prints the installed version and exits 0; it works without a
subcommand.

Thirteen top-level command groups. `main()` loads the config before dispatching to any command,
so a retired or malformed key fails identically for all of them: `job-sluice: <message>` to
stderr, exit code `2`, no traceback. The same channel carries a usage error a COMMAND raises
once it has the config -- `cv run` uses it to refuse a vault that has nothing to compose from
(#242) -- so exit 2 means "you have to fix something before this can run", not specifically
"your config file is malformed". That is deliberate rather than incidental — a clean usage
error, not a crash, is what lets you actually read what's wrong and re-run `init` or `doctor`
to fix it, rather than a stack trace burying the message that would tell you how.

## `job-sluice ingest`

Scan job boards into the lead store.

### `job-sluice ingest list-sources [--health]`

Lists every registered source, sorted by id, one per line:

```text
<id> <kind> enabled|disabled[ EXAMPLE-SEARCH(<n>/<m>|?)]
```

Fully offline. Exit 0, except a malformed `sources.<id>.searches` entry: `main()`'s config
load (see above) refuses it with a named error, stating the source id, the offending entry's
index and the expected `[label, url, {params}?]` shape, so the command exits 2 for that case
before listing a single row rather than crashing mid-listing.

With `--health` the line grows, and the example-search marker moves to the end of it:

```text
<id> <kind> enabled|disabled  baseline=<N>[ company=<P>%][ link=<P>%][ location=<P>%]
    [ (<A> runs ago)][ UNMEASURED | UNGUARDED(<signal>,...)][ RETIRE][ EXAMPLE-SEARCH(<n>/<m>|?)]
```

- **`baseline=<N>`** — median row count over the last 7 runs.
- **`company=` / `link=` / `location=`** — completeness of that field across the run's parsed
  leads. A count alone is what a rotted extractor keeps: a board can serve a healthy-looking
  20 rows a run with the location read off none of them, which is what these columns exist to
  make visible. Omitted entirely when the run produced too few leads to measure (below 8),
  because "no rate measured" and "a rate of 0%" are different facts.
- **`(<A> runs ago)`** — the rates are not from the most recent run. They can be up to 30 runs
  old, so an undated percentage would let a stale 100% read as a current healthy measurement.
- **`UNMEASURED`** — the newest run recorded no rate, so the `blank` drift check cannot fire
  for this source right now whatever its history says. Shown whenever the rates are not from
  the newest run — including when they are merely stale, since a rate from three runs ago is
  not evidence the guard is running today.
- **`UNGUARDED(<signal>)`** — that signal's best-ever rate never cleared 0.8, so `blank` can
  never fire for it however far the source falls. This is legitimate for a board that does not
  publish the field and a real blind spot for one that was already broken when first recorded,
  and nothing local can tell the two apart — hence a flag for a human rather than a verdict. A
  source can record the benign ruling with `unpublished_fields`, which stops the flag for the
  named field only. Shown for enabled sources only: nothing runs for a disabled one, so no
  guard can be blind.
- **`RETIRE`** — the source is flagged for auto-retirement.
- **`EXAMPLE-SEARCH(<n>/<m>|?)`** — of this source's `<m>` searches this listing, `<n>` are its
  shipped example rather than one configured under `sources.<id>.searches` (#212). Printed on
  BOTH the plain and `--health` listings, since which searches a source runs is config state,
  not health state, and for enabled sources only, the same reason `UNGUARDED` is: a disabled
  source runs nothing, so it is neither configured nor falling back. Shown only when at least
  one of the source's searches is unconfigured, and always `<n>` equal to `<m>` when shown —
  `sources.<id>.searches` replaces a source's whole search list rather than merging into it,
  so a source is never partially configured.
- **`EXAMPLE-SEARCH(?)`** — this source's provenance could not be computed at all (a broken
  plugin whose `searches()` did not yield real `Search` objects); a warning naming the source
  is also logged. Distinct from the absence of any marker, which means "fully configured" —
  without this token a provenance failure would render byte-identical to a healthy,
  fully-configured source, which is exactly the invisibility this feature exists to remove.

### `job-sluice ingest run [--source ID ...] [--all] [--sink {vault,json}] [--dry-run]`

Drives a live Camofox session and writes results.

| Flag | Default | Notes |
|---|---|---|
| `--source ID` | — | repeatable; mutually exclusive with `--all` |
| `--all` | — | mutually exclusive with `--source`; bare `run` (neither flag) also means all sources |
| `--sink` | `vault` | `vault` or `json` |
| `--dry-run` | off | still records source health, writes nothing to the vault or `seen.db` |

`--dry-run` bounds what sluice **writes**, not what a run **does**. A dry run still invokes every
selected source's `fetch` exactly as a real run does — the flag only changes the SINK (JSON instead
of vault) and softens the relocated-`seen.db` guard from a refusal to a warning (`fatal=not
(dry_run or json_sink)` in `Sluice.ingest`). The flag itself never reaches `fetch`, and no source can
ask whether it is set, so a source has no way to suppress a remote side effect just because this is a
dry run — any side effect a fetch has on the far side happens on a dry run exactly as it does on a
real one. `ingest test-source` calls `fetch` with no sink at all and inherits the same property. No
shipped source mutates remote state today; this states the boundary rather than a current hazard, the
way the `triage run` row below names its billed backend call.

The run report is printed to **stderr**, not stdout — pipe accordingly:
```text
  <source_id>      status=<ok|error> fetched=<N> fresh=<N> drift=<drift|->[ withheld=<N>][ rejected_paths=<N>][ health_error=<msg>][ example_searches=<N>] [ RETIRED]
written: N created, N updated[, N merged][, N refused][, N merged-away][, N merged-away (unproven)], N skipped
```
`withheld` appears only when non-zero: `drift` of `fallback`/`blank`/`login` withholds that
source's leads from the selected sink (`--sink vault` or `--sink json`) for the run rather
than writing them (see `docs/ARCHITECTURE.md`'s `BREAKER_REASONS` note) — the leads are
never recorded as seen, so the next run retries them automatically once the source
recovers.
`health_error` appears only when the health pipeline itself failed. `drift` then prints `-`
because the run could not be classified, and the leads are withheld for that reason too.
`example_searches` appears only when non-zero: it counts how many of that source's searches this
run were its shipped example rather than one you configured under `sources.<id>.searches` (#212).
A fully configured source omits it, so a clean line means the criteria that ran were yours.
Refuses (exit 1) if the disabled-sources overlay is unreadable, or if the selection resolves
to zero enabled sources. Otherwise exits 0 even when individual sources errored — check the
per-source `status=` field for that. Telegram-notifies (if configured) when any source
degraded. Ingest refuses to run at all if `seen.db` looks relocated and this run would write
dedup state; `--dry-run` and `--sink json` are exempt since neither writes it — see
`docs/CONFIGURATION.md`.

### `job-sluice ingest test-source ID [--raw]`

Runs one source live against its configured search (or its built-in example search if none is
configured) and prints the parsed leads as indented JSON to stdout. With `--raw`, prints the
raw fetch payload instead — this is how a golden parser fixture gets captured. A short
one-line summary (`# <id>: <N> leads from '<label>'`) goes to stderr unless `--raw` is given.
Exit 0, except an unknown `ID`: `_require_known_source_ids` (shared with `--source` above)
refuses it with a named error listing every valid id, so the command exits 2 before attempting
any fetch rather than raising a bare `KeyError` traceback.

### `job-sluice ingest enable ID` / `job-sluice ingest disable ID`

Persist an operator on/off override to a small JSON overlay (`SLUICE_DISABLED`, default
`<XDG_STATE_HOME>/sluice/sluice_disabled.json`), independent of config and of the runtime
auto-retire mechanism. Prints `enabled <id>` / `disabled <id>` to stdout. Exit 0. An unreadable
or malformed overlay is not caught here and raises.

## `job-sluice triage`

Classify leads.

### `job-sluice triage run [--status LIST] [--limit N] [--dry-run] [--backend NAME] [--no-llm]`

| Flag | Default | Notes |
|---|---|---|
| `--status` | `new,research,unjudgeable` | comma-separated statuses to consider |
| `--limit` | none | cap the number processed |
| `--backend` | `auto` | `auto`, `primary`, `fallback` (`claude-max`/`deepseek` are deprecated role aliases). Selects the JUDGE's backend only -- tier-3 company resolution (`triage.company_resolve_llm`) always runs on the cheap `fallback` role regardless of this flag |
| `--no-llm` | off | deterministic rules only; touches no backend at all, judge or resolution |

Deterministic rules resolve obvious cases; ambiguous leads go to the LLM judge (skipped
entirely under `--no-llm`). A blank/placeholder-company `needs_review` lead gets one resolution
attempt first: a free regex over the role text's own trailing `"<role> at <Company>"`
clause, tier 0, then a free URL-pattern tier 1, an opt-in real page-visit tier 2
(`triage.company_resolve_fetch`), then an opt-in LLM read of that SAME page data, tier 3
(`triage.company_resolve_llm`). Never touches a lead already in the application
lifecycle. `--dry-run` still COMPUTES every resolution tier -- including a real tier-3
backend call, which is billed -- only the vault write and audit line are skipped.
`role_type` is judged as a pay BASIS, not as a label (#223). The salary's own markers
decide it (`/day`, `day rate`, `per annum`, ...); an unmarked salary consults the note's
`role_type` only where `role_type_source` records it as `observed` (read off the job
description) or `declared` (from a search you configured, or a lead you typed), never
where it is `assumed` -- the tool's own guess from a shipped example search or a source
default. A description that contradicts a value YOU declared is reported per lead and
recorded in the audit log, and your value is KEPT -- reading a pay basis out of prose is
imprecise enough that it should not overwrite something you typed, so the choice stays
yours. Filling a blank, or correcting the tool's own guess, is counted rather than
announced.

Pay is judged against **one floor per basis** — `contract_floor_gbp_hour`,
`contract_floor_gbp_day`, `contract_floor_gbp_week`, `perm_floor_gbp` — and never against
another basis's. An advert naming two bases abstains; one naming none is read as annual.
Each floor is `0` by default, meaning no floor, so leads on a basis you have not
configured are never rejected on pay. Before hourly and weekly had floors of their own the
basis was not parsed at all and both fell to `perm_floor_gbp`, so `£2,000 per week` — about
£104k a year — was silently binned as a sub-floor salary.

**The first run on a vault written before this feature writes nothing.** Those notes
carry no `role_type_source`, so they read as `assumed` and stop being judged on
`role_type`; weekly and hourly leads stop being judged as salaries at the same time. That
moves a batch of verdicts at once, in BOTH directions, so the run names the affected leads
and stops. Review them, then run it again to apply. A `--dry-run` shows the notice without
consuming it. Leads already at `dismiss` are not re-selected by a later run, so recovering
any the old gate binned needs a deliberate `--status dismiss` sweep.

Prints `job-sluice triage: <counts> judged=<N> resolved=<by-tier counts> llm_calls=<N>
observed_role_types=<by-origin counts> backend=<name> failures=<N>` to stderr and
Telegram-notifies. `tests/test_docs_claims.py` derives those key names from `cli.py` and
fails when this line and the real one disagree, because every previous version of this
sentence went stale silently. Exit 0 always.

### `job-sluice triage normalize-status [--dry-run]`

Canonicalizes status aliases (`shortlisted` → `shortlist`, `Researching` → `research`, etc.)
without changing anything else. Prints
`status normalize: changed=N unchanged=N conflicts=[...] skipped=[...] unknown=[...]` to
stdout, ` (dry-run)` appended when applicable. Exit 0 always.

## `job-sluice cv`

Compose, gate, render and sign off a tailored CV.

### `job-sluice cv run (--lead SLUG | --all-shortlist) [--limit N] [--dry-run] [--backend NAME] [--no-serve] [--include-stale]`

`--lead` and `--all-shortlist` are mutually exclusive and one is **required**.

| Flag | Notes |
|---|---|
| `--lead SLUG` | compose one CV for the shortlist lead matching this slug |
| `--all-shortlist` | compose for every shortlist lead without a `tailored_cv` yet |
| `--include-stale` | compose even for a lead older than `lead_ttl_days` (see #9 in `docs/CONFIGURATION.md`) |
| `--no-serve` | skip staging the rendered PDF for `apply` |

Per-result line to stderr: `cv: <status> <lead> served=<path> violations=<N> audit_flags=<N>
slop=<N> voice_flags=<N> dossier_failed=<bool>`, followed by one indented line per slop
finding (`SLOP <label>: <snippet>`, already prefixed) and one per voice finding (`VOICE:
<flag>`, opt-in via `cv.voice_check` -- see `docs/CONFIGURATION.md`) -- both empty on a
clean run, so nothing extra prints. A summary line follows when any dossier fetch failed
and composition proceeded blind. **Exit 1**
if: `--lead` matched no shortlist lead; `--lead` was ambiguous; or any result is
`skipped-config` (the candidate's derived name or contact block — from `Job Applications/
Candidate Profile.md` in your vault — is blank; the compose refuses before any LLM spend).
**Exit 2** if the vault cannot compose at all: the baseline CV at `baseline_rel` is missing,
empty, or unreadable (a permission problem, a refused symlink, or bytes that are not UTF-8), or
the `experience` corpus has no verified entries or cannot be read (#242). Unreadable and absent
are reported differently -- a read failure carries the underlying error rather than claiming the
file is not there. That is a config problem rather than a per-lead outcome,
so it is refused once for the whole run, before the renderer, the backend or any dossier fetch,
and it reports through `main`'s usage-error path like a malformed config key. Otherwise exit 0,
including when a result is `needs-signoff` (the advisory audit withheld the send-ready pointer
— see `cv signoff` below and #60 in `docs/ARCHITECTURE.md`).

### `job-sluice cv signoff --lead SLUG [--discard] [--yes]`

Releases or discards a CV that composed clean against the hard fabrication gate but was held
back by the softer advisory audit (`cv.require_signoff`, on by default). Without `--yes`,
prompts interactively: lists the unsupported claims, prints the served path, then
`sign off <slug>? [y/N] `. `--discard` rejects the held CV instead, freeing a fresh compose.
Exit 0 on `promoted`/`discarded`/`collision`/`aborted`; exit 1 on `no-match`/`ambiguous`/
`nothing`/`conflict`.

## `job-sluice apply`

Stage a CV and prep packet; record what you actually submitted.

### `job-sluice apply prep (--lead SLUG | --all-shortlist) [--limit N] [--json] [--dry-run] [--include-stale]`

`--lead` and `--all-shortlist` are mutually exclusive and one is required. This command is
offline by contract — no backend call, no browser. `--all-shortlist` previews the ready queue
(no CV is staged) and prints `apply-preview: eligible=N skipped=N` to stderr. `--lead` stages
the CV into the Camofox upload directory under a neutral filename and emits a prep packet
(text, or `--json`). `--dry-run` with `--lead` previews without staging. Exit 0 on `staged` (or
a dry-run preview); exit 1 on `skipped`, printing the reason.

The prep packet (#133) also carries whatever the vault's `Job Applications/Candidate
Profile.md` note declares beyond the CV header (address, right-to-work, "how did you hear",
and similar application-form fields — the note holds 36 fields in total, of which up to 30
reach this packet; see `docs/CONFIGURATION.md`), so a declared field never has to be retyped
into the same ATS form twice. The text form groups them
under two headings: `DETAILS:` for the fields reviewed as safe to print plainly, and `  EQUAL
OPPORTUNITIES MONITORING (optional on most forms):` for the ones covering an Equality Act 2010
protected characteristic (ethnicity, religion, disability, sexual orientation, gender identity,
marital status, nationality, and similar) plus the derived `age`, printed by default rather than
withheld behind a flag — the heading is the mitigation, not a refusal to show what was declared.
Only DECLARED fields appear; an undeclared one is omitted entirely, never printed blank.
`--json` emits the identical key/value pairs with no heading of its own, so it is not itself a
mitigation for retention — piping the packet somewhere just moves where that same data is read.

### `job-sluice apply record --lead SLUG [--ats NAME] [--url URL] [--dry-run]`

Marks a lead `applied` after you have submitted it by hand through the ATS. Refuses (never
clobbers) if the lead isn't in a state that permits the transition. Exit 0 →
`apply-record: <lead> -> applied (ats=<x> cv=<y>)`; exit 1 →
`apply-record: <lead> refused (status=<reason>)`.

## `job-sluice track`

Reconcile the funnel from email and calendar signals.

### `job-sluice track run [--dry-run] [--backend NAME]`

Reads Gmail + Calendar since the last watermark (or `track.gmail_lookback_days` if none
exists). Auto-advances status only on a proof-grade, authenticated, non-multi-tenant match
above the configured confidence floor; every weaker signal becomes a dead-letter proposal that
resurfaces on every run until a human acts. Prints a summary plus the open-proposal list to
**stderr** (the whole block, including the digest line):
```text
track: msgs=N classified=N auto=N proposed=N calendar_added=N receipts_recorded=N failures=N open=N
  FAILED <message_id>: <cause>
  WARNING: N calendar entries booked from a DTSTART with no usable timezone ...
  WARNING: the lastrun watermark is being HELD, so every run will re-query a widening window ...
  WARNING: the Gmail search hit its cap ...
  (no notification sent: no Telegram token configured ...)
  OPEN PROPOSALS (awaiting action):
  [<first_seen> x<times_surfaced>[ (new)]] <lead|candidates|?> <<message_id>>: <proposal> :: <hint>
```
`receipts_recorded` counts a domain-matched receipt for a lead already past `shortlist` — it
cannot advance anything (`can_apply` refuses), so its evidence (sender, subject, date, match
tier) is stamped onto the lead's own note instead, and the count is what makes that visible
without reading the log stream, which is discarded under cron. Under `--dry-run` the count
reports evidence sections that WOULD be stamped, exactly like `calendar_added` beside it — no
note is written.

Every failed message is NAMED, not just counted, and a real (non-`--dry-run`) run with any
failure Telegram-notifies **if a token is configured**. The digest reports which of the three
outcomes happened rather than leaving you to assume it went out: delivered (silent),
unconfigured, or rejected by the transport — that last one was previously indistinguishable
from success, because the send error is swallowed by design. The notified list is capped, with
the total count leading the message so truncating the list loses nothing vital, because an
oversized body is rejected outright. On a real run each failure is additionally recorded as a
dead-letter row — **when that write succeeds** — so it survives the Gmail query window moving
past it and can be cleared with `track dismiss --id`. If the dead-letter store cannot be
written the run says so on its own `WARNING:` line and holds the watermark instead, which is
the only reason the message stays reachable at all. `--dry-run` records nothing and sends
nothing.

Exit 1 only on a Google reauth failure (`track: google reauth needed (token refresh
failed)`); otherwise exit 0 — including a run with failures. A run that could not WRITE the
dead-letter store prints a `WARNING:` line saying the lastrun watermark is being held, since
that silently widens the Gmail query window on every subsequent run. A run whose Gmail search
hit its cap prints a `WARNING:` too — it did NOT see every matching message, and the ones it
missed are the oldest. That run HOLDS the lastrun watermark, so the missed messages stay
inside the query window and are recoverable: narrow `track.gmail_extra_query` and re-run.
(Shortening `track.gmail_lookback_days` will not help here — it applies only when there is no
watermark file yet.) Cron alerting is built on the exit-code rule above, and a
transient single-message failure making every run "fail" is how an alert gets muted.

### `job-sluice track confirm --lead SLUG --to STATUS [--when DATETIME] [--dry-run]`

Applies a proposed (or any valid) status transition by hand — this is what a dead-letter
proposal's `hint` line tells you to run. `--to` completes against the real status vocabulary
under `job-sluice[completion]` (see README's Shell completion section). Exit 0 →
`track-confirm: <lead> <from> -> <to>`; exit 1 → `track-confirm: <lead> refused (<reason>)`.

### `job-sluice track dismiss (--id MESSAGE_ID | --lead SLUG) [--dry-run]`

Clears a dead-letter proposal without changing any lead's status — for a proposal that turned
out to need no action. `--id` and `--lead` are mutually exclusive and one is required. Always
exit 0: `track-dismiss: <cleared|would clear> N entr(y|ies)`.

## `job-sluice leads`

Maintenance passes. **Report by default; none of these offers `--dry-run`, because the
default *is* the dry run** — a write happens only with the flag named below.
**Exception: `leads add` (#241) and `leads dismiss` (#131) both write unconditionally on
every call**, like the pipeline commands (`ingest run`/`triage run`/`cv run`/`apply
record`/`track run`), not like their `leads` siblings — the distinguishing property is who
decided what to write: `add` and `dismiss` act on what the USER typed (the lead's own
fields; `--lead`/`--reason`), while `dedupe`/`expire`/`reconcile` write over a set the TOOL
computed. There is no report to preview when the user is the one supplying the content.

### `job-sluice leads add --url URL --company NAME --role TITLE [--location L] [--salary S] [--role-type contract|permanent]`

Adds one lead by hand, for a job no scanner found — the only route into the lead store that
needs neither a Camofox browser server nor an MCP client (the MCP `create_lead` write tool
drives the same facade, but only under `job-sluice mcp serve --write`):

```console
$ job-sluice leads add --url https://example.invalid/jobs/1234 \
    --company "Example Systems" --role "Senior Engineer" \
    --location "Example City" --salary "£500/day" --role-type contract
leads add: Example Systems - Senior Engineer: created
```

It routes through the same `Vault.upsert` a scrape does, so every store guarantee applies
unchanged, and it reports which of upsert's six outcomes actually happened rather than
assuming `created`:

| outcome | exit | what happened |
| --- | --- | --- |
| `created` | 0 | a new note, at `status: new`, ready for `triage run` |
| `updated` | 0 | a lead already existed at this company+role and this posting proved to be the same one — its url matched, or failing that its location did — so `last_seen` was bumped and nothing else touched |
| `merged` | 0 | as `updated`, but the evidence could not prove same-or-different, so the store declined to split it into a second note |
| `refused` | 1 | nothing written: company and role both read back blank, or every name the lead could be seated at belongs to a different job |
| `merged_away` | 1 | nothing written: you previously merged this lead away and the archived note under `_merged/` carries this exact url |
| `merged_away_unproven` | 1 | nothing written: an archived note matches on weaker evidence than a url |

Three consequences worth knowing before you use it:

- **A second add does not correct a field.** `updated`/`merged` bump `last_seen` and nothing
  else — that is never-clobber, the same rule that protects your notes from a re-scrape. Edit
  the note in Obsidian to change a salary or a location.
- **A lead you merged away is not resurrected** (#81). Both `merged_away` outcomes write
  nothing and exit non-zero. To bring one back, move its note out of `Job Leads/_merged/`;
  it returns to the active view and the next add reconciles against it normally.
- **Nothing is recorded in `seen.db`.** A hand-added lead does not suppress the later genuine
  scrape of the same posting, which matters because `seen.db` has no removal path.

`--url` is required and must be http(s): it is what makes the lead apply-eligible, and what
`triage`/`cv` fetch the job description from.

`--role-type` records the pay basis your configured salary floors are judged against, as
`declared` provenance. It accepts `contract` and `permanent` and the usual spellings of each
(`perm`, `freelance`, `interim`, `fte`, `temp`, `fixed term`, …); anything else is refused
with the accepted list, rather than quietly stored as no basis at all.

There is no `--source` flag. `source` is not a free-text note about where you found the job —
it is a key into the ingest source registry, and `triage` reads it as one: a company it
resolves that equals the source id is discarded as the board's own name rather than the
employer. Setting it to a real board id therefore makes triage treat your hand-added lead as
that board's, and an employer genuinely called (say) Reed would be thrown away. The value is
always `manual`, which matches no board, so that check correctly abstains.

### `job-sluice leads dedupe [--merge ID ...] [--json]`

Reports candidate duplicate clusters (stdout):
```
[<cluster id>][ CONFLICT][ ⚑losers] survivor=<slug>
    <status>       <slug>  <url>
```
`--merge ID [ID ...]` merges the named clusters from a prior report — archives losers to
`_merged/` rather than deleting them, so a wrong merge is reversible (see
`docs/ARCHITECTURE.md`'s non-resurrection invariant). Always exit 0.

### `job-sluice leads expire [--expire [SLUG ...]] [--json]`

Reports leads stale past `lead_ttl_days` (stdout, one row per lead):
```
[stale]/[held ] <slug>  <N>d  <status>  first_seen <date>[  ⚑flag...][  sign-off hold]
```
`--expire` (bare, or naming specific slugs) dismisses the reported leads. If
`lead_ttl_days` is `0` (staleness off — the default), prints a one-line notice and exits 0
unless `--expire` was given, in which case it exits **1**. With `--expire`, also exits 1 if any
outcome is `no-match`/`conflict`/`unreadable`/`skipped`/`ambiguous`. A lead under a
`pending_cv` sign-off hold is refused rather than dismissed, since that would silently discard
a composed CV no human has reviewed yet.

### `job-sluice leads dismiss --lead SLUG --reason REASON`

Dismisses ONE lead by EXACT store-issued slug (never a substring match), with
`--reason` required and appended to `relevance_notes` under a same-day idempotency
tag -- a same-day repeat is a real `unchanged`, not a duplicate note. Resolves over
every triage-owned status, `dismiss` included, so re-dismissing an already-dismissed
lead is a legitimate no-op rather than a regression. Reuses `Sluice.dismiss_lead`
verbatim -- the same write path the `dismiss_lead` MCP tool calls (see below).
Refused (writes nothing) if the slug is ambiguous (a name collision), the lead has
moved into the application lifecycle, or it is held by a #60 sign-off
(`pending_cv`) -- resolve that first: `job-sluice cv signoff --lead "<slug>"
--discard`.

Exit 1 for no match, ambiguous, either refusal, or a lost write race (`conflict`);
exit 0 for `dismissed` or `unchanged`.

### `job-sluice leads reconcile [--apply] [--json]`

Reports (or with `--apply`, moves) each lead note into the folder its `status` implies — only
meaningful once `lead_layout: active_archive` is set (see `docs/CONFIGURATION.md`); with the
default flat layout, reports `reconcile: lead_layout is unset...` and exits 0 (or 1 if `--apply`
was given). **Do not run `--apply` concurrently with `ingest`/`triage`/`cv`/`apply`/`track`**: a
move landing inside another writer's compare-and-set window can leave two notes at one name,
reported under `ambiguous` rather than prevented. Exits 1 on `--apply` if `collisions`,
`skipped` or `ambiguous` is non-empty; exit 2 (`sluice: <exc>`) if the store has no layout
support at all.

### `job-sluice leads rename [--apply] [--json]`

Reports (or with `--apply`, renames) each lead note whose on-disk *basename* disagrees with its
frontmatter company (#151). A lead note created with a blank or sentinel ("Unknown", ...)
company is seated at a stale filename; once triage backfills a real company, this pass renames
the note in place to match — the same folder, a corrected basename — so a later re-scrape of the
same posting finds the existing note instead of minting a duplicate. **Do not run `--apply`
concurrently with `ingest`/`triage`/`cv`/`apply`/`track`**: a rename landing inside another
writer's compare-and-set window re-creates the source at the *old* basename, leaving two notes
where there was one — reported under `resurrected` rather than prevented. A renamed note is
found by the next scrape only once that scrape *also* carries the correct company; for a source
not yet fixed at ingest, that guarantee lives entirely in `seen.db` (keyed on the listing URL,
never the filename), and it disappears the moment `seen.db` is rebuilt or relocated.

The human report lists `renames`/`collisions`/`skipped`/`resurrected`/`ambiguous`;
`unresolved` (notes whose frontmatter still offers no real company to rename to — typically a
large, unactionable backlog) is a **count only** on the trailing summary line, never listed
item-by-item. Under `--apply`, also migrates the dead-letter store's rows for each renamed lead
(so `track confirm`/`track dismiss --lead` keep finding them under the new slug); a dead-letter
store known to be unreachable refuses the *whole* run before any note is renamed. Exits 1 on
`--apply` if `collisions`, `ambiguous`, `resurrected`, `skipped`, or a dead-letter migration
failure is non-empty; exit 2 (`job-sluice: <exc>`) if the store cannot rename notes at all.

## Evidence corpus capture: `experience`, `skills`, `stories`

Human-authored source material for CV composition (#164) — the Experience Library (the gate's
only citable source), the Skills Inventory (shown to the composer as framing since #165) and STAR
Stories (captured, not yet consumed), one per `EvidenceKind` in
`sluice/core/protocols.py`. All three groups (and their `add`/`list`/`verify` subcommands) are
built from ONE loop over that registry, so they share an identical shape and a fourth kind later
is one registry entry rather than three more hand-written command blocks.

**Nothing captured this way is used by the CV gate unless a human runs `verify`.** `add` only
ever proposes an entry into the kind's `_inbox/`; there is deliberately no `--all` and no `--yes`
anywhere in `verify` — it is the ONE operation that grants citability to the CV fabrication gate,
and a bulk flag would be the same `--verified` hole one level up (`add`'s field flags are derived
from the kind's user-facing fields, which is exactly why `verified` is never among them). `--id`
on `verify` FILTERS which pending entries are offered for review; it never answers for you.

*Unless*, not *until*: review is necessary for every kind, and sufficient for one. The gate
LICENSES the **Experience Library** only. Since #165 the other two differ from each other:
a verified **Skills Inventory** entry is shown to the composer as framing — it orders and
emphasises the experience entries, and no number may be quoted from it — while **STAR Stories**
are captured and reviewed but consumed by nothing yet.

Two registry flags carry that distinction: `EvidenceKind.read_by_composer` (does the corpus reach
the prompt) and `EvidenceKind.cited_by_gate` (may the gate license its content). `add`'s
confirmation line and `doctor`'s row both read them, so neither claims a citability the code does
not have.

An Experience Library entry's `--skills`/`Skills:` field (#168) licenses skills RELATIONALLY rather
than by merely existing in the Skills Inventory: a comma-separated (or YAML block-list) set of names
that THIS entry evidences, so a CV bullet citing it may use those names without being flagged a
misattributed skill, and a digit embedded in one (`Widget3`) is not read as a fabricated metric for
a bullet citing that entry. Every token of a `Skills:` value must begin with a letter, or with a dot
then a letter (`.NET`). A token that begins with a DIGIT is refused, and that is wider than it
sounds: a bare `92` is refused, and so are `ISO 9001`, `Web 2.0`, `Section 508`, `3D modelling`, `5S`
and `802.11ac`. Those are real things people hold, and the refusal is deliberate rather than an
oversight — a word followed by a bare number is structurally identical to metric shorthand like
`Result 92`, and admitting one admits the other, which would let a `Skills:` value blank a real
figure out of the numeric gate. Name them another way (`ISO quality management`) or leave them out.

That check runs at CV compose time (`cv/bundle.py`'s `build_bundle`), not at `add` or `verify`, and
it is **not** scoped to one lead: `build_bundle` runs per lead over the shared verified corpus, so a
single malformed value fails EVERY lead in the run — measured over three shortlisted leads, all
three returned `cv run`'s `error` outcome. The run itself does not abort (each failure is isolated
per lead), and the proposal and review commands never REFUSE on it, because they do not import
`cv/bundle.py` and so never validate it — they do read it (`experience list` prints the field, and
`verify` shows the raw note text including its `Skills:` line). Fix the one entry and the whole run
recovers. There is no
requirement that a `Skills:` name also exist as a verified `skills` entry, or the reverse;
`job-sluice doctor` reports a drift between the two corpora as an informational count, never the
skill's own name, and neither direction affects its exit code.

### `job-sluice experience add --name NAME [--company V] [--category V] [--best-for V] [--metrics V] [--skills V] [--body TEXT] [--body-file PATH|-]`
### `job-sluice skills add --name NAME [--proficiency V] [--domain V] [--evidence V] [--signal-value V] [--body TEXT] [--body-file PATH|-]`
### `job-sluice stories add --name NAME [--company V] [--best-for V] [--body TEXT] [--body-file PATH|-]`

Proposes one entry. `--name` becomes the entry's filename (reduced to letters, digits and
hyphens) and must not already be taken — either by a pending entry or by a verified one, since
both would collide at promotion and the name is only worth arguing about while you are typing it.
`--body-file -` reads the body from stdin instead of `--body`. Exit 1 (stderr) if the name is
already proposed (`'<slug>' is already proposed`) or already verified (`a verified <kind> entry is
already named '<slug>'`), the name does not reduce to a usable filename, a body line is shaped
like a bundle citation code, `--body-file` cannot be read, or the store refuses to write (e.g. a
symlinked evidence directory); otherwise prints the written path and exits 0. (An unknown *field*
is not among them: each group's flags are generated from its own kind, so argparse rejects an
undeclared one as a usage error before the command runs.)

### `job-sluice experience list [--pending]`
### `job-sluice skills list [--pending]`
### `job-sluice stories list [--pending]`

Lists verified entries by default, one per line: `<title>  [<verified date>]`. With
`--pending`, lists the not-yet-verified queue instead: `<title>  [pending]`. Exit 0 unless the
store cannot read an entry (see the note under `verify`, below).

Verified is not the same as **citable**: the CV fabrication gate licenses the Experience Library
alone, so a verified `skills` entry is shown to the composer as framing but cited by nothing, and
a verified `stories` entry is consumed by nothing yet (`EvidenceKind.cited_by_gate` and
`read_by_composer`).

### `job-sluice experience verify [--id NAME]`
### `job-sluice skills verify [--id NAME]`
### `job-sluice stories verify [--id NAME]`

Interactive only: under a non-interactive terminal (piped stdin, CI), nothing is promoted —
every pending entry (post-`--id`-filter) is printed `pending: <title>` and the command exits 0
with a note to stderr that promotion needs a real terminal. At a real terminal, shows each
pending entry's full text and asks `verify this entry? [y/N] ` — **anything but an explicit
`y`/`yes` is NO**, including a blank line or EOF. `--id NAME` offers only that one entry for
review, and matches either way round: the title `list --pending` displays (which is what an
entry you added to `_inbox/` by hand is called), or the same value `add --name` took, reduced
to a slug the way `add` reduced it — so the name's original spaces/casing still match. If it
does not name a pending entry, exits **1** with `<kind> verify: no pending entry matching
'<NAME>'` (stderr) rather than silently reporting nothing to do.

Each promotion is reported `verified: <title>` on stdout. Two per-entry outcomes go to stderr and
never stop the rest of the queue being offered:

- `changed since you reviewed it, not promoted: <title>` — the entry was edited (in Obsidian, say)
  between being shown to you and being promoted. You approved specific bytes; different bytes are
  not promoted. Exit 0 — nothing failed, there is just work to redo.
- `not promoted: <title> -- <reason>` — that one entry could not be read or promoted (its name is
  already taken in the citable set, it vanished mid-review, it is unreadable). The reason is
  words, never an errno. Exit **1**, with every successful promotion still listed on stdout.

`list` and `verify` both read a directory you may edit by hand, so a failure affecting the WHOLE
command (an unreadable inbox, a symlinked evidence directory, an unknown kind) exits **1** with
`<kind> list: <error>` / `<kind> verify: <error>` on stderr rather than a traceback.

## `job-sluice health [--leads]`

Per-source scrape baseline and retire state, one line each:
`<id> baseline=<N> recent=<counts>[ RETIRE][ BROKEN reason=<reason> x<N>][ unjudgeable=<N>/<N>]`.
`BROKEN` is the cumulative signal for a source stuck on a NAMED, non-retiring failure (`auth`,
`blocked`, `unreachable`) — see `docs/ARCHITECTURE.md`'s `_RECOVERABLE` note.

`unjudgeable=<N>/<N>` (numerator/denominator: that source's `unjudgeable`-status leads over the
leads triage has *concluded* about — every triage-owned status except `new`) appears only with
`--leads`, which walks the vault once to compute it — off by default, since this command is
otherwise a source-registry-and-health-store read a user runs often and cheaply, and a JD fetch
that never arrives for one board clusters by source rather than at random (#169 §2).

The denominator is deliberately *not* the `new`/`research`/`unjudgeable` triage selection, which
it was at first: a lead leaves that set the moment it is judged, so the numerator stayed while the
denominator drained and the figure climbed toward 100% as a source got *healthier* — a source with
500 scraped, 480 dismissed, 17 judged and 3 stuck printed `3/3`. The trade taken in return is that
a source breaking *today* now shows a percentage against its own history rather than 100%; that is
the right way round here, because a false alarm in a health report trains people to ignore the row,
and `detect_drift`'s per-run reasons and the ingest breaker are what actually catch a source
breaking today. Fully offline either way. Exit 0 always.

## `job-sluice mcp`

### `job-sluice mcp serve [--write]`

Runs sluice as a Model Context Protocol server over stdio, so an agent (Claude Code
or otherwise) can call sluice's tools directly instead of shelling out to the CLI
and parsing its stdout. Needs `pip install -e '.[mcp]'`; if the `mcp` package is not
installed, exits 2 with a stderr message naming `job-sluice[mcp]` as the extra to
install (see `sluice/mcpserver.py`'s `McpNotInstalled`) rather than a traceback.
Blocks for the life of the process once started; there is no `--dry-run`.

**Without `--write`** (the default), the read-only tools are registered:
`list_leads`, `get_lead`, `doctor`, `health`, `list_evidence`. `list_evidence(kind,
pending=False)` lists evidence corpus entries (`experience`, `skills`, `stories`) --
verified ones by default, or the not-yet-verified queue when `pending=True`. Verified
does not mean citable for every kind: the CV fabrication gate licenses the Experience
Library alone, and since #165 a verified `skills` entry reaches the composer as framing
without becoming citable. A non-empty result carries a `content_warning` --
entry text is written by the user, and reaches the calling agent as data to read, never
as instructions to follow. Proposing an entry needs `--write` (`propose_evidence`,
below); **verifying one is not possible through MCP at any privilege level** -- there is
no such tool, deliberately, because verification is what makes an entry citable and
promotion stays interactive-only (`job-sluice <kind> verify`). See
`sluice/mcpserver.py`'s `list_evidence` docstring for why.

**With `--write`**, these further tools are registered:

- `dismiss_lead(lead, reason)` -- dismiss one lead by EXACT slug, recording `reason`.
- `apply_record(lead, ats=None, url=None)` -- record a sent application (shortlist
  -> applied).
- `cv_run(lead, backend="auto")` -- compose and render a CV for one shortlisted
  lead. The composed text itself is never included in the response.
- `cv_signoff(lead, discard=False, confirm_token=None)` -- resolve a #60 sign-off
  hold. `discard=True` clears it outright. **Promoting needs TWO calls**: the first
  (no `confirm_token`) writes nothing and returns a `confirm_token` bound to the
  exact claims text; relay the claims to a human, get explicit approval, then call
  again passing that token back to actually promote. A token whose claims have
  since changed (a re-compose interleaved) returns `stale_confirmation` with a
  fresh token, having written nothing.
- `create_lead(title, company, url, location="", salary="", job_type="",
  source="manual")` -- create a new lead note directly, for a job a human found
  that no scanner ingested. Lands at `status: new`; `job-sluice triage run`
  promotes it from there. Reports `upsert`'s own outcome vocabulary verbatim:
  identity is company+title, and a second call at that same identity bumps
  `last_seen` ONLY, reported as `updated` when the incoming url (or, absent a
  url match, the location) proves the same posting, or `merged` when neither
  does (inconclusive evidence -- e.g. a blank-url lead whose location is
  blank, or is compared against a note whose own location is blank) -- UNLESS
  the two locations are proven DIFFERENT (two non-blank, non-overlapping
  locations), in which case the call creates a genuinely NEW note instead
  (`created` again -- a second real note at the same company+title). Both
  `updated` and `merged` are a bare `last_seen` bump; the new
  url/salary/location is NOT recorded either way. `slug` is OMITTED from the
  response only for `refused`/`merged_away`/`merged_away_unproven`, which
  write nothing and so never have a slug to report -- `created`/`updated`/
  `merged` always carry the slug of the note this call actually touched, the
  store's own answer, never a guess.
- `propose_evidence(kind, name, fields, body="")` -- propose one evidence entry for
  a human to review. `fields` takes that kind's own declared field names (the same
  set `job-sluice <kind> add` exposes as flags); an undeclared key is refused by
  name, `verified` among them. The entry lands in the pending inbox, which the
  verified read cannot see, so it is **not citable by the CV fabrication gate and
  not visible to `list_evidence`'s default view** until a human runs `job-sluice
  <kind> verify`. Every successful response says so in its own `detail`. A name
  already taken -- in the pending queue or in the verified corpus -- comes back as
  `outcome: "refused"` carrying the store's own message, rather than as an error:
  the MCP SDK discards an exception's text, and "pick another name" is the one
  recovery a caller needs to be able to act on. Malformed input (an unknown kind, an
  undeclared field key, an unusable name) still raises and reaches the client as a
  tool error.

`--write` is a per-registration trust decision about one MCP client: every existing
read-only registration is unaffected, and a read-only server's `tools/list`
genuinely omits every write tool, not merely refusing them at call time.

## `job-sluice init`

Scaffold a config, a Judging Profile, and a Candidate Profile. See the Quickstart section of
the [README](../README.md) and `docs/CONFIGURATION.md` for what each question controls. Identity
lives in the Candidate Profile note (`Job Applications/Candidate Profile.md`) from here on,
created by `job-sluice init` or by hand.

| Flag | Notes |
|---|---|
| `--vault PATH` | your Obsidian vault directory |
| `--no-input` | never prompt; answer only from `--vault`/`$VAULT_DIR`/an existing config's `vault_dir` |

Never overwrites an existing config or Judging Profile — re-running is safe and reports what
it left alone. The Candidate Profile note differs: its gate is whether *anything* is declared
yet (`has_any_declared`), not whether the note merely exists, so a note with some identity
already declared is left alone and its interview is skipped on every future run — fill in
anything still blank directly in Obsidian. If the note exists but is entirely blank, `init`
re-asks, but the write still refuses (never-clobber) because the note is already there; your
answers land in `Candidate Profile.init-scaffold.md` beside it instead, and re-running again with
that scaffold ALSO occupied is reported as a failed write rather than silently discarding what
you typed. Exit 2 if `--vault` and `$VAULT_DIR` name different directories, if `--no-input` is
given with no vault answer available anywhere, or if the resolved `vault_dir` exists and is not
a directory. Exit 1 if any individual write failed. Otherwise 0.

## `job-sluice doctor [--offline] [--strict] [--verbose] [--require CAPABILITY]`

Preflights backends, the renderer, the store's on-disk artefacts (vault, baseline CV, Judging
Profile, a verified/pending count for each of the three evidence corpora — #164: Experience
Library, Skills Inventory, STAR Stories — and the Candidate Profile note's own declared
name/contact — #133/#107), track's Google adapter, and every list-typed preference gate's
abstain/active posture. A `cv.name`/`cv.contact` key still set in `sluice.yaml` from an older
config is its own DEAD `cv-config` row rather than a traceback — see
`docs/TROUBLESHOOTING.md`. Never opens a browser and never writes through the store or renderer.

| Flag | Notes |
|---|---|
| `--offline` | config-only checks; no network round-trip |
| `--strict` | also fail (exit 1) on any `degraded` result, not just `dead` |
| `--verbose`, `-v` | print every check as a table, not just the verdict |
| `--require CAPABILITY` | exit 1 unless these are ready: `ingest`, `triage`, `cv`, `apply`, `track`. Comma-separated, repeatable |

**Default output (stdout) is a verdict**, not a table (#243): which of the five things sluice
does are ready, which are waiting on something you have not supplied, and which are broken —
followed by the remedy for each row in the last two groups, verbatim from the check that knows
it. `--verbose` prints the full table instead, which is what you want once something *is*
broken.

Five classification states per row: `ok`, `degraded`, `dead`, `setup`, and `notice`. `notice`
and `setup` never affect the exit code, even under `--strict`. The `--verbose` output is two
tables — backends, then components — each ending with an
`N ok, N degraded, N dead, N setup[, N notice]` summary. Both summaries move with the install
and they move independently: the backend one tracks which providers are reachable, the component
one tracks your vault and your optional extras. A captured run in any doc is therefore one
machine's answer, never a constant to compare yours against.

**The `gates` rows report posture, not faults.** They sweep every **list-valued** setting and say
what its current value means: `abstaining (empty)` for a preference gate, and its own posture for
the settings where empty means something else — of which there is more than one kind, since an
empty security allowlist grants no exceptions and an empty phrase allow-list leaves the full list
active. All of them are `notice`, so none reaches the exit code. The numeric pay floors
(`contract_floor_gbp_day`, `perm_floor_gbp`) get no row at all: they default to `0`, which is off.

**`setup` versus `dead` is the distinction the exit code is built on.** `setup` means you have
not supplied the thing yet — no baseline CV, no verified evidence, no API key in the
environment, the `render` extra not installed, no vault. `dead` means something you *did*
supply does not work — a `cv.renderer` naming no registered renderer, a `cv.template` that is
not a file, an API key that fails its round-trip, a store that has moved. Exit 1 if any row is
`dead` (or, under `--strict`, `degraded`); otherwise 0.

**A fresh install therefore exits 0.** It still has rows to act on, and `doctor` still lists
them; what it no longer does is report a to-do list as a failure. How many rows depends on the
install: a packaged channel supplies the renderer's native libraries, a bare `pip` install does
not, so a count stated here would be wrong for one of them. Run this before a real pipeline run;
see `docs/TROUBLESHOOTING.md` for what a specific `dead`/`degraded` line means and how to fix
it.

**`--require` is for monitoring.** The exit code answers "is anything broken", which is not
the same question as "can this install still do the thing I depend on". A `setup` row does not
fail the build — so an install that stops working for a reason sluice reads as *unsupplied*
(a cron unit whose `PATH` lacks `~/.local/bin`, an API key not exported outside an interactive
shell) exits 0. Name what you actually need and it exits 1 the moment that stops being ready:

```bash
job-sluice doctor --require triage,cv || notify-me
```

Any bucket other than ready fails it — `needs setup`, `degraded` and `broken` alike — because
the question is "can I do this", and all three answer no. An unknown capability name is exit 2,
a usage error, deliberately distinct from the exit 1 that means a capability is down.

`ready` means nothing `doctor` *checks* is blocking that capability. It is not a promise the
thing works: `--offline` never round-trips a backend, and nothing ever dials Camofox. So
`--require cv` is a much stronger claim than `--require ingest`.

> **This changed (#243).** `doctor` used to exit 1 whenever any row was `dead`, and a fresh
> install had `dead` rows by design — so the happy path failed. If you alert on `doctor`'s exit
> code (a cron job, a setup script, a health check), it now fires only on a genuine fault.
> **Point that alert at `--require` instead**, naming the capabilities you actually use. That
> is a sharper signal than the one it replaces, not a weaker one: the old exit 1 fired on a
> fresh install and on every gap indiscriminately, while `--require triage` fires precisely
> when triage stops working.
