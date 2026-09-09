"""Write triage outcomes back to the vault, format-preserving and never clobbering
a lead that has already entered the application lifecycle (applied, phone_screen,
...). Reject maps to the canonical `dismiss`; a plain-language reason and the
judge's reasoning are appended (once) to relevance_notes."""
from datetime import date

from sluice.core import status as _status
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
# The permitted set is WIDER than `_DECISION_REQUIRE["unjudgeable"]` by exactly one member,
# `research`, and the difference is the whole point of #300 rather than an oversight:
#
#   - The classification arm fires when `jd_arrived` is False -- nothing was read, so the
#     write records pure ABSENCE and may only land where no verdict exists (`new`).
#   - This arm fires when the model DID read the fetched page and identified it as page
#     chrome. That is a positive finding about the evidence, and `research` reached by a
#     conservative score on that same unreadable page is not a human conclusion worth
#     protecting -- it is the artifact this issue exists to clear.
#
# `shortlist`, `dismiss` and `needs_review` stay excluded. Each records a decision made on
# evidence, and a transient block must not erase one: the measured #169 incident demoted a
# SHORTLISTED lead carrying a composed CV pointer, which then pointed at nothing. Losing a
# genuine `research` lead to a transient block is the one accepted cost here, and it is
# self-healing -- `unjudgeable` is re-selected nightly, so the next successful fetch
# re-judges it straight back.
#
# Its members coincide with `_status.DEFAULT_TRIAGE_STATUSES` TODAY and it is still written
# out longhand, deliberately: that tuple is the SELECTION default (which leads a run READS)
# and this is a WRITE guard. Deriving one from the other is the same identity/cache-key
# conflation #109 made once already, and would silently widen this guard the next time the
# selection set grows.
_VERDICT_REQUIRE = {"unjudgeable": frozenset({"new", "research", "unjudgeable"})}


def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    tag = f"[triage {date.today().isoformat()}]"
    # require_status (#109 inv2-001): the pre-existing _guarded() check above reads
    # note.status, a plain dataclass field frozen at read_leads() time -- byte-identical
    # to no guard at all against a real vault. This re-reads the FRESH status inside the
    # CAS transform, closing the window a #109 tier-2 fetch (real page load, seconds) now
    # opens ahead of this write.
    wrote = vault.update_fields(
        note.ref, {"status": new_status},
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
        require_status=_DECISION_REQUIRE.get(decision, frozenset(_status.TRIAGE_OWNED)))
    # #118: `wrote=False` here is always a genuine no-op, never a race -- either
    # require_status refused on a fresh re-read (the lead already left TRIAGE_OWNED,
    # someone got there first) or the write was a byte-identical rewrite (the value
    # was already current, e.g. a same-day re-triage). A REAL content collision raises
    # VaultConflict instead, caught separately one level up in triage/engine.py.
    # "unchanged" either way, not a failure.
    return "applied" if wrote else "unchanged"


def apply_verdict(vault, note, verdict, dossier) -> str:
    if _guarded(note):
        return "skipped"
    status = clamp_verdict(verdict.get("verdict", ""))
    score = int(verdict.get("relevance_score", 0) or 0)
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
    # Abstain on the FIELD, never the write: losing a triage verdict because a culture flag
    # contained a quote would be the worse failure. Logged, because a silent drop is invisible
    # to the person reading the note.
    rating = (dossier.get("glassdoor") or {}).get("rating", "")
    flags = ", ".join(verdict.get("culture_flags") or [])
    fields = {"status": status, "score": str(score)}
    for key, raw in (("glassdoor_rating", rating), ("culture_flags", flags)):
        safe = frontmatter_safe(str(raw)) if raw else ""
        if raw and not safe:
            _log.warning("triage: %s dropped for %s -- unsafe for frontmatter", key, note.slug)
            continue
        fields[key] = f'"{safe}"'
    tag = f"[triage {date.today().isoformat()}]"
    parts = [verdict.get("fit_reasoning", "")]
    if verdict.get("concerns"):
        parts.append("Concerns: " + "; ".join(verdict["concerns"]))
    if verdict.get("recommended_next_action"):
        parts.append("Next: " + verdict["recommended_next_action"])
    note_text = f"{tag} " + " ".join(p for p in parts if p)
    # require_status: same hardening as apply_classification above, closing the
    # identical pre-existing gap behind the (even longer) dossier-fetch-plus-judge
    # round trip.
    wrote = vault.update_fields(note.ref, fields, append_note=note_text.strip(), note_tag=tag,
                                require_status=_VERDICT_REQUIRE.get(
                                    status, frozenset(_status.TRIAGE_OWNED)))
    return "applied" if wrote else "unchanged"  # #118: symmetric with apply_classification above
