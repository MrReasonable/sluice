# sluice/cv/engine.py
"""CV tailoring orchestrator: select -> bundle -> compose -> gate -> render -> serve
-> record -> notify. Composition is a bounded backend call over the closed verified
bundle; a HARD-gate failure triggers exactly one retry with the violations fed back,
then the lead is skipped (never rendered ungated). dry_run computes and reports but
writes nothing."""
import json
import re
from dataclasses import dataclass, field
from datetime import date

from sluice.core import status as _status
from sluice.core.leads import StalenessPolicy, ambiguous_slug_warnings, index_by_slug
from sluice.core.log import get_logger
from sluice.cv import bundle as _bundle
from sluice.cv import compose as _compose
from sluice.cv.audit import run_audit, unsupported_claims
from sluice.cv.slop import check_text as _slop
from sluice.cv.validate import validate as _validate

_log = get_logger("cv.engine")


@dataclass
class CvResult:
    """status is one of: rendered, skipped-gate, skipped-selection, skipped-has-cv,
    skipped-stale (#9: last_seen older than lead_ttl_days, refused before any dossier
    fetch or compose -- see run_one),
    skipped-ambiguous (#1: the lead did not resolve to exactly ONE note, so nothing was
    composed for it. TWO producers, neither of them run_one -- which is handed one note and
    has no list to find a twin in: run_batch emits it for each of two shortlist notes
    claiming one slug, and `Sluice.compose_cv`'s single-lead path emits it for each note a
    `--lead` fragment matched, which -- `slug_matches` being a SUBSTRING match -- need not
    share a slug at all. The CLI exits non-zero on the second, since a named lead composed
    for neither twin),
    needs-signoff (an unsupported profile audit flag withheld the send-ready pointer,
    #60), skipped-needs-signoff (a re-run over a lead already held for sign-off),
    dry-run, error (a single lead's exception caught by run_batch -- see run_batch --
    so one bad lead never aborts the rest of the batch)."""
    lead: str
    status: str
    violations: list = field(default_factory=list)
    slop: list = field(default_factory=list)
    audit_flags: list = field(default_factory=list)
    served: str | None = None
    backend: str | None = None
    # #18: set when dossier_cache.get_or_build() raised (a blocked/failed fetch) and
    # composition proceeded with jd="" anyway -- see the `except` below. This does NOT
    # change control flow (skipping the lead here would be a bigger behaviour change
    # than the SSRF guard should carry), only visibility: without it, "status: rendered"
    # is indistinguishable from a CV genuinely tailored to a real job description.
    dossier_failed: bool = False


def _slug(company: str, role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{company}-{role}".lower()).strip("-")[:80] or "lead"


def _jd_keywords(role: str, jd: str) -> list:
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


def run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False,
           guard_existing_cv=False, policy=StalenessPolicy()) -> CvResult:
    # The OPTIONAL half of the Renderer seam (see core/protocols.py). `getattr`, not a
    # required protocol member: a renderer that imposes no grammar of its own must not be
    # made to declare one. Resolved ONCE here rather than inside the retry loop, and the
    # engine no longer imports cv.parse at all -- the grammar belongs to whichever
    # renderer needs it, not to the orchestrator.
    _precheck = getattr(renderer, "precheck", None)
    fm = note.fm
    # Process ONLY shortlist leads. This enforces the shortlist-only constraint and
    # inherently never touches (never clobbers) application-owned leads.
    if _status.normalize(fm.get("status", "")) != "shortlist":
        return CvResult(note.ref, "skipped-selection")

    # THE LATCH (#60): a lead already held for sign-off (pending_cv set) must NOT be
    # recomposed. run_audit is non-deterministic, so a re-run could re-roll a clean
    # verdict and set tailored_cv without a human ever signing off -- the gate would be
    # a dice reroll, not a hold. Skip BEFORE compose. Both cv paths route through
    # run_one (single-lead calls it directly; run_batch calls it per lead), so this one
    # early return covers both. `sluice cv signoff [--discard]` is the only way out.
    if fm.get("pending_cv"):
        return CvResult(note.ref, "skipped-needs-signoff")

    # #9: refuse a stale lead before ANY spend. Placed AFTER the #60 latch so the check
    # is strictly additive -- it can only fire on leads that would otherwise have gone on
    # to compose, so a held lead still reports skipped-needs-signoff and #60's observable
    # behaviour does not move. Placed BEFORE get_or_build because that is the first line
    # that costs anything: a dossier fetch drives a real browser, and the compose below
    # is an LLM call. Tailoring a CV for a closed posting is exactly the spend this
    # exists to stop.
    # `blocks`, never `is_stale`: that is what keeps --include-stale one decision rather
    # than two that could drift apart between here and apply.
    if policy.blocks(fm.get("last_seen", "")):
        return CvResult(note.ref, "skipped-stale")

    company, role = fm.get("company", ""), fm.get("role", "")
    jd, dossier_failed = "", False
    try:
        d = dossier_cache.get_or_build(fm)
        jd = (d.get("jd") or {}).get("markdown", "")
    except Exception as e:
        _log.warning("dossier for %s failed: %s", note.ref, e)
        dossier_failed = True

    # Everything from here on can raise for reasons that have nothing to do with the
    # dossier (a render failure, a backend timeout mid-compose, a store write
    # conflict) -- and if it does, the exception crosses run_batch's own per-lead
    # catch-all (see that function's comment), which is the one place that decides
    # whether this lead's CvResult gets built at all. dossier_failed is a LOCAL
    # variable, though: once the stack unwinds out of this function it is gone, and
    # run_batch has no way left to ask "was THIS lead's dossier blocked". The only
    # thing that still crosses the boundary is the exception object itself, so stamp
    # the fact onto it here -- the one place both are simultaneously in scope -- and
    # re-raise unchanged (a bare `raise` preserves the original traceback). run_batch
    # reads it back via `getattr(..., False)`, which also covers an exception from a
    # path that predates #18 and so never carries the attribute.
    try:
        entries = vault.read_experience_entries(verified_only=True)
        baseline = vault.read_baseline()
        b = _bundle.build_bundle(entries, baseline, cvcfg.negatives,
                                 _jd_keywords(role, jd), cvcfg.prefix_map)
        bundle_text = _bundle.render_bundle(b)

        gate_msgs, cv_text, violations, slop_err = None, "", [], []
        for _ in range(2):
            cv_text = _compose.compose(backend, bundle_text, jd, company, role,
                                       name=cvcfg.name, contact=cvcfg.contact,
                                       employers=cvcfg.employers, prior_violations=gate_msgs)
            violations = _validate(cv_text, bundle_text, employers=cvcfg.employers,
                                   fabrication_decoys=cvcfg.fabrication_decoys)
            # Fail-closed: validate()'s per-bullet citation checks only run inside the
            # section keyed on a case-insensitive "WORK EXPERIENCE" header (validate()
            # upper-cases before comparing). If the composer drifted the header entirely
            # (e.g. "PROFESSIONAL EXPERIENCE"), those checks silently do not run and
            # validate() returns []. Mirror validate()'s exact condition here so this guard
            # fires in exactly the cases validate() silently skips -- no more, no fewer.
            if not any(line.strip().upper() == "WORK EXPERIENCE" for line in cv_text.splitlines()):
                violations = ["STRUCTURAL: composed CV lacks the exact 'WORK EXPERIENCE' "
                              "header, so the citation gate did not run"] + violations
            # Symmetric with the WORK-EXPERIENCE guard above: the profile fabrication
            # sweep in validate() is keyed on the exact "PROFILE" header, so a composed CV
            # that drops the header has an empty profile region and is swept -- a silent
            # fail-open. Catch it here and HARD-fail the gate. (#30)
            if not any(line.strip().upper() == "PROFILE" for line in cv_text.splitlines()):
                violations = ["STRUCTURAL: composed CV lacks the exact 'PROFILE' header, "
                              "so the profile fabrication check did not run"] + violations
            # Ask the RENDERER, inside the retry loop, in the same shape a gate violation
            # takes. cv/validate.py never checks the `template` renderer's meta-line
            # grammar (`MM/YYYY-MM/YYYY | LOCATION | Role`) -- only the citation gate does
            # -- so a CV that clears every fabrication check can still be unrenderable by
            # it. Discovering that at render time would be AFTER the LLM spend with no
            # recovery: this loop is the only retry there is, and it closes before render
            # ever runs. Asking here means the model gets one chance to fix its own
            # formatting, feeding gate_msgs into the second compose prompt exactly like a
            # citation violation would.
            #
            # Asking the renderer, rather than parsing here, is what keeps the requirement
            # attached to the renderer that HAS it. The engine previously called
            # `parse_cv` unconditionally, which imposed one implementation's grammar on
            # the whole seam: measured 2026-08-06, a gate-clean CV carrying a PUBLICATIONS
            # section reported `skipped-gate` under `cv.renderer: script`, whose own
            # script would have rendered it. `precheck` is optional (core/protocols.py);
            # `script` does not implement it and is not gated.
            #
            # The `template` renderer parses TWICE as a result (once here, once in
            # `render` to get the document it lays out) -- deliberate rather than
            # wasteful: parse_cv is pure, no I/O, and the two calls use the result for
            # different things, which is what lets `render`'s seam signature stay
            # `(cv_text, out_dir, *, neutral_name)` and the renderer stay ignorant of this
            # retry loop.
            if _precheck is not None:
                violations = violations + list(_precheck(cv_text))
            slop_err, _warns = _slop(cv_text)
            gate_msgs = violations + [f"SLOP {lbl}: {snip}" for _ln, lbl, snip in slop_err]
            if not gate_msgs:
                break

        backend_used = getattr(backend, "last_backend", None)
        if gate_msgs:
            return CvResult(note.ref, "skipped-gate", violations=violations,
                            slop=[s[2] for s in slop_err], backend=backend_used,
                            dossier_failed=dossier_failed)

        # The audit is advisory only (see audit.py: "NEVER blocks"). A backend error or
        # timeout here must not prevent a CV that already passed the HARD gate from
        # rendering -- swallow and log, never propagate.
        try:
            _report, audit_flags = run_audit(backend, cv_text, bundle_text)
        except Exception as e:
            _log.warning("advisory audit failed for %s: %s", note.ref, e)
            audit_flags = []
        if dry_run:
            return CvResult(note.ref, "dry-run", audit_flags=audit_flags, backend=backend_used,
                            dossier_failed=dossier_failed)

        from sluice.cv import render as _render
        out_dir = f"{cvcfg.output_dir}/{_slug(company, role)}"
        # The renderer is INJECTED, never built here. An engine that constructs its own
        # adapter breaks both the seam and the offline tests. It is reached only past the
        # HARD gate above: a renderer never validates, and is never handed a CV with
        # outstanding violations.
        pdf = renderer.render(cv_text, out_dir, neutral_name=cvcfg.neutral_filename)
        served = (_render.serve(pdf, cvcfg.served_dir, served_prefix=cvcfg.served_prefix)
                  if cvcfg.served_dir else None)
        # An `unsupported` audit flag WITHHOLDS the send-ready pointer until a human signs off
        # (#60). The audit stays advisory to the model; only this consequence is new, and only
        # `unsupported` (never `paraphrase`, which is legitimate tailoring) blocks. Fail-open:
        # an audit backend error already yields no flags above, so a possibly-fabricated CV
        # still serves -- the gate is best-effort, never harder than the audit ran.
        blockers = unsupported_claims(audit_flags) if cvcfg.require_signoff else []
        if served and blockers:
            # Record what to promote (pending_cv) and what to review (needs_signoff, a
            # single-line JSON scalar so a claim's quote/colon can't corrupt the frontmatter);
            # withhold tailored_cv. The served PDF stays in served_dir (it passed the HARD gate)
            # but is inert -- apply/select returns no_artifact without the pointer. hold_for_signoff
            # stamps ONLY IF no tailored_cv already exists (checked atomically on fresh content): a
            # real CV that appeared during compose, or an intentional re-tailor of a CV'd lead,
            # must not latch the lead behind a redundant hold -- it reports skipped-has-cv instead.
            held = vault.hold_for_signoff(
                note.ref, pending=f"{served} ({date.today().isoformat()})",
                claims=json.dumps(blockers))
            if not held:
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags,
                                backend=backend_used, dossier_failed=dossier_failed)
            return CvResult(note.ref, "needs-signoff", audit_flags=audit_flags,
                            served=served, backend=backend_used, dossier_failed=dossier_failed)
        if served:
            wrote = vault.set_tailored_cv(
                note.ref, f"{served} ({date.today().isoformat()})",
                only_if_absent=guard_existing_cv)
            if guard_existing_cv and not wrote:
                # A CV appeared for this lead during our compose+render window; do not clobber
                # it. The served PDF we rendered is left in served_dir (it passed the gate);
                # only the note pointer is withheld. See #16 cv long-window.
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags,
                                backend=backend_used, dossier_failed=dossier_failed)
        return CvResult(note.ref, "rendered", audit_flags=audit_flags,
                        served=served, backend=backend_used, dossier_failed=dossier_failed)
    except Exception as e:
        e.dossier_failed = dossier_failed
        raise


def run_batch(vault, cvcfg, backend, dossier_cache, *, renderer, limit=None,
              dry_run=False, policy=StalenessPolicy()) -> list:
    notes = [n for n in vault.read_leads({"shortlist"})]
    # The LAST consumer of a `read_leads` list that walked it without the slug guard (#1).
    # `index_by_slug` is the shared verdict -- track, `leads expire` and `apply`'s batch path
    # take the same one -- so the call sites cannot drift into different opinions about what
    # ambiguous means. Only the second element is wanted: this pass walks notes, not slugs,
    # which is exactly the shape that let `apply/select.py:select_all` keep both twins.
    _, dropped = index_by_slug(notes)
    for msg in ambiguous_slug_warnings("cv: shortlisted lead", dropped):
        _log.warning("%s", msg)
    results = []
    for note in notes:
        # BEFORE the has-cv check, on `select_all`'s reasoning: what is wrong here is the
        # IDENTITY, and reporting `skipped-has-cv` for a twin that happens to carry a pointer
        # would name a condition the user cannot act on while hiding the one they can.
        #
        # What this costs when it is missing is WASTE, not corruption, and the distinction is
        # worth stating because the neighbouring guards are about irreversible writes and
        # this one is not. Each twin's writes go through its OWN `ref`, `serve` names the
        # served file by CONTENT digest so neither pointer can name the other's PDF, and the
        # hard fabrication gate runs per compose and is untouched. So the harm is that a
        # single job is composed TWICE -- two LLM calls, plus a render each -- and that both
        # renders target one working directory (`output_dir/<slug(company, role)>`, derived
        # from frontmatter the twins share), so only the later twin's intermediate PDF
        # survives there. Downstream, `apply prep --all-shortlist` already refuses both twins
        # as ambiguous, so the duplicate never reaches the ready queue; this spends money to
        # produce artefacts nothing will use.
        if note.slug in dropped:
            results.append(CvResult(note.ref, "skipped-ambiguous"))
            continue
        if note.fm.get("tailored_cv"):
            results.append(CvResult(note.ref, "skipped-has-cv"))
            continue
        # A single lead's exception (e.g. a WeasyPrint render failure) must not abort
        # the rest of the batch -- mirrors the triage engine's per-lead resilience.
        try:
            results.append(run_one(note, vault, cvcfg, backend, dossier_cache,
                                   renderer=renderer, dry_run=dry_run,
                                   guard_existing_cv=True, policy=policy))
        except Exception as e:
            _log.warning("cv run failed for %s: %s", note.ref, e)
            # run_one stamps dossier_failed onto the exception before re-raising (see
            # its own comment) precisely so this catch-all -- which must stay a
            # catch-all, for the isolation reason above -- does not silently under-
            # report "N CV(s) composed blind" (cli.py's summary line) for a lead whose
            # dossier WAS blocked but which then also failed downstream for an
            # unrelated reason. `getattr(..., False)` also covers an exception raised
            # by code that predates #18 and so never carries the attribute.
            results.append(CvResult(note.ref, "error",
                                    dossier_failed=getattr(e, "dossier_failed", False)))
        # needs-signoff counts toward --limit alongside rendered/dry-run: a held lead did
        # the full (expensive) compose + render + serve; only the pointer was withheld, so
        # it consumed a unit of the requested work just as a rendered one did.
        if limit and sum(1 for r in results
                         if r.status in ("rendered", "dry-run", "needs-signoff")) >= limit:
            break
    return results
