"""sluice command-line interface. Installed as the `job-sluice` console script (the PyPI
distribution and the command are both `job-sluice`; the import package stays `sluice`).

  job-sluice ingest list-sources [--health]     list plugins + enabled/health state
  job-sluice ingest run [--all|--source ID ...] [--sink vault|json] [--dry-run]
  job-sluice ingest test-source ID [--raw]      run ONE source live (fixture capture)
  job-sluice ingest enable|disable ID           persist an operator on/off override
  job-sluice health                             per-source baseline + retire state
  job-sluice mcp serve [--write]                run the MCP server (stdio transport)
  job-sluice doctor [--offline] [--strict]      preflight backends + renderer/store/gates

`run` and `test-source` drive the live Camofox session; the rest are offline.
enable/disable persist to a small JSON overlay (SLUICE_DISABLED) so an operator
override survives across runs, on top of config + runtime auto-retire.
"""
import argparse
import json
import os
import sys
from dataclasses import asdict

# Optional: shell-completion support (`job-sluice[completion]`). Guarded like every other opt-in
# runtime dependency (yaml, jinja2, weasyprint, the Google libs) -- a bare install must not
# need this. `argcomplete.autocomplete(parser)` (called in `main`, below) is itself a no-op
# unless a shell's completion hook has set `_ARGCOMPLETE` in the environment, so calling it
# unconditionally when the import succeeds costs nothing on an ordinary invocation.
try:
    import argcomplete
except ImportError:  # pragma: no cover - exercised by not having the extra installed
    argcomplete = None

from sluice import __version__
from sluice.core.config import load_config
from sluice.core.health import HealthStore
from sluice.core.log import get_logger, notify
from sluice.core.paths import resolve
from sluice.ingest import sources as registry

_log = get_logger("cli")


# Resolve the disabled-overlay path lazily (each call) so env overrides - and tests'
# monkeypatch - win; an import-time snapshot would be unpatchable. The health path's
# equivalent resolution lives solely in HealthStore.__init__ (sluice/core/health.py) --
# see cmd_health/cmd_list_sources.
def _disabled_path() -> str:
    return resolve(env_var="SLUICE_DISABLED", config_value="", kind="state",
                   name="sluice_disabled.json")


# ── operator on/off overlay ──────────────────────────────────────────────────
def _load_disabled() -> set:
    """The operator's disabled-source ids. RAISES if the overlay exists but is unusable.

    MISSING -> nothing disabled, the ordinary state. `lexists`, not `exists`: a DANGLING
    SYMLINK is not an absent file, and treating it as one sends `_save_disabled` writing
    through the link.

    Anything else raises, and the raise is the point. THE CRITERION IS WHAT THE CALLER
    DOES WITH THE ANSWER, not whether it writes this file back: a caller that only
    REPORTS the answer takes `_disabled_or_warn`; one that ACTS on it or WRITES it back
    takes this function. Writing back is the worst case -- a swallowed read there rebuilds
    the overlay from an empty set and destroys every decision the operator made, reporting
    success -- and it is also the only one of the three a guard can enumerate, which
    `test_every_overlay_writer_reads_through_the_raising_loader` does.

    The shape is validated for the reason `_merge_denylist` exists in track/config.py:
    `set(json.load(f))` over a dict yields its KEYS and over a string yields its
    CHARACTERS, so a malformed overlay would silently become a nonsense set of source ids
    rather than an error.
    """
    path = _disabled_path()
    if not os.path.lexists(path):
        return set()
    if not os.path.exists(path):
        # Present as a link, absent as a file. Saying so beats the bare
        # "No such file or directory" the open would raise for a path we just proved
        # exists -- the sibling in seendb.py words it the same way.
        raise OSError(
            f"the disabled-sources overlay at {path} is a symlink to something that does "
            f"not exist. Fix or remove the link; removing it re-enables every source you "
            f"had turned off.")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        bad = f"got {type(data).__name__}"
    else:
        # Name the ELEMENT's type, not the container's: reporting "got list" for
        # `["reed", 1]` describes the thing that was RIGHT and hides the thing that
        # was wrong.
        offenders = [x for x in data if not isinstance(x, str)]
        bad = (f"got a list containing {type(offenders[0]).__name__}" if offenders
               else "")
    if bad:
        raise ValueError(
            f"the disabled-sources overlay at {path} must be a JSON list of source ids, "
            f"{bad}. Fix or delete it; deleting it re-enables every source you had "
            f"turned off.")
    return set(data)


def _disabled_or_warn() -> set:
    """`_load_disabled` for callers that only REPORT the answer: warn, and treat nothing
    as disabled.

    See `_load_disabled` for the criterion. #80 newly made this reachable:
    the file used to sit in the cwd and now resolves per-system, so an upgrader's overlay
    is at the old location. `paths.resolve` warns about the move, but that notice names
    the file and not the consequence -- and this is the consequence.

    Warn rather than refuse, deliberately: a re-enabled source costs a wasted scrape and
    is fixed by disabling it again, a different order of harm from the dedup stores
    (a duplicate application, irreversible). It must not be SILENT, though.
    """
    try:
        return _load_disabled()
    except (OSError, ValueError) as e:
        _log.warning(
            "could not read the disabled-sources overlay at %s (%s): treating every "
            "source as ENABLED for this run. Any source you disabled will be scraped.",
            _disabled_path(), e)
        return set()


def _save_disabled(ids: set) -> None:
    path = _disabled_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f)


def _is_enabled(src, config, disabled: set) -> bool:
    return (
        config.source(src.id).enabled
        and getattr(src, "enabled", True)
        and src.id not in disabled
    )


def _selected(args, config, disabled) -> list:
    if getattr(args, "source", None):
        chosen = [registry.get(sid) for sid in args.source]
    else:  # --all or default: every registered source
        chosen = registry.all_sources()
    return [s for s in chosen if _is_enabled(s, config, disabled)]


# ── commands ─────────────────────────────────────────────────────────────────
def cmd_list_sources(args, config) -> int:
    disabled = _disabled_or_warn()   # read-only: never writes the overlay back
    health = HealthStore() if getattr(args, "health", False) else None
    for src in sorted(registry.all_sources(), key=lambda s: s.id):
        state = "enabled" if _is_enabled(src, config, disabled) else "disabled"
        line = f"{src.id:16} {src.kind:9} {state}"
        if health is not None:
            line += f"  baseline={health.baseline(src.id):.0f}"
            if health.should_retire(src.id):
                line += " RETIRE"
        print(line)
    return 0


def cmd_enable(args, config) -> int:
    disabled = _load_disabled()
    disabled.discard(args.id)
    _save_disabled(disabled)
    print(f"enabled {args.id}")
    return 0


def cmd_disable(args, config) -> int:
    disabled = _load_disabled()
    disabled.add(args.id)
    _save_disabled(disabled)
    print(f"disabled {args.id}")
    return 0


def cmd_leads_reconcile(args, config) -> int:
    from sluice.core.app import Sluice, StoreHasNoLayout
    # From the PURE leads module, never the concrete vault store: importing the store here would
    # put it on the import path of the one command that explicitly handles a store WITHOUT a
    # layout, which is the coupling `Sluice._layout_store`'s getattr exists to avoid.
    from sluice.core.leads import EMPTY_RECONCILE_REPORT

    if not config.lead_layout:
        # NOT "0 to move": that is indistinguishable from "nothing is out of place", and would let
        # a user believe a knob they never configured is filing their vault. The `lead_ttl_days is
        # unset` arm in cmd_leads_expire is the same sentence for the same reason.
        # Worded about the KNOB, not about "the flat layout is in use": the latter states a
        # store's filing behaviour, and this arm runs before any store is resolved -- for a store
        # with no layout at all it would be simply false.
        print("reconcile: lead_layout is unset, so there is nothing to reconcile "
              "(set lead_layout: active_archive to opt in)", file=sys.stderr)
        # Still emit a document on --json: a consumer parsing stdout must not have to distinguish
        # "no output" from "empty result". From the store's own constant, never a second literal.
        if args.json:
            print(json.dumps(EMPTY_RECONCILE_REPORT))
        # An --apply that wrote nothing is a failure, not a success -- the silent no-op this
        # report-first command is shaped to avoid.
        return 1 if args.apply else 0

    try:
        # `reconcile_report()` for the read path, matching `dedupe_report`/`expire_report`. Calling
        # `reconcile(apply=False)` for both left the facade's report method with no production
        # caller -- the same test-only surface this branch deletes from triage/prompt.py.
        app = Sluice(config)
        rep = app.reconcile(apply=True) if args.apply else app.reconcile_report()
    except StoreHasNoLayout as exc:
        # A sentence and rc 2, not a traceback. `main` catches only ValueError (around
        # load_config) and then calls args.func bare, so without this the RuntimeError reaches the
        # user as a stack trace -- which is exactly what the capability check exists to avoid, so
        # leaving it unhandled would make the guard's justification pure prose.
        print(f"job-sluice: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rep))
    else:
        # The human REPORT goes to STDOUT, the trailing summary to stderr. Both sibling passes
        # already do exactly this -- `leads dedupe` prints its cluster lines to stdout and
        # "no duplicate clusters" to stderr, and `leads expire` prints its `[kind] slug ...` rows
        # to stdout and its count line to stderr. (An earlier draft of this comment claimed the
        # two precedents DISAGREED and that this was a tie-break; measured, they agree. The
        # behaviour was right and the stated reason was invented.) It keeps
        # `job-sluice leads reconcile | grep` useful and matches --json, already on stdout.
        verb = "moved" if args.apply else "would move"
        for _slug, src, dst in rep["moves"]:
            print(f"reconcile: {verb} {src} -> {dst}")
        for slug, refs in sorted(rep["ambiguous"].items()):
            print(f"reconcile: {slug}: NOT moved -- {len(refs)} notes claim this slug "
                  f"({', '.join(refs)}); merge them (job-sluice leads dedupe) or rename one")
        for slug, raw in rep["unknown"]:
            print(f"reconcile: {slug}: left in place -- status {raw!r} is not canonical")
        for slug, where in rep["user_filed"]:
            print(f"reconcile: {slug}: left in place -- {where}/ is yours, not sluice's")
        for slug, dst in rep["collisions"]:
            print(f"reconcile: {slug}: NOT moved -- {dst} is taken (a numeric suffix would "
                  f"change the slug, which is the identity)")
        for slug, err in rep["skipped"]:
            print(f"reconcile: {slug}: NOT moved -- {err}")
        print(f"reconcile: layout={rep['layout']} {verb}={len(rep['moves'])} "
              f"in_place={rep['in_place']} ambiguous={len(rep['ambiguous'])} "
              f"unknown={len(rep['unknown'])} user_filed={len(rep['user_filed'])} "
              f"collisions={len(rep['collisions'])} skipped={len(rep['skipped'])}"
              f"{'' if args.apply else ' (report only -- pass --apply to move)'}",
              file=sys.stderr)
    if not args.apply:
        return 0
    # Only the buckets where a MOVE was attempted-or-owed and did not happen. `unknown` and
    # `user_filed` are states this pass is DESIGNED to leave alone, so counting them would make a
    # correct run exit 1 forever. `ambiguous` DOES count: the user asked for a write over a set
    # including a lead nothing was written for, which is precisely what they must notice.
    return 1 if (rep["collisions"] or rep["skipped"] or rep["ambiguous"]) else 0


def cmd_health(args, config) -> int:
    from sluice.core.app import Sluice

    for src in Sluice(config).health_report():
        flag = " RETIRE" if src.should_retire else ""
        # A source stuck on a NAMED failure is not retired (an operator action fixes it), so
        # without this line it would look merely quiet. This is the cumulative signal that
        # replaces the RETIRE flag for that case -- the one an operator reads days later.
        if src.broken_reason:
            flag += f" BROKEN reason={src.broken_reason} x{src.broken_runs}"
        print(f"{src.id:16} baseline={src.baseline:.0f} recent={src.recent}{flag}")
    return 0


def cmd_run(args, config) -> int:
    # Imported here so offline commands (and their tests) never touch Camofox.
    from sluice.core.app import Sluice

    # ACTS on the answer, so it refuses (see `_load_disabled`). This does not hard-fail
    # upgraders: an overlay still at the pre-#80 location leaves the resolved path
    # missing, which is the abstain arm. Measured.
    try:
        disabled = _load_disabled()
    except (OSError, ValueError) as e:
        _log.error("%s", e)
        return 1
    srcs = _selected(args, config, disabled)
    if not srcs:
        _log.warning("no enabled sources selected")
        return 1
    report = Sluice(config).ingest(srcs, dry_run=args.dry_run, json_sink=(args.sink == "json"))
    _print_report(report)
    if report.degraded:
        _notify_reporting(_format_degraded(report), config=config,
                          label="degraded-sources",
                          unconfigured_note=" -- the degraded sources are listed above")
    return 0


def cmd_test_source(args, config) -> int:
    from sluice.core.app import Sluice
    from sluice.ingest.base import Ctx, searches_for

    src = registry.get(args.id)
    ctx = Ctx(camofox=Sluice(config).fetcher(), config=config)
    search = searches_for(src, config)[0]  # honour a configured override, else built-in
    raw = src.fetch(ctx, search)
    if args.raw:  # print ONLY the raw payload, for redirecting into a golden fixture
        print(json.dumps(raw, indent=2))
        return 0
    leads = src.parse(raw, search)
    print(f"# {src.id}: {len(leads)} leads from '{search.label}'", file=sys.stderr)
    print(json.dumps([asdict(lead) for lead in leads], indent=2))
    return 0


def _print_report(report) -> None:
    for r in report.sources:
        print(f"  {r.source_id:16} status={r.status} fetched={r.fetched} "
              f"fresh={r.fresh} drift={r.drift or '-'}"
              f"{' RETIRED' if r.retired else ''}", file=sys.stderr)
    w = report.written
    # Sparse: merged/refused (#5) and merged_away/merged_away_unproven (#81) are printed
    # only when non-zero, and every read uses .get so a clean run — whose sink never
    # adds those keys — does not KeyError.
    parts = [f"{w.get('created', 0)} created", f"{w.get('updated', 0)} updated"]
    if w.get("merged"):
        parts.append(f"{w['merged']} merged")
    if w.get("refused"):
        parts.append(f"{w['refused']} refused")
    if w.get("merged_away"):
        parts.append(f"{w['merged_away']} merged-away")
    if w.get("merged_away_unproven"):
        parts.append(f"{w['merged_away_unproven']} merged-away (unproven)")
    parts.append(f"{w.get('skipped', 0)} skipped")
    print("written: " + ", ".join(parts), file=sys.stderr)


def _notify_reporting(body, *, config, label, unconfigured_note=None) -> str:
    """`notify`, then SAY what happened to it. Every sub-app goes through this.

    `notify` gained a three-state outcome because `_telegram_sender` swallows transport
    errors, so a revoked token, a wrong chat_id, a 4xx or a dead network all read as delivered
    -- on exactly the run whose alert mattered. Track consumed the new outcome and ingest,
    triage and cv did not, which is the same fix-one-instance shape the outcome was added to
    close. One helper rather than four call sites, because four copies is how three of them
    end up saying different things.

    `unconfigured_note` is opt-in: for track's failure alert, "no token configured" means the
    alert channel does not exist and the operator should know. For triage's and cv's routine
    success digests, printing that on every run would be noise beside the local digest they
    already print, so they pass None and report only a genuine delivery FAILURE.
    """
    outcome = notify(body, config=config)
    if outcome == "failed":
        print(f"  WARNING: the {label} notification could NOT be delivered (Telegram "
              f"rejected it or was unreachable) -- check the token, the chat id and the log.",
              file=sys.stderr)
    elif outcome == "unconfigured" and unconfigured_note:
        print(f"  (no notification sent: no Telegram token configured{unconfigured_note})",
              file=sys.stderr)
    return outcome


def _format_degraded(report) -> str:
    """One line per unhealthy source (drifted, errored, or auto-retired);
    healthy sources are omitted. Used as the Telegram notify body."""
    lines = []
    for r in report.sources:
        if not (r.drift or r.retired or r.status == "error"):
            continue
        reason = r.drift or ("error" if r.status == "error" else "ok")
        lines.append(f"- {r.source_id}: {reason}{' [RETIRED]' if r.retired else ''}")
    return "job-sluice: degraded sources this run:\n" + "\n".join(lines)


# ── triage ───────────────────────────────────────────────────────────────────
def cmd_triage_normalize(args, config) -> int:
    from sluice.core.app import Sluice
    summary = Sluice(config).normalize_statuses(dry_run=args.dry_run)
    print(f"status normalize: changed={summary['changed']} "
          f"unchanged={summary['unchanged']} "
          f"conflicts={summary.get('conflicts', [])} "
          f"skipped={summary.get('skipped', [])} "
          f"unknown={sorted(set(summary['unknown']))}"
          f"{' (dry-run)' if args.dry_run else ''}")
    return 0


def cmd_leads_dedupe(args, config) -> int:
    from sluice.core.app import Sluice
    app = Sluice(config)
    if args.merge:
        for cid, outcome in app.dedupe_merge(args.merge):
            print(f"dedupe: {cid} {outcome}", file=sys.stderr)
        return 0
    report = app.dedupe_report()
    if args.json:
        print(json.dumps([{
            "id": c.id, "conflict": c.conflict,
            "survivor": (c.survivor.slug if c.survivor else None),
            "members": [{"slug": n.slug, "status": n.status, "url": n.fm.get("url", "")}
                        for n in c.members],
            "flagged_losers": [n.slug for n in c.flagged_losers],
        } for c in report]))
    else:
        for c in report:
            tag = " CONFLICT" if c.conflict else ""
            flag = " ⚑losers" if c.flagged_losers else ""
            print(f"[{c.id}]{tag}{flag} survivor={c.survivor.slug if c.survivor else '-'}")
            for n in c.members:
                print(f"    {n.status:12} {n.slug}  {n.fm.get('url','')}")
        if not report:
            print("dedupe: no duplicate clusters", file=sys.stderr)
    return 0


def cmd_leads_dismiss(args, config) -> int:
    from sluice.core.app import Sluice

    result = Sluice(config).dismiss_lead(lead=args.lead, reason=args.reason)
    if result.outcome == "not_found":
        print(f"leads dismiss: no lead matching '{args.lead}'", file=sys.stderr)
        return 1
    if result.outcome == "ambiguous":
        print(f"leads dismiss: ambiguous: {len(result.candidates)} notes claim the slug "
              f"'{args.lead}' ({' | '.join(result.candidates)}) -- rename or merge them "
              f"first (job-sluice leads dedupe)", file=sys.stderr)
        return 1
    if result.outcome == "refused_signoff_hold":
        print(f'leads dismiss: {result.slug}: refused (sign-off hold) -- resolve it '
              f'first: job-sluice cv signoff --lead "{result.slug}" --discard',
              file=sys.stderr)
        return 1
    if result.outcome == "refused_status":
        print(f"leads dismiss: {result.slug}: refused (status={result.status})",
              file=sys.stderr)
        return 1
    # dismissed | unchanged both print and exit 0 -- unchanged is a legitimate
    # idempotent same-day-repeat outcome (decision 5), not a failure.
    print(f"leads dismiss: {result.slug}: {result.outcome}", file=sys.stderr)
    return 1 if result.outcome == "conflict" else 0


def cmd_leads_expire(args, config) -> int:
    from sluice.core.app import Sluice

    if config.lead_ttl_days <= 0:
        # NOT "0 stale": that is indistinguishable from "nothing is stale", and would let
        # a user believe a knob they never configured is protecting them.
        print("expire: lead_ttl_days is unset (0) -- staleness is off, nothing to report",
              file=sys.stderr)
        # Still emit a document on --json: a consumer that parses stdout must not have to
        # distinguish "no output" from "empty result", and the knob-ON empty case prints
        # `[]`. `leads dedupe --json` always prints one too.
        if args.json:
            print(json.dumps([]))
        # And a NAMED --expire that wrote nothing is a failure, not a success. Exiting 0
        # here is the silent no-op the no-match exit below exists to prevent.
        return 1 if args.expire is not None else 0

    app = Sluice(config)
    if args.expire is not None:      # NOT truthiness: [] is the bulk case and is falsy
        outcomes = app.expire(slugs=args.expire)
        counts = {}
        for slug, outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "refused-signoff":
                # Print the way OUT, not just the refusal. Without it this is a permanent
                # no with no stated remedy, on every run.
                print(f'expire: {slug}: refused (sign-off hold) -- resolve it first: '
                      f'job-sluice cv signoff --lead "{slug}" --discard', file=sys.stderr)
            elif outcome != "dismissed":
                print(f"expire: {slug}: {outcome}", file=sys.stderr)
        print("expire: " + ", ".join(f"{n} {o}" for o, n in sorted(counts.items())),
              file=sys.stderr)
        # Any outcome where the write did not happen exits non-zero: a silent no-op is the
        # exact failure mode this report-first command is shaped to avoid. `conflict` and
        # `unreadable` belong here for the same reason `no-match` does -- the user asked
        # for a write and did not get one. `skipped` too: it means the lead left the
        # triage lifecycle mid-sweep, which is precisely the case a caller must notice.
        # `ambiguous` (#1) is the same class and was missed when `expire` gained it: two
        # stale notes claim the named slug, so nothing was written and the user must act.
        # This set is the whole reason a new outcome is not free -- adding one to `expire`
        # without adding it here exits 0 on a lead nobody dismissed.
        _FAILED = {"no-match", "conflict", "unreadable", "skipped", "ambiguous"}
        return 1 if any(o in _FAILED for _, o in outcomes) else 0

    report = app.expire_report()
    if args.json:
        print(json.dumps([{
            "slug": r.slug, "status": r.status, "last_seen": r.last_seen,
            "first_seen": r.first_seen, "days": r.days,
            "flagged": r.flagged, "refused": r.refused,
        } for r in report]))
        return 0
    for r in report:
        kind = "held " if r.refused else "stale"
        flags = ("  " + " ".join("⚑" + f for f in r.flagged)) if r.flagged else ""
        held = "  sign-off hold" if r.refused else ""
        print(f"[{kind}] {r.slug}  {r.days}d  {r.status}  "
              f"first_seen {r.first_seen}{flags}{held}")
    refused = sum(1 for r in report if r.refused)
    extra = f" ({refused} refused: sign-off hold)" if refused else ""
    print(f"expire: {len(report)} stale{extra}, 0 written (--expire to apply)",
          file=sys.stderr)
    return 0


def cmd_triage_run(args, config) -> int:
    from sluice.core.app import Sluice

    statuses = tuple(s.strip() for s in (args.status or "new,research").split(",") if s.strip())
    report = Sluice(config).triage(statuses=statuses, limit=args.limit,
                                   dry_run=args.dry_run, no_llm=args.no_llm,
                                   backend_role=args.backend)
    print(f"triage: {report.counts} judged={report.judged} "
          f"resolved={report.resolved} llm_calls={report.llm_calls} "
          f"backend={report.backend} failures={len(report.failures)}", file=sys.stderr)
    for msg in report.failures:
        print(f"  {msg}", file=sys.stderr)
    _notify_reporting(f"job-sluice triage: {report.counts} (backend {report.backend})",
                      config=config, label="triage-summary")
    return 0


# ── backend construction ─────────────────────────────────────────────────────
# `--backend` names a ROLE (auto/primary/fallback), not a provider; the config
# decides which provider fills each role. Role resolution -- and the provider-
# construction that used to live here as per-command wrappers -- now lives
# entirely in Sluice.backend(), which every cmd_* below calls via Sluice(config).
# This literal is KEPT here so argparse still has its `choices` without importing
# the moved role/alias tables. MUST stay in sync with Sluice._BACKEND_ROLES +
# Sluice._BACKEND_ALIASES (sluice/core/app.py) -- those own the roles/aliases,
# this is only argparse's copy of the same choices.
_BACKEND_CHOICES = ["auto", "primary", "fallback", "claude-max", "deepseek"]
_BACKEND_HELP = (
    "which configured backend to use: auto (primary, falling back), primary, or "
    "fallback. claude-max/deepseek are deprecated aliases for primary/fallback.")


# ── cv ────────────────────────────────────────────────────────────────────
def cmd_cv_run(args, config) -> int:
    from sluice.core.app import Sluice

    results = Sluice(config).compose_cv(
        include_stale=args.include_stale,
        lead=args.lead, all_shortlist=args.all_shortlist, limit=args.limit,
        dry_run=args.dry_run, no_serve=args.no_serve, backend_role=args.backend)
    if not results and not args.all_shortlist:
        print(f"cv: no shortlist lead matching '{args.lead}'", file=sys.stderr)
        return 1
    # #99: cv.name is one root-config value, not per-lead, so a run that reaches
    # ANY skipped-config result reached ALL of them the same way -- checking
    # presence is equivalent to checking every result, and simpler. Scoped to
    # BOTH --lead and --all-shortlist (unlike the ambiguous branch below, which is
    # genuinely per-lead): nothing composed at all here, for a reason the user can
    # fix in one place, so both call shapes exit non-zero with the same actionable
    # line rather than a batch silently reporting zero rendered CVs.
    if any(r.status == "skipped-config" for r in results):
        print("cv: cv.name is still the shipped placeholder 'Your Name' -- set "
              "it in the cv: block of sluice.yaml before composing (it becomes "
              "the PDF's headline)", file=sys.stderr)
        return 1
    # A named --lead that resolved to two notes composed for NEITHER, so it exits non-zero
    # for the same reason the no-match branch above does: the user asked for a CV and did
    # not get one. Falling through to the per-result loop would print a `skipped-ambiguous`
    # row among the ordinary ones and still exit 0 -- the exact gap `leads expire` shipped
    # with when it gained an `ambiguous` outcome cli.py's _FAILED set did not name (#1).
    # Scoped to the single-lead path, mirroring the line above: --all-shortlist reports the
    # twins it skipped and legitimately composed for every other lead, so its exit code is
    # not this branch's business.
    ambiguous = [str(r.lead) for r in results if r.status == "skipped-ambiguous"]
    if ambiguous and not args.all_shortlist:
        print(f"cv: ambiguous: {' | '.join(ambiguous)} -- retype a longer fragment "
              f"than '{args.lead}'", file=sys.stderr)
        return 1

    for r in results:
        print(f"cv: {r.status} {r.lead} served={r.served} "
              f"violations={len(r.violations)} audit_flags={len(r.audit_flags)} "
              f"dossier_failed={r.dossier_failed}",
              file=sys.stderr)
    # #18: a blocked/failed dossier fetch does not stop composition (cv/engine.py's
    # `except` proceeds with jd="" so the fabrication gate still runs), so "rendered"
    # alone would silently hide that some of these CVs were composed against no real
    # job description at all. A dedicated summary line makes that countable without
    # changing cv's control flow, which is a bigger change than this guard should carry.
    blind = sum(1 for r in results if r.dossier_failed)
    if blind:
        print(f"cv: {blind} CV(s) composed blind (dossier fetch failed)", file=sys.stderr)
    rendered = [r for r in results if r.status == "rendered"]
    if rendered:
        _notify_reporting("job-sluice cv: " + "; ".join(
            f"{r.served} (audit flags: {len(r.audit_flags)})" for r in rendered),
            config=config, label="cv-summary")
    return 0


def cmd_cv_signoff(args, config) -> int:
    from sluice.core.app import Sluice

    confirm = None
    if not args.discard and not args.yes:
        # Review the flagged claims before promoting a possibly-fabricated CV to send-ready;
        # only the candidate knows whether an aspirational claim is true (#60). The prompt
        # lives HERE (the CLI), passed into sign_off_cv as a callback, so the app layer does
        # no I/O and still resolves the lead exactly once (no peek/execute divergence).
        def confirm(slug, pending, claims):
            print(f"cv signoff: {slug} has {len(claims)} unsupported claim(s):", file=sys.stderr)
            for c in claims:
                print(f"  - {c}", file=sys.stderr)
            print(f"served CV: {pending}", file=sys.stderr)
            return input(f"sign off {slug}? [y/N] ").strip().lower() in ("y", "yes")

    result = Sluice(config).sign_off_cv(lead=args.lead, accept=not args.discard, confirm=confirm)
    if result.outcome == "not_found":
        print(f"cv signoff: no shortlist lead matching '{args.lead}'", file=sys.stderr)
        return 1
    if result.outcome == "ambiguous":
        # #131: candidates is now a slug list (decision 15), not the old joined-ref
        # string -- a deliberate, stated wording change from the pre-#131 CLI output.
        # sign_off_cv resolves by SUBSTRING (slug_matches), unlike dismiss_lead's exact
        # match, so "retype a longer fragment" genuinely helps when candidates are
        # DISTINCT slugs -- but when every candidate is the SAME slug (a collision from
        # the recursive scan, #1), no fragment can ever distinguish them; only renaming
        # or merging can (round-2 review finding).
        remedy = ("rename or merge them first (job-sluice leads dedupe)"
                 if len(set(result.candidates)) == 1
                 else f"retype a longer fragment than '{args.lead}'")
        print(f"cv signoff: ambiguous: {len(result.candidates)} notes match "
              f"({' | '.join(result.candidates)}) -- {remedy}", file=sys.stderr)
        return 1
    msg = {"nothing": "has nothing pending", "aborted": "aborted",
          "stale": "stale: confirmation no longer matches (something changed)"}.get(
        result.outcome, result.outcome)
    print(f"cv signoff: {result.slug} {msg}", file=sys.stderr)
    # cmd_leads_expire's `_FAILED` rule, applied here: an outcome where the write did
    # not happen exits non-zero, because the user named one lead and asked for one
    # write. `conflict` is a sustained write race (#16). `nothing` is `no-match`'s
    # shape one step further in. `stale` (#131) joins them: require_pending's fresh
    # comparison failed, so nothing was signed off and the #60 hold is still held.
    # Deliberately NOT members, each for a reason the word alone does not give:
    #   `collision` WROTE -- the hold is resolved and the lead IS send-ready.
    #   `aborted` wrote nothing, but the user declined the prompt themselves.
    _FAILED = {"nothing", "conflict", "stale"}
    return 1 if result.outcome in _FAILED else 0


# ── apply ────────────────────────────────────────────────────────────────────
def cmd_apply_prep(args, config) -> int:
    from sluice.core.app import Sluice
    from sluice.apply import packet

    app = Sluice(config)
    if args.all_shortlist:
        results = app.prep(all_shortlist=True, limit=args.limit,
                           include_stale=args.include_stale)
        for r in results:
            if r.status == "previewed":
                print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        eligible = sum(1 for r in results if r.status == "previewed")
        skipped = sum(1 for r in results if r.status == "skipped")
        print(f"apply-preview: eligible={eligible} skipped={skipped}", file=sys.stderr)
        return 0
    if args.dry_run:
        # Sluice.prep(dry_run=True) wraps select+packet as one PrepResult with
        # status "previewed"/"skipped" -- the engine's real vocabulary -- but the
        # CLI's dry-run wording predates that method and stays "dry-run", not
        # "previewed dry-run", so it is reproduced literally here rather than
        # printed from r.status.
        r = app.prep(lead=args.lead, dry_run=True,
                     include_stale=args.include_stale)[0]
        if r.status == "skipped":
            print(f"apply-prep: {args.lead} skipped ({r.reason})", file=sys.stderr)
            return 1
        print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        print(f"apply-prep: {args.lead} dry-run", file=sys.stderr)
        return 0
    r = app.prep(lead=args.lead, include_stale=args.include_stale)[0]
    if r.status == "staged":
        print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        print(f"apply-prep: {r.lead} staged", file=sys.stderr)
        return 0
    print(f"apply-prep: {r.lead} {r.status} ({r.reason})", file=sys.stderr)
    return 1


def cmd_apply_record(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).record(lead=args.lead, ats=args.ats, url=args.url,
                                dry_run=args.dry_run)
    if out["ok"]:
        f = out["fields"]
        print(f"apply-record: {args.lead} -> applied "
              f"(ats={f.get('ats', '(dropped)')} cv={f['applied_cv']})", file=sys.stderr)
        if out.get("url_dropped"):
            print("  applied_url dropped: --url was unsafe for frontmatter "
                  "and was not recorded", file=sys.stderr)
        if out.get("ats_dropped"):
            print("  ats dropped: the ATS name was unsafe for frontmatter "
                  "and was not recorded", file=sys.stderr)
        return 0
    if out["reason"] == "raced":
        # #131: distinct from the generic "refused (status=...)" wording below --
        # "raced" is not a status, and printing "status=raced" would misleadingly
        # suggest the lead's OWN status field literally reads "raced".
        print(f"apply-record: {args.lead} lost the write race (left shortlist "
              f"mid-write) -- retry", file=sys.stderr)
    else:
        print(f"apply-record: {args.lead} refused (status={out['reason']})", file=sys.stderr)
    return 1


# ── track ────────────────────────────────────────────────────────────────────
def cmd_track_run(args, config) -> int:
    from sluice.core.app import Sluice

    rep = Sluice(config).track(dry_run=args.dry_run, backend_role=args.backend)
    if rep.auth_error:
        print("track: google reauth needed (token refresh failed)", file=sys.stderr)
        return 1
    print(f"track: msgs={rep.msgs} classified={rep.classified} auto={rep.auto} "
          f"proposed={rep.proposed} calendar_added={rep.calendar_added} "
          f"failures={len(rep.failures)} open={len(rep.open_proposals)}", file=sys.stderr)
    # Each one NAMED, the way cmd_triage_run already does it. A bare count cannot tell a
    # one-off blip from a message that fails deterministically every run.
    for f in rep.failures:
        # FULL cause here: this is the operator's own terminal, and scrubbing the local
        # diagnostic would leave nowhere to debug from.
        # `.detail()`, explicitly. This is the operator's own stderr, so the full cause
        # belongs here -- but `str(f)` is the SAFE rendering now, so the local site has to
        # ask for the detail by name rather than getting it by default.
        print(f"  FAILED {f.detail()}", file=sys.stderr)
    if rep.calendar_assumed_tz:
        # Its own line rather than another key on the digest: this is a correctness warning,
        # not a statistic. The entries look ordinary in the calendar -- only the HOUR is a
        # guess -- so the one chance to notice is here.
        #
        # `would be` under --dry-run: like `calendar_added` beside it, the counter reports what
        # the run WOULD do (sync_event returns created/updated without writing), so the count
        # is right either way and only the verb has to change. Saying "booked" for a preview
        # would send the reader hunting for a calendar entry that does not exist.
        n = rep.calendar_assumed_tz
        verb = "would be booked" if args.dry_run else "booked"
        print(f"  WARNING: {n} calendar entr{'y' if n == 1 else 'ies'} {verb} from a DTSTART "
              f"with no usable timezone; the time is ASSUMED (see "
              f"track.calendar_assumed_timezone) and may be wrong. Check it against the invite.",
              file=sys.stderr)
    if rep.deadletter_error:
        # Consumed at exactly one place before this -- `app.py`'s `_save_lastrun` gate -- and
        # printed nowhere. A read-only dead-letter store (disk full, permissions, a stale
        # sidecar after a path migration) therefore silently declined to advance the watermark
        # on every run, widening the Gmail window without bound, while the digest looked
        # entirely normal and the command exited 0.
        print("  WARNING: the dead-letter store could not be written, so the lastrun "
              "watermark is being HELD. Every run will re-query a widening window until "
              "this is fixed.", file=sys.stderr)
    if rep.search_truncated:
        # The Gmail search capped out, so this run never saw some matching messages -- and
        # because Gmail returns newest-first, the ones it missed are the OLDEST.
        #
        # This HOLDS the watermark (`app.py`'s `_save_lastrun` gate). Advancing would move
        # `after:` to today and put the starved messages permanently out of reach; holding
        # keeps them queryable so narrowing the query actually recovers them next run.
        #
        # Deliberately does NOT say "shorten the lookback": `_gmail_query` consults
        # `gmail_lookback_days` only when there is no `.lastrun`, so in steady state that
        # knob does nothing and the advice would send the operator somewhere inert.
        print("  WARNING: the Gmail search hit its cap -- this run did NOT see every matching "
              "message, and the ones it missed are the OLDEST. The lastrun watermark is being "
              "HELD so they stay reachable; narrow track.gmail_extra_query and re-run.",
              file=sys.stderr)
    if rep.failures and not args.dry_run:
        # track was the ONLY sub-app that never notified (ingest, triage and cv all do), so
        # under cron a dropped interview invite reached nobody: the digest goes to a stderr
        # stream that is normally discarded. Exit code deliberately unchanged -- USAGE.md
        # documents exit 1 only for a reauth failure and cron alerting relies on it, and a
        # transient single-message failure failing every run is how an alert gets muted.
        #
        # `.safe()`, not the cause: this leaves the machine. The exception text can carry
        # message bodies, interview subjects, meeting urls, and the Gmail `q=` built from
        # gmail_extra_query. The id is what keeps it actionable.
        #
        # BOUNDED. #137 raised the message cap 50 -> 500, so a mass failure can produce 500
        # entries (~18KB), which Telegram rejects -- and `_telegram_sender` swallows every
        # send error by design, so the alert would silently not arrive on exactly the run
        # that most needed it. The count leads, so truncating the list loses nothing vital.
        _CAP = 10
        shown = [f.safe() for f in rep.failures[:_CAP]]
        extra = len(rep.failures) - len(shown)
        body = "job-sluice track: {} message(s) failed: {}".format(
            len(rep.failures), "; ".join(shown))
        if extra > 0:
            body += f"; ...and {extra} more (see the run digest)"
        # Through the SHARED helper, like the other three sub-apps. This block was written
        # inline first and the helper was extracted for ingest/triage/cv around it, which left
        # the outcome->message mapping in two places -- the exact duplication the helper
        # exists to prevent, in the sub-app it was written for.
        #
        # Gated on `not args.dry_run` above: a preview must not push an external message, and
        # it records no dead-letter rows either, which made the unconfigured note's durability
        # claim false on exactly that path.
        _notify_reporting(body, config=config, label="track-failure",
                          unconfigured_note=" -- these failures are recorded in the "
                                            "dead-letter store and re-surface every run")
    if rep.open_proposals:
        print("  OPEN PROPOSALS (awaiting action):", file=sys.stderr)
        for e in rep.open_proposals:
            tag = " (new)" if e.times_surfaced <= 1 else ""
            label = e.lead or e.candidates or "?"
            # The message-id is PRINTED, because `track dismiss --id` is the only lever
            # for a no-lead row (a classify failure, an unmatched proposal) and its label
            # renders as `?`. Without this the id existed only inside the SQLite file, so
            # the one documented way to clear those rows needed a value no command emitted
            # -- and they re-surface every run until someone acts on them.
            print(f"  [{e.first_seen} x{e.times_surfaced}{tag}] {label} <{e.message_id}>: "
                  f"{e.proposal} :: {e.hint}", file=sys.stderr)
    return 0


def cmd_track_confirm(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).track_confirm(lead=args.lead, to=args.to, when=args.when,
                                       dry_run=args.dry_run)
    if out["ok"]:
        print(f"track-confirm: {args.lead} {out['from']} -> {out['to']}", file=sys.stderr)
        if out.get("when_dropped"):
            # Mirrors apply-record's `url_dropped` line above, for the same reason and in the
            # same words. `engine.confirm` guards `--when` with `frontmatter_safe` and abstains
            # on the field; the abstention was produced and then read by nobody, so the
            # operator saw a clean success and never learned their date had not been recorded.
            # A guard whose result nothing consumes is the silent drop it was added to prevent.
            print("  interview_date dropped: --when was unsafe for frontmatter "
                  "and was not recorded", file=sys.stderr)
        return 0
    print(f"track-confirm: {args.lead} refused ({out['reason']})", file=sys.stderr)
    return 1


def cmd_track_dismiss(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).track_dismiss(message_id=args.id, lead=args.lead, dry_run=args.dry_run)
    verb = "would clear" if out["dry_run"] else "cleared"
    noun = "entry" if out["cleared"] == 1 else "entries"
    print(f"track-dismiss: {verb} {out['cleared']} {noun}", file=sys.stderr)
    return 0


# ── init ──────────────────────────────────────────────────────────────────────
def cmd_init(args, config, *, asker=None) -> int:
    """Scaffold a config and a Judging Profile (#8).

    The CONFIG destination is resolved before any question is asked, and when it already exists the
    questions that write only to it are skipped -- a wizard that interviews someone for five
    minutes and then says "config already exists" wasted their time to learn something it knew at
    the start. It used to do exactly that, and then report the discarded answers as live gates.

    The PROFILE destination cannot be preflighted the same way: it lives inside the vault, so it is
    not known until the vault question is answered. The profile interview is gated on it as soon as
    it IS known, which is the earliest honest point.
    """
    import dataclasses

    from sluice.core.app import Sluice
    from sluice.core.paths import config_file
    from sluice.core.protocols import CRITERIA_RELPATH
    from sluice.core.vault import DEFAULT_VAULT
    from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect,
                                    collect_profile, collect_sources)
    from sluice.onboard.plan import build_plan
    from sluice.onboard.questions import catalogue

    # `stores/vault.py:_make` is ENV-FIRST, so routing a --vault through the seam while $VAULT_DIR
    # is also set would write to the ENV path while this command's report named the flag. A
    # precedence rule would pick a winner silently; only the user knows which they meant.
    env_vault = os.environ.get("VAULT_DIR")
    if args.vault and env_vault and os.path.abspath(os.path.expanduser(env_vault)) != \
            os.path.abspath(os.path.expanduser(args.vault)):
        print("job-sluice init: --vault and $VAULT_DIR name different directories. Unset one, or pass "
              "the one you mean.", file=sys.stderr)
        return 2

    config_dest = config_file()
    config_exists = os.path.exists(config_dest)

    presets = {}
    vault_arg = args.vault or env_vault or config.vault_dir
    if vault_arg:
        presets["vault_dir"] = os.path.abspath(os.path.expanduser(vault_arg))

    if asker is None:
        # $EDITOR is resolved HERE and passed in, so the asker itself reads no environment and a
        # test can pin "no editor" without the developer's real one leaking into the run.
        asker = (TtyAsker(stdin=sys.stdin, stdout=sys.stdout, editor=os.environ.get("EDITOR"))
                 if not args.no_input and sys.stdin.isatty() else NoInputAsker(presets=presets))
    # Ask the ASKER, not the terminal. Deriving this from isatty() independently meant an injected
    # asker could not reach the half it governs: under pytest isatty() is False, so the board walk,
    # the profile interview and the .init-scaffold.md rescue were all structurally unreachable.
    interactive = asker.interactive

    # A preset must win over a prompt even on a TTY: someone who passed --vault has already
    # answered, and asking again invites a different answer to the same question.
    questions = tuple(q for q in catalogue(default_vault=DEFAULT_VAULT) if q.key not in presets)

    # When the config is already there, ask ONLY what the profile still needs. Every other question
    # writes exclusively to the config, so asking them was a 15-question interview whose answers
    # were then discarded -- and the report went on to describe them as live gates. Measured: a
    # second run printed "reject titles matching: ..." for a file it had just declined to touch.
    #
    # This is also what the docstring above has always CLAIMED: `config_exists` was computed before
    # the interview and not consulted until after it, so the preflight prevented nothing.
    if config_exists:
        questions = tuple(q for q in questions if q.key == "vault_dir")
        print(f"  exists  {config_dest}  (left alone -- skipping the config questions)")

    try:
        # Presets go through the QUESTION'S OWN parser, exactly as a typed answer does. `--vault`
        # previously bypassed `parse_path` entirely, so the two routes in could normalize
        # differently -- the same class of split that let the config and the store name different
        # vaults.
        by_key = {q.key: q for q in catalogue(default_vault=DEFAULT_VAULT)}
        answers = {k: by_key[k].parse(v) if k in by_key else v for k, v in presets.items()}
        answers.update(collect(asker, questions))
    except MissingAnswer as exc:
        print(f"job-sluice init: {exc}", file=sys.stderr)
        return 2

    vault_dir = answers["vault_dir"]
    if os.path.exists(vault_dir) and not os.path.isdir(vault_dir):
        print(f"job-sluice init: {vault_dir} is not a directory.", file=sys.stderr)
        return 2
    vault_created = not os.path.exists(vault_dir)

    # The store is built ONCE, before the preflight, and ASKED whether the profile is there. The
    # previous `os.path.exists(os.path.join(vault_dir, CRITERIA_RELPATH))` read store state through
    # the filesystem, which protocols.py's own comment on that constant forbids ("an opaque DOCUMENT
    # KEY, not a path -- nothing here may assume a filesystem"). The write was always safe (O_EXCL
    # makes never-clobber a property of the open), but a non-filesystem store would get
    # `profile_exists` wrong, mis-gate the interview, and then park the user's typed prose in a
    # `.init-scaffold.md` they never needed. (This used to say #1 would land that store; it does
    # NOT -- #1 is the vault's folder LAYOUT and adds no store at all. Reading through the seam is
    # right regardless of when a second implementation arrives.)
    # No mkdir here: `read_criteria` returns "" for a store that does not exist yet, so the
    # directory is still created inside the write block below, where an OSError is REPORTED rather
    # than raised uncaught -- and an abandoned interview leaves no empty vault behind.
    store = Sluice(dataclasses.replace(config, vault_dir=vault_dir)).store()
    profile_exists = bool(store.read_criteria())
    # DISPLAY ONLY, and a deliberate compromise. The write and the existence probe both go through
    # the seam; this join exists because the two arms that do NOT get a handle -- an abstain
    # (`write_document` returns "") and an OSError -- still have to name something to the user.
    # A second, non-filesystem store would render this wrong in those two lines.
    #
    # Not fixed by adding a `Store.display_location()`: that is API surface invented for one
    # implementation, which is the premature abstraction this codebase keeps removing (#1's
    # `reconcile_layout` declines the same thing, and `ensure_stfolder` was moved OUT of the
    # protocol for it). The trigger is a SECOND STORE with a concrete opinion about what a user
    # should be shown -- not any particular issue number. #1 was named here and was the wrong
    # guess: it ships the vault's folder layout and no store at all.
    profile_dest = os.path.join(vault_dir, CRITERIA_RELPATH)

    profile_answers = {}
    sources = {}
    # `and not config_exists`: the board walk writes ONLY into plan.config_text, exactly like the
    # preference questions above. Gating those and not this left a second interactive run still
    # asking for board ids, search labels and URLs -- then discarding all of it, rc 0, no report.
    # Same failure the .init-scaffold rescue exists to prevent for the profile.
    if interactive:
        # Each interview is gated on the artefact IT writes. `collect_sources` feeds only
        # `plan.config_text`, so it is skipped when the config exists; `collect_profile` feeds only
        # `plan.profile_text` and has no stake in the config at all. Nesting the second inside the
        # first meant the common second run -- config from run one, profile still missing because
        # the user abandoned it or the vault moved -- silently skipped the prose interview and wrote
        # a bare scaffold without ever asking.
        if not config_exists:
            sources = collect_sources(asker, [s.id for s in registry.all_sources()])
        if not profile_exists:
            profile_answers = collect_profile(asker)

    plan = build_plan(answers, profile_answers=profile_answers, sources=sources)

    written, skipped, failed = [], [], []

    if config_exists:
        skipped.append(config_dest)
    else:
        try:
            # INSIDE the try: an OSError here (unwritable parent, or a relative $SLUICE_CONFIG whose
            # dirname is "") must become this command's own FAILED report, not an uncaught traceback.
            # Reproduced with SLUICE_CONFIG=sluice.local.yaml, where dirname is "".
            parent = os.path.dirname(config_dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            # "x": an exclusive create cannot truncate a config a concurrent shell just wrote.
            # Never-clobber is a property of the open, not of the check above it.
            with open(config_dest, "x", encoding="utf-8") as fh:
                fh.write(plan.config_text)
            written.append(config_dest)
        except FileExistsError:
            skipped.append(config_dest)
        except OSError as exc:
            failed.append(f"{config_dest}: {exc}")

    try:
        # Through the STORE SEAM, not Vault(...) directly: the profile is a store-managed document,
        # and the seam is the contract regardless of how many implementations exist today. (#1 was
        # named here as the thing that would make a second store real; it does not -- it is the
        # vault's folder layout.) The returned HANDLE is what
        # the report names, since that is what the contract says a caller may show a user.
        os.makedirs(vault_dir, exist_ok=True)
        handle = store.write_document(CRITERIA_RELPATH, plan.profile_text, only_if_absent=True)
        if handle:
            written.append(handle)
        else:
            skipped.append(profile_dest)
        if not handle and profile_answers:
            # The user typed prose into an interview and the profile turned up already there. Do
            # NOT overwrite it, and do not silently bin what they wrote: park it beside the real
            # one and say so.
            spare = CRITERIA_RELPATH.replace(".md", ".init-scaffold.md")
            if store.write_document(spare, plan.profile_text, only_if_absent=True):
                written.append(os.path.join(vault_dir, spare))
            else:
                # The spare exists too (a second interactive run). Reporting this is the whole
                # point: dropping through left the user's five typed answers discarded in silence
                # with rc 0, directly under a comment saying not to. `failed` rather than
                # `skipped`, because something the user typed was genuinely lost.
                failed.append(f"{os.path.join(vault_dir, spare)}: already exists, so the answers "
                              f"you just typed were NOT saved -- copy them out of the terminal, or "
                              f"move that file aside and re-run")
    except OSError as exc:
        failed.append(f"{profile_dest}: {exc}")

    for path in written:
        print(f"  wrote   {path}")
    for path in skipped:
        print(f"  exists  {path}  (left alone)")
    for line in failed:
        print(f"  FAILED  {line}", file=sys.stderr)

    # Reported from what is ON DISK now, not from the pre-flight intent: when the vault write
    # failed, `vault_created` is still True and the old wording announced a directory that does
    # not exist.
    if vault_created and os.path.isdir(vault_dir):
        print(f"\ncreated a new vault directory at {vault_dir}")
        print("if you meant an existing one, re-run with --vault pointing at it")
    elif os.path.isdir(vault_dir):
        print(f"\nusing the existing vault at {vault_dir}")
    else:
        print(f"\nno vault directory at {vault_dir} -- see the FAILED line above", file=sys.stderr)

    # ONLY when the config was actually written. `plan.notes` is derived from the ANSWERS, not from
    # what landed, so printing it after an abstain told the user their gates were live when the file
    # on disk had never heard of them.
    if plan.notes and config_dest in written:
        print("\nYour config will:")
        for note in plan.notes:
            print(f"  {note}")

    print("\nNext:")
    print("  1. fill in the headings in your Judging Profile")
    print("  2. job-sluice ingest list-sources --health")
    print("  3. job-sluice triage run --no-llm")

    # Nothing is rolled back on a partial failure. Deleting a file we just wrote to someone's disk,
    # to tidy up after a failure they can see and retry, is a destructive act -- and a re-run skips
    # what landed and retries what did not.
    return 1 if failed else 0


# ── mcp ───────────────────────────────────────────────────────────────────────
def cmd_mcp_serve(args, config) -> int:
    from sluice import mcpserver

    try:
        mcpserver.serve(config, write=args.write)
    except mcpserver.McpNotInstalled as exc:
        print(f"job-sluice: {exc}", file=sys.stderr)
        return 2
    return 0


# ── doctor ────────────────────────────────────────────────────────────────────
def cmd_doctor(args, config) -> int:
    from sluice.core.app import Sluice

    report = Sluice(config).doctor(offline=args.offline)
    _print_doctor(report, offline=args.offline)
    return report.exit_code(strict=args.strict)


def _print_doctor(report, *, offline) -> None:
    """One line per distinct backend, annotated with the sub-app roles it serves,
    followed by one line per component check (renderer, cv identity, store, camofox
    artefacts, track's Google adapter, preference gate posture). Written to
    stdout, like `health`/`list-sources` -- doctor's output IS the answer the
    operator asked for, not a run side-report.

    The backend table's own summary line ("N ok, N degraded, N dead") is left
    untouched rather than folded together with the component table's: it is
    pinned verbatim by an existing test, and the two tables answer different
    questions -- one backend can serve six roles, one component check is one
    fact -- so a single merged count would blur "how many distinct backends
    are broken" into "how many lines printed"."""
    from sluice.core.doctor import DEAD, DEGRADED, NOTICE, OK, format_roles

    print(f"job-sluice doctor  ({'offline' if offline else 'live round-trip'})\n")
    for c in report.checks:
        t = c.target
        elapsed = f"  ({c.elapsed:.1f}s)" if c.elapsed is not None else ""
        print(f"{t.provider:11} {t.model:20} {c.state:9} "
              f"{format_roles(t.uses)}  {c.detail}{elapsed}")
    n_ok = sum(1 for c in report.checks if c.state == OK)
    n_deg = sum(1 for c in report.checks if c.state == DEGRADED)
    n_dead = sum(1 for c in report.checks if c.state == DEAD)
    print(f"\n{n_ok} ok, {n_deg} degraded, {n_dead} dead")

    if report.components:
        print()
        for c in report.components:
            blocks = f"  blocks: {', '.join(c.blocks)}" if c.blocks else ""
            print(f"{c.component:12} {c.subject:32} {c.state:9} {c.detail}{blocks}")
        c_ok = sum(1 for c in report.components if c.state == OK)
        c_deg = sum(1 for c in report.components if c.state == DEGRADED)
        c_dead = sum(1 for c in report.components if c.state == DEAD)
        c_notice = sum(1 for c in report.components if c.state == NOTICE)
        print(f"\n{c_ok} ok, {c_deg} degraded, {c_dead} dead, {c_notice} notice")


# ── shell-completion candidate providers ─────────────────────────────────────
#
# Attached below as `.completer` on the relevant Action, which is the attribute
# `argcomplete` reads when it IS installed; setting it costs nothing when it is not, since
# nothing else ever looks at it. A completer must NEVER raise -- an exception here breaks the
# user's shell on every TAB press, not just this command, so each is wrapped defensively and
# each stays OFFLINE: no vault, no backend, no network, matching the same offline guarantee
# `ingest list-sources` already gives (both read the same in-memory plugin registry, populated
# once at import time). `--lead` (cv/apply/track) deliberately gets no completer here: doing
# that live would mean opening the user's vault and re-deriving the shortlist on every TAB
# press, which is a materially bigger and riskier feature than this pass -- left for later,
# not silently dropped.


def _complete_source_id(prefix, parsed_args, **kwargs):
    """Real registered source ids, for `ingest`'s `--source`/`id` completion."""
    try:
        return [s.id for s in registry.all_sources() if s.id.startswith(prefix)]
    except Exception:
        return []


def _complete_status(prefix, parsed_args, **kwargs):
    """The canonical status vocabulary, for `track confirm --to`."""
    try:
        from sluice.core.status import CANONICAL

        return sorted(s for s in CANONICAL if s.startswith(prefix))
    except Exception:
        return []


# ── argument parsing ─────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="job-sluice")
    # `job-sluice --version` is what a user pastes into a bug report, so it must answer without
    # demanding a subcommand -- argparse's version action fires while parsing and exits
    # before the required-subcommand check is reached. That is a property of the ACTION,
    # not of where this line sits: an earlier comment here claimed the placement was what
    # made it work, and moving the call below `add_subparsers` left the suite green and the
    # behaviour identical. Deleting the line is what reds it, which is the property the
    # test asserts.
    #
    # Reads the ONE version literal (sluice/__init__.py); pyproject derives from the same
    # attribute, so this cannot disagree with `pip show job-sluice`.
    p.add_argument("--version", action="version", version=f"job-sluice {__version__}")
    top = p.add_subparsers(dest="group", required=True)

    ingest = top.add_parser("ingest", help="ingestion commands").add_subparsers(
        dest="cmd", required=True
    )

    ls = ingest.add_parser("list-sources", help="list registered sources and enabled/health state")
    ls.add_argument("--health", action="store_true")
    ls.set_defaults(func=cmd_list_sources)

    run = ingest.add_parser("run", help="scrape configured sources into the lead store")
    # --all and --source name the same selection two ways, and _selected keys off
    # args.source ALONE -- so `run --source X --all` silently ran only X and dropped
    # --all. Make them mutually exclusive so the ambiguous combination errors instead
    # of degrading silently (the module docstring's `run [--all|--source ID ...]`
    # already claimed the exclusion). NOT required: bare `run` still means all sources.
    sel = run.add_mutually_exclusive_group()
    sel.add_argument(
        "--source", action="append", help="source id (repeatable)"
    ).completer = _complete_source_id
    sel.add_argument("--all", action="store_true")
    run.add_argument("--sink", choices=["vault", "json"], default="vault")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    ts = ingest.add_parser("test-source", help="run one source live, print its parsed leads")
    ts.add_argument("id").completer = _complete_source_id
    ts.add_argument("--raw", action="store_true", help="print raw fetch payload only")
    ts.set_defaults(func=cmd_test_source)

    en = ingest.add_parser("enable", help="re-enable a source disabled by a prior --disable")
    en.add_argument("id").completer = _complete_source_id
    en.set_defaults(func=cmd_enable)

    di = ingest.add_parser("disable", help="turn a source off until a matching --enable")
    di.add_argument("id").completer = _complete_source_id
    di.set_defaults(func=cmd_disable)

    triage = top.add_parser("triage", help="triage commands").add_subparsers(
        dest="cmd", required=True)

    tr = triage.add_parser("run", help="classify leads: deterministic rules, then an LLM judge")
    tr.add_argument("--status", default="new,research")
    tr.add_argument("--limit", type=int)
    tr.add_argument("--dry-run", action="store_true")
    tr.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                    help=_BACKEND_HELP)
    tr.add_argument("--no-llm", action="store_true")
    tr.set_defaults(func=cmd_triage_run)

    tn = triage.add_parser("normalize-status", help="canonicalize status aliases (e.g. 'shortlisted')")
    tn.add_argument("--dry-run", action="store_true")
    tn.set_defaults(func=cmd_triage_normalize)

    cv = top.add_parser("cv", help="cv tailoring commands").add_subparsers(
        dest="cmd", required=True)
    cvrun = cv.add_parser("run", help="compose, gate and render a tailored CV for a shortlist lead")
    g = cvrun.add_mutually_exclusive_group(required=True)
    g.add_argument("--lead", help="compose one CV for the shortlist lead matching this slug")
    g.add_argument("--all-shortlist", action="store_true",
                   help="compose for shortlist leads without a tailored_cv")
    cvrun.add_argument("--limit", type=int)
    cvrun.add_argument("--dry-run", action="store_true")
    cvrun.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                       help=_BACKEND_HELP)
    cvrun.add_argument("--no-serve", action="store_true")
    # #9: `last_seen` only bumps when a lead reappears in a scrape, so narrowing your
    # searches ages a still-live posting. Without a way through, that false positive
    # makes people set lead_ttl_days back to 0 and lose the feature entirely.
    cvrun.add_argument("--include-stale", action="store_true",
                       help="compose even for a lead older than lead_ttl_days")
    cvrun.set_defaults(func=cmd_cv_run)
    cvsign = cv.add_parser(
        "signoff", help="release or discard a CV held for a human sign-off (#60)")
    cvsign.add_argument("--lead", required=True,
                        help="sign off (or --discard) the CV held for the shortlist lead matching this slug")
    cvsign.add_argument("--discard", action="store_true",
                        help="reject the held CV instead of promoting it, freeing a fresh compose")
    cvsign.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    cvsign.set_defaults(func=cmd_cv_signoff)

    apply_ = top.add_parser("apply", help="application prep + tracking").add_subparsers(
        dest="cmd", required=True)
    ap = apply_.add_parser("prep", help="stage a tailored CV + prep packet for a shortlist lead")
    apg = ap.add_mutually_exclusive_group(required=True)
    apg.add_argument("--lead", help="stage one application for the shortlist lead matching this slug")
    apg.add_argument("--all-shortlist", action="store_true", help="preview the ready queue (no CV staged)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    # #9, same escape hatch as `cv run`: a narrowed search list ages a still-live posting,
    # and a refusal with no way through makes people turn the feature off entirely.
    ap.add_argument("--include-stale", action="store_true",
                    help="stage even a lead older than lead_ttl_days")
    ap.set_defaults(func=cmd_apply_prep)

    arec = apply_.add_parser("record", help="mark a lead applied after you submit it by hand")
    arec.add_argument("--lead", required=True)
    arec.add_argument("--ats", default=None)
    arec.add_argument("--url", default=None)
    arec.add_argument("--dry-run", action="store_true")
    arec.set_defaults(func=cmd_apply_record)

    track = top.add_parser("track", help="application tracking from email + calendar").add_subparsers(
        dest="cmd", required=True)
    trun = track.add_parser("run", help="reconcile the funnel from email + calendar signals")
    trun.add_argument("--dry-run", action="store_true")
    trun.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                      help=_BACKEND_HELP)
    trun.set_defaults(func=cmd_track_run)
    tconf = track.add_parser("confirm", help="apply a proposed status transition by hand")
    tconf.add_argument("--lead", required=True)
    tconf.add_argument("--to", required=True).completer = _complete_status
    tconf.add_argument("--when", default=None)
    tconf.add_argument("--dry-run", action="store_true")
    tconf.set_defaults(func=cmd_track_confirm)
    tdis = track.add_parser("dismiss", help="clear a dead-letter proposal (no status change)")
    tdg = tdis.add_mutually_exclusive_group(required=True)
    tdg.add_argument("--id", help="Gmail message-id of the dead-letter entry to clear")
    tdg.add_argument("--lead", help="clear a lead's dead-letter entries without advancing status")
    tdis.add_argument("--dry-run", action="store_true")
    tdis.set_defaults(func=cmd_track_dismiss)

    leads = top.add_parser("leads", help="lead maintenance").add_subparsers(
        dest="cmd", required=True)
    dd = leads.add_parser("dedupe", help="find/merge duplicate lead notes")
    dd.add_argument("--merge", nargs="+", metavar="ID",
                    help="merge the named vetted clusters (from a prior report)")
    dd.add_argument("--json", action="store_true", help="machine-readable report")
    dd.set_defaults(func=cmd_leads_dedupe)

    ex = leads.add_parser("expire", help="report/dismiss leads stale past lead_ttl_days")
    # NOT dedupe's `--merge nargs="+"`: that REQUIRES an argument, so a bare `--expire`
    # would be an argparse error rather than the bulk case. And NOT dedupe's
    # `if args.merge:` dispatch either -- a bare flag parses to a FALSY [], which would
    # fall through to the report branch and leave the write flag silently inert. The
    # pairing that works is `nargs="*", default=None` + `is not None`.
    ex.add_argument("--expire", nargs="*", default=None, metavar="SLUG",
                    help='dismiss the reported leads; name slugs to narrow, e.g. '
                         '--expire "Example Ltd - Example Role"')
    ex.add_argument("--json", action="store_true", help="machine-readable report")
    ex.set_defaults(func=cmd_leads_expire)

    ds = leads.add_parser("dismiss", help="dismiss one lead by exact slug, with a reason")
    ds.add_argument("--lead", required=True, metavar="SLUG",
                    help='exact store-issued slug, e.g. --lead "Example Ltd - Example Role"')
    ds.add_argument("--reason", required=True, help="why this lead is being dismissed")
    ds.set_defaults(func=cmd_leads_dismiss)

    rc = leads.add_parser(
        "reconcile",
        help="report/file lead notes into their status-implied folders",
        description="Report, or with --apply move, each lead note into the folder its status "
                    "implies. Do NOT run --apply concurrently with a pipeline command "
                    "(ingest/triage/cv/apply/track): a move landing inside another writer's "
                    "compare-and-set window re-creates the source path, leaving two notes at one "
                    "name. That state is reported under `ambiguous` rather than prevented.")
    # No --dry-run: the default IS the dry run (nothing moves without --apply), and a flag that
    # does nothing is drift. Same shape as `leads dedupe --merge` and `leads expire --expire`.
    rc.add_argument("--apply", action="store_true",
                    help="actually move the notes this would otherwise only report")
    rc.add_argument("--json", action="store_true", help="machine-readable report")
    rc.set_defaults(func=cmd_leads_reconcile)

    health = top.add_parser("health", help="per-source baseline + retire state")
    health.set_defaults(func=cmd_health)

    mcp_group = top.add_parser("mcp", help="Model Context Protocol server").add_subparsers(
        dest="cmd", required=True)
    mcp_serve = mcp_group.add_parser("serve", help="run the MCP server (stdio transport)")
    mcp_serve.add_argument(
        "--write", action="store_true",
        help="also register the five write-capable tools (dismiss_lead, apply_record, "
             "cv_run, cv_signoff, create_lead) -- off by default, since this is a "
             "per-registration trust decision about one MCP client, not a property "
             "of the install")
    mcp_serve.set_defaults(func=cmd_mcp_serve)

    init = top.add_parser("init", help="scaffold a config and a Judging Profile")
    init.add_argument("--vault", help="your Obsidian vault directory")
    init.add_argument("--no-input", action="store_true",
                      # NOT "take every default": NoInputAsker deliberately takes NO defaults, and
                      # its own comment says why -- the moment a future question gains one,
                      # --no-input would write it into a config nobody was asked about.
                      help="never prompt; answer only from flags")
    init.set_defaults(func=cmd_init)

    doctor = top.add_parser(
        "doctor", help="preflight backends, renderer, store artefacts and gate posture")
    doctor.add_argument("--offline", action="store_true",
                        help="config-only checks; no round-trip")
    doctor.add_argument("--strict", action="store_true",
                        help="exit non-zero on degraded (e.g. a keyless fallback) too")
    doctor.set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    parser = _build_parser()
    if argcomplete is not None:
        argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    try:
        config = load_config()
        return args.func(args, config)
    except ValueError as exc:
        # A retired or malformed config key is a USAGE error, not a crash. It reached the user as a
        # raw traceback, and the command it blocked hardest was `job-sluice init` -- the one that would
        # have written them a correct config -- plus `doctor`, which exists to diagnose exactly this.
        #
        # #120: widened from wrapping only load_config() to wrapping the whole dispatch. Every
        # sub-app config (triage/cv/track/apply) is loaded LAZILY, inside its own Sluice.* method,
        # not here -- so a malformed triage:/cv:/track: block (this round's own
        # company_resolve_llm cross-field check, and the pre-existing quoted-bool check every
        # *Config loader already shares) previously escaped THIS except entirely and surfaced as a
        # raw traceback instead of the identical "job-sluice: <message>" / exit 2 shape a malformed
        # ROOT config key already gets. All REACHABLE ValueError sites in this dispatch path are
        # usage-error class raises (config or argument validation) — unreachable internal-invariant
        # guards exist too (paths.py:307's kind check, track_dismiss's selector guard) but cannot
        # currently fire from any live call site, so the widening's real-world effect is purely
        # improving UX for config errors that previously escaped as raw tracebacks.
        print(f"job-sluice: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
