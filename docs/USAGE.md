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
stderr, exit code `2`, no traceback. That is deliberate rather than incidental — a clean usage
error, not a crash, is what lets you actually read what's wrong and re-run `init` or `doctor`
to fix it, rather than a stack trace burying the message that would tell you how.

## `job-sluice ingest`

Scan job boards into the lead store.

### `job-sluice ingest list-sources [--health]`

Lists every registered source, sorted by id, one per line: `<id> <kind> enabled|disabled`.
With `--health`, appends the baseline count and ` RETIRE` if the source is flagged for
auto-retirement. Fully offline. Exit 0 always.

### `job-sluice ingest run [--source ID ...] [--all] [--sink {vault,json}] [--dry-run]`

Drives a live Camofox session and writes results.

| Flag | Default | Notes |
|---|---|---|
| `--source ID` | — | repeatable; mutually exclusive with `--all` |
| `--all` | — | mutually exclusive with `--source`; bare `run` (neither flag) also means all sources |
| `--sink` | `vault` | `vault` or `json` |
| `--dry-run` | off | still records source health, writes nothing to the vault or `seen.db` |

The run report is printed to **stderr**, not stdout — pipe accordingly:
```
  <source_id>      status=<ok|error> fetched=<N> fresh=<N> drift=<drift|->[ withheld=<N>][ health_error=<msg>] [ RETIRED]
written: N created, N updated[, N merged][, N refused][, N merged-away][, N merged-away (unproven)], N skipped
```
`withheld` appears only when non-zero: `drift` of `fallback`/`blank`/`login` withholds that
source's leads from the selected sink (`--sink vault` or `--sink json`) for the run rather
than writing them (see `docs/ARCHITECTURE.md`'s `BREAKER_REASONS` note) — the leads are
never recorded as seen, so the next run retries them automatically once the source
recovers.
`health_error` appears only when the health pipeline itself failed. `drift` then prints `-`
because the run could not be classified, and the leads are withheld for that reason too.
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
Exit 0 always.

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
Prints `job-sluice triage: <counts> judged=<N> resolved=<by-tier counts> llm_calls=<N>
backend=<name> failures=<N>` to stderr and Telegram-notifies. Exit 0 always.

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
Otherwise exit 0, including when a result is `needs-signoff` (the advisory audit withheld
the send-ready pointer — see `cv signoff` below and #60 in `docs/ARCHITECTURE.md`).

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
**Exception: `leads dismiss` writes unconditionally on every call** (#131), like the
pipeline commands (`ingest run`/`triage run`/`cv run`/`apply record`/`track run`), not
like its `leads` siblings — the distinguishing property is who decided what to write:
`dismiss` acts on a verdict the USER typed (`--lead`/`--reason`), while
`dedupe`/`expire`/`reconcile` write over a set the TOOL computed.

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
as instructions to follow. Deliberately read-only: there is no MCP tool anywhere that
proposes or verifies an entry (see `sluice/mcpserver.py`'s `list_evidence` docstring for
why).

**With `--write`**, five more tools are registered:

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

`--write` is a per-registration trust decision about one MCP client: every existing
read-only registration is unaffected, and a read-only server's `tools/list`
genuinely omits the five write tools, not merely refusing them at call time.

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

## `job-sluice doctor [--offline] [--strict]`

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

Four classification states per row: `ok`, `degraded`, `dead`, and `notice` (which never
affects the exit code, even under `--strict`). Output (stdout) is two tables — backends, then
components — each ending with an `N ok, N degraded, N dead[, N notice]` summary. Exit 1 if any
row is `dead` (or, under `--strict`, `degraded`); otherwise 0. Run this before a real pipeline
run; see `docs/TROUBLESHOOTING.md` for what a specific `dead`/`degraded` line means and how to
fix it.
