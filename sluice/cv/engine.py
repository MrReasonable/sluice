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


def _slug(company: str, role: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{company}-{role}".lower()).strip("-")[:80] or "lead"


def _jd_keywords(role: str, jd: str) -> list:
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


def run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False,
           guard_existing_cv=False) -> CvResult:
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

    company, role = fm.get("company", ""), fm.get("role", "")
    jd = ""
    try:
        d = dossier_cache.get_or_build(fm)
        jd = (d.get("jd") or {}).get("markdown", "")
    except Exception as e:
        _log.warning("dossier for %s failed: %s", note.ref, e)

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
        slop_err, _warns = _slop(cv_text)
        gate_msgs = violations + [f"SLOP {lbl}: {snip}" for _ln, lbl, snip in slop_err]
        if not gate_msgs:
            break

    backend_used = getattr(backend, "last_backend", None)
    if gate_msgs:
        return CvResult(note.ref, "skipped-gate", violations=violations,
                        slop=[s[2] for s in slop_err], backend=backend_used)

    # The audit is advisory only (see audit.py: "NEVER blocks"). A backend error or
    # timeout here must not prevent a CV that already passed the HARD gate from
    # rendering -- swallow and log, never propagate.
    try:
        _report, audit_flags = run_audit(backend, cv_text, bundle_text)
    except Exception as e:
        _log.warning("advisory audit failed for %s: %s", note.ref, e)
        audit_flags = []
    if dry_run:
        return CvResult(note.ref, "dry-run", audit_flags=audit_flags, backend=backend_used)

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
        # but is inert -- apply/select returns no_artifact without the pointer.
        vault.update_fields(note.ref, {
            "pending_cv": f"{served} ({date.today().isoformat()})",
            "needs_signoff": json.dumps(blockers)})
        return CvResult(note.ref, "needs-signoff", audit_flags=audit_flags,
                        served=served, backend=backend_used)
    if served:
        wrote = vault.set_tailored_cv(
            note.ref, f"{served} ({date.today().isoformat()})",
            only_if_absent=guard_existing_cv)
        if guard_existing_cv and not wrote:
            # A CV appeared for this lead during our compose+render window; do not clobber
            # it. The served PDF we rendered is left in served_dir (it passed the gate);
            # only the note pointer is withheld. See #16 cv long-window.
            return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags,
                            backend=backend_used)
    return CvResult(note.ref, "rendered", audit_flags=audit_flags,
                    served=served, backend=backend_used)


def run_batch(vault, cvcfg, backend, dossier_cache, *, renderer, limit=None, dry_run=False) -> list:
    notes = [n for n in vault.read_leads({"shortlist"})]
    results = []
    for note in notes:
        if note.fm.get("tailored_cv"):
            results.append(CvResult(note.ref, "skipped-has-cv"))
            continue
        # A single lead's exception (e.g. a WeasyPrint render failure) must not abort
        # the rest of the batch -- mirrors the triage engine's per-lead resilience.
        try:
            results.append(run_one(note, vault, cvcfg, backend, dossier_cache,
                                   renderer=renderer, dry_run=dry_run,
                                   guard_existing_cv=True))
        except Exception as e:
            _log.warning("cv run failed for %s: %s", note.ref, e)
            results.append(CvResult(note.ref, "error"))
        if limit and sum(1 for r in results if r.status in ("rendered", "dry-run")) >= limit:
            break
    return results
