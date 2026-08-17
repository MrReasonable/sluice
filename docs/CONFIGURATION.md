# Configuration

Config is layered: code defaults < the YAML file at `$SLUICE_CONFIG` (else
`<XDG_CONFIG_HOME>/sluice/config.yaml`) < environment variables. Every key below has a code
default, so `job-sluice` runs with no config file at all. `job-sluice init` writes a config for
you (see the README's Quickstart) — **do not** `cp sluice.yaml.example sluice.local.yaml`; it
is a catalogue with several illustrative values left ACTIVE rather than commented, so a
verbatim copy silently closes gates a fresh install would otherwise leave open.

## The abstain rule

Read this before any of the tables below. **Every preference gate defaults to
empty/zero, and an unconfigured gate abstains — it passes every lead through,
it does not reject everything.** An empty `target_locations` keeps every lead
regardless of location; it does not discard every lead that names one. Getting
this backwards has silently binned someone's entire job hunt before (see
`CLAUDE.md`'s `672ad2a` reference) — it is now pinned by a dedicated test
(`tests/test_sluice_neutral_defaults.py`).

Two shipped values are the deliberate **exception** to "empty by default", and they run the
other way: `track.ats_relay_domains` and `track.job_board_domains` are non-empty **safety
denylists**, not preference gates — see the Track table below. Numeric gates
(`lead_ttl_days`, the two pay floors) follow the same abstain rule at `0`, not empty list, and
`lead_layout`/`cv.template` follow it at `""`, not empty list — each carries its own guard
test rather than the shared list-keyed sweep, because a `str`/`int` field is invisible to a
sweep keyed on list defaults.

## Root

| Key | Default | Env override | Meaning when unset |
|---|---|---|---|
| `store` | `"vault"` | — | which store implementation; an unknown name raises at construction, listing the valid ones |
| `fetcher` | `"camofox"` | — | which browser-automation implementation |
| `baseline_rel` | `"My CV/CV.md"` | — | your baseline CV's path, relative to the store root |
| `vault_dir` | `""` | `VAULT_DIR` | `./vault`, relative to the cwd — the one path sluice deliberately does **not** relocate to XDG, since it's your Obsidian directory, not sluice's state |
| `dossier_dir` | `""` | `DOSSIER_DIR` | `<XDG_CACHE_HOME>/sluice/dossiers` — shared cache for triage's and cv's job-ad fetches |
| `dossier_allow_hosts` | `[]` | — | a **security allowlist**, not a preference gate: empty means no exceptions to the SSRF guard, not "block everything" — public urls still fetch either way. Entries are an exact hostname or a CIDR/IP; prefer a CIDR where you can, since a hostname grant covers every address that name resolves to, today and at each future fetch |
| `relevance_keep` | `[]` | — | coarse ingest title keep-list, applied **before** dedup and any LLM call |
| `relevance_drop` | `[]` | — | coarse ingest title drop-list, same timing |
| `location_noise_words` | `[]` | — | words subtracted from a location before same-opportunity comparison (e.g. dedup deciding two postings are the same job) |
| `dedupe_title_noise_words` | `[]` | — | tokens stripped from a title before `leads dedupe` clusters two notes; empty is the strictest setting |
| `lead_ttl_days` | `0` | — | days since last-scraped before a lead counts as stale; `0` turns staleness off entirely. Rejects YAML bools (`lead_ttl_days: yes` errors rather than silently parsing as a 1-day TTL — `bool` subclasses `int` in Python, and PyYAML resolves `yes`/`on`/`true` to `True`) |
| `lead_layout` | `""` | — | `""` (flat, one directory) or `"active_archive"` (splits into `Active/`/`Archive/`); nothing moves on its own — `leads reconcile --apply` does the filing |
| `sources` | `{}` | — | per-source `{enabled, tuning, searches}` overrides; an unlisted source runs enabled with its built-in example search |
| `notify` | `{}` | — | `notify.telegram.{token,chat_id}`; the matching env vars win over these |
| `locations` | **retired** | `SLUICE_LOCATIONS` | setting either raises — use `triage.target_locations` |

## `triage:`

| Key | Default | Meaning |
|---|---|---|
| `accept_titles` | `[]` | title keep-list for the deterministic gate; empty abstains, every lead reaches the judge |
| `reject_titles` | `[]` | title reject-list |
| `target_locations` | `[]` | empty = no geographic gate at all |
| `reject_locations` | `[]` | |
| `reject_companies` | `[]` | |
| `contract_floor_gbp_day` | `0` | `0` = no floor; compares numbers, not currencies |
| `perm_floor_gbp` | `0` | `0` = no floor |
| `batch_size` | `5` | leads per judge call |
| `ttl_days` | `7` | **dossier cache** TTL — unrelated to the root `lead_ttl_days` |
| `audit_jsonl` | `""` | resolves to `<XDG_STATE_HOME>/sluice/triage-audit.jsonl`; env override `TRIAGE_AUDIT` |
| `rejected_note` | `"Job Applications/Rejected Leads Audit.md"` | rolling digest note in the vault |
| `primary_backend` / `fallback_backend` | `"claude-max"` / `"deepseek"` | which provider fills each role — see `--backend` in `docs/USAGE.md` |
| `claude_max_model` | `"claude-sonnet-4-5"` | the primary role's model, **bare**, no `provider/` prefix |
| `cheap_model` | `"deepseek-v4-flash"` | the fallback role's model |
| `claude_max_host` / `claude_max_path` | `""` / `"claude"` | empty host runs `claude_max_path` locally; set a host to shell it over SSH. A leading `-` in either is refused (argument-injection guard) |
| `route_borderline` | `false` | rejects non-bool values (see `lead_ttl_days` above for why) |

Company resolution (`resolve.py`, #109/#120/#151) for a blank/placeholder-company `needs_review` lead runs
tier 0 (a regex over the role text's own trailing `"<role> at <Company>"` clause) and tier 1 (a
URL-pattern match via the source adapter) **unconditionally** — both are free, no fetch, no LLM,
and neither has a row below because neither has a config gate: they run the same on a bare
`--no-llm` install as anywhere else. The two rows below gate only tiers 2 and 3, the only two
that ever open a browser tab or spend a backend call.

| Key | Default | Meaning |
|---|---|---|
| `company_resolve_fetch` | `false` | opt-in: lets a blank/placeholder-company `needs_review` lead trigger a real (no-LLM) page visit to try to identify the employer from the page itself, feeding tiers 2 AND (if also enabled) 3 below; off by default so an unconfigured install never opens a browser tab it wasn't asked to. Rejects non-bool values, same reasoning as `lead_ttl_days` above |
| `company_resolve_llm` | `false` | opt-in: tier 3 of the same resolution, an LLM read of the page data tier 2 already fetched (no second visit) when tiers 0, 1, and 2 abstain. Always runs on the **fallback** role's cheap model (`fallback_backend`/`cheap_model`) regardless of `--backend`, since it is bulk extraction rather than judgement. **Requires `company_resolve_fetch: true`** — set alone the loader raises, because tier 3 reads what tier 2 fetches and could never fire. Off under `--no-llm`. Rejects non-bool values, same reasoning as `lead_ttl_days` above |

## `cv:`

Entirely personal — every field defaults empty or to a neutral placeholder, and the whole
block is commented out in `sluice.yaml.example`.

| Key | Default | Meaning |
|---|---|---|
| `name` | `"Your Name"` | **must be changed** — a compose refuses before any spend while this is still the placeholder, since it becomes the PDF's `<h1>` |
| `contact` | `""` | rendered verbatim; blank makes `doctor` report DEGRADED |
| `employers` | `[]` | every name must appear verbatim (case-sensitive) in each tailored CV; empty skips the per-employer completeness check |
| `fabrication_decoys` | `[]` | known-hallucination strings — a hard fail if any appear in the composed CV |
| `served_prefix` | `"CV"` | must match `apply.served_prefix` |
| `prefix_map` | `{}` | |
| `negatives` | `[]` | |
| `ttl_days` | `7` | dossier cache TTL for cv |
| `require_signoff` | `true` | a safety valve, not a preference — ships **on**; an `unsupported` audit claim withholds the send-ready pointer until `job-sluice cv signoff` |
| `renderer` | `"template"` | `template` or `script`; `weasyprint` (the old bundled renderer) is **retired** and raises, naming `template` as the replacement |
| `template` | `""` | blank = the packaged layout; point at your own `.html.j2` (contract: `docs/cv-template-example.html.j2`) |
| `render_script` | `"./scripts/cv_render_v2.py"` | `script` renderer only; no script ships — point it at your own or it fails at construction |
| `render_python` | `"/usr/bin/python3"` | |
| `render_home` | `"./cv-home"` | cwd-relative by design, no `~` expansion |
| `output_dir` | `"./cv-output"` | cwd-relative |
| `served_dir` | `"./cv-served"` | cwd-relative |
| `vault_cv_dir` | `"My CV/tailored"` | inside the vault |
| `neutral_filename` | `"CV.pdf"` | |
| `primary_backend` / `fallback_backend` | `"claude-max"` / `"deepseek"` | |
| `compose_model` | `"claude-sonnet-4-5"` | |
| `compose_effort` | `"max"` | |
| `cheap_model` | `"deepseek-v4-flash"` | |
| `audit_model` | `"claude-sonnet-4-5"` | |
| `compose_host` / `compose_claude_path` | `""` / `"claude"` | same shape as `triage.claude_max_host` |
| `compose_timeout` | `300` | **seconds per invocation per leg.** The engine composes up to twice then audits (3 invocations), and under `auto` each may try primary then fallback — worst case per lead is **6×** this value. Must be a positive integer; there is no "off", and `yes` is refused rather than read as 1 second |

## `apply:`

| Key | Default | Meaning |
|---|---|---|
| `served_dir` | `"./cv-served"` | where `cv` serves PDFs |
| `camofox_upload_dir` | `"./cv-host"` | bind-mounted into Camofox, read-only |
| `camofox_cv_dir` | `"./cv-uploads"` | the same directory's path as seen *inside* the browser |
| `neutral_name` | `"CV.pdf"` | what a recruiter sees on the form |
| `served_prefix` | `"CV"` | must match `cv.served_prefix` |

## `track:`

| Key | Default | Meaning |
|---|---|---|
| `seen_db` | `""` | resolves to `<XDG_STATE_HOME>/sluice/track-seen.db`; **no env override**, this key is the only way to move it. Derives the `.lastrun` watermark and the `#49` dead-letter store, so a stray value silently hides the whole backlog |
| `token_path` | `""` | resolves to `<XDG_STATE_HOME>/sluice/google_token.json` (written `0600`); **no env override** |
| `gmail_lookback_days` | `2` | fallback window when no `.lastrun` watermark exists |
| `gmail_extra_query` | `""` | |
| `calendar_lookahead_days` | `45` | half-width of the window sluice reads around an invite's start. Rescheduling an interview further than this no longer loses track of sluice's own entry — that is found by its tag, whatever the window, provided the invite carries a `UID` (nearly all do; one that does not cannot be identified across runs at all, so it is deliberately never matched). Detection of a *foreign* event at the same slot only needs ±`calendar_match_minutes`, so it survives any lookahead of a day or more. The real cost of a wide window is that it is what trips `calendar_max_events` below. |
| `calendar_match_minutes` | `30` | start-proximity dedup window |
| `gmail_max_messages` | `500` | hard TOTAL a run will read across pages. Hitting it means the run did NOT see every matching message — the oldest are starved, since Gmail returns newest-first — so the run holds the `.lastrun` watermark and warns. Raise it, or narrow `gmail_extra_query`. |
| `calendar_max_events` | `2500` | hard TOTAL events a single calendar read returns, and it bounds both reads sluice makes — the window read and the UID-tag lookup. Correctness-bearing: hitting it makes sluice answer `unresolved` rather than guess, so an interview is left unbooked until you act. The window is `2 × calendar_lookahead_days` with recurrences expanded, so raising the lookahead raises the event count against this ceiling. |
| `calendar_assumed_timezone` | `UTC` | IANA zone assumed for a DTSTART with no usable one (floating time, date-only, or an unresolvable TZID). Set it to your own zone — a zone-less invite in your inbox is far likelier to be in local time than UTC. Whenever the guess is written (a `created` or `updated` outcome, dry runs included) it is warned about and counted in the run digest; a `present` outcome writes nothing and stays silent. An unresolvable value warns once and falls back to UTC. |
| `primary_backend` / `fallback_backend` | `"claude-max"` / `"deepseek"` | |
| `claude_max_model` | `"claude-sonnet-4-5"` | |
| `cheap_model` | `"deepseek-v4-flash"` | |
| `auto_status_min` | `0.75` | min confidence to auto-advance a scheduling/offer signal |
| `auto_reject_min` | `0.9` | stricter bar to auto-reject |
| `auto_apply_min` | `0.75` | min receipt confidence to auto-advance `shortlist`→`applied`, and **only** on a domain-proof match: the sender host must match one of the lead's known hosts (`applied_url` then `url`), on a message whose `Authentication-Results` records a PASS aligned with that sender, with neither host multi-tenant |
| `ats_relay_domains` | shipped, multi-tenant recruiting-platform vendors | **safety denylist, not a preference gate.** Membership follows a stated selection rule (see `sluice/track/config.py`'s module comment) rather than a fixed roster meant to be enumerated here — a count printed in prose goes stale the moment the shipped list grows. Anything you set is *merged over* the shipped defaults, never replacing them — a shipped entry cannot be removed, only relabelled by reusing its key. A value that isn't a mapping of host → label raises rather than silently emptying the list. Emptying this makes the proof tier **more** permissive |
| `job_board_domains` | shipped, the boards `sluice/ingest/sources/` scrapes | same merge semantics, for the boards this project itself scrapes |

## Environment variables

| Variable | Overrides | Default when unset |
|---|---|---|
| `SLUICE_CONFIG` | config file location | `<XDG_CONFIG_HOME>/sluice/config.yaml` |
| `VAULT_DIR` | `vault_dir` | `./vault`, relative to the cwd |
| `SEEN_DB` | ingest's dedup database | `<XDG_STATE_HOME>/sluice/seen.db` |
| `SLUICE_HEALTH` | per-source health/drift store | `<XDG_STATE_HOME>/sluice/sluice_health.json` |
| `SLUICE_DISABLED` | the `ingest enable`/`disable` overlay | `<XDG_STATE_HOME>/sluice/sluice_disabled.json` |
| `TRIAGE_AUDIT` | `triage.audit_jsonl` | `<XDG_STATE_HOME>/sluice/triage-audit.jsonl` |
| `DOSSIER_DIR` | `dossier_dir` | `<XDG_CACHE_HOME>/sluice/dossiers` |
| `XDG_CONFIG_HOME` / `XDG_STATE_HOME` / `XDG_CACHE_HOME` | the roots every relocatable path above resolves under | `~/.config` / `~/.local/state` / `~/.cache`. A **relative** value is ignored with a warning, per the XDG spec |
| `SLUICE_LOG_LEVEL` | logger level | `INFO` |
| `SLUICE_TELEGRAM_TOKEN` / `SLUICE_TELEGRAM_CHAT` | `notify.telegram.{token,chat_id}` | notifications disabled unless both are present |
| `SLUICE_LOCATIONS` | — | **retired** — merely being set makes `load_config` raise |
| `CAMOFOX_URL` | the Camofox server's base URL | `http://127.0.0.1:9377` |
| `CAMOFOX_USER` | **selects the cookie profile** — Camofox stores one profile per user id, so this decides whose logins a run inherits | `default` |
| `CAMOFOX_SESSION` | groups tabs within that profile; does **not** select the profile or the authenticated session | `sluice` |
| `EDITOR` | the editor `job-sluice init` opens for the Judging Profile's prose questions | none — a blank answer keeps the shipped neutral default |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | the `anthropic` backend's credentials/endpoint | none / provider default |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | the `openai` backend | none / provider default |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | the `deepseek` backend | none / provider default |

The `claude-max` role needs **no** API key — it shells out to the flat-rate `claude` CLI,
locally or over SSH (`claude_max_host`/`claude_max_path`, or `compose_host`/
`compose_claude_path` under `cv:`). A keyless *fallback* backend is a sanctioned degrade
(`doctor` reports it `degraded`, `--strict` fails on it); a keyless *primary* backend is `dead`.

A leading `~` in an explicitly-set path (env var or config key) is expanded. `cv:`'s five
working directories (`render_home`, `output_dir`, `served_dir`, plus `apply:`'s
`camofox_upload_dir`/`camofox_cv_dir`) and the `render_script` path are the deliberate
exceptions: they name a workspace you're standing in, not per-system state, so they stay
cwd-relative and are never `~`-expanded or XDG-resolved.

## Camofox

`ingest run`/`ingest test-source` need a persistent, separate headless-browser server
reachable at `CAMOFOX_URL` — this repository does not bundle one. See
[jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) to stand one
up. `triage` and `cv` also reach it, but only lazily, on a dossier cache miss (the two share
one cache — see `docs/ARCHITECTURE.md`); every other command does not need Camofox.

## Upgrading from a pre-XDG install

If `seen.db`, `track-seen.db`, `sluice_health.json`, `sluice_disabled.json`,
`triage-audit.jsonl`, `google_token.json` or `dossiers/` still live next to wherever you used
to run the old command from, `job-sluice` never moves them for you — it prints the exact `mv`
commands for each, including any companion files a store has to move alongside it. For the two
dedup databases (`seen.db`, `track-seen.db`) it **refuses to run** until you've moved them,
because starting from an empty dedup set can re-create leads you'd merged away, or apply to the
same job twice. `ingest` only refuses on a run that would actually write dedup state —
`--dry-run` and `--sink json` still work; every `track` command refuses, dry runs included.
This only applies where sluice picked the location itself: naming a path yourself (an env var
or a config key) is used as given, with no warning and no refusal, because there's nothing to
migrate from.
