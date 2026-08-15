"""Orchestration: run (fetch -> classify -> reconcile, per-message resilient) and
confirm (apply an approved proposal). The Gmail query is scoped by time window; the
`seen` set (a message-id store) provides dedup, never read-state (F8/F10)."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sluice.core import status as _status
from sluice.core.leads import ambiguous_slug_warnings, index_by_slug, slug_matches
from sluice.core.log import get_logger
from sluice.core.protocols import VaultConflict
from sluice.track.classify import classify
from sluice.track.reconcile import reconcile
from sluice.track.ics import parse_ics
from sluice.track.google_client import GoogleAuthError
from sluice.track.deadletter import Entry
from sluice.track.receipt import match_receipt

_log = get_logger("track.engine")

_INFLIGHT = ("applied", "phone_screen", "interview", "offer")  # non-terminal application states

# Map a classified event type -> the target status a proposal would advance to (F7).
_PROPOSE_TARGET = {"phone_screen": "phone_screen", "interview": "interview",
                   "rejection": "rejected", "offer": "offer", "receipt": "applied"}


@dataclass
class RunReport:
    msgs: int = 0
    classified: int = 0
    auto: int = 0
    proposed: int = 0
    calendar_added: int = 0
    failures: int = 0
    results: list = field(default_factory=list)
    open_proposals: list = field(default_factory=list)  # every currently-open dead-letter Entry
    auth_error: bool = False
    deadletter_error: bool = False  # a dead-letter WRITE raised this run; app.py must hold
                                     # the lastrun watermark so the un-persisted message
                                     # re-queries next run instead of aging out of Gmail's
                                     # advancing `after:` window (#49's write-path silent loss)


def _dl_write(rep, op):
    # A dead-letter WRITE failure must both skip seen.add (re-raise into the per-message
    # except) and hold the lastrun watermark (rep.deadletter_error -> app.py skips
    # _save_lastrun), so the un-persisted message re-queries next run instead of falling
    # out of the advancing Gmail `after:` window. #49's silent-loss on the write path.
    try:
        op()
    except Exception:
        rep.deadletter_error = True
        raise


def _gmail_query(cfg, now_iso, since_iso=None):
    if since_iso:
        after = datetime.fromisoformat(since_iso).strftime("%Y/%m/%d")
    else:
        now = datetime.fromisoformat(now_iso)
        after = (now - timedelta(days=cfg.gmail_lookback_days)).strftime("%Y/%m/%d")
    q = f"after:{after} -category:promotions"
    return (q + " " + cfg.gmail_extra_query).strip()


def run(vault, cfg, client, backend, *, seen, deadletter, now_iso, since_iso=None, dry_run=False) -> RunReport:
    rep = RunReport()
    today = datetime.fromisoformat(now_iso).date().isoformat()
    leads = [n for n in vault.read_leads(set(_status.APPLICATION_OWNED))
             if n.status in _INFLIGHT]
    # index_by_slug, never a dict comprehension: two notes at one slug would otherwise leave
    # whichever came LAST, and for shortlist_by_slug that twin is what match_receipt then
    # weighs a receipt against -- an `applied` written to the wrong note, which is
    # irreversible and silently suppresses the real application. Dropping both instead sends
    # the receipt to the dead-letter for a human, which is where every weaker outcome already
    # goes. The ambiguous set is not needed here: not matching IS the report.
    note_by_slug, inflight_dropped = index_by_slug(leads)
    for msg in ambiguous_slug_warnings("track: in-flight lead", inflight_dropped):
        _log.warning("%s", msg)
    # A receipt's lead lives in shortlist (pre-application), not note_by_slug (in-flight
    # application states) -- match_receipt matches against this snapshot by domain.
    shortlist = vault.read_leads({"shortlist"})
    shortlist_by_slug, dropped_shortlist = index_by_slug(shortlist)
    for msg in ambiguous_slug_warnings("track: shortlisted lead", dropped_shortlist):
        _log.warning("%s", msg)
    # The twins index_by_slug DROPPED, kept for the probe below. Refusing to act on them is
    # right; going quieter about them than about a receipt that is merely ambiguous by
    # DOMAIN is not, and that is what dropping them from the matcher's input did. Read off
    # the grouping the indexer RETURNS -- a second filter over `shortlist` re-derives what
    # it already computed, and can drift from it.
    dropped_twins = [n for members in dropped_shortlist.values() for n in members]
    try:
        ids = client.search_messages(_gmail_query(cfg, now_iso, since_iso))
    except GoogleAuthError:
        rep.auth_error = True
        return rep
    # Bump carried entries before any new record, so a row first recorded THIS run
    # stays at times_surfaced=1. Outside the per-message try on purpose: a raise
    # here (corrupt/unwritable store) aborts the run before seen/lastrun save --
    # fail-safe, since nothing has been processed or seen.add'd yet.
    if not dry_run:
        deadletter.bump_surfaced()
    new_entries = []
    for mid in ids:
        if mid in seen:
            continue
        rep.msgs += 1
        try:
            msg = client.get_message(mid)
            msg["message_id"] = mid
            ics = None
            for att in msg.get("attachments", []):
                if att.get("filename", "").lower().endswith(".ics") or "calendar" in att.get("mime", "").lower():
                    ics = parse_ics(att.get("data", b"").decode("utf-8", "replace"))
                    break
            ev = classify(msg, leads, backend, cfg, ics=ics)
            rep.classified += 1
            if ev.type == "receipt":
                # classify() deliberately leaves lead_slug/candidates unset for a receipt
                # (it only knows the message IS a receipt, not whose) -- match_receipt
                # resolves WHICH shortlist lead by domain, here where the raw msg (body/
                # headers) is still in scope; reconcile only sees the resolved Event.
                m = match_receipt(msg, shortlist_by_slug.values(), cfg.ats_relay_domains,
                                  cfg.job_board_domains)
                ev.lead_slug, ev.candidates, ev.receipt_tier = m.lead_slug, m.candidates, m.tier
            res = reconcile(ev, note_by_slug, vault, cfg, client, dry_run=dry_run,
                             shortlist_by_slug=shortlist_by_slug)
            rep.results.append(res)
            # Never-regress across messages in one run: reflect the just-written
            # status back into the snapshot so the next message for this lead
            # reconciles against current, not stale, state. Only meaningful when
            # something was actually written (never in a dry-run preview). A
            # receipt-advanced lead lives in shortlist_by_slug, not note_by_slug -- check
            # both, so a second same-run receipt for the same lead sees the reflected
            # `applied` snapshot (via shortlist_by_slug). reconcile then fails that note's
            # can_apply check and SKIPS it: no second write, and -- since the pre-fix code
            # instead proposed it -- no dead-letter row carrying a `--to applied` command
            # that confirm() would refuse forever while the row re-surfaced every run.
            if not dry_run and res.status_to and ev.lead_slug:
                if ev.lead_slug in note_by_slug:
                    note_by_slug[ev.lead_slug].status = res.status_to
                elif ev.lead_slug in shortlist_by_slug:
                    shortlist_by_slug[ev.lead_slug].status = res.status_to
            # #1: a receipt for a lead whose slug TWO notes claim never reaches the matcher
            # at all -- index_by_slug dropped both twins, so `match_receipt` searched a set
            # that does not contain them, found nothing, and reconcile filed it as the quiet
            # skip reserved for an UNTRACKED job's receipt. That is the wrong quiet: the job
            # is tracked twice over, the message is `seen.add`ed and never re-queried, and
            # the only surviving trace was a log line. A receipt merely ambiguous by DOMAIN
            # proposes; this is the same class of evidence and now does too.
            #
            # Probed against the DROPPED set explicitly, never inferred from "there exists a
            # duplicate somewhere": a row raised for every unmatched receipt in a vault that
            # happens to hold one duplicate is a false signpost, and this branch's whole
            # value is that it fires on the receipts that really are about those twins.
            # Only after the deterministic pass came back empty, so it can never intercept a
            # real match.
            quiet_receipt = (ev.type == "receipt" and ev.receipt_tier == "none"
                             and res.action == "skipped")
            twin_hit = None
            if quiet_receipt and dropped_twins:
                probe = match_receipt(msg, dropped_twins, cfg.ats_relay_domains,
                                      cfg.job_board_domains)
                twin_hit = probe if probe.tier != "none" else None
            if quiet_receipt and (twin_hit or ev.llm_lead_slug or ev.llm_candidates):
                # match_receipt found NO domain evidence at all (never even a corroborated
                # match) -- the common cause is a receipt about a lead that has already
                # advanced PAST shortlist (applied/phone_screen/...), since match_receipt
                # only ever searches shortlist_by_slug and such a lead structurally cannot
                # appear there. Silently accepting reconcile's "skipped" here would be the
                # #40 loss class again: if the model mislabelled a REJECTION as "receipt",
                # that rejection vanishes and the lead sits at `applied` forever with zero
                # signal anywhere. The LLM's own (lower-trust, name-based) resolution is
                # good enough to SURFACE a review item even though it is not good enough to
                # ACT on -- so this branch only ever records a dead-letter row, never a
                # write; deterministic domain matching still owns every write, unchanged
                # above (#10 fix-round-1).
                #
                # An AMBIGUOUS fallback (the LLM's named company matches several in-flight
                # leads) is still a KNOWN-lead signal -- the email demonstrably concerns two
                # or more real leads, just not provably which -- so it must surface too
                # (#10 fix-round-2), on the same "never silently drop a known-lead signal"
                # ruling as the unique case; only a fallback resolving to NOTHING stays a
                # quiet skip. The wording echoes the generic multi-candidate hint below
                # ("ambiguous lead; pick one") rather than inventing a new shape, but
                # deliberately omits a --to applied command: unlike that generic path (whose
                # candidates are real shortlist matches with can_apply true), these
                # candidates are already in-flight, so --to applied would be refused
                # outright -- an unrunnable command is worse than an honest "look at this
                # yourself".
                rep.proposed += 1
                proposal = "receipt (unverified lead match)"
                if twin_hit:
                    # Checked FIRST: a twin hit is DETERMINISTIC domain evidence, so it must
                    # not be described by whatever lower-trust name the LLM also guessed.
                    # The remedy names the state, not a status: `--to applied` is withheld on
                    # the same ruling as the in-flight arm below -- confirm() resolves a lead
                    # by slug and this slug resolves to two notes, so the command could only
                    # be refused or, worse, land on the wrong twin. Renaming or merging the
                    # notes is the whole of the fix, and the row re-surfaces until it happens.
                    hit_slug = twin_hit.lead_slug or (twin_hit.candidates or [""])[0]
                    refs = sorted(str(n.ref) for n in dropped_twins if n.slug == hit_slug)
                    hint = (f'(receipt email for shortlisted lead "{hit_slug}" -- {len(refs)} '
                            f"notes claim that slug ({'; '.join(refs)}), so which lead this "
                            f"is cannot be known; rename or merge them, then re-run)")
                    proposal = "receipt (lead slug claimed by two notes)"
                    # `lead` and `candidates` stay EMPTY. Both feed the runnable `sluice track
                    # confirm --lead <slug>` hints, and this slug is exactly the one that
                    # resolves to two notes -- offering it would hand the user a command that
                    # lands on whichever twin the resolver picks. The refs go in the prose.
                    lead, candidates = "", []
                elif ev.llm_lead_slug:
                    hint = (f'(receipt email for in-flight lead "{ev.llm_lead_slug}" -- the '
                            f"match could not be verified by sender/link domain; review "
                            f"manually)")
                    lead, candidates = ev.llm_lead_slug, []
                else:
                    names = "; ".join(f'"{c}"' for c in ev.llm_candidates)
                    hint = (f"(ambiguous lead; pick one -- candidates: {names}; "
                            f"in-flight leads, review manually)")
                    lead, candidates = "", ev.llm_candidates
                entry = Entry(message_id=mid, lead=lead, candidates=",".join(candidates),
                              ev_type=ev.type, proposal=proposal,
                              hint=hint, first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                if not dry_run:
                    _dl_write(rep, lambda: deadletter.record(entry))
            elif res.action == "applied":
                rep.auto += 1
                # Symmetric with confirm's clear-on-advance: an auto-resolved lead's
                # pending proposals are resolved too, so its dead-letter entries stop
                # re-surfacing with a now-un-runnable confirm hint (the lead is already
                # terminal). Clear on ev.lead_slug -- the same key run() records under.
                if not dry_run and ev.lead_slug:
                    _dl_write(rep, lambda: deadletter.clear_lead(ev.lead_slug))
            elif res.action == "proposed":
                rep.proposed += 1
                target = _PROPOSE_TARGET.get(ev.type, "")
                if ev.lead_slug and target:
                    hint = f'job-sluice track confirm --lead "{ev.lead_slug}" --to {target}'
                elif ev.candidates:
                    # Each option needs its own "job-sluice track confirm" prefix -- prefixing
                    # only the first (as an earlier version did) leaves every option after the
                    # first ";" reading as a bare --lead/--to fragment, not a runnable command.
                    # Stays single-line: `hint` is printed as one row of the OPEN PROPOSALS
                    # report (cli.py's `... {e.proposal} :: {e.hint}`), so an embedded newline
                    # here would break that format rather than just being ugly.
                    opts = "; ".join(
                        f'job-sluice track confirm --lead "{c}" --to {target or "<status>"}'
                        for c in ev.candidates)
                    hint = f"(ambiguous lead; pick one: {opts})"
                else:
                    hint = f'(no runnable action for type "{ev.type}" / lead "{res.lead}"; review manually)'
                entry = Entry(message_id=mid, lead=ev.lead_slug or "",
                              candidates=",".join(ev.candidates), ev_type=ev.type,
                              proposal=res.proposal or ev.type, hint=hint,
                              first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                # record BEFORE seen.add: a write failure raises, the `except`
                # below skips seen.add, and the message re-processes next run.
                if not dry_run:
                    _dl_write(rep, lambda: deadletter.record(entry))
            if res.calendar in ("created", "updated"):
                rep.calendar_added += 1
            if not dry_run:
                seen.add(mid)
        except GoogleAuthError:
            rep.auth_error = True
            break
        except Exception as exc:
            rep.failures += 1
            # Never silent. A failure deliberately skips seen.add so the message retries, which
            # means a message that fails DETERMINISTICALLY (a malformed attachment, an API that
            # rejects a value this message always produces) fails again on every future run --
            # and the digest's bare `failures=N` cannot tell that apart from a one-off blip.
            # The id and the cause go in the message itself, not only the traceback, so the
            # line is diagnosable wherever logs are read as text.
            _log.exception("track: message %s failed: %s", mid, exc)
    # Emit the full open set. Non-dry: the store already holds this run's new rows,
    # so it is the single source of truth. Dry: union the persisted set with this
    # run's computed-new (keyed by message_id, persisted wins), recording nothing.
    if dry_run:
        by_id = {e.message_id: e for e in new_entries}
        for e in deadletter.open_entries():
            by_id[e.message_id] = e
        rep.open_proposals = sorted(by_id.values(), key=lambda e: (e.first_seen, e.message_id))
    else:
        rep.open_proposals = deadletter.open_entries()
    return rep


def confirm(vault, cfg, slug, to, *, deadletter, when=None, dry_run=False) -> dict:
    matches = [n for n in vault.read_leads() if slug_matches(n, slug)]
    if not matches:
        return {"ok": False, "reason": "no_match"}
    if len(matches) > 1:
        return {"ok": False, "reason": "ambiguous"}
    note = matches[0]
    # can_transition routes `--to applied` through can_apply (shortlist-only) and
    # everything else through can_advance (the ladder) -- a receipt confirmation and
    # an on-ladder confirmation share this one entry point since confirm() accepts an
    # arbitrary `--to` target (#10).
    if not _status.can_transition(note.status, to):
        return {"ok": False, "reason": note.status}
    if not dry_run:
        # BEFORE the status write. `clear_lead` below can raise on an unreachable store,
        # and by then the advance has already landed -- leaving a row nobody can clear,
        # because re-running is refused on a transition that already happened.
        deadletter.check_reachable()
        fields = {"status": _status.normalize(to), "last_signal": date.today().isoformat()}
        if when:
            fields["interview_date"] = f'"{when}"'
        try:
            vault.update_fields(note.ref, fields)
        except VaultConflict:
            # #16: a concurrent edit won the write race; return BEFORE clear_lead,
            # so the dead-letter row survives (clearing it here would be #49's
            # silent loss -- the confirm never actually took effect).
            return {"ok": False, "reason": "conflict"}
        # Clear only after can_advance passed AND the write succeeded: a refused
        # confirm returned above and never reaches here, and a conflicted write
        # returns above too, so neither path ever deletes a row (deleting on a
        # non-write would be #49's silent loss on the clear path).
        deadletter.clear_lead(note.slug)
    return {"ok": True, "from": note.status, "to": _status.normalize(to)}
