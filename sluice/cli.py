"""sluice command-line interface.

  sluice ingest list-sources [--health]     list plugins + enabled/health state
  sluice ingest run [--all|--source ID ...] [--sink vault|json] [--dry-run]
  sluice ingest test-source ID [--raw]      run ONE source live (fixture capture)
  sluice ingest enable|disable ID           persist an operator on/off override
  sluice health                             per-source baseline + retire state

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
from sluice.ingest import sources as registry
from sluice.ingest.engine import run as engine_run

_log = get_logger("cli")


# Read paths lazily (each call) so env overrides - and tests' monkeypatch - win.
def _health_path() -> str:
    return os.environ.get("SLUICE_HEALTH", "./sluice_health.json")


def _disabled_path() -> str:
    return os.environ.get("SLUICE_DISABLED", "./sluice_disabled.json")


def _dossier_dir() -> str:
    return os.environ.get("DOSSIER_DIR", "./dossiers")


def _audit_path() -> str:
    return os.environ.get("TRIAGE_AUDIT", "./triage-audit.jsonl")


# ── operator on/off overlay ──────────────────────────────────────────────────
def _load_disabled() -> set:
    try:
        with open(_disabled_path(), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
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
    health = HealthStore(_health_path()) if getattr(args, "health", False) else None
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
    health = HealthStore(_health_path())
    for src in sorted(registry.all_sources(), key=lambda s: s.id):
        counts = health.counts(src.id)
        flag = " RETIRE" if health.should_retire(src.id) else ""
        print(f"{src.id:16} baseline={health.baseline(src.id):.0f} "
              f"recent={counts}{flag}")
    return 0


def cmd_run(args, config) -> int:
    # Imported here so offline commands (and their tests) never touch Camofox.
    from sluice.core.app import Sluice
    from sluice.core.seendb import SeenDb
    from sluice.ingest.base import Ctx
    from sluice.ingest.sink import JsonSink, VaultSink

    disabled = _load_disabled()
    srcs = _selected(args, config, disabled)
    if not srcs:
        _log.warning("no enabled sources selected")
        return 1
    app = Sluice(config)
    ctx = Ctx(camofox=app.fetcher(), config=config)
    seen = SeenDb()
    health = HealthStore(_health_path())
    if args.dry_run or args.sink == "json":
        sink = JsonSink(sys.stdout)  # dry-run never writes the vault or seen.db
    else:
        # The store ensures its own Syncthing marker on the write path now, so cli.py
        # no longer reaches into a vault-specific method no other store could implement.
        sink = VaultSink(app.store(), seen)
    report = engine_run(srcs, ctx, sink, seen, health)
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
    print(f"written: {w['created']} created, {w['updated']} updated", file=sys.stderr)


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
    summary = Sluice(config).store().normalize_all_statuses(dry_run=args.dry_run)
    print(f"status normalize: changed={summary['changed']} "
          f"unchanged={summary['unchanged']} "
          f"conflicts={summary.get('conflicts', [])} "
          f"unknown={sorted(set(summary['unknown']))}"
          f"{' (dry-run)' if args.dry_run else ''}")
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
# construction helpers behind it -- now live in Sluice.backend(), which the three
# per-command wrappers below delegate to. This literal is KEPT here so argparse
# still has its `choices` without importing the moved role/alias tables.
_BACKEND_CHOICES = ["auto", "primary", "fallback", "claude-max", "deepseek"]
_BACKEND_HELP = (
    "which configured backend to use: auto (primary, falling back), primary, or "
    "fallback. claude-max/deepseek are deprecated aliases for primary/fallback.")


def _build_backend(tcfg, backend_choice="auto"):
    from sluice.core.app import Sluice
    return Sluice().backend(
        backend_choice,
        primary_name=tcfg.primary_backend, primary_model=tcfg.claude_max_model,
        effort=tcfg.claude_max_effort, host=tcfg.claude_max_host,
        claude_path=tcfg.claude_max_path,
        fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)


def _dossier_fetcher(app):
    """Fetcher-backed JD enrichment. The fetcher is resolved lazily on the first cache
    miss, so a --no-llm run or a fully-cached run never opens a browser. Text is read via
    evaluate(document.body.innerText) - the same {"result": ...} shape the ingest sources
    use - rather than guessing the snapshot payload key."""
    cam = {}

    def fetch(lead: dict) -> dict:
        md = ""
        url = lead.get("url")
        if url:
            if "client" not in cam:
                cam["client"] = app.fetcher()
            c = cam["client"]
            tid = c.create_tab(url)
            if tid:
                res = c.evaluate(tid, "document.body.innerText")
                md = res.get("result") if isinstance(res, dict) else ""
                c.close_tab(tid)
        return {"jd": {"markdown": md or ""}, "glassdoor": {}}
    return fetch


# ── cv ────────────────────────────────────────────────────────────────────
def _build_compose_backend(cvcfg, backend_choice="auto"):
    from sluice.core.app import Sluice
    return Sluice().backend(
        backend_choice,
        primary_name=cvcfg.primary_backend, primary_model=cvcfg.compose_model,
        effort=cvcfg.compose_effort, host=cvcfg.compose_host,
        claude_path=cvcfg.compose_claude_path,
        fallback_name=cvcfg.fallback_backend, fallback_model=cvcfg.cheap_model)


def cmd_cv_run(args, config) -> int:
    from sluice.core.app import Sluice

    results = Sluice(config).compose_cv(
        lead=args.lead, all_shortlist=args.all_shortlist, limit=args.limit,
        dry_run=args.dry_run, no_serve=args.no_serve, backend_role=args.backend)
    if not results and not args.all_shortlist:
        print(f"cv: no shortlist lead matching '{args.lead}'", file=sys.stderr)
        return 1

    for r in results:
        print(f"cv: {r.status} {r.lead} served={r.served} "
              f"violations={len(r.violations)} audit_flags={len(r.audit_flags)}",
              file=sys.stderr)
    rendered = [r for r in results if r.status == "rendered"]
    if rendered:
        notify("sluice cv: " + "; ".join(
            f"{r.served} (audit flags: {len(r.audit_flags)})" for r in rendered),
            config=config)
    return 0


# ── apply ────────────────────────────────────────────────────────────────────
def cmd_apply_prep(args, config) -> int:
    from sluice.core.app import Sluice
    from sluice.apply import packet

    app = Sluice(config)
    if args.all_shortlist:
        results = app.prep(all_shortlist=True, limit=args.limit)
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
        r = app.prep(lead=args.lead, dry_run=True)[0]
        if r.status == "skipped":
            print(f"apply-prep: {args.lead} skipped ({r.reason})", file=sys.stderr)
            return 1
        print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        print(f"apply-prep: {args.lead} dry-run", file=sys.stderr)
        return 0
    r = app.prep(lead=args.lead)[0]
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
def _track_backend(tcfg, backend_choice="auto"):
    from sluice.core.app import Sluice
    return Sluice().backend(
        backend_choice,
        primary_name=tcfg.primary_backend, primary_model=tcfg.claude_max_model,
        effort=tcfg.claude_max_effort, host=tcfg.claude_max_host,
        claude_path=tcfg.claude_max_path,
        fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)


def _load_seen(path):
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except OSError:
        return set()


def _save_seen(path, seen):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(sorted(seen)))


def _load_lastrun(path):
    """Read the ISO timestamp of the previous successful (non-dry-run) track run,
    so the next run's Gmail query can be scoped since then (F10) instead of the
    fixed lookback window. Missing/unreadable file just means "no prior run"."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _save_lastrun(path, iso):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(iso)


def cmd_track_run(args, config) -> int:
    from datetime import datetime, timezone
    from sluice.core.app import Sluice
    from sluice.track.config import load_track_config
    from sluice.track.engine import run
    from sluice.track.google_client import RealGoogleClient

    tcfg = load_track_config()
    lastrun_path = tcfg.seen_db + ".lastrun"
    seen = _load_seen(tcfg.seen_db)
    since_iso = _load_lastrun(lastrun_path)
    client = RealGoogleClient(tcfg.token_path)
    backend = _track_backend(tcfg, args.backend)
    now_iso = datetime.now(timezone.utc).isoformat()
    rep = run(Sluice(config).store(), tcfg, client, backend, seen=seen, now_iso=now_iso,
              since_iso=since_iso, dry_run=args.dry_run)
    if not args.dry_run:
        _save_seen(tcfg.seen_db, seen)
    if rep.auth_error:
        print("track: google reauth needed (token refresh failed)", file=sys.stderr)
        return 1
    print(f"track: msgs={rep.msgs} classified={rep.classified} auto={rep.auto} "
          f"proposed={rep.proposed} calendar_added={rep.calendar_added} failures={rep.failures}",
          file=sys.stderr)
    for p in rep.proposals:
        print(f"  PROPOSAL {p}", file=sys.stderr)
    if not args.dry_run:
        _save_lastrun(lastrun_path, now_iso)
    return 0


def cmd_track_confirm(args, config) -> int:
    from sluice.core.app import Sluice
    from sluice.track.config import load_track_config
    from sluice.track.engine import confirm

    out = confirm(Sluice(config).store(), load_track_config(), args.lead, args.to,
                  when=args.when, dry_run=args.dry_run)
    if out["ok"]:
        print(f"track-confirm: {args.lead} {out['from']} -> {out['to']}", file=sys.stderr)
        return 0
    print(f"track-confirm: {args.lead} refused ({out['reason']})", file=sys.stderr)
    return 1


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
    run.add_argument("--source", action="append", help="source id (repeatable)")
    run.add_argument("--all", action="store_true")
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
    tr.add_argument("--sink", choices=["vault", "json"], default="vault")
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
    cvrun.set_defaults(func=cmd_cv_run)

    apply_ = top.add_parser("apply", help="application prep + tracking").add_subparsers(
        dest="cmd", required=True)
    ap = apply_.add_parser("prep")
    apg = ap.add_mutually_exclusive_group(required=True)
    apg.add_argument("--lead", help="stage one application for the shortlist lead matching this slug")
    apg.add_argument("--all-shortlist", action="store_true", help="preview the ready queue (no CV staged)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
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
    trun.add_argument("--limit", type=int, default=None)
    trun.add_argument("--json", action="store_true")
    trun.add_argument("--backend", choices=_BACKEND_CHOICES, default="auto",
                      help=_BACKEND_HELP)
    trun.set_defaults(func=cmd_track_run)
    tconf = track.add_parser("confirm")
    tconf.add_argument("--lead", required=True)
    tconf.add_argument("--to", required=True)
    tconf.add_argument("--when", default=None)
    tconf.add_argument("--dry-run", action="store_true")
    tconf.set_defaults(func=cmd_track_confirm)

    health = top.add_parser("health")
    health.set_defaults(func=cmd_health)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config()
    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
