"""Write triage outcomes back to the vault, format-preserving and never clobbering
a lead that has already entered the application lifecycle (applied, phone_screen,
...). Reject maps to the canonical `dismiss`; a plain-language reason and the
judge's reasoning are appended (once) to relevance_notes."""
import math
from datetime import date

from sluice.core import status as _status
from sluice.core.leads import FRAMING_KEYS, blocked_stamp
from sluice.core.log import get_logger
from sluice.core.vault import frontmatter_safe

_log = get_logger("triage.apply")
_DECISION_STATUS = {"reject": "dismiss", "needs_review": "needs_review", "keep": "new",
                    "unjudgeable": "unjudgeable"}

# The judge's OWN vocabulary -- four verdicts, exactly what triage/prompt.py's
# `_SCAFFOLD_TAIL` (its "Output schema" block) and triage/judge.py's `_build_prompt`
# tail ask the model for. Named by SYMBOL, not by line number: a line number is
# accurate only until someone inserts anything above it, and a citation that has
# silently drifted is worse than none.
#
# `unjudgeable` joined in #300. Before it, the schema offered no way to say "the page
# I was given is not a job description", so the prompt told the model to score
# conservatively instead -- and a conservative score on a page with no evidence lands
# at or just above the dismiss threshold, which routes to `research`. `research` means
# "a human should investigate this", so every bot-check and consent wall filed itself
# as a human research task and was re-judged, unchanged, every night thereafter. The
# model was diagnosing the failure correctly in `fit_reasoning` the whole time; it had
# nowhere structured to put the diagnosis. This is that place.
_JUDGE_VERDICTS = frozenset({"shortlist", "research", "dismiss", "unjudgeable"})


def clamp_verdict(raw: str) -> str:
    """The model's verdict, or `needs_review` if it said something else.

    `_status.normalize` passes an unrecognised value through untouched, and
    `apply_verdict` used to write whatever came back straight into `status`. That was a
    live hole: `require_status` checks only the status the lead is CURRENTLY in, not
    the one being written, so a model returning `verdict: "applied"` on a `new` lead
    wrote an APPLICATION-OWNED status from triage -- the never-regress invariant,
    reachable from model output.

    Pure, and shared: the engine's counts row and audit trail call this too, so a run
    reports the status that was actually WRITTEN rather than the raw model string. A
    second copy inline in engine.py would be exactly the hand-list drift this codebase
    keeps engineering out -- and it WOULD drift, since the two live in different files.
    """
    s = _status.normalize(raw or "")
    return s if s in _JUDGE_VERDICTS else "needs_review"


# The fields the judge's schema declares as strings and as lists (triage/judge.py's prompt tail).
# `verdict` is absent from the first tuple on purpose: an unusable verdict is REJECTED, never
# repaired, because repairing it would write a status on no judgement at all (#329).
_STRING_FIELDS = ("fit_reasoning", "recommended_next_action")
_LIST_FIELDS = ("culture_flags", "concerns")


def _normalise_list(field, value, slug):
    """A verdict's list field as a list of frontmatter-safe strings.

    One item at a time, never the joined value: a single unsafe item used to fail the joined
    string, skip the whole key and leave the PREVIOUS verdict's value on the note, which the CV
    composer now reads as framing (#329). A non-string item is dropped rather than `str()`-ed,
    because `str({...})` writes a Python repr into a user's note. Logged by field and lead,
    never by value: the value is model output that has just failed a safety check."""
    if value is None or value == "" or value == [] or value == ():
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        _log.warning("triage: %s dropped for %s -- not a list of strings", field, slug)
        return []
    kept = []
    for item in items:
        safe = frontmatter_safe(item) if isinstance(item, str) else None
        if safe is None:
            _log.warning("triage: an item of %s dropped for %s -- not a safe string", field, slug)
            continue
        kept.append(safe)
    return kept


def _normalise_score(value, slug):
    """`relevance_score` as an int, with `0` for anything unusable -- the value today's `or 0`
    already gives a missing score. `bool` is checked FIRST: it subclasses `int`, so `True`
    would otherwise score 1. A string that is not an int is read as a float and, when finite,
    truncated exactly as the float arm truncates, so the same number scores the same whether the
    judge sent it as a string or as a number."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        _log.warning("triage: relevance_score for %s was not a number -- scored 0", slug)
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
        try:
            number = float(value.strip())
        except ValueError:
            pass
        else:
            if math.isfinite(number):
                return int(number)
    _log.warning("triage: relevance_score for %s was not a number -- scored 0", slug)
    return 0


def normalise_fields(raw, slug):
    """Every field of a judge verdict except `lead_id`, repaired or rejected (#329).

    Returns `(fields, "")`, or `(None, reason)` when `raw` is not a dict or its `verdict` is not
    a non-blank string. Shared by `normalise_verdict` (the engine's entry point) and
    `apply_verdict` (which is handed the note, so needs no `lead_id`). Idempotent: a second pass
    over its own output returns an equal dict and logs nothing."""
    if not isinstance(raw, dict):
        return None, "not a JSON object"
    verdict = raw.get("verdict")
    if not isinstance(verdict, str) or not verdict.strip():
        return None, "no usable verdict field"
    out = dict(raw)
    for field in _STRING_FIELDS:
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            _log.warning("triage: %s dropped for %s -- not a string", field, slug)
            value = ""
        out[field] = value or ""
    out["relevance_score"] = _normalise_score(raw.get("relevance_score"), slug)
    for field in _LIST_FIELDS:
        out[field] = _normalise_list(field, raw.get(field), slug)
    return out, ""


def normalise_verdict(raw):
    """A judge verdict fit to apply, or `(None, reason)` (#329).

    The engine needs `lead_id` to match a verdict to its note, so a verdict without a usable one
    is rejected here; everything else is `normalise_fields`. One function decides the rejection
    and names its reason, so the failure line that reports it cannot drift from the decision."""
    if not isinstance(raw, dict):
        return None, "not a JSON object"
    lead_id = raw.get("lead_id")
    if not isinstance(lead_id, str) or not lead_id.strip():
        return None, "no usable lead_id"
    # The STRIPPED id is what is checked above and what a note is matched by below, so it is
    # also what must ride forward in the returned verdict -- `normalise_fields` copies `raw`
    # verbatim (`out = dict(raw)`), so passing `raw` itself here would carry the padded
    # spelling through unchanged and the engine's `note_by_id` lookup would then fail on a
    # verdict whose id names a real lead.
    cleaned = dict(raw, lead_id=lead_id.strip())
    return normalise_fields(cleaned, cleaned["lead_id"])


def _guarded(note) -> bool:
    if _status.is_application_owned(note.status):
        _log.info("skip %s: application-owned status %s", note.ref, note.status)
        return True
    return False


# Which statuses each decision may be written OVER. The default is the whole
# triage-owned set: never-regress permits triage to rewrite freely among its own states,
# and `shortlist -> dismiss` after re-reading a JD is a normal, correct re-judgement.
#
# `unjudgeable` is the one exception, and the difference is EVIDENCE (#169, found in
# review round 2). Every other decision here is a JUDGEMENT -- something read the posting
# and concluded. `unjudgeable` records the ABSENCE of one: the JD never arrived. Writing
# an absence over a verdict destroys a real conclusion on no evidence at all, and the
# measured case is the bad one -- a transient fetch failure during
# `triage run --status shortlist` demoted a SHORTLISTED lead (one already carrying a
# composed CV pointer, which stayed on the note pointing at nothing), left
# `read_leads({"shortlist"})` empty so cv/apply/track saw no verdict and no route back,
# and exited 0.
#
# `new` and `unjudgeable` are the only two triage states that carry no verdict, so they
# are the only two this may overwrite. Deliberately NOT `DEFAULT_TRIAGE_STATUSES`, which
# is the SELECTION default -- which leads a run READS -- a different concern that merely
# overlaps today; conflating a selection set with a write guard is the same
# cache-key/identity-key mistake #109 already made once. Keyed on the DECISION rather
# than passed by the caller so a future call site cannot forget it.
_DECISION_REQUIRE = {"unjudgeable": frozenset({"new", "unjudgeable"})}

# The same never-overwrite-a-conclusion rule as `_DECISION_REQUIRE` above, for the VERDICT
# path opened by #300. Keyed on the CLAMPED status for the same reason `_DECISION_REQUIRE`
# is keyed on the decision: a call site cannot forget to pass it.
#
# The permitted set is IDENTICAL to `_DECISION_REQUIRE["unjudgeable"]`, and the two are
# still written out separately rather than aliased: they guard different call sites and a
# future divergence should be a visible edit to one of them, not a silent widening of both.
#
# An earlier draft of #300 also permitted `research`, reasoning that a `research` reached
# by scoring an unreadable page is an artifact rather than a conclusion. That reasoning
# does not survive contact with the field it depends on: `status` records WHERE a lead is,
# never HOW it got there, so a conservative-score artifact is byte-identical to a research
# task a human set by hand in Obsidian. Overwriting on that basis breaks never-clobber
# against the very person whose queue it is. `new` and `unjudgeable` remain the only two
# triage states that carry no decision at all, which is what makes them writable here.
#
# The cost is that leads already parked in `research` by the OLD behaviour stay parked.
# Clearing them rewrites a human's queue, so it is a pass a human runs: the refusal stamps
# `triage_blocked` (see _stamp_blocked) and `job-sluice leads unpark` lists those leads and
# moves them only on `--apply` (#302), never a nightly cron acting on them silently.
_VERDICT_REQUIRE = {"unjudgeable": frozenset({"new", "unjudgeable"})}

# The frontmatter keys a person is invited to hand-edit: the CV composer's framing roster
# (#329, `core.leads.FRAMING_KEYS`, derived rather than re-typed) PLUS the triage-only keys
# #378 adds. A multi-line value typed into one is left alone by a triage write rather than
# corrupted by one; see `core/vault.py::_holds_multiline_value`. The #378 keys are added
# HERE and never to FRAMING_KEYS: that roster is what `cv/engine.py` feeds the composer, so a
# research question there would reach every CV prompt. A hand-typed block under
# `triage_next` therefore outlives a newer verdict's action: never-clobber outranks
# freshness. `followup_asked` is defence in depth: a block value there reads blank, so
# `apply_verdict` never writes the key over one today (see its stale-stamp rule), and this
# keeps a later writer from corrupting it either. `triage_blocked` (#302) is defence in depth
# the same way: a clear runs only when the snapshot holds a date stamp, and a value turned
# multi-line since then fails the clear's `require_unchanged` first.
_HAND_EDITABLE_KEYS = frozenset(FRAMING_KEYS) | {"triage_next", "followup_asked",
                                                 "triage_blocked"}

_RESEARCH = frozenset({"research"})


def _stamp_blocked(vault, note, today: str) -> str:
    """Record a REFUSED `unjudgeable` on a `research` lead as `triage_blocked: <today>` (#302).

    `_VERDICT_REQUIRE`/`_DECISION_REQUIRE` refuse that write, rightly: `status` cannot tell a
    conservative-score artifact from a research task a human set. The stamp is what lets
    `job-sluice leads unpark` list those leads for a human to move. It sets one key and
    nothing else, and never writes over a person's own text in that key: a `triage_blocked`
    that is not a stamp (`core/leads.py::blocked_stamp`) is theirs, so a snapshot holding one
    returns "unchanged" without a write. `preserve_block_values` cannot stand in for that
    check, since it guards only a value spread over several lines, and a word, a bare `|` or
    a quoted string all sit on the key's own line. On this write it is a backstop only:
    `require_unchanged` also compares whether the value spans several lines, so one turned
    multi-line since the snapshot is refused there first.

    Only a lead whose SNAPSHOT is `research` is tried, so a refusal on `shortlist`,
    `dismiss` and the rest costs no extra write; a lead hand-moved to `research` while being
    judged can be stamped on a later run whose verdict is again unjudgeable. The write's own
    guards are re-read fresh inside the store's CAS: the lead must still be at `research`,
    must not have been marked asked (a follow-up question outranks the stamp), and its
    `triage_blocked` must still read what the snapshot read (`require_unchanged`), so text
    typed into the key while the lead was being judged refuses the stamp. An absent key and a
    blank one both read as `("", False)` there, which is what lets the first stamp land.

    "blocked" when the stamp landed, or when the snapshot already carried today's stamp (a
    second run the same day is a byte-identical no-op, so the write returns False). The
    store has no single-note read, and a whole-vault re-read per refused lead is too dear
    for the nightly loop, so that second arm reads the snapshot. Residual, stated: a lead
    stamped earlier today that leaves `research` mid-judge is counted once more in this
    run's digest. The outcome only feeds a count; `leads unpark` re-reads before acting."""
    if _status.normalize(note.status) != "research":
        return "unchanged"
    raw = note.fm.get("triage_blocked") or ""
    if raw.strip() and not blocked_stamp(note.fm):
        return "unchanged"
    # Written UNQUOTED, like `Sluice.mark_asked`'s `followup_asked`: `today` comes from
    # `date.isoformat()` and can carry no quote, so it needs no quoting or frontmatter_safe.
    if vault.update_fields(note.ref, {"triage_blocked": today},
                           require_status=_RESEARCH,
                           require_blank=frozenset({"followup_asked"}),
                           require_unchanged={"triage_blocked": raw},
                           preserve_block_values=frozenset({"triage_blocked"})):
        return "blocked"
    return "blocked" if blocked_stamp(note.fm) == today else "unchanged"


def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    today = date.today().isoformat()
    tag = f"[triage {today}]"
    # require_status (#109 inv2-001): the pre-existing _guarded() check above reads
    # note.status, a plain dataclass field frozen at read_leads() time -- byte-identical
    # to no guard at all against a real vault. This re-reads the FRESH status inside the
    # CAS transform, closing the window a #109 tier-2 fetch (real page load, seconds) now
    # opens ahead of this write.
    # #302: a landed classification clears a `triage_blocked` stamp, as a landed verdict
    # does (see apply_verdict). Only a real date stamp is cleared; a hand-typed value is left.
    fields = {"status": new_status}
    require_unchanged = None
    stamp = blocked_stamp(note.fm)
    if stamp:
        fields["triage_blocked"] = '""'
        require_unchanged = {"triage_blocked": stamp}
    wrote = vault.update_fields(
        note.ref, fields,
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
        require_status=_DECISION_REQUIRE.get(decision, frozenset(_status.TRIAGE_OWNED)),
        require_unchanged=require_unchanged,
        preserve_block_values=_HAND_EDITABLE_KEYS)
    # #118: `wrote=False` is either a genuine no-op -- require_status refused on a fresh
    # re-read (the lead already left TRIAGE_OWNED), or a byte-identical rewrite -- or (#302)
    # the stale-stamp `require_unchanged` refusing a `triage_blocked` re-stamp that landed
    # mid-classify, where the newer stamp stands. A REAL content collision raises
    # VaultConflict instead, caught one level up in triage/engine.py. Never a failure.
    if wrote:
        return "applied"
    if decision == "unjudgeable":
        # #302: the pre-judge gate's refusal on a `research` lead is recorded the same way
        # the judge's is. See _stamp_blocked.
        return _stamp_blocked(vault, note, today)
    return "unchanged"


def apply_verdict(vault, note, verdict, dossier) -> str:
    if _guarded(note):
        return "skipped"
    # #329: repaired here as well as in the engine, so a direct caller gets the same behaviour.
    # A second pass over the engine's already-normalised verdict changes nothing.
    verdict, why = normalise_fields(verdict, note.slug)
    if verdict is None:
        _log.warning("triage: verdict for %s ignored -- %s", note.slug, why)
        return "skipped"
    status = clamp_verdict(verdict["verdict"])
    score = verdict["relevance_score"]
    # BOTH untrusted, and both were written into quoted YAML scalars raw. `culture_flags` is
    # the model's verdict JSON; `glassdoor_rating` comes off the fetched dossier. A `"` closes
    # the scalar early and everything after it is parsed as frontmatter -- executed: a single
    # culture flag injected a SECOND `status:` key, and YAML resolves last-wins, so model
    # output could regress a lead's status. That is the never-regress invariant, reachable
    # from a model.
    #
    # Same class as #141 in `track/reconcile.py`, one sub-app over. It survived that sweep
    # because the sweep's boundary was the `track` package -- "a hand-list with extra steps",
    # in the words of the test that drew the boundary.
    #
    # Abstain on the ITEM, never the write: `_normalise_list` has already dropped each unsafe
    # list item on its own, so a verdict's other flags and concerns still land. The joined
    # `frontmatter_safe` check below therefore cannot fire for `culture_flags` or
    # `triage_concerns` (safe items, safe joiners) and stays live for `glassdoor_rating`, which
    # comes off the dossier and is not normalised item by item. Logged, because a silent drop is
    # invisible to the person reading the note.
    rating = (dossier.get("glassdoor") or {}).get("rating", "")
    flags = ", ".join(verdict["culture_flags"])
    # #329: the concerns are ALSO written as their own key, replaced on every verdict, so the CV
    # composer reads triage's latest judgement without parsing `relevance_notes`, which
    # accumulates dated prose from triage, dismiss and expire alike. `triage_`-prefixed on
    # purpose: the key is replaced on every verdict, so it must not be a name a person might
    # already use for their own top-level notes, and a bare `concerns` is exactly such a name. A
    # `concerns:` nested under another property is safe either way, since a write finds a key at
    # the frontmatter's base indent only (`core/vault.py::_key_lines`).
    concerns = "; ".join(verdict["concerns"])
    fields = {"status": status, "score": str(score)}
    for key, raw in (("glassdoor_rating", rating), ("culture_flags", flags),
                     ("triage_concerns", concerns)):
        safe = frontmatter_safe(str(raw)) if raw else ""
        if raw and not safe:
            _log.warning("triage: %s dropped for %s -- unsafe for frontmatter", key, note.slug)
            continue
        fields[key] = f'"{safe}"'
    # #378: the verdict's `Next:` question as its own key, replaced on every verdict, so the
    # worklist reads it without parsing `relevance_notes`. Blank when the model gave none, AND
    # blank when the text is unsafe for frontmatter: unlike the list keys above, keeping the
    # earlier value would leave an instruction the latest verdict no longer gives. The one
    # exception is a hand-typed multi-line value, which `_HAND_EDITABLE_KEYS` leaves alone.
    next_action = (verdict["recommended_next_action"] or "").strip()
    safe_next = frontmatter_safe(next_action) if next_action else ""
    if next_action and not safe_next:
        _log.warning("triage: triage_next blanked for %s -- unsafe for frontmatter", note.slug)
        safe_next = ""
    fields["triage_next"] = f'"{safe_next}"'
    # #378: a stale "asked" stamp. An awaiting research lead is never judged, so a judged
    # lead carrying one is a lead a human moved off `research`; left in place, a later
    # `research` verdict would hide it again with nobody having asked. Two rules:
    #   - a BLANK snapshot writes no key, so a stamp added while this lead was being judged
    #     (the realistic race) is never touched;
    #   - a STAMPED snapshot blanks it under `require_unchanged`, so a re-stamp on a different
    #     date during the judge refuses this whole write (`unchanged`) instead of erasing it.
    #     Residual, stated: a re-stamp on the SAME date as the stale stamp passes the guard.
    require_unchanged = {}
    stamp = note.fm.get("followup_asked", "") or ""
    if stamp.strip():
        fields["followup_asked"] = '""'
        require_unchanged["followup_asked"] = stamp
    # #302: the same rule for `triage_blocked`, MERGED into the one dict so neither entry
    # replaces the other. Only a real date stamp is cleared; a hand-typed value is left.
    blocked = blocked_stamp(note.fm)
    if blocked:
        fields["triage_blocked"] = '""'
        require_unchanged["triage_blocked"] = blocked
    today = date.today().isoformat()
    tag = f"[triage {today}]"
    parts = [verdict["fit_reasoning"]]
    if verdict["concerns"]:
        parts.append("Concerns: " + "; ".join(verdict["concerns"]))
    if verdict["recommended_next_action"]:
        parts.append("Next: " + verdict["recommended_next_action"])
    note_text = f"{tag} " + " ".join(p for p in parts if p)
    # require_status: same hardening as apply_classification above, closing the
    # identical pre-existing gap behind the (even longer) dossier-fetch-plus-judge
    # round trip.
    wrote = vault.update_fields(note.ref, fields, append_note=note_text.strip(), note_tag=tag,
                                require_status=_VERDICT_REQUIRE.get(
                                    status, frozenset(_status.TRIAGE_OWNED)),
                                require_unchanged=require_unchanged or None,
                                preserve_block_values=_HAND_EDITABLE_KEYS)
    # #118: symmetric with apply_classification above. Both writers carry the (#302)
    # stale-stamp `require_unchanged` refusing a `triage_blocked` re-stamp landed mid-judge;
    # only this one also carries the (#378) refusal of a `followup_asked` re-stamp. Each is a
    # refusal where the newer stamp stands and `unchanged` fits; neither is a VaultConflict.
    if wrote:
        return "applied"
    if status == "unjudgeable":
        return _stamp_blocked(vault, note, today)
    return "unchanged"
