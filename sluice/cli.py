"""sluice command-line interface.

  sluice ingest list-sources [--health]     list plugins + enabled/health state
  sluice ingest run [--all|--source ID ...] [--sink vault|json] [--dry-run]
  sluice ingest test-source ID [--raw]      run ONE source live (fixture capture)
  sluice ingest enable|disable ID           persist an operator on/off override
  sluice health                             per-source baseline + retire state
  sluice doctor [--offline] [--strict]      preflight configured backends (live round-trip)

`run` and `test-source` drive the live Camofox session; the rest are offline.
enable/disable persist to a small JSON overlay (SLUICE_DISABLED) so an operator
override survives across runs, on top of config + runtime auto-retire.
"""
import argparse
import json
import os
import sys
from dataclasses import asdict

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
    path = _disabled_path()
    # MISSING -> nothing disabled (the ordinary state). An unreadable or malformed
    # overlay is NOT that, and reading it as "nothing disabled" silently RE-ENABLES
    # every source the operator turned off -- which #80 newly made reachable, because
    # this file used to sit in the cwd and now resolves per-system, so an upgrader's
    # overlay is at the old location. `paths.resolve` warns about the move, but that
    # notice names the file and not the consequence, and this is the consequence.
    #
    # Warn rather than refuse, deliberately: re-enabling a source costs a wasted scrape
    # and is fixed by disabling it again, which is a different order of harm from the
    # dedup stores (a duplicate application, irreversible). But it must not be SILENT.
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError) as e:
        _log.warning(
            "could not read the disabled-sources overlay at %s (%s): treating every "
            "source as ENABLED for this run. Any source you disabled will be scraped.",
            path, e)
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
    disabled = _load_disabled()
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


def cmd_health(args, config) -> int:
    health = HealthStore()
    for src in sorted(registry.all_sources(), key=lambda s: s.id):
        counts = health.counts(src.id)
        flag = " RETIRE" if health.should_retire(src.id) else ""
        print(f"{src.id:16} baseline={health.baseline(src.id):.0f} "
              f"recent={counts}{flag}")
    return 0


def cmd_run(args, config) -> int:
    # Imported here so offline commands (and their tests) never touch Camofox.
    from sluice.core.app import Sluice

    disabled = _load_disabled()
    srcs = _selected(args, config, disabled)
    if not srcs:
        _log.warning("no enabled sources selected")
        return 1
    report = Sluice(config).ingest(srcs, dry_run=args.dry_run, json_sink=(args.sink == "json"))
    _print_report(report)
    if report.degraded:
        notify(_format_degraded(report), config=config)
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
    # Sparse: merged/refused (#5) are printed only when non-zero, and every read uses
    # .get so a clean run — whose sink never adds those keys — does not KeyError.
    parts = [f"{w.get('created', 0)} created", f"{w.get('updated', 0)} updated"]
    if w.get("merged"):
        parts.append(f"{w['merged']} merged")
    if w.get("refused"):
        parts.append(f"{w['refused']} refused")
    parts.append(f"{w.get('skipped', 0)} skipped")
    print("written: " + ", ".join(parts), file=sys.stderr)


def _format_degraded(report) -> str:
    """One line per unhealthy source (drifted, errored, or auto-retired);
    healthy sources are omitted. Used as the Telegram notify body."""
    lines = []
    for r in report.sources:
        if not (r.drift or r.retired or r.status == "error"):
            continue
        reason = r.drift or ("error" if r.status == "error" else "ok")
        lines.append(f"- {r.source_id}: {reason}{' [RETIRED]' if r.retired else ''}")
    return "sluice: degraded sources this run:\n" + "\n".join(lines)


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
                      f'sluice cv signoff --lead "{slug}" --discard', file=sys.stderr)
            elif outcome != "dismissed":
                print(f"expire: {slug}: {outcome}", file=sys.stderr)
        print("expire: " + ", ".join(f"{n} {o}" for o, n in sorted(counts.items())),
              file=sys.stderr)
        # Any outcome where the write did not happen exits non-zero: a silent no-op is the
        # exact failure mode this report-first command is shaped to avoid. `conflict` and
        # `unreadable` belong here for the same reason `no-match` does -- the user asked
        # for a write and did not get one. `skipped` too: it means the lead left the
        # triage lifecycle mid-sweep, which is precisely the case a caller must notice.
        _FAILED = {"no-match", "conflict", "unreadable", "skipped"}
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
          f"backend={report.backend} failures={len(report.failures)}", file=sys.stderr)
    notify(f"sluice triage: {report.counts} (backend {report.backend})", config=config)
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
        notify("sluice cv: " + "; ".join(
            f"{r.served} (audit flags: {len(r.audit_flags)})" for r in rendered),
            config=config)
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
    if result is None:
        print(f"cv signoff: no shortlist lead matching '{args.lead}'", file=sys.stderr)
        return 1
    slug, outcome = result
    msg = {"nothing": "has nothing pending", "aborted": "aborted"}.get(outcome, outcome)
    print(f"cv signoff: {slug} {msg}", file=sys.stderr)
    return 0


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
              f"(ats={f['ats']} cv={f['applied_cv']})", file=sys.stderr)
        return 0
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
          f"failures={rep.failures} open={len(rep.open_proposals)}", file=sys.stderr)
    if rep.open_proposals:
        print("  OPEN PROPOSALS (awaiting action):", file=sys.stderr)
        for e in rep.open_proposals:
            tag = " (new)" if e.times_surfaced <= 1 else ""
            label = e.lead or e.candidates or "?"
            print(f"  [{e.first_seen} x{e.times_surfaced}{tag}] {label}: {e.proposal} :: {e.hint}",
                  file=sys.stderr)
    return 0


def cmd_track_confirm(args, config) -> int:
    from sluice.core.app import Sluice

    out = Sluice(config).track_confirm(lead=args.lead, to=args.to, when=args.when,
                                       dry_run=args.dry_run)
    if out["ok"]:
        print(f"track-confirm: {args.lead} {out['from']} -> {out['to']}", file=sys.stderr)
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


# ── doctor ────────────────────────────────────────────────────────────────────
def cmd_doctor(args, config) -> int:
    from sluice.core.app import Sluice

    report = Sluice(config).doctor(offline=args.offline)
    _print_doctor(report, offline=args.offline)
    return report.exit_code(strict=args.strict)


def _print_doctor(report, *, offline) -> None:
    """One line per distinct backend, annotated with the sub-app roles it serves.
    Written to stdout, like `health`/`list-sources` -- doctor's output IS the
    answer the operator asked for, not a run side-report."""
    from sluice.core.doctor import DEAD, DEGRADED, OK, format_roles

    print(f"sluice doctor  ({'offline' if offline else 'live round-trip'})\n")
    for c in report.checks:
        t = c.target
        elapsed = f"  ({c.elapsed:.1f}s)" if c.elapsed is not None else ""
        print(f"{t.provider:11} {t.model:20} {c.state:9} "
              f"{format_roles(t.uses)}  {c.detail}{elapsed}")
    n_ok = sum(1 for c in report.checks if c.state == OK)
    n_deg = sum(1 for c in report.checks if c.state == DEGRADED)
    n_dead = sum(1 for c in report.checks if c.state == DEAD)
    print(f"\n{n_ok} ok, {n_deg} degraded, {n_dead} dead")


# ── argument parsing ─────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sluice")
    top = p.add_subparsers(dest="group", required=True)

    ingest = top.add_parser("ingest", help="ingestion commands").add_subparsers(
        dest="cmd", required=True
    )

    ls = ingest.add_parser("list-sources")
    ls.add_argument("--health", action="store_true")
    ls.set_defaults(func=cmd_list_sources)

    run = ingest.add_parser("run")
    # --all and --source name the same selection two ways, and _selected keys off
    # args.source ALONE -- so `run --source X --all` silently ran only X and dropped
    # --all. Make them mutually exclusive so the ambiguous combination errors instead
    # of degrading silently (the module docstring's `run [--all|--source ID ...]`
    # already claimed the exclusion). NOT required: bare `run` still means all sources.
    sel = run.add_mutually_exclusive_group()
    sel.add_argument("--source", action="append", help="source id (repeatable)")
    sel.add_argument("--all", action="store_true")
    run.add_argument("--sink", choices=["vault", "json"], default="vault")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    ts = ingest.add_parser("test-source")
    ts.add_argument("id")
    ts.add_argument("--raw", action="store_true", help="print raw fetch payload only")
    ts.set_defaults(func=cmd_test_source)

    en = ingest.add_parser("enable")
    en.add_argument("id")
    en.set_defaults(func=cmd_enable)

    di = ingest.add_parser("disable")
    di.add_argument("id")
    di.set_defaults(func=cmd_disable)

    triage = top.add_parser("triage", help="triage commands").add_subparsers(
        dest="cmd", required=True)

    tr = triage.add_parser("run")
    tr.add_argument("--status", default="new,research")
    tr.add_argument("--limit", type=int)
    tr.add_argument("--dry-run", action="store_true")
    tr.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                    help=_BACKEND_HELP)
    tr.add_argument("--no-llm", action="store_true")
    tr.set_defaults(func=cmd_triage_run)

    tn = triage.add_parser("normalize-status")
    tn.add_argument("--dry-run", action="store_true")
    tn.set_defaults(func=cmd_triage_normalize)

    cv = top.add_parser("cv", help="cv tailoring commands").add_subparsers(
        dest="cmd", required=True)
    cvrun = cv.add_parser("run")
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
    cvsign = cv.add_parser("signoff")
    cvsign.add_argument("--lead", required=True,
                        help="sign off (or --discard) the CV held for the shortlist lead matching this slug")
    cvsign.add_argument("--discard", action="store_true",
                        help="reject the held CV instead of promoting it, freeing a fresh compose")
    cvsign.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    cvsign.set_defaults(func=cmd_cv_signoff)

    apply_ = top.add_parser("apply", help="application prep + tracking").add_subparsers(
        dest="cmd", required=True)
    ap = apply_.add_parser("prep")
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

    arec = apply_.add_parser("record")
    arec.add_argument("--lead", required=True)
    arec.add_argument("--ats", default=None)
    arec.add_argument("--url", default=None)
    arec.add_argument("--dry-run", action="store_true")
    arec.set_defaults(func=cmd_apply_record)

    track = top.add_parser("track", help="application tracking from email + calendar").add_subparsers(
        dest="cmd", required=True)
    trun = track.add_parser("run")
    trun.add_argument("--dry-run", action="store_true")
    trun.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                      help=_BACKEND_HELP)
    trun.set_defaults(func=cmd_track_run)
    tconf = track.add_parser("confirm")
    tconf.add_argument("--lead", required=True)
    tconf.add_argument("--to", required=True)
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

    health = top.add_parser("health")
    health.set_defaults(func=cmd_health)

    doctor = top.add_parser("doctor", help="preflight the configured backends")
    doctor.add_argument("--offline", action="store_true",
                        help="config-only checks; no round-trip")
    doctor.add_argument("--strict", action="store_true",
                        help="exit non-zero on degraded (e.g. a keyless fallback) too")
    doctor.set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config()
    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
