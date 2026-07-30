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

    Preflight resolves BOTH destinations before a single question is asked: a wizard that
    interviews someone for five minutes and then says "config already exists" wasted their time to
    learn something it knew at the start.
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
        print("sluice init: --vault and $VAULT_DIR name different directories. Unset one, or pass "
              "the one you mean.", file=sys.stderr)
        return 2

    config_dest = config_file()
    config_exists = os.path.exists(config_dest)

    presets = {}
    vault_arg = args.vault or env_vault or config.vault_dir
    if vault_arg:
        presets["vault_dir"] = os.path.abspath(os.path.expanduser(vault_arg))

    interactive = not args.no_input and sys.stdin.isatty()
    if asker is None:
        # $EDITOR is resolved HERE and passed in, so the asker itself reads no environment and a
        # test can pin "no editor" without the developer's real one leaking into the run.
        asker = (TtyAsker(stdin=sys.stdin, stdout=sys.stdout, editor=os.environ.get("EDITOR"))
                 if interactive else NoInputAsker(presets=presets))

    # A preset must win over a prompt even on a TTY: someone who passed --vault has already
    # answered, and asking again invites a different answer to the same question.
    questions = tuple(q for q in catalogue(default_vault=DEFAULT_VAULT) if q.key not in presets)

    try:
        answers = dict(presets)
        answers.update(collect(asker, questions))
    except MissingAnswer as exc:
        print(f"sluice init: {exc}", file=sys.stderr)
        return 2

    vault_dir = answers["vault_dir"]
    if os.path.exists(vault_dir) and not os.path.isdir(vault_dir):
        print(f"sluice init: {vault_dir} is not a directory.", file=sys.stderr)
        return 2
    vault_created = not os.path.exists(vault_dir)

    profile_dest = os.path.join(vault_dir, CRITERIA_RELPATH)
    profile_exists = os.path.exists(profile_dest)

    profile_answers = {}
    sources = {}
    if interactive:
        sources = collect_sources(asker, [s.id for s in registry.all_sources()])
        if not profile_exists:
            profile_answers = collect_profile(asker)

    plan = build_plan(answers, config_dest=config_dest, profile_dest=profile_dest,
                      profile_answers=profile_answers, sources=sources)

    written, skipped, failed = [], [], []

    if config_exists:
        skipped.append(config_dest)
    else:
        os.makedirs(os.path.dirname(config_dest), exist_ok=True)
        try:
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
        os.makedirs(vault_dir, exist_ok=True)
        # Through the STORE SEAM, not Vault(...) directly: the profile is a store-managed document,
        # and #1 makes the second store real rather than hypothetical.
        store = Sluice(dataclasses.replace(config, vault_dir=vault_dir)).store()
        handle = store.write_document(CRITERIA_RELPATH, plan.profile_text, only_if_absent=True)
        (written if handle else skipped).append(profile_dest)
        if not handle and profile_answers:
            # The user typed prose into an interview and the profile turned up already there. Do
            # NOT overwrite it, and do not silently bin what they wrote: park it beside the real
            # one and say so.
            spare = CRITERIA_RELPATH.replace(".md", ".init-scaffold.md")
            if store.write_document(spare, plan.profile_text, only_if_absent=True):
                written.append(os.path.join(vault_dir, spare))
    except OSError as exc:
        failed.append(f"{profile_dest}: {exc}")

    for path in written:
        print(f"  wrote   {path}")
    for path in skipped:
        print(f"  exists  {path}  (left alone)")
    for line in failed:
        print(f"  FAILED  {line}", file=sys.stderr)

    if vault_created:
        print(f"\ncreated a new vault directory at {vault_dir}")
        print("if you meant an existing one, re-run with --vault pointing at it")
    else:
        print(f"\nusing the existing vault at {vault_dir}")

    if plan.notes:
        print("\nYour config will:")
        for note in plan.notes:
            print(f"  {note}")

    print("\nNext:")
    print("  1. fill in the headings in your Judging Profile")
    print("  2. sluice ingest list-sources --health")
    print("  3. sluice triage run --no-llm")

    # Nothing is rolled back on a partial failure. Deleting a file we just wrote to someone's disk,
    # to tidy up after a failure they can see and retry, is a destructive act -- and a re-run skips
    # what landed and retries what did not.
    return 1 if failed else 0


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

    init = top.add_parser("init", help="scaffold a config and a Judging Profile")
    init.add_argument("--vault", help="your Obsidian vault directory")
    init.add_argument("--no-input", action="store_true",
                      help="take every default; never prompt")
    init.set_defaults(func=cmd_init)

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
