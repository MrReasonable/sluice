"""Triage orchestrator: load -> classify -> resolve -> enrich -> judge -> apply -> audit.

Deterministic classify resolves the obvious cases for free (no dossier, no LLM). A lead
classify() leaves at blank/placeholder-company needs_review gets ONE resolution attempt: a free
regex-over-the-role-text tier 0 (#151), then a free URL-pattern tier 1 (#109), then --
opt-in via cfg.company_resolve_fetch -- a real, no-LLM page-visit tier 2 (#109), reusing
the same fetch/cache the enrich pass needs anyway, then -- opt-in via
cfg.company_resolve_llm, and only when a resolve_backend was threaded in -- tier 3 (#120),
an LLM read of the SAME page data tier 2 already fetched, on a SEPARATE backend from the
judge's (always the cheap "fallback" role, built in Sluice.triage()). Tier 0, like tier 1,
needs neither cfg.company_resolve_fetch nor an LLM, so both run unconditionally on a
zero-config `--no-llm` install.
Only the kept, ambiguous leads are enriched and judged. dry_run computes and reports but
writes nothing (no vault edits, no audit lines) -- resolution's COMPUTATION still runs
under dry_run, including a real tier-3 backend call, only its WRITE is skipped. no_llm
runs classify + (tier-0/tier-1-only) resolve + apply + audit only -- no backend of any kind is
ever built. Every lead already in the application lifecycle is skipped by the apply layer,
so triage never clobbers human state -- and a skipped lead is audited nowhere, because no
decision of ours landed on it.

A verdict is routed back to its note by the dossier's `lead_id`, which the enrich
pass sets to the store-issued `note.slug` -- NOT the cache's storage key, which is
a url hash two leads at one page deliberately share. Two kept leads at one slug are
refused outright and reported, on `index_by_slug`'s shared verdict; see there.
"""
from dataclasses import dataclass, field
from datetime import date

from sluice.core import status as _status
from sluice.core.leads import (
    NON_ANSWER_COMPANIES,
    ambiguous_slug_warnings,
    index_by_slug,
    is_placeholder_company,
)
from sluice.core.log import get_logger
from sluice.core.protocols import VaultConflict
from sluice.core.roletype import DECLARED, OBSERVED, normalise_role_type
from sluice.core.vault import frontmatter_safe
from sluice.triage import resolve, reverdict
from sluice.triage.apply import apply_classification, apply_verdict, clamp_verdict
from sluice.triage.audit import render_rejected_note
from sluice.triage.classify import classify, reverdict_notice
from sluice.triage.judge import judge
from sluice.triage.prompt import build_system_prompt_from

_log = get_logger("triage.engine")

# #120: after this many CONSECUTIVE tier-3 backend errors in one run, stop
# attempting tier 3 for the REST of this run. 107 candidate leads x
# resolve_backend's own timeout (DEFAULT_TIMEOUT=300s, core/backends.py) is up to
# ~9 hours if the backend is simply down -- this bounds that to
# _LLM_BREAKER_THRESHOLD failed attempts, reported ONCE, with every remaining
# candidate lead abstaining through resolve_company's OWN existing
# "resolve_backend is None" gate rather than a second gate here.
_LLM_BREAKER_THRESHOLD = 3

# How far back to look for an already-recorded role_type disagreement (#223 §2.5). Long,
# because the conflict persists until a human resolves it and re-recording it a year on
# would restart exactly the unbounded growth this bounds. `AuditLog.read_recent` treats an
# undated entry as in-range, so a hand-edited log stays covered.
_CONFLICT_LOOKBACK_DAYS = 3650


@dataclass
class TriageReport:
    counts: dict = field(default_factory=lambda: {
        "keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
        "needs_review": 0, "skipped": 0, "unjudgeable": 0})
    judged: int = 0
    backend: str | None = None
    failures: list = field(default_factory=list)
    # #120: which tier actually filled a blank/placeholder company, counted only where the
    # write LANDED (or would have, under dry_run) -- the same discipline `_audit`
    # already applies to a classify decision, and for the identical reason: a count
    # that includes a write the vault refused claims a resolution that never
    # actually happened. `llm_calls` counts every tier-3 ATTEMPT (hit, guard-
    # rejected, NONE, or a backend error) -- the abstain rate is what tells an
    # operator the tier's real cost per lead it actually recovers. Both are NEW
    # fields, not new rows inside `counts`: counts rows are lead OUTCOMES
    # (keep/shortlist/...) that cmd_triage_run prints and notify() sends to
    # Telegram verbatim -- mixing resolution PROVENANCE into that dict would make
    # its rows stop summing to the lead total a human reads in a phone notification.
    resolved: dict = field(default_factory=lambda: {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0})
    llm_calls: int = 0
    # #223 §2.4: how many leads had `role_type` written from the POSTING, split by what
    # the observation replaced. `filled` had nothing; `confirmed` already agreed and only
    # gains the stronger provenance; `corrected` had the tool's own guess; `conflicted`
    # had the user's own declaration. NEW fields rather than rows inside `counts` for the
    # reason stated above it: counts rows are lead OUTCOMES that sum to the lead total a
    # human reads in a phone notification.
    observed_role_types: dict = field(
        default_factory=lambda: {"filled": 0, "confirmed": 0, "corrected": 0,
                                 "conflicted": 0})
    # One message per `conflicted` lead, printed like `failures` -- §2.5's second
    # obligation, that a disagreement is surfaced rather than silently overridden.
    # Deliberately NOT in `failures`: nothing failed, and a user scanning a failure
    # count for something to fix would be misled about both.
    role_type_conflicts: list = field(default_factory=list)
    # #223 §2.1: leads whose pay verdict this release MOVES, on a vault written before
    # provenance existed. See `triage/reverdict.py`.
    reverdict_pending: list = field(default_factory=list)
    # ...and whether the run therefore STOPPED. Two bits, because `reverdict_pending` is
    # non-empty on both arms and cannot also say which one was taken: the run holds when
    # the acknowledgement landed, and PROCEEDS when it could not be recorded. Inferring
    # the arm from the list alone made `cmd_triage_run` print "WROTE NOTHING" over a run
    # that had just dismissed every lead it named, push the same claim to the
    # notification channel, and return before the summary and the failures line saying
    # so. Found by a reviewer and independently while reading the CLI back.
    reverdict_deferred: bool = False


def run(vault, cfg, backend, dossier_cache, audit, *,
        statuses=_status.DEFAULT_TRIAGE_STATUSES, limit=None, dry_run=False, no_llm=False,
        get_source=None, resolve_backend=None, reverdict_scope=""):
    """`reverdict_scope` identifies WHICH lead store #223's one-shot notice is about, so
    acknowledging on one vault cannot silence it for another.

    INJECTED rather than read off the store, and that is the point. An earlier version
    reached for `vault.dir`, which `Store` does not declare -- so an injected non-`Vault`
    store raised `AttributeError` on every triage run, through the very seam the engine
    exists on the other side of. `Sluice.triage` supplies it, because that is where the
    concrete `Vault` is constructed and its directory is a fact.

    The default `""` is a SHARED bucket: every caller that supplies no identity acknowledges
    on behalf of every other. That is fine for the suite, where conftest gives each test its
    own `XDG_STATE_HOME`, and it is why `Sluice.triage` -- the only production caller --
    computes a stable NON-EMPTY scope rather than passing a store attribute through. See
    `Sluice._reverdict_scope`; a caller driving two stores must supply distinct scopes or
    the first to acknowledge silences the notice for the second.
    """
    report = TriageReport()
    today = date.today().isoformat()
    notes = vault.read_leads(set(statuses))
    if limit:
        notes = notes[:limit]

    # #223 §2.1's delivery requirement, and it runs BEFORE anything else in this
    # function: the first run that would re-verdict a pre-#223 vault names the affected
    # leads and writes nothing at all. Returning here rather than skipping the affected
    # leads individually is deliberate -- the whole run is cheap to repeat (no judge call
    # is reached), and a partial run is harder for a user to reason about than one that
    # plainly did nothing.
    #
    # `dry_run` prints the notice and does NOT acknowledge it, because a dry run writes
    # nothing and that has to include this marker: a user who happens to preview first
    # must still get the notice on their real run.
    if not reverdict.acknowledged(reverdict_scope):
        # Over every status triage OWNS, and BEFORE `--limit` -- never over `notes`,
        # which both have already narrowed. The acknowledgement covers a whole VAULT, so
        # its scope has to be the vault rather than this run's selection. Measured on the
        # narrowed version, with two affected leads and `--limit 1`: run 1 named one lead
        # and spent the marker; run 2 named nothing and dismissed BOTH. The lead it never
        # named went to `dismiss`, which `DEFAULT_TRIAGE_STATUSES` does not re-select, so
        # nothing would ever surface it again.
        #
        # COST, stated correctly after a first version of this comment got it wrong: an
        # AFFECTED vault pays one extra read, once, because the marker is spent and
        # `acknowledged()` then short-circuits above. An UNAFFECTED one pays it on every
        # run, forever -- the marker is deliberately only spent when a notice is actually
        # shown (see `reverdict.py`), so there is nothing to short-circuit on. That is
        # the price of not silently re-verdicting a vault the user syncs in later.
        affected = vault.read_leads(set(_status.TRIAGE_OWNED))
        report.reverdict_pending = [
            f"{note.slug}: {said}" for note in affected
            if (said := reverdict_notice(note.fm, cfg))]
        if report.reverdict_pending:
            if dry_run:
                report.reverdict_deferred = True
                return report          # writes nothing, the marker included
            if reverdict.acknowledge(reverdict_scope):
                report.reverdict_deferred = True
                return report          # recorded, so the next run applies it
            # The marker did NOT land (a read-only state dir, a full disk). Returning
            # early anyway would repeat this forever and triage would never triage
            # again -- worse than the harm, and it exits 0 looking like an idle run. So
            # the run PROCEEDS, having printed the notice: the half of §2.1 that
            # actually protects the user is that they SEE the affected leads, and they
            # do. The half being given up is "and nothing is written yet".
            report.failures.append(
                "role-type re-verdict: the notice above could not be recorded, so this "
                "run is APPLYING it rather than repeating the notice forever -- fix the "
                "state directory if you wanted to review the listed leads first")

    keeps = []          # notes that pass the pre-gate, headed for enrich + judge
    audit_entries = []
    # #120: tier 3's own audit trail, kept OUT of audit_entries so a run that only
    # resolved companies (rejected nothing) does not start re-rendering "Rejected
    # Leads Audit.md" on a path that previously never touched it -- see the render
    # trigger at the bottom of this function, which checks audit_entries only.
    # Deliberately write-only/unread by this function -- _resolve_audit appends to
    # it and to the durable `audit` log, but nothing here ever reads it back. Do
    # not "simplify" this by merging it into audit_entries: that merge is exactly
    # what test_a_tier3_resolution_never_triggers_the_rejected_leads_note pins
    # against, since it would make render_rejected_note fire on a resolve-only run.
    resolve_audit_entries = []

    def _audit(entry):
        audit_entries.append(entry)
        if not dry_run:
            audit.append(entry)

    def _resolve_audit(entry):
        resolve_audit_entries.append(entry)
        if not dry_run:
            audit.append(entry)

    # The identity of a role-type disagreement: which lead, what the posting read as, and
    # what the note holds. `ts` is deliberately OUT -- including it would make every
    # entry unique by construction and the deduplication inert, which is the shape this
    # repo keeps finding in its own guards.
    #
    # Keyed on `lead_ref`, NOT on `slug`. A slug is filename-derived and the scan walks
    # subdirectories (#1), so two notes in different folders can carry the same one --
    # which is why this very function runs `index_by_slug` and refuses ambiguous pairs
    # outright. Within one run that refusal keeps colliding notes away from here, but the
    # de-duplication reads the DURABLE log across runs: a note that conflicted last month
    # and has since been merged away can suppress a different note that inherits its slug
    # today. `ref` is the store's own opaque handle and is unique by construction; it is
    # COMPARED here, never parsed, which is what its contract permits. `slug` stays in the
    # entry because it is the name a human recognises.
    _CONFLICT_IDENTITY = ("lead_ref", "observed_role_type", "role_type",
                          "role_type_source")

    def _role_type_conflict_is_new(entry) -> bool:
        """Has this exact disagreement already been recorded for this lead?

        Reads the durable log rather than only this run's entries: the repeat that
        matters is across RUNS, and a nightly cron is where it accumulates. Read
        failures answer True -- writing a duplicate row is a cosmetic cost, while
        swallowing the first record of a real conflict is the loud one.
        """
        wanted = tuple(entry[k] for k in _CONFLICT_IDENTITY)
        try:
            seen = audit.read_recent(_CONFLICT_LOOKBACK_DAYS)
        except Exception as e:                       # pragma: no cover - defensive
            _log.warning("triage: could not read the audit log to de-duplicate a "
                         "role_type conflict (%s); recording it again", e)
            return True
        return not any(
            e.get("stage") == "role_type"
            and tuple(e.get(k) for k in _CONFLICT_IDENTITY) == wanted
            for e in seen)

    def _report_role_type_conflict(note, observed, previous, previous_source) -> None:
        """Say that the posting disagrees with what the user declared, and change nothing.

        Two surfaces, because they answer different questions. The run summary is what a
        human sees now; the durable audit log is what survives the scrollback and lets
        them find the lead later. `stage` keeps the entry out of the rendered Rejected
        Leads note (`_is_reject`), the same way `_resolve_audit`'s own entries stay out --
        a run that only noticed a disagreement rejected nothing.
        """
        report.role_type_conflicts.append(
            f"role-type {note.ref}: the posting reads as {observed!r}, but this lead "
            f"carries {previous!r} as {previous_source!r} -- KEPT, because it is yours. "
            "Your search's premise may not hold for this posting")
        # The RUN-SUMMARY line above repeats every run, and should: the disagreement is
        # still live, and a user who has not acted on it should keep seeing it. The
        # DURABLE entry must not. Nothing here changes the note, so the conflict
        # recomputes identically on the next run -- on a nightly cron that is one audit
        # row per conflicted lead per day, for ever, and the user cannot clear it except
        # by editing the note. An unbounded run of identical rows makes the one that
        # matters harder to find, which is the opposite of what the log is for.
        entry = {"ts": today, "slug": note.slug, "lead_ref": str(note.ref),
                 "stage": "role_type",
                 "observed_role_type": observed, "role_type": previous,
                 "role_type_source": previous_source,
                 "role": note.fm.get("role", ""),
                 "url": note.fm.get("url", ""),
                 "reason": "the posting contradicts the declared role type; "
                           "the declaration was kept"}
        if _role_type_conflict_is_new(entry):
            _resolve_audit(entry)

    def _observe_role_type(note, dossier) -> None:
        """#223 §2.4/§2.5: write what the POSTING said about pay basis onto the note.

        The ladder is `observed > declared > assumed > ""`, so an observation always
        wins on the value: the posting is ground truth about what the job IS, while a
        `declared` value is the user's assertion about a SEARCH, and #223's whole premise
        is that a search label is not a fact about a posting.

        **Abstention is not an observation of "".** A JD that says nothing about basis is
        not evidence the user's declaration is wrong, so a blank observation writes
        nothing at all rather than blanking what is there.

        **Whether a human is TOLD depends on what was overwritten**, which is the only
        part §2.5 leaves to this PR. Overwriting an `assumed` value is the feature
        working -- it is the tool correcting its own guess about which search ran, and on
        the population #223 describes that is most leads, so a line each would bury the
        run summary. Overwriting a `declared` one means the user's search premise was
        wrong about this posting, and they have no other route to learn it. Counted
        either way; announced only for the second.

        **The write is best-effort and unreported, unlike the company-resolve site above,
        and the asymmetry is deliberate.** `update_fields` returns False for a refusal and
        for a no-op alike, so a caller cannot tell them apart. There, the write is the
        entire point of a tier-2 page load or a tier-3 LLM call, so a non-landing is worth
        a line. Here the observation is a free byproduct of a dossier this run was
        building anyway, and "the note already says this" is the common case -- reporting
        every non-landing would be almost all noise. Retried next run either way.

        Runs AFTER the classify pass -- named rather than cited by line, because the
        `:118` this said was already pointing at a different statement two commits later
        (#191 is the standing issue about line citations as a drift surface). So an
        observation cannot reach the gate on the run that fetches it; §9 records that as
        an accepted residual, and §2.3's marker-first ordering is what makes it tolerable.
        """
        observed = normalise_role_type(dossier.get("role_type_observed"))
        if not observed:
            return
        previous = normalise_role_type(note.fm.get("role_type"))
        previous_source = note.fm.get("role_type_source") or ""
        if observed == previous and previous_source == OBSERVED:
            return
        # Gated on the status BEFORE either arm. The write has always been guarded by
        # `require_status`; the conflict REPORT needs the same gate for the same reason,
        # or triage starts commenting on leads it has no business touching -- a lead in
        # the application lifecycle is one it may not write to and should not narrate.
        if note.status not in _status.TRIAGE_OWNED:
            return
        if previous and observed != previous and previous_source == DECLARED:
            # THE USER'S ASSERTION SURVIVES. §2.5 as designed put `observed` above
            # `declared` outright, on the reasoning that the posting is ground truth
            # about what the job IS. Three rounds of review then measured the observer:
            # 37% recall, and a residual false-positive rate on adverts whose SUBJECT is
            # contracting (recruitment, payroll, bid work). Overriding a declaration on
            # that basis writes a lexical guess over something the user actually typed --
            # permanently, and stickily, since a later run would see the value agreeing
            # with itself and skip.
            #
            # So the disagreement is reported and the note is left alone. The owner took
            # this decision against the measurement on 2026-09-01; the design doc's §2.5
            # and §9 record the change and why.
            report.observed_role_types["conflicted"] = (
                report.observed_role_types.get("conflicted", 0) + 1)
            _report_role_type_conflict(note, observed, previous, previous_source)
            return
        if not previous:
            bucket = "filled"
        elif observed == previous:
            # The posting AGREES; only the provenance strengthens. Its own bucket rather
            # than being folded into one of the other three: an earlier version filed it
            # under `filled`, which the field's own comment defines as "had nothing", and
            # carried a dead `else "corrected"` arm that the guard two lines up -- the
            # exact negation of its condition -- made unreachable for every input.
            bucket = "confirmed"
        else:
            # Differs, and the previous value was NOT the user's own -- a blank, the
            # tool's `assumed` guess, or an earlier observation this one supersedes.
            # Correcting any of those is the feature working.
            bucket = "corrected"
        # Under `dry_run` nothing is written, so this stands in for the write's own
        # `require_status` guard. Without it a dry run REPORTED a conflict the real run
        # refuses: measured on `applied`/`interview`/`rejected`/`accepted`, the real run
        # counted 0 and the dry run counted 1 and printed a conflict line.
        #
        # It reads `note.status` (`_fm_dict`, LAST duplicate key wins) while
        # `update_fields` re-reads via `_fm_value` (FIRST wins), so on a hand-edited note
        # carrying two `status:` keys the two can disagree in both directions. That is
        # the divergence `update_fields`' own docstring already documents and reasons
        # about: a caller's pre-check via `note.fm` running first is exactly the shape it
        # says makes the disagreement unreachable as a silent overwrite. Named here
        # rather than closed with a second guard -- the residual is a wrong dry-run
        # REPORT on a malformed note, and the vault never writes one.
        wrote = False
        if dry_run:
            wrote = note.status in _status.TRIAGE_OWNED
        else:
            # `frontmatter_safe` even though both values come from closed sets -- the
            # sweep in tests/test_frontmatter_write_sweep.py should keep guarding this
            # line rather than carrying a `_GUARDED_UPSTREAM` decision that goes stale
            # silently if either producer is ever widened.
            safe_role_type = frontmatter_safe(observed) or ""
            safe_source = frontmatter_safe(OBSERVED) or ""
            try:
                wrote = vault.update_fields(
                    note.ref,
                    {"role_type": f'"{safe_role_type}"',
                     "role_type_source": f'"{safe_source}"'},
                    require_status=frozenset(_status.TRIAGE_OWNED),
                    # Never-clobber, decided against the FRESH note rather than the
                    # snapshot this function is holding. A dossier fetch spends seconds
                    # between `read_leads` and here, and a human typing their own
                    # role_type into Obsidian in that window must win -- measured before
                    # this guard: they were overwritten, and because the stale snapshot
                    # read blank the write took the `filled` path, so the rule that a
                    # `declared` value is never overwritten did not even engage.
                    #
                    # RAW values, not the folded `previous`: `require_unchanged` compares
                    # against `_fm_value`, so a normalised expectation would never match
                    # a stored `Contract` and the write would silently never land.
                    require_unchanged={"role_type": note.fm.get("role_type") or "",
                                       "role_type_source": previous_source})
            except VaultConflict as e:
                # Isolated, exactly as the two sibling write sites are: a human editing
                # in Obsidian won the race, and one lead's lost observation must not
                # abort a whole triage run over a field the next run recomputes.
                report.failures.append(f"role-type {note.ref}: {e}")
                return
        if not wrote:
            return
        note.fm["role_type"] = observed
        note.fm["role_type_source"] = OBSERVED
        report.observed_role_types[bucket] = report.observed_role_types.get(bucket, 0) + 1

    _llm_consecutive_errors = 0
    _llm_breaker_tripped = False

    # ── classify pass (free unless resolution's tier 2 visits a page, or tier 3 spends a call) ──
    for note in notes:
        company = (note.fm.get("company") or "").strip()
        decision, reason = classify(note.fm, cfg)
        # #109/#120: resolution attempted only for classify()'s OWN blank/placeholder-company
        # needs_review branch, never ahead of its existing title/location/pay
        # rejects (which don't depend on company at all) -- so a lead classify
        # would reject regardless never triggers a tier-2 page visit or a tier-3
        # LLM call. Also gated on the lead's CURRENT status being one triage owns:
        # the write below is already correctly guarded by
        # `require_status=frozenset(_status.TRIAGE_OWNED)`, so a lead read in under
        # an explicit `--status <other>` (e.g. a deliberate `--status needs_review`
        # backlog sweep that also happens to cover an application-owned status, or
        # any status outside TRIAGE_OWNED) could otherwise trigger a real page fetch
        # and/or a real LLM call for a write that could never actually land anyway.
        # A cost gap, not a safety gap -- the write guard already protects the
        # vault -- but there is no reason to pay for a fetch/call whose result is
        # guaranteed to be discarded.
        if (decision == "needs_review" and is_placeholder_company(company)
                and note.status in _status.TRIAGE_OWNED):
            res = resolve.resolve_company(
                note.fm, get_source, dossier_cache, no_llm=no_llm,
                company_resolve_fetch=cfg.company_resolve_fetch,
                company_resolve_llm=cfg.company_resolve_llm,
                resolve_backend=None if _llm_breaker_tripped else resolve_backend)
            if res.llm_called:
                report.llm_calls += 1        # the spend happened whatever the outcome
                if res.llm_error:
                    _llm_consecutive_errors += 1
                    if (not _llm_breaker_tripped
                            and _llm_consecutive_errors >= _LLM_BREAKER_THRESHOLD):
                        _llm_breaker_tripped = True
                        report.failures.append(
                            f"company-resolve tier3: {_LLM_BREAKER_THRESHOLD} "
                            "consecutive backend errors -- tier 3 disabled for the "
                            "rest of this run")
                else:
                    _llm_consecutive_errors = 0
            resolved = res.company
            if resolved:
                wrote = False
                if not dry_run:
                    try:
                        # require_blank, alongside require_status: this decision ("company
                        # is blank/placeholder, so filling it in is safe") was made from
                        # the read_leads snapshot, and tier 2/3 spend SECONDS on a real
                        # page load or an LLM round trip before getting here. A human
                        # typing the company into Obsidian in that window must win --
                        # never-clobber -- so the blankness check has to be a FRESH
                        # re-read inside the CAS transform, exactly like require_status
                        # beside it. A caller-side check on `company` above is stale by
                        # construction and would be an equivalent mutant.
                        #
                        # blank_values=NON_ANSWER_COMPANIES widens what counts as blank
                        # for THIS guard to the same placeholder set the gate above
                        # already recognises -- a note that already reads "Unknown" is
                        # exactly the shape this resolution pass exists to repair, and
                        # without this the write would refuse on presence forever, since
                        # require_blank has no other route to accept a non-empty value.
                        # A human's own REAL company typed in the race window is still
                        # refused: it folds to something outside NON_ANSWER_COMPANIES,
                        # so the guard's never-clobber promise is unchanged for it.
                        wrote = vault.update_fields(
                            note.ref, {"company": f'"{resolved}"'},
                            require_status=frozenset(_status.TRIAGE_OWNED),
                            require_blank=frozenset({"company"}),
                            blank_values=NON_ANSWER_COMPANIES)
                    except VaultConflict as e:
                        report.failures.append(f"company-resolve {note.ref}: {e}")
                    else:
                        if not wrote:
                            report.failures.append(
                                f"company-resolve {note.ref}: company write did not land "
                                "(status changed, company was already set to a real "
                                "name, or the status is not one triage owns)")
                if wrote or dry_run:
                    note.fm["company"] = resolved
                    report.resolved[res.tier] = report.resolved.get(res.tier, 0) + 1
                    _resolve_audit({"ts": today, "slug": note.slug, "company": resolved,
                                    "role": note.fm.get("role", ""),
                                    "url": note.fm.get("url", ""), "stage": "resolve",
                                    "tier": res.tier,
                                    "reason": "blank/placeholder company resolved from the posting"})
                    decision, reason = classify(note.fm, cfg)
        if decision == "keep":
            report.counts["keep"] += 1
            keeps.append(note)
            continue
        if dry_run:
            outcome = "skipped"
        else:
            try:
                outcome = apply_classification(vault, note, decision, reason)
            except VaultConflict as e:
                # #16: a concurrent edit won the write race; leave the lead as-is,
                # retried next run. except VaultConflict (not broad Exception) so a
                # real apply-layer logic bug is not silently counted as a transient
                # conflict. continue skips the counting/audit below for this lead.
                report.failures.append(f"apply {note.ref}: {e}")
                continue
        # #109 round 3 (arch3-001/inv3-001) established `unchanged` (named
        # `skipped-race` before #118) as its own outcome distinct from `skipped`:
        # apply_classification's require_status guard stopping the vault write closes
        # a gap, a PERSISTED audit-log entry claiming a decision that never actually
        # applied, which render_rejected_note would otherwise render into a
        # human-facing summary as if it had. #118: it is NEVER actually a race --
        # apply_classification's own docstring/comment now says so, and a real content
        # collision raises VaultConflict instead (caught above, a separate, already
        # correctly `report.failures`-reported path) -- so it does NOT belong in
        # report.failures. It is grouped with `skipped` below purely for counting/audit
        # purposes, which is unrelated to whether it is a failure.
        key = "skipped" if outcome in ("skipped", "unchanged") else (
            "dismiss" if decision == "reject" else "needs_review")
        report.counts[key] = report.counts.get(key, 0) + 1
        # BOTH skip outcomes, grouped exactly as `key` above groups them, because they
        # have the identical shape: a decision was computed and NO write happened.
        # `unchanged` is #109's own (the fresh-status re-read refused, or the value was
        # already current); plain `skipped` is the pre-existing one (apply.py's
        # _guarded() refused, because the lead has already left TRIAGE_OWNED -- it is
        # `applied`, `offer`, ...). The argument that excludes the first excludes the
        # second unchanged: a persisted audit line claiming a decision that never
        # applied, which render_rejected_note would put in front of a human as if it
        # had. Both are still COUNTED (`key` above): a skip is reported, just not
        # audited as a decision.
        #
        # dry_run forces `skipped` at both sites too, so under dry_run _audit is now
        # never called at all. `_audit`'s own `if not dry_run` and the `not dry_run`
        # on the render gate below are kept regardless: neither site's correctness
        # should depend on a fact established 100 lines away, and a future outcome
        # value that reaches _audit under dry_run must still write nothing.
        if outcome not in ("skipped", "unchanged"):
            _audit({"ts": today, "slug": note.slug,
                    "company": note.fm.get("company", ""), "role": note.fm.get("role", ""),
                    "url": note.fm.get("url", ""), "stage": "classify",
                    "decision": decision, "reason": reason, "score": 0})

    # ── enrich + judge (kept, ambiguous) ──
    if keeps and not no_llm:
        dossiers = []
        note_by_id = {}
        # The judge round trip is keyed on the dossier's `lead_id` -- it is what the prompt
        # labels each dossier with and what every verdict comes back wearing -- so that field
        # has to be UNIQUE PER NOTE. `DossierCache.cache_key`, which get_or_build stamps into
        # it, is not: it hashes the url so that two leads at one page share ONE cache entry,
        # which is exactly the double-fetch saving it exists for and is right for STORAGE.
        # Two not-yet-deduped leads at one url (a re-scrape, a cross-post) therefore reached
        # the judge under one id, came back as two verdicts wearing it, and both dicts here
        # resolved every one of them to whichever note was inserted LAST: one lead silently
        # took the other's verdict, the other took none, and nothing raised. So `lead_id` is
        # overridden to `note.slug` below -- the store-issued per-note identity (see
        # core/protocols.py:LeadNote) -- while cache_key keeps its own job unchanged.
        #
        # That identity's uniqueness is BOUNDED rather than absolute: the vault's slug is the
        # note filename, and a recursive scan (#1) lets a human seat one filename in two
        # directories. index_by_slug is the one sanctioned way to key on it -- track, cv,
        # `apply`'s batch path and `leads expire` all take the same verdict -- and it DROPS
        # both twins rather than picking one, which is the same never-silently-misroute rule
        # this whole block is about. Reported as well as skipped: a lead that vanishes from
        # the run with nothing said is the mirror failure.
        #
        # Over `keeps`, not `notes`: the set at risk is the one presented to the judge in one
        # batch. A twin the classify pass rejected never reaches here, and its own write went
        # through `note.ref`, which is unique whatever the slug does -- so refusing the KEPT
        # twin on its account would drop a lead nothing could have misrouted.
        _, ambiguous = index_by_slug(keeps)
        for msg in ambiguous_slug_warnings("triage: kept lead", ambiguous):
            _log.warning("%s", msg)
            report.failures.append(msg)
        for note in keeps:
            if note.slug in ambiguous:
                # BEFORE get_or_build, on the same reasoning apply/select.py and cv/engine.py
                # give for placing it before their own eligibility checks: what is wrong is
                # the IDENTITY, which no later check inspects -- and a twin must not even be
                # FETCHED for a judgment that could not be routed back to it.
                continue
            try:
                d = dossier_cache.get_or_build(note.fm)
            except Exception as e:
                report.failures.append(f"dossier {note.ref}: {e}")
                continue
            # The JD never arrived (#169). Spending a judge call here buys a verdict on
            # page chrome -- and because "unjudgeable" used to collapse into `research`,
            # the nightly run over `_status.DEFAULT_TRIAGE_STATUSES` re-selected the lead
            # and paid for the same non-answer every night until the cache entry expired.
            # Nothing was cached this run (see DossierCache.get_or_build), so the next
            # run refetches; marking the lead `unjudgeable` is what separates "the
            # pipeline should retry this" from "a human should investigate this", which
            # is what `research` means.
            #
            # `continue` BEFORE dossiers.append(d) below is the whole saving: the lead
            # never enters the batch handed to the judge, so it costs no judge call --
            # placing this check after the append, or filtering the batch later, would
            # still pay for the call this exists to avoid.
            if not dossier_cache.jd_arrived(d):
                reason = (f"no job description was fetched (floor: "
                         f"{dossier_cache.min_jd_chars} chars)")
                if dry_run:
                    outcome = "skipped"
                else:
                    try:
                        outcome = apply_classification(vault, note, "unjudgeable", reason)
                    except VaultConflict as e:
                        # Symmetric with the classify-pass site earlier in this function
                        # (#16): a concurrent edit -- a human in Obsidian, another process
                        # -- won the write race. apply_classification can raise this (the
                        # classify pass already proves it), and leaving it uncaught HERE
                        # would abort the WHOLE triage run over one lead that is simply
                        # retried next run regardless. `continue` skips the counting
                        # below for this lead, matching the classify-pass site exactly.
                        report.failures.append(f"apply {note.ref}: {e}")
                        continue
                # Counted off the WRITE OUTCOME, not unconditionally -- mirroring the
                # classify-pass convention: a count that includes a write the vault
                # refused claims a status change that never happened. Worse here than
                # the generic case: a `_guarded` refusal means this lead is `applied`
                # or later, so counting it `unjudgeable` unconditionally would report
                # the OPPOSITE of that lead's real state. `dry_run` forces `skipped`
                # above for the identical reason it does at the classify-pass site: a
                # dry run reports what WOULD happen, never a write that did not occur.
                # `unjudgeable` is the only non-skip outcome this branch can produce
                # (unlike the classify pass, which routes to `dismiss`/`needs_review`
                # depending on `decision`), so the key has no third case to name.
                key = "skipped" if outcome in ("skipped", "unchanged") else "unjudgeable"
                report.counts[key] = report.counts.get(key, 0) + 1
                continue
            _observe_role_type(note, d)
            # #109: get_or_build SNAPSHOTS these four off the lead at BUILD time, and the
            # classify pass above resolves a blank/placeholder company into note.fm AFTER that --
            # while the url-hash cache_key makes both passes land on the SAME entry,
            # which is exactly the double-fetch saving it was added for. So the cheaper
            # fetch and a stale judge input are the same fact, and the entry keeps
            # serving that stale blank for the whole ttl (7 days by default), on
            # precisely the leads this feature exists to give a company to.
            #
            # All four, not just `company`, because they share one cause and the same
            # key change widened it: keying on company/role meant a hand edit to either
            # MINTED a new key and re-fetched, and keying on the url means the old entry
            # is reused with its old snapshot instead. The note the engine is holding is
            # the source of truth for every one of them; only the FETCHED half of the
            # dossier (jd, glassdoor) is the cache's to answer for.
            #
            # Re-derived here, where the dossier is handed to the judge, rather than
            # written back: the cached JSON stays a faithful record of what was fetched.
            # Unconditional, same as `lead_id` below: by this point every one of these
            # four dossier fields IS the note's own value, re-derived -- never a fetched
            # fact (the only genuinely fetched dossier fields are
            # jd/glassdoor/page_title/structured_data) -- so a blank note value has
            # nothing correct to fall back to. An `or` here would silently backfill a
            # human's deliberate blank (e.g. clearing a wrong location for someone to
            # refill) from a stale cached copy of what the field used to say -- the same
            # staleness bug fixed for `lead_id` below, just reached from the correction
            # path a human would actually take (#113).
            #
            # `lead_id` rides the same override for the same reason one step further out:
            # the cache stamped its STORAGE key there, and identity is not storage. The
            # store contract guarantees a non-empty slug for every note it returns, so
            # there is nothing to fall back to and nothing to fall back FOR. Every
            # consumer downstream of this line -- the judge prompt, note_by_id, by_id --
            # therefore sees the slug and not the url hash.
            d = {**d,
                 "lead_id": note.slug,
                 "company": note.fm.get("company", ""),
                 "position": note.fm.get("role", ""),
                 "location": note.fm.get("location", ""),
                 "role_type": note.fm.get("role_type", "")}
            dossiers.append(d)
            # Read back off `d`, never written as `note_by_id[note.slug]`, so this map and
            # the id the judge is actually shown cannot drift apart: whatever ends up in the
            # dossier's `lead_id` field one line above is what a verdict comes back with, and
            # what this has to be keyed on. (It also keeps the shape out of
            # tests/test_slug_indexing_discipline.py's sweep for a `.slug`-keyed subscript
            # assignment -- which would be a false positive here, index_by_slug having
            # already refused every twin, but is a warning worth heeding rather than
            # exempting: that sweep only matches a PLAIN `.slug`, so this line was invisible
            # to it either way and the collision it now cannot have went unseen for exactly
            # that reason.)
            note_by_id[d["lead_id"]] = note
        # Compose the judge prompt from the candidate's vault-sourced criteria
        # (their editable source of truth), falling back to the baked-in default
        # if it is missing.
        system_prompt = build_system_prompt_from(vault.read_criteria())
        verdicts = judge(dossiers, backend, batch_size=cfg.batch_size,
                         system_prompt=system_prompt)
        report.judged = len(verdicts)
        report.backend = getattr(backend, "last_backend", None)
        # A bare comprehension, and safe as one only because of the two facts above: every
        # `lead_id` in `dossiers` is a `note.slug`, and no two notes reaching this line share
        # one -- `keeps` holds each note once (read_leads yields one LeadNote per path) and
        # index_by_slug removed every slug two of them claimed. Take either away and this
        # silently keeps the last twin again, which is the whole defect.
        by_id = {d["lead_id"]: d for d in dossiers}
        for verdict in verdicts:
            note = note_by_id.get(verdict.get("lead_id"))
            if note is None:
                # `lead_id` is now the note's slug -- prose, not an opaque hash -- so a
                # model that paraphrases it (collapses whitespace, swaps a dash) produces
                # a verdict this run can never match back to a note. Silently continuing
                # here would make that read as a healthy no-op (judged=N, failures=0,
                # exit 0) instead of the lost verdict it is.
                report.failures.append(
                    f"judge {verdict.get('lead_id')!r}: no note matches this lead_id "
                    "(the model likely paraphrased the echoed slug)")
                continue
            dossier = by_id.get(verdict["lead_id"], {})
            if dry_run:
                outcome = "skipped"
            else:
                try:
                    outcome = apply_verdict(vault, note, verdict, dossier)
                except VaultConflict as e:
                    # Symmetric with the classify-pass site above.
                    report.failures.append(f"apply {note.ref}: {e}")
                    continue
            # Symmetric with the classify-pass site above, including #118: `unchanged`
            # is grouped with `skipped` for counting/audit purposes only -- it is never
            # reported in report.failures, since it is reachable only via a benign
            # require_status no-op, never a real content collision.
            #
            # #169: clamp_verdict, not the raw model string or a bare _status.normalize.
            # apply_verdict() above already clamps what it WRITES, but counts/audit are
            # computed here, outside it, off the same raw `verdict` dict -- a clamp that
            # fixed only the write would report a verdict that never landed (the
            # #109/#118 bug class this repo has already fixed twice). One shared helper
            # in triage/apply.py, not a second copy of the rule here.
            key = "skipped" if outcome in ("skipped", "unchanged") else clamp_verdict(
                verdict.get("verdict", ""))
            report.counts[key] = report.counts.get(key, 0) + 1
            if outcome not in ("skipped", "unchanged"):
                _audit({"ts": today, "slug": verdict["lead_id"],
                        "company": note.fm.get("company", ""),
                        "role": note.fm.get("role", ""), "url": note.fm.get("url", ""),
                        "stage": "judge", "verdict": clamp_verdict(verdict.get("verdict", "")),
                        "reason": verdict.get("fit_reasoning", ""),
                        "score": verdict.get("relevance_score", 0)})

    # ── rendered audit note ──
    if not dry_run and audit_entries:
        render_rejected_note(vault, audit.read_recent(30), cfg.rejected_note)
    return report
