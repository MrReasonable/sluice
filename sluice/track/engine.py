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
from sluice.core.vault import frontmatter_safe
from sluice.track.deadletter import EV_TYPE_CALENDAR, EV_TYPE_FAILURE, Entry
from sluice.track.receipt import match_receipt

_log = get_logger("track.engine")

_INFLIGHT = ("applied", "phone_screen", "interview", "offer")  # non-terminal application states

# Map a classified event type -> the target status a proposal would advance to (F7).
_PROPOSE_TARGET = {"phone_screen": "phone_screen", "interview": "interview",
                   "rejection": "rejected", "offer": "offer", "receipt": "applied"}


@dataclass(repr=False)
class TrackFailure:
    """One message that could not be processed, in two renderings.

    ONE field carrying both, deliberately: a separate "safe summary" field would drift from
    the real cause, and the count would end up describing a different set than the detail.

    The split exists because the tiers are genuinely different. `cause` is the full exception
    text and can carry the contents of your mail: `classify` hands the message BODY to a
    backend, `_event_body` carries the interview summary and meeting url, and a
    `googleapiclient.HttpError` renders the request URI -- which for a Gmail search is
    `_gmail_query`'s `q=`, built from `gmail_extra_query`. That is fine for the operator's own
    stderr and for the gitignored dead-letter db (`deadletter.py` already accounts for
    message-ids and proposal text as private runtime state). It is NOT fine for a Telegram
    message, which leaves the machine. `track/config.py` states the same principle for its own
    errors: an exception message travels further than the file does."""
    message_id: str
    cause: str
    # The exception CLASS NAME, stored at construction rather than parsed back out of
    # `cause`. `safe()` used to do `cause.split(":", 1)[0]`, which is a format contract
    # between the producer and the redactor with nothing guarding it: a second producer
    # written the obvious way -- `TrackFailure(mid, str(exc))` -- makes `safe()` emit
    # unbounded free text up to the first colon, so a `ValueError("no lead matched Example Co
    # - Staff Engineer: giving up")` would render the employer and role straight to Telegram.
    # Two values derived from the same exception in the same expression cannot drift, which
    # was the stated objection to a second field and does not apply to this one.
    kind: str = "error"

    def safe(self) -> str:
        """What may leave the machine: the id and the exception TYPE, never its text.

        Still actionable -- the id is what `track dismiss --id` takes and what the dead-letter
        row is keyed on -- while carrying none of the message content."""
        return f"{self.message_id} ({self.kind or 'error'})"

    def detail(self) -> str:
        """The full cause. LOCAL ONLY: the operator's own stderr and the gitignored
        dead-letter row. Never a notification, a bug report, or anything that leaves here."""
        return f"{self.message_id}: {self.cause}"

    def __str__(self) -> str:
        """The SAFE rendering, deliberately.

        This returned the full cause, so every natural thing a caller writes -- `str(f)`,
        `f"{f}"`, `_log.info("%s", f)` -- leaked message content by default, and the docstring
        above spent a paragraph explaining why that content is dangerous before making it the
        default. Getting it wrong now produces a less useful line rather than a disclosure;
        the full text is available, but you have to ask for it by name."""
        return self.safe()

    # `repr=False` on the dataclass, then repr == str. The GENERATED `__repr__` prints every
    # field, `cause` included, so a safe `__str__` closed only the bare-object case:
    # `RunReport.failures` is a LIST, and `_log.warning("%s", rep.failures)` -- the most
    # natural debug line for a list-valued field -- renders each element's repr. `RunReport`
    # is a dataclass too, so `"%s" % rep` did the same one level up. "Getting it wrong costs
    # detail rather than causing a disclosure" was true only until the object went into a
    # container, which is where objects usually are.
    __repr__ = __str__


@dataclass
class RunReport:
    msgs: int = 0
    classified: int = 0
    auto: int = 0
    proposed: int = 0
    calendar_added: int = 0
    calendar_assumed_tz: int = 0   # of those, how many booked an instant we GUESSED
    # list[TrackFailure], not a count -- matching triage's RunReport, which set the
    # precedent. `failures=N` in the digest is len() of this list, so the count and the
    # detail come off one field and cannot disagree. A bare int meant a cron run could
    # say "failures=1" and name nothing, on a stream nobody reads.
    failures: list = field(default_factory=list)
    # The Gmail search hit its cap, so this run did not see every matching message. HOLDS
    # the lastrun watermark (see `app.py`'s `_save_lastrun` gate): advancing would move
    # `after:` to today and put every starved message permanently out of reach, which is the
    # opposite of recoverable.
    search_truncated: bool = False
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
        ids, rep.search_truncated = client.search_messages(_gmail_query(cfg, now_iso, since_iso))
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
    # ev_type of every ALREADY-OPEN row, by message_id. Was failure-rows-only, which was too
    # narrow in both directions: a repeatedly-failing message must not accumulate a row per
    # run (the original reason), AND any stale row for a message being re-processed collides
    # with the new one on the message_id PRIMARY KEY.
    _open_ev_type = {e.message_id: e.ev_type for e in deadletter.open_entries()}
    # message_ids this run has filed a row for. `_clear_stale_row` must not delete a row
    # written moments earlier: the `needs_review` record runs BEFORE the clear and leaves
    # `_open_ev_type[mid] == EV_TYPE_CALENDAR`, so a clear keyed only on the kind would
    # remove the very row it just wrote.
    _recorded_now = set()

    def _record_replacing(message_id, entry):
        """Record `entry`, REPLACING whatever row already holds that message_id.

        THREE record sites go through this: the quiet-receipt branch, the `proposed` branch,
        and the `needs_review` calendar row. The fourth -- the failure row at the bottom of
        the per-message `except` -- deliberately does not, because it must never replace a
        proposal row carrying a runnable `confirm` hint with a bare "failed"; it is guarded
        instead on there being no open row at all.

        (An earlier version of this docstring said "BOTH", then "the TWO proposal record
        sites", while the count was three and then four. A helper that exists to stop a site
        being missed is the worst possible place to miscount the sites.)

        The table is keyed on message_id, so a row from an earlier run collides with the new
        one, and `record` raises on a differing row rather than discarding it silently.
        Missing a site converts the old silent loss into a permanent stall: `deadletter_error`
        every run, so the watermark never advances and `_gmail_query`'s `after:` widens
        without bound.

        Scoped to ANY stale row, not just `failure`. A proposal row differs between runs
        whenever its `proposal` string embeds a model confidence, which is the routine shape
        of a re-processed message, and that hit the raise just as hard."""
        _dl_write(rep, lambda: deadletter.record(entry, replace=message_id in _open_ev_type))
        _open_ev_type[message_id] = entry.ev_type
        _recorded_now.add(message_id)

    def _clear_stale_row(message_id):
        """A message that PROCESSED has resolved its ENGINE-authored row, whatever the outcome.

        The clear used to live inside the record helper, so it reached the two outcomes that
        record a row and missed `applied` and `skipped`. On those, a row from one transient
        Gmail 500 survived its own resolution and re-surfaced every run with `times_surfaced`
        climbing, for a message now in `seen` and never reprocessed -- clearable only by a
        hand-typed `track dismiss --id`. Indistinguishable from a live failure, which is how
        an operator learns to ignore the real one.

        `clear_lead` cannot do this: the failure Entry is written with `lead=""`, so a
        lead-keyed clear structurally cannot reach it.

        BEST-EFFORT, unlike every other dead-letter write here. This runs at the very end of a
        message that SUCCEEDED -- the status advance and the calendar write have both already
        landed -- so re-raising into the per-message `except` would report a fully-applied
        message as a failure and skip `seen.add`. Next run then re-processes an advance that
        has already happened, and for a terminal target `can_advance` refuses, filing a row
        whose hint is a `confirm` command that can never succeed. `deadletter_error` is still
        set (the watermark still holds, which is the part that matters); only the raise is
        swallowed."""
        # A row filed by THIS run is not stale. Without this the `needs_review` record
        # above -- which runs first and leaves EV_TYPE_CALENDAR in `_open_ev_type` --
        # would be deleted by the clear a few lines later.
        if message_id in _recorded_now:
            return
        if _open_ev_type.get(message_id) not in (EV_TYPE_FAILURE, EV_TYPE_CALENDAR):
            return
        try:
            _dl_write(rep, lambda m=message_id: deadletter.clear_id(m))
            _open_ev_type.pop(message_id, None)
        except Exception:
            _log.exception("track: could not clear the stale row for %s", message_id)
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
                    _record_replacing(mid, entry)
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
                # `startswith`, not an equality on one reason code. `reconcile` now emits
                # `cancel-unresolved` AND `cancel-foreign`, and matching one literal is
                # how the second reason would inherit `_PROPOSE_TARGET` and hand the
                # operator a `confirm --to interview` for an interview that was
                # cancelled -- the exact runnable-and-wrong hint this branch exists to
                # prevent.
                if (res.proposal or "").startswith("cancel-"):
                    # MUST NOT inherit _PROPOSE_TARGET. `ev.type` for a cancelled interview
                    # invite is still "interview", so the generic branch handed the operator
                    # `confirm --to interview` -- a runnable command that BOOKS the thing that
                    # was just cancelled. An unrunnable hint is worse than an honest "look at
                    # this yourself" (see the else branch); a runnable and WRONG one is worse
                    # than either.
                    uid = getattr(ev.ics, "uid", "") or "?"
                    # Deliberately does NOT name a cause. `unresolved` now covers two (no
                    # DTSTART to search with, and a calendar window we know was truncated),
                    # and an earlier version hardcoded the first -- which became a false
                    # statement the moment the second was added.
                    hint = (f'(cancellation for uid "{uid}" could not be matched to a calendar '
                            f'entry, so nothing was deleted. Check your calendar and remove it '
                            f'by hand, then `job-sluice track dismiss --id {mid}`)')
                elif ev.lead_slug and target:
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
                              candidates=",".join(ev.candidates),
                              # A cancel we could not match is a CALENDAR row, not a status
                              # proposal, so an unrelated later advance on the same lead does
                              # not `clear_lead` away the "remove it by hand" instruction.
                              ev_type=(EV_TYPE_CALENDAR
                                       if (res.proposal or "").startswith("cancel-")
                                       else ev.type),
                              proposal=res.proposal or ev.type, hint=hint,
                              first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                # record BEFORE seen.add: a write failure raises, the `except`
                # below skips seen.add, and the message re-processes next run.
                if not dry_run:
                    _record_replacing(mid, entry)
            if res.needs_review and not any(e.message_id == mid for e in new_entries):
                # Recorded independently of `action`, which is the whole point: this fires on
                # the `applied` path (status advanced, nothing booked) and on the `calendar`
                # path, and NEITHER of those records anything. The `not any(...)` guard is for
                # the case where the action branch above already filed a row for this message
                # -- one message, one row, since message_id is the table's PRIMARY KEY.
                #
                # `ev_type` is EV_TYPE_CALENDAR rather than `ev.type`, so `clear_lead` cannot
                # delete it when an unrelated later email advances the same lead. A stale
                # calendar entry is not resolved by a status advance.
                uid = getattr(ev.ics, "uid", "") or "?"
                entry = Entry(
                    message_id=mid, lead=ev.lead_slug or "", candidates="",
                    ev_type=EV_TYPE_CALENDAR, proposal=res.needs_review,
                    hint=(f'(the calendar entry for uid "{uid}" could NOT be created or '
                          f'verified -- the lead was still advanced, but check your calendar '
                          f'and add it by hand, then `job-sluice track dismiss --id {mid}`)'),
                    first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                if not dry_run:
                    _record_replacing(mid, entry)
            if res.calendar in ("created", "updated"):
                rep.calendar_added += 1
                if res.calendar_assumed_tz:
                    rep.calendar_assumed_tz += 1
            if not dry_run:
                # Whatever the outcome was, this message PROCESSED, so any failure row from an
                # earlier run is resolved. Here rather than in the record helper, because the
                # `applied` and `skipped` outcomes record nothing and were therefore missed.
                _clear_stale_row(mid)
                seen.add(mid)
        except GoogleAuthError:
            rep.auth_error = True
            break
        except Exception as exc:
            rep.failures.append(TrackFailure(message_id=mid,
                                             cause=f"{type(exc).__name__}: {exc}",
                                             kind=type(exc).__name__))
            # Never silent, and never only a log line. A failure skips seen.add, which leaves
            # the message retryable -- but only while it stays inside `_gmail_query`'s
            # day-granular `after:` window, and `app.py` advances the lastrun watermark on
            # this very run (`rep.failures` is not in that gate, unlike auth_error and
            # deadletter_error). So a DETERMINISTIC failure gets roughly a day of retries and
            # is then never queried again: absent from `seen`, and until now absent from the
            # dead-letter store and every report too. After ~2 runs it ceased to exist (#139).
            #
            # NOT fixed by holding the watermark. One permanently-poisoned message would
            # freeze it forever and the window would grow without bound -- a bigger fetch
            # every run, now that #137 lifted the page cap -- while the failing message, being
            # the OLDEST thing in that window, is the least likely to be reached. Make the
            # FAILURE durable instead, with the machinery #49 already built: the row
            # re-surfaces every run via bump_surfaced() and carries a dismiss lever.
            _log.exception("track: message %s failed: %s", mid, exc)
            # ONCE PER MESSAGE, whatever row already holds it -- not just a failure row.
            # Comparing against EV_TYPE_FAILURE let this through whenever the open row was a
            # PROPOSAL, and `record` (no `replace` here, deliberately) then raised on the
            # differing row: `deadletter_error` every run, `_save_lastrun` skipped forever,
            # `after:` frozen while now advances, the window growing without bound. Reachable
            # any time a run records a proposal and dies before `_save_seen`, which runs after
            # `run()` returns.
            #
            # NOT `replace=True`: the open proposal carries a runnable `confirm` hint, and
            # replacing it with "failed" would discard the more useful row. The existing row
            # keeps surfacing the message, and `rep.failures` + the digest + the notify still
            # name the failure.
            if not dry_run and mid not in _open_ev_type:
                # Once per message. A deterministic failure fails again next run while its row
                # is still open, and re-recording would turn one broken message into a growing
                # pile of identical rows -- the digest getting noisier the longer the problem
                # goes unfixed, which is backwards.
                entry = Entry(message_id=mid, lead="", candidates="",
                              ev_type=EV_TYPE_FAILURE,
                              proposal="failed", hint=f"processing failed: {exc}",
                              first_seen=today, times_surfaced=1)
                # Same _dl_write as every other row -- it sets `rep.deadletter_error`, which
                # holds the watermark, so an unpersistable failure keeps the message inside
                # the query window. That is the one case where holding it IS right.
                #
                # But it also RE-RAISES, and we are already inside the per-message `except`:
                # an escaping raise here has nothing left to catch it and would abort the
                # whole run over one unrecordable message. Swallow it -- `deadletter_error`
                # is already set by then, which is the part that matters.
                try:
                    _dl_write(rep, lambda e=entry: deadletter.record(e))
                except Exception:
                    _log.exception("track: could not dead-letter the failure for %s", mid)
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
    when_dropped = False
    if not dry_run:
        # BEFORE the status write. `clear_lead` below can raise on an unreachable store,
        # and by then the advance has already landed -- leaving a row nobody can clear,
        # because re-running is refused on a transition that already happened.
        deadletter.check_reachable()
        fields = {"status": _status.normalize(to), "last_signal": date.today().isoformat()}
        if when:
            # The THIRD `interview_date` write site. #141 guarded `reconcile._advance` and its
            # commit claimed the class was closed while this one sat one module over, writing
            # the same field with the same quoting -- the exact miss that commit criticised
            # #111 for. `--when` is operator-typed but `frontmatter_safe`'s own docstring names
            # "a CLI value that may have been pasted rather than typed" as needing the guard,
            # and `apply/record.py` already guards `--url` on that reasoning. Pasting a time
            # out of an invite (`Tuesday 3pm "BST"`) writes YAML the vault cannot read back,
            # AFTER the status advance has landed.
            safe_when = frontmatter_safe(when)
            if safe_when:
                fields["interview_date"] = f'"{safe_when}"'
            else:
                # Surfaced, not dropped in silence: the operator typed this, so they are the
                # one person who can retype it. Matches `record.py`'s `url_dropped`.
                when_dropped = True
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
    return {"ok": True, "from": note.status, "to": _status.normalize(to),
            "when_dropped": when_dropped}
